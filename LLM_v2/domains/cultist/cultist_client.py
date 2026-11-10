# ============================================================
# LLM_v2/domains/cultist/cultist_client.py — 密教模拟器 REST 客户端
# ============================================================
# 依赖游戏内 CSRest mod（本地 mods/CSRest，监听 127.0.0.1:8765）。
# 只做 HTTP 读写 + 状态归一化，不含任何 LLM 逻辑。
# ============================================================

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:8765"

_READ_TIMEOUT = 8.0
_ACTION_TIMEOUT = 45.0
_START_TIMEOUT = 120.0


class CultistAPIError(RuntimeError):
    """游戏接口不可用 / 返回错误"""


def _strip_html(text: str, limit: int = 300) -> str:
    text = re.sub(r"<[^>]+>", "", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class CultistClient:
    """极简 REST 客户端（stdlib urllib，同步；由域线程池承载）"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    # ---------- HTTP ----------

    def _request(self, method: str, path: str, payload: Any = None,
                 timeout: float = _READ_TIMEOUT, attempts: int = 2) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last_error: Optional[Exception] = None
        for attempt in range(max(1, attempts)):
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:400]
                except Exception:  # noqa: BLE001
                    pass
                raise CultistAPIError(f"HTTP {e.code}: {detail or e.reason}") from e
            except urllib.error.URLError as e:
                if isinstance(e.reason, (ConnectionResetError, ConnectionAbortedError,
                                         BrokenPipeError)):
                    last_error = e
                    if attempt + 1 < attempts:
                        time.sleep(0.3)
                        continue
                    raise CultistAPIError("游戏接口连接被重置（10054），请重试") from e
                raise CultistAPIError(
                    "游戏未运行或 CSRest 未监听 8765（请先启动密教模拟器）"
                ) from e
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
                last_error = e
                if attempt + 1 < attempts:
                    time.sleep(0.3)
                    continue
                raise CultistAPIError("游戏接口连接被重置（10054），请重试") from e
            except TimeoutError as e:
                last_error = e
                if attempt + 1 < attempts:
                    continue
                raise CultistAPIError(f"游戏接口超时（{timeout}s）") from e
        else:
            raise CultistAPIError(f"游戏接口不可用: {last_error}")
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @staticmethod
    def _as_list(node: Any) -> List[Dict[str, Any]]:
        """接口有时返回裸数组，有时包 {value:[...], Count:n}"""
        if isinstance(node, list):
            return [x for x in node if isinstance(x, dict)]
        if isinstance(node, dict):
            for key in ("value", "Value", "result", "Result"):
                inner = node.get(key)
                if isinstance(inner, list):
                    return [x for x in inner if isinstance(x, dict)]
        return []

    # ---------- 基础读取 ----------

    def legacy(self) -> Dict[str, Any]:
        node = self._request("GET", "api/game-state/legacy", timeout=5.0)
        return node if isinstance(node, dict) else {}

    def tokens(self, sphere_path: str) -> List[Dict[str, Any]]:
        node = self._request("GET", f"api/by-path/{sphere_path}/tokens")
        return self._as_list(node)

    def spheres(self, token_path: str) -> List[Dict[str, Any]]:
        node = self._request("GET", f"api/by-path/{token_path}/spheres")
        return self._as_list(node)

    def token(self, token_path: str) -> Dict[str, Any]:
        node = self._request("GET", f"api/by-path/{token_path}")
        return node if isinstance(node, dict) else {}

    # ---------- 状态归一化 ----------

    def status(self) -> Dict[str, Any]:
        legacy = self.legacy()
        out: Dict[str, Any] = {
            "success": True,
            "legacy": legacy.get("legacyId"),
            "legacy_label": legacy.get("legacyLabel"),
            "cards": [],
            "verbs": [],
            "next_event": None,
        }
        if not legacy.get("legacyId"):
            out["hint"] = "当前不在牌桌上：请调用 cs_start_game 开始一局，或从菜单继续游戏。"
            return out

        cards: List[Dict[str, Any]] = []
        verbs: List[Dict[str, Any]] = []
        for t in self.tokens("~/tabletop"):
            ptype = t.get("payloadType")
            if ptype == "ElementStack":
                cards.append(self._card(t))
            elif ptype == "Situation":
                verb = self._verb(t)
                verb["slots"], verb["outputs"] = self._verb_spheres(t.get("path", ""))
                verbs.append(verb)

        out["cards"] = cards
        out["verbs"] = verbs
        try:
            out["next_event"] = self._request("GET", "api/time/events", timeout=4.0)
        except CultistAPIError:
            out["next_event"] = None
        return out

    @staticmethod
    def _card(token: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": token.get("id"),
            "label": token.get("label"),
            "element_id": token.get("elementId"),
            "quantity": token.get("quantity", 1),
            "aspects": token.get("elementAspects") or {},
            "description": _strip_html(token.get("description")),
            "path": token.get("path"),
            "decays": bool(token.get("decays")),
            "lifetime": token.get("lifetimeRemaining"),
        }

    @staticmethod
    def _verb(token: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": token.get("id"),
            "verb_id": token.get("verbId"),
            "label": token.get("label"),
            "state": token.get("state"),
            "time_remaining": token.get("timeRemaining"),
            "recipe": token.get("recipeLabel"),
            "description": _strip_html(token.get("description"), 400),
            "path": token.get("path"),
        }

    def _verb_spheres(self, token_path: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        slots: List[Dict[str, Any]] = []
        outputs: List[Dict[str, Any]] = []
        if not token_path:
            return slots, outputs
        try:
            spheres = self.spheres(token_path)
        except CultistAPIError:
            return slots, outputs
        for sphere in spheres:
            category = sphere.get("category")
            path = sphere.get("path") or ""
            if category not in ("Threshold", "Output"):
                continue
            try:
                toks = self.tokens(path)
            except CultistAPIError:
                toks = []
            cards = [self._card(t) for t in toks
                     if t.get("payloadType") == "ElementStack"]
            entry = {
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "cards": cards,
            }
            if category == "Threshold":
                slots.append(entry)
            else:
                outputs.append(entry)
        return slots, outputs

    # ---------- 动作 ----------

    def start_game(self, legacy_id: str = "aspirant") -> Dict[str, Any]:
        self._request("PUT", "api/game-state/legacy",
                      {"legacyId": legacy_id}, timeout=_START_TIMEOUT)
        return {"success": True, "legacy": legacy_id}

    def move(self, token_path: str, sphere_path: str) -> Dict[str, Any]:
        node = self._request("PATCH", f"api/by-path/{token_path}",
                             {"spherePath": sphere_path}, timeout=_ACTION_TIMEOUT)
        return node if isinstance(node, dict) else {"success": True}

    def execute(self, verb_path: str) -> Dict[str, Any]:
        node = self._request("POST", f"api/by-path/{verb_path}/execute",
                             {}, timeout=_ACTION_TIMEOUT)
        return node if isinstance(node, dict) else {"success": True}

    def conclude(self, verb_path: str) -> List[Dict[str, Any]]:
        node = self._request("POST", f"api/by-path/{verb_path}/conclude",
                             {}, timeout=_ACTION_TIMEOUT)
        if isinstance(node, dict) and node.get("id"):
            return [node]
        return self._as_list(node)

    def beat(self, seconds: float) -> Dict[str, Any]:
        node = self._request("POST", "api/time/beat",
                             {"seconds": float(seconds)}, timeout=_ACTION_TIMEOUT)
        return node if isinstance(node, dict) else {"success": True}


# ---------- 查找 ----------

def find_card(state: Dict[str, Any], ref: str) -> tuple[Optional[Dict[str, Any]], str]:
    """按 id / elementId / label 找桌面或槽内卡片；歧义时返回候选说明。"""
    ref = str(ref or "").strip()
    if not ref:
        return None, "缺少 card 参数"
    candidates: List[Dict[str, Any]] = list(state.get("cards") or [])
    for verb in state.get("verbs") or []:
        for slot in verb.get("slots") or []:
            candidates.extend(slot.get("cards") or [])

    exact = [c for c in candidates if ref in (c.get("id"), c.get("element_id"), c.get("label"))]
    if len(exact) == 1:
        return exact[0], ""
    if not exact:
        # 模糊：包含匹配
        fuzzy = [c for c in candidates if ref in str(c.get("label") or "")]
        if len(fuzzy) == 1:
            return fuzzy[0], ""
        names = "、".join(sorted({str(c.get("label")) for c in candidates}))
        return None, f"找不到卡片「{ref}」。桌面/槽内的卡片有：{names or '无'}"
    if len(exact) > 1:
        ids = "、".join(str(c.get("id")) for c in exact)
        return None, f"卡片「{ref}」有多个匹配：{ids}；请改用 id"
    return None, f"找不到卡片「{ref}」"


# 动词 id → 常见中文叫法（卡片放入后 label 会变成配方名，不能只靠 label 找）
_VERB_ALIASES: Dict[str, tuple] = {
    "work": ("作业", "工作", "打工", "上班"),
    "study": ("研究", "阅读", "读书", "学习"),
    "dream": ("入梦", "做梦", "梦境", "睡觉"),
    "explore": ("探索", "探险"),
    "talk": ("交谈", "谈话", "对话"),
    "timepass": ("消磨时间", "时间流逝", "打发时间"),
    "cult": ("教团", "布道"),
    "rite": ("仪式", "献祭"),
    "rest": ("休息",),
    "paint": ("绘画", "作画"),
    "write": ("写作",),
}


def find_verb(state: Dict[str, Any], ref: str,
              prefer_state: Optional[str] = None) -> tuple[Optional[Dict[str, Any]], str]:
    """按 id / verbId / label / 中文别名 找动词；prefer_state 优先。"""
    ref = str(ref or "").strip()
    if not ref:
        return None, "缺少 verb 参数"
    verbs = list(state.get("verbs") or [])
    matches = [v for v in verbs
               if ref in (v.get("id"), v.get("verb_id"), v.get("label"))]
    if not matches:
        matches = [v for v in verbs
                   if ref in [str(a) for a in _VERB_ALIASES.get(str(v.get("verb_id")), ())]]
    if not matches:
        matches = [v for v in verbs if ref in str(v.get("label") or "")]
    if not matches:
        names = "、".join(f"{v.get('label')}[{v.get('state')}]" for v in verbs) or "无"
        return None, f"找不到动词「{ref}」。桌面上的动词有：{names}"
    if prefer_state:
        preferred = [v for v in matches if v.get("state") == prefer_state]
        if preferred:
            matches = preferred
    if len(matches) == 1:
        return matches[0], ""
    ids = "、".join(f"{v.get('id')}[{v.get('state')}]" for v in matches)
    return None, f"动词「{ref}」有多个匹配：{ids}；请改用 id"


def pick_slot(verb: Dict[str, Any], ref: str = "") -> tuple[Optional[Dict[str, Any]], str]:
    """选择动词槽位：指定名字/路径/序号，或默认第一个空槽。"""
    slots = list(verb.get("slots") or [])
    if not slots:
        return None, f"动词「{verb.get('label')}」当前没有可放入卡片的槽位"

    ref = str(ref or "").strip()
    if ref:
        if ref.isdigit():
            idx = int(ref) - 1
            if 0 <= idx < len(slots):
                return slots[idx], ""
            return None, f"槽位序号 {ref} 超出范围（共 {len(slots)} 个）"
        exact = [s for s in slots if ref == s.get("name")]
        if len(exact) == 1:
            return exact[0], ""
        full = [s for s in slots if ref == s.get("path")]
        if len(full) == 1:
            return full[0], ""
        matched = [s for s in slots if ref in str(s.get("path") or "")]
        if len(matched) == 1:
            return matched[0], ""
        names = "、".join(str(s.get("name")) for s in slots)
        return None, f"找不到槽位「{ref}」；可用槽位：{names}"

    empty = [s for s in slots if not s.get("cards")]
    if empty:
        return empty[0], ""
    return None, f"动词「{verb.get('label')}」的槽位都已占用"


__all__ = [
    "CultistClient",
    "CultistAPIError",
    "find_card",
    "find_verb",
    "pick_slot",
]
