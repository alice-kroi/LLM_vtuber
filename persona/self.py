# ============================================================
# persona/self.py — 独立人格内核（Self）
# ============================================================
# 定位：小鲤的"自己"。不是一个功能、不是某个脑的附属，也不做成能力域。
#   三脑只通过两个接口与它交互：
#     snapshot()  读——我现在是什么状态
#     perceive()  写——刚刚发生了什么
#
# 三层（ALMA / Gebhard 2005）：
#   Personality  长期不变，来自 profile.yaml（只读）
#   Mood         小时级，持久化，按分钟向人格基线回归
#   Emotion      秒~分钟级，瞬时脉冲，注入 mood 后自身快速消退
#
# 独立性的三个硬指标：
#   1. 自己的心跳（run_loop），不寄生在 WillEngine.heartbeat
#   2. 离线也活着 —— 启动时按 updated_at 补算衰减
#   3. 情绪不依赖对话轮次：没人说话时也会回落
#
# 参考：ALMA（三层 + 人格调制）、Kuppens 2010 DynamAffect（γ≈0.03 min⁻¹）、
#       sovyx（后台调度而非生成时计算）、OpenClaw/pad-plus-ai（情绪随时间衰减）
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from persona.persona import PersonaProfile
from persona.relationship import RelationshipStore, affinity
from persona.state import PAD, PersonaState, StateEvent, make_event

logger = logging.getLogger(__name__)

_EMOTION_TABLE_PATH = Path(__file__).resolve().parent / "emotion_pad.json"

# ---------- 动力学常数 ----------
# mood：每分钟的保留率。0.97 → 半衰期 ≈ 23 分钟（直播场景合适）
MOOD_GAMMA_PER_MIN = {"P": 0.975, "A": 0.960, "D": 0.970}
# emotion：半衰期 150 秒（快变量）
EMOTION_HALF_LIFE_SEC = 150.0
# 事件反应率（一次事件不会把状态打满）
ALPHA_BASE = 0.40
# 情绪脉冲注入 mood 的比例（ALMA：emotion 作冲动注入，而非两套向量并列）
EMOTION_TO_MOOD = 0.35
# 离线补算上限：超过就不线性外推了（避免关服一周后算出荒谬值）
MAX_CATCHUP_MIN = 24 * 60

# ---------- 资源条（每分钟） ----------
ENERGY_RECOVER = 1.0        # 空闲恢复
SOCIAL_RISE = 0.5
FOCUS_IDLE_RECOVER = 0.6    # 空闲时专注回升（旧实现里空闲也在掉，是 bug）
CURIOSITY_BASELINE = 55
CURIOSITY_PULL = 0.02

# 只采信实证条目（与 emotion_pad.json 的 _usage 约定一致）
_ACCEPTED_STATUS = {"verified_primary", "verified_secondary"}

# 事件 → 关系增量（增量会在 RelationshipStore 里按剩余空间缩放）
_RELATION_STEPS: Dict[str, Dict[str, float]] = {
    "chat_received": {"intimacy": 0.010, "trust": 0.002},
    "gift_received": {"intimacy": 0.030, "trust": 0.020},
    "task_done": {"trust": 0.030},
    "task_failed": {"trust": -0.010},
}


@dataclass
class EmotionPulse:
    """瞬时情绪脉冲（快变量）"""
    name: str
    P: float
    A: float
    D: float
    intensity: float          # 0-1
    at: float = field(default_factory=time.time)

    def level(self, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        decay = 0.5 ** (max(0.0, now - self.at) / EMOTION_HALF_LIFE_SEC)
        return self.intensity * decay


class Self:
    """独立人格内核。

    注意：`state` 与旧代码共用同一个 PersonaState 实例，
    所以 streamer / will_engine / webui 里既有的 `runtime.state` 读取无需改动。
    """

    def __init__(self, profile: PersonaProfile, state: PersonaState,
                 emotion_table_path: Optional[Path] = None,
                 learning_db_path: Optional[str] = None):
        self.profile = profile
        self.state = state
        self.emotions: List[EmotionPulse] = []

        self._table = self._load_emotion_table(emotion_table_path or _EMOTION_TABLE_PATH)
        self.last_appraisal = None       # 最近一次认知评估（审计用）
        self.last_user: Optional[str] = None
        self.relationships = RelationshipStore(
            Path(__file__).resolve().parent / "relationships.json")
        # 学习层（P0：情景 + 偏好）。库默认与 PersonaState 同一个 state.db。
        try:
            from persona.learning.store import LearningStore
            self.learning: Optional[LearningStore] = LearningStore(
                learning_db_path or "persona/state.db")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[self] 学习层不可用（人格内核继续运行）: {e}")
            self.learning = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_tick = time.time()
        self._last_consolidate_check = 0.0
        self.persist_hook: Optional[Callable[[], None]] = None

    # ---------- 初始化 ----------

    def _load_emotion_table(self, path: Path) -> Dict[str, Dict[str, float]]:
        if not path.exists():
            logger.warning(f"[self] 情感坐标表不存在: {path}（情绪脉冲将不可用）")
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[self] 情感坐标表解析失败: {e}")
            return {}
        allow_derived = bool(data.get("allow_derived"))
        out: Dict[str, Dict[str, float]] = {}
        for name, item in (data.get("emotions") or {}).items():
            status = item.get("status", "")
            if status not in _ACCEPTED_STATUS and not (
                    allow_derived and status in ("derived", "predicted_paper")):
                continue
            try:
                out[name] = {"P": float(item["P"]), "A": float(item["A"]),
                             "D": float(item["D"])}
            except (KeyError, TypeError, ValueError):
                continue
        logger.info(f"[self] 已加载 {len(out)} 条情绪坐标（{data.get('primary_lineage')}）")
        return out

    @property
    def baseline(self) -> PAD:
        return self.profile.personality.baseline_mood

    def catch_up(self) -> float:
        """把离线期间的时间补算出来（Self 独立存在的重要一环）"""
        last = self.state.updated_at
        if not last:
            return 0.0
        dt = (datetime.now() - last).total_seconds()
        if dt <= 30:
            return 0.0
        capped = min(dt, MAX_CATCHUP_MIN * 60)
        self.tick(capped)
        logger.info(f"[self] 离线补算 {dt / 60:.1f} 分钟"
                    f"{'（已按上限裁剪）' if dt > capped else ''}")
        return dt

    # ---------- 动力学 ----------

    def tick(self, seconds: float) -> None:
        """按真实流逝时间推进状态（不是按轮次）"""
        if seconds <= 0:
            return
        minutes = seconds / 60.0
        base = self.baseline

        # mood：分维度指数回归到人格基线
        for dim in ("P", "A", "D"):
            gamma = MOOD_GAMMA_PER_MIN[dim] ** minutes
            cur = getattr(self.state.pad, dim)
            setattr(self.state.pad, dim, cur * gamma + getattr(base, dim) * (1 - gamma))
        self.state.pad._clamp()

        # 资源条
        st = self.state
        st.energy = int(min(100, st.energy + ENERGY_RECOVER * minutes))
        st.social_need = int(min(100, st.social_need + SOCIAL_RISE * minutes))
        st.focus = int(min(100, st.focus + FOCUS_IDLE_RECOVER * minutes))
        st.curiosity = round(
            st.curiosity + (CURIOSITY_BASELINE - st.curiosity) * CURIOSITY_PULL * minutes, 1)
        st._clamp_resources()

        # 情绪层：低于 5% 的脉冲直接丢弃，避免无限累积
        now = time.time()
        self.emotions = [e for e in self.emotions if e.level(now) >= 0.05]

        st.updated_at = datetime.now()

    def _alpha(self) -> float:
        """人格调制反应率：神经质高的人更容易被带动"""
        p = self.profile.personality
        n = (p.neuroticism - 0.5) * 2          # 归一化到 [-1,1]
        e = (p.extraversion - 0.5) * 2
        alpha = ALPHA_BASE * (1 + 0.4 * n + 0.3 * e)
        return max(0.15, min(0.85, alpha))

    # ---------- 写：perceive ----------

    def perceive(self, event_type: str, *, emotion: Optional[str] = None,
                 intensity: Optional[float] = None,
                 user: Optional[str] = None, text: Optional[str] = None,
                 **deltas) -> Dict[str, Any]:
        """接收一件刚发生的事。

        情绪来源优先级：
        1. 显式 `emotion`（调用方已确定）
        2. 文本信号 `text`（明确夸奖/道谢/攻击，仅聊天类事件）
        3. 事件类型规则（礼物、任务成败等）

        关系变量会把同一事件的强度按亲密度放大/缩小。
        """
        from persona.emotion import appraise, appraise_text

        now = time.time()
        before = self.state.pad.to_dict()
        applied_emotion = None
        rel = None

        # 关系：熟人对我说的好话/坏话都更"有分量"
        if user and user not in ("?", "unknown"):
            self.last_user = user
            rel = self.relationships.get(user)

        if not emotion:
            ap = None
            if text and event_type in ("chat_received", "user_message"):
                ap = appraise_text(text)
            if ap is None:
                ap = appraise(event_type,
                              {"intensity": intensity} if intensity is not None else None,
                              neuroticism=self.profile.personality.neuroticism)
            if ap is not None:
                emotion = ap.emotion
                intensity = ap.intensity
                # chat_replied 只是"我回完了"，会覆盖掉真正有意义的评估，不记
                if event_type != "chat_replied":
                    self.last_appraisal = ap
            elif intensity is None:
                intensity = 0.5

        if emotion and rel is not None and intensity is not None:
            # affinity 0.2 视为基准；越亲近，刺激越强
            factor = 1 + 0.4 * (rel["affinity"] - 0.2)
            intensity = max(0.0, min(1.0, intensity * factor))

        if emotion:
            coord = self._table.get(emotion)
            if coord is None:
                logger.debug(f"[self] 未知或无实证坐标的情绪: {emotion}（跳过脉冲）")
            else:
                level = max(0.0, min(1.0, intensity if intensity is not None else 0.5))
                # 同一个情绪已经很强烈时不叠加，避免弹幕刷屏把状态打爆
                same = max((e.level(now) for e in self.emotions if e.name == emotion), default=0.0)
                if same < 0.6:
                    self.emotions.append(EmotionPulse(
                        name=emotion, intensity=level,
                        P=coord["P"], A=coord["A"], D=coord["D"], at=now))
                    alpha = self._alpha() * EMOTION_TO_MOOD * level
                    for dim in ("P", "A", "D"):
                        cur = getattr(self.state.pad, dim)
                        setattr(self.state.pad, dim, cur + (coord[dim] - cur) * alpha)
                    self.state.pad._clamp()
                    applied_emotion = emotion
                else:
                    applied_emotion = f"{emotion}(已饱和)"

        if deltas:
            event = make_event(event_type, **deltas)
            self.state.apply_event(event)
        else:
            self.state.last_event = event_type
            self.state.last_event_at = datetime.now()
            self.state.updated_at = datetime.now()

        # 关系增量：按事件性质调整信任/亲密
        if user:
            step = _RELATION_STEPS.get(event_type)
            if applied_emotion == "anger":
                step = {"trust": -0.02}
            if step:
                rel = self.relationships.touch(
                    user, when=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **step)

        # 学习：情景 + 偏好（失败不影响人格主流程）
        learned = self._learn(event_type, user, text, applied_emotion, intensity, rel)

        return {
            "event": event_type,
            "emotion": applied_emotion,
            "intensity": round(intensity, 3) if intensity is not None else None,
            "pad_before": before,
            "pad_after": self.state.pad.to_dict(),
            "user": user,
            "relationship": rel,
            "learned": learned,
        }

    # ---------- 学习（P0：偏好记忆） ----------

    _KIND_MAP = {
        "chat_received": "chat", "user_message": "chat", "chat_replied": "chat",
        "gift_received": "gift",
        "task_done": "task", "task_failed": "task",
    }

    def _learn(self, event_type: str, user: Optional[str], text: Optional[str],
               emotion: Optional[str], intensity: Optional[float],
               rel: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """从刚发生的事里抽取偏好并写入学习库。

        记录门槛（任一满足）：
          - 抽到了偏好（必须有 episode 作证据）
          - 用户说了"记住"
          - 情绪强度 ≥ 0.5（值得回忆的片段）
        返回本次新增/加权的偏好（供 WebUI 审计），失败返回空列表。
        """
        if self.learning is None or not user or user in ("?", "unknown"):
            return []
        try:
            from persona.learning.preference import extract
            from persona.learning.signals import to_reward

            hits = extract(text) if text else []
            note = bool(text) and "记住" in text
            strong = intensity is not None and intensity >= 0.5
            if not hits and not note and not strong:
                return []

            affinity = (rel or {}).get("affinity", 0.2)
            ep_id = self.learning.record_episode(
                user_id=user,
                kind=self._KIND_MAP.get(event_type, "chat"),
                summary=(text or event_type),
                emotion=emotion,
                intensity=intensity,
                reward=to_reward(emotion, intensity, affinity) if emotion else None,
                importance=round(min(1.0, 0.5 + (intensity or 0.0) * 0.5), 3),
            )
            out = []
            for hit in hits:
                row = self.learning.add_preference(
                    user, hit.slot, hit.value, hit.polarity, evidence=[ep_id])
                if row:
                    out.append({"slot": row["slot"], "value": row["value"],
                                "polarity": row["polarity"],
                                "confidence": round(row["confidence"], 3)})
            if out or note:
                logger.info(f"[self] 学到 {len(out)} 条偏好（{user}）: "
                            f"{[(h['slot'], h['value']) for h in out]}")
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[self] 学习写入失败（已忽略）: {e}")
            return []

    # ---------- 读：snapshot ----------

    @property
    def mood_strength(self) -> float:
        """ALMA 的心境强度：|PAD| / √3"""
        return self.state.pad.mood_strength()

    def active_emotions(self, threshold: float = 0.1) -> List[Dict[str, Any]]:
        now = time.time()
        out = [{"name": e.name, "intensity": round(e.level(now), 3)}
               for e in self.emotions if e.level(now) >= threshold]
        out.sort(key=lambda x: -x["intensity"])
        return out

    def snapshot(self) -> Dict[str, Any]:
        st = self.state
        rel = self.relationships.get(self.last_user) if self.last_user else None
        return {
            "pad": st.pad.to_dict(),
            "quadrant": st.pad.dominant_quadrant(),
            "mood_strength": round(self.mood_strength, 3),
            "emotions": self.active_emotions(),
            "energy": st.energy,
            "focus": st.focus,
            "social_need": st.social_need,
            "curiosity": round(st.curiosity, 1),
            "last_user": self.last_user,
            "relationship": rel,
            "known_contacts": len(self.relationships),
            "preferences": self.known_preferences(),
            "memories": self.recall_memories(),
            "last_appraisal": ({
                "emotion": self.last_appraisal.emotion,
                "intensity": self.last_appraisal.intensity,
                "relevance": self.last_appraisal.relevance,
                "implication": self.last_appraisal.implication,
            } if self.last_appraisal else None),
            "updated_at": st.updated_at.strftime("%Y-%m-%d %H:%M:%S") if st.updated_at else None,
        }

    def known_preferences(self, user: Optional[str] = None,
                          limit: int = 5) -> List[Dict[str, Any]]:
        """当前对话对象生效中的偏好（confidence ≥ 0.5）"""
        if self.learning is None:
            return []
        target = user or self.last_user
        if not target:
            return []
        try:
            return self.learning.preferences_for(target, limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[self] 读取偏好失败: {e}")
            return []

    def recall_memories(self, user: Optional[str] = None,
                        query: Optional[str] = None,
                        limit: int = 3) -> List[Dict[str, Any]]:
        """跨会话召回该用户最值得回忆的片段（按重要度×时间衰减+关键词）"""
        if self.learning is None:
            return []
        target = user or self.last_user
        if not target:
            return []
        try:
            from persona.learning.episode import select
            rows = self.learning.recall_episodes(target)
            return select(rows, query=query, limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[self] 情景召回失败: {e}")
            return []

    def to_prompt_block(self, user: Optional[str] = None,
                        query: Optional[str] = None) -> str:
        """给三脑的心境片段（比 state.to_prompt_block 多情绪 + 记忆 + 偏好）

        Args:
            user: 当前对话对象；给了就用它查偏好/记忆，否则用 last_user
            query: 当前这句话，用于情景召回的关键词加成
        """
        lines = ["【此刻的心境】"]
        target = user or self.last_user
        st = self.state
        lines.append(f"- 主体情绪：{st.pad.dominant_quadrant()} "
                     f"(P={st.pad.P:.2f}, A={st.pad.A:.2f}, D={st.pad.D:.2f})，"
                     f"强度 {self.mood_strength:.2f}")
        emotions = self.active_emotions()
        if emotions:
            shown = "、".join(f"{e['name']}({e['intensity']:.2f})" for e in emotions[:3])
            lines.append(f"- 正在感受：{shown}")
        if target:
            rel = self.relationships.get(target)
            label = "熟人" if rel["acquaintance"] else "新观众"
            lines.append(f"- 对 {target} 的观感：{label}"
                         f"（信任 {rel['trust']:.2f}，亲密 {rel['intimacy']:.2f}）")
        lines.append(f"- 精力 {st.energy}/100，专注 {st.focus}/100，"
                     f"社交需求 {st.social_need}/100，好奇 {st.curiosity:.0f}/100")

        # 情景记忆：跨会话召回，最多 3 条
        memories = self.recall_memories(target, query)
        if memories:
            from persona.learning.episode import describe
            lines.append("【我记得的事】")
            for m in memories:
                lines.append(f"- {describe(m)}")

        # 偏好：只注入当前对话对象、已生效、最多 5 条
        prefs = self.known_preferences(target)
        if prefs:
            lines.append(f"【对这个人的了解（{target}）】")
            for p in prefs:
                mark = "喜欢" if p["polarity"] > 0 else "不喜欢"
                lines.append(f"- {p['slot']}：{mark}「{p['value']}」")
        return "\n".join(lines)

    # ---------- 生命周期 ----------

    def consolidate(self, force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """夜间巩固：合并重复偏好 / 淘汰旧情景 / 退役低权重流程（纯规则）"""
        if self.learning is None:
            return {}
        try:
            from persona.learning.consolidate import consolidate, needs_consolidation
            if not force and not needs_consolidation(self.learning):
                return {"skipped": True}
            report = consolidate(self.learning, dry_run=dry_run)
            logger.info(f"[self] 巩固完成: {report}")
            return report
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[self] 巩固失败（已忽略）: {e}")
            return {"error": str(e)}

    async def run_loop(self, interval: float = 30.0) -> None:
        """自己的心跳：推进状态并落盘。与任何脑无关。"""
        self._running = True
        self._last_tick = time.time()
        logger.info(f"[self] 心跳启动（{interval:.0f}s）")
        while self._running:
            await asyncio.sleep(interval)
            try:
                now = time.time()
                dt = now - self._last_tick
                self._last_tick = now
                self.tick(dt)
                if self.persist_hook:
                    self.persist_hook()
                # 每小时检查一次是否该做巩固（默认 20h 一次，夜间/启动补跑）
                if now - self._last_consolidate_check >= 3600:
                    self._last_consolidate_check = now
                    self.consolidate()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[self] 心跳异常（继续运行）: {e}")
        logger.info("[self] 心跳停止")

    def start(self, interval: float = 30.0) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_loop(interval), name="self-core")
        return self._task

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None


__all__ = ["Self", "EmotionPulse"]
