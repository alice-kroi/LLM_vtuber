# ============================================================
# LLM_v2/domains/mcp_bridge/mcp_bridge.py — MCP 桥接能力域
# ============================================================
# 把外部 MCP 服务器的工具注册进 DomainAssembly，使它们和本地能力域
# 出现在同一张工具表里：
#   - stdio: command + args（本地进程）
#   - http : Streamable HTTP MCP 端点
#
# 命名：<prefix>_<remote_tool>，默认 prefix 取服务器名（dsh-xxx → dsh）。
# 远程工具调用返回统一结构 {success, result, text?, error?}。
#
# 配置：config/mcp_servers.json（可用 MCP_SERVERS_CONFIG 覆盖）
# {
#   "servers": [
#     {"name": "dsh-memory", "transport": "stdio",
#      "command": "node", "args": ["..."], "prefix": "dsh",
#      "timeout": 60, "enabled": true}
#   ]
# }
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType, tool_schema
from LLM_v2.domains.mcp_bridge.mcp_bridge_tools import (
    MCP_BRIDGE_MANAGEMENT_TOOLS,
    MCP_BRIDGE_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG = _ROOT / "config" / "mcp_servers.json"


def _sanitize(value: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", str(value or "").lower()).strip("_")
    if not s:
        s = "mcp"
    if not s[0].isalpha():
        s = "mcp_" + s
    return s


class McpStdioClient:
    """最小 stdio MCP 客户端（JSON-RPC 2.0，按行分隔）。

    MCP SDK 2.0 的 stdio 传输依赖 anyio.open_process(creationflags=...)，
    当前环境 anyio 4.2 不支持该参数，所以 stdio 走这里，避免 SDK 版本冲突。
    """

    def __init__(self, spec: Dict[str, Any], timeout: float):
        self.spec = spec
        self.timeout = timeout
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._seq = 0
        self._tools: List[Dict[str, Any]] = []
        self._error = ""

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return list(self._tools)

    async def start(self) -> None:
        command = str(self.spec.get("command") or "")
        if not command:
            raise ValueError("stdio 传输缺少 command")
        args = [str(x) for x in (self.spec.get("args") or [])]
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (self.spec.get("env") or {}).items()})
        self._proc = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self.spec.get("cwd") or None,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="mcp_stdio_reader")
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name="mcp_stdio_stderr")
        await self._initialize()
        self._tools = await self._list_tools()

    async def _initialize(self) -> None:
        res = await self._request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "llm-vtuber", "version": "1.0"},
        }, timeout=self.timeout)
        if not isinstance(res, dict):
            raise RuntimeError("initialize 返回格式异常")
        await self._notify("notifications/initialized", {})

    async def _list_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            res = await self._request("tools/list", params, timeout=self.timeout)
            if not isinstance(res, dict):
                break
            tools.extend([t for t in (res.get("tools") or []) if isinstance(t, dict)])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, args: Dict[str, Any],
                        timeout: Optional[float] = None) -> Dict[str, Any]:
        try:
            res = await self._request("tools/call", {
                "name": name,
                "arguments": args or {},
            }, timeout=float(timeout or self.timeout))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        return self._normalize(res)

    async def _notify(self, method: str, params: Dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: Dict[str, Any],
                       timeout: float) -> Any:
        if self._proc is None or self._proc.returncode is not None:
            raise RuntimeError(self._error or "MCP 进程未运行")
        self._seq += 1
        req_id = self._seq
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._write({
                "jsonrpc": "2.0", "id": req_id,
                "method": method, "params": params,
            })
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f"MCP {method} 超时（{timeout:.0f}s）")
        finally:
            self._pending.pop(req_id, None)

    async def _write(self, msg: Dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP stdin 不可用")
        raw = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self._proc.stdin.write(raw)
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", errors="ignore"))
                except Exception:  # noqa: BLE001
                    continue
                req_id = msg.get("id")
                if req_id is None:
                    continue
                fut = self._pending.get(req_id)
                if fut is None or fut.done():
                    continue
                if msg.get("error"):
                    err = msg["error"]
                    detail = err.get("message") if isinstance(err, dict) else str(err)
                    fut.set_exception(RuntimeError(str(detail)))
                else:
                    fut.set_result(msg.get("result"))
        except Exception as e:  # noqa: BLE001
            self._error = str(e)
        finally:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError(self._error or "MCP 连接已关闭"))

    async def _drain_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                logger.debug(
                    f"[mcp_bridge] {self.spec.get('name')} stderr: "
                    f"{line.decode('utf-8', errors='ignore').rstrip()[:300]}")
        except Exception:  # noqa: BLE001
            pass

    async def stop(self) -> None:
        proc = self._proc
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
        self._proc = None

    @staticmethod
    def _normalize(result: Any) -> Dict[str, Any]:
        data = result if isinstance(result, dict) else {}
        is_error = bool(data.get("isError"))
        text = ""
        blocks = data.get("content") or []
        if isinstance(blocks, list):
            text = "\n".join(
                str(b.get("text") or "")
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "text")
        return {
            "success": not is_error,
            "result": data,
            "text": text,
            "error": text if is_error else None,
        }


class McpServerClient:
    """一个 MCP 服务器连接：独立任务持有 session，避免跨任务退出 cancel scope。"""

    def __init__(self, spec: Dict[str, Any]):
        self.spec = spec
        self.name = str(spec.get("name") or "")
        self.transport = str(spec.get("transport") or "stdio").lower()
        self.timeout = float(spec.get("timeout") or 60.0)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._stdio: Optional[McpStdioClient] = None
        self._error = ""
        self._tools: List[Any] = []

    @property
    def connected(self) -> bool:
        return bool(self._task and not self._task.done() and not self._error
                    and self._ready.is_set())

    @property
    def error(self) -> str:
        return self._error

    @property
    def tools(self) -> List[Any]:
        return list(self._tools)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(), name=f"mcp_client_{self.name}")
        await self._ready.wait()
        if self._error:
            raise RuntimeError(self._error)

    async def _run(self) -> None:
        try:
            if self.transport in ("http", "streamable_http", "streamable-http"):
                await self._run_http()
            else:
                await self._run_stdio()
        except Exception as e:  # noqa: BLE001
            self._error = str(e)
            logger.warning(f"[mcp_bridge] 服务器 {self.name} 连接失败: {e}")
            self._ready.set()
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if item and not item[2].done():
                    item[2].set_result({"success": False, "error": self._error})

    async def _run_stdio(self) -> None:
        self._stdio = McpStdioClient(self.spec, self.timeout)
        await self._stdio.start()
        self._tools = self._stdio.tools
        self._ready.set()
        logger.info(
            f"[mcp_bridge] {self.name} 已连接（stdio），工具 {len(self._tools)} 个")
        while not self._stop.is_set():
            item = await self._queue.get()
            if item is None:
                break
            tool_name, args, fut = item
            if fut.done():
                continue
            fut.set_result(await self._stdio.call_tool(
                tool_name, args, timeout=self.timeout))

    async def _run_http(self) -> None:
        async with AsyncExitStack() as stack:
            from mcp.client.streamable_http import streamable_http_client
            url = str(self.spec.get("url") or "")
            if not url:
                raise ValueError("http 传输缺少 url")
            streams = await stack.enter_async_context(
                streamable_http_client(url))
            read, write = streams[0], streams[1]
            from mcp import ClientSession
            session = await stack.enter_async_context(ClientSession(
                read, write, read_timeout_seconds=self.timeout))
            await session.initialize()
            listed = await session.list_tools()
            self._tools = list(getattr(listed, "tools", None) or [])
            self._ready.set()
            logger.info(
                f"[mcp_bridge] {self.name} 已连接（http），工具 {len(self._tools)} 个")
            while not self._stop.is_set():
                item = await self._queue.get()
                if item is None:
                    break
                tool_name, args, fut = item
                if fut.done():
                    continue
                try:
                    result = await session.call_tool(tool_name, args)
                    fut.set_result(self._normalize(result))
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"[mcp_bridge] {self.name}.{tool_name} 调用失败: {e}")
                    fut.set_result({"success": False, "error": str(e)})

    async def call(self, tool_name: str, arguments: Dict[str, Any],
                   timeout: Optional[float] = None) -> Dict[str, Any]:
        if not self.connected:
            return {"success": False,
                    "error": self._error or f"MCP 服务器未连接: {self.name}"}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put((tool_name, arguments or {}, fut))
        try:
            return await asyncio.wait_for(
                fut, timeout=float(timeout or self.timeout))
        except asyncio.TimeoutError:
            return {"success": False,
                    "error": f"MCP 调用超时（{timeout or self.timeout:.0f}s）"}

    async def stop(self) -> None:
        self._stop.set()
        if self._stdio is not None:
            await self._stdio.stop()
            self._stdio = None
        if self._task is not None:
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except Exception:  # noqa: BLE001
                self._task.cancel()
                try:
                    await self._task
                except Exception:  # noqa: BLE001
                    pass
        self._task = None

    @staticmethod
    def _normalize(result: Any) -> Dict[str, Any]:
        data: Any
        if hasattr(result, "model_dump"):
            try:
                data = result.model_dump(mode="json")
            except Exception:  # noqa: BLE001
                data = result.model_dump()
        else:
            data = result
        is_error = False
        text = ""
        if isinstance(data, dict):
            is_error = bool(data.get("isError"))
            blocks = data.get("content") or []
            if isinstance(blocks, list):
                text = "\n".join(
                    str(b.get("text") or "")
                    for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text")
        return {
            "success": not is_error,
            "result": data,
            "text": text,
            "error": text if is_error else None,
        }


class McpBridgeDomain(Actuator):
    """MCP 桥接：把外部 MCP 工具注册成 <prefix>_<tool>。"""

    id = "mcp_bridge"
    label = "MCP 桥接"
    prefix = "mcp_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 60.0

    def __init__(self):
        super().__init__()
        self._specs: List[Dict[str, Any]] = []
        self._clients: Dict[str, McpServerClient] = {}
        self._remote_tools: Dict[str, Dict[str, Any]] = {}
        self._errors: Dict[str, str] = {}
        self.tool_timeouts: Dict[str, float] = {
            "mcp_status": 10.0,
            "mcp_refresh": 120.0,
            "mcp_servers": 5.0,
            "mcp_call": 120.0,
        }

    # ---------- 域契约 ----------

    def allowed_names(self) -> List[str]:
        return list(MCP_BRIDGE_MANAGEMENT_TOOLS) + sorted(self._remote_tools)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(MCP_BRIDGE_MANAGEMENT_TOOLS)
        schemas = [s for s in MCP_BRIDGE_TOOLS_SCHEMA
                   if s["function"]["name"] in allowed]
        for local, info in sorted(self._remote_tools.items()):
            schemas.append(tool_schema(
                local,
                f"[MCP:{info['server']}] {info.get('description') or info['remote']}",
                info.get("parameters") or {},
                info.get("required") or [],
            ))
        return schemas

    def preflight(self) -> Optional[str]:
        try:
            import mcp  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return f"mcp 未安装: {e}"
        return None

    def system_prompt_fragment(self) -> str:
        return (
            "【MCP 桥接】外部 MCP 服务器的工具会以 <prefix>_<tool> 出现在工具表里，"
            "可直接像本地工具一样调用；mcp_status 查看连接，mcp_refresh 重连，"
            "mcp_servers 列出配置，mcp_call 是通用兜底调用。"
        )

    def central_card(self) -> Dict[str, Any]:
        connected = sum(1 for c in self._clients.values() if c.connected)
        return {
            "summary": (f"MCP 桥接：{connected}/{len(self._specs)} 个服务器已连接，"
                        f"{len(self._remote_tools)} 个远程工具"),
            "channels": [{
                "name": "MCP 远程工具",
                "tools": ["mcp_status", "mcp_servers"],
                "description": "查看服务器与远程工具清单",
            }],
        }

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._specs = self._load_specs()
        await self._connect_all()
        self._refresh_assembly()

    async def stop(self) -> None:
        await self._disconnect_all()

    # ---------- 工具 ----------

    async def mcp_status(self) -> Dict[str, Any]:
        servers = []
        for spec in self._specs:
            name = str(spec.get("name") or "")
            client = self._clients.get(name)
            servers.append({
                "name": name,
                "transport": spec.get("transport") or "stdio",
                "prefix": self._prefix_for(spec),
                "enabled": bool(spec.get("enabled", True)),
                "connected": bool(client and client.connected),
                "tool_count": len(client.tools) if client else 0,
                "error": self._errors.get(name) or (client.error if client else ""),
            })
        return {
            "success": True,
            "server_count": len(self._specs),
            "connected": sum(1 for s in servers if s["connected"]),
            "remote_tool_count": len(self._remote_tools),
            "servers": servers,
            "remote_tools": [
                {"name": k, "server": v["server"], "remote": v["remote"]}
                for k, v in sorted(self._remote_tools.items())
            ],
        }

    async def mcp_servers(self) -> Dict[str, Any]:
        return {
            "success": True,
            "config": str(self._config_path()),
            "servers": [{
                "name": s.get("name"),
                "transport": s.get("transport") or "stdio",
                "prefix": self._prefix_for(s),
                "enabled": bool(s.get("enabled", True)),
            } for s in self._specs],
        }

    async def mcp_refresh(self) -> Dict[str, Any]:
        await self._disconnect_all()
        self._specs = self._load_specs()
        await self._connect_all()
        refreshed = self._refresh_assembly()
        status = await self.mcp_status()
        status["refresh"] = refreshed
        return status

    async def mcp_call(self, server: str = "", tool: str = "",
                       arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        server = str(server or "").strip()
        tool = str(tool or "").strip()
        if not server or not tool:
            return {"success": False, "error": "缺少 server 或 tool"}
        client = self._clients.get(server)
        if client is None:
            return {"success": False, "error": f"未知 MCP 服务器: {server}"}
        return await client.call(tool, arguments or {}, timeout=120.0)

    # ---------- 动态远程工具 ----------

    def __getattr__(self, name: str):
        info = (self.__dict__.get("_remote_tools") or {}).get(name)
        if info:
            async def _call(**kwargs):
                return await self._invoke_remote(info, kwargs)
            return _call
        raise AttributeError(name)

    async def _invoke_remote(self, info: Dict[str, Any],
                             args: Dict[str, Any]) -> Dict[str, Any]:
        client = self._clients.get(info["server"])
        if client is None:
            return {"success": False,
                    "error": f"MCP 服务器未连接: {info['server']}"}
        return await client.call(
            info["remote"], args or {}, timeout=info.get("timeout") or 60.0)

    # ---------- 连接 ----------

    async def _connect_all(self) -> None:
        self._remote_tools = {}
        self._errors = {}
        self._clients = {}
        for spec in self._specs:
            name = str(spec.get("name") or "").strip()
            if not name or not spec.get("enabled", True):
                continue
            client = McpServerClient(spec)
            self._clients[name] = client
            try:
                await client.start()
                self._register_tools(spec, client)
            except Exception as e:  # noqa: BLE001
                self._errors[name] = str(e)
                logger.warning(f"[mcp_bridge] {name} 连接失败: {e}")

    async def _disconnect_all(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[mcp_bridge] 断开 {client.name} 失败: {e}")
        self._clients = {}

    def _register_tools(self, spec: Dict[str, Any],
                        client: McpServerClient) -> None:
        prefix = self._prefix_for(spec)
        timeout = float(spec.get("timeout") or 60.0)
        for tool in client.tools:
            if isinstance(tool, dict):
                remote = str(tool.get("name") or "")
                desc = str(tool.get("description") or "")
                schema = tool.get("inputSchema") or {}
            else:
                remote = str(getattr(tool, "name", "") or "")
                desc = str(getattr(tool, "description", "") or "")
                schema = getattr(tool, "inputSchema", None) or {}
            if not remote:
                continue
            local = _sanitize(f"{prefix}_{remote}")[:60]
            if local in MCP_BRIDGE_MANAGEMENT_TOOLS or local in self._remote_tools:
                base = local
                idx = 2
                while local in MCP_BRIDGE_MANAGEMENT_TOOLS or local in self._remote_tools:
                    suffix = f"_{idx}"
                    local = base[:60 - len(suffix)] + suffix
                    idx += 1
            self._remote_tools[local] = {
                "server": str(spec.get("name")),
                "remote": remote,
                "description": desc,
                "parameters": schema.get("properties") or {},
                "required": list(schema.get("required") or []),
                "timeout": timeout,
            }
            self.tool_timeouts[local] = timeout

    def _refresh_assembly(self) -> Dict[str, Any]:
        try:
            from LLM_v2.core.persona_runtime import get_runtime
            asm = getattr(get_runtime(), "assembly", None)
            if asm is None:
                return {"success": False, "error": "assembly 未就绪"}
            return asm.refresh_domain(self.id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[mcp_bridge] 刷新装配失败: {e}")
            return {"success": False, "error": str(e)}

    # ---------- 配置 ----------

    @staticmethod
    def _config_path() -> Path:
        return Path(os.getenv("MCP_SERVERS_CONFIG") or _DEFAULT_CONFIG)

    def _load_specs(self) -> List[Dict[str, Any]]:
        path = self._config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data.get("servers") if isinstance(data, dict) else data
            if not isinstance(servers, list):
                return []
            return [s for s in servers if isinstance(s, dict)]
        except FileNotFoundError:
            logger.info(f"[mcp_bridge] 未找到配置 {path}，跳过 MCP 服务器")
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[mcp_bridge] 配置读取失败 {path}: {e}")
            return []

    @staticmethod
    def _prefix_for(spec: Dict[str, Any]) -> str:
        raw = spec.get("prefix")
        if raw:
            return _sanitize(str(raw))[:20]
        name = str(spec.get("name") or "mcp")
        # dsh-memory → dsh；其余用服务器名
        first = re.split(r"[-_]", name)[0]
        return _sanitize(first or name)[:20]


def build_mcp_bridge_domain() -> McpBridgeDomain:
    return McpBridgeDomain()


__all__ = ["McpBridgeDomain", "build_mcp_bridge_domain", "McpServerClient"]
