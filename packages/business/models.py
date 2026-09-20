from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    external_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    masked_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    brand: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    usage_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    audience_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    sales_count: Mapped[int] = mapped_column(Integer, nullable=False)
    data_version: Mapped[str] = mapped_column(String(40), nullable=False)


class Sku(Base, TimestampMixin):
    __tablename__ = "skus"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sku_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    color: Mapped[str] = mapped_column(String(40), nullable=False)
    specifications_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    price_valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("sku_id", "region", name="uq_inventory_sku_region"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    sku_id: Mapped[str] = mapped_column(
        ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    region: Mapped[str] = mapped_column(String(40), nullable=False)
    warehouse: Mapped[str] = mapped_column(String(80), nullable=False)
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (Index("ix_order_items_order", "order_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    sku_id: Mapped[str] = mapped_column(ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False)
    name_snapshot: Mapped[str] = mapped_column(String(180), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    subtotal_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_condition: Mapped[str] = mapped_column(String(32), nullable=False, default="unopened")


class LogisticsEvent(Base):
    __tablename__ = "logistics_events"
    __table_args__ = (UniqueConstraint("order_id", "sequence", name="uq_logistics_order_sequence"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    carrier: Mapped[str] = mapped_column(String(40), nullable=False)
    tracking_number_masked: Mapped[str] = mapped_column(String(48), nullable=False)
    status_code: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(240), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AfterSalesRule(Base):
    __tablename__ = "after_sales_rules"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_conditions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    excluded_reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    required_materials: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ServiceTicket(Base):
    __tablename__ = "service_tickets"
    __table_args__ = (
        UniqueConstraint("principal_id", "idempotency_key", name="uq_ticket_principal_idem"),
        UniqueConstraint("idempotency_fingerprint", name="uq_ticket_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False
    )
    request_type: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    description_redacted: Mapped[str] = mapped_column(String(1000), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(40), nullable=False)
    refund_amount_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalTask(Base):
    __tablename__ = "approval_tasks"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(
        ForeignKey("service_tickets.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    session_id: Mapped[str | None] = mapped_column(String(48), index=True)
    checkpoint_thread_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    decided_by: Mapped[str | None] = mapped_column(String(40))
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    graph_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_session_created", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    graph_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolCallLog(Base):
    __tablename__ = "tool_call_logs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    arguments_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeReindexJob(Base):
    __tablename__ = "knowledge_reindex_jobs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(40), nullable=False)
    document_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    target_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    error_summary: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "case_id", name="uq_eval_result_run_case"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    evaluation_run_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    error_type: Mapped[str | None] = mapped_column(String(80))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


REQUIRED_TABLE_NAMES = {
    "users",
    "products",
    "skus",
    "inventory",
    "orders",
    "order_items",
    "logistics_events",
    "after_sales_rules",
    "service_tickets",
    "approval_tasks",
    "chat_sessions",
    "chat_messages",
    "agent_runs",
    "tool_call_logs",
    "knowledge_documents",
    "knowledge_versions",
    "knowledge_reindex_jobs",
    "evaluation_runs",
    "evaluation_results",
}
