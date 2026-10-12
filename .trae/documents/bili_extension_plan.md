# B 站直播监控扩展化改造方案（主播脑 ↔ 哔哩哔哩解耦）

> 文档日期：2026-10-11
> 目标：把"主播思维直接对接哔哩哔哩直播间"改为扩展——B 站直播监控（监听/去重/过滤/奖励/投递）整体挪进一个 SkillWorld 能力域，主播脑只消费标准化的观众消息流。
> 对齐：复用项目已有的 `SkillWorld` / `SubprocessSkillWorld` / `skill_worlds.json` / 自动发现机制（`LLM/<domain>/<domain>_world.py` + `build_<domain>_world()`）。

## 0. 结论摘要

当前主播脑与 B 站的耦合体现在两处：**① main.py 直接 Popen 启动 `broadcast/bili_main.py` 子进程**（[main.py#L2433](file:///e:/GitHub/LLM_vtuber/main.py#L2433)）；**② 弹幕处理全链路（去重→限流→奖励→入队）内嵌在 main.py 的 `handle_post_request`**（[main.py#L336](file:///e:/GitHub/LLM_vtuber/main.py#L336)），通过 webui `POST /` 收 bili_main 的 HTTP 转发。

改造后：新增 `bili` 能力域（`LLM/bili/`），把 blivedm 监听、去重、过滤、奖励采集、消息投递全部封装进该域的子进程引擎；主播脑侧不再感知"B 站/直播间/弹幕协议"细节，只从标准化消息流（`{type, username, content, gift…}`）消费。`start_bilibili_listener`/`handle_post_request` 中与 B 站强相关的逻辑迁出 main.py；`POST /` 保留为外部注入兼容口。

## 1. 现状梳理（改造对象）

### 1.1 弹幕监听进程
- 启动：`main.py` `start_bilibili_listener(room_id)`（[main.py#L2433](file:///e:/GitHub/LLM_vtuber/main.py#L2433)）→ `subprocess.Popen` 常驻 `broadcast/bili_main.py`；退出清理 `_bili_process`（[main.py#L157](file:///e:/GitHub/LLM_vtuber/main.py#L157)、[#2289-2304](file:///e:/GitHub/LLM_vtuber/main.py#L2289-L2304)）。
- 房间号：`args.room_id`，默认读 `broadcast/config.json` 的 `ROOM_IDS`（[main.py#L2799-2810](file:///e:/GitHub/LLM_vtuber/main.py#L2799-L2810)）。
- SESSDATA：`config.json` > 环境变量 > `config.ini`（`bili_main._resolve_sessdata`）。

### 1.2 弹幕处理全链路（内嵌 main.py）
`webui/app.py` `POST /`（[app.py#L273-287](file:///e:/GitHub/LLM_vtuber/webui/app.py#L273-L287)）→ `app_main.handle_post_request`（main.py）：
1. **去重**：`_recent_message_keys` 按 `uname|content|ts` 去重（[main.py#L245-263](file:///e:/GitHub/LLM_vtuber/main.py#L245-L263)）；
2. **限流/重要事件**：`broadcast.danmu_filter`（礼物/SC 放行、普通弹幕窗口限流，[main.py#L267-282](file:///e:/GitHub/LLM_vtuber/main.py#L267-L282)）；
3. **奖励采集**：`reward_tracker.on_messages`（[main.py#L284-289](file:///e:/GitHub/LLM_vtuber/main.py#L284-L289)）；
4. **入队**：`_message_queue`（上限 10，满丢旧）→ 队列 worker 串行消费 → LangGraph；
5. **附加**：WebSocket 广播弹幕、AI 读弹幕 TTS 触发（[main.py#L303-327](file:///e:/GitHub/LLM_vtuber/main.py#L303-L327)）。

### 1.3 主播脑（LangGraph）
- 节点：`streamer_request`（主播首轮：情感+操作请求）→ `supervisor`（主体思维，`LLM.LLM_node.supervisor_route_node`）→ `operator_worker` / `streamer_worker`（[main.py#L999-1030](file:///e:/GitHub/LLM_vtuber/main.py#L999-L1030)）。
- 弹幕经队列 worker（[main.py#L497-528](file:///e:/GitHub/LLM_vtuber/main.py#L497-L528)）喂给 LangGraph 的 `streamer_request` 入口。

### 1.4 现状问题
- 监控逻辑分散：监听在 `broadcast/`，处理逻辑散在 `main.py` 多个函数（去重/过滤/奖励/队列/TTS/WS 广播），新增直播间能力或换平台都要动 main.py。
- 主播脑与 B 站协议强耦合：`handle_post_request` 直接面对 bili_main 的 HTTP 转发格式。
- 启动/停止/状态管理游离于能力域体系外（与 launcher/pvz/desktop_agent 的 SkillWorld 机制不一致）。

## 2. 目标架构

```
┌─ 主播脑（LangGraph：streamer_request→supervisor→operator/streamer_worker）
│        ▲ 只消费"标准化观众消息流"
│        │  {type, username, content, gift_name, coin_type, ts, room_id, source:"bili"}
│        │  （投递/入队/TTS/WS 广播仍复用现有队列与回调）
├─────────────────────────────────────────────────────────────
│  bili 能力域（SkillWorld，子进程引擎，自动发现挂载）
│     blivedm 监听 ──去重──> danmu_filter 限流 ──> reward_tracker 奖励
│         └── 标准化消息流 → 主播脑消费（队列/事件）
│     配置：skill_worlds.json 的 "bili" 段（room_ids / sessdata / output…）
└─────────────────────────────────────────────────────────────
     main.py 仅保留：WebUI POST /（外部注入兼容口）+ 退出时停域
```

- **主播脑与 B 站解耦**：主播脑/`handle_post_request` 不感知 blivedm、房间号、弹幕协议；只面对统一的 `source:"bili"` 消息。
- **监控自治**：监听/重连/去重/限流/奖励都在 `bili` 域子进程内完成，随域启停。
- **可换平台**：将来加抖音/斗鱼 = 再注册一个 `douyin` 域，主播脑零改动。

## 3. 扩展设计

### 3.1 目录与文件（`LLM/bili/`）
```
LLM/bili/
├─ __init__.py
├─ bili_world.py          # BiliWorld(SubprocessSkillWorld) + build_bili_world()
├─ bili_engine.py         # 子进程引擎：JSON 行协议，分派 bili_* 工具；内部跑监听任务
├─ bili_listener.py       # blivedm 封装（由 broadcast/bili_main.py 迁移）：连接/重连/去重
├─ bili_processor.py      # 由 main.py 迁入：去重→danmu_filter→reward_tracker→标准化投递
├─ bili_tools.py          # BILI_TOOL_NAMES + BILI_TOOLS_SCHEMA
└─ (迁移后) broadcast/blivedm 保留为公共依赖；bili_listener 直接引用
```

### 3.2 工具集（prefix `bili_`）
| 工具 | 功能 |
|---|---|
| `bili_start` | 启动监听（room_id 可选，默认取 skill_worlds.json） |
| `bili_stop` | 停止监听 |
| `bili_status` | 监听/重连状态、当前房间、最近错误 |
| `bili_observe` | 当前直播状态 + 最近弹幕/礼物/SC 流（缓冲窗口，供 AI 查看） |
| `bili_send`（可选 P1） | 以配置的账号发弹幕 |

### 3.3 配置（`skill_worlds.json`）
```json
"bili": {
  "enabled": true,
  "room_ids": [71001],
  "sessdata_env": "BILIBILI_SESSDATA",
  "output": { "http_compat_port": 8081, "enabled": true },
  "poll_buffer": 200
}
```
- `room_ids`/`sessdata` 迁移来源：`broadcast/config.json` / 环境变量 / `config.ini`（bili 域保留同优先级解析，避免破坏现有 cookie）。
- `http_compat_port`：过渡期仍把消息 HTTP POST 到 8081（webui），保证不迁移 reward/TTS/WS 也能跑；迁移完成可关。

## 4. 主播脑对接方式

**推荐：投递兼容 + 消息加 `source`。** 最小侵入、主播脑零改动：
1. `bili` 域引擎内部完成 监听→去重→过滤→奖励；
2. 标准化消息通过现有 `_message_queue` 投递（引擎把消息写回队列，或经 webui `POST /` 兼容口回流）；
3. 每条消息打 `source:"bili"`，后续可按 source 路由（对齐 `world_extension_adaptation.md` 的 P1 事件 source 路由）。
4. 主播脑的 `streamer_request` 入口不变。

备选（更强解耦，P2）：主播脑订阅域事件流（`bili_observe` 轮询或事件回调），完全移除 `handle_post_request` 对 bili 的认知——留给后续是否需要多平台并行时做。

## 5. 迁移步骤

### P0 — 域骨架 + 监听入域（无功能回归）
1. 建 `LLM/bili/`：`bili_listener.py`（从 `bili_main.py` 迁 blivedm 连接/去重，输出改为"入域内缓冲 + 可选 HTTP 兼容口"）；
2. `bili_engine.py` + `bili_world.py` + `build_bili_world()` → 自动发现挂载；`skill_worlds.json` 加 `bili` 段；
3. `bili_start/stop/status/observe` 打通；`--smoke` + 真连房间验证弹幕能进缓冲。
4. main.py：`start_bilibili_listener` 改为"激活 bili 域"（`skill_assembly.start_world('bili')` + `bili_start`），保留退出清理。

### P1 — 处理链路迁域（去耦主体）
5. 把 `handle_post_request` 中的 去重→danmu_filter→reward_tracker→入队 逻辑迁入 `bili_processor.py`（域内执行）；
6. 消息标准化（`source:"bili"`）后经兼容口/队列回流主播脑；
7. `handle_post_request` 收窄为纯转发（外部注入兼容），不再内嵌 B 站逻辑。

### P2 — 主播脑完全解耦（可选，多平台时做）
8. 移除对 bili HTTP 格式的认知，主播脑改订阅域事件流；新增第二平台域（如 douyin）零改主播脑验证。

## 6. 配置迁移对照

| 项 | 现状 | 迁移后 |
|---|---|---|
| 房间号 | `broadcast/config.json` ROOM_IDS | `skill_worlds.json` bili.room_ids |
| SESSDATA | config.json/环境变量/config.ini | 保留多来源，`bili.sessdata_env` 指定 |
| 输出端口 | `config.json` output_port=8081 | `bili.output.http_compat_port` |
| 启动方式 | `start_bilibili_listener` Popen | `start_world('bili')` + `bili_start` |

## 7. 验证

- **P0**：`bili_world` 自动发现挂载；`bili_status` 显示已连接房间；`bili_observe` 能读到弹幕；主播脑回归（desktop 等其它域不受影响）。
- **P1**：真实弹幕/礼物/SC 依次经域处理入主播脑队列，行为与迁移前一致（去重/限流/奖励/TTS/WS 广播逐项对照）。
- **P2**：新增一个桩域（如 `douyin` 玩具域）验证主播脑零改动接入。

## 8. 风险与取舍

- **blivedm 协议更新风险**：`broadcast/blivedm/` 为社区库，随 B 站协议演进需更新——封装进域后更新面收敛到 `bili_listener.py` 单文件。
- **HTTP 兼容口保留**：不迁移 reward/TTS/WS 也能跑，是 P0 安全的过渡手段；迁移完成后可关闭，避免双写。
- **cookie 安全**：SESSDATA 属敏感信息，继续走环境变量/`config.ini`（`*.secret`/`.env` 忽略规则），不进 git。
- **不推翻主播脑**：只迁监控与投递，LangGraph 节点/边/主播脑 prompt 不动（对齐 `world_extension_adaptation.md` 最小侵入原则）。
