"""Pure data-contract tests; synthetic samples are not deployment evidence."""

from copy import deepcopy
import json
import math

import pytest

from demo.benchmark.control_step_evidence import ControlStepEvidence, validate_control_step_evidence
from demo.benchmark.provenance import canonical_sha256


def state(x=0.):
    return [x, 0., .2, 1., 0., 0., 0., 0., 0., 0., 0., 0., 0.]


def validate(evidence, steps=2, dt=.02):
    return validate_control_step_evidence(evidence, steps_taken=steps, control_dt_s=dt)


def rehash(evidence):
    evidence["sha256"] = canonical_sha256({k: v for k, v in evidence.items() if k != "sha256"})


@pytest.fixture
def recorded():
    collector = ControlStepEvidence(.02)
    collector.record(1, {"carter": [1., 0., .2]}, {"arm": state(), "carter": state(.02)})
    collector.record(2, {"carter": [0., 0., 0.]}, {"carter": state(.025), "arm": state(.01)})
    return collector


def test_roundtrip_and_index_derived_submission_readback_times(recorded):
    evidence = json.loads(json.dumps(recorded.report(), allow_nan=False))
    assert validate(evidence) == evidence
    assert evidence["robot_ids"] == ["carter"]
    assert evidence["state_ids"] == ["arm", "carter"]
    assert evidence["samples"][0]["submitted_at_sim_time_s"] == 0.
    assert evidence["samples"][0]["post_step_sim_time_s"] == .02
    assert evidence["samples"][1]["submitted_at_sim_time_s"] == .02
    assert evidence["samples"][1]["post_step_sim_time_s"] == .04
    assert evidence["command_scope"] == "submitted_to_env_step"
    assert evidence["actuator_applied_validated"] is False
    assert evidence["physical_control_validated"] is False
    assert evidence["contact_physics_validated"] is False


def test_both_input_and_returned_data_are_detached():
    collector = ControlStepEvidence(.02)
    commands, states = {"carter": [1., 0., .2]}, {"carter": state()}
    assert collector.record(1, commands, states) is None
    snapshot = collector.report()
    commands["carter"][0] = 999
    states["carter"][0] = 999
    snapshot["samples"][0]["post_root_states"]["carter"][0] = 1000
    snapshot["robot_ids"].clear()
    evidence = collector.report()
    assert evidence["samples"][0]["commands"]["carter"][0] == 1
    assert evidence["samples"][0]["post_root_states"]["carter"][0] == 0
    assert evidence["robot_ids"] == ["carter"]
    validate(evidence, steps=1)


def test_empty_collector_is_serializable_but_not_admissible():
    evidence = ControlStepEvidence(.02).report()
    assert evidence["failure"] is None
    assert evidence["validated"] is False
    json.dumps(evidence, allow_nan=False)
    with pytest.raises(ValueError, match="not validated"):
        validate(evidence, steps=0)


@pytest.mark.parametrize("dt", [0, -1, True, None, "0.02", math.nan, math.inf, -math.inf, 10**400])
def test_invalid_dt_is_rejected_before_recording(dt):
    with pytest.raises(ValueError, match="control_dt_s"):
        ControlStepEvidence(dt)


@pytest.mark.parametrize("step", [0, 2, -1, 1.0, True, None, "1", math.nan, math.inf])
def test_invalid_first_step_latches_a_json_safe_failure(step):
    collector = ControlStepEvidence(.02)
    collector.record(step, {"carter": [0., 0., 0.]}, {"carter": state()})
    evidence = collector.report()
    assert evidence["failure"]["expected_step"] == 1
    assert evidence["validated"] is False
    json.dumps(evidence, allow_nan=False)
    with pytest.raises(ValueError, match="failure"):
        validate(evidence, steps=1)


@pytest.mark.parametrize("field,index", [("commands", 0), ("post_root_states", 0),
                                         ("post_root_states", 4), ("post_root_states", 12)])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, None, "1", 10**400])
def test_bad_components_preserve_valid_prefix_and_never_enter_json(field, index, value):
    collector = ControlStepEvidence(.02)
    collector.record(1, {"carter": [0., 0., 0.]}, {"carter": state()})
    vectors = {"commands": {"carter": [0., 0., 0.]}, "post_root_states": {"carter": state()}}
    vectors[field]["carter"][index] = value
    collector.record(2, **vectors)
    failed = collector.report()
    assert failed["sample_count"] == 1
    assert failed["failure"]["step"] == 2
    assert f"{field}.carter[{index}]" in failed["failure"]["reason"]
    json.dumps(failed, allow_nan=False)
    collector.record(2, {"carter": [0., 0., 0.]}, {"carter": state()})
    collector.record(3, {"carter": [0., 0., 0.]}, {"carter": state()})
    assert collector.report() == failed
    with pytest.raises(ValueError, match="failure"):
        validate(failed)


@pytest.mark.parametrize("commands,states", [
    ({}, {"carter": state()}), ({"carter": [0., 0., 0.]}, {}),
    ({"carter": [[0., 0., 0.]]}, {"carter": state()}),
    ({"carter": [0., 0.]}, {"carter": state()}),
    ({"carter": [0., 0., 0., 0.]}, {"carter": state()}),
    ({"carter": [0., 0., 0.]}, {"carter": state()[:12]}),
    ({"carter": [0., 0., 0.]}, {"carter": [state()]}),
    ({"carter": (0., 0., 0.)}, {"carter": state()}),
    ({1: [0., 0., 0.]}, {"carter": state()}),
    ({"": [0., 0., 0.]}, {"carter": state()}),
    ({"carter": [0., 0., 0.]}, {"other_robot": state()}),
])
def test_malformed_vectors_and_unmatched_ids_record_failure(commands, states):
    collector = ControlStepEvidence(.02)
    collector.record(1, commands, states)
    evidence = collector.report()
    assert evidence["failure"]
    assert not evidence["validated"]
    json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize("change", ["repeat", "gap", "command_ids", "state_ids"])
def test_mid_episode_discontinuities_cannot_be_hidden(recorded, change):
    commands, states, step = {"carter": [0., 0., 0.]}, {"carter": state(), "arm": state()}, 3
    if change == "repeat":
        step = 2
    elif change == "gap":
        step = 4
    elif change == "command_ids":
        commands["arm"] = [0., 0., 0.]
    else:
        states.pop("arm")
    recorded.record(step, commands, states)
    assert recorded.report()["failure"]
    with pytest.raises(ValueError, match="failure"):
        validate(recorded.report(), steps=3)


def test_time_overflow_is_json_safe_failure():
    collector = ControlStepEvidence(1e308)
    collector.record(1, {"carter": [0., 0., 0.]}, {"carter": state()})
    collector.record(2, {"carter": [0., 0., 0.]}, {"carter": state()})
    evidence = collector.report()
    assert evidence["failure"]["step"] == 2
    json.dumps(evidence, allow_nan=False)


def test_hash_detects_a_changed_command(recorded):
    evidence = recorded.report()
    evidence["samples"][1]["commands"]["carter"][0] = 99.
    with pytest.raises(ValueError, match="digest"):
        validate(evidence)


@pytest.mark.parametrize("mutation", ["scope", "state_layout", "authority", "version", "count", "last_step",
                                     "time", "step", "gap", "inventory", "dimension", "bool", "dt"])
def test_rehashing_does_not_bypass_contract_checks(recorded, mutation):
    evidence = deepcopy(recorded.report())
    if mutation == "scope":
        evidence["command_scope"] = "actuator_applied"
    elif mutation == "state_layout":
        evidence["root_state_components"][3:7] = ["qx", "qy", "qz", "qw"]
    elif mutation == "authority":
        evidence["physical_control_validated"] = True
    elif mutation == "version":
        evidence["version"] = True
    elif mutation == "count":
        evidence["sample_count"] = 1
    elif mutation == "last_step":
        evidence["last_step"] = 3
    elif mutation == "time":
        evidence["samples"][1]["post_step_sim_time_s"] = .02
    elif mutation == "step":
        evidence["samples"][1]["step"] = 1
    elif mutation == "gap":
        evidence["samples"].pop(0)
    elif mutation == "inventory":
        evidence["samples"][1]["post_root_states"].pop("arm")
    elif mutation == "dimension":
        evidence["samples"][1]["commands"]["carter"].append(0.)
    elif mutation == "bool":
        evidence["samples"][1]["commands"]["carter"][0] = True
    else:
        evidence["control_dt_s"] = .01
    rehash(evidence)
    with pytest.raises(ValueError):
        validate(evidence)


def test_validator_rejects_nonfinite_instead_of_trying_to_hash_it(recorded):
    evidence = recorded.report()
    evidence["samples"][0]["post_root_states"]["carter"][0] = math.nan
    with pytest.raises(ValueError, match="finite JSON"):
        validate(evidence)
