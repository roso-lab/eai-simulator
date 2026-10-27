from __future__ import annotations

import pytest

from algorithm.skills import PICK_PLACE_SKILL_ID, SkillRequest
from algorithm.skills.pick_place import BackendFeedback
from task.executors.pick_place import PickPlaceExecutor
from task.transport import (
    TransportAdmissionResolver,
    TransportCommand,
    TRANSPORT_COMMAND_SCHEMA,
    compile_transport_command,
)


def _command(**overrides):
    values = {
        "request_id": "req-1",
        "task_id": "move-box",
        "robot_id": "robot-a",
        "object_ref": "box-a",
        "source_ref": "semantic:source",
        "target_ref": "semantic:target",
    }
    values.update(overrides)
    return TransportCommand(**values)


def test_transport_command_is_semantic_and_round_trips():
    command = _command(handoff_ref="semantic:handoff", phase="source_to_handoff")
    assert command.to_mapping()["schema"] == TRANSPORT_COMMAND_SCHEMA
    assert "mass_kg" not in command.to_mapping()
    assert TransportCommand.from_mapping(command.to_mapping()) == command


def test_assignment_compiles_to_transport_command_without_business_fields():
    class Assignment:
        task_id = "move-box"
        robot_id = "robot-a"

    class Task:
        task_id = "move-box"
        target_ref = "semantic:target"
        metadata = {"transport": {
            "object_ref": "box-a",
            "source_ref": "semantic:source",
            "target_ref": "semantic:target",
        }}

    command = compile_transport_command(Assignment(), Task(), request_id="req-2")
    assert command.robot_id == "robot-a"
    assert command.target_ref == "semantic:target"
    assert "order_id" not in command.to_mapping()


def test_transport_admission_rejects_overload_without_accepting_command_mass():
    resolver = TransportAdmissionResolver(
        {"box-a": {"physics": {"mass_kg": 4.0}}},
        {"robot-a": {"manipulation": {"payload_kg": 3.0}}},
    )
    result = resolver.check(_command(parameters={"mass_kg": 0.1}))
    assert result.admitted is False
    assert result.result_code == 0
    assert result.object_mass_kg == pytest.approx(4.0)
    assert result.robot_payload_kg == pytest.approx(3.0)
    assert resolver.records[-1]["reason"] == "object mass exceeds robot payload"
    feedback = result.to_execution_feedback(task_id="move-box", robot_id="robot-a")
    assert feedback.outcome == "failed"
    assert feedback.failure_code.value == "infeasible_team"
    assert feedback.world_changes["transport_admission"]["result"] == 0


def test_transport_admission_allows_equal_load_and_records_evidence():
    resolver = TransportAdmissionResolver(
        {"box-a": {"mass_kg": 3.0}},
        {"robot-a": {"payload_kg": 3.0}},
    )
    result = resolver.check(_command())
    assert result.admitted is True
    assert result.result_code == 1
    assert resolver.records[-1]["admitted"] is True


def test_resolver_indexes_authored_json_collections():
    resolver = TransportAdmissionResolver.from_json_documents(
        {"composition": {"entities": [{"ref": "box-a", "physics": {"mass_kg": 2.0}}]}},
        {"resumes": [{"resume_id": "robot-a", "manipulation": {"payload_kg": 3.0}}]},
    )
    assert resolver.check(_command()).admitted is True


def test_pick_place_executor_short_circuits_before_backend_on_failed_admission():
    class Adapter:
        robot_id = "robot-a"
        resource_key = lambda self, ref: "object:" + ref
        validate = lambda self, command: (_ for _ in ()).throw(AssertionError("validate called"))
        start = lambda self, command: (_ for _ in ()).throw(AssertionError("start called"))
        before_step = lambda self: None
        after_step = lambda self: None
        feedback = lambda self: BackendFeedback("", "stopped", "empty")
        request_stop = lambda self, reason: None

    request = SkillRequest(
        "req-admission", PICK_PLACE_SKILL_ID, "robot-a", target_pose=(0.0, 0.0, 0.0),
        parameters={
            "operation": "pick", "object_ref": "box-a",
            "base_position_world_m": (0.0, 0.0, 0.0),
            "base_orientation_xyzw": (0.0, 0.0, 0.0, 1.0),
            "world_state_seq": 0, "simulation_time_s": 0.0,
        },
    )
    executor = PickPlaceExecutor(
        advance=lambda: None, simulation_time=lambda: 0.0,
        transport_admission=lambda command: False,
    )
    executor.register_adapter(Adapter())
    assert executor.execute(request) is False


def test_pick_place_keeps_controller_call_but_ignores_geometry_lifecycle_flags():
    calls = []

    class Adapter:
        robot_id = "robot-a"
        resource_key = lambda self, ref: "object:" + ref
        # A legacy geometry validator is present but must not be called.
        validate = lambda self, command: (_ for _ in ()).throw(AssertionError("legacy validate called"))

        def start(self, command):
            calls.append(("controller.start", command.object_ref))

        before_step = lambda self: None
        after_step = lambda self: None
        request_stop = lambda self, reason: None

        def feedback(self):
            return BackendFeedback("req-controller", "completed", "holding",
                                   object_ref="box-a", ready=False,
                                   stopped=False, clean=False)

    request = SkillRequest(
        "req-controller", PICK_PLACE_SKILL_ID, "robot-a", target_pose=(0.0, 0.0, 0.0),
        parameters={
            "operation": "pick", "object_ref": "box-a",
            "base_position_world_m": (0.0, 0.0, 0.0),
            "base_orientation_xyzw": (0.0, 0.0, 0.0, 1.0),
            "world_state_seq": 0, "simulation_time_s": 0.0,
        },
    )
    executor = PickPlaceExecutor(
        advance=lambda: None, simulation_time=lambda: 0.0,
        transport_admission=lambda command: True,
    )
    executor.register_adapter(Adapter())
    assert executor.execute(request) is True
    assert calls == [("controller.start", "box-a")]


def test_idealized_manipulator_does_not_run_legacy_geometry_backend():
    """Semantic transport succeeds even when the physical arm path rejects geometry."""
    calls = []

    class Adapter:
        robot_id = "robot-a"
        idealized_semantics = True
        resource_key = lambda self, ref: "object:" + ref
        validate = lambda self, command: (_ for _ in ()).throw(AssertionError("validate called"))

        def start(self, command):
            calls.append("legacy-start")
            raise ValueError("unreachable Cartesian target")

        before_step = lambda self: (_ for _ in ()).throw(AssertionError("stepped"))
        after_step = lambda self: (_ for _ in ()).throw(AssertionError("stepped"))
        feedback = lambda self: (_ for _ in ()).throw(AssertionError("feedback called"))
        request_stop = lambda self, reason: calls.append(("stop", reason))

    request = SkillRequest(
        "req-idealized", PICK_PLACE_SKILL_ID, "robot-a", target_pose=(0.0, 0.0, 0.0),
        parameters={
            "operation": "pick", "object_ref": "box-a",
            "base_position_world_m": (0.0, 0.0, 0.0),
            "base_orientation_xyzw": (0.0, 0.0, 0.0, 1.0),
            "world_state_seq": 0, "simulation_time_s": 0.0,
        },
    )
    executor = PickPlaceExecutor(
        advance=lambda: (_ for _ in ()).throw(AssertionError("advanced")),
        simulation_time=lambda: 0.0,
        transport_admission=lambda command: True,
    )
    executor.register_adapter(Adapter())
    assert executor.execute(request) is True
    assert calls == []
