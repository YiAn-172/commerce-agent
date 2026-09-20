from __future__ import annotations

import pandas as pd

from scripts.build_intent_dataset import assign_connected_group_keys


def test_connected_groups_are_transitive_across_all_grouping_fields() -> None:
    frame = pd.DataFrame(
        [
            {
                "sample_id": "a",
                "source_dialogue_id": "dialogue-1",
                "template_family": "family-1",
                "semantic_cluster_id": "cluster-a",
            },
            {
                "sample_id": "b",
                "source_dialogue_id": "dialogue-2",
                "template_family": "family-1",
                "semantic_cluster_id": "cluster-b",
            },
            {
                "sample_id": "c",
                "source_dialogue_id": "dialogue-2",
                "template_family": "family-2",
                "semantic_cluster_id": "cluster-c",
            },
            {
                "sample_id": "d",
                "source_dialogue_id": "dialogue-3",
                "template_family": None,
                "semantic_cluster_id": "cluster-d",
            },
        ]
    )

    grouped = assign_connected_group_keys(frame)

    assert grouped.loc[0, "group_key"] == grouped.loc[1, "group_key"]
    assert grouped.loc[1, "group_key"] == grouped.loc[2, "group_key"]
    assert grouped.loc[2, "group_key"] != grouped.loc[3, "group_key"]
