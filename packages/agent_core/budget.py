from __future__ import annotations

import hashlib
import json
from typing import Any

from packages.agent_core.contracts import GraphLimits
from packages.agent_core.state import CommerceState


class BudgetExceeded(RuntimeError):
    pass


def consume_node(state: CommerceState, limits: GraphLimits, name: str) -> int:
    count = int(state.get("node_count", 0)) + 1
    if count > limits.max_nodes:
        raise BudgetExceeded(f"node budget exceeded before {name}")
    return count


def consume_llm(state: CommerceState, limits: GraphLimits, name: str) -> int:
    count = int(state.get("llm_call_count", 0)) + 1
    if count > limits.max_llm_calls:
        raise BudgetExceeded(f"LLM budget exceeded before {name}")
    return count


def tool_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{tool_name}:{payload}".encode()).hexdigest()[:24]
    return f"{tool_name}:{digest}"


def consume_tool(
    state: CommerceState,
    limits: GraphLimits,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[int, dict[str, int], str]:
    total = int(state.get("tool_call_count", 0)) + 1
    if total > limits.max_tool_calls:
        raise BudgetExceeded(f"tool budget exceeded before {tool_name}")
    fingerprint = tool_fingerprint(tool_name, arguments)
    counts = dict(state.get("tool_call_counts", {}))
    repeated = counts.get(fingerprint, 0) + 1
    if repeated > limits.max_repeated_tool_call:
        raise BudgetExceeded(f"repeated tool call budget exceeded for {tool_name}")
    counts[fingerprint] = repeated
    return total, counts, fingerprint
