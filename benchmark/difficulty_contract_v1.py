"""Contract-only difficulty tiers and pre-release validation for paper dac7b94.

No simulator, solver result, condition assignment, or candidate outcome is an
input to this module.  Raw components are recomputed from a content-bound base
contract and the version-1 bounds are frozen below.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import hashlib
import json
import math
from statistics import NormalDist
from typing import Any, Mapping, Sequence

from .scorebook_v3 import PAPER_COMMIT


BASE_CONTRACT_SCHEMA = "heterocoord.difficulty-base-contract.v1"
ASSESSMENT_SCHEMA = "heterocoord.difficulty-assessment.v1"
PILOT_SCHEMA = "heterocoord.difficulty-pilot-sweeps.v1"
FRESH_SCHEMA = "heterocoord.difficulty-fresh-ordering.v1"
CERTIFICATION_SCHEMA = "heterocoord.difficulty-certification.v1"
VALIDATION_REGISTRY_SCHEMA = "heterocoord.difficulty-validation-registry.v1"
EVIDENCE_SCHEMA = "heterocoord.difficulty-evidence.v1"
COMMON_PAIR_REGISTRY_SCHEMA = "heterocoord.common-pair-registry.v1"
COMMON_PAIR_RECEIPT_SCHEMA = "heterocoord.common-pair-receipt.v1"


@dataclass(frozen=True)
class ComponentSpec:
    lower: float
    upper: float
    orientation: int = 1

    def __post_init__(self) -> None:
        if not math.isfinite(self.lower) or not math.isfinite(self.upper) or self.lower >= self.upper:
            raise ValueError("difficulty bounds must be finite and non-degenerate")
        if self.orientation not in {-1, 1}:
            raise ValueError("difficulty orientation must be +1 or -1")


COMPONENT_SPECS: dict[str, ComponentSpec] = {
    "dependency_depth": ComponentSpec(2, 6),
    "coordination_width": ComponentSpec(2, 6),
    "capability_scarcity": ComponentSpec(1, 3),
    "contention": ComponentSpec(2, 6),
    "hazard": ComponentSpec(0, 4),
    "communication_demand": ComponentSpec(0, 4),
    "branching_load": ComponentSpec(0, 6),
    "uncertainty": ComponentSpec(0, 8),
}
COMPONENTS = tuple(COMPONENT_SPECS)
FORMAL_GATES = {
    "physical_qualified": False,
    "rankable": False,
    "paper_score_eligible": False,
}


class DifficultyContractError(ValueError):
    """Raised when contract-only difficulty evidence is malformed or stale."""


def _canonical(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DifficultyContractError("difficulty material must be finite canonical JSON") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_base_contract_hash(contract: Mapping[str, Any]) -> str:
    if not isinstance(contract, Mapping):
        raise DifficultyContractError("base contract must be an object")
    return _canonical({key: value for key, value in contract.items() if key != "base_contract_hash"})


def bind_base_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(contract)
    value.pop("base_contract_hash", None)
    value["base_contract_hash"] = canonical_base_contract_hash(value)
    return value


def _ids(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DifficultyContractError(f"{name} must be a list")
    result = tuple(value)
    if (not allow_empty and not result) or any(not isinstance(item, str) or not item.strip() for item in result):
        raise DifficultyContractError(f"{name} contains invalid IDs")
    if len(set(result)) != len(result):
        raise DifficultyContractError(f"{name} contains duplicate IDs")
    return result


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DifficultyContractError(f"{name} must be an integer >= {minimum}")
    return value


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DifficultyContractError(f"{name} must be finite")
    result = float(value)
    if (positive and result <= 0) or (not positive and result < 0):
        raise DifficultyContractError(f"{name} has an invalid sign")
    return result


def _longest_chain(nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]) -> int:
    outgoing = {node: [] for node in nodes}
    indegree = {node: 0 for node in nodes}
    for before, after in edges:
        outgoing[before].append(after)
        indegree[after] += 1
    queue = sorted(node for node in nodes if indegree[node] == 0)
    depth = {node: 1 for node in nodes}
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for target in outgoing[node]:
            depth[target] = max(depth[target], depth[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
                queue.sort()
    if visited != len(nodes):
        raise DifficultyContractError("precedence graph contains a cycle")
    return max(depth.values())


def _reachable_without_safety(
    start: str,
    target: str,
    adjacency: Mapping[str, set[str]],
    constrained: set[str],
) -> bool:
    if start in constrained or target in constrained:
        return False
    frontier = [start]
    visited = {start}
    while frontier:
        node = frontier.pop(0)
        if node == target:
            return True
        for neighbor in sorted(adjacency[node]):
            if neighbor not in constrained and neighbor not in visited:
                visited.add(neighbor)
                frontier.append(neighbor)
    return False


def _coordination_width(robots: tuple[str, ...], capable: tuple[set[str], ...], minima: tuple[int, ...], team_min: int) -> int:
    target = minima
    states: dict[tuple[int, ...], int] = {tuple(0 for _ in target): 0}
    for robot in robots:
        updated = dict(states)
        for state, count in states.items():
            next_state = tuple(min(target[index], state[index] + int(robot in capable[index])) for index in range(len(target)))
            updated[next_state] = min(updated.get(next_state, len(robots) + 1), count + 1)
        states = updated
    if target not in states:
        raise DifficultyContractError("no robot coalition satisfies obligation minima")
    width = max(team_min, states[target])
    if width > len(robots):
        raise DifficultyContractError("team_min exceeds the robot pool")
    return width


def _capability_scarcity(capable: tuple[set[str], ...], minima: tuple[int, ...]) -> float:
    best = 0.0
    # An optimum S can be reduced to a union of capable sets without lowering
    # the numerator.  Enumerating obligation subsets is therefore exact here.
    for size in range(1, len(capable) + 1):
        for chosen in combinations(range(len(capable)), size):
            subset: set[str] = set().union(*(capable[index] for index in chosen))
            if not subset:
                raise DifficultyContractError("an obligation has no capable robot")
            numerator = sum(minima[index] for index, values in enumerate(capable) if values <= subset)
            best = max(best, numerator / len(subset))
    return best


def _uncertainty(variables: Sequence[Any]) -> float:
    total = 0.0
    seen: set[str] = set()
    for index, row in enumerate(variables):
        if not isinstance(row, Mapping):
            raise DifficultyContractError(f"hidden_variables[{index}] must be an object")
        variable_id = row.get("variable_id")
        if not isinstance(variable_id, str) or not variable_id.strip() or variable_id in seen:
            raise DifficultyContractError("hidden variable IDs must be non-empty and unique")
        seen.add(variable_id)
        if set(row) == {"variable_id", "candidate_set"}:
            candidates = _ids(row["candidate_set"], f"hidden_variables[{index}].candidate_set")
            if len(candidates) < 2:
                raise DifficultyContractError("hidden variable candidate sets must be non-degenerate")
            total += math.log2(len(candidates))
        elif set(row) == {"variable_id", "prior"}:
            prior = row["prior"]
            if not isinstance(prior, Mapping) or len(prior) < 2:
                raise DifficultyContractError("hidden variable prior must have at least two candidates")
            probabilities = [_number(value, "prior probability", positive=True) for value in prior.values()]
            if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-12):
                raise DifficultyContractError("hidden variable prior must sum to one")
            total -= sum(probability * math.log2(probability) for probability in probabilities)
        else:
            raise DifficultyContractError("hidden variable must contain exactly candidate_set or prior")
    return total


def score_component(component: str, raw_value: float, *, specs: Mapping[str, ComponentSpec] = COMPONENT_SPECS) -> dict[str, Any]:
    if component not in specs:
        raise DifficultyContractError(f"unknown difficulty component: {component}")
    spec = specs[component]
    if spec.lower >= spec.upper:
        raise DifficultyContractError(f"degenerate bounds for {component}")
    value = _number(raw_value, component)
    numerator = value - spec.lower if spec.orientation == 1 else spec.upper - value
    q = min(1.0, max(0.0, numerator / (spec.upper - spec.lower)))
    bin_value = min(3, math.floor(4 * q))
    return {"raw": value, "lower": spec.lower, "upper": spec.upper, "orientation": spec.orientation, "q": q, "bin": bin_value}


def evaluate_difficulty_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the eight raw components and tier from one bound base contract."""

    required = {
        "schema", "base_contract_hash", "domain", "primitive", "robots", "team_min", "obligations",
        "precedence_edges", "resource_capacities", "navigation",
        "observation_rights", "milestones", "admissible_decomposition_count",
        "hidden_variables",
    }
    if not isinstance(contract, Mapping) or set(contract) != required:
        raise DifficultyContractError("base contract schema fields are incomplete or contain outcome data")
    if contract.get("schema") != BASE_CONTRACT_SCHEMA:
        raise DifficultyContractError("unsupported difficulty base contract schema")
    expected_hash = canonical_base_contract_hash(contract)
    if contract.get("base_contract_hash") != expected_hash:
        raise DifficultyContractError("base_contract_hash mismatch")
    if contract.get("domain") not in {"production", "service", "living_public"}:
        raise DifficultyContractError("base contract domain is invalid")
    if contract.get("primitive") not in {"P1", "P2", "P3", "P4"}:
        raise DifficultyContractError("base contract primitive is invalid")
    robots = _ids(contract["robots"], "robots")
    robot_set = set(robots)
    team_min = _integer(contract["team_min"], "team_min", minimum=2)
    rows = contract["obligations"]
    if not isinstance(rows, list) or not rows:
        raise DifficultyContractError("at least one obligation is required")
    obligation_ids: list[str] = []
    capable: list[set[str]] = []
    minima: list[int] = []
    resource_demands: list[dict[str, float]] = []
    information_needs: list[tuple[str, ...]] = []
    for index, row in enumerate(rows):
        required_row = {"obligation_id", "capable_robots", "min_coalition", "resource_demands", "information_needs"}
        if not isinstance(row, Mapping) or set(row) != required_row:
            raise DifficultyContractError(f"obligations[{index}] schema mismatch")
        obligation_id = row["obligation_id"]
        if not isinstance(obligation_id, str) or not obligation_id.strip() or obligation_id in obligation_ids:
            raise DifficultyContractError("obligation IDs must be non-empty and unique")
        obligation_ids.append(obligation_id)
        robots_o = set(_ids(row["capable_robots"], f"obligations[{index}].capable_robots"))
        if not robots_o <= robot_set:
            raise DifficultyContractError("capable_robots contains an unknown robot")
        minimum = _integer(row["min_coalition"], f"obligations[{index}].min_coalition", minimum=1)
        if minimum > len(robots_o):
            raise DifficultyContractError("min_coalition exceeds the capable set")
        demands = row["resource_demands"]
        if not isinstance(demands, Mapping):
            raise DifficultyContractError("resource_demands must be an object")
        capable.append(robots_o)
        minima.append(minimum)
        resource_demands.append({str(key): _number(value, "resource demand") for key, value in demands.items()})
        information_needs.append(_ids(row["information_needs"], "information_needs", allow_empty=True))

    edges_value = contract["precedence_edges"]
    if not isinstance(edges_value, list):
        raise DifficultyContractError("precedence_edges must be a list")
    edges: list[tuple[str, str]] = []
    for index, edge in enumerate(edges_value):
        if not isinstance(edge, Mapping) or set(edge) != {"before", "after"}:
            raise DifficultyContractError(f"precedence_edges[{index}] schema mismatch")
        pair = (edge["before"], edge["after"])
        if pair[0] not in obligation_ids or pair[1] not in obligation_ids or pair[0] == pair[1] or pair in edges:
            raise DifficultyContractError("precedence edge is unknown, self-referential, or duplicated")
        edges.append(pair)

    capacities = contract["resource_capacities"]
    if not isinstance(capacities, Mapping):
        raise DifficultyContractError("resource_capacities must be an object")
    capacity_values = {str(key): _number(value, "resource capacity", positive=True) for key, value in capacities.items()}
    if any(set(demands) - set(capacity_values) for demands in resource_demands):
        raise DifficultyContractError("resource demand references an unknown capacity")
    contention_values = [
        math.ceil(sum(demands.get(resource, 0.0) for demands in resource_demands) / capacity)
        for resource, capacity in capacity_values.items()
    ]

    navigation = contract["navigation"]
    navigation_fields = {
        "nodes", "edges", "robot_start_nodes", "obligation_site_nodes",
        "safety_constrained_nodes",
    }
    if not isinstance(navigation, Mapping) or set(navigation) != navigation_fields:
        raise DifficultyContractError("navigation schema mismatch")
    nodes = _ids(navigation["nodes"], "navigation.nodes")
    node_set = set(nodes)
    constrained = set(_ids(navigation["safety_constrained_nodes"], "safety_constrained_nodes", allow_empty=True))
    if not constrained <= node_set:
        raise DifficultyContractError("safety_constrained_nodes contains an unknown node")
    starts = navigation["robot_start_nodes"]
    sites = navigation["obligation_site_nodes"]
    if not isinstance(starts, Mapping) or set(starts) != robot_set:
        raise DifficultyContractError("robot_start_nodes must cover the robot pool")
    if not isinstance(sites, Mapping) or set(sites) != set(obligation_ids):
        raise DifficultyContractError("obligation_site_nodes must cover every obligation")
    if any(node not in node_set for node in starts.values()) or any(node not in node_set for node in sites.values()):
        raise DifficultyContractError("navigation start/site references an unknown node")
    adjacency = {node: set() for node in nodes}
    nav_edges = navigation["edges"]
    if not isinstance(nav_edges, list):
        raise DifficultyContractError("navigation.edges must be a list")
    seen_nav_edges: set[tuple[str, str, bool]] = set()
    for edge in nav_edges:
        if not isinstance(edge, Mapping) or set(edge) != {"from", "to", "bidirectional"} or type(edge["bidirectional"]) is not bool:
            raise DifficultyContractError("navigation edge schema mismatch")
        source, target = edge["from"], edge["to"]
        identity = (source, target, edge["bidirectional"])
        if source not in node_set or target not in node_set or source == target or identity in seen_nav_edges:
            raise DifficultyContractError("navigation edge is unknown, self-referential, or duplicated")
        seen_nav_edges.add(identity)
        adjacency[source].add(target)
        if edge["bidirectional"]:
            adjacency[target].add(source)
    hazard = 0
    for index, obligation_id in enumerate(obligation_ids):
        site = sites[obligation_id]
        reachable = any(
            _reachable_without_safety(starts[robot], site, adjacency, constrained)
            for robot in capable[index]
        )
        hazard += not reachable

    rights = contract["observation_rights"]
    if not isinstance(rights, Mapping):
        raise DifficultyContractError("observation_rights must be an object")
    normalized_rights = {str(item): set(_ids(observers, f"observation_rights[{item}]", allow_empty=True)) for item, observers in rights.items()}
    if any(not observers <= robot_set for observers in normalized_rights.values()):
        raise DifficultyContractError("observation_rights contains an unknown robot")
    communication = 0
    for index, needs in enumerate(information_needs):
        for item in needs:
            if item not in normalized_rights:
                raise DifficultyContractError(f"missing observation rights for information item {item}")
            communication += not bool(capable[index] & normalized_rights[item])

    milestones = contract["milestones"]
    if not isinstance(milestones, list) or not milestones:
        raise DifficultyContractError("milestones must be non-empty")
    decompositions = 1
    seen_milestones: set[str] = set()
    for index, milestone in enumerate(milestones):
        if not isinstance(milestone, Mapping) or set(milestone) != {"milestone_id", "alternatives"}:
            raise DifficultyContractError(f"milestones[{index}] schema mismatch")
        milestone_id = milestone["milestone_id"]
        if not isinstance(milestone_id, str) or not milestone_id.strip() or milestone_id in seen_milestones:
            raise DifficultyContractError("milestone IDs must be non-empty and unique")
        seen_milestones.add(milestone_id)
        decompositions *= len(_ids(milestone["alternatives"], f"milestones[{index}].alternatives"))
    admissible_decompositions = _integer(
        contract["admissible_decomposition_count"],
        "admissible_decomposition_count",
        minimum=1,
    )
    if admissible_decompositions > decompositions:
        raise DifficultyContractError(
            "admissible_decomposition_count exceeds registered milestone alternatives"
        )

    raw = {
        "dependency_depth": float(_longest_chain(tuple(obligation_ids), tuple(edges))),
        "coordination_width": float(_coordination_width(robots, tuple(capable), tuple(minima), team_min)),
        "capability_scarcity": _capability_scarcity(tuple(capable), tuple(minima)),
        "contention": float(max(contention_values, default=0)),
        "hazard": float(hazard),
        "communication_demand": float(communication),
        "branching_load": math.log2(admissible_decompositions),
        "uncertainty": _uncertainty(contract["hidden_variables"]),
    }
    components = {name: score_component(name, raw[name]) for name in COMPONENTS}
    result = {
        "schema": ASSESSMENT_SCHEMA,
        "base_contract_hash": expected_hash,
        "domain": contract["domain"],
        "primitive": contract["primitive"],
        "components": components,
        "tier": 1 + max(item["bin"] for item in components.values()),
        "condition_invariant": True,
        "outcome_independent": True,
        "formal_gates": dict(FORMAL_GATES),
    }
    result["assessment_digest"] = _canonical(result)
    return result


def validate_assessment(value: Mapping[str, Any], base_contract: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema", "base_contract_hash", "domain", "primitive", "components", "tier", "condition_invariant", "outcome_independent", "formal_gates", "assessment_digest"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != ASSESSMENT_SCHEMA:
        raise DifficultyContractError("difficulty assessment schema mismatch")
    if value.get("assessment_digest") != _canonical({key: item for key, item in value.items() if key != "assessment_digest"}):
        raise DifficultyContractError("difficulty assessment digest mismatch")
    components = value.get("components")
    if not isinstance(components, Mapping) or set(components) != set(COMPONENTS):
        raise DifficultyContractError("difficulty assessment component set mismatch")
    bins = []
    for name in COMPONENTS:
        item = components[name]
        if not isinstance(item, Mapping) or item != score_component(name, item.get("raw")):
            raise DifficultyContractError(f"difficulty assessment component {name} was tampered")
        bins.append(item["bin"])
    if value.get("domain") not in {"production", "service", "living_public"} or value.get("primitive") not in {"P1", "P2", "P3", "P4"}:
        raise DifficultyContractError("difficulty assessment cell identity was tampered")
    if value.get("condition_invariant") is not True or value.get("outcome_independent") is not True:
        raise DifficultyContractError("difficulty assessment invariance flags were tampered")
    if value.get("tier") != 1 + max(bins) or value.get("formal_gates") != FORMAL_GATES:
        raise DifficultyContractError("difficulty assessment tier or gates were tampered")
    recomputed = evaluate_difficulty_contract(base_contract)
    if dict(value) != recomputed:
        raise DifficultyContractError("difficulty assessment does not reproduce from base contract")
    return dict(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DifficultyContractError(f"{name} must be a non-empty string")
    return value


def _digest_text(value: Any, name: str) -> str:
    result = _text(value, name)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise DifficultyContractError(f"{name} must be a lowercase SHA-256 digest")
    return result


def validation_registry_digest(registry: Mapping[str, Any]) -> str:
    """Return the content address that must be pinned outside result evidence."""

    if not isinstance(registry, Mapping):
        raise DifficultyContractError("difficulty validation registry must be an object")
    return _canonical(registry)


def common_pair_registry_digest(registry: Mapping[str, Any]) -> str:
    """Return the externally pinned content address for a common-pair registry."""

    if not isinstance(registry, Mapping):
        raise DifficultyContractError("common-pair registry must be an object")
    return _canonical(registry)


def _validate_common_pair_registry(
    registry: Mapping[str, Any], expected_digest: str,
) -> tuple[str, dict[str, Mapping[str, Any]]]:
    actual_digest = common_pair_registry_digest(registry)
    if actual_digest != _digest_text(expected_digest, "expected common-pair registry digest"):
        raise DifficultyContractError("common-pair registry differs from externally pinned digest")
    required = {"schema", "paper_commit", "pairs"}
    if set(registry) != required or registry.get("schema") != COMMON_PAIR_REGISTRY_SCHEMA:
        raise DifficultyContractError("common-pair registry schema mismatch")
    if registry.get("paper_commit") != PAPER_COMMIT:
        raise DifficultyContractError("common-pair registry paper identity mismatch")
    pairs = registry["pairs"]
    if not isinstance(pairs, list) or not pairs:
        raise DifficultyContractError("common-pair registry must contain pairs")
    pair_fields = {
        "common_anchor_id", "left_side_id", "right_side_id",
        "left_domain", "left_primitive", "left_base_contract_hash",
        "right_domain", "right_primitive", "right_base_contract_hash",
    }
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(pairs):
        if not isinstance(row, Mapping) or set(row) != pair_fields:
            raise DifficultyContractError(f"common-pair registry pairs[{index}] schema mismatch")
        anchor_id = _text(row["common_anchor_id"], "common_anchor_id")
        left_id = _text(row["left_side_id"], "left_side_id")
        right_id = _text(row["right_side_id"], "right_side_id")
        if anchor_id in by_id or left_id == right_id:
            raise DifficultyContractError("common-pair identities must be unique and sides distinct")
        if row["left_domain"] not in {"production", "service", "living_public"} or row["right_domain"] not in {"production", "service", "living_public"}:
            raise DifficultyContractError("common-pair domain is invalid")
        if row["left_primitive"] not in {"P1", "P2", "P3", "P4"} or row["right_primitive"] not in {"P1", "P2", "P3", "P4"}:
            raise DifficultyContractError("common-pair primitive is invalid")
        left_hash = _digest_text(row["left_base_contract_hash"], "left_base_contract_hash")
        right_hash = _digest_text(row["right_base_contract_hash"], "right_base_contract_hash")
        if left_hash == right_hash:
            raise DifficultyContractError("common-pair sides must bind distinct base contracts")
        by_id[anchor_id] = row
    return actual_digest, by_id


def validate_common_pair_tiers(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    common_anchor_id: str,
    left_side_id: str,
    right_side_id: str,
    pair_registry: Mapping[str, Any],
    expected_pair_registry_digest: str,
) -> dict[str, Any]:
    """Validate an externally registered common pair and recompute both tiers."""

    registry_digest, pairs = _validate_common_pair_registry(
        pair_registry, expected_pair_registry_digest,
    )
    anchor_id = _text(common_anchor_id, "common_anchor_id")
    if anchor_id not in pairs:
        raise DifficultyContractError("common pair is not registered")
    entry = pairs[anchor_id]
    if left_side_id != entry["left_side_id"] or right_side_id != entry["right_side_id"]:
        raise DifficultyContractError("common-pair side identity mismatch")
    first, second = evaluate_difficulty_contract(left), evaluate_difficulty_contract(right)
    if (
        first["base_contract_hash"] != entry["left_base_contract_hash"]
        or second["base_contract_hash"] != entry["right_base_contract_hash"]
    ):
        raise DifficultyContractError("common-pair base-contract hash mismatch")
    if (first["domain"], first["primitive"]) != (entry["left_domain"], entry["left_primitive"]):
        raise DifficultyContractError("common-pair left cell mismatch")
    if (second["domain"], second["primitive"]) != (entry["right_domain"], entry["right_primitive"]):
        raise DifficultyContractError("common-pair right cell mismatch")
    if first["tier"] != second["tier"]:
        raise DifficultyContractError("common pair difficulty tier mismatch")
    result = {
        "schema": COMMON_PAIR_RECEIPT_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "common_anchor_id": anchor_id,
        "pair_registry_digest": registry_digest,
        "left_side_id": left_side_id,
        "right_side_id": right_side_id,
        "left_assessment_digest": first["assessment_digest"],
        "right_assessment_digest": second["assessment_digest"],
        "common_pair_tier": first["tier"],
        "matched": True,
        "formal_gates": dict(FORMAL_GATES),
    }
    result["receipt_digest"] = _canonical(result)
    return result


def _registered_assessment(contract_hash: Any, contracts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(contract_hash, str) or contract_hash not in contracts:
        raise DifficultyContractError("observation has no registered base contract")
    assessment = evaluate_difficulty_contract(contracts[contract_hash])
    if assessment["base_contract_hash"] != contract_hash:
        raise DifficultyContractError("base contract registry key/hash mismatch")
    return assessment


def _seal_report(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["report_digest"] = _canonical(result)
    return result


def _trend(successes: Sequence[int], trials: Sequence[int], alpha: float) -> dict[str, Any]:
    total = sum(trials)
    successes_total = sum(successes)
    p = successes_total / total
    mean_score = sum(index * trials[index] for index in range(4)) / total
    # Hypergeometric null variance conditional on the total successes:
    # n1*n0/[N*(N-1)] * sum_j n_j(x_j-xbar)^2.
    variance = p * (1 - p) * total / (total - 1) * sum(
        trials[index] * (index - mean_score) ** 2 for index in range(4)
    )
    if variance <= 0:
        return {"z": None, "p_decrease": 1.0, "p_increase": 1.0, "direction": "degenerate"}
    numerator = sum(index * (successes[index] - trials[index] * p) for index in range(4))
    z = numerator / math.sqrt(variance)
    normal = NormalDist()
    p_decrease = normal.cdf(z)
    p_increase = 1 - normal.cdf(z)
    direction = "significant_decrease" if p_decrease < alpha else "significant_increase" if p_increase < alpha else "not_significant"
    return {"z": z, "p_decrease": p_decrease, "p_increase": p_increase, "direction": direction}


def _validate_validation_registry(
    registry: Mapping[str, Any], expected_digest: str,
) -> dict[str, Any]:
    actual_digest = validation_registry_digest(registry)
    if actual_digest != _digest_text(expected_digest, "expected validation registry digest"):
        raise DifficultyContractError("validation registry differs from externally pinned digest")
    required = {
        "schema", "paper_commit", "generator_revision", "pilot_alpha", "fresh_tolerance",
        "implementations", "pilot_roster", "fresh_cells", "fresh_instances",
        "fresh_roster", "allowed_base_contract_hashes",
    }
    if set(registry) != required or registry.get("schema") != VALIDATION_REGISTRY_SCHEMA:
        raise DifficultyContractError("difficulty validation registry schema mismatch")
    if registry.get("paper_commit") != PAPER_COMMIT:
        raise DifficultyContractError("difficulty validation registry paper identity mismatch")
    generator_revision = _text(registry["generator_revision"], "generator_revision")
    alpha = _number(registry["pilot_alpha"], "pilot alpha", positive=True)
    tolerance = _number(registry["fresh_tolerance"], "fresh tolerance")
    if alpha != 0.05:
        raise DifficultyContractError("pilot alpha is frozen at 0.05")
    if tolerance > 1:
        raise DifficultyContractError("fresh tolerance must be in [0,1]")

    implementations_value = registry["implementations"]
    if not isinstance(implementations_value, list) or not implementations_value:
        raise DifficultyContractError("validation registry implementations must be non-empty")
    implementations: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(implementations_value):
        if not isinstance(row, Mapping) or set(row) != {"implementation_id", "implementation_role", "candidate"}:
            raise DifficultyContractError(f"implementations[{index}] schema mismatch")
        identifier = _text(row["implementation_id"], "implementation_id")
        if identifier in implementations or row["implementation_role"] not in {"registered_pilot", "candidate"} or not isinstance(row["candidate"], bool):
            raise DifficultyContractError("implementation registry identity, role, or candidate flag is invalid")
        if (row["implementation_role"] == "candidate") != row["candidate"]:
            raise DifficultyContractError("implementation role and candidate flag disagree")
        implementations[identifier] = row
    pilot_implementations = {
        identifier for identifier, row in implementations.items()
        if row["implementation_role"] == "registered_pilot" and row["candidate"] is False
    }
    if not pilot_implementations:
        raise DifficultyContractError("validation registry has no registered pilot implementation")

    allowed_hashes = _ids(registry["allowed_base_contract_hashes"], "allowed_base_contract_hashes")
    for contract_hash in allowed_hashes:
        _digest_text(contract_hash, "allowed base-contract hash")
    if tuple(sorted(allowed_hashes)) != allowed_hashes:
        raise DifficultyContractError("allowed base-contract hashes must be sorted")
    allowed_hash_set = set(allowed_hashes)

    pilot_fields = {"observation_id", "implementation_id", "component", "bin", "instance_id", "base_contract_hash"}
    pilot_roster_value = registry["pilot_roster"]
    if not isinstance(pilot_roster_value, list) or not pilot_roster_value:
        raise DifficultyContractError("pilot roster must be non-empty")
    pilot_roster: dict[str, Mapping[str, Any]] = {}
    pilot_bins: dict[tuple[str, str], set[int]] = {}
    seen_pilot_slots: set[tuple[str, str, int, str]] = set()
    for index, row in enumerate(pilot_roster_value):
        if not isinstance(row, Mapping) or set(row) != pilot_fields:
            raise DifficultyContractError(f"pilot_roster[{index}] schema mismatch")
        observation_id = _text(row["observation_id"], "pilot observation_id")
        implementation_id = _text(row["implementation_id"], "pilot implementation_id")
        component = row["component"]
        bin_value = _integer(row["bin"], "pilot bin")
        instance_id = _text(row["instance_id"], "pilot instance_id")
        contract_hash = _digest_text(row["base_contract_hash"], "pilot base-contract hash")
        slot = (implementation_id, component, bin_value, instance_id)
        if observation_id in pilot_roster or implementation_id not in pilot_implementations or component not in COMPONENTS or bin_value > 3 or slot in seen_pilot_slots or contract_hash not in allowed_hash_set:
            raise DifficultyContractError("pilot roster contains an invalid, duplicate, candidate, or unregistered slot")
        pilot_roster[observation_id] = row
        seen_pilot_slots.add(slot)
        pilot_bins.setdefault((implementation_id, component), set()).add(bin_value)
    expected_sweeps = {(implementation_id, component) for implementation_id in pilot_implementations for component in COMPONENTS}
    if set(pilot_bins) != expected_sweeps or any(bins != {0, 1, 2, 3} for bins in pilot_bins.values()):
        raise DifficultyContractError("pilot roster is not the complete implementation/component/bin sweep")

    cells_value = registry["fresh_cells"]
    if not isinstance(cells_value, list) or not cells_value:
        raise DifficultyContractError("fresh_cells must be non-empty")
    cells: set[tuple[str, str]] = set()
    for index, row in enumerate(cells_value):
        if not isinstance(row, Mapping) or set(row) != {"domain", "primitive"}:
            raise DifficultyContractError(f"fresh_cells[{index}] schema mismatch")
        cell = (row["domain"], row["primitive"])
        if cell[0] not in {"production", "service", "living_public"} or cell[1] not in {"P1", "P2", "P3", "P4"} or cell in cells:
            raise DifficultyContractError("fresh cell is invalid or duplicated")
        cells.add(cell)

    instances_value = registry["fresh_instances"]
    if not isinstance(instances_value, list) or not instances_value:
        raise DifficultyContractError("fresh_instances must be non-empty")
    fresh_instances: set[tuple[str, str, str]] = set()
    for index, row in enumerate(instances_value):
        if not isinstance(row, Mapping) or set(row) != {"domain", "primitive", "instance_id"}:
            raise DifficultyContractError(f"fresh_instances[{index}] schema mismatch")
        instance = (row["domain"], row["primitive"], _text(row["instance_id"], "fresh instance_id"))
        if instance[:2] not in cells or instance in fresh_instances:
            raise DifficultyContractError("fresh instance references an invalid cell or is duplicated")
        fresh_instances.add(instance)
    if {instance[:2] for instance in fresh_instances} != cells:
        raise DifficultyContractError("every fresh cell must have a frozen instance")

    fresh_fields = {"observation_id", "domain", "primitive", "implementation_id", "tier", "instance_id", "base_contract_hash"}
    fresh_roster_value = registry["fresh_roster"]
    if not isinstance(fresh_roster_value, list) or not fresh_roster_value:
        raise DifficultyContractError("fresh roster must be non-empty")
    fresh_roster: dict[str, Mapping[str, Any]] = {}
    fresh_slots: set[tuple[str, str, str, str, int]] = set()
    for index, row in enumerate(fresh_roster_value):
        if not isinstance(row, Mapping) or set(row) != fresh_fields:
            raise DifficultyContractError(f"fresh_roster[{index}] schema mismatch")
        observation_id = _text(row["observation_id"], "fresh observation_id")
        implementation_id = _text(row["implementation_id"], "fresh implementation_id")
        tier = _integer(row["tier"], "fresh tier", minimum=1)
        instance = (row["domain"], row["primitive"], _text(row["instance_id"], "fresh instance_id"))
        contract_hash = _digest_text(row["base_contract_hash"], "fresh base-contract hash")
        slot = (*instance, implementation_id, tier)
        if observation_id in fresh_roster or observation_id in pilot_roster or implementation_id not in pilot_implementations or tier > 4 or instance not in fresh_instances or slot in fresh_slots or contract_hash not in allowed_hash_set:
            raise DifficultyContractError("fresh roster contains an invalid, duplicate, candidate, or unregistered slot")
        fresh_roster[observation_id] = row
        fresh_slots.add(slot)
    expected_fresh_slots = {
        (*instance, implementation_id, tier)
        for instance in fresh_instances
        for implementation_id in pilot_implementations
        for tier in range(1, 5)
    }
    if fresh_slots != expected_fresh_slots:
        raise DifficultyContractError("fresh roster is not the complete cell/implementation/tier/instance product")

    return {
        "registry_digest": actual_digest,
        "generator_revision": generator_revision,
        "alpha": alpha,
        "tolerance": tolerance,
        "pilot_implementations": tuple(sorted(pilot_implementations)),
        "allowed_hashes": allowed_hash_set,
        "pilot_roster": pilot_roster,
        "fresh_roster": fresh_roster,
        "fresh_cells": cells,
        "fresh_instances": fresh_instances,
    }


def _base_contract_registry(
    base_contracts: Mapping[str, Mapping[str, Any]], allowed_hashes: set[str],
) -> tuple[str, dict[str, dict[str, Any]]]:
    if not isinstance(base_contracts, Mapping) or set(base_contracts) != allowed_hashes:
        raise DifficultyContractError("base-contract registry does not exactly match the frozen allowed hash roster")
    assessments: dict[str, dict[str, Any]] = {}
    material = []
    for contract_hash in sorted(allowed_hashes):
        assessment = _registered_assessment(contract_hash, base_contracts)
        assessments[contract_hash] = assessment
        material.append({"base_contract_hash": contract_hash, "base_contract": dict(base_contracts[contract_hash])})
    return _canonical(material), assessments


def _validation_context(
    base_contracts: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Any],
    expected_registry_digest: str,
) -> dict[str, Any]:
    context = _validate_validation_registry(registry, expected_registry_digest)
    base_digest, assessments = _base_contract_registry(base_contracts, context["allowed_hashes"])
    for row in context["pilot_roster"].values():
        assessment = assessments[row["base_contract_hash"]]
        bins = {name: assessment["components"][name]["bin"] for name in COMPONENTS}
        target_bin = row["bin"]
        if bins[row["component"]] != target_bin or any(bins[name] != 0 for name in COMPONENTS if name != row["component"]) or assessment["tier"] != 1 + target_bin:
            raise DifficultyContractError("pilot roster contract is not its registered single-component bin")
    for row in context["fresh_roster"].values():
        assessment = assessments[row["base_contract_hash"]]
        if (assessment["domain"], assessment["primitive"], assessment["tier"]) != (row["domain"], row["primitive"], row["tier"]):
            raise DifficultyContractError("fresh roster contract differs from its registered cell or tier")
    context["base_contract_registry_digest"] = base_digest
    context["assessments"] = assessments
    return context


def _evidence_rows(
    evidence: Mapping[str, Any] | None,
    roster: Mapping[str, Mapping[str, Any]],
    phase: str,
) -> tuple[str | None, tuple[str, ...], dict[str, Mapping[str, Any]]]:
    if evidence is None:
        return None, (), {}
    if not isinstance(evidence, Mapping) or set(evidence) != {"schema", "observations"} or evidence.get("schema") != EVIDENCE_SCHEMA:
        raise DifficultyContractError(f"{phase} evidence schema mismatch; evidence may contain observations only")
    observations = evidence["observations"]
    if not isinstance(observations, list):
        raise DifficultyContractError(f"{phase} observations must be a list")
    rows: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(observations):
        if not isinstance(row, Mapping) or set(row) != {"observation_id", "successes", "trials"}:
            raise DifficultyContractError(f"{phase} observations[{index}] schema mismatch")
        observation_id = _text(row["observation_id"], f"{phase} observation_id")
        trials = _integer(row["trials"], f"{phase} trials", minimum=1)
        successes = _integer(row["successes"], f"{phase} successes")
        if observation_id in rows or successes > trials:
            raise DifficultyContractError(f"{phase} observation is duplicated or successes exceed trials")
        rows[observation_id] = row
    if set(rows) != set(roster):
        raise DifficultyContractError(f"{phase} evidence observation IDs do not exactly match the frozen roster")
    identifiers = tuple(sorted(rows))
    return _canonical(evidence), identifiers, rows


def certify_pilot_sweeps(
    evidence: Mapping[str, Any] | None,
    base_contracts: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Any],
    expected_registry_digest: str,
) -> dict[str, Any]:
    """Certify all frozen implementation/component sweeps from observation-only evidence."""

    context = _validation_context(base_contracts, registry, expected_registry_digest)
    evidence_digest, observation_ids, observations = _evidence_rows(evidence, context["pilot_roster"], "pilot")
    reports: dict[str, Any] = {}
    missing = list(COMPONENTS) if evidence is None else []
    for component in COMPONENTS:
        implementation_reports = []
        if evidence is not None:
            for implementation_id in context["pilot_implementations"]:
                successes = [0] * 4
                trials = [0] * 4
                for observation_id, roster_row in context["pilot_roster"].items():
                    if roster_row["component"] != component or roster_row["implementation_id"] != implementation_id:
                        continue
                    bin_value = roster_row["bin"]
                    evidence_row = observations[observation_id]
                    successes[bin_value] += evidence_row["successes"]
                    trials[bin_value] += evidence_row["trials"]
                implementation_reports.append({"implementation_id": implementation_id, **_trend(successes, trials, context["alpha"])})
        passed = bool(implementation_reports) and any(item["direction"] == "significant_decrease" for item in implementation_reports) and not any(item["direction"] == "significant_increase" for item in implementation_reports)
        reports[component] = {
            "status": "passed" if passed else "missing_evidence" if evidence is None else "failed",
            "released_as": "certified" if passed else "diagnostic",
            "implementations": implementation_reports,
        }
    gate = evidence is not None and not missing and all(item["status"] == "passed" for item in reports.values())
    return _seal_report({
        "schema": PILOT_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "validation_registry_digest": context["registry_digest"],
        "base_contract_registry_digest": context["base_contract_registry_digest"],
        "evidence_digest": evidence_digest,
        "observation_ids": list(observation_ids),
        "alpha": context["alpha"],
        "pilot_gate": gate,
        "missing_components": missing,
        "components": reports,
        "formal_gates": dict(FORMAL_GATES),
    })


def certify_fresh_ordering(
    evidence: Mapping[str, Any] | None,
    base_contracts: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Any],
    expected_registry_digest: str,
) -> dict[str, Any]:
    """Check frozen fresh cell/implementation/tier/instance observations."""

    context = _validation_context(base_contracts, registry, expected_registry_digest)
    evidence_digest, observation_ids, observations = _evidence_rows(evidence, context["fresh_roster"], "fresh")
    aggregate: dict[tuple[str, str, str, int], list[int]] = {}
    if evidence is not None:
        for observation_id, roster_row in context["fresh_roster"].items():
            evidence_row = observations[observation_id]
            key = (roster_row["domain"], roster_row["primitive"], roster_row["implementation_id"], roster_row["tier"])
            counts = aggregate.setdefault(key, [0, 0])
            counts[0] += evidence_row["successes"]
            counts[1] += evidence_row["trials"]
    unordered = []
    missing = []
    for domain, primitive in sorted(context["fresh_cells"]):
        for implementation_id in context["pilot_implementations"]:
            rates = {}
            for tier in range(1, 5):
                counts = aggregate.get((domain, primitive, implementation_id, tier))
                if counts is not None:
                    rates[tier] = counts[0] / counts[1]
            if set(rates) != {1, 2, 3, 4}:
                missing.append({"domain": domain, "primitive": primitive, "implementation_id": implementation_id, "missing_tiers": sorted({1, 2, 3, 4} - set(rates))})
                continue
            for lower in range(1, 4):
                rise = rates[lower + 1] - rates[lower]
                if rise > context["tolerance"]:
                    unordered.append({"domain": domain, "primitive": primitive, "implementation_id": implementation_id, "lower_tier": lower, "upper_tier": lower + 1, "rise": rise, "tolerance": context["tolerance"]})
    return _seal_report({
        "schema": FRESH_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "validation_registry_digest": context["registry_digest"],
        "base_contract_registry_digest": context["base_contract_registry_digest"],
        "evidence_digest": evidence_digest,
        "observation_ids": list(observation_ids),
        "tolerance": context["tolerance"],
        "fresh_gate": evidence is not None and not missing and not unordered,
        "unordered_pairs": unordered,
        "missing_strata": missing,
        "formal_gates": dict(FORMAL_GATES),
    })


def validate_pilot_report(
    value: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    base_contracts: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Any],
    expected_registry_digest: str,
) -> dict[str, Any]:
    expected = certify_pilot_sweeps(evidence, base_contracts, registry, expected_registry_digest)
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise DifficultyContractError("pilot report does not reproduce from frozen registry, base contracts, and evidence")
    return dict(value)


def validate_fresh_report(
    value: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    base_contracts: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Any],
    expected_registry_digest: str,
) -> dict[str, Any]:
    expected = certify_fresh_ordering(evidence, base_contracts, registry, expected_registry_digest)
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise DifficultyContractError("fresh report does not reproduce from frozen registry, base contracts, and evidence")
    return dict(value)


def difficulty_certification(
    pilot: Mapping[str, Any],
    fresh: Mapping[str, Any],
    pilot_evidence: Mapping[str, Any] | None,
    fresh_evidence: Mapping[str, Any] | None,
    base_contracts: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Any],
    expected_registry_digest: str,
) -> dict[str, Any]:
    pilot_report = validate_pilot_report(pilot, pilot_evidence, base_contracts, registry, expected_registry_digest)
    fresh_report = validate_fresh_report(fresh, fresh_evidence, base_contracts, registry, expected_registry_digest)
    if pilot_report["base_contract_registry_digest"] != fresh_report["base_contract_registry_digest"]:
        raise DifficultyContractError("pilot and fresh reports bind different base-contract registries")
    pilot_gate = pilot_report["pilot_gate"] is True
    fresh_gate = fresh_report["fresh_gate"] is True
    result = {
        "schema": CERTIFICATION_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "validation_registry_digest": pilot_report["validation_registry_digest"],
        "base_contract_registry_digest": pilot_report["base_contract_registry_digest"],
        "pilot_report_digest": pilot_report["report_digest"],
        "fresh_report_digest": fresh_report["report_digest"],
        "pilot_evidence_digest": pilot_report["evidence_digest"],
        "fresh_evidence_digest": fresh_report["evidence_digest"],
        "pilot_observation_ids": list(pilot_report["observation_ids"]),
        "fresh_observation_ids": list(fresh_report["observation_ids"]),
        "difficulty_certification_gate": pilot_gate and fresh_gate,
        "pilot_gate": pilot_gate,
        "fresh_gate": fresh_gate,
        "candidate_outcomes_used": False,
        "tier_source": "base_contract_only",
        "formal_gates": dict(FORMAL_GATES),
    }
    result["certification_digest"] = _canonical(result)
    return result


def validate_certification(
    value: Mapping[str, Any],
    pilot_evidence: Mapping[str, Any] | None,
    fresh_evidence: Mapping[str, Any] | None,
    base_contracts: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Any],
    expected_registry_digest: str,
) -> dict[str, Any]:
    """Reproduce both reports and the final receipt from frozen inputs."""

    pilot = certify_pilot_sweeps(pilot_evidence, base_contracts, registry, expected_registry_digest)
    fresh = certify_fresh_ordering(fresh_evidence, base_contracts, registry, expected_registry_digest)
    expected = difficulty_certification(
        pilot, fresh, pilot_evidence, fresh_evidence,
        base_contracts, registry, expected_registry_digest,
    )
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise DifficultyContractError("difficulty certification receipt does not reproduce from frozen inputs")
    return dict(value)


__all__ = [
    "ASSESSMENT_SCHEMA", "BASE_CONTRACT_SCHEMA", "CERTIFICATION_SCHEMA",
    "COMPONENTS", "COMPONENT_SPECS", "ComponentSpec", "DifficultyContractError",
    "COMMON_PAIR_RECEIPT_SCHEMA", "COMMON_PAIR_REGISTRY_SCHEMA", "EVIDENCE_SCHEMA",
    "FRESH_SCHEMA", "PILOT_SCHEMA", "VALIDATION_REGISTRY_SCHEMA", "bind_base_contract",
    "canonical_base_contract_hash", "certify_fresh_ordering",
    "certify_pilot_sweeps", "common_pair_registry_digest", "difficulty_certification",
    "evaluate_difficulty_contract", "score_component", "validate_assessment",
    "validate_certification", "validate_common_pair_tiers", "validate_fresh_report",
    "validate_pilot_report", "validation_registry_digest",
]
