#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创建LLM_vtuber数据库的脚本

此脚本用于创建名为"LLM_vtuber"的Milvus数据库，包含以下功能：
1. 检查数据库是否存在
2. 如果存在则删除
3. 创建新的数据库
4. 错误处理机制
5. 清晰的日志输出

遵循Python PEP 8编码规范，支持Python 3.8及以上版本。
"""

import traceback
from pymilvus import MilvusClient, DataType


def create_llm_vtuber_database():
    """
    创建LLM_vtuber数据库

    按照设计文档的规范创建数据库，包含以下步骤：
    1. 初始化Milvus客户端
    2. 检查数据库是否存在
    3. 如果存在则删除
    4. 创建新的数据库
    5. 验证数据库创建结果

    Returns:
        bool: 数据库创建是否成功
    """
    # 初始化参数
    uri = "http://localhost:19530"
    token = "root:Milvus"
    db_name = "LLM_vtuber"

    try:
        print("=== 开始创建LLM_vtuber数据库 ===")
        
        # 初始化Milvus客户端（使用默认数据库连接）
        client = MilvusClient(
            uri=uri,
            token=token
        )
        print(f"成功连接到Milvus服务: {uri}")
        
        # 检查数据库是否存在
        existing_databases = client.list_databases()
        print(f"当前存在的数据库: {existing_databases}")
        
        if db_name in existing_databases:
            print(f"数据库 '{db_name}' 已存在，准备删除...")
            # 切换到要删除的数据库
            client_with_db = MilvusClient(
                uri=uri,
                token=token,
                db_name=db_name
            )
            # 列出数据库中的所有集合
            existing_collections = client_with_db.list_collections()
            print(f"数据库 '{db_name}' 中的集合: {existing_collections}")
            # 删除所有集合
            for collection in existing_collections:
                print(f"删除集合: {collection}")
                client_with_db.drop_collection(collection_name=collection)
            # 关闭客户端
            client_with_db.close()
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
            print("\n✅ 数据库创建成功！LLM_vtuber数据库已准备就绪")
            
            # 切换到新创建的数据库
            client_with_db = MilvusClient(
                uri=uri,
                token=token,
                db_name=db_name
            )
            
            # 创建所有集合
            create_all_collections(client_with_db)
            
            # 关闭客户端
            client_with_db.close()
            
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


def create_chat_history_collection(client):
    """
    创建聊天历史记录集合
    
    Args:
        client: Milvus客户端实例
    """
    collection_name = "chat_history"
    print(f"\n=== 创建 {collection_name} 集合 ===")
    
    # 检查集合是否存在
    if client.has_collection(collection_name):
        print(f"集合 '{collection_name}' 已存在，准备删除...")
        client.drop_collection(collection_name=collection_name)
    
    # 创建schema
    schema = client.create_schema(
        auto_id=True,
        enable_dynamic_field=True
    )
    
    # 添加字段
    schema.add_field(
        field_name="message_id",
        datatype=DataType.INT64,
        is_primary=True,
        auto_id=True
    )
    
    schema.add_field(
        field_name="session_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="role_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="user_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="message_type",
        datatype=DataType.VARCHAR,
        max_length=32,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="content",
        datatype=DataType.VARCHAR,
        max_length=4096,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="content_vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=2560,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="timestamp",
        datatype=DataType.TIMESTAMPTZ,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="context_relevance",
        datatype=DataType.FLOAT,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="is_important",
        datatype=DataType.BOOL,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="metadata",
        datatype=DataType.JSON,
        is_nullable=True
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
        field_name="content_vector",
        index_type="AUTOINDEX",  # 使用自动索引
        index_name="content_vector_index"
    )
    
    # 标量索引
    index_params.add_index(
        field_name="session_id",
        index_type="STL_SORT",
        index_name="session_id_index"
    )
    
    index_params.add_index(
        field_name="role_id",
        index_type="STL_SORT",
        index_name="role_id_index"
    )
    
    index_params.add_index(
        field_name="user_id",
        index_type="STL_SORT",
        index_name="user_id_index"
    )
    
    index_params.add_index(
        field_name="message_type",
        index_type="STL_SORT",
        index_name="message_type_index"
    )
    
    index_params.add_index(
        field_name="timestamp",
        index_type="STL_SORT",
        index_name="timestamp_index"
    )
    
    client.create_index(
        collection_name=collection_name,
        index_params=index_params
    )
    
    print(f"✅ 成功创建 {collection_name} 集合")


def create_role_memory_collection(client):
    """
    创建角色记忆集合
    
    Args:
        client: Milvus客户端实例
    """
    collection_name = "role_memory"
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
        field_name="memory_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_primary=True,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="role_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="memory_type",
        datatype=DataType.VARCHAR,
        max_length=32,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="content",
        datatype=DataType.VARCHAR,
        max_length=4096,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="content_vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=2560,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="timestamp",
        datatype=DataType.TIMESTAMPTZ,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="importance_level",
        datatype=DataType.INT32,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="metadata",
        datatype=DataType.JSON,
        is_nullable=True
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
        field_name="content_vector",
        index_type="AUTOINDEX",  # 使用自动索引
        index_name="content_vector_index"
    )
    
    # 标量索引
    index_params.add_index(
        field_name="role_id",
        index_type="STL_SORT",
        index_name="role_id_index"
    )
    
    client.create_index(
        collection_name=collection_name,
        index_params=index_params
    )
    
    print(f"✅ 成功创建 {collection_name} 集合")


def create_session_context_collection(client):
    """
    创建会话上下文集合
    
    Args:
        client: Milvus客户端实例
    """
    collection_name = "session_context"
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
        field_name="session_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_primary=True,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="role_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="user_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="session_summary",
        datatype=DataType.VARCHAR,
        max_length=4096,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="summary_vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=2560,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="start_time",
        datatype=DataType.TIMESTAMPTZ,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="end_time",
        datatype=DataType.TIMESTAMPTZ,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="message_count",
        datatype=DataType.INT32,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="metadata",
        datatype=DataType.JSON,
        is_nullable=True
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
        field_name="summary_vector",
        index_type="AUTOINDEX",  # 使用自动索引
        index_name="summary_vector_index"
    )
    
    # 标量索引
    index_params.add_index(
        field_name="role_id",
        index_type="STL_SORT",
        index_name="role_id_index"
    )
    
    index_params.add_index(
        field_name="user_id",
        index_type="STL_SORT",
        index_name="user_id_index"
    )
    
    client.create_index(
        collection_name=collection_name,
        index_params=index_params
    )
    
    print(f"✅ 成功创建 {collection_name} 集合")


def create_user_profiles_collection(client):
    """
    创建用户画像集合
    
    Args:
        client: Milvus客户端实例
    """
    collection_name = "user_profiles"
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
        field_name="user_id",
        datatype=DataType.VARCHAR,
        max_length=64,
        is_primary=True,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="username",
        datatype=DataType.VARCHAR,
        max_length=100,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="user_preferences",
        datatype=DataType.JSON,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="interaction_history",
        datatype=DataType.JSON,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="last_interaction_time",
        datatype=DataType.TIMESTAMPTZ,
        is_nullable=True
    )
    
    schema.add_field(
        field_name="user_vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=2560,
        is_nullable=False
    )
    
    schema.add_field(
        field_name="metadata",
        datatype=DataType.JSON,
        is_nullable=True
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
        field_name="user_vector",
        index_type="AUTOINDEX",  # 使用自动索引
        index_name="user_vector_index"
    )
    
    # 标量索引
    index_params.add_index(
        field_name="username",
        index_type="STL_SORT",
        index_name="username_index"
    )
    
    client.create_index(
        collection_name=collection_name,
        index_params=index_params
    )
    
    print(f"✅ 成功创建 {collection_name} 集合")


def create_all_collections(client):
    """
    创建所有集合
    
    Args:
        client: Milvus客户端实例
    """
    print("\n=== 开始创建所有集合 ===")
    
    try:
        create_chat_history_collection(client)
        create_role_memory_collection(client)
        create_session_context_collection(client)
        create_user_profiles_collection(client)
        
        # 验证所有集合是否创建成功
        created_collections = client.list_collections()
        print(f"\n=== 集合创建结果 ===")
        print(f"成功创建的集合: {created_collections}")
        
        expected_collections = ["chat_history", "role_memory", "session_context", "user_profiles"]
        all_created = all(col in created_collections for col in expected_collections)
        
        if all_created:
            print("\n✅ 所有集合创建成功！LLM_vtuber数据库已完全初始化")
        else:
            missing_collections = [col for col in expected_collections if col not in created_collections]
            print(f"\n❌ 集合创建失败！缺少集合: {missing_collections}")
            
    except Exception as e:
        print(f"\n❌ 创建集合时发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    """
    主函数，执行数据库创建操作
    """
    success = create_llm_vtuber_database()
    if success:
        print("\n🎉 任务完成：LLM_vtuber数据库创建成功！")
    else:
        print("\n💥 任务失败：LLM_vtuber数据库创建失败，请检查错误信息。")
