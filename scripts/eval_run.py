#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测台 CLI：跑基准 / 看历史 / 配对比较

用法：
    python scripts/eval_run.py smoke
    python scripts/eval_run.py smoke --cases sql-demo-guarded
    python scripts/eval_run.py --history 5
    python scripts/eval_run.py --compare <run_a> <run_b>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from LLM_v2.domains.eval_lab.eval_lab import build_eval_lab_domain  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="评测台")
    parser.add_argument("benchmark", nargs="?", default="", help="基准名或路径")
    parser.add_argument("--cases", nargs="*", default=[], help="只跑指定用例 id")
    parser.add_argument("--history", type=int, default=0, help="列出最近 N 次运行")
    parser.add_argument("--compare", nargs=2, default=[], metavar=("A", "B"),
                        help="配对比较两次运行（A=基线，B=候选）")
    args = parser.parse_args()

    dom = build_eval_lab_domain()

    if args.compare:
        result = dom.eval_compare(args.compare[0], args.compare[1])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1

    if args.history:
        result = dom.eval_history(args.history)
        for run in result.get("runs") or []:
            print(f"{run['id']}  {run['started_at']}  {run['name']}  "
                  f"{run['passed']}/{run['total']}")
        return 0

    if not args.benchmark:
        parser.print_help()
        return 2

    result = dom.eval_run(args.benchmark, args.cases or None)
    if not result.get("success"):
        print("失败:", result.get("error"))
        return 1
    metrics = result["metrics"]
    print(f"run_id={result['run_id']}  {result['name']}")
    print(f"通过 {metrics['passed']}/{metrics['total']}  "
          f"成功率 {metrics['success_rate']}  耗时 {metrics['duration_s']}s")
    for case in result["cases"]:
        mark = "PASS" if case["passed"] else "FAIL"
        print(f"  [{mark}] {case['id']} {case['error'][:90]}")
    print("报告:", result["report_path"])
    return 0 if metrics["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
