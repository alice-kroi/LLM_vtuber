# ============================================================
# LLM_v2/domains/live2d/live2d.py — Live2D 虚拟形象能力域
# ============================================================
# 将 V1 的 LLM_v2/io/live2d_listener.py（事件监听器）升级为能力域：
#   - 类型：Actuator（提供工具 + 监听 LIVE2D_EXPRESSION / LIVE2D_MOTION 事件）
#   - 工具：live2d_status / live2d_set_expression / live2d_trigger_motion
#   - 事件：outbound 监听 LIVE2D_EXPRESSION → 切表情；LIVE2D_MOTION → 触发动作
#   - 生命周期：start() 注册监听 + 懒加载 VTS API；stop() 注销监听 + 断开
#   - default_enabled=False（默认不启动，作为能力挂载，可由 WebUI/配置热启）
# ============================================================

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    Actuator,
    CapabilityType,
    tool_schema,
)

logger = logging.getLogger(__name__)

LIVE2D_TOOL_NAMES = [
    "live2d_status",
    "live2d_set_expression",
    "live2d_trigger_motion",
]

LIVE2D_TOOLS_SCHEMA: list = [
    tool_schema(
        "live2d_status",
        "查询 VTube Studio 连接状态 / 当前模型 / 表情列表。",
    ),
    tool_schema(
        "live2d_set_expression",
        "切换 Live2D 表情（VTS expression 文件名或 persona 映射键）。",
        {
            "expression": {"type": "string", "description": "表情文件名（如 smile_01）或情绪键（excited/angry/sad/calm/neutral）"},
            "active": {"type": "boolean", "description": "是否激活（默认 true）"},
            "fade_time": {"type": "number", "description": "过渡时长秒（默认 0.25）"},
        },
        required=["expression"],
    ),
    tool_schema(
        "live2d_trigger_motion",
        "触发 VTube Studio 热键动作（motion_id 即 hotkey id）。",
        {
            "motion_id": {"type": "string", "description": "VTS 热键 id"},
        },
        required=["motion_id"],
    ),
]


class ParamEngine:
    """逐帧嘴型 / 目光参数（纯计算，不直接碰 VTS）。

    输入：
        - 指针归一化坐标 nx/ny（-1..1，屏幕向下为正）
        - 当前情绪象限
        - TTS_SPEAK 触发的说话窗口
    输出：
        VTS 跟踪参数：EyeLeftX/Y、EyeRightX/Y、MouthOpen、MouthSmile
    """

    EYE_LEFT_X = "EyeLeftX"
    EYE_LEFT_Y = "EyeLeftY"
    EYE_RIGHT_X = "EyeRightX"
    EYE_RIGHT_Y = "EyeRightY"
    MOUTH_OPEN = "MouthOpen"
    MOUTH_SMILE = "MouthSmile"
    # 兼容旧调用名
    EYE_X = EYE_LEFT_X
    EYE_Y = EYE_LEFT_Y

    MOOD_FORM = {
        "exuberant": 0.20, "dependent": 0.10, "relaxed": 0.05,
        "docile": 0.05, "hostile": -0.25, "anxious": -0.20,
        "disdainful": -0.15, "boredom": -0.10, "neutral": 0.0,
        # 旧 7 值兜底
        "excited": 0.20, "calm": 0.05, "angry": -0.25,
        "sad": -0.20, "confident": 0.15, "submissive": 0.05,
    }
    MOOD_GAZE_Y = {
        "exuberant": -0.06, "dependent": 0.05, "relaxed": 0.0,
        "docile": 0.05, "hostile": -0.05, "anxious": 0.12,
        "disdainful": 0.0, "boredom": 0.05, "neutral": 0.0,
        "excited": -0.06, "calm": 0.0, "angry": -0.05,
        "sad": 0.12, "confident": -0.04, "submissive": 0.05,
    }

    def __init__(self, deadzone: float = 0.08,
                 clock=time.monotonic):
        self.deadzone = float(deadzone)
        self._clock = clock
        self._mood = "neutral"
        self._speaking_until = 0.0
        self._x = 0.0
        self._y = 0.0
        self._open = 0.0
        self._form = 0.0
        self._gaze_x = 0.0
        self._gaze_y = 0.0
        # VTS 跟踪参数 MouthSmile 的中性值（从 InputParameterList 读取）
        self.neutral_smile = 0.0

    def set_neutral_smile(self, value: float) -> None:
        self.neutral_smile = max(0.0, min(1.0, float(value)))

    def set_mood(self, quadrant: str) -> None:
        self._mood = str(quadrant or "neutral")

    def start_speaking(self, duration_ms: float,
                       now: Optional[float] = None) -> None:
        ts = self._clock() if now is None else float(now)
        duration = max(0.0, float(duration_ms or 0))
        self._speaking_until = ts + duration / 1000.0

    def _shape(self, value: float) -> float:
        size = abs(value)
        if size <= self.deadzone:
            return 0.0
        t = min(1.0, (size - self.deadzone) / max(1e-6, 1.0 - self.deadzone))
        return -t if value < 0 else t

    def set_pointer(self, nx: float, ny: float) -> None:
        sx = self._shape(float(nx))
        sy = self._shape(float(ny))
        norm = math.hypot(sx, sy)
        if norm > 1.0:
            sx /= norm
            sy /= norm
        self._x = sx
        self._y = sy

    def compute(self, now: Optional[float] = None) -> Dict[str, float]:
        ts = self._clock() if now is None else float(now)
        mood_form = self.MOOD_FORM.get(self._mood, 0.0)
        mood_gaze_y = self.MOOD_GAZE_Y.get(self._mood, 0.0)

        if ts < self._speaking_until:
            target_open = 0.32 + 0.28 * abs(math.sin(ts * 9.0))
            target_form = 0.15 + mood_form * 0.5
        else:
            target_open = min(1.0, math.hypot(self._x, self._y)) * 0.65
            target_form = -self._y * 0.5 + mood_form

        # 20Hz tick 下的一阶缓动；与 dsh 的指数缓动等价到观感
        self._open += (max(0.0, min(1.0, target_open)) - self._open) * 0.25
        self._form += (max(-1.0, min(1.0, target_form)) - self._form) * 0.25

        self._gaze_x = max(-1.0, min(1.0, self._x))
        self._gaze_y = max(-1.0, min(1.0, self._y + mood_gaze_y))
        # VTS 的跟踪参数 MouthSmile 只有 0..1，以模型的实际中性值为基准
        smile = max(0.0, min(1.0, self.neutral_smile + self._form * 0.5))
        return {
            self.EYE_LEFT_X: round(self._gaze_x, 4),
            self.EYE_LEFT_Y: round(self._gaze_y, 4),
            self.EYE_RIGHT_X: round(self._gaze_x, 4),
            self.EYE_RIGHT_Y: round(self._gaze_y, 4),
            self.MOUTH_OPEN: round(self._open, 4),
            self.MOUTH_SMILE: round(smile, 4),
        }

    def status(self) -> Dict[str, Any]:
        return {
            "mood": self._mood,
            "speaking": self._clock() < self._speaking_until,
            "pointer": [round(self._x, 3), round(self._y, 3)],
            "gaze": [round(self._gaze_x, 3), round(self._gaze_y, 3)],
            "mouth": [round(self._open, 3), round(self._form, 3)],
        }


_LIVE2D_PROMPT = """可用工具（Live2D 虚拟形象能力）：
- live2d_status: 查询 VTS 连接 / 当前模型
- live2d_set_expression: 切表情（支持文件名或情绪键）
- live2d_trigger_motion: 触发动作热键

执行原则：
1. 表情/动作由主播脑通过 LIVE2D_EXPRESSION / LIVE2D_MOTION 事件触发。
2. 本域默认不启动，未启用时返回 domain_disabled。
3. VTS 需在 VTube Studio 内启用 API 插件（默认端口 8001）。
"""


class Live2DDomain(Actuator):
    """Live2D 虚拟形象能力域（H-2 Actuator 类型）

    事件链路（V1 live2d_listener 迁移）：
        StreamerBrain --emit LIVE2D_EXPRESSION--> 本域 _on_expression --> VTS 切表情
        StreamerBrain --emit LIVE2D_MOTION--> 本域 _on_motion --> VTS 触发动作
    """

    id = "live2d"
    label = "Live2D 虚拟形象（VTube Studio）"
    prefix = "live2d_"
    default_enabled = False      # 默认不启动（能力挂载，可由 WebUI/配置热启）
    type = CapabilityType.ACTUATOR

    call_timeout = 30.0

    # ALMA 八象限+neutral → Live2D expression ID（与 persona.yaml avatar.emotion_to_expression 对齐）
    QUADRANT_TO_EXPRESSION = {
        "exuberant": "smile_01",     # +P +A +D
        "dependent": "smile_02",     # +P +A -D
        "relaxed": "calm_01",        # +P -A +D
        "docile": "calm_01",         # +P -A -D
        "hostile": "angry_01",       # -P +A +D
        "anxious": "sad_01",         # -P +A -D
        "disdainful": "angry_01",    # -P -A +D
        "boredom": "neutral_01",     # -P -A -D
        "neutral": "neutral_01",
        # 旧 7 值兜底（外部仍可能发旧事件）
        "excited": "smile_01",
        "calm": "calm_01",
        "angry": "angry_01",
        "sad": "sad_01",
        "confident": "smile_02",
        "submissive": "sad_02",
    }

    def __init__(self, persona=None):
        super().__init__()
        self._vts = None          # 懒加载 VTubeStudioAPI
        self._persona = persona   # PersonaProfile（可选）
        self.bus = None           # 由 DomainAssembly.start_domain 注入
        self._last_expression = None
        self._last_expression_at = 0.0
        self._debounce_sec = 1.0

        # dsh 风格的基础表情 + 反应层
        self._base_expression: Optional[str] = None
        self._reaction_expression: Optional[str] = None
        self._reaction_until = 0.0
        self._reaction_priority = 0
        self._reaction_task: Optional[asyncio.Task] = None

        # 动作重播保护
        self._last_motion_id: Optional[str] = None
        self._last_motion_at = 0.0
        self._motion_until = 0.0

        # 表情有效性缓存 / 连接失败冷却
        self._available_expressions: Optional[set] = None
        self._expression_cache_at = 0.0
        self._connect_fail_until = 0.0

        # 逐帧嘴型/目光
        self._params = ParamEngine()
        self._param_task: Optional[asyncio.Task] = None
        self._param_values: Dict[str, float] = {}
        self._param_baseline = {
            ParamEngine.EYE_LEFT_X: 0.0,
            ParamEngine.EYE_LEFT_Y: 0.0,
            ParamEngine.EYE_RIGHT_X: 0.0,
            ParamEngine.EYE_RIGHT_Y: 0.0,
            ParamEngine.MOUTH_OPEN: 0.0,
            ParamEngine.MOUTH_SMILE: 0.0,
        }
        self._pointer_cache_at = 0.0
        self._pointer_window = None
        self._tracking_loaded = False
        self._tracking_available: Optional[set] = None

    # 反应层参数（可被测试/后续配置覆盖）
    REACTION_FAILED_MS = 3000
    REACTION_DONE_MS = 1500
    MOTION_COOLDOWN_SEC = 1.5
    MOTION_DEFAULT_MS = 1600
    EXPRESSION_CACHE_SEC = 300
    CONNECT_FAIL_COOLDOWN_SEC = 30
    REACTION_TICK_SEC = 0.2
    PARAM_TICK_SEC = 0.05
    POINTER_CACHE_SEC = 2.0

    # ---------- H-2 事件声明 ----------

    def inbound_event_types(self) -> List[str]:
        """本域不产出新事件"""
        return []

    def outbound_event_types(self) -> List[str]:
        """本域监听的事件：LIVE2D_EXPRESSION / LIVE2D_MOTION"""
        from LLM_v2.events import EVENTS
        return [EVENTS.LIVE2D_EXPRESSION, EVENTS.LIVE2D_MOTION]

    # ---------- 工具 ----------

    def allowed_names(self) -> List[str]:
        return list(LIVE2D_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in LIVE2D_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _LIVE2D_PROMPT

    # ---------- 生命周期 ----------

    def preflight(self) -> Optional[str]:
        """前置检查：VTS API 依赖（websockets）是否安装；不强制连接（VTS 可后开）"""
        try:
            import websockets  # noqa: F401
            return None
        except ImportError:
            return "websockets 未安装，无法连接 VTube Studio"
        except Exception as e:
            return f"preflight 异常: {e}"

    async def start(self) -> None:
        """注册监听器 + 启动反应层 tick + 懒加载 VTS API"""
        from LLM_v2.events import EVENTS
        if self.bus is not None:
            self.bus.on(EVENTS.LIVE2D_EXPRESSION, self._on_expression)
            self.bus.on(EVENTS.LIVE2D_MOTION, self._on_motion)
            # dsh 风格的瞬时反应：失败/完成临时覆盖基础表情
            self.bus.on(EVENTS.ERROR, self._on_error)
            self.bus.on(EVENTS.TASK_FAILED, self._on_error)
            self.bus.on(EVENTS.OPERATOR_DONE, self._on_operator_done)
            self.bus.on(EVENTS.TTS_SPEAK, self._on_tts_speak)
            logger.info(
                f"[live2d-domain] 已注册 {EVENTS.LIVE2D_EXPRESSION}/"
                f"{EVENTS.LIVE2D_MOTION} + 反应层 + 嘴型/目光监听器")
        self._load_vts()
        self._reaction_task = asyncio.create_task(
            self._reaction_loop(), name="live2d-reaction-loop")
        self._param_task = asyncio.create_task(
            self._param_loop(), name="live2d-param-loop")
        logger.info("[live2d-domain] started")

    async def stop(self) -> None:
        """注销监听器 + 停止反应/参数层 + 断开 VTS"""
        from LLM_v2.events import EVENTS
        if self._reaction_task is not None:
            self._reaction_task.cancel()
            try:
                await self._reaction_task
            except asyncio.CancelledError:
                pass
            self._reaction_task = None
        if self._param_task is not None:
            self._param_task.cancel()
            try:
                await self._param_task
            except asyncio.CancelledError:
                pass
            self._param_task = None
        if self.bus is not None:
            try:
                self.bus.off(EVENTS.LIVE2D_EXPRESSION, self._on_expression)
                self.bus.off(EVENTS.LIVE2D_MOTION, self._on_motion)
                self.bus.off(EVENTS.ERROR, self._on_error)
                self.bus.off(EVENTS.TASK_FAILED, self._on_error)
                self.bus.off(EVENTS.OPERATOR_DONE, self._on_operator_done)
                self.bus.off(EVENTS.TTS_SPEAK, self._on_tts_speak)
            except Exception:
                pass
        await self._release_params()
        if self._vts is not None:
            try:
                await self._vts.disconnect()
            except Exception:
                pass
        logger.info("[live2d-domain] stopped")

    # ---------- 内部：VTS ----------

    def _load_vts(self):
        if self._vts is not None:
            return
        try:
            from live2d.vtuber_studio_info import VTubeStudioAPI
            self._vts = VTubeStudioAPI()
            logger.info("[live2d-domain] VTS API 就绪")
        except Exception as e:
            logger.warning(f"[live2d-domain] VTS API 加载失败: {e}")

    async def _ensure_connected(self) -> bool:
        """确保已连接 VTS 并完成插件认证。

        VTS 协议三步：
          1. connect()                    建立 WebSocket
          2. request_auth_token()         请求认证令牌（VTS 端弹窗，需用户点授权）
          3. authenticate()               用令牌做会话认证

        失败后进入 30 秒冷却，避免事件频繁触发时反复弹授权。
        """
        if self._vts is None:
            self._load_vts()
        if self._vts is None:
            return False
        if getattr(self._vts, "websocket", None) is not None:
            return True
        if time.monotonic() < self._connect_fail_until:
            return False
        try:
            ok = await self._vts.connect()
            if ok:
                # 请求认证令牌（VTS 会弹窗，需人工授权；超时 30s）
                token_ok = await self._vts.request_auth_token(
                    "LLM_vtuber", "LLM_vtuber")
                if token_ok:
                    auth_ok = await self._vts.authenticate()
                    if auth_ok:
                        logger.info("[live2d-domain] VTS 插件认证成功")
                        # 新会话：重新读取跟踪参数基线
                        self._tracking_loaded = False
                        self._tracking_available = None
                        return True
                    logger.warning("[live2d-domain] VTS 会话认证失败（token 已获取但认证未通过）")
                else:
                    logger.warning("[live2d-domain] VTS 令牌请求失败（请确认 VTS 弹窗已授权）")
            self._connect_fail_until = time.monotonic() + self.CONNECT_FAIL_COOLDOWN_SEC
            return False
        except Exception as e:
            logger.warning(f"[live2d-domain] VTS 连接失败: {e}")
            self._connect_fail_until = time.monotonic() + self.CONNECT_FAIL_COOLDOWN_SEC
            return False

    # ---------- 内部：事件监听 ----------

    async def _on_expression(self, payload: Any):
        """LIVE2D_EXPRESSION：基础表情；payload 带 transient 时走反应层。"""
        if not isinstance(payload, dict):
            return
        quadrant = payload.get("quadrant", "neutral")
        self._params.set_mood(quadrant)
        expression_id = self._resolve_expression(quadrant)
        if payload.get("transient") is True:
            duration = payload.get("duration_ms") or self.REACTION_DONE_MS
            priority = payload.get("priority") or 1
            await self._push_reaction_expression(
                expression_id, float(duration), int(priority))
            return

        self._base_expression = expression_id
        logger.info(f"[live2d-domain] 基础表情: {quadrant} -> {expression_id}")
        await self._apply_effective()

    async def _on_motion(self, payload: Any):
        """LIVE2D_MOTION：触发动作 + 同动作重播保护 + 可选临时表情。"""
        if not isinstance(payload, dict):
            return
        motion_id = payload.get("motion_id", "")
        if not motion_id:
            return
        now = time.monotonic()
        if (motion_id == self._last_motion_id
                and now - self._last_motion_at < self.MOTION_COOLDOWN_SEC):
            logger.debug(f"[live2d-domain] 动作冷却中，跳过: {motion_id}")
            return
        duration_ms = payload.get("duration_ms")
        if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
            duration_ms = self.MOTION_DEFAULT_MS
        self._last_motion_id = motion_id
        self._last_motion_at = now
        self._motion_until = now + float(duration_ms) / 1000.0
        logger.info(f"[live2d-domain] 触发动作: {motion_id} "
                    f"({duration_ms}ms)")
        await self._trigger_motion(motion_id)

        expression = payload.get("expression")
        if isinstance(expression, str) and expression:
            if expression in self.QUADRANT_TO_EXPRESSION:
                expression = self._resolve_expression(expression)
            await self._push_reaction_expression(
                expression, float(duration_ms),
                int(payload.get("priority") or 1))

    async def _on_error(self, payload: Any):
        """错误 → 短暂难过，而不是把基础情绪永久改掉。"""
        await self._push_reaction("sad", self.REACTION_FAILED_MS, 2)

    async def _on_operator_done(self, payload: Any):
        """任务完成 → 短暂开心；优先级低于失败反应。"""
        await self._push_reaction("excited", self.REACTION_DONE_MS, 1)

    async def _push_reaction(self, key: str, duration_ms: float,
                             priority: int) -> None:
        await self._push_reaction_expression(
            self._resolve_expression(key), duration_ms, priority)

    async def _push_reaction_expression(self, expression: Optional[str],
                                        duration_ms: float,
                                        priority: int) -> None:
        if not expression:
            return
        now = time.monotonic()
        if (self._reaction_expression is not None
                and now < self._reaction_until
                and priority < self._reaction_priority):
            return
        self._reaction_expression = expression
        self._reaction_until = now + max(0.0, float(duration_ms)) / 1000.0
        self._reaction_priority = int(priority)
        logger.info(f"[live2d-domain] 反应表情: {expression} "
                    f"({int(duration_ms)}ms, p{priority})")
        await self._apply_effective()

    async def _apply_effective(self) -> None:
        """反应层优先；没有反应时回基础表情。"""
        now = time.monotonic()
        if self._reaction_expression is not None and now < self._reaction_until:
            expression = self._reaction_expression
        else:
            if (self._reaction_expression is not None
                    and now >= self._reaction_until):
                self._reaction_expression = None
                self._reaction_priority = 0
            expression = self._base_expression
        if not expression or expression == self._last_expression:
            return
        self._last_expression = expression
        await self._set_expression(expression)

    async def _reaction_loop(self) -> None:
        """0.2 秒 tick：反应超时后回到基础表情。"""
        while True:
            await asyncio.sleep(self.REACTION_TICK_SEC)
            try:
                if (self._reaction_expression is not None
                        and time.monotonic() >= self._reaction_until):
                    self._reaction_expression = None
                    self._reaction_priority = 0
                    await self._apply_effective()
                if self._motion_until and time.monotonic() >= self._motion_until:
                    self._motion_until = 0.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[live2d-domain] 反应 tick 失败: {e}")

    async def _on_tts_speak(self, payload: Any) -> None:
        """说话窗口：嘴型由参数层按包络驱动。"""
        if not isinstance(payload, dict):
            return
        text = str(payload.get("text") or "")
        duration = payload.get("duration_ms")
        if not isinstance(duration, (int, float)) or duration <= 0:
            duration = len(text) * 180.0
        duration = max(1200.0, min(10000.0, float(duration)))
        self._params.start_speaking(duration)

    async def _param_loop(self) -> None:
        """20Hz 参数 tick：嘴型、目光。"""
        while True:
            await asyncio.sleep(self.PARAM_TICK_SEC)
            try:
                await self._param_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[live2d-domain] 参数 tick 失败: {e}")

    def _sample_pointer(self) -> tuple:
        """鼠标位置 → VTS 窗口内归一化坐标（-1..1）。失败返回 (0,0)。"""
        try:
            import pygetwindow as gw
            import pyautogui
        except Exception:
            return 0.0, 0.0
        now = time.monotonic()
        if now - self._pointer_cache_at > self.POINTER_CACHE_SEC:
            self._pointer_cache_at = now
            try:
                wins = [
                    w for w in gw.getAllWindows()
                    if w.title and "VTube Studio" in w.title
                ]
                self._pointer_window = wins[0] if wins else None
            except Exception:
                self._pointer_window = None
        try:
            x, y = pyautogui.position()
            win = self._pointer_window
            if win is not None and win.width > 0 and win.height > 0:
                nx = (x - win.left) / win.width * 2.0 - 1.0
                ny = (y - win.top) / win.height * 2.0 - 1.0
            else:
                sw, sh = pyautogui.size()
                nx = x / max(1, sw) * 2.0 - 1.0
                ny = y / max(1, sh) * 2.0 - 1.0
            return max(-1.0, min(1.0, nx)), max(-1.0, min(1.0, ny))
        except Exception:
            return 0.0, 0.0

    async def _refresh_tracking_params(self) -> None:
        """读取 VTS 可注入的跟踪参数与基线值（每次连接只读一次）。"""
        if self._tracking_loaded:
            return
        self._tracking_loaded = True
        try:
            resp = await self._vts.get_tracking_parameters()
        except Exception:  # noqa: BLE001
            return
        if not isinstance(resp, dict) or resp.get("messageType") == "APIError":
            return
        data = resp.get("data") or {}
        rows = (data.get("defaultParameters") or []) + (
            data.get("customParameters") or [])
        available: set = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            if not isinstance(name, str) or not name:
                continue
            available.add(name)
            default = row.get("defaultValue")
            if name in self._param_baseline and isinstance(default, (int, float)):
                self._param_baseline[name] = float(default)
        self._tracking_available = available or None
        self._params.set_neutral_smile(
            self._param_baseline.get(ParamEngine.MOUTH_SMILE, 0.0))

    async def _param_tick(self) -> None:
        if self._vts is None or getattr(self._vts, "websocket", None) is None:
            return
        if not self._tracking_loaded:
            await self._refresh_tracking_params()
        nx, ny = self._sample_pointer()
        self._params.set_pointer(nx, ny)
        values = self._params.compute()
        if self._tracking_available:
            values = {
                name: value for name, value in values.items()
                if name in self._tracking_available
            }
        self._param_values = values
        if not values:
            return
        rows = [{"id": name, "value": float(value)}
                for name, value in values.items()]
        try:
            await self._vts.inject_parameter_data(
                rows, face_found=False, mode="set")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[live2d-domain] 参数注入失败: {e}")

    async def _release_params(self) -> None:
        """停止时把嘴型/眼球参数写回基线（VTS 已关闭自动还原）。"""
        if (self._vts is None
                or getattr(self._vts, "websocket", None) is None):
            return
        if not self._param_values:
            return
        rows = [
            {"id": name, "value": float(value)}
            for name, value in self._param_baseline.items()
            if self._tracking_available is None
            or name in self._tracking_available
        ]
        if not rows:
            self._param_values = {}
            return
        try:
            await self._vts.inject_parameter_data(
                rows, face_found=False, mode="set")
            self._param_values = {}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[live2d-domain] 参数还原失败: {e}")

    def _resolve_expression(self, quadrant: str) -> str:
        """解析 quadrant → expression_id（优先用 persona 配置）"""
        if self._persona and getattr(self._persona, "avatar", None):
            custom = self._persona.avatar.emotion_to_expression.get(quadrant)
            if custom:
                return custom
        return self.QUADRANT_TO_EXPRESSION.get(quadrant, "neutral_01")

    async def _refresh_expression_cache(self) -> None:
        """缓存 VTS 当前模型真实存在的表情名，避免反复 404。"""
        now = time.monotonic()
        if (self._available_expressions is not None
                and now - self._expression_cache_at < self.EXPRESSION_CACHE_SEC):
            return
        try:
            resp = await self._vts.get_expression_state(details=False)
        except Exception:  # noqa: BLE001
            return
        if not isinstance(resp, dict) or resp.get("messageType") == "APIError":
            return
        rows = (resp.get("data") or {}).get("expressions") or []
        names = {
            str(row.get("name")) for row in rows
            if isinstance(row, dict) and row.get("name")
        }
        if names:
            self._available_expressions = names
            self._expression_cache_at = now

    def _validate_expression(self, expression_id: str) -> str:
        names = self._available_expressions
        if not names or expression_id in names:
            return expression_id
        fallback = "neutral_01" if "neutral_01" in names else sorted(names)[0]
        logger.warning(
            f"[live2d-domain] 表情不存在: {expression_id}，回退 {fallback}")
        return fallback

    async def _set_expression(self, expression_id: str):
        """调 VTS API 切表情（带有效性校验）"""
        if not await self._ensure_connected():
            return
        await self._refresh_expression_cache()
        expression_id = self._validate_expression(expression_id)
        try:
            await self._vts.activate_expression(expression_id, True, 0.25)
        except Exception as e:
            logger.warning(f"[live2d-domain] 切表情失败: {e}")

    async def _trigger_motion(self, motion_id: str):
        """调 VTS API 触发动作"""
        if not await self._ensure_connected():
            return
        try:
            await self._vts.trigger_hotkey(motion_id, None)
        except Exception as e:
            logger.warning(f"[live2d-domain] 触发动作失败: {e}")

    # ---------- 工具实现 ----------

    async def live2d_status(self) -> Dict[str, Any]:
        """查询 VTS 连接 / 当前模型 / 表情"""
        connected = await self._ensure_connected()
        info: Dict[str, Any] = {"success": True, "connected": connected}
        if connected and self._vts is not None:
            try:
                state = await self._vts.get_api_state()
                info["api_state"] = state.get("data", {}) if isinstance(state, dict) else {}
            except Exception:
                pass
            try:
                model = await self._vts.get_current_model()
                info["model"] = model.get("data", {}).get("modelName", "?") if isinstance(model, dict) else "?"
            except Exception:
                pass
        info["params"] = self._params.status()
        return info

    async def live2d_set_expression(self, expression: str, active: bool = True,
                                    fade_time: float = 0.25) -> Dict[str, Any]:
        """切表情（支持情绪键或文件名）"""
        if not await self._ensure_connected():
            return {"success": False, "error": "VTS 未连接"}
        try:
            expr = self._resolve_expression(expression) if expression in self.QUADRANT_TO_EXPRESSION else expression
            await self._vts.activate_expression(expr, bool(active), float(fade_time))
            self._last_expression = expr
            return {"success": True, "expression": expr}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def live2d_trigger_motion(self, motion_id: str) -> Dict[str, Any]:
        """触发动作热键"""
        if not await self._ensure_connected():
            return {"success": False, "error": "VTS 未连接"}
        try:
            await self._vts.trigger_hotkey(motion_id, None)
            return {"success": True, "motion_id": motion_id}
        except Exception as e:
            return {"success": False, "error": str(e)}


def build_live2d_domain() -> Live2DDomain:
    return Live2DDomain()


__all__ = ['Live2DDomain', 'build_live2d_domain',
           'LIVE2D_TOOL_NAMES', 'LIVE2D_TOOLS_SCHEMA']
