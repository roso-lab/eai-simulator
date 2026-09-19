"""Focused offline checks for the deterministic feasibility oracle.

Covers inflation gating (the narrow-doorway dead layout), A* determinism,
per-node route certificates over the phase DAG, and the capability-based
minimum-team certificate. No provider map, Isaac, ROS, or network.
"""

import copy
from pathlib import Path

import pytest

from EAI.scenario_spec.geometry import FactoryMap, static_blocked_grid
from EAI.scenario_spec.oracle import (
    InflatedGrid,
    minimum_team_certificate,
    validate_oracle,
)
from EAI.scenario_spec.operators import instantiate
from EAI.scenario_spec.validation import ScenarioError

from test_scenario_family import _synthetic_map, _synthetic_template, _spec


def test_inflation_gates_narrow_doorway(tmp_path: Path) -> None:
    occupancy = _synthetic_map(tmp_path)  # 2.0 m doorway in the dividing wall
    blocked = static_blocked_grid(occupancy, {"composition": {"entities": []}})
    slim = InflatedGrid(occupancy, 0.5, blocked)
    wide = InflatedGrid(occupancy, 1.0, blocked)
    assert slim.path((2.0, 5.0), (8.0, 5.0)) is not None
    assert wide.path((2.0, 5.0), (8.0, 5.0)) is None


def test_astar_is_deterministic(tmp_path: Path) -> None:
    occupancy = _synthetic_map(tmp_path)
    blocked = static_blocked_grid(occupancy, {"composition": {"entities": []}})
    grid = InflatedGrid(occupancy, 0.5, blocked)
    first = grid.path((2.0, 5.0), (8.0, 5.0))
    rebuilt = InflatedGrid(occupancy, 0.5, blocked).path((2.0, 5.0), (8.0, 5.0))
    assert first == rebuilt and len(first) > 2


def test_oracle_routes_and_minimum_team(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    data = instantiate(_spec(template), template, occupancy, seed=5)
    report = validate_oracle(data, occupancy)
    assert report["status"] == "feasibility_validated"
    assert {(route["node"], route["agent"]) for route in report["routes"]} >= {
        ("move_to_cargo", "carter_1"), ("transport:carry", "carter_1")}
    team = report["minimum_team"]
    assert "carter_1" in team["necessary_robots"]
    assert team["minimum_team_size"] == 1
    assert team["forced_cooperation"] is False
    assert report["arm_collision_check"]["state"] == "not_installed"


def test_oracle_rejects_route_through_narrow_gap(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    data = instantiate(_spec(template), template, occupancy, seed=5)
    # Widen the robots beyond the 2.0 m doorway by shrinking the map resolution
    # is not possible post hoc; instead seal the doorway with a static beam.
    data["composition"]["entities"].append({
        "id": "doorseal", "asset_key": "safety_beam",
        "size_m": [0.6, 3.0, 0.45],
        "spawn_pose": {"position": [5.0, 5.0, 0.225], "rotation": [1.0, 0.0, 0.0, 0.0]},
        "physics": {"mode": "static", "mass_kg": None, "static_friction": 0.8,
                    "dynamic_friction": 0.6, "restitution": 0.05},
    })
    with pytest.raises(ScenarioError, match="no collision-free route"):
        validate_oracle(data, occupancy)


def test_minimum_team_certificate_pure_logic() -> None:
    data = {
        "robots": [
            {"id": "carter_1", "type": "carter", "attachments": [{"type": "z1"}]},
            {"id": "go2_1", "type": "go2", "attachments": []},
        ],
        "phase_graph": {"nodes": [
            {"id": "n1", "skill_key": "transport_object"},
            {"id": "n2", "skill_key": "navigate"},
        ]},
    }
    cert = minimum_team_certificate(data)
    assert cert["necessary_robots"] == ["carter_1"]
    assert cert["minimum_teams"] == [["carter_1"]]
    assert cert["single_agent_feasible"] == ["carter_1"]
    assert cert["forced_cooperation"] is False

    data["phase_graph"]["nodes"] = [{"id": "n2", "skill_key": "navigate"}]
    cert = minimum_team_certificate(data)
    assert cert["necessary_robots"] == []
    assert cert["forced_cooperation"] is False


def test_payload_policy_gates_transport_eligibility(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    spec = _spec(template)
    # Two armed robots so capability alone cannot force cooperation.
    spec["robots"][1]["attachments"] = ["z1"]
    data = instantiate(spec, template, occupancy, seed=5)  # cargo mass 8.0
    policy = {"carter": 40.0, "m20": 15.0, "go2": 8.0, "scout": 40.0}

    cert = minimum_team_certificate(data, payload_policy=policy)
    assert cert["necessary_robots"] == [] and cert["forced_cooperation"] is False  # 8 kg: either arm works

    from EAI.scenario_spec.operators import apply_delta
    heavy = apply_delta(copy.deepcopy(data), {"op": "set_mass", "ref": "cargo", "mass_kg": 30.0})
    cert = minimum_team_certificate(heavy, payload_policy=policy)
    assert cert["necessary_robots"] == ["carter_1"]  # 30 kg exceeds the m20's 15 kg
    assert cert["minimum_teams"] == [["carter_1"]]
    assert cert["forced_cooperation"] is False

    with pytest.raises(ScenarioError, match="cannot carry"):
        validate_oracle(heavy, occupancy, payload_policy={"carter": 5.0, "m20": 15.0})

    # A policy is an explicit experimental model. Missing robot types are
    # unknown/ineligible, never an implicit infinite payload allowance.
    cert = minimum_team_certificate(data, payload_policy={"carter": 40.0})
    assert cert["eligible_agents_by_node"]["transport"] == ["carter_1"]


def test_family_passes_oracle_on_factory_template() -> None:
    from test_scenario_family import _factory_or_skip

    from EAI.scenario_spec.operators import expand_family, skeleton_family

    template, occupancy = _factory_or_skip()
    family = skeleton_family("TRN-01", family_id="oracle_smoke", seed=17)
    record, instances = expand_family(family, template, occupancy)  # run_gates includes the oracle
    assert set(instances) == {"low", "high"}


def test_kinematic_entity_is_barrier_not_checkpoint(tmp_path: Path) -> None:
    from EAI.scenario_spec.geometry import validate_connectivity

    template, occupancy = _synthetic_template(tmp_path)
    data = instantiate(_spec(template), template, occupancy, seed=5)
    data["composition"]["entities"].append({
        "id": "moving_wall", "asset_key": "safety_beam",
        "size_m": [0.4, 1.0, 0.45],
        "spawn_pose": {"position": [5.0, 8.5, 0.225], "rotation": [1.0, 0.0, 0.0, 0.0]},
        "physics": {"mode": "kinematic", "mass_kg": 3.0, "static_friction": 0.8,
                    "dynamic_friction": 0.6, "restitution": 0.05},
    })
    assert validate_connectivity(data, occupancy)["status"] == "connectivity_validated"


def test_oracle_navigate_to_static_entity(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    data = instantiate(_spec(template), template, occupancy, seed=5)
    data["composition"]["entities"].append({
        "id": "wall_e", "asset_key": "safety_beam",
        "size_m": [0.3, 1.0, 0.45],
        "spawn_pose": {"position": [7.5, 5.0, 0.225], "rotation": [1.0, 0.0, 0.0, 0.0]},
        "physics": {"mode": "static", "mass_kg": None, "static_friction": 0.8,
                    "dynamic_friction": 0.6, "restitution": 0.05},
    })
    data["phase_graph"]["nodes"].append({
        "id": "inspect_wall", "skill_key": "navigate",
        "preferred_agent": "carter_1", "target_ref": "wall_e", "object_ref": None,
        "prerequisites": [], "timeout_s": 30.0,
    })
    report = validate_oracle(data, occupancy)
    assert any(r["node"] == "inspect_wall" for r in report["routes"])


def test_oracle_fallback_honors_payload_policy(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    spec = _spec(template)
    spec["robots"][1]["attachments"] = ["z1"]  # both armed; capability alone cannot force choice
    data = instantiate(spec, template, occupancy, seed=5)
    from EAI.scenario_spec.operators import apply_delta
    heavy = apply_delta(copy.deepcopy(data), {"op": "set_mass", "ref": "cargo", "mass_kg": 30.0})
    policy = {"carter": 40.0, "m20": 15.0, "go2": 8.0, "scout": 40.0}
    # m20 (15 kg) cannot carry 30 kg; the fallback must pick carter, not robots[0]
    report = validate_oracle(heavy, occupancy, payload_policy=policy)
    transport_routes = [r for r in report["routes"] if r["node"].startswith("transport")]
    assert all(r["agent"] == "carter_1" for r in transport_routes)
    assert report["minimum_team"]["payload_policy"] == policy

    # Nobody can carry 60 kg under this policy -> explicit rejection
    with pytest.raises(ScenarioError, match="cannot carry"):
        validate_oracle(heavy, occupancy, payload_policy={"carter": 5.0, "m20": 5.0})


def test_oracle_existence_fallback_tries_other_agents(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    spec = _spec(template)
    spec["robots"][1]["attachments"] = ["z1"]
    spec["phase_nodes"][0]["preferred_agent"] = None  # navigate without pinned agent
    data = instantiate(spec, template, occupancy, seed=5)
    report = validate_oracle(data, occupancy)
    assert report["status"] == "feasibility_validated"


def test_inflated_grid_cannot_leave_mapped_area(tmp_path: Path) -> None:
    from EAI.scenario_spec.oracle import InflatedGrid
    from EAI.scenario_spec.geometry import static_blocked_grid

    occupancy = _synthetic_map(tmp_path)
    blocked = static_blocked_grid(occupancy, {"composition": {"entities": []}})
    grid = InflatedGrid(occupancy, 0.5, blocked)
    # Map edge cells must not be passable: the route disk would leave the
    # mapped area (raw EDT treats outside as free).
    assert not grid.passable(0, 0)
    assert not grid.passable(grid.width - 1, grid.height - 1)
    assert grid.passable(grid.width // 2, grid.height // 2)


def test_swept_disk_clearance_detects_corner_cutting_between_pose_samples() -> None:
    from EAI.scenario_spec.geometry import ground_surface_swept_disk_clearance

    data = {"scene_key": "factory", "benchmark_task_id": "F-INS-01"}
    clear = ground_surface_swept_disk_clearance(data, (-3.3, -1.8), (-3.3, -0.8), 0.5)
    assert clear["surface_id"] == "main_stair"
    assert clear["clearance_m"] == pytest.approx(0.16)
    overlap = ground_surface_swept_disk_clearance(data, (-3.3, -1.8), (-2.7, -0.8), 0.5)
    assert overlap["surface_id"] == "main_stair"
    assert overlap["clearance_m"] < 0


def test_target_on_robot_spawn_is_rejected(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    data = instantiate(_spec(template), template, occupancy, seed=5)
    robot = data["robots"][0]
    data["composition"]["targets"][0]["position"] = list(robot["spawn_pose"]["position"][:2]) + [0.0]
    from EAI.scenario_spec.geometry import validate_geometry
    with pytest.raises(ScenarioError, match="target overlaps"):
        validate_geometry(data, occupancy)


def test_level_routing_gates_wheeled_robots(tmp_path: Path) -> None:
    from EAI.scenario_spec.oracle import validate_level_routing
    from EAI.scenario_spec.templates import StairLink, build_template

    occupancy = _synthetic_map(tmp_path)
    from EAI.scenario_spec.templates import Level, Zone

    template = build_template("lvl", "factory", (Zone("west", (0, 0, 4, 10), "storage"),),
                              occupancy)
    template = type(template)(template.key, template.scene_key, template.zones, template.anchors,
                              template.map_sha256,
                              (Level("ground", 0.0), Level("mezzanine", 2.8)),
                              (StairLink("stair1", (4.0, 4.0, 5.0, 6.0), 0.0, 2.6, "stair"),))

    def node(agent, target_z):
        return {"id": "n1", "skill_key": "navigate", "preferred_agent": agent,
                "target_ref": "goal_box", "object_ref": None, "prerequisites": [],
                "timeout_s": 30.0}

    data = {
        "robots": [{"id": "wheeled_1", "type": "carter", "attachments": [],
                    "spawn_pose": {"position": [1.0, 1.0, 0.2], "rotation": [1.0, 0, 0, 0]}},
                   {"id": "dog_1", "type": "go2", "attachments": [],
                    "spawn_pose": {"position": [1.0, 2.0, 0.4], "rotation": [1.0, 0, 0, 0]}},
                   {"id": "drone_1", "type": "cf2x", "attachments": [],
                    "spawn_pose": {"position": [1.0, 3.0, 1.2], "rotation": [1.0, 0, 0, 0]}}],
        "composition": {"entities": [
            {"id": "goal_box", "asset_key": "primitive_box", "size_m": [0.3, 0.3, 0.3],
             "spawn_pose": {"position": [2.0, 5.0, 2.95], "rotation": [1.0, 0, 0, 0]},
             "physics": {"mode": "static", "mass_kg": None, "static_friction": 0.8,
                         "dynamic_friction": 0.6, "restitution": 0.05}}],
            "targets": []},
        "phase_graph": {"nodes": []},
    }

    # 轮式目标在二层（z=2.95≈mezzanine 2.8）→ 无可用 stair → 拒绝
    data["phase_graph"]["nodes"] = [node("wheeled_1", 2.95)]
    with pytest.raises(ScenarioError, match="wheeled_1"):
        validate_level_routing(data, template)

    # 四足同样目标 → stair 可用 → 通过
    data["phase_graph"]["nodes"] = [node("dog_1", 2.95)]
    assert validate_level_routing(data, template)["status"] == "levels_gated"

    # 旋翼忽略层级 → 通过
    data["phase_graph"]["nodes"] = [node("drone_1", 2.95)]
    assert validate_level_routing(data, template)["status"] == "levels_gated"

    # 同层目标（z≈0）→ 不触发
    data["composition"]["entities"][0]["spawn_pose"]["position"][2] = 0.15
    data["phase_graph"]["nodes"] = [node("wheeled_1", 0.15)]
    assert validate_level_routing(data, template)["status"] == "levels_gated"

    # 无楼梯模板 → single_level 直通
    assert validate_level_routing(data)["status"] == "single_level"


def test_joint_transport_and_aerial_scan_certificates(tmp_path: Path) -> None:
    """真异构原语：联合施力容量求和 + 空中侦察独占，证书 forced=True。"""
    from copy import deepcopy
    template, occupancy = _synthetic_template(tmp_path)
    # 两台轮式 + 声明策略：单机 40kg，箱子 60kg → 必须联合
    spec = _spec(template)
    spec["robots"][0]["type"] = "m20"      # carrier: m20+z1
    spec["robots"][0]["attachments"] = ["z1"]
    spec["robots"][1]["type"] = "carter"   # relay: 无臂 carter
    spec["robots"][1]["attachments"] = []
    spec["entities"][0]["mass_kg"] = 60.0
    data = instantiate(spec, template, occupancy, seed=5)
    # 把 transport 换成 joint_transport（compile 后的 V2 用 skill_key/agents）
    for node in data["phase_graph"]["nodes"]:
        if node["skill_key"] == "transport_object":
            node["skill_key"] = "joint_transport"
            node["agents"] = [data["robots"][0]["id"], data["robots"][1]["id"]]
    policy = {"carter": 40.0, "m20": 40.0, "go2": 8.0, "scout": 40.0, "cf2x": 0.0}
    cert = minimum_team_certificate(data, payload_policy=policy)
    assert cert["forced_cooperation"] is True
    assert cert["minimum_team_size"] == 2
    # 容量不足时显式拒绝
    with pytest.raises(ScenarioError, match="joint capacity"):
        validate_oracle(data, occupancy, payload_policy={"carter": 10.0, "m20": 10.0})


def test_aerial_scan_requires_aerial_platform() -> None:
    """aerial_scan 只有空中平台能执行 → 删掉 cf2x 任务无解。"""
    from EAI.scenario_spec.validation import robot_capabilities
    cf2x = {"type": "cf2x", "attachments": []}
    m20 = {"type": "m20", "attachments": ["z1"]}
    assert "aerial" in robot_capabilities(cf2x)
    assert "aerial" not in robot_capabilities(m20)
