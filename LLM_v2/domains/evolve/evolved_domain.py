# ============================================================
# LLM_v2/domains/evolve/evolved_domain.py — 自进化工具域（通用加载器）
# ============================================================
# 每次 evolve_add 都会生成一个独立能力域目录：
#   LLM_v2/domains/evo_<name>/
#     evo_<name>.py   ← 极简工厂，返回本类实例
#     manifest.json   ← 域元信息（evolved=true）
#     tool.json       ← 工具 schema
#     handler.py      ← AI 写的 run(**kwargs)
#
# 本类负责加载 handler、把 run 暴露成域工具、并在 stop 时卸载。
# 代码与主进程同权限（可信自用环境），与 dsh-evolve 的信任模型一致。
# ============================================================

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    Actuator,
    CapabilityType,
    tool_schema,
)

logger = logging.getLogger(__name__)


class EvToolDomain(Actuator):
    """由 AI 生成、可热挂载/卸载的单工具能力域。"""

    type = CapabilityType.ACTUATOR

    def __init__(self, domain_dir: str | Path):
        super().__init__()
        self._dir = Path(domain_dir).resolve()
        manifest = self._read_json(self._dir / "manifest.json")
        tool = self._read_json(self._dir / "tool.json")

        self.id = str(manifest.get("id") or self._dir.name)
        self.label = str(manifest.get("label") or self.id)
        self.default_enabled = True
        self.tool_name = str(tool.get("name") or "")
        self.description = str(tool.get("description") or "")
        self.parameters = tool.get("parameters") or {}
        self.required = list(tool.get("required") or [])
        self.timeout = float(tool.get("timeout") or 30.0)
        self.prefix = (self.tool_name.split("_")[0] + "_") if "_" in self.tool_name else "evo_"
        self.tool_timeouts = {self.tool_name: self.timeout}

        self._handler_path = self._dir / "handler.py"
        self._module_name = f"llm_v2_evo_{self.id}_handler"
        self._handler = None
        self._load_error = ""

    # ---------- 域契约 ----------

    def allowed_names(self) -> List[str]:
        return [self.tool_name] if self.tool_name else []

    def tools(self) -> List[Dict[str, Any]]:
        if not self.tool_name:
            return []
        return [tool_schema(
            self.tool_name,
            self.description or f"{self.label}（AI 自进化工具）",
            self.parameters,
            self.required,
        )]

    def preflight(self) -> Optional[str]:
        if not self.tool_name:
            return "tool.json 缺少 name"
        if not self._handler_path.is_file():
            return f"handler.py 不存在: {self._handler_path}"
        return None

    def system_prompt_fragment(self) -> str:
        return (
            f"【自进化工具】{self.tool_name}（{self.label}）："
            f"{self.description}。参数：{', '.join(self.parameters) or '无'}。"
        )

    def central_card(self) -> Dict[str, Any]:
        return {"summary": f"自进化工具 {self.tool_name}：{self.description[:60]}"}

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._load_handler()

    async def stop(self) -> None:
        self._handler = None
        if self._module_name in sys.modules:
            sys.modules.pop(self._module_name, None)

    # ---------- 动态工具入口 ----------

    def __getattr__(self, name: str):
        # assembly 用 getattr(domain, tool_name) 找工具；动态工具名不在类属性里
        if name == self.tool_name and name != "":
            return self._invoke
        raise AttributeError(name)

    async def _invoke(self, **kwargs: Any) -> Dict[str, Any]:
        if self._handler is None:
            try:
                self._load_handler()
            except Exception as e:  # noqa: BLE001
                return {"success": False, "error": f"handler 加载失败: {e}"}
        run = getattr(self._handler, "run", None)
        if not callable(run):
            return {"success": False, "error": "handler.py 缺少可调用的 run(**kwargs)"}
        try:
            if inspect.iscoroutinefunction(run):
                result = await asyncio.wait_for(run(**kwargs), timeout=self.timeout)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(run, **kwargs), timeout=self.timeout)
        except asyncio.TimeoutError:
            return {"success": False, "error": f"工具超时（{self.timeout:.0f}s）"}
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[evolve] {self.tool_name} 执行异常: {e}")
            return {"success": False, "error": str(e)}

        if isinstance(result, dict):
            result.setdefault("success", True)
            return result
        return {"success": True, "result": result}

    # ---------- 内部 ----------

    def _load_handler(self) -> None:
        if self._handler is not None:
            return
        spec = importlib.util.spec_from_file_location(
            self._module_name, str(self._handler_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 handler: {self._handler_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[self._module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(self._module_name, None)
            raise
        if not callable(getattr(module, "run", None)):
            sys.modules.pop(self._module_name, None)
            raise RuntimeError("handler.py 必须定义 run(**kwargs)")
        self._handler = module
        self._load_error = ""

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}


def build_evolved_domain(domain_dir: str | Path) -> EvToolDomain:
    return EvToolDomain(domain_dir)


__all__ = ["EvToolDomain", "build_evolved_domain"]
