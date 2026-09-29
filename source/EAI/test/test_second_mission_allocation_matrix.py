"""Admission checks for the second-mission multi-solution allocation matrix.

Every expanded allocation must pass both offline admission layers against its
task's public task:

1. ``validate_goal_plan(plan, task_spec=public, admission="interface")``
2. ``second_mission_constraint_violation(public, task) is None`` per task entry.

Isaac execution of the matrix (minimal episodes) is owned by the GPU queue;
this suite stays offline and never starts Isaac, ROS, or a network call.
"""

from demo.benchmark.goal_driven_contract import validate_goal_plan
from demo.benchmark.goal_house_episode import second_mission_constraint_violation
from demo.benchmark.second_mission_allocation_matrix import (
    ALLOCATION_RECORDS,
    POOL_CAPACITY_NOTES,
    allocation_records,
    expand_allocation_plan,
    matrix_public_task,
    matrix_task_ids,
)
from demo.benchmark.second_mission_episode import second_mission_builders

_PLAN_FIELDS = {"schema", "version", "plan_id", "assigned_team", "tasks"}
_TASK_FIELDS = {"task_id", "skill", "arguments", "prerequisites",
                "required_capabilities", "assigned_agents", "evidence_refs",
                "execution"}


def _admit(task_id: str, allocation_id: str) -> tuple[dict, dict]:
    public = matrix_public_task(task_id)
    plan = expand_allocation_plan(task_id, allocation_id)
    report = validate_goal_plan(plan, task_spec=public, admission="interface")
    return plan, report


def _tasks(plan: dict, skill: str) -> list[dict]:
    return [task for task in plan["tasks"] if task["skill"] == skill]


def _pick(plan: dict, object_id: str) -> dict:
    return next(task for task in _tasks(plan, "pick")
                if task["arguments"]["object_id"] == object_id)


def _place(plan: dict, object_id: str) -> dict:
    return next(task for task in _tasks(plan, "place")
                if task["arguments"]["object_id"] == object_id)


def _observe(plan: dict, mode: str) -> list[dict]:
    return [task for task in _tasks(plan, "observe")
            if task["arguments"].get("observation_mode") == mode]


def _agents(task: dict) -> list[str]:
    return list(task["assigned_agents"])


def test_matrix_covers_every_second_mission_with_two_admitted_allocations() -> None:
    builders = second_mission_builders()
    assert set(matrix_task_ids()) == set(builders)
    identifiers = [record.allocation_id for record in ALLOCATION_RECORDS]
    assert len(identifiers) == len(set(identifiers))
    for task_id in matrix_task_ids():
        records = allocation_records(task_id)
        assert len(records) >= 2, task_id
        assert len({record.axis for record in records}) >= 2, task_id
        for record in records:
            assert record.description.strip()
            plan, report = _admit(task_id, record.allocation_id)
            assert report["validated"] is True, (task_id, record.allocation_id)
            for task in plan["tasks"]:
                violation = second_mission_constraint_violation(
                    matrix_public_task(task_id), task)
                assert violation is None, (task_id, record.allocation_id, violation)


def test_expanded_plans_are_complete_legal_goal_plans() -> None:
    for task_id in matrix_task_ids():
        public = matrix_public_task(task_id)
        pool = {row["id"] for row in public["candidate_robot_pool"]}
        budget = public["budgets"]["max_active_agents"]
        for record in allocation_records(task_id):
            plan = expand_allocation_plan(task_id, record.allocation_id)
            assert set(plan) == _PLAN_FIELDS
            assert plan["schema"] == "eai.benchmark.goal_plan"
            assert plan["plan_id"] == record.allocation_id
            assert set(plan["assigned_team"]) <= pool
            assert len(plan["assigned_team"]) <= budget
            assert len({task["task_id"] for task in plan["tasks"]}) == len(plan["tasks"])
            for task in plan["tasks"]:
                assert set(task) == _TASK_FIELDS
                assert set(task["assigned_agents"]) <= set(plan["assigned_team"])
                assert set(task["execution"]) == {"start_s", "duration_s"}
                assert task["execution"]["duration_s"] > 0.0


def test_witness_based_expansions_reproduce_their_witness() -> None:
    builders = second_mission_builders()
    for record in ALLOCATION_RECORDS:
        if record.basis_witness_index is None or record.task_overrides:
            continue
        assert record.agent_substitutions == ()
        witness_plan = (builders[record.task_id]()["hidden_evaluator"]
                        ["witness_plans"][record.basis_witness_index]["plan"])
        plan = expand_allocation_plan(record.task_id, record.allocation_id)
        assert set(plan["assigned_team"]) == set(witness_plan["assigned_team"])
        assert plan["tasks"] == witness_plan["tasks"]


def test_allocations_are_semantically_distinct_within_each_task() -> None:
    for task_id in matrix_task_ids():
        signatures = set()
        for record in allocation_records(task_id):
            _, report = _admit(task_id, record.allocation_id)
            assert report["semantic_sha256"] not in signatures, task_id
            signatures.add(report["semantic_sha256"])


def test_factory_fire_swaps_carrier_and_observer_axes() -> None:
    baseline = expand_allocation_plan(
        "FACTORY-FIRE-01", "factory_fire_witness_baseline_m20_1_carrier")
    swap_carrier = expand_allocation_plan(
        "FACTORY-FIRE-01", "factory_fire_swap_carrier_m20_2")
    swap_observer = expand_allocation_plan(
        "FACTORY-FIRE-01", "factory_fire_swap_observer_to_carrier")
    extinguisher = "factory_extinguisher_01"
    assert _agents(_pick(baseline, extinguisher)) == ["factory_m20_1"]
    assert _agents(_pick(swap_carrier, extinguisher)) == ["factory_m20_2"]
    assert _agents(_place(swap_carrier, extinguisher)) == ["factory_m20_2"]
    # The button strike stays with Scout in every allocation.
    for plan in (baseline, swap_carrier, swap_observer):
        assert _agents(_observe(plan, "fixed_joint_button_strike")[0]) == ["factory_scout_1"]
    # The fire-scene perception is the swap_observer axis: Carter -> carrier M20.
    assert _agents(_observe(baseline, "fire_scene_sense")[0]) == ["factory_carter_1"]
    assert _agents(_observe(swap_carrier, "fire_scene_sense")[0]) == ["factory_carter_1"]
    carrier_sense = _observe(swap_observer, "fire_scene_sense")
    assert [task["task_id"] for task in carrier_sense] == ["sense_fire_by_carrier_o"]
    assert _agents(carrier_sense[0]) == ["factory_m20_1"]
    # Carter keeps its own fire-site rendezvous arrival in every allocation.
    for plan in (baseline, swap_carrier, swap_observer):
        carter_arrivals = [task for task in _tasks(plan, "ground_navigation")
                           if "factory_carter_1" in task["assigned_agents"]]
        assert carter_arrivals


def test_warehouse_inbound_swaps_carrier_roles_and_carton_carrier() -> None:
    task_id = "WAREHOUSE-INBOUND-01"
    baseline = expand_allocation_plan(task_id, "warehouse_inbound_witness_baseline_picker_m20_1")
    swap_roles = expand_allocation_plan(task_id, "warehouse_inbound_swap_carrier_roles_picker_m20_2")
    swap_carton = expand_allocation_plan(task_id, "warehouse_inbound_swap_carton_carrier_to_partner")
    crate, carton = "order_crate_01", "order_carton_01"

    def crate_bindings(plan: dict) -> dict[str, str]:
        carry = next(task for task in _tasks(plan, "cooperative_carry")
                     if task["arguments"]["object_id"] == crate)
        assert carry["arguments"]["handling_mode_id"] == "paired_support"
        return {row["agent_id"]: row["point_id"] for row in
                carry["arguments"]["carrier_bindings"]}

    assert crate_bindings(baseline) == {
        "warehouse_m20_1": "support_left", "warehouse_m20_2": "support_right"}
    assert crate_bindings(swap_roles) == {
        "warehouse_m20_2": "support_left", "warehouse_m20_1": "support_right"}
    # The carton-carrier swap keeps the crate pair and changes the carton holder.
    assert crate_bindings(swap_carton) == crate_bindings(baseline)
    assert _agents(_pick(baseline, carton)) == ["warehouse_m20_1"]
    assert _agents(_pick(swap_carton, carton)) == ["warehouse_m20_2"]
    assert (_pick(swap_carton, carton)["arguments"]["carrier_bindings"]
            == [{"agent_id": "warehouse_m20_2", "point_id": "center_grip"}])
    assert _agents(_place(swap_carton, carton)) == ["warehouse_m20_2"]
    # Carter keeps the arrival observations and the buffer closeout everywhere.
    for plan in (baseline, swap_roles, swap_carton):
        assert all("warehouse_carter_1" in task["assigned_agents"]
                   for task in _observe(plan, "inbound_arrival"))
        assert _agents(_observe(plan, "receiving_closeout")[0]) == ["warehouse_carter_1"]


def test_house_delivery_carrier_axis_swaps_the_only_capable_m20() -> None:
    task_id = "MODERN_HOUSE-DELIVERY-01"
    first = expand_allocation_plan(task_id, "house_delivery_witness_baseline_carrier_m20_1")
    second = expand_allocation_plan(task_id, "house_delivery_swap_carrier_m20_2")
    parcel = "doorstep_parcel_01"
    for plan, carrier in ((first, "house_m20_1"), (second, "house_m20_2")):
        pick = _pick(plan, parcel)
        assert pick["arguments"]["handling_mode_id"] == "support_attach"
        assert pick["arguments"]["carrier_bindings"] == [
            {"agent_id": carrier, "point_id": "support_center"}]
        assert _agents(_place(plan, parcel)) == [carrier]
        # The static signoff stays with the single Pepper in both allocations.
        assert _agents(_observe(plan, "static_signoff")[0]) == ["house_pepper_1"]
        assert all(task["skill"] != "relay" or _agents(task) == ["house_pepper_1"]
                   for task in plan["tasks"])


def test_city_exit_swaps_the_guide_and_admits_a_solo_team() -> None:
    task_id = "CITY-EXIT-01"
    baseline = expand_allocation_plan(task_id, "city_exit_witness_baseline_guide_go2")
    swap_guide = expand_allocation_plan(task_id, "city_exit_swap_guide_to_carter")
    solo = expand_allocation_plan(task_id, "city_exit_solo_guide_go2")

    def guide_agents(plan: dict) -> list[str]:
        return [agent for task in _tasks(plan, "guide_people") for agent in _agents(task)]

    assert guide_agents(baseline) == ["go2_1"]
    assert guide_agents(swap_guide) == ["carter_1"]
    assert _agents(_observe(baseline, "corridor_clearance")[0]) == ["carter_1"]
    assert _agents(_observe(swap_guide, "corridor_clearance")[0]) == ["go2_1"]
    # The solo allocation keeps the guide with go2 and merges the corridor leg.
    assert solo["assigned_team"] == ["go2_1"]
    assert guide_agents(solo) == ["go2_1"]
    assert _agents(_observe(solo, "corridor_clearance")[0]) == ["go2_1"]
    assert all(set(task["assigned_agents"]) == {"go2_1"} for task in solo["tasks"])


def test_restaurant_service_swaps_dish_carriers_between_tables() -> None:
    task_id = "RESTAURANT-SERVICE-01"
    serial = expand_allocation_plan(task_id, "restaurant_service_witness_baseline_serial_carter")
    split = expand_allocation_plan(task_id, "restaurant_service_split_dishes_carter_a_m20_b")
    swap = expand_allocation_plan(task_id, "restaurant_service_swap_dish_carriers_m20_a_carter_b")
    plate_a, plate_b = "service_plate_apple_a", "service_plate_apple_b"
    assert _agents(_place(serial, plate_a)) == ["restaurant_carter_1"]
    assert _agents(_place(serial, plate_b)) == ["restaurant_carter_1"]
    assert _agents(_place(split, plate_a)) == ["restaurant_carter_1"]
    assert _agents(_place(split, plate_b)) == ["restaurant_m20_1"]
    assert _agents(_place(swap, plate_a)) == ["restaurant_m20_1"]
    assert _agents(_place(swap, plate_b)) == ["restaurant_carter_1"]
    for plan in (serial, split, swap):
        # Pepper keeps both order relays and both receipts in every allocation.
        relays = _tasks(plan, "relay")
        assert {task["arguments"]["message_kind"] for task in relays} >= {
            "restaurant_order:dining_table_a:service_plate_apple_a",
            "restaurant_order:dining_table_b:service_plate_apple_b",
            "restaurant_service_receipt:dining_table_a:service_plate_apple_a",
            "restaurant_service_receipt:dining_table_b:service_plate_apple_b",
        }
        assert all(_agents(task) == ["restaurant_pepper_1"] for task in relays)
        for plate, table in ((plate_a, "dining_table_a"), (plate_b, "dining_table_b")):
            assert _place(plan, plate)["arguments"]["placement_region_id"] == table


def test_pool_capacity_notes_record_the_axes_the_pool_cannot_realize() -> None:
    assert set(POOL_CAPACITY_NOTES) == set(matrix_task_ids())
    for task_id, note in POOL_CAPACITY_NOTES.items():
        assert note.strip(), task_id
    # The ruling's "swap the order-taking Pepper" axis is a real pool gap:
    # the restaurant pool has exactly one Pepper.
    restaurant = matrix_public_task("RESTAURANT-SERVICE-01")
    peppers = [row for row in restaurant["candidate_robot_pool"] if row["type"] == "pepper"]
    assert len(peppers) == 1
    assert "pepper" in POOL_CAPACITY_NOTES["RESTAURANT-SERVICE-01"]


def test_expansions_are_independent_deep_copies() -> None:
    record = allocation_records("FACTORY-FIRE-01")[0]
    first = expand_allocation_plan(record.task_id, record.allocation_id)
    first["tasks"][0]["assigned_agents"] = ["factory_m20_2"]
    first["assigned_team"].append("factory_m20_2")
    second = expand_allocation_plan(record.task_id, record.allocation_id)
    assert second["tasks"][0]["assigned_agents"] == ["factory_scout_1"]
    assert second["assigned_team"] == list(record.assigned_team)
