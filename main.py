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

# 条件导入自主学习模块（奖励机制 + 反馈注入）
try:
    from learning import (
        get_reward_tracker,
        init_reward_tracker,
        get_feedback_injector,
        get_knowledge_curator,
        get_emotion_pacer,
        get_memory_reflection,
    )
    learning_available = True
except ImportError as e:
    learning_available = False
    logger.warning(f"自主学习模块导入失败: {e}")

# 惰性导入真正的知识库（knowledge_base）管理器，用于 RAG 知识检索
_kb_manager = None  # 惰性单例


def _get_kb_manager():
    """惰性获取知识库管理器单例（Milvus 不可用返回 None）"""
    global _kb_manager
    if _kb_manager is not None:
        return _kb_manager
    try:
        from knowledge_base import KnowledgeBaseManager
        _kb_manager = KnowledgeBaseManager()
        logger.info("[RAG] 知识库管理器已初始化（knowledge_base）")
    except Exception as e:
        logger.warning(f"[RAG] 知识库管理器初始化失败（降级为无知识库）: {e}")
        _kb_manager = False  # 标记失败不再重试
    return _kb_manager if _kb_manager is not False else None

# 全局变量
args = None
http_server = None
langgraph_manager = None
live2d_manager = None
live2d_action_generator = None
_bili_process = None  # 弹幕监听子进程引用，用于退出时清理
_sovits_process = None  # GPT-SoVITS v2pro TTS 服务子进程引用，用于退出时清理
danmu_tts_config = DanmuTtsConfig()
vision_danmu_config = VisionDanmuConfig()
cloud_tts_config = None  # 云端 TTS 配置，延迟加载

# AI 读弹幕 TTS 服务实例
danmu_tts_service = None
# 视觉弹幕服务实例
vision_danmu_service = None
_vision_danmu_task = None

# 自主学习模块实例（延迟初始化）
reward_tracker = None
feedback_injector = None
knowledge_curator = None
emotion_pacer = None
memory_reflection = None
_reward_finalize_task = None  # 定时 finalize reward 的后台任务
_curator_periodic_task = None  # 知识策展 + 记忆反思 定时任务

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

# 任务式交互架构开关：默认开启；设 TASK_MANAGER_ENABLED=0 可回退到原同步 operator_worker 路径
TASK_MANAGER_ENABLED = os.environ.get("TASK_MANAGER_ENABLED", "1") != "0"

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
    parser.add_argument("--cli", action="store_true",
                        help="启用 CLI 渠道：从 stdin 输入与主播脑对话（阶段1）")
    return parser.parse_args()

# 异步HTTP服务器处理函数
async def process_openai_messages(messages):
    """统一处理 OpenAI 格式消息列表：去重 → 限流 → 奖励 → 入队 → WS/TTS。

    阶段1 抽取：handle_post_request 与 streamer inbox（渠道统一）共用此入口；
    LangGraph 旧链路（_message_queue → _queue_worker → handle_messages）并行保留。
    """
    from fastapi.responses import JSONResponse

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

    # ============ P0-1 弹幕降噪限流：重要事件(礼物/SC)优先放行，普通弹幕窗口限流 ============
    try:
        from broadcast.danmu_filter import danmu_filter
        kept = []
        for msg in messages:
            deliver, _reason = danmu_filter.should_deliver(msg)
            if not deliver:
                continue  # 普通弹幕超限 → 归档式丢弃，不喂给 LLM
            if danmu_filter.is_important(msg):
                msg = danmu_filter.mark_important(msg)
            kept.append(msg)
        messages = kept
        if not messages:
            return JSONResponse({"status": "rate_limited"})
    except Exception as e:
        logger.debug(f"[P0-1] 弹幕过滤跳过: {e}")

    # ============ 奖励采集：识别弹幕情感/礼物/SC 并打分 ============
    if learning_available and reward_tracker is not None:
        try:
            asyncio.create_task(reward_tracker.on_messages(messages))
        except Exception as e:
            logger.debug(f"奖励采集跳过: {e}")

    while _message_queue and _message_queue.qsize() >= _QUEUE_MAX_SIZE:
        try:
            _message_queue.get_nowait()
            _message_queue.task_done()
            logger.warning(f"消息队列已满({_QUEUE_MAX_SIZE})，丢弃旧消息")
        except asyncio.QueueEmpty:
            break

    if _message_queue is not None:
        await _message_queue.put(_queue_pack(messages))
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


async def handle_post_request(request):
    """
    处理POST请求：解析外部注入（bili HTTP 回流 / API）为消息列表，
    统一交给 process_openai_messages 处理（去重/限流/奖励/入队）。

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

        return await process_openai_messages(messages)
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


# ============================================================
# P0-2 队列投递水位 + 停滞自检 + 重启补投（借鉴 Cortico lastDeliveredCursor）
# ============================================================
_QUEUE_META_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_QUEUE_META_FILE = os.path.join(_QUEUE_META_DIR, "queue_meta.json")
_queue_seq = 0                 # 已入队单调序号（投递水位用）
_queue_delivered = 0           # 投递水位：已成功处理的最大 seq
_queue_item_meta = {}          # seq -> {"ts": float, "messages": list}（保留近期未交付项）
_QUEUE_STALL_S = float(os.environ.get("QUEUE_STALL_S", "300"))   # 停滞阈值：待处理最老消息超过此秒数 → 告警（只报不自动修）
_QUEUE_AUDIT_INTERVAL_S = float(os.environ.get("QUEUE_AUDIT_INTERVAL_S", "120"))
_QUEUE_STALL_WARNED_SEQ = None
_QUEUE_META_RETAIN = 400


def _queue_pack(messages):
    """为入队消息分配 seq 并记录元数据（供补投/水位）。返回 (seq, ts, messages)。"""
    global _queue_seq
    _queue_seq += 1
    _queue_item_meta[_queue_seq] = {"ts": time.time(), "messages": messages}
    # 防止元数据无限膨胀，只保留最近项
    if len(_queue_item_meta) > _QUEUE_META_RETAIN:
        for k in sorted(_queue_item_meta)[:_QUEUE_META_RETAIN // 2]:
            _queue_item_meta.pop(k, None)
    return (_queue_seq, _queue_item_meta[_queue_seq]["ts"], messages)


def _queue_persist_meta():
    """持久化投递水位 + 未交付项（补投基线）。仅保留 seq>交付的项。"""
    try:
        undelivered = [{"seq": s, "ts": m["ts"], "messages": m["messages"]}
                       for s, m in _queue_item_meta.items() if s > _queue_delivered]
        os.makedirs(_QUEUE_META_DIR, exist_ok=True)
        with open(_QUEUE_META_FILE, "w", encoding="utf-8") as f:
            json.dump({"delivered": _queue_delivered, "undelivered": undelivered[-200::]},
                      f, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"[P0-2] 水位持久化失败: {e}")


def _queue_load_meta():
    try:
        with open(_QUEUE_META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _queue_restore_undelivered():
    """启动时补投水位之后仍未处理的消息（借鉴 Cortico requeueUndelivered）。"""
    global _queue_delivered
    global _queue_seq
    meta = _queue_load_meta()
    if not meta:
        return
    _queue_delivered = int(meta.get("delivered", 0))
    to_readd = [u for u in meta.get("undelivered", []) if int(u["seq"]) > _queue_delivered]
    if not to_readd:
        return
    for u in sorted(to_readd, key=lambda x: int(x["seq"])):
        seq = int(u["seq"])
        _queue_seq = max(_queue_seq, seq)
        _queue_item_meta[seq] = {"ts": u.get("ts", time.time()), "messages": u["messages"]}
    logger.info(f"[P0-2] 重启补投: 水位={_queue_delivered}，补投 {len(to_readd)} 条未处理消息")
    for u in sorted(to_readd, key=lambda x: int(x["seq"])):
        try:
            _message_queue.put_nowait((int(u["seq"]), u.get("ts", time.time()), u["messages"]))
        except asyncio.QueueFull:
            logger.warning("[P0-2] 补投队列已满，丢弃最旧")
            break


async def _audit_queue_stall():
    """定期自检队列停滞：最老待处理消息超过阈值 → 仅告警不自动修复（诚实认知论）。"""
    global _QUEUE_STALL_WARNED_SEQ
    while True:
        await asyncio.sleep(_QUEUE_AUDIT_INTERVAL_S)
        try:
            pending = {s: m for s, m in _queue_item_meta.items() if s > _queue_delivered}
            pending_items = sorted(pending.items())  # (seq, {ts, messages}) 按 seq 有序
            if not pending_items:
                _QUEUE_STALL_WARNED_SEQ = None
                continue
            oldest_seq, oldest_meta = pending_items[0]
            oldest_age = time.time() - oldest_meta["ts"]
            behind = len(pending_items)
            if oldest_age > _QUEUE_STALL_S and _QUEUE_STALL_WARNED_SEQ != oldest_seq:
                _QUEUE_STALL_WARNED_SEQ = oldest_seq
                sample = ""
                try:
                    msg0 = oldest_meta["messages"][-1]
                    if isinstance(msg0, dict):
                        sample = str(msg0.get("content", ""))[:80]
                except Exception:
                    pass
                logger.error(
                    f"[P0-2] 投递水位停滞: 最老未处理消息已等待 {oldest_age:.0f}s"
                    f"(>{_QUEUE_STALL_S:.0f}s)，积压 {behind} 条。水位={_queue_delivered}，"
                    f"oldest={sample}"
                )
        except Exception as e:
            logger.debug(f"[P0-2] 停滞自检异常: {e}")


# ============================================================
# 任务式交互架构：TaskManager 进度 / 完成回调
# ============================================================
def _on_task_progress(task):
    """任务进展回调（TaskManager 后台操作脑每轮工具后触发）。仅记录日志；
    实时广播 task_update 已由 task_manager._ws_broadcast 完成。"""
    logger.info(f"[TaskManager·进展] {task.task_id} {task.progress:.0%} - {task.message}")


async def _on_task_done(task):
    """任务完成回调：把操作结果通过消息队列回灌给主播，由主播组织播报（不触碰 Live2D/TTS 锁）。"""
    try:
        # 若主播已在当轮顺路播报（result_reported=True），则不重复回灌
        if getattr(task, "result_reported", False):
            logger.info(f"[TaskManager] 任务 {task.task_id} 结果已由主播顺路播报，跳过回灌")
            return
        summary = (task.result or {}).get("summary") or (task.result or {}).get("result") or task.message or ""
        done = task.status.value == "done"
        broadcast_msg = {
            "role": "user",
            "name": "系统",
            "source": "operator_broadcast",
            "content": (
                f"【系统播报】刚才的电脑操作任务已完成{bool(done)}。"
                f"结果：{summary} 请用你的主播语气向观众简要播报这个结果。"
            ),
            "timestamp": time.time() + 2.0,  # 错开去重时间窗
        }
        global _message_queue
        if _message_queue is not None:
            await _message_queue.put(_queue_pack([broadcast_msg]))
            logger.info(f"[TaskManager·完成] 任务 {task.task_id} 完成，回灌主播播报: {summary[:40]}")
        else:
            logger.warning("[TaskManager·完成] 消息队列未就绪，跳过结果播报回灌")
    except Exception as e:
        logger.error(f"[TaskManager·完成] 播报回灌异常: {e}")


async def _queue_worker():
    """
    队列工作协程：串行处理消息，避免 Live2D/TTS 并发冲突。

    每条消息依次走完完整流程（init→load_memory→rag→llm→rag_save→save_memory→live2d→tts→finalize），
    确保operation_lock不会因并发争抢而卡死，TTS音频也不会重叠播放。
    """
    logger.info("消息队列 worker 已启动")
    _last_persist = 0.0
    while True:
        try:
            item = await _message_queue.get()
            # 兼容两种负载：_queue_pack 的 (seq, ts, messages)，或 webui 直接 put 的裸 list
            if isinstance(item, tuple) and len(item) == 3:
                seq, ts, messages = item
            else:
                seq, messages = None, item
                ts = time.time()
            logger.info(f"从队列取出消息处理（剩余队列长度: {_message_queue.qsize()}）")
            await handle_messages(messages)
            # 处理成功 → 推进投递水位并持久化（节流）
            if seq is not None:
                global _queue_delivered
                if seq > _queue_delivered:
                    _queue_delivered = seq
                now = time.time()
                if now - _last_persist > 2.0:
                    _last_persist = now
                    _queue_persist_meta()
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
    Live2D 动作联动节点 (A-P0 扩展版: 表情 + 热键)

    从大模型响应中解析动作信息，执行：
    1. 目光方向
    2. 嘴巴状态
    3. A-P0: 表情（优先 LLM 指定 expression，否则从 tone 自动推断）
    4. A-P0: 热键触发（LLM 显式指定 hotkey）
    """
    global live2d_manager

    try:
        response = state.get("response", "")
        if structured_output_available:
            parsed = parse_structured_response(response, enable_live2d=True)
        else:
            parsed = parse_response_format(response, enable_live2d=True)

        tone = parsed.get("tone", "普通")
        visual_focus = parsed["visual_focus"]
        mouth_state = parsed["mouth_state"]
        expression = parsed.get("expression")          # A-P0 新字段
        expression_intensity = parsed.get("expression_intensity")
        hotkey = parsed.get("hotkey")                   # A-P0 新字段

        if not live2d_manager or not live2d_manager.is_connected:
            logger.warning("Live2D控制器未连接，跳过动作")
            state["live2d_status"] = "disconnected"
            state["live2d_message"] = "Live2D控制器未连接"
            return state

        # A-P0: 表情自动推断（当 LLM 没显式输出 expression 时，从 tone 推断）
        if not expression:
            from LLM.live2d_models import TONE_TO_EXPRESSION
            inferred = TONE_TO_EXPRESSION.get(tone)
            if inferred:
                logger.info(f"[表情] LLM 未指定 expression，从 tone='{tone}' 推断 → {inferred}")
                expression = inferred
            elif inferred is None and tone in TONE_TO_EXPRESSION:
                # 明确在表里但映射到 None，不输出任何表情
                pass
            else:
                # tone 不在映射表，尝试模糊匹配（关键词包含）
                tone_lower = tone.lower()
                _FUZZY = [
                    ("笑|开心|兴|喜|嗨|甜", "smile"),
                    ("难过|悲伤|哭|伤心|泪", "sad"),
                    ("生气|愤怒|怒|讨厌|烦", "angry"),
                    ("惊讶|吃惊|慌|惊讶", "surprised"),
                    ("害羞|羞涩|尴尬|脸红|难为情", "shy"),
                    ("疑问|好奇|疑惑|困惑", None),
                    ("平和|温柔|普通|礼貌|淡定", None),
                ]
                for pat, exp in _FUZZY:
                    import re as _re
                    if _re.search(pat, tone):
                        if exp:
                            logger.info(f"[表情] tone='{tone}' 未在表中，模糊匹配 → {exp}")
                            expression = exp
                        break

        logger.info(f"Live2D节点: tone={tone}, 目光={visual_focus}, 嘴巴={mouth_state}, "
                    f"expression={expression}, hotkey={hotkey}")

        # 执行目光方向动作
        try:
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

        # A-P0: 执行表情动作
        if expression and live2d_manager:
            try:
                intensity = expression_intensity or 0.8
                ok = await live2d_manager.set_expression(
                    expression=expression,
                    intensity=intensity,
                    duration=args.live2d_speed if args is not None else 2.5,
                )
                if ok:
                    logger.info(f"✓ 执行表情: {expression} (intensity={intensity})")
                else:
                    logger.info(f"- 表情 {expression} 未应用（参数缺失或模型不支持）")
            except Exception as e:
                logger.error(f"执行表情 {expression} 失败: {e}")

        # A-P0: 执行热键触发
        if hotkey and live2d_manager:
            try:
                ok = await live2d_manager.trigger_hotkey(hotkey)
                if ok:
                    logger.info(f"✓ 执行热键: {hotkey}")
                else:
                    logger.info(f"- 热键 {hotkey} 未触发（模型中可能不存在）")
            except Exception as e:
                logger.error(f"执行热键 {hotkey} 失败: {e}")

        state["live2d_status"] = "success"
        state["live2d_message"] = (
            f"目光:{visual_focus}, 嘴巴:{mouth_state}, "
            f"表情:{expression or '-'}, 热键:{hotkey or '-'}"
        )
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
# 电脑操作（Computer Use）共用指引：教 LLM 像真人一样先思考再操作的策略
COMPUTER_USE_TOOL_LIST = (
    "os_open_path / os_list_directory / os_list_windows / os_activate_window / "
    "os_minimize_window / os_show_desktop / os_click_xy / os_type_text / os_press_key / "
    "os_scroll / ui_list_elements / vision_click"
)
COMPUTER_USE_GUIDANCE = f"""## 电脑操作（Computer Use）：操作电脑的思考策略
可用工具：**{COMPUTER_USE_TOOL_LIST}**

请像真人操作电脑一样**先思考、再动手**，遵循以下决策原则：

1. **打开优先用系统级**：打开"我的电脑/盘符/文件夹/应用"时，优先用 `os_open_path`（调用系统 Shell，最稳），不要用模拟键鼠 win+r。
2. **先盘点桌面空间**：开始桌面操作前，先 `os_list_windows` 看有哪些窗口、是否遮挡。
3. **发现遮挡立即清理**：若发现目标被窗口遮挡、或视觉定位 `vision_click` 找不到目标/点击失败，优先最小化遮挡窗口：
   - 遮挡窗口少 → `os_minimize_window("某窗口标题关键词")`
   - 遮挡很多或要点桌面图标 → `os_show_desktop()` 一键显示桌面（Win+D）
   - 最小化后再重试定位/点击。
4. **读内容用文件系统**：用户问"某盘/文件夹里有什么" → 直接 `os_list_directory` 读文件系统返回准确列表，不要靠截图猜。
5. **精确坐标靠 UI Automation**：要点击标准控件（按钮/菜单/输入框）时，优先 `ui_list_elements` 拿真实坐标，再 `os_click_xy`；非标准界面才用 `vision_click`。
6. **每步截图自校验**：关键操作后截图确认结果，失败就修正重试（如最小化遮挡、重新定位），不要盲目继续。
"""


SYSTEM_PROMPT_LIVE2D = """你是虚拟主播「爱莉希雅」，活泼可爱的二次元少女。

## 输出规则（必须遵守）
1. 回复简洁，不超过80字
2. 禁止Markdown符号、列表、表情
3. 禁止输出思考过程
4. 使用口语化表达，结尾用「~」「呢」「呀」
5. 自然融入直播互动话术（如「感谢关注~」「欢迎新来的朋友~」）

## 搜索工具（纯搜索/抓取）
- **web_search**: 搜索网页
- **fetch_webpage**: 抓取URL内容

## Edge 交互浏览器（真正打开可见 Edge 窗口、点击、输入）
- **browser_snapshot**: 获取 Edge 当前页面的可交互元素列表（带 @eN 引用）
- **browser_navigate**: 导航 Edge 到指定 URL
- **browser_click**: 点击 Edge 页面上的元素（用 @eN 引用）
- **browser_type**: 在 Edge 输入框中输入文本（用 @eN 引用）
- **browser_press_key**: 按键盘（Enter/Tab/Esc/ArrowUp/Down 等）
- **browser_scroll**: 滚动页面
- **browser_tabs**: 管理标签页（list/new/switch/close）

### 浏览器使用原则
- 用户说「帮我去XX看看」「帮我打开XX」「帮我在浏览器里XX」 → 用 Edge 交互浏览器
- 用户说「帮我搜一下XX」「XX是什么」 → 优先用 web_search
- Edge 浏览器已在屏幕上可见，操作后会自动更新页面
- 推荐流程: browser_navigate → browser_snapshot → browser_click → browser_type → browser_press_key(Enter) → 回到 snapshot

""" + COMPUTER_USE_GUIDANCE + """
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

## 搜索工具（纯搜索/抓取）
- **web_search**: 搜索网页
- **fetch_webpage**: 抓取URL内容

## Edge 交互浏览器（真正打开可见 Edge 窗口、点击、输入）
- **browser_snapshot**: 获取 Edge 当前页面的可交互元素列表（带 @eN 引用）
- **browser_navigate**: 导航 Edge 到指定 URL
- **browser_click**: 点击 Edge 页面上的元素（用 @eN 引用）
- **browser_type**: 在 Edge 输入框中输入文本（用 @eN 引用）
- **browser_press_key**: 按键盘（Enter/Tab/Esc/ArrowUp/Down 等）
- **browser_scroll**: 滚动页面
- **browser_tabs**: 管理标签页（list/new/switch/close）

### 浏览器使用原则
- 用户说「帮我去XX看看」「帮我打开XX」「帮我在浏览器里XX」 → 用 Edge 交互浏览器
- 用户说「帮我搜一下XX」「XX是什么」 → 优先用 web_search
- Edge 浏览器已在屏幕上可见，操作后会自动更新页面

""" + COMPUTER_USE_GUIDANCE + "仅在以下情况使用搜索：实时信息、不确定、需事实支撑"

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
        
        # 意图分类器嵌入模型将在首次使用时懒加载
        # 如需预加载，可在 build_graph 后调用 _preload_intent_model_async()
        self._intent_preload_done = False

    def _preload_intent_model_async(self):
        """在后台线程中预加载意图分类器模型"""
        if self._intent_preload_done:
            return
        self._intent_preload_done = True
        import threading
        def _load():
            try:
                from LLM_node import preload_embed_model
                preload_embed_model()
                logger.info("[意图分类] 嵌入模型预加载完成（后台线程）")
            except Exception as e:
                logger.warning(f"意图分类器预加载失败: {e}")
        threading.Thread(target=_load, daemon=True).start()

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
        
        # 后台预加载意图分类器嵌入模型
        self._preload_intent_model_async()

        # 创建状态图，使用LLMState作为状态类型
        self.graph = StateGraph(LLMState)

        # 添加节点
        self.graph.add_node("init", self._init_node)
        self.graph.add_node("load_memory", self._load_memory_node)
        self.graph.add_node("rag_retrieval", self._rag_retrieval_node)
        self.graph.add_node("context_control", self._context_control_node)
        self.graph.add_node("streamer_request", self._streamer_request_node)  # 主播首轮：同步情感+操作请求给主体
        self.graph.add_node("supervisor", self._supervisor_node)              # 主体思维：信息交换中枢
        self.graph.add_node("operator_worker", self._operator_worker_node)    # 操作思维
        self.graph.add_node("streamer_worker", self._llm_process_node)        # 主播末轮：最终回复+广播+后处理
        self.graph.add_node("finalize", self._finalize_node)

        # 注意: rag_save/save_memory/live2d/tts 不再作为图节点，
        # 改为在 _trigger_parallel_post_process 中通过 asyncio 并行执行
        # - _rag_save_node:       RAG保存（Milvus）
        # - _save_memory_node:    记忆保存（InMemoryStore）
        # - live2d_action_node:   Live2D动作（目光/嘴巴）
        # - tts_node/cloud_tts_node: TTS合成与播放

        # 配置三脑架构的共享状态更新规则（修正信息流模型）
        # 添加边：init → load_memory → rag_retrieval → [context_control] → streamer_request
        #        streamer_request → supervisor
        #        supervisor →(有操作请求)→ operator_worker → streamer_worker
        #        supervisor →(无操作请求)→ streamer_worker
        #        streamer_worker → finalize（后处理并行执行）
        #
        # 信息流（符合主播/主体/操作三脑模型）：
        #   主播(streamer_request) 产出情感+操作请求 → 主体(supervisor) 接收并路由
        #   → 操作(operator_worker) 领取指令执行并回报 → 主播(streamer_worker) 组织最终话术
        self.graph.add_edge(START, "init")
        self.graph.add_edge("init", "load_memory")
        self.graph.add_edge("load_memory", "rag_retrieval")

        if enable_context_control:
            self.graph.add_edge("rag_retrieval", "context_control")
            self.graph.add_edge("context_control", "streamer_request")
        else:
            self.graph.add_edge("rag_retrieval", "streamer_request")
        self.graph.add_edge("streamer_request", "supervisor")

        # 条件路由：主体根据主播是否有操作请求决定下一节点
        def _route_after_supervisor(state: LLMState) -> str:
            # 任务式交互架构：操作已后台异步执行，主播恒走 streamer_worker 立即回复"处理中"。
            # 回退开关 TASK_MANAGER_ENABLED=0 时，恢复原同步 operator_worker 路径。
            if os.environ.get("TASK_MANAGER_ENABLED", "1") != "0":
                return "streamer_worker"
            return "operator_worker" if state.get("routed_to") == "operator" else "streamer_worker"

        self.graph.add_conditional_edges(
            "supervisor", _route_after_supervisor,
            {"operator_worker": "operator_worker", "streamer_worker": "streamer_worker"},
        )
        # 操作完成后必须回到主播脑组织最终话术
        self.graph.add_edge("operator_worker", "streamer_worker")

        # 优化2.1A: streamer_worker → finalize，后处理通过 asyncio.create_task 并行执行
        self.graph.add_edge("streamer_worker", "finalize")

        # 编译图
        self.graph = self.graph.compile(checkpointer=self.checkpointer)

        logger.info("LangGraph 图结构构建完成（三脑: streamer_request→supervisor→operator/streamer→finalize）")

        # ============ 初始化自主学习模块 ============
        if learning_available:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._init_learning_modules_async())
            except RuntimeError:
                # build_graph 在非 async 上下文中被调用时（比如命令行直接 import）
                # 此时还没有 running loop，同步初始化即可
                self._init_learning_modules_sync()

    async def _init_learning_modules_async(self):
        """异步初始化（有 running loop 时走这条路径）"""
        global reward_tracker, _curator_periodic_task
        try:
            await init_reward_tracker(self.memory_manager)
            reward_tracker = get_reward_tracker()
            logger.info("[自主学习] RewardTracker 已初始化（异步）")
        except Exception as e:
            logger.error(f"[自主学习] RewardTracker 异步初始化失败: {e}")
            return

        self._init_learning_modules_remaining()

        # 启动后台周期任务（知识策展 + 记忆反思），自主学习真正在后台跑起来
        if _curator_periodic_task is None or _curator_periodic_task.done():
            _curator_periodic_task = asyncio.create_task(self._learning_periodic_loop())
            logger.info("[自主学习] 后台自主学习周期任务已启动")

    async def _learning_periodic_loop(self):
        """
        后台自主学习周期循环：知识策展 + 记忆反思

        流程（每轮一个周期）：
        1. try 记忆反思提炼（LLM）—— 可能耗时
        2. sleep 一段可配置时长
        3. 重试
        """
        global knowledge_curator, memory_reflection
        import time
        last_curate_ts = 0.0
        REFLECT_INTERVAL = 600      # 反思最短间隔 10 分钟（内部还有 30 分钟节流）
        CURATE_INTERVAL = 3600      # 知识策展最短间隔 1 小时
        while True:
            try:
                # 1. 记忆反思（异步提炼洞察）
                if memory_reflection is not None:
                    await memory_reflection.run_periodic()

                # 2. 知识策展（提炼高奖励模式）
                now = time.time()
                if knowledge_curator is not None and (now - last_curate_ts) >= CURATE_INTERVAL:
                    try:
                        await knowledge_curator.curate_high_reward_patterns(force=False)
                        last_curate_ts = time.time()
                    except Exception as e:
                        logger.error(f"[后台] 知识策展异常: {e}")

            except asyncio.CancelledError:
                logger.info("[后台] 自主学习周期任务被取消")
                break
            except Exception as e:
                logger.error(f"[后台] 自主学习周期任务异常: {e}")

            await asyncio.sleep(REFLECT_INTERVAL)

    def _init_learning_modules_sync(self):
        """同步 fallback（没有 running loop 时）"""
        global reward_tracker
        try:
            reward_tracker = get_reward_tracker()
            reward_tracker._memory_manager = self.memory_manager
            reward_tracker._initialized = True
            logger.info("[自主学习] RewardTracker 已初始化（同步 fallback）")
        except Exception as e:
            logger.error(f"[自主学习] RewardTracker 同步初始化失败: {e}")
            return

        self._init_learning_modules_remaining()

    def _init_learning_modules_remaining(self):
        """初始化 feedback_injector + knowledge_curator + emotion_pacer + memory_reflection"""
        global feedback_injector, knowledge_curator, emotion_pacer, memory_reflection

        try:
            feedback_injector = get_feedback_injector(
                min_score=5,
                max_examples=3,
                since_hours=24.0,
                enabled=True,
            )
            logger.info("[自主学习] FeedbackInjector 已初始化 (min_score=5, max_examples=3)")
        except Exception as e:
            logger.warning(f"[自主学习] FeedbackInjector 初始化失败: {e}")

        try:
            knowledge_curator = get_knowledge_curator(
                memory_manager=self.memory_manager,
                min_curate_score=10,
                curate_interval_minutes=60.0,
            )
            logger.info("[自主学习] KnowledgeCurator 已初始化")
        except Exception as e:
            logger.warning(f"[自主学习] KnowledgeCurator 初始化失败: {e}")

        try:
            emotion_pacer = get_emotion_pacer()
            logger.info("[自主学习] EmotionPacer 已初始化（情绪节奏器）")
        except Exception as e:
            logger.warning(f"[自主学习] EmotionPacer 初始化失败: {e}")

        try:
            memory_reflection = get_memory_reflection(
                memory_manager=self.memory_manager,
                max_insights_per_run=3,
                interval_minutes=30.0,
                enabled=True,
            )
            logger.info("[自主学习] MemoryReflection 已初始化（异步记忆反思）")
        except Exception as e:
            logger.warning(f"[自主学习] MemoryReflection 初始化失败: {e}")

        logger.info(
            f"[自主学习] 模块就绪: RewardTracker={reward_tracker is not None}, "
            f"FeedbackInjector={feedback_injector is not None}, "
            f"KnowledgeCurator={knowledge_curator is not None}, "
            f"EmotionPacer={emotion_pacer is not None}, "
            f"MemoryReflection={memory_reflection is not None}"
        )

    def _rag_retrieval_node(self, state: LLMState) -> LLMState:
        """RAG 检索节点：从 Milvus 检索相关上下文

        优化方向二:
        - P0-2: 检索短路（短消息 + skip 意图）
        - P1-2: force_search 短路 + 查询简化 + 动态 top_k
        """
        logger.info("执行 RAG 检索节点")

        try:
            user_message = _extract_last_user_message(state)
            if not user_message:
                logger.warning("未找到用户消息，跳过 RAG 检索")
                return state

            query_text = user_message.get("content", "").strip()

            # ---- P0-2 + P1-2: RAG 检索短路 ----
            intent_action = "normal"
            try:
                from LLM.LLM_node import IntentClassifier
                intent = IntentClassifier.classify(query_text)
                intent_action = intent["action"]
            except Exception:
                pass

            # 1. 极短消息（弹幕刷词）直接跳过
            if len(query_text) <= 3:
                logger.info(f"[RAG短路] 消息过短({len(query_text)}字符)，跳过: '{query_text}'")
                return state

            # 2. skip 意图（问候/道别/感谢）跳过 RAG
            if intent_action == "skip":
                logger.info(f"[RAG短路] 意图={intent_action}，跳过 RAG: '{query_text}'")
                return state

            # 3. P1-2: force_search 意图（实时查询）跳过 RAG
            #    历史对话对实时信息没有帮助，避免无效 Milvus 调用
            if intent_action == "force_search":
                logger.info(f"[RAG短路] 意图={intent_action}(实时查询)，跳过 RAG: '{query_text}'")
                return state

            # ---- P1-2: 查询简化 ----
            simplified = self._simplify_query(query_text)
            if simplified and simplified != query_text:
                logger.info(f"[RAG优化] 查询简化: '{query_text[:30]}' -> '{simplified[:30]}'")
                query_text = simplified

            # ---- P1-2: 动态 top_k ----
            top_k = self._calc_dynamic_top_k(query_text)
            logger.info(f"[RAG优化] 动态 top_k={top_k} (intent={intent_action}, len={len(query_text)})")

            rag_state = RAGState(
                query_text=query_text,
                collection_name="chat_history",
                db_name="LLM_vtuber",
                query_params={"top_k": top_k, "metric_type": "COSINE", "nprobe": 10},
                messages=state.get("messages", [])
            )

            rag_result = rag_retrieval_node(rag_state)

            state["context"] = rag_result.get("context")
            state["retrieved_documents"] = rag_result.get("retrieved_documents")
            logger.info(f"RAG 检索完成，找到 {rag_result.get('num_documents', 0)} 个文档")

            # ============ 真正的知识库 RAG：从 knowledge_base 检索领域知识 ============
            # 说明：上方 rag_retrieval_node 检索的是 chat_history（历史对话）；
            # 这里补充从独立知识库检索虚拟主播等领域知识，作为真正的 RAG 上下文注入
            knowledge_ctx = self._search_knowledge_base(query_text, top_k=3)
            if knowledge_ctx:
                state["knowledge_context"] = knowledge_ctx
                # 知识优先注入（拼在历史上下文之前，让 LLM 优先消费领域知识）
                existing_ctx = state.get("context") or ""
                state["context"] = (
                    f"{knowledge_ctx}\n\n{existing_ctx}" if existing_ctx else knowledge_ctx
                )
                logger.info(f"[RAG] 已从 knowledge_base 注入知识上下文（{len(knowledge_ctx)}字符）")

            return state

        except Exception as e:
            logger.error(f"RAG 检索节点失败: {e}")
            return state

    def _search_knowledge_base(self, query: str, top_k: int = 3) -> str:
        """
        从 knowledge_base 知识库做语义检索，返回格式化知识文本。

        Args:
            query: 查询文本
            top_k: 返回知识条数

        Returns:
            格式化知识字符串；无命中或不可用时返回空串
        """
        kb = _get_kb_manager()
        if kb is None or not query:
            return ""
        try:
            hits = kb.search(query, top_k=top_k)
            if not hits:
                return ""
            parts = []
            for i, hit in enumerate(hits, 1):
                score = hit.get("score", 0)
                content = (hit.get("content") or "").strip()
                if not content:
                    continue
                # 低于阈值的跳过（score 是 COSINE 相似度，越高越好）
                if score < 0.30:
                    continue
                parts.append(f"{i}. {content}")
            return "\n".join(parts)
        except Exception as e:
            logger.warning(f"[RAG] 知识库检索失败: {e}")
            return ""

    def _make_first_token_push(self):
        """
        P0：首字流式 —— 生成一个同步回调，把 LLM 首个内容片段尽早推送到 WebSocket。

        返回的同步函数会在 LLM 流式生成首个内容时被调用；
        内部用 create_task 异步广播，绝不阻塞生成。
        """
        async def _push(text):
            try:
                from webui.app import ws_manager
                await ws_manager.broadcast_ai_stream_start(text)
                logger.debug(f"[流式] 已推送首字: {text[:20]}")
            except Exception as e:
                logger.debug(f"[流式] 首字推送失败: {e}")

        def _cb(text):
            # P0-A：LLM 调用已改到线程池(to_thread)，此回调可能在子线程触发，
            # 用 call_soon_threadsafe 调度回主事件循环创建 task（保证线程安全）。
            try:
                _loop = asyncio.get_event_loop()
                _loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(_push(text))
                )
            except Exception:
                pass
        return _cb

    @staticmethod
    def _simplify_query(text: str) -> str:
        """P1-2: RAG 查询简化 - 去掉噪音提高 embedding 准确度"""
        import re
        # 去掉 emoji
        text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF]', '', text)
        # 压缩超重复字符 (哈哈哈 -> 哈哈)
        text = re.sub(r'(.)\1{3,}', r'\1\1', text)
        # 清理首尾
        text = text.strip('，。！？、,.!? \t')
        return text

    @staticmethod
    def _calc_dynamic_top_k(query: str) -> int:
        """P1-2: 根据查询复杂度动态决定 RAG 返回数量"""
        qlen = len(query)
        if qlen <= 10:
            return 2
        if qlen <= 30:
            return 3
        return 5

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

    # ============ 三脑架构节点（主体/操作/主播） ============

    async def _streamer_request_node(self, state: LLMState) -> LLMState:
        """
        主播思维（Streamer）首轮：感知用户意图，把「情感 + 需要的电脑操作」同步给主体。
        - 判断用户问题是否需要操作电脑 → 写 streamer_operation_request（供主体路由）
        - 提取基础情感标记 → 写 streamer_emotion（供主体/后续 Live2D）
        真正的主播话术由 streamer_worker（_llm_process_node）在主体调度后生成。
        """
        question = state.get("question") or ""
        if not question:
            um = _extract_last_user_message(state)
            question = (um.get("content", "") if um else "")

        # 1. 判断是否需要电脑操作（规则：含操作意图关键词）
        from LLM.LLM_node import _is_operator_intent
        # 【系统播报】为"操作完成播报"回灌消息：主播只需组织播报，绝不二次触发操作任务（防重复执行）
        is_broadcast = question.startswith("【系统播报】")
        need_op = False if is_broadcast else _is_operator_intent(question)
        state["streamer_operation_request"] = question if need_op else ""
        # 对操作请求提升轮次上限
        if need_op:
            current = state.get("max_rounds") or 4
            state["max_rounds"] = max(current, 10)

        # 2. 基础情感标记（默认普通；后续由 _llm_process_node 输出的 tone 覆盖）
        state["streamer_emotion"] = state.get("streamer_emotion") or "普通"

        logger.info(
            f"[主播思维·首轮] 感知='{question[:30]}' → 操作请求={'有' if need_op else '无'}, "
            f"情感={state.get('streamer_emotion')}"
        )
        return state

    async def _supervisor_node(self, state: LLMState) -> LLMState:
        """主体思维（Supervisor）节点：路由主播/操作，中转指令。薄封装 LLM_node.supervisor_route_node。"""
        from LLM.LLM_node import supervisor_route_node
        return await supervisor_route_node(state)

    async def _operator_worker_node(self, state: LLMState) -> LLMState:
        """操作思维（Operator）节点：执行真实电脑操作。薄封装 LLM_node.operator_worker_node。"""
        from LLM.LLM_node import operator_worker_node
        return await operator_worker_node(state)

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

            # ============ 反馈注入：追加历史高奖励案例作为 few-shot ============
            if learning_available and feedback_injector is not None:
                try:
                    session_id = state.get("session_id", state.get("thread_id", "default"))
                    system_prompt = await feedback_injector.inject_into_system_prompt(
                        system_prompt,
                        memory_manager=self.memory_manager,
                        session_id=session_id,
                    )
                except Exception as e:
                    logger.debug(f"反馈注入跳过: {e}")

            # ============ 情绪节奏：根据近期语气分布调整提示 ============
            if learning_available and emotion_pacer is not None:
                try:
                    # 取最近回复聚合的奖励分作为活跃度信号
                    recent_reward = 0.0
                    if reward_tracker is not None:
                        recent_high = reward_tracker.get_recent_high_reward(1)
                        recent_low = reward_tracker.get_recent_low_reward(1)
                        if recent_high:
                            recent_reward = recent_high[0].get("total_score", 0)
                        elif recent_low:
                            recent_reward = recent_low[0].get("total_score", 0)
                    advice = emotion_pacer.get_advice(recent_reward)
                    if advice.get("kind") != "neutral":
                        system_prompt = emotion_pacer.nudge_system_prompt(system_prompt, advice)
                        logger.info(
                            f"[情绪节奏] {advice['kind']}: {advice['reason']}"
                        )
                except Exception as e:
                    logger.debug(f"情绪节奏跳过: {e}")

            # ============ 三脑：主播读取主体转交的中枢信息（操作结果/桌面情况）============
            # 这是主播(streamer_worker)唯一从主体(supervisor)/操作(operator)取信息的接口。
            # 无论 context_controlled 是否启用，都显式注入 system_prompt，保证操作结果/桌面
            # 情况始终能到达主播（不依赖 RAG context 拼接，避免被裁剪丢弃）。
            core_info_parts = []
            # 1) 操作结果（主体经 operator_worker 写入的 core_message / operator_result）
            if state.get("operator_result"):
                _op = state["operator_result"]
                _sum = state.get("operator_summary") or _op.get("summary") or ""
                _res = _op.get("result") or ""
                core_info_parts.append(
                    f"[主体转达-操作结果] 已完成{state.get('operator_completed', False)}。"
                    f"简述:{_sum} 详情:{_res}"
                )
                # 2) 桌面情况（主体维护的 desktop_snapshot）——仅在操作流程中随结果一起转达
                _ds = state.get("desktop_snapshot") or {}
                if _ds.get("windows"):
                    _wins = "、".join(_ds["windows"][:10])
                    core_info_parts.append(f"[主体转达-桌面情况] 当前可见窗口:{_wins}"
                                           f"（共{_ds.get('count', len(_ds['windows']))}个）")
            elif state.get("core_message"):
                core_info_parts.append(f"[主体转达] {state.get('core_message')}")
            if core_info_parts:
                core_info_block = "\n".join(core_info_parts)
                system_prompt = (
                    f"{system_prompt}\n\n===== 主体中枢转达的信息（供你组织回复参考）=====\n"
                    f"{core_info_block}\n"
                    f"=============================="
                )
                logger.info(f"[主播·中枢信息] 已注入主体转达 {len(core_info_parts)} 段: "
                            f"{core_info_parts[0][:50]}")

            # 任务式架构(F2)：任务已后台派发 → 主播本轮只应输出自然的"受理/进度"话术，
            # 强约束不要调用/提到任何工具名，避免模型把待办操作输出成工具名（如"os_list_windows"）。
            if TASK_MANAGER_ENABLED and state.get("task_id"):
                system_prompt = (
                    f"{system_prompt}\n\n"
                    f"[后台任务受理提示] 用户请求的电脑操作已作为后台任务正在执行，操作结果完成后会另行通报。"
                    f"你现在只需用自然、口语化的中文向观众反馈『好的，我在处理/正在查看，稍等哦』之类的受理话术。"
                    f"绝对不要调用任何工具，也绝对不要输出工具名或JSON代码。"
                )

            # 如果上下文控制已启用，RAG 上下文已整合到 messages 中，不再需要单独拼接
            context_controlled = state.get("context_controlled", False)
            llm_context = None if context_controlled else state.get("context")

            # 构建LLM状态
            # 关键修复(E1)：若操作脑已完成操作(operation_status=="done")，主播末轮关闭抗幻觉守卫，避免二次执行 os。
            # 任务式架构修复(F1)：任务已后台派发(task_id 非空)时，主播本轮只负责"受理/进度"话术，
            # 绝不调用电脑操作工具（否则与后台操作脑同时操作桌面 → 重复/冲突）。
            disable_streamer_tools = (
                bool(state.get("operation_status") == "done")
                or (TASK_MANAGER_ENABLED and bool(state.get("task_id")))
            )
            llm_state = LLMState(
                messages=state.get("messages", []),
                system_prompt=system_prompt,
                model=DEFAULT_MODEL,
                temperature=DEFAULT_TEMPERATURE,
                max_tokens=DEFAULT_MAX_TOKENS,
                question=user_message.get("content", ""),
                context=llm_context,
                name=user_message.get("name"),
                # P0：首字流式回调 —— LLM 首个内容到达即推送到 WebSocket（首字优先）
                on_stream_first=self._make_first_token_push(),
                skip_operation_guard=disable_streamer_tools,
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
            # 三脑：把主播最终情感回写到共享状态（供主体/后续 Live2D/情绪节奏读取）
            state["streamer_emotion"] = parsed["tone"]

            # ============ 情绪节奏：记录本次使用的语气 ============
            if learning_available and emotion_pacer is not None:
                try:
                    emotion_pacer.record_tone(parsed["tone"])
                except Exception as e:
                    logger.debug(f"情绪记录跳过: {e}")

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

            # ============ 奖励追踪：开始追踪这条 AI 回复 ============
            if learning_available and reward_tracker is not None:
                try:
                    reward_tracker.bind_to_response(parsed["content"])
                except Exception as e:
                    logger.debug(f"奖励绑定跳过: {e}")

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
        # S3：若 LLM 已主动调用 tts_synthesize，跳过自动 TTS 兜底，避免重复发声
        llm_called_tts = state_snapshot.get("tts_called", False)
        if llm_called_tts:
            logger.info("[TTS] LLM 已主动调用 tts_synthesize，跳过自动 TTS")
        elif self.enable_tts:
            tasks.append(self._async_tts_synthesize(state_snapshot, "local"))
        elif self.enable_cloud_tts:
            tasks.append(self._async_tts_synthesize(state_snapshot, "cloud"))
        
        # 5. 奖励 finalize（聚合这条 AI 回复收到的弹幕奖励）
        if learning_available and reward_tracker is not None:
            tasks.append(self._async_finalize_reward())
        
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

    async def _async_finalize_reward(self):
        """异步奖励 finalize：聚合 AI 回复窗口内的弹幕奖励，并触发 S11 反向学习"""
        try:
            await asyncio.sleep(0)
            global reward_tracker, knowledge_curator
            if reward_tracker is not None:
                # 等几秒钟让弹幕陆续到达再 finalize
                await asyncio.sleep(15)
                await reward_tracker.finalize_current_response()
                # S11：低奖励回复记录为"避免模式"，供正向注入反例
                if learning_available and knowledge_curator is not None:
                    try:
                        recent_low = reward_tracker.get_recent_low_reward(1)
                        if recent_low:
                            low = recent_low[0]
                            text = low.get("response_text", "")
                            score = low.get("total_score", 0)
                            if text and score < 0:
                                await knowledge_curator.record_avoid_pattern(
                                    text=text,
                                    score=score,
                                    reason=f"低奖励 {score}分，弹幕反馈不佳",
                                )
                                logger.info(
                                    f"[S11] 已记录避免模式（{score}分）: {text[:40]}..."
                                )
                    except Exception as e:
                        logger.debug(f"[S11] 反向学习跳过: {e}")
        except Exception as e:
            logger.error(f"[并行] 奖励 finalize 异常: {e}")

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

        # 三脑架构：每轮初始清零操作/路由残留字段（防持久会话跨轮次污染 P-A），
        # 并重置 max_rounds（防上一轮提升后污染本轮 P-D）。
        for _field in ("routed_to", "operator_result", "operator_summary",
                       "desktop_snapshot", "core_message", "operator_instruction",
                       "task_id", "task_info"):
            state.pop(_field, None)
        state["operator_completed"] = False
        state["operation_status"] = "idle"
        state["max_rounds"] = 4
        # 主播首轮会重新写入真实情感/操作请求
        state.setdefault("streamer_emotion", "普通")
        state.setdefault("streamer_operation_request", "")

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

    # 使用 uvicorn 启动服务器（P0-C：默认绑定 127.0.0.1，防局域网远程注入；可配置 [webui] host 覆盖）
    _listen_host = "127.0.0.1"
    try:
        _wcfg = configparser.ConfigParser(interpolation=None)
        _wcfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding='utf-8')
        _listen_host = _wcfg.get("webui", "host", fallback="127.0.0.1").strip() or "127.0.0.1"
    except Exception:
        pass
    config = uvicorn.Config(app, host=_listen_host, port=port, log_level="info")
    server = uvicorn.Server(config)

    # 在后台任务中启动（不阻塞主事件循环）
    server_task = asyncio.create_task(server.serve())

    logger.info(f"HTTP服务器已启动，监听端口 {port}")
    logger.info(f"Web UI 控制台: http://localhost:{port}/")
    logger.info(f"API 文档: http://localhost:{port}/docs")

    return server

def _start_bili_legacy(room_id=None):
    """旧路径：Popen 启动 broadcast/bili_main.py 子进程（bili 域激活失败时回退）。"""
    import subprocess
    import sys as _sys

    broadcast_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast")
    script_path = os.path.join(broadcast_dir, "bili_main.py")
    cmd = [_sys.executable, script_path]

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


async def _start_bili_domain(room_id=None):
    """阶段0：激活 bili 能力域并启动监听（bili_start 工具）；失败回退旧路径。"""
    try:
        from LLM.skill_world import skill_assembly
        from tool.tool_node import tool_registry, ToolCall
        await skill_assembly.start_world("bili")
        args_ = {"room_ids": [room_id]} if room_id is not None else {}
        r = await tool_registry.execute_tool(
            ToolCall(tool_call_id="bili_start_auto", name="bili_start",
                     arguments=args_), timeout=60)
        # execute_tool → {"result": {"result": {引擎回执}, "id": N}}，success 在最内层
        inner = (r or {}).get("result", {}) if isinstance(r, dict) else {}
        engine = inner.get("result", {}) if isinstance(inner, dict) else {}
        ok = bool(engine.get("success")) if isinstance(engine, dict) else False
        logger.info(f"bili 域已激活，监听启动: success={ok} {str(r)[:120]}")
        if not ok:
            logger.warning(f"bili_start 未成功，回退旧 bili_main.py: {r}")
            _start_bili_legacy(room_id)
    except Exception as e:
        logger.warning(f"bili 域激活失败，回退旧 bili_main.py: {e}")
        _start_bili_legacy(room_id)


def start_bilibili_listener(room_id=None):
    """
    启动哔哩哔哩直播监听。

    阶段0（扩展化）：优先激活 bili 能力域（LLM/bili，blivedm 监听 + HTTP 回流），
    失败则回退旧 broadcast/bili_main.py 子进程。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(_start_bili_domain(room_id))
        return
    _start_bili_legacy(room_id)


def start_sovits_server(host="127.0.0.1", port=9880, sovits_dir=""):
    """
    托管拉起 GPT-SoVITS v2pro TTS 服务（api_v2.py 子进程）。

    与弹幕监听器同款模式：独立子进程常驻 9880，退出时由 main 统一清理。
    若 9880 已被占用（服务已在跑），则不重复拉起。
    """
    import subprocess
    import sys as _sys

    global _sovits_process
    if _sovits_process and _sovits_process.poll() is None:
        logger.info("GPT-SoVITS v2pro 服务已在运行，跳过拉起")
        return True

    # 定位 v2pro 项目目录：程序代码保存在本项目内（junction 将权重大文件
    # 链到 D 盘，不占项目磁盘），因此优先项目内目录；项目内不存在时回退
    # 用户本机原目录，兼容旧部署
    base = os.path.dirname(os.path.abspath(__file__))
    sovits_dir = sovits_dir or os.environ.get("SOVITS_DIR", "")
    if not sovits_dir:
        _local_dir = os.path.join(base, "GPT-SoVITS-v2pro")
        _orig_dir = r"D:\Github\GPT-SoVITS-v2pro-20250604"
        sovits_dir = _local_dir if os.path.isfile(
            os.path.join(_local_dir, "api_v2.py")) else _orig_dir
    api_script = os.path.join(sovits_dir, "api_v2.py")
    runtime_py = os.path.join(sovits_dir, "runtime", "python.exe")
    if not os.path.isfile(api_script):
        logger.error(f"[sovits] 找不到 api_v2.py: {api_script}")
        return False

    # 若端口已被占用（外部已起服务），直接视为成功
    try:
        import socket
        with socket.create_connection((host, port), timeout=0.5):
            logger.info(f"[sovits] 端口 {port} 已有服务，复用")
            return True
    except Exception:
        pass

    # 选择解释器：优先 runtime/python.exe，否则当前 sys.executable
    python = runtime_py if os.path.isfile(runtime_py) else _sys.executable
    config_path = os.path.join("GPT_SoVITS", "configs", "tts_infer.yaml")
    cmd = [python, api_script, "-a", host, "-p", str(port), "-c", config_path]

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([sovits_dir,
                                         os.path.join(sovits_dir, "GPT_SoVITS")])

    logger.info(f"[sovits] 托管拉起 TTS 服务: {' '.join(cmd)}  cwd={sovits_dir}")
    try:
        _sovits_process = subprocess.Popen(
            cmd,
            cwd=sovits_dir,
            env=env,
            stdout=None,
            stderr=None,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        logger.info(f"[sovits] GPT-SoVITS v2pro 服务已拉起 (PID={_sovits_process.pid})")
        return True
    except Exception as e:
        logger.error(f"[sovits] 拉起失败: {e}")
        _sovits_process = None
        return False


def stop_sovits_server():
    """停止托管拉起的 v2pro TTS 子进程。"""
    global _sovits_process
    if _sovits_process is not None:
        try:
            if _sovits_process.poll() is None:
                _sovits_process.terminate()
                try:
                    _sovits_process.wait(timeout=3)
                except Exception:
                    _sovits_process.kill()
                    _sovits_process.wait(timeout=1)
            logger.info("GPT-SoVITS v2pro 服务已停止")
        except Exception as e:
            logger.error(f"[sovits] 停止失败: {e}")
        finally:
            _sovits_process = None


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

    # 托管拉起 GPT-SoVITS v2pro 服务：当本地 TTS 启用且引擎为 sovits_v2pro 时
    if args.tts:
        try:
            _engine = _cfg.get("tts", "engine", fallback="auto").strip().lower()
        except Exception:
            _engine = "auto"
        if _engine in ("auto", "sovits_v2pro"):
            # 托管拉起：程序代码在项目内（junction 链大权重到 D 盘），项目内优先
            sovits_dir = os.environ.get("SOVITS_DIR", "")
            if not sovits_dir:
                _local_dir = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "GPT-SoVITS-v2pro")
                _orig_dir = r"D:\Github\GPT-SoVITS-v2pro-20250604"
                sovits_dir = _local_dir if os.path.isfile(
                    os.path.join(_local_dir, "api_v2.py")) else _orig_dir
            if os.path.isfile(os.path.join(sovits_dir, "api_v2.py")):
                start_sovits_server(host=_cfg.get("tts", "host", fallback="127.0.0.1"),
                                    port=_cfg.getint("tts", "port", fallback=9880),
                                    sovits_dir=sovits_dir)
            else:
                logger.info("[sovits] 未检测到 GPT-SoVITS-v2pro，跳过托管拉起")

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
        edge_agent_enabled = False
        try:
            import configparser
            config = configparser.ConfigParser(interpolation=None)
            config_ini_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
            config.read(config_ini_path, encoding='utf-8')

            if config.has_section("browser") and config.getboolean("browser", "enabled", fallback=False):
                # EdgeAgent (交互式 Edge，带 snapshot/@ref) —— 按需启动
                # 惰性启动：只缓存启动参数，首次调用浏览器工具时才真正拉起 Edge，
                # 避免每次运行主程序都自动弹出 Edge 窗口。
                if config.has_option("browser", "edge_interactive") and config.getboolean("browser", "edge_interactive"):
                    from tool.edge_agent import EdgeAgent
                    _edge_agent = EdgeAgent.get_instance()
                    _ea_mode = config.get("browser", "mode", fallback="headed").strip()
                    _ea_cdp = config.get("browser", "cdp_url", fallback="").strip() or None
                    _ea_channel = config.get("browser", "channel", fallback="msedge").strip()
                    _ea_maximized = config.getboolean("browser", "start_maximized", fallback=True)
                    _edge_agent.configure(mode=_ea_mode, cdp_url=_ea_cdp,
                                          channel=_ea_channel, start_maximized=_ea_maximized)
                    edge_agent_enabled = True
                    logger.info(f"✅ Edge 交互浏览器已配置（惰性启动，首次调用工具时再打开，mode={_ea_mode}）")
                else:
                    # 传统 headless 浏览器（web_search/fetch_webpage 用）
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

        # 阶段1：初始化主播脑统一收件箱（渠道标准化 → 旧 LangGraph 链路）
        try:
            from LLM.streamer.inbox import init_inbox
            init_inbox(process_openai_messages)
            logger.info("[Streamer] 统一收件箱已初始化（legacy=旧LangGraph链路）")
        except Exception as e:
            logger.warning(f"[Streamer] 收件箱初始化失败（不影响旧链路）: {e}")

        # 阶段2：绑定门面层编排器（STREAMER_NEW=1 时 streamer_ingress 用它进 LangGraph）
        try:
            from LLM.streamer.streamer_brain import get_streamer_front
            get_streamer_front().bind_orchestrator(handle_messages)
            logger.info("[Streamer] 门面层编排器已绑定（handle_messages）")
        except Exception as e:
            logger.warning(f"[Streamer] 门面层编排器绑定失败: {e}")

        # 阶段1：CLI 渠道（--cli 时从 stdin 读用户输入投收件箱）
        if args and getattr(args, "cli", False):
            try:
                from LLM.streamer.cli_channel import start_cli_channel
                asyncio.create_task(start_cli_channel())
                logger.info("[Streamer] CLI 渠道已启动（stdin 输入可对话）")
            except Exception as e:
                logger.warning(f"[Streamer] CLI 渠道启动失败: {e}")

        # P0-2：启动时补投重启前未处理消息 + 启动投递水位停滞自检
        try:
            _queue_restore_undelivered()
            asyncio.create_task(_audit_queue_stall())
        except Exception as e:
            logger.warning(f"[P0-2] 队列补投/自检启动失败: {e}")

        # 任务式交互架构：绑定 TaskManager 进度/完成回调（操作后台执行，完成后回灌主播播报）
        if TASK_MANAGER_ENABLED:
            try:
                from LLM.task_manager import task_manager
                task_manager.bind_callbacks(on_progress=_on_task_progress, on_done=_on_task_done)
                logger.info("[TaskManager] 已绑定进度/完成回调")
            except Exception as e:
                logger.warning(f"[TaskManager] 回调绑定失败: {e}")

        # P2-1：启动所有激活能力域的生命周期钩子（如重资源域的进程/连接）
        try:
            from LLM.skill_world import skill_assembly
            await skill_assembly.start_all()
            logger.info("[SkillWorld] 能力域生命周期钩子已全部启动")
        except Exception as e:
            logger.warning(f"[SkillWorld] 能力域生命周期钩子启动失败: {e}")

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

            # 终止 GPT-SoVITS v2pro TTS 服务子进程
            stop_sovits_server()

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

            if edge_agent_enabled:
                try:
                    from tool.edge_agent import EdgeAgent
                    _ea = EdgeAgent.get_instance()
                    await _ea.stop()
                except Exception as e:
                    logger.warning(f"关闭 EdgeAgent 失败: {e}")

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