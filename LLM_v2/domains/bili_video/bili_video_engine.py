# -*- coding: utf-8 -*-
# ============================================================
# LLM_v2/domains/bili_video/bili_video_engine.py — bili_video 域子进程入口
# ============================================================
# JSON 行式 stdin/stdout 协议（与 SubprocessCapabilityDomain 对齐）。
#
# 职责边界：只做「了解信息」——读 B 站空间 / 投稿 / 首页推荐 / 视频详情 / 字幕 / 评论。
# **不含任何音频职责**：本域不合成、不播放、不产出声音；发声统一由 tts 域负责。
# 返回约定：与 launcher 域一致，业务失败返回 {"success": false, "error": ...}。
# ============================================================

from __future__ import annotations

import json
import os
import sys

# 注入项目根到 sys.path（子进程独立启动需要）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _PROJECT_ROOT)

from LLM_v2.domains.bili_video.bili_video_api import BiliVideoAPI
from LLM_v2.domains.bili_video.bili_video_http import BiliHTTP, resolve_sessdata

_api: BiliVideoAPI | None = None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name) or ""
    try:
        return int(raw)
    except ValueError:
        return default


def _get_api() -> BiliVideoAPI:
    """懒构造：首次工具调用才建立 HTTP 会话（preflight 已保证 SESSDATA 存在）"""
    global _api
    if _api is None:
        http = BiliHTTP(
            sessdata=resolve_sessdata() or "",
            jct=os.environ.get("BILIBILI_JCT") or None,
            min_interval_ms=_env_int("BILI_VIDEO_MIN_INTERVAL_MS", 1200),
        )
        _api = BiliVideoAPI(
            http,
            subtitle_max_chars=_env_int("BILI_VIDEO_SUBTITLE_MAX_CHARS", 8000),
            comments_page_size=_env_int("BILI_VIDEO_COMMENTS_PAGE_SIZE", 20),
            default_uid=_env_int("BILI_VIDEO_DEFAULT_UID", 0),
        )
    return _api


def _call(method: str, p: dict):
    api = _get_api()
    if method == "bv_status":
        return api.status()
    if method == "bv_space_info":
        return api.space_info(uid=p.get("uid") or None)
    if method == "bv_space_videos":
        return api.space_videos(uid=p.get("uid") or None, pn=p.get("pn", 1),
                                ps=p.get("ps", 30), keyword=p.get("keyword") or None)
    if method == "bv_home_feed":
        return api.home_feed(ps=p.get("ps", 10))
    if method == "bv_video_info":
        return api.video_info(bvid=p.get("bvid") or None, aid=p.get("aid") or None)
    if method == "bv_video_subtitle":
        return api.video_subtitle(bvid=p.get("bvid") or None,
                                  aid=p.get("aid") or None,
                                  cid=p.get("cid") or None)
    if method == "bv_video_comments":
        return api.video_comments(bvid=p.get("bvid") or None,
                                  oid=p.get("oid") or None,
                                  pn=p.get("pn", 1), sort=p.get("sort", 1),
                                  with_replies=bool(p.get("with_replies")))
    # ---- 对网站进行操作（写）----
    if method == "bv_like":
        return api.like(bvid=p.get("bvid") or None, aid=p.get("aid") or None,
                        on=bool(p.get("like", True)), allow_mine=True)
    if method == "bv_coin":
        return api.coin(bvid=p.get("bvid") or None, aid=p.get("aid") or None,
                        multiply=p.get("multiply", 1),
                        select_like=bool(p.get("select_like")), allow_mine=True)
    if method == "bv_fav":
        return api.fav(bvid=p.get("bvid") or None, aid=p.get("aid") or None,
                       media_ids=p.get("media_ids") or None,
                       remove=bool(p.get("remove")), allow_mine=True)
    if method == "bv_fav_folders":
        return api.fav_folders(uid=p.get("uid") or None)
    if method == "bv_action_status":
        return api.action_status(bvid=p.get("bvid") or None, aid=p.get("aid") or None)
    raise ValueError(f"未知方法: {method}")


def handle(method: str, params: dict):
    p = params or {}
    if method == "__handshake__":
        return {"ready": True, "engine": "bili_video", "protocol_version": 2}
    if method == "__shutdown__":
        return {"stopping": True}
    try:
        data = _call(method, p)
        if isinstance(data, dict) and "success" in data:
            return data
        return {"success": True, **(data if isinstance(data, dict) else {"data": data})}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _main_loop():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        rid = None
        try:
            req = json.loads(raw)
            rid = req.get("id")
            result = handle(req.get("method"), req.get("params") or {})
            sys.stdout.write(json.dumps({"result": result, "id": rid},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()
            if req.get("method") == "__shutdown__":
                break
        except Exception as e:  # noqa: BLE001
            sys.stdout.write(json.dumps({"error": str(e), "id": rid},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main():
    if "--smoke" in sys.argv:
        sys.stdout.write(json.dumps({"result": {"ok": True, "smoke": True}}) + "\n")
        sys.stdout.flush()
        return
    _main_loop()


if __name__ == "__main__":
    main()
