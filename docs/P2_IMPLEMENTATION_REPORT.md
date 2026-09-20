# P2 意图模型训练、校准与 ONNX 服务实现报告

生成日期：2026-09-16  
阶段状态：**工程实现完成；内部测试通过；人工冻结金标验收待完成**

## 1. 本阶段交付

- TF-IDF + LinearSVC 可复现基线；
- `hfl/chinese-macbert-base` 的三学习率搜索、三随机种子训练与早停；
- 验证集温度缩放、coverage-risk 阈值联合搜索；
- OOS 概率、最大 Softmax 和能量分数组合门控；
- ONNX 动态 batch/sequence 导出、2,500 条一致性校验；
- 200 次预热、2,000 次 CPU 端到端延迟基准；
- 只含 Tokenizer、ONNX Runtime 和校准配置的独立 Docker 推理镜像；
- `/predict`、`/batch_predict`、`/health`、`/health/live`、`/health/ready`；
- 高优先级人工/严重投诉规则、多意图规则、置信度与 margin 门控；
- 冻结金标失败关闭评测脚本。

## 2. 可复现输入

| 项目 | 固定值 |
|---|---|
| 数据版本 | `intent_v1` |
| 训练/验证/内部测试 | 20,000 / 2,500 / 2,500 |
| 标签数 | 16 个互斥主意图 |
| 基座模型 | `hfl/chinese-macbert-base` |
| Hugging Face revision | `a986e004d2a7f2a1c2f5a3edef4e20604a974ed1` |
| 许可证 | Apache-2.0 |
| 最大长度 | 128；25,000 条覆盖率 100%，最长 92 tokens |
| 学习率搜索 | `1e-5`、`2e-5`、`3e-5` |
| 正式随机种子 | 13、42、2026 |
| 最终学习率 | `3e-5`，仅由验证集 Macro-F1 选择 |
| 部署 checkpoint | `seed13_lr3em05`，仅由验证集 Macro-F1 选择 |

配置位于 `configs/intent/macbert_v1.yaml`。模型 revision 已由可变的 `main` 固定为实际下载
commit，报告记录配置和数据 manifest 的 SHA256。

## 3. 基线与 MacBERT 结果

以下全部是 **内部开发测试集**，不是人工冻结金标，不应写成线上效果或最终验收数字。

| 模型/种子 | 最佳 epoch | Accuracy | Macro-F1 | Top-2 Accuracy |
|---|---:|---:|---:|---:|
| TF-IDF + LinearSVC | - | 94.44% | 94.27% | 98.00% |
| MacBERT seed 13 | 5 | 95.92% | 95.80% | 98.96% |
| MacBERT seed 42 | 4 | 96.08% | 95.86% | 98.96% |
| MacBERT seed 2026 | 5 | 96.00% | 95.85% | 99.04% |
| MacBERT 三种子均值 | - | **96.00% ± 0.08%** | **95.84% ± 0.03%** | **98.99% ± 0.05%** |

规则关键词基线 Accuracy 30.64%、Macro-F1 33.45%，用于证明不能用散落规则代替分类模型。
完整逐类指标和混淆矩阵位于 `reports/training/tfidf_v1.json` 与
`reports/training/macbert_v1.json`。

## 4. 校准、路由阈值和 OOS

温度缩放只使用验证集，温度为 `1.304166`：

- 验证集 ECE：2.51% → 1.09%；
- 内部测试集校准后 ECE：1.56%；
- 未使用测试集选择温度或阈值。

`configs/routing/thresholds.intent_v1.yaml` 由脚本生成，不是人工挑点：

| 参数/结果 | 值 |
|---|---:|
| 自动路由最小置信度 | 0.98 |
| 自动路由最小 Top-2 margin | 0.50 |
| 澄清下界 | 0.91 |
| 验证集自动路由覆盖率 | 85.44% |
| 验证集选择性准确率 | 99.44% |

OOS 门控使用三个特征：`out_of_scope` 类概率、最大校准概率、能量分数。验证集拟合的
逻辑回归参数以均值、尺度、系数、截距和阈值保存到 `calibration.json`，线上仅做数值运算，
不依赖 sklearn。

| 数据 | AUROC | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| 验证集 | 99.39% | 96.03% | 93.08% | 94.53% |
| 内部测试集 | 99.02% | 87.59% | 92.31% | 89.89% |

“请解释量子引力”仍可能被主模型高置信度判为闲聊，但当前置信度门控会进入澄清而非自动
路由。这个已知难例没有被删除或改测试标签，应该进入后续人工挑战集。

## 5. ONNX 发布门禁

| 检查 | 实际结果 | 门槛 |
|---|---:|---:|
| ONNX checker | 通过 | 必须通过 |
| 对照样本 | 2,500 | 2,500 |
| PyTorch/ONNX Top-1 一致率 | **100.00%** | >= 99.9% |
| 最大 logits 绝对误差 | `1.955e-05` | 记录 |
| 平均 logits 绝对误差 | `5.637e-07` | 记录 |
| CPU P50 | 22.62 ms | 记录 |
| CPU P95 | **24.14 ms** | < 120 ms |
| CPU P99 | 29.00 ms | 记录 |

CPU 基准范围包含分词和 ONNX Session，使用 2 个 intra-op 线程、1 个 inter-op 线程，
batch=1，预热 200 次后连续测量 2,000 次。该数字来自当前开发机，不能直接承诺其他 CPU
或并发下的 SLA。

发布脚本只有在一致率门禁通过后才原子替换
`models/intent_classifier/current`。当前 ONNX SHA256：
`fb019e74904bf499c6928cd7966b091fa5f656459998e1349f02ac7c44e70e06`。

## 6. Docker 服务验证

`intent-service` 使用专门的 `intent-runtime` 镜像，不安装 PyTorch。容器以非 root 用户运行，
启动时加载一次 Tokenizer 和 ONNX Session，完成预热后 readiness 才返回 200。Docker
healthcheck 使用 `/health/ready` 而非仅检查进程存活。

已验证：

- 容器状态为 `healthy`；
- 商品检索正确进入 `shopping`；
- 物流意图返回 Top-2、校准概率、margin、能量和 OOS 分数；
- 明确转人工由优先规则覆盖并记录 `model_label`、`matched_rule`、`route_source`；
- “查物流，另外退货”被多意图规则标记并进入澄清；
- 批量接口限制 1–128 条；
- 模型缺失或损坏时 readiness 返回 503，不以空模型假装就绪。

## 7. 尚未完成的真实验收

下列工作必须由人工数据完成，代码不会用内部测试集冒充：

1. `data/annotation/gold_annotation_queue_v1.csv` 的 1,600 条双人标注、仲裁与冻结；
2. 冻结金标上的 TF-IDF/MacBERT Accuracy、Macro-F1、逐类指标和混淆矩阵；
3. 400 条挑战集上的多意图 F1、OOS、近邻意图和澄清后路由率；
4. 真实并发 HTTP 压测和目标机器资源规格下的 SLA。

`scripts/finalize_intent_gold.py` 会在标注不完整、Kappa 不足、重复、PII 或训练泄漏时失败；
`apps.intent_service.training.evaluate_gold` 会在冻结金标或 manifest 缺失时失败。当前这些失败是
正确状态，不应绕过。

## 8. 关键命令

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml --profile train run --rm intent-trainer `
  uv run python -m apps.intent_service.training.train
docker compose -f compose.yaml -f compose.gpu.yaml --profile train run --rm intent-trainer `
  uv run python -m apps.intent_service.training.calibrate
docker compose --profile train run --rm intent-trainer `
  uv run python -m apps.intent_service.training.select_thresholds
docker compose --profile train run --rm intent-trainer `
  uv run python -m apps.intent_service.training.export_onnx
docker compose -f compose.yaml -f compose.gpu.yaml --profile train run --rm intent-trainer `
  uv run python -m apps.intent_service.training.verify_onnx --samples 2500
docker compose --profile dev-lite up -d --build intent-service
```

机器可读证据位于 `reports/training/`，模型文件位于 `models/intent_classifier/`；两者按项目
数据边界不提交 Git。
