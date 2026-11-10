# ============================================================
# persona/learning/procedure.py — 程序性记忆（轨迹 → 三段式流程）
# ============================================================
# 原料：coding_sessions / coding_steps（已经落盘，不新增采集）
# 产出：when_to_use / steps / done_when / avoid
#
# 设计取舍（参考 Skill-Pro / ExpeL）：
#   - 成功轨迹给 steps，失败轨迹给 avoid；avoid 往往比正面步骤更可迁移
#   - 不做 LLM 反思（P2 才接），这里全规则，零成本、可复现
#   - signature 用任务类型（P1b 简化版，不引入 E/S/C 分档）
# ============================================================

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

MAX_STEPS = 8
MAX_AVOID = 3
MAX_TEXT = 80
# 只保留少量侦察/查资料步骤，避免它们挤掉"改文件 / 验证"这些关键步骤
MAX_RECON_STEPS = 3
_CORE_TOOLS = {
    "os_create_file", "os_edit_file", "code_run_tests", "os_run_command",
}

# 状态分档阈值（0 低 / 1 中 / 2 高）：
#   E = energy（精力，越高越适合长任务）
#   S = social_need（社交需求，越高越容易分心去互动）
#   C = curiosity（好奇，越高越倾向探索而非复用旧路径）
TIER_LOW = 30
TIER_HIGH = 65
DEFAULT_STATE = (60.0, 50.0, 50.0)

_SIG_RE = re.compile(
    r"^编码\|(?P<type>[^|]+)\|E(?P<e>[0-2])\|S(?P<s>[0-2])\|C(?P<c>[0-2])$")

_TASK_TYPES = [
    ("fix_bug", ("修复", "报错", "错误", "bug", "崩溃", "不工作", "跑不起来", "失败")),
    ("capability_domain", ("能力域", "capability", "插件", "工具域", "domain")),
    ("test", ("测试", "验证", "跑通", "pytest", "单测")),
    ("refactor", ("重构", "整理", "拆分", "结构优化")),
    ("add_feature", ("新增", "添加", "实现", "增加", "支持", "功能")),
]

_TYPE_CN = {
    "fix_bug": "修复缺陷",
    "capability_domain": "新建能力域",
    "test": "补测试/验证",
    "refactor": "重构整理",
    "add_feature": "新增功能",
    "general": "通用编码",
}


def classify(goal: str) -> str:
    """按目标里的动作词判定任务类型（顺序即优先级）"""
    body = (goal or "").lower()
    for task_type, words in _TASK_TYPES:
        if any(w in body for w in words):
            return task_type
    return "general"


def task_label(task_type: str) -> str:
    return _TYPE_CN.get(task_type, _TYPE_CN["general"])


def tier(value: Any) -> int:
    """资源值 → 0/1/2 档（<30 / <65 / ≥65）"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 50.0
    if v < TIER_LOW:
        return 0
    if v < TIER_HIGH:
        return 1
    return 2


def tiers_of(energy: Any = 60.0, social_need: Any = 50.0,
             curiosity: Any = 50.0):
    """把三项资源压成 (E档, S档, C档)"""
    return tier(energy), tier(social_need), tier(curiosity)


def build_signature(task_type: str, energy: Any = 60.0,
                    social_need: Any = 50.0, curiosity: Any = 50.0) -> str:
    """流程签名：编码|任务类型|E档|S档|C档（与设计文档示例一致）"""
    e, s, c = tiers_of(energy, social_need, curiosity)
    return f"编码|{task_label(task_type)}|E{e}|S{s}|C{c}"


def tier_distance(signature: str, state_values=None) -> float:
    """签名与当前状态的档位距离（E/C 权重 1.0，S 权重 0.5）"""
    match = _SIG_RE.match(signature or "")
    if not match:
        return 5.0
    e, s, c = tiers_of(*(state_values or DEFAULT_STATE))
    return (abs(int(match.group("e")) - e)
            + 0.5 * abs(int(match.group("s")) - s)
            + abs(int(match.group("c")) - c))


def _hint(args_digest: str) -> str:
    """从 args_digest（JSON 字符串）里抽一个短提示，方便人读"""
    raw = (args_digest or "").strip()
    if not raw:
        return ""
    try:
        args = json.loads(raw)
    except Exception:  # noqa: BLE001
        return re.sub(r"\s+", " ", raw)[:40]
    if not isinstance(args, dict):
        return re.sub(r"\s+", " ", raw)[:40]
    for key in ("path", "file", "filename", "command", "query", "pattern"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value[:60]
    return ""


def _step_line(step: Dict[str, Any]) -> str:
    tool = step.get("tool") or "?"
    hint = _hint(step.get("args_digest") or "")
    return f"{tool} {hint}".strip()


def extract(session: Dict[str, Any], steps: List[Dict[str, Any]],
            recon_rounds: int = 3, state_values=None) -> Dict[str, Any]:
    """把一次编码会话压成候选流程。

    Args:
        state_values: (energy, social_need, curiosity)，用于生成 E/S/C 分档签名

    Returns:
        {signature, when_to_use, steps, avoid, done_when, success}
    """
    goal = (session.get("goal") or "").strip()
    task_type = classify(goal)
    energy, social_need, curiosity = state_values or DEFAULT_STATE
    signature = build_signature(task_type, energy, social_need, curiosity)
    success = (session.get("status") == "done")
    steps = steps or []

    # ---- 成功步骤：按发生顺序去重；侦察/查资料最多留 3 条 ----
    out_steps: List[str] = []
    seen = set()
    recon_steps = 0
    for step in steps:
        if not step.get("success") or not step.get("tool"):
            continue
        tool = step["tool"]
        if tool not in _CORE_TOOLS:
            if recon_steps >= MAX_RECON_STEPS:
                continue
            recon_steps += 1
        key = (tool, _hint(step.get("args_digest") or "")[:40])
        if key in seen:
            continue
        seen.add(key)
        out_steps.append(_step_line(step))
        if len(out_steps) >= MAX_STEPS:
            break

    # ---- 失败教训 ----
    avoid: List[str] = []
    seen_avoid = set()
    for step in steps:
        if step.get("success"):
            continue
        detail = re.sub(r"\s+", " ", (step.get("detail") or "")).strip()
        if not detail or detail.lower() in ("ok", "none"):
            continue
        line = f"{step.get('tool') or '?'} 失败：{detail[:MAX_TEXT]}"
        if line in seen_avoid:
            continue
        seen_avoid.add(line)
        avoid.append(line)
        if len(avoid) >= MAX_AVOID:
            break

    rounds = int(session.get("rounds") or 0)
    edited = any(s.get("tool") in ("os_create_file", "os_edit_file") for s in steps)
    if rounds > recon_rounds and not edited:
        avoid.append(f"不要连续侦察超过 {recon_rounds} 轮仍不写文件")
    final_error = (session.get("final_error") or "").strip()
    if final_error and not success:
        line = f"上次最终错误：{final_error[:MAX_TEXT]}"
        if line not in seen_avoid:
            avoid.append(line)

    # ---- 完成标准 ----
    verify_tool = next((s.get("tool") for s in steps
                        if s.get("success") and s.get("tool") in
                        ("code_run_tests", "os_run_command")), None)
    acceptance = (session.get("acceptance") or "").strip()
    if verify_tool:
        done_when = f"{verify_tool} 返回成功；验收：{acceptance or '目标可运行'}"
    else:
        done_when = f"验证命令返回成功；验收：{acceptance or '目标可运行'}"

    when_to_use = f"{_TYPE_CN[task_type]}类任务"
    if goal:
        when_to_use += f"（示例目标：{goal[:50]}）"

    return {
        "signature": signature,
        "when_to_use": when_to_use,
        "steps": out_steps,
        "avoid": avoid[:MAX_AVOID],
        "done_when": done_when[:200],
        "success": success,
    }


def render_block(proc: Dict[str, Any]) -> str:
    """把一条流程渲染成注入 PLAN 的文本"""
    status = proc.get("status") or "candidate"
    title = ("【上次成功路径（同类任务）】" if status == "active"
             else "【上次的路径（候选，供参考）】")
    lines = [title, f"- 适用：{proc.get('when_to_use') or ''}"]

    steps = proc.get("steps") or []
    if steps:
        lines.append("- 步骤：")
        for i, step in enumerate(steps, 1):
            lines.append(f"  {i}. {step}")
    else:
        lines.append("- 步骤：（上次没有成功的步骤记录）")

    if proc.get("done_when"):
        lines.append(f"- 完成标准：{proc['done_when']}")

    avoid = proc.get("avoid") or []
    if avoid:
        lines.append("- 已知坑（务必避免）：")
        for item in avoid:
            lines.append(f"  - {item}")

    lines.append("- 这是你之前的经验，不是硬性脚本；当前仓库结构不同就按实际情况调整。")
    return "\n".join(lines)


__all__ = ["classify", "task_label", "tier", "tiers_of", "build_signature",
           "tier_distance", "extract", "render_block"]
