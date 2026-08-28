#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 模型控制程序（优化版 v2）

核心改进（相比 v1）:
  1. 修复 move() 帧计时 bug：v1 中 next_tick 计算错误导致只发 2 帧（起止），
     表现为"瞬间跳到一边"。v2 用帧索引精确对齐 50FPS。
  2. 统一 WebSocket 串行化：v1 中 idle_movement 的可视化 get_tracking_parameters
     在锁外调用，与 move 的 inject 争抢同一 WebSocket 导致
     "cannot call recv while another coroutine is already running recv"。
     v2 移除 idle 中的 get_tracking，所有 WS 操作都在 operation_lock 下。
  3. 消除参数映射混乱：v1 中 core_params 存 mapped 值 [-1,1]，
     DIRECTION_TEMPLATES 也是 mapped 值，但 inject 时 unmap 成实际度数。
     idle 用 core_params(mapped) 覆盖时会和 move 的目标不一致。
     v2 中 core_params 和 DIRECTION_TEMPLATES 都直接存实际值（度数），
     inject 直接用，无需 map/unmap。
  4. 增大呼吸幅度：v1 的 0.08 太小（VTS 显示波动为 0），v2 改为 0.3，
     让动作结束后模型仍有可见的生命感（解决"突然停住"）。
  5. ease-in-out cubic 缓动 + 多频正弦呼吸 + 动作间微抖动。
"""

import asyncio
import math
import time
import sys
import os
import argparse
import logging
import random

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live2d.vtuber_studio_info import VTubeStudioAPI

logger = logging.getLogger("Live2DMain")

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------

# 方向模板：直接使用实际值（度数/位移），避免 map/unmap 转换混乱
# FaceAngleX/Y/Z 单位为度，典型范围 [-30, 30]
# FacePositionX/Y 为归一化位移，典型范围 [-1, 1]
# 注：D方案（极限版），基础方向约占VTS可动范围的40%，呼吸+微调叠加后动作明显有表现力
DIRECTION_TEMPLATES = {
    "center":    {"FaceAngleX":   0.0, "FaceAngleY":   0.0, "FaceAngleZ":   0.0, "FacePositionX":   0.0,  "FacePositionY":   0.0},
    "up":        {"FaceAngleX":   0.0, "FaceAngleY":  12.0, "FaceAngleZ":   0.0, "FacePositionX":   0.0,  "FacePositionY":  0.18},
    "down":      {"FaceAngleX":   0.0, "FaceAngleY": -12.0, "FaceAngleZ":   0.0, "FacePositionX":   0.0,  "FacePositionY": -0.18},
    "left":      {"FaceAngleX": -10.5, "FaceAngleY":   0.0, "FaceAngleZ":   4.5, "FacePositionX": -0.18, "FacePositionY":   0.0},
    "right":     {"FaceAngleX":  10.5, "FaceAngleY":   0.0, "FaceAngleZ":  -4.5, "FacePositionX":  0.18, "FacePositionY":   0.0},
    "upleft":    {"FaceAngleX":  -7.5, "FaceAngleY":   7.5, "FaceAngleZ":   3.3, "FacePositionX": -0.135, "FacePositionY":  0.135},
    "upright":   {"FaceAngleX":   7.5, "FaceAngleY":   7.5, "FaceAngleZ":  -3.3, "FacePositionX":  0.135, "FacePositionY":  0.135},
    "downleft":  {"FaceAngleX":  -7.5, "FaceAngleY":  -7.5, "FaceAngleZ":   3.3, "FacePositionX": -0.135, "FacePositionY": -0.135},
    "downright": {"FaceAngleX":   7.5, "FaceAngleY":  -7.5, "FaceAngleZ":  -3.3, "FacePositionX":  0.135, "FacePositionY": -0.135},
}
VALID_DIRECTIONS = list(DIRECTION_TEMPLATES.keys())

# A-P0: 表情 -> 参数映射表
# 当模型没有预设表情热键时，通过参数组合模拟表情
# 注意：参数名需根据实际模型调整（这里用通用名做关键词匹配）
EXPRESSION_PARAM_MAP = {
    # 开心/笑容: 嘴角上扬 + 眼睛微弯 + 眉毛稍抬
    "smile": {
        "MouthForm": 0.8,          # 嘴型弯曲 (通用名，实际匹配模型参数)
        "MouthOpenY": 0.25,        # 微张
        "EyeOpenLeft": 0.85,       # 眯眼 (笑眼)
        "EyeOpenRight": 0.85,
        "BrowLY": 0.4,             # 左眉毛稍抬
        "BrowRY": 0.4,             # 右眉毛稍抬
        "ParamAngleY": 3.0,        # 头微仰
    },
    # 生气: 眉毛下压 + 眼睛眯起 + 嘴抿紧
    "angry": {
        "BrowLY": -0.8,
        "BrowRY": -0.8,
        "EyeOpenLeft": 0.6,        # 眯眼
        "EyeOpenRight": 0.6,
        "MouthOpenY": 0.1,         # 抿嘴
        "ParamAngleY": -2.0,
        "ParamAngleX": -3.0,       # 脸稍侧 (怒视)
    },
    # 难过: 眉毛内压 + 嘴角下垂 + 眼睛低垂
    "sad": {
        "BrowLY": 0.7,             # 眉尾下压
        "BrowRY": 0.7,
        "EyeOpenLeft": 0.65,       # 泪眼低垂
        "EyeOpenRight": 0.65,
        "MouthForm": -0.6,         # 嘴角下垂
        "MouthOpenY": 0.15,
        "FacePositionY": -0.15,    # 头稍低
        "ParamAngleY": -4.0,
    },
    # 惊讶: 眼睛瞪大 + 眉毛高抬 + 嘴微张
    "surprised": {
        "EyeOpenLeft": 1.0,        # 全睁
        "EyeOpenRight": 1.0,
        "BrowLY": -0.5,            # 眉毛抬起（惊讶时上扬）
        "BrowRY": -0.5,
        "MouthOpenY": 0.55,       # 张嘴
        "MouthForm": 0.3,          # 圆嘴
        "ParamAngleY": 4.0,
    },
    # 害羞: 眼睛半眯 + 眉毛柔和 + 嘴微抿
    "shy": {
        "EyeOpenLeft": 0.55,       # 低头眯眼
        "EyeOpenRight": 0.55,
        "BrowLY": 0.3,
        "BrowRY": 0.3,
        "MouthForm": 0.4,
        "MouthOpenY": 0.2,
        "ParamAngleX": -4.0,       # 脸侧开
        "FacePositionY": -0.08,
    },
    # 眨左眼
    "wink_left": {
        "EyeOpenLeft": 0.0,
        "EyeOpenRight": 1.0,
    },
    # 眨右眼
    "wink_right": {
        "EyeOpenLeft": 1.0,
        "EyeOpenRight": 0.0,
    },
    # 眨眼 (快速)
    "blink": {
        "EyeOpenLeft": 0.0,
        "EyeOpenRight": 0.0,
    },
    # 中性/恢复
    "neutral": {},
}

# 参数名标准化映射：把 EXPRESSION_PARAM_MAP 中的通用参数名
# 映射到模型实际的参数名（根据 param_ranges 做模糊匹配）
_PARAM_NAME_ALIASES = {
    "EyeOpenLeft": ["EyeOpenLeft", "ParamEyeBallX", "eye_open_l"],
    "EyeOpenRight": ["EyeOpenRight", "ParamEyeBallY", "eye_open_r"],
    "MouthOpenY": ["MouthOpenY", "ParamMouthOpenY", "mouth_open"],
    "MouthForm": ["MouthForm", "ParamMouthForm", "mouth_form"],
    "BrowLY": ["BrowLY", "ParamBrowLY", "brow_l"],
    "BrowRY": ["BrowRY", "ParamBrowRY", "brow_r"],
    "ParamAngleX": ["ParamAngleX", "ParamAngleX"],
    "ParamAngleY": ["ParamAngleY", "ParamAngleY"],
    "ParamAngleZ": ["ParamAngleZ", "ParamAngleZ"],
}

# 动作帧率（每秒下发参数次数）
_MOVE_FPS = 50
_MOVE_STEP_SEC = 1.0 / _MOVE_FPS

# 嘴巴平滑过渡时长 (秒)
# v1=0.15 太短，开合太突然；D方案动作整体3秒，嘴巴0.5秒配合整体节奏
_MOUTH_TRANSITION_SEC = 0.5

# 呼吸晃动：多频正弦叠加 (权重, 周期秒, 相位)
_BREATH_LAYERS = (
    (0.50, 9.0, 0.0),
    (0.30, 14.0, 1.3),
    (0.20, 23.0, 2.7),
)
# 呼吸幅度：D方案修正版（降低后避免净位移被呼吸掩盖）
# - 角度 ±6° ≈ FaceAngleY=12° 模板的 50%（净位移12°与呼吸±6°能清晰区分）
# - 位移 ±0.50 ≈ 总范围±1的50%
_BREATH_ANGLE_AMP = 6.0      # 角度参数呼吸幅度（度）
_BREATH_POSITION_AMP = 0.50  # 位移参数呼吸幅度
# 动作过程中的微抖动幅度（约为呼吸幅度的一半，且被 α 衰减在首尾）
_IRREGULAR_ANGLE_AMP = 3.0
_IRREGULAR_POSITION_AMP = 0.25

# idle 段低频注视点漂移：打破呼吸正弦极值点的"静止感"
# 周期 7-17s（短于呼吸9-23s，确保在短idle段5-8s内完成半个周期以上）
# 幅度 3° ≈ 呼吸的 1/2，角速度 3*2π/7 ≈ 2.7°/s，在极值点提供持续微移
_DRIFT_LAYERS = (
    (0.45, 7.0, 0.3),
    (0.35, 11.0, 2.1),
    (0.20, 17.0, 4.5),
)
_DRIFT_ANGLE_AMP = 3.0      # 漂移角度幅度（度）— 约为呼吸的 1/2
_DRIFT_POSITION_AMP = 0.20  # 漂移位移幅度

# 呼吸各参数的缩放系数（让不同参数晃动幅度不同，避免机械同步）
_BREATH_PARAM_SCALE = {
    "FaceAngleX": 0.8,
    "FaceAngleY": 1.0,
    "FaceAngleZ": 0.5,
    "FacePositionX": 0.4,
    "FacePositionY": 1.2,
}

# move 段中段呼吸衰减系数：呼吸角速度峰值(10.8°/s)与缓动速度(6.8°/s)同量级，
# 衰减到 40% 后呼吸角速度降到 4.3°/s，不再主导净位移方向，消除忽快忽慢
_MOVE_BREATH_ATTENUATION = 0.4
# move 段微抖动衰减系数：微抖动角速度(22.7°/s)是缓动速度(11°/s)的 2 倍，
# 衰减到 20% 后角速度降到 4.5°/s，不再干扰缓动方向
_MOVE_IRR_ATTENUATION = 0.2


# ---------------------------------------------------------------------------
# 缓动函数
# ---------------------------------------------------------------------------

def _ease_in_out_sine(t: float) -> float:
    """正弦缓入缓出：全程速度变化更柔和，没有 cubic 中段的冲感。
    适合用于角色姿态切换，避免"嗖地一下到位"的机械感。
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return -(math.cos(math.pi * t) - 1.0) * 0.5


# ---------------------------------------------------------------------------
# Live2DMain 核心控制器
# ---------------------------------------------------------------------------

class Live2DMain:
    """Live2D 模型控制主类"""

    def __init__(self, host="localhost", port=8001):
        self.api = VTubeStudioAPI(host=host, port=port)
        self.running = False

        # 当前方向和嘴巴状态
        self.current_direction = "center"
        self._mouth_target_open = 0.0   # 目标嘴巴开合度 [0,1]
        self._mouth_current_open = 0.0  # 当前（平滑过渡中）嘴巴开合度

        # 核心参数：存储实际值（度数/位移原值），inject 时直接用
        self.core_params: dict[str, float] = {}

        # 参数映射信息（保留用于 map/unmap 兼容，但核心逻辑不再使用）
        self.param_ranges: dict[str, dict] = {}

        # A-P0: 模型资源缓存（启动时 discover_model_resources 填充）
        self.model_capabilities: dict = {"params": {}, "hotkeys": [], "expressions": []}

        # 启动时间，用于呼吸晃动计时
        self.start_time = 0.0

        # 参数可视化器（可选，默认不启用以避免 WS 争抢）
        self.visualizer = None

        # 指令队列
        self.command_queue: asyncio.Queue = asyncio.Queue()

        # 全局操作锁：所有 WebSocket 操作必须持锁，避免 send/recv 交错
        # VTubeStudioAPI.send_request 内部 send+recv 非原子，并发会报
        # "cannot call recv while another coroutine is already running recv"
        self.operation_lock: asyncio.Lock = asyncio.Lock()

        # 嘴巴参数名缓存
        self._cached_mouth_param: str | None = None

        # 参数日志（运行时记录所有 inject 的参数值，用于诊断分析）
        # 通过环境变量 LIVE2D_PARAM_LOG=1 启用
        self._param_log_file = None
        self._param_log_path = None
        if os.environ.get("LIVE2D_PARAM_LOG"):
            self._enable_param_log()

    # ------------------------------------------------------------------
    # 参数映射（保留兼容，核心逻辑不再使用）
    # ------------------------------------------------------------------

    def map_parameter(self, param_name, value):
        if param_name not in self.param_ranges:
            return value
        r = self.param_ranges[param_name]
        min_v, max_v, default_v = r["min"], r["max"], r["default"]
        if max_v == min_v:
            if default_v == min_v:
                return 0
            if default_v == max_v:
                return 1
            return 0
        if default_v == min_v:
            return (value - min_v) / (max_v - min_v)
        if default_v == max_v:
            return 1 - (value - min_v) / (max_v - min_v)
        return 2 * (value - min_v) / (max_v - min_v) - 1

    def unmap_parameter(self, param_name, mapped_value):
        if param_name not in self.param_ranges:
            return mapped_value
        r = self.param_ranges[param_name]
        min_v, max_v, default_v = r["min"], r["max"], r["default"]
        if max_v == min_v:
            return min_v
        if default_v == min_v:
            return min_v + mapped_value * (max_v - min_v)
        if default_v == max_v:
            return max_v - mapped_value * (max_v - min_v)
        return min_v + (mapped_value + 1) * (max_v - min_v) / 2

    # ------------------------------------------------------------------
    # 呼吸/抖动
    # ------------------------------------------------------------------

    def _breath_contribution(self, t_rel: float) -> float:
        """返回 [-1,1] 的自然呼吸值（多频正弦叠加）。"""
        total = 0.0
        for w, period, phase in _BREATH_LAYERS:
            total += w * math.sin(2.0 * math.pi * t_rel / period + phase)
        return total

    def _breath_param_dict(self, elapsed: float) -> dict[str, float]:
        """根据当前时间返回每个 Face 参数的呼吸偏移值（实际值单位）。

        按参数类型使用不同幅度：角度参数用 _BREATH_ANGLE_AMP，
        位移参数用 _BREATH_POSITION_AMP，确保两类参数都有可见波动。
        """
        base = self._breath_contribution(elapsed)  # [-1, 1]
        result = {}
        for pname, scale in _BREATH_PARAM_SCALE.items():
            if "Position" in pname:
                result[pname] = base * scale * _BREATH_POSITION_AMP
            else:
                result[pname] = base * scale * _BREATH_ANGLE_AMP
        return result

    def _irregular_param_dict(self, elapsed: float) -> dict[str, float]:
        """动作过程中的微抖动（高频、低幅），同样按参数类型区分幅度。"""
        v1 = math.sin(2.0 * math.pi * elapsed / 0.83)
        v2 = math.sin(2.0 * math.pi * elapsed / 1.37 + 0.9)
        return {
            "FaceAngleX": v1 * _IRREGULAR_ANGLE_AMP,
            "FaceAngleY": v2 * _IRREGULAR_ANGLE_AMP,
            "FaceAngleZ": v1 * 0.5 * _IRREGULAR_ANGLE_AMP,
            "FacePositionX": v2 * 0.3 * _IRREGULAR_POSITION_AMP,
            "FacePositionY": v1 * 0.3 * _IRREGULAR_POSITION_AMP,
        }

    def _drift_contribution(self, elapsed: float) -> float:
        """idle 段低频漂移：多频正弦叠加，周期 11-29s，输出 [-1, 1]。
        与呼吸(9-23s)频段接近但相位不同，在呼吸极值点提供持续微移。
        """
        total = 0.0
        for w, period, phase in _DRIFT_LAYERS:
            t_rel = elapsed % period
            total += w * math.sin(2.0 * math.pi * t_rel / period + phase)
        return total

    def _drift_param_dict(self, elapsed: float) -> dict[str, float]:
        """返回每个 Face 参数的漂移偏移值（实际值单位）。"""
        base = self._drift_contribution(elapsed)
        result = {}
        for pname, scale in _BREATH_PARAM_SCALE.items():
            if "Position" in pname:
                result[pname] = base * scale * _DRIFT_POSITION_AMP
            else:
                result[pname] = base * scale * _DRIFT_ANGLE_AMP
        return result

    # ------------------------------------------------------------------
    # 核心参数初始化与 WebSocket 下发
    # ------------------------------------------------------------------

    def _ensure_core_params(self) -> None:
        """初始化 core_params 为所有参数的默认实际值。"""
        if self.core_params:
            return
        for pname, pr in self.param_ranges.items():
            self.core_params[pname] = pr["default"]

    def _enable_param_log(self) -> None:
        """启用参数日志记录，写入 CSV 文件。"""
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        self._param_log_path = os.path.join(log_dir, f"live2d_params_{int(time.time())}.csv")
        self._param_log_file = open(self._param_log_path, "w", encoding="utf-8")
        self._param_log_file.write("timestamp,elapsed_sec,source,FaceAngleX,FaceAngleY,FaceAngleZ,FacePositionX,FacePositionY,MouthOpen\n")
        self._param_log_t0 = time.perf_counter()
        logger.info(f"参数日志已启用: {self._param_log_path}")

    def _log_params(self, items: list[tuple[str, float]], source: str = "") -> None:
        """将参数值写入日志文件（如果已启用）。"""
        if not self._param_log_file:
            return
        d = {name: val for name, val in items}
        # 查找嘴巴参数
        mouth_val = ""
        mouth_param = self._cached_mouth_param
        if mouth_param and mouth_param in d:
            pr = self.param_ranges.get(mouth_param, {})
            m_min = pr.get("min", 0)
            m_max = pr.get("max", 1)
            if m_max > m_min:
                mouth_val = f"{(d[mouth_param] - m_min) / (m_max - m_min):.4f}"
        elapsed = time.perf_counter() - self._param_log_t0
        # 安全格式化：参数不存在时留空
        def _fmt(key):
            v = d.get(key)
            return f"{v:.4f}" if v is not None else ""
        self._param_log_file.write(
            f"{time.time():.3f},{elapsed:.4f},{source},"
            f"{_fmt('FaceAngleX')},{_fmt('FaceAngleY')},"
            f"{_fmt('FaceAngleZ')},{_fmt('FacePositionX')},"
            f"{_fmt('FacePositionY')},{mouth_val}\n"
        )
        self._param_log_file.flush()

    # VTS 注入追踪：记录最近一次注入的参数值，用于快照对比
    _last_injected_values: dict[str, float] = {}

    async def _inject_params(self, items: list[tuple[str, float]], source: str = "") -> None:
        """直接用实际值下发参数到 VTube Studio（不经过 map/unmap）。

        调用者必须持有 operation_lock。
        """
        self._log_params(items, source)
        # 记录最近一次注入值（用于快照对比）
        for name, val in items:
            self._last_injected_values[name] = val
        parameter_values = [{"id": name, "value": val} for name, val in items]
        resp = await self.inject_parameter_data(parameter_values, face_found=True, mode="set")
        # 检查 VTS 返回的错误
        if resp and resp.get("data") and resp["data"].get("errorID", 0):
            err = resp["data"]
            logger.warning(f"[inject] VTS 返回错误: errorID={err.get('errorID')}, msg={err.get('message')}, params={parameter_values[:3]}...")
        elif not resp:
            logger.warning(f"[inject] 注入返回 None, params={parameter_values[:3]}... source={source}")

    # ------------------------------------------------------------------
    # 移动（缓动插值 + 呼吸相位锚定衔接 + 微抖动）
    # ------------------------------------------------------------------

    async def move(self, target_params: dict, duration: float = 2.5, direction: str = "",
                   commit_params: dict | None = None) -> None:
        """
        从当前核心参数平滑移动到目标参数。

        关键改进（v6, 解决起步冲量+忽快忽慢）:
          1. 缓动: 0.3*ease_out_cubic + 0.7*linear。
             降低 cubic 占比(原0.6)，起步斜率从 2.2x 降到 1.6x，
             减少起步冲量；中段 90% 近似匀速，消除忽快忽慢。
          2. 呼吸相位锚定: move 首尾帧把"自身呼吸"与"前/后段 idle 呼吸"
             通过 α 渐变融合，段与段衔接处不会出现 3-5° 的瞬时跳变。
          3. move 中段呼吸衰减到 40%: 呼吸角速度峰值(10.8°/s)与缓动速度(6.8°/s)
             同量级会干扰净位移方向，衰减后不再主导，消除"帧15→20 从11°/s突降到0.76°/s"。
          4. 取消 move_final 单独帧: 最后一帧(progress=1.0)在循环内直接发送。
          5. commit_params (新增, v6): 动作完成后 core_params 更新为 commit_params，
             而非 target_params。当同方向随机微调时，让 core_params 回落到纯净的
             方向模板值，避免下一次同方向微调时"越来越歪"。

        参数:
            commit_params: None = 使用 target_params 提交，否则用这个 dict 提交。
        """
        self._ensure_core_params()

        start_time = time.perf_counter()
        start_params = {p: self.core_params.get(p, 0.0) for p in target_params}
        deltas = {p: target_params[p] - start_params[p] for p in target_params}
        src_label = direction or "unknown"

        # idle 期间的呼吸基线（用 move 启动瞬间的呼吸相位锚定首尾衔接）
        t_abs_start = time.time() - self.start_time
        idle_breath_start = self._breath_param_dict(t_abs_start)
        # move 结束瞬间预期的 idle 呼吸相位（用于末尾锚定落位）
        t_abs_end = t_abs_start + duration
        idle_breath_end = self._breath_param_dict(t_abs_end)

        # 总帧数 + 预留末尾最终帧
        total_frames = int(duration * _MOVE_FPS) + 1  # 例如 3*50+1 = 151 帧
        blend_width = max(6, int(total_frames * 0.15))  # 首尾各 15% 做呼吸锚定渐变

        async with self.operation_lock:
            for frame_index in range(total_frames):
                # 精确对齐帧时间（避免 time.perf_counter 抖动导致丢帧/重复帧）
                target_tick = start_time + frame_index * _MOVE_STEP_SEC
                # 本帧缓动进度 [0, 1]，最后一帧强制为 1.0（确保精准落位）
                if frame_index == total_frames - 1:
                    progress_raw = 1.0
                else:
                    # 用 time.perf_counter() 的真实位置计算，但钳制到 [0, 1]
                    now = time.perf_counter()
                    progress_raw = max(0.0, min(1.0, (now - start_time) / duration))

                # 混合缓动: 30% ease_out_cubic + 70% linear
                # v6: 降低 cubic 占比(0.6→0.3)，起步斜率从 2.2x 降到 1.6x，
                # 减少起步冲量；中段 90% 近似匀速，消除忽快忽慢
                def _blend_ease(t: float) -> float:
                    if t <= 0.0: return 0.0
                    if t >= 1.0: return 1.0
                    # ease_out_cubic: 1 - (1-t)^3
                    cubic = 1.0 - (1.0 - t) ** 3
                    return 0.3 * cubic + 0.7 * t
                progress = _blend_ease(progress_raw)

                # --- 呼吸偏移锚定 ---
                breath_move = self._breath_param_dict(time.time() - self.start_time)
                irr = self._irregular_param_dict(progress_raw * duration)

                # blend α: 0=完全用idle锚定呼吸(衔接段), 1=完全用move自身呼吸
                if frame_index < blend_width:
                    # 开头 blend_width 帧: α 从 0 → 1 (从前段 idle 呼吸平滑切入)
                    alpha = frame_index / blend_width
                elif frame_index >= total_frames - blend_width:
                    # 结尾 blend_width 帧: α 从 1 → 0 (平滑切回后段 idle 呼吸)
                    alpha = (total_frames - 1 - frame_index) / blend_width
                else:
                    alpha = 1.0

                # 线性插值: 首尾两端分别锚定到 idle_breath_start / idle_breath_end
                # 起点侧: (1-α)*idle_breath_start + α*breath_move
                # 终点侧: 同步向 idle_breath_end 靠拢
                t_rel_end = progress_raw  # 0→1
                t_rel_start = 1.0 - progress_raw

                items: list[tuple[str, float]] = []
                for pname in target_params:
                    base = start_params[pname] + deltas[pname] * progress
                    bm = breath_move.get(pname, 0.0)
                    bs = idle_breath_start.get(pname, 0.0)
                    be = idle_breath_end.get(pname, 0.0)
                    # v6: move 中段呼吸衰减到 40%，避免呼吸角速度(10.8°/s)
                    # 干扰缓动方向(6.8°/s)导致忽快忽慢；首尾保持 100% 与 idle 衔接
                    move_breath = bm * _MOVE_BREATH_ATTENUATION
                    blended_breath = (
                        (1 - alpha) * (t_rel_start * bs + t_rel_end * be)
                        + alpha * move_breath
                    )
                    # 微抖动仅在中段 α≈1 时生效，首尾衔接时抑制
                    # v6: 中段额外衰减到 20%，角速度从 22.7→4.5°/s，不再干扰缓动方向
                    blended_irr = irr.get(pname, 0.0) * alpha * _MOVE_IRR_ATTENUATION
                    total = base + blended_breath + blended_irr
                    items.append((pname, total))

                # 最后一帧: source 标记为 move_final（保持CSV兼容性）
                if frame_index == total_frames - 1:
                    src = f"move_final:{src_label}"
                else:
                    src = f"move:{src_label}"
                await self._inject_params(items, source=src)

                # 精确睡到下一帧
                sleep_s = target_tick - time.perf_counter()
                if sleep_s > 0:
                    await asyncio.sleep(sleep_s)
                else:
                    await asyncio.sleep(0)

            # 到达终点：更新核心参数
            # 若指定了 commit_params（同方向微调场景），则用模板值回退，避免微调累积"越来越歪"；
            # 否则用 target_params（跨方向场景）。
            if commit_params is not None:
                for pname, v in commit_params.items():
                    self.core_params[pname] = v
            else:
                for pname, v in target_params.items():
                    self.core_params[pname] = v

        logger.debug(f"移动完成，耗时 {duration:.2f}s，发送 {total_frames} 帧")

    # ------------------------------------------------------------------
    # 方向移动（高层 API）
    # ------------------------------------------------------------------

    async def move_to_direction(self, direction: str, duration: float = 2.5) -> bool:
        if direction not in DIRECTION_TEMPLATES:
            logger.warning(f"无效方向: {direction}  (有效值: {VALID_DIRECTIONS})")
            return False
        base_target = dict(DIRECTION_TEMPLATES[direction])  # 纯净模板（始终作为commit基准）
        target_params = dict(base_target)
        commit_params: dict | None = None  # None = 直接用 target_params 提交

        # 方向与当前相同时，添加随机微调，避免 delta=0 无可见动作
        # v6修正:
        # 1. 微调幅度加大到 ±20° / ±0.35（跨方向模板约±12°，微调换方向感更强）
        # 2. 按实际最大绝对位移量计算 duration，保持 ~10°/s 目标速度，
        #    与跨方向速度保持一致
        # 3. move 结束后 commit_params 回落到纯净 base_target，不累积微调，
        #    避免 N 次同方向后 AX/AY "越来越歪"
        if direction == self.current_direction:
            tweak_angle_amp = 20.0
            tweak_pos_amp = 0.35
            # 记录本次微调值，用于日志和计算实际速度匹配
            tweaks: dict[str, float] = {}
            max_abs_delta_deg = 0.0
            for pname in target_params:
                if "Angle" in pname:
                    tw = random.uniform(-tweak_angle_amp, tweak_angle_amp)
                    target_params[pname] += tw
                    # 实际位移量 = 微调值 (因为 start_params≈base_target 同方向)
                    if abs(tw) > max_abs_delta_deg:
                        max_abs_delta_deg = abs(tw)
                    tweaks[pname] = tw
                elif "Position" in pname:
                    tw = random.uniform(-tweak_pos_amp, tweak_pos_amp)
                    target_params[pname] += tw
                    tweaks[pname] = tw
            # 目标速度：~10°/s（与跨方向 Δ≈25°/2.5s ≈10°/s 一致）
            TARGET_SPEED_DEG_S = 10.0
            TARGET_SPEED_POS_S = 0.20  # 位移维度同步约 0.2/s
            # 用最大 delta 估算时长，保持速度一致
            if max_abs_delta_deg > 0.01:
                auto_duration = max_abs_delta_deg / TARGET_SPEED_DEG_S
            else:
                auto_duration = 2.0
            # 夹在合理范围：至少 1.2s（避免太快），最多 3.0s
            duration = max(1.2, min(3.0, auto_duration))
            commit_params = base_target
            # 打印本次各参数微调详情，便于日志验证不累积
            tweak_str = ", ".join(f"{k}={v:+.1f}" for k, v in tweaks.items()
                                  if "Angle" in k and abs(v) > 0.5)
            logger.info(
                f"方向相同({direction})，添加随机微调 [{tweak_str}]，"
                f"调整时长={duration:.2f}s (目标速度~{TARGET_SPEED_DEG_S:.0f}°/s)"
            )

        await self.move(target_params, duration, direction=direction,
                        commit_params=commit_params)
        self.current_direction = direction
        logger.info(f"已移动到方向: {direction}")
        return True

    # ------------------------------------------------------------------
    # 嘴巴状态（平滑过渡）
    # ------------------------------------------------------------------

    async def set_mouth_state(self, open_state: float | bool) -> bool:
        """
        设置嘴巴开合度，150ms 内平滑过渡避免突兀。
        整个过渡持有 operation_lock。
        """
        if isinstance(open_state, bool):
            target = 1.0 if open_state else 0.0
        else:
            target = max(0.0, min(1.0, float(open_state)))

        self._mouth_target_open = target

        mouth_param = self._resolve_mouth_param()
        if mouth_param is None:
            logger.warning("未找到嘴巴参数，无法设置嘴巴状态")
            return False

        # 获取嘴巴参数的实际范围，用于把 [0,1] 开合度映射到实际值
        pr = self.param_ranges.get(mouth_param, {})
        m_min = pr.get("min", 0)
        m_max = pr.get("max", 1)
        # 开合度 0→m_min, 1→m_max
        def _open_to_actual(o: float) -> float:
            return m_min + o * (m_max - m_min)

        start_open = self._mouth_current_open
        start_t = time.perf_counter()

        async with self.operation_lock:
            while True:
                elapsed = time.perf_counter() - start_t
                if elapsed >= _MOUTH_TRANSITION_SEC:
                    break
                t_p = elapsed / _MOUTH_TRANSITION_SEC
                self._mouth_current_open = start_open + (target - start_open) * t_p
                await self._inject_params([(mouth_param, _open_to_actual(self._mouth_current_open))], source="mouth_transition")
                await asyncio.sleep(min(_MOVE_STEP_SEC, _MOUTH_TRANSITION_SEC - elapsed))

            # 落位
            self._mouth_current_open = target
            # 同步到 core_params，防止 idle 循环用旧值覆盖嘴巴状态
            self.core_params[mouth_param] = _open_to_actual(target)
            await self._inject_params([(mouth_param, _open_to_actual(target))], source="mouth_final")

        state_name = "张开" if target >= 0.5 else "闭合"
        logger.info(f"嘴巴状态已设置为: {state_name} (mouth={self._mouth_current_open:.2f})")
        return True

    def _resolve_mouth_param(self) -> str | None:
        """查找嘴巴参数名，并做缓存。"""
        if self._cached_mouth_param and self._cached_mouth_param in self.param_ranges:
            return self._cached_mouth_param
        for pname in self.param_ranges.keys():
            low = pname.lower()
            if "mouth" in low or "嘴" in pname:
                self._cached_mouth_param = pname
                return pname
        return None

    # ------------------------------------------------------------------
    # 空闲状态（呼吸维持循环）
    # ------------------------------------------------------------------

    async def update_non_moving_state(self, current_time: float) -> None:
        """下发 core_params + 呼吸 + 漂移，维持生命感。调用者需持锁。

        只注入 Face 相关参数 + 嘴巴参数（共 ~6 个），而非全部 127 个参数，
        避免 WebSocket 往返过慢导致 FPS 下降。

        v6 改进：叠加低频漂移(_drift)，打破呼吸正弦极值点的 ~1.5s 静止感。
        """
        self._ensure_core_params()
        elapsed = current_time - self.start_time
        breath = self._breath_param_dict(elapsed)
        drift = self._drift_param_dict(elapsed)

        items: list[tuple[str, float]] = []
        # 只注入 Face 参数（带呼吸 + 漂移）+ 嘴巴参数
        for pname, scale in _BREATH_PARAM_SCALE.items():
            core = self.core_params.get(pname, 0.0)
            items.append((pname, core + breath.get(pname, 0.0) + drift.get(pname, 0.0)))

        # 嘴巴参数（保持当前开合度，不叠加呼吸）
        mouth_param = self._resolve_mouth_param()
        if mouth_param and mouth_param in self.core_params:
            items.append((mouth_param, self.core_params[mouth_param]))

        await self._inject_params(items, source=f"idle:{self.current_direction}")

    # A-P0: VTS 实际参数快照（用于诊断）
    _SNAPSHOT_KEY_PARAMS = [
        "FaceAngleX", "FaceAngleY", "FaceAngleZ",
        "FacePositionX", "FacePositionY", "FacePositionZ",
        "MouthOpen", "MouthSmile",
        "EyeOpenLeft", "EyeOpenRight",
        "BrowLeft", "BrowRight",
    ]

    async def dump_vts_current_params(self, label: str = "") -> None:
        """向 VTS 请求当前所有参数的实际值并打日志。

        使用 InputParameterListRequest（返回 FaceAngleX 等人类可读参数名），
        而非 Live2DParameterListRequest（返回 Param158 等占位名）。
        调用者必须持有 operation_lock。
        """
        try:
            params_resp = await self.api.get_tracking_parameters()
            if not params_resp or "data" not in params_resp:
                logger.warning(f"[快照] get_tracking_parameters 响应无效: {params_resp}")
                return

            data = params_resp["data"]
            defaults = data.get("defaultParameters", [])
            customs = data.get("customParameters", [])

            # 合并所有参数，建立 name -> value 映射
            all_params = {}
            for p in defaults + customs:
                all_params[p.get("name", "")] = p.get("value", 0.0)

            parts = [f"[快照] model=Y({len(defaults)} default + {len(customs)} custom)"]
            if label:
                parts.append(f"label={label}")

            # 对比关键参数：期望值 (最近一次注入) vs 实际值 (VTS)
            key_params = [
                "FaceAngleX", "FaceAngleY", "FaceAngleZ",
                "FacePositionX", "FacePositionY",
                "MouthOpen", "MouthSmile",
                "EyeOpenLeft", "EyeOpenRight",
                "Brows",
            ]
            for pname in key_params:
                actual = all_params.get(pname)
                expected = self._last_injected_values.get(pname, self.core_params.get(pname))
                if actual is not None:
                    if expected is not None:
                        delta = actual - expected
                        marker = "✓" if abs(delta) < 0.1 else "⚠"
                        parts.append(f"{pname}={actual:+.3f}(inj={expected:+.3f}{marker})")
                    else:
                        parts.append(f"{pname}={actual:+.3f}(no-inj)")

            # 额外：列出 core_params 里有但不在上表中的参数
            extra_names = [n for n in self.core_params if n not in key_params]
            if extra_names:
                extra_preview = []
                for n in extra_names[:5]:
                    actual = all_params.get(n, "?")
                    extra_preview.append(f"{n}={actual:+.2f}" if isinstance(actual, (int, float)) else f"{n}={actual}")
                parts.append(f"extra=[{', '.join(extra_preview)}]")

            logger.info(" ".join(parts))

            # 检查是否完全没匹配到
            if not any(all_params.get(p) is not None for p in key_params):
                all_names = list(all_params.keys())[:15]
                logger.warning(f"[快照] ⚠️ 关键参数名未匹配！VTS 实际参数名示例: {all_names}")

        except Exception as e:
            logger.error(f"[快照] 获取 VTS 参数失败: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # 连接/登录/初始化/断开
    # ------------------------------------------------------------------

    async def connect(self):
        return await self.api.connect()

    async def disconnect(self):
        await self.api.disconnect()

    async def login(self, plugin_name="Live2DMain", plugin_developer="Developer"):
        if not await self.api.request_auth_token(plugin_name, plugin_developer):
            logger.error("请求认证令牌失败")
            return False
        if not await self.api.authenticate(plugin_name, plugin_developer):
            logger.error("认证失败")
            return False
        logger.info("登录成功")
        return True

    async def initialize(self) -> bool:
        logger.info("开始初始化 Live2D 参数...")
        if not await self.get_parameter_ranges():
            logger.error("初始化失败: 无法获取参数范围")
            return False
        self._ensure_core_params()
        logger.info(f"核心参数初始化完成，共 {len(self.core_params)} 个参数")
        
        # 打印前 10 个参数名，确认 VTS 参数名正确
        param_names_preview = list(self.param_ranges.keys())[:10]
        logger.info(f"参数预览: {param_names_preview}")
        
        self.start_time = time.time()
        await self.set_mouth_state(False)

        # A-P0: 自动发现模型资源（热键/表情文件）
        try:
            resources = await self.discover_model_resources()
            hk_count = len(resources.get("hotkeys", []))
            exp_count = len(resources.get("expressions", []))
            logger.info(f"[资源发现] hotkeys={hk_count}, expressions={exp_count}")
        except Exception as e:
            logger.warning(f"[资源发现] 失败(非致命): {e}")

        logger.info("初始化完成 (含表情系统)")
        return True

    async def get_parameter_ranges(self) -> bool:
        try:
            # 获取参数范围也需要持锁，避免与 inject 并发
            async with self.operation_lock:
                resp = await self.api.get_tracking_parameters()
            if not resp or "data" not in resp:
                logger.error("获取参数范围失败: 响应数据无效")
                return False
            data = resp["data"]
            for key in ("defaultParameters", "customParameters"):
                for p in data.get(key, []):
                    pname = p.get("name")
                    if not pname:
                        continue
                    self.param_ranges[pname] = {
                        "min": p.get("min", 0),
                        "max": p.get("max", 0),
                        "default": p.get("defaultValue", 0),
                    }
            self._cached_mouth_param = None
            logger.info(f"获取到 {len(self.param_ranges)} 个参数的范围和默认值")
            return True
        except Exception as e:
            logger.error(f"获取参数范围失败: {e}")
            return False

    async def set_direction(self, direction: str) -> bool:
        """仅设置方向标签，不实际移动（兼容保留）。"""
        if direction not in VALID_DIRECTIONS:
            logger.warning(f"无效方向: {direction}")
            return False
        self.current_direction = direction
        return True

    # ------------------------------------------------------------------
    # WebSocket 注入
    # ------------------------------------------------------------------

    async def inject_parameter_data(self, parameter_values, face_found=True, mode="set"):
        """下发参数到 VTube Studio。调用者必须持有 operation_lock。"""
        try:
            data = {
                "parameterValues": parameter_values,
                "faceFound": face_found,
                "mode": mode,
            }
            if self.api.auth_token:
                data["authenticationToken"] = self.api.auth_token
            return await self.api.send_request(
                api_name="VTubeStudioPublicAPI",
                message_type="InjectParameterDataRequest",
                data=data,
            )
        except Exception as e:
            logger.error(f"注入参数数据失败: {e}")
            return None

    # ------------------------------------------------------------------
    # A-P0 表情 & 热键系统
    # ------------------------------------------------------------------

    def _resolve_expression_params(self, expression: str) -> list[tuple[str, float]]:
        """把 EXPRESSION_PARAM_MAP 中的通用参数名映射到模型实际参数名。

        Returns:
            [(actual_param_name, value), ...] - 只包含模型实际有的参数
        """
        expr_params = EXPRESSION_PARAM_MAP.get(expression, {})
        if not expr_params:
            return []

        resolved = []
        for generic_name, value in expr_params.items():
            # 如果 generic_name 本身就在 param_ranges 里，直接用
            if generic_name in self.param_ranges:
                resolved.append((generic_name, float(value)))
                continue
            # 尝试通过别名匹配
            aliases = _PARAM_NAME_ALIASES.get(generic_name, [])
            found = False
            for alias in aliases:
                if alias in self.param_ranges:
                    resolved.append((alias, float(value)))
                    found = True
                    break
            if not found:
                # 最后兜底：param_ranges 里模糊搜索
                low = generic_name.lower()
                for actual_name in self.param_ranges.keys():
                    if low in actual_name.lower():
                        resolved.append((actual_name, float(value)))
                        found = True
                        break
            if not found:
                logger.debug(f"[表情] 参数 {generic_name} 在模型中未找到，跳过")

        return resolved

    async def set_expression(self, expression: str, intensity: float = 0.8,
                              duration: float = 2.5) -> bool:
        """A-P0: 设置表情（通过参数组合或热键）。

        Args:
            expression: 表情名称，见 EXPRESSION_PARAM_MAP
            intensity: 表情强度 0.0-1.0（参数值会乘以此系数）
            duration: 表情持续秒数

        Returns:
            True 表示成功（已注入参数）
        """
        if expression not in EXPRESSION_PARAM_MAP:
            logger.warning(f"[表情] 未知表情: {expression}")
            return False
        if expression == "neutral":
            logger.info("[表情] neutral，跳过（由 idle 循环保持基础表情）")
            return True

        resolved = self._resolve_expression_params(expression)
        if not resolved:
            logger.warning(f"[表情] {expression} 没有可解析的参数，跳过")
            return False

        # 应用强度系数
        resolved_intense = [
            (name, val * intensity) for name, val in resolved
        ]

        # 参数范围裁剪
        items_capped = []
        for name, val in resolved_intense:
            pr = self.param_ranges.get(name, {})
            pmin, pmax = pr.get("min", -1.0), pr.get("max", 1.0)
            val = max(pmin, min(pmax, val))
            items_capped.append((name, val))

        logger.info(f"[表情] 设置 {expression} (intensity={intensity}) → {items_capped}")

        # 必须持 operation_lock，否则与 idle/move 的 WebSocket send/recv 交错
        async with self.operation_lock:
            await self._inject_params(items_capped, source=f"expression:{expression}")
            # 立即请求 VTS 确认参数是否真的生效
            await self.dump_vts_current_params(label=f"after_expression:{expression}")

        # 保存表情状态到 core_params，让 idle 循环持续保持
        # （但只保存表情专属参数，避免覆盖 direction/breath 参数）
        for name, val in items_capped:
            self.core_params[name] = val

        return True

    async def trigger_hotkey(self, hotkey_name: str) -> bool:
        """A-P0: 通过 VTS HotkeyTriggerRequest 触发热键。

        Args:
            hotkey_name: 热键名称（模型预设的）

        Returns:
            True 表示 VTS 返回成功
        """
        try:
            data = {
                "hotkeyID": hotkey_name,
                "hotkeyPressType": "Trigger",  # Trigger / KeepDown / KeepUp
            }
            if self.api.auth_token:
                data["authenticationToken"] = self.api.auth_token

            # 持锁：send_request 内部 send+recv 非原子
            async with self.operation_lock:
                result = await self.api.send_request(
                    api_name="VTubeStudioPublicAPI",
                    message_type="HotkeyTriggerRequest",
                    data=data,
                )
            if result and result.get("messageType") == "HotkeyTriggerResponse":
                success = result.get("data", {}).get("hotkeyTriggered", False)
                logger.info(f"[热键] {hotkey_name} → triggered={success}")
                return success
            logger.warning(f"[热键] 异常响应: {result}")
            return False
        except Exception as e:
            logger.error(f"[热键] 触发 {hotkey_name} 失败: {e}")
            return False

    async def trigger_expression_file(self, expression_file: str,
                                      fade_time: float = 0.5,
                                      intensity: float = 1.0) -> bool:
        """A-P0: 激活 VTS 预设的 .exp3.json 表情文件。

        这比参数注入更精确（表情文件由模型作者设计），
        但需要先知道模型有哪些表情文件（见 discover_model_resources）。

        Args:
            expression_file: VTS 表情文件名 (不含扩展名)
            fade_time: 淡入淡出时间（秒）
            intensity: 表情强度 0.0-1.0
        """
        try:
            data = {
                "expressionFile": f"{expression_file}.exp3.json",
                "expression": {
                    "name": expression_file,
                    "file": f"{expression_file}.exp3.json",
                    "active": True,
                    "inverted": False,
                    "locked": False,
                },
                "fadeTime": fade_time,
            }
            if self.api.auth_token:
                data["authenticationToken"] = self.api.auth_token

            result = await self.api.send_request(
                api_name="VTubeStudioPublicAPI",
                message_type="ExpressionActivationRequest",
                data=data,
            )
            ok = result is not None
            logger.info(f"[表情文件] {expression_file} → fade={fade_time}s, ok={ok}")
            return ok
        except Exception as e:
            logger.error(f"[表情文件] 激活失败: {e}")
            return False

    async def discover_model_resources(self) -> dict:
        """A-P0: 查询当前模型的所有可用资源（参数、热键、表情文件）。

        启动时调用一次，缓存到 self.model_capabilities。
        """
        result = {"params": {}, "hotkeys": [], "expressions": []}

        # 1. 参数已经在 self.param_ranges 里了（get_parameter_ranges 时缓存）
        result["params"] = dict(self.param_ranges)

        # 2. 查询热键列表
        try:
            hk_data = {
                "modelName": "",
                "hotkeyIDs": [],
            }
            if self.api.auth_token:
                hk_data["authenticationToken"] = self.api.auth_token
            resp = await self.api.send_request(
                api_name="VTubeStudioPublicAPI",
                message_type="HotkeyListRequest",
                data=hk_data,
            )
            if resp and resp.get("messageType") == "HotkeyListResponse":
                result["hotkeys"] = resp.get("data", {}).get("hotkeys", [])
                logger.info(f"[资源发现] 模型有 {len(result['hotkeys'])} 个热键")
        except Exception as e:
            logger.warning(f"[资源发现] 热键列表查询失败: {e}")

        # 3. 查询表情文件列表
        try:
            exp_data = {
                "modelName": "",
                "expressionFile": "",
            }
            if self.api.auth_token:
                exp_data["authenticationToken"] = self.api.auth_token
            resp = await self.api.send_request(
                api_name="VTubeStudioPublicAPI",
                message_type="CurrentModelDataRequest",
                data=exp_data,
            )
            if resp and resp.get("messageType") == "CurrentModelDataResponse":
                result["expressions"] = (
                    resp.get("data", {}).get("modelInformation", {})
                    .get("expressions", [])
                )
                logger.info(f"[资源发现] 模型有 {len(result['expressions'])} 个表情文件")
        except Exception as e:
            logger.warning(f"[资源发现] 表情文件查询失败: {e}")

        # 缓存
        self.model_capabilities = result
        logger.info(
            f"[资源发现] 完成: params={len(result['params'])}, "
            f"hotkeys={len(result['hotkeys'])}, expressions={len(result['expressions'])}"
        )
        return result

    # ------------------------------------------------------------------
    # 长运行循环：空闲呼吸 + 指令队列处理
    # ------------------------------------------------------------------

    async def idle_movement(self) -> None:
        """空闲呼吸循环：每 tick 持锁下发 core_params + 呼吸。"""
        _tick_count = 0
        _start_time = time.time()
        logger.info(f"[idle] 常态循环已启动，param_ranges={len(self.param_ranges)} params")
        while self.running:
            try:
                async with self.operation_lock:
                    now = time.time()
                    await self.update_non_moving_state(now)
                    # 每 200 tick (~60s) 请求一次 VTS 实际参数快照
                    if _tick_count % 200 == 0:
                        await self.dump_vts_current_params(label=f"tick={_tick_count}")
                _tick_count += 1
                await asyncio.sleep(_MOVE_STEP_SEC)
            except Exception as e:
                logger.error(f"[idle] 执行常态运动失败: {e}")
                await asyncio.sleep(1.0)

    async def add_command(self, command: dict) -> None:
        await self.command_queue.put(command)
        logger.debug(f"已添加指令: {command}")

    async def process_commands(self) -> None:
        while self.running:
            try:
                try:
                    command = self.command_queue.get_nowait()
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.02)
                    continue
                await self.execute_command(command)
                self.command_queue.task_done()
            except Exception as e:
                logger.error(f"处理指令失败: {e}")
                await asyncio.sleep(0.1)

    async def execute_command(self, command: dict) -> None:
        action = str(command.get("action", "")).lower()
        if action == "move_to_direction":
            direction = command.get("direction", "center")
            duration = float(command.get("duration", 1.5))
            logger.info(f"执行指令: 移动到方向 {direction} ({duration}s)")
            await self.move_to_direction(direction, duration)
        elif action == "set_direction":
            await self.set_direction(command.get("direction", "center"))
        elif action == "set_mouth":
            state = command.get("state", False)
            logger.info(f"执行指令: 设置嘴巴状态 state={state}")
            await self.set_mouth_state(state)
        elif action == "open_mouth":
            logger.info("执行指令: 张开嘴巴")
            await self.set_mouth_state(True)
        elif action == "close_mouth":
            logger.info("执行指令: 关闭嘴巴")
            await self.set_mouth_state(False)
        elif action == "idle":
            logger.info("执行指令: 回到中心")
            await self.move_to_direction("center", duration=0.6)
        elif action == "stop":
            logger.info("执行指令: 停止控制器")
            self.running = False
        else:
            logger.warning(f"未知指令: {action}")

    async def run(self) -> None:
        self.running = True
        logger.info("Live2D 控制器已启动（持续监听模式）")
        try:
            await asyncio.gather(
                asyncio.create_task(self.idle_movement()),
                asyncio.create_task(self.process_commands()),
            )
        except Exception as e:
            logger.error(f"控制器运行出错: {e}")
        finally:
            self.running = False

    async def stop(self) -> None:
        self.running = False
        logger.info("Live2D 控制器已停止")


# ---------------------------------------------------------------------------
# 模块级入口
# ---------------------------------------------------------------------------

live2d_controller: Live2DMain | None = None


async def initialize_live2d(host="localhost", port=8001, visualize=True):
    """初始化 Live2D 控制器（供外部调用）"""
    global live2d_controller
    try:
        live2d_controller = Live2DMain(host=host, port=port)
        if not await live2d_controller.connect():
            logger.error("连接 VTube Studio 服务器失败")
            return None
        if not await live2d_controller.login():
            await live2d_controller.disconnect()
            logger.error("登录 VTube Studio 失败")
            return None
        if not await live2d_controller.initialize():
            await live2d_controller.disconnect()
            logger.error("初始化失败")
            return None
        await live2d_controller.set_mouth_state(False)

        if visualize:
            try:
                from live2d_param_visualizer import Live2DParamVisualizer
                viz = Live2DParamVisualizer()
                live2d_controller.visualizer = viz
                asyncio.create_task(viz.run())
                # 可视化器单独采样，持锁避免 WS 冲突
                async def _viz_sample():
                    while live2d_controller.running:
                        try:
                            async with live2d_controller.operation_lock:
                                resp = await live2d_controller.api.get_tracking_parameters()
                            if resp and "data" in resp:
                                data = resp["data"]
                                params = []
                                for key in ("defaultParameters", "customParameters"):
                                    for p in data.get(key, []):
                                        params.append({
                                            "name": p.get("name"),
                                            "value": p.get("value", 0),
                                            "min": p.get("min", 0),
                                            "max": p.get("max", 0),
                                            "defaultValue": p.get("defaultValue", 0),
                                            "addedBy": p.get("addedBy", "VTube Studio"),
                                        })
                                viz.update_gui(params)
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)  # 慢采样，减少锁争抢
                asyncio.create_task(_viz_sample())
                logger.info("可视化界面初始化完成")
            except Exception as e:
                logger.debug(f"初始化可视化失败: {e}")

        logger.info("Live2D 控制器初始化完成")
        return live2d_controller
    except Exception as e:
        logger.error(f"初始化 Live2D 控制器失败: {e}")
        if live2d_controller:
            await live2d_controller.disconnect()
        return None


async def send_command(command: dict) -> bool:
    if live2d_controller and live2d_controller.running:
        await live2d_controller.add_command(command)
        return True
    logger.warning("Live2D 控制器未启动或未初始化")
    return False


def live2d_node(state):
    """LangGraph 兼容节点（同步桥接，保留旧接口）。"""
    global live2d_controller
    if not live2d_controller or not live2d_controller.running:
        state["live2d_status"] = "error"
        state["live2d_message"] = "Live2D 控制器未启动"
        return state
    try:
        visual_focus = state.get("visual_focus", "").strip().lower()
        action = state.get("action", "").strip().lower()
        valid = set(VALID_DIRECTIONS)

        if visual_focus and visual_focus in valid:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(live2d_controller.add_command({
                "action": "move_to_direction",
                "direction": visual_focus,
                "duration": 1.5,
            }))
            loop.close()
            state["live2d_status"] = "success"
            state["live2d_message"] = f"已发送视觉焦点指令: {visual_focus}"
        if action:
            amap = {"open_mouth": "open_mouth", "close_mouth": "close_mouth", "idle": "idle"}
            if action in amap:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(live2d_controller.add_command({"action": amap[action]}))
                loop.close()
                state["live2d_status"] = "success"
                state["live2d_message"] = f"已发送动作指令: {action}"
        return state
    except Exception as e:
        state["live2d_status"] = "error"
        state["live2d_message"] = str(e)
        logger.error(f"Live2D 节点执行失败: {e}")
        return state


async def main():
    parser = argparse.ArgumentParser(description="Live2D 模型控制程序（持续监听模式）")
    parser.add_argument("--no-visualize", action="store_true", help="不启动参数可视化界面")
    parser.add_argument("--host", type=str, default="localhost", help="VTube Studio 服务器主机")
    parser.add_argument("--port", type=int, default=8001, help="VTube Studio 服务器端口")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    controller = await initialize_live2d(
        host=args.host, port=args.port, visualize=not args.no_visualize
    )
    if not controller:
        print("初始化失败，退出程序")
        return
    try:
        await controller.run()
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    finally:
        await controller.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
