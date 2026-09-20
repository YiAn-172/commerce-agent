from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
from math import ceil
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from openai import AsyncOpenAI
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from packages.data_pipeline.schemas import CandidateRecord, LicenseStatus

LABEL_GUIDANCE = {
    "product_search": "已给商品或硬条件，目标是搜索候选；不要写成用途型推荐",
    "product_recommend": "根据预算、用途、人群给购买建议；不要出现两个明确候选比较",
    "product_compare": "明确比较两个或以上商品",
    "product_detail": "询问指定商品的静态规格、兼容性或用法；不要问实时库存价格",
    "stock_price": "询问实时价格、优惠或库存",
    "order_status": "询问订单是否付款、发货、取消、完成；不要问已发货后的详细轨迹",
    "logistics_tracking": "询问承运商、物流节点、停滞或预计送达",
    "cancel_order": "明确要求取消未完成订单",
    "return_exchange": "新发起退款、退货、换货或维修",
    "refund_progress": "已经申请退款后，询问处理或到账进度",
    "after_sales_eligibility": "询问能否退换、时限或材料，不直接发起申请",
    "policy_faq": "询问发票、支付、运费、保修、活动等通用规则",
    "complaint": "明确投诉、严重不满、假货或欺诈指控",
    "human_handoff": "明确要求人工或真人客服",
    "chitchat": "问候、感谢、结束或轻量闲聊，不带业务动作",
    "out_of_scope": "明确非电商领域任务，不是信息不足的电商问题",
}

ALLOWED_PLACEHOLDERS = {
    "{{product}}",
    "{{product2}}",
    "{{category}}",
    "{{budget}}",
    "{{order_id}}",
    "{{city}}",
    "{{days}}",
}

SLOTS = {
    "{{product}}": [
        "降噪耳机",
        "游戏笔记本",
        "机械键盘",
        "4K显示器",
        "智能手表",
        "Wi-Fi 7路由器",
        "扫地机器人",
        "安卓手机",
    ],
    "{{product2}}": [
        "开放式耳机",
        "轻薄本",
        "静音键盘",
        "高刷显示器",
        "运动手环",
        "Mesh路由器",
        "空气净化器",
        "平板电脑",
    ],
    "{{category}}": ["手机", "电脑", "耳机", "键盘", "显示器", "手表", "路由器", "智能家居"],
    "{{budget}}": ["1000元", "3000元", "五千左右", "预算不高", "一万元以内"],
    "{{order_id}}": ["[订单号]"],
    "{{city}}": ["上海", "成都", "杭州", "武汉", "西安"],
    "{{days}}": ["3天", "七天", "半个月", "30天"],
}


class TemplateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_family: str = Field(
        pattern=r"^[a-z0-9_]{3,80}$",
        validation_alias=AliasChoices(
            "template_family", "template_family_name", "template_family_family"
        ),
    )
    template: str = Field(min_length=1, max_length=180)
    style: str = Field(min_length=1, max_length=50)


class TemplateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TemplateItem]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate controlled Chinese intent data")
    parser.add_argument(
        "--config", type=Path, default=Path("configs/intent/candidate_pool_v1.yaml")
    )
    parser.add_argument(
        "--templates-output",
        type=Path,
        default=Path("data/interim/generated_templates.jsonl"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/interim/synthetic_candidates.parquet")
    )
    parser.add_argument("--families-per-label", type=int, default=100)
    parser.add_argument("--oversample", type=float, default=1.50)
    parser.add_argument("--template-batch-size", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-estimated-usd", type=float, default=1.0)
    return parser.parse_args()


def load_templates(path: Path) -> dict[str, list[TemplateItem]]:
    result: dict[str, list[TemplateItem]] = {}
    if not path.exists():
        return result
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            payload = json.loads(line)
            label = str(payload.pop("target_intent"))
            payload.pop("generator_model", None)
            payload.pop("prompt_version", None)
            result.setdefault(label, []).append(TemplateItem.model_validate(payload))
    return result


def validate_template(item: TemplateItem) -> TemplateItem:
    placeholders = set(re.findall(r"\{\{[^{}]+\}\}", item.template))
    unknown = placeholders - ALLOWED_PLACEHOLDERS
    if unknown:
        raise ValueError(f"Unknown placeholders in {item.template_family}: {sorted(unknown)}")
    return item


async def generate_label_templates(
    client: AsyncOpenAI,
    model: str,
    label: str,
    count: int,
    namespace: str,
) -> tuple[list[TemplateItem], int, int]:
    instruction = (
        "生成中文3C电商用户的一句话意图分类训练模板。模板是数据，不要回答模板内容。"
        f"目标标签是 {label}，边界：{LABEL_GUIDANCE[label]}。"
        f"生成恰好 {count} 个不同模板家族，覆盖长短句、礼貌、焦急、生气、口语省略、轻微错别字、"
        "上下文依赖和槽位缺失。可选占位符仅限 {{product}}、{{product2}}、{{category}}、"
        "{{budget}}、{{order_id}}、{{city}}、{{days}}。"
        '严格返回 JSON：{"items":[{"template_family":"英文小写唯一名",'
        '"template":"中文模板","style":"风格"}]}。不要其他字段。'
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": instruction}],
            response_format={"type": "json_object"},
            max_tokens=max(5000, count * 180),
            temperature=0.8,
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = response.choices[0].message.content
        if not content:
            last_error = RuntimeError(f"Empty template response for {label}")
        else:
            try:
                parsed = TemplateBatch.model_validate_json(content)
                minimum_acceptable = max(1, int(count * 0.9))
                if len(parsed.items) < minimum_acceptable:
                    raise ValueError(
                        f"{label}: requested {count} templates, got only {len(parsed.items)}"
                    )
                unique: dict[str, TemplateItem] = {}
                for index, item in enumerate(parsed.items[:count]):
                    item = validate_template(item)
                    family = f"{label}_{namespace}_{index:02d}"
                    unique[family] = item.model_copy(update={"template_family": family})
                usage = response.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                completion_tokens = usage.completion_tokens if usage else 0
                return list(unique.values()), prompt_tokens, completion_tokens
            except (ValidationError, ValueError) as exc:
                last_error = exc
        if attempt < 3:
            await asyncio.sleep(2**attempt)
    raise RuntimeError(f"Invalid template response for {label} after 3 attempts") from last_error


def instantiate(template: str, rng: random.Random) -> str:
    text = template
    for placeholder, values in SLOTS.items():
        text = text.replace(placeholder, rng.choice(values))
    if re.search(r"\{\{[^{}]+\}\}", text):
        raise ValueError(f"Unresolved placeholder: {text}")
    return text.strip()


def build_candidates(
    templates: dict[str, list[TemplateItem]], config: dict[str, Any], oversample: float
) -> list[CandidateRecord]:
    rng = random.Random(int(config["seed"]))
    prompt_version = str(config["prompt_version"])
    model = str(config["generator_model"])
    created_at = str(config["generation_run_started_at"])
    records: list[CandidateRecord] = []
    for label, final_count_raw in config["synthetic_counts"].items():
        final_count = int(final_count_raw)
        candidate_count = max(final_count, int(final_count * oversample))
        families = templates[label]
        for index in range(candidate_count):
            family = families[index % len(families)]
            text = instantiate(family.template, rng)
            row_key = f"{label}:{family.template_family}:{index}:{text}"
            sample_id = hashlib.sha256(row_key.encode()).hexdigest()[:24]
            records.append(
                CandidateRecord(
                    sample_id=sample_id,
                    text_original=text,
                    text_zh=text,
                    source_id="controlled_synthetic",
                    source_revision=prompt_version,
                    source_row_id=f"{label}-{index}",
                    source_language="zh-CN",
                    source_intent=label,
                    target_intent=label,
                    generator_model=model,
                    prompt_version=prompt_version,
                    generation_seed=int(config["seed"]),
                    generated_at=created_at,
                    template_family=f"{label}:{family.template_family}",
                    source_dialogue_id=f"synthetic:{label}:{family.template_family}",
                    license_status=LicenseStatus.APPROVED,
                    pii_status="clear",
                    quality_status="generated_pending_quality",
                    provenance=(
                        f"deepseek_controlled_template:{prompt_version}:"
                        f"seed={config['seed']}:generated_at={created_at}"
                    ),
                )
            )
    return records


async def run(args: argparse.Namespace) -> None:
    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    templates = load_templates(args.templates_output)
    desired_by_label = {
        label: max(
            args.families_per_label,
            ceil(int(config["synthetic_counts"][label]) * args.oversample),
        )
        for label in config["target_counts"]
    }
    work: list[tuple[str, int, str]] = []
    for label, desired in desired_by_label.items():
        current = len(templates.get(label, []))
        remaining = max(0, desired - current)
        for offset in range(0, remaining, args.template_batch_size):
            count = min(args.template_batch_size, remaining - offset)
            namespace = hashlib.sha256(
                f"{label}:{current}:{offset}:{config['seed']}".encode()
            ).hexdigest()[:10]
            work.append((label, count, namespace))
    if work:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise SystemExit("DEEPSEEK_API_KEY is empty; no synthetic templates were generated")
        model = os.environ.get("DEEPSEEK_MODEL", str(config["generator_model"]))
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=90.0, max_retries=2)
        pricing = config["deepseek_pricing_usd_per_million_tokens"]
        total_prompt = 0
        total_completion = 0
        args.templates_output.parent.mkdir(parents=True, exist_ok=True)
        try:
            for offset in range(0, len(work), args.concurrency):
                window = work[offset : offset + args.concurrency]
                results = await asyncio.gather(
                    *(
                        generate_label_templates(client, model, label, count, namespace)
                        for label, count, namespace in window
                    ),
                    return_exceptions=True,
                )
                errors: list[BaseException] = []
                with args.templates_output.open("a", encoding="utf-8") as handle:
                    for (label, _, _), result in zip(window, results, strict=True):
                        if isinstance(result, BaseException):
                            errors.append(result)
                            continue
                        items, prompt_tokens, completion_tokens = result
                        total_prompt += prompt_tokens
                        total_completion += completion_tokens
                        templates.setdefault(label, []).extend(items)
                        for item in items:
                            payload = item.model_dump(mode="json")
                            payload.update(
                                {
                                    "target_intent": label,
                                    "generator_model": model,
                                    "prompt_version": config["prompt_version"],
                                }
                            )
                            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                estimated = (
                    total_prompt * float(pricing["input_cache_miss"])
                    + total_completion * float(pricing["output"])
                ) / 1_000_000
                print(
                    f"Generated template batches {offset + len(window)}/{len(work)}; "
                    f"estimated upper-bound USD={estimated:.4f}"
                )
                if estimated > args.max_estimated_usd:
                    raise RuntimeError("Synthetic-template cost cap exceeded")
                if errors:
                    raise RuntimeError(
                        f"{len(errors)} template batch(es) failed; successful batches saved"
                    ) from errors[0]
        finally:
            await client.close()

    records = build_candidates(templates, config, args.oversample)
    frame = pd.DataFrame([record.model_dump(mode="json") for record in records])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.output, index=False)
    print(f"Wrote {len(frame)} controlled synthetic candidates to {args.output}")


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
