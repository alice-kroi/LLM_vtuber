#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui_perception 域核心库：未知软件界面识别（分级：UIA 树 → OCR → 视觉 grounding）。

覆盖能力（对齐 computer_use_research.md 阶段 B+C / R2）：
- UIA 层（主力）：窗口控件树 dump、按控件点击/设值/读文本——IDE/QQ 等标准控件最准
- OCR 层（兜底）：窗口/全屏截图 OCR（引擎可插拔：paddleocr → easyocr），自绘/无 UIA 界面用
- 视觉 grounding 层（兜底）：豆包 GUI 模型定位目标坐标（需 Doubao_API_KEY）

分级原则：先 UIA（零延迟、精确），无控件/无 Name 再 OCR，最后视觉。
"""

import base64
import io
import os
import time


# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------

def _coinit():
    try:
        import pythoncom
        pythoncom.CoInitialize()
        return pythoncom
    except ImportError:
        return None


def _coininit_end(pc):
    if pc:
        try:
            pc.CoUninitialize()
        except Exception:
            pass


def _screenshot_pil(window_title: str):
    """窗口/全屏截图，返回 (PIL.Image, 左上角坐标) 或 None。"""
    from tool.computer_use import _capture_window_content_sync, _screenshot_window_sync
    if window_title and window_title.strip():
        shot = _capture_window_content_sync(window_title)
        if shot and shot.get("image") is not None:
            return shot["image"], (shot["left"], shot["top"])
        shot = _screenshot_window_sync(window_title)
        if shot and shot.get("base64"):
            from PIL import Image
            img = Image.open(io.BytesIO(base64.b64decode(shot["base64"]))).convert("RGB")
            return img, (shot["left"], shot["top"])
    else:
        shot = _screenshot_window_sync("")
        if shot and shot.get("base64"):
            from PIL import Image
            img = Image.open(io.BytesIO(base64.b64decode(shot["base64"]))).convert("RGB")
            return img, (0, 0)
    return None


# ---------------------------------------------------------------------------
# UIA 层
# ---------------------------------------------------------------------------

def list_windows() -> dict:
    """列出全部可见窗口（盘点桌面上有哪些应用/遮挡）。"""
    try:
        from LLM.desktop_agent.desktop_agent import list_windows as _lw
        return _lw(visible_only=True)
    except Exception as e:
        return {"success": False, "error": f"列窗口失败: {e}"}


def _find_controls(win, name, limit: int):
    """在窗口内按名称模糊找控件（返回匹配列表，截断到 limit）。"""
    import uiautomation as auto
    out = []
    kw = (name or "").strip()

    def walk(c, depth=0):
        if len(out) >= limit or depth > 10:
            return
        try:
            if kw and kw in (c.Name or ""):
                out.append(c)
        except Exception:
            pass
        try:
            for ch in c.GetChildren():
                walk(ch, depth + 1)
        except Exception:
            pass

    if kw:
        walk(win)
    return out


def dump_ui_tree(window_title: str, max_depth: int = 8, max_items: int = 150) -> dict:
    """UIA 控件树 dump：类型/名称/坐标（含 Text 控件，用于读取界面文字）。"""
    import uiautomation as auto
    pc = _coinit()
    try:
        win = auto.WindowControl(searchDepth=1, SubName=window_title)
        if not win.Exists(3, 1):
            return {"success": False, "error": f"UIA 未找到窗口: {window_title}",
                    "hint": "无 UIA 暴露时改用 uip_ocr_screen / uip_ground_element"}
        out = []

        def walk(c, depth):
            if depth > int(max_depth) or len(out) >= int(max_items):
                return
            try:
                name = (c.Name or "").strip()
                ctype = c.ControlTypeName or ""
                rect = c.BoundingRectangle
                item = {"depth": depth, "type": ctype.replace("Control", "").lower(),
                        "name": name[:80]}
                if rect is not None:
                    item["x"] = rect.left
                    item["y"] = rect.top
                    item["w"] = rect.width()
                    item["h"] = rect.height()
                if name or ctype in ("TextControl", "ButtonControl", "EditControl",
                                     "MenuItemControl", "TabItemControl", "CheckBoxControl",
                                     "ListItemControl", "ComboBoxControl", "HyperlinkControl"):
                    out.append(item)
            except Exception:
                pass
            try:
                for ch in c.GetChildren():
                    walk(ch, depth + 1)
            except Exception:
                pass

        walk(win, 0)
        return {"success": True, "window": window_title, "count": len(out),
                "elements": out,
                "note": "uip_click_control/uip_set_text 可按 name 直接操作这些控件"}
    finally:
        _coininit_end(pc)


def click_control(window_title: str, control_name: str, index: int = 0) -> dict:
    """按控件名点击（优先 UIA Pattern，失败回退坐标点击）。"""
    import uiautomation as auto
    import pyautogui
    pc = _coinit()
    try:
        win = auto.WindowControl(searchDepth=1, SubName=window_title)
        if not win.Exists(3, 1):
            return {"success": False, "error": f"UIA 未找到窗口: {window_title}"}
        found = _find_controls(win, control_name, limit=int(index) + 1)
        if not found or len(found) <= int(index):
            return {"success": False, "error": f"未找到控件 '{control_name}'",
                    "hint": "可用 uip_dump_ui_tree 看实际控件名"}
        c = found[int(index)]
        # 1) 交互 Pattern 优先（不抢焦点、精确）
        for pat in ("InvokePattern", "SelectionItemPattern", "TogglePattern",
                    "ExpandCollapsePattern"):
            try:
                p = getattr(c, f"Get{pat}")()
                if p is not None:
                    if pat == "InvokePattern":
                        p.Invoke()
                    elif pat == "SelectionItemPattern":
                        p.Select()
                    elif pat == "TogglePattern":
                        p.Toggle()
                    else:
                        p.Expand()
                    return {"success": True, "method": pat,
                            "control": (c.Name or "")[:60]}
            except Exception:
                continue
        # 2) 坐标点击兜底
        r = c.BoundingRectangle
        if r is not None and r.width() > 0 and r.height() > 0:
            x, y = r.left + r.width() // 2, r.top + r.height() // 2
            pyautogui.click(x, y)
            return {"success": True, "method": "coordinate", "x": x, "y": y,
                    "control": (c.Name or "")[:60]}
        return {"success": False, "error": "控件无可用交互方式"}
    finally:
        _coininit_end(pc)


def set_text(window_title: str, control_name: str, text: str, index: int = 0) -> dict:
    """给输入控件设值（优先 UIA ValuePattern，失败回退坐标点击+剪贴板粘贴）。"""
    import uiautomation as auto
    import pyautogui
    import pyperclip
    pc = _coinit()
    try:
        win = auto.WindowControl(searchDepth=1, SubName=window_title)
        if not win.Exists(3, 1):
            return {"success": False, "error": f"UIA 未找到窗口: {window_title}"}
        found = _find_controls(win, control_name, limit=int(index) + 1)
        if not found or len(found) <= int(index):
            return {"success": False, "error": f"未找到控件 '{control_name}'"}
        c = found[int(index)]
        try:
            vp = c.GetValuePattern()
            if vp is not None:
                vp.SetValue(text)
                return {"success": True, "method": "valuepattern",
                        "control": (c.Name or "")[:60], "set": text[:40]}
        except Exception:
            pass
        # 兜底：坐标点击聚焦 + 清空 + 剪贴板粘贴（支持中文）
        r = c.BoundingRectangle
        if r is not None and r.width() > 0 and r.height() > 0:
            x, y = r.left + r.width() // 2, r.top + r.height() // 2
            pyautogui.click(x, y)
            time.sleep(0.2)
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.hotkey("ctrl", "v")
            return {"success": True, "method": "clipboard", "x": x, "y": y,
                    "set": text[:40]}
        return {"success": False, "error": "控件无可用输入方式"}
    finally:
        _coininit_end(pc)


def read_text(window_title: str, control_name: str = "") -> dict:
    """读界面文本：指定控件文本，或窗口内全部文字（拼接，限长）。"""
    import uiautomation as auto
    pc = _coinit()
    try:
        win = auto.WindowControl(searchDepth=1, SubName=window_title)
        if not win.Exists(3, 1):
            return {"success": False, "error": f"UIA 未找到窗口: {window_title}"}
        if control_name and control_name.strip():
            found = _find_controls(win, control_name, limit=5)
            if not found:
                return {"success": False, "error": f"未找到控件 '{control_name}'"}
            c = found[0]
            try:
                vp = c.GetValuePattern()
                if vp is not None:
                    return {"success": True, "control": (c.Name or "")[:60],
                            "text": str(vp.Value())[:2000]}
            except Exception:
                pass
            return {"success": True, "control": (c.Name or "")[:60],
                    "text": str(c.Name or "")[:2000]}
        # 整窗文本
        parts = []

        def collect(c, depth=0):
            if depth > 8 or len(parts) > 200:
                return
            try:
                if c.ControlTypeName == "TextControl" and c.Name:
                    parts.append(str(c.Name)[:120])
            except Exception:
                pass
            try:
                for ch in c.GetChildren():
                    collect(ch, depth + 1)
            except Exception:
                pass

        collect(win)
        text = "\n".join(parts)
        return {"success": True, "window": window_title, "count": len(parts),
                "text": text[:3000]}
    finally:
        _coininit_end(pc)


# ---------------------------------------------------------------------------
# OCR 层（可插拔：paddleocr → easyocr）
# ---------------------------------------------------------------------------

def _ocr_engines():
    engines = []
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        engines.append("paddleocr")
    except Exception:
        pass
    try:
        import easyocr  # noqa: F401
        engines.append("easyocr")
    except Exception:
        pass
    return engines


def _run_ocr(img) -> list:
    """执行 OCR，返回 [{text, x1,y1,x2,y2}]（屏幕坐标需由调用方加偏移）。"""
    import numpy as np
    arr = np.array(img)
    eng = _ocr_engines()
    if not eng:
        return None
    if "paddleocr" in eng:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        result = ocr.ocr(arr, cls=True)
        out = []
        for line in (result or [])[0] or []:
            box, (txt, conf) = line, line[1]
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append({"text": str(txt), "x1": int(min(xs)), "y1": int(min(ys)),
                        "x2": int(max(xs)), "y2": int(max(ys)), "conf": round(float(conf), 2)})
        return out
    # easyocr
    import easyocr
    reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
    result = reader.readtext(arr)
    out = []
    for box, txt, conf in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({"text": str(txt), "x1": int(min(xs)), "y1": int(min(ys)),
                    "x2": int(max(xs)), "y2": int(max(ys)), "conf": round(float(conf), 2)})
    return out


def ocr_screen(window_title: str = "") -> dict:
    """窗口/全屏截图后 OCR（中文）。无 UIA 暴露的自绘界面用。需安装 OCR 引擎。"""
    shot = _screenshot_pil(window_title)
    if shot is None:
        return {"success": False, "error": f"截图失败: {window_title or '全屏'}"}
    img, (ox, oy) = shot
    if not _ocr_engines():
        return {"success": False, "error": "未安装 OCR 引擎",
                "hint": "pip install paddleocr 或 pip install easyocr（任选其一，均支持中文）"}
    try:
        items = _run_ocr(img)
        if items is None:
            return {"success": False, "error": "OCR 引擎不可用"}
        for it in items:
            it["x1"] += ox
            it["y1"] += oy
            it["x2"] += ox
            it["y2"] += oy
        return {"success": True, "window": window_title or "全屏", "count": len(items),
                "items": items[:60],
                "note": "坐标已换算为屏幕绝对坐标，可直接 cu_click_xy"}
    except Exception as e:
        return {"success": False, "error": f"OCR 失败: {e}"}


# ---------------------------------------------------------------------------
# 视觉 grounding 层（豆包 GUI 模型）
# ---------------------------------------------------------------------------

def ground_element(window_title: str, description: str) -> dict:
    """视觉定位：截图 → GUI 模型找目标 → 返回屏幕坐标（并点击）。"""
    import pyautogui
    try:
        from tool.computer_use import _call_gui_grounding, _screenshot_window_sync
    except Exception as e:
        return {"success": False, "error": f"grounding 依赖不可用: {e}"}
    shot = _screenshot_window_sync(window_title)
    if not shot:
        return {"success": False, "error": f"截图失败: {window_title or '全屏'}"}
    cx, cy = _call_gui_grounding(shot["base64"], description)
    if cx is None:
        return {"success": False, "error": "GUI 模型未能定位目标（需配置 Doubao_API_KEY 且模型可用）"}
    abs_x = int(round(cx / 1000 * shot["width"])) + shot["left"]
    abs_y = int(round(cy / 1000 * shot["height"])) + shot["top"]
    pyautogui.click(abs_x, abs_y)
    return {"success": True, "description": description,
            "clicked": (abs_x, abs_y),
            "note": "已点击；未配置/失败时走 OCR 或 UIA"}


# ---------------------------------------------------------------------------
# 截图（供视觉模型分析）
# ---------------------------------------------------------------------------

def capture_window(window_title: str) -> dict:
    """窗口截图（base64 + 保存 PNG），供视觉模型分析界面。"""
    shot = _screenshot_pil(window_title)
    if shot is None:
        return {"success": False, "error": f"截图失败: {window_title}"}
    img, (left, top) = shot
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    save_dir = "./screenshots"
    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception:
        save_dir = "."
    fpath = os.path.join(save_dir, f"uip_{int(time.time() * 1000)}.png")
    try:
        img.save(fpath, format="PNG")
    except Exception:
        fpath = ""
    return {"success": True, "window": window_title or "全屏",
            "width": img.width, "height": img.height,
            "image_base64": b64, "saved": fpath}
