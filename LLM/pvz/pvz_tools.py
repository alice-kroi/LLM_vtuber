#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PvZ 语义世界工具 Schema（对齐上游工具协议，提供给操作脑/主播脑消费）。"""

from __future__ import annotations

PVZ_TOOL_NAMES = [
    "pvz_observe",
    "pvz_do",
    "pvz_queue",
    "pvz_arm",
    "pvz_stop",
    "pvz_glance",
]

PVZ_TOOLS_SCHEMA: list = [
    {"type": "function", "function": {
        "name": "pvz_observe",
        "description": "读取 PvZ 当前语义局面：菜单/阶段、太阳与金币、卡片栏（费用/冷却百分比/是否可买）、棋盘每格植物、每排僵尸（血量带/位置/咀嚼）、割草机状态、进行中的任务队列。返回为 JSON。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "pvz_do",
        "description": "向 PvZ 提交有序语义步骤队列并立即返回接纳回执；execution 异步进行，完成后经 pvz_queue 查看终态。支持 menu/选种子/profile/种植/铲除/收集/special 等步骤。",
        "parameters": {"type": "object", "properties": {
            "steps": {"type": "array", "description": "有序步骤数组，每步一个对象（action+参数），如 {\"action\":\"plant\",\"plant\":\"向日葵\",\"row\":1,\"col\":2}"},
            "queue": {"type": "string", "enum": ["replace", "append", "now"], "description": "队列模式（默认 replace）"},
            "task_id": {"type": "string", "description": "可选任务ID（需撤销时用）"},
        }, "required": ["steps"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "pvz_queue",
        "description": "查看运行中/等待中/停靠中任务、已武装触发、最近一次任务终态结果。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "pvz_arm",
        "description": "武装一个触发：当条件（sun/card/cell/zombie 谓词组合）成立时自动提交对应步骤队列。",
        "parameters": {"type": "object", "properties": {
            "when": {"type": "object", "description": "条件表达式，如 {\"zombie\":{\"row\":2}, \"card\":{\"plant\":\"樱桃炸弹\",\"ready\":true}}"},
            "steps": {"type": "array", "description": "条件成立时提交的步骤队列"},
            "expires_in_ms": {"type": "integer"},
            "max_firings": {"type": "integer", "minimum": 1, "maximum": 16},
        }, "required": ["when", "steps"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "pvz_stop",
        "description": "撤回一个任务或触发；都不给则停止一切并释放输入。",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string"},
            "trigger_id": {"type": "string"},
            "all": {"type": "boolean", "description": "置真则全部停止"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "pvz_glance",
        "description": "截取一帧游戏画面 PNG（兼容性诊断用；数据仍走语义过滤，不解密隐藏体）。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
]


def tools_schema_for(names: list) -> list:
    return [s for s in PVZ_TOOLS_SCHEMA if s["function"]["name"] in names]