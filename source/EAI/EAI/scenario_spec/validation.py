"""Strict JSON/schema and semantic checks. Never imports Isaac or calls a provider."""

from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path

from EAI.hmrs_env.env_diy import catalog
from EAI_assets.scenario_assets import DECLARED_ASSETS
from .schema import ROBOT_PROFILES, SKILLS, scenario_schema


class ScenarioError(ValueError):
    pass


def validate_node_timing(node, *, skill_field="skill_key"):
    wait_yaw = node.get("wait_yaw_rad")
    if wait_yaw is not None:
        if node[skill_field] != "wait":
            raise ScenarioError(f"{node['id']}: wait_yaw_rad is only supported for wait")
        if (isinstance(wait_yaw, bool) or not isinstance(wait_yaw, (int, float))
                or not math.isfinite(wait_yaw) or not -math.pi <= wait_yaw <= math.pi):
            raise ScenarioError(f"{node['id']}: wait_yaw_rad must be finite and within [-pi, pi]")
    scan_yaw = node.get("scan_yaw_rad")
    if scan_yaw is not None:
        if node[skill_field] != "aerial_scan":
            raise ScenarioError(f"{node['id']}: scan_yaw_rad is only supported for aerial_scan")
        if (isinstance(scan_yaw, bool) or not isinstance(scan_yaw, (int, float))
                or not math.isfinite(scan_yaw) or not -math.pi <= scan_yaw <= math.pi):
            raise ScenarioError(f"{node['id']}: scan_yaw_rad must be finite and within [-pi, pi]")
    scan_height = node.get("scan_height_m")
    if scan_height is not None:
        if node[skill_field] != "aerial_scan":
            raise ScenarioError(f"{node['id']}: scan_height_m is only supported for aerial_scan")
        if (isinstance(scan_height, bool) or not isinstance(scan_height, (int, float))
                or not math.isfinite(scan_height) or not 0 < scan_height <= 100):
            raise ScenarioError(f"{node['id']}: scan_height_m must be finite and within (0, 100]")
    duration = node.get("duration_s")
    if node[skill_field] == "wait":
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration <= 0
                or duration > node.get("timeout_s", 60.0)):
            raise ScenarioError(f"{node['id']}: wait requires positive duration_s no greater than timeout_s")
    elif duration is not None:
        raise ScenarioError(f"{node['id']}: duration_s is only supported for wait")
    resources = node.get("resources", [])
    if (not isinstance(resources, list)
            or any(not isinstance(resource, str) or not resource for resource in resources)
            or len(resources) != len(set(resources))):
        raise ScenarioError(f"{node['id']}: resources must be distinct nonempty names")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_scenario(text):
    def reject_constant(value):
        raise ScenarioError(f"non-finite JSON number: {value}")
    try:
        return validate_scenario(json.loads(text, object_pairs_hook=_unique_object, parse_constant=reject_constant))
    except json.JSONDecodeError as exc:
        raise ScenarioError(str(exc)) from exc


def load_scenario(path):
    source = Path(path)
    if source.stat().st_size > 1_000_000:
        raise ScenarioError("scenario JSON exceeds 1 MB")
    return parse_scenario(source.read_text(encoding="utf-8"))


def robot_capabilities(robot, robot_type=None):
    caps = {"navigation"}
    for item in robot.get("attachments") or []:
        kind = item["type"] if isinstance(item, dict) else item
        if kind in {"ur5", "z1"}:
            caps.add("manipulation")
    key = robot.get("type", robot_type)
    from .schema import ROBOT_PROFILES
    if ROBOT_PROFILES.get(key, {}).get("mobility") == "aerial":
        caps.add("aerial")
    return caps


# V2 is factory-locked, so the probed factory floors are the default level
# contract; callers with an explicit template override via known_floors.
_DEFAULT_FACTORY_FLOORS = (0.0, 0.85, 1.57)


def validate_scenario(payload, known_floors=None):
    from jsonschema import Draft202012Validator

    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ScenarioError("all numbers must be finite")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)
    finite(payload)
    errors = sorted(Draft202012Validator(scenario_schema()).iter_errors(payload), key=lambda error: str(error.path))
    if errors:
        raise ScenarioError("; ".join(f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in errors[:8]))
    data = deepcopy(payload)
    diagnostic_scene = {"W-DAG-01": "warehouse_nvd", "MH-DAG-01": "modern_house_nvd"}.get(
        data.get("benchmark_task_id"))
    if diagnostic_scene is not None and data["scene_key"] != diagnostic_scene:
        raise ScenarioError("diagnostic benchmark_task_id does not match scene_key")
    if known_floors is None:
        known_floors = _DEFAULT_FACTORY_FLOORS
    counts = Counter()
    ids = set()
    for item in [*data["robots"], *data["composition"]["entities"], *data["composition"]["targets"]]:
        if item["id"] in ids:
            raise ScenarioError(f"duplicate entity/robot/target id: {item['id']}")
        ids.add(item["id"])
        if "spawn_pose" in item:
            try:
                pose = catalog.normalize_spawn_pose(item["spawn_pose"])
            except ValueError as exc:
                raise ScenarioError(f"{item['id']}: {exc}") from exc
            # The initial geometry gate is a ground-floor, yaw-only contract.
            if abs(pose["rotation"][1]) > 1e-7 or abs(pose["rotation"][2]) > 1e-7:
                raise ScenarioError(f"{item['id']}: only yaw rotations are supported")
            item["spawn_pose"] = {key: list(value) for key, value in pose.items()}
    for robot in data["robots"]:
        key = robot["type"]
        counts[key] += 1
        if robot["id"] != f"{key}_{counts[key]}":
            raise ScenarioError(f"robot id must be {key}_{counts[key]} to match EAI runtime naming")
        controller_cfg = robot["controller"]["cfg"]
        vertical_go2_policy = (
            data.get("benchmark_task_id") == "F-VERT-01"
            and key == "go2"
            and controller_cfg in {
                "GO2_MIXED_STAIRS_CFG",
                "GO2_MIXED_STAIRS_CONTINUED_CFG",
            }
        )
        if controller_cfg != catalog.default_controller_cfg(key) and not vertical_go2_policy:
            raise ScenarioError(f"{robot['id']}: use the registered default controller")
        attachments = [item["type"] for item in robot["attachments"]]
        if len(attachments) != len(set(attachments)):
            raise ScenarioError(f"{robot['id']}: duplicate attachment")
        catalog.validate_attachment_types(key, attachments)
        for attachment in robot["attachments"]:
            expected = {"ur5": "UR5_IK_CFG", "z1": "Z1_IK_CFG"}.get(attachment["type"])
            if attachment["controller"] != (None if expected is None else {"mode": "default", "cfg": expected}):
                raise ScenarioError(f"{robot['id']}: invalid attachment controller")
        spawn_z = robot["spawn_pose"]["position"][2]
        profile_z = ROBOT_PROFILES[key]["spawn_z_m"]
        floors_r = sorted(known_floors) or (0.0,)
        if ROBOT_PROFILES[key]["mobility"] == "aerial":
            # An airborne start is a task viewpoint, not a floor-supported
            # root pose. Continuous voxel/body-envelope admission owns the
            # actual clearance; do not force every drone into the same height.
            if spawn_z <= ROBOT_PROFILES[key]["radius_m"] + 0.1:
                raise ScenarioError(f"{robot['id']}: aerial spawn must clear the ground envelope")
        elif not any(abs(spawn_z - (floor + profile_z)) <= 1e-6 for floor in floors_r):
            raise ScenarioError(
                f"{robot['id']}: spawn z must equal profile spawn_z_m on a known floor")
    entities = {item["id"]: item for item in data["composition"]["entities"]}
    for entity in entities.values():
        physics = entity["physics"]
        mode = physics["mode"]
        if mode not in DECLARED_ASSETS[entity["asset_key"]].modes:
            raise ScenarioError(f"{entity['id']}: unsupported physics mode {mode}")
        if (physics["mass_kg"] is not None) != (mode in {"dynamic", "kinematic"}):
            raise ScenarioError(f"{entity['id']}: mass required only for dynamic/kinematic bodies")
        if physics["dynamic_friction"] > physics["static_friction"]:
            raise ScenarioError(f"{entity['id']}: dynamic friction exceeds static friction")
        bottom = entity["spawn_pose"]["position"][2] - entity["size_m"][2] / 2
        floors = sorted(known_floors) or (0.0,)
        if not any(-1e-6 <= bottom - floor <= 0.1 for floor in floors):
            floors_text = ", ".join(f"{floor:.2f}" for floor in floors)
            raise ScenarioError(
                f"{entity['id']}: bottom must lie on a known floor ({floors_text})")
    for target in data["composition"]["targets"]:
        floors_t = sorted(known_floors) or (0.0,)
        if not any(abs(target["position"][2] - floor) <= 1e-6 for floor in floors_t):
            raise ScenarioError(f"{target['id']}: targets must sit on a known floor")
    nodes = data["phase_graph"]["nodes"]
    graph = {node["id"]: node["prerequisites"] for node in nodes}
    if len(graph) != len(nodes):
        raise ScenarioError("duplicate phase id")
    robot_names = {robot["id"] for robot in data["robots"]}
    lifecycle = data.get("runtime_lifecycle")
    if lifecycle is None:
        lifecycle = {
            "initial_agent_ids": sorted(robot_names),
            "preloaded_reserve_agent_ids": [],
        }
        data["runtime_lifecycle"] = lifecycle
    initial_agent_ids = lifecycle["initial_agent_ids"]
    reserve_agent_ids = lifecycle["preloaded_reserve_agent_ids"]
    initial_agents = set(initial_agent_ids)
    reserve_agents = set(reserve_agent_ids)
    if (len(initial_agents) != len(initial_agent_ids)
            or len(reserve_agents) != len(reserve_agent_ids)):
        raise ScenarioError("runtime lifecycle agent ids must be unique")
    if initial_agents & reserve_agents:
        raise ScenarioError("initial and preloaded reserve agent ids must be disjoint")
    if initial_agents | reserve_agents != robot_names:
        raise ScenarioError(
            "runtime lifecycle inventory must partition every configured robot")
    fault_events = data.get("fault_events", [])
    fault_ids = set()
    active_agents = set(initial_agents)
    available_reserves = set(reserve_agents)
    capability_versions = {agent_id: 1 for agent_id in robot_names}
    for event in sorted(fault_events, key=lambda item: (item["time_s"], item["id"])):
        if event["id"] in fault_ids:
            raise ScenarioError(f"duplicate fault event id: {event['id']}")
        fault_ids.add(event["id"])
        if event["agent"] not in robot_names:
            raise ScenarioError(f"{event['id']}: unknown fault agent")
        kind = event["kind"]
        agent = event["agent"]
        if kind == "member_join":
            if agent not in available_reserves:
                raise ScenarioError(
                    f"{event['id']}: member_join requires an unused preloaded reserve")
            available_reserves.remove(agent)
            active_agents.add(agent)
        elif kind == "member_exit":
            replacement = event.get("replacement_agent")
            if agent not in active_agents:
                raise ScenarioError(f"{event['id']}: member_exit requires an active agent")
            if replacement not in active_agents or replacement == agent:
                raise ScenarioError(
                    f"{event['id']}: replacement_agent must be a different active agent")
            active_agents.remove(agent)
        else:
            if agent not in active_agents:
                raise ScenarioError(
                    f"{event['id']}: capability_change requires an active agent")
            if event["capability_version"] <= capability_versions[agent]:
                raise ScenarioError(
                    f"{event['id']}: capability_version must strictly increase")
            capability_versions[agent] = event["capability_version"]
    target_ids = set(entities) | {target["id"] for target in data["composition"]["targets"]}
    for node in nodes:
        validate_node_timing(node)
        vertical = node.get("vertical")
        if vertical is not None:
            if node["skill_key"] != "navigate":
                raise ScenarioError(f"{node['id']}: vertical metadata requires navigate skill")
            if vertical["support_probe"] not in {"required", "validated"}:
                raise ScenarioError(f"{node['id']}: unsupported vertical support probe state")
        if node["target_ref"] not in target_ids:
            raise ScenarioError(f"{node['id']}: unknown target_ref")
        if node.get("scan_height_m") is not None:
            target = next((target for target in data["composition"]["targets"]
                           if target["id"] == node["target_ref"]), None)
            target_z = (target["position"][2] if target else
                        entities[node["target_ref"]]["spawn_pose"]["position"][2])
            if node["scan_height_m"] <= target_z:
                raise ScenarioError(f"{node['id']}: scan_height_m must be above the target")
        observation_ref = node.get("observation_ref")
        if observation_ref is not None and observation_ref not in target_ids:
            raise ScenarioError(f"{node['id']}: unknown observation_ref")
        recovery_for = node.get("recovery_for")
        if recovery_for is not None and recovery_for not in graph:
            raise ScenarioError(f"{node['id']}: unknown recovery_for")
        if recovery_for is not None and recovery_for not in node["prerequisites"]:
            raise ScenarioError(f"{node['id']}: recovery_for must be a prerequisite")
        if node["preferred_agent"] is not None and node["preferred_agent"] not in robot_names:
            raise ScenarioError(f"{node['id']}: unknown preferred_agent")
        if len(node["prerequisites"]) != len(set(node["prerequisites"])) or set(node["prerequisites"]) - graph.keys():
            raise ScenarioError(f"{node['id']}: duplicate or unknown prerequisite")
        skill = SKILLS[node["skill_key"]]
        if skill["object_required"]:
            entity = entities.get(node["object_ref"])
            if entity is None or entity["physics"]["mode"] != "dynamic":
                raise ScenarioError(f"{node['id']}: skill requires a dynamic object_ref")
        elif node["object_ref"] is not None:
            raise ScenarioError(f"{node['id']}: object_ref must be null for this skill")
        eligible = [robot for robot in data["robots"] if set(skill["capabilities"]) <= robot_capabilities(robot)]
        if not eligible:
            raise ScenarioError(f"{node['id']}: no robot has the required equipment")
        if node["preferred_agent"] and node["preferred_agent"] not in {robot["id"] for robot in eligible}:
            raise ScenarioError(f"{node['id']}: preferred agent lacks required equipment")
    pending = dict(graph)
    while pending:
        ready = {key for key, deps in pending.items() if not set(deps) & pending.keys()}
        if not ready:
            raise ScenarioError("phase_graph contains a cycle")
        pending = {key: deps for key, deps in pending.items() if key not in ready}
    return data
