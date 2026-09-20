# Intent MacBERT v1 Model Card

## Model details

- Task: 16-class Chinese e-commerce primary-intent classification.
- Base: `hfl/chinese-macbert-base`.
- Base revision: `a986e004d2a7f2a1c2f5a3edef4e20604a974ed1`.
- Base license: Apache-2.0.
- Data version: `intent_v1`.
- Deployment format: FP32 ONNX, opset 17.
- Maximum sequence length: 128.
- Selected run: seed 13, learning rate `3e-5`, epoch 5.

The classification head was newly initialized and fine-tuned; the base checkpoint is not itself an
intent classifier.

## Intended use

The model provides a first-stage routing signal for the CommerceAgent demo: shopping, knowledge,
order, after-sales, human, general, and safe-reply routes. It must be used with the published
temperature, confidence/margin thresholds, OOS detector, priority rules, and clarification flow.

It is not intended to authorize refunds, compensation, order cancellation, or other state-changing
operations. Those actions require deterministic business validation and human confirmation.

## Labels

`product_search`, `product_recommend`, `product_compare`, `product_detail`, `stock_price`,
`order_status`, `logistics_tracking`, `cancel_order`, `return_exchange`, `refund_progress`,
`after_sales_eligibility`, `policy_faq`, `complaint`, `human_handoff`, `chitchat`, `out_of_scope`.

## Training data

The version contains exactly 25,000 Chinese rows: 20,000 train, 2,500 validation, and 2,500 internal
test. It combines 10,000 controlled synthetic rows and 15,000 selected public-source rows with
record-level provenance. Exact, MinHash and BGE semantic deduplication were run before grouped
splitting. This is a constructed development corpus and must not be described as 25,000 genuine
production conversations.

## Internal evaluation

Across seeds 13, 42 and 2026, internal-test Accuracy is 96.00% ± 0.08%, Macro-F1 is 95.84% ± 0.03%,
and Top-2 Accuracy is 98.99% ± 0.05%. TF-IDF + LinearSVC reaches 94.44% Accuracy and 94.27%
Macro-F1 on the same split.

These are not frozen human-gold results. Final resume or acceptance claims must wait for the 1,600-row
independently reviewed gold set.

## Calibration and OOS

Temperature scaling reduced validation ECE from 2.51% to 1.09%. The OOS detector combines the OOS
class probability, maximum calibrated probability, and energy score. Its internal-test AUROC is
99.02% and F1 is 89.89%. Low-confidence predictions do not force a business route.

## ONNX verification

PyTorch and ONNX Top-1 predictions agree on 100% of 2,500 internal-test rows. On the development
machine, tokenization plus ONNX inference has CPU P95 24.14 ms after 200 warmup iterations across
2,000 measured batch-1 iterations.

## Limitations

- The 1,600-row human gold set and 400-row challenge set are pending annotation/adjudication.
- Public and synthetic data may not match production query distributions.
- Short, ambiguous, novel-domain, adversarial, typo-heavy, and context-dependent inputs can fail.
- A novel-domain question may be predicted as `chitchat`; confidence gating and clarification are
  required even when the OOS ensemble is below threshold.
- Thresholds were selected on internal validation and must be revalidated on frozen human gold.
- Latency depends on CPU, thread settings, input length, container contention, and concurrency.

## Runtime artifacts

The runtime package under `models/intent_classifier/current` contains the tokenizer, ONNX graph,
label order, model manifest, temperature, OOS detector, and route thresholds. The service rejects an
incomplete package and exposes readiness only after loading and warmup.
