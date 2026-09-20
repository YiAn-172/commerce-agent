# P6 LangGraph Agent 实现与验收报告

## 1. 结论

P6 的主图、四个业务子图、图预算、受控 DeepSeek 适配器、MCP/RAG/意图网关、
SQLite checkpoint 和 MySQL 运行轨迹持久化已实现。2026-09-18 的内部工程门禁结果为：

- 全仓 Pytest：83/83 通过；
- Ruff：通过；
- mypy：97 个源码文件通过；
- Alembic：无待生成升级操作；
- Compose：配置解析通过；
- 演示数据库：18 张必需表存在，固定业务数据一致性检查通过；
- 五类真实基础设施集成场景全部通过，见 `reports/agent/p6_smoke.json`；
- DeepSeek 真实 API 结构化输出复验已于 2026-09-20 通过，证据见
  `reports/agent/p6_deepseek_live.json`。

这意味着 P6 的本地代码、图路由、基础设施集成和真实 DeepSeek 最小结构化输出门禁均已
通过。

## 2. 实现范围

### 2.1 主图

`packages/agent_core/graph.py` 实现一个编译后的 LangGraph 主图：

```text
preprocess
  -> safety gate
  -> intent classify
  -> confidence gate
  -> select task
  -> route
  -> knowledge | shopping | order | after_sales
  -> complete task
  -> answer validate
  -> persist trace
```

安全拦截在模型和工具调用之前执行。低置信度/OOS 进入可解释澄清或安全回复；多意图最多
拆成两个任务，并按“只读优先、可能写入的售后最后”排序。每个新用户轮次都重置意图、
路由和决策等临时字段，避免同一 `thread_id` 的上轮状态污染本轮分类。

### 2.2 四个子图

- Knowledge：查询改写、真实 Hybrid RAG、证据门控、基于证据生成、引用 ID 校验。
- Shopping：槽位抽取、Catalog MCP 检索、库存 MCP 检查、只对候选商品做 RAG、
  候选商品与引用白名单校验。
- Order：订单号提取/最近订单选择、Order MCP 归属校验、确定性事实回复；不调用 LLM
  生成价格、订单或物流事实。
- After-sales：请求槽位抽取、After-sales MCP 资格检查；当前阶段只生成待确认内容，
  用户确认前不创建写操作。

### 2.3 契约与预算

`packages/agent_core/contracts.py` 定义严格 Pydantic 输出模型，未知字段被拒绝。知识答案
必须至少有一个引用，导购答案必须同时包含候选商品 ID 和引用 ID。

`packages/agent_core/budget.py` 对节点数、LLM 次数、工具次数和同参数重复工具调用进行
失败关闭计数。默认单意图预算为 30 个节点、3 次 LLM、5 次工具，同一工具同参数最多
2 次；超限不会继续循环。

## 3. DeepSeek Adapter

`packages/agent_core/deepseek.py` 使用 `AsyncOpenAI` 连接
`https://api.deepseek.com`，默认模型为 `deepseek-flash`。模型名与 2026-09-18 官方
文档一致；旧的 `deepseek-v4-flash` 仅作为兼容别名，不再作为项目默认值。

适配器具备以下约束：

- Prompt 从 `configs/prompts/*_v1.yaml` 按 ID 和版本加载；
- 请求强制 JSON Object，并关闭 thinking；
- 首轮与修复提示都嵌入目标 Pydantic JSON Schema，并禁止额外字段；
- 输出必须通过对应 Pydantic 模型；
- 网络、429、5xx 最多在线重试一次；
- Schema 失败只允许一次 JSON 修复；
- 余额不足、鉴权、业务规则和参数缺失不会被静默重试或切换模型。

单元测试使用伪客户端验证请求参数、失败重试和 JSON 修复。真实 API 冒烟脚本为
`scripts/smoke_deepseek_live.py`，它不会输出或写入 API Key。2026-09-20 复验中，首轮模型
曾返回额外 `type` 字段并被严格模型拒绝；补充完整 JSON Schema 约束后，真实请求在
1,207.1 ms 内返回并通过 `RewriteOutput` 验证，报告状态为 `passed`。

复验命令：

```powershell
docker compose --profile tooling run --rm worker `
  uv run python scripts/smoke_deepseek_live.py
```

成功条件是进程返回 0，且 `reports/agent/p6_deepseek_live.json` 中 `status=passed`、
输出符合 `RewriteOutput`。

## 4. 网关与信任边界

- `HttpIntentGateway` 调用真实 `intent-service`，直接从 JSON 文本执行严格模型解析。
- `RagRetrievalGateway` 调用 P5 的 ES + Milvus + Reranker 实现。
- `McpToolGateway` 使用 Streamable HTTP 调用三个 MCP Server；`principal_id`、Scope、
  Audience、Trace、Request 和 Deadline 只放入可信请求头，不暴露给 LLM 工具参数。
- `MCP_INTERNAL_TOKEN` 未单独配置时，本地演示环境与服务端一致地回退到 `APP_SECRET`；
  生产/共享环境仍应配置独立随机 token。

## 5. checkpoint 与运行轨迹

`packages/agent_core/runtime.py` 使用 `AsyncSqliteSaver`，把 `session_id` 映射为
LangGraph `thread_id`。P6 测试验证同一线程连续调用可恢复状态，并验证新轮次临时状态
不会继承旧值。

`packages/agent_core/persistence.py` 将运行结果写入 MySQL：

- `chat_sessions`：会话与乐观锁 `state_version`；
- `agent_runs`：路由、状态、节点/LLM/工具计数；
- `chat_messages`：用户输入和最终回复；
- `tool_call_logs`：工具名、请求摘要、结果和耗时。

更新使用 `state_version` 条件写入，版本冲突时失败关闭。P6 v2 冒烟写入了 5 个会话、
5 个运行、10 条消息和 4 条工具日志。

## 6. 集成冒烟证据

`scripts/smoke_agent_graph.py` 使用真实意图 HTTP、真实 MCP 协议、真实 RAG、真实 MySQL
和真实 SQLite checkpoint；为保证基础设施回归确定性，仅 LLM 使用录制式严格响应。

| 场景 | 路由 | 最终状态 | 节点 | LLM | 工具 | 引用 |
|---|---|---:|---:|---:|---:|---:|
| knowledge | knowledge | completed | 13 | 2 | 0 | 1 |
| shopping | shopping | completed | 14 | 2 | 2 | 5 |
| order | order | completed | 12 | 0 | 1 | 0 |
| after_sales | after_sales | awaiting_user_confirmation | 11 | 1 | 1 | 0 |
| safety | safe_reply | completed | 5 | 0 | 0 | 0 |

脚本对每个场景的路由、状态、工具数以及知识引用做硬断言，任一不符即非零退出。

复验需要先启动依赖：

```powershell
docker compose --profile demo-full up -d --wait `
  mysql elasticsearch etcd minio milvus intent-service `
  mcp-catalog mcp-order mcp-after-sales
docker compose --profile tooling run --rm worker `
  uv run python scripts/smoke_agent_graph.py
```

## 7. 自动化测试覆盖

P6 新增 18 个 Agent 测试，覆盖：

- 四子图主路径、安全拦截、低置信度/OOS、多意图顺序和意图切换；
- 图预算、工具重复调用限制和写操作确认边界；
- Prompt manifest、严格结构化输出和候选/引用契约；
- DeepSeek 请求参数、一次重试和一次 JSON 修复；
- SQLite checkpoint 的同线程恢复；
- Intent、MCP 与 RAG 网关契约和可信身份头。

完整回归结果：

```text
83 passed, 1 warning in 18.27s
```

唯一 warning 来自 Starlette TestClient 对 AnyIO 旧类型别名的上游弃用提示，不影响测试
结果，也不是业务代码异常。

## 8. 尚未完成与下一阶段

- DeepSeek 真实最小结构化输出已通过；完整业务质量仍由 P9 E2E Judge 和人工复核约束。
- P7 才实现真正的 `interrupt()` 人工审批、批准/拒绝/退回/过期状态机以及崩溃恢复。
- 对外 `/v1/chat`、SSE 事件流、重连/去重和端到端多轮会话尚未接入。
- 800 条路由、1,000 条工具、360 组 E2E、安全攻击集和负载测试属于 P9-P10。

因此本报告只证明 P6 内部工程门禁和真实基础设施集成，不等价于整个项目完成，也不把
录制式 LLM 冒烟描述成真实 DeepSeek 生成。
