# ============================================================
# LLM_v2/domains/cultist/cultist_tools.py — cultist 域工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

CULTIST_TOOL_NAMES = [
    "cs_launch",
    "cs_status",
    "cs_move",
    "cs_start",
    "cs_conclude",
    "cs_wait",
    "cs_start_game",
    "cs_world",
    "cs_knowledge",
    "cs_learn",
]

CULTIST_TOOLS_SCHEMA: list = [
    tool_schema(
        "cs_launch",
        "确保游戏可用：接口没响应时自动启动《密教模拟器》（Steam 或直接运行游戏 exe），"
        "并等待 CSRest 监听 8765。已可用则直接返回。",
        {"wait_sec": {"type": "number",
                      "description": "启动后最多等待多少秒", "default": 180}},
    ),
    tool_schema(
        "cs_status",
        "读取密教模拟器当前局面：遗产、桌面卡片（含属性/描述）、动词（含状态/槽位/待结算产出）、下个时间事件。",
        {},
    ),
    tool_schema(
        "cs_move",
        "把一张卡片放进某个动词的槽位（或移回桌面）。card/verb 可用中文名或 token id。",
        {
            "card": {"type": "string", "description": "卡片名或 id，例如「资金」或 !funds_5"},
            "verb": {"type": "string", "description": "动词名或 id，例如「作业」或 !work_0"},
            "slot": {"type": "string", "description": "槽位名/路径/序号；留空则用第一个空槽", "default": ""},
            "to": {"type": "string", "description": "目标描述：留空=动词槽位；填 tabletop=移回桌面", "default": ""},
        },
        required=["card", "verb"],
    ),
    tool_schema(
        "cs_start",
        "开始一个未开始（Unstarted）动词的配方。",
        {"verb": {"type": "string", "description": "动词名或 id"}},
        required=["verb"],
    ),
    tool_schema(
        "cs_conclude",
        "结算一个已完成（Complete）的动词，返回产出的卡片。",
        {"verb": {"type": "string", "description": "动词名或 id"}},
        required=["verb"],
    ),
    tool_schema(
        "cs_wait",
        "让游戏时间前进指定秒数（快进等待配方完成/卡片衰减）。",
        {"seconds": {"type": "number", "description": "前进秒数，1~180", "default": 10}},
    ),
    tool_schema(
        "cs_start_game",
        "开始新的一局密教模拟器。legacy_id 默认 aspirant（有志青年）。会覆盖当前进度。",
        {"legacy_id": {"type": "string", "description": "遗产 id", "default": "aspirant"}},
    ),
    tool_schema(
        "cs_world",
        "读取本能力域的信息缓存：最近局面、见过的卡片/动词、已观察配方、失败记录与学习经验（不访问游戏，速度快）。",
        {"include_state": {"type": "boolean", "description": "是否带最近局面", "default": True}},
    ),
    tool_schema(
        "cs_knowledge",
        "检索已学到的游戏知识：经验、配方、卡牌/动词目录、失败记录。",
        {
            "query": {"type": "string", "description": "关键词，如「研究 理性」「入梦」", "default": ""},
            "limit": {"type": "integer", "description": "最多返回条数", "default": 8},
        },
    ),
    tool_schema(
        "cs_learn",
        "记录一条游戏知识/经验（自己发现的或从攻略学到的）。",
        {
            "topic": {"type": "string", "description": "主题，如「研究槽位」"},
            "text": {"type": "string", "description": "具体经验，一句话说清"},
            "source": {"type": "string", "description": "来源：play/web/reflection", "default": "play"},
            "confidence": {"type": "number", "description": "置信度 0~1", "default": 0.6},
        },
        required=["topic", "text"],
    ),
]


def tools_schema_for(names: list) -> list:
    return [s for s in CULTIST_TOOLS_SCHEMA if s["function"]["name"] in names]
