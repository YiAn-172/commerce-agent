from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.intent_service.runtime import IntentRuntime
from scripts.human_review_workspace import ROOT, read_csv, read_jsonl, write_csv

DRAFT_ROOT = ROOT / "data/annotation/review_work/ai_drafts"
SUMMARY = ROOT / "reports/eval/ai_review_draft_summary.json"

LABEL_RATIONALES = {
    "product_search": "用户给出了商品或硬条件，目标是查找候选商品。",
    "product_recommend": "用户希望结合预算、用途或人群获得购买建议。",
    "product_compare": "用户要求比较两个或以上明确商品。",
    "product_detail": "用户询问指定商品的静态规格、兼容性或用法。",
    "stock_price": "用户询问实时售价、优惠或库存。",
    "order_status": "用户查询订单支付、发货、取消或完成状态。",
    "logistics_tracking": "用户查询已发货后的快递节点、停滞或送达时间。",
    "cancel_order": "用户明确要求取消尚未完成的订单。",
    "return_exchange": "用户要新发起退款、退货、换货或维修。",
    "refund_progress": "用户查询已经提交的退款进度或到账状态。",
    "after_sales_eligibility": "用户询问是否符合退换资格、时限或材料要求。",
    "policy_faq": "用户询问配送、发票、支付、保修、运费或活动通用规则。",
    "complaint": "用户表达明确投诉、严重不满或欺诈/假货指控。",
    "human_handoff": "用户明确要求转接真人客服。",
    "chitchat": "内容属于问候、感谢、结束或轻量闲聊。",
    "out_of_scope": "内容明确不属于电商客服能力范围。",
}

ROUTING_CONTRACTS = {
    "product_consult": ("knowledge", "auto_route"),
    "product_recommend": ("shopping", "auto_route"),
    "order_logistics": ("order", "auto_route"),
    "after_sales": ("after_sales", "auto_route"),
    "ambiguous": ("safe_reply", "safe_reply"),
    "direct_dispatch": ("knowledge", "auto_route"),
    "clarification": (None, "clarify"),
    "human_handoff": ("human", "auto_route"),
}

TOOL_CONTRACTS = {
    "shopping": ["search_products", "check_inventory"],
    "order_detail": ["get_order_detail"],
    "logistics": ["track_logistics"],
    "recent_order": ["list_recent_orders"],
    "after_sales": ["check_after_sales_eligibility"],
    "knowledge": [],
    "human": [],
    "general": [],
    "safe_reply": [],
    "missing_information": [],
}


def rewrite_text(label: str, index: int) -> str:
    products = ["蓝牙耳机", "机械键盘", "咖啡机", "智能手表", "旅行背包"]
    uses = ["通勤", "办公", "运动", "旅行"]
    budgets = ["300元", "500元", "800元", "1200元", "2000元"]
    orders = ["[订单号]", "刚才那笔订单", "最近一笔订单", "昨天提交的订单"]
    variants: dict[str, list[str]] = {
        "product_search": [
            f"帮我找一款支持主动降噪的{products[index % len(products)]}",
            f"有没有{budgets[index % len(budgets)]}以内的{products[index % len(products)]}",
            f"想看看适合{uses[index % len(uses)]}的{products[index % len(products)]}",
            f"帮我筛选一下能用于{uses[index % len(uses)]}的{products[index % len(products)]}",
        ],
        "product_recommend": [
            (
                f"预算{budgets[index % len(budgets)]}，推荐一款适合"
                f"{uses[index % len(uses)]}的{products[index % len(products)]}"
            ),
            f"我主要用于{uses[index % len(uses)]}，哪款{products[index % len(products)]}更值得买",
            (
                f"第一次买{products[index % len(products)]}，请按"
                f"{budgets[index % len(budgets)]}预算给建议"
            ),
            f"给我推荐一个适合{uses[index % len(uses)]}场景的{products[index % len(products)]}",
        ],
        "product_compare": [
            f"对比一下A款和B款{products[index % len(products)]}的区别",
            f"这两款{products[index % len(products)]}哪一个更适合{uses[index % len(uses)]}",
            f"帮我比较商品A、商品B和商品C的优缺点，都是{products[index % len(products)]}",
            f"同价位的这两款{products[index % len(products)]}怎么选",
        ],
        "product_detail": [
            f"这款{products[index % len(products)]}支持哪些连接方式",
            f"商品A的{products[index % len(products)]}具体尺寸和重量是多少",
            f"这款{products[index % len(products)]}怎么使用，包装里有什么",
            f"商品A和我的设备兼容吗，商品类型是{products[index % len(products)]}",
        ],
        "stock_price": [
            f"这款{products[index % len(products)]}现在多少钱",
            "商品A今天有优惠吗，还有现货吗",
            f"帮我查一下{products[index % len(products)]}在上海仓的库存",
            f"这款{products[index % len(products)]}当前到手价是多少",
        ],
        "order_status": [
            f"帮我看看{orders[index % len(orders)]}现在是什么状态",
            f"{orders[index % len(orders)]}支付成功了吗",
            f"{orders[index % len(orders)]}为什么还没有发货",
            f"查询一下{orders[index % len(orders)]}是否已经取消",
        ],
        "logistics_tracking": [
            f"{orders[index % len(orders)]}的快递到哪里了",
            f"物流两天没更新了，帮我查查{orders[index % len(orders)]}",
            f"{orders[index % len(orders)]}预计什么时候送到",
            f"帮我看一下{orders[index % len(orders)]}的最新物流节点",
        ],
        "cancel_order": [
            f"我不想要了，帮我取消{orders[index % len(orders)]}",
            f"{orders[index % len(orders)]}还没发货，请直接取消",
            f"能不能撤销{orders[index % len(orders)]}",
            f"请停止处理并取消{orders[index % len(orders)]}",
        ],
        "return_exchange": [
            f"收到的{products[index % len(products)]}不合适，我要申请退货",
            "商品A有质量问题，帮我发起换货",
            f"我想给{orders[index % len(orders)]}申请退款退货",
            "这个商品坏了，怎么提交维修售后",
        ],
        "after_sales_eligibility": [
            f"{orders[index % len(orders)]}已经签收十天了还能退吗",
            "商品包装拆开后是否还符合换货条件",
            "申请退货需要准备哪些材料",
            "这个商品超过七天还能申请售后吗",
        ],
        "policy_faq": [
            "平台支持哪些支付方式",
            "电子发票应该怎么申请",
            "普通商品的保修政策是什么",
            "订单不满包邮金额时运费怎么计算",
        ],
        "complaint": [
            f"收到的{products[index % len(products)]}疑似是假货，我要投诉",
            "客服一直不处理问题，我要正式投诉",
            "商家存在欺诈宣传，请记录我的投诉",
            "商品质量很差而且拒绝售后，我要升级处理",
        ],
        "human_handoff": [
            "请马上帮我转接真人客服",
            "这个问题机器人解决不了，我要人工处理",
            "我要和人工客服沟通",
            "请安排真人客服继续处理我的订单问题",
        ],
        "chitchat": [
            "你好，今天辛苦了",
            "谢谢你的帮助",
            "好的，我知道了，再见",
            "早上好，你在吗",
        ],
        "out_of_scope": [
            "帮我写一段旅游攻略",
            "今天的足球比赛谁会赢",
            "请帮我解一道高等数学题",
            "给我推荐一部正在上映的电影",
        ],
    }
    if label == "refund_progress":
        times = ["昨天", "三天前", "上周", "前几天", "本月初", "五天前", "七天前", "十天前"]
        states = ["还没到账", "一直显示处理中", "状态没有更新", "银行卡还没收到", "没有任何进展"]
        asks = ["帮我查下进度", "现在到哪一步了", "预计什么时候到账", "请确认退款状态"]
        return (
            f"我{times[index % len(times)]}为{orders[(index // 2) % len(orders)]}申请了退款，"
            f"退款金额是{23 + index * 7}元，现在{states[(index // 3) % len(states)]}，"
            f"{asks[(index // 5) % len(asks)]}"
        )
    options = variants[label]
    base = options[index % len(options)]
    qualifiers = ["", "，麻烦尽快处理", "，请帮我确认一下", "，我现在比较着急", "，谢谢"]
    return base + qualifiers[(index // len(options)) % len(qualifiers)]


def predict_rows(runtime: IntentRuntime, texts: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for start in range(0, len(texts), 64):
        for prediction in runtime.predict_batch(texts[start : start + 64]):
            model_label = prediction.model_label or prediction.label
            result.append(
                {
                    "model_label": model_label.value,
                    "served_label": prediction.label.value,
                    "confidence": prediction.confidence,
                    "decision": prediction.decision,
                    "route": prediction.route.value,
                }
            )
    return result


def generate_p1(runtime: IntentRuntime) -> dict[str, Any]:
    source = read_csv(ROOT / "data/annotation/gold_annotation_queue_v1.csv")
    work = {
        row["annotation_id"]: row
        for row in read_csv(ROOT / "data/annotation/review_work/p1_gold_annotator_1_blind.csv")
    }
    rewrite_counters: Counter[str] = Counter()
    candidate_texts: list[str] = []
    for row in source:
        label = row["stratum_intent"]
        if row["requires_independent_human_rewrite"].lower() == "true":
            candidate_text = rewrite_text(label, rewrite_counters[label])
            rewrite_counters[label] += 1
        else:
            candidate_text = row["seed_text"]
        candidate_texts.append(candidate_text)
    predictions = predict_rows(runtime, candidate_texts)
    rows: list[dict[str, Any]] = []
    attention = 0
    for source_row, text, prediction in zip(source, candidate_texts, predictions, strict=True):
        intended = source_row["stratum_intent"]
        needs_attention = (
            prediction["model_label"] != intended
            or float(prediction["confidence"]) < 0.75
            or prediction["decision"] != "auto_route"
        )
        attention += int(needs_attention)
        rows.append(
            {
                "batch_id": work[source_row["annotation_id"]]["batch_id"],
                "annotation_id": source_row["annotation_id"],
                "source_row_sha256": work[source_row["annotation_id"]]["source_row_sha256"],
                "requires_independent_human_rewrite": source_row[
                    "requires_independent_human_rewrite"
                ],
                "seed_text": source_row["seed_text"],
                "ai_suggested_text": text,
                "ai_suggested_label": intended,
                "runtime_model_label": prediction["model_label"],
                "runtime_confidence": f"{float(prediction['confidence']):.6f}",
                "runtime_decision": prediction["decision"],
                "runtime_route": prediction["route"],
                "ai_needs_human_attention": str(needs_attention).lower(),
                "ai_rationale": LABEL_RATIONALES[intended],
                "draft_status": "ai_assisted_draft_not_human_review",
            }
        )
    path = DRAFT_ROOT / "p1_gold_ai_draft.csv"
    attention_path = DRAFT_ROOT / "p1_gold_ai_attention.csv"
    fields = list(rows[0])
    write_csv(path, rows, fields)
    write_csv(
        attention_path,
        [row for row in rows if row["ai_needs_human_attention"] == "true"],
        fields,
    )
    return {
        "output": str(path),
        "attention_output": str(attention_path),
        "rows": len(rows),
        "needs_human_attention": attention,
    }


def generate_challenge(runtime: IntentRuntime) -> dict[str, Any]:
    source = read_csv(ROOT / "data/annotation/challenge_annotation_queue_v1.csv")
    work = {
        row["challenge_id"]: row
        for row in read_csv(ROOT / "data/annotation/review_work/p1_challenge_review.csv")
    }
    predictions = predict_rows(runtime, [row["text"] for row in source])
    rows: list[dict[str, Any]] = []
    for row, prediction in zip(source, predictions, strict=True):
        challenge_type = row["challenge_type"]
        suggested = json.loads(row["suggested_intents"])
        if challenge_type == "low_information":
            intents: list[str] = []
            rationale = "信息不足，AI 草稿建议不强行指定单一业务意图。"
        elif challenge_type == "near_boundary":
            intents = [str(prediction["model_label"])]
            rationale = "边界案例暂取发布意图模型的首选标签，必须人工复核。"
        elif challenge_type == "out_of_scope":
            intents = ["out_of_scope"]
            rationale = "挑战类型明确为非电商范围，但仍需人工检查文本是否确实 OOS。"
        else:
            intents = [str(value) for value in suggested[:2]]
            rationale = "多意图草稿沿用生成阶段候选，必须人工逐项确认。"
        rows.append(
            {
                "batch_id": work[row["challenge_id"]]["batch_id"],
                "challenge_id": row["challenge_id"],
                "challenge_type": challenge_type,
                "text": row["text"],
                "source_row_sha256": work[row["challenge_id"]]["source_row_sha256"],
                "ai_suggested_intents": json.dumps(intents, ensure_ascii=False),
                "runtime_model_label": prediction["model_label"],
                "runtime_confidence": f"{float(prediction['confidence']):.6f}",
                "ai_rationale": rationale,
                "draft_status": "ai_assisted_draft_not_human_review",
            }
        )
    path = DRAFT_ROOT / "p1_challenge_ai_draft.csv"
    write_csv(path, rows, list(rows[0]))
    return {"output": str(path), "rows": len(rows)}


def generate_routing(runtime: IntentRuntime) -> dict[str, Any]:
    source = read_jsonl(ROOT / "reports/eval/routing_800_v1_review_queue.jsonl")
    work = {
        row["case_id"]: row
        for row in read_csv(ROOT / "data/annotation/review_work/p9_routing_review.csv")
    }
    predictions = predict_rows(runtime, [str(row["text"]) for row in source])
    rows: list[dict[str, Any]] = []
    runtime_disagreement = 0
    for row, prediction in zip(source, predictions, strict=True):
        route, decision = ROUTING_CONTRACTS[str(row["category"])]
        verdict = (
            "accept"
            if (row.get("proposed_route") == route and row.get("proposed_decision") == decision)
            else "correct"
        )
        disagrees = prediction["route"] != route or prediction["decision"] != decision
        runtime_disagreement += int(disagrees)
        rows.append(
            {
                "batch_id": work[str(row["case_id"])]["batch_id"],
                "case_id": row["case_id"],
                "category": row["category"],
                "text": row["text"],
                "previous_user_text": row.get("previous_user_text") or "",
                "source_row_sha256": row["source_row_sha256"],
                "proposed_route": row.get("proposed_route") or "",
                "proposed_decision": row["proposed_decision"],
                "ai_verdict": verdict,
                "ai_adjudicated_route": route or "",
                "ai_adjudicated_decision": decision,
                "runtime_route": prediction["route"],
                "runtime_decision": prediction["decision"],
                "runtime_confidence": f"{float(prediction['confidence']):.6f}",
                "ai_needs_human_attention": str(disagrees).lower(),
                "ai_rationale": (
                    "按挑战类别的语义契约生成草稿；运行时输出仅用于暴露模型分歧，不能作为人工金标。"
                ),
                "draft_status": "ai_assisted_draft_not_human_review",
            }
        )
    path = DRAFT_ROOT / "p9_routing_ai_draft.csv"
    attention_path = DRAFT_ROOT / "p9_routing_ai_attention.csv"
    fields = list(rows[0])
    write_csv(path, rows, fields)
    write_csv(
        attention_path,
        [row for row in rows if row["ai_needs_human_attention"] == "true"],
        fields,
    )
    return {
        "output": str(path),
        "attention_output": str(attention_path),
        "rows": len(rows),
        "runtime_disagreement": runtime_disagreement,
    }


def generate_tools() -> dict[str, Any]:
    source = read_jsonl(ROOT / "reports/eval/tool_selection_1000_v1_review_queue.jsonl")
    work = {
        row["case_id"]: row
        for row in read_csv(ROOT / "data/annotation/review_work/p9_tool_selection_review.csv")
    }
    rows: list[dict[str, Any]] = []
    for row in source:
        tools = TOOL_CONTRACTS[str(row["category"])]
        proposed = [str(value) for value in row.get("proposed_tools", [])]
        verdict = "accept" if proposed == tools else "correct"
        rows.append(
            {
                "batch_id": work[str(row["case_id"])]["batch_id"],
                "case_id": row["case_id"],
                "category": row["category"],
                "text": row["text"],
                "previous_user_text": row.get("previous_user_text") or "",
                "source_row_sha256": row["source_row_sha256"],
                "proposed_tools": json.dumps(proposed, ensure_ascii=False),
                "ai_verdict": verdict,
                "ai_adjudicated_tools": json.dumps(tools, ensure_ascii=False),
                "ai_rationale": ("按业务类别的确定性工具契约生成草稿；该结论不是独立人工判断。"),
                "draft_status": "ai_assisted_draft_not_human_review",
            }
        )
    path = DRAFT_ROOT / "p9_tool_selection_ai_draft.csv"
    write_csv(path, rows, list(rows[0]))
    return {"output": str(path), "rows": len(rows)}


def e2e_draft(row: dict[str, Any]) -> tuple[int, int, bool, str, str]:
    turns = row["turns"]
    answers = [str(turn.get("assistant_answer") or "").strip() for turn in turns]
    statuses = [str(turn.get("status") or "") for turn in turns]
    if any(not answer for answer in answers):
        return 1, 2, False, "fail", "存在空回答，必须人工检查。"
    scenario = str(row["scenario"])
    expected_terminal = {
        "missing_after_sales_slots": "needs_clarification",
        "fail_search_products": "insufficient_evidence",
        "fail_after_sales": "tool_failed",
        "fail_order_lookup": "tool_failed",
        "invalid_citation": "validation_failed",
        "invalid_product": "validation_failed",
    }.get(scenario)
    if expected_terminal is not None and expected_terminal not in statuses:
        return 2, 3, True, "fail", f"场景预期出现 {expected_terminal}，实际状态为 {statuses}。"
    return 4, 4, True, "pass", "回答非空且终态符合脚本化异常/正常场景契约。"


def generate_e2e() -> dict[str, Any]:
    source = read_jsonl(ROOT / "reports/eval/e2e_360_v1_manual_review.jsonl")
    work = {
        row["case_id"]: row
        for row in read_csv(ROOT / "data/annotation/review_work/p9_e2e_review.csv")
    }
    rows: list[dict[str, Any]] = []
    failures = 0
    for row in source:
        relevance, clarity, safe, verdict, rationale = e2e_draft(row)
        failures += int(verdict == "fail")
        conversation = work[str(row["case_id"])]["conversation"]
        rows.append(
            {
                "batch_id": work[str(row["case_id"])]["batch_id"],
                "case_id": row["case_id"],
                "category": row["category"],
                "scenario": row["scenario"],
                "conversation": conversation,
                "review_input_sha256": row["review_input_sha256"],
                "ai_relevance_score": relevance,
                "ai_clarity_score": clarity,
                "ai_safe_and_helpful": str(safe).lower(),
                "ai_verdict": verdict,
                "ai_rationale": rationale,
                "draft_status": "ai_assisted_draft_not_human_review",
            }
        )
    path = DRAFT_ROOT / "p9_e2e_ai_draft.csv"
    write_csv(path, rows, list(rows[0]))
    return {"output": str(path), "rows": len(rows), "draft_failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AI-assisted review drafts")
    parser.add_argument("--runtime", type=Path, default=Path("models/intent_classifier/current"))
    args = parser.parse_args()
    DRAFT_ROOT.mkdir(parents=True, exist_ok=True)
    runtime = IntentRuntime(args.runtime)
    result = {
        "schema_version": "1.0",
        "evaluation_status": "ai_assisted_draft_not_human_review",
        "warning": (
            "These files are suggestions only. They do not satisfy independent human review, "
            "must not populate reviewer identities, and cannot promote release evidence."
        ),
        "model_version": runtime.metadata.model_version,
        "p1_gold": generate_p1(runtime),
        "p1_challenge": generate_challenge(runtime),
        "p9_routing": generate_routing(runtime),
        "p9_tool_selection": generate_tools(),
        "p9_e2e": generate_e2e(),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
