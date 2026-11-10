# -*- coding: utf-8 -*-
"""Live2DDomain 表情分层 / 动作冷却 / 表情校验测试。"""
from __future__ import annotations

import asyncio
import time

from LLM_v2.domains.live2d.live2d import Live2DDomain


class FakeAPI:
    def __init__(self, available=None, connected=True,
                 tracking_defaults=None):
        self.websocket = object() if connected else None
        self.available = list(available or [
            "smile_01", "smile_02", "calm_01", "sad_01", "sad_02",
            "angry_01", "neutral_01",
        ])
        self.tracking_defaults = dict(tracking_defaults or {})
        self.activations = []
        self.hotkeys = []
        self.param_calls = []
        self.connect_calls = 0

    async def connect(self):
        self.connect_calls += 1
        if self.websocket is not None:
            return True
        return False

    async def get_expression_state(self, details=False):
        return {"messageType": "ExpressionStateResponse", "data": {
            "expressions": [
                {"name": name, "active": False} for name in self.available
            ]}}

    async def activate_expression(self, file, active, fade_time=0.25):
        self.activations.append((file, active, fade_time))
        return {"messageType": "ExpressionActivationResponse", "data": {}}

    async def trigger_hotkey(self, hotkey_id, item_instance_id=None):
        self.hotkeys.append(hotkey_id)
        return {"messageType": "HotkeyTriggeredResponse", "data": {}}

    async def inject_parameter_data(self, values, face_found=False,
                                    mode="set"):
        self.param_calls.append((list(values), face_found, mode))
        return {"messageType": "InjectParameterDataResponse", "data": {}}

    async def get_tracking_parameters(self):
        names = ["EyeLeftX", "EyeLeftY", "EyeRightX", "EyeRightY",
                 "MouthOpen", "MouthSmile"]
        return {"messageType": "InputParameterListResponse", "data": {
            "defaultParameters": [
                {"name": name, "defaultValue": self.tracking_defaults.get(name, 0.0),
                 "min": -1, "max": 1} for name in names
            ],
            "customParameters": [],
        }}

    async def disconnect(self):
        pass


def _domain(available=None, connected=True, tracking_defaults=None):
    d = Live2DDomain()
    d._vts = FakeAPI(available=available, connected=connected,  # noqa: SLF001
                     tracking_defaults=tracking_defaults)
    d._load_vts = lambda: None  # noqa: SLF001
    return d


def _run(coro):
    return asyncio.run(coro)


def test_base_expression_from_quadrant():
    d = _domain()
    _run(d._on_expression({"quadrant": "exuberant"}))
    assert d._vts.activations[-1][0] == "smile_01"  # noqa: SLF001
    assert d._last_expression == "smile_01"  # noqa: SLF001


def test_reaction_temporarily_overrides_base_and_restores():
    d = _domain()
    _run(d._on_expression({"quadrant": "exuberant"}))
    _run(d._on_error({}))
    assert d._vts.activations[-1][0] == "sad_01"  # noqa: SLF001

    d._reaction_until = time.monotonic() - 1  # noqa: SLF001
    _run(d._apply_effective())
    assert d._vts.activations[-1][0] == "smile_01"  # noqa: SLF001
    assert d._reaction_expression is None  # noqa: SLF001


def test_lower_priority_reaction_does_not_override_failure():
    d = _domain()
    _run(d._on_error({}))
    assert d._vts.activations[-1][0] == "sad_01"  # noqa: SLF001
    _run(d._on_operator_done({}))
    assert d._vts.activations[-1][0] == "sad_01"  # noqa: SLF001


def test_transient_expression_payload():
    d = _domain()
    _run(d._on_expression({
        "quadrant": "angry", "transient": True,
        "duration_ms": 1000, "priority": 3,
    }))
    assert d._vts.activations[-1][0] == "angry_01"  # noqa: SLF001
    assert d._base_expression is None  # noqa: SLF001


def test_motion_cooldown_and_duration():
    d = _domain()
    _run(d._on_motion({"motion_id": "hk1"}))
    assert d._vts.hotkeys == ["hk1"]  # noqa: SLF001
    _run(d._on_motion({"motion_id": "hk1"}))
    assert d._vts.hotkeys == ["hk1"]  # noqa: SLF001

    d._last_motion_at -= 2.0  # noqa: SLF001
    _run(d._on_motion({"motion_id": "hk1"}))
    assert d._vts.hotkeys == ["hk1", "hk1"]  # noqa: SLF001


def test_motion_with_expression_pushes_reaction():
    d = _domain()
    _run(d._on_motion({
        "motion_id": "hk2", "expression": "calm", "duration_ms": 500,
    }))
    assert d._vts.activations[-1][0] == "calm_01"  # noqa: SLF001
    assert d._reaction_expression == "calm_01"  # noqa: SLF001


def test_expression_fallback_when_missing():
    d = _domain(available=["neutral_01"])
    _run(d._on_expression({"quadrant": "exuberant"}))
    assert d._vts.activations[-1][0] == "neutral_01"  # noqa: SLF001


def test_connect_failure_cooldown():
    d = _domain(connected=False)
    assert _run(d._ensure_connected()) is False
    assert _run(d._ensure_connected()) is False
    assert d._vts.connect_calls == 1  # noqa: SLF001


def test_param_tick_injects_mouth_and_gaze():
    d = _domain()
    d._sample_pointer = lambda: (0.5, 0.0)  # noqa: SLF001
    _run(d._param_tick())
    rows, face_found, mode = d._vts.param_calls[-1]  # noqa: SLF001
    ids = {row["id"] for row in rows}
    assert ids == {
        "EyeLeftX", "EyeLeftY", "EyeRightX", "EyeRightY",
        "MouthOpen", "MouthSmile",
    }
    assert face_found is False
    assert mode == "set"
    assert d._param_values  # noqa: SLF001


def test_tts_speak_opens_mouth():
    d = _domain()
    d._sample_pointer = lambda: (0.0, 0.0)  # noqa: SLF001
    _run(d._on_tts_speak({"text": "你好世界"}))
    _run(d._param_tick())
    rows = {row["id"]: row["value"] for row in d._vts.param_calls[-1][0]}  # noqa: SLF001
    assert rows["MouthOpen"] > 0.0


def test_mood_updates_gaze_bias():
    d = _domain()
    d._sample_pointer = lambda: (0.0, 0.0)  # noqa: SLF001
    _run(d._on_expression({"quadrant": "anxious"}))
    _run(d._param_tick())
    rows = {row["id"]: row["value"] for row in d._vts.param_calls[-1][0]}  # noqa: SLF001
    assert rows["EyeLeftY"] > 0.0


def test_release_params_writes_baseline():
    d = _domain()
    d._sample_pointer = lambda: (0.5, 0.2)  # noqa: SLF001
    _run(d._param_tick())
    _run(d._release_params())
    rows = d._vts.param_calls[-1][0]  # noqa: SLF001
    assert all(row["value"] == 0.0 for row in rows)
    assert d._param_values == {}  # noqa: SLF001


def test_tracking_baseline_imports_mouth_smile():
    d = _domain(tracking_defaults={"MouthSmile": 0.5})
    d._sample_pointer = lambda: (0.0, 0.0)  # noqa: SLF001
    _run(d._param_tick())
    rows = {row["id"]: row["value"] for row in d._vts.param_calls[-1][0]}  # noqa: SLF001
    assert abs(rows["MouthSmile"] - 0.5) < 0.01
