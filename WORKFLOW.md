# 多AI Agent工作流详细说明

## 1. 工作流概述

本工作流定义了**19个专业化Agent**之间的协作流程，覆盖从需求到交付的完整工程化链路。工作流采用**智能调度**模式，根据用户意图动态选择Agent组合。

## 2. Agent 列表

### 2.1 调度层

| Agent | 职责 | 调用时机 |
|-------|------|----------|
| orchestrator | 工作流编排器 | 用户请求完整流程 |
| dispatcher | 智能调度Agent | 复杂任务精细调度 |

### 2.2 准备层

| Agent | 职责 | 调用时机 |
|-------|------|----------|
| env-setup | 环境配置 | 新项目开始或环境变更时 |
| resource-fetcher | 资源调取 | 需要外部依赖、文档、数据时 |

### 2.3 方案层

| Agent | 职责 | 调用时机 |
|-------|------|----------|
| analyzer | 功能分析 | 流程起始 |
| planner | 方案编写 | analyzer完成后 |
| reviewer | 方案审核 | planner完成后 |

### 2.4 代码实现层

| Agent | 职责 | 调用时机 |
|-------|------|----------|
| **code-coordinator** | 编程统筹 | 方案审核通过后，协调代码Agent |
| frontend | 前端开发 | code-coordinator调度 |
| backend | 后端开发 | code-coordinator调度 |
| llm | 大模型 | code-coordinator调度 |
| knowledge-base | 知识库 | code-coordinator调度 |
| external-integration | 外部调用 | code-coordinator调度 |
| deep-learning | 深度学习 | code-coordinator调度 |
| executor | 通用代码实现 | 后备方案 |

### 2.5 验证层

| Agent | 职责 | 调用时机 |
|-------|------|----------|
| verifier | 代码验证 | code-coordinator完成后 |
| proofreader | 代码校对 | verifier完成后 |
| quality | 质量审查 | proofreader完成后 |

### 2.6 收尾层

| Agent | 职责 | 调用时机 |
|-------|------|----------|
| file-organizer | 文件整理 | 开发完成后 |
| documenter | 文档编写 | 整理完成后 |

## 3. 执行流程

### 3.1 智能调度模式（推荐）

```
用户请求
   │
   ▼
┌─────────────────────────────────────────────┐
│  orchestrator / dispatcher                  │
│  • 分析用户意图                             │
│  • 评估上下文状态                           │
│  • 选择Agent组合                           │
└──────────────────┬──────────────────────────┘
                   │
   ┌───────────────┼───────────────┐
   ▼               ▼               ▼
┌─────────┐  ┌─────────┐  ┌─────────┐
│ 准备层   │  │ 方案层   │  │ 收尾层   │
│env-setup │  │analyzer │  │file-org │
│resource  │  │planner  │  │document │
│-fetcher  │  │reviewer │  │         │
└─────────┘  └────┬────┘  └─────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────┐
│  代码实现层                                      │
│  ┌───────────────────────────────────────────┐  │
│  │  code-coordinator 编程统筹                │  │
│  │  • 分析技术方案 → 选择代码Agent组合       │  │
│  │  • 协调编码执行 → 审核产出质量           │  │
│  └──────────────────┬────────────────────────┘  │
│                     │                           │
│  ┌─────┬───────┬───┴───┬───────┬───────┐       │
│  ▼     ▼       ▼       ▼       ▼       ▼       │
│  frontend backend llm kb ext deep-learning      │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  验证层                                          │
│  verifier → proofreader → quality               │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
            交付给用户
```

### 3.2 按请求类型调度

| 请求类型 | 推荐调度路径 |
|----------|-------------|
| 新建项目 | env-setup → resource-fetcher → analyzer → planner → reviewer → code-coordinator → verifier → proofreader → quality → file-organizer → documenter |
| 修改功能 | analyzer → planner → reviewer → code-coordinator → verifier → proofreader → quality → documenter |
| 代码审查 | quality → documenter |
| 环境配置 | env-setup |
| 获取资源 | resource-fetcher |
| 清理项目 | file-organizer |
| 仅编写方案 | analyzer → planner → reviewer |
| 仅实现代码 | code-coordinator → verifier |

### 3.3 代码Agent调度策略

code-coordinator根据项目类型选择代码Agent组合：

| 项目类型 | 代码Agent组合 |
|----------|---------------|
| 纯前端 | frontend |
| 纯后端 | backend |
| 全栈Web | frontend + backend |
| AI应用 | llm + knowledge-base |
| AI+Web | frontend + backend + llm + knowledge-base |
| 深度学习 | deep-learning |
| AI+外部集成 | llm + external-integration |
| 综合项目 | 按需组合 |

### 3.4 完整流水线模式

按固定顺序执行所有19个Agent：

```
env-setup → resource-fetcher → analyzer → planner → reviewer
→ code-coordinator → [frontend/backend/llm/kb/ext/deep-learning]
→ verifier → proofreader → quality → file-organizer → documenter
```

### 3.5 质量门禁

| 阶段 | 门禁条件 | 不通过处理 |
|------|----------|------------|
| 准备层 | 环境就绪 + 资源齐全 | 返回env-setup/resource-fetcher |
| 方案设计 | 方案审核通过 | 返回planner修改 |
| 代码实现 | code-coordinator审核通过 | 返回code-coordinator协调修改 |
| 质量保障 | 验证+校对+质量通过 | 返回code-coordinator或executor修复 |
| 收尾交付 | 文档完整 + 结构整洁 | 返回对应Agent |

## 4. 代码Agent技术栈

### 4.1 frontend 技术栈
| 分类 | 技术 |
|------|------|
| 语言 | JavaScript, TypeScript |
| 框架 | React, Vue, Angular, Svelte |
| 样式 | CSS, SCSS, Tailwind, Styled Components |
| 构建 | Vite, Webpack, Rollup |
| 状态 | Redux, Pinia, Zustand |

### 4.2 backend 技术栈
| 分类 | 技术 |
|------|------|
| 语言 | Node.js, Python, Go, Java |
| 框架 | Express, FastAPI, Django, Gin, Spring |
| 数据库 | PostgreSQL, MySQL, MongoDB, Redis |
| API | RESTful, GraphQL, gRPC |
| 认证 | JWT, OAuth2, Session |

### 4.3 llm 技术栈
| 分类 | 技术 |
|------|------|
| LLM | GPT, Claude, Llama, Gemini |
| 框架 | LangChain, LlamaIndex, AutoGen |
| 功能 | Prompt工程, RAG, Agent, Function Calling |
| 存储 | 向量数据库, Embedding |

### 4.4 knowledge-base 技术栈
| 分类 | 技术 |
|------|------|
| 向量库 | Chroma, Pinecone, Milvus, FAISS |
| Embedding | text-embedding, bge, sentence-transformers |
| 文档处理 | PyPDF, markdown, unstructured |
| 检索 | 向量检索, 混合检索, 重排序 |

### 4.5 external-integration 技术栈
| 分类 | 技术 |
|------|------|
| 协议 | REST, WebSocket, gRPC |
| 工具 | MCP, Postman, Insomnia |
| 认证 | API Key, OAuth2, HMAC |
| 容错 | 重试, 熔断, 降级 |

### 4.6 deep-learning 技术栈
| 分类 | 技术 |
|------|------|
| 框架 | PyTorch, TensorFlow, JAX |
| 模型 | Transformer, CNN, RNN, GNN |
| 工具 | Lightning, HuggingFace, ONNX |
| 优化 | 量化, 蒸馏, 推理加速 |

## 5. 输入输出规范

### 5.1 输入规范

用户需提供：
- **请求描述**：清晰的功能描述或任务说明
- **意图类型**（可选）：指定请求类型
- **技术栈偏好**（可选）：指定使用的技术
- **Agent偏好**（可选）：指定跳过或使用的Agent
- **约束条件**（可选）：特殊限制要求

### 5.2 输出规范

最终交付物（根据调度路径）：
1. 调度执行计划
2. 环境配置报告（如调用env-setup）
3. 资源获取报告（如调用resource-fetcher）
4. 功能分析文档
5. 技术方案文档
6. 审核报告
7. 代码审核报告（code-coordinator产出）
8. 实现的源代码
9. 验证报告
10. 校对报告
11. 质量报告
12. 文件整理报告（如调用file-organizer）
13. 完整项目文档

## 6. 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v3.0 | 2026-08-15 | 代码Agent细分为7个领域Agent，新增code-coordinator |
| v2.0 | 2026-08-15 | 扩展为12个Agent，新增智能调度、环境配置、资源调取、文件整理 |
| v1.0 | 2026-08-15 | 初始版本，包含8个Agent |
