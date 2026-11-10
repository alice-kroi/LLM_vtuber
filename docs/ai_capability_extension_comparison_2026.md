# AI 能力扩展开源项目调研与对比（2026-Q4 截稿）

> 研究范围：2025-2026 年**高性能 AI 控制电脑**与**AI 感知/UI Grounding**方向的开源项目，对照可缇（Cortico）与本地 LLM_vtuber。数据时效：所有外部信息均加 `as of 2026-Q3/Q4` 标注；半年以上未复查的标 `[unverified]`。

---

## 一、行业语境：四股主线

| 主线 | 关键命题 | 2026 现状 |
|---|---|---|
| ① **原生 GUI Agent** | 不依赖 DOM 选择器，端到端操控 OS/Web | 字节 UI-TARS-2 OSWorld **47.5%**（1500 步内），[cite:1] Claude Opus 4.6 OSWorld-Verified **72.7%**（100 步），[cite:7] GPT-5.x / ScaleCUA **75.0%** [cite:1] |
| ② **UI 感知 / 屏幕解析** | 截图→结构化元素→可点击坐标 | OS-Atlas 跨平台 GUI grounding 基础模型 [cite:23]；OmniParser V2 成为"感知底座"事实标准 [cite:22][cite:24][cite:25] |
| ③ **AI 扩展/能力域管理** | 域的热启停、错误隔离、配置持久化 | Cortico WorldAssembly（preflight/persist/restart）；Matimo 的 Policy Engine + YAML 热重载 [cite:19][cite:20]；Qwen Code 扩展 `/extensions` slash 命令热重载 [cite:21] |
| ④ **AI VTuber / 数字陪伴** | 长时存在 + 实时语音 + 表情/口型 + 主动行动 | Project AIRI 47,939★、Minecraft/Factorio 游戏智能体 [cite:18]；Open-LLM-VTuber 10,447★、Letta 长记忆 + 回声消除 [cite:14][cite:15] |

> 边界：主线 ① ② 直接对位本地 `desktop` 域；主线 ③ 直接对位本地 `SkillWorld` 装配层（已实现热启停/隔离/持久化）；主线 ④ 对位本地"AI 主播+桌宠+Live2D"整体形态。

---

## 二、多维对比矩阵

### 2.1 Computer Use（电脑操控能力）

| 项目 | 模型/形态 | OSWorld 50 步 | OSWorld-Verified | WebVoyager 2026 | 部署 | License |
|---|---|---|---|---|---|---|
| **Claude Opus 4.6** | 闭源 API [cite:7] | — | **72.7%**（100 步） | — | SaaS | 商业 |
| **Claude Opus 4.8** | 闭源 API [cite:8] | — | OSWorld 2.0 20.6%/54.8% partial [cite:9] | — | SaaS | 商业 |
| **GPT-5.4 (OpenAI CUA)** | 闭源 Operator→Agent [cite:10] | 38.1% (full, May 2026 仍领先) | — | — | SaaS | 商业 |
| **UI-TARS-2-2509** | 开源 7B/72B [cite:1] | **47.5%** / 53.1% [cite:28] | — | 88.2% (Online-Mind2Web) | 本地/HF | Apache-2.0 |
| **UI-TARS-1.5** | 开源 [cite:3] | 24.6% (72B, 50步) | — | VisualWebBench **82.8%** [cite:6] | 本地 | Apache-2.0 |
| **Skyvern v2.0** | 开源 [cite:11] | — | — | **~85.85%** [cite:12] | 自托管/云 | AGPL-3.0 |
| **Browser-Use** | 开源 [cite:13] | — | — | ~89% [cite:12] | 自托管 | MIT |
| **Stagehand** | 开源 [cite:12] | — | — | ~89% | 自托管 | MIT |

> 本地 LLM_vtuber：22 个 `os_*`/`ui_*` 工具，OSWorld 类基准上**未参评**（脚本式坐标+UIA+视觉三角定位，无原生大模型基座）。

### 2.2 UI 感知 / Grounding

| 项目 | 形态 | 输出 | 用途 |
|---|---|---|---|
| **Microsoft OmniParser** [cite:22] | 检测+描述两阶段 | 元素 bbox + 功能语义 | 让 GPT-4V/4o 获得可点击坐标 |
| **OmniParser V2（阿里）** [cite:24] | 统一解析框架 | 结构化点 + 文本 | 多任务统一；DRS-GUI 等下游消费 [cite:25] |
| **OS-Atlas** [cite:23] | 跨平台 GUI grounding 基础模型 | 元素坐标 | Win/Linux/macOS/Android/Web |
| **DRS-GUI** (CVPR 2026) [cite:25] | 训练免费 GUI grounding | 区域 + 语义 | 直接接 V2 作 UI Perceptor |
| **本地 ui_perception 域** | Doubao 视觉 API + UI Automation | 文本/坐标 | 当前主链路；可接 OmniParser V2 作本地兜底 |

### 2.3 能力扩展/装配（域管理框架）

| 框架 | 热启停 | 错误隔离 | 持久化 | 预建实例 | Policy | License |
|---|---|---|---|---|---|---|
| **Cortico WorldAssembly** | ✅ activate/deactivate/restart | ✅ slots/missing | ✅ persist 深合并 | ✅ addPrebuilt | ❌ | MIT |
| **Matimo** [cite:19][cite:20] | ✅ hot-reload | ✅ policy.can_reload/can_create | YAML | — | ✅ 策略引擎+人工审批 | MIT |
| **Qwen Code /extensions** [cite:21] | ✅ CLI 热重载 | — | 配置 | — | — | Apache-2.0 |
| **byoa-core** | ✅ activate/deactivate API | — | Skill 系统 | — | — | — |
| **LLM_vtuber SkillWorld（已升级）** | ✅ set_world_enabled async | ✅ 构造/聚合/执行三层 try/except | ✅ persist 写回 `skill_worlds.json` | ❌ | ❌ | 项目内 |

### 2.4 AI VTuber / 数字陪伴

| 项目 | 离线性 | 实时打断 | 长期记忆 | 屏幕/相机视觉 | 桌宠/游戏 | Live2D/VRM | Stars | License |
|---|---|---|---|---|---|---|---|---|
| **Project AIRI** [cite:18] | ✅ 自托管 | ✅ 实时语音 | ✅ | ✅ | ✅ Minecraft/Factorio | Live2D+VRM | **47,939★** | MIT |
| **Open-LLM-VTuber** [cite:14][cite:15] | ✅ 全离线 | ✅ 浏览器端 AEC 消除自声 | ✅ Letta/EVI | ✅ 屏幕+相机 | ✅ 桌宠 | Live2D Cubism 5 | 10,447★ | MIT |
| **Neuro-sama** | ❌（闭源） | ✅ | — | ✅ | ✅ 多游戏 | Live2D | n/a | 商业 |
| **LLM_vtuber** | 部分（GPT-SoVITS 本地） | ✅ TTS 流水线 | ✅ langchain memory | ✅ 视觉分析+窗口截屏 | ❌ | ✅ Live2D+桌宠 | n/a | 项目内 |

---

## 三、SWOT — 三方对照

### 3.1 LLM_vtuber

| | 正面 | 负面 |
|---|---|---|
| **内部** | **S** — SkillWorld 装配层已对齐 Cortico（含 preflight/persist/restart/缺失槽位/异步启停）；22 桌面工具覆盖打开/截图/坐标/视觉/剪贴板全链路；操作脑三脑分层（streamer/supervisor/operator） | **W** — 缺原生大模型基座（GUI Agent 能力靠豆包/UI 自动化补偿）；未参评 OSWorld/WebVoyager 等公共基准；离线栈弱（依赖豆包 Vision API） |
| **外部** | **O** — VTuber 赛道仍在高速演进，桌宠+游戏智能体是增长点；OmniParser V2 已是事实"感知底座"可作本地兜底 [cite:24] | **T** — Project AIRI/Open-LLM-VTuber 同赛道明星项目体量与社区远超（万级 vs 千级 stars）；UI-TARS-2 开源性能正快速逼近闭源 [cite:1] |

### 3.2 Cortico（可缇）

| | 正面 | 负面 |
|---|---|---|
| **内部** | **S** — 事件系统 trigger 四态（preempt/flush/debounce/piggyback）；工具回执 endsTurn/barrierAfter 流控；ENV_PROMPT 三层覆盖；Cortina AI 扩展创建器 | **W** — TS 生态对小项目集成门槛高；事件 cursor/run 双轨较复杂 |
| **外部** | **O** — B 站可缇Corti 真实用户反馈驱动迭代 | **T** — 同赛道被 Project AIRI（更大社区）反超 |

### 3.3 UI-TARS-2（字节）

| | 正面 | 负面 |
|---|---|---|
| **内部** | **S** — OSWorld 47.5% / WindowsAgentArena 50.6% / AndroidWorld 73.3% / Online-Mind2Web 88.2% [cite:1]；System 2 推理+统一动作建模 [cite:3]；Apache-2.0 全栈开源（模型+训练栈+UI-TARS-desktop 0.3.x） | **W** — 7B 起步显存门槛（L40S 48G 推荐）；非思考-行动链外的复杂任务仍有瓶颈（OSWorld 2.0 仍 < 人类 [cite:9]） |
| **外部** | **O** — "未见过站点/应用"评测是行业新基线 | **T** — Claude Opus 4.6 在 100 步设置仍 72.7% 领先开源 [cite:7]；ScaleCUA 等自进化路线在追赶 |

---

## 四、定位地图（功能深度 × 生态广度）

```
                    功能深度高
                        │
       Claude Opus 4.6  │   UI-TARS-2
       (闭源 SOTA)      │   (开源 SOTA)
                        │
   ─────────────────────┼───────────────────── 生态广度高
   Skyvern (AGPL,Web    │   Project AIRI (MIT, 47k★
   自动化专精)          │   全栈数字陪伴)
                        │   Open-LLM-VTuber (10k★)
   ─────────────────────┼─────────────────────
                        │
   域管理专精           │   LLM_vtuber
   Cortico / Matimo     │   (域+主播+三脑)
   (高内聚)             │   (中型功能、中型生态)
                        │
                    功能深度低
```

LLM_vtuber 落在"中型功能 + 中型生态"象限，是国内 B 站直播主控场景的纵深玩家，但**生态广度**与**原生大模型基座**是明显短板。

---

## 五、战略推演与建议（1-12 个月）

| 线索 | 信号 | 推断意图 | 置信度 | 对 LLM_vtuber 的建议 |
|---|---|---|---|---|
| UI-TARS-2 开源 OSWorld 47.5% [cite:1] | Apache-2.0 全栈 + 多端基准 SOTA | 字节把"原生 GUI Agent"做成基础设施 | 高 | `desktop` 域引入 UI-TARS-2 作"高难度任务规划层"，现有 22 工具降为"动作执行层" |
| OmniParser V2 成为 CVPR 2026 DRS-GUI 默认底座 [cite:25] | 行业共识收敛 | 视觉 grounding 走向本地化、低依赖 | 高 | `ui_perception` 域接 OmniParser V2（YOLO 39MB 权重、wheels 内嵌）作本地兜底，减少豆包调用 |
| Matimo 的 Policy.can_reload/can_create [cite:20] | 工具级审批、灰度上线 | "能力扩展"从工程命题进入治理命题 | 中 | SkillWorld 装配层加 `policy_gate`（启用/停用钩子），为敏感域（launcher/pvz/桌面）走人工审批 |
| AIRI 47,939★ + 游戏智能体 [cite:18] | Neuro-sama 风格开源复刻成为流量入口 | 桌宠/游戏是 VTuber 增长点 | 中 | 评估"游戏智能体域"作为下一个新域（独立于 pvz），框架用 AIRI/Minecraft-Factorio 风格接口 |
| OpenAI Operator→Agent（38.1% OSWorld，2026-05 仍领先）[cite:10] | 闭源 GUI 商业化 | 头部模型公司把 CUA 当入口产品 | 中 | 主播脑 prompt 增加"何时把高难度电脑操作委派给 Operator/UI-TARS"判断分支 |
| Cortico trigger 四态 | 流控需求真实存在 | 投递时序影响体验 | 高 | StreamerInbox 补 preempt/flush/debounce/piggyback 四级（已在上一份建议清单中） |
| 工具回执 endsTurn/barrierAfter | 流式 + 多工具并发需要"我先停一下" | — | 高 | ToolOutcome 补两个布尔位（已在建议清单） |

---

## 六、对 LLM_vtuber 现状的差距与可立即吸取的项

| 维度 | 现状 | 行业做法 | 行动 |
|---|---|---|---|
| GUI Agent 基座 | 脚本坐标+视觉，无原生大模型 | UI-TARS-2 7B 本地 / Claude Opus 4.6 API | 引入 UI-TARS-2 作"规划层"，保留 22 工具作"执行层"；形成规划-执行分层 |
| 视觉感知 | 豆包 Vision API（云端） | OmniParser V2 本地 YOLO 39MB | `ui_perception` 域接 OmniParser V2，截图→元素 JSON 离线完成 |
| 域管理 | 已对齐 Cortico WorldAssembly | Matimo 的 Policy Engine | 增 `policy_gate`（人工审批/灰度） |
| 工具回执 | 简单 dict | endsTurn/barrierAfter | ToolOutcome 增两个布尔 |
| 事件 trigger | 双轨（NEW/STREAMER） | preempt/flush/debounce/piggyback | StreamerInbox 补四态 |
| 离线性 | 部分（GPT-SoVITS 本地）+ 云（豆包/GPT） | 全离线栈成熟（Open-LLM-VTuber/AIRI） | 评估把"视觉/感知"链路迁到本地 OmniParser V2 + Qwen2-VL/SmolVLM |
| 桌宠/游戏 | 仅桌宠 | 桌宠+游戏智能体（AIRI/Neuro-sama） | 评估"游戏域"作为下一波 |

---

## 七、Sources

- [1] arXiv 2509.02544 — *UI-TARS-2 Technical Report* (ByteDance, Sept 2025) — <https://arxiv.org/pdf/2509.02544>
- [2] arXiv 2501.12326 — *UI-TARS: Pioneering Automated GUI Interaction with Native Agents* — <https://arxiv.org/pdf/2501.12326>
- [3] ByteDance Seed Blog — *UI-TARS-1.5 开源* — <https://seed.bytedance.com/zh/blog/bytedance-seed-agent-model-ui-tars-1-5-open-source-achieving-sota-performance-in-various-benchmarks>
- [4] ui-tarsai.com — <https://ui-tarsai.com/>
- [5] AnnounceHQ — *UI-TARS-desktop v0.3.0-beta.8* — <https://announcehq.com/ui-tars-desktop/ffb4f303-649d-4a85-8245-d625a269d523/v0-3-0-beta-8>
- [6] agentconn.com — *UI-TARS-desktop Review* — <https://agentconn.com/blog/bytedance-ui-tars-desktop-review/>
- [7] Anthropic — *Claude Opus 4.6 System Card* (OSWorld-Verified 72.7%) — <https://www-cdn.anthropic.com/c788cbc0a3da9135112f97cdf6dcd06f2c16cee2.pdf>
- [8] Anthropic — *Claude Opus 4.7 / 4.8 release notes* — <https://www.anthropic.com/claude/opus>
- [9] *OSWorld 2.0: Benchmarking Computer Use Agents on Long-Horizon Real-World Tasks* — <https://s46486.pcdn.co/wp-content/uploads/2022/01/OSWorld2.0.pdf>
- [10] presenc.ai — *OpenAI Operator Update Tracker (2026)* — <https://presenc.ai/research/openai-operator-update-tracker-2026>
- [11] Skyvern Blog — *9 Browser Use Alternatives Compared for 2026* — <https://www.skyvern.com/blog/browser-use-alternatives/>
- [12] aiworkflowlab — *Browser Use vs Stagehand vs Skyvern (2026)* — <https://aiworkflowlab.dev/article/browser-use-vs-stagehand-vs-skyvern-vs-computer-use-2026>
- [13] agenticindex — *Browser Use vs Skyvern* — <https://agenticindex.io/compare/browser-use-vs-skyvern>
- [14] Open-LLM-VTuber Docs — <http://docs.llmvtuber.com/>
- [15] andrew.ooo — *Open-LLM-VTuber Review* (AEC + emotionMap) — <https://andrew.ooo/posts/open-llm-vtuber-offline-ai-companion-review/>
- [16] AIRI Docs — <https://airi.moeru.ai/docs/zh-Hans/docs/overview/>
- [17] moeru-ai-airi.mintlify — <https://moeru-ai-airi.mintlify.app/introduction>
- [18] funailab — *Project AIRI (47,939★)* — <https://funailab.com/github-spotlight-project-airi-the-open-source-engine-for-self-hosted-ai-vtubers/>
- [19] matimo.dev — <https://matimo.dev/>
- [20] docs.matimo.dev — <https://docs.matimo.dev/>
- [21] Qwen Code — *Extensions* — <https://qwenlm.github.io/qwen-code-docs/en/users/extension/introduction/>
- [22] Microsoft — *OmniParser* — <https://microsoft.github.io/OmniParser/>
- [23] arXiv 2410.23218 — *OS-ATLAS: A Foundation Action Model for Generalist GUI Agents* — <https://arxiv.org/pdf/2410.23218v1>
- [24] arXiv 2502.16161 — *OmniParser V2 (Alibaba Research)* — <https://arxiv.org/pdf/2502.16161v2>
- [25] CVPR 2026 — *DRS-GUI: Dynamic Region Search for Training-Free GUI Grounding* — <https://openaccess.thecvf.com/content/CVPR2026/papers/Liu_DRS-GUI_Dynamic_Region_Search_for_Training-Free_GUI_Grounding_CVPR_2026_paper.pdf>
- [26] Open-LLM-VTuber GitHub (10,447★) — <https://docs.llmvtuber.com/en/intro/>
- [27] seed-tars.com — *UI-TARS-2B* — <https://seed-tars.com/1/>
- [28] arXiv 2608.25777 — *LocalLSTC*（UI-TARS-2-2509 OSWorld 53.1%）— <https://arxiv.org/html/2608.25777>
- [29] Toutiao — *FrontierCode 1.1 (Opus 5.5 综合 52.2%)* — <http://m.toutiao.com/group/7696354851634053667/>
