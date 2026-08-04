#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
哔哩哔哩弹幕监听主程序

功能：
1. 常态监听指定直播间的弹幕信息
2. 每收到一条弹幕就转换为OPENAI格式并打印
3. 持续运行，实时处理新弹幕
"""

import asyncio
import json
import logging
import time
import uuid
import http.cookies
import os
import aiohttp
from typing import Dict, Any, List, Optional

# 导入blivedm库
import blivedm
import blivedm.models.web as web_models

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bili_main.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('BiliMain')

# 配置参数
class Config:
    """配置参数"""
    # 从config.json读取配置
    def __init__(self):
        config_path = os.path.join(os.path.dirname(__file__), 'config.json')
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                # 读取ROOM_IDS，取第一个房间ID
                room_ids = config_data.get('ROOM_IDS', '27885573')
                self.ROOM_ID = int(room_ids.split(',')[0].strip())
                # 读取SESSDATA
                self.SESSDATA = config_data.get('SESSDATA', '')
        except Exception as e:
            logger.error(f"读取配置文件失败: {str(e)}")
            # 使用默认值
            self.ROOM_ID = 27885573
            self.SESSDATA = ""
    
    # 直播间配置
    ROOM_ID = 27885573  # 直播间ID（默认值，会被__init__覆盖）
    
    # SESSDATA（可选，用于获取更高权限）
    SESSDATA = ""  # 默认值，会被__init__覆盖

# 初始化配置
config = Config()
# 重新赋值给类变量
Config.ROOM_ID = config.ROOM_ID
Config.SESSDATA = config.SESSDATA

def convert_to_openai_format(danmaku_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将单个弹幕转换为OPENAI格式
    
    参数:
        danmaku_data: 弹幕数据
    
    返回:
        OPENAI格式的消息
    """
    # 生成弹幕文本
    user_name = danmaku_data.get("user", {}).get("uname", "未知用户")
    content = danmaku_data.get("content", "")
    timestamp = danmaku_data.get("timestamp", 0)
    message_type = danmaku_data.get("message_type", "danmaku")
    
    # 格式化时间
    try:
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    except:
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    
    # 根据消息类型添加前缀
    if message_type == "danmaku":
        type_prefix = "[弹幕]"
    elif message_type == "gift":
        type_prefix = "[礼物]"
    elif message_type == "super_chat":
        type_prefix = "[SC]"
    else:
        type_prefix = "[其他]"
    
    danmaku_text = f"[{time_str}]{type_prefix} {user_name}: {content}"
    
    # 构建OPENAI格式
    openai_format = {
        "messages": [
            {
                "role": "system",
                "content": "你是一个专业的直播弹幕分析助手，能够分析直播弹幕内容，总结讨论的主要话题，并对热门问题进行回答。你需要基于弹幕内容，提供准确、全面、有条理的分析和回答。"
            },
            {
                "role": "user",
                "content": f"请分析以下直播消息内容，总结讨论的主要话题，并对热门问题进行回答：\n{danmaku_text}"
            }
        ],
        "session_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "danmaku_count": 1,
        "timestamp": time.time()
    }
    
    return openai_format

class RealTimeDanmakuHandler(blivedm.BaseHandler):
    """实时弹幕处理器"""
    
    def _on_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage):
        """处理弹幕消息"""
        danmaku_data = {
            "user": {
                "uname": message.uname,
                "uid": message.uid
            },
            "content": message.msg,
            "timestamp": time.time(),
            "message_type": "danmaku"
        }
        logger.info(f"收到弹幕: {message.uname}: {message.msg}")
        
        # 转换为OpenAI格式
        openai_format = convert_to_openai_format(danmaku_data)
        
        # 打印结果
        print("\n=== 消息分析 ===")
        print("收到1条弹幕")
        print("OpenAI格式消息:")
        print(json.dumps(openai_format, ensure_ascii=False, indent=2))
        print("\n" + "="*50 + "\n")
    
    def _on_gift(self, client: blivedm.BLiveClient, message: web_models.GiftMessage):
        """处理礼物消息"""
        gift_data = {
            "user": {
                "uname": message.uname,
                "uid": message.uid
            },
            "content": f"赠送了{message.num}个{message.gift_name}",
            "gift_name": message.gift_name,
            "num": message.num,
            "timestamp": time.time(),
            "message_type": "gift"
        }
        logger.info(f"收到礼物: {message.uname} 赠送了{message.num}个{message.gift_name}")
        
        # 转换为OpenAI格式
        openai_format = convert_to_openai_format(gift_data)
        
        # 打印结果
        print("\n=== 消息分析 ===")
        print("收到1条礼物消息")
        print("OpenAI格式消息:")
        print(json.dumps(openai_format, ensure_ascii=False, indent=2))
        print("\n" + "="*50 + "\n")
    
    def _on_super_chat(self, client: blivedm.BLiveClient, message: web_models.SuperChatMessage):
        """处理超级聊天消息"""
        sc_data = {
            "user": {
                "uname": message.uname,
                "uid": message.uid
            },
            "content": message.message,
            "price": message.price,
            "timestamp": time.time(),
            "message_type": "super_chat"
        }
        logger.info(f"收到SC: {message.uname} ({message.price}元): {message.message}")
        
        # 转换为OpenAI格式
        openai_format = convert_to_openai_format(sc_data)
        
        # 打印结果
        print("\n=== 消息分析 ===")
        print("收到1条超级聊天消息")
        print("OpenAI格式消息:")
        print(json.dumps(openai_format, ensure_ascii=False, indent=2))
        print("\n" + "="*50 + "\n")

def init_session():
    """初始化session"""
    cookies = http.cookies.SimpleCookie()
    if Config.SESSDATA:
        cookies['SESSDATA'] = Config.SESSDATA
        cookies['SESSDATA']['domain'] = 'bilibili.com'

    session = aiohttp.ClientSession()
    if Config.SESSDATA:
        session.cookie_jar.update_cookies(cookies)
    
    return session

async def start_real_time_listener(room_id: int):
    """
    开始实时监听弹幕
    
    参数:
        room_id: 直播间ID
    """
    logger.info(f"开始实时监听直播间 {room_id} 的弹幕")
    
    # 初始化session
    session = init_session()
    
    # 创建处理器
    handler = RealTimeDanmakuHandler()
    
    # 创建客户端
    client = blivedm.BLiveClient(room_id, session=session)
    client.set_handler(handler)
    
    try:
        # 开始连接
        client.start()
        logger.info("客户端已启动，开始实时监听弹幕")
        print(f"\n=== 实时弹幕监听系统 ===")
        print(f"正在监听直播间: {room_id}")
        print("每收到一条消息就会转换为OpenAI格式并打印")
        print("按 Ctrl+C 停止\n")
        
        # 持续运行
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("用户中断程序")
        print("\n程序已停止")
    except Exception as e:
        logger.error(f"监听过程中发生错误: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        print(f"\n发生错误: {str(e)}")
    finally:
        # 停止客户端和session
        client.stop()
        await client.join()
        await client.stop_and_close()
        await session.close()
        logger.info("客户端已停止")

def main():
    """
    主函数
    """
    # 启动实时监听
    asyncio.run(start_real_time_listener(Config.ROOM_ID))

if __name__ == "__main__":
    main()
