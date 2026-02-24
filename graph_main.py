from langgraph.graph import StateGraph
from LLM.chat_model import ChatState, doubao_chat_node


# 创建仅使用豆包的状态图
graph = StateGraph(ChatState)


# 只添加豆包聊天节点
graph.add_node("doubao_chat", doubao_chat_node)


# 简化的图结构 - 直接从入口到豆包节点
graph.set_entry_point("doubao_chat")
graph.set_finish_point("doubao_chat")


# 编译图
app = graph.compile()


# 创建豆包聊天图的便捷函数
def create_doubao_chat_graph():
    """
    创建仅使用豆包节点的聊天图
    """
    return app


# 使用图进行聊天 - 豆包专属示例
if __name__ == "__main__":
    result = app.invoke({
        "messages": [
            {"role": "user", "content": "你好，能帮我翻译这句话吗？"},
            {"role": "user", "content": "Hello, how are you?"}
        ],
        # 豆包模型配置
        "model": "doubao-seed-1-8-251228",
        "temperature": 0.7,
        "max_tokens": 500,
        "system_prompt": "你是一个专业的翻译助手，准确翻译用户提供的内容。"
    })

    # 打印结果
    if result.get("error"):
        print(f"错误: {result['error']}")
    else:
        print(f"豆包回复: {result['response']}")
        print(f"使用的模型: {result['model']}")
        
        # 打印完整对话历史
        print("\n完整对话历史:")
        for msg in result["messages"]:
            print(f"{msg['role'].capitalize()}: {msg['content']}")