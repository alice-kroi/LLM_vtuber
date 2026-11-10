# ============================================================
# LLM_v2/domains/bili_video/bili_video_tools.py — bili_video 域工具 Schema
# ============================================================
from __future__ import annotations

from LLM_v2.core.capability_domain import tool_schema

BV_TOOL_NAMES = [
    # 了解信息（只读）
    "bv_status",
    "bv_space_info",
    "bv_space_videos",
    "bv_home_feed",
    "bv_video_info",
    "bv_video_subtitle",
    "bv_video_comments",
    # 对网站进行操作（写，需 csrf / bili_jct）
    "bv_like",
    "bv_coin",
    "bv_fav",
    "bv_fav_folders",
    "bv_action_status",
]

BV_TOOLS_SCHEMA: list = [
    tool_schema(
        "bv_status",
        "查看 B 站登录态与风控健康度（uid / 昵称 / 等级 / WBI key 年龄 / 最近错误）。",
    ),
    tool_schema(
        "bv_space_info",
        "读取 B 站用户空间资料（昵称 / 签名 / 等级 / 粉丝数 / 关注数）。",
        {"uid": {"type": "integer",
                 "description": "目标用户 UID（缺省=当前登录账号）", "default": 0}},
    ),
    tool_schema(
        "bv_space_videos",
        "读取某用户的投稿视频列表（按发布时间倒序）。",
        {
            "uid": {"type": "integer",
                    "description": "目标用户 UID（缺省=当前登录账号）", "default": 0},
            "pn": {"type": "integer", "description": "页码（从 1 开始）", "default": 1},
            "ps": {"type": "integer", "description": "每页数量（默认 30）", "default": 30},
            "keyword": {"type": "string", "description": "可选：按标题关键词过滤"},
        },
    ),
    tool_schema(
        "bv_home_feed",
        "读取 B 站首页推荐视频流（当前登录账号的推荐）。",
        {"ps": {"type": "integer", "description": "返回条数（默认 10）", "default": 10}},
    ),
    tool_schema(
        "bv_video_info",
        "读取单个视频详情（标题 / 简介 / UP主 / 分P / 播放点赞等统计）。",
        {
            "bvid": {"type": "string", "description": "BV 号，如 BV1xx411c7mD"},
            "aid": {"type": "integer", "description": "av 号（与 bvid 二选一）", "default": 0},
        },
    ),
    tool_schema(
        "bv_video_subtitle",
        "读取视频字幕（CC 字幕 / AI 字幕），返回带时间戳的分段文本。无字幕时返回 hint。",
        {
            "bvid": {"type": "string", "description": "BV 号，如 BV1xx411c7mD"},
            "aid": {"type": "integer", "description": "av 号（与 bvid 二选一）", "default": 0},
            "cid": {"type": "integer", "description": "分P cid（缺省自动取第 1 个分P）", "default": 0},
        },
    ),
    tool_schema(
        "bv_video_comments",
        "读取视频评论区主楼（可选附带前 3 条楼中楼）。",
        {
            "bvid": {"type": "string", "description": "BV 号，如 BV1xx411c7mD"},
            "oid": {"type": "integer",
                    "description": "评论区 oid（= av 号，与 bvid 二选一）", "default": 0},
            "pn": {"type": "integer", "description": "页码（从 1 开始）", "default": 1},
            "sort": {"type": "integer", "description": "1=按时间，2=按热度", "default": 1},
            "with_replies": {"type": "boolean",
                             "description": "是否附带前 3 条楼中楼", "default": False},
        },
    ),
    # ---- 对网站进行操作（写操作，需登录态 + csrf）----
    tool_schema(
        "bv_like",
        "给视频点赞或取消点赞（**写操作**，会真实改变账号状态）。",
        {
            "bvid": {"type": "string", "description": "BV 号；与 aid 二选一，都不给则用自己空间最新投稿"},
            "aid": {"type": "integer", "description": "av 号（与 bvid 二选一）", "default": 0},
            "like": {"type": "boolean", "description": "true=点赞，false=取消点赞", "default": True},
        },
    ),
    tool_schema(
        "bv_coin",
        "给视频投币（**写操作**，最多 2 枚）。注意：B 站不允许给自己投稿的视频投币。",
        {
            "bvid": {"type": "string", "description": "BV 号；与 aid 二选一，都不给则用自己空间最新投稿"},
            "aid": {"type": "integer", "description": "av 号（与 bvid 二选一）", "default": 0},
            "multiply": {"type": "integer", "description": "投币数量（1 或 2）", "default": 1},
            "select_like": {"type": "boolean", "description": "是否同时点赞", "default": False},
        },
    ),
    tool_schema(
        "bv_fav",
        "收藏视频到收藏夹（**写操作**）。media_ids 缺省用「默认收藏夹」。",
        {
            "bvid": {"type": "string", "description": "BV 号；与 aid 二选一，都不给则用自己空间最新投稿"},
            "aid": {"type": "integer", "description": "av 号（与 bvid 二选一）", "default": 0},
            "media_ids": {"type": "array", "items": {"type": "integer"},
                          "description": "目标收藏夹 id 列表（缺省=默认收藏夹）"},
            "remove": {"type": "boolean", "description": "true=取消收藏", "default": False},
        },
    ),
    tool_schema(
        "bv_fav_folders",
        "列出账号下的收藏夹（id / 名称 / 内容数），只读。",
        {"uid": {"type": "integer",
                 "description": "目标用户 UID（缺省=当前登录账号）", "default": 0}},
    ),
    tool_schema(
        "bv_action_status",
        "查询视频的点赞 / 投币状态（只读，用于确认写操作是否生效）。",
        {
            "bvid": {"type": "string", "description": "BV 号；与 aid 二选一"},
            "aid": {"type": "integer", "description": "av 号（与 bvid 二选一）", "default": 0},
        },
    ),
]


BV_WRITE_TOOL_NAMES = ["bv_like", "bv_coin", "bv_fav"]


def tools_schema_for(names: list) -> list:
    return [s for s in BV_TOOLS_SCHEMA if s["function"]["name"] in names]
