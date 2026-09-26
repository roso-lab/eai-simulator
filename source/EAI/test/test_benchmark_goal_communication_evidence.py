"""Disk producer/replay tests for Stage70 raw communication evidence."""

from copy import deepcopy
import hashlib
import json

import pytest

from demo.benchmark.communication import (
    CommunicationRuntime,
    validate_communication_report,
)
from demo.benchmark.goal_communication_evidence import (
    CONDITION_SPEC_SCHEMA,
    EXPECTED_MANIFEST_FILE,
    ROW_SCHEMA,
    SOURCE_ARCHIVE_SCHEMA,
    build_episode_source_row,
    freeze_expected_manifest,
    verify_communication_evidence_archive,
    write_episode_source_row,
)
from demo.benchmark.goal_communication_source_binding import (
    EXPECTED_MANIFEST_SCHEMA,
)
from demo.benchmark.goal_progress_evidence import (
    GoalProgressRecorder,
    replay_progress_evidence,
)
from demo.benchmark.goal_communication_tool import GoalCommunicationTools
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_scoring_adapter import build_archive_index
from demo.benchmark.goal_scoring_adapter import SCORING_ADAPTER_SCHEMA
from demo.benchmark.provenance import canonical_sha256


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")


def _public_task():
    return {
        "task_id": "task0",
        "mission": {"scene_key": "city"},
        "required_outcomes": [{"id": "milestone0"}],
        "budgets": {"simulation_s": 10.0},
    }


def _contract():
    return {
        "pools": {
            "p2p": {"capacity_bps": 100000.0, "ttl_s": 10.0},
            "backhaul_uplink": {"capacity_bps": 100000.0, "ttl_s": 10.0},
            "backhaul_downlink": {"capacity_bps": 100000.0, "ttl_s": 10.0},
        },
        "access": {"*": {"capacity_bps": 100000.0}},
    }


def _manifest(public_task=None, *, episode_id="ep0", relpath="episodes/ep0"):
    public_task = public_task or _public_task()
    task_id = public_task["task_id"]
    initial_contract = CommunicationRuntime(_contract()).report()["initial_contract"]
    spec = {
        "schema": CONDITION_SPEC_SCHEMA,
        "version": 1,
        "family": "fixed_delay",
        "condition_id": "delay-0",
        "initial_contract": initial_contract,
        "final_contract": initial_contract,
        "condition_events": [],
    }
    cell = {
        "task_id": task_id,
        "base_instance_id": "base0",
        "family": "fixed_delay",
        "condition_id": "delay-0",
        "channel_seed": "seed0",
        "episode_id": episode_id,
        "episode_relpath": relpath,
        "condition_spec": spec,
        "condition_spec_sha256": canonical_sha256(spec),
    }
    return {
        "schema": EXPECTED_MANIFEST_SCHEMA,
        "version": 1,
        "frozen_before_execution": True,
        "frozen_at": "2026-09-26T00:00:00Z",
        "task_id": task_id,
        "contract_sha256": canonical_sha256(public_task),
        "evaluator_ref": "stage70-fixture",
        "metric_name": "MCR_comm",
        "excluded_message_ids": ["benchmark-control"],
        "archive_schema": SOURCE_ARCHIVE_SCHEMA,
        "archive_version": 1,
        "transport_report_schema": "eai.benchmark.communication",
        "transport_report_version": 2,
        "shared_transport_runtime_ref": "runner.shared_communication_runtime",
        "cells": [cell],
        "release": False,
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
    }


def _episode(matrix_root, manifest, *, messages=False):
    cell = manifest["cells"][0]
    root = matrix_root / cell["episode_relpath"]
    root.mkdir(parents=True)
    public = _public_task()
    runtime = CommunicationRuntime(cell["condition_spec"]["initial_contract"])
    if messages:
        runtime.publish(
            message_id="model-message", producer_id="robot:r1",
            recipients=["robot:r2"], channel="observation",
            estimate={"value": 1}, valid_until_s=8.0,
            pool="p2p", direction="p2p")
        runtime.advance(1.0)
    transport = runtime.report()
    transport_replay = validate_communication_report(transport)
    assert transport_replay["validated"] is True

    recorder = GoalProgressRecorder(public, cell["episode_id"])
    recorder.append({
        "episode_id": cell["episode_id"],
        "time_s": 0.0,
        "clock_source": "contract_clock",
        "evaluator_ref": "progress-reader",
        "predicates": {"milestone0": {"satisfied": None,
                                       "evidence_refs": []}},
    }, boundary="initial", host_time_s=0.0)
    recorder.append({
        "episode_id": cell["episode_id"],
        "time_s": 1.0,
        "clock_source": "contract_clock",
        "evaluator_ref": "progress-reader",
        "predicates": {"milestone0": {"satisfied": True,
                                       "evidence_refs": ["native://m0"]}},
    }, boundary="terminal", host_time_s=1.0)
    progress = recorder.report(termination_reason="participant_stopped",
                               host_cutoff_s=1.0)
    progress_replay = replay_progress_evidence(
        progress, public, episode_id=cell["episode_id"])

    episode_manifest = {
        "schema": "eai.benchmark.formal_goal_episode",
        "version": 3,
        "episode_id": cell["episode_id"],
        "task_id": cell["task_id"],
        "scene_key": "city",
        "public_task_sha256": canonical_sha256(public),
        "execution_validated": False,
        "scoreable": False,
    }
    session = {
        "episode_id": cell["episode_id"],
        "report": {
            "episode_id": cell["episode_id"],
            "task_id": cell["task_id"],
            "current_time_s": 1.0,
            "events": [{
                "schema": "eai.benchmark.goal_event", "version": 1,
                "sequence": 0, "time_s": 0.0, "type": "episode_started",
                "payload": {"public_task_digest": canonical_sha256(public)},
            }],
            "actions": [],
        },
    }
    tool_report = {
        "schema": "eai.benchmark.goal_communication_tools_report",
        "version": 1,
        "episode_id": cell["episode_id"],
        "profile": {
            "schema": "eai.benchmark.goal_communication_tools",
            "version": 1,
            "episode_id": cell["episode_id"],
            "public_task_sha256": canonical_sha256(public),
            "endpoints": {"cloud": "cloud", "edges": {},
                          "robots": ["r1", "r2"]},
            "allowed_directions": {
                "table": [{"producer": "robot:<agent_id>",
                           "recipient": "robot:<agent_id>",
                           "pool": "p2p", "direction": "p2p"}],
                "everything_else": "endpoint_pair_forbidden",
            },
        },
        "communication": transport,
        "release": False,
        "release_eligible": False,
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
    }
    result = {
        "schema": "eai.benchmark.formal_goal_episode_result",
        "version": 3,
        "episode_id": cell["episode_id"],
        "task_id": cell["task_id"],
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    values = {
        "episode_manifest.json": episode_manifest,
        "public_task.json": public,
        "goal_session_report.json": session,
        "communication_transport_report.json": transport,
        "communication_transport_replay.json": transport_replay,
        "communication_tool_report.json": tool_report,
        "goal_progress_evidence.json": progress,
        "goal_progress_replay.json": progress_replay,
        "result.json": result,
    }
    for name, value in values.items():
        _write(root / name, value)
    scoring = {
        "schema": SCORING_ADAPTER_SCHEMA,
        "version": 1,
        "episode_id": cell["episode_id"],
        "task_id": cell["task_id"],
        "task_contract_sha256": canonical_sha256(public),
        "archive_index": build_archive_index(root),
        "release": False,
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
    }
    _write(root / "scoring_adapter_metadata.json", scoring)
    return root


def _tree_hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _frozen_episode(tmp_path, *, messages=False):
    root = tmp_path / "matrix"
    manifest = _manifest()
    digest = freeze_expected_manifest(root, manifest)
    episode = _episode(root, manifest, messages=messages)
    return root, manifest, digest, episode


def test_disk_producer_zero_message_and_read_only_verifier(tmp_path):
    root, manifest, digest, episode = _frozen_episode(tmp_path)
    row = build_episode_source_row(root, episode, manifest["cells"][0])
    assert row["schema"] == ROW_SCHEMA
    assert row["source_bindings"]["expected_manifest_sha256"] == digest
    assert row["source_bindings"]["transport_replay_validated"] is True
    assert row["condition_metrics"]["communication_cost"]["B_offer_bits"] == 0
    assert row["condition_metrics"]["communication_cost"]["B_link_bits"] == 0
    assert row["condition_metrics"]["task_performance"]["MCR_comm"] is None
    assert row["task_performance_mapping_status"] == (
        "na_not_frozen_stage70_no_inference")
    write_episode_source_row(root, row)

    before = _tree_hashes(root)
    result = verify_communication_evidence_archive(root)
    after = _tree_hashes(root)
    assert before == after
    assert result["status"] == "raw_only_incomplete"
    assert result["verified_row_count"] == 1
    assert result["source_matrix"]["diagnostic_only_count"] == 1
    assert result["source_matrix"]["accepted_raw_only_count"] == 0
    assert result["source_matrix"]["formal_scoring_wired"] is False
    assert result["source_matrix"]["dimension_status"] == (
        "na_dimension_source_not_wired")


def test_post_runner_producer_reads_the_actual_episode_archive(tmp_path):
    class Adapter:
        def manifest(self):
            return {
                "kind": "participant", "id": "stage70-runner", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter",
                "interface_version": 1, "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control",
            }

        def initialize(self, public_task, participant_api):
            pass

        def act(self, events):
            return []

        def close(self):
            pass

    class Backend:
        def execute(self, *args, **kwargs):
            raise AssertionError("no task is dispatched in the source fixture")

    bundle = build_goal_task_package("warehouse_nvd")
    public = bundle["public_task"]
    root = tmp_path / "matrix"
    manifest = _manifest(public, episode_id="runner-ep",
                         relpath="episodes/runner-ep")
    freeze_expected_manifest(root, manifest)
    runtime = CommunicationRuntime(
        manifest["cells"][0]["condition_spec"]["initial_contract"])
    ids = [row["id"] for row in public["candidate_robot_pool"]]
    tools = GoalCommunicationTools(
        public, episode_id="runner-ep",
        endpoints={"edges": {}, "robots": ids}, runtime=runtime)

    predicate_ids = [row["id"] for row in public["required_outcomes"]]
    def progress_reader(*, episode_id, boundary, trusted_report):
        return {
            "episode_id": episode_id,
            "time_s": trusted_report["session"]["current_time_s"],
            "clock_source": "contract_clock",
            "evaluator_ref": "stage70-progress-reader",
            "predicates": {predicate: {"satisfied": None, "evidence_refs": []}
                           for predicate in predicate_ids},
        }

    episode = root / "episodes" / "runner-ep"
    regions = {row["id"]: "rack_aisle_a"
               for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {
        "region_id": row["physical_properties"]["initial_region_id"]}
        for row in public["public_world"]["objects"]}
    result = FormalGoalEpisodeRunner(
        public, bundle["participant_api"], Adapter(), Backend(),
        output_root=episode, episode_id="runner-ep",
        shared_communication_runtime=runtime,
        communication_tools=tools,
        progress_reader=progress_reader,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert result["error"] is None

    row = build_episode_source_row(root, episode, manifest["cells"][0])
    write_episode_source_row(root, row)
    verified = verify_communication_evidence_archive(root)
    assert verified["verified_row_count"] == 1
    assert verified["invalid_row_count"] == 0
    assert row["source_archive"]["files"][
        "communication_transport_report.json"]["sha256"] == hashlib.sha256(
            (episode / "communication_transport_report.json").read_bytes()).hexdigest()
    assert row["condition_metrics"]["task_performance"]["status"] == (
        "na_missing_milestones")


def test_create_once_manifest_is_idempotent_but_never_overwritten(tmp_path):
    root = tmp_path / "matrix"
    manifest = _manifest()
    first = freeze_expected_manifest(root, manifest)
    assert freeze_expected_manifest(root, deepcopy(manifest)) == first
    changed = deepcopy(manifest)
    changed["evaluator_ref"] = "post-result-evaluator"
    with pytest.raises(FileExistsError, match="immutable"):
        freeze_expected_manifest(root, changed)
    assert json.loads((root / EXPECTED_MANIFEST_FILE).read_text())["evaluator_ref"] == (
        "stage70-fixture")

    late = tmp_path / "late"
    late.mkdir()
    (late / "episode-result.json").write_text("{}")
    with pytest.raises(FileExistsError, match="before matrix artifacts"):
        freeze_expected_manifest(late, manifest)


@pytest.mark.parametrize("mutation", [
    "transport_report_missing",
    "transport_report_tampered",
    "transport_replay_forged",
    "endpoint_profile_missing",
    "progress_replay_forged",
    "archive_index_tampered",
    "scoring_task_digest_tampered",
    "tool_transport_substitution",
    "session_task_digest_missing",
    "archive_index_omitted_known_file",
])
def test_disk_sources_fail_closed_without_tool_fallback(tmp_path, mutation):
    root, manifest, _, episode = _frozen_episode(tmp_path, messages=True)
    cell = manifest["cells"][0]
    if mutation == "transport_report_missing":
        (episode / "communication_transport_report.json").unlink()
    elif mutation == "transport_report_tampered":
        report = json.loads((episode / "communication_transport_report.json").read_text())
        report["time_s"] = 9.0
        _write(episode / "communication_transport_report.json", report)
    elif mutation == "transport_replay_forged":
        replay = json.loads((episode / "communication_transport_replay.json").read_text())
        replay["validated"] = False
        _write(episode / "communication_transport_replay.json", replay)
    elif mutation == "endpoint_profile_missing":
        report = json.loads((episode / "communication_tool_report.json").read_text())
        report.pop("profile")
        _write(episode / "communication_tool_report.json", report)
    elif mutation == "progress_replay_forged":
        replay = json.loads((episode / "goal_progress_replay.json").read_text())
        replay["status"] = "failed"
        _write(episode / "goal_progress_replay.json", replay)
    elif mutation == "archive_index_tampered":
        scoring = json.loads((episode / "scoring_adapter_metadata.json").read_text())
        scoring["archive_index"]["files"]["public_task.json"]["sha256"] = "0" * 64
        _write(episode / "scoring_adapter_metadata.json", scoring)
    elif mutation == "scoring_task_digest_tampered":
        scoring = json.loads((episode / "scoring_adapter_metadata.json").read_text())
        scoring["task_contract_sha256"] = "0" * 64
        _write(episode / "scoring_adapter_metadata.json", scoring)
    elif mutation == "tool_transport_substitution":
        tool = json.loads((episode / "communication_tool_report.json").read_text())
        tool["communication"] = CommunicationRuntime(_contract()).report()
        tool["communication"]["time_s"] = 3.0
        _write(episode / "communication_tool_report.json", tool)
    elif mutation == "session_task_digest_missing":
        session = json.loads((episode / "goal_session_report.json").read_text())
        session["report"]["events"][0]["payload"].pop("public_task_digest")
        _write(episode / "goal_session_report.json", session)
    else:
        _write(episode / "participant_trace.json", {
            "episode_id": cell["episode_id"], "events": [], "actions": []})
    with pytest.raises(ValueError):
        build_episode_source_row(root, episode, cell)


def test_post_seal_source_tamper_rejects_previously_written_row(tmp_path):
    root, manifest, _, episode = _frozen_episode(tmp_path, messages=True)
    row = build_episode_source_row(root, episode, manifest["cells"][0])
    write_episode_source_row(root, row)
    report = json.loads((episode / "communication_transport_report.json").read_text())
    report["contract"]["pools"]["p2p"]["fixed_delay_s"] = 4.0
    _write(episode / "communication_transport_report.json", report)

    result = verify_communication_evidence_archive(root)
    assert result["status"] == "raw_only_source_failures"
    assert result["invalid_row_count"] == 1
    assert result["source_matrix"]["rejected_count"] == 1
    assert result["source_matrix"]["accepted_raw_only_count"] == 0
    assert "row_disk_recomputation_failed" in result["errors"][0]["reason"]
    assert result["source_matrix"]["rows"][0]["measurement"].get(
        "B_offer_bits") is None


def test_duplicate_and_unexpected_rows_remain_separate_diagnostics(tmp_path):
    root, manifest, _, episode = _frozen_episode(tmp_path)
    row = build_episode_source_row(root, episode, manifest["cells"][0])
    path = write_episode_source_row(root, row)
    duplicate = path.parent / ("1" * 64 + ".json")
    _write(duplicate, row)
    unexpected_row = deepcopy(row)
    unexpected_row["condition_id"] = "post-result-extra"
    unexpected_row["source_bindings"]["condition_id"] = "post-result-extra"
    unexpected_identity = {key: unexpected_row[key] for key in (
        "task_id", "base_instance_id", "family", "condition_id", "channel_seed")}
    unexpected = path.parent / (canonical_sha256(unexpected_identity) + ".json")
    _write(unexpected, unexpected_row)

    result = verify_communication_evidence_archive(root)
    matrix = result["source_matrix"]
    assert matrix["duplicate_row_count"] == 2
    assert matrix["unexpected_row_count"] == 1
    assert matrix["rejected_count"] == 3
    assert matrix["accepted_raw_only_count"] == 0


def test_forged_row_release_flag_is_rejected_not_echoed(tmp_path):
    root, manifest, _, episode = _frozen_episode(tmp_path)
    row = build_episode_source_row(root, episode, manifest["cells"][0])
    path = write_episode_source_row(root, row)
    forged = json.loads(path.read_text())
    forged["release"] = True
    forged["source_bindings"]["scoreable"] = True
    _write(path, forged)

    result = verify_communication_evidence_archive(root)
    record = result["source_matrix"]["rows"][0]
    assert record["row_status"] == "rejected"
    assert "row_release_must_be_false" in record["source_reasons"]
    assert "source_bindings_scoreable_must_be_false" in record["source_reasons"]
    assert record["raw_input_embedded"] is False
    assert "raw_input" not in record


def test_writer_refuses_a_row_changed_after_disk_recomputation(tmp_path):
    root, manifest, _, episode = _frozen_episode(tmp_path)
    row = build_episode_source_row(root, episode, manifest["cells"][0])
    row["source_bindings"]["archive_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="differs from disk recomputation"):
        write_episode_source_row(root, row)
    assert not (root / "rows").exists()


def test_episode_assignment_and_condition_spec_are_frozen_before_run(tmp_path):
    manifest = _manifest()
    bad_digest = deepcopy(manifest)
    bad_digest["cells"][0]["condition_spec"]["condition_id"] = "changed"
    with pytest.raises(ValueError, match="condition_spec id conflicts"):
        freeze_expected_manifest(tmp_path / "bad-digest", bad_digest)

    bad_path = _manifest(relpath="../outside")
    with pytest.raises(ValueError, match="below the matrix root"):
        freeze_expected_manifest(tmp_path / "bad-path", bad_path)

    root, frozen, _, episode = _frozen_episode(tmp_path / "bound")
    foreign = deepcopy(frozen["cells"][0])
    foreign["episode_id"] = "another-episode"
    with pytest.raises(ValueError, match="differs from the frozen manifest"):
        build_episode_source_row(root, episode, foreign)


def test_episode_symlink_component_is_rejected(tmp_path):
    root, manifest, _, episode = _frozen_episode(tmp_path)
    real = root / "episodes" / "real-episode"
    episode.rename(real)
    episode.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="contains a symlink"):
        build_episode_source_row(root, episode, manifest["cells"][0])


def test_missing_report_is_not_reinterpreted_as_zero_communication(tmp_path):
    root, manifest, _, episode = _frozen_episode(tmp_path)
    row = build_episode_source_row(root, episode, manifest["cells"][0])
    assert row["condition_metrics"]["communication_cost"]["B_offer_bits"] == 0
    write_episode_source_row(root, row)
    (episode / "communication_transport_report.json").unlink()
    result = verify_communication_evidence_archive(root)
    assert result["source_matrix"]["rows"][0]["row_status"] == "rejected"
    measurement = result["source_matrix"]["rows"][0]["measurement"]
    assert measurement.get("B_offer_bits") is None
    assert measurement["metric_value"] is None
    assert result["errors"]


def test_actual_condition_schedule_must_match_the_frozen_spec(tmp_path):
    root, manifest, _, episode = _frozen_episode(tmp_path)
    runtime = CommunicationRuntime(
        manifest["cells"][0]["condition_spec"]["initial_contract"])
    runtime.set_conditions({"fixed_delay_s": 2.0}, pool="p2p", time_s=0.0)
    report = runtime.report()
    _write(episode / "communication_transport_report.json", report)
    _write(episode / "communication_transport_replay.json",
           validate_communication_report(report))
    tool = json.loads((episode / "communication_tool_report.json").read_text())
    tool["communication"] = report
    _write(episode / "communication_tool_report.json", tool)
    with pytest.raises(ValueError, match="final contract differs"):
        build_episode_source_row(root, episode, manifest["cells"][0])
