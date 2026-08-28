#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 LangGraph 框架的大模型节点模块

提供两个对外函数（保持原有接口）：
- llm_chat_node(state): 纯聊天
- context_aware_qa_node(state): 在 system_prompt 后拼上下文，然后调用

两个函数都委托给内部的 _run_doubao_chat，避免大量重复。

优化 2026-08-28:
- 新增 IntentClassifier：规则+关键词意图预判，减少无效工具调用
- 新增 summarize_search_results：搜索结果预摘要，降低 token 消耗
"""

import asyncio
import logging
import json
import re

from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from typing import Optional

from chat_model import ChatState, doubao_chat_node

logger = logging.getLogger(__name__)


# ============================================================
# 意图分类器 - 优化方向一 P0-1
# ============================================================

# 意图定义：每种意图包含示例句子，用于嵌入相似度匹配
INTENT_EXAMPLES = {
    "skip": {
        "max_rounds": 0,
        "examples": [
            "你好", "在吗", "hi", "hello", "嗨",
            "早上好", "晚上好", "午安",
            "你是谁", "介绍一下你自己", "自我介绍", "你叫什么名字",
            "谢谢", "感谢", "多谢", "thanks", "thank you",
            "再见", "拜拜", "下次见", "晚安", "bye bye",
            "点头", "摇头", "笑", "哭了", "生气", "开心",
            "你好呀", "很高兴见到你", "好久不见",
        ]
    },
    "force_search": {
        "max_rounds": 2,
        "examples": [
            "今天天气怎么样", "最新新闻", "最近有什么事",
            "现在几点", "实时行情", "当前时间",
            "今天几号", "今天星期几",
            "帮我查一下", "查一下", "搜索一下", "搜一下",
            "打开这个网页", "看看这个",
            "股票行情", "汇率多少", "比分是多少",
            "最新消息", "最新进展",
            "看看直播", "直播现在怎么样",
            "查询一下", "获取最新信息",
        ]
    },
    "reduce_rounds": {
        "max_rounds": 1,
        "examples": [
            "什么是人工智能", "AI是什么", "解释一下",
            "怎么学习编程", "如何入门", "为什么会这样",
            "介绍一下这个概念", "讲讲这个",
            "推荐一些学习资源", "建议我怎么做",
            "想要了解一下", "打算开始学习",
            "这个东西怎么用", "如何解决",
            "有哪些方法", "怎么做比较好",
            "我想了解一下", "帮我分析一下", "分析一下",
            "了解一下这个", "想知道为什么",
        ]
    },
    "normal": {
        "max_rounds": 2,
        "examples": [
            "你觉得呢", "怎么样", "好的",
            "真的吗", "是吗", "嗯",
            "然后呢", "接下来呢",
            "我明白了", "了解了", "知道了",
            "继续说", "接着讲",
            "对", "没错", "有道理",
            "什么意思", "怎么回事",
            "可以的", "没问题",
        ]
    },
}

# 显式正则规则（快速路径，优先于嵌入匹配）
# 注意：skip 规则应尽量精确，避免误匹配
_INTENT_REGEX_RULES = {
    "skip": [
        re.compile(r"^(你好|您好|在吗|hi|hello|嗨|早上好|晚上好|午安)[!！。.\s]*$", re.IGNORECASE),
        re.compile(r"^(你是谁|你叫什么名字)[?？]?$", re.IGNORECASE),
        re.compile(r"^(自我介绍)[!！。.\s]*$", re.IGNORECASE),
        re.compile(r"^(介绍一下你自己|介绍下你自己)[?？]?$", re.IGNORECASE),
        re.compile(r"^(谢谢|感谢|多谢|thanks|thank you)[!！。.\s]*$", re.IGNORECASE),
        re.compile(r"^(再见|拜拜|下次见|晚安|bye|bye bye)[!！。.\s]*$", re.IGNORECASE),
        re.compile(r"^(点头|摇头|笑|哭|生气|开心)[!！。.\s]*$", re.IGNORECASE),
    ],
    "force_search": [
        re.compile(r".*(今天|最新|最近|现在|实时|当前|刚才).*"),
        re.compile(r".*(新闻|天气|股价|汇率|比分|直播).*"),
        re.compile(r".*(几号|日期|时间|几点|星期).*"),
        re.compile(r".*(比分|结果|战况|排名|榜单).*"),
        re.compile(r".*(帮我查|查一下|搜索|搜一下|查下).*"),
        re.compile(r"^(打开|看看).*(这个|那个|一下).*"),
    ],
    "reduce_rounds": [
        re.compile(r".*(什么是|是什么|怎么|如何|为什么).*"),
        re.compile(r".*(推荐|建议|想要|打算|计划).*"),
        re.compile(r".*(介绍|讲讲|解释|说明).*(一下|下|这个|那个|概念|内容).*"),
        re.compile(r".*(了解|想知道|知道|分析).*(一下|下|这个|那个|情况|内容|原因|方法).*"),
        re.compile(r".*(帮我|请).*(分析|解释|说明|介绍|讲讲|推荐|建议).*"),
    ],
}

# 嵌入模型缓存（懒加载）
_embed_model = None
_embed_embeddings = {}  # 意图名 -> 示例句子嵌入矩阵
_embed_preloading = False  # 防止并发预加载


def preload_embed_model():
    """
    预加载嵌入模型（建议在应用启动时调用）
    
    避免首次意图分类时的模型加载延迟（约 10-15s）。
    该函数是幂等的，可安全重复调用。
    """
    global _embed_preloading
    if _embed_model is not None or _embed_preloading:
        return
    _embed_preloading = True
    try:
        model = _get_embed_model()
        if model:
            _compute_intent_embeddings()
            logger.info("[意图分类] 嵌入模型预加载完成")
    finally:
        _embed_preloading = False


def _get_embed_model():
    """懒加载 Sentence-BERT 嵌入模型"""
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("[意图分类] 嵌入模型已加载: all-MiniLM-L6-v2")
        except Exception as e:
            logger.warning(f"[意图分类] 嵌入模型加载失败: {e}")
            _embed_model = False  # 标记失败，不再重试
    return _embed_model


def _compute_intent_embeddings():
    """计算所有意图示例的嵌入向量"""
    global _embed_embeddings
    if _embed_embeddings:
        return _embed_embeddings

    model = _get_embed_model()
    if not model:
        return {}

    try:
        for intent, config in INTENT_EXAMPLES.items():
            examples = config["examples"]
            embeddings = model.encode(examples, normalize_embeddings=True)
            _embed_embeddings[intent] = embeddings
        logger.info(f"[意图分类] 已计算 {len(_embed_embeddings)} 种意图的嵌入向量")
    except Exception as e:
        logger.error(f"[意图分类] 嵌入计算失败: {e}")
        _embed_embeddings = {}

    return _embed_embeddings


def _embedding_classify(message: str) -> Optional[dict]:
    """
    基于嵌入相似度的意图分类
    
    Args:
        message: 用户输入文本
    
    Returns:
        分类结果 dict，或 None（无法判断时）
    """
    model = _get_embed_model()
    if not model:
        return None

    embeddings_map = _compute_intent_embeddings()
    if not embeddings_map:
        return None

    try:
        # 编码用户输入
        msg_embedding = model.encode([message], normalize_embeddings=True)

        # 计算与每种意图的最大余弦相似度
        best_intent = None
        best_score = 0.0
        scores = {}

        for intent, intent_embeddings in embeddings_map.items():
            # 计算与所有示例的最大相似度
            similarities = (intent_embeddings @ msg_embedding.T).flatten()
            max_score = float(similarities.max())
            scores[intent] = max_score

            if max_score > best_score:
                best_score = max_score
                best_intent = intent

        # 阈值判断：低于 0.4 则不自信，回退到默认
        if best_score < 0.4 or best_intent is None:
            return None

        config = INTENT_EXAMPLES[best_intent]
        scores_str = ", ".join(f"{k}={v:.3f}" for k, v in scores.items())
        logger.info(f"[意图分类] 嵌入匹配: intent={best_intent}, score={best_score:.3f}, "
                     f"scores=[{scores_str}]")

        return {
            "action": best_intent,
            "max_rounds": config["max_rounds"],
            "reason": f"嵌入相似度匹配 (score={best_score:.3f})"
        }

    except Exception as e:
        logger.error(f"[意图分类] 嵌入分类异常: {e}")
        return None


class IntentClassifier:
    """
    混合意图分类器（正则 + 嵌入相似度）
    
    两级判断：
    1. 快速路径：正则规则匹配（<1ms）
    2. 智能路径：Sentence-BERT 嵌入相似度匹配（~30ms）
    3. 兜底：默认 normal 处理
    """

    @classmethod
    def classify(cls, message: str) -> dict:
        """
        分类用户意图，返回判断结果。
        
        Returns:
            {
                "action": "skip" | "force_search" | "reduce_rounds" | "normal",
                "max_rounds": int,  # 建议的最大工具轮次
                "reason": str       # 判断原因
            }
        """
        if not message or not message.strip():
            return {"action": "normal", "max_rounds": 3, "reason": "空消息"}

        msg = message.strip()

        # ---- 第一级：正则快速路径 ----
        for intent_name, patterns in _INTENT_REGEX_RULES.items():
            for pattern in patterns:
                if pattern.search(msg):
                    config = INTENT_EXAMPLES.get(intent_name, {"max_rounds": 2})
                    logger.info(f"[意图分类] 正则匹配: '{msg[:30]}' -> {intent_name}")
                    return {
                        "action": intent_name,
                        "max_rounds": config["max_rounds"],
                        "reason": f"正则匹配: {pattern.pattern}"
                    }

        # ---- 第二级：嵌入相似度匹配 ----
        embed_result = _embedding_classify(msg)
        if embed_result:
            return embed_result

        # ---- 兜底：默认 normal ----
        logger.info(f"[意图分类] 默认处理: '{msg[:30]}'")
        return {"action": "normal", "max_rounds": 2, "reason": "默认处理"}


def summarize_search_results(results: list, max_chars: int = 500) -> str:
    """
    搜索结果预摘要 - 优化方向一 P0-2
    
    将浏览器搜索结果压缩为 LLM 友好的短摘要，避免注入过多 token。
    
    Args:
        results: 搜索结果列表，每项包含 title, snippet, url
        max_chars: 最大摘要字符数
    
    Returns:
        格式化的摘要字符串
    """
    if not results:
        return ""

    lines = []
    for i, r in enumerate(results[:3]):
        title = (r.get("title") or "无标题")[:40]
        snippet = (r.get("snippet") or r.get("content") or "")[:100]
        url = r.get("url") or ""
        lines.append(f"{i+1}. [{title}]({url})\n   {snippet}")

    summary = "\n".join(lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "..."

    return summary


# ============================================================
# 工具 Schema（延迟导入避免循环依赖）
# ============================================================
_BROWSER_TOOLS = None
_VISION_TOOLS = None
_ALL_TOOLS = None


def _get_browser_tools():
    """延迟获取浏览器工具 Schema"""
    global _BROWSER_TOOLS
    if _BROWSER_TOOLS is None:
        try:
            from tool.browser_tool import BROWSER_TOOLS_SCHEMA
            _BROWSER_TOOLS = BROWSER_TOOLS_SCHEMA
        except ImportError:
            _BROWSER_TOOLS = []
            logger.warning("browser_tool 模块未安装，浏览器工具不可用")
    return _BROWSER_TOOLS


def _get_vision_tools():
    """延迟获取视觉分析工具 Schema"""
    global _VISION_TOOLS
    if _VISION_TOOLS is None:
        try:
            from tool.vision_tool import VISION_TOOLS_SCHEMA
            _VISION_TOOLS = VISION_TOOLS_SCHEMA
        except ImportError:
            _VISION_TOOLS = []
            logger.warning("vision_tool 模块未安装，视觉分析工具不可用")
    return _VISION_TOOLS


def _get_all_tools():
    """获取所有可用工具 Schema（浏览器 + 视觉）"""
    global _ALL_TOOLS
    if _ALL_TOOLS is None:
        all_tools = []
        all_tools.extend(_get_browser_tools())
        all_tools.extend(_get_vision_tools())
        _ALL_TOOLS = all_tools
        logger.info(f"已加载 {len(all_tools)} 个工具 Schema")
    return _ALL_TOOLS


class LLMState(ChatState):
    """
    LLM 节点状态定义，基于 ChatState 扩展

    字段说明：
    - 继承自 ChatState 的所有字段
    - question: 当前用户问题
    - context: 上下文信息（RAG 检索结果）
    - name: 消息发送者名称
    - search_performed: 本轮对话是否执行过搜索
    - search_results_count: 搜索结果总数
    - search_rounds: 工具调用轮数
    """
    question: Optional[str] = None
    context: Optional[str] = None
    name: Optional[str] = None
    search_performed: bool = False
    search_results_count: int = 0
    search_rounds: int = 0


def _extract_last_user_question(state: LLMState) -> Optional[str]:
    """从状态中提取最后一条用户消息内容作为 question"""
    if state.get("question"):
        return state["question"]
    for msg in reversed(state.get("messages", []) or []):
        role = msg.role if hasattr(msg, "role") else (msg.get("role") if isinstance(msg, dict) else None)
        if role == "user":
            content = msg.content if hasattr(msg, "content") else (msg.get("content") if isinstance(msg, dict) else None)
            return content
    return None


async def _run_doubao_chat(state: LLMState, *, with_context: bool) -> dict:
    """
    统一的豆包聊天执行逻辑（异步，支持工具调用循环）。

    优化 2026-08-28: 集成 IntentClassifier 意图预判，减少无效工具调用。
    
    Args:
        state: LLM 状态
        with_context: 是否把 state["context"] 拼接到 system_prompt 末尾
    """
    try:
        logger.info(f"执行 {'上下文感知问答' if with_context else '大模型对话'} 节点")

        question = _extract_last_user_question(state)
        if not question:
            raise ValueError("未找到用户问题")

        # ---- 意图预判（优化方向一 P0-1）----
        intent = IntentClassifier.classify(question)
        max_tool_rounds = intent["max_rounds"]
        skip_tools = intent["action"] == "skip"
        logger.info(f"[意图分类] action={intent['action']}, max_rounds={max_tool_rounds}, reason={intent['reason']}")

        # 构建 system prompt（可选拼上 RAG 上下文）
        system_prompt = state["system_prompt"] or ""
        if with_context and state.get("context"):
            system_prompt = f"{system_prompt}\n\n[上下文信息]\n{state['context']}".strip()

        logger.info(f"处理用户问题: {question[:50]}...")

        # 组装 ChatState（保持 name 传递）
        chat_state = ChatState(
            messages=state["messages"],
            system_prompt=system_prompt,
            model=state["model"],
            temperature=state["temperature"],
            max_tokens=state["max_tokens"],
            name=state.get("name")
        )

        tools = _get_all_tools()
        tool_round = 0
        had_tool_calls = False
        search_results_count = 0

        # 意图预判：跳过工具时不传递 tools
        effective_tools = None if skip_tools else tools

        while True:
            logger.info(f"[LLM] 工具调用第 {tool_round} 轮 (max={max_tool_rounds})")
            result = doubao_chat_node(chat_state, tools=effective_tools)

            tool_calls = result.get("tool_calls")
            if not tool_calls or tool_round >= max_tool_rounds:
                break

            had_tool_calls = True
            logger.info(f"[LLM] 检测到 {len(tool_calls)} 个工具调用: "
                        f"{[tc['name'] for tc in tool_calls]}")

            tool_results = await _execute_tool_calls(tool_calls)
            search_results_count += sum(1 for tr in tool_results if tr.get("result") is not None)

            new_messages = list(chat_state.get("messages", []))
            new_messages.append({
                "role": "assistant",
                "content": result.get("response") or "",
                "tool_calls": [
                    {
                        "id": tc["tool_call_id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                        }
                    }
                    for tc in tool_calls
                ]
            })
            for tr in tool_results:
                # 搜索结果预摘要（优化方向一 P0-2）
                result_content = tr.get("result")
                if result_content is not None and isinstance(result_content, dict):
                    # 对 web_search 结果进行摘要
                    search_results = result_content.get("results")
                    if search_results and isinstance(search_results, list):
                        total_count = len(search_results)
                        summary = summarize_search_results(search_results)
                        if summary:
                            result_content = {
                                "summary": summary,
                                "total": total_count
                            }
                            logger.info(f"[搜索摘要] {total_count} 条结果压缩为摘要")
                new_messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": json.dumps(result_content, ensure_ascii=False)
                        if result_content is not None
                        else (tr.get("error") or "工具执行失败")
                })

            chat_state["messages"] = new_messages
            tool_round += 1

        if had_tool_calls:
            logger.info("[LLM] 工具调用完毕，生成最终回答")
            result = doubao_chat_node(chat_state, tools=None)

        search_performed = tool_round > 0

        return {
            **state,
            "response": result.get("response"),
            "messages": result.get("messages", state["messages"]),
            "tokens_used": result.get("tokens_used", 0),
            "error": result.get("error"),
            "search_performed": search_performed,
            "search_results_count": search_results_count,
            "search_rounds": tool_round
        }

    except Exception as e:
        tag = "上下文感知的问题回答节点" if with_context else "大模型对话节点"
        error_msg = f"{tag}失败: {str(e)}"
        logger.error(error_msg)
        return {
            **state,
            "response": None,
            "error": error_msg
        }


async def _execute_tool_calls(tool_calls: list) -> list:
    """执行工具调用列表，返回结果列表（含独立降级保护）"""
    from tool.tool_node import tool_registry

    results = []
    for tc in tool_calls:
        tool_call = {
            "tool_call_id": tc["tool_call_id"],
            "name": tc["name"],
            "arguments": tc["arguments"]
        }
        logger.info(f"[LLM] 执行工具: {tc['name']}(args={tc['arguments']})")
        try:
            result = await asyncio.wait_for(
                tool_registry.execute_tool(tool_call, timeout=60.0),
                timeout=60.0
            )
            results.append(result)
            logger.info(f"[LLM] 工具结果: {tc['name']} -> "
                        f"{'成功' if result.get('result') else '失败'}")
        except asyncio.TimeoutError:
            logger.error(f"[LLM] 工具超时: {tc['name']} (60s)")
            results.append({
                "tool_call_id": tc["tool_call_id"],
                "error": f"工具执行超时（超过60秒）",
                "result": None
            })
        except Exception as e:
            logger.error(f"[LLM] 工具异常: {tc['name']} -> {str(e)}")
            results.append({
                "tool_call_id": tc["tool_call_id"],
                "error": f"工具执行异常: {str(e)}",
                "result": None
            })
    return results


async def llm_chat_node(state: LLMState) -> LLMState:
    """大模型对话节点（不带RAG上下文增强）"""
    return await _run_doubao_chat(state, with_context=False)


async def context_aware_qa_node(state: LLMState) -> LLMState:
    """上下文感知的问题回答节点（拼上 state['context'] 后调用 LLM）"""
    return await _run_doubao_chat(state, with_context=True)


if __name__ == "__main__":
    """
    LLM 节点图测试主函数
    """
    try:
        # 配置日志
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        print("=== 测试 LLM 节点图 ===")
        
        # 创建状态图
        from langgraph.graph import StateGraph, START, END
        graph = StateGraph(LLMState)
        
        # 添加大模型对话节点
        graph.add_node("llm_chat", llm_chat_node)
        
        # 设置图结构
        graph.add_edge(START, "llm_chat")
        graph.add_edge("llm_chat", END)
        
        # 编译图
        app = graph.compile()
        print("图编译成功！")
        
        # 测试图
        result = app.invoke({
                "messages": [{"role": "user", "content": "你好，我是一名开发者。"}],
                "model": "doubao-seed-1-8-251228",
                "temperature": 0.7,
                "max_tokens": 500,
                "system_prompt": "你是一个友好的AI助手，用简洁明了的语言回答用户的问题。"
            })
        
        print("\n=== 测试结果 ===")
        print(f"回答: {result['response']}")
        print(f"错误: {result['error']}")
        print(f"使用的模型: {result['model']}")
        
        # 打印完整对话历史
        print("\n完整对话历史:")
        for msg in result["messages"]:
            print(f"{msg['role'].capitalize()}: {msg['content']}")
        
        print("\n=== 测试完成 ===")
        
    except Exception as e:
        print(f"测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
