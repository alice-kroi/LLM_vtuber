#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务式交互架构核心：中央任务管理器（TaskManager）

将「操作脑」（operator）从 LangGraph 同步主链中剥离，改为**后台异步任务队列**：

- 主播(streamer) 把操作请求作为任务提交 → 主体(supervisor) 派发到 TaskManager
- TaskManager 维护中央优先队列 + worker 池，后台执行操作脑 ReAct 循环
- 支持任务优先级（high/normal/low）与即时并行任务（如"查看桌面"）
- 操作进展实时回调：写入 TaskStore + WebSocket 广播 task_update 进度事件
- 操作完成后通过 on_done 回调安全播报（不触碰 Live2D/TTS 串行锁）

设计：与 main.py 的 _message_queue（聊天串行流）职责分离、共存。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------- 枚举 ----------

class Priority(IntEnum):
    """任务优先级（数值越小越优先，PriorityQueue 按小到大出队）。"""
    HIGH = 0
    NORMAL = 1
    LOW = 2


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------- Task 模型 ----------

@dataclass
class Task:
    """一个后台任务单元。"""
    instruction: str                                  # 操作指令（主体下发给操作脑）
    priority: Priority = Priority.NORMAL              # 优先级
    is_immediate: bool = False                        # 即时并行任务（如"查看桌面"）
    type: str = "operator"                            # operator | immediate | chat
    submitted_by: str = ""                            # 提交来源（主播/主体）
    context: Dict[str, Any] = field(default_factory=dict)  # 提交时快照的上下文（供后续使用）

    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")  # 自生成 ID
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0                             # 0~1
    message: str = ""                                 # 当前进展描述
    result: Optional[Dict[str, Any]] = None            # 操作完成结果 {summary, result, completed, rounds}
    error: str = ""                                   # 失败原因

    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0

    # 循环内部保留字段（非 dataclass 构造参数，由 submit 时创建）
    cancel_event: Optional[asyncio.Event] = None
    result_reported: bool = False                 # 是否已由主播顺路播报（防重复播报）

    def to_broadcast(self) -> Dict[str, Any]:
        """构造 WebSocket 广播用的任务信息（不携带内部 cancel_event）。"""
        return {
            "task_id": self.task_id,
            "type": self.type,
            "status": self.status.value,
            "progress": self.progress,
            "phase": self.message,
            "message": self.message,
            "priority": self.priority.name.lower(),
            "is_immediate": self.is_immediate,
            "submitted_by": self.submitted_by,
            "result": self.result,
        }


# ---------- TaskManager 单例 ----------

class TaskManager:
    """
    中央任务管理器（单例 task_manager）。

    - 优先队列 PriorityQueue 存 (priority, seq, task)，保证同级 FIFO。
    - worker 池（默认 2）串行消费队列跑慢任务。
    - 即时任务（is_immediate）通过信号量上限并发并行派发。
    - _tasks: TaskStore（task_id → Task），保留存活与最近完成结果。
    - on_progress / on_done：外部注册的异步回调（进度上报 / 完成播报）。
    """

    def __init__(self, worker_count: int = 2, immediate_max_concurrency: int = 3):
        self._worker_count = worker_count
        self._immediate_max_concurrency = immediate_max_concurrency
        self._queue: Optional[asyncio.PriorityQueue] = None
        self._tasks: Dict[str, Task] = {}
        self._seq = 0
        self._workers: List[asyncio.Task] = []
        self._immediate_sem: Optional[asyncio.Semaphore] = None
        self._started = False
        self._bound_on_progress: Optional[Callable[..., Any]] = None
        self._bound_on_done: Optional[Callable[..., Any]] = None
        # 保留最近 N 条完成/失败结果（供主播读取）
        self._recent_completed: List[str] = []
        self.RECENT_MAX = 20

    # ---------- 生命周期 ----------

    def ensure_started(self) -> None:
        """确保队列与 worker 已启动（需在 running loop 内调用）。"""
        if self._started:
            return
        loop = asyncio.get_running_loop()
        self._queue = asyncio.PriorityQueue()
        self._immediate_sem = asyncio.Semaphore(self._immediate_max_concurrency)
        self._workers = [loop.create_task(self._worker_loop()) for _ in range(self._worker_count)]
        self._started = True
        logger.info(f"[TaskManager] 已启动：worker x{self._worker_count}, "
                    f"即时并行上限 {self._immediate_max_concurrency}")

    def bind_callbacks(self, on_progress: Optional[Callable[..., Any]] = None,
                       on_done: Optional[Callable[..., Any]] = None) -> None:
        """绑定进度 / 完成回调（由外部 main.py 注入）。"""
        self._bound_on_progress = on_progress
        self._bound_on_done = on_done

    # ---------- 提交 ----------

    def submit(self, instruction: str, *, priority: Priority = Priority.NORMAL,
               is_immediate: bool = False, type: str = "operator",
               submitted_by: str = "", context: Optional[Dict[str, Any]] = None,
               source: str = "desktop") -> str:
        """提交任务，返回 task_id。即时任务并行派发，慢任务入优先队列。

        source: 能力域来源标识（如 desktop/game_mc），随任务上下文透传到 run_operator
        并回写进结果，供主播按域回灌。"""
        self.ensure_started()
        task = Task(
            instruction=instruction,
            priority=priority,
            is_immediate=is_immediate,
            type=type,
            submitted_by=submitted_by,
            context={**(context or {}), "source": source},
        )
        task.cancel_event = asyncio.Event()
        self._tasks[task.task_id] = task
        if is_immediate:
            asyncio.create_task(self._run_immediate(task))
            logger.info(f"[TaskManager] 即时任务派发: {task.task_id} type={type}")
        else:
            self._seq += 1
            self._queue.put_nowait((task.priority.value, self._seq, task))
            logger.info(f"[TaskManager] 任务入队: {task.task_id} priority={priority.name} "
                        f"（队列深 {self._queue.qsize()}）")
        self._emit_progress(task, phase="submitted")
        return task.task_id

    # ---------- 派发执行 ----------

    async def _run_immediate(self, task: Task) -> None:
        """即时任务：占用信号量并发执行。"""
        async with self._immediate_sem:  # type: ignore
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            self._emit_progress(task, phase="running")
            await self._dispatch(task)

    async def _worker_loop(self) -> None:
        """普通任务 worker：循环消费优先队列。"""
        while True:
            try:
                _, _seq, task = await self._queue.get()  # type: ignore
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
                self._emit_progress(task, phase="running")
                await self._dispatch(task)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TaskManager] worker 异常: {e}")
            finally:
                try:
                    self._queue.task_done()  # type: ignore
                except Exception:
                    pass

    async def _dispatch(self, task: Task) -> None:
        """真正执行操作脑循环（带协作式取消 + 进度回调）。"""
        try:
            from LLM.operator_agent import run_operator

            op_result = await run_operator(
                task.instruction,
                max_rounds=10,
                task_id=task.task_id,
                cancel_event=task.cancel_event,
                progress_cb=lambda frac, msg: self._on_progress(task, frac, msg),
                source=(task.context or {}).get("source", "desktop"),
            )
            task.status = TaskStatus.DONE if op_result.get("completed") else TaskStatus.FAILED
            task.result = op_result
            task.error = "" if op_result.get("completed") else str(op_result.get("result", ""))
            task.progress = 1.0 if op_result.get("completed") else task.progress
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.message = "任务被取消"
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.result = {"summary": "操作执行异常", "result": str(e), "completed": False}
            logger.error(f"[TaskManager] 任务 {task.task_id} 执行异常: {e}")
        finally:
            task.finished_at = time.time()
            self._finish(task)

    # ---------- 进度 / 完成回调 ----------

    def _on_progress(self, task: Task, frac: float, msg: str) -> None:
        """koko操作进展：更新 TaskStore + 广播 + 调用外部 progress 回调。"""
        task.progress = max(0.0, min(1.0, frac))
        task.message = msg or task.message
        self._emit_progress(task, phase=task.message)

    def _emit_progress(self, task: Task, phase: str) -> None:
        """把进展异步广播出去（不阻塞操作 ReAct 循环）。"""
        try:
            # 广播到 WebSocket
            asyncio.create_task(self._ws_broadcast(task, event="task_update"))
            # 外部进度回调（如 main.py 注入的钩子）
            if self._bound_on_progress:
                try:
                    self._bound_on_progress(task)
                except Exception as e:
                    logger.debug(f"[TaskManager] on_progress 回调异常: {e}")
        except RuntimeError:
            pass  # 无 running loop（例如模块导入期）

    def _finish(self, task: Task) -> None:
        """任务结束：广播完成事件 + 触发外部 on_done 回调。"""
        if task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self._recent_completed.append(task.task_id)
            if len(self._recent_completed) > self.RECENT_MAX:
                self._recent_completed.pop(0)
        event = "task_done" if task.status == TaskStatus.DONE else "task_failed"
        try:
            asyncio.create_task(self._ws_broadcast(task, event=event))
        except RuntimeError:
            pass
        if self._bound_on_done is not None:
            try:
                _ret = self._bound_on_done(task)
                # 支持异步回调（若返回 coroutine，则以任务方式调度，不阻塞当前协程）
                if _ret is not None and asyncio.iscoroutine(_ret):
                    asyncio.create_task(_ret)
            except Exception as e:
                logger.error(f"[TaskManager] on_done 回调异常: {e}")

    # ---------- WebSocket 广播（懒加载 ws_manager） ----------

    async def _ws_broadcast(self, task: Task, event: str) -> None:
        try:
            from webui.app import ws_manager
            await ws_manager.broadcast_task(task, event=event)
        except Exception as e:
            logger.debug(f"[TaskManager] WebSocket 广播失败: {e}")

    # ---------- 查询 / 控制 ----------

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_by_ids(self, task_ids: List[str]) -> List[Task]:
        return [self._tasks.get(tid) for tid in task_ids if tid in self._tasks]

    def all(self) -> List[Task]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return False
        if task.cancel_event:
            task.cancel_event.set()  # 协作式取消：操作脑在每轮工具间感知
        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.CANCELLED
        return True

    def recent_completed_ids(self) -> List[str]:
        return list(self._recent_completed)


# 单例：供 supervisor 与 main.py 复用（Milvus 连接管理同款单例语义）
task_manager = TaskManager()