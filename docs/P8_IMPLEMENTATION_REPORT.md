# P8 演示前端实施报告

日期：2026-09-18  
状态：工程实现与本地容器验收通过；DeepSeek 真实生成仍受账户余额阻塞

## 1. 交付范围

P8 已在 `apps/web` 实现 Next.js App Router + TypeScript 演示控制台，覆盖流程文件约定的
全部展示面：

- Demo 用户、客服、管理员选择；
- 用户隔离的会话列表、历史消息与新建会话；
- `POST /chat/stream` 的 SSE 运行状态、终态与错误态；
- Agent 候选商品卡片；
- 可展开的 RAG 引用；
- 工具名称、成功/失败、耗时、错误码与白名单结果摘要；
- 人工审批队列，以及通过、拒绝、退回补充和工作流恢复；
- 当前/最近知识版本、最近索引任务与最近评测结果。

没有实现购物车、真实支付、复杂账号、图片搜索或单独移动端产品。财务相关动作仍是
`mock_*` 业务动作，不连接真实支付渠道。

## 2. 运行架构

```text
浏览器 :3000
    │ 同源请求；不保存 Bearer Token
    ▼
Next.js standalone Web / BFF
    │ HttpOnly Cookie -> Authorization Header
    │ 仅允许固定 API 白名单；SSE 流透传
    ▼
FastAPI :8000
    ├─ MySQL：会话、审批、知识版本、评测
    ├─ Redis：SSE 事件历史与 Last-Event-ID 续传
    ├─ Intent Service：本地意图分类
    ├─ Elasticsearch / Milvus：RAG
    ├─ MCP Servers：商品、订单、售后工具
    └─ DeepSeek API：答案生成
```

浏览器只访问同源 `/api/backend/*`。BFF 登录接口把 FastAPI JWT 写入
`HttpOnly + SameSite=Lax` cookie，响应正文不向浏览器 JavaScript 返回 token。代理使用
方法和正则路径双重白名单，不是任意 URL 转发器。

## 3. 新增后端契约

### 3.1 会话列表

`GET /api/v1/sessions`：

- customer 只返回 `user_id == JWT.sub` 的最近 50 个会话；
- agent/admin 可读取最近 50 个会话，用于客服排查；
- 仍由 `session:read` scope 控制。

### 3.2 安全的 Agent 展示字段

`ChatResponse` 与 SSE `message_completed` 新增：

- `products`：图状态中的候选商品；
- `tool_trace`：只保留 `tool_name/ok/duration_ms/error_code/result_summary`。

没有返回 prompt、消息草稿、模型 reasoning、内部 checkpoint 或工具原始认证头。

### 3.3 运营概览

- `GET /api/v1/knowledge/status`：活动版本、最近版本、状态与最近五个重建任务；
- `GET /api/v1/evaluations`：最近五次评测及通过数量；
- agent/admin 获得 `knowledge:read` 与 `evaluation:read`，customer 没有这些 scope。

当 MySQL 没有 `active` 知识版本、但已存在 `verified` 版本时，接口同时返回
`active_version=null` 和 `latest_version`，页面明确显示“LATEST VERSION/尚未激活”，不会把
“已验证”伪装成“已激活”。当前数据库返回 `kb_20260917_001 / verified`。

## 4. Web 交互与边界

### 4.1 用户工作台

- 登录后拉取自己的最近会话和消息；
- 没有会话时，首次发送会自动创建；
- 流式请求展示 connecting/running/done/error；
- 当前请求的商品、引用和工具轨迹显示在消息区/证据侧栏；
- 历史消息只显示持久化内容，不伪造不存在的历史 trace。

### 4.2 客服工作台

- 服务端返回全部待处理任务，页面首屏只渲染最近 20 条并显示 `20 / 总数`，避免大量输入
  控件导致浏览器卡顿；刷新后会重新取数；
- 决策提交必须携带 `state_version` 和处理说明；
- 决策提交成功后立即调用 resume；后端仍负责乐观锁、状态复检与幂等；
- 浏览器验收只读队列，没有擅自改变已有审批数据。

### 4.3 知识与评测

- 知识卡区分 ACTIVE VERSION 和 LATEST VERSION；
- 嵌套评测指标提取首选核心指标形成短摘要，完整值保留在 `title`，避免把大段 JSON
  直接铺满页面；
- 当前持久评测为 2,400 cases、2,009 passed，页面如实显示 83.7%，未改写实测数字。

## 5. 容器化

`infra/docker/web.Dockerfile` 使用固定 `node:24.21.0-bookworm-slim`：

1. `deps`：只复制 package manifest，执行 `npm ci`；
2. `builder`：执行 `tsc --noEmit` 与 `next build`；
3. `runner`：只复制 Next standalone 和静态产物；
4. UID/GID 1001 的 `nextjs` 非 root 用户启动；
5. 内置 `/api/health` 健康检查。

仓库只保留 `package-lock.json`，没有第二种 JS lockfile。浏览器运行不依赖外部字体或 CDN。

Compose 的 `web` 同时加入 `dev-lite` 与 `demo-full` profile，容器内通过
`API_INTERNAL_URL=http://api:8000` 访问 API，宿主机只暴露 `3000`。

## 6. 验收结果

### 6.1 自动化

```text
Next TypeScript:       passed
Next production build: passed (standalone)
Ruff:                  passed
mypy packages/apps:    76 files, passed
mypy 含 tests:          120 files, passed
pytest:                 98 passed, 1 third-party deprecation warning
Compose config:         passed
```

新增测试验证：

- customer 的会话列表不能看到其他用户会话；
- agent 可查看跨用户会话但仍需 `session:read`；
- agent 可以读取知识/评测看板；
- 活动知识版本和评测通过数量来自真实数据库；
- SSE 仍只产生一个 terminal event，续传不会再次执行 Agent；
- SSE 正文不包含 `reasoning_content`。

### 6.2 真实容器

`dev-lite` 实测健康：

```text
mysql          healthy
redis          healthy
elasticsearch  healthy
intent-service healthy
api            healthy
web            healthy
```

BFF 冒烟结果：

```text
web health             ok
customer HttpOnly 会话  created
customer sessions       8
agent HttpOnly 会话     created
pending approvals       100
knowledge               kb_20260917_001 / verified
evaluation runs         1
```

### 6.3 浏览器检查

实际浏览器完成：角色选择、用户会话加载、消息输入、SSE `Agent 执行中`、错误终态、客服队列
加载、知识版本和评测卡加载。首屏与窄屏布局无水平溢出，按钮和输入框可用。

## 7. 已知阻塞与下一步

DeepSeek 官方接口仍返回余额不足相关失败，因此本轮浏览器聊天最终进入明确错误态，不能记为
“真实生成成功”。前端没有静默切换其他模型，也没有用固定答案伪造在线结果。充值后需要：

1. 重跑 `scripts/smoke_deepseek_live.py`；
2. 以 `demo-full` 启动三个 MCP 与 Milvus；
3. 在浏览器完成知识、选品、订单、售后四类对话；
4. 核对商品卡、引用、工具耗时和审批中断/恢复；
5. 将成功证据写入 P10 发布报告。

P8 已完成；下一阶段按流程进入 P9 自动评测、压测与安全测试。
