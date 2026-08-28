#!/usr/bin/env python3
"""
豆包/火山引擎 TTS WebSocket V3 双向协议

基于 OpenSpeech V3 WebSocket API 协议规范 (JSON 文本消息):
  MsgType: FullServerResponse / AudioOnlyServer
  EventType: ConnectionStarted / SessionStarted / TTSResponse / SessionFinished / ConnectionFinished

文档: https://www.volcengine.com/docs/6561/1167935
"""

import json
import enum
import uuid
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

logger = logging.getLogger("tts_protocols")


class MsgType(enum.StrEnum):
    FullServerResponse = "FullServerResponse"
    AudioOnlyServer = "AudioOnlyServer"


class EventType(enum.StrEnum):
    ConnectionStarted = "ConnectionStarted"
    ConnectionFinished = "ConnectionFinished"
    SessionStarted = "SessionStarted"
    SessionFinished = "SessionFinished"
    TTSRequest = "TTSRequest"
    TTSResponse = "TTSResponse"
    TTSSentenceStart = "TTSSentenceStart"
    TTSSentenceEnd = "TTSSentenceEnd"


@dataclass
class ServerMessage:
    msg_type: MsgType
    event: EventType
    session_id: str = ""
    connect_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    audio_data: bytes = b""


def _make_message(event: EventType, session_id: str = "", payload: Dict[str, Any] = None) -> str:
    msg = {"EventType": event.value}
    if session_id:
        msg["SessionId"] = session_id
    if payload:
        msg["Payload"] = payload
    return json.dumps(msg)


def _parse_message(raw: str) -> ServerMessage:
    data = json.loads(raw)
    msg_type = MsgType(data.get("MsgType", "FullServerResponse"))
    event = EventType(data.get("EventType", "ConnectionFinished"))
    return ServerMessage(
        msg_type=msg_type,
        event=event,
        session_id=data.get("SessionId", ""),
        connect_id=data.get("ConnectId", ""),
        payload=data.get("Payload", {}),
    )


async def start_connection(ws) -> None:
    """发送 ConnectionStarted 请求"""
    msg = _make_message(EventType.ConnectionStarted)
    await ws.send(msg)
    logger.debug("发送 ConnectionStarted")


async def finish_connection(ws) -> None:
    """发送 ConnectionFinished 请求"""
    msg = _make_message(EventType.ConnectionFinished)
    await ws.send(msg)
    logger.debug("发送 ConnectionFinished")


async def start_session(ws, payload_bytes: bytes, session_id: str) -> None:
    """发送 SessionStarted 请求"""
    payload = json.loads(payload_bytes) if isinstance(payload_bytes, bytes) else payload_bytes
    msg = _make_message(EventType.SessionStarted, session_id, payload)
    await ws.send(msg)
    logger.debug(f"发送 SessionStarted (session={session_id[:8]}...)")


async def finish_session(ws, session_id: str) -> None:
    """发送 SessionFinished 请求"""
    msg = _make_message(EventType.SessionFinished, session_id)
    await ws.send(msg)
    logger.debug(f"发送 SessionFinished (session={session_id[:8]}...)")


async def task_request(ws, payload_bytes: bytes, session_id: str) -> None:
    """发送 TTSRequest"""
    payload = json.loads(payload_bytes) if isinstance(payload_bytes, bytes) else payload_bytes
    msg = _make_message(EventType.TTSRequest, session_id, payload)
    await ws.send(msg)


async def receive_message(ws) -> ServerMessage:
    """接收一条服务端消息 (JSON 文本帧)"""
    raw = await ws.recv()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return _parse_message(raw)


async def receive_audio(ws, audio_data: bytearray) -> ServerMessage:
    """
    接收一条完整消息:
      - FullServerResponse: JSON 文本帧
      - AudioOnlyServer: 二进制音频帧
    """
    raw = await ws.recv()
    if isinstance(raw, bytes):
        audio_data.extend(raw)
        return ServerMessage(
            msg_type=MsgType.AudioOnlyServer,
            event=EventType.TTSResponse,
            audio_data=bytes(raw),
        )
    else:
        if isinstance(raw, str):
            raw = raw
        return _parse_message(raw)


async def wait_for_event(
    ws,
    expected_msg_type: MsgType,
    expected_event: EventType,
    timeout: float = 30.0,
) -> ServerMessage:
    """等待特定类型的事件消息"""
    start = asyncio.get_event_loop().time()
    while True:
        elapsed = asyncio.get_event_loop().time() - start
        if elapsed > timeout:
            raise TimeoutError(f"等待事件超时 ({timeout}s): msg_type={expected_msg_type}, event={expected_event}")

        msg = await receive_message(ws)

        if msg.msg_type == expected_msg_type and msg.event == expected_event:
            logger.debug(f"收到期望事件: {expected_msg_type.value}, {expected_event.value}")
            return msg

        logger.debug(
            f"跳过消息: {msg.msg_type.value}, {msg.event.value} "
            f"(期望 {expected_msg_type.value}/{expected_event.value})"
        )
