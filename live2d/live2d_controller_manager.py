#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 动作联动管理器（优化版，薄封装层）

负责把 main.py / LangGraph 节点调用桥接到 Live2DMain 实际控制器：
  - 暴露统一异步 API (move_to_direction / open_mouth / close_mouth / set_mouth_state)
  - 内部委托给 Live2DMain，Live2DMain 已经通过 operation_lock 串行化所有 WebSocket 操作
  - 保留 Direction / Live2DConfig / ActionGenerator 等公共类型用于 main.py 导入
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any

logger = logging.getLogger("Live2DController")


class Direction(Enum):
    """方向枚举，合法值与 live2d_main.VALID_DIRECTIONS 保持一致"""
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
    """Live2D 配置类

    其中只有响应速度/动作平滑度/灵敏度被实际使用，
    其余保留用于未来扩展或兼容旧代码。"""
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
class EmotionState:
    """情绪状态类（保留，便于后续扩展表情联动）"""
    emotion: str = "普通"
    intensity: float = 1.0
    duration: float = 0.0
    timestamp: float = field(default_factory=time.time)


class Live2DControllerManager:
    """Live2D 控制器管理器（薄封装）

    直接把调用转发给 Live2DMain，不再维护独立的 action_queue / execute_action
    机制（之前实现中仅入队但从不消费，等于直通+死代码）。
    Live2DMain.operation_lock 已经保证所有 WebSocket 操作串行，无需重复加锁。
    """

    def __init__(self, config: Live2DConfig = None):
        self.config = config or Live2DConfig()
        self.controller: Any = None  # Live2DMain 实例
        self.is_connected = False
        self.current_emotion = EmotionState()
        self.current_direction: Direction = Direction.CENTER
        self.last_action_time = 0.0
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def connect(self, host: str = "localhost", port: int = 8001) -> bool:
        try:
            from live2d.live2d_main import Live2DMain
            self.controller = Live2DMain(host=host, port=port)
            if not await self.controller.connect():
                raise ConnectionError("无法连接到 VTube Studio 服务器")
            if not await self.controller.login():
                raise ConnectionError("VTube Studio 登录失败")
            if not await self.controller.initialize():
                raise ConnectionError("VTube Studio 初始化失败")

            await self.controller.set_mouth_state(False)
            self.controller.running = True
            # 启动后台任务：空闲呼吸 + 指令队列（持续监听模式）
            asyncio.create_task(self.controller.idle_movement())
            asyncio.create_task(self.controller.process_commands())

            self.is_connected = True
            logger.info(f"已连接到 VTube Studio ({host}:{port})")
            return True
        except Exception as e:
            logger.error(f"连接失败: {e}")
            self.is_connected = False
            self._last_error = str(e)
            return False

    async def disconnect(self) -> None:
        if self.controller:
            self.controller.running = False
            await self.controller.disconnect()
            self.is_connected = False
            logger.info("已断开与 VTube Studio 的连接")

    # ------------------------------------------------------------------
    # 方向/嘴巴 控制（供 LangGraph / main.py 调用）
    # ------------------------------------------------------------------

    async def move_to_direction(self, direction, duration: float = None) -> bool:
        """
        移动到指定方向。
        direction 接受 Direction 枚举或字符串；不区分大小写。
        """
        if not self.is_connected or self.controller is None:
            logger.warning("Live2D 未连接，跳过 move_to_direction")
            return False

        if isinstance(direction, Direction):
            dir_str = direction.value
        elif isinstance(direction, str):
            dir_str = direction.lower()
        else:
            logger.warning(f"方向参数类型无效: {type(direction)}")
            return False

        duration = (duration or self.config.response_speed) * self.config.sensitivity
        try:
            ok = await self.controller.move_to_direction(dir_str, duration)
            if ok:
                # 回写当前方向
                try:
                    self.current_direction = Direction(dir_str)
                except ValueError:
                    pass
                self.last_action_time = time.time()
            return ok
        except Exception as e:
            logger.error(f"执行方向动作失败: {e}")
            self._last_error = str(e)
            return False

    async def set_mouth_state(self, open: bool) -> bool:
        if not self.is_connected or self.controller is None:
            logger.warning("Live2D 未连接，跳过 set_mouth_state")
            return False
        try:
            ok = await self.controller.set_mouth_state(bool(open))
            if ok:
                self.last_action_time = time.time()
            return ok
        except Exception as e:
            logger.error(f"设置嘴巴状态失败: {e}")
            self._last_error = str(e)
            return False

    async def open_mouth(self) -> bool:
        return await self.set_mouth_state(True)

    async def close_mouth(self) -> bool:
        return await self.set_mouth_state(False)

    async def idle(self) -> bool:
        return await self.move_to_direction(Direction.CENTER, duration=0.6)

    # ------------------------------------------------------------------
    # 调试/监控接口
    # ------------------------------------------------------------------

    def get_current_state(self) -> Dict:
        return {
            "connected": self.is_connected,
            "current_direction": self.current_direction.value,
            "current_emotion": self.current_emotion.emotion,
            "emotion_intensity": self.current_emotion.intensity,
            "last_action_time": self.last_action_time,
            "last_error": self._last_error,
        }


class ActionGenerator:
    """动作生成器 - 根据回答内容生成动作建议（保留给上层使用）"""

    TONE_ACTION_MAP: Dict[str, Dict[str, str]] = {
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
        "普通": {"mouth": "close", "expression": "neutral"},
    }

    DIRECTION_KEYWORDS: Dict[str, Direction] = {
        "上": Direction.UP, "上面": Direction.UP, "抬头": Direction.UP, "向上": Direction.UP,
        "下": Direction.DOWN, "下面": Direction.DOWN, "低头": Direction.DOWN, "向下": Direction.DOWN,
        "左": Direction.LEFT, "左边": Direction.LEFT, "左看": Direction.LEFT,
        "右": Direction.RIGHT, "右边": Direction.RIGHT, "右看": Direction.RIGHT,
        "左上": Direction.UPLEFT, "右上": Direction.UPRIGHT,
        "左下": Direction.DOWNLEFT, "右下": Direction.DOWNRIGHT,
    }

    def __init__(self, config: Live2DConfig = None):
        self.config = config or Live2DConfig()

    def extract_tone(self, response: str) -> Optional[str]:
        if not response:
            return None
        if response.startswith("【") and "】" in response:
            tone = response[1:response.find("】")]
            if tone in self.TONE_ACTION_MAP:
                return tone
        return None

    def analyze_direction_from_content(self, content: str) -> Optional[Direction]:
        if not content or not self.config.eye_tracking_enabled:
            return None
        for keyword, direction in self.DIRECTION_KEYWORDS.items():
            if keyword in content:
                return direction
        return None

    def generate_actions(self, response: str) -> List[Dict]:
        """生成动作序列（供上层决定是否执行）"""
        actions: List[Dict] = []
        tone = self.extract_tone(response)
        if tone:
            mouth_action = self.TONE_ACTION_MAP.get(tone, {}).get("mouth", "close")
            actions.append({"type": "mouth", "action": mouth_action, "trigger": "tone"})
        content = response.split("】", 1)[1] if "】" in response else response
        direction = self.analyze_direction_from_content(content)
        if direction:
            actions.append({
                "type": "direction",
                "direction": direction.value,
                "duration": self.config.response_speed,
                "trigger": "content",
            })
        return actions


class EmotionAnalyzer:
    """基于关键词的基础情绪分析器（保留）"""

    EMOTION_PATTERNS = {
        "开心": [r"哈[哈哈]+", r"太棒了", r"好开心", r"真高兴", r"哈哈"],
        "惊喜": [r"哇", r"哇哦", r"真的吗", r"太意外了", r"没想到", r"惊喜"],
        "调皮": [r"哼", r"才不是", r"你猜", r"欸嘿", r"调皮", r"捉弄"],
        "撩拨": [r"心动了", r"喜欢", r"想你了", r"亲爱的"],
        "撒娇": [r"嘛", r"好不好", r"求求", r"啦~", r"呜"],
        "生气": [r"生气", r"过分", r"讨厌", r"可恶"],
        "严肃": [r"认真说", r"必须", r"一定", r"重要", r"严肃"],
        "难过": [r"伤心", r"难过", r"失落", r"可惜", r"遗憾"],
        "疑问": [r"为什么", r"怎么", r"是不是", r"吗", r"呢", r"？", r"\?"],
        "尴尬": [r"呃", r"那个", r"这个", r"其实", r"怎么说"],
        "感动": [r"感动", r"泪目", r"太棒了", r"谢谢你", r"感谢"],
        "积极": [r"加油", r"努力", r"一定可以", r"没问题", r"冲"],
        "急了": [r"快", r"快快", r"赶紧", r"快点", r"急"],
        "假装": [r"假装", r"其实", r"装作", r"不是真的"],
        "自言": [r"自言自语", r"自己", r"我呢", r"嗯"],
        "扮演慌张": [r"慌张", r"紧张", r"慌", r"乱"],
    }

    def analyze(self, text: str) -> Dict[str, Any]:
        scores: Dict[str, int] = {}
        for emotion, patterns in self.EMOTION_PATTERNS.items():
            for p in patterns:
                if re.search(p, text, re.IGNORECASE):
                    scores[emotion] = scores.get(emotion, 0) + 1
        if not scores:
            return {"emotion": "普通", "intensity": 0.0, "scores": {}}
        dominant = max(scores, key=scores.get)
        return {
            "emotion": dominant,
            "intensity": min(scores[dominant] / 3.0, 1.0),
            "scores": scores,
        }


def create_live2d_manager(config: Live2DConfig = None) -> Live2DControllerManager:
    """创建 Live2D 控制器管理器"""
    return Live2DControllerManager(config)
