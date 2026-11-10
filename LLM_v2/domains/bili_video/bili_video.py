# ============================================================
# LLM_v2/domains/bili_video/bili_video.py — bili_video 域类
# ============================================================
# B 站「了解信息」能力域（只读），与 bili_live（直播间监听）拆分开。
# 类型 Actuator：只提供工具，不 emit 任何事件（故不含声音相关职责）。
# ============================================================

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    SubprocessCapabilityDomain,
    Actuator,
    CapabilityType,
)
from LLM_v2.domains.bili_video.bili_video_http import resolve_sessdata
from LLM_v2.domains.bili_video.bili_video_tools import (
    BV_TOOL_NAMES,
    BV_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)


_BILI_VIDEO_PROMPT = """可用工具（B 站）：
【了解信息 · 只读】
- bv_status: 登录态与风控健康度
- bv_space_info: 用户空间资料（昵称/签名/等级/粉丝/关注）
- bv_space_videos: 用户投稿视频列表
- bv_home_feed: 首页推荐视频流
- bv_video_info: 单个视频详情
- bv_video_subtitle: 视频字幕（CC/AI，带时间戳分段）
- bv_video_comments: 视频评论区主楼（可选楼中楼）
- bv_fav_folders: 列出账号下的收藏夹
- bv_action_status: 查询视频的点赞/投币状态

【对网站进行操作 · 写】
- bv_like: 点赞 / 取消点赞
- bv_coin: 投币（最多 2 枚；不能给自投稿件投币）
- bv_fav: 收藏 / 取消收藏（缺省「默认收藏夹」）

执行原则：
1. 参数优先用 BV 号（如 BV1xx411c7mD）；缺省 uid 表示当前登录账号。
2. 写操作（bv_like/bv_coin/bv_fav）**只在用户明确要求时调用**，不要自作主张；
   未指定视频时会默认作用于「自己空间最新投稿」。
3. 本域**不负责发声**：拿到文本后由主播脑生成解说，声音统一交给 tts 域。
4. 无字幕属于正常情况，返回里会给 hint，不要当作失败反复重试。
"""

# 域配置 → 子进程环境变量（子进程继承 os.environ）
_CONFIG_ENV_MAP = {
    "min_interval_ms": "BILI_VIDEO_MIN_INTERVAL_MS",
    "subtitle_max_chars": "BILI_VIDEO_SUBTITLE_MAX_CHARS",
    "comments_page_size": "BILI_VIDEO_COMMENTS_PAGE_SIZE",
    "default_uid": "BILI_VIDEO_DEFAULT_UID",
}


class BiliVideoDomain(SubprocessCapabilityDomain, Actuator):
    """B 站视频浏览能力域（V2 子进程域，Actuator 类型，只读）"""

    id = "bili_video"
    label = "B 站视频浏览"
    prefix = "bv_"
    default_enabled = False     # 默认关闭（依赖 BILIBILI_SESSDATA 登录态）
    type = CapabilityType.ACTUATOR

    call_timeout = 30.0
    tool_timeouts = {
        "bv_status": 15.0,
        "bv_space_info": 30.0,
        "bv_space_videos": 30.0,
        "bv_home_feed": 30.0,
        "bv_video_info": 30.0,
        "bv_video_subtitle": 45.0,
        "bv_video_comments": 45.0,
        "bv_like": 20.0,
        "bv_coin": 20.0,
        "bv_fav": 60.0,
        "bv_fav_folders": 25.0,
        "bv_action_status": 30.0,
    }

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "bili_video_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(BV_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in BV_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _BILI_VIDEO_PROMPT

    def preflight(self) -> Optional[str]:
        """前置检查：只查环境变量，不联网（真实登录校验放在 bv_status）"""
        if not resolve_sessdata():
            return "BILIBILI_SESSDATA 环境变量未设置（bili_video 域需登录态）"
        return None

    async def start(self) -> None:
        """把域配置透传给子进程（子进程继承 os.environ），再走基类懒启动逻辑"""
        cfg = getattr(self, "world_config", None) or {}
        for key, env_name in _CONFIG_ENV_MAP.items():
            val = cfg.get(key)
            if val is not None:
                # 遵循「环境变量 > 域配置」优先级：已存在的环境变量不被覆盖
                os.environ.setdefault(env_name, str(val))
        await super().start()

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call_child(method, params)


def build_bili_video_domain() -> BiliVideoDomain:
    return BiliVideoDomain()


__all__ = ['BiliVideoDomain', 'build_bili_video_domain']
