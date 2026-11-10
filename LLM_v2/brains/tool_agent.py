# ============================================================
# LLM_v2/brains/tool_agent.py — 通用工具智能体
# ============================================================
# 与关键词路由不同，这里由 LLM 自己完成：
#   1. 判断请求是否需要工具
#   2. 选择相关能力域
#   3. 分析怎么做、给出可执行步骤
#   4. 逐步调用工具并观察结果
#   5. 复盘是否完成；未完成继续规划
#
# 硬约束：
#   - 只能用真实存在的工具，参数必须来自 schema
#   - 没有成功工具结果时，不允许声称"已完成"
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from LLM_v2.core.llm_adapter import LLMAdapter

logger = logging.getLogger(__name__)

ProgressCB = Callable[[int, str, str], Awaitable[None]]


DOMAIN_SELECT_SYSTEM = """你是「维尔薇」的执行调度器。判断用户请求是否需要调用工具，并选出最相关的能力域。

规则：
- 纯闲聊、情绪表达、常识问答、无需实时数据的普通写作 → actionable=false
- 需要执行动作、读取实时信息、修改系统/账号/文件/游戏 → actionable=true
- 只选真正可能用到的 1-3 个域，不要贪多
- 涉及 QQ 资料/头像/签名/说说/操作记录 → 优先 qq_napcat，不要用 central_info 代替
- 涉及 QQ 资料卡背景/空间背景 → qq_napcat（qq_profile_background_prepare 只能准备图片）
- 用户要求给 AI 新增/删除可复用工具、学会新能力、自进化 → evolve
- 结合【最近上下文】理解指代：若上文刚聊过 QQ 资料/头像/签名，这里的"签名/头像"就指 QQ 域
- 拿不准时宁可 actionable=true，让规划器看到具体工具后再判断

只输出 JSON，不要代码块：
{"actionable": true, "domains": ["域id"], "reason": "为什么选这些域"}
或
{"actionable": false, "reason": "为什么不需要工具"}"""


PLAN_SYSTEM = """你是「维尔薇」的执行规划器。根据用户请求、可用工具和已执行结果，决定下一步怎么做。

规则：
1. 只使用【可用工具】里列出的工具；参数必须符合括号里的类型，带 * 的是必填。
2. 先读后写：需要现状时先调用读取类工具；写完用可验证结果确认。
3. 不要重复调用已经成功且结果足够的工具；同一个工具+同一组参数最多执行一次。
4. 动作类请求禁止在没有工具结果时声称"已完成"。
5. 参数里的文件路径、QQ 号、URL 等必须来自用户请求或已有工具结果，不要编造。
6. 最多 {max_steps} 步，优先少而准；能一步完成就不要拆三步。
7. 如果没有任何工具能完成，直接说明原因，不要硬编工具名。
8. analysis 控制在 80 字以内，why 控制在 40 字以内，避免输出过长被截断。
9. 用户问"发了几次/有没有重复/做过吗/签名为啥重复"时，先调用只读记录类工具
   （例如 qq_status 的 recent_ops）核对，不要用 ci_overview 之类全局摘要代替具体记录。
10. ci_overview 只在确实需要全局能力域信息时调用；不要重复调用它来凑步骤。
11. 遇到"改资料卡背景/空间背景/主题皮肤"这类平台没有开放设置接口的请求：
    调用 qq_profile_background_prepare 准备图片，然后如实说明只能手动设置，
    禁止声称已经改好。
12. 用户要求"给自己加个工具/学会一个可复用能力/自进化"时，用 evolve_add：
    goal 写清输入输出示例和需要固化的私有信息；name 用 kebab-case；
    code 可留空让工具自动生成；新增成功后下一轮就能调用新工具。
    一次性需求不要进化，直接用现有工具完成。

输出 JSON（不要代码块）：
{"analysis": "如何执行/为什么这样做", "done": false, "steps": [{"tool": "工具名", "args": {}, "why": "这一步达到什么"}]}

当已有工具结果足以证明完成时：
{"analysis": "完成依据", "done": true, "verified": true, "answer": "对用户说的结论，必须含关键证据"}
"""


def _json_obj(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出里抽出第一个 JSON 对象（容忍代码块/前后废话）。"""
    if not text:
        return None
    raw = str(text).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = raw[start:end + 1]
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        # 常见瑕疵：尾逗号
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else None
        except Exception:  # noqa: BLE001
            return None


def _clip(data: Any, limit: int = 1200) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = str(data)
    return text[:limit]


class ToolAgent:
    """LLM 驱动的有界工具智能体（计划 → 执行 → 观察 → 复盘）。"""

    def __init__(
        self,
        runtime,
        llm: Optional[LLMAdapter] = None,
        on_progress: Optional[ProgressCB] = None,
        max_rounds: int = 3,
        max_steps: int = 10,
    ):
        self.runtime = runtime
        # 输出上限交给配置文件（operator_max_tokens），不在这里写死
        self._llm = llm or LLMAdapter(purpose="operator", temperature=0.2)
        self._on_progress = on_progress
        self.max_rounds = max(1, int(max_rounds or 3))
        self.max_steps = max(1, int(max_steps or 10))
        self._executed: List[Dict[str, Any]] = []
        self._used_steps = 0
        self._domains: List[str] = []
        self._feedback = ""

    # ---------- 对外入口 ----------

    async def run(self, goal: str, context: str = "") -> Dict[str, Any]:
        goal = str(goal or "").strip()
        if not goal:
            return {"success": False, "error": "目标为空",
                    "answer": "", "steps": []}

        await self._progress(2, "agent:analyze", "分析请求并选择能力域")
        selection, raw_sel = await self._select_domains(goal, context)
        if selection is None:
            return {
                "success": False,
                "error": f"调度器输出无法解析: {raw_sel[:200]}",
                "answer": "我没能理解这个请求该怎么执行。",
                "steps": [],
            }
        if not selection.get("actionable", True):
            return {
                "success": False,
                "actionable": False,
                "answer": str(selection.get("reason") or "这个请求不需要调用工具。"),
                "domains": [],
                "steps": [],
            }

        self._domains = [str(d) for d in (selection.get("domains") or [])]
        allowed_tools = self._tools_for(self._domains)
        if not allowed_tools:
            return {
                "success": False,
                "actionable": True,
                "error": "选中的能力域没有可用工具",
                "answer": "没有找到能完成这个请求的工具。",
                "domains": self._domains,
                "steps": [],
            }

        analysis = str(selection.get("reason") or "")
        last_answer = ""
        for round_idx in range(self.max_rounds):
            await self._progress(
                8 + round_idx * 5, "agent:plan",
                f"第 {round_idx + 1} 轮规划（已执行 {self._used_steps} 步）")
            plan, raw_plan = await self._plan(
                goal, allowed_tools, context, analysis)
            if plan is None:
                return {
                    "success": False,
                    "error": f"规划输出无法解析: {raw_plan[:200]}",
                    "answer": "规划失败，我没能给出可执行步骤。",
                    "domains": self._domains,
                    "steps": self._executed,
                }

            analysis = str(plan.get("analysis") or analysis)
            last_answer = str(plan.get("answer") or last_answer)

            if plan.get("done"):
                if self._has_evidence():
                    await self._progress(100, "agent:done", "任务已完成")
                    return {
                        "success": True,
                        "verified": True,
                        "answer": last_answer or "已完成。",
                        "domains": self._domains,
                        "rounds": round_idx + 1,
                        "steps": self._executed,
                    }
                # 没有成功工具结果 → 不允许声称完成
                self._feedback = (
                    "你声称已完成，但没有任何成功的工具执行结果。"
                    "必须继续调用工具获取证据，或如实说明没做成。"
                )
                logger.warning(
                    f"[tool_agent] 拒绝无证据的完成声明: {last_answer[:80]}")
                continue

            steps = plan.get("steps") or []
            if not isinstance(steps, list) or not steps:
                self._feedback = "你没有给出任何步骤；请给出下一步工具调用，或说明为什么做不到。"
                continue

            await self._run_steps(steps, allowed_tools)
            self._refresh_after_evolve(allowed_tools)

        # 轮次耗尽：给出诚实结论
        ok_steps = [s for s in self._executed if s.get("success")]
        if ok_steps:
            answer = last_answer or self._fallback_answer()
            return {
                "success": False,
                "partial": True,
                "answer": answer,
                "domains": self._domains,
                "rounds": self.max_rounds,
                "steps": self._executed,
                "error": "达到最大规划轮次，任务未完全完成",
            }
        return {
            "success": False,
            "answer": last_answer or self._fallback_answer(),
            "domains": self._domains,
            "rounds": self.max_rounds,
            "steps": self._executed,
            "error": _last_error(self._executed) or "没有成功的工具结果",
        }

    # ---------- 能力域 / 工具目录 ----------

    async def _select_domains(self, goal: str, context: str = ""):
        catalog = self._domain_catalog()
        user_text = f"【用户请求】\n{goal}\n\n"
        if context:
            user_text += f"【最近上下文】\n{context[:800]}\n\n"
        user_text += f"【能力域】\n{catalog}\n\n只输出 JSON。"
        raw = await self._chat(DOMAIN_SELECT_SYSTEM, user_text)
        return _json_obj(raw), raw

    def _domain_catalog(self) -> str:
        lines: List[str] = []
        for d in self.runtime.assembly.enabled_domains():
            try:
                names = d.allowed_names()
            except Exception:  # noqa: BLE001
                continue
            if not names:
                continue
            lines.append(f"- {d.id}｜{d.label}：{', '.join(names)}")
        return "\n".join(lines)

    def _tools_for(self, domains: List[str]) -> Dict[str, Dict[str, Any]]:
        """返回 {tool_name: schema}（只包含选中域）。"""
        wanted = set(domains)
        out: Dict[str, Dict[str, Any]] = {}
        for d in self.runtime.assembly.enabled_domains():
            if d.id not in wanted:
                continue
            try:
                schemas = d.tools()
            except Exception:  # noqa: BLE001
                continue
            for schema in schemas:
                fn = schema.get("function") or {}
                name = str(fn.get("name") or "")
                if name:
                    out[name] = schema
        return out

    def _tool_lines(self, tools: Dict[str, Dict[str, Any]]) -> str:
        lines: List[str] = []
        for name, schema in tools.items():
            fn = schema.get("function") or {}
            desc = str(fn.get("description") or "")[:120]
            params = fn.get("parameters") or {}
            props = params.get("properties") or {}
            required = set(params.get("required") or [])
            plist: List[str] = []
            for pname, spec in props.items():
                ptype = str((spec or {}).get("type") or "any")
                plist.append(f"{pname}:{ptype}{'*' if pname in required else ''}")
            lines.append(f"- {name}({', '.join(plist)}) — {desc}")
        return "\n".join(lines)

    # ---------- 规划 ----------

    async def _plan(
        self,
        goal: str,
        tools: Dict[str, Dict[str, Any]],
        context: str,
        analysis: str,
    ):
        remaining = max(1, self.max_steps - self._used_steps)
        system = PLAN_SYSTEM.replace("{max_steps}", str(remaining))
        parts = [f"【用户请求】\n{goal}"]
        if context:
            parts.append(f"【补充上下文】\n{context[:1200]}")
        if analysis:
            parts.append(f"【当前分析】\n{analysis[:600]}")
        parts.append(f"【可用工具】\n{self._tool_lines(tools)}")
        if self._executed:
            parts.append("【已执行步骤】\n" + self._history_text())
        if self._feedback:
            parts.append(f"【上一轮校验】\n{self._feedback}")
        parts.append("只输出 JSON。")
        raw = await self._chat(system, "\n\n".join(parts))
        return _json_obj(raw), raw

    def _history_text(self) -> str:
        lines: List[str] = []
        for i, rec in enumerate(self._executed, start=1):
            status = "成功" if rec.get("success") else "失败"
            err = f"｜{rec.get('error')}" if rec.get("error") else ""
            lines.append(
                f"{i}. {rec.get('tool')}({_clip(rec.get('args') or {}, 300)})"
                f" → {status}{err}｜结果：{rec.get('data', '')}")
        return "\n".join(lines)

    # ---------- 执行 ----------

    async def _run_steps(
        self,
        steps: List[Any],
        tools: Dict[str, Dict[str, Any]],
    ) -> None:
        seen = {
            self._step_key(rec.get("tool"), rec.get("args") or {})
            for rec in self._executed
        }
        for raw_step in steps:
            if self._used_steps >= self.max_steps:
                return
            if not isinstance(raw_step, dict):
                continue
            name = str(raw_step.get("tool") or "").strip()
            args = raw_step.get("args")
            if not isinstance(args, dict):
                args = {}
            self._used_steps += 1

            key = self._step_key(name, args)
            if key in seen:
                self._executed.append({
                    "tool": name,
                    "args": args,
                    "success": False,
                    "error": "重复调用已跳过（相同工具和参数已执行过）",
                    "data": "",
                    "why": raw_step.get("why", ""),
                })
                self._feedback = (
                    "你重复调用了已经执行过的工具，已跳过。"
                    "请基于已有结果判断是否完成，不要再给出相同调用。"
                )
                logger.info(f"[tool_agent] 跳过重复调用: {name}")
                continue
            seen.add(key)

            if name not in tools:
                rec = {
                    "tool": name,
                    "args": args,
                    "success": False,
                    "error": "工具不在本域可用清单内（禁止编造工具名）",
                    "data": "",
                    "why": raw_step.get("why", ""),
                }
                self._executed.append(rec)
                logger.warning(f"[tool_agent] 拒绝未知工具: {name}")
                continue

            percent = min(95, 10 + int(85 * self._used_steps / self.max_steps))
            await self._progress(
                percent, "agent:execute",
                f"执行 {name}: {str(raw_step.get('why') or '')[:60]}")

            try:
                result = await self.runtime.assembly.call_tool(
                    name, args, timeout=90.0)
            except Exception as e:  # noqa: BLE001
                result = {"success": False, "error": str(e)}

            success = bool(result.get("success"))
            rec = {
                "tool": name,
                "args": args,
                "success": success,
                "error": str(result.get("error") or ""),
                "data": _clip(result.get("result", result)),
                "why": raw_step.get("why", ""),
            }
            self._executed.append(rec)
            logger.info(
                f"[tool_agent] {name} → {'成功' if success else '失败'} "
                f"({str(result.get('error') or '')[:80]})")

    @staticmethod
    def _step_key(tool: Any, args: Any) -> str:
        try:
            body = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            body = str(args)
        return f"{tool}|{body}"

    def _refresh_after_evolve(self, tools: Dict[str, Dict[str, Any]]) -> None:
        """evolve_add 成功后，把新域的工具加进当前这轮，下一步即可调用。"""
        for rec in self._executed:
            if rec.get("tool") != "evolve_add" or not rec.get("success"):
                continue
            try:
                data = json.loads(str(rec.get("data") or ""))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(data, dict):
                continue
            domain_id = str(data.get("domain_id") or "")
            if domain_id and domain_id not in self._domains:
                self._domains.append(domain_id)
                tools.update(self._tools_for([domain_id]))
                logger.info(f"[tool_agent] 新进化已可用: {domain_id}")

    _TOOL_ANSWERS = {
        "evolve_add": "已生成并挂载一个新工具",
        "evolve_remove": "已卸载一个自进化工具",
        "evolve_list": "已读取自进化清单",
        "qq_avatar_from_web": "已从网上找图并更换头像",
        "qq_profile_background_prepare": "已找到适合做资料卡背景的图片（需要手动设置）",
        "qq_avatar_set": "已更换头像",
        "qq_profile_set": "已更新 QQ 昵称/签名",
        "qq_zone_post": "已发布一条仅自己可见的说说",
        "qq_status": "已查到 QQ 操作记录",
        "qq_send": "已发送 QQ 消息",
        "qq_recent": "已读取最近 QQ 消息",
        "ci_overview": "已读取能力域概览",
    }

    def _fallback_answer(self) -> str:
        """没有拿到规划器的最终话术时，用成功步骤拼一个诚实结论。"""
        ok = [r for r in self._executed if r.get("success")]
        if not ok:
            err = _last_error(self._executed)
            return f"没做成：{err}" if err else "我没能完成这个请求。"
        labels: List[str] = []
        for rec in ok[-5:]:
            label = self._TOOL_ANSWERS.get(str(rec.get("tool")))
            if label and label not in labels:
                labels.append(label)
        if not labels:
            labels = [f"{r.get('tool')} 执行成功" for r in ok[-3:]]
        return "；".join(labels) + "。"


    # ---------- 校验 / 调用 ----------

    def _has_evidence(self) -> bool:
        return any(rec.get("success") for rec in self._executed)

    async def _chat(self, system: str, user_text: str) -> str:
        try:
            res = await asyncio.wait_for(
                self._llm.chat(
                    system_prompt=system,
                    user_text=user_text,
                    history=[],
                    remember=False,
                ),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[tool_agent] LLM 超时（90s）")
            return ""
        if res.get("error"):
            logger.warning(f"[tool_agent] LLM 错误: {res['error']}")
            return ""
        return str(res.get("response") or "")

    async def _progress(self, percent: int, stage: str, detail: str) -> None:
        if self._on_progress is None:
            return
        try:
            await self._on_progress(int(percent), stage, detail)
        except Exception:  # noqa: BLE001
            pass


def _last_error(executed: List[Dict[str, Any]]) -> str:
    for rec in reversed(executed):
        if rec.get("error"):
            return str(rec["error"])[:200]
    return ""


__all__ = ["ToolAgent"]
