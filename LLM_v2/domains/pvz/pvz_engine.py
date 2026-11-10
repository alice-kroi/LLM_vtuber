# ============================================================
# LLM_v2/domains/pvz/pvz_engine.py — pvz 子进程入口
# ============================================================
# 迁自 LLM/pvz/pvz_engine.py；协议保持兼容（不回 id，V2 父进程会自动兼容）
# ============================================================

from __future__ import annotations

import io
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)


class PvzEngine:
    def __init__(self, config: dict):
        self.config = config
        self.mem = None
        self.tasks = []
        self.triggers = {}
        self._seq = 0
        self._window = None

    def _ensure_attached(self):
        if self.mem is not None:
            return self.mem
        # 注：pvz_memory 仍在 LLM/pvz/ 下，Phase G 时一并迁
        from LLM.pvz import pvz_memory
        self.mem = pvz_memory.attach_or_none()
        return self.mem

    def handle(self, method: str, params: dict):
        if method.startswith("pvz_"):
            method = method[len("pvz_"):]
        if method == "__handshake__":
            return {"ready": True, "engine": "pvz", "protocol_version": 2}
        if method == "ping":
            return {"ok": True, "pong": "pvz-engine", "attached": self._ensure_attached() is not None}
        if method == "observe":
            return self._observe()
        if method == "glance":
            return self._glance()
        if method == "queue":
            return self._queue()
        if method == "stop":
            return self._stop(params)
        if method == "do":
            return self._do(params)
        if method == "arm":
            return self._arm(params)
        if method == "__shutdown__":
            return {"stopping": True}
        raise ValueError(f"未知方法: {method}")

    def _observe(self):
        mem = self._ensure_attached()
        if mem is None:
            return {"attached": False, "phase": "game_not_running",
                    "info": "未检测到 PlantsVsZombies.exe 进程，请先启动游戏"}
        from LLM.pvz import pvz_offsets as off
        from LLM.pvz import pvz_reader
        return pvz_reader.read_observe(mem, off)

    def _glance(self):
        hwnd = self._game_window()
        if not hwnd:
            return {"attached": False, "error": "未找到游戏窗口"}
        try:
            from PIL import ImageGrab
            rect = self._window_rect(hwnd)
            img = ImageGrab.grab(bbox=(rect["l"], rect["t"], rect["r"], rect["b"]))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return {"width": img.width, "height": img.height,
                    "png_bytes": len(buf.getvalue())}
        except Exception as e:
            return {"error": str(e)}

    def _queue(self):
        return {"running": [t for t in self.tasks if t["status"] == "running"],
                "waiting": [t for t in self.tasks if t["status"] == "waiting"],
                "parked": [t for t in self.tasks if t["status"] == "parked"],
                "triggers": [v for v in self.triggers.values()],
                "last_result": getattr(self, "_last_result", None)}

    def _do(self, params):
        steps = params.get("steps") or []
        if not steps:
            return {"error": "steps 不能为空"}
        mode = params.get("queue", "replace")
        tid = params.get("task_id") or f"t{self._next_seq()}"
        task = {"id": tid, "steps": steps, "status": "waiting", "mode": mode,
                "accepted": True}
        if mode == "replace":
            self.tasks = [t for t in self.tasks if t["status"] == "running"]
        self.tasks.append(task)
        return {"accepted": True, "task_id": tid, "queue": self._queue_snapshot()}

    def _arm(self, params):
        tid = f"g{self._next_seq()}"
        self.triggers[tid] = {"id": tid, "when": params.get("when"),
                              "steps": params.get("steps"),
                              "expires_in_ms": params.get("expires_in_ms"),
                              "max_firings": params.get("max_firings", 1),
                              "fired": 0}
        return {"accepted": True, "trigger_id": tid}

    def _stop(self, params):
        if params.get("all"):
            self.tasks.clear()
            self.triggers.clear()
            return {"stopped": "all"}
        task_id = params.get("task_id")
        trigger_id = params.get("trigger_id")
        removed = []
        if task_id:
            self.tasks = [t for t in self.tasks if not self._match(t, task_id)]
            removed.append(task_id)
        if trigger_id and trigger_id in self.triggers:
            self.triggers.pop(trigger_id)
            removed.append(trigger_id)
        return {"stopped": removed or ["none"]}

    @staticmethod
    def _match(task, tid):
        return task["id"] == tid

    def _queue_snapshot(self):
        return {"running": [t["id"] for t in self.tasks if t["status"] == "running"],
                "waiting": [t["id"] for t in self.tasks if t["status"] == "waiting"]}

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _game_window(self):
        from LLM.pvz import pvz_input
        if self._window:
            return self._window
        self._window = pvz_input.find_game_window()
        return self._window

    @staticmethod
    def _window_rect(hwnd):
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        return {"l": r.left, "t": r.top, "r": r.right, "b": r.bottom}


def _main_loop(engine: PvzEngine):
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            method = req.get("method")
            params = req.get("params") or {}
            result = engine.handle(method, params)
            # 注：pvz 引擎不回 id（V1 行为），V2 父进程 SubprocessCapabilityDomain
            # 已实现"宽容模式"兼容此协议（首条合法 JSON 即采纳）
            sys.stdout.write(json.dumps({"result": result}, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            if method == "__shutdown__":
                break
        except Exception as e:
            sys.stdout.write(json.dumps({"error": str(e)}, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main():
    config = {}
    if "--smoke" in sys.argv:
        sys.stdout.write(json.dumps({"result": {"ok": True, "smoke": True}}) + "\n")
        sys.stdout.flush()
        return
    engine = PvzEngine(config)
    _main_loop(engine)


if __name__ == "__main__":
    main()