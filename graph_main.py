from langgraph.graph import StateGraph, START, END
from LLM.chat_model import ChatState, doubao_chat_node
from LLM.LLM_node import LLMState, llm_chat_node, context_aware_qa_node
from RAG.RAG_node import rag_retrieval_node
from tool.tool_node import tool_dispatch_node


# 创建完整的状态图
def create_full_chat_graph():
    """
    创建完整的聊天图，包含 RAG 检索、工具调用和大模型对话功能
    
    Returns:
        编译后的 langgraph 图
    """
    # 使用扩展的 LLMState
    graph = StateGraph(LLMState)
    
    # 添加节点
    graph.add_node("rag_retrieval", rag_retrieval_node)
    graph.add_node("tool_dispatch", tool_dispatch_node)
    graph.add_node("llm_chat", llm_chat_node)
    graph.add_node("context_aware_qa", context_aware_qa_node)
    
    # 设置图结构
    graph.add_edge(START, "rag_retrieval")
    graph.add_edge("rag_retrieval", "context_aware_qa")
    graph.add_edge("context_aware_qa", END)
    
    # 编译图
    return graph.compile()


# 创建仅使用豆包的状态图
def create_doubao_chat_graph():
    """
    创建仅使用豆包节点的聊天图
    
    Returns:
        编译后的 langgraph 图
    """
    graph = StateGraph(ChatState)
    graph.add_node("doubao_chat", doubao_chat_node)
    graph.set_entry_point("doubao_chat")
    graph.set_finish_point("doubao_chat")
    return graph.compile()


# 使用图进行聊天 - 完整功能示例
if __name__ == "__main__":
    # 创建完整的聊天图
    full_app = create_full_chat_graph()
    
    # 测试完整功能
    print("=== 测试完整聊天图 ===")
    result = full_app.invoke({
        "messages": [
            {"role": "user", "content": "我上一条聊天记录问了什么？"}
        ],
        "system_prompt": "你是一个知识渊博的AI助手，能够基于检索到的信息回答用户的问题。",
        "query": "我上一条聊天记录问了什么？",
        "model": "doubao-seed-1-8-251228",
        "temperature": 0.7,
        "max_tokens": 1000
    })

    # 打印结果
    print("\n=== 测试结果 ===")
    if result.get("error"):
        print(f"错误: {result['error']}")
    else:
        print(f"回答: {result['response']}")
        print(f"使用的模型: {result['model']}")
        print(f"检索到的文档数量: {result.get('num_documents', 0)}")
        print(f"检索耗时: {result.get('retrieval_time', 0):.2f} 秒")
        
        # 打印完整对话历史
        print("\n完整对话历史:")
        for msg in result["messages"]:
            print(f"{msg['role'].capitalize()}: {msg['content']}")
        
        # 打印检索到的文档
        if result.get("retrieved_documents"):
            print("\n检索到的文档:")
            for i, doc in enumerate(result["retrieved_documents"], 1):
                print(f"[{i}] 相似度: {doc.get('similarity', 0):.4f}")
                print(f"   内容: {doc.get('content', '')}")