#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面宠物核心库：启动现成桌宠（Bongo Cat Mver）+ 悬浮输入框。

- pet_start()：Popen 启动 F:\\爱丽\\A-爱莉-带表情版\\Bongo Cat Mver.exe（桌宠本体）
              + Popen 启动 pet_input.py（悬浮输入框，输入走主播脑 inbox）
- pet_stop()：结束桌宠与输入框进程
- pet_status()：进程存活状态
"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional

# 桌宠程序路径（用户现有额外软件）
PET_EXE = r"F:\爱丽\A-爱莉-带表情版\Bongo Cat Mver.exe"
PET_DIR = os.path.dirname(PET_EXE)
WEBUI_URL = "http://127.0.0.1:8081"   # 主程序 webui（输入框经 /api/send 投 inbox）
DEFAULT_WINDOW_SIZE = (400, 400)      # 桌宠窗口尺寸；太小会把程序自绘的按键气泡裁掉

_CONFIG_PATH = os.path.join(PET_DIR, "config.json")

_input_proc: Optional[subprocess.Popen] = None
_pet_proc: Optional[subprocess.Popen] = None


def _load_window_size() -> tuple:
    """从 LLM/skill_worlds.json 的 desktop_pet.window_size 读桌宠窗口尺寸（可调）。"""
    try:
        cfg = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "skill_worlds.json")
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        v = data.get("desktop_pet", {}).get("window_size")
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return (int(v[0]), int(v[1]))
    except Exception:
        pass
    return DEFAULT_WINDOW_SIZE


def _patch_config_window_size(size: tuple) -> Dict:
    """把桌宠 config.json 的 window_size 文本替换为指定尺寸（该文件含注释，非标准 JSON）。"""
    if not os.path.exists(_CONFIG_PATH):
        return {"note": "config.json 不存在"}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        pat = re.compile(r'("window_size"\s*:\s*\[\s*)\d+(\s*,\s*)\d+(\s*\])',
                         re.S)
        new, n = pat.subn(rf"\g<1>{size[0]}\g<2>{size[1]}\g<3>", text)
        if n == 0:
            return {"note": "未找到 window_size 字段", "size": list(size)}
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(new)
        return {"patched": True, "window_size": list(size), "count": n}
    except Exception as e:
        return {"patched": False, "error": str(e)}


def _input_script() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "pet_input.py")


def _move_pet_to_bottom_right(timeout_s: float = 6.0) -> Dict:
    """桌宠窗口就绪后移到屏幕右下角（避开任务栏）。"""
    import ctypes
    import time
    try:
        import pygetwindow as gw
    except Exception:
        return {"note": "缺 pygetwindow，跳过移动"}
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    taskbar_reserve = 60
    margin = 10
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        wins = [w for w in gw.getAllWindows() if "Bongo" in (w.title or "")]
        if wins:
            w = wins[0]
            x = max(0, sw - w.width - margin)
            y = max(0, sh - w.height - taskbar_reserve)
            w.moveTo(x, y)
            return {"moved_to": (x, y), "size": (w.width, w.height)}
        time.sleep(0.2)
    return {"note": "桌宠窗口未在限时内出现"}


def pet_start(webui_url: str = WEBUI_URL, with_input: bool = True) -> Dict:
    """启动桌宠 + 输入框（幂等：已运行则跳过）。

    桌宠窗口尺寸由 skill_worlds.json 的 desktop_pet.window_size 控制
    （缺省 400x400）；启动前会把该尺寸写入桌宠 config.json，
    启动后自动把窗口移到屏幕右下角。
    """
    global _pet_proc, _input_proc
    result = {"success": True}
    # 0) 桌宠窗口尺寸（可调；太小会把按键气泡裁掉）
    size = _load_window_size()
    result["window_size"] = _patch_config_window_size(size)
    # 1) 桌宠本体
    if _pet_proc is None or _pet_proc.poll() is not None:
        if not os.path.exists(PET_EXE):
            return {"success": False, "error": f"桌宠程序不存在: {PET_EXE}"}
        try:
            _pet_proc = subprocess.Popen(
                [PET_EXE], cwd=PET_DIR,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            result["pet"] = {"launched": PET_EXE,
                             "corner": _move_pet_to_bottom_right()}
        except Exception as e:
            return {"success": False, "error": f"桌宠启动失败: {e}"}
    else:
        result["pet"] = {"note": "桌宠已在运行"}
    # 2) 悬浮输入框（独立 tkinter 进程）
    if with_input:
        if _input_proc is None or _input_proc.poll() is not None:
            try:
                _input_proc = subprocess.Popen(
                    [sys.executable, _input_script(), "--url", webui_url],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                result["input"] = {"launched": "pet_input.py"}
            except Exception as e:
                result["input"] = {"success": False, "error": f"输入框启动失败: {e}"}
        else:
            result["input"] = {"note": "输入框已在运行"}
    return result


def pet_stop() -> Dict:
    """结束桌宠与输入框进程。"""
    global _pet_proc, _input_proc
    out = {"success": True}
    for name, proc in (("pet", _pet_proc), ("input", _input_proc)):
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
                out[name] = "已停止"
            except Exception as e:
                out[name] = f"停止失败: {e}"
        else:
            out[name] = "未运行"
    _pet_proc = _input_proc = None
    return out


def pet_status() -> Dict:
    return {
        "success": True,
        "pet_running": bool(_pet_proc and _pet_proc.poll() is None),
        "input_running": bool(_input_proc and _input_proc.poll() is None),
        "pet_exe": PET_EXE,
        "webui_url": WEBUI_URL,
    }
