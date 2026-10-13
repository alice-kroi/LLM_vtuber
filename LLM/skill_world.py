#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
能力域契约（SkillWorld）与装配层（SkillWorldAssembly）—— 对齐可提 WorldDefinition/WorldAssembly

设计目标（对应 docs/world_extension_adaptation.md 的 P0）：
把"操作脑"从单一扁平白名单，升级为可热插拔、多能力域并存的架构：

- SkillWorld：一个能力域 = id/label/default_enabled + tools()/allowed_names()/
  system_prompt_fragment()，外加可选 start()/stop() 生命周期钩子（P2 用）。
- SkillWorldAssembly：登记各域、做工具名冲突(toolClash)检测、按启用开关聚合
  激活域，把"工具 Schema + prompt"聚合给操作脑（run_operator）消费。

P0 只落"契约 + 装配"，不引入前缀强制校验（留 P1）、不引入子进程隔离（留 P2）。
默认只挂 desktop 域，聚合结果与现有 OPERATOR_TOOL_NAMES 白名单一致，保证零回归。
"""

from __future__ import annotations

import logging
import json
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _assign_deep(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
    """就地深合并：叶子替换，沿途对象身份保持（对齐可提 persist 语义）。"""
    for k, v in patch.items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _assign_deep(target[k], v)
        else:
            target[k] = v


# ---------- 能力域契约 ----------

class SkillWorld:
    """一个能力域的抽象契约。子类实现各能力包（游戏/桌面/浏览器等）。"""

    id: str = ""
    label: str = ""
    # 域工具名统一前缀（P1 的 dom_ 前缀校验用）。P0 阶段可选但建议填。
    prefix: str = ""
    default_enabled: bool = True

    def allowed_names(self) -> List[str]:
        """该域允许调用的工具名集合（白名单语义）。"""
        raise NotImplementedError

    def system_prompt_fragment(self) -> str:
        """补充到操作脑 prompt 的域说明（可用工具 + 该域执行约定）。"""
        return ""

    def preflight(self) -> Optional[str]:
        """激活前的前置检查（对齐可提 preflight）。返回 None=可激活；返回字符串=拒绝理由。

        子类可覆盖：如检查运行时依赖/配置是否就绪，未就绪时给出明确原因，
        start_world() 会据此拒绝启动该域。
        """
        return None

    def tools(self) -> List[dict]:
        """从真实工具 Schema 过滤出本域允许的子集（懒加载，避免模块间循环导入）。"""
        raise NotImplementedError

    # P2：可选生命周期钩子（热插拔时启停资源）
    async def start(self) -> None:  # pragma: no cover
        pass

    async def stop(self) -> None:  # pragma: no cover
        pass


# ---------- 装配层 ----------

class SkillWorldAssembly:
    """
    装配层：登记能力域 → 冲突检测 → 按启用开关聚合激活域。

    - registry: id → SkillWorld
    - active:   已激活的域 id 集合
    - reserved: 主播脑 / Core 保留的工具名（占位即拒绝，对应可提 reservedToolNames）
    """

    def __init__(self, reserved_names: Optional[List[str]] = None,
                 config_path: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        self._registry: Dict[str, SkillWorld] = {}
        self._active: set = set()
        self._started: set = set()          # P2-1：已启动生命周期的域
        self._reserved: set = set(reserved_names or [])
        # 聚合查询缓存（扩展性能：激活域变更时才重建，避免每次调用全量重建）
        self._cache: Dict[str, Any] = {}
        self._config_path: Optional[str] = None
        # P2-3：域级配置文件（skill_worlds.json 风格，敏感/域级开关放这里）
        self._config: Dict[str, Any] = config or {}
        if config_path:
            self.load_config(config_path)

    def _invalidate(self) -> None:
        """域挂载/启停变化时清空聚合缓存（下次查询重建）。"""
        self._cache.clear()

    @property
    def reserved(self) -> set:
        return self._reserved

    # P2-3：域级配置文件。期望结构 { "<id>": {"enabled": bool, ...其它域配置} }。
    def load_config(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"skill_worlds 配置必须是 JSON 对象: {path}")
        self._config = {**(self._config), **data}
        self._config_path = path
        logger.info(f"[SkillWorld] 已加载域级配置: {path}")

    def world_config(self, world_id: str) -> Dict[str, Any]:
        return self._config.get(world_id, {})

    def persist_world_config(self, world_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """域配置热重载：深合并 patch 进 worlds.<id> 并写回 skill_worlds.json（对齐可提 persist）。

        支持运行时可改的域参数（如 call_timeout、实例列表、房间号），
        写回后经 restart_world() 让域按新配置重建。
        """
        section = self._config.setdefault(world_id, {})
        _assign_deep(section, patch)
        if self._config_path:
            try:
                with open(self._config_path, "w", encoding="utf-8") as f:
                    json.dump(self._config, f, ensure_ascii=False, indent=2)
            except Exception as e:
                return {"success": False, "error": f"写回配置失败: {e}"}
        self._invalidate()
        return {"success": True, "world": world_id, "config": section}

    def set_world_enabled(self, world_id: str, enabled: bool) -> Dict[str, Any]:
        """运行时开关域并写回配置（重启保留）。activate/deactivate 本身仅改内存。"""
        if world_id not in self._registry:
            raise ValueError(f"未知能力域: {world_id}")
        if enabled:
            self.activate(world_id)
        else:
            self.deactivate(world_id)
        return self.persist_world_config(world_id, {"enabled": bool(enabled)})

    async def restart_world(self, world_id: str) -> None:
        """热重启一个域：停掉旧实例后按最新配置重新启动（保留激活语义）。"""
        if world_id not in self._registry:
            raise ValueError(f"未知能力域: {world_id}")
        was_active = world_id in self._active
        await self.stop_world(world_id)
        if was_active:
            await self.start_world(world_id)

    def register_world(self, world: SkillWorld) -> None:
        """登记一个能力域。校验工具名冲突（toolClash）与保留字占用，冲突则拒绝挂载。"""
        if not world.id:
            raise ValueError("SkillWorld 必须声明 id")
        names = world.allowed_names()
        exists = {}
        for other in self._registry.values():
            for n in other.allowed_names():
                exists.setdefault(n, other.id)
        clash = [n for n in names if n in exists]
        if clash:
            raise ValueError(
                f"工具名与 {[exists[n] for n in clash][0]} 冲突，拒绝挂载: {clash}"
            )
        reserves = [n for n in names if n in self._reserved]
        if reserves:
            raise ValueError(f"工具名已被 Core/主播脑保留，拒绝挂载: {reserves}")
        # 冲突检测用 allowed_names；更严格的前缀校验（P1 预留）在此扩展
        self._registry[world.id] = world
        # 启用开关优先级：文件配置 > 环境变量 > default_enabled
        if self._world_enabled(world):
            self._active.add(world.id)
            logger.info(f"[SkillWorld] 已挂载并激活 {world.label}（{world.id}）")
        else:
            logger.info(f"[SkillWorld] 已挂载但未激活（{world.id} 未启用）")
        self._invalidate()

    def _world_enabled(self, world: SkillWorld) -> bool:
        cfg = self._config.get(world.id)
        if isinstance(cfg, dict) and "enabled" in cfg:
            return bool(cfg["enabled"])
        env_key = f"SKILL_WORLD_{world.id.upper()}_ENABLED"
        v = os.environ.get(env_key)
        return v != "0" if v is not None else world.default_enabled

    def activate(self, world_id: str) -> None:
        """热启用一个已挂载的域（工具集写入聚合；若该域需生命周期，调用方另走 start_world）。"""
        if world_id not in self._registry:
            raise ValueError(f"未知能力域: {world_id}")
        self._active.add(world_id)
        self._invalidate()
        logger.info(f"[SkillWorld] 已启用 {world_id}")

    def deactivate(self, world_id: str) -> None:
        """热停用一个域。工具集与 prompt 立即随之聚合消失。"""
        if world_id in self._active:
            self._active.discard(world_id)
            self._started.discard(world_id)   # 停用连带生命周期标记清除
            self._invalidate()
            logger.info(f"[SkillWorld] 已停用 {world_id}")

    # ---------- P2-1：域级生命周期钩子（start/stop 资源启停） ----------

    async def start_world(self, world_id: str) -> None:
        """激活并调用该域 start()（资源/连接启动）。可重复调用取幂。"""
        self.activate(world_id)
        world = self._registry[world_id]
        # 把域配置注入世界实例（call_timeout 等运行参数在 _spawn 时读取）
        world.world_config = self.world_config(world_id)
        # 前置检查：域可声明"未就绪则拒绝启动"（对齐可提 preflight）
        reason = world.preflight()
        if reason:
            self.deactivate(world_id)
            raise ValueError(f"域 {world_id} 前置检查未通过: {reason}")
        if world_id not in self._started:
            await world.start()
            self._started.add(world_id)
            logger.info(f"[SkillWorld] 生命周期 start: {world_id}")

    async def stop_world(self, world_id: str) -> None:
        """调用该域 stop() 并停用。可重复调用取幂。"""
        if world_id not in self._registry:
            raise ValueError(f"未知能力域: {world_id}")
        if world_id in self._started:
            await self._registry[world_id].stop()
            self._started.discard(world_id)
            logger.info(f"[SkillWorld] 生命周期 stop: {world_id}")
        self.deactivate(world_id)

    async def start_all(self) -> None:
        """启动所有激活域的生命周期钩子（应用启动时调用一次）；单域失败不中断其他域。"""
        for wid in list(self._active):
            try:
                await self.start_world(wid)
            except Exception as e:
                logger.warning(f"[SkillWorld] 域 {wid} 生命周期启动失败（不影响其他域）: {e}")

    async def stop_all(self) -> None:
        """停用全部域并调用 stop()（应用退出/热停用）；单域失败不中断其他域。"""
        for wid in list(self._active):
            try:
                await self.stop_world(wid)
            except Exception as e:
                logger.warning(f"[SkillWorld] 域 {wid} 生命周期停止失败（继续处理其他域）: {e}")

    # ---------- 聚合查询（供操作脑消费） ----------

    def enabled_worlds(self) -> List[SkillWorld]:
        return [self._registry[i] for i in self._active if i in self._registry]

    def active_tool_names(self) -> List[str]:
        if "names" in self._cache:
            return self._cache["names"]
        names: List[str] = []
        for w in self.enabled_worlds():
            for n in w.allowed_names():
                if n not in names:
                    names.append(n)
        self._cache["names"] = names
        return names

    def active_tool_schemas(self) -> List[dict]:
        if "schemas" in self._cache:
            return self._cache["schemas"]
        schemas: List[dict] = []
        for w in self.enabled_worlds():
            for s in w.tools():
                schemas.append(s)
        self._cache["schemas"] = schemas
        return schemas

    def build_system_prompt(self, base: str = "") -> str:
        key = ("prompt", base)
        if key in self._cache:
            return self._cache[key]
        fragments = [f for w in self.enabled_worlds() if (f := w.system_prompt_fragment())]
        out = base + "\n".join(fragments) if fragments else base
        self._cache[key] = out
        return out


# ---------- desktop 域：封装现有电脑操作工具 ----------

# 与旧 operator_agent.OPERATOR_TOOL_NAMES 完全一致（保证零回归）
_DESKTOP_TOOL_NAMES = [
    "os_open_path", "os_list_directory", "os_list_windows", "os_activate_window",
    "os_minimize_window", "os_show_desktop", "os_click_xy", "os_type_text",
    "os_press_key", "os_scroll", "ui_list_elements", "vision_click",
    "os_check", "os_capture_window", "os_open_file", "os_find_file",
    "os_browser_search",
]

_DESKTOP_PROMPT = """可用工具：
- os_open_path: 系统级打开"我的电脑/盘符/文件夹/URL/应用"
- os_list_directory: 读取目录下的文件夹/文件列表
- os_list_windows / os_activate_window: 列窗口 / 激活窗口
- os_minimize_window / os_show_desktop: 最小化遮挡窗口 / 一键显示桌面
- os_click_xy / os_type_text / os_press_key / os_scroll: 键鼠操作
- ui_list_elements: UI Automation 读标准控件真实坐标
- vision_click: 视觉定位点击（webview/非标准界面用）
- os_capture_window: 截取指定窗口内容，观察界面现状
- os_check: 断言式核验（验证操作结果，只报差异）
- os_open_file: 用默认程序打开指定文件（文档/图片/程序）
- os_find_file: 按文件名关键词递归搜索文件位置
- os_browser_search: 打开浏览器搜索关键词（受控 Edge，可继续操作）

执行原则：
1. 先盘点再动手：先 os_list_windows 看桌面遮挡/可用窗口。
2. 开目录/程序优先 os_open_path（系统Shell最稳），别模拟 win+r。
3. 读"某盘/文件夹里有什么"用 os_list_directory，不要全靠截图猜。
4. 发现目标被遮挡/找不到 → os_minimize_window 或 os_show_desktop 清理后重试。
5. 点标准控件用 ui_list_elements 拿坐标 → os_click_xy；webview/游戏用 vision_click。
6. **关键步骤用 os_check 验证结果**（窗口/文件/控件/画面断言），不成立就修正重试，
   绝不假装成功；os_check 失败回执含差异原因，据此换招或换目标。
7. 需要观察界面现状用 os_capture_window（只截指定窗口，避免全屏无关信息）。
"""


class DesktopWorld(SkillWorld):
    """桌面/电脑操作能力域：封装现有全部 os_/ui_/vision_click 工具。"""

    id = "desktop"
    label = "桌面电脑操作"
    prefix = "os_"
    default_enabled = True

    def allowed_names(self) -> List[str]:
        return list(_DESKTOP_TOOL_NAMES)

    def system_prompt_fragment(self) -> str:
        return _DESKTOP_PROMPT

    def tools(self) -> List[dict]:
        from tool.computer_use import COMPUTER_USE_TOOLS_SCHEMA
        return [s for s in COMPUTER_USE_TOOLS_SCHEMA
                if s["function"]["name"] in self.allowed_names()]


# ---------- P2-2：子进程隔离脚手架 ----------
# 重资源域（如重量级游戏）跑独立子进程，父进程维护代理工具，避免拖垮直播主循环。
# 协议：子进程从 stdin/stdout 读行式 JSON，工具项为 {"method": tool_name, "params": {...}}，
#       子进程回 {"result": ...} 或 {"error": ...}。父进程把该域的每个工具 Agent 化转发。

import asyncio
import subprocess
import sys
import time


class SubprocessSkillWorld(SkillWorld):
    """在独立子进程中执行工具的能力域基类。

    子类只需实现 launch_args()（返回 argv）与 parse_line()（解析子进程输出行）。
    工具在父进程侧通过 _proxy_tool(name) 转发到子进程执行。
    """

    python_interp: str = sys.executable

    # 子进程启动参数（由子类提供，通常指向从 stdin 读 JSON 的入口脚本）
    def launch_args(self) -> List[str]:
        raise NotImplementedError

    async def _spawn(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            self.python_interp, *self.launch_args(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not self._proc.stdin or not self._proc.stdout:
            raise RuntimeError("子进程通道未建立")
        self._call_lock = asyncio.Lock()                 # 并发写读串行化
        self._req_seq = 0                                # 自增请求 id
        # 每域 call_timeout：worlds.<id>.call_timeout（配置）> 类属性 call_timeout > 默认 300
        self._call_timeout = float(
            getattr(self, "world_config", {}).get(
                "call_timeout", getattr(self, "call_timeout", 300.0)))

    async def _respawn(self) -> None:
        """重建子进程：丢弃旧管道里的一切残留响应，恢复行协议到干净状态。"""
        proc, self._proc = getattr(self, "_proc", None), None
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                pass
        await self._spawn()

    async def _call_child(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """向子进程发一条 JSON 行并取回结果（含协议自愈）。

        每次自带递增 id；读响应时丢弃 id 不匹配的残留行与解析失败的行；
        超时/EOF/写失败则自动重建子进程后报错（下一调用即基于干净通道）。
        """
        self._req_seq += 1
        rid = self._req_seq
        payload = json.dumps({"id": rid, "method": method,
                              "params": params or {}}, ensure_ascii=False)
        async with self._call_lock:
            if getattr(self, "_proc", None) is None or self._proc.returncode is not None:
                await self._respawn()
            try:
                self._proc.stdin.write((payload + "\n").encode("utf-8"))
                await self._proc.stdin.drain()
            except Exception as e:
                await self._respawn()
                raise RuntimeError(f"[{self.id}] 子进程写失败，已重建: {e}")
            # 工具级超时：tool_timeouts[method] > 域 call_timeout
            timeout = self._call_timeout
            tmap = getattr(self, "tool_timeouts", None)
            if tmap and method in tmap:
                timeout = float(tmap[method])
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    await self._respawn()
                    raise RuntimeError(f"[{self.id}] 未取回 id={rid} 应答，已重建")
                try:
                    raw = await asyncio.wait_for(
                        self._proc.stdout.readline(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    continue
                if not raw:  # EOF / 子进程退出
                    await self._respawn()
                    raise RuntimeError(f"[{self.id}] 子进程 EOF，已重建")
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue  # 跳过受污染的残留行
                # 引擎回 id 则严格匹配；不回 id（如旧版 pvz）则取首条合法 JSON
                if obj.get("id") is None or obj.get("id") == rid:
                    return obj
                # id 不匹配 → 残留的滞后响应，丢弃继续读
                continue

    def _proxy_tool(self, name: str):
        """生成一个转发到子进程的代理工具函数（signature 与 tool_registry 兼容）。"""
        async def _run(**params):
            try:
                return await self._call_child(name, params)
            except Exception as e:
                return {"success": False, "error": str(e)}
        return _run

    def async_main(self, config: Dict[str, Any]) -> None:
        """子进程入口主循环：逐行读 JSON、调用本地同步/异步工具、回写结果。

        子类应同步实现 _run_child_method(method, params) 返回可 JSON 化的 dict。"""
        raise NotImplementedError

    # 生命周期：start/stop 管理子进程启停
    async def start(self) -> None:
        await self._spawn()

    async def stop(self) -> None:
        """停止子进程：先发 __shutdown__，terminate + 有界 wait，超时 kill 兜底。"""
        if getattr(self, "_proc", None) and self._proc.returncode is None:
            try:
                self._proc.stdin.write(b'{"method":"__shutdown__","params":{}}\n')
                await self._proc.stdin.drain()
            except Exception:
                pass
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning(f"[SkillWorld] {self.id} 子进程未在 5s 内退出，强制 kill")
                try:
                    self._proc.kill()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3)
                except Exception:
                    pass


# ---------- 全局单例 + 默认挂载 desktop 域 ----------

# 主播脑工具名保留给装配层（桌面域工具不与它们冲突；后续域也不许占用）
_STREAMER_RESERVED = [
    "web_search", "fetch_webpage", "browser_snapshot", "browser_navigate",
    "browser_click", "browser_type", "browser_press_key", "browser_scroll",
    "browser_tabs", "vision_analyze", "list_windows", "tts_synthesize",
    "play_audio",
]

skill_assembly = SkillWorldAssembly(reserved_names=_STREAMER_RESERVED)

# 域级配置文件（P2-3）：skill_worlds.json 提供各域启用与部署参数（文件缺失不阻塞）
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill_worlds.json")
try:
    skill_assembly.load_config(_config_path)
except Exception as e:  # pragma: no cover
    logger.warning(f"[SkillWorld] 域级配置加载失败（用默认启用态兜底）: {e}")

skill_assembly.register_world(DesktopWorld())


def _discover_domain_worlds() -> List[SkillWorld]:
    """自动发现能力域：扫描 LLM/<domain>/<domain>_world.py，实例化其工厂。

    约定（对齐可提"丢目录即挂载"）：域目录名 = 域 id，目录下须有
    <domain>_world.py，且提供 build_<domain>_world() 工厂函数返回 SkillWorld。
    desktop 域为内建（无目录），不在此列。
    """
    import importlib
    worlds: List[SkillWorld] = []
    base = os.path.dirname(os.path.abspath(__file__))
    for entry in sorted(os.listdir(base)):
        d = os.path.join(base, entry)
        if not os.path.isdir(d) or entry.startswith("__") or entry.startswith("."):
            continue
        if not os.path.exists(os.path.join(d, f"{entry}_world.py")):
            continue
        try:
            mod = importlib.import_module(f"LLM.{entry}.{entry}_world")
            build = getattr(mod, f"build_{entry}_world", None)
            if not callable(build):
                logger.warning(f"[SkillWorld] 自动发现 {entry}：缺工厂函数 build_{entry}_world()")
                continue
            w = build()
            worlds.append(w)
            logger.info(f"[SkillWorld] 自动发现能力域: {entry}（{w.label}）")
        except Exception as e:  # pragma: no cover
            logger.warning(f"[SkillWorld] 自动发现 {entry} 失败（跳过）: {e}")
    return worlds


# 自动发现域目录挂载：新增能力域只需在 LLM/ 放 <id>/<id>_world.py + 工厂函数，
# 无需再改本文件（对齐可提 WorldAssembly 动态挂载）。
for _discovered in _discover_domain_worlds():
    try:
        skill_assembly.register_world(_discovered)
    except Exception as e:  # pragma: no cover
        logger.warning(f"[SkillWorld] 域 {_discovered.id} 挂载失败（不影响其它域）: {e}")