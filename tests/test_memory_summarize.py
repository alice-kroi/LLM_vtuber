# -*- coding: utf-8 -*-
"""persona/memory/summarize.py 摘要与候选事实抽取单元测试。"""
from __future__ import annotations

import asyncio

import pytest

from persona.memory import summarize as summarize_mod


class _FakeLLM:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _run(coro):
    return asyncio.run(coro)


def test_parse_json_plain_fenced_and_trailing_comma():
    assert summarize_mod._parse_json('{"a": 1}') == {"a": 1}
    assert summarize_mod._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert summarize_mod._parse_json('{"a": 1,}') == {"a": 1}
    assert summarize_mod._parse_json("not json") == {}
    assert summarize_mod._parse_json("") == {}


def test_fallback_summary_is_bounded():
    data = summarize_mod.fallback_summary("问" * 200, "答" * 200)
    assert data["summary"].startswith("用户：")
    assert len(data["summary"]) <= 160
    assert data["candidate_facts"] == []


def test_summarize_turn_parses_and_sanitizes(monkeypatch):
    fake = _FakeLLM({
        "response": (
            '```json\n'
            '{"summary": "用户告知名字与喜好",'
            ' "entities": ["小明", "小明", "无糖可乐", ""],'
            ' "importance": 1.7,'
            ' "candidate_facts": ['
            '  {"kind": "profile", "text": "用户叫小明", "reason": "自我介绍", "confidence": 1.4},'
            '  {"kind": "profile", "text": "用户喜欢无糖可乐", "reason": "偏好", "confidence": -0.3},'
            '  {"kind": "profile", "text": "第三条应被截断", "reason": "x", "confidence": 0.5},'
            '  {"kind": "other", "text": "非法类型", "reason": "x", "confidence": 0.5}'
            ' ]}\n```'
        )
    })
    monkeypatch.setattr(
        "LLM_v2.core.llm_adapter.get_llm_for",
        lambda purpose="general", **kwargs: fake)

    data = _run(summarize_mod.summarize_turn("我叫小明", "好的", purpose="general"))
    assert data["summary"] == "用户告知名字与喜好"
    assert data["entities"] == ["小明", "无糖可乐"]
    assert data["importance"] == 1.0
    assert len(data["candidate_facts"]) == 2
    assert data["candidate_facts"][0]["kind"] == "profile"
    assert data["candidate_facts"][0]["confidence"] == 1.0
    assert data["candidate_facts"][1]["confidence"] == 0.0
    # 摘要调用不应污染会话历史
    assert fake.calls and fake.calls[0]["remember"] is False
    assert fake.calls[0]["temperature"] == 0.1


@pytest.mark.parametrize("result", [
    {"error": "boom"},
    {"response": ""},
    {"response": "不是 JSON"},
])
def test_summarize_turn_falls_back(monkeypatch, result):
    fake = _FakeLLM(result)
    monkeypatch.setattr(
        "LLM_v2.core.llm_adapter.get_llm_for",
        lambda purpose="general", **kwargs: fake)
    data = _run(summarize_mod.summarize_turn("用户问题内容", "回答内容"))
    assert data["summary"].startswith("用户：")
    assert data["candidate_facts"] == []


def test_summarize_turn_falls_back_when_llm_raises(monkeypatch):
    fake = _FakeLLM(RuntimeError("network down"))
    monkeypatch.setattr(
        "LLM_v2.core.llm_adapter.get_llm_for",
        lambda purpose="general", **kwargs: fake)
    data = _run(summarize_mod.summarize_turn("用户问题内容", "回答内容"))
    assert data["summary"].startswith("用户：")


def test_summarize_turn_missing_llm_module_falls_back(monkeypatch):
    def boom(*args, **kwargs):
        raise ImportError("no llm")

    monkeypatch.setattr("LLM_v2.core.llm_adapter.get_llm_for", boom)
    data = _run(summarize_mod.summarize_turn("用户问题内容", "回答内容"))
    assert data["summary"].startswith("用户：")
