# Live2D 设计文档（路线 A 已实现）

> 本文档描述 Live2D 控制的设计理念、当前实现状态和已知约束。

## 1. 核心设计理念

Live2D 控制的本质是**高频参数注入**。

VTube Studio 启用摄像头追踪时，会以约 20Hz 的频率自动覆盖参数值。要让我们的指令生效，
注入频率必须高于追踪频率（**≥ 每 0.5s 一次**），且必须设置 `faceFound=True` 让 VTS 相信
"追踪找到了有效人脸"，从而接受我们的值。

```
          VTS 内部参数更新源
          ┌────────────────────────────────┐
          │ 摄像头追踪  (默认 ~20Hz)       │
          │  ↓ 覆盖                        │
          │ VTS 最终参数值                 │
          │  ↑ 我们注入  (目标 ≥ 3.3Hz)   │
          │  faceFound=True, mode="set"   │
          └────────────────────────────────┘
```

## 2. 参数名称的两个世界（重要）

VTube Studio 有两种参数列表 API，返回的参数名**完全不同**：

| API | 返回参数名 | 用途 |
|------|------|------|
| `InputParameterListRequest` | `FaceAngleX`, `MouthOpen`, `EyeOpenLeft` ... | **我们用这个**：人类可读，和注入用的 ID 一致 |
| `Live2DParameterListRequest` | `Param158`, `Param159` ... | 模型内部占位名，**不要用这个做快照** |

> 曾因此踩过坑：快照方法用了 `Live2DParameterListRequest`，结果日志里全是占位名，
> 完全无法对比期望值。后改为 `get_tracking_parameters()` 即 `InputParameterListRequest`。

## 3. 常态循环（idle_movement）

连接建立后立即启动一个后台协程，每 `_MOVE_STEP_SEC`（≈ 0.3s）tick 一次：

```python
async def idle_movement(self):
    while self.running:
        async with self.operation_lock:       # 串行化 WebSocket 发送
            now = time.time()
            params = self.core_params.copy()   # 基础值（方向/表情参数）
            params += breath_wave(now)         # 呼吸 ±4.8°, 0.15Hz (T≈6.67s)
            params += drift_wave(now)          # 漂移 ±2°,  0.33Hz (T≈3s)
            await self.inject_parameter_data(
                params, face_found=True, mode="set"
            )
        await asyncio.sleep(_MOVE_STEP_SEC)
```

呼吸和漂移使用**多频正弦叠加**（2-3 个不同频率叠加模拟不规则自然动作）。

## 4. 目光移动（move_to_direction）

9 个预设方向：`center`, `up`, `down`, `left`, `right`, `upleft`, `upright`, `downleft`, `downright`。

执行流程：
1. 设置 `self.current_direction = target`
2. 设置 `self.core_params` 中的方向参数到目标值（如 `FaceAngleX=+12°` 表示向左看）
3. 由**下一个 idle tick** 自然叠加呼吸/漂移后注入（不需要单独发一次）
4. 过渡时长由 `_MOVE_STEP_SEC` 决定（实际是渐进式多帧过渡，由 `update_non_moving_state` 内的插值实现）

## 5. 表情系统（A-P0 已实现）

### 5.1 触发方式

- **显式指定**：LLM 在回复中返回 `【语气=开心 表情=开心】`
- **自动推断**：LLM 返回语气词 → `TONE_TO_EXPRESSION` 映射表 → 模糊匹配 fallback

### 5.2 实现方式

表情通过组合基础参数值模拟，而非调用 VTS 的表情文件系统。
`EXPRESSION_PARAM_MAP` 定义了每种表情对应哪些参数偏移：

```python
EXPRESSION_PARAM_MAP = {
    "开心": [("FaceAngleZ", +3), ("Brows", -0.3), ("EyeOpenLeft", +0.2)],
    "生气": [("Brows", +0.5), ("FaceAngleZ", -2), ("MouthOpen", -0.3)],
    ...
}
```

设置时：
1. 用快照确认当前参数基线
2. 叠加表情偏移 + 截断到合法范围
3. 持 `operation_lock` 注入
4. 更新 `_last_injected_values` 让快照能对比
5. 表情参数写入 `core_params`，让 idle 循环后续持续保持

### 5.3 与常态动作的叠加

表情参数**只写入表情专属参数**（Brows, EyeOpen*, MouthSmile*），
不覆盖方向/呼吸参数。idle 循环每次 tick 都会把 `core_params`（含表情）+ 呼吸 + 漂移
一起注入，实现表情与常态动作自然叠加。

## 6. 诊断快照（dump_vts_current_params）

为排查"参数到底有没有真的到 VTS"而设计的调试工具：

```python
async def dump_vts_current_params(self, label=""):
    resp = await self.api.get_tracking_parameters()  # InputParameterListRequest
    all_params = merge(defaults + customs)
    for pname in key_params:
        actual = all_params.get(pname)              # VTS 实际值
        expected = self._last_injected_values.get(pname)  # 我们最近注入的值
        delta = actual - expected
        marker = "✓" if abs(delta) < 0.1 else "⚠"
        log(f"FaceAngleX={actual:+.3f}(inj={expected:+.3f}{marker})")
```

每 200 tick（~60s）在 idle 循环里自动跑一次，表情/目光动作后立即跑一次。

## 7. 操作锁（operation_lock）

Live2D 控制所有 WebSocket 发送/接收都必须持锁：

```python
# idle 循环持锁
async with self.operation_lock:
    await self.update_non_moving_state(now)

# 表情设置持锁
async with self.operation_lock:
    await self._inject_params(items)
    await self.dump_vts_current_params()
```

不持锁会导致两条路径的 WebSocket 发送/接收交错，
读到对方的响应 → JSON 解析失败或参数错乱。

## 8. 已知约束 & 坑

| # | 坑 | 说明 |
|---|------|------|
| 1 | **VTS 模型加载延迟** | WebSocket 能连上但 modelLoaded=false，要等 3-5 秒 |
| 2 | **追踪抢回参数** | 注入频率 < 1s 会被摄像头追踪覆盖 |
| 3 | **两种参数名混淆** | InputParameterListRequest ≠ Live2DParameterListRequest |
| 4 | **faceFound=False** | 设 false 时注入会被 VTS 拒绝；必须 faceFound=True |
| 5 | **operation_lock 竞态** | 表情设置和 idle 循环必须串行访问 WebSocket |
| 6 | **语气词映射不全** | LLM 可能返回"平和""普通"等未收录语气 → 表情推断失败 |
| 7 | **gitignore 误排除** | 曾把 `tool/browser_tool.py` 和 `live2d/vtuber_studio_info.py` 排除，已修复 |

## 9. 路线 A 状态

| 阶段 | 功能 | 状态 |
|------|------|------|
| A-P0 | 参数注入框架（idle + move） | ✅ 已完成 |
| A-P0 | 表情系统（语气推断 + 参数映射） | ✅ 已完成 |
| A-P0 | VTS 参数快照诊断 | ✅ 已完成 |
| A-P1 | 热键触发表情文件（ActivateExpression） | 📋 待实现 |
| A-P1 | 模型资源自动发现（GetExpressionList / GetHotkeys） | 📋 待实现 |
| 路线 B | 脱离 VTS，直接 Live2D Cubism SDK | ❌ 暂不考虑 |
