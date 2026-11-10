# ============================================================
# LLM_v2/core/central_context.py — 中央信息层
# ============================================================
# 两层信息架构：
#   中央信息（本模块）：基础信息 + 各能力域摘要 + 指向域内详情的渠道 + 近期聊天
#   域内信息（各能力域自己）：完整局面/细节，只通过该域工具读取
#
# 中央层只保存"摘要与指针"，不复制域内全量数据，避免全局缓存越滚越乱。
# ============================================================

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

_MAX_CHAT = 40
_MAX_DOMAINS = 32


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _short(text: Any, limit: int = 50) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


class CentralContext:
    """线程安全的中央信息黑板（进程内单例）"""

    def __init__(self):
        self._lock = threading.RLock()
        self._domains: Dict[str, Dict[str, Any]] = {}
        self._chat: List[Dict[str, Any]] = []
        self._base: Dict[str, Any] = {}
        self._updated_at = ""

    # ---------- 基础信息 ----------

    def update_base(self, **fields: Any) -> None:
        with self._lock:
            self._base.update(fields)
            self._updated_at = _now()

    # ---------- 能力域摘要 ----------

    def update_domain(self, domain_id: str, *,
                      label: str = "",
                      summary: str = "",
                      channels: Optional[List[Dict[str, Any]]] = None,
                      status: Optional[str] = None,
                      tools: Optional[List[str]] = None,
                      progress: Optional[str] = None,
                      cache: Optional[Dict[str, Any]] = None) -> None:
        """写入/合并一个域的中央卡片（只存摘要+渠道，不存域内全量数据）"""
        if not domain_id:
            return
        with self._lock:
            card = self._domains.get(domain_id) or {
                "id": domain_id, "label": domain_id, "summary": "",
                "channels": [], "status": "unknown", "tools": [],
                "progress": "", "cache": {}, "updated_at": "",
            }
            if label:
                card["label"] = label
            if summary:
                card["summary"] = str(summary)[:400]
            if channels is not None:
                card["channels"] = channels
            if status is not None:
                card["status"] = status
            if tools is not None:
                card["tools"] = list(tools)
            if progress is not None:
                card["progress"] = str(progress)[:200]
            if cache is not None:
                card["cache"] = dict(cache)
            card["updated_at"] = _now()
            self._domains[domain_id] = card
            self._updated_at = _now()

    def set_status(self, domain_id: str, status: str) -> None:
        self.update_domain(domain_id, status=status)

    def set_progress(self, domain_id: str, progress: str) -> None:
        self.update_domain(domain_id, progress=progress)

    def remove_domain(self, domain_id: str) -> None:
        with self._lock:
            self._domains.pop(domain_id, None)
            self._updated_at = _now()

    def domain(self, domain_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            card = self._domains.get(domain_id)
            return dict(card) if card else None

    # ---------- 聊天 ----------

    def record_chat(self, role: str, text: str, source: str = "",
                    user: str = "") -> None:
        text = str(text or "").strip()
        if not text:
            return
        with self._lock:
            self._chat.append({
                "role": role or "user",
                "text": text[:300],
                "source": source or "",
                "user": user or "",
                "at": _now(),
            })
            self._chat = self._chat[-_MAX_CHAT:]
            self._updated_at = _now()

    def recent_chat(self, limit: int = 8) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._chat[-max(1, min(_MAX_CHAT, int(limit or 8))):])

    # ---------- 快照 / 文本 ----------

    def _ordered_domains(self, domain_limit: int,
                         focus: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        values = list(self._domains.values())
        focus = [str(f) for f in (focus or []) if f]
        if focus:
            first = [v for v in values if v.get("id") in focus]
            rest = [v for v in values if v.get("id") not in focus]
            values = first + rest
        return values[:max(1, int(domain_limit or _MAX_DOMAINS))]

    def snapshot(self, chat_limit: int = 8, domain_limit: int = _MAX_DOMAINS,
                 focus: Optional[List[str]] = None) -> Dict[str, Any]:
        with self._lock:
            domains = self._ordered_domains(domain_limit, focus)
            chat = self._chat[-max(1, int(chat_limit or 8)):]
            return {
                "success": True,
                "updated_at": self._updated_at,
                "base": dict(self._base),
                "chat": chat,
                "domains": domains,
                "counts": {
                    "domains": len(self._domains),
                    "chat": len(self._chat),
                },
            }

    @staticmethod
    def _is_rich(card: Dict[str, Any]) -> bool:
        """有局部状态（缓存/进行中进展/自定义渠道）的域才给完整卡片"""
        cache = card.get("cache") or {}
        progress = str(card.get("progress") or "").strip()
        if progress in ("已停用", "已关闭"):
            progress = ""
        return bool(cache.get("has_cache") or progress or card.get("channels"))

    def compact_snapshot(self, chat_limit: int = 6,
                         domain_limit: int = _MAX_DOMAINS,
                         focus: Optional[List[str]] = None) -> Dict[str, Any]:
        """紧凑视图：少量完整卡片 + 其余域的极简索引（不含工具清单）"""
        snap = self.snapshot(chat_limit=chat_limit, domain_limit=domain_limit,
                             focus=focus)
        cards: List[Dict[str, Any]] = []
        index: List[Dict[str, Any]] = []
        for card in snap.get("domains") or []:
            if self._is_rich(card):
                cards.append(card)
            else:
                index.append({
                    "id": card.get("id"),
                    "label": card.get("label"),
                    "status": card.get("status"),
                    "summary": _short(card.get("summary"), 60),
                    "has_cache": bool((card.get("cache") or {}).get("has_cache")),
                })
        return {
            "success": True,
            "updated_at": snap.get("updated_at"),
            "base": snap.get("base") or {},
            "chat": snap.get("chat") or [],
            "cards": cards,
            "index": index,
            "counts": snap.get("counts") or {},
        }

    @staticmethod
    def _describe_channels(card: Dict[str, Any]) -> str:
        parts: List[str] = []
        for ch in card.get("channels") or []:
            tools = "、".join(ch.get("tools") or [])
            desc = ch.get("description") or ""
            if tools:
                parts.append(f"{ch.get('name') or '详情'}：{tools}"
                             + (f"（{desc}）" if desc else ""))
        if not parts and card.get("tools"):
            parts.append("本域工具：" + "、".join(card["tools"][:8]))
        return "；".join(parts)

    @staticmethod
    def _describe_cache(card: Dict[str, Any]) -> str:
        """把域内缓存的信号压成一句人类可读说明"""
        cache = card.get("cache") or {}
        if not cache.get("has_cache"):
            return ""
        if cache.get("available"):
            text = "可用"
            if cache.get("has_data"):
                text += f"，已有 {cache.get('entries', 0)} 条数据"
            else:
                text += "，暂无数据"
        else:
            text = "已关闭（内存已清空"
            if cache.get("persisted"):
                text += "；磁盘有历史快照，重新启用可恢复"
            text += "）"
        return text

    def overview_text(self, chat_limit: int = 6,
                      domain_limit: int = _MAX_DOMAINS,
                      focus: Optional[List[str]] = None) -> str:
        """给 LLM 的紧凑中央信息：完整卡片 + 极简索引 + 聊天"""
        snap = self.compact_snapshot(chat_limit=chat_limit,
                                     domain_limit=domain_limit, focus=focus)
        lines: List[str] = ["【中央信息】"]

        base = snap.get("base") or {}
        if base:
            bits = [f"{k}={v}" for k, v in base.items()]
            lines.append("基础：" + "，".join(bits))

        chat = snap.get("chat") or []
        if chat:
            lines.append("最近对话：")
            for row in chat:
                is_user = row.get("role") == "user"
                who = (row.get("user") or row.get("source") or "unknown") if is_user \
                    else (row.get("source") or "我")
                role = "观众" if is_user else "我"
                lines.append(f"- {role}({who})：{row.get('text')}")

        cards = snap.get("cards") or []
        if cards:
            lines.append("局部信息卡片：")
            for card in cards:
                head = f"- {card.get('label')}（{card.get('id')}，{card.get('status')}）"
                if card.get("summary"):
                    head += f"：{card.get('summary')}"
                if card.get("progress"):
                    head += f"｜进展：{card.get('progress')}"
                channel = self._describe_channels(card)
                if channel:
                    head += f"｜详情渠道：{channel}"
                cache_text = self._describe_cache(card)
                if cache_text:
                    head += f"｜缓存：{cache_text}"
                lines.append(head)

        index = snap.get("index") or []
        if index:
            lines.append("其他域索引（用 ci_domain 查看工具与详情）：")
            for item in index:
                lines.append(f"- {item.get('id')}：{item.get('summary') or '可用'}")
        return "\n".join(lines)

    # ---------- 调试 ----------

    def clear(self) -> None:
        with self._lock:
            self._domains.clear()
            self._chat.clear()
            self._base.clear()
            self._updated_at = ""


_CENTRAL: Optional[CentralContext] = None
_CENTRAL_LOCK = threading.Lock()


def get_central() -> CentralContext:
    global _CENTRAL
    with _CENTRAL_LOCK:
        if _CENTRAL is None:
            _CENTRAL = CentralContext()
        return _CENTRAL


__all__ = ["CentralContext", "get_central"]
