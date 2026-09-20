from __future__ import annotations

import json
from pathlib import Path

from evals.routing.build_suite import build_cases, write_suite
from evals.routing.run import load_suite, rate, wilson_interval


def test_routing_suite_has_eight_balanced_categories() -> None:
    output = Path("tests/.generated-routing-balanced.jsonl")
    try:
        manifest = write_suite(output)
        cases = load_suite(output)

        assert manifest["rows"] == 800
        assert len(cases) == 800
        assert set(manifest["categories"].values()) == {100}
        assert len({case["text"] for case in cases}) == 800
    finally:
        output.unlink(missing_ok=True)


def test_routing_suite_is_deterministic() -> None:
    first = Path("tests/.generated-routing-first.jsonl")
    second = Path("tests/.generated-routing-second.jsonl")
    try:
        first_manifest = write_suite(first)
        second_manifest = write_suite(second)

        assert first_manifest["sha256"] == second_manifest["sha256"]
        assert first.read_bytes() == second.read_bytes()
        assert build_cases()[0]["case_id"] == "product_consult_001"
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)


def test_wilson_interval_and_rate() -> None:
    lower, upper = wilson_interval(80, 100)
    metric = rate(80, 100)

    assert 0.70 < lower < 0.80 < upper < 0.90
    assert metric["accuracy"] == 0.8
    assert metric["correct"] == 80
    assert metric["total"] == 100


def test_suite_rows_are_valid_json() -> None:
    line = json.dumps(build_cases()[0], ensure_ascii=False)
    assert json.loads(line)["provenance"] == "deterministic_template_challenge"
