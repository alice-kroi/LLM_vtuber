#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web UI API 路由处理

提供 RESTful API 用于 Web 控制台与后端交互：
- GET  /api/status     系统状态
- GET  /api/config     当前配置
- POST /api/config     更新配置
- GET  /api/messages   消息历史
- POST /api/send       发送测试消息
- POST /api/vision     触发视觉分析
- GET  /api/windows    列出可见窗口
- GET  /               控制台首页
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import Dict, Any, Optional

from aiohttp import web

logger = logging.getLogger(__name__)

# --------- 模块级状态（由 main.py 初始化/更新） ---------

# 消息历史环形缓冲区，保存最近 500 条消息
_message_history: deque = deque(maxlen=500)

# 消息历史去重，防止同一条消息在多个节点被重复添加到历史
# (例如 handle_post_request 与 _llm_process_node 都会上报 danmaku)
_recent_history_keys: set = set()
_RECENT_HISTORY_MAX = 2000

# 全局配置引用（由 main.py 设置）
_config = None
_config_path = None

# 运行开始时间
_start_time = time.time()

# 消息计数
_messages_processed = 0


# --------- 日志收集器（用于 Web UI 日志页面） ---------

# 内存日志缓冲区
_log_buffer: deque = deque(maxlen=2000)

# 日志级别筛选映射
_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class MemoryLogHandler(logging.Handler):
    """将日志同时输出到内存缓冲区，供 Web UI 查询"""

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "timestamp": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            _log_buffer.append(entry)

            # 实时推送给 WebSocket（仅推送 INFO 及以上，避免刷爆）
            try:
                from webui.websocket import ws_manager
                if record.levelno >= logging.INFO:
                    asyncio.create_task(ws_manager.broadcast_log(entry))
            except Exception:
                pass
        except Exception:
            pass


# 注册内存日志处理器
_memory_log_handler = MemoryLogHandler()
_memory_log_handler.setLevel(logging.INFO)
# 添加到 root logger
logging.getLogger().addHandler(_memory_log_handler)


def init_api(config=None, config_path=None):
    """
    初始化 API 模块（在 main.py 中调用）

    Args:
        config: configparser.ConfigParser 实例
        config_path: config.ini 文件路径
    """
    global _config, _config_path
    _config = config
    _config_path = config_path
    logger.info("API 模块已初始化")


def _build_history_key(msg_type: str, data: Dict[str, Any]) -> str:
    """构造消息历史去重键（覆盖 danmaku / ai_response 两类主要消息）"""
    if msg_type == "danmaku":
        uname = data.get("username", data.get("name", ""))
        content = data.get("content", "")
        return f"D:{uname}|{content}"
    if msg_type == "ai_response":
        content = data.get("content", "")
        tone = data.get("tone", "")
        return f"A:{tone}|{content[:100]}"
    # 其他类型仅按类型+时间戳粗粒度去重
    return f"X:{msg_type}|{int(time.time())}"


def add_message_to_history(msg_type: str, data: Dict[str, Any]):
    """
    添加消息到历史记录（由 main.py 在关键节点调用）

    内置去重：同一条消息（按 类型+内容 指纹）在短时间内只记录一次，
    避免 handle_post_request / _llm_process_node / API 多处调用导致的重复。

    Args:
        msg_type: 消息类型 (danmaku|ai_response|system|vision)
        data: 消息数据
    """
    global _messages_processed, _recent_history_keys

    key = _build_history_key(msg_type, data)
    if key in _recent_history_keys:
        logger.debug(f"[历史去重] 跳过重复记录: {key[:50]}")
        return
    _recent_history_keys.add(key)
    if len(_recent_history_keys) > _RECENT_HISTORY_MAX:
        keys_list = list(_recent_history_keys)
        _recent_history_keys = set(keys_list[_RECENT_HISTORY_MAX // 2:])

    entry = {
        "type": msg_type,
        "data": data,
        "timestamp": time.time()
    }
    _message_history.append(entry)
    _messages_processed += 1


# --------- 路由处理函数 ---------

async def get_status(request: web.Request) -> web.Response:
    """GET /api/status - 返回系统状态"""
    try:
        # 从 main.py 获取全局状态
        import main as app_main

        # Live2D 连接状态
        live2d_connected = False
        live2d_status = "not_enabled"
        if app_main.args and app_main.args.live2d:
            if app_main.live2d_manager is not None and app_main.live2d_manager.is_connected:
                live2d_connected = True
                live2d_status = "connected"
            else:
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
            "ws_connections": 0,
        }

        # 尝试获取 WebSocket 连接数
        try:
            from webui.websocket import ws_manager
            status["ws_connections"] = ws_manager.connection_count
        except Exception:
            pass

        return web.json_response(status)
    except Exception as e:
        logger.error(f"get_status 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def get_config(request: web.Request) -> web.Response:
    """GET /api/config - 返回当前配置"""
    try:
        if not _config:
            return web.json_response({"error": "配置未加载"}, status=500)

        config_data = {}
        for section in _config.sections():
            config_data[section] = dict(_config.items(section))

        # 隐藏敏感信息
        if "bilibili" in config_data and "sessdata" in config_data["bilibili"]:
            sessdata = config_data["bilibili"]["sessdata"]
            config_data["bilibili"]["sessdata"] = sessdata[:10] + "..." if len(sessdata) > 10 else "***"

        if "api" in config_data and "api_key" in config_data.get("api", {}):
            config_data["api"]["api_key"] = "***"

        return web.json_response(config_data)
    except Exception as e:
        logger.error(f"get_config 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def update_config(request: web.Request) -> web.Response:
    """POST /api/config - 更新配置"""
    try:
        if not _config or not _config_path:
            return web.json_response({"error": "配置未加载"}, status=500)

        body = await request.json()
        updated_sections = []

        for section, values in body.items():
            if section not in _config.sections():
                _config.add_section(section)
                updated_sections.append(section)
            for key, value in values.items():
                _config.set(section, key, str(value))

        # 保存到文件
        with open(_config_path, 'w', encoding='utf-8') as f:
            _config.write(f)

        logger.info(f"配置已更新: {updated_sections}")

        # 通知 WebSocket
        try:
            from webui.websocket import ws_manager
            await ws_manager.broadcast_status_change("config", "updated", f"已更新: {updated_sections}")
        except Exception:
            pass

        return web.json_response({"success": True, "updated_sections": updated_sections})
    except Exception as e:
        logger.error(f"update_config 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def get_messages(request: web.Request) -> web.Response:
    """GET /api/messages - 返回消息历史"""
    try:
        limit = int(request.query.get("limit", 50))
        messages = list(_message_history)
        if limit > 0:
            messages = messages[-limit:]
        return web.json_response({"messages": messages, "total": len(_message_history)})
    except Exception as e:
        logger.error(f"get_messages 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def send_test_message(request: web.Request) -> web.Response:
    """POST /api/send - 发送测试消息"""
    try:
        body = await request.json()
        content = body.get("content", "你好")
        name = body.get("name", "测试用户")

        import main as app_main

        # 确保队列已初始化
        if app_main._message_queue is None:
            import asyncio
            app_main._message_queue = asyncio.Queue(maxsize=app_main._QUEUE_MAX_SIZE)
            app_main._queue_worker_task = asyncio.create_task(app_main._queue_worker())
            logger.warning("消息队列未初始化，已自动创建")

        if not app_main._message_queue:
            return web.json_response({"error": "消息队列未初始化"}, status=500)

        # 构造消息（注意：入队后由 _llm_process_node 统一添加到历史记录，避免重复）
        messages = [{
            "role": "user",
            "content": f"{name}: {content}",
            "name": name
        }]

        await app_main._message_queue.put(messages)
        logger.info(f"测试消息已入队: {content}")

        # 立刻 WebSocket 推送弹幕消息，以便用户在 UI 上即时看到
        try:
            from webui.websocket import ws_manager
            await ws_manager.broadcast_danmaku(name, content)
        except Exception:
            pass

        return web.json_response({"success": True, "queued": True})
    except Exception as e:
        logger.error(f"send_test_message 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def trigger_vision(request: web.Request) -> web.Response:
    """POST /api/vision - 触发视觉分析"""
    try:
        body = await request.json()
        target = body.get("target", "desktop")
        prompt = body.get("prompt", "")

        from tool.vision_tool import vision_analyze

        # 在后台执行（不阻塞请求）
        async def run_vision():
            result = await vision_analyze(target=target, prompt=prompt)
            try:
                from webui.websocket import ws_manager
                await ws_manager.broadcast_vision_result(
                    target=result.get("screenshot_info", {}).get("window_title", target),
                    analysis=result.get("analysis", result.get("error", "")),
                    success=result.get("success", False)
                )
            except Exception as e:
                logger.error(f"广播视觉结果失败: {e}")

        asyncio.create_task(run_vision())

        return web.json_response({
            "success": True,
            "message": "视觉分析已触发，结果将通过 WebSocket 推送"
        })
    except Exception as e:
        logger.error(f"trigger_vision 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def list_windows(request: web.Request) -> web.Response:
    """GET /api/windows - 列出系统可见窗口"""
    try:
        from tool.vision_tool import list_available_windows
        windows = list_available_windows()
        return web.json_response({"success": True, "windows": windows, "count": len(windows)})
    except Exception as e:
        logger.error(f"list_windows 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def get_logs(request: web.Request) -> web.Response:
    """GET /api/logs - 返回日志消息，支持按级别和logger筛选"""
    try:
        level = request.query.get("level", "").upper()
        logger_name = request.query.get("logger", "")
        limit = int(request.query.get("limit", 200))

        logs = list(_log_buffer)
        if level and level in _LOG_LEVELS:
            min_level = _LOG_LEVELS[level]
            logs = [l for l in logs if _LOG_LEVELS.get(l["level"], 0) >= min_level]
        if logger_name:
            logs = [l for l in logs if logger_name.lower() in l["logger"].lower()]
        if limit > 0:
            logs = logs[-limit:]

        return web.json_response({"logs": logs, "total": len(_log_buffer)})
    except Exception as e:
        logger.error(f"get_logs 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def get_features(request: web.Request) -> web.Response:
    """GET /api/features - 查询各功能状态"""
    try:
        import main as app_main

        # 获取功能可用性标志（从 main.py 安全导入）
        try:
            live2d_avail = bool(app_main.live2d_available)
        except AttributeError:
            live2d_avail = False
        try:
            tts_avail = bool(app_main.tts_available)
        except AttributeError:
            tts_avail = False

        # Live2D 连接状态
        live2d_connected = False
        live2d_status = "not_enabled"
        if app_main.args and app_main.args.live2d:
            if app_main.live2d_manager is not None and app_main.live2d_manager.is_connected:
                live2d_connected = True
                live2d_status = "connected"
            else:
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
        logger.debug(f"get_features: live2d.enabled={features['live2d']['enabled']}, live2d.connected={features['live2d']['connected']}, live2d_avail={live2d_avail}")
        return web.json_response(features)
    except Exception as e:
        logger.error(f"get_features 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def start_feature(request: web.Request) -> web.Response:
    """POST /api/features/{name}/start - 启动指定功能"""
    try:
        feature_name = request.match_info["name"]
        import main as app_main

        features = {
            "live2d": {"enabled": bool(app_main.args and app_main.args.live2d)},
            "tts": {"enabled": bool(app_main.args and app_main.args.tts)},
            "danmaku_listener": {"enabled": bool(app_main._bili_process and app_main._bili_process.poll() is None)},
        }

        if feature_name not in features:
            return web.json_response({"error": f"未知功能: {feature_name}"}, status=400)

        # 启动功能
        async def start():
            if feature_name == "live2d":
                if not (app_main.args and app_main.args.live2d):
                    await app_main.enable_live2d()
            elif feature_name == "tts":
                if not (app_main.args and app_main.args.tts):
                    await app_main.enable_tts()
            elif feature_name == "danmaku_listener":
                if not (app_main._bili_process and app_main._bili_process.poll() is None):
                    await app_main.start_danmaku_listener()

        asyncio.create_task(start())
        return web.json_response({"success": True, "message": f"正在启动 {feature_name}..."})
    except Exception as e:
        logger.error(f"start_feature 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def stop_feature(request: web.Request) -> web.Response:
    """POST /api/features/{name}/stop - 停止指定功能"""
    try:
        feature_name = request.match_info["name"]
        import main as app_main

        if feature_name not in ("live2d", "tts", "danmaku_listener"):
            return web.json_response({"error": f"未知功能: {feature_name}"}, status=400)

        async def stop():
            if feature_name == "live2d":
                await app_main.disable_live2d()
            elif feature_name == "tts":
                await app_main.disable_tts()
            elif feature_name == "danmaku_listener":
                await app_main.stop_danmaku_listener()

        asyncio.create_task(stop())
        return web.json_response({"success": True, "message": f"正在停止 {feature_name}..."})
    except Exception as e:
        logger.error(f"stop_feature 错误: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def serve_index(request: web.Request) -> web.Response:
    """GET / - 返回控制台首页"""
    try:
        index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
        if not os.path.exists(index_path):
            return web.Response(text="<h1>Web UI not found</h1>", content_type="text/html")

        with open(index_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        return web.Response(text=html_content, content_type="text/html")
    except Exception as e:
        logger.error(f"serve_index 错误: {e}")
        return web.Response(text=f"<h1>Error: {e}</h1>", content_type="text/html", status=500)


def setup_api_routes(app: web.Application):
    """
    注册所有 API 路由到 aiohttp app

    在 main.py 的 start_http_server 中调用。
    """
    app.add_routes([
        # 控制台首页
        web.get('/', serve_index),
        # API 路由
        web.get('/api/status', get_status),
        web.get('/api/config', get_config),
        web.post('/api/config', update_config),
        web.get('/api/messages', get_messages),
        web.post('/api/send', send_test_message),
        web.post('/api/vision', trigger_vision),
        web.get('/api/windows', list_windows),
        # 日志
        web.get('/api/logs', get_logs),
        # 功能控制
        web.get('/api/features', get_features),
        web.post('/api/features/{name}/start', start_feature),
        web.post('/api/features/{name}/stop', stop_feature),
    ])

    logger.info("API 路由已注册")