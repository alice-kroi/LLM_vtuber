#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PvZ 输入：定位游戏窗口 HWND、行列→客户区像素、向游戏窗口投递 WM_MOUSE 消息（后台可用）。

沿用上游植入体的思路：PvZ 通过 Win32 鼠标消息驱动，直接 PostMessage 到游戏窗口即可
后台操作，不依赖系统光标/焦点（避开本项目"键鼠投递被前台/IDE 抢占"的既有教训）。
坐标规则：800x600 客户区，row=1..5(从上到下，屋顶从高)、col=1..9(从右到左，越靠左越靠近房子)。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

from . import pvz_offsets as off

user32 = ctypes.windll.user32

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203


def _make_lparam(x: int, y: int) -> int:
    return (y << 16) | (x & 0xFFFF)


def find_game_window(title_hint: str = "Plants vs. Zombies") -> Optional[int]:
    """返回顶层窗口 HWND（首选匹配标题，兜底枚举 PvZ 进程主窗口）。"""
    hwnd = user32.FindWindowW(None, title_hint)
    if hwnd:
        return hwnd
    return None


def _ensure_foreground(hwnd: int) -> None:
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def click_client(hwnd: int, x: int, y: int, activate: bool = True) -> bool:
    """在指定 HWND 客户区 (x,y) 点击（PostMessage，不抢占系统光标）。x,y 为屏幕坐标，func 内部按窗口客户原点转换。"""
    if not hwnd:
        return False
    if activate:
        _ensure_foreground(hwnd)
    lp = _make_lparam(x, y)
    # PvZ 需要 wParam 为 MK_LBUTTON(1)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, 1, lp)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    return True


def cell_to_client(row: int, col: int, height_adj: int = 0) -> tuple:
    """行列 → 800x600 客户区像素中心。这是起始几何，P1 逐背景实测校正。"""
    x = 80 * col - 40          # 1 列=80px，col=1 → x=40；刻度参考：80 宽/格
    y = 100 * row - 50 + height_adj  # 5 行，每行 100px 高（起始猜测）
    return x, y


def client_to_screen(hwnd: int, x: int, y: int) -> tuple:
    """把客户区坐标转换为屏幕坐标（供需要屏幕坐标的点击用）。"""
    pt = wintypes.POINT(x, y)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y