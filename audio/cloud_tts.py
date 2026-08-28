#!/usr/bin/env python3
"""
云端 TTS 模块 - 不依赖 GPT-SoVITS 的语音合成方案

支持:
  1. 豆包/火山引擎 TTS (Doubao / Volcengine TTS)
     - V3 WebSocket 双向 TTS (推荐, 使用 X-Api-Key 认证)
     - V3 HTTP 单向 TTS (使用 X-Api-Access-Key 认证)
     - V1 HTTP TTS (旧版, 请求体认证)
  2. 通用 HTTP TTS 接口 (自定义 POST endpoint)

设计原则:
  - 零本地模型依赖, 仅需 HTTP / WebSocket API 调用
  - 与现有 tts_node / tts_request_and_stream_play 兼容
  - 支持流式和非流式两种模式
"""

import os
import json
import asyncio
import logging
import hashlib
import time
import copy
import uuid
from io import BytesIO
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger("cloud_tts")

try:
    import aiohttp
    aiohttp_available = True
except ImportError:
    aiohttp_available = False
    logger.warning("aiohttp库未安装, 云端TTS功能不可用")

try:
    import websockets
    websockets_available = True
except ImportError:
    websockets_available = False
    logger.warning("websockets库未安装, 云端TTS WebSocket模式不可用")


# ============ 数据类 ============

@dataclass
class CloudTtsConfig:
    """云端 TTS 配置"""
    provider: str = "doubao"
    enabled: bool = False

    # ---- 通用配置 ----
    api_url: str = ""
    api_key: str = ""
    api_secret: str = ""
    timeout: int = 30
    retry_count: int = 3
    retry_interval: float = 1.0

    # ---- 豆包专有 ----
    api_version: str = "v3"
    appid: str = ""
    access_token: str = ""
    resource_id: str = "seed-tts-2.0"
    voice_type: str = "zh_female_vv_uranus_bigtts"
    speed: float = 1.0
    volume: float = 1.0
    sample_rate: int = 24000
    format: str = "pcm"

    # ---- 端点常量 ----
    V3_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    V3_WS_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    V1_ENDPOINT = "https://openspeech.bytedance.com/api/v1/tts"

    # ---- 环境变量名映射 ----
    _ENV_MAP = {
        "api_key": ["DOUBAO_API_KEY", "VOLCENGINE_API_KEY", "ARK_API_KEY", "DOUBAO_API_TOKEN"],
        "api_url": ["DOUBAO_TTS_API_URL", "VOLCENGINE_TTS_API_URL"],
        "appid": ["DOUBAO_APPID", "VOLCENGINE_APPID"],
        "access_token": ["TTS_KEY", "DOUBAO_TTS_KEY", "DOUBAO_ACCESS_TOKEN", "VOLCENGINE_ACCESS_TOKEN"],
        "resource_id": ["DOUBAO_RESOURCE_ID", "VOLCENGINE_RESOURCE_ID"],
        "voice_type": ["DOUBAO_VOICE_TYPE", "VOLCENGINE_VOICE_TYPE"],
    }

    def __post_init__(self):
        """自动从环境变量补充缺失的配置"""
        self._fill_from_env()

    def _fill_from_env(self):
        """用环境变量填充空字段"""
        for field_name, env_names in self._ENV_MAP.items():
            current = getattr(self, field_name, "")
            if current:
                continue
            for env_name in env_names:
                val = os.environ.get(env_name, "")
                if val:
                    setattr(self, field_name, val)
                    logger.debug(f"从环境变量 {env_name} 读取 {field_name}")
                    break

        # TTS_KEY 环境变量也可以作为 access_token 的 fallback (最高优先级)
        if not self.access_token:
            for env_name in ["TTS_KEY", "DOUBAO_TTS_KEY", "DOUBAO_API_KEY", "VOLCENGINE_API_KEY", "ARK_API_KEY"]:
                val = os.environ.get(env_name, "")
                if val:
                    self.access_token = val
                    logger.debug(f"从环境变量 {env_name} 读取 access_token")
                    break

        # 如果没有 api_url, 且有 DOUBAO_API_URL 环境变量, 尝试转换
        # DOUBAO_API_URL 通常是 Ark 端点 (ark.cn-beijing), TTS 需要 openspeech.bytedance.com
        if not self.api_url:
            env_api_url = os.environ.get("DOUBAO_API_URL", "")
            if env_api_url and "ark.cn-beijing" in env_api_url:
                # Ark URL, 转换为 TTS V3 端点
                self.api_url = self.V3_ENDPOINT if self.api_version == "v3" else self.V1_ENDPOINT
            elif env_api_url:
                self.api_url = env_api_url

    @classmethod
    def from_dict(cls, d: dict) -> "CloudTtsConfig":
        config = cls(
            provider=d.get("provider", "doubao"),
            enabled=d.get("enabled", False),
            api_url=d.get("api_url", ""),
            api_key=d.get("api_key", ""),
            api_secret=d.get("api_secret", ""),
            timeout=d.get("timeout", 30),
            retry_count=d.get("retry_count", 3),
            retry_interval=d.get("retry_interval", 1.0),
            api_version=d.get("api_version", "v3"),
            appid=d.get("appid", ""),
            access_token=d.get("access_token", ""),
            resource_id=d.get("resource_id", "seed-tts-2.0"),
            voice_type=d.get("voice_type", "BV700_streaming"),
            speed=d.get("speed", 1.0),
            volume=d.get("volume", 1.0),
            sample_rate=d.get("sample_rate", 24000),
            format=d.get("format", "wav"),
        )
        return config

    @classmethod
    def from_env(cls, enabled: bool = True) -> "CloudTtsConfig":
        """从环境变量创建配置"""
        return cls(enabled=enabled)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "enabled": self.enabled,
            "api_url": self.api_url,
            "api_key": "***" if self.api_key else "",
            "api_secret": "***" if self.api_secret else "",
            "timeout": self.timeout,
            "retry_count": self.retry_count,
            "retry_interval": self.retry_interval,
            "api_version": self.api_version,
            "appid": self.appid,
            "access_token": "***" if self.access_token else "",
            "resource_id": self.resource_id,
            "voice_type": self.voice_type,
            "speed": self.speed,
            "volume": self.volume,
            "sample_rate": self.sample_rate,
            "format": self.format,
        }

    def get_credential(self) -> tuple:
        """
        获取认证凭据, 返回 (appid, access_token)

        优先级:
        1. access_token 字段 (明确指定)
        2. api_key 字段 (兼容旧配置)
        3. 若 API Key 以特定前缀开头, 可能为新版 API Key-only 模式
        """
        token = self.access_token or self.api_key
        return self.appid, token

    def is_valid(self) -> bool:
        """检查配置是否有效"""
        _, token = self.get_credential()
        return bool(token)


# ============ 提供者基类 ============

class CloudTtsProvider:
    """云端 TTS 提供者基类"""

    def __init__(self, config: CloudTtsConfig):
        self.config = config

    async def synthesize(self, text: str, **kwargs) -> bytes:
        raise NotImplementedError


# ============ 豆包 TTS 提供者 ============

class DoubaoTtsProvider(CloudTtsProvider):
    """
    豆包/火山引擎 TTS 提供者

    支持三种接入方式:
      1. V3 WebSocket 双向 TTS (推荐): 使用 X-Api-Key 认证, WebSocket 协议
      2. V3 HTTP 单向 TTS: 使用 X-Api-Access-Key 认证
      3. V1 HTTP TTS (旧版): APPID + Access Token, 请求体认证

    API 文档:
      - V3 WebSocket: https://www.volcengine.com/docs/6561/1167935
      - V3 HTTP: https://www.volcengine.com/docs/6561/1598757
      - 控制台: https://console.volcengine.com/speech/new/overview
    """

    def __init__(self, config: CloudTtsConfig):
        super().__init__(config)

    async def synthesize(self, text: str, **kwargs) -> bytes:
        """调用豆包 TTS 接口合成语音"""
        if not self.config.is_valid():
            raise ValueError(
                "豆包 TTS 认证信息不完整。请在 config.ini 中配置:\n"
                "  推荐方式: 设置环境变量 TTS_KEY (豆包语音服务 API Key)\n"
                "  备选方式: 在 [cloud_tts] 段设置 access_token\n"
                "\n"
                "获取地址: https://console.volcengine.com/speech/new/overview"
            )

        appid, token = self.config.get_credential()
        voice_type = kwargs.get("voice_type", self.config.voice_type)
        speed = kwargs.get("speed", self.config.speed)
        volume = kwargs.get("volume", self.config.volume)
        sample_rate = kwargs.get("sample_rate", self.config.sample_rate)
        fmt = kwargs.get("format", self.config.format)
        resource_id = kwargs.get("resource_id", self.config.resource_id)

        # V3 优先使用 HTTP 单向流式
        if self.config.api_version == "v3":
            return await self._synthesize_v3_http(
                text, appid, token, voice_type,
                speed, volume, sample_rate, fmt, resource_id
            )
        else:
            return await self._synthesize_v1(
                text, appid, token, voice_type,
                speed, volume, sample_rate, fmt
            )

    # ---------- V3 WebSocket 双向 TTS (推荐) ----------

    async def _synthesize_v3_websocket(
        self, text: str, token: str,
        voice_type: str, speed: float, volume: float,
        sample_rate: int, fmt: str, resource_id: str
    ) -> bytes:
        """
        V3 WebSocket 双向 TTS

        使用 X-Api-Key 认证, 通过 WebSocket 协议与豆包语音服务通信。
        参考: https://www.volcengine.com/docs/6561/1167935
        """
        from tts_protocols import (
            EventType, MsgType,
            start_connection, finish_connection,
            start_session, finish_session,
            task_request, receive_message, wait_for_event,
        )

        ws_url = self.config.V3_WS_ENDPOINT
        if self.config.api_url and "bidirection" in self.config.api_url:
            ws_url = self.config.api_url

        headers = {
            "X-Api-Key": token,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

        logger.info(
            f"[Doubao TTS V3 WS] 连接 {ws_url}, "
            f"文本: {text[:50]}... (len={len(text)}, voice={voice_type})"
        )

        last_error = None
        for attempt in range(self.config.retry_count):
            try:
                if attempt > 0:
                    await asyncio.sleep(self.config.retry_interval * attempt)
                    logger.info(f"[Doubao TTS V3 WS] 重试第 {attempt + 1} 次...")

                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    logger.info(
                        f"[Doubao TTS V3 WS] 已连接, "
                        f"Logid: {ws.response.headers.get('x-tt-logid', 'N/A')}"
                    )

                    # 启动连接
                    await start_connection(ws)
                    await wait_for_event(
                        ws, MsgType.FullServerResponse,
                        EventType.ConnectionStarted,
                        timeout=self.config.timeout,
                    )

                    # 构建基础请求参数
                    base_request = {
                        "req_params": {
                            "speaker": voice_type,
                            "audio_params": {
                                "format": fmt if fmt != "wav" else "pcm",
                                "sample_rate": sample_rate,
                            },
                        },
                    }

                    # 启动会话
                    start_session_req = copy.deepcopy(base_request)
                    start_session_req["event"] = EventType.StartSession
                    session_id = str(uuid.uuid4())
                    await start_session(
                        ws, json.dumps(start_session_req).encode(), session_id
                    )
                    await wait_for_event(
                        ws, MsgType.FullServerResponse,
                        EventType.SessionStarted,
                        timeout=self.config.timeout,
                    )

                    # 异步发送文本任务
                    send_chars_done = asyncio.Event()

                    async def send_chars():
                        try:
                            for char in text:
                                synthesis_req = copy.deepcopy(base_request)
                                synthesis_req["event"] = EventType.TaskRequest
                                synthesis_req["req_params"]["text"] = char
                                await task_request(
                                    ws,
                                    json.dumps(synthesis_req).encode(),
                                    session_id,
                                )
                                await asyncio.sleep(0.005)
                            await finish_session(ws, session_id)
                        except Exception as e:
                            logger.error(f"[Doubao TTS V3 WS] 发送任务异常: {e}")
                        finally:
                            send_chars_done.set()

                    send_task = asyncio.create_task(send_chars())

                    # 接收音频数据
                    audio_data = bytearray()
                    audio_received = False

                    while True:
                        msg = await receive_message(ws)

                        if msg.msg_type == MsgType.FullServerResponse:
                            if msg.event == EventType.SessionFinished:
                                break
                            elif msg.event == EventType.Error:
                                error_info = msg.payload.decode("utf-8", errors="replace")
                                raise RuntimeError(f"服务端错误: {error_info}")
                        elif msg.msg_type == MsgType.AudioOnlyServer:
                            audio_received = True
                            audio_data.extend(msg.payload)
                        else:
                            logger.debug(
                                f"[Doubao TTS V3 WS] 跳过消息: "
                                f"type={msg.msg_type}, event={msg.event}"
                            )

                    await send_task

                    if audio_data:
                        logger.info(
                            f"[Doubao TTS V3 WS] 合成成功: {len(audio_data)} bytes"
                        )
                        # 正确关闭连接
                        try:
                            await finish_connection(ws)
                            await wait_for_event(
                                ws, MsgType.FullServerResponse,
                                EventType.ConnectionFinished,
                                timeout=5.0,
                            )
                        except Exception:
                            pass
                        return bytes(audio_data)

                    if not audio_received:
                        raise RuntimeError("未收到任何音频数据")

            except asyncio.TimeoutError:
                last_error = "请求超时"
                logger.warning(
                    f"[Doubao TTS V3 WS] 请求超时 (第 {attempt + 1} 次)"
                )
            except Exception as e:
                last_error = str(e)
                logger.error(f"[Doubao TTS V3 WS] 请求异常: {e}")

        raise ConnectionError(f"豆包 TTS V3 WebSocket 合成失败: {last_error}")

    # ---------- V3 HTTP 单向 TTS (主要方式) ----------

    async def _synthesize_v3_http(
        self, text: str, appid: str, token: str,
        voice_type: str, speed: float, volume: float,
        sample_rate: int, fmt: str, resource_id: str
    ) -> bytes:
        """
        V3 HTTP 单向流式 TTS

        使用 X-Api-Key 认证, POST JSON 请求, 响应为音频二进制流。
        参考: https://www.volcengine.com/docs/6561/1167935
        """
        headers = {
            "X-Api-Key": token,
            "X-Api-Resource-Id": resource_id,
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }

        url = self.config.api_url or self.config.V3_ENDPOINT

        payload = {
            "req_params": {
                "text": text,
                "speaker": voice_type,
                "audio_params": {
                    "format": fmt,
                    "sample_rate": sample_rate,
                    "speech_rate": int((speed - 1.0) * 100),
                    "loudness_rate": int((volume - 1.0) * 100),
                },
            },
        }

        logger.info(
            f"[Doubao TTS V3] 请求文本: {text[:50]}... (len={len(text)}, "
            f"speaker={voice_type})"
        )

        return await self._request_v3_http_with_retry(url, payload, headers)

    async def _request_v3_http_with_retry(
        self, url: str, payload: dict, headers: dict
    ) -> bytes:
        """V3 HTTP API 带重试的流式请求 (JSON 协议, base64 音频)"""
        import base64 as _base64
        last_error = None
        for attempt in range(self.config.retry_count):
            try:
                if attempt > 0:
                    await asyncio.sleep(self.config.retry_interval * attempt)
                    logger.info(f"[Doubao TTS V3] 重试第 {attempt + 1} 次...")

                timeout = aiohttp.ClientTimeout(total=self.config.timeout)
                audio_buffer = bytearray()
                text_buffer = ""
                completed = False
                decoder = json.JSONDecoder()
                last_sentence_text = ""

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        url, json=payload, headers=headers
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            last_error = f"HTTP {resp.status}: {body[:200]}"
                            logger.error(f"[Doubao TTS V3] {last_error}")
                            continue

                        async for chunk in resp.content.iter_chunked(4096):
                            if not chunk:
                                continue
                            text_buffer += chunk.decode("utf-8", errors="ignore")

                            # 使用 JSON 流解码器按对象解析（不依赖换行符分割）
                            while text_buffer:
                                try:
                                    obj, end = decoder.raw_decode(text_buffer)
                                    text_buffer = text_buffer[end:].lstrip()

                                    code = obj.get("code", -1)

                                    if code == 0:
                                        data = obj.get("data")
                                        if data and isinstance(data, str) and data:
                                            try:
                                                audio_bytes = _base64.b64decode(data)
                                                audio_buffer.extend(audio_bytes)
                                            except Exception:
                                                pass

                                        sentence = obj.get("sentence", {})
                                        if sentence and isinstance(sentence, dict):
                                            sent_text = sentence.get("text", "")
                                            if sent_text:
                                                last_sentence_text = sent_text

                                        usage = obj.get("usage", {})
                                        if usage:
                                            logger.debug(f"[Doubao TTS V3] usage: {usage}")

                                    elif code == 20000000:
                                        completed = True
                                        logger.debug("[Doubao TTS V3] 合成完成")
                                        break
                                    else:
                                        msg = obj.get("message", "unknown error")
                                        last_error = f"API 错误 code={code}: {msg}"
                                        logger.error(f"[Doubao TTS V3] {last_error}")
                                        break

                                except json.JSONDecodeError:
                                    # 数据不足，等待更多 chunk
                                    break

                if audio_buffer:
                    logger.info(
                        f"[Doubao TTS V3] 合成成功: {len(audio_buffer)} bytes, "
                        f"sentence='{last_sentence_text}'"
                    )
                    return bytes(audio_buffer)

                if completed:
                    logger.info("[Doubao TTS V3] 合成完成 (无音频数据)")
                    return b""

                if not last_error:
                    last_error = "未收到音频数据"
                logger.error(f"[Doubao TTS V3] 合成失败: {last_error}")

            except asyncio.TimeoutError:
                last_error = "请求超时"
                logger.warning(
                    f"[Doubao TTS V3] 请求超时 (第 {attempt + 1} 次)"
                )
            except Exception as e:
                last_error = str(e)
                logger.error(f"[Doubao TTS V3] 请求异常: {e}")

        raise ConnectionError(f"豆包 TTS V3 HTTP 合成失败: {last_error}")

    # ---------- V1 API (请求体认证, 兼容旧版) ----------

    async def _synthesize_v1(
        self, text: str, appid: str, token: str,
        voice_type: str, speed: float, volume: float,
        sample_rate: int, fmt: str
    ) -> bytes:
        """V1 旧版 API"""
        payload = {
            "app": {
                "appid": appid,
                "token": token,
                "cluster": "volcano_tts",
            },
            "user": {
                "uid": "llm_vtuber_user",
            },
            "audio": {
                "voice_type": voice_type,
                "encoding": fmt,
                "speed_ratio": speed,
                "volume_ratio": volume,
                "sample_rate": sample_rate,
            },
            "request": {
                "reqid": hashlib.md5(
                    f"{time.time()}{text}".encode()
                ).hexdigest(),
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer;{token}",
        }

        logger.info(
            f"[Doubao TTS V1] 请求文本: {text[:50]}... (len={len(text)})"
        )

        return await self._request_v1_with_retry(
            self.config.api_url, payload, headers
        )

    async def _request_v1_with_retry(
        self, url: str, payload: dict, headers: dict
    ) -> bytes:
        """V1 API 带重试的请求"""
        import base64

        last_error = None
        for attempt in range(self.config.retry_count):
            try:
                if attempt > 0:
                    await asyncio.sleep(self.config.retry_interval * attempt)
                    logger.info(f"[Doubao TTS V1] 重试第 {attempt + 1} 次...")

                timeout = aiohttp.ClientTimeout(total=self.config.timeout)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        url, json=payload, headers=headers
                    ) as resp:
                        if resp.status == 200:
                            content_type = resp.headers.get(
                                "Content-Type", ""
                            )
                            if "audio" in content_type:
                                audio_data = await resp.read()
                                logger.info(
                                    f"[Doubao TTS V1] 合成成功: "
                                    f"{len(audio_data)} bytes"
                                )
                                return audio_data
                            else:
                                resp_json = await resp.json()
                                code = resp_json.get("code", -1)
                                message = resp_json.get(
                                    "message", "unknown error"
                                )
                                if code == 3000:
                                    audio_data = base64.b64decode(
                                        resp_json.get("data", "")
                                    )
                                    logger.info(
                                        f"[Doubao TTS V1] 合成成功(base64): "
                                        f"{len(audio_data)} bytes"
                                    )
                                    return audio_data
                                else:
                                    last_error = (
                                        f"API错误: code={code}, "
                                        f"message={message}"
                                    )
                                    logger.error(
                                        f"[Doubao TTS V1] {last_error}"
                                    )
                        elif resp.status == 401:
                            body = await resp.text()
                            raise PermissionError(f"认证失败: {body}")
                        else:
                            body = await resp.text()
                            last_error = f"HTTP {resp.status}: {body[:200]}"
                            logger.error(
                                f"[Doubao TTS V1] {last_error}"
                            )
            except asyncio.TimeoutError:
                last_error = "请求超时"
                logger.warning(
                    f"[Doubao TTS V1] 请求超时 (第 {attempt + 1} 次)"
                )
            except Exception as e:
                last_error = str(e)
                logger.error(f"[Doubao TTS V1] 请求异常: {e}")

        raise ConnectionError(f"豆包 TTS V1 合成失败: {last_error}")


# ============ 通用 HTTP TTS 提供者 ============

class GenericHttpTtsProvider(CloudTtsProvider):
    """
    通用 HTTP TTS 提供者

    通过 POST JSON 请求到自定义端点
    期望响应为音频二进制数据
    """

    async def synthesize(self, text: str, **kwargs) -> bytes:
        if not self.config.api_url:
            raise ValueError("通用 TTS 需要配置 api_url")

        payload = {
            "text": text,
            "voice_type": kwargs.get(
                "voice_type", self.config.voice_type
            ),
            "speed": kwargs.get("speed", self.config.speed),
            "volume": kwargs.get("volume", self.config.volume),
            "sample_rate": kwargs.get(
                "sample_rate", self.config.sample_rate
            ),
            "format": kwargs.get("format", self.config.format),
        }

        if self.config.api_key:
            payload["api_key"] = self.config.api_key

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        logger.info(
            f"[Generic TTS] 请求 {self.config.api_url}, "
            f"文本: {text[:50]}..."
        )

        return await self._request_with_retry(
            self.config.api_url, payload, headers
        )

    async def _request_with_retry(
        self, url: str, payload: dict, headers: dict
    ) -> bytes:
        last_error = None
        for attempt in range(self.config.retry_count):
            try:
                if attempt > 0:
                    await asyncio.sleep(self.config.retry_interval * attempt)
                    logger.info(f"[Generic TTS] 重试第 {attempt + 1} 次...")

                timeout = aiohttp.ClientTimeout(total=self.config.timeout)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        url, json=payload, headers=headers
                    ) as resp:
                        if resp.status == 200:
                            audio_data = await resp.read()
                            if audio_data:
                                logger.info(
                                    f"[Generic TTS] 合成成功: "
                                    f"{len(audio_data)} bytes"
                                )
                                return audio_data
                        else:
                            body = await resp.text()
                            last_error = (
                                f"HTTP {resp.status}: {body[:200]}"
                            )
                            logger.error(
                                f"[Generic TTS] {last_error}"
                            )
            except asyncio.TimeoutError:
                last_error = "请求超时"
                logger.warning(
                    f"[Generic TTS] 请求超时 "
                    f"(第 {attempt + 1} 次)"
                )
            except Exception as e:
                last_error = str(e)
                logger.error(f"[Generic TTS] 请求异常: {e}")

        raise ConnectionError(f"通用 TTS 合成失败: {last_error}")


# ============ 工厂与入口 ============

_PROVIDER_MAP = {
    "doubao": DoubaoTtsProvider,
    "generic": GenericHttpTtsProvider,
}

_PROVIDER_NAMES = {
    "doubao": "豆包/火山引擎 TTS",
    "generic": "通用 HTTP TTS",
}


def create_provider(config: CloudTtsConfig) -> CloudTtsProvider:
    """根据配置创建对应的 TTS 提供者"""
    provider_class = _PROVIDER_MAP.get(
        config.provider, DoubaoTtsProvider
    )
    return provider_class(config)


async def cloud_tts_synthesize(
    text: str,
    config: CloudTtsConfig,
    streaming: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    """
    云端 TTS 合成主入口

    Args:
        text: 要合成的文本
        config: TTS 配置
        streaming: 是否使用流式模式
        **kwargs: 传递给提供者的额外参数

    Returns:
        Dict[str, Any]: 结果字典
    """
    result: Dict[str, Any] = {
        "success": False,
        "audio_data": b"",
        "error": None,
        "provider": config.provider,
        "sample_rate": config.sample_rate,
        "format": config.format,
    }

    if not aiohttp_available:
        result["error"] = "aiohttp库未安装, 请运行: pip install aiohttp"
        return result

    if not config.enabled:
        result["error"] = "云端TTS未启用 (cloud_tts.enabled = true)"
        return result

    if not text or not text.strip():
        result["error"] = "文本为空"
        return result

    try:
        provider = create_provider(config)
        audio_data = await provider.synthesize(text, **kwargs)

        if audio_data:
            result["success"] = True
            result["audio_data"] = audio_data
        else:
            result["error"] = "合成返回空音频"
    except PermissionError as e:
        result["error"] = f"认证失败: {e}"
        logger.error(result["error"])
    except ConnectionError as e:
        result["error"] = str(e)
        logger.error(result["error"])
    except Exception as e:
        result["error"] = f"合成异常: {e}"
        logger.error(result["error"], exc_info=True)

    return result


def get_cloud_tts_status(config: CloudTtsConfig) -> Dict[str, Any]:
    """获取云端 TTS 状态信息"""
    _, token = config.get_credential()
    return {
        "enabled": config.enabled,
        "provider": config.provider,
        "api_configured": bool(token),
        "api_version": config.api_version,
        "has_appid": bool(config.appid),
        "voice_type": config.voice_type,
        "resource_id": config.resource_id,
        "sample_rate": config.sample_rate,
        "format": config.format,
        "supported_providers": list(_PROVIDER_NAMES.keys()),
    }
