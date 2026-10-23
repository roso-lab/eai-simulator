from dataclasses import replace
import pytest
from benchmark.resource_claim_v3 import ResourceClaimRegistrationV3, assess_resource_claim
from benchmark.statistics_v3 import IntervalV3


def interval(lo, hi, alpha=.01):
    return IntervalV3((lo+hi)/2, lo, hi, "registered_paired_interval", alpha)


def claim(**overrides):
    registration = ResourceClaimRegistrationV3("candidate", "reference", "same-support", ("link_bits", "execution_time"), .02, .03)
    args = dict(costs={"link_bits": interval(-20, -10), "execution_time": interval(-2, 0)},
                success=interval(-.02, .03), progress=interval(-.03, .02),
                support_digest="same-support", registration_digest=registration.digest(), evidence_digest="records")
    args.update(overrides)
    return assess_resource_claim(registration, **args)


def test_pareto_and_both_outcome_gates_are_required():
    result = claim()
    assert result["numerical_gate_passed"] and not result["physical_qualified"]
    assert not result["publication_ready"]
    assert not claim(success=interval(-.03, .01))["numerical_gate_passed"]
    assert not claim(progress=interval(-.04, .01))["numerical_gate_passed"]
    assert not claim(costs={"link_bits": interval(-20, -10), "execution_time": interval(-2, 1)})["numerical_gate_passed"]
    assert not claim(costs={"link_bits": interval(-20, 0), "execution_time": interval(-2, 0)})["numerical_gate_passed"]


def test_missing_or_incompatible_evidence_is_na():
    assert claim(progress=None)["status"] == "na"
    assert claim(support_digest="other-support")["status"] == "na"
    assert claim(registration_digest="other-registration")["status"] == "na"
    assert claim(costs={"link_bits": interval(-20, -10)})["status"] == "na"
    assert claim(success=interval(-.01, .01, alpha=.05))["status"] == "na"


def test_cost_selection_and_margins_are_frozen_in_identity():
    registration = ResourceClaimRegistrationV3("a", "b", "support", ("link_bits",), .01, .02)
    assert replace(registration, progress_margin=.03).digest() != registration.digest()
    with pytest.raises(ValueError):
        replace(registration, costs=("arbitrary_cost",))
    with pytest.raises(ValueError):
        replace(registration, success_margin=-1)
