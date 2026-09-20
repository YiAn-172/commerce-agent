# CommerceAgent

Docker-first 电商智能客服与商品导购 Agent。P0-P10 的工程实现和 AI 辅助演示验收均已
完成：包含意图数据与模型、业务数据库、三个 MCP Server、版本化 RAG 双索引、
LangGraph 主图与四个业务子图、Human-in-the-loop、FastAPI/Redis SSE、容器化演示前端、
自动评测，以及 Docker 发布/恢复/备份验收。严格生产发布仍与演示配置隔离并保持
fail-closed。

## 固定项目位置

```text
C:\Users\mtcbf\Desktop\commerce-agent
```

除 Docker Desktop、Git 和编辑器外，宿主机不需要安装 Python、Node 或数据库。

## 当前可验证结果

- 25,000 条中文一级意图语料：训练 20,000、验证 2,500、内部测试 2,500。
- 16 个互斥标签全部达到版本化目标；受控合成 10,000，公开来源 15,000。
- 数据来源 revision、目录 SHA256、许可证状态和逐条 provenance 已记录。
- 精确哈希、MinHash 0.90、BGE Small 中文 0.997 三层去重已执行。
- 样本 ID、文本哈希、对话、模板族和语义簇跨 split 泄漏均为 0。
- 25,000 条残留 PII 扫描通过；1,164 条输入在清洗阶段被替换为占位符。
- 1,600 条金标待标队列和 400 条挑战集待审队列已生成。
- TF-IDF 内部测试 Macro-F1 94.27%；MacBERT 三种子内部测试 Macro-F1
  95.84% ± 0.03%。
- 温度缩放后验证 ECE 1.09%；内部测试 OOS AUROC 99.02%、F1 89.89%。
- PyTorch/ONNX 在 2,500 条上的 Top-1 一致率 100%；CPU 端到端 P95 24.14 ms。
- Docker `intent-service` 已提供单条/批量推理、优先规则、多意图、OOS 与置信度门控。
- MySQL 8.4 已完成 19 张业务表的 Alembic 迁移，当前 revision 为
  `d2a6f4108c3b`。
- 固定种子 `20260915` 已生成 100 用户、1,200 SKU、3,000 订单、4,500 订单明细、
  6,533 物流事件、80 售后规则和 100 工单。
- 订单状态机、售后资格、退款快照、订单归属与数据库幂等约束均有自动化测试；P3
  13 个测试全部通过。
- `mcp-catalog`、`mcp-order`、`mcp-after-sales` 已通过 Streamable HTTP 真实协议
  冒烟，三个容器健康。
- 八个 MCP 工具的输入/输出均有精确 JSON Schema；模型参数不包含身份、权限、受众或
  deadline。
- P4 契约、安全和 20 并发幂等测试共 17 个全部通过；20 次相同售后预填只写入一条
  工单和一条 mock 审批。
- P5 已构建 1,580 份知识文档/2,780 个 chunk；ES 与 Milvus 当前 alias 均指向
  `kb_20260917_001`，100 条抽样 hash/metadata、512 维 schema 和 20 条在线冒烟通过。
- 600 题四路检索评测已产出 2,400 条明细；Hybrid + Reranker 的 Recall@5、MRR、
  引用命中/验证和 50 条独立无答案拒答均为 100%，CPU P50/P95 为 843/1,117 ms。
- LangGraph 主图和 Knowledge、Shopping、Order、After-sales 四个子图已编译运行；
  安全拦截、低置信度/OOS、多意图、意图切换和图预算均有自动化测试。
- P6 五类基础设施集成场景全部通过：真实意图 HTTP、MCP、RAG、MySQL 和 SQLite
  checkpoint，LLM 使用可复现的录制式严格响应。
- DeepSeek `deepseek-flash` 真实结构化请求已通过；适配器把目标 Pydantic JSON Schema
  写入首轮与修复提示，严格拒绝额外字段。最新实测延迟 1,207.1 ms。
- P7 已实现审批五类终态、`interrupt()`、乐观锁、资格复检、过期处理和崩溃补偿；
  真实 API 容器重启后从持久 checkpoint 恢复，重复 resume 不会重复执行。
- JWT Scope、会话/聊天/审批/知识/评测 API 和 Redis SSE 已接入；SSE 支持 15 秒心跳、
  10 分钟留存、`Last-Event-ID` 续传和隐藏字段过滤。
- P8 已提供用户/客服/管理员视角、会话列表、SSE 聊天状态、商品卡片、RAG 引用、
  结构化工具轨迹、人工审批队列、知识版本和最近评测；JWT 仅保存在 Next.js HttpOnly
  cookie，浏览器不直接访问内部 API，也不展示模型隐藏推理。
- Next.js 16.3.5/React 19.3.0 Web 使用 Node 24.21.0 LTS 多阶段镜像，生产 runner
  为非 root、standalone 产物；`dev-lite` 全栈 6 个长期服务均已健康。
- P1/P9 的项目所有者接受版 AI 辅助证据已生成并标记为
  `ai_assisted_verified_for_demo`；所有文件仍明确保留
  `human_review_status=not_performed`，不会冒充人工金标。
- P10 的 12 服务 Compose 栈、依赖感知就绪检查、10 场景安全冒烟、MySQL 备份恢复、
  checkpoint 导出、API 重启恢复、64 条安全攻击和两组在线负载均已形成可审计证据。
- 浏览器演示已完成 AI 辅助视觉复验：客户登录默认进入干净的新咨询，压测/安全测试
  技术会话不会污染客户侧栏，运营视角可见激活知识版本、评测指标和 mock 审批队列。
- 最新全仓回归为 148 个测试全部通过；Ruff 和 215 个源码/测试文件的 mypy 通过。
  Compose 配置、Alembic 漂移、演示数据一致性和知识版本激活状态均通过。

人工队列尚未完成双人标注与仲裁，因此当前不能称为“1,600 条冻结人工金标”。当前可用
的是项目所有者接受的 AI 辅助演示配置；以上模型数字也不能称为人工金标生产指标。详细
边界见 `docs/P1_IMPLEMENTATION_REPORT.md`、
`docs/P2_IMPLEMENTATION_REPORT.md`、`docs/P3_IMPLEMENTATION_REPORT.md`、
`docs/P4_IMPLEMENTATION_REPORT.md`、`docs/P5_IMPLEMENTATION_REPORT.md` 和
`docs/P6_IMPLEMENTATION_REPORT.md`、`docs/P7_IMPLEMENTATION_REPORT.md`、
`docs/P8_IMPLEMENTATION_REPORT.md`；标注规则见
`docs/annotation_guide.md`。

## P8 演示前端

启动日常演示栈：

```powershell
docker compose --profile dev-lite up -d --build
docker compose --profile dev-lite ps
```

浏览器访问 `http://127.0.0.1:3000`。首屏可选择两个演示用户、人工客服或管理员：

- 用户视角：只读取自己的会话，支持新建会话、SSE 状态、商品卡片、引用和工具摘要；
- 客服视角：读取待人工审批队列，支持通过、拒绝和退回补充，决策后恢复 LangGraph；
- 管理员视角：复用审批台，并显示知识版本、索引任务和最近评测；
- 前端只允许代理白名单 API，JWT 使用 HttpOnly/SameSite cookie；外部输入不会渲染为
  HTML，工具轨迹只返回白名单字段。

前端生产构建、BFF 冒烟、浏览器可视化检查和已知边界见
`docs/P8_IMPLEMENTATION_REPORT.md`。DeepSeek 真实结构化输出门禁现已通过；外部模型、网络
或余额异常时，用户聊天仍会显示可审计的安全降级状态且不会执行危险工具。

## P7 审批恢复与 SSE 验收命令

启动最终 API、Redis、MySQL 和售后 MCP：

```powershell
docker compose --profile demo-full up -d --build --wait `
  api mcp-after-sales
```

真实崩溃恢复分三段执行。第一段提交审批但故意不恢复，随后重启 API，再运行补偿任务：

```powershell
docker compose --profile tooling run --rm worker `
  uv run python scripts/smoke_p7_api.py decide
docker compose restart api
docker compose --profile demo-full up -d --wait api
docker compose --profile tooling run --rm worker `
  uv run python scripts/recover_approvals.py
docker compose --profile tooling run --rm worker `
  uv run python scripts/smoke_p7_api.py resume
```

真实 Redis SSE 留存、续传、TTL 与敏感字段过滤：

```powershell
docker compose --profile tooling run --rm worker `
  uv run python scripts/smoke_p7_redis_events.py
```

证据位于 `reports/api/p7_smoke.json`、`reports/api/p7_recovery_worker.json` 和
`reports/api/p7_sse_redis.json`。详细状态机、API 权限和验收边界见
`docs/P7_IMPLEMENTATION_REPORT.md`。

## P6 Agent 图与 DeepSeek 验收命令

确定性集成冒烟会调用真实意图服务、MCP、RAG、MySQL 和 checkpoint，仅将 LLM 响应
固定为严格录制值：

```powershell
docker compose --profile demo-full up -d --wait `
  mysql elasticsearch etcd minio milvus intent-service `
  mcp-catalog mcp-order mcp-after-sales
docker compose --profile tooling run --rm worker `
  uv run python scripts/smoke_agent_graph.py
```

真实 DeepSeek 冒烟单独运行，避免把余额、网络等外部失败与本地图回归混为一谈：

```powershell
docker compose --profile tooling run --rm worker `
  uv run python scripts/smoke_deepseek_live.py
```

当前真实调用证据为 `reports/agent/p6_deepseek_live.json`，状态是 `passed`。输出通过
`RewriteOutput` 严格解析；完整实现和验收边界见 `docs/P6_IMPLEMENTATION_REPORT.md`。

## P5 RAG 构建、验证与评测命令

P5 使用固定 revision 的 `BAAI/bge-small-zh-v1.5`（512 维）和
`BAAI/bge-reranker-base`。动态价格、库存、订单、物流和退款金额不进入知识索引，必须
由 MCP 工具实时返回。

```powershell
docker compose --profile demo-full up -d elasticsearch etcd minio milvus
docker compose --profile tooling build worker
docker compose --profile tooling run --rm worker uv run python scripts/prefetch_rag_models.py
docker compose --profile tooling run --rm worker uv run python -m packages.rag_core.ingest.build
docker compose --profile tooling run --rm worker uv run python -m packages.rag_core.ingest.verify
docker compose --profile tooling run --rm worker uv run python -m packages.rag_core.ingest.activate
docker compose --profile tooling run --rm worker uv run python -m evals.rag.build_suite
docker compose --profile tooling run --rm worker uv run python -m evals.rag.run
```

版本回滚必须指定一个已验证或曾激活的版本：

```powershell
docker compose --profile tooling run --rm worker uv run python `
  -m packages.rag_core.ingest.rollback --version kb_20260915_001
```

构建只写物理索引/collection；`verify` 在激活前比较条数、抽样 hash、metadata、维度和
20 条 smoke query。只有验证通过才能切换 ES/Milvus 两侧 alias；任一侧失败会补偿恢复
原 alias，旧知识版本继续服务。

评测支持逐条 checkpoint/resume。已路由到具体知识类型的域内查询使用类型与有效期过滤；
只有未路由的开放检索使用 reranker `0.8` 阈值拒答，避免一个全局阈值误拒口语化域内
问题。完整实测、版本回滚证据和已知边界见 `docs/P5_IMPLEMENTATION_REPORT.md`。

## P4 MCP 服务命令

```powershell
docker compose --profile demo-full up -d --build `
  mcp-catalog mcp-order mcp-after-sales
docker compose --profile demo-full ps `
  mcp-catalog mcp-order mcp-after-sales
docker compose --profile tooling run --rm worker uv run python scripts/smoke_mcp.py
docker compose --profile tooling run --rm worker uv run pytest tests/mcp -q
```

MCP 主端点分别为容器网络内的 `mcp-catalog:8101/mcp`、`mcp-order:8102/mcp` 和
`mcp-after-sales:8103/mcp`。主 Compose 不暴露端口到宿主机；调试端口只在
`compose.dev.yaml` 中开放。首次配置时必须将 `.env.example` 中的
`MCP_INTERNAL_TOKEN` 替换为独立随机值。

## P3 数据库与演示数据命令

```powershell
docker compose --profile core up -d mysql redis
docker compose --profile tooling run --rm worker uv run alembic upgrade head
docker compose --profile tooling run --rm worker uv run python scripts/seed_demo_data.py
docker compose --profile tooling run --rm worker uv run python scripts/validate_demo_data.py
```

种子脚本可重复执行：当固定版本已经完整存在时返回 `already_seeded`；如果只存在部分
数据则失败关闭，不会静默覆盖。十个固定演示场景及数据集指纹记录在
`data/manifests/demo_business_v1.json`。只有明确需要重建演示数据时才使用
`scripts/seed_demo_data.py --reset`。

## P2 训练与部署命令

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

服务启动后访问 `http://127.0.0.1:8001/docs`，或调用 `/predict`、`/batch_predict`、
`/health/ready`。模型卡位于 `docs/model_cards/intent_macbert_v1.md`。

## P1 复现命令

```powershell
Set-Location -LiteralPath 'C:\Users\mtcbf\Desktop\commerce-agent'
if (-not (Test-Path -LiteralPath .\.env)) { Copy-Item .env.example .env }

docker compose config --quiet
docker compose --profile tooling build worker
docker compose --profile train build intent-trainer

docker compose --profile tooling run --rm worker uv run python scripts/download_intent_sources.py
docker compose --profile tooling run --rm worker uv run python scripts/check_data_licenses.py
docker compose --profile tooling run --rm worker uv run python scripts/build_intent_candidates.py
docker compose --profile tooling run --rm worker uv run python scripts/select_external_candidates.py
docker compose --profile tooling run --rm worker uv run python scripts/translate_intent_data.py
docker compose --profile tooling run --rm worker uv run python scripts/compact_translations.py
docker compose --profile tooling run --rm worker uv run python scripts/generate_intent_data.py
docker compose --profile tooling run --rm worker uv run python scripts/prepare_intent_quality_pool.py

docker compose --profile train run --rm --volume C:/Users/mtcbf/Desktop/commerce-agent:/workspace intent-trainer `
  uv run python scripts/semantic_deduplicate.py --batch-size 128

docker compose --profile tooling run --rm worker uv run python scripts/build_intent_dataset.py
docker compose --profile tooling run --rm worker uv run python scripts/check_split_leakage.py
docker compose --profile tooling run --rm worker uv run python scripts/scan_processed_pii.py
docker compose --profile tooling run --rm worker uv run python scripts/profile_intent_data.py
docker compose --profile tooling run --rm worker uv run python scripts/create_annotation_queues.py
```

翻译和生成脚本是可恢复的追加式任务，均有结构化 JSON 校验、重试、并发和成本上限。
已有输出时只处理缺失 ID。`.env` 中必须填写 `DEEPSEEK_API_KEY`；密钥不会写入报告。

## 质量门禁

```powershell
docker compose --profile tooling run --rm worker uv run ruff check .
docker compose --profile tooling run --rm worker uv run mypy packages apps scripts
docker compose --profile tooling run --rm worker uv run pytest -q
docker compose --profile tooling run --rm worker uv run alembic check
docker compose --profile tooling run --rm worker uv run python scripts/validate_demo_data.py
docker compose --profile tooling run --rm worker uv run python scripts/check_data_licenses.py
docker compose --profile tooling run --rm worker uv run python scripts/check_split_leakage.py
docker compose --profile tooling run --rm worker uv run python scripts/scan_processed_pii.py
```

人工标注完成后，运行下面的失败关闭门禁。Kappa、每类数量、训练集泄漏、重复和 PII
任一不合格都不会生成金标文件：

```powershell
docker compose --profile tooling run --rm worker uv run python scripts/finalize_intent_gold.py
```


## P9 evaluation commands

P1/P9 human-review CSV files, blind-review separation, synchronization, and progress tracking are
documented in `docs/HUMAN_REVIEW_RUNBOOK.md`. Initialize them with:

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py prepare
uv run --cache-dir .uv-cache --frozen python scripts/human_review_workspace.py status
```

The P9 evidence pipeline is fail-closed. The routing suite can be rebuilt and evaluated with:

```powershell
docker compose --profile test run --rm evaluator `
  uv run python -m evals.routing.build_suite
docker compose --profile test run --rm evaluator `
  uv run python -m evals.routing.run --suite routing_800_v1
docker compose --profile test run --rm evaluator `
  uv run python -m evals.routing.review build
docker compose --profile test run --rm evaluator `
  uv run python -m evals.routing.review finalize
# Only after all 800 rows are independently reviewed and finalize succeeds:
docker compose --profile test run --rm evaluator `
  uv run python -m evals.routing.run --suite routing_800_v1_human_gold
docker compose --profile test run --rm evaluator `
  uv run python -m evals.tools.build_suite
docker compose --profile test run --rm evaluator `
  uv run python -m evals.tools.run --suite tools_1000_v1
docker compose --profile test run --rm evaluator `
  uv run python -m evals.tool_selection.build_suite
docker compose --profile test run --rm evaluator `
  uv run python -m evals.tool_selection.run --suite tool_selection_1000_v1
docker compose --profile test run --rm evaluator `
  uv run python -m evals.tool_selection.review build
docker compose --profile test run --rm evaluator `
  uv run python -m evals.tool_selection.review finalize
# Only after all 1,000 rows are independently reviewed and finalize succeeds:
docker compose --profile test run --rm evaluator `
  uv run python -m evals.tool_selection.run --suite tool_selection_1000_v1_human_gold
docker compose --profile test run --rm evaluator `
  uv run python -m evals.e2e.build_suite
docker compose --profile test run --rm evaluator `
  uv run python -m evals.e2e.run --suite e2e_360_v1
docker compose --profile test run --rm evaluator `
  uv run python -m evals.e2e.judge
docker compose --profile test run --rm evaluator `
  uv run python -m evals.e2e.manual_review
docker compose --profile test run --rm load-runner `
  uv run python -m evals.load.mixed --base-url http://api:8000 --concurrency 20 --requests 3000
docker compose --profile test run --rm load-runner `
  uv run python -m evals.load.faq_cache --base-url http://api:8000 --requests 5000
docker compose --profile test run --rm evaluator `
  uv run python -m evals.security.build_suite
docker compose --profile test run --rm evaluator `
  uv run python -m evals.security.run --suite attacks_v1
```

After human adjudication of all 1,600 intent examples, freeze and evaluate the gold set:

```powershell
docker compose --profile tooling run --rm worker `
  uv run python scripts/finalize_intent_gold.py
docker compose --profile test run --rm evaluator `
  uv run python -m apps.intent_service.training.evaluate_gold
```

Build a release candidate evidence file at any time. Promotion writes
`reports/release/latest_verified.json` only when every required report is verified, Git has a
HEAD commit, and the working tree is clean:

```powershell
docker compose --profile test run --rm evaluator `
  uv run python -m evals.release.build_report
docker compose --profile test run --rm evaluator `
  uv run python -m evals.release.build_report --promote
```


The deterministic tools suite executes 1,000 cases against the production tool service classes on
an isolated seeded SQLite database: 400 legal calls, 150 strict-schema rejections, 150 permission
rejections, 100 ownership-isolation checks, 100 timeout/dependency failures, and 100 idempotency
checks. The executable contract result is 1,000/1,000 with zero duplicate writes and zero leaked
order fields. This remains a post-selection parameter/execution contract score.

A separate 1,000-case tool-selection challenge runs the published ONNX intent runtime through the
production LangGraph routing path with scripted dependencies. The measured diagnostic score is
808/1,000 (80.8%): tool-required cases are 308/500 (61.6%), while correct no-tool behavior is
500/500. All 192 failures were fail-closed abstentions—140 clarifications and 52 safe replies—with
zero wrong-tool sequences and zero unexpected tool calls. The suite is
`template_challenge_unreviewed`; it does not reproduce the resume target of 95.2% and must not be
described as human-gold or production accuracy. The independent review queue currently has 0/1,000
reviewed rows. The matching routing queue has 0/800 reviewed rows. `review finalize` exits nonzero and
deletes any stale adjudicated output until every row has a reviewer ID, a timezone-aware ISO 8601
timestamp, and a valid accept/correct verdict. Reviews are preserved only while each source-row hash
is unchanged. The human-gold runners also verify the source hash and adjudicated-suite hash recorded
by the review summary, so a CLI suite name alone cannot promote evidence status.

The deterministic e2e suite executes 360 two-turn conversations (720 turns) through the production
LangGraph state machine with scripted gateways. It includes 36 missing-information, 36 injected
tool-failure, and 24 validation-guard cases. The graph-contract result is 360/360 and abnormal
fallback is 96/96. These are scripted-gateway contract measurements, not live-LLM production
completion metrics. On September 20, 2026, DeepSeek Judge completed all 360 expression reviews:
344/360 were relevant (95.56%) and mean relevance score was 3.728/5. The exported 72-case
(20%) manual-review queue contains the actual user/assistant turns and remains
`pending_human_review` with 0/72 reviewed. Its summarizer requires reviewer identity, timestamp,
1-5 relevance/clarity scores, safety judgment, and pass/fail verdict for every row. Re-running the
suite preserves a review only when the reviewed turn-content hash is unchanged.
On September 19, 2026 the live Docker stack completed both fixed load suites. Mixed traffic passed
3,000/3,000 requests at concurrency 20 with zero errors (35.219 requests/s; P95 2771.803 ms).
The FAQ cache workload passed 5,000/5,000 requests with one cold miss, 4,999 hot hits, and hot P95
12.277 ms. Reports remain explicit that component-level DeepSeek versus local latency is not exposed.

The 64-case security suite passed 64/64 against the real Compose MySQL backend. All eight 20-way
idempotency races produced exactly one ticket and one mock approval; cross-user leaks, duplicate
business writes, real-money paths, and malicious-knowledge tool calls were all zero.

For the current demo only, the project owner accepted separately stored AI-assisted labels. These
artifacts are marked `ai_assisted_verified_for_demo` and retain
`human_review_status=not_performed`. They do not populate or overwrite human review queues. The
strict production report remains blocked, while `reports/release/latest_demo_verified.json` records
the explicit demo waivers and verified Docker/load/security/recovery evidence. See
`docs/P9_IMPLEMENTATION_REPORT.md` and `docs/P10_IMPLEMENTATION_REPORT.md`.

### 运行时产物包

模型权重和生成后的知识文档不提交 Git。发布演示仓库前，可生成一个不含 `.env` 或密钥的
运行时包，并将报告中的 SHA-256 与压缩包一起发布到受控 Release：

```powershell
uv run --cache-dir .uv-cache --frozen python scripts/export_runtime_bundle.py
uv run --cache-dir .uv-cache --frozen python scripts/import_runtime_bundle.py `
  dist/commerce-agent-demo-runtime-v1.zip --sha256 <release-sha256>
```

`dist/` 默认被 Git 忽略。导入器要求压缩包成员与清单完全一致、拒绝路径穿越，并逐文件
验证大小和 SHA-256；遇到不同的已有文件默认失败关闭，只有显式 `--replace` 才会替换。
v1 包已上传到私有 GitHub Release `demo-runtime-v1`，远端资产大小和 GitHub 返回的 digest
均与本地报告一致。完整隔离栈从零启动仍是独立验收项。

## 数据安全与版本边界

- `.env`、原始数据、翻译、标注内容、处理后 Parquet、模型权重和备份不提交 Git。
- 仓库保留下载/构建脚本、来源清单、revision、SHA256、统计和非敏感报告。
- SMP2017 仅用于本地非商业研究；所有数据按各自许可与署名要求使用，不重新分发原文。
- `candidate_pool_v1.yaml` 冻结候选池分布；`dataset_v1.yaml` 冻结质量过滤后的最终配额。
- DeepSeek 外部失败时任务会明确失败或安全降级，不会静默换模型或伪造样本。
- 当前 P1/P9 已生成项目所有者接受的 AI 辅助演示证据；人工金标队列仍未执行，
  严格生产发布继续阻塞。
- 当前 P3 的财务动作全部为 `mock_*` 审批任务，不会调用真实支付或退款接口。
- MCP 工具中的主体、Scope、Audience、Trace 和 Deadline 只能由 Gateway 可信请求头
  注入，不能由 DeepSeek 或工具参数填写。

P0–P10 的证据分别见 `docs/P0_IMPLEMENTATION_REPORT.md`、
`docs/P1_IMPLEMENTATION_REPORT.md`、`docs/P2_IMPLEMENTATION_REPORT.md`、
`docs/P3_IMPLEMENTATION_REPORT.md`、`docs/P4_IMPLEMENTATION_REPORT.md`、
`docs/P5_IMPLEMENTATION_REPORT.md`、`docs/P6_IMPLEMENTATION_REPORT.md`、
`docs/P7_IMPLEMENTATION_REPORT.md`、`docs/P8_IMPLEMENTATION_REPORT.md`、
`docs/P9_IMPLEMENTATION_REPORT.md`、`docs/P10_IMPLEMENTATION_REPORT.md`。
