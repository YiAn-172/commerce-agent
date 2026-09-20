# P3 业务数据层与确定性演示数据实施报告

## 1. 完成状态

P3 已完成并通过真实 MySQL 8.4 环境验收。本阶段交付了可迁移的业务数据库、确定性
演示数据、订单与售后规则、数据访问边界和自动化验证；没有调用 DeepSeek，也没有执行
任何真实支付、退款或库存扣减。

验收基线：

- Alembic revision：`cdb0d35109b0`（head）。
- 固定随机种子：`20260915`。
- 数据版本：`demo-business-v1`。
- 数据集指纹：`459ebd36abae58a13bbe42aa39d14f622357caa8b6325150d120a591f94af785`。
- P3 单元测试：13 个全部通过。
- 数据库自动校验：`passed`，错误数为 0。

## 2. 数据模型

Alembic 迁移和 SQLAlchemy 2.x 模型共同定义以下 18 张表：

| 领域 | 表 |
| --- | --- |
| 用户、商品、库存 | `users`、`products`、`skus`、`inventory` |
| 订单、履约、售后 | `orders`、`order_items`、`logistics_events`、`after_sales_rules`、`service_tickets`、`approval_tasks` |
| 对话与 Agent 审计 | `chat_sessions`、`chat_messages`、`agent_runs`、`tool_call_logs` |
| 知识与评测 | `knowledge_documents`、`knowledge_versions`、`evaluation_runs`、`evaluation_results` |

`service_tickets` 在数据库中实际存在两个唯一约束：

- `uq_ticket_principal_idem`：同一主体下，幂等键不得重复。
- `uq_ticket_fingerprint`：规范化请求指纹全局唯一。

订单明细保留商品名、SKU 名、成交单价和小计快照；退款金额只能从订单明细快照计算，
不会引用可能变化的商品现价。SKU 价格带有 `valid_until`，库存带有 `as_of`，为后续
Agent 工具提供“数据是否过期”的明确判断依据。

## 3. 业务规则

### 3.1 订单状态机

状态迁移由代码白名单控制，未声明的迁移直接拒绝。关键取消策略如下：

- `pending_payment`：允许生成直接取消草稿。
- `paid`、`packed`：只能生成待人工审批的取消草稿。
- `shipped`：禁止直接取消，转为物流拦截或售后流程。
- `delivered`：依据类目、时效、商品状态和排除原因判断退换资格。

所有金额相关动作在本阶段只生成 `mock_*` 类型审批任务。数据校验会拒绝任何非模拟
财务动作，从而避免演示环境误触真实交易。

### 3.2 售后资格

资格判断同时检查：

1. 订单是否处于允许售后的状态；
2. 类目和申请类型是否命中生效规则；
3. 签收时间是否在规则窗口内；
4. 商品状态是否允许；
5. 原因码是否在排除列表；
6. 规则的版本和生效时间。

判断结果返回稳定的原因码和缺失材料列表，适合后续 MCP 工具和 Agent 编排直接消费，
而不是让大模型自行猜测业务规则。

### 3.3 数据权限与幂等

订单查询仓储必须同时携带 `principal_id` 和 `order_id`，从数据访问层阻止跨用户查询。
售后请求使用“主体、订单、明细、申请类型、幂等键”生成稳定 SHA-256 指纹，并由两组
数据库唯一约束处理并发重放。

## 4. 演示数据

种子脚本生成的数据全部为虚构或脱敏内容。品牌使用虚构名称，手机号保持
`13x****xxxx` 格式，不含真实用户联系方式。

| 数据 | 行数 |
| --- | ---: |
| 用户 | 100 |
| 商品 | 300 |
| SKU | 1,200 |
| 分仓库存 | 3,600 |
| 订单 | 3,000 |
| 订单明细 | 4,500 |
| 物流事件 | 6,533 |
| 售后规则 | 80 |
| 服务工单 | 100 |
| 审批任务 | 100 |
| 知识版本 | 1 |

每个已发货及后续状态订单具有 2–6 条物流事件。80 条售后规则中有 20 条当前生效，
其余用于版本和时间边界测试。

### 4.1 十个稳定场景 ID

| 场景 | 固定 ID |
| --- | --- |
| 待支付订单 | `ord_demo_000001` |
| 已支付订单 | `ord_demo_000002` |
| 已打包订单 | `ord_demo_000003` |
| 已发货订单 | `ord_demo_000004` |
| 已签收订单 | `ord_demo_000005` |
| 已申请取消订单 | `ord_demo_000006` |
| 已取消订单 | `ord_demo_000007` |
| 已退款订单 | `ord_demo_000010` |
| 预算耳机商品 | `prd_demo_000001` |
| 零库存样例 | `inv_demo_000121_0` |

这些 ID 被写入版本化 manifest，并在数据库校验中逐项确认记录确实存在。后续演示、
接口测试和评测集可以稳定引用，不依赖随机查询结果。

## 5. 可重复执行与失败保护

种子脚本首先检查当前数据库计数：

- 数据库为空时，在一个事务内写入整套数据。
- 数据与目标版本完全一致时返回 `already_seeded`，不会重复插入。
- 发现部分数据或计数漂移时默认失败，避免覆盖人工修改。
- 只有显式传入 `--reset` 才允许重置 P3 数据。

复现命令：

```powershell
Set-Location -LiteralPath 'C:\Users\mtcbf\Desktop\commerce-agent'
docker compose --profile core up -d mysql redis
docker compose --profile tooling run --rm worker uv run alembic upgrade head
docker compose --profile tooling run --rm worker uv run python scripts/seed_demo_data.py
docker compose --profile tooling run --rm worker uv run python scripts/validate_demo_data.py
```

迁移状态与漂移检查：

```powershell
docker compose --profile tooling run --rm worker uv run alembic current
docker compose --profile tooling run --rm worker uv run alembic check
```

## 6. 验收证据

真实 MySQL 自动校验已确认：

- 必需表 18 张，缺失 0 张；
- 必需工单唯一约束 2 个，缺失 0 个；
- 订单金额与订单明细快照不一致 0 条；
- 工单退款金额与订单明细快照不一致 0 条；
- 物流事件数量不合规订单 0 个；
- 未脱敏手机号 0 个；
- 非模拟审批动作 0 个；
- 十个稳定场景记录全部存在。

单元测试覆盖状态机、取消决策、售后资格、退款快照、价格时效、幂等指纹、订单归属、
数据库唯一约束、18 表契约，以及相同种子重复生成的指纹稳定性。

## 7. 阶段边界

P3 只实现业务数据和规则内核。以下内容不在本阶段伪装完成：

- MCP 服务、工具协议、鉴权中间件和工具级审计将在 P4 实现；
- 知识文档清洗、切分、向量化和召回评测将在 P5 实现；
- DeepSeek Agent 编排、双轨状态机和回复生成将在后续阶段实现；
- 真实支付、退款、订单修改和外部物流写操作始终不属于本演示系统。

因此，P3 的结论是“业务数据底座和规则内核可复现、可测试、可供后续工具调用”，并非
完整客服 Agent 已上线。
