#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG检索流程图

功能：
1. 输入状态（包含查询文本）
2. 检索相关内容（从Milvus知识库）
3. 输出相关内容
4. 生成输入报告
"""

import os
import sys
import time
import json
from datetime import datetime
from typing import TypedDict, Optional, Dict, List, Any

# 添加项目路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import StateGraph, START, END

from Millvus_base import (
    init_milvus_client,
    DoubaoEmbeddings
)


class RetrievalState(TypedDict):
    """
    检索流程状态
    
    字段说明：
    - query_text: 查询文本
    - collection_name: 目标集合名称
    - db_name: 数据库名称
    - query_params: 查询参数配置
    - retrieved_documents: 检索到的文档
    - output_results: 输出结果
    - error: 错误信息
    - execution_time: 执行耗时
    - report: 生成的报告
    """
    query_text: str
    collection_name: str
    db_name: str
    query_params: Optional[Dict]
    retrieved_documents: Optional[List[Dict]]
    output_results: Optional[Dict]
    error: Optional[str]
    execution_time: float
    report: Optional[Dict]


def create_initial_state() -> RetrievalState:
    """创建初始状态"""
    return {
        "query_text": "",
        "collection_name": "chat_history",
        "db_name": "LLM_vtuber",
        "query_params": {
            "top_k": 10,
            "metric_type": "COSINE",
            "nprobe": 10
        },
        "retrieved_documents": None,
        "output_results": None,
        "error": None,
        "execution_time": 0.0,
        "report": None
    }


def retrieve_documents_node(state: RetrievalState) -> RetrievalState:
    """
    检索相关内容节点
    
    从Milvus知识库中检索与查询文本相关的文档
    """
    start_time = time.time()
    node_name = "retrieve_documents"
    
    try:
        # 验证参数
        if not state.get("query_text"):
            raise ValueError("缺少query_text参数")
        
        # 初始化Milvus客户端
        client = init_milvus_client(
            uri="http://localhost:19530",
            token="root:Milvus",
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
            output_fields=["session_id", "role_id", "user_id", "content", "timestamp", "message_type", "context_relevance"],
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
                    "session_id": hit.get("session_id"),
                    "role_id": hit.get("role_id"),
                    "user_id": hit.get("user_id"),
                    "content": hit.get("content"),
                    "timestamp": hit.get("timestamp"),
                    "message_type": hit.get("message_type"),
                    "context_relevance": hit.get("context_relevance", 0.0),
                    "similarity": similarity
                }
                retrieved_docs.append(doc)
        
        execution_time = time.time() - start_time
        
        # 更新状态
        state["retrieved_documents"] = retrieved_docs
        state["execution_time"] = execution_time
        
        print(f"✅ {node_name}: 成功检索到 {len(retrieved_docs)} 条相关文档，耗时 {execution_time:.3f} 秒")
        
    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"检索文档失败: {str(e)}"
        
        state["error"] = error_msg
        state["execution_time"] = execution_time
        
        print(f"❌ {node_name}: {error_msg}")
    
    return state


def output_results_node(state: RetrievalState) -> RetrievalState:
    """
    输出相关内容节点
    
    处理检索结果并生成输出
    """
    start_time = time.time()
    node_name = "output_results"
    
    try:
        # 检查是否有检索结果
        retrieved_docs = state.get("retrieved_documents", [])
        
        # 生成输出结果
        output_results = {
            "query": state["query_text"],
            "retrieved_count": len(retrieved_docs),
            "documents": retrieved_docs,
            "summary": f"成功检索到 {len(retrieved_docs)} 条与'{state['query_text']}'相关的文档",
            "timestamp": datetime.now().isoformat()
        }
        
        # 更新状态
        state["output_results"] = output_results
        
        execution_time = time.time() - start_time
        state["execution_time"] += execution_time
        
        print(f"✅ {node_name}: 生成输出结果，耗时 {execution_time:.3f} 秒")
        
    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"生成输出结果失败: {str(e)}"
        
        state["error"] = error_msg
        state["execution_time"] += execution_time
        
        print(f"❌ {node_name}: {error_msg}")
    
    return state


def generate_report_node(state: RetrievalState) -> RetrievalState:
    """
    生成输入报告节点
    
    生成详细的检索报告
    """
    start_time = time.time()
    node_name = "generate_report"
    
    try:
        # 生成报告
        report = {
            "input": {
                "query_text": state["query_text"],
                "collection_name": state["collection_name"],
                "db_name": state["db_name"],
                "query_params": state["query_params"]
            },
            "results": state["output_results"],
            "error": state["error"],
            "execution_time": state["execution_time"],
            "timestamp": datetime.now().isoformat()
        }
        
        # 更新状态
        state["report"] = report
        
        # 打印报告摘要
        print("\n" + "="*60)
        print("📊 检索报告摘要")
        print("="*60)
        print(f"查询文本: {state['query_text']}")
        print(f"检索到的文档数: {len(state.get('retrieved_documents', []))}")
        print(f"总执行时间: {state['execution_time']:.3f} 秒")
        print(f"状态: {'成功' if not state['error'] else '失败'}")
        print("="*60)
        
        execution_time = time.time() - start_time
        state["execution_time"] += execution_time
        
    except Exception as e:
        error_msg = f"生成报告失败: {str(e)}"
        state["error"] = error_msg
        print(f"❌ {node_name}: {error_msg}")
    
    return state


def build_retrieval_graph() -> StateGraph:
    """
    构建检索流程图
    
    创建包含输入、检索、输出、报告生成节点的状态图
    """
    # 创建状态图
    workflow = StateGraph(RetrievalState)
    
    # 添加节点
    workflow.add_node("retrieve_documents", retrieve_documents_node)
    workflow.add_node("output_results", output_results_node)
    workflow.add_node("generate_report", generate_report_node)
    
    # 连接节点
    workflow.add_edge(START, "retrieve_documents")
    workflow.add_edge("retrieve_documents", "output_results")
    workflow.add_edge("output_results", "generate_report")
    workflow.add_edge("generate_report", END)
    
    return workflow.compile()


def run_retrieval_test(test_query: str) -> RetrievalState:
    """
    运行检索测试
    
    Args:
        test_query: 测试查询文本
        
    Returns:
        最终状态
    """
    # 创建初始状态
    state = create_initial_state()
    state["query_text"] = test_query
    
    # 构建状态图
    graph = build_retrieval_graph()
    
    # 执行状态图
    final_state = graph.invoke(state)
    
    # 确保test文件夹存在
    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test")
    if not os.path.exists(test_dir):
        os.makedirs(test_dir)
    
    # 保存报告到test文件夹
    report_file = os.path.join(test_dir, f"retrieval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(final_state["report"], f, ensure_ascii=False, indent=2)
    
    print(f"\n📄 检索报告已保存到: {report_file}")
    
    return final_state


def generate_markdown_report(report: Dict[str, Any], output_file: str = "retrieval_report.md"):
    """
    生成markdown格式的检索报告
    
    Args:
        report: 检索报告数据
        output_file: 输出文件路径
    """
    # 确保test文件夹存在
    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test")
    if not os.path.exists(test_dir):
        os.makedirs(test_dir)
    
    # 确保输出文件路径在test文件夹下
    if not output_file.startswith(test_dir):
        output_file = os.path.join(test_dir, output_file)
    
    # 生成markdown内容
    markdown_content = f"""# RAG检索测试报告

## 测试信息

- **测试时间**: {report.get('timestamp', datetime.now().isoformat())}
- **执行时间**: {report.get('execution_time', 0):.3f} 秒
- **状态**: {'成功' if not report.get('error') else '失败'}

## 输入参数

- **查询文本**: {report.get('input', {}).get('query_text', '')}
- **集合名称**: {report.get('input', {}).get('collection_name', '')}
- **数据库名称**: {report.get('input', {}).get('db_name', '')}
- **查询参数**:
  - top_k: {report.get('input', {}).get('query_params', {}).get('top_k', 3)}
  - metric_type: {report.get('input', {}).get('query_params', {}).get('metric_type', 'COSINE')}
  - nprobe: {report.get('input', {}).get('query_params', {}).get('nprobe', 10)}

## 检索结果

- **检索到的文档数**: {report.get('results', {}).get('retrieved_count', 0)}
- **摘要**: {report.get('results', {}).get('summary', '')}

## 详细文档

"""
    
    # 添加详细文档
    documents = report.get('results', {}).get('documents', [])
    for i, doc in enumerate(documents, 1):
        markdown_content += f"### 文档 {i}\n"
        markdown_content += f"**相似度**: {doc.get('similarity', 0):.4f}\n"
        markdown_content += f"**消息类型**: {'用户' if doc.get('message_type') == 'user_message' else '助手'}\n"
        markdown_content += f"**会话ID**: {doc.get('session_id', '')}\n"
        markdown_content += f"**用户ID**: {doc.get('user_id', '')}\n"
        markdown_content += f"**时间戳**: {doc.get('timestamp', '')}\n"
        markdown_content += f"**内容**:\n"
        markdown_content += f"> {doc.get('content', '')}\n\n"

        markdown_content += "---\n\n"
    
    # 添加错误信息（如果有）
    if report.get('error'):
        markdown_content += "## 错误信息\n"
        markdown_content += f"> {report.get('error')}\n"
    
    # 保存markdown文件
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    
    print(f"✅ Markdown报告已生成: {output_file}")


def main():
    """
    主函数
    """
    print("🚀 开始RAG检索测试")
    print("=" * 60)
    
    # 模拟测试问题（基于之前插入的聊天数据）
    test_queries = [
        "什么是Milvus？",
        "向量数据库有什么作用？",
        "如何使用RAG？",
        "大语言模型的未来发展趋势是什么？"
    ]
    
    # 运行测试
    for i, test_query in enumerate(test_queries, 1):
        print(f"\n{'='*60}")
        print(f"📝 测试查询 {i}/{len(test_queries)}: {test_query}")
        print(f"{'='*60}")
        
        # 运行检索测试
        final_state = run_retrieval_test(test_query)
        
        # 生成markdown报告
        if final_state.get("report"):
            md_report_file = f"retrieval_report_{i}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            generate_markdown_report(final_state["report"], md_report_file)
    
    print("\n" + "=" * 60)
    print("🎉 RAG检索测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
