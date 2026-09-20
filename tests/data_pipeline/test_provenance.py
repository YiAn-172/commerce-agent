from pathlib import Path

from packages.contracts.intent import IntentLabel
from packages.data_pipeline.provenance import hash_directory, sha256_file
from packages.data_pipeline.schemas import CandidateRecord, LicenseStatus


def test_directory_hash_is_stable_and_ignores_git(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("二", encoding="utf-8")
    (tmp_path / "a.txt").write_text("一", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "ignored").write_text("changes", encoding="utf-8")

    first = hash_directory(tmp_path)
    (git_dir / "ignored").write_text("other", encoding="utf-8")
    second = hash_directory(tmp_path)

    assert first == second
    assert first[1] == 2
    assert sha256_file(tmp_path / "a.txt") != sha256_file(tmp_path / "b.txt")


def test_candidate_group_key_cannot_depend_on_split() -> None:
    candidate = CandidateRecord(
        sample_id="sample-1",
        text_original="订单到哪里了",
        text_zh="订单到哪里了",
        source_id="fixture",
        source_revision="abcdef0",
        source_row_id="1",
        source_language="zh-CN",
        source_intent="order_status",
        target_intent=IntentLabel.ORDER_STATUS,
        template_family="order_status_short",
        source_dialogue_id="dialogue-1",
        semantic_cluster_id="cluster-1",
        license_status=LicenseStatus.APPROVED,
        provenance="test_fixture",
    )

    assert candidate.group_key == "dialogue-1|order_status_short|cluster-1"
