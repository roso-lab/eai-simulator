"""Outcome-based city task drafts and explicit admission gaps.

Use the shared public/API validators. The preparation draft still carries no
full mission witnesses: they are declaration-level author plans owned by the
registered city goal-bundle builder, whose business necessary conditions are
reviewed there while execution evidence stays external.
"""
from __future__ import annotations

from copy import deepcopy

from demo.benchmark.goal_driven_contract import GOAL_CONTRACT_VERSION, validate_goal_task_spec
from demo.benchmark.goal_driven_fixtures import execution_contract
from demo.benchmark.skill_ontology import SKILL_CATALOG_SCHEMA, SKILL_CATALOG_VERSION
from demo.benchmark.provenance import canonical_sha256


def candidate_pool():
    # These are interface declarations, not role assignments or validations of
    # climbing, grasping, firefighting, crowd control or vehicle braking.
    # ``camera`` and ``event_bus`` are the shared ontology names; the more
    # specific onboard-camera label remains as an implementation detail.
    return [
        {"id": "go2_1", "type": "go2", "mobility": "ground",
         "public_capabilities": ["ground_navigation", "relay"],
         "interfaces": ["velocity_command", "event_bus"],
         "physical_profile": {"configuration_id": "city_go2_navigation_v1",
                              "body_size_m": [0.8, 0.5, 0.8], "effective_payload_kg": 0.0,
                              "reach_m": 0.0, "grasp_interfaces": [], "max_step_height_m": 0.22}},
        {"id": "carter_1", "type": "carter", "mobility": "ground",
         "public_capabilities": ["ground_navigation", "relay"],
         "interfaces": ["velocity_command", "event_bus"],
         "physical_profile": {"configuration_id": "city_carter_navigation_v1",
                              "body_size_m": [0.8, 0.65, 1.2], "effective_payload_kg": 0.0,
                              "reach_m": 0.0, "grasp_interfaces": [], "max_step_height_m": 0.03}},
        {"id": "iris_1", "type": "iris", "mobility": "aerial",
         "public_capabilities": ["aerial_navigation", "observe", "relay"],
         "interfaces": ["goal_position", "onboard_camera", "camera", "event_bus"],
         "physical_profile": {"configuration_id": "city_iris_camera_v1",
                              "body_size_m": [0.6, 0.6, 0.3], "effective_payload_kg": 0.0,
                              "reach_m": 0.0, "grasp_interfaces": [], "max_step_height_m": 0.0}},
    ]


def _public_routes(semantic_map: dict) -> list[dict]:
    """Publish conservative approach resources without publishing an incident route."""
    regions = {row["id"]: row for row in semantic_map["regions"]}
    routes = [{"id": "city_ground_road_network", "from_region_id": "road_surface",
               "to_region_id": "road_surface", "access_modes": ["ground_navigation"],
               "length_m": 1.0, "clearance_m": [4.0, 4.0], "max_step_height_m": 0.0,
               "resource_id": "road_network", "capacity": 4},
              {"id": "city_aerial_road_network", "from_region_id": "road_surface",
               "to_region_id": "road_surface", "access_modes": ["aerial_navigation"],
               "length_m": 1.0, "clearance_m": [4.0, 4.0], "max_step_height_m": 0.0,
               "resource_id": "aerial_network", "capacity": 2}]
    for identifier, region in regions.items():
        if identifier == "road_surface":
            continue
        modes = [mode for mode in ("ground_navigation", "aerial_navigation")
                 if mode in region["access_modes"]]
        if not modes:
            continue
        routes.append({"id": f"approach_{identifier}", "from_region_id": "road_surface",
                       "to_region_id": identifier, "access_modes": modes,
                       "length_m": 10.0, "clearance_m": [4.0, 4.0],
                       "max_step_height_m": 0.0, "resource_id": f"approach_{identifier}",
                       "capacity": region["resource_capacity"]})
    return routes


def city_outcome_rows():
    """The frozen urban incident-response outcome family shared by every city artifact."""
    return [
        ("incident_assessed", "事故范围、受影响建筑和人车状态得到可追溯确认。",
         "当前观察支持事故位置与影响范围，并明确区分已确认和未知信息。",
         ["timestamped_incident_observation", "observation_provenance"]),
        ("exposed_people_accounted_for", "受危险影响的人员到达可接受的安全位置且人数连续可核对。",
         "受影响人员身份与轨迹连续；到达经审核集合区，人数无遗漏或重复，且未穿越禁入危险区。",
         ["exposed_actor_roster", "guidance_acknowledgement", "measured_person_trajectory", "assembly_arrival"]),
        ("secondary_traffic_protected", "后续交通得到停车或改道保护，避免继续进入事故危险区。",
         "管制服务确认已应用指令，实测车辆运动在规定反应窗口后满足禁入边界和停车距离约束。",
         ["traffic_control_acknowledgement", "measured_vehicle_trajectory", "controlled_ingress_check"]),
        ("response_access_preserved", "救援进入路线与人员撤离路线可用，并向响应方交付当前态势。",
         "完整通路的当前净空、人员车辆状态与接收方回执一致；旧消息和自报成功不能替代证据。",
         ["continuous_access_check", "current_incident_record", "response_service_receipt"]),
    ]


def build_public_task(semantic_map: dict, *, response_budget_s=180.0, max_active_agents=3):
    from demo.city_benchmark.task_logic import default_deadline_policy, public_deadline_constraints

    deadline_policy = default_deadline_policy(response_budget_s)
    outcomes = city_outcome_rows()
    public = {
        "schema": "eai.benchmark.goal_task_spec", "version": GOAL_CONTRACT_VERSION,
        "task_id": "CITY-GOAL-01", "evaluation_track": "goal_driven_team_planning_v1",
        "mission": {"scene_key": "city", "goal": "应对城市道路突发事故：查明影响，保护人员和后续交通，保持救援通路。",
                    "context": "城市街区有背景交通、步行人员和建筑；根据实际到达的告警与观察组织响应。具体事故位置和未来事件不预先公开。"},
        "required_outcomes": [{"id": identifier, "description": description, "acceptance": acceptance,
                               "evidence_expectations": evidence}
                              for identifier, description, acceptance, evidence in outcomes],
        "candidate_robot_pool": candidate_pool(),
        "skill_catalog_version": f"{SKILL_CATALOG_SCHEMA}.v{SKILL_CATALOG_VERSION}",
        "execution_contract": execution_contract(),
        "public_world": {"regions": deepcopy(semantic_map["regions"]), "objects": [], "vertical_links": [],
                         "routes": _public_routes(semantic_map),
                         "public_notes": "配套静态地图使用米和 Z-up；集合区为候选，不代表实时安全。人员车辆身份与危险状态须由公开观察绑定。"},
        "business_constraints": [
            "执行团队、任务分解、调度和恢复方法由参与者选择；仅能使用已接入的公开能力和服务。",
            "不得用机器人撞击或以机体阻挡高速失控事故车；后续交通保护使用具备确认回执的停车或改道接口。",
            "静态空地不能自动判定为安全集合区；需满足人数容量、实测可达性和当前危险净空要求。",
            "不能仅因导航到人员附近就判定人员已疏散，也不能仅因提交管制请求就判定车辆已停止。",
            "火焰与烟雾的可视效果不作为温度、毒性、灭火成功或建筑可进入的证据。",
            "事故演示的背景车自动暂停和路人预设行走均不记作参与者成果。",
        ] + public_deadline_constraints(deadline_policy),
        "budgets": {"simulation_s": response_budget_s, "planning_s": 30.0,
                    "message_bytes": 8192, "model_tokens": 12000,
                    "max_active_agents": max_active_agents},
        "experiment_factor": {"id": "communication_quality", "level": "immediate",
                              "public_rule": "仅使用已经送达的信息；成员和能力变化仅在观察到事件后生效。",
                              "frozen_inputs": ["scene_layout", "incident_instance", "actor_initial_states",
                                                "candidate_robot_pool", "budgets", "model_budget"]},
    }
    validate_goal_task_spec(public)
    return public


def required_services():
    """City services exposed to participants with runtime validation pending.

    Registration in ``eai.skills`` only defines the participant-facing call
    contract.  It does not activate an Isaac episode backend or certify a
    service receipt, so the release gate remains closed until the live city
    host supplies those witnesses.
    """
    return [
        {"id": "guide_people", "status": "public_skill_registered_runtime_pending",
         "participant_operation": "guide_people", "required_public_skill": "guide_people",
         "arguments": ["observed_person_ids", "assembly_region_id", "route_reference"],
         "preconditions": ["current_observed_people", "acknowledged_guidance_channel", "route_and_capacity_admitted"],
         "effects": ["people_receive_guidance"],
         "failure_codes": ["person_not_observed", "no_acknowledgement", "route_unsafe", "person_unresponsive"],
         "evidence_types": ["guidance_acknowledgement", "measured_person_trajectory", "assembly_arrival"],
         "cancel_semantics": "acknowledged_safe_hold_or_replacement_guidance",
         "timeout_semantics": "bounded_per_group_guidance_and_arrival_window",
         "owner": "human_behavior_runtime", "outcome_ids": ["exposed_people_accounted_for"]},
        {"id": "control_traffic", "status": "public_skill_registered_runtime_pending",
         "participant_operation": "control_following_traffic", "required_public_skill": "control_following_traffic",
         "arguments": ["ingress_region_id", "mode_hold_divert_release", "route_reference"],
         "preconditions": ["control_authority_available", "observed_following_traffic", "safe_control_point_admitted"],
         "effects": ["traffic_service_applies_acknowledged_policy"],
         "failure_codes": ["no_authority", "control_point_blocked", "diversion_blocked", "no_acknowledgement"],
         "evidence_types": ["traffic_control_acknowledgement", "measured_vehicle_trajectory"],
         "cancel_semantics": "keep_existing_hold_until_safe_release_is_acknowledged",
         "timeout_semantics": "explicit_service_response_and_following_vehicle_reaction_window",
         "owner": "traffic_runtime", "outcome_ids": ["secondary_traffic_protected"]},
        {"id": "report_incident", "status": "public_skill_registered_runtime_pending",
         "participant_operation": "report_incident", "required_public_skill": "report_incident",
         "arguments": ["incident_observation_refs", "recipient_service_id"],
         "preconditions": ["fresh_delivered_evidence", "reachable_response_service"],
         "effects": ["response_service_receives_incident_record"],
         "failure_codes": ["evidence_expired", "recipient_unavailable", "receipt_missing"],
         "evidence_types": ["response_service_receipt", "observation_provenance"],
         "cancel_semantics": "cancel_before_receipt_commit",
         "timeout_semantics": "bounded_message_and_receipt_window",
         "owner": "domain_service_runtime", "outcome_ids": ["response_access_preserved"]},
    ]


def participant_service_coverage() -> dict:
    """Audit whether city business services have public participant entry points.

    This audit distinguishes a participant-visible service contract from a
    validated city episode backend.  A registered skill makes the service
    invocable; it does not make an acknowledgement, trajectory or receipt a
    business result.
    """
    from demo.benchmark.skill_ontology import public_skill_catalog

    available = set(public_skill_catalog()["skills"])
    rows = []
    for service in required_services():
        required = service["required_public_skill"]
        rows.append({"service_id": service["id"],
                     "participant_operation": service["participant_operation"],
                     "required_public_skill": required,
                     "public_entry_point": required in available,
                     "status": ("registered_runtime_pending" if required in available
                                else "missing_public_skill"),
                     "runtime_status": "pending"})
    return {"schema": "eai.city.participant_service_coverage", "version": 1,
            "available_public_skills": sorted(available), "services": rows,
            "all_services_publicly_invocable": all(row["public_entry_point"] for row in rows)}


CITY_EPISODE_BINDING_SCHEMA = "eai.city.episode_binding"
CITY_EPISODE_BINDING_VERSION = 1
_EPISODE_BINDING_FIELDS = (
    "schema", "version", "status", "seed", "affected_person_roster", "assembly_capacity",
    "dynamic_hazard_boundaries", "safe_ingress_control_points",
    "following_traffic_response_window", "response_service_endpoint",
)
_EPISODE_BINDING_STATUS = "author_frozen_runtime_admission_pending"
_INGRESS_OFFSETS_M = ((4.0, 0.0), (-4.0, 0.0), (0.0, 4.0), (0.0, -4.0), (6.0, 6.0), (-6.0, -6.0))


def episode_parameter_names():
    """Episode parameters that are now author-bound instead of runtime-picked."""
    return ["affected_person_roster", "dynamic_hazard_boundaries",
            "assembly_capacity", "safe_ingress_control_points",
            "following_traffic_response_window", "response_service_endpoint"]


def build_episode_binding(semantic_map, *, people_count=4, seed=7):
    """Freeze the concrete episode parameters against the authored semantic map.

    The runtime service probe used to pick the affected person, the following
    vehicle, the assembly target, the hazard box, the access probe and the
    divert route at run time. This binding moves those choices into author
    declarations: the roster reuses the deterministic ``build_response_people``
    seed, per-person assembly candidates, axis-aligned hazard extents around the
    frozen-seed incident sample, bounded access probe offsets, the traffic
    reaction window and the response-service receipt endpoint. Admission
    against measured geometry at episode time stays a separate gate.
    """
    from demo.city_benchmark.response import build_response_people
    from demo.city_benchmark.task_logic import default_deadline_policy

    people = build_response_people(semantic_map, count=people_count, seed=seed)
    assemblies = {row["id"]: row for row in semantic_map.get("bindings", ())
                  if str(row.get("id", "")).startswith("assembly_candidate_")}
    grouped = {}
    for person in people:
        grouped.setdefault(person.assembly_region_id, []).append(person)
    capacity_rows = []
    for region_id in sorted(grouped):
        binding = assemblies[region_id]
        geometry = binding.get("geometry", {})
        if geometry.get("type") != "Point":
            raise ValueError(f"assembly candidate '{region_id}' is not a point")
        x, y = (float(value) for value in geometry["coordinates"][:2])
        capacity_rows.append({
            "region_id": region_id, "center_xy_m": [x, y],
            "radius_m": float(binding.get("candidate_radius_m", 0.75)),
            "capacity_people": len(grouped[region_id]),
            "bound_person_ids": [person.person_id for person in grouped[region_id]],
        })
    policy = default_deadline_policy()
    return {
        "schema": CITY_EPISODE_BINDING_SCHEMA, "version": CITY_EPISODE_BINDING_VERSION,
        "status": _EPISODE_BINDING_STATUS, "seed": int(seed),
        "affected_person_roster": [{
            "person_id": person.person_id, "asset_id": person.asset_id,
            "start_xyz": [float(v) for v in person.start_xyz],
            "evacuation_route": [[float(v) for v in point] for point in person.evacuation_route],
            "assembly_region_id": person.assembly_region_id,
            "walk_speed_mps": float(person.walk_speed_mps),
            "jog_speed_mps": float(person.jog_speed_mps),
        } for person in people],
        "assembly_capacity": capacity_rows,
        "dynamic_hazard_boundaries": {
            "derivation": "axis_aligned_box_centred_on_incident_position_sampled_from_AccidentPlan_with_frozen_seed",
            "incident_seed": "freeze_at_episode_instantiation",
            "exclusion_half_extents_m": [4.0, 4.0],
            "z_window_above_road_m": [0.05, 3.0],
            "control_point_rule": "min_max_corners_from_incident_centre_plus_half_extents",
            "admission": "native_region_reader_measured_clear_required",
        },
        "safe_ingress_control_points": {
            "anchor": "bound_assembly_region_center",
            "candidate_offsets_m": [list(offset) for offset in _INGRESS_OFFSETS_M],
            "probe_half_extents_xy_m": [1.0, 1.0],
            "probe_z_window_m": [0.05, 2.5],
            "selection": "first_measured_clear_candidate_at_request_time",
        },
        "following_traffic_response_window": {
            "vehicle_binding": "active_following_vehicles_bound_at_episode_instantiation",
            "control_ack_deadline_s": float(policy["traffic_control_ack_s"]),
            "trajectory_reaction_window_s": float(policy["traffic_reaction_window_s"]),
            "control_modes": ["hold", "divert", "release"],
        },
        "response_service_endpoint": {
            "service_id": "city_dispatch",
            "receiver_entrypoint": "demo.city_benchmark.live_witness.CityIncidentReceiver",
            "receipt_deadline_s": float(policy["report_receipt_s"]),
            "continuous_access_min_span_s": 0.5,
        },
        "notes": "coordinates share the city_world metre frame; hazards and ingress probes still require measured episode-time admission",
    }


def review_episode_binding(binding, semantic_map):
    """Machine-check an author-frozen episode binding against the authored map."""
    import math

    def fail(code):
        raise ValueError(f"episode binding rejected: {code}")

    if not isinstance(binding, dict):
        fail("binding must be an object")
    if set(binding) != set(_EPISODE_BINDING_FIELDS) | {"notes"}:
        fail("fields mismatch")
    if (binding["schema"] != CITY_EPISODE_BINDING_SCHEMA
            or type(binding["version"]) is not int
            or binding["version"] != CITY_EPISODE_BINDING_VERSION
            or binding["status"] != _EPISODE_BINDING_STATUS):
        fail("unsupported schema, version or status")
    if isinstance(binding["seed"], bool) or not isinstance(binding["seed"], int):
        fail("seed must be an integer")

    regions = {row["id"]: row for row in semantic_map.get("regions", ())}
    assemblies = {row["id"]: row for row in semantic_map.get("bindings", ())
                  if str(row.get("id", "")).startswith("assembly_candidate_")}

    roster = binding["affected_person_roster"]
    if not isinstance(roster, list) or not roster:
        fail("empty roster")
    person_ids = []
    for row in roster:
        if not isinstance(row, dict) or set(row) != {
                "person_id", "asset_id", "start_xyz", "evacuation_route",
                "assembly_region_id", "walk_speed_mps", "jog_speed_mps"}:
            fail("roster row fields mismatch")
        if not isinstance(row["person_id"], str) or not row["person_id"]:
            fail("invalid person identity")
        person_ids.append(row["person_id"])
        if row["person_id"] in person_ids[:-1]:
            fail("duplicate person identity")
        if not isinstance(row["asset_id"], str) or not row["asset_id"]:
            fail("invalid asset identity")
        region_id = row["assembly_region_id"]
        if region_id not in assemblies or region_id not in regions:
            fail(f"unknown assembly candidate {region_id!r}")
        if regions[region_id].get("kind") != "assembly_candidate":
            fail(f"{region_id} is not an assembly candidate region")
        geometry = assemblies[region_id].get("geometry", {})
        if geometry.get("type") != "Point":
            fail(f"assembly candidate {region_id!r} is not a point")
        speeds = (row["walk_speed_mps"], row["jog_speed_mps"])
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
               for v in speeds) or speeds[1] <= speeds[0]:
            fail("roster speeds must be finite with jog above walk")
        route = row["evacuation_route"]
        if (not isinstance(route, list) or len(route) < 2
                or any(not isinstance(point, list) or len(point) != 3
                       or any(isinstance(v, bool) or not isinstance(v, (int, float))
                              or not math.isfinite(v) for v in point) for point in route)):
            fail("evacuation route must contain finite three-coordinate points")
        if any(abs(a - b) > 1e-9 for a, b in zip(route[0], row["start_xyz"])):
            fail("route must start at the bound start position")
        target_x, target_y = (float(v) for v in geometry["coordinates"][:2])
        if math.hypot(route[-1][0] - target_x, route[-1][1] - target_y) > 1e-6:
            fail("route must end at the bound assembly candidate")

    capacity = binding["assembly_capacity"]
    if not isinstance(capacity, list) or not capacity:
        fail("empty assembly capacity")
    bound = {}
    for row in capacity:
        if not isinstance(row, dict) or set(row) != {
                "region_id", "center_xy_m", "radius_m", "capacity_people", "bound_person_ids"}:
            fail("capacity row fields mismatch")
        region_id = row["region_id"]
        if region_id not in assemblies:
            fail(f"unknown assembly candidate {region_id!r}")
        geometry = assemblies[region_id]["geometry"]
        x, y = (float(v) for v in geometry["coordinates"][:2])
        if (not isinstance(row["center_xy_m"], list) or len(row["center_xy_m"]) != 2
                or any(abs(a - b) > 1e-9 for a, b in zip(row["center_xy_m"], (x, y)))):
            fail(f"{region_id} capacity centre must match the authored candidate")
        if (isinstance(row["radius_m"], bool) or not isinstance(row["radius_m"], (int, float))
                or not math.isfinite(row["radius_m"]) or row["radius_m"] <= 0):
            fail(f"{region_id} capacity radius must be positive")
        ids = row["bound_person_ids"]
        if (not isinstance(ids, list) or not ids
                or any(value not in person_ids for value in ids)
                or len(ids) != len(set(ids))):
            fail(f"{region_id} bound persons must be roster members without repeats")
        if row["capacity_people"] != len(ids):
            fail(f"{region_id} capacity must equal its bound person count")
        bound[region_id] = ids
    if sorted(bound) != sorted({row["assembly_region_id"] for row in roster}):
        fail("capacity rows must cover exactly the roster assemblies")
    if sorted(pid for ids in bound.values() for pid in ids) != sorted(person_ids):
        fail("capacity rows must account for every roster member exactly once")

    hazard = binding["dynamic_hazard_boundaries"]
    if not isinstance(hazard, dict) or set(hazard) != {
            "derivation", "incident_seed", "exclusion_half_extents_m",
            "z_window_above_road_m", "control_point_rule", "admission"}:
        fail("hazard template fields mismatch")
    extents = hazard["exclusion_half_extents_m"]
    z_window = hazard["z_window_above_road_m"]
    if (not isinstance(extents, list) or len(extents) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or v <= 0 for v in extents)):
        fail("hazard half extents must be positive")
    if (not isinstance(z_window, list) or len(z_window) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or v <= 0 for v in z_window)):
        fail("hazard z window must be positive above the road surface")
    for key in ("derivation", "incident_seed", "control_point_rule", "admission"):
        if not isinstance(hazard[key], str) or not hazard[key]:
            fail("hazard template strings must be nonempty")

    ingress = binding["safe_ingress_control_points"]
    if not isinstance(ingress, dict) or set(ingress) != {
            "anchor", "candidate_offsets_m", "probe_half_extents_xy_m",
            "probe_z_window_m", "selection"}:
        fail("ingress control point fields mismatch")
    offsets = ingress["candidate_offsets_m"]
    if (not isinstance(offsets, list) or not offsets
            or any(not isinstance(offset, list) or len(offset) != 2
                   or any(isinstance(v, bool) or not isinstance(v, (int, float))
                          or not math.isfinite(v) for v in offset) for offset in offsets)):
        fail("ingress offsets must be finite two-coordinate candidates")
    for key in ("probe_half_extents_xy_m", "probe_z_window_m"):
        window = ingress[key]
        if (not isinstance(window, list) or len(window) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v <= 0 for v in window)):
            fail(f"{key} must contain positive finite values")
    for key in ("anchor", "selection"):
        if not isinstance(ingress[key], str) or not ingress[key]:
            fail("ingress template strings must be nonempty")

    window = binding["following_traffic_response_window"]
    if not isinstance(window, dict) or set(window) != {
            "vehicle_binding", "control_ack_deadline_s",
            "trajectory_reaction_window_s", "control_modes"}:
        fail("following traffic response window fields mismatch")
    for key in ("control_ack_deadline_s", "trajectory_reaction_window_s"):
        value = window[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            fail(f"{key} must be positive")
    if window["control_modes"] != ["hold", "divert", "release"]:
        fail("control modes must cover hold, divert and release")
    if not isinstance(window["vehicle_binding"], str) or not window["vehicle_binding"]:
        fail("vehicle binding must be a nonempty string")

    endpoint = binding["response_service_endpoint"]
    if not isinstance(endpoint, dict) or set(endpoint) != {
            "service_id", "receiver_entrypoint", "receipt_deadline_s", "continuous_access_min_span_s"}:
        fail("response service endpoint fields mismatch")
    for key in ("service_id", "receiver_entrypoint"):
        if not isinstance(endpoint[key], str) or not endpoint[key]:
            fail(f"{key} must be a nonempty string")
    for key in ("receipt_deadline_s", "continuous_access_min_span_s"):
        value = endpoint[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            fail(f"{key} must be positive")
    return {"validated": True, "schema": CITY_EPISODE_BINDING_SCHEMA,
            "version": CITY_EPISODE_BINDING_VERSION,
            "bound_person_count": len(person_ids),
            "bound_assembly_count": len(bound),
            "ingress_candidate_count": len(offsets),
            "runtime_admission_pending": True,
            "execution_validated": False}


def authoring_record(public_task: dict, provenance: dict, obstacles: list[dict], semantic_map: dict | None = None):
    from demo.city_benchmark.scene.scenario import AccidentTiming, IMPACT_SPEED_MPS
    from demo.city_benchmark.response import build_response_people
    from demo.city_benchmark.task_logic import (default_deadline_policy,
                                                factor_effect_requirements,
                                                public_decision_principles)
    from dataclasses import asdict

    # The private response roster is generated from the authored map by the
    # episode seed. It is a preparation aid, not a participant-visible answer.
    response_template = None
    episode_binding = None
    if semantic_map is not None:
        response_template = {
            "seed": 7,
            "people": [asdict(person) for person in build_response_people(semantic_map, count=4, seed=7)],
            "status": "private_episode_template_pending_route_and_hazard_validation",
        }
        # Formal episode binding: the affected roster, per-person assembly
        # candidates, hazard template, ingress probes, reaction window and
        # receipt endpoint are author declarations now, not runtime picks.
        episode_binding = build_episode_binding(semantic_map, people_count=4, seed=7)

    return {
        "schema": "eai.city.authoring_private", "version": 1,
        "task_id": public_task["task_id"], "public_task_sha256": canonical_sha256(public_task),
        "source_provenance": deepcopy(provenance),
        "static_prim_bindings": [{"id": r["id"], "runtime_prim_path": r["runtime_prim_path"]} for r in obstacles],
        "incident_template": {"timing": asdict(AccidentTiming()), "impact_speed_mps": IMPACT_SPEED_MPS,
                              "impact_time": "derived_from_selected_AccidentPlan_not_a_fixed_6.8_seconds",
                              "incident_seed": "freeze_at_episode_instantiation", "selected_target": None,
                              "source": "demo.city_benchmark.scene.scenario.AccidentPlan", "visibility": "author_only"},
        "outcome_evidence_requirements": deepcopy(public_task["required_outcomes"]),
        "full_mission_witnesses": [],
        "witness_status": "declaration_level_witnesses_admitted_via_city_goal_bundle_builder_draft_carries_none",
        "hidden_evaluator_status": "authoring_side_evaluator_buildable_via_city_goal_bundle_builder;runtime_scoring_binding_pending",
        "required_services": required_services(),
        "task_logic": {
            "schema": "eai.city.task_logic",
            "version": 1,
            "decision_principles": public_decision_principles(),
            "factor_effect_requirements": factor_effect_requirements(),
            "deadline_policy": default_deadline_policy(public_task["budgets"]["simulation_s"]),
            "status": "predicate_skeleton_pending_runtime_binding",
        },
        "response_template": response_template,
        "episode_binding": episode_binding,
        "private_factor_design": {
            "communication": "freeze captures/content/TTL; vary only delivery channel; prove a decision is affected",
            "member_exit": "withdraw active responder only with reachable remaining capacity or a disclosed join event",
            "member_join": "reserve outside public pool until arrival; no automatic task replacement",
            "capability_change": "versioned sensor/channel availability with observed readback",
            "frozen": ["map", "accident_seed", "actor_states", "budgets", "team_pool", "business_outcomes"],
        },
        "required_episode_parameters": episode_parameter_names(),
    }


def review_readiness(public_task: dict, semantics: dict):
    validate_goal_task_spec(public_task)
    pools = public_task["candidate_robot_pool"]
    candidates = {skill: [r["id"] for r in pools if skill in r["public_capabilities"]]
                  for skill in ("ground_navigation", "aerial_navigation", "observe")}
    assemblies = [r["id"] for r in semantics["regions"] if r["kind"] == "assembly_candidate"]
    service_coverage = participant_service_coverage()
    return {
        "schema": "eai.city.authoring_review", "version": 1,
        "public_contract_validated": True, "authoring_status": "blocked",
        "map_status": "static_geometry_candidate", "execution_validated": False, "scoreable": False,
        "stage_completion": {"S1_inventory": True, "S2_static_maps": True,
                             "S3_public_contract_draft": True, "S4_integration_tests": "see_test_run",
                             "S5_full_mission_admission": False},
        "private_capability_candidates": candidates,
        "participant_service_coverage": service_coverage,
        "assembly_candidate_count": len(assemblies),
        "valid_full_mission_plan_count": 0,
        "decision_scope": "two ground navigation candidates; only one aerial observer; no claim of full mission alternatives",
        "blockers": [
            "human_guidance_and_evacuation_readback", "following_traffic_control_acknowledgement",
            "incident_detector_and_delivery_provenance", "current_hazard_and_assembly_capacity_binding",
            "robot_selected_team_to_runtime_binding", "ground_and_aerial_navigation_validation",
            "observed_actor_reference_extension", "two_causally_complete_feasible_plans",
            "heterogeneity_counterfactual_and_factor_effect", "response_service_receipt",
            "city_service_runtime_witness",
        ] + ([] if len(assemblies) >= 2 else ["at_least_two_assembly_candidates"]),
        "reflection": [
            "Four background people are deliberately kept away from the accident; evacuation cannot be tested by reusing their animation as success.",
            "18 m/s scripted incident motion cannot be stopped by assigning a robot; following traffic is a separate control target.",
            "An 8-second fire sequence is not the task deadline; response requires an independently admitted budget and no loop reset.",
            "Passing the public schema is not a feasible plan or physical certificate.",
        ],
    }
