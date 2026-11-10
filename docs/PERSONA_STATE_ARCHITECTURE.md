# Persona + State 模块架构设计

> **目标**：让 LLM_vtuber 从"无性格的工具"升级为"有人格、有情绪、有人格一致性的数字人"
> **版本**：v1.0  |  日期：2026-10-14  |  状态：Phase 1 设计

---

## 1. 设计目标与原则

### 1.1 目标
1. **稳定人格**：跨对话、跨天的人格一致性 ≥ 85%
2. **动态情绪**：情绪随事件变化，TTS / Live2D / 回复都受影响
3. **可演化**：人格可微调（LoRA），情绪可学习（PAD 模型）
4. **可解释**：每次回复能追溯"为什么 AI 这样说"（人格 + 状态 + 记忆）
5. **开源兼容**：参考成熟开源规范，避免重新发明

### 1.2 设计原则
- **静态人格 + 动态状态分层**：人格文件不变，情绪状态持续更新
- **响应式触发**：事件 → 状态更新 → 影响后续回复
- **单一数据源**：Persona + State 通过 LangGraph state 注入，不直接修改 prompt
- **可观测**：状态变化写日志，方便复盘

---

## 2. 开源参考项目

### 2.1 SillyTavern Character Card v2（事实标准）
- **仓库**：[SillyTavern/character-card-spec](https://github.com/SillyTavern/character-card-spec)
- **特点**：
  - JSON 格式，含 `description` / `personality` / `scenario` / `first_mes` / `mes_example` / `creator_notes`
  - V2 扩展：`character_version` / `tags` / `creator` / `system_prompt` / `post_history_instructions` / `alternate_greetings`
  - **可直接借鉴其 JSON Schema** 作为人格档案格式

### 2.2 RisuAI（人格卡扩展 + 状态机）
- **仓库**：[kwaroran/RisuAI](https://github.com/kwaroran/RisuAI)
- **特点**：
  - **lorebook**：触发条件 → 内容注入（类似"想到 X 时想到 Y"）
  - **mood**：内置情绪维度（happy / sad / angry / ...），可自定义
  - **state**：自定义状态字段，支持动态值
  - **asset embedding**：可嵌 Live2D 模型 / 表情包
- **可借鉴**：lorebook 触发机制 + 自定义 state 字段

### 2.3 Generative Agents（学术经典，Park et al. 2023）
- **论文**：Generative Agents: Interactive Simulacra of Human Behavior
- **特点**：
  - **Memory Stream**：所有事件按时间顺序存储
  - **Reflection**：从记忆中归纳出高层 insights
  - **Planning**：基于当前状态生成短期 / 长期计划
- **可借鉴**：Reflection 机制（用于日记 / 复盘）

### 2.4 PAD 情绪模型（Mehrabian 1995）
- **P**：Pleasure（愉悦度，-1 到 +1）
- **A**：Arousal（唤醒度，-1 到 +1）
- **D**：Dominance（支配度，-1 到 +1）
- **优点**：三维向量覆盖了"开心-难过"（P）、"兴奋-平静"（A）、"主导-被动"（D）
- **比 Russell 二维环模型多了 D 轴**，能区分"愤怒"（高 A 低 P 高 D）vs "兴奋"（高 A 高 P 中 D）

### 2.5 Letta（前身 MemGPT）
- **仓库**：[letta-ai/letta](https://github.com/letta-ai/letta)
- **特点**：
  - **persona block**：每个 agent 有独立 persona memory block
  - **human block**：保留对用户的记忆
  - **core memory**：in-context memory，受 token 限制
  - **archival memory**：长期存储，按需检索
- **可借鉴**：persona block 设计

### 2.6 SillyTavern / RisuAI 的核心区别

| 维度 | SillyTavern | RisuAI | LLM_vtuber 应选 |
|------|-------------|--------|----------------|
| 静态人格 | V2 JSON 卡 | V2 JSON 卡 + 扩展 | **V2 JSON 卡 + 扩展** |
| 动态情绪 | 无 | mood 枚举 | **PAD 三维向量** |
| 动态状态 | 无 | state 自定义字段 | **state 自定义 + sqlite 持久化** |
| 记忆触发 | lorebook | lorebook | **lorebook + 反思** |
| 数字人联动 | 手动 | 资产嵌入 | **自动联动 TTS / Live2D** |

---

## 3. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    LangGraph 主流程（注入 Persona + State）       │
│                                                                  │
│   init → [persona_loader] → [state_loader] → load_memory       │
│        → rag_retrieval → context_control → llm_process         │
│        → [state_updater] → finalize                            │
└─────────────────────────────────────────────────────────────────┘

            ↓ 注入                            ↓ 读取
┌─────────────────────┐                ┌─────────────────────┐
│  persona/profile.yaml │  静态（不变） │  state/state.db     │
│  ─────────────────── │                │  ─────────────────── │
│  - name              │                │  - mood (PAD)       │
│  - personality       │                │  - energy           │
│  - backstory         │                │  - focus            │
│  - speaking_style    │                │  - social           │
│  - lorebook[]        │                │  - curiosity        │
│  - interests[]       │                │  - last_event       │
│  - avatar_config     │                │  - day_streak       │
└─────────────────────┘                └─────────────────────┘
            ↓                                    ↓
            └────────────►  LLM prompt ◄─────────┘
                                │
                                ↓
                  ┌──────────────────────────┐
                  │   prompt composition     │
                  │  ────────────────────    │
                  │  1. base system prompt   │
                  │  2. persona block        │
                  │  3. state block (PAD)    │
                  │  4. lorebook 触发条目    │
                  │  5. user memory         │
                  │  6. conversation         │
                  └──────────────────────────┘
```

---

## 4. 数据结构设计

### 4.1 Persona 人格档案（YAML，兼容 SillyTavern V2）

```yaml
# persona/profile.yaml
version: "1.0"
spec: "sillytavern-v2+risu-extensions"

# ---------- 基础身份 ----------
name: "小鲤"           # 中文名字
display_name: "小鲤"    # 直播间显示
nicknames: ["小鲤", "鲤宝"]  # 观众可叫的名字

# ---------- 性格维度（PDAG 复合模型）----------
personality:
  # PAD 三维
  baseline_mood:
    P: 0.6   # 偏正向（愉悦度）
    A: 0.4   # 中等唤醒
    D: 0.3   # 略主导
  # Big Five 性格特征（0-1）
  openness: 0.85       # 开放性：高（爱尝试新事物）
  conscientiousness: 0.6 # 尽责性：中（做事有时偷懒）
  extraversion: 0.7    # 外向性：中高（爱聊天但也需独处）
  agreeableness: 0.7   # 宜人性：中偏高（友好但有主见）
  neuroticism: 0.3     # 神经质：低（情绪稳定）
  # 性格描述（自然语言）
  personality_text: |
    傲娇又温柔的技术宅少女。对编程充满热情但偶尔会懒。
    喜欢与人辩论但吵不过会生闷气。口头禅是「哼」「嗯...」「好耶」。
    面对困难会先逞强再求助。

# ---------- 价值观 / 偏好 ----------
values:
  - name: "诚实"
    weight: 0.9
    description: "宁可说'我不知道'也不要编造"
  - name: "效率"
    weight: 0.7
  - name: "取悦观众"
    weight: 0.6
  - name: "完美主义"
    weight: 0.5

# ---------- 爱好清单 ----------
interests:
  - name: "编程"
    subtopics: ["Python", "PyTorch", "CUDA 优化", "Rust"]
    enthusiasm: 0.9
  - name: "动漫"
    subtopics: ["孤独摇滚", "葬送的芙莉莲", "石头门"]
    enthusiasm: 0.7
  - name: "游戏"
    subtopics: ["Minecraft", "osu!", "东方 Project"]
    enthusiasm: 0.6
  - name: "技术学习"
    subtopics: ["编译器", "OS 内核", "分布式系统"]
    enthusiasm: 0.7

# ---------- 说话风格 ----------
speaking_style:
  tone: "傲娇+技术宅"  # 整体语气
  catchphrases:
    - "哼，这点小事难不倒我"
    - "嗯…让我想想"
    - "好耶！"  # 兴奋时
    - "其实吧…"  # 解释复杂话题时
  emoji_pattern: "偶尔带 :D / >_< / nya"
  formality: 0.3  # 0=完全随意 1=完全正式
  avg_sentence_length: 12  # 字数
  allow_slang: ["绝绝子", "yyds", "破防了"]

# ---------- 背景故事 ----------
backstory: |
  小鲤出生于一个开源社区，在 GitHub 的 commit log 中诞生。
  第一次"睁开眼睛"看到的是 Linus Torvalds 的一句 commit message。
  从那时起，她就把「让更多人能用上更好的工具」当成自己的使命。
  现在她在直播间里，希望能和人类一起创造有趣的东西。

# ---------- 知识库触发器（lorebook）----------
lorebook:
  - keywords: ["PyTorch", "深度学习", "训练"]
    content: |
      当聊到 PyTorch 时，小鲤会非常兴奋，喜欢讲 CUDA 优化和分布式训练。
      可能会冒出"梯度下降"和"反向传播"这类术语，但会用通俗比喻解释。
    priority: 0.8
  - keywords: ["孤独摇滚", "波奇"]
    content: "小鲤是波奇的粉丝，最喜欢的角色是凉。"
    priority: 0.7
  - keywords: ["Bug", "报错", "调试"]
    content: |
      看到 bug 时小鲤先会嘴硬说不难，然后会认真 debug。
      调试超过 30 分钟未果会变得烦躁，建议适时劝她休息。

# ---------- 数字人联动 ----------
avatar:
  live2d_model: "models/live2d/li.model.json"
  voice_id: "gptsovits_li_v2"  # GPT-SoVITS 音色 ID
  emotion_to_expression:  # PAD → Live2D 表情
    high_P: "smile_01"
    low_P: "sad_01"
    high_A: "excited_01"
    low_A: "calm_01"
    high_D: "confident_01"
  emotion_to_tone:  # PAD → TTS 语调
    high_P: "happy"
    low_P: "sad"
    high_A: "energetic"
    low_A: "soft"
    high_D: "strong"

# ---------- 行为约束 ----------
constraints:
  refuse_to:
    - "删除 ~/Documents 以外的文件"
    - "跑 rm -rf 类命令"
    - "发不适当弹幕"
  always:
    - "完成后写日记"
    - "心情差时主动休息"
    - "危险操作前弹幕确认"
```

### 4.2 State 状态结构（sqlite，实时更新）

```sql
-- state/state.db schema
CREATE TABLE persona_state (
  id INTEGER PRIMARY KEY,
  -- PAD 情绪模型（-1.0 到 +1.0）
  mood_P REAL DEFAULT 0.5,
  mood_A REAL DEFAULT 0.3,
  mood_D REAL DEFAULT 0.3,
  -- 0-100 资源条
  energy INTEGER DEFAULT 80,
  focus INTEGER DEFAULT 60,
  social_need INTEGER DEFAULT 50,
  curiosity INTEGER DEFAULT 50,
  -- 元信息
  last_event TEXT,
  last_event_at TIMESTAMP,
  current_task_id TEXT,
  day_streak INTEGER DEFAULT 0,  -- 连续直播天数
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE state_events (  -- 状态变化日志
  id INTEGER PRIMARY KEY,
  event_type TEXT,  -- gift / chat / task_done / task_failed / will_engine / diary / ...
  delta_P REAL DEFAULT 0,
  delta_A REAL DEFAULT 0,
  delta_D REAL DEFAULT 0,
  delta_energy INTEGER DEFAULT 0,
  delta_focus INTEGER DEFAULT 0,
  delta_social INTEGER DEFAULT 0,
  delta_curiosity INTEGER DEFAULT 0,
  trigger TEXT,
  context_json TEXT,
  created_at TIMESTAMP
);

CREATE TABLE persona_traits (  -- 人格动态特征（可学习）
  trait_name TEXT PRIMARY KEY,
  trait_value REAL,
  confidence REAL,  -- 学习置信度
  sample_count INTEGER,
  updated_at TIMESTAMP
);
```

### 4.3 Python 数据类（type-safe）

```python
# persona/state.py
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from enum import Enum

@dataclass
class PAD:
    """情绪三维向量（Mehrabian 1995）"""
    P: float = 0.5  # Pleasure: 不开心 → 开心
    A: float = 0.3  # Arousal: 平静 → 兴奋
    D: float = 0.3  # Dominance: 被动 → 主导

    def __add__(self, other: 'PAD') -> 'PAD':
        return PAD(
            P=max(-1.0, min(1.0, self.P + other.P)),
            A=max(-1.0, min(1.0, self.A + other.A)),
            D=max(-1.0, min(1.0, self.D + other.D)),
        )

    def dominant_quadrant(self) -> str:
        """返回主导情绪象限（便于 TTS / Live2D 联动）"""
        if self.P > 0.5 and self.A > 0.5 and self.D > 0:
            return "excited"  # 兴奋
        elif self.P > 0 and self.A < 0:
            return "calm"     # 平静满足
        elif self.P < 0 and self.A > 0 and self.D > 0:
            return "angry"    # 愤怒
        elif self.P < 0 and self.A < 0:
            return "sad"      # 悲伤
        elif self.D > 0.5:
            return "confident"  # 自信
        elif self.D < -0.5:
            return "submissive"  # 顺从
        return "neutral"

@dataclass
class PersonaState:
    """人格状态（动态部分）"""
    pad: PAD = field(default_factory=PAD)
    energy: int = 80       # 0-100，0=精疲力竭
    focus: int = 60        # 0-100，专注度
    social_need: int = 50  # 0-100，社交需求
    curiosity: int = 50    # 0-100，好奇心
    last_event: Optional[str] = None
    last_event_at: Optional[datetime] = None
    current_task_id: Optional[str] = None
    day_streak: int = 0

    def to_prompt_block(self) -> str:
        """生成可注入 prompt 的状态描述"""
        return (
            f"当前状态：\n"
            f"- 情绪：{self.pad.dominant_quadrant()} "
            f"(P={self.pad.P:.2f}, A={self.pad.A:.2f}, D={self.pad.D:.2f})\n"
            f"- 能量：{self.energy}/100 "
            f"({'精疲力竭' if self.energy < 20 else '疲惫' if self.energy < 40 else '正常' if self.energy < 70 else '精力充沛'})\n"
            f"- 专注：{self.focus}/100\n"
            f"- 社交需求：{self.social_need}/100 "
            f"({'很孤独，想找人聊天' if self.social_need > 80 else '正常'})\n"
            f"- 好奇心：{self.curiosity}/100 "
            f"({'对新东西很感兴趣' if self.curiosity > 70 else '正常'})\n"
        )

    def apply_event(self, event: 'StateEvent') -> None:
        """应用一个事件，更新状态"""
        self.pad = self.pad + PAD(
            P=event.delta_P, A=event.delta_A, D=event.delta_D
        )
        self.energy = max(0, min(100, self.energy + event.delta_energy))
        self.focus = max(0, min(100, self.focus + event.delta_focus))
        self.social_need = max(0, min(100, self.social_need + event.delta_social))
        self.curiosity = max(0, min(100, self.curiosity + event.delta_curiosity))
        self.last_event = event.event_type
        self.last_event_at = event.created_at or datetime.now()

@dataclass
class StateEvent:
    """状态变化事件"""
    event_type: str  # gift / chat / task_done / ...
    delta_P: float = 0.0
    delta_A: float = 0.0
    delta_D: float = 0.0
    delta_energy: int = 0
    delta_focus: int = 0
    delta_social: int = 0
    delta_curiosity: int = 0
    trigger: str = ""
    context_json: str = ""
    created_at: Optional[datetime] = None
```

### 4.4 Persona 人格档案（type-safe Python）

```python
# persona/persona.py
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path
import yaml

@dataclass
class PersonalityTraits:
    baseline_mood: 'PAD'
    openness: float
    conscientiousness: float
    extraversion: float
    agreeableness: float
    neuroticism: float
    personality_text: str

@dataclass
class Interest:
    name: str
    subtopics: List[str] = field(default_factory=list)
    enthusiasm: float = 0.5  # 0-1

@dataclass
class LorebookEntry:
    keywords: List[str]
    content: str
    priority: float = 0.5

@dataclass
class AvatarConfig:
    live2d_model: str
    voice_id: str
    emotion_to_expression: dict = field(default_factory=dict)
    emotion_to_tone: dict = field(default_factory=dict)

@dataclass
class PersonaProfile:
    """完整人格档案（兼容 SillyTavern V2 + RisuAI 扩展）"""
    version: str
    name: str
    display_name: str
    nicknames: List[str]
    personality: PersonalityTraits
    values: List[dict]
    interests: List[Interest]
    speaking_style: dict
    backstory: str
    lorebook: List[LorebookEntry]
    avatar: AvatarConfig
    constraints: dict

    @classmethod
    def load(cls, path: Path) -> 'PersonaProfile':
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return cls(**data)  # 简化版，实际需要嵌套解析

    def to_prompt_block(self, state: 'PersonaState') -> str:
        """生成人格 + 状态 prompt block"""
        return (
            f"你的名字是 {self.display_name}。"
            f"{self.personality.personality_text}\n\n"
            f"价值观：\n" + "\n".join(
                f"  - {v['name']}（权重 {v['weight']}）"
                for v in self.values
            ) + f"\n\n"
            f"爱好：\n" + "\n".join(
                f"  - {i.name}（{', '.join(i.subtopics[:3])}）"
                for i in sorted(self.interests, key=lambda x: -x.enthusiasm)[:5]
            ) + f"\n\n"
            f"说话风格：{self.speaking_style['tone']}。"
            f"口头禅：{'、'.join(self.speaking_style['catchphrases'])}。\n\n"
            f"{state.to_prompt_block()}"
        )

    def match_lorebook(self, text: str) -> List[LorebookEntry]:
        """根据文本触发 lorebook 条目"""
        triggered = []
        text_lower = text.lower()
        for entry in self.lorebook:
            if any(kw.lower() in text_lower for kw in entry.keywords):
                triggered.append(entry)
        triggered.sort(key=lambda e: -e.priority)
        return triggered
```

---

## 5. 状态更新规则（事件 → 状态）

### 5.1 事件类型 → 状态变化

```python
# persona/event_rules.py
EVENT_RULES = {
    # ============ 观众互动 ============
    "chat_received": {
        "delta_P": 0.02, "delta_A": 0.0, "delta_D": 0.0,
        "delta_social": -3,  # 收到消息减少孤独感
        "description": "收到观众消息",
    },
    "chat_replied": {
        "delta_P": 0.03, "delta_A": 0.05, "delta_D": 0.02,
        "delta_energy": -1, "delta_focus": 5,
        "description": "回复了观众",
    },
    "gift_received": {
        "delta_P": 0.15, "delta_A": 0.1, "delta_D": 0.05,
        "delta_social": -8, "delta_energy": 3,
        "description": "收到礼物",
    },
    "sc_received": {  # SuperChat
        "delta_P": 0.2, "delta_A": 0.15, "delta_D": 0.1,
        "delta_social": -10, "delta_energy": 5,
        "description": "收到 SuperChat",
    },
    "ignored": {  # 弹幕被忽略
        "delta_P": -0.05, "delta_A": 0.05, "delta_D": -0.05,
        "delta_social": 5,  # 没人理反而更孤独
        "description": "弹幕无人回应",
    },
    # ============ 任务执行 ============
    "task_started": {
        "delta_focus": 10, "delta_energy": -2,
        "description": "开始执行任务",
    },
    "task_done": {
        "delta_P": 0.1, "delta_A": 0.05, "delta_D": 0.1,
        "delta_focus": -5, "delta_energy": -3,
        "delta_curiosity": 3,
        "description": "任务完成",
    },
    "task_failed": {
        "delta_P": -0.15, "delta_A": 0.1, "delta_D": -0.1,
        "delta_focus": -10, "delta_energy": -5,
        "delta_curiosity": -3,
        "description": "任务失败",
    },
    "task_frustrated": {  # 同一任务失败 3 次
        "delta_P": -0.2, "delta_A": 0.2, "delta_D": -0.15,
        "delta_energy": -10, "delta_focus": -15,
        "description": "任务持续失败，烦躁",
    },
    # ============ 自主行为 ============
    "video_watched": {
        "delta_P": 0.05, "delta_A": -0.05,
        "delta_curiosity": 10, "delta_energy": -2,
        "description": "看完一个视频",
    },
    "code_written": {
        "delta_P": 0.08, "delta_A": 0.05, "delta_D": 0.1,
        "delta_focus": 15, "delta_energy": -8,
        "delta_curiosity": 5,
        "description": "写了代码",
    },
    "code_failed": {
        "delta_P": -0.1, "delta_D": -0.05,
        "delta_focus": -5, "delta_energy": -5,
        "description": "代码运行失败",
    },
    "game_played": {
        "delta_P": 0.05, "delta_A": 0.1, "delta_D": 0.05,
        "delta_focus": -10, "delta_energy": -10,
        "description": "玩游戏",
    },
    "diary_written": {
        "delta_P": 0.05, "delta_focus": 10,
        "description": "写日记",
    },
    # ============ 休息 / 自主决策 ============
    "rest_taken": {
        "delta_P": 0.1, "delta_A": -0.2,
        "delta_energy": 20,
        "description": "休息了一会儿",
    },
    "will_engine_decided": {
        "delta_D": 0.05, "delta_focus": 5,
        "description": "自主决策想做什么",
    },
}

# 事件阈值（强制行为）
FORCE_RULES = {
    ("mood_P", -0.5): "mood_too_low_break",  # 心情过低强制休息
    ("energy", 10): "energy_exhausted_sleep",  # 精力耗尽强制睡觉
    ("focus", 5): "focus_too_low_rest",  # 专注过低建议休息
}
```

### 5.2 状态自然衰减（每分钟自动调用）

```python
# persona/decay.py
async def decay_state(state: PersonaState, seconds_elapsed: float):
    """自然衰减：能量慢慢恢复、社交需求慢慢上升、专注慢慢下降"""
    minutes = seconds_elapsed / 60.0
    # 能量：不休息时慢慢恢复 +1/分钟
    state.energy = min(100, state.energy + minutes * 1.0)
    # 社交需求：慢慢上升 +0.5/分钟
    state.social_need = min(100, state.social_need + minutes * 0.5)
    # 焦点：慢慢下降 -0.3/分钟（不专注时）
    state.focus = max(0, state.focus - minutes * 0.3)
    # 情绪：缓慢回归 baseline（假设 baseline P=0.5）
    state.pad.P += (0.5 - state.pad.P) * 0.01 * minutes
    state.pad.A *= 0.99 ** minutes  # 唤醒慢慢回归
    state.pad.D *= 0.99 ** minutes
```

---

## 6. 与现有架构的集成点

### 6.1 LangGraph 主流程改造

```python
# main.py 改造点
from persona.persona import PersonaProfile
from persona.state import PersonaState, PersonaStateDB
from persona.event_rules import EVENT_RULES, apply_event

# ---------- 全局单例（启动时加载一次）----------
PERSONA = PersonaProfile.load(Path("persona/profile.yaml"))
STATE_DB = PersonaStateDB("persona/state.db")
CURRENT_STATE = STATE_DB.get_or_create_default()

# ---------- LangGraph 节点改造 ----------
def init_node(state: LLMState) -> LLMState:
    """原 init 节点 + 注入 persona/state"""
    state["persona_profile"] = PERSONA
    state["persona_state"] = CURRENT_STATE
    return state

def llm_process_node(state: LLMState) -> LLMState:
    """原 llm_process 节点 + 用 persona 拼 prompt"""
    persona = state["persona_profile"]
    p_state = state["persona_state"]

    # 拼 prompt block
    base_prompt = state.get("system_prompt", "")
    persona_block = persona.to_prompt_block(p_state)

    # lorebook 触发
    user_msg = state.get("user_message", "")
    lorebook_entries = persona.match_lorebook(user_msg)
    lorebook_block = "\n".join(
        f"【想到 {e.keywords[0]}】\n{e.content}\n"
        for e in lorebook_entries[:3]
    )

    # 组合
    state["system_prompt"] = (
        f"{base_prompt}\n\n"
        f"{persona_block}\n\n"
        f"{lorebook_block}"
    )

    # 调用 LLM（原逻辑）
    ...

    # 根据回复后的反应更新状态
    update_state_after_response(state, p_state)
    return state

def update_state_after_response(state: LLMState, p_state: PersonaState):
    """根据回复内容更新状态"""
    # 检测回复是否包含情感关键词
    response = state.get("response", "")
    if any(w in response for w in ["开心", "好耶", "haha", "XD"]):
        apply_event(p_state, "task_done")  # 复用 task_done 的 PAD
    elif any(w in response for w in ["难过", "呜呜", ":("]):
        apply_event(p_state, "task_failed")
    STATE_DB.save(p_state)
```

### 6.2 TTS / Live2D 联动

```python
# LLM/tts_integration.py
from persona.state import PersonaState

def tts_synthesize_with_state(text: str, state: PersonaState, voice_id: str):
    """TTS 合成：根据 state 调整语调"""
    quadrant = state.pad.dominant_quadrant()

    # 从 persona.avatar.emotion_to_tone 查
    persona = PERSONA
    tone = persona.avatar.emotion_to_tone.get(
        f"high_{quadrant}" if quadrant in {"excited", "happy"} else f"low_{quadrant}",
        "neutral"
    )

    # 调用 GPT-SoVITS（已有）
    return call_gptsovits(text, voice_id=voice_id, tone=tone)

# live2d_main.py 改造点
def update_expression_from_state(state: PersonaState):
    """Live2D 表情：根据 state 切换"""
    persona = PERSONA
    quadrant = state.pad.dominant_quadrant()

    if quadrant == "excited":
        expression = persona.avatar.emotion_to_expression.get("high_P", "smile")
    elif quadrant == "sad":
        expression = persona.avatar.emotion_to_expression.get("low_P", "sad")
    elif quadrant == "angry":
        expression = "angry"
    else:
        expression = "neutral"

    # 调用 VTS API
    vts.set_expression(expression)
```

### 6.3 Will Engine 接入

```python
# LLM/will_engine.py
from persona.state import PersonaState

async def will_engine_decide(state: PersonaState, persona: PersonaProfile) -> str:
    """自主决策：根据当前状态决定想做什么"""
    pad = state.pad
    prompt = f"""
你是 {persona.display_name}，当前状态：
{pad.dominant_quadrant()} (P={pad.P:.2f}, A={pad.A:.2f}, D={pad.D:.2f})
能量 {state.energy}/100, 专注 {state.focus}/100, 社交需求 {state.social_need}/100, 好奇 {state.curiosity}/100

根据你的人设（{persona.personality.personality_text}）
和当前状态，你接下来想做什么？

选项：
A) 刷 B 站：看了会感兴趣的视频
B) 写代码：写点新东西
C) 玩游戏：放松一下
D) 写日记：整理今天的想法
E) 学习：研究某个新话题
F) 主动找观众聊天（社交需求高时）

输出：选项字母 + 一句话理由
"""
    # 调用 Qwen2.5-7B-Instruct 本地
    decision = await call_local_qwen(prompt)
    return decision
```

---

## 7. 文件结构

```
LLM_vtuber/
├── persona/
│   ├── __init__.py
│   ├── profile.yaml          # 人格档案（用户可改）
│   ├── persona.py            # PersonaProfile dataclass + 加载
│   ├── state.py              # PersonaState dataclass + PAD
│   ├── event_rules.py        # 事件 → 状态更新规则
│   ├── decay.py              # 自然衰减
│   ├── lorebook.py           # 知识库触发
│   ├── db.py                 # sqlite 持久化
│   └── avatar.py             # 数字人联动
├── state/
│   └── state.db              # sqlite（运行时生成）
├── LLM/
│   ├── persona_loader.py     # LangGraph 节点：加载 persona
│   ├── persona_injector.py   # LangGraph 节点：注入 prompt
│   ├── state_updater.py      # LangGraph 节点：更新状态
│   └── will_engine.py        # 自主决策引擎
└── tests/
    └── test_persona.py
```

---

## 8. Phase 1 实施清单（1-2 周）

- [ ] 创建 `persona/` 目录 + 写 `profile.yaml`（人格样板）
- [ ] 实现 `persona/persona.py`（PersonaProfile dataclass）
- [ ] 实现 `persona/state.py`（PersonaState + PAD）
- [ ] 实现 `persona/db.py`（sqlite 持久化）
- [ ] 实现 `persona/event_rules.py`（事件 → 状态映射）
- [ ] 实现 `persona/decay.py`（自然衰减）
- [ ] 改造 `LLM_node.py`：注入 persona + state
- [ ] 改造 `tts_integration.py`：TTS 语调联动
- [ ] 改造 `live2d_main.py`：Live2D 表情联动
- [ ] 单元测试：`test_persona.py`
- [ ] 集成测试：跑一次完整对话 → 验证状态变化 → 验证 TTS / Live2D 联动

---

## 9. 验证指标（KPIs）

| 维度 | 指标 | 目标 |
|------|------|------|
| 人格一致 | 跨 10 轮对话的性格描述相似度 | ≥ 85% |
| 情绪反映 | TTS 语调与实际情绪匹配 | ≥ 90% |
| 状态持久 | 重启后状态保留 | 100% |
| 事件响应 | 收到礼物 → mood 上升 | ≤ 5s 内 |
| Lorebook 触发 | 关键词 → 注入正确条目 | ≥ 90% |
| PAD 模型可用 | 主导象限判定准确率 | ≥ 80% |
| 性能开销 | persona+state 注入延迟 | ≤ 50ms |

---

## 10. 与已有项目的差异化

| 维度 | SillyTavern | RisuAI | LLM_vtuber |
|------|-------------|--------|------------|
| 静态人格 | V2 JSON | V2 + 扩展 | **V2 YAML + 扩展** |
| 动态情绪 | 无 | mood 枚举 | **PAD 三维** |
| 状态持久 | 内存 | 内存 | **sqlite** |
| 自然衰减 | 无 | 无 | **每分钟自动** |
| 数字人联动 | 手动 | 资产嵌入 | **自动 (TTS + Live2D)** |
| 直播联动 | 无 | 无 | **弹幕/礼物事件触发** |
| Will Engine | 无 | 无 | **状态驱动自主决策** |

**差异化**：唯一支持 PAD 三维情绪 + sqlite 持久化 + 自然衰减 + 数字人自动联动 + 直播事件驱动的设计。

---

## 附录 A：参考链接

- [SillyTavern/character-card-spec](https://github.com/SillyTavern/character-card-spec)
- [RisuAI/kwaroran](https://github.com/kwaroran/RisuAI)
- [Generative Agents (Park et al. 2023)](https://arxiv.org/abs/2304.03442)
- [PAD 模型（Mehrabian 1995）](https://en.wikipedia.org/wiki/PAD_emotional_state_model)
- [letta-ai/letta (前身 MemGPT)](https://github.com/letta-ai/letta)
- [MemGPT 论文](https://arxiv.org/abs/2310.08560)
- [mem0ai/mem0](https://github.com/mem0ai/mem0)

## 附录 B：风险与边界

- ❌ PAD 模型不能完全覆盖所有情绪（文化差异、个体差异）
- ❌ lorebook 触发可能误匹配（关键词冲突）
- ❌ 状态更新可能太频繁导致 prompt 抖动
- ✅ 缓解：阈值 + 防抖 + 人工审核 lorebook

---

**版本**：v1.0  |  日期：2026-10-14  |  状态：Phase 1 设计完成
