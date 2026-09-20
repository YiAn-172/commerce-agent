from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.auth import (
    DemoLoginRequest,
    Principal,
    issue_token,
    require_scope,
)
from apps.api.schemas import (
    AfterSalesConfirmRequest,
    ApprovalDecisionBody,
    ChatRequest,
    ChatResponse,
    KnowledgeDocumentRequest,
    KnowledgeReindexRequest,
    MessageResponse,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionResponse,
    TokenResponse,
)
from apps.api.services import ApplicationServices
from apps.api.settings import Settings, get_settings
from packages.agent_core.gateways import McpToolGateway
from packages.agent_core.graph import GRAPH_VERSION
from packages.api_core.events import EventStore, RedisEventStore, encode_sse
from packages.approval_core.contracts import ApprovalDecision, ApprovalResume
from packages.approval_core.service import ApprovalNotFound, ApprovalStateConflict
from packages.business.models import (
    ChatMessage,
    ChatSession,
    EvaluationResult,
    EvaluationRun,
    KnowledgeDocument,
    KnowledgeReindexJob,
    KnowledgeVersion,
    User,
)

router = APIRouter(prefix="/api/v1")


def get_services(request: Request) -> ApplicationServices:
    return cast(ApplicationServices, request.app.state.services)


def get_event_store(request: Request) -> EventStore:
    store: RedisEventStore = request.app.state.event_store
    return store


def require_session_access(session: ChatSession, principal: Principal) -> None:
    if principal.role == "customer" and session.user_id != principal.principal_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")


def session_response(session: ChatSession) -> SessionResponse:
    return SessionResponse(
        session_id=session.id,
        user_id=session.user_id,
        state_version=session.state_version,
        graph_version=session.graph_version,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.post("/auth/demo-login", response_model=TokenResponse, tags=["auth"])
async def demo_login(
    body: DemoLoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    if not settings.demo_auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="demo login disabled")
    token, expires_at = issue_token(body, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/sessions", response_model=SessionResponse, status_code=201, tags=["sessions"])
async def create_session(
    body: SessionCreateRequest,
    principal: Annotated[Principal, Depends(require_scope("session:create"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> SessionResponse:
    now = datetime.now(UTC).replace(tzinfo=None)
    session_id = body.session_id or f"ses_{uuid4().hex}"
    async with services.factory() as database:
        if await database.get(User, principal.principal_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
        chat = ChatSession(
            id=session_id,
            user_id=principal.principal_id,
            state_version=1,
            graph_version=GRAPH_VERSION,
            created_at=now,
            updated_at=now,
        )
        database.add(chat)
        try:
            await database.commit()
        except IntegrityError as error:
            await database.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="session exists",
            ) from error
        return session_response(chat)


@router.get("/sessions", response_model=list[SessionResponse], tags=["sessions"])
async def list_sessions(
    principal: Annotated[Principal, Depends(require_scope("session:read"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> list[SessionResponse]:
    async with services.factory() as database:
        statement = select(ChatSession).order_by(ChatSession.updated_at.desc()).limit(50)
        if principal.role == "customer":
            statement = statement.where(ChatSession.user_id == principal.principal_id)
        rows = list((await database.scalars(statement)).all())
        return [session_response(item) for item in rows]


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse, tags=["sessions"])
async def get_session(
    session_id: str,
    principal: Annotated[Principal, Depends(require_scope("session:read"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> SessionDetailResponse:
    async with services.factory() as database:
        chat = await database.get(ChatSession, session_id)
        if chat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        require_session_access(chat, principal)
        rows = list(
            (
                await database.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at)
                )
            ).all()
        )
        return SessionDetailResponse(
            **session_response(chat).model_dump(),
            messages=[
                MessageResponse(
                    role=item.role,
                    content=item.content_redacted,
                    created_at=item.created_at,
                )
                for item in rows
            ],
        )


async def checked_session(
    services: ApplicationServices,
    principal: Principal,
    session_id: str,
) -> ChatSession:
    async with services.factory() as database:
        chat = await database.get(ChatSession, session_id)
        if chat is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        require_session_access(chat, principal)
        return chat


@router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude={"cache_status"},
    tags=["chat"],
)
async def chat(
    body: ChatRequest,
    response: Response,
    principal: Annotated[Principal, Depends(require_scope("chat:write"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> ChatResponse:
    session = await checked_session(services, principal, body.session_id)
    result = await services.chat.run(
        body,
        principal_id=principal.principal_id,
        state_version=session.state_version,
    )
    response.headers["X-Cache"] = result.cache_status
    return result


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(
    body: ChatRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_scope("chat:write"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
    events: Annotated[EventStore, Depends(get_event_store)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    session = await checked_session(services, principal, body.session_id)
    heartbeat = get_settings().sse_heartbeat_seconds

    async def generate() -> Any:
        if last_event_id:
            for stored in await events.history(body.session_id, after_event_id=last_event_id):
                yield encode_sse(stored)
            return
        run_id = f"run_stream_{uuid4().hex}"
        started = await events.append(
            session_id=body.session_id,
            run_id=run_id,
            event_type="run_started",
            payload={"request_id": body.request_id},
        )
        yield encode_sse(started)
        task = asyncio.create_task(
            services.chat.run(
                body,
                principal_id=principal.principal_id,
                state_version=session.state_version,
            )
        )
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=heartbeat)
            except TimeoutError:
                if await request.is_disconnected():
                    task.cancel()
                    return
                yield ": heartbeat\n\n"
        try:
            result = task.result()
            message = await events.append(
                session_id=body.session_id,
                run_id=run_id,
                event_type="message_completed",
                payload={
                    "status": result.status,
                    "answer": result.answer,
                    "route": result.route,
                    "citations": result.citations,
                    "products": result.products,
                    "tool_trace": result.tool_trace,
                },
            )
            yield encode_sse(message)
            completed = await events.append(
                session_id=body.session_id,
                run_id=run_id,
                event_type="completed",
                payload={"request_id": result.request_id, "status": result.status},
            )
            yield encode_sse(completed)
        except Exception as error:
            failed = await events.append(
                session_id=body.session_id,
                run_id=run_id,
                event_type="error",
                payload={"error_type": type(error).__name__, "message": "请求执行失败。"},
            )
            yield encode_sse(failed)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/after-sales/confirm", tags=["approvals"])
async def confirm_after_sales(
    body: AfterSalesConfirmRequest,
    principal: Annotated[Principal, Depends(require_scope("after-sales:confirm"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> dict[str, Any]:
    await checked_session(services, principal, body.session_id)
    envelope = await McpToolGateway().call(
        "prefill_service_ticket",
        body.model_dump(exclude={"session_id"}, mode="json"),
        principal_id=principal.principal_id,
        trace_id=f"tr_confirm_{uuid4().hex}",
        request_id=f"req_confirm_{uuid4().hex}",
        deadline_ms=10_000,
    )
    data = envelope.get("data") if envelope.get("ok") else None
    if not isinstance(data, dict):
        error = envelope.get("error")
        message = error.get("message") if isinstance(error, dict) else "创建售后草稿失败。"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    async with services.approval_runner() as workflow:
        snapshot = await workflow.start(
            str(data["approval_id"]),
            session_id=body.session_id,
            expected_state_version=int(data["approval_state_version"]),
        )
    return snapshot.model_dump(mode="json")


@router.get("/approvals", tags=["approvals"])
async def list_approvals(
    _: Annotated[Principal, Depends(require_scope("approval:read"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in await services.approvals.list_pending()]


@router.post("/approvals/{approval_id}/decision", tags=["approvals"])
async def decide_approval(
    approval_id: str,
    body: ApprovalDecisionBody,
    principal: Annotated[Principal, Depends(require_scope("approval:decide"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> dict[str, Any]:
    try:
        snapshot = await services.approvals.decide(
            approval_id,
            decision=body.decision,
            reason=body.reason,
            decided_by=principal.principal_id,
            expected_state_version=body.state_version,
        )
        return snapshot.model_dump(mode="json")
    except ApprovalNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ApprovalStateConflict, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/approvals/{approval_id}/resume", tags=["approvals"])
async def resume_approval(
    approval_id: str,
    _: Annotated[Principal, Depends(require_scope("approval:resume"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> dict[str, Any]:
    try:
        snapshot = await services.approvals.get(approval_id)
        if snapshot.status == "pending_human_approval" or snapshot.decided_by is None:
            raise ApprovalStateConflict("approval has no committed decision")
        async with services.approval_runner() as workflow:
            result = await workflow.resume(
                approval_id,
                ApprovalResume(
                    decision=ApprovalDecision(snapshot.status),
                    state_version=snapshot.state_version,
                    decided_by=snapshot.decided_by,
                ),
            )
        return result.model_dump(mode="json")
    except ApprovalNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ApprovalStateConflict as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/knowledge/documents", status_code=201, tags=["knowledge"])
async def create_knowledge_document(
    body: KnowledgeDocumentRequest,
    principal: Annotated[Principal, Depends(require_scope("knowledge:write"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> dict[str, Any]:
    digest = hashlib.sha256(body.content.encode("utf-8")).hexdigest()
    now = datetime.now(UTC).replace(tzinfo=None)
    async with services.factory() as database:
        existing = await database.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.content_sha256 == digest)
        )
        if existing is not None:
            return {
                "document_id": existing.id,
                "content_sha256": existing.content_sha256,
                "status": existing.status,
                "duplicate": True,
            }
        document = KnowledgeDocument(
            id=f"kdoc_{uuid4().hex}",
            document_type=body.document_type,
            title=body.title,
            source_uri=body.source_uri,
            content_sha256=digest,
            content_text=body.content,
            created_by=principal.principal_id,
            status="draft",
            created_at=now,
            updated_at=now,
        )
        database.add(document)
        await database.commit()
        return {
            "document_id": document.id,
            "content_sha256": document.content_sha256,
            "status": document.status,
            "duplicate": False,
        }


@router.post("/knowledge/reindex", status_code=202, tags=["knowledge"])
async def create_reindex_job(
    body: KnowledgeReindexRequest,
    principal: Annotated[Principal, Depends(require_scope("knowledge:write"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with services.factory() as database:
        if body.document_ids:
            found = set(
                (
                    await database.scalars(
                        select(KnowledgeDocument.id).where(
                            KnowledgeDocument.id.in_(body.document_ids)
                        )
                    )
                ).all()
            )
            missing = sorted(set(body.document_ids) - found)
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={"missing_document_ids": missing[:20]},
                )
        job = KnowledgeReindexJob(
            id=f"kjob_{uuid4().hex}",
            requested_by=principal.principal_id,
            document_ids=body.document_ids,
            target_version=body.target_version,
            status="queued",
            error_summary=None,
            created_at=now,
            started_at=None,
            completed_at=None,
        )
        database.add(job)
        await database.commit()
        return {
            "job_id": job.id,
            "status": job.status,
            "target_version": job.target_version,
            "document_count": len(job.document_ids),
        }


@router.get("/knowledge/status", tags=["knowledge"])
async def get_knowledge_status(
    _: Annotated[Principal, Depends(require_scope("knowledge:read"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> dict[str, Any]:
    async with services.factory() as database:
        latest = await database.scalar(
            select(KnowledgeVersion).order_by(KnowledgeVersion.created_at.desc()).limit(1)
        )
        active = await database.scalar(
            select(KnowledgeVersion)
            .where(KnowledgeVersion.status == "active")
            .order_by(KnowledgeVersion.activated_at.desc())
            .limit(1)
        )
        jobs = list(
            (
                await database.scalars(
                    select(KnowledgeReindexJob)
                    .order_by(KnowledgeReindexJob.created_at.desc())
                    .limit(5)
                )
            ).all()
        )
        return {
            "active_version": active.version if active is not None else None,
            "latest_version": latest.version if latest is not None else None,
            "status": (
                active.status
                if active is not None
                else latest.status
                if latest is not None
                else "not_initialized"
            ),
            "activated_at": active.activated_at if active is not None else None,
            "recent_jobs": [
                {
                    "job_id": item.id,
                    "target_version": item.target_version,
                    "status": item.status,
                    "document_count": len(item.document_ids),
                    "created_at": item.created_at,
                    "error_summary": item.error_summary,
                }
                for item in jobs
            ],
        }


@router.get("/evaluations", tags=["evaluations"])
async def list_evaluations(
    _: Annotated[Principal, Depends(require_scope("evaluation:read"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> list[dict[str, Any]]:
    async with services.factory() as database:
        runs = list(
            (
                await database.scalars(
                    select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(5)
                )
            ).all()
        )
        output: list[dict[str, Any]] = []
        for run in runs:
            results = list(
                (
                    await database.scalars(
                        select(EvaluationResult).where(EvaluationResult.evaluation_run_id == run.id)
                    )
                ).all()
            )
            output.append(
                {
                    "run_id": run.id,
                    "name": run.name,
                    "status": run.status,
                    "metrics": run.metrics_json,
                    "result_count": len(results),
                    "passed_count": sum(1 for item in results if item.passed),
                    "created_at": run.created_at,
                    "completed_at": run.completed_at,
                }
            )
        return output


@router.get("/evaluations/{run_id}", tags=["evaluations"])
async def get_evaluation(
    run_id: str,
    _: Annotated[Principal, Depends(require_scope("evaluation:read"))],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> dict[str, Any]:
    async with services.factory() as database:
        run = await database.get(EvaluationRun, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        results = list(
            (
                await database.scalars(
                    select(EvaluationResult).where(EvaluationResult.evaluation_run_id == run_id)
                )
            ).all()
        )
        return {
            "run_id": run.id,
            "name": run.name,
            "status": run.status,
            "manifest_sha256": run.manifest_sha256,
            "metrics": run.metrics_json,
            "result_count": len(results),
            "passed_count": sum(1 for item in results if item.passed),
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }
