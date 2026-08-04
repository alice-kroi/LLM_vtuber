#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Milvus 连接复用测试脚本

测试内容：
1. 单线程连接复用测试
2. 多线程并发连接测试
3. 连接状态监控测试
4. 资源占用和响应时间测试
"""

import time
import threading
import concurrent.futures
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from Millvus_base import (
    init_milvus_client,
    get_connection_manager,
    MilvusConnectionManager,
    DoubaoEmbeddings
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_single_thread_reuse():
    """单线程连接复用测试"""
    print("\n=== 测试1: 单线程连接复用 ===")
    start_time = time.time()
    
    client1 = init_milvus_client(db_name="LLM_vtuber")
    client2 = init_milvus_client(db_name="LLM_vtuber")
    client3 = init_milvus_client(db_name="LLM_vtuber")
    
    end_time = time.time()
    
    manager = get_connection_manager(db_name="LLM_vtuber")
    status = manager.get_connection_status()
    
    print(f"三次连接耗时: {end_time - start_time:.4f} 秒")
    print(f"连接计数: {status['connection_count']} (预期: 1)")
    print(f"操作计数: {status['operation_count']}")
    print(f"连接状态: {status['status']}")
    
    assert status['connection_count'] == 1, f"连接复用失败，创建了 {status['connection_count']} 个连接"
    assert status['status'] == "connected", f"连接状态异常: {status['status']}"
    
    print("✅ 单线程连接复用测试通过")


def test_multi_thread_concurrent():
    """多线程并发连接测试"""
    print("\n=== 测试2: 多线程并发连接 ===")
    
    thread_count = 10
    results = []
    errors = []
    
    def worker(thread_id):
        try:
            start_time = time.time()
            client = init_milvus_client(db_name="LLM_vtuber")
            collections = client.list_collections()
            end_time = time.time()
            results.append({
                'thread_id': thread_id,
                'duration': end_time - start_time,
                'collections': len(collections)
            })
            logger.info(f"线程 {thread_id} 完成，耗时 {end_time - start_time:.4f} 秒")
        except Exception as e:
            errors.append({
                'thread_id': thread_id,
                'error': str(e)
            })
            logger.error(f"线程 {thread_id} 失败: {e}")
    
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
        executor.map(worker, range(thread_count))
    
    end_time = time.time()
    
    manager = get_connection_manager(db_name="LLM_vtuber")
    status = manager.get_connection_status()
    
    print(f"\n并发测试结果:")
    print(f"总耗时: {end_time - start_time:.4f} 秒")
    print(f"成功线程数: {len(results)}")
    print(f"失败线程数: {len(errors)}")
    print(f"连接计数: {status['connection_count']} (预期: 1)")
    print(f"操作计数: {status['operation_count']}")
    
    avg_duration = sum(r['duration'] for r in results) / len(results)
    max_duration = max(r['duration'] for r in results)
    min_duration = min(r['duration'] for r in results)
    
    print(f"\n响应时间统计:")
    print(f"  平均: {avg_duration:.4f} 秒")
    print(f"  最大: {max_duration:.4f} 秒")
    print(f"  最小: {min_duration:.4f} 秒")
    
    assert len(errors) == 0, f"有 {len(errors)} 个线程失败"
    assert status['connection_count'] == 1, f"多线程下连接未复用，创建了 {status['connection_count']} 个连接"
    
    print("✅ 多线程并发连接测试通过")


def test_connection_status_monitor():
    """连接状态监控测试"""
    print("\n=== 测试3: 连接状态监控 ===")
    
    manager = get_connection_manager(db_name="LLM_vtuber")
    
    print("\n初始状态:")
    status = manager.get_connection_status()
    for key, value in status.items():
        print(f"  {key}: {value}")
    
    client = manager.get_client()
    client.list_collections()
    
    print("\n操作后状态:")
    status = manager.get_connection_status()
    for key, value in status.items():
        print(f"  {key}: {value}")
    
    assert status['operation_count'] > 0, "操作计数未增加"
    assert status['last_used_time'] != "从未使用", "最后使用时间未更新"
    
    print("✅ 连接状态监控测试通过")


def test_reconnect():
    """自动重连测试"""
    print("\n=== 测试4: 自动重连 ===")
    
    manager = get_connection_manager(db_name="LLM_vtuber")
    client = manager.get_client()
    
    print("获取初始连接状态:")
    status = manager.get_connection_status()
    print(f"  连接计数: {status['connection_count']}")
    
    manager.close()
    print("\n关闭连接后状态:")
    status = manager.get_connection_status()
    print(f"  状态: {status['status']}")
    
    print("\n重新获取连接:")
    client = manager.get_client()
    status = manager.get_connection_status()
    print(f"  状态: {status['status']}")
    print(f"  连接计数: {status['connection_count']}")
    
    assert status['status'] == "connected", f"重连失败: {status['status']}"
    assert status['connection_count'] == 2, f"连接计数异常: {status['connection_count']}"
    
    print("✅ 自动重连测试通过")


def test_multiple_databases():
    """多数据库连接测试"""
    print("\n=== 测试5: 多数据库连接 ===")
    
    manager1 = get_connection_manager(db_name="LLM_vtuber")
    manager2 = get_connection_manager(db_name="vtuber")
    
    client1 = manager1.get_client()
    client2 = manager2.get_client()
    
    status1 = manager1.get_connection_status()
    status2 = manager2.get_connection_status()
    
    print(f"数据库 LLM_vtuber 连接状态: {status1['status']}, 连接计数: {status1['connection_count']}")
    print(f"数据库 vtuber 连接状态: {status2['status']}, 连接计数: {status2['connection_count']}")
    
    assert status1['connection_count'] >= 1, f"数据库1连接计数异常"
    assert status2['connection_count'] == 1, f"数据库2连接计数异常"
    
    all_instances = MilvusConnectionManager.get_all_instances()
    print(f"连接管理器实例数量: {len(all_instances)}")
    
    assert len(all_instances) >= 2, f"实例数量异常: {len(all_instances)}"
    
    print("✅ 多数据库连接测试通过")


def test_performance_comparison():
    """性能对比测试"""
    print("\n=== 测试6: 性能对比 ===")
    
    iterations = 50
    
    print("\n测试连接复用模式:")
    start_time = time.time()
    for i in range(iterations):
        client = init_milvus_client(db_name="LLM_vtuber")
        client.list_collections()
    reuse_time = time.time() - start_time
    
    manager = get_connection_manager(db_name="LLM_vtuber")
    status = manager.get_connection_status()
    
    print(f"  {iterations} 次操作耗时: {reuse_time:.4f} 秒")
    print(f"  平均每次: {reuse_time / iterations:.4f} 秒")
    print(f"  连接计数: {status['connection_count']}")
    
    print("\n✅ 性能对比测试完成")


def main():
    """主测试函数"""
    print("=" * 60)
    print("Milvus 连接复用功能测试")
    print("=" * 60)
    
    try:
        test_single_thread_reuse()
        test_multi_thread_concurrent()
        test_connection_status_monitor()
        test_reconnect()
        test_multiple_databases()
        test_performance_comparison()
        
        print("\n" + "=" * 60)
        print("🎉 所有测试通过！")
        print("=" * 60)
        
        manager = get_connection_manager(db_name="LLM_vtuber")
        final_status = manager.get_connection_status()
        print("\n最终连接状态:")
        for key, value in final_status.items():
            print(f"  {key}: {value}")
        
        MilvusConnectionManager.close_all()
        print("\n已关闭所有连接")
        
        return True
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)