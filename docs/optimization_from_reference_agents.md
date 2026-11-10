# LLM_vtuber 优化与改进方案（参考 Cortico & NagaAgent）

> 更新日期：2026-09-17
> 参考项目：`D:\Github\env\Cortico`（基于事件流的 Agent Harness），`D:\Github\env\NagaAgent`（多服务多Agent框架）
> 目标：提炼对本项目（单进程 LangGraph LLM 虚拟主播，三脑 Supervisor 架构）有实际价值、可落地的改进点。

---

## 一、与参考项目对照的现状

| 维度 | LLM_vtuber 现状 | Cortico | NagaAgent |
|------|----------------|---------|-----------|
| 编排 | 单 LangGraph 图 + Supervisor 三脑(主播/主体/操作) | 事件流主循环 + 临时 fork 子任务 | 多服务并行 + 规则/LLM 分级路由 |
| 记忆 | InMemoryStore + Milvus RAG | 工作区文件为准 | summer_memory 异步队列(去重/重试/清理) |
| 工具 | OpenAI function calling | 事件+工具契约 | 厂商无关代码块工具+按 agentType 路由 |
| 配置 | config.ini + 环境变量 | 四层热更新 | Pydantic 类型安全 + json5 热更新 |
| 健康 | 裸异常日志 | 水位/stall/事故包/doctor 自检 | /health 探活 + dogtag 调度 |

---

## 二、按优先级排列的改进点

### P0：高价值、低风险、可快速落地

**1. 输入侧降噪与限流（借鉴 Cortico B站 World）**
- 现象：B站高频弹幕容易导致上下文预算膨胀、LLM 被无关消息刷屏。
- 改进：对弹幕做「300ms 窗口 + 64 条容量归并」；对大额礼物/SC/重要观众作高优先级投递，普通消息归档式丢弃。把"什么值得喂给 LLM"作为独立的投递过滤层。
- 落地：新建 `broadcast/danmu_filter.py`，在弹幕入队前归并去噪；重要事件（礼物/SC/上舰）单独标记 `important` 字段（项目已有 Milvus 的 `is_important` 字段，可复用）。

**2. 弹幕积压水位与投递停滞自检（借鉴 Cortico core/loop/qon）**
- 现象：弹幕监听器停止后队列残留消息继续被处理（项目记忆已记录过该坑）。
- 改进：为消息队列记录 `lastDeliveredCursor`（投递水位），重启后补投未处理弹幕；队列停滞超过阈值时告警而非只靠崩溃堆栈。
- 落地：`main.py` 消息队列 worker 增加水位持久化，周期性自检积压量。

**3. TTS/语音关键库调用加结构化重试与日志（借鉴 Cortico ResubmitPolicy + jsonl）**
- 现象：TTS/弹幕库失败只打裸日志，无法区分"可重试(429/5xx)"与"不可重试(401/输入超限)"。
- 改进：统一封装 `RetryPolicy(maxConsecutive=2, backoff=[2s,10s])`，按状态码分类；日志加结构化关联字段（有的是 `sess/round/tool`）。
- 落地：新建 `common/retry.py`，LLM 调用与 TTS 调用接入。

### P1：中高价值、中等改动

**4. 五层提示词与三处存储解耦（借鉴 NagaAgent，对三脑最直接）**
- 现象：当前主播/操作 prompt 集中在 main.py 常量，人设、工具、记忆混在一起。
- 改进：提示词分 5 层——tier1 人设(主播) → tier2 工具诉 → tier3 记忆/桌面情况 → tier4 环境 → tier5 消息增量；存储到 3 处：`characters/`(人设)、`system/prompts`(规则)、长期记忆独立引用槽。
- 对三脑的好处：主播人设/操作策略分离，给主体"桌面情况"作为独立引用槽而非塞进 context 字符串。

**5. 厂商无关工具调用 + 按 agentType 并行路由（借鉴 NagaAgent）**
- 现象：当前 LLM 依赖 OpenAI function calling（绑定豆包兼容接口）。
- 改进：允许 LLM 以文本代码块方式输出工具 JSON（` ```tool``` `，json5 容错），按 agentType 路由(mcp/live2d/operate)，`asyncio.gather` 并行执行，结果回注循环最多 5 轮。
- 收益：切换模型/厂商时工具调用不受 OpenAI schema 绑定。

**6. 记忆异步任务队列（借鉴 NagaAgent summer_memory）**
- 现象：RAG 嵌入/检索同步执行会阻塞事件循环（项目记忆已记录该坑）。
- 改进：记忆入库/提炼走异步队列 + 3 worker + SHA-256 去重 + 超时重试 + 定期清理。
- 落地：`memory_manager` / `knowledge_curator` 改为队列驱动。

**7. 主动发言调度器（借鉴 NagaAgent dogtag + proactive_vision）**
- 现象：主播目前是被动响应弹幕，无主动开话题。
- 改进：dogtag 式 1s 主循环管理周期任务；主动发言定义 `keywords/cooldown_seconds/quiet_hours(23-7)`，避免骚扰超载。
- 落地：新增 `learning/proactive.py` 心跳调度。

### P2：结构性 / 长期演进

**8. 事件流语义分层（借鉴 Cortico 四层架构）**
- 构思：把 "Core(语义无关机制)" 与 "Persona(主播人格)" 与 "World(外部环境/直播协议)" 分离，TTS/live2d/弹幕 作为可通过事件+工具契约调用的 World，解耦平台细节与主播语义。长期目标，非短期革命。

**9. 治理与运维增强（借鉴 Cortico）**
- 全局结构化日志（jsonl + 关联字段）、事故包、单实例锁（防止 WebUI 与后台重复启 bot）、配置热更新。

**10. 配置类型安全化（借鉴 NagaAgent config.py）**
- config.ini 升级为 Pydantic 模型 + json5 热更新，端口/密钥类型校验，./health 探活。

---

## 三、不采纳的项及原因

| 参考项 | 不采纳原因 |
|--------|-----------|
| Cortico 单进程单 bot 常驻主循环取代三脑 | 与本项目已验证的三脑 Supervisor 冲突，且单 bot 模式不利于"主播+操作+主体"的职责分离 |
| NagaAgent 多服务多进程全拆分 | 单机部署运维成本高；本项目单进程事件循环已够，仅借鉴"逻辑分层" |
| 用事件流完全替代 LangGraph | LangGraph 的检查点/持久化对直播常驻状态更适合，保留 |

---

## 四、建议落地顺序

1. **本轮即可做**：P0-2（投递水位/队列自检）、P1-6（记忆异步入队，已有部分基础）、P0-1（弹幕降噪限流）
2. **短期（1-2轮）**：P1-4（五层提示词）、P1-5（厂商无关工具）
3. **中期**：P1-7（主动发言）、P0-3（重试/结构化日志）
4. **长期**：P2-8/9/10（事件流分层、治理、配置）

---

## 五、参考文献对照

- `D:\Github\env\Cortico\PHILOSOPHY.md`、`AGENTS.md`、`src\core\loop.ts`、`src\worlds\bilibili\README.md`、`docs\extensions.md`
- `D:\Github\env\NagaAgent\main.py`、`guide_engine\query_router`、`summer_memory`、`mcpserver\mcp_registry`、`config.py`、`proactive_vision_config.json`