#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""悬浮输入框（桌面宠物渠道）：tkinter 置顶小窗，输入内容 → 主播脑 inbox。

- 输入回车/点发送 → HTTP POST 主程序 /api/send（webui，走 streamer inbox）
- 轮询 /api/messages 显示最近一条回复（桌宠旁的气泡）
- 无边框置顶，可拖动
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

    # 左下角置顶（贴近桌宠，避开任务栏）
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"340x92+10+{sh - 92 - 70}")

    frame = tk.Frame(root, bg="#2b2b2b")
    frame.pack(fill="both", expand=True, padx=8, pady=6)

    entry = tk.Entry(frame, font=("Microsoft YaHei", 11), bg="#3a3a3a",
                     fg="#e8e8e8", insertbackground="#e8e8e8", relief="flat")
    entry.pack(fill="x", pady=(0, 4))
    entry.focus_set()

    reply_lbl = tk.Label(frame, text="（回复显示在这里）", font=("Microsoft YaHei", 9),
                         bg="#2b2b2b", fg="#9a9a9a", anchor="w", justify="left",
                         wraplength=320)
    reply_lbl.pack(fill="x")

    # 拖动窗口
    drag = {"x": 0, "y": 0}

    def on_press(e):
        drag["x"], drag["y"] = e.x_root - root.winfo_x(), e.y_root - root.winfo_y()

    def on_move(e):
        root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    frame.bind("<Button-1>", on_press)
    frame.bind("<B1-Motion>", on_move)
    reply_lbl.bind("<Button-1>", on_press)
    reply_lbl.bind("<B1-Motion>", on_move)

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
                    reply_lbl.config(text=str(c)[:80])
        root.after(2500, poll_reply)

    poll_reply()
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
