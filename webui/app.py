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
ws_manager = _ws_manager  # 提前导出，确保导入时可用


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
        danmaku_enabled = False
        try:
            bili_proc = getattr(app_main, '_bili_process', None)
            danmaku_enabled = bool(bili_proc and bili_proc.poll() is None)
        except Exception:
            pass

        danmaku_info = {
            "enabled": danmaku_enabled,
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
        try:
            cloud_tts_avail = bool(app_main.cloud_tts_node_available)
        except AttributeError:
            cloud_tts_avail = False
        try:
            vision_avail = bool(app_main.vision_danmu_available)
        except AttributeError:
            vision_avail = False
        vision_enabled = False
        try:
            vision_enabled = bool(
                app_main.vision_danmu_config.enabled and
                app_main.vision_danmu_service and
                app_main.vision_danmu_service.is_running
            )
        except AttributeError:
            pass
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
            "cloud_tts": {
                "enabled": bool(getattr(app_main.args, 'cloud_tts', False) if app_main.args else False),
                "available": cloud_tts_avail,
                "provider": app_main.cloud_tts_config.provider if hasattr(app_main, 'cloud_tts_config') and app_main.cloud_tts_config else "doubao",
                "api_configured": app_main.cloud_tts_config.is_valid() if hasattr(app_main, 'cloud_tts_config') and app_main.cloud_tts_config else False,
                "api_version": app_main.cloud_tts_config.api_version if hasattr(app_main, 'cloud_tts_config') and app_main.cloud_tts_config else "v3",
                "has_appid": bool(app_main.cloud_tts_config.appid) if hasattr(app_main, 'cloud_tts_config') and app_main.cloud_tts_config else False,
            },
            "danmaku_listener": {
                "enabled": bool(app_main._bili_process and app_main._bili_process.poll() is None),
                "available": True,
                "room_id": app_main.args.room_id if app_main.args and app_main.args.room_id else
                           (_config.get("bilibili", "room_id") if _config else "904823"),
            },
            "vision_danmu": {
                "enabled": vision_enabled,
                "available": vision_avail,
                "capture_interval": app_main.vision_danmu_config.capture_interval if hasattr(app_main, 'vision_danmu_config') else 5,
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
        valid = {"live2d", "tts", "danmaku_listener", "vision_danmu", "cloud_tts"}
        if name not in valid:
            raise HTTPException(status_code=400, detail=f"未知功能: {name}")

        async def start():
            try:
                result = False
                if name == "live2d" and not (app_main.args and app_main.args.live2d):
                    result = await app_main.enable_live2d()
                elif name == "tts" and not (app_main.args and app_main.args.tts):
                    result = await app_main.enable_tts()
                elif name == "cloud_tts" and not (app_main.args and getattr(app_main.args, 'cloud_tts', False)):
                    result = await app_main.enable_cloud_tts()
                elif name == "danmaku_listener" and not (app_main._bili_process and app_main._bili_process.poll() is None):
                    result = await app_main.start_danmaku_listener()
                elif name == "vision_danmu":
                    result = await app_main.enable_vision_danmu()
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
        valid = {"live2d", "tts", "danmaku_listener", "vision_danmu", "cloud_tts"}
        if name not in valid:
            raise HTTPException(status_code=400, detail=f"未知功能: {name}")

        async def stop():
            try:
                if name == "live2d":
                    await app_main.disable_live2d()
                elif name == "tts":
                    await app_main.disable_tts()
                elif name == "cloud_tts":
                    await app_main.disable_cloud_tts()
                elif name == "danmaku_listener":
                    await app_main.stop_danmaku_listener()
                elif name == "vision_danmu":
                    await app_main.disable_vision_danmu()
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


# --------- AI 读弹幕 TTS API ---------

@app.get("/api/config/danmu-tts")
async def get_danmu_tts_config():
    try:
        app_main = _get_app_main()
        cfg = app_main.danmu_tts_config
        return {
            "enabled": cfg.enabled,
            "read_interval": cfg.read_interval,
            "max_text_length": cfg.max_text_length,
            "clean_emoji": cfg.clean_emoji,
        }
    except Exception as e:
        logger.error(f"get_danmu_tts_config 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config/danmu-tts")
async def set_danmu_tts_config(request: Request):
    try:
        app_main = _get_app_main()
        cfg = app_main.danmu_tts_config
        data = await request.json()
        if "enabled" in data:
            cfg.enabled = data["enabled"]
            if cfg.enabled and app_main.danmu_tts_available and app_main.danmu_tts_service is None:
                try:
                    from danmu_tts import DanmuTtsService
                    app_main.danmu_tts_service = DanmuTtsService(cfg)
                    app_main.danmu_tts_service.start()
                    logger.info("AI 读弹幕 TTS 服务已通过 API 启动")
                except Exception as e:
                    logger.error(f"AI 读弹幕 TTS 服务启动失败: {e}")
                    app_main.danmu_tts_service = None
            elif not cfg.enabled and app_main.danmu_tts_service:
                try:
                    app_main.danmu_tts_service.stop()
                    logger.info("AI 读弹幕 TTS 服务已通过 API 停止")
                except Exception as e:
                    logger.warning(f"停止 AI 读弹幕 TTS 服务失败: {e}")
        if "read_interval" in data:
            cfg.read_interval = data["read_interval"]
        if "max_text_length" in data:
            cfg.max_text_length = data["max_text_length"]
        if "clean_emoji" in data:
            cfg.clean_emoji = data["clean_emoji"]
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"set_danmu_tts_config 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status/danmu-tts")
async def get_danmu_tts_status():
    try:
        app_main = _get_app_main()
        if app_main.danmu_tts_service:
            return app_main.danmu_tts_service.get_status()
        return {"status": "not_initialized"}
    except Exception as e:
        logger.error(f"get_danmu_tts_status 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --------- 视觉弹幕 API ---------

@app.get("/api/config/vision-danmu")
async def get_vision_danmu_config():
    try:
        app_main = _get_app_main()
        cfg = app_main.vision_danmu_config
        return {
            "enabled": cfg.enabled,
            "capture_interval": cfg.capture_interval,
            "target_window": cfg.target_window,
            "persona": cfg.persona,
            "max_comment_length": cfg.max_comment_length,
            "cooldown": cfg.cooldown,
        }
    except Exception as e:
        logger.error(f"get_vision_danmu_config 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config/vision-danmu")
async def set_vision_danmu_config(request: Request):
    try:
        app_main = _get_app_main()
        cfg = app_main.vision_danmu_config
        data = await request.json()
        if "enabled" in data:
            cfg.enabled = data["enabled"]
        if "capture_interval" in data:
            cfg.capture_interval = data["capture_interval"]
        if "target_window" in data:
            cfg.target_window = data["target_window"]
        if "persona" in data:
            cfg.persona = data["persona"]
        if "max_comment_length" in data:
            cfg.max_comment_length = data["max_comment_length"]
        if "cooldown" in data:
            cfg.cooldown = data["cooldown"]
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"set_vision_danmu_config 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status/vision-danmu")
async def get_vision_danmu_status():
    try:
        app_main = _get_app_main()
        if app_main.vision_danmu_service:
            return app_main.vision_danmu_service.get_status()
        return {"status": "not_initialized"}
    except Exception as e:
        logger.error(f"get_vision_danmu_status 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --------- Milvus 管理 API ---------

import configparser

def _load_app_config():
    config = configparser.ConfigParser()
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.ini')
    if os.path.exists(config_path):
        config.read(config_path, encoding='utf-8')
    return config

_app_config = _load_app_config()

MILVUS_URI = os.getenv("MILVUS_URI", _app_config.get("milvus", "uri", fallback="http://localhost:19530"))
MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", _app_config.get("milvus", "token", fallback=""))
MILVUS_DB = os.getenv("MILVUS_DB", _app_config.get("milvus", "database", fallback="LLM_vtuber"))


def _get_milvus_client():
    from pymilvus import MilvusClient
    return MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN, db_name=MILVUS_DB)


def _ensure_collection_loaded(client, collection_name):
    """确保集合已加载到内存"""
    try:
        client.load_collection(collection_name, timeout=10)
        return True
    except Exception:
        return False


def _get_collection_count(client, collection_name):
    """获取集合数据量（确保集合已加载）"""
    try:
        _ensure_collection_loaded(client, collection_name)
        return client.count_items(collection_name)
    except Exception:
        try:
            stats = client.get_collection_stats(collection_name)
            return int(stats.get('row_count', 0))
        except Exception:
            return 0


@app.get("/api/milvus/collections")
async def milvus_list_collections():
    try:
        client = _get_milvus_client()
        collections = client.list_collections()
        result = []
        for name in collections:
            try:
                info = client.describe_collection(collection_name=name)
                count = _get_collection_count(client, name)
                result.append({
                    "name": name,
                    "count": count,
                    "description": info.get("description", ""),
                    "fields": [f.get("name", "") for f in info.get("fields", [])],
                })
            except Exception as e:
                result.append({"name": name, "error": str(e)})
        return {"success": True, "collections": result}
    except Exception as e:
        logger.error(f"milvus_list_collections 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/milvus/collections/{collection_name}")
async def milvus_get_collection(collection_name: str):
    try:
        client = _get_milvus_client()
        info = client.describe_collection(collection_name=collection_name)
        count = _get_collection_count(client, collection_name)
        loaded = False
        try:
            state = client.get_load_state(collection_name=collection_name)
            loaded = "Load" in str(state)
        except Exception:
            pass
        return {
            "success": True,
            "name": collection_name,
            "count": count,
            "loaded": loaded,
            "description": info.get("description", ""),
            "fields": info.get("fields", []),
        }
    except Exception as e:
        logger.error(f"milvus_get_collection 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/milvus/collections/{collection_name}/data")
async def milvus_query_data(
    collection_name: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filter: str = Query(default=""),
    output_fields: str = Query(default=""),
    start_time: str = Query(default="", description="开始时间，ISO 格式(如 2026-01-01T00:00:00Z)"),
    end_time: str = Query(default="", description="结束时间，ISO 格式(如 2026-12-31T23:59:59Z)"),
):
    try:
        client = _get_milvus_client()
        client.load_collection(collection_name=collection_name)

        coll_info = client.describe_collection(collection_name=collection_name)
        all_fields = [f["name"] for f in coll_info.get("fields", [])
                      if f.get("name") not in ("content_vector", "summary_vector", "user_vector")]

        if output_fields:
            fields = [f.strip() for f in output_fields.split(",") if f.strip()]
        else:
            fields = all_fields

        query_params = {"limit": limit, "offset": offset, "output_fields": fields}
        if filter:
            query_params["filter"] = filter

        results = client.query(collection_name=collection_name, **query_params)

        # 时间范围过滤（在内存中过滤，因为 Milvus Timestamptz 不支持直接比较查询）
        if start_time or end_time:
            import datetime

            def parse_ts(ts_str: str) -> datetime.datetime:
                """解析 timestamp 字符串为 datetime 对象"""
                ts_str = ts_str.replace("Z", "+00:00")
                try:
                    return datetime.datetime.fromisoformat(ts_str)
                except Exception:
                    # 尝试其他格式
                    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                        try:
                            dt = datetime.datetime.strptime(ts_str[:len(fmt)+2] if "T" in ts_str else ts_str, fmt)
                            return dt.replace(tzinfo=datetime.timezone.utc)
                        except ValueError:
                            continue
                    raise ValueError(f"无法解析时间: {ts_str}")

            def parse_filter_time(time_str: str) -> datetime.datetime:
                """解析过滤用的时间字符串"""
                time_str = time_str.replace("Z", "+00:00")
                # 如果只有日期，加上时间
                if len(time_str) == 10:  # YYYY-MM-DD
                    if start_time and time_str == start_time:
                        time_str += "T00:00:00+00:00"
                    elif end_time and time_str == end_time:
                        time_str += "T23:59:59+00:00"
                return datetime.datetime.fromisoformat(time_str)

            filtered = []
            for item in results:
                ts_str = item.get("timestamp", "")
                if not ts_str:
                    continue
                try:
                    item_ts = parse_ts(ts_str)
                    if start_time:
                        start_dt = parse_filter_time(start_time)
                        if item_ts < start_dt:
                            continue
                    if end_time:
                        end_dt = parse_filter_time(end_time)
                        if item_ts > end_dt:
                            continue
                    filtered.append(item)
                except Exception:
                    filtered.append(item)  # 解析失败的保留
            results = filtered

        total = 0
        try:
            total = client.count_items(collection_name=collection_name)
        except Exception:
            total = len(results)

        return {"success": True, "data": results, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        logger.error(f"milvus_query_data 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/milvus/collections/{collection_name}/delete")
async def milvus_delete_data(collection_name: str, request: Request):
    try:
        body = await request.json()
        client = _get_milvus_client()

        ids = body.get("ids", [])
        filter_expr = body.get("filter", "")
        confirm = body.get("confirm", False)

        if not confirm:
            return {"success": False, "error": "需要 confirm=true 确认删除操作"}

        if ids:
            results = client.delete(collection_name=collection_name, ids=ids)
            logger.info(f"删除 Milvus 数据: collection={collection_name}, ids={ids}, results={results}")
            return {"success": True, "deleted_count": len(ids), "results": results}
        elif filter_expr:
            results = client.delete(collection_name=collection_name, filter=filter_expr)
            logger.info(f"删除 Milvus 数据: collection={collection_name}, filter={filter_expr}, results={results}")
            return {"success": True, "filter": filter_expr, "results": results}
        else:
            return {"success": False, "error": "需要提供 ids 或 filter 条件"}
    except Exception as e:
        logger.error(f"milvus_delete_data 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/milvus/collections/{collection_name}")
async def milvus_drop_collection(collection_name: str, request: Request):
    try:
        body = await request.json()
        confirm = body.get("confirm", False)
        if not confirm:
            return {"success": False, "error": "需要 confirm=true 确认删除操作"}

        client = _get_milvus_client()
        client.drop_collection(collection_name=collection_name)
        logger.warning(f"删除 Milvus collection: {collection_name}")
        return {"success": True, "message": f"Collection {collection_name} 已删除"}
    except Exception as e:
        logger.error(f"milvus_drop_collection 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/milvus/collections/{collection_name}/load")
async def milvus_load_collection(collection_name: str):
    try:
        client = _get_milvus_client()
        client.load_collection(collection_name=collection_name)
        return {"success": True, "message": f"Collection {collection_name} 已加载"}
    except Exception as e:
        logger.error(f"milvus_load_collection 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --------- Knowledge Base ---------

_knowledge_manager = None

def _get_knowledge_manager():
    global _knowledge_manager
    if _knowledge_manager is None:
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'RAG'))
        from knowledge_base import get_knowledge_manager
        _knowledge_manager = get_knowledge_manager()
    return _knowledge_manager


@app.post("/api/knowledge/upload")
async def knowledge_upload(request: Request):
    """上传文档到知识库"""
    try:
        import tempfile
        body = await request.json()
        file_data_base64 = body.get("file_data", "")
        filename = body.get("filename", "")
        description = body.get("description", "")
        tags = body.get("tags", [])
        uploaded_by = body.get("uploaded_by", "webui")

        if not file_data_base64 or not filename:
            raise HTTPException(status_code=400, detail="缺少文件数据或文件名")

        import base64
        file_data = base64.b64decode(file_data_base64)

        km = _get_knowledge_manager()
        result = km.add_document(
            file_data=file_data,
            filename=filename,
            uploaded_by=uploaded_by,
            tags=tags,
            description=description
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"knowledge_upload 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/knowledge/add-text")
async def knowledge_add_text(request: Request):
    """添加文本到知识库"""
    try:
        body = await request.json()
        text = body.get("text", "")
        description = body.get("description", "")
        tags = body.get("tags", [])
        uploaded_by = body.get("uploaded_by", "webui")
        source_type = body.get("source_type", "manual")

        if not text:
            raise HTTPException(status_code=400, detail="缺少文本内容")

        km = _get_knowledge_manager()
        result = km.add_text(
            text=text,
            description=description,
            tags=tags,
            uploaded_by=uploaded_by,
            source_type=source_type
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"knowledge_add_text 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/knowledge/search")
async def knowledge_search(
    query: str = Query(..., description="搜索查询"),
    top_k: int = Query(5, ge=1, le=50, description="返回结果数"),
    content_type: str = Query(None, description="内容类型筛选"),
    tags: str = Query(None, description="标签筛选(逗号分隔)"),
    score_threshold: float = Query(0.3, ge=0.0, le=1.0, description="相似度阈值")
):
    """搜索知识库"""
    try:
        km = _get_knowledge_manager()
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        results = km.search(
            query=query,
            top_k=top_k,
            content_type=content_type,
            tags=tag_list,
            score_threshold=score_threshold
        )
        return {"success": True, "data": results, "total": len(results)}
    except Exception as e:
        logger.error(f"knowledge_search 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/knowledge/documents")
async def knowledge_list_documents(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    content_type: str = Query(None, description="内容类型筛选"),
    tags: str = Query(None, description="标签筛选(逗号分隔)")
):
    """列出知识库文档"""
    try:
        km = _get_knowledge_manager()
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        result = km.list_documents(
            limit=limit,
            offset=offset,
            content_type=content_type,
            tags=tag_list
        )
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"knowledge_list_documents 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/knowledge/documents/{doc_id}")
async def knowledge_get_document(doc_id: str):
    """获取文档详情"""
    try:
        km = _get_knowledge_manager()
        doc = km.get_document(doc_id)
        if doc:
            return {"success": True, "data": doc}
        else:
            return {"success": False, "error": "文档不存在"}
    except Exception as e:
        logger.error(f"knowledge_get_document 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/knowledge/documents/{doc_id}")
async def knowledge_delete_document(doc_id: str):
    """删除文档"""
    try:
        km = _get_knowledge_manager()
        result = km.delete_document(doc_id)
        return result
    except Exception as e:
        logger.error(f"knowledge_delete_document 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/knowledge/stats")
async def knowledge_stats():
    """获取知识库统计"""
    try:
        km = _get_knowledge_manager()
        stats = km.get_stats()
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"knowledge_stats 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/knowledge/minio/buckets")
async def knowledge_minio_buckets():
    """列出 MinIO 存储桶"""
    try:
        from knowledge_base import MinIOManager
        minio = MinIOManager()
        buckets = minio.client.list_buckets()
        return {"success": True, "data": [{"name": b.name, "creation_date": b.creation_date.isoformat() if b.creation_date else None} for b in buckets]}
    except Exception as e:
        logger.error(f"knowledge_minio_buckets 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/knowledge/minio/objects")
async def knowledge_minio_objects(
    bucket: str = Query("knowledge-files", description="存储桶名称"),
    prefix: str = Query("", description="前缀筛选")
):
    """列出 MinIO 文件"""
    try:
        from knowledge_base import MinIOManager
        minio = MinIOManager()
        objects = minio.list_objects(bucket, prefix)
        return {"success": True, "data": objects, "bucket": bucket}
    except Exception as e:
        logger.error(f"knowledge_minio_objects 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/knowledge/minio/upload")
async def knowledge_minio_upload(request: Request):
    """直接上传文件到 MinIO"""
    try:
        body = await request.json()
        file_data_base64 = body.get("file_data", "")
        filename = body.get("filename", "")
        bucket = body.get("bucket", "knowledge-files")

        if not file_data_base64 or not filename:
            raise HTTPException(status_code=400, detail="缺少文件数据或文件名")

        import base64
        file_data = base64.b64decode(file_data_base64)

        from knowledge_base import MinIOManager
        minio = MinIOManager()
        object_name, size = minio.upload_file(file_data, filename, bucket)

        return {"success": True, "object_name": object_name, "size": size, "bucket": bucket}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"knowledge_minio_upload 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --------- Context Controller ---------

_context_controller = None

def _get_context_controller():
    global _context_controller
    if _context_controller is None:
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'RAG'))
        from context_controller import get_context_controller
        _context_controller = get_context_controller()
    return _context_controller


@app.get("/api/context/config")
async def context_get_config():
    """获取上下文配置"""
    try:
        cc = _get_context_controller()
        return {"success": True, "data": cc.get_config()}
    except Exception as e:
        logger.error(f"context_get_config 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/context/config")
async def context_update_config(request: Request):
    """更新上下文配置"""
    try:
        body = await request.json()
        cc = _get_context_controller()
        new_config = cc.update_config(body)
        return {"success": True, "data": new_config}
    except Exception as e:
        logger.error(f"context_update_config 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/context/process")
async def context_process(request: Request):
    """处理上下文 - 测试上下文控制流程"""
    try:
        body = await request.json()
        messages = body.get("messages", [])
        query = body.get("query", "")
        system_prompt = body.get("system_prompt", "")
        additional_context = body.get("additional_context", "")

        cc = _get_context_controller()
        result = cc.process_context(
            messages=messages,
            current_query=query,
            system_prompt=system_prompt,
            additional_context=additional_context
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"context_process 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/context/summarize")
async def context_summarize(request: Request):
    """压缩历史消息"""
    try:
        body = await request.json()
        messages = body.get("messages", [])
        max_keep = body.get("max_keep", 10)

        sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'RAG'))
        from context_controller import ContextSummarizer

        compressed, summary = ContextSummarizer.compress_messages(messages, max_keep)
        return {
            "success": True,
            "data": {
                "compressed": compressed,
                "summary": summary,
                "original_count": len(messages),
                "compressed_count": len(compressed)
            }
        }
    except Exception as e:
        logger.error(f"context_summarize 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/context/stats")
async def context_stats(request: Request):
    """获取上下文统计"""
    try:
        body = await request.json()
        messages = body.get("messages", [])

        sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'RAG'))
        from context_controller import ContextSelector

        stats = ContextSelector.get_context_statistics(messages)
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"context_stats 错误: {e}")
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


