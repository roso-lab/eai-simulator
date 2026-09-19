"""One schema source for local validation and OpenAI Structured Outputs."""

from EAI.hmrs_env.env_diy import catalog
from EAI_assets.scenario_assets import DECLARED_ASSETS

# These are conservative placement envelopes, NOT measured carrying capacities.
ROBOT_PROFILES = {
    "carter": {"radius_m": 0.5, "spawn_z_m": 0.2, "mobility": "wheeled", "max_payload_kg": None},
    "simple_car": {"radius_m": 0.45, "spawn_z_m": 0.34, "mobility": "wheeled", "max_payload_kg": None},
    "pepper": {"radius_m": 0.45, "spawn_z_m": 0.0, "mobility": "wheeled", "max_payload_kg": None},
    "scout": {"radius_m": 0.65, "spawn_z_m": 0.2, "mobility": "wheeled", "max_payload_kg": None},
    "mushr_v2": {"radius_m": 0.35, "spawn_z_m": 0.0, "mobility": "wheeled", "max_payload_kg": None},
    "coco": {"radius_m": 0.4, "spawn_z_m": 0.3, "mobility": "wheeled", "max_payload_kg": None},
    "go2": {"radius_m": 0.55, "spawn_z_m": 0.4, "mobility": "quadruped", "max_payload_kg": None},
    "b2": {"radius_m": 0.7, "spawn_z_m": 0.58, "mobility": "quadruped", "max_payload_kg": None},
    "m20": {"radius_m": 0.65, "spawn_z_m": 0.52, "mobility": "quadruped", "max_payload_kg": None},
    "lite3": {"radius_m": 0.55, "spawn_z_m": 0.35, "mobility": "quadruped", "max_payload_kg": None},
    "g1": {"radius_m": 0.5, "spawn_z_m": 0.74, "mobility": "biped", "max_payload_kg": None},
    "cf2x": {"radius_m": 0.4, "spawn_z_m": 1.2, "mobility": "aerial", "max_payload_kg": None},
    "iris": {"radius_m": 0.45, "spawn_z_m": 1.0, "mobility": "aerial", "max_payload_kg": None},
    "pegasus": {"radius_m": 0.45, "spawn_z_m": 1.0, "mobility": "aerial", "max_payload_kg": None},
}
SKILLS = {
    "navigate": {"capabilities": ["navigation"], "object_required": False},
    "wait": {"capabilities": [], "object_required": False},
    "transport_object": {"capabilities": ["navigation", "manipulation"], "object_required": True},
    "handoff_object": {"capabilities": ["manipulation"], "object_required": True},
    # Joint force application: two agents carry one object together. Capacity
    # is the SUM over the assigned team, so a 60 kg crate over any single
    # robot's limit becomes feasible only with both -- the benchmark's
    # canonical forced-cooperation primitive (H2).
    "joint_transport": {"capabilities": ["navigation"], "object_required": True,
                        "team_size": 2},
    # Aerial reconnaissance: only aerial platforms (cf2x) can hover over the
    # scene and localize targets -- the H3 information-asymmetry primitive.
    "aerial_scan": {"capabilities": ["aerial"], "object_required": False},
}


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items, minimum=0, maximum=64):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def number(minimum, maximum):
    return {"type": "number", "minimum": minimum, "maximum": maximum}


def scenario_schema(*, strict=False):
    identifier = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$"}
    pose = obj({"position": array(number(-100, 100), 3, 3), "rotation": array(number(-1, 1), 4, 4)})
    choice = obj({"mode": {"type": "string", "enum": ["default"]},
                  "cfg": {"type": "string", "enum": list(catalog.controller_cfg_names())}})
    attachment_keys = sorted(catalog.attachment_catalog() | catalog.tool_catalog())
    robot = obj({"id": identifier, "type": {"type": "string", "enum": list(ROBOT_PROFILES)},
                 "controller": choice,
                 "attachments": array(obj({"type": {"type": "string", "enum": attachment_keys},
                                           "controller": {"anyOf": [choice, {"type": "null"}]}}), 0, 8),
                 "spawn_pose": pose})
    physics = obj({"mode": {"type": "string", "enum": ["static", "dynamic", "kinematic", "visual"]},
                   "mass_kg": {"anyOf": [number(0.01, 200), {"type": "null"}]},
                   "static_friction": number(0, 2), "dynamic_friction": number(0, 2),
                   "restitution": number(0, 1)})
    # Schema admission uses the audited declaration registry.  Provider-backed
    # props may be unavailable in a clean checkout, but rejecting their keys
    # here would make the public task generator fall back to primitives before
    # the separate asset-closure gate can report the real limitation.
    entity = obj({"id": identifier, "asset_key": {"type": "string", "enum": list(DECLARED_ASSETS)},
                  "size_m": array(number(0.02, 4), 3, 3), "spawn_pose": pose, "physics": physics})
    node = obj({"id": identifier, "skill_key": {"type": "string", "enum": list(SKILLS)},
                "preferred_agent": {"anyOf": [identifier, {"type": "null"}]},
                "agents": {"anyOf": [array(identifier, 2, 4), {"type": "null"}]},
                "target_ref": identifier, "object_ref": {"anyOf": [identifier, {"type": "null"}]},
                "observation_ref": {"anyOf": [identifier, {"type": "null"}]},
                "recovery_for": {"anyOf": [identifier, {"type": "null"}]},
                "prerequisites": array(identifier, 0, 32), "timeout_s": number(0.1, 600),
                "duration_s": {"anyOf": [number(0.01, 600), {"type": "null"}]},
                "scan_height_m": {"anyOf": [number(0.01, 100), {"type": "null"}]},
                "scan_yaw_rad": {"anyOf": [number(-3.141592653589793, 3.141592653589793), {"type": "null"}]},
                "wait_yaw_rad": {"anyOf": [number(-3.141592653589793, 3.141592653589793), {"type": "null"}]},
                "vertical": {"anyOf": [{"type": "object", "properties": {
                    "link_id": identifier, "kind": {"type": "string", "enum": ["stair", "ramp"]},
                    "start_level": identifier, "goal_level": identifier,
                    "approach_waypoints": {"type": "array", "items": {"type": "array", "items": number(-100, 100), "minItems": 2, "maxItems": 2}, "maxItems": 8},
                    "support_probe": {"type": "string", "enum": ["required", "validated"]}},
                    "required": ["link_id", "kind", "start_level", "goal_level", "support_probe"],
                    "additionalProperties": False}, {"type": "null"}]},
                "resources": array(identifier, 0, 32)})
    if not strict:
        for optional in ("agents", "duration_s", "resources", "observation_ref", "recovery_for",
                         "scan_height_m", "scan_yaw_rad", "wait_yaw_rad", "vertical"):
            node["required"].remove(optional)
    member_exit_event = obj({
        "id": identifier,
        "kind": {"const": "member_exit"},
        "time_s": number(0, 3600),
        "agent": identifier,
        "replacement_agent": {"anyOf": [identifier, {"type": "null"}]},
    })
    capability_declaration = {"type": "object", "minProperties": 1}
    member_join_event = obj({
        "id": identifier,
        "kind": {"const": "member_join"},
        "time_s": number(0, 3600),
        "agent": identifier,
        "capability_declaration": capability_declaration,
        "capability_version": {"type": "integer", "const": 1},
    })
    capability_change_event = obj({
        "id": identifier,
        "kind": {"const": "capability_change"},
        "time_s": number(0, 3600),
        "agent": identifier,
        "capability_declaration": capability_declaration,
        "capability_version": {"type": "integer", "minimum": 2},
    })
    fault_event = {"oneOf": [
        member_exit_event, member_join_event, capability_change_event,
    ]}
    runtime_lifecycle = obj({
        "initial_agent_ids": array(identifier, 1, 8),
        "preloaded_reserve_agent_ids": array(identifier, 0, 8),
    })
    semantic = obj({
        "task_family": identifier,
        "domain": identifier,
        "mission_brief": obj({"en": {"type": "string", "minLength": 1}, "zh_cn": {"type": "string", "minLength": 1}}),
        # Semantic artifacts may name an optional NVD prop that is unavailable
        # in a clean checkout; asset closure is a separate release-QA gate.
        "artifacts": array(obj({"id": identifier, "asset_key": identifier, "meaning": {"type": "string", "minLength": 1}}), 2, 32),
        "factors": array(obj({"id": identifier, "levels": array({"type": "string", "minLength": 1}, 2, 16), "evidence": {"type": "string", "minLength": 1}}), 3, 32),
        "objective_semantics": {"type": "object", "minProperties": 1},
        "team_lifecycle": {"type": "object", "minProperties": 1},
        "communication": {"type": "object", "minProperties": 1},
        "success_evidence": array({"type": "string", "minLength": 1}, 1, 32),
    })
    root = obj({"version": {"type": "integer", "enum": [2]}, "task_name": identifier,
                "benchmark_task_id": {"type": "string", "enum": [
                    "F-INS-01", "F-TRAFFIC-01", "F-COV-01", "F-VERT-01"]},
                "scene_key": {"type": "string", "enum": ["factory", "warehouse_nvd", "modern_house_nvd"]},
                "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                "robots": array(robot, 1, 8),
                "runtime_lifecycle": runtime_lifecycle,
                "fault_events": array(fault_event, 0, 16),
                "composition": obj({"entities": array(entity, 0, 32),
                                    "targets": array(obj({"id": identifier, "position": array(number(-100, 100), 3, 3),
                                                          "radius_m": number(0.05, 2)}), 1, 32)}),
                "phase_graph": obj({"nodes": array(node, 1, 32)}),
                "semantic_task": semantic})
    if not strict:
        root["required"].remove("semantic_task")
    if not strict:
        root["required"].remove("benchmark_task_id")
        root["required"].remove("runtime_lifecycle")
        root["required"].remove("fault_events")
    return root
