# B 站「AI 玩 B 站」开源参考调研

> **目的**：为 LLM_vtuber 的 `bili_video` / `bili_live` 域找外部参考，看社区是怎么做"AI 读 B 站 / 写 B 站"的。
> **背景**：用户观察"最近 B 站的 AI 变多了"——本调研同时给出这个现象背后的**工具化原因**与**监管现状**。
> **日期**：2026-10-20 ｜ 状态：调研记录（含待验证项，见 §6）

---

## 0. 先说结论

1. **"AI 变多"是工具化的直接结果**：评论自动回复机器人已经开源到"填个 API Key 就能跑"的程度（BiliCommentBot 就是典型），门槛从"会写代码"降到"会改配置文件"。
2. **社区实现高度收敛成同一套架构**：`弹幕/评论接入 → 关键词/规则路由 → LLM 兜底 → 限流 → 回写`。这与我们 V2 的「能力域 → 意志脑路由 → 主播脑发声」在骨架上一致，差别在我们的**三脑分工更严**、而社区普遍是单进程流水线。
3. **最有借鉴价值的是两个工程细节**，而不是模型用法：
   - **"固定回复优先于 AI"**（命中规则就不走 LLM）——等价于我们的"规则优先 + API 兜底"，可反向验证我们的 `rule → api` 分层是对的；
   - **AI 回复必须带发送间隔/队列上限**——社区把它当第一等公民，我们目前只有 1200ms 请求间隔（针对读接口）。
4. ⚠️ **合规红线（最重要）**：中央网信办 2026 年 4 月起的「清朗·整治 AI 应用乱象」专项行动**明确把"使用 AI 群控软件、社交机器人刷量控评，生成虚假流量数据，制造虚假舆论热点"列为整治对象**。这与我们项目记忆里"B 站相关功能仅限个人自用、不对外提供服务"的约束完全吻合——**个人自用可做，拿去刷量/控评/养号绝对不可做**。

---

## 1. 项目分类总览

| 类别 | 代表项目 | 做什么 | 对我们的价值 |
|---|---|---|---|
| **A. 全栈 AI 主播** | Open-LLM-VTuber、AI-Vtuber(Luna AI)、LiveStream 数字人直播 | 弹幕接入 + LLM + Live2D + TTS 全家桶 | **架构对照**：看别人怎么切模块 |
| **B. 单点工具** | BiliBili-AI-Danmaku、BiliCommentBot、BiliVoice、神奇弹幕、弹幕姬 | 只做一件事（回复/语音/场控） | **细节参考**：限流、登录、队列、路由 |
| **C. API / 协议层** | bilibili-api、bilibili-API-collect、blivedm、bilibili-mcp | 封装 B 站接口与弹幕协议 | **取舍参考**：哪条路已经断了 |
| **D. 学术** | LiveBot（B 站弹幕数据集） | 研究报告与数据集 | 历史资料，非工程参考 |

---

## 2. 逐项调研

### 2.1 Open-LLM-VTuber（最著名的开源 AI VTuber）

- **仓库**：`Open-LLM-VTuber/Open-LLM-VTuber`（作者 t41372，80+ 贡献者）｜**文档**：docs.llmvtuber.com
- **能力**：Live2D 形象 + 语音对话 + 视觉感知（摄像头/录屏/截图）+ MCP 工具调用 + 浏览器自动化 + **B 站直播弹幕客户端** + 长期记忆
- **B 站接入架构**（关键）：**独立进程 + WebSocket 代理**
  ```
  Bilibili 直播客户端（独立进程，用 blivedm 抓弹幕）
        ↓  /proxy-ws（WebSocket 代理端点）
  Open-LLM-VTuber 主进程（AI / TTS / 核心）
        ↓
  前端 UI（Live2D + 回复展示）
  ```
- **配置形态**：单一 YAML（`config_templates/conf.default.yaml`），直播段是
  ```yaml
  live_config:
    bilibili_live:
      room_ids: [1991478060]
  ```
- **v1.2.0 新增**：**长期记忆基于 Letta**、MCP 支持、Cubism 5、中文前端、B 站弹幕客户端
- **可借鉴**：
  1. **弹幕接入独立进程**——我们已经是这样（`bili_live` 域是独立域，且有子进程协议）；
  2. **代理端点把"平台输入"与"AI 处理"解耦**——我们的 `EventBus` 做得比它更彻底（连 TTS/Live2D 都走事件）；
  3. ⚠️ **它用 Letta 做长期记忆**——正好是我们在意志脑设计里**评估后放弃**的那条路（推荐本地自研四件套）。它的这个选择值得当作反向验证：我们放弃的理由是"需额外常驻服务"，而它作为通用桌面应用没有"本地单机+已有 Milvus/SQLite"这个约束，所以路径不同不矛盾。
- **与我们的关系**：**它是通用 AI 伴侣**（本地语音对话为主），我们是**三脑 + 能力域的数字主播**；它的直播功能是"接进来一个弹幕源"，我们的是"能力域热插拔 + 意志脑决策"。定位不同，但它的 B 站客户端实现（blivedm）值得单独看。

### 2.2 BiliBili-AI-Danmaku（与我们的目标最接近）

- **仓库**：`xgxdmx/BiliBili-AI-Danmaku`｜宣传语：**弹幕监听 + 关键词精准匹配 + 大模型智能回复 + 以弹幕形式回发直播间**
- **定位**：完全开源、本地运行、数据不出本机；Python 3.13 + Node 24 + Electron 42 + Vue 3
- **架构**（值得逐字读）：
  ```
  📺 B站直播间 ──WebSocket──> 🐍 Python 弹幕核心 ──stdio──> ⚡ Electron 主进程
                                                 │
                                    ┌────────────┼────────────┐
                                    ▼            ▼            ▼
                               🎯 关键词    ⚡固定回复    🤖 AI 中继
                                                              │
                                                       ┌──────┴──────┐
                                                       ▼             ▼
                                                🌐 OpenAI API   🐳 Ollama(本地)
  ```
- **数据流（6 步）**：
  1. Python 核心 WebSocket 连 B 站弹幕服务器实时接收
  2. 弹幕经 **stdio JSON-RPC** 传给 Electron 主进程
  3. 关键词过滤器按 `scope` 路由
  4. **固定回复优先级高于 AI，命中后直接发送并阻止 AI 重复回复**
  5. AI 回复经**发送间隔控制**后回传 Python，以弹幕形式发回
  6. GUI 可视化配置与状态监控
- **`scope` 设计（很实用）**：`both`（固定回复 + AI）/ `quickReply`（仅固定回复处理命中项，AI 处理其余）/ `ai`（仅 AI）
- **登录态**：独立 `auth-window` 做**扫码登录并自动提取 Cookie**；配置**加密存储**（`config-store.ts`）
- **可借鉴（对我们的直接映射）**：

  | 它的机制 | 我们的对应物 | 结论 |
  |---|---|---|
  | 固定回复优先于 AI（命中即不走 LLM） | `will_engine._route_by_keywords` 规则优先 → API 兜底 | **已验证同向**，我们的分层是对的 |
  | 关键词 `scope` 三态路由 | 意图分类 + 工具路由 | 可考虑引入"仅规则 / 仅 AI / 两者"的显式开关，便于调试 |
  | AI 回复的**发送间隔 + 队列上限 + 跳过规则** | 我们只有读接口 1200ms 间隔 | **缺口**：写接口（发评论/回复）应有独立的发送节流与队列 |
  | 扫码登录自动提取 Cookie | 我们走 `passport.../cookie/info` 自动取 `bili_jct` | 我们有 CSRF，但**没有扫码登录流程**（依赖手工 SESSDATA） |
  | 配置加密存储 | 我们强制环境变量 | 我们更符合开源安全基线（记忆中的硬性约束） |
  | Python 核心 ↔ Electron 走 JSON-RPC over stdio | 我们的 `SubprocessCapabilityDomain` 也是 JSON 行协议 | **架构同源**，我们的更通用 |

### 2.3 BiliCommentBot（"AI 变多"的直接推手）

- **仓库**：`Janson20/BiliCommentBot`（B 站视频《人人都能拥有自己的"Doro"！B站评论 AI 自动回复机器人开源了！》）
- **能力**：自动监控**视频新评论**（不是直播间弹幕）+ 集成 DeepSeek API + **内置频率监控与优化** + 配置简单
- **为什么它值得单独记**：它把"AI 评论机器人"的门槛压到了配置级——这就是"最近 B 站 AI 变多"最直接的技术解释。
- ⚠️ **同时是最需要引以为戒的项目**：一旦被规模化使用去评论任意视频，就落在 §0 提到的"社交机器人刷量控评"整治范围内。
- **对我们的意义**：它的监测对象是**视频评论区**而非直播间——**这正是我们 `bili_video` 域的战场**。我们的 `bv_video_comments`（只读）可以对照它的轮询/去重策略；但**写评论能力应当谨慎**（见 §5）。

### 2.4 BiliVoice（弹幕语音播报，与我们的 tts 域同题）

- **仓库**：`Candlend/bili_voice`｜FastAPI + Next.js 14 桌面应用（内置 WebView）
- **能力**：播报弹幕/礼物/舰长/SC，一键对接 **GPT-SoVITS WebUI**，可选自动启动外部 WebUI
- **登录**：扫码 / 短信 / 密码三种，且**带极验（Geetest）流程**
- **工程细节**：
  - `protos/` 目录放 **B 站相关 Protobuf 转译**（用于解析直播事件）——说明社区走的是**解析二进制协议**而非纯 JSON 接口
  - **TTS 队列带优先级**（SC / 礼物优先播报）
  - 文本模板可自定义（普通弹幕/礼物/舰长/SC/进场/关注/分享/点赞）
  - 登录态只存 Cookie 子集到 `app_data/credential.json`
- **可借鉴（映射到我们的 `tts` 域）**：
  1. **TTS 优先队列**——高价值消息（SC/礼物）优先播报，我们目前是 FIFO；
  2. **模板化文案**——不同事件类型不同措辞，这与我们"意志脑决定说什么"是两条路，可作对照；
  3. **protobuf 解析**——如果我们以后要更稳地拿弹幕，这条比 JSON 接口更抗变动；
  4. ⚠️ 它的 README 自述"**99% 代码由 GPT-5 生成**"，且**不包含模型训练**——可直接对标我们 `bili_live` 的成熟度。

### 2.5 神奇弹幕 MagicalDanmaku / 弹幕姬（功能面参考）

- **神奇弹幕**：Qt/C++ 直播场控机器人（**6.0.0，2026-03**），自称"弹幕姬 + 答谢姬 + 回复姬 + 点歌姬"，含扣字小游戏（扣1禁言、关注解禁、礼物优先点歌）、防喷禁言、原画录播、粉丝数据统计。GitHub 受限，主要经 **gitcode 镜像**分发。
- **弹幕姬（danmuji.org）**：自称"开源、简洁的 B 站直播弹幕工具"，强调"多次优化弹幕处理逻辑、再多的弹幕也保证一个不丢、被断开自动重连"，并特别声明**它是"原官方钦定弹幕"工具**（即与 B 站官方生态有渊源）。
- **可借鉴**：这两者给出了**直播间运营的完整功能面**——提示我们"AI 玩 B 站"不止"读+写"，还有**点歌、答谢、数据统计、防喷**等运营动作。可作为我们后续 `bili_video` / `bili_live` 能力扩展的需求清单。
- **注意**：它们绝大多数能力是**直播间场控**（我们已有 `bili_live`），而**视频侧**（我们 `bili_video` 的主战场）社区工具很少——这是个空白位。

### 2.6 API / 协议层的可用性（关键取舍信息）

| 项目 | 现状 | 影响 |
|---|---|---|
| `MoyuScript/bilibili-api` | **原仓库已被删除** | 与项目记忆"2026 年因律师函关停"一致 |
| `Nemo2011/bilibili-api`（主 fork） | **GitHub 仓库已归档（archived）**，Issues 已关闭 | **不可作为依赖**；但 `bilibili-api-python` 包仍在 PyPI（GPLv3） |
| `SocialSisterYi/bilibili-API-collect` | 文档站仍可访问（有镜像） | 仅作**接口语义参考**，不是可用依赖 |
| `blivedm` | Open-LLM-VTuber 等仍在用其抓直播弹幕 | 直播侧仍可用 |
| `bilibili-mcp`（多个实现，如 shirenchuang/bilibili-mcp） | 有 MCP 封装（评论、用户互动、音频转录、反检测、浏览器池） | ⚠️ 其文档自述"**回复评论/发私信谨慎使用，B 站社区规则对此类自动化比较敏感，频繁触发容易被限流**" |

> **结论**：**依赖链已断**。这再次印证我们"自行实现 WBI 签名 + 自动取 buvid3/bili_jct + 1200ms 限流"的路线是当下唯一可行解，而不是过度造轮子。

### 2.7 LiveBot（学术，非工程参考）

- 论文 `arXiv:1809.04938`《LiveBot: Generating Live Video Comments Based on Visual and Textual Contexts》，代码 `lancopku/livebot`
- 用 **B 站 2,361 个视频 / 895,929 条弹幕**做"自动生成直播评论"任务，是 B 站弹幕研究最经典的公开数据集
- 另有一篇 rebuttal（`arXiv:2006.03022`）指出其评测有问题
- **对我们的意义**：若以后要训练"看视频生成弹幕"类模型，这是唯一公开的大规模底座；但**不是工程参考**（2023 年后无维护，是做研究用的冻结产物）。

---

## 3. 横向对比

| 维度 | Open-LLM-VTuber | BiliBili-AI-Danmaku | BiliCommentBot | BiliVoice | **LLM_vtuber（我们）** |
|---|---|---|---|---|---|
| 定位 | 通用 AI 伴侣 + 直播 | 直播间弹幕回复 | 视频评论回复 | 弹幕语音播报 | **三脑数字主播 + 能力域** |
| 弹幕接入 | blivedm + 独立进程 + WS 代理 | WebSocket（Python 核心） | 评论轮询 | WebSocket + protobuf | **独立能力域 + EventBus** |
| 事件模型 | WS 代理转发 | stdio JSON-RPC | 轮询 | 进程内 | **EDA 事件总线（24+ 事件）** |
| 路由 | 交给 LLM | 关键词 `scope` 三态 | LLM 直接生成 | 模板 | **意志脑：规则优先→API 兜底** |
| 回复通道 | LLM → TTS/UI | 弹幕（HTTP API） | 评论回复 | TTS 播报 | **主播脑 → TTS 域 / BILIBILI_REPLY** |
| 限流节流 | 未强调 | **发送间隔 + 队列上限 + 跳过规则** | 内置频率监控 | TTS 优先队列 | 读接口 1200ms；**写接口缺独立节流** |
| 登录 | 房间号即可（看弹幕） | **扫码登录自动取 Cookie** | 未详 | 扫码/短信/密码 + 极验 | SESSDATA（手工）+ 自动取 bili_jct |
| 长期记忆 | Letta | 无 | 无 | 无 | **意志脑四件套（设计完成）** |
| 合规立场 | 未提及 | 强调本地、数据不出机 | 强调"安全稳定" | 未提及 | **仅个人自用、不对外服务** |

---

## 4. 对我们的可复用清单

按"性价比"排序，均为**思路借鉴**而非代码拷贝（多数项目许可与依赖链不宜直接引）：

| # | 借鉴点 | 来源项目 | 落点 | 优先级 |
|---|---|---|---|---|
| 1 | 写接口的**独立发送节流 + 队列上限 + 跳过规则** | BiliBili-AI-Danmaku | `bili_video` 写工具（`bv_like`/`bv_coin`/`bv_fav`/未来评论） | P0 |
| 2 | **TTS 优先队列**（SC/礼物 优先播报） | BiliVoice | `tts` 域 | P1 |
| 3 | 关键词路由引入 **scope 三态**（仅规则/仅 AI/两者）便于调试与灰度 | BiliBili-AI-Danmaku | `will_engine._route_by_keywords` | P2 |
| 4 | **protobuf 解析**直播事件（抗协议变动） | BiliVoice | `bili_live` 弹幕解析 | P2 |
| 5 | 直播间运营功能面清单（点歌/答谢/防喷/统计） | 神奇弹幕 / 弹幕姬 | `bili_live` 需求池 | P3 |
| 6 | 扫码登录流程（免手工 SESSDATA） | BiliBili-AI-Danmaku / BiliVoice | `bili_video` 凭据获取 | P3（收益有限，维持手工） |
| 7 | 弹幕数据集底座（若做弹幕生成模型） | LiveBot | 训练侧 | 观望 |

**明确不采纳**：直接依赖 `bilibili-api`（**已归档**）、`bilibili-API-collect` 文档（仅参考）、任何"群控/批量评论"类框架（**合规红线**）。

---

## 5. 风险与红线

| 风险 | 依据 | 我们的对策 |
|---|---|---|
| ⚠️ **合规红线** | 中央网信办 2026-04 起「清朗·整治 AI 应用乱象」，明确整治"**使用 AI 群控软件、社交机器人刷量控评，生成虚假流量数据，制造虚假舆论热点**" | **严格守住**项目记忆既有约束：仅个人自用、不对外提供服务、不做数据聚合替代官方客户端、不公开传播 API 文档/SDK。个人小规模读写**不是**"刷量控评"，但**不可批量、不可多账号、不可代他人操作** |
| 依赖链断裂 | `Nemo2011/bilibili-api` 已 archived；原仓库已删除 | 继续自行实现签名与请求逻辑（**现状正确**） |
| 写操作被限流/风控 | bilibili-mcp 文档自述"回复评论/发私信谨慎，频繁触发容易被限流" | 写接口加节流 + 队列 + 上限（见 §4 #1）；保留现有 -412 冷却 3s / -111 CSRF 重试策略 |
| 单点工具化的社会外部性 | BiliCommentBot 把门槛降到配置级 → "AI 变多" | 不做**他人可见的自动化写操作**（发评论/私信）除非明确个人自用场景；优先做"读+总结+播报" |
| 归档项目的教程污染 | 网上大量教程仍引用已归档/已删除的 API 库 | 团队内部统一以"自行实现"为准，避免照抄失效教程 |

---

## 6. 待验证项（本轮未完全确认，标记以免误用）

1. **Open-LLM-VTuber 的 star 数与最新版本号**——仅从二手文章确认 v1.2.0 及 Letta/MCP 特性，未取得仓库实时数据。
2. **BiliBili-AI-Danmaku 的许可证与最近提交**——README 抓取到内容但未读到 License 段与提交时间。
3. **`bilibili-api-python` 在 PyPI 是否仍在更新**——仅确认包存在（GPLv3），未核对最近发布日期。
4. **神奇弹幕的 GitHub 主仓库是否也受限**——目前只见 gitcode 镜像在分发。
5. **MagicalDanmaku 与弹幕姬的功能清单**——来自二手中文介绍，未逐条对照官方 README。

---

## 7. 参考链接

**项目**
- [Open-LLM-VTuber（GitHub）](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber)
- [Open-LLM-VTuber 直播集成开发文档](http://docs.llmvtuber.com/en/docs/development-guide/live-dev/)
- [Open-LLM-VTuber B 站直播接入指南](http://docs.llmvtuber.com/en/docs/user-guide/live/bilibili/)
- [Open-LLM-VTuber 默认配置模板（live_config / bilibili_live）](http://raw.githubusercontent.com/Open-LLM-VTuber/Open-LLM-VTuber/main/config_templates/conf.default.yaml)
- [BiliBili-AI-Danmaku（弹幕监听 + AI 自动回复）](https://github.com/xgxdmx/BiliBili-AI-Danmaku)
- [BiliCommentBot（B 站评论自动回复机器人）](https://github.com/Janson20/BiliCommentBot)
- [BiliVoice（弹幕语音播报 / GPT-SoVITS）](https://github.com/Candlend/bili_voice)
- [AI-Vtuber / Luna AI（多平台虚拟主播）](https://gitee.com/pinezh/AI-Vtuber)
- [数字人直播开源项目 LiveStream 技术栈](https://jishuzhan.net/article/2083873791720964098)
- [bilibili-mcp（MCP 封装，含风险提示）](https://kimigao.com/blog/bilibili-mcp/)
- [bilibili-mcp（LobeHub 收录）](https://lobehub.com/pt-BR/mcp/shirenchuang-bilibili-mcp)
- [B站弹幕姬 danmuji](https://www.danmuji.org/)
- [神奇弹幕 MagicalDanmaku（gitcode 镜像）](https://gitcode.com/gh_mirrors/bi/Bilibili-MagicalDanmaku)
- [Nemo2011/bilibili-api 归档状态（RepositoryStats）](https://repositorystats.com/nemo2011/bilibili-api)
- [bilibili-api-python（PyPI，GPLv3）](https://pypi.org/project/bilibili-api-python/)
- [SocialSisterYi/bilibili-API-collect（文档参考）](https://git-hub.com/SocialSisterYi/bilibili-API-collect)

**学术**
- [LiveBot: Generating Live Video Comments Based on Visual and Textual Contexts](https://arxiv.org/pdf/1809.04938)
- [Response to LiveBot（反驳）](https://arxiv.org/pdf/2006.03022.pdf)

**监管**
- [中央网信办部署开展"清朗·整治 AI 应用乱象"专项行动](https://www.cac.gov.cn/2026-04/30/c_1779289298718765.htm)
- [中央网信办深入开展专项行动第一阶段工作](https://www.cac.gov.cn/2026-07/06/c_1785081384384987.htm)
- [中央网信办深入开展专项行动第二阶段工作](https://www.cac.gov.cn/2026-09/02/c_1790099041364574.htm)
- [剑指生成"数字泔水"等 AI 应用乱象（新华网）](http://www.xinhuanet.com/20260430/eecf1b693ce14ef98b8eb7bd3b4d239d/c.html)
