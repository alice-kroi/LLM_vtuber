# 后端代码规范模板

## 技术栈选择
- 语言：Node.js / Python / Go / Java
- 框架：Express / FastAPI / Django / Gin / Spring
- 数据库：PostgreSQL / MySQL / MongoDB / {{数据库}}
- ORM：Prisma / SQLAlchemy / GORM / {{ORM}}

## 项目结构
```
src/
├── controllers/   # 控制器/路由处理
├── services/      # 业务逻辑
├── models/        # 数据模型
├── repositories/  # 数据访问层
├── middlewares/    # 中间件
├── validators/    # 请求验证
├── utils/         # 工具函数
├── config/        # 配置管理
├── types/         # 类型定义
└── tests/         # 测试文件
```

## 编码规范

### API规范
- RESTful风格：GET/POST/PUT/DELETE
- 统一响应格式：{ code, data, message }
- 版本化API：/api/v1/
- 分页支持：page, limit

### 代码规范
- 分层清晰：Controller → Service → Repository
- 单一职责，函数长度 < 50行
- 依赖注入，便于测试
- 错误统一处理

### 数据库规范
- 迁移管理
- 索引优化
- 软删除支持
- 审计字段：created_at, updated_at

## 输出清单
- [ ] API路由
- [ ] 控制器
- [ ] 服务层
- [ ] 数据模型
- [ ] 数据库迁移
- [ ] API文档
- [ ] 单元测试
