# P4 MCP 服务与八个工具实施报告

## 1. 完成状态

P4 已实现三个独立 FastMCP 服务和八个业务工具，并通过真实 Streamable HTTP、真实
MySQL、跨用户安全和 20 并发幂等测试。服务只负责可验证的业务事实和确定性规则，
本阶段没有调用 DeepSeek，也没有执行真实支付、退款或订单修改。

三个容器均以非 root 用户运行并通过 Docker healthcheck：

| 服务 | 内部地址 | 工具数 | 状态 |
| --- | --- | ---: | --- |
| `mcp-catalog` | `http://mcp-catalog:8101/mcp` | 3 | healthy |
| `mcp-order` | `http://mcp-order:8102/mcp` | 3 | healthy |
| `mcp-after-sales` | `http://mcp-after-sales:8103/mcp` | 2 | healthy |

主 Compose 不向宿主机暴露 MCP 端口；只有使用 `compose.dev.yaml` 调试时才映射
8101–8103。服务之间通过 `commerce-agent-net` 和 Compose service name 通信。

## 2. 工具清单

| 工具 | 服务 | Scope | 写入 | 结果来源 |
| --- | --- | --- | ---: | --- |
| `get_product_detail` | Catalog | `catalog:read` | 否 | MySQL 商品/SKU |
| `check_inventory` | Catalog | `catalog:read` | 否 | MySQL 库存快照 |
| `search_products` | Catalog | `catalog:read` | 否 | MySQL 商品、SKU、价格 |
| `get_order_detail` | Order | `order:read` | 否 | 当前主体订单快照 |
| `list_recent_orders` | Order | `order:read` | 否 | 当前主体订单分页 |
| `track_logistics` | Order | `order:read` | 否 | 当前主体脱敏物流轨迹 |
| `check_after_sales_eligibility` | After-sales | `after-sales:read` | 否 | 订单快照和确定性规则 |
| `prefill_service_ticket` | After-sales | `after-sales:write` | 是 | 工单草稿和 mock 审批 |

每个工具返回精确的 `ToolEnvelope[T]` JSON Schema，而不是无约束字典。Envelope 包含：

- `ok`、`data`、`error`；
- `tool_call_id`、`trace_id`、`schema_version`；
- `as_of`、`duration_ms`、`retry_count`；
- 版本化错误码和是否允许重试。

输入和输出模型都设置 `additionalProperties=false`。商品 ID、订单 ID、幂等键、分页上限、
价格区间和售后类型均在 MCP JSON Schema 中带有机器可读约束。

## 3. 可信身份边界

模型可见的八个工具 Schema 中没有以下字段：

```text
user_id
principal_id
scopes
audience
trace_id
request_id
deadline_ms
```

这些字段只能由 Gateway 通过内部 HTTP 请求头注入：

| 请求头 | 作用 |
| --- | --- |
| `X-Internal-Token` | Gateway 与 MCP Server 的内部共享认证 |
| `X-Principal-Id` | 服务端验证后的可信主体 |
| `X-Scopes` | 空格或逗号分隔的权限范围 |
| `X-Mcp-Audience` | 防止 Token/上下文跨 MCP 服务复用 |
| `X-Trace-Id` | 端到端追踪 |
| `X-Request-Id` | 请求去重和定位 |
| `X-Deadline-Ms` | 1–60,000 ms 的服务端执行预算 |

内部 Token 使用常量时间比较。缺失 Token、错误 audience、非法主体、缺失 scope、非法
trace/request ID 或越界 deadline 都在数据库访问前失败。

Order 和 After-sales 服务的数据库查询始终带上 `principal_id + order_id`。订单不存在和
不属于当前主体统一返回 `NOT_FOUND`，响应中不包含订单金额、明细、真实所有者或物流字段。

## 4. 商品和订单工具

商品工具返回：

- 商品、品牌、类目、属性、使用/人群标签和数据版本；
- 所有在售 SKU、当前价格、`price_snapshot_id` 和 `valid_until`；
- 区域库存数量、仓库和 `as_of`；
- 可解释的关键词、品牌、描述或结构化条件匹配原因。

订单工具返回成交时的商品名、单价和总额快照，不引用当前商品价格。最近订单使用不透明
游标分页，游标错误返回 `INVALID_ARGUMENT`。物流单号使用数据库中的脱敏值。

## 5. 售后预填和幂等事务

`prefill_service_ticket` 的服务器端流程：

1. 验证内部 Token、audience 和 `after-sales:write`；
2. 使用可信主体重新查询订单和订单项；
3. 重新运行取消或退换修资格规则；
4. 从订单项成交快照计算退款金额；
5. 对描述中的手机号和邮箱进行二次脱敏；
6. 计算规范化幂等指纹；
7. 先 flush 工单父记录，再在同一事务写入 mock 审批；
8. MySQL 唯一键冲突后回滚并读取首次结果；
9. 返回 `duplicate=true` 和首次创建的同一个 `ticket_id`。

写工具没有自动重试。Redis 不承担最终一致性；最终防线是 MySQL 的
`uq_ticket_principal_idem` 和 `uq_ticket_fingerprint`。

所有审批动作均以 `mock_` 开头：退换修使用 `mock_refund_review`，取消草稿使用
`mock_cancel_draft`。系统没有真实资金接口。

## 6. 测试证据

P4 分组测试：

```text
tests/mcp/contracts     7 passed
tests/mcp/security      9 passed
tests/mcp/idempotency   1 passed
合计                    17 passed
```

包含 P0–P4 的全仓回归为 `54 passed, 1 warning`；Ruff、105 文件格式检查、Mypy 69 个
源文件、Compose 配置解析和 Alembic schema 漂移检查全部通过。唯一 warning 来自
Starlette 测试客户端使用的 AnyIO 弃用别名，不影响当前功能。

覆盖内容：

- MCP 工具名称恰好为要求的八个；
- 输入和输出 JSON Schema 具有严格约束；
- 工具 Schema 不暴露任何身份和权限字段；
- 商品、库存、订单、分页、物流和售后规则读取真实 MySQL；
- 错误 Token、audience、deadline、trace 和主体被拒绝；
- 跨用户订单、物流和售后查询不泄露订单字段；
- 缺失 scope 在返回业务数据前失败；
- 只读调用超时返回可重试 `TIMEOUT`，写调用超时明确禁止自动重试；
- 20 个并发相同预填请求只产生 1 条工单和 1 条审批；
- 其余 19 次返回同一个首次工单，并标记 `duplicate=true`；
- 手机号和邮箱在写入前被替换为 `[PHONE]`、`[EMAIL]`；
- 并发测试完成后清理临时数据，P3 数据校验仍为 `passed`。

真实 MCP 客户端还完成了三次 Streamable HTTP 握手、`tools/list` 和 `tools/call`：

```text
mcp-catalog       get_product_detail                 passed
mcp-order         get_order_detail                   passed
mcp-after-sales   check_after_sales_eligibility      passed
```

## 7. 运行与复现

启动基础设施并迁移、Seed：

```powershell
Set-Location -LiteralPath 'C:\Users\mtcbf\Desktop\commerce-agent'
docker compose --profile core up -d mysql redis
docker compose --profile tooling run --rm worker uv run alembic upgrade head
docker compose --profile tooling run --rm worker uv run python scripts/seed_demo_data.py
```

启动三个 MCP 服务：

```powershell
docker compose --profile demo-full up -d --build `
  mcp-catalog mcp-order mcp-after-sales
docker compose --profile demo-full ps `
  mcp-catalog mcp-order mcp-after-sales
```

协议冒烟和分组测试：

```powershell
docker compose --profile tooling run --rm worker uv run python scripts/smoke_mcp.py
docker compose --profile tooling run --rm worker uv run pytest tests/mcp/contracts -q
docker compose --profile tooling run --rm worker uv run pytest tests/mcp/security -q
docker compose --profile tooling run --rm worker uv run pytest tests/mcp/idempotency -q
```

首次复制 `.env.example` 后，应把 `MCP_INTERNAL_TOKEN` 改为独立随机值。代码在未单独配置
时会兼容回退到 `APP_SECRET`，仅用于现有本地开发环境平滑升级；正式演示配置应显式设置。

## 8. 阶段边界

P4 只负责 MCP 工具、可信上下文、业务查询和售后草稿。以下内容留给后续阶段：

- P5：ES + Milvus 双索引、BGE Embedding、Reranker 和引用；
- P6：LangGraph 主图、子图、工具选择和图预算；
- P7：Gateway JWT 验证、审批 interrupt/resume、完整 MySQL 工具审计和 SSE；
- P9：1,000 条工具评测、攻击集和负载测试。

因此本阶段能够证明工具服务本身可运行、权限不可由模型伪造、写入具备数据库幂等性，
但不声称完整 Agent 编排已经完成。
