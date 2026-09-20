from __future__ import annotations

from evals.release.build_report import report_blockers


def verified_reports() -> dict[str, dict[str, object] | None]:
    return {
        "intent_gold": {"evaluation_status": "human_gold_verified"},
        "routing": {
            "suite_status": "human_gold_verified",
            "suite_sha256": "routing-adjudicated",
            "source_suite_sha256": "routing-source",
        },
        "routing_review": {
            "status": "human_gold_verified",
            "adjudicated_sha256": "routing-adjudicated",
            "source_sha256": "routing-source",
        },
        "tools": {
            "suite_status": "human_gold_verified",
            "metrics": {"tool_selection_accuracy": {"status": "not_measured"}},
        },
        "tool_selection": {
            "evaluation_status": "human_gold_verified",
            "suite_sha256": "tool-selection-adjudicated",
            "source_suite_sha256": "tool-selection-source",
            "metrics": {"tool_selection_accuracy": {"accuracy": 0.95}},
        },
        "tool_selection_review": {
            "status": "human_gold_verified",
            "adjudicated_sha256": "tool-selection-adjudicated",
            "source_sha256": "tool-selection-source",
        },
        "e2e": {
            "suite_status": "deterministic_graph_contract_verified",
            "evaluation_status": "verified",
            "judge": {"status": "verified"},
            "manual_review": {"status": "human_review_verified"},
        },
        "load_mixed": {"evaluation_status": "verified"},
        "load_faq_cache": {"evaluation_status": "verified"},
        "security": {"evaluation_status": "verified"},
    }


def test_release_report_fails_closed_for_missing_evidence() -> None:
    blockers = report_blockers(
        {
            "intent_gold": None,
            "routing": {"suite_status": "template_challenge_unreviewed"},
            "routing_review": None,
            "tools": None,
            "tool_selection": None,
            "tool_selection_review": None,
            "e2e": None,
            "load_mixed": None,
            "load_faq_cache": None,
            "security": None,
        }
    )

    assert any("gold_evaluation_v1" in blocker for blocker in blockers)
    assert any("routing suite" in blocker for blocker in blockers)
    assert any("tools_1000" in blocker for blocker in blockers)
    assert any("tool_selection_1000" in blocker for blocker in blockers)
    assert any("security_attacks_v1" in blocker for blocker in blockers)


def test_release_report_accepts_verified_evidence() -> None:
    assert report_blockers(verified_reports()) == []


def test_release_report_keeps_deterministic_only_e2e_blocked() -> None:
    reports = verified_reports()
    reports["e2e"] = {
        "suite_status": "deterministic_graph_contract_verified",
        "evaluation_status": "deterministic_only",
        "judge": {"status": "not_run"},
        "manual_review": {"status": "pending_human_review"},
    }
    blockers = report_blockers(reports)

    assert any("DeepSeek" in blocker for blocker in blockers)
    assert any("manual review" in blocker for blocker in blockers)


def test_release_report_blocks_unreviewed_tool_selection() -> None:
    reports = verified_reports()
    reports["tool_selection"] = {
        "evaluation_status": "diagnostic_measured",
        "metrics": {"tool_selection_accuracy": {"accuracy": 0.808}},
    }
    blockers = report_blockers(reports)

    assert blockers == ["tool-selection suite is not independently reviewed human gold"]


def test_release_report_blocks_unverified_security_exit_gates() -> None:
    reports = verified_reports()
    reports["security"] = {"evaluation_status": "blocked"}
    blockers = report_blockers(reports)

    assert blockers == ["security attack-suite exit gates are not verified"]


def test_release_report_blocks_pending_review_summaries() -> None:
    reports = verified_reports()
    reports["routing_review"] = {"status": "pending_human_review"}
    reports["tool_selection_review"] = {"status": "pending_human_review"}

    blockers = report_blockers(reports)

    assert "routing review summary is not human_gold_verified" in blockers
    assert "tool-selection review summary is not human_gold_verified" in blockers


def test_release_report_blocks_mismatched_adjudicated_hashes() -> None:
    reports = verified_reports()
    routing = reports["routing"]
    tool_selection = reports["tool_selection"]
    assert routing is not None
    assert tool_selection is not None
    routing["suite_sha256"] = "wrong-routing-hash"
    tool_selection["source_suite_sha256"] = "wrong-tool-source-hash"

    blockers = report_blockers(reports)

    assert "routing report does not match the adjudicated suite hash" in blockers
    assert (
        "tool-selection report does not match the reviewed source suite hash" in blockers
    )
