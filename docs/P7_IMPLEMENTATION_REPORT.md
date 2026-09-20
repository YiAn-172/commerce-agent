# P7 Human-in-the-loop、API 与 SSE 实现报告

## 1. 结论

P7 的 Human-in-the-loop 审批状态机、LangGraph `interrupt()`、数据库先提交后恢复、
崩溃补偿、FastAPI 主路由、JWT Scope、Redis SSE 事件留存与知识管理入口已实现。

2026-09-18 最终工程门禁：

- P0–P7 全仓 Pytest：96/96 通过；
- Ruff：通过；
- mypy：112 个源码文件通过；
- Alembic：当前 revision `d2a6f4108c3b`，无 Schema 漂移；
- Docker Compose：解析通过；
- 演示数据：19 张必需表存在，100 条种子工单和审批完整；
- 真实审批恢复演练：通过；
- 真实 Redis SSE 留存/续传演练：通过；
- 最终 API 镜像重建成功，健康检查通过，OpenAPI 包含 P7 规定路由。

真实 DeepSeek 聊天成功路径仍受账户 HTTP 402 `Insufficient Balance` 阻塞。API、图和 SSE
成功路径使用受控服务替身测试，错误终态不会伪装成真实模型成功。

## 2. 审批状态机

`packages/approval_core/service.py` 实现以下审批状态：

```text
pending_human_approval
  -> approved
  -> rejected
  -> needs_more_info
  -> cancelled
  -> expired（仅过期任务自动设置）
```

每次状态变更都使用 `approval_id + state_version + 当前状态` 条件更新。只有更新行数为 1
才提交，同时在同一事务中同步 `service_tickets.approval_status`。相同审批人、理由和决策的
重复点击返回已有结果；不同的第二次决策或旧版本请求失败关闭。

P7 为 `approval_tasks` 增加：

- `session_id`；
- `checkpoint_thread_id`；
- `decided_by`、`decision_reason`；
- `expires_at`；
- `resumed_at`；
- `resume_result`；
- `updated_at`。

迁移保留已有 100 条演示审批数据。运行 P7 冒烟后数据库共有 102 条工单和审批，其中
`ticket_demo_%`/`approval_demo_%` 种子记录仍各为 100 条；新增两条是可追踪的真实 P7
恢复演练记录，不属于种子污染。

## 3. LangGraph interrupt 与崩溃恢复

`packages/approval_core/workflow.py` 的独立审批图：

```text
START
  -> await_human_decision / interrupt(payload)
  -> revalidate
  -> finalize
  -> END
```

工作流遵守以下顺序：

1. 用户确认后，After-sales MCP 幂等创建工单草稿和 `mock_*` 审批；
2. 审批绑定 `session_id` 与持久化 checkpoint thread；
3. LangGraph 调用 `interrupt()`，payload 只包含审批 ID、工单 ID、动作、金额快照、版本和
   允许的决策；
4. 客服使用 `approval:decide` Scope 提交数据库决策；
5. `Command(resume=...)` 恢复前，代码核对恢复 payload 与数据库已提交决策；
6. 只有 `approved` 会通过 After-sales MCP 重新检查订单状态和售后资格；
7. 资格失效时用新版本原子补偿为 `cancelled`；
8. 最终结果写入 `resume_result`，重复恢复直接返回已存在结果。

`scripts/recover_approvals.py` 是补偿任务：先关闭到期 pending 审批，再扫描“已有终态、
有 checkpoint、但 `resumed_at` 为空”的记录，并逐条只恢复一次。

### 3.1 真实崩溃演练

本轮没有只做内存测试，而是执行了以下真实流程：

1. 通过 FastAPI 登录演示用户；
2. 创建 MySQL 会话；
3. 通过真实 Streamable HTTP After-sales MCP 创建售后草稿；
4. 审批图进入持久 SQLite `interrupt()`；
5. 客服将 `approved` 提交到 MySQL，但不恢复图；
6. `docker compose restart api` 模拟进程崩溃；
7. 重启后运行 `scripts/recover_approvals.py`；
8. 补偿任务从 named volume 中的 checkpoint 恢复；
9. 真实调用 After-sales MCP 复检资格；
10. 第二次 API resume 返回 `duplicate=true`。

实测结果：

```json
{
  "status": "passed",
  "recovered_count": 1,
  "decision": "approved",
  "outcome": "mock_action_authorized",
  "duplicate_resume": true,
  "real_money_action": false
}
```

证据文件：

- `reports/api/p7_recovery_worker.json`
- `reports/api/p7_smoke.json`

`mock_action_authorized` 只表示演示审批通过，不调用支付、退款、取消订单或第三方工单系统。

## 4. FastAPI 与权限

P7 注册并验证了以下路由：

```text
POST /api/v1/auth/demo-login
POST /api/v1/sessions
GET  /api/v1/sessions/{id}
POST /api/v1/chat
POST /api/v1/chat/stream
POST /api/v1/after-sales/confirm
GET  /api/v1/approvals
POST /api/v1/approvals/{id}/decision
POST /api/v1/approvals/{id}/resume
POST /api/v1/knowledge/documents
POST /api/v1/knowledge/reindex
GET  /api/v1/evaluations/{run_id}
GET  /health/live
GET  /health/ready
```

Demo JWT 使用 HS256，签名材料先经 SHA-256 派生为 32 字节密钥；Token 固定 audience，
包含过期时间、角色和 Scope。服务端拒绝角色允许集合之外的 Scope。

角色边界：

- Customer：创建/读取自己的会话、聊天、确认售后；
- Agent：读取会话、查看/决定/恢复审批；
- Admin：上述权限加知识写入和评测读取。

Customer 请求其他用户会话返回 404，避免暴露资源是否存在。`principal_id` 不由请求中的
工具参数决定，而是从校验后的 JWT 进入服务端可信上下文。

## 5. SSE

`packages/api_core/events.py` 使用 Redis 保存每个会话最近 600 秒的事件。事件 ID 和
sequence 由 Redis 原子递增生成。实现包括：

- 15 秒 heartbeat；
- `Last-Event-ID` 后续传；
- 客户端按 `event_id` 去重；
- 每次运行恰好一个 `completed` 或 `error`；
- 断开连接时取消仍在执行的本地任务；
- 事件 payload 递归移除 token、authorization、API Key、Prompt、reasoning 和
  `reasoning_content`；
- Redis key 自动设置 600 秒 TTL。

真实 Redis 冒烟结果：

```json
{
  "status": "passed",
  "event_count": 3,
  "resume_count": 2,
  "sequences": [1, 2, 3],
  "terminal_events": ["completed"],
  "ttl_seconds": 600,
  "forbidden_payload_removed": true
}
```

证据文件：`reports/api/p7_sse_redis.json`。

## 6. 知识与评测 API

知识文档上传执行 SHA-256 幂等：相同内容返回已有文档，不重复写入。文档只允许
`faq/policy/activity/product` 四类，先保存为 `draft`。

重建请求持久化到 `knowledge_reindex_jobs`。`scripts/run_reindex_jobs.py` 使用数据库锁只
领取一个 queued job，激活目标文档，然后复用 P5 的 build、verify、activate 三段流程；
失败时将文档状态恢复并保存错误摘要，不会切换未验证索引。

评测读取接口直接返回 `evaluation_runs` 的固定 manifest、指标和结果通过数量，不现场
重新计算或伪造简历数字。

## 7. 数据库迁移

P7 包含两个增量 revision：

- `b4d7e9f102aa`：审批 checkpoint、决策、过期和恢复字段；
- `d2a6f4108c3b`：知识原文、创建者和 `knowledge_reindex_jobs`。

第二个迁移在首次演练时暴露了 MySQL 非事务 DDL 的部分执行特性。迁移已改为安全重入：
先检查列/表是否存在，并在 `ALTER COLUMN` 中显式声明 `existing_type`。随后从部分执行状态
续跑成功，最终 `alembic check` 为 `No new upgrade operations detected`，没有清库。

## 8. 自动化测试

P7 新增 13 个测试，P0–P7 合计 96 个：

- 审批原子决策、重复点击、不同决策冲突；
- 自动过期；
- `interrupt()`、批准恢复和重复恢复；
- 批准后资格复检与失效补偿；
- 数据库已提交但图未恢复的补偿恢复；
- JWT Scope 和无效 Token；
- SSE 单终态、续传、不重复运行和隐藏字段过滤；
- 知识文档幂等、缺失文档失败关闭和重建任务入队；
- 评测结果读取。

最终结果：

```text
96 passed, 1 warning in 20.85s
```

唯一 warning 是 Starlette TestClient 对 AnyIO 旧类型别名的上游弃用提示。

## 9. 已知边界

- DeepSeek 余额不足，真实 `/chat` 成功生成仍待充值后复验；错误会形成 SSE `error`
  终态，不会伪造回复。
- 知识重建 worker 已实现并复用 P5 三段门禁，但本轮未激活一个人为上传的新知识版本，
  避免为了接口验收改变当前已验证 alias；该演练应在单独目标版本上执行。
- Demo Auth 只适用于本地展示，生产必须替换为真实身份提供商和密钥轮换。
- 备份恢复、全新环境 5 分钟启动、360 组 E2E 和压测仍属于 P9–P11。
