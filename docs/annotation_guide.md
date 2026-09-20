# 意图金标与挑战集人工标注指南

`data/annotation/gold_annotation_queue_v1.csv` 是待标注队列，不是金标集。只有
`scripts/finalize_intent_gold.py` 的全部门禁通过后，才会生成
`data/processed/intent_gold_v1.parquet`。

## 操作规则

1. 标注者先阅读 `docs/intent_label_spec.md`，看不到模型预测或另一名标注者结果。
2. 每条填写 `annotator_1_text` 和 `annotator_1_label`；文本保持用户口吻，不写客服答案。
3. `requires_independent_human_rewrite=true` 的行不得从训练语料复制，必须由人工独立写句子。
4. `double_annotation_required=true` 的 320 行由第二名标注者独立填写
   `annotator_2_text/annotator_2_label`。
5. 分歧讨论后填写 `adjudicated_text/adjudicated_label`，并把 `status` 改为
   `adjudicated`。不得为了提高一致率删除困难样本。
6. 任何手机号、邮箱、身份证号、真实订单号、姓名或详细地址都必须使用占位符或改写。
7. `challenge_annotation_queue_v1.csv` 同样需要人工确认；多意图最多保留两个业务动作，
   低信息样本不强行猜单标签。

## 冻结命令

```powershell
docker compose --profile tooling run --rm worker uv run python scripts/finalize_intent_gold.py
```

脚本会拒绝：未仲裁行、非法标签、每类不足 100 条、Cohen's Kappa 低于 0.80、与
25,000 条训练语料重复、金标内部重复或残留 PII。未通过前，项目报告只能写
“1,600 条待人工复核队列已生成”，不能写“1,600 条冻结金标已完成”。

完整的盲标拆分、同步、P9 复核和进度命令见 `docs/HUMAN_REVIEW_RUNBOOK.md`。先运行：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py prepare
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py status
```
