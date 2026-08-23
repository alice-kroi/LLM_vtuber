# Agent 参考文档

## 概述

本项目包含 **19个专业化Agent**，覆盖从需求分析到代码交付的完整工程化流程。Agent按职能分为6层：

---

## 1. 调度层

### 1.1 orchestrator - 工作流编排器

**定位**：多Agent协作的总编排器

**职责**：
- 分析用户意图，自动选择Agent序列
- 协调各Agent的执行顺序和依赖关系
- 监控执行状态，处理异常
- 汇总产出，生成最终交付物

**调用方式**：
```
请使用 orchestrator，帮我完成以下需求：{{需求描述}}
```

**调度路径示例**：
| 请求类型 | 自动调度路径 |
|----------|-------------|
| 新建项目 | env-setup → resource-fetcher → analyzer → planner → reviewer → code-coordinator → verifier → proofreader → quality → file-organizer → documenter |
| 修改功能 | analyzer → planner → reviewer → code-coordinator → verifier → proofreader → quality → documenter |
| 仅实现代码 | code-coordinator → verifier |

---

### 1.2 dispatcher - 智能调度Agent

**定位**：精细调度专家，意图深度分析

**职责**：
- 深度解析用户请求的核心意图
- 分析当前工作区上下文状态
- 生成详细的调度执行计划
- 为各Agent准备输入模板

**调用方式**：
```
请使用 dispatcher，分析并调度以下需求：{{需求描述}}
```

---

## 2. 准备层

### 2.1 env-setup - 环境配置Agent

**定位**：项目环境工程师

**职责**：
- 分析技术栈需求
- 检测当前环境状态
- 生成并执行环境配置方案
- 验证环境可用性

**技术栈检测清单**：
| 类型 | 检测命令 |
|------|----------|
| Python | `python --version` |
| Node.js | `node --version` |
| PostgreSQL | `psql --version` |
| Redis | `redis-server --version` |

**调用方式**：
```
请使用 env-setup，为 {{项目名}} 配置开发环境
```

---

### 2.2 resource-fetcher - 资源调取Agent

**定位**：资源获取专家

**职责**：
- 识别项目所需外部资源
- 选择获取方式（git clone/npm install等）
- 执行获取操作
- 验证资源完整性

**资源类型**：
| 类型 | 获取方式 |
|------|----------|
| 代码库 | git clone |
| 依赖包 | npm/pip/maven install |
| 配置模板 | 下载/创建 |
| 参考文档 | 搜索/下载 |

**调用方式**：
```
请使用 resource-fetcher，获取 {{项目}} 所需的外部资源
```

---

## 3. 方案层

### 3.1 analyzer - 功能分析Agent

**定位**：需求分析专家

**职责**：
- 深入理解用户需求
- 将需求分解为功能模块
- 定义验收标准
- 识别技术难点和风险

**输出**：功能分析文档（analysis_template.md）

**调用方式**：
```
请使用 analyzer，分析以下需求：{{需求描述}}
```

---

### 3.2 planner - 方案编写Agent

**定位**：技术方案架构师

**职责**：
- 设计技术架构
- 定义接口和数据结构
- 规划实现步骤
- 评估技术选型

**输出**：技术方案文档（plan_template.md）

**调用方式**：
```
请使用 planner，为以下需求编写技术方案：{{需求描述}}
```

---

### 3.3 reviewer - 方案审核Agent

**定位**：方案评审专家

**职责**：
- 评估技术方案可行性
- 识别潜在风险
- 检查合规性
- 提出改进建议

**输出**：审核报告（review_template.md）

**调用方式**：
```
请使用 reviewer，审核以下技术方案：{{方案内容}}
```

---

## 4. 代码实现层

### 4.1 code-coordinator - 编程统筹Agent 🔑

**定位**：代码实现阶段总协调者

**核心职责**：
- 分析技术方案，识别技术领域
- 选择代码Agent组合
- 协调跨Agent依赖
- 审核代码产出质量
- 保证跨Agent代码一致性

**调度策略**：
| 项目类型 | 代码Agent组合 |
|----------|---------------|
| 纯前端 | frontend |
| 纯后端 | backend |
| 全栈Web | frontend + backend |
| AI应用 | llm + knowledge-base |
| AI+Web | frontend + backend + llm + knowledge-base |
| 深度学习 | deep-learning |
| AI+外部 | llm + external-integration |
| 综合项目 | 按需组合 |

**调用方式**：
```
请使用 code-coordinator，协调实现以下需求：{{需求描述}}
```

---

### 4.2 frontend - 前端开发Agent

**定位**：前端开发专家

**技术栈**：
| 分类 | 技术 |
|------|------|
| 语言 | JavaScript, TypeScript |
| 框架 | React, Vue, Angular |
| 样式 | CSS, SCSS, Tailwind |
| 构建 | Vite, Webpack |
| 状态 | Redux, Pinia |

**项目结构**：
```
src/
├── components/     # 可复用组件
├── pages/         # 页面组件
├── hooks/         # 自定义Hooks
├── store/         # 状态管理
└── utils/         # 工具函数
```

**调用方式**：
```
请使用 frontend，实现以下前端需求：{{需求描述}}
```

---

### 4.3 backend - 后端开发Agent

**定位**：后端开发专家

**技术栈**：
| 分类 | 技术 |
|------|------|
| 语言 | Node.js, Python, Go, Java |
| 框架 | Express, FastAPI, Django, Gin |
| 数据库 | PostgreSQL, MySQL, MongoDB, Redis |
| API | RESTful, GraphQL, gRPC |

**项目结构**：
```
src/
├── controllers/   # 控制器
├── services/      # 业务逻辑
├── models/        # 数据模型
├── repositories/  # 数据访问
└── middlewares/    # 中间件
```

**调用方式**：
```
请使用 backend，实现以下后端需求：{{需求描述}}
```

---

### 4.4 llm - 大模型Agent

**定位**：LLM应用开发专家

**技术栈**：
| 分类 | 技术 |
|------|------|
| LLM | GPT, Claude, Llama, Gemini |
| 框架 | LangChain, LlamaIndex, AutoGen |
| 功能 | RAG, Agent, Function Calling |

**项目结构**：
```
src/llm/
├── chains/        # Chain定义
├── agents/        # Agent定义
├── prompts/      # Prompt模板
├── tools/        # 工具定义
└── memory/        # 记忆管理
```

**调用方式**：
```
请使用 llm，实现以下LLM需求：{{需求描述}}
```

---

### 4.5 knowledge-base - 知识库Agent

**定位**：知识库系统专家

**技术栈**：
| 分类 | 技术 |
|------|------|
| 向量库 | Chroma, Pinecone, Milvus, FAISS |
| Embedding | text-embedding, bge |
| 文档处理 | PyPDF, markdown, unstructured |

**项目结构**：
```
src/
├── documents/     # 文档处理
├── embeddings/    # Embedding模型
├── vectorstore/   # 向量存储
└── retrieval/     # 检索策略
```

**调用方式**：
```
请使用 knowledge-base，构建以下知识库：{{需求描述}}
```

---

### 4.6 external-integration - 外部调用Agent

**定位**：外部服务集成专家

**技术栈**：
| 分类 | 技术 |
|------|------|
| 协议 | REST, WebSocket, gRPC |
| 工具 | MCP, Axios, SDK |
| 认证 | API Key, OAuth2, HMAC |
| 容错 | 重试, 熔断, 降级 |

**项目结构**：
```
src/
├── clients/       # API客户端
├── adapters/      # 服务适配器
├── auth/          # 认证模块
└── cache/         # 缓存策略
```

**调用方式**：
```
请使用 external-integration，集成以下外部服务：{{需求描述}}
```

---

### 4.7 deep-learning - 深度学习Agent

**定位**：深度学习工程师

**技术栈**：
| 分类 | 技术 |
|------|------|
| 框架 | PyTorch, TensorFlow, JAX |
| 模型 | Transformer, CNN, RNN, Diffusion |
| 工具 | Lightning, HuggingFace, ONNX |

**项目结构**：
```
src/
├── models/        # 模型定义
├── data/          # 数据处理
├── training/      # 训练流程
└── inference/     # 推理管道
```

**调用方式**：
```
请使用 deep-learning，实现以下深度学习需求：{{需求描述}}
```

---

### 4.8 executor - 通用代码Agent（后备）

**定位**：通用代码实现后备

**职责**：
- 当特定代码Agent不适用时使用
- 处理跨领域或特殊需求的代码
- 保证通用代码实现能力

**调用方式**：
```
请使用 executor，实现以下代码需求：{{需求描述}}
```

---

## 5. 验证层

### 5.1 verifier - 代码验证Agent

**定位**：功能验证专家

**职责**：
- 验证代码功能正确性
- 检查边界条件处理
- 验证需求覆盖度
- 执行测试用例

**输出**：验证报告（verification_template.md）

**调用方式**：
```
请使用 verifier，验证以下代码：{{代码位置}}
```

---

### 5.2 proofreader - 代码校对Agent

**定位**：一致性校对专家

**职责**：
- 校对代码与技术方案的一致性
- 检查需求遗漏
- 识别实现偏差
- 追溯需求覆盖

**输出**：校对报告（proofread_template.md）

**调用方式**：
```
请使用 proofreader，校对以下代码与方案的一致性
```

---

### 5.3 quality - 质量审查Agent

**定位**：代码质量专家

**职责**：
- 检查代码规范
- 审查异常处理
- 评估安全风险
- 评估可维护性

**输出**：质量报告（quality_template.md）

**调用方式**：
```
请使用 quality，审查以下代码质量：{{代码位置}}
```

---

## 6. 收尾层

### 6.1 file-organizer - 文件整理Agent

**定位**：项目整理专家

**职责**：
- 分析当前目录结构
- 清理无用文件（缓存、临时文件等）
- 规范化目录结构
- 生成文件索引

**标准目录结构**：
```
project/
├── src/          # 源代码
├── tests/        # 测试代码
├── docs/         # 项目文档
├── config/       # 配置文件
├── scripts/      # 脚本工具
└── README.md
```

**调用方式**：
```
请使用 file-organizer，整理当前项目目录
```

---

### 6.2 documenter - 文档编写Agent

**定位**：文档工程专家

**职责**：
- 编写README文档
- 编写API文档
- 编写使用指南
- 同步代码与文档

**输出**：项目文档（docs_template.md）

**调用方式**：
```
请使用 documenter，为 {{项目名}} 编写文档
```

---

## Agent调用速查表

| 需求 | 推荐Agent | 调用方式 |
|------|----------|----------|
| 完整开发流程 | orchestrator | `orchestrator + 需求描述` |
| 需求分析 | analyzer | `analyzer + 需求描述` |
| 技术方案 | planner | `planner + 需求描述` |
| 方案审核 | reviewer | `reviewer + 方案内容` |
| 前端开发 | frontend | `frontend + 需求描述` |
| 后端开发 | backend | `backend + 需求描述` |
| LLM应用 | llm | `llm + 需求描述` |
| 知识库 | knowledge-base | `knowledge-base + 需求描述` |
| 外部集成 | external-integration | `external-integration + 需求描述` |
| 深度学习 | deep-learning | `deep-learning + 需求描述` |
| 代码验证 | verifier | `verifier + 代码位置` |
| 代码校对 | proofreader | `proofreader + 代码位置` |
| 质量审查 | quality | `quality + 代码位置` |
| 环境配置 | env-setup | `env-setup + 项目名` |
| 资源获取 | resource-fetcher | `resource-fetcher + 项目名` |
| 文件整理 | file-organizer | `file-organizer` |
| 文档编写 | documenter | `documenter + 项目名` |

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v3.0 | 2026-08-15 | 19个Agent，代码Agent细分为6个专业领域 |
| v2.0 | 2026-08-15 | 12个Agent，新增智能调度、环境配置等 |
| v1.0 | 2026-08-15 | 初始版本，8个Agent |
