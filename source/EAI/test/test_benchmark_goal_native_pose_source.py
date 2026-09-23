from types import SimpleNamespace

import pytest

from demo.benchmark.goal_native_pose_source import NativeGoalPoseSource


class Root:
    def __init__(self):
        self.values = [1., 2., .5, 1., 0., 0., 0., 0., 0., 0., 0., 0., 0.]

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.values[:]


def setup():
    root = Root()
    sim = SimpleNamespace(current_time_step_index=20, get_physics_dt=lambda: .01)
    articulation = SimpleNamespace(data=SimpleNamespace(root_state_w=[root]))
    session = SimpleNamespace(num_envs=1, base_env=SimpleNamespace(
        sim=sim, scene=SimpleNamespace(articulations={"m20_1": articulation})))
    return NativeGoalPoseSource(session, {"public_m20": "m20_1"}, episode_step_origin=20), sim, root


def test_current_root_readback_uses_physics_clock_not_plan_time():
    source, sim, root = setup()
    assert source.time_s == 0
    sim.current_time_step_index = 45
    root.values[0] = 3.
    sample = source("public_m20", .25)
    assert sample["source"] == "physx"
    assert sample["position_m"] == [3., 2., .5]
    assert sample["quality"]["physics_step"] == 45
    assert sample["quality"]["position_error_m"] is None
    with pytest.raises(ValueError, match="physics time"):
        source("public_m20", 16)
    with pytest.raises(ValueError, match="binding"):
        source("hidden_reserve", .25)


def test_reset_requires_new_branch_source_and_nonfinite_pose_is_rejected():
    source, sim, root = setup()
    sim.current_time_step_index = 30
    assert source.time_s == .1
    sim.current_time_step_index = 20
    with pytest.raises(ValueError, match="backwards"):
        source("public_m20", 0)
    sim.current_time_step_index = 30
    root.values[2] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        source("public_m20", .1)


def test_invalid_quaternion_is_not_reported_as_valid_heading():
    source, _, root = setup()
    root.values[3:7] = [0, 0, 0, 0]
    with pytest.raises(ValueError, match="quaternion"):
        source("public_m20", 0)
