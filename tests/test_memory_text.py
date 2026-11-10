# -*- coding: utf-8 -*-
"""persona/memory/text.py 纯函数单元测试。"""
from __future__ import annotations

import time

from persona.memory import text as memtext


def test_tokens_extracts_cjk_bigrams_and_english_words():
    tokens = memtext.tokens("我喜欢喝无糖可乐 with Python")
    assert "喜欢" in tokens
    assert "无糖" in tokens
    assert "python" in tokens          # 英文统一小写
    assert "the" not in tokens
    assert "的" not in tokens          # 停用词/单字不进入


def test_tokens_filters_stop_phrases():
    tokens = memtext.tokens("帮我查一下这个功能")
    assert "帮我" not in tokens
    assert "这个" not in tokens
    assert tokens                      # 仍然有有效片段


def test_similarity_high_for_shared_content_low_for_unrelated():
    same = memtext.similarity("我喜欢喝无糖可乐", "我平时喝无糖可乐")
    diff = memtext.similarity("我喜欢喝无糖可乐", "今天天气不错")
    assert same > 0.5
    assert diff < 0.2
    assert same > diff


def test_similarity_returns_zero_on_empty_input():
    assert memtext.similarity("", "无糖可乐") == 0.0
    assert memtext.similarity("无糖可乐", "") == 0.0


def test_phrase_containment_detects_subset_phrase():
    short = "用户喜欢喝无糖可乐"
    long = "用户喜欢喝无糖可乐和果汁"
    assert memtext.phrase_containment(short, long) >= 0.8
    assert memtext.phrase_containment(long, short) >= 0.8
    assert memtext.phrase_containment(short, "今天天气不错") < 0.2


def test_recency_score_recent_and_old():
    recent = memtext.recency_score(memtext.now_str())
    old = memtext.recency_score("2020-01-01 00:00:00")
    assert recent > 0.99
    assert old < 0.01
    assert memtext.recency_score("not-a-time") == 0.5


def test_clip_respects_limit_and_marks_truncation():
    assert memtext.clip("short", 10) == "short"
    clipped = memtext.clip("一二三四五六七八九十", 6)
    assert clipped.endswith("…")
    assert len(clipped) == 6
    assert memtext.clip("abc", 0) == "abc"


def test_visibility_for_known_sources():
    assert memtext.visibility_for("webui", "") == "private"
    assert memtext.visibility_for("qq", "qq:12345") == "private"
    assert memtext.visibility_for("qq", "qq:group:888") == "group"
    assert memtext.visibility_for("bilibili", "") == "public"
    assert memtext.visibility_for("bili_live", "") == "public"


def test_session_key_prefers_chat_key():
    assert memtext.session_key("qq", "u1", "qq:group:1") == "qq:group:1"
    assert memtext.session_key("webui", "u1", "") == "webui:u1"
    assert memtext.session_key("webui", "", "") == "webui:anonymous"
    assert len(memtext.session_key("x", "y" * 300)) <= 160


def test_now_str_format():
    stamp = memtext.now_str()
    assert time.strptime(stamp, "%Y-%m-%d %H:%M:%S")
