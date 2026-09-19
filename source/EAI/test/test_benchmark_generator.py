"""Focused pure tests for the unified benchmark admission loop."""

from types import SimpleNamespace
import json
import sys

import pytest

from tools.scenario_generation import benchmark_generator as bg


def test_model_provider_serializes_profile_and_preserves_instruction_on_repair(monkeypatch):
    from tools.scenario_generation.benchmark_task_design import SCENE_TASK_PROFILES

    calls = []

    def create(**kwargs):
        calls.append(json.loads(json.dumps(kwargs)))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"task_name":"candidate"}', refusal=None),
            finish_reason="stop")])

    monkeypatch.setattr("tools.scenario_generation.common.manifest",
                        lambda *args, **kwargs: {"assets": [], "robots": []})
    monkeypatch.setattr("EAI.scenario_spec.templates.template_for",
                        lambda *args: SimpleNamespace(key="factory", map_sha256="map", zones=[]))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider = bg.openai_candidate_provider(
        client, model="offline-test", scene_key="factory", occupancy=object(),
        instruction="检查被货架遮挡的物料")
    provider({"round": 1, "repair_prompt": ""})
    provider({"round": 2, "repair_prompt": "repair the target"})
    first = json.loads(calls[0]["messages"][-1]["content"])
    repaired = json.loads(calls[1]["messages"][-1]["content"])
    assert first["instruction"] == repaired["instruction"] == "检查被货架遮挡的物料"
    assert first["manifest"]["task_profile"]["required_skills"] == ["aerial_scan", "joint_transport"]
    assert repaired["request"]["repair_prompt"] == "repair the target"
    assert isinstance(SCENE_TASK_PROFILES["factory"]["required_skills"], set)


def test_generation_cli_passes_user_instruction_to_provider(monkeypatch, tmp_path):
    from tools.scenario_generation import generate_benchmark as cli

    class OfflineClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    captured = {}

    def provider(client, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("OPENAI_API_KEY", "offline-placeholder")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=OfflineClient))
    monkeypatch.setattr("EAI.scenario_spec.geometry.provider_map", lambda **kwargs: object())
    monkeypatch.setattr(cli, "openai_candidate_provider", provider)
    monkeypatch.setattr(cli, "BenchmarkGenerator", lambda *args, **kwargs: SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(as_dict=lambda: {"pool": []})))
    assert cli.main(["--scene-key", "factory", "--instruction", "共享通道巡检",
                     "--payload-policy", '{"carter":1}',
                     "--output", str(tmp_path / "candidate.json")]) == 1
    assert captured["instruction"] == "共享通道巡检"


def _candidate():
    return {
        "scene_key": "warehouse_nvd",
        "task_name": "candidate_a",
        "version": 2,
        "robots": [],
        "composition": {"entities": [], "targets": []},
        "phase_graph": {"nodes": []},
    }


def _patch_all(monkeypatch, *, team=True):
    monkeypatch.setattr(
        "EAI.scenario_spec.validation.validate_scenario",
        lambda data, known_floors=(): dict(data),
    )
    monkeypatch.setattr(bg, "_geometry", lambda data, occupancy: {"status": "geometry"})
    monkeypatch.setattr(bg, "_connectivity", lambda data, occupancy: {"status": "connectivity"})
    monkeypatch.setattr(bg, "_oracle", lambda data, occupancy, policy, template: {"status": "oracle"})
    monkeypatch.setattr(
        bg,
        "_team_contract",
        lambda data, policy: {"status": "pass" if team else "fail", "eligible": team},
    )


def _generator(monkeypatch, *, team=True):
    _patch_all(monkeypatch, team=team)
    return bg.BenchmarkGenerator(
        "warehouse_nvd",
        occupancy=object(),
        template=SimpleNamespace(scene_key="warehouse_nvd", levels=()),
        payload_policy={"carter": 20.0},
        executor_contract=lambda data, *, transport_mode: {
            "tier": "navigation_execution", "transport_mode": transport_mode,
        },
    )


def test_admitted_candidate_has_all_local_gates_and_no_execution_report(monkeypatch):
    generator = _generator(monkeypatch)
    entry, feedback = generator.admit(_candidate())

    assert entry is not None
    assert feedback.accepted is True
    assert [gate.name for gate in feedback.gates] == list(bg.GATE_NAMES)
    assert all(gate.status == "passed" for gate in feedback.gates)
    assert "settle" not in entry
    assert "execution" not in entry
    assert entry["admission"]["status"] == "admitted"


def test_failed_team_contract_never_enters_pool(monkeypatch):
    generator = _generator(monkeypatch, team=False)
    result = generator.run([_candidate()], max_rounds=1).as_dict()

    assert result["pool"] == []
    assert result["feedback"][0]["accepted"] is False
    assert result["feedback"][0]["failed_gates"] == ["team_necessity"]
    assert "team_necessity" in result["feedback"][0]["repair_prompt"]
    assert result["execution_evidence"] == "not_attempted"


def test_conditional_manipulator_never_enters_dynamic_pool(monkeypatch):
    generator = _generator(monkeypatch)
    candidate = _candidate()
    candidate["robots"] = [{"id": "scout_1", "type": "scout", "attachments": [{"type": "ur5"}]}]
    candidate["phase_graph"]["nodes"] = [{
        "id": "carry", "skill_key": "transport_object", "preferred_agent": "scout_1",
    }]
    monkeypatch.setattr(
        "tools.scenario_generation.robot_dossier.robot_dossier",
        lambda *args, **kwargs: {"robot_key": "scout", "selected_attachments": ["ur5"]},
    )
    monkeypatch.setattr(
        "tools.scenario_generation.robot_suitability.evaluate_suitability",
        lambda *args, **kwargs: {"status": "ineligible", "reasons": ["conditional"]},
    )

    entry, feedback = generator.admit(candidate)

    assert entry is None
    assert feedback.accepted is False
    assert feedback.gates[-1].name == "robot_suitability"
    assert feedback.gates[-1].status == "failed"


def test_provider_receives_failed_feedback_and_can_repair_next_round(monkeypatch):
    generator = _generator(monkeypatch)
    seen = []

    def provider(request):
        seen.append(request)
        if request["round"] == 1:
            return "not-json"
        return _candidate()

    result = generator.run(provider, max_rounds=2).as_dict()

    assert len(result["feedback"]) == 2
    assert result["feedback"][0]["accepted"] is False
    assert result["feedback"][1]["accepted"] is True
    assert len(result["pool"]) == 1
    assert seen[1]["previous_feedback"][0]["failed_gates"] == ["schema"]
    assert seen[1]["repair_prompt"]


def test_schema_failure_blocks_every_downstream_gate(monkeypatch):
    generator = _generator(monkeypatch)
    feedback = generator.evaluate({"scene_key": "wrong"})

    assert feedback.accepted is False
    assert [gate.name for gate in feedback.gates] == list(bg.GATE_NAMES)
    assert feedback.gates[0].status == "failed"


def test_nvd_generation_reuses_authoring_review_and_rejects_wrong_business_prop(monkeypatch):
    from copy import deepcopy
    from demo.benchmark.scene_challenge import generate_scene_scenario

    monkeypatch.setattr(bg, "_geometry", lambda *args: {"status": "geometry"})
    monkeypatch.setattr(bg, "_connectivity", lambda *args: {"status": "connectivity"})
    monkeypatch.setattr(bg, "_oracle", lambda *args: {"status": "oracle"})
    monkeypatch.setattr(bg, "_team_contract", lambda *args, **kwargs: {"eligible": True})
    data = generate_scene_scenario(
        scene_key="warehouse_nvd", task_name="candidate", seed=17,
        origin=(-10.0, -11.75), secondary_ground_offset=(6.425, 0.0))
    generator = bg.BenchmarkGenerator(
        "warehouse_nvd", occupancy=object(),
        template=SimpleNamespace(scene_key="warehouse_nvd", levels=()),
        payload_policy={"m20": 20.0, "carter": 20.0},
        executor_contract=lambda *args, **kwargs: {"tier": "navigation_execution"},
        require_long_horizon=True,
    )

    feedback = generator.evaluate(data)
    assert feedback.accepted
    authoring = next(gate for gate in feedback.gates if gate.name == "authoring_review")
    assert authoring.status == "passed"

    broken = deepcopy(data)
    broken["composition"]["entities"][0]["asset_key"] = "primitive_box"
    broken_feedback = generator.evaluate(broken)
    assert not broken_feedback.accepted
    authoring = next(gate for gate in broken_feedback.gates if gate.name == "authoring_review")
    assert authoring.status == "failed"
    assert "domain_props_are_primitive_proxies" in authoring.error


@pytest.mark.parametrize("aerial_type", ["cf2x", "iris", "pegasus"])
def test_strong_long_horizon_profile_requires_real_role_coupling(aerial_type):
    from tools.scenario_generation.benchmark_task_design import validate_long_horizon_task

    data = {
        "scene_key": "warehouse_nvd",
        "robots": [
            {"id": "scout", "type": aerial_type},
            {"id": "left", "type": "m20"},
            {"id": "right", "type": "carter"},
        ],
        "phase_graph": {"nodes": [
            {"id": "scan", "skill_key": "aerial_scan", "preferred_agent": "scout"},
            {"id": "approach_l", "skill_key": "navigate", "prerequisites": ["scan"]},
            {"id": "approach_r", "skill_key": "navigate", "prerequisites": ["scan"]},
            {"id": "joint", "skill_key": "joint_transport", "agents": ["left", "right"],
             "prerequisites": ["approach_l", "approach_r"]},
            {"id": "secure", "skill_key": "transport_object", "preferred_agent": "left",
             "prerequisites": ["joint"]},
            {"id": "verify", "skill_key": "transport_object", "preferred_agent": "right",
             "prerequisites": ["secure"]},
            {"id": "dock", "skill_key": "navigate", "preferred_agent": "left",
             "prerequisites": ["verify"]},
            {"id": "inspect", "skill_key": "transport_object", "preferred_agent": "right",
             "prerequisites": ["dock"]},
        ]},
    }
    report = validate_long_horizon_task(data)
    assert report["eligible"] is True

    short = {**data, "phase_graph": {"nodes": data["phase_graph"]["nodes"][:4]}}
    assert validate_long_horizon_task(short)["eligible"] is False
