"""Bounded exact deployment-contract engine for the registered paper subset.

This module deliberately accepts only an explicit machine contract.  It does
not infer roles, handoffs, resources or timing from catalog prose, and it does
not use navigation/physics evidence.  A completed result is contract-level
evidence only; physical skill qualification remains a separate gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import itertools
import json
import math
import time
from typing import Any, Mapping

SCHEMA = "heterocoord.canonical_deployment.v3"
BACKEND_ID = "bounded-explicit-contract-v3"
CHECKER_BACKEND_ID = "bounded-independent-flat-checker-v1"
CHECKER_POLICY = "flat-team-role-participant-tick-v1"
DT = 0.02


class Status(str, Enum):
    DEPLOYABLE = "deployable"
    INFEASIBLE = "team_state_infeasible"
    UNVERIFIED = "feasibility_unverified"


class ContractError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ContractError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class Robot:
    robot_id: str
    capabilities: tuple[str, ...]
    roles: tuple[str, ...]


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    required_capabilities: tuple[str, ...]
    role_requirements: Mapping[str, tuple[int, int]]
    min_participants: int
    max_participants: int
    duration: int
    release: int
    deadline: int
    participant_min_offset: int
    participant_max_offset: int
    sync_window: int
    resource_demands: Mapping[str, int]


@dataclass(frozen=True)
class Resource:
    resource_id: str
    capacity: int


@dataclass(frozen=True)
class Edge:
    edge_id: str
    before: str
    after: str
    min_lag: int
    max_lag: int
    kind: str
    release_min: int | None = None
    release_max: int | None = None
    acceptance_min: int | None = None
    acceptance_max: int | None = None
    ownership_change: bool = False


@dataclass(frozen=True)
class Contract:
    task_id: str
    horizon: int
    robots: tuple[Robot, ...]
    obligations: tuple[Obligation, ...]
    resources: tuple[Resource, ...]
    edges: tuple[Edge, ...]
    mutex_pairs: tuple[tuple[str, str], ...]
    min_team: int
    max_team: int
    tick_dt_s: float = DT
    schema: str = SCHEMA

    @property
    def robot_ids(self) -> tuple[str, ...]:
        return tuple(r.robot_id for r in self.robots)

    @property
    def obligation_ids(self) -> tuple[str, ...]:
        return tuple(o.obligation_id for o in self.obligations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "task_id": self.task_id, "horizon_ticks": self.horizon,
            "tick_dt_s": self.tick_dt_s, "team_bounds": {"min": self.min_team, "max": self.max_team},
            "robots": [{"id": r.robot_id, "capabilities": list(r.capabilities), "roles": list(r.roles)} for r in self.robots],
            "obligations": [{"id": o.obligation_id, "required_capabilities": list(o.required_capabilities),
                             "role_requirements": {k: list(v) for k, v in sorted(o.role_requirements.items())},
                             "participant_bounds": [o.min_participants, o.max_participants],
                             "duration_ticks": o.duration, "release_tick": o.release, "deadline_tick": o.deadline,
                             "participant_tick_offsets": [o.participant_min_offset, o.participant_max_offset],
                             "sync_window_ticks": o.sync_window,
                             "resource_demands": dict(sorted(o.resource_demands.items()))} for o in self.obligations],
            "resources": [{"id": r.resource_id, "capacity": r.capacity} for r in self.resources],
            "temporal_edges": [{"id": e.edge_id, "before": e.before, "after": e.after, "min_lag": e.min_lag,
                                "max_lag": e.max_lag, "kind": e.kind, "release_min": e.release_min,
                                "release_max": e.release_max, "acceptance_min": e.acceptance_min,
                                "acceptance_max": e.acceptance_max, "ownership_change": e.ownership_change} for e in self.edges],
            "mutex_pairs": [list(p) for p in self.mutex_pairs],
        }

    def digest(self) -> str:
        return _digest(self.to_dict())


def from_mapping(raw: Mapping[str, Any]) -> Contract:
    if not isinstance(raw, Mapping) or raw.get("schema") != SCHEMA:
        raise ContractError(f"schema must be {SCHEMA}")
    unknown = set(raw) - {"schema", "task_id", "horizon_ticks", "tick_dt_s", "team_bounds", "robots", "obligations", "resources", "temporal_edges", "mutex_pairs"}
    if unknown: raise ContractError(f"unsupported contract keys: {sorted(unknown)}")
    if raw.get("tick_dt_s") != DT:
        raise ContractError("tick_dt_s must equal 0.02")
    task_id = str(raw.get("task_id", ""))
    if not task_id.strip(): raise ContractError("task_id is required")
    horizon = _int(raw.get("horizon_ticks"), "horizon_ticks", minimum=0)
    team = raw.get("team_bounds")
    if not isinstance(team, Mapping): raise ContractError("team_bounds is required")
    min_team, max_team = _int(team.get("min"), "team min", minimum=1), _int(team.get("max"), "team max", minimum=1)
    robots = []
    for item in raw.get("robots", ()):
        if not isinstance(item, Mapping) or not str(item.get("id", "")).strip(): raise ContractError("robot id required")
        caps, roles = tuple(str(x) for x in item.get("capabilities", ())), tuple(str(x) for x in item.get("roles", ()))
        if not caps or not roles or len(set(caps)) != len(caps) or len(set(roles)) != len(roles): raise ContractError("explicit robot capabilities/roles required")
        robots.append(Robot(str(item["id"]), caps, roles))
    if not robots or len({r.robot_id for r in robots}) != len(robots): raise ContractError("unique robots required")
    obligations = []
    for item in raw.get("obligations", ()):
        if not isinstance(item, Mapping): raise ContractError("obligation must be an object")
        oid = str(item.get("id", "")); req = tuple(str(x) for x in item.get("required_capabilities", ()))
        rr = item.get("role_requirements")
        offsets = item.get("participant_tick_offsets")
        bounds = item.get("participant_bounds")
        if not oid.strip() or not req or not isinstance(rr, Mapping) or not isinstance(offsets, (list, tuple)) or len(offsets) != 2 or not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ContractError("obligation requires capabilities, role_requirements and participant_tick_offsets")
        role_req = {}
        for role, role_bounds in rr.items():
            if not isinstance(role_bounds, (list, tuple)) or len(role_bounds) != 2: raise ContractError("role bounds malformed")
            role_req[str(role)] = (_int(role_bounds[0], "role min", minimum=0), _int(role_bounds[1], "role max", minimum=0))
        duration, release, deadline = (_int(item.get(k), k, minimum=0) for k in ("duration_ticks", "release_tick", "deadline_tick"))
        lo, hi = _int(offsets[0], "participant min offset"), _int(offsets[1], "participant max offset")
        sync = _int(item.get("sync_window_ticks"), "sync_window_ticks", minimum=0)
        pmin, pmax = _int(bounds[0], "participant min", minimum=1), _int(bounds[1], "participant max", minimum=1)
        demands = {str(k): _int(v, "resource demand", minimum=0) for k, v in dict(item.get("resource_demands", {})).items()}
        if duration <= 0 or release > horizon or deadline > horizon or lo > hi or pmin > pmax: raise ContractError("obligation timing invalid")
        if any(a > b for a, b in role_req.values()) or not role_req: raise ContractError("role requirements invalid")
        obligations.append(Obligation(oid, req, role_req, pmin, pmax, duration, release, deadline, lo, hi, sync, demands))
    if not obligations or len({o.obligation_id for o in obligations}) != len(obligations): raise ContractError("unique obligations required")
    resources = tuple(Resource(str(x["id"]), _int(x["capacity"], "resource capacity", minimum=1)) for x in raw.get("resources", ()))
    resource_ids = {r.resource_id for r in resources}
    if any(not r.resource_id.strip() for r in resources) or len(resource_ids) != len(resources): raise ContractError("resource ids must be unique and nonempty")
    if any(set(o.resource_demands) - resource_ids for o in obligations): raise ContractError("unknown resource")
    oid = {o.obligation_id for o in obligations}; edges = []
    for item in raw.get("temporal_edges", ()):
        if not isinstance(item, Mapping) or item.get("before") not in oid or item.get("after") not in oid: raise ContractError("edge endpoints invalid")
        kind = str(item.get("kind", ""));
        if kind not in {"precedence", "handoff"}: raise ContractError("edge kind must be explicit")
        mn, mx = _int(item.get("min_lag"), "min_lag", minimum=0), _int(item.get("max_lag"), "max_lag", minimum=0)
        if mn > mx: raise ContractError("lag bounds invalid")
        if kind == "handoff":
            vals = [item.get(k) for k in ("release_min", "release_max", "acceptance_min", "acceptance_max")]
            if any(type(v) is not int for v in vals) or vals[0] < 0 or vals[1] < vals[0] or vals[2] < 0 or vals[3] < vals[2]: raise ContractError("handoff release/acceptance bounds required")
            if type(item.get("ownership_change", False)) is not bool: raise ContractError("ownership_change must be boolean")
            args = (*vals, item.get("ownership_change", False))
        else: args = (None, None, None, None, False)
        edges.append(Edge(str(item.get("id", "")), str(item["before"]), str(item["after"]), mn, mx, kind, *args))
    if any(not e.edge_id.strip() for e in edges) or len({e.edge_id for e in edges}) != len(edges): raise ContractError("unique edge ids required")
    pairs = []
    for p in raw.get("mutex_pairs", ()):
        if not isinstance(p, (list, tuple)) or len(p) != 2 or p[0] not in oid or p[1] not in oid or p[0] == p[1]: raise ContractError("invalid mutex pair")
        pair = tuple(sorted((str(p[0]), str(p[1]))));
        if pair in pairs: raise ContractError("duplicate mutex pair")
        pairs.append(pair)
    if min_team > max_team or max_team > len(robots): raise ContractError("team bounds invalid")
    return Contract(task_id, horizon, tuple(robots), tuple(obligations), resources, tuple(edges), tuple(pairs), min_team, max_team)


def _eligible(c: Contract, o: Obligation, rid: str) -> bool:
    r = next(x for x in c.robots if x.robot_id == rid)
    return set(o.required_capabilities) <= set(r.capabilities) and bool(set(r.roles) & set(o.role_requirements))


def _role_maps(c: Contract, o: Obligation, members: tuple[str, ...]):
    choices = []
    for rid in members:
        r = next(x for x in c.robots if x.robot_id == rid)
        choices.append(tuple(role for role in r.roles if role in o.role_requirements))
    for selected in itertools.product(*choices):
        counts = {role: selected.count(role) for role in o.role_requirements}
        if all(lo <= counts.get(role, 0) <= hi for role, (lo, hi) in o.role_requirements.items()):
            yield dict(zip(members, selected))


def _domain(c: Contract):
    """Yield finite candidate skeletons; this is also independently replayable."""
    def obligation_domain(team, o):
        eligible = tuple(r for r in team if _eligible(c, o, r))
        min_members = max(1, o.min_participants, sum(x[0] for x in o.role_requirements.values()))
        max_members = min(len(eligible), o.max_participants, sum(x[1] for x in o.role_requirements.values()))
        for size in range(min_members, max_members + 1):
            for members in itertools.combinations(eligible, size):
                for roles in _role_maps(c, o, members):
                    for start in range(o.release, o.deadline - o.duration + 1):
                        lo = max(0, start + o.participant_min_offset)
                        hi = min(c.horizon, start + o.participant_max_offset)
                        if lo > hi:
                            continue
                        for ticks in itertools.product(range(lo, hi + 1), repeat=size):
                            if max(ticks) - min(ticks) <= o.sync_window:
                                yield members, roles, start, dict(zip(members, ticks))

    def obligation_product(team, index, partial):
        if index == len(c.obligations):
            yield tuple(partial)
            return
        for value in obligation_domain(team, c.obligations[index]):
            yield from obligation_product(team, index + 1, (*partial, value))

    for team_size in range(c.min_team, c.max_team + 1):
        for team in itertools.combinations(c.robot_ids, team_size):
            yield from ((team, values) for values in obligation_product(team, 0, ()))


def _witness(c: Contract, team, values):
    all_roles = sorted({role for robot in c.robots for role in robot.roles})
    z = {rid: int(rid in team) for rid in c.robot_ids}; u, role_links, starts, ticks = {}, {}, {}, {}
    used_roles = {rid: set() for rid in c.robot_ids}
    for o, (members, roles, start, pticks) in zip(c.obligations, values):
        u[o.obligation_id] = {rid: int(rid in members) for rid in c.robot_ids}
        role_links[o.obligation_id] = dict(roles)
        for rid, role in roles.items(): used_roles[rid].add(role)
        starts[o.obligation_id], ticks[o.obligation_id] = start, pticks
    v = {rid: {role: int(role in used_roles[rid]) for role in all_roles} for rid in c.robot_ids}
    b = {}
    for a, d in c.mutex_pairs:
        if starts[a] + next(o.duration for o in c.obligations if o.obligation_id == a) <= starts[d]:
            b[f"{a}|{d}"] = {"forward": 1, "reverse": 0}
        elif starts[d] + next(o.duration for o in c.obligations if o.obligation_id == d) <= starts[a]:
            b[f"{a}|{d}"] = {"forward": 0, "reverse": 1}
        else:
            b[f"{a}|{d}"] = {"forward": 1, "reverse": 0}
    members = {o.obligation_id: tuple(x[0]) for o, x in zip(c.obligations, values)}
    handoffs = {}
    for edge in c.edges:
        if edge.kind != "handoff":
            continue
        rel = starts[edge.before] + next(o.duration for o in c.obligations if o.obligation_id == edge.before) + edge.release_min
        acc = starts[edge.after] - edge.acceptance_min
        chosen = next(((fr, to) for fr in members[edge.before] for to in members[edge.after]
                       if (not edge.ownership_change or fr != to) and rel <= acc), None)
        if chosen is None:
            handoffs[edge.edge_id] = {"release_tick": rel, "acceptance_tick": acc,
                                      "from_robot": "", "to_robot": ""}
        else:
            handoffs[edge.edge_id] = {"release_tick": rel, "acceptance_tick": acc,
                                      "from_robot": chosen[0], "to_robot": chosen[1]}
    return {"schema": SCHEMA, "contract_digest": c.digest(), "z": z, "u": u, "v": v,
            "role_links": role_links, "tau_o": starts, "tau_rv": ticks, "b": b, "handoffs": handoffs}


def _check(c: Contract, w: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    if not isinstance(w, Mapping) or w.get("schema") != SCHEMA or w.get("contract_digest") != c.digest(): return False, ("shape_or_digest",)
    for key in ("z", "u", "v", "role_links", "tau_o", "tau_rv", "b", "handoffs"):
        if not isinstance(w.get(key), Mapping): return False, (f"missing:{key}",)
    if set(w["z"]) != set(c.robot_ids) or any(type(x) is not int or x not in (0, 1) for x in w["z"].values()): return False, ("z_domain",)
    team = {r for r, x in w["z"].items() if x};
    if not c.min_team <= len(team) <= c.max_team: return False, ("team_bounds",)
    specs = {o.obligation_id: o for o in c.obligations}; robots = {r.robot_id: r for r in c.robots}
    if set(w["u"]) != set(specs) or set(w["role_links"]) != set(specs) or set(w["tau_o"]) != set(specs) or set(w["tau_rv"]) != set(specs): return False, ("obligation_keys",)
    all_roles = {role for robot in c.robots for role in robot.roles}
    if set(w["v"]) != set(robots): return False, ("v_robot_keys",)
    for rid, row in w["v"].items():
        if not isinstance(row, Mapping) or set(row) != all_roles or any(type(x) is not int or x not in (0, 1) for x in row.values()): return False, (f"v_domain:{rid}",)
        if any(bit and (rid not in team or role not in robots[rid].roles) for role, bit in row.items()): return False, (f"v_compatibility:{rid}",)
    used_roles = {rid: set() for rid in robots}
    intervals = {}; resource_use = {}
    for oid, o in specs.items():
        u, links, start = w["u"][oid], w["role_links"][oid], w["tau_o"][oid]
        if set(u) != set(robots) or type(start) is not int or not o.release <= start <= o.deadline - o.duration: return False, (f"time:{oid}",)
        members = [rid for rid, x in u.items() if x == 1]
        if any(type(x) is not int or x not in (0, 1) for x in u.values()) or not members or any(rid not in team for rid in members): return False, (f"assignment:{oid}",)
        counts = {role: 0 for role in o.role_requirements}; ticks = w["tau_rv"][oid]
        if set(ticks) != set(members) or not isinstance(links, Mapping) or set(links) != set(members): return False, (f"participant_keys:{oid}",)
        for rid in members:
            if not set(o.required_capabilities) <= set(robots[rid].capabilities): return False, (f"capability:{oid}:{rid}",)
            role = links[rid]
            if not isinstance(role, str) or role not in robots[rid].roles or role not in o.role_requirements: return False, (f"role:{oid}:{rid}")
            counts[role] += 1; used_roles[rid].add(role)
            if type(ticks[rid]) is not int or not 0 <= ticks[rid] <= c.horizon or not start + o.participant_min_offset <= ticks[rid] <= start + o.participant_max_offset: return False, (f"participant_tick:{oid}",)
        if len(members) < o.min_participants or len(members) > o.max_participants or any(lo > counts.get(role, 0) or counts.get(role, 0) > hi for role, (lo, hi) in o.role_requirements.items()) or max(ticks.values()) - min(ticks.values()) > o.sync_window: return False, (f"coalition:{oid}",)
        intervals[oid] = (start, start + o.duration)
        for t in range(start, start + o.duration):
            for rid, demand in o.resource_demands.items(): resource_use[t, rid] = resource_use.get((t, rid), 0) + demand
    for rid in robots:
        if {role for role, bit in w["v"][rid].items() if bit} != used_roles[rid]: return False, (f"v_assignment_link:{rid}",)
    for (t, rid), demand in resource_use.items():
        cap = next(x.capacity for x in c.resources if x.resource_id == rid)
        if demand > cap: return False, (f"resource:{rid}",)
    for e in c.edges:
        gap = w["tau_o"][e.after] - (w["tau_o"][e.before] + specs[e.before].duration)
        if not e.min_lag <= gap <= e.max_lag: return False, (f"lag:{e.edge_id}",)
        if e.kind == "handoff":
            h = w["handoffs"].get(e.edge_id)
            if not isinstance(h, Mapping): return False, (f"handoff_missing:{e.edge_id}",)
            rel, acc, fr, to = h.get("release_tick"), h.get("acceptance_tick"), h.get("from_robot"), h.get("to_robot")
            if type(rel) is not int or type(acc) is not int or fr not in set(w["u"][e.before]) or to not in set(w["u"][e.after]): return False, (f"handoff_shape:{e.edge_id}",)
            if not w["u"][e.before][fr] or not w["u"][e.after][to] or not rel <= acc: return False, (f"handoff_link:{e.edge_id}",)
            if not e.release_min <= rel - (w["tau_o"][e.before] + specs[e.before].duration) <= e.release_max or not e.acceptance_min <= w["tau_o"][e.after] - acc <= e.acceptance_max: return False, (f"handoff_window:{e.edge_id}",)
            if e.ownership_change and fr == to: return False, (f"ownership:{e.edge_id}",)
    for a, d in c.mutex_pairs:
        row = w["b"].get(f"{a}|{d}") or w["b"].get(f"{d}|{a}")
        if not isinstance(row, Mapping) or set(row) != {"forward", "reverse"} or sorted(row.values()) != [0, 1]: return False, (f"mutex_literal:{a}:{d}",)
        forward = row["forward"] == 1; first, second = (a, d) if forward else (d, a)
        if intervals[first][1] > intervals[second][0]: return False, (f"mutex_order:{a}:{d}")
    return True, ()


def check_witness(contract: Contract, witness: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    from .canonical_deployment_proof_v3 import verify_witness

    return verify_witness(contract, witness)


@dataclass(frozen=True)
class Limits:
    max_candidates: int = 100_000
    max_seconds: float = 5.0


@dataclass(frozen=True)
class Result:
    status: Status
    contract_digest: str
    witness: Mapping[str, Any] | None
    proof: Mapping[str, Any] | None
    explored: int
    complete: bool
    reasons: tuple[str, ...] = ()


def solve(contract: Contract, limits: Limits = Limits()) -> Result:
    if limits.max_candidates <= 0 or limits.max_seconds <= 0: raise ContractError("positive limits required")
    started, explored = time.monotonic(), 0
    for team, values in _domain(contract):
        explored += 1
        if explored > limits.max_candidates or time.monotonic() - started > limits.max_seconds:
            return Result(Status.UNVERIFIED, contract.digest(), None, None, explored, False, ("budget_exhausted",))
        witness = _witness(contract, team, values)
        if _check(contract, witness)[0]: return Result(Status.DEPLOYABLE, contract.digest(), witness, None, explored, True)
    if explored > limits.max_candidates or time.monotonic() - started > limits.max_seconds:
        return Result(Status.UNVERIFIED, contract.digest(), None, None, explored, False, ("budget_exhausted",))
    proof = {
        "schema": SCHEMA,
        "kind": "exhaustive_no_witness",
        "contract_digest": contract.digest(),
        "backend_id": BACKEND_ID,
        "checker_backend_id": CHECKER_BACKEND_ID,
        "checker_policy": CHECKER_POLICY,
        "candidate_count": explored,
        "max_candidates": limits.max_candidates,
        "max_seconds": limits.max_seconds,
        "search_exhausted": True,
        "complete": True,
    }
    proof["proof_digest"] = _digest({k: v for k, v in proof.items() if k != "proof_digest"})
    return Result(Status.INFEASIBLE, contract.digest(), None, proof, explored, True)


def check_infeasibility_proof(contract: Contract, proof: Mapping[str, Any], limits: Limits = Limits()) -> bool:
    from .canonical_deployment_proof_v3 import verify_infeasibility_proof

    return verify_infeasibility_proof(contract, proof, limits)


__all__ = ["BACKEND_ID", "CHECKER_BACKEND_ID", "CHECKER_POLICY", "SCHEMA", "Contract", "ContractError", "Limits", "Result", "Status", "check_infeasibility_proof", "check_witness", "from_mapping", "solve"]
