from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.business.models import Inventory, Product, Sku
from packages.contracts.catalog import (
    CheckInventoryInput,
    InventorySnapshot,
    ProductDetail,
    ProductDetailInput,
    ProductSku,
    ProductSummary,
    SearchProductsInput,
)
from packages.contracts.common import ErrorCode, ToolEnvelope
from packages.mcp_core.context import TrustedToolContext
from packages.mcp_core.errors import ToolFailure
from packages.mcp_core.runtime import execute_tool


def _matches_filters(product: Product, sku: Sku, filters: Mapping[str, object]) -> bool:
    available: dict[str, object] = {
        **product.attributes_json,
        **sku.specifications_json,
        "color": sku.color,
    }
    return all(available.get(key) == value for key, value in filters.items())


def _match_reasons(product: Product, sku: Sku, query: str) -> list[str]:
    reasons: list[str] = []
    normalized = query.casefold()
    if normalized in product.name.casefold() or normalized in sku.name.casefold():
        reasons.append("名称匹配")
    if normalized in product.brand.casefold():
        reasons.append("品牌匹配")
    if normalized in product.description.casefold():
        reasons.append("描述匹配")
    if not reasons:
        reasons.append("结构化条件匹配")
    return reasons


class CatalogToolService:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def search_products(
        self,
        context: TrustedToolContext,
        payload: SearchProductsInput,
    ) -> ToolEnvelope[list[ProductSummary]]:
        async def operation() -> list[ProductSummary]:
            statement: Select[tuple[Product, Sku]] = (
                select(Product, Sku)
                .join(Sku, Sku.product_id == Product.id)
                .where(Product.status == "active", Sku.status == "active")
            )
            query = payload.query.strip()
            statement = statement.where(
                or_(
                    Product.name.contains(query),
                    Product.brand.contains(query),
                    Product.description.contains(query),
                    Sku.name.contains(query),
                )
            )
            if payload.category:
                statement = statement.where(Product.category == payload.category)
            if payload.brand:
                statement = statement.where(Product.brand == payload.brand)
            if payload.price_min is not None:
                statement = statement.where(Sku.current_price >= payload.price_min)
            if payload.price_max is not None:
                statement = statement.where(Sku.current_price <= payload.price_max)
            if payload.sort == "price_asc":
                statement = statement.order_by(Sku.current_price.asc(), Product.id, Sku.id)
            elif payload.sort == "price_desc":
                statement = statement.order_by(Sku.current_price.desc(), Product.id, Sku.id)
            elif payload.sort == "rating":
                statement = statement.order_by(Product.rating.desc(), Product.id, Sku.id)
            else:
                statement = statement.order_by(
                    Product.sales_count.desc(), Product.rating.desc(), Product.id, Sku.id
                )
            statement = statement.limit(200)
            async with self.factory() as session:
                rows = (await session.execute(statement)).all()
            results: list[ProductSummary] = []
            for product, sku in rows:
                if not _matches_filters(product, sku, payload.filters):
                    continue
                results.append(
                    ProductSummary(
                        product_id=product.id,
                        sku_id=sku.id,
                        name=sku.name,
                        brand=product.brand,
                        category=product.category,
                        price=sku.current_price,
                        rating=float(product.rating),
                        match_reasons=_match_reasons(product, sku, query),
                        price_snapshot_id=sku.price_snapshot_id,
                        valid_until=sku.price_valid_until,
                    )
                )
                if len(results) == payload.limit:
                    break
            return results

        return await execute_tool(
            name="search_products",
            context=context,
            required_scope="catalog:read",
            operation=operation,
        )

    async def get_product_detail(
        self,
        context: TrustedToolContext,
        payload: ProductDetailInput,
    ) -> ToolEnvelope[ProductDetail]:
        async def operation() -> ProductDetail:
            async with self.factory() as session:
                product = await session.scalar(
                    select(Product).where(
                        Product.id == payload.product_id,
                        Product.status == "active",
                    )
                )
                if product is None:
                    raise ToolFailure(ErrorCode.NOT_FOUND, "商品不存在或当前不可用。")
                skus = (
                    await session.scalars(
                        select(Sku)
                        .where(Sku.product_id == product.id, Sku.status == "active")
                        .order_by(Sku.id)
                    )
                ).all()
            return ProductDetail(
                product_id=product.id,
                name=product.name,
                brand=product.brand,
                category=product.category,
                description=product.description,
                attributes=product.attributes_json,
                usage_tags=product.usage_tags,
                audience_tags=product.audience_tags,
                rating=float(product.rating),
                skus=[
                    ProductSku(
                        sku_id=sku.id,
                        name=sku.name,
                        color=sku.color,
                        specifications=sku.specifications_json,
                        price=sku.current_price,
                        price_snapshot_id=sku.price_snapshot_id,
                        valid_until=sku.price_valid_until,
                    )
                    for sku in skus
                ],
                data_version=product.data_version,
            )

        return await execute_tool(
            name="get_product_detail",
            context=context,
            required_scope="catalog:read",
            operation=operation,
        )

    async def check_inventory(
        self,
        context: TrustedToolContext,
        payload: CheckInventoryInput,
    ) -> ToolEnvelope[InventorySnapshot]:
        async def operation() -> InventorySnapshot:
            statement: Select[tuple[Inventory]] = (
                select(Inventory)
                .join(Sku, Sku.id == Inventory.sku_id)
                .where(
                    Sku.product_id == payload.product_id,
                    Sku.id == payload.sku_id,
                    Sku.status == "active",
                    Inventory.region == payload.region,
                )
            )
            async with self.factory() as session:
                inventory = await session.scalar(statement)
            if inventory is None:
                raise ToolFailure(ErrorCode.NOT_FOUND, "未找到对应商品、规格和区域的库存。")
            return InventorySnapshot(
                product_id=payload.product_id,
                sku_id=payload.sku_id,
                region=inventory.region,
                available_quantity=inventory.available_quantity,
                warehouse=inventory.warehouse,
                as_of=inventory.as_of,
            )

        return await execute_tool(
            name="check_inventory",
            context=context,
            required_scope="catalog:read",
            operation=operation,
        )
