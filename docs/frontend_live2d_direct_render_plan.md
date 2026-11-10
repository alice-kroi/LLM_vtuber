# 前端直渲 Live2D 改造方案（摆脱 VTube Studio）

> 参考实现：`D:\Github\env\NagaAgent\frontend`（pixi.js + pixi-live2d-display/cubism4 + Live2DCubismCore，浏览器内直接渲染与参数驱动，完全不依赖 VTS）
> 本方案针对本项目（LLM_vtuber：后端 Python/LangGraph，前端为 `webui/index.html` 纯 HTML+JS 单文件，无 npm 构建链）
> 目标：把「VTS 程序渲染 + VTS API 控制」改为「浏览器 canvas 直渲 + 官方 Cubism Core 参数直写」，同时保留 VTS 模式作回退。

---

## 一、现状与目标

### 现状链路（VTS 依赖）

```
后端 LLM 输出动作(【语气】内容|目光方向|嘴巴状态)
        │
        ▼
live2d_controller_manager (live2d/live2d_controller_manager.py)
        │  move_to_direction / open_mouth / close_mouth / set_mouth_state
        ▼
VTS Plugin HTTP/WS (:8001)  ←── 外部程序 VTube Studio 本体负责渲染
        │
        ▼
VTS 渲染窗口（独立桌面窗口，非 WebUI 内）
```

痛点：
1. **强依赖 VTS 程序**：用户必须单独安装/启动 VTube Studio、加载模型、开插件端口，环境门槛高。
2. **控制粒度受限**：VTS API 是"方向/开关"级，无法精细写单参数（如 `ParamMouthOpenY=0.6`）。
3. **渲染与控制分离**：模型渲染在 VTS 窗口，无法与 WebUI（FastAPI 控制台）同屏、无法叠加 OBS/直播图层。

### 目标链路（前端直渲）

```
后端 LLM 输出动作(【语气】内容|目光方向|嘴巴状态)
        │
        ▼
Live2D 参数指令层（新增抽象：tone/visual_focus/mouth_state → 参数命令）
        │  WebSocket 广播 live2d_params 事件
        ▼
webui/index.html 内置 Pixi canvas
        │  加载 .model3.json / .exp3.json / live2dcubismcore.min.js
        ▼
浏览器内渲染模型，tick 循环 setParameterValueById() 直写参数
```

收益：
1. **彻底摆脱 VTS**：浏览器自带渲染，无需 VTS 程序/端口/模型加载。
2. **控制粒度到单参数**：可精细做中文口型（viseme 多参数混合）、表情 Blend 合成。
3. **与 WebUI 同屏**：模型直接画在控制台页面，可缩放/拖拽/眼神跟随，方便直播套层。
4. **复用现有产物**：LLM 动作解析（tone/visual_focus/mouth_state）逻辑不变，只换"末端执行器"。

---

## 二、技术选型（对齐 NagaAgent，但适配无构建链）

| 组件 | 用途 | 来源 |
|---|---|---|
| `pixi.js` (v7/8) | Canvas 2D/WebGL 渲染引擎 | 本地 `webui/lib/pixi.min.js` |
| `pixi-live2d-display/cubism4` | 加载/渲染 Cubism4 模型，暴露 `Live2DModel` | 本地 `webui/lib/pixi-live2d-display.min.js` |
| `live2dcubismcore.min.js` | Live2D 官方 Web 运行时（`window.Live2DCubismCore`） | 本地 `webui/lib/live2dcubismcore.min.js` |
| 模型资源 | `.model3.json` + 纹理/motion/表情 + `.exp3.json` | `webui/models/`（用户放入或上传） |

**关键适配点**：本项目前端是**单 HTML 文件（无 Vite/webpack）**，因此不引入构建链，全部用 `<script src>` 本地 lib 引入（与 NagaAgent 的 `live2dCoreLoader.ts` 动态注入思路一致，只是改为静态 script 标签 + 兜底动态注入）。

---

## 三、改造步骤

### 步骤 1：新增前端 Live2D 渲染模块（webui/ 下）

新建 `webui/live2d.js`（原生 JS 单例模块），职责对齐 NagaAgent 的 `live2dController.ts`：

- `initLive2D(canvas, modelSource)`：创建 `PIXI.Application`，`Live2DModel.from(modelSource)`，`app.stage.addChild(model)`。
- **正交通道合成**（每帧 tick）：
  - 通道1 身体摇摆（idle/thinking/talking 状态关键帧，`ParamBodyAngleX/AngleZ`）
  - 通道2 头部动作（nod/shake，`ParamAngleX/Y/EyeBallX/Y`）
  - 通道3 表情（`.exp3.json` Blend 合成，tone→happy/sad/surprise/normal）
  - 口型通道（中文 viseme 加权随机：开口/半开/齐齿/合口/撮口/闭合/停顿，多参数 `ParamMouthOpenY/JawOpen/MouthFunnel/ParamMouthForm`）
  - 目光跟随（鼠标/拖拽 → `ParamEyeBallX/Y`、`ParamAngleX/Y`）
- `applyLive2DAction({tone, visual_focus, mouth_state, hotkey})`：把后端动作映射为对应通道输入。
- `setParameterValueById(param, value)`：最终直写 `model.internalModel.coreModel`。

> 与 NagaAgent 对齐：完全复用其"多通道正交 + tick 内合并 + 单参数直写"模型，代码可参考 `D:\Github\env\NagaAgent\frontend\src\utils\live2dController.ts` 逐段移植为原生 JS。

### 步骤 2：WebUI 挂载模型容器

在 `webui/index.html`：
- 新增一个透明 `canvas#live2d-canvas` 容器（绝对定位覆盖层，可选显隐）。
- `<script>` 依次引入 `lib/live2dcubismcore.min.js` → `lib/pixi.min.js` → `lib/pixi-live2d-display.min.js` → `live2d.js`。
- 配置入口：模型源（`.model3.json` URL/相对路径）、缩放、位置、是否启用前端直渲（`LIVE2D_MODE=frontend|vts|off`）。

### 步骤 3：后端新增"Live2D 参数指令层"（不破坏现有 VTS 控制）

新建 `live2d/live2d_command.py`（抽象层，参考现有 `live2d_controller_manager.py` 的 API 面保持兼容）：

```python
@dataclass
class Live2DAction:
    tone: str            # 开心/撒娇/调皮/温柔/惊讶/疑问/普通 → 表情通道
    visual_focus: str    # 左/右/上/下/中心 → 头部+目光通道
    mouth_state: str     # open/close/half → 口型通道
    hotkey: str = ""     # 可选：自定义动作名（nod/shake 等）
```

- `ActionGenerator`/`EmotionAnalyzer`（现有）继续产出 `tone/visual_focus/mouth_state` → 组装为 `Live2DAction`。
- **双后端分发器**：
  - `LIVE2D_BACKEND=frontend`（默认新）：`Live2DAction` → WebSocket 广播 `live2d_params` 事件，前端直渲消费；后端不再连 VTS。
  - `LIVE2D_BACKEND=vts`（回退）：维持现有 `live2d_controller_manager.py` 调 VTS，一行不改。
  - `LIVE2D_BACKEND=off`：不驱动 Live2D（纯聊天）。

### 步骤 4：WebSocket 协议扩展

在 `webui/app.py` 的 `WebSocketManager` 新增广播辅助：

```python
async def broadcast_live2d_params(self, action: Dict[str, Any]):
    # {"type":"live2d","event":"live2d_params","data":{tone,visual_focus,mouth_state,hotkey}}
    await self.broadcast("live2d_params", action, "live2d")
```

后端在现有点位（`live2d_action_node` 或 `_trigger_parallel_post_process` 内）由 VTS 调用改为：
- `backend=frontend` → `ws_manager.broadcast_live2d_params(action)`
- `backend=vts` → 原有 `manager.move_to_direction(...)`

### 步骤 5：模型与资源目录

- 建 `webui/models/<角色>/` 放置 `.model3.json`、贴图、motion、`.exp3.json`、`actions.json`（可选自定义动作）。
- 模型源配置项：`config.ini [browser]` 或新增 `[live2d] model_source = /models/<角色>/<角色>.model3.json`（不走硬编码，遵循环境变量>config.ini>默认值）。
- `lib/` 三件套放入 `webui/lib/`，与项目一起分发（live2dcubismcore 为官方免费运行时，随包分发合规）。

---

## 四、改造前后数据流对比

| 环节 | 改造前(VTS) | 改造后(frontend) |
|---|---|---|
| 渲染载体 | VTS 桌面程序窗口 | 浏览器 canvas |
| 控制通道 | VTS HTTP/WS :8001 | WebSocket `/ws` 广播 live2d_params |
| 参数粒度 | 方向/开合（API 级） | 单参数直写（`setParameterValueById`） |
| 表情 | 依赖 VTS 预设动作 | `.exp3.json` Blend 合成（原生） |
| 口型 | VTS 自动 | 中文 viseme 多参数混合（可控） |
| 目光 | VTS tracking | 前端鼠标/指针映射 |
| 启动依赖 | 需装 VTS + 开插件 + 加载模型 | 只需浏览器（模型随包） |

---

## 五、分阶段落地顺序

1. **阶段A（渲染跑通）**：`webui/lib` 三件套 + `live2d.js` 最小实现（加载模型+静态显示+呼吸/眨眼），`index.html` 挂 canvas。验证：控制台页面出现可动模型。
2. **阶段B（动作驱动）**：`live2d/live2d_command.py` + 双后端分发 + `broadcast_live2d_params` + 前端 tick 通道合成（表情/口型/目光）。验证：发一条 LLM 回复，前端模型按 tone/目光/嘴巴动作。
3. **阶段C（回退与打磨）**：`LIVE2D_BACKEND` 三态切换验证（frontend/vts/off）；模型配置化（上传/选择）；跟随/拖拽/缩放。
4. **阶段D（直播集成）**：模型层透明可叠加，供 OBS/直播前端取用。

---

## 六、风险与注意点

- **pixi-live2d-display 与 pixi.js 版本兼容**：需锁定匹配版本（cubism4 入口依赖 pixi v7 为主；若用 pixi v8 需确认插件兼容）。**本方案倾向固定 pixi v7 + 对应 pixi-live2d-display**，减少适配成本。
- **WebGL 环境**：浏览器需支持 WebGL；无 GPU 环境可退 antialias/降分辨率（`ssaa` 缩放已在 NagaAgent 验证）。
- **后端并发广播**：`broadcast_live2d_params` 走现有 `_ws_manager.broadcast`，不引入新锁；若高频刷帧，可前端侧节流（requestAnimationFrame 天然节流）。
- **模型资源版权/合规**：`live2dcubismcore.min.js` 官方许可随包；用户模型遵循其自身许可。
- **回退保障**：`LIVE2D_BACKEND=vts` 完整保留旧路径，改造期间随时可切回，零风险回滚。

---

## 七、验证方式

1. 启动 `python main.py`，浏览器打开 `http://localhost:8081/`，`LIVE2D_BACKEND=frontend` 时页面出现模型并呼吸/眨眼。
2. 通过 `/api/send` 注入含动作的回复，观察模型按 `tone`（表情）、`visual_focus`（目光）、`mouth_state`（口型）联动；WebSocket 侧可见 `type:live2d / event:live2d_params` 消息。
3. `LIVE2D_BACKEND=vts` 重启，验证仍走原 VTS 路径（若装有 VTS）。
4. `LIVE2D_BACKEND=off` 重启，验证纯聊天无 Live2D 副作用。
5. 对比两模式下 LLM 动作解析产物（tone/visual_focus/mouth_state）一致，仅末端执行器不同。

---

## 关键参考文件

- NagaAgent：`frontend/src/components/Live2dModel.vue`（PIXI 渲染挂载）
- NagaAgent：`frontend/src/utils/live2dController.ts`（通道合成+单参数直写，核心参考）
- NagaAgent：`frontend/src/utils/live2dCoreLoader.ts`（Cubism Core 注入）
- 本项目：`live2d/live2d_controller_manager.py`（保留，作为 VTS 回退后端）
- 本项目：`webui/app.py` `WebSocketManager`（扩展 `broadcast_live2d_params`）
