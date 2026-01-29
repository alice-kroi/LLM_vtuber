# -*- coding: utf-8 -*-
import asyncio
import http.cookies
import random
from typing import *
import websockets
import aiohttp
import json
import blivedm
import blivedm.models.web as web_models

# 直播间ID的取值看直播间URL
TEST_ROOM_IDS = [
    32662853
]

# 这里填一个已登录账号的cookie的SESSDATA字段的值
# 不填也可以连接，但是收到弹幕的用户名会打码，UID会变成0
# ==============================================================================
#      如果你是从 `Chrome开发者工具 - 应用` 复制cookie的，不要勾选“显示已解码的网址”
# ==============================================================================
SESSDATA = ''

session: Optional[aiohttp.ClientSession] = None

WEBSOCKET_HOST = 'localhost'  # 本地地址
WEBSOCKET_PORT = 8765  # 目标端口
websocket_connection: Optional[websockets.WebSocketClientProtocol] = None
async def main():
    init_session()
    await init_websocket()  
    try:
        await run_single_client()
        await run_multi_clients()
    finally:
        await session.close()
        await close_websocket()


def init_session():
    cookies = http.cookies.SimpleCookie()
    cookies['SESSDATA'] = SESSDATA
    cookies['SESSDATA']['domain'] = 'bilibili.com'

    global session
    session = aiohttp.ClientSession()
    session.cookie_jar.update_cookies(cookies)


async def init_websocket():
    """初始化WebSocket连接"""
    global websocket_connection
    try:
        websocket_url = f"ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}"
        websocket_connection = await websockets.connect(websocket_url)
        print(f"WebSocket已连接到 {websocket_url}")
    except Exception as e:
        print(f"WebSocket连接失败: {e}")

async def close_websocket():
    """关闭WebSocket连接"""
    global websocket_connection
    if websocket_connection:
        await websocket_connection.close()
        websocket_connection = None
        print("WebSocket已关闭")



async def send_to_websocket(message_data: dict):
    """向WebSocket服务器发送JSON格式信息"""
    if websocket_connection:
        try:
            json_message = json.dumps(message_data, ensure_ascii=False)
            await websocket_connection.send(json_message)
            print(f"已发送WebSocket消息: {json_message}")
        except Exception as e:
            print(f"发送WebSocket消息失败: {e}")
            # 如果连接断开，尝试重新连接
            await init_websocket()
    else:
        print("WebSocket连接未初始化，尝试重新连接...")
        await init_websocket()

async def run_single_client():
    """
    演示监听一个直播间
    """
    room_id = random.choice(TEST_ROOM_IDS)
    client = blivedm.BLiveClient(room_id, session=session)
    handler = MyHandler()
    client.set_handler(handler)

    client.start()
    try:
        # 演示5秒后停止
        await asyncio.sleep(5)
        client.stop()

        await client.join()
    finally:
        await client.stop_and_close()


async def run_multi_clients():
    """
    演示同时监听多个直播间
    """
    clients = [blivedm.BLiveClient(room_id, session=session) for room_id in TEST_ROOM_IDS]
    handler = MyHandler()
    for client in clients:
        client.set_handler(handler)
        client.start()

    try:
        await asyncio.gather(*(
            client.join() for client in clients
        ))
    finally:
        await asyncio.gather(*(
            client.stop_and_close() for client in clients
        ))


class MyHandler(blivedm.BaseHandler):
    # # 演示如何添加自定义回调
    # _CMD_CALLBACK_DICT = blivedm.BaseHandler._CMD_CALLBACK_DICT.copy()
    #
    # # 看过数消息回调
    # def __watched_change_callback(self, client: blivedm.BLiveClient, command: dict):
    #     print(f'[{client.room_id}] WATCHED_CHANGE: {command}')
    # _CMD_CALLBACK_DICT['WATCHED_CHANGE'] = __watched_change_callback  # noqa

    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage):
        print(f'[{client.room_id}] 心跳')
        message_data = {
            "type": "heartbeat",
            "room_id": client.room_id,
            "popularity": message.popularity
        }
        #send_to_port(message_data)


    def _on_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage):
        print(f'[{client.room_id}] {message.uname}：{message.msg}')
        message_data = {
            "type": "danmaku",
            "room_id": client.room_id,
            "user": {
                "uid": message.uid,
                "uname": message.uname,
                "admin": message.admin,
                "vip": message.vip,
                "svip": message.svip,
                "user_level": message.user_level
            },
            "content": message.msg,
            "timestamp": message.timestamp,
            "color": message.color,
            "font_size": message.font_size,
            "mode": message.mode,
            "medal": {
                "level": message.medal_level,
                "name": message.medal_name,
                "room_id": message.medal_room_id,
                "anchor_name": message.runame
            }
        }
        #print(message_data)
        asyncio.create_task(send_to_websocket(message_data))

    def _on_gift(self, client: blivedm.BLiveClient, message: web_models.GiftMessage):
        print(f'[{client.room_id}] {message.uname} 赠送{message.gift_name}x{message.num}'
              f' （{message.coin_type}瓜子x{message.total_coin}）')
        message_data = {
            "type": "gift",
            "room_id": client.room_id,
            "user": {
                "uid": message.uid,
                "uname": message.uname,
                "guard_level": message.guard_level
            },
            "gift": {
                "name": message.gift_name,
                "id": message.gift_id,
                "type": message.gift_type,
                "num": message.num,
                "price": message.price,
                "total_coin": message.total_coin,
                "coin_type": message.coin_type
            },
            "timestamp": message.timestamp,
            "medal": {
                "level": message.medal_level,
                "name": message.medal_name,
                "room_id": message.medal_room_id,
                "anchor_id": message.medal_ruid
            }
        }
        asyncio.create_task(send_to_websocket(message_data))

    # def _on_buy_guard(self, client: blivedm.BLiveClient, message: web_models.GuardBuyMessage):
    #     print(f'[{client.room_id}] {message.username} 上舰，guard_level={message.guard_level}')

    def _on_user_toast_v2(self, client: blivedm.BLiveClient, message: web_models.UserToastV2Message):
        if message.source != 2:
            print(f'[{client.room_id}] {message.username} 上舰，guard_level={message.guard_level}')
            message_data = {
                "type": "user_toast_v2",
                "room_id": client.room_id,
                "user": {
                    "uid": message.uid,
                    "username": message.username
                },
                "guard": {
                    "level": message.guard_level,
                    "num": message.num,
                    "price": message.price,
                    "unit": message.unit,
                    "gift_id": message.gift_id
                },
                "toast_msg": message.toast_msg
            }
            asyncio.create_task(send_to_websocket(message_data))
    def _on_super_chat(self, client: blivedm.BLiveClient, message: web_models.SuperChatMessage):
        print(f'[{client.room_id}] 醒目留言 ¥{message.price} {message.uname}：{message.message}')
        message_data = {
            "type": "super_chat",
            "room_id": client.room_id,
            "user": {
                "uid": message.uid,
                "uname": message.uname,
                "guard_level": message.guard_level,
                "user_level": message.user_level
            },
            "message": message.message,
            "price": message.price,
            "start_time": message.start_time,
            "end_time": message.end_time,
            "time": message.time,
            "background": {
                "color": message.background_color,
                "bottom_color": message.background_bottom_color,
                "price_color": message.background_price_color,
                "image": message.background_image,
                "icon": message.background_icon
            },
            "gift": {
                "id": message.gift_id,
                "name": message.gift_name
            },
            "medal": {
                "level": message.medal_level,
                "name": message.medal_name,
                "room_id": message.medal_room_id,
                "anchor_id": message.medal_ruid
            }
        }
        asyncio.create_task(send_to_websocket(message_data))

    # def _on_interact_word_v2(self, client: blivedm.BLiveClient, message: web_models.InteractWordV2Message):
    #     if message.msg_type == 1:
    #         print(f'[{client.room_id}] {message.username} 进入房间')
    #         send_to_port(f'[{client.room_id}] {message.username} 进入房间')   


if __name__ == '__main__':
    asyncio.run(main())
