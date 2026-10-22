"""Fail-closed validation for the authored ``prod-p4-01`` catalog mapping.

The mapping is a checked author declaration, not a prose compiler.  This
module accepts one named profile and compares every catalog claim with an
explicitly supported structural projection into the canonical deployment
contract.  Passing the check establishes completeness and source integrity
only; it does not establish that the authored projection is scientifically
correct or that the task executes in the simulator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical_deployment_v3 import Contract, from_mapping
from .oracle_clock_v3 import seconds_to_ticks
from .scorebook_v3 import PAPER_COMMIT
from .task_catalog import TaskAnchor, validate_catalog


SCHEMA = "heterocoord.catalog_contract_mapping.v1"
RECEIPT_SCHEMA = "heterocoord.catalog_contract_mapping_receipt.v1"
PROFILE = "prod-p4-01.catalog-contract.v1"
DEFAULT_ROBOT_RESUME_PATH = Path(
    "/home/pupil/HeteroCoord/benchmark/robot_resume/robot_pool_resume.v1.json"
)
MAX_INPUT_BYTES = 8 * 1024 * 1024

_SOURCE_DIGEST_KEYS = {
    "catalog_sha256",
    "scenario_sha256",
    "contract_sha256",
    "package_sha256",
    "canonical_contract_digest",
    "robot_resume_sha256",
}
_QUALIFICATION_FALSE = {
    "catalog_certified": False,
    "catalog_semantic_equivalence_verified": False,
    "physical_qualified": False,
    "formal_measured_qualified": False,
    "paper_score_eligible": False,
}


def _strict_json(raw: bytes) -> Any:
    def pairs(items):
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def invalid(value):
        raise ValueError(f"nonfinite JSON value: {value}")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON input") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _read(path: str | Path, root: Path) -> bytes:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("mapping inputs must resolve inside the project")
    with resolved.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("mapping input exceeds byte limit")
    return raw


def _exact_keys(value: Any, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} requires exactly {sorted(keys)}")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _claim(
    claim_id: str,
    catalog_field: str,
    catalog_value: Any,
    predicate: str,
    target: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "catalog_field": catalog_field,
        "catalog_value": catalog_value,
        "predicate": predicate,
        "target": dict(target),
    }


def _by_id(items: tuple[Any, ...], attribute: str, kind: str) -> dict[str, Any]:
    result = {getattr(item, attribute): item for item in items}
    if len(result) != len(items):
        raise ValueError(f"contract {kind} identifiers are not unique")
    return result


def _verify_scenario_semantics(
    scenario: Mapping[str, Any],
    contract: Contract,
    anchor: TaskAnchor,
    project_root: Path,
) -> None:
    """Verify static scenario bindings without claiming native qualification."""
    if not isinstance(scenario, Mapping):
        raise ValueError("scenario must be an object")
    expected_source = (project_root / "source").resolve()
    expected_usd = (project_root / "usd").resolve()
    source_root = scenario.get("source_root")
    usd_root = scenario.get("usd_root")
    if (
        not isinstance(source_root, str)
        or not Path(source_root).is_absolute()
        or Path(source_root).resolve() != expected_source
    ):
        raise ValueError("scenario source_root must equal the project source directory")
    if (
        not isinstance(usd_root, str)
        or not Path(usd_root).is_absolute()
        or Path(usd_root).resolve() != expected_usd
    ):
        raise ValueError("scenario usd_root must equal the project usd directory")

    scene_identity = scenario.get("scene_identity")
    if not isinstance(scene_identity, Mapping):
        raise ValueError("scenario scene_identity is required")
    if scene_identity.get("backend") != "project-local-eai":
        raise ValueError("scenario backend must be project-local-eai")
    expected_scene_roots = {
        "eai_root": expected_source / "EAI",
        "assets_root": expected_source / "EAI_assets",
        "hmrs_root": expected_source / "EAI_hmrs",
    }
    for field, expected in expected_scene_roots.items():
        value = scene_identity.get(field)
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or Path(value).resolve() != expected
        ):
            raise ValueError(f"scenario {field} must equal the project-local EAI directory")
    if scene_identity.get("scene_descriptor") != f"{anchor.scene_key}/{anchor.scene_anchor}":
        raise ValueError("scenario scene_descriptor differs from scene identity")

    initial_state = scenario.get("initial_state")
    if not isinstance(initial_state, Mapping):
        raise ValueError("scenario initial_state is required")
    robots = initial_state.get("robots")
    if not isinstance(robots, list) or robots != list(contract.robot_ids):
        raise ValueError("scenario initial_state robots differ from contract robot_ids")
    resources = _by_id(contract.resources, "resource_id", "resource")
    dock = resources.get("dock_slot")
    if dock is None:
        raise ValueError("contract dock_slot resource is required")
    if initial_state.get("dock_capacity") != dock.capacity:
        raise ValueError("scenario dock_capacity differs from contract dock_slot capacity")
    if initial_state.get("dock_slot") != dock.resource_id:
        raise ValueError("scenario dock_slot differs from contract resource id")
    qualification = scenario.get("qualification")
    expected_qualification = {
        "native_scene_loaded": False,
        "physical_skill_qualified": False,
        "common_start_verified": False,
    }
    if qualification != expected_qualification:
        raise ValueError("scenario qualification flags must remain explicitly false")


def _expected_claims(
    anchor: TaskAnchor,
    scenario: Mapping[str, Any],
    contract: Contract,
) -> dict[str, dict[str, Any]]:
    """Build the only supported structural projection for this profile.

    Exact identifiers below are a declared profile, not inferred from prose.
    A new anchor or changed claim requires a new reviewed profile.
    """
    if anchor.anchor_id != "prod-p4-01":
        raise ValueError("mapping profile supports only prod-p4-01")
    if anchor.scene_key != "factory" or anchor.scene_anchor != "shared_dock_queue":
        raise ValueError("unsupported prod-p4-01 scene identity")
    if scenario.get("scene_key") != anchor.scene_key or scenario.get("scene_anchor") != anchor.scene_anchor:
        raise ValueError("scenario scene identity differs from catalog anchor")
    if scenario.get("instance_id") != contract.task_id:
        raise ValueError("scenario instance differs from contract task")

    horizon_ticks = seconds_to_ticks(anchor.horizon_s, rounding="floor")
    if contract.tick_dt_s != 0.02 or contract.horizon != horizon_ticks:
        raise ValueError("contract horizon does not exactly compile the catalog horizon")

    obligations = _by_id(contract.obligations, "obligation_id", "obligation")
    resources = _by_id(contract.resources, "resource_id", "resource")
    edges = _by_id(contract.edges, "edge_id", "edge")
    if set(obligations) != set(anchor.obligations):
        raise ValueError("contract obligations differ from catalog anchor")

    contract_roles = {
        role for obligation in contract.obligations for role in obligation.role_requirements
    }
    contract_capabilities = {
        capability
        for obligation in contract.obligations
        for capability in obligation.required_capabilities
    }
    if contract_roles != set(anchor.roles):
        raise ValueError("contract roles differ from catalog anchor")
    if contract_capabilities != set(anchor.required_capabilities):
        raise ValueError("contract capabilities differ from catalog anchor")

    claims: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any]) -> None:
        claim_id = item["claim_id"]
        if claim_id in claims:
            raise ValueError(f"duplicate expected claim: {claim_id}")
        claims[claim_id] = item

    add(_claim("anchor.anchor_id", "anchor_id", anchor.anchor_id,
               "package_anchor_equals", {"anchor_id": anchor.anchor_id}))
    add(_claim("anchor.scene_key", "scene_key", anchor.scene_key,
               "package_and_scenario_scene_equals", {"scene_key": anchor.scene_key}))
    add(_claim("anchor.scene_anchor", "scene_anchor", anchor.scene_anchor,
               "scenario_scene_anchor_equals", {"scene_anchor": anchor.scene_anchor}))
    add(_claim("anchor.horizon_s", "horizon_s", anchor.horizon_s,
               "exact_tick_horizon", {
                   "tick_dt_s": contract.tick_dt_s,
                   "rounding": "floor",
                   "horizon_ticks": contract.horizon,
               }))

    for obligation_id in anchor.obligations:
        add(_claim(
            f"obligations.{obligation_id}",
            "obligations",
            obligation_id,
            "obligation_present",
            {"obligation_id": obligation_id},
        ))

    for role in anchor.roles:
        obligation_ids = [
            obligation_id
            for obligation_id in anchor.obligations
            if role in obligations[obligation_id].role_requirements
        ]
        add(_claim(
            f"roles.{role}",
            "roles",
            role,
            "exact_obligation_role_occurrences",
            {
                "role": role,
                "obligation_ids": obligation_ids,
            },
        ))

    for capability in anchor.required_capabilities:
        obligation_ids = [
            obligation_id
            for obligation_id in anchor.obligations
            if capability in obligations[obligation_id].required_capabilities
        ]
        add(_claim(
            f"required_capabilities.{capability}",
            "required_capabilities",
            capability,
            "exact_obligation_capability_occurrences",
            {
                "capability": capability,
                "obligation_ids": obligation_ids,
            },
        ))

    milestone_rules: dict[str, tuple[str, dict[str, Any]]] = {
        "assigned": (
            "selected_team_available",
            {
                "team_bounds": {"min": contract.min_team, "max": contract.max_team},
                "robot_ids": list(contract.robot_ids),
            },
        ),
        "reserved": ("obligation_completion", {"obligation_id": "reserve_dock"}),
        "load_a_complete": ("obligation_completion", {"obligation_id": "load_a"}),
        "released": ("obligation_completion", {"obligation_id": "release_dock"}),
        "load_b_complete": ("obligation_completion", {"obligation_id": "load_b"}),
        "accepted": (
            "all_obligations_completion",
            {"obligation_ids": list(anchor.obligations)},
        ),
    }
    if set(anchor.milestones) != set(milestone_rules):
        raise ValueError("unsupported milestone claim set for mapping profile")
    for milestone in anchor.milestones:
        predicate, target = milestone_rules[milestone]
        add(_claim(
            f"milestones.{milestone}", "milestones", milestone, predicate, target
        ))

    if set(anchor.temporal_constraints) != {
        "load_a_and_load_b_cannot_overlap_dock",
        "release_before_load_b",
    }:
        raise ValueError("unsupported temporal claim set for mapping profile")
    mutex_pair = tuple(sorted(("load_a", "load_b")))
    if mutex_pair not in set(contract.mutex_pairs):
        raise ValueError("required load mutex pair is absent")
    dock = resources.get("dock_slot")
    if dock is None or dock.capacity != 1:
        raise ValueError("required unit-capacity dock resource is absent")
    if any(obligations[item].resource_demands.get("dock_slot") != 1 for item in mutex_pair):
        raise ValueError("mutex obligations do not demand the unit-capacity dock")
    add(_claim(
        "temporal_constraints.load_a_and_load_b_cannot_overlap_dock",
        "temporal_constraints",
        "load_a_and_load_b_cannot_overlap_dock",
        "unit_capacity_mutex_pair",
        {"mutex_pair": list(mutex_pair), "resource_id": "dock_slot", "capacity": 1},
    ))
    edge = edges.get("release_before_load_b")
    if (
        edge is None
        or edge.kind != "precedence"
        or edge.before != "release_dock"
        or edge.after != "load_b"
    ):
        raise ValueError("required release-before-load-b edge is absent")
    add(_claim(
        "temporal_constraints.release_before_load_b",
        "temporal_constraints",
        "release_before_load_b",
        "exact_temporal_edge",
        {
            "edge_id": edge.edge_id,
            "kind": edge.kind,
            "before": edge.before,
            "after": edge.after,
            "min_lag": edge.min_lag,
            "max_lag": edge.max_lag,
        },
    ))

    if tuple(anchor.resource_constraints) != ("dock_capacity_one",):
        raise ValueError("unsupported resource claim set for mapping profile")
    add(_claim(
        "resource_constraints.dock_capacity_one",
        "resource_constraints",
        "dock_capacity_one",
        "exact_resource_capacity",
        {"resource_id": "dock_slot", "capacity": dock.capacity},
    ))
    return claims


@dataclass(frozen=True)
class CatalogMappingVerificationV3:
    mapping_sha256: str
    source_digests: Mapping[str, str]
    instance_id: str
    anchor_id: str
    scene_key: str
    claim_counts: Mapping[str, int]

    def receipt(self) -> dict[str, Any]:
        core: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA,
            "paper_commit": PAPER_COMMIT,
            "mapping_profile": PROFILE,
            "mapping_sha256": self.mapping_sha256,
            "source_digests": dict(sorted(self.source_digests.items())),
            "instance_id": self.instance_id,
            "anchor_id": self.anchor_id,
            "scene_key": self.scene_key,
            "claim_counts": dict(sorted(self.claim_counts.items())),
            "verified_claim_count": sum(self.claim_counts.values()),
            "authored_mapping_verified": True,
            **_QUALIFICATION_FALSE,
            "scope": "authored_mapping_structure_and_source_integrity_only",
        }
        core["receipt_sha256"] = _sha256(_canonical_bytes(core))
        return core

    def receipt_bytes(self) -> bytes:
        return _canonical_bytes(self.receipt())


def verify_catalog_mapping(
    mapping_path: str | Path,
    *,
    expected_mapping_sha256: str,
    catalog_path: str | Path,
    scenario_path: str | Path,
    contract_path: str | Path,
    package_path: str | Path,
    robot_resume_path: str | Path | None = None,
    project_root: str | Path = Path(__file__).resolve().parents[1],
) -> CatalogMappingVerificationV3:
    """Verify the frozen authored mapping and return a deterministic receipt.

    ``expected_mapping_sha256`` is an external pin.  Recomputing that pin from
    an untrusted mapping at the call site defeats freshness checking and is not
    appropriate for release use.
    """
    if not _is_sha256(expected_mapping_sha256):
        raise ValueError("external mapping SHA-256 pin is required")
    root = Path(project_root).resolve(strict=True)
    paths = {
        "mapping": mapping_path,
        "catalog": catalog_path,
        "scenario": scenario_path,
        "contract": contract_path,
        "package": package_path,
        "robot_resume": robot_resume_path or DEFAULT_ROBOT_RESUME_PATH,
    }
    raw = {name: _read(path, root) for name, path in paths.items()}
    mapping_sha256 = _sha256(raw["mapping"])
    if mapping_sha256 != expected_mapping_sha256:
        raise ValueError("external mapping hash mismatch")

    mapping = _strict_json(raw["mapping"])
    _exact_keys(mapping, {
        "schema", "mapping_profile", "paper_commit", "instance_id",
        "anchor_id", "scene_key", "source_digests", "claims",
    }, "mapping")
    if mapping["schema"] != SCHEMA or mapping["mapping_profile"] != PROFILE:
        raise ValueError("unsupported mapping schema or profile")
    if mapping["paper_commit"] != PAPER_COMMIT:
        raise ValueError("mapping paper commit is stale")

    source_digests = _exact_keys(
        mapping["source_digests"], _SOURCE_DIGEST_KEYS, "source_digests"
    )
    for key, value in source_digests.items():
        if not _is_sha256(value):
            raise ValueError(f"invalid source digest: {key}")
    for name in ("catalog", "scenario", "contract", "package", "robot_resume"):
        if source_digests[f"{name}_sha256"] != _sha256(raw[name]):
            raise ValueError(f"{name} source digest mismatch")

    catalog_data = _strict_json(raw["catalog"])
    scenario = _strict_json(raw["scenario"])
    contract_data = _strict_json(raw["contract"])
    package = _strict_json(raw["package"])
    if not isinstance(catalog_data, Mapping) or not isinstance(catalog_data.get("anchors"), list):
        raise ValueError("catalog must contain an anchor list")
    if not isinstance(scenario, Mapping):
        raise ValueError("scenario must be an object")
    contract = from_mapping(contract_data)
    if source_digests["canonical_contract_digest"] != contract.digest():
        raise ValueError("canonical contract digest mismatch")

    _exact_keys(package, {
        "schema", "paper_commit", "instance_id", "anchor_id", "scene_key",
        "catalog_sha256", "scenario_sha256", "contract",
    }, "package")
    if package["paper_commit"] != PAPER_COMMIT:
        raise ValueError("package paper commit is stale")
    if package["catalog_sha256"] != source_digests["catalog_sha256"]:
        raise ValueError("package catalog digest differs from mapping")
    if package["scenario_sha256"] != source_digests["scenario_sha256"]:
        raise ValueError("package scenario digest differs from mapping")
    if package["contract"] != contract_data:
        raise ValueError("package contract differs from contract source")

    identity = (mapping["instance_id"], mapping["anchor_id"], mapping["scene_key"])
    if identity != (package["instance_id"], package["anchor_id"], package["scene_key"]):
        raise ValueError("mapping identity differs from package")
    if contract.task_id != mapping["instance_id"]:
        raise ValueError("contract task differs from mapping instance")

    anchors = validate_catalog(TaskAnchor.from_mapping(item) for item in catalog_data["anchors"])
    matches = [anchor for anchor in anchors if anchor.anchor_id == mapping["anchor_id"]]
    if len(matches) != 1:
        raise ValueError("mapping anchor must identify exactly one catalog entry")
    anchor = matches[0]
    if anchor.scene_key != mapping["scene_key"]:
        raise ValueError("mapping scene differs from catalog anchor")
    _verify_scenario_semantics(scenario, contract, anchor, root)

    expected = _expected_claims(anchor, scenario, contract)
    claims = mapping["claims"]
    if not isinstance(claims, list):
        raise ValueError("claims must be a list")
    actual: dict[str, Mapping[str, Any]] = {}
    for claim in claims:
        _exact_keys(
            claim,
            {"claim_id", "catalog_field", "catalog_value", "predicate", "target"},
            "claim",
        )
        claim_id = claim["claim_id"]
        if not isinstance(claim_id, str) or not claim_id:
            raise ValueError("claim_id must be a nonempty string")
        if claim_id in actual:
            raise ValueError(f"duplicate claim: {claim_id}")
        actual[claim_id] = claim
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        raise ValueError(f"claim inventory mismatch; missing={missing}, extra={extra}")
    for claim_id, expected_claim in expected.items():
        if actual[claim_id] != expected_claim:
            raise ValueError(f"unsupported or inconsistent claim binding: {claim_id}")

    counts: dict[str, int] = {}
    for claim in claims:
        field = claim["catalog_field"]
        counts[field] = counts.get(field, 0) + 1
    return CatalogMappingVerificationV3(
        mapping_sha256=mapping_sha256,
        source_digests=dict(source_digests),
        instance_id=mapping["instance_id"],
        anchor_id=mapping["anchor_id"],
        scene_key=mapping["scene_key"],
        claim_counts=counts,
    )


__all__ = [
    "CatalogMappingVerificationV3",
    "PROFILE",
    "RECEIPT_SCHEMA",
    "SCHEMA",
    "verify_catalog_mapping",
]
