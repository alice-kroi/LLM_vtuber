# ============================================================
# LLM_v2/domains/hf_hub/hf_hub.py — HuggingFace 模型检索能力域
# ============================================================
# Actuator：只读外部 API，不订阅事件。
# 风险等级 R2（联网）：默认不自动准入，需人工确认后才挂载。
#
# 端点选择：huggingface.co 在部分网络下不可达，默认走 hf-mirror.com；
# 可用环境变量 HF_ENDPOINT 覆盖（例如设回 https://huggingface.co）。
# ============================================================

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.domains.hf_hub.hf_hub_tools import HF_TOOL_NAMES, HF_TOOLS_SCHEMA

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://hf-mirror.com"
TOOL_TIMEOUTS = {
    "hf_search_models": 30.0,
    "hf_model_info": 30.0,
}


def _endpoint() -> str:
    return (os.getenv("HF_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")


def _get_json(path: str, timeout: float = 20.0) -> Any:
    url = f"{_endpoint()}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "llm-vtuber-hf_hub/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class HfHubDomain(Actuator):
    """HuggingFace 模型检索（R2 联网，只读）"""

    id = "hf_hub"
    label = "HuggingFace 模型检索"
    prefix = "hf_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 30.0
    tool_timeouts = dict(TOOL_TIMEOUTS)

    def allowed_names(self) -> List[str]:
        return list(HF_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(HF_TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return (
            "可用工具（HuggingFace 模型检索，R2 联网只读）：\n"
            "- hf_search_models: 按关键词找模型（query 必填）\n"
            "- hf_model_info: 查单个模型详情（model_id 必填）\n"
            "执行原则：\n"
            "1. 只做读取，不下载权重、不写本地文件。\n"
            "2. 用户问'有没有现成的模型'时优先用 hf_search_models，再按需查详情。\n"
            f"3. 当前端点：{_endpoint()}（可用 HF_ENDPOINT 切换）。\n"
        )

    # ---------- 工具 ----------

    def hf_search_models(self, query: str, limit: int = 5,
                         sort: str = "downloads") -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "query 不能为空"}
        try:
            limit = max(1, min(int(limit), 20))
        except (TypeError, ValueError):
            limit = 5
        params = urllib.parse.urlencode({
            "search": query,
            "limit": limit,
            "sort": sort,
            "direction": -1,
        })
        try:
            data = _get_json(f"/api/models?{params}")
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

        if not isinstance(data, list):
            return {"success": False, "error": "返回结构不是列表"}

        models = [{
            "model_id": m.get("modelId") or m.get("id"),
            "downloads": m.get("downloads"),
            "likes": m.get("likes"),
            "task": m.get("pipeline_tag"),
            "tags": (m.get("tags") or [])[:8],
        } for m in data]
        return {"success": True, "count": len(models), "models": models,
                "endpoint": _endpoint()}

    def hf_model_info(self, model_id: str) -> Dict[str, Any]:
        model_id = (model_id or "").strip()
        if not model_id:
            return {"success": False, "error": "model_id 不能为空"}
        try:
            data = _get_json(f"/api/models/{urllib.parse.quote(model_id)}")
        except urllib.error.HTTPError as e:
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

        if not isinstance(data, dict):
            return {"success": False, "error": "返回结构不是对象"}

        siblings = data.get("siblings") or []
        return {
            "success": True,
            "model_id": data.get("modelId") or data.get("id"),
            "author": data.get("author"),
            "task": data.get("pipeline_tag"),
            "downloads": data.get("downloads"),
            "likes": data.get("likes"),
            "tags": (data.get("tags") or [])[:15],
            "files": [s.get("rfilename") for s in siblings[:10]],
            "endpoint": _endpoint(),
        }

    def preflight(self) -> Optional[str]:
        try:
            urllib.request  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return f"urllib 不可用: {e}"
        return None


def build_hf_hub_domain() -> HfHubDomain:
    return HfHubDomain()


__all__ = ["HfHubDomain", "build_hf_hub_domain", "DEFAULT_ENDPOINT", "TOOL_TIMEOUTS"]
