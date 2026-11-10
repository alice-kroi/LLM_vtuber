# ============================================================
# persona/state.py — 动态状态机（PAD 三维 + 资源条）
# ============================================================
# 借鉴 Generative Agents + PAD 模型（Mehrabian 1995）
# 职责：维护 AI 的实时情绪 + 资源状态 + 事件触发更新
# ============================================================

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# ============================================================
# PAD 模型（Mehrabian 1995）
# P = Pleasure（愉悦度）, A = Arousal（唤醒度）, D = Dominance（支配度）
# 每维范围 [-1.0, +1.0]
# ============================================================

@dataclass
class PAD:
    """PAD 情绪三维向量"""
    P: float = 0.5
    A: float = 0.3
    D: float = 0.3

    def __post_init__(self):
        self._clamp()

    def _clamp(self):
        self.P = max(-1.0, min(1.0, self.P))
        self.A = max(-1.0, min(1.0, self.A))
        self.D = max(-1.0, min(1.0, self.D))

    def __add__(self, other: 'PAD') -> 'PAD':
        return PAD(
            P=self.P + other.P,
            A=self.A + other.A,
            D=self.D + other.D,
        )

    def scale(self, factor: float) -> 'PAD':
        """按比例缩放（用于衰减）"""
        return PAD(P=self.P * factor, A=self.A * factor, D=self.D * factor)

    def lerp(self, target: 'PAD', t: float) -> 'PAD':
        """线性插值（向目标回归）"""
        return PAD(
            P=self.P + (target.P - self.P) * t,
            A=self.A + (target.A - self.A) * t,
            D=self.D + (target.D - self.D) * t,
        )

    def mood_strength(self) -> float:
        """心境强度（ALMA）：|PAD| / √3，范围 0~1"""
        return math.sqrt(self.P ** 2 + self.A ** 2 + self.D ** 2) / math.sqrt(3)

    def dominant_quadrant(self, neutral_threshold: float = 0.15) -> str:
        """ALMA 八象限 + neutral（Gebhard 2005）

        以 PAD 三维符号划分八象限（不是自制的 7 值分类）：
            +P +A +D = exuberant    兴高采烈
            +P +A -D = dependent    依赖撒娇
            +P -A +D = relaxed      放松
            +P -A -D = docile       温顺
            -P +A +D = hostile      敌对
            -P +A -D = anxious      焦虑
            -P -A +D = disdainful   轻蔑
            -P -A -D = boredom      无聊
        心境强度低于阈值时一律归为 neutral，避免在原点附近乱跳。
        """
        if self.mood_strength() < neutral_threshold:
            return "neutral"
        p_pos, a_pos, d_pos = self.P >= 0, self.A >= 0, self.D >= 0
        if p_pos and a_pos and d_pos:
            return "exuberant"
        if p_pos and a_pos and not d_pos:
            return "dependent"
        if p_pos and not a_pos and d_pos:
            return "relaxed"
        if p_pos and not a_pos and not d_pos:
            return "docile"
        if not p_pos and a_pos and d_pos:
            return "hostile"
        if not p_pos and a_pos and not d_pos:
            return "anxious"
        if not p_pos and not a_pos and d_pos:
            return "disdainful"
        return "boredom"

    def to_dict(self) -> Dict[str, float]:
        return {'P': self.P, 'A': self.A, 'D': self.D}


# ============================================================
# StateEvent — 状态变化事件
# ============================================================

@dataclass
class StateEvent:
    """状态变化事件"""
    event_type: str                    # gift / chat / task_done / ...
    delta_P: float = 0.0
    delta_A: float = 0.0
    delta_D: float = 0.0
    delta_energy: int = 0
    delta_focus: int = 0
    delta_social: int = 0
    delta_curiosity: int = 0
    trigger: str = ""                  # 触发者（uid / 系统）
    context: str = ""                  # 上下文描述
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['created_at'] = self.created_at.isoformat() if self.created_at else None
        return d


# ============================================================
# PersonaState — 完整动态状态
# ============================================================

@dataclass
class PersonaState:
    """人格状态（动态部分）"""
    pad: PAD = field(default_factory=PAD)
    energy: int = 80           # 0-100 能量条
    focus: int = 60            # 0-100 专注度
    social_need: int = 50      # 0-100 社交需求
    curiosity: int = 50        # 0-100 好奇心
    last_event: Optional[str] = None
    last_event_at: Optional[datetime] = None
    current_task_id: Optional[str] = None
    day_streak: int = 0        # 连续直播天数

    # ---- 元信息 ----
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self._clamp_resources()

    def _clamp_resources(self):
        self.energy = max(0, min(100, self.energy))
        self.focus = max(0, min(100, self.focus))
        self.social_need = max(0, min(100, self.social_need))
        self.curiosity = max(0, min(100, self.curiosity))

    # ---- 核心 API ----

    def apply_event(self, event: StateEvent) -> None:
        """应用一个事件，更新状态"""
        self.pad = self.pad + PAD(
            P=event.delta_P,
            A=event.delta_A,
            D=event.delta_D,
        )
        self.energy += event.delta_energy
        self.focus += event.delta_focus
        self.social_need += event.delta_social
        self.curiosity += event.delta_curiosity
        self._clamp_resources()
        self.last_event = event.event_type
        self.last_event_at = event.created_at or datetime.now()
        self.updated_at = datetime.now()
        logger.debug(
            f"[state] event={event.event_type} "
            f"pad={self.pad.dominant_quadrant()} "
            f"E={self.energy} F={self.focus} S={self.social_need} C={self.curiosity}"
        )

    def decay(self, seconds_elapsed: float, baseline_pad: Optional[PAD] = None) -> None:
        """自然衰减（每 N 秒调用一次）

        - 能量慢慢恢复 +1/分钟
        - 社交需求慢慢上升 +0.5/分钟
        - 焦点慢慢下降 -0.3/分钟
        - 情绪缓慢回归 baseline
        """
        minutes = seconds_elapsed / 60.0
        self.energy = min(100, self.energy + minutes * 1.0)
        self.social_need = min(100, self.social_need + minutes * 0.5)
        self.focus = max(0, self.focus - minutes * 0.3)

        if baseline_pad is not None:
            # 向 baseline 回归（每分钟回归 1%）
            self.pad = self.pad.lerp(baseline_pad, 0.01 * minutes)
        else:
            # 默认向 (0.5, 0.3, 0.3) 回归
            self.pad = self.pad.lerp(PAD(0.5, 0.3, 0.3), 0.01 * minutes)

        self.updated_at = datetime.now()

    # ---- 阈值检测 ----

    def should_force_rest(self) -> bool:
        """是否应该强制休息"""
        return (
            self.energy < 10 or
            self.pad.P < -0.5 or
            self.focus < 5
        )

    def should_take_break(self) -> bool:
        """是否应该建议休息"""
        return (
            self.energy < 30 or
            self.pad.P < -0.3 or
            self.focus < 20
        )

    def wants_social(self) -> bool:
        """是否想主动找人聊天"""
        return self.social_need > 70

    def wants_exploration(self) -> bool:
        """是否想自主探索（刷视频 / 学东西）"""
        return self.curiosity > 60 and self.social_need < 60

    # ---- prompt 注入 ----

    def to_prompt_block(self) -> str:
        """生成可注入 prompt 的状态描述"""
        lines = []
        lines.append("【当前状态】")
        quadrant = self.pad.dominant_quadrant()
        quadrant_cn = {
            'exuberant': '兴高采烈',
            'dependent': '依赖撒娇',
            'relaxed': '放松',
            'docile': '温顺',
            'hostile': '敌对',
            'anxious': '焦虑',
            'disdainful': '轻蔑',
            'boredom': '无聊',
            'neutral': '平静',
        }.get(quadrant, '平静')
        lines.append(f"- 情绪：{quadrant_cn} "
                     f"(P={self.pad.P:.2f}, A={self.pad.A:.2f}, D={self.pad.D:.2f})，"
                     f"强度 {self.pad.mood_strength():.2f}")

        lines.append(f"- 能量：{self.energy}/100 "
                     f"({self._energy_label()})")
        lines.append(f"- 专注：{self.focus}/100")
        lines.append(f"- 社交需求：{self.social_need}/100 "
                     f"({'很孤独，想找人聊天' if self.social_need > 80 else '正常'})")
        lines.append(f"- 好奇心：{self.curiosity}/100 "
                     f"({'对新东西很感兴趣' if self.curiosity > 70 else '正常'})")

        if self.should_force_rest():
            lines.append("⚠️ 状态告警：你已经精疲力竭，必须立刻休息。")
        elif self.should_take_break():
            lines.append("💡 状态提示：你需要休息一下。")

        return "\n".join(lines)

    def _energy_label(self) -> str:
        if self.energy < 20:
            return '精疲力竭'
        elif self.energy < 40:
            return '疲惫'
        elif self.energy < 70:
            return '正常'
        return '精力充沛'

    # ---- 序列化 ----

    def to_dict(self) -> Dict[str, Any]:
        return {
            'pad': self.pad.to_dict(),
            'quadrant': self.pad.dominant_quadrant(),  # ALMA 八象限+neutral
            'energy': self.energy,
            'focus': self.focus,
            'social_need': self.social_need,
            'curiosity': self.curiosity,
            'last_event': self.last_event,
            'last_event_at': self.last_event_at.isoformat() if self.last_event_at else None,
            'current_task_id': self.current_task_id,
            'day_streak': self.day_streak,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PersonaState':
        pad_data = data.get('pad', {})
        return cls(
            pad=PAD(
                P=float(pad_data.get('P', 0.5)),
                A=float(pad_data.get('A', 0.3)),
                D=float(pad_data.get('D', 0.3)),
            ),
            energy=int(data.get('energy', 80)),
            focus=int(data.get('focus', 60)),
            social_need=int(data.get('social_need', 50)),
            curiosity=int(data.get('curiosity', 50)),
            last_event=data.get('last_event'),
            last_event_at=datetime.fromisoformat(data['last_event_at']) if data.get('last_event_at') else None,
            current_task_id=data.get('current_task_id'),
            day_streak=int(data.get('day_streak', 0)),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else None,
            updated_at=datetime.fromisoformat(data['updated_at']) if data.get('updated_at') else None,
        )


# ============================================================
# 便利函数
# ============================================================

def make_event(event_type: str, **kwargs) -> StateEvent:
    """快速构造一个 StateEvent"""
    return StateEvent(event_type=event_type, **kwargs)


__all__ = [
    'PAD',
    'StateEvent',
    'PersonaState',
    'make_event',
]
