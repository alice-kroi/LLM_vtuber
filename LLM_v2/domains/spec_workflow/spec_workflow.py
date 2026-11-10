# ============================================================
# LLM_v2/domains/spec_workflow/spec_workflow.py — 规格驱动交付能力域
# ============================================================
# 移植自 SpecWorkflow（DSH 技能包）的七阶段流程，落到本项目原生能力域：
#   需求审讯 → 写规格 → 执行 → 交付评审 → 修复计划 → 缺陷诊断
# 核心防遗忘机制（这就是"为什么不丢需求"）：
#   1. 需求写成账本落库，不依赖对话上下文
#   2. 每轮执行前注入账本，改过文件后已通过项自动转 stale
#   3. 只有带证据（命令+退出码）才算 verified；评审只认账本
# ============================================================

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.domains.spec_workflow.spec_workflow_tools import (
    SPEC_TOOL_NAMES,
    SPEC_TOOLS_SCHEMA,
)
from LLM_v2.domains.spec_workflow.store import (
    PROJECT_ROOT,
    STATUS_APPROVED,
    SpecStore,
)

logger = logging.getLogger(__name__)

# 需求审讯：出现这些信号说明不是一行改动，应该走完整流程
_AMBIGUOUS_MARKERS = ("更好", "优化一下", "大概", "可能", "顺便", "重构",
                      "升级", "完善", "支持", "整个", "全部")
_RISK_MARKERS = ("权限", "安全", "数据", "迁移", "支付", "上线", "并发",
                 "兼容", "加密", "审计", "删除")
_CROSS_MODULE_MARKERS = ("模块", "接口", "api", "前后端", "数据库", "架构",
                         "能力域", "插件", "多文件")
_DIRECT_MARKERS = ("改文案", "改个名", "调大", "调小", "加个日志", "换个颜色",
                   "错别字", "空格", "注释")


def _triage(request: str, files_hint: str = "") -> Dict[str, Any]:
    """判定直接改动 vs 完整规格流程（对应上游 Fast-Path Triage）"""
    text = f"{request or ''} {files_hint or ''}".lower()
    reasons: List[str] = []
    full = False

    direct_hits = [m for m in _DIRECT_MARKERS if m in text]
    risky_hits = [m for m in _RISK_MARKERS if m in text]
    cross_hits = [m for m in _CROSS_MODULE_MARKERS if m in text]
    # 快通道优先：明确的窄改动、且不碰风险面/跨模块，就不该套流程
    if direct_hits and not risky_hits and not cross_hits and len(request or "") <= 160:
        return {
            "success": True,
            "path": "direct",
            "reasons": [f"明显是小改动：{'、'.join(direct_hits[:2])}"],
            "must_answer": [],
            "next": "直接改，改完用一条命令验证即可，不必建规格",
        }

    ambiguous = [m for m in _AMBIGUOUS_MARKERS if m in text]
    if ambiguous:
        full = True
        reasons.append(f"表述含含糊动词：{'、'.join(ambiguous[:3])}")
    risky = risky_hits
    if risky:
        full = True
        reasons.append(f"触及高风险面：{'、'.join(risky[:3])}")
    cross = cross_hits
    if cross:
        full = True
        reasons.append(f"跨模块/接口：{'、'.join(cross[:3])}")
    if len((request or "")) > 160:
        full = True
        reasons.append("请求较长，包含多个目标")

    must_answer: List[str] = []
    if full:
        must_answer.extend([
            "验收标准：哪几条行为必须成立，用什么命令验证？",
            "非目标：这次明确不做什么？",
        ])
        if risky:
            must_answer.append("权限/安全边界：谁可以触发，失败时如何拒绝？")
        if cross:
            must_answer.append("受影响模块与接口契约：改哪些文件，对外行为是否变化？")

    return {
        "success": True,
        "path": "full" if full else "direct",
        "reasons": reasons,
        "must_answer": must_answer,
        "next": ("走 spec_create 写规格 → spec_approve → 执行 → spec_review"
                 if full else "直接改，改完用一条命令验证即可，不必建规格"),
    }


class SpecWorkflowDomain(Actuator):
    """规格驱动交付流程（R1：只写工作产物与账本，不执行代码）"""

    id = "spec_workflow"
    label = "规格驱动交付流程"
    prefix = "spec_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    def __init__(self, db_path: Any = None, artifact_root: Any = None):
        self._db_path = db_path
        self._artifact_root = artifact_root
        self._store: Optional[SpecStore] = None
        self._lock = threading.RLock()

    # ---------- 契约 ----------

    def allowed_names(self) -> List[str]:
        return list(SPEC_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(SPEC_TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return (
            "可用工具（规格驱动交付流程，R1 只写工作产物）：\n"
            "- spec_triage: 先判定该直接改还是走完整流程（含糊/高风险/跨模块 → 完整流程）\n"
            "- spec_create: 写规格与需求清单（每条可验收，尽量给 check 命令）\n"
            "- spec_approve / spec_amend: 审批或修订规格\n"
            "- spec_requirements: 取需求账本（执行时每轮都要对照）\n"
            "- spec_record: 写入每条需求的证据（命令 + 退出码）\n"
            "- spec_review: 按账本判定完成度与上线结论\n"
            "- spec_fix_plan: 未通过项生成修复规格\n"
            "- spec_bug_report: 缺陷诊断报告\n"
            "流程约定：\n"
            "1. 非一行改动都先 spec_triage；判定 full 时先规格后代码，规格未审批不进入执行。\n"
            "2. 执行阶段不得新增需求；新发现写进 spec_amend 或记为后续项。\n"
            "3. 评审阶段只判定不改代码；结论只能是 can ship / fix before ship / do not ship。\n"
            "4. 需求账本是唯一事实源：没证据的条目不等于完成。\n"
        )

    def preflight(self) -> Optional[str]:
        try:
            store = self._get_store()
            store.list(1)
        except Exception as e:  # noqa: BLE001
            return f"规格账本不可用: {e}"
        return None

    # ---------- 内部 ----------

    def _get_store(self) -> SpecStore:
        with self._lock:
            if self._store is None:
                self._store = SpecStore(self._db_path, self._artifact_root)
            return self._store

    def _resolve(self, spec_id: str) -> Dict[str, Any]:
        store = self._get_store()
        spec = store.find(spec_id) if (spec_id or "").strip() else store.active()
        return spec

    # ---------- 工具 ----------

    def spec_triage(self, request: str, files_hint: str = "") -> Dict[str, Any]:
        request = (request or "").strip()
        if not request:
            return {"success": False, "error": "request 不能为空"}
        return _triage(request, files_hint)

    def spec_create(self, title: str, requirements: Any,
                    goal: str = "", non_goals: str = "",
                    validation: str = "") -> Dict[str, Any]:
        if not (title or "").strip():
            return {"success": False, "error": "title 不能为空"}
        reqs = self._normalize_requirements(requirements)
        if not reqs:
            return {"success": False, "error": "requirements 至少要有一条"}
        store = self._get_store()
        spec = store.create(title, goal=goal, requirements=reqs,
                            non_goals=non_goals, validation=validation)
        logger.info(f"[spec_workflow] 规格已创建: {spec['id']} {spec['title']}")
        return {"success": True, "spec": self._brief(spec),
                "spec_path": str(store.write_spec_md(spec["id"])),
                "next": "确认需求无误后调用 spec_approve 进入执行"}

    @staticmethod
    def _normalize_requirements(requirements: Any) -> List[Dict[str, str]]:
        """接受字符串 / 对象 / JSON 字符串，统一成 [{text, check, id}]"""
        if isinstance(requirements, str):
            raw = requirements.strip()
            if raw.startswith("["):
                import json
                try:
                    requirements = json.loads(raw)
                except Exception:  # noqa: BLE001
                    requirements = [line.strip(" -") for line in raw.splitlines()
                                    if line.strip()]
            else:
                requirements = [line.strip(" -0123456789.")
                                for line in raw.splitlines() if line.strip()]
        if not isinstance(requirements, list):
            return []
        out: List[Dict[str, str]] = []
        for item in requirements:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                check = str(item.get("check") or "").strip()
                req_id = str(item.get("id") or "").strip()
            else:
                text, check, req_id = str(item or "").strip(), "", ""
            if not text:
                continue
            entry = {"text": text}
            if check:
                entry["check"] = check
            if req_id:
                entry["id"] = req_id
            out.append(entry)
        return out

    def spec_list(self, limit: int = 10) -> Dict[str, Any]:
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 10
        specs = self._get_store().list(limit)
        return {"success": True, "count": len(specs),
                "specs": [self._brief(s) for s in specs]}

    def spec_show(self, spec_id: str) -> Dict[str, Any]:
        spec = self._resolve(spec_id)
        if not spec:
            return {"success": False, "error": "规格不存在"}
        return {"success": True, "spec": spec,
                "ledger": self._get_store().requirements_block(spec)}

    def spec_approve(self, spec_id: str, note: str = "") -> Dict[str, Any]:
        store = self._get_store()
        spec = store.approve(spec_id, note)
        if not spec:
            return {"success": False, "error": "规格不存在或没有需求条目"}
        return {"success": True, "spec": self._brief(spec),
                "next": "现在可以执行；执行时每轮对照需求账本"}

    def spec_amend(self, spec_id: str, note: str,
                   add_requirements: Any = None) -> Dict[str, Any]:
        store = self._get_store()
        reqs = self._normalize_requirements(add_requirements or [])
        spec = store.amend(spec_id, note, reqs)
        if not spec:
            return {"success": False, "error": "规格不存在"}
        return {"success": True, "spec": self._brief(spec),
                "added": len(reqs)}

    def spec_requirements(self, spec_id: str = "") -> Dict[str, Any]:
        spec = self._resolve(spec_id)
        if not spec:
            return {"success": False, "error": "没有可用规格（先 spec_create）"}
        return {"success": True, "spec_id": spec["id"], "title": spec["title"],
                "status": spec["status"], "requirements": spec["requirements"],
                "ledger": self._get_store().requirements_block(spec)}

    def spec_record(self, spec_id: str, results: Any) -> Dict[str, Any]:
        store = self._get_store()
        spec = store.find(spec_id)
        if not spec:
            return {"success": False, "error": "规格不存在"}
        if isinstance(results, str):
            import json
            try:
                results = json.loads(results)
            except Exception:  # noqa: BLE001
                return {"success": False, "error": "results 需为数组"}
        if not isinstance(results, list):
            return {"success": False, "error": "results 需为数组"}
        updated = store.record(spec["id"], results)
        return {"success": True, "spec": self._brief(updated),
                "ledger": store.requirements_block(updated)}

    def spec_review(self, spec_id: str = "") -> Dict[str, Any]:
        spec = self._resolve(spec_id)
        if not spec:
            return {"success": False, "error": "没有可用规格"}
        return self._get_store().review(spec["id"])

    def spec_fix_plan(self, spec_id: str = "") -> Dict[str, Any]:
        spec = self._resolve(spec_id)
        if not spec:
            return {"success": False, "error": "没有可用规格"}
        return self._get_store().repair_plan(spec["id"])

    def spec_bug_report(self, title: str, symptom: str, root_cause: str,
                        repro: str, fix: str, validation: str) -> Dict[str, Any]:
        title = (title or "").strip()
        if not title:
            return {"success": False, "error": "title 不能为空"}
        path = self._get_store().write_bug_report(
            title, symptom, root_cause, repro, fix, validation)
        return {"success": True, "path": str(path)}

    # ---------- 辅助 ----------

    @staticmethod
    def _brief(spec: Dict[str, Any]) -> Dict[str, Any]:
        reqs = spec.get("requirements") or []
        done = sum(1 for r in reqs if r.get("status") == "verified")
        return {
            "id": spec.get("id"),
            "slug": spec.get("slug"),
            "title": spec.get("title"),
            "status": spec.get("status"),
            "progress": f"{done}/{len(reqs)}",
            "artifacts": spec.get("artifacts"),
            "updated_at": spec.get("updated_at"),
        }


def build_spec_workflow_domain() -> SpecWorkflowDomain:
    return SpecWorkflowDomain()


__all__ = ["SpecWorkflowDomain", "build_spec_workflow_domain",
           "PROJECT_ROOT", "STATUS_APPROVED"]
