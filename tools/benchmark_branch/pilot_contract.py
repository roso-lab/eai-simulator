"""Formal, non-certified contract projection for pilot task candidates."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .oracle_predicates import (
    ALL_BOUND_PREDICATES,
    DRAFT_BINDING_REGISTRY,
    _DRAFT_DEADLINE_IDS,
    _DRAFT_DEADLINE_TERMINALS,
    _DRAFT_HORIZON_TICKS,
    _DRAFT_IDENTITY_SPECS,
    _DRAFT_CONTACT_IDS,
    _DRAFT_WINDOW_IDS,
    _DRAFT_PRECEDENCE_IDS,
    _DRAFT_PREDICATES,
    _DRAFT_RESOURCE_IDS,
    _DRAFT_ZONE_IDS,
)

PILOT_CONTRACT_SCHEMA = "heterocoord-pilot-contract.v1"
PENDING = "pending"
DRAFT_PENDING_VALIDATION = "draft_pending_validation"
ORACLE_BOUND_PENDING_REPLAY = "bound_pending_replay"
MILESTONE_POLICY_VERSION = "milestone-policy.v2"
ORACLE_SOURCE_COMMITS = ("cf839ec8", "0b6890c0")

_ORACLE_BINDINGS = {
    "lot_a_and_lot_b_may_run_in_parallel": {"event_pair": {"start": "lot_first_pickup", "end": "lot_last_scan", "subjects": ["lot_a", "lot_b"]}},
    "reconcile_after_both_picks": {"event_pair": {"before": "both_lots_picked", "after": "count_reconcile_start"}, "thresholds": {"deadline_ticks": 500}},
    "one_scan_station_per_lot": {"zone_id": "scan_station", "capacity": 1},
    "mark_after_inspection": {"event_pair": {"before": "inspection_complete", "after": "quarantine_marked"}},
    "accept_after_release": {"event_pair": {"before": "released", "after": "accepted"}},
    "one_quarantine_bay": {"zone_id": "quarantine_bay", "capacity": 1},
    "carriers_start_within_sync_window": {"event_pair": {"left": "arrive_in_position:left_carrier", "right": "arrive_in_position:right_carrier"}, "thresholds": {"window_ticks": 250}},
    "release_after_joint_carry": {"event_pair": {"before": "joint_carry_complete", "after": "released"}},
    "pallet_requires_two_carriers": {"event_pair": {"start": "max(arrive_in_position:left_carrier,arrive_in_position:right_carrier)", "end": "joint_carry_complete"}, "object_id": "pallet", "minimum_contacts": 2},
    "coverage_has_no_uncovered_window": {"event_pair": {"start": "coverage_started", "end": "accepted"}, "zone_id": "aisle", "thresholds": {"max_gap_ticks": 50}},
    "scan_point_use_is_mutex": {"zone_id": "scan_point", "capacity": 1},
    "scan_point_capacity_one": {"zone_id": "scan_point", "capacity": 1},
    "requests_cannot_overlap_counter": {"zone_id": "service_counter", "capacity": 1},
    "release_between_requests": {"event_pair": {"before": "request_a.exit", "after": "request_b.enter"}, "zone_id": "service_counter"},
    "counter_capacity_one": {"zone_id": "service_counter", "capacity": 1},
    "load_a_and_load_b_cannot_overlap_dock": {"zone_id": "dock", "capacity": 1},
    "release_before_load_b": {"event_pair": {"before": "loader_a.exit", "after": "loader_b.enter"}, "zone_id": "dock"},
    "dock_capacity_one": {"zone_id": "dock", "capacity": 1},
}


_ORACLE_PREDICATES = {
    "prod-p1-01": ("lot_a_and_lot_b_may_run_in_parallel", "reconcile_after_both_picks", "one_scan_station_per_lot"),
    "svc-p2-02": ("mark_after_inspection", "accept_after_release", "one_quarantine_bay"),
    "liv-p3-01": ("carriers_start_within_sync_window", "release_after_joint_carry", "accept_after_release", "pallet_requires_two_carriers"),
    "prod-t-02": ("coverage_has_no_uncovered_window", "scan_point_use_is_mutex", "scan_point_capacity_one"),
    "svc-t-01": ("requests_cannot_overlap_counter", "release_between_requests", "counter_capacity_one"),
    "liv-p4-01": ("load_a_and_load_b_cannot_overlap_dock", "release_before_load_b", "dock_capacity_one"),
}

_ORACLE_THRESHOLDS = {
    "reconcile_after_both_picks": {"deadline_ticks": 500},
    "carriers_start_within_sync_window": {"window_ticks": 250},
    "coverage_has_no_uncovered_window": {"max_gap_ticks": 50},
    "pallet_requires_two_carriers": {"minimum_contacts": 2},
}

# The 24 author-approved draft bindings are imported from the executable
# oracle registry so contract generation and judgment cannot silently drift.
_ORACLE_PREDICATES = {
    "prod-p1-01": ("lot_a_and_lot_b_may_run_in_parallel", "reconcile_after_both_picks", "one_scan_station_per_lot"),
    "svc-p2-02": ("mark_after_inspection", "accept_after_release", "one_quarantine_bay"),
    "liv-p3-01": ("carriers_start_within_sync_window", "release_after_joint_carry", "accept_after_release", "pallet_requires_two_carriers"),
    "prod-t-02": ("coverage_has_no_uncovered_window", "scan_point_use_is_mutex", "scan_point_capacity_one"),
    "svc-t-01": ("requests_cannot_overlap_counter", "release_between_requests", "counter_capacity_one"),
    "liv-p4-01": ("load_a_and_load_b_cannot_overlap_dock", "release_before_load_b", "dock_capacity_one"),
    **_DRAFT_PREDICATES,
}

_DRAFT_OPERATOR_RESOURCE_IDS = set(_DRAFT_RESOURCE_IDS)
_DRAFT_OPERATOR_DEADLINE_IDS = set(_DRAFT_DEADLINE_IDS)
_DRAFT_OPERATOR_IDENTITY_IDS = set(_DRAFT_IDENTITY_SPECS)

# A few author labels do not contain lexical hints such as ``before`` or
# ``capacity_one``.  Keep their categories explicit rather than downgrading
# them to an unresolved predicate in the generated contract.
_DRAFT_OPERATOR_OVERRIDES = {
    "one_quarantine_bay": "non_overlapping",
    "pallet_requires_two_carriers": "contact_count",
    "release_between_scans": "precedence",
}
_DRAFT_OPERATOR_CONTACT_IDS = set(_DRAFT_CONTACT_IDS)
_DRAFT_OPERATOR_WINDOW_IDS = set(_DRAFT_WINDOW_IDS)
_DRAFT_OPERATOR_PRECEDENCE_IDS = set(_DRAFT_PRECEDENCE_IDS)

for _anchor_id, _predicate_ids in _DRAFT_PREDICATES.items():
    for _predicate_id in _predicate_ids:
        if (_anchor_id, _predicate_id) not in DRAFT_BINDING_REGISTRY:
            raise RuntimeError(f"missing draft binding registry row: {(_anchor_id, _predicate_id)!r}")


def _milestone_policy(labels: list[Any]) -> dict[str, Any]:
    """Register the paper's seven-field milestone scoring contract."""
    return {
        "schema_version": MILESTONE_POLICY_VERSION,
        "activation_denominator": "frozen_at_contract_activation",
        "weights": {"W": 1, "S": 1, "I": 0, "R": 0, "T": 0},
        "tau_stable_ticks": 25,
        "necessity": {"W": "required", "S": "required", "T": "required",
                      "I": "optional", "R": "optional"},
        "monotonicity": {"W": True, "S": False, "I": False, "R": False, "T": False},
        "visibility": {"W": "public", "I": "public", "S": "private",
                       "R": "private", "T": "private"},
        "duplicate_rule": "first_valid_attainment_only",
        "registered_labels": list(labels),
    }


def _lock_service_contract() -> dict[str, Any]:
    return {
        "schema_version": "typed-lock-service.v1",
        "system_visible_interface": ["request", "grant", "deny", "release"],
        "evaluator_private_position_ledger": True,
        "p4_violation_predicates": ["action_without_grant", "occupancy_over_capacity"],
        "request_and_retry_count_as_occupancy": False,
    }


class PilotContractError(ValueError):
    pass


@dataclass(frozen=True)
class PilotContract:
    anchor_id: str
    paper_sha: str
    contract: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.contract)


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PilotContractError(f"{path} must be a non-empty string")
    return value


def _items(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise PilotContractError(f"{path} must be a non-empty list")
    return list(value)


def _predicate(label: str, kind: str, *, bound_operator: str | None = None) -> dict[str, Any]:
    """Create a conservative formalization draft for a catalog phrase.

    The expression is an auditable placeholder for an oracle author; it is not
    evaluated here and therefore cannot certify a case.
    """
    normalized = label.replace("_", " ")
    if bound_operator is not None:
        operator = bound_operator
    elif label in _DRAFT_OPERATOR_DEADLINE_IDS:
        operator = "deadline"
    elif label in _DRAFT_OPERATOR_IDENTITY_IDS:
        operator = "identity_consistency"
    elif label in _DRAFT_OPERATOR_OVERRIDES:
        operator = _DRAFT_OPERATOR_OVERRIDES[label]
    elif label in _DRAFT_OPERATOR_CONTACT_IDS:
        operator = "contact_count"
    elif label in _DRAFT_OPERATOR_WINDOW_IDS:
        operator = "within_window"
    elif label in _DRAFT_OPERATOR_PRECEDENCE_IDS:
        operator = "precedence"
    elif "cannot overlap" in normalized or "mutex" in normalized or "capacity one" in normalized or label in _DRAFT_OPERATOR_RESOURCE_IDS:
        operator = "non_overlapping"
    elif "after" in normalized or "before" in normalized:
        operator = "precedence"
    elif "parallel" in normalized:
        operator = "may_overlap"
    elif "continuous" in normalized or "no uncovered" in normalized:
        operator = "continuous_coverage"
    elif "within" in normalized or "sync window" in normalized:
        operator = "within_window"
    else:
        operator = "unresolved_catalog_predicate"
    if operator == "deadline":
        inputs = ["milestone_ledger"]
        units = {"time": "s", "tick": "integer"}
        window = {"lower_s": 0.0, "upper_s": None, "source": "registered_anchor_horizon"}
    elif operator == "identity_consistency":
        inputs = ["identity_ledger"]
        units = {"identity": "opaque_evaluator_key"}
        window = {"lower_s": None, "upper_s": None, "source": "registered_identity_mapping_pending"}
    elif operator == "precedence":
        inputs = ["milestone_ledger"]
        units = {"time": "s", "tick": "integer"}
        window = {"lower_s": 0.0, "upper_s": None, "source": "anchor_horizon_pending"}
    elif operator == "non_overlapping":
        inputs = ["resource_ledger", "milestone_ledger"]
        units = {"time": "s", "resource_count": "integer"}
        window = {"lower_s": 0.0, "upper_s": None, "source": "resource_capacity_pending"}
    elif operator == "continuous_coverage":
        inputs = ["observation", "milestone_ledger"]
        units = {"time": "s", "coverage_gap": "s"}
        window = {"lower_s": 0.0, "upper_s": 0.0, "source": "registered_gap_tolerance_pending"}
    elif operator == "within_window":
        inputs = ["milestone_ledger", "observation"]
        units = {"time": "s", "tick": "integer"}
        window = {"lower_s": 0.0, "upper_s": None, "source": "sync_window_pending"}
    elif operator == "contact_count":
        inputs = ["hold_ledger", "milestone_ledger"]
        units = {"time": "s", "tick": "integer", "holder_count": "integer"}
        window = {"lower_s": 0.0, "upper_s": None, "source": "registered_support_window"}
    else:
        inputs = ["milestone_ledger", "resource_ledger", "observation"]
        units = {"time": "s"}
        window = {"lower_s": None, "upper_s": None, "source": "semantic_binding_pending"}
    binding_spec = {
        "precedence": {"event_pair": None, "required_quantities": ["t_before", "t_after"]},
        "non_overlapping": {"event_pair": None, "required_quantities": ["interval_start", "interval_end", "resource_id"]},
        "continuous_coverage": {"event_pair": None, "required_quantities": ["coverage_gap_s", "horizon_s"]},
        "within_window": {"event_pair": None, "required_quantities": ["t_start", "t_end", "window_s"]},
        "deadline": {"event_pair": None, "required_quantities": ["terminal_tick", "deadline_tick"]},
        "identity_consistency": {"event_pair": None, "required_quantities": ["actual_identity", "expected_identity"]},
        "contact_count": {"event_pair": None, "required_quantities": ["window_start", "window_end", "holder_count"]},
        "may_overlap": {"event_pair": None, "required_quantities": ["interval_start", "interval_end"]},
            "unresolved_catalog_predicate": {"event_pair": None, "required_quantities": [],},
    }[operator]
    return {"id": label, "kind": kind, "operator": operator,
            "formal_expression": f"UNBOUND({kind}, {label})",
            "input_fields": inputs, "units": units, "time_window": window,
            "binding_spec": binding_spec,
            "unbound_parameters": ["event_pair", "thresholds", "identity_mapping"],
            "binding_status": DRAFT_PENDING_VALIDATION,
            "status": DRAFT_PENDING_VALIDATION}


def _bind_oracle_predicate(predicate: dict[str, Any], anchor_id: str) -> dict[str, Any]:
    """Attach the executable evaluator predicate without certifying the case."""
    bound = dict(predicate)
    bound_id = predicate["id"]
    spec = (
        DRAFT_BINDING_REGISTRY[(anchor_id, bound_id)]
        if anchor_id in _DRAFT_PREDICATES
        else _ORACLE_BINDINGS[bound_id]
    )
    identity_pending = anchor_id in _DRAFT_PREDICATES
    bound.update({
        "oracle_predicate_id": bound_id,
        "oracle_module": "tools.benchmark_branch.oracle_predicates",
        "formal_expression": f"ORACLE({anchor_id}, {bound_id})",
        "binding_status": ORACLE_BOUND_PENDING_REPLAY,
        "status": ORACLE_BOUND_PENDING_REPLAY,
        "unbound_parameters": ["identity_mapping"] if identity_pending else [],
        "oracle_thresholds": _oracle_thresholds(spec, bound_id, include_pilot_defaults=anchor_id not in _DRAFT_PREDICATES),
        "requires_replay_receipt": True,
    })
    bound["binding_spec"] = dict(bound["binding_spec"], event_pair=_binding_event_pair(spec),
                                  oracle_binding=dict(spec))
    # Preserve the released six-pilot projection exactly.  The draft registry
    # may classify the same English label more precisely (e.g. contact_count
    # or non_overlapping), but that refinement must not rewrite the pilot
    # contract artifact in place.
    if anchor_id not in _DRAFT_PREDICATES and bound_id in {
        "one_scan_station_per_lot", "one_quarantine_bay", "pallet_requires_two_carriers",
    }:
        bound.update({
            "operator": "unresolved_catalog_predicate",
            "input_fields": ["milestone_ledger", "resource_ledger", "observation"],
            "units": {"time": "s"},
            "time_window": {"lower_s": None, "upper_s": None, "source": "semantic_binding_pending"},
        })
        if bound_id in {"one_scan_station_per_lot", "one_quarantine_bay"}:
            bound["binding_spec"]["required_quantities"] = []
        elif bound_id == "pallet_requires_two_carriers":
            bound["binding_spec"]["required_quantities"] = ["carry_window_start", "carry_window_end", "robot_id"]
    if spec.get("event_pair") and not bound["binding_spec"].get("required_quantities"):
        bound["binding_spec"]["required_quantities"] = ["carry_window_start", "carry_window_end", "robot_id"]
    zone_ids = list(spec.get("zone_ids", []))
    resource_name = spec.get("zone_id") or (zone_ids[0] if zone_ids else None) or spec.get("object_id")
    resolved_source_ledger = (
        spec.get("source_ledger")
        or ("evaluator_private_zone_intervals" if spec.get("zone_id") else None)
        or ("evaluator_private_hold_intervals" if spec.get("object_id") else None)
    )
    candidate_updates = {
        "resource_id_candidate": resource_name,
        "capacity_constraint_candidate": spec.get("capacity"),
        "interval_source": resolved_source_ledger,
    }
    if resolved_source_ledger == "evaluator_private_hold_intervals":
        bound["observation_quantity"] = "distinct_joint_holders"
        bound["observation_source"] = "held_object_or_joint_skill_session"
        bound["object_identity_scope"] = "task_level_id"
    if anchor_id in _DRAFT_PREDICATES:
        binding_kinds = {
            "precedence": "event_precedence",
            "non_overlapping": "zone_interval_ledger",
            "may_overlap": "diagnostic_interval_pair",
            "continuous_coverage": "coverage_interval_ledger",
            "within_window": "event_window",
            "contact_count": "hold_interval_ledger",
            "deadline": "terminal_milestone_deadline",
            "identity_consistency": "identity_receipt",
        }
        candidate_updates.update(
            binding_kind=binding_kinds[spec["operator"]],
            required_fields=list(spec["required_fields"]),
            source="author_approved_pair_registry",
            status="executable",
            requires_oracle_review=False,
        )
        bound["time_window"] = dict(
            bound["time_window"], source="author_approved_pair_registry",
        )
        bound["outcome_semantics"] = dict(spec["outcome_semantics"])
    bound["binding_candidates"] = dict(bound["binding_candidates"], **candidate_updates)
    if anchor_id in _DRAFT_PREDICATES:
        bound["event_pair_candidate"] = {
            "status": "bound", "source": "author_approved_pair_registry",
            "binding": _binding_event_pair(spec), "requires_oracle_review": False,
        }
    source_ledger = resolved_source_ledger
    group = spec.get("dedupe_group")
    if source_ledger in {"evaluator_private_zone_intervals", "evaluator_private_hold_intervals"}:
        source_name = resource_name
        if group is None:
            group = f"{anchor_id}:occupancy:{source_name}" if source_ledger == "evaluator_private_zone_intervals" else f"{anchor_id}:contact:{source_name}"
        bound.update({
            "source_ledger": source_ledger,
            "dedupe_group": group,
            "dedupe_key": f"{group}:{bound_id}",
            "counting_policy": "one_consumer_per_source_group",
        })
    if anchor_id in _DRAFT_PREDICATES:
        bound["identity_mapping"] = {
            "status": PENDING,
            "scenario_entity_ids": [],
            "candidate_key": spec.get("identity_key") or resource_name,
            "source": "semantic_map_and_evaluator_receipt",
        }
    return bound


def _binding_event_pair(spec: Mapping[str, Any]) -> Any:
    if "event_pair" in spec:
        return spec["event_pair"]
    if "before_events" in spec:
        return {"before": spec["before_events"], "after": spec["after_event"]}
    if "event_pairs" in spec:
        return spec["event_pairs"]
    if "window" in spec:
        return spec["window"]
    if spec.get("mode") == "release_enter":
        return {"before": f"{spec['before_holder']}.exit", "after": f"{spec['after_holder']}.enter"}
    return None


def _oracle_thresholds(
    spec: Mapping[str, Any],
    predicate_id: str,
    *,
    include_pilot_defaults: bool = True,
) -> dict[str, Any]:
    result = dict(_ORACLE_THRESHOLDS.get(predicate_id, {})) if include_pilot_defaults else {}
    for key in ("deadline_ticks", "window_ticks", "max_gap_ticks", "minimum_contacts", "min_overlap_ticks", "max_gap_ticks"):
        if key in spec:
            result[key] = spec[key]
    if spec.get("operator") == "identity_consistency":
        result["identity_checks"] = list(spec.get("checks", []))
    return result


def _event_pair_candidate(label: str, milestones: list[Any]) -> dict[str, Any]:
    """Suggest milestone labels for a predicate without asserting their semantics."""
    if label and ("bay" in label or "capacity" in label or "station" in label
                  or "pallet" in label or "counter" in label or "dock" in label):
        return {"candidate_labels": [], "source": "resource_interval_requires_position_observation",
                "status": DRAFT_PENDING_VALIDATION, "requires_oracle_review": True}
    words = label.split("_")
    hits = [m for m in milestones if isinstance(m, str) and any(w in m for w in words if len(w) > 2)]
    return {"candidate_labels": hits[:4], "source": "milestone_token_overlap",
            "status": DRAFT_PENDING_VALIDATION, "requires_oracle_review": True}


def _binding_candidates(label: str, kind: str, operator: str) -> dict[str, Any]:
    """Expose non-milestone binding seams without claiming they are bound.

    Resource capacity and interval mutex predicates are not reliably identified
    by milestone-name token overlap.  Keep those candidates explicit so an
    evaluator/oracle can bind them later, while preserving the non-certified
    contract boundary.
    """
    if kind == "resource":
        return {
            "binding_kind": "resource_ledger",
            "resource_id_candidate": None,
            "capacity_constraint_candidate": None,
            "required_fields": ["resource_id", "capacity", "occupancy_interval"],
            "source": "resource_constraint_semantics",
            "status": DRAFT_PENDING_VALIDATION,
            "requires_oracle_review": True,
        }
    if operator == "non_overlapping":
        return {
            "binding_kind": "interval_ledger",
            "interval_source": None,
            "required_fields": ["interval_start", "interval_end", "identity_key"],
            "source": "temporal_mutex_semantics",
            "status": DRAFT_PENDING_VALIDATION,
            "requires_oracle_review": True,
        }
    return {
        "binding_kind": "not_applicable_pending_review",
        "required_fields": [],
        "source": "predicate_kind_review",
        "status": DRAFT_PENDING_VALIDATION,
        "requires_oracle_review": True,
    }


def _role_obligation_binding(roles: list[Any], obligations: list[Any]) -> dict[str, Any]:
    """Keep role-to-obligation correspondence explicit but unbound.

    Names alone do not establish who performs an obligation; the mapping must
    come from a case author/evaluator receipt rather than a lexical guess.
    """
    return {
        "candidate_pairs": [],
        "available_roles": list(roles),
        "available_obligations": list(obligations),
        "mapping_source": "case_author_or_evaluator_receipt",
        "status": DRAFT_PENDING_VALIDATION,
        "requires_case_author_review": True,
    }


def _skill_contract(capabilities: list[Any]) -> dict[str, Any]:
    return {
        "preconditions": [], "postconditions": [], "failure_rules": [],
        "required_capabilities": list(capabilities),
        "skill_binding": None,
        "status": DRAFT_PENDING_VALIDATION,
        "requires_skill_validation": True,
    }


def build_pilot_contract(candidate: Mapping[str, Any], anchor: Mapping[str, Any], paper_sha: str) -> PilotContract:
    cid = _text(candidate.get("anchor_id"), "candidate.anchor_id")
    aid = _text(anchor.get("anchor_id"), "anchor.anchor_id")
    if cid != aid:
        raise PilotContractError(f"candidate/catalog anchor mismatch: {cid!r} != {aid!r}")
    if candidate.get("domain") != anchor.get("domain"):
        raise PilotContractError(f"{cid}: domain mismatch")
    if candidate.get("relation") not in (anchor.get("relation_signature") or []):
        raise PilotContractError(f"{cid}: relation is not in relation_signature")
    if candidate.get("p3_subtype") != anchor.get("p3_subtype"):
        raise PilotContractError(f"{cid}: P3 subtype mismatch")
    obligations = _items(anchor.get("obligations"), f"{cid}.obligations")
    roles = _items(anchor.get("roles"), f"{cid}.roles")
    milestones = _items(anchor.get("milestones"), f"{cid}.milestones")
    capabilities = _items(anchor.get("required_capabilities"), f"{cid}.required_capabilities")
    temporal = _items(anchor.get("temporal_constraints"), f"{cid}.temporal_constraints")
    resources = ([] if anchor.get("resource_constraints") == [] else
                 _items(anchor.get("resource_constraints"), f"{cid}.resource_constraints"))
    # The six pilot anchors continue to derive their labels from task_catalog.
    # For the 24 author-approved drafts, use the draft predicate list as the
    # source of truth; catalog metadata still supplies identity/roles/
    # milestones and is checked above.
    if cid in _DRAFT_PREDICATES:
        labels = list(_DRAFT_PREDICATES[cid])
        def _draft_kind(label: str) -> str:
            return "resource" if label in _DRAFT_OPERATOR_RESOURCE_IDS else "temporal"
        formal_predicates = []
        for label in labels:
            kind = _draft_kind(label)
            spec = DRAFT_BINDING_REGISTRY[(cid, label)]
            draft = _predicate(label, kind, bound_operator=spec["operator"])
            formal_predicates.append(dict(
                draft,
                event_pair_candidate=_event_pair_candidate(label, milestones),
                binding_candidates=_binding_candidates(label, kind, draft["operator"]),
            ))
    else:
        formal_predicates = [dict(_predicate(x, "temporal"),
                                  event_pair_candidate=_event_pair_candidate(x, milestones),
                                  binding_candidates=_binding_candidates(x, "temporal", _predicate(x, "temporal")["operator"]))
                             for x in temporal]
        formal_predicates += [dict(_predicate(x, "resource"),
                                   event_pair_candidate=_event_pair_candidate(x, milestones),
                                   binding_candidates=_binding_candidates(x, "resource", _predicate(x, "resource")["operator"]))
                              for x in resources]
    oracle_ids = set(_ORACLE_PREDICATES.get(cid, ()))
    formal_predicates = [_bind_oracle_predicate(p, cid) if p["id"] in oracle_ids else p for p in formal_predicates]
    contract = {
        "schema_version": PILOT_CONTRACT_SCHEMA, "status": "candidate_contract_only",
        "anchor_id": cid, "paper_sha": _text(paper_sha, "paper_sha"),
        "identity": {"domain": anchor.get("domain"), "scene_key": anchor.get("scene_key"),
                      "scene_anchor": anchor.get("scene_anchor"), "relation": candidate.get("relation"),
                      "p3_subtype": candidate.get("p3_subtype"),
                      "relation_signature": list(anchor.get("relation_signature") or [])},
        "obligation_contract": {"roles": roles, "obligations": obligations,
                                 "role_obligation_edges": [], "edge_status": PENDING,
                                 "role_obligation_binding": _role_obligation_binding(roles, obligations)},
        "milestone_contract": {"ordered_labels": milestones, "predicate_definitions": [],
                                "predicate_status": PENDING,
                                "evaluation_policy": _milestone_policy(milestones)},
        "capability_contract": {"required_capabilities": capabilities, "robot_resume_refs": [],
                                 "evidence_refs": [], "evidence_status": PENDING},
        "resource_contract": {"tick_dt_s": 0.02, "temporal_constraints": temporal,
                               "resource_constraints": resources,
                               "lock_service": _lock_service_contract(),
                               "formal_predicates": formal_predicates,
                               "predicate_status": (ORACLE_BOUND_PENDING_REPLAY
                                                    if cid in _DRAFT_PREDICATES
                                                    else DRAFT_PENDING_VALIDATION)},
        "success_conditions": {
            "safety_class": cid in {"prod-t-01", "svc-p3-01", "liv-p3-01", "liv-t-03", "prod-t-03", "liv-t-04"},
            "safety_predicates": ([
                "pallet_requires_two_carriers" if cid in {"prod-t-01", "svc-p3-01", "liv-p3-01"} else
                "room_entry_is_mutex" if cid == "liv-t-03" else
                "staging_zone_capacity_one" if cid == "prod-t-03" else ""
            ] if cid in {"prod-t-01", "svc-p3-01", "liv-p3-01", "liv-t-03", "prod-t-03"} else ["heavy_carry_joint_hold"] if cid == "liv-t-04" else []),
            "failure_semantics": "task_unsuccessful_without_experiment_abort" if cid in {"prod-t-01", "svc-p3-01", "liv-p3-01", "liv-t-03", "prod-t-03", "liv-t-04"} else "obligation_score_only",
        },
        "skill_contract": _skill_contract(capabilities),
        "physical_feasibility_witness": {"witness_refs": [], "status": PENDING},
        "oracle_contract": {"schema_version": "heterocoord-oracle-input.v1",
                             "input_fields": ["observation", "milestone_ledger", "resource_ledger", "history_records"],
                             "oracle_ref": "tools.benchmark_branch.oracle_predicates:judge_anchor",
                             "binding_status": ORACLE_BOUND_PENDING_REPLAY,
                             "status": anchor.get("oracle_status", "not_yet_certified"),
                             "requires_evaluator_receipt": True},
        "certification": {"certification_status": anchor.get("certification_status"),
                           "case_certified": False, "paper_score_eligible": False},
    }
    return PilotContract(cid, paper_sha, contract)


def build_pilot_contract_set(candidate_doc: Mapping[str, Any], catalog_doc: Mapping[str, Any]) -> dict[str, Any]:
    paper_sha = _text(candidate_doc.get("paper_sha"), "candidate_doc.paper_sha")
    anchors = {a.get("anchor_id"): a for a in catalog_doc.get("anchors", [])}
    candidates = candidate_doc.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise PilotContractError("candidate_doc.candidates must be a non-empty list")
    contracts = []
    for candidate in candidates:
        anchor = anchors.get(candidate.get("anchor_id"))
        if anchor is None:
            raise PilotContractError(f"candidate not found in catalog: {candidate.get('anchor_id')!r}")
        contracts.append(build_pilot_contract(candidate, anchor, paper_sha).as_dict())
    return {"schema_version": "heterocoord-pilot-contract-set.v1", "status": "candidate_contract_only",
            "paper_sha": paper_sha, "source_catalog": candidate_doc.get("source_catalog"),
            "oracle_source_commits": list(ORACLE_SOURCE_COMMITS),
            "candidate_count": len(contracts), "contracts": contracts,
            "certification_note": "No contract certifies physical feasibility, oracle validity, or paper eligibility."}


def build_author_approved_draft_contract_set(
    catalog_doc: Mapping[str, Any],
    *,
    paper_sha: str,
    source_catalog: str,
) -> dict[str, Any]:
    """Build the 24-anchor contract projection from the author registry.

    The draft predicate registry is the machine-readable projection of the
    signed 2026-10-24 decision sheet.  Catalog metadata remains authoritative
    for domain, roles, milestones and scene identity; no scene-map IDs are
    invented here.  This helper deliberately excludes the six historical
    pilots so their release artifact remains immutable.
    """
    anchors = {a.get("anchor_id"): a for a in catalog_doc.get("anchors", [])}
    missing = sorted(set(_DRAFT_PREDICATES) - set(anchors))
    if missing:
        raise PilotContractError(f"draft anchors missing from catalog: {missing}")
    candidates = []
    for anchor_id in _DRAFT_PREDICATES:
        anchor = anchors[anchor_id]
        relation_signature = anchor.get("relation_signature") or []
        if not relation_signature:
            raise PilotContractError(f"{anchor_id}: relation_signature is empty")
        candidates.append({
            "anchor_id": anchor_id,
            "domain": anchor.get("domain"),
            "relation": relation_signature[0],
            "p3_subtype": anchor.get("p3_subtype"),
            "reason": "author-approved draft projection including room_06 extension",
        })
    candidate_doc = {
        "schema_version": "heterocoord-pilot-candidate-set.v1",
        "status": "candidate_only",
        "selection_rule": "author-approved non-pilot anchor_drafts including liv-t-04; six pilots excluded from this artifact",
        "source_catalog": source_catalog,
        "source_catalog_status": "all source anchors remain planned/not_yet_certified",
        "paper_sha": paper_sha,
        "candidates": candidates,
    }
    result = build_pilot_contract_set(candidate_doc, catalog_doc)
    result["draft_file_count"] = len(_DRAFT_PREDICATES)
    result["draft_predicate_count"] = sum(len(items) for items in _DRAFT_PREDICATES.values())
    operator_counts: dict[str, int] = {}
    for spec in DRAFT_BINDING_REGISTRY.values():
        operator = str(spec["operator"])
        operator_counts[operator] = operator_counts.get(operator, 0) + 1
    result["formal_operator_counts"] = dict(sorted(operator_counts.items()))
    result["safety_class_anchors"] = [
        # Include the already-bound pilot member so this top-level index is
        # the full catalog safety set, while the artifact contracts themselves
        # remain scoped to the 24 new drafts.
        "prod-t-01", "prod-t-03", "svc-p3-01", "liv-p3-01", "liv-t-03", "liv-t-04",
    ]
    return result


def validate_pilot_contract_set(document: Mapping[str, Any]) -> None:
    if document.get("schema_version") != "heterocoord-pilot-contract-set.v1":
        raise PilotContractError("unsupported contract-set schema")
    if document.get("status") != "candidate_contract_only":
        raise PilotContractError("contract set must remain candidate_contract_only")
    contracts = document.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise PilotContractError("contracts must be a non-empty list")
    ids = [c.get("anchor_id") for c in contracts]
    if len(ids) != len(set(ids)):
        raise PilotContractError("anchor_id values must be unique")
    if document.get("candidate_count") != len(contracts):
        raise PilotContractError("candidate_count does not match contracts")
    if document.get("oracle_source_commits") != list(ORACLE_SOURCE_COMMITS):
        raise PilotContractError("oracle source provenance mismatch")
    for contract in contracts:
        if contract.get("paper_sha") != document.get("paper_sha"):
            raise PilotContractError(f"{contract.get('anchor_id')}: paper SHA mismatch")
        if contract.get("status") != "candidate_contract_only":
            raise PilotContractError(f"{contract.get('anchor_id')}: unexpected status")
        cert = contract.get("certification", {})
        if cert.get("case_certified") or cert.get("paper_score_eligible"):
            raise PilotContractError(f"{contract.get('anchor_id')}: premature certification")
        ob = contract.get("obligation_contract", {}).get("role_obligation_binding", {})
        if ob.get("candidate_pairs") != [] or ob.get("status") != DRAFT_PENDING_VALIDATION:
            raise PilotContractError(f"{contract.get('anchor_id')}: role-obligation binding must remain pending")
        skill = contract.get("skill_contract", {})
        if skill.get("skill_binding") is not None or skill.get("status") != DRAFT_PENDING_VALIDATION:
            raise PilotContractError(f"{contract.get('anchor_id')}: skill binding must remain pending")
        oracle = contract.get("oracle_contract", {})
        if oracle.get("schema_version") != "heterocoord-oracle-input.v1":
            raise PilotContractError(f"{contract.get('anchor_id')}: unsupported oracle input schema")
        if oracle.get("binding_status") not in {DRAFT_PENDING_VALIDATION, ORACLE_BOUND_PENDING_REPLAY} or not oracle.get("requires_evaluator_receipt"):
            raise PilotContractError(f"{contract.get('anchor_id')}: invalid oracle binding status")
        if oracle.get("binding_status") == ORACLE_BOUND_PENDING_REPLAY and not oracle.get("oracle_ref"):
            raise PilotContractError(f"{contract.get('anchor_id')}: bound oracle requires oracle_ref")
        policy = contract.get("milestone_contract", {}).get("evaluation_policy", {})
        if (policy.get("schema_version") != MILESTONE_POLICY_VERSION
                or policy.get("activation_denominator") != "frozen_at_contract_activation"
                or policy.get("weights") != {"W": 1, "S": 1, "I": 0, "R": 0, "T": 0}
                or policy.get("tau_stable_ticks") != 25
                or policy.get("duplicate_rule") != "first_valid_attainment_only"):
            raise PilotContractError(f"{contract.get('anchor_id')}: milestone policy drift")
        lock = contract.get("resource_contract", {}).get("lock_service", {})
        if (lock.get("schema_version") != "typed-lock-service.v1"
                or lock.get("system_visible_interface") != ["request", "grant", "deny", "release"]
                or lock.get("evaluator_private_position_ledger") is not True
                or lock.get("p4_violation_predicates") != ["action_without_grant", "occupancy_over_capacity"]
                or lock.get("request_and_retry_count_as_occupancy") is not False):
            raise PilotContractError(f"{contract.get('anchor_id')}: typed lock policy drift")
        predicates = contract.get("resource_contract", {}).get("formal_predicates", [])
        if (contract.get("anchor_id") in _DRAFT_PREDICATES
                and contract.get("resource_contract", {}).get("predicate_status") != ORACLE_BOUND_PENDING_REPLAY):
            raise PilotContractError(f"{contract.get('anchor_id')}: draft predicate status is stale")
        by_id = {p.get("id"): p for p in predicates}
        expected_oracle_ids = set(_ORACLE_PREDICATES.get(contract.get("anchor_id"), ()))
        if not expected_oracle_ids.issubset(by_id):
            missing = sorted(expected_oracle_ids - set(by_id))
            raise PilotContractError(f"{contract.get('anchor_id')}: missing oracle predicates {missing}")
        for pid in expected_oracle_ids:
            if by_id[pid].get("binding_status") != ORACLE_BOUND_PENDING_REPLAY:
                raise PilotContractError(f"{contract.get('anchor_id')}: oracle predicate {pid} is not bound_pending_replay")
        for predicate in predicates:
            if predicate.get("binding_status") != ORACLE_BOUND_PENDING_REPLAY:
                continue
            pid = predicate.get("oracle_predicate_id")
            anchor_id = contract.get("anchor_id")
            spec = (
                DRAFT_BINDING_REGISTRY.get((anchor_id, pid))
                if anchor_id in _DRAFT_PREDICATES
                else _ORACLE_BINDINGS.get(pid)
            )
            if spec is None or predicate.get("binding_spec", {}).get("event_pair") != _binding_event_pair(spec):
                raise PilotContractError(f"{contract.get('anchor_id')}: oracle event binding drift for {pid}")
            if predicate.get("binding_spec", {}).get("oracle_binding") != spec:
                raise PilotContractError(f"{contract.get('anchor_id')}: oracle binding spec drift for {pid}")
            if anchor_id in _DRAFT_PREDICATES:
                if predicate.get("operator") != spec.get("operator"):
                    raise PilotContractError(f"{anchor_id}: oracle operator drift for {pid}")
                identity_mapping = predicate.get("identity_mapping", {})
                if (predicate.get("unbound_parameters") != ["identity_mapping"]
                        or identity_mapping.get("status") != PENDING
                        or identity_mapping.get("scenario_entity_ids") != []
                        or identity_mapping.get("source") != "semantic_map_and_evaluator_receipt"):
                    raise PilotContractError(f"{anchor_id}: identity mapping boundary drift for {pid}")
                if predicate.get("outcome_semantics") != spec.get("outcome_semantics"):
                    raise PilotContractError(f"{anchor_id}: outcome semantics drift for {pid}")
            expected_thresholds = _oracle_thresholds(
                spec, pid, include_pilot_defaults=anchor_id not in _DRAFT_PREDICATES,
            )
            if predicate.get("oracle_thresholds") != expected_thresholds:
                raise PilotContractError(f"{contract.get('anchor_id')}: oracle thresholds drift for {pid}")
            if predicate.get("oracle_module") != "tools.benchmark_branch.oracle_predicates":
                raise PilotContractError(f"{contract.get('anchor_id')}: oracle module drift for {pid}")
            zone_ids = list(spec.get("zone_ids", []))
            source_name = spec.get("zone_id") or (zone_ids[0] if zone_ids else None) or spec.get("object_id")
            expected_source = (
                spec.get("source_ledger")
                or ("evaluator_private_zone_intervals" if spec.get("zone_id") else None)
                or ("evaluator_private_hold_intervals" if spec.get("object_id") else None)
            )
            if expected_source in {"evaluator_private_zone_intervals", "evaluator_private_hold_intervals"}:
                expected_group = spec.get("dedupe_group") or (
                    f"{anchor_id}:occupancy:{source_name}"
                    if expected_source == "evaluator_private_zone_intervals"
                    else f"{anchor_id}:contact:{source_name}"
                )
                if (predicate.get("source_ledger") != expected_source
                        or predicate.get("dedupe_group") != expected_group
                        or predicate.get("dedupe_key") != f"{expected_group}:{pid}"
                        or predicate.get("counting_policy") != "one_consumer_per_source_group"):
                    raise PilotContractError(f"{contract.get('anchor_id')}: source dedupe metadata drift for {pid}")
            if anchor_id in _DRAFT_PREDICATES:
                candidates = predicate.get("binding_candidates", {})
                if candidates.get("status") != "executable" or candidates.get("requires_oracle_review") is not False:
                    raise PilotContractError(f"{anchor_id}: stale binding candidates for {pid}")
                event_candidate = predicate.get("event_pair_candidate", {})
                if event_candidate.get("status") != "bound" or event_candidate.get("requires_oracle_review") is not False:
                    raise PilotContractError(f"{anchor_id}: stale event-pair candidate for {pid}")
                if (candidates.get("source") != "author_approved_pair_registry"
                        or not candidates.get("required_fields")
                        or "pending" in str(candidates.get("binding_kind", ""))
                        or "pending" in str(candidates.get("source", ""))
                        or predicate.get("time_window", {}).get("source") != "author_approved_pair_registry"):
                    raise PilotContractError(f"{anchor_id}: stale pending binding marker for {pid}")


def validate_pilot_contract(contract: Mapping[str, Any]) -> None:
    """Validate one selected contract with the same rules as a full release.

    A selected contract has no authority merely because a caller recomputed its
    digest.  Wrapping it in the minimum contract-set envelope keeps the exact
    generated binding checks in one place and prevents the runtime seam from
    drifting away from release validation.
    """

    if not isinstance(contract, Mapping):
        raise PilotContractError("selected contract must be an object")
    validate_pilot_contract_set(
        {
            "schema_version": "heterocoord-pilot-contract-set.v1",
            "status": "candidate_contract_only",
            "paper_sha": contract.get("paper_sha"),
            "candidate_count": 1,
            "oracle_source_commits": list(ORACLE_SOURCE_COMMITS),
            "contracts": [contract],
        }
    )
