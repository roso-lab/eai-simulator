import pytest
from benchmark.defects_v3 import StateMessageV3, DefectModulesV3, AttributionRegistrationV3, attribution_status, attribution_holm
from benchmark.statistics_v3 import IntervalV3


def test_communication_defect_changes_consumption_only_when_expired():
    msg = StateMessageV3("m", 1, 4, {"position": 2})
    original, defect, sham = [DefectModulesV3(v) for v in ("original", "stale_message", "sham")]
    assert original.consume([msg], tick=2) == defect.consume([msg], tick=2) == sham.consume([msg], tick=2)
    assert original.consume([msg], tick=5) == sham.consume([msg], tick=5) == ()
    assert defect.consume([msg], tick=5) == (msg,)
    assert defect.audit[-1]["expired"] is True
    assert msg.payload == {"position": 2}


def test_no_reallocation_is_latched_and_reset_between_runs():
    policy = DefectModulesV3("no_reallocation")
    assert policy.reassign(function_loss=False, current_assignment={"a":"r1"}, propose=lambda: {"a":"r2"}) == {"a":"r2"}
    assert policy.reassign(function_loss=True, current_assignment={"a":"r1"}, propose=lambda: {"a":"r2"}) == {"a":"r1"}
    assert policy.reassign(function_loss=False, current_assignment={"a":"r3"}, propose=lambda: {"a":"r2"}) == {"a":"r1"}
    policy.reset()
    assert policy.reassign(function_loss=False, current_assignment={}, propose=lambda: {"a":"r2"}) == {"a":"r2"}


def interval(lo, hi):
    return IntervalV3((lo+hi)/2, lo, hi, "test_fixture_only", .05)


def test_attribution_requires_nominal_equivalence_external_ledger_interaction_and_restore():
    registration = AttributionRegistrationV3("impl", "dev", "heldout", .02, {f"D{i}": 3. for i in range(1,7)}, "defects-only")
    diff = {f"D{i}": interval(-1, 1) for i in range(1,7)}
    diff["D2"] = interval(-20, -10)
    args = dict(variant="stale_message", differences=diff, nominal_success=interval(-.01,.01), interaction=interval(-15,-5), restored_target=interval(-2,2), external_condition_seen=True, nominal_unsaturated=True, nominal_decisions_identical=True, nominal_sent_messages_identical=True)
    result = attribution_status(registration, **args)
    assert result["all_checks_supported"] and not result["physical_attribution_qualified"]
    result = attribution_status(registration, **{**args, "nominal_unsaturated": False})
    assert not result["all_checks_supported"]
    result = attribution_status(registration, **{**args, "restored_target": interval(-10,-5)})
    assert result["pattern_supported"] and not result["all_checks_supported"]


def test_sham_requires_all_six_equivalent_not_just_nonsignificance():
    registration = AttributionRegistrationV3("impl", "dev", "heldout", .02, {f"D{i}": 3. for i in range(1,7)}, "sham")
    result = attribution_status(registration, variant="sham", differences={f"D{i}": interval(-10,10) for i in range(1,7)}, nominal_success=interval(-.01,.01), interaction=None, restored_target=None, external_condition_seen=False, nominal_unsaturated=True, nominal_decisions_identical=True, nominal_sent_messages_identical=True)
    assert not result["pattern_supported"]
    with pytest.raises(ValueError):
        attribution_holm({"one": .01}, registered_hypotheses=("one", "two"))
