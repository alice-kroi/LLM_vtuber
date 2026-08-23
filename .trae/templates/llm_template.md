# LLM代码规范模板

## 技术栈选择
- LLM：GPT / Claude / Llama / {{模型}}
- 框架：LangChain / LlamaIndex / AutoGen / {{框架}}
- 功能：RAG / Agent / 对话 / Function Calling

## 项目结构
```
src/
├── llm/
│   ├── chains/      # Chain定义
│   ├── agents/      # Agent定义
│   ├── prompts/     # Prompt模板
│   ├── tools/       # 工具定义
│   ├── memory/      # 记忆管理
│   └── config/      # LLM配置
├── retrieval/       # 检索相关
├── generation/      # 生成相关
└── utils/           # 工具函数
```

## 编码规范

### Prompt工程
- 使用System Message设定角色
- 结构化输入输出
- 包含few-shot示例
- 转义特殊字符

### Chain/Agent设计
- 单一职责原则
- 可组合、可复用
- 明确输入输出格式
- 错误处理和重试

### Token优化
- 使用token计数器
- 实现上下文滑动窗口
- 缓存常用prompt
- 压缩历史消息

## 输出清单
- [ ] Prompt模板
- [ ] Chain/Agent定义
- [ ] LLM调用封装
- [ ] 错误处理
- [ ] Token优化
- [ ] 单元测试
