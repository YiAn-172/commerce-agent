"use client";

import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

type Role = "customer" | "agent" | "admin";

type Persona = {
  id: string;
  role: Role;
  name: string;
  subtitle: string;
  initials: string;
  accent: string;
};

type Session = {
  session_id: string;
  user_id: string;
  state_version: number;
  graph_version: string;
  created_at: string;
  updated_at: string;
};

type Message = { role: string; content: string; created_at?: string };
type Product = Record<string, unknown>;
type Citation = Record<string, unknown>;
type ToolTrace = {
  tool_name?: string;
  ok?: boolean;
  duration_ms?: number;
  error_code?: string | null;
  result_summary?: Record<string, unknown>;
};
type RunDetail = {
  status: string;
  route?: string | null;
  products: Product[];
  citations: Citation[];
  tool_trace: ToolTrace[];
};
type Approval = {
  approval_id: string;
  ticket_id: string;
  principal_id: string;
  action_type: string;
  status: string;
  amount_snapshot?: string | null;
  state_version: number;
  session_id?: string | null;
  expires_at?: string | null;
};
type KnowledgeStatus = {
  active_version: string | null;
  latest_version: string | null;
  status: string;
  activated_at: string | null;
  recent_jobs: Array<Record<string, unknown>>;
};
type Evaluation = {
  run_id: string;
  name: string;
  status: string;
  metrics: Record<string, unknown>;
  result_count: number;
  passed_count: number;
  created_at: string;
  completed_at?: string | null;
};

const PERSONAS: Persona[] = [
  {
    id: "usr_demo_0001",
    role: "customer",
    name: "林晓",
    subtitle: "商品与订单咨询",
    initials: "林",
    accent: "mint",
  },
  {
    id: "usr_demo_0005",
    role: "customer",
    name: "周然",
    subtitle: "售后审批演示",
    initials: "周",
    accent: "amber",
  },
  {
    id: "agent_demo_0001",
    role: "agent",
    name: "客服小马",
    subtitle: "人工审批工作台",
    initials: "马",
    accent: "violet",
  },
  {
    id: "admin_demo_0001",
    role: "admin",
    name: "运营管理员",
    subtitle: "知识与评测状态",
    initials: "管",
    accent: "blue",
  },
];

const STARTERS = [
  "推荐 500 元以内、适合通勤的耳机",
  "查询订单 ord_demo_000001 的物流",
  "这款蓝牙耳机的降噪怎么样",
];

const EVALUATION_SESSION_PREFIXES = [
  "ses_load",
  "ses_smoke_",
  "ses_security_",
  "ses_p7_",
  "ses_sse_",
  "ses_toolsel_",
];

function isEvaluationSession(sessionId: string) {
  return EVALUATION_SESSION_PREFIXES.some((prefix) => sessionId.startsWith(prefix));
}

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    spark: <path d="m12 2 1.5 5.2L18 9l-4.5 1.8L12 16l-1.5-5.2L6 9l4.5-1.8L12 2Zm-7 12 .8 2.7L8 18l-2.2 1.3L5 22l-.8-2.7L2 18l2.2-1.3L5 14Z" />,
    chat: <path d="M21 12a8 8 0 0 1-8 8H7l-4 2 1.4-4.2A9 9 0 1 1 21 12Z" />,
    plus: <path d="M12 5v14M5 12h14" />,
    shield: <path d="M12 3 4.5 6v5.4c0 4.8 3.1 8.2 7.5 9.6 4.4-1.4 7.5-4.8 7.5-9.6V6L12 3Zm-3 9 2 2 4-4" />,
    book: <path d="M4 5.5A3.5 3.5 0 0 1 7.5 2H11v17H7.5A3.5 3.5 0 0 0 4 22V5.5ZM20 5.5A3.5 3.5 0 0 0 16.5 2H13v17h3.5A3.5 3.5 0 0 1 20 22V5.5Z" />,
    chart: <path d="M4 20V10m6 10V4m6 16v-7m5 7H2" />,
    send: <path d="m22 2-7 20-4-9-9-4 20-7Zm-11 11 5-5" />,
    box: <path d="m21 8-9 5-9-5m9 5v9M5 6l7-4 7 4 2 2v10l-9 4-9-4V8l2-2Z" />,
    tool: <path d="M14.7 6.3a4 4 0 0 0-5-5L12 3.6 9.6 6 7.3 3.7a4 4 0 0 0 5 5L20 16.4V20h-3.6l-7.7-7.7a4 4 0 0 0-5-5L6 9.6 3.6 12 1.3 9.7a4 4 0 0 0 5 5L14.7 6.3Z" />,
    quote: <path d="M7 17H3v-4a6 6 0 0 1 6-6v3a3 3 0 0 0-3 3h1v4Zm10 0h-4v-4a6 6 0 0 1 6-6v3a3 3 0 0 0-3 3h1v4Z" />,
    check: <path d="m5 12 4 4L19 6" />,
    x: <path d="M6 6l12 12M18 6 6 18" />,
    rotate: <path d="M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5" />,
    logout: <path d="M10 4H4v16h6m5-4 4-4-4-4m4 4H9" />,
    arrow: <path d="m9 18 6-6-6-6" />,
    clock: <path d="M12 8v5l3 2m6-3a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />,
  };
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail ?? payload);
    } catch {
      // Keep the status-based message when the upstream did not return JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function formatTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function compactId(value: string) {
  return value.length > 20 ? `${value.slice(0, 12)}…${value.slice(-5)}` : value;
}

function valueText(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function metricText(value: unknown) {
  if (typeof value === "number") return value <= 1 ? value.toFixed(3) : value.toLocaleString("zh-CN");
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    const preferred = ["accuracy", "macro_f1", "ndcg_at_5", "recall_at_5", "mrr", "score"];
    const key = preferred.find((item) => typeof record[item] === "number");
    if (key) return `${key.replaceAll("_", " ")} · ${Number(record[key]).toFixed(3)}`;
    return `${Object.keys(record).length} metrics`;
  }
  return valueText(value);
}

export default function CommerceConsole() {
  const [persona, setPersona] = useState<Persona>(PERSONAS[0]);
  const [authenticated, setAuthenticated] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streamState, setStreamState] = useState<"idle" | "connecting" | "running" | "done" | "error">("idle");
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeStatus | null>(null);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const isCustomer = persona.role === "customer";
  const activeSessionData = sessions.find((item) => item.session_id === activeSession);

  const loadSessions = useCallback(async (preferred?: string | null, preserveRunDetail = false) => {
    const rows = (await api<Session[]>("sessions")).filter(
      (session) => !isEvaluationSession(session.session_id),
    );
    setSessions(rows);
    const next = preferred === undefined ? (rows[0]?.session_id ?? null) : preferred;
    setActiveSession(next);
    if (next) {
      const detail = await api<Session & { messages: Message[] }>(`sessions/${next}`);
      setMessages(detail.messages);
    } else {
      setMessages([]);
    }
    if (!preserveRunDetail) setRunDetail(null);
  }, []);

  const loadOps = useCallback(async () => {
    const [queue, kb, evals] = await Promise.all([
      api<Approval[]>("approvals"),
      api<KnowledgeStatus>("knowledge/status"),
      api<Evaluation[]>("evaluations"),
    ]);
    setApprovals(queue);
    setKnowledge(kb);
    setEvaluations(evals);
  }, []);

  async function enter(nextPersona: Persona) {
    setBusy(true);
    setNotice(null);
    try {
      await api("auth/demo-login", {
        method: "POST",
        body: JSON.stringify({ principal_id: nextPersona.id, role: nextPersona.role }),
      });
      setPersona(nextPersona);
      setAuthenticated(true);
      setRunDetail(null);
      setStreamState("idle");
      // Keep the demo landing state clean even when load tests created large historical sessions.
      // The user can explicitly open an old conversation from the sidebar when it is useful.
      if (nextPersona.role === "customer") await loadSessions(null);
      else await loadOps();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法进入演示");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    await fetch("/api/logout", { method: "POST" });
    setAuthenticated(false);
    setSessions([]);
    setMessages([]);
    setActiveSession(null);
    setRunDetail(null);
    setNotice(null);
  }

  async function createSession() {
    setBusy(true);
    setNotice(null);
    try {
      const created = await api<Session>("sessions", { method: "POST", body: "{}" });
      await loadSessions(created.session_id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建会话失败");
    } finally {
      setBusy(false);
    }
  }

  async function openSession(id: string) {
    setActiveSession(id);
    setRunDetail(null);
    setNotice(null);
    try {
      const detail = await api<Session & { messages: Message[] }>(`sessions/${id}`);
      setMessages(detail.messages);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "读取会话失败");
    }
  }

  function consumeEvent(eventType: string, payload: Record<string, unknown>) {
    if (eventType === "run_started") setStreamState("running");
    if (eventType === "message_completed") {
      setMessages((current) => [...current, { role: "assistant", content: String(payload.answer ?? "") }]);
      setRunDetail({
        status: String(payload.status ?? "completed"),
        route: typeof payload.route === "string" ? payload.route : null,
        products: Array.isArray(payload.products) ? (payload.products as Product[]) : [],
        citations: Array.isArray(payload.citations) ? (payload.citations as Citation[]) : [],
        tool_trace: Array.isArray(payload.tool_trace) ? (payload.tool_trace as ToolTrace[]) : [],
      });
    }
    if (eventType === "completed") setStreamState("done");
    if (eventType === "error") {
      setStreamState("error");
      setNotice(String(payload.message ?? "Agent 执行失败"));
    }
  }

  async function sendMessage(event?: FormEvent, starter?: string) {
    event?.preventDefault();
    const text = (starter ?? input).trim();
    if (!text || streamState === "connecting" || streamState === "running") return;
    let sessionId = activeSession;
    setNotice(null);
    setInput("");
    setRunDetail(null);
    setMessages((current) => [...current, { role: "user", content: text }]);
    setStreamState("connecting");
    try {
      if (!sessionId) {
        const created = await api<Session>("sessions", { method: "POST", body: "{}" });
        sessionId = created.session_id;
        setActiveSession(sessionId);
        setSessions((current) => [created, ...current]);
      }
      const requestId = `req_web_${crypto.randomUUID().replaceAll("-", "")}`;
      const response = await fetch("/api/backend/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ session_id: sessionId, text, request_id: requestId }),
      });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload.detail === "string" ? payload.detail : `流式请求失败 (${response.status})`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          let eventType = "message";
          let data = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) eventType = line.slice(6).trim();
            if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (data) consumeEvent(eventType, JSON.parse(data) as Record<string, unknown>);
        }
        if (done) break;
      }
      await loadSessions(sessionId, true);
    } catch (error) {
      setStreamState("error");
      setNotice(error instanceof Error ? error.message : "发送失败");
    }
  }

  async function decide(approval: Approval, decision: "approved" | "rejected" | "needs_more_info") {
    setBusy(true);
    setNotice(null);
    try {
      const reason = notes[approval.approval_id]?.trim() || (decision === "approved" ? "信息核验通过，同意执行。" : decision === "rejected" ? "人工复核未通过。" : "需要补充凭证后重新提交。");
      await api(`approvals/${approval.approval_id}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision, state_version: approval.state_version, reason }),
      });
      await api(`approvals/${approval.approval_id}/resume`, { method: "POST", body: "{}" });
      setNotice(`审批 ${compactId(approval.approval_id)} 已${decision === "approved" ? "通过" : decision === "rejected" ? "拒绝" : "退回"}`);
      await loadOps();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "审批提交失败");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, streamState]);

  if (!authenticated) {
    return <PersonaGate selected={persona} busy={busy} notice={notice} onSelect={setPersona} onEnter={() => enter(persona)} />;
  }

  return (
    <main className="app-shell">
      <Header persona={persona} onSwitch={() => setAuthenticated(false)} onLogout={logout} />
      {notice && (
        <div className={`toast ${streamState === "error" ? "error" : ""}`} role="status">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)} aria-label="关闭提示"><Icon name="x" size={15} /></button>
        </div>
      )}
      {isCustomer ? (
        <CustomerWorkspace
          persona={persona}
          sessions={sessions}
          activeSession={activeSession}
          activeSessionData={activeSessionData}
          messages={messages}
          input={input}
          streamState={streamState}
          runDetail={runDetail}
          busy={busy}
          bottomRef={bottomRef}
          onCreate={createSession}
          onOpen={openSession}
          onInput={setInput}
          onSend={sendMessage}
        />
      ) : (
        <OperationsWorkspace
          persona={persona}
          approvals={approvals}
          knowledge={knowledge}
          evaluations={evaluations}
          notes={notes}
          busy={busy}
          onNotes={setNotes}
          onDecide={decide}
          onRefresh={loadOps}
        />
      )}
    </main>
  );
}

function PersonaGate({ selected, busy, notice, onSelect, onEnter }: { selected: Persona; busy: boolean; notice: string | null; onSelect: (persona: Persona) => void; onEnter: () => void }) {
  return (
    <main className="gate">
      <div className="gate-orb orb-one" />
      <div className="gate-orb orb-two" />
      <section className="gate-card">
        <div className="gate-brand"><span className="brand-mark"><Icon name="spark" size={20} /></span><span>COMMERCE AGENT</span><em>DEMO</em></div>
        <div className="gate-copy">
          <p className="eyebrow">可追溯 · 可审批 · 可恢复</p>
          <h1>选择一个视角，<br />进入真实业务流程。</h1>
          <p>所有页面连接本地 FastAPI、MySQL 与 Redis。这里展示决策结果和工具摘要，不展示模型隐藏推理。</p>
        </div>
        <div className="persona-grid">
          {PERSONAS.map((item) => (
            <button key={`${item.role}-${item.id}`} className={`persona-card ${selected.id === item.id ? "selected" : ""}`} onClick={() => onSelect(item)}>
              <span className={`avatar ${item.accent}`}>{item.initials}</span>
              <span><strong>{item.name}</strong><small>{item.subtitle}</small></span>
              <span className="radio-dot" />
            </button>
          ))}
        </div>
        {notice && <p className="gate-error">{notice}</p>}
        <button className="primary gate-enter" onClick={onEnter} disabled={busy}>
          {busy ? <><span className="spinner" />正在连接服务</> : <>进入 {selected.name} 视角<Icon name="arrow" size={17} /></>}
        </button>
        <div className="gate-foot"><span><i className="live-dot" />本地演示环境</span><span>DeepSeek API · LangGraph · MCP</span></div>
      </section>
    </main>
  );
}

function Header({ persona, onSwitch, onLogout }: { persona: Persona; onSwitch: () => void; onLogout: () => void }) {
  return (
    <header className="topbar">
      <div className="brand"><span className="brand-mark"><Icon name="spark" size={18} /></span><span><b>Commerce Agent</b><small>智能客服演示控制台</small></span></div>
      <div className="environment"><i className="live-dot" />服务已连接<span>DEMO · P8</span></div>
      <div className="identity">
        <span className={`avatar small ${persona.accent}`}>{persona.initials}</span>
        <span><b>{persona.name}</b><small>{persona.role === "customer" ? "演示用户" : persona.role === "agent" ? "人工客服" : "系统管理员"}</small></span>
        <button className="text-button" onClick={onSwitch}>切换视角</button>
        <button className="icon-button" onClick={onLogout} title="退出"><Icon name="logout" /></button>
      </div>
    </header>
  );
}

function CustomerWorkspace(props: {
  persona: Persona; sessions: Session[]; activeSession: string | null; activeSessionData?: Session; messages: Message[]; input: string;
  streamState: string; runDetail: RunDetail | null; busy: boolean; bottomRef: React.RefObject<HTMLDivElement | null>;
  onCreate: () => void; onOpen: (id: string) => void; onInput: (value: string) => void; onSend: (event?: FormEvent, starter?: string) => void;
}) {
  const { sessions, activeSession, activeSessionData, messages, input, streamState, runDetail, busy, bottomRef, onCreate, onOpen, onInput, onSend } = props;
  return (
    <div className="customer-layout">
      <aside className="session-panel panel">
        <div className="panel-heading"><div><span className="section-kicker">WORKSPACE</span><h2>会话</h2></div><button className="icon-button dark" onClick={onCreate} disabled={busy} title="新建会话"><Icon name="plus" /></button></div>
        <div className="session-list">
          {sessions.length === 0 && <EmptyMini icon="chat" text="还没有会话" />}
          {sessions.map((session, index) => (
            <button key={session.session_id} className={`session-item ${activeSession === session.session_id ? "active" : ""}`} onClick={() => onOpen(session.session_id)}>
              <span className="session-icon"><Icon name="chat" size={16} /></span>
              <span><b>{index === 0 ? "最近咨询" : `历史会话 ${sessions.length - index}`}</b><small>{compactId(session.session_id)}</small></span>
              <time>{formatTime(session.updated_at)}</time>
            </button>
          ))}
        </div>
        <div className="session-footer"><Icon name="shield" size={16} /><span>账号隔离已启用<br /><small>只能访问当前用户会话</small></span></div>
      </aside>

      <section className="chat-panel panel">
        <div className="chat-heading">
          <div><span className="section-kicker">CONVERSATION</span><h2>{activeSession ? "智能客服会话" : "开始一次新咨询"}</h2></div>
          <div className={`run-state ${streamState}`}><i />{streamState === "running" ? "Agent 执行中" : streamState === "connecting" ? "正在建立连接" : streamState === "error" ? "执行异常" : "准备就绪"}</div>
        </div>
        <div className="messages">
          {messages.length === 0 && (
            <div className="welcome">
              <span className="welcome-mark"><Icon name="spark" size={28} /></span>
              <h3>你好，我是电商智能客服</h3>
              <p>可以帮你查商品、比参数、看订单物流，也能发起需要人工确认的售后申请。</p>
              <div className="starter-list">
                {STARTERS.map((starter) => <button key={starter} onClick={() => onSend(undefined, starter)}><span>{starter}</span><Icon name="arrow" size={15} /></button>)}
              </div>
            </div>
          )}
          {messages.map((message, index) => (
            <div key={`${message.role}-${index}`} className={`message-row ${message.role === "user" ? "user" : "assistant"}`}>
              {message.role !== "user" && <span className="message-avatar"><Icon name="spark" size={16} /></span>}
              <div className="message-bubble"><p>{message.content}</p><small>{message.created_at ? formatTime(message.created_at) : message.role === "user" ? "刚刚" : "Agent 回答"}</small></div>
            </div>
          ))}
          {(streamState === "connecting" || streamState === "running") && <div className="message-row assistant"><span className="message-avatar"><Icon name="spark" size={16} /></span><div className="thinking"><i /><i /><i /><span>{streamState === "connecting" ? "连接事件流" : "正在检索与调用工具"}</span></div></div>}
          {runDetail && runDetail.products.length > 0 && <ProductStrip products={runDetail.products} />}
          <div ref={bottomRef} />
        </div>
        <form className="composer" onSubmit={onSend}>
          <textarea value={input} onChange={(event) => onInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); onSend(); } }} placeholder="输入商品、订单或售后问题…" maxLength={4000} />
          <div className="composer-meta"><span>Enter 发送 · Shift + Enter 换行</span><span>{input.length}/4000</span></div>
          <button className="send-button" disabled={!input.trim() || streamState === "running" || streamState === "connecting"} aria-label="发送"><Icon name="send" size={19} /></button>
        </form>
      </section>

      <aside className="inspector panel">
        <div className="panel-heading"><div><span className="section-kicker">TRACE INSPECTOR</span><h2>执行证据</h2></div></div>
        <div className="inspector-summary">
          <div><span>路由</span><b>{runDetail?.route ?? "等待请求"}</b></div>
          <div><span>状态</span><b className={runDetail?.status === "completed" ? "good" : ""}>{runDetail?.status ?? "idle"}</b></div>
          <div><span>图版本</span><b>{activeSessionData?.graph_version ?? "—"}</b></div>
        </div>
        <TraceSection title="工具调用" icon="tool" count={runDetail?.tool_trace.length ?? 0}>
          {!runDetail?.tool_trace.length ? <EmptyMini icon="tool" text="本轮暂无工具调用" /> : runDetail.tool_trace.map((tool, index) => <div className="tool-row" key={`${tool.tool_name}-${index}`}><span className={`status-icon ${tool.ok ? "success" : "failed"}`}>{tool.ok ? <Icon name="check" size={13} /> : <Icon name="x" size={13} />}</span><span><b>{tool.tool_name ?? "unknown"}</b><small>{tool.error_code ?? (tool.ok ? "调用成功" : "调用失败")}</small></span><em>{tool.duration_ms ?? 0} ms</em></div>)}
        </TraceSection>
        <TraceSection title="RAG 引用" icon="quote" count={runDetail?.citations.length ?? 0}>
          {!runDetail?.citations.length ? <EmptyMini icon="quote" text="本轮暂无知识引用" /> : runDetail.citations.map((citation, index) => <details className="citation" key={String(citation.chunk_id ?? index)}><summary><span>{String(citation.title ?? "知识来源")}</span><Icon name="arrow" size={14} /></summary><dl><div><dt>文档</dt><dd>{valueText(citation.doc_id)}</dd></div><div><dt>知识版本</dt><dd>{valueText(citation.knowledge_version)}</dd></div><div><dt>Chunk</dt><dd>{valueText(citation.chunk_id)}</dd></div><div><dt>生效时间</dt><dd>{formatTime(String(citation.effective_from ?? ""))}</dd></div></dl></details>)}
        </TraceSection>
        <div className="privacy-note"><Icon name="shield" size={17} /><span><b>可解释，但不暴露思维链</b><small>仅展示结构化工具结果、引用与耗时。</small></span></div>
      </aside>
    </div>
  );
}

function ProductStrip({ products }: { products: Product[] }) {
  return <div className="product-strip"><div className="inline-label"><Icon name="box" size={15} />候选商品 · {products.length}</div><div className="product-grid">{products.slice(0, 5).map((product, index) => <article className="product-card" key={String(product.product_id ?? index)}><div className="product-visual"><Icon name="box" size={28} /><span>#{index + 1}</span></div><div><small>{valueText(product.brand)} · {valueText(product.category)}</small><h4>{valueText(product.name)}</h4><div className="product-price"><b>¥{valueText(product.price)}</b><span>★ {valueText(product.rating)}</span></div><p>{Array.isArray(product.match_reasons) ? product.match_reasons.join(" · ") : "Agent 候选集"}</p></div></article>)}</div></div>;
}

function TraceSection({ title, icon, count, children }: { title: string; icon: string; count: number; children: ReactNode }) {
  return <section className="trace-section"><div className="trace-title"><span><Icon name={icon} size={16} />{title}</span><em>{count}</em></div>{children}</section>;
}

function EmptyMini({ icon, text }: { icon: string; text: string }) {
  return <div className="empty-mini"><Icon name={icon} size={18} /><span>{text}</span></div>;
}

function OperationsWorkspace({ persona, approvals, knowledge, evaluations, notes, busy, onNotes, onDecide, onRefresh }: {
  persona: Persona; approvals: Approval[]; knowledge: KnowledgeStatus | null; evaluations: Evaluation[]; notes: Record<string, string>; busy: boolean;
  onNotes: (value: Record<string, string>) => void; onDecide: (approval: Approval, decision: "approved" | "rejected" | "needs_more_info") => void; onRefresh: () => Promise<void>;
}) {
  const latest = evaluations[0];
  const passRate = latest?.result_count ? Math.round((latest.passed_count / latest.result_count) * 1000) / 10 : null;
  const metrics = useMemo(() => Object.entries(latest?.metrics ?? {}).slice(0, 6), [latest]);
  return (
    <div className="ops-layout">
      <section className="ops-main">
        <div className="ops-hero">
          <div><span className="section-kicker">HUMAN IN THE LOOP</span><h1>{persona.role === "agent" ? "人工审批工作台" : "运营控制台"}</h1><p>高风险售后动作必须由人工提交决策，并在恢复执行前重新校验业务状态。</p></div>
          <button className="secondary" onClick={() => onRefresh()} disabled={busy}><Icon name="rotate" size={16} />刷新数据</button>
        </div>
        <div className="stat-row">
          <Stat label="待处理审批" value={String(approvals.length)} note="MySQL 乐观锁保护" tone="amber" />
          <Stat label="知识版本" value={knowledge?.active_version ?? knowledge?.latest_version ?? "未初始化"} note={knowledge?.activated_at ? `激活于 ${formatTime(knowledge.activated_at)}` : `当前状态：${knowledge?.status ?? "loading"}`} tone="mint" />
          <Stat label="最近评测通过率" value={passRate === null ? "暂无" : `${passRate}%`} note={latest ? `${latest.passed_count}/${latest.result_count} 条通过` : "等待评测入库"} tone="violet" />
        </div>
        <div className="queue-heading"><div><span className="section-kicker">APPROVAL QUEUE</span><h2>待人工决策</h2></div><span>{Math.min(approvals.length, 20)} / {approvals.length} 项</span></div>
        <div className="approval-list">
          {approvals.length === 0 && <div className="empty-queue"><span><Icon name="check" size={26} /></span><h3>审批队列已清空</h3><p>新的售后申请进入中断点后会显示在这里。</p></div>}
          {approvals.slice(0, 20).map((approval) => (
            <article className="approval-card" key={approval.approval_id}>
              <div className="approval-top"><span className="approval-badge"><Icon name="shield" size={15} />等待人工审批</span><time><Icon name="clock" size={14} />{approval.expires_at ? `截止 ${formatTime(approval.expires_at)}` : "无到期时间"}</time></div>
              <div className="approval-content">
                <div><span>动作类型</span><b>{approval.action_type}</b></div><div><span>申请用户</span><b>{approval.principal_id}</b></div><div><span>售后单</span><b>{approval.ticket_id}</b></div><div><span>金额快照</span><b>{approval.amount_snapshot ? `¥${approval.amount_snapshot}` : "—"}</b></div>
              </div>
              <div className="approval-ids"><span>Approval</span><code>{approval.approval_id}</code><span>State</span><code>v{approval.state_version}</code></div>
              <label className="decision-note"><span>处理说明</span><input value={notes[approval.approval_id] ?? ""} onChange={(event) => onNotes({ ...notes, [approval.approval_id]: event.target.value })} placeholder="填写复核依据（未填则使用标准说明）" maxLength={500} /></label>
              <div className="approval-actions"><button className="approve" onClick={() => onDecide(approval, "approved")} disabled={busy}><Icon name="check" size={16} />通过并恢复</button><button className="return" onClick={() => onDecide(approval, "needs_more_info")} disabled={busy}><Icon name="rotate" size={16} />退回补充</button><button className="reject" onClick={() => onDecide(approval, "rejected")} disabled={busy}><Icon name="x" size={16} />拒绝</button></div>
            </article>
          ))}
        </div>
      </section>
      <aside className="ops-side">
        <section className="side-card"><div className="side-title"><span><Icon name="book" size={17} />知识库状态</span><em className={knowledge?.status === "active" ? "active-pill" : "neutral-pill"}>{knowledge?.status ?? "loading"}</em></div><div className="version-block"><small>{knowledge?.active_version ? "ACTIVE VERSION" : "LATEST VERSION"}</small><b>{knowledge?.active_version ?? knowledge?.latest_version ?? "—"}</b><span>{knowledge?.activated_at ? formatTime(knowledge.activated_at) : "尚未激活"}</span></div><div className="mini-list">{(knowledge?.recent_jobs ?? []).slice(0, 3).map((job, index) => <div key={String(job.job_id ?? index)}><span><i className={`job-dot ${job.status === "completed" ? "done" : ""}`} />{valueText(job.target_version)}</span><em>{valueText(job.status)}</em></div>)}{!knowledge?.recent_jobs.length && <EmptyMini icon="book" text="暂无重建任务" />}</div></section>
        <section className="side-card"><div className="side-title"><span><Icon name="chart" size={17} />最近评测</span><em className={latest?.status === "completed" ? "active-pill" : "neutral-pill"}>{latest?.status ?? "none"}</em></div>{latest ? <><div className="eval-head"><small>{latest.name}</small><b>{passRate ?? 0}<sup>%</sup></b><span>{latest.run_id}</span></div><div className="metric-list">{metrics.map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><b title={valueText(value)}>{metricText(value)}</b></div>)}</div><div className="eval-foot"><span>{latest.result_count} cases</span><span>{formatTime(latest.completed_at ?? latest.created_at)}</span></div></> : <EmptyMini icon="chart" text="暂无评测记录" />}</section>
        <section className="side-card integrity"><Icon name="shield" size={22} /><div><b>审批一致性保护</b><p>决策提交使用 state_version 乐观锁；恢复前重新验证订单和售后状态，重复恢复不会产生重复业务动作。</p></div></section>
      </aside>
    </div>
  );
}

function Stat({ label, value, note, tone }: { label: string; value: string; note: string; tone: string }) {
  return <div className={`stat ${tone}`}><span>{label}</span><b>{value}</b><small>{note}</small></div>;
}
