import pytest

from algorithm.skills.contracts import SkillRequest
from algorithm.skills.simulator_schema import SimulatorSchemaError, validate_simulator_skill_request
from algorithm.skills.pick_place import BackendFeedback
from task.executors.pick_place import HoldingRegistry, PickPlaceExecutor


class FakeAdapter:
    robot_id = "arm_1"
    def __init__(self):
        self.command = None
        self.started = 0
    def resource_key(self, ref): return "object:" + ref
    def start(self, command): self.command, self.started = command, self.started + 1
    def before_step(self): pass
    def after_step(self): pass
    def request_stop(self, reason): pass
    def feedback(self):
        return BackendFeedback(self.command.request_id, "completed",
                                "holding" if self.command.operation == "pick" else "empty",
                                self.command.object_ref if self.command.operation == "pick" else None,
                                ready=True, stopped=True, clean=True)


def req(request_id, operation, object_ref="box_1", **extra):
    parameters = {"operation": operation, "object_ref": object_ref, "timeout_s": 5.0}
    parameters.update(extra)
    return SkillRequest(request_id, "eai.manipulation.pick_place", "arm_1", parameters=parameters)


def test_fixed_action_request_and_holding_queries():
    adapter = FakeAdapter()
    executor = PickPlaceExecutor(advance=lambda: None, simulation_time=lambda: 0.0,
                                 physical_object_experiment_authorization="fixed-action")
    executor.register_adapter(adapter)
    assert executor.execute(req("pick-1", "pick")) is True
    assert executor.holding_object("arm_1") == "box_1"
    assert executor.holder_of("box_1") == "arm_1"
    assert executor.execute(req("place-1", "place")) is True
    assert executor.holding_snapshot()["robot_to_object"] == {}


def test_place_object_mismatch_does_not_start_adapter():
    adapter = FakeAdapter()
    executor = PickPlaceExecutor(advance=lambda: None, simulation_time=lambda: 0.0,
                                 physical_object_experiment_authorization="fixed-action")
    executor.register_adapter(adapter)
    assert executor.execute(req("place-1", "place", "box_1")) is False
    assert adapter.started == 0


@pytest.mark.parametrize("extra", [
    {"semantic_point": "source"}, {"robot_ids": ["arm_1"]},
    {"base_position_world_m": [0, 0, 0]}, {"world_state_seq": 1},
])
def test_pick_place_rejects_navigation_and_geometry_fields(extra):
    with pytest.raises(SimulatorSchemaError):
        validate_simulator_skill_request(req("bad", "pick", **extra))
