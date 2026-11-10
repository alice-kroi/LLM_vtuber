# ============================================================
# LLM_v2/domains/obs_control/obs_control.py — OBS 控制能力域
# ============================================================
# 协议：obs-websocket 5.x（OBS 28+ 内置）
#   默认 127.0.0.1:4455；密码读环境变量 OBS_WS_PASSWORD（不写死）
# 风险：R2（仅本机 WebSocket；启用密码时等同 R3）
# ============================================================

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType, tool_schema

_TOOL_NAMES = [
    "obs_status",
    "obs_scenes",
    "obs_switch_scene",
    "obs_set_source_visibility",
    "obs_set_mute",
    "obs_start_record",
    "obs_stop_record",
]

_TOOLS_SCHEMA = [
    tool_schema("obs_status", "[OBS] 查看 OBS 版本与当前场景。"),
    tool_schema("obs_scenes", "[OBS] 列出所有场景名。"),
    tool_schema("obs_switch_scene", "[OBS] 切换当前直播场景。",
                {"scene": {"type": "string", "description": "场景名"}},
                required=["scene"]),
    tool_schema("obs_set_source_visibility",
                "[OBS] 显示/隐藏某个场景里的源（摄像头、字幕、图片等）。",
                {"scene": {"type": "string", "description": "场景名"},
                 "source": {"type": "string", "description": "源名称"},
                 "visible": {"type": "boolean", "description": "true=显示"}},
                required=["scene", "source", "visible"]),
    tool_schema("obs_set_mute", "[OBS] 静音/取消静音某个音频输入。",
                {"source": {"type": "string", "description": "音频输入名"},
                 "muted": {"type": "boolean", "description": "true=静音"}},
                required=["source", "muted"]),
    tool_schema("obs_start_record", "[OBS] 开始录制。"),
    tool_schema("obs_stop_record", "[OBS] 停止录制。"),
]


class _OBS:
    """极简 obs-websocket v5 客户端（每次调用独立连接）"""

    def __init__(self, host: str = "", port: int = 0):
        self.host = host or os.environ.get("OBS_WS_HOST", "127.0.0.1")
        self.port = int(port or os.environ.get("OBS_WS_PORT", "4455"))
        self.password = os.environ.get("OBS_WS_PASSWORD", "")

    def _connect(self):
        import websocket
        ws = websocket.create_connection(
            f"ws://{self.host}:{self.port}", timeout=5)
        hello = json.loads(ws.recv())
        auth = None
        challenge = (hello.get("d") or {}).get("authentication")
        if challenge:
            if not self.password:
                ws.close()
                raise RuntimeError("OBS 开启了认证，请设置环境变量 OBS_WS_PASSWORD")
            secret = base64.b64encode(
                hashlib.sha256((self.password + challenge["salt"]).encode()).digest())
            auth = base64.b64encode(
                hashlib.sha256(secret + challenge["challenge"].encode()).digest()
            ).decode()
        payload = {"op": 1, "d": {"rpcVersion": 1}}
        if auth:
            payload["d"]["authentication"] = auth
        ws.send(json.dumps(payload))
        resp = json.loads(ws.recv())
        if resp.get("op") != 2:
            ws.close()
            raise RuntimeError(f"OBS 认证失败: {resp}")
        return ws

    def request(self, request_type: str,
                request_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ws = self._connect()
        try:
            rid = str(uuid.uuid4())
            ws.send(json.dumps({"op": 6, "d": {
                "requestType": request_type, "requestId": rid,
                "requestData": request_data or {},
            }}))
            deadline = time.time() + 8
            while time.time() < deadline:
                msg = json.loads(ws.recv())
                if msg.get("op") != 7:
                    continue          # 跳过事件
                data = msg.get("d") or {}
                if data.get("requestId") != rid:
                    continue
                status = data.get("requestStatus") or {}
                if not status.get("result", False):
                    raise RuntimeError(
                        f"{request_type} 失败: code={status.get('code')} "
                        f"{status.get('comment', '')}")
                return data.get("responseData") or {}
            raise RuntimeError(f"{request_type} 超时")
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass


class ObsControlDomain(Actuator):
    id = "obs_control"
    label = "OBS 控制"
    prefix = "obs_"
    default_enabled = True
    type = CapabilityType.ACTUATOR
    call_timeout = 60.0

    def allowed_names(self) -> List[str]:
        return list(_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(_TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return ("【OBS 控制】可切换场景、显示/隐藏源、静音、开始/停止录制。"
                "需要 OBS 28+ 并开启 WebSocket（默认 4455）。")

    def _client(self) -> _OBS:
        return _OBS()

    @staticmethod
    def _fail(e: Exception) -> Dict[str, Any]:
        msg = str(e)
        hint = ""
        if "refused" in msg.lower() or "connect" in msg.lower():
            hint = "（OBS 未启动，或 工具→WebSocket 服务器设置 未开启）"
        return {"success": False, "error": msg[:300] + hint}

    def obs_status(self) -> Dict[str, Any]:
        try:
            client = self._client()
            version = client.request("GetVersion")
            scene = client.request("GetCurrentProgramScene")
            return {"success": True,
                    "obs_version": version.get("obsVersion"),
                    "websocket_version": version.get("obsWebSocketVersion"),
                    "scene": scene.get("currentProgramSceneName")}
        except Exception as e:  # noqa: BLE001
            return self._fail(e)

    def obs_scenes(self) -> Dict[str, Any]:
        try:
            data = self._client().request("GetSceneList")
            scenes = [s.get("sceneName") for s in data.get("scenes") or []]
            return {"success": True, "scenes": scenes,
                    "current": data.get("currentProgramSceneName")}
        except Exception as e:  # noqa: BLE001
            return self._fail(e)

    def obs_switch_scene(self, scene: str) -> Dict[str, Any]:
        try:
            self._client().request("SetCurrentProgramScene",
                                   {"sceneName": scene})
            return {"success": True, "scene": scene}
        except Exception as e:  # noqa: BLE001
            return self._fail(e)

    def obs_set_source_visibility(self, scene: str, source: str,
                                  visible: bool) -> Dict[str, Any]:
        try:
            client = self._client()
            item = client.request("GetSceneItemId", {
                "sceneName": scene, "sourceName": source})
            client.request("SetSceneItemEnabled", {
                "sceneName": scene,
                "sceneItemId": item.get("sceneItemId"),
                "sceneItemEnabled": bool(visible),
            })
            return {"success": True, "scene": scene, "source": source,
                    "visible": bool(visible)}
        except Exception as e:  # noqa: BLE001
            return self._fail(e)

    def obs_set_mute(self, source: str, muted: bool) -> Dict[str, Any]:
        try:
            self._client().request("SetInputMute", {
                "inputName": source, "inputMuted": bool(muted)})
            return {"success": True, "source": source, "muted": bool(muted)}
        except Exception as e:  # noqa: BLE001
            return self._fail(e)

    def obs_start_record(self) -> Dict[str, Any]:
        try:
            self._client().request("StartRecord")
            return {"success": True, "recording": True}
        except Exception as e:  # noqa: BLE001
            return self._fail(e)

    def obs_stop_record(self) -> Dict[str, Any]:
        try:
            data = self._client().request("StopRecord")
            return {"success": True, "recording": False,
                    "output": data.get("outputPath", "")}
        except Exception as e:  # noqa: BLE001
            return self._fail(e)


def build_obs_control_domain() -> ObsControlDomain:
    return ObsControlDomain()


__all__ = ["ObsControlDomain", "build_obs_control_domain"]
