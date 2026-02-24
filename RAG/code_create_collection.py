#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Milvus数据库管理工具
功能：检查当前存在的数据库
"""

from pymilvus import MilvusClient
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def describe_collection(client, collection_name):
    """
    获取集合的详细信息
    
    Args:
        client: Milvus客户端实例
        collection_name: 集合名称
        
    Returns:
        dict: 集合信息
    """
    try:
        info = client.describe_collection(collection_name)
        print(f"  集合信息:")
        print(f"    名称: {info.get('collection_name', 'N/A')}")
        print(f"    分区数: {info.get('num_partitions', 'N/A')}")
        print(f"    行数: {info.get('num_rows', 'N/A')}")
        print(f"    主键字段: {info.get('primary_field', 'N/A')}")
        
        # 显示字段信息
        if 'schema' in info and 'fields' in info['schema']:
            print(f"    字段 ({len(info['schema']['fields'])}):")
            for field in info['schema']['fields']:
                field_name = field.get('name', 'N/A')
                field_type = field.get('type', 'N/A')
                is_primary = field.get('is_primary', False)
                print(f"      - {field_name} ({field_type}){' [主键]' if is_primary else ''}")
        
        return info
    except Exception as e:
        logger.error(f"获取集合 {collection_name} 信息失败: {e}")
        print(f"  获取集合信息失败: {e}")
        return {}


def check_collection_content(db_name, collection_name, limit=10):
    """
    检查指定集合的内容
    
    Args:
        db_name: 数据库名称
        collection_name: 集合名称
        limit: 返回记录数限制
        
    Returns:
        list: 集合中的数据记录
    
    Raises:
        Exception: 操作失败时抛出异常
    """
    try:
        # 初始化Milvus客户端（指定数据库）
        client = MilvusClient(
            uri="http://localhost:19530",
            token="root:Milvus",
            db_name=db_name
        )
        
        print(f"\n=== 检查集合内容: {db_name}.{collection_name} ===")
        
        # 首先获取集合信息
        describe_collection(client, collection_name)
        
        # 查询集合中的数据
        try:
            # 获取集合的主键字段
            info = client.describe_collection(collection_name)
            primary_field = info.get('primary_field', 'id')
            
            # 执行查询
            results = client.query(
                collection_name=collection_name,
                filter=f"{primary_field} IS NOT NULL",
                limit=limit
            )
            
            print(f"\n  数据记录 ({min(len(results), limit)}):")
            if results:
                for i, record in enumerate(results[:limit]):
                    print(f"\n    记录 {i+1}:")
                    for key, value in record.items():
                        # 对于向量字段，只显示前几个元素
                        if isinstance(value, list) and len(value) > 5:
                            print(f"      {key}: [{value[0]:.4f}, {value[1]:.4f}, ...] (长度: {len(value)})")
                        else:
                            print(f"      {key}: {value}")
            else:
                print("    集合为空")
            
            return results
            
        except Exception as query_error:
            logger.error(f"查询集合 {collection_name} 失败: {query_error}")
            print(f"  查询集合失败: {query_error}")
            return []
        
    except Exception as e:
        logger.error(f"检查集合 {collection_name} 失败: {e}")
        raise Exception(f"检查集合 {collection_name} 失败: {e}")
    finally:
        # 关闭客户端连接
        try:
            if 'client' in locals():
                client.close()
        except:
            pass


def list_collections_in_database(db_name):
    """
    列出指定数据库中的所有集合
    
    Args:
        db_name: 数据库名称
        
    Returns:
        list: 集合名称列表
    
    Raises:
        Exception: 操作失败时抛出异常
    """
    try:
        # 初始化Milvus客户端（指定数据库）
        client = MilvusClient(
            uri="http://localhost:19530",
            token="root:Milvus",
            db_name=db_name
        )
        
        # 列出所有集合
        collections = client.list_collections()
        
        if collections:
            print(f"  集合 ({len(collections)}):")
            for collection_name in collections:
                print(f"  - {collection_name}")
        else:
            print("  没有找到任何集合")
        
        return collections
        
    except Exception as e:
        logger.error(f"列出数据库 {db_name} 中的集合失败: {e}")
        print(f"  列出集合失败: {e}")
        return []
    finally:
        # 关闭客户端连接
        try:
            if 'client' in locals():
                client.close()
        except:
            pass


def list_databases():
    """
    列出所有存在的数据库及其包含的集合
    
    Returns:
        dict: 数据库名称到集合列表的映射
    
    Raises:
        Exception: 操作失败时抛出异常
    """
    try:
        # 初始化Milvus客户端
        client = MilvusClient(
            uri="http://localhost:19530",
            token="root:Milvus"
        )
        
        logger.info("成功连接到Milvus服务")
        
        # 列出所有数据库
        databases = client.list_databases()
        
        print("=== 当前存在的数据库及其集合 ===")
        
        db_collections_map = {}
        
        if databases:
            for db_name in databases:
                print(f"\n- 数据库: {db_name}")
                # 列出该数据库中的集合
                collections = list_collections_in_database(db_name)
                db_collections_map[db_name] = collections
        else:
            print("没有找到任何数据库")
        
        print(f"\n总共找到 {len(databases)} 个数据库")
        
        # 统计所有集合
        total_collections = sum(len(collections) for collections in db_collections_map.values())
        print(f"总共找到 {total_collections} 个集合")
        
        return db_collections_map
        
    except Exception as e:
        logger.error(f"列出数据库失败: {e}")
        raise Exception(f"列出数据库失败: {e}")
    finally:
        # 关闭客户端连接（如果需要）
        try:
            if 'client' in locals():
                client.close()
        except:
            pass

def parse_collection_input(input_str):
    """
    解析用户输入的集合名称
    
    Args:
        input_str: 用户输入的集合名称，格式如 "database.collection"
        
    Returns:
        tuple: (database_name, collection_name) 或 (None, None) 如果解析失败
    """
    if not input_str:
        return None, None
    
    parts = input_str.strip().split('.')
    if len(parts) != 2:
        print("输入格式错误，请使用 '数据库名.集合名' 格式，如 'default.chat_history'")
        return None, None
    
    db_name, collection_name = parts
    return db_name, collection_name

def main():
    """
    主函数
    """
    try:
        # 1. 列出所有数据库及其集合
        db_collections_map = list_databases()
        
        # 2. 如果存在数据库和集合，提供交互式检查选项
        if db_collections_map:
            print("\n=== 检查集合内容 ===")
            print("请输入要检查的集合名称（格式: 数据库名.集合名，如 'default.chat_history'）")
            print("或输入 'exit' 退出")
            
            while True:
                user_input = input("\n请输入: ").strip()
                
                if user_input.lower() == 'exit':
                    print("退出程序")
                    break
                
                # 解析用户输入
                db_name, collection_name = parse_collection_input(user_input)
                if not db_name or not collection_name:
                    continue
                
                # 验证数据库是否存在
                if db_name not in db_collections_map:
                    print(f"错误: 数据库 '{db_name}' 不存在")
                    continue
                
                # 验证集合是否存在
                if collection_name not in db_collections_map[db_name]:
                    print(f"错误: 集合 '{collection_name}' 在数据库 '{db_name}' 中不存在")
                    continue
                
                # 检查集合内容
                check_collection_content(db_name, collection_name, limit=5)
                
                # 询问用户是否继续
                print("\n是否继续检查其他集合？")
                print("输入 'y' 继续，输入其他任意键退出")
                continue_input = input("请输入: ").strip().lower()
                if continue_input != 'y':
                    print("退出程序")
                    break
        else:
            print("没有找到任何数据库和集合，无法检查内容")
        
    except Exception as e:
        print(f"操作失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()