# Live2D 控制算法对照与改造方案（对齐 Cortico mixer）

> 文档日期：2026-09-21
> 对照对象：可提 `Pal-AI-Lab/cortico-world-vtuber` 的 L3 混音台 `mixer.ts`
> 改造目标：把生理级算法（注视/眼头协同/ducking/环境漂移/模型无关换算）以最小侵入方式并入
> 现有 `live2d/live2d_main.py`，不破坏当前已验证的动作/口型链路。

## 0. 结论摘要

现有 `live2d_main.py` 已覆盖可提 mixer 约 60% 的"生命感"：**多频正弦呼吸、低频漂移、眨眼、mini-move、缓动 Move、口型 500ms 过渡**均已实现。真正值得改造的差距集中在可提最值钱的三点：

1. **注视模型**（ex-Gaussian 注视 + 主序列扫视 + 眼头协同）——现状用低频漂移模拟，生理感弱
2. **Sidechain ducking**——现状 move 段手动衰减呼吸，无通用"主动作占领→同参数状态被压"机制
3. **模型无关换算表**——现状 `core_params` 直接存数值，参数名硬编码 `FaceAngleX` 等，换模型要改代码

以及一个**口型改进**：可提用非对称跟随器（下行 τ 40ms、上行即时）盖住片尾悬崖，现状 `_MOUTH_TRANSITION_SEC=0.5` 对称过渡。

## 1. 现状盘点（live2d_main.py）

| 机制 | 位置 | 实现 | 可提对应 | 差距 |
|---|---|---|---|---|
| 呼吸 | `_BREATH_LAYERS` + `_breath_param_dict` | 3 频正弦 9/14/23s，角度±6° | `mixer` 无显式呼吸（交由 idle） | 同源，无需改 |
| 漂移 | `_DRIFT_LAYERS` + `_drift_param_dict` | 3 频 7/11/17s，±3° | `AMBIENT_HEAD` 1/f 漂移 | 可提漂移绑定头部三轴且低速；现状覆盖 2 类参数更全 |
| 眨眼 | `_blink_once` | 4-8s 随机，闭 150ms 过渡 | `BLINK_CLOSE_MS=75/TOTAL_MS=233`, 2.5s 均值间隔 | 时间分布可升级为 ex-Gaussian |
| 方向 Move | `move_to_direction` + `_move_impl` | 0.3 ease_out_cubic + 0.7 linear，呼吸相位锚定 | clip 关键帧 + 缓动名 | 现状是"插值到目标"，可提是"关键帧轨道"，思路不同 |
| 口型 | `set_mouth_state` + `_MOUTH_TRANSITION_SEC=0.5` | 对称线性过渡 | `MOUTH_FALL_TAU_MS=40` 非对称跟随器 | **下行悬崖会突跳 0.71→0**（可提专门修了这个） |
| mini-move | `_idle_mini_move` | 10-18s 随机 ±5-12° | gaze 目标切换 | 现状无"视线保持-扫视"，是均匀二次曲线 |
| 表情 | `set_expression` / `trigger_expression_file` | 直接开关 | `setExpression` + ExpressionActivationRequest | 同构 |
| 注入 | `_inject_params` → `inject_parameter_data` | faceFound=true, mode=set | `InjectParameterDataRequest` faceFound=false | 同构 |
| 换算 | `core_params` 存数值 + `param_ranges` | 参数名硬编码 | 模型档案 `cortico.profile.json` 换算表 | **换模型要改代码** |

## 2. 差距分析与改造优先级

### P0（推荐做，生理可感，改动局部）

**改造①：口型非对称跟随器（修正下行悬崖）**

问题：现 `set_mouth_state` 只做"目标→当前"的 0.5s 对称过渡，但当一句台词被 INSERT 拼接/打断时，`_mouth_current_open` 会被直接置位导致 `MouthOpen` 单帧从 0.71→0。

改法：在 `_inject_params` 基础上加一个**下行一阶跟随器**（可提 `MOUTH_FALL_TAU_MS=40`），嘴部参数单独走：

```python
# 新增成员
_MOUTH_FALL_TAU = 0.040          # 下行时间常数秒
_mouth_last_open = 0.0
_mouth_last_t = 0.0

def _follow_mouth(self, target: float, now: float) -> float:
    """非对称跟随：上行(开嘴)即时，下行(收嘴)按 τ 指数回落，盖住片尾/打断悬崖。"""
    if target >= self._mouth_last_open:
        self._mouth_last_open = target
    else:
        dt = now - self._mouth_last_t
        self._mouth_last_open = target + (self._mouth_last_open - target) * \
            math.exp(-dt / self._MOUTH_FALL_TAU)
    self._mouth_last_t = now
    return self._mouth_last_open
```

改造点：`set_mouth_state` 落位帧与 `update_non_moving_state` 的嘴部注入都改走 `_follow_mouth`。

**改造②：Sidechain ducking（move/动作期间压同参数字段）**

问题：现状 move 段靠 `_MOVE_BREATH_ATTENUATION=0.4` 手动衰减呼吸，但没有"动作 clip 优先→空闲状态贡献自动压低"的通用机制。当呼吸/漂移目标字段与动作目标字段重叠时，会出现打架。

改法：引入两条通道——`state_layer`（呼吸/漂移/表情常驻）与 `clip_layer`（move/动作脉冲）——当 clip_layer 某参数活跃时，state_layer 同名参数贡献压至 30%：

```python
_DUCK_LEVEL = 0.3
_DUCK_ATTACK_TAU = 0.090   # 压入 ~270ms
_DUCK_RECOVER_TAU = 0.35   # 松动 ~1s
```

改造点：`update_non_moving_state` 里呼吸/漂移计算改为"被 clip 覆盖时乘 ducking 因子"。

### P1（价值高，改动量中等，需隔离回归）

**改造③：注视模型（ex-Gaussian 保持 + 主序列扫视 + 眼头协同）**

问题：现 `_drift_param_dict` + mini-move 是"平滑二次曲线朝目标移动"，而真人眼球是"保持-突跳（fixation-saccade）"序列：长时间盯住 + 快速扫移 + 头跟随。

新增 `gaze.py`（照搬可提 mixer 生理常数）：

```python
# ex-Gaussian 注视时长 Cronin 2020: μ240/σ50/τ60ms, 下限150ms
FIX_MU_MS, FIX_SIGMA_MS, FIX_TAU_MS, FIX_MIN_MS = 240, 50, 60, 150
# 扫视主序列 Carpenter 1988: D = 2.2·A + 21 (ms)
def saccade_duration_ms(amp_deg): return 2.2 * amp_deg + 21
# 峰速时刻: 加速相约22ms 且与幅度无关
def saccade_peak_at(d) return min(0.5, max(0.25, 22 / d))
# 换目标扫视下限130ms(渲染率让步)
SHIFT_MIN_MS = 130
```

**眼头协同核心式子**（可提最精妙处，无需 VOR 状态机）：

```
眼在头内 = 总视线 − 头已转部分
```

即眼球先顶到位（顶满就停），头跟上后眼回中，实现"眼先冲→头跟→眼收"。头滞后 60ms、跟随衰减 ×0.85（欠量）。

改造点：新增 `gaze.py` + `Live2DGazeController`，取代 `_idle_mini_move` 的均匀曲线，让 mini/空闲视线走真实扫视。

### P2（结构性，建议配合换模型时做）

**改造④：模型无关语义换算表（cortico.profile.json）**

问题：现 `core_params` 存"实际数值"，且 `_BREATH_PARAM_SCALE` / `DIRECTION_TEMPLATES` 硬编码 `FaceAngleX` 等参数名。换模型（另一套 Live2D 参数命名/量程）需改代码。

改法：仿可提——三个文件彼此解耦：
- 语义参数（跨模型不变）：`FaceAngleX/Y/Z`(度)、`MouthSmile`(`[-1,1]`)、`EyeOpen`(开合度)
- 演出包 `clips.json`（bot 所有）：关键帧轨道 `[ms, value, ease]` + sustain 保持 + gaze 目标，只用语义参数
- 模型档案 `cortico.profile.json`（模型目录内）：语义参数 → 实机输入参数名/量程的换算表

注入时经 `profile.convert(语义参数, 值)` 得到实际 `{param: value}`。这样换模型只换档案不换演出包/不换代码。

## 3. 最小侵入落地方案（建议顺序）

### 阶段一（P0，约半数工时）：口型跟随器 + ducking
1. `live2d_main.py` 加 `_follow_mouth`，`set_mouth_state` 落位与 idle 嘴部注入改走它
2. 引入 `state_layer`/`clip_layer` 两通道，`update_non_moving_state` 叠 ducking
3. 回归：`--live2d` 跑 idle 30s 无跳变，D 方案动作下嘴部无悬崖

### 阶段二（P1）：注视模型
4. 新增 `live2d/gaze.py`，实现 ex-Gaussian 注视 + 主序列扫视 + 眼头协同
5. `_idle_mini_move` 改走 gaze（保留原接口签名，避免上层改动）
6. 参数可视化/日志验证扫视形态（对比现状均匀曲线）

### 阶段三（P2）：模型无关换算表
7. 抽 `cortico.profile.json` 格式，先把现有参数名/量程固化为默认档案
8. `_inject_params` 前经 `profile.convert` 换算；`core_params` 改存语义量
9. 换一个真实模型做兼容验证

## 4. 风险与注意事项
- **口型跟随器**：上行即时影响"快速回话"的爆发感，需调 `_MOUTH_FALL_TAU`（40ms 偏快，可试 60-90ms）
- **gaze 扫视**：现状 mini-move 已无 jitter 问题，改造须守住 `operation_lock` 与 fire-and-forget，避免与 move_to_direction 竞争
- **ducking**：不加状态机，只做"clip 覆盖时 state 压 30%"；回中性要斜坡，不可瞬间松
- **换算表**：属 P2 重构，改造期间所有注入路径需保持向后兼容旧数值式 core_params

## 5. 与可提能力覆盖对照

| 能力 | 现状 | 改后 |
|---|---|---|
| 呼吸多频 | ✅ | ✅ |
| 低频漂移 | ✅`_drift` | ✅ + `AMBIENT_HEAD` 绑定头部三轴 |
| 眨眼 | ✅4-8s 均匀 | ✅ ex-Gaussian 注视间隔 |
| 视线扫视 | ⚠️ 均匀曲线 | ✅ 保持-突跳 |
| 眼头协同 | ❌ 无 | ✅ 眼先头后式子 |
| Sidechain ducking | ❌ 手动衰减 | ✅ 通用 |
| 口型悬崖防护 | ❌ 会 0.71→0 | ✅ 非对称跟随器 |
| 模型无关 | ❌ 参数名硬编码 | ✅ 换算表 |
| 表情开关 | ✅ | ✅ |
| 命令反射层 | ✅command_queue | ✅（可提 reflex 等价的失败兜底） |