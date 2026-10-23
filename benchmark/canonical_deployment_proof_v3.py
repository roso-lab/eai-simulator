"""Independent bounded replay checker for canonical deployment proofs.

No producer private search/constructor/checker is imported.  The checker uses a
flat finite-domain enumeration and fail-closes on candidate, domain-item, or
wall-clock exhaustion.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import itertools, json, math, time
from typing import Any, Mapping
from .canonical_deployment_v3 import BACKEND_ID, CHECKER_BACKEND_ID, CHECKER_POLICY, Contract, Limits, SCHEMA

def _digest(v: Any) -> str:
    return sha256(json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

@dataclass(frozen=True)
class CheckerLimits:
    max_candidates: int = 100_000
    max_seconds: float = 5.0
    max_domain_items: int = 1_000_000

class _BudgetExceeded(Exception): pass
class _Budget:
    def __init__(self, limits: CheckerLimits):
        if type(limits.max_candidates) is not int or limits.max_candidates <= 0 or type(limits.max_seconds) not in (int,float) or not math.isfinite(limits.max_seconds) or limits.max_seconds <= 0 or type(limits.max_domain_items) is not int or limits.max_domain_items <= 0: raise ValueError("invalid checker limits")
        self.limits, self.started, self.candidates, self.domain_items = limits, time.monotonic(), 0, 0
    def item(self):
        if time.monotonic() - self.started >= self.limits.max_seconds: raise _BudgetExceeded
        self.domain_items += 1
        if self.domain_items > self.limits.max_domain_items: raise _BudgetExceeded
    def candidate(self):
        self.item(); self.candidates += 1
        if self.candidates > self.limits.max_candidates: raise _BudgetExceeded

def _limits(value):
    if isinstance(value, CheckerLimits): return value
    n, s = getattr(value, "max_candidates", None), getattr(value, "max_seconds", None)
    if type(n) is not int or not isinstance(s, (int,float)): raise ValueError("limits missing fields")
    return CheckerLimits(n, s, max(1000, n * 10))

def _robot(c, rid):
    return next(r for r in c.robots if r.robot_id == rid)

def _lazy_product(domains, budget, prefix=()):
    """Do not let itertools.product materialize a potentially huge tick range."""
    budget.item()
    if len(prefix) == len(domains):
        yield prefix
        return
    for value in domains[len(prefix)]:
        budget.item()
        yield from _lazy_product(domains, budget, (*prefix, value))


def _flat_values(c, team, o, b):
    # Deliberately broader than the producer domain: no capability, coalition,
    # role-compatibility, role-count or synchronization pruning. The independent
    # predicate below decides these constraints. Each robot is either absent or
    # carries one of the explicitly registered obligation roles (subset policy).
    roles = tuple(o.role_requirements)
    for assignment in _lazy_product([(None, *roles)] * len(team), b):
        members = tuple(r for r, role in zip(team, assignment) if role is not None)
        if not members:
            continue
        links = {r: role for r, role in zip(team, assignment) if role is not None}
        for start in range(o.release, o.deadline - o.duration + 1):
            b.item()
            low = max(0, start + o.participant_min_offset)
            high = min(c.horizon, start + o.participant_max_offset)
            for ticks in _lazy_product([range(low, high + 1)] * len(members), b):
                yield members, links, start, dict(zip(members, ticks))

def _flat_candidates(c,b):
    def rec(team,i,partial):
        b.item()
        if i == len(c.obligations): b.candidate(); yield team,tuple(partial); return
        for value in _flat_values(c,team,c.obligations[i],b): yield from rec(team,i+1,partial+[value])
    for n in range(c.min_team,c.max_team+1):
        b.item()
        for team in itertools.combinations(c.robot_ids,n): b.item(); yield from rec(team,0,[])

def _candidate(c,team,values,budget):
    roles=sorted({x for r in c.robots for x in r.roles}); u={}; links={}; starts={}; ticks={}; used={r:set() for r in c.robot_ids}
    for o,(members,rm,s,pt) in zip(c.obligations,values):
        budget.item()
        u[o.obligation_id]={r:int(r in members) for r in c.robot_ids}; links[o.obligation_id]=rm; starts[o.obligation_id]=s; ticks[o.obligation_id]=pt
        for r,x in rm.items(): used[r].add(x)
    b={}; dur={o.obligation_id:o.duration for o in c.obligations}
    for a,d in c.mutex_pairs: b[f"{a}|{d}"]={"forward":int(starts[a]+dur[a]<=starts[d]),"reverse":int(starts[d]+dur[d]<=starts[a])}
    for a,d in c.mutex_pairs:
        if b[f"{a}|{d}"]["forward"]+b[f"{a}|{d}"]["reverse"] != 1: b[f"{a}|{d}"]={"forward":1,"reverse":0}
    hs={}
    for e in c.edges:
        budget.item()
        if e.kind!='handoff': continue
        rel=starts[e.before]+dur[e.before]+e.release_min; acc=starts[e.after]-e.acceptance_min; pair=next(((x,y) for x in links[e.before] for y in links[e.after] if (not e.ownership_change or x!=y) and rel<=acc),None)
        hs[e.edge_id]={"release_tick":rel,"acceptance_tick":acc,"from_robot":pair[0] if pair else "","to_robot":pair[1] if pair else ""}
    return {"schema":SCHEMA,"contract_digest":c.digest(),"z":{r:int(r in team) for r in c.robot_ids},"u":u,"v":{r:{x:int(x in used[r]) for x in roles} for r in c.robot_ids},"role_links":links,"tau_o":starts,"tau_rv":ticks,"b":b,"handoffs":hs}

def _valid(c,w,budget):
    budget.item()
    if not isinstance(w,Mapping) or w.get("schema")!=SCHEMA or w.get("contract_digest")!=c.digest(): return False
    if any(not isinstance(w.get(k),Mapping) for k in ("z","u","v","role_links","tau_o","tau_rv","b","handoffs")): return False
    ids=set(c.robot_ids); team={r for r,x in w['z'].items() if x}; robots={r.robot_id:r for r in c.robots}; obs={o.obligation_id:o for o in c.obligations}
    if set(w['z'])!=ids or not c.min_team<=len(team)<=c.max_team or any(type(x)is not int or x not in (0,1) for x in w['z'].values()): return False
    if any(set(w[k])!=set(obs) for k in ('u','role_links','tau_o','tau_rv')) or set(w['v'])!=ids: return False
    allroles={x for r in c.robots for x in r.roles}; used={r:set() for r in ids}; intervals={}; usage={}
    for r,row in w['v'].items():
        budget.item()
        if not isinstance(row,Mapping) or set(row)!=allroles or any(type(x)is not int or x not in (0,1) for x in row.values()) or any(x and (r not in team or role not in robots[r].roles) for role,x in row.items()): return False
    for oid,o in obs.items():
        budget.item()
        u,links,s,t=w['u'][oid],w['role_links'][oid],w['tau_o'][oid],w['tau_rv'][oid]
        if any(not isinstance(row, Mapping) for row in (u, links, t)): return False
        members=[r for r,x in u.items() if x]
        if set(u)!=ids or any(type(x)is not int or x not in (0,1) for x in u.values()) or not members or any(r not in team for r in members) or set(links)!=set(members) or set(t)!=set(members) or type(s)is not int or not o.release<=s<=o.deadline-o.duration: return False
        counts={role:0 for role in o.role_requirements}
        for r in members:
            budget.item()
            role=links[r]; robot=robots[r]
            if role not in robot.roles or role not in o.role_requirements or not set(o.required_capabilities)<=set(robot.capabilities) or type(t[r]) is not int or not 0<=t[r]<=c.horizon or not s+o.participant_min_offset<=t[r]<=s+o.participant_max_offset:return False
            counts[role]+=1;used[r].add(role)
        if not o.min_participants<=len(members)<=o.max_participants or any(not a<=counts.get(k,0)<=b for k,(a,b) in o.role_requirements.items()) or max(t.values())-min(t.values())>o.sync_window:return False
        intervals[oid]=(s,s+o.duration)
        # Half-open interval capacity via boundary deltas, independent of the
        # producer's per-tick occupancy loop and proportional to contract size.
        for resource,demand in o.resource_demands.items():
            budget.item()
            events = usage.setdefault(resource, {})
            events[s] = events.get(s, 0) + demand
            events[s + o.duration] = events.get(s + o.duration, 0) - demand
    if any({role for role,x in w['v'][r].items() if x}!=used[r] for r in ids):return False
    caps={r.resource_id:r.capacity for r in c.resources}
    for resource, events in usage.items():
        occupancy = 0
        for tick in sorted(events):
            budget.item()
            occupancy += events[tick]
            if occupancy > caps[resource]: return False
    for e in c.edges:
        budget.item()
        gap=w['tau_o'][e.after]-(w['tau_o'][e.before]+obs[e.before].duration)
        if not e.min_lag<=gap<=e.max_lag:return False
        if e.kind=='handoff':
            h=w['handoffs'].get(e.edge_id)
            if not isinstance(h,Mapping):return False
            rel,acc,fr,to=(h.get(k) for k in ('release_tick','acceptance_tick','from_robot','to_robot'))
            if type(rel)is not int or type(acc)is not int or fr not in ids or to not in ids or not w['u'][e.before][fr] or not w['u'][e.after][to] or rel>acc:return False
            if not e.release_min<=rel-(w['tau_o'][e.before]+obs[e.before].duration)<=e.release_max or not e.acceptance_min<=w['tau_o'][e.after]-acc<=e.acceptance_max or (e.ownership_change and fr==to):return False
    for a,d in c.mutex_pairs:
        budget.item()
        row=w['b'].get(f'{a}|{d}') or w['b'].get(f'{d}|{a}')
        if not isinstance(row,Mapping) or set(row)!={'forward','reverse'} or any(type(x) is not int for x in row.values()) or sorted(row.values())!=[0,1]:return False
        first,second=(a,d) if row['forward'] else (d,a)
        if intervals[first][1]>intervals[second][0]:return False
    return True

def verify_infeasibility_proof(contract: Contract, proof: Mapping[str,Any], limits: Limits|CheckerLimits=Limits()) -> bool:
    if not isinstance(proof,Mapping) or any((proof.get(k)!=v) for k,v in (("schema",SCHEMA),("kind","exhaustive_no_witness"),("contract_digest",contract.digest()),("backend_id",BACKEND_ID),("checker_backend_id",CHECKER_BACKEND_ID),("checker_policy",CHECKER_POLICY))):return False
    if proof.get('search_exhausted') is not True or proof.get('complete') is not True or type(proof.get('candidate_count')) is not int or proof['candidate_count']<0:return False
    if type(proof.get('max_candidates')) is not int or proof['max_candidates'] <= 0:return False
    if type(proof.get('max_seconds')) not in (int,float) or not math.isfinite(proof['max_seconds']) or proof['max_seconds'] <= 0:return False
    if proof['candidate_count'] > proof['max_candidates']:return False
    try:
        b=_Budget(_limits(limits))
        if proof.get('proof_digest')!=_digest({k:v for k,v in proof.items() if k!='proof_digest'}):return False
        for team,values in _flat_candidates(contract,b):
            if _valid(contract,_candidate(contract,team,values,b),b):return False
        b.item()
    except (_BudgetExceeded,ValueError,KeyError,TypeError,OverflowError,RecursionError):return False
    return True

def verify_witness(contract: Contract, witness: Mapping[str, Any]):
    try:
        budget = _Budget(CheckerLimits())
        valid = _valid(contract, witness, budget)
        budget.item()
    except (_BudgetExceeded, KeyError, TypeError, ValueError, IndexError, RecursionError):
        valid = False
    return (True, ()) if valid else (False, ("independent_checker_rejected",))
__all__=['CheckerLimits','verify_infeasibility_proof','verify_witness']
