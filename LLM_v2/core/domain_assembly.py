# ============================================================
# LLM_v2/core/domain_assembly.py — V2 能力域装配层（替换 V1 SkillWorldAssembly）
# ============================================================
# 职责：
#   1. 登记 CapabilityDomain 实例（register：toolClash / reserved 校验）
#   2. 激活/停用域（activate/deactivate：惰性缓存聚合）
#   3. 生命周期（start_domain/stop_domain：preflight + 域 start/stop）
#   4. 状态跟踪（_missing/_degraded：构造失败、运行期降级）
#   5. 统一调度入口（call_tool：唯一工具执行入口，反向索引 _name_to_domain）
#   6. 配置热重载（load_config/persist_world_config + restart_domain）
#   7. 自动发现（_discover_domains / rediscover：扫描 LLM_v2/domains/<id>/ 热插拔）
#
# 与 V1 SkillWorldAssembly 的差异：
#   - 强制注入 runtime（bus 派生自 runtime.bus），无需外部传 bus
#   - 域状态变化 emit DOMAIN_* 事件到 bus（WebUI 可订阅）
#   - 反向索引 _name_to_domain: dict 避免 call_tool O(N) 查找
#   - 单例 domain_assembly 由模块顶层创建，挂 runtime.assembly
# ============================================================

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from LLM_v2.core.capability_domain import (
    CapabilityDomain,
    SubprocessCapabilityDomain,
    ToolCall,
)
from LLM_v2.core.event_bus import EventBus

logger = logging.getLogger(__name__)


# ---------- 装配层核心 ----------

class DomainAssembly:
    """V2 能力域装配层（替换 V1 SkillWorldAssembly）。

    设计参考 V1，但做了 4 项关键改进：
        1. 强制 runtime 注入（构造时拿 bus，避免散落各处的 bus 参数）
        2. 反向索引 _name_to_domain（call_tool O(1) 而非 O(N)）
        3. 域状态变化 emit DOMAIN_* 事件（WebUI 可观察）
        4. 单例由模块顶层创建，挂 runtime.assembly（不再全局裸 export）
    """

    def __init__(
        self,
        *,
        runtime: Any,                      # PersonaRuntime（避免循环 import 用 Any）
        reserved: Optional[Iterable[str]] = None,
        config_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._runtime = runtime
        self._bus: EventBus = runtime.bus
        self._registry: Dict[str, CapabilityDomain] = {}
        self._active: Set[str] = set()
        self._started: Set[str] = set()        # 已 start() 的域
        self._reserved: Set[str] = set(reserved or [])
        self._cache: Dict[str, Any] = {}       # 聚合查询缓存
        self._missing: List[Dict[str, Any]] = []     # 构造/挂载失败
        self._degraded: Dict[str, str] = {}          # 运行期聚合降级
        self._name_to_domain: Dict[str, CapabilityDomain] = {}   # 反向索引
        # H-2 事件路由
        self._event_producers: Dict[str, str] = {}           # event_type -> 能力 id
        self._event_consumers: Dict[str, List[str]] = {}     # event_type -> [能力 ids]
        self._config: Dict[str, Any] = config or {}
        self._config_path: Optional[str] = None
        if config_path:
            self.load_config(config_path)

    # ---------- 派生属性 ----------

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def reserved(self) -> Set[str]:
        return self._reserved

    # ---------- 内部辅助 ----------

    def _invalidate(self) -> None:
        """域挂载/启停变化时清空聚合缓存（下次查询重建）"""
        self._cache.clear()
        # 不清 _degraded：让历史降级记录保留到下次显式清理

    def _record_missing(self, domain_id: str, label: str, stage: str, reason: str) -> None:
        """记录构造/挂载失败的域；同一 (id, stage) 去重"""
        if not any(m["id"] == domain_id and m["stage"] == stage for m in self._missing):
            entry = {"id": domain_id, "label": label, "stage": stage, "reason": reason}
            self._missing.append(entry)
            self._emit_safe(
                "__domain_missing__",
                {"domain": domain_id, "label": label, "stage": stage, "reason": reason},
            )

    def _quarantine(self, domain: CapabilityDomain, stage: str, error: Exception) -> None:
        """运行期某域在聚合阶段抛错：只降级丢该域，其余域与主进程照常（不向上抛）"""
        self._degraded[domain.id] = f"{stage}: {error}"
        logger.warning(
            f"[DomainAssembly] 域 {domain.id} 在 {stage} 阶段失败，"
            f"已跳过该域（不影响其他域）: {error}"
        )
        self._emit_safe(
            "__domain_degraded__",
            {"domain": domain.id, "stage": stage, "error": str(error)},
        )

    def _emit_safe(self, event_type_fallback: str, payload: Dict[str, Any]) -> None:
        """同步 fire-and-forget emit（使用 emit_nowait 不需要运行中的事件循环）。

        失败静默（不影响主流程）：事件总线通常在装配阶段尚未启动，
        但只要 bus.queue 已创建（PersonaRuntime 构造时即创建），emit_nowait 就能入队。
        """
        try:
            from LLM_v2.events import EVENTS
            event_map = {
                "__domain_registered__": EVENTS.DOMAIN_REGISTERED,
                "__domain_started__": EVENTS.DOMAIN_STARTED,
                "__domain_stopped__": EVENTS.DOMAIN_STOPPED,
                "__domain_degraded__": EVENTS.DOMAIN_DEGRADED,
                "__domain_missing__": EVENTS.DOMAIN_MISSING,
                "__domain_tool_called__": EVENTS.DOMAIN_TOOL_CALLED,
            }
            et = event_map.get(event_type_fallback, event_type_fallback)
        except Exception:
            et = event_type_fallback
        try:
            self._bus.emit_nowait(et, payload)
        except Exception as e:
            logger.debug(f"[DomainAssembly] emit {et} 失败（不影响主流程）: {e}")

    # ---------- 配置加载/持久化 ----------

    def load_config(self, path: str) -> None:
        """加载域级配置 { "<id>": {"enabled": bool, ...} }"""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"domains 配置必须是 JSON 对象: {path}")
            self._config = {**(self._config), **data}
            self._config_path = path
            logger.info(f"[DomainAssembly] 已加载域配置: {path}")
        except Exception as e:
            logger.warning(f"[DomainAssembly] 域配置加载失败（用默认启用态）: {e}")

    def world_config(self, domain_id: str) -> Dict[str, Any]:
        return self._config.get(domain_id, {})

    def persist_world_config(self, domain_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """域配置热重载：深合并 patch 进 config.<id> 并写回"""
        section = self._config.setdefault(domain_id, {})
        self._assign_deep(section, patch)
        if self._config_path:
            try:
                with open(self._config_path, "w", encoding="utf-8") as f:
                    json.dump(self._config, f, ensure_ascii=False, indent=2)
            except Exception as e:
                return {"success": False, "error": f"写回配置失败: {e}"}
        self._invalidate()
        return {"success": True, "domain": domain_id, "config": section}

    @staticmethod
    def _assign_deep(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
        for k, v in patch.items():
            if v is None:
                continue
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                DomainAssembly._assign_deep(target[k], v)
            else:
                target[k] = v

    # ---------- 注册/激活/停用 ----------

    def register(self, domain: CapabilityDomain) -> None:
        """登记一个能力域。校验工具名冲突（toolClash）与保留字占用"""
        if not domain.id:
            raise ValueError("CapabilityDomain 必须声明 id")
        try:
            names = domain.allowed_names()
        except Exception as e:
            self._record_missing(domain.id or "<no-id>", domain.label, "allowed_names", str(e))
            raise

        # H-2: 三型契约校验
        try:
            domain._validate_type_contract()
        except AssertionError as e:
            self._record_missing(domain.id, domain.label, "type_contract", str(e))
            raise ValueError(f"域 {domain.id} 契约违反: {e}")

        # 冲突检测
        exists: Dict[str, str] = {}
        for other in self._registry.values():
            for n in other.allowed_names():
                exists.setdefault(n, other.id)
        clash = [n for n in names if n in exists]
        if clash:
            other_id = exists[clash[0]]
            self._record_missing(
                domain.id, domain.label, "tool_clash", f"与 {other_id} 撞名: {clash}"
            )
            raise ValueError(f"工具名与 {other_id} 冲突，拒绝挂载: {clash}")

        # 保留字占用
        reserves = [n for n in names if n in self._reserved]
        if reserves:
            self._record_missing(
                domain.id, domain.label, "reserved", f"被保留: {reserves}"
            )
            raise ValueError(f"工具名已被 Core/主播脑保留，拒绝挂载: {reserves}")

        self._registry[domain.id] = domain
        # 反向索引填表
        for n in names:
            self._name_to_domain[n] = domain

        # H-2: 事件路由表登记
        from LLM_v2.core.capability_domain import CapabilityType
        for et in domain.inbound_event_types():
            # H-2 修复：所有类型都登记；同一事件允许多源 emit
            # （如 perception_updated 可由 desktop/browser/pvz 都 emit）
            self._event_producers[et] = domain.id
        for et in domain.outbound_event_types():
            self._event_consumers.setdefault(et, []).append(domain.id)

        # 启用开关优先级：文件配置 > 环境变量 > default_enabled
        if self._domain_enabled(domain):
            self._active.add(domain.id)
            logger.info(f"[DomainAssembly] 已挂载并激活 {domain.label}（{domain.id}）")
        else:
            logger.info(
                f"[DomainAssembly] 已挂载但未激活（{domain.id} 未启用）"
            )
        self._invalidate()
        self._push_central(domain)
        self._emit_safe(
            "__domain_registered__",
            {"id": domain.id, "label": domain.label, "enabled": domain.id in self._active},
        )

    def _push_central(self, domain: CapabilityDomain) -> None:
        """把域的摘要/渠道/缓存状态推到中央信息层（不复制域内全量数据）"""
        try:
            from LLM_v2.core.central_context import get_central
            card: Dict[str, Any] = {}
            try:
                card = domain.central_card() or {}
            except Exception:  # noqa: BLE001
                card = {}
            try:
                cache = domain.cache_info() or {}
            except Exception:  # noqa: BLE001
                cache = {}
            active = domain.id in self._active
            status = "started" if domain.id in self._started else (
                "active" if active else "inactive")
            names = list(domain.allowed_names()) if active else []
            summary = card.get("summary") or (
                f"{domain.label}：{'已启用' if active else '未启用'}，{len(names)} 个工具")
            if not active and "已停用" not in summary and "缓存已关闭" not in summary:
                summary += "（已停用"
                if cache.get("persisted"):
                    summary += "，磁盘快照保留"
                summary += "）"
            get_central().update_domain(
                domain.id,
                label=domain.label,
                summary=summary,
                channels=card.get("channels") or [],
                status=status,
                tools=names,
                progress=(card.get("progress") or None) if active else "已停用",
                cache=cache,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[DomainAssembly] 中央信息更新失败（不影响域）: {e}")

    def _domain_enabled(self, domain: CapabilityDomain) -> bool:
        cfg = self._config.get(domain.id)
        if isinstance(cfg, dict) and "enabled" in cfg:
            return bool(cfg["enabled"])
        env_key = f"DOMAIN_{domain.id.upper()}_ENABLED"
        v = os.environ.get(env_key)
        return v != "0" if v is not None else domain.default_enabled

    def unregister(self, domain_id: str) -> bool:
        """从装配层移除一个域（用于自进化卸载）。

        调用方必须先 stop_domain；本方法只清理登记/反向索引/事件路由/缓存。
        """
        domain = self._registry.pop(domain_id, None)
        if domain is None:
            return False
        try:
            names = domain.allowed_names()
        except Exception:  # noqa: BLE001
            names = []
        for name in names:
            if self._name_to_domain.get(name) is domain:
                self._name_to_domain.pop(name, None)
        self._active.discard(domain_id)
        self._started.discard(domain_id)
        for et in domain.inbound_event_types():
            if self._event_producers.get(et) == domain_id:
                self._event_producers.pop(et, None)
        for et in domain.outbound_event_types():
            ids = self._event_consumers.get(et) or []
            rest = [x for x in ids if x != domain_id]
            if rest:
                self._event_consumers[et] = rest
            else:
                self._event_consumers.pop(et, None)
        self._invalidate()
        logger.info(f"[DomainAssembly] 已移除能力域 {domain_id}")
        return True

    def refresh_domain(self, domain_id: str) -> Dict[str, Any]:
        """重建某域的工具反向索引（动态工具增删后调用）。

        MCP 桥接这类域的 allowed_names 会随远程工具变化；本方法只重排该域
        在 _name_to_domain 里的条目，并清空聚合缓存，不改变启停状态。
        """
        domain = self._registry.get(domain_id)
        if domain is None:
            return {"success": False, "error": f"未知能力域: {domain_id}"}
        for name, owner in list(self._name_to_domain.items()):
            if owner is domain:
                self._name_to_domain.pop(name, None)
        names: List[str] = []
        clash: List[str] = []
        try:
            declared = domain.allowed_names()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        for name in declared:
            other = self._name_to_domain.get(name)
            if other is not None and other is not domain:
                clash.append(name)
                continue
            self._name_to_domain[name] = domain
            names.append(name)
        self._invalidate()
        if clash:
            logger.warning(
                f"[DomainAssembly] {domain_id} 动态工具撞名已跳过: {clash}")
        logger.info(
            f"[DomainAssembly] 已刷新 {domain_id} 工具索引: {len(names)} 个")
        return {"success": True, "names": names, "clash": clash}

    def activate(self, domain_id: str) -> None:
        """热启用一个已挂载的域（工具集写入聚合）"""
        if domain_id not in self._registry:
            raise ValueError(f"未知能力域: {domain_id}")
        self._active.add(domain_id)
        self._invalidate()
        logger.info(f"[DomainAssembly] 已启用 {domain_id}")
        self._push_central(self._registry[domain_id])

    def deactivate(self, domain_id: str) -> None:
        """热停用一个域（先关闭并清空域内缓存，再更新中央信息）"""
        if domain_id in self._active:
            domain = self._registry.get(domain_id)
            if domain is not None:
                try:
                    domain.close_cache()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[DomainAssembly] 关闭 {domain_id} 缓存失败: {e}")
            self._active.discard(domain_id)
            self._started.discard(domain_id)
            self._invalidate()
            logger.info(f"[DomainAssembly] 已停用 {domain_id}")
            if domain is not None:
                self._push_central(domain)

    # ---------- 生命周期 ----------

    async def start_domain(self, domain_id: str) -> None:
        """激活并调用该域 start()（资源/连接启动）。可重复调用取幂"""
        self.activate(domain_id)
        domain = self._registry[domain_id]
        # 把域配置注入域实例（call_timeout 等运行参数在 spawn 时读取）
        domain.world_config = self.world_config(domain_id)   # type: ignore[attr-defined]
        # 注入 bus（供域自注册事件监听器，如 tts/live2d 域）
        domain.bus = self._bus                                 # type: ignore[attr-defined]
        # 前置检查
        reason = domain.preflight()
        if reason:
            self.deactivate(domain_id)
            raise ValueError(f"域 {domain_id} 前置检查未通过: {reason}")
        if domain_id not in self._started:
            await domain.start()
            self._started.add(domain_id)
            logger.info(f"[DomainAssembly] 生命周期 start: {domain_id}")
            self._push_central(domain)
            self._emit_safe(
                "__domain_started__", {"domain": domain_id},
            )

    async def stop_domain(self, domain_id: str) -> None:
        """调用该域 stop() 并停用。可重复调用取幂"""
        if domain_id not in self._registry:
            raise ValueError(f"未知能力域: {domain_id}")
        if domain_id in self._started:
            await self._registry[domain_id].stop()
            self._started.discard(domain_id)
            logger.info(f"[DomainAssembly] 生命周期 stop: {domain_id}")
            self._emit_safe(
                "__domain_stopped__", {"domain": domain_id},
            )
        self.deactivate(domain_id)

    async def start_all(self) -> None:
        """启动所有激活域的生命周期钩子；单域失败不中断其他域"""
        for wid in list(self._active):
            try:
                await self.start_domain(wid)
            except Exception as e:
                logger.warning(
                    f"[DomainAssembly] 域 {wid} 生命周期启动失败（不影响其他域）: {e}"
                )

    async def stop_all(self) -> None:
        """停用全部域并调用 stop()；单域失败不中断其他域"""
        for wid in list(self._active):
            try:
                await self.stop_domain(wid)
            except Exception as e:
                logger.warning(
                    f"[DomainAssembly] 域 {wid} 生命周期停止失败（继续处理其他域）: {e}"
                )

    async def restart_domain(self, domain_id: str) -> None:
        """热重启一个域：停掉旧实例后按最新配置重新启动（保留激活语义）"""
        if domain_id not in self._registry:
            raise ValueError(f"未知能力域: {domain_id}")
        was_active = domain_id in self._active
        await self.stop_domain(domain_id)
        if was_active:
            await self.start_domain(domain_id)

    async def set_domain_enabled(self, domain_id: str, enabled: bool) -> Dict[str, Any]:
        """运行时开关域并写回配置（重启保留）"""
        if domain_id not in self._registry:
            raise ValueError(f"未知能力域: {domain_id}")
        if enabled:
            try:
                await self.start_domain(domain_id)
            except Exception:
                self.persist_world_config(domain_id, {"enabled": False})
                raise
            return self.persist_world_config(domain_id, {"enabled": True})
        await self.stop_domain(domain_id)
        return self.persist_world_config(domain_id, {"enabled": False})

    async def rediscover(self) -> Dict[str, Any]:
        """重新发现能力域（运行期热插拔）：重扫描 domains/ 目录并挂载新增域。

        已登记域跳过（保留现有实例与运行状态）；新域走 register +（若启用）
        start_domain，与启动时 attach_assembly 的装配流程一致。
        返回全量域信号快照（含新挂载清单 / 启动失败清单）。
        """
        found: List[str] = []
        newly: List[str] = []
        start_failed: List[Dict[str, str]] = []
        for domain in _discover_domains():
            found.append(domain.id)
            if domain.id in self._registry:
                continue
            try:
                self.register(domain)
            except Exception as e:
                logger.warning(f"[DomainAssembly] 重发现挂载 {domain.id} 失败（跳过）: {e}")
                continue
            newly.append(domain.id)
            if domain.id in self._active:
                try:
                    await self.start_domain(domain.id)
                except Exception as e:
                    start_failed.append({"id": domain.id, "error": str(e)})
                    logger.warning(f"[DomainAssembly] 重发现启动 {domain.id} 失败: {e}")
        logger.info(
            f"[DomainAssembly] 重发现完成：磁盘发现 {len(found)} 域，"
            f"新挂载 {len(newly)} 域，当前登记 {len(self._registry)} 域"
        )
        return {
            "found": found,
            "newly_registered": newly,
            "start_failed": start_failed,
            **self.worlds_status(),
        }

    # ---------- 聚合查询 ----------

    def enabled_domains(self) -> List[CapabilityDomain]:
        return [self._registry[i] for i in self._active if i in self._registry]

    def active_tool_names(self) -> List[str]:
        if "names" in self._cache:
            return self._cache["names"]
        names: List[str] = []
        for d in self.enabled_domains():
            try:
                for n in d.allowed_names():
                    if n not in names:
                        names.append(n)
            except Exception as e:
                self._quarantine(d, "allowed_names", e)
        self._cache["names"] = names
        return names

    def active_tool_schemas(self) -> List[Dict[str, Any]]:
        if "schemas" in self._cache:
            return self._cache["schemas"]
        schemas: List[Dict[str, Any]] = []
        for d in self.enabled_domains():
            try:
                schemas.extend(d.tools())
            except Exception as e:
                self._quarantine(d, "tools", e)
        self._cache["schemas"] = schemas
        return schemas

    def build_system_prompt(self, base: str = "") -> str:
        """拼装 system prompt = base + 各域 fragment（单域失败只丢该域）"""
        key = ("prompt", base, frozenset(self._active))
        if key in self._cache:
            return self._cache[key]
        fragments: List[str] = []
        for d in self.enabled_domains():
            try:
                frag = d.system_prompt_fragment()
            except Exception as e:
                self._quarantine(d, "system_prompt_fragment", e)
                continue
            if frag:
                fragments.append(frag)
        out = base + "\n".join(fragments) if fragments else base
        self._cache[key] = out
        return out

    def worlds_status(self) -> Dict[str, Any]:
        """域状态快照：供 WebUI / 指令查询"""
        worlds = []
        for wid, d in self._registry.items():
            try:
                tool_count = len(d.allowed_names()) if wid in self._active else 0
            except Exception as e:
                self._quarantine(d, "allowed_names", e)
                tool_count = 0
            from LLM_v2.core.capability_domain import CapabilityType
            try:
                cache = d.cache_info() or {}
            except Exception:  # noqa: BLE001
                cache = {}
            worlds.append({
                "id": wid,
                "label": d.label,
                "type": d.type.value,
                "enabled": wid in self._active,
                "started": wid in self._started,
                "tool_count": tool_count,
                "degraded": self._degraded.get(wid, ""),
                "cache": cache,
                "inbound_events": d.inbound_event_types(),
                "outbound_events": d.outbound_event_types(),
            })
        return {
            "worlds": worlds,
            "missing": list(self._missing),
            "event_producers": dict(self._event_producers),
        }

    def domain_catalog(self) -> List[Dict[str, Any]]:
        """能力目录：供思维脑做"按需启停"决策（id/标签/工具/提示/状态）。

        与 worlds_status 的区别：这里包含未激活域的工具与提示摘要，
        用于判断"这个任务可能需要哪个当前没开的能力"。
        """
        out: List[Dict[str, Any]] = []
        domains_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for wid, d in self._registry.items():
            try:
                tools = list(d.allowed_names())
            except Exception:  # noqa: BLE001
                tools = []
            try:
                prompt = d.system_prompt_fragment() or ""
            except Exception:  # noqa: BLE001
                prompt = ""
            risk = "R1"
            try:
                manifest_path = os.path.join(
                    domains_root, "domains", wid, "manifest.json")
                with open(manifest_path, "r", encoding="utf-8") as f:
                    risk = str((json.load(f) or {}).get("risk") or "R1")
            except Exception:  # noqa: BLE001
                pass
            out.append({
                "id": wid,
                "label": d.label,
                "type": d.type.value,
                "enabled": wid in self._active,
                "started": wid in self._started,
                "default_enabled": bool(d.default_enabled),
                "risk": risk,
                "tools": tools,
                "prompt": prompt[:400],
            })
        return out

    # ---------- H-2 事件路由查询 ----------

    def event_producer(self, event_type: str) -> Optional[str]:
        """查询某事件由哪个能力产出（用于 USER_MESSAGE 等通用事件的路由）

        Returns: 能力 id 或 None（多 Receiver 共享则返回首个）
        """
        return self._event_producers.get(event_type)

    def event_consumers(self, event_type: str) -> List[str]:
        """查询某事件被哪些能力订阅"""
        return list(self._event_consumers.get(event_type, []))

    # ---------- 统一调度入口（V2 唯一工具执行入口）----------

    async def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> Dict[str, Any]:
        """统一工具调度入口（取代 V1 tool/tool_node.execute_tool）。

        流程：
            1. 反查 _name_to_domain（O(1)）→ 未找到返回 {"error": "tool_not_found"}
            2. 该域未激活 → 返回 {"error": "domain_disabled"}
            3. 子进程域 → 调 self._call_child（DomainAssembly 自动用代理函数封装）
            4. 同步域 → 丢线程池 + asyncio.wait_for 超时保护
            5. 异步域（直接是 async def） → await
            6. emit DOMAIN_TOOL_CALLED 审计事件
        """
        arguments = arguments or {}
        domain = self._name_to_domain.get(name)
        if domain is None:
            return {"success": False, "error": f"tool_not_found: {name}"}
        if domain.id not in self._active:
            return {"success": False, "error": f"domain_disabled: {domain.id}"}

        start_ts = time.monotonic()
        tool_call = ToolCall(
            tool_call_id=f"asm-{int(start_ts * 1000)}",
            arguments=arguments,
        )

        # 工具级超时：域声明的 tool_timeouts 优先于调用方默认值
        # （子进程域内部 _call_child 亦按此值等待，两层必须一致）
        declared = getattr(domain, "tool_timeouts", None) or {}
        effective_timeout = float(declared.get(name, timeout))

        try:
            # 子进程域：调 _call_child（经基类代理）
            if isinstance(domain, SubprocessCapabilityDomain):
                proxy = domain._proxy_tool(name)
                result_dict = await asyncio.wait_for(
                    proxy(tool_call),
                    timeout=effective_timeout,
                )
            else:
                # 内存域：直接调函数（同步走线程池，异步 await）
                func = getattr(domain, name, None)
                if func is None:
                    # 走基类代理
                    proxy = domain._proxy_tool(name)
                    result_dict = await asyncio.wait_for(
                        proxy(tool_call), timeout=effective_timeout,
                    )
                else:
                    if asyncio.iscoroutinefunction(func):
                        result_dict = await asyncio.wait_for(
                            func(**arguments), timeout=effective_timeout,
                        )
                    else:
                        result_dict = await asyncio.wait_for(
                            asyncio.to_thread(func, **arguments),
                            timeout=effective_timeout,
                        )

            elapsed_ms = int((time.monotonic() - start_ts) * 1000)
            # 工具自身声明的 success=False 必须被识别（否则熔断/统计/决策结果全部失真）
            inner = result_dict if isinstance(result_dict, dict) else {}
            inner_ok = inner.get("success")
            ok = True if inner_ok is None else bool(inner_ok)
            inner_error = str(inner.get("error") or "")[:500]
            # 审计（失败也吞掉）
            event = {
                "tool": name,
                "domain": domain.id,
                "elapsed_ms": elapsed_ms,
                "success": ok,
            }
            if not ok and inner_error:
                event["error"] = inner_error[:200]
            self._emit_safe("__domain_tool_called__", event)
            out = {"success": ok, "result": result_dict, "elapsed_ms": elapsed_ms}
            if not ok:
                out["error"] = inner_error or f"tool {name} returned success=false"
            return out

        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - start_ts) * 1000)
            # 超时同样要发审计事件：熔断/统计依赖它，否则超时对调用方不可见
            self._emit_safe(
                "__domain_tool_called__",
                {
                    "tool": name,
                    "domain": domain.id,
                    "elapsed_ms": elapsed_ms,
                    "success": False,
                    "error": f"timeout({effective_timeout}s)",
                },
            )
            return {
                "success": False,
                "error": f"timeout({effective_timeout}s): {name}",
                "elapsed_ms": elapsed_ms,
            }
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_ts) * 1000)
            logger.exception(f"[DomainAssembly] 工具 {name} 异常: {e}")
            self._emit_safe(
                "__domain_tool_called__",
                {
                    "tool": name,
                    "domain": domain.id,
                    "elapsed_ms": elapsed_ms,
                    "success": False,
                    "error": str(e),
                },
            )
            return {"success": False, "error": str(e), "elapsed_ms": elapsed_ms}


# ---------- 自动发现 ----------

def _discover_domains(base: str = "LLM_v2/domains") -> List[CapabilityDomain]:
    """自动发现能力域：扫描 <base>/<id>/<id>.py，调用 build_<id>_domain()。

    约定（对齐可提"丢目录即挂载"）：域目录名 = 域 id，目录下须有 <id>.py，
    且提供 build_<id>_domain() 工厂函数返回 CapabilityDomain。
    desktop 域为内建（在 __init__ 中显式构造），不在此列。
    """
    domains: List[CapabilityDomain] = []
    base_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "domains",
    )
    if not os.path.isdir(base_path):
        return domains
    for entry in sorted(os.listdir(base_path)):
        d = os.path.join(base_path, entry)
        if not os.path.isdir(d) or entry.startswith("__") or entry.startswith("."):
            continue
        module_py = os.path.join(d, f"{entry}.py")
        if not os.path.exists(module_py):
            continue
        try:
            mod = importlib.import_module(f"LLM_v2.domains.{entry}.{entry}")
            build = getattr(mod, f"build_{entry}_domain", None)
            if not callable(build):
                logger.warning(f"[DomainAssembly] 自动发现 {entry}：缺工厂函数 build_{entry}_domain()")
                continue
            domain = build()
            domains.append(domain)
            logger.info(f"[DomainAssembly] 自动发现能力域: {entry}（{domain.label}）")
        except Exception as e:
            logger.warning(f"[DomainAssembly] 自动发现 {entry} 失败（跳过）: {e}")
    return domains


# ---------- 模块级单例（lazy attach to runtime）----------

# 主播脑工具名保留给装配层（域工具不与它们冲突；后续域也不许占用）
_STREAMER_RESERVED = [
    "web_search", "fetch_webpage", "browser_snapshot", "browser_navigate",
    "browser_click", "browser_type", "browser_press_key", "browser_scroll",
    "browser_tabs", "vision_analyze", "list_windows", "tts_synthesize",
    "play_audio",
    # 域管理工具（专属，域不得占用）
    "world_list", "world_enable", "world_disable", "world_restart",
]


def attach_assembly(runtime: Any) -> DomainAssembly:
    """把 DomainAssembly 单例挂到 runtime 上（懒创建，避免 PersonaRuntime 循环 import）。

    返回已创建（或已存在）的 DomainAssembly 实例。
    """
    if hasattr(runtime, "_assembly") and runtime._assembly is not None:
        return runtime._assembly

    assembly = DomainAssembly(
        runtime=runtime,
        reserved=_STREAMER_RESERVED,
        config_path=_default_config_path(),
    )
    runtime._assembly = assembly

    # 自动发现 + 注册域（构造失败域记录到 _missing，不影响其它域）
    for domain in _discover_domains():
        try:
            assembly.register(domain)
        except Exception as e:
            logger.warning(f"[DomainAssembly] 域 {domain.id} 挂载失败（不影响其它域）: {e}")

    logger.info(
        f"[DomainAssembly] 已挂载 {len(assembly._registry)} 个域，"
        f"激活 {len(assembly._active)} 个，"
        f"失败 {len(assembly._missing)} 个"
    )
    return assembly


def _default_config_path() -> Optional[str]:
    """默认 config 路径：项目根/config/domains.json（不存在也不阻塞）"""
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    path = os.path.join(project_root, "config", "domains.json")
    if os.path.exists(path):
        return path
    # 兼容 V1 路径：LLM/skill_worlds.json
    legacy = os.path.join(project_root, "LLM", "skill_worlds.json")
    if os.path.exists(legacy):
        logger.info(f"[DomainAssembly] 使用 V1 兼容配置: {legacy}")
        return legacy
    return None


__all__ = [
    'DomainAssembly',
    'attach_assembly',
    '_discover_domains',
]