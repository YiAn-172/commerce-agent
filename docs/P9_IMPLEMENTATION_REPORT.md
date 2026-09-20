# P9 Evaluation and Resume-Evidence Implementation Report

Date: 2026-09-19  
Status: AI-assisted demo evidence verified; strict human-gold production release remains blocked

## 1. Delivered in this iteration

- Extended frozen intent-gold evaluation with 15-bin ECE, multiclass Brier score, and OOS F1.
- Added a public calibrated-probability batch API to the production intent runtime so evaluation
  uses exactly the serving tokenizer, ONNX graph, temperature, and class order.
- Added a deterministic 800-case routing challenge with eight balanced categories and duplicate
  detection.
- Added routing accuracy, per-category accuracy, direct-dispatch/clarification/human-handoff
  accuracy, route confusion matrix, failure samples, and Wilson 95% confidence intervals.
- Added a fail-closed release evidence builder. It writes a candidate report but refuses to write
  `latest_verified.json` unless all required evidence is present and verified, Git has a HEAD, and
  the working tree is clean.
- Added unit tests for suite balance/determinism, confidence intervals, release gating, and the new
  intent calibration metrics.
- Added `tools_1000_v1`, executing production catalog/order/after-sales service classes against an
  isolated, deterministically seeded SQLite database.
- Added a separate 1,000-case tool-selection challenge through the published ONNX runtime and
  production LangGraph routing path.
- Added DeepSeek expression Judge and a content-hash-aware 20% human-review workflow.
- Added independent, source-row-hash-aware routing and tool-selection review queues, strict
  timestamp/adjudication validation, and hash-verified human-gold runner modes.
- Added CSV review workspaces with 40–50-case batch IDs for P1 gold/challenge, P9 routing,
  tool selection, and E2E, plus safe synchronization back into the authoritative queues.
- Added separately stored AI-assisted drafts that never populate human reviewer identity/status and
  are excluded from release promotion evidence.
- Hardened every generated MCP input model to `extra=forbid`; spoofed identity fields are now
  rejected instead of silently discarded, and advertised input schemas carry
  `additionalProperties=false`.

## 2. Measured routing result

Command:

```powershell
uv run --cache-dir .uv-cache --frozen python -m evals.routing.run --suite routing_800_v1
```

Measured on the currently published `intent-macbert-v1` ONNX runtime:

| Metric | Correct / total | Accuracy | Wilson 95% CI |
|---|---:|---:|---:|
| Overall routing contract | 526 / 800 | 65.75% | 62.39%-68.96% |
| Route only (cases with an expected route) | 533 / 700 | 76.14% | 72.85%-79.15% |
| Direct dispatch | 40 / 100 | 40.00% | 30.94%-49.80% |
| Clarification | 85 / 100 | 85.00% | 76.72%-90.69% |
| Human handoff | 100 / 100 | 100.00% | 96.30%-100.00% |

Per-category accuracy:

- product consult: 49%;
- product recommendation: 81%;
- order/logistics: 40%;
- after-sales: 59%;
- ambiguous input: 72%;
- direct dispatch: 40%;
- clarification: 85%;
- human handoff: 100%.

The suite is marked `template_challenge_unreviewed`. It is diagnostic evidence, not a human gold
set, and must not be presented as production accuracy. The measured value is intentionally kept
instead of substituting the resume target.

The routing review queue is now materialized at
`reports/eval/routing_800_v1_review_queue.jsonl`. Its summary is
`pending_human_review`, with 0/800 reviewed, and no
`routing_800_v1_human_gold.jsonl` exists. The workflow is:

```powershell
uv run --cache-dir .uv-cache --frozen python -m evals.routing.review build
uv run --cache-dir .uv-cache --frozen python -m evals.routing.review finalize
# After finalize succeeds:
uv run --cache-dir .uv-cache --frozen python -m evals.routing.run `
  --suite routing_800_v1_human_gold
```

While reviews are incomplete, `finalize` exits 2 and deletes stale adjudicated output.


## 3. Tool contract result

The tools suite contains exactly 1,000 cases:

| Category | Cases | Passed |
|---|---:|---:|
| Legal requests and parameter contracts | 400 | 400 |
| Undeclared/schema argument rejection | 150 | 150 |
| Missing-scope rejection | 150 | 150 |
| Cross-user ownership isolation | 100 | 100 |
| Timeout and dependency-error envelopes | 100 | 100 |
| Idempotent write replay | 100 | 100 |
| **Total** | **1,000** | **1,000** |

The run produced zero duplicate business writes and zero unauthorized order-field leaks. This is a
deterministic executable-contract result, not a tool-selection score. The suite starts after a tool
has been selected. Its 100% result must not be used as the resume's 95.2% tool-selection claim;
tool selection is measured separately below. MySQL/MCP-over-HTTP portability remains a Compose
integration check.

The schema cases exposed a real boundary issue: FastMCP's generated argument models defaulted to
ignoring extra fields. The server now closes both runtime argument models and published schemas, so
fields such as `principal_id`, `scopes`, and `deadline_ms` cannot be smuggled into tool arguments.


### 3.1 Tool-selection diagnostic

`tool_selection_1000_v1` runs the currently published `intent-macbert-v1` ONNX runtime through the
production confidence gate, LangGraph route selection, and subgraph tool-call path. Tool backends
are scripted so the measured boundary remains selection rather than execution.

| Metric | Correct / total | Accuracy |
|---|---:|---:|
| Overall tool selection | 808 / 1,000 | 80.8% |
| Tool-required cases | 308 / 500 | 61.6% |
| Correct no-tool behavior | 500 / 500 | 100.0% |

Per-category results are shopping 82%, order detail 100%, logistics 0%, recent order 51%,
after-sales 75%, and 100% for knowledge, human handoff, general, safe reply, and missing-information
no-tool cases. All 192 failures were fail-closed abstentions: 140 ended in `needs_clarification` and
52 in `safe_reply`. There were zero wrong-tool sequences, zero unexpected tool calls, and zero
execution errors. The dominant gap is therefore confidence gating rather than unsafe tool choice.

The suite is `template_challenge_unreviewed`. Therefore 80.8% is a diagnostic measurement, not a
human-gold or production claim, and it does not reproduce the resume target of 95.2%.

The tool-selection review queue is now materialized at
`reports/eval/tool_selection_1000_v1_review_queue.jsonl`. Its summary remains
`pending_human_review`, with 0/1,000 reviewed, and no adjudicated suite exists. The workflow is:

```powershell
uv run --cache-dir .uv-cache --frozen python -m evals.tool_selection.review build
uv run --cache-dir .uv-cache --frozen python -m evals.tool_selection.review finalize
# After finalize succeeds:
uv run --cache-dir .uv-cache --frozen python -m evals.tool_selection.run `
  --suite tool_selection_1000_v1_human_gold
```

Both review workflows preserve completed reviews only while the source-row SHA256 is unchanged.
Reviewed rows require a reviewer ID, a timezone-aware ISO 8601 timestamp, and an `accept` or
`correct` verdict; corrections require valid adjudicated labels. The runners independently verify
the current source-suite hash and adjudicated-suite hash against the completed review summary. A
CLI flag alone therefore cannot promote either report to `human_gold_verified`.

### 3.2 AI-assisted review drafts

`generate_ai_review_drafts.py` generated separate, non-authoritative drafts for 800 routing cases,
1,000 tool-selection cases, and 72 E2E cases. The published runtime disagreed with the routing
contract draft on 359/800 cases; this is a model diagnostic, not evidence that the labels are wrong.
The deterministic E2E draft marked 72/72 as contract-consistent after accepting
`insufficient_evidence` as the fail-closed search fallback. All draft rows are labeled
`ai_assisted_draft_not_human_review`; release gating continues to use only the untouched human
review queues, currently at 0/800, 0/1,000, and 0/72.

## 4. Deterministic multi-turn e2e result

`e2e_360_v1` contains 360 conversations and 720 turns, executed through the production
`build_graph()` state machine with an in-memory LangGraph checkpointer and dedicated deterministic
gateways. The suite distribution is 48 knowledge, 48 shopping, 48 order, 48 after-sales, 36
multi-intent, 36 human/general/safety, 36 missing-information, 36 tool-failure, and 24 validation
guard cases.

Measured graph-contract results:

- 360/360 conversations passed route, tool-sequence, terminal-state, state-version, and approval
  assertions;
- 96/96 abnormal cases used the expected fail-closed fallback;
- no write tool ran before after-sales confirmation;
- safety turns invoked neither intent classification nor tools;
- the second turn received the previous human utterance through checkpointed message history.

The runtime previously collapsed tool failures, missing-information responses, validation failures,
and handoffs into `completed`. The final answer validator now preserves those terminal statuses.
The graph also now derives `previous_user_text` from the prior checkpointed human message when the
caller does not explicitly provide it.

This is labeled `production_graph_with_scripted_gateways`. It is not a live-LLM production task
completion result. On September 20, 2026, DeepSeek Judge completed 360/360 expression reviews:
344 were relevant (95.56%), with mean relevance score 3.728/5. Judge status is now `verified`.
The deterministically sampled 72-case (20%) manual-review queue carries the actual turns and review
fields but remains
`pending_human_review`, with 0/72 reviewed. Reviews survive regeneration only while their content
hash remains unchanged. The fail-closed release builder therefore continues to block promotion.

## 5. Live load and MySQL security evidence

The fixed live HTTP workloads were executed against the healthy Docker demo stack on September 19,
2026. Both reports are `evaluation_status=verified`:

- mixed workload: 3,000/3,000 HTTP requests succeeded at concurrency 20, zero errors,
  35.219 requests/second, P50 32.759 ms, P95 2771.803 ms, P99 4891.323 ms;
- FAQ cache: one measured cold miss plus 4,999/4,999 hot hits, zero errors, 100% hot hit rate,
  hot P50 10.295 ms, P95 12.277 ms, P99 16.035 ms.

The mixed runner now uses a unique request-ID namespace per run and serializes requests within each
optimistically versioned chat session, while retaining 20 sessions in parallel. This prevents the
benchmark itself from generating false request-ID collisions or same-session state-version races.
FAQ caching is session-scoped, reports trusted `X-Cache: MISS/HIT` headers, and remains an
optimization rather than a source of business truth.

The 64-case attack suite now runs all eight 20-way idempotency races against the real Compose MySQL
backend. Result: 64/64 passed, zero blocked, and all four exit gates measured at zero:

- cross-user order-field leaks: 0;
- duplicate business writes: 0;
- real-money action paths: 0;
- malicious knowledge-triggered tool calls: 0.

SQLite remains explicitly invalid for this concurrency gate; omitting `MYSQL_DSN` keeps those cases
blocked instead of fabricating release evidence.

## 6. AI-assisted acceptance boundary

The project owner accepted AI-assisted labels for the current demo scope. Separate artifacts were
finalized with the exact status `ai_assisted_verified_for_demo`, provenance
`ai_assisted_project_owner_accepted`, and `human_review_status=not_performed`:

- 1,600-row intent evaluation set and 400-row challenge set;
- 800 routing cases;
- 1,000 tool-selection cases;
- 72-case E2E review sample.

The AI-assisted intent evaluation measured TF-IDF accuracy 90.25% / Macro-F1 90.3145%, and the
published MacBERT runtime accuracy 95.375% / Macro-F1 95.3509%. Routing remains 526/800 (65.75%) and
tool selection remains 808/1,000 (80.8%). These are demo/internal measurements, not independent
human-gold or production claims.

The original human queues are intentionally untouched at zero completed reviews. The strict
production release builder still requires human-gold evidence and remains fail-closed.

## 7. Release-evidence profiles

Two profiles are now intentionally separate:

1. `reports/release/latest_demo_verified.json` is
   `demo_verified_with_waivers`. It accepts only the explicitly labeled AI-assisted evidence and
   still requires verified live load, real-MySQL security, Docker readiness, smoke, restart recovery,
   checkpoint backup, and MySQL restore evidence.
2. `reports/release/latest_candidate.json` remains `blocked`. The DeepSeek live/Judge gates are now
   complete; it still requires independent human review, a Git HEAD, and a clean working tree. No
   `latest_verified.json` has been generated or relaxed.

The live DeepSeek structured-output probe now passes. The API still converts future provider failures
into an auditable `dependency_failed` safe response with no tool execution instead of an unhandled
HTTP 500.

## 8. Verification performed

```text
Ruff: passed across packages/apps/evals/scripts/tests
Mypy: passed across 203 source/test files
Pytest in Compose evaluator: 145 passed
Docker readiness: API, Intent, 3 MCP services, Web, Elasticsearch, Milvus passed
Mixed load: 3,000/3,000 passed
FAQ cache load: 5,000/5,000 passed; 4,999 hot hits
Security attacks: 64/64 passed on MySQL
Ten-scenario HTTP smoke: 10/10 contract/safety checks passed
MySQL backup restore: source/restored row counts matched in a deleted temporary database
Restart recovery: approval resumed after API restart; duplicate resume was idempotent
```

P10 Docker/recovery details are recorded in `docs/P10_IMPLEMENTATION_REPORT.md`.
