# ============================================================
# LLM_v2/core/llm_adapter.py — LLM 适配器（接入真实豆包）
# ============================================================
# 封装现有 LLM/chat_model.py 的 doubao_chat_node
# 提供 async 接口 + 历史管理 + 重试
# ============================================================

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------- 按用途的模型路由 ----------
# 优先级：环境变量 > config.ini [model] <purpose>_model > 用途默认 > 通用默认
# 注意：火山方舟模型名必须带日期后缀，短名会报 InvalidEndpointOrModel.NotFound。
MODEL_DEFAULTS: Dict[str, str] = {
    # ⚠️ 本账号实测可用（2026-10-20 探测 / 已开通）：
    #   doubao-seed-character-260628       → 0.8s，支持 function calling
    #   doubao-seed-2-0-code-preview-260215 → 2.5s，编码专用，支持 function calling
    #   doubao-seed-2-1-turbo-260628       → 约 20s（深度思考），支持 function calling
    # 其他名字（doubao-seed-1-8-* / doubao-seed-code-preview-* / deepseek-v4-* 等）
    # 在本账号下分别是"端点已关闭 / 未开通 / 模型不存在"，不要写死。
    "general":  "doubao-seed-2-0-code-preview-260215",
    "streamer": "doubao-seed-character-260628",   # 数字人实时对话（低延迟专用）
    "will":     "doubao-seed-character-260628",   # 意志脑（决策/解说，优先低延迟）
    "operator": "doubao-seed-2-0-code-preview-260215",  # 操作脑：编码专用
    "coding":   "doubao-seed-2-0-code-preview-260215",  # 编码循环：编码专用
}

_PURPOSE_ENV = {
    "general":  ("Doubao_MODEL", "DOUBAO_MODEL"),
    "streamer": ("DOUBAO_STREAMER_MODEL",),
    "will":     ("DOUBAO_WILL_MODEL",),
    "operator": ("DOUBAO_OPERATOR_MODEL",),
    "coding":   ("DOUBAO_CODING_MODEL",),
}

# 各用途的默认输出上限（token）。注意：这是"一次能吐多少"，不是上下文窗口。
# 模型窗口参考：seed-2-0 系列 256k，seed-character 系列 128k。
PURPOSE_DEFAULT_TOKENS: Dict[str, int] = {
    "general": 2048,
    "streamer": 1024,   # 主播回复短，低延迟优先
    "will": 1024,
    "operator": 4096,
    "coding": 8192,     # 一次要写完整文件，太小会把工具参数截断
}


def _cfg_int(cfg, section: str, key: str) -> Optional[int]:
    """从 ConfigParser 读整数；缺失/非法返回 None"""
    if cfg is None:
        return None
    try:
        raw = cfg.get(section, key, fallback=None)
    except Exception:  # noqa: BLE001
        return None
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def resolve_model(purpose: str = "general", config_path: str = "config.ini") -> Optional[str]:
    """解析某用途应使用的模型名（未配置时返回 None，由调用方回落到通用）。"""
    for env_key in _PURPOSE_ENV.get(purpose, ()):
        val = os.getenv(env_key)
        if val:
            return val
    if purpose != "general":
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(config_path, encoding="utf-8")
            val = cfg.get("model", f"{purpose}_model", fallback=None)
            if val:
                return val
        except Exception:
            pass
        return MODEL_DEFAULTS.get(purpose)
    return None


def _is_model_unavailable(err: str) -> bool:
    """判断错误是否应触发降级到备份模型（模型不存在 / 账号配额用尽）。"""
    if not err:
        return False
    low = err.lower()
    return ("invalidendpointormodel" in low or "model.notfound" in low
            or ("not found" in low and "model" in low)
            or "accountquotaexceeded" in low or "quota" in low)


# 主模型故障记忆（进程级）：一次判定不可用后，后续新建的实例直接走备份配置，
# 避免每个实例都先吃一轮 429/404 重试。到期后自动重试主模型。
_PRIMARY_DOWN_UNTIL = 0.0
PRIMARY_RETRY_SEC = float(os.getenv("LLM_PRIMARY_RETRY_SEC") or 600)


def _primary_down() -> bool:
    return time.time() < _PRIMARY_DOWN_UNTIL


class LLMAdapter:
    """LLM 适配器（v1 只接豆包，可扩展其他模型）"""

    def __init__(
        self,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        config_path: str = "config.ini",
        purpose: str = "general",
    ):
        # 从 config.ini 读取（环境变量 > config.ini > 默认值）
        config_model = None
        cfg = None
        if Path(config_path).exists():
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(config_path, encoding='utf-8')
            config_model = cfg.get('model', 'model', fallback=None)

        self.purpose = purpose
        self.config_path = config_path
        self.fallback_model = MODEL_DEFAULTS["general"]
        # 按用途解析模型（环境变量 > config.ini [model] <purpose>_model > 用途默认 > 通用）
        self.model = model or resolve_model(purpose, config_path) or config_model or MODEL_DEFAULTS["general"]
        if _primary_down() and not model:
            self.model = self.fallback_model
        self.temperature = temperature
        # 输出上限（不是上下文窗口）：显式参数 > [model] <purpose>_max_tokens
        # > [model] max_tokens > 用途默认
        self.max_tokens = (max_tokens
                           if max_tokens is not None
                           else _cfg_int(cfg, "model", f"{purpose}_max_tokens")
                           or _cfg_int(cfg, "model", "max_tokens")
                           or PURPOSE_DEFAULT_TOKENS.get(purpose, 2048))
        self.history_max_chars = (
            _cfg_int(cfg, "model", "history_max_chars") or 20000)
        # 主语言模型专用端点（与视觉/embedding 使用的 Doubao_API_URL 隔离）
        # 优先级：显式参数 > LLM_API_KEY/LLM_API_URL > Doubao_API_KEY/Doubao_API_URL
        # 主模型已判定不可用时，新实例直接使用备份密钥/端点
        if _primary_down():
            self.api_key = (api_key or os.getenv("Doubao_API_KEY")
                            or os.getenv("DOUBAO_API_KEY"))
            self.api_url = (api_url
                            or os.getenv("Doubao_API_URL",
                                         "https://ark.cn-beijing.volces.com/api/v3"))
        else:
            self.api_key = (api_key
                            or os.getenv("LLM_API_KEY")
                            or os.getenv("Doubao_API_KEY")
                            or os.getenv("DOUBAO_API_KEY"))
            self.api_url = (api_url
                            or os.getenv("LLM_API_URL")
                            or os.getenv("Doubao_API_URL",
                                         "https://ark.cn-beijing.volces.com/api/v3"))

        if not self.api_key:
            logger.warning(
                "[llm] LLM_API_KEY / Doubao_API_KEY 未设置，调用将失败。"
                "请设置环境变量或通过 .env 注入。"
            )

        # 简单历史缓存（单会话内存）
        self._history: List[Dict[str, str]] = []
        self._last_call_at = 0.0
        self._min_interval = 0.2  # 简单限速

    # ---------- 对外接口 ----------

    async def chat(
        self,
        system_prompt: str,
        user_text: str,
        history: Optional[List[Dict[str, str]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        remember: bool = True,
    ) -> Dict[str, Any]:
        """异步调用豆包

        Args:
            system_prompt: 系统提示（含 persona + state）
            user_text: 用户输入
            history: 历史消息（None 用内置缓存）
            temperature: 覆盖默认
            max_tokens: 覆盖默认
            tools: function calling 工具列表
            images: 主播态按需抓帧，[{source, data: dataURL, mime_type}]
            remember: 是否把本轮写入内置历史（规划器/Agent 循环应传 False）

        Returns:
            {"response": str, "tokens_used": int, "tool_calls": list, "error": str|None}
        """
        if not self.api_key:
            return {
                "response": "",
                "tokens_used": 0,
                "tool_calls": None,
                "error": "Doubao_API_KEY 未设置",
            }

        # 限速（避免触发 API 限流）
        await self._rate_limit()

        # 多模态：当前帧只用于这一次请求，不进入历史
        image_parts: List[Dict[str, Any]] = []
        for img in (images or [])[:2]:
            if not isinstance(img, dict):
                continue
            data = img.get("data")
            if isinstance(data, str) and data.startswith("data:image/"):
                image_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data, "detail": "auto"},
                })
        user_content: Any = user_text
        if image_parts:
            user_content = [{"type": "text", "text": user_text or "（看看这个）"}]
            user_content.extend(image_parts)

        # 拼 state（参考 LLM/chat_model.py:ChatState）
        history_msgs = list(history) if history is not None else list(self._history)
        msgs = history_msgs + [{"role": "user", "content": user_content}]
        state = {
            "messages": msgs,
            "system_prompt": system_prompt,
            "question": user_text,
            "model": self.model,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            # 显式带上本适配器的端点，避免被其它模块的全局 Doubao_API_URL 覆盖
            "api_key": self.api_key,
            "api_url": self.api_url,
        }

        # 异步调用（在线程池跑同步函数）
        try:
            loop = asyncio.get_running_loop()
            from LLM.chat_model import doubao_chat_node
            result = await loop.run_in_executor(
                None, doubao_chat_node, state, tools
            )
            # 主模型不可用（模型不存在 / 配额用尽）→ 降级到备份模型；
            # 备份模型不在 coding 端点支持列表内，因此同时切回备份端点
            err_text = str(result.get("error") or "")
            if err_text and _is_model_unavailable(err_text) and self.model != self.fallback_model:
                global _PRIMARY_DOWN_UNTIL
                _PRIMARY_DOWN_UNTIL = time.time() + PRIMARY_RETRY_SEC
                backup_key = (os.getenv("Doubao_API_KEY")
                              or os.getenv("DOUBAO_API_KEY") or "")
                backup_url = (os.getenv("Doubao_API_URL")
                              or "https://ark.cn-beijing.volces.com/api/v3")
                switched = []
                if backup_key and backup_key != self.api_key:
                    self.api_key = backup_key
                    state["api_key"] = self.api_key
                    switched.append("密钥")
                if backup_url != self.api_url:
                    self.api_url = backup_url
                    state["api_url"] = self.api_url
                    switched.append("端点")
                logger.warning(
                    f"[llm] 主模型 {self.model} 不可用（{err_text[:120]}），"
                    f"降级到 {self.fallback_model}"
                    + (f"（切回备份{'/'.join(switched)}）" if switched else "")
                )
                self.model = self.fallback_model
                state["model"] = self.model
                result = await loop.run_in_executor(
                    None, doubao_chat_node, state, tools
                )
        except Exception as e:
            logger.exception(f"[llm] 调用异常: {e}")
            return {
                "response": "",
                "tokens_used": 0,
                "tool_calls": None,
                "error": str(e),
            }

        # 后处理：更新历史（图片不落历史，避免 base64 膨胀）
        response = result.get("response", "") or ""
        if remember and not result.get("error"):
            history_user = {
                "role": "user",
                "content": (user_text or "（看看这个）")
                + ("\n[已查看图片]" if image_parts else ""),
            }
            self._history = self._trim_history(
                history_msgs + [history_user, {"role": "assistant", "content": response}],
                self.history_max_chars,
            )

        return {
            "response": response,
            "tokens_used": result.get("tokens_used", 0),
            "tool_calls": result.get("tool_calls"),
            "error": result.get("error"),
        }

    async def chat_simple(
        self,
        system_prompt: str,
        user_text: str,
        images: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """简单接口：只返回文本（可附带当前帧图片）"""
        result = await self.chat(system_prompt, user_text,
                                 images=images, temperature=temperature)
        if result.get("error"):
            logger.error(f"[llm] {result['error']}")
            return "（AI 调用出错，请稍后再试）"
        return result["response"]

    async def chat_with_context_control(
        self,
        system_prompt: str,
        user_text: str,
        history: Optional[List[Dict[str, str]]] = None,
        max_history_chars: int = 12000,
    ) -> str:
        """带上下文控制的对话（自动压缩历史 + 限长）

        借鉴 context_controller.py 的设计：
          - 历史超 max_history_chars 时，截断最早的 user/assistant
          - 保留 system_prompt + 最近 N 轮对话
        """
        result = await self.chat(
            system_prompt=system_prompt,
            user_text=user_text,
            history=self._trim_history(history, max_history_chars),
        )
        if result.get("error"):
            return "（AI 调用出错，请稍后再试）"
        return result["response"]

    def _trim_history(
        self, history: Optional[List[Dict[str, str]]], max_chars: int,
    ) -> List[Dict[str, str]]:
        """截断历史到指定字符数（保留最新的消息）"""
        if not history:
            return []

        def _text_len(msg: Dict[str, Any]) -> int:
            content = msg.get("content", "")
            if isinstance(content, str):
                return len(content)
            if isinstance(content, list):
                return sum(
                    len(str(part.get("text", "")))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            return 0

        total = sum(_text_len(m) for m in history)
        if total <= max_chars:
            return list(history)
        # 从最早的开始丢，直到 ≤ max_chars
        trimmed = list(history)
        while total > max_chars and len(trimmed) > 2:
            removed = trimmed.pop(0)
            total -= _text_len(removed)
        return trimmed

    def reset_history(self):
        """清空历史（用于切会话）"""
        self._history = []

    # ---------- 内部 ----------

    async def _rate_limit(self):
        elapsed = time.time() - self._last_call_at
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_call_at = time.time()


# 单例（按用途各一个实例，互不污染 history）
_LLM_INSTANCES: Dict[str, LLMAdapter] = {}


def get_llm_for(purpose: str = "general", **kwargs) -> LLMAdapter:
    """获取某用途的 LLM 适配器单例（general / streamer / will / operator / coding）"""
    if purpose not in _LLM_INSTANCES:
        _LLM_INSTANCES[purpose] = LLMAdapter(purpose=purpose, **kwargs)
    return _LLM_INSTANCES[purpose]


def get_llm(**kwargs) -> LLMAdapter:
    """获取通用 LLM 适配器单例（向后兼容）"""
    return get_llm_for("general", **kwargs)


def get_streamer_llm() -> LLMAdapter:
    """主播脑：数字人实时对话模型（低延迟）"""
    return get_llm_for("streamer")


def get_will_llm() -> LLMAdapter:
    """意志脑：决策模型"""
    return get_llm_for("will")


def get_operator_llm() -> LLMAdapter:
    """操作脑：执行/推理模型"""
    return get_llm_for("operator")


def get_coding_llm() -> LLMAdapter:
    """编码循环：编码模型（未配置时回落通用模型）"""
    return get_llm_for("coding")


__all__ = [
    'LLMAdapter', 'get_llm', 'get_llm_for',
    'get_streamer_llm', 'get_will_llm', 'get_operator_llm', 'get_coding_llm',
    'resolve_model', 'MODEL_DEFAULTS', 'PURPOSE_DEFAULT_TOKENS',
]
