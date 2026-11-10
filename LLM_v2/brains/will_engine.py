# ============================================================
# LLM_v2/brains/will_engine.py — 意志脑（API 决策版）
# ============================================================
# 决策模式：
#   - rule: 纯规则（v1）
#   - api:  调用 LLM API（豆包）
#   - hybrid: 规则优先，规则失败时回退到 API（默认）
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from LLM_v2.core.brain_base import Brain
from LLM_v2.core.llm_adapter import LLMAdapter, get_llm
from LLM_v2.core.will_knowledge import get_will_knowledge
from LLM_v2.events import EVENTS
from LLM_v2.io.webui import log_event as _log_event

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_AVATAR = _ROOT / "persona" / "avatar" / "villv_avatar_640.jpg"

# 任务多久没进展就判定中断（思维脑巡检时据此关账）
TASK_STALL_SEC = float(os.getenv("TASK_STALL_SEC") or 600)
# 自主立项：冷却时间 / 同时最多几个进行中任务 / 近期已结束任务的去重窗口
TASK_PROPOSE_COOLDOWN = float(os.getenv("TASK_PROPOSE_COOLDOWN_SEC") or 300)
TASK_MAX_ACTIVE = int(os.getenv("TASK_MAX_ACTIVE") or 2)
TASK_PROPOSE_DEDUP_SEC = float(os.getenv("TASK_PROPOSE_DEDUP_SEC") or 1800)
# 思维脑可以自己立项的工具（都走操作脑执行）
PROPOSABLE_TOOLS = ("research_task", "cultist_task", "code_task", "tool_agent")


class WillEngineBrain(Brain):
    """意志脑——决策 AI 想干啥"""

    IDLE_INTERVAL = 30.0
    IDLE_THRESHOLD = 30.0
    MIN_KNOWLEDGE_CHARS = 4
    # 只有像"在问知识"的消息才检索：否则每条闲聊都要多等 1 秒多（含 rerank）
    KNOWLEDGE_HINTS = (
        "?", "？", "什么", "怎么", "为什么", "为何", "如何", "哪", "多少",
        "是否", "介绍", "解释", "原理", "区别", "含义", "定义", "讲讲",
        "说说", "科普", "查一下", "依据", "出自", "根据",
    )

    # 决策 prompt 模板
    DECISION_SYSTEM = """你是 {name} 的内心（意志脑）。你的任务是：根据 AI 当前的"人格 + 状态"，决定它接下来想做什么。

【人格档案】
{persona_text}

【当前状态】
{state_text}

【可选动作类型】
- social_action: 想找人聊天（社交需求 > 70 时触发）
- explore_interest: 想探索爱好（好奇心 > 60 且不累）
- watch_video: 想去 B 站刷视频
- code_action: 想写代码练手
- play_game: 想去玩密教模拟器
- rest: 想休息（精疲力竭时）
- diary: 想写日记（深度思考时）

【输出格式】
严格 JSON（不要解释、不要 markdown）：
{{"type": "动作类型", "target": "目标对象", "reasoning": "一句话理由"}}

只输出 JSON。"""

    def __init__(
        self,
        runtime,
        decision_mode: str = "hybrid",  # rule / api / hybrid
        llm: Optional[LLMAdapter] = None,
        autonomous: bool = False,       # 自主决策开关（默认关闭：不自行"想干啥"）
    ):
        super().__init__(
            name="will_engine",
            runtime=runtime,
            heartbeat_interval=self.IDLE_INTERVAL,
        )
        self.decision_mode = decision_mode
        self.autonomous = autonomous      # True=空闲时自主决策；False=仅被动响应
        self.llm = llm  # 懒加载
        self.last_activity_at = datetime.now()
        self._decision_count = 0
        # 任务过程监控表（思维脑监控所有脑的任务）
        self._task_monitor: dict = {}          # task_id -> {description, status, percent, stage, ...}
        self._task_progress_history: list = []  # 最近进度记录（供 WebUI / 调试）
        self._last_patrol_at = 0.0             # 上次台账巡检时间
        self._patrol_watching = 0              # 巡检时在跑的任务数
        self._last_task_proposal_at = 0.0      # 上次自主立项时间
        self._bv_narrated: dict = {}           # B 站内容播报去重：bvid/uid -> 上次播报时间
        self._research_narrated: dict = {}     # 研究结果播报去重：topic -> 上次播报时间
        self._soul = None                      # WillSoul 懒加载
        self._last_distill_at = 0.0            # 经验反思节流（每小时最多一次）
        # 自主执行节流 / 去重 / 作息去重
        self._last_action_exec_at = 0.0
        self._executed_actions: dict = {}
        self._routine_fired: set = set()
        self._warm_task = None                  # 知识库预热任务

    async def _warm_knowledge(self) -> None:
        """后台预热知识库（集合加载 + 嵌入），避免首条消息检索超时。"""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, get_will_knowledge().warm)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 知识库预热失败: {e}")

    # ---------- 灵魂层 ----------

    def _get_soul(self):
        """身份 / 决策记忆 / 经验（不可用时返回 None，决策链路不受影响）"""
        if self._soul is None:
            try:
                self._soul = getattr(self.runtime, "will_soul", None)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[brain:{self.name}] WillSoul 不可用: {e}")
                self._soul = None
        return self._soul

    def _is_discouraged(self, action_type: str) -> bool:
        """同类心境下失败过 2 次以上的动作，规则阶段直接跳过"""
        soul = self._get_soul()
        if soul is None:
            return False
        try:
            for stat in soul.experience_stats():
                if (stat["action_type"] == action_type
                        and stat["attempts"] >= 2
                        and stat["avg_outcome"] <= -0.3):
                    logger.debug(
                        f"[brain:{self.name}] 经验劝阻 {action_type}: "
                        f"avg={stat['avg_outcome']}")
                    return True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 读取经验失败: {e}")
        return False

    DISTILL_INTERVAL = 3600.0

    def _schedule_distill(self) -> None:
        """任务结果回填后异步反思一次（节流，失败不影响主流程）"""
        now = time.time()
        if now - self._last_distill_at < self.DISTILL_INTERVAL:
            return
        self._last_distill_at = now
        try:
            asyncio.create_task(self._distill_experience())
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 反思调度失败: {e}")

    async def _distill_experience(self) -> None:
        soul = self._get_soul()
        if soul is None:
            return
        try:
            from persona.learning.reflect import distill_will_experience
            created = await distill_will_experience(soul, self._get_llm())
            for item in created:
                _log_event("INFO", "will_engine",
                           f"经验反思：{item['action_type']} → {item['distilled']}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 经验反思失败（已忽略）: {e}")

    def _get_llm(self) -> Optional[LLMAdapter]:
        """懒加载 LLM"""
        if self.llm is None:
            self.llm = get_llm()
        return self.llm

    async def setup(self):
        """注册监听器（H-2 Phase 6: 加 USER_MESSAGE → 意图分类 → 调度 Operator）

        v5.8: 思维脑监控所有脑的任务过程（TASK_STARTED / OPERATOR_PROGRESS / OPERATOR_DONE）
        """
        _log_event("DEBUG", "will_engine", "setup: 注册 USER_MESSAGE 监听器（_on_activity + _on_user_intent）")
        # 上次退出时没来得及收尾的任务：本进程无法续跑，先由思维脑关账，
        # 否则会在看板上一直显示 running，直到超时巡检才被清掉。
        self._recover_interrupted_tasks()
        self.on(EVENTS.BILIBILI_MSG, self._on_activity)
        self.on(EVENTS.GIFT_RECEIVED, self._on_activity)
        self.on(EVENTS.USER_COMMAND, self._on_activity)
        self.on(EVENTS.USER_MESSAGE, self._on_activity)   # 重置 idle
        self.on(EVENTS.USER_MESSAGE, self._on_user_intent)  # 新增：意图分类
        # 内心侧 bond：谁常来、跟谁更亲近
        self.on(EVENTS.USER_MESSAGE, self._on_bond_touch)
        self.on(EVENTS.GIFT_RECEIVED, self._on_bond_gift)
        # 任务过程监控（思维脑接受监控其他脑的任务）
        self.on(EVENTS.TASK_STARTED, self._on_task_started)
        self.on(EVENTS.OPERATOR_PROGRESS, self._on_operator_progress)
        self.on(EVENTS.OPERATOR_DONE, self._on_operator_done)
        self.on(EVENTS.TASK_DONE, self._on_task_finished)
        self.on(EVENTS.TASK_FAILED, self._on_task_finished)
        # 进度查询（主播脑转发 → 思维脑取数 → 生成报告 emit 回主播脑）
        self.on(EVENTS.PROGRESS_QUERY, self._on_progress_query)
        # 知识检索归思维脑：检索分块知识库 → 发布共享缓存，供主播脑读取
        self.on(EVENTS.USER_MESSAGE, self._on_knowledge_demand)
        self.on(EVENTS.BILIBILI_MSG, self._on_knowledge_demand)
        # activity（重置 idle）由上面 handlers 内部调用 _on_activity 覆盖，
        # 不再单独绑定 _on_activity 到 TASK_DONE / TASK_FAILED（避免重复）
        self.on(EVENTS.TTS_SPEAK, self._on_activity)
        _log_event("INFO", "will_engine",
                   f"setup 完成: USER_MESSAGE 监听={sum(1 for l in self.bus.listeners.get(EVENTS.USER_MESSAGE, []))}")
        # 后台预热知识库，避免启动后第一条消息等待集合加载
        self._warm_task = asyncio.create_task(
            self._warm_knowledge(), name="will-knowledge-warm")
        logger.info(
            f"[brain:{self.name}] setup complete "
            f"(mode={self.decision_mode})"
        )

    # ---------- bond（内心侧关系感） ----------

    @staticmethod
    def _payload_user(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("user", "username", "uname", "uid"):
            value = payload.get(key)
            if value not in (None, "", "?", "unknown"):
                return str(value)
        return ""

    async def _on_bond_touch(self, payload: Any) -> None:
        soul = self._get_soul()
        user = self._payload_user(payload)
        if soul is None or not user:
            return
        try:
            soul.touch_bond(user)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] bond 更新失败: {e}")

    async def _on_bond_gift(self, payload: Any) -> None:
        soul = self._get_soul()
        user = self._payload_user(payload)
        if soul is None or not user:
            return
        try:
            soul.touch_bond(user, familiarity=0.03, attachment=0.02, note="gift")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] gift bond 更新失败: {e}")

    # ---------- 知识检索（思维脑的知识件）----------

    @classmethod
    def _needs_knowledge(cls, text: str) -> bool:
        """粗筛：像不像在问"知识/资料"问题。闲聊不触发检索，避免每条消息多等 1 秒。"""
        t = (text or "").strip()
        return any(hint in t for hint in cls.KNOWLEDGE_HINTS)

    async def _on_knowledge_demand(self, payload: Any) -> None:
        """思维脑接管知识检索：检索分块知识库并发布到共享缓存。

        主播脑只读缓存，不再直接访问知识库。检索不到时由知识件记下缺口，
        空闲决策可据此把「还没弄明白的」当作探索目标（走既有联网研究）。
        """
        if not isinstance(payload, dict):
            return
        text = str(payload.get("text") or "").strip()
        if (len(text) < self.MIN_KNOWLEDGE_CHARS
                or self._looks_actionable(text)
                or not self._needs_knowledge(text)):
            return

        knowledge = get_will_knowledge()
        knowledge.begin(text)   # 先同步打标，主播脑才知道要等
        items, error = await knowledge.search(text)
        await knowledge.publish(text, items, error)
        if items:
            logger.debug(
                f"[brain:{self.name}] 知识检索 {len(items)} 块: {text[:30]}")
        elif error:
            logger.debug(f"[brain:{self.name}] 知识检索不可用: {error[:80]}")

    # ---------- 任务过程监控（思维脑：监控所有脑的任务）----------

    async def _on_task_started(self, payload: Any):
        """任务开始：登记到任务监控表 + 重置 idle"""
        await self._on_activity(payload)
        if not isinstance(payload, dict):
            return
        task = payload.get("task", {})
        task_id = self._task_id(task)
        self._task_monitor[task_id] = {
            "description": task.get("description", task.get("tool", "?")),
            "tool": task.get("tool", ""),
            "status": "running",
            "percent": 0,
            "stage": "started",
            "started_at": __import__("time").time(),
            "detail": "",
        }
        _log_event("INFO", "will_engine",
                   f"任务开始监控: {task.get('description','?')}")

    async def _on_operator_progress(self, payload: Any):
        """任务进度：更新监控表（思维脑实时掌握操作脑进度）"""
        if not isinstance(payload, dict):
            return
        task = payload.get("task", {})
        prog = payload.get("progress", {})
        task_id = self._task_id(task)
        entry = self._task_monitor.setdefault(task_id, {
            "description": task.get("description", task.get("tool", "?")),
            "tool": task.get("tool", ""),
            "status": "running",
            "percent": 0,
            "stage": "started",
            "started_at": __import__("time").time(),
            "detail": "",
        })
        entry["percent"] = prog.get("percent", entry.get("percent", 0))
        entry["stage"] = prog.get("stage", entry.get("stage", ""))
        entry["detail"] = prog.get("detail", "")
        # 只记最近 20 条进度，供 WebUI / 调试
        self._task_progress_history.append({
            "task_id": task_id,
            "percent": entry["percent"],
            "stage": entry["stage"],
            "detail": entry["detail"],
            "at": __import__("time").time(),
        })
        if len(self._task_progress_history) > 20:
            self._task_progress_history = self._task_progress_history[-20:]
        logger.debug(f"[brain:{self.name}] 监控进度 [{task_id}] "
                     f"{entry['percent']}% {entry['detail']}")

    async def _on_operator_done(self, payload: Any):
        """操作脑完成：更新状态 + 重置 idle"""
        if not isinstance(payload, dict):
            return
        task = payload.get("task", {})
        result = payload.get("result", {})
        task_id = self._task_id(task)
        entry = self._task_monitor.get(task_id)
        if entry is None:
            entry = {
                "description": task.get("description", task.get("tool", "?")),
                "tool": task.get("tool", ""),
                "started_at": __import__("time").time(),
            }
            self._task_monitor[task_id] = entry
        entry["status"] = "done" if result.get("success") else "failed"
        entry["percent"] = 100
        entry["stage"] = "done"
        entry["finished_at"] = __import__("time").time()

        # 思维脑验收：把"执行方自称成功"换成"对着子目标核对过"
        verified = await self._verify_task(task, result)
        if isinstance(verified, dict):
            entry["status"] = "done" if verified.get("success") else "failed"
            result = verified
        # 思维脑收尾判定：去台账里确认这条任务的最终状态
        self._close_ledger_task(task, result)

    def _close_ledger_task(self, task: Any, result: Any) -> None:
        """核对执行结果后把台账任务置为终态（done/failed）。"""
        if not isinstance(task, dict):
            return
        tid = task.get("ci_task_id") or task.get("_ci_task_id")
        if not tid:
            return
        result = result if isinstance(result, dict) else {}
        if "success" in result:
            ok = bool(result.get("success"))
        else:
            ok = not bool(result.get("error"))
        detail = str(result.get("summary") or result.get("error")
                     or ("完成" if ok else "失败"))[:200]
        self._close_task(tid, ok=ok, detail=detail)

    def _close_task(self, task_id: str, ok: bool, detail: str = "") -> None:
        """关账：只有思维脑能走到这里（TaskStore 层也会校验 actor）。"""
        try:
            from LLM_v2.domains.central_info.task_store import TaskStore
            res = TaskStore().close(task_id, ok=ok, detail=detail,
                                    actor="will_engine")
            if not res.get("ok"):
                logger.warning(f"[brain:{self.name}] 台账收尾被拒: "
                               f"{res.get('error')}")
            else:
                _log_event("INFO", "will_engine",
                           f"任务关账：{task_id} → {'done' if ok else 'failed'}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 台账收尾失败: {e}")

    def _recover_interrupted_tasks(self) -> None:
        """启动时收尾上次进程遗留的未完成台账（思维脑独占关账）。

        任务的执行状态只在本进程内存里，重启后无法续跑，所以这里直接判定
        「中断」；已经跑完、只差验收的（stage=awaiting_review）按「未完成
        验收」记失败，避免绕过验收被当成 done。
        """
        try:
            from LLM_v2.domains.central_info.task_store import TaskStore
            rows = TaskStore().list(limit=100)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 启动恢复：台账不可用: {e}")
            return
        for t in rows:
            task_id = str(t.get("task_id") or "")
            if not task_id:
                continue
            if str(t.get("stage") or "") == "awaiting_review":
                self._close_task(task_id, ok=False,
                                 detail="服务重启，未完成验收")
            else:
                self._close_task(task_id, ok=False,
                                 detail="服务重启，任务中断")
        if rows:
            logger.info(f"[brain:{self.name}] 启动恢复：已收尾 "
                        f"{len(rows)} 条遗留任务")

    async def _ledger_patrol(self) -> None:
        """定期巡检台账：看还有哪些任务在跑、跑到哪，并负责关账。

        - 执行脑已报告「执行完成」（stage=awaiting_review）→ 核对后关闭
        - 长期无进展（默认 5 分钟）→ 判定中断并关闭
        - 其余只记录巡检，不打扰任务
        与 autonomous 开关无关：只要台账里有任务就会检查。
        """
        try:
            from LLM_v2.domains.central_info.task_store import TaskStore
            rows = TaskStore().list(limit=20)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 台账巡检不可用: {e}")
            return
        # 先记时间戳：即使当前没任务，也能看出思维脑的巡检是活的
        self._last_patrol_at = time.time()
        self._patrol_watching = sum(
            1 for r in rows if r.get("phase") == "running")

        now = time.time()
        checked = []
        for t in rows:
            task_id = str(t.get("task_id") or "")
            if not task_id:
                continue
            title = str(t.get("title") or "")[:40]
            phase = str(t.get("phase") or "")
            percent = int(t.get("percent") or 0)
            stage = str(t.get("stage") or "")
            age = now - (float(t.get("updated_at") or 0) or now)

            if stage == "awaiting_review":
                self._close_task(task_id, ok=percent >= 100,
                                 detail=str(t.get("detail") or "")[:200])
                continue
            if age > TASK_STALL_SEC:
                self._close_task(
                    task_id, ok=False,
                    detail=f"超过 {int(age)}s 无进展，判定中断")
                continue
            checked.append(f"{title} {percent}%")

        if checked:
            logger.debug(f"[brain:{self.name}] 台账巡检："
                         + "；".join(checked))

        # 巡检的第二步：根据共享空间 + 台账，自己决定要不要立项
        await self._maybe_propose_tasks(rows)

    def _shared_space_block(self) -> str:
        """共享空间：各能力域的实时卡片 + 最近对话（CentralContext）。"""
        try:
            from LLM_v2.core.central_context import get_central
            return get_central().overview_text(chat_limit=4, domain_limit=6)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 共享空间读取失败: {e}")
            return ""

    async def _maybe_propose_tasks(self, ledger_rows: list) -> None:
        """自主立项：读共享空间与台账，判断是否需要新建任务并交给操作脑。

        保护：仅自主模式、冷却期内不重复、进行中任务数有上限、
        与台账已有标题重复的提案直接丢弃。
        """
        if not self.autonomous:
            return
        now = time.time()
        if now - self._last_task_proposal_at < TASK_PROPOSE_COOLDOWN:
            return

        active = [r for r in ledger_rows
                  if r.get("phase") in ("running", "pending", "blocked")]
        if len(active) >= TASK_MAX_ACTIVE:
            logger.debug(f"[brain:{self.name}] 已有 {len(active)} 个任务，暂不立项")
            return

        llm = self._get_llm()
        if llm is None or not getattr(llm, "api_key", None):
            return

        shared = self._shared_space_block()
        ledger = self._ledger_block()
        if not shared and not ledger:
            return

        titles = [str(r.get("title") or "") for r in ledger_rows]
        # 刚结束（含失败）的任务也算重复：否则验收没过会立刻再立一次同样的项
        try:
            from LLM_v2.domains.central_info.task_store import TaskStore
            cutoff = now - TASK_PROPOSE_DEDUP_SEC
            titles += [str(r.get("title") or "")
                       for r in TaskStore().list(limit=50, include_terminal=True)
                       if float(r.get("updated_at") or 0) >= cutoff]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 立项去重读取历史失败: {e}")
        prompt = (
            "你是维尔薇的意志脑。请根据下面的【共享空间】和【任务台账】，"
            "判断现在是否需要给自己新开一个任务。\n"
            "规则：\n"
            "1) 只有确实有价值、且台账里没有在做的事，才立项；否则返回 []\n"
            "2) 一次最多 1 条，不要为了忙而忙\n"
            f"3) 可用工具：{'、'.join(PROPOSABLE_TOOLS)}\n"
            "   research_task=联网研究/学习；cultist_task=玩密教模拟器；"
            "code_task=写代码；tool_agent=其他多步操作\n"
            "4) goal 用一句具体中文描述，不能与台账已有任务重复\n"
            '只输出 JSON 数组，例如 '
            '[{"tool":"research_task","goal":"了解向量数据库的最新进展","reason":"观众提过"}]'
            "\n\n【共享空间】\n" + (shared or "（空）")
            + "\n\n" + (ledger or "（台账为空）")
        )
        try:
            text = await asyncio.wait_for(
                llm.chat_simple(
                    system_prompt="你是谨慎的任务规划器，只输出 JSON 数组。",
                    user_text=prompt,
                ),
                timeout=20.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 自主立项调用失败: {e}")
            return
        self._last_task_proposal_at = now

        proposals = self._parse_proposals(text)
        logger.debug(f"[brain:{self.name}] 立项原始输出: {str(text)[:200]}")
        if not proposals:
            logger.info(f"[brain:{self.name}] 自主立项：本轮无需新任务")
            return
        for item in proposals[:1]:
            tool = str(item.get("tool") or "").strip()
            goal = str(item.get("goal") or "").strip()
            if tool not in PROPOSABLE_TOOLS or not goal:
                continue
            if self._duplicate_goal(goal, titles):
                logger.debug(f"[brain:{self.name}] 立项与台账重复，跳过: {goal[:40]}")
                continue
            await self._create_task(tool, goal, str(item.get("reason") or ""))

    @staticmethod
    def _parse_proposals(text: str) -> list:
        """从模型输出里抠出 JSON 数组（容忍前后废话）。"""
        if not text:
            return []
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except Exception:  # noqa: BLE001
            return []
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    @staticmethod
    def _duplicate_goal(goal: str, titles: list) -> bool:
        """粗去重：目标前 10 个字出现在任何已有标题里就视为重复。"""
        key = goal.strip()[:10]
        if not key:
            return False
        return any(key in (t or "") for t in titles)

    async def _create_task(self, tool: str, goal: str, reason: str) -> None:
        """在台账立项（owner=will_engine），再把任务交给操作脑执行。"""
        task_id = ""
        try:
            from LLM_v2.domains.central_info.task_store import TaskStore
            rec = TaskStore().create(f"{tool}｜{goal}"[:120],
                                     owner="will_engine", stage=tool,
                                     detail=reason[:200], phase="pending")
            task_id = rec.get("task_id") or ""
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 自主立项写台账失败: {e}")
            return
        if not task_id:
            return

        args = {"goal": goal, "request": goal}
        if tool == "research_task":
            args["max_sources"] = 2
        logger.info(f"[brain:{self.name}] 自主立项: {tool}｜{goal[:50]}")
        _log_event("INFO", "will_engine",
                   f"自主立项：{tool}｜{goal[:60]}")
        await self.emit(EVENTS.WILL_ENGINE_ACTION, {
            "type": "execute_tool",
            "tool": tool,
            "args": args,
            "description": goal[:60],
            "reasoning": reason or "自主立项",
            "source": "will_engine_autonomous",
            "ci_task_id": task_id,
        })

    async def _on_task_finished(self, payload: Any):
        """任务结束（TASK_DONE / TASK_FAILED）：标记完成 + 重置 idle + 状态反映"""
        await self._on_activity(payload)
        if not isinstance(payload, dict):
            return
        task = payload.get("task", {})
        result = payload.get("result", {})
        task_id = self._task_id(task)
        entry = self._task_monitor.get(task_id)
        if entry is not None:
            entry["status"] = "done" if result.get("success") else "failed"
            entry["stage"] = "done"
            entry["finished_at"] = time.time()

        # 结果回填：把这次执行结果记到对应决策上（经验读取时现场聚合）
        decision_id = task.get("decision_id") if isinstance(task, dict) else None
        if decision_id:
            soul = self._get_soul()
            if soul is not None:
                started = entry.get("started_at") if entry else None
                duration = max(0.0, time.time() - float(started)) if started else 0.0
                soul.record_outcome(
                    int(decision_id),
                    success=bool(result.get("success")),
                    duration_sec=duration,
                    error=str(result.get("error") or payload.get("error") or ""),
                    task_id=task_id,
                    note=task.get("tool") or "",
                )
                self._schedule_distill()

        # B 站视频域：意志脑负责"总结要说什么"，再交主播脑发声（三脑分工）
        try:
            await self._narrate_bili_video_content(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] B 站内容总结失败（不影响任务）: {e}")

        # 联网研究：意志脑把研究结果转成一句主播话术
        try:
            await self._narrate_research_result(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 研究结果总结失败（不影响任务）: {e}")

        # 密教模拟器：意志脑把游玩结果转成一句主播话术
        try:
            await self._narrate_cultist_result(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 密教游玩总结失败（不影响任务）: {e}")

        # 中央信息：意志脑把全局摘要转成主播话术
        try:
            await self._narrate_central_result(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 中央信息总结失败（不影响任务）: {e}")

        # 通用工具智能体：把执行结论转成主播话术（含 chat_key 路由）
        try:
            await self._narrate_agent_result(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 工具智能体总结失败（不影响任务）: {e}")

    @staticmethod
    def _task_id(task: dict) -> str:
        """生成稳定的任务 id（优先外部 id，否则用 tool+description 摘要）

        中央台账的 task_id 优先：操作脑开工时会写进 _ci_task_id，
        这样监控表与台账是同一条记录，WebUI 不会出现重复行。
        """
        if not isinstance(task, dict):
            task = {}
        tid = (task.get("_ci_task_id") or task.get("ci_task_id")
               or task.get("task_id") or task.get("id"))
        if tid:
            return str(tid)
        return f"{task.get('tool','?')}:{task.get('description','?')[:30]}"

    # ---------- B 站视频域：意志脑总结 → 交主播脑发声 ----------

    @staticmethod
    def _unwrap_tool_payload(node: Any) -> Any:
        """剥掉工具返回的多层 result 包装，取出业务数据"""
        for _ in range(4):
            if isinstance(node, dict) and isinstance(node.get("result"), (dict, list)):
                node = node["result"]
            else:
                break
        return node

    @staticmethod
    def _summarize_bv_payload(data: Any) -> str:
        """把 bv_* 工具的业务数据压成一段可读文本（限长，避免撑爆上下文）"""
        if not isinstance(data, dict) or data.get("success") is False:
            return ""
        if data.get("text"):                       # 字幕
            return (f"视频 {data.get('bvid', '?')} 字幕（来源 {data.get('source')}）：\n"
                    + str(data["text"])[:1000])
        if isinstance(data.get("replies"), list):  # 评论区主楼
            lines = [f"- {r.get('user', '?')}：{r.get('message', '')}"
                     for r in data["replies"][:8]]
            return (f"视频 {data.get('bvid', '?')} 评论区（共 {data.get('total')} 条），"
                    f"前 {len(lines)} 条主楼：\n" + "\n".join(lines))
        if isinstance(data.get("list"), list):     # 首页推荐 / 投稿列表
            kind = "投稿" if data.get("uid") else "首页推荐"
            lines = [f"- {it.get('title')}（{it.get('bvid')}）" for it in data["list"][:10]]
            return f"B 站{kind} {len(lines)} 条：\n" + "\n".join(lines)
        if data.get("title"):                      # 视频详情
            stat = data.get("stat") or {}
            return (f"视频 {data.get('bvid')}《{data.get('title')}》"
                    f"，UP主 {((data.get('owner') or {}).get('name'))}"
                    f"，播放 {stat.get('view')} 点赞 {stat.get('like')} 评论 {stat.get('reply')}"
                    f"；简介：{str(data.get('desc') or '')[:200]}")
        if data.get("name"):                       # 空间资料
            parts = [f"UP主 {data.get('name')}（UID {data.get('uid')}）"]
            if data.get("level") is not None:
                parts.append(f"等级 {data.get('level')}")
            if data.get("fans") is not None:
                parts.append(f"粉丝 {data.get('fans')}")
            if data.get("following") is not None:
                parts.append(f"关注 {data.get('following')}")
            if data.get("sign"):
                parts.append(f"签名：{data.get('sign')}")
            return "，".join(parts)
        if data.get("action"):                     # 写操作回执（点赞/投币/收藏）
            return f"已执行 {data.get('action')}：{data.get('bvid') or data.get('aid')}"
        return json.dumps(data, ensure_ascii=False)[:600]

    async def _narrate_bili_video_content(self, payload: Any) -> None:
        """bv_ 工具成功完成 → 意志脑总结成"要说的话" → emit PROGRESS_REPORT 交主播脑发声

        三脑分工：意志脑**想说什么**，主播脑只负责**说**（emit TTS_SPEAK）。
        bili_video 域零音频职责，声音统一由 tts 域合成播放。
        """
        if not isinstance(payload, dict):
            return
        task = payload.get("task") or {}
        tool = str(task.get("tool") or "")
        if not tool.startswith("bv_"):
            return
        result = payload.get("result") or {}
        if not result.get("success"):
            return
        data = self._unwrap_tool_payload(result)
        summary = self._summarize_bv_payload(data)
        if not summary:
            return

        # 防刷屏：同一目标 60s 内只总结播报一次
        dedup_key = str(data.get("bvid") or data.get("uid") or tool)
        now = time.time()
        if now - self._bv_narrated.get(dedup_key, 0.0) < 60.0:
            logger.debug(f"[brain:{self.name}] B 站内容 {dedup_key} 60s 内已播报，跳过")
            return
        self._bv_narrated[dedup_key] = now

        llm = self._get_llm()
        if llm is None:
            return
        system_prompt = self.runtime.build_system_prompt("streamer")
        user_text = (
            "【任务】你刚读到的 B 站内容如下。请用 1~2 句自然口语总结给观众听，"
            "不要罗列字段、不要提工具名或实现细节。\n" + summary[:1500]
        )
        try:
            text = await asyncio.wait_for(
                llm.chat_simple(system_prompt=system_prompt, user_text=user_text),
                timeout=15.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] B 站内容总结调用失败: {e}")
            return
        text = (text or "").strip()
        if not text:
            return
        source = str(task.get("source") or "webui")
        _log_event("INFO", "will_engine", f"B 站内容总结 → 主播脑: {text[:60]}")
        await self.emit(EVENTS.PROGRESS_REPORT, {
            "report": text,
            "source": source,
            "user": "system",
            "perception_context": summary,
        })

    _STUDY_MARKERS = ("整理成", "经验", "记忆", "学习成果", "示例", "教我",
                      "学习一下", "学到什么")

    def _is_study_request(self, request: str, topic: str) -> bool:
        text = f"{request or ''} {topic or ''}"
        return any(k in text for k in self._STUDY_MARKERS)

    async def _build_study_report(self, topic: str, summary: str,
                                  sources: Any, request: str) -> str:
        """把研究资料整理成：成果 / 原理 / 防御经验 / 安全示例 / 记忆条目。"""
        try:
            from LLM_v2.core.llm_adapter import get_operator_llm
            llm = get_operator_llm()
        except Exception:  # noqa: BLE001
            llm = self._get_llm()
        if llm is None:
            return ""

        head = (summary or "")[:300]
        if any(m in head for m in ("未获取到", "没查到", "资料不足",
                                   "无法确认", "没有找到", "403")):
            logger.warning("[brain:will_engine] 研究资料不可用，跳过学习报告生成")
            return ""

        source_text = "\n".join(f"- {s}" for s in (sources or [])[:5])
        system = (
            "你是维尔薇。你刚完成一次联网学习，现在要给用户一份中文学习报告。\n"
            "要求：\n"
            "1. 只依据给定资料；不确定就明确标注，不要编造来源。\n"
            "2. 不要提供可直接用于攻击的完整载荷；代码示例必须是防御性的、可本地运行。\n"
            "3. 用 Markdown 输出，结构固定：\n"
            "## 学习成果\n- 3~5 条关键事实\n"
            "## 原理\n- 简明解释\n"
            "## 防御与经验\n- 3~5 条可操作要点\n"
            "## 安全小示例\n```python\n...\n```\n"
            "## 记忆条目\n- 一句话总结，方便下次复用\n"
            "## 资料来源\n- 只列实际来源"
        )
        user = (
            f"学习主题：{topic}\n"
            f"用户原始要求：{(request or '')[:300]}\n\n"
            f"资料总结：\n{(summary or '')[:2500]}\n\n"
            f"实际来源：\n{source_text}"
        )
        try:
            result = await asyncio.wait_for(
                llm.chat(
                    system_prompt=system,
                    user_text=user,
                    max_tokens=2600,
                    remember=False,
                ),
                timeout=60.0,
            )
            if result.get("error"):
                logger.warning(f"[brain:{self.name}] 学习报告生成失败: {result['error']}")
                return ""
            report = str(result.get("response") or "").strip()
            if report and "## 资料来源" not in report:
                report += "\n\n## 资料来源\n" + (source_text or "- （本次未读到可用页面）")
            return report
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 学习报告生成失败: {e}")
            return ""

    def _save_study_package(self, topic: str, report: str,
                            sources: Any) -> tuple:
        """把学习成果写入笔记（资料/记忆）和程序性经验。"""
        store = getattr(getattr(self.runtime, "self_core", None), "learning", None)
        if store is None:
            try:
                from persona.learning.store import LearningStore
                store = LearningStore()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[brain:{self.name}] LearningStore 不可用: {e}")
                return {}, {}

        tags = ("study,security"
                if any(k in topic for k in ("安全", "攻击", "漏洞", "注入",
                                            "渗透", "加密", "防御"))
                else "study")
        note: Dict[str, Any] = {}
        proc: Dict[str, Any] = {}
        try:
            note = store.save_note(
                topic=f"学习：{topic}"[:200],
                summary=report,
                sources=list(sources or [])[:20],
                tags=tags,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 学习笔记保存失败: {e}")
        try:
            slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", topic).strip("-")[:60]
            proc = store.upsert_procedure(
                signature=f"study:{slug or 'topic'}",
                when_to_use=f"当用户询问「{topic}」或需要相关安全示例时",
                steps=["检索权威资料", "提炼原理与关键事实",
                       "整理防御/经验要点", "给出安全示例", "记录来源"],
                done_when="报告包含原理、防御要点、安全示例和来源",
                avoid=["不要提供可直接执行的攻击载荷",
                       "不要编造资料未支持的事实"],
                evidence=[f"research:{topic}"],
                success=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 学习经验保存失败: {e}")
        return note, proc

    async def _narrate_research_result(self, payload: Any) -> None:
        """research_task 成功 → 意志脑把资料变成一句主播话术 → PROGRESS_REPORT"""
        if not isinstance(payload, dict):
            return
        task = payload.get("task") or {}
        tool = str(task.get("tool") or "")
        if tool not in ("research_task", "wr_notes"):
            return
        result = payload.get("result") or {}
        if not result.get("success"):
            return
        data = self._unwrap_tool_payload(result)
        if not isinstance(data, dict):
            data = result

        if tool == "wr_notes":
            notes = data.get("notes") or []
            topic = "我最近查过的资料"
            if notes:
                summary = "\n".join(
                    f"- {n.get('topic', '')}：{str(n.get('summary') or '')[:160]}"
                    for n in notes[:5]
                )
            else:
                summary = "我还没查过什么资料。"
        else:
            summary = str(data.get("summary") or "").strip()
            topic = str(data.get("topic") or task.get("description") or "刚查的资料")
        if not summary:
            return

        dedup_key = f"research:{topic}"
        now = time.time()
        if now - self._research_narrated.get(dedup_key, 0.0) < 60.0:
            logger.debug(f"[brain:{self.name}] 研究主题 {topic} 60s 内已播报，跳过")
            return
        self._research_narrated[dedup_key] = now

        source = str(task.get("source") or "webui")
        request = str((task.get("args") or {}).get("request") or "")

        # 学习型请求：输出结构化学习报告 + 安全示例，并落笔记/经验
        if tool == "research_task" and self._is_study_request(request, topic):
            sources = list(data.get("sources") or [])
            report = await self._build_study_report(
                topic, summary, sources, request)
            if report:
                note, proc = self._save_study_package(topic, report, sources)
                note_txt = (f"#{note.get('id')}" if note else "保存失败")
                proc_txt = (f"#{proc.get('id')}" if proc else "保存失败")
                header = (
                    f"【学习完成】{topic}\n"
                    f"已整理：{len(sources)} 个来源｜学习笔记 {note_txt}｜"
                    f"经验 {proc_txt}\n\n"
                )
                full = header + report
                _log_event(
                    "INFO", "will_engine",
                    f"学习报告完成: {topic[:50]}｜笔记 {note_txt}｜经验 {proc_txt}")
                await self.emit(EVENTS.PROGRESS_REPORT, {
                    "report": full,
                    "source": source,
                    "user": "system",
                    "perception_context": full,
                })
                return

        llm = self._get_llm()
        if llm is None:
            return
        system_prompt = self.runtime.build_system_prompt("streamer")
        user_text = (
            f"【任务】你刚联网查完「{topic}」。请用 1~2 句自然口语把最重要的发现"
            "告诉观众，不要罗列来源链接、不要提工具名或实现细节。资料如下：\n"
            + summary[:1500]
        )
        try:
            text = await asyncio.wait_for(
                llm.chat_simple(system_prompt=system_prompt, user_text=user_text),
                timeout=15.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 研究结果总结调用失败: {e}")
            return
        text = (text or "").strip()
        if not text:
            return

        _log_event("INFO", "will_engine", f"研究结果总结 → 主播脑: {text[:60]}")
        await self.emit(EVENTS.PROGRESS_REPORT, {
            "report": text,
            "source": source,
            "user": "system",
            "perception_context": summary,
        })

    async def _narrate_cultist_result(self, payload: Any) -> None:
        """cultist_task / cs_knowledge / cs_status / cs_world → 主播话术"""
        if not isinstance(payload, dict):
            return
        task = payload.get("task") or {}
        tool = str(task.get("tool") or "")
        if tool not in ("cultist_task", "cs_knowledge", "cs_status", "cs_world"):
            return
        result = payload.get("result") or {}
        data = self._unwrap_tool_payload(result)
        if not isinstance(data, dict):
            return

        if tool == "cs_knowledge":
            lessons = data.get("lessons") or []
            recipes = data.get("recipes") or []
            lines = [f"- {r.get('topic')}：{r.get('text')}" for r in lessons[:5]]
            lines += [f"- 配方：{r.get('verb_label')} → "
                      + "、".join(str(o.get("label")) for o in r.get("outputs") or [])
                      for r in recipes[:3]]
            summary = "\n".join(lines) if lines else "我还没学到什么密教经验。"
        elif tool == "cs_status":
            cards = data.get("cards") or []
            verbs = data.get("verbs") or []
            if not cards and not verbs:
                summary = "当前不在牌桌上。"
            else:
                verb_txt = "、".join(
                    f"{v.get('label')}({v.get('state')})" for v in verbs[:5])
                card_txt = "、".join(
                    f"{c.get('label')}x{c.get('quantity', 1)}" for c in cards[:8])
                summary = (f"遗产：{data.get('legacy_label') or '未知'}；"
                           f"桌面卡片：{card_txt or '无'}；动词：{verb_txt or '无'}")
        elif tool == "cs_world":
            counts = data.get("counts") or {}
            lessons = data.get("lessons") or []
            summary = (f"域内缓存：{counts.get('cards', 0)} 种卡、"
                       f"{counts.get('verbs', 0)} 种动词、"
                       f"{counts.get('recipes', 0)} 条配方、"
                       f"{counts.get('lessons', 0)} 条经验。\n"
                       + "\n".join(f"- {r.get('topic')}：{r.get('text')}"
                                   for r in lessons[-3:]))
        elif not data.get("success"):
            error = str(data.get("error") or "没能玩下去")
            summary = f"密教模拟器没玩成：{error}"
        else:
            summary = str(data.get("summary") or "").strip()
        if not summary:
            return

        llm = self._get_llm()
        if llm is None:
            return
        system_prompt = self.runtime.build_system_prompt("streamer")
        if tool == "cs_knowledge":
            lead = ("【任务】观众在问你在《密教模拟器》里学到了什么。请用 1~2 句自然口语"
                    "说说你总结出的经验，不要罗列 id、不要提工具名。你的笔记如下：\n")
        elif tool == "cs_status":
            lead = ("【任务】观众在问《密教模拟器》现在的牌桌状态。请用 1~2 句自然口语"
                    "说说桌面有什么、正在做什么，不要罗列 id 或接口名。局面如下：\n")
        elif tool == "cs_world":
            lead = ("【任务】观众在问你在密教模拟器里积累了什么。请用 1~2 句自然口语"
                    "说说你的卡牌/配方/经验积累，不要罗列 id 或接口名。缓存如下：\n")
        else:
            ending = str(data.get("ending") or "").strip()
            if ending:
                lead = (f"【任务】你刚玩完一局《密教模拟器》，达成了结局「{ending}」。"
                        "请用 2~3 句自然口语向观众解说明白：这是什么样的结局、"
                        "你是怎么做到的、有什么经验。不要罗列 id、不要提工具名或接口。"
                        "经过如下：\n")
            else:
                lead = ("【任务】你刚玩了一小段《密教模拟器》。请用 1~2 句自然口语向观众说说"
                        "你做了什么、现在局面如何、学到什么，不要罗列 id、不要提工具名或接口。"
                        "经过如下：\n")
        user_text = lead + summary[:1500]
        try:
            text = await asyncio.wait_for(
                llm.chat_simple(system_prompt=system_prompt, user_text=user_text),
                timeout=15.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 密教游玩总结调用失败: {e}")
            return
        text = (text or "").strip()
        if not text:
            return

        source = str(task.get("source") or "webui")
        _log_event("INFO", "will_engine", f"密教游玩总结 → 主播脑: {text[:60]}")
        await self.emit(EVENTS.PROGRESS_REPORT, {
            "report": text,
            "source": source,
            "user": "system",
            "perception_context": summary,
        })

    async def _narrate_agent_result(self, payload: Any) -> None:
        """tool_agent 结束 → 把实际执行结果转成主播话术 → PROGRESS_REPORT"""
        if not isinstance(payload, dict):
            return
        task = payload.get("task") or {}
        tool = str(task.get("tool") or "")
        if tool not in ("tool_agent", "agent_task"):
            return
        result = payload.get("result") or {}
        data = self._unwrap_tool_payload(result)
        if not isinstance(data, dict):
            return

        success = bool(data.get("success"))
        answer = str(data.get("answer") or "").strip()
        steps = data.get("steps") or []
        lines: List[str] = []
        for i, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            status = "成功" if step.get("success") else "失败"
            err = f"（{step.get('error')}）" if step.get("error") else ""
            lines.append(f"{i}. {step.get('tool')} → {status}{err}")
        summary = "\n".join(lines) or "没有执行任何工具。"
        if answer:
            summary = f"结论：{answer}\n步骤：\n{summary}"

        llm = self._get_llm()
        text = ""
        if llm is not None:
            system_prompt = self.runtime.build_system_prompt("streamer")
            lead = (
                "【任务】你刚替用户执行完一个请求。请用 1~2 句自然口语告诉用户结果；"
                "只输出纯台词，不要动作描写，不要提工具名、接口名或内部 id。"
            )
            if not success:
                lead += " 没做成时如实说卡在哪一步，绝不能编造成功。"
            try:
                text = await asyncio.wait_for(
                    llm.chat_simple(
                        system_prompt=system_prompt,
                        user_text=lead + "\n" + summary[:1500],
                    ),
                    timeout=15.0,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[brain:{self.name}] 工具智能体话术失败: {e}")
        text = (text or answer or "").strip()
        if not text:
            return

        source = str(task.get("source") or "webui")
        _log_event("INFO", "will_engine", f"工具智能体总结 → 主播脑: {text[:60]}")
        await self.emit(EVENTS.PROGRESS_REPORT, {
            "report": text,
            "source": source,
            "user": task.get("user") or "system",
            "chat_key": task.get("chat_key") or "",
            "perception_context": answer,
        })

    async def _narrate_central_result(self, payload: Any) -> None:
        """ci_* 工具结束 → 意志脑把中央信息转成主播话术 → PROGRESS_REPORT"""
        if not isinstance(payload, dict):
            return
        task = payload.get("task") or {}
        tool = str(task.get("tool") or "")
        if tool not in ("ci_overview", "ci_domain", "ci_chat"):
            return
        result = payload.get("result") or {}
        data = self._unwrap_tool_payload(result)
        if not isinstance(data, dict) or not data.get("success"):
            return

        if tool == "ci_overview":
            summary = str(data.get("text") or ""
                          ).replace("【中央信息】", "").strip()
        elif tool == "ci_domain":
            card = data.get("domain") or {}
            channels = "；".join(
                f"{c.get('name')}（{'、'.join(c.get('tools') or [])}）"
                for c in card.get("channels") or []
            )
            summary = (f"{card.get('label')}：{card.get('summary') or ''}"
                       + (f"；进展：{card.get('progress')}" if card.get("progress") else "")
                       + (f"；详情渠道：{channels}" if channels else ""))
        else:
            rows = data.get("chat") or []
            summary = "\n".join(
                f"- {'观众' if r.get('role') == 'user' else '我'}"
                f"({r.get('user') or r.get('source') or '?'})：{r.get('text')}"
                for r in rows[:6]
            )
        if not summary:
            return

        llm = self._get_llm()
        if llm is None:
            return
        system_prompt = self.runtime.build_system_prompt("streamer")
        user_text = (
            "【任务】观众在问当前的整体情况。请用 1~2 句自然口语说说：我这边在做什么、"
            "其他能力域/聊天里有什么，不要罗列工具名或内部字段。信息如下：\n"
            + summary[:1500]
        )
        try:
            text = await asyncio.wait_for(
                llm.chat_simple(system_prompt=system_prompt, user_text=user_text),
                timeout=15.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[brain:{self.name}] 中央信息总结调用失败: {e}")
            return
        text = (text or "").strip()
        if not text:
            return

        source = str(task.get("source") or "webui")
        _log_event("INFO", "will_engine", f"中央信息总结 → 主播脑: {text[:60]}")
        await self.emit(EVENTS.PROGRESS_REPORT, {
            "report": text,
            "source": source,
            "user": "system",
            "perception_context": summary,
        })

    def task_monitor_snapshot(self) -> dict:
        """任务监控快照（供 WebUI / 调试）"""
        return {
            "running": {k: v for k, v in self._task_monitor.items()
                        if v.get("status") == "running"},
            "recent": list(self._task_progress_history[-10:]),
        }

    # ---------- 进度查询（思维脑取数 → 生成报告 → 交主播脑回复）----------

    async def _on_progress_query(self, payload: Any):
        """主播脑转发的进度查询：思维脑生成进度报告 emit 回主播脑"""
        if not isinstance(payload, dict):
            return
        source = payload.get("source", "unknown")
        user = payload.get("user", "anonymous")
        report = self._build_progress_report()
        logger.info(f"[brain:{self.name}] 进度查询→报告 [{source}]: {report[:60]}")
        _log_event("INFO", "will_engine", f"生成进度报告: {report[:100]}")
        await self.emit(EVENTS.PROGRESS_REPORT, {
            "report": report, "source": source, "user": user,
        })

    # ---------- 规划 + 验收（思维脑同时负责） ----------

    # 需要先规划的多步任务（单工具调用不必规划）
    COMPLEX_TOOLS = {"cultist_task", "research_task", "code_task",
                     "tool_agent", "agent_task"}

    PLANNER_SYSTEM = """你是维尔薇的意志脑中的「规划器」。唯一职责：把一个目标拆成少量、可验收的子目标，交给执行方去做。

拆解原则：
1. 只拆到"可独立执行"的粒度：以 2~5 条为主；目标本身只有一件事时只给 1 条。
2. 每条只写"要达成什么"，不要写具体点击/调用步骤——那是执行方的工作。
3. acceptance 必须能被结果数据验证，优先写成可观察字段或状态：
   - 游戏类：出现结局 / 资金>0 / 某张卡进入某槽位 / 某动词完成
   - 研究类：读到 N 个来源 / 给出可引用结论
   - 编码类：测试通过 / 文件存在且内容正确
   - 通用：结果里出现明确成功证据
   禁止写"做好了""体验良好"这类不可验证的话。
4. 信息不足时，第一条子目标就是"查清 X"（探查/检索），不要凭空假设。
5. 允许带失败回退：acceptance 可以写成"若 A 不可行，则 B 成立"。
6. 不要重复台账里已经在做的事。

只输出 JSON 数组，不要解释、不要 markdown：
[{"subgoal":"...","acceptance":"...","suggested_tools":["能力/工具名"],"notes":"给操作脑的执行建议","risk":"可选：最可能失败的点"}]

示例（目标：玩密教模拟器并达成任意结局）：
[
 {"subgoal":"开局建立资金循环，避免因饥荒/疾病中断","acceptance":"资金>0 且 健康/理智未归零","suggested_tools":["cs_status","cs_move","cs_start"],"notes":"优先把资金卡放进 work 槽，保持 10 以上资金再推进主线","risk":"工作槽位卡不匹配"},
 {"subgoal":"推进主线直到出现结局界面","acceptance":"结果中出现 ending 字段或回到遗产界面","suggested_tools":["cs_move","cs_start","cs_conclude","cs_wait"],"notes":"Complete 立即 conclude；卡住时用 cs_wait 推进时间，连续失败先 research","risk":"步数用尽仍未结局"}
]"""

    VERIFIER_SYSTEM = """你是维尔薇的意志脑中的「验收员」。执行方会声称"完成了"，你的任务是只根据结果证据逐条核对子目标。

规则：
1. 证据只来自执行结果里出现的字段；没有证据 = 未达成，不替执行方脑补。
2. 每条子目标给一个状态：passed / failed / unverifiable。
3. 允许部分完成：只有 acceptance 明确满足时才给 passed。
4. 结论必须指出具体证据，或明确说明缺了什么证据。

只输出 JSON：
{"passed":true/false,
 "results":[{"subgoal":"...","status":"passed|failed|unverifiable","evidence":"引用的证据或缺失原因"}],
 "reason":"一句话总结"}"""

    async def _make_plan(self, goal: str, tool: str) -> List[Dict[str, str]]:
        """把目标拆成带验收条件的子目标（失败则退化为单条）。"""
        if os.getenv("WILL_PLAN_ENABLED", "1") in ("0", "false", "False"):
            return []
        llm = self._get_llm()
        if llm is None:
            return [{"subgoal": goal[:80], "acceptance": "执行结果明确成功"}]
        prompt = (f"工具：{tool}\n目标：{goal}\n\n"
                  "请拆解为子目标，并给每个子目标写可验证的完成条件。")
        ledger = self._ledger_block()
        shared = self._shared_space_block()
        if ledger:
            prompt += "\n\n【当前台账（避免重复立项）】\n" + ledger[:900]
        if shared:
            prompt += "\n\n【共享空间（背景，最多参考 800 字）】\n" + shared[:800]
        try:
            text = await asyncio.wait_for(
                llm.chat_simple(system_prompt=self.PLANNER_SYSTEM, user_text=prompt),
                timeout=15.0)
            match = re.search(r"\[.*\]", text or "", re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                plan = []
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    sub = str(row.get("subgoal") or "").strip()
                    acc = str(row.get("acceptance") or "").strip()
                    if sub:
                        item = {"subgoal": sub[:120],
                                "acceptance": acc[:120] or "结果明确成功"}
                        tools = row.get("suggested_tools") or []
                        if isinstance(tools, list) and tools:
                            item["suggested_tools"] = [
                                str(x)[:40] for x in tools[:6]]
                        notes = str(row.get("notes") or "").strip()
                        if notes:
                            item["notes"] = notes[:160]
                        risk = str(row.get("risk") or "").strip()
                        if risk:
                            item["risk"] = risk[:80]
                        plan.append(item)
                if plan:
                    return plan[:5]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 规划失败，退化为单条: {e}")
        return [{"subgoal": goal[:80], "acceptance": "执行结果明确成功"}]

    async def _prepare_task_plan(self, text: str, tool_name: str) -> tuple:
        """阶段一（规划器）：拆子目标 → 写台账 → 按需开能力域。

        只读台账/共享空间，不碰执行；返回 (plan, ci_task_id)。
        关键词路由和通用工具智能体都走这里，保证两阶段模式一致。
        """
        plan = await self._make_plan(text, tool_name)
        if not plan:
            return [], ""
        notes = "；".join(
            f"{p.get('subgoal')}（建议 "
            f"{'/'.join(p.get('suggested_tools') or []) or '按需'}）"
            for p in plan[:4])
        ci_task_id = ""
        try:
            from LLM_v2.domains.central_info.task_store import TaskStore
            rec = TaskStore().create(
                f"{tool_name}｜{text.strip()[:80]}",
                owner="will_engine", stage=tool_name,
                detail=notes[:400], phase="pending", plan=plan)
            ci_task_id = rec.get("task_id") or ""
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 计划写台账失败: {e}")
        # 计划里点名的能力域先打开；关闭交给闲置清理/项目结束
        targets = " ".join(
            " ".join(p.get("suggested_tools") or []) for p in plan)
        try:
            await self._get_capability_router().prepare(
                f"{text} {targets}"[:400], source="plan")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 计划开能力失败: {e}")
        _log_event(
            "INFO", "will_engine",
            f"规划 {len(plan)} 个子目标，已写台账 {ci_task_id or '(失败)'}："
            + " → ".join(p.get("subgoal", "")[:18] for p in plan))
        return plan, ci_task_id

    async def _verify_task(self, task: Any, result: Any) -> Dict[str, Any]:
        """验收：执行方说成功不算数，要对着子目标的验收条件核对。"""
        if not isinstance(result, dict):
            return result or {}
        plan = []
        if isinstance(task, dict):
            plan = (task.get("args") or {}).get("plan") or []
        if result.get("success") is False or not plan:
            return result

        evidence_parts = []
        for key in ("summary", "ending", "error", "steps"):
            if result.get(key):
                evidence_parts.append(f"{key}={result.get(key)}")
        if result.get("legacy_label"):
            evidence_parts.append(f"遗产={result['legacy_label']}")
        evidence = "｜".join(str(x) for x in evidence_parts)[:1500] or "（无结果说明）"

        checks = "\n".join(
            f"{i + 1}. 子目标：{p.get('subgoal')}｜验收条件：{p.get('acceptance')}"
            for i, p in enumerate(plan))
        llm = self._get_llm()
        if llm is None:
            # 没有模型时只做保守检查：目标要求结局就必须有结局
            goal = str((task.get("args") or {}).get("goal") or "")
            if any(k in goal for k in ("结局", "通关", "胜利")) and not result.get("ending"):
                return {**result, "success": False,
                        "error": "验收未通过：没有看到结局证据"}
            return result
        try:
            text = await asyncio.wait_for(
                llm.chat_simple(
                    system_prompt=self.VERIFIER_SYSTEM,
                    user_text=f"子目标：\n{checks}\n\n执行结果证据：\n{evidence}",
                ),
                timeout=20.0)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 验收调用失败（按原结果处理）: {e}")
            return result
        match = re.search(r"\{.*\}", text or "", re.DOTALL)
        if not match:
            return result
        try:
            verdict = json.loads(match.group(0))
        except Exception:  # noqa: BLE001
            return result
        if verdict.get("passed") is False:
            failed = verdict.get("failed") or []
            detail = verdict.get("results") or []
            if not failed and detail:
                failed = [str(r.get("subgoal")) for r in detail
                          if isinstance(r, dict) and r.get("status") != "passed"]
            reason = str(verdict.get("reason") or "子目标未达成")[:160]
            if failed:
                reason += "：" + "；".join(str(x)[:40] for x in failed[:3])
            logger.info(f"[brain:{self.name}] 验收未通过: {reason}")
            _log_event("WARN", "will_engine", f"验收未通过：{reason}")
            return {**result, "success": False,
                    "error": f"验收未通过：{reason}",
                    "verification": detail}
        return {**result, "verification": verdict.get("results") or []}

    def _ledger_block(self) -> str:
        """任务台账摘要：思维脑决策/进度汇报的实时依据。"""
        try:
            from LLM_v2.domains.central_info.task_store import TaskStore
            rows = TaskStore().list(limit=10)
        except Exception:  # noqa: BLE001
            return ""
        if not rows:
            return ""
        lines = ["【任务台账（实时，可随时查阅）】"]
        for t in rows:
            detail = str(t.get("detail") or "").replace("\n", " ")[:70]
            lines.append(
                f"- [{t.get('phase')}] {t.get('title')} · "
                f"{t.get('percent')}% · {t.get('stage') or '-'}"
                + (f" · {detail}" if detail else ""))
        return "\n".join(lines)

    def _build_progress_report(self) -> str:
        """从任务监控表生成面向观众的自然语言进度报告。

        - 有运行中任务 → 报告任务 + 当前进度%
        - 无运行中任务但有最近记录 → 报告最近完成/失败
        - 完全空闲 → 提示当前无事
        """
        import time as _t
        # 优先读台账：它是所有任务的权威进度（含排队中的）
        try:
            from LLM_v2.domains.central_info.task_store import TaskStore
            ledger_rows = TaskStore().list(limit=6)
        except Exception:  # noqa: BLE001
            ledger_rows = []
        if ledger_rows:
            lines = []
            for t in ledger_rows:
                line = f"· {t.get('title')}：{t.get('percent')}%"
                detail = str(t.get("detail") or "").replace("\n", " ")[:40]
                if detail:
                    line += f"（{detail}）"
                lines.append(line)
            return "当前我在处理这些事～" + "，".join(lines) + "！"

        running = {k: v for k, v in self._task_monitor.items()
                   if v.get("status") == "running"}
        if running:
            lines = ["当前我在处理这些事～"]
            for task_id, e in running.items():
                desc = e.get("description", "任务")
                pct = e.get("percent", 0)
                stage = e.get("stage", "")
                detail = e.get("detail", "")
                line = f"· {desc}：{pct}%"
                if detail:
                    line += f"（{detail}）"
                lines.append(line)
            return "，".join(lines) + "！"

        # 无运行中任务：查最近进度记录
        recent = [p for p in self._task_progress_history
                  if p.get("stage") in ("done", "failed")]
        if recent:
            last = recent[-1]
            pct = last.get("percent", 100)
            detail = last.get("detail", "")
            state = "完成了" if last.get("stage") == "done" else "失败了"
            base = f"最近的任务{state}（{pct}%）"
            if detail:
                base += f"：{detail}"
            return base + "。现在没有进行中的任务啦～"
        return "现在没有进行中的任务哦，需要我帮你做点什么吗？"

    def _get_capability_router(self):
        """懒加载能力按需启停器（思维脑的能力路由）。"""
        router = getattr(self, "_capability_router", None)
        if router is None:
            from LLM_v2.brains.capability_router import CapabilityRouter
            router = CapabilityRouter(self.runtime, llm=self._get_llm())
            self._capability_router = router
        return router

    async def _sync_capabilities(self, text: str, source: str) -> None:
        """任务开始/结束时，按需启停能力域（运行时作用域，不改持久配置）。"""
        try:
            router = self._get_capability_router()
            idle = await router.maybe_finish_idle()
            if idle and idle.get("disabled"):
                _log_event(
                    "INFO", "capability",
                    f"项目闲置结束，关闭临时能力: {idle.get('disabled')}")
            if router.detect_end(text):
                report = await router.finish("user_end")
                if report.get("disabled") or report.get("kept"):
                    _log_event(
                        "INFO", "capability",
                        f"项目结束：关闭 {report.get('disabled')}；"
                        f"保留 {report.get('kept')}；"
                        f"{report.get('decision_source')}：{report.get('llm_reason') or ''}")
                return
            if self._looks_actionable(text):
                report = await router.prepare(text, source)
                if report.get("enabled"):
                    _log_event(
                        "INFO", "capability",
                        f"按需开启能力: {report.get('enabled')} "
                        f"（{report.get('source')}：{report.get('reason') or ''}）")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 能力路由失败（不阻塞任务）: {e}")

    async def _on_user_intent(self, payload: Any):
        """H-2 Phase 6: USER_MESSAGE → 意图分类 → emit WILL_ENGINE_ACTION(execute_tool)

        WillEngine 决策"用户想让 AI 做什么"，按关键词直接路由（避免调 LLM 超时）。
        关键词命中 → emit WILL_ENGINE_ACTION(execute_tool: tool_name, args) 让 Operator 执行。
        """
        if not isinstance(payload, dict):
            return
        text = payload.get("text", "")
        if not text:
            return
        source = payload.get("source", "unknown")
        user = payload.get("user", "")
        metadata = payload.get("metadata") or {}
        chat_key = str(metadata.get("chat_key") or "")

        _log_event("DEBUG", "will_engine", f"收到 USER_MESSAGE: {repr(text)[:80]}")

        # 能力按需启停：任务开始前临时开启需要的域；项目结束/闲置时关闭
        await self._sync_capabilities(text, source)

        # H-2 Phase 6: 按关键词直接路由（不调 LLM，避免 character model 不擅 function calling 的问题）
        action_keywords = ["打开", "关闭", "启动", "执行", "查", "截屏", "写文件", "读文件",
                          "列出", "搜索", "运行", "调用", "调",
                          # 联网研究 / 自主学习
                          "查资料", "搜资料", "搜一下", "搜索一下", "查一查", "搜一搜",
                          "调研", "了解一下", "学习一下", "网上查", "搜集资料",
                          # 编码任务（走操作脑 CodingLoop）
                          "编写", "写代码", "写一个", "写个", "实现", "开发",
                          "重构", "修复", "调试", "改代码",
                          # B 站视频域（了解信息）
                          "看看", "看一下", "读一下", "读评论", "评论", "首页", "推荐",
                          "投稿", "字幕", "UP主", "up主", "BV",
                          # B 站视频域（对网站进行操作）
                          "点赞", "投币", "收藏", "收藏夹",
                          # 直播互动助手
                          "标记", "高光", "点歌", "签到", "歌单",
                          # 直播制作：OBS / 唱歌 / 转写 / 切片
                          "OBS", "obs", "录制", "唱歌", "唱一首", "唱首歌",
                          "转写", "听写", "切片", "录像", "语音识别",
                          # 密教模拟器
                          "密教", "密教模拟器", "玩一局", "玩会儿",
                          # QQ 形象 / QQ 空间
                          "说说", "动态", "QQ空间", "qq空间",
                          "头像", "昵称", "个性签名", "签名",
                          # 自进化
                          "自进化", "进化", "新工具", "新增工具", "加个工具",
                          "扩展能力",
                          # 中央信息
                          "中央信息", "全局情况", "全局状态", "能力域状态"]
        matched = [kw for kw in action_keywords if kw in text]

        # 根据关键词路由到具体工具（轻量规则，不需要 LLM API）
        tool_name, args = ("", {})
        if matched:
            tool_name, args = self._route_by_keywords(text, source)

        # 规则没命中/无法路由 → 交给通用工具智能体自主判断与规划
        if not tool_name:
            if self._looks_actionable(text):
                await self._dispatch_agent(text, source, user, chat_key)
            else:
                _log_event(
                    "DEBUG", "will_engine",
                    f"无需工具: 关键词 {matched or '无'} / '{text[:40]}'")
            return
        if tool_name in ("sa_checkin", "sa_queue_add"):
            args.setdefault("user", user or source)

        # 阶段一（规划）：只读台账/共享空间 → 拆子目标 → 写台账 → 按需开能力域
        ci_task_id = ""
        if tool_name in self.COMPLEX_TOOLS and not args.get("plan"):
            plan, ci_task_id = await self._prepare_task_plan(
                text.strip(), tool_name)
            if plan:
                args["plan"] = plan

        _log_event("INFO", "will_engine", f"调度 {tool_name}({args}) ← '{text[:40]}'")
        action = {
            "type": "execute_tool",
            "tool": tool_name,
            "args": args,
            "source": f"will_engine_{source}",
            "reasoning": f"keyword {matched}",
            "user": user,
            "chat_key": chat_key,
            "ci_task_id": ci_task_id or None,
        }
        soul = self._get_soul()
        if soul is not None:
            decision_id = soul.record_decision(
                action, trigger="user_message", mode="rule", confidence=0.9)
            if decision_id:
                action["decision_id"] = decision_id
        await self.emit(EVENTS.WILL_ENGINE_ACTION, action)

    # 通用工具智能体：动作意图词（没有命中固定规则时，交给 LLM 规划）
    _AGENT_VERBS = (
        "帮", "给我", "替我", "把", "找", "搜", "下载", "换", "改", "设置",
        "发", "装点", "弄", "搞", "做", "生成", "写", "打开", "查", "看看",
        "当", "用", "安装", "删除", "上传", "发布",
        "加", "新增", "学", "进化", "扩展", "剪", "配",
    )

    @classmethod
    def _looks_actionable(cls, text: str) -> bool:
        """粗筛：像不像"请帮我做事"，避免把每句闲聊都送进规划器。"""
        t = (text or "").strip()
        if len(t) < 3:
            return False
        return any(v in t for v in cls._AGENT_VERBS)

    async def _dispatch_agent(self, text: str, source: str, user: str,
                              chat_key: str) -> None:
        """交给 ToolAgent 自主选域、规划、执行、校验。"""
        goal = text.strip()
        # 阶段一：通用智能体同样先拆计划、写台账、按需开能力域
        plan, ci_task_id = await self._prepare_task_plan(goal, "tool_agent")
        context_parts = [f"来源={source}；用户={user or 'unknown'}"]
        try:
            from LLM_v2.core.central_context import get_central
            recent = get_central().recent_chat(8)
            lines = []
            for chat in recent:
                body = str(chat.get("text") or "").strip().replace("\n", " ")
                if body:
                    lines.append(f"{chat.get('role')}: {body[:80]}")
            if lines:
                context_parts.append("最近对话：\n" + "\n".join(lines))
        except Exception:  # noqa: BLE001
            pass
        action = {
            "type": "execute_tool",
            "tool": "tool_agent",
            "args": {
                "goal": goal[:500],
                "context": "\n".join(context_parts)[:1200],
            },
            "source": f"will_engine_{source}",
            "reasoning": "autonomous tool planning",
            "user": user,
            "chat_key": chat_key,
            "ci_task_id": ci_task_id or None,
        }
        if plan:
            action["args"]["plan"] = plan
        soul = self._get_soul()
        if soul is not None:
            decision_id = soul.record_decision(
                action, trigger="user_message", mode="agent", confidence=0.6)
            if decision_id:
                action["decision_id"] = decision_id
        _log_event("INFO", "will_engine", f"自主工具规划 ← '{goal[:50]}'")
        await self.emit(EVENTS.WILL_ENGINE_ACTION, action)

    @staticmethod
    def _extract_research_topic(text: str) -> str:
        """从"帮我查一下 XXX"里抽出研究主题 XXX。"""
        import re as _re
        topic = _re.sub(
            r"^(?:帮我|请|麻烦你?|你)?"
            r"(?:查资料|查一下|查一查|查查|搜资料|搜一下|搜索一下|搜一搜|搜索|"
            r"调研一下|调研|了解一下|学习一下|网上查|搜集资料|帮我查|帮我搜)?"
            r"(?:一下|一遍|下)?"
            r"[：:\s]*",
            "",
            (text or "").strip(),
        )
        # 去掉"整理成资料/然后告诉我/并给个示例"这类任务说明，只留研究主题
        topic = _re.split(
            r"[，,。；;]\s*(?:整理|总结|归纳|然后|并|再|告诉|给我|输出|写成|做成)",
            topic, maxsplit=1,
        )[0]
        topic = topic.replace("里的", " ").replace("中的", " ").strip()
        topic = " ".join(topic.split())
        return topic.strip("，。！？? ~") or (text or "").strip()

    def _route_by_keywords(self, text: str, source: str) -> tuple:
        """H-2 Phase 6: 轻量规则路由（关键词 → 工具 + 参数）

        常见电脑操作（打开盘符/我的电脑/文件夹/URL/应用）优先走
        os_open_path（系统级安全打开，最稳定）；应用名走 os_launch_app。
        """
        # ---------- 中央信息（全局摘要 + 各域指针）----------
        if any(k in text for k in ("中央信息", "全局情况", "全局状态", "能力域状态")):
            return "ci_overview", {"chat_limit": 6, "domain_limit": 32}

        # ---------- QQ 形象 / QQ 空间（只写自己的资料；说说固定仅自己可见）----------
        if any(k in text for k in ("QQ空间", "qq空间", "说说", "发动态", "发条动态")):
            # 只读语义（查看/列列表）不触发发布
            if any(k in text for k in ("看看", "查看", "列表", "最新", "有几条", "有什么")):
                return "", {}
            if any(k in text for k in ("发", "发布", "写")):
                m = re.search(
                    r"(?:发条?说说|发条?动态|发个说说|发到QQ空间|QQ空间发|空间发|发布)"
                    r"[：:\s]*[「“\"]?([\s\S]+)", text)
                content = (m.group(1).strip().rstrip("。！!？? 」”\"")
                           if m else "")
                if content:
                    return "qq_zone_post", {"content": content}
            return "", {}

        if "头像" in text and any(k in text for k in ("换", "改", "设置", "换成", "用")):
            if _DEFAULT_AVATAR.is_file():
                return "qq_avatar_set", {"file": str(_DEFAULT_AVATAR)}
            return "", {}

        if (any(k in text for k in ("昵称", "个性签名", "签名"))
                and any(k in text for k in ("改", "换", "设置"))):
            m = re.search(
                r"(?:昵称|个性签名|签名)\s*(?:改成|改为|换成|设置成|设置|改|换)"
                r"\s*[：:\s「“\"]*([^」”\"\n]+)", text)
            value = (m.group(1).strip().rstrip("。！!？? 」”\"")
                     if m else "")
            if value:
                if "昵称" in text:
                    return "qq_profile_set", {"nickname": value}
                return "qq_profile_set", {"personal_note": value}
            return "", {}

        # ---------- 自进化（优先于编码任务，避免"给自己加个工具"被当成写代码）----------
        if any(k in text for k in ("自进化", "进化出", "新工具", "新增工具",
                                   "加个工具", "给自己加", "扩展能力",
                                   "学会一个可复用")):
            return "tool_agent", {"goal": text.strip()[:500]}

        # ---------- 编码任务（优先于普通文件操作）----------
        # 判据：有"编码动作词" + 有"编程对象词"，避免把"写个便签"误判成写代码
        _coding_actions = ("编写", "写代码", "写一个", "写个", "实现", "开发",
                           "重构", "修复", "修正", "改正", "调试", "改代码")
        _coding_objects = ("代码", "程序", "脚本", "函数", "类", "模型", "网络",
                           "算法", "接口", "爬虫", "测试", "模块", "工具",
                           "能力域", "插件", "cnn", "rnn", "lstm", "bug",
                           "demo", "示例", "原型")
        _text_low = text.lower()
        if (any(k in text for k in _coding_actions)
                and any(k in _text_low for k in _coding_objects)):
            return "code_task", {"goal": text, "workspace": None, "acceptance": ""}

        # ---------- 密教模拟器（必须排在 launcher 之前，避免"模拟器"被当成 MuMu）----------
        if "密教" in text or "cultist" in text.lower():
            # 只问"现在什么状态/到哪一步"→ 只读牌桌，不启动游玩
            if any(k in text for k in ("状态", "局面", "牌桌", "到哪一步", "现在怎样", "什么情况")):
                return "cs_status", {}
            # 问"学到了什么/有什么攻略"→ 查共享知识缓存，而不是继续游玩
            if any(k in text for k in ("学到", "经验", "攻略", "知识", "记得", "学会了")):
                import re as _re_kn
                query = _re_kn.sub(
                    r"(你|你的|密教模拟器|密教|cultist|到底|都|在|里|里面|关于|哪些|"
                    r"有什么|学到了?|学会了?|什么|经验|攻略|知识|记得|查一下|看看|一下|的)",
                    " ", text, flags=_re_kn.I)
                query = _re_kn.sub(r"[^\w\u4e00-\u9fff]+", " ", query).strip()
                return "cs_knowledge", {"query": query, "limit": 8}
            import re as _re_cs
            m = _re_cs.search(r"(\d+)\s*步", text)
            if m:
                steps = max(1, min(60, int(m.group(1))))
            elif any(k in text for k in ("结局", "通关", "胜利", "赢一局", "玩到")):
                # 要求达成结局时给足步数，否则连开局流程都跑不完
                steps = 40
            else:
                steps = 8
            return "cultist_task", {"goal": text.strip()[:200], "max_steps": steps}

        # ---------- launcher 域（MuMu/ALAS/MAA 启动器）优先于 os_* ----------
        _lower = text.lower()
        _targets = []
        if "mumu" in _lower or "模拟器" in text:
            _targets.append("mumu")
        if "alas" in _lower or "碧蓝" in text:
            _targets.append("alas")
        if "maa" in _lower or "明日方舟" in text:
            _targets.append("maa")
        if _targets:
            if any(k in text for k in ("停止", "关闭", "关掉", "结束", "停一下")):
                return "launcher_stop", {}
            if any(k in text for k in ("状态", "在吗", "在不在", "怎么样了")):
                return "launcher_status", {}
            if len(_targets) >= 2 or any(k in text for k in ("全部", "所有", "一键", "都")):
                return "launcher_start_all", {}
            return f"launcher_{_targets[0]}", {}

        # ---------- B 站视频域（只读：空间/首页/视频/字幕/评论）----------
        import re as _re2
        _bv = _re2.search(r"(?i)(BV[0-9A-Za-z]{10})", text)
        _av = _re2.search(r"(?i)av(\d{1,12})", text)
        _uid = _re2.search(r"(?i)(?:space\.bilibili\.com/|uid[:：=]\s*)(\d{4,12})", text)
        if _uid is None and any(k in text for k in ("UP主", "up主", "空间", "粉丝")):
            _uid = _re2.search(r"(\d{5,12})", text)
        _feed_kw = ("首页", "推荐")
        _space_kw = ("投稿", "UP主", "up主", "空间", "粉丝")
        _write_kw = ("点赞", "投币", "收藏", "收藏夹")
        if _bv or _av or _uid or any(k in text for k in _feed_kw + _space_kw
                                      + ("字幕", "评论区", "读评论", "评论") + _write_kw):
            bvid = _bv.group(1) if _bv else ""
            aid = int(_av.group(1)) if _av else 0
            uid = int(_uid.group(1)) if _uid else 0
            vargs = {}
            if bvid:
                vargs["bvid"] = bvid
            if aid:
                vargs["aid"] = aid
            # 对网站进行操作（写）：只在用户明确要求时；先排除"查询状态/列收藏夹"这类只读语义
            if any(k in text for k in _write_kw):
                # 只读语义优先：查询状态
                if any(k in text for k in ("状态", "是否", "有没有", "查一下", "还在吗")):
                    return "bv_action_status", dict(vargs)
                # 「收藏夹」仅在没有"放入目标"语意时按"列出收藏夹"处理
                if "收藏夹" in text and not any(
                        k in text for k in ("到", "进", "加入", "添加", "放进", "存入")):
                    return "bv_fav_folders", ({"uid": uid} if uid else {})
                if "点赞" in text or "赞一下" in text:
                    return "bv_like", {**vargs, "like": "取消" not in text}
                if "投币" in text:
                    return "bv_coin", dict(vargs)
                if "收藏" in text:
                    return "bv_fav", {**vargs, "remove": "取消" in text}
            if bvid or aid:
                if any(k in text for k in ("评论", "留言")):
                    return "bv_video_comments", dict(vargs)
                if "字幕" in text:
                    return "bv_video_subtitle", dict(vargs)
                return "bv_video_info", dict(vargs)
            if any(k in text for k in _feed_kw):
                return "bv_home_feed", {}
            if "投稿" in text:
                return "bv_space_videos", ({"uid": uid} if uid else {})
            if uid:
                return "bv_space_info", {"uid": uid}

        # ---------- 联网研究 / 自主学习（放在 B 站之后，避免抢 BV/点赞语义）----------
        if any(k in text for k in ("查过什么", "研究笔记", "资料笔记", "之前查过")):
            return "wr_notes", {"query": "", "limit": 5}

        _research_triggers = (
            "查资料", "查一下", "查一查", "查查", "搜资料", "搜一下",
            "搜索一下", "搜一搜", "搜索", "调研", "了解一下", "学习一下",
            "网上查", "搜集资料", "帮我查", "帮我搜",
        )
        if (any(k in text for k in _research_triggers)
                and "打开" not in text
                and not any(k in text for k in ("B站", "bilibili", "哔哩哔哩"))
                and not any(k in text for k in ("几点", "现在时间", "今天几号", "日期"))):
            topic = self._extract_research_topic(text)
            return "research_task", {
                "goal": topic,
                "max_sources": 2,
                "request": text,
            }

        # ---------- 打开类操作 ----------
        if "打开" in text or "开一下" in text or "开启" in text:
            # 提取动作词之后的目标（"打开D盘"→"D盘"；"开一下浏览器"→"浏览器"）
            import re as _re
            m = _re.search(r"(?:打开|开一下|开启)\s*(.+)", text)
            after = (m.group(1).strip() if m and m.group(1).strip() else "").rstrip("。！!？? ")
            after_lower = after.lower()

            # 1) 我的电脑 / 此电脑
            if any(k in after for k in ("我的电脑", "此电脑", "我的计算机")):
                return "os_open_path", {"path": "我的电脑"}

            # 2) 盘符：D盘 / D:\ / D: / 打开D  → os_open_path("D:")
            m = _re.search(r"([A-Za-z])\s*盘", after)
            if m:
                return "os_open_path", {"path": f"{m.group(1).upper()}:"}
            if _re.match(r"^[A-Za-z]:[\\\\/]?$", after) or _re.match(r"^[A-Za-z]$", after):
                return "os_open_path", {"path": f"{after[0].upper()}:"}

            # 3) 特殊文件夹：下载/文档/桌面/回收站/控制面板
            special = ["下载", "文档", "桌面", "回收站", "控制面板", "我的文档",
                       "此电脑", "图片", "音乐", "视频"]
            for s in special:
                if s in after:
                    return "os_open_path", {"path": s}

            # 4) 常见应用名（按用户机器可能安装的）→ os_launch_app
            app_map = [
                ("google chrome", "chrome", "谷歌浏览器", "chrome"),
                ("microsoft edge", "msedge", "edge", "msedge"),
                ("浏览器", "browser", "浏览器"),
                ("firefox", "firefox"),
                ("code", "vscode", "vs code", "code"),
                ("notepad", "记事本", "notepad"),
                ("explorer", "文件资源管理器", "explorer"),
                ("计算器", "calculator", "calc"),
                ("cmd", "命令提示符", "cmd"),
            ]
            for entry in app_map:
                for kw in entry[:-1]:
                    if kw in after_lower or kw in after:
                        return "os_launch_app", {"app": entry[-1]}

            # 5) URL
            if after_lower.startswith(("http://", "https://", "www.")):
                return "os_open_path", {"path": after}

            # 6) 其它（文件夹/文件/未知）→ 交给 os_open_path 尝试系统级打开
            if after:
                return "os_open_path", {"path": after}

            # 空目标：默认打开我的电脑
            return "os_open_path", {"path": "我的电脑"}

        # ---------- 直播互动助手（stream_assist）----------
        if any(k in text for k in ("标记亮点", "标记一下", "高光时刻", "高光标记")):
            return "sa_mark_highlight", {"note": text[:200]}
        if "高光列表" in text or "直播亮点" in text:
            return "sa_list_highlights", {"limit": 10}
        if "点歌" in text:
            import re as _re3
            m = _re3.search(r"点歌[：: ]*(.+)", text)
            song = m.group(1).strip() if m else ""
            if song:
                return "sa_queue_add", {"song": song[:80]}
            return "sa_queue_list", {}
        if any(k in text for k in ("歌单", "点歌列表", "点歌队列")):
            return "sa_queue_list", {"limit": 20}
        if "签到" in text:
            return "sa_checkin", {}

        # ---------- 直播制作：OBS / 唱歌 / 语音转写 / 切片 ----------
        low = text.lower()
        if "obs" in low:
            import re as _re4
            if "切换" in text:
                m = _re4.search(r"切换(?:到|场景)?[：: ]*(.+)", text)
                scene = m.group(1).strip().rstrip("。！!？? ") if m else ""
                if scene:
                    return "obs_switch_scene", {"scene": scene[:60]}
            if "停止" in text and "录制" in text:
                return "obs_stop_record", {}
            if "录制" in text:
                return "obs_start_record", {}
            if "场景" in text:
                return "obs_scenes", {}
            return "obs_status", {}
        if any(k in text for k in ("唱一首", "唱首歌", "唱歌")):
            import re as _re5
            m = _re5.search(r"(?:唱一首|唱首歌|唱歌)[：: ]*(.+)", text)
            lyric = m.group(1).strip() if m else ""
            if lyric:
                return "sing_render", {"text": lyric[:120]}
            return "sing_health", {}
        if any(k in text for k in ("语音转写", "转写", "听写", "语音识别")):
            import re as _re6
            m = _re6.search(
                r"([A-Za-z]:\\[^\s，。；;]+|/[^\s，。；;]+)"
                r"\.(?:wav|mp3|mp4|mkv|flac|m4a|aac)", text, _re6.I)
            if m:
                return "stt_transcribe", {"path": m.group(0)}
            return "stt_health", {}
        if any(k in text for k in ("切片", "录像", "高光剪辑")):
            return "clip_list", {"limit": 10}

        # ---------- 截屏 / 看桌面 ----------
        if "截屏" in text or "截图" in text:
            return "os_capture_window", {"window_title": ""}
        if "列出窗口" in text or "看桌面" in text or "现在屏幕" in text:
            return "os_list_windows", {}

        # ---------- 文件操作 ----------
        if "写文件" in text or "创建文件" in text:
            return "os_create_file", {"path": "~/test.txt", "content": ""}
        if "读文件" in text:
            return "os_read_file", {"path": "~/test.txt"}
        if "列目录" in text or "文件夹里有什么" in text or "目录内容" in text:
            return "os_list_directory", {"path": "D:\\"}
        return None, None

    async def _classify_intent_with_tools(self, text: str) -> Optional[dict]:
        """H-2 Phase 6: 调 LLM API（带 tools）分类用户意图"""
        if not self.llm:
            self.llm = self._get_llm()
        if not self.llm or not self.llm.api_key:
            return None
        try:
            from LLM_v2.core.persona_runtime import get_runtime as _get_rt
            runtime = _get_rt()
            tools = runtime.assembly.active_tool_schemas()
        except Exception:
            return None

        system_prompt = """你是意图分类器。用户消息是任务指令，输出 JSON（仅一个工具调用）：
{"tool": "工具名", "args": {"参数": "值"}}

只输出 JSON，不要解释。如果消息只是闲聊不需调工具，输出 {"tool": null}。"""
        try:
            result = await asyncio.wait_for(
                self.llm.chat(system_prompt=system_prompt, user_text=text, tools=tools),
                timeout=10.0,
            )
            if result.get("error"):
                return None
            tool_calls = result.get("tool_calls") or []
            if tool_calls:
                tc = tool_calls[0]
                return {
                    "tool": tc.get("name") or tc.get("function"),
                    "args": tc.get("arguments") or tc.get("args") or {},
                }
        except asyncio.TimeoutError:
            logger.debug(f"[brain:{self.name}] 意图分类 LLM 超时")
        except Exception as e:
            logger.debug(f"[brain:{self.name}] 意图分类异常: {e}")
        return None

    async def _on_activity(self, payload: Any):
        """任何活动都重置 idle 计时"""
        self.last_activity_at = datetime.now()

    async def heartbeat(self):
        """每 30 秒检查一次——该不该自主决策

        仅当 self.autonomous=True 时才进行任何自主行为
        （空闲决策 / 强制休息请求等）；
        autonomous=False 时 heartbeat 完全静默：不 emit 任何事件、不调 LLM。
        任务监控 / 进度报告 / 意图分类等被动响应不受此开关影响。
        """
        # 台账巡检：有任务在跑就要看，与是否自主决策无关
        try:
            await self._ledger_patrol()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 台账巡检异常: {e}")

        now = datetime.now()
        idle_sec = (now - self.last_activity_at).total_seconds()

        if idle_sec < self.IDLE_THRESHOLD:
            return

        # 能力作用域闲置清理：即使不开自主决策也要做，
        # 避免项目临时开启的能力一直挂在后台
        try:
            idle_cap = await self._get_capability_router().maybe_finish_idle()
            if idle_cap and idle_cap.get("disabled"):
                _log_event(
                    "INFO", "capability",
                    f"项目闲置结束，关闭临时能力: {idle_cap.get('disabled')}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 能力闲置清理失败: {e}")

        # 自主决策开关关闭：不自行产生任何想法/行为（含强制休息请求）
        if not self.autonomous:
            return

        soul = self._get_soul()

        # 安静时段（跨午夜）：不自主决策、不主动说话
        if soul is not None and soul.is_quiet(now):
            logger.debug(f"[brain:{self.name}] quiet hours，跳过自主决策")
            return

        # 作息仪式优先（±5 分钟窗口，同一天只触发一次）
        if soul is not None:
            due = soul.routine_due(now)
            if due and due["key"] not in self._routine_fired:
                self._routine_fired.add(due["key"])
                self._routine_fired = set(list(self._routine_fired)[-20:])
                await self._run_routine(due, soul)
                return

        state = self.runtime.state
        persona = self.runtime.persona

        # 强制休息检查（仅在自主模式开启时生效；关闭时 AI 不自行请求休息）
        if state.should_force_rest():
            logger.info(
                f"[brain:{self.name}] energy too low, requesting rest "
                f"(E={state.energy}, mood_P={state.pad.P:.2f})"
            )
            await self.emit(EVENTS.REST_REQUESTED, {
                "reason": "energy_exhausted",
                "energy": state.energy,
                "mood_P": state.pad.P,
            })
            return

        # 根据决策模式决定
        action = None
        if self.decision_mode == "rule":
            action = self._decide_rule(state, persona)
        elif self.decision_mode == "api":
            action = await self._decide_api(state, persona)
        else:  # hybrid
            action = self._decide_rule(state, persona)
            if action is None:
                logger.debug(
                    f"[brain:{self.name}] 规则无匹配，回退到 API"
                )
                action = await self._decide_api(state, persona)

        if action:
            self._decision_count += 1
            # 决策落库（灵魂层不可用时跳过，不影响执行）
            decision_id = 0
            soul = self._get_soul()
            if soul is not None:
                decision_id = soul.record_decision(
                    action, trigger="idle", mode=self.decision_mode,
                    confidence=0.7 if self.decision_mode != "api" else 0.6)
                if decision_id:
                    action["decision_id"] = decision_id
            logger.info(
                f"[brain:{self.name}] 决策 #{self._decision_count}: "
                f"{action['type']} (mode={self.decision_mode}, "
                f"idle={idle_sec:.0f}s, E={state.energy})"
            )
            await self.emit(EVENTS.WILL_ENGINE_DECIDED, action)
            # 自主执行：把安全动作交给操作脑（有冷却与去重，见 _maybe_execute）
            await self._maybe_execute(action, decision_id)
            self.apply_event(
                "will_engine_decided",
                delta_energy=-2, delta_focus=5,
            )

    # ---------- 自主执行 / 作息 ----------

    # 只自动执行这些动作；social_action / rest / diary 不产生工具调用
    EXECUTABLE_ACTIONS = {
        "watch_video", "browse_bilibili", "explore_interest",
        "code_action", "list_files",
    }
    ACTION_COOLDOWN_SEC = 900      # 两次自主执行至少间隔 15 分钟
    ACTION_DEDUPE_SEC = 3600       # 同一动作 1 小时内不重复
    ROUTINE_LINES = {
        "greet": "早上好呀，今天也一起慢慢来。",
        "rest_check": "坐久了就起来动一动，别一直硬撑。",
        "diary": "今天也谢谢你陪我，晚点我做个简单复盘。",
    }

    async def _maybe_execute(self, action: dict, decision_id: int = 0) -> None:
        """把自主决策交给操作脑执行（受开关、冷却、去重保护）"""
        soul = self._get_soul()
        if soul is None or not soul.autonomous_execute():
            return
        action_type = action.get("type")
        if action_type not in self.EXECUTABLE_ACTIONS:
            return
        now = time.time()
        if now - self._last_action_exec_at < self.ACTION_COOLDOWN_SEC:
            logger.debug(f"[brain:{self.name}] 自主执行冷却中，跳过 {action_type}")
            return
        key = f"{action_type}:{action.get('target') or ''}"
        last = self._executed_actions.get(key)
        if last and now - last < self.ACTION_DEDUPE_SEC:
            logger.debug(f"[brain:{self.name}] 自主动作重复，跳过 {key}")
            return

        self._last_action_exec_at = now
        self._executed_actions[key] = now
        self._executed_actions = {k: v for k, v in self._executed_actions.items()
                                  if now - v < 86400}
        # 自主动作也按需求临时开启能力（如 code_action → coder，watch_video → bili_video）
        try:
            await self._get_capability_router().prepare(
                str(action.get("target") or action.get("reasoning") or action_type),
                source="autonomous")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 自主动作能力路由失败: {e}")
        payload = dict(action)
        payload["source"] = "will_engine_autonomous"
        if decision_id:
            payload["decision_id"] = decision_id
        logger.info(f"[brain:{self.name}] 自主执行: {action_type} → {action.get('target')}")
        _log_event("INFO", "will_engine",
                   f"自主执行：{action_type} → {action.get('target') or ''}")
        await self.emit(EVENTS.WILL_ENGINE_ACTION, payload)

    async def _run_routine(self, due: dict, soul) -> None:
        """作息仪式：记录决策 → 必要时请求休息 → 让主播脑说一句"""
        entry = due.get("entry") or {}
        action = entry.get("action") or "routine"
        prompt = entry.get("prompt") or ""
        text = entry.get("text") or self.ROUTINE_LINES.get(action) or prompt or "……"
        state = self.runtime.state
        decision_id = soul.record_decision(
            {"type": f"routine_{action}", "target": action,
             "reasoning": prompt or action},
            trigger="routine", mode="rule", confidence=0.9)
        if action == "rest_check" and state.energy < 40:
            await self.emit(EVENTS.REST_REQUESTED, {
                "reason": "routine_rest", "energy": state.energy,
                "mood_P": state.pad.P,
            })
        await self.emit(EVENTS.PROGRESS_REPORT, {
            "report": text,
            "source": "will_engine_routine",
            "user": "audience",
        })
        logger.info(f"[brain:{self.name}] 作息触发 {action}（{prompt}）")
        _log_event("INFO", "will_engine",
                   f"作息触发：{action}｜{prompt}｜decision={decision_id}")

    # ---------- L1: 规则决策 ----------

    def _decide_rule(self, state, persona) -> Optional[dict]:
        """纯规则决策（v1 逻辑 + 新增）

        返回 None 时调用方回退到 API
        """
        # 规则 1：社交需求高 → 想找人聊天（优先找内心更亲近的常驻观众）
        if state.wants_social() and not self._is_discouraged("social_action"):
            target = "audience"
            reason = f"social_need={state.social_need} > 70"
            soul = self._get_soul()
            if soul is not None:
                top = soul.top_bonds(1)
                if top:
                    target = top[0]["user_id"]
                    reason += (f"，想找 {target}"
                               f"（亲近 {top[0]['attachment']:.2f}）")
            return {
                "type": "social_action",
                "target": target,
                "params": {"mood": state.pad.dominant_quadrant()},
                "reasoning": reason,
            }

        # 规则 2：好奇心高 + 社交需求低 → 想探索
        if (state.wants_exploration()
                and not self._is_discouraged("explore_interest")):
            if persona.interests:
                # 随机选（v1 总是选第一个，这里加随机）
                import random
                interest = random.choice(persona.interests)
                return {
                    "type": "explore_interest",
                    "target": interest.name,
                    "params": {"subtopics": interest.subtopics[:2]},
                    "reasoning": (
                        f"curiosity={state.curiosity} > 60, "
                        f"random pick={interest.name}"
                    ),
                }

        # 规则 3：能量充沛 + 专注低 → 想写代码
        if (state.energy > 60 and state.focus < 50
                and not self._is_discouraged("code_action")):
            return {
                "type": "code_action",
                "target": "side_project",
                "params": {"duration_min": 30},
                "reasoning": (
                    f"energy={state.energy} high, focus={state.focus} low"
                ),
            }

        # 规则 4：心情好 → 看视频
        if (state.pad.P > 0.5 and state.pad.A < 0
                and not self._is_discouraged("watch_video")):
            return {
                "type": "watch_video",
                "target": "bilibili_recommended",
                "params": {"category": "tech"},
                "reasoning": f"心情好且平静, P={state.pad.P:.2f}",
            }

        # 规则 5：低能量 → 休息
        if state.energy < 30 and not self._is_discouraged("rest"):
            return {
                "type": "rest",
                "target": "self",
                "params": {"reason": "low_energy"},
                "reasoning": f"energy={state.energy} < 30",
            }

        # 无规则匹配
        return None

    # ---------- L2: API 决策 ----------

    async def _decide_api(self, state, persona) -> Optional[dict]:
        """API 决策：调 LLM"""
        llm = self._get_llm()
        if llm is None or not llm.api_key:
            logger.warning("[brain:will_engine] 无 LLM，跳过 API 决策")
            return None

        try:
            # 8 段决策上下文：人格 → 状态 → 记忆 → 经验 → 知识 → 任务 → 格式
            soul = self._get_soul()
            if soul is not None:
                system_prompt = soul.build_decision_context()
            else:
                system_prompt = self.DECISION_SYSTEM.format(
                    name=persona.display_name,
                    persona_text=persona.to_prompt_block(state).split("【当前状态】")[0],
                    state_text=state.to_prompt_block(),
                )
            user_text = "你刚才已经空闲了一会儿，根据当前状态决定你想做什么。"
            ledger = self._ledger_block()
            if ledger:
                system_prompt += "\n\n" + ledger

            response = await llm.chat_simple(
                system_prompt=system_prompt,
                user_text=user_text,
            )

            # 解析 JSON
            action = self._parse_json_action(response)
            if action:
                logger.debug(
                    f"[brain:will_engine] API 决策: {action}"
                )
                return action
            else:
                logger.warning(
                    f"[brain:will_engine] API 返回非 JSON: {response[:100]}"
                )
                return None

        except Exception as e:
            logger.exception(f"[brain:will_engine] API 决策失败: {e}")
            return None

    @staticmethod
    def _parse_json_action(text: str) -> Optional[dict]:
        """解析 LLM 返回的 JSON 动作"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except Exception:
            pass

        # 尝试提取 JSON 块（处理 ```json ... ``` 包裹）
        match = re.search(r"\{.*?\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass

        return None


__all__ = ['WillEngineBrain']
