# 深度学习代码规范模板

## 技术栈选择
- 框架：PyTorch / TensorFlow / JAX
- 模型：Transformer / CNN / RNN / GNN
- 工具：Lightning / HuggingFace / ONNX
- 优化：量化 / 蒸馏 / 推理加速

## 项目结构
```
src/
├── models/         # 模型定义
│   ├── backbone/   # 骨干网络
│   ├── heads/      # 任务头
│   └── losses/     # 损失函数
├── data/           # 数据处理
│   ├── datasets/   # 数据集类
│   ├── loaders/    # 数据加载
│   └── transforms/ # 数据变换
├── training/       # 训练相关
│   ├── trainer/    # 训练器
│   ├── callbacks/  # 回调函数
│   └── schedulers/ # 学习率调度
├── inference/      # 推理相关
├── utils/          # 工具函数
└── configs/        # 配置文件
```

## 编码规范

### 模型定义
- 继承nn.Module
- 清晰的forward逻辑
- 支持不同输入尺寸
- 参数初始化策略

### 数据处理
- Dataset + DataLoader
- 数据增强合理
- 支持分布式训练
- 内存高效

### 训练流程
- 使用Lightning Trainer
- 混合精度训练
- 梯度累积
- 早停机制

## 输出清单
- [ ] 模型定义
- [ ] 数据加载器
- [ ] 训练脚本
- [ ] 评估脚本
- [ ] 推理管道
- [ ] 预训练权重
- [ ] 实验日志
