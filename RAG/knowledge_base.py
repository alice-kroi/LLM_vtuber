import os
import io
import uuid
import logging
import mimetypes
import threading
import configparser
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

from minio import Minio
from minio.error import S3Error
from pymilvus import MilvusClient, DataType, CollectionSchema, FieldSchema
import numpy as np

logger = logging.getLogger(__name__)


def _load_config():
    """从 config.ini 加载配置"""
    config = configparser.ConfigParser()
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.ini')
    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')
    return config


_config = _load_config()

# MinIO 配置 - 优先从环境变量读取，否则从配置文件读取
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", _config.get("minio", "endpoint", fallback="localhost:9000"))
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", _config.get("minio", "access_key", fallback=""))
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", _config.get("minio", "secret_key", fallback=""))
MINIO_SECURE = os.getenv("MINIO_SECURE", _config.get("minio", "secure", fallback="false")).lower() == "true"

# Milvus 配置 - 优先从环境变量读取，否则从配置文件读取
MILVUS_URI = os.getenv("MILVUS_URI", _config.get("milvus", "uri", fallback="http://localhost:19530"))
MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", _config.get("milvus", "token", fallback=""))
MILVUS_DB = os.getenv("MILVUS_DB", _config.get("milvus", "database", fallback="LLM_vtuber"))

# 知识库配置
KNOWLEDGE_COLLECTION = "knowledge_base"
KNOWLEDGE_BUCKET = os.getenv("KNOWLEDGE_BUCKET", _config.get("minio", "bucket", fallback="knowledge-files"))
VECTOR_DIM = 2560

SUPPORTED_IMAGE_TYPES = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
SUPPORTED_DOC_TYPES = {'.txt', '.md', '.pdf', '.doc', '.docx', '.csv', '.json', '.html', '.xml'}
SUPPORTED_AUDIO_TYPES = {'.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'}
SUPPORTED_VIDEO_TYPES = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}


class MinIOManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, endpoint=None, access_key=None, secret_key=None, secure=None):
        if self._initialized:
            return
        self.endpoint = endpoint or MINIO_ENDPOINT
        self.access_key = access_key or MINIO_ACCESS_KEY
        self.secret_key = secret_key or MINIO_SECRET_KEY
        self.secure = secure if secure is not None else MINIO_SECURE
        self.client = None
        self._initialized = True
        self._connect()

    def _connect(self):
        try:
            self.client = Minio(
                self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure
            )
            logger.info(f"MinIO 连接成功: {self.endpoint}")
        except Exception as e:
            logger.error(f"MinIO 连接失败: {str(e)}")
            raise

    def ensure_bucket(self, bucket_name: str = KNOWLEDGE_BUCKET):
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
                logger.info(f"MinIO 创建存储桶: {bucket_name}")
            return True
        except S3Error as e:
            logger.error(f"MinIO 存储桶操作失败: {str(e)}")
            return False

    def upload_file(self, file_data: bytes, filename: str, 
                    bucket_name: str = KNOWLEDGE_BUCKET,
                    content_type: str = None, metadata: Dict = None) -> Tuple[str, int]:
        self.ensure_bucket(bucket_name)
        
        if content_type is None:
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        
        unique_name = f"{datetime.now().strftime('%Y/%m/%d')}/{uuid.uuid4().hex}_{filename}"
        
        self.client.put_object(
            bucket_name,
            unique_name,
            io.BytesIO(file_data),
            len(file_data),
            content_type=content_type,
            metadata=metadata or {}
        )
        
        logger.info(f"MinIO 上传文件: {unique_name} ({len(file_data)} bytes)")
        return unique_name, len(file_data)

    def download_file(self, object_name: str, bucket_name: str = KNOWLEDGE_BUCKET) -> bytes:
        try:
            response = self.client.get_object(bucket_name, object_name)
            data = response.read()
            response.close()
            return data
        except S3Error as e:
            logger.error(f"MinIO 下载文件失败: {str(e)}")
            raise

    def delete_file(self, object_name: str, bucket_name: str = KNOWLEDGE_BUCKET) -> bool:
        try:
            self.client.remove_object(bucket_name, object_name)
            logger.info(f"MinIO 删除文件: {object_name}")
            return True
        except S3Error as e:
            logger.error(f"MinIO 删除文件失败: {str(e)}")
            return False

    def get_presigned_url(self, object_name: str, bucket_name: str = KNOWLEDGE_BUCKET, 
                         expires: int = 3600) -> str:
        try:
            url = self.client.presigned_get_object(bucket_name, object_name, expires=expires)
            return url
        except S3Error as e:
            logger.error(f"MinIO 生成签名URL失败: {str(e)}")
            return ""

    def list_objects(self, bucket_name: str = KNOWLEDGE_BUCKET, prefix: str = "") -> List[Dict]:
        try:
            objects = self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
            result = []
            for obj in objects:
                result.append({
                    "object_name": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                    "content_type": obj.content_type
                })
            return result
        except S3Error as e:
            logger.error(f"MinIO 列出文件失败: {str(e)}")
            return []


class KnowledgeBaseManager:
    def __init__(self):
        self.minio = MinIOManager()
        self.milvus_client = None
        self._init_milvus()

    def _init_milvus(self):
        try:
            self.milvus_client = MilvusClient(
                uri=MILVUS_URI,
                token=MILVUS_TOKEN,
                db_name=MILVUS_DB
            )
            self._ensure_collection()
            logger.info("知识库 Milvus 初始化成功")
        except Exception as e:
            logger.error(f"知识库 Milvus 初始化失败: {str(e)}")
            raise

    def _ensure_collection(self):
        collections = self.milvus_client.list_collections()
        if KNOWLEDGE_COLLECTION in collections:
            return
        
        schema = CollectionSchema(
            fields=[
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="content_type", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="file_name", dtype=DataType.VARCHAR, max_length=512),
                FieldSchema(name="file_ext", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="minio_object", dtype=DataType.VARCHAR, max_length=1024),
                FieldSchema(name="bucket", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="file_size", dtype=DataType.INT64),
                FieldSchema(name="source_type", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="tags", dtype=DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=64, max_length=128),
                FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=1024),
                FieldSchema(name="uploaded_by", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="updated_at", dtype=DataType.VARCHAR, max_length=64)
            ],
            description="多模态知识库集合"
        )
        
        self.milvus_client.create_collection(KNOWLEDGE_COLLECTION, schema=schema)
        
        from pymilvus.milvus_client.index import IndexParams
        index_params = IndexParams()
        index_params.add_index(
            field_name="vector",
            index_type="IVF_FLAT",
            index_name="vector_index",
            metric_type="COSINE",
            nlist=128
        )
        self.milvus_client.create_index(
            collection_name=KNOWLEDGE_COLLECTION,
            index_params=index_params
        )
        
        logger.info(f"创建知识库集合: {KNOWLEDGE_COLLECTION}")

    def _ensure_loaded(self):
        try:
            self.milvus_client.load_collection(KNOWLEDGE_COLLECTION)
        except Exception:
            pass

    def _classify_file(self, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if ext in SUPPORTED_IMAGE_TYPES:
            return "image"
        elif ext in SUPPORTED_DOC_TYPES:
            return "document"
        elif ext in SUPPORTED_AUDIO_TYPES:
            return "audio"
        elif ext in SUPPORTED_VIDEO_TYPES:
            return "video"
        return "other"

    def _extract_text_content(self, file_data: bytes, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        
        try:
            if ext == '.txt' or ext == '.md':
                return file_data.decode('utf-8', errors='ignore')
            elif ext == '.json':
                return file_data.decode('utf-8', errors='ignore')
            elif ext == '.csv':
                return file_data.decode('utf-8', errors='ignore')[:50000]
            elif ext in {'.doc', '.docx'}:
                try:
                    import docx
                    from io import BytesIO
                    doc = docx.Document(BytesIO(file_data))
                    return '\n'.join([p.text for p in doc.paragraphs])[:50000]
                except ImportError:
                    logger.warning("python-docx 未安装，无法解析 .docx 文件")
                    return f"[二进制文件] {filename}"
                except Exception:
                    return f"[二进制文件] {filename}"
            elif ext == '.pdf':
                try:
                    import fitz
                    from io import BytesIO
                    doc = fitz.open(stream=BytesIO(file_data), filetype="pdf")
                    text = ""
                    for page in doc:
                        text += page.get_text()
                    return text[:50000]
                except ImportError:
                    logger.warning("PyMuPDF 未安装，无法解析 .pdf 文件")
                    return f"[PDF文件] {filename}"
                except Exception:
                    return f"[PDF文件] {filename}"
            elif ext in SUPPORTED_IMAGE_TYPES:
                return f"[图片文件] {filename}"
            elif ext in SUPPORTED_AUDIO_TYPES:
                return f"[音频文件] {filename}"
            elif ext in SUPPORTED_VIDEO_TYPES:
                return f"[视频文件] {filename}"
            else:
                return f"[文件] {filename}"
        except Exception as e:
            logger.error(f"提取文本内容失败 {filename}: {str(e)}")
            return f"[文件] {filename}"

    def _generate_embedding(self, text: str) -> List[float]:
        try:
            from Millvus_base import DoubaoEmbeddings
            embedder = DoubaoEmbeddings()
            return embedder.embed_query(text)
        except Exception as e:
            logger.error(f"生成向量嵌入失败: {str(e)}")
            return np.random.random(VECTOR_DIM).tolist()

    def add_document(self, file_data: bytes, filename: str, 
                    uploaded_by: str = "system",
                    tags: List[str] = None,
                    description: str = "") -> Dict[str, Any]:
        start_time = datetime.now()
        
        content_type = self._classify_file(filename)
        file_ext = os.path.splitext(filename)[1].lower()
        
        minio_object, file_size = self.minio.upload_file(
            file_data, filename,
            metadata={
                "content_type": content_type,
                "uploaded_by": uploaded_by
            }
        )
        
        text_content = self._extract_text_content(file_data, filename)
        
        combined_text = f"{description}\n{text_content}" if description else text_content
        vector = self._generate_embedding(combined_text)
        
        doc_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        
        entity = {
            "id": doc_id,
            "vector": vector,
            "content": text_content[:65000],
            "content_type": content_type,
            "file_name": filename,
            "file_ext": file_ext,
            "minio_object": minio_object,
            "bucket": KNOWLEDGE_BUCKET,
            "file_size": file_size,
            "source_type": "upload",
            "tags": tags or [],
            "description": description[:1000] if description else "",
            "uploaded_by": uploaded_by,
            "created_at": now,
            "updated_at": now
        }
        
        try:
            self.milvus_client.insert(KNOWLEDGE_COLLECTION, [entity])
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"知识库添加文档: {filename} ({content_type}), 耗时 {elapsed:.2f}s")
            return {"success": True, "id": doc_id, "message": "文档添加成功", "elapsed": elapsed}
        except Exception as e:
            logger.error(f"知识库添加文档失败: {str(e)}")
            self.minio.delete_file(minio_object)
            return {"success": False, "message": f"添加失败: {str(e)}"}

    def add_text(self, text: str, description: str = "", 
                 tags: List[str] = None,
                 uploaded_by: str = "system",
                 source_type: str = "manual") -> Dict[str, Any]:
        start_time = datetime.now()
        
        vector = self._generate_embedding(text)
        
        doc_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        
        entity = {
            "id": doc_id,
            "vector": vector,
            "content": text[:65000],
            "content_type": "text",
            "file_name": "",
            "file_ext": "",
            "minio_object": "",
            "bucket": "",
            "file_size": len(text.encode('utf-8')),
            "source_type": source_type,
            "tags": tags or [],
            "description": description[:1000] if description else "",
            "uploaded_by": uploaded_by,
            "created_at": now,
            "updated_at": now
        }
        
        try:
            self.milvus_client.insert(KNOWLEDGE_COLLECTION, [entity])
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"知识库添加文本: {description[:50]}, 耗时 {elapsed:.2f}s")
            return {"success": True, "id": doc_id, "message": "文本添加成功", "elapsed": elapsed}
        except Exception as e:
            logger.error(f"知识库添加文本失败: {str(e)}")
            return {"success": False, "message": f"添加失败: {str(e)}"}

    def search(self, query: str, top_k: int = 5, 
               content_type: str = None,
               tags: List[str] = None,
               score_threshold: float = 0.3) -> List[Dict[str, Any]]:
        try:
            self._ensure_loaded()
            
            query_vector = self._generate_embedding(query)
            
            filter_expr = ""
            conditions = []
            if content_type:
                conditions.append(f'content_type == "{content_type}"')
            if tags:
                tag_conditions = " or ".join([f'"{tag}" in tags' for tag in tags])
                conditions.append(f"({tag_conditions})")
            if conditions:
                filter_expr = " and ".join(conditions)
            
            results = self.milvus_client.search(
                collection_name=KNOWLEDGE_COLLECTION,
                data=[query_vector],
                limit=top_k,
                filter=filter_expr if filter_expr else None,
                output_fields=["content", "content_type", "file_name", "file_ext",
                               "minio_object", "bucket", "file_size", "source_type",
                               "tags", "description", "created_at", "updated_at"]
            )
            
            items = []
            if results and len(results) > 0:
                for hit in results[0]:
                    score = hit.get('distance', 0)
                    if score < score_threshold:
                        continue
                    
                    entity = hit.get('entity', {})
                    tags_val = entity.get('tags', [])
                    if tags_val and not isinstance(tags_val, list):
                        tags_val = list(tags_val) if hasattr(tags_val, '__iter__') else []
                    
                    item = {
                        "id": hit.get('id', ''),
                        "score": score,
                        "content": entity.get('content', '')[:500],
                        "content_type": entity.get('content_type', ''),
                        "file_name": entity.get('file_name', ''),
                        "file_ext": entity.get('file_ext', ''),
                        "minio_object": entity.get('minio_object', ''),
                        "description": entity.get('description', ''),
                        "tags": tags_val,
                        "created_at": entity.get('created_at', '')
                    }
                    
                    if entity.get('minio_object'):
                        try:
                            item['download_url'] = self.minio.get_presigned_url(
                                entity['minio_object'], 
                                entity.get('bucket', KNOWLEDGE_BUCKET)
                            )
                        except Exception:
                            item['download_url'] = ''
                    
                    items.append(item)
            
            items.sort(key=lambda x: x['score'], reverse=True)
            return items
            
        except Exception as e:
            logger.error(f"知识库搜索失败: {str(e)}")
            return []

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        try:
            self._ensure_loaded()
            results = self.milvus_client.query(
                collection_name=KNOWLEDGE_COLLECTION,
                filter=f'id == "{doc_id}"',
                output_fields=["content", "content_type", "file_name", "file_ext",
                               "minio_object", "bucket", "file_size", "source_type",
                               "tags", "description", "uploaded_by", "created_at", "updated_at"]
            )
            
            if results and len(results) > 0:
                entity = results[0]
                if entity.get('minio_object'):
                    try:
                        entity['download_url'] = self.minio.get_presigned_url(
                            entity['minio_object'],
                            entity.get('bucket', KNOWLEDGE_BUCKET)
                        )
                    except Exception:
                        entity['download_url'] = ''
                return entity
            return None
        except Exception as e:
            logger.error(f"获取文档失败: {str(e)}")
            return None

    def delete_document(self, doc_id: str) -> Dict[str, Any]:
        try:
            doc = self.get_document(doc_id)
            if doc and doc.get('minio_object'):
                self.minio.delete_file(doc['minio_object'], doc.get('bucket', KNOWLEDGE_BUCKET))
            
            self.milvus_client.delete(KNOWLEDGE_COLLECTION, ids=[doc_id])
            logger.info(f"知识库删除文档: {doc_id}")
            return {"success": True, "message": "文档删除成功"}
        except Exception as e:
            logger.error(f"知识库删除文档失败: {str(e)}")
            return {"success": False, "message": f"删除失败: {str(e)}"}

    def list_documents(self, limit: int = 100, offset: int = 0,
                       content_type: str = None,
                       tags: List[str] = None) -> Dict[str, Any]:
        try:
            self._ensure_loaded()
            filter_expr = ""
            conditions = []
            if content_type:
                conditions.append(f'content_type == "{content_type}"')
            if tags:
                tag_conditions = " or ".join([f'"{tag}" in tags' for tag in tags])
                conditions.append(f"({tag_conditions})")
            if conditions:
                filter_expr = " and ".join(conditions)
            
            results = self.milvus_client.query(
                collection_name=KNOWLEDGE_COLLECTION,
                filter=filter_expr if filter_expr else None,
                output_fields=["id", "content", "content_type", "file_name", "file_ext",
                               "file_size", "source_type", "tags", "description",
                               "uploaded_by", "created_at"],
                limit=limit,
                offset=offset
            )
            
            total = self._count_documents(filter_expr)
            
            # 确保 tags 字段格式正确
            cleaned_results = []
            for item in results:
                tags_val = item.get('tags', [])
                if tags_val and not isinstance(tags_val, list):
                    tags_val = list(tags_val) if hasattr(tags_val, '__iter__') else []
                item['tags'] = tags_val
                cleaned_results.append(item)
            
            return {
                "total": total,
                "offset": offset,
                "limit": limit,
                "data": cleaned_results
            }
        except Exception as e:
            logger.error(f"列出文档失败: {str(e)}")
            return {"total": 0, "data": [], "error": str(e)}

    def _count_documents(self, filter_expr: str = "") -> int:
        try:
            self._ensure_loaded()
            if not filter_expr:
                stats = self.milvus_client.get_collection_stats(KNOWLEDGE_COLLECTION)
                return int(stats.get('row_count', 0))
            results = self.milvus_client.query(
                collection_name=KNOWLEDGE_COLLECTION,
                filter=filter_expr,
                output_fields=["id"],
                limit=10000
            )
            return len(results) if results else 0
        except Exception:
            return 0

    def get_stats(self) -> Dict[str, Any]:
        try:
            stats = self.milvus_client.get_collection_stats(KNOWLEDGE_COLLECTION)
            total = int(stats.get('row_count', 0))
            
            type_counts = {}
            for ct in ["text", "image", "document", "audio", "video", "other"]:
                count = self._count_documents(f'content_type == "{ct}"')
                if count > 0:
                    type_counts[ct] = count
            
            return {
                "total": stats.get("row_count", 0),
                "by_type": type_counts,
                "collection_name": KNOWLEDGE_COLLECTION
            }
        except Exception as e:
            logger.error(f"获取统计失败: {str(e)}")
            return {"total": 0, "error": str(e)}


_knowledge_manager: Optional[KnowledgeBaseManager] = None
_manager_lock = threading.Lock()


def get_knowledge_manager() -> KnowledgeBaseManager:
    global _knowledge_manager
    with _manager_lock:
        if _knowledge_manager is None:
            _knowledge_manager = KnowledgeBaseManager()
        return _knowledge_manager
