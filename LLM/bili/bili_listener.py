#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili 能力域 · blivedm 监听封装（由 broadcast/bili_main.py 迁移）。

阶段0 目标：把 B 站监听搬进扩展，但**仍走旧 HTTP 回流**（forward=True 时
把消息 HTTP POST 到主程序 8081），主播脑暂不动；同时维护最近消息缓冲供
bili_observe 查看。

迁移保留：blivedm 连接、指数退避重连、历史弹幕过滤、去重、OpenAI 格式转换。
新增：状态查询（status）、最近消息缓冲（recent）。
"""

import asyncio
import http.cookies
import json
import logging
import os
import sys
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import aiohttp

# blivedm 是 broadcast/ 下的本地包，需显式加入 sys.path（脚本不在 broadcast 目录运行）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BROADCAST_DIR = os.path.join(_PROJECT_ROOT, "broadcast")
if _BROADCAST_DIR not in sys.path:
    sys.path.insert(0, _BROADCAST_DIR)

try:
    import blivedm
    import blivedm.models.web as web_models
except ImportError:  # pragma: no cover
    blivedm = None
    web_models = None

logger = logging.getLogger("bili_listener")
logging.getLogger("blivedm").setLevel(logging.WARNING)

# 项目根与 config.json（broadcast/ 目录，与旧 bili_main 共用）
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "broadcast", "config.json")

_HISTORY_GRACE_SECONDS = 5
_PROCESSED_MAX = 5000


def _resolve_sessdata(config_sessdata: str) -> str:
    """多来源解析 SESSDATA（config.json > 环境变量 > config.ini），与 bili_main 一致。"""
    if config_sessdata:
        return config_sessdata
    for var in ("bilibli_SESSDATA", "BILIBILI_SESSDATA", "SESSDATA"):
        v = os.getenv(var, "").strip()
        if v:
            return v
    try:
        import configparser
        ini = configparser.ConfigParser()
        ini.read(os.path.join(_PROJECT_ROOT, "config.ini"), encoding="utf-8")
        if ini.has_section("bilibili"):
            return ini.get("bilibili", "sessdata", fallback="").strip()
    except Exception:
        pass
    return ""


def _load_config() -> Dict:
    """读 broadcast/config.json（与旧 bili_main 一致，兼容现有 cookie/房间配置）。"""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        room_raw = cfg.get("ROOM_IDS", "71001")
        if isinstance(room_raw, (list, tuple)):
            rooms = [int(str(x).strip()) for x in room_raw if str(x).strip()]
        else:
            rooms = [int(x.strip()) for x in str(room_raw).split(",") if x.strip()]
        return {"room_ids": rooms,
                "sessdata": _resolve_sessdata(str(cfg.get("SESSDATA", "") or "").strip()),
                "output_port": int(cfg.get("output_port", 8081))}
    except Exception:
        return {"room_ids": [71001], "sessdata": "", "output_port": 8081}


class RealTimeDanmakuHandler(blivedm.BaseHandler):
    """实时弹幕处理器：历史过滤 + 去重 + 缓冲 + 转发。"""

    def __init__(self, on_message):
        self.on_message = on_message          # async (openai_msg, raw) -> None
        self.connect_time = time.time()

    def _is_history_message(self, timestamp: float) -> bool:
        return float(timestamp) < self.connect_time - _HISTORY_GRACE_SECONDS

    @staticmethod
    def _dedup_key(room_id, mtype, uid, ts, content) -> str:
        return f"{room_id}|{mtype}|{uid}|{int(ts)}|{content[:40]}"

    def _handle(self, raw: Dict, content: str):
        if self._is_history_message(raw.get("timestamp", 0)):
            return
        if not _mark_processed(self._dedup_key(
                raw.get("room_id", 0), raw.get("type", "?"),
                raw.get("user", {}).get("uid", 0),
                float(raw.get("timestamp", 0)), content)):
            return
        openai_msg = convert_to_openai_message(raw)
        asyncio.create_task(self.on_message(openai_msg, raw))

    def _on_danmaku(self, client, message: web_models.DanmakuMessage):
        raw = {"type": "danmaku", "room_id": client.room_id,
               "user": {"uid": message.uid, "uname": message.uname},
               "content": message.msg, "timestamp": message.timestamp}
        self._handle(raw, message.msg)

    def _on_gift(self, client, message: web_models.GiftMessage):
        raw = {"type": "gift", "room_id": client.room_id,
               "user": {"uid": message.uid, "uname": message.uname},
               "gift": {"name": message.gift_name, "num": message.num,
                        "price": message.price},
               "timestamp": message.timestamp}
        self._handle(raw, f"{message.gift_name}x{message.num}")

    def _on_super_chat(self, client, message: web_models.SuperChatMessage):
        raw = {"type": "super_chat", "room_id": client.room_id,
               "user": {"uid": message.uid, "uname": message.uname},
               "message": message.message, "price": message.price,
               "timestamp": message.start_time}
        self._handle(raw, message.message)

    def _on_unknown_command(self, client, command):
        pass

    def _on_heartbeat(self, client, message):
        pass


def convert_to_openai_message(message_data: Dict[str, Any]) -> Dict[str, Any]:
    """弹幕/礼物/SC → OpenAI message 格式（与 bili_main 一致）。"""
    mtype = message_data.get("type", "danmaku")
    uname = message_data.get("user", {}).get("uname", "未知用户")
    if mtype == "danmaku":
        content = message_data.get("content", "")
    elif mtype == "gift":
        g = message_data.get("gift", {})
        content = f"赠送了{g.get('num', 1)}个{g.get('name', '')}"
    elif mtype == "super_chat":
        content = f"[{message_data.get('price', 0)}元SC] {message_data.get('message', '')}"
    else:
        content = message_data.get("content", "")
    return {"role": "user", "content": content, "name": uname,
            "important": mtype in ("gift", "super_chat")}


# 去重集合（模块级，与旧实现一致）
_processed_ids: set = set()


def _mark_processed(key: str) -> bool:
    if key in _processed_ids:
        return False
    _processed_ids.add(key)
    if len(_processed_ids) > _PROCESSED_MAX:
        half = _PROCESSED_MAX // 2
        for k in sorted(_processed_ids)[:half]:
            _processed_ids.discard(k)
    return True


class BiliListener:
    """blivedm 监听器：可 start/stop/status/recent。

    - forward=True（阶段0 默认）：消息继续 HTTP POST 到主程序 8081（旧链路，主播脑不动）；
    - 无论是否 forward，都写入 recent 缓冲供 bili_observe 查看。
    """

    def __init__(self, room_ids: Optional[List[int]] = None, sessdata: str = "",
                 output_port: int = 8081, forward: bool = True,
                 buffer_size: int = 200):
        cfg = _load_config()
        self.room_ids = room_ids or cfg["room_ids"]
        self.sessdata = sessdata or cfg["sessdata"]
        self.output_port = output_port or cfg["output_port"]
        self.forward = forward
        self._forward_url = f"http://127.0.0.1:{self.output_port}/"
        self.recent: Deque[Dict] = deque(maxlen=buffer_size)
        self._task: Optional[asyncio.Task] = None
        self._http: Optional[aiohttp.ClientSession] = None
        self.started_at: Optional[float] = None
        self.last_error: str = ""
        self.clients: list = []

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> Dict:
        return {"success": True, "running": self.running,
                "room_ids": self.room_ids, "forward_url": self._forward_url,
                "forward": self.forward, "started_at": self.started_at,
                "last_error": self.last_error, "buffer": len(self.recent),
                "sessdata_ready": bool(self.sessdata)}

    def recent_messages(self, limit: int = 20) -> List[Dict]:
        items = list(self.recent)[-limit:]
        return [{"type": m.get("openai", {}).get("important") and "important" or m["type"],
                 "user": m.get("user"), "content": m.get("content"),
                 "ts": m.get("ts")} for m in items]

    async def _on_message(self, openai_msg: Dict, raw: Dict) -> None:
        self.recent.append({"type": raw.get("type"), "user": raw.get("user", {}).get("uname"),
                            "content": openai_msg.get("content", ""),
                            "ts": time.time(), "openai": openai_msg})
        if self.forward:
            await self._forward(openai_msg)

    async def _forward(self, openai_msg: Dict) -> None:
        try:
            if self._http is None or self._http.closed:
                self._http = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10))
            async with self._http.post(self._forward_url, json=openai_msg) as resp:
                if resp.status >= 300:
                    self.last_error = f"转发非2xx: {resp.status}"
                    logger.error(self.last_error)
        except Exception as e:
            self.last_error = f"转发失败: {e}"
            logger.error(self.last_error)

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            session = self._init_session()
            handler = RealTimeDanmakuHandler(self._on_message)
            self.clients = [blivedm.BLiveClient(rid, session=session)
                            for rid in self.room_ids]
            for c in self.clients:
                c.set_handler(handler)
            try:
                for c in self.clients:
                    c.start()
                logger.info(f"已启动 {len(self.clients)} 个监听客户端: {self.room_ids}")
                backoff = 1.0
                await asyncio.gather(*(c.join() for c in self.clients),
                                     return_exceptions=True)
            except asyncio.CancelledError:
                logger.info("监听取消")
                break
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"监听异常: {e}")
            finally:
                for c in self.clients:
                    try:
                        c.stop()
                    except Exception:
                        pass
                try:
                    await session.close()
                except Exception:
                    pass
            # 指数退避后重连
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _init_session(self) -> aiohttp.ClientSession:
        cookies = http.cookies.SimpleCookie()
        if self.sessdata:
            cookies["SESSDATA"] = self.sessdata
            cookies["SESSDATA"]["domain"] = "bilibili.com"
        session = aiohttp.ClientSession()
        if self.sessdata:
            session.cookie_jar.update_cookies(cookies)
        return session

    async def start(self) -> Dict:
        if self.running:
            return {"success": True, "note": "已在运行", **self.status()}
        if blivedm is None:
            return {"success": False, "error": "缺 blivedm 依赖"}
        self.started_at = time.time()
        self.last_error = ""
        self._task = asyncio.create_task(self._run())
        return {"success": True, **self.status()}

    async def stop(self) -> Dict:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.started_at = None
        if self._http and not self._http.closed:
            await self._http.close()
            self._http = None
        return {"success": True, "running": False}
