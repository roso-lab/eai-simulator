import json
from types import SimpleNamespace

import numpy as np
import pytest

from tools.physics_replay.live import LiveReplayController, state_errors
from tools.physics_replay.physics_trace import PhysicsStepTrace, PhysxMeasurementReader, PolicyInferenceTrace
from tools.physics_replay.tests.test_live import Backend


def test_inference_trace_reads_actual_input_without_recomputing():
    class Policy:
        calls = 0

        def act(self, inputs, role="policy"):
            self.calls += 1
            inputs["states"] += 2
            return inputs["states"] * self.calls, None

    policy = Policy()
    trace = PolicyInferenceTrace({"robot": policy})
    result = policy.act({"states": np.array([3.0])})
    assert policy.calls == 1
    frame = trace.take_frame()
    np.testing.assert_array_equal(frame["inference/robot/0/observation"], [3.0])
    np.testing.assert_array_equal(frame["inference/robot/0/raw_action"], result[0])
    assert trace.take_frame()["inference/robot/calls"] == 0
    trace.close()
    assert "act" not in vars(policy)


def test_trace_reads_after_solve_and_checks_substep_count():
    simulation = SimpleNamespace(value=0)

    def step():
        simulation.value += 1

    simulation.step = step
    trace = PhysicsStepTrace(simulation, lambda: {"force_N": np.array([simulation.value])}, 2)
    trace.begin()
    simulation.step()
    simulation.step()
    result = trace.take_frame()
    assert result["substep/0/force_N"][0] == 1
    assert result["substep/1/force_N"][0] == 2
    simulation.step()
    with pytest.raises(ValueError, match="substep count mismatch"):
        trace.take_frame()
    trace.close()
    assert simulation.step is step


@pytest.mark.parametrize("force_error", [0.0, 1e-12])
def test_exact_verification_catches_force_error_with_identical_boundary_pose(tmp_path, force_error):
    backend = Backend(tmp_path)
    backend.exact = True
    force = [1.0]
    simulation = SimpleNamespace(step=lambda: None)
    trace = PhysicsStepTrace(simulation, lambda: {"force_N": np.array(force)}, 2)
    backend.physics_trace = trace
    backend.read_verification_state = lambda: {**backend.read_physics(), **trace.take_frame()}
    controller = LiveReplayController(backend)
    controller.capture_checkpoint()
    controller.request("verify", 2)
    assert controller.poll()
    for index in range(4):
        assert controller.poll()
        force[0] = 1.0 + (force_error if index == 2 else 0)
        simulation.step()
        simulation.step()
        controller.after_step()
    report = json.loads((tmp_path / "trajectory_verification.json").read_text())
    assert report["passed"] is (force_error == 0)
    assert report["threshold"] == 0
    assert report["zero_deviation_required"]
    assert not report["complete_physical_state_restored"]
    assert not trace.active
    trace.close()


def test_exact_verification_rejects_two_restored_runs(tmp_path):
    backend = Backend(tmp_path)
    backend.exact = True
    controller = LiveReplayController(backend)
    controller.capture_checkpoint()
    controller.after_step()
    controller.request("verify", 2)
    assert not controller.poll()
    assert "uninterrupted original reference" in controller.message
    assert not backend.restores


def test_exact_identical_nonunit_quaternion_has_no_acos_roundoff_error():
    pose = np.array([0, 0, 0, 0.2, 0.3, 0.4, 0.5], dtype=np.float32)
    assert state_errors({"body/pose": pose}, {"body/pose": pose})["body/pose/rotation_rad"] == 0


def test_contact_patches_keep_pair_identity_and_ignore_unused_capacity():
    reader = PhysxMeasurementReader.__new__(PhysxMeasurementReader)
    reader.contacts = SimpleNamespace(sensor_count=2, filter_count=2, max_contact_data_count=10)
    forces = np.full((10, 1), np.nan)
    points = np.full((10, 3), np.nan)
    forces[3:5, 0] = [20.0, 10.0]
    points[3:5] = [[2.0, 0, 0], [1.0, 0, 0]]
    counts = np.array([[0, 2], [0, 0]])
    starts = np.array([[0, 3], [0, 0]])
    measured = {}
    reader._patches(measured, "contact", (forces, points, counts, starts), ("force_N", "point_m"))
    np.testing.assert_array_equal(measured["contact/force_N"], [[10.0], [20.0]])
    np.testing.assert_array_equal(measured["contact/pair_indices"], [[0, 1], [0, 1]])
    assert all(np.all(np.isfinite(value)) for value in measured.values())
    reader.contacts.max_contact_data_count = 2
    with pytest.raises(ValueError, match="capacity reached"):
        reader._patches({}, "contact", (forces, points, counts, starts), ("force_N", "point_m"))
