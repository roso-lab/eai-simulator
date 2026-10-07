from algorithm.system.coordination.bt_capabilities import BTCapability, CapabilityMission, BTStatus
from algorithm.common_coordination import CoordinationTask, RobotCapability


def test_bt_capability_auction_uses_utility_and_precedence():
    mission = CapabilityMission(
        (
            BTCapability(CoordinationTask("find", "find", frozenset({"camera"})), {"scout": 2.0}),
            BTCapability(CoordinationTask("decon", "decon", frozenset({"tool"}), ("find",)), {"worker": 3.0}),
        ),
        (
            RobotCapability("scout", frozenset({"camera"})),
            RobotCapability("worker", frozenset({"tool"})),
        ),
    )
    first = mission.tick()
    assert first.assignments[0].robot_id == "scout"
    mission.feedback("find", success=True, robot_id="scout")
    second = mission.tick()
    assert second.assignments[0].robot_id == "worker"


def test_bt_reauction_after_capability_loss():
    mission = CapabilityMission(
        (BTCapability(CoordinationTask("inspect", "inspect", frozenset({"camera"})), {"a": 2.0, "b": 1.0}),),
        (RobotCapability("a", frozenset({"camera"})), RobotCapability("b", frozenset({"camera"}))),
    )
    assert mission.tick().assignments[0].robot_id == "a"
    mission.feedback("inspect", success=False, robot_id="a", capability_lost=True)
    assert mission.tick().assignments[0].robot_id == "b"
