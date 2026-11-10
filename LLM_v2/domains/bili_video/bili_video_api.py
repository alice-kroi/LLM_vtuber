# -*- coding: utf-8 -*-
# ============================================================
# LLM_v2/domains/bili_video/bili_video_api.py — B 站视频域接口封装（只读）
# ============================================================
# 本文件只做「了解信息」：空间资料 / 投稿列表 / 首页推荐 / 视频详情 / 字幕 / 评论。
# 不含任何声音相关职责（发声统一由 tts 域负责）。
# ============================================================

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from LLM_v2.domains.bili_video.bili_video_http import (
    BiliHTTP,
    BiliRequestError,
    unescape_text,
)

logger = logging.getLogger(__name__)

_REFERER_SPACE = "https://space.bilibili.com/{uid}"
_REFERER_VIDEO = "https://www.bilibili.com/video/{bvid}"
_REFERER_FEED = "https://www.bilibili.com/"

_BV_PATTERN = re.compile(r"(BV[0-9A-Za-z]{10})")


def normalize_bvid(text: Any) -> Optional[str]:
    """从 BV 号 / 视频链接中提取标准 BV 号"""
    if not text:
        return None
    m = _BV_PATTERN.search(str(text))
    return m.group(1) if m else None


class BiliVideoAPI:
    """只读接口封装。构造函数不联网，首次调用才发请求。"""

    def __init__(self, http: BiliHTTP, subtitle_max_chars: int = 8000,
                 comments_page_size: int = 20, default_uid: int = 0):
        self._http = http
        self._subtitle_max_chars = max(500, int(subtitle_max_chars))
        self._comments_page_size = max(1, min(20, int(comments_page_size)))
        self._default_uid = int(default_uid or 0)
        self._nav_cache: Optional[Dict[str, Any]] = None

    # ---------- 内部 ----------

    def _nav(self) -> Dict[str, Any]:
        if self._nav_cache is None:
            self._nav_cache = self._http.get("/x/web-interface/nav") or {}
        return self._nav_cache

    def _resolve_uid(self, uid: Optional[int]) -> int:
        if uid:
            return int(uid)
        if self._default_uid:
            return self._default_uid
        mid = self._nav().get("mid")
        if not mid:
            raise BiliRequestError("未登录，无法确定 UID（请设置 BILIBILI_SESSDATA）")
        return int(mid)

    def _resolve_aid(self, bvid: Optional[str], aid: Optional[int]) -> Dict[str, Any]:
        """把 bvid/aid 归一化成 (bvid, aid, cid)；cid 供字幕接口使用"""
        norm_bvid = normalize_bvid(bvid) or bvid
        if norm_bvid:
            info = self.video_info(bvid=norm_bvid)
        elif aid:
            info = self.video_info(aid=aid)
        else:
            raise BiliRequestError("需要 bvid 或 aid 之一")
        return {"bvid": info.get("bvid"), "aid": info.get("aid"), "cid": info.get("cid")}

    def _video_ref(self, bvid: Optional[str], aid: Optional[int],
                   allow_mine: bool = False) -> Dict[str, Any]:
        """轻量构造视频引用（不额外请求）；allow_mine 时缺省取自己空间最新投稿"""
        norm_bvid = normalize_bvid(bvid) or bvid
        if norm_bvid:
            return {"bvid": norm_bvid}
        if aid:
            return {"aid": int(aid)}
        if allow_mine:
            mine = self.space_videos(uid=None, ps=1)
            lst = mine.get("list") or []
            if not lst:
                raise BiliRequestError("未指定视频，且你的空间没有投稿")
            return {"bvid": lst[0].get("bvid")}
        raise BiliRequestError("需要 bvid 或 aid 之一")

    # ---------- 登录态 ----------

    def status(self) -> Dict[str, Any]:
        nav = self._nav()
        return {
            "logged_in": bool(nav.get("isLogin")),
            "uid": nav.get("mid"),
            "name": nav.get("uname"),
            "level": (nav.get("level_info") or {}).get("current_level"),
            "wbi_age_s": self._http.wbi_age_seconds(),
            "recent_errors": self._http.recent_errors(),
        }

    # ---------- 空间 ----------

    def space_info(self, uid: Optional[int] = None) -> Dict[str, Any]:
        target = self._resolve_uid(uid)
        referer = _REFERER_SPACE.format(uid=target)
        info = self._http.get(
            "/x/space/wbi/acc/info",
            {"mid": target, "platform": "web", "web_location": "1550101"},
            referer=referer, wbi=True) or {}
        try:
            stat = self._http.get("/x/relation/stat", {"vmid": target},
                                  referer=referer) or {}
        except BiliRequestError as e:
            logger.debug("[bili_video] relation/stat 失败: %s", e)
            stat = {}
        return {
            "uid": target,
            "name": info.get("name"),
            "sign": info.get("sign"),
            "level": info.get("level"),
            "sex": info.get("sex"),
            "fans": stat.get("follower"),
            "following": stat.get("following"),
        }

    def space_videos(self, uid: Optional[int] = None, pn: int = 1,
                     ps: int = 30, keyword: Optional[str] = None) -> Dict[str, Any]:
        target = self._resolve_uid(uid)
        params: Dict[str, Any] = {
            "mid": target, "ps": max(1, min(50, int(ps))), "pn": max(1, int(pn)),
            "order": "pubdate", "platform": "web", "web_location": "1550101",
        }
        if keyword:
            params["keyword"] = keyword
        data = self._http.get("/x/space/wbi/arc/search", params,
                              referer=_REFERER_SPACE.format(uid=target), wbi=True) or {}
        vlist = ((data.get("list") or {}).get("vlist")) or []
        return {
            "uid": target,
            "page": int(pn),
            "total": (data.get("page") or {}).get("count"),
            "count": len(vlist),
            "list": [{
                "bvid": v.get("bvid"),
                "aid": v.get("aid"),
                "title": v.get("title"),
                "play": v.get("play"),
                "duration": v.get("length"),
                "created": v.get("created"),
            } for v in vlist],
        }

    # ---------- 首页推荐 ----------

    def home_feed(self, ps: int = 10) -> Dict[str, Any]:
        params = {
            "ps": max(1, min(30, int(ps))),
            "fresh_type": 3,
            "feed_version": "V8",
            "web_location": "1430650",
        }
        data = self._http.get("/x/web-interface/wbi/index/top/feed/rcmd", params,
                              referer=_REFERER_FEED, wbi=True) or {}
        items = data.get("item") or []
        out: List[Dict[str, Any]] = []
        for it in items:
            bvid = it.get("bvid") or it.get("id")
            if not bvid:
                continue
            stat = it.get("stat") or {}
            out.append({
                "bvid": bvid,
                "title": it.get("title"),
                "owner": (it.get("owner") or {}).get("name"),
                "duration": it.get("duration"),
                "view": stat.get("view"),
                "like": stat.get("like"),
            })
        return {"count": len(out), "list": out}

    # ---------- 视频详情 ----------

    def video_info(self, bvid: Optional[str] = None,
                   aid: Optional[int] = None) -> Dict[str, Any]:
        norm_bvid = normalize_bvid(bvid) or bvid
        params: Dict[str, Any] = {}
        if norm_bvid:
            params["bvid"] = norm_bvid
        elif aid:
            params["aid"] = int(aid)
        else:
            raise BiliRequestError("需要 bvid 或 aid 之一")
        data = self._http.get(
            "/x/web-interface/wbi/view", params,
            referer=_REFERER_VIDEO.format(bvid=norm_bvid or ""), wbi=True) or {}
        pages = data.get("pages") or []
        stat = data.get("stat") or {}
        return {
            "bvid": data.get("bvid"),
            "aid": data.get("aid"),
            "cid": data.get("cid"),
            "title": data.get("title"),
            "desc": (data.get("desc") or "")[:500],
            "owner": {"mid": (data.get("owner") or {}).get("mid"),
                      "name": (data.get("owner") or {}).get("name")},
            "duration": data.get("duration"),
            "pubdate": data.get("pubdate"),
            "pages": [{"cid": p.get("cid"), "page": p.get("page"),
                       "part": p.get("part"), "duration": p.get("duration")}
                      for p in pages[:20]],
            "stat": {k: stat.get(k) for k in ("view", "danmaku", "reply", "like", "favorite")},
        }

    # ---------- 字幕 ----------

    def video_subtitle(self, bvid: Optional[str] = None, aid: Optional[int] = None,
                       cid: Optional[int] = None) -> Dict[str, Any]:
        ids = self._resolve_aid(bvid, aid)
        target_bvid = ids.get("bvid")
        target_cid = int(cid) if cid else ids.get("cid")
        if not target_cid:
            raise BiliRequestError("缺少 cid，且无法从视频详情取得")
        params = {"cid": target_cid}
        if target_bvid:
            params["bvid"] = target_bvid
        elif ids.get("aid"):
            params["aid"] = ids["aid"]
        data = self._http.get("/x/player/wbi/v2", params,
                              referer=_REFERER_VIDEO.format(bvid=target_bvid or ""),
                              wbi=True) or {}
        subs = ((data.get("subtitle") or {}).get("subtitles")) or []
        if not subs:
            return {"source": "none", "bvid": target_bvid, "cid": target_cid,
                    "segments": [], "text": "",
                    "hint": "该视频没有 CC/AI 字幕（AI 字幕风控更严，可考虑二期 ASR 兜底）"}

        # 优先中文，其次第一条
        chosen = next(
            (s for s in subs if str(s.get("lan") or "").lower().startswith(("zh", "ai-zh"))),
            subs[0])
        url = chosen.get("subtitle_url") or ""
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            return {"source": "none", "bvid": target_bvid, "cid": target_cid,
                    "segments": [], "text": "", "hint": "字幕条目存在但无可用地址"}
        body = self._http.get_raw_json(url,
                                       referer=_REFERER_VIDEO.format(bvid=target_bvid or "")) or {}
        lan = str(chosen.get("lan") or "")
        segments: List[Dict[str, Any]] = []
        total_chars = 0
        truncated = False
        for seg in (body.get("body") or []):
            content = unescape_text(seg.get("content"))
            if total_chars + len(content) > self._subtitle_max_chars:
                truncated = True
                break
            total_chars += len(content)
            segments.append({"from": seg.get("from"), "to": seg.get("to"),
                             "content": content})
        return {
            "source": "ai" if "ai" in lan.lower() else "cc",
            "bvid": target_bvid,
            "cid": target_cid,
            "lan": lan,
            "lan_doc": chosen.get("lan_doc"),
            "segments": segments,
            "truncated": truncated,
            "text": "\n".join(s["content"] for s in segments),
        }

    # ---------- 评论 ----------

    def video_comments(self, bvid: Optional[str] = None, oid: Optional[int] = None,
                       pn: int = 1, sort: int = 1,
                       with_replies: bool = False) -> Dict[str, Any]:
        ids = self._resolve_aid(bvid, oid)
        target_oid = ids.get("aid") or oid
        if not target_oid:
            raise BiliRequestError("需要 bvid 或 oid 之一")
        params = {
            "oid": int(target_oid),
            "type": 1,                      # 1 = 视频
            "mode": 3 if int(sort) == 2 else 2,   # 2 时间序 / 3 热度序
            "pn": max(1, int(pn)),
            "ps": self._comments_page_size,
            "web_location": "1315875",
        }
        data = self._http.get("/x/v2/reply/wbi/main", params,
                              referer=_REFERER_VIDEO.format(bvid=ids.get("bvid") or ""),
                              wbi=True) or {}
        replies = data.get("replies") or []
        out: List[Dict[str, Any]] = []
        for r in replies:
            content = r.get("content")
            message = content.get("message") if isinstance(content, dict) else ""
            item: Dict[str, Any] = {
                "rpid": r.get("rpid"),
                "user": (r.get("member") or {}).get("uname"),
                "message": unescape_text(message),
                "like": r.get("like"),
                "ctime": r.get("ctime"),
                "rcount": r.get("rcount"),
            }
            if with_replies and item["rcount"]:
                try:
                    sub = self._http.get(
                        "/x/v2/reply/reply",
                        {"oid": int(target_oid), "type": 1, "root": item["rpid"],
                         "pn": 1, "ps": 3},
                        referer=_REFERER_VIDEO.format(bvid=ids.get("bvid") or "")) or {}
                    item["sub_replies"] = [{
                        "user": (x.get("member") or {}).get("uname"),
                        "message": unescape_text((x.get("content") or {}).get("message")),
                    } for x in (sub.get("replies") or [])[:3]]
                except BiliRequestError as e:
                    item["sub_replies"] = []
                    item["sub_replies_error"] = str(e)
            out.append(item)
        return {
            "bvid": ids.get("bvid"),
            "oid": int(target_oid),
            "page": int(pn),
            "count": len(out),
            "replies": out,
            "total": (data.get("cursor") or {}).get("all_count"),
        }


    # ---------- 写操作（对网站进行操作，二期）----------

    def like(self, bvid: Optional[str] = None, aid: Optional[int] = None,
             on: bool = True, allow_mine: bool = False) -> Dict[str, Any]:
        """点赞 / 取消点赞（POST，需 csrf）"""
        ref = self._video_ref(bvid, aid, allow_mine=allow_mine)
        self._http.post(
            "/x/web-interface/archive/like",
            {**ref, "like": 1 if on else 2},
            referer=_REFERER_VIDEO.format(bvid=ref.get("bvid") or ""))
        return {**ref, "action": "like" if on else "unlike",
                "liked": bool(on), "success": True}

    def coin(self, bvid: Optional[str] = None, aid: Optional[int] = None,
             multiply: int = 1, select_like: bool = False,
             allow_mine: bool = False) -> Dict[str, Any]:
        """投币（POST，需 csrf）。B 站规则：不能给自己投稿的视频投币（34002）"""
        ref = self._video_ref(bvid, aid, allow_mine=allow_mine)
        nums = max(1, min(2, int(multiply or 1)))
        data = self._http.post(
            "/x/web-interface/coin/add",
            {**ref, "multiply": nums, "select_like": 1 if select_like else 0},
            referer=_REFERER_VIDEO.format(bvid=ref.get("bvid") or "")) or {}
        return {**ref, "multiply": nums, "like": data.get("like"), "success": True}

    def fav_folders(self, uid: Optional[int] = None) -> Dict[str, Any]:
        """列出账号下的收藏夹（含 id / 名称 / 内容数）"""
        target = self._resolve_uid(uid)
        data = self._http.get(
            "/x/v3/fav/folder/created/list-all", {"up_mid": target},
            referer=_REFERER_SPACE.format(uid=target)) or {}
        lst = data.get("list") or []
        return {
            "uid": target,
            "count": len(lst),
            "folders": [{"id": f.get("id"), "title": f.get("title"),
                         "media_count": f.get("media_count"),
                         "attr": f.get("attr")} for f in lst],
        }

    def default_fav_folder_id(self, uid: Optional[int] = None) -> int:
        """默认收藏夹 id：优先标题为「默认收藏夹」的，否则取第一个"""
        folders = self.fav_folders(uid).get("folders") or []
        if not folders:
            raise BiliRequestError("账号下没有可用收藏夹")
        for f in folders:
            if str(f.get("title") or "") == "默认收藏夹":
                return int(f["id"])
        return int(folders[0]["id"])

    def fav(self, bvid: Optional[str] = None, aid: Optional[int] = None,
            media_ids: Optional[List[int]] = None, remove: bool = False,
            allow_mine: bool = False) -> Dict[str, Any]:
        """收藏 / 取消收藏（POST，需 csrf；rid 需 avid 故先查一次视频详情）"""
        ids = self._resolve_aid(bvid, aid) if (bvid or aid) else None
        if ids is None:
            ref = self._video_ref(bvid, aid, allow_mine=allow_mine)
            ids = self._resolve_aid(ref.get("bvid"), ref.get("aid"))
        rid = ids.get("aid")
        if not rid:
            raise BiliRequestError("无法取得稿件 avid（收藏接口需要）")
        mids = [int(m) for m in media_ids] if media_ids else [self.default_fav_folder_id()]
        body: Dict[str, Any] = {"rid": rid, "type": 2}
        if remove:
            body["del_media_ids"] = ",".join(str(m) for m in mids)
        else:
            body["add_media_ids"] = ",".join(str(m) for m in mids)
        self._http.post("/x/v3/fav/resource/deal", body,
                        referer=_REFERER_VIDEO.format(bvid=ids.get("bvid") or ""))
        return {"bvid": ids.get("bvid"), "aid": rid, "media_ids": mids,
                "action": "remove" if remove else "add", "success": True}

    # ---------- 写操作结果确认（只读）----------

    def action_status(self, bvid: Optional[str] = None,
                      aid: Optional[int] = None) -> Dict[str, Any]:
        """查询视频的点赞 / 投币状态（用于确认写操作是否生效）"""
        ref = self._video_ref(bvid, aid, allow_mine=True)
        out: Dict[str, Any] = dict(ref)
        try:
            out["liked"] = bool(self._http.get(
                "/x/web-interface/archive/has/like", dict(ref)))
        except BiliRequestError as e:
            out["liked_error"] = str(e)
        try:
            coins = self._http.get(
                "/x/web-interface/archive/coins", dict(ref)) or {}
            out["coin"] = coins.get("multiply", 0)
        except BiliRequestError as e:
            out["coin_error"] = str(e)
        return out


__all__ = ["BiliVideoAPI", "normalize_bvid"]
