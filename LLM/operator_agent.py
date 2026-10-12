#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
操作思维（Operator Agent）—— 三脑架构之"操作脑"

职责：根据主体（Supervisor）转交的指令，通过 ReAct 循环真实操作电脑
（打开文件/窗口、键鼠、UI定位、视觉定位、目录读取等），并把操作结果
与技术性描述返回给主体，供主播脑组织语言回复观众。

设计要点：
- 独立 system prompt：与主播人设解耦，专注"怎么把电脑操作做对"。
- 独立工具集：只暴露 computer_use 类工具，不混入 web_search/tts 等主播工具。
- 独立思考循环：复用 tool_registry 执行，但限定操作工具子集。
- 三脑信息交换：operator 接收 supervisor.state 传入的 question/tools，
  返回 {requested_op, completed, summary, result} 写回共享 LLMState。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 操作脑中允许使用的工具白名单（均为"操作电脑"原子能力）
OPERATOR_TOOL_NAMES = [
    "os_open_path",
    "os_list_directory",
    "os_list_windows",
    "os_activate_window",
    "os_minimize_window",
    "os_show_desktop",
    "os_click_xy",
    "os_type_text",
    "os_press_key",
    "os_scroll",
    "ui_list_elements",
    "vision_click",
    "os_check",
    "os_capture_window",
    "os_open_file",
    "os_find_file",
    "os_browser_search",
]


def _build_operator_schema() -> List[dict]:
    """从 computer_use 的真实工具 Schema 中过滤出操作脑允许的工具（含完整 parameters）。"""
    try:
        from tool.computer_use import COMPUTER_USE_TOOLS_SCHEMA
        return [s for s in COMPUTER_USE_TOOLS_SCHEMA
                if s["function"]["name"] in OPERATOR_TOOL_NAMES]
    except Exception:
        return [{"type": "function", "function": {"name": n}} for n in OPERATOR_TOOL_NAMES]


# 操作脑工具 Schema（由 SkillWorldAssembly 按激活域聚合，含完整 parameters）
# 默认挂载 desktop 域，聚合结果与原白名单完全一致（零回归）。
from LLM.skill_world import skill_assembly

OPERATOR_TOOL_SCHEMA: List[dict] = skill_assembly.active_tool_schemas()

# 操作脑基础人格（域无关），可用工具/执行原则由各激活域拼装
_OPERATOR_IDENTITY = """你是「操作思维」，一名专注的电脑操作执行者（Operator）。

你的唯一任务：把接到的指令通过真实调用电脑操作工具完成，不编造结果。

"""

OPERATOR_SYSTEM_PROMPT = skill_assembly.build_system_prompt(base=_OPERATOR_IDENTITY) + (
    """
输出格式（严格 JSON，不要多余文字）：
{"summary": "操作过程简述", "result": "操作结果或关键信息", "done": true}
"""
)


async def _execute_operator_tool_name(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个操作工具（复用全局 tool_registry，但仅操作激活域白名单内工具）"""
    from tool.tool_node import tool_registry
    from tool.tool_node import ToolCall

    if name not in skill_assembly.active_tool_names():
        return {"success": False, "error": f"操作脑不允许调用工具: {name}"}
    tc = ToolCall(
        tool_call_id=f"op_{name}",
        name=name,
        arguments=arguments,
    )
    return await tool_registry.execute_tool(tc, timeout=240.0)


def _tool_name(tc) -> str:
    """兼容取 tool_call 的名称（新版 SDK 用 tc.function.name）"""
    fn = getattr(tc, "function", None)
    if fn is not None:
        return getattr(fn, "name", "") or ""
    return getattr(tc, "name", "") or ""


def _tool_arguments(tc) -> str:
    """兼容取 tool_call 的参数 JSON 字符串"""
    fn = getattr(tc, "function", None)
    if fn is not None:
        return getattr(fn, "arguments", "") or "{}"
    return getattr(tc, "arguments", "{}") or "{}"


def _tool_call_id(tc) -> str:
    """兼容取 tool_call id"""
    return getattr(tc, "id", "") or ""


def _fresh_tools() -> List[dict]:
    """每次调用重新从装配层取激活域工具集，让每域开关运行时生效（P1-1）。"""
    from LLM.skill_world import skill_assembly
    return skill_assembly.active_tool_schemas()


def _fresh_prompt() -> str:
    """每次调用重新拼装 prompt（激活域增删即时反映到操作脑指令）。"""
    from LLM.skill_world import skill_assembly
    prompt = skill_assembly.build_system_prompt(base=_OPERATOR_IDENTITY)
    return prompt + (
        """
输出格式（严格 JSON，不要多余文字）：
{"summary": "操作过程简述", "result": "操作结果或关键信息", "done": true}
"""
    )


async def run_operator(question: str, tools: Optional[List[dict]] = None,
                       max_rounds: int = 10, *,
                       progress_cb: Optional[Any] = None,
                       cancel_event: Optional[asyncio.Event] = None,
                       task_id: str = "", source: str = "desktop") -> Dict[str, Any]:
    """
    运行操作思维大脑：对一条电脑操作指令执行 ReAct 工具循环。

    Args:
        question: 主体转交的操作指令（如"打开E盘并列出文件夹"）
        tools: 操作工具 Schema 列表（默认由装配层聚合的最新激活域工具集）
        max_rounds: 最大工具轮数
        progress_cb: 可选进度回调 progress_cb(frac, message)，每轮工具后调用（任务式架构用）
        cancel_event: 可选协作式取消事件，每轮工具前检测（被置位则提前停止）
        task_id: 任务 ID（进度上报用）
        source: 能力域来源标识（默认 desktop，由 TaskManager 派发时透传）

    Returns:
        统一结果 {requested_op, completed, summary, result, rounds, task_id, source}
    """
    if tools is None:
        tools = _fresh_tools()
    prompt = _fresh_prompt()

    def _report(rounds_done, msg):
        if progress_cb is not None:
            try:
                progress_cb(min(1.0, rounds_done / max(max_rounds, 1)), msg)
            except Exception:
                pass

    # 操作脑使用独立轻量 LLM 调用（chat/completions，带 tools），配置从环境变量读取（与项目一致）
    import os
    from openai import AsyncOpenAI

    api_key = os.getenv("Doubao_API_KEY") or os.getenv("DOUBAO_API_KEY")
    api_url = os.getenv("Doubao_API_URL", "https://ark.cn-beijing.volces.com/api/v3")
    model = os.getenv("DOUBAO_MODEL", "doubao-seed-1-8-251228")
    if not api_key:
        logger.error("[操作思维] 未找到 Doubao_API_KEY 环境变量")
        return {"requested_op": question, "completed": False,
                "summary": "未配置API Key", "result": "", "rounds": 0,
                "task_id": task_id, "source": source}

    client = AsyncOpenAI(api_key=api_key, base_url=api_url)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": question},
    ]

    rounds = 0
    try:
        while rounds < max_rounds:
            # 协作式取消：每轮工具前检测（任务式架构中用户/主播可取消）
            if cancel_event is not None and cancel_event.is_set():
                return {"requested_op": question, "completed": False,
                        "summary": "任务被取消", "result": "", "rounds": rounds,
                        "task_id": task_id, "source": source}

            rounds += 1
            tool_names_now = [] if not messages else []
            resp = await client.chat.completions.create(
                model=model, messages=messages, tools=tools, temperature=0.2,
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                # 无工具调用 → 返回最终结论（要求 JSON）
                try:
                    payload = json.loads(msg.content or "{}")
                except Exception:
                    payload = {"summary": msg.content or "", "result": msg.content or "", "done": True}
                _report(rounds, "完成分析与归纳")
                return {
                    "requested_op": question,
                    "completed": payload.get("done", True),
                    "summary": payload.get("summary", ""),
                    "result": payload.get("result", msg.content or ""),
                    "rounds": rounds,
                    "task_id": task_id,
                    "source": source,
                }

            # 执行本轮全部工具调用（并行）
            tool_names_now = [_tool_name(tc) for tc in msg.tool_calls]
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": _tool_call_id(tc), "type": "function",
                 "function": {"name": _tool_name(tc), "arguments": _tool_arguments(tc)}}
                for tc in msg.tool_calls
            ]})
            results = await asyncio.gather(*[
                _execute_operator_tool_name(_tool_name(tc), json.loads(_tool_arguments(tc) or "{}"))
                for tc in msg.tool_calls
            ])
            for tc, res in zip(msg.tool_calls, results):
                content = json.dumps(res.get("result") if res.get("result") is not None else res.get("error"), ensure_ascii=False)
                messages.append({"role": "tool", "tool_call_id": _tool_call_id(tc), "content": content[:2000]})
            # 进度上报（向任务管理器/主播通报当前进展）
            _report(rounds, f"正在执行「{'、'.join(tool_names_now)}」")

        return {"requested_op": question, "completed": False,
                "summary": "达到最大轮次仍未完成", "result": "", "rounds": rounds,
                "task_id": task_id, "source": source}
    except Exception as e:
        logger.error(f"[操作思维] 运行失败: {e}")
        import traceback
        traceback.print_exc()
        return {"requested_op": question, "completed": False,
                "summary": f"操作脑异常", "result": str(e), "rounds": rounds,
                "task_id": task_id, "source": source}