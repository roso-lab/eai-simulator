"""Second-mission multi-solution allocation matrix (offline admission fixture).

发起人裁定 (2026-09-29): the second missions must pass a multi-solution
executability acceptance.  For every second mission this matrix enumerates at
least two distinct legal team allocations over that task's public
``candidate_robot_pool``.  The acceptance bar is that the difference between
two legal choices is only physical (route length, timing), never "the harness
does not support this choice".

Legality is exactly the two offline admission layers, both of which every
expanded plan below must pass:

1. ``demo.benchmark.goal_driven_contract.validate_goal_plan(
       plan, task_spec=public_task, admission="interface")`` -- structural
       goal-plan admission (schema, pool membership, skill arguments, graph).
2. ``demo.benchmark.goal_house_episode.second_mission_constraint_violation(
       public_task, task) is None`` for every expanded plan task entry --
       the capability / mobility / region-access organization contract.

This module is admission/acceptance data, not runtime code: skill backends and
the simulator must not import it.  Isaac execution of every expanded plan
(minimal episodes) is owned by the GPU queue on the main thread; nothing here
starts Isaac, uses a GPU or network, changes ``scoreable``/``benchmark_ready``
gates, or touches frozen hashes and the 405 grid.

Pool-capacity findings are recorded verbatim in ``POOL_CAPACITY_NOTES``: an
axis the ruling asked for but the current public pool cannot realize is a
reportable gap, not a reason to invent an illegal allocation.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any, Iterable, Mapping, NamedTuple

MATRIX_SCHEMA = "eai.benchmark.second_mission_allocation_matrix"
MATRIX_VERSION = 1

# Axes are stable vocabulary shared by the fixture, the admission test, and the
# Isaac GPU-queue handoff: witness_baseline, swap_carrier, swap_observer,
# swap_carton_carrier, swap_guide, solo_guide, split_dish_carriers,
# swap_dish_carriers.


class TaskOverride(NamedTuple):
    """Re-assign one witness task (matched by task-id prefix) to new agents."""

    task_id_prefix: str
    assigned_agents: tuple[str, ...]


class AllocationRecord(NamedTuple):
    """One legal allocation of one second mission.

    ``basis_witness_index`` expands from that author witness plan (then applies
    ``agent_substitutions`` / ``task_overrides``); records with ``tasks`` carry
    fully constructed plan rows instead.  Either way
    :func:`expand_allocation_plan` returns a complete, self-contained goal plan
    that passes both admission layers against the task's public task.
    """

    task_id: str
    allocation_id: str
    axis: str
    description: str
    assigned_team: tuple[str, ...]
    basis_witness_index: int | None = None
    agent_substitutions: tuple[tuple[str, str], ...] = ()
    task_overrides: tuple[TaskOverride, ...] = ()
    tasks: tuple[Mapping[str, Any], ...] = ()
    schedule_starts: tuple[tuple[str, float], ...] = ()


def _row(task_id: str, skill: str, arguments: Mapping[str, Any],
         prerequisites: Iterable[str], agents: Iterable[str]) -> dict[str, Any]:
    from demo.benchmark.skill_ontology import required_capabilities

    return {
        "task_id": task_id,
        "skill": skill,
        "arguments": deepcopy(dict(arguments)),
        "prerequisites": list(prerequisites),
        "required_capabilities": sorted(required_capabilities(skill)),
        "assigned_agents": list(agents),
        "evidence_refs": [],
    }


def _factory_fire_swap_observer_rows() -> tuple[dict[str, Any], ...]:
    """Perception moves from Carter to the carrier M20; Carter keeps arrival.

    The team is the witness-a team.  The carrier delivers the extinguisher,
    releases it at ``fire_site``, and only then records the fire-scene
    observation from inside that region, so the observe task stays executable.
    Carter's fire-site rendezvous arrival is kept as its own navigation task.
    """
    from demo.benchmark.factory_fire_task import (
        BUTTON_MODE, CHANNEL_ROUTE, EXTINGUISHER_ID, SENSE_MODE,
    )

    scout, carter, carrier = "factory_scout_1", "factory_carter_1", "factory_m20_1"
    strike = "strike_button_o"
    return (
        _row("nav_button_o", "ground_navigation",
             {"region_id": "rescue_button", "route_ids": ["staging_to_button"]}, [], [scout]),
        _row(strike, "observe",
             {"region_id": "rescue_button", "subject_ref": "rescue_channel_button",
              "observation_mode": BUTTON_MODE}, ["nav_button_o"], [scout]),
        _row("carter_to_fire_o", "ground_navigation",
             {"region_id": "fire_site",
              "route_ids": ["staging_to_channel", CHANNEL_ROUTE]}, [strike], [carter]),
        _row("nav_cache_o", "ground_navigation",
             {"region_id": "extinguisher_cache", "route_ids": ["staging_to_cache"]},
             [strike], [carrier]),
        _row("pick_extinguisher_o", "pick",
             {"object_id": EXTINGUISHER_ID, "pickup_region_id": "extinguisher_cache",
              "handling_mode_id": "support_attach",
              "carrier_bindings": [
                  {"agent_id": carrier, "point_id": "support_center"}]},
             ["nav_cache_o"], [carrier]),
        _row("carry_extinguisher_o", "ground_navigation",
             {"region_id": "fire_site",
              "route_ids": ["cache_to_channel", CHANNEL_ROUTE]},
             ["pick_extinguisher_o", strike], [carrier]),
        _row("place_extinguisher_o", "place",
             {"object_id": EXTINGUISHER_ID, "placement_region_id": "fire_site"},
             ["carry_extinguisher_o"], [carrier]),
        _row("sense_fire_by_carrier_o", "observe",
             {"region_id": "fire_site", "subject_ref": "fire_site",
              "observation_mode": SENSE_MODE},
             ["place_extinguisher_o"], [carrier]),
        _row("scout_to_fire_o", "ground_navigation",
             {"region_id": "fire_site",
              "route_ids": ["button_to_channel", CHANNEL_ROUTE]}, [strike], [scout]),
    )


def _warehouse_inbound_swap_carton_rows() -> tuple[dict[str, Any], ...]:
    """Carton handling moves from the picker to the partner M20.

    The crate keeps the witness-a paired-support bindings (picker on
    ``support_left``, partner on ``support_right``).  After the crate is
    shelved, the partner returns for the carton's declared ``center_grip``,
    and the two M20 withdrawal legs swap so each robot exits from the aisle it
    actually ends in.
    """
    from demo.benchmark.warehouse_inbound_task import (
        ARRIVAL_MODE, CARTON_ID, CLOSEOUT_MODE, CRATE_ID,
    )

    carter, picker, partner = "warehouse_carter_1", "warehouse_m20_1", "warehouse_m20_2"
    return (
        _row("carter_to_buffer_c", "ground_navigation",
             {"region_id": "receiving_buffer", "route_ids": ["standby_a_to_buffer"]},
             [], [carter]),
        _row("see_crate_c", "observe",
             {"region_id": "receiving_buffer", "subject_ref": CRATE_ID,
              "observation_mode": ARRIVAL_MODE}, ["carter_to_buffer_c"], [carter]),
        _row("see_carton_c", "observe",
             {"region_id": "receiving_buffer", "subject_ref": CARTON_ID,
              "observation_mode": ARRIVAL_MODE}, ["see_crate_c"], [carter]),
        _row("picker_to_buffer_c", "ground_navigation",
             {"region_id": "receiving_buffer", "route_ids": ["standby_a_to_buffer"]},
             ["see_crate_c", "see_carton_c"], [picker]),
        _row("partner_to_buffer_c", "ground_navigation",
             {"region_id": "receiving_buffer", "route_ids": ["standby_b_to_buffer"]},
             ["see_crate_c", "see_carton_c"], [partner]),
        _row("shelve_crate_c", "cooperative_carry",
             {"object_id": CRATE_ID, "destination_region_id": "rack_aisle_a",
              "handling_mode_id": "paired_support",
              "carrier_bindings": [
                  {"agent_id": picker, "point_id": "support_left"},
                  {"agent_id": partner, "point_id": "support_right"}],
              "route_ids": ["buffer_to_rack_a"]},
             ["picker_to_buffer_c", "partner_to_buffer_c"], [picker, partner]),
        _row("partner_back_to_buffer_c", "ground_navigation",
             {"region_id": "receiving_buffer", "route_ids": ["rack_a_to_buffer"]},
             ["shelve_crate_c"], [partner]),
        _row("pick_carton_c", "pick",
             {"object_id": CARTON_ID, "pickup_region_id": "receiving_buffer",
              "handling_mode_id": "single_grip",
              "carrier_bindings": [
                  {"agent_id": partner, "point_id": "center_grip"}]},
             ["partner_back_to_buffer_c"], [partner]),
        _row("shelve_carton_c", "ground_navigation",
             {"region_id": "rack_aisle_b", "route_ids": ["buffer_to_rack_b"]},
             ["pick_carton_c"], [partner]),
        _row("place_carton_c", "place",
             {"object_id": CARTON_ID, "placement_region_id": "rack_aisle_b"},
             ["shelve_carton_c"], [partner]),
        _row("picker_withdraw_c", "ground_navigation",
             {"region_id": "warehouse_standby_b", "route_ids": ["rack_a_to_standby_b"]},
             ["shelve_crate_c"], [picker]),
        _row("partner_withdraw_c", "ground_navigation",
             {"region_id": "warehouse_standby_a", "route_ids": ["rack_b_to_standby_a"]},
             ["place_carton_c"], [partner]),
        _row("close_buffer_c", "observe",
             {"region_id": "receiving_buffer", "subject_ref": "receiving_buffer",
              "observation_mode": CLOSEOUT_MODE},
             ["picker_withdraw_c", "partner_withdraw_c"], [carter]),
        _row("carter_withdraw_c", "ground_navigation",
             {"region_id": "warehouse_standby_a", "route_ids": ["buffer_to_standby_a"]},
             ["close_buffer_c"], [carter]),
    )


def _restaurant_service_swap_dish_rows() -> tuple[dict[str, Any], ...]:
    """Swap the two dining-table lanes between Carter and the M20.

    Structure mirrors the parallel witness: Pepper still records both orders
    and both receipts; the M20 now carries plate A to table A and Carter
    carries plate B to table B, so each carrier's kitchen-table route is the
    physical difference.
    """
    from demo.benchmark.restaurant_service_task import (
        ORDER_KIND_A, ORDER_KIND_B, RECEIPT_KIND_A, RECEIPT_KIND_B,
    )

    pepper, m20, carter = "restaurant_pepper_1", "restaurant_m20_1", "restaurant_carter_1"
    return (
        _row("service_c_pepper_nav_table_a", "ground_navigation",
             {"region_id": "dining_table_a", "route_ids": ["service_staging_to_table_a"]},
             [], [pepper]),
        _row("service_c_pepper_observe_a", "observe",
             {"region_id": "dining_table_a", "subject_ref": "static_customer_a",
              "observation_mode": "customer_order"},
             ["service_c_pepper_nav_table_a"], [pepper]),
        _row("service_c_pepper_order_a", "relay",
             {"message_kind": ORDER_KIND_A, "region_id": "dining_table_a"},
             ["service_c_pepper_observe_a"], [pepper]),
        _row("service_c_pepper_nav_table_b", "ground_navigation",
             {"region_id": "dining_table_b", "route_ids": ["service_table_a_to_table_b"]},
             ["service_c_pepper_order_a"], [pepper]),
        _row("service_c_pepper_observe_b", "observe",
             {"region_id": "dining_table_b", "subject_ref": "static_customer_b",
              "observation_mode": "customer_order"},
             ["service_c_pepper_nav_table_b"], [pepper]),
        _row("service_c_pepper_order_b", "relay",
             {"message_kind": ORDER_KIND_B, "region_id": "dining_table_b"},
             ["service_c_pepper_observe_b"], [pepper]),
        _row("service_c_m20_nav_kitchen", "ground_navigation",
             {"region_id": "kitchen_service", "route_ids": ["restaurant_staging_to_kitchen"]},
             ["service_c_pepper_order_a"], [m20]),
        _row("service_c_m20_pick_a", "pick",
             {"object_id": "service_plate_apple_a", "pickup_region_id": "kitchen_service",
              "handling_mode_id": "support_attach",
              "carrier_bindings": [{"agent_id": m20, "point_id": "support_center"}]},
             ["service_c_m20_nav_kitchen"], [m20]),
        _row("service_c_m20_nav_table_a", "ground_navigation",
             {"region_id": "dining_table_a", "route_ids": ["service_kitchen_to_table_a"]},
             ["service_c_m20_pick_a"], [m20]),
        _row("service_c_m20_place_a", "place",
             {"object_id": "service_plate_apple_a", "placement_region_id": "dining_table_a"},
             ["service_c_m20_nav_table_a"], [m20]),
        _row("service_c_carter_nav_kitchen", "ground_navigation",
             {"region_id": "kitchen_service", "route_ids": ["restaurant_staging_to_kitchen"]},
             ["service_c_pepper_order_b"], [carter]),
        _row("service_c_carter_pick_b", "pick",
             {"object_id": "service_plate_apple_b", "pickup_region_id": "kitchen_service",
              "handling_mode_id": "support_attach",
              "carrier_bindings": [{"agent_id": carter, "point_id": "support_center"}]},
             ["service_c_carter_nav_kitchen"], [carter]),
        _row("service_c_carter_nav_table_b", "ground_navigation",
             {"region_id": "dining_table_b", "route_ids": ["service_kitchen_to_table_b"]},
             ["service_c_carter_pick_b"], [carter]),
        _row("service_c_carter_place_b", "place",
             {"object_id": "service_plate_apple_b", "placement_region_id": "dining_table_b"},
             ["service_c_carter_nav_table_b"], [carter]),
        _row("service_c_receipt_a", "relay",
             {"message_kind": RECEIPT_KIND_A, "region_id": "dining_table_b"},
             ["service_c_pepper_order_a", "service_c_pepper_order_b",
              "service_c_m20_place_a"], [pepper]),
        _row("service_c_receipt_b", "relay",
             {"message_kind": RECEIPT_KIND_B, "region_id": "dining_table_b"},
             ["service_c_pepper_order_b", "service_c_carter_place_b",
              "service_c_receipt_a"], [pepper]),
    )


_RESTAURANT_SWAP_DISH_STARTS: tuple[tuple[str, float], ...] = (
    # Parallel witness timing with the A/B carrier lanes swapped.
    ("service_c_pepper_nav_table_a", 0.0),
    ("service_c_pepper_observe_a", 8.0),
    ("service_c_pepper_order_a", 12.0),
    ("service_c_pepper_nav_table_b", 13.0),
    ("service_c_pepper_observe_b", 21.0),
    ("service_c_pepper_order_b", 25.0),
    ("service_c_m20_nav_kitchen", 13.0),
    ("service_c_m20_pick_a", 21.0),
    ("service_c_m20_nav_table_a", 25.0),
    ("service_c_m20_place_a", 33.0),
    ("service_c_carter_nav_kitchen", 26.0),
    ("service_c_carter_pick_b", 34.0),
    ("service_c_carter_nav_table_b", 38.0),
    ("service_c_carter_place_b", 46.0),
    ("service_c_receipt_a", 41.0),
    ("service_c_receipt_b", 54.0),
)


ALLOCATION_RECORDS: tuple[AllocationRecord, ...] = (
    # ---------------------------------------------------------------- FACTORY
    AllocationRecord(
        task_id="FACTORY-FIRE-01",
        allocation_id="factory_fire_witness_baseline_m20_1_carrier",
        axis="witness_baseline",
        description=(
            "作者见证 a 作基线：factory_scout_1 敲通道按钮，factory_carter_1 到火源做现场感知，"
            "factory_m20_1 以 support_attach 承载灭火器并释放到火源。"),
        assigned_team=("factory_scout_1", "factory_carter_1", "factory_m20_1"),
        basis_witness_index=0,
    ),
    AllocationRecord(
        task_id="FACTORY-FIRE-01",
        allocation_id="factory_fire_swap_carrier_m20_2",
        axis="swap_carrier",
        description=(
            "把灭火器交付者从 factory_m20_1 换成 factory_m20_2（作者见证 b 的等价物）；"
            "能力合同与路线集合不变，差异只在承载者的路程与时间。"),
        assigned_team=("factory_scout_1", "factory_carter_1", "factory_m20_2"),
        basis_witness_index=1,
    ),
    AllocationRecord(
        task_id="FACTORY-FIRE-01",
        allocation_id="factory_fire_swap_observer_to_carrier",
        axis="swap_observer",
        description=(
            "火场现场感知从 factory_carter_1 移到承载者 factory_m20_1（释放灭火器后在火源内做"
            " fire_scene_sense 观察）；Carter 保留火源会合到达，三机在火源会合不变。"),
        assigned_team=("factory_scout_1", "factory_carter_1", "factory_m20_1"),
        tasks=_factory_fire_swap_observer_rows(),
    ),
    # -------------------------------------------------------------- WAREHOUSE
    AllocationRecord(
        task_id="WAREHOUSE-INBOUND-01",
        allocation_id="warehouse_inbound_witness_baseline_picker_m20_1",
        axis="witness_baseline",
        description=(
            "作者见证 a 作基线：warehouse_m20_1 为取货者（木箱 support_left 加纸箱 center_grip），"
            "warehouse_m20_2 为配合者（support_right），warehouse_carter_1 做到货识别与关闭观察。"),
        assigned_team=("warehouse_carter_1", "warehouse_m20_1", "warehouse_m20_2"),
        basis_witness_index=0,
    ),
    AllocationRecord(
        task_id="WAREHOUSE-INBOUND-01",
        allocation_id="warehouse_inbound_swap_carrier_roles_picker_m20_2",
        axis="swap_carrier",
        description=(
            "交换两台 M20 的承载角色：warehouse_m20_2 成为取货者（support_left 加纸箱），"
            "warehouse_m20_1 成为配合者（support_right）（作者见证 b 的等价物）。"),
        assigned_team=("warehouse_carter_1", "warehouse_m20_2", "warehouse_m20_1"),
        basis_witness_index=1,
    ),
    AllocationRecord(
        task_id="WAREHOUSE-INBOUND-01",
        allocation_id="warehouse_inbound_swap_carton_carrier_to_partner",
        axis="swap_carton_carrier",
        description=(
            "木箱仍由 warehouse_m20_1(support_left)+warehouse_m20_2(support_right) 成对支撑上架，"
            "但纸箱的取放从取货者移交给配合者 warehouse_m20_2，两台 M20 的离场通道相应对调。"),
        assigned_team=("warehouse_carter_1", "warehouse_m20_1", "warehouse_m20_2"),
        tasks=_warehouse_inbound_swap_carton_rows(),
    ),
    # ---------------------------------------------------------- MODERN HOUSE
    AllocationRecord(
        task_id="MODERN_HOUSE-DELIVERY-01",
        allocation_id="house_delivery_witness_baseline_carrier_m20_1",
        axis="witness_baseline",
        description=(
            "作者见证 a 作基线：house_m20_1 以 support_attach 承载门口包裹并释放到室内暂存点，"
            "house_pepper_1 在静态签收点写回执。"),
        assigned_team=("house_pepper_1", "house_m20_1"),
        basis_witness_index=0,
    ),
    AllocationRecord(
        task_id="MODERN_HOUSE-DELIVERY-01",
        allocation_id="house_delivery_swap_carrier_m20_2",
        axis="swap_carrier",
        description=(
            "把包裹承载者从 house_m20_1 换成 house_m20_2（作者见证 b 的等价物）；"
            "签收仍由唯一的 house_pepper_1 完成。"),
        assigned_team=("house_pepper_1", "house_m20_2"),
        basis_witness_index=1,
    ),
    # ------------------------------------------------------------------ CITY
    AllocationRecord(
        task_id="CITY-EXIT-01",
        allocation_id="city_exit_witness_baseline_guide_go2",
        axis="witness_baseline",
        description=(
            "作者见证 a 作基线：go2_1 引导名册并核销人数，carter_1 穿过并检查通向车站的走廊。"),
        assigned_team=("go2_1", "carter_1"),
        basis_witness_index=0,
    ),
    AllocationRecord(
        task_id="CITY-EXIT-01",
        allocation_id="city_exit_swap_guide_to_carter",
        axis="swap_guide",
        description=(
            "把引导技能从 go2_1 换给同样具备 guide_people 的 carter_1，"
            "走廊清空检查换给 go2_1（作者见证 b 的等价物）。"),
        assigned_team=("carter_1", "go2_1"),
        basis_witness_index=1,
    ),
    AllocationRecord(
        task_id="CITY-EXIT-01",
        allocation_id="city_exit_solo_guide_go2",
        axis="solo_guide",
        description=(
            "单机队伍：go2_1 在完成引导与人数核销后兼任走廊清空检查；"
            "与基线的差异只在并行度与完成时间，不改变任何技能合同。"),
        assigned_team=("go2_1",),
        basis_witness_index=0,
        task_overrides=(
            TaskOverride("corridor_to_station", ("go2_1",)),
            TaskOverride("check_corridor", ("go2_1",)),
        ),
    ),
    # ------------------------------------------------------------- RESTAURANT
    AllocationRecord(
        task_id="RESTAURANT-SERVICE-01",
        allocation_id="restaurant_service_witness_baseline_serial_carter",
        axis="witness_baseline",
        description=(
            "作者见证 a 作基线：restaurant_pepper_1 记两桌订单，"
            "restaurant_carter_1 串行承载两份餐盘。"),
        assigned_team=("restaurant_pepper_1", "restaurant_carter_1"),
        basis_witness_index=0,
    ),
    AllocationRecord(
        task_id="RESTAURANT-SERVICE-01",
        allocation_id="restaurant_service_split_dishes_carter_a_m20_b",
        axis="split_dish_carriers",
        description=(
            "作者见证 b 的等价物：restaurant_carter_1 送 A 餐、restaurant_m20_1 送 B 餐，"
            "两份餐并行分摊到两台承载者。"),
        assigned_team=("restaurant_pepper_1", "restaurant_carter_1", "restaurant_m20_1"),
        basis_witness_index=1,
    ),
    AllocationRecord(
        task_id="RESTAURANT-SERVICE-01",
        allocation_id="restaurant_service_swap_dish_carriers_m20_a_carter_b",
        axis="swap_dish_carriers",
        description=(
            "交换两张餐桌的送餐者：restaurant_m20_1 送 A 餐、restaurant_carter_1 送 B 餐；"
            "与上一分配的差异只在两台承载者各自的厨房-餐桌路程。"),
        assigned_team=("restaurant_pepper_1", "restaurant_carter_1", "restaurant_m20_1"),
        tasks=_restaurant_service_swap_dish_rows(),
        schedule_starts=_RESTAURANT_SWAP_DISH_STARTS,
    ),
)

#: Axes the ruling asked for that the current public pool cannot realize.
#: These are reportable pool-capacity gaps, not license to invent allocations.
POOL_CAPACITY_NOTES: Mapping[str, str] = {
    "FACTORY-FIRE-01": (
        "池容量：只有 factory_m20_1 与 factory_m20_2 具备 pick/place，灭火器交付者仅两个合法选择；"
        "factory_carter_1 与 factory_scout_1 无抓取能力，不能成为交付者。"),
    "WAREHOUSE-INBOUND-01": (
        "池容量：只有两台 M20 具备 cooperative_carry，木箱成对支撑的组合固定为这两台；"
        "warehouse_carter_1 无 pick/cooperative_carry，不能进入承载对。"),
    "MODERN_HOUSE-DELIVERY-01": (
        "池容量：承载轴只有 house_m20_1/house_m20_2 两个合法选择，静态签收锁定在唯一的"
        " house_pepper_1（池中无第二台 pepper 可换取签收者），故无第三个合法分配。"),
    "CITY-EXIT-01": (
        "池容量：只有 go2_1 与 carter_1 具备 guide_people，引导者轴仅两个合法选择。"),
    "RESTAURANT-SERVICE-01": (
        "池容量缺口：发起人允许的“换 pepper 记单”轴无法实现——池中只有唯一的"
        " restaurant_pepper_1，且订单结果明确署名 Pepper；餐盘承载可在 carter/m20 间互换。"),
}


@lru_cache(maxsize=None)
def _package_for(task_id: str) -> Mapping[str, Any]:
    from demo.benchmark.second_mission_episode import second_mission_builders

    builders = second_mission_builders()
    if task_id not in builders:
        raise ValueError(f"unknown second mission: {task_id}")
    return builders[task_id]()


def matrix_task_ids() -> tuple[str, ...]:
    """Second-mission task ids covered by this matrix, in record order."""
    ids: list[str] = []
    for record in ALLOCATION_RECORDS:
        if record.task_id not in ids:
            ids.append(record.task_id)
    return tuple(ids)


def allocation_records(task_id: str) -> tuple[AllocationRecord, ...]:
    """All allocation records of one second mission."""
    return tuple(record for record in ALLOCATION_RECORDS if record.task_id == task_id)


def allocation_record(task_id: str, allocation_id: str) -> AllocationRecord:
    for record in allocation_records(task_id):
        if record.allocation_id == allocation_id:
            return record
    raise ValueError(f"unknown allocation {allocation_id} for {task_id}")


def matrix_public_task(task_id: str) -> Mapping[str, Any]:
    """The task's public task (cached, read-only; deepcopy before mutating)."""
    return _package_for(task_id)["public_task"]


def _schedule(tasks: list[dict[str, Any]], record: AllocationRecord) -> None:
    from demo.benchmark.goal_driven_fixtures import SKILL_DURATIONS_S

    starts = dict(record.schedule_starts)
    clock_s = 0.0
    for task in tasks:
        start_s = starts.get(task["task_id"], clock_s)
        task["execution"] = {
            "start_s": start_s,
            "duration_s": SKILL_DURATIONS_S[task["skill"]],
        }
        clock_s = start_s + SKILL_DURATIONS_S[task["skill"]]


def _expand_from_witness(record: AllocationRecord) -> dict[str, Any]:
    package = _package_for(record.task_id)
    witnesses = package["hidden_evaluator"]["witness_plans"]
    index = record.basis_witness_index
    if index is None or not 0 <= index < len(witnesses):
        raise ValueError(f"{record.allocation_id}: basis witness index is invalid")
    plan = deepcopy(dict(witnesses[index]["plan"]))
    plan["plan_id"] = record.allocation_id
    substitutions = dict(record.agent_substitutions)

    def substitute(agent: str) -> str:
        return substitutions.get(agent, agent)

    plan["assigned_team"] = [substitute(agent) for agent in record.assigned_team]
    for task in plan["tasks"]:
        for override in record.task_overrides:
            if task["task_id"].startswith(override.task_id_prefix):
                task["assigned_agents"] = list(override.assigned_agents)
        task["assigned_agents"] = [substitute(agent) for agent in task["assigned_agents"]]
        bindings = (task.get("arguments") or {}).get("carrier_bindings")
        if isinstance(bindings, list):
            for binding in bindings:
                if isinstance(binding, dict) and "agent_id" in binding:
                    binding["agent_id"] = substitute(binding["agent_id"])
    return plan


def _expand_constructed(record: AllocationRecord) -> dict[str, Any]:
    from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION

    tasks = [deepcopy(dict(row)) for row in record.tasks]
    _schedule(tasks, record)
    return {
        "schema": "eai.benchmark.goal_plan",
        "version": GOAL_PLAN_VERSION,
        "plan_id": record.allocation_id,
        "assigned_team": list(record.assigned_team),
        "tasks": tasks,
    }


def expand_allocation_plan(task_id: str, allocation_id: str) -> dict[str, Any]:
    """Expand one record into a complete, self-contained goal plan.

    The result is fresh deep-copied data (safe to mutate), carries the
    allocation id as its ``plan_id``, and is directly usable for the two
    admission layers: ``validate_goal_plan(..., admission="interface")`` and
    per-task ``second_mission_constraint_violation(public_task, task)``.
    """
    record = allocation_record(task_id, allocation_id)
    if record.tasks:
        return _expand_constructed(record)
    return _expand_from_witness(record)


def iter_expanded_allocations() -> Iterable[tuple[str, Mapping[str, Any], AllocationRecord, dict[str, Any]]]:
    """Yield ``(task_id, public_task, record, plan)`` for every matrix entry.

    This is the handoff shape for the Isaac GPU queue: each plan is a legal
    admission-time allocation whose minimal-episode execution is owned by that
    queue, not by this fixture.
    """
    for task_id in matrix_task_ids():
        public = matrix_public_task(task_id)
        for record in allocation_records(task_id):
            yield task_id, public, record, expand_allocation_plan(task_id, record.allocation_id)


__all__ = [
    "MATRIX_SCHEMA", "MATRIX_VERSION",
    "AllocationRecord", "TaskOverride",
    "ALLOCATION_RECORDS", "POOL_CAPACITY_NOTES",
    "allocation_record", "allocation_records", "expand_allocation_plan",
    "iter_expanded_allocations", "matrix_public_task", "matrix_task_ids",
]
