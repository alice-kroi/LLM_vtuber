# ============================================================
# LLM_v2/core/persona_runtime.py — 单例 Persona + State 运行时
# ============================================================
# 三个脑共享同一个 Persona + State 实例
# 这是"单角色三脑"架构的关键：人格一致 + 状态同步
# ============================================================

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from persona.persona import PersonaProfile, get_persona
from persona.state import PersonaState, PAD, StateEvent

from LLM_v2.core.event_bus import EventBus
from LLM_v2.events import EVENTS

logger = logging.getLogger(__name__)


class StateDB:
    """PersonaState 的 sqlite 持久化（单角色单库）"""

    def __init__(self, db_path: str = "persona/state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS persona_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    pad_P REAL DEFAULT 0.5,
                    pad_A REAL DEFAULT 0.3,
                    pad_D REAL DEFAULT 0.3,
                    energy INTEGER DEFAULT 80,
                    focus INTEGER DEFAULT 60,
                    social_need INTEGER DEFAULT 50,
                    curiosity INTEGER DEFAULT 50,
                    last_event TEXT,
                    last_event_at TEXT,
                    current_task_id TEXT,
                    day_streak INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS state_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    delta_P REAL DEFAULT 0,
                    delta_A REAL DEFAULT 0,
                    delta_D REAL DEFAULT 0,
                    delta_energy INTEGER DEFAULT 0,
                    delta_focus INTEGER DEFAULT 0,
                    delta_social INTEGER DEFAULT 0,
                    delta_curiosity INTEGER DEFAULT 0,
                    trigger TEXT,
                    context TEXT,
                    created_at TEXT
                );
            """)

    def load(self) -> PersonaState:
        """加载 state（不存在则返回默认）"""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT pad_P, pad_A, pad_D, energy, focus, social_need, "
                "curiosity, last_event, last_event_at, current_task_id, "
                "day_streak, created_at, updated_at FROM persona_state WHERE id=1"
            ).fetchone()
            if row is None:
                logger.info("[state-db] no state found, creating default")
                return PersonaState()
            return PersonaState(
                pad=PAD(P=row[0], A=row[1], D=row[2]),
                energy=row[3],
                focus=row[4],
                social_need=row[5],
                curiosity=row[6],
                last_event=row[7],
                last_event_at=datetime.fromisoformat(row[8]) if row[8] else None,
                current_task_id=row[9],
                day_streak=row[10],
                created_at=datetime.fromisoformat(row[11]) if row[11] else None,
                updated_at=datetime.fromisoformat(row[12]) if row[12] else None,
            )

    def save(self, state: PersonaState) -> None:
        """保存 state（id 永远 = 1，单例）"""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            state.updated_at = datetime.now()
            conn.execute("""
                INSERT OR REPLACE INTO persona_state
                (id, pad_P, pad_A, pad_D, energy, focus, social_need,
                 curiosity, last_event, last_event_at, current_task_id,
                 day_streak, created_at, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state.pad.P, state.pad.A, state.pad.D,
                state.energy, state.focus, state.social_need, state.curiosity,
                state.last_event,
                state.last_event_at.isoformat() if state.last_event_at else None,
                state.current_task_id, state.day_streak,
                state.created_at.isoformat() if state.created_at else None,
                state.updated_at.isoformat(),
            ))

    def log_event(self, event: StateEvent) -> None:
        """记录事件到日志表（可审计）"""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO state_events
                (event_type, delta_P, delta_A, delta_D,
                 delta_energy, delta_focus, delta_social, delta_curiosity,
                 trigger, context, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_type,
                event.delta_P, event.delta_A, event.delta_D,
                event.delta_energy, event.delta_focus,
                event.delta_social, event.delta_curiosity,
                event.trigger, event.context,
                event.created_at.isoformat() if event.created_at else None,
            ))

    def recent_events(self, limit: int = 50) -> list:
        """最近事件（用于反思 / 日记）"""
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT event_type, delta_P, delta_A, delta_D, "
                "delta_energy, delta_focus, delta_social, delta_curiosity, "
                "trigger, context, created_at FROM state_events "
                "ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [
                {
                    "event_type": r[0], "delta_P": r[1], "delta_A": r[2], "delta_D": r[3],
                    "delta_energy": r[4], "delta_focus": r[5],
                    "delta_social": r[6], "delta_curiosity": r[7],
                    "trigger": r[8], "context": r[9], "created_at": r[10],
                }
                for r in rows
            ]


class PersonaRuntime:
    """单角色三脑的共享运行时

    职责：
        - 持有 PersonaProfile（人格档案）
        - 持有 PersonaState（动态状态）
        - 持有 EventBus（三脑通信总线）
        - 提供 state 修改的集中入口
        - 自动持久化 + 审计日志
    """

    _instance: Optional['PersonaRuntime'] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        profile_path: str = "persona/profile.yaml",
        state_db_path: str = "persona/state.db",
        bus_name: str = "main",
    ):
        # 避免重复初始化
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True

        self.persona: PersonaProfile = get_persona(profile_path)
        self.profile_path = profile_path
        self._state_db_path = state_db_path
        self.state_db: StateDB = StateDB(state_db_path)
        self.state: PersonaState = self.state_db.load()
        self.bus = EventBus(name=bus_name)

        # 独立人格内核：三脑只通过 snapshot() / perceive() 与它交互。
        # 与旧代码共用同一个 PersonaState 实例，所以既有 runtime.state 读取不受影响。
        from persona.self import Self
        self.self_core = Self(self.persona, self.state,
                              learning_db_path=state_db_path)
        self.self_core.persist_hook = lambda: self.state_db.save(self.state)
        # 离线补算：关服期间的时间也要让心境回落
        self.catchup_seconds = self.self_core.catch_up()
        # 启动补跑巩固（距上次 ≥20h 才执行；纯规则，不调 LLM）
        try:
            self.consolidation = self.self_core.consolidate()
        except Exception as e:  # noqa: BLE001
            self.consolidation = {"error": str(e)}

        logger.info(
            f"[runtime] initialized: name={self.persona.name} "
            f"pad={self.state.pad.dominant_quadrant()} "
            f"E={self.state.energy} "
            f"catchup={self.catchup_seconds / 60:.1f}min"
        )

    # ---------- 状态变更集中入口 ----------

    def apply_event(self, event_type: str, **kwargs) -> None:
        """统一的状态变更入口（任何 brain 都走这里）

        用法：
            runtime.apply_event("gift_received", delta_P=0.15, ...)
            runtime.apply_event("gift_received", emotion="gratitude", intensity=0.6)
        """
        # 交给独立人格内核处理（情绪脉冲 + 心境更新）
        self.self_core.perceive(event_type, **kwargs)
        # 审计：只记事件，不重复施加增量
        try:
            self.state_db.log_event(StateEvent(event_type=event_type, **{
                k: v for k, v in kwargs.items()
                if k in StateEvent.__dataclass_fields__ and k != "event_type"
            }))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[runtime] 状态事件审计失败: {e}")
        self.state_db.save(self.state)
        # 异步通知（不阻塞）
        asyncio.create_task(self.bus.emit(
            EVENTS.STATE_UPDATED,
            {"event_type": event_type, "state": self.state.to_dict()}
        ))

    def apply_state_event(self, event: StateEvent) -> None:
        """直接接收 StateEvent 的版本"""
        self.self_core.perceive(event.event_type, **{
            k: v for k, v in asdict(event).items()
            if k.startswith("delta_") and v
        })
        self.state_db.log_event(event)
        self.state_db.save(self.state)
        asyncio.create_task(self.bus.emit(
            EVENTS.STATE_UPDATED,
            {"event_type": event.event_type, "state": self.state.to_dict()}
        ))

    # ---------- 独立人格内核 ----------

    def snapshot(self) -> dict:
        """三脑读取当前心境（唯一读入口）"""
        return self.self_core.snapshot()

    def perceive(self, event_type: str, **kwargs) -> dict:
        """三脑上报发生了什么（唯一写入口）"""
        return self.self_core.perceive(event_type, **kwargs)

    def start_self_core(self, interval: float = 30.0):
        """启动 Self 自己的心跳（不寄生在任何 brain 上）"""
        return self.self_core.start(interval)

    async def stop_self_core(self) -> None:
        await self.self_core.stop()

    # ---------- 派生数据 ----------

    def build_system_prompt(self, brain_role: str = "streamer",
                            user: Optional[str] = None,
                            query: Optional[str] = None) -> str:
        """生成带 persona + state 的 system prompt

        Args:
            brain_role: 哪个脑（streamer / operator / will_engine）
            user: 当前对话对象（可选；用于注入该用户已生效的偏好与情景记忆）
            query: 当前这句话（可选；用于情景召回的关键词加成）
        """
        name = getattr(self.persona, "display_name", None) or "维尔薇"
        brain_prompts = {
            "streamer": (
                f"你是「{name}」对外沟通的部分——主播脑。\n"
                "你的职责：与观众聊天、回复弹幕、组织语言、维护人设。\n"
                "你不直接调用 OS 工具，需要操作时通过 emit('do_task') 交给操作脑。\n"
            ),
            "operator": (
                f"你是「{name}」动手的部分——操作脑。\n"
                "你的职责：专注把电脑操作做对，不与人闲聊。\n"
                "完成后通过 emit('operator_done') 把结果交回。\n"
            ),
            "will_engine": (
                f"你是「{name}」的内心——意志脑。\n"
                "你的职责：观察当前状态，决定 AI 想做什么。\n"
                "决定后通过 emit('will_engine_action') 通知其他脑执行。\n"
            ),
        }
        brain_intro = brain_prompts.get(brain_role, "")
        # 人格静态段 + 当前心境段（由独立内核提供，含实时情绪）
        persona_block = self.persona.to_prompt_block(None)
        mood_block = self.self_core.to_prompt_block(user, query)
        return f"{brain_intro}\n{persona_block}\n{mood_block}"

    # ---------- 域装配（懒创建）----------

    @property
    def assembly(self):
        """域装配单例（首次访问时创建，挂到 runtime._assembly）

        调用方：OperatorBrain / WebUI / 任何需要工具入口的地方
        """
        if not hasattr(self, "_assembly") or self._assembly is None:
            from LLM_v2.core.domain_assembly import attach_assembly
            attach_assembly(self)
        return self._assembly

    # ---------- 意志脑灵魂层（懒创建）----------

    @property
    def will_soul(self):
        """身份 / 决策记忆 / 经验 / 知识（失败返回 None，不影响主流程）"""
        if not hasattr(self, "_will_soul"):
            self._will_soul = None
            try:
                from LLM_v2.core.will_soul import WillSoul
                self._will_soul = WillSoul(self, self._state_db_path, self.profile_path)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[runtime] WillSoul 初始化失败（已降级）: {e}")
        return self._will_soul

    # ---------- 重置（仅测试用）----------

    def _reset(self):
        """重置单例（仅测试用）"""
        PersonaRuntime._instance = None
        self._initialized = False


# 异步需要（延迟 import 避免循环）
import asyncio  # noqa: E402


def get_runtime(
    profile_path: str = "persona/profile.yaml",
    state_db_path: str = "persona/state.db",
) -> PersonaRuntime:
    """获取 runtime 单例（首次调用时创建）"""
    return PersonaRuntime(profile_path, state_db_path)


__all__ = ['StateDB', 'PersonaRuntime', 'get_runtime']
