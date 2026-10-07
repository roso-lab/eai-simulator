from algorithm.common_coordination import RobotCapability
from algorithm.system.coordination.hybrid_susd.adapter import HybridSUSDAllocator, HybridTask


def test_market_and_susd_respect_q_y_capabilities():
    allocator = HybridSUSDAllocator.from_capabilities(
        [
            RobotCapability("sensor", frozenset({"navigate", "sensor"})),
            RobotCapability("terrain", frozenset({"navigate", "terrain", "carry_extinguisher"})),
            RobotCapability("button", frozenset({"navigate", "button"})),
        ],
        {"sensor": (0.0, 0.0), "terrain": (0.0, 0.0), "button": (0.0, 0.0)},
    )
    plan = allocator.allocate(
        [
            HybridTask("blue", frozenset({"sensor"}), (1.0, 0.0)),
            HybridTask("yellow", frozenset({"terrain", "carry_extinguisher"}), (1.0, 0.0), priority=1.0),
            HybridTask("green", frozenset({"button"}), (1.0, 0.0)),
        ]
    )
    assert plan.mapping == {"button": "green", "sensor": "blue", "terrain": "yellow"}
    assert plan.unassigned_task_ids == ()
    assert plan.market_objective <= plan.objective


def test_repair_excludes_failed_member_and_marks_adapter_source():
    allocator = HybridSUSDAllocator.from_capabilities(
        [
            RobotCapability("terrain-a", frozenset({"terrain"})),
            RobotCapability("terrain-b", frozenset({"terrain"})),
        ],
        {"terrain-a": (0.0, 0.0), "terrain-b": (1.0, 0.0)},
    )
    tasks = [HybridTask("red", frozenset({"terrain"}))]
    first = allocator.allocate(tasks)
    repaired = allocator.repair(tasks, first, failed_robot_ids=(first.assignments[0].robot_id,), delayed_task_ids=("red",))
    assert repaired.mapping == {"terrain-b": "red"}
    assert repaired.source == "official-hybrid-susd-adapter-reallocation"
