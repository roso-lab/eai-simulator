"""Paper-registered task anchors and catalog validation.

The catalog is an authoring contract, not a simulator scene loader.  It keeps
the paper's task identity (domain, relation signature, difficulty and
milestones) separate from USD assets and from the submitted algorithm.  A
catalog entry may therefore be reviewed and oracle-certified before a live
Isaac run exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


class CatalogError(ValueError):
    """Raised when a registered task catalog violates the paper contract."""


DOMAINS = ("production", "service", "living_public")
PRIMITIVES = ("P1", "P2", "P3", "P4")
P3_SUBTYPES = ("P3-J", "P3-S")
DIFFICULTY_DIMENSIONS = (
    "dependency_depth",
    "coordination_width",
    "capability_scarcity",
    "contention",
    "hazard",
    "communication_demand",
    "branching_load",
    "uncertainty",
)
METRIC_DIMENSIONS = tuple(f"D{i}" for i in range(1, 7))
SCENE_KEYS = frozenset(("factory", "factory_benchmark", "warehouse_nvd", "restaurant", "city", "modern_house_nvd"))
SCENE_KEYS = SCENE_KEYS | {"warehouse", "house", "restaurant_svc", "restaurant_prod", "pending_reassignment", "room_06"}
PRIMARY_TIE_BREAK = {"P1": 1, "P4": 2, "P2": 3, "P3": 4}

# Table S2: a common anchor is admissible in these domain/primitive cells;
# unsupported common cells must remain typical-only.
COMMON_CELLS = {
    "production": frozenset(("P1", "P2", "P4")),
    "service": frozenset(("P1", "P2", "P3")),
    "living_public": frozenset(("P2", "P3", "P4")),
}
CATALOG_SCHEMA_VERSION = "paper-task-catalog.v2"
CONTRACT_VERSION = "C-P2-02-quarantine-marked-v2"


def _text(value: Any, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise CatalogError(f"{name} is required")
    return result


def _tuple_text(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or value is None:
        raise CatalogError(f"{name} must be a non-empty sequence")
    result = tuple(_text(item, f"{name} item") for item in value)
    if not result:
        raise CatalogError(f"{name} cannot be empty")
    return result


@dataclass(frozen=True)
class DifficultyVector:
    """Eight generation-time demand bins, each frozen to 0..3.

    The paper's tier is a weakest-link tier: ``1 + max(bin)``.  It is not a
    score produced by EMOS or by any other submitted system.
    """

    dependency_depth: int
    coordination_width: int
    capability_scarcity: int
    contention: int
    hazard: int
    communication_demand: int
    branching_load: int
    uncertainty: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DifficultyVector":
        if not isinstance(value, Mapping):
            raise CatalogError("difficulty_vector must be an object")
        missing = [key for key in DIFFICULTY_DIMENSIONS if key not in value]
        if missing:
            raise CatalogError(f"difficulty_vector missing dimensions: {', '.join(missing)}")
        values: dict[str, int] = {}
        for key in DIFFICULTY_DIMENSIONS:
            raw = value[key]
            if isinstance(raw, bool) or not isinstance(raw, int) or raw not in range(4):
                raise CatalogError(f"{key} must be an integer bin in [0, 3]")
            values[key] = raw
        return cls(**values)

    @property
    def tier(self) -> int:
        return 1 + max(getattr(self, key) for key in DIFFICULTY_DIMENSIONS)

    def to_dict(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in DIFFICULTY_DIMENSIONS}


@dataclass(frozen=True)
class TaskAnchor:
    """One baseline paper scene anchor or an explicitly registered extension.

    ``contract_semantics`` is deliberately independent from ``objective`` and
    ``scene_anchor``.  Common anchors must preserve the former while allowing
    geometry, embodiment and wording to change between domains.
    """

    anchor_id: str
    domain: str
    scene_key: str
    scene_anchor: str
    family: str
    typical_or_common: str
    common_anchor_id: str | None
    primary_label: str
    relation_signature: tuple[str, ...]
    p3_subtype: str | None
    objective: str
    obligations: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    roles: tuple[str, ...]
    milestones: tuple[str, ...]
    temporal_constraints: tuple[str, ...]
    resource_constraints: tuple[str, ...]
    difficulty: DifficultyVector
    horizon_s: float
    factor_support: tuple[str, ...]
    terminal_value: str
    certification_status: str = "planned"
    oracle_status: str = "not_yet_certified"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    obligation_subtypes: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TaskAnchor":
        if not isinstance(value, Mapping):
            raise CatalogError("each catalog entry must be an object")
        relation = tuple(_text(item, "relation_signature item") for item in value.get("relation_signature", ()))
        if not relation:
            raise CatalogError("relation_signature cannot be empty")
        if len(set(relation)) != len(relation) or any(item not in PRIMITIVES for item in relation):
            raise CatalogError(f"invalid relation_signature: {relation!r}")
        p3 = value.get("p3_subtype")
        p3 = None if p3 in (None, "", "null") else _text(p3, "p3_subtype")
        subtypes = value.get("obligation_subtypes", {})
        if not isinstance(subtypes, Mapping) or any(key not in value.get("obligations", ()) or subtype not in P3_SUBTYPES for key, subtype in subtypes.items()):
            raise CatalogError("obligation subtypes must bind registered obligations to P3-J/P3-S")
        if "P3" in relation and p3 not in P3_SUBTYPES and not subtypes:
            raise CatalogError("P3 contracts require obligation subtypes or a legacy P3-J/P3-S label")
        if "P3" not in relation and subtypes:
            raise CatalogError("only P3 obligations have subtypes")
        if "P3" not in relation and p3 is not None:
            raise CatalogError("p3_subtype is only valid for P3 contracts")
        common_kind = _text(value.get("typical_or_common", ""), "typical_or_common")
        if common_kind not in {"typical", "common"}:
            raise CatalogError("typical_or_common must be typical or common")
        common_id = value.get("common_anchor_id")
        common_id = None if common_id in (None, "") else _text(common_id, "common_anchor_id")
        if common_kind == "common" and common_id is None:
            raise CatalogError("common entries require common_anchor_id")
        if common_kind == "typical" and common_id is not None:
            raise CatalogError("typical entries cannot declare common_anchor_id")
        return cls(
            anchor_id=_text(value.get("anchor_id"), "anchor_id"),
            domain=_text(value.get("domain"), "domain"),
            scene_key=_text(value.get("scene_key"), "scene_key"),
            scene_anchor=_text(value.get("scene_anchor"), "scene_anchor"),
            family=_text(value.get("family"), "family"),
            typical_or_common=common_kind,
            common_anchor_id=common_id,
            primary_label=_text(value.get("primary_label"), "primary_label"),
            relation_signature=relation,
            p3_subtype=p3,
            objective=_text(value.get("objective"), "objective"),
            obligations=_tuple_text(value.get("obligations", ()), "obligations"),
            obligation_subtypes=dict(subtypes),
            required_capabilities=_tuple_text(value.get("required_capabilities", ()), "required_capabilities"),
            roles=_tuple_text(value.get("roles", ()), "roles"),
            milestones=_tuple_text(value.get("milestones", ()), "milestones"),
            temporal_constraints=_tuple_text(value.get("temporal_constraints", ()), "temporal_constraints"),
            resource_constraints=(() if value.get("resource_constraints") == [] else
                                  _tuple_text(value.get("resource_constraints", ()), "resource_constraints")),
            difficulty=DifficultyVector.from_mapping(value.get("difficulty_vector", {})),
            horizon_s=float(value.get("horizon_s", 0)),
            factor_support=_tuple_text(value.get("factor_support", ()), "factor_support"),
            terminal_value=_text(value.get("terminal_value"), "terminal_value"),
            certification_status=_text(value.get("certification_status", "planned"), "certification_status"),
            oracle_status=_text(value.get("oracle_status", "not_yet_certified"), "oracle_status"),
            metadata=dict(value.get("metadata", {})),
        )

    def __post_init__(self) -> None:
        if self.domain not in DOMAINS:
            raise CatalogError(f"unknown domain: {self.domain}")
        if self.scene_key not in SCENE_KEYS:
            raise CatalogError(f"unknown scene_key alias: {self.scene_key}")
        if self.primary_label not in PRIMITIVES:
            raise CatalogError(f"unknown primary_label: {self.primary_label}")
        if self.primary_label != max(self.relation_signature, key=PRIMARY_TIE_BREAK.__getitem__):
            raise CatalogError(
                f"primary_label {self.primary_label} does not match tie-break for {self.relation_signature}"
            )
        if self.horizon_s <= 0:
            raise CatalogError("horizon_s must be positive")
        if any(item not in METRIC_DIMENSIONS for item in self.factor_support):
            raise CatalogError("factor_support contains an unknown metric dimension")
        if self.terminal_value not in {"material_flow", "accountable_continuity", "access_safety_privacy"}:
            raise CatalogError(f"unknown terminal_value: {self.terminal_value}")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def difficulty_tier(self) -> int:
        return self.difficulty.tier

    @property
    def canonical_signature(self) -> tuple[str, ...]:
        return tuple(sorted(self.relation_signature, key=PRIMITIVES.index))

    def semantic_contract(self) -> dict[str, Any]:
        """Return the fields that must agree within a common-anchor group."""

        return {
            "relation_signature": list(self.canonical_signature),
            "p3_subtype": self.p3_subtype,
            "obligation_subtypes": dict(self.obligation_subtypes),
            "obligations": list(self.obligations),
            "required_capabilities": list(self.required_capabilities),
            "roles": list(self.roles),
            "milestones": list(self.milestones),
            "temporal_constraints": list(self.temporal_constraints),
            "resource_constraints": list(self.resource_constraints),
        }

    def to_protocol_contract(self, *, observation_schema: str = "task.observation.v1") -> Any:
        """Project this authoring record into the runtime's transport contract.

        The import is local so the catalog remains usable without importing
        runtime modules at package import time.  Rich paper metadata stays in
        ``ProtocolContract.metadata``; the runtime contract itself remains
        intentionally small.
        """

        from .protocol import ProtocolContract
        from .oracle_clock_v3 import seconds_to_ticks

        return ProtocolContract(
            contract_id=self.anchor_id,
            version="paper-task-catalog.v1",
            domain=self.domain,
            primary_label=self.primary_label,
            relation_signature=self.canonical_signature,
            observation_schema=observation_schema,
            difficulty=f"L{self.difficulty_tier}",
            p3_subtype=self.p3_subtype,
            obligation_subtypes=self.obligation_subtypes,
            milestones=self.milestones,
            horizon_ticks=seconds_to_ticks(self.horizon_s, rounding="floor"),
            metadata={
                "scene_key": self.scene_key,
                "scene_anchor": self.scene_anchor,
                "family": self.family,
                "common_anchor_id": self.common_anchor_id,
                "typical_or_common": self.typical_or_common,
                "semantic_contract": self.semantic_contract(),
                "difficulty_vector": self.difficulty.to_dict(),
                "factor_support": list(self.factor_support),
                "terminal_value": self.terminal_value,
                "certification_status": self.certification_status,
                "oracle_status": self.oracle_status,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "domain": self.domain,
            "scene_key": self.scene_key,
            "scene_anchor": self.scene_anchor,
            "family": self.family,
            "typical_or_common": self.typical_or_common,
            "common_anchor_id": self.common_anchor_id,
            "primary_label": self.primary_label,
            "relation_signature": list(self.relation_signature),
            "p3_subtype": self.p3_subtype,
            "obligation_subtypes": dict(self.obligation_subtypes),
            "objective": self.objective,
            "obligations": list(self.obligations),
            "required_capabilities": list(self.required_capabilities),
            "roles": list(self.roles),
            "milestones": list(self.milestones),
            "temporal_constraints": list(self.temporal_constraints),
            "resource_constraints": list(self.resource_constraints),
            "difficulty_vector": self.difficulty.to_dict(),
            "difficulty_tier": self.difficulty_tier,
            "horizon_s": self.horizon_s,
            "factor_support": list(self.factor_support),
            "terminal_value": self.terminal_value,
            "certification_status": self.certification_status,
            "oracle_status": self.oracle_status,
            "metadata": dict(self.metadata),
        }


def validate_catalog(entries: Iterable[TaskAnchor], *, expected_total: int | None = None) -> tuple[TaskAnchor, ...]:
    """Validate the complete 3-domain, Table-S2-constrained catalog."""

    catalog = tuple(entries)
    extensions = tuple(item for item in catalog if item.anchor_id == "liv-t-04")
    if expected_total is None:
        expected_total = 30 + len(extensions)
    if len(catalog) != expected_total:
        raise CatalogError(f"expected {expected_total} anchors, found {len(catalog)}")
    ids = [item.anchor_id for item in catalog]
    if len(set(ids)) != len(ids):
        raise CatalogError("anchor_id values must be unique")
    slots = [(item.domain, item.scene_anchor) for item in catalog]
    if len(set(slots)) != len(slots):
        raise CatalogError("scene_anchor slots must be unique within each domain")
    by_domain = {domain: tuple(item for item in catalog if item.domain == domain) for domain in DOMAINS}
    for domain, items in by_domain.items():
        baseline_items = tuple(item for item in items if item not in extensions)
        if len(baseline_items) != 10:
            raise CatalogError(f"domain {domain} must contain 10 anchors, found {len(items)}")
        if {item.difficulty_tier for item in items} != {1, 2, 3, 4}:
            raise CatalogError(f"domain {domain} must cover all four difficulty tiers")
    groups: dict[str, list[TaskAnchor]] = {}
    for item in catalog:
        if item.typical_or_common == "common":
            assert item.common_anchor_id is not None
            groups.setdefault(item.common_anchor_id, []).append(item)
    if len(groups) != 9:
        raise CatalogError(f"expected 9 common-anchor groups, found {len(groups)}")
    for common_id, items in groups.items():
        if len(items) < 2:
            raise CatalogError(f"common anchor {common_id} needs at least two domain instances")
        fingerprints = {json.dumps(item.semantic_contract(), sort_keys=True, separators=(",", ":")) for item in items}
        if len(fingerprints) != 1:
            raise CatalogError(f"common anchor {common_id} changes its obligation/capability contract")
        if len({item.primary_label for item in items}) != 1:
            raise CatalogError(f"common anchor {common_id} changes its primary label")
        for item in items:
            if item.primary_label not in COMMON_CELLS[item.domain]:
                raise CatalogError(f"common anchor {common_id} is in unsupported cell {item.domain}/{item.primary_label}")
    for domain, allowed_common in COMMON_CELLS.items():
        for primitive in PRIMITIVES:
            cell = [item for item in by_domain[domain] if primitive in item.relation_signature]
            if not cell:
                raise CatalogError(f"{domain}/{primitive} has no relation-bearing anchor")
            common = [item for item in cell if item.typical_or_common == "common"]
            if bool(common) != (primitive in allowed_common):
                expected = "common" if primitive in allowed_common else "typical-only"
                raise CatalogError(f"{domain}/{primitive} must be {expected}")
    for domain, items in by_domain.items():
        if not any(item.typical_or_common == "typical" for item in items):
            raise CatalogError(f"{domain} needs typical anchors")
    return catalog


def load_catalog(path: str | Path) -> tuple[TaskAnchor, ...]:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise CatalogError("unsupported catalog schema_version")
    if data.get("contract_version") != CONTRACT_VERSION:
        raise CatalogError("unsupported catalog contract_version")
    raw_entries = data.get("anchors") if isinstance(data, Mapping) else None
    if not isinstance(raw_entries, list):
        raise CatalogError("catalog JSON must contain an anchors list")
    # Include top-level version identity in every canonical entry so changing
    # the contract cannot silently preserve the old catalog digest.
    entries = []
    for item in raw_entries:
        enriched = dict(item)
        metadata = dict(enriched.get("metadata", {}))
        metadata.update({"catalog_schema_version": data["schema_version"],
                         "contract_version": data["contract_version"]})
        enriched["metadata"] = metadata
        entries.append(TaskAnchor.from_mapping(enriched))
    return validate_catalog(tuple(entries))


def default_catalog_path() -> Path:
    return Path(__file__).with_name("task_catalog.json")


def load_default_catalog() -> tuple[TaskAnchor, ...]:
    return load_catalog(default_catalog_path())


def catalog_digest(entries: Iterable[TaskAnchor]) -> str:
    payload = json.dumps([item.to_dict() for item in entries], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def catalog_report(entries: Iterable[TaskAnchor]) -> dict[str, Any]:
    """Return a release-audit summary without interpreting outcomes."""

    catalog = tuple(entries)
    validate_catalog(catalog)
    common = [item for item in catalog if item.typical_or_common == "common"]
    cells = {
        f"{domain}/{primitive}": {
            "anchors": sum(primitive in item.relation_signature and item.domain == domain for item in catalog),
            "common": sum(primitive in item.relation_signature and item.domain == domain and item.typical_or_common == "common" for item in catalog),
            "typical": sum(primitive in item.relation_signature and item.domain == domain and item.typical_or_common == "typical" for item in catalog),
        }
        for domain in DOMAINS
        for primitive in PRIMITIVES
    }
    return {
        "anchor_count": len(catalog),
        "domain_counts": {domain: sum(item.domain == domain for item in catalog) for domain in DOMAINS},
        "common_anchor_groups": sorted({item.common_anchor_id for item in common if item.common_anchor_id}),
        "common_certification": {
            status: sum(item.typical_or_common == "common" and item.certification_status == status for item in catalog)
            for status in sorted({item.certification_status for item in common})
        },
        "cells": cells,
        "difficulty_tiers": {domain: sorted({item.difficulty_tier for item in catalog if item.domain == domain}) for domain in DOMAINS},
        "catalog_digest": catalog_digest(catalog),
    }


__all__ = [
    "COMMON_CELLS",
    "CatalogError",
    "DIFFICULTY_DIMENSIONS",
    "DifficultyVector",
    "DOMAINS",
    "METRIC_DIMENSIONS",
    "PRIMITIVES",
    "P3_SUBTYPES",
    "SCENE_KEYS",
    "TaskAnchor",
    "catalog_digest",
    "catalog_report",
    "default_catalog_path",
    "load_catalog",
    "load_default_catalog",
    "validate_catalog",
]
