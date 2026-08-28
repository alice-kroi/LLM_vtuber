#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创建LLM数据库的脚本

此脚本用于创建名为"LLM"的Milvus数据库，并创建一个名为"messages"的集合，用于存储消息数据。
"""

import traceback
import sys
import os

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "RAG"))
from Millvus_base import init_milvus_client, get_connection_manager
from pymilvus import DataType


def create_llm_database():
    """
    创建LLM数据库（使用连接复用）

    按照设计文档的规范创建数据库，包含以下步骤：
    1. 初始化Milvus客户端
    2. 检查数据库是否存在
    3. 如果存在则删除
    4. 创建新的数据库
    5. 验证数据库创建结果

    Returns:
        bool: 数据库创建是否成功
    """
    # 初始化参数（从环境变量读取）
    uri = os.getenv("MILVUS_URI", "http://localhost:19530")
    token = os.getenv("MILVUS_TOKEN", "")
    db_name = os.getenv("MILVUS_DB", "LLM")

    try:
        print("=== 开始创建LLM数据库 ===")
        
        # 使用连接管理器获取Milvus客户端（使用默认数据库）
        manager_default = get_connection_manager(uri=uri, token=token, db_name="default")
        client = manager_default.get_client()
        
        # 检查数据库是否存在
        existing_databases = client.list_databases()
        print(f"当前存在的数据库: {existing_databases}")
        
        if db_name in existing_databases:
            print(f"数据库 '{db_name}' 已存在，准备删除...")
            # 使用连接管理器切换到要删除的数据库
            manager_db = get_connection_manager(uri=uri, token=token, db_name=db_name)
            client_with_db = manager_db.get_client()
            # 列出数据库中的所有集合
            existing_collections = client_with_db.list_collections()
            print(f"数据库 '{db_name}' 中的集合: {existing_collections}")
            # 删除所有集合
            for collection in existing_collections:
                print(f"删除集合: {collection}")
                client_with_db.drop_collection(collection_name=collection)
            # 删除数据库
            client.drop_database(db_name=db_name)
            print(f"成功删除数据库: {db_name}")
        
        # 创建数据库
        client.create_database(db_name=db_name)
        print(f"成功创建数据库: {db_name}")
        
        # 验证数据库是否创建成功
        updated_databases = client.list_databases()
        print(f"更新后的数据库列表: {updated_databases}")
        
        if db_name in updated_databases:
            print("\n✅ 数据库创建成功！LLM数据库已准备就绪")
            
            # 使用连接管理器切换到新创建的数据库
            manager_db = get_connection_manager(uri=uri, token=token, db_name=db_name)
            client_with_db = manager_db.get_client()
            
            # 创建messages集合
            create_messages_collection(client_with_db)
            
            return True
        else:
            print("\n❌ 数据库创建失败！数据库未出现在列表中")
            return False
            
    except Exception as e:
        print(f"\n❌ 创建数据库时发生错误: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 关闭客户端连接
        if 'client' in locals():
            try:
                client.close()
                print("已关闭Milvus客户端连接")
            except Exception as e:
                print(f"关闭客户端连接时发生错误: {e}")


def create_messages_collection(client):
    """
    创建messages集合
    
    Args:
        client: Milvus客户端实例
    """
    collection_name = "messages"
    print(f"\n=== 创建 {collection_name} 集合 ===")
    
    # 检查集合是否存在
    if client.has_collection(collection_name):
        print(f"集合 '{collection_name}' 已存在，准备删除...")
        client.drop_collection(collection_name=collection_name)
    
    # 创建schema
    schema = client.create_schema(
        auto_id=False,
        enable_dynamic_field=True
    )
    
    # 添加字段
    schema.add_field(
        field_name="message_id",
        datatype=DataType.VARCHAR,
        max_length=36,
        is_primary=True,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="role",
        datatype=DataType.VARCHAR,
        max_length=16,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="content",
        datatype=DataType.VARCHAR,
        max_length=4096,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="timestamp",
        datatype=DataType.VARCHAR,
        max_length=32,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="status",
        datatype=DataType.VARCHAR,
        max_length=16,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=1536,  # 1536维向量，对应OpenAI的text-embedding-ada-002模型
        is_nullable=False
    )
    
    # 创建集合
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        consistency_level="Strong"
    )
    
    # 创建索引
    index_params = client.prepare_index_params()
    
    # 向量索引
    index_params.add_index(
        field_name="vector",
        index_type="AUTOINDEX",  # 使用自动索引
        index_name="vector_index"
    )
    
    # 标量索引
    index_params.add_index(
        field_name="message_id",
        index_type="STL_SORT",
        index_name="message_id_index"
    )
    
    index_params.add_index(
        field_name="role",
        index_type="STL_SORT",
        index_name="role_index"
    )
    
    index_params.add_index(
        field_name="status",
        index_type="STL_SORT",
        index_name="status_index"
    )
    
    client.create_index(
        collection_name=collection_name,
        index_params=index_params
    )
    
    print(f"✅ 成功创建 {collection_name} 集合")


def main():
    """
    主函数，执行数据库创建操作
    """
    success = create_llm_database()
    if success:
        print("\n🎉 任务完成：LLM数据库创建成功！")
    else:
        print("\n💥 任务失败：LLM数据库创建失败，请检查错误信息。")


if __name__ == "__main__":
    main()
