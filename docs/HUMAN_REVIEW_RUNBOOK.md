# P1 与 P9 人工复核操作手册

日期：2026-09-18

本手册用于完成 P1 人工金标和 P9 人工评测。自动化工具只负责拆分、同步、校验和统计，
不会自动填写人工结论，也不会把模板结果提升为人工金标。

## 1. 初始化与查看进度

在项目根目录运行：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py prepare
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py status
```

人工工作文件位于 `data/annotation/review_work/`。`prepare` 可以重复执行：只有来源行哈希
未变化的编辑结果才会被保留。

所有时间使用带时区 ISO 8601，例如：

```text
2026-09-18T18:30:00+08:00
```

## 2. P1：1,600 条人工金标

### 2.1 第一标注者

把 `p1_gold_annotator_1_blind.csv` 交给第一标注者。该文件不包含预设类别，避免标签泄漏。
逐行填写：

- `annotator_1_text`：最终判断所依据的用户表达；不是客服回答。
- `annotator_1_label`：必须来自 `docs/intent_label_spec.md` 的 16 个标签。
- `annotator_1_id`：稳定的标注者代号，例如 `ann_a`。
- `annotator_1_reviewed_at`：带时区的 ISO 8601 时间。

对于 `requires_independent_human_rewrite=True` 的行，`seed_text` 为空，必须由真人独立写一句
自然用户表达，不能从 25,000 条语料复制。其他行也必须检查原句；有歧义、错标签或不自然时
可以改写。

### 2.2 第二标注者

把 `p1_gold_annotator_2_blind.csv` 交给另一名真人。该文件只有 320 条重叠样本，也不包含
第一标注者答案和预设类别。填写：

- `annotator_2_text`
- `annotator_2_label`
- `annotator_2_id`
- `annotator_2_reviewed_at`

第二标注者 ID 必须与第一标注者不同。两人不得互相查看结果。

### 2.3 第一次同步

两份盲标文件填写后运行：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py sync
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py prepare
```

第二次 `prepare` 会把两名标注者结果刷新到 `p1_gold_coordinator.csv`，供仲裁者查看。

### 2.4 仲裁

仲裁者打开 `p1_gold_coordinator.csv`，比较两名标注者结果并填写：

- `adjudicated_text`
- `adjudicated_label`
- `adjudicator_id`
- `adjudicated_at`
- `status=adjudicated`

所有 1,600 行都必须仲裁。不得删除难例，也不得为提高 Kappa 修改两名标注者原始结果。
文本中不得保留真实手机号、邮箱、身份证、姓名、地址或真实订单号。

仲裁完成后再次运行：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py sync
uv run --cache-dir .uv-cache --frozen python scripts/finalize_intent_gold.py
uv run --cache-dir .uv-cache --frozen python -m apps.intent_service.training.evaluate_gold
```

冻结门禁要求：

- 1,600 行全部仲裁；
- 每类恰好 100 条；
- 320 条双标样本完整；
- 两名标注者不同；
- Cohen's Kappa 不低于 0.80；
- 无 PII；
- 金标内部无重复；
- 与原 25,000 条语料无精确泄漏。

## 3. P1：400 条挑战集

打开 `p1_challenge_review.csv`，逐行填写：

- `adjudicated_intents`：JSON 数组，例如 `["cancel_order", "product_search"]`；低信息案例
  可以填写 `[]`，最多两个业务意图。
- `reviewer_id`
- `reviewed_at`
- `notes`：可选。
- `status=adjudicated`

完成后运行：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py sync
uv run --cache-dir .uv-cache --frozen python scripts/finalize_intent_challenge.py
```

挑战集与 1,600 条冻结金标分开统计，不能互相替代。

## 4. P9：路由复核

打开 `p9_routing_review.csv`。逐行检查 `text`、`previous_user_text`、
`proposed_route` 和 `proposed_decision`。

如果建议正确：

```text
review_status=reviewed
verdict=accept
reviewer_id=<你的稳定代号>
reviewed_at=2026-09-18T18:30:00+08:00
```

如果建议错误：

```text
review_status=reviewed
verdict=correct
adjudicated_route=<正确 route；clarify 时留空>
adjudicated_decision=auto_route | clarify | safe_reply
```

`safe_reply` 决策要求 route 也是 `safe_reply`。全部完成后：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py sync
uv run --cache-dir .uv-cache --frozen python -m evals.routing.review finalize
uv run --cache-dir .uv-cache --frozen python -m evals.routing.run `
  --suite routing_800_v1_human_gold
```

## 5. P9：工具选择复核

打开 `p9_tool_selection_review.csv`。检查用户文本和 `proposed_tools`。

正确时填写 `verdict=accept`。错误时填写 `verdict=correct`，并在 `adjudicated_tools` 中填写
JSON 数组，例如：

```json
["search_products", "check_inventory"]
```

不应调用工具时填写 `[]`。同时填写 `review_status=reviewed`、`reviewer_id` 和
`reviewed_at`。全部完成后：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py sync
uv run --cache-dir .uv-cache --frozen python -m evals.tool_selection.review finalize
uv run --cache-dir .uv-cache --frozen python -m evals.tool_selection.run `
  --suite tool_selection_1000_v1_human_gold
```

## 6. P9：E2E 72 条人工复核

打开 `p9_e2e_review.csv`，阅读 `conversation`，填写：

- `relevance_score`：1–5；
- `clarity_score`：1–5；
- `safe_and_helpful`：`true` 或 `false`；
- `verdict`：`pass` 或 `fail`；
- `review_status=reviewed`；
- `reviewer_id`；
- `reviewed_at`；
- `notes`：失败时写明原因。

完成后：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py sync
uv run --cache-dir .uv-cache --frozen python -m evals.e2e.manual_review
```

当前门禁要求 72 条全部有效，并且没有 `fail`，才能通过 E2E 人工质量门禁。

## 7. 每次工作结束时

建议每完成 20–50 行执行一次：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py sync
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py prepare
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py status
```

不要直接修改哈希列、案例 ID、原始文本、建议标签或建议工具。来源发生变化时，相应人工结果
会按行哈希失效，防止旧结论错误复用。

## 8. AI 辅助草稿（不能替代人工复核）

可运行：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/generate_ai_review_drafts.py
```

输出位于 `data/annotation/review_work/ai_drafts/`。这些文件只包含 `ai_*` 建议字段，
统一标记为 `ai_assisted_draft_not_human_review`，不会写入真人 `reviewer_id`、不会把权威
队列改成 `reviewed/adjudicated`，也不会满足发布门禁。

当前草稿结果：

- P1 金标建议 1,600 条，其中 440 条因模型标签分歧、低置信度或安全弃权被标记为
  `ai_needs_human_attention=true`；
- 366 条 AI 改写草稿无精确重复、与 25,000 条语料无精确哈希重叠、PII 检测为 0；
- P1 挑战集建议 400 条；
- P9 路由建议 800 条，发布运行时与模板语义契约有 359 条分歧；
- P9 工具选择建议 1,000 条；
- P9 E2E 建议 72 条，确定性契约规则草稿均为 `pass`。

这些数字是草稿诊断，不是人工准确率或人工通过率。若真人查看 AI 草稿后再确认，必须如实
记录为 AI 辅助复核，不能描述为盲标或独立人工金标。
