#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将测试数据导入到LLM数据库的messages集合中
"""

import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "RAG"))
from Millvus_base import init_milvus_client, get_connection_manager

def import_test_data():
    """
    导入测试数据到messages集合（使用连接复用）
    """
    # 初始化参数（从环境变量读取）
    uri = os.getenv("MILVUS_URI", "http://localhost:19530")
    token = os.getenv("MILVUS_TOKEN", "")
    db_name = os.getenv("MILVUS_DB", "LLM")
    collection_name = "messages"
    
    try:
        print("=== 开始导入测试数据 ===")
        
        # 使用连接管理器获取Milvus客户端
        manager = get_connection_manager(uri=uri, token=token, db_name=db_name)
        client = manager.get_client()
        print(f"使用数据库: {db_name}")
        
        # 检查集合是否存在
        if not client.has_collection(collection_name):
            print(f"集合 '{collection_name}' 不存在，请先运行 create_llm_database.py 创建集合")
            return False
        
        # 读取测试数据
        test_data_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "LLM", "test", "test_data", "test_messages_20260227_150104.json"
        )
        
        if not os.path.exists(test_data_file):
            print(f"测试数据文件不存在: {test_data_file}")
            return False
        
        print(f"读取测试数据文件: {test_data_file}")
        with open(test_data_file, 'r', encoding='utf-8') as f:
            messages = json.load(f)
        
        print(f"成功读取 {len(messages)} 条测试数据")
        
        # 导入数据
        print("开始导入数据...")
        for i, message in enumerate(messages):
            # 确保message_id是字符串
            message["message_id"] = str(message["message_id"])
            # 确保vector是列表
            if "vector" not in message or message["vector"] is None:
                message["vector"] = [0.0] * 1536
            
            # 插入数据
            client.insert(
                collection_name=collection_name,
                data=[message]
            )
            
            if (i + 1) % 10 == 0:
                print(f"已导入 {i + 1} 条数据")
        
        print(f"成功导入 {len(messages)} 条测试数据到 {collection_name} 集合")
        
        # 验证数据导入
        # 加载集合
        print("加载集合...")
        client.load_collection(collection_name=collection_name)
        print("集合加载成功")
        
        # 使用 query 方法获取数据数量
        result = client.query(
            collection_name=collection_name,
            filter="",
            output_fields=["message_id"],
            limit=1000
        )
        count = len(result)
        print(f"集合 {collection_name} 中的数据条数: {count}")
        
        if count == len(messages):
            print("✅ 数据导入成功！")
            return True
        else:
            print(f"❌ 数据导入失败！期望 {len(messages)} 条，实际 {count} 条")
            return False
            
    except Exception as e:
        print(f"\n❌ 导入数据时发生错误: {e}")
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

def main():
    """
    主函数
    """
    success = import_test_data()
    if success:
        print("\n🎉 任务完成：测试数据导入成功！")
    else:
        print("\n💥 任务失败：测试数据导入失败，请检查错误信息。")


if __name__ == "__main__":
    main()
