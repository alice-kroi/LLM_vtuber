# -*- coding: utf-8 -*-
"""ParamEngine 单元测试：嘴型、目光、情绪偏置、说话包络。"""
from __future__ import annotations

import math

from LLM_v2.domains.live2d.live2d import ParamEngine


def _engine():
    return ParamEngine(clock=lambda: 0.0)


def test_deadzone_keeps_center_still():
    e = _engine()
    e.set_pointer(0.02, -0.02)
    values = e.compute(now=0.0)
    assert values[ParamEngine.EYE_X] == 0.0
    assert values[ParamEngine.EYE_Y] == 0.0
    assert values[ParamEngine.MOUTH_OPEN] == 0.0


def test_pointer_moves_eyes_and_mouth():
    e = _engine()
    e.set_pointer(0.8, 0.0)
    values = e.compute(now=0.0)
    assert values[ParamEngine.EYE_LEFT_X] > 0.5
    assert values[ParamEngine.EYE_RIGHT_X] > 0.5
    assert values[ParamEngine.MOUTH_OPEN] > 0.0
    assert values[ParamEngine.MOUTH_SMILE] == 0.0


def test_pointer_is_normalized_to_circle():
    e = _engine()
    e.set_pointer(1.0, 1.0)
    values = e.compute(now=0.0)
    assert math.hypot(values[ParamEngine.EYE_LEFT_X],
                      values[ParamEngine.EYE_LEFT_Y]) <= 1.0001


def test_mood_bias_changes_mouth_and_gaze():
    e = _engine()
    e.set_mood("anxious")
    values = e.compute(now=0.0)
    assert values[ParamEngine.EYE_LEFT_Y] > 0.0
    # 缓动需要几轮才接近目标；MouthSmile 只保留正向，负向交给表情层
    for _ in range(8):
        values = e.compute(now=0.0)
    assert e.status()["mouth"][1] < 0.0
    assert values[ParamEngine.MOUTH_SMILE] == 0.0


def test_speaking_envelope_opens_mouth():
    e = _engine()
    e.start_speaking(2000, now=0.0)
    values = e.compute(now=0.1)
    assert values[ParamEngine.MOUTH_OPEN] > 0.05


def test_neutral_smile_baseline():
    e = _engine()
    e.set_neutral_smile(0.5)
    values = e.compute(now=0.0)
    assert abs(values[ParamEngine.MOUTH_SMILE] - 0.5) < 0.001


def test_values_stay_in_range():
    e = _engine()
    e.set_pointer(1.0, -1.0)
    e.start_speaking(1000, now=0.0)
    for t in (0.0, 0.1, 0.5, 1.5):
        values = e.compute(now=t)
        for name in (ParamEngine.EYE_LEFT_X, ParamEngine.EYE_LEFT_Y,
                     ParamEngine.EYE_RIGHT_X, ParamEngine.EYE_RIGHT_Y):
            assert -1.0 <= values[name] <= 1.0
        assert 0.0 <= values[ParamEngine.MOUTH_OPEN] <= 1.0
        assert 0.0 <= values[ParamEngine.MOUTH_SMILE] <= 1.0


def test_status_shape():
    e = _engine()
    e.set_mood("excited")
    e.set_pointer(0.3, -0.2)
    e.compute(now=0.0)
    status = e.status()
    assert status["mood"] == "excited"
    assert len(status["pointer"]) == 2
    assert len(status["gaze"]) == 2
    assert len(status["mouth"]) == 2
