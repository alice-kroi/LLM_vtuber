# ============================================================
# LLM_v2/domains/spec_workflow/store.py — 规格与需求账本（SQLite + 工作产物）
# ============================================================
# 移植自 SpecWorkflow（DSH 技能包）的持久化约定：
#   - 规格、需求、证据、评审结论落 persona/state.db，进程重启不丢
#   - 工作产物落 .spec-workflow/<feature-slug>/，与生产代码分离
#   - 需求条目是"每轮必读"的账本：状态 pending / verified / stale / failed
#
# 关键约束（对应上游 Hard Gates）：
#   - 只有带证据的需求才算 verified；改过文件后已通过项自动转 stale
#   - 评审只判定，不改代码；修复计划只产出 repair-spec.md
# ============================================================

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = PROJECT_ROOT / "persona" / "state.db"
ARTIFACT_DIR = ".spec-workflow"

STATUS_STAGED = "staged"
STATUS_APPROVED = "approved"
STATUS_IN_PROGRESS = "in_progress"
STATUS_REVIEWED = "reviewed"
STATUS_BLOCKED = "blocked"

REQ_PENDING = "pending"
REQ_VERIFIED = "verified"
REQ_STALE = "stale"
REQ_FAILED = "failed"

# 活跃规格有效期：避免已审批规格永久套用到无关的后续编码任务
ACTIVE_TTL_SEC = 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS specs (
    id          TEXT PRIMARY KEY,
    slug        TEXT NOT NULL,
    title       TEXT NOT NULL,
    goal        TEXT NOT NULL DEFAULT '',
    non_goals   TEXT NOT NULL DEFAULT '',
    validation  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'staged',
    artifacts   TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS spec_requirements (
    spec_id     TEXT NOT NULL,
    req_id      TEXT NOT NULL,
    text        TEXT NOT NULL,
    check_cmd   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    evidence    TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL,
    PRIMARY KEY (spec_id, req_id)
);
CREATE TABLE IF NOT EXISTS spec_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    at          REAL NOT NULL
);
"""


def _slugify(text: str) -> str:
    s = re.sub(r"\s+", "-", (text or "spec").strip())
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "", s)
    s = s.strip("-").lower()
    return s[:60] or "spec"


def _now() -> float:
    return time.time()


class SpecStore:
    """规格账本：规格 / 需求 / 事件（只做数据，不执行代码）"""

    def __init__(self, db_path: Any = None, artifact_root: Any = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root = Path(artifact_root) if artifact_root else PROJECT_ROOT
        self._conn = sqlite3.connect(str(self.db_path), timeout=10,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------- 内部 ----------

    def _row(self, row: Optional[sqlite3.Row]) -> Dict[str, Any]:
        return dict(row) if row else {}

    def _requirements(self, spec_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM spec_requirements WHERE spec_id=? ORDER BY req_id",
            (spec_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            raw = item.pop("evidence", "") or ""
            try:
                item["evidence"] = json.loads(raw) if raw else {}
            except Exception:  # noqa: BLE001
                item["evidence"] = {"summary": raw}
            out.append(item)
        return out

    def _spec(self, row: Optional[sqlite3.Row]) -> Dict[str, Any]:
        spec = self._row(row)
        if spec:
            spec["requirements"] = self._requirements(spec["id"])
        return spec

    def _event(self, spec_id: str, kind: str, detail: str = "") -> None:
        self._conn.execute(
            "INSERT INTO spec_events(spec_id, kind, detail, at) VALUES(?,?,?,?)",
            (spec_id, kind, detail[:2000], _now()))
        self._conn.commit()

    def _artifacts_dir(self, slug: str) -> Path:
        return self.artifact_root / ARTIFACT_DIR / slug

    # ---------- 规格 ----------

    def create(self, title: str, goal: str = "",
               requirements: Optional[List[Any]] = None,
               non_goals: str = "", validation: str = "") -> Dict[str, Any]:
        title = (title or "").strip() or "未命名规格"
        spec_id = uuid.uuid4().hex[:16]
        slug = _slugify(title)
        now = _now()
        art = self._artifacts_dir(slug)
        self._conn.execute(
            "INSERT INTO specs(id, slug, title, goal, non_goals, validation,"
            " status, artifacts, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (spec_id, slug, title, goal, non_goals, validation,
             STATUS_STAGED, str(art), now, now))
        self._add_requirements(spec_id, requirements or [])
        self._conn.commit()
        self._event(spec_id, "created", title)
        self.write_spec_md(spec_id)
        return self.get(spec_id)

    def _add_requirements(self, spec_id: str, requirements: List[Any]) -> None:
        existing = {r["req_id"] for r in self._requirements(spec_id)}
        n = len(existing)
        for item in requirements:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                check = str(item.get("check") or item.get("check_cmd") or "").strip()
                req_id = str(item.get("id") or "").strip()
            else:
                text, check, req_id = str(item or "").strip(), "", ""
            if not text:
                continue
            n += 1
            req_id = req_id or f"R{n}"
            if req_id in existing:
                continue
            self._conn.execute(
                "INSERT INTO spec_requirements(spec_id, req_id, text, check_cmd,"
                " status, evidence, updated_at) VALUES(?,?,?,?,?,?,?)",
                (spec_id, req_id, text, check, REQ_PENDING, "", _now()))
            existing.add(req_id)
        self._conn.commit()

    def get(self, spec_id: str) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM specs WHERE id=?", (spec_id,)).fetchone()
        return self._spec(row)

    def find(self, spec_id: str) -> Dict[str, Any]:
        """按 id / slug / 标题前缀找规格"""
        key = (spec_id or "").strip()
        if not key:
            return {}
        spec = self.get(key)
        if spec:
            return spec
        row = self._conn.execute(
            "SELECT * FROM specs WHERE slug=? ORDER BY created_at DESC LIMIT 1",
            (key,)).fetchone()
        if row:
            return self._spec(row)
        rows = self._conn.execute(
            "SELECT * FROM specs ORDER BY created_at DESC").fetchall()
        for row in rows:
            if str(row["title"]).startswith(key):
                return self._spec(row)
        return {}

    def list(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM specs ORDER BY created_at DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return [self._spec(r) for r in rows]

    def active(self) -> Dict[str, Any]:
        """当前活跃规格：最近一个 approved / in_progress 且未过期的规格

        过期保护：没有时间窗的话，一个已审批规格会永久污染后续所有编码任务。
        """
        row = self._conn.execute(
            "SELECT * FROM specs WHERE status IN (?,?) AND updated_at >= ?"
            " ORDER BY updated_at DESC LIMIT 1",
            (STATUS_APPROVED, STATUS_IN_PROGRESS, _now() - ACTIVE_TTL_SEC)).fetchone()
        return self._spec(row)

    def set_status(self, spec_id: str, status: str) -> Dict[str, Any]:
        self._conn.execute(
            "UPDATE specs SET status=?, updated_at=? WHERE id=?",
            (status, _now(), spec_id))
        self._conn.commit()
        self._event(spec_id, f"status:{status}")
        return self.get(spec_id)

    def approve(self, spec_id: str, note: str = "") -> Dict[str, Any]:
        spec = self.find(spec_id)
        if not spec:
            return {}
        if not spec.get("requirements"):
            return {}
        self.set_status(spec["id"], STATUS_APPROVED)
        if note:
            self._event(spec["id"], "approve-note", note)
        return self.get(spec["id"])

    def amend(self, spec_id: str, note: str = "",
              add_requirements: Optional[List[Any]] = None) -> Dict[str, Any]:
        spec = self.find(spec_id)
        if not spec:
            return {}
        self._add_requirements(spec["id"], add_requirements or [])
        count = len(self._amendments(spec))
        path = Path(spec["artifacts"]) / "amendments"
        path.mkdir(parents=True, exist_ok=True)
        file = path / f"{count + 1:02d}-{_slugify(note)[:40] or 'amend'}.md"
        file.write_text(
            f"# 规格修订 {count + 1}\n\n- 规格：{spec['title']}（{spec['id']}）\n"
            f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n## 变更说明\n{note}\n\n"
            f"## 新增需求\n" + ("\n".join(
                f"- {r.get('text') if isinstance(r, dict) else r}"
                for r in (add_requirements or [])) or "- （无）") + "\n",
            encoding="utf-8")
        self._conn.execute(
            "UPDATE specs SET updated_at=? WHERE id=?", (_now(), spec["id"]))
        self._conn.commit()
        self._event(spec["id"], "amended", note[:300])
        self.write_spec_md(spec["id"])
        return self.get(spec["id"])

    def _amendments(self, spec: Dict[str, Any]) -> List[str]:
        path = Path(spec.get("artifacts") or "") / "amendments"
        return [p.name for p in sorted(path.glob("*.md"))] if path.is_dir() else []

    # ---------- 需求账本 ----------

    def record(self, spec_id: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """写入需求证据。results: [{id, status, evidence}]"""
        spec = self.find(spec_id)
        if not spec:
            return {}
        valid = {r["req_id"] for r in spec["requirements"]}
        for item in results or []:
            if not isinstance(item, dict):
                continue
            req_id = str(item.get("id") or item.get("req_id") or "").strip()
            if req_id not in valid:
                continue
            status = str(item.get("status") or REQ_VERIFIED).strip().lower()
            if status not in (REQ_PENDING, REQ_VERIFIED, REQ_STALE, REQ_FAILED):
                status = REQ_VERIFIED
            evidence = item.get("evidence")
            if not isinstance(evidence, dict):
                evidence = {"summary": str(evidence or "")}
            self._conn.execute(
                "UPDATE spec_requirements SET status=?, evidence=?, updated_at=?"
                " WHERE spec_id=? AND req_id=?",
                (status, json.dumps(evidence, ensure_ascii=False), _now(),
                 spec["id"], req_id))
        self._conn.commit()
        self._event(spec["id"], "evidence-recorded",
                    f"{len(results or [])} 条")
        return self.get(spec["id"])

    def mark_stale(self, spec_id: str) -> int:
        """代码改动后：已通过项退回待验（回归守卫）"""
        cur = self._conn.execute(
            "UPDATE spec_requirements SET status=?, updated_at=?"
            " WHERE spec_id=? AND status=?",
            (REQ_STALE, _now(), spec_id, REQ_VERIFIED))
        self._conn.commit()
        return cur.rowcount

    def requirements_block(self, spec: Dict[str, Any]) -> str:
        """渲染成每轮必读的需求清单"""
        reqs = spec.get("requirements") or []
        if not reqs:
            return ""
        mark = {REQ_VERIFIED: "[x]", REQ_STALE: "[~]", REQ_FAILED: "[!]"}
        lines = [f"【需求清单｜规格 {spec.get('slug')}｜每轮必读，不得丢失】"]
        for r in reqs:
            lines.append(f"{mark.get(r['status'], '[ ]')} {r['req_id']}. {r['text']}")
        lines.append("规则：任何改动都不得让已通过项（[x]）回落；"
                     "改过文件后 [x] 会变成 [~]，必须重新验证才能再次算完成。")
        return "\n".join(lines)

    def missing(self, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [r for r in (spec.get("requirements") or [])
                if r["status"] != REQ_VERIFIED]

    def review(self, spec_id: str) -> Dict[str, Any]:
        """机械评审：只判完成度，不改代码"""
        spec = self.find(spec_id)
        if not spec:
            return {"success": False, "error": "规格不存在"}
        reqs = spec.get("requirements") or []
        failed = [r for r in reqs if r["status"] != REQ_VERIFIED]
        if not reqs:
            verdict, completion = "do not ship", "incomplete"
        elif not failed:
            verdict, completion = "can ship", "complete"
        else:
            verdict = "fix before ship"
            completion = "mostly complete" if len(failed) < len(reqs) else "incomplete"
        report = {
            "success": True,
            "spec_id": spec["id"],
            "title": spec["title"],
            "completion": completion,
            "ship_decision": verdict,
            "passed": [{"id": r["req_id"], "text": r["text"],
                        "evidence": r["evidence"]}
                       for r in reqs if r["status"] == REQ_VERIFIED],
            "must_fix": [{"id": r["req_id"], "text": r["text"],
                          "status": r["status"]} for r in failed],
            "report_path": str(self.write_review_md(spec, completion, verdict, failed)),
        }
        self.set_status(spec["id"], STATUS_REVIEWED if not failed else STATUS_IN_PROGRESS)
        self._event(spec["id"], "reviewed", f"{completion}/{verdict}")
        return report

    def repair_plan(self, spec_id: str) -> Dict[str, Any]:
        """把未通过需求整理成修复计划（repair-spec.md），不写代码"""
        spec = self.find(spec_id)
        if not spec:
            return {"success": False, "error": "规格不存在"}
        missing = self.missing(spec)
        path = Path(spec["artifacts"]) / "repair-spec.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = [f"# 修复规格：{spec['title']}", "",
                f"- 规格 id：{spec['id']}",
                f"- 目标：{spec.get('goal') or '（见 spec.md）'}", "",
                "## 必须修复（按顺序）", ""]
        for i, r in enumerate(missing, 1):
            body.append(f"{i}. [{r['req_id']}] {r['text']}")
            if r.get("check_cmd"):
                body.append(f"   - 验证命令：`{r['check_cmd']}`")
            if r.get("evidence"):
                body.append(f"   - 上次证据：{r['evidence']}")
        if not missing:
            body.append("- 无未通过项")
        body += ["", "## 执行方式", "",
                 "按 spec_workflow 的流程：改代码后用 check 命令重新取证，"
                 "再调用 spec_review 复核。"]
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        self._event(spec["id"], "repair-plan", str(len(missing)))
        return {"success": True, "spec_id": spec["id"], "must_fix": len(missing),
                "path": str(path)}

    # ---------- 工作产物 ----------

    def write_spec_md(self, spec_id: str) -> Path:
        spec = self.find(spec_id)
        if not spec:
            return Path()
        art = Path(spec["artifacts"])
        art.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# {spec['title']}", "",
            "## 背景与目标", spec.get("goal") or "（待补充）", "",
            "## 非目标", spec.get("non_goals") or "（无）", "",
            "## 需求与验收", "",
        ]
        for r in spec.get("requirements") or []:
            lines.append(f"- [{r['status']}] {r['req_id']}. {r['text']}")
            if r.get("check_cmd"):
                lines.append(f"  - 验证命令：`{r['check_cmd']}`")
        lines += ["", "## 验证计划", spec.get("validation") or "（按各需求 check 执行）", ""]
        path = art / "spec.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def write_review_md(self, spec: Dict[str, Any], completion: str,
                        verdict: str, failed: List[Dict[str, Any]]) -> Path:
        art = Path(spec["artifacts"])
        art.mkdir(parents=True, exist_ok=True)
        lines = [f"# 交付评审：{spec['title']}", "",
                 f"- 完成度：{completion}",
                 f"- 上线结论：{verdict}",
                 f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
                 "## 已通过", ""]
        passed = [r for r in spec.get("requirements") or []
                  if r["status"] == REQ_VERIFIED]
        for r in passed:
            ev = r.get("evidence") or {}
            lines.append(f"- [x] {r['req_id']}. {r['text']}")
            if ev:
                lines.append(f"  - 证据：{json.dumps(ev, ensure_ascii=False)[:300]}")
        if not passed:
            lines.append("- （无）")
        lines += ["", "## 必须修复", ""]
        for r in failed:
            lines.append(f"- [{r['status']}] {r['req_id']}. {r['text']}")
        if not failed:
            lines.append("- （无）")
        path = art / "review-report.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_bug_report(self, title: str, symptom: str, root_cause: str,
                         repro: str, fix: str, validation: str) -> Path:
        slug = _slugify(title)[:40] or "bug"
        art = self.artifact_root / ARTIFACT_DIR / f"bug-{slug}"
        art.mkdir(parents=True, exist_ok=True)
        path = art / "diagnosis.md"
        path.write_text(
            f"# 缺陷诊断：{title}\n\n"
            f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"## 症状\n{symptom}\n\n"
            f"## 根因\n{root_cause}\n\n"
            f"## 复现\n{repro}\n\n"
            f"## 修复\n{fix}\n\n"
            f"## 验证\n{validation}\n", encoding="utf-8")
        return path


__all__ = [
    "SpecStore", "PROJECT_ROOT", "DEFAULT_DB", "ARTIFACT_DIR",
    "STATUS_STAGED", "STATUS_APPROVED", "STATUS_IN_PROGRESS",
    "STATUS_REVIEWED", "STATUS_BLOCKED",
    "REQ_PENDING", "REQ_VERIFIED", "REQ_STALE", "REQ_FAILED",
]
