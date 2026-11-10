# -*- coding: utf-8 -*-
# ============================================================
# LLM_v2/domains/bili_video/bili_video_http.py — B 站 HTTP 请求层
# ============================================================
# 职责：Cookie 组装 / UA / Referer / 节流 / 退避重试 / WBI key 缓存与刷新 /
#       CSRF Token（bili_jct）自动获取（写操作必需）。
# 说明：本能力仅限个人自用，不得作为公开服务对外提供；必须保持限流。
# ============================================================

from __future__ import annotations

import html
import json
import logging
import os
import random
import time
from typing import Any, Dict, Optional, Tuple

import requests

from LLM_v2.domains.bili_video.bili_wbi import enc_wbi

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

API_BASE = "https://api.bilibili.com"
NAV_URL = API_BASE + "/x/web-interface/nav"
CSRF_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
DEFAULT_REFERER = "https://www.bilibili.com/"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_CODE_WBI_INVALID = -403       # wbi 失效 → 刷新 key 后重试
_CODE_COOLDOWN = (-412, -509)  # 风控冷却
_CODE_NOT_LOGIN = (-101,)      # 未登录 / 登录失效（注意 -111 是 CSRF，不在此列）
_CODE_CSRF = -111              # CSRF 校验失败 → 刷新 bili_jct 后重试

# 写操作已知业务错误码 → 可读原因（提升可诊断性）
_CODE_MESSAGES = {
    -102: "账号被封停",
    -104: "硬币不足",
    10003: "不存在该稿件",
    34002: "不能给自己投稿的视频投币",
    34003: "非法的投币数量",
    34004: "投币间隔太短",
    34005: "超过投币上限",
    65004: "取消点赞失败",
    65006: "重复点赞",
}

_MAX_ERRORS_KEPT = 5


def describe_code_error(code: Optional[int], message: str) -> str:
    """把 B 站错误码翻译成「原始信息（可读原因）」"""
    reason = _CODE_MESSAGES.get(code)
    text = message or ""
    if reason:
        return f"{text}（{reason}）" if text and text != "0" else reason
    return text or f"code={code}"


class BiliRequestError(RuntimeError):
    """B 站请求失败（硬失败，会被引擎转成 {"success": false, "error": ...}）"""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def resolve_sessdata(env_name: str = "BILIBILI_SESSDATA") -> str:
    """多来源解析 SESSDATA：broadcast/config.json > 环境变量 > config.ini（与 V1 一致）

    返回空字符串表示未配置；调用方据此给出明确失败原因。
    """
    # 1) broadcast/config.json（与旧 bili_main 一致，兼容现有 cookie 配置）
    try:
        with open(os.path.join(_PROJECT_ROOT, "broadcast", "config.json"),
                  encoding="utf-8-sig") as f:
            cfg = json.load(f)
        v = str(cfg.get("SESSDATA", "") or "").strip()
        if v:
            return v
    except Exception:
        pass
    # 2) 环境变量（含 V1 兼容命名；env_name 可被域配置指定）
    for var in (env_name, "bilibli_SESSDATA", "BILIBILI_SESSDATA", "SESSDATA"):
        v = (os.environ.get(var) or "").strip()
        if v:
            return v
    # 3) config.ini [bilibili] sessdata
    try:
        import configparser
        ini = configparser.ConfigParser()
        ini.read(os.path.join(_PROJECT_ROOT, "config.ini"), encoding="utf-8")
        if ini.has_section("bilibili"):
            return ini.get("bilibili", "sessdata", fallback="").strip()
    except Exception:
        pass
    return ""


class BiliHTTP:
    """同步请求封装（子进程内单线程使用）"""

    def __init__(self, sessdata: str, jct: Optional[str] = None,
                 buvid3: Optional[str] = None,
                 min_interval_ms: int = 1200, max_retries: int = 2):
        if not sessdata:
            raise BiliRequestError("缺少 SESSDATA（请设置 BILIBILI_SESSDATA 环境变量）")
        self._cookies: Dict[str, str] = {"SESSDATA": sessdata}
        if jct:
            self._cookies["bili_jct"] = jct          # 显式提供则优先，否则自动获取
        if buvid3:
            self._cookies["buvid3"] = buvid3
        self._min_interval = max(0, int(min_interval_ms)) / 1000.0
        self._max_retries = max(1, int(max_retries))
        self._last_ts = 0.0
        self._wbi: Optional[Tuple[str, str]] = None
        self._wbi_day: Optional[str] = None
        self._recent_errors: list = []
        self._s = requests.Session()

    # ---------- 内部工具 ----------

    def _headers(self, referer: Optional[str]) -> Dict[str, str]:
        return {
            "User-Agent": _UA,
            "Referer": referer or DEFAULT_REFERER,
            "Accept": "application/json, text/plain, */*",
            "Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items()),
        }

    def _throttle(self) -> None:
        """简单节流：两次请求至少间隔 min_interval_ms（+ 抖动）"""
        if self._min_interval <= 0:
            return
        wait = self._min_interval - (time.monotonic() - self._last_ts)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.15))
        self._last_ts = time.monotonic()

    def _record_error(self, msg: str) -> None:
        self._recent_errors.append(f"{time.strftime('%H:%M:%S')} {msg}")
        if len(self._recent_errors) > _MAX_ERRORS_KEPT:
            self._recent_errors = self._recent_errors[-_MAX_ERRORS_KEPT:]

    def recent_errors(self) -> list:
        return list(self._recent_errors)

    def wbi_age_seconds(self) -> Optional[int]:
        if not self._wbi_day:
            return None
        try:
            day_start = time.mktime(time.strptime(self._wbi_day, "%Y-%m-%d"))
        except Exception:
            return None
        return int(time.time() - day_start)

    def has_csrf(self) -> bool:
        return bool(self._cookies.get("bili_jct"))

    # ---------- 凭据：buvid3 / WBI key / CSRF ----------

    def _ensure_buvid3(self) -> None:
        """获取设备指纹 buvid3/buvid4（B 站部分接口风控必需），只取一次"""
        if self._cookies.get("buvid3"):
            return
        try:
            resp = self._s.get(API_BASE + "/x/frontend/finger/spi",
                               headers=self._headers(DEFAULT_REFERER), timeout=10)
            data = (resp.json() or {}).get("data") or {}
            for key, cookie in (("b_3", "buvid3"), ("b_4", "buvid4")):
                val = data.get(key)
                if val:
                    self._cookies[cookie] = val
            logger.debug("[bili_video] buvid3 已获取")
        except Exception as e:  # noqa: BLE001
            logger.debug("[bili_video] buvid3 获取失败（继续请求）: %s", e)

    def _ensure_csrf(self, force: bool = False) -> str:
        """CSRF Token（bili_jct）：有效 SESSDATA 下由 cookie/info 接口下发

        写操作（点赞/投币/收藏）必填；失效时再取一次。
        """
        if not force and self._cookies.get("bili_jct"):
            return self._cookies["bili_jct"]
        try:
            resp = self._s.get(CSRF_URL, headers=self._headers(DEFAULT_REFERER), timeout=10)
            for c in resp.cookies:
                if c.name in ("bili_jct", "DedeUserID"):
                    self._cookies[c.name] = c.value
        except Exception as e:  # noqa: BLE001
            logger.debug("[bili_video] 获取 bili_jct 异常: %s", e)
        jct = self._cookies.get("bili_jct") or ""
        if not jct:
            raise BiliRequestError(
                "无法获取 CSRF Token（bili_jct）：请确认 SESSDATA 为有效登录态")
        return jct

    def _ensure_wbi(self, force: bool = False) -> Tuple[str, str]:
        """取 img_key / sub_key（按自然日缓存；force=True 时强制刷新）"""
        day = time.strftime("%Y-%m-%d")
        if not force and self._wbi and self._wbi_day == day:
            return self._wbi
        self._throttle()
        try:
            resp = self._s.get(NAV_URL, headers=self._headers(DEFAULT_REFERER), timeout=10)
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise BiliRequestError(f"获取 WBI key 失败（nav 请求异常）: {e}")
        wbi_img = (data.get("data") or {}).get("wbi_img") or {}
        img_url = wbi_img.get("img_url") or ""
        sub_url = wbi_img.get("sub_url") or ""
        if not img_url or not sub_url:
            raise BiliRequestError(
                f"nav 未返回 wbi_img，可能未登录（code={data.get('code')}）"
            )
        img_key = os.path.splitext(os.path.basename(img_url))[0]
        sub_key = os.path.splitext(os.path.basename(sub_url))[0]
        self._wbi = (img_key, sub_key)
        self._wbi_day = day
        logger.debug("[bili_video] WBI key 已刷新: img=%s sub=%s", img_key[:8], sub_key[:8])
        return self._wbi

    # ---------- 统一错误处理（get/post 共用）----------

    def _handle_code_error(self, code: int, message: str, attempt: int,
                           kind: str) -> bool:
        """处理 code != 0。返回 True 表示"已安排重试，调用方应 continue"；否则抛错。"""
        if kind == "get" and code == _CODE_WBI_INVALID and attempt < self._max_retries:
            self._wbi = None      # 强制刷新 WBI key 后重试
            self._record_error(f"wbi 失效(-403)，已刷新 key 重试: {message}")
            return True
        if kind == "post" and code == _CODE_CSRF and attempt < self._max_retries:
            self._cookies.pop("bili_jct", None)   # 刷新 CSRF 后重试
            self._ensure_csrf(force=True)
            self._record_error(f"csrf 校验失败(-111)，已刷新重试: {message}")
            return True
        if code in _CODE_COOLDOWN:
            self._record_error(f"风控 code={code}: {message}")
            if attempt < self._max_retries:
                time.sleep(3.0)      # 冷却后重试一次
                return True
            raise BiliRequestError(
                f"触发 B 站风控（code={code}），请降低频率后重试", code)
        if code in _CODE_NOT_LOGIN:
            self._record_error(f"登录失效 code={code}: {message}")
            raise BiliRequestError(
                f"未登录或登录已失效，请更新 SESSDATA（code={code}）", code)
        self._record_error(f"code={code}: {message}")
        raise BiliRequestError(
            f"接口失败 code={code}: {describe_code_error(code, message)}", code)

    # ---------- 请求 ----------

    def get(self, path: str, params: Optional[Dict[str, Any]] = None,
            referer: Optional[str] = None, wbi: bool = False) -> Any:
        """GET 一个 api.bilibili.com 接口，返回 data 字段；失败抛 BiliRequestError"""
        url = path if path.startswith("http") else API_BASE + path
        base_params = dict(params or {})
        last_err: Optional[str] = None
        self._ensure_buvid3()

        for attempt in range(1, self._max_retries + 1):
            query = dict(base_params)
            if wbi:
                img_key, sub_key = self._ensure_wbi(force=(attempt > 1))
                query = enc_wbi(query, img_key, sub_key)
            self._throttle()
            try:
                resp = self._s.get(url, params=query,
                                   headers=self._headers(referer), timeout=12)
            except Exception as e:  # noqa: BLE001
                last_err = f"网络异常: {e}"
                self._record_error(last_err)
                if attempt < self._max_retries:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                raise BiliRequestError(last_err)

            try:
                data = resp.json()
            except Exception:
                raise BiliRequestError(f"返回非 JSON（HTTP {resp.status_code}）")

            code = data.get("code")
            if code == 0:
                return data.get("data")
            if self._handle_code_error(code, data.get("message") or "", attempt, "get"):
                continue
        raise BiliRequestError(last_err or "请求失败")

    def post(self, path: str, data: Optional[Dict[str, Any]] = None,
             referer: Optional[str] = None, wbi: bool = False,
             need_csrf: bool = True) -> Any:
        """POST 一个 api.bilibili.com 接口（表单），返回 data 字段。

        写操作需要 csrf（自动取自 bili_jct）；失效时刷新后重试一次。
        """
        url = path if path.startswith("http") else API_BASE + path
        base_data = dict(data or {})
        last_err: Optional[str] = None
        self._ensure_buvid3()

        for attempt in range(1, self._max_retries + 1):
            body = dict(base_data)
            if need_csrf:
                body["csrf"] = self._ensure_csrf(force=(attempt > 1))
            if wbi:
                img_key, sub_key = self._ensure_wbi(force=(attempt > 1))
                body = enc_wbi(body, img_key, sub_key)
            self._throttle()
            try:
                resp = self._s.post(url, data=body,
                                    headers=self._headers(referer), timeout=12)
            except Exception as e:  # noqa: BLE001
                last_err = f"网络异常: {e}"
                self._record_error(last_err)
                if attempt < self._max_retries:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                raise BiliRequestError(last_err)

            try:
                payload = resp.json()
            except Exception:
                raise BiliRequestError(f"返回非 JSON（HTTP {resp.status_code}）")

            code = payload.get("code")
            if code == 0:
                return payload.get("data")
            if self._handle_code_error(code, payload.get("message") or "", attempt, "post"):
                continue
        raise BiliRequestError(last_err or "请求失败")

    def get_raw_json(self, url: str, referer: Optional[str] = None) -> Any:
        """GET 一个绝对 URL 并返回原始 JSON（用于字幕文件等，不做 wbi 签名）"""
        self._throttle()
        resp = self._s.get(url, headers=self._headers(referer), timeout=15)
        try:
            return resp.json()
        except Exception:
            raise BiliRequestError(f"字幕文件非 JSON（HTTP {resp.status_code}）")


def unescape_text(text: str) -> str:
    """字幕/评论里常见的 HTML 实体反转义"""
    return html.unescape(text or "")


__all__ = ["BiliHTTP", "BiliRequestError", "resolve_sessdata", "unescape_text",
           "describe_code_error", "API_BASE", "NAV_URL", "CSRF_URL"]
