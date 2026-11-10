# 待测事项清单（2026-10-27）

> 用途：记录当前已实现、但尚未在真实 V2 主进程/真实外部服务里完成的验证项。
> 完成一项就在 `[ ]` 改成 `[x]`，并在行尾补充日期与结果。

## 1. Live2D / VTS

- [ ] **真实 V2 主进程启用 `live2d` 域**：`python -m LLM_v2.main --live2d`，确认端口 8001 连接、首次授权、`live2d_status` 正常。
- [ ] **基础表情 + 反应层**：触发情绪象限变化，确认 `LIVE2D_EXPRESSION` 生效；触发 `ERROR/TASK_FAILED` 后 3 秒回基础表情，`OPERATOR_DONE` 后 1.5 秒回基础表情。
- [ ] **嘴型/目光 20Hz 注入**：移动鼠标，确认 `EyeLeftX/Y`、`EyeRightX/Y` 跟随；触发 `TTS_SPEAK`，确认 `MouthOpen` 有包络；确认 `MouthSmile` 随情绪变化。
- [ ] **参数还原**：停止 `live2d` 域，确认四个/六个跟踪参数写回基线，不残留。
- [ ] **授权异常**：VTS 已有授权窗口时，确认错误 51 不刷屏、30 秒冷却生效；重启 VTS 后可恢复。
- [ ] **表情缺失回退**：让映射到不存在的表情名，确认回退 `neutral_01`/首个可用表情，不报错。
- [ ] **长时间稳定性**：连续 10 分钟对话 + 弹幕，确认 20Hz 参数 tick 不阻塞主事件流、不内存增长。

## 2. 记忆内核

- [ ] **WebUI 记忆页回归**：手动添加 → 编辑后采纳 → 拒绝 → 检索 → 审计，刷新后状态一致。
- [ ] **对话链路**：发送“记住……”→ 生成摘要与待确认建议 → 采纳 → 下一轮召回生效。
- [ ] **重启持久化**：重启后端，确认 `turn_summaries`、`mem_items`、`mem_proposals` 不丢。
- [ ] **隐私隔离**：私聊不串用户、群聊/公开渠道不注入用户档案。
- [ ] **与 Live2D 并发**：记忆 worker 摘要与 20Hz 参数注入同时运行时不互相阻塞。

## 3. 联网研究 / 知识学习

- [ ] **research 域链路**：`wr_search → wr_read → wr_note_save → wr_notes` 在真实 V2 中可用，笔记能注入主播 prompt。
- [ ] **研究边界**：无结果、网页超时、LLM 总结失败时按预期降级，不编造。
- [ ] **知识库旧代码**：确认 MinIO/Milvus 可连接；用 PDF/DOCX/CSV 跑旧 `KnowledgeBaseManager.add_document` 与 `search`。
- [x] **V2 接入**：知识库检索归思维脑（`WillKnowledge`），主播脑只读共享缓存；`RAGAdapter` 回归纯历史对话。
- [x] **嵌入失败保护**：已移除随机向量兜底，嵌入失败直接失败/跳过。

### 知识库 P0（已实现，待真实书籍/对话验证）

- [x] 新建 `knowledge_chunks` 集合：HNSW + 标量索引，dim 2560。
- [x] 文档/文本按 1000 字、150 字重叠分块，保留 page/section/hash。
- [x] 嵌入失败直接失败，不再写随机向量；查询嵌入带 LRU 缓存。
- [x] 思维脑检索并发布共享缓存，带标题/页码引用；主播脑回复前读取（最长等 1.2s，短句 0.2s 宽限）。
- [x] 知识缺口：命中分低于 `knowledge_gap_min_score`（默认 0.76）时记缺口，进入意志脑决策上下文 `【我还没弄明白的】`。
- [x] 真实 Milvus 2.6.7 冒烟：建集合 → 入库 → 检索（score 0.7808）→ 删除 → 重建空集合。
- [x] 真实 PDF 书籍入库：`llm_survey_2303.18223.pdf`（144 页、86 万字符、无空页）→ 1073 块，页级引用正确。
- [x] 大批量：单次导入 1073 块（>1000），批量嵌入 16/批、insert 500/批、flush 与加载状态正常；检索 70~90ms（首次 0.8s 含加载）。
- [x] 分块边界修复：重叠片段原本从词中间截断（块首出现 `nities and challenges`），已裁到词/句边界。
- [x] **真实主播对话**：真实 LLM + 真实 Milvus 联调（WebUI 源）。在题问题引用 `（llm_survey_2303.18223 p.16/p.3/p.39）`；离题问题知识注入为 0 条，主播如实说不会，并记入知识缺口。回复耗时 1.8~6.2s（含 LLM）。
- [ ] **引用仍需抽检**：模型偶尔把资料说成"之前提到的那篇综述"，且页码跟随检索结果，检索挑到综述页时引用会指向 p.3/p.16 而不是具体章节。
- [ ] **扫描版 PDF**：本地日语教材 333 页仅 856 字符，P0 文本解析无法入库，需 OCR（MinerU/PaddleOCR/Docling）。

### 知识库 P1 混合检索（已实现，收益有限）

- [x] `RAG/hybrid.py`：英文词 + 中文二字组分词、BM25、加权 RRF，无第三方依赖。
- [x] `KnowledgeChunkManager.search()` 双路召回：向量候选池（默认 40）+ `content like` 词法候选（默认 200），加权 RRF 融合。
- [x] 词法噪声控制：候选池内 df 过高的查询词剪掉（human/model/language 之类）；只有词法分 ≥ 池内最强值 50% 的块参与融合。
- [x] 配置项：`hybrid_enabled / hybrid_pool / hybrid_lexical_limit / hybrid_max_terms / hybrid_rrf_k / hybrid_lexical_weight / hybrid_lexical_min_ratio`（环境变量可覆盖）。
- [x] 单元测试 `tests/test_hybrid.py`（分词、BM25、RRF 加权、融合提升、降级）。
- [ ] **实测收益**：arXiv 综述 6 个查询，MRR dense=0.7556；词法权重 0.1/0.25/0.3 无提升（0.3 时 en-RLHF 1→2），0.4/0.5 把 en-CoT 从 #3 提到 #1 但 en-RLHF 掉到 #2，MRR 0.7833。默认取 0.1（不回归）。
- [ ] **结论**：该语料上排序主要由向量决定，词法只能做近似平票时的微调；真正瓶颈是泛化综述页（p.3）语义上就居中。后续优先级：LLM/交叉编码器 rerank > 换嵌入模型 > 调分块大小（已实验：把 p.3 切到 250 字，top1 反而升到 0.852，说明不是分块粒度问题）。

### 知识库 P1.5 rerank（已实现）

- [x] `LLM_v2/core/reranker.py`：两种实现——`LocalReranker`（本地 cross-encoder，GPU 逐对打分）与 `LLMReranker`（意志脑模型判断「哪几段真正回答了问题」）。
- [x] **关闭 rerank 的四种方式**：`config.ini [rag] rerank_enabled=false`、环境变量 `RAG_RERANK_ENABLED=false`、启动参数 `--no-rerank`、`rerank_backend=off`。
- [x] **后端选择**：`rerank_backend = auto|local|llm|off`；auto 时本地模型存在就用本地，否则用 LLM。本地模型缺失或加载/推理失败一律回退原顺序。
- [x] 本地模型下载：`python scripts/download_reranker.py`（默认 `BAAI/bge-reranker-v2-m3` → `models/`，走 hf-mirror + `HF_HUB_DISABLE_XET=1`，已加入 `.gitignore`）；实测 2187MB。
- [x] **启动预热默认关闭**：`rerank_warmup = false`。关闭时首个知识问题不阻塞（后台线程加载模型，本次沿用原顺序，约 9.4s 后就绪）；开启后启动即加载（约 9.4s / 2GB 显存）。
- [x] 召回池与候选数分离：`knowledge_recall_pool=20`（hybrid 召回）→ `rerank_candidates=10`（送模型）→ `knowledge_top_k=3`。
- [x] 失败即回退：解析失败/超时/模型报错一律保留原顺序，不影响回复。
- [x] 预筛：只有像"在问知识"的消息才检索（`WillEngine._needs_knowledge`），闲聊不触发，避免每条消息多等 1 秒多。
- [x] 单元测试 `tests/test_reranker.py`、`tests/test_will_knowledge.py`（提示词、解析、回退、候选截断、本地打分、工厂选择、预热开关）。
- [x] **三种模式实测（同一批 5 问）**：
  - LLM：1.3~1.5s/次，排序最稳（预训练 `16,17,18`；局限 `97,3,28`）。
  - 本地 bge-reranker-v2-m3：104~307ms/次（热），排序优于纯 hybrid（思维链去掉 p.3；局限 `96,70,97`），但略逊 LLM。
  - off：80ms 左右，泛化页（p.3/p.16）压过具体章节。
- [x] 真实链路：知识问题引用 `（llm_survey_2303.18223 p.97/p.3/p.28）`；闲聊知识注入 0 字符。
- [ ] **rerank 延迟**：LLM 后端每次多 1.0~1.2s；本地后端热态仅 0.1~0.3s，但首次需 9.4s 加载（可用 `rerank_warmup=true` 挪到启动）。
- [ ] **思维链召回缺口**：`p.32`（CoT 章节）在 hybrid 前 20 都没出现，rerank 无法补救；需要更强的嵌入模型或关键词兜底。


## 4. 其他

- [ ] **launcher 一键启动**：`launcher_start_all` 240 秒超时下，双实例 + ALAS + MAA 全链路成功。
- [ ] **MCP 桥接**：配置一个真实 MCP 服务，确认动态工具注册、调用、刷新、卸载。
