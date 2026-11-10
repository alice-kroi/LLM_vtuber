# ============================================================
# LLM_v2/domains/central_info/central_info.py — 中央信息能力域
# ============================================================
# 三层固定顺序：
#   固定层（问题/需求）：来自 spec_workflow 的已审批规格，只读，无写工具
#   可变层（任务进度）：运行时任务表，操作脑可写（CAS + 相位机）
#   域内信息：各域卡片/索引 + 详情渠道（本域只做索引，不复制全量数据）
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.core.central_context import get_central
from LLM_v2.domains.central_info.central_info_tools import (
    CENTRAL_INFO_TOOL_NAMES,
    CENTRAL_INFO_TOOLS_SCHEMA,
)
from LLM_v2.domains.central_info.task_store import TaskStore

logger = logging.getLogger(__name__)


_CENTRAL_PROMPT = """【中央信息】提供全局索引，不提供域内完整细节。
- ci_overview：按固定顺序返回「问题与需求 → 任务进度 → 各域卡片/索引 → 近期聊天」
- ci_brief：只读当前问题与需求（来自已审批规格）
- ci_tasks：任务进度表；ci_task_update：推进任务（必须带 revision）
- ci_domain：按 id 查看某个域的工具与详情渠道
- ci_chat：最近跨来源对话

权限铁律：
1. 问题与需求（ci_brief）是固定层，**没有任何写工具**。不要试图改写目标或验收标准，
   需要变更时说明理由，让用户在 WebUI 确认或走规格修订（spec_amend）。
2. 你只能推进任务进度：更新相位/百分比/阶段/说明，并且必须带当前 revision；
   过期 revision 会被拒绝，需先 ci_tasks 重新读取。
3. done / failed 是终态，不可回退；要返工请新建任务，不要把旧任务改回 running。
4. 中央信息不复制域内全量数据，域内细节用 ci_domain 拿渠道后去调对应工具。
"""


class CentralInfoDomain(Actuator):
    """中央信息（Actuator，默认启用）"""

    id = "central_info"
    label = "中央信息"
    prefix = "ci_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 10.0
    tool_timeouts = {
        "ci_overview": 8.0,
        "ci_brief": 8.0,
        "ci_tasks": 8.0,
        "ci_task_update": 8.0,
        "ci_domain": 8.0,
        "ci_chat": 8.0,
    }

    def __init__(self, db_path: Any = None):
        self._db_path = db_path
        self._task_store: Optional[TaskStore] = None
        self._spec_store = None

    async def stop(self) -> None:
        if self._task_store is not None:
            self._task_store.close()
            self._task_store = None
        if self._spec_store is not None:
            try:
                self._spec_store.close()
            except Exception:  # noqa: BLE001
                pass
            self._spec_store = None

    def allowed_names(self) -> List[str]:
        return list(CENTRAL_INFO_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in CENTRAL_INFO_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _CENTRAL_PROMPT

    def central_card(self) -> Dict[str, Any]:
        snap = get_central().snapshot(chat_limit=1)
        counts = snap.get("counts") or {}
        brief = self._brief_projection()
        active = self._tasks().list(limit=50)
        objective = (brief.get("objective") or "").strip()
        summary = f"中央信息：{counts.get('domains', 0)} 个能力域，"
        if objective:
            summary += f"当前问题「{objective[:24]}」，"
        summary += f"{len(active)} 个进行中任务"
        return {
            "summary": summary,
            "progress": (f"进行中 {len(active)} 个任务"
                         if active else "当前没有进行中的任务"),
            "channels": [
                {"name": "总览", "tools": ["ci_overview"],
                 "description": "问题与需求 → 任务进度 → 各域索引"},
                {"name": "问题与需求（只读）", "tools": ["ci_brief"],
                 "description": "目标、验收条目与状态、非目标"},
                {"name": "任务进度", "tools": ["ci_tasks", "ci_task_update"],
                 "description": "读取/推进任务（需 revision）"},
                {"name": "单域索引", "tools": ["ci_domain"],
                 "description": "某域的摘要与详情渠道"},
                {"name": "聊天", "tools": ["ci_chat"],
                 "description": "最近跨来源对话"},
            ],
        }

    # ---------- 固定层投影 / 可变层 ----------

    def _tasks(self) -> TaskStore:
        if self._task_store is None:
            self._task_store = TaskStore(self._db_path)
        return self._task_store

    def _brief_projection(self, spec_id: str = "") -> Dict[str, Any]:
        """问题与需求：只读投影自已审批规格，本域不存副本"""
        try:
            from LLM_v2.domains.spec_workflow.store import SpecStore
            if self._spec_store is None:
                self._spec_store = SpecStore()
            store = self._spec_store
            spec = store.find(spec_id) if (spec_id or "").strip() else store.active()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[central_info] 规格投影失败: {e}")
            return {"has_spec": False, "reason": f"规格账本不可用: {e}"}
        if not spec:
            return {"has_spec": False,
                    "reason": "当前没有已审批规格；问题来源：用户最近一条请求",
                    "writable": False}
        reqs = spec.get("requirements") or []
        return {
            "has_spec": True,
            "writable": False,
            "spec_id": spec.get("id"),
            "slug": spec.get("slug"),
            "status": spec.get("status"),
            "objective": spec.get("goal") or spec.get("title") or "",
            "title": spec.get("title") or "",
            "non_goals": spec.get("non_goals") or "",
            "validation": spec.get("validation") or "",
            "acceptance": [{"id": r.get("req_id"), "text": r.get("text"),
                            "status": r.get("status")} for r in reqs],
            "updated_at": spec.get("updated_at"),
        }

    def ci_brief(self, spec_id: str = "") -> Dict[str, Any]:
        brief = self._brief_projection(spec_id)
        return {"success": True, **brief,
                "note": "问题与需求是固定层，只读；变更请用户确认或走 spec_amend"}

    def ci_tasks(self, limit: int = 20,
                 include_terminal: bool = False) -> Dict[str, Any]:
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 20
        rows = self._tasks().list(limit, bool(include_terminal))
        return {"success": True, "count": len(rows), "tasks": rows,
                "table": self._tasks().render_table(limit, bool(include_terminal))}

    def ci_task_update(self, task_id: str = "", title: str = "",
                       revision: int = 0, phase: str = "", percent: int = -1,
                       stage: str = "", detail: str = "",
                       actor: str = "operator") -> Dict[str, Any]:
        store = self._tasks()
        task_id = (task_id or "").strip()
        if not task_id:
            if not (title or "").strip():
                return {"success": False,
                        "error": "新建任务需要 title；更新任务需要 task_id + revision"}
            task = store.create(title, owner=actor, stage=stage, detail=detail,
                                phase=phase or "running")
            return {"success": True, "task": task, "revision": task["revision"],
                    "message": f"已新建任务 {task['task_id']}"}
        result = store.update(task_id, revision, actor=actor, phase=phase,
                              percent=None if percent is None or percent < 0 else percent,
                              stage=stage, detail=detail)
        if not result.get("ok"):
            return {"success": False, "error": result.get("error"),
                    "task": result.get("task")}
        task = result["task"]
        return {"success": True, "task": task, "revision": task["revision"]}

    # ---------- 工具 ----------

    def _push_self(self) -> None:
        """刷新自己的中央卡片，避免摘要停留在启动时的计数"""
        try:
            card = self.central_card()
            get_central().update_domain(
                self.id, label=self.label,
                summary=card.get("summary") or "",
                channels=card.get("channels") or [],
                status="started", tools=self.allowed_names(),
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _to_card(card: Dict[str, Any]) -> Dict[str, Any]:
        """把内部域卡片整理成标准的"局部信息卡片"（列表→详情→渠道）"""
        channels = [{
            "name": ch.get("name"),
            "tools": ch.get("tools") or [],
            "description": ch.get("description") or "",
        } for ch in card.get("channels") or []]
        tools = card.get("tools") or []
        if not channels and tools:
            channels = [{
                "name": "本域工具",
                "tools": tools,
                "description": "直接调用该域工具读取局部信息",
            }]
        cache = card.get("cache") or {}
        if not cache:
            cache = {"has_cache": False}
        return {
            "id": card.get("id"),
            "label": card.get("label"),
            "kind": "local_info",
            "description": card.get("summary") or "",
            "status": card.get("status"),
            "progress": card.get("progress") or "",
            "access": channels,
            "tools": tools,
            "cache": cache,
            "updated_at": card.get("updated_at"),
        }

    def ci_overview(self, chat_limit: int = 6,
                    domain_limit: int = 32) -> Dict[str, Any]:
        central = get_central()
        # 紧凑视图：有状态的域给完整卡片，其余只给极简索引
        snap = central.compact_snapshot(chat_limit=chat_limit,
                                        domain_limit=domain_limit)
        snap["cards"] = [self._to_card(c) for c in snap.get("cards") or []]
        snap["index"] = [{**item, "kind": "index"}
                         for item in snap.get("index") or []]
        brief = self._brief_projection()
        task_table = self._tasks().render_table(20)
        snap["brief"] = brief
        snap["task_table"] = task_table
        # 固定顺序：问题 → 需求 → 任务 → 各域 → 聊天（问题永远在最前）
        head = [self._brief_text(brief), "## 任务进度", task_table, ""]
        snap["text"] = "\n".join(head) + "\n" + central.overview_text(
            chat_limit=chat_limit, domain_limit=domain_limit)
        self._push_self()
        return snap

    @staticmethod
    def _brief_text(brief: Dict[str, Any]) -> str:
        if not brief.get("has_spec"):
            return ("## 问题与需求（固定层）\n"
                    f"- {brief.get('reason') or '暂无已审批规格'}\n"
                    "- 本层只读：任何角色都不能在这里改写问题或验收标准。")
        lines = ["## 问题与需求（固定层，只读）",
                 f"- 目标：{brief.get('objective') or '（未填写）'}"]
        if brief.get("non_goals"):
            lines.append(f"- 非目标：{brief['non_goals']}")
        acceptance = brief.get("acceptance") or []
        if acceptance:
            mark = {"verified": "[x]", "stale": "[~]", "failed": "[!]"}
            lines.append("- 验收：")
            for item in acceptance:
                lines.append(f"  {mark.get(item.get('status'), '[ ]')} "
                             f"{item.get('id')}. {item.get('text')}")
        lines.append(f"- 规格：{brief.get('slug')}（{brief.get('status')}）")
        return "\n".join(lines)

    def ci_domain(self, domain_id: str = "") -> Dict[str, Any]:
        domain_id = str(domain_id or "").strip()
        if not domain_id:
            return {"success": False, "error": "缺少 domain_id"}
        card = get_central().domain(domain_id)
        if card is None:
            snap = get_central().snapshot(chat_limit=0)
            known = [d.get("id") for d in snap.get("domains") or []]
            return {"success": False,
                    "error": f"未知能力域 {domain_id}；已知：{'、'.join(known) or '无'}"}
        standard = self._to_card(card)
        return {
            "success": True,
            "card": standard,
            "domain": standard,
            "hint": "从 card.access 选择渠道调用对应工具；cache 信号说明缓存是否可用/是否已有数据。",
        }

    def ci_chat(self, limit: int = 10) -> Dict[str, Any]:
        rows = get_central().recent_chat(limit=limit)
        return {"success": True, "count": len(rows), "chat": rows}


def build_central_info_domain() -> CentralInfoDomain:
    return CentralInfoDomain()


__all__ = ["CentralInfoDomain", "build_central_info_domain"]
