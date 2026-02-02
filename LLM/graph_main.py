from langgraph.graph import StateGraph
from LLM.chat_model import ChatState, openai_chat_node, doubao_chat_node, ollama_chat_node

# 创建状态图
graph = StateGraph(ChatState)

# 添加多个聊天节点
graph.add_node("openai_chat", openai_chat_node)
graph.add_node("doubao_chat", doubao_chat_node)
graph.add_node("ollama_chat", ollama_chat_node)

# 添加选择节点（示例）
def choose_llm(state: ChatState) -> str:
    # 根据配置选择使用哪个LLM
    if "llm_type" in state.config:
        if state.config["llm_type"] == "openai":
            return "openai_chat"
        elif state.config["llm_type"] == "doubao":
            return "doubao_chat"
        elif state.config["llm_type"] == "ollama":
            return "ollama_chat"
    return "openai_chat"  # 默认使用OpenAI

# 添加选择节点
graph.add_node("choose_llm", choose_llm)

# 设置边
graph.add_edge("choose_llm", "openai_chat")
graph.add_edge("choose_llm", "doubao_chat")
graph.add_edge("choose_llm", "ollama_chat")

# 设置入口和出口
graph.set_entry_point("choose_llm")
graph.set_finish_point("openai_chat")
graph.set_finish_point("doubao_chat")
graph.set_finish_point("ollama_chat")

# 编译图
app = graph.compile()

# 使用图进行聊天
result = app.invoke({
    "messages": [
        {"role": "user", "content": "你好，能帮我翻译这句话吗？"},
        {"role": "user", "content": "Hello, how are you?"}
    ],
    "config": {
        "llm_type": "openai",
        "api_key": "your_openai_api_key",
        "model": "gpt-3.5-turbo",
        "temperature": 0.7
    }
})

# 打印结果
print(f"回复: {result.response}")
print(f"使用的模型: {result.model}")
