# ============================================================
# persona/persona.py — 静态人格档案
# ============================================================
# 兼容 SillyTavern Character Card V2 + RisuAI 扩展
# 单一职责：加载 profile.yaml → 生成 PersonaProfile dataclass
# ============================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from persona.state import PAD, PersonaState

logger = logging.getLogger(__name__)


# ---------- 子结构 ----------

@dataclass
class PersonalityTraits:
    """性格特征（PAD + Big Five + 自然语言描述）"""
    baseline_mood: PAD
    openness: float            # 0-1
    conscientiousness: float   # 0-1
    extraversion: float        # 0-1
    agreeableness: float       # 0-1
    neuroticism: float         # 0-1
    personality_text: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PersonalityTraits':
        bm = data.get('baseline_mood', {})
        return cls(
            baseline_mood=PAD(
                P=float(bm.get('P', 0.5)),
                A=float(bm.get('A', 0.3)),
                D=float(bm.get('D', 0.3)),
            ),
            openness=float(data.get('openness', 0.5)),
            conscientiousness=float(data.get('conscientiousness', 0.5)),
            extraversion=float(data.get('extraversion', 0.5)),
            agreeableness=float(data.get('agreeableness', 0.5)),
            neuroticism=float(data.get('neuroticism', 0.5)),
            personality_text=str(data.get('personality_text', '')),
        )


@dataclass
class Value:
    name: str
    weight: float
    description: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Value':
        return cls(
            name=str(data.get('name', '')),
            weight=float(data.get('weight', 0.5)),
            description=str(data.get('description', '')),
        )


@dataclass
class Interest:
    name: str
    subtopics: List[str] = field(default_factory=list)
    enthusiasm: float = 0.5

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Interest':
        return cls(
            name=str(data.get('name', '')),
            subtopics=list(data.get('subtopics', [])),
            enthusiasm=float(data.get('enthusiasm', 0.5)),
        )


@dataclass
class LorebookEntry:
    """知识库触发条目（RisuAI lorebook 模式）"""
    keywords: List[str]
    content: str
    priority: float = 0.5

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LorebookEntry':
        return cls(
            keywords=list(data.get('keywords', [])),
            content=str(data.get('content', '')),
            priority=float(data.get('priority', 0.5)),
        )


@dataclass
class AvatarConfig:
    """数字人联动配置"""
    live2d_model: str = ""
    voice_id: str = ""
    emotion_to_expression: Dict[str, str] = field(default_factory=dict)
    emotion_to_tone: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AvatarConfig':
        return cls(
            live2d_model=str(data.get('live2d_model', '')),
            voice_id=str(data.get('voice_id', '')),
            emotion_to_expression=dict(data.get('emotion_to_expression', {})),
            emotion_to_tone=dict(data.get('emotion_to_tone', {})),
        )


# ---------- 主结构 ----------

@dataclass
class PersonaProfile:
    """完整人格档案（兼容 SillyTavern V2 + RisuAI 扩展）"""
    version: str
    name: str
    display_name: str
    nicknames: List[str]
    personality: PersonalityTraits
    values: List[Value]
    interests: List[Interest]
    speaking_style: Dict[str, Any]
    backstory: str
    lorebook: List[LorebookEntry]
    avatar: AvatarConfig
    constraints: Dict[str, Any]
    custom_extensions: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> 'PersonaProfile':
        """从 YAML 文件加载人格档案"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Persona file not found: {path}")
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        profile = cls.from_dict(data)
        logger.info(f"[persona] loaded: name={profile.name} "
                    f"lorebook={len(profile.lorebook)} "
                    f"interests={len(profile.interests)}")
        return profile

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PersonaProfile':
        return cls(
            version=str(data.get('version', '1.0')),
            name=str(data.get('name', 'unknown')),
            display_name=str(data.get('display_name', data.get('name', 'unknown'))),
            nicknames=list(data.get('nicknames', [])),
            personality=PersonalityTraits.from_dict(data.get('personality', {})),
            values=[Value.from_dict(v) for v in data.get('values', [])],
            interests=[Interest.from_dict(i) for i in data.get('interests', [])],
            speaking_style=dict(data.get('speaking_style', {})),
            backstory=str(data.get('backstory', '')),
            lorebook=[LorebookEntry.from_dict(e) for e in data.get('lorebook', [])],
            avatar=AvatarConfig.from_dict(data.get('avatar', {})),
            constraints=dict(data.get('constraints', {})),
            custom_extensions=dict(data.get('_custom_extensions', {})),
        )

    def match_lorebook(self, text: str, top_k: int = 3) -> List[LorebookEntry]:
        """根据文本触发 lorebook 条目（按 priority 排序）"""
        text_lower = text.lower()
        triggered = []
        for entry in self.lorebook:
            if any(kw.lower() in text_lower for kw in entry.keywords):
                triggered.append(entry)
        triggered.sort(key=lambda e: -e.priority)
        return triggered[:top_k]

    def to_prompt_block(self, state: Optional[PersonaState] = None) -> str:
        """生成可注入 prompt 的人格 + 状态描述"""
        lines: List[str] = []

        # 基础身份
        lines.append(f"你的名字是 {self.display_name}。")
        if self.nicknames:
            lines.append(f"观众也可以叫你：{'、'.join(self.nicknames)}。")

        # 性格
        lines.append("")
        lines.append(self.personality.personality_text)

        # 价值观
        if self.values:
            lines.append("")
            lines.append("你的价值观（按权重排序）：")
            for v in sorted(self.values, key=lambda x: -x.weight)[:5]:
                lines.append(f"  - {v.name}（权重 {v.weight:.2f}）：{v.description}")

        # 爱好
        if self.interests:
            lines.append("")
            lines.append("你的爱好（按热情排序）：")
            for i in sorted(self.interests, key=lambda x: -x.enthusiasm)[:5]:
                sub = '、'.join(i.subtopics[:3]) if i.subtopics else ''
                lines.append(f"  - {i.name}（{sub}）")

        # 说话风格
        ss = self.speaking_style
        if ss:
            lines.append("")
            lines.append(f"说话风格：{ss.get('tone', '')}")
            if ss.get('catchphrases'):
                lines.append(f"口头禅：{'、'.join(ss['catchphrases'])}")

        # 背景故事（截断避免过长）
        if self.backstory:
            lines.append("")
            lines.append(f"背景故事：{self.backstory[:200]}")

        # 行为约束
        if self.constraints:
            lines.append("")
            if self.constraints.get('refuse_to'):
                lines.append("绝对不做：")
                for r in self.constraints['refuse_to']:
                    lines.append(f"  - {r}")
            if self.constraints.get('always'):
                lines.append("始终要做：")
                for a in self.constraints['always']:
                    lines.append(f"  - {a}")

        # 状态
        if state is not None:
            lines.append("")
            lines.append(state.to_prompt_block())

        return "\n".join(lines)


# ---------- 单例加载（避免每次重新读 YAML）----------

_PROFILE_CACHE: Optional[PersonaProfile] = None


def get_persona(path: str | Path = "persona/profile.yaml") -> PersonaProfile:
    """获取人格档案单例（首次加载后缓存）"""
    global _PROFILE_CACHE
    if _PROFILE_CACHE is None:
        _PROFILE_CACHE = PersonaProfile.load(path)
    return _PROFILE_CACHE


def reload_persona(path: str | Path = "persona/profile.yaml") -> PersonaProfile:
    """强制重新加载人格档案（用于热更新）"""
    global _PROFILE_CACHE
    _PROFILE_CACHE = PersonaProfile.load(path)
    logger.info(f"[persona] reloaded from {path}")
    return _PROFILE_CACHE


__all__ = [
    'PersonalityTraits',
    'Value',
    'Interest',
    'LorebookEntry',
    'AvatarConfig',
    'PersonaProfile',
    'get_persona',
    'reload_persona',
]
