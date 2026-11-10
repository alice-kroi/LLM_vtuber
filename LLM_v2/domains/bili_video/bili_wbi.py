# -*- coding: utf-8 -*-
# ============================================================
# LLM_v2/domains/bili_video/bili_wbi.py — B 站 WBI 签名
# ============================================================
# 原理（2023-03 起 B 站 Web 端风控，作用于用户投稿/首页推送/视频信息/取流/搜索等）：
#   1. nav 接口取 wbi_img.img_url / sub_url → 取 url 末尾无扩展名文件名
#      = img_key / sub_key（每日变化，与登录态/IP 无关，需按日缓存）
#   2. img_key + sub_key 按 64 位置换表重排、取前 32 位 = mixin_key
#   3. 参数加 wts（秒级时间戳）→ 按 key 升序 → urlencode（过滤 value 中 !'()* ）
#   4. 末尾拼 mixin_key → md5（32 位小写）= w_rid
# 缺失 w_rid/wts 会在若干次请求后返回 -403 非法访问。
# ============================================================

from __future__ import annotations

import hashlib
import time
import urllib.parse
from typing import Any, Dict, Optional

# 64 位字符重排映射表
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

_FILTER_CHARS = "!'()*"


def get_mixin_key(orig: str) -> str:
    """对 img_key + sub_key 做字符顺序重排，取前 32 位作为 mixin_key"""
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _clean(value: Any) -> str:
    """过滤 value 中的 !'()* 字符（B 站签名要求）"""
    return "".join(ch for ch in str(value) if ch not in _FILTER_CHARS)


def enc_wbi(params: Dict[str, Any], img_key: str, sub_key: str,
            wts: Optional[int] = None) -> Dict[str, Any]:
    """为请求参数做 WBI 签名，返回带 wts / w_rid 的新参数字典"""
    mixin_key = get_mixin_key(img_key + sub_key)
    signed = {k: _clean(v) for k, v in params.items()}
    signed["wts"] = int(wts if wts is not None else time.time())
    items = sorted(signed.items())
    # urlencode 默认使用 quote_plus，百分号后为大写字母（B 站要求）
    query = urllib.parse.urlencode(items)
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return signed


__all__ = ["MIXIN_KEY_ENC_TAB", "get_mixin_key", "enc_wbi"]
