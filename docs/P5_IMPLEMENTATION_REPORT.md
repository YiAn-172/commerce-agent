# P5 版本化 RAG 双索引实施报告

## 1. 结论

P5 已在 Docker full profile 中完成真实实现和验收，不是接口占位或 mock：

- Elasticsearch 8.17.7 与 Milvus 2.5.14 双索引均保存 2,780 个 chunk；
- 当前活动知识版本为 `kb_20260917_001`，两侧 alias 均为 `commerce_kb_active`；
- 固定 revision 的 BGE embedding 与 reranker 已在 CPU 容器真实推理；
- 建库、校验、原子激活、失败补偿、回滚、重新激活均已实际执行；
- 600 题、四种检索方案共 2,400 条结果已完成并写入 MySQL；
- P0-P5 全仓测试 65/65 通过，Ruff、mypy、Alembic 与 Compose 门禁通过。

该结果属于固定演示数据上的内部工程验证，不能表述为线上生产指标或人工盲测结果。

## 2. 固定模型与运行边界

| 用途 | 模型 | 固定 revision | 运行方式 |
|---|---|---|---|
| 向量召回 | `BAAI/bge-small-zh-v1.5` | `7999e1d3359715c523056ef9478215996d62a620` | CPU，512 维，归一化 |
| 重排 | `BAAI/bge-reranker-base` | `2cfc18c9415c912f9d8155881c133215df768a70` | CPU，Top-20 重排 |

模型通过 Hugging Face 官方仓库下载到 Docker named volume `hf-cache`。预取脚本只下载
SafeTensors、配置和 tokenizer 文件，避免同时缓存 PyTorch/ONNX 等重复大文件。`uv.lock`
将 PyTorch 锁定到 CPU wheel，不引入 CUDA runtime。

动态价格、库存、订单、物流、退款金额和售后资格不进入 RAG；这些事实仍必须由 MCP
工具实时读取。知识块只包含 FAQ、静态商品说明和规则解释。

## 3. 知识数据与索引

固定种子 `20260915` 生成的知识数据如下：

| 数据类型 | 文档数 | chunk 数 | 说明 |
|---|---:|---:|---|
| FAQ | 300 | 300 | 一问一答一块 |
| 政策/活动 | 80 | 80 | 保存规则版本、有效期和状态 |
| 商品说明 | 1,200 | 2,400 | 概览与 SKU 静态规格各一块 |
| 合计 | 1,580 | 2,780 | 指纹见 manifest |

当前数据集指纹：
`ba3616b94a89f353450a46f50d4f131e4494a570ac004465a74cad67872ee719`。

每个 chunk 保存 `chunk_id`、`doc_id`、`doc_type`、标题、正文、来源 URI、知识版本、
状态、生效/失效时间、内容哈希、product/SKU/category 和扩展 metadata。Elasticsearch
负责 BM25，Milvus 使用 IP 距离保存归一化向量。

## 4. 构建、验证和版本切换

构建总是写新物理对象 `commerce_kb_<version>`，不直接改活动 alias。验证阶段完成：

1. Elasticsearch 和 Milvus 条数必须等于 manifest 的 2,780；
2. 从 manifest 均匀抽取 100 条，对比两侧 content hash；
3. 对比 100 条的 12 个关键 metadata 字段；
4. 从 Milvus collection schema 读取并确认实际向量维度为 512；
5. 对 20 条经过意图类型路由的查询执行真实 Hybrid + Reranker 检索；
6. 验证引用的 chunk、版本和内容哈希能回指返回证据。

最终校验报告为 `reports/rag/verify_kb_20260917_001.json`，20/20 条冒烟通过。
首条查询包含模型冷启动，实测约 8.9 秒；其余热查询约 0.66-1.07 秒。

激活按 Elasticsearch alias、Milvus alias、MySQL 状态三部分处理。开发过程中两次故意
触发 Milvus alias API 兼容失败，Elasticsearch alias 都成功补偿回旧版本。修复后已实际
完成：旧版激活、新版激活、回滚旧版、重新激活新版。最终状态为：

- Elasticsearch alias -> `commerce_kb_kb_20260917_001`；
- Milvus alias -> `commerce_kb_kb_20260917_001`；
- MySQL 新版 `verified`，旧版 `superseded`。

## 5. 在线检索

在线链路为 BM25 Top-20 与 Vector Top-20 并行，使用 RRF 合并去重，再以
`BAAI/bge-reranker-base` 重排并返回 Top-5。时间状态过滤在两侧召回前执行。商品候选
ID 也在 Elasticsearch/Milvus 查询阶段预过滤，返回后再做一次防御性过滤。

用户意图已经路由到 FAQ、商品、政策或活动时，类型过滤作为域内约束；未路由的开放检索
才使用 reranker `0.8` 阈值拒答。这是因为口语化的合法域内查询分数最低可到 0.01，单一
全局阈值会大量误拒答。无答案集中特意保留了一个因编号与 SKU 偶然相似、分数约 0.62
的 hard negative，因此开放检索阈值不是从容易样本随意选择的。

检索证据以 `trust="untrusted"` 包装，正文无法通过伪造结束标签跳出证据边界。引用保存
chunk ID、文档 ID、来源、知识版本、内容哈希和生效时间。

## 6. 600 题四路评测

评测集包含商品 250、政策 200、活动 100、无答案 50。50 条负样本是跨科学、历史、
实时私人信息、认证秘密和虚构实体的独立问题，并包含编号碰撞 hard negative；不是同一
模板只改编号。检查点按 `(variant, case_id)` 逐条落盘，长任务可恢复。

| 方案 | Recall@5 | MRR | nDCG@5 | 引用命中率 | 引用可验证率 | 无答案拒答率 | P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BM25 | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 0.00% | 3 ms | 6 ms |
| Vector | 56.18% | 55.50% | 55.66% | 56.18% | 100.00% | 0.00% | 201 ms | 400 ms |
| Hybrid RRF | 100.00% | 97.55% | 98.19% | 100.00% | 100.00% | 0.00% | 397 ms | 400 ms |
| Hybrid + Reranker | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 843 ms | 1,117 ms |

报告文件：

- `reports/eval/rag_v1.json`：汇总与全部 2,400 条结果；
- `reports/eval/rag_v1_results.jsonl`：可恢复检查点；
- MySQL `evaluation_runs/evaluation_results`：1 个完成运行、2,400 条明细。

BM25 的满分主要来自演示语料结构规整、评测问法包含 SKU/规则代码等强词面信号，不能据此
宣称真实用户问题上也有 100%。Vector 单路较弱说明中文短问句的通用向量召回需要继续
用人工盲测集优化，当前生产候选应使用 Hybrid + Reranker。

## 7. 自动化门禁

- `pytest -q`：65 passed；
- `ruff check .`：passed；
- `mypy packages apps services scripts evals`：87 source files passed；
- `alembic check`：No new upgrade operations detected；
- `scripts/validate_demo_data.py`：18 张表、固定数量和业务不变量通过；
- `docker compose --profile demo-full config --quiet`：passed。

## 8. 未完成边界

P5 的内部工程闭环已经完成，但以下内容仍属于后续工作：

- 由真实标注员制作不含模板提示的盲测查询，并单独报告置信区间；
- P6 LangGraph 将意图类型、候选商品 ID、证据和引用接入主图；
- P8 扩充 prompt/RAG injection 攻击集，而不只依赖证据边界单测；
- P9 在固定硬件上拆分 embedding、ES、Milvus、reranker 各阶段 P99 和并发吞吐；
- 全新机器冷启动、备份恢复和长时间稳定性仍需 P10 验收。
