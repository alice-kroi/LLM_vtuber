#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI 应用核心

基于 FastAPI + uvicorn 的 Web UI 控制台，提供：
- REST API 用于状态查询、配置管理、消息收发、视觉分析、功能控制
- WebSocket 用于实时推送弹幕、AI 回复、状态变更、日志
- 静态文件服务（前端页面）
"""

import asyncio
import json
import logging
import os
import sys
import time
from collections import deque
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request
from fastapi.responses import Response, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _get_app_main():
    """获取主模块的实际引用，解决 __main__ 与 import main 的命名空间差异"""
    main_mod = sys.modules.get("__main__")
    if main_mod and hasattr(main_mod, "live2d_manager"):
        return main_mod
    import main as app_main
    return app_main

# --------- 共享状态（与原 api.py 保持一致） ---------

_message_history: deque = deque(maxlen=500)
_recent_history_keys: set = set()
_RECENT_HISTORY_MAX = 2000
_config = None
_config_path = None
_start_time = time.time()
_messages_processed = 0

_log_buffer: deque = deque(maxlen=2000)
_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class MemoryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "timestamp": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            _log_buffer.append(entry)
            if record.levelno >= logging.INFO:
                try:
                    asyncio.create_task(_ws_manager.broadcast_log(entry))
                except Exception:
                    pass
        except Exception:
            pass


_memory_log_handler = MemoryLogHandler()
_memory_log_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(_memory_log_handler)


def init_api(config=None, config_path=None):
    global _config, _config_path
    _config = config
    _config_path = config_path
    logger.info("API 模块已初始化")


def _build_history_key(msg_type: str, data: Dict[str, Any]) -> str:
    if msg_type == "danmaku":
        uname = data.get("username", data.get("name", ""))
        content = data.get("content", "")
        return f"D:{uname}|{content}"
    if msg_type == "ai_response":
        content = data.get("content", "")
        tone = data.get("tone", "")
        return f"A:{tone}|{content[:100]}"
    return f"X:{msg_type}|{int(time.time())}"


def add_message_to_history(msg_type: str, data: Dict[str, Any]):
    global _messages_processed, _recent_history_keys
    key = _build_history_key(msg_type, data)
    if key in _recent_history_keys:
        return
    _recent_history_keys.add(key)
    if len(_recent_history_keys) > _RECENT_HISTORY_MAX:
        keys_list = list(_recent_history_keys)
        _recent_history_keys = set(keys_list[_RECENT_HISTORY_MAX // 2:])
    entry = {"type": msg_type, "data": data, "timestamp": time.time()}
    _message_history.append(entry)
    _messages_processed += 1


# --------- WebSocket 管理器（FastAPI 版） ---------

class WebSocketManager:
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()
        self._message_count = 0

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
            logger.info(f"WebSocket 客户端已连接 (共 {len(self._connections)} 个)")
        await self._send_to(ws, {
            "type": "system", "event": "connected",
            "data": {"message": "WebSocket 已连接", "connections": len(self._connections)},
            "timestamp": time.time()
        })

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)
            logger.info(f"WebSocket 客户端已断开 (剩余 {len(self._connections)} 个)")

    async def _send_to(self, ws: WebSocket, message: Dict[str, Any]):
        try:
            if ws.client_state.name == "CONNECTED":
                await ws.send_json(message)
        except Exception:
            pass

    async def broadcast(self, event: str, data: Dict[str, Any], msg_type: str = "system"):
        message = {"type": msg_type, "event": event, "data": data, "timestamp": time.time()}
        async with self._lock:
            dead = []
            for ws in self._connections:
                try:
                    if ws.client_state.name == "CONNECTED":
                        await ws.send_json(message)
                    else:
                        dead.append(ws)
                except Exception:
                    dead.append(ws)
            for d in dead:
                if d in self._connections:
                    self._connections.remove(d)
        self._message_count += 1

    async def broadcast_danmaku(self, username: str, content: str, room_id: str = ""):
        await self.broadcast("danmaku", {"username": username, "content": content, "room_id": room_id}, "danmaku")

    async def broadcast_ai_response(self, content: str, tone: str = "", visual_focus: str = "", mouth_state: str = ""):
        await self.broadcast("ai_response", {"content": content, "tone": tone, "visual_focus": visual_focus, "mouth_state": mouth_state}, "ai_response")

    async def broadcast_status_change(self, component: str, status: str, message: str = ""):
        await self.broadcast("status_change", {"component": component, "status": status, "message": message}, "system")

    async def broadcast_vision_result(self, target: str, analysis: str, success: bool):
        await self.broadcast("vision_analysis", {"target": target, "analysis": analysis, "success": success}, "vision")

    async def broadcast_log(self, log_entry: Dict[str, Any]):
        await self.broadcast("log", log_entry, "log")

    @property
    def connection_count(self) -> int:
        return len(self._connections)


_ws_manager = WebSocketManager()


# --------- Pydantic 模型 ---------

class ConfigUpdate(BaseModel):
    sections: Dict[str, Dict[str, Any]]


class SendMessage(BaseModel):
    content: str = "你好"
    name: str = "测试用户"


class VisionRequest(BaseModel):
    target: str = "desktop"
    prompt: str = ""


# --------- FastAPI 应用 ---------

app = FastAPI(
    title="LLM_vtuber 控制台",
    description="LLM_vtuber 项目 Web 控制台 API",
    version="2.0",
    docs_url=None,
    redoc_url=None,
)

_webui_dir = os.path.dirname(os.path.abspath(__file__))


@app.on_event("startup")
async def on_startup():
    logger.info("FastAPI 应用已启动")


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("FastAPI 应用已关闭")


# --------- API 路由 ---------

@app.post("/")
async def danmaku_post(request: Request):
    """弹幕监听程序的消息转发入口（POST /）"""
    try:
        app_main = _get_app_main()
        return await app_main.handle_post_request(request)
    except Exception as e:
        logger.error(f"弹幕处理失败: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/danmaku")
async def danmaku_api(request: Request):
    """弹幕 API 入口（与 POST / 功能相同，路径更清晰）"""
    return await danmaku_post(request)


@app.get("/api/status")
async def get_status():
    try:
        app_main = _get_app_main()
        live2d_connected = False
        live2d_status = "not_enabled"
        if app_main.live2d_manager is not None and app_main.live2d_manager.is_connected:
            live2d_connected = True
            live2d_status = "connected"
        elif app_main.args and app_main.args.live2d:
            live2d_status = "connection_failed"
        live2d_info = {
            "enabled": bool(app_main.args.live2d if app_main.args else False),
            "connected": live2d_connected,
            "status": live2d_status,
            "host": app_main.args.live2d_host if app_main.args else "localhost",
            "port": app_main.args.live2d_port if app_main.args else 8001,
        }
        tts_info = {
            "enabled": bool(app_main.args.tts if app_main.args else False),
            "port": _config.getint("tts", "port", fallback=9880) if _config else 9880,
        }
        model_info = {
            "name": _config.get("model", "model") if _config else "doubao-seed-1-8-251228",
            "temperature": _config.getfloat("model", "temperature") if _config else 0.7,
        }
        danmaku_info = {
            "room_id": app_main.args.room_id if app_main.args and app_main.args.room_id else
                       (_config.get("bilibili", "room_id") if _config else "904823"),
        }
        queue_size = app_main._message_queue.qsize() if app_main._message_queue else 0
        status = {
            "live2d": live2d_info,
            "tts": tts_info,
            "danmaku": danmaku_info,
            "model": model_info,
            "queue": {"size": queue_size, "max_size": 10},
            "uptime": int(time.time() - _start_time),
            "messages_processed": _messages_processed,
            "ws_connections": _ws_manager.connection_count,
        }
        return status
    except Exception as e:
        logger.error(f"get_status 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_config():
    try:
        if not _config:
            raise HTTPException(status_code=500, detail="配置未加载")
        config_data = {}
        for section in _config.sections():
            config_data[section] = dict(_config.items(section))
        if "bilibili" in config_data and "sessdata" in config_data["bilibili"]:
            sessdata = config_data["bilibili"]["sessdata"]
            config_data["bilibili"]["sessdata"] = sessdata[:10] + "..." if len(sessdata) > 10 else "***"
        if "api" in config_data and "api_key" in config_data.get("api", {}):
            config_data["api"]["api_key"] = "***"
        return config_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_config 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config")
async def update_config(request: Request):
    """更新配置 - 支持前端发送的 {section: {key: value}} 格式"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的配置数据"}

        if not _config or not _config_path or not os.path.exists(_config_path):
            return {"success": False, "error": "配置文件不存在"}

        updated_sections = []
        for section, values in data.items():
            if section.lower() in ("llm", "bilibili", "display", "live2d", "tts", "vision", "webui", "browser", "model"):
                if not _config.has_section(section):
                    _config.add_section(section)
                    updated_sections.append(section)
                for key, value in values.items():
                    _config.set(section, key, str(value))
                if section not in updated_sections:
                    updated_sections.append(section)

        if updated_sections:
            with open(_config_path, 'w', encoding='utf-8') as f:
                _config.write(f)
            logger.info(f"配置已更新: {updated_sections}")
            await _ws_manager.broadcast_status_change("config", "updated", f"已更新: {updated_sections}")
            return {"success": True}
        else:
            return {"success": False, "error": "未识别的配置段"}
    except Exception as e:
        logger.error(f"update_config 错误: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/messages")
async def get_messages(limit: int = Query(default=50, ge=1, le=500)):
    try:
        messages = list(_message_history)
        if limit > 0:
            messages = messages[-limit:]
        return {"messages": messages, "total": len(_message_history)}
    except Exception as e:
        logger.error(f"get_messages 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/send")
async def send_test_message(body: SendMessage):
    try:
        app_main = _get_app_main()
        if app_main._message_queue is None:
            app_main._message_queue = asyncio.Queue(maxsize=app_main._QUEUE_MAX_SIZE)
            app_main._queue_worker_task = asyncio.create_task(app_main._queue_worker())
            logger.warning("消息队列未初始化，已自动创建")
        if not app_main._message_queue:
            raise HTTPException(status_code=500, detail="消息队列未初始化")
        messages = [{"role": "user", "content": f"{body.name}: {body.content}", "name": body.name}]
        await app_main._message_queue.put(messages)
        logger.info(f"测试消息已入队: {body.content}")
        await _ws_manager.broadcast_danmaku(body.name, body.content)
        return {"success": True, "queued": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"send_test_message 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vision")
async def trigger_vision(body: VisionRequest):
    try:
        from tool.vision_tool import vision_analyze

        async def run_vision():
            result = await vision_analyze(target=body.target, prompt=body.prompt)
            await _ws_manager.broadcast_vision_result(
                target=result.get("screenshot_info", {}).get("window_title", body.target),
                analysis=result.get("analysis", result.get("error", "")),
                success=result.get("success", False)
            )
        asyncio.create_task(run_vision())
        return {"success": True, "message": "视觉分析已触发，结果将通过 WebSocket 推送"}
    except Exception as e:
        logger.error(f"trigger_vision 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/windows")
async def list_windows():
    try:
        from tool.vision_tool import list_available_windows
        windows = list_available_windows()
        return {"success": True, "windows": windows, "count": len(windows)}
    except Exception as e:
        logger.error(f"list_windows 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs")
async def get_logs(level: str = Query(default=""), logger_name: str = Query(default=""), limit: int = Query(default=200, ge=1, le=500)):
    try:
        level = level.upper()
        logs = list(_log_buffer)
        if level and level in _LOG_LEVELS:
            min_level = _LOG_LEVELS[level]
            logs = [l for l in logs if _LOG_LEVELS.get(l["level"], 0) >= min_level]
        if logger_name:
            logs = [l for l in logs if logger_name.lower() in l["logger"].lower()]
        if limit > 0:
            logs = logs[-limit:]
        return {"logs": logs, "total": len(_log_buffer)}
    except Exception as e:
        logger.error(f"get_logs 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/features")
async def get_features():
    try:
        app_main = _get_app_main()
        try:
            live2d_avail = bool(app_main.live2d_available)
        except AttributeError:
            live2d_avail = False
        try:
            tts_avail = bool(app_main.tts_available)
        except AttributeError:
            tts_avail = False
        live2d_connected = False
        live2d_status = "not_enabled"
        if app_main.live2d_manager is not None and app_main.live2d_manager.is_connected:
            live2d_connected = True
            live2d_status = "connected"
        elif app_main.args and app_main.args.live2d:
            live2d_status = "connection_failed"
        features = {
            "live2d": {
                "enabled": bool(app_main.args.live2d if app_main.args else False),
                "available": live2d_avail,
                "connected": live2d_connected,
                "status": live2d_status,
                "host": app_main.args.live2d_host if app_main.args else "localhost",
                "port": app_main.args.live2d_port if app_main.args else 8001,
            },
            "tts": {
                "enabled": bool(app_main.args.tts if app_main.args else False),
                "available": tts_avail,
                "port": _config.getint("tts", "port", fallback=9880) if _config else 9880,
            },
            "danmaku_listener": {
                "enabled": bool(app_main._bili_process and app_main._bili_process.poll() is None),
                "available": True,
                "room_id": app_main.args.room_id if app_main.args and app_main.args.room_id else
                           (_config.get("bilibili", "room_id") if _config else "904823"),
            },
            "langgraph": {
                "running": bool(app_main.langgraph_manager and app_main.langgraph_manager.graph is not None),
            },
        }
        return features
    except Exception as e:
        logger.error(f"get_features 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/features/{name}/start")
async def start_feature(name: str):
    try:
        app_main = _get_app_main()
        valid = {"live2d", "tts", "danmaku_listener"}
        if name not in valid:
            raise HTTPException(status_code=400, detail=f"未知功能: {name}")

        async def start():
            try:
                result = False
                if name == "live2d" and not (app_main.args and app_main.args.live2d):
                    result = await app_main.enable_live2d()
                elif name == "tts" and not (app_main.args and app_main.args.tts):
                    result = await app_main.enable_tts()
                elif name == "danmaku_listener" and not (app_main._bili_process and app_main._bili_process.poll() is None):
                    result = await app_main.start_danmaku_listener()
                await _ws_manager.broadcast_status_change(name, "started" if result else "failed", f"{name} {'启动成功' if result else '启动失败'}")
            except Exception as e:
                logger.error(f"start_feature 异步错误: {e}")
                await _ws_manager.broadcast_status_change(name, "failed", str(e))
        asyncio.create_task(start())
        return {"success": True, "message": f"正在启动 {name}..."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"start_feature 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/features/{name}/stop")
async def stop_feature(name: str):
    try:
        app_main = _get_app_main()
        valid = {"live2d", "tts", "danmaku_listener"}
        if name not in valid:
            raise HTTPException(status_code=400, detail=f"未知功能: {name}")

        async def stop():
            try:
                if name == "live2d":
                    await app_main.disable_live2d()
                elif name == "tts":
                    await app_main.disable_tts()
                elif name == "danmaku_listener":
                    await app_main.stop_danmaku_listener()
                await _ws_manager.broadcast_status_change(name, "stopped", f"{name} 已停止")
            except Exception as e:
                logger.error(f"stop_feature 异步错误: {e}")
                await _ws_manager.broadcast_status_change(name, "failed", str(e))
        asyncio.create_task(stop())
        return {"success": True, "message": f"正在停止 {name}..."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"stop_feature 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --------- WebSocket ---------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await _ws_manager.connect(ws)
    try:
        async for message in ws.iter_json():
            event = message.get("event", "")
            payload = message.get("data", {})
            if event == "ping":
                await _ws_manager._send_to(ws, {
                    "type": "system", "event": "pong",
                    "data": {"message": "pong"}, "timestamp": time.time()
                })
            elif event == "subscribe":
                await _ws_manager._send_to(ws, {
                    "type": "system", "event": "subscribed",
                    "data": {"channels": payload.get("channels", ["all"])},
                    "timestamp": time.time()
                })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
    finally:
        await _ws_manager.disconnect(ws)


# --------- 静态文件 & 首页 ---------

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(_webui_dir, "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse("<h1>Web UI not found</h1>")
    with open(index_path, 'r', encoding='utf-8') as f:
        return HTMLResponse(f.read())


# 导出 ws_manager 供 main.py 使用
ws_manager = _ws_manager
