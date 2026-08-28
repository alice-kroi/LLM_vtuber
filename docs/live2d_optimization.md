# Live2D 深入优化方案（设计思考）

> 目标：从"能动"到"有表情、会说话、有情绪"
> 本文档保留设计思考和两条路线的架构设计，**不含实施计划**（已完成项见 `live2d_design.md`）。

---

## 0. 现状诊断

### 0.1 已实现的控制（路线 A P0）

| 能力 | 实现方式 | 代码位置 |
|------|----------|----------|
| 9 方向目光 | InjectParameterData (FaceAngleX/Y/Z + FacePositionX/Y) | `live2d_main.py:DIRECTION_TEMPLATES` |
| 嘴巴开合 | InjectParameterData (MouthOpen) | `live2d_main.py:open_mouth/close_mouth` |
| 呼吸/漂移 | 多频正弦叠加参数值 | `live2d_main.py:_BREATH_LAYERS` |
| 动作间锁 | asyncio.Lock + 0.3s tick | `live2d_main.py:operation_lock` |
| 表情系统 | EXPRESSION_PARAM_MAP 参数映射 + TONE_TO_EXPRESSION 自动推断 | `LLM/live2d_models.py` |
| VTS 参数快照 | dump_vts_current_params 对比期望值 vs 实际值 | `live2d_main.py` |

### 0.2 VTS API 未使用的能力（可解锁）

VTube Studio 通过 WebSocket API (ws://localhost:8001) 暴露了以下能力，**当前代码只用了 InjectParameterData 和 GetTrackingParameters**：

| VTS API | 消息类型 | 说明 | 当前状态 |
|---------|----------|------|----------|
| 参数注入 | InjectParameterDataRequest | ✓ 已使用 | 注入表情/方向/呼吸参数 |
| 参数查询 | InputParameterListRequest | ✓ 已使用 | 获取人类可读参数名（FaceAngleX 等） |
| 触发热键 | HotkeyTriggerRequest | 触发模型预设的动作/表情 | ❌ 未用 |
| 表情激活 | ExpressionActivationRequest | 激活 .exp3.json 表情文件 | ❌ 未用 |
| 物理参数 | SetCurrentModelPhysicsRequest | 调整风、物理强度 | ❌ 未用 |
| 模型移动 | MoveModelRequest | 整体移动/旋转/缩放 | ❌ 未用 |
| 加载道具 | ItemLoadRequest | 加载帽子/武器/特效 | ❌ 未用 |
| 颜色叠加 | ColorTintRequest | ArtMesh 染色 | ❌ 未用 |
| 后处理 | PostProcessingUpdateRequest | Bloom/色调/景深 | ❌ 未用 |
| 订阅事件 | EventSubscriptionRequest | 监听 VTS 状态变化 | ❌ 未用 |
| 获取模型数据 | CurrentModelDataRequest | 获取 ArtMesh/Expression/Motion 列表 | ❌ 未用 |

### 0.3 LLM 侧的扩展空间

当前 Live2DResponse Schema 已扩展支持表情、热键、嘴型强度等字段：

```python
class Live2DResponse(BaseModel):
    tone: Literal[ALLOWED_TONES]       # 17 种固定语气
    content: str                        # 回答内容
    visual_focus: Direction | None      # 9 方向
    mouth_state: Literal["open","close"] | None
    mouth_intensity: float | None       # 嘴巴开合度 0.0-1.0
    expression: Literal[...] | None      # 表情
    expression_duration: float = 2.0
    expression_intensity: float = 0.8
    hotkey: str | None                  # VTS 热键触发
    hotkey_duration: float = 3.0
    param_overrides: dict[str, float] | None  # 直接覆盖参数
    physics_wind: float | None          # 风强度 0-100
    physics_strength: float | None      # 物理强度 0-100
    model_position: tuple[float, float] | None  # (x, y) -1~1
    model_rotation: float | None        # -360~360
    model_scale: float | None           # 0.5~2.0
```

---

## 路线 A：VTS 增强（推荐，已完成 P0）

不更换底层，完全利用 VTS 已有的 API 能力，增量式增强。

### A-1 模型资源自动发现

```python
# 启动时调用一次，缓存到 self.model_capabilities
async def discover_model_resources(self):
    """查询当前模型的全部控制资源"""
    # 1. 获取所有 Live2D 参数（不止 FaceAngleX/Y/Z）
    params = await self.api.get_tracking_parameters()
    # → EyeOpenLeft/Right, MouthOpenY, Brows, MouthSmileL/R, ...
    
    # 2. 获取所有热键（模型预设动作）
    hotkeys = await self.api.get_hotkeys_in_model()
    # → 可能包含: "眨眼" "生气" "开心" "挥手" "待机动作" ...
    
    # 3. 获取所有表情文件
    expressions = await self.api.get_expression_state()
    # → 模型自带的 .exp3.json 列表
```

### A-2 表情→参数映射（无需热键也能实现）

如果模型没有预设表情热键，可以通过参数组合模拟：

```python
EXPRESSION_PARAM_MAP = {
    "smile":    {"MouthForm": 1.0, "MouthOpenY": 0.3, "Brows": -0.3},
    "angry":    {"Brows": +0.5, "FaceAngleZ": -2, "MouthOpen": -0.3},
    "sad":      {"Brows": +0.6, "MouthForm": -0.5},
    "surprised":{"EyeOpenLeft": 1.0, "EyeOpenRight": 1.0, "MouthOpenY": 0.6},
    "shy":      {"Brows": +0.4, "EyeOpenLeft": 0.6, "EyeOpenRight": 0.6},
    "wink_left":{"EyeOpenLeft": 0.0, "EyeOpenRight": 1.0},
}
```

**关键点**：表情参数只写入表情专属参数（Brows, EyeOpen*, MouthSmile*），不覆盖方向/呼吸参数。

### A-3 嘴型同步设计（P1，待实现）

**现状**：嘴巴只有 open/close 两态，说话期间持续 open。

**目标**：嘴巴开合幅度随 TTS 音频能量实时变化。

```python
class Live2DMain:
    def __init__(self, ...):
        self._mouth_energy = 0.0
        self._mouth_energy_lock = asyncio.Lock()
    
    async def update_mouth_energy(self, energy: float):
        """由 TTS 回调调用，平滑跟随避免抖动"""
        async with self._mouth_energy_lock:
            self._mouth_energy = self._mouth_energy * 0.6 + energy * 0.4
```

在 idle 循环中读取 energy 值：
```python
# idle_movement tick 时
async with self._mouth_energy_lock:
    energy = self._mouth_energy
params["MouthOpen"] = base_mouth_target * energy + idle_breath_mouth
```

RMS 能量计算（TTS 侧）：
```python
# 对 PCM 数据分 100ms 窗口
rms = sqrt(mean(sample_window ** 2))
energy = min(1.0, rms / 3000)
```

### A-4 情绪状态机（P2，设计中）

```python
class EmotionStateMachine:
    STATES = {
        "neutral": {"breath_period": 9.0, "blink_interval": (3, 7),
                     "gaze_distribution": {"center": 0.3, "random": 0.7}},
        "happy":   {"breath_period": 7.0, "blink_interval": (2, 5),
                     "gaze_distribution": {"center": 0.5, "left_right": 0.5},
                     "expression": "smile"},
        "angry":   {"breath_period": 5.0, "blink_interval": (4, 10),
                     "gaze_distribution": {"center": 0.8, "down": 0.2},
                     "expression": "angry"},
        "sad":     {"breath_period": 14.0,
                     "gaze_distribution": {"down": 0.7, "center": 0.3},
                     "expression": "sad"},
    }
    
    def transition(self, target_emotion: str):
        """平滑过渡到新情绪，同时调整呼吸、目光、表情（2-3 秒过渡）"""
```

---

## 路线 B：独立渲染（抛弃 VTS，自建 Live2D Web 服务）

### 为什么考虑路线 B？

| VTS 的限制 | 独立渲染的优势 |
|------------|----------------|
| 必须运行额外桌面程序 | 单一进程，随 main.py 启停 |
| WebSocket 协议开销（每帧 JSON） | 直接 WebGL 渲染，零延迟 |
| 参数集由模型作者决定 | 可自定义参数、混合、叠加 |
| 无法在云端运行 | 可部署到服务器 headless 渲染 |
| 无法捕捉模型画面到主程序 | 直接 WebSocket 推帧/截图 |

### 推荐技术栈：easy-live2d + WebSocket 桥接

```
┌─────────────────────────────────────────────────────────────────┐
│  main.py (FastAPI 端)                                           │
│  LangGraph → live2d_node → Live2DController                    │
│                                  │                               │
│                                  │ WebSocket                     │
│                                  ▼                               │
│  ┌─────────────────────────────────────────────────┐            │
│  │  live2d_viewer.html (浏览器页面)                 │            │
│  │  easy-live2d + WebGL 渲染 Live2D 模型            │            │
│  │  ┌─ param_update(x, y, z, mouth, brow...)        │            │
│  │  ├─ trigger_motion(name)                        │            │
│  │  ├─ trigger_expression(name, fade_time)         │            │
│  │  ├─ set_physics(strength, wind)                  │            │
│  │  ├─ capture_frame(base64) → 回传截图              │            │
│  │  └─ mouth_energy(0.0-1.0)  ← TTS 音频能量         │            │
│  └─────────────────────────────────────────────────┘            │
│  浏览器可以是: 内置 Playwright / 独立窗口 / OBS 浏览器源          │
└─────────────────────────────────────────────────────────────────┘
```

### 统一控制器设计（路线 A/B 共存）

```python
class Live2DController:
    BACKENDS = {
        "vts": VTSBackend,         # 路线 A - 现有 VTube Studio
        "web": WebGLBackend,       # 路线 B - 自建 Web 渲染
        "none": NullBackend,       # 禁用
    }
    
    def __init__(self, backend: str = "vts"):
        self.backend = self.BACKENDS[backend]()
    
    # 统一 API
    async def set_gaze(self, direction, duration): ...
    async def set_expression(self, expression, duration): ...
    async def set_physics(self, wind, strength): ...
    async def trigger_motion(self, motion_name): ...
    async def set_mouth_energy(self, energy): ...
    async def get_model_resources(self) -> dict: ...
```

config.ini 切换：
```ini
[live2d]
backend = vts                   # vts / web / none
```
