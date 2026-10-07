from algorithm.common_coordination import RobotCapability
from algorithm.system.coordination.mrta_heuristic.adapter import HeuristicTask, HeuristicTaskAllocator


def test_heuristic_allocator_respects_capabilities_and_is_deterministic():
    agents = [
        RobotCapability("sensor", frozenset({"navigate", "sensor"})),
        RobotCapability("terrain", frozenset({"navigate", "terrain", "carry_extinguisher"})),
    ]
    allocator = HeuristicTaskAllocator.from_capabilities(
        agents, {"sensor": (4.0, 0.0), "terrain": (0.0, 0.0)}
    )
    plan = allocator.allocate(
        [
            HeuristicTask("blue", frozenset({"sensor"}), (3.0, 0.0)),
            HeuristicTask("yellow", frozenset({"carry_extinguisher"}), (1.0, 0.0), priority=1.0),
        ]
    )
    assert plan.mapping == {"sensor": "blue", "terrain": "yellow"}


def test_plan_repair_reassigns_failed_agent_and_keeps_unaffected_tasks():
    tasks = [
        HeuristicTask("red", frozenset({"terrain"}), (0.0, 0.0)),
        HeuristicTask("blue", frozenset({"sensor"}), (0.0, 0.0)),
    ]
    allocator = HeuristicTaskAllocator.from_capabilities(
        [
            RobotCapability("terrain-a", frozenset({"terrain"})),
            RobotCapability("terrain-b", frozenset({"terrain"})),
            RobotCapability("sensor-a", frozenset({"sensor"})),
        ],
        {"terrain-a": (0.0, 0.0), "terrain-b": (1.0, 0.0), "sensor-a": (0.0, 0.0)},
    )
    initial = allocator.allocate(tasks)
    repaired = allocator.repair(tasks, initial, failed_robot_ids=("terrain-a",))
    assert repaired.mapping["sensor-a"] == "blue"
    assert repaired.mapping["terrain-b"] == "red"
    assert repaired.source == "official-mrta-planRepair-equivalent"
