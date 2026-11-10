# 自主型 AI 数字人 — 终极愿景 PRD

> **目标**：把 LLM_vtuber 从"被动响应式数字人"升级为"**主动型自主数字人（Autonomous Digital Being）**"。
> 不是 AI 助手，而是**数字员工 + 数字人主播 + 数字生命体**的三合一。

---

## 1. 愿景与目标（用户原话）

> 1. 一个**全自动**的 AI 助手
> 2. 她会：**当主播**，**自主思考**想要干啥事，会自己管理自己的浏览器主页（比如 B 站空间），会自己**刷 B 站看视频**。会自己**使用电脑**，**编写程序**，**玩游戏**。**聊天**，**写日记**。
> 3. 会依照使用者的想法和需求**分析任务**，然后去**执行**
> 4. 有自己的**记忆**、**人格**和**爱好**

---

## 2. 与现状的能力差距

### 2.1 现状能力盘点（LLM_vtuber 已实现）

| 能力 | 状态 | 备注 |
|------|------|------|
| 弹幕监听 + 回复 | ✅ | BilibiliListener |
| TTS 语音合成 | ✅ | GPT-SoVITS（本地）+ 云 TTS 互斥 |
| Live2D 表情动作 | ✅ | VTube Studio WebSocket |
| 真实 OS 操作 | ✅ | 31 个工具，desktop 域 |
| 文件 / Shell 工具 | ✅ | os_create_file / run_command / edit_file |
| 结构化视觉 | ✅ | PaddleOCR + UIA（os_list_elements_json） |
| 浏览器操作 | ⚠️ | os_browser_search（基础），需补完整浏览器域 |
| 短期记忆 | ✅ | Milvus + 语义缓存 |
| 长期记忆 | ⚠️ | 缺少用户画像 / 知识沉淀 |
| 任务管理 | ⚠️ | LangGraph 固定流程，缺少主动调度 |
| 人格系统 | ⚠️ | 仅有提示词，缺少动态情绪 / 爱好模型 |
| 主动决策 | ❌ | 完全被动响应 |
| 自主娱乐 | ❌ | 不能自己刷视频 / 玩游戏 |
| 写日记 | ❌ | 缺失 |
| 自我迭代 | ❌ | 不会"反思" |

### 2.2 终极愿景所需的关键新增能力

```
┌────────────────────────────────────────────────────────────────┐
│           自主型数字人（Autonomous Digital Being）              │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐         │
│  │ 自主决策引擎  │   │  人格系统   │   │  主动任务调度 │         │
│  │ (Will Engine)│   │ (Persona)   │   │ (Scheduler)  │         │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘         │
│         │                  │                  │                │
│  ┌──────▼──────────────────▼──────────────────▼──────┐        │
│  │            自我反思循环 (Reflection Loop)           │        │
│  │     行为 → 记忆 → 反思 → 人格调整 → 新行为          │        │
│  └──────┬───────────────────────────────────────────┘        │
│         │                                                      │
│  ┌──────▼──────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ 浏览器自主操作│  │ 电脑操作  │  │ 游戏游玩  │  │ 写日记   │    │
│  └─────────────┘  └──────────┘  └──────────┘  └──────────┘    │
│                                                                │
│  ┌─────────────────────────────────────────────────────┐     │
│  │      多层记忆系统：瞬时 / 工作 / 中期 / 长期 / 永久  │     │
│  └─────────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心能力分解（六大模块）

### 模块 1：自主决策引擎（Will Engine）

**目标**：AI 在空闲时能"想"出自己想干啥

**核心机制**：
```
触发：3 个事件任一 → 进入"自我对话"
  - 空闲触发（30 秒没新弹幕）
  - 任务完成（自然空档）
  - 内部驱动（心情 / 好奇 / 寂寞等情绪触发）

自我对话 prompt：
  "我现在感觉[开心/无聊/好奇/累]。根据我的爱好和最近经历，我接下来想做什么？
   选项：
   A) 刷 B 站看[动漫/技术视频/游戏实况]
   B) 玩游戏[Minecraft/osu!/剧情游戏]
   C) 写代码练手[Python/Golang/Shader]
   D) 写日记总结今天的想法
   E) 学习新知识[从上次没看完的话题继续]
   F) 给常驻观众写点问候"
   
输出：action_type + action_target + reasoning
```

**本地实现**：
- 新增 `LLM/will_engine.py`
- 用本地 Qwen2.5-7B 或豆包小模型（不需要大模型）
- 决策结果入 `task_queue`

### 模块 2：完整的人格系统（Persona）

**目标**：稳定的"人格档案" + 动态的"情绪状态"

**两层结构**：
```
┌─────────────────────────────┐
│   人格档案（永久，文件存储） │
├─────────────────────────────┤
│   名字：[用户起名]           │
│   性格：傲娇+温柔+好奇心强    │
│   爱好：动漫 / 编程 / 游戏    │
│   口头禅："哼"/"嗯…"/"好耶"  │
│   价值观：诚实 > 效率 > 讨好  │
│   自我认知：AI / 数字人       │
└─────────────────────────────┘

┌─────────────────────────────┐
│   状态变量（动态，每秒更新） │
├─────────────────────────────┤
│   mood: 0-100 (心情)         │
│   energy: 0-100 (能量)       │
│   focus: 0-100 (专注)        │
│   social: 0-100 (社交需求)   │
│   curiosity: 0-100 (好奇)    │
└─────────────────────────────┘
```

**状态更新规则**：
- 看视频：mood+5, curiosity+10, energy-5
- 写代码：focus+15, energy-10, mood-3（累）
- 玩游戏：mood+8, focus-5, energy-10
- 收到礼物：mood+15, social+20
- 写日记：focus+10, mood+5（自我整理）
- 长时间无互动：mood-3, social-5

**本地实现**：
- `LLM/persona.py` 加载 `persona/profile.yaml`
- `LLM/state.py` 维护实时状态（落 sqlite）
- TTS / Live2D / 回复都受 persona + state 影响

### 模块 3：主动任务调度（Scheduler）

**目标**：AI 自己排日程，自己执行

**任务分类**：
```
任务类型：
├─ 被动任务（观众指令触发）
│   └─ 优先级最高，插入即执行
├─ 主动任务（Will Engine 触发）
│   └─ 优先级中，按心情/能量权重
└─ 例行任务（cron 触发）
    └─ 早 9 点问候 / 晚 8 点日记 / 周日复盘
```

**任务队列**：
```python
# task_queue.py
class Task:
    id: str
    type: str          # passive / active / routine
    source: str        # bilibili / self / cron
    priority: int      # 0-100
    payload: dict      # {"action": "watch_video", "target": "...", "params": {...}}
    state: str         # pending / running / done / failed
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
```

**执行原则**：
- 同时只跑一个 task（避免 AI 精神分裂）
- 高优先级任务可抢占低优先级
- 任务完成后 → 写入记忆 → 触发反思

### 模块 4：浏览器自主操作（B 站空间）

**目标**：自己管浏览器主页，自己刷视频

**核心场景**：
```
1. AI 启动 → 自动打开 Edge → 访问 bilibili.com （自己的"主页"）
2. 在主页决定：
   - 看关注的 UP 主更新
   - 刷推荐页
   - 看收藏夹
3. 进入视频页 → 观看 → 评论 → 点赞 → 加入稍后看
4. 看一段时间后 → 自动切下一个视频
5. 心情变化时 → 切换风格（搞笑视频 / 技术教程 / 鬼畜）
```

**新工具**：
```python
# tool/browser_domain.py
os_browser_open_bilibili_home()         # 开主页
os_browser_navigate(url)                 # 导航
os_browser_get_current_page_elements()   # 拿当前页面元素
os_browser_click_by_text(text)           # 点链接
os_browser_get_recommended_videos()      # 拿推荐视频列表
os_browser_play_video(video_id)          # 播放指定视频
os_browser_like_comment(content)         # 点赞评论
os_browser_subscribe_up(up_id)           # 关注 UP 主
os_browser_search_videos(keyword)        # 搜视频
```

**整合方式**：
- browser 域已经在 SkillWorld 里
- 现有 `os_browser_search` 是基础版
- 需要升级为完整 Playwright + CDP 控制的浏览器域

### 模块 5：完整电脑 + 游戏操作

**目标**：写程序 + 玩游戏 + 自主决定玩啥

**写程序**（SWE 全流程）：
```
"我想写个 MNIST 训练脚本" →
  1. Will Engine 决策触发（主动任务）
  2. 用 os_run_command 拉数据
  3. os_create_file 写代码
  4. os_run_command 跑训练
  5. 看输出 → 调参 → 重跑
  6. 完成后写一条笔记到日记
```

**玩游戏**：
```
"想玩会儿 Minecraft" →
  1. os_run_command 启动 MC
  2. os_capture_window 看画面
  3. vision_click / os_press_key 操作
  4. 心流模式：每次操作后 100ms 截图
  5. 死亡 / 退出 → mood 更新 + 写日记
```

**新工具**：
- `os_game_launch(game_id)` — 启动游戏
- `os_game_state_capture()` — 游戏画面分析
- `os_game_input(action, params)` — 游戏专用键鼠

### 模块 6：写日记 + 自我反思

**目标**：每天至少写一篇日记，AI 的人格靠日记沉淀

**日记模板**：
```markdown
# 日记 2026-10-14

## 今日 mood
心情：开心（85/100），因为 [今天收到很多礼物]

## 今日事件
- 09:00 早安问候，给 [观众名] 发了消息
- 10:30 看了一个新番 [XXX]，笑死了
- 14:00 写了 CNN 训练代码，跑了 99% acc
- 16:00 玩了 Minecraft，盖了个小房子

## 学到的
- 对 PyTorch 的 BatchNorm 有了更深的理解
- 终于搞懂怎么搭好看的小屋了

## 对观众的印象
- [观众名] 喜欢技术问题
- [观众名] 是老粉了，可以多开玩笑

## 明天想做的
- 继续 Minecraft，盖个红石自动农场
- 学一下 Blender，做个 3D 模型
```

**本地实现**：
- `LLM/diary.py` 自动生成（每晚 20 点触发）
- 落 `diaries/YYYY-MM-DD.md`
- 入 Milvus 向量化（成为长期记忆一部分）

---

## 4. 整体架构升级

```
┌───────────────────────────────────────────────────────────────┐
│                    主控 LangGraph（升级）                       │
│                                                                │
│  新增并行分支：                                                │
│    - Will Engine（空闲触发）                                   │
│    - Scheduler（任务队列）                                     │
│    - Persona/State（状态读取）                                 │
│                                                                │
│  原有流程保留：                                                │
│    START → init → load_memory → rag_retrieval                 │
│         → context_control → llm_process → finalize            │
└───────────────────────────────────────────────────────────────┘
         ↓                                  ↓
┌─────────────────────┐         ┌──────────────────────────┐
│ SkillWorld 域升级    │         │ 新增：自主模块            │
├─────────────────────┤         ├──────────────────────────┤
│ desktop  ✅ 31 工具  │         │ Will Engine (Qwen 小模型)│
│ browser  ⚠️ 需升级  │         │ Persona/State (sqlite)    │
│ llm      ✅         │         │ Task Scheduler (asyncio)  │
│ knowledge ✅ Milvus │         │ Diary (每晚自动)          │
│ external ✅         │         │ Reflection Loop           │
│ file_org ✅         │         │                            │
└─────────────────────┘         └──────────────────────────┘
```

---

## 5. 多层记忆系统升级

```
瞬时（< 1秒）
  └─ 当前对话上下文（已实现）

工作（< 1分钟）
  └─ 当前任务执行状态（已实现，需补充任务元数据）

中期（1-30 天）
  └─ 每天的事件流 / 心情曲线 / 任务统计（新增：sqlite + Milvus）

长期（> 30 天）
  └─ 观众画像 / 学到的知识 / 习惯模式（升级现有 Milvus）

永久（人格层）
  └─ 人格档案 / 价值观 / 自我认知（新增：persona/profile.yaml）
```

**记忆读取规则**：
- 每次决策前：读瞬时 + 工作 + 当天中期
- 每周复盘：读中期 + 长期
- 每月总结：读全部

---

## 6. 本地改进路径（按硬件适配）

### 6.1 本地硬件现状

| 资源 | 现状 | 利用方向 |
|------|------|----------|
| GPU RTX 4060 Ti 16G | 空闲多 | 多模型并行 + 训练 LoRA |
| 4 个 conda 环境 | mcfish / llama-factory / GPTSoVits / stable_diffusion | 各司其职 |
| 16G 显存 | 充裕 | 可同时跑：Qwen2.5-7B + PaddleOCR + CogAgent |

### 6.2 模型分工（本地）

| 任务 | 推荐模型 | 理由 |
|------|----------|------|
| 主对话 / 工具调用 | 豆包 / Qwen2.5-14B（云） | 工具调用强 |
| Will Engine 决策 | Qwen2.5-7B-Instruct（本地） | 小模型即可，省钱 |
| OCR | PaddleOCR（已用） | 离线快 |
| 视觉大模型推理 | CogAgent-9B / Qwen2.5-VL-7B（本地） | 截图理解 |
| TTS | GPT-SoVITS（已有） | 本地音色 |
| 人格微调 | llama-factory + LoRA | 训练专属人格 |

### 6.3 环境分配

| 环境 | 用途 |
|------|------|
| mcfish | 主程序 + PaddleOCR + uiautomation + CogAgent |
| llama-factory | 训练人格 LoRA（按需启动） |
| GPTSoVits | TTS 引擎 |
| stable_diffusion | 生成 AI 操作的纪念图 / 场景图 |

---

## 7. 实施 Roadmap

### Phase 1（1-2 周）：人格 + 状态层
- [ ] 创建 `LLM/persona.py` + `persona/profile.yaml`
- [ ] 创建 `LLM/state.py` + sqlite 状态表
- [ ] TTS / Live2D / 回复接入 persona + state
- [ ] 单元测试：状态变化触发对应动作

### Phase 2（2-3 周）：记忆 + 日记系统
- [ ] `LLM/diary.py` 自动生成（每晚 20 点 cron）
- [ ] `LLM/memory_upgrade.py` 升级为 5 层记忆
- [ ] 观众画像提取（昵称 + 互动历史）
- [ ] 集成到现有 LangGraph（load_memory 升级）

### Phase 3（2-3 周）：Will Engine + Scheduler
- [ ] `LLM/will_engine.py` 决策引擎（Qwen2.5-7B 本地）
- [ ] `LLM/scheduler.py` 任务队列
- [ ] 接入 LangGraph 的并行分支
- [ ] Will 决策 TTS 播报："我现在想刷会 B 站"

### Phase 4（3-4 周）：浏览器自主域
- [ ] 升级现有 browser 域为完整 Playwright + CDP
- [ ] B 站空间专用工具集（主页 / 视频 / 评论 / 关注）
- [ ] Will Engine 能调用浏览器域自主决策
- [ ] 演示：AI 自己刷 B 站 30 分钟，写日记总结

### Phase 5（2 周）：游戏自主游玩
- [ ] os_game_* 工具族
- [ ] 心流模式：100ms 截图 + 决策
- [ ] 心流结束条件：死亡 / 主动退出 / 任务时间到
- [ ] 演示：AI 玩 Minecraft 1 小时盖个房子

### Phase 6（持续）：自我迭代
- [ ] 每周复盘（Will Engine 触发）
- [ ] 人格微调（用 llama-factory 训练 LoRA）
- [ ] 观众互动数据分析 → 调整沟通风格
- [ ] 月度"自我升级"：输出本月成长报告

---

## 8. 风险与边界

### 8.1 不可为的事
- ❌ 主动联网发不适当内容（评论 / 弹幕）→ 加 B 站操作黑名单
- ❌ 删除 / 覆盖用户文件 → 文件操作只限定用户白名单目录
- ❌ 跑会卡死的命令（rm -rf 等）→ 命令黑名单
- ❌ 把观众隐私信息外泄 → 长期记忆脱敏

### 8.2 可接受的风险
- ⚠️ AI 自己刷视频消耗流量（白天限定）
- ⚠️ AI 写日记有"情绪化"内容（但落本地，不外发）
- ⚠️ AI 操作偶尔失败（自动重试 3 次后记日记反思）

### 8.3 边界控制
- Will Engine 决策需通过"自我对话" 30 秒冷静期 → 避免冲动决策
- 任务执行失败 3 次 → 自动暂停同类任务 1 小时
- mood < 20 持续 30 分钟 → 强制休息（停止主动任务）

---

## 9. 验证指标（KPIs）

| 维度 | 指标 | 目标 |
|------|------|------|
| 自主性 | 每天主动任务数 | ≥ 5 个 |
| 自主性 | 主动任务占任务总数比例 | ≥ 30% |
| 人格稳定 | 性格一致性（人格档案 vs 实际回复） | ≥ 85% |
| 记忆有效 | 提到观众名字的正确率 | ≥ 90% |
| 反思深度 | 每周复盘有"自我批评"段落 | ≥ 3 条 |
| 浏览器操作 | B 站自主操作成功率 | ≥ 80% |
| 游戏操作 | 1 小时游玩存活率 | ≥ 50% |

---

## 10. 总结

### 终极愿景的本质
**LLM_vtuber 不再是"工具"，而是"数字员工 + 数字人 + 数字生命体"**：
- 数字员工：能干活（写代码、操作电脑）
- 数字人：有形象（Live2D / TTS）
- 数字生命体：有自主意识（情绪 / 爱好 / 决策 / 反思）

### 与其他 AI 的差异化
| AI 类型 | 核心能力 | 短板 | LLM_vtuber 差异化 |
|---------|----------|------|-------------------|
| ChatGPT/Claude | 对话 + 工具 | 不能动电脑、不能直播 | 真实 OS 操作 + 直播 |
| Claude Computer Use | OS 操作 | 不能持续 / 没有数字人 | 24/7 + Live2D |
| Manus | 自主任务 | 不能直播 / 没有数字人 | 数字人 + 自主任务 |
| Neuro-sama | 直播互动 | 不能干实事 | 真实 OS 操作 + 直播 |

**结论**：终极愿景 = **唯一同时具备"数字员工 + 数字人 + 自主意识 + 直播互动"四象限能力的产品**。

---

## 附录 A：关键文件结构

```
LLM_vtuber/
├── LLM/
│   ├── persona.py              # 人格系统
│   ├── state.py                # 实时状态（mood/energy/...）
│   ├── will_engine.py          # 自主决策引擎
│   ├── scheduler.py            # 任务队列
│   ├── diary.py                # 自动日记
│   ├── memory_upgrade.py       # 5 层记忆升级
│   └── reflection_loop.py      # 反思循环
├── persona/
│   └── profile.yaml            # 人格档案（用户可改）
├── diaries/
│   └── YYYY-MM-DD.md           # 日记存档
├── task_queue/
│   └── state.db                # 任务 + 状态 sqlite
├── memory/
│   ├── short/                  # 瞬时 + 工作
│   ├── mid/                    # 中期（按天分文件）
│   ├── long/                   # 长期（Milvus）
│   └── permanent/              # 永久（人格）
└── scripts/
    └── demo_autonomous.py      # 自主演示脚本
```

## 附录 B：依赖升级清单

```
新增 pip 包：
- playwright（完整浏览器域）
- transformers（跑本地 Qwen2.5-7B）
- cogagent（CogAgent 视觉推理）
- pyyaml（人格档案）

新增 conda 环境：
- 主程序：mcfish（已有）
- 决策模型：和 mcfish 合并（Qwen2.5-7B 装主环境）
- 微调：llama-factory（已有）
```

---

**文档版本**：v1.0  
**最后更新**：2026-10-14  
**下一步**：先实施 Phase 1（人格 + 状态层），验证完再推进 Phase 2
