# 前端代码规范模板

## 技术栈选择
- 框架：React / Vue / Angular / {{框架}}
- 语言：TypeScript / JavaScript
- 样式：CSS / SCSS / Tailwind / {{方案}}
- 构建：Vite / Webpack / {{工具}}

## 项目结构
```
src/
├── components/     # 可复用组件
├── pages/         # 页面组件
├── hooks/         # 自定义Hooks
├── utils/         # 工具函数
├── styles/        # 全局样式
├── assets/        # 静态资源
├── store/         # 状态管理
├── router/        # 路由配置
└── types/         # 类型定义
```

## 编码规范

### 命名规范
- 组件：PascalCase (MyComponent.tsx)
- 变量/函数：camelCase
- 常量：UPPER_SNAKE_CASE
- 类型/接口：PascalCase
- 文件名：kebab-case 或 PascalCase

### 组件规范
- 单一职责，组件长度 < 100行
- Props必须有类型定义
- 使用函数式组件 + Hooks
- 提取可复用逻辑到自定义Hooks

### 样式规范
- 优先使用CSS Modules或Scoped Styles
- 响应式设计：Mobile First
- 使用CSS变量管理主题
- 避免!important

## 输出清单
- [ ] 组件代码
- [ ] 样式文件
- [ ] 路由配置
- [ ] 状态管理
- [ ] 类型定义
- [ ] 单元测试
