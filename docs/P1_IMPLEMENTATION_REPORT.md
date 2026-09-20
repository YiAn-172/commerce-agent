# P1 意图数据工程实施报告

版本：`intent_v1`  
执行日期：2026-09-15 至 2026-09-16  
状态：自动化数据阶段完成；人工金标与挑战集仍待标注、复核和冻结

## 1. 交付结论

P1 已在 Docker 环境完成可运行的数据工程闭环，最终产物为恰好 25,000 条中文
电商一级意图语料：训练 20,000、验证 2,500、内部测试 2,500。16 类目标数全部命中，
其中受控合成 10,000 条，公开来源 15,000 条。最终 Parquet、来源 manifest、配置、
语义模型 revision、报告和输出 SHA256 已形成证据链。

本报告不把待标队列冒充已完成金标。1,600 条队列和 400 条挑战集草案已经生成，
但只有双人标注、仲裁、Kappa、PII 和泄漏门禁全部通过后，
`scripts/finalize_intent_gold.py` 才会生成 `intent_gold_v1`。

## 2. 实际数据来源与许可门禁

| 来源 | 锁定 revision | 声明许可/用途 | 最终入选 |
|---|---|---|---:|
| Full E-commerce Chatbot Dataset | `1726d95a7ca7876b1a99366976ca0bc6ef59eb2f` | 聚合页 MIT；只允许逐条来源白名单，本地实验，不重新分发 | 5,385 |
| Bitext Retail/E-commerce | `12dd624ddcd3057382b2faad661bcda1fa869491` | CDLA Sharing 1.0，保留署名与来源 | 8,045 |
| MASSIVE zh-CN | `2d22dfb0ce80a74e8350417c0435223ac0e2ef7c` | 上游 CC BY 4.0，只映射语义兼容标签 | 1,041 |
| SMP2017 ECDT | `25ddbc9e10f0051d4871b0474052fb1adc5354f7` | README 研究用途声明，仅本地非商业实验 | 529 |
| DeepSeek 受控模板 | Prompt `intent_templates_v1`，seed `20260915` | 本项目生成，逐条记录模型/Prompt/模板族/时间 | 10,000 |

目录哈希、文件数、字节数、下载时间、上游说明和责任人在
`data/manifests/data_sources.yaml` 中。Full E-commerce 中 ASOS/Amazon 来源记录被隔离，
只接受 `synthetic_api_generated`、`bitext_customer_support`、`bitext_retail_ecom`。

## 3. 候选池与 DeepSeek 处理

公开原始数据抽取后形成 64,545 条统一候选；冻结候选池选中 26,174 条公开记录。
英文候选通过 OpenAI-compatible DeepSeek API 批量翻译和中文口语改写：

- 每批 25 条（支持 20–50 条），最大并发受命令参数限制；
- JSON 对象和 Pydantic 严格校验，样本 ID 集必须完全一致；
- 数字和方括号占位符在本地二次校验；
- 429、5xx、超时和格式错误有限重试；成功批次立即追加，失败可断点恢复；
- 显式关闭 thinking，温度为 0，输入内容视为不可信数据；
- 最终翻译检查点包含 23,574 个唯一样本 ID。

首次高并发失败后的断点文件曾有 6,725 条重复记录，其中 5,670 个 ID 的响应存在差异；
`compact_translations.py` 按“实体完整、无自报问题、最新记录”选择并保留审计历史。
最终质量门禁拒绝 2 条实体不完整翻译。模型标记的错别字、粗俗情绪等 2,380 条记录
作为鲁棒性表达保留，但未绕过本地实体校验。

已记录的完成轮次成本上限估算显示翻译约 0.67 美元，受控模板主要完成轮次低于
0.10 美元；由于早期失败窗口在写入累计用量前中止，不能把二者相加冒充精确账单。
后续尝试补充模板时 DeepSeek 返回 HTTP 402 `Insufficient Balance`，任务按失败关闭；
最终数据使用此前已经生成并通过门禁的 15,000 条合成候选，没有伪造或静默换源。

## 4. 清洗与三层去重

清洗顺序为 NFKC、HTML 实体/标签、不可见字符、空白归一化、PII 替换、长度门禁：

- 输入外部候选：26,174；受控合成候选：15,000；
- 长度拒绝：6；翻译实体不完整拒绝：2；
- 精确重复删除：4,553；跨标签精确冲突删除：385（110 个冲突哈希）；
- 精确层输出：36,228；
- MinHash 0.90 删除：108；
- BGE 删除：300；最终质量池：35,820。

BGE 使用 `BAAI/bge-small-zh-v1.5`，锁定 revision
`7999e1d3359715c523056ef9478215996d62a620`。阈值不是拍脑袋写死：

| 余弦阈值 | BGE 删除 | 输出 | 固定标签/来源配额可行 |
|---:|---:|---:|---|
| 0.960 | 8,837 | 27,283 | 否 |
| 0.985 | 3,107 | 33,013 | 否 |
| 0.990 | 1,815 | 34,305 | 否 |
| 0.997 | 300 | 35,820 | 是 |

低阈值把同一意图下的有效不同表达大量折叠。最终 0.997 在精确哈希和 MinHash 之后
只承担近乎同一向量表达的门禁；完整校准记录见
`reports/data/bge_threshold_calibration.json`。

## 5. 最终数据与切分

| 指标 | 结果 |
|---|---:|
| 总量 | 25,000 |
| train / validation / test | 20,000 / 2,500 / 2,500 |
| 公开来源 / 受控合成 | 15,000 / 10,000 |
| 需要 DeepSeek 翻译的入选样本 | 13,430 |
| 清洗阶段发生 PII 替换 | 1,164 |
| 最终残留 PII | 0 |
| 重复 sample ID / 文本哈希 | 0 / 0 |
| 文本长度 min / P50 / P95 / P99 / max | 2 / 17 / 35 / 56 / 130 |
| 单一模板族最大入选量 | 2 |

切分不是普通随机抽样。代码对 `source_dialogue_id`、`template_family`、
`semantic_cluster_id` 建传递闭包，整个连通组只能进入一个 split。最终 25,000 条有
24,999 个联合组，所有组、单字段分组、样本 ID 和规范化文本哈希跨 split 泄漏均为 0。

最终标签计数与需求规格完全一致；详细分布见 `reports/data/intent_v1.json`，数据与配置
哈希见 `data/manifests/intent_v1.json`。

## 6. 人工标注队列

已生成以下本地、不提交 Git 的待标文件：

- `data/annotation/gold_annotation_queue_v1.csv/.parquet`：1,600 条，每类 100；
- 其中 366 条要求人工独立改写，超过 20% 最低要求；
- 320 条要求双人独立重叠标注，正好占 20%；
- `data/annotation/challenge_annotation_queue_v1.csv/.parquet`：400 条；
- 挑战集构成为多意图 160、低信息 120、OOS 80、近邻边界 40。

这些文件状态是 `pending_human_review_not_gold`。冻结脚本要求每类恰好 100、全部仲裁、
Cohen's Kappa 不低于 0.80、与 25k 无精确泄漏、内部无重复且无残留 PII。

2026-09-18 已增加 `scripts/human_review_workspace.py` 和
`docs/HUMAN_REVIEW_RUNBOOK.md`。工作区会生成不含预设类别的第一标注者盲标文件、320 条
第二标注者盲标文件、独立仲裁文件、挑战集文件，以及 P9 路由/工具选择/E2E 复核文件。
每 40–50 条划分一个 `batch_id`，支持按源行 SHA256 保留或失效复核结果。冻结金标现在还
要求标注者 ID、仲裁者 ID 和带时区 ISO 8601 时间；双标行禁止使用同一标注者两次。
当前实际进度仍为第一标注 0/1,600、第二标注 0/320、仲裁 0/1,600、挑战集 0/400。

另已生成独立的 AI 辅助草稿：1,600 条金标建议和 400 条挑战集建议。金标草稿中有
440 条被标记为需要重点人工检查；366 条 AI 改写无精确重复、无 25k 精确哈希重叠且
PII 检测为 0。草稿状态固定为 `ai_assisted_draft_not_human_review`，不计入上述人工进度，
也不能通过金标冻结门禁。

## 7. 自动化验收结果

- Docker worker 与 trainer 镜像：构建成功；
- BGE 模型下载、revision 解析与全量推理：成功；
- `docker compose config --quiet`：通过；
- Ruff：通过；
- 严格 MyPy：35 个源码文件通过；
- Pytest：17 passed（1 条第三方 Starlette deprecation warning）；
- 许可证门禁：4 个来源通过；
- 25k 泄漏门禁：通过；
- 25k PII 门禁：通过；
- 未标注队列执行金标冻结：按预期失败关闭，未生成伪金标文件。

严格 MyPy 首次扩展到 `scripts/` 后发现 pandas/datasets/sklearn/datasketch/torch/
transformers 缺少类型桩；这些第三方模块已加精确 scoped ignore，脚本自身的真实 `Any`
返回已修复。最终总门禁全部通过。

## 8. 已知限制与下一阶段

1. 人工标注需要真实的第二名标注者参与，自动化不能替代；因此金标、Kappa 和最终模型
   指标仍未完成。
2. SMP2017 没有标准 SPDX LICENSE，只按 README 的研究用途说明在本地非商业实验中使用，
   不重新分发。
3. DeepSeek 402 说明外部账户余额是可观测依赖；继续 P2 前如需新增 LLM 数据，应先充值
   或明确批准其他模型，不能静默切换。
4. 当前 25k 内部测试集用于开发阶段回归；最终对外 Accuracy/Macro-F1 必须在独立人工
   冻结金标上报告。
5. 下一阶段 P2 才实现 TF-IDF/LinearSVC 基线、MacBERT 三随机种子微调、温度校准、ONNX
   导出和 CPU 延迟测试。当前不得声称模型指标已达到简历目标。
