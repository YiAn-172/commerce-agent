import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

const API_BASE = (process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const TOKEN_COOKIE = "commerce_agent_token";

const allowedRoutes: Array<{ method: string; pattern: RegExp }> = [
  { method: "POST", pattern: /^auth\/demo-login$/ },
  { method: "GET", pattern: /^sessions$/ },
  { method: "POST", pattern: /^sessions$/ },
  { method: "GET", pattern: /^sessions\/ses_[A-Za-z0-9_-]{8,64}$/ },
  { method: "POST", pattern: /^chat\/stream$/ },
  { method: "GET", pattern: /^approvals$/ },
  { method: "POST", pattern: /^approvals\/[A-Za-z0-9_-]{3,64}\/decision$/ },
  { method: "POST", pattern: /^approvals\/[A-Za-z0-9_-]{3,64}\/resume$/ },
  { method: "GET", pattern: /^knowledge\/status$/ },
  { method: "GET", pattern: /^evaluations$/ },
];

function isAllowed(method: string, path: string) {
  return allowedRoutes.some((entry) => entry.method === method && entry.pattern.test(path));
}

async function relay(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path: parts } = await context.params;
  const path = parts.join("/");
  if (!isAllowed(request.method, path)) {
    return NextResponse.json({ detail: "route not available through demo gateway" }, { status: 404 });
  }

  const login = path === "auth/demo-login";
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  if (!login && !token) {
    return NextResponse.json({ detail: "demo session expired" }, { status: 401 });
  }

  const headers = new Headers({ Accept: request.headers.get("accept") ?? "application/json" });
  if (!login && token) headers.set("Authorization", `Bearer ${token}`);
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId) headers.set("Last-Event-ID", lastEventId);

  let body: string | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.text();
    headers.set("Content-Type", request.headers.get("content-type") ?? "application/json");
  }

  try {
    const upstream = await fetch(`${API_BASE}/api/v1/${path}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: request.signal,
    });
    const contentType = upstream.headers.get("content-type") ?? "application/json";

    if (login) {
      const payload = await upstream.json();
      if (!upstream.ok) return NextResponse.json(payload, { status: upstream.status });
      const response = NextResponse.json({ expires_at: payload.expires_at });
      response.cookies.set(TOKEN_COOKIE, payload.access_token, {
        httpOnly: true,
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production" && process.env.COOKIE_SECURE === "true",
        path: "/",
        maxAge: 8 * 60 * 60,
      });
      return response;
    }

    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": contentType.includes("text/event-stream") ? "no-cache" : "no-store",
        ...(contentType.includes("text/event-stream") ? { "X-Accel-Buffering": "no" } : {}),
      },
    });
  } catch (error) {
    return NextResponse.json(
      { detail: "backend unavailable", error_type: error instanceof Error ? error.name : "Error" },
      { status: 503 },
    );
  }
}

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const GET = relay;
export const POST = relay;
