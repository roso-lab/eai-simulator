"""Focused tests for the explicit autonomous City adapter entry."""

from copy import deepcopy
import hashlib
import inspect
import json

import pytest

from demo.benchmark.goal_city_autonomous import (
    CITY_AUTONOMOUS_ORACLE_GAP_REF,
    CITY_AUTONOMOUS_ROBOT_IDS,
    build_city_autonomous_contract,
    run_city_autonomous_context,
    run_city_autonomous_session,
)
from demo.benchmark.goal_city_episode import CityNativeGoalBackend
from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
from demo.benchmark.goal_driven_tasks import (
    _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY,
)
from demo.benchmark.skill_ontology import required_capabilities
from source.EAI.test.test_benchmark_goal_city_episode import _make_ctx


def _manifest(identifier):
    return {
        "kind": "participant",
        "id": identifier,
        "version": 1,
        "interface": "eai.benchmark.goal_algorithm_adapter",
        "interface_version": 1,
        "evaluation_track": "goal_driven",
        "input_scope": "participant_visible_events_only",
        "control_scope": "benchmark_managed_low_level_control",
    }


def _action(identifier, kind, payload, time_s):
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": identifier,
        "submitted_at_s": float(time_s),
        "type": kind,
        "payload": deepcopy(payload),
    }


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _AutonomousAdapter:
    def __init__(self, mode):
        self.mode = mode
        self.public = None
        self.api = None
        self.assessed = False
        self.planned = False
        self.dispatched = False
        self.closed = False

    def manifest(self):
        return _manifest(f"city-autonomous-{self.mode}")

    def initialize(self, public_task, participant_api):
        self.public = deepcopy(public_task)
        self.api = deepcopy(participant_api)

    def _plan(self):
        durations = self.public["execution_contract"]["skill_durations_s"]
        skill = "guide_people"
        index = 0 if self.mode == "guide_go2" else 1
        team = ["go2_1" if index == 0 else "carter_1"]
        person_id, assembly_region_id = sorted(
            _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY.items())[index]
        task_id = f"participant_guide_{index}"
        arguments = {
            "person_ids": [person_id],
            "observation_refs": ["participant-visible-incident"],
            "assembly_region_id": assembly_region_id,
        }
        task = {
            "task_id": task_id,
            "skill": skill,
            "arguments": arguments,
            "prerequisites": [],
            "required_capabilities": sorted(required_capabilities(skill)),
            "assigned_agents": list(team),
            "evidence_refs": [],
            "execution": {
                "start_s": 0.0,
                "duration_s": max(30.0, float(durations[skill])),
            },
        }
        return {
            "schema": "eai.benchmark.goal_plan",
            "version": GOAL_PLAN_VERSION,
            "plan_id": f"participant-{self.mode}-plan",
            "assigned_team": team,
            "tasks": [task],
        }

    def act(self, events):
        required = next((row for row in events
                         if row["type"] == "deployment_assessment_required"), None)
        if not self.assessed and required is not None:
            self.assessed = True
            gate = required["payload"]
            return [_action(
                f"{self.mode}-assessment",
                "deployment_assessment",
                {
                    "assessment_id": gate["assessment_id"],
                    "assessment_epoch": gate["assessment_epoch"],
                    "roster_version": gate["roster_version"],
                    "capability_versions": gate["capability_versions"],
                    "decision": "deployable",
                    "reason_codes": [],
                    "evidence_refs": [],
                },
                required["time_s"],
            )]
        if self.assessed and not self.planned:
            now = max(row["time_s"] for row in events)
            plan = self._plan()
            self.planned = True
            return [
                _action(f"{self.mode}-select", "select_team",
                        {"agent_ids": plan["assigned_team"]}, now),
                _action(f"{self.mode}-plan", "submit_plan", {"plan": plan}, now),
            ]
        if self.planned and not self.dispatched:
            now = max(row["time_s"] for row in events)
            self.dispatched = True
            return [_action(
                f"{self.mode}-dispatch",
                "dispatch_skill",
                {"task_id": self._plan()["tasks"][0]["task_id"],
                 "evidence_refs": []},
                now,
            )]
        return []

    def close(self):
        self.closed = True


class _ControlledBackend:
    def __init__(self):
        self.tasks = []
        self.calls = 0

    def execute(self, task, *, episode_id, trusted_report):
        self.calls += 1
        self.tasks.append(deepcopy(task))
        row = trusted_report["session"]["tasks"][task["task_id"]]
        completed = task["execution"]["start_s"] + task["execution"]["duration_s"]
        return {
            "status": "succeeded",
            "completed_at_s": completed,
            "receipt": {
                "backend_id": "test.city.controlled-native-context",
                "receipt_ref": f"{episode_id}/{task['task_id']}",
                "task_id": task["task_id"],
                "action_id": row["dispatch_action_id"],
                "status": "succeeded",
                "completed_at_s": completed,
                "episode_id": episode_id,
                "evidence_refs": [f"controlled-native:{task['task_id']}"],
            },
            "runtime_state": {
                "robot_regions": {
                    robot_id: "road_surface"
                    for robot_id in CITY_AUTONOMOUS_ROBOT_IDS},
                "object_states": {},
            },
        }

    def cancel(self, *args, **kwargs):
        raise AssertionError((args, kwargs))


class _IdleAdapter:
    def manifest(self):
        return _manifest("city-autonomous-idle")

    def initialize(self, public_task, participant_api):
        self.public = public_task
        self.api = participant_api

    def act(self, events):
        return []

    def close(self):
        pass


class _HelpAdapter(_IdleAdapter):
    def __init__(self):
        self.sent = False

    def manifest(self):
        return _manifest("city-autonomous-help")

    def act(self, events):
        if self.sent:
            return []
        required = next((row for row in events
                         if row["type"] == "deployment_assessment_required"), None)
        if required is None:
            return []
        self.sent = True
        gate = required["payload"]
        return [_action("city-help", "deployment_assessment", {
            "assessment_id": gate["assessment_id"],
            "assessment_epoch": gate["assessment_epoch"],
            "roster_version": gate["roster_version"],
            "capability_versions": gate["capability_versions"],
            "decision": "not_deployable",
            "reason_codes": ["missing_capability"],
            "evidence_refs": [],
        }, required["time_s"])]


@pytest.mark.parametrize("mode,expected_agent", [
    ("guide_go2", "go2_1"),
    ("guide_carter", "carter_1"),
])
def test_two_external_plans_run_without_author_decomposition(
        tmp_path, mode, expected_agent):
    adapter = _AutonomousAdapter(mode)
    root = tmp_path / mode
    ctx, _runner, _steps, _roles = _make_ctx(tmp_path / f"ctx-{mode}", start_s=0.0)
    original_step = ctx["step"]
    result, backend = run_city_autonomous_context(
        ctx,
        adapter,
        output_root=root,
        episode_id=f"city-autonomous-{mode}",
    )

    assert result["error"] is None
    assert result["skill_receipt_count"] == 1
    assert result["session_audit_validated"] is True
    assert any(row.get("skill") == "guide_people" for row in backend.evidence)
    assert ctx["trace"]["member_dispatch"][0]["agent_id"] == expected_agent
    assert adapter.closed is True
    assert ctx["step"] is original_step
    archived_result = json.loads((root / "result.json").read_text())
    assert result == archived_result
    entry = json.loads((root / "goal_city_autonomous_entry.json").read_text())
    assert entry["candidate_robot_ids"] == list(
        CITY_AUTONOMOUS_ROBOT_IDS)
    assert entry["author_plan_supplied"] is False
    assert entry["hidden_package_supplied"] is False
    assert entry["execution_validated"] is False
    assert entry["business_outcomes_validated"] is False
    assert entry["scoreable"] is False
    audit = json.loads((root / "goal_session_audit.json").read_text())
    plan = audit["final_report"]["plans"][0]["plan"]
    assert plan["plan_id"] == f"participant-{mode}-plan"
    hidden = json.loads((root / "hidden_evaluator_trace.json").read_text())
    evaluation = hidden["deployment_assessment"]["oracle_evaluations"][0]
    assert evaluation == {
        "assessment_id": "deployment-assessment-0",
        "assessment_epoch": 0,
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": [],
        "evaluator_ref": CITY_AUTONOMOUS_ORACLE_GAP_REF,
    }
    progress = json.loads((root / "goal_progress_evidence.json").read_text())
    sources = json.loads((root / "city_progress_sources.json").read_text())
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    indexed = metadata["archive_index"]["files"]
    assert metadata["episode_result"]["error"] is None
    assert "progress_evidence" in metadata
    for name in ("goal_city_autonomous_entry.json", "city_progress_sources.json"):
        assert indexed[name]["sha256"] == _sha256(root / name)
        assert indexed[name]["bytes"] == (root / name).stat().st_size
    assert progress["samples"][-1]["boundary"] == "terminal"
    assert sources["samples"][-1]["boundary"] == "terminal"
    assert len(sources["samples"]) == len(progress["samples"])
    assert all(row["satisfied"] is None and row["evidence_refs"] == []
               for row in progress["samples"][0]["predicates"].values())
    assert all(row["clock_source"] == "native_simulation"
               for row in progress["samples"])


def test_help_terminates_without_native_execution_or_business_claim(tmp_path):
    backend = _ControlledBackend()
    root = tmp_path / "help"
    result = run_city_autonomous_session(
        _HelpAdapter(), backend, output_root=root,
        episode_id="city-autonomous-help",
        evaluator=lambda *_args: pytest.fail("help must not evaluate business"),
    )
    assert result["error"] is None
    assert result["termination"]["category"] == "human_assistance_requested"
    assert result["skill_receipt_count"] == 0
    assert result["session_audit_validated"] is True
    assert backend.calls == 0
    entry = json.loads((root / "goal_city_autonomous_entry.json").read_text())
    assert entry["scoreable"] is False
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    indexed = metadata["archive_index"]["files"]
    assert metadata["episode_result"]["error"] is None
    assert indexed["goal_city_autonomous_entry.json"]["sha256"] == _sha256(
        root / "goal_city_autonomous_entry.json")


def test_undeclared_initial_gate_fails_closed_and_still_audits(tmp_path):
    backend = _ControlledBackend()
    result = run_city_autonomous_session(
        _IdleAdapter(), backend, output_root=tmp_path / "idle",
        episode_id="city-autonomous-idle",
    )
    assert "missing_deployment_assessment" in result["error"]
    assert result["session_audit_validated"] is True
    assert backend.calls == 0
    protocol = json.loads(
        (tmp_path / "idle/deployment_protocol_error.json").read_text())
    assert protocol["code"] == "missing_assessment"


def test_acceptance_reader_failure_is_platform_evidence_failure(
        tmp_path, monkeypatch):
    from demo.benchmark import goal_city_autonomous as autonomous

    def fail_acceptance(*_args, **_kwargs):
        raise ValueError("broken native acceptance reader")

    monkeypatch.setattr(autonomous, "evaluate_city_acceptance", fail_acceptance)
    root = tmp_path / "reader-failure"
    ctx, _runner, _steps, _roles = _make_ctx(
        tmp_path / "ctx-reader-failure", start_s=0.0)
    original_step = ctx["step"]
    result, _backend = run_city_autonomous_context(
        ctx,
        _AutonomousAdapter("guide_go2"),
        output_root=root,
        episode_id="city-autonomous-reader-failure",
    )

    assert result["error"].startswith("lifecycle_error:")
    assert ctx["step"] is original_step
    progress = json.loads((root / "goal_progress_evidence.json").read_text())
    assert progress["status"] == "failed"
    assert progress["samples"][0]["boundary"] == "initial"
    assert progress["quality_errors"]
    assert all(row["category"] == "infrastructure_evidence_failure"
               and row["error"].startswith("RuntimeError:")
               for row in progress["quality_errors"])
    sources = json.loads((root / "city_progress_sources.json").read_text())
    assert len(sources["samples"]) == (
        len(progress["samples"]) + len(progress["quality_errors"]))
    failed_sources = [row for row in sources["samples"] if row["error"] is not None]
    assert len(failed_sources) == len(progress["quality_errors"])
    assert all(row["acceptance_review"] is None
               and row["error"] == "ValueError: broken native acceptance reader"
               for row in failed_sources)
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    assert metadata["progress_evidence"]["status"] == "failed"


def test_autonomous_entry_does_not_build_or_pass_the_city_witness_plan():
    from demo.benchmark import goal_city_autonomous as autonomous

    source = inspect.getsource(autonomous)
    assert "build_city_episode_plan" not in source
    assert "CityWitnessParticipant" not in source
    signature = inspect.signature(CityNativeGoalBackend)
    assert signature.parameters["plan"].default is None
    public, _api = build_city_autonomous_contract()
    assert public["task_id"] == "CITY-GOAL-NATIVE-01"
    assert public["skill_catalog_version"] == "eai.skills.v3"
    assert tuple(row["id"] for row in public["candidate_robot_pool"]) == (
        CITY_AUTONOMOUS_ROBOT_IDS)
    assert public["deployment_assessment"]["required"] is True
    reader_source = inspect.getsource(autonomous.CityAcceptanceProgressReader.__call__)
    assert "await_assembly_arrivals" not in reader_source
    assert '["step"]' not in reader_source


def test_native_warmup_does_not_consume_participant_execution_window(tmp_path):
    ctx, _runner, _steps, _roles = _make_ctx(tmp_path / "warmup", start_s=10.0)
    root = tmp_path / "episode"
    result, backend = run_city_autonomous_context(
        ctx, _AutonomousAdapter("guide_go2"), output_root=root,
        episode_id="warmup-epoch")
    assert result["error"] is None
    assert backend.clock_epoch_s == 10.0
    assert ctx["city"].elapsed == pytest.approx(40.0, abs=0.051)
    progress = json.loads((root / "goal_progress_evidence.json").read_text())
    sources = json.loads((root / "city_progress_sources.json").read_text())
    assert progress["samples"][0]["time_s"] == 0.0
    assert sources["samples"][0]["native_time_s"] == 10.0
    assert sources["clock_epoch_s"] == 10.0
    for source, sample in zip(sources["samples"], progress["samples"]):
        assert source["time_s"] == sample["time_s"]
        assert source["native_time_s"] - source["clock_epoch_s"] == sample["time_s"]


def test_adapter_manifest_is_frozen_once_for_host_and_entry(tmp_path):
    class ChangingManifestAdapter(_HelpAdapter):
        calls = 0

        def manifest(self):
            self.calls += 1
            return _manifest(f"changing-{self.calls}")

    adapter = ChangingManifestAdapter()
    identities = []
    root = tmp_path / "episode"

    def assessment(episode_id, report):
        from demo.benchmark.goal_city_autonomous import city_autonomous_platform_gap_evaluator
        identities.append(deepcopy(report["adapter"]))
        return city_autonomous_platform_gap_evaluator(episode_id, report)

    result = run_city_autonomous_session(
        adapter, _ControlledBackend(), output_root=root,
        assessment_evaluator=assessment,
        episode_id="manifest-freeze")
    assert result["error"] is None
    assert adapter.calls == 1
    entry = json.loads((root / "goal_city_autonomous_entry.json").read_text())
    assert entry["adapter_manifest"]["id"] == "changing-1"
    assert identities and all(identity == entry["adapter_manifest"]
                              for identity in identities)


@pytest.mark.parametrize("changed", [False, True])
def test_native_wrapper_records_start_hashes_and_blocks_source_drift(
        tmp_path, monkeypatch, changed):
    from demo.benchmark import goal_city_autonomous as autonomous
    from demo.city_benchmark import service_probe

    ctx, _runner, _steps, _roles = _make_ctx(tmp_path / "ctx", start_s=10.0)
    ctx["report"] = {"trace": {}}
    ctx["report_path"] = tmp_path / "service_probe_report.json"
    ctx["session"].device = "cpu"
    calls = []

    def hashes():
        digest = "after" if changed and calls else "before"
        calls.append(digest)
        return {"goal_modules": {"goal_city_autonomous.py": digest},
                "service_probe_sources": {"scene": "constant"}}

    monkeypatch.setattr(autonomous, "_source_hashes", hashes)
    monkeypatch.setattr(service_probe, "prepare_probe", lambda *_a, **_k: None)

    def execute(_root, *, driver, **_kwargs):
        driver(ctx)
        return ctx["report"]

    monkeypatch.setattr(service_probe, "execute_probe", execute)
    report = autonomous.run_city_autonomous_episode(
        tmp_path, _HelpAdapter(), episode_id="source-freeze")
    assert report["status"] == ("blocked" if changed else "completed")
    root = tmp_path / "goal_autonomous_episode"
    source = json.loads((root / "goal_city_autonomous_source.json").read_text())
    assert source["source_sha256"] == "before"
    assert source["sources_unchanged"] is (not changed)
    assert source["clock_epoch_s"] == 10.0
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    assert metadata["archive_index"]["files"]["goal_city_autonomous_source.json"][
        "sha256"] == _sha256(root / "goal_city_autonomous_source.json")
    assert report["trace"]["goal_autonomous_episode"]["scoreable"] is False
