from pymilvus import MilvusClient, DataType
from openai import OpenAI
from langchain_core.embeddings import Embeddings
import logging
import uuid
import time
import numpy as np
import os

logger = logging.getLogger(__name__)

class DoubaoEmbeddings(Embeddings):
    def __init__(self, api_key=None, model="doubao-embedding-text-240715"):
        # 如果没有提供api_key，可以使用环境变量或默认值
        self.client = OpenAI(
            base_url=os.getenv("Doubao_API_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            api_key=api_key or os.getenv("Doubao_API_KEY")  # 可以替换为环境变量
        )
        self.model = model
        self.vector_dim = 768  # 明确向量维度
    
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
        
def init_milvus_client(
    uri: str = "http://localhost:19530",
    token: str = "root:Milvus",
    db_name: str = "vtuber"
) -> MilvusClient:
    """
    初始化Milvus客户端
    
    Args:
        uri: Milvus服务地址
        token: 认证令牌
        db_name: 数据库名称
        
    Returns:
        MilvusClient: 初始化后的Milvus客户端
    
    Raises:
        Exception: 客户端初始化失败时抛出异常
    """
    try:
        client = MilvusClient(
            uri=uri,
            token=token,
            db_name=db_name
        )
        print(f"成功连接到Milvus服务: {uri}")
        return client
    except Exception as e:
        raise Exception(f"Milvus客户端初始化失败: {e}")
    
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
        
        # 添加向量字段
        schema.add_field(
            field_name="vector_field",
            datatype=DataType.FLOAT_VECTOR,
            dim=768  # 使用豆包嵌入模型的维度（768）
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
    
    # 准备插入数据
    insert_data = []
    for i, msg in enumerate(test_messages):
        insert_data.append({
            "message_id": str(uuid.uuid4()),
            "user_id": msg["user_id"],
            "username": msg["username"],
            "content": msg["content"],
            "timestamp": int(time.time()),
            "message_type": msg["message_type"],
            "vector_field": vectors[i]
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
        data=[query_vector],  # 搜索数据
        anns_field="vector_field",  # 向量字段名
        limit=top_k,  # 返回结果数量
        output_fields=["message_id", "user_id", "username", "content", "timestamp", "message_type"],  # 返回的字段
        metric_type="COSINE",  # 相似度度量方式
        params={"nprobe": 10}  # 搜索参数
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
    # 初始化参数
    uri = "http://localhost:19530"
    token = "root:Milvus"
    db_name = "vtuber"
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