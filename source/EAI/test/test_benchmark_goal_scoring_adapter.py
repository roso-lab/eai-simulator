from __future__ import annotations

import json

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_scoring_adapter import (
    build_archive_index,
    build_scoring_adapter_metadata,
)


def test_scoring_sidecar_is_raw_only_and_has_no_protocol_branch(tmp_path):
    task = build_goal_task_package("factory")["public_task"]
    (tmp_path / "participant_trace.json").write_text(
        json.dumps({"events": [], "actions": []}), encoding="utf-8")
    metadata = build_scoring_adapter_metadata(
        task, episode_id="episode-1", output_root=tmp_path)
    assert metadata["raw_only"] is True
    assert metadata["not_protocol_score"] is True
    assert metadata["branch_id"] is None
    assert metadata["branch_status"] == "single_episode_no_protocol_branch"
    assert metadata["execution_validated"] is False
    assert metadata["scoreable"] is False
    assert metadata["archive_index"]["files"]["participant_trace.json"]["bytes"] > 0


def test_archive_index_distinguishes_missing_files(tmp_path):
    index = build_archive_index(tmp_path)
    assert index["raw_only"] is True
    assert index["not_protocol_score"] is True
    assert index["files"] == {}
    assert index["counts"]["events"] is None


def test_scoring_sidecar_rejects_invalid_episode_or_task():
    with pytest.raises(ValueError):
        build_scoring_adapter_metadata({}, episode_id="episode", output_root="/tmp")
    task = build_goal_task_package("factory")["public_task"]
    with pytest.raises(ValueError):
        build_scoring_adapter_metadata(task, episode_id="", output_root="/tmp")


@pytest.mark.parametrize("payload,expected", [
    ({}, None), ({"report": {}}, None), ({"report": {"events": None}}, None),
    ({"report": {"events": []}}, 0),
    ({"report": {"events": [{"type": "episode_started"}]}}, 1),
])
def test_missing_event_evidence_is_not_counted_as_zero(tmp_path, payload, expected):
    (tmp_path / "goal_session_report.json").write_text(json.dumps(payload))
    assert build_archive_index(tmp_path)["counts"]["events"] == expected
