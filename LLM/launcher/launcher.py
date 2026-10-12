#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""launcher 核心流程库：MuMu 双实例 + ALAS/MAA 的底层启动与"点启动"。

原则（对齐项目电脑操作策略）：
- 启动 exe 用系统级 subprocess.Popen + 指定 cwd，不依赖键鼠，最稳。
- 打开模拟器实例用 MuMuManager 原生 CLI（底层，非视觉）。
- 点应用内"启动/开始"按钮用 uiautomation 按控件定位点击（底层 Windows 自动化），
  而非截图视觉识别；无该控件则回退到 pygetwindow 激活窗口。
- 所有阻塞型等待用有界轮询(deadline)，避免卡死。
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Dict, Any, List, Optional, Tuple

# ---- 运行时常量（来自本机探测/用户指定）----
MUMU_MANAGER = r"F:\MuMu Player 12\nx_main\MuMuManager.exe"
ALAS_EXE = r"F:\download\AzurLaneAutoScript\alas.exe"
ALAS_DIR = os.path.dirname(ALAS_EXE)
MAA_EXE = r"F:\maa\maa.exe"
MAA_DIR = os.path.dirname(MAA_EXE)

# ---- 控件校准（坐标法回退；窗口内相对偏移 0-1，运行时换算成屏幕绝对坐标）----
# ALAS: Electron webview，UIA 读不到 DOM 按钮，须用坐标点击。
# 校准来源：o(625,74)1(280,880) 窗口截图上，"调度器"面板蓝色"启动"按钮中心 = 窗口内像素 (527,81)。
ALAS_MAIN_TITLE = "Alas"                       # ALAS 主界面窗口标题
ALAS_START_REL: Tuple[float, float] = (0.41, 0.092)  # 启动按钮窗口内相对位置 (fx, fy)
# MAA: 原生 Qt 界面，uiautomation 读不到内部按钮，同样须用坐标点击。
# 校准来源：MAA 主窗 "MAA - v6.18.0*MuMu 模拟器(emulator-xxxx)- 官服" 待机态，
#   底部 "Link Start!" 开始按钮中心 = 窗口内相对 (0.185, 0.89)。
MAA_MAIN_TITLE = "MAA -"                       # MAA 主控制窗标题前缀
MAA_START_REL: Tuple[float, float] = (0.185, 0.89)  # 开始(Link Start)按钮窗口内相对位置


def _popen(exe: str, cwd: str) -> Dict[str, Any]:
    try:
        # 断开子进程 stdio：避免孙进程把输出写进本引擎的协议管道，污染 JSON 行协议
        subprocess.Popen([exe], cwd=cwd or os.path.dirname(exe),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return {"success": True, "launched": exe}
    except Exception as e:
        return {"success": False, "error": f"启动失败 {exe}: {e}"}


# ---------- MuMu 模拟器实例 ----------
def launch_mumu(instances: List[int] = (0, 1), wait_sec: float = 15.0) -> Dict[str, Any]:
    """用 MuMuManager 启动指定编号的模拟器实例（底层 CLI）。"""
    if not os.path.exists(MUMU_MANAGER):
        return {"success": False, "error": f"MuMuManager 不存在: {MUMU_MANAGER}"}
    vm = ",".join(str(i) for i in instances)
    try:
        subprocess.run([MUMU_MANAGER, "control", "-v", vm, "launch"],
                       timeout=30, capture_output=True, text=True)
    except Exception as e:
        return {"success": False, "error": f"MuMuManager launch 失败: {e}"}
    time.sleep(wait_sec)
    return {"success": True, "vmindex": instances,
            "after_wait_sec": wait_sec, "windows": present_windows(
                ["MuMu Player", "MuMuNx"])}


# ---------- ALAS / MAA ----------
def launch_alas() -> Dict[str, Any]:
    return _popen(ALAS_EXE, ALAS_DIR)


def launch_maa() -> Dict[str, Any]:
    return _popen(MAA_EXE, MAA_DIR)


# ---------- 窗口与点击（底层） ----------
def _get_window(title_keyword: str):
    try:
        import pygetwindow as gw
        for w in gw.getWindowsWithTitle(title_keyword):
            if w.title:
                return w
    except Exception:
        return None
    return None


def activate_window(title_keyword: str) -> bool:
    w = _get_window(title_keyword)
    if not w:
        return False
    try:
        if w.isMinimized:
            w.restore()
        if not w.isActive:
            w.activate()
        return True
    except Exception:
        return False


def present_windows(title_keywords: List[str]) -> List[str]:
    """返回当前存在窗口里标题命中关键词之一的列表（用于状态上报）。"""
    try:
        import pygetwindow as gw
        wsz = gw.getAllWindows()
    except Exception:
        return []
    hits = []
    for w in wsz:
        if w.title and any(k.lower() in w.title.lower() for k in title_keywords):
            hits.append(w.title)
    return hits


def click_button_in_window(title_keywords: List[str], button_names: List[str],
                           wait_for_title: Optional[List[str]] = None,
                           timeout_sec: float = 45.0) -> Dict[str, Any]:
    """等待目标窗口出现后，用 uiautomation 定位并点击按钮。

    wait_for_title: 需等到这些标题之一出现才视为"主界面就绪"（如 ALAS 先出更新器再出主界面）。
    button_names: 命中任一即视为目标启动按钮（如 ["启动","开始"]）。
    若找不到控件则回退为"窗口已激活"（供人工/后续校准）。
    """
    deadline = time.time() + timeout_sec
    win_title = None
    while time.time() < deadline:
        cur = present_windows(wait_for_title or title_keywords)
        if cur:
            win_title = cur[0]
            break
        time.sleep(0.6)
    if not win_title:
        return {"success": False, "error": f"超时未见目标窗口: {wait_for_title or title_keywords}"}

    activate_window(win_title)
    time.sleep(0.8)
    try:
        import uiautomation as uia
    except Exception as e:
        return {"success": False, "error": f"uiautomation 未安装: {e}",
                "window": win_title, "need_manual_calibration": True}

    # 检索该窗口内控件树中的按钮
    attempted = []
    try:
        top = uia.WindowControl(searchDepth=1, SubName=win_title[:40])
        found = _search_button(top, button_names, attempted)
        if found is not None:
            found.Click(simulateMove=False)
            return {"success": True, "window": win_title,
                    "clicked_button": found.Name, "searched": attempted[:8]}
    except Exception as e:
        return {"success": False, "error": f"点击失败: {e}", "window": win_title,
                "searched": attempted[:8], "need_manual_calibration": True}

    return {"success": False, "window": win_title,
            "error": "未找到启动按钮控件", "searched": attempted[:8],
            "need_manual_calibration": True}


def click_window_rel(title_keywords: List[str], rel: Tuple[float, float],
                     timeout_sec: float = 30.0) -> Dict[str, Any]:
    """坐标法：等目标窗口出现，按其窗口内相对偏移(rel)换算屏幕绝对坐标并点击。

    Electron/webview 类应用（如 ALAS）UIA 读不到 DOM 按钮，用户选择用坐标点击；
    这是运行时底层的点击，不是视觉识别。返回实际屏幕坐标以便复核。
    """
    deadline = time.time() + timeout_sec
    win = None
    while time.time() < deadline:
        w = _first_window(title_keywords)
        if w is not None:
            win = w
            break
        time.sleep(0.6)
    if win is None:
        return {"success": False, "error": f"超时未见窗口: {title_keywords}"}
    abs_x, abs_y = win.left + int(win.width * rel[0]), win.top + int(win.height * rel[1])
    try:
        import pyautogui
        if win.isMinimized:
            win.restore()
        if not win.isActive:
            win.activate()
        time.sleep(0.5)
        pyautogui.click(abs_x, abs_y)
        return {"success": True, "window": win.title, "relative": rel,
                "abs_xy": (abs_x, abs_y), "method": "coordinate"}
    except Exception as e:
        return {"success": False, "error": f"坐标点击失败: {e}", "window": win.title,
                "need_manual_calibration": True}


def _first_window(title_keywords: List[str]):
    try:
        import pygetwindow as gw
        for w in gw.getAllWindows():
            t = w.title or ""
            if t and any(k.lower() in t.lower() for k in title_keywords):
                return w
    except Exception:
        return None
    return None


def _search_button(root, button_names: List[str], attempted: List[str]):
    """在 root 控件子树里深度优先搜 Name 命中 button_names 的按钮控件。"""
    import uiautomation as uia
    if root is None:
        return None
    try:
        if root.ControlType in (uia.ControlType.ButtonControl,
                                uia.ControlType.MenuItemControl,
                                uia.ControlType.TabItemControl,
                                uia.ControlType.CheckBoxControl):
            nm = (root.Name or "").strip()
            if nm:
                attempted.append(nm)
                if any(b in nm for b in button_names):
                    return root
        for child in root.GetChildren():
            got = _search_button(child, button_names, attempted)
            if got is not None:
                return got
    except Exception:
        pass
    return None


# ---------- 总览 ----------
def start_all(mumu_instances: List[int] = (0, 1), click_start: bool = True,
              alas_wait_titles=("Alas", "Azur Lane"), maa_wait_titles=("MAA",)) -> Dict[str, Any]:
    """编排：MuMu→ALAS→MAA，并在各自主界面点启动。"""
    report: Dict[str, Any] = {}
    # 1) MuMu
    report["mumu"] = launch_mumu(mumu_instances)

    # 2) ALAS：先启动，等主界面，点启动（Electron webview UIA 读不到按钮）
    report["alas_launch"] = launch_alas()
    report["alas_start"] = None
    if click_start:
        try:
            report["alas_start"] = click_button_in_window(
                alas_wait_titles, ["启动", "开始", "运行"], wait_for_title=alas_wait_titles)
        except Exception as e:
            report["alas_start"] = {"success": False, "error": str(e)}
        r = report["alas_start"] or {}
        if not r.get("success") or r.get("need_manual_calibration"):
            # 网页 CDP（Playwright 驱动 Edge 打开 pywebio 页面，DOM 原生点击，真控件优先）
            try:
                from LLM.launcher.alas_web import alas_web_click
                report["alas_start_web"] = alas_web_click("启动")
            except Exception as e:
                report["alas_start_web"] = {"success": False, "error": str(e)}
            wr = report["alas_start_web"] or {}
            # 网页 CDP 失败 → 坐标法兜底
            if not wr.get("success"):
                report["alas_start_coord"] = click_window_rel(
                    [ALAS_MAIN_TITLE], ALAS_START_REL)

    # 3) MAA：启动，等主界面，点开始
    report["maa_launch"] = launch_maa()
    report["maa_start"] = None
    if click_start:
        # MAA 为原生 Qt：主启动按钮 "Link Start!" 是模板按钮，UIA 读到 Name 为空，
        # 反而会误命中 "ADB 连接失败时尝试启动模拟器" 等引导按钮 → 优先坐标法。
        try:
            report["maa_start_coord"] = click_window_rel(
                [MAA_MAIN_TITLE], MAA_START_REL)
        except Exception as e:
            report["maa_start_coord"] = {"success": False, "error": str(e)}
        cr = report["maa_start_coord"] or {}
        if not cr.get("success"):
            # 坐标法失败（窗口未就绪等）→ UIA 搜索兜底
            try:
                report["maa_start"] = click_button_in_window(
                    ["MAA -"], ["开始", "启动", "运行", "Link Start"],
                    wait_for_title=["MAA -"])
            except Exception as e:
                report["maa_start"] = {"success": False, "error": str(e)}

    report["final_windows"] = present_windows(["MuMu", "Alas", "Azur Lane", "MAA"])
    return report


def status() -> Dict[str, Any]:
    return {"present_windows": present_windows(
        ["MuMuNx", "MuMu Player", "Alas", "Azur Lane", "MAA"])}