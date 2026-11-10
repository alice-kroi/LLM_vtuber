#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM_v2 WebUI — 桥接 EventBus 到 FastAPI

提供：
  - GET  /api/state        PersonaState 实时状态
  - GET  /api/persona      PersonaProfile 摘要
  - GET  /api/stats        EventBus 统计
  - POST /api/emit         手动发射事件
  - GET  /api/messages     最近消息历史
  - WS   /ws/events        实时事件推送

用法（在 LLM_v2/main.py 集成）：
    from LLM_v2.io.webui import create_webui_app, start_webui
    webui_task = asyncio.create_task(start_webui(runtime, port=8081))
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 添加项目根到 sys.path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STARTED_AT = time.time()

from LLM_v2.core.persona_runtime import PersonaRuntime
from LLM_v2.events import EVENTS

logger = logging.getLogger("v2.webui")

# ---------- 全局缓冲（供 API 查询） ----------

_message_history: deque = deque(maxlen=500)  # 最近 500 条消息
_event_log: deque = deque(maxlen=2000)       # 最近 2000 个事件
_ws_clients: Set[WebSocket] = set()           # WebSocket 客户端（推送）
_input_clients: Set[WebSocket] = set()        # WebSocket 客户端（输入）
_run_log: deque = deque(maxlen=2000)          # Phase 6: 全局运行日志缓冲


class GlobalLogBuffer:
    """H-2 Phase 6: 全局运行日志缓冲（暴露给 WebUI）"""
    def __init__(self):
        self._logs = deque(maxlen=2000)
        self._lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None

    def log(self, level: str, source: str, message: str):
        import time
        entry = {
            "timestamp": time.time(),
            "level": level,
            "source": source,
            "message": message,
        }
        self._logs.append(entry)

    def snapshot(self, limit: int = 100, level: Optional[str] = None) -> list:
        logs = list(self._logs)
        if level:
            logs = [l for l in logs if l["level"] == level]
        return logs[-limit:]


global_log = GlobalLogBuffer()


def log_event(level: str, source: str, message: str):
    """全局日志入口（任何 brain / capability / handler 都可以调用）"""
    global_log.log(level, source, message)


# ---------- Pydantic 模型 ----------

class EmitRequest(BaseModel):
    event_type: str
    payload: Optional[Dict[str, Any]] = None


class InputRequest(BaseModel):
    """H-2: WebUI 用户输入"""
    text: str
    user: Optional[str] = "webui_user"
    uid: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    # 主播态按需抓帧：前端发送消息时附带的当前屏幕/摄像头帧
    images: Optional[List[Dict[str, Any]]] = None


class MemoryDecideRequest(BaseModel):
    """记忆内核：采纳/拒绝/编辑待确认建议"""
    id: str
    action: str          # accept / reject / edit
    text: Optional[str] = ""


class MemoryAddRequest(BaseModel):
    """记忆内核：手动提交长期记忆（进待确认队列）"""
    track: str           # profile / global_fact / project_key / rule
    text: str
    reason: Optional[str] = ""
    scope_key: Optional[str] = ""


# 主播态视觉输入限制：最多 2 帧（屏幕 + 摄像头），单帧约 3MB base64
_MAX_INPUT_IMAGES = 2
_MAX_IMAGE_B64_CHARS = 3_000_000
_ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp"}


def _normalize_images(raw: Any) -> List[Dict[str, Any]]:
    """校验并裁剪前端传来的图片附件，避免把超大 base64 带进事件总线。"""
    if not raw or not isinstance(raw, list):
        return []
    images: List[Dict[str, Any]] = []
    for item in raw[:_MAX_INPUT_IMAGES]:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, str) or not data.startswith("data:image/"):
            continue
        try:
            header, b64 = data.split(",", 1)
        except ValueError:
            continue
        if ";base64" not in header:
            continue
        mime_type = header[5:].split(";")[0].strip().lower()
        if mime_type not in _ALLOWED_IMAGE_MIME or len(b64) > _MAX_IMAGE_B64_CHARS:
            continue
        source = item.get("source") if item.get("source") in ("camera", "screen") else "screen"
        images.append({
            "source": source,
            "data": data,
            "mime_type": mime_type,
        })
    return images


async def _route_user_input(
    runtime,
    source: str,
    text: str,
    metadata: Dict[str, Any],
    images: Optional[List[Dict[str, Any]]] = None,
):
    """H-2: 统一路由用户输入 → emit USER_MESSAGE 事件"""
    from LLM_v2.events import EVENTS
    payload = {
        "source": source,
        "user": metadata.get("user", "webui_user"),
        "uid": metadata.get("uid"),
        "text": text,
        "timestamp": time.time(),
        "metadata": metadata,
    }
    if images:
        payload["images"] = images
    await runtime.bus.emit(EVENTS.USER_MESSAGE, payload)


# ---------- WebSocket 推送器 ----------

class EventPusher:
    """把 EventBus 事件推送到所有 WebSocket 客户端"""

    def __init__(self, runtime: PersonaRuntime):
        self.runtime = runtime
        self._subscribed = False

    async def start(self):
        """订阅所有事件到缓冲 + WS 推送"""
        if self._subscribed:
            return
        # 订阅关键事件类型（每个事件用独立 handler 记录正确 event_type）
        self.runtime.bus.on(EVENTS.BILIBILI_MSG, self._on_bilibili_msg)
        self.runtime.bus.on(EVENTS.GIFT_RECEIVED, self._on_gift)
        self.runtime.bus.on(EVENTS.TTS_SPEAK, self._on_tts)
        self.runtime.bus.on(EVENTS.LIVE2D_EXPRESSION, self._on_live2d)
        self.runtime.bus.on(EVENTS.WILL_ENGINE_DECIDED, self._on_decision)
        self.runtime.bus.on(EVENTS.OPERATOR_DONE, self._on_operator)
        self.runtime.bus.on(EVENTS.STATE_UPDATED, self._on_state)
        self.runtime.bus.on(EVENTS.ERROR, self._on_error)
        # H-2: 通用输入与感知事件
        self.runtime.bus.on(EVENTS.USER_MESSAGE, self._on_user_message)
        self.runtime.bus.on(EVENTS.PERCEPTION_UPDATED, self._on_perception)
        self.runtime.bus.on(EVENTS.DOMAIN_REGISTERED, self._on_domain_registered)
        self.runtime.bus.on(EVENTS.DOMAIN_STARTED, self._on_domain_started)
        self.runtime.bus.on(EVENTS.DOMAIN_STOPPED, self._on_domain_stopped)
        self.runtime.bus.on(EVENTS.DOMAIN_TOOL_CALLED, self._on_domain_tool_called)
        self._subscribed = True
        logger.info("[webui] event pusher started")

    async def _on_bilibili_msg(self, payload):
        await self._record(EVENTS.BILIBILI_MSG, payload)

    async def _on_gift(self, payload):
        await self._record(EVENTS.GIFT_RECEIVED, payload)

    async def _on_user_message(self, payload):
        # 图片只给主播脑消费；事件流/WS/状态缓存里只保留计数，避免 base64 刷屏
        safe = dict(payload) if isinstance(payload, dict) else payload
        if isinstance(safe, dict):
            images = safe.pop("images", None) or []
            if images:
                safe["image_count"] = len(images)
        await self._record(EVENTS.USER_MESSAGE, safe)
        _brain_state_buffer.streamer["last_input"] = safe

    async def _on_perception(self, payload):
        await self._record(EVENTS.PERCEPTION_UPDATED, payload)

    async def _on_domain_registered(self, payload):
        await self._record(EVENTS.DOMAIN_REGISTERED, payload)

    async def _on_domain_started(self, payload):
        await self._record(EVENTS.DOMAIN_STARTED, payload)

    async def _on_domain_stopped(self, payload):
        await self._record(EVENTS.DOMAIN_STOPPED, payload)

    async def _on_domain_tool_called(self, payload):
        await self._record(EVENTS.DOMAIN_TOOL_CALLED, payload)

    async def _on_msg(self, payload):
        """通用事件 handler（保留兼容）"""
        await self._record("bilibili_msg", payload)

    async def _on_tts(self, payload):
        await self._record("tts_speak", payload)
        _brain_state_buffer.streamer["last_response"] = payload

    async def _on_live2d(self, payload):
        await self._record("live2d_expression", payload)

    async def _on_decision(self, payload):
        await self._record("will_engine_decided", payload)
        _brain_state_buffer.will_engine["last_decision"] = payload

    async def _on_operator(self, payload):
        await self._record("operator_done", payload)

    async def _on_state(self, payload):
        await self._record("state_updated", payload)

    async def _on_error(self, payload):
        await self._record("error", payload)

    async def _record(self, event_type: str, payload: Any):
        """记录事件 + 推送 WS"""
        entry = {
            "timestamp": time.time(),
            "event_type": event_type,
            "payload": payload,
        }
        _event_log.append(entry)

        # 推送到所有 WS 客户端
        if _ws_clients:
            dead = set()
            for ws in _ws_clients:
                try:
                    await ws.send_json(entry)
                except Exception:
                    dead.add(ws)
            _ws_clients.difference_update(dead)


# ---------- 脑状态缓冲（H-2 控制台用）----------

class BrainStateBuffer:
    """跟踪 3 脑的实时状态：决策、任务、对话历史

    数据来源：
        - WILL_ENGINE_DECIDED → will_engine.last_decision
        - TASK_STARTED / TASK_DONE → operator.running_task / task_history
        - TTS_SPEAK → streamer.last_response
        - USER_MESSAGE → streamer.last_input
    """

    def __init__(self):
        self.will_engine = {
            "mode": "hybrid",     # 由 WillEngineBrain.__init__ 填充
            "decision_count": 0,
            "last_decision": None,  # 最近一次决策（dict）
            "last_activity_age_sec": 0,
            "is_thinking": False,
        }
        self.streamer = {
            "last_input": None,     # 最近输入
            "last_response": None,  # 最近 TTS 输出
            "is_thinking": False,   # 调 LLM 中
            "conversation": [],     # 最近 N 条对话
        }
        self.operator = {
            "is_busy": False,
            "running_task": None,
            "queue_size": 0,
            "task_history": [],     # 最近 N 条
        }

    def snapshot(self, runtime) -> dict:
        """生成 API 返回快照（实时从 runtime 实例读）"""
        # 直接读 WillEngineBrain 实例
        we = runtime.bus  # 借用别名
        # 从全局 _brains_refs 拿实例（通过 bus 反射不可靠，改为全局注册）
        we_brain = _BRAINS.get("will_engine")
        op_brain = _BRAINS.get("operator")
        st_brain = _BRAINS.get("streamer")

        # WillEngine
        if we_brain:
            self.will_engine["mode"] = getattr(we_brain, "decision_mode", "hybrid")
            self.will_engine["decision_count"] = getattr(we_brain, "_decision_count", 0)
            self.will_engine["autonomous"] = bool(getattr(we_brain, "autonomous", False))
            from datetime import datetime
            last_act = getattr(we_brain, "last_activity_at", None)
            if last_act:
                self.will_engine["last_activity_age_sec"] = int(
                    (datetime.now() - last_act).total_seconds()
                )
            self.will_engine["is_thinking"] = self.will_engine["last_activity_age_sec"] >= 25
            # 任务过程监控（思维脑视角：当前运行中任务 + 最近进度）
            try:
                snapshot = we_brain.task_monitor_snapshot()
                self.will_engine["task_monitor"] = snapshot
            except Exception:
                self.will_engine["task_monitor"] = {"running": {}, "recent": []}

        # Operator
        if op_brain:
            self.operator["is_busy"] = op_brain._running_task is not None
            self.operator["running_task"] = op_brain._running_task
            self.operator["queue_size"] = op_brain._queue.qsize() if hasattr(op_brain._queue, 'qsize') else 0
            self.operator["task_history"] = list(op_brain._task_history[-10:])

        # Streamer
        if st_brain:
            # 读最近响应
            last_tts = getattr(st_brain, "_last_tts", None)
            self.streamer["last_response"] = last_tts
            last_user = getattr(st_brain, "_last_user_msg", None)
            self.streamer["last_input"] = last_user
            self.streamer["conversation"] = list(getattr(st_brain, "_conversation", [])[-20:])

        return {
            "will_engine": self.will_engine,
            "streamer": self.streamer,
            "operator": self.operator,
        }


# 全局脑实例引用（H-2 控制台用）
_BRAINS: Dict[str, Any] = {}
_brain_state_buffer = BrainStateBuffer()


# ---------- 任务总览：数据采集辅助 ----------

# 存储观测点（相对项目根）：只读统计，不写入
_STORAGE_TARGETS = (
    ("人格状态库", "persona/state.db"),
    ("记忆内核", "persona/memory.db"),
    ("网页缓存", "data/web_cache"),
    ("知识库文件", "knowledge_base"),
    ("评测报告", "evals"),
    ("运行日志", "logs"),
    ("规格账本", ".spec-workflow"),
)

# 任务总览过滤阈值（可用环境变量覆盖）
#   TASK_ACTIVE_TTL：非终态任务多久没更新就视为已结束（默认 30 分钟）
#   TASK_DONE_GRACE：已完成/失败任务完成后还显示多久（默认 90 秒，便于看到结果）
TASK_ACTIVE_TTL = float(os.getenv("TASK_OVERVIEW_TTL_SEC") or 1800)
TASK_DONE_GRACE = float(os.getenv("TASK_OVERVIEW_GRACE_SEC") or 90)


def _collect_processes(asm) -> List[Dict[str, Any]]:
    """收集子进程域的运行状态（懒启动子进程的 pid / 存活情况）。"""
    rows: List[Dict[str, Any]] = []
    if asm is None:
        return rows
    worlds = asm.worlds_status().get("worlds", [])
    for w in worlds:
        dom = asm._registry.get(w["id"])
        if dom is None:
            continue
        proc = getattr(dom, "_proc", None)
        pid = None
        if proc is not None and getattr(proc, "pid", None) is not None:
            if hasattr(proc, "returncode"):          # asyncio 子进程 / Popen
                alive = proc.returncode is None
            elif hasattr(proc, "is_alive"):          # multiprocessing.Process
                alive = bool(proc.is_alive())
            else:
                alive = True
            pid = proc.pid if alive else None
        rows.append({
            "domain": w["id"],
            "label": w["label"],
            "started": w["started"],
            "enabled": w["enabled"],
            "pid": pid,
            "degraded": w.get("degraded", ""),
        })
    return rows


def _dir_usage(path: Path, max_files: int = 20000) -> Dict[str, Any]:
    """统计文件大小（有上限，避免大目录拖慢接口）。"""
    if not path.exists():
        return {"bytes": 0, "files": 0, "exists": False}
    if path.is_file():
        try:
            return {"bytes": path.stat().st_size, "files": 1, "exists": True}
        except OSError:
            return {"bytes": 0, "files": 0, "exists": True}
    total = 0
    files = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue
            files += 1
            if files >= max_files:
                return {"bytes": total, "files": files,
                        "exists": True, "truncated": True}
    return {"bytes": total, "files": files, "exists": True}


def _overview_tasks() -> List[Dict[str, Any]]:
    """合并任务来源，并过滤掉「已完成/长期无进展」的记录。

    - 台账是权威源（操作脑每步进度都会写进 central_tasks + 事件表）
    - 思维脑监控表补充尚未落台账的运行中任务
    - 终态（done/failed）只在完成后短暂展示（默认 90s），之后隐藏
    - 非终态超过 TTL（默认 30 分钟）没有更新，视为已结束，隐藏
    """
    now = time.time()
    tasks: Dict[str, Dict[str, Any]] = {}

    store = None
    try:
        from LLM_v2.domains.central_info.task_store import TaskStore
        store = TaskStore()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[overview] 台账不可用: {e}")

    if store is not None:
        try:
            for t in store.list(limit=40, include_terminal=True):
                phase = str(t.get("phase") or "pending")
                updated = float(t.get("updated_at") or t.get("created_at") or 0)
                age = now - updated if updated else 1e9
                if phase in ("done", "failed"):
                    if age > TASK_DONE_GRACE:
                        continue
                elif age > TASK_ACTIVE_TTL:
                    continue
                tasks[t["task_id"]] = {
                    "task_id": t["task_id"],
                    "source": "任务台账",
                    "title": t.get("title") or "",
                    "phase": phase,
                    "percent": int(t.get("percent") or 0),
                    "stage": t.get("stage") or "",
                    "detail": t.get("detail") or "",
                    "owner": t.get("owner") or "",
                    "revision": t.get("revision"),
                    "started_at": t.get("created_at"),
                    "updated_at": t.get("updated_at"),
                    "age_sec": int(age),
                    "plan": t.get("plan") or [],
                }
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[overview] 台账读取失败: {e}")

    # 思维脑监控表：补台账里还没有的运行中任务（并同步最新阶段/说明）
    we = _BRAINS.get("will_engine")
    monitor = {}
    if we is not None:
        try:
            monitor = (we.task_monitor_snapshot() or {}).get("running") or {}
        except Exception:  # noqa: BLE001
            monitor = {}
    for tid, info in monitor.items():
        if tid in tasks:
            row = tasks[tid]
            row["percent"] = max(row["percent"], int(info.get("percent") or 0))
            if info.get("stage"):
                row["stage"] = info["stage"]
            if info.get("detail"):
                row["detail"] = info["detail"]
            row["source"] = "任务台账 + 思维脑"
            continue
        tasks[tid] = {
            "task_id": tid,
            "source": "思维脑",
            "title": info.get("description") or tid,
            "phase": "running",
            "percent": int(info.get("percent") or 0),
            "stage": info.get("stage") or "",
            "detail": info.get("detail") or "",
            "started_at": info.get("started_at"),
            "updated_at": info.get("started_at"),
            "age_sec": int(now - (info.get("started_at") or now)),
        }

    # 操作脑兜底：台账不可用时仍能看到当前任务
    op = _BRAINS.get("operator")
    running = getattr(op, "_running_task", None) if op is not None else None
    if isinstance(running, dict):
        tid = running.get("_ci_task_id") or running.get("ci_task_id")
        if not tid:
            tid = f"op|{running.get('tool')}|{running.get('description')}"
            if tid not in tasks:
                tasks[tid] = {
                    "task_id": tid,
                    "source": "操作脑",
                    "title": running.get("description")
                             or running.get("tool") or "执行中任务",
                    "phase": "running",
                    "percent": 0,
                    "stage": running.get("tool") or "",
                    "detail": str(running.get("args") or "")[:120],
                    "started_at": running.get("started_at"),
                }

    # 事件时间线（点击任务展开时用）
    for tid, row in tasks.items():
        events: List[Dict[str, Any]] = []
        if store is not None:
            try:
                rows = store.events(tid, limit=15)
            except Exception:  # noqa: BLE001
                rows = []
            for ev in reversed(rows):
                events.append({
                    "kind": ev.get("kind") or "",
                    "actor": ev.get("actor") or "",
                    "detail": ev.get("detail") or "",
                    "at": ev.get("at"),
                })
        row["events"] = events

    order = {"running": 0, "blocked": 1, "pending": 2, "done": 3, "failed": 4}
    return sorted(
        tasks.values(),
        key=lambda x: (order.get(x.get("phase"), 9),
                       -(x.get("updated_at") or x.get("started_at") or 0)),
    )


def _overview_storage() -> Dict[str, Any]:
    items = []
    for label, rel in _STORAGE_TARGETS:
        usage = _dir_usage(_PROJECT_ROOT / rel)
        items.append({"label": label, "path": rel, **usage})
    items.sort(key=lambda x: x["bytes"], reverse=True)
    return {"items": items, "total_bytes": sum(i["bytes"] for i in items)}


def _overview_llm() -> List[Dict[str, Any]]:
    """各用途当前生效的模型与端点（降级后能看到真实值）。"""
    rows: List[Dict[str, Any]] = []
    try:
        from LLM_v2.core import llm_adapter
        for purpose, inst in getattr(llm_adapter, "_LLM_INSTANCES", {}).items():
            rows.append({
                "purpose": purpose,
                "model": inst.model,
                "api_url": inst.api_url,
                "fallback": inst.model == inst.fallback_model,
            })
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[overview] LLM 实例读取失败: {e}")
    return rows



# ---------- FastAPI App 工厂 ----------

def create_webui_app(runtime: PersonaRuntime) -> FastAPI:
    """创建 FastAPI app（桥接 runtime）"""
    app = FastAPI(
        title="LLM_vtuber V2 WebUI",
        version="3.0",
        description="桥接 EventBus 到 FastAPI 控制台",
    )
    pusher = EventPusher(runtime)
    # 暴露给外部（start_webui / 测试）复用同一个实例，避免重复订阅导致事件双发
    app.state.pusher = pusher

    @app.on_event("startup")
    async def _startup():
        await pusher.start()

    # ---------- 状态查询 ----------

    @app.get("/api/state")
    async def get_state():
        """PersonaState 实时状态（含独立人格内核的情绪层）"""
        s = runtime.state
        core = getattr(runtime, "self_core", None)
        extra = core.snapshot() if core is not None else {}
        return {
            "persona_name": runtime.persona.name,
            "pad": {
                "P": s.pad.P, "A": s.pad.A, "D": s.pad.D,
                "quadrant": s.pad.dominant_quadrant(),
            },
            "resources": {
                "energy": s.energy,
                "focus": s.focus,
                "social_need": s.social_need,
                "curiosity": s.curiosity,
            },
            "mood_strength": extra.get("mood_strength"),
            "emotions": extra.get("emotions", []),
            "last_appraisal": extra.get("last_appraisal"),
            "last_user": extra.get("last_user"),
            "relationship": extra.get("relationship"),
            "known_contacts": extra.get("known_contacts", 0),
            "preferences": extra.get("preferences", []),
            "memories": extra.get("memories", []),
            "should_force_rest": s.should_force_rest(),
            "should_take_break": s.should_take_break(),
            "wants_social": s.wants_social(),
            "wants_exploration": s.wants_exploration(),
            "current_task_id": s.current_task_id,
            "day_streak": s.day_streak,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }

    @app.get("/api/persona")
    async def get_persona():
        """PersonaProfile 摘要"""
        p = runtime.persona
        return {
            "name": p.name,
            "display_name": p.display_name,
            "nicknames": p.nicknames,
            "personality_text": p.personality.personality_text,
            "interests": [
                {"name": i.name, "enthusiasm": i.enthusiasm, "subtopics": i.subtopics}
                for i in p.interests
            ],
            "speaking_style": p.speaking_style,
            "constraints": p.constraints,
        }

    @app.get("/api/stats")
    async def get_stats():
        """EventBus 统计"""
        return runtime.bus.stats()

    # ---------- 事件操作 ----------

    @app.post("/api/emit")
    async def post_emit(req: EmitRequest):
        """手动发射事件"""
        if not req.event_type:
            raise HTTPException(400, "event_type 必填")
        await runtime.bus.emit(req.event_type, req.payload or {})
        return {"success": True, "event_type": req.event_type}

    @app.get("/api/messages")
    async def get_messages(limit: int = 50):
        """最近消息历史（BILIBILI_MSG / GIFT_RECEIVED）"""
        msgs = [
            e for e in list(_message_history)[-limit:]
        ]
        return {"count": len(msgs), "messages": msgs}

    @app.get("/api/events")
    async def get_events(limit: int = 100, event_type: Optional[str] = None):
        """最近事件日志（先按 event_type 过滤，再取最近 limit 条）"""
        events = list(_event_log)
        if event_type:
            events = [e for e in events if e["event_type"] == event_type]
        events = events[-limit:]
        return {"count": len(events), "events": events}

    # ---------- 状态变更（手动） ----------

    @app.post("/api/state/apply_event")
    async def post_apply_event(req: EmitRequest):
        """手动触发 state 事件"""
        if not req.event_type:
            raise HTTPException(400, "event_type 必填")
        # 抽出非 delta_* 参数给 apply_event 当 kwargs
        payload = req.payload or {}
        kwargs = {k: v for k, v in payload.items()
                  if k in ("delta_P", "delta_A", "delta_D",
                          "delta_energy", "delta_focus", "delta_social", "delta_curiosity")}
        runtime.apply_event(req.event_type, **kwargs)
        return {"success": True, "event_type": req.event_type, "applied_kwargs": kwargs}

    # ---------- WebSocket ----------

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket):
        await websocket.accept()
        _ws_clients.add(websocket)
        logger.info(f"[webui] WS 客户端连接（当前 {len(_ws_clients)}）")
        try:
            while True:
                # 等客户端消息（ping/pong 或其他）
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            _ws_clients.discard(websocket)
            logger.info(f"[webui] WS 客户端断开（当前 {len(_ws_clients)}）")

    # ---------- H-2: WebUI 输入通道（双向 WS + REST POST）----------

    @app.post("/api/input")
    async def post_input(req: InputRequest):
        """WebUI REST 输入：手动发送消息给主播"""
        text = (req.text or "").strip()
        images = _normalize_images(req.images)
        if not text and not images:
            raise HTTPException(400, "text 不能为空")
        metadata = dict(req.metadata or {})
        metadata["user"] = req.user or "webui_user"
        if req.uid:
            metadata["uid"] = req.uid
        await _route_user_input(runtime, "webui", text, metadata, images=images)
        return {
            "success": True,
            "source": "webui",
            "text": text,
            "image_count": len(images),
            "routed_event": "USER_MESSAGE",
        }

    @app.websocket("/ws/input")
    async def ws_input(websocket: WebSocket):
        """H-2: 双向 WebSocket 输入通道（client → server 发输入）"""
        await websocket.accept()
        _input_clients.add(websocket)
        logger.info(f"[webui] WS input 连接（当前 {len(_input_clients)}）")
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except Exception:
                    logger.debug(f"[webui] WS input 非 JSON: {data[:50]}")
                    continue
                if not isinstance(msg, dict):
                    continue
                text = (msg.get("text", "") or "").strip()
                images = _normalize_images(msg.get("images"))
                if not text and not images:
                    continue
                metadata = dict(msg.get("metadata", {}) or {})
                metadata.setdefault("user", msg.get("user", "webui_user"))
                if msg.get("uid"):
                    metadata["uid"] = msg["uid"]
                await _route_user_input(runtime, "webui", text, metadata, images=images)
                # 回执给 client（可选）
                try:
                    await websocket.send_json({
                        "ack": True,
                        "text": text,
                        "image_count": len(images),
                        "routed_event": "USER_MESSAGE",
                    })
                except Exception:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            _input_clients.discard(websocket)
            logger.info(f"[webui] WS input 断开（当前 {len(_input_clients)}）")

    # ---------- 能力域管理（V2 新增）----------

    @app.get("/api/domains")
    async def get_domains():
        """列出所有能力域（启用状态、工具数量、缺失/降级信息）"""
        return runtime.assembly.worlds_status()

    @app.get("/api/domains/{domain_id}")
    async def get_domain_detail(domain_id: str):
        """单个域详情（H-2 Phase 6：含工具列表 + 完整状态 + signal_status）"""
        cap = runtime.assembly._registry.get(domain_id)
        if cap is None:
            raise HTTPException(404, f"未知能力域: {domain_id}")
        try:
            tools = cap.tools()
            names = cap.allowed_names()
        except Exception as e:
            raise HTTPException(500, f"获取工具失败: {e}")

        # H-2 Phase 6: signal_status（域运行时信号）
        signal_status = _get_domain_signal(cap.id, runtime)

        return {
            "id": cap.id,
            "label": cap.label,
            "type": cap.type.value,
            "enabled": cap.id in runtime.assembly._active,
            "started": cap.id in runtime.assembly._started,
            "tool_count": len(names),
            "tools": [
                {"name": s["function"]["name"],
                 "description": s["function"].get("description", ""),
                 "parameters": s["function"].get("parameters", {})}
                for s in tools
            ],
            "prompt": cap.system_prompt_fragment(),
            "preflight_error": cap.preflight(),
            "degraded": runtime.assembly._degraded.get(cap.id, ""),
            "signal_status": signal_status,
            "operation_count": _count_domain_ops(cap.id, runtime),
        }

    @app.get("/api/domains/{domain_id}/operations")
    async def get_domain_operations(domain_id: str, limit: int = 20):
        """H-2 Phase 6: 该域最近的工具调用历史（从 OPERATOR_DONE 事件提取）"""
        ops = []
        for entry in reversed(list(_event_log)):
            if entry.get("event_type") != "operator_done":
                continue
            payload = entry.get("payload", {}) or {}
            task = payload.get("task", {}) or {}
            if task.get("tool") is None and not task.get("description", "").startswith(domain_id):
                continue
            ops.append({
                "timestamp": entry.get("timestamp"),
                "tool": task.get("tool"),
                "description": task.get("description"),
                "success": (payload.get("result") or {}).get("success"),
            })
            if len(ops) >= limit:
                break
        return {"domain_id": domain_id, "count": len(ops), "operations": ops}

    @app.post("/api/domains/rediscover")
    async def post_domains_rediscover():
        """重新发现能力域（重扫描 domains/ 目录并挂载新增域），返回全量域信号快照"""
        try:
            return await runtime.assembly.rediscover()
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/domains/{domain_id}/enable")
    async def post_domain_enable(domain_id: str):
        """启用一个能力域（含前置检查 + 生命周期）"""
        try:
            result = await runtime.assembly.set_domain_enabled(domain_id, True)
            return result
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/domains/{domain_id}/disable")
    async def post_domain_disable(domain_id: str):
        """停用一个能力域（释放资源，工具集从聚合中消失）"""
        try:
            result = await runtime.assembly.set_domain_enabled(domain_id, False)
            return result
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/domains/{domain_id}/restart")
    async def post_domain_restart(domain_id: str):
        """热重启一个能力域（停掉旧实例后重新启动）"""
        try:
            await runtime.assembly.restart_domain(domain_id)
            return {"success": True, "domain": domain_id}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ---------- 脑状态（H-2 控制台用）----------

    @app.get("/api/brains")
    async def get_brains_status():
        """3 个脑的实时状态：决策计数、思考中、运行任务、最近动作"""
        return _brain_state_buffer.snapshot(runtime)

    # ---------- 数据与进程 API ----------

    @app.get("/api/processes")
    async def get_processes():
        """数据与进程：运行中的子进程引擎 / 共享数据 / 域信号状态"""
        asm = getattr(runtime, "assembly", None)
        worlds = asm.worlds_status() if asm else {"worlds": [], "missing": []}
        processes = _collect_processes(asm)

        # 共享数据：EventBus 统计 + 最近消息
        stats = runtime.bus.stats()
        recent_msgs = [
            {"source": m.get("source", "?"), "text": m.get("text", ""),
             "ts": m.get("timestamp", 0)}
            for m in list(_message_history)[-20:]
        ]
        return {
            "processes": processes,
            "bus_stats": {
                "emitted": stats.get("emitted", 0),
                "dispatched": stats.get("dispatched", 0),
                "errors": stats.get("errors", 0),
                "queue_size": stats.get("queue_size", 0),
            },
            "recent_messages": recent_msgs,
            "worlds": worlds,
        }

    @app.get("/api/overview")
    async def get_overview():
        """任务总览：进行中任务 / 子进程 / 最近对话 / 存储占用 / 生效模型"""
        asm = getattr(runtime, "assembly", None)
        stats = runtime.bus.stats()

        streamer = _BRAINS.get("streamer")
        conversation = list(getattr(streamer, "_conversation", []) or [])[-14:]
        operator = _BRAINS.get("operator")
        tasks = await asyncio.get_event_loop().run_in_executor(
            None, _overview_tasks)
        storage = await asyncio.get_event_loop().run_in_executor(
            None, _overview_storage)

        return {
            "runtime": {
                "uptime_sec": int(time.time() - _STARTED_AT),
                "persona": runtime.persona.name,
                "quadrant": runtime.state.pad.dominant_quadrant(),
                "bus": {
                    "emitted": stats.get("emitted", 0),
                    "dispatched": stats.get("dispatched", 0),
                    "errors": stats.get("errors", 0),
                    "queue_size": stats.get("queue_size", 0),
                },
                "operator_queue": (operator._queue.qsize()
                                   if operator is not None
                                   and hasattr(operator._queue, "qsize") else 0),
                "patrol": {
                    "at": getattr(_BRAINS.get("will_engine"),
                                  "_last_patrol_at", 0),
                    "watching": getattr(_BRAINS.get("will_engine"),
                                        "_patrol_watching", 0),
                },
            },
            "tasks": tasks,
            "processes": _collect_processes(asm),
            "conversation": conversation,
            "storage": storage,
            "llm": _overview_llm(),
        }

    # ---------- 记忆 API（记忆 / 人格 / 知识库 / 提示词） ----------

    @app.get("/api/memory")
    async def get_memory():
        """记忆视图数据：人格 / 状态 / RAG / 提示词"""
        p = runtime.persona
        s = runtime.state
        # 各脑提示词
        prompts = {
            "streamer": runtime.build_system_prompt("streamer"),
            "operator": runtime.build_system_prompt("operator"),
            "will_engine": runtime.build_system_prompt("will_engine"),
        }
        # lorebook 知识库（触发器）
        lorebook = [
            {"keywords": getattr(lb, "keywords", []),
             "content": getattr(lb, "content", ""),
             "priority": getattr(lb, "priority", 0)}
            for lb in (getattr(p, "lorebook", None) or [])
        ]
        # 状态事件审计（state.db 最近记录）
        state_events = []
        try:
            state_events = runtime.state_db.recent_events(limit=30)
        except Exception as e:
            state_events = []
        # 学习层（P0 偏好 / P1 情景召回）
        learning = {}
        try:
            store = getattr(runtime.self_core, "learning", None)
            if store:
                learning = {
                    "stats": store.stats(),
                    "preferences": store.all_preferences(),
                    "episodes": store.recall_episodes(
                        runtime.self_core.last_user, limit=20),
                    "procedures": store.list_procedures(),
                }
        except Exception as e:  # noqa: BLE001
            learning = {"error": str(e)}

        return {
            "persona": {
                "name": p.name,
                "display_name": getattr(p, "display_name", p.name),
                "nicknames": p.nicknames,
                "personality_text": getattr(getattr(p, "personality", None), "personality_text", ""),
                "interests": [{"name": i.name, "enthusiasm": i.enthusiasm,
                               "subtopics": i.subtopics} for i in p.interests],
                "speaking_style": getattr(p, "speaking_style", {}),
                "constraints": getattr(p, "constraints", {}),
                "backstory": getattr(p, "backstory", ""),
                "values": getattr(p, "values", []),
            },
            "state": {
                "pad": s.pad.to_dict(),
                "quadrant": s.pad.dominant_quadrant(),
                "energy": s.energy, "focus": s.focus,
                "social_need": s.social_need, "curiosity": s.curiosity,
            },
            "lorebook": lorebook,
            "prompts": prompts,
            "state_events": state_events,
            "learning": learning,
        }

    # ---------- 记忆内核 API（persona/memory.db） ----------

    @app.get("/api/memory/kernel")
    async def get_memory_kernel(summaries: int = 30, items: int = 60):
        """记忆内核：状态 / 待确认建议 / 长期条目 / 最近摘要"""
        try:
            from persona.memory.service import get_memory_service
            svc = get_memory_service()
            status = await svc.status()
            proposals = await svc.proposals(status="pending", limit=50)
            turns = await svc.recent_turns(limit=summaries)
            mem_items = await svc.recent_items(limit=items)
            return {
                "available": bool(status.get("enabled", False)),
                "status": status,
                "proposals": proposals.get("proposals", []),
                "turns": turns.get("turns", []),
                "items": mem_items.get("items", []),
            }
        except Exception as e:  # noqa: BLE001
            return {"available": False, "error": str(e)}

    @app.get("/api/memory/kernel/search")
    async def search_memory_kernel(q: str = "", track: str = "",
                                   limit: int = 20):
        """语义（词面）检索历史摘要与长期记忆"""
        try:
            from persona.memory.service import get_memory_service
            return await get_memory_service().search(q, track=track, limit=limit)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e), "results": []}

    @app.get("/api/memory/kernel/read/{item_id}")
    async def read_memory_kernel(item_id: str):
        """读取一条记忆或轮次摘要的完整内容"""
        try:
            from persona.memory.service import get_memory_service
            return await get_memory_service().read(item_id)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    @app.post("/api/memory/kernel/decide")
    async def post_memory_kernel_decide(req: MemoryDecideRequest):
        """采纳 / 拒绝 / 编辑后采纳一条建议"""
        try:
            from persona.memory.service import get_memory_service
            return await get_memory_service().decide(
                req.id, req.action, text=req.text or None, actor="webui")
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    @app.post("/api/memory/kernel/add")
    async def post_memory_kernel_add(req: MemoryAddRequest):
        """手动提交长期记忆（进待确认队列）"""
        try:
            from persona.memory.service import get_memory_service
            return await get_memory_service().add(
                req.track, req.text, reason=req.reason or "",
                scope_key=req.scope_key or "", user="web_console")
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    # ---------- 学习面板 API（P0/P1/P1b/P2） ----------

    @app.get("/api/learning")
    async def get_learning():
        """学习结果只读视图：统计 / 偏好 / 情景 / 流程 / 反思"""
        store = getattr(runtime.self_core, "learning", None)
        if store is None:
            return {"available": False}
        return {
            "available": True,
            "stats": store.stats(),
            "preferences": store.all_preferences(),
            "episodes": store.recall_episodes(runtime.self_core.last_user, limit=30),
            "procedures": store.list_procedures(),
            "reflections": store.list_reflections(limit=20),
            "notes": store.recent_notes(limit=30),
        }

    @app.post("/api/learning/consolidate")
    async def post_consolidate(dry_run: bool = False):
        """手动触发一次巩固（夜间任务也可由 Self 心跳自动跑）"""
        return runtime.self_core.consolidate(force=True, dry_run=dry_run)

    # ---------- 意志脑面板 API（WillSoul） ----------

    @app.get("/api/will")
    async def get_will():
        """身份 / 决策记忆 / 经验 / 知识 / 当前 8 段上下文"""
        soul = getattr(runtime, "will_soul", None)
        if soul is None:
            return {"available": False}
        return {
            "available": True,
            "signature": soul.state_signature(),
            "identity": soul.identity_block(),
            "state": soul.state_snapshot(),
            "replay": soul.replay_block(),
            "experience": soul.experience_stats(),
            "knowledge": soul.knowledge_block(),
            "bonds": soul.top_bonds(5),
            "routine": soul.routine_entries(),
            "quiet_hours": soul.identity.get("quiet_hours") or [],
            "is_quiet": soul.is_quiet(),
            "autonomous_execute": soul.autonomous_execute(),
            "decisions": soul.recent_decisions(20),
            "context": soul.build_decision_context(),
        }

    @app.post("/api/will/export")
    async def post_will_export():
        """导出 persona/will_soul_report.md"""
        soul = getattr(runtime, "will_soul", None)
        if soul is None:
            return {"success": False, "error": "WillSoul 不可用"}
        try:
            path = soul.export_markdown()
            return {"success": True, "path": path}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    # ---------- 直播互动助手（stream_assist）----------

    @app.get("/api/stream_assist")
    async def get_stream_assist(limit: int = 20):
        """高光标记 / 点歌队列 / 签到（只读视图）"""
        path = Path("persona/stream_assist.json")
        if not path.exists():
            return {"highlights": [], "queue": [], "checkins": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        data["highlights"] = (data.get("highlights") or [])[-limit:][::-1]
        data["queue"] = (data.get("queue") or [])[:limit]
        return data

    # ---------- 编程会话 API（coder 域可视化） ----------

    @app.get("/api/coding")
    async def get_coding(limit: int = 10, session_id: Optional[int] = None):
        """编程会话与执行步骤（读 persona/state.db 的 coding_sessions / coding_steps）"""
        import sqlite3
        db_path = getattr(getattr(runtime, "state_db", None), "db_path", None) or "persona/state.db"
        out = {"sessions": [], "steps": [], "session_id": session_id}
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            sessions = [dict(r) for r in conn.execute(
                "select id, started_at, finished_at, status, rounds, tool_calls, model,"
                " files_changed, substr(goal,1,180) as goal,"
                " substr(final_error,1,240) as final_error"
                " from coding_sessions order by id desc limit ?",
                (max(1, int(limit)),),
            ).fetchall()]
            out["sessions"] = sessions
            sid = session_id or (sessions[0]["id"] if sessions else None)
            if sid:
                out["session_id"] = int(sid)
                out["steps"] = [dict(r) for r in conn.execute(
                    "select round_no, phase, tool, success,"
                    " substr(detail,1,220) as detail, at"
                    " from coding_steps where session_id=? order by id",
                    (int(sid),),
                ).fetchall()]
            conn.close()
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
        return out

    # ---------- 自主能力登记表 API（capability_ops 可视化） ----------

    @app.get("/api/capabilities")
    async def get_capabilities():
        """自生成能力的准入状态、闸门结果、调用统计与熔断阈值"""
        try:
            from LLM_v2.domains.capability_ops import capability_ops_impl as impl
            data = impl.list_capabilities()
            # 去掉过大的字段，避免前端表格被 schema 撑爆
            for rec in data.get("capabilities", []):
                rec.pop("dir", None)
            return data
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[webui] /api/capabilities 失败: {e}")
            return {"capabilities": [], "error": str(e)}

    # ---------- H-2 Phase 6: 全局日志 API ----------

    @app.get("/api/logs")
    async def get_logs(limit: int = 100, level: Optional[str] = None):
        """H-2 Phase 6: 返回最近 N 条运行日志（按 level 过滤）"""
        logs = global_log.snapshot(limit=limit, level=level)
        return {"count": len(logs), "logs": logs}

    @app.get("/api/state/apply_event")
    async def _dummy(): pass  # placeholder

    # ---------- 静态前端（H-2 Web 控制界面）----------

    if _STATIC_DIR.exists():
        # 挂载 /static 目录
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def root_index():
        """服务 Web 控制台首页（index.html）"""
        index_path = _STATIC_DIR / "index.html"
        if index_path.exists():
            # 禁止缓存首页，避免前端更新后仍加载旧 JS/CSS 引用
            return FileResponse(
                str(index_path), headers={"Cache-Control": "no-store"})
        # 降级：返回 JSON 端点列表
        return {
            "name": "LLM_vtuber V2 WebUI",
            "version": "3.0",
            "endpoints": [
                "/api/state",
                "/api/persona",
                "/api/stats",
                "/api/messages",
                "/api/events",
                "/api/emit (POST)",
                "/api/state/apply_event (POST)",
                # H-2: 输入通道
                "/api/input (POST)",
                "/ws/input (双向)",
                # 能力域管理
                "/api/domains",
                "/api/domains/rediscover (POST)",
                "/api/domains/{id}/enable (POST)",
                "/api/domains/{id}/disable (POST)",
                "/api/domains/{id}/restart (POST)",
                "/ws/events",
            ],
        }

    # ---------- 根路径已上移到 root_index（H-2: 优先返回 HTML）----------

    return app


# ---------- 启动器 ----------

async def start_webui(runtime: PersonaRuntime, host: str = "127.0.0.1", port: int = 8081):
    """启动 WebUI（独立协程）"""
    try:
        import uvicorn
    except ImportError:
        logger.error("[webui] uvicorn 未安装，跳过")
        return

    app = create_webui_app(runtime)
    # 复用 create_webui_app 内的 pusher 实例（防止两个实例重复订阅 → 事件双发）
    pusher: EventPusher = app.state.pusher
    await pusher.start()
    logger.info("[webui] event pusher pre-started")

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info(f"[webui] 启动 http://{host}:{port}")
    await server.serve()


__all__ = ['create_webui_app', 'start_webui', 'EventPusher',
           'BrainStateBuffer', 'global_log', 'log_event',
           '_get_domain_signal', '_count_domain_ops']


def _get_domain_signal(domain_id: str, runtime) -> dict:
    """H-2 Phase 6: 返回域的运行时信号状态

    用于 WebUI 卡片显示"已订阅/已启动/已连接"等实时信号
    """
    cap = runtime.assembly._registry.get(domain_id)
    if cap is None:
        return {"status": "unknown"}
    started = cap.id in runtime.assembly._started
    active = cap.id in runtime.assembly._active
    # 简单 signal：started/active/preflight_error
    return {
        "status": "started" if started else ("active" if active else "inactive"),
        "started": started,
        "active": active,
        "preflight_ok": cap.preflight() is None,
        "prefix": getattr(cap, "prefix", ""),
    }


def _count_domain_ops(domain_id: str, runtime) -> int:
    """H-2 Phase 6: 从事件日志中数该域的调用次数（最近 2000 条内）"""
    count = 0
    cap = runtime.assembly._registry.get(domain_id)
    prefix = getattr(cap, "prefix", "") if cap else ""
    for entry in _event_log:
        if entry.get("event_type") != "operator_done":
            continue
        task = (entry.get("payload") or {}).get("task", {}) or {}
        desc = task.get("description", "")
        if desc.startswith(domain_id) or task.get("tool", "").startswith(prefix):
            count += 1
    return count
