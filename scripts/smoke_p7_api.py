from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

STATE_PATH = Path("reports/api/p7_recovery_state.json")
REPORT_PATH = Path("reports/api/p7_smoke.json")


def require(response: httpx.Response, expected: int = 200) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {response.text[:1000]}"
        )
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("API response is not a JSON object")
    return value


def login(client: httpx.Client, principal_id: str, role: str) -> str:
    data = require(
        client.post(
            "/api/v1/auth/demo-login",
            json={"principal_id": principal_id, "role": role},
        )
    )
    return str(data["access_token"])


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def phase_decide(base_url: str) -> None:
    suffix = uuid4().hex[:16]
    principal_id = "usr_demo_0005"
    session_id = f"ses_p7_{suffix}"
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        customer_token = login(client, principal_id, "customer")
        created = require(
            client.post(
                "/api/v1/sessions",
                headers=headers(customer_token),
                json={"session_id": session_id},
            ),
            201,
        )
        confirmation = require(
            client.post(
                "/api/v1/after-sales/confirm",
                headers=headers(customer_token),
                json={
                    "session_id": session_id,
                    "order_id": "ord_demo_000005",
                    "item_id": "item_demo_000005_1",
                    "request_type": "return",
                    "reason_code": "DO_NOT_WANT",
                    "description": "P7 容器恢复冒烟：用户确认申请退货。",
                    "idempotency_key": f"idem_p7_{suffix}",
                },
            )
        )
        agent_token = login(client, "agent_demo_0001", "agent")
        approval_id = str(confirmation["approval_id"])
        decision = require(
            client.post(
                f"/api/v1/approvals/{approval_id}/decision",
                headers=headers(agent_token),
                json={
                    "decision": "approved",
                    "state_version": confirmation["state_version"],
                    "reason": "P7 冒烟审批：材料完整，同意 mock 审批。",
                },
            )
        )
    state = {
        "phase": "decision_committed_graph_not_resumed",
        "principal_id": principal_id,
        "session_id": session_id,
        "approval_id": approval_id,
        "decision": decision["status"],
        "decision_state_version": decision["state_version"],
        "api_container_restart_required": True,
        "session_created": created["session_id"] == session_id,
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False, indent=2))


def phase_resume(base_url: str) -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        agent_token = login(client, "agent_demo_0001", "agent")
        first = require(
            client.post(
                f"/api/v1/approvals/{state['approval_id']}/resume",
                headers=headers(agent_token),
            )
        )
        duplicate = require(
            client.post(
                f"/api/v1/approvals/{state['approval_id']}/resume",
                headers=headers(agent_token),
            )
        )
        customer_token = login(client, str(state["principal_id"]), "customer")
        session = require(
            client.get(
                f"/api/v1/sessions/{state['session_id']}",
                headers=headers(customer_token),
            )
        )
    if first["status"] != "approved" or first["outcome"] != "mock_action_authorized":
        raise RuntimeError(f"unexpected first resume result: {first}")
    if duplicate.get("duplicate") is not True:
        raise RuntimeError(f"second resume was not idempotent: {duplicate}")
    report = {
        "status": "passed",
        "scenario": "decision_committed_then_api_container_restarted",
        "approval_id": state["approval_id"],
        "session_id": state["session_id"],
        "decision": first["status"],
        "outcome": first["outcome"],
        "revalidation": first.get("revalidation_reason"),
        "duplicate_resume": duplicate["duplicate"],
        "session_read_after_restart": session["session_id"] == state["session_id"],
        "real_money_action": False,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("decide", "resume"))
    parser.add_argument("--base-url", default="http://api:8000")
    args = parser.parse_args()
    if args.phase == "decide":
        phase_decide(args.base_url)
    else:
        phase_resume(args.base_url)


if __name__ == "__main__":
    main()
