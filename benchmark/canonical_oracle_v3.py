"""Small, exact canonical feasibility producer for paper-level contracts.

The authoring catalog intentionally contains high-level task semantics.  It is
not rich enough to be fed directly to the paper's deployment oracle.  This
module therefore accepts an explicit, frozen integer-tick contract and runs a
deterministic bounded search over robot assignments and obligation start times.
Every deployable result is independently checked from the serialized witness;
an exhaustive search with no witness produces a checkable infeasibility proof.
Budget exhaustion, malformed contracts, unsupported features and any checker
disagreement remain ``feasibility_unverified``.

This is an exact bounded backend for small released contracts, not an optimizer
and not a replacement for the existing EAI 2-D navigation pre-screen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import itertools
import json
import math
from typing import Any, Iterator, Mapping, Sequence


SCHEMA = "heterocoord.canonical_oracle.v3"
BACKEND_ID = "bounded-enumerator-v3"

# A result is only eligible for the adapter after it has crossed the producer
# boundary.  Keeping the marker private prevents a deserialized or hand-built
# result with a plausible witness/proof from being mistaken for producer output.
_PRODUCER_ATTESTATION = object()


class OracleStatus(str, Enum):
    DEPLOYABLE = "deployable"
    TEAM_STATE_INFEASIBLE = "team_state_infeasible"
    UNVERIFIED = "feasibility_unverified"


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class RobotSpecV3:
    robot_id: str
    capabilities: tuple[str, ...]
    roles: tuple[str, ...]

    def __post_init__(self) -> None:
        capabilities = tuple(self.capabilities)
        roles = tuple(self.roles)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "roles", roles)
        if not self.robot_id.strip() or not capabilities or not roles:
            raise ContractError("robot_id, capabilities and roles are required")
        if len(set(capabilities)) != len(capabilities) or len(set(roles)) != len(roles):
            raise ContractError("robot capabilities and roles must be unique")


@dataclass(frozen=True)
class ResourceSpecV3:
    resource_id: str
    capacity: int

    def __post_init__(self) -> None:
        if not self.resource_id.strip() or type(self.capacity) is not int or self.capacity <= 0:
            raise ContractError("resource capacity must be a positive integer")


@dataclass(frozen=True)
class ObligationSpecV3:
    obligation_id: str
    capability: str
    role: str
    duration_ticks: int
    release_tick: int
    deadline_tick: int
    min_participants: int = 1
    max_participants: int = 1
    sync_window_ticks: int = 0
    resource_demands: Mapping[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.obligation_id.strip() or not self.capability.strip() or not self.role.strip():
            raise ContractError("obligation identity, capability and role are required")
        ints = (self.duration_ticks, self.release_tick, self.deadline_tick,
                self.min_participants, self.max_participants, self.sync_window_ticks)
        if any(type(value) is not int for value in ints):
            raise ContractError("obligation timing and coalition fields must be integers")
        if self.duration_ticks <= 0 or self.release_tick < 0 or self.deadline_tick < self.release_tick + self.duration_ticks:
            raise ContractError("invalid obligation timing")
        if self.min_participants <= 0 or self.max_participants < self.min_participants:
            raise ContractError("invalid coalition bounds")
        if self.sync_window_ticks < 0:
            raise ContractError("sync_window_ticks cannot be negative")
        demands = dict(self.resource_demands or {})
        if any(not str(key).strip() or type(value) is not int or value < 0 for key, value in demands.items()):
            raise ContractError("resource demands must be nonnegative integers")
        object.__setattr__(self, "resource_demands", demands)


@dataclass(frozen=True)
class PrecedenceV3:
    before: str
    after: str
    min_lag_ticks: int = 0
    max_lag_ticks: int | None = None

    def __post_init__(self) -> None:
        if not self.before.strip() or not self.after.strip() or self.before == self.after:
            raise ContractError("precedence endpoints must be distinct")
        if type(self.min_lag_ticks) is not int or self.min_lag_ticks < 0:
            raise ContractError("min_lag_ticks must be a nonnegative integer")
        if self.max_lag_ticks is not None and (type(self.max_lag_ticks) is not int or self.max_lag_ticks < self.min_lag_ticks):
            raise ContractError("max_lag_ticks must be >= min_lag_ticks")


@dataclass(frozen=True)
class CanonicalContractV3:
    task_id: str
    horizon_ticks: int
    robots: tuple[RobotSpecV3, ...]
    obligations: tuple[ObligationSpecV3, ...]
    precedences: tuple[PrecedenceV3, ...] = ()
    resources: tuple[ResourceSpecV3, ...] = ()
    mutex_pairs: tuple[tuple[str, str], ...] = ()
    min_team_size: int = 1
    max_team_size: int | None = None
    tick_dt_s: float = 0.02

    def __post_init__(self) -> None:
        if not self.task_id.strip() or type(self.horizon_ticks) is not int or self.horizon_ticks < 0:
            raise ContractError("task_id and nonnegative horizon_ticks are required")
        if not self.robots or not self.obligations:
            raise ContractError("robots and obligations are required")
        if len({robot.robot_id for robot in self.robots}) != len(self.robots):
            raise ContractError("robot IDs must be unique")
        if len({item.obligation_id for item in self.obligations}) != len(self.obligations):
            raise ContractError("obligation IDs must be unique")
        if type(self.min_team_size) is not int or self.min_team_size <= 0:
            raise ContractError("min_team_size must be positive")
        max_team = len(self.robots) if self.max_team_size is None else self.max_team_size
        if type(max_team) is not int or max_team < self.min_team_size or max_team > len(self.robots):
            raise ContractError("invalid team-size bounds")
        if self.min_team_size > len(self.robots):
            raise ContractError("min_team_size exceeds robot pool")
        if isinstance(self.tick_dt_s, bool) or not isinstance(self.tick_dt_s, (int, float)) or not math.isclose(float(self.tick_dt_s), 0.02, rel_tol=0.0, abs_tol=1e-12):
            raise ContractError("tick_dt_s must be the registered 0.02 seconds")
        obligation_ids = {item.obligation_id for item in self.obligations}
        resource_ids = {item.resource_id for item in self.resources}
        for item in self.obligations:
            if item.deadline_tick > self.horizon_ticks:
                raise ContractError(f"obligation {item.obligation_id} exceeds horizon")
            if not set(item.resource_demands) <= resource_ids:
                raise ContractError(f"obligation {item.obligation_id} references unknown resource")
        for edge in self.precedences:
            if edge.before not in obligation_ids or edge.after not in obligation_ids:
                raise ContractError("precedence references unknown obligation")
        pairs = set()
        for first, second in self.mutex_pairs:
            if first not in obligation_ids or second not in obligation_ids or first == second:
                raise ContractError("mutex pair references invalid obligation")
            pair = tuple(sorted((first, second)))
            if pair in pairs:
                raise ContractError("duplicate mutex pair")
            pairs.add(pair)

    @property
    def max_team(self) -> int:
        return len(self.robots) if self.max_team_size is None else self.max_team_size

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "task_id": self.task_id,
            "horizon_ticks": self.horizon_ticks,
            "tick_dt_s": self.tick_dt_s,
            "min_team_size": self.min_team_size,
            "max_team_size": self.max_team,
            "robots": [{"robot_id": item.robot_id, "capabilities": list(item.capabilities), "roles": list(item.roles)} for item in self.robots],
            "obligations": [asdict(item) for item in self.obligations],
            "precedences": [asdict(item) for item in self.precedences],
            "resources": [asdict(item) for item in self.resources],
            "mutex_pairs": [list(item) for item in self.mutex_pairs],
        }

    def digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class OracleResultV3:
    task_id: str
    status: OracleStatus
    backend_id: str
    contract_digest: str
    witness: Mapping[str, Any] | None
    proof: Mapping[str, Any] | None
    explored_nodes: int
    complete: bool
    reasons: tuple[str, ...] = ()
    _producer_attestation: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", OracleStatus(self.status))
        if self.status is OracleStatus.DEPLOYABLE and not self.witness:
            raise ContractError("deployable result requires a witness")
        if self.status is OracleStatus.TEAM_STATE_INFEASIBLE and not self.proof:
            raise ContractError("infeasible result requires a proof")
        if self.explored_nodes < 0 or type(self.complete) is not bool:
            raise ContractError("invalid search accounting")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "task_id": self.task_id, "status": self.status.value,
                "backend_id": self.backend_id, "contract_digest": self.contract_digest,
                "witness": self.witness, "proof": self.proof,
                "explored_nodes": self.explored_nodes, "complete": self.complete,
                "reasons": list(self.reasons)}

    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_certificate(self, contract: CanonicalContractV3):
        """Adapt a producer result to the existing release-gate certificate.

        The adapter is intentionally one-way: it can only use the witness or
        proof already produced and checked by this module.  It never turns a
        caller-supplied status field into a certificate.
        """
        from .oracle_v3 import OracleFinding, make_oracle_certificate

        if self.contract_digest != contract.digest():
            raise ContractError("result contract digest does not match certificate contract")
        if self._producer_attestation is not _PRODUCER_ATTESTATION:
            raise ContractError("result was not produced by solve_canonical")

        # v3 is intentionally a narrow bounded diagnostic subset. Its exact
        # result is useful evidence for this subset, but cannot be promoted to
        # the paper's complete role/lag/participant/mutex contract certificate.
        diagnostic_limit = ("bounded_subset_diagnostic", "paper_canonical_contract_incomplete")
        if self.status is OracleStatus.DEPLOYABLE:
            if self.witness is None or not check_witness(contract, self.witness)[0]:
                raise ContractError("deployable result witness failed independent checker")
            findings = [OracleFinding(item, "unknown", "bounded subset is not paper-complete")
                        for item in ("capability", "role", "time", "joint", "resource", "mutex", "deadline")]
            return make_oracle_certificate(task_id=self.task_id, backend_id=self.backend_id,
                                           findings=findings, witness=self.witness,
                                           backend_complete=False, limitations=diagnostic_limit)
        if self.status is OracleStatus.TEAM_STATE_INFEASIBLE:
            if self.proof is None or not check_infeasibility_proof(contract, self.proof):
                raise ContractError("infeasibility proof failed independent checker")
            findings = [OracleFinding(item, "unknown", "bounded subset proof is not paper-complete")
                        for item in ("capability", "role", "time", "joint", "resource", "mutex", "deadline")]
            return make_oracle_certificate(task_id=self.task_id, backend_id=self.backend_id,
                                           findings=findings, limitations=diagnostic_limit)
        findings = [OracleFinding(item, "unknown", "canonical search is incomplete")
                    for item in ("capability", "role", "time", "joint", "resource", "mutex", "deadline")]
        return make_oracle_certificate(task_id=self.task_id, backend_id=self.backend_id,
                                       findings=findings, limitations=diagnostic_limit + self.reasons)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _validate_witness(contract: CanonicalContractV3, witness: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    ids = {item.obligation_id for item in contract.obligations}
    robots = {item.robot_id: item for item in contract.robots}
    assignments = witness.get("assignments")
    starts = witness.get("starts")
    participant_ticks = witness.get("participant_ticks")
    team = witness.get("team")
    if not isinstance(assignments, Mapping) or not isinstance(starts, Mapping) or not isinstance(participant_ticks, Mapping) or not isinstance(team, list):
        return False, ("witness_shape_invalid",)
    if witness.get("contract_digest") != contract.digest():
        return False, ("witness_contract_digest_mismatch",)
    if set(assignments) != ids or set(starts) != ids or set(participant_ticks) != ids or len(team) < contract.min_team_size or len(team) > contract.max_team:
        return False, ("witness_identity_or_team_invalid",)
    if len(set(team)) != len(team) or not set(team) <= set(robots):
        return False, ("witness_team_invalid",)
    specs = {item.obligation_id: item for item in contract.obligations}
    occupied: dict[int, dict[str, int]] = {}
    for obligation_id, spec in specs.items():
        members = assignments[obligation_id]
        start = starts[obligation_id]
        if not isinstance(members, list) or not members or len(set(members)) != len(members):
            return False, (f"assignment_invalid:{obligation_id}",)
        if len(members) < spec.min_participants or len(members) > spec.max_participants:
            return False, (f"coalition_bounds:{obligation_id}",)
        if type(start) is not int or start < spec.release_tick or start + spec.duration_ticks > spec.deadline_tick or start + spec.duration_ticks > contract.horizon_ticks:
            return False, (f"timing_invalid:{obligation_id}",)
        if not set(members) <= set(team) or not set(members) <= set(robots):
            return False, (f"team_membership:{obligation_id}",)
        if any(spec.capability not in robots[robot_id].capabilities or spec.role not in robots[robot_id].roles for robot_id in members):
            return False, (f"capability_or_role:{obligation_id}",)
        ticks = participant_ticks[obligation_id]
        if not isinstance(ticks, Mapping) or set(ticks) != set(members):
            return False, (f"participant_ticks:{obligation_id}",)
        if any(type(ticks[robot_id]) is not int or ticks[robot_id] < 0 or ticks[robot_id] > contract.horizon_ticks for robot_id in members):
            return False, (f"sync_window:{obligation_id}",)
        if any(abs(ticks[first_robot] - ticks[second_robot]) > spec.sync_window_ticks
               for first_robot, second_robot in itertools.combinations(members, 2)):
            return False, (f"sync_window:{obligation_id}",)
        for tick in range(start, start + spec.duration_ticks):
            for resource_id, demand in spec.resource_demands.items():
                occupied.setdefault(tick, {})[resource_id] = occupied.setdefault(tick, {}).get(resource_id, 0) + demand
    for edge in contract.precedences:
        gap = starts[edge.after] - (starts[edge.before] + specs[edge.before].duration_ticks)
        if gap < edge.min_lag_ticks or (edge.max_lag_ticks is not None and gap > edge.max_lag_ticks):
            return False, (f"precedence:{edge.before}->{edge.after}",)
    for first, second in contract.mutex_pairs:
        a, b = starts[first], starts[second]
        sa, sb = specs[first], specs[second]
        if a < b + sb.duration_ticks and b < a + sa.duration_ticks:
            return False, (f"mutex:{first}:{second}",)
    for tick_resource, values in occupied.items():
        if not isinstance(tick_resource, int):
            continue
        for resource_id, demand in values.items():
            resource = next((item for item in contract.resources if item.resource_id == resource_id), None)
            if resource is not None and demand > resource.capacity:
                return False, (f"resource:{resource_id}",)
    return True, ()


def check_witness(contract: CanonicalContractV3, witness: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Independent witness checker with no solver state or search shortcuts."""
    return _validate_witness(contract, witness)


def solve_canonical(contract: CanonicalContractV3, *, node_budget: int = 100_000) -> OracleResultV3:
    """Run deterministic bounded exact enumeration for a small contract."""
    if type(node_budget) is not int or node_budget <= 0:
        raise ContractError("node_budget must be a positive integer")
    digest = contract.digest()
    specs = tuple(sorted(contract.obligations, key=lambda item: item.obligation_id))
    robots = tuple(sorted(contract.robots, key=lambda item: item.robot_id))
    candidates: dict[str, tuple[tuple[str, ...], ...]] = {}
    for spec in specs:
        eligible = tuple(robot.robot_id for robot in robots if spec.capability in robot.capabilities and spec.role in robot.roles)
        if len(eligible) < spec.min_participants:
            return OracleResultV3(contract.task_id, OracleStatus.TEAM_STATE_INFEASIBLE, BACKEND_ID, digest, None,
                                  {"schema": SCHEMA, "kind": "exhaustive_empty_domain", "contract_digest": digest, "backend_id": BACKEND_ID, "obligation": spec.obligation_id, "explored_nodes": 0, "node_budget": 0, "complete": True}, 0, True,
                                  (f"no_eligible_coalition:{spec.obligation_id}",), _PRODUCER_ATTESTATION)
        candidates[spec.obligation_id] = tuple(itertools.combinations(eligible, size)
                                               for size in range(spec.min_participants, min(spec.max_participants, len(eligible)) + 1))
        candidates[spec.obligation_id] = tuple(item for groups in candidates[spec.obligation_id] for item in groups)
        if not candidates[spec.obligation_id]:
            return OracleResultV3(contract.task_id, OracleStatus.TEAM_STATE_INFEASIBLE, BACKEND_ID, digest, None,
                                  {"schema": SCHEMA, "kind": "exhaustive_empty_domain", "contract_digest": digest, "backend_id": BACKEND_ID, "obligation": spec.obligation_id, "explored_nodes": 0, "node_budget": 0, "complete": True}, 0, True,
                                  (f"empty_coalition_domain:{spec.obligation_id}",), _PRODUCER_ATTESTATION)
    candidate_count = 1
    for spec in specs:
        starts_count = spec.deadline_tick - spec.duration_ticks - spec.release_tick + 1
        candidate_count *= len(candidates[spec.obligation_id]) * max(0, starts_count)
    explored = 0
    budget_exhausted = False
    found: dict[str, Any] | None = None
    starts: dict[str, int] = {}
    assignments: dict[str, list[str]] = {}

    def recurse(index: int) -> bool:
        nonlocal explored, found, budget_exhausted
        if budget_exhausted:
            return False
        explored += 1
        if explored > node_budget:
            budget_exhausted = True
            return False
        if index == len(specs):
            team = _team_for_assignments(contract, assignments)
            if team is None:
                return False
            candidate = {"schema": SCHEMA, "contract_digest": digest, "team": team,
                         "assignments": {key: list(value) for key, value in assignments.items()},
                         "starts": dict(starts),
                         "participant_ticks": {key: {robot_id: starts[key] for robot_id in value}
                                                for key, value in assignments.items()}}
            valid, _ = check_witness(contract, candidate)
            if valid:
                found = candidate
                return True
            return False
        spec = specs[index]
        for members in candidates[spec.obligation_id]:
            if budget_exhausted:
                break
            assignments[spec.obligation_id] = list(members)
            for start in range(spec.release_tick, spec.deadline_tick - spec.duration_ticks + 1):
                if budget_exhausted:
                    break
                starts[spec.obligation_id] = start
                partial = {"schema": SCHEMA, "contract_digest": digest,
                           "team": _team_for_assignments(contract, assignments) or [],
                           "assignments": {key: list(value) for key, value in assignments.items()},
                           "starts": dict(starts),
                           "participant_ticks": {key: {robot_id: starts[key] for robot_id in value}
                                                  for key, value in assignments.items()}}
                # Check complete constraints only when all referenced values exist.
                if index == len(specs) - 1:
                    if recurse(index + 1):
                        return True
                else:
                    # Cheap local pruning: no resource or mutex violation among assigned obligations.
                    if _partial_valid(contract, partial, specs[: index + 1]):
                        if recurse(index + 1):
                            return True
            assignments.pop(spec.obligation_id, None)
            starts.pop(spec.obligation_id, None)
        return False

    recurse(0)
    if found is not None:
        valid, reasons = check_witness(contract, found)
        if not valid:
            return OracleResultV3(contract.task_id, OracleStatus.UNVERIFIED, BACKEND_ID, digest, None, None, explored, False,
                                  ("internal_witness_checker_disagreement", *reasons), _PRODUCER_ATTESTATION)
        return OracleResultV3(contract.task_id, OracleStatus.DEPLOYABLE, BACKEND_ID, digest, found, None, explored, True,
                              _producer_attestation=_PRODUCER_ATTESTATION)
    if budget_exhausted:
        return OracleResultV3(contract.task_id, OracleStatus.UNVERIFIED, BACKEND_ID, digest, None, None, explored, False,
                              ("node_budget_exhausted",), _PRODUCER_ATTESTATION)
    if candidate_count > node_budget:
        return OracleResultV3(contract.task_id, OracleStatus.UNVERIFIED, BACKEND_ID, digest, None, None, explored, False,
                              ("candidate_product_exceeds_budget",), _PRODUCER_ATTESTATION)
    proof = {"schema": SCHEMA, "kind": "exhaustive_no_witness", "contract_digest": digest,
             "backend_id": BACKEND_ID, "explored_nodes": candidate_count,
             "candidate_count": candidate_count, "node_budget": node_budget, "complete": True}
    return OracleResultV3(contract.task_id, OracleStatus.TEAM_STATE_INFEASIBLE, BACKEND_ID, digest, None, proof, explored, True,
                          _producer_attestation=_PRODUCER_ATTESTATION)


def _partial_valid(contract: CanonicalContractV3, witness: Mapping[str, Any], specs: Sequence[ObligationSpecV3]) -> bool:
    starts = witness.get("starts", {})
    for edge in contract.precedences:
        if edge.before in starts and edge.after in starts:
            a = starts[edge.after] - (starts[edge.before] + next(item.duration_ticks for item in contract.obligations if item.obligation_id == edge.before))
            if a < edge.min_lag_ticks or (edge.max_lag_ticks is not None and a > edge.max_lag_ticks):
                return False
    spec_map = {item.obligation_id: item for item in specs}
    for first, second in contract.mutex_pairs:
        if first in starts and second in starts:
            if starts[first] < starts[second] + spec_map[second].duration_ticks and starts[second] < starts[first] + spec_map[first].duration_ticks:
                return False
    for tick in range(contract.horizon_ticks + 1):
        for resource in contract.resources:
            demand = sum(item.resource_demands.get(resource.resource_id, 0)
                         for item in specs
                         if item.obligation_id in starts
                         and starts[item.obligation_id] <= tick < starts[item.obligation_id] + item.duration_ticks)
            if demand > resource.capacity:
                return False
    return True


def _team_for_assignments(contract: CanonicalContractV3, assignments: Mapping[str, Sequence[str]]) -> list[str] | None:
    """Select a deterministic team superset; idle selected robots are allowed."""
    team = sorted({member for values in assignments.values() for member in values})
    if len(team) > contract.max_team:
        return None
    available = [robot.robot_id for robot in sorted(contract.robots, key=lambda item: item.robot_id)
                 if robot.robot_id not in team]
    team.extend(available[: max(0, contract.min_team_size - len(team))])
    return team if contract.min_team_size <= len(team) <= contract.max_team else None


def check_infeasibility_proof(contract: CanonicalContractV3, proof: Mapping[str, Any], *, node_budget: int = 100_000) -> bool:
    if proof.get("schema") != SCHEMA or proof.get("kind") not in {"exhaustive_no_witness", "exhaustive_empty_domain"} or proof.get("contract_digest") != contract.digest() or proof.get("backend_id") != BACKEND_ID or proof.get("complete") is not True:
        return False
    if proof.get("kind") == "exhaustive_empty_domain":
        return proof.get("explored_nodes") == 0 and proof.get("node_budget") == 0 and any(item.obligation_id == proof.get("obligation") and
                   len([robot for robot in contract.robots
                        if item.capability in robot.capabilities and item.role in robot.roles]) < item.min_participants
                   for item in contract.obligations)
    recorded_budget = proof.get("node_budget")
    if proof.get("candidate_count") != proof.get("explored_nodes"):
        return False
    if type(recorded_budget) is not int or recorded_budget <= 0 or recorded_budget > node_budget:
        return False
    specs = tuple(sorted(contract.obligations, key=lambda item: item.obligation_id))
    domains = []
    for spec in specs:
        eligible = tuple(robot.robot_id for robot in contract.robots
                         if spec.capability in robot.capabilities and spec.role in robot.roles)
        groups = tuple(group for size in range(spec.min_participants, min(spec.max_participants, len(eligible)) + 1)
                       for group in itertools.combinations(eligible, size))
        starts = tuple(range(spec.release_tick, spec.deadline_tick - spec.duration_ticks + 1))
        if not groups or not starts:
            return False
        domains.append(tuple((group, start) for group in groups for start in starts))
    checked = 0
    for choices in itertools.product(*domains):
        checked += 1
        if checked > recorded_budget:
            return False
        assignments = {spec.obligation_id: list(choice[0]) for spec, choice in zip(specs, choices)}
        starts = {spec.obligation_id: choice[1] for spec, choice in zip(specs, choices)}
        team = _team_for_assignments(contract, assignments)
        if team is None:
            continue
        witness = {"schema": SCHEMA, "contract_digest": contract.digest(), "team": team,
                   "assignments": assignments, "starts": starts,
                   "participant_ticks": {key: {robot_id: starts[key] for robot_id in value}
                                          for key, value in assignments.items()}}
        if check_witness(contract, witness)[0]:
            return False
    return checked == proof.get("candidate_count")


__all__ = ["BACKEND_ID", "SCHEMA", "CanonicalContractV3", "ContractError", "ObligationSpecV3", "OracleResultV3", "OracleStatus", "PrecedenceV3", "ResourceSpecV3", "RobotSpecV3", "check_infeasibility_proof", "check_witness", "solve_canonical"]
