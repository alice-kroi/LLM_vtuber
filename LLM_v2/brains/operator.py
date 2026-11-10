# ============================================================
# LLM_v2/brains/operator.py — V2 操作脑（消费 runtime.assembly）
# ============================================================
# V2 改进（vs V1 / V2 phase 1）：
#   - 不再 import tool/tool_node.py，零 V1 依赖
#   - executor 走 DomainAssembly.call_tool（统一调度入口）
#   - get_tool_schemas() 直接从 assembly 聚合（恢复 schema 优先，不再用 inspect）
#   - busy 状态改为 asyncio.Queue 排队（不再拒绝新任务）
# ============================================================

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional, Dict, List

from LLM_v2.core.brain_base import Brain
from LLM_v2.events import EVENTS
from LLM_v2.io.webui import log_event as _log_event

logger = logging.getLogger(__name__)


ToolExecutor = Any  # async def executor(tool: str, args: dict) -> dict


def make_default_executor(runtime):
    """默认执行器：调用 runtime.assembly.call_tool（V2 唯一调度入口）"""
    async def executor(tool: str, args: dict) -> dict:
        try:
            result = await runtime.assembly.call_tool(tool, args or {}, timeout=60.0)
            if result.get("error"):
                return {"success": False, "error": result["error"]}
            return {
                "success": True,
                "result": result.get("result", result),
                "elapsed_ms": result.get("elapsed_ms", 0),
            }
        except Exception as e:
            logger.exception(f"[operator] 工具 {tool} 执行异常: {e}")
            return {"success": False, "error": str(e)}
    return executor


class OperatorBrain(Brain):
    """操作脑——动手能力（V2 原生：从 runtime.assembly 消费所有工具）"""

    # will_engine_action → 工具名映射（V1 兼容 + V2 扩展）
    WILL_ACTION_TO_TOOL = {
        "watch_video": "os_browser_search",
        "browse_bilibili": "os_browser_search",
        "explore_interest": "research_task",   # 自主探索 → 联网研究并写笔记
        "research_topic": "research_task",
        "code_action": "code_task",
        "play_game": "cultist_task",           # 玩密教模拟器（有界循环）
        "social_action": None,           # 不需要操作
        "rest": None,
        "open_app": "os_launch_app",
        "list_files": "os_list_directory",
        # V2 扩展：直接做更具体的任务
        "open_file": "os_open_file",
        "play_audio": "play_audio",       # V1 保留
        "speak": "tts_synthesize",         # V1 保留
    }

    def __init__(
        self,
        runtime,
        executor: Optional[ToolExecutor] = None,
        coding_llm=None,
    ):
        super().__init__(
            name="operator",
            runtime=runtime,
            heartbeat_interval=3.0,
        )
        self._executor = executor or make_default_executor(runtime)
        self._coding_llm = coding_llm          # 编码循环专用模型（懒加载）
        self._running_task = None
        self._task_history: List[Dict[str, Any]] = []  # 最近任务历史
        from asyncio import Queue
        self._queue: Queue = Queue()                    # 任务排队
        # H-2 控制台用：累计任务计数
        self._total_tasks = 0
        # 中央信息桥接：操作脑的进度直接写任务台账（问题/需求仍只读）
        self._task_store = None

    def _get_coding_llm(self):
        """编码循环用的模型（未注入时按 purpose=coding 解析）"""
        if self._coding_llm is None:
            from LLM_v2.core.llm_adapter import get_coding_llm
            self._coding_llm = get_coding_llm()
        return self._coding_llm

    async def setup(self):
        """注册监听器"""
        self.on(EVENTS.DO_TASK, self._on_do_task)
        self.on(EVENTS.WILL_ENGINE_ACTION, self._on_will_action)
        logger.info(f"[brain:{self.name}] setup complete (executor ready)")

    # ---------- 监听器实现 ----------

    async def _on_do_task(self, payload: Any):
        """接 do_task 事件：执行任务（busy 时排队）"""
        task = payload if isinstance(payload, dict) else {"description": str(payload)}
        tool = task.get("tool", "")
        args = task.get("args", {})
        description = task.get("description", tool)

        # busy 检查：入队而非拒绝（先入台账 pending，思维脑才看得到排队任务）
        if self._running_task is not None:
            logger.debug(f"[brain:{self.name}] busy, queueing task: {description}")
            self._ci_register(task, phase="pending")
            await self._queue.put(task)
            return

        await self._execute_task(task)

    async def _execute_task(self, task: Dict[str, Any]):
        """执行单个任务（含历史记录 + 事件 emit + 进度上报）"""
        await self._ci_start(task)
        # 联网研究：搜索→读页→整理→存笔记（有界多步，不是单工具调用）
        if task.get("tool") == "research_task":
            await self._execute_research_task(task)
            await self._drain_queue()
            return

        # 编程任务：走 CodingLoop（有界 ReAct），而非单工具调用
        if task.get("tool") == "code_task":
            await self._execute_code_task(task)
            await self._drain_queue()
            return

        # 密教模拟器：读局面 → 决策 → 操作，有界循环
        if task.get("tool") == "cultist_task":
            await self._execute_cultist_task(task)
            await self._drain_queue()
            return

        # 通用工具智能体：LLM 自主选域 → 规划 → 执行 → 校验
        if task.get("tool") in ("tool_agent", "agent_task"):
            await self._execute_agent_task(task)
            await self._drain_queue()
            return

        tool = task.get("tool", "")
        args = task.get("args", {})
        description = task.get("description", tool)
        steps = task.get("steps") or []   # 可选：多步骤任务 [{tool, args, description}]
        logger.info(f"[brain:{self.name}] 执行: {description} (tool={tool}, steps={len(steps)})")
        await self.emit(EVENTS.TASK_STARTED, {"task": task})

        self._running_task = task
        try:
            # 多步骤任务：逐步执行 + 每步上报进度
            if steps:
                await self._execute_steps(task, steps)
            else:
                # 单工具任务：开始 / 完成 两个进度点
                await self._emit_progress(task, 0, "executing", f"执行 {tool}")
                result = await self._executor(tool, args)
                await self._emit_progress(task, 100, "done", f"{tool} 完成")
                self._record_task(task, result)

                # H-2: 感知类工具执行后 emit PERCEPTION_UPDATED
                if self._is_perception_tool(tool) and result.get("success"):
                    await self._emit_perception(tool, result)

                await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": result})
                await self.emit(
                    EVENTS.TASK_DONE if result.get("success") else EVENTS.TASK_FAILED,
                    {"task": task, "result": result},
                )
        except Exception as e:
            logger.exception(f"[brain:{self.name}] 任务异常: {e}")
            err_result = {"success": False, "error": str(e)}
            self._record_task(task, err_result)
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": err_result})
            await self.emit(EVENTS.TASK_FAILED, {"task": task, "error": str(e)})
        finally:
            self._running_task = None

        # 队列中如有任务，继续执行
        await self._drain_queue()

    async def _drain_queue(self):
        """队列中如有任务，继续执行（一次取一个，避免递归过深）"""
        if not self._queue.empty():
            try:
                next_task = self._queue.get_nowait()
                await self._execute_task(next_task)
            except asyncio.QueueEmpty:
                pass

    async def _execute_code_task(self, task: Dict[str, Any]):
        """编程任务：交给 CodingLoop 做有界 ReAct（侦察→计划→改→验证→修正）。

        与单工具任务的区别：这里由循环内部多次调用工具，完成判定基于
        **可验证信号**（测试/脚本执行通过），而非模型自称完成。
        """
        from LLM_v2.brains.coding_loop import CodingLoop

        args = task.get("args") or {}
        goal = str(args.get("goal") or task.get("description") or "").strip()
        if not goal:
            goal = task.get("description", "完成一个编程任务")

        await self.emit(EVENTS.TASK_STARTED, {"task": task})
        logger.info(f"[brain:{self.name}] 编程任务开始: {goal[:80]}")
        _log_event("INFO", "operator", f"编程任务开始: {goal[:100]}")
        self._running_task = task

        try:
            loop = CodingLoop(
                self.runtime,
                llm=self._get_coding_llm(),
                workspace=args.get("workspace"),
                spec_id=args.get("spec_id"),
            )

            async def on_progress(percent: int, stage: str, detail: str):
                await self._emit_progress(task, percent, f"coding:{stage}", detail)

            result = await loop.run(
                goal=goal,
                acceptance=str(args.get("acceptance") or ""),
                workspace=args.get("workspace"),
                on_progress=on_progress,
            )
            self._record_task(task, result)
            if result.get("success"):
                _log_event("INFO", "operator",
                           f"编程任务完成: {result.get('rounds')} 轮 / "
                           f"改动 {len(result.get('files_changed') or [])} 个文件")
            else:
                _log_event("WARN", "operator",
                           f"编程任务未完成: {result.get('error')}")
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": result})
            await self.emit(
                EVENTS.TASK_DONE if result.get("success") else EVENTS.TASK_FAILED,
                {"task": task, "result": result},
            )
        except Exception as e:
            logger.exception(f"[brain:{self.name}] 编程任务异常: {e}")
            err = {"success": False, "error": str(e)}
            self._record_task(task, err)
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": err})
            await self.emit(EVENTS.TASK_FAILED, {"task": task, "result": err})
        finally:
            self._running_task = None

    async def _execute_research_task(self, task: Dict[str, Any]):
        """联网研究任务：搜索 → 精读 1~2 个来源 → LLM 整理 → 存学习笔记。"""
        from LLM_v2.brains.research import ResearchRunner

        args = task.get("args") or {}
        topic = str(args.get("goal") or args.get("topic") or task.get("description") or "").strip()
        if not topic:
            topic = "最近感兴趣的话题"

        await self.emit(EVENTS.TASK_STARTED, {"task": task})
        logger.info(f"[brain:{self.name}] 研究任务开始: {topic[:80]}")
        _log_event("INFO", "operator", f"联网研究开始: {topic[:100]}")
        self._running_task = task

        try:
            runner = ResearchRunner(
                self.runtime,
                llm=self._get_research_llm(),
                max_sources=int(args.get("max_sources") or 2),
            )

            async def on_progress(percent: int, stage: str, detail: str):
                await self._emit_progress(task, percent, f"research:{stage}", detail)

            result = await asyncio.wait_for(
                runner.run(topic, on_progress=on_progress),
                timeout=150.0,
            )
            self._record_task(task, result)
            if result.get("success"):
                _log_event(
                    "INFO", "operator",
                    f"研究完成: {topic[:60]}｜读 {result.get('pages_read', 0)} 个来源",
                )
            else:
                _log_event("WARN", "operator", f"研究未完成: {result.get('error')}")
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": result})
            await self.emit(
                EVENTS.TASK_DONE if result.get("success") else EVENTS.TASK_FAILED,
                {"task": task, "result": result},
            )
        except asyncio.TimeoutError:
            err = {"success": False, "error": "研究任务超时（150s）"}
            self._record_task(task, err)
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": err})
            await self.emit(EVENTS.TASK_FAILED, {"task": task, "result": err})
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[brain:{self.name}] 研究任务异常: {e}")
            err = {"success": False, "error": str(e)}
            self._record_task(task, err)
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": err})
            await self.emit(EVENTS.TASK_FAILED, {"task": task, "result": err})
        finally:
            self._running_task = None

    async def _execute_cultist_task(self, task: Dict[str, Any]):
        """密教模拟器任务：读局面 → LLM 决策 → 执行 → 再读，有界循环。"""
        from LLM_v2.brains.cultist import CultistRunner

        args = task.get("args") or {}
        goal = str(args.get("goal") or task.get("description") or "").strip()
        if not goal:
            goal = "玩一会儿密教模拟器，先活下来并推进一点主线"
        plan = args.get("plan") or []
        if plan:
            goal += "\n（子目标：" + "；".join(
                str(p.get("subgoal") or "") for p in plan
                if isinstance(p, dict)) + "）"
        max_steps = int(args.get("max_steps") or 8)

        await self.emit(EVENTS.TASK_STARTED, {"task": task})
        logger.info(f"[brain:{self.name}] 密教模拟器开始: {goal[:80]}")
        _log_event("INFO", "operator", f"密教模拟器开始: {goal[:100]}")
        self._running_task = task

        try:
            async def on_progress(percent: int, stage: str, detail: str):
                await self._emit_progress(task, percent, f"cultist:{stage}", detail)

            runner = CultistRunner(
                self.runtime,
                llm=self._get_cultist_llm(),
                max_steps=max_steps,
                on_progress=on_progress,
            )
            # 玩到结局可能需要很久（含自动启动游戏 + 多步决策）
            cultist_timeout = float(os.getenv("CULTIST_TIMEOUT_SEC") or 1800)
            result = await asyncio.wait_for(runner.run(goal),
                                            timeout=cultist_timeout)
            self._record_task(task, result)
            if result.get("success"):
                _log_event("INFO", "operator",
                           f"密教模拟器完成: {result.get('steps')} 步"
                           + (f"，结局 {result.get('ending')}"
                              if result.get("ending") else ""))
            else:
                _log_event("WARN", "operator",
                           f"密教模拟器未完成: {result.get('error')}")
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": result})
            await self.emit(
                EVENTS.TASK_DONE if result.get("success") else EVENTS.TASK_FAILED,
                {"task": task, "result": result},
            )
        except asyncio.TimeoutError:
            err = {"success": False, "error": "密教模拟器任务超时"}
            self._record_task(task, err)
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": err})
            await self.emit(EVENTS.TASK_FAILED, {"task": task, "result": err})
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[brain:{self.name}] 密教模拟器任务异常: {e}")
            err = {"success": False, "error": str(e)}
            self._record_task(task, err)
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": err})
            await self.emit(EVENTS.TASK_FAILED, {"task": task, "result": err})
        finally:
            self._running_task = None

    async def _execute_agent_task(self, task: Dict[str, Any]):
        """通用工具智能体：由 LLM 自己判断、选域、规划、执行、校验。

        与单工具任务区别：工具不是 WillEngine 写死的，而是 Agent 根据目标
        和可用工具目录自己挑；每一步结果都会回灌给规划器，未完成会继续。
        """
        from LLM_v2.brains.tool_agent import ToolAgent

        args = task.get("args") or {}
        goal = str(args.get("goal") or task.get("description") or "").strip()
        if not goal:
            goal = task.get("description", "")
        context = str(args.get("context") or "")
        plan = args.get("plan") or []
        if plan:
            context = (context + "\n\n【思维脑拆解的子目标】\n" + "\n".join(
                f"{i + 1}. {p.get('subgoal')}｜验收：{p.get('acceptance')}"
                for i, p in enumerate(plan) if isinstance(p, dict))).strip()
        ledger = self._ledger_context()
        if ledger:
            context = (context + "\n\n" + ledger).strip()

        await self.emit(EVENTS.TASK_STARTED, {"task": task})
        logger.info(f"[brain:{self.name}] 工具智能体开始: {goal[:80]}")
        _log_event("INFO", "operator", f"工具智能体开始: {goal[:100]}")
        self._running_task = task
        started = asyncio.get_event_loop().time()

        try:
            async def on_progress(percent: int, stage: str, detail: str):
                await self._emit_progress(task, percent, stage, detail)

            agent = ToolAgent(
                self.runtime,
                on_progress=on_progress,
                max_rounds=3,
                max_steps=10,
            )
            agent_result = await asyncio.wait_for(
                agent.run(goal, context=context), timeout=240.0)

            elapsed_ms = int(
                (asyncio.get_event_loop().time() - started) * 1000)
            summary = {
                "success": bool(agent_result.get("success")),
                "result": agent_result,
                "elapsed_ms": elapsed_ms,
                # 验收/台账要看得到执行方自己的说明和失败原因
                "summary": str(agent_result.get("summary") or ""),
                "error": str(agent_result.get("error") or ""),
                "steps": agent_result.get("steps") or [],
            }
            # 执行方按实际情况改写计划：台账里留下"实际怎么做的"
            steps = agent_result.get("steps") or []
            actual_plan = []
            for s in steps[:6]:
                if not isinstance(s, dict):
                    continue
                label = str(s.get("goal") or s.get("tool")
                            or s.get("description") or "").strip()
                if label:
                    actual_plan.append({"subgoal": label[:80],
                                        "acceptance": "已执行"})
            if actual_plan:
                self._ci_set_plan(task, actual_plan, "执行方按实际情况调整")
            self._record_task(task, summary)
            if summary["success"]:
                _log_event(
                    "INFO", "operator",
                    f"工具智能体完成: {len(agent_result.get('steps') or [])} 步")
            else:
                _log_event(
                    "WARN", "operator",
                    f"工具智能体未完成: {agent_result.get('error')}")
            await self.emit(EVENTS.OPERATOR_DONE, {
                "task": task, "result": summary})
            await self.emit(
                EVENTS.TASK_DONE if summary["success"] else EVENTS.TASK_FAILED,
                {"task": task, "result": summary},
            )
        except asyncio.TimeoutError:
            err = {"success": False, "error": "工具智能体超时（240s）"}
            self._record_task(task, err)
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": err})
            await self.emit(EVENTS.TASK_FAILED, {"task": task, "result": err})
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[brain:{self.name}] 工具智能体异常: {e}")
            err = {"success": False, "error": str(e)}
            self._record_task(task, err)
            await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": err})
            await self.emit(EVENTS.TASK_FAILED, {"task": task, "result": err})
        finally:
            self._running_task = None

    def _get_cultist_llm(self):
        """游戏决策用的通用模型（需要稳定的 JSON 输出）"""
        if getattr(self, "_cultist_llm", None) is None:
            try:
                from LLM_v2.core.llm_adapter import get_llm_for
                self._cultist_llm = get_llm_for("general")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[brain:{self.name}] 密教 LLM 不可用: {e}")
                self._cultist_llm = None
        return self._cultist_llm

    def _get_research_llm(self):
        """研究整理用的通用模型（不复用主播角色模型，避免话术化）"""
        if getattr(self, "_research_llm", None) is None:
            try:
                from LLM_v2.core.llm_adapter import get_llm_for
                self._research_llm = get_llm_for("general")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[brain:{self.name}] 研究 LLM 不可用: {e}")
                self._research_llm = None
        return self._research_llm

    async def _execute_steps(self, task: Dict[str, Any], steps: list):
        """多步骤任务：逐步执行，每步 emit OPERATOR_PROGRESS。

        steps 元素: {"tool": str, "args": dict, "description": str}
        步骤失败即中止（聚合结果 + error），进度停留在失败点。
        """
        results = []
        total = len(steps)
        for i, step in enumerate(steps, start=1):
            tool = step.get("tool", "")
            args = step.get("args", {})
            desc = step.get("description", tool)
            percent = int((i - 1) / total * 100)
            await self._emit_progress(
                task, percent, "step",
                f"步骤 {i}/{total}: {desc}")
            result = await self._executor(tool, args)
            results.append({"tool": tool, "result": result, "step": i})
            if not result.get("success"):
                await self._emit_progress(task, percent, "failed", f"步骤 {i} 失败")
                await self.emit(EVENTS.OPERATOR_DONE, {"task": task, "result": result,
                                                       "progress": {"step": i, "total": total}})
                await self.emit(EVENTS.TASK_FAILED, {"task": task, "result": result,
                                                     "step": i})
                return
            _log_event("INFO", "operator", f"步骤 {i}/{total} {tool}: success")

        # 全部步骤完成
        ok_all = all(r["result"].get("success") for r in results)
        self._record_task(task, {"success": ok_all, "results": results})
        await self._emit_progress(task, 100, "done", f"{total} 步全部完成")
        await self.emit(EVENTS.OPERATOR_DONE, {
            "task": task, "result": {"success": ok_all, "results": results}})
        await self.emit(
            EVENTS.TASK_DONE if ok_all else EVENTS.TASK_FAILED,
            {"task": task, "result": {"success": ok_all, "results": results}},
        )

    # ---------- 中央信息桥接（只写任务进度，不碰问题/需求）----------

    def _central_task_store(self):
        if self._task_store is None:
            try:
                from LLM_v2.domains.central_info.task_store import TaskStore
                self._task_store = TaskStore()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[brain:{self.name}] 任务台账不可用: {e}")
                return None
        return self._task_store

    @staticmethod
    def _ci_title(task: Dict[str, Any]) -> str:
        tool = str(task.get("tool") or "task")
        desc = str(task.get("description") or "").strip()
        title = f"{tool}｜{desc}" if desc and desc != tool else tool
        return title[:120]

    def _ledger_context(self) -> str:
        """给子智能体的任务台账摘要：让执行器知道自己处在哪、还有哪些没做。"""
        store = self._central_task_store()
        if store is None:
            return ""
        try:
            rows = store.list(limit=8)
        except Exception:  # noqa: BLE001
            return ""
        if not rows:
            return ""
        lines = ["【任务台账】"]
        for t in rows:
            lines.append(
                f"- [{t.get('phase')}] {t.get('title')} "
                f"{t.get('percent')}% {t.get('stage') or ''} "
                f"{(t.get('detail') or '')[:60]}".rstrip())
        return "\n".join(lines)

    def _ci_register(self, task: Dict[str, Any], phase: str = "pending") -> None:
        """把任务写进中央台账（排队阶段就登记，思维脑可随时查阅）。"""
        if task.get("_ci_task_id") or task.get("ci_task_id"):
            return
        store = self._central_task_store()
        if store is None:
            return
        try:
            created = store.create(self._ci_title(task), owner="operator",
                                   stage=str(task.get("tool") or ""),
                                   phase=phase)
            if created:
                task["_ci_task_id"] = created["task_id"]
                task["_ci_revision"] = created["revision"]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 台账登记失败: {e}")

    async def _ci_start(self, task: Dict[str, Any]) -> None:
        """任务开工：已有记录则 pending→running，否则直接建 running。"""
        # 认领思维脑自主立项时预建的台账记录
        if not task.get("_ci_task_id") and task.get("ci_task_id"):
            task["_ci_task_id"] = task["ci_task_id"]
            store = self._central_task_store()
            if store is not None:
                try:
                    rec = store.get(task["_ci_task_id"])
                    if rec:
                        task["_ci_revision"] = rec.get("revision", 0)
                except Exception:  # noqa: BLE001
                    pass
        if task.get("_ci_task_id"):
            self._ci_update(task, phase="running",
                            stage=str(task.get("tool") or ""))
            return
        self._ci_register(task, phase="running")

    def _ci_update(self, task: Dict[str, Any], *, phase: str = "",
                   percent: Optional[int] = None, stage: str = "",
                   detail: str = "") -> None:
        """同步进度到台账（CAS；失败只记日志，不影响任务本身）"""
        task_id = task.get("_ci_task_id")
        if not task_id:
            return
        store = self._central_task_store()
        if store is None:
            return
        try:
            result = store.update(task_id, task.get("_ci_revision", 0),
                                  actor="operator", phase=phase, percent=percent,
                                  stage=stage, detail=detail)
            if result.get("ok"):
                task["_ci_revision"] = result["task"]["revision"]
            else:
                logger.debug(f"[brain:{self.name}] 台账更新被拒: "
                             f"{result.get('error')}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 台账更新失败: {e}")

    def _ci_finish(self, task: Dict[str, Any], result: Dict[str, Any]) -> None:
        """执行结束：写最终进度，交给思维脑判定是否收尾。"""
        if not task.get("_ci_task_id"):
            return
        result = result if isinstance(result, dict) else {}
        if "success" in result:
            ok = bool(result.get("success"))
        else:
            ok = not bool(result.get("error"))
        if ok:
            parts = ["执行完成"]
            files = result.get("files_changed") or []
            if files:
                parts.append(f"改动 {len(files)} 个文件")
            summary = str(result.get("summary") or "").strip()
            if summary:
                parts.append(summary[:180])
            detail = "，".join(parts)
        else:
            detail = str(result.get("error") or "失败")[:200]
        # 注意：这里不把相位设为 done —— 最终完成由思维脑核对结果后调用 close()
        self._ci_update(task, percent=100 if ok else None,
                        stage="awaiting_review" if ok else "failed",
                        detail=detail)
        # 保留公开 id：完成事件之后思维脑仍能把它归到同一条任务上
        task["ci_task_id"] = task.get("_ci_task_id")
        task.pop("_ci_task_id", None)
        task.pop("_ci_revision", None)

    def _ci_set_plan(self, task: Dict[str, Any],
                     plan: List[Dict[str, Any]], reason: str = "") -> None:
        """执行方按实际情况改写计划（写回台账，思维脑可见）。"""
        task_id = task.get("_ci_task_id") or task.get("ci_task_id")
        if not task_id or not plan:
            return
        store = self._central_task_store()
        if store is None:
            return
        try:
            rec = store.get(task_id)
            if not rec:
                return
            result = store.update(task_id, rec.get("revision", 0),
                                  actor="operator", plan=plan,
                                  detail=(reason or rec.get("detail") or "")[:400])
            if result.get("ok"):
                task["_ci_revision"] = result["task"]["revision"]
                task["plan"] = plan
                logger.info(f"[brain:{self.name}] 计划已改写（{len(plan)} 条）"
                            + (f"：{reason}" if reason else ""))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[brain:{self.name}] 计划改写失败: {e}")

    async def _emit_progress(self, task: Dict[str, Any], percent: int,
                             stage: str, detail: str = ""):
        """emit OPERATOR_PROGRESS：任务进度定期上报（思维脑/主播脑可监控）"""
        payload = {
            "task": task,
            "progress": {
                "percent": percent,
                "stage": stage,
                "detail": detail,
                "at": asyncio.get_event_loop().time(),
            },
        }
        await self.emit(EVENTS.OPERATOR_PROGRESS, payload)
        # 同步进中央信息台账，保证看板看到的就是真实进度
        self._ci_update(task, percent=percent, stage=stage, detail=detail)
        logger.debug(f"[brain:{self.name}] 进度 {percent}% [{stage}] {detail}")

    def _record_task(self, task: Dict[str, Any], result: Dict[str, Any]):
        """记录任务历史（最多 20 条）"""
        self._ci_finish(task, result)
        self._task_history.append({
            "task": task,
            "result": result,
            "at": asyncio.get_event_loop().time(),
        })
        if len(self._task_history) > 20:
            self._task_history = self._task_history[-20:]

    async def _on_will_action(self, payload: Any):
        """意志脑决策：转化为 do_task"""
        if not isinstance(payload, dict):
            return
        action_type = payload.get("type", "")

        # H-2 Phase 6: WillEngine 意图分类直接给 tool + args（type = "execute_tool"）
        if action_type == "execute_tool":
            tool_name = payload.get("tool")
            args = payload.get("args", {})
            if not tool_name:
                logger.warning(f"[brain:{self.name}] execute_tool 缺 tool 名")
                return
            logger.info(f"[brain:{self.name}] WillEngine → 执行: {tool_name}({args})")
            _log_event("INFO", "operator", f"执行 {tool_name}: 来自 WillEngine")
            # 描述带上任务主题：台账标题要能区分"查 SQL"和"查 XSS"
            subject = str(args.get("goal") or args.get("topic")
                          or args.get("query") or args.get("path") or "").strip()
            await self.emit(EVENTS.DO_TASK, {
                "tool": tool_name,
                "args": args,
                "description": subject[:60] if subject else f"WillEngine → {tool_name}",
                # 思维脑自主立项时已经把任务写进台账，这里直接认领同一条记录
                "ci_task_id": payload.get("ci_task_id"),
                "source": payload.get("source", "will_engine"),
                "decision_id": payload.get("decision_id"),
                "reasoning": payload.get("reasoning", ""),
                "user": payload.get("user", ""),
                "chat_key": payload.get("chat_key", ""),
            })
            return

        target = payload.get("target", "")
        params = payload.get("params", {})
        tool = self.WILL_ACTION_TO_TOOL.get(action_type)
        if tool is None:
            logger.debug(f"[brain:{self.name}] 意志动作无需操作: {action_type}")
            return

        # 检查工具是否在 assembly 中可用（code_task / research_task / cultist_task 是脑内合成任务，不查域）
        if tool not in ("code_task", "research_task", "cultist_task") and not await self._is_tool_available(tool):
            logger.warning(f"[brain:{self.name}] 意志动作需要 {tool}，但域未启用")
            return

        args = self._build_args(action_type, target, params)
        logger.info(f"[brain:{self.name}] 意志→操作: {action_type} -> {tool}")
        await self.emit(EVENTS.DO_TASK, {
            "tool": tool,
            "args": args,
            "description": f"执行意志动作: {action_type} ({target})",
            "source": "will_engine",
            "decision_id": payload.get("decision_id"),
            "reasoning": payload.get("reasoning", ""),
        })

    async def _is_tool_available(self, tool: str) -> bool:
        """检查工具是否在 assembly 中可用（域激活 + 工具已注册）"""
        try:
            names = self.runtime.assembly.active_tool_names()
            return tool in names
        except Exception:
            return False

    def _build_args(self, action_type: str, target: str, params: dict) -> dict:
        """构建工具参数（按动作类型）"""
        if action_type in ("watch_video", "browse_bilibili"):
            return {"keyword": target or "热门", "engine": "bilibili"}
        elif action_type in ("explore_interest", "research_topic"):
            goal = (params.get("goal") or target or "最近感兴趣的话题").strip()
            subtopics = params.get("subtopics") or []
            if subtopics:
                goal = f"{goal}（关注：{'、'.join(str(s) for s in subtopics[:3])}）"
            return {"goal": goal, "max_sources": params.get("max_sources", 2)}
        elif action_type == "code_action":
            # 意志脑自主决策"想写代码"→ 交给 CodingLoop；目标优先取 params.goal
            goal = (params.get("goal") or params.get("reasoning")
                    or target or "写一个小程序练手")
            return {"goal": goal,
                    "workspace": params.get("workspace"),
                    "acceptance": params.get("acceptance", "")}
        elif action_type == "play_game":
            return {"goal": (params.get("goal") or target or "玩一会儿密教模拟器"),
                    "max_steps": params.get("max_steps", 8)}
        elif action_type == "open_app":
            return {"app": target}
        elif action_type == "list_files":
            return {"path": params.get("path", "~/Documents")}
        return params

    async def heartbeat(self):
        """心跳：当前任务进度报告"""
        if self._running_task:
            logger.debug(
                f"[brain:{self.name}] running: "
                f"{self._running_task.get('description', '?')}"
            )

    # ---------- LLM 友好的工具调用接口（用于 function calling）----------

    def _is_perception_tool(self, tool_name: str) -> bool:
        """H-2: 判断工具是否属于感知类（执行后需 emit PERCEPTION_UPDATED）"""
        perception_prefixes = (
            "os_capture_window",    # 截屏
            "os_list_windows",      # 列窗口
            "os_list_processes",    # 列进程
            "ui_list_elements",     # UIA 读控件
            "os_list_elements_json",  # OCR + UIA 合并
            "os_clipboard_get",    # 读剪贴板
            "dsk_windows",          # desktop_agent 域的感知
            "uip_list_windows",     # uip 域的感知
            "uip_ocr",              # uip OCR
            "uip_capture",          # uip 截屏
            "pvz_observe",          # pvz 语义局面读取
        )
        return tool_name in perception_prefixes or tool_name.startswith(
            ("br_snapshot", "br_get_content", "bv_"))

    async def _emit_perception(self, tool: str, result: Dict[str, Any]):
        """H-2: emit PERCEPTION_UPDATED 事件"""
        # 从工具名推断 source_capability
        if tool.startswith("os_") or tool.startswith("ui_"):
            source_cap = "desktop"
        elif tool.startswith("dsk_"):
            source_cap = "desktop_agent"
        elif tool.startswith("uip_"):
            source_cap = "ui_perception"
        elif tool.startswith("br_"):
            source_cap = "browser"
        elif tool.startswith("pvz_"):
            source_cap = "pvz"
        elif tool.startswith("bv_"):
            source_cap = "bili_video"
        else:
            source_cap = "unknown"

        payload = {
            "source_capability": source_cap,
            "tool": tool,
            "perception": result.get("result", {}),
            "timestamp": asyncio.get_event_loop().time(),
        }
        await self.emit(EVENTS.PERCEPTION_UPDATED, payload)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """返回 LLM function calling 用的工具 schema（从 assembly 聚合）

        V2 改进：直接读 DomainAssembly.active_tool_schemas()，
        不再用 inspect 反射签名（恢复 schema 优先；与域的 tools() 一致）。
        """
        try:
            return self.runtime.assembly.active_tool_schemas()
        except Exception as e:
            logger.warning(f"[brain:{self.name}] 聚合 schema 失败: {e}")
            return []


__all__ = ['OperatorBrain', 'make_default_executor']