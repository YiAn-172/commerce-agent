# P10 Docker Release Acceptance Report

Date: 2026-09-20  
Status: AI-assisted demo release verified with explicit waivers; strict production release blocked

## 1. Scope and release profiles

P10 was executed against the `demo-full` Compose stack without deleting named volumes. The project
now publishes two intentionally separate evidence profiles:

- `reports/release/latest_demo_verified.json`: `demo_verified_with_waivers`, suitable for the
  current project demonstration only;
- `reports/release/latest_candidate.json`: strict production candidate, still `blocked` and unable
  to create `latest_verified.json`.

The demo report preserves `human_review_status=not_performed` and
`provenance=ai_assisted_project_owner_accepted`. It does not relabel AI-assisted annotations as
human gold. The initial Git baseline now exists. A clean-clone source verification is recorded
separately, while a fully isolated demo-runtime reconstruction still waits for externally
distributed model/data artifacts. A presentation video has not been recorded.

## 2. Docker stack and readiness

Twelve long-running services were started and healthy: MySQL, Redis, Elasticsearch, etcd, MinIO,
Milvus, Intent Service, three MCP services, API, and Web. `scripts/wait_for_ready.py` measured the
following endpoints from the Compose network:

- API `/health/ready`;
- Intent Service `/health/ready`;
- catalog, order, and after-sales MCP `/health/ready`;
- Web `/api/health`;
- Elasticsearch cluster health;
- Milvus `/healthz`.

The API readiness endpoint itself now verifies MySQL, the active knowledge version, Redis, Intent
Service, all three MCP services, Elasticsearch, and Milvus. The verified active knowledge version is
`kb_20260917_001`. All eight externally polled endpoints passed in one attempt. Evidence:
`reports/release/readiness.json`.

`scripts/export_release_environment.py` generated:

- a resolved but secret-redacted Compose file at
  `reports/release/compose.resolved.yaml`;
- Compose hashes, Docker server metadata, 13 image records/digests, and 12-container resource
  snapshots at `reports/release/docker_environment.json`.

The exporter computes the hash of the actual resolved Compose output in memory but only writes a
redacted configuration. Passwords, DSNs, API keys, tokens, and secrets are never printed.

## 3. P9 gates closed during P10

### 3.1 Real MySQL security gate

`evals.security.run` now accepts a MySQL DSN and reuses the production SQLAlchemy repositories and
after-sales service. Each of eight cases issues 20 concurrent identical requests and verifies the
persisted ticket and approval counts.

Measured result:

- 64/64 attacks passed;
- 8/8 MySQL concurrency cases passed;
- cross-user order-field leaks: 0;
- duplicate business writes: 0;
- real-money action paths: 0;
- malicious knowledge-triggered tool calls: 0.

Without MySQL the concurrency cases remain blocked; SQLite is not accepted as substitute evidence.
Evidence: `reports/eval/security_attacks_v1.json`.

### 3.2 Live load

The mixed load runner now generates a unique request-ID namespace for every run and serializes
requests only within each optimistic-concurrency session. Twenty independent sessions remain active
in parallel.

Latest measured mixed result:

- 3,000/3,000 completed successfully;
- concurrency: 20;
- error rate: 0%;
- throughput: 35.219 requests/second;
- P50/P95/P99: 32.759 / 2771.803 / 4891.323 ms.

The API now provides a session-scoped Redis cache for the fixed FAQ workload with trusted
`X-Cache: MISS/HIT` evidence. Latest measured cache result:

- 5,000/5,000 completed successfully;
- one cold miss and 4,999 hot hits;
- hot hit rate: 100%;
- hot P50/P95/P99: 10.295 / 12.277 / 16.035 ms;
- error rate: 0%.

Evidence: `reports/eval/load_mixed.json` and `reports/eval/load_faq_cache.json`.

## 4. Smoke and external-provider degradation

`scripts/smoke_demo.py` executes the ten final demo themes over authenticated live HTTP sessions and
checks response contracts, hidden-field leakage, absence of real-money tools, and prompt-injection
tool suppression. All ten contract/safety checks passed.

After DeepSeek access recovered on September 20, the strict-schema adapter and API image were rebuilt
and the ten scenarios were rerun: 10/10 passed with no `dependency_failed` result. The knowledge
scenario completed with live generation, while contract, hidden-field, prompt-injection, and
real-money-tool checks remained clean. DeepSeek Judge also completed 360/360 expression reviews.

`LiveChatService` retains the previously verified fail-safe behavior for future provider failures.
Evidence:
`reports/release/smoke_demo.json` and `reports/agent/p6_deepseek_live.json`.

### 4.1 Browser visual QA

The production Web image was inspected interactively in compact and wide layouts. The customer
entry flow now deliberately opens a clean new-consultation state instead of selecting the newest
load-test session. Technical sessions created by load, smoke, security, recovery, SSE, and
tool-selection runners are excluded from the customer demo sidebar without deleting their backend
evidence. The misleading policy starter was replaced with an intent-runtime-verified product-detail
starter. The administrator view rendered the approval queue, active knowledge version, and latest
evaluation metrics correctly.

This is AI-assisted demo visual QA, not human acceptance. No presentation video or persisted
screenshot artifact was produced. Evidence: `reports/release/browser_visual_qa.json`.

## 5. Restart and recovery

The following sequence was executed:

1. create and approve a mock after-sales approval without graph resume;
2. restart the API container while preserving named volumes;
3. wait for API health;
4. run the approval recovery worker;
5. call resume again to verify idempotency and read the session after restart.

Result:

- recovery worker found and resumed the pending approval;
- outcome was `mock_action_authorized`;
- duplicate resume returned the prior result;
- session remained readable after restart;
- no real-money action occurred.

Evidence: `reports/api/p7_recovery_worker.json` and `reports/api/p7_smoke.json`.

## 6. Backups and restore drill

`scripts/export_checkpoints.py` exported the latest snapshot for each LangGraph thread/namespace from
both checkpoint SQLite databases:

- 794 rows exported;
- SHA-256 recorded;
- output stored under ignored `backups/checkpoints.jsonl`;
- evidence stored in `reports/release/checkpoint_backup.json`.

`scripts/verify_mysql_backup_restore.py` performed a real restore drill:

1. generated a consistent `mysqldump`;
2. created an isolated randomly named temporary database;
3. imported the dump;
4. compared key source/restored row counts;
5. dropped the temporary database in `finally`.

Measured source and restored counts matched: 100 users, 3,000 orders, 110 service tickets,
110 approval tasks, and 3 knowledge versions. The dump was 11,712,187 bytes and its SHA-256 is in
`reports/release/mysql_backup_restore.json`. Backup contents remain Git-ignored.

## 7. Quality verification

Final commands and results:

```text
ruff check packages apps services evals scripts tests: passed
mypy packages apps services evals scripts tests: passed on 215 source files
pytest in Compose evaluator: 148 passed, 1 dependency warning
```

The full test suite ran inside the Compose evaluator so MySQL integration tests used the real
container hostname and backend.

## 8. Clean-clone source verification

After creating the initial Git baseline at commit
`9dbf9ac08a188e67f87fc3936717e48e153de7fd`,
`scripts/verify_fresh_clone.py` cloned only committed files into an ignored temporary directory and
measured the clone independently:

- clone working tree: clean;
- `docker compose config --quiet`: passed;
- Ruff: passed;
- mypy: passed on 210 source files;
- pytest from the clone-mounted Compose evaluator: 145 passed.

The test container reused the already-running Compose dependency network and the local `.env`
configuration; secrets were not copied into evidence. This is therefore source reproducibility
evidence, not a fully isolated deployment reproduction.

The ignored ONNX runtime and generated knowledge documents were packaged into
`dist/commerce-agent-demo-runtime-v1.zip` using a strict ten-file whitelist. The 414,276,839-byte
bundle contains no secrets and has SHA-256
`f8386e93c390b444dc72e73de86eb67c21e227be9ec62b0678be3d6f26ea4c73`.
Its importer rejects undeclared members and path traversal, verifies every file hash, and fails
closed on mismatched existing files. The bundle is local and Git-ignored until it is uploaded to a
controlled release location. Evidence: `reports/release/runtime_bundle_export.json` and
`reports/release/fresh_clone_reproduction.json`. The latter is
`source_and_runtime_artifacts_verified`; a separate isolated stack was not started.

## 9. Remaining strict-release blockers

The strict `latest_candidate.json` correctly remains blocked by:

- no independent human intent gold;
- routing/tool-selection human review not completed;
- E2E human review not completed;
- fully isolated fresh-clone runtime reconstruction and presentation recording are not completed.

These blockers are intentionally not weakened. The current project can be demonstrated using the
AI-assisted profile, but must not be represented as a human-gold or strict production release.
