from copy import deepcopy
import pytest
from benchmark.calibration_runtime_v3 import MEASUREMENT_PLAN, MeteredNavigationProtocol


CONTRACT = {"robot_id": "robot", "task_id": "task", "target_ref": "node:finish"}


def test_dispatch_and_terminal_knowledge_require_actual_link_delivery():
    protocol = MeteredNavigationProtocol(CONTRACT)
    assert not protocol.advance(0)
    assert not protocol.advance(1)
    assert protocol.advance(2)
    protocol.observe(3, {"position_m": [1., 0., 0.], "speed_m_s": 0., "simulation_time_s": .06},
                     {"status": "completed", "stop_confirmed": True})
    assert protocol.terminal_feedback is None
    protocol.advance(3)
    protocol.advance(4)
    assert protocol.terminal_feedback is None
    protocol.advance(5)
    assert protocol.terminal_feedback["status"] == "completed"
    result = protocol.result(start_time_s=0., end_time_s=.1, physics_step_s=.02)
    assert result["traffic_complete"] and result["accounting_consistent"]
    assert result["accounting"]["transmitted_bits"] == sum(f["bits"] for f in result["frames"])
    assert {f["sender_id"] for f in result["frames"]} == {"coordinator", "robot"}
    assert result["physical_elapsed_s"] == .1
    assert result["nominal_capacity_bps"] == 409600
    assert result["reference_scales"] is None and not result["paper_score_eligible"]


def test_queued_data_is_not_reported_as_transmitted_or_complete():
    plan = deepcopy(MEASUREMENT_PLAN)
    plan["channel"]["bits_per_tick"] = 1
    protocol = MeteredNavigationProtocol(CONTRACT, plan=plan)
    protocol.advance(0)
    result = protocol.result(start_time_s=0., end_time_s=.02, physics_step_s=.02)
    assert result["accounting"]["transmitted_bits"] == 1
    assert result["accounting"]["submitted_bits"] > 1
    assert not result["traffic_complete"] and result["terminal_feedback"] is None


def test_duplicate_tick_cannot_create_free_transmission_capacity():
    protocol = MeteredNavigationProtocol(CONTRACT)
    protocol.advance(0)
    with pytest.raises(ValueError, match="once"):
        protocol.advance(0)


def test_arrival_before_stop_confirmation_cannot_end_resource_measurement():
    protocol = MeteredNavigationProtocol(CONTRACT)
    sample = {"position_m": [1., 0., 0.], "speed_m_s": .09, "simulation_time_s": .06}
    for tick in range(3):
        protocol.advance(tick)
    protocol.observe(3, sample, {"status": "completed", "stop_confirmed": False})
    for tick in range(3, 6):
        protocol.advance(tick)
    assert protocol.terminal_feedback is None
    assert not protocol._terminal_sent
    protocol.observe(6, {**sample, "speed_m_s": .001}, {"status": "completed", "stop_confirmed": True})
    for tick in range(6, 9):
        protocol.advance(tick)
    assert protocol.terminal_feedback["stop_confirmed"] is True
