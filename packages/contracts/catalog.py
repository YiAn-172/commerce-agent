from datetime import datetime
from decimal import Decimal

from pydantic import Field, model_validator

from packages.contracts.base import StrictModel


class SearchProductsInput(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=60)
    brand: str | None = Field(default=None, max_length=60)
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    limit: int = Field(default=10, ge=1, le=20)
    sort: str = Field(default="relevance", pattern=r"^(relevance|price_asc|price_desc|rating)$")

    @model_validator(mode="after")
    def validate_price_range(self) -> "SearchProductsInput":
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min cannot exceed price_max")
        return self


class ProductDetailInput(StrictModel):
    product_id: str = Field(pattern=r"^prd_[A-Za-z0-9_-]{6,64}$")


class CheckInventoryInput(StrictModel):
    product_id: str = Field(pattern=r"^prd_[A-Za-z0-9_-]{6,64}$")
    sku_id: str = Field(pattern=r"^sku_[A-Za-z0-9_-]{6,64}$")
    region: str = Field(min_length=2, max_length=40)


class ProductSummary(StrictModel):
    product_id: str
    sku_id: str
    name: str
    brand: str
    category: str
    price: Decimal = Field(ge=0)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    rating: float = Field(ge=0, le=5)
    match_reasons: list[str] = Field(default_factory=list, max_length=10)
    price_snapshot_id: str
    valid_until: datetime


class InventorySnapshot(StrictModel):
    product_id: str
    sku_id: str
    region: str
    available_quantity: int = Field(ge=0)
    warehouse: str
    as_of: datetime


class ProductSku(StrictModel):
    sku_id: str
    name: str
    color: str
    specifications: dict[str, str | int | float | bool]
    price: Decimal = Field(ge=0)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    price_snapshot_id: str
    valid_until: datetime


class ProductDetail(StrictModel):
    product_id: str
    name: str
    brand: str
    category: str
    description: str
    attributes: dict[str, str | int | float | bool]
    usage_tags: list[str]
    audience_tags: list[str]
    rating: float = Field(ge=0, le=5)
    skus: list[ProductSku]
    data_version: str
