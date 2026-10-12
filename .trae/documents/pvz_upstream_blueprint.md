# PvZ 上游(cortico-world-pvz)可照搬蓝图

依据对 `F:\cortico-world-pvz` 全量精读所得。目标：用纯 Python(pymem) 复现可提的「内存读取→语义局面」链路，
不换版本、不用视觉回退、不做移植 DLL。一次读懂，直接照搬。

## 结论总览

可提**不做运行时扫描** gLawnApp。它把三条东西写死进构建：
1. **绝对地址**：`kGlobalLawnApp=0x007578F8`、`kLawnAppVtable=0x00710D80`、`kSecondaryVtable=0x00710F30`
2. **PE 指纹**：timestamp `0x4CC8E5F8`、entry `0x002BA65F`、SizeOfImage `0x00450000`、checksum `0x0039863F`
3. **accessor 字节签名**（injector.cpp L1450-1462）：`0x0045DE20` 等地址处的机器码，如
   `0xA1 0xF8 0x78 0x75 0x00`（mov eax,[0x007578F8] → gLawnApp，见 implant.cpp `accessor1`）

读取前先校验 SHA-256 + PE 头 + 无 reloc + 无 ASLR，全部命中才读内存。

**对你的 1.2.0.1096**：字段偏移（对象内部）与 1073 同引擎，**可直接沿用 profile.h/implant.cpp 的值**；
但绝对地址与 PE 指纹是构建相关，**必须对本构建离线逆向得到**，写进 profile 块，运行时只做校验不再扫描。

## A. 稳定部分（跨构建可照搬）

### A1. ArrayHeader + 遍历算法（implant.cpp L296-306, L1516-1531）
```cpp
struct ArrayHeader { uintptr_t block; uint32_t maxUsedCount,maxSize,freeListHead,size,nextKey; uintptr_t name; }; // 0x1C
// 校验: header.block 非0 && maxSize<=hardLimit && maxUsedCount<=maxSize && size<=maxSize
// 逐索引遍历: addr = block + index*stride; 读 stride 字节;
//   id = *(uint32_t*)(raw + objectSize);  仅当 (id&0xFFFF0000)!=0 && (id&0xFFFF)==index 才是有效实体
```
Python：pymem 一次 `read_bytes(addr, stride)`，`struct.unpack_from('<I', raw, objectSize)`。

### A2. LawnApp 读取（implant.cpp L1496-1514, ReadBoard L2741）
- 读 `[kGlobalLawnApp]` → lawnApp，非0
- 校验 `[lawnApp]==typeof(vtable)` 且 `[lawnApp+4]==secondaryVtable`（PVC 双指针）
- `[lawnApp+app::board(0x868)]` → board；board 非0

### A3. Board 标量字段（ReadBoard L2747-2759）
字段偏移取自 profile.h `board::`（含上方 L2741 起全部）：
```
background 0x5564, level 0x5568, sun 0x5578, numWaves 0x557C, mainCounter 0x5580,
currentWave 0x5594, tutorialState 0x559C, progressMeterWidth 0x5628,
boardFadeOutCounter 0x5618, nextSurvivalStageCounter 0x561C,
paused 0x17C, levelComplete 0x5614, levelAwardSpawned 0x5624,
rows = (background==2||bg==3) ? 6 : 5
```

### A4. SeedBank 卡片（ReadBoard L2799-2840）
- `[board+seedBank(0x15C)]` → bank；`[bank+0x08/0x0C]=选中区 x/y`，`[bank+0x10/0x14]=w/h`
- `[bank+packetCount(0x24)]` → packetCount（0..10）
- 遍历 slot：`addr = bank + packets(0x28) + slot*stride(0x50)`，读 stride 字节
  - `type=[+0x34]（0..255）`、packetW=[+0x10]、packetH=[+0x14]（>0 且<=200）
  - centerX = bankX + [+0x08] + [+0x30] + packetW/2；centerY = bankY + [+0x0C] + packetH/2
  - 卡片: slot,type, imitater=[+0x38], refreshCounter=[+0x24], refreshTime=[+0x28], timesUsed=[+0x4C],
    active=[+0x48]!0, refreshing=[+0x49]!0

### A5. Plant 遍历（ReadBoard L2844-2865）
对象：plantObjectSize=0x148, stride=0x14C，hardLimit=1024
```
type=[+0x24]  column=[+0x28]  row=[+0x1C]  x=[+0x08]+max([+0x10],1)/2
过滤：!byte[0x18] || byte[0x141] || byte[0x144] || type越界(>255) || col<0||col>=9 || row<0||row>=6
push: publicId, type, row, column, phase=[+0x3C], health=[+0x40], maxHealth=[+0x44],
      x=[+0x08], y=[+0x0C], width=[+0x10], height=[+0x14], pottedIndex=[+0x13C],
      sleeping=byte[+0x143]!=0, squished=byte[+0x142]!=0
```

### A6. Zombie 遍历（ReadBoard L2867-2948）
对象：zombieObjectSize=0x164, stride=0x168，hardLimit=1024；mode!=21
```
type=[+0x24]  storedRow=[+0x1C]  x=float[+0x2C]  y=float[+0x30]
visibleRect: left=floor(float[+0x2C])+int[+0x8C]; top=floor(float[+0x30])+int[+0x90]; w=[+0x94]; h=[+0x98];
  (type==14 时 top-=altitude, h 压缩, altitude=float[+0x84], phaseCounter=int[+0x68])
过滤：!byte[0x18] || byte[0xEC] || fromWave==-2/-3 || type越界 || row越界 || 不可见
column = clamp((x-40)/80, 0, 8)
push: id,type, phase=[+0x28], row, column, targetColumn=[+0x80], height=[+0x64], x,
  velocityX=float[+0x34], hitX/hitY, hitLeft/Top/Width/Height,
  renderOrder=[+0x20], health=[+0xC8], maxHealth=[+0xCC], armorHealth=[+0xD0],
  armorMax=[+0xD4], armorType=[+0xC4], shieldHealth=[+0xDC], shieldMax=[+0xE0], shieldType=[+0xD8],
  hasHead=byte[+0xBA]!=0, hasArm=byte[+0xBB]!=0, hypnotized=byte[+0xB8]!=0,
  slowed=int[+0xAC]>0, immobilized=int[+0xB0]>0||int[+0xB4]>0,
  eating=byte[+0x51]!=0, blowingAway=byte[+0xB9]!=0, hasObject=byte[+0xBC]!=0
(僵尸 speed/condition/phase 名由原生语义表计算，见 C)
```

### A7. GridItem / Coin / Mower 遍历
- gridItem：obj 0xE8/stride 0xEC，limit 128；type=[+0x08], state=[+0x0C], row=[+0x14], col=[+0x10],
  potContentType=[+0x44], seedType=[+0x40], zombieType=[+0x3C], transCounter=[+0x4C], sunCount=[+0x50]
- coin：obj 0xD0/stride 0xD8, limit 1024；type=int[+0x58]（>0 且<=64）, byte[0x18], byte[0x38] 校验；
  posX=float[+0x24], posY=float[+0x28], w=max(int[+0x10],1), h=max(int[+0x14],1)，
  renderOrder=int[+0x20], containedType=int[+0x68]
- mower：obj 0x44/stride 0x48, limit 32；row=int[+0x14], x=float[+0x08], state=int[+0x2C], type=int[+0x34], byte[+0x30],[+0x31]

### A8. publicId（implant.cpp L2708-2739）
```
同一 board+mainCounter 运行段内缓存的 raw→public 双向映射；新 run 清空，next=1。
public 候选 = raw & 0x7FFFFFFF，若冲突则取 next++。
```
Python 直接用 raw id 即可（简化实现，不做 run 段 opaqueness），或照搬该双向映射。

## B. 构建相关部分（你的 1096 版需离线逆向后写死）

| 项 | 上游 1073 值(参考) | 1096 版操作 |
|---|---|---|
| kGlobalLawnApp | 0x007578F8 | **用 CE/x64dbg 找本构建的全局指针**，写死 |
| LawnApp vtable / secondary | 0x00710D80 / 0x00710F30 | 同上，写死 |
| PE timestamp/entry/size/checksum | 0x4CC8E5F8 / 0x2BA65F / 0x450000 / 0x39863F | 用 `pefile` 读本 exe 写入 |
| accessor 签名地址 | 0x0045DE20 等 | 可在本构建里搜对应字节模式（可选加固） |
| 名字/模式/相位表 | —— | **100% 可直接照搬**（表格与构建无关） |

校验目标：`exe SHA-256`（本机 1096 = `868F8E2B…A5CA45`，可提 1073 = `9ba1c9b2…7b7198ed`）。
**改为：校验 PE 指纹 + 写死的 gLawnApp 落点一致性；无需做字节签名注入式校验（纯读不注入）。**

## C. 语义变换表（implant.cpp L1727-1960，全部可照搬）

- **ThirdDamageCondition**(health,max)：`max<=0 或 3h>=2m→intact；3h>=m→worn；else→critical`
- **PlantVisibleCondition**(type,h,m)：type==46(spikerock) 特判；type in {3,23,30,36}→thirds；else→intact
- **ArmorVisibleCondition**(zombieType,type,h,m,shield)：`m<=0→none；h<=0→(shield? critical若(27|31) else) lost；否则看 visibleDamage→thirds else intact`
- **ZombieCellsPerSecond** / **ZombieSpeedName**：相位常量表见 implant.cpp L1854-1907（含 airborne/stationary/retreating/slow/normal/fast, 一格80px, 47px/s, 100帧/s, 冰冻×0.5）
- **模式名/植物名/僵尸名/卡片名**：kPlantNames/kZombieNames/kModeNames/kCardName+ `kPlantCosts`（见 implant.cpp L1534-1614）——**整表照搬**
- **phase 名字表**：PlantPhaseName L1749、ZombiePhaseName L1776（49/96 项表）
- **lawnRows**：board rows = bg∈{2,3} ? 6 : 5

## D. 语义局面 schema（protocol.ts，已精读）

`PvzSnapshot`：executable{supported}/presentation/managed,screen,scene,mode,modeName,modeKind,
profile,lastRun,menu,dialog,seedPicker,board{runId,rows,columns,level,background,paused,sun,cursor,
fog,disclosure,cells,cards,plants,zombies,gridItems,collectibles,mowers,progress,tutorial,
allowedSpecialActions,special,boss}。
字段校验规则（validateBoard 等 L786-L1206）确定各实体可发布字段。**Python 只需产出板面核心子集**
(plants/zombies/cards/cells/sun/mowers/collectibles + paused/level/rows) 供 LLM 推理，无需完整 schema。

## E. 输入的物理动作（桥接 WM_MOUSE / 点击）

可提用 PostMessage 投 WM_MOUSE（后台可用），由 widgetManager::mouseUp/mouseDown/mouseMove 派发。
Python 侧 pvz_input 已有 cell_to_client + PostMessage，方向正确，无需改动。

## 落地顺序（本仓库）

1. `pvz_offsets.py`：改 profile 块为 `PROFILE/NAME 表(照搬 A/C)` + `BUILD 指纹(占位,待逆向)`
   删除运行时 `locate_lawn_app` 依赖，改 `MEMORY = {gLawnApp, vtable, secondary, ...}` 硬编码。
2. `pvz_memory.py`：实现 `read_lawn_app()`（读+双 vtable 校验）、`iterate_array()`（照搬 A1）、`read_board()`（A3-A7）。
3. `pvz_reader.py`：把原生视图映射到协议 schema（D）。
4. `pvz_offsets.py` 补 `env` 校验：`hasattr` 存在 gLawnApp 才允许读，否则 `err=NEED_CALIBRATION`。
5. P1 用 CE 逆向本机 1096 的 gLawnApp + vtable，回填 `BUILD/GLOBAL` → 对照画面验证 observe。