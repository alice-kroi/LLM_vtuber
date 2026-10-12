#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PvZ 语义局面读取：经硬编码 gLawnApp → Board → 实体数组，输出可 JSON 化观察。

对齐上游 cortico-world-pvz：内存读取在 pvz_memory（硬编码+遍历），本模块把原生视图
映射成语义 schema（植物/僵尸/卡片/割草机/太阳，含名字与健康条件）。
"""
from __future__ import annotations

from typing import Optional


def plant_condition(ptype: int, health, maximum) -> str:
    """implant.cpp PlantVisibleCondition：墙果/高坚果/南瓜/大蒜三分档，其余 intact。"""
    if ptype in (3, 23, 30, 36):
        return off.thirds_condition(health, maximum)
    if ptype == 46:  # spikerock
        if not maximum or health > 300:
            return "intact"
        return "worn" if health > 150 else "critical"
    return "intact"


def _slow_condition_for(health, maximum) -> str:
    return off.thirds_condition(health, maximum)


def read_observe(mem, off_) -> dict:
    global off
    off = off_
    app, err = mem.read_lawn_app()
    if err:
        return {"attached": True, "phase": "unknown", "error": err}
    mode = mem.i32(app + off.APP["gameMode"])
    scene = mem.i32(app + off.APP["gameScene"])

    board = mem.read_board(app)
    base = {"attached": True, "mode": mode, "scene": scene, "game_mode": off.PROFILE}

    if board is None:
        base["phase"] = "menu"
        base["error"] = None
        return base

    base["phase"] = "board"
    base["paused"] = board["paused"]
    base["sun"] = board["sun"]
    base["level"] = board["level"]
    base["rows"] = board["rows"]
    base["columns"] = board["columns"]

    # 卡片栏
    base["cards"] = [
        {"slot": c["slot"], "type": c["type"], "name": off.plant_name(c["type"]),
         "imitates": c["imitater"] or None,
         "cost": off.PLANT_COSTS[c["type"]] if 0 <= c["type"] < len(off.PLANT_COSTS) else None,
         "ready": not c["refreshing"], "x": c["x"], "y": c["y"]}
        for c in board["cards"]
    ]

    # 植物
    base["plants"] = [
        {"type": p["type"], "name": off.plant_name(p["type"]),
         "row": p["row"], "column": p["column"],
         "phase": _plant_phase(p["type"], p["phase"]),
         "condition": plant_condition(p["type"], p["health"], p["maxHealth"]),
         "sleeping": p["sleeping"], "squished": p["squished"]}
        for p in board["plants"]
    ]

    # 僵尸
    base["zombies"] = [
        {"type": z["type"], "name": off.zombie_name(z["type"]),
         "row": z["row"], "column": z["column"],
         "phase": _zombie_phase(z["phase"]),
         "condition": _zombie_condition(z),
         "eating": z["eating"], "slowed": z["slowed"]}
        for z in board["zombies"]
    ]

    # 割草机
    base["mowers"] = [
        {"row": m["row"], "kind": "lawn_mower",
         "state": "ready" if m["state"] == 0 else ("triggered" if m["state"] == 1 else "squished")}
        for m in board["mowers"]
    ]

    # 太阳（coin 类型 4/5/6）
    sun_count = {4: 25, 5: 15, 6: 50}
    base["suns"] = [
        {"x": round(c["x"], 1), "y": round(c["y"], 1),
         "value": sun_count.get(c["type"], 0)}
        for c in board["coins"] if c["type"] in sun_count
    ]

    # 网格实体（墓碑/莲叶/花盆等）
    _GRID_NAMES = {1: "grave_stone", 2: "crater", 3: "lily_pad", 4: "flower_pot",
                   6: "vase", 7: "portal", 8: "portal"}
    base["gridItems"] = [
        {"kind": _GRID_NAMES.get(g["type"], f"grid_{g['type']}"),
         "row": g["row"], "column": g["column"]}
        for g in board["gridItems"]
    ]

    base["error"] = None
    return base


_PLANT_PHASES = {
    0: "active", 1: "potato_mine_arming", 7: "squash_watching", 8: "squash_launching",
    14: "chomper_biting", 15: "chomper_digesting", 18: "potato_mine_armed",
    20: "spikeweed_attacking", 22: "sun_shroom_small", 23: "sun_shroom_growing",
    24: "sun_shroom_large", 36: "cob_cannon_arming", 37: "cob_cannon_loading",
    38: "cob_cannon_ready", 39: "cob_cannon_firing",
}


def _plant_phase(ptype: int, state: int) -> str:
    if state == 0:
        return "potato_mine_arming" if ptype == 4 else "active"
    return _PLANT_PHASES.get(state, "unknown")


_ZOMBIE_PHASES = {
    0: "walking", 1: "dying", 2: "burned", 3: "mowed",
    11: "pole_vault_ready", 12: "pole_vaulting",
    14: "rising_from_grave", 19: "pogo_bouncing",
    29: "newspaper_reading", 30: "newspaper_enraging", 31: "newspaper_enraged",
    32: "digger_tunneling", 33: "digger_rising", 38: "digger_retreating",
    40: "dancer_snapping", 41: "dancer_dancing", 45: "dancer_rising",
    46: "dolphin_walking", 47: "dolphin_entering_pool", 48: "dolphin_riding",
    49: "dolphin_jumping", 50: "dolphin_swimming",
    51: "snorkel_walking", 52: "snorkel_entering_pool", 53: "snorkel_submerged",
    55: "zombiquarium_accelerating", 56: "zombiquarium_drifting",
    57: "zombiquarium_turning", 58: "zombiquarium_biting",
    63: "balloon_flying", 64: "balloon_popping",
    71: "boss_spawning", 72: "boss_idle",
}


def _zombie_phase(phase: int) -> str:
    return _ZOMBIE_PHASES.get(phase, "walking")


def _zombie_condition(z: dict) -> str:
    h, mx = z["health"], z["maxHealth"]
    if not mx:
        return "intact"
    if not z["hasHead"]:
        return "critical"
    # 普通僵尸：血量用分数带（简化：照搬三分档）
    return off.thirds_condition(h, mx)