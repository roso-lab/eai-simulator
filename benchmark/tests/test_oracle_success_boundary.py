"""CPU checks use actual bound predicates, never mocked oracle verdicts."""
import pytest

from benchmark.receipt_adapter_v3 import ReceiptAdapterError, _validate_oracle_success_consistency
from tools.benchmark_branch.oracle_predicates import DRAFT_BINDING_REGISTRY


def receipt(anchor, *, success=True, integrity=True, milestones=(), events=(), contacts=()):
    return {"identity": {"anchor_id": anchor}, "facts": {"success": success, "d6": {"success": success}},
            "streams": {"zone_intervals": [], "hold_intervals": list(contacts),
                        "milestones": [{"label": label, "tick": tick} for label, tick in milestones],
                        "task_events": [{"name": name, "subject": subject, "tick": tick} for name, subject, tick in events],
                        "identity_records": {}, "integrity_confirmed": integrity}}


def test_pilot_safety_violation_cannot_be_overridden_by_success_fact():
    row = receipt("liv-p3-01", milestones=[("joint_carry_complete", 20)],
                  events=[("arrive_in_position", "left_carrier", 1), ("arrive_in_position", "right_carrier", 1)])
    with pytest.raises(ReceiptAdapterError, match="unmet oracle success"):
        _validate_oracle_success_consistency(row)
    row["facts"] = {"success": False, "d6": {"success": False}}
    assert _validate_oracle_success_consistency(row)["status"] == "unmet"


@pytest.mark.parametrize("integrity,success,expected", [(True, False, "unmet"), (True, True, "reject"), (False, False, "reject"), (False, True, "reject")])
def test_missing_safety_event_distinguishes_unreached_from_missing_record(integrity, success, expected):
    row = receipt("liv-p3-01", integrity=integrity, success=success)
    if expected == "reject":
        with pytest.raises(ReceiptAdapterError):
            _validate_oracle_success_consistency(row)
    else:
        assert _validate_oracle_success_consistency(row)["status"] == expected


@pytest.mark.parametrize("offset", [0, 1])
def test_deadline_boundary_controls_task_success(offset):
    spec = DRAFT_BINDING_REGISTRY[("liv-p2-01", "delivery_handoff_within_horizon")]
    row = receipt("liv-p2-01", milestones=[("accepted", spec["deadline_ticks"] + offset)])
    if offset:
        with pytest.raises(ReceiptAdapterError, match="unmet oracle success"):
            _validate_oracle_success_consistency(row)
    else:
        assert _validate_oracle_success_consistency(row)["status"] == "satisfied"


def test_d6_cannot_bypass_failed_task_success():
    row = receipt("prod-p1-01", success=False)
    row["facts"]["d6"]["success"] = True
    with pytest.raises(ReceiptAdapterError, match="D6 success must equal"):
        _validate_oracle_success_consistency(row)


def test_diagnostic_overlap_does_not_become_success_condition():
    row = receipt("prod-p1-01", events=[("lot_first_pickup", "lot_a", 1),
                  ("lot_last_scan", "lot_a", 2), ("lot_first_pickup", "lot_b", 3),
                  ("lot_last_scan", "lot_b", 4)])
    assert _validate_oracle_success_consistency(row)["status"] == "not_applicable"
