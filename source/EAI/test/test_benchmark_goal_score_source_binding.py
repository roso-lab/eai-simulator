"""Real runner archives reject swapped plan/cost sources without native claims."""

from copy import deepcopy
import hashlib
import json
import shutil

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_scoring_adapter import build_scoring_adapter_metadata


class _PlanOnlyAdapter:
    def manifest(self):
        return {
            "kind": "participant", "id": "source-binding-fixture", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
            "evaluation_track": "goal_driven", "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self.sent = False

    def act(self, events):
        if self.sent:
            return []
        self.sent = True
        robot = "warehouse_ground_1"
        plan = {
            "schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "source-binding-plan", "assigned_team": [robot],
            "tasks": [{
                "task_id": "navigation", "skill": "ground_navigation",
                "arguments": {"region_id": "consolidation_area",
                              "route_ids": ["rack_a_to_consolidation"]},
                "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
                "prerequisites": [], "evidence_refs": [],
                "execution": {"start_s": 0.0, "duration_s": 16.0},
            }],
        }
        return [
            {"schema": "eai.benchmark.goal_action", "version": 1,
             "action_id": action_id, "submitted_at_s": 0.0, "type": kind, "payload": payload}
            for action_id, kind, payload in (
                ("select", "select_team", {"agent_ids": [robot]}),
                ("plan", "submit_plan", {"plan": plan}),
            )
        ]

    def close(self):
        pass


class _NoExecution:
    def execute(self, *args, **kwargs):
        raise AssertionError("fixture submits a plan but does not dispatch")


def _run(root, episode_id):
    bundle = build_goal_task_package("warehouse_nvd")
    public = bundle["public_task"]
    runtime = CommunicationRuntime({"pools": {
        "backhaul_downlink": {"capacity_bps": 1000000}}})
    runtime.publish(
        message_id=f"message:{episode_id}", producer_id="cloud",
        recipients=["warehouse_ground_1"], channel="execution",
        estimate={"fixture_episode": episode_id}, valid_until_s=10,
        pool="backhaul_downlink", direction="downlink")
    runtime.advance(1.0)
    result = FormalGoalEpisodeRunner(
        public, bundle["participant_api"], _PlanOnlyAdapter(), _NoExecution(),
        output_root=root, episode_id=episode_id,
        shared_communication_runtime=runtime,
    ).run(initial_state={
        "robot_regions": {r["id"]: "rack_aisle_a" for r in public["candidate_robot_pool"]},
        "object_states": {o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
                          for o in public["public_world"]["objects"]},
    })
    assert result["error"] is None
    assert result["skill_receipt_count"] == 0
    return public


def _read_metadata(root, public, episode_id):
    return build_scoring_adapter_metadata(public, output_root=root, episode_id=episode_id)


def test_runner_source_binding_retains_valid_zero_and_positive_raw_facts(tmp_path):
    tmp_path = tmp_path / "episode"
    public = _run(tmp_path, "binding-good")
    metadata = _read_metadata(tmp_path, public, "binding-good")
    assert metadata["plan_admission"]["accepted_plan_attempt_count"] == 1
    cost = metadata["cost_evidence"]
    assert cost["accepted_actions"]["total_count"] == 2
    assert cost["clock"]["elapsed_s"] == 0.0
    assert cost["activation_cost_proxy"]["value"] == 0.0
    assert cost["communication"]["total_bytes"] > 0
    assert cost["planning_compute_cost"]["total_s"] is None
    assert "goal_cost_source_bindings.json" in metadata["archive_index"]["files"]
    for flag in ("execution_validated", "business_outcomes_validated", "scoreable"):
        assert metadata[flag] is False
        assert cost["validity"][flag] is False


@pytest.mark.parametrize("substitution", ["episode", "task", "missing_episode"])
def test_session_substitution_cannot_supply_plan_or_session_costs(tmp_path, substitution):
    tmp_path = tmp_path / "episode"
    public = _run(tmp_path, "binding-original")
    if substitution == "task":
        public = deepcopy(public)
        public["task_id"] = "another-public-task"
    else:
        path = tmp_path / "goal_session_report.json"
        payload = json.loads(path.read_text())
        if substitution == "episode":
            payload["episode_id"] = "foreign-episode"
        else:
            payload.pop("episode_id")
        path.write_text(json.dumps(payload))
    metadata = _read_metadata(tmp_path, public, "binding-original")
    assert metadata["plan_admission"]["status"] != "observed"
    assert metadata["cost_evidence"]["accepted_actions"]["total_count"] is None
    assert metadata["cost_evidence"]["clock"]["elapsed_s"] is None
    assert metadata["cost_evidence"]["planning_compute_cost"]["total_s"] is None
    assert metadata["scoreable"] is False


@pytest.mark.parametrize("source,metric", [
    ("team_usage.json", "activation_cost_proxy"),
    ("communication_transport_report.json", "communication"),
])
def test_independently_valid_foreign_sources_fail_original_binding(tmp_path, source, metric):
    left, right = tmp_path / "left", tmp_path / "right"
    public = _run(left, "binding-left")
    _run(right, "binding-right-with-other-payload")
    if source == "team_usage.json":
        # A plausible numeric value cannot substitute for the original source.
        payload = json.loads((right / source).read_text())
        payload["activation_cost_proxy"] = 99.0
        (right / source).write_text(json.dumps(payload))
    shutil.copyfile(right / source, left / source)
    if metric == "communication":
        shutil.copyfile(right / "communication_transport_replay.json",
                        left / "communication_transport_replay.json")
    metadata = _read_metadata(left, public, "binding-left")
    cost = metadata["cost_evidence"]
    value = cost[metric]["value" if metric == "activation_cost_proxy" else "total_bytes"]
    assert value is None
    assert cost[metric]["valid"] is False
    assert cost["accepted_actions"]["total_count"] == 2
    assert metadata["plan_admission"]["accepted_plan_attempt_count"] == 1


def test_reading_an_unbound_old_archive_cannot_create_source_proof(tmp_path):
    tmp_path = tmp_path / "episode"
    public = _run(tmp_path, "binding-missing")
    (tmp_path / "goal_cost_source_bindings.json").unlink()
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.iterdir() if p.is_file()}
    metadata = _read_metadata(tmp_path, public, "binding-missing")
    assert metadata["cost_evidence"]["activation_cost_proxy"]["value"] is None
    assert metadata["cost_evidence"]["communication"]["total_bytes"] is None
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in tmp_path.iterdir() if p.is_file()}
    assert before == after


@pytest.mark.parametrize("corruption", ["foreign_episode", "foreign_task", "bad_session_entry"])
def test_invalid_binding_cannot_crash_or_qualify_foreign_session_facts(tmp_path, corruption):
    root = tmp_path / "episode"
    public = _run(root, "binding-corruption")
    path = root / "goal_cost_source_bindings.json"
    binding = json.loads(path.read_text())
    if corruption == "foreign_episode":
        binding["episode_id"] = "another-episode"
    elif corruption == "foreign_task":
        binding["public_task_sha256"] = "0" * 64
    else:
        binding["source_files"]["goal_session_report.json"] = None
    path.write_text(json.dumps(binding))
    metadata = _read_metadata(root, public, "binding-corruption")
    assert metadata["plan_admission"]["status"] != "observed"
    assert metadata["cost_evidence"]["accepted_actions"]["total_count"] is None
    assert metadata["scoreable"] is False
