from __future__ import annotations

from pathlib import Path

from evals.tools.build_suite import EXPECTED_COUNTS, build_cases, write_suite
from evals.tools.run import load_suite


def test_tools_suite_is_balanced_and_deterministic() -> None:
    first = Path("tests/.generated-tools-first.jsonl")
    second = Path("tests/.generated-tools-second.jsonl")
    try:
        first_manifest = write_suite(first)
        second_manifest = write_suite(second)
        cases = load_suite(first)

        assert len(cases) == 1000
        assert first_manifest["categories"] == EXPECTED_COUNTS
        assert first_manifest["sha256"] == second_manifest["sha256"]
        assert first.read_bytes() == second.read_bytes()
        assert len({case["case_id"] for case in build_cases()}) == 1000
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)
