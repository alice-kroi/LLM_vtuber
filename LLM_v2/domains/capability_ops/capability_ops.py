# ============================================================
# LLM_v2/domains/capability_ops/capability_ops.py — 自主能力管理域
# ============================================================
# 定位：AI 给自己"加能力"的管理器，不是框架层拦截器。
#   - 全部逻辑在本域内部实现，不改 DomainAssembly / EventBus / 脑逻辑
#   - 复用现有扩展点：域自动发现、assembly.rediscover/stop_domain、事件总线
#   - 准入与熔断的原则来自 MUSE-Autoskill（生命周期）与
#     OpenClaw Superpowers（静态扫描 + 沙箱 + 观察期 + 自动弃用）
#
# 链路：
#   编程任务完成(TASK_DONE) → 发现 LLM_v2/domains/<id>/ →
#   G1 静态扫描 → G2 子进程导入 → R0/R1 自动挂载（R2/R3 拒绝）
#   运行调用(DOMAIN_TOOL_CALLED) → 统计 → 连续失败/低成功率/超预算 → 停用回滚
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.domains.capability_ops import capability_ops_impl as impl
from LLM_v2.domains.capability_ops.capability_ops_tools import (
    CAPABILITY_TOOL_NAMES,
    CAPABILITY_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)

_TOOL_TIMEOUTS = {
    "cap_list": 15.0,
    "cap_verify": 90.0,
    "cap_admit": 150.0,
    "cap_suspend": 60.0,
    "cap_resume": 120.0,
    "cap_call": 120.0,
    "cap_fix": 15.0,
}

_PROMPT = """可用工具（自主能力管理）：
- cap_list: 查看自己生成的能力、风险等级、成功率与熔断状态
- cap_verify: 只验证（静态扫描 + 子进程导入），不挂载
- cap_admit: 验证并自动挂载（R0/R1 自动；R2/R3 需人工）
- cap_suspend / cap_resume: 手动熔断 / 恢复
- cap_call: 经管理域调用自生成能力（会检查熔断状态）
- cap_fix: 人工确认固定能力（fixed，不再自动降级）

新增能力的目录约定（必须遵守，否则会被闸门拒绝）：
LLM_v2/domains/<id>/
  ├── __init__.py
  ├── <id>.py            # 必须提供 build_<id>_domain() 工厂，返回 CapabilityDomain
  ├── <id>_tools.py      # 工具 Schema（tool_schema）
  └── manifest.json      # {"id","label","version","risk","smoke"}
                         # smoke 可选：{"<tool>": {参数}}，用于 G3 真调用

验证分三道闸门：
  G1 静态扫描（禁用 eval/exec/__import__、识别联网/写盘/密钥）
  G2 子进程导入（构造域实例，不接触主进程）
  G3 冒烟调用（在子进程中真跑一次工具）：
     - manifest.smoke 里写了的工具，用给定参数跑
     - 有 manifest 且风险为 R0、必填参数都有默认值时，自动用默认值跑
     - 其余跳过（避免用瞎编参数触发写操作/联网；没有 manifest 的既有域一律不跑）
工具必须返回 dict 且包含 success 字段，否则 G3 失败。

风险分级：R0=只读本地、R1=写本地/跑命令、R2=联网只读、R3=凭据/高副作用。
R0-R2 由系统自行判定并自动挂载；只有 R3 需要人工确认。
禁止：eval/exec/__import__、读取 .env 或环境变量中的密钥。
写完新能力后直接调用 cap_verify；通过后调用 cap_admit 挂载。
"""


class CapabilityOpsDomain(Actuator):
    """自主能力管理域（Actuator：只暴露工具，同时监听内部事件）"""

    id = impl.SELF_ID
    label = "自主能力管理"
    prefix = "cap_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 150.0
    tool_timeouts = dict(_TOOL_TIMEOUTS)

    def __init__(self):
        super().__init__()
        self.bus = None  # 由 DomainAssembly.start_domain 注入

    # ---------- 工具 ----------

    def allowed_names(self) -> List[str]:
        return list(CAPABILITY_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in CAPABILITY_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _PROMPT

    def preflight(self) -> Optional[str]:
        try:
            import sys
            import subprocess  # noqa: F401
            _ = sys.executable
        except Exception as e:  # noqa: BLE001
            return f"子进程验证能力不可用: {e}"
        if not impl.DOMAINS_ROOT.is_dir():
            return f"域目录不存在: {impl.DOMAINS_ROOT}"
        return None

    # ---------- 工具实现 ----------

    def cap_list(self) -> Dict[str, Any]:
        return impl.list_capabilities()

    def cap_verify(self, path: str) -> Dict[str, Any]:
        return impl.verify_capability(path)

    async def cap_admit(self, path: str, force: bool = False,
                        source: str = "explicit") -> Dict[str, Any]:
        return await impl.admit_capability(path, force=force, source=source)

    async def cap_suspend(self, id: str, reason: str = "manual") -> Dict[str, Any]:
        return await impl.suspend_capability(id, reason)

    async def cap_resume(self, id: str) -> Dict[str, Any]:
        return await impl.resume_capability(id)

    async def cap_fix(self, id: str, fixed: bool = True) -> Dict[str, Any]:
        """人工确认固定 / 取消固定（fixed 不参与自动降级）"""
        return impl.set_stage(id, "fixed" if fixed else "derived",
                              note="manual_fix" if fixed else "manual_unfix")

    async def cap_call(self, tool: str,
                       arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await impl.call_capability(tool, arguments)

    # ---------- 生命周期：订阅事件（不新增事件类型） ----------

    async def start(self) -> None:
        from LLM_v2.events import EVENTS
        if self.bus is not None:
            self.bus.on(EVENTS.TASK_DONE, self._on_task_done)
            self.bus.on(EVENTS.DOMAIN_TOOL_CALLED, self._on_tool_called)
            logger.info("[capability_ops] 已订阅 TASK_DONE / DOMAIN_TOOL_CALLED")
        # 启动时执行登记表：未准入的自生成能力不得随装配自动生效
        try:
            actions = await impl.enforce_registry()
            if actions:
                logger.info(f"[capability_ops] 启动核查：{actions}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[capability_ops] 启动核查失败（不影响主流程）: {e}")
        logger.info("[capability_ops] started")

    async def stop(self) -> None:
        from LLM_v2.events import EVENTS
        if self.bus is not None:
            for et, fn in ((EVENTS.TASK_DONE, self._on_task_done),
                           (EVENTS.DOMAIN_TOOL_CALLED, self._on_tool_called)):
                try:
                    self.bus.off(et, fn)
                except Exception:  # noqa: BLE001
                    pass
        logger.info("[capability_ops] stopped")

    # ---------- 事件处理 ----------

    async def _on_task_done(self, payload: Any) -> None:
        """编程任务成功后自动准入新生成的能力域（R0/R1 免点击）"""
        try:
            result = (payload or {}).get("result") or {}
            if not result.get("success"):
                return
            outcomes = await impl.auto_admit_from_result(result)
            for o in outcomes:
                logger.info(f"[capability_ops] 自动准入结果: {o.get('id')} "
                            f"{o.get('status')} {o.get('risk', '')}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[capability_ops] 自动准入异常（不影响主流程）: {e}")

    async def _on_tool_called(self, payload: Any) -> None:
        """统计自生成能力的调用结果，触发熔断"""
        try:
            p = payload or {}
            domain_id = p.get("domain") or ""
            if not domain_id:
                return
            reason = impl.note_tool_result(
                domain_id, p.get("tool", ""),
                bool(p.get("success")), int(p.get("elapsed_ms", 0) or 0))
            if reason:
                await impl.suspend_capability(domain_id, reason)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[capability_ops] 熔断检查异常（不影响主流程）: {e}")


def build_capability_ops_domain() -> CapabilityOpsDomain:
    return CapabilityOpsDomain()


__all__ = ["CapabilityOpsDomain", "build_capability_ops_domain"]
