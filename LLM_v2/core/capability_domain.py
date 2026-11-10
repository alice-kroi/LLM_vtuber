# ============================================================
# LLM_v2/core/capability_domain.py — V2 能力域契约（替换 V1 SkillWorld）
# ============================================================
# 设计原则：
#   - Domain 是被动 provider（被 OperatorBrain 调起），不继承 Brain
#   - 统一 _proxy_tool() 在基类提供，子类不重复
#   - SubprocessCapabilityDomain 与 V1 SubprocessSkillWorld 协议兼容（兼容 id 缺失）
#   - ToolCall / ToolResult / tool_schema 从 V1 tool/tool_node.py 迁入
#   - 不再保留全局 ToolRegistry（迁移到 DomainAssembly.call_tool 统一调度）
# ============================================================

from __future__ import annotations

import asyncio
import enum
import json
import logging
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)


# ---------- 能力类型枚举（H-2 三型抽象）----------

class CapabilityType(enum.Enum):
    """V2 Capability 三型分类（按生命周期模式）
    
    RECEIVER     - 只接受信息：无工具，emit 事件
    TRANSCEIVER  - 收发一体：接收外部事件 + 提供工具
    ACTUATOR     - 只使用能力：只提供工具，无 inbound 事件
    """
    RECEIVER = "receiver"
    TRANSCEIVER = "transceiver"
    ACTUATOR = "actuator"


# ---------- 工具调用契约（从 V1 tool_node.py 迁入）----------

class ToolCall(TypedDict, total=False):
    """工具调用定义"""
    tool_call_id: str
    arguments: Dict[str, Any]


class ToolResult(TypedDict, total=False):
    """工具执行结果"""
    tool_call_id: str
    tool_name: str
    result: Optional[Any]
    error: Optional[str]


def tool_schema(
    name: str,
    description: str,
    params: Optional[Dict[str, Dict[str, Any]]] = None,
    required: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """声明式工具 Schema（少写样板 JSON，参数与实现天然一致）。

    规则：
        - params 中带 "default" 的键视为可选参数（不进 required）
        - 必填由 required 显式声明（缺省 = 全部可选）
        - 生成的 parameters 带 additionalProperties: false（与既有 schema 一致）
    """
    props: Dict[str, Any] = {pname: dict(p) for pname, p in (params or {}).items()}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "additionalProperties": False,
                "required": list(required or []),
            },
        },
    }


# ---------- 能力域基类 ----------

class CapabilityDomain(ABC):
    """V2 能力域抽象契约（替换 V1 SkillWorld，三型分类：Receiver/Transceiver/Actuator）

    关键变化（vs V1 SkillWorld）：
        - 不再依赖 tool_registry 中心化注册表（V2 走 DomainAssembly.call_tool）
        - _proxy_tool() 由基类统一提供，子类不再各自重写
        - 所有域都强制实现 async start/stop（V1 是可选钩子）
        - H-2 三型分类（type + inbound_event_types + outbound_event_types）
    """

    id: str = ""
    label: str = ""
    prefix: str = ""
    default_enabled: bool = True
    type: CapabilityType = CapabilityType.TRANSCEIVER  # H-2: 默认收发一体

    @abstractmethod
    def allowed_names(self) -> List[str]:
        """该域允许调用的工具名集合"""
        raise NotImplementedError

    @abstractmethod
    def tools(self) -> List[Dict[str, Any]]:
        """从真实工具 Schema 过滤出本域允许的子集（懒加载，避免循环导入）"""
        raise NotImplementedError

    def system_prompt_fragment(self) -> str:
        """补充到操作脑 prompt 的域说明（可用工具 + 该域执行约定）"""
        return ""

    def central_card(self) -> Dict[str, Any]:
        """中央信息卡：只提供摘要与“详情渠道”，域内全量信息留在本域工具里。

        返回结构（均可选）：
            {"summary": "一句话摘要", "progress": "当前进展",
             "channels": [{"name": "实时局面", "tools": ["xx_status"],
                            "description": "读取完整细节"}]}
        """
        return {}

    def cache_info(self) -> Dict[str, Any]:
        """域内缓存状态信号（供中央信息/控制台/对接检查使用）。

        无本地缓存的域返回空 dict；有缓存的域建议返回：
            {"has_cache": True, "available": bool, "has_data": bool,
             "entries": int, "backend": "json", "persisted": bool,
             "updated_at": str, "last_error": str}
        """
        return {}

    def close_cache(self) -> None:
        """关闭并清空域内缓存（停用/卸载域时由装配层调用，必须幂等）"""
        return None

    def preflight(self) -> Optional[str]:
        """激活前的前置检查（依赖/配置就绪）。返回 None=可激活；返回字符串=拒绝理由。

        子类可覆盖：如检查运行时依赖/配置是否就绪，未就绪时给出明确原因，
        start_domain() 会据此拒绝启动该域。
        """
        return None

    # ---------- H-2 事件声明 ----------

    def inbound_event_types(self) -> List[str]:
        """本能力会向 bus emit 的事件类型（默认空）

        示例：
        - BilibiliCapability 返回 [EVENTS.USER_MESSAGE, EVENTS.GIFT_RECEIVED]
        - LauncherCapability 返回 []
        """
        return []

    def outbound_event_types(self) -> List[str]:
        """本能力会监听 bus 上的事件类型（默认空）"""
        return []

    def _validate_type_contract(self) -> None:
        """按 type 校验契约（在 DomainAssembly.register 时调用）"""
        if self.type == CapabilityType.RECEIVER:
            assert not self.allowed_names(), \
                f"Receiver '{self.id}' 不能声明工具（allowed_names 必须为空）"
            assert self.tools() == [], \
                f"Receiver '{self.id}' 不能暴露工具（tools() 必须返回空列表）"
            assert self.inbound_event_types(), \
                f"Receiver '{self.id}' 必须声明至少一个 emit 事件"
        elif self.type == CapabilityType.ACTUATOR:
            assert self.tools(), \
                f"Actuator '{self.id}' 必须暴露至少一个工具"
        elif self.type == CapabilityType.TRANSCEIVER:
            assert self.tools(), \
                f"Transceiver '{self.id}' 必须暴露至少一个工具"
            assert self.inbound_event_types(), \
                f"Transceiver '{self.id}' 必须声明至少一个 emit 事件"

    async def start(self) -> None:
        """启动该域（连接资源、初始化状态）。基类默认无操作"""
        pass

    async def stop(self) -> None:
        """停止该域（释放资源）。基类默认无操作"""
        pass

    # ---------- 工具代理（V2 改进：基类统一）----------

    def _proxy_tool(self, name: str) -> Callable:
        """生成该域的工具代理函数：签名 (tool_call: ToolCall) -> ToolResult。

        V2 域不再各自重写 _proxy_tool；DomainAssembly.call_tool 会调用本方法
        把代理函数绑定到全局 call_tool 入口。
        """
        async def _run(tool_call: ToolCall) -> ToolResult:
            raise NotImplementedError(
                f"[{self.id}] 域 '{name}' 未实现代理调用（子进程域应继承 "
                f"SubprocessCapabilityDomain 或重写 _proxy_tool）"
            )
        return _run

    def __repr__(self) -> str:
        return f"<CapabilityDomain id={self.id!r} label={self.label!r}>"


# ---------- 子进程能力域基类（替换 V1 SubprocessSkillWorld）----------

class SubprocessCapabilityDomain(CapabilityDomain):
    """在独立子进程中执行工具的能力域基类。

    V1 SubprocessSkillWorld 行为对齐：
        - 子进程从 stdin/stdout 读行式 JSON
        - 工具项 {"method": tool_name, "params": {...}}，回 {"result":...} 或 {"error":...}
        - V2 改进：兼容"不回 id"（pvz 旧引擎）的宽容模式

    子类需实现：
        - launch_args() -> List[str]  子进程启动参数（指向引擎入口脚本）
        - async def _run_child_method(method, params) -> dict  实际调用
    """

    python_interp: str = sys.executable
    lazy_start: bool = True   # 默认懒启动（首次工具调用才 spawn）

    call_timeout: float = 300.0
    tool_timeouts: Dict[str, float] = {}   # per-tool 超时覆盖

    def __init__(self):
        self._proc: Optional[subprocess.Process] = None
        self._call_lock: Optional[asyncio.Lock] = None
        self._req_seq: int = 0
        self._handshake_done: bool = False   # 是否已与子进程握手成功
        self._engine_returns_id: bool = False  # 引擎是否在应答里回 id（握手时探测）

    def launch_args(self) -> List[str]:
        """子进程启动参数（子类实现，通常指向 <dir>/<id>_engine.py）"""
        raise NotImplementedError

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """实际执行单个工具调用。子类通常委托给引擎的 _run_child_method 实现"""
        raise NotImplementedError

    # ---------- 子进程生命周期 ----------

    async def _spawn(self) -> None:
        """拉起子进程（stdin/stdout 行式 JSON）"""
        proc = await asyncio.create_subprocess_exec(
            self.python_interp, *self.launch_args(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not proc.stdin or not proc.stdout:
            raise RuntimeError("子进程通道未建立")
        self._proc = proc
        logger.info(f"[{self.id}] 子进程已启动: pid={proc.pid}")

    async def _respawn(self) -> None:
        """重建子进程：丢弃旧管道里的一切残留响应，恢复行协议到干净状态"""
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
        self._handshake_done = False
        self._engine_returns_id = False
        await self._spawn()

    async def _call_child(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """向子进程发一条 JSON 行并取回结果（含协议自愈 + 懒启动 + 握手 + id 兼容）。

        V2 改进 vs V1：
            1. 启动时主动握手：发 {"method":"__handshake__","id":0}，期望子进程回 {"ready":true,...}
               - 握手成功 → 后续严格 id 匹配
               - 握手失败（子进程不回 id 或回格式不符）→ 切到宽容模式
            2. 协议宽容模式（兼容 pvz 不回 id）：首条合法 JSON 即采纳
            3. spawn 后端口被覆盖保护：spawn 内不重建锁/序号
        """
        # 并发锁/序号惰性只建一次（getattr 与 setattr 之间无 await，事件循环单线程下原子）
        lock = getattr(self, "_call_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._call_lock = lock
            self._req_seq = 0

        async with lock:
            if getattr(self, "_proc", None) is None or self._proc.returncode is not None:
                await self._respawn()

            # 启动握手：仅在 spawn 后首次调用时执行
            if not self._handshake_done:
                try:
                    self._req_seq += 1
                    hs_id = self._req_seq
                    self._proc.stdin.write(
                        (json.dumps({"id": hs_id, "method": "__handshake__",
                                     "params": {}}) + "\n").encode("utf-8")
                    )
                    await self._proc.stdin.drain()
                    # 1 秒内期望握手回包
                    deadline = time.monotonic() + 1.0
                    while time.monotonic() < deadline:
                        try:
                            raw = await asyncio.wait_for(
                                self._proc.stdout.readline(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        if not raw:
                            break
                        try:
                            obj = json.loads(raw)
                        except Exception:
                            continue
                        # 记录引擎是否回 id（探测协议兼容）
                        if obj.get("id") is not None:
                            self._engine_returns_id = True
                        # 子进程回了 ready → 握手成功
                        if obj.get("ready") or obj.get("result", {}).get("ready"):
                            self._handshake_done = True
                            break
                except Exception as e:
                    logger.debug(f"[{self.id}] 握手异常，按宽容模式继续: {e}")
                # 不论握手成败，都继续后续工具调用（兼容旧协议）

            # 发送实际请求
            self._req_seq += 1
            rid = self._req_seq
            payload = json.dumps(
                {"id": rid, "method": method, "params": params or {}},
                ensure_ascii=False,
            )
            try:
                self._proc.stdin.write((payload + "\n").encode("utf-8"))
                await self._proc.stdin.drain()
            except Exception as e:
                await self._respawn()
                raise RuntimeError(f"[{self.id}] 子进程写失败，已重建: {e}")

            # 工具级超时：tool_timeouts[method] > call_timeout
            timeout = float(self.call_timeout)
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
                        self._proc.stdout.readline(),
                        timeout=min(remaining, 1.0),
                    )
                except asyncio.TimeoutError:
                    continue
                if not raw:  # EOF / 子进程退出
                    await self._respawn()
                    raise RuntimeError(f"[{self.id}] 子进程 EOF，已重建")
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                # 严格模式（引擎回了 id）：回 id 则严格匹配
                if self._engine_returns_id:
                    if obj.get("id") == rid:
                        return obj
                    # id 不匹配 → 残留的滞后响应，丢弃继续读
                    continue
                # 宽容模式（引擎不回 id，如 pvz）：首条合法 JSON 即采纳
                return obj

    # ---------- 工具代理（V2 改进：基类统一）----------

    def _proxy_tool(self, name: str) -> Callable:
        """生成转发到子进程的代理函数（基类实现，子类无需重写）"""
        async def _run(tool_call: ToolCall) -> ToolResult:
            tool_call_id = tool_call.get("tool_call_id", "")
            arguments = tool_call.get("arguments", {})
            result = await self._call_child(name, arguments)
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name=name,
                result=result,
            )
        return _run

    async def start(self) -> None:
        """启动子进程域。lazy_start=True 时不立刻 spawn（首次 _call_child 自动 respawn）"""
        if self.lazy_start:
            logger.info(f"[{self.id}] 懒启动（首次工具调用时拉起子进程）")
            return
        await self._spawn()

    async def stop(self) -> None:
        """停止子进程：先发 __shutdown__，terminate + 有界 wait，超时 kill 兜底"""
        if getattr(self, "_proc", None) and self._proc.returncode is None:
            try:
                self._proc.stdin.write(
                    b'{"method":"__shutdown__","params":{}}\n'
                )
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
                logger.warning(f"[{self.id}] 子进程未在 5s 内退出，强制 kill")
                try:
                    self._proc.kill()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3)
                except Exception:
                    pass
        self._handshake_done = False


# ---------- H-2 三型子类 ----------

class Receiver(CapabilityDomain):
    """只接受信息：无工具，仅 emit 事件（生命周期：建监听 → 跑后台 → 关闭）

    V2 实例：
        - 未来的 KeyListener（全局快捷键监听）
        - 未来的 FileWatcher（文件变更监听）
    """
    type = CapabilityType.RECEIVER

    def allowed_names(self) -> List[str]:
        return []

    def tools(self) -> List[Dict[str, Any]]:
        return []


class Transceiver(CapabilityDomain):
    """收发一体：接收外部事件 + 提供工具（最常用类型）

    V2 实例：
        - BilibiliCapability（监听弹幕 + 发弹幕工具）
        - WebUICapability（WS 双向 + REST 输入）
        - DesktopCapability（operator 调感知工具 → emit PERCEPTION_UPDATED + 动作工具）
    """
    type = CapabilityType.TRANSCEIVER


class Actuator(CapabilityDomain):
    """只使用能力：只提供工具，无 inbound 事件（生命周期：注册工具 → 执行）

    V2 实例：
        - LauncherCapability（MuMu/ALAS/MAA 启动）
        - DesktopAgentCapability（Win32 调用）
        - UIPerceptionCapability（OCR/UIA 调用）
    """
    type = CapabilityType.ACTUATOR


__all__ = [
    'ToolCall',
    'ToolResult',
    'tool_schema',
    'CapabilityType',
    'CapabilityDomain',
    'SubprocessCapabilityDomain',
    'Receiver',
    'Transceiver',
    'Actuator',
]