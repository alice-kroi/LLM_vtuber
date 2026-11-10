# ============================================================
# LLM_v2/domains/launcher/launcher_engine.py — 子进程入口
# ============================================================
# JSON 行式 stdin/stdout 协议（与 SubprocessCapabilityDomain 对齐）
# 支持 --smoke 自检
# 协议增强：支持 __handshake__ 方法，回 {"ready":true,"id":...}（V2 改进）
# ============================================================

from __future__ import annotations

import json
import os
import sys

# 注入项目根到 sys.path（子进程独立启动需要）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

from LLM_v2.domains.launcher.launcher import (
    start_all, launch_mumu, launch_alas, launch_maa, status,
)


class LauncherEngine:
    def __init__(self, config: dict):
        self.config = config or {}
        self._busy = False

    def handle(self, method: str, params: dict):
        p = params or {}
        if method == "__handshake__":
            return {"ready": True, "engine": "launcher", "protocol_version": 2}
        if method == "ping":
            return {"ok": True, "pong": "launcher-engine"}
        if method == "launcher_start_all":
            inst = p.get("instances") or [0, 1]
            click = bool(p.get("click_start", True))
            return start_all(mumu_instances=[int(i) for i in inst], click_start=click)
        if method == "launcher_mumu":
            inst = p.get("instances") or [0, 1]
            return launch_mumu([int(i) for i in inst])
        if method == "launcher_alas":
            return launch_alas()
        if method == "launcher_maa":
            return launch_maa()
        if method == "launcher_status":
            return status()
        if method == "launcher_stop":
            return {"success": True, "stopped": "编排已结束（应用保持运行）"}
        if method == "__shutdown__":
            return {"stopping": True}
        raise ValueError(f"未知方法: {method}")


def _main_loop(engine: LauncherEngine):
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            result = engine.handle(req.get("method"), req.get("params") or {})
            sys.stdout.write(json.dumps({"result": result, "id": req.get("id")},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()
            if req.get("method") == "__shutdown__":
                break
        except Exception as e:
            rid = req.get("id") if "req" in locals() else None
            sys.stdout.write(json.dumps({"error": str(e), "id": rid},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main():
    if "--smoke" in sys.argv:
        sys.stdout.write(json.dumps({"result": {"ok": True, "smoke": True}}) + "\n")
        sys.stdout.flush()
        return
    engine = LauncherEngine({})
    _main_loop(engine)


if __name__ == "__main__":
    main()