#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
哔哩哔哩弹幕监听主程序

功能：
1. 常态监听指定直播间的弹幕/礼物/SC信息
2. 弹幕去重（避免重复处理同一条消息）
3. 连接断开自动重连（指数退避）
4. 每收到一条有效消息就转换为OpenAI格式，并通过HTTP POST转发给主程序
"""

import asyncio
import json
import logging
import time
import http.cookies
import os
import aiohttp
from typing import Dict, Any, Optional, Set

# 导入blivedm库（当前目录）
import blivedm
import blivedm.models.web as web_models

# ---------- 配置 ----------
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

# 从config.json读取配置
def _load_config() -> Dict[str, Any]:
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        room_ids_raw = cfg.get('ROOM_IDS', '71001')
        if isinstance(room_ids_raw, (list, tuple)):
            room_ids = [int(str(x).strip()) for x in room_ids_raw if str(x).strip()]
        else:
            room_ids = [int(x.strip()) for x in str(room_ids_raw).split(',') if x.strip()]
        return {
            'ROOM_IDS': room_ids,
            'SESSDATA': cfg.get('SESSDATA', ''),
            'OUTPUT_PORT': int(cfg.get('output_port', 8081)),
        }
    except Exception as e:
        logging.error(f"读取配置文件失败: {e}，使用默认值")
        return {
            'ROOM_IDS': [71001],
            'SESSDATA': '',
            'OUTPUT_PORT': 8081,
        }

_CFG = _load_config()
TEST_ROOM_IDS = _CFG['ROOM_IDS']
SESSDATA = _CFG['SESSDATA']
OUTPUT_PORT = _CFG['OUTPUT_PORT']

# ---------- 日志 ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'bili_main.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('BiliMain')
# 降低blivedm日志噪音
logging.getLogger('blivedm').setLevel(logging.WARNING)

# ---------- 弹幕去重 ----------
# (room_id, message_type, uid, timestamp_ms_or_0, content)
_processed_ids: Set[str] = set()
_PROCESSED_MAX = 5000  # 去重集合上限，避免无限增长

def _dedup_key(room_id: int, mtype: str, uid: int, ts: float, content: str) -> str:
    return f"{room_id}|{mtype}|{uid}|{int(ts)}|{content[:40]}"

def _mark_processed(key: str) -> bool:
    """返回 True 表示是新消息（未处理过）"""
    if key in _processed_ids:
        return False
    _processed_ids.add(key)
    # 超过上限时淘汰老的一半
    if len(_processed_ids) > _PROCESSED_MAX:
        half = _PROCESSED_MAX // 2
        to_remove = sorted(_processed_ids)[:half]
        for k in to_remove:
            _processed_ids.discard(k)
    return True

# ---------- HTTP转发 ----------
_FORWARD_URL = f"http://127.0.0.1:{OUTPUT_PORT}/"
_forward_session: Optional[aiohttp.ClientSession] = None

def _ensure_forward_session() -> aiohttp.ClientSession:
    global _forward_session
    if _forward_session is None or _forward_session.closed:
        _forward_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    return _forward_session


async def forward_message(openai_message: Dict[str, Any], raw: Dict[str, Any]) -> None:
    """
    转发消息到主程序。payload与sample.py兼容：直接发OpenAI message字典，
    主程序handle_post_request会自动包装成列表调用handle_messages。
    """
    try:
        session = _ensure_forward_session()
        async with session.post(
            _FORWARD_URL,
            json=openai_message,
            headers={'Content-Type': 'application/json'},
        ) as resp:
            status = resp.status
            if status >= 300:
                txt = await resp.text()
                logger.error(f"转发消息到主程序返回非2xx: status={status}, body={txt[:200]}")
            else:
                logger.info(f"✅ 转发成功: {openai_message.get('content', '')[:50]}")
    except asyncio.TimeoutError:
        logger.error(f"❌ 转发超时(10s): {openai_message.get('content', '')[:50]}")
    except Exception as e:
        logger.error(f"❌ 转发失败: {e}, 消息: {openai_message.get('content', '')[:50]}")


# ---------- 消息格式转换 ----------
def convert_to_openai_message(message_data: Dict[str, Any]) -> Dict[str, Any]:
    """将弹幕/礼物/SC数据转为OpenAI message格式（含name字段）"""
    message_type = message_data.get("type", "danmaku")
    user_name = message_data.get("user", {}).get("uname", "未知用户")

    if message_type == "danmaku":
        content = message_data.get("content", "")
    elif message_type == "gift":
        gift_name = message_data.get("gift", {}).get("name", "")
        num = message_data.get("gift", {}).get("num", 1)
        content = f"赠送了{num}个{gift_name}"
    elif message_type == "super_chat":
        content = message_data.get("message", "")
        price = message_data.get("price", 0)
        content = f"[{price}元SC] {content}"
    else:
        content = message_data.get("content", "")

    return {
        "role": "user",
        "content": content,
        "name": user_name,
    }


# ---------- Handler ----------
# 连接时间阈值：连接前的历史弹幕将被丢弃（5秒容差处理时钟偏差）
_HISTORY_GRACE_SECONDS = 5


class RealTimeDanmakuHandler(blivedm.BaseHandler):
    """实时弹幕处理器：历史弹幕过滤 + 去重 + 打印 + 转发"""

    def __init__(self):
        # 记录连接时间，用于过滤连接时推送的历史弹幕
        self.connect_time = time.time()

    def _is_history_message(self, timestamp: float) -> bool:
        """判断是否为历史消息（连接前推送的旧弹幕）"""
        return float(timestamp) < self.connect_time - _HISTORY_GRACE_SECONDS

    def _on_unknown_command(self, client, command):
        pass  # 静默忽略未知命令

    def _on_heartbeat(self, client, message):
        pass

    def _on_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage):
        # 过滤连接时推送的历史弹幕
        if self._is_history_message(message.timestamp):
            logger.debug(f"[历史弹幕-跳过] {message.uname}: {message.msg[:30]}")
            return

        raw = {
            "type": "danmaku",
            "room_id": client.room_id,
            "user": {
                "uid": message.uid,
                "uname": message.uname,
            },
            "content": message.msg,
            "timestamp": message.timestamp,
        }
        key = _dedup_key(
            client.room_id, "danmaku", message.uid,
            float(message.timestamp), message.msg,
        )
        if not _mark_processed(key):
            logger.debug(f"[去重-弹幕] 跳过重复: {message.uname}: {message.msg[:30]}")
            return

        logger.info(f"[弹幕] {message.uname}: {message.msg}")
        openai_msg = convert_to_openai_message(raw)
        print(json.dumps(openai_msg, ensure_ascii=False))
        asyncio.create_task(forward_message(openai_msg, raw))

    def _on_gift(self, client: blivedm.BLiveClient, message: web_models.GiftMessage):
        # 过滤连接时推送的历史礼物
        if self._is_history_message(message.timestamp):
            logger.debug(f"[历史礼物-跳过] {message.uname} {message.gift_name}x{message.num}")
            return

        raw = {
            "type": "gift",
            "room_id": client.room_id,
            "user": {"uid": message.uid, "uname": message.uname},
            "gift": {
                "name": message.gift_name,
                "num": message.num,
                "price": message.price,
            },
            "timestamp": message.timestamp,
        }
        key = _dedup_key(
            client.room_id, "gift", message.uid,
            float(message.timestamp),
            f"{message.gift_name}x{message.num}",
        )
        if not _mark_processed(key):
            logger.debug(f"[去重-礼物] 跳过重复: {message.uname} {message.gift_name}x{message.num}")
            return

        logger.info(f"[礼物] {message.uname} 赠送了{message.num}个{message.gift_name}")
        openai_msg = convert_to_openai_message(raw)
        print(json.dumps(openai_msg, ensure_ascii=False))
        asyncio.create_task(forward_message(openai_msg, raw))

    def _on_super_chat(self, client: blivedm.BLiveClient, message: web_models.SuperChatMessage):
        # 过滤连接时推送的历史SC
        if self._is_history_message(message.start_time):
            logger.debug(f"[历史SC-跳过] {message.uname} ({message.price}元)")
            return

        raw = {
            "type": "super_chat",
            "room_id": client.room_id,
            "user": {"uid": message.uid, "uname": message.uname},
            "message": message.message,
            "price": message.price,
            "timestamp": message.start_time,
        }
        key = _dedup_key(
            client.room_id, "super_chat", message.uid,
            float(message.start_time), message.message,
        )
        if not _mark_processed(key):
            logger.debug(f"[去重-SC] 跳过重复: {message.uname} ({message.price}元)")
            return

        logger.info(f"[SC] {message.uname} ({message.price}元): {message.message}")
        openai_msg = convert_to_openai_message(raw)
        print(json.dumps(openai_msg, ensure_ascii=False))
        asyncio.create_task(forward_message(openai_msg, raw))


# ---------- Session初始化 ----------
def init_session() -> aiohttp.ClientSession:
    cookies = http.cookies.SimpleCookie()
    if SESSDATA:
        cookies['SESSDATA'] = SESSDATA
        cookies['SESSDATA']['domain'] = 'bilibili.com'
    session = aiohttp.ClientSession()
    if SESSDATA:
        session.cookie_jar.update_cookies(cookies)
    return session


# ---------- 监听+重连循环 ----------
async def start_real_time_listener(room_ids):
    """
    开始实时监听弹幕（支持多个直播间），连接断开后自动重连。
    重连间隔：1s -> 2s -> 4s -> 8s -> 最大30s。
    """
    logger.info(f"开始实时监听直播间 {room_ids} 的弹幕，转发到 {_FORWARD_URL}")

    backoff = 1.0
    backoff_max = 30.0

    while True:
        session = init_session()
        handler = RealTimeDanmakuHandler()
        clients = [blivedm.BLiveClient(rid, session=session) for rid in room_ids]
        for c in clients:
            c.set_handler(handler)

        try:
            for c in clients:
                c.start()
            logger.info(f"已启动 {len(clients)} 个弹幕监听客户端")
            print(f"\n=== 实时弹幕监听系统 ===")
            print(f"正在监听直播间: {room_ids}")
            print(f"转发目标: {_FORWARD_URL}")
            print("按 Ctrl+C 停止\n")

            backoff = 1.0  # 连接成功：重置退避
            # 等待任意客户端终止（通常是连接断开）
            await asyncio.gather(*(c.join() for c in clients), return_exceptions=True)

        except asyncio.CancelledError:
            logger.info("监听任务被取消")
            break
        except KeyboardInterrupt:
            logger.info("用户中断程序")
            break
        except Exception as e:
            logger.error(f"监听过程中发生错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            # 关闭所有客户端和session
            for c in clients:
                try:
                    c.stop()
                except Exception:
                    pass
            for c in clients:
                try:
                    await c.stop_and_close()
                except Exception:
                    pass
            try:
                await session.close()
            except Exception:
                pass
            logger.info(f"当前轮客户端已停止，{backoff:.1f}s后尝试重连...")

        # 退避等待，期间允许CancelledError中断
        try:
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            break
        backoff = min(backoff * 2, backoff_max)

    # 关闭转发session
    if _forward_session is not None:
        try:
            await _forward_session.close()
        except Exception:
            pass
    logger.info("弹幕监听器已完全退出")


async def main_async():
    if not TEST_ROOM_IDS:
        logger.error("ROOM_IDS为空，无法启动弹幕监听")
        return
    await start_real_time_listener(TEST_ROOM_IDS)


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n程序已停止")


if __name__ == "__main__":
    main()
