# 模板参考文档

## 概述

本项目提供 **18个标准化输出模板**，覆盖各Agent的产出物格式。模板位于 `.trae/templates/` 目录。

---

## 模板列表

### 调度层模板

| 模板文件 | 用途 | 对应Agent |
|---------|------|-----------|
| `dispatch_template.md` | 调度执行计划与状态追踪 | dispatcher |
| `code_review_template.md` | 代码审核报告 | code-coordinator |

### 准备层模板

| 模板文件 | 用途 | 对应Agent |
|---------|------|-----------|
| `env_setup_template.md` | 环境配置报告 | env-setup |
| `resource_template.md` | 资源获取报告 | resource-fetcher |

### 方案层模板

| 模板文件 | 用途 | 对应Agent |
|---------|------|-----------|
| `analysis_template.md` | 功能分析文档 | analyzer |
| `plan_template.md` | 技术方案文档 | planner |
| `review_template.md` | 方案审核报告 | reviewer |

### 代码实现层模板

| 模板文件 | 用途 | 对应Agent |
|---------|------|-----------|
| `frontend_template.md` | 前端代码规范 | frontend |
| `backend_template.md` | 后端代码规范 | backend |
| `llm_template.md` | LLM代码规范 | llm |
| `knowledge_base_template.md` | 知识库代码规范 | knowledge-base |
| `external_template.md` | 外部集成代码规范 | external-integration |
| `deep_learning_template.md` | 深度学习代码规范 | deep-learning |

### 验证层模板

| 模板文件 | 用途 | 对应Agent |
|---------|------|-----------|
| `verification_template.md` | 代码验证报告 | verifier |
| `proofread_template.md` | 代码校对报告 | proofreader |
| `quality_template.md` | 质量审查报告 | quality |

### 收尾层模板

| 模板文件 | 用途 | 对应Agent |
|---------|------|-----------|
| `file_organize_template.md` | 文件整理报告 | file-organizer |
| `docs_template.md` | 项目文档模板 | documenter |

---

## 通用模板结构

大多数模板包含以下标准章节：

```markdown
# 文档标题

> 版本: 1.0
> 创建日期: {{DATE}}
> 执行人: {{Agent名}}
> 关联项目: {{项目名称}}

## 1. 概述
- 输入摘要
- 执行背景

## 2. 执行过程
- 步骤记录
- 产出清单

## 3. 结果
- 执行结果
- 质量评估

## 4. 后续建议
- 下一步操作建议
```

---

## 各模板详细说明

### dispatch_template.md

**用途**：调度Agent的执行计划与状态追踪

**包含内容**：
- 意图分析
- Agent执行序列
- 模板填充状态
- 执行状态追踪表
- 异常处理方案

---

### code_review_template.md

**用途**：code-coordinator的代码审核报告

**包含内容**：
- 编码执行计划
- 各Agent产出清单
- 代码规范检查
- 跨Agent一致性检查
- 集成验证结果
- 问题清单
- 质量评分

---

### env_setup_template.md

**用途**：环境配置全过程记录

**包含内容**：
- 技术栈需求表
- 当前环境检测结果
- 依赖清单
- 配置执行记录
- 环境验证测试

---

### resource_template.md

**用途**：外部资源获取报告

**包含内容**：
- 资源需求清单
- 获取计划
- 执行结果
- 资源索引
- 完整性验证

---

### analysis_template.md

**用途**：功能需求分析文档

**包含内容**：
- 需求理解
- 功能模块拆解
- 依赖关系图
- 验收标准
- 可行性预判

---

### plan_template.md

**用途**：技术设计方案文档

**包含内容**：
- 架构设计
- 模块设计
- 接口定义
- 数据结构
- 部署方案

---

### review_template.md

**用途**：方案可行性审核报告

**包含内容**：
- 审核范围
- 可行性评估
- 风险识别
- 改进建议
- 审核结论

---

### frontend_template.md

**用途**：前端代码规范和交付清单

**包含内容**：
- 技术栈选择
- 项目结构
- 编码规范
- 组件清单
- 交付检查项

---

### backend_template.md

**用途**：后端代码规范和交付清单

**包含内容**：
- 技术栈选择
- 项目结构
- API规范
- 数据库规范
- 交付检查项

---

### llm_template.md

**用途**：LLM代码规范和交付清单

**包含内容**：
- 技术栈选择
- 项目结构
- Prompt工程规范
- Chain/Agent设计
- 交付检查项

---

### knowledge_base_template.md

**用途**：知识库代码规范和交付清单

**包含内容**：
- 技术栈选择
- 项目结构
- 文档处理规范
- 检索策略
- 交付检查项

---

### external_template.md

**用途**：外部集成代码规范和交付清单

**包含内容**：
- 技术栈选择
- 项目结构
- API客户端规范
- 容错机制
- 交付检查项

---

### deep_learning_template.md

**用途**：深度学习代码规范和交付清单

**包含内容**：
- 技术栈选择
- 项目结构
- 模型定义规范
- 训练流程
- 交付检查项

---

### verification_template.md

**用途**：代码验证报告

**包含内容**：
- 测试范围
- 测试用例执行结果
- 功能验证结果
- 边界条件检查
- 需求覆盖度检查

---

### proofread_template.md

**用途**：代码校对报告

**包含内容**：
- 校对范围
- 需求追溯结果
- 一致性检查
- 遗漏识别
- 校对结论

---

### quality_template.md

**用途**：代码质量审查报告

**包含内容**：
- 审查范围
- 规范检查结果
- 异常处理评估
- 安全审查
- 可维护性评估
- 质量评分

---

### file_organize_template.md

**用途**：文件整理报告

**包含内容**：
- 整理前状态
- 标准目录结构
- 清理文件清单
- 整理后状态
- 结构评估

---

### docs_template.md

**用途**：项目文档模板

**包含内容**：
- 项目概述
- 功能说明
- 快速开始
- API文档
- 部署指南
- FAQ

---

## 模板使用指南

### 1. 自动填充
调用Agent时，模板会自动根据上下文填充：
- 日期
- Agent名称
- 项目名称
- 输入摘要

### 2. 手动填充
可手动编辑模板中的占位符：
- `{{DATE}}` - 日期
- `{{Agent名}}` - 执行Agent
- `{{项目名}}` - 项目名称
- `{{...}}` - 其他动态内容

### 3. 自定义模板
可基于现有模板创建自定义模板：
1. 复制现有模板
2. 修改章节结构
3. 调整表格字段
4. 保存到 `.trae/templates/`

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v3.0 | 2026-08-15 | 18个模板，新增代码Agent领域模板 |
| v2.0 | 2026-08-15 | 11个模板，新增准备层和收尾层模板 |
| v1.0 | 2026-08-15 | 7个基础模板 |
