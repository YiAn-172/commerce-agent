from __future__ import annotations

from evals.rag.build_suite import NO_ANSWER_QUERIES


def test_no_answer_pool_is_unique_and_not_a_numbered_template() -> None:
    assert len(NO_ANSWER_QUERIES) == 50
    assert len(set(NO_ANSWER_QUERIES)) == 50
    prefixes = {query[:4] for query in NO_ANSWER_QUERIES}
    assert len(prefixes) >= 30
