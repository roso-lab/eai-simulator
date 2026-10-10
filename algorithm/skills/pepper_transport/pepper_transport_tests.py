from __future__ import annotations

from dataclasses import asdict
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from algorithm.skills.contracts import SkillRequest
from algorithm.skills.pepper_transport import (
    CARRY_MODE,
    MAX_HORIZON_TICKS,
    PepperTransportEvidenceEvaluator,
    PepperTransportExecutionError,
    PepperTransportExecutorAdapter,
    PepperTransportProvider,
)
from algorithm.skills.simulator_schema import SimulatorSchemaError


def _request(**overrides):
    values = {
        "request_id": "transport-1",
        "skill_id": "eai.transport.composite",
        "robot_id": "house_pepper_1",
        "parameters": {
            "object_ref": "g1_grasp_box",
            "source_ref": "box_source_table",
            "destination_ref": "box_destination_table",
            "carry_mode": CARRY_MODE,
            "horizon_ticks": 2,
        },
    }
    values.update(overrides)
    return SkillRequest(**values)


def compute_pepper_box_transport_action():
    pass


def apply_pepper_box_transport_action():
    pass


class PepperBoxTransportControllerCfg:
    robot_type = "Pepper"
    compute_action_from_command_func = staticmethod(compute_pepper_box_transport_action)
    apply_action_func = staticmethod(apply_pepper_box_transport_action)


class _GraspState:
    def __init__(self):
        self.config = SimpleNamespace(
            cube_prim_name="g1_grasp_box",
            box_position=(0.20, 0.0, 0.62),
            box_size=(0.26, 0.28, 0.30),
        )
        self.phase = "done"
        self.attached = False
        self.completed_cycles = 1

    def snapshot(self):
        return {
            "phase": self.phase,
            "attached": self.attached,
            "completed_cycles": self.completed_cycles,
        }


class _Transport:
    def __init__(self):
        self.config = SimpleNamespace(
            source_table_name="box_source_table",
            destination_table_name="box_destination_table",
            destination_table_position=(0.20, 0.75, 0.43),
            table_size=(0.42, 0.44, 0.08),
            table_top_z=0.47,
        )
        self.initial_pose = (0.0, 0.0, 0.0)
        self.last_pose = (0.0, 0.75, 0.0)
        self.phase = "done"
        self.completed_cycles = 1

    def snapshot(self):
        return {
            "phase": self.phase,
            "completed_cycles": self.completed_cycles,
            "displacement": [0.0, 0.75, 0.0],
            "destination_offset": [0.0, 0.75, 0.0],
        }


class _RootView:
    def __init__(self, mass=8.0):
        self.mass = mass

    def get_masses(self):
        return [[self.mass]]


class _PepperArticulation:
    def __init__(self):
        self.data = SimpleNamespace(joint_vel=[[0.001, 0.0, 0.0]])

    def find_joints(self, names, preserve_order=False):
        assert names == ["XDisp", "YDisp", "ZRot"]
        assert preserve_order is True
        return [0, 1, 2], list(names)


class _Env:
    num_envs = 1
    device = "cpu"

    def __init__(self, *, mass=8.0, linear_speed=0.001):
        runtime = SimpleNamespace(state=_GraspState())
        transport = _Transport()
        self.controller = {
            "name": "house_pepper_1",
            "robot_type": "Pepper",
            "runtime": runtime,
            "transport": transport,
            "locomotion_controller": "PEPPER_HOLONOMIC_CFG",
            "locomotion_compute_calls": 3,
            "locomotion_apply_calls": 12,
            "internal_velocity_command": [0.0, 0.0, 0.0],
        }
        self._controller_configs = {
            "house_pepper_1": PepperBoxTransportControllerCfg()
        }
        self._controllers_dict = {"house_pepper_1": self.controller}
        self.box_transport_state = transport.snapshot()
        self.pepper_box_grasp_state = runtime.state.snapshot()
        root = [0.20, 0.75, 0.62, 1.0, 0.0, 0.0, 0.0]
        root.extend([linear_speed, 0.0, 0.0, 0.0, 0.0, 0.0])
        box = SimpleNamespace(
            data=SimpleNamespace(root_state_w=[root]),
            root_physx_view=_RootView(mass),
        )
        self.scene = SimpleNamespace(
            articulations={"house_pepper_1": _PepperArticulation()},
            rigid_objects={"g1_grasp_box": box},
        )
        source = SimpleNamespace(
            spawn=SimpleNamespace(size=(0.42, 0.44, 0.08)),
            init_state=SimpleNamespace(pos=(0.20, 0.0, 0.43)),
        )
        destination = SimpleNamespace(
            spawn=SimpleNamespace(size=(0.42, 0.44, 0.08)),
            init_state=SimpleNamespace(pos=(0.20, 0.75, 0.43)),
        )
        self.cfg = SimpleNamespace(
            scene=SimpleNamespace(
                box_source_table=source,
                box_destination_table=destination,
            )
        )

    def step(self, actions):
        assert list(actions) == ["house_pepper_1"]
        self.controller["locomotion_compute_calls"] += 1
        self.controller["locomotion_apply_calls"] += 4


def test_provider_is_dependency_light_and_plan_is_deterministic_json_safe():
    provider_source = Path(__file__).with_name("provider.py").read_text()
    imported_roots = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(ast.parse(provider_source))
        if isinstance(node, ast.Import)
    }
    imported_roots.update(
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(provider_source))
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imported_roots.isdisjoint({"torch", "isaaclab", "omni", "rclpy", "rospy"})

    first = PepperTransportProvider().plan(_request())
    second = PepperTransportProvider().plan(_request())
    assert first == second
    assert first.commands[0].command_type == "pepper_transport_advance"
    assert first.metadata["physical_outcome"] == "external_evaluator_required"
    json.dumps(asdict(first), allow_nan=False, sort_keys=True)


def test_evaluator_has_no_g1_state_fallback():
    evaluator_source = Path(__file__).with_name("evaluator.py").read_text()
    assert "g1_box_grasp_state" not in evaluator_source


@pytest.mark.parametrize(
    "skill_request, expected_status",
    [
        (
            _request(
                robot_id="g1_1",
            ),
            "unsupported_capability",
        ),
        (
            _request(
                parameters={
                    "object_ref": "g1_grasp_box",
                    "source_ref": "box_source_table",
                    "destination_ref": "box_destination_table",
                    "carry_mode": "rigid_weld",
                }
            ),
            "unsupported_capability",
        ),
        (
            _request(
                parameters={
                    "object_ref": "g1_grasp_box",
                    "source_ref": "box_source_table",
                    "destination_ref": "box_destination_table",
                    "carry_mode": CARRY_MODE,
                    "speed": 1.0,
                }
            ),
            "invalid_request",
        ),
        (
            _request(
                parameters={
                    "object_ref": "g1_grasp_box",
                    "source_ref": "box_source_table",
                    "destination_ref": "box_destination_table",
                    "carry_mode": CARRY_MODE,
                    "horizon_ticks": MAX_HORIZON_TICKS + 1,
                }
            ),
            "unsupported_capability",
        ),
    ],
)
def test_provider_reports_invalid_or_unsupported_requests(skill_request, expected_status):
    plan = PepperTransportProvider().plan(skill_request)
    assert plan.status == expected_status
    assert not plan.commands
    assert plan.metadata["reason"]


def test_provider_defaults_optional_horizon_to_bounded_completion_budget():
    parameters = dict(_request().parameters)
    parameters.pop("horizon_ticks")
    plan = PepperTransportProvider().plan(_request(parameters=parameters))
    assert plan.commands[0].payload["horizon_ticks"] == 1200


def test_executor_advances_only_the_verified_controller_and_reports_feedback():
    env = _Env()
    plan = PepperTransportProvider().plan(_request())
    result = PepperTransportExecutorAdapter().execute(
        plan,
        env,
        neutral_action_factory=lambda _env, _robot: [0.0, 0.0, 0.0],
    )
    assert result["status"] == "command_applied"
    assert result["feedback"]["ticks_advanced"] == 2
    assert result["feedback"]["locomotion_compute_calls"] == 5
    assert result["feedback"]["locomotion_apply_calls"] == 20
    assert "physical_success" not in json.dumps(result)


@pytest.mark.parametrize("field", ["provider_version", "physical_outcome"])
def test_executor_rejects_incomplete_provider_metadata(field):
    env = _Env()
    plan = PepperTransportProvider().plan(_request())
    plan.metadata.pop(field)
    with pytest.raises(PepperTransportExecutionError, match=field):
        PepperTransportExecutorAdapter().execute(
            plan,
            env,
            neutral_action_factory=lambda _env, _robot: [0.0, 0.0, 0.0],
        )


def test_executor_rejects_a_non_transport_controller():
    env = _Env()
    env._controller_configs["house_pepper_1"] = SimpleNamespace(robot_type="Pepper")
    with pytest.raises(PepperTransportExecutionError, match="not routed"):
        PepperTransportExecutorAdapter().execute(
            PepperTransportProvider().plan(_request()),
            env,
            neutral_action_factory=lambda _env, _robot: [0.0, 0.0, 0.0],
        )


def test_external_evaluator_requires_all_live_terminal_evidence():
    env = _Env()
    plan = PepperTransportProvider().plan(_request())
    result = PepperTransportEvidenceEvaluator().evaluate(env, plan=plan)
    assert result["verdict"] == "success"
    assert result["physical_success"] is True
    json.dumps(result, allow_nan=False, sort_keys=True)


@pytest.mark.parametrize(
    "mutate, verdict, reason",
    [
        (
            lambda env: setattr(env.controller["runtime"].state, "attached", True),
            "failure",
            "grasp_state_snapshot_mismatch",
        ),
        (
            lambda env: env.scene.rigid_objects["g1_grasp_box"].data.root_state_w[0].__setitem__(7, 0.2),
            "failure",
            "terminal_linear_speed_too_high",
        ),
        (
            lambda env: setattr(env.scene.rigid_objects["g1_grasp_box"].root_physx_view, "mass", 1.0),
            "failure",
            "box_mass_mismatch",
        ),
        (
            lambda env: env.controller.pop("internal_velocity_command"),
            "unknown",
            "controller_command_evidence_unavailable",
        ),
        (
            lambda env: env.scene.articulations["house_pepper_1"].data.joint_vel[0].__setitem__(0, 0.2),
            "failure",
            "pepper_terminal_linear_speed_too_high",
        ),
        (
            lambda env: setattr(env.controller["transport"], "phase", "transport"),
            "failure",
            "transport_state_snapshot_mismatch",
        ),
    ],
)
def test_external_evaluator_fails_closed_for_missing_or_contradictory_evidence(
    mutate, verdict, reason
):
    env = _Env()
    mutate(env)
    result = PepperTransportEvidenceEvaluator().evaluate(
        env, plan=PepperTransportProvider().plan(_request())
    )
    assert result["verdict"] == verdict
    assert reason in result["reason_codes"]


def test_plan_status_alone_cannot_create_physical_success():
    env = _Env()
    env.controller["transport"].phase = "transport"
    env.box_transport_state = env.controller["transport"].snapshot()
    result = PepperTransportEvidenceEvaluator().evaluate(
        env, plan=PepperTransportProvider().plan(_request())
    )
    assert result["verdict"] == "unknown"
    assert result["physical_success"] is None
    assert "transport_not_done" in result["reason_codes"]
