from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.business.demo_data import BASE_TIME
from packages.business.models import AfterSalesRule, KnowledgeDocument, Product, Sku
from packages.rag_core.models import KnowledgeChunk, KnowledgeSourceDocument


@dataclass(frozen=True)
class KnowledgeDataset:
    documents: list[KnowledgeSourceDocument]
    chunks: list[KnowledgeChunk]
    fingerprint: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chunk(
    document: KnowledgeSourceDocument,
    *,
    index: int,
    text: str,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=f"chk_{document.doc_id.removeprefix('doc_')}_{index:02d}",
        doc_id=document.doc_id,
        doc_type=document.doc_type,
        title=document.title,
        text=text,
        source_uri=document.source_uri,
        knowledge_version=document.version,
        status=document.status,
        effective_from=document.effective_from,
        effective_to=document.effective_to,
        content_hash=_hash(text),
        product_id=document.product_id,
        sku_id=document.sku_id,
        category=document.category,
        metadata=document.metadata,
    )


def _fingerprint(chunks: list[KnowledgeChunk]) -> str:
    payload = [
        chunk.model_dump(mode="json", exclude={"knowledge_version"})
        for chunk in sorted(chunks, key=lambda item: item.chunk_id)
    ]
    return _hash(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


async def build_knowledge_dataset(session: AsyncSession, version: str) -> KnowledgeDataset:
    products = list((await session.scalars(select(Product).order_by(Product.id))).all())
    skus = list((await session.scalars(select(Sku).order_by(Sku.id))).all())
    rules = list(
        (await session.scalars(select(AfterSalesRule).order_by(AfterSalesRule.id))).all()
    )
    custom_documents = list(
        (
            await session.scalars(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.id.like("kdoc_%"),
                    KnowledgeDocument.status == "active",
                )
                .order_by(KnowledgeDocument.id)
            )
        ).all()
    )
    product_map = {product.id: product for product in products}
    documents: list[KnowledgeSourceDocument] = []
    chunks: list[KnowledgeChunk] = []

    for product in products:
        warranty = product.attributes_json["warranty_months"]
        usage = "、".join(product.usage_tags)
        audience = "、".join(product.audience_tags)
        content = (
            f"问：{product.name}适合哪些使用场景和人群？答：该商品属于{product.category}类目，"
            f"适合{usage}场景，主要面向{audience}。静态说明中的建议仅用于选型参考，"
            f"标准保修期为{warranty}个月；具体售后资格仍以当前生效政策和售后工具校验结果为准。"
        )
        document = KnowledgeSourceDocument(
            doc_id=f"doc_faq_{product.id.removeprefix('prd_')}",
            doc_type="faq",
            title=f"{product.name}常见问题",
            content=content,
            source_uri=f"demo://faq/{product.id}",
            version=version,
            effective_from=BASE_TIME - timedelta(days=365),
            product_id=product.id,
            category=product.category,
            metadata={"data_version": product.data_version},
        )
        documents.append(document)
        chunks.append(_chunk(document, index=1, text=content))

    for rule_index, rule in enumerate(rules, start=1):
        # Enabled demo rules occur at indices 1, 5, 9, ... . Mix one enabled and
        # one draft rule into every eight-row activity cohort so both policy and
        # activity corpora exercise lifecycle filtering.
        doc_type: Literal["activity", "policy"] = (
            "activity" if rule_index % 8 in {1, 4} else "policy"
        )
        conditions = "、".join(rule.allowed_conditions)
        exclusions = "、".join(rule.excluded_reason_codes)
        materials = "、".join(rule.required_materials)
        content = (
            f"规则代码{rule.rule_code}，版本{rule.version}，适用于{rule.category}类目的"
            f"{rule.request_type}申请。自签收日起{rule.window_days}天内可以提交材料进行资格审核。"
            f"允许的商品状态包括{conditions}；排除原因包括{exclusions}。申请时需要提供{materials}。"
            "该文档只解释规则，不代表已批准退款、换货或维修，也不包含任何用户订单、实时库存、"
            "当前售价、退款金额或物流状态。最终资格必须由售后工具基于订单快照重新计算。"
            "若政策版本、有效期或商品状态发生冲突，应停止自动回答并展示当前规则原文或转人工处理。"
        )
        document = KnowledgeSourceDocument(
            doc_id=f"doc_rule_{rule.id.removeprefix('rule_')}",
            doc_type=doc_type,
            title=f"{rule.category} {rule.request_type}规则 {rule.rule_code}",
            content=content,
            source_uri=f"demo://rule/{rule.rule_code}",
            version=version,
            status="active" if rule.enabled else "draft",
            effective_from=rule.active_from,
            effective_to=rule.active_until,
            category=rule.category,
            metadata={"rule_code": rule.rule_code, "rule_version": rule.version},
        )
        documents.append(document)
        chunks.append(_chunk(document, index=1, text=content))

    for sku in skus:
        product = product_map[sku.product_id]
        usage = "、".join(product.usage_tags)
        audience = "、".join(product.audience_tags)
        static_attributes = "、".join(
            f"{key}={value}" for key, value in sorted(product.attributes_json.items())
        )
        specifications = "、".join(
            f"{key}={value}" for key, value in sorted(sku.specifications_json.items())
        )
        overview = (
            f"{product.name}是{product.brand}品牌的{product.category}类目演示商品。"
            f"适用场景包括{usage}，适用人群包括{audience}。静态属性为{static_attributes}。"
            "本说明只描述稳定产品特征，不包含售价、促销额度、区域库存、用户订单或物流状态；"
            "这些动态事实必须通过 Catalog 或 Order MCP 工具实时获取。"
        )
        specification = (
            f"SKU {sku.id}的名称为{sku.name}，颜色为{sku.color}，静态规格为{specifications}。"
            "使用前应阅读包装内说明，保持接口和设备清洁，避免超出标称使用环境。"
            "保修和退换条件以当前生效政策为准；知识说明不能代替售后资格工具，也不能承诺退款。"
        )
        document = KnowledgeSourceDocument(
            doc_id=f"doc_product_{sku.id.removeprefix('sku_')}",
            doc_type="product",
            title=f"{sku.name}产品说明",
            content=f"{overview}\n{specification}",
            source_uri=f"demo://product/{product.id}/{sku.id}",
            version=version,
            effective_from=BASE_TIME - timedelta(days=180),
            product_id=product.id,
            sku_id=sku.id,
            category=product.category,
            metadata={"brand": product.brand, "data_version": product.data_version},
        )
        documents.append(document)
        chunks.extend(
            [
                _chunk(document, index=1, text=overview),
                _chunk(document, index=2, text=specification),
            ]
        )

    if len(documents) != 1580 or len(chunks) != 2780:
        raise RuntimeError(
            f"unexpected knowledge cardinality: documents={len(documents)}, chunks={len(chunks)}"
        )
    for custom in custom_documents:
        document = KnowledgeSourceDocument(
            doc_id=f"doc_custom_{custom.id.removeprefix('kdoc_')}",
            doc_type=cast(
                Literal["faq", "policy", "activity", "product"],
                custom.document_type,
            ),
            title=custom.title,
            content=custom.content_text,
            source_uri=custom.source_uri,
            version=version,
            status=cast(Literal["active", "draft", "expired"], custom.status),
            effective_from=custom.created_at,
            metadata={
                "api_document_id": custom.id,
                "created_by": custom.created_by,
            },
        )
        documents.append(document)
        chunks.append(_chunk(document, index=1, text=custom.content_text))
    return KnowledgeDataset(
        documents=documents,
        chunks=chunks,
        fingerprint=_fingerprint(chunks),
    )


def chunk_payload(chunk: KnowledgeChunk) -> dict[str, Any]:
    payload = chunk.model_dump(mode="json")
    payload["effective_from_epoch"] = int(chunk.effective_from.timestamp())
    payload["effective_to_epoch"] = (
        int(chunk.effective_to.timestamp()) if chunk.effective_to else 253402300799
    )
    return payload
