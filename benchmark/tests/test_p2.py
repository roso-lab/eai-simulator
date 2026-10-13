from benchmark.evidence import EvaluationStatus, EvidenceStatus
from benchmark.p2 import P2MilestoneEvaluator
from benchmark.protocol import ProtocolContract


def _contract():
    return ProtocolContract(
        contract_id="p2",
        version="v1",
        domain="factory",
        primary_label="handoff",
        relation_signature=("precedence", "handoff"),
        observation_schema="p2.observation.v1",
        milestones=("precedence", "release", "handoff", "accept"),
        metadata={
            "p2": {
                "predecessor_id": "robot_a",
                "receiver_id": "robot_b",
                "stable_window": 2,
                "tolerance_m": 0.1,
            }
        },
    )


def _sample(tick, events=(), a=(0, 0, 0), b=(5, 0, 0)):
    return {
        "tick": tick,
        "sim_time_s": tick * 0.1,
        "robots": {"robot_a": {"position": list(a)}, "robot_b": {"position": list(b)}},
        "targets": {
            "release_zone": {"position": [1, 0, 0]},
            "handoff_zone": {"position": [2, 0, 0]},
            "accept_zone": {"position": [3, 0, 0]},
        },
        "external_events": list(events),
    }


def test_p2_external_milestones_require_receipts_and_stable_accept_window():
    evaluator = P2MilestoneEvaluator(contract=_contract())
    observations = [
        _sample(0, a=(1, 0, 0)),
        _sample(1, events=({"event": "release_receipt", "robot_id": "robot_a"},), a=(1, 0, 0)),
        _sample(2, events=({"event": "handoff_receipt", "robot_id": "robot_b"},), a=(1.5, 0, 0), b=(2, 0, 0)),
        _sample(3, events=({"event": "accept_receipt", "robot_id": "robot_b"},), a=(1.5, 0, 0), b=(3, 0, 0)),
        _sample(4, b=(3, 0, 0)),
    ]
    report = evaluator.evaluate(task=None, observations=observations, branch=None)
    assert report.status is EvaluationStatus.SUCCESS
    assert {item.predicate_id for item in report.evidence} == {"precedence", "release", "handoff", "accept"}
    assert all(item.status is EvidenceStatus.SATISFIED for item in report.evidence)
    assert report.evidence[-1].stability_window == 2
    assert all(item.evidence_hash for item in report.evidence)


def test_p2_missing_receipt_is_unknown_and_no_physical_sample_is_na():
    evaluator = P2MilestoneEvaluator(contract=_contract())
    report = evaluator.evaluate(task=None, observations=[{"tick": 0, "sim_time_s": 0.0}], branch=None)
    assert report.status is EvaluationStatus.NOT_APPLICABLE
    report = evaluator.evaluate(task=None, observations=[_sample(0, a=(1, 0, 0))], branch=None)
    assert report.status is EvaluationStatus.UNKNOWN
    assert any(item.predicate_id == "release" and item.status is EvidenceStatus.UNKNOWN for item in report.evidence)


def test_p2_bad_order_is_failure_when_receipts_arrive_out_of_order():
    evaluator = P2MilestoneEvaluator(contract=_contract())
    observations = [
        _sample(0, events=({"event": "accept_receipt", "robot_id": "robot_b"},), a=(1, 0, 0), b=(3, 0, 0)),
        _sample(1, events=({"event": "release_receipt", "robot_id": "robot_a"},), a=(1, 0, 0), b=(3, 0, 0)),
        _sample(2, events=({"event": "handoff_receipt", "robot_id": "robot_b"},), a=(1, 0, 0), b=(2, 0, 0)),
        _sample(3, b=(3, 0, 0)),
    ]
    report = evaluator.evaluate(task=None, observations=observations, branch=None)
    assert report.status is EvaluationStatus.FAILURE


def test_stable_window_requires_consecutive_ticks():
    evaluator = P2MilestoneEvaluator(contract=_contract())
    observations = [
        _sample(0, a=(1, 0, 0)),
        _sample(1, events=({"event": "release_receipt", "robot_id": "robot_a"},), a=(1, 0, 0)),
        _sample(3, events=({"event": "accept_receipt", "robot_id": "robot_b"},), a=(1, 0, 0), b=(3, 0, 0)),
        _sample(5, b=(3, 0, 0)),
    ]
    report = evaluator.evaluate(task=None, observations=observations, branch=None)
    accept = next(item for item in report.evidence if item.predicate_id == "accept")
    assert accept.status is EvidenceStatus.UNKNOWN
