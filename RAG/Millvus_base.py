from pymilvus import MilvusClient, DataType
from openai import OpenAI
from langchain_core.embeddings import Embeddings
import logging
import uuid
import time
import numpy as np
import os
import threading
import weakref
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class DoubaoEmbeddings(Embeddings):
    def __init__(self, api_key=None, model="doubao-embedding-text-240715"):
        # 如果没有提供api_key，可以使用环境变量或默认值
        self.client = OpenAI(
            base_url=os.getenv("Doubao_API_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            api_key=api_key or os.getenv("Doubao_API_KEY")  # 可以替换为环境变量
        )
        self.model = model
        self.vector_dim = 2560  # 明确向量维度
    
    def embed_documents(self, texts):
        """为文档列表生成嵌入向量"""
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
                dimensions=self.vector_dim
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"豆包生成文档嵌入失败: {str(e)}")
            # 如果API调用失败，返回随机向量作为备选
            return [np.random.random(self.vector_dim).tolist() for _ in texts]
    
    def embed_query(self, text):
        """为单个查询生成嵌入向量"""
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=[text],
                dimensions=self.vector_dim
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"豆包生成查询嵌入失败: {str(e)}")
            # 如果API调用失败，返回随机向量作为备选
            return np.random.random(self.vector_dim).tolist()
        
class MilvusConnectionManager:
    _instances: Dict[str, 'MilvusConnectionManager'] = {}
    _instance_lock = threading.Lock()

    def __new__(cls, uri: str = "",
                token: str = "",
                db_name: str = "",
                **kwargs):
        uri = uri or os.getenv("MILVUS_URI", "http://localhost:19530")
        token = token or os.getenv("MILVUS_TOKEN", "")
        db_name = db_name or os.getenv("MILVUS_DB", "vtuber")
        key = f"{uri}_{token}_{db_name}"
        with cls._instance_lock:
            if key not in cls._instances:
                instance = super().__new__(cls)
                cls._instances[key] = instance
            else:
                instance = cls._instances[key]
        return instance

    def __init__(self, uri: str = "",
                 token: str = "",
                 db_name: str = "",
                 max_idle_time: int = 300,
                 connection_timeout: int = 10,
                 retry_count: int = 3,
                 retry_delay: float = 1.0):
        if hasattr(self, '_initialized') and self._initialized:
            return

        self.uri = uri or os.getenv("MILVUS_URI", "http://localhost:19530")
        self.token = token or os.getenv("MILVUS_TOKEN", "")
        self.db_name = db_name or os.getenv("MILVUS_DB", "vtuber")
        self.max_idle_time = max_idle_time
        self.connection_timeout = connection_timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay

        self._client: Optional[MilvusClient] = None
        self._lock = threading.Lock()
        self._last_used_time = 0.0
        self._connection_count = 0
        self._operation_count = 0
        self._is_initializing = False
        self._initialized = True

        self._connection_status = "disconnected"
        self._error_message = ""
        self._last_reconnect_time = 0.0

    def _create_client(self) -> MilvusClient:
        """创建新的Milvus客户端连接"""
        try:
            client = MilvusClient(
                uri=self.uri,
                token=self.token,
                db_name=self.db_name,
                timeout=self.connection_timeout
            )
            self._connection_status = "connected"
            self._error_message = ""
            logger.info(f"成功创建Milvus客户端连接: {self.uri}, db={self.db_name}")
            return client
        except Exception as e:
            self._connection_status = "error"
            self._error_message = str(e)
            logger.error(f"创建Milvus客户端连接失败: {e}")
            raise

    def _check_connection(self) -> bool:
        """检查连接是否有效"""
        if self._client is None:
            return False
        try:
            self._client.list_collections()
            return True
        except Exception:
            return False

    def _reconnect(self) -> bool:
        """尝试重新连接"""
        with self._lock:
            if self._is_initializing:
                return False
            self._is_initializing = True
            try:
                if self._client:
                    try:
                        self._client.close()
                    except Exception:
                        pass
                    self._client = None

                for attempt in range(self.retry_count):
                    try:
                        self._client = self._create_client()
                        self._last_reconnect_time = time.time()
                        self._connection_status = "connected"
                        self._error_message = ""
                        logger.info(f"Milvus重新连接成功，尝试次数: {attempt + 1}")
                        return True
                    except Exception as e:
                        logger.warning(f"Milvus重新连接失败，尝试 {attempt + 1}/{self.retry_count}: {e}")
                        if attempt < self.retry_count - 1:
                            time.sleep(self.retry_delay * (attempt + 1))

                self._connection_status = "disconnected"
                self._error_message = f"重新连接失败，已尝试 {self.retry_count} 次"
                logger.error(self._error_message)
                return False
            finally:
                self._is_initializing = False

    def get_client(self) -> MilvusClient:
        """获取Milvus客户端实例（连接复用）"""
        with self._lock:
            self._last_used_time = time.time()
            self._operation_count += 1

            if self._client is None:
                self._client = self._create_client()
                self._connection_count += 1
                logger.info(f"[连接复用] 创建新连接: {self.uri}, db={self.db_name}, 连接计数={self._connection_count}")
                return self._client

            if not self._check_connection():
                if self._reconnect():
                    self._connection_count += 1
                    logger.info(f"[连接复用] 重新连接成功: {self.uri}, db={self.db_name}, 连接计数={self._connection_count}")
                    return self._client
                else:
                    raise Exception(f"Milvus连接失败: {self._error_message}")

            logger.debug(f"[连接复用] 使用已有连接: {self.uri}, db={self.db_name}, 连接计数={self._connection_count}")
            return self._client

    def close(self):
        """关闭连接"""
        with self._lock:
            if self._client:
                try:
                    self._client.close()
                    logger.info(f"已关闭Milvus客户端连接: {self.uri}")
                except Exception as e:
                    logger.error(f"关闭Milvus客户端连接失败: {e}")
                self._client = None
                self._connection_status = "disconnected"

    def get_connection_status(self) -> Dict[str, Any]:
        """获取连接状态信息"""
        with self._lock:
            return {
                "status": self._connection_status,
                "uri": self.uri,
                "db_name": self.db_name,
                "connection_count": self._connection_count,
                "operation_count": self._operation_count,
                "last_used_time": time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(self._last_used_time)
                ) if self._last_used_time > 0 else "从未使用",
                "last_reconnect_time": time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(self._last_reconnect_time)
                ) if self._last_reconnect_time > 0 else "从未重连",
                "error_message": self._error_message
            }

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connection_status == "connected"

    def reset_stats(self):
        """重置统计信息"""
        with self._lock:
            self._connection_count = 0
            self._operation_count = 0
            self._last_used_time = 0.0
            self._last_reconnect_time = 0.0

    @classmethod
    def get_all_instances(cls) -> List['MilvusConnectionManager']:
        """获取所有连接管理器实例"""
        with cls._instance_lock:
            return list(cls._instances.values())

    @classmethod
    def close_all(cls):
        """关闭所有连接"""
        with cls._instance_lock:
            for instance in cls._instances.values():
                instance.close()

def init_milvus_client(
    uri: str = "",
    token: str = "",
    db_name: str = "",
    max_idle_time: int = 300,
    connection_timeout: int = 10,
    retry_count: int = 3,
    retry_delay: float = 1.0
) -> MilvusClient:
    """
    初始化Milvus客户端（使用连接复用）
    
    Args:
        uri: Milvus服务地址（默认从环境变量 MILVUS_URI 读取）
        token: 认证令牌（默认从环境变量 MILVUS_TOKEN 读取）
        db_name: 数据库名称（默认从环境变量 MILVUS_DB 读取）
        max_idle_time: 最大空闲时间（秒）
        connection_timeout: 连接超时时间（秒）
        retry_count: 重连尝试次数
        retry_delay: 重连间隔时间（秒）
        
    Returns:
        MilvusClient: 初始化后的Milvus客户端（复用连接）
    
    Raises:
        Exception: 客户端初始化失败时抛出异常
    """
    try:
        manager = MilvusConnectionManager(
            uri=uri,
            token=token,
            db_name=db_name,
            max_idle_time=max_idle_time,
            connection_timeout=connection_timeout,
            retry_count=retry_count,
            retry_delay=retry_delay
        )
        client = manager.get_client()
        return client
    except Exception as e:
        raise Exception(f"Milvus客户端初始化失败: {e}")


def get_connection_manager(
    uri: str = "",
    token: str = "",
    db_name: str = "",
    **kwargs
) -> MilvusConnectionManager:
    """
    获取Milvus连接管理器实例
    
    Args:
        uri: Milvus服务地址
        token: 认证令牌
        db_name: 数据库名称
        **kwargs: 其他连接参数
        
    Returns:
        MilvusConnectionManager: 连接管理器实例
    """
    return MilvusConnectionManager(
        uri=uri,
        token=token,
        db_name=db_name,
        **kwargs
    )
    
def create_database(client: MilvusClient, db_name: str) -> bool:
    """
    检查并创建数据库
    
    Args:
        client: Milvus客户端实例
        db_name: 数据库名称
        
    Returns:
        bool: 数据库创建成功返回True，已存在返回False
    
    Raises:
        Exception: 数据库创建失败时抛出异常
    """
    try:
        client.create_database(db_name=db_name)
        print(f"成功创建数据库: {db_name}")
        return True
    except Exception as e:
        if "already exists" in str(e):
            print(f"数据库已存在: {db_name}")
            return False
        else:
            raise Exception(f"数据库创建失败: {e}")
        

def describe_database(client: MilvusClient, db_name: str):
    """
    获取数据库信息
    
    Args:
        client: Milvus客户端实例
        db_name: 数据库名称
        
    Returns:
        Dict[str, Any]: 数据库信息
    
    Raises:
        Exception: 获取数据库信息失败时抛出异常
    """
    try:
        describe = client.describe_database(db_name=db_name)
        print(f"数据库信息 - {db_name}: {describe}")
        return describe
    except Exception as e:
        raise Exception(f"获取数据库信息失败: {e}")


def create_collection(client: MilvusClient, collection_name: str, db_name: str) -> bool:
    """
    创建聊天历史集合
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        db_name: 数据库名称
        
    Returns:
        bool: 集合创建成功返回True
    
    Raises:
        Exception: 集合创建失败时抛出异常
    """
    try:
        # 切换到指定数据库（如果需要）
        # 注意：MilvusClient在初始化时已经指定了db_name，通常不需要再次切换
        
        # 删除已存在的集合
        if client.has_collection(collection_name):
            client.drop_collection(collection_name)
            print(f"已删除现有集合: {collection_name}")
        
        # 创建schema
        schema = client.create_schema(
            auto_id=False,  # 不使用自动ID，使用自定义UUID
            enable_dynamic_field=True  # 允许动态字段
        )
        
        # 添加字段
        schema.add_field(
            field_name="message_id",
            datatype=DataType.VARCHAR,
            max_length=36,
            is_primary=True
        )
        
        # 添加向量字段（注意：字段名必须与 RAG_node 中使用的一致）
        # 豆包嵌入模型 doubao-embedding-text-240715 的维度为 2560
        schema.add_field(
            field_name="content_vector",
            datatype=DataType.FLOAT_VECTOR,
            dim=2560
        )
        
        # 添加其他必要字段
        schema.add_field(
            field_name="user_id",
            datatype=DataType.VARCHAR,
            max_length=36
        )
        
        schema.add_field(
            field_name="username",
            datatype=DataType.VARCHAR,
            max_length=100
        )
        
        schema.add_field(
            field_name="content",
            datatype=DataType.VARCHAR,
            max_length=2000
        )
        
        schema.add_field(
            field_name="timestamp",
            datatype=DataType.DOUBLE
        )
        
        schema.add_field(
            field_name="message_type",
            datatype=DataType.VARCHAR,
            max_length=50
        )
        
        # 创建集合
        client.create_collection(
            collection_name=collection_name,
            schema=schema,
            consistency_level="Strong"
        )
        
        # 创建索引
        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="vector_field",
            index_type="AUTOINDEX",  # 使用自动索引
            index_name="vector_index"
        )
        
        client.create_index(
            collection_name=collection_name,
            index_params=index_params
        )
        
        print(f"成功创建集合: {collection_name}")
        return True
        
    except Exception as e:
        raise Exception(f"集合创建失败: {e}")
    
    
def load_collection(client: MilvusClient, collection_name: str) -> bool:
    """
    加载集合到内存
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        
    Returns:
        bool: 集合加载成功返回True
    
    Raises:
        Exception: 集合加载失败时抛出异常
    """
    try:
        client.load_collection(collection_name=collection_name)
        print(f"成功加载集合: {collection_name}")
        return True
    except Exception as e:
        raise Exception(f"集合加载失败: {e}")
    
def get_collection_load_state(client: MilvusClient, collection_name: str) :
    """
    获取集合加载状态
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        
    Returns:
        Dict[str, Any]: 集合加载状态信息
    
    Raises:
        Exception: 获取集合加载状态失败时抛出异常
    """
    try:
        state = client.get_load_state(collection_name=collection_name)
        print(f"集合加载状态 - {collection_name}: {state}")
        return state
    except Exception as e:
        raise Exception(f"获取集合加载状态失败: {e}")


def list_collections(client: MilvusClient) -> list:
    """
    列出所有集合
    
    Args:
        client: Milvus客户端实例
        
    Returns:
        list: 集合名称列表
    
    Raises:
        Exception: 列出集合失败时抛出异常
    """
    try:
        collections = client.list_collections()
        print(f"所有集合: {collections}")
        return collections
    except Exception as e:
        raise Exception(f"列出集合失败: {e}")


def describe_collection(client: MilvusClient, collection_name: str) -> dict:
    """
    查看集合信息
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        
    Returns:
        dict: 集合信息
    
    Raises:
        Exception: 获取集合信息失败时抛出异常
    """
    try:
        collection_info = client.describe_collection(collection_name)
        print(f"集合信息 - {collection_name}: {collection_info}")
        return collection_info
    except Exception as e:
        raise Exception(f"获取集合信息失败: {e}")


def insert_test_data(client: MilvusClient, collection_name: str, embedding_model: Embeddings) -> list:
    """
    插入测试数据
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        embedding_model: 嵌入模型实例
        
    Returns:
        list: 插入的数据列表
    
    Raises:
        Exception: 插入数据失败时抛出异常
    """
    print("\n=== 插入测试数据 ===")
    # 生成3条测试消息
    test_messages = [
        {"user_id": str(uuid.uuid4()), "username": "测试用户1", "content": "你好，我想了解一下Milvus", "message_type": "user_message"},
        {"user_id": str(uuid.uuid4()), "username": "测试用户1", "content": "Milvus支持哪些向量索引类型？", "message_type": "user_message"},
        {"user_id": str(uuid.uuid4()), "username": "测试用户2", "content": "向量数据库的应用场景有哪些？", "message_type": "user_message"}
    ]
    
    # 为每条消息生成嵌入向量
    contents = [msg["content"] for msg in test_messages]
    vectors = embedding_model.embed_documents(contents)
    print(f"生成的向量维度: {len(vectors[0])}")

    # 准备插入数据（字段名与集合schema、RAG_node保持一致：content_vector）
    insert_data = []
    for i, msg in enumerate(test_messages):
        insert_data.append({
            "message_id": str(uuid.uuid4()),
            "user_id": msg["user_id"],
            "username": msg["username"],
            "content": msg["content"],
            "timestamp": int(time.time()),
            "message_type": msg["message_type"],
            "content_vector": vectors[i]
        })
    
    # 插入数据
    result = client.insert(
        collection_name=collection_name,
        data=insert_data
    )
    print(f"成功插入 {len(insert_data)} 条数据")
    return insert_data


def query_test_data(client: MilvusClient, collection_name: str, embedding_model: Embeddings, query_text: str, top_k: int = 2) -> list:
    """
    向量查询测试
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        embedding_model: 嵌入模型实例
        query_text: 查询文本
        top_k: 返回结果数量
        
    Returns:
        list: 查询结果
    
    Raises:
        Exception: 查询失败时抛出异常
    """
    print(f"\n=== 查询测试: '{query_text}' ===")
    # 生成查询向量
    query_vector = embedding_model.embed_query(query_text)
    
    # 执行向量搜索
    results = client.search(
        collection_name=collection_name,
        data=[query_vector],            # 搜索数据
        anns_field="content_vector",    # 向量字段名（必须与schema一致）
        limit=top_k,                    # 返回结果数量
        output_fields=["message_id", "user_id", "username", "content", "timestamp", "message_type"],
        metric_type="COSINE",
        params={"nprobe": 10}
    )
    
    # 显示查询结果
    for i, hits in enumerate(results):
        print(f"查询结果 {i+1}:")
        for j, hit in enumerate(hits):
            print(f"  结果 {j+1}:")
            print(f"    消息ID: {hit['message_id']}")
            print(f"    用户: {hit['username']}")
            print(f"    内容: {hit['content']}")
            print(f"    时间戳: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(hit['timestamp']))}")
            print(f"    消息类型: {hit['message_type']}")
            print(f"    相似度: {hit['distance']:.4f}")
    
    return results


def scalar_query_test(client: MilvusClient, collection_name: str, username: str) -> list:
    """
    标量查询测试
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        username: 用户名
        
    Returns:
        list: 查询结果
    
    Raises:
        Exception: 查询失败时抛出异常
    """
    print(f"\n=== 标量查询测试: 查询用户 '{username}' 的所有消息 ===")
    results = client.query(
        collection_name=collection_name,
        filter=f"username == '{username}'",  # 查询条件
        output_fields=["message_id", "user_id", "username", "content", "timestamp", "message_type"]  # 返回的字段
    )
    
    print(f"找到 {len(results)} 条消息:")
    for msg in results:
        print(f"  消息ID: {msg['message_id']}")
        print(f"    内容: {msg['content']}")
        print(f"    时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg['timestamp']))}")
    
    return results


def count_messages(client: MilvusClient, collection_name: str) -> int:
    """
    统计消息数量
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        
    Returns:
        int: 消息数量
    
    Raises:
        Exception: 统计失败时抛出异常
    """
    print("\n=== 统计消息数量 ===")
    # 使用client.query查询所有消息，然后计算数量
    results = client.query(
        collection_name=collection_name,
        filter="message_id IS NOT NULL",  # 条件始终为真，查询所有记录
        output_fields=["message_id"],  # 只返回message_id字段，减少数据传输
        limit=1000  # 设置一个合理的限制
    )
    
    message_count = len(results)
    print(f"总消息数量: {message_count}")
    return message_count


def run_tests(client: MilvusClient, collection_name: str, embedding_model: Embeddings):
    """
    运行所有测试
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        embedding_model: 嵌入模型实例
    """
    # 插入测试数据
    insert_test_data(client, collection_name, embedding_model)
    
    # 统计消息数量
    count_messages(client, collection_name)
    
    # 向量查询测试
    query_test_data(client, collection_name, embedding_model, "Milvus是什么？", top_k=2)
    query_test_data(client, collection_name, embedding_model, "向量索引有哪些？", top_k=2)
    
    # 标量查询测试
    scalar_query_test(client, collection_name, "测试用户1")
    
if __name__ == "__main__":
    # 初始化参数（从环境变量读取）
    uri = os.getenv("MILVUS_URI", "http://localhost:19530")
    token = os.getenv("MILVUS_TOKEN", "")
    db_name = os.getenv("MILVUS_DB", "LLM_vtuber")
    collection_name = "chat_history"
    
    # 初始化Milvus客户端
    client = init_milvus_client(uri=uri, token=token, db_name=db_name)
    
    # 检查并创建数据库
    try:
        create_database(client, db_name=db_name)
        print(f"成功创建数据库: {db_name}")
    except Exception as e:
        print(f"数据库已存在或创建失败: {e}")
    
    # 获取数据库信息
    describe_database(client, db_name=db_name)
    
    # 删除已存在的集合（测试用）
    if client.has_collection(collection_name):
        client.drop_collection(collection_name)
        print(f"已删除现有集合: {collection_name}")
    
    # 创建集合
    create_collection(client, collection_name, db_name)
    
    # 查看集合信息
    describe_collection(client, collection_name)
    
    # 初始化嵌入模型
    embedding_model = DoubaoEmbeddings()
    
    # 列出所有集合
    list_collections(client)
    
    # 加载集合
    load_collection(client, collection_name)
    
    # 获取集合加载状态
    get_collection_load_state(client, collection_name)
    
    # 运行所有测试
    run_tests(client, collection_name, embedding_model)
