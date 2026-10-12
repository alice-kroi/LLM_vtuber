# 主播脑重构方案：从 LangGraph 直播专线 → 多渠道交互中枢

> 文档日期：2026-10-11
> 目标：**演进**主播脑的 LangGraph（保留成熟引擎与三脑节点实现，重构输入/会话/门面三层），把主播脑升级为**用户与程序的统一交互接口（对外门面）**——除了哔哩哔哩直播间，用户可通过 WebUI 聊天、语音、命令行、外部 API 等任意渠道把信息/说话/问题告诉主播脑。
> 关系：B 站直播监控渠道化落地见 [bili_extension_plan.md](./bili_extension_plan.md)，本文是总架构方案。

## 0. 结论摘要

旧主播脑是**为"直播弹幕 → AI 回复"设计的 LangGraph 固定管线**：每次回复都走 `init→load_memory→rag→streamer_request→supervisor→operator/streamer_worker→finalize`，输入被假设为直播间弹幕，输出固定回直播间。渠道只有 B 站一条，交互模型是"弹幕驱动"。

**结论：不推翻，演进。** 现有 LangGraph 引擎与三脑节点实现（`supervisor_route_node` 等）已成熟，保留它作为**编排层**；只演进三层——输入渠道化、多会话、门面化。

新主播脑 = **团队对外门面（Persona Front），LangGraph 演进为多渠道编排 + 三级路由省算力**：
1. **门面定位**：主播脑是用户接触到的"形象"，带 persona（人格/口吻/情绪），负责更好、更具情感地与用户交流；
2. **门面层 = 表达层 + L0 免费预筛**：ingress 标准化/鉴权/会话；egress 润色/路由；**L0 用确定性规则（正则/白名单，非 LLM）命中纯寒暄/问候/致谢**（"你好""谢谢""在吗"）→ 模板+最近会话直接答，**不耗 LLM 算力**；未命中才进编排层；
3. **需求分流（编排层 L1）**：`streamer_request` 一次解析"这个回答需要什么"——①需要**回答所需信息**（记忆/RAG/情感/上下文）②需要**执行操作**（操作脑）③都不需要（开放闲聊，轻回答不重跑 rag）；
4. **情感化润色**：门面末轮把答复加工成有 persona、有感情的话再发回用户；
5. **渠道抽象（Channel）**：B站弹幕、WebUI 聊天、语音、CLI、外部 API 都是平等输入渠道，统一产标准化消息 `{channel, msg_type, user, content, ts, meta}`；
6. **编排层 = LangGraph（保留演进）**：图结构按三脑语义组织，输入吃标准化消息，输出按渠道路由；L2 重管线（memory/rag/context）按需求按需加载；
7. **能力层（Capabilities）**：`operator`、`live2d`、`desktop_agent`、`launcher`、`bili` 等域是能力层，继续用 `skill_assembly` 调度；
8. **多轮会话**：按 `(channel, user)` 维护上下文，WebUI 用户与直播间观众都能连续对话。

一句话：**门面（收发+L0 免费预筛）→ 编排层（L1 需求解析，L2 重管线按需）→ 能力层；LangGraph 是承载三脑的编排引擎（保留演进）。三级路由保证"极简单问题零算力、普通问题一次解析、复杂问题才重管线"。**

## 1. 现状（要演进的）

### 1.1 LangGraph 主播脑（三脑模型）
- 图结构：[main.py#L992-1044](file:///e:/GitHub/LLM_vtuber/main.py#L992-L1044)：`init→load_memory→rag_retrieval→[context_control]→streamer_request→supervisor→{operator_worker|streamer_worker}→finalize`。
- 节点职责：
  - `streamer_request`：主播首轮，产出情感 + 操作请求；
  - `supervisor`（主体思维，`LLM.LLM_node.supervisor_route_node` [LLM_node.py#L886](file:///e:/GitHub/LLM_vtuber/LLM/LLM_node.py#L886)）：信息交换中枢，路由操作；
  - `operator_worker`：操作思维执行（`TASK_MANAGER_ENABLED=1` 时后台异步）；
  - `streamer_worker`：主播末轮组织话术 + 广播 + 后处理（RAG 保存/记忆/Live2D/TTS 并行）。

### 1.2 输入只有一条：B 站弹幕
- main.py `start_bilibili_listener` Popen `bili_main.py` → webui `POST /` → `handle_post_request`（去重/限流/奖励/入队）→ 队列 → LangGraph。
- 用户其它输入（WebUI 聊天 `/api/send`、语音等）要么缺，要么是旁路，不是主播脑一等公民。

### 1.3 痛点
- 图结构把"一次弹幕回复"写死成固定管线，加入新输入渠道/新交互类型都要改图；
- 会话状态面向单直播间，无法表达"某用户在 WebUI 跟我连续聊"这类多会话；
- 主播脑与"直播弹幕"强耦合，无法作为通用 AI 交互体复用。

## 2. 目标架构（主播脑 = 团队门面，LangGraph 演进为多渠道编排）

需求定性：**主播脑是团队的对外门面**——带 persona，把思维脑传回的答复情感化润色后再对用户开口；它是用户唯一直接接触的"形象"。LangGraph 保留为编排引擎（需求判断在编排层）。

```
   用户（多渠道） / 扩展（信息来源：bili 域弹幕、桌面事件…）
          │ ①用户消息/扩展事件（标准化消息 StreamerMessage）
          ▼
┌──────────────────── 门面层 Streamer Front（表达层 + 小模型简单思考） ───────┐
│  ingress：接收/鉴权/去重/限流/奖励/会话关联                                  │
│  L0 规则模板：确定性寒暄/问候/致谢 → 模板+最近会话直接答（零算力）           │
│  L1 小模型（本地 Qwen2.5-0.5B，CPU）：简单问答直接答 + 判断"需不需要大模型"  │
│  polish：门面末轮把答复润色成有 persona 的话（小模型/模板）                  │
│  egress：按 reply_to 路由回用户/渠道                                        │
└────────────┬────────────────────────────────────┬──────────────────────────┘
     ②需要大模型/信息/操作→进编排层         L0/L1 命中→直接答（不进编排层）
             ▼
┌──────────────────── 编排层 LangGraph（保留演进，大模型） ───────────────────┐
│  L2 需求解析（streamer_request，大模型）：需信息? 需操作? 都不需要?          │
│  → 需信息：load_memory/rag/context（按需加载）                               │
│  → 需操作：supervisor → operator（TaskManager 异步）                        │
│  → 都不需要：轻回答（不重跑 rag）                                            │
│  streamer_worker(门面末轮·润色) → finalize(并行：RAG保存/记忆/live2d/tts)    │
└──────────────────────┬─────────────────────────────────────────────────────┘
       ④操作指令（任务）              ③答复 + ⑤操作结果
                       ▼
┌──────────────────── 能力层 skill_assembly ─────────────────────────────────┐
│  operator / live2d / desktop_agent / launcher / bili 等域                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

消息流（用户视角的一次交互）：
1. 用户/扩展消息 → **门面层** ingress（标准化/鉴权/去重/限流/奖励/会话关联）；
2. **L0 规则模板**（免费）命中确定性寒暄 → 直接答；否则 **L1 小模型简单思考**（本地）：能答的简单问题直接答；判断需要大模型（需信息/需操作/复杂）→ 进编排层；
3. **L2 大模型需求解析**（`streamer_request`）：需信息 → memory/rag/context（按需加载）；需操作 → supervisor 路由 operator；都不需要 → 轻回答；
4. **操作脑**经 TaskManager/`skill_assembly` 执行，结果回报；
5. **门面末轮** `streamer_worker` 把答复**情感化润色成 persona 话术**；
6. **门面层** egress 按 `reply_to` 路由回对应用户/渠道。

要点：
- **大小模型协同省算力**：L0 规则模板（免费）→ L1 本地小模型（CPU，简单思考/情感/初筛）→ L2 豆包大模型（仅需要时）。寒暄/闲聊/简单问题基本不耗大模型 API。
- **小模型"简单思考"**：不只规则——能处理多变的简单问答（闲聊、情绪回应），并判断"是否需要大模型"，避免规则预筛的局限。
- **需求判断在编排层**：`streamer_request`（大模型）判断"需信息/需操作/都不需要"——supervisor 路由的输入（现状已产出情感+操作请求，演进为三类）。
- **LangGraph 保留为编排引擎（不推翻）**：三脑节点实现（`supervisor_route_node` 等）已成熟，只演进三处——输入渠道化、LLMState 多会话、门面末轮润色（+L2 按需加载）。
- **复用现有资产**：LangGraph 引擎、记忆/RAG/情感/奖励、`supervisor_route_node`/`operator_agent`/`skill_assembly` 全部保留。

## 3. 主播脑接口层设计

### 3.1 标准化消息协议（跨渠道唯一契约）
```json
{
  "channel": "bili" | "webui" | "voice" | "cli" | "api",
  "msg_type": "danmaku" | "gift" | "sc" | "chat" | "command" | "question",
  "user": {"id": "uid_...", "name": "观众名"},
  "content": "消息文本",
  "ts": 1791685652320,
  "meta": {"room_id": 71001, "gift_name": "...", "session_id": "..."}
}
```
- 所有渠道向主播脑 ingress 投喂该格式；主播脑转思维脑，产出回复时带 `reply_to`（目标渠道）与 `speak`（是否走 TTS）。

### 3.2 LangGraph 演进设计（保留引擎 + 大小模型协同）
```
# ① 门面层（新增，LLM/streamer/）——收发 + 小模型简单思考
async def streamer_ingress(msg):            # 用户/扩展消息统一入口
    m = await normalize(msg)                # 标准化/鉴权/去重/限流/奖励
    sess = session_mgr.get(m)               # (channel, user) 会话
    if l0_greeting_rule(m):                 # L0 规则模板：确定性寒暄（免费）
        return await streamer_egress(greeting_reply(m, sess), sess)
    small = await small_brain.simple_think(m, sess)  # L1 本地小模型简单思考
    if small.reply:                          # 小模型能直接答（简单问答/闲聊/情绪）
        return await streamer_egress(small.reply, sess)
    if not small.need_big:                   # 小模型判断不需要大模型
        return await streamer_egress(small.fallback, sess)
    return await langgraph_run(m, sess)     # 需要大模型 → 进编排层

# ② 编排层 = LangGraph（保留演进，大模型）
#    演进1-输入渠道化：入口节点构造 LLMState 时注入标准化消息 + channel/reply_to/session_key
#    演进2-多会话：LLMState 增加 session_key；invoke 前按会话拼接历史
#    L2 需求解析：streamer_request（大模型）判断 需信息? 需操作? 都不需要?
#       → 需信息走 memory/rag/context（按需加载）；需操作经 supervisor→operator；都不需要→轻回答
#    演进3-门面润色：streamer_worker（门面末轮）调 streamer_polish 做 persona 情感化润色
async def langgraph_run(m, sess):
    state = create_state(m, sess)           # 演进1+2：消息/渠道/会话入 state
    result = await langgraph_manager.run_with_messages(state)  # 现有图引擎 + L2 需求解析
    return await streamer_egress(result, sess)

# ③ 门面层 egress（新增）
async def streamer_egress(result, sess):
    text = await streamer_polish(result.reply, sess)  # persona 润色（小模型/模板）
    channel = result.reply_to or sess.channel
    await channels[channel].send(text, sess)          # bili/webui/tts/cli/api
```
- **L0 规则模板**：确定性寒暄（正则/白名单），免费零算力。
- **L1 小模型简单思考**（`small_brain.py`，本地 Qwen2.5-0.5B CPU）：①直接答简单问题（闲聊/情绪/寒暄变体）②判断 `need_big`（需信息/需操作/复杂→True）——比纯规则更能应对多变输入。
- **L2 需求解析**：`streamer_request`（大模型）判断"需信息/需操作/都不需要"——supervisor 路由的输入。
- **L2 按需加载**：memory/rag/context 仅在"需信息"时加载。
- **三处演进都是加法，不动现有图节点内部实现**：`build_graph` 的三脑节点原样保留，只改入口构造、需求解析输出、L2 按需与末轮润色。
- 并发保护：同会话串行（`per-session asyncio.Lock`），不同会话可并行；LangGraph 单实例保序。

### 3.3 会话管理（新概念，旧结构没有）
- `SessionManager`：`(channel, user_id) → ChatState`（历史/记忆句柄/情感缓存）。
- 直播间：按 `(bili, uid)`；WebUI：按 `(webui, session_id)`；命令行：单例。
- 上下文长度/裁剪/过期复用现有 `context_control` 逻辑。

### 3.4 输出路由（Egress）
- `reply_text` + `egress_channel` → 对应渠道发（B站发弹幕用 `bili_send`；webui 用 WS 推送；voice 用 TTS）。
- 操作结果通知（"已启动模拟器"等）同样经主播脑 egress 回报用户。
- 与旧 `_message_queue → worker → LangGraph` 的投递语义等价，但出口多路化。

## 4. 渠道接入清单

| 渠道 | 接入方式 | 输入 | 输出 |
|---|---|---|---|
| **bili**（首个落地） | `bili` 能力域（见 bili_extension_plan.md） | 弹幕/礼物/SC → 标准化消息 | `bili_send` 发弹幕 |
| **webui 聊天** | 现有 `/api/send` + WS（[webui/app.py#L413](file:///e:/GitHub/LLM_vtuber/webui/app.py#L413)）改为直接投主播脑 ingress | 用户聊天/问题 | WS 显示 + 可选 TTS |
| **voice 语音** | 语音输入（ASR，复用 face_dataset/asr.py）→ 文本投主播脑 ingress | 语音 → 文字 | TTS 朗读回复 |
| **cli** | 主程序 stdin / 命令行参数 | 直接问 | 打印 |
| **api** | HTTP POST /api/hub（外部程序接入） | 任意结构化输入 | JSON 回复 |

## 5. 与能力域（SkillWorld）的关系

- **渠道**：bili 是 SkillWorld 域；webui/voice/cli/api 是主播脑内建或轻量渠道，统一走"标准化消息"。
- **能力**：`operator`、`live2d`、`desktop_agent`、`launcher` 等现有域是**能力层**（操作脑的执行目标），继续用 `skill_assembly` 调度。
- 主播脑接口层本身是**核心组件**（非域）：它连接用户/扩展与思维脑/操作脑，可复用于任意交互场景。

## 6. 迁移步骤

### P0 — 渠道化先行（不推翻 LangGraph）
1. 先落地 bili 域（按 bili_extension_plan.md P0），保持现状消息流不变，主播脑暂不动。

### P1 — 引入渠道抽象（LangGraph 并行保留）
2. 定义标准化消息协议 + `Channel` 抽象（ingress/egress）；
3. WebUI `/api/send`、CLI 接入为 webui/cli 渠道，投喂标准化消息；
4. LangGraph 入口改为消费"标准化消息"（`handle_post_request` 与 `/api/send` 合并进统一 inbox）。

### P2 — 演进 LangGraph：门面层 + 三处加法（核心）
5. 新 `LLM/streamer/`：`streamer_brain.py`（门面：ingress/polish/egress，纯收发）、`channel.py`（渠道抽象，阶段1 已建）、`session.py`（会话，阶段1 已建）、`inbox.py`（统一收件箱，阶段1 已建）；
6. 门面层接入 `inbox`（`STREAMER_NEW=1` 走 `streamer_ingress`）；LangGraph `build_graph` 三处加法演进（入口注入渠道/会话、LLMState 加 session_key、streamer_worker 门面润色），三脑节点内部不动；
7. bili 渠道从"HTTP POST 回流"改为"域内直接投主播脑 inbox"（去掉 HTTP 中转）；webui/voice/cli/api 渠道齐备。

### P3 — 能力层复用与多会话验证
8. 操作请求、表情、TTS 副作用走 `skill_assembly`；验证直播间观众 + WebUI 用户同时多会话并发不互相干扰。

## 7. 配置

```json
// skill_worlds.json 追加
"hub": {
  "enabled": true,
  "channels": {"bili": true, "webui": true, "cli": true, "voice": false, "api": true},
  "session": {"max_history": 20, "expire_min": 60}
}
```

## 8. 验证

- **P1**：WebUI 聊天与 B 站弹幕都能驱动主播脑，行为与旧 LangGraph 等价（回归）。
- **P2**：门面层 + LangGraph 演进上线后，多渠道消息串行/并行正确；多会话互不串扰；操作请求照常走 operator 域；回复按渠道路由正确。
- **P3**：直播弹幕 + WebUI 双用户同时交互，情感/记忆/奖励各自归属正确。

## 9. 风险与取舍

- **回归风险低**：不推翻 LangGraph，三处演进均为加法（入口/会话/润色），三脑节点内部不动；P1 并行期（LangGraph 保留、渠道抽象先行）进一步降低风险。
- **并发模型**：主播脑接口层用 per-session 锁 + 全局回复器保序，语义对齐旧 `_message_queue` worker（避免 Live2D/TTS 并发死锁的老问题）。
- **复用优先**：`supervisor_route_node`/`doubao_chat_node` 的 LLM 封装、记忆/RAG/情感/奖励逻辑全部保留，只改"编排"层，不动模型与数据层。
- **不绑定直播**：重构后主播脑是通用交互体，直播只是 bili 渠道；将来任何输入渠道 = 加一个 Channel。

## 10. 实现与修改方案（落地清单，按阶段）

> 本文档定稿架构后，实现分 4 阶段执行。每阶段独立可验证、可回滚。

### 阶段 0 — bili 渠道域（独立落地，先行）
按 [bili_extension_plan.md](./bili_extension_plan.md) 实施，目标：B 站监听成为 `bili` 能力域，但**仍走旧 HTTP 回流**，主播脑暂不动。
- 新增：`LLM/bili/{bili_world,bili_engine,bili_listener,bili_processor,bili_tools}.py` + `__init__.py`
- 修改：`main.py`（`start_bilibili_listener` 改为激活 bili 域）、`LLM/skill_worlds.json`（加 `bili` 段）
- 验证：`bili_observe` 能读弹幕；`handle_post_request` 行为与现状一致（回归）。

### 阶段 1 — 渠道抽象 + 标准化消息（LangGraph 并行保留）
- 新增：`LLM/streamer/channel.py`（`Channel` 抽象：`ingress(msg)` / `egress(reply, sess)`）、`message.py`（标准化消息 dataclass：`channel/msg_type/user/content/ts/meta`）、`session.py`（`SessionManager`：`(channel,user)→ChatState`，复用 `context_control` 裁剪）
- 修改：`webui/app.py` `/api/send`（[#L413](file:///e:/GitHub/LLM_vtuber/webui/app.py#L413)）改为构造标准化消息投 `streamer.inbox`；`main.py` `handle_post_request` 收窄为"转标准化消息入 inbox"；`cli` 渠道（stdin）接入
- 保留：LangGraph `build_graph` 原路径不动（并行）；inbox 同时喂旧队列与 LangGraph（双写开关 `STREAMER_NEW=0` 默认旧路径）
- 验证：WebUI 聊天、CLI 都能驱动旧 LangGraph 主播脑，行为等价（回归）。

### 阶段 2 — 门面层 + LangGraph 演进（不推翻编排引擎）
新增 `LLM/streamer/`（门面层，核心实现）：
| 文件 | 职责 | 关键接口/复用 |
|---|---|---|
| `streamer_brain.py` | **主播脑门面**：ingress + L0 规则模板 + polish + egress | `StreamerFront.ingress(msg)`；L0 正则/白名单（免费）；polish 小模型/模板，persona 来自配置 |
| `small_brain.py` | **本地小模型简单思考**（Qwen2.5-0.5B，CPU） | `simple_think(msg, sess) -> {reply, need_big}`：直接答简单问题 + 判断是否需要大模型 |
| `session.py` | 会话管理 | `(channel,user)→Session`，per-session 锁（阶段1 已建） |
| `inbox.py` | 统一收件箱 | `submit(StreamerMessage)`，`STREAMER_NEW=1` 时走门面层（阶段1 已建） |
| `egress.py` | 渠道输出路由 | 复用 `channel.py` 内置渠道（cli/webui/bili） |

修改（演进 LangGraph，三处加法，不动三脑节点内部）：
- `main.py`：门面层接入 `inbox`（`STREAMER_NEW=1` 时 `streamer_ingress` 统一进 LangGraph 编排层）；**演进1** `create_default_llm_state` 注入 `channel/reply_to/session_key`
- `LLM/LLM_node.py`：**演进2** `LLMState` 增加 `session_key`，`run_with_messages` 前按会话拼接历史；`supervisor_route_node` 等节点内部不动
- `streamer_worker`（门面末轮）：**演进3** 调 `streamer_polish` 做 persona 情感化润色
- `webui/app.py` / bili 域：阶段2 后从"HTTP POST 回流"改为**直接投 `streamer.inbox`**（去掉 HTTP 中转）
- 验证：多渠道消息串行/并行正确；简单问题由门面直接答、复杂问题进 LangGraph；操作请求走 operator 域；回复按渠道路由；与旧路径同输入同场景行为对照。

### 阶段 3 — 多会话与门面体验打磨
- `persona` 配置化（口吻/情绪/口头禅）；`streamer_polish` 按 persona + 情绪值润色
- 直播观众 + WebUI 用户同时多会话互不串扰验证；情感/记忆/奖励各自归属正确
- 输出渠道路由完整：bili 发弹幕 / WebUI WS / TTS / CLI / API

### 配置变更汇总（`skill_worlds.json`）
```json
"streamer": {
  "enabled": true,
  "channels": {"bili": true, "webui": true, "cli": true, "api": false, "voice": false},
  "persona": {"name": "…", "tone": "活泼", "emotion_default": 0.5},
  "session": {"max_history": 20, "expire_min": 60},
  "small_model": {"model": "Qwen/Qwen2.5-0.5B-Instruct", "device": "cpu",
                  "max_new_tokens": 96, "enabled": true},
  "mode": "legacy"     // "legacy" 旧直通 LangGraph | "streamer" 门面层+大小模型协同，阶段 2 后置 "streamer"
}
```

### 回滚策略
- 阶段 1/2 均以 `streamer.mode`（或 `STREAMER_NEW` 环境变量）双轨切换，`legacy` 即旧行为，随时可回退。
- LangGraph 节点不删除、仅不挂载；LLM 封装函数保留供三脑复用。
