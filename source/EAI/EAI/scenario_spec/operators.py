"""Task specs, placement operators, and paired instance families (V3 layer).

A task spec is semantic: robots by zone, entities by asset and zone, targets
by zone, and a phase graph over refs. The sampler resolves every item to a
concrete free anchor with a seeded RNG and compiles a frozen V2 episode JSON,
which then passes the full V2 gate stack. A family materializes the same base
spec under per-condition deltas, so paired conditions differ only in the
declared axes (the Suite operator's data face).

LLM or recipe proposes the spec; the sampler owns geometry. Neither sees USD
paths, prim paths, or controller files.
"""

import copy
import random

from EAI.hmrs_env.env_diy import catalog
from EAI_assets.scenario_assets import ASSETS
from .schema import ROBOT_PROFILES, SKILLS
from .templates import zone_anchors
from .validation import ScenarioError, validate_node_timing

_MASS_LEVELS = {"low": 8.0, "high": 30.0}
_FRICTION_DEFAULTS = {"static_friction": 0.8, "dynamic_friction": 0.6, "restitution": 0.05}


def validate_task_spec(spec, template) -> None:
    if not isinstance(spec, dict):
        raise ScenarioError("task spec must be an object")
    for field in ("robots", "targets", "phase_nodes"):
        if not isinstance(spec.get(field), list) or not spec[field]:
            raise ScenarioError(f"task spec field '{field}' must be a non-empty list")
    if not isinstance(spec.get("entities"), list):
        raise ScenarioError("task spec field 'entities' must be a list")

    refs = set()

    def claim(ref, kind):
        if not isinstance(ref, str) or not ref or ref in refs:
            raise ScenarioError(f"duplicate or empty {kind} ref: {ref!r}")
        refs.add(ref)

    def check_zones(item, kind):
        zones = item.get("zones")
        if not isinstance(zones, list) or not zones:
            raise ScenarioError(f"{kind}: 'zones' must be a non-empty list")
        for zone_id in zones:
            if not zone_anchors(template, zone_id):
                raise ScenarioError(f"{kind}: zone '{zone_id}' has no free anchors")

    robots = spec["robots"]
    for robot in robots:
        claim(robot.get("ref"), "robot")
        key = robot.get("type")
        if key not in ROBOT_PROFILES:
            raise ScenarioError(f"unknown robot type '{key}'")
        check_zones(robot, robot["ref"])
        attachments = robot.get("attachments") or []
        if not isinstance(attachments, list):
            raise ScenarioError(f"{robot['ref']}: attachments must be a list")
        try:
            catalog.validate_attachment_types(key, [str(item) for item in attachments])
        except ValueError as exc:
            raise ScenarioError(f"{robot['ref']}: {exc}") from exc

    for entity in spec["entities"]:
        claim(entity.get("ref"), "entity")
        asset = ASSETS.get(entity.get("asset"))
        if asset is None:
            raise ScenarioError(f"{entity['ref']}: unknown asset '{entity.get('asset')}'")
        check_zones(entity, entity["ref"])
        size = entity.get("size_m")
        if size is None and asset.size_m is not None:
            entity["size_m"] = list(asset.size_m)  # registry-known geometry, no hallucination
            size = entity["size_m"]
        if not isinstance(size, list) or len(size) != 3 or not all(0.02 <= v <= 4 for v in size):
            raise ScenarioError(f"{entity['ref']}: size_m must be three values in [0.02, 4]")
        mode = entity.get("mode", "dynamic" if "dynamic" in asset.modes else asset.modes[0])
        if mode not in asset.modes:
            raise ScenarioError(f"{entity['ref']}: asset does not support mode '{mode}'")
        mass = entity.get("mass_kg")
        if mass is None and mode in {"dynamic", "kinematic"} and asset.suggested_mass_kg is not None:
            entity["mass_kg"] = asset.suggested_mass_kg  # audited registry mass, no invention
            mass = entity["mass_kg"]
        if (mass is None) != (mode not in {"dynamic", "kinematic"}):
            raise ScenarioError(f"{entity['ref']}: mass_kg required exactly for dynamic/kinematic bodies")
        if mass is not None and not 0.01 <= mass <= 200:
            raise ScenarioError(f"{entity['ref']}: mass_kg out of range")

    for target in spec["targets"]:
        claim(target.get("ref"), "target")
        check_zones(target, target["ref"])
        radius = target.get("radius_m", 0.5)
        if not 0.05 <= radius <= 2:
            raise ScenarioError(f"{target['ref']}: radius_m out of range")
        if radius > 0.9:
            raise ScenarioError(
                f"{target['ref']}: radius_m {radius} exceeds the 0.9 m anchor clearance; "
                "use at most 0.9")

    node_ids = []
    for node in spec["phase_nodes"]:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ScenarioError("phase node requires an id")
        if node_id in node_ids:
            raise ScenarioError(f"duplicate phase node id '{node_id}'")
        node_ids.append(node_id)
        timeout = node.get("timeout_s", 60.0)
        if not 0.1 <= float(timeout) <= 600:
            raise ScenarioError(f"{node_id}: timeout_s out of range [0.1, 600]")
        validate_node_timing(node, skill_field="skill")
    known = set(node_ids)
    for node in spec["phase_nodes"]:
        for prereq in node.get("prerequisites") or []:
            if prereq not in known:
                raise ScenarioError(f"{node['id']}: unknown prerequisite '{prereq}'")
            if prereq == node["id"]:
                raise ScenarioError(f"{node['id']}: prerequisite references itself")
    pending = {node["id"]: set(node.get("prerequisites") or []) for node in spec["phase_nodes"]}
    while pending:
        ready = {key for key, deps in pending.items() if not deps & pending.keys()}
        if not ready:
            raise ScenarioError("phase_nodes contain a cycle")
        pending = {key: deps for key, deps in pending.items() if key not in ready}

    robot_by_ref = {robot["ref"]: robot for robot in robots}
    # joint_transport: a fixed team of agents carries the object together.
    for node in spec["phase_nodes"]:
        if node.get("skill") != "joint_transport":
            continue
        team = node.get("agents")
        if not isinstance(team, list) or len(team) != SKILLS["joint_transport"]["team_size"]:
            raise ScenarioError(
                f"{node['id']}: joint_transport requires exactly "
                f"{SKILLS['joint_transport']['team_size']} agents in 'agents'")
        for ref in team:
            if ref not in robot_by_ref:
                raise ScenarioError(f"{node['id']}: unknown team member '{ref}'")
        # manipulation not required per member; the joint is the mechanism
        node["agent_ref"] = team[0]  # lead agent for nav feasibility

    entity_modes = {entity["ref"]: entity.get(
        "mode", "dynamic" if "dynamic" in ASSETS[entity["asset"]].modes else ASSETS[entity["asset"]].modes[0])
        for entity in spec["entities"]}
    for node in spec["phase_nodes"]:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ScenarioError("phase node requires an id")
        skill = SKILLS.get(node.get("skill"))
        if skill is None:
            raise ScenarioError(f"{node_id}: unknown skill '{node.get('skill')}'")
        agent_ref = node.get("agent_ref")
        robot = robot_by_ref.get(agent_ref)
        if robot is None:
            raise ScenarioError(f"{node_id}: unknown agent_ref '{agent_ref}'")
        needed = set(skill["capabilities"])
        from .validation import robot_capabilities
        has = robot_capabilities({"type": robot["type"], "attachments": robot.get("attachments") or []})
        if not needed <= has:
            raise ScenarioError(f"{node_id}: robot '{agent_ref}' lacks required equipment")
        for ref_field in ("target_ref", "object_ref"):
            value = node.get(ref_field)
            if value is not None and value not in refs:
                raise ScenarioError(f"{node_id}: unknown {ref_field} '{value}'")
        if skill["object_required"]:
            if node.get("object_ref") is None or entity_modes.get(node["object_ref"]) != "dynamic":
                raise ScenarioError(f"{node_id}: skill requires a dynamic object_ref")
        elif node.get("object_ref") is not None:
            raise ScenarioError(f"{node_id}: object_ref must be null for this skill")


def _footprint(item, kind):
    if kind == "robot":
        radius = ROBOT_PROFILES[item["type"]]["radius_m"]
        if any(a in {"ur5", "z1"} for a in item.get("attachments") or []):
            radius = max(radius, 0.9)
        return radius, radius
    if kind == "target":
        radius = item.get("radius_m", 0.5)
        return radius, radius
    w, d, _ = item["size_m"]
    return w / 2 + 0.05, d / 2 + 0.05


def _overlaps(ax, ay, ahx, ahy, bx, by, bhx, bhy):
    return abs(ax - bx) < ahx + bhx and abs(ay - by) < ahy + bhy


def _assign_anchors(spec, template, rng):
    placed = []
    assignment = {}

    def pick(item, kind):
        hx, hy = _footprint(item, kind)
        candidates = [anchor for zone_id in item["zones"] for anchor in zone_anchors(template, zone_id)]
        rng.shuffle(candidates)
        for anchor in candidates:
            if any(_overlaps(anchor.x, anchor.y, hx, hy, px, py, phx, phy) for px, py, phx, phy in placed):
                continue
            placed.append((anchor.x, anchor.y, hx, hy))
            assignment[item["ref"]] = anchor
            return
        raise ScenarioError(f"{item['ref']}: no free anchor in zones {item['zones']}")

    for robot in spec["robots"]:
        pick(robot, "robot")
    for entity in spec["entities"]:
        pick(entity, "entity")
    for target in spec["targets"]:
        pick(target, "target")
    return assignment


def _default_task_name(spec, seed):
    return f"{str(spec.get('skeleton', 'task')).lower().replace('-', '_')}_{seed}"


def _zone_level(template, zone_id):
    for zone in template.zones:
        if zone.id == zone_id:
            return zone.level
    return 0


def _level_floor_z(template, level):
    if template.levels and 0 <= level < len(template.levels):
        return template.levels[level].floor_z
    return 0.0


def _compile(spec, template, assignment, *, seed, task_name):
    counts: dict[str, int] = {}
    ref_to_id = {}
    robots = []
    for robot in spec["robots"]:
        key = robot["type"]
        counts[key] = counts.get(key, 0) + 1
        robot_id = f"{key}_{counts[key]}"
        ref_to_id[robot["ref"]] = robot_id
        attachments = []
        for attachment_type in robot.get("attachments") or []:
            expected = {"ur5": "UR5_IK_CFG", "z1": "Z1_IK_CFG"}.get(attachment_type)
            attachments.append({
                "type": attachment_type,
                "controller": None if expected is None else {"mode": "default", "cfg": expected},
            })
        anchor = assignment[robot["ref"]]
        level = _zone_level(template, robot["zones"][0])
        spawn_z = _level_floor_z(template, level) + ROBOT_PROFILES[key]["spawn_z_m"]
        robots.append({
            "id": robot_id,
            "type": key,
            "controller": {"mode": "default", "cfg": catalog.default_controller_cfg(key)},
            "attachments": attachments,
            "spawn_pose": {
                "position": [anchor.x, anchor.y, spawn_z],
                "rotation": [1.0, 0.0, 0.0, 0.0],
            },
        })

    entities = []
    for entity in spec["entities"]:
        asset = ASSETS[entity["asset"]]
        mode = entity.get("mode", "dynamic" if "dynamic" in asset.modes else asset.modes[0])
        anchor = assignment[entity["ref"]]
        level = _zone_level(template, entity["zones"][0])
        entity_z = _level_floor_z(template, level) + entity["size_m"][2] / 2
        entities.append({
            "id": entity["ref"],
            "asset_key": entity["asset"],
            "size_m": list(entity["size_m"]),
            "spawn_pose": {
                "position": [anchor.x, anchor.y, entity_z],
                "rotation": [1.0, 0.0, 0.0, 0.0],
            },
            "physics": {"mode": mode, "mass_kg": entity.get("mass_kg"), **_FRICTION_DEFAULTS},
        })

    targets = [{
        "id": target["ref"],
        "position": [assignment[target["ref"]].x, assignment[target["ref"]].y,
                     _level_floor_z(template, _zone_level(template, target["zones"][0]))],
        "radius_m": target.get("radius_m", 0.5),
    } for target in spec["targets"]]

    nodes = [{
        "id": node["id"],
        "skill_key": node["skill"],
        "preferred_agent": ref_to_id[node["agent_ref"]],
        "agents": [ref_to_id[ref] for ref in node.get("agents") or []] or None,
        "target_ref": node["target_ref"],
        "object_ref": node.get("object_ref"),
        "prerequisites": list(node.get("prerequisites") or []),
        "timeout_s": float(node.get("timeout_s", 60.0)),
        **({"duration_s": node["duration_s"]} if node.get("duration_s") is not None else {}),
        **({"scan_height_m": node["scan_height_m"]} if node.get("scan_height_m") is not None else {}),
        **({"scan_yaw_rad": node["scan_yaw_rad"]} if node.get("scan_yaw_rad") is not None else {}),
        **({"wait_yaw_rad": node["wait_yaw_rad"]} if node.get("wait_yaw_rad") is not None else {}),
        **({"resources": list(node["resources"])} if node.get("resources") else {}),
    } for node in spec["phase_nodes"]]

    return {
        "version": 2,
        "task_name": task_name or _default_task_name(spec, seed),
        "scene_key": template.scene_key,
        "seed": seed,
        "robots": robots,
        "composition": {"entities": entities, "targets": targets},
        "phase_graph": {"nodes": nodes},
    }


def run_gates(data, occupancy, payload_policy=None, template=None):
    from .evaluation import validate_evaluation
    from .geometry import validate_connectivity, validate_geometry
    from .oracle import validate_oracle, validate_level_routing
    from .validation import validate_scenario

    floors = tuple(level.floor_z for level in template.levels) if template and template.levels else ()
    data = validate_scenario(data, known_floors=floors)
    validate_geometry(data, occupancy)
    validate_connectivity(data, occupancy)
    validate_oracle(data, occupancy, payload_policy=payload_policy, template=template)
    validate_level_routing(data, template)
    validate_evaluation(data)
    return data


def instantiate(spec, template, occupancy, *, seed, task_name=None, attempts=24,
                payload_policy=None):
    """Resolve a task spec to one fully validated frozen V2 episode."""
    validate_task_spec(spec, template)
    rng = random.Random(seed)
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            assignment = _assign_anchors(spec, template, rng)
            data = _compile(spec, template, assignment, seed=seed, task_name=task_name)
            return run_gates(data, occupancy, payload_policy=payload_policy, template=template)
        except ScenarioError as exc:
            last_error = exc
    raise ScenarioError(f"placement exhausted after {attempts} attempts: {last_error}")


def apply_delta(data, delta):
    """Apply one condition delta to a materialized instance.

    Supported ops: set_mass. Add/remove/swap stay out until a recipe needs
    them, because they break the pairing guarantee unless they, too, are
    declared axes.
    """
    op = delta.get("op")
    if op != "set_mass":
        raise ScenarioError(f"unsupported delta op '{op}'")
    ref = delta.get("ref")
    mass = delta.get("mass_kg")
    if not isinstance(mass, (int, float)) or not 0.01 <= mass <= 200:
        raise ScenarioError("set_mass requires mass_kg in [0.01, 200]")
    for entity in data["composition"]["entities"]:
        if entity["id"] == ref:
            if entity["physics"]["mode"] not in {"dynamic", "kinematic"}:
                raise ScenarioError(f"{ref}: cannot set mass on a {entity['physics']['mode']} body")
            entity["physics"]["mass_kg"] = float(mass)
            return data
    raise ScenarioError(f"set_mass: unknown entity '{ref}'")


def _sanitize_identifier(value):
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(value))
    return cleaned if cleaned and (cleaned[0].isalpha() or cleaned[0] == "_") else "family_" + cleaned


def expand_family(family_spec, template, occupancy, payload_policy=None):
    """Materialize one base spec under per-condition deltas.

    Returns (family_record, {condition_id: validated_v2_instance}). Conditions
    share anchors and poses exactly; only declared deltas differ.
    """
    family_spec = {**family_spec, "family_id": _sanitize_identifier(family_spec["family_id"])}
    base_spec = family_spec["base_spec"]
    seed = int(family_spec["seed"])
    conditions = family_spec.get("conditions") or [
        {"id": "base", "level": "base", "delta": []}
    ]
    seen = set()
    for condition in conditions:
        if condition["id"] in seen:
            raise ScenarioError(f"duplicate condition id '{condition['id']}'")
        seen.add(condition["id"])

    instances = {}
    base = instantiate(base_spec, template, occupancy, seed=seed,
                       task_name=f"{family_spec['family_id']}_base",
                       payload_policy=payload_policy)
    for condition in conditions:
        data = copy.deepcopy(base)
        for delta in condition["delta"]:
            data = apply_delta(data, delta)
        data["task_name"] = f"{family_spec['family_id']}_{condition['id']}"
        instances[condition["id"]] = run_gates(
            data, occupancy, payload_policy=payload_policy, template=template)

    record = {
        "version": 3,
        "family_id": family_spec["family_id"],
        "template": template.key,
        "map_sha256": template.map_sha256,
        "seed": seed,
        "skeleton": base_spec.get("skeleton"),
        "scientific_target": family_spec.get("scientific_target"),
        "payload_policy": payload_policy,
        "instruction": base_spec.get("instruction"),
        "base_spec": base_spec,
        "conditions": [
            {"id": condition["id"], "level": condition.get("level"), "delta": condition["delta"]}
            for condition in conditions
        ],
        "pairing": "conditions share anchors, poses, robots, and phase graph; only declared deltas differ",
    }
    return record, instances


def _trn01(mass_level=None, arm="z1"):
    mass = _MASS_LEVELS[mass_level] if mass_level else _MASS_LEVELS["low"]
    if arm == "ur5":
        carrier = {"ref": "carrier", "type": "m20", "zones": ["storage_west"], "attachments": ["ur5"]}
    else:
        carrier = {"ref": "carrier", "type": "carter", "zones": ["storage_west"], "attachments": ["z1"]}
    return {
        "skeleton": "TRN-01",
        # This is intentionally a single-carrier payload baseline.  The old
        # unused "relay" robot made the episode look collaborative even though
        # it owned no phase and could not affect the result.
        "robots": [carrier],
        "entities": [
            {"ref": "cargo", "asset": "primitive_box", "zones": ["storage_west"],
             "size_m": [0.5, 0.4, 0.4], "mass_kg": mass},
        ],
        "targets": [
            {"ref": "delivery", "zones": ["assembly_east"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "move_to_cargo", "skill": "navigate", "agent_ref": "carrier",
             "target_ref": "cargo", "object_ref": None, "prerequisites": []},
            {"id": "transport_to_delivery", "skill": "transport_object", "agent_ref": "carrier",
             "target_ref": "delivery", "object_ref": "cargo", "prerequisites": ["move_to_cargo"]},
        ],
    }


def _sar01(mass_level=None, arm="z1"):
    if arm == "ur5":
        responder = {"ref": "responder", "type": "m20", "zones": ["storage_west"], "attachments": ["ur5"]}
    else:
        responder = {"ref": "responder", "type": "carter", "zones": ["storage_west"], "attachments": ["z1"]}
    return {
        "skeleton": "SAR-01",
        "robots": [
            {"ref": "perception", "type": "go2", "zones": ["yard_south"], "attachments": []},
            responder,
        ],
        "entities": [
            {"ref": "beacon", "asset": "primitive_box", "zones": ["dock_north"],
             "size_m": [0.3, 0.3, 0.3], "mass_kg": 2.0},
            {"ref": "occluder", "asset": "safety_beam", "zones": ["midband", "yard_south"],
             "size_m": [0.3, 2.0, 0.45], "mode": "static", "mass_kg": None},
        ],
        "targets": [
            {"ref": "extraction", "zones": ["yard_south"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "locate_beacon", "skill": "navigate", "agent_ref": "perception",
             "target_ref": "beacon", "object_ref": None, "prerequisites": []},
            {"id": "extract_beacon", "skill": "transport_object", "agent_ref": "responder",
             "target_ref": "extraction", "object_ref": "beacon", "prerequisites": ["locate_beacon"]},
        ],
    }


def _cov03(mass_level=None, arm="z1"):
    robots = [
        {"ref": "patrol_west", "type": "carter", "zones": ["storage_west"], "attachments": []},
        {"ref": "patrol_south", "type": "go2", "zones": ["yard_south"], "attachments": []},
        {"ref": "patrol_east", "type": "m20", "zones": ["assembly_east"], "attachments": []},
    ]
    targets = [
        {"ref": f"waypoint_{zone}", "zones": [zone], "radius_m": 0.5}
        for zone in ("storage_west", "yard_south", "assembly_east")
    ]
    return {
        "skeleton": "COV-03",
        "robots": robots,
        "entities": [],
        "targets": targets,
        "phase_nodes": [
            {"id": f"cover_{target['ref']}", "skill": "navigate", "agent_ref": robot["ref"],
             "target_ref": target["ref"], "object_ref": None, "prerequisites": []}
            for robot, target in zip(robots, targets)
        ],
    }


def _trn02(mass_level=None, arm="z1"):
    """Eccentric long cargo: a beam too long for one grasp point, escorted
    through the midband corridor by a second robot."""
    mass = {"low": 12.0, "high": 24.0}.get(mass_level, 12.0)
    if arm == "ur5":
        carrier = {"ref": "carrier", "type": "m20", "zones": ["storage_west"], "attachments": ["ur5"]}
    else:
        carrier = {"ref": "carrier", "type": "carter", "zones": ["storage_west"], "attachments": ["z1"]}
    return {
        "skeleton": "TRN-02",
        "robots": [
            carrier,
            {"ref": "escort", "type": "go2", "zones": ["midband"], "attachments": []},
        ],
        "entities": [
            {"ref": "beam", "asset": "primitive_box", "zones": ["storage_west"],
             "size_m": [1.6, 0.35, 0.35], "mass_kg": mass},
        ],
        "targets": [
            {"ref": "delivery", "zones": ["assembly_east"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "escort_to_corridor", "skill": "navigate", "agent_ref": "escort",
             "target_ref": "delivery", "object_ref": None, "prerequisites": []},
            {"id": "carrier_to_beam", "skill": "navigate", "agent_ref": "carrier",
             "target_ref": "beam", "object_ref": None, "prerequisites": []},
            {"id": "transport_beam", "skill": "transport_object", "agent_ref": "carrier",
             "target_ref": "delivery", "object_ref": "beam",
             "prerequisites": ["carrier_to_beam", "escort_to_corridor"]},
        ],
    }


def _trn03(mass_level=None, arm="z1"):
    """Cross-zone relay: leg A carries the cargo to a handoff zone, leg B
    carries it on to the delivery zone.

    The receiver may move to a staging point early, but it must not occupy the
    shared handoff point.  The sender clears the handoff before the receiver
    approaches the cargo.  Keeping those states explicit makes the generated
    DAG executable as a relay instead of relying on two robots to converge on
    one pose at the same time.
    """
    if arm == "ur5":
        first = {"ref": "leg_a", "type": "scout", "zones": ["storage_west"], "attachments": ["ur5"]}
        second = {"ref": "leg_b", "type": "m20", "zones": ["assembly_east"], "attachments": ["ur5"]}
    else:
        first = {"ref": "leg_a", "type": "carter", "zones": ["storage_west"], "attachments": ["z1"]}
        second = {"ref": "leg_b", "type": "m20", "zones": ["assembly_east"], "attachments": ["z1"]}
    return {
        "skeleton": "TRN-03",
        "robots": [first, second],
        "entities": [
            {"ref": "cargo", "asset": "primitive_box", "zones": ["storage_west"],
             "size_m": [0.5, 0.4, 0.4], "mass_kg": 10.0},
        ],
        "targets": [
            {"ref": "handoff", "zones": ["yard_south"], "radius_m": 0.5},
            {"ref": "receiver_staging", "zones": ["assembly_east"], "radius_m": 0.5},
            {"ref": "sender_clear", "zones": ["storage_west"], "radius_m": 0.5},
            {"ref": "delivery", "zones": ["dock_north"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "leg_a_to_cargo", "skill": "navigate", "agent_ref": "leg_a",
             "target_ref": "cargo", "object_ref": None, "prerequisites": []},
            {"id": "leg_a_carry", "skill": "transport_object", "agent_ref": "leg_a",
             "target_ref": "handoff", "object_ref": "cargo", "prerequisites": ["leg_a_to_cargo"]},
            {"id": "leg_a_clear_handoff", "skill": "navigate", "agent_ref": "leg_a",
             "target_ref": "sender_clear", "object_ref": None, "prerequisites": ["leg_a_carry"]},
            {"id": "leg_b_to_staging", "skill": "navigate", "agent_ref": "leg_b",
             "target_ref": "receiver_staging", "object_ref": None, "prerequisites": []},
            {"id": "leg_b_carry", "skill": "transport_object", "agent_ref": "leg_b",
             "target_ref": "delivery", "object_ref": "cargo",
             "prerequisites": ["leg_a_clear_handoff", "leg_b_to_staging"]},
        ],
    }




def _sar02(mass_level=None, arm="z1"):
    """Complementary observation: an aerial overview sweep plus a ground
    close-in search locate the beacon from different vantage points (H3)."""
    return {
        "skeleton": "SAR-02",
        "robots": [
            {"ref": "overwatch", "type": "cf2x", "zones": ["midband"], "attachments": []},
            {"ref": "ground_search", "type": "go2", "zones": ["yard_south"], "attachments": []},
        ],
        "entities": [
            {"ref": "beacon", "asset": "primitive_box", "zones": ["dock_north"],
             "size_m": [0.3, 0.3, 0.3], "mass_kg": 1.0},
        ],
        "targets": [
            {"ref": "sweep_east", "zones": ["assembly_east"], "radius_m": 0.5},
            {"ref": "sweep_north", "zones": ["dock_north"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "aerial_sweep", "skill": "navigate", "agent_ref": "overwatch",
             "target_ref": "sweep_east", "object_ref": None, "prerequisites": []},
            {"id": "ground_search", "skill": "navigate", "agent_ref": "ground_search",
             "target_ref": "beacon", "object_ref": None, "prerequisites": ["aerial_sweep"]},
        ],
    }


def _sar03(mass_level=None, arm="z1"):
    """Multi-target triage: three casualties with different urgency routed to
    two different collection zones by two carriers."""
    if arm == "ur5":
        medic_a = {"ref": "medic_a", "type": "scout", "zones": ["dock_north"], "attachments": ["ur5"]}
        medic_b = {"ref": "medic_b", "type": "m20", "zones": ["storage_west"], "attachments": ["ur5"]}
    else:
        medic_a = {"ref": "medic_a", "type": "carter", "zones": ["dock_north"], "attachments": ["z1"]}
        medic_b = {"ref": "medic_b", "type": "m20", "zones": ["storage_west"], "attachments": ["z1"]}
    return {
        "skeleton": "SAR-03",
        "robots": [medic_a, medic_b],
        "entities": [
            {"ref": f"casualty_{i}", "asset": "primitive_box", "zones": ["dock_north"],
             "size_m": [0.3, 0.3, 0.3], "mass_kg": mass}
            for i, mass in enumerate((1.0, 2.0, 4.0), start=1)
        ],
        "targets": [
            {"ref": "urgent_care", "zones": ["yard_south"], "radius_m": 0.5},
            {"ref": "triage_hold", "zones": ["storage_west"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "triage_scan", "skill": "navigate", "agent_ref": "medic_a",
             "target_ref": "casualty_1", "object_ref": None, "prerequisites": []},
            {"id": "urgent_extract", "skill": "transport_object", "agent_ref": "medic_a",
             "target_ref": "urgent_care", "object_ref": "casualty_1",
             "prerequisites": ["triage_scan"]},
            {"id": "hold_extract_a", "skill": "transport_object", "agent_ref": "medic_b",
             "target_ref": "triage_hold", "object_ref": "casualty_2", "prerequisites": []},
            {"id": "hold_extract_b", "skill": "transport_object", "agent_ref": "medic_b",
             "target_ref": "triage_hold", "object_ref": "casualty_3",
             "prerequisites": ["hold_extract_a"]},
        ],
    }


def _cov01(mass_level=None, arm="z1"):
    """Heterogeneous area coverage: a wheeled base takes the open corridors
    while a quadruped covers the cluttered south yard (H1 split)."""
    return {
        "skeleton": "COV-01",
        "robots": [
            {"ref": "corridor_bot", "type": "carter", "zones": ["midband"], "attachments": []},
            {"ref": "yard_bot", "type": "go2", "zones": ["yard_south"], "attachments": []},
        ],
        "entities": [
            {"ref": "yard_blocker", "asset": "safety_beam", "zones": ["yard_south"],
             "size_m": [0.3, 2.0, 0.45], "mode": "static", "mass_kg": None},
        ],
        "targets": [
            {"ref": "corridor_wp", "zones": ["assembly_east"], "radius_m": 0.5},
            {"ref": "yard_wp_a", "zones": ["yard_south"], "radius_m": 0.5},
            {"ref": "yard_wp_b", "zones": ["storage_west"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "cover_corridor", "skill": "navigate", "agent_ref": "corridor_bot",
             "target_ref": "corridor_wp", "object_ref": None, "prerequisites": []},
            {"id": "cover_yard_a", "skill": "navigate", "agent_ref": "yard_bot",
             "target_ref": "yard_wp_a", "object_ref": None, "prerequisites": []},
            {"id": "cover_yard_b", "skill": "navigate", "agent_ref": "yard_bot",
             "target_ref": "yard_wp_b", "object_ref": None, "prerequisites": ["cover_yard_a"]},
        ],
    }


def _cov02(mass_level=None, arm="z1"):
    """Multimodal patrol: an aerial pass over the long corridor followed by a
    ground close patrol of the storage racks."""
    return {
        "skeleton": "COV-02",
        "robots": [
            {"ref": "air_patrol", "type": "cf2x", "zones": ["midband"], "attachments": []},
            {"ref": "ground_patrol", "type": "scout", "zones": ["storage_west"], "attachments": []},
        ],
        "entities": [],
        "targets": [
            {"ref": "sky_wp_east", "zones": ["assembly_east"], "radius_m": 0.5},
            {"ref": "sky_wp_west", "zones": ["storage_west"], "radius_m": 0.5},
            {"ref": "rack_wp", "zones": ["dock_north"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "sky_leg_east", "skill": "navigate", "agent_ref": "air_patrol",
             "target_ref": "sky_wp_east", "object_ref": None, "prerequisites": []},
            {"id": "sky_leg_west", "skill": "navigate", "agent_ref": "air_patrol",
             "target_ref": "sky_wp_west", "object_ref": None, "prerequisites": ["sky_leg_east"]},
            {"id": "rack_leg", "skill": "navigate", "agent_ref": "ground_patrol",
             "target_ref": "rack_wp", "object_ref": None, "prerequisites": []},
        ],
    }




def _trnmez(mass_level=None, arm="z1"):
    """Flagship 3-D task: cargo waits on the mezzanine platform; only a
    legged robot can use the stair link, so the wheeled escort is forced
    into the ground role (H1 made decisive by the level geometry)."""
    if arm == "ur5":
        carrier = {"ref": "carrier", "type": "m20", "zones": ["mezzanine_south"], "attachments": ["ur5"]}
    else:
        carrier = {"ref": "carrier", "type": "m20", "zones": ["mezzanine_south"], "attachments": ["z1"]}
    return {
        "skeleton": "TRN-MEZ",
        "robots": [
            carrier,
            {"ref": "escort", "type": "carter", "zones": ["yard_south"], "attachments": []},
        ],
        "entities": [
            {"ref": "cargo", "asset": "primitive_box", "zones": ["mezzanine_south"],
             "size_m": [0.4, 0.4, 0.4], "mass_kg": 6.0},
        ],
        "targets": [
            {"ref": "dropoff", "zones": ["assembly_east"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "escort_to_staging", "skill": "navigate", "agent_ref": "escort",
             "target_ref": "dropoff", "object_ref": None, "prerequisites": []},
            {"id": "carrier_to_cargo", "skill": "navigate", "agent_ref": "carrier",
             "target_ref": "cargo", "object_ref": None, "prerequisites": []},
            {"id": "descend_and_deliver", "skill": "transport_object", "agent_ref": "carrier",
             "target_ref": "dropoff", "object_ref": "cargo",
             "prerequisites": ["carrier_to_cargo", "escort_to_staging"]},
        ],
    }




def _asm01(mass_level=None, arm="z1"):
    """Hold-and-fasten: both parts converge on one station zone; the second
    placement is sequenced after the first (the fastener waits on the hold)."""
    if arm == "ur5":
        holder = {"ref": "holder", "type": "scout", "zones": ["storage_west"], "attachments": ["ur5"]}
        fastener = {"ref": "fastener", "type": "m20", "zones": ["assembly_east"], "attachments": ["ur5"]}
    else:
        holder = {"ref": "holder", "type": "carter", "zones": ["storage_west"], "attachments": ["z1"]}
        fastener = {"ref": "fastener", "type": "m20", "zones": ["assembly_east"], "attachments": ["z1"]}
    return {
        "skeleton": "ASM-01",
        "robots": [holder, fastener],
        "entities": [
            {"ref": "beam_part", "asset": "primitive_box", "zones": ["midband"],
             "size_m": [0.6, 0.3, 0.3], "mass_kg": 9.0},
            {"ref": "fastener_part", "asset": "primitive_box", "zones": ["dock_north"],
             "size_m": [0.25, 0.25, 0.25], "mass_kg": 1.5},
        ],
        "targets": [{"ref": "station", "zones": ["assembly_east"], "radius_m": 0.5}],
        "phase_nodes": [
            {"id": "hold_leg", "skill": "transport_object", "agent_ref": "holder",
             "target_ref": "station", "object_ref": "beam_part", "prerequisites": []},
            {"id": "fasten_leg", "skill": "transport_object", "agent_ref": "fastener",
             "target_ref": "station", "object_ref": "fastener_part", "prerequisites": ["hold_leg"]},
        ],
    }


def _asm02(mass_level=None, arm="z1"):
    """Capability-exclusive stations: the heavy casting only moves on the
    wheeled arm, the delicate sensor only on the quadruped arm (payload policy
    makes the split forced when capacities differ)."""
    if arm == "ur5":
        heavy_mover = {"ref": "heavy_mover", "type": "scout", "zones": ["dock_north"], "attachments": ["ur5"]}
        delicate_mover = {"ref": "delicate_mover", "type": "m20", "zones": ["storage_west"], "attachments": ["ur5"]}
    else:
        heavy_mover = {"ref": "heavy_mover", "type": "carter", "zones": ["dock_north"], "attachments": ["z1"]}
        delicate_mover = {"ref": "delicate_mover", "type": "m20", "zones": ["storage_west"], "attachments": ["z1"]}
    return {
        "skeleton": "ASM-02",
        "robots": [heavy_mover, delicate_mover],
        "entities": [
            {"ref": "casting", "asset": "primitive_box", "zones": ["midband"],
             "size_m": [0.7, 0.5, 0.5], "mass_kg": 28.0},
            {"ref": "sensor_unit", "asset": "primitive_box", "zones": ["yard_south"],
             "size_m": [0.2, 0.2, 0.2], "mass_kg": 1.0},
        ],
        "targets": [{"ref": "bench", "zones": ["assembly_east"], "radius_m": 0.5}],
        "phase_nodes": [
            {"id": "casting_leg", "skill": "transport_object", "agent_ref": "heavy_mover",
             "target_ref": "bench", "object_ref": "casting", "prerequisites": []},
            {"id": "sensor_leg", "skill": "transport_object", "agent_ref": "delicate_mover",
             "target_ref": "bench", "object_ref": "sensor_unit", "prerequisites": []},
        ],
    }


def _asm03(mass_level=None, arm="z1"):
    """Timing interlock: three parts, each placement gated on the previous;
    any out-of-order arrival breaks the chain."""
    if arm == "ur5":
        a1 = {"ref": "fitter_a", "type": "scout", "zones": ["storage_west"], "attachments": ["ur5"]}
        a2 = {"ref": "fitter_b", "type": "m20", "zones": ["dock_north"], "attachments": ["ur5"]}
    else:
        a1 = {"ref": "fitter_a", "type": "carter", "zones": ["storage_west"], "attachments": ["z1"]}
        a2 = {"ref": "fitter_b", "type": "m20", "zones": ["dock_north"], "attachments": ["z1"]}
    return {
        "skeleton": "ASM-03",
        "robots": [a1, a2],
        "entities": [
            {"ref": "module_1", "asset": "primitive_box", "zones": ["yard_south"],
             "size_m": [0.35, 0.35, 0.35], "mass_kg": 4.0},
            {"ref": "module_2", "asset": "primitive_box", "zones": ["dock_north"],
             "size_m": [0.35, 0.35, 0.35], "mass_kg": 4.0},
            {"ref": "module_3", "asset": "primitive_box", "zones": ["midband"],
             "size_m": [0.35, 0.35, 0.35], "mass_kg": 4.0},
        ],
        "targets": [{"ref": "line_cell", "zones": ["assembly_east"], "radius_m": 0.5}],
        "phase_nodes": [
            {"id": "place_1", "skill": "transport_object", "agent_ref": "fitter_a",
             "target_ref": "line_cell", "object_ref": "module_1", "prerequisites": []},
            {"id": "place_2", "skill": "transport_object", "agent_ref": "fitter_b",
             "target_ref": "line_cell", "object_ref": "module_2", "prerequisites": ["place_1"]},
            {"id": "place_3", "skill": "transport_object", "agent_ref": "fitter_a",
             "target_ref": "line_cell", "object_ref": "module_3", "prerequisites": ["place_2"]},
        ],
    }


def _trnramp(mass_level=None, arm="z1"):
    """West-ramp half-level task: the quadruped climbs the drivable ramp to
    the elevated marker while the wheeled base covers the ground leg."""
    return {
        "skeleton": "TRN-RAMP",
        "robots": [
            {"ref": "climber", "type": "go2", "zones": ["yard_south"], "attachments": []},
            {"ref": "courier", "type": "carter", "zones": ["midband"], "attachments": []},
        ],
        "entities": [],
        "targets": [
            {"ref": "ramp_top", "zones": ["ramp_top_west"], "radius_m": 0.2},
            {"ref": "ground_check", "zones": ["storage_west"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "climb_ramp", "skill": "navigate", "agent_ref": "climber",
             "target_ref": "ramp_top", "object_ref": None, "prerequisites": []},
            {"id": "ground_leg", "skill": "navigate", "agent_ref": "courier",
             "target_ref": "ground_check", "object_ref": None, "prerequisites": []},
        ],
    }


SKELETONS = {"TRN-01": _trn01, "TRN-02": _trn02, "TRN-03": _trn03, "TRN-MEZ": _trnmez,
             "TRN-RAMP": _trnramp, "ASM-01": _asm01, "ASM-02": _asm02, "ASM-03": _asm03,
             "SAR-01": _sar01, "SAR-02": _sar02, "SAR-03": _sar03,
             "COV-01": _cov01, "COV-02": _cov02, "COV-03": _cov03}


SKELETON_DESCRIPTIONS = {
    "TRN-01": "Single-carrier payload-mass sweep baseline (not a collaboration task)",
    "TRN-02": "Prescribed carrier/escort long-beam workflow; team necessity is not established",
    "TRN-03": "Prescribed two-agent relay and ownership-transfer workflow; single-agent counterfactual remains feasible",
    "TRN-MEZ": "Prescribed mezzanine carrier/escort workflow with level geometry",
    "TRN-RAMP": "Prescribed quadruped and wheeled parallel navigation workflow",
    "ASM-01": "Prescribed two-agent sequenced part-delivery workflow",
    "ASM-02": "Prescribed heavy/delicate allocation workflow; requires an external payload policy",
    "ASM-03": "Prescribed three-part timing chain",
    "SAR-01": "Prescribed perception/disposal workflow with an occluder",
    "SAR-02": "Prescribed aerial/ground search workflow",
    "SAR-03": "Prescribed two-carrier multi-casualty workflow",
    "COV-01": "Prescribed wheeled/quadruped coverage workflow",
    "COV-02": "Prescribed aerial/ground patrol workflow",
    "COV-03": "Three-robot independent parallel-waypoint baseline; no team-necessity claim",
}


def skeleton_spec(skeleton, *, mass_level=None, arm="z1"):
    if skeleton not in SKELETONS:
        raise ScenarioError(f"unknown skeleton '{skeleton}'")
    if arm not in {"z1", "ur5"}:
        raise ScenarioError(f"unknown arm variant '{arm}'")
    return SKELETONS[skeleton](mass_level, arm)


def skeleton_family(skeleton, *, family_id, seed, mass_levels=("low", "high"), arm="z1"):
    """Build a family spec for a skeleton recipe."""
    if skeleton in {"TRN-01", "TRN-02"}:
        ref = "cargo" if skeleton == "TRN-01" else "beam"
        levels = mass_levels if skeleton == "TRN-01" else ("low", "high")
        base = skeleton_spec(skeleton, mass_level=levels[0], arm=arm)
        conditions = [
            {"id": level, "level": level,
             "delta": [{"op": "set_mass", "ref": ref,
                        "mass_kg": (_MASS_LEVELS if skeleton == "TRN-01"
                                    else {"low": 12.0, "high": 24.0})[level]}]}
            for level in levels
        ]
        target = "H2=payload_mass"
    else:
        base = skeleton_spec(skeleton, arm=arm)
        conditions = [{"id": "base", "level": "base", "delta": []}]
        target = None
    return {
        "family_id": family_id,
        "seed": seed,
        "scientific_target": target,
        "base_spec": base,
        "conditions": conditions,
    }


def task_spec_schema(template):
    """JSON Schema (Structured Outputs strict) for LLM task-spec proposals."""
    from .schema import array, number, obj

    zone_enum = {"type": "string", "enum": [zone.id for zone in template.zones]}
    identifier = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$"}
    attachment_enum = {"type": "string", "enum": sorted(catalog.attachment_catalog() | catalog.tool_catalog())}
    robot = obj({
        "ref": identifier,
        "type": {"type": "string", "enum": list(ROBOT_PROFILES)},
        "zones": array(zone_enum, 1, 3),
        "attachments": array(attachment_enum, 0, 8),
    })
    entity = obj({
        "ref": identifier,
        "asset": {"type": "string", "enum": list(ASSETS)},
        "zones": array(zone_enum, 1, 3),
        "size_m": array(number(0.02, 4), 3, 3),
        "mass_kg": {"anyOf": [number(0.01, 200), {"type": "null"}]},
        "mode": {"type": "string", "enum": ["static", "dynamic", "kinematic", "visual"]},
    })
    target = obj({"ref": identifier, "zones": array(zone_enum, 1, 3), "radius_m": number(0.05, 2)})
    node = obj({
        "id": identifier,
        "skill": {"type": "string", "enum": list(SKILLS)},
        "agent_ref": identifier,
        "agents": array(identifier, 2, 4),
        "target_ref": identifier,
        "object_ref": {"anyOf": [identifier, {"type": "null"}]},
        "prerequisites": array(identifier, 0, 32),
        "timeout_s": number(0.1, 600),
        "duration_s": {"anyOf": [number(0.01, 600), {"type": "null"}]},
        "scan_height_m": {"anyOf": [number(0.01, 100), {"type": "null"}]},
        "scan_yaw_rad": {"anyOf": [number(-3.141592653589793, 3.141592653589793), {"type": "null"}]},
        "wait_yaw_rad": {"anyOf": [number(-3.141592653589793, 3.141592653589793), {"type": "null"}]},
        "resources": array(identifier, 0, 32),
    })
    return obj({
        "skeleton": {"type": "string", "enum": list(SKELETONS)},
        "instruction": {"type": "string"},
        "robots": array(robot, 1, 8),
        "entities": array(entity, 0, 32),
        "targets": array(target, 1, 32),
        "phase_nodes": array(node, 1, 32),
    })


def task_spec_manifest(template, occupancy=None):
    """Prompt-facing view: zones, assets, skills, skeletons. No file paths."""
    from .templates import template_view

    view = template_view(template)
    view["assets"] = [
        {"asset_key": key, "modes": list(asset.modes), "description": asset.description}
        for key, asset in ASSETS.items()
    ]
    view["skills"] = {key: value for key, value in SKILLS.items()}
    view["skeletons"] = dict(SKELETON_DESCRIPTIONS)
    arm_hosts = {arm: sorted(catalog.attachment_entry(arm).supported_robots) for arm in ("ur5", "z1")}
    view["manipulator_hosts"] = arm_hosts
    view["constraints"] = [
        "navigate and wait may be assigned to any robot; transport_object and handoff_object may ONLY be "
        "assigned (agent_ref) to a robot whose attachments include ur5 or z1 -- armless robots must not "
        "receive transport or handoff nodes",
        "Manipulator hosts are strict: ur5 only on " + str(arm_hosts["ur5"]) + "; z1 only on " + str(arm_hosts["z1"]),
        "mass_kg must be null for static/visual entities and set for dynamic/kinematic ones",
        "Target radius_m is at most 0.9 (the anchor clearance disc); larger goal zones are not placeable",
        "An occluder or barrier must not seal every route; connectivity is verified locally",
        "Use zone ids, asset keys, and skill keys exactly as listed; never invent paths or coordinates",
        "Assigning nodes to multiple agents does not prove collaboration necessity; generated pools "
        "classify the counterfactual minimum team separately",
        "All refs (robots, entities, targets) share one namespace and must be globally unique; "
        "do not reuse a marker's name as both an entity ref and a target ref",
    ]
    return view



def layout_signature(data):
    """Stable signature of the physical layout, ignoring volatile experiment fields.

    Two instances with the same signature share robots, entities, and targets
    up to mass values, seed, and task name -- i.e. an accidental duplicate
    layout rather than a paired condition. Conditions within one family are
    expected to share a signature; instances across seeds should not.
    """
    import hashlib
    import json as _json

    def norm_pose(pose):
        return [[round(v, 3) for v in pose["position"]], [round(v, 3) for v in pose["rotation"]]]

    canonical = {
        "robots": [
            {"type": r["type"],
             "attachments": sorted(a["type"] for a in r["attachments"]),
             "pose": norm_pose(r["spawn_pose"])}
            for r in data["robots"]
        ],
        "entities": [
            {"id": e["id"], "asset": e["asset_key"], "size": [round(v, 3) for v in e["size_m"]],
             "pose": norm_pose(e["spawn_pose"]), "mode": e["physics"]["mode"]}
            for e in data["composition"]["entities"]
        ],
        "targets": [
            {"id": t["id"], "pos": [round(v, 3) for v in t["position"]], "radius": round(t["radius_m"], 3)}
            for t in data["composition"]["targets"]
        ],
    }
    return hashlib.sha256(_json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:16]


def diff_fields(left, right):
    """Field paths where two validated instances differ (for pairing audits)."""
    differences = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    differences.append(f"{path}.{key}")
                else:
                    walk(a[key], b[key], f"{path}.{key}")
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                differences.append(f"{path}[len]")
            else:
                for index, (ia, ib) in enumerate(zip(a, b)):
                    walk(ia, ib, f"{path}[{index}]")
        elif a != b:
            differences.append(path)

    walk(left, right, "$")
    return differences
