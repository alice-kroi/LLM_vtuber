#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 响应 Pydantic 数据模型 (v2 - 路线 A P0 扩展)

定义 Live2D 模块的响应数据结构，包含：
- 语气 (tone)
- 内容 (content)
- 目光方向 (visual_focus)
- 嘴巴状态 (mouth_state)
- A-P0 新增: 表情(expression) + 热键(hotkey) + 嘴巴开合强度(mouth_intensity)

同时提供旧格式字符串解析和 JSON Schema 常量，
用于 function calling 的 response_format。
"""

import json
import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel


# --------- 常量 ---------
ALLOWED_TONES = {
    "扮演慌张", "调皮", "尴尬", "感动", "积极", "急了", "假装",
    "惊喜", "开心", "撩拨", "难过", "普通", "撒娇", "生气",
    "严肃", "疑问", "自言"
}

VALID_DIRECTIONS = {
    "center", "up", "down", "left", "right",
    "upleft", "upright", "downleft", "downright"
}

# A-P0: 可用表情类型 (与 EXPRESSION_PARAM_MAP 对应)
VALID_EXPRESSIONS = {
    "smile", "angry", "sad", "surprised", "shy",
    "wink_left", "wink_right", "blink", "neutral"
}

# A-P0: Tone -> Expression 自动推断表 (兼容没有表情热键的模型)
TONE_TO_EXPRESSION = {
    # --- 原始允许值 ---
    "开心": "smile", "调皮": "smile", "撩拨": "wink_right",
    "积极": "smile", "感动": "sad", "难过": "sad",
    "生气": "angry", "急了": "angry", "扮演慌张": "surprised",
    "惊喜": "surprised", "撒娇": "shy", "尴尬": "shy",
    "疑问": None, "普通": None, "严肃": None, "假装": None, "自言": None,
    # --- 扩展常见 tone（LLM 可能返回的额外值） ---
    "平和": None, "温柔": "smile", "礼貌": None, "冷淡": None,
    "热情": "smile", "好奇": "surprised", "害羞": "shy",
    "悲伤": "sad", "愤怒": "angry", "惊讶": "surprised",
    "开心笑": "smile", "撒娇笑": "shy", "嘲笑": "smile",
    "无奈": None, "困惑": None, "沉思": None, "淡定": None,
}


class Live2DResponse(BaseModel):
    """Live2D 响应数据模型 (v2 - 扩展表情/热键)

    Attributes:
        tone: 语气，必须是 ALLOWED_TONES 中的值
        content: 说话内容
        visual_focus: 目光方向，必须是 VALID_DIRECTIONS 中的值
        mouth_state: 嘴巴状态，open 或 close
        mouth_intensity: A-P0 嘴巴开合强度 0.0-1.0，覆盖 mouth_state
        expression: A-P0 表情名称，None=保持当前
        expression_intensity: A-P0 表情强度 0.0-1.0
        hotkey: A-P0 通过名称触发热键动作（需模型有对应热键）
    """

    # --- 基础字段 (v1) ---
    tone: Literal[
        "扮演慌张", "调皮", "尴尬", "感动", "积极", "急了", "假装",
        "惊喜", "开心", "撩拨", "难过", "普通", "撒娇", "生气",
        "严肃", "疑问", "自言"
    ] = "普通"
    content: str = ""
    visual_focus: Literal[
        "center", "up", "down", "left", "right",
        "upleft", "upright", "downleft", "downright"
    ] = "center"
    mouth_state: Literal["open", "close"] = "close"

    # --- A-P0 新增字段 (可选，LLM 可以不填) ---
    mouth_intensity: Optional[float] = None      # 0.0-1.0, 覆盖 mouth_state
    expression: Optional[str] = None              # VALID_EXPRESSIONS 中的值
    expression_intensity: Optional[float] = None  # 0.0-1.0
    hotkey: Optional[str] = None                  # 热键名称

    def to_dict(self) -> dict:
        """将响应转换为字典格式 (包含 A-P0 新字段)"""
        return {
            "tone": self.tone,
            "content": self.content,
            "visual_focus": self.visual_focus,
            "mouth_state": self.mouth_state,
            "mouth_intensity": self.mouth_intensity,
            "expression": self.expression,
            "expression_intensity": self.expression_intensity,
            "hotkey": self.hotkey,
        }

    @classmethod
    def from_legacy_string(cls, response: str) -> "Live2DResponse":
        """解析旧格式字符串 【语气】内容|目光方向|嘴巴状态

        Args:
            response: 大模型生成的原始响应，格式为 【语气】内容|方向|嘴部状态

        Returns:
            解析后的 Live2DResponse 实例
        """
        if not response or not response.startswith("【"):
            return cls(content=response or "")

        end_bracket = response.find("】")
        if end_bracket == -1:
            return cls(content=response)

        extracted_tone = response[1:end_bracket]
        tone = extracted_tone if extracted_tone in ALLOWED_TONES else "普通"

        rest = response[end_bracket + 1:].strip()
        parts = rest.split("|")
        content = parts[0].strip() if parts else ""

        visual_focus = "center"
        mouth_state = "close"

        if len(parts) > 1:
            direction = parts[1].strip().lower()
            if direction in VALID_DIRECTIONS:
                visual_focus = direction

        if len(parts) > 2:
            mouth = parts[2].strip().lower()
            if mouth in ("open", "close"):
                mouth_state = mouth

        return cls(
            tone=tone,
            content=content,
            visual_focus=visual_focus,
            mouth_state=mouth_state,
        )


# --------- JSON Schema ---------
# A-P0: 新字段可选，LLM 可以不填 expression/hotkey/mouth_intensity
LIVE2D_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "live2d_response",
        "description": "Live2D 虚拟主播响应结构 (v2 扩展表情/热键)",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "tone": {
                    "type": "string",
                    "enum": sorted(ALLOWED_TONES),
                    "description": "语气类型"
                },
                "content": {
                    "type": "string",
                    "description": "说话内容"
                },
                "visual_focus": {
                    "type": "string",
                    "enum": sorted(VALID_DIRECTIONS),
                    "description": "目光方向"
                },
                "mouth_state": {
                    "type": "string",
                    "enum": ["open", "close"],
                    "description": "嘴巴状态"
                },
                "expression": {
                    "type": ["string", "null"],
                    "enum": sorted(VALID_EXPRESSIONS) + [None],
                    "description": "表情名称（可选）：smile开心/angry生气/sad难过/surprised惊讶/shy害羞/wink_left/wink_right眨眼/blink眨眼/neutral中性"
                },
                "expression_intensity": {
                    "type": ["number", "null"],
                    "description": "表情强度 0.0-1.0（可选）"
                },
                "hotkey": {
                    "type": ["string", "null"],
                    "description": "触发热键名称（可选，需模型有预设热键）"
                }
            },
            "required": ["tone", "content", "visual_focus", "mouth_state"],
            "additionalProperties": False
        }
    }
}

logger = logging.getLogger(__name__)


def _extract_json_from_text(text: str) -> str | None:
    """从文本中提取 JSON 内容，支持代码块包裹和纯 JSON 格式。

    Args:
        text: 可能包含 JSON 的文本

    Returns:
        提取出的 JSON 字符串，如果未找到则返回 None
    """
    if not text:
        return None

    # 尝试匹配 ```json ... ``` 格式
    json_block_pattern = r'```json\s*\n?(.*?)\n?\s*```'
    match = re.search(json_block_pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 尝试匹配 ``` ... ``` 格式（无语言标记）
    generic_block_pattern = r'```\s*\n?(.*?)\n?\s*```'
    match = re.search(generic_block_pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 尝试直接解析整个文本
    stripped = text.strip()
    if stripped.startswith('{') and stripped.endswith('}'):
        return stripped

    # 尝试提取第一个 { 到最后一个 } 之间的内容
    first_brace = stripped.find('{')
    last_brace = stripped.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        potential_json = stripped[first_brace:last_brace + 1]
        try:
            json.loads(potential_json)
            return potential_json
        except json.JSONDecodeError:
            pass

    return None


def parse_structured_response(response: str, enable_live2d: bool = True) -> dict:
    """解析结构化响应，优先 JSON 解析 + Pydantic 校验，失败则降级到旧格式字符串解析。

    Args:
        response: 大模型生成的原始响应
        enable_live2d: 是否启用 Live2D 相关解析

    Returns:
        统一格式的响应字典 (包含 expression/hotkey 等 A-P0 字段)
    """
    default_result = {
        "tone": "普通",
        "content": response if response else "",
        "visual_focus": "center",
        "mouth_state": "close",
        "mouth_intensity": None,
        "expression": None,
        "expression_intensity": None,
        "hotkey": None,
    }

    if not response or not isinstance(response, str) or not response.strip():
        logger.warning("响应为空或无效，返回默认值")
        return default_result

    # 尝试提取并解析 JSON（支持代码块包裹）
    json_str = _extract_json_from_text(response)
    if json_str:
        try:
            parsed = json.loads(json_str)
            logger.info("JSON 解析成功，尝试 Pydantic 校验")

            result = Live2DResponse.model_validate(parsed)
            logger.info("Pydantic 校验通过，使用 JSON 解析路径")
            return result.to_dict()
        except json.JSONDecodeError as e:
            logger.info(f"JSON 解析失败: {e}，尝试旧格式字符串解析")
        except Exception as e:
            logger.warning(f"Pydantic 校验失败: {e}，尝试旧格式字符串解析")
    else:
        logger.info("未找到有效 JSON 内容，尝试旧格式字符串解析")

    try:
        result = Live2DResponse.from_legacy_string(response)
        logger.info("旧格式字符串解析成功，使用 legacy 解析路径")
        return result.to_dict()
    except Exception as e:
        logger.warning(f"旧格式解析也失败: {e}，返回默认值")
        return default_result