"""Case identity checks preserve task occupants without accepting unknown IDs."""
import pytest

from benchmark.receipt_adapter_v3 import (
    ReceiptAdapterError, _validate_case_observation_bindings,
)


def check(holder="lot_a", *, robot=None, zone="scan_station"):
    case = {
        "scene": {"zone_ids": ["scan_station"], "entity_ids": ["lot_a", "lot_b"]},
        "robot_pool": {"robots": [{"robot_id": "carrier-left"}]},
    }
    streams = {
        "zone_intervals": [{"zone_id": zone, "holder_id": holder,
                            "enter_tick": 0, "exit_tick": 10}],
        "hold_intervals": [] if robot is None else [
            {"object_id": "lot_a", "robot_id": robot, "start_tick": 0, "end_tick": 10}],
        "milestones": [], "task_events": [], "identity_records": {},
        "integrity_confirmed": True,
    }
    _validate_case_observation_bindings([{"streams": streams}], case)


@pytest.mark.parametrize("holder", ["lot_a", "lot_b", "carrier-left"])
def test_registered_entity_or_robot_can_occupy_zone(holder):
    check(holder, robot="carrier-left")


def test_unknown_occupant_is_rejected():
    with pytest.raises(ReceiptAdapterError, match="occupancy holders absent"):
        check("unregistered-lot")


def test_scene_entity_cannot_impersonate_contact_robot():
    with pytest.raises(ReceiptAdapterError, match="robots absent"):
        check(robot="lot_a")


def test_unregistered_zone_remains_rejected():
    with pytest.raises(ReceiptAdapterError, match="zones absent"):
        check(zone="zone-1")
