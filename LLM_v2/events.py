# ============================================================
# LLM_v2/events.py — 事件类型常量
# ============================================================
# EDA 架构的事件协议：所有事件类型集中定义
# 任何新增事件都必须在此登记，否则视为不规范
# ============================================================

from __future__ import annotations

from typing import Final


class EVENTS:
    """所有事件类型的中央注册表"""

    # ---------- 输入事件（外部 → 系统）----------
    BILIBILI_MSG: Final = "bilibili_msg"           # 弹幕消息
    GIFT_RECEIVED: Final = "gift_received"         # 收到礼物
    USER_COMMAND: Final = "user_command"           # 主播指令（WebUI / 手动）

    # ---------- 内部事件（脑 → 脑 / 内部流转）----------
    IDLE_TICK: Final = "idle_tick"                 # 空闲心跳（每 30s）
    TASK_STARTED: Final = "task_started"           # 任务开始
    TASK_DONE: Final = "task_done"                 # 任务完成
    TASK_FAILED: Final = "task_failed"             # 任务失败
    NEEDS_CLARIFICATION: Final = "needs_clarification"  # 需要观众确认
    REST_REQUESTED: Final = "rest_requested"       # 请求休息

    # ---------- 输出事件（系统 → 外部）----------
    TTS_SPEAK: Final = "tts_speak"                 # TTS 说话
    LIVE2D_EXPRESSION: Final = "live2d_expression" # Live2D 表情切换
    LIVE2D_MOTION: Final = "live2d_motion"         # Live2D 动作
    BILIBILI_REPLY: Final = "bilibili_reply"       # 弹幕回复

    # ---------- 自主事件（意志脑）----------
    WILL_ENGINE_DECIDED: Final = "will_engine_decided"   # 意志脑决策结果
    DIARY_WRITTEN: Final = "diary_written"               # 日记已写

    # ---------- 任务事件 ----------
    DO_TASK: Final = "do_task"                     # 操作脑执行任务
    OPERATOR_ACTION: Final = "operator_action"     # 操作脑动作请求
    OPERATOR_PROGRESS: Final = "operator_progress" # 操作脑任务进度（定期上报）
    OPERATOR_DONE: Final = "operator_done"         # 操作脑完成
    WILL_ENGINE_ACTION: Final = "will_engine_action"  # 意志脑想做的动作

    # ---------- RAG / 记忆事件 ----------
    RAG_QUERY: Final = "rag_query"                 # RAG 查询请求
    RAG_RETRIEVED: Final = "rag_retrieved"         # RAG 检索完成
    MEMORY_STORE: Final = "memory_store"           # 记忆存储请求

    # ---------- 进度查询/报告事件（主播脑 ↔ 思维脑）----------
    PROGRESS_QUERY: Final = "progress_query"       # 主播脑→思维脑：请求任务进度（主播脑不直接答）
    PROGRESS_REPORT: Final = "progress_report"     # 思维脑→主播脑：进度报告（由主播脑对外回复）

    # ---------- 系统事件 ----------
    ERROR: Final = "error"                         # 错误发生
    SHUTDOWN: Final = "shutdown"                   # 系统关闭
    STATE_UPDATED: Final = "state_updated"         # 状态已更新

    # ---------- H-2 通用输入/感知事件 ----------
    USER_MESSAGE: Final = "user_message"           # 通用用户输入（source 区分来源）
    PERCEPTION_UPDATED: Final = "perception_updated"  # 环境感知广播（operator 调完感知工具后 emit）

    # ---------- 能力域事件（V2 DomainAssembly → EventBus 推送）----------
    DOMAIN_REGISTERED: Final = "domain_registered"   # 域已挂载（register 成功）
    DOMAIN_STARTED: Final = "domain_started"         # 域 start() 成功
    DOMAIN_STOPPED: Final = "domain_stopped"         # 域 stop() 成功
    DOMAIN_DEGRADED: Final = "domain_degraded"       # 域运行期聚合抛错（单域隔离）
    DOMAIN_MISSING: Final = "domain_missing"         # 域构造/挂载失败（tool_clash/reserved 等）
    DOMAIN_TOOL_CALLED: Final = "domain_tool_called" # call_tool 每次调用（审计）


# ---------- 事件分类辅助 ----------
INPUT_EVENTS = frozenset([
    EVENTS.BILIBILI_MSG,
    EVENTS.GIFT_RECEIVED,
    EVENTS.USER_COMMAND,
])

OUTPUT_EVENTS = frozenset([
    EVENTS.TTS_SPEAK,
    EVENTS.LIVE2D_EXPRESSION,
    EVENTS.LIVE2D_MOTION,
    EVENTS.BILIBILI_REPLY,
])

INTERNAL_EVENTS = frozenset([
    EVENTS.IDLE_TICK,
    EVENTS.TASK_STARTED,
    EVENTS.TASK_DONE,
    EVENTS.TASK_FAILED,
    EVENTS.NEEDS_CLARIFICATION,
    EVENTS.REST_REQUESTED,
    EVENTS.DO_TASK,
    EVENTS.OPERATOR_ACTION,
    EVENTS.OPERATOR_PROGRESS,
    EVENTS.OPERATOR_DONE,
    EVENTS.WILL_ENGINE_DECIDED,
    EVENTS.WILL_ENGINE_ACTION,
    EVENTS.DIARY_WRITTEN,
    EVENTS.STATE_UPDATED,
    EVENTS.USER_MESSAGE,
    EVENTS.PERCEPTION_UPDATED,
    EVENTS.PROGRESS_QUERY,
    EVENTS.PROGRESS_REPORT,
    EVENTS.DOMAIN_REGISTERED,
    EVENTS.DOMAIN_STARTED,
    EVENTS.DOMAIN_STOPPED,
    EVENTS.DOMAIN_DEGRADED,
    EVENTS.DOMAIN_MISSING,
    EVENTS.DOMAIN_TOOL_CALLED,
])

SYSTEM_EVENTS = frozenset([
    EVENTS.ERROR,
    EVENTS.SHUTDOWN,
])

DOMAIN_EVENTS = frozenset([
    EVENTS.DOMAIN_REGISTERED,
    EVENTS.DOMAIN_STARTED,
    EVENTS.DOMAIN_STOPPED,
    EVENTS.DOMAIN_DEGRADED,
    EVENTS.DOMAIN_MISSING,
    EVENTS.DOMAIN_TOOL_CALLED,
])
