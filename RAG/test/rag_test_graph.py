#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG测试程序 - 基于LangGraph框架

包含三个功能节点：
1. 知识库内容添加功能节点
2. 知识库内容删除功能节点
3. 知识库内容查询功能节点

支持完整的测试流程和报告生成。
"""

import sys
import os
import time
import uuid
import json
from typing import TypedDict, Optional, Dict, List, Any
from datetime import datetime

# 添加项目路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from Millvus_base import (
    init_milvus_client,
    DoubaoEmbeddings
)


class RAGTestState(TypedDict):
    """
    RAG测试程序主状态
    
    字段说明：
    - operation: 当前操作类型 ("add"/"delete"/"query")
    - collection_name: 目标集合名称
    - db_name: 数据库名称
    - documents: 文档列表（用于添加/删除操作）
    - query_text: 查询文本（用于查询操作）
    - query_params: 查询参数配置
    - results: 操作结果
    - error: 错误信息
    - execution_time: 执行耗时
    - node_history: 节点执行历史
    - test_report: 测试报告
    """
    operation: str
    collection_name: str
    db_name: str
    documents: Optional[List[Dict]]
    query_text: Optional[str]
    query_params: Optional[Dict]
    results: Optional[Dict]
    error: Optional[str]
    execution_time: float
    node_history: List[Dict]
    test_report: Optional[Dict]


def create_initial_state() -> RAGTestState:
    """创建初始状态"""
    return {
        "operation": "",
        "collection_name": "chat_history",
        "db_name": "LLM_vtuber",
        "documents": None,
        "query_text": None,
        "query_params": None,
        "results": None,
        "error": None,
        "execution_time": 0.0,
        "node_history": [],
        "test_report": None
    }


def add_knowledge_node(state: RAGTestState) -> RAGTestState:
    """
    知识库内容添加功能节点
    
    向知识库添加新文档
    """
    start_time = time.time()
    node_name = "add_knowledge_node"
    
    try:
        # 验证参数
        if not state.get("documents"):
            raise ValueError("缺少documents参数")
        
        # 初始化Milvus客户端（从环境变量读取）
        client = init_milvus_client(
            uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
            token=os.getenv("MILVUS_TOKEN", ""),
            db_name=state["db_name"]
        )
        
        # 初始化嵌入模型
        embedding_model = DoubaoEmbeddings()
        
        # 准备插入数据
        documents = state["documents"]
        contents = [doc.get("content", "") for doc in documents]
        vectors = embedding_model.embed_documents(contents)
        
        insert_data = []
        for i, doc in enumerate(documents):
            # 不添加message_id，因为schema中设置了auto_id=True
            # timestamp使用ISO格式字符串
            insert_data.append({
                "session_id": doc.get("session_id", str(uuid.uuid4())),
                "role_id": doc.get("role_id", "default_role"),
                "user_id": doc.get("user_id", str(uuid.uuid4())),
                "message_type": doc.get("message_type", "user_message"),
                "content": doc.get("content", ""),
                "timestamp": datetime.now().isoformat(),  # 使用ISO格式字符串
                "content_vector": vectors[i],
                "context_relevance": 0.0,  # 添加上下文相关性字段
                "is_important": False,  # 添加重要性标记字段
                "metadata": {}  # 添加元数据字段
            })
        
        # 插入数据
        result = client.insert(
            collection_name=state["collection_name"],
            data=insert_data
        )
        
        execution_time = time.time() - start_time
        
        # 更新状态
        # 从插入结果中获取生成的ID
        inserted_ids = result.get("ids", []) if isinstance(result, dict) else []
        state["results"] = {
            "success": True,
            "inserted_count": len(insert_data),
            "inserted_ids": inserted_ids
        }
        state["execution_time"] = execution_time
        state["node_history"].append({
            "node": node_name,
            "status": "success",
            "execution_time": execution_time,
            "details": {"inserted_count": len(insert_data)}
        })
        
        print(f"✅ {node_name}: 成功插入 {len(insert_data)} 条数据，耗时 {execution_time:.3f} 秒")
        
    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"添加知识库内容失败: {str(e)}"
        
        state["error"] = error_msg
        state["execution_time"] = execution_time
        state["node_history"].append({
            "node": node_name,
            "status": "failed",
            "execution_time": execution_time,
            "error": error_msg
        })
        
        print(f"❌ {node_name}: {error_msg}")
    
    return state


def delete_knowledge_node(state: RAGTestState) -> RAGTestState:
    """
    知识库内容删除功能节点
    
    从知识库删除指定文档
    """
    start_time = time.time()
    node_name = "delete_knowledge_node"
    
    try:
        # 验证参数
        if not state.get("documents"):
            raise ValueError("缺少documents参数")
        
        # 初始化Milvus客户端（从环境变量读取）
        client = init_milvus_client(
            uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
            token=os.getenv("MILVUS_TOKEN", ""),
            db_name=state["db_name"]
        )
        
        documents = state["documents"]
        deleted_count = 0
        
        # 删除数据
        for doc in documents:
            message_id = doc.get("message_id")
            if message_id:
                # 使用message_id删除
                result = client.delete(
                    collection_name=state["collection_name"],
                    ids=[message_id]
                )
                deleted_count += result.get("delete_count", 0)
            else:
                # 使用过滤条件删除
                filter_expr = doc.get("filter")
                if filter_expr:
                    result = client.delete(
                        collection_name=state["collection_name"],
                        filter=filter_expr
                    )
                    deleted_count += result.get("delete_count", 0)
        
        execution_time = time.time() - start_time
        
        # 更新状态
        state["results"] = {
            "success": True,
            "deleted_count": deleted_count
        }
        state["execution_time"] = execution_time
        state["node_history"].append({
            "node": node_name,
            "status": "success",
            "execution_time": execution_time,
            "details": {"deleted_count": deleted_count}
        })
        
        print(f"✅ {node_name}: 成功删除 {deleted_count} 条数据，耗时 {execution_time:.3f} 秒")
        
    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"删除知识库内容失败: {str(e)}"
        
        state["error"] = error_msg
        state["execution_time"] = execution_time
        state["node_history"].append({
            "node": node_name,
            "status": "failed",
            "execution_time": execution_time,
            "error": error_msg
        })
        
        print(f"❌ {node_name}: {error_msg}")
    
    return state


def query_knowledge_node(state: RAGTestState) -> RAGTestState:
    """
    知识库内容查询功能节点
    
    从知识库查询相关文档
    """
    start_time = time.time()
    node_name = "query_knowledge_node"
    
    try:
        # 验证参数
        if not state.get("query_text"):
            raise ValueError("缺少query_text参数")
        
        # 初始化Milvus客户端（从环境变量读取）
        client = init_milvus_client(
            uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
            token=os.getenv("MILVUS_TOKEN", ""),
            db_name=state["db_name"]
        )
        
        # 初始化嵌入模型
        embedding_model = DoubaoEmbeddings()
        
        # 获取查询参数
        query_params = state.get("query_params", {})
        top_k = query_params.get("top_k", 3)
        metric_type = query_params.get("metric_type", "COSINE")
        nprobe = query_params.get("nprobe", 10)
        
        # 生成查询向量
        query_vector = embedding_model.embed_query(state["query_text"])
        
        # 执行向量搜索
        results = client.search(
            collection_name=state["collection_name"],
            data=[query_vector],
            anns_field="content_vector",
            limit=top_k,
            output_fields=["message_id", "session_id", "role_id", "user_id", "content", "timestamp", "message_type"],
            metric_type=metric_type,
            params={"nprobe": nprobe}
        )
        
        # 处理查询结果
        retrieved_docs = []
        MIN_SIMILARITY_THRESHOLD = 0.9
        
        for hits in results:
            for hit in hits:
                similarity = hit.get("distance", 0.0)
                
                if similarity < MIN_SIMILARITY_THRESHOLD:
                    continue
                    
                doc = {
                    "message_id": hit.get("message_id"),
                    "content": hit.get("content"),
                    "user_id": hit.get("user_id"),
                    "timestamp": hit.get("timestamp"),
                    "similarity": similarity
                }
                retrieved_docs.append(doc)
        
        execution_time = time.time() - start_time
        
        # 更新状态
        state["results"] = {
            "success": True,
            "retrieved_count": len(retrieved_docs),
            "documents": retrieved_docs
        }
        state["execution_time"] = execution_time
        state["node_history"].append({
            "node": node_name,
            "status": "success",
            "execution_time": execution_time,
            "details": {"retrieved_count": len(retrieved_docs)}
        })
        
        print(f"✅ {node_name}: 成功检索到 {len(retrieved_docs)} 条数据，耗时 {execution_time:.3f} 秒")
        
    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"查询知识库内容失败: {str(e)}"
        
        state["error"] = error_msg
        state["execution_time"] = execution_time
        state["node_history"].append({
            "node": node_name,
            "status": "failed",
            "execution_time": execution_time,
            "error": error_msg
        })
        
        print(f"❌ {node_name}: {error_msg}")
    
    return state


def route_by_operation(state: RAGTestState) -> str:
    """
    根据操作类型路由到不同节点
    """
    operation = state.get("operation", "").lower()
    
    if operation == "add":
        return "add_knowledge"
    elif operation == "delete":
        return "delete_knowledge"
    elif operation == "query":
        return "query_knowledge"
    else:
        # 未知操作类型，记录错误
        state["error"] = f"未知的操作类型: {operation}"
        state["node_history"].append({
            "node": "router",
            "status": "failed",
            "error": state["error"]
        })
        return "generate_report"


def generate_test_report(state: RAGTestState) -> RAGTestState:
    """
    生成测试报告
    """
    node_history = state.get("node_history", [])
    
    # 统计测试结果
    total_tests = len(node_history)
    passed_tests = sum(1 for node in node_history if node.get("status") == "success")
    failed_tests = total_tests - passed_tests
    total_execution_time = sum(node.get("execution_time", 0) for node in node_history)
    
    # 生成测试报告
    test_report = {
        "test_summary": {
            "total_tests": total_tests,
            "passed": passed_tests,
            "failed": failed_tests,
            "total_execution_time": round(total_execution_time, 3),
            "timestamp": datetime.now().isoformat()
        },
        "node_results": node_history,
        "errors": [
            {
                "node": node["node"],
                "error": node["error"]
            }
            for node in node_history if node.get("status") == "failed"
        ]
    }
    
    state["test_report"] = test_report
    
    # 打印测试报告摘要
    print("\n" + "="*60)
    print("📊 测试报告")
    print("="*60)
    print(f"总测试数: {total_tests}")
    print(f"通过: {passed_tests} ✅")
    print(f"失败: {failed_tests} ❌")
    print(f"总执行时间: {total_execution_time:.3f} 秒")
    print("="*60)
    
    return state


def build_rag_test_graph() -> StateGraph:
    """
    构建RAG测试状态图
    
    创建包含三个功能节点的状态图，并设置条件路由
    """
    # 创建状态图
    workflow = StateGraph(RAGTestState)
    
    # 添加节点
    workflow.add_node("add_knowledge", add_knowledge_node)
    workflow.add_node("delete_knowledge", delete_knowledge_node)
    workflow.add_node("query_knowledge", query_knowledge_node)
    workflow.add_node("generate_report", generate_test_report)
    
    # 添加条件边
    workflow.add_conditional_edges(
        START,
        route_by_operation,
        {
            "add_knowledge": "add_knowledge",
            "delete_knowledge": "delete_knowledge",
            "query_knowledge": "query_knowledge",
            "generate_report": "generate_report"
        }
    )
    
    # 添加结束边
    workflow.add_edge("add_knowledge", "generate_report")
    workflow.add_edge("delete_knowledge", "generate_report")
    workflow.add_edge("query_knowledge", "generate_report")
    workflow.add_edge("generate_report", END)
    
    return workflow.compile()


def run_test(test_case: Dict[str, Any]) -> RAGTestState:
    """
    运行单个测试用例
    
    Args:
        test_case: 测试用例配置
        
    Returns:
        最终状态
    """
    # 创建初始状态
    state = create_initial_state()
    state.update(test_case)
    
    # 构建状态图
    graph = build_rag_test_graph()
    
    # 执行状态图
    final_state = graph.invoke(state)
    
    return final_state


def run_all_tests():
    """
    运行所有测试用例
    """
    print("🚀 开始RAG测试程序")
    print("="*60)
    
    # 定义测试用例
    test_cases = [
        {
            "name": "添加知识库内容测试",
            "operation": "add",
            "collection_name": "chat_history",
            "db_name": "LLM_vtuber",
            "documents": [
                {
                    "content": "Milvus是一个开源的向量数据库，用于存储和检索向量数据。",
                    "session_id": "test_session_1",
                    "role_id": "assistant",
                    "user_id": "test_user_1",
                    "message_type": "assistant_message"
                },
                {
                    "content": "向量数据库在AI应用中非常重要，可以支持语义搜索和推荐系统。",
                    "session_id": "test_session_1",
                    "role_id": "assistant",
                    "user_id": "test_user_1",
                    "message_type": "assistant_message"
                }
            ]
        },
        {
            "name": "查询知识库内容测试",
            "operation": "query",
            "collection_name": "chat_history",
            "db_name": "LLM_vtuber",
            "query_text": "什么是向量数据库？",
            "query_params": {
                "top_k": 3,
                "metric_type": "COSINE",
                "nprobe": 10
            }
        }
    ]
    
    # 运行测试用例
    all_results = []
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n{'='*60}")
        print(f"📝 测试用例 {i}/{len(test_cases)}: {test_case['name']}")
        print(f"{'='*60}")
        
        final_state = run_test(test_case)
        all_results.append(final_state)
        
        # 打印测试结果
        if final_state.get("test_report"):
            print(f"\n测试报告已生成")
    
    # 生成综合测试报告
    print("\n" + "="*60)
    print("📋 综合测试报告")
    print("="*60)
    
    total_tests = len(test_cases)
    total_passed = sum(1 for result in all_results 
                       if result.get("test_report", {}).get("test_summary", {}).get("failed", 0) == 0)
    
    print(f"总测试用例数: {total_tests}")
    print(f"全部通过: {total_passed} ✅")
    print(f"部分失败: {total_tests - total_passed} ❌")
    
    # 保存测试报告到文件
    report_file = f"rag_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "test_cases": [
                {
                    "name": test_case["name"],
                    "report": result.get("test_report", {})
                }
                for test_case, result in zip(test_cases, all_results)
            ]
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n📄 测试报告已保存到: {report_file}")
    print("="*60)


if __name__ == "__main__":
    run_all_tests()
