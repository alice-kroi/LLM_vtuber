#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PvZ 偏移与语义表（完整照搬上游 cortico-world-pvz `profile.h` + `implant.cpp` 的确认值）。

设计（对齐上游"硬编码 + 校验"而非运行时扫描）：
- 字段偏移（APP/BOARD/SEEDBANK/DATAARRAY/实体内部）为**同引擎稳定值**，直接照搬 profile.h。
- gLawnApp/vtable 为**绝对地址、绑定构建**：放在 GLOBAL。当前 Steam 1.2.0.1096 尚未一次校准，
  留 None。read_observe 在 GLOBAL.lawnApp 为空时返回 needs_calibration，绝不扫描。
- PE/EXE 指纹记录真实构建值，供校验/诊断。
"""

from __future__ import annotations

# ---- 构建标识（当前 Steam 版实测）----
PROFILE = "steam-1.2.0.1096"
EXECUTABLE_NAME = "PlantsVsZombies.exe"
EXECUTABLE_SHA256 = "868F8E2BAB0D6A7EF8AFC4C5960C608ECCEF82BD086BD6E0C0E2670199A5CA45"

BUILD = {
    # 当前 1096 构建实测（从 PE 头读出；与上游 1073 差异显著，见校验）
    "peTimestamp": 0x48ECE74,
    "peEntryPoint": 0xE4F5A,
    "peSizeOfImage": 0x204000,
    "peChecksum": 0x4,
    "imageBase": 0x00400000,
}

# ---- 全局绝对地址（绑定构建；一次校准后写死，运行时不扫描）----
# 上标参考:上游 GOTY-APAC 1073 = lawnApp 0x007578F8, vtable 0x00710D80, secondary 0x00710F30
# 当前 1096 版:用 CE 值搜索 sun→board→lawnApp→gLawnApp 回溯后回填。
GLOBAL = {
    "lawnApp": None,     # gLawnApp 全局指针地址（指向 LawnApp 堆对象）
    "vtable": None,      # LawnApp 主 vtable（[lawnApp]）
    "secondary": None,   # LawnApp 次 vtable（[lawnApp+4]）
}

# ---- 托管窗口客户区（PvZ 固定 800x600；输入用）----
MANAGED_CLIENT_W = 800
MANAGED_CLIENT_H = 600

# ---- LawnApp 字段偏移（profile.h `app::`）----
APP = {
    "widgetManager": 0x320,
    "ddInterface": 0x3A0,
    "profileManager": 0x948,
    "board": 0x868,
    "titleScreen": 0x86C,
    "gameSelector": 0x870,
    "seedChooser": 0x874,
    "awardScreen": 0x878,
    "creditScreen": 0x87C,
    "challengeScreen": 0x880,
    "zenGarden": 0x93C,
    "gameMode": 0x918,
    "gameScene": 0x91C,
    "playerInfo": 0x94C,
    "boardResult": 0x9A8,
}

# ---- Board 字段偏移（profile.h `board::`；另含 implant.cpp ReadBoard 直读偏移）----
BOARD = {
    "zombies": 0xA8,
    "plants": 0xC4,
    "projectiles": 0xE0,
    "coins": 0xFC,
    "mowers": 0x118,
    "gridItems": 0x134,
    "cursorObject": 0x150,
    "seedBank": 0x15C,
    "menuButton": 0x160,
    "storeButton": 0x164,
    "cutScene": 0x174,
    "challenge": 0x178,
    "paused": 0x17C,
    "gridSquareType": 0x180,
    "fogGrid": 0x4E0,
    "fogOffset": 0x5E8,
    "plantRows": 0x5F0,
    "iceMinX": 0x624,
    "iceTimer": 0x63C,
    "zombieWaves": 0x6CC,
    "zombieAllowed": 0x54EC,
    "background": 0x5564,
    "level": 0x5568,
    "sun": 0x5578,
    "numWaves": 0x557C,
    "mainCounter": 0x5580,
    "currentWave": 0x5594,
    "tutorialState": 0x559C,
    "levelComplete": 0x5614,
    "showShovel": 0x5609,
    "levelAwardSpawned": 0x5624,
    "progressMeterWidth": 0x5628,
    "cobCannonCursorDelay": 0x576C,
    "boardFadeOutCounter": 0x5618,
    "nextSurvivalStageCounter": 0x561C,
}

# ---- SeedBank（seedBank:: + ReadBoard 读取所需偏移）----
SEEDBANK = {
    "x": 0x08,
    "y": 0x0C,
    "width": 0x10,
    "height": 0x14,
    "packetCount": 0x24,
    "packets": 0x28,
    "packetStride": 0x50,
}

# 卡片 packet 内部偏移（implant.cpp L2811-2838）
PACKET = {
    "relX": 0x08,     # 相对 bank x 的附加偏移（centerX 用）
    "width": 0x10,
    "height": 0x14,
    "refreshCounter": 0x24,
    "refreshTime": 0x28,
    "offsetX": 0x30,  # centerX 计算的另一分量
    "type": 0x34,
    "imitater": 0x38,
    "active": 0x48,
    "refreshing": 0x49,
    "timesUsed": 0x4C,
}

# ---- DataArray（实体数组：header/对象大小/步长）----
DATAARRAY = {
    "headerSize": 0x1C,
    "plantObjectSize": 0x148, "plantStride": 0x14C,
    "zombieObjectSize": 0x164, "zombieStride": 0x168,
    "coinObjectSize": 0xD0, "coinStride": 0xD8,
    "mowerObjectSize": 0x44, "mowerStride": 0x48,
    "gridItemObjectSize": 0xE8, "gridItemStride": 0xEC,
}

# ---- 实体内部字段偏移（implant.cpp ReadBoard 各回调，相对每个对象基址）----
PLANT = {
    "x": 0x08, "y": 0x0C,
    "width": 0x10, "height": 0x14,
    "alive": 0x18,          # uint8, must 非0
    "row": 0x1C,
    "type": 0x24, "column": 0x28,
    "phase": 0x3C, "health": 0x40, "maxHealth": 0x44,
    "pottedIndex": 0x13C,
    "excluded": 0x141, "excluded2": 0x144,  # uint8, must ==0
    "squished": 0x142, "sleeping": 0x143,   # uint8 标志
}

ZOMBIE = {
    "alive": 0x18,          # uint8
    "row": 0x1C,
    "renderOrder": 0x20,
    "type": 0x24, "phase": 0x28,
    "x": 0x2C, "y": 0x30,   # float
    "velocityX": 0x34,      # float
    "excluded": 0xEC,       # uint8 must ==0
    "height": 0x64,
    "phaseCounter": 0x68,
    "fromWave": 0x6C,
    "targetColumn": 0x80,
    "altitude": 0x84,       # float（气球/rising）
    "hitLeftAdd": 0x8C,
    "hitTopAdd": 0x90,
    "hitWidth": 0x94,
    "hitHeight": 0x98,
    "slowedCounter": 0xAC,
    "immobilizedA": 0xB0, "immobilizedB": 0xB4,
    "hypnotized": 0xB8, "blowingAway": 0xB9,
    "hasHead": 0xBA, "hasArm": 0xBB, "hasObject": 0xBC,
    "armorType": 0xC4,
    "health": 0xC8, "maxHealth": 0xCC,
    "armorHealth": 0xD0, "armorMax": 0xD4,
    "shieldType": 0xD8, "shieldHealth": 0xDC, "shieldMax": 0xE0,
    "eating": 0x51,         # uint8
}

GRIDITEM = {
    "type": 0x08, "state": 0x0C,
    "column": 0x10, "row": 0x14,
    "visible": 0x20,        # uint8 must ==0
    "x": 0x24, "y": 0x28,   # float
    "zombieType": 0x3C,
    "seedType": 0x40,
    "potContentType": 0x44,
    "transparentCounter": 0x4C,
    "sunCount": 0x50,
}

COIN = {
    "alive": 0x18,          # uint8
    "renderOrder": 0x20,
    "x": 0x24, "y": 0x28,   # float
    "excluded": 0x38,       # uint8
    "width": 0x10, "height": 0x14,
    "collected": 0x50,      # uint8
    "type": 0x58,
    "containedType": 0x68,
}

MOWER = {
    "x": 0x08,              # float
    "row": 0x14,
    "state": 0x2C,
    "gone": 0x30, "gone2": 0x31,  # uint8
    "type": 0x34,
}

# ---- 人类语言名/费用表（implant.cpp L1534-1614，与构建无关，整表照搬）----
PLANT_NAMES = [
    "peashooter", "sunflower", "cherry_bomb", "wall_nut", "potato_mine", "snow_pea",
    "chomper", "repeater", "puff_shroom", "sun_shroom", "fume_shroom", "grave_buster",
    "hypno_shroom", "scaredy_shroom", "ice_shroom", "doom_shroom", "lily_pad", "squash",
    "threepeater", "tangle_kelp", "jalapeno", "spikeweed", "torchwood", "tall_nut",
    "sea_shroom", "plantern", "cactus", "blover", "split_pea", "starfruit", "pumpkin",
    "magnet_shroom", "cabbage_pult", "flower_pot", "kernel_pult", "coffee_bean", "garlic",
    "umbrella_leaf", "marigold", "melon_pult", "gatling_pea", "twin_sunflower",
    "gloom_shroom", "cattail", "winter_melon", "gold_magnet", "spikerock", "cob_cannon",
    "imitater", "explod_o_nut", "giant_wall_nut", "sprout", "leftpeater"]

ZOMBIE_NAMES = [
    "zombie", "flag_zombie", "conehead", "pole_vaulting", "buckethead", "newspaper",
    "screen_door", "football", "dancing", "backup_dancer", "ducky_tube", "snorkel",
    "zomboni", "bobsled", "dolphin_rider", "jack_in_the_box", "balloon", "digger",
    "pogo", "yeti", "bungee", "ladder", "catapult", "gargantuar", "imp", "dr_zomboss",
    "pea_head", "wall_nut_head", "jalapeno_head", "gatling_head", "squash_head",
    "tall_nut_head", "giga_gargantuar"]

PLANT_COSTS = [
    100, 50, 150, 50, 25, 175, 150, 200, 0, 25, 75, 75, 75, 25, 75, 125, 25, 50,
    325, 25, 125, 100, 175, 125, 0, 25, 125, 100, 125, 125, 125, 100, 100, 25,
    100, 75, 50, 100, 50, 300, 250, 150, 150, 225, 200, 50, 125, 500, 0]

# ---- 语义条件/相位/速度变换辅助 ----
def plant_name(t: int) -> str:
    return PLANT_NAMES[t] if 0 <= t < len(PLANT_NAMES) else f"plant_{t}"

def zombie_name(t: int) -> str:
    return ZOMBIE_NAMES[t] if 0 <= t < len(ZOMBIE_NAMES) else f"zombie_{t}"

def thirds_condition(health, maximum) -> str:
    """implant.cpp L1727 ThirdsDamageCondition。返回 intact/worn/damaged/critical。"""
    if not maximum or health * 3 >= maximum * 2:
        return "intact"
    if health * 3 >= maximum:
        return "worn"
    return "critical"