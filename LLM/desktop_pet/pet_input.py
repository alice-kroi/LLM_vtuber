#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""悬浮输入框（桌面宠物渠道）：tkinter 置顶小窗，输入内容 → 主播脑 inbox。

- 输入回车/点发送 → HTTP POST 主程序 /api/send（webui，走 streamer inbox）
- 轮询 /api/messages 显示最近一条回复（桌宠头顶的小气泡）
- 无边框置顶，可拖动
- 默认位置：桌宠（右下角 300x300）正上方，水平居中
"""

import argparse
import json
import sys
import time
import urllib.request


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"success": False, "error": str(e)}


def _pet_head_pos() -> tuple:
    """桌宠窗口左上角 (x, y, w, h)；找不到时按右下角 300x300 默认计算。"""
    try:
        import pygetwindow as gw
        wins = [w for w in gw.getAllWindows() if "Bongo" in (w.title or "")]
        if wins:
            w = wins[0]
            return w.left, w.top, w.width, w.height
    except Exception:
        pass
    import ctypes
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    pet_w, pet_h, margin, taskbar = 300, 300, 10, 60
    return sw - pet_w - margin, sh - pet_h - taskbar, pet_w, pet_h


def main():
    import tkinter as tk

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8081")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    root = tk.Tk()
    root.title("桌宠输入")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#2b2b2b")

    # 输入框在桌宠头顶：水平居中于桌宠、紧贴其上
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    ww, wh = 340, 150
    px, py, pw, ph = _pet_head_pos()
    x = max(0, min(px + (pw - ww) // 2, sw - ww))
    y = max(0, py - wh - 8)
    root.geometry(f"{ww}x{wh}+{x}+{y}")

    frame = tk.Frame(root, bg="#2b2b2b")
    frame.pack(fill="both", expand=True, padx=8, pady=6)

    # 小气泡（回复显示区）：小字号、限长，完整显示在输入框内
    reply_lbl = tk.Label(frame, text="（回复显示在这里）", font=("Microsoft YaHei", 9),
                         bg="#353535", fg="#cfcfcf", anchor="w", justify="left",
                         wraplength=310, padx=8, pady=5)
    reply_lbl.pack(fill="x", pady=(0, 6))

    entry = tk.Entry(frame, font=("Microsoft YaHei", 11), bg="#3a3a3a",
                     fg="#e8e8e8", insertbackground="#e8e8e8", relief="flat")
    entry.pack(fill="x")
    entry.focus_set()

    # 拖动窗口
    drag = {"x": 0, "y": 0}

    def on_press(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def on_move(e):
        root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    for widget in (frame, reply_lbl, entry):
        widget.bind("<Button-1>", on_press)
        widget.bind("<B1-Motion>", on_move)

    def send(_=None):
        text = entry.get().strip()
        if not text:
            return
        entry.delete(0, "end")
        _post(f"{base}/api/send",
              {"name": "桌宠用户", "content": text})

    entry.bind("<Return>", send)

    def poll_reply():
        r = _get(f"{base}/api/messages")
        msgs = r.get("messages") if isinstance(r, dict) else None
        if msgs:
            last = msgs[-1]
            if isinstance(last, dict):
                c = last.get("content") or last.get("text") or ""
                if c:
                    reply_lbl.config(text=str(c)[:100])
        root.after(2500, poll_reply)

    poll_reply()
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
