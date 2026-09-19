"""Test the control-loop evidence boundary without starting Isaac."""

from types import SimpleNamespace

import pytest

from demo.benchmark.control_step_evidence import (
    ControlStepEvidence, validate_control_step_evidence,
)
from demo.benchmark.runtime import step_with_control_evidence


class _Vector(list):
    def tolist(self):
        return list(self)


def _asset():
    return SimpleNamespace(data=SimpleNamespace(
        root_state_w=[_Vector([0., 0., 0., 1., 0., 0., 0., 0., 0., 0., 0., 0., 0.])]))


def test_submission_is_copied_before_controller_mutation_and_state_after_step():
    robot = _asset()
    commands = {"carter_1": [_Vector([0.2, 0., 0.1])]}
    recorder = ControlStepEvidence(0.02)

    def simulate(submitted):
        submitted["carter_1"][0][0] = 9.
        robot.data.root_state_w[0][0] = 0.004
        return "step-result"

    result = step_with_control_evidence(
        SimpleNamespace(step=simulate), commands, {"carter_1": robot}, recorder, step=1)
    # A later pose override must not rewrite the archived post-step state.
    robot.data.root_state_w[0][0] = 100.
    report = recorder.report()
    validate_control_step_evidence(report, steps_taken=1, control_dt_s=0.02)
    assert result == "step-result"
    assert report["samples"][0]["commands"]["carter_1"] == [0.2, 0., 0.1]
    assert report["samples"][0]["post_root_states"]["carter_1"][0] == 0.004


def test_failed_env_step_does_not_manufacture_a_post_step_sample():
    recorder = ControlStepEvidence(0.02)

    def simulate(_):
        raise RuntimeError("backend failed")

    with pytest.raises(RuntimeError, match="backend failed"):
        step_with_control_evidence(
            SimpleNamespace(step=simulate), {"carter_1": [_Vector([0., 0., 0.])]},
            {"carter_1": _asset()}, recorder, step=1)
    assert recorder.report()["samples"] == []
    assert recorder.report()["validated"] is False


def test_nonfinite_backend_state_is_retained_as_safe_failure():
    robot = _asset()
    recorder = ControlStepEvidence(0.02)

    def simulate(_):
        robot.data.root_state_w[0][0] = float("nan")

    step_with_control_evidence(
        SimpleNamespace(step=simulate), {"carter_1": [_Vector([0., 0., 0.])]},
        {"carter_1": robot}, recorder, step=1)
    report = recorder.report()
    assert report["validated"] is False
    assert report["failure"]["expected_step"] == 1
    assert report["samples"] == []
