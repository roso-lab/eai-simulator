"""Author-facing, bounded task-generation contract for the three scene families.

This is input to a task-writing model, not the participant's test packet. A
generated episode still requires geometry, business-evidence and execution QA.
"""

from copy import deepcopy
from itertools import combinations
import json
import math

from .factor_matrix import build_public_factor_manifest
from .semantic_contract import build_semantic_design_catalog, review_semantic_design
from .provenance import canonical_sha256


# Author-side restrictions on the existing templates, not new runtime nodes.
_PRECEDENCE = {
    "factory": {
        "ground_approach": ["aerial_view"],
        "ground_view": ["ground_approach"],
        "route_confirmation": ["ground_view"],
    },
    "warehouse_nvd": {
        "aisle_left_access": ["rack_overview_scan"],
        "aisle_right_access": ["rack_overview_scan"],
        "oversized_crate_transfer": ["aisle_left_access", "aisle_right_access"],
        "priority_carton_stage": ["oversized_crate_transfer"],
        "dock_lane_check": ["priority_carton_stage"],
        "priority_carton_dispatch": ["dock_lane_check"],
        "outbound_inventory_scan": ["priority_carton_dispatch"],
        "dock_confirmation_hold": ["outbound_inventory_scan"],
        "aisle_closeout": ["dock_confirmation_hold"],
    },
    "modern_house_nvd": {
        "furniture_side_access": ["room_entry_scan"],
        "doorway_access_check": ["room_entry_scan"],
        "coordinated_furniture_reposition": ["furniture_side_access", "doorway_access_check"],
        "furniture_clearance_scan": ["coordinated_furniture_reposition"],
        "keepsake_relocation": ["furniture_clearance_scan"],
        "room_entry_verification": ["keepsake_relocation"],
        "reviewer_standby": ["room_entry_verification"],
        "hall_release": ["reviewer_standby"],
        "room_condition_scan": ["hall_release"],
        "placement_confirmation_hold": ["room_condition_scan"],
    },
}
_PROP_ROLES = {
    "warehouse_nvd": {"oversized_crate_transfer": "wooden_crate_a",
                      "priority_carton_stage": "cardbox_c",
                      "priority_carton_dispatch": "cardbox_c"},
    "modern_house_nvd": {"coordinated_furniture_reposition": "mh_armchair",
                         "keepsake_relocation": "mh_bookstack"},
}
_BRIEFS = {
    "factory": {
        "zh_cn": "工位出现待核对物料：结合俯视与近景标记证据，将指定机器人派往装配工位或 dock，并记录依据与到达。",
        "en": "Use overhead and close-view marker evidence to dispatch the designated robot to the assembly station or dock.",
    },
    "warehouse_nvd": {
        "zh_cn": "核对一组指定订单物品，将木箱与纸箱集货，再派送同一纸箱到出库区，完成派送后的复核与交接。",
        "en": "Reconcile the specified order items, consolidate a crate and carton, dispatch the same carton, then verify the dock handoff.",
    },
    "modern_house_nvd": {
        "zh_cn": "为住户回家整理房间：移动指定扶手椅、妥善暂存指定书堆，机器人撤至待机区后复核入口到生活区的连续通路。",
        "en": "Reposition the specified armchair, stage the specified books, withdraw the robots and check the continuous home access path.",
    },
}


def _graph(nodes):
    if (not isinstance(nodes, list) or not nodes
            or any(not isinstance(n, dict) or not isinstance(n.get("id"), str) for n in nodes)):
        raise ValueError("authoring requires named phase nodes")
    by_id = {n["id"]: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("duplicate authoring node")
    pending, ancestors, order = dict(by_id), {}, []
    while pending:
        ready = []
        for name, node in pending.items():
            deps = node.get("prerequisites")
            if (not isinstance(deps, list) or any(not isinstance(d, str) for d in deps)
                    or len(set(deps)) != len(deps)):
                raise ValueError("prerequisites must be distinct node IDs")
            if set(deps) <= ancestors.keys():
                ready.append(name)
        if not ready:
            raise ValueError("authoring graph has a cycle or unknown prerequisite")
        for name in sorted(ready):
            deps = pending.pop(name)["prerequisites"]
            ancestors[name] = set(deps).union(*(ancestors[d] for d in deps))
            order.append(name)
    return by_id, ancestors, order


def validate_authoring_scaffold(scenario):
    """Pure business/causal admission; no assets loaded, geometry or live claims."""
    scene = scenario.get("scene_key")
    if scene not in _PRECEDENCE:
        raise ValueError("scaffold helper requires Warehouse/House; Factory uses its dedicated dispatch compiler")
    review = review_semantic_design(scenario)
    rejected = {"missing_semantic_contract", "invalid_semantic_contract", "duplicate_objective",
                "unmapped_or_misbound_objective", "missing_domain_objectives",
                "navigation_smoke_only", "domain_props_are_primitive_proxies"}
    findings = [row for row in review["findings"] if row["code"] in rejected]
    if findings:
        raise ValueError(f"unreviewed business scaffold: {findings}")
    nodes, ancestors, _ = _graph(scenario["phase_graph"]["nodes"])
    for name, required in _PRECEDENCE[scene].items():
        if not set(required) <= ancestors[name]:
            raise ValueError(f"{name}: missing business causal predecessor")
    rows = scenario["composition"]["entities"]
    entities = {e["id"]: e for e in rows}
    if len(entities) != len(rows):
        raise ValueError("duplicate business object")
    from EAI_assets.scenario_assets import get_declared_asset
    for name, asset_key in _PROP_ROLES[scene].items():
        entity = entities.get(nodes[name].get("object_ref"))
        if entity is None or entity.get("asset_key") != asset_key:
            raise ValueError(f"{name}: object does not match the reviewed business prop role")
        declared = get_declared_asset(asset_key)
        size = entity.get("size_m")
        if (not isinstance(size, list) or len(size) != 3 or declared.size_m is None
                or any(isinstance(x, bool) or not isinstance(x, (float, int))
                       or not math.isfinite(x) or not math.isclose(x, y, rel_tol=0., abs_tol=1e-6)
                       for x, y in zip(size, declared.size_m))):
            raise ValueError(f"{name}: native prop dimensions differ from the audited declaration")
        physics = entity.get("physics") or {}
        mass = physics.get("mass_kg")
        if (physics.get("mode") not in {"dynamic", "kinematic"}
                or isinstance(mass, bool) or not isinstance(mass, (float, int))
                or not math.isfinite(mass) or mass <= 0):
            raise ValueError(f"{name}: movable prop requires a positive declared mass")
        if (declared.suggested_mass_kg is None
                or not math.isclose(mass, declared.suggested_mass_kg, rel_tol=0., abs_tol=1e-6)):
            raise ValueError(f"{name}: mass differs from reviewed prop estimate; new load cases need separate review")
    if scene == "warehouse_nvd" and (
            nodes["priority_carton_stage"]["object_ref"] != nodes["priority_carton_dispatch"]["object_ref"]):
        raise ValueError("dispatch must retain the staged carton identity")
    return {"validated": True, "scope": "business_roles_and_causal_scaffold_only",
            "execution_validated": False}


def build_authoring_witness(scenario, *, active_agent_ids=None, remaining_node_ids=None):
    """Count declared-interface teams and construct a conditional serial witness.

    Remaining nodes must be successor-closed: excluded predecessors are assumed
    completed, not replayed. Active IDs may select a predeclared reserve after a
    hypothetical join. Capability changes require an explicitly revised author
    scenario. This is NOT a lifecycle replay, motion plan or deadline proof.
    """
    validate_authoring_scaffold(scenario)
    from .scene_challenge import build_scene_runtime_contract, _public_robot
    from .participant_runtime import eligible_public_agents
    public, _ = build_scene_runtime_contract(scenario)
    nodes, ancestors, order = _graph(scenario["phase_graph"]["nodes"])
    fleet = {r["id"] for r in scenario["robots"]}
    if len(fleet) != len(scenario["robots"]):
        raise ValueError("duplicate robot ID")
    initial = set((scenario.get("runtime_lifecycle") or {}).get("initial_agent_ids", fleet))
    active = initial if active_agent_ids is None else set(active_agent_ids)
    remaining = set(nodes) if remaining_node_ids is None else set(remaining_node_ids)
    if not active <= fleet or not remaining <= nodes.keys():
        raise ValueError("witness refers to an unknown member or objective")
    if any(ancestors[name] & remaining for name in nodes.keys() - remaining):
        raise ValueError("remaining objectives must be successor-closed")
    # Author-only hypothetical roster. A joined reserve becomes visible to the
    # participant at its event, but must never appear in the initial task packet.
    author_task = deepcopy(public)
    public_ids = {robot["id"] for robot in public["initial_fleet"]}
    author_task["initial_fleet"].extend(
        _public_robot(robot) for robot in scenario["robots"]
        if robot["id"] in active - public_ids
    )
    decisions, witness = [], []
    for name in order:
        if name not in remaining:
            continue
        eligible = sorted(eligible_public_agents(author_task, node_id=name, active_agent_ids=active))
        size = 2 if nodes[name]["skill_key"] == "joint_transport" else 1
        teams = [list(team) for team in combinations(eligible, size)]
        decisions.append({"node_id": name, "team_size": size, "candidate_teams": teams,
                          "candidate_count": len(teams), "has_allocation_choice": len(teams) > 1})
        if teams:
            witness.append({"node_id": name, "agents": teams[0],
                            "resources": list(nodes[name].get("resources") or [])})
    feasible = all(d["candidate_count"] > 0 for d in decisions)
    return {"schema": "eai.benchmark.authoring_witness", "version": 1,
            "visibility": "author_evaluator_only", "scenario_sha256": canonical_sha256(scenario),
            "scope": "declared_interfaces_and_serial_resource_exclusion",
            "active_agent_ids": sorted(active), "remaining_node_ids": sorted(remaining),
            "declaration_feasible": feasible, "decisions": decisions,
            "assignment_combination_count": math.prod(d["candidate_count"] for d in decisions),
            "choice_node_ids": [d["node_id"] for d in decisions if d["has_allocation_choice"]],
            "serial_witness": witness if feasible else None,
            "empty_candidate_node_ids": [d["node_id"] for d in decisions if not d["candidate_count"]],
            "execution_validated": False, "physical_team_necessity": "unknown",
            "assumptions": ["excluded objectives already completed", "each primitive can finish before its deadline",
                            "serial execution is allowed", "no further membership/capability change during this snapshot"],
            "not_checked": ["candidate-specific routes", "sensor identity accuracy", "timing and energy",
                            "payload/grasp/stair dynamics", "in-flight ownership and recovery"]}


def _json(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if json.loads(encoded) != value:
        raise ValueError("author experiment inputs must be JSON data")
    return encoded


def validate_authoring_factor_pair(control, variant, *, factor_id):
    """Compare actual supplied design inputs, not a caller's equal hash claims.

    Only protocol factor pairs can freeze the whole scene here. Scene-changing
    axes need a separate geometry-aware comparison. This checks experimental
    input isolation, not whether a scheduled treatment actually took effect.
    All inputs and the returned hashes are author/evaluator-only.
    """
    fields = {"scenario", "observation_inputs", "budgets", "scoring", "reserve_pool",
              "factor_levels", "interventions"}
    for row in (control, variant):
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("factor comparison requires complete experiment inputs")
        _json(row)
        if (any(not isinstance(row[key], dict) or not row[key]
                for key in ("scenario", "budgets", "scoring"))
                or not isinstance(row["observation_inputs"], list)
                or not isinstance(row["reserve_pool"], list)):
            raise ValueError("factor comparison requires concrete scenario, observation, budget and scoring inputs")
    scene = control["scenario"]["scene_key"]
    axes = {a["id"]: a for a in build_public_factor_manifest(scene)["axes"]}
    if factor_id not in {"communication_quality", "team_continuity"}:
        raise ValueError("scene-changing factor requires its own reviewed comparison validator")
    for row in (control, variant):
        if (not isinstance(row["factor_levels"], dict) or set(row["factor_levels"]) != set(axes)
                or any(level not in axes[name]["levels"] for name, level in row["factor_levels"].items())):
            raise ValueError("factor levels must cover the reviewed scene axes")
        if (not isinstance(row["interventions"], dict)
                or set(row["interventions"]) != {"communication_quality", "team_continuity"}):
            raise ValueError("both protocol intervention plans must be explicit")
        if any(not isinstance(events, list) or any(not isinstance(event, dict) for event in events)
               for events in row["interventions"].values()):
            raise ValueError("protocol intervention plans must be lists of event records")
    changed = [name for name in axes if control["factor_levels"][name] != variant["factor_levels"][name]]
    if changed != [factor_id]:
        raise ValueError("comparison must change exactly the named factor")
    for field in fields - {"factor_levels", "interventions"}:
        if _json(control[field]) != _json(variant[field]):
            raise ValueError(f"non-target input changed: {field}")
    for name in control["interventions"]:
        same = _json(control["interventions"][name]) == _json(variant["interventions"][name])
        if name == factor_id and same:
            raise ValueError("factor labels changed without a changed intervention plan")
        if name != factor_id and not same:
            raise ValueError(f"non-target intervention changed: {name}")
    return {"validated": True, "scope": "supplied_design_input_isolation_only",
            "factor_id": factor_id, "visibility": "author_evaluator_only",
            "control_sha256": canonical_sha256(control), "variant_sha256": canonical_sha256(variant),
            "factor_effect_validated": False, "execution_validated": False,
            "remaining_gates": ["level-to-treatment semantics", "intervention replay and observability",
                                "post-intervention feasibility or declared infeasibility track"]}


def _public_catalog(value):
    """Keep the model-facing packet free of repository implementation paths."""
    if isinstance(value, dict):
        return {key: _public_catalog(item) for key, item in value.items() if key != "source"}
    if isinstance(value, list):
        return [_public_catalog(item) for item in value]
    return value


def build_authoring_contract(scene_key):
    catalog = build_semantic_design_catalog(scene_key)
    factory = scene_key == "factory"
    support = _public_catalog(deepcopy(catalog["implementation_support"]))
    support["lifecycle"]["member_exit"]["scope"] = (
        "Conditional runtime code for preloaded ground-member replacement; Warehouse/House require "
        "the participant boundary and a pre-event control-deactivation readback when a live "
        "environment binding is present. An unrelated aerial observer may remain present. Dynamic join "
        "is not claimed; preloaded reserve parking, activation and capability reconfiguration use "
        "the MultiRobotDirectEnv callback/readback binding. Scene event plans and physical reference "
        "episodes remain required. This is code support, not live evidence.")
    artifacts = deepcopy(catalog["artifacts"])
    for artifact in artifacts:
        artifact["declaration_scope"] = "catalog_entry_not_instantiation_or_closure_evidence"
        if artifact["asset_key"] == "wooden_crate_a":
            artifact["meaning"] = "order crate assigned to joint kinematic transfer; physical team necessity unverified"
        elif artifact["asset_key"] == "mh_bookstack":
            artifact["meaning"] = "specified household books to stage; autonomous search is not evaluated"
    return {
        "schema": "eai.benchmark.task_authoring", "version": 2,
        "scene_key": scene_key, "task_family": catalog["task_family"],
        "mission_brief": deepcopy(_BRIEFS[scene_key]),
        "evaluation_track": "evidence_based_dispatch" if factory else "fixed_business_dag_allocation",
        "autonomous_decomposition_evaluated": False,
        "output_schema": "EAI.scenario_spec.schema.scenario_schema",
        "required_objective_roles": deepcopy(catalog["objective_contracts"]),
        "scene_artifacts": artifacts,
        "factor_inventory": _public_catalog(deepcopy(catalog["factors"])),
        "factor_manifest": build_public_factor_manifest(scene_key),
        "implementation_support": support,
        "participant_decides": (
            ["consume available evidence, wait, and choose assembly_site or dock_site for route_confirmation"]
            if factory else ["allocate/reassign eligible members to the supplied DAG",
                             "select a permitted public navigation target; transport destination stays fixed"]),
        "fixed_by_author": ["business decomposition and prerequisite structure", "object-to-business-role bindings",
                            "target regions, acceptance thresholds and evaluation budget",
                            "available action scope: vocabulary alone does not enable an action"],
        "forbidden_claims": (
            ["production restart", "hazard isolation", "autonomous task decomposition"] if factory else
            ["autonomous task decomposition", "physical payload/team necessity",
             "priority-order scheduling" if scene_key == "warehouse_nvd" else "autonomous search for an unknown item",
             "pre-dispatch dock authorization" if scene_key == "warehouse_nvd" else "general resident safety"]),
        "machine_generation_constraints": {
            "causal_predecessors": deepcopy(_PRECEDENCE.get(scene_key, {})),
            "object_role_asset_bindings": deepcopy(_PROP_ROLES.get(scene_key, {})),
            "dimensions": "must equal audited native prop declaration; no shrink-to-fit",
            "mass": "freeze the reviewed estimate; it is not a robot payload rating",
            "carton_identity": "staging and dispatch retain the same object",
            "scaffold_validator": "validate_authoring_scaffold" if not factory else "dedicated Factory dispatch compiler",
            "witness_helper": "build_authoring_witness" if not factory else "dedicated Factory allocation QA",
            "candidate_count_scope": "eligible declared-interface teams per node; neither routes nor physical coalitions",
            "allocation_choice_claim": "requires at least one node with two distinct candidate teams",
            "single_candidate_interpretation": "compliance/coordination only; no allocation-choice difficulty at that node",
        },
        "intervention_design": {
            "business_reasons": {
                "factory": ["inspection shift handover", "metal cell interrupts a camera report"],
                "warehouse_nvd": ["carrier goes to charge", "rack blocks a dispatch message"],
                "modern_house_nvd": ["service robot returns to its dock", "room partition delays a review message"],
            }[scene_key],
            "rules": ["freeze reserve pool, budget and schedules before model evaluation",
                      "join does not become visible or eligible before its event",
                      "exit/capability loss requires explicit in-flight release and recovery evidence",
                      "recount teams for unfinished nodes after every intervention",
                      "zero candidates means infeasible under the declaration; never count as a model failure to solve",
                      "hold publication payload, capture time and TTL fixed in a communication comparison",
                      "pair input isolation is not proof the factor changed any available decision"],
            "pair_validator": "validate_authoring_factor_pair",
            "live_claim": "not established by authoring validation",
        },
        "may_vary": [
            "robot selection within the declared catalog and attachment compatibility",
            "object and viewpoint placement after footprint, route and 3D clearance admission",
            "timeouts fixed before execution and shared by paired comparison conditions",
            "registered native props consistent with the business role and measured dimensions",
        ],
        "must_preserve": [
            "business objective IDs, skills and target roles bound by the reviewed domain evaluator",
            "causal order: valid observation and consumption before the informed action",
            "actual object identity, ownership, delivery and final access evidence",
            "native prop scale, conservative footprint, estimated mass and physical-claim limits",
            "a feasible qualified team throughout each required operation; unknown payload ratings remain unknown",
        ],
        "requires_separate_execution_adapter": [
            "scene reserve parking/event instances and physical lifecycle reference episodes",
            "rack/room identity detectors and domain service observations",
            "autonomous order or household-item selection instead of a fixed business DAG",
        ],
        "participant_receives": [
            "public_task.json: scene, visible fleet/capabilities, objectives, dependencies, targets and constraints",
            "participant_api.json: enabled actions and visible event envelopes",
            "delivered observations and lifecycle events as they occur",
        ],
        "evaluator_retains": [
            "object identity bindings and private domain contract",
            "future intervention times, reserve pool, channel conditions and reference decisions",
            "asset/provenance hashes, measured trajectory and ownership/service ledgers",
        ],
        "comparison_rules": [
            "change one declared factor at a time with geometry, fleet budget and observation content held fixed",
            "report business success, protocol correctness, recovery and motion-baseline failure separately",
            "reserve development episodes for debugging; freeze held-out layouts, identities and schedules before model evaluation",
            "do not infer team necessity from an imposed two-agent node without a single-agent feasibility or capacity argument",
        ],
        "admission_function": ("demo.factory_benchmark.challenge.compile_challenge" if factory else
                               "demo.benchmark.authoring.validate_authored_business_scenario"),
        "execution_validated": False,
    }


def build_public_task_skeleton(scene_key):
    """Project the reviewed authoring scaffold into a participant-safe recipe.

    A public task is an instantiated episode.  This smaller projection gives a
    task-writing model enough scene-native roles and factor vocabulary to
    regenerate a task family, while leaving implementation paths, private
    schedules, object identities and reference decisions in the author record.
    It is descriptive input only; it does not certify a generated episode.
    """
    contract = build_authoring_contract(scene_key)
    objective_roles = {}
    for identifier, role in contract["required_objective_roles"].items():
        objective_roles[identifier] = {
            key: deepcopy(role[key])
            for key in ("id", "skill", "target_ref", "role", "purpose", "deliverable",
                        "acceptance_evidence", "allowed_alternatives")
            if key in role
        }
    factors = []
    for factor in contract["factor_inventory"]:
        factors.append({key: deepcopy(factor[key]) for key in ("id", "levels", "evidence")
                        if key in factor})
    artifacts = []
    for artifact in contract["scene_artifacts"]:
        artifacts.append({key: deepcopy(artifact[key])
                          for key in ("id", "asset_key", "meaning", "declaration_scope")
                          if key in artifact})
    constraints = contract["machine_generation_constraints"]
    factor_axes = {axis["id"]: axis for axis in contract["factor_manifest"]["axes"]}
    return {
        "schema": "eai.benchmark.public_task_skeleton",
        "version": 1,
        "scene_key": scene_key,
        "task_family": contract["task_family"],
        "mission_brief": deepcopy(contract["mission_brief"]),
        "output_schema": contract["output_schema"],
        "required_objective_roles": objective_roles,
        "causal_predecessors": deepcopy(constraints["causal_predecessors"]),
        "scene_artifacts": artifacts,
        "object_role_asset_bindings": deepcopy(constraints["object_role_asset_bindings"]),
        "factor_inventory": factors,
        "lifecycle": {
            "events": ["member_join", "member_exit", "capability_change"],
            "join_policy": "join becomes visible only after the declared runtime event and eligibility check",
            "exit_policy": "unfinished work requires explicit release and reassignment evidence",
            "capability_change_policy": "recount eligible teams after a versioned capability event",
            "future_schedule_visibility": "hidden",
        },
        "communication": {
            "conditions": deepcopy(factor_axes["communication_quality"]["levels"]),
            "participant_observes": ["delivered_messages", "message_age", "message_sequence", "team_events"],
            "future_delivery_schedule_visibility": "hidden",
        },
        "participant_decides": deepcopy(contract["participant_decides"]),
        "fixed_by_author": deepcopy(contract["fixed_by_author"]),
        "generation_constraints": {
            key: deepcopy(constraints[key]) for key in (
                "dimensions", "mass", "carton_identity", "candidate_count_scope",
                "allocation_choice_claim", "single_candidate_interpretation")
            if key in constraints
        },
        "forbidden_claims": deepcopy(contract["forbidden_claims"]),
        "autonomous_decomposition_evaluated": False,
        "execution_validated": False,
        "visibility": "participant_public_regeneration_scaffold",
    }


def validate_authored_business_scenario(scenario, *, evaluation_track="fixed_business_dag_allocation",
                                      require_allocation_choice=False):
    """Admit a fixed scaffold, conditional witness and existing geometry gates.

    ``require_allocation_choice`` is an author quality gate, not a new task
    objective. Changing prose or passing a different track cannot promote the
    fixed graph to autonomous business decomposition. No helper in this module
    marks a release ready or supplies missing live domain evidence.
    """
    from .scene_challenge import audit_generated_scene_scenario
    if scenario.get("scene_key") == "factory":
        raise ValueError("Factory business publication requires its dedicated F-INS dispatch compiler; production recovery is not implemented")
    if evaluation_track != "fixed_business_dag_allocation":
        raise ValueError("fixed DAG does not evaluate autonomous decomposition")
    if type(require_allocation_choice) is not bool:
        raise ValueError("require_allocation_choice must be boolean")
    nodes = (scenario.get("phase_graph") or {}).get("nodes") or []
    if (not scenario.get("semantic_task") and not (scenario.get("composition") or {}).get("entities")
            and nodes and all(node.get("skill_key") in {"navigate", "wait"} for node in nodes)):
        raise ValueError("navigation_smoke is a runtime smoke input and cannot be published as a business package")
    witness = build_authoring_witness(scenario)
    if not witness["declaration_feasible"]:
        raise ValueError(f"no declared feasible team: {witness['empty_candidate_node_ids']}")
    if require_allocation_choice and not witness["choice_node_ids"]:
        raise ValueError("allocation-choice claim requires at least two candidate teams at a node")
    from .domain_evaluator import build_domain_contract
    contract = build_domain_contract(scenario)
    if not contract["required"]:
        raise ValueError("business scaffold lacks mandatory domain acceptance")
    return {"validated": True, "scope": "author_scaffold_and_geometric_admission",
            "evaluation_track": evaluation_track, "authoring_witness": witness,
            "generation_admission": audit_generated_scene_scenario(scenario),
            "domain_scope": contract["scope"], "execution_validated": False, "release_ready": False}


def validate_generated_business_scenario(scenario):
    """Run the authoring-only gate shared by LLM candidates and compilers.

    Geometry, connectivity and execution checks remain separate gates.  This
    function closes the earlier gap where a candidate could enter the
    generation pool after generic checks and then fail the final NVD compiler
    because its business roles or causal scaffold were invalid.
    """
    scene_key = scenario.get("scene_key")
    if scene_key not in {"warehouse_nvd", "modern_house_nvd"}:
        raise ValueError("generated business review requires a Warehouse or Modern House scenario")
    scaffold = validate_authoring_scaffold(scenario)
    semantic = review_semantic_design(scenario)
    # A live detector/service is an execution dependency owned by another
    # layer. It must be reported, but it is not a task-authoring failure.
    blocking = [finding for finding in semantic["findings"]
                if finding.get("severity") == "blocking"
                and finding.get("code") not in {
                    "domain_predicates_pending", "legacy_support_unqualified",
                }]
    if blocking:
        raise ValueError(f"semantic authoring review failed: {blocking}")
    return {
        "validated": True,
        "scope": "business_roles_causal_scaffold_and_semantic_mapping",
        "scene_key": scene_key,
        "scaffold": scaffold,
        "semantic_review": semantic,
        "execution_validated": False,
        "release_ready": False,
    }
