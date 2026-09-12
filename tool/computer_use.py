#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 操作电脑（Computer Use）工具模块

让 LLM 能够操作系统级键鼠与窗口，实现"读懂界面 → 决策 → 执行"的闭环：
- 窗口级：列窗口 / 激活窗口
- 键鼠级：像素点击 / 文本输入 / 快捷键 / 滚动（pyautogui + pygetwindow）
- 视觉辅助：GUI Grounding（豆包 Seed 2.x）定位目标坐标 → 点击 → 校验闭环

设计原则：
- 所有 OS 同步操作丢到线程池执行，不阻塞事件循环
- 坐标统一相对 0-1000（豆包官方协议），映射回真实屏幕像素
- 视觉点击含"动作→截图→校验→修正"闭环（限次），提升落点可用性
- 可选 UI Automation（uiautomation 库）读标准控件真实坐标（最准），未安装则优雅降级
"""

import asyncio
import base64
import io
import json
import logging
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except ImportError:
    _HAS_PYAUTOGUI = False

try:
    import pygetwindow as gw
    _HAS_PYGETWINDOW = True
except ImportError:
    _HAS_PYGETWINDOW = False

# 豆包 GUI Grounding 专用模型（需在方舟开通）
GUI_MODEL = os.getenv("DOUBAO_GUI_MODEL", "doubao-seed-evolving")
GUI_API_KEY = os.getenv("Doubao_API_KEY") or os.getenv("Doubao_API_KEY")
GUI_API_URL = os.getenv("Doubao_API_URL") or "https://ark.cn-beijing.volces.com/api/v3"


def _in_thread(func, *args, **kw):
    """同步函数转到线程执行，避免阻塞事件循环"""
    return asyncio.to_thread(func, *args, **kw)


# ============================================================
# 窗口级操作
# ============================================================

def _list_windows_sync() -> List[Dict[str, Any]]:
    if not _HAS_PYGETWINDOW:
        return []
    out = []
    for w in gw.getAllWindows():
        if w.title:
            out.append({"title": w.title, "left": w.left, "top": w.top,
                        "width": w.width, "height": w.height})
    return out


async def os_list_windows() -> Dict[str, Any]:
    """列出可见窗口"""
    try:
        wins = await _in_thread(_list_windows_sync)
        return {"success": True, "count": len(wins), "windows": wins[:30]}
    except Exception as e:
        return {"success": False, "error": f"列窗口失败: {e}"}


def _activate_window_sync(title_keyword: str) -> Dict[str, Any]:
    if not _HAS_PYAUTOGUI or not _HAS_PYGETWINDOW:
        return {"success": False, "error": "依赖未安装: pyautogui/pygetwindow"}
    wins = gw.getWindowsWithTitle(title_keyword)
    if not wins:
        return {"success": False, "error": f"未找到标题含 '{title_keyword}' 的窗口"}
    win = wins[0]
    try:
        if not win.isActive:
            win.activate()
        if win.isMinimized:
            win.restore()
        return {"success": True, "title": win.title, "left": win.left, "top": win.top,
                "width": win.width, "height": win.height}
    except Exception as e:
        # 某些系统 activate 失败，退化为点击标题栏
        try:
            import time
            cx, cy = win.left + win.width // 2, win.top + 10
            pyautogui.click(cx, cy)
            time.sleep(0.3)
            return {"success": True, "title": win.title, "note": "via-click-titlebar"}
        except Exception as e2:
            return {"success": False, "error": f"激活窗口失败: {e} / {e2}"}


async def os_activate_window(title_keyword: str) -> Dict[str, Any]:
    """激活指定窗口（按标题关键词）"""
    return await _in_thread(_activate_window_sync, title_keyword)


def _minimize_window_sync(title_keyword: str) -> Dict[str, Any]:
    import pygetwindow as _gw
    wins = _gw.getWindowsWithTitle(title_keyword)
    if not wins:
        return {"success": False, "error": f"未找到标题含 '{title_keyword}' 的窗口"}
    out = []
    for w in wins:
        try:
            if w.title and not w.isMinimized:
                w.minimize()
                out.append(w.title)
        except Exception:
            continue
    if not out:
        return {"success": True, "note": "窗口已是最小化状态", "minimized": []}
    return {"success": True, "minimized": out}


async def os_minimize_window(title_keyword: str) -> Dict[str, Any]:
    """最小化指定窗口（按标题关键词），用于清理遮挡目标的操作面"""
    return await _in_thread(_minimize_window_sync, title_keyword)


def _show_desktop_sync() -> Dict[str, Any]:
    # 模拟 Win+D 显示桌面：最小化所有前台窗口，露出桌面/图标
    import subprocess
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)      # Win 按下
        ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)      # D 按下
        ctypes.windll.user32.keybd_event(0x44, 0, 0x0002, 0) # D 抬起
        ctypes.windll.user32.keybd_event(0x5B, 0, 0x0002, 0) # Win 抬起
        return {"success": True, "note": "已显示桌面（Win+D），全部窗口最小化"}
    except Exception as e:
        # 兜底：用 explorer 打开桌面文件夹
        try:
            subprocess.Popen(["explorer.exe", "shell:Desktop"])
            return {"success": True, "note": "已打开桌面文件夹作为兜底"}
        except Exception as e2:
            return {"success": False, "error": f"显示桌面失败: {e} / {e2}"}


async def os_show_desktop() -> Dict[str, Any]:
    """显示桌面（一键最小化所有窗口露出桌面），清理全部遮挡"""
    return await _in_thread(_show_desktop_sync)


# ============================================================
# 键鼠级操作
# ============================================================

async def os_click_xy(x: int, y: int, clicks: int = 1, button: str = "left") -> Dict[str, Any]:
    """在屏幕绝对像素坐标 (x,y) 点击"""
    if not _HAS_PYAUTOGUI:
        return {"success": False, "error": "pyautogui 未安装"}
    try:
        await _in_thread(pyautogui.click, x, y, clicks=clicks, button=button)
        return {"success": True, "clicked": (x, y)}
    except Exception as e:
        return {"success": False, "error": f"点击失败: {e}"}


async def os_type_text(text: str) -> Dict[str, Any]:
    """输入文本（兼容中文：走剪贴板 ctrl+v）"""
    if not _HAS_PYAUTOGUI:
        return {"success": False, "error": "pyautogui 未安装"}
    try:
        if any(ord(ch) > 127 for ch in text):
            # 含非 ASCII（中文等）→ 剪贴板粘贴
            try:
                import pyperclip
                await _in_thread(pyperclip.copy, text)
                await asyncio.sleep(0.2)
                await _in_thread(pyautogui.hotkey, "ctrl", "v")
            except ImportError:
                return {"success": False, "error": "含中文输入需要 pyperclip (pip install pyperclip)"}
        else:
            await _in_thread(pyautogui.write, text)
        return {"success": True, "typed": text[:50]}
    except Exception as e:
        return {"success": False, "error": f"输入失败: {e}"}


async def os_press_key(hotkey: str) -> Dict[str, Any]:
    """按组合键，如 'ctrl+s'、'alt+tab'、'enter'"""
    if not _HAS_PYAUTOGUI:
        return {"success": False, "error": "pyautogui 未安装"}
    try:
        keys = [k.strip() for k in hotkey.split("+")]
        if len(keys) == 1:
            await _in_thread(pyautogui.press, keys[0])
        else:
            await _in_thread(pyautogui.hotkey, *keys)
        return {"success": True, "hotkey": hotkey}
    except Exception as e:
        return {"success": False, "error": f"按键失败: {e}"}


async def os_scroll(dy: int) -> Dict[str, Any]:
    """滚动（dy>0 上滚）"""
    if not _HAS_PYAUTOGUI:
        return {"success": False, "error": "pyautogui 未安装"}
    try:
        await _in_thread(pyautogui.scroll, dy)
        return {"success": True, "scrolled": dy}
    except Exception as e:
        return {"success": False, "error": f"滚动失败: {e}"}


# ============================================================
# 系统级安全打开：绕过键鼠失效，直接调用 OS Shell
# ============================================================

# 常见特殊文件夹映射（用系统外壳命令打开，最稳）
_SPECIAL_FOLDERS = {
    "我的电脑": "shell:MyComputerFolder",
    "此电脑": "shell:MyComputerFolder",
    "电脑": "shell:MyComputerFolder",
    "我的文档": "shell:Personal",
    "文档": "shell:Personal",
    "下载": "shell:Downloads",
    "桌面": "shell:Desktop",
    "回收站": "shell:RecycleBinFolder",
    "控制面板": "shell:ControlPanelFolder",
}


def _open_path_sync(path: str) -> Dict[str, Any]:
    import subprocess, os as _os, sys
    instr = (path or "").strip()
    if not instr:
        return {"success": False, "error": "未指定要打开的目标"}
    # 1) 特殊文件夹（我的电脑/文档等）→ 用 explorer shell 命名空间
    low = " ".join(instr.split())
    if low in _SPECIAL_FOLDERS:
        subprocess.Popen(["explorer.exe", _SPECIAL_FOLDERS[low]])
        return {"success": True, "opened": instr, "kind": "special-folder",
                "target": _SPECIAL_FOLDERS[low]}
    # 2) 盘符（E:\ 或 E:）→ explorer 打开
    if re.match(r"^[A-Za-z]:\\\\?$", low) or re.match(r"^[A-Za-z]:$", low):
        drive = low[:2] + "\\"
        subprocess.Popen(["explorer.exe", drive])
        return {"success": True, "opened": instr, "kind": "drive", "target": drive}
    # 3) 纯盘符+斜杠路径（如 C:\Windows）→ explorer
    if re.match(r"^[A-Za-z]:[\\\\/]", low):
        subprocess.Popen(["explorer.exe", low])
        return {"success": True, "opened": instr, "kind": "path", "target": low}
    # 4) 网页 URL → 默认浏览器
    if low.startswith(("http://", "https://")):
        _os.startfile(low)
        return {"success": True, "opened": instr, "kind": "url", "target": low}
    # 5) 已安装应用名（如 notepad/calc）→ 尝试直接启动
    try:
        _os.startfile(low)
        return {"success": True, "opened": instr, "kind": "app", "target": low}
    except Exception:
        pass
    return {"success": False, "error": f"无法识别要打开的目标: {instr}"}


async def os_open_path(path: str) -> Dict[str, Any]:
    """
    [推荐！] 系统级安全打开：打开"我的电脑/此电脑"、任意盘符(如 E:)、
    文件夹路径、网页URL、或已安装应用(notepad/calc/记事本)。
    直接调用 Windows 系统 Shell，不依赖键鼠模拟，最稳定可靠。
    打开"我的电脑/某盘/某文件夹"时优先用本工具。
    """
    try:
        r = await _in_thread(_open_path_sync, path)
        # 截图校验：确认对应窗口是否真实弹出
        try:
            await asyncio.sleep(1.0)
            wins = await _in_thread(_list_windows_sync)
            probe = next((w for w in wins if (path and path[:2] in (w["title"] or ""))
                          or (path and "文档" in (w["title"] or "") and "我的" in path)), None)
            if probe:
                r["verified_window"] = probe["title"]
        except Exception:
            pass
        return r
    except Exception as e:
        return {"success": False, "error": f"打开失败: {e}"}


def _list_directory_sync(path: str) -> Dict[str, Any]:
    import os as _os
    target = (path or "").strip()
    if not target:
        return {"success": False, "error": "未指定目录"}
    # 兼容 "E:" / "E" / "E:\\" 形式 → 归一为带反斜杠盘符
    if re.match(r"^[A-Za-z]:?$", target):
        target = target[:1] + ":\\"
    if not target.endswith(("\\", "/")):
        target += "\\"
    if not _os.path.exists(target):
        return {"success": False, "error": f"目录不存在: {target}", "path": target}
    try:
        entries = _os.listdir(target)
        dirs = [e for e in entries if _os.path.isdir(_os.path.join(target, e))]
        files = [e for e in entries if not _os.path.isdir(_os.path.join(target, e))]
        dirs.sort(); files.sort()
        return {
            "success": True, "path": target,
            "total": len(entries),
            "folders_count": len(dirs),
            "files_count": len(files),
            "folders": dirs, "files": files,
        }
    except Exception as e:
        return {"success": False, "error": f"读取目录失败: {e}", "path": target}


async def os_list_directory(path: str) -> Dict[str, Any]:
    """
    [优先用] 读取指定目录下的文件夹与文件列表。
    用户问"E盘有什么文件夹/有哪些内容"时，直接用此工具读文件系统，最准确。
    """
    try:
        return await _in_thread(_list_directory_sync, path)
    except Exception as e:
        return {"success": False, "error": f"列出目录失败: {e}"}


# ============================================================
# UI Automation：标准控件真实坐标（最准，可选依赖）
# ============================================================

async def ui_list_elements(window_title: str, limit: int = 40) -> Dict[str, Any]:
    """用 UI Automation 读取标准控件（名称+真实坐标）。未装 uiautomation 时降级。"""
    try:
        import uiautomation as auto
    except ImportError:
        return {"success": False, "error": "uiautomation 未安装 (pip install uiautomation)"}
    return await _in_thread(_ui_list_elements_sync, window_title, limit)


def _ui_list_elements_sync(window_title: str, limit: int) -> Dict[str, Any]:
    import uiautomation as auto
    # asyncio 线程池中 uiautomation 需要先初始化 COM（避免 [WinError -2147221008] 尚未调用 CoInitialize）
    try:
        import pythoncom
        if pythoncom._GetInterfaceCount is not None:
            pass
        pythoncom.CoInitialize()
    except ImportError:
        pass
    except Exception:
        pass
    try:
        win = auto.WindowControl(searchDepth=1, SubName=window_title)
        if not win.Exists(3, 1):
            return {"success": False, "error": f"UI Automation 未找到窗口: {window_title}"}
        items = []
        def walk(control, depth):
            if len(items) >= limit or depth > 6:
                return
            name = control.Name
            if name and len(str(name)) > 0:
                r = control.BoundingRectangle
                if r.right > r.left and r.bottom > r.top:
                    items.append({
                        "name": str(name)[:60],
                        "type": control.ControlTypeName or "",
                        "x1": r.left, "y1": r.top, "x2": r.right, "y2": r.bottom,
                    })
        def walk_all(ctx, depth):
            for c in ctx.GetChildren():
                walk(c, depth)
                if len(items) < limit:
                    walk_all(c, depth + 1)
        walk_all(win, 0)
        return {"success": True, "count": len(items), "elements": items}
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ============================================================
# 视觉 Grounding 定位 + 校验闭环（豆包 Seed 2.x 专用）
# ============================================================

def _call_gui_grounding(image_b64: str, description: str) -> Optional[tuple]:
    """
    调用豆包 GUI 模型定位目标，返回 (cx_per900, cy_per900) 或 None。
    用 0-1000 相对坐标协议，提示语要求 <bbox>。
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=GUI_API_KEY, base_url=GUI_API_URL)
        prompt = (
            f"在图中定位名为 '{description}' 的目标元素（按钮/输入框/文字）。"
            "输出仅一个 <bbox>x1 y1 x2 y2</bbox>，坐标为0-1000相对坐标（左上0,0右下999,999），不含其他文字。"
        )
        resp = client.chat.completions.create(
            model=GUI_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ]}],
            max_tokens=200, timeout=60,
        )
        out = resp.choices[0].message.content or ""
        m = re.search(r"<bbox>\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*</bbox>", out, re.S)
        if not m:
            logger.warning(f"[computer_use] grounding 未返回合法 bbox: {out[:80]}")
            return None
        x1, y1, x2, y2 = map(int, m.groups())
        return ((x1 + x2) / 2, (y1 + y2) / 2)
    except Exception as e:
        logger.error(f"[computer_use] grounding 调用失败: {e}")
        return None


def _screenshot_window_sync(window_title: str) -> Optional[Dict[str, Any]]:
    """截图指定窗口，返回 {base64, win(px绝对区域)}"""
    if not _HAS_PYAUTOGUI or not _HAS_PYGETWINDOW:
        return None
    if window_title and window_title.strip():
        wins = gw.getWindowsWithTitle(window_title.strip())
        if not wins:
            return None
        win = wins[0]
        region = (win.left, win.top, win.width, win.height)
    else:
        win = None
        region = None
    img = pyautogui.screenshot(region=region)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    base = base64.b64encode(buf.getvalue()).decode()
    return {"base64": base, "left": win.left if win else 0, "top": win.top if win else 0,
            "width": img.width, "height": img.height}


async def vision_click(window_title: str, description: str, max_retry: int = 2) -> Dict[str, Any]:
    """
    视觉定位点击：让 GUI 模型(Grounding)找目标 → 点击 → 截图校验 → 失败修正（限次）。

    Args:
        window_title: 目标窗口标题关键词（空=全屏）
        description: 要点击的目标描述（如"登录按钮"）
        max_retry: 校验失败的修正重试次数

    Returns:
        {success, clicked_x, clicked_y, verify, error}
    """
    if not _HAS_PYAUTOGUI:
        return {"success": False, "error": "pyautogui 未安装"}
    for attempt in range(max_retry + 1):
        shot = await _in_thread(_screenshot_window_sync, window_title)
        if not shot:
            return {"success": False, "error": f"截图失败或未找到窗口: {window_title}"}
        cx, cy = _call_gui_grounding(shot["base64"], description)
        if cx is None:
            return {"success": False, "error": "GUI 模型未能定位目标（未返回合法坐标）"}
        # 相对(0-1000) → 屏幕绝对像素
        abs_x = int(round(cx / 1000 * shot["width"])) + shot["left"]
        abs_y = int(round(cy / 1000 * shot["height"])) + shot["top"]
        await _in_thread(pyautogui.click, abs_x, abs_y)
        # 校验：再截图，确认是否点中
        verify = await _in_thread(_screenshot_window_sync, window_title)
        if verify:
            confirm = _verify_hit(verify["base64"], description)
            if confirm:
                return {"success": True, "clicked_x": abs_x, "clicked_y": abs_y,
                        "verify": f"校验通过(第{attempt + 1}次)：" + confirm[:40]}
        logger.warning(f"[computer_use] 第{attempt + 1}次未校验通过，重试")
        await asyncio.sleep(0.6)
    return {"success": False, "error": "定位点击多次未通过校验",
            "last": (abs_x, abs_y)}


def _verify_hit(image_b64: str, description: str) -> str:
    """用 GUI 模型确认点击后界面是否出现目标变化/是否处于目标"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=GUI_API_KEY, base_url=GUI_API_URL)
        resp = client.chat.completions.create(
            model=GUI_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": f"刚才执行了一次点击（目标：{description}）。截图是否显示了点击成功的结果（如弹窗、选中、高亮、输入框聚焦）？简短回答'成功'或原因，20字内。"},
            ]}],
            max_tokens=60, timeout=60,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


# ============================================================
# OpenAI function calling 工具 Schema
# ============================================================

COMPUTER_USE_TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "os_open_path",
        "description": "[优先用] 系统级安全打开：打开'我的电脑/此电脑'、盘符(E:\\ 或 E:)、任意文件夹路径、网页URL、或已安装应用(notepad/calc)。直接调用 Windows 系统 Shell，最稳定可靠。用户说'打开我的电脑''打开E盘''打开XX'时优先用本工具。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "要打开的目标：'我的电脑'/'E:/'/'E:\\'/'E:\\GitHub'/'https://...'/'notepad' 等"}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "os_list_directory",
        "description": "[优先用] 读取指定目录下的文件夹/文件列表（用户问'有什么文件夹''有哪些内容'时用）。不依赖截图，直接读文件系统，返回准确列表。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录路径，如 'E:\\' 或 'E:\\GitHub'"}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "os_list_windows",
        "description": "列出当前所有可见窗口及位置。想知道打开了哪些应用、找特定窗口时使用。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "os_activate_window",
        "description": "把指定窗口置为前台（按标题关键词模糊匹配）。操作某个应用前先激活它。",
        "parameters": {"type": "object", "properties": {
            "title_keyword": {"type": "string", "description": "窗口标题关键词，如 '记事本'"}},
            "required": ["title_keyword"]}}},
    {"type": "function", "function": {"name": "os_minimize_window",
        "description": "最小化指定窗口（按标题关键词），用于清理遮挡桌面/目标的操作面。当视觉定位找不到目标或因窗口遮挡失败时，先最小化遮挡窗口。",
        "parameters": {"type": "object", "properties": {
            "title_keyword": {"type": "string", "description": "要最小化的窗口标题关键词，如 'Edge'、'Trae' 等"}},
            "required": ["title_keyword"]}}},
    {"type": "function", "function": {"name": "os_show_desktop",
        "description": "一键显示桌面（模拟 Win+D）：最小化所有前台窗口露出桌面/图标。当需要点击桌面图标前，或目标被大量窗口遮挡找不到时，优先调用本工具清理全部遮挡。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "os_click_xy",
        "description": "在屏幕绝对像素坐标点击鼠标。坐标来自视觉定位或你已知的位置。",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "clicks": {"type": "integer", "default": 1},
            "button": {"type": "string", "default": "left"}},
            "required": ["x", "y"]}}},
    {"type": "function", "function": {"name": "os_type_text",
        "description": "在当前聚焦输入框输入文本（支持中文，走剪贴板）。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "os_press_key",
        "description": "按组合键或回车，如 'ctrl+s'、'enter'、'alt+tab'。",
        "parameters": {"type": "object", "properties": {
            "hotkey": {"type": "string", "description": "按键或组合键，如 ctrl+s"}},
            "required": ["hotkey"]}}},
    {"type": "function", "function": {"name": "os_scroll",
        "description": "滚动当前页面（正数上滚）。",
        "parameters": {"type": "object", "properties": {
            "dy": {"type": "integer", "description": "滚动量，正数上滚"}},
            "required": ["dy"]}}},
    {"type": "function", "function": {"name": "ui_list_elements",
        "description": "用 UI Automation 读取标准控件的名称和真实像素坐标（最准）。界面元素定位优先用它。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string"},
            "limit": {"type": "integer", "default": 40}},
            "required": ["window_title"]}}},
    {"type": "function", "function": {"name": "vision_click",
        "description": "视觉定位点击：描述目标（如'登录按钮'），AI定位坐标→点击→截图校验→自动修正。适合非标准界面。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词（空=全屏）"},
            "description": {"type": "string", "description": "要点击的目标描述"}},
            "required": ["window_title", "description"]}}},
]


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def demo():
        print("1. 列窗口:", json.dumps(await os_list_windows())[:150])
        print("2. 激活记事本/计算器:")
        r = await os_activate_window("记事本")
        print("   ", r)
        if r.get("success"):
            print("3. 输入文本:")
            print("   ", await os_type_text("AI computer use test 123"))
        print("4. UI元素:")
        print("   ", json.dumps(await ui_list_elements("记事本"), ensure_ascii=False)[:200])

    asyncio.run(demo())