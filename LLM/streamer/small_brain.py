#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小模型简单思考（Small Brain）：主播脑门面的 L1 层。

职责（本地小模型，CPU 推理，几乎零 API 成本）：
- 直接回答简单问题：寒暄 / 闲聊 / 情绪回应 / 简单问答（多变输入，比纯规则强）
- 判断是否需要大模型（need_big）：需查信息 / 需执行操作 / 复杂推理 → True，进编排层

设计：
- 惰性加载（首次调用才载入，常驻）；推理放线程池避免阻塞事件循环
- 结构化提示让 0.5B 模型输出 {reply} 或 {need_big: true}，解析容错
- 加载失败/解析失败 → 保守返回 need_big=True（宁可放大模型，不漏需求）

模型：Qwen/Qwen2.5-0.5B-Instruct（本地路径默认 D:\\models\\qwen2.5-0.5b-instruct）
注：本地 config.json 的 transformers_version 已修正为 4.58.0，规避 transformers 4.57.2
    的 tokenizer 加载 bug（dict.model_type）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = r"D:\models\qwen2.5-0.5b-instruct"

# 让模型二选一：能答就直接给简短回复；不能答/需大模型则标 need_big
_SYSTEM_PROMPT = """你是 AI 主播的小助手。判断用户消息：
- 只有当你非常确定它只是寒暄、闲聊、问候、情绪表达时，才输出 {"reply": "一句简短友好的回答"}
- 如果你不确定、或涉及查信息/执行操作/专业知识/复杂推理，必须输出 {"need_big": true}
- 绝对不要编造事实，不要假装执行操作，不要复述用户的话
只输出一个 JSON 对象，不要输出其它内容。"""

# 强需求规则：命中即放大模型，不信任小模型判断（防幻觉）
_STRONG_NEED_BIG = [
    r"帮我|请你|麻烦你|给我",
    r"启动|打开|关闭|删除|安装|下载|设置|执行|切换|搜索|查询|播放|暂停|停止|发送|记录|记住|连接|启动",
    r"什么|为什么|哪里|哪儿|多少|几点|几号|星期几|谁|哪个|哪些|天气预报|新闻|价格|行情",
    r"[0-9]+\s*[+\-*/×÷]\s*[0-9]", r"翻译|解释一下|推荐|建议",
]
# 弱需求规则（常见闲聊疑问词）：命中则交小模型判断（它是闲聊还是真需求）
_WEAK_NEED_BIG = [r"怎么|怎么样|怎样|如何|是不是|有没有|能不能|发生了什么|最新"]


def _need_big_by_rules(content: str) -> bool:
    """强规则命中 → 必须放大模型。"""
    import re as _re
    return any(_re.search(p, content) for p in _STRONG_NEED_BIG)


def _weak_rule_hit(content: str) -> bool:
    """弱规则命中（可能闲聊）→ 交给小模型复核。"""
    import re as _re
    return any(_re.search(p, content) for p in _WEAK_NEED_BIG)


class SmallBrain:
    """本地小模型简单思考封装。"""

    def __init__(self, model_path: Optional[str] = None, persona: Optional[str] = None,
                 max_new_tokens: int = 96, device: str = "cpu"):
        self.model_path = model_path or os.environ.get(
            "SMALL_BRAIN_MODEL", DEFAULT_MODEL_PATH)
        self.persona = persona or "活泼友好"
        self.max_new_tokens = max_new_tokens
        self.device = device
        self._model = None
        self._tok = None
        self._load_lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    async def _ensure_loaded(self) -> bool:
        """惰性加载（线程池中执行，避免阻塞事件循环）。"""
        if self.loaded:
            return True
        async with self._load_lock:
            if self.loaded:
                return True
            try:
                await asyncio.to_thread(self._load_sync)
                logger.info(f"[SmallBrain] 小模型已加载: {self.model_path}")
                return True
            except Exception as e:
                logger.error(f"[SmallBrain] 小模型加载失败（将回退大模型）: {e}")
                return False

    def _load_sync(self) -> None:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self._tok = AutoTokenizer.from_pretrained(self.model_path)
        self._model = AutoModelForCausalLM.from_pretrained(self.model_path)
        self._model.eval()

    def _generate(self, prompt: str) -> str:
        msgs = [{"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt}]
        text = self._tok.apply_chat_template(msgs, tokenize=False,
                                             add_generation_prompt=True)
        ids = self._tok(text, return_tensors="pt")
        out = self._model.generate(**ids, max_new_tokens=self.max_new_tokens,
                                   do_sample=False, pad_token_id=self._tok.eos_token_id)
        return self._tok.decode(out[0][ids["input_ids"].shape[1]:],
                                skip_special_tokens=True).strip()

    def _think_sync(self, content: str, recent: Optional[str]) -> Dict[str, Any]:
        ctx = f"（最近对话：{recent[:120]}）\n" if recent else ""
        prompt = f"{ctx}用户消息：{content[:200]}\n判断并输出："
        try:
            raw = self._generate(prompt)
        except Exception as e:
            logger.warning(f"[SmallBrain] 推理失败，保守放大模型: {e}")
            return {"reply": None, "need_big": True}
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> Dict[str, Any]:
        """容错解析 {reply} / {need_big}。"""
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"reply": None, "need_big": True}   # 无 JSON → 保守放大模型
        try:
            obj = json.loads(m.group(0))
        except Exception:
            # JSON 不严格：尝试提取 reply 文本
            rm = re.search(r'"reply"\s*:\s*"([^"]+)"', m.group(0))
            if rm:
                return {"reply": rm.group(1), "need_big": False}
            return {"reply": None, "need_big": True}
        reply = str(obj.get("reply", "")).strip() if obj.get("reply") else None
        need_big = bool(obj.get("need_big", False))
        if reply:
            return {"reply": reply, "need_big": False}
        return {"reply": None, "need_big": need_big}

    async def simple_think(self, content: str, sess: Any = None) -> Dict[str, Any]:
        """小模型简单思考：直接答简单问题，或判断需要大模型。

        路由（保守，防幻觉）：
        1. L0 规则模板：确定性寒暄直接答（免费）
        2. 需求关键词规则：操作/信息意图 → 直接 need_big=True（不信任小模型判断）
        3. 小模型：仅当无明显需求时，判断能否简单答（闲聊/情绪）
        """
        # 1) L0 规则模板（免费）
        quick = self._quick_greeting(content)
        if quick:
            return {"reply": quick, "need_big": False}
        # 2) 需求关键词规则 → 放大模型（防小模型幻觉）
        if _need_big_by_rules(content):
            return {"reply": None, "need_big": True}
        # 3) 小模型简单思考
        if not await self._ensure_loaded():
            return {"reply": None, "need_big": True}   # 模型不可用 → 放大模型
        recent = None
        if sess is not None and getattr(sess, "history", None):
            recent = str(sess.history[-1].get("content", ""))[-120:]
        return await asyncio.to_thread(self._think_sync, content, recent)

    @staticmethod
    def _quick_greeting(content: str) -> Optional[str]:
        """L0 规则模板：确定性寒暄直接答（免费，不调模型）。

        分词匹配：内容去掉标点/空白后拆成若干问候词，若每个词都命中词表 → 答通用问候。
        """
        import re as _re
        c = content.strip().lower()
        if not c:
            return None
        # 问候/致谢词表
        greetings = {"你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗",
                     "晚上好", "早上好", "中午好", "下午好", "晚安"}
        thanks = {"谢谢", "感谢", "多谢", "thanks", "谢谢啦"}
        bye = {"再见", "拜拜", "886", "bye"}
        tokens = [t for t in _re.split(r"[，。！？\s,.!?~～]+", c) if t]
        if not tokens:
            return None
        # 全部是问候词 → 通用问候
        if all(t in greetings for t in tokens):
            return "你好呀！我在呢，有什么想聊的都可以跟我说～"
        # 全部是致谢词 → 回应
        if all(t in thanks for t in tokens):
            return "不客气～能帮到你我很开心！"
        # 全部是道别词 → 再见
        if all(t in bye for t in tokens):
            return "再见啦，下次再来找我玩哦！"
        # 纯笑声
        if _re.fullmatch(r"[哈哈呵呵嘿嘿]+|hahaha+|lol+", c):
            return "哈哈哈，你笑得好开心，我也跟着高兴！"
        return None


# 模块级单例（门面层复用）
_brain: Optional[SmallBrain] = None


def get_small_brain(model_path: Optional[str] = None) -> SmallBrain:
    global _brain
    if _brain is None:
        _brain = SmallBrain(model_path=model_path)
    return _brain
