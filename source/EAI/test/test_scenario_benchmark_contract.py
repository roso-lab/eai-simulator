"""Pure admission checks for heterogeneous-team benchmark candidates."""

import pytest

from EAI.scenario_spec.benchmark_contract import benchmark_eligibility_contract


def _scenario(robots, nodes):
    return {
        "robots": robots,
        "composition": {
            "entities": [{
                "id": "cargo", "physics": {"mass_kg": 5.0},
            }],
        },
        "phase_graph": {"nodes": nodes},
    }


def _carrier(robot_id="carter_1", robot_type="carter"):
    return {"id": robot_id, "type": robot_type, "attachments": [{"type": "z1"}]}


def test_trn01_like_single_carrier_is_rejected():
    data = _scenario(
        [_carrier(), {"id": "go2_1", "type": "go2", "attachments": []}],
        [
            {"id": "approach", "skill_key": "navigate"},
            {"id": "transport", "skill_key": "transport_object", "object_ref": "cargo",
             "prerequisites": ["approach"]},
        ],
    )
    report = benchmark_eligibility_contract(
        data, payload_policy={"carter": 20.0, "go2": 8.0}
    )
    assert report["status"] == "fail"
    assert report["eligible"] is False
    assert report["minimum_team"]["minimum_team_size"] == 1
    assert report["minimum_team"]["single_agent_feasible"] == ["carter_1"]


def test_capability_heterogeneous_team_is_accepted():
    data = _scenario(
        [_carrier(), {"id": "cf2x_1", "type": "cf2x", "attachments": []}],
        [
            {"id": "transport", "skill_key": "transport_object", "object_ref": "cargo"},
            {"id": "survey", "skill_key": "aerial_scan"},
        ],
    )
    report = benchmark_eligibility_contract(
        data, payload_policy={"carter": 20.0, "cf2x": 2.0}
    )
    assert report["status"] == "pass"
    assert report["eligible"] is True
    assert report["minimum_team"]["minimum_team_size"] == 2
    assert report["minimum_team"]["forced_heterogeneity"] is True
    assert report["minimum_team"]["single_agent_feasible"] == []
    assert all(item["status"] == "pass" for item in report["findings"])


def test_different_robot_types_do_not_hide_single_agent_solution():
    data = _scenario(
        [_carrier(), _carrier("m20_1", "m20")],
        [{"id": "transport", "skill_key": "transport_object", "object_ref": "cargo"}],
    )
    report = benchmark_eligibility_contract(
        data, payload_policy={"carter": 20.0, "m20": 20.0}
    )
    assert report["status"] == "fail"
    assert report["minimum_team"]["forced_heterogeneity"] is False
    assert set(report["minimum_team"]["single_agent_feasible"]) == {"carter_1", "m20_1"}


def test_missing_payload_policy_is_rejected():
    data = _scenario(
        [_carrier(), {"id": "cf2x_1", "type": "cf2x", "attachments": []}],
        [
            {"id": "transport", "skill_key": "transport_object", "object_ref": "cargo"},
            {"id": "survey", "skill_key": "aerial_scan"},
        ],
    )
    report = benchmark_eligibility_contract(data)
    assert report["status"] == "fail"
    finding = next(item for item in report["findings"] if item["check"] == "payload_policy")
    assert finding["status"] == "fail"


def test_escort_only_prerequisite_does_not_establish_heterogeneous_necessity():
    data = _scenario(
        [_carrier(), {"id": "cf2x_1", "type": "cf2x", "attachments": []}],
        [
            {"id": "escort_to_delivery", "skill_key": "navigate"},
            {"id": "transport", "skill_key": "transport_object", "object_ref": "cargo",
             "prerequisites": ["escort_to_delivery"]},
        ],
    )
    # This graph deliberately uses a navigation prerequisite as an invalid
    # escort stand-in: the transport itself is fully executable by the carrier.
    report = benchmark_eligibility_contract(
        data, payload_policy={"carter": 20.0, "cf2x": 2.0}
    )
    assert report["status"] == "fail"
    finding = next(item for item in report["findings"] if item["check"] == "escort_prerequisite_only")
    assert finding["status"] == "fail"


def _joint_instance(combo: bool):
    robots = [
        {"id": "m20_1", "type": "m20", "attachments": [{"type": "ur5"}]},
        {"id": "carter_1", "type": "carter", "attachments": []},
    ]
    nodes = [
        {"id": "a_to_crate", "skill_key": "navigate", "preferred_agent": "m20_1"},
        {"id": "b_to_crate", "skill_key": "navigate", "preferred_agent": "carter_1"},
        {
            "id": "joint_carry", "skill_key": "joint_transport",
            "agents": ["m20_1", "carter_1"], "object_ref": "cargo",
            "prerequisites": ["a_to_crate", "b_to_crate"],
        },
    ]
    if combo:
        robots.insert(0, {"id": "cf2x_1", "type": "cf2x", "attachments": []})
        nodes.insert(0, {"id": "bee_locate", "skill_key": "aerial_scan"})
        nodes[1]["prerequisites"] = ["bee_locate"]
        nodes[2]["prerequisites"] = ["bee_locate"]
    return _scenario(robots, nodes)


@pytest.mark.parametrize("combo", [False, True], ids=["heavy_wh_joint", "combo_isr_heavy"])
def test_joint_instances_keep_team_approaches_and_pass_contract(combo):
    report = benchmark_eligibility_contract(
        _joint_instance(combo),
        payload_policy={"m20": 40.0, "carter": 40.0, "cf2x": 10.0},
    )
    assert report["status"] == "pass"
    assert report["eligible"] is True
    assert report["counterfactual"]["single_agent_feasible"] == []
    assert {item["status"] for item in report["findings"]} == {"pass"}
