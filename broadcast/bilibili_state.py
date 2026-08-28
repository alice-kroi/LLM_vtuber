#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bilibili 状态管理模块

基于 bilibili 直播弹幕数据，实现符合 LangGraph 框架的状态管理系统。
"""

from typing_extensions import TypedDict
from typing import Optional, Dict, List, Any
import json
import os
import asyncio
from blivedm import BLiveClient
from blivedm.models.web import DanmakuMessage, GiftMessage, SuperChatMessage, UserToastV2Message
import aiohttp
import http.cookies


class BilibiliState(TypedDict):
    """
    Bilibili 状态定义
    
    字段说明：
    - function_id: 功能ID
    - state: 状态
    - state_data: 状态数据
    - last_updated: 最后更新时间
    - is_valid: 是否有效
    """
    function_id: str  # 功能ID
    state: str  # 状态
    state_data: Dict[str, Any]  # 状态数据
    last_updated: str  # 最后更新时间
    is_valid: bool  # 是否有效


class DanmakuData(TypedDict):
    """
    弹幕数据定义
    """
    type: str  # 消息类型
    room_id: int  # 房间ID
    user: Dict[str, Any]  # 用户信息
    content: str  # 消息内容
    timestamp: int  # 时间戳
    color: int  # 颜色
    font_size: int  # 字体大小
    mode: int  # 模式
    medal: Dict[str, Any]  # 勋章信息


class GiftData(TypedDict):
    """
    礼物数据定义
    """
    type: str  # 消息类型
    room_id: int  # 房间ID
    user: Dict[str, Any]  # 用户信息
    gift: Dict[str, Any]  # 礼物信息
    timestamp: int  # 时间戳
    medal: Dict[str, Any]  # 勋章信息


class SuperChatData(TypedDict):
    """
    醒目留言数据定义
    """
    type: str  # 消息类型
    room_id: int  # 房间ID
    user: Dict[str, Any]  # 用户信息
    message: str  # 留言内容
    price: int  # 价格
    start_time: int  # 开始时间
    end_time: int  # 结束时间
    time: int  # 持续时间
    background: Dict[str, Any]  # 背景信息
    gift: Dict[str, Any]  # 礼物信息
    medal: Dict[str, Any]  # 勋章信息


class BilibiliStateData(TypedDict):
    """
    Bilibili 状态数据定义
    """
    room_id: int  # 房间ID
    sessdata: str  # SESSDATA
    danmakus: List[DanmakuData]  # 弹幕数据
    gifts: List[GiftData]  # 礼物数据
    super_chats: List[SuperChatData]  # 醒目留言数据
    total_danmakus: int  # 总弹幕数
    total_gifts: int  # 总礼物数
    total_super_chats: int  # 总醒目留言数
    last_danmaku_time: str  # 最后弹幕时间
    last_gift_time: str  # 最后礼物时间
    last_super_chat_time: str  # 最后醒目留言时间


def create_initial_bilibili_state() -> BilibiliState:
    """
    创建初始的 Bilibili 状态
    
    Returns:
        初始的 BilibiliState 对象
    """
    import datetime
    now = datetime.datetime.now().isoformat()
    
    return BilibiliState(
        function_id="bilibili_state",
        state="active",
        state_data=BilibiliStateData(
            room_id=0,
            sessdata="",
            danmakus=[],
            gifts=[],
            super_chats=[],
            total_danmakus=0,
            total_gifts=0,
            total_super_chats=0,
            last_danmaku_time="",
            last_gift_time="",
            last_super_chat_time=""
        ),
        last_updated=now,
        is_valid=True
    )


def load_bilibili_config(config_file: str = "config.json") -> Dict[str, str]:
    """
    加载 Bilibili 配置
    
    SESSDATA 优先从环境变量 BILIBILI_SESSDATA 读取，其次从配置文件读取。
    
    Args:
        config_file: 配置文件路径
    
    Returns:
        配置字典
    """
    config_path = os.path.join(os.path.dirname(__file__), config_file)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        # 环境变量优先
        env_sessdata = os.getenv("BILIBILI_SESSDATA", "")
        if env_sessdata:
            config["SESSDATA"] = env_sessdata
        
        return config
    except Exception as e:
        print(f"加载配置文件失败: {e}")
        # 即使配置文件不存在，也尝试从环境变量读取
        env_sessdata = os.getenv("BILIBILI_SESSDATA", "")
        if env_sessdata:
            return {"SESSDATA": env_sessdata}
        return {}


class BilibiliHandler:
    """
Bilibili 弹幕处理器
    """
    
    def __init__(self, state: BilibiliState, on_new_danmaku=None):
        """
        初始化处理器
        
        Args:
            state: Bilibili 状态
            on_new_danmaku: 新弹幕回调函数
        """
        self.state = state
        self.on_new_danmaku = on_new_danmaku
    
    def handle(self, client: BLiveClient, command):
        """
        处理各种类型的消息
        
        Args:
            client: BLiveClient 实例
            command: 消息命令
        """
        import logging
        import asyncio
        logger = logging.getLogger("BilibiliHandler")
        
        cmd = command.get('cmd')
        logger.debug(f"收到消息: {cmd}")
        
        if cmd == 'DANMU_MSG':
            # 处理弹幕消息
            from blivedm.models.web import DanmakuMessage
            try:
                # 打印 command 结构，以便理解数据格式
                logger.debug(f"DANMU_MSG 命令结构: {command}")
                
                # 尝试不同的字段名
                info = command.get('data', [])
                if not info:
                    info = command.get('info', [])
                if not info:
                    info = command.get('danmaku', [])
                
                if not info:
                    logger.error("弹幕消息中没有找到数据字段")
                    return
                
                message = DanmakuMessage.from_command(info)
                # 使用 create_task 异步处理，避免阻塞
                asyncio.create_task(self._on_danmaku(client, message))
            except Exception as e:
                logger.error(f"处理弹幕消息失败: {e}")
                import traceback
                traceback.print_exc()
        elif cmd == 'SEND_GIFT':
            # 处理礼物消息
            from blivedm.models.web import GiftMessage
            try:
                message = GiftMessage.from_command(command)
                logger.info(f"收到礼物: {message.uname} 赠送了 {message.gift_name} x {message.num}")
                # 使用 create_task 异步处理，避免阻塞
                asyncio.create_task(self._on_gift(client, message))
            except Exception as e:
                logger.error(f"处理礼物消息失败: {e}")
        elif cmd == 'SUPER_CHAT_MESSAGE':
            # 处理醒目留言消息
            from blivedm.models.web import SuperChatMessage
            try:
                message = SuperChatMessage.from_command(command)
                logger.info(f"收到醒目留言: {message.uname} ({message.price}元): {message.message}")
                # 使用 create_task 异步处理，避免阻塞
                asyncio.create_task(self._on_super_chat(client, message))
            except Exception as e:
                logger.error(f"处理醒目留言消息失败: {e}")
        # 其他类型的消息可以在这里添加处理逻辑
        else:
            logger.debug(f"收到其他类型消息: {cmd}")
    
    def on_client_stopped(self, client: BLiveClient, exc):
        """
        客户端停止时调用
        
        Args:
            client: BLiveClient 实例
            exc: 异常信息
        """
        pass
    
    async def _on_danmaku(self, client: BLiveClient, message: DanmakuMessage):
        """
        处理弹幕消息
        """
        import datetime
        danmaku_data = DanmakuData(
            type="danmaku",
            room_id=client.room_id,
            user={
                "uid": message.uid,
                "uname": message.uname,
                "admin": message.admin,
                "vip": message.vip,
                "svip": message.svip,
                "user_level": message.user_level
            },
            content=message.msg,
            timestamp=message.timestamp,
            color=message.color,
            font_size=message.font_size,
            mode=message.mode,
            medal={
                "level": message.medal_level,
                "name": message.medal_name,
                "room_id": message.medal_room_id,
                "anchor_name": message.runame
            }
        )
        
        # 更新状态
        self.state["state_data"]["danmakus"].append(danmaku_data)
        self.state["state_data"]["total_danmakus"] += 1
        self.state["state_data"]["last_danmaku_time"] = datetime.datetime.now().isoformat()
        self.state["last_updated"] = datetime.datetime.now().isoformat()
        
        # 调用新弹幕回调函数
        if self.on_new_danmaku:
            try:
                await self.on_new_danmaku(danmaku_data)
            except Exception as e:
                pass
    
    async def _on_gift(self, client: BLiveClient, message: GiftMessage):
        """
        处理礼物消息
        """
        import datetime
        try:
            gift_data = GiftData(
                type="gift",
                room_id=client.room_id,
                user={
                    "uid": getattr(message, 'uid', 0),
                    "uname": getattr(message, 'uname', '未知用户'),
                    "guard_level": getattr(message, 'guard_level', 0)
                },
                gift={
                    "name": getattr(message, 'gift_name', '') or getattr(message, 'giftName', ''),
                    "id": getattr(message, 'gift_id', 0) or getattr(message, 'giftId', 0),
                    "type": getattr(message, 'gift_type', 0),
                    "num": getattr(message, 'num', 0),
                    "price": getattr(message, 'price', 0),
                    "total_coin": getattr(message, 'total_coin', 0),
                    "coin_type": getattr(message, 'coin_type', '')
                },
                timestamp=getattr(message, 'timestamp', 0),
                medal={
                    "level": getattr(message, 'medal_level', 0),
                    "name": getattr(message, 'medal_name', ''),
                    "room_id": getattr(message, 'medal_room_id', 0),
                    "anchor_id": getattr(message, 'medal_ruid', 0)
                }
            )
            
            # 更新状态
            self.state["state_data"]["gifts"].append(gift_data)
            self.state["state_data"]["total_gifts"] += 1
            self.state["state_data"]["last_gift_time"] = datetime.datetime.now().isoformat()
            self.state["last_updated"] = datetime.datetime.now().isoformat()
        except Exception as e:
            import logging
            logger = logging.getLogger("BilibiliHandler")
            logger.error(f"处理礼物消息失败: {e}")
    
    async def _on_super_chat(self, client: BLiveClient, message: SuperChatMessage):
        """
        处理醒目留言消息
        """
        import datetime
        super_chat_data = SuperChatData(
            type="super_chat",
            room_id=client.room_id,
            user={
                "uid": message.uid,
                "uname": message.uname,
                "guard_level": message.guard_level,
                "user_level": message.user_level
            },
            message=message.message,
            price=message.price,
            start_time=message.start_time,
            end_time=message.end_time,
            time=message.time,
            background={
                "color": message.background_color,
                "bottom_color": message.background_bottom_color,
                "price_color": message.background_price_color,
                "image": message.background_image,
                "icon": message.background_icon
            },
            gift={
                "id": message.gift_id,
                "name": message.gift_name
            },
            medal={
                "level": message.medal_level,
                "name": message.medal_name,
                "room_id": message.medal_room_id,
                "anchor_id": message.medal_ruid
            }
        )
        
        # 更新状态
        self.state["state_data"]["super_chats"].append(super_chat_data)
        self.state["state_data"]["total_super_chats"] += 1
        self.state["state_data"]["last_super_chat_time"] = datetime.datetime.now().isoformat()
        self.state["last_updated"] = datetime.datetime.now().isoformat()


async def bilibili_danmaku_node(state: Dict[str, Any], on_new_danmaku=None) -> Dict[str, Any]:
    """
    Bilibili 弹幕处理节点
    
    加载配置，连接到 Bilibili 直播房间，获取弹幕数据，并更新状态
    
    Args:
        state: 主状态
        on_new_danmaku: 新弹幕回调函数
    
    Returns:
        更新后的主状态
    """
    import logging
    import datetime
    
    logger = logging.getLogger("bilibili_danmaku_node")
    logger.info("执行 Bilibili 弹幕处理节点")
    
    try:
        # 加载配置
        config = load_bilibili_config()
        room_id = int(config.get("ROOM_IDS", "0"))
        sessdata = config.get("SESSDATA", "")
        
        if not room_id:
            logger.error("未配置房间ID")
            return state
        
        # 初始化 Bilibili 状态
        if "sub_function_state" not in state:
            state["sub_function_state"] = {}
        
        if "bilibili_state" not in state["sub_function_state"]:
            state["sub_function_state"]["bilibili_state"] = create_initial_bilibili_state()
        
        bilibili_state = state["sub_function_state"]["bilibili_state"]
        bilibili_state["state_data"]["room_id"] = room_id
        bilibili_state["state_data"]["sessdata"] = sessdata
        bilibili_state["last_updated"] = datetime.datetime.now().isoformat()
        
        # 初始化会话
        cookies = http.cookies.SimpleCookie()
        if sessdata:
            cookies['SESSDATA'] = sessdata
            cookies['SESSDATA']['domain'] = 'bilibili.com'
        
        session = aiohttp.ClientSession()
        if sessdata:
            session.cookie_jar.update_cookies(cookies)
        
        # 创建客户端和处理器
        client = BLiveClient(room_id, session=session)
        handler = BilibiliHandler(bilibili_state, on_new_danmaku=on_new_danmaku)
        client.set_handler(handler)
        
        # 启动客户端
        client.start()
        logger.info(f"开始获取直播间 {room_id} 的弹幕数据")
        
        # 持续运行，直到被取消
        try:
            # 这里使用一个无限循环，直到任务被取消
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            # 任务被取消，停止客户端
            logger.info("Bilibili 弹幕处理节点被取消")
        finally:
            # 停止客户端
            await client.stop_and_close()
            await session.close()
            
            logger.info(f"获取到 {bilibili_state['state_data']['total_danmakus']} 条弹幕，{bilibili_state['state_data']['total_gifts']} 个礼物，{bilibili_state['state_data']['total_super_chats']} 条醒目留言")
        
        return state
        
    except Exception as e:
        logger.error(f"Bilibili 弹幕处理节点失败: {e}")
        import traceback
        traceback.print_exc()
        return state


def update_bilibili_state(state: Dict[str, Any], bilibili_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    更新 Bilibili 状态
    
    Args:
        state: 主状态
        bilibili_data: Bilibili 数据
    
    Returns:
        更新后的主状态
    """
    import datetime
    
    if "sub_function_state" not in state:
        state["sub_function_state"] = {}
    
    if "bilibili_state" not in state["sub_function_state"]:
        state["sub_function_state"]["bilibili_state"] = create_initial_bilibili_state()
    
    bilibili_state = state["sub_function_state"]["bilibili_state"]
    bilibili_state["state_data"].update(bilibili_data)
    bilibili_state["last_updated"] = datetime.datetime.now().isoformat()
    
    return state


def get_bilibili_state(state: Dict[str, Any]) -> Optional[BilibiliState]:
    """
    获取 Bilibili 状态
    
    Args:
        state: 主状态
    
    Returns:
        Bilibili 状态或 None
    """
    if "sub_function_state" in state and "bilibili_state" in state["sub_function_state"]:
        return state["sub_function_state"]["bilibili_state"]
    return None


def get_recent_danmakus(state: Dict[str, Any], limit: int = 10) -> List[DanmakuData]:
    """
    获取最近的弹幕
    
    Args:
        state: 主状态
        limit: 限制数量
    
    Returns:
        最近的弹幕列表
    """
    bilibili_state = get_bilibili_state(state)
    if not bilibili_state:
        return []
    
    danmakus = bilibili_state["state_data"].get("danmakus", [])
    return danmakus[-limit:] if len(danmakus) > limit else danmakus


def get_recent_gifts(state: Dict[str, Any], limit: int = 10) -> List[GiftData]:
    """
    获取最近的礼物
    
    Args:
        state: 主状态
        limit: 限制数量
    
    Returns:
        最近的礼物列表
    """
    bilibili_state = get_bilibili_state(state)
    if not bilibili_state:
        return []
    
    gifts = bilibili_state["state_data"].get("gifts", [])
    return gifts[-limit:] if len(gifts) > limit else gifts


def get_recent_super_chats(state: Dict[str, Any], limit: int = 10) -> List[SuperChatData]:
    """
    获取最近的醒目留言
    
    Args:
        state: 主状态
        limit: 限制数量
    
    Returns:
        最近的醒目留言列表
    """
    bilibili_state = get_bilibili_state(state)
    if not bilibili_state:
        return []
    
    super_chats = bilibili_state["state_data"].get("super_chats", [])
    return super_chats[-limit:] if len(super_chats) > limit else super_chats