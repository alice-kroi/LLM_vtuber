# ============================================================
# LLM_v2/domains/eval_lab/eval_store.py — 评测运行账本
# ============================================================
# 移植自 dsh-eval 的核心：一次运行 = 一份 JSON + 一份 Markdown 报告，
# 历史落 persona/state.db，便于做配对 A/B（改动到底变好还是变坏）。
# ============================================================

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = PROJECT_ROOT / "persona" / "state.db"
RUNS_DIR = PROJECT_ROOT / "evals" / "runs"
BENCH_DIR = PROJECT_ROOT / "evals" / "benchmarks"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL NOT NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    passed      INTEGER NOT NULL DEFAULT 0,
    report_path TEXT NOT NULL DEFAULT '',
    cases       TEXT NOT NULL DEFAULT '[]'
);
"""


class EvalStore:
    """评测运行记录（只存结果，不执行用例）"""

    def __init__(self, db_path: Any = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Dict[str, Any]:
        if not row:
            return {}
        item = dict(row)
        try:
            item["cases"] = json.loads(item.get("cases") or "[]")
        except Exception:  # noqa: BLE001
            item["cases"] = []
        return item

    def save(self, name: str, started_at: float, cases: List[Dict[str, Any]],
             report_path: str = "") -> Dict[str, Any]:
        run_id = uuid.uuid4().hex[:12]
        total = len(cases)
        passed = sum(1 for c in cases if c.get("passed"))
        now = time.time()
        self._conn.execute(
            "INSERT INTO eval_runs(id, name, started_at, finished_at, total,"
            " passed, report_path, cases) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, name[:120], started_at, now, total, passed, report_path,
             json.dumps(cases, ensure_ascii=False)))
        self._conn.commit()
        return self.get(run_id)

    def get(self, run_id: str) -> Dict[str, Any]:
        key = (run_id or "").strip()
        row = self._conn.execute(
            "SELECT * FROM eval_runs WHERE id=?", (key,)).fetchone()
        if not row:
            row = self._conn.execute(
                "SELECT * FROM eval_runs WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1",
                (f"{key}%",)).fetchone()
        return self._row(row)

    def list(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM eval_runs ORDER BY started_at DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return [self._row(r) for r in rows]

    def latest(self, name: str) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM eval_runs WHERE name=? ORDER BY started_at DESC LIMIT 1",
            (name,)).fetchone()
        return self._row(row)

    # ---------- 报告 ----------

    def write_reports(self, name: str, cases: List[Dict[str, Any]],
                      started_at: float, metrics: Dict[str, Any]) -> Path:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started_at))
        slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name)[:40]
        payload = {
            "name": name,
            "started_at": started_at,
            "finished_at": time.time(),
            "metrics": metrics,
            "cases": cases,
        }
        json_path = RUNS_DIR / f"{slug}-{stamp}.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")

        lines = [f"# 评测报告：{name}", "",
                 f"- 运行：{stamp}",
                 f"- 用例：{metrics.get('total')} 通过 {metrics.get('passed')}"
                 f"（成功率 {metrics.get('success_rate')}）",
                 f"- 总耗时：{metrics.get('duration_s')}s", "",
                 "| 用例 | 结果 | 耗时 | 失败原因 |", "|---|---|---|---|"]
        for case in cases:
            reason = (case.get("error") or "").replace("|", "/").replace("\n", " ")[:80]
            lines.append(f"| {case.get('id')} | "
                         f"{'PASS' if case.get('passed') else 'FAIL'} | "
                         f"{case.get('duration_s')}s | {reason or '-'} |")
        (RUNS_DIR / f"{slug}-{stamp}.md").write_text("\n".join(lines) + "\n",
                                                     encoding="utf-8")
        return json_path


__all__ = ["EvalStore", "PROJECT_ROOT", "DEFAULT_DB", "RUNS_DIR", "BENCH_DIR"]
