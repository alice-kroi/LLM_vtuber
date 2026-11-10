# ============================================================
# LLM_v2/domains/eval_lab/eval_lab.py — 评测能力域（R1）
# ============================================================
# 借鉴 dsh-eval 的最小可用子集：
#   benchmark.json → 逐用例跑检查命令 → 指标汇总 → JSON/MD 报告 → 历史落库
#   + 配对 A/B（胜/负/平）+ 符号检验 p 值
#
# 用例类型：
#   checks  — 一串 shell 命令，全部退出码 0 才算通过
#   spec    — 复用规格账本：重跑该规格每条需求的 check 命令
# ============================================================

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.domains.eval_lab.eval_lab_tools import EVAL_TOOL_NAMES, EVAL_TOOLS_SCHEMA
from LLM_v2.domains.eval_lab.eval_store import (
    BENCH_DIR,
    EvalStore,
    PROJECT_ROOT,
)

logger = logging.getLogger(__name__)


class EvalLabDomain(Actuator):
    """评测台：基准编排 + 指标 + 配对比较"""

    id = "eval_lab"
    label = "评测台"
    prefix = "eval_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 600.0
    tool_timeouts = {"eval_run": 600.0, "eval_compare": 30.0}

    def __init__(self, db_path: Any = None):
        self._db_path = db_path
        self._store: Optional[EvalStore] = None
        self._lock = threading.RLock()

    # ---------- 契约 ----------

    def allowed_names(self) -> List[str]:
        return list(EVAL_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(EVAL_TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return (
            "可用工具（评测台，R1 只跑本地检查命令）：\n"
            "- eval_run: 跑 evals/benchmarks/<name>.json，逐用例执行检查，出报告\n"
            "- eval_history / eval_show: 看历史与逐用例结果\n"
            "- eval_compare: 两次运行配对比较（胜/负/平 + 符号检验 p 值）\n"
            "使用原则：\n"
            "1. 改完关键代码后先跑基准，再谈“改好了”；没有基准就说没有。\n"
            "2. 比较改动前后时用 eval_compare，不要凭印象判断。\n"
            "3. 用例检查命令必须可重复执行、无副作用。\n"
        )

    def preflight(self) -> Optional[str]:
        try:
            self._eval_store().list(1)
        except Exception as e:  # noqa: BLE001
            return f"评测账本不可用: {e}"
        return None

    async def stop(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    # ---------- 内部 ----------

    def _eval_store(self) -> EvalStore:
        with self._lock:
            if self._store is None:
                self._store = EvalStore(self._db_path)
            return self._store

    @staticmethod
    def _resolve_benchmark(name: str) -> Path:
        raw = (name or "").strip()
        if not raw:
            raise ValueError("benchmark 不能为空")
        candidates = []
        for path in (raw, raw + ".json"):
            p = Path(path)
            if not p.is_absolute():
                p = PROJECT_ROOT / p
            candidates.append(p)
            candidates.append(BENCH_DIR / p.name)
        for path in candidates:
            if path.is_file():
                return path
        raise FileNotFoundError(f"找不到基准文件：{raw}（可放 evals/benchmarks/{raw}.json）")

    def _run_cmd(self, cmd: str, timeout: float = 120.0) -> Dict[str, Any]:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, shell=True, cwd=str(PROJECT_ROOT),
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": f"超时 {timeout}s",
                    "duration_s": round(time.monotonic() - t0, 2)}
        out = (proc.stdout or "")[-400:]
        err = (proc.stderr or "")[-400:]
        return {
            "ok": proc.returncode == 0,
            "reason": "" if proc.returncode == 0 else f"exit={proc.returncode} {err.strip()[:160]}",
            "stdout": out.strip(),
            "duration_s": round(time.monotonic() - t0, 2),
        }

    def _spec_checks(self, spec_id: str) -> List[str]:
        from LLM_v2.domains.spec_workflow.store import SpecStore
        spec_store = SpecStore()
        try:
            spec = spec_store.find(spec_id)
            if not spec:
                raise ValueError(f"规格不存在: {spec_id}")
            return [r["check_cmd"] for r in spec.get("requirements") or []
                    if r.get("check_cmd")]
        finally:
            spec_store.close()

    def _run_case(self, case: Dict[str, Any]) -> Dict[str, Any]:
        cid = str(case.get("id") or "case")
        checks: List[str] = [str(c) for c in case.get("checks") or []]
        spec_id = str(case.get("spec_id") or "")
        t0 = time.monotonic()
        try:
            if spec_id and not checks:
                checks = self._spec_checks(spec_id)
            if not checks:
                return {"id": cid, "passed": False, "error": "用例没有检查命令",
                        "duration_s": round(time.monotonic() - t0, 2), "checks": []}
            results = []
            for cmd in checks:
                r = self._run_cmd(cmd, float(case.get("timeout") or 120))
                results.append({"cmd": cmd, **r})
                if not r["ok"]:
                    break     # 与常见评测一致：首个失败即中止该用例
            passed = all(r["ok"] for r in results) and len(results) == len(checks)
            first_fail = next((r for r in results if not r["ok"]), None)
            return {
                "id": cid,
                "passed": passed,
                "error": (first_fail or {}).get("reason", ""),
                "duration_s": round(time.monotonic() - t0, 2),
                "checks": results,
            }
        except Exception as e:  # noqa: BLE001
            return {"id": cid, "passed": False, "error": f"{type(e).__name__}: {e}",
                    "duration_s": round(time.monotonic() - t0, 2), "checks": []}

    # ---------- 工具 ----------

    def eval_run(self, benchmark: str, case_ids: Any = None) -> Dict[str, Any]:
        try:
            path = self._resolve_benchmark(benchmark)
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
        cases = data.get("cases") or []
        wanted = set(case_ids or [])
        if wanted:
            cases = [c for c in cases if str(c.get("id")) in wanted]
        if not cases:
            return {"success": False, "error": "基准里没有可运行的用例"}

        name = str(data.get("name") or path.stem)
        started = time.time()
        results = [self._run_case(c) for c in cases]
        passed = sum(1 for r in results if r["passed"])
        metrics = {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "success_rate": round(passed / len(results), 3) if results else 0.0,
            "duration_s": round(time.time() - started, 2),
            "benchmark": str(path),
        }
        report_path = str(self._eval_store().write_reports(name, results, started, metrics))
        run = self._eval_store().save(name, started, results, report_path)
        logger.info(f"[eval_lab] 运行完成 {name}: {passed}/{len(results)} → {report_path}")
        return {"success": True, "run_id": run.get("id"), "name": name,
                "metrics": metrics, "report_path": report_path,
                "cases": [{"id": r["id"], "passed": r["passed"],
                           "error": r.get("error", "")} for r in results]}

    def eval_history(self, limit: int = 10) -> Dict[str, Any]:
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 10
        runs = self._eval_store().list(limit)
        return {"success": True, "count": len(runs),
                "runs": [{"id": r["id"], "name": r["name"],
                          "total": r["total"], "passed": r["passed"],
                          "started_at": time.strftime(
                              "%Y-%m-%d %H:%M:%S", time.localtime(r["started_at"])),
                          "report_path": r["report_path"]} for r in runs]}

    def eval_show(self, run_id: str) -> Dict[str, Any]:
        run = self._eval_store().get(run_id)
        if not run:
            return {"success": False, "error": f"运行不存在: {run_id}"}
        return {"success": True, "run": {
            "id": run["id"], "name": run["name"],
            "total": run["total"], "passed": run["passed"],
            "report_path": run["report_path"], "cases": run["cases"]}}

    def eval_compare(self, run_a: str, run_b: str) -> Dict[str, Any]:
        a = self._eval_store().get(run_a)
        b = self._eval_store().get(run_b)
        if not a or not b:
            return {"success": False, "error": "两次运行必须都存在"}
        a_map = {c["id"]: bool(c.get("passed")) for c in a.get("cases") or []}
        b_map = {c["id"]: bool(c.get("passed")) for c in b.get("cases") or []}
        common = [cid for cid in a_map if cid in b_map]
        if not common:
            return {"success": False, "error": "两次运行没有共同用例"}
        wins = sum(1 for cid in common if b_map[cid] and not a_map[cid])
        losses = sum(1 for cid in common if a_map[cid] and not b_map[cid])
        ties = len(common) - wins - losses
        n = wins + losses
        p_value = 1.0 if n == 0 else min(
            1.0, 2 * sum(math.comb(n, k) for k in range(0, min(wins, losses) + 1)) / (2 ** n))
        verdict = ("候选更好" if wins > losses else
                   "候选更差" if losses > wins else "无显著差异")
        return {"success": True,
                "a": {"id": a["id"], "name": a["name"], "passed": a["passed"]},
                "b": {"id": b["id"], "name": b["name"], "passed": b["passed"]},
                "common_cases": len(common), "wins": wins, "losses": losses,
                "ties": ties, "p_value": round(p_value, 4), "verdict": verdict,
                "detail": [{"id": cid, "a": a_map[cid], "b": b_map[cid]}
                           for cid in common]}


def build_eval_lab_domain() -> EvalLabDomain:
    return EvalLabDomain()


__all__ = ["EvalLabDomain", "build_eval_lab_domain"]
