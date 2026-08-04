#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 动作联动管理器
实现大模型回答过程中的动作控制机制
"""

import asyncio
import logging
import time
import re
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import threading

logger = logging.getLogger("Live2DController")

class ActionType(Enum):
    """动作类型枚举"""
    MOVE_TO_DIRECTION = "move_to_direction"
    SET_MOUTH = "set_mouth"
    OPEN_MOUTH = "open_mouth"
    CLOSE_MOUTH = "close_mouth"
    IDLE = "idle"
    EXPRESSION = "expression"

class Direction(Enum):
    """方向枚举"""
    CENTER = "center"
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    UPLEFT = "upleft"
    UPRIGHT = "upright"
    DOWNLEFT = "downleft"
    DOWNRIGHT = "downright"

@dataclass
class Live2DConfig:
    """Live2D 配置类"""
    sensitivity: float = 1.0
    response_speed: float = 1.5
    motion_smoothness: float = 0.8
    eye_tracking_enabled: bool = True
    expression_enabled: bool = True
    max_action_queue_size: int = 10
    action_timeout: float = 5.0
    enable_error_recovery: bool = True
    log_level: str = "INFO"

@dataclass
class ActionState:
    """动作状态类"""
    timestamp: float = field(default_factory=time.time)
    action_type: ActionType = None
    direction: Direction = None
    duration: float = 1.5
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    error: Optional[str] = None

@dataclass
class EmotionState:
    """情绪状态类"""
    emotion: str = "普通"
    intensity: float = 1.0
    duration: float = 0.0
    timestamp: float = field(default_factory=time.time)

class Live2DControllerManager:
    """Live2D 控制器管理器"""
    
    def __init__(self, config: Live2DConfig = None):
        """
        初始化 Live2D 控制器管理器
        
        Args:
            config: Live2D 配置对象
        """
        self.config = config or Live2DConfig()
        self.controller = None
        self.is_connected = False
        self.action_queue: List[ActionState] = []
        self.current_emotion = EmotionState()
        self.current_direction = Direction.CENTER
        self.last_action_time = 0
        self.action_history: List[ActionState] = []
        self._lock = threading.Lock()
        self._error_count = 0
        self._max_errors = 5
        
        self._setup_logging()
    
    def _setup_logging(self):
        """设置日志"""
        logging.getLogger("Live2DController").setLevel(
            getattr(logging, self.config.log_level)
        )
    
    async def connect(self, host: str = "localhost", port: int = 8001):
        """
        连接到 Live2D 控制器
        
        Args:
            host: 主机地址
            port: 端口号
        """
        try:
            from live2d.live2d_main import Live2DMain
            
            self.controller = Live2DMain(host=host, port=port)
            
            if not await self.controller.connect():
                raise ConnectionError("无法连接到VTube Studio服务器")
            
            if not await self.controller.login():
                raise ConnectionError("VTube Studio登录失败")
            
            if not await self.controller.initialize():
                raise ConnectionError("VTube Studio初始化失败")
            
            await self.controller.set_mouth_state(False)
            self.controller.running = True
            
            self.is_connected = True
            logger.info(f"已连接到VTube Studio ({host}:{port})")
            
            return True
        
        except Exception as e:
            logger.error(f"连接失败: {e}")
            self.is_connected = False
            return False
    
    async def disconnect(self):
        """断开连接"""
        if self.controller:
            self.controller.running = False
            await self.controller.disconnect()
            self.is_connected = False
            logger.info("已断开与VTube Studio的连接")
    
    def _enqueue_action(self, action: ActionState):
        """
        将动作添加到队列
        
        Args:
            action: 动作状态对象
        """
        with self._lock:
            if len(self.action_queue) >= self.config.max_action_queue_size:
                self.action_queue.pop(0)
            self.action_queue.append(action)
            self.action_history.append(action)
    
    async def execute_action(self, action: ActionState) -> bool:
        """
        执行动作
        
        Args:
            action: 动作状态对象
        
        Returns:
            是否成功执行
        """
        if not self.is_connected or not self.controller:
            logger.warning("控制器未连接，跳过动作")
            return False
        
        try:
            action.status = "executing"
            
            if action.action_type == ActionType.MOVE_TO_DIRECTION:
                # 处理方向参数，支持字符串或Direction枚举
                if isinstance(action.direction, Direction):
                    direction_str = action.direction.value
                elif isinstance(action.direction, str):
                    direction_str = action.direction
                else:
                    direction_str = "center"
                
                await self.controller.move_to_direction(
                    direction_str,
                    action.duration
                )
            
            elif action.action_type == ActionType.SET_MOUTH:
                mouth_state = action.parameters.get("open", False)
                await self.controller.set_mouth_state(mouth_state)
            
            elif action.action_type == ActionType.OPEN_MOUTH:
                await self.controller.set_mouth_state(True)
            
            elif action.action_type == ActionType.CLOSE_MOUTH:
                await self.controller.set_mouth_state(False)
            
            elif action.action_type == ActionType.IDLE:
                await self.controller.move_to_direction("center", 1.0)
            
            action.status = "completed"
            self.last_action_time = time.time()
            self._error_count = 0
            logger.debug(f"动作执行成功: {action.action_type.value}")
            return True
        
        except Exception as e:
            action.status = "failed"
            action.error = str(e)
            self._error_count += 1
            logger.error(f"动作执行失败: {e}")
            
            if self.config.enable_error_recovery and self._error_count >= self._max_errors:
                logger.warning("错误次数过多，尝试恢复...")
                await self._error_recovery()
            
            return False
    
    async def _error_recovery(self):
        """错误恢复"""
        try:
            if self.controller:
                await self.controller.move_to_direction("center", 1.0)
                await self.controller.set_mouth_state(False)
            self._error_count = 0
            logger.info("错误恢复完成")
        except Exception as e:
            logger.error(f"错误恢复失败: {e}")
    
    async def move_to_direction(self, direction: Direction, duration: float = None):
        """
        移动到指定方向
        
        Args:
            direction: 方向
            duration: 持续时间
        """
        duration = duration or self.config.response_speed
        
        action = ActionState(
            action_type=ActionType.MOVE_TO_DIRECTION,
            direction=direction,
            duration=duration * self.config.sensitivity
        )
        
        self._enqueue_action(action)
        self.current_direction = direction
        await self.execute_action(action)
    
    async def set_mouth_state(self, open: bool):
        """
        设置嘴巴状态
        
        Args:
            open: 是否张开
        """
        action = ActionState(
            action_type=ActionType.SET_MOUTH if open else ActionType.CLOSE_MOUTH,
            parameters={"open": open}
        )
        
        self._enqueue_action(action)
        await self.execute_action(action)
    
    async def open_mouth(self):
        """张开嘴巴"""
        action = ActionState(action_type=ActionType.OPEN_MOUTH)
        self._enqueue_action(action)
        await self.execute_action(action)
    
    async def close_mouth(self):
        """关闭嘴巴"""
        action = ActionState(action_type=ActionType.CLOSE_MOUTH)
        self._enqueue_action(action)
        await self.execute_action(action)
    
    async def idle(self):
        """恢复常态"""
        action = ActionState(action_type=ActionType.IDLE)
        self._enqueue_action(action)
        await self.execute_action(action)
    
    def get_action_history(self, limit: int = 10) -> List[Dict]:
        """
        获取动作历史
        
        Args:
            limit: 返回的最大数量
        
        Returns:
            动作历史列表
        """
        with self._lock:
            history = self.action_history[-limit:]
            return [
                {
                    "timestamp": a.timestamp,
                    "type": a.action_type.value,
                    "direction": a.direction.value if a.direction else None,
                    "duration": a.duration,
                    "status": a.status,
                    "error": a.error
                }
                for a in history
            ]
    
    def get_current_state(self) -> Dict:
        """
        获取当前状态
        
        Returns:
            当前状态字典
        """
        return {
            "connected": self.is_connected,
            "current_direction": self.current_direction.value,
            "current_emotion": self.current_emotion.emotion,
            "emotion_intensity": self.current_emotion.intensity,
            "last_action_time": self.last_action_time,
            "queue_size": len(self.action_queue),
            "error_count": self._error_count
        }

class ActionGenerator:
    """动作生成器 - 根据回答内容生成动作"""
    
    TONE_ACTION_MAP = {
        "开心": {"mouth": "open", "expression": "happy"},
        "惊喜": {"mouth": "open", "expression": "surprised"},
        "调皮": {"mouth": "open", "expression": "playful"},
        "撩拨": {"mouth": "open", "expression": "flirty"},
        "撒娇": {"mouth": "open", "expression": "coquettish"},
        "生气": {"mouth": "close", "expression": "angry"},
        "严肃": {"mouth": "close", "expression": "serious"},
        "难过": {"mouth": "close", "expression": "sad"},
        "疑问": {"mouth": "close", "expression": "curious"},
        "尴尬": {"mouth": "close", "expression": "awkward"},
        "感动": {"mouth": "close", "expression": "touched"},
        "积极": {"mouth": "open", "expression": "positive"},
        "急了": {"mouth": "open", "expression": "anxious"},
        "假装": {"mouth": "close", "expression": "pretending"},
        "自言": {"mouth": "close", "expression": "self_talk"},
        "扮演慌张": {"mouth": "open", "expression": "panicked"},
        "普通": {"mouth": "close", "expression": "neutral"}
    }
    
    DIRECTION_KEYWORDS = {
        "上": Direction.UP,
        "上面": Direction.UP,
        "抬头": Direction.UP,
        "向上": Direction.UP,
        "下": Direction.DOWN,
        "下面": Direction.DOWN,
        "低头": Direction.DOWN,
        "向下": Direction.DOWN,
        "左": Direction.LEFT,
        "左边": Direction.LEFT,
        "左看": Direction.LEFT,
        "右": Direction.RIGHT,
        "右边": Direction.RIGHT,
        "右看": Direction.RIGHT,
        "左上": Direction.UPLEFT,
        "右上": Direction.UPRIGHT,
        "左下": Direction.DOWNLEFT,
        "右下": Direction.DOWNRIGHT
    }
    
    def __init__(self, config: Live2DConfig = None):
        """
        初始化动作生成器
        
        Args:
            config: Live2D 配置对象
        """
        self.config = config or Live2DConfig()
        self.emotion_analyzer = EmotionAnalyzer()
    
    def extract_tone(self, response: str) -> Optional[str]:
        """
        从回答中提取语气
        
        Args:
            response: 回答文本
        
        Returns:
            语气字符串
        """
        if not response:
            return None
        
        if response.startswith("【") and "】" in response:
            end = response.find("】")
            tone = response[1:end]
            
            if tone in self.TONE_ACTION_MAP:
                return tone
        
        return None
    
    def analyze_direction_from_content(self, content: str) -> Optional[Direction]:
        """
        从内容分析目光方向
        
        Args:
            content: 内容文本
        
        Returns:
            方向
        """
        if not content or not self.config.eye_tracking_enabled:
            return None
        
        for keyword, direction in self.DIRECTION_KEYWORDS.items():
            if keyword in content:
                return direction
        
        return None
    
    def generate_actions(self, response: str) -> List[Dict]:
        """
        根据回答生成动作序列
        
        Args:
            response: 回答文本
        
        Returns:
            动作序列列表
        """
        actions = []
        
        tone = self.extract_tone(response)
        if tone:
            tone_info = self.TONE_ACTION_MAP.get(tone, {})
            
            mouth_action = tone_info.get("mouth", "close")
            actions.append({
                "type": "mouth",
                "action": "open" if mouth_action == "open" else "close",
                "trigger": "tone"
            })
        
        content = response
        if "】" in content:
            content = content.split("】", 1)[1]
        
        direction = self.analyze_direction_from_content(content)
        if direction:
            actions.append({
                "type": "direction",
                "direction": direction.value,
                "duration": self.config.response_speed,
                "trigger": "content"
            })
        
        return actions

class EmotionAnalyzer:
    """情绪分析器"""
    
    EMOTION_PATTERNS = {
        "开心": [r"哈[哈哈]+", r"太棒了", r"好开心", r"真高兴", r"哈哈", r"♪", r"~\(≧▽≦)/~"],
        "惊喜": [r"哇", r"哇哦", r"真的吗", r"太意外了", r"没想到", r"惊喜"],
        "调皮": [r"哼", r"才不是", r"你猜", r"欸嘿", r"调皮", r"捉弄"],
        "撩拨": [r"心动了", r"喜欢", r"想你了", r" sweet", r"亲爱的"],
        "撒娇": [r"嘛", r"好不好", r"求求", r"啦~", r"呜", r"~/~"],
        "生气": [r"哼", r"生气", r"过分", r"讨厌", r"可恶", r"!!"],
        "严肃": [r"认真说", r"必须", r"一定", r"重要", r"严肃"],
        "难过": [r"伤心", r"难过", r"失落", r"可惜", r"遗憾"],
        "疑问": [r"为什么", r"怎么", r"是不是", r"吗", r"呢", r"？", r"?"],
        "尴尬": [r"呃", r"那个", r"这个", r"其实", r"怎么说"],
        "感动": [r"感动", r"泪目", r"太棒了", r"谢谢你", r"感谢"],
        "积极": [r"加油", r"努力", r"一定可以", r"没问题", r"冲"],
        "急了": [r"快", r"快快", r"赶紧", r"快点", r"急"],
        "假装": [r"假装", r"其实", r"装作", r"不是真的"],
        "自言": [r"自言自语", r"自己", r"我呢", r"嗯"],
        "扮演慌张": [r"慌张", r"紧张", r"慌", r"乱"]
    }
    
    def analyze(self, text: str) -> Dict[str, Any]:
        """
        分析文本中的情绪
        
        Args:
            text: 文本内容
        
        Returns:
            情绪分析结果
        """
        emotion_scores = {}
        
        for emotion, patterns in self.EMOTION_PATTERNS.items():
            score = 0
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    score += 1
            if score > 0:
                emotion_scores[emotion] = score
        
        if not emotion_scores:
            return {"emotion": "普通", "intensity": 0.0, "scores": {}}
        
        dominant_emotion = max(emotion_scores, key=emotion_scores.get)
        max_score = emotion_scores[dominant_emotion]
        
        return {
            "emotion": dominant_emotion,
            "intensity": min(max_score / 3.0, 1.0),
            "scores": emotion_scores
        }

def create_live2d_manager(config: Live2DConfig = None) -> Live2DControllerManager:
    """
    创建 Live2D 控制器管理器
    
    Args:
        config: 配置对象
    
    Returns:
        Live2D 控制器管理器实例
    """
    return Live2DControllerManager(config)
