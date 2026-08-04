# -*- coding: utf-8 -*-
import asyncio
import http.cookies
import random
from typing import *
import aiohttp
import json
import blivedm
import blivedm.models.web as web_models
import os
import time
import uuid
import logging

# 禁用blivedm的日志
logging.getLogger('blivedm').setLevel(logging.CRITICAL)

# 从config.json读取配置
config_path = os.path.join(os.path.dirname(__file__), 'config.json')
print(f"[配置] 正在读取配置文件: {config_path}")
print(f"[配置] 文件是否存在: {os.path.exists(config_path)}")
try:
    with open(config_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)
        # 读取ROOM_IDS，取第一个房间ID
        room_ids = config_data.get('ROOM_IDS', '71001')
        TEST_ROOM_IDS = [int(room_id.strip()) for room_id in room_ids.split(',')]
        # 读取SESSDATA
        SESSDATA = config_data.get('SESSDATA', '')
        # 读取output_port
        output_port = config_data.get('output_port', 8080)
    print(f"[配置] 读取成功！ROOM_IDS={TEST_ROOM_IDS}, output_port={output_port}")
except Exception as e:
    # 使用默认值
    TEST_ROOM_IDS = [27885573]
    SESSDATA = ""
    output_port = 8080
    print(f"[配置] 读取失败！使用默认值: {e}")
    print(f"[配置] 默认值: ROOM_IDS={TEST_ROOM_IDS}, output_port={output_port}")

session: Optional[aiohttp.ClientSession] = None



# OpenAI message格式转换函数
def convert_to_openai_message(message_data: dict) -> dict:
    """
    将消息数据转换为OpenAI的message格式
    
    参数:
        message_data: 消息数据
    
    返回:
        OpenAI的message格式
    """
    # 生成消息文本
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
        content = f"[{price}元] {content}"
    else:
        content = message_data.get("content", "")
    
    # 构建OpenAI message格式，包含name字段
    openai_message = {
        "role": "user",
        "content": content,
        "name": user_name
    }
    
    return openai_message

async def send_to_output_port(message: dict):
    """
    向指定的输出端口发送消息
    
    参数:
        message: 要发送的消息
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://localhost:{output_port}/"
            headers = {"Content-Type": "application/json"}
            print(f"发送请求到: {url}")
            print(f"消息内容: {json.dumps(message, ensure_ascii=False)}")
            response = await session.post(url, json=message, headers=headers, timeout=5)
            print(f"响应状态: {response.status}")
    except Exception as e:
        print(f"发送请求失败: {e}")
        # 静默处理错误，不影响主程序运行
        pass

async def main():
    init_session()
    try:
        await run_single_client()
        await run_multi_clients()
    finally:
        await session.close()


def init_session():
    cookies = http.cookies.SimpleCookie()
    if SESSDATA:
        cookies['SESSDATA'] = SESSDATA
        cookies['SESSDATA']['domain'] = 'bilibili.com'
    global session
    session = aiohttp.ClientSession()
    if SESSDATA:
        session.cookie_jar.update_cookies(cookies)

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
    # 重写默认的未知命令处理，静默处理
    def _on_unknown_command(self, client, command):
        pass

    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage):
        pass

    def _on_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage):
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
        # 转换为OpenAI message格式并打印
        openai_message = convert_to_openai_message(message_data)
        print(json.dumps(openai_message, ensure_ascii=False))
        # 向输出端口发送请求
        asyncio.create_task(send_to_output_port(openai_message))

    def _on_gift(self, client: blivedm.BLiveClient, message: web_models.GiftMessage):
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
        # 转换为OpenAI message格式并打印
        openai_message = convert_to_openai_message(message_data)
        print(json.dumps(openai_message, ensure_ascii=False))
        # 向输出端口发送请求
        asyncio.create_task(send_to_output_port(openai_message))

    def _on_user_toast_v2(self, client: blivedm.BLiveClient, message: web_models.UserToastV2Message):
        pass

    def _on_super_chat(self, client: blivedm.BLiveClient, message: web_models.SuperChatMessage):
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
        # 转换为OpenAI message格式并打印
        openai_message = convert_to_openai_message(message_data)
        print(json.dumps(openai_message, ensure_ascii=False))
        # 向输出端口发送请求
        asyncio.create_task(send_to_output_port(openai_message))


if __name__ == '__main__':
    asyncio.run(main())
