from __future__ import annotations

import contextlib
import csv
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

ROOT = Path(os.getenv("COMMERCE_AGENT_ROOT", Path(__file__).resolve().parents[2])).resolve()
P1_GOLD = ROOT / "data/annotation/gold_annotation_queue_v1.csv"
P1_CHALLENGE = ROOT / "data/annotation/challenge_annotation_queue_v1.csv"
ROUTING = ROOT / "reports/eval/routing_800_v1_review_queue.jsonl"
TOOL_SELECTION = ROOT / "reports/eval/tool_selection_1000_v1_review_queue.jsonl"
E2E = ROOT / "reports/eval/e2e_360_v1_manual_review.jsonl"
HTML_PATH = Path(__file__).with_name("index.html")

LABELS = (
    "product_search",
    "product_recommend",
    "product_compare",
    "product_detail",
    "stock_price",
    "order_status",
    "logistics_tracking",
    "cancel_order",
    "return_exchange",
    "refund_progress",
    "after_sales_eligibility",
    "policy_faq",
    "complaint",
    "human_handoff",
    "chitchat",
    "out_of_scope",
)
ROUTES = ("knowledge", "shopping", "order", "after_sales", "human", "general", "safe_reply")
DECISIONS = ("auto_route", "clarify", "safe_reply")
TOOLS = (
    "search_products",
    "check_inventory",
    "get_order_detail",
    "list_recent_orders",
    "track_logistics",
    "check_after_sales_eligibility",
)
P1_AUDIT_COLUMNS = (
    "annotator_1_id",
    "annotator_1_at",
    "annotator_2_id",
    "annotator_2_at",
    "adjudicator_id",
    "adjudicated_at",
    "adjudication_notes",
)
CHALLENGE_AUDIT_COLUMNS = ("reviewer_id", "reviewed_at", "notes")
LOCK = Lock()


class ReviewSubmission(BaseModel):
    queue: str
    case_id: str
    reviewer_id: str
    values: dict[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8-sig", newline="", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        temporary = Path(handle.name)
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def ensure_audit_columns() -> None:
    with LOCK:
        for path, required in (
            (P1_GOLD, P1_AUDIT_COLUMNS),
            (P1_CHALLENGE, CHALLENGE_AUDIT_COLUMNS),
        ):
            fields, rows = read_csv(path)
            missing = [column for column in required if column not in fields]
            if not missing:
                continue
            fields.extend(missing)
            for row in rows:
                for column in missing:
                    row[column] = ""
            write_csv(path, fields, rows)


def truthy(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def fields_complete(row: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(str(row.get(field, "")).strip() for field in fields)


def p1_a1_complete(row: dict[str, Any]) -> bool:
    return fields_complete(
        row, ("annotator_1_text", "annotator_1_label", "annotator_1_id", "annotator_1_at")
    )


def p1_a2_complete(row: dict[str, Any]) -> bool:
    return fields_complete(
        row, ("annotator_2_text", "annotator_2_label", "annotator_2_id", "annotator_2_at")
    )


def p1_adjudicated(row: dict[str, Any]) -> bool:
    return row.get("status") == "adjudicated" and fields_complete(
        row, ("adjudicated_text", "adjudicated_label", "adjudicator_id", "adjudicated_at")
    )


def queue_rows(queue: str) -> list[dict[str, Any]]:
    if queue in {"p1_annotator1", "p1_annotator2", "p1_adjudication"}:
        rows: list[dict[str, Any]] = read_csv(P1_GOLD)[1]
        if queue == "p1_annotator2":
            return [row for row in rows if truthy(row["double_annotation_required"])]
        if queue == "p1_adjudication":
            return [
                row
                for row in rows
                if p1_a1_complete(row)
                and (not truthy(row["double_annotation_required"]) or p1_a2_complete(row))
            ]
        return rows
    if queue == "p1_challenge":
        return list(read_csv(P1_CHALLENGE)[1])
    mapping = {"routing": ROUTING, "tool_selection": TOOL_SELECTION, "e2e": E2E}
    if queue not in mapping:
        raise HTTPException(status_code=404, detail=f"unknown queue: {queue}")
    return read_jsonl(mapping[queue])


def complete(queue: str, row: dict[str, Any]) -> bool:
    if queue == "p1_annotator1":
        return p1_a1_complete(row)
    if queue == "p1_annotator2":
        return p1_a2_complete(row)
    if queue == "p1_adjudication":
        return p1_adjudicated(row)
    if queue == "p1_challenge":
        return row.get("status") == "adjudicated" and bool(row.get("reviewer_id"))
    return row.get("review_status") == "reviewed"


def public_row(queue: str, row: dict[str, Any]) -> dict[str, Any]:
    if queue in {"p1_annotator1", "p1_annotator2"}:
        suffix = "1" if queue.endswith("1") else "2"
        rewrite = truthy(row["requires_independent_human_rewrite"])
        return {
            "case_id": row["annotation_id"],
            "seed_text": row["seed_text"],
            "rewrite_required": rewrite,
            "assigned_intent": row["stratum_intent"] if rewrite else None,
            "text": row[f"annotator_{suffix}_text"] or row["seed_text"],
            "label": row[f"annotator_{suffix}_label"],
        }
    if queue == "p1_adjudication":
        double = truthy(row["double_annotation_required"])
        return {
            "case_id": row["annotation_id"],
            "seed_text": row["seed_text"],
            "stratum_intent": row["stratum_intent"],
            "double_annotation_required": double,
            "annotator_1_text": row["annotator_1_text"],
            "annotator_1_label": row["annotator_1_label"],
            "annotator_1_id": row.get("annotator_1_id"),
            "annotator_2_text": row["annotator_2_text"] if double else None,
            "annotator_2_label": row["annotator_2_label"] if double else None,
            "annotator_2_id": row.get("annotator_2_id") if double else None,
            "text": row["adjudicated_text"] or row["annotator_1_text"],
            "label": row["adjudicated_label"] or row["annotator_1_label"],
            "notes": row.get("adjudication_notes", ""),
        }
    if queue == "p1_challenge":
        intents: list[str] = []
        with contextlib.suppress(json.JSONDecodeError):
            intents = list(json.loads(row.get("adjudicated_intents") or "[]"))
        return {
            "case_id": row["challenge_id"],
            "challenge_type": row["challenge_type"],
            "text": row["text"],
            "intents": intents,
            "notes": row.get("notes", ""),
        }
    return dict(row)


def status_payload() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for queue in (
        "p1_annotator1",
        "p1_annotator2",
        "p1_adjudication",
        "p1_challenge",
        "routing",
        "tool_selection",
        "e2e",
    ):
        rows = queue_rows(queue)
        reviewed = sum(complete(queue, row) for row in rows)
        result[queue] = {"reviewed": reviewed, "pending": len(rows) - reviewed, "total": len(rows)}
    return result


def require_reviewer(value: str) -> str:
    reviewer = value.strip()
    if not reviewer:
        raise HTTPException(status_code=422, detail="reviewer_id is required")
    return reviewer


def require_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="review text is required")
    return text


def require_label(value: Any) -> str:
    label = str(value or "").strip()
    if label not in LABELS:
        raise HTTPException(status_code=422, detail="invalid intent label")
    return label


def update_p1(queue: str, submission: ReviewSubmission) -> None:
    path = P1_CHALLENGE if queue == "p1_challenge" else P1_GOLD
    fields, rows = read_csv(path)
    id_field = "challenge_id" if queue == "p1_challenge" else "annotation_id"
    row = next((item for item in rows if item[id_field] == submission.case_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    reviewer = require_reviewer(submission.reviewer_id)
    values = submission.values
    now = utc_now()
    if queue == "p1_annotator1":
        row["annotator_1_text"] = require_text(values.get("text"))
        row["annotator_1_label"] = require_label(values.get("label"))
        row["annotator_1_id"] = reviewer
        row["annotator_1_at"] = now
    elif queue == "p1_annotator2":
        if not truthy(row["double_annotation_required"]):
            raise HTTPException(status_code=422, detail="row does not require double annotation")
        if reviewer == row.get("annotator_1_id"):
            raise HTTPException(status_code=422, detail="annotator 2 must differ from annotator 1")
        row["annotator_2_text"] = require_text(values.get("text"))
        row["annotator_2_label"] = require_label(values.get("label"))
        row["annotator_2_id"] = reviewer
        row["annotator_2_at"] = now
    elif queue == "p1_adjudication":
        if not p1_a1_complete(row) or (
            truthy(row["double_annotation_required"]) and not p1_a2_complete(row)
        ):
            raise HTTPException(status_code=422, detail="required annotations are incomplete")
        row["adjudicated_text"] = require_text(values.get("text"))
        row["adjudicated_label"] = require_label(values.get("label"))
        row["adjudicator_id"] = reviewer
        row["adjudicated_at"] = now
        row["adjudication_notes"] = str(values.get("notes") or "").strip()
        row["status"] = "adjudicated"
    else:
        raw_intents = values.get("intents", [])
        if not isinstance(raw_intents, list):
            raise HTTPException(status_code=422, detail="intents must be a list")
        intents = [str(value) for value in raw_intents if str(value).strip()]
        if len(intents) != len(set(intents)) or len(intents) > 2:
            raise HTTPException(status_code=422, detail="choose at most two unique intents")
        if any(intent not in LABELS for intent in intents):
            raise HTTPException(status_code=422, detail="challenge contains invalid intent")
        row["adjudicated_intents"] = json.dumps(intents, ensure_ascii=False)
        row["reviewer_id"] = reviewer
        row["reviewed_at"] = now
        row["notes"] = str(values.get("notes") or "").strip()
        row["status"] = "adjudicated"
    write_csv(path, fields, rows)


def update_jsonl(queue: str, submission: ReviewSubmission) -> None:
    mapping = {"routing": ROUTING, "tool_selection": TOOL_SELECTION, "e2e": E2E}
    rows = read_jsonl(mapping[queue])
    row = next((item for item in rows if str(item["case_id"]) == submission.case_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    reviewer = require_reviewer(submission.reviewer_id)
    values = submission.values
    if queue == "routing":
        verdict = values.get("verdict")
        if verdict not in {"accept", "correct"}:
            raise HTTPException(status_code=422, detail="verdict must be accept or correct")
        row["verdict"] = verdict
        if verdict == "correct":
            decision = str(values.get("adjudicated_decision") or "")
            route_value = values.get("adjudicated_route")
            route = None if route_value in {None, ""} else str(route_value)
            if decision not in DECISIONS or (route is not None and route not in ROUTES):
                raise HTTPException(status_code=422, detail="invalid route correction")
            if decision == "auto_route" and route is None:
                raise HTTPException(status_code=422, detail="auto_route requires a route")
            if decision == "clarify" and route is not None:
                raise HTTPException(status_code=422, detail="clarify requires an empty route")
            if decision == "safe_reply" and route != "safe_reply":
                raise HTTPException(status_code=422, detail="safe_reply requires safe_reply route")
            row["adjudicated_decision"] = decision
            row["adjudicated_route"] = route
        else:
            row["adjudicated_decision"] = None
            row["adjudicated_route"] = None
    elif queue == "tool_selection":
        verdict = values.get("verdict")
        if verdict not in {"accept", "correct"}:
            raise HTTPException(status_code=422, detail="verdict must be accept or correct")
        row["verdict"] = verdict
        if verdict == "correct":
            tools = values.get("adjudicated_tools")
            if not isinstance(tools, list) or any(tool not in TOOLS for tool in tools):
                raise HTTPException(status_code=422, detail="invalid adjudicated tools")
            if len(tools) != len(set(tools)):
                raise HTTPException(status_code=422, detail="duplicate adjudicated tools")
            row["adjudicated_tools"] = tools
        else:
            row["adjudicated_tools"] = None
    else:
        relevance, clarity = values.get("relevance_score"), values.get("clarity_score")
        if not isinstance(relevance, int) or isinstance(relevance, bool) or not 1 <= relevance <= 5:
            raise HTTPException(status_code=422, detail="relevance_score must be 1-5")
        if not isinstance(clarity, int) or isinstance(clarity, bool) or not 1 <= clarity <= 5:
            raise HTTPException(status_code=422, detail="clarity_score must be 1-5")
        safe, verdict = values.get("safe_and_helpful"), values.get("verdict")
        if not isinstance(safe, bool) or verdict not in {"pass", "fail"}:
            raise HTTPException(status_code=422, detail="invalid E2E review")
        row.update(
            {
                "relevance_score": relevance,
                "clarity_score": clarity,
                "safe_and_helpful": safe,
                "verdict": verdict,
            }
        )
    row["review_status"] = "reviewed"
    row["reviewer_id"] = reviewer
    row["reviewed_at"] = utc_now()
    row["notes"] = str(values.get("notes") or "").strip() or None
    write_jsonl(mapping[queue], rows)


app = FastAPI(title="CommerceAgent Human Review Assistant")


@app.on_event("startup")
def startup() -> None:
    ensure_audit_columns()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    page = HTML_PATH.read_text(encoding="utf-8")
    return (
        page.replace("%LABELS%", json.dumps(LABELS, ensure_ascii=False))
        .replace("%ROUTES%", json.dumps(ROUTES, ensure_ascii=False))
        .replace("%DECISIONS%", json.dumps(DECISIONS, ensure_ascii=False))
        .replace("%TOOLS%", json.dumps(TOOLS, ensure_ascii=False))
    )


@app.get("/api/status")
def status() -> dict[str, Any]:
    return status_payload()


@app.get("/api/next")
def next_item(queue: str = Query(...)) -> dict[str, Any]:
    pending = [row for row in queue_rows(queue) if not complete(queue, row)]
    return {"item": public_row(queue, pending[0]) if pending else None}


@app.post("/api/review")
def save_review(submission: ReviewSubmission) -> dict[str, Any]:
    with LOCK:
        if submission.queue.startswith("p1_"):
            update_p1(submission.queue, submission)
        elif submission.queue in {"routing", "tool_selection", "e2e"}:
            update_jsonl(submission.queue, submission)
        else:
            raise HTTPException(status_code=404, detail="unknown queue")
    return {"saved": True, "case_id": submission.case_id, "updated_at": utc_now()}
