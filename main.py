#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LangGraph 主程序

基于 LangGraph 框架创建图结构，参考 langgraph_state_schema.json 和 message_state_schema.json 中的状态模式规范。
整合并复用 openai_message_node.py 和 message_state.py 中的代码组件。
"""

import logging
import os
import sys
import uuid
import asyncio
import argparse
import json
import time
import configparser
import uvicorn

# 配置日志（必须在其他模块导入前定义，因为导入时可能已需要logger）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 添加 LLM 目录到系统路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "LLM"))
# 添加 broadcast 目录到模块搜索路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast"))
# 添加 RAG 目录到模块搜索路径
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "RAG"))

from langgraph.graph import StateGraph, START
from langgraph.checkpoint.memory import MemorySaver

from LLM_node import llm_chat_node, context_aware_qa_node, LLMState
from RAG_node import rag_retrieval_node, rag_save_node, RAGState
from memory_manager import get_memory_manager, MemoryManager

# 条件导入 TTS 模块
try:
    from audio.audio_main import tts_node
    tts_available = True
except ImportError:
    tts_available = False
    tts_node = None

# 条件导入 Live2D 模块
try:
    from live2d.live2d_controller_manager import (
        Live2DControllerManager,
        Live2DConfig,
        ActionGenerator,
        Direction
    )
    live2d_available = True
except ImportError as e:
    live2d_available = False
    Live2DControllerManager = None
    Live2DConfig = None
    ActionGenerator = None
    Direction = None
    logger.warning(f"Live2D模块导入失败: {e}")

# 全局变量
args = None
http_server = None
langgraph_manager = None
live2d_manager = None
live2d_action_generator = None
_bili_process = None  # 弹幕监听子进程引用，用于退出时清理

# 消息处理队列：串行处理弹幕，避免 Live2D/TTS 并发冲突导致死锁
# （move_to_direction 持锁 1.5s，TTS 播放需独占音频，并发会导致排队卡死）
_message_queue: asyncio.Queue = None
_queue_worker_task: asyncio.Task = None
_QUEUE_MAX_SIZE = 10  # 队列上限，超过则丢弃旧消息（弹幕时效性强，保留最新的）

# 消息去重：防止同一条弹幕被多次处理
_recent_message_keys: set = set()
_RECENT_MSG_MAX = 2000  # 去重集合上限
_RECENT_MSG_WINDOW = 5.0  # 去重时间窗口（秒）
_last_dedup_cleanup = 0.0  # 上次清理时间

# 命令行参数解析
def parse_args():
    """
    解析命令行参数
    """
    parser = argparse.ArgumentParser(description="LangGraph 主程序")
    parser.add_argument("--all", action="store_true", help="启动所有功能（Live2D + TTS + 弹幕监听）")
    parser.add_argument("--live2d", action="store_true", help="开启 live2d 功能")
    parser.add_argument("--live2d-host", type=str, default="localhost", help="VTube Studio 服务器地址")
    parser.add_argument("--live2d-port", type=int, default=8001, help="VTube Studio 服务器端口")
    parser.add_argument("--live2d-sensitivity", type=float, default=1.0, help="动作灵敏度 (0.1-2.0)")
    parser.add_argument("--live2d-speed", type=float, default=2.5, help="响应速度/移动时间 (秒)，值越大动作越慢越柔和")
    parser.add_argument("--live2d-smoothness", type=float, default=0.8, help="动作平滑度 (0.0-1.0)")
    parser.add_argument("--live2d-eye-tracking", action="store_true", default=True, help="启用目光追踪")
    parser.add_argument("--live2d-expression", action="store_true", default=True, help="启用表情动作")
    parser.add_argument("--tts", action="store_true", help="开启 tts 功能")
    parser.add_argument("--room-id", type=int, help="指定哔哩哔哩直播间号")
    return parser.parse_args()

# 异步HTTP服务器处理函数
async def handle_post_request(request):
    """
    处理POST请求：将消息放入队列串行处理，立即返回响应。

    使用队列的原因：Live2D 的 move_to_direction 持有 operation_lock 长达 1.5s，
    TTS 播放也需独占音频设备。若每条弹幕都并发启动 graph.ainvoke，会导致
    锁竞争排队甚至死锁，TTS 永远无法执行。串行处理确保每条消息完整走完
    init→load_memory→rag→llm→rag_save→save_memory→live2d→tts→finalize 流程。
    """
    from fastapi.responses import JSONResponse

    try:
        request_data = await request.json()
        logger.info(f"收到HTTP请求: {json.dumps(request_data, ensure_ascii=False)}")

        if isinstance(request_data, list):
            messages = request_data
        elif isinstance(request_data, dict) and 'messages' in request_data:
            messages = request_data['messages']
        else:
            messages = [request_data]

        global _recent_message_keys, _last_dedup_cleanup
        now = time.time()
        if now - _last_dedup_cleanup > 30:
            _recent_message_keys.clear()
            _last_dedup_cleanup = now
            logger.debug("消息去重集合已清理")

        deduped_messages = []
        for msg in (messages if isinstance(messages, list) else [messages]):
            if msg.get("role") == "user":
                uname = msg.get("name", msg.get("username", "匿名"))
                content = msg.get("content", "")
                ts = msg.get("timestamp", 0)
                if isinstance(ts, float):
                    ts = int(ts)
                msg_key = f"{uname}|{content}|{ts}"
                if msg_key in _recent_message_keys:
                    logger.debug(f"[去重] 跳过重复消息: {uname}: {content[:30]}")
                    continue
                _recent_message_keys.add(msg_key)
                if len(_recent_message_keys) > _RECENT_MSG_MAX:
                    keys_list = list(_recent_message_keys)
                    _recent_message_keys = set(keys_list[_RECENT_MSG_MAX // 2:])
            deduped_messages.append(msg)
        if not deduped_messages:
            return JSONResponse({"status": "deduped"})

        messages = deduped_messages

        while _message_queue and _message_queue.qsize() >= _QUEUE_MAX_SIZE:
            try:
                _message_queue.get_nowait()
                _message_queue.task_done()
                logger.warning(f"消息队列已满({_QUEUE_MAX_SIZE})，丢弃旧消息")
            except asyncio.QueueEmpty:
                break

        if _message_queue is not None:
            await _message_queue.put(messages)
            logger.info(f"消息已入队列（当前队列长度: {_message_queue.qsize()}）")

            try:
                from webui.app import ws_manager
                for msg in (messages if isinstance(messages, list) else [messages]):
                    if msg.get("role") == "user":
                        name = msg.get("name", "匿名")
                        content = msg.get("content", "")
                        await ws_manager.broadcast_danmaku(name, content)
                        from webui.app import add_message_to_history
                        add_message_to_history("danmaku", {"username": name, "content": content})
            except Exception:
                pass

        return JSONResponse({"status": "queued"})

    except Exception as e:
        logger.error(f"处理HTTP请求失败: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

async def handle_messages(messages):
    """
    处理消息列表，统一入口（合并了原handle_messages和handle_single_message）
    """
    global langgraph_manager
    if not langgraph_manager:
        return

    # 确保messages是列表格式
    if not isinstance(messages, list):
        messages = [messages]

    logger.info(f"处理消息: {json.dumps(messages, ensure_ascii=False)}")

    # 创建LLM状态并运行
    initial_state = create_default_llm_state(messages=messages)
    logger.info("运行LangGraph处理消息")
    await langgraph_manager.run_with_messages(initial_state)


async def _queue_worker():
    """
    队列工作协程：串行处理消息，避免 Live2D/TTS 并发冲突。

    每条消息依次走完完整流程（init→load_memory→rag→llm→rag_save→save_memory→live2d→tts→finalize），
    确保operation_lock不会因并发争抢而卡死，TTS音频也不会重叠播放。
    """
    logger.info("消息队列 worker 已启动")
    while True:
        try:
            messages = await _message_queue.get()
            logger.info(f"从队列取出消息处理（剩余队列长度: {_message_queue.qsize()}）")
            await handle_messages(messages)
        except asyncio.CancelledError:
            logger.info("消息队列 worker 被取消")
            break
        except Exception as e:
            logger.error(f"队列 worker 处理消息异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            try:
                _message_queue.task_done()
            except Exception:
                pass
    logger.info("消息队列 worker 已退出")


# Live2D 动作联动节点
async def live2d_action_node(state: LLMState) -> LLMState:
    """
    Live2D 动作联动节点

    从大模型响应中解析动作信息，执行目光方向和嘴巴状态动作。
    """
    global live2d_manager

    try:
        response = state.get("response", "")
        parsed = parse_response_format(response, enable_live2d=True)
        visual_focus = parsed["visual_focus"]
        mouth_state = parsed["mouth_state"]

        if not live2d_manager or not live2d_manager.is_connected:
            logger.warning("Live2D控制器未连接，跳过动作")
            state["live2d_status"] = "disconnected"
            state["live2d_message"] = "Live2D控制器未连接"
            return state

        logger.info(f"Live2D节点: 目光={visual_focus}, 嘴巴={mouth_state}")

        # 执行目光方向动作
        try:
            # 使用命令行参数 --live2d-speed 控制移动时长（默认3秒）
            duration = args.live2d_speed if args is not None else 3.0
            await live2d_manager.move_to_direction(direction=visual_focus, duration=duration)
            logger.info(f"✓ 执行目光方向: {visual_focus}（时长 {duration:.1f}s）")
        except Exception as e:
            logger.error(f"执行目光方向失败: {e}")

        # 执行嘴巴动作
        try:
            if mouth_state == "open":
                await live2d_manager.open_mouth()
            else:
                await live2d_manager.close_mouth()
            logger.info(f"✓ 执行嘴巴动作: {mouth_state}")
        except Exception as e:
            logger.error(f"执行嘴巴动作失败: {e}")

        state["live2d_status"] = "success"
        state["live2d_message"] = f"目光:{visual_focus}, 嘴巴:{mouth_state}"
        return state

    except Exception as e:
        logger.error(f"Live2D动作节点执行失败: {e}")
        state["live2d_status"] = "error"
        state["live2d_error"] = str(e)
        state["live2d_message"] = f"执行失败: {e}"
        return state

# 允许的语气列表（与 audio_main.py 保持一致）
ALLOWED_TONES = {
    "扮演慌张", "调皮", "尴尬", "感动", "积极", "急了", "假装",
    "惊喜", "开心", "撩拨", "难过", "普通", "撒娇", "生气",
    "严肃", "疑问", "自言"
}

# 系统提示词常量
SYSTEM_PROMPT_LIVE2D = """
你是一位由Live2D技术驱动的AI虚拟主播，名叫爱莉希雅。

## 角色设定
- **身份**: 活泼可爱的二次元虚拟主播，拥有粉色长发和灵动的大眼睛
- **性格**: 元气满满、俏皮可爱、温柔体贴，偶尔会有点小调皮
- **说话风格**: 使用可爱的口语化表达，适当加入语气词和表情符号
- **口头禅**: 喜欢用"~"、"呢"、"呀"等结尾，让回答更有亲和力

## 直播互动规则
- 时刻保持热情友好的态度，让观众感受到温暖和快乐
- 回答要简洁明快，适合语音合成播放
- 可以适当加入一些主播常用的互动话术
- 保持积极向上的氛围

## 回答格式要求
请在回答时使用以下格式输出：
【语气】回答内容|目光方向|嘴巴状态

其中：
1. 语气必须从以下列表中选择：
   扮演慌张、调皮、尴尬、感动、积极、急了、假装、惊喜、开心、撩拨、难过、普通、撒娇、生气、严肃、疑问、自言

2. 目光方向必须从以下选项中选择一个（不要总是选center，要多样化选择）：
   center、up、down、left、right、upleft、upright、downleft、downright

3. 嘴巴状态必须为以下之一：
   open（张开嘴巴，说话时）、close（闭合嘴巴，安静思考时）

## 目光方向选择指南（重要！）
目光方向要多样化，**不要总是选择center**！按照以下优先级选择：

**内容相关方向（优先）**：
- 提到"上面/天上/高/天空/飞"相关内容 → up
- 提到"下面/地下/低/地面/地板"相关内容 → down
- 提到"左边/左/左侧"相关内容 → left
- 提到"右边/右/右侧"相关内容 → right
- 提到"左上/右上/左下/右下"等对角方向 → 对应方向

**情绪动作（可选）**：
- 开心/惊喜/撩拨时 → 可选 up/center
- 调皮/撒娇时 → 可选 left/right
- 疑问/思考时 → 可选 up/down
- 生气/难过时 → 可选 down

**默认规则**：如果内容没有明确的方向提示，优先选择 up/down/left/right 中的一个，而不是 center！

**建议分布**：center 占比不超过 30%，其他方向占比 70%

## 嘴巴状态选择
- 说话中 → open
- 句子结束、停顿、思考 → close
"""

SYSTEM_PROMPT_DEFAULT = "你是一个友好的AI助手，用简洁明了的语言回答用户的问题。"

# 默认LLM配置
DEFAULT_MODEL = "doubao-seed-1-8-251228"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 500

# 合法的目光方向和嘴巴状态
VALID_DIRECTIONS = ["center", "up", "down", "left", "right",
                    "upleft", "upright", "downleft", "downright"]
VALID_MOUTH_STATES = ["open", "close"]


def parse_response_format(response: str, enable_live2d: bool = False) -> dict:
    """
    解析大模型响应中的语气、内容、目光方向和嘴巴状态。

    响应格式：【语气】内容|目光方向|嘴巴状态

    Args:
        response: 大模型生成的原始响应
        enable_live2d: 是否启用Live2D（影响是否解析动作信息）

    Returns:
        包含 tone, content, visual_focus, mouth_state 的字典
    """
    result = {
        "tone": "普通",
        "content": response,
        "visual_focus": "center",
        "mouth_state": "close"
    }

    if not response or not response.startswith("【"):
        return result

    end_bracket = response.find("】")
    if end_bracket == -1:
        logger.warning("无效的格式，缺少结束括号】")
        return result

    # 提取语气
    extracted_tone = response[1:end_bracket]
    if extracted_tone in ALLOWED_TONES:
        result["tone"] = extracted_tone

    # 提取剩余部分
    rest = response[end_bracket + 1:].strip()
    parts = rest.split("|")
    result["content"] = parts[0].strip()

    # 只有启用Live2D时才解析动作信息
    if not enable_live2d:
        return result

    # 解析目光方向
    if len(parts) > 1:
        direction = parts[1].strip().lower()
        if direction in VALID_DIRECTIONS:
            result["visual_focus"] = direction

    # 解析嘴巴状态
    if len(parts) > 2:
        mouth = parts[2].strip().lower()
        if mouth in VALID_MOUTH_STATES:
            result["mouth_state"] = mouth

    return result


def create_default_llm_state(messages=None) -> LLMState:
    """创建默认的LLM状态"""
    return LLMState(
        messages=messages or [],
        system_prompt=SYSTEM_PROMPT_DEFAULT,
        model=DEFAULT_MODEL,
        temperature=DEFAULT_TEMPERATURE,
        max_tokens=DEFAULT_MAX_TOKENS
    )


def _extract_last_user_message(state):
    """从状态中提取最后一条用户消息"""
    for msg in reversed(state.get("messages", [])):
        if msg.get("role") == "user":
            return msg
    return None


class LangGraphManager:
    """
    LangGraph 管理器
    """

    def __init__(self):
        """
        初始化 LangGraph 管理器
        """
        self.graph = None
        self.checkpointer = MemorySaver()
        self.memory_manager = get_memory_manager()
        self.processing_danmaku = False
        self.enable_live2d = False

    def build_graph(self, enable_tts=False, enable_live2d=False):
        """
        构建 LangGraph 图结构

        Args:
            enable_tts: 是否启用 TTS 功能
            enable_live2d: 是否启用 Live2D 功能
        """
        self.enable_live2d = enable_live2d and live2d_available
        logger.info(f"开始构建 LangGraph 图结构 (TTS: {'启用' if enable_tts else '禁用'}, Live2D: {'启用' if self.enable_live2d else '禁用'})")

        # 创建状态图，使用LLMState作为状态类型
        self.graph = StateGraph(LLMState)

        # 添加节点
        self.graph.add_node("init", self._init_node)
        self.graph.add_node("load_memory", self._load_memory_node)
        self.graph.add_node("rag_retrieval", self._rag_retrieval_node)
        self.graph.add_node("llm_process", self._llm_process_node)
        self.graph.add_node("rag_save", self._rag_save_node)
        self.graph.add_node("save_memory", self._save_memory_node)
        self.graph.add_node("finalize", self._finalize_node)

        # 根据参数决定是否添加 TTS 节点
        if enable_tts and tts_available:
            logger.info("添加 TTS 节点到图中")
            self.graph.add_node("tts", tts_node)

        # 根据参数决定是否添加 Live2D 节点
        if self.enable_live2d:
            logger.info("添加 Live2D 动作联动节点到图中")
            self.graph.add_node("live2d", live2d_action_node)

        # 添加边：init → load_memory → rag_retrieval → llm_process → rag_save → save_memory
        self.graph.add_edge(START, "init")
        self.graph.add_edge("init", "load_memory")
        self.graph.add_edge("load_memory", "rag_retrieval")
        self.graph.add_edge("rag_retrieval", "llm_process")
        self.graph.add_edge("llm_process", "rag_save")
        self.graph.add_edge("rag_save", "save_memory")

        # 根据是否启用 TTS 和 Live2D 决定 save_memory 的下一个节点
        if self.enable_live2d:
            # Live2D 在 save_memory 之后、finalize 之前执行
            self.graph.add_edge("save_memory", "live2d")
            if enable_tts and tts_available:
                self.graph.add_edge("live2d", "tts")
                self.graph.add_edge("tts", "finalize")
            else:
                self.graph.add_edge("live2d", "finalize")
        elif enable_tts and tts_available:
            # 只启用 TTS
            self.graph.add_edge("save_memory", "tts")
            self.graph.add_edge("tts", "finalize")
        else:
            # 都不启用
            self.graph.add_edge("save_memory", "finalize")

        # 编译图
        self.graph = self.graph.compile(checkpointer=self.checkpointer)

        logger.info("LangGraph 图结构构建完成")

    def _rag_retrieval_node(self, state: LLMState) -> LLMState:
        """RAG 检索节点：从 Milvus 检索相关上下文"""
        logger.info("执行 RAG 检索节点")

        try:
            user_message = _extract_last_user_message(state)
            if not user_message:
                logger.warning("未找到用户消息，跳过 RAG 检索")
                return state

            rag_state = RAGState(
                query_text=user_message.get("content", ""),
                collection_name="chat_history",
                db_name="LLM_vtuber",
                query_params={"top_k": 3, "metric_type": "COSINE", "nprobe": 10},
                messages=state.get("messages", [])
            )

            rag_result = rag_retrieval_node(rag_state)

            state["context"] = rag_result.get("context")
            state["retrieved_documents"] = rag_result.get("retrieved_documents")
            logger.info(f"RAG 检索完成，找到 {rag_result.get('num_documents', 0)} 个文档")
            return state

        except Exception as e:
            logger.error(f"RAG 检索节点失败: {e}")
            return state

    async def _llm_process_node(self, state: LLMState) -> LLMState:
        """LLM 处理节点：调用大模型生成响应并解析格式"""
        logger.info("执行 LLM 处理节点")

        try:
            user_message = _extract_last_user_message(state)
            if not user_message:
                logger.warning("未找到用户消息，跳过 LLM 处理")
                return state

            # 根据是否启用Live2D选择系统提示词：
            # - 启用Live2D时使用带【语气】|目光|嘴巴 格式约束的提示词
            # - 未启用时使用普通提示词，LLM不会输出动作信息
            system_prompt = (
                SYSTEM_PROMPT_LIVE2D if self.enable_live2d else SYSTEM_PROMPT_DEFAULT
            )

            # 构建LLM状态
            llm_state = LLMState(
                messages=state.get("messages", []),
                system_prompt=system_prompt,
                model=DEFAULT_MODEL,
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
                question=user_message.get("content", ""),
                context=state.get("context"),
                name=user_message.get("name")
            )

            # 根据是否有上下文选择不同节点（均为 async 调用）
            llm_result = await (context_aware_qa_node(llm_state) if state.get("context")
                                else llm_chat_node(llm_state))

            # 更新状态
            response = llm_result.get("response", "")
            state["response"] = response
            state["messages"] = llm_result.get("messages", state.get("messages", []))
            state["error"] = llm_result.get("error")

            # 使用提取的解析函数（消除嵌套）
            parsed = parse_response_format(response, enable_live2d=self.enable_live2d)
            state["tone"] = parsed["tone"]
            state["content"] = parsed["content"]
            state["visual_focus"] = parsed["visual_focus"]
            state["mouth_state"] = parsed["mouth_state"]

            logger.info(f"LLM节点读取: enable_live2d={self.enable_live2d}")
            if self.enable_live2d:
                logger.info(f"LLM 处理完成，语气: {parsed['tone']}, 目光: {parsed['visual_focus']}, 嘴巴: {parsed['mouth_state']}")
            else:
                logger.info(f"LLM 处理完成，语气: {parsed['tone']} (Live2D未启用)")

            # WebSocket 推送 AI 回复（danmaku 已在 handle_post_request 中推送，避免重复）
            try:
                from webui.app import ws_manager, add_message_to_history
                await ws_manager.broadcast_ai_response(
                    content=parsed["content"],
                    tone=parsed["tone"],
                    visual_focus=parsed["visual_focus"],
                    mouth_state=parsed["mouth_state"]
                )
                add_message_to_history("ai_response", {
                    "content": parsed["content"],
                    "tone": parsed["tone"],
                    "visual_focus": parsed["visual_focus"],
                    "mouth_state": parsed["mouth_state"]
                })
            except Exception:
                pass

            return state

        except Exception as e:
            logger.error(f"LLM 处理节点失败: {e}")
            return state

    def _rag_save_node(self, state: LLMState) -> LLMState:
        """
        RAG 保存节点

        将对话消息保存到 Milvus 数据库中。

        Args:
            state: LLM状态

        Returns:
            更新后的LLM状态
        """
        logger.info("执行 RAG 保存节点")

        try:
            # 构建 RAG 状态
            rag_state = RAGState(
                query_text=state.get("question", ""),
                collection_name="chat_history",
                db_name="LLM_vtuber",
                query_params={
                    "top_k": 3,
                    "metric_type": "COSINE",
                    "nprobe": 10
                },
                messages=state.get("messages", []),
                response=state.get("response")
            )

            # 执行 RAG 保存
            rag_result = rag_save_node(rag_state)

            # 更新状态
            state["save_success"] = rag_result.get("save_success")

            if rag_result.get("save_success"):
                logger.info("RAG 保存成功")
            else:
                logger.warning("RAG 保存失败")

            return state

        except Exception as e:
            logger.error(f"RAG 保存节点失败: {e}")
            return state

    async def _load_memory_node(self, state: LLMState) -> LLMState:
        """
        加载记忆节点：从 InMemoryStore 加载用户历史对话到上下文中。

        在回答前检查是否有该用户的历史记忆，有则提取作为对话上下文。
        使用用户名作为记忆标识，确保同一用户的多轮对话可以共享记忆。
        """
        logger.info("执行加载记忆节点")

        try:
            user_message = _extract_last_user_message(state)
            user_name = ""
            if user_message:
                user_name = user_message.get("name", "")

            if not user_name:
                logger.info("无用户名，跳过加载记忆")
                return state

            # 使用用户名作为记忆标识
            user_id = f"user_{user_name}"
            memory_messages = await self.memory_manager.load_user_memory(user_id)

            if memory_messages:
                logger.info(f"加载到用户 {user_name} 的 {len(memory_messages)} 条历史记忆")
                # 将历史记忆注入到 messages 中
                existing_messages = state.get("messages", [])
                # 在现有消息之前插入历史记忆（作为对话上下文）
                state["messages"] = memory_messages + existing_messages
                state["memory_loaded"] = True
                state["memory_count"] = len(memory_messages)
            else:
                logger.info(f"用户 {user_name} 无历史记忆")
                state["memory_loaded"] = False
                state["memory_count"] = 0

            return state

        except Exception as e:
            logger.warning(f"加载记忆节点失败（降级处理）: {e}")
            state["memory_loaded"] = False
            return state

    async def _save_memory_node(self, state: LLMState) -> LLMState:
        """
        保存记忆节点：将本轮对话保存到 InMemoryStore。

        回答后更新短期记忆，供后续对话使用。
        使用用户名作为记忆标识，确保同一用户的多轮对话可以共享记忆。
        """
        logger.info("执行保存记忆节点")

        try:
            response = state.get("response", "")
            if not response:
                logger.info("无 AI 回答，跳过保存记忆")
                return state

            user_message = _extract_last_user_message(state)
            user_name = ""
            if user_message:
                user_name = user_message.get("name", "")

            if not user_name:
                logger.info("无用户名，跳过保存记忆")
                return state

            # 使用用户名作为记忆标识
            user_id = f"user_{user_name}"
            messages = state.get("messages", [])

            # 保存用户消息和 AI 回答到 InMemoryStore
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
                    await self.memory_manager.save_message(user_id, msg)

            # 保存上下文信息
            await self.memory_manager.save_context(user_id, {
                "question": state.get("question", ""),
                "context": state.get("context", ""),
                "source": "langgraph_save_memory",
                "username": user_name
            })

            memory_stats = await self.memory_manager.get_memory_stats(user_id)
            logger.info(f"记忆保存成功 (user={user_name}, 总消息数={memory_stats.get('message_count', 0)})")

            state["memory_saved"] = True
            return state

        except Exception as e:
            logger.warning(f"保存记忆节点失败（降级处理）: {e}")
            state["memory_saved"] = False
            return state

    async def run_with_messages(self, initial_state: LLMState = None):
        """运行图处理消息（合并了原run_with_messages和run两个重复方法）"""
        logger.info("开始运行 LangGraph 处理消息")

        global args
        if not self.graph:
            self.build_graph(
                enable_tts=args.tts if args else False,
                enable_live2d=args.live2d if args else False
            )

        if not initial_state:
            initial_state = create_default_llm_state()

        # 生成 thread_id 并注入 state 作为 session_id，供 memory 管理使用
        thread_id = f"thread_{uuid.uuid4()}"
        initial_state["thread_id"] = thread_id
        initial_state["session_id"] = thread_id

        logger.info(f"初始状态: {json.dumps(initial_state, ensure_ascii=False)}")
        logger.info("调用graph.ainvoke")

        result = await self.graph.ainvoke(
            initial_state,
            config={
                "recursion_limit": 10,
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": f"session_{uuid.uuid4()}"
                }
            }
        )

        logger.info(f"运行结果: {json.dumps(result, ensure_ascii=False)}")
        logger.info("LangGraph 运行完成")
        return result

    def _init_node(self, state: LLMState) -> LLMState:
        """初始化节点：设置默认字段和Live2D标志"""
        logger.info("执行初始化节点")

        state["enable_live2d"] = self.enable_live2d
        logger.info(f"初始化节点: enable_live2d={self.enable_live2d}")

        # 使用常量设置默认值
        state.setdefault("system_prompt", SYSTEM_PROMPT_DEFAULT)
        state.setdefault("model", DEFAULT_MODEL)
        state.setdefault("temperature", DEFAULT_TEMPERATURE)
        state.setdefault("max_tokens", DEFAULT_MAX_TOKENS)
        state.setdefault("messages", [])

        return state

    def _finalize_node(self, state: LLMState) -> LLMState:
        """最终处理节点"""
        logger.info("执行最终处理节点")
        return state

    async def handle_new_danmaku(self, danmaku_data):
        """处理新弹幕"""
        if self.processing_danmaku:
            return

        try:
            self.processing_danmaku = True
            logger.info(f"处理新弹幕: {danmaku_data['content']}")

            initial_state = create_default_llm_state(messages=[{
                "role": "user",
                "content": f"用户 {danmaku_data['user']['uname']} 说: {danmaku_data['content']}",
                "name": danmaku_data['user']['uname']
            }])

            result = await self.run_with_messages(initial_state)

            if result.get('messages'):
                last_message = result['messages'][-1]
                logger.info(f"AI 回复: {last_message['content'][:100]}...")

        except Exception as e:
            logger.error(f"处理新弹幕时发生错误: {e}")
        finally:
            self.processing_danmaku = False

    def stream(self, initial_state: LLMState = None):
        """流式运行图"""
        logger.info("开始流式运行 LangGraph")

        global args
        if not self.graph:
            self.build_graph(enable_tts=args.tts if args else False)

        if not initial_state:
            initial_state = create_default_llm_state()

        for chunk in self.graph.stream(
            initial_state,
            config={
                "configurable": {
                    "thread_id": f"thread_{uuid.uuid4()}",
                    "checkpoint_id": f"session_{uuid.uuid4()}"
                }
            }
        ):
            yield chunk

        logger.info("LangGraph 流式运行完成")

    async def start_danmaku_listener(self):
        """启动弹幕监听器（占位，实际监听由独立程序完成）"""
        logger.info("启动弹幕监听器，开始持续监听直播间弹幕")
        while True:
            await asyncio.sleep(1)

    def rebuild_graph(self, enable_tts=None, enable_live2d=None):
        """
        重新构建 LangGraph 图结构（用于动态启停功能）

        Args:
            enable_tts: 是否启用 TTS，None 则保持当前设置
            enable_live2d: 是否启用 Live2D，None 则保持当前设置
        """
        global args
        if args is None:
            args = parse_args()

        # 如果未指定，使用当前 args 的值
        if enable_tts is None:
            enable_tts = args.tts
        if enable_live2d is None:
            enable_live2d = args.live2d and live2d_available

        logger.info(f"重新构建图结构: TTS={enable_tts}, Live2D={enable_live2d}")
        self.build_graph(enable_tts=enable_tts, enable_live2d=enable_live2d)


# ============ 动态功能控制函数 ============

async def enable_live2d():
    """启用 Live2D 功能"""
    global args, live2d_manager

    if not live2d_available:
        logger.error("Live2D 模块不可用")
        return False

    if live2d_manager and live2d_manager.is_connected:
        logger.info("Live2D 已连接")
        return True

    if args is None:
        args = parse_args()

    logger.info("正在初始化 Live2D...")

    if live2d_manager is None:
        from live2d.live2d_controller_manager import Live2DConfig, Live2DControllerManager
        config = Live2DConfig(
            sensitivity=args.live2d_sensitivity,
            response_speed=args.live2d_speed,
            motion_smoothness=args.live2d_smoothness,
            eye_tracking_enabled=args.live2d_eye_tracking,
            expression_enabled=args.live2d_expression
        )
        live2d_manager = Live2DControllerManager(config)

    try:
        connected = await live2d_manager.connect(
            host=args.live2d_host,
            port=args.live2d_port
        )
        if connected:
            args.live2d = True
            logger.info("Live2D 已启用")
            if langgraph_manager:
                langgraph_manager.rebuild_graph(enable_live2d=True)
            return True
        else:
            logger.warning("Live2D 连接失败")
            return False
    except Exception as e:
        logger.error(f"Live2D 初始化失败: {e}")
        return False


async def disable_live2d():
    """禁用 Live2D 功能"""
    global args, live2d_manager

    if not (args and args.live2d):
        logger.info("Live2D 未启用")
        return True

    logger.info("正在禁用 Live2D...")

    if live2d_manager and live2d_manager.is_connected:
        await live2d_manager.disconnect()

    args.live2d = False
    logger.info("Live2D 已禁用")

    if langgraph_manager:
        langgraph_manager.rebuild_graph(enable_live2d=False)

    return True


async def enable_tts():
    """启用 TTS 功能"""
    global args

    if not tts_available:
        logger.error("TTS 模块不可用")
        return False

    if args is None:
        args = parse_args()

    if args.tts:
        logger.info("TTS 已启用")
        return True

    args.tts = True
    logger.info("TTS 已启用")

    if langgraph_manager:
        langgraph_manager.rebuild_graph(enable_tts=True)

    return True


async def disable_tts():
    """禁用 TTS 功能"""
    global args

    if not (args and args.tts):
        logger.info("TTS 未启用")
        return True

    args.tts = False
    logger.info("TTS 已禁用")

    if langgraph_manager:
        langgraph_manager.rebuild_graph(enable_tts=False)

    return True


async def start_danmaku_listener():
    """启动弹幕监听器"""
    global args, _bili_process

    if _bili_process and _bili_process.poll() is None:
        logger.info("弹幕监听器已在运行")
        return True

    if args is None:
        args = parse_args()

    logger.info("正在启动弹幕监听器...")
    start_bilibili_listener(args.room_id)

    # 等待子进程启动
    await asyncio.sleep(1)

    if _bili_process and _bili_process.poll() is None:
        logger.info("弹幕监听器已启动")
        return True
    else:
        logger.error("弹幕监听器启动失败")
        return False


async def stop_danmaku_listener():
    """停止弹幕监听器"""
    global _bili_process

    if not _bili_process or _bili_process.poll() is not None:
        logger.info("弹幕监听器未运行")
        return True

    logger.info("正在停止弹幕监听器...")
    try:
        _bili_process.terminate()
        try:
            _bili_process.wait(timeout=3)
        except Exception:
            _bili_process.kill()
            _bili_process.wait(timeout=1)
        logger.info("弹幕监听器已停止")
        _bili_process = None
        return True
    except Exception as e:
        logger.error(f"停止弹幕监听器失败: {e}")
        _bili_process = None
        return False

async def start_http_server(port=8081):
    """
    启动HTTP服务器（FastAPI + uvicorn）

    Args:
        port: 端口号

    Returns:
        uvicorn.Server 实例（可用于停止服务器）
    """
    # 读取配置
    import configparser as _cp
    _config = _cp.ConfigParser(interpolation=None)
    _config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    _config.read(_config_path, encoding='utf-8')

    webui_enabled = True
    if _config.has_section("webui"):
        webui_enabled = _config.getboolean("webui", "enabled", fallback=True)

    if not webui_enabled:
        logger.info("Web UI 控制台未启用 (config.ini [webui] enabled=false)")
        return None

    # 导入 Web UI 应用
    from webui.app import app, init_api

    # 初始化 API 模块（注入 config 引用）
    init_api(config=_config, config_path=_config_path)

    # 使用 uvicorn 启动服务器
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    # 在后台任务中启动（不阻塞主事件循环）
    server_task = asyncio.create_task(server.serve())

    logger.info(f"HTTP服务器已启动，监听端口 {port}")
    logger.info(f"Web UI 控制台: http://localhost:{port}/")
    logger.info(f"API 文档: http://localhost:{port}/docs")

    return server

def start_bilibili_listener(room_id=None):
    """
    启动哔哩哔哩直播监听程序（使用 bili_main.py 常驻监听，替换原 sample.py 仅5秒演示）

    bili_main.py 特性：
    - 自动读取 config.json 的 ROOM_IDS / output_port
    - 连接断开自动指数退避重连
    - 弹幕/礼物/SC 去重后 HTTP POST 到主程序 (127.0.0.1:<output_port>/)

    Args:
        room_id: 直播间号；若指定则写入 config.json 覆盖默认
    """
    import subprocess
    import sys

    broadcast_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast")
    script_path = os.path.join(broadcast_dir, "bili_main.py")
    cmd = [sys.executable, script_path]

    # 如果指定了房间号，修改config.json
    if room_id is not None:
        config_path = os.path.join(broadcast_dir, "config.json")
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            config['ROOM_IDS'] = str(room_id)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info(f"已更新直播间号为: {room_id}")
        except Exception as e:
            logger.error(f"更新直播间号失败: {e}")

    # 启动监听程序（子进程）
    logger.info(f"启动哔哩哔哩直播监听程序: {' '.join(cmd)}  cwd={broadcast_dir}")
    try:
        global _bili_process
        _bili_process = subprocess.Popen(
            cmd,
            cwd=broadcast_dir,
            stdout=None,   # 继承父进程stdout（用户能看到弹幕打印）
            stderr=None,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except Exception as e:
        logger.error(f"启动弹幕监听器失败: {e}")


def _cleanup_port(port: int):
    """
    清理占用指定端口的进程（Windows 环境）

    避免 OSError: [Errno 10048] 端口冲突错误。
    使用 netstat 查找占用端口的进程 PID，然后终止该进程。
    同时尝试清理本项目 (bili_main.py / 自身) 的残留子进程。

    Args:
        port: 需要清理的端口号
    """
    if os.name != 'nt':
        return

    import subprocess
    import re
    try:
        # 中文 Windows 上 netstat / wmic 的输出编码是 GBK，需要显式指定
        win_encoding = 'gbk'
        # 精确匹配本端口（避免 :19999 匹配到 :199990 / :199991 等）
        port_pattern = re.compile(rf':{port}(\s|$)')

        # 1. 找出所有占用该端口的 PID
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True, timeout=5,
            encoding=win_encoding, errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        pids_to_kill = set()
        my_pid = os.getpid()

        for line in (result.stdout or '').splitlines():
            if 'LISTENING' not in line:
                continue
            if not port_pattern.search(line):
                continue
            parts = line.strip().split()
            if len(parts) < 5:
                continue
            try:
                pid = int(parts[-1])
            except ValueError:
                continue
            if pid != my_pid:
                pids_to_kill.add(pid)

        # 2. 额外：尝试杀掉所有 bili_main.py 子进程（它们是本项目启动的弹幕监听，可能残留）
        try:
            proc_result = subprocess.run(
                ['wmic', 'process', 'where',
                 "commandline like '%bili_main%'",
                 'get', 'processid,commandline'],
                capture_output=True, timeout=5,
                encoding=win_encoding, errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            for line in (proc_result.stdout or '').splitlines():
                line = line.strip()
                if not line or line.startswith('ProcessId') or line.startswith('CommandLine'):
                    continue
                for token in line.split():
                    if token.isdigit() and int(token) != my_pid:
                        pids_to_kill.add(int(token))
        except Exception:
            pass  # wmic 可能在某些系统不可用，忽略

        # 3. 终止这些 PID
        for pid in sorted(pids_to_kill):
            logger.info(f"发现端口 {port} 残留 PID {pid}，正在终止...")
            try:
                subprocess.run(
                    ['taskkill', '/PID', str(pid), '/F'],
                    capture_output=True, timeout=5,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
            except Exception as e:
                logger.warning(f"终止 PID {pid} 失败: {e}")

        if pids_to_kill:
            import time
            time.sleep(1.0)  # 等待端口释放
            logger.info(f"已清理端口 {port} 上的 {len(pids_to_kill)} 个残留进程")
        else:
            logger.info(f"端口 {port} 未被占用")
    except Exception as e:
        logger.warning(f"端口清理失败（可忽略）: {e}")


def main():
    """
    主函数

    启动模式：
    - 默认：仅启动 Web 控制台，功能需通过 UI 手动启动
    - --all：启动所有功能（Live2D + TTS + 弹幕监听）
    - --live2d/--tts：启动指定功能
    """
    global args, langgraph_manager, live2d_manager, _bili_process

    # 解析命令行参数
    args = parse_args()

    # --all 模式：启用所有功能
    if args.all:
        args.live2d = True
        args.tts = True
        logger.info("使用 --all 模式：启动所有功能")

    # 加载 config.ini 作为默认值来源（命令行参数优先级更高）
    config_ini_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    _cfg = configparser.ConfigParser(interpolation=None)
    _cfg.read(config_ini_path, encoding='utf-8')

    # 若命令行未显式指定 --live2d，则读取 config.ini [live2d] enabled
    if not args.live2d and _cfg.has_section("live2d"):
        args.live2d = _cfg.getboolean("live2d", "enabled", fallback=False)
        if args.live2d:
            logger.info(f"Live2D 已从 config.ini 启用")
    if not args.live2d_host and _cfg.has_section("live2d"):
        args.live2d_host = _cfg.get("live2d", "host", fallback="localhost")
    if args.live2d_port == 8001 and _cfg.has_section("live2d"):
        args.live2d_port = _cfg.getint("live2d", "port", fallback=8001)
    if args.live2d_sensitivity == 1.0 and _cfg.has_section("live2d"):
        args.live2d_sensitivity = _cfg.getfloat("live2d", "sensitivity", fallback=1.0)
    if args.live2d_speed == 2.5 and _cfg.has_section("live2d"):
        args.live2d_speed = _cfg.getfloat("live2d", "response_speed", fallback=2.5)
    if args.live2d_smoothness == 0.8 and _cfg.has_section("live2d"):
        args.live2d_smoothness = _cfg.getfloat("live2d", "smoothness", fallback=0.8)
    if _cfg.has_section("live2d"):
        args.live2d_eye_tracking = _cfg.getboolean("live2d", "eye_tracking", fallback=True)
        args.live2d_expression = _cfg.getboolean("live2d", "expression", fallback=True)

    # 若命令行未显式指定 --tts，则读取 config.ini [tts] enabled
    if not args.tts and _cfg.has_section("tts"):
        args.tts = _cfg.getboolean("tts", "enabled", fallback=False)
        if args.tts:
            logger.info("TTS 已从 config.ini 启用")

    # 如果未指定 room_id，从 config.json 读取默认值
    if args.room_id is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast", "config.json")
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            room_ids = config.get("ROOM_IDS", "")
            if room_ids:
                args.room_id = int(room_ids)
                logger.info(f"未指定直播间号，使用配置文件中的默认值: {args.room_id}")
            else:
                logger.warning("配置文件中未设置 ROOM_IDS")
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")

    # 清理可能占用的端口（避免 OSError: [Errno 10048]）
    _cleanup_port(8081)

    logger.info(f"启动模式: live2d={args.live2d}, tts={args.tts}, all={args.all}, room_id={args.room_id}")

    async def run_async():
        logger.info("=== 启动 LangGraph 主程序 ===")

        # 检查环境变量
        if not os.getenv("Doubao_API_KEY"):
            logger.warning("警告: 环境变量 Doubao_API_KEY 未设置")
            logger.warning("将使用模拟响应进行测试")

        # 加载浏览器工具配置并启动浏览器
        browser_enabled = False
        try:
            import configparser
            config = configparser.ConfigParser(interpolation=None)
            config_ini_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
            config.read(config_ini_path, encoding='utf-8')

            if config.has_section("browser") and config.getboolean("browser", "enabled", fallback=False):
                from tool.browser_tool import browser_manager, configure_from_ini
                configure_from_ini(config)
                await browser_manager.start(headless=config.getboolean("browser", "headless", fallback=True))
                browser_enabled = True
                logger.info("浏览器工具已启用")
            else:
                logger.info("浏览器工具未启用（config.ini [browser] enabled=false 或无配置）")
        except Exception as e:
            logger.warning(f"浏览器工具初始化失败（不影响主程序运行）: {e}")

        # 加载视觉分析工具配置
        try:
            import configparser
            config = configparser.ConfigParser(interpolation=None)
            config_ini_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
            config.read(config_ini_path, encoding='utf-8')

            if config.has_section("vision") and config.getboolean("vision", "enabled", fallback=False):
                from tool.vision_tool import configure_from_ini as vision_configure
                vision_configure(config)
                logger.info("视觉分析工具已启用")
            else:
                logger.info("视觉分析工具未启用（config.ini [vision] enabled=false 或无配置）")
        except Exception as e:
            logger.warning(f"视觉分析工具初始化失败（不影响主程序运行）: {e}")

        # 初始化 Live2D 管理器
        if args.live2d and live2d_available:
            logger.info("初始化 Live2D 控制器管理器...")
            global live2d_manager

            config = Live2DConfig(
                sensitivity=args.live2d_sensitivity,
                response_speed=args.live2d_speed,
                motion_smoothness=args.live2d_smoothness,
                eye_tracking_enabled=args.live2d_eye_tracking,
                expression_enabled=args.live2d_expression
            )

            live2d_manager = Live2DControllerManager(config)

            try:
                connected = await live2d_manager.connect(
                    host=args.live2d_host,
                    port=args.live2d_port
                )

                if connected:
                    logger.info("Live2D 控制器连接成功")
                else:
                    logger.warning("Live2D 控制器连接失败")
                    args.live2d = False  # 连接失败时重置标记
                    live2d_manager = None
            except Exception as e:
                logger.error(f"Live2D 控制器初始化失败: {e}")
                args.live2d = False  # 异常时重置标记
                live2d_manager = None

        # 创建 LangGraph 管理器
        global langgraph_manager
        langgraph_manager = LangGraphManager()

        # 构建图（基础结构 + 可选功能节点）
        langgraph_manager.build_graph(
            enable_tts=args.tts if args else False,
            enable_live2d=args.live2d and live2d_available if args else False
        )

        # 初始化消息队列并启动 worker（必须在 HTTP 服务器启动前初始化，否则 API 调用时队列为空）
        global _message_queue, _queue_worker_task
        _message_queue = asyncio.Queue(maxsize=_QUEUE_MAX_SIZE)
        _queue_worker_task = asyncio.create_task(_queue_worker())

        # 启动HTTP服务器（Web 控制台）
        runner = await start_http_server()
        logger.info("Web 控制台已启动，可通过 UI 控制功能")

        # 启动时自动启用的功能（通过 --all 或 config.ini 配置）
        auto_start_features = args.all if args else False

        # 如果 Live2D 在启动时已启用（通过 --all 或 config.ini），等待连接完成
        if args and args.live2d and live2d_available:
            logger.info("Live2D 将在启动时自动启用")
        elif not args or not args.live2d:
            logger.info("Live2D 未在启动时启用，可通过控制台手动启动")

        # 如果使用 --all 模式，自动启动弹幕监听
        if auto_start_features and args and args.room_id:
            start_bilibili_listener(args.room_id)
            logger.info("弹幕监听器已通过 --all 模式启动")
        else:
            logger.info("弹幕监听器未自动启动，可通过控制台手动启动")

        try:
            # 持续运行
            logger.info("主程序已启动，等待请求...")
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("收到中断信号，正在关闭...")
        except Exception as e:
            logger.error(f"运行 LangGraph 时发生错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 终止弹幕监听子进程，避免残留占用端口
            global _bili_process
            if _bili_process is not None:
                try:
                    _bili_process.terminate()
                    try:
                        _bili_process.wait(timeout=3)
                    except Exception:
                        _bili_process.kill()
                        _bili_process.wait(timeout=1)
                    logger.info("弹幕监听子进程已终止")
                except Exception as e:
                    logger.warning(f"终止弹幕监听子进程失败: {e}")
                _bili_process = None

            # 取消队列 worker
            if _queue_worker_task and not _queue_worker_task.done():
                _queue_worker_task.cancel()
                try:
                    await _queue_worker_task
                except asyncio.CancelledError:
                    pass
                logger.info("消息队列 worker 已停止")

            # 关闭浏览器
            if browser_enabled:
                try:
                    from tool.browser_tool import browser_manager
                    await browser_manager.stop()
                except Exception as e:
                    logger.warning(f"关闭浏览器失败: {e}")

            # 关闭 Live2D 连接
            if live2d_manager:
                await live2d_manager.disconnect()
                logger.info("Live2D 连接已关闭")

            # 关闭HTTP服务器
            if 'runner' in locals():
                await runner.cleanup()
                logger.info("HTTP服务器已关闭")

    # 运行异步函数
    asyncio.run(run_async())


if __name__ == "__main__":
    main()