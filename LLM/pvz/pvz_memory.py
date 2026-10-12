#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pymem 内存封装：附加 PvZ 进程、基础类型安全读、DataArray 遍历、Board 语义结构读取。

对齐上游 cortico-world-pvz `implant.cpp`：
- `read_lawn_app` 用 GLOBAL 硬编码 gLawnApp + 双 vtable 校验，**不做运行时扫描**。
- `iterate_array` 复刻 `IterateArray`(L1516)：header.block + index*stride 遍历，
  以对象末尾 [objectSize] 处 id 校验（高位非0 且 低位==index）。
- `read_board` 复刻 `ReadBoard`(L2741) 的标量 + 四类实体读取。
错偏移一律返回哨兵，不崩引擎。
"""

from __future__ import annotations

import struct
import os
import sys
from typing import Optional, Iterator, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from LLM.pvz import pvz_offsets as off


class PvzMemory:
    """到 PlantsVsZombies.exe 的内存视图。构造后支持 32 位指针链、数组遍历与 Board 读取。"""

    def __init__(self, process_name: str = "PlantsVsZombies.exe"):
        import pymem
        self._pymem = pymem.Pymem(process_name)
        self.img_begin = int(self._pymem.base_address)
        self.img_size = self._module_size()
        self.attached = True

    # ---------- 基础读取（错偏移安全返回 None） ----------
    def read(self, addr: int, size: int) -> Optional[bytes]:
        try:
            return self._pymem.read_bytes(addr, size)
        except Exception:
            return None

    def u8(self, addr: int) -> Optional[int]:
        b = self.read(addr, 1)
        return b[0] if b is not None else None

    def i32(self, addr: int) -> Optional[int]:
        b = self.read(addr, 4)
        return struct.unpack("<i", b)[0] if b is not None else None

    def u32(self, addr: int) -> Optional[int]:
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b is not None else None

    def f32(self, addr: int) -> Optional[float]:
        b = self.read(addr, 4)
        return struct.unpack("<f", b)[0] if b is not None else None

    def ptr(self, addr: int) -> Optional[int]:
        v = self.u32(addr)
        return v if v else None

    # 指针链：基址 + 偏移序列，任一步失败返回 None
    def follow(self, base: int, *offsets: int) -> Optional[int]:
        cur = base
        for o in offsets:
            cur = self.ptr(cur + o)
            if not cur:
                return None
        return cur

    def _module_size(self) -> int:
        try:
            e_lfanew = struct.unpack("<I", self._pymem.read_bytes(self.img_begin + 0x3C, 4))[0]
            return struct.unpack("<I", self._pymem.read_bytes(self.img_begin + e_lfanew + 0x50, 4))[0]
        except Exception:
            return 0

    # ---------- gLawnApp（硬编码 + 双 vtable 校验，不扫描） ----------
    def read_lawn_app(self) -> Tuple[Optional[int], Optional[str]]:
        """返回 (lawnApp, error)。error 非空表示未能定位。"""
        g = off.GLOBAL["lawnApp"]
        if not g:
            return None, "needs_calibration: GLOBAL.lawnApp 尚未校准（用 CE 值搜索 sun→board→lawnApp 回溯后写死）"
        app = self.u32(g)
        if not app:
            return None, "gLawnApp 指向空（LawnApp 未初始化）"
        if self.img_begin <= app < self.img_begin + self.img_size:
            return None, "gLawnApp 指向映像内（非法）"
        v0 = self.u32(app)
        v1 = self.u32(app + 4)
        if not v0 or not v1:
            return None, "LawnApp 双 vtable 不可读"
        if off.GLOBAL.get("vtable") and (v0 != off.GLOBAL["vtable"]
                                         or (off.GLOBAL.get("secondary") and v1 != off.GLOBAL["secondary"])):
            return None, f"LawnApp vtable 不匹配 v0={hex(v0)} v1={hex(v1)}"
        return app, None

    # ---------- DataArray 遍历（复刻 IterateArray） ----------
    def iterate_array(self, owner: int, offset: int, object_size: int, stride: int,
                      hard_limit: int) -> Iterator[Tuple[bytes, int]]:
        """对 owner+offset 处的 DataArray 遍历。产出 (对象原始字节, id)。"""
        hdr = self.read(owner + offset, off.DATAARRAY["headerSize"])
        if not hdr or len(hdr) < off.DATAARRAY["headerSize"]:
            return
        block = struct.unpack_from("<I", hdr, 0x00)[0]
        max_used = struct.unpack_from("<I", hdr, 0x04)[0]
        max_size = struct.unpack_from("<I", hdr, 0x08)[0]
        size = struct.unpack_from("<I", hdr, 0x10)[0]
        if not block or max_size > hard_limit or max_used > max_size or size > max_size:
            return
        if stride > 0x200 or object_size + 4 > stride:
            return
        for index in range(max_used):
            addr = block + index * stride
            item = self.read(addr, stride)
            if not item or len(item) < stride:
                continue
            obj_id = struct.unpack_from("<I", item, object_size)[0]
            if (obj_id & 0xFFFF0000) == 0 or (obj_id & 0xFFFF) != index:
                continue
            yield item, obj_id

    # ---------- Board 结构读取（复刻 ReadBoard） ----------
    def read_board(self, lawn_app: int) -> Optional[dict]:
        """返回 Board 语义结构原始数据；非对局返回 None。"""
        board = self.ptr(lawn_app + off.APP["board"])
        if not board:
            return None
        b = off.BOARD
        out = {
            "address": board,
            "background": self.i32(board + b["background"]),
            "level": self.i32(board + b["level"]),
            "sun": self.i32(board + b["sun"]),
            "numWaves": self.i32(board + b["numWaves"]),
            "currentWave": self.i32(board + b["currentWave"]),
            "mainCounter": self.i32(board + b["mainCounter"]),
            "paused": bool(self.u8(board + b["paused"])),
            "levelComplete": bool(self.i32(board + b["levelComplete"])),
            "cards": self._read_cards(board),
            "plants": list(self._read_plants(board)),
            "zombies": list(self._read_zombies(board)),
            "gridItems": list(self._read_griditems(board)),
            "coins": list(self._read_coins(board)),
            "mowers": list(self._read_mowers(board)),
        }
        bg = out["background"]
        out["rows"] = 6 if bg == 2 or bg == 3 else 5
        out["columns"] = 9
        return out

    # ---- 卡片（seedBank packets） ----
    def _read_cards(self, board: int) -> list:
        bank = self.ptr(board + off.BOARD["seedBank"])
        if not bank:
            return []
        count = self.i32(bank + off.SEEDBANK["packetCount"])
        if not count or count < 0 or count > 10:
            return []
        bx = self.i32(bank + off.SEEDBANK["x"]) or 0
        by = self.i32(bank + off.SEEDBANK["y"]) or 0
        cards = []
        for slot in range(count):
            addr = bank + off.SEEDBANK["packets"] + slot * off.SEEDBANK["packetStride"]
            pkt = self.read(addr, off.SEEDBANK["packetStride"])
            if not pkt or len(pkt) < off.SEEDBANK["packetStride"]:
                continue
            pk = off.PACKET
            ptype = struct.unpack_from("<i", pkt, pk["type"])[0]
            pw = struct.unpack_from("<i", pkt, pk["width"])[0]
            ph = struct.unpack_from("<i", pkt, pk["height"])[0]
            if ptype < 0 or ptype > 255 or not (0 < pw <= 200) or not (0 < ph <= 200):
                continue
            left = bx + struct.unpack_from("<i", pkt, pk["relX"])[0] + struct.unpack_from("<i", pkt, pk["offsetX"])[0]
            top = by + struct.unpack_from("<i", pkt, pk["y"])[0]
            from struct import unpack_from as uf
            cards.append({
                "slot": slot, "type": ptype,
                "imitater": uf("<i", pkt, pk["imitater"])[0],
                "refreshCounter": uf("<i", pkt, pk["refreshCounter"])[0],
                "refreshTime": uf("<i", pkt, pk["refreshTime"])[0],
                "timesUsed": uf("<i", pkt, pk["timesUsed"])[0],
                "active": pkt[pk["active"]] != 0,
                "refreshing": pkt[pk["refreshing"]] != 0,
                "x": left + pw // 2, "y": top + ph // 2,
            })
        return cards

    # ---- 各类实体 ----
    def _read_plants(self, board: int):
        arr = self.ptr(board + off.BOARD["plants"])
        if not arr:
            return
        p = off.PLANT
        od, st = off.DATAARRAY["plantObjectSize"], off.DATAARRAY["plantStride"]
        from struct import unpack_from as uf
        for item, _ in self.iterate_array(board, off.BOARD["plants"], od, st, 1024):
            if not item[0x18] or item[0x141] or item[0x144]:
                continue
            ptype = uf("<i", item, p["type"])[0]
            row = uf("<i", item, p["row"])[0]
            col = uf("<i", item, p["column"])[0]
            if ptype < 0 or ptype > 255 or not (0 <= col < 9) or not (0 <= row < 6):
                continue
            yield {
                "type": ptype, "row": row + 1, "column": col,
                "phase": uf("<i", item, p["phase"])[0],
                "health": uf("<i", item, p["health"])[0],
                "maxHealth": uf("<i", item, p["maxHealth"])[0],
                "x": uf("<i", item, p["x"])[0], "y": uf("<i", item, p["y"])[0],
                "sleeping": item[p["sleeping"]] != 0,
                "squished": item[p["squished"]] != 0,
            }

    def _read_zombies(self, board: int):
        arr = self.ptr(board + off.BOARD["zombies"])
        if not arr:
            return
        z = off.ZOMBIE
        od, st = off.DATAARRAY["zombieObjectSize"], off.DATAARRAY["zombieStride"]
        from struct import unpack_from as uf
        for item, _ in self.iterate_array(board, off.BOARD["zombies"], od, st, 1024):
            if not item[z["alive"]] or item[z["excluded"]]:
                continue
            from_wave = uf("<i", item, z["fromWave"])[0]
            if from_wave == -2 or from_wave == -3:
                continue
            ztype = uf("<i", item, z["type"])[0]
            srow = uf("<i", item, z["row"])[0]
            if ztype < 0 or ztype > 255 or not (0 <= srow < 6):
                continue
            x = uf("<f", item, z["x"])[0]
            y = uf("<f", item, z["y"])[0]
            col = max(0, min(8, int((x - 40) / 80)))
            yield {
                "type": ztype, "row": srow + 1, "column": col,
                "phase": uf("<i", item, z["phase"])[0],
                "x": x, "y": y,
                "health": uf("<i", item, z["health"])[0],
                "maxHealth": uf("<i", item, z["maxHealth"])[0],
                "armorHealth": uf("<i", item, z["armorHealth"])[0],
                "shieldHealth": uf("<i", item, z["shieldHealth"])[0],
                "eating": item[z["eating"]] != 0,
                "hasHead": item[z["hasHead"]] != 0,
                "hypnotized": item[z["hypnotized"]] != 0,
                "slowed": uf("<i", item, z["slowedCounter"])[0] > 0,
            }

    def _read_griditems(self, board: int):
        arr = self.ptr(board + off.BOARD["gridItems"])
        if not arr:
            return
        g = off.GRIDITEM
        od, st = off.DATAARRAY["gridItemObjectSize"], off.DATAARRAY["gridItemStride"]
        from struct import unpack_from as uf
        for item, _ in self.iterate_array(board, off.BOARD["gridItems"], od, st, 128):
            if item[g["visible"]]:
                continue
            gtype = uf("<i", item, g["type"])[0]
            if gtype <= 0 or gtype > 64:
                continue
            yield {
                "type": gtype,
                "row": uf("<i", item, g["row"])[0] + 1,
                "column": uf("<i", item, g["column"])[0],
                "x": uf("<f", item, g["x"])[0], "y": uf("<f", item, g["y"])[0],
            }

    def _read_coins(self, board: int):
        arr = self.ptr(board + off.BOARD["coins"])
        if not arr:
            return
        c = off.COIN
        od, st = off.DATAARRAY["coinObjectSize"], off.DATAARRAY["coinStride"]
        from struct import unpack_from as uf
        for item, _ in self.iterate_array(board, off.BOARD["coins"], od, st, 1024):
            if not item[c["alive"]] or item[c["excluded"]]:
                continue
            ctype = uf("<i", item, c["type"])[0]
            if ctype <= 0 or ctype > 64:
                continue
            yield {
                "type": ctype,
                "x": uf("<f", item, c["x"])[0],
                "y": uf("<f", item, c["y"])[0],
                "containedType": uf("<i", item, c["containedType"])[0],
            }

    def _read_mowers(self, board: int):
        arr = self.ptr(board + off.BOARD["mowers"])
        if not arr:
            return
        m = off.MOWER
        od, st = off.DATAARRAY["mowerObjectSize"], off.DATAARRAY["mowerStride"]
        from struct import unpack_from as uf
        for item, _ in self.iterate_array(board, off.BOARD["mowers"], od, st, 32):
            yield {
                "row": uf("<i", item, m["row"])[0] + 1,
                "state": uf("<i", item, m["state"])[0],
                "type": uf("<i", item, m["type"])[0],
            }


def attach_or_none(process_name: str = "PlantsVsZombies.exe") -> Optional[PvzMemory]:
    try:
        return PvzMemory(process_name)
    except Exception:
        return None