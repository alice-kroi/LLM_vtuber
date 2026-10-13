#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""computer_use 域核心库：电脑常规操作（纯底层，非视觉）。

覆盖能力（对齐 computer_use_research.md 阶段 A）：
- R1 常规操作：查找/打开文件、打开文件夹、启动软件、列目录/应用/进程、
  输入文本、按键、剪贴板
- R4 遮挡处理：窗口列表、激活/最小化/还原/显示桌面（先清理遮挡再操作）

实现全部走系统 API（Win32 / Shell / UIA），不做截图识别。
"""

import os
import subprocess
import time

try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except ImportError:  # pragma: no cover
    _HAS_PYAUTOGUI = False

try:
    import pyperclip
    _HAS_PYPERCLIP = True
except ImportError:  # pragma: no cover
    _HAS_PYPERCLIP = False

# 复用既有底层实现（避免重复造轮子）
try:
    from LLM.desktop_agent.desktop_agent import (
        available_apps as _available_apps,
        list_windows as _list_windows,
        activate_window as _activate_window,
    )
    _HAS_DESKTOP_AGENT = True
except Exception:  # pragma: no cover
    _HAS_DESKTOP_AGENT = False

try:
    from tool.computer_use import (
        _open_path_sync as _shell_open,
        _find_file_sync as _find_file,
        _list_directory_sync as _list_directory,
        _minimize_window_sync as _minimize,
        _show_desktop_sync as _show_desktop,
        _os_check_sync as _check_sync,
    )
    _HAS_TOOL_CU = True
except Exception:  # pragma: no cover
    _HAS_TOOL_CU = False


# ---------------------------------------------------------------------------
# R1 常规操作
# ---------------------------------------------------------------------------

def find_file(keyword: str, root: str = "C:\\", depth: int = 6, limit: int = 20) -> dict:
    """按文件名关键词递归搜索（跳过 node_modules/.git/Windows 等）。"""
    if not _HAS_TOOL_CU:
        return {"success": False, "error": "tool.computer_use 不可用"}
    return _find_file(keyword, root, int(depth), int(limit))


def open_folder(target: str) -> dict:
    """打开文件夹/盘符/我的电脑（系统 Shell，最稳）。"""
    if not _HAS_TOOL_CU:
        return {"success": False, "error": "tool.computer_use 不可用"}
    return _shell_open(target)


def open_file(path: str) -> dict:
    """用系统默认程序打开文件。"""
    target = (path or "").strip()
    if not target:
        return {"success": False, "error": "未指定文件路径"}
    try:
        os.startfile(target)
        return {"success": True, "opened": target, "kind": "file"}
    except OSError as e:
        return {"success": False, "error": f"打开失败: {e}"}


def launch_app(app: str) -> dict:
    """启动软件：exe 路径 → 系统 Shell（应用名/快捷方式名）→ startfile 兜底。"""
    target = (app or "").strip()
    if not target:
        return {"success": False, "error": "未指定应用"}
    if os.path.isfile(target) and target.lower().endswith((".exe", ".bat", ".cmd", ".lnk")):
        try:
            subprocess.Popen([target], cwd=os.path.dirname(target) or None,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return {"success": True, "launched": target, "method": "exe-path"}
        except Exception as e:
            return {"success": False, "error": f"启动失败: {e}"}
    if _HAS_TOOL_CU:
        r = _shell_open(target)
        if r.get("success"):
            return {"success": True, "launched": target, "method": "shell"}
        return {"success": False, "error": r.get("error", f"无法启动 {target}")}
    try:
        os.startfile(target)
        return {"success": True, "launched": target, "method": "startfile"}
    except OSError as e:
        return {"success": False, "error": f"启动失败: {e}"}


def list_apps(include_registry: bool = False) -> dict:
    """列出可启动软件（桌面/开始菜单快捷方式）。"""
    if not _HAS_DESKTOP_AGENT:
        return {"success": False, "error": "desktop_agent 不可用"}
    return _available_apps(include_registry=bool(include_registry))


def list_directory(path: str) -> dict:
    """读取目录下的文件夹/文件列表。"""
    if not _HAS_TOOL_CU:
        return {"success": False, "error": "tool.computer_use 不可用"}
    return _list_directory(path)


def type_text(text: str) -> dict:
    """在当前聚焦输入框输入文本（含中文时走剪贴板 ctrl+v）。"""
    if not _HAS_PYAUTOGUI:
        return {"success": False, "error": "pyautogui 未安装"}
    try:
        if any(ord(ch) > 127 for ch in text):
            if not _HAS_PYPERCLIP:
                return {"success": False, "error": "中文输入需要 pyperclip"}
            pyperclip.copy(text)
            time.sleep(0.15)
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(text)
        return {"success": True, "typed": text[:60]}
    except Exception as e:
        return {"success": False, "error": f"输入失败: {e}"}


def press_key(hotkey: str) -> dict:
    """按组合键/按键，如 ctrl+s、alt+tab、enter。"""
    if not _HAS_PYAUTOGUI:
        return {"success": False, "error": "pyautogui 未安装"}
    try:
        keys = [k.strip() for k in (hotkey or "").split("+") if k.strip()]
        if not keys:
            return {"success": False, "error": "未指定按键"}
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        return {"success": True, "hotkey": hotkey}
    except Exception as e:
        return {"success": False, "error": f"按键失败: {e}"}


def click_xy(x: int, y: int, clicks: int = 1, button: str = "left") -> dict:
    """在屏幕绝对坐标点击。"""
    if not _HAS_PYAUTOGUI:
        return {"success": False, "error": "pyautogui 未安装"}
    try:
        pyautogui.click(int(x), int(y), clicks=int(clicks), button=button)
        return {"success": True, "clicked": (int(x), int(y))}
    except Exception as e:
        return {"success": False, "error": f"点击失败: {e}"}


def clipboard_get() -> dict:
    """读剪贴板文本。"""
    if not _HAS_PYPERCLIP:
        return {"success": False, "error": "缺 pyperclip"}
    try:
        return {"success": True, "clipboard": pyperclip.paste()[:2000]}
    except Exception as e:
        return {"success": False, "error": f"读剪贴板失败: {e}"}


def clipboard_set(text: str) -> dict:
    """写剪贴板文本。"""
    if not _HAS_PYPERCLIP:
        return {"success": False, "error": "缺 pyperclip"}
    try:
        pyperclip.copy(text or "")
        return {"success": True, "set": (text or "")[:60]}
    except Exception as e:
        return {"success": False, "error": f"写剪贴板失败: {e}"}


def list_processes(keyword: str = "") -> dict:
    """列出进程（可选按名称关键词过滤）；用 tasklist 规避 psutil 依赖。"""
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=20)
        rows = []
        kw = (keyword or "").strip().lower()
        for line in (out.stdout or "").splitlines():
            parts = line.strip().strip('"').split('","')
            if len(parts) < 2:
                continue
            name = parts[0].strip('"')
            pid = parts[1].strip('"')
            if kw and kw not in name.lower():
                continue
            rows.append({"name": name, "pid": int(pid) if pid.isdigit() else pid})
            if len(rows) >= 60:
                break
        return {"success": True, "count": len(rows), "processes": rows,
                "note": "窗口态进程可看 cu_list_windows"}
    except Exception as e:
        return {"success": False, "error": f"列进程失败: {e}"}


# ---------------------------------------------------------------------------
# R4 遮挡处理：窗口盘点 / 清理
# ---------------------------------------------------------------------------

def list_windows() -> dict:
    """列出全部可见窗口（标题/进程/pid/矩形），先盘点桌面遮挡。"""
    if not _HAS_DESKTOP_AGENT:
        return {"success": False, "error": "desktop_agent 不可用"}
    return _list_windows(visible_only=True)


def foreground(title_keyword: str) -> dict:
    """把指定窗口置为前台（还原最小化）。"""
    if not _HAS_DESKTOP_AGENT:
        return {"success": False, "error": "desktop_agent 不可用"}
    return _activate_window(title_keyword=title_keyword)


def minimize_window(title_keyword: str) -> dict:
    """最小化指定窗口，用于清理遮挡目标的操作面。"""
    if not _HAS_TOOL_CU:
        return {"success": False, "error": "tool.computer_use 不可用"}
    return _minimize(title_keyword)


def restore_window(title_keyword: str) -> dict:
    """还原最小化窗口（激活即还原并置前）。"""
    if not _HAS_DESKTOP_AGENT:
        return {"success": False, "error": "desktop_agent 不可用"}
    return _activate_window(title_keyword=title_keyword)


def show_desktop() -> dict:
    """一键显示桌面（Win+D），清理全部遮挡。"""
    if not _HAS_TOOL_CU:
        return {"success": False, "error": "tool.computer_use 不可用"}
    return _show_desktop()


# ---------------------------------------------------------------------------
# 验证闭环
# ---------------------------------------------------------------------------

def check(check_type: str, target: str, expect: str = "true") -> dict:
    """断言式核验（对齐可提 mc_check）：window/file/dir/element/screen。"""
    if not _HAS_TOOL_CU:
        return {"success": False, "error": "tool.computer_use 不可用"}
    expect_bool = str(expect or "true").strip().lower() not in ("false", "0", "no", "否")
    return _check_sync((check_type or "").strip().lower(), (target or "").strip(), expect_bool)
