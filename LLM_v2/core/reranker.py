"""知识块 rerank：把候选块交给意志脑模型判断「哪几段真正回答了问题」。

为什么需要：向量检索对"泛化综述页"过于友好——一份长文档的开头/总结页
和任何主题都相似度很高，会压过具体章节。rerank 用问题与片段的对应关系
来判断"这段话是否回答了这个问题"，这是相似度检索做不到的。

失败策略：解析失败、超时、模型报错一律回退到原顺序，绝不让 rerank
拖垮回复或丢掉召回结果。
"""

from __future__ import annotations

import asyncio
import configparser
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CANDIDATES = 10
DEFAULT_TOP_K = 3
DEFAULT_TIMEOUT = 3.0
DEFAULT_SNIPPET_CHARS = 400
DEFAULT_LOCAL_MODEL = "models/bge-reranker-v2-m3"
DEFAULT_MAX_LENGTH = 512
MAX_SNIPPET_CHARS = 800

_SYSTEM_PROMPT = (
    "你是检索结果审核员。给定一个问题和若干候选资料片段，"
    "挑出真正回答了问题的片段，按相关度从高到低排序。"
    "片段里只是顺带提到关键词、或与该问题无关的，不要选。"
    "只输出 JSON，不要解释、不要 markdown。"
)

_INT_RE = re.compile(r"\d+")


def build_prompt(query: str, items: Sequence[Dict[str, Any]], top_k: int,
                 snippet_chars: int = DEFAULT_SNIPPET_CHARS) -> str:
    """构造 rerank 用户提示词：编号 + 标题页码 + 片段。"""
    limit = max(80, min(int(snippet_chars), MAX_SNIPPET_CHARS))
    blocks = [f"问题：{query}", "", "候选资料："]
    for i, item in enumerate(items, 1):
        content = " ".join(str(item.get("content") or "").split())
        if len(content) > limit:
            content = content[:limit] + "..."
        page = item.get("page_from") or 0
        title = item.get("title") or "未命名资料"
        ref = f"{title} p.{page}" if page else title
        blocks.append(f"{i}. [{ref}] {content}")
    blocks.append("")
    blocks.append(
        f'输出格式：{{"order": [编号, ...]}}，按相关度降序，最多 {max(1, int(top_k))} 个；'
        "全部都不相关时输出 {\"order\": []}。")
    return "\n".join(blocks)


def parse_order(text: str, candidate_count: int) -> List[int]:
    """从模型输出里解析 1-based 编号，过滤越界与重复。解析失败返回空列表。"""
    if not text:
        return []
    raw = str(text).strip()
    order: List[int] = []
    try:
        data = json.loads(_extract_json(raw))
        values = data.get("order") if isinstance(data, dict) else data
        if isinstance(values, list):
            order = [int(v) for v in values if isinstance(v, (int, float))]
    except Exception:  # noqa: BLE001
        # 兜底：抓第一个 [...] 里的数字
        match = re.search(r"\[([^\]]*)\]", raw)
        if match:
            order = [int(n) for n in _INT_RE.findall(match.group(1))]
    cleaned: List[int] = []
    for idx in order:
        if 1 <= idx <= candidate_count and idx not in cleaned:
            cleaned.append(idx)
    return cleaned


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


class LLMReranker:
    """用大模型对候选块重排；所有失败都回退原顺序。"""

    def __init__(self, llm=None, candidates: int = DEFAULT_CANDIDATES,
                 timeout: float = DEFAULT_TIMEOUT,
                 snippet_chars: int = DEFAULT_SNIPPET_CHARS):
        self._llm = llm
        self.candidates = max(1, int(candidates))
        self.timeout = max(0.1, float(timeout))
        self.snippet_chars = max(80, min(int(snippet_chars), MAX_SNIPPET_CHARS))

    def _get_llm(self):
        if self._llm is None:
            from LLM_v2.core.llm_adapter import get_will_llm
            self._llm = get_will_llm()
        return self._llm

    async def rerank(self, query: str, items: List[Dict[str, Any]],
                     top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
        """返回重排后的前 top_k 个块；任何异常都退回原顺序。"""
        if not items:
            return []
        limit = max(1, int(top_k))
        pool = list(items[:self.candidates])
        if len(pool) <= 1:
            return pool[:limit]

        prompt = build_prompt(query, pool, limit, self.snippet_chars)
        try:
            text = await asyncio.wait_for(
                self._get_llm().chat_simple(
                    _SYSTEM_PROMPT, prompt, temperature=0.0),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.info(f"[rerank] 超时({self.timeout:.1f}s)，保留原顺序")
            return pool[:limit]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rerank] 调用失败，保留原顺序: {e}")
            return pool[:limit]

        order = parse_order(text, len(pool))
        if not order:
            logger.info(f"[rerank] 输出无法解析，保留原顺序: {str(text)[:120]}")
            return pool[:limit]

        ranked = [pool[i - 1] for i in order]
        selected = set(order)
        for i, item in enumerate(pool, 1):
            if len(ranked) >= limit:
                break
            if i not in selected:
                ranked.append(item)
        for item in ranked:
            item["reranked"] = True
        logger.debug(f"[rerank] 候选={len(pool)} 选中={order} → 返回 {len(ranked[:limit])}")
        return ranked[:limit]


class LocalReranker:
    """本地 cross-encoder（如 bge-reranker-v2-m3）：逐对打分，不联网、不走 token 计费。

    模型懒加载（首次 use 时才载入显存）；推理放在线程池，避免阻塞事件循环。
    加载或推理失败一律回退原顺序。
    """

    def __init__(self, model_path: str = DEFAULT_LOCAL_MODEL,
                 candidates: int = DEFAULT_CANDIDATES,
                 device: str = "auto", max_length: int = DEFAULT_MAX_LENGTH,
                 passage_chars: int = 1200, _scorer=None):
        self.model_path = str(model_path)
        self.candidates = max(1, int(candidates))
        self.max_length = max(64, int(max_length))
        self.passage_chars = max(200, int(passage_chars))
        self._device = device or "auto"
        self._model = None
        self._tokenizer = None
        self._scorer = _scorer      # 测试注入：fn(query, passages) -> List[float]
        self._load_failed = False
        self._warm_lock = threading.Lock()
        self._warming = False

    # ---------- 模型加载 ----------

    def _resolve_device(self, torch) -> str:
        if self._device != "auto":
            return self._device
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            import torch
            from transformers import (AutoModelForSequenceClassification,
                                      AutoTokenizer)
            device = self._resolve_device(torch)
            tokenizer = AutoTokenizer.from_pretrained(self.model_path)
            model = AutoModelForSequenceClassification.from_pretrained(
                self.model_path)
            model.eval()
            if device == "cuda":
                model = model.half().to(device)
            else:
                model = model.to(device)
            self._tokenizer, self._model, self._device = tokenizer, model, device
            logger.info(f"[rerank] 本地模型已加载: {self.model_path} ({device})")
            return True
        except Exception as e:  # noqa: BLE001
            self._load_failed = True
            logger.warning(f"[rerank] 本地模型加载失败（回退原顺序）: {e}")
            return False

    def warm(self) -> None:
        """预加载模型与 tokenizer（可选，默认不调用：2GB 模型 + 约 10s）。"""
        self._load()

    def warm_async(self) -> None:
        """后台线程加载模型，不阻塞调用方（首个知识问题先回退原顺序）。"""
        if self._model is not None or self._warming or self._load_failed:
            return
        with self._warm_lock:
            if self._model is not None or self._warming or self._load_failed:
                return
            self._warming = True

        def _run() -> None:
            try:
                self._load()
            finally:
                self._warming = False

        threading.Thread(target=_run, name="rerank-warm", daemon=True).start()
        logger.info(f"[rerank] 后台加载本地模型: {self.model_path}")

    def _ready(self) -> bool:
        return self._scorer is not None or self._model is not None

    def _score_sync(self, query: str, passages: List[str]) -> List[float]:
        if self._scorer is not None:
            return list(self._scorer(query, passages))
        if not self._load():
            return []
        import torch
        inputs = self._tokenizer(
            [query] * len(passages), passages,
            padding=True, truncation=True,
            max_length=self.max_length, return_tensors="pt",
        ).to(self._device)
        with torch.no_grad():
            logits = self._model(**inputs).logits.view(-1).float()
        return torch.sigmoid(logits).cpu().tolist()

    # ---------- 接口 ----------

    async def rerank(self, query: str, items: List[Dict[str, Any]],
                     top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
        if not items:
            return []
        limit = max(1, int(top_k))
        pool = list(items[:self.candidates])
        if len(pool) <= 1:
            return pool[:limit]

        passages = [
            " ".join(str(item.get("content") or "").split())[:self.passage_chars]
            for item in pool
        ]
        if not self._ready():
            # 未预热时不阻塞首个问题：后台加载，本次沿用原顺序
            self.warm_async()
            logger.info("[rerank] 本地模型尚未就绪，本次回退原顺序")
            return pool[:limit]
        try:
            scores = await asyncio.to_thread(self._score_sync, query, passages)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[rerank] 本地打分失败，保留原顺序: {e}")
            return pool[:limit]
        if len(scores) != len(pool):
            return pool[:limit]

        ranked = []
        for score, item in sorted(zip(scores, pool), key=lambda x: -x[0]):
            item["rerank_score"] = round(float(score), 4)
            item["reranked"] = True
            ranked.append(item)
        logger.debug(
            f"[rerank] local 候选={len(pool)} scores="
            f"{[round(float(s), 3) for s in scores]}")
        return ranked[:limit]


def _load_cfg() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    path = PROJECT_ROOT / "config.ini"
    if path.exists():
        try:
            cfg.read(path, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return cfg


def _cfg_str(env_name: str, key: str, default: str) -> str:
    env = (os.getenv(env_name) or "").strip()
    if env:
        return env
    try:
        return _load_cfg().get("rag", key, fallback=default)
    except Exception:  # noqa: BLE001
        return default


def _cfg_int(env_name: str, key: str, default: int) -> int:
    env = (os.getenv(env_name) or "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            return default
    try:
        return _load_cfg().getint("rag", key, fallback=default)
    except Exception:  # noqa: BLE001
        return default


def _cfg_float(env_name: str, key: str, default: float) -> float:
    env = (os.getenv(env_name) or "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            return default
    try:
        return _load_cfg().getfloat("rag", key, fallback=default)
    except Exception:  # noqa: BLE001
        return default


def build_reranker():
    """按配置构造 reranker；返回 None 表示关闭。

    backend: off / local / llm / auto（auto=本地模型存在就用本地，否则用 LLM）
    """
    enabled = (os.getenv("RAG_RERANK_ENABLED") or "").strip()
    if enabled:
        if enabled.lower() != "true":
            return None
    else:
        try:
            if not _load_cfg().getboolean("rag", "rerank_enabled", fallback=True):
                return None
        except Exception:  # noqa: BLE001
            pass

    backend = _cfg_str("RAG_RERANK_BACKEND", "rerank_backend", "auto").lower()
    if backend == "off":
        return None

    candidates = max(1, _cfg_int("RAG_RERANK_CANDIDATES", "rerank_candidates",
                                 DEFAULT_CANDIDATES))
    if backend in ("auto", "local"):
        model_path = _cfg_str("RAG_RERANK_MODEL", "rerank_model_path",
                              DEFAULT_LOCAL_MODEL)
        path = Path(model_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists():
            return LocalReranker(
                model_path=str(path),
                candidates=candidates,
                device=_cfg_str("RAG_RERANK_DEVICE", "rerank_device", "auto"),
                max_length=_cfg_int("RAG_RERANK_MAX_LENGTH",
                                    "rerank_max_length", DEFAULT_MAX_LENGTH),
            )
        logger.info(f"[rerank] 本地模型不存在({path})，{'改用 LLM' if backend == 'auto' else '关闭 rerank'}")
        if backend == "local":
            return None

    return LLMReranker(
        candidates=candidates,
        timeout=_cfg_float("RAG_RERANK_TIMEOUT", "rerank_timeout", DEFAULT_TIMEOUT),
        snippet_chars=_cfg_int("RAG_RERANK_SNIPPET_CHARS", "rerank_snippet_chars",
                               DEFAULT_SNIPPET_CHARS),
    )


__all__ = ['LLMReranker', 'LocalReranker', 'build_reranker',
           'build_prompt', 'parse_order']

