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
import time
from pathlib import Path
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


# ============================================================
# P0-minus：本地结构化元素表（UIA + PaddleOCR 合并）
# 给 AI 程序化消费的元素列表 → [id, type, name, text, bbox, conf, source]
# 替代"靠远程 API 吐一段自然语言描述"的旧链路。
# ============================================================

# PaddleOCR 单例（首次 import 后即下载 100MB+ 权重）
_PADDLE_OCR_INSTANCE = None
def _get_paddle_ocr():
    """懒加载 + 单例 PaddleOCR（CPU 跑 ch 通用模型）。"""
    global _PADDLE_OCR_INSTANCE
    if _PADDLE_OCR_INSTANCE is not None:
        return _PADDLE_OCR_INSTANCE
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError as e:
        raise RuntimeError(f"paddleocr 未安装: {e}")
    import os as _os
    _os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    # PaddleOCR 3.x 旧版签名参数会变；以 kwargs+静音容错为主
    _PADDLE_OCR_INSTANCE = PaddleOCR(use_textline_orientation=False, lang='ch')
    return _PADDLE_OCR_INSTANCE


def _bbox_iou(a, b) -> float:
    """a/b: [x1,y1,x2,y2]，返回 IOU（0-1）。空矩形返回 0。"""
    ix1 = max(a[0], b[0]); iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2]); iy2 = min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0, (a[2]-a[0])) * max(0, (a[3]-a[1]))
    area_b = max(0, (b[2]-b[0])) * max(0, (b[3]-b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _ocr_items_from_image(img, off_x: int, off_y: int):
    """截图 OCR：火山云 OCRNormal 优先，本地 PaddleOCR 兜底。

    返回 (items, error)：items 已换算成屏幕绝对坐标。
    """
    cloud_err = ""
    try:
        from tool import ocr_cloud
        if ocr_cloud.available():
            res = ocr_cloud.ocr_pil_image(img)
            if res.get("success"):
                items = [{
                    "text": ln.get("text", ""), "conf": float(ln.get("conf") or 0.9),
                    "x1": int(ln.get("x1", 0)) + off_x,
                    "y1": int(ln.get("y1", 0)) + off_y,
                    "x2": int(ln.get("x2", 0)) + off_x,
                    "y2": int(ln.get("y2", 0)) + off_y,
                } for ln in res.get("lines") or []]
                return items, ""
            cloud_err = str(res.get("error") or "云端 OCR 失败")
        else:
            cloud_err = "云端 OCR 未配置（缺 VOLC_ACCESSKEY/VOLC_SECRETKEY）"
    except Exception as e:  # noqa: BLE001
        cloud_err = f"云端 OCR 异常: {type(e).__name__}: {e}"

    # 本地兜底：PaddleOCR（未安装时这里会给出明确原因）
    try:
        import numpy as _np
        ocr = _get_paddle_ocr()
        arr = _np.array(img.convert("RGB"))
        res = ocr.predict(arr)
        if not res:
            raise RuntimeError("OCR 返回空")
        # PaddleOCR 3.x 输出：page 是 dict-like（含 rec_texts/rec_boxes/rec_scores）
        # rec_boxes = ndarray(N, 4) int16，axis 0 是行序，axis 1 是 [x1,y1,x2,y2]
        items: List[Dict[str, Any]] = []
        for page in res:
            keys_iter = getattr(page, "keys", None)
            if callable(keys_iter):
                keys = list(keys_iter())
                if {"rec_texts", "rec_boxes", "rec_scores"} <= set(keys):
                    rec_t = list(page["rec_texts"])
                    rec_b = _np.asarray(page["rec_boxes"])  # shape (N, 4)
                    rec_s = list(page["rec_scores"])
                    if rec_b.ndim == 2 and rec_b.shape[1] >= 4:
                        for t, b_row, s in zip(rec_t, rec_b, rec_s):
                            x1, y1, x2, y2 = (int(b_row[0]), int(b_row[1]),
                                              int(b_row[2]), int(b_row[3]))
                            items.append({
                                "text": str(t), "conf": float(s),
                                "x1": x1 + off_x,
                                "y1": y1 + off_y,
                                "x2": x2 + off_x,
                                "y2": y2 + off_y,
                            })
                    continue
            # 退化：list 形态（[[box], (text, conf)]）
            if isinstance(page, list):
                for ln in page:
                    if isinstance(ln, (list, tuple)) and len(ln) == 2:
                        box, txt = ln
                        if isinstance(txt, (list, tuple)) and len(txt) == 2:
                            t_str, t_conf = txt
                        else:
                            t_str, t_conf = str(txt), 0.9
                        if isinstance(box, (list, tuple)) and len(box) >= 4:
                            xs = [p[0] for p in box[:4]]
                            ys = [p[1] for p in box[:4]]
                            items.append({
                                "text": str(t_str), "conf": float(t_conf),
                                "x1": int(min(xs)) + off_x,
                                "y1": int(min(ys)) + off_y,
                                "x2": int(max(xs)) + off_x,
                                "y2": int(max(ys)) + off_y,
                            })
        return items, ""
    except Exception as e:  # noqa: BLE001
        local_err = f"{type(e).__name__}: {e}"
        return [], f"{cloud_err}；本地 OCR: {local_err}"


def _merge_ocr_uia(uia_items: List[Dict[str, Any]], ocr_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    合并 UIA 控件 + OCR 文字：
    - IOU>=0.25 的视为同一元素 → name/text 合并（UIA name > OCR text）
    - 仅 UIA：图标按钮等无字控件 → text 空、name=UIA name
    - 仅 OCR：纯文字段落 → name=空、text=OCR
    输出按 id 顺序。
    """
    elements = []
    used_ocr = set()
    for i, u in enumerate(uia_items):
        u_box = (u["x1"], u["y1"], u["x2"], u["y2"])
        best_j = -1; best_iou = 0.0
        for j, o in enumerate(ocr_items):
            if j in used_ocr:
                continue
            o_box = (o["x1"], o["y1"], o["x2"], o["y2"])
            iou = _bbox_iou(u_box, o_box)
            if iou > best_iou:
                best_iou, best_j = iou, j
        el = {
            "id": f"el{i}",
            "type": (u.get("type") or "control").lower(),
            "name": u.get("name", "")[:80],
            "text": "",
            "bbox": [u_box[0], u_box[1], u_box[2], u_box[3]],
            "conf": 0.95,
            "source": "uia",
        }
        if best_iou >= 0.25 and best_j >= 0:
            o = ocr_items[best_j]
            el["text"] = o.get("text", "")[:120]
            el["conf"] = o.get("conf", 0.9)
            el["source"] = "uia+ocr"
            used_ocr.add(best_j)
        elements.append(el)
    # 未被合并的 OCR 项
    for j, o in enumerate(ocr_items):
        if j in used_ocr:
            continue
        elements.append({
            "id": f"ocr{j}",
            "type": "text",
            "name": "",
            "text": o.get("text", "")[:120],
            "bbox": [o["x1"], o["y1"], o["x2"], o["y2"]],
            "conf": o.get("conf", 0.9),
            "source": "ocr",
        })
    return elements


def _os_list_elements_json_sync(window_title: str) -> Dict[str, Any]:
    """
    [AI看窗口] 结构化元素 = UIA（真实控件坐标）+ PaddleOCR（任何文字）合并。
    返回 {'success': True, 'window_title': ..., 'width': ..., 'height': ...,
          'elements': [{id,type,name,text,bbox,conf,source}, ...],
          'uia_count': ..., 'ocr_count': ...}
    """
    # 1) 截图
    shot = _capture_window_content_sync(window_title)
    if not shot:
        shot = _screenshot_window_sync(window_title)
    if not shot:
        return {"success": False, "error": f"未找到窗口或截图失败: {window_title}"}
    img = shot["image"]
    win_left, win_top = shot["left"], shot["top"]
    # 2) UIA
    uia_items: List[Dict[str, Any]] = []
    uia_err = ""
    try:
        r = _ui_list_elements_sync(window_title, limit=200)
        if r.get("success"):
            uia_items = r.get("elements", [])
        else:
            uia_err = r.get("error", "UIA failed")
    except Exception as e:
        uia_err = str(e)
    # 3) OCR：云端优先（火山 OCRNormal），本地 PaddleOCR 兜底
    ocr_items: List[Dict[str, Any]] = []
    ocr_err = ""
    ocr_items, ocr_err = _ocr_items_from_image(img, win_left, win_top)
    if ocr_err:
        logger.warning(f"[computer_use] OCR failed: {ocr_err}")
    # 4) 合并
    merged = _merge_ocr_uia(uia_items, ocr_items)
    return {
        "success": True,
        "window_title": shot.get("title", window_title),
        "width": shot["width"], "height": shot["height"],
        "elements": merged,
        "uia_count": len(uia_items),
        "ocr_count": len(ocr_items),
        "uia_error": uia_err,
        "ocr_error": ocr_err,
    }


async def os_list_elements_json(window_title: str) -> Dict[str, Any]:
    """[AI看窗口·结构化] 截图+OCR+UIA 合并：返回 [{id,type,name,text,bbox,conf,source}]"""
    return await _in_thread(_os_list_elements_json_sync, window_title)


def _os_click_id_sync(window_title: str, element_id: str,
                      bbox: Optional[List[int]] = None) -> Dict[str, Any]:
    """[点击·按元素 id] 若 bbox 已提供则直接点击（避免重跑 OCR 导致 id 不稳定），
    否则列元素表 + 按 id 找 bbox。
    bbox 格式 [x1, y1, x2, y2]；不传则内部 list → find → click，
    此时 id 由本次 OCR 决定，可能与上次不同（已知问题，推荐传 bbox）。
    """
    if bbox and len(bbox) == 4:
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) // 2; cy = (y1 + y2) // 2
        if _HAS_PYAUTOGUI:
            pyautogui.click(cx, cy)
            return {"success": True, "clicked": (cx, cy),
                    "element": {"id": element_id, "bbox": bbox,
                                "method": "bbox-direct"}}
        return {"success": False, "error": "pyautogui 不可用"}
    # 没传 bbox：列元素 + 按 id 找（兼容旧调用方）
    r = _os_list_elements_json_sync(window_title)
    if not r.get("success"):
        return {"success": False, "error": r.get("error", "列元素失败")}
    for el in r.get("elements", []):
        if el.get("id") == element_id:
            x1, y1, x2, y2 = el["bbox"]
            cx = (x1 + x2) // 2; cy = (y1 + y2) // 2
            if _HAS_PYAUTOGUI:
                pyautogui.click(cx, cy)
                return {"success": True, "clicked": (cx, cy),
                        "element": el, "method": "id-lookup"}
            return {"success": False, "error": "pyautogui 不可用"}
    return {"success": False, "error": f"未找到元素 id={element_id}",
            "available_ids": [el["id"] for el in r.get("elements", [])][:20]}


async def os_click_id(window_title: str, element_id: str,
                      bbox: Optional[List[int]] = None) -> Dict[str, Any]:
    """[点击·按元素 id] 先列元素表，按 id 找到 → 点击屏幕坐标。
    强烈推荐传 bbox 直接点击，避免 os_list_elements_json id 不稳定导致点错目标。
    """
    return await _in_thread(_os_click_id_sync, window_title, element_id, bbox)


def _os_type_by_label_sync(window_title: str, label, text: str) -> Dict[str, Any]:
    """[输入·按标签] OCR 找 label → 找其右/下方最近的输入框 → 点击输入框 → 输入。
    label 可以是单个字符串、字符串列表、或逗号分隔的多候选（中英双语 OS dialog 用）。
    """
    # 归一化 label 为候选列表
    if isinstance(label, (list, tuple)):
        labels = [str(x).strip() for x in label if str(x or "").strip()]
    elif isinstance(label, str):
        # 支持 "中文|english" 或 "中文,english"
        if "|" in label:
            labels = [s.strip() for s in label.split("|") if s.strip()]
        elif "," in label and any('一' <= ch <= '鿿' for ch in label):
            # 看起来是中英双语逗号分隔
            labels = [s.strip() for s in label.split(",") if s.strip()]
        else:
            labels = [label.strip()]
    else:
        labels = [str(label).strip()]

    r = _os_list_elements_json_sync(window_title)
    if not r.get("success"):
        return {"success": False, "error": r.get("error", "列元素失败")}
    elements = r.get("elements", [])

    # 找 label：精确匹配 → 子串匹配，按候选优先级
    label_el = None
    matched_label = None
    for cand in labels:
        # 1) 精确
        for el in elements:
            t = (el.get("text") or "").strip()
            n = (el.get("name") or "").strip()
            if t == cand or n == cand:
                label_el = el
                matched_label = cand
                break
        if label_el:
            break
        # 2) 去括号/冒号子串
        for el in elements:
            blob = (el.get("text") or "") + "|" + (el.get("name") or "")
            if cand and cand in blob:
                label_el = el
                matched_label = cand
                break
        if label_el:
            break
    if not label_el:
        return {"success": False, "error": f"未找到标签 {labels}",
                "hint": "可用元素 text/name", "elements": [
                    {"id": el.get("id"), "text": el.get("text"),
                     "name": el.get("name")} for el in elements[:20]]}
    lx1, ly1, lx2, ly2 = label_el["bbox"]
    # 找最近的输入框（UIA type 似 Edit/Text 控件，或 type=input）于 label 右侧/下方
    candidates = []
    for el in elements:
        if el.get("id") == label_el["id"]:
            continue
        et = (el.get("type") or "").lower()
        if et in ("edit", "editcontrol", "text", "input", "document"):
            bx1, by1, bx2, by2 = el["bbox"]
            # 必须在 label 右侧或下方一定距离
            if (bx1 >= lx2 - 5 and by1 >= ly1 - 10) or (by1 >= ly2 - 5):
                dist = (max(0, bx1 - lx2) ** 2 + max(0, by1 - ly1) ** 2) ** 0.5
                candidates.append((dist, el))
    if not candidates:
        return {"success": False, "error": f"未在标签 {labels} 附近找到输入框",
                "label_bbox": label_el["bbox"], "label_element": label_el}
    candidates.sort(key=lambda t: t[0])
    target = candidates[0][1]
    tx1, ty1, tx2, ty2 = target["bbox"]
    cx = (tx1 + tx2) // 2; cy = (ty1 + ty2) // 2
    if _HAS_PYAUTOGUI:
        pyautogui.click(cx, cy)
        # 先清空输入框（Ctrl+A 全选 → Delete），避免和已有内容拼接
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        await_sleep(0.05)
        pyautogui.write(text, interval=0.02)
        return {"success": True, "clicked": (cx, cy),
                "matched_label": matched_label,
                "label_element": label_el, "input_element": target,
                "typed": text[:100], "cleared_existing": True}
    return {"success": False, "error": "pyautogui 不可用"}


async def os_type_by_label(window_title: str, label, text: str) -> Dict[str, Any]:
    """[输入·按标签] 先找 label，再找其右侧/下方的输入框，点之+输入。
    label 支持单个字符串 / 列表 / "中|英" 多候选。
    """
    return await _in_thread(_os_type_by_label_sync, window_title, label, text)


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


# ============================================================
# 指定窗口内容截取（Win32 PrintWindow：只截窗口自身，不受遮挡/半出屏影响）
# ============================================================

def _capture_window_content_sync(window_title: str) -> Optional[Dict[str, Any]]:
    """
    用 Win32 PrintWindow 截取指定窗口自身内容（即使被其他窗口遮挡、部分在屏幕外）。

    原理：向该窗口的 HDC 发 WM_PRINT，DWM 按窗口自身内容渲染（PW_RENDERFULLCONTENT=2），
    与屏幕上"谁盖在上面"无关 —— 只得到目标窗口，不含无关的全屏信息。

    Returns:
        {"image": PIL.Image, "left": int, "top": int, "width": int, "height": int, "title": str}
        失败返回 None。
    """
    import ctypes
    from ctypes import wintypes

    if not _HAS_PYGETWINDOW:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None

    wins = gw.getWindowsWithTitle(window_title.strip())
    if not wins:
        return None
    win = wins[0]
    hwnd = getattr(win, "_hWnd", None)
    if not hwnd:
        return None
    if win.isMinimized:
        try:
            win.restore()
            time.sleep(0.4)
        except Exception:
            pass

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # 显式声明 Win32 签名：64 位句柄必须按指针处理，否则截断导致参数溢出。
    # 注意：不声明 GetWindowRect 的 argtypes —— pygetwindow 也在用同名 API 且传自己的
    # RECT 类型，全局签名会被污染（GDI 函数 pygetwindow 不使用，声明安全）。
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = wintypes.INT
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                       wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None

    # 构造 32bit 顶向下 DIB，PrintWindow 直接画入内存位图
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER)]

    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # 负值=顶向下（top-down）
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB
    bmi.bmiHeader.biSizeImage = width * height * 4
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0,
                                  ctypes.byref(bits), None, 0)
    if not hbmp or not bits.value:
        if hbmp:
            gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)
        return None

    old = gdi32.SelectObject(hdc_mem, hbmp)
    # PW_RENDERFULLCONTENT=2：经 DWM 渲染整窗内容；个别老窗口失败则回退 0
    if not user32.PrintWindow(hwnd, hdc_mem, 2):
        user32.PrintWindow(hwnd, hdc_mem, 0)
    buf = ctypes.string_at(bits, width * height * 4)
    img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).copy()

    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)

    # 全黑说明 PrintWindow 对该窗口无效（如硬件加速内容），由上层回退区域裁剪
    try:
        if img.getextrema() == ((0, 0), (0, 0), (0, 0), (0, 0)):
            return None
    except Exception:
        pass
    return {"image": img, "left": rect.left, "top": rect.top,
            "width": width, "height": height, "title": win.title}


async def os_capture_window(window_title: str) -> Dict[str, Any]:
    """
    [AI看窗口] 截取指定窗口的内容（只截该窗口，不截全屏，避免无关信息）。

    用 Win32 PrintWindow 截取窗口自身内容——即使窗口被遮挡、部分在屏幕外也能拿到；
    PrintWindow 无效时回退屏幕区域裁剪（需窗口在前台可见）。
    返回窗口截图（base64 + 保存路径 + 尺寸），若视觉模型已配置则同时给出 AI 描述。
    """
    if not window_title or not window_title.strip():
        return {"success": False, "error": "未指定窗口标题关键词"}
    shot = await _in_thread(_capture_window_content_sync, window_title)
    if not shot:
        # 回退旧方式：pyautogui 区域裁剪
        shot = await _in_thread(_screenshot_window_sync, window_title)
        if not shot:
            return {"success": False, "error": f"未找到窗口或截图失败: {window_title}"}
        return {"success": True, "window_title": window_title,
                "width": shot["width"], "height": shot["height"],
                "image_base64": shot["base64"],
                "saved": "", "analysis": "",
                "note": "PrintWindow 无效，已回退屏幕区域裁剪（窗口需在前台可见）",
                "error": ""}

    img = shot["image"]
    # 保存 PNG 到 screenshots 目录（复用 vision_tool 配置）
    save_dir = "./screenshots"
    try:
        from tool.vision_tool import vision_config
        save_dir = vision_config.screenshot_dir or save_dir
    except Exception:
        pass
    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception:
        save_dir = "."
    fname = f"win_{int(time.time() * 1000)}.png"
    fpath = os.path.join(save_dir, fname)
    try:
        img.save(fpath, format="PNG")
    except Exception:
        fpath = ""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    # 若视觉模型可用，让 AI 描述窗口内容
    analysis = ""
    try:
        from tool.vision_tool import vision_config, _call_vision_model
        if vision_config.enabled and vision_config.api_key:
            prompt = (f"请详细描述这个窗口（标题含'{window_title}'）的内容：应用类型、"
                      "界面元素、文字、按钮、图标、布局等。用中文简洁列出要点。")
            r = await _in_thread(_call_vision_model, b64, prompt)
            analysis = r.get("result", "") if r.get("success") \
                else f"[视觉分析失败: {r.get('error', '')}]"
    except Exception as e:
        logger.warning(f"[computer_use] 视觉分析不可用: {e}")

    return {"success": True, "window_title": shot["title"],
            "width": shot["width"], "height": shot["height"],
            "image_base64": b64, "saved": fpath, "analysis": analysis,
            "error": ""}


# ============================================================
# 断言式验证 os_check（对齐可提 mc_check：只报差异，驱动"执行→验证→修正"闭环）
# ============================================================

def _os_check_sync(check_type: str, target: str, expect: bool) -> Dict[str, Any]:
    """
    断言式核验同步实现（在线程中执行）。

    check_type:
      window  - target=窗口标题关键词，核验窗口是否存在
      file    - target=文件完整路径，核验文件是否存在
      dir     - target=目录路径，核验目录是否存在
      element - target='窗口标题>>控件名'，用 UI Automation 核验控件是否存在
      screen  - target='窗口标题>>画面描述'，截取该窗口后让视觉模型核验描述是否成立
    expect: True=断言应成立 / False=断言应不成立。
    """
    evidence = ""
    if check_type == "window":
        if not _HAS_PYGETWINDOW:
            return {"success": False, "result": "pygetwindow 未安装，无法核验窗口"}
        exists = len(gw.getWindowsWithTitle((target or "").strip())) > 0
        evidence = f"窗口'{target}' {'存在' if exists else '不存在'}"
        ok = exists == expect
    elif check_type == "file":
        exists = os.path.isfile((target or "").strip())
        evidence = f"文件'{target}' {'存在' if exists else '不存在'}"
        ok = exists == expect
    elif check_type == "dir":
        exists = os.path.isdir((target or "").strip())
        evidence = f"目录'{target}' {'存在' if exists else '不存在'}"
        ok = exists == expect
    elif check_type == "element":
        win_part, _, name_part = (target or "").partition(">>")
        win_part, name_part = win_part.strip(), name_part.strip()
        if not name_part:
            return {"success": False, "result": "element 断言格式应为 '窗口标题>>控件名'"}
        try:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                import uiautomation as auto
                win = auto.WindowControl(searchDepth=1, SubName=win_part) if win_part \
                    else auto.GetRootControl()
                if win_part and not win.Exists(2, 1):
                    exists = False
                else:
                    ctrl = win.Control(searchDepth=8, Name=name_part) if win_part \
                        else auto.Control(searchDepth=8, Name=name_part)
                    exists = bool(ctrl.Exists(1, 1))
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        except ImportError:
            return {"success": False, "result": "uiautomation 未安装，无法核验 UI 控件"}
        except Exception as e:
            return {"success": False, "result": f"UI 核验异常: {e}"}
        evidence = f"控件'{name_part}' @ '{win_part}' {'存在' if exists else '不存在'}"
        ok = exists == expect
    elif check_type == "screen":
        win_part, _, desc = (target or "").partition(">>")
        win_part, desc = win_part.strip(), desc.strip()
        if not desc:
            return {"success": False, "result": "screen 断言格式应为 '窗口标题>>画面描述'"}
        shot = _capture_window_content_sync(win_part) if win_part else None
        if not shot:
            shot = _screenshot_window_sync(win_part) if win_part else None
        if not shot:
            # 无窗口关键词或窗口截图失败 → 全屏兜底
            shot = _screenshot_window_sync("")
        if not shot:
            return {"success": False, "result": "截图失败，无法核验画面"}
        b64 = shot.get("base64")
        if not b64:
            buf = io.BytesIO()
            shot["image"].save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
        try:
            from tool.vision_tool import vision_config, _call_vision_model
            if not (vision_config.enabled and vision_config.api_key):
                return {"success": False,
                        "result": "视觉核验需要配置 Doubao_API_KEY（config.ini [vision]）"}
            question = (f"图中是否出现了以下内容/状态：{desc}？只回答'是'或'否'，"
                        f"并附不超过10字的原因。")
            r = _call_vision_model(b64, question)
            if not r.get("success"):
                return {"success": False, "result": f"视觉核验失败: {r.get('error', '')}"}
            answer = (r.get("result") or "").strip()
            exists = answer[:1] in ("是", "有", "对", "Y", "y", "1")
            evidence = f"视觉核验（{desc}）: {answer[:40]}"
            ok = exists == expect
        except Exception as e:
            return {"success": False, "result": f"视觉核验异常: {e}"}
    else:
        return {"success": False, "result": f"未知断言类型: {check_type}（window/file/dir/element/screen）"}

    if ok:
        return {"success": True, "check": {"type": check_type, "target": target},
                "result": f"通过：{evidence}", "evidence": evidence}
    expect_desc = "应成立" if expect else "应不成立"
    return {"success": False, "check": {"type": check_type, "target": target},
            "result": f"差异：{evidence}（{expect_desc}）", "evidence": evidence}


async def os_check(check_type: str, target: str, expect: str = "true") -> Dict[str, Any]:
    """
    [验证] 断言式核验（对齐可提 mc_check）：核验一个事实是否成立，回执只报差异。

    执行电脑操作后用本工具验证结果；不成立就修正重试，形成"执行→验证→修正"闭环。

    check_type:
      window  - 窗口是否存在（target=标题关键词，如 '记事本'）
      file    - 文件是否存在（target=完整路径）
      dir     - 目录是否存在（target=路径）
      element - UI 控件是否存在（target='窗口标题>>控件名'）
      screen  - 画面断言（target='窗口标题>>画面应出现的内容'，用视觉模型核验；
                不写窗口标题则截全屏）
    expect: "true"=断言应成立（默认）/ "false"=断言应不成立。
    """
    check_type = (check_type or "").strip().lower()
    target = (target or "").strip()
    expect_bool = str(expect or "true").strip().lower() not in ("false", "0", "no", "否")
    r = await _in_thread(_os_check_sync, check_type, target, expect_bool)
    return r


# ============================================================
# 基础操作：打开文件 / 查找文件 / 浏览器搜索
# ============================================================

async def os_open_file(path: str) -> Dict[str, Any]:
    """
    [打开文件] 用系统默认程序打开任意文件/文件夹/URL（os.startfile）。
    用户说"打开 XXX 文件/文档/图片"时用本工具；开目录/盘符/应用用 os_open_path。
    """
    target = (path or "").strip()
    if not target:
        return {"success": False, "error": "未指定要打开的文件"}
    try:
        os.startfile(target)
    except OSError as e:
        return {"success": False, "error": f"打开失败: {e}"}
    # 校验：相关窗口/资源管理器是否出现
    verified = ""
    try:
        await asyncio.sleep(0.8)
        wins = await _in_thread(_list_windows_sync)
        base = os.path.basename(target.rstrip("\\/"))
        probe_key = base.split(".")[0][:8] if base else ""
        probe = next((w for w in wins if probe_key and probe_key in (w["title"] or "")), None)
        if probe:
            verified = probe["title"]
    except Exception:
        pass
    return {"success": True, "opened": target, "kind": "file", "verified_window": verified}


# 目录剪枝：跳过依赖目录、系统目录与隐藏临时目录，加快搜索
_SKIP_DIRS = {
    "node_modules", ".git", ".svn", ".hg", "__pycache__", "site-packages",
    "Windows", "$Recycle.Bin", "AppData", "System Volume Information",
    "venv", ".venv", "env", ".conda", "Program Files", "Program Files (x86)",
}


def _find_file_sync(keyword: str, root: str, depth: int, limit: int) -> Dict[str, Any]:
    """递归按文件名模糊搜索（在线程中执行）。"""
    import os as _os
    kw = (keyword or "").strip().lower()
    if not kw:
        return {"success": False, "error": "未指定搜索关键词"}
    root = (root or "C:\\").strip()
    if not _os.path.isdir(root):
        return {"success": False, "error": f"搜索根目录不存在: {root}"}
    matches: List[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in _os.walk(root):
        rel = _os.path.relpath(dirpath, root)
        level = 0 if rel == "." else rel.count(_os.sep) + 1
        if level > depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith("$")]
        scanned += 1
        for fn in filenames:
            if kw in fn.lower():
                matches.append(_os.path.join(dirpath, fn))
                if len(matches) >= limit:
                    return {"success": True, "keyword": keyword, "root": root,
                            "count": len(matches), "truncated": True,
                            "matches": matches, "scanned_dirs": scanned}
    return {"success": True, "keyword": keyword, "root": root,
            "count": len(matches), "truncated": False,
            "matches": matches, "scanned_dirs": scanned}


async def os_find_file(keyword: str, root: str = "C:\\", depth: int = 6, limit: int = 20) -> Dict[str, Any]:
    """
    [查找文件] 按文件名关键词递归搜索指定目录（默认 C:\\ 全盘），返回匹配的文件路径列表。
    用户问"XX 文件在哪里/帮我找 XX 文件"时使用。自动跳过 node_modules/.git/Windows 等目录。
    搜索范围越大越慢；可指定 root（如 E:\\GitHub）和 depth 缩小范围。
    """
    r = await _in_thread(_find_file_sync, keyword, root, int(depth), int(limit))
    return r


_SEARCH_URLS = {
    "bing": "https://www.bing.com/search?q={}",
    "baidu": "https://www.baidu.com/s?wd={}",
    "google": "https://www.google.com/search?q={}",
}


async def os_browser_search(keyword: str, engine: str = "bing") -> Dict[str, Any]:
    """
    [浏览器搜索] 打开浏览器搜索关键词：优先用 EdgeAgent（Playwright 受控 Edge，返回页面
    快照供 AI 继续操作）；Edge 不可用时回退系统默认浏览器打开搜索页。
    engine: bing（默认）/ baidu / google。
    """
    import urllib.parse
    kw = (keyword or "").strip()
    if not kw:
        return {"success": False, "error": "未指定搜索关键词"}
    engine = (engine or "bing").strip().lower()
    if engine not in _SEARCH_URLS:
        return {"success": False, "error": f"不支持的搜索引擎: {engine}（bing/baidu/google）"}
    url = _SEARCH_URLS[engine].format(urllib.parse.quote(kw))
    # 优先 EdgeAgent 受控打开，返回快照
    try:
        from tool.edge_agent import browser_navigate
        r = await browser_navigate(url)
        if r.get("success"):
            return {"success": True, "engine": engine, "keyword": kw,
                    "url": r.get("url", url), "title": r.get("title", ""),
                    "next_view": r.get("next_view", ""), "mode": "edge-agent"}
        logger.warning(f"[computer_use] EdgeAgent 导航失败: {r.get('error')}")
    except Exception as e:
        logger.warning(f"[computer_use] EdgeAgent 不可用: {e}")
    # 回退：系统默认浏览器
    os.startfile(url)
    return {"success": True, "engine": engine, "keyword": kw, "url": url,
            "mode": "default-browser", "next_view": "",
            "note": "EdgeAgent 不可用，已用系统默认浏览器打开"}


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
# 剪贴板 / 进程 / 应用（原 computer_use(cu_) 域独有能力，合并进 desktop 域）
# ============================================================

def _clipboard_get_sync() -> Dict[str, Any]:
    """读剪贴板文本。优先 win32 API，pyperclip 兜底。"""
    # ---------- 路径 A：win32 ----------
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        if not user32.OpenClipboard(0):
            raise RuntimeError(f"OpenClipboard rc=0 err={ctypes.GetLastError()}")
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return {"success": True, "clipboard": ""}
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                return {"success": True, "clipboard": ""}
            try:
                # 读 UTF-16LE 直到 NUL
                buf = ctypes.string_at(ptr, 8192)
                text = buf.decode("utf-16-le", errors="ignore").split("\0", 1)[0]
                return {"success": True, "clipboard": text[:2000], "method": "win32"}
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()
    except Exception as e:
        err_a = f"win32 不可用: {e}"

    # ---------- 路径 B：pyperclip ----------
    try:
        import pyperclip
        return {"success": True, "clipboard": (pyperclip.paste() or "")[:2000], "method": "pyperclip"}
    except Exception as e:
        return {"success": False, "error": f"{err_a} | pyperclip 不可用: {e}"}


async def os_clipboard_get() -> Dict[str, Any]:
    """[剪贴板] 读取系统剪贴板文本。"""
    return await _in_thread(_clipboard_get_sync)


def _clipboard_set_sync(text: str) -> Dict[str, Any]:
    """写剪贴板文本。优先 win32 API（更稳），pyperclip 兜底。
    win32 路径：
      1) OpenClipboard / EmptyClipboard / SetClipboardData(CF_UNICODETEXT) / CloseClipboard
      2) 用 GlobalAlloc 分配 GMEM_MOVEABLE，再用 ctypes 写入 UTF-16LE，SetClipboardData 接管所有权
    失败重试 4 次，每次 sleep 0.2/0.4/0.8/1.6（OpenClipboard 偶尔被其他进程短暂占用）
    """
    payload = text or ""
    # ---------- 路径 A：win32 API ----------
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        CF_UNICODETEXT = 13
        GHND = 0x0042  # GMEM_MOVEABLE | GMEM_ZEROINIT

        last_err = ""
        for attempt in range(4):
            try:
                if not user32.OpenClipboard(0):
                    last_err = f"OpenClipboard rc=0 err={ctypes.GetLastError()}"
                    await_sleep(0.2 * (2 ** attempt))
                    continue
                try:
                    user32.EmptyClipboard()
                    # 计算 UTF-16LE 字节数（含结尾 NUL）
                    data = (payload + "\0").encode("utf-16-le")
                    h = kernel32.GlobalAlloc(GHND, len(data))
                    if not h:
                        last_err = "GlobalAlloc 失败"
                        continue
                    # 锁定并写入
                    ptr = kernel32.GlobalLock(h)
                    if not ptr:
                        kernel32.GlobalFree(h)
                        last_err = "GlobalLock 失败"
                        continue
                    ctypes.memmove(ptr, data, len(data))
                    kernel32.GlobalUnlock(h)
                    if not user32.SetClipboardData(CF_UNICODETEXT, h):
                        kernel32.GlobalFree(h)
                        last_err = f"SetClipboardData rc=0 err={ctypes.GetLastError()}"
                        continue
                    return {"success": True, "set": payload[:60], "method": "win32"}
                finally:
                    user32.CloseClipboard()
            except Exception as e:
                last_err = f"win32 attempt {attempt}: {e}"
                await_sleep(0.2 * (2 ** attempt))
                continue
        # win32 全部失败 → 退到 pyperclip
        err_a = last_err or "win32 路径无详细错误"
    except Exception as e:
        err_a = f"win32 路径不可用: {e}"

    # ---------- 路径 B：pyperclip ----------
    try:
        import pyperclip
        last_err = ""
        for attempt in range(4):
            try:
                pyperclip.copy(payload)
                return {"success": True, "set": payload[:60], "method": "pyperclip"}
            except Exception as e:
                last_err = f"pyperclip attempt {attempt}: {e}"
                import time as _t
                _t.sleep(0.25 * (2 ** attempt))
        return {"success": False, "error": f"win32: {err_a[:120]} | pyperclip: {last_err[:120]}"}
    except Exception as e:
        return {"success": False, "error": f"win32: {err_a[:120]} | pyperclip 不可用: {e}"}


def await_sleep(s):
    import time as _t
    _t.sleep(s)


async def os_clipboard_set(text: str) -> Dict[str, Any]:
    """[剪贴板] 写入文本到系统剪贴板（可配合 os_press_key 'ctrl+v' 粘贴）。"""
    return await _in_thread(_clipboard_set_sync, text)


async def os_paste_text(text: str) -> Dict[str, Any]:
    """[智能粘贴] 写入剪贴板 + Ctrl+V，剪贴板被锁时自动降级到 os_type_text 直接键入。
    推荐代替 os_clipboard_set + os_press_key('ctrl+v') 的两段调用。
    """
    cr = await os_clipboard_set(text)
    method = "clipboard+ctrl+v"
    if cr.get("success"):
        r = await os_press_key("ctrl+v")
        return {"success": True, "method": method, "clipboard": cr, "press": r}
    # 剪贴板失败：降级到 os_type_text
    method = "direct-type-fallback"
    tr = await os_type_text(text)
    return {"success": tr.get("success", False), "method": method,
            "clipboard_error": cr.get("error"),
            "type_result": tr}


def _list_processes_sync(keyword: str = "") -> Dict[str, Any]:
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
                "note": "窗口态进程可用 os_list_windows 查看"}
    except Exception as e:
        return {"success": False, "error": f"列进程失败: {e}"}


async def os_list_processes(keyword: str = "") -> Dict[str, Any]:
    """[进程] 列出当前运行进程（可按名称关键词过滤），用于确认软件是否已启动。"""
    return await _in_thread(_list_processes_sync, keyword)


def _list_apps_sync(include_registry: bool = False) -> Dict[str, Any]:
    try:
        from LLM.desktop_agent.desktop_agent import available_apps
        return available_apps(include_registry=bool(include_registry))
    except Exception as e:
        return {"success": False, "error": f"desktop_agent 不可用: {e}"}


async def os_list_apps(include_registry: bool = False) -> Dict[str, Any]:
    """[应用] 列出可启动的软件（桌面/开始菜单快捷方式），知道有哪些应用可开。"""
    return await _in_thread(_list_apps_sync, include_registry)


# 通用应用别名（"浏览器"等不指具体品牌）→ 候选列表，按序尝试已安装的
_LAUNCH_ALIASES = {
    "浏览器": ["msedge", "chrome", "firefox"],
    "browser": ["msedge", "chrome", "firefox"],
    "默认浏览器": ["msedge", "chrome", "firefox"],
}


def _launch_app_sync(app: str) -> Dict[str, Any]:
    target = (app or "").strip()
    if not target:
        return {"success": False, "error": "未指定应用"}
    # exe/bat/cmd/lnk 完整路径 → 直接启动（os_open_path 对这类路径只会打开所在文件夹）
    if os.path.isfile(target) and target.lower().endswith((".exe", ".bat", ".cmd", ".lnk")):
        try:
            subprocess.Popen([target], cwd=os.path.dirname(target) or None,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return {"success": True, "launched": target, "method": "exe-path"}
        except Exception as e:
            return {"success": False, "error": f"启动失败: {e}"}
    # 通用别名（不指具体品牌的"浏览器"）→ 依次尝试候选，取第一个可启动的
    for key, cands in _LAUNCH_ALIASES.items():
        if target.lower() == key.lower():
            for cand in cands:
                r = _open_path_sync(cand)
                if r.get("success"):
                    r["resolved_from"] = target
                    return r
            return {"success": False,
                    "error": f"未找到可启动的应用（已尝试: {', '.join(cands)}）"}
    # 应用名/快捷方式名 → 复用系统 Shell 打开逻辑
    return _open_path_sync(target)


async def os_launch_app(app: str) -> Dict[str, Any]:
    """[启动软件] 启动软件：可传 exe 完整路径，或应用名/快捷方式名（如 'notepad'/'微信'）。"""
    return await _in_thread(_launch_app_sync, app)


# ============================================================
# 文件 / Shell 工具（P0-1：把"AI 写项目"从不可能→秒级）
# 行业对照：Claude Code Read/Write/Edit/Glob/Grep + Bash 沙箱
# ============================================================

_MAX_FILE_BYTES = 5 * 1024 * 1024   # 5MB 写文件上限（防误写大文件阻塞）
_MAX_CMD_OUTPUT = 50 * 1024          # 50KB 单次命令 stdout/stderr 截断


def _create_file_sync(path: str, content: str, encoding: str,
                      overwrite: bool, auto_mkdir: bool) -> Dict[str, Any]:
    """[写文件] 同步实现。"""
    try:
        p = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
        if p.exists() and not overwrite:
            return {"success": False, "error": f"文件已存在: {p}（需 overwrite=True）"}
        if auto_mkdir and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        data = content if content is not None else ""
        p.write_text(data, encoding=encoding)
        return {"success": True, "path": str(p), "size": p.stat().st_size, "created": True}
    except Exception as e:
        return {"success": False, "error": f"创建文件失败: {e}"}


async def os_create_file(path: str, content: str = "",
                          encoding: str = "utf-8",
                          overwrite: bool = False,
                          auto_mkdir: bool = True) -> Dict[str, Any]:
    """[写文件] 直接创建/覆盖文件。优先于 UI 键入方式写入文本。

    Args:
        path: 目标文件路径。
        content: 文本内容（默认 "" 即空文件）。
        encoding: 文件编码，默认 utf-8。
        overwrite: 已存在时是否覆盖，默认 False。
        auto_mkdir: 父目录不存在时是否自动创建，默认 True。
    """
    return await _in_thread(_create_file_sync, path, content, encoding, overwrite, auto_mkdir)


def _mkdir_sync(path: str, parents: bool, exist_ok: bool) -> Dict[str, Any]:
    """[建目录] 同步实现。"""
    try:
        p = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
        p.mkdir(parents=parents, exist_ok=exist_ok)
        return {"success": True, "path": str(p), "created": True}
    except Exception as e:
        return {"success": False, "error": f"创建目录失败: {e}"}


async def os_mkdir(path: str, parents: bool = True,
                    exist_ok: bool = True) -> Dict[str, Any]:
    """[建目录] 创建目录。parents=True 支持多级，exist_ok=True 幂等。"""
    return await _in_thread(_mkdir_sync, path, parents, exist_ok)


def _run_command_sync(cmd, cwd: Optional[str], timeout: float,
                      env_extra: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """[Shell] 同步实现。cmd 为字符串走 shell；为列表走 exec。"""
    env = None
    if env_extra:
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in env_extra.items()})
    shell = isinstance(cmd, str)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, shell=shell, cwd=cwd, capture_output=True,
            text=True, timeout=timeout, env=env,
        )
        dur = round(time.monotonic() - t0, 3)
        out = (proc.stdout or "")
        err = (proc.stderr or "")
        out_trunc = len(out) > _MAX_CMD_OUTPUT
        err_trunc = len(err) > _MAX_CMD_OUTPUT
        return {
            "success": True,
            "returncode": proc.returncode,
            "stdout": out[:_MAX_CMD_OUTPUT] if out_trunc else out,
            "stderr": err[:_MAX_CMD_OUTPUT] if err_trunc else err,
            "stdout_truncated": out_trunc,
            "stderr_truncated": err_trunc,
            "duration_s": dur,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"命令执行超时 ({timeout}s)",
                "timeout": True, "duration_s": round(time.monotonic() - t0, 3)}
    except FileNotFoundError as e:
        return {"success": False, "error": f"命令未找到: {e}"}
    except Exception as e:
        return {"success": False, "error": f"命令执行失败: {e}",
                "duration_s": round(time.monotonic() - t0, 3)}


async def os_run_command(cmd, cwd: Optional[str] = None,
                          timeout: float = 60.0,
                          env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """[Shell] 执行 shell 命令。cmd 是字符串（shell=True）或列表（shell=False）。

    返回 stdout/stderr/returncode/duration_s；单边输出超 50KB 截断。
    高危工具：建议在 Policy 治理（P3-1）落定前只对可信意图调用。
    """
    return await _in_thread(_run_command_sync, cmd, cwd, timeout, env)


def _read_file_sync(path: str, encoding: str, max_bytes: Optional[int],
                    offset: Optional[int]) -> Dict[str, Any]:
    """[读文件] 同步实现。"""
    try:
        p = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
        if not p.exists():
            return {"success": False, "error": f"文件不存在: {p}"}
        if not p.is_file():
            return {"success": False, "error": f"不是普通文件: {p}"}
        size = p.stat().st_size
        if size > _MAX_FILE_BYTES and not max_bytes:
            return {"success": False, "error":
                    f"文件过大 ({size} bytes)，超过 {_MAX_FILE_BYTES} 上限；"
                    f"请用 max_bytes+offset 分块读取"}
        with p.open("r", encoding=encoding, errors="replace") as f:
            if offset:
                f.seek(offset)
            content = f.read(max_bytes) if max_bytes else f.read()
        # Windows 文本模式 universal-newline：\r\n 读为 \n；按读到的内容长度判断截断更可靠
        return {
            "success": True,
            "path": str(p),
            "content": content,
            "size": size,
            "encoding": encoding,
            "offset": offset or 0,
            "truncated": bool(max_bytes) and len(content) >= (max_bytes - 1),
        }
    except Exception as e:
        return {"success": False, "error": f"读取文件失败: {e}"}


async def os_read_file(path: str, encoding: str = "utf-8",
                        max_bytes: Optional[int] = None,
                        offset: Optional[int] = None) -> Dict[str, Any]:
    """[读文件] 读文件内容。大文件用 offset+max_bytes 分块（对齐 Claude Code Read）。"""
    return await _in_thread(_read_file_sync, path, encoding, max_bytes, offset)


def _edit_file_sync(path: str, search_text: str, new_text: str,
                    replace_all: bool) -> Dict[str, Any]:
    """[改文件] 同步实现：Aider Search-Replace Block 协议。"""
    try:
        p = Path(os.path.expandvars(os.path.expanduser(path))).resolve()
        if not p.exists():
            return {"success": False, "error": f"文件不存在: {p}"}
        original = p.read_text(encoding="utf-8")
        count = original.count(search_text)
        if count == 0:
            return {"success": False, "error":
                    "未找到 search_text 匹配（含空格/换行须完全一致），"
                    "建议先 os_read_file 看实际内容"}
        if count > 1 and not replace_all:
            return {"success": False, "error":
                    f"匹配到 {count} 处，需 replace_all=True 才能全部替换",
                    "match_count": count}
        updated = original.replace(search_text, new_text) if replace_all \
            else original.replace(search_text, new_text, 1)
        p.write_text(updated, encoding="utf-8")
        return {
            "success": True,
            "path": str(p),
            "replaced": count if replace_all else 1,
            "old_size": len(original),
            "new_size": len(updated),
        }
    except Exception as e:
        return {"success": False, "error": f"编辑文件失败: {e}"}


async def os_edit_file(path: str, search_text: str, new_text: str,
                        replace_all: bool = False) -> Dict[str, Any]:
    """[改文件] Search-Replace 精确编辑（Aider 协议）。

    search_text 必须完全匹配（含空格/换行/缩进）；多次匹配需 replace_all=True。
    失败时先 os_read_file 看实际内容再重试。
    """
    return await _in_thread(_edit_file_sync, path, search_text, new_text, replace_all)


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
    {"type": "function", "function": {"name": "os_capture_window",
        "description": "[AI看窗口] 截取指定窗口的内容并让 AI 描述（只截该窗口，不截全屏，避免无关信息）。窗口被遮挡或部分在屏幕外也能截取。想知道某个窗口里有什么内容/界面时使用；可先用 os_list_windows 查看窗口标题。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词，如 'Edge'、'记事本'、'Trae'"}},
            "required": ["window_title"]}}},
    {"type": "function", "function": {"name": "os_check",
        "description": "[验证] 断言式核验（对齐可提 mc_check）：核验一个事实是否成立，回执只报差异。执行电脑操作后必须用本工具验证结果，不成立就修正重试，形成'执行→验证→修正'闭环。check_type: window=窗口是否存在(target=标题关键词)；file=文件是否存在(target=完整路径)；dir=目录是否存在(target=路径)；element=UI控件是否存在(target='窗口标题>>控件名')；screen=画面断言(target='窗口标题>>画面应出现的内容'，用视觉模型核验)。expect: true=应成立(默认)/false=应不成立。",
        "parameters": {"type": "object", "properties": {
            "check_type": {"type": "string", "enum": ["window", "file", "dir", "element", "screen"],
                            "description": "断言类型"},
            "target": {"type": "string", "description": "断言目标（格式见工具描述）"},
            "expect": {"type": "string", "default": "true", "description": "true=断言应成立 / false=断言应不成立"}},
            "required": ["check_type", "target"]}}},
    {"type": "function", "function": {"name": "os_open_file",
        "description": "[打开文件] 用系统默认程序打开任意文件（文档/图片/程序等）。用户说'打开 XXX 文件'时用；开目录/盘符/应用用 os_open_path。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "要打开的文件完整路径，如 'E:\\notes\\计划.txt'"}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "os_find_file",
        "description": "[查找文件] 按文件名关键词递归搜索（默认 C:\\ 全盘），返回匹配文件路径列表。用户问'XX 文件在哪里/帮我找 XX 文件'时用；自动跳过 node_modules/.git/Windows 等目录，可指定 root 和 depth 缩小范围。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "文件名关键词，如 'test'、'计划'"},
            "root": {"type": "string", "description": "搜索根目录，默认 'C:\\\\'", "default": "C:\\\\"},
            "depth": {"type": "integer", "description": "递归深度上限，默认 6", "default": 6},
            "limit": {"type": "integer", "description": "结果数量上限，默认 20", "default": 20}},
            "required": ["keyword"]}}},
    {"type": "function", "function": {"name": "os_browser_search",
        "description": "[浏览器搜索] 打开浏览器搜索关键词：优先用受控 Edge（返回页面快照可继续操作），Edge 不可用时回退系统默认浏览器。engine 可选 bing（默认）/ baidu / google。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "搜索关键词"},
            "engine": {"type": "string", "enum": ["bing", "baidu", "google"],
                        "description": "搜索引擎，默认 bing", "default": "bing"}},
            "required": ["keyword"]}}},
    {"type": "function", "function": {"name": "os_clipboard_get",
        "description": "[剪贴板] 读取系统剪贴板文本。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "os_clipboard_set",
        "description": "[剪贴板] 写入文本到系统剪贴板（可配合 os_press_key 'ctrl+v' 粘贴）。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "要写入剪贴板的文本"}},
            "required": ["text"]}}},
    {"type": "function", "function": {"name": "os_list_processes",
        "description": "[进程] 列出当前运行进程（可按名称关键词过滤），用于确认软件是否已启动。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "进程名关键词过滤，可空", "default": ""}},
            "required": []}}},
    {"type": "function", "function": {"name": "os_list_apps",
        "description": "[应用] 列出可启动的软件（桌面/开始菜单快捷方式），用于知道有哪些应用可开。",
        "parameters": {"type": "object", "properties": {
            "include_registry": {"type": "boolean", "description": "是否包含注册表安装项，默认 false", "default": False}},
            "required": []}}},
    {"type": "function", "function": {"name": "os_launch_app",
        "description": "[启动软件] 启动软件：可传 exe 完整路径，或应用名/快捷方式名（如 'notepad'、'微信'）。注意：打开文件夹/盘符/URL 用 os_open_path。",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string", "description": "exe 路径或应用名，如 'E:\\\\app\\\\foo.exe'、'notepad'"}},
            "required": ["app"]}}},
    # P0-1：文件 / Shell 工具 —— 写项目从不可能→秒级
    {"type": "function", "function": {"name": "os_create_file",
        "description": "[写文件] 直接创建/写入文本文件。AI 写代码、生成 README/requirements.txt 等用。比 UI 键入快百倍、可靠百倍。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目标文件路径，如 'E:\\\\myproj\\\\main.py'"},
            "content": {"type": "string", "description": "文件内容（默认空）", "default": ""},
            "encoding": {"type": "string", "description": "编码，默认 utf-8", "default": "utf-8"},
            "overwrite": {"type": "boolean", "description": "文件已存在时是否覆盖", "default": False},
            "auto_mkdir": {"type": "boolean", "description": "父目录不存在时自动创建", "default": True}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "os_mkdir",
        "description": "[建目录] 创建目录，默认支持多级（parents=True）、已存在不报错（exist_ok=True）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录路径"},
            "parents": {"type": "boolean", "description": "是否自动创建中间目录", "default": True},
            "exist_ok": {"type": "boolean", "description": "目录已存在时不报错", "default": True}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "os_run_command",
        "description": "[Shell] 执行 shell 命令：git init / pip install / npm install / python main.py / dir 等。cmd 字符串走 PowerShell 模式；列表走 exec 模式。返回 stdout/stderr/returncode。单次输出 > 50KB 自动截断。",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string", "description": "命令（字符串=PowerShell 模式；列表=exec 模式）"},
            "cwd": {"type": "string", "description": "工作目录", "default": None},
            "timeout": {"type": "number", "description": "超时秒数", "default": 60},
            "env": {"type": "object", "description": "额外环境变量", "default": None}},
            "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "os_read_file",
        "description": "[读文件] 读文件内容。>5MB 大文件用 offset+max_bytes 分块读（对齐 Claude Code Read）。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "encoding": {"type": "string", "description": "编码", "default": "utf-8"},
            "max_bytes": {"type": "integer", "description": "最多读多少字节", "default": None},
            "offset": {"type": "integer", "description": "从第几个字节开始", "default": None}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "os_edit_file",
        "description": "[改文件] Search-Replace 精确编辑（Aider 协议）：search_text 必须完全匹配（含空格/换行/缩进），多次匹配需 replace_all=True。失败先 os_read_file 看实际内容再重试。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "search_text": {"type": "string", "description": "要替换的精确文本"},
            "new_text": {"type": "string", "description": "替换后的新文本"},
            "replace_all": {"type": "boolean", "description": "匹配到多处时是否全部替换", "default": False}},
            "required": ["path", "search_text", "new_text"]}}},
    # P0-minus：本地 PaddleOCR + UIA 合并的结构化视觉能力
    {"type": "function", "function": {"name": "os_list_elements_json",
        "description": "[AI看窗口·结构化] 截图 + 本地 PaddleOCR + UI Automation 合并，返回 [{id,type,name,text,bbox,conf,source}]。用于'AI 看到啥→下一步点啥'，比 os_capture_window 更适合程序化决策。注意：返回的 id 仅本次调用稳定，下次调用可能变化；点击时建议同时传 bbox 锁定坐标。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词"}},
            "required": ["window_title"]}}},
    {"type": "function", "function": {"name": "os_click_id",
        "description": "[点击·按元素 id] 点击指定元素（标准控件或 OCR 文本）。强烈推荐同时传 bbox 直接锁定坐标，避免 OCR 重跑导致 id 漂移。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词"},
            "element_id": {"type": "string", "description": "元素 id（来自 os_list_elements_json）"},
            "bbox": {"type": "array", "items": {"type": "integer"},
                     "description": "可选：直接传 [x1,y1,x2,y2] 锁定坐标，跳过重跑 OCR（推荐）"}},
            "required": ["window_title", "element_id"]}}},
    {"type": "function", "function": {"name": "os_type_by_label",
        "description": "[输入·按标签] OCR 找 label 文字 → 定位其右/下方最近的输入框 → 点击 + 输入文本。适合 OS Save As / 设置 dialog。label 支持 '中文|english' 多候选（中英双语 OS 自动匹配）。",
        "parameters": {"type": "object", "properties": {
            "window_title": {"type": "string", "description": "窗口标题关键词"},
            "label": {"type": "string", "description": "标签文字（如 '文件名' / 'File name' / '文件名(N):|File name'）；单字符串或 '|' 分隔的多候选"},
            "text": {"type": "string", "description": "要输入的文本"}},
            "required": ["window_title", "label", "text"]}}},
    {"type": "function", "function": {"name": "os_paste_text",
        "description": "[智能粘贴] 把文本写入剪贴板并触发 Ctrl+V 粘贴；剪贴板被锁时自动降级到直接键入。比 os_clipboard_set + os_press_key 两段调用更可靠。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "要粘贴的文本"}},
            "required": ["text"]}}},
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