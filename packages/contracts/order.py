from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from packages.contracts.base import StrictModel


class OrderStatus(StrEnum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    PACKED = "packed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    AFTER_SALES_REQUESTED = "after_sales_requested"
    AFTER_SALES_PROCESSING = "after_sales_processing"
    REFUNDED = "refunded"
    CLOSED = "closed"


class ListRecentOrdersInput(StrictModel):
    limit: int = Field(default=3, ge=1, le=10)
    cursor: str | None = Field(default=None, max_length=200)


class GetOrderDetailInput(StrictModel):
    order_id: str = Field(pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")


class TrackLogisticsInput(StrictModel):
    order_id: str = Field(pattern=r"^ord_[A-Za-z0-9_-]{6,64}$")


class OrderItem(StrictModel):
    item_id: str
    product_id: str
    sku_id: str
    name: str
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0)


class OrderDetail(StrictModel):
    order_id: str
    status: OrderStatus
    items: list[OrderItem]
    total_amount: Decimal = Field(ge=0)
    currency: str = "CNY"
    state_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class OrderSummary(StrictModel):
    order_id: str
    status: OrderStatus
    item_count: int = Field(ge=1)
    total_amount: Decimal = Field(ge=0)
    currency: str = "CNY"
    state_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class RecentOrderPage(StrictModel):
    orders: list[OrderSummary]
    next_cursor: str | None = None


class LogisticsEventView(StrictModel):
    sequence: int = Field(ge=1)
    carrier: str
    tracking_number_masked: str
    status_code: str
    description: str
    occurred_at: datetime
    as_of: datetime


class LogisticsTimeline(StrictModel):
    order_id: str
    order_status: OrderStatus
    events: list[LogisticsEventView]
    as_of: datetime
