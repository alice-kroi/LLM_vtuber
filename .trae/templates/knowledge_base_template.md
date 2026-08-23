# 知识库代码规范模板

## 技术栈选择
- 向量库：Chroma / Pinecone / Milvus / FAISS
- Embedding：text-embedding / bge / sentence-transformers
- 文档处理：PyPDF / markdown / unstructured
- 检索：向量检索 / 混合检索 / Rerank

## 项目结构
```
src/
├── documents/      # 文档处理
│   ├── loaders/    # 文档加载器
│   ├── splitters/  # 文本分割器
│   └── cleaners/   # 文本清洗
├── embeddings/     # Embedding模型
├── vectorstore/    # 向量存储
├── retrieval/      # 检索策略
│   ├── retrievers/ # 检索器
│   └── ranking/    # 重排序
├── ingestion/      # 数据入库管道
└── utils/          # 工具函数
```

## 编码规范

### 文档处理
- 支持多种格式：PDF、Markdown、TXT、HTML
- 合理分块：chunk_size=500, overlap=50
- 保留元数据：来源、时间、作者
- 清洗噪声：页眉页脚、重复内容

### 向量存储
- 集合命名规范
- 索引策略选择
- 元数据字段设计
- 批量写入优化

### 检索策略
- 支持相似度搜索
- 支持MMR（最大边际相关性）
- 支持过滤条件
- 返回分数和排序

## 输出清单
- [ ] 文档处理管道
- [ ] Embedding配置
- [ ] 向量存储操作
- [ ] 检索查询接口
- [ ] 入库脚本
- [ ] 单元测试
