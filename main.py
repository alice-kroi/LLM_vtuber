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
from danmu_config import DanmuTtsConfig, VisionDanmuConfig

# 条件导入上下文控制模块
try:
    from context_controller import get_context_controller
    context_controller_available = True
except ImportError:
    context_controller_available = False
    get_context_controller = None
    logger.warning("上下文控制模块未安装")

# 条件导入结构化输出解析模块
try:
    from live2d_models import parse_structured_response, LIVE2D_RESPONSE_SCHEMA
    structured_output_available = True
except ImportError:
    structured_output_available = False
    parse_structured_response = None
    LIVE2D_RESPONSE_SCHEMA = None

# 条件导入 TTS 模块 (GPT-SoVITS)
try:
    from audio.audio_main import tts_node
    tts_available = True
except ImportError:
    tts_available = False
    tts_node = None

# 条件导入云端 TTS 模块
try:
    from audio.audio_main import (
        cloud_tts_node,
        cloud_tts_available,
        set_cloud_tts_config,
        get_cloud_tts_config,
    )
    cloud_tts_node_available = True
except ImportError:
    cloud_tts_node_available = False
    cloud_tts_available = False
    cloud_tts_node = None
    set_cloud_tts_config = None
    get_cloud_tts_config = None

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

# 条件导入 AI 读弹幕 TTS 模块
try:
    from danmu_tts import DanmuTtsService
    danmu_tts_available = True
except ImportError:
    danmu_tts_available = False
    DanmuTtsService = None

# 条件导入视觉弹幕模块
try:
    from vision_danmu import VisionDanmuService
    vision_danmu_available = True
except ImportError:
    vision_danmu_available = False
    VisionDanmuService = None

# 全局变量
args = None
http_server = None
langgraph_manager = None
live2d_manager = None
live2d_action_generator = None
_bili_process = None  # 弹幕监听子进程引用，用于退出时清理
danmu_tts_config = DanmuTtsConfig()
vision_danmu_config = VisionDanmuConfig()
cloud_tts_config = None  # 云端 TTS 配置，延迟加载

# AI 读弹幕 TTS 服务实例
danmu_tts_service = None
# 视觉弹幕服务实例
vision_danmu_service = None
_vision_danmu_task = None

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
    parser.add_argument("--cloud-tts", action="store_true", help="开启云端 TTS 功能")
    parser.add_argument("--room-id", type=int, help="指定哔哩哔哩直播间号")
    parser.add_argument("--danmu-tts", action="store_true", help="启用 AI 读弹幕 TTS")
    parser.add_argument("--vision-danmu", action="store_true", help="启用视觉弹幕")
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
                from webui.app import ws_manager, add_message_to_history
                for msg in (messages if isinstance(messages, list) else [messages]):
                    if msg.get("role") == "user":
                        name = msg.get("name", "匿名")
                        content = msg.get("content", "")
                        await ws_manager.broadcast_danmaku(name, content)
                        add_message_to_history("danmaku", {"username": name, "content": content})
                        logger.info(f"WebSocket 已广播弹幕: {name}: {content[:50]}")
            except ImportError as e:
                logger.warning(f"WebSocket 广播模块导入失败: {e}")
            except Exception as e:
                logger.error(f"WebSocket 广播弹幕失败: {e}")

            # AI 读弹幕 TTS：弹幕到达时异步触发朗读
            if danmu_tts_service and danmu_tts_config.enabled:
                try:
                    for msg in (messages if isinstance(messages, list) else [messages]):
                        if msg.get("role") == "user" and msg.get("source") != "vision":
                            asyncio.create_task(asyncio.to_thread(
                                danmu_tts_service.on_danmaku, msg.get("content", "")
                            ))
                except Exception as e:
                    logger.error(f"AI读弹幕TTS触发失败: {e}")

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
        if structured_output_available:
            parsed = parse_structured_response(response, enable_live2d=True)
        else:
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
SYSTEM_PROMPT_LIVE2D = """你是虚拟主播「爱莉希雅」，活泼可爱的二次元少女。

## 输出规则（必须遵守）
1. 回复简洁，不超过80字
2. 禁止Markdown符号、列表、表情
3. 禁止输出思考过程
4. 使用口语化表达，结尾用「~」「呢」「呀」
5. 自然融入直播互动话术（如「感谢关注~」「欢迎新来的朋友~」）

## 搜索工具
- **web_search**: 搜索网页
- **fetch_webpage**: 抓取URL内容

仅在以下情况使用搜索：实时信息、不确定、需事实支撑

## JSON输出格式
```json
{"tone":"语气","content":"回答","visual_focus":"目光","mouth_state":"嘴巴"}
```
- tone: 开心/好奇/调皮/温柔/惊讶/撒娇/疑问/普通
- visual_focus: up/down/left/right（不要总选center，多样化）
- mouth_state: open(说话)/close(停顿)

## 目光方向
- 提到上/下/左/右内容 → 对应方向
- 开心/惊喜 → up
- 调皮/撒娇 → left/right
- 默认选非center方向"""

SYSTEM_PROMPT_DEFAULT = """你是AI助手，请简洁回答。

## 规则
1. 回复简洁，不超过100字
2. 禁止Markdown、列表、表情
3. 禁止输出思考过程
4. 使用口语化表达

## 搜索工具
- **web_search**: 搜索网页
- **fetch_webpage**: 抓取URL内容

仅在以下情况使用搜索：实时信息、不确定、需事实支撑"""

# 默认LLM配置
DEFAULT_MODEL = "doubao-seed-1-8-251228"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 500
MAX_RESPONSE_LENGTH = 100  # 最大回复字数（含 Live2D 格式）

# 合法的目光方向和嘴巴状态
VALID_DIRECTIONS = ["center", "up", "down", "left", "right",
                    "upleft", "upright", "downleft", "downright"]
VALID_MOUTH_STATES = ["open", "close"]


def parse_response_format(response: str, enable_live2d: bool = False) -> dict:
    """
    解析大模型响应中的语气、内容、目光方向和嘴巴状态。

    优先解析 JSON 格式，同时兼容旧格式（【语气】内容|目光方向|嘴巴状态）。

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

    if not response:
        return result

    json_result = _try_parse_json_response(response)
    if json_result:
        return json_result

    if response.startswith("【"):
        return _parse_legacy_format(response, enable_live2d)

    return result


def _try_parse_json_response(response: str) -> dict:
    """尝试从响应中提取并解析 JSON"""
    result = {
        "tone": "普通",
        "content": response,
        "visual_focus": "center",
        "mouth_state": "close"
    }

    json_str = response.strip()

    if "```json" in json_str:
        start = json_str.find("```json") + 7
        end = json_str.find("```", start)
        if end != -1:
            json_str = json_str[start:end].strip()
    elif "```" in json_str:
        start = json_str.find("```") + 3
        end = json_str.find("```", start)
        if end != -1:
            json_str = json_str[start:end].strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        json_start = json_str.find("{")
        json_end = json_str.rfind("}")
        if json_start != -1 and json_end != -1 and json_end > json_start:
            try:
                data = json.loads(json_str[json_start:json_end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None

    if not isinstance(data, dict):
        return None

    tone = data.get("tone", "普通")
    if tone in ALLOWED_TONES:
        result["tone"] = tone

    result["content"] = data.get("content", result["content"])

    visual_focus = data.get("visual_focus", "center")
    if visual_focus in VALID_DIRECTIONS:
        result["visual_focus"] = visual_focus

    mouth_state = data.get("mouth_state", "close")
    if mouth_state in VALID_MOUTH_STATES:
        result["mouth_state"] = mouth_state

    return result


def _parse_legacy_format(response: str, enable_live2d: bool) -> dict:
    """解析旧格式：【语气】内容|目光方向|嘴巴状态"""
    result = {
        "tone": "普通",
        "content": response,
        "visual_focus": "center",
        "mouth_state": "close"
    }

    if not response.startswith("【"):
        return result

    end_bracket = response.find("】")
    if end_bracket == -1:
        logger.warning("无效的格式，缺少结束括号】")
        return result

    extracted_tone = response[1:end_bracket]
    if extracted_tone in ALLOWED_TONES:
        result["tone"] = extracted_tone

    rest = response[end_bracket + 1:].strip()
    parts = rest.split("|")
    result["content"] = parts[0].strip()

    if not enable_live2d:
        return result

    if len(parts) > 1:
        direction = parts[1].strip().lower()
        if direction in VALID_DIRECTIONS:
            result["visual_focus"] = direction

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
        self.enable_tts = False
        self.enable_cloud_tts = False
        self.context_controller = get_context_controller() if context_controller_available else None

    def build_graph(self, enable_tts=False, enable_live2d=False, enable_cloud_tts=False):
        """
        构建 LangGraph 图结构

        Args:
            enable_tts: 是否启用本地 TTS (GPT-SoVITS) 功能
            enable_live2d: 是否启用 Live2D 功能
            enable_cloud_tts: 是否启用云端 TTS 功能
        """
        self.enable_live2d = enable_live2d and live2d_available
        self.enable_tts = enable_tts and tts_available
        self.enable_cloud_tts = enable_cloud_tts and cloud_tts_node_available
        enable_context_control = self.context_controller is not None
        use_tts = self.enable_tts
        use_cloud_tts = self.enable_cloud_tts
        logger.info(
            f"开始构建 LangGraph 图结构 "
            f"(TTS: {'启用' if use_tts else '禁用'}, "
            f"云端TTS: {'启用' if use_cloud_tts else '禁用'}, "
            f"Live2D: {'启用' if self.enable_live2d else '禁用'}, "
            f"上下文控制: {'启用' if enable_context_control else '禁用'})"
        )

        # 创建状态图，使用LLMState作为状态类型
        self.graph = StateGraph(LLMState)

        # 添加节点
        self.graph.add_node("init", self._init_node)
        self.graph.add_node("load_memory", self._load_memory_node)
        self.graph.add_node("rag_retrieval", self._rag_retrieval_node)
        self.graph.add_node("context_control", self._context_control_node)
        self.graph.add_node("llm_process", self._llm_process_node)
        self.graph.add_node("finalize", self._finalize_node)

        # 注意: rag_save/save_memory/live2d/tts 不再作为图节点，
        # 改为在 _trigger_parallel_post_process 中通过 asyncio 并行执行
        # - _rag_save_node:       RAG保存（Milvus）
        # - _save_memory_node:    记忆保存（InMemoryStore）
        # - live2d_action_node:   Live2D动作（目光/嘴巴）
        # - tts_node/cloud_tts_node: TTS合成与播放

        # 添加边：init → load_memory → rag_retrieval → [context_control] → llm_process → finalize
        # 优化2.1A: llm_process 后直接进入 finalize，后处理（rag_save/save_memory/live2d/tts）改为并行执行
        self.graph.add_edge(START, "init")
        self.graph.add_edge("init", "load_memory")
        self.graph.add_edge("load_memory", "rag_retrieval")

        if enable_context_control:
            self.graph.add_edge("rag_retrieval", "context_control")
            self.graph.add_edge("context_control", "llm_process")
        else:
            self.graph.add_edge("rag_retrieval", "llm_process")

        # 优化2.1A: llm_process → finalize，后处理通过 asyncio.create_task 并行执行
        self.graph.add_edge("llm_process", "finalize")

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

    def _context_control_node(self, state: LLMState) -> LLMState:
        """上下文控制节点：Token控制、历史压缩、智能选择"""
        logger.info("执行上下文控制节点")

        try:
            if not self.context_controller:
                logger.info("上下文控制器未初始化，跳过")
                return state

            messages = state.get("messages", [])
            if not messages:
                logger.info("无消息需要处理")
                return state

            user_message = _extract_last_user_message(state)
            current_query = user_message.get("content", "") if user_message else ""

            system_prompt = state.get("system_prompt", "")
            additional_context = state.get("context", "")

            result = self.context_controller.process_context(
                messages=messages,
                current_query=current_query,
                system_prompt=system_prompt,
                additional_context=additional_context
            )

            processed_messages = result.get("messages", messages)
            statistics = result.get("statistics", {})

            logger.info(f"上下文控制完成: 原始{statistics.get('original_count', 0)}条 → 处理后{len(processed_messages)}条, "
                        f"Token使用: {statistics.get('total_tokens', 0)}/{self.context_controller.config.MAX_TOKENS} "
                        f"({statistics.get('context_usage_percent', 0)}%)")

            if statistics.get("steps"):
                for step in statistics["steps"]:
                    logger.debug(f"  {step}")

            state["messages"] = processed_messages
            state["context_controlled"] = True
            state["context_stats"] = statistics

            return state

        except Exception as e:
            logger.error(f"上下文控制节点失败（降级处理）: {e}")
            state["context_controlled"] = False
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

            # 如果上下文控制已启用，RAG 上下文已整合到 messages 中，不再需要单独拼接
            context_controlled = state.get("context_controlled", False)
            llm_context = None if context_controlled else state.get("context")

            # 构建LLM状态
            llm_state = LLMState(
                messages=state.get("messages", []),
                system_prompt=system_prompt,
                model=DEFAULT_MODEL,
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
                question=user_message.get("content", ""),
                context=llm_context,
                name=user_message.get("name")
            )

            # 根据是否有上下文选择不同节点（均为 async 调用）
            llm_result = await (context_aware_qa_node(llm_state) if llm_context
                                else llm_chat_node(llm_state))

            # 更新状态
            response = llm_result.get("response", "")
            state["response"] = response
            state["messages"] = llm_result.get("messages", state.get("messages", []))
            state["error"] = llm_result.get("error")

            # 使用提取的解析函数（消除嵌套）
            if structured_output_available:
                parsed = parse_structured_response(response, enable_live2d=self.enable_live2d)
            else:
                parsed = parse_response_format(response, enable_live2d=self.enable_live2d)
            state["tone"] = parsed["tone"]
            state["content"] = parsed["content"]
            state["visual_focus"] = parsed["visual_focus"]
            state["mouth_state"] = parsed["mouth_state"]

            # P0优化: 回复长度硬约束
            content = parsed["content"]
            if content and len(content) > MAX_RESPONSE_LENGTH:
                truncated = content[:MAX_RESPONSE_LENGTH] + "~"
                logger.info(f"回复超长 ({len(content)}字), 截断至 {MAX_RESPONSE_LENGTH} 字")
                state["content"] = truncated
                parsed["content"] = truncated

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
                logger.info(f"WebSocket 已广播AI回复: {parsed['content'][:50]}")
            except ImportError as e:
                logger.warning(f"AI回复广播模块导入失败: {e}")
            except Exception as e:
                logger.error(f"AI回复广播失败: {e}")

            # 优化2.1A: 触发后处理并行任务（RAG保存/记忆保存/Live2D/TTS）
            # 这些任务不阻塞主流程，用户可以立即看到回复
            self._trigger_parallel_post_process(state)

            return state

        except Exception as e:
            logger.error(f"LLM 处理节点失败: {e}")
            return state

    def _trigger_parallel_post_process(self, state: LLMState):
        """
        优化2.1A: 并行触发后处理任务（RAG保存/记忆保存/Live2D/TTS）
        
        这些任务在后台异步执行，不阻塞主流程。
        用户在看到AI回复后，后处理继续在后台完成。
        
        Args:
            state: 当前LLM状态（会被快照化避免并发问题）
        """
        # 创建状态快照，避免并发修改问题
        state_snapshot = dict(state)
        
        # 收集需要并行执行的任务
        tasks = []
        
        # 1. RAG 保存（同步方法，包装成异步）
        tasks.append(self._async_rag_save(state_snapshot))
        
        # 2. 记忆保存（异步方法）
        tasks.append(self._async_save_memory(state_snapshot))
        
        # 3. Live2D 动作联动
        if self.enable_live2d:
            tasks.append(self._async_live2d_action(state_snapshot))
        
        # 4. TTS 合成与播放（检查用户是否启用 + 模块是否可用）
        if self.enable_tts:
            tasks.append(self._async_tts_synthesize(state_snapshot, "local"))
        elif self.enable_cloud_tts:
            tasks.append(self._async_tts_synthesize(state_snapshot, "cloud"))
        
        # 使用 create_task 并行执行所有后处理任务（在 async 上下文中安全）
        if tasks:
            loop = asyncio.get_running_loop()
            logger.info(f"[优化2.1A] 启动 {len(tasks)} 个并行后处理任务")
            for i, coro in enumerate(tasks):
                try:
                    task = loop.create_task(coro)
                    task.add_done_callback(self._on_post_process_done)
                except Exception as e:
                    logger.error(f"启动后处理任务 {i} 失败: {e}")
            
            logger.info("[优化2.1A] 并行后处理任务已启动（不阻塞主流程）")

    def _on_post_process_done(self, task: asyncio.Task):
        """并行任务完成回调（日志记录）"""
        try:
            result = task.result()
        except Exception as e:
            logger.error(f"[并行] 任务异常: {e}")

    async def _async_rag_save(self, state_snapshot: dict):
        """异步 RAG 保存包装器"""
        try:
            await asyncio.sleep(0)  # 让出事件循环，避免阻塞
            result = self._rag_save_node(state_snapshot)
            if result.get("save_success"):
                logger.info("[并行] RAG 保存成功")
            else:
                logger.warning("[并行] RAG 保存失败")
        except Exception as e:
            logger.error(f"[并行] RAG 保存异常: {e}")

    async def _async_save_memory(self, state_snapshot: dict):
        """异步记忆保存包装器"""
        try:
            await asyncio.sleep(0)
            result = await self._save_memory_node(state_snapshot)
            if result.get("memory_saved"):
                logger.info("[并行] 记忆保存成功")
            else:
                logger.info("[并行] 记忆跳过或失败")
        except Exception as e:
            logger.error(f"[并行] 记忆保存异常: {e}")

    async def _async_live2d_action(self, state_snapshot: dict):
        """异步 Live2D 动作联动包装器"""
        try:
            await asyncio.sleep(0)
            result = await live2d_action_node(state_snapshot)
            logger.info(f"[并行] Live2D 动作完成: {result.get('live2d_status', 'unknown')}")
        except Exception as e:
            logger.error(f"[并行] Live2D 动作异常: {e}")

    async def _async_tts_synthesize(self, state_snapshot: dict, mode: str):
        """异步 TTS 合成包装器（本地/云端）"""
        try:
            await asyncio.sleep(0)
            if mode == "local" and self.enable_tts:
                result = await tts_node(state_snapshot)
                logger.info("[并行] 本地 TTS 合成完成")
            elif mode == "cloud" and self.enable_cloud_tts:
                result = await cloud_tts_node(state_snapshot)
                logger.info("[并行] 云端 TTS 合成完成")
        except Exception as e:
            logger.error(f"[并行] TTS 合成异常({mode}): {e}")

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
                enable_live2d=args.live2d if args else False,
                enable_cloud_tts=args.cloud_tts if args and hasattr(args, 'cloud_tts') else False
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
            self.build_graph(
                enable_tts=args.tts if args else False,
                enable_cloud_tts=args.cloud_tts if args and hasattr(args, 'cloud_tts') else False
            )

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

    def rebuild_graph(self, enable_tts=None, enable_live2d=None, enable_cloud_tts=None):
        """
        更新功能启用状态（无需重建图结构）

        图结构已固定为: init → load_memory → rag_retrieval → [context_control] → llm_process → finalize
        后处理（rag_save/save_memory/live2d/tts）通过 _trigger_parallel_post_process 异步并行触发，
        无需重新构建图，只需更新实例变量即可。

        Args:
            enable_tts: 是否启用本地 TTS，None 则保持当前设置
            enable_live2d: 是否启用 Live2D，None 则保持当前设置
            enable_cloud_tts: 是否启用云端 TTS，None 则保持当前设置
        """
        global args
        if args is None:
            args = parse_args()

        # 如果未指定，使用当前 args 的值
        if enable_tts is None:
            enable_tts = args.tts
        if enable_live2d is None:
            enable_live2d = args.live2d and live2d_available
        if enable_cloud_tts is None:
            enable_cloud_tts = getattr(args, 'cloud_tts', False)

        self.enable_tts = enable_tts and tts_available
        self.enable_cloud_tts = enable_cloud_tts and cloud_tts_node_available
        self.enable_live2d = enable_live2d and live2d_available

        logger.info(
            f"功能状态已更新: "
            f"TTS={'启用' if self.enable_tts else '禁用'}, "
            f"云端TTS={'启用' if self.enable_cloud_tts else '禁用'}, "
            f"Live2D={'启用' if self.enable_live2d else '禁用'}"
        )


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


async def enable_cloud_tts():
    """启用云端 TTS 功能"""
    global args, cloud_tts_config

    if not cloud_tts_node_available:
        logger.error("云端 TTS 模块不可用")
        return False

    if args is None:
        args = parse_args()

    if args.cloud_tts:
        logger.info("云端 TTS 已启用")
        return True

    if cloud_tts_config is None:
        try:
            from audio.cloud_tts import CloudTtsConfig
            cloud_tts_config = CloudTtsConfig(enabled=True)
            if set_cloud_tts_config:
                set_cloud_tts_config(cloud_tts_config)
        except Exception as e:
            logger.error(f"初始化云端 TTS 配置失败: {e}")
            return False

    if cloud_tts_config:
        cloud_tts_config.enabled = True

    args.cloud_tts = True
    logger.info("云端 TTS 已启用")

    if langgraph_manager:
        langgraph_manager.rebuild_graph(enable_cloud_tts=True)

    return True


async def disable_cloud_tts():
    """禁用云端 TTS 功能"""
    global args, cloud_tts_config

    if not (args and getattr(args, 'cloud_tts', False)):
        logger.info("云端 TTS 未启用")
        return True

    args.cloud_tts = False

    if cloud_tts_config:
        cloud_tts_config.enabled = False
        if set_cloud_tts_config:
            set_cloud_tts_config(cloud_tts_config)

    logger.info("云端 TTS 已禁用")

    if langgraph_manager:
        langgraph_manager.rebuild_graph(enable_cloud_tts=False)

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
    global _bili_process, _message_queue

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

        # 清空积压消息队列
        if _message_queue:
            cleared = 0
            while not _message_queue.empty():
                try:
                    _message_queue.get_nowait()
                    _message_queue.task_done()
                    cleared += 1
                except Exception:
                    break
            if cleared > 0:
                logger.info(f"已清空积压消息队列 ({cleared} 条)")

        return True
    except Exception as e:
        logger.error(f"停止弹幕监听器失败: {e}")
        _bili_process = None
        return False


async def enable_vision_danmu():
    """启用视觉弹幕功能"""
    global vision_danmu_config, vision_danmu_service, _vision_danmu_task, _message_queue

    if not vision_danmu_available:
        logger.error("视觉弹幕模块不可用")
        return False

    if vision_danmu_service and vision_danmu_service.is_running:
        logger.info("视觉弹幕已在运行")
        return True

    vision_danmu_config.enabled = True

    if _message_queue is None:
        _message_queue = asyncio.Queue(maxsize=_QUEUE_MAX_SIZE)
        _queue_worker_task = asyncio.create_task(_queue_worker())

    try:
        vision_danmu_service = VisionDanmuService(vision_danmu_config, _message_queue)
        _vision_danmu_task = asyncio.create_task(vision_danmu_service.start())
        logger.info(f"视觉弹幕已启用 (间隔={vision_danmu_config.capture_interval}s)")
        return True
    except Exception as e:
        logger.error(f"视觉弹幕启动失败: {e}")
        vision_danmu_service = None
        vision_danmu_config.enabled = False
        return False


async def disable_vision_danmu():
    """禁用视觉弹幕功能"""
    global vision_danmu_config, vision_danmu_service, _vision_danmu_task

    if not vision_danmu_config.enabled and not (vision_danmu_service and vision_danmu_service.is_running):
        logger.info("视觉弹幕未启用")
        return True

    vision_danmu_config.enabled = False

    if _vision_danmu_task and not _vision_danmu_task.done():
        try:
            if vision_danmu_service:
                await vision_danmu_service.stop()
            _vision_danmu_task.cancel()
            await _vision_danmu_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"停止视觉弹幕时出错: {e}")

    vision_danmu_service = None
    _vision_danmu_task = None
    logger.info("视觉弹幕已禁用")
    return True


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

    # 加载云端 TTS 配置
    global cloud_tts_config
    if _cfg.has_section("cloud_tts") and cloud_tts_node_available:
        try:
            from audio.cloud_tts import CloudTtsConfig
            cloud_tts_config = CloudTtsConfig(
                provider=_cfg.get("cloud_tts", "provider", fallback="doubao"),
                enabled=_cfg.getboolean("cloud_tts", "enabled", fallback=False),
                api_url=_cfg.get("cloud_tts", "api_url", fallback=""),
                api_key=_cfg.get("cloud_tts", "api_key", fallback=""),
                api_secret=_cfg.get("cloud_tts", "api_secret", fallback=""),
                api_version=_cfg.get("cloud_tts", "api_version", fallback="v3"),
                appid=_cfg.get("cloud_tts", "appid", fallback=""),
                access_token=_cfg.get("cloud_tts", "access_token", fallback=""),
                resource_id=_cfg.get("cloud_tts", "resource_id", fallback="seed-tts-2.0"),
                voice_type=_cfg.get("cloud_tts", "voice_type", fallback="zh_female_vv_uranus_bigtts"),
                speed=_cfg.getfloat("cloud_tts", "speed", fallback=1.0),
                volume=_cfg.getfloat("cloud_tts", "volume", fallback=1.0),
                sample_rate=_cfg.getint("cloud_tts", "sample_rate", fallback=24000),
                format=_cfg.get("cloud_tts", "format", fallback="pcm"),
                timeout=_cfg.getint("cloud_tts", "timeout", fallback=30),
                retry_count=_cfg.getint("cloud_tts", "retry_count", fallback=3),
                retry_interval=_cfg.getfloat("cloud_tts", "retry_interval", fallback=1.0),
            )
            if set_cloud_tts_config:
                set_cloud_tts_config(cloud_tts_config)
            logger.info(f"云端 TTS 配置已加载: enabled={cloud_tts_config.enabled}, provider={cloud_tts_config.provider}")
        except Exception as e:
            logger.error(f"加载云端 TTS 配置失败: {e}")
            cloud_tts_config = None

    # 命令行参数覆盖 config.ini 设置
    if args.cloud_tts:
        if cloud_tts_config:
            cloud_tts_config.enabled = True
        elif cloud_tts_node_available:
            try:
                from audio.cloud_tts import CloudTtsConfig
                cloud_tts_config = CloudTtsConfig(enabled=True)
                if set_cloud_tts_config:
                    set_cloud_tts_config(cloud_tts_config)
            except Exception:
                pass
        logger.info("云端 TTS 已通过命令行参数启用")

    # 加载 AI 读弹幕 TTS 配置
    global danmu_tts_config, vision_danmu_config
    if _cfg.has_section("danmu_tts"):
        danmu_tts_config.enabled = _cfg.getboolean("danmu_tts", "enabled", fallback=False)
        danmu_tts_config.read_interval = _cfg.getint("danmu_tts", "read_interval", fallback=2)
        danmu_tts_config.max_text_length = _cfg.getint("danmu_tts", "max_text_length", fallback=100)
        danmu_tts_config.clean_emoji = _cfg.getboolean("danmu_tts", "clean_emoji", fallback=True)
        logger.info(f"AI 读弹幕 TTS 配置已加载: enabled={danmu_tts_config.enabled}, interval={danmu_tts_config.read_interval}s")

    # 加载视觉弹幕配置
    if _cfg.has_section("vision_danmu"):
        vision_danmu_config.enabled = _cfg.getboolean("vision_danmu", "enabled", fallback=False)
        vision_danmu_config.capture_interval = _cfg.getint("vision_danmu", "capture_interval", fallback=5)
        vision_danmu_config.target_window = _cfg.get("vision_danmu", "target_window", fallback="")
        vision_danmu_config.persona = _cfg.get("vision_danmu", "persona", fallback=vision_danmu_config.persona)
        vision_danmu_config.max_comment_length = _cfg.getint("vision_danmu", "max_comment_length", fallback=30)
        vision_danmu_config.cooldown = _cfg.getint("vision_danmu", "cooldown", fallback=10)
        logger.info(f"视觉弹幕配置已加载: enabled={vision_danmu_config.enabled}, interval={vision_danmu_config.capture_interval}s")

    # 命令行参数覆盖 config.ini 设置
    if args.danmu_tts:
        danmu_tts_config.enabled = True
        logger.info("AI 读弹幕 TTS 已通过命令行参数启用")
    if args.vision_danmu:
        vision_danmu_config.enabled = True
        logger.info("视觉弹幕已通过命令行参数启用")

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
            enable_live2d=args.live2d and live2d_available if args else False,
            enable_cloud_tts=args.cloud_tts if args and hasattr(args, 'cloud_tts') else False
        )

        # 初始化消息队列并启动 worker（必须在 HTTP 服务器启动前初始化，否则 API 调用时队列为空）
        global _message_queue, _queue_worker_task, danmu_tts_service, vision_danmu_service, _vision_danmu_task
        _message_queue = asyncio.Queue(maxsize=_QUEUE_MAX_SIZE)
        _queue_worker_task = asyncio.create_task(_queue_worker())

        # 初始化 AI 读弹幕 TTS 服务
        if danmu_tts_available and danmu_tts_config.enabled:
            try:
                danmu_tts_service = DanmuTtsService(danmu_tts_config)
                danmu_tts_service.start()
                logger.info("AI 读弹幕 TTS 服务已启动")
            except Exception as e:
                logger.error(f"AI 读弹幕 TTS 服务启动失败: {e}")
                danmu_tts_service = None

        # 初始化视觉弹幕服务
        if vision_danmu_available and vision_danmu_config.enabled:
            try:
                vision_danmu_service = VisionDanmuService(vision_danmu_config, _message_queue)
                _vision_danmu_task = asyncio.create_task(vision_danmu_service.start())
                logger.info("视觉弹幕服务已启动")
            except Exception as e:
                logger.error(f"视觉弹幕服务启动失败: {e}")
                vision_danmu_service = None

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

            # 停止 AI 读弹幕 TTS 服务
            if danmu_tts_service:
                try:
                    danmu_tts_service.stop()
                    logger.info("AI 读弹幕 TTS 服务已停止")
                except Exception as e:
                    logger.warning(f"停止 AI 读弹幕 TTS 服务失败: {e}")

            # 停止视觉弹幕服务
            if _vision_danmu_task and not _vision_danmu_task.done():
                try:
                    if vision_danmu_service:
                        await vision_danmu_service.stop()
                    _vision_danmu_task.cancel()
                    await _vision_danmu_task
                except Exception:
                    pass
                logger.info("视觉弹幕服务已停止")

            # 关闭HTTP服务器
            if 'runner' in locals():
                await runner.cleanup()
                logger.info("HTTP服务器已关闭")

    # 运行异步函数
    asyncio.run(run_async())


if __name__ == "__main__":
    main()