#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面内容感知与操作核心库（纯底层，非视觉）。

用 Win32 API + UIA 提供"桌面上有什么、前台在用什么、哪里可以点"：
- available_apps()       可启动软件（桌面/开始菜单快捷方式，可选注册表已安装）
- foreground_window()    当前前台窗口（标题/进程/pid/矩形）
- list_windows()         全部可见窗口（标题/进程/pid/矩形）
- clickable_controls()   指定窗口内可点击控件（UIA：按钮/链接/菜单项/输入框等 + 坐标）
- click(x, y)            底层点击（SendInput 风格）
- activate_window()      把窗口带到前台

设计原则：全部走系统 API / UIA，不做截图识别；坐标均为屏幕绝对像素（逻辑坐标），
可直接交给 pyautogui / SendInput 使用。
"""

import ctypes
import glob
import os
import re

try:
    import win32gui
    import win32process
    import win32con
except ImportError:  # pragma: no cover
    win32gui = win32process = win32con = None

try:
    import pygetwindow as gw
except ImportError:  # pragma: no cover
    gw = None

try:
    import uiautomation as auto
except ImportError:  # pragma: no cover
    auto = None

import pyautogui


# ---------------------------------------------------------------------------
# 进程名辅助
# ---------------------------------------------------------------------------
def _pid_exe(pid: int) -> str:
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.c_ulong(512)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        ctypes.windll.kernel32.CloseHandle(h)
        return os.path.basename(buf.value) if buf.value else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 1. 可启动软件
# ---------------------------------------------------------------------------
_START_DIRS = [
    ("桌面", r"{USERPROFILE}\Desktop"),
    ("开始菜单(用户)", r"{APPDATA}\Microsoft\Windows\Start Menu\Programs"),
    ("开始菜单(公共)", r"{ProgramData}\Microsoft\Windows\Start Menu\Programs"),
]


def _resolve(path: str) -> str:
    for k in ("USERPROFILE", "APPDATA", "ProgramData"):
        path = path.replace("{" + k + "}", os.environ.get(k, ""))
    return path


def available_apps(include_registry: bool = False) -> dict:
    """列出可启动软件：桌面 + 开始菜单快捷方式（可选注册表已安装应用）。"""
    apps = []
    seen = set()
    for category, d in _START_DIRS:
        base = _resolve(d)
        if not os.path.isdir(base):
            continue
        # 顶层 *.lnk + 一级子目录 *.lnk（覆盖开始菜单分组）
        for pat in (os.path.join(base, "*.lnk"), os.path.join(base, "*", "*.lnk")):
            for lnk in glob.glob(pat):
                name = os.path.splitext(os.path.basename(lnk))[0]
                if not name or name in seen:
                    continue
                seen.add(name)
                apps.append({"name": name, "path": lnk, "category": category,
                             "kind": "shortcut"})
    if include_registry:
        apps.extend(_registry_apps())
    return {"success": True, "count": len(apps),
            "note": "启动用 os.startfile(path)；桌面/开始菜单快捷方式已含",
            "apps": apps[:100]}


def _registry_apps() -> list:
    """注册表已安装应用（DisplayName），供 include_registry 时补充。"""
    import winreg
    out = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, key_path in roots:
        try:
            k = winreg.OpenKey(root, key_path)
        except OSError:
            continue
        try:
            for i in range(winreg.QueryInfoKey(k)[0]):
                try:
                    sk = winreg.EnumKey(k, i)
                    with winreg.OpenKey(k, sk) as sub:
                        name, _ = winreg.QueryValueEx(sub, "DisplayName")
                        if name:
                            out.append({"name": name, "path": sk,
                                        "category": "注册表已安装", "kind": "registry"})
                except OSError:
                    continue
        finally:
            winreg.CloseKey(k)
    return out


# ---------------------------------------------------------------------------
# 2. 前台窗口 / 窗口列表
# ---------------------------------------------------------------------------
def _window_info(hwnd: int) -> dict:
    title = win32gui.GetWindowText(hwnd)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    return {
        "hwnd": hwnd, "title": title, "pid": pid, "process": _pid_exe(pid),
        "left": rect[0], "top": rect[1], "width": rect[2] - rect[0],
        "height": rect[3] - rect[1],
    }


def foreground_window() -> dict:
    """当前前台窗口。"""
    if win32gui is None:
        return {"success": False, "error": "缺 pywin32"}
    try:
        hwnd = win32gui.GetForegroundWindow()
        info = _window_info(hwnd)
        return {"success": True, "window": info,
                "hint": "可用 dsk_clickable(title) 看该窗口可点击控件"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_windows(visible_only: bool = True) -> dict:
    """所有顶层窗口（默认只可见且标题非空）。"""
    if win32gui is None:
        return {"success": False, "error": "缺 pywin32"}
    wins = []

    def _cb(hwnd, _):
        if visible_only and not win32gui.IsWindowVisible(hwnd):
            return True
        info = _window_info(hwnd)
        if info["title"]:
            wins.append(info)
        return True

    win32gui.EnumWindows(_cb, None)
    return {"success": True, "count": len(wins), "windows": wins[:60],
            "hint": "前台窗口可看 dsk_foreground_window"}


# ---------------------------------------------------------------------------
# 3. 可点击控件（UIA）
# ---------------------------------------------------------------------------
_CLICKABLE_TYPES = (
    "ButtonControl", "HyperlinkControl", "MenuItemControl",
    "EditControl", "CheckBoxControl", "RadioButtonControl",
    "ListItemControl", "TabItemControl", "ComboBoxControl",
)


def clickable_controls(title_keyword: str = "", hwnd: int = 0,
                       max_items: int = 80) -> dict:
    """指定窗口内可点击控件列表（UIA，含绝对坐标）。

    title_keyword 或 hwnd 二选一定位窗口；坐标可直接交给 dsk_click。
    """
    if auto is None:
        return {"success": False, "error": "缺 uiautomation"}
    if not hwnd:
        if gw is None:
            return {"success": False, "error": "缺 pygetwindow"}
        wins = gw.getWindowsWithTitle(title_keyword) if title_keyword else []
        if not wins:
            return {"success": False, "error": f"未找到标题含 '{title_keyword}' 的窗口"}
        hwnd = wins[0]._hWnd
    try:
        win = auto.ControlFromHandle(hwnd)
    except Exception as e:
        return {"success": False, "error": f"UIA 定位窗口失败: {e}"}
    if not win:
        return {"success": False, "error": "UIA 无法读取该窗口（可能无 UIA 暴露）"}

    out = []

    def _walk(c, depth=0):
        if depth > 14 or len(out) >= max_items:
            return
        try:
            for ch in c.GetChildren():
                t = ch.ControlTypeName or ""
                if t in _CLICKABLE_TYPES:
                    r = ch.BoundingRectangle
                    if r and r.width() > 0 and r.height() > 0:
                        out.append({
                            "control_type": t.replace("Control", "").lower(),
                            "text": (ch.Name or "").strip(),
                            "x": r.left + r.width() // 2,
                            "y": r.top + r.height() // 2,
                            "w": r.width(), "h": r.height(),
                            "enabled": bool(getattr(ch, "IsEnabled", True)),
                        })
                _walk(ch, depth + 1)
        except Exception:
            return

    _walk(win)
    return {"success": True, "window": (win.Name or title_keyword) or hwnd,
            "count": len(out),
            "hint": "x/y 为屏幕绝对坐标，可用 dsk_click 点击",
            "clickables": out}


# ---------------------------------------------------------------------------
# 4. 底层点击 / 激活
# ---------------------------------------------------------------------------
def click(x: int, y: int, button: str = "left", clicks: int = 1,
          background_hwnd: int = 0) -> dict:
    """点击屏幕绝对坐标。background_hwnd>0 时用 PostMessage 后台点击（不抢焦点）。"""
    try:
        if background_hwnd and win32gui is not None:
            lparam = (y << 16) | (x & 0xFFFF)
            for _ in range(clicks):
                win32gui.PostMessage(background_hwnd, win32con.WM_LBUTTONDOWN,
                                     win32con.MK_LBUTTON, lparam)
                win32gui.PostMessage(background_hwnd, win32con.WM_LBUTTONUP, 0, lparam)
            return {"success": True, "method": "postmessage-bg", "x": x, "y": y}
        pyautogui.click(x, y, clicks=clicks, button=button)
        return {"success": True, "method": "sendinput-foreground", "x": x, "y": y}
    except Exception as e:
        return {"success": False, "error": f"点击失败: {e}"}


def activate_window(title_keyword: str = "", hwnd: int = 0) -> dict:
    """把窗口带到前台（还原最小化）。"""
    try:
        if not hwnd and gw is not None:
            wins = gw.getWindowsWithTitle(title_keyword)
            if not wins:
                return {"success": False, "error": f"未找到 '{title_keyword}'"}
            hwnd = wins[0]._hWnd
        if gw is not None:
            for w in gw.getAllWindows():
                if getattr(w, "_hWnd", None) == hwnd:
                    if w.isMinimized:
                        w.restore()
                    w.activate()
                    break
        return {"success": True, "hwnd": hwnd}
    except Exception as e:
        return {"success": False, "error": f"激活失败: {e}"}
