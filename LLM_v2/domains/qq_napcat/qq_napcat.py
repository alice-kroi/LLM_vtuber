# ============================================================
# LLM_v2/domains/qq_napcat/qq_napcat.py — QQ（NapCat / OneBot 11）
# ============================================================
# Transceiver：
#   - 入站：NapCat 通过反向 WebSocket 推来 OneBot 11 事件 → emit USER_MESSAGE
#   - 出站：监听 TTS_SPEAK，把 source=qq 的回复发回对应私聊/群
# 只做协议适配，不参与人格/总结/工具决策（三脑分工不变）。
# ============================================================

from __future__ import annotations

import asyncio
import configparser
import json
import logging
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from LLM_v2.core.capability_domain import Transceiver, CapabilityType
from LLM_v2.domains.qq_napcat.qq_napcat_tools import (
    QQ_NAPCAT_TOOL_NAMES,
    QQ_NAPCAT_TOOLS_SCHEMA,
)
from LLM_v2.events import EVENTS

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _id_set(value: str) -> Set[str]:
    return {x.strip() for x in str(value or "").replace("，", ",").split(",") if x.strip()}


class QQNapCatDomain(Transceiver):
    """QQ（NapCat）能力域：OneBot 11 反向 WebSocket 适配"""

    id = "qq_napcat"
    label = "QQ（NapCat）"
    prefix = "qq_"
    default_enabled = True
    type = CapabilityType.TRANSCEIVER

    call_timeout = 20.0
    tool_timeouts = {
        "qq_status": 5.0,
        "qq_send": 20.0,
        "qq_recent": 5.0,
        "qq_profile_set": 20.0,
        "qq_avatar_set": 30.0,
        "qq_avatar_from_web": 75.0,
        "qq_profile_background_prepare": 90.0,
        "qq_zone_post": 30.0,
    }

    def __init__(self):
        super().__init__()
        cfg = self._load_config()
        self._host = cfg["host"]
        self._port = cfg["port"]
        self._token = cfg["token"]
        self._allow_all = cfg["allow_all"]
        self._allow_users = cfg["allow_users"]
        self._allow_groups = cfg["allow_groups"]
        self._bot_qq = cfg["bot_qq"]
        self._reply_only_when_mentioned = cfg["reply_only_when_mentioned"]
        self._max_text = cfg["max_text"]

        self.bus = None
        self._server_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._ws = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._seq = 0
        self._self_id = str(self._bot_qq or "")
        self._connected_at = 0.0
        self._last_target: Optional[Tuple[str, str, str]] = None
        self._recent: List[Dict[str, Any]] = []
        self._seen: Dict[str, float] = {}
        self._sent_recent: Dict[Tuple[str, str], float] = {}
        self._ops_path = _ROOT / "persona" / "qq_ops.json"
        self._ops: List[Dict[str, Any]] = self._load_ops()
        self._last_zone_post: Tuple[str, float] = ("", 0.0)

    # ---------- 配置 ----------

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        cfg = {
            "host": "127.0.0.1",
            "port": 6700,
            "token": "",
            "allow_all": False,
            "allow_users": set(),
            "allow_groups": set(),
            "bot_qq": 0,
            "reply_only_when_mentioned": True,
            "max_text": 1500,
        }
        try:
            cp = configparser.ConfigParser()
            cp.read(_ROOT / "config.ini", encoding="utf-8")
            if cp.has_section("qq"):
                sec = cp["qq"]
                cfg["host"] = sec.get("host", cfg["host"])
                cfg["port"] = int(sec.get("port", cfg["port"]))
                cfg["token"] = sec.get("token", cfg["token"])
                cfg["allow_all"] = _as_bool(sec.get("allow_all"), False)
                cfg["allow_users"] = _id_set(sec.get("allow_users", ""))
                cfg["allow_groups"] = _id_set(sec.get("allow_groups", ""))
                cfg["bot_qq"] = int(sec.get("bot_qq", 0) or 0)
                cfg["reply_only_when_mentioned"] = _as_bool(
                    sec.get("reply_only_when_mentioned"), True)
                cfg["max_text"] = int(sec.get("max_text", 1500) or 1500)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qq_napcat] 读取 config.ini 失败，用默认值: {e}")

        # 环境变量优先
        cfg["host"] = os.getenv("QQ_ONEBOT_HOST", cfg["host"])
        try:
            cfg["port"] = int(os.getenv("QQ_ONEBOT_PORT", cfg["port"]))
        except ValueError:
            pass
        cfg["token"] = os.getenv("QQ_ONEBOT_TOKEN", cfg["token"])
        cfg["allow_all"] = _as_bool(os.getenv("QQ_ALLOW_ALL"), cfg["allow_all"])
        env_users = os.getenv("QQ_ALLOW_USERS")
        if env_users is not None:
            cfg["allow_users"] = _id_set(env_users)
        env_groups = os.getenv("QQ_ALLOW_GROUPS")
        if env_groups is not None:
            cfg["allow_groups"] = _id_set(env_groups)
        try:
            cfg["bot_qq"] = int(os.getenv("QQ_BOT_QQ", cfg["bot_qq"]) or 0)
        except ValueError:
            pass
        cfg["reply_only_when_mentioned"] = _as_bool(
            os.getenv("QQ_REPLY_ONLY_MENTIONED"), cfg["reply_only_when_mentioned"])
        try:
            cfg["max_text"] = int(os.getenv("QQ_MAX_TEXT", cfg["max_text"]))
        except ValueError:
            pass
        return cfg

    # ---------- 域契约 ----------

    def allowed_names(self) -> List[str]:
        return list(QQ_NAPCAT_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in QQ_NAPCAT_TOOLS_SCHEMA
                if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return (
            "【QQ（NapCat）】通过 OneBot 11 接入 QQ。"
            "qq_status 查看连接/白名单/最近操作记录；qq_send 主动发消息；qq_recent 看最近消息；"
            "qq_profile_set 改昵称/签名；qq_avatar_set 换头像（本地图片或链接）；"
            "qq_avatar_from_web 从网上搜图并换头像；"
            "qq_profile_background_prepare 为资料卡背景找图（NapCat 无设置背景接口，只能准备图）；"
            "qq_zone_post 发说说（固定仅自己可见，5 分钟内同内容自动拦截）。"
            "QQ 入站消息以 source=qq 进入 USER_MESSAGE，回复由主播脑的 TTS_SPEAK 自动发回，"
            "本域不负责总结内容。"
        )

    def inbound_event_types(self) -> List[str]:
        return [EVENTS.USER_MESSAGE]

    def outbound_event_types(self) -> List[str]:
        return [EVENTS.TTS_SPEAK]

    def preflight(self) -> Optional[str]:
        try:
            import websockets  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return f"websockets 未安装（qq_napcat 需要）: {e}"
        return None

    def central_card(self) -> Dict[str, Any]:
        connected = self._ws is not None
        summary = (f"QQ（NapCat）：{'已连接' if connected else '未连接'}；"
                   f"机器人 {self._self_id or '未知'}；最近 {len(self._recent)} 条消息")
        return {"summary": summary}

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        if self.bus is not None:
            self.bus.on(EVENTS.TTS_SPEAK, self._on_tts_speak)
        self._stop_event = asyncio.Event()
        self._server_task = asyncio.create_task(
            self._serve(), name="qq_napcat_ws_server")
        logger.info(
            f"[qq_napcat] 启动反向 WS 服务: ws://{self._host}:{self._port} "
            f"(allow_all={self._allow_all})")

    async def stop(self) -> None:
        if self.bus is not None:
            try:
                self.bus.off(EVENTS.TTS_SPEAK, self._on_tts_speak)
            except Exception:  # noqa: BLE001
                pass
        if self._stop_event is not None:
            self._stop_event.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._server_task is not None:
            self._server_task.cancel()
            try:
                await self._server_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._server_task = None
        self._ws = None
        logger.info("[qq_napcat] 已停止")

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve
        while not self._stop_event.is_set():
            try:
                async with serve(
                    self._handler, self._host, self._port,
                    ping_interval=20, ping_timeout=20, max_size=2 ** 20,
                ) as server:
                    self._server = server
                    logger.info(f"[qq_napcat] 监听 ws://{self._host}:{self._port}")
                    await self._stop_event.wait()
            except asyncio.CancelledError:
                break
            except OSError as e:
                logger.warning(f"[qq_napcat] 端口 {self._port} 启动失败: {e}；15s 后重试")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[qq_napcat] WS 服务异常: {e}")
                await asyncio.sleep(5)

    # ---------- 连接 / 鉴权 ----------

    def _check_auth(self, websocket) -> bool:
        if not self._token:
            return True
        given = ""
        try:
            headers = websocket.request.headers
            auth = headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                given = auth[7:].strip()
            if not given:
                path = websocket.request.path or ""
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
                given = (qs.get("access_token") or qs.get("token") or [""])[0]
        except Exception:  # noqa: BLE001
            return False
        return given == self._token

    async def _handler(self, websocket) -> None:
        if not self._check_auth(websocket):
            logger.warning("[qq_napcat] 拒绝未授权连接")
            await websocket.close(code=1008, reason="invalid token")
            return
        self._ws = websocket
        self._connected_at = time.time()
        logger.info(f"[qq_napcat] NapCat 已连接: {websocket.remote_address}")
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(msg, dict):
                    continue
                if "echo" in msg:
                    fut = self._pending.pop(str(msg.get("echo")), None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                    continue
                try:
                    await self._handle_event(msg)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[qq_napcat] 处理事件失败: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qq_napcat] 连接异常: {e}")
        finally:
            if self._ws is websocket:
                self._ws = None
            logger.info("[qq_napcat] NapCat 连接已断开")

    # ---------- 入站 ----------

    async def _handle_event(self, evt: Dict[str, Any]) -> None:
        self_id = str(evt.get("self_id") or "")
        if self_id:
            self._self_id = self_id

        post_type = evt.get("post_type")
        if post_type == "meta_event":
            if evt.get("meta_event_type") == "lifecycle":
                logger.info(
                    f"[qq_napcat] 生命周期: {evt.get('sub_type')} "
                    f"self_id={self._self_id}")
            return
        if post_type != "message":
            return

        user_id = str(evt.get("user_id") or "")
        if user_id and user_id == (self_id or self._self_id):
            return
        chat_type = str(evt.get("message_type") or "")
        if chat_type not in ("private", "group"):
            return

        group_id = str(evt.get("group_id") or "")
        target_id = group_id if chat_type == "group" else user_id
        if chat_type == "private":
            if not (self._allow_all or user_id in self._allow_users):
                logger.info(f"[qq_napcat] 私聊 {user_id} 不在白名单，已忽略")
                return
        else:
            if not (self._allow_all or group_id in self._allow_groups):
                logger.info(f"[qq_napcat] 群 {group_id} 不在白名单，已忽略")
                return
            if self._reply_only_when_mentioned and not self._is_mentioned(
                    evt, self._self_id or str(self._bot_qq)):
                return

        message_id = str(evt.get("message_id") or "")
        now = time.time()
        if message_id:
            last = self._seen.get(message_id)
            if last is not None and now - last < 120:
                return
            self._seen[message_id] = now
            if len(self._seen) > 500:
                cutoff = now - 300
                self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}

        text = self._extract_text(evt.get("message"))
        if not text:
            return

        sender = evt.get("sender") or {}
        user_name = str(sender.get("card") or sender.get("nickname")
                        or user_id or "unknown")
        chat_key = f"qq:{chat_type}:{target_id}"
        self._last_target = (chat_type, target_id, chat_key)
        self._recent.append({
            "chat_key": chat_key,
            "chat_type": chat_type,
            "user": user_name,
            "user_id": user_id,
            "group_id": group_id or None,
            "text": text[:500],
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._recent = self._recent[-50:]

        payload = {
            "text": text,
            "source": "qq",
            "user": user_name,
            "timestamp": now,
            "metadata": {
                "chat_type": chat_type,
                "chat_key": chat_key,
                "user_id": user_id,
                "group_id": group_id or None,
                "message_id": message_id,
                "self_id": self._self_id,
            },
        }
        if self.bus is not None:
            await self.bus.emit(EVENTS.USER_MESSAGE, payload)
        logger.info(f"[qq_napcat] 收到 {chat_key} {user_name}: {text[:60]}")

    @staticmethod
    def _extract_text(message: Any) -> str:
        if isinstance(message, str):
            return message.strip()
        if not isinstance(message, list):
            return ""
        parts: List[str] = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type")
            data = seg.get("data") or {}
            if seg_type == "text":
                parts.append(str(data.get("text") or ""))
            elif seg_type == "at":
                parts.append(f"@{data.get('name') or data.get('qq') or ''}")
            elif seg_type == "image":
                parts.append("[图片]")
            elif seg_type == "face":
                parts.append("[表情]")
            elif seg_type == "record":
                parts.append("[语音]")
        return "".join(parts).strip()

    @staticmethod
    def _is_mentioned(evt: Dict[str, Any], bot_qq: str) -> bool:
        message = evt.get("message")
        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    qq = str((seg.get("data") or {}).get("qq") or "")
                    if qq and qq == bot_qq:
                        return True
            return False
        raw = str(evt.get("raw_message") or "")
        return f"[CQ:at,qq={bot_qq}]" in raw

    # ---------- 出站 ----------

    async def _on_tts_speak(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        source = str(payload.get("source") or "")
        chat_key = str(payload.get("chat_key") or "")

        target: Optional[Tuple[str, str, str]] = None
        parsed = self._parse_chat_key(chat_key) if chat_key else None
        if parsed:
            target = (parsed[0], parsed[1], chat_key)
        elif source.startswith("will_engine_qq"):
            target = self._last_target
        if target is None:
            return

        key = (target[2], text[:80])
        now = time.time()
        if now - self._sent_recent.get(key, 0.0) < 5.0:
            return
        self._sent_recent[key] = now
        if len(self._sent_recent) > 200:
            self._sent_recent = {
                k: v for k, v in self._sent_recent.items() if now - v < 300}

        await self._send_text(target[0], target[1], text)

    @staticmethod
    def _parse_chat_key(chat_key: str) -> Optional[Tuple[str, str]]:
        parts = str(chat_key or "").split(":")
        if len(parts) != 3 or parts[0] != "qq":
            return None
        if parts[1] not in ("private", "group"):
            return None
        return parts[1], parts[2]

    async def _send_text(self, chat_type: str, target: str, text: str) -> Dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            return {"success": False, "error": "空消息"}
        chat_type = "group" if chat_type == "group" else "private"
        action = "send_group_msg" if chat_type == "group" else "send_private_msg"
        chunks = [text[i:i + self._max_text]
                  for i in range(0, len(text), self._max_text)]
        last: Dict[str, Any] = {"success": False, "error": "未发送"}
        for idx, chunk in enumerate(chunks):
            params = {
                "message": [{"type": "text", "data": {"text": chunk}}]
            }
            if chat_type == "group":
                params["group_id"] = int(target)
            else:
                params["user_id"] = int(target)
            last = await self._api(action, params)
            if not last.get("success"):
                return last
            if idx + 1 < len(chunks):
                await asyncio.sleep(0.3)
        logger.info(f"[qq_napcat] 已回复 {chat_type}:{target}")
        return last

    async def _api(self, action: str, params: Dict[str, Any],
                   timeout: float = 15.0) -> Dict[str, Any]:
        ws = self._ws
        if ws is None:
            return {"success": False, "error": "NapCat 未连接"}
        self._seq += 1
        echo = f"lvq_{int(time.time() * 1000)}_{self._seq}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        try:
            await ws.send(json.dumps(
                {"action": action, "params": params, "echo": echo},
                ensure_ascii=False))
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            self._pending.pop(echo, None)
            return {"success": False, "error": str(e)}
        ok = resp.get("status") == "ok" or resp.get("retcode") == 0
        return {
            "success": bool(ok),
            "retcode": resp.get("retcode"),
            "data": resp.get("data"),
            "error": None if ok else (
                resp.get("message") or resp.get("wording") or "OneBot API 失败"),
        }

    # ---------- 工具 ----------

    async def qq_status(self) -> Dict[str, Any]:
        return {
            "success": True,
            "connected": self._ws is not None,
            "self_id": self._self_id,
            "listen": f"ws://{self._host}:{self._port}",
            "connected_at": (time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self._connected_at))
                if self._connected_at else ""),
            "allow_all": self._allow_all,
            "allow_users": sorted(self._allow_users),
            "allow_groups": sorted(self._allow_groups),
            "reply_only_when_mentioned": self._reply_only_when_mentioned,
            "recent_count": len(self._recent),
            "last_target": self._last_target[2] if self._last_target else "",
            "recent_ops": self._ops[-10:],
        }

    def _record_op(self, action: str, success: bool, detail: str = "") -> None:
        """记录本域写操作并落盘，供“我到底做过几次”这类追问跨重启核对。"""
        self._ops.append({
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "success": bool(success),
            "detail": str(detail)[:160],
        })
        self._ops = self._ops[-50:]
        try:
            self._ops_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ops_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._ops, ensure_ascii=False, indent=2),
                encoding="utf-8")
            tmp.replace(self._ops_path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qq_napcat] 操作记录落盘失败: {e}")

    def _load_ops(self) -> List[Dict[str, Any]]:
        try:
            if self._ops_path.is_file():
                data = json.loads(self._ops_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)][-50:]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qq_napcat] 操作记录读取失败: {e}")
        return []

    async def qq_send(self, chat_type: str = "", target: str = "",
                      text: str = "") -> Dict[str, Any]:
        chat_type = str(chat_type or "").strip().lower()
        target = str(target or "").strip()
        if chat_type not in ("private", "group"):
            return {"success": False, "error": "chat_type 必须是 private 或 group"}
        if not target:
            return {"success": False, "error": "缺少 target"}
        if not target.isdigit():
            return {"success": False, "error": "target 必须是数字 QQ 号或群号"}
        if not str(text or "").strip():
            return {"success": False, "error": "缺少 text"}
        result = await self._send_text(chat_type, target, text)
        return {"success": bool(result.get("success")),
                "error": result.get("error"),
                "retcode": result.get("retcode"),
                "data": result.get("data")}

    async def qq_recent(self, limit: int = 10) -> Dict[str, Any]:
        limit = max(1, min(50, int(limit or 10)))
        return {"success": True, "count": min(limit, len(self._recent)),
                "messages": self._recent[-limit:][::-1]}

    async def qq_profile_set(self, nickname: str = "", personal_note: str = "",
                             sex: Optional[int] = None) -> Dict[str, Any]:
        nickname = str(nickname or "").strip()
        note = str(personal_note or "").strip()
        if not nickname:
            if not note:
                return {"success": False, "error": "nickname 和 personal_note 至少提供一个"}
            # 只改签名：走 set_self_longnick，避免误改昵称
            result = await self._api("set_self_longnick", {"longNick": note})
            ok = bool(result.get("success"))
            self._record_op("profile_set", ok, f"personal_note={note[:40]}")
            return {
                "success": ok,
                "error": result.get("error"),
                "retcode": result.get("retcode"),
                "nickname": "",
                "personal_note": note,
            }
        params: Dict[str, Any] = {"nickname": nickname}
        if note:
            params["personal_note"] = note
        if sex is not None:
            params["sex"] = int(sex)
        result = await self._api("set_qq_profile", params)
        ok = bool(result.get("success"))
        self._record_op(
            "profile_set", ok,
            f"nickname={nickname}; note={note[:40]}")
        return {
            "success": ok,
            "error": result.get("error"),
            "retcode": result.get("retcode"),
            "nickname": nickname,
            "personal_note": note,
        }

    async def qq_avatar_set(self, file: str = "") -> Dict[str, Any]:
        ref = self._image_ref(file)
        if not ref:
            return {"success": False,
                    "error": "头像文件不存在、超过 2MB 或格式不支持"}
        result = await self._api("set_qq_avatar", {"file": ref}, timeout=25.0)
        ok = bool(result.get("success"))
        self._record_op("avatar_set", ok, f"file={str(file)[:80]}")
        return {
            "success": ok,
            "error": result.get("error"),
            "retcode": result.get("retcode"),
            "file": str(file),
        }

    async def qq_avatar_from_web(self, query: str = "",
                                 index: int = 0) -> Dict[str, Any]:
        """搜图 → 下载 → 设为头像（用户说“自己从网上找张图当头像”时用）。"""
        query = str(query or "").strip()
        if not query:
            return {"success": False, "error": "缺少搜索关键词 query"}
        # 维尔薇的图已有本地缓存时直接用，避免搜图接口偶发超时
        if "维尔薇" in query or "vill-v" in query.lower():
            cached = self._latest_cached_image()
            if cached:
                result = await self._api(
                    "set_qq_avatar", {"file": self._image_ref(cached)},
                    timeout=25.0)
                ok = bool(result.get("success"))
                self._record_op(
                    "avatar_from_web", ok, f"{query} ← 本地缓存")
                return {
                    "success": ok,
                    "error": result.get("error"),
                    "retcode": result.get("retcode"),
                    "query": query,
                    "source_url": "",
                    "file": cached,
                    "cached": True,
                }
        try:
            urls = await asyncio.to_thread(self._search_image_urls, query)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"搜图失败: {e}"}
        if not urls:
            return {"success": False, "error": f"没搜到可用图片: {query}"}

        idx = max(0, min(len(urls) - 1, int(index or 0)))
        candidates = urls[idx:idx + 3]
        last_error = ""
        for url in candidates:
            try:
                path = await asyncio.to_thread(self._download_image, url)
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                continue
            if not path:
                last_error = "下载到的不是有效图片或超过 2MB"
                continue
            result = await self._api(
                "set_qq_avatar", {"file": self._image_ref(str(path))},
                timeout=25.0)
            ok = bool(result.get("success"))
            self._record_op(
                "avatar_from_web", ok,
                f"{query} ← {url[:80]}")
            return {
                "success": ok,
                "error": result.get("error"),
                "retcode": result.get("retcode"),
                "query": query,
                "source_url": url,
                "file": str(path),
            }
        return {"success": False,
                "error": f"图片都不可用: {last_error or '下载失败'}",
                "query": query}

    async def qq_profile_background_prepare(
            self, query: str = "维尔薇 崩坏3") -> Dict[str, Any]:
        """找一张适合做资料卡背景的图；NapCat 无法自动设置，只能准备素材。"""
        query = str(query or "").strip() or "维尔薇 崩坏3"
        # 维尔薇已有本地缓存时直接用，避免萌娘百科偶发超时拖垮整次请求
        if "维尔薇" in query or "vill-v" in query.lower():
            cached = self._latest_cached_image()
            if cached:
                self._record_op(
                    "background_prepare", True, f"{query} ← 本地缓存")
                return {
                    "success": True,
                    "query": query,
                    "file": cached,
                    "source_url": "",
                    "cached": True,
                    "note": ("使用本地已下载的维尔薇图片。"
                             "NapCat/OneBot 没有修改资料卡背景的接口，无法自动设置。"
                             "手机QQ：左上角头像 → 我的个性装扮 → 名片 → 自定义封面/背景；"
                             "PC QQ：点自己头像打开资料卡 → 右上角换肤。"),
                }
        try:
            urls = await asyncio.to_thread(self._search_image_urls, query)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"搜图失败: {e}", "query": query}
        if not urls:
            return {"success": False, "error": f"没搜到可用图片: {query}",
                    "query": query}

        last_error = ""
        for url in urls[:3]:
            try:
                path = await asyncio.to_thread(self._download_image, url)
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                continue
            if not path:
                last_error = "下载到的不是有效图片或超过 2MB"
                continue
            self._record_op(
                "background_prepare", True, f"{query} ← {url[:80]}")
            return {
                "success": True,
                "query": query,
                "file": path,
                "source_url": url,
                "note": ("NapCat/OneBot 没有修改资料卡背景的接口，无法自动设置。"
                         "手机QQ：左上角头像 → 我的个性装扮 → 名片 → 自定义封面/背景；"
                         "PC QQ：点自己头像打开资料卡 → 右上角换肤。"),
            }
        return {"success": False,
                "error": f"图片都不可用: {last_error or '下载失败'}",
                "query": query}

    # ---------- 联网搜图辅助 ----------

    @staticmethod
    def _search_image_urls(query: str) -> List[str]:
        """萌娘百科优先（二次元角色最稳），Bing 图片兜底。"""
        import re as _re
        from urllib.parse import quote

        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Accept-Encoding": "identity",
        }
        found: List[str] = []

        def _add(url: Any) -> None:
            if not isinstance(url, str):
                return
            u = url.strip().replace("\\/", "/")
            if not u.lower().startswith(("http://", "https://")):
                return
            if u not in found:
                found.append(u)

        # 1) 萌娘百科：角色页主图（最可靠）
        raw_query = (query or "").strip()
        raw_candidates: List[str] = []
        if raw_query:
            raw_candidates.append(raw_query)
            raw_candidates += [
                t for t in _re.split(r"[\s,，。;；/|]+", raw_query) if t
            ]
            raw_candidates += _re.findall(r"[\u4e00-\u9fff]{2,8}", raw_query)
        generic = {
            "高清", "简约", "背景", "壁纸", "图片", "适合", "资料卡", "封面",
            "相关", "好看", "竖版", "横版", "崩坏", "崩坏3", "崩坏三",
            "qq", "QQ",
        }

        def _usable(title: str) -> bool:
            return len(title) >= 2 and not any(g in title for g in generic)

        candidates: List[str] = []
        for t in raw_candidates:
            t = t.strip()
            if t and _usable(t) and t not in candidates:
                candidates.append(t)
        for t in candidates[:2]:
            for attempt in range(2):
                try:
                    import json as _json
                    from urllib.parse import urlencode
                    api = ("https://zh.moegirl.org.cn/api.php?"
                           + urlencode({
                               "action": "query", "titles": t,
                               "prop": "pageimages", "format": "json",
                               "pithumbsize": "1200", "redirects": "1",
                           }))
                    raw = QQNapCatDomain._http_get(
                        api, headers=headers, timeout=10)
                    data = _json.loads(raw.decode("utf-8", errors="ignore"))
                    pages = ((data.get("query") or {}).get("pages") or {})
                    for page in pages.values():
                        thumb = ((page or {}).get("thumbnail") or {}).get("source")
                        if thumb:
                            _add(_re.sub(r"/fw/\d+/", "/fw/1024/", thumb))
                    if found:
                        break
                except Exception as e:  # noqa: BLE001
                    logger.debug(
                        f"[qq_napcat] 萌娘百科搜图失败({t}, "
                        f"try {attempt + 1}): {e}")
            if found:
                break

        # 2) Bing 图片 HTML 兜底
        if not found:
            try:
                url = (f"https://cn.bing.com/images/search?q={quote(query)}"
                       "&form=HDRSC2&first=1")
                html = QQNapCatDomain._http_get(
                    url, headers=headers, timeout=15).decode(
                        "utf-8", errors="ignore")
                for m in _re.finditer(
                        r'murl(?:&quot;|\\?"):(?:&quot;|\\?")(.*?)(?:&quot;|\\?")',
                        html):
                    _add(m.group(1))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[qq_napcat] Bing 搜图失败: {e}")

        # 过滤明显不是图片的 URL
        bad = (".svg", ".ico", ".gif")
        return [u for u in found if not u.lower().endswith(bad)][:10]

    @staticmethod
    def _http_get(url: str, headers: Dict[str, str],
                  timeout: float = 15) -> bytes:
        import urllib.parse as _up
        import urllib.request
        # 非 ASCII 路径（中文图片名）必须转义，否则 http.client 会报 ascii 编码错误
        safe_url = _up.quote(
            url, safe=":/?&=%#[]@!$'()*+,;~")
        req = urllib.request.Request(safe_url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(4 * 1024 * 1024)

    @staticmethod
    def _download_image(url: str) -> str:
        """下载图片到 persona/avatar/web_<hash>.jpg；校验魔数与大小。"""
        import hashlib
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Referer": "https://image.baidu.com/",
            "Accept-Encoding": "identity",
        }
        raw = QQNapCatDomain._http_get(url, headers=headers, timeout=20)
        if not raw or len(raw) > 2 * 1024 * 1024:
            return ""
        head = raw[:16]
        if head.startswith(b"\xff\xd8\xff"):
            ext = ".jpg"
        elif head.startswith(b"\x89PNG\r\n\x1a\n"):
            ext = ".png"
        elif head[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext = ".webp"
        else:
            return ""

        digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
        out_dir = _ROOT / "persona" / "avatar"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"web_{digest}{ext}"
        if path.is_file() and path.stat().st_size > 0:
            return str(path)
        path.write_bytes(raw)
        return str(path)

    @staticmethod
    def _latest_cached_image() -> str:
        """最近一次成功下载到 persona/avatar 的图片（离线兜底）。"""
        out_dir = _ROOT / "persona" / "avatar"
        if not out_dir.is_dir():
            return ""
        files = [
            p for p in out_dir.glob("web_*")
            if p.is_file() and p.stat().st_size > 0
        ]
        if not files:
            return ""
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return str(files[0])

    async def qq_zone_post(self, content: str = "",
                           images: Optional[List[str]] = None) -> Dict[str, Any]:
        content = str(content or "").strip()
        if not content:
            return {"success": False, "error": "说说正文不能为空"}
        normalized = "".join(content.split())
        last_text, last_ts = self._last_zone_post
        if normalized == last_text and time.time() - last_ts < 300:
            return {"success": False,
                    "error": "5 分钟内已发过相同内容，本次跳过（防重复）"}
        refs: List[str] = []
        for item in (images or []):
            ref = self._image_ref(item)
            if not ref:
                return {"success": False,
                        "error": f"图片不可用（不存在/超过 2MB/格式不支持）: {item}"}
            refs.append(ref)
        # 固定「仅自己可见」，不提供公开发布入口
        params = {"content": content, "images": refs, "ugc_right": 64}
        result = await self._api("send_qzone_msg", params, timeout=25.0)
        data = result.get("data") or {}
        ok = bool(result.get("success"))
        if ok:
            self._last_zone_post = (normalized, time.time())
        self._record_op(
            "zone_post", ok,
            f"tid={data.get('tid') or ''}; content={content[:40]}")
        return {
            "success": ok,
            "error": result.get("error"),
            "retcode": result.get("retcode"),
            "tid": str(data.get("tid") or ""),
            "visibility": "仅自己可见",
        }

    @staticmethod
    def _image_ref(file: str) -> str:
        """把本地图片转成 NapCat 可识别的 base64:// 引用。"""
        import base64
        f = str(file or "").strip()
        if not f:
            return ""
        low = f.lower()
        if low.startswith(("http://", "https://", "base64://", "file://")):
            return f
        path = Path(f)
        if not path.is_absolute():
            path = (_ROOT / path).resolve()
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return ""
        return "base64://" + base64.b64encode(path.read_bytes()).decode()


def build_qq_napcat_domain() -> QQNapCatDomain:
    return QQNapCatDomain()


__all__ = ["QQNapCatDomain", "build_qq_napcat_domain"]
