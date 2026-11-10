# LLM_vtuber 优化调研清单（2026-09-18）

> 来源：本地代码审查（Explore 子代理）+ 同类开源项目网络调研。
> 已有文档 `docs/optimization_from_reference_agents.md` 中的 P0-1/2/3 已落地，本清单为新增可落地项。

---

## 一、本地代码审查结论（文件级）

### P0（高价值低风险，建议优先）

**P0-A 事件循环被同步 LLM 调用整段阻塞（性能天花板）**
- 位置：`LLM/chat_model.py:167-193`（同步 `client.chat.completions.create`）、`:296`（同步流式）、`:225`（`time.sleep` 退避）
- 问题：LLM 生成 5-15 秒内 asyncio 循环被占死，uvicorn/WebSocket/弹幕队列全部停摆
- 改法：`_chat` 包 `asyncio.to_thread(...)`，或改用 AsyncOpenAI（`operator_agent.py:160` 已是此范式）；退避改 `await asyncio.sleep`
- 风险：低，仅改延迟与吞吐

**P0-B 语义缓存每次 get/put 都发同步 HTTP embedding 且阻塞**
- 位置：`LLM/semantic_cache.py:65-76`（`DoubaoEmbeddings().embed_query`）、`:106/:132`
- 问题：每条弹幕命中前/生成后各一次豆包 embedding 往返（0.2-0.5s），未命中也要付全价
- 改法：用本地 sentence-transformers（与 `LLM/LLM_node.py:154` 意图分类共用），或至少 `asyncio.to_thread` 包装
- 风险：低-中（本地 384 维 vs HTTP 2560 维，持久化需标注来源）

**P0-C WebUI/弹幕入口无鉴权且绑 0.0.0.0，可远程驱动电脑操作**
- 位置：`main.py:2408`（host="0.0.0.0"）；`webui/app.py:247-261`（POST /）、`:387-406`（/api/send）、`:909-951`（删 Milvus）、`:341-372`（改写 config.ini）、`:1290`（/ws）
- 问题：局域网任意主机可注入弹幕 → 直达 `os_click_xy/os_type_text/os_press_key` 等电脑操作工具；可删 Milvus collection/传 MinIO/改配置
- 改法：默认绑 `127.0.0.1`（配置可改）；`/api/*`、`/ws`、POST `/` 加 Bearer Token；破坏性操作要求 admin token
- 风险：低，安全收益极大

### P1（中价值）

**P1-A Milvus 每次操作都做 list_collections 健康检查 + webui 每请求新建客户端**
- 位置：`RAG/Millvus_base.py:122-130`、`:166-187`；`webui/app.py:742-744`
- 改法：健康检查加 TTL(30s)；webui 复用 `MilvusConnectionManager` 单例

**P1-B operator_agent 的 AsyncOpenAI 无超时**
- 位置：`LLM/operator_agent.py:160`
- 改法：`timeout=(5.0,60.0)`；失败接 `common/retry.py` 的 `run_with_retry`（基建已有，未接入）

**P1-C 每轮给 LLM 全量下发 ~20 个工具 schema**
- 位置：`LLM/LLM_node.py:569` `_get_all_tools()`
- 改法：按意图裁剪工具集（force_search→web/Edge；电脑操作→computer_use；normal/skip→空），白名单保留 tts/vision

**P1-D 弹幕去重窗口失效（`_RECENT_MSG_WINDOW` 死代码）+ 双集合重复**
- 位置：`main.py:183-187`（窗口常量未用）、`:237-243`（30s 整体 clear）；`webui/app.py:39-41`（另一套去重）
- 改法：改为 `{key: ts}` 按 5s 窗口惰性淘汰；webui 复用主程序去重

**P1-E 记忆/奖励/TaskManager 存储无限增长**
- 位置：`memory_manager.py:146-169`、`:68-80`（SQLite 每条弹幕 insert 无清理）；`LLM/task_manager.py:107/154`（`_tasks` 只增不减）
- 改法：每用户封顶最近 100 条；SQLite 定期清理；TaskManager 完成 1h 后摘除

**P1-F 意图/操作判定每消息重复计算 + supervisor 每消息刷桌面快照**
- 位置：`IntentClassifier.classify` 在 `main.py:1205` 与 `LLM_node.py:478` 各跑一次；`supervisor_route_node` 每条消息 `os_list_windows()`
- 改法：intent 在 `_streamer_request_node` 算一次写 state 共享；桌面快照加 5s 节流、仅操作请求时刷新

### P2（小成本）

- **P2-A** 优雅关闭：后台 `create_task` 无引用（`main.py:2809/1080/174`），finally 未 cancel；task_manager 无 shutdown → 统一引用列表 + cancel
- **P2-B** 配置双源：`config.ini [model]` 段主流程未用（改配置无效）；`audio/audio_main.py:47 REF_AUDIO_PATH` 硬编码绝对路径 → 注入 config
- **P2-C** 死代码与日志：删除 `webui/api.py`（与 app.py 重复，无导入）；`bili_main.log` 无轮转 → RotatingFileHandler；`_cleanup_port` 用 taskkill /F 可能误杀 → 仅杀含 bili_main/main.py 的非自身进程

### 当前最大 3 个薄弱点
1. 事件循环被同步 LLM/embedding 阻塞（吞吐/延迟根本瓶颈）
2. WebUI 0 鉴权 + 0.0.0.0 + 完整电脑操作工具链（最高危安全面）
3. 配置双源漂移 + 长期运行资源无上限

---

## 二、同类开源项目调研（可借鉴）

### 1. Open-LLM-VTuber（最相关，~13.8k stars）
- 语音优先模块化：VAD→ASR→LLM→流式断句→TTS→Live2D
- 借鉴：**流式边说边播**（pysbd 按句切分，首个逗号即入 TTS 队列）；**emomap 表情-动作协议**（LLM 输出 "set emotion: surprised" 驱动）；**think_tag 内心戏不播报**；主动发言 prompt
- 短板：B站直播实验性，逐条回复无合并；长时记忆临时移除

### 2. Ikaros-521/AI-Vtuber（~4.5k stars）
- 多 LLM+多 TTS+多形象全平台直播
- 借鉴：**完整过滤体系**（触发/过滤/违禁词/黑名单/双优先级）；**文案模式**（无弹幕时循环自言自语暖场）；**点歌队列**（可中途取消不阻塞对话）
- 短板：config 上千行混乱；重度依赖外部弹幕转发平台

### 3. morettt/my-neuro（Neuro-sama 复刻，~1k stars）
- 插件化工作台 + BarrageManager 弹幕队列
- 借鉴：**smart-barrage**（LLM 智能过滤弹幕：是否值得回复+情绪，非纯规则）；**随机节奏抖动**防机械感；实时打断
- 短板：Windows only、6 服务+Node 部署复杂

### 4. moeru-ai/airi（~49k stars）
- Neuro-sama 级陪伴，实时语音+游戏操作+RAG
- 借鉴：内嵌向量库长期记忆；Minecraft/Factorio 游戏操作（mineflayer、YOLO 视觉）——与本项目"操作脑"同源
- 短板：无 B站弹幕原生；TS+Rust 栈

### 5. jiran214/GPT-vup（停维护，~1.2k stars）
- 生产者-消费者 + 插件开关（speech/action/schedule/context）
- 借鉴：**action 插件**（观众互动→pyvts 动作映射表）；**schedule 定时事件**（讲故事/rap）；milvus 上下文
- 短板：GPT-3.5 时代、无流式

### 6. 其他
- **BiliBili AI 弹幕姬**：固定回复引擎毫秒响应 + sendIntervalMs 限频（地址待核实）
- **bili-bot**（GoFrame+Vue）：三级优先级消息队列 + 分层提示词（按观众等级），值得关注

### 对本项目最有价值的前 5 条借鉴
1. **流式边说边播**（Open-LLM-VTuber）：LLM 流式 token 按句切分，首个逗号即入 TTS，感知延迟降一个量级
2. **表情-动作协议化**（Open-LLM-VTuber）：emomap 关键词由 LLM 输出控表情/动作，与"内心戏不播报"分离
3. **LLM 智能弹幕过滤**（my-neuro）：降噪后做轻量 LLM 分类（值得回复/情绪/待办），替代纯规则
4. **闲时主动发言**（AI-Vtuber 文案模式 + proactive + 随机节奏）：冷场时由 LLM 决定何时主动开话题
5. **多级优先级队列+分层提示词**（bili-bot）：SC/礼物/舰长高优即时响应，进房通知批量降级；按观众等级分层提示词

---

## 三、建议落地顺序

1. **本轮即可做（P0）**：P0-A（to_thread/AsyncOpenAI 化 LLM）、P0-C（绑 127.0.0.1 + Token 鉴权）、P0-B（缓存走本地嵌入）
2. **短期（P1）**：P1-C 按意图裁剪工具、P1-D 去重统一、P1-B operator 超时、P1-F intent/快照节流
3. **中期（借鉴）**：流式边说边播（接现有 TTS）、emomap 表情协议、LLM 智能弹幕过滤、闲时主动发言
4. **长期**：P1-E/P2 资源上限与优雅关闭、多级优先级队列、分层提示词

> 注意：借鉴项（流式 TTS/emomap）依赖现有 TTS/Live2D 链路，需与本项目"前端直渲 Live2D 方案"(docs/frontend_live2d_direct_render_plan.md) 协同推进。
