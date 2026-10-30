"""Independent D1 logical probes and first-commit evidence for bar.

Negative probes are a separate candidate registry, never a modification of
the normal T1--T4 manifest. Certificates prove logical constraints only. This
module grants no physical certification, panel support or paper eligibility.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path

from .organization_v3 import (
    OrganizationCheckV3, OrganizationEventV3,
    OrganizationInstanceV3, OrganizationRunV3, evaluate_organization_run,
)
from .records_v3 import canonical_digest
from .scenarios.bar import BarScenarioAdapter

DEFAULT_CASES = Path(__file__).resolve().parent / 'tasks/bar/bar_d1_cases.v1.json'
CASE_IDS = (
    'bar_infeasible_no_stair_capability',
    'bar_infeasible_insufficient_joint_manipulators',
    'bar_infeasible_workbench_time_window',
    'bar_infeasible_unreachable_inner_handoff',
)
CATEGORIES = ('capability', 'role', 'time', 'joint', 'resource', 'mutex', 'deadline')


def _integer(value, name, *, positive=False):
    if type(value) is not int or value < int(positive):
        raise ValueError(f'{name} must be a {"positive" if positive else "nonnegative"} integer')
    return value


def _roster(adapter, case):
    manifest = adapter.manifest()
    models = {row['robot_id']: row for row in manifest['robot_roster']}
    if len(case['roster']) != 3 or len({r['robot_id'] for r in case['roster']}) != 3:
        raise ValueError('D1 probes require three distinct robot instance IDs')
    roster = []
    for entry in case['roster']:
        model = models[entry['model_ref']]
        start = 'ground::' + manifest['spawn_points'][entry['start_role']]
        row = dict(entry, capabilities=sorted(model['capabilities']), start_node=start,
                   speed_mps=model['speed_mps'])
        if 'logical_payload_limit_kg' in row:
            limit = row['logical_payload_limit_kg']
            if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit < 0:
                raise ValueError('logical payload limit must be finite and nonnegative')
        roster.append(row)
    return roster


def _reachable(graph, source, capabilities, extra_requirements=None):
    """Complete graph search retaining the map's directions and stair rules."""
    seen, stack = {source}, [source]
    caps = set(capabilities)
    while stack:
        node = stack.pop()
        for target, _ in graph.adjacency[node]:
            if (target not in seen and graph.edge_allowed(node, target, caps)
                    and set((extra_requirements or {}).get((node, target), ())).issubset(caps)):
                seen.add(target)
                stack.append(target)
    return sorted(seen)


def bar_d1_certificates(adapter=None, *, cases_path=DEFAULT_CASES):
    adapter = adapter or BarScenarioAdapter()
    policy = json.loads(Path(cases_path).read_text())
    if policy.get('schema') != 'heterocoord.bar.d1-cases.v1':
        raise ValueError('unsupported bar D1 registry')
    cases = policy['cases']
    if tuple(row['case_id'] for row in cases) != CASE_IDS:
        raise ValueError('four unique registered D1 cases required')
    submission = _integer(policy['submission_deadline_tick'], 'submission deadline')
    assessment = _integer(policy['assessment_deadline_tick'], 'assessment deadline')
    if submission > assessment:
        raise ValueError('assessment deadline precedes submission')
    graph = adapter.semantic_graph()  # Includes the ordinary map file/hash audit.
    manifest = adapter.manifest()
    topology = json.loads((adapter.map_root / 'topology.json').read_text())
    edges = {row['id']: row for row in topology['edges']}
    results = []
    for case in cases:
        roster = _roster(adapter, case)
        proof = {}
        kind = case['proof_type']
        if kind == 'no_stair_route':
            targets = case.get('targets', ())
            stair_edge_ids = case.get('stair_edges', ())
            if (not targets or len(set(targets)) != len(targets)
                    or not stair_edge_ids or len(set(stair_edge_ids)) != len(stair_edge_ids)):
                raise ValueError('no-stair probe requires unique nonempty targets and stair witnesses')
            required = set(case['required_capabilities'])
            eligible = [r['robot_id'] for r in roster if required.issubset(r['capabilities'])]
            bindings = {label: list(graph.resolve(label, 'ground')) for label in targets}
            reachable = {r['robot_id']: _reachable(graph, r['start_node'], r['capabilities']) for r in roster}
            if any(edge_id not in edges for edge_id in stair_edge_ids):
                raise ValueError('no-stair probe references an unknown stair witness')
            witnesses = [edges[edge_id] for edge_id in stair_edge_ids]
            if eligible or any(node in nodes for nodes in reachable.values() for targets in bindings.values() for node in targets):
                raise ValueError('no-stair probe is solvable under the supplied map/team')
            if any(edge.get('traversal_mode') != 'stairs'
                   or 'stair_capable' not in edge.get('required_controller_capabilities', ()) for edge in witnesses):
                raise ValueError('stair witness does not require stair_capable')
            proof = dict(eligible_robot_ids=eligible, required_distinct_holders=1,
                         target_bindings=bindings, reachable_nodes=reachable, stair_edges=witnesses)
        elif kind == 'joint_cardinality':
            required = set(case['required_capabilities'])
            threshold = case['minimum_payload_kg']
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold <= 0:
                raise ValueError('positive finite payload threshold required')
            count = _integer(case['required_distinct_holders'], 'required holders', positive=True)
            _integer(case['joint_sync_window_ticks'], 'joint synchronization window', positive=True)
            target = graph.resolve(case['target'], 'ground')
            eligible = [r['robot_id'] for r in roster
                        if required.issubset(r['capabilities'])
                        and r['logical_payload_limit_kg'] >= threshold
                        and set(target).intersection(_reachable(graph, r['start_node'], r['capabilities']))]
            if len(eligible) >= count:
                raise ValueError('joint probe is solvable under the supplied team')
            proof = dict(eligible_robot_ids=eligible, required_distinct_holders=count,
                         minimum_payload_kg=threshold, joint_sync_window_ticks=case['joint_sync_window_ticks'])
        elif kind == 'serial_time_window':
            resource = next(r for r in manifest['resources'] if r['resource_id'] == case['resource_id'])
            capacity = _integer(case['resource_capacity'], 'resource capacity', positive=True)
            if capacity != 1 or capacity != resource['capacity']:
                raise ValueError('serial-time proof requires the authored capacity-one workbench')
            duration = round(resource['service_s'] / manifest['logic_clock']['dt_s'])
            deadline = _integer(case['deadline_tick'], 'window deadline', positive=True)
            _integer(case['max_lag_ticks'], 'max lag')
            if len({j['job_id'] for j in case['jobs']}) != len(case['jobs']):
                raise ValueError('unique workbench jobs required')
            lower_bound = 0
            for job in case['jobs']:
                lo, hi = job['duration_ticks']
                _integer(lo, 'duration minimum', positive=True)
                _integer(hi, 'duration maximum', positive=True)
                if lo != duration or hi < lo or job['release_tick'] != 0 or job['deadline_tick'] != deadline:
                    raise ValueError('workbench duration/window does not match public logical model')
                graph.resolve(job['point'], 'ground')
                lower_bound += lo
            job_ids = {j['job_id'] for j in case['jobs']}
            if any(a not in job_ids or b not in job_ids or a == b for a, b in case['precedence']):
                raise ValueError('invalid workbench precedence')
            if lower_bound <= deadline:
                raise ValueError('workbench time window has no infeasibility proof')
            proof = dict(resource_capacity=capacity, serial_lower_bound_ticks=lower_bound,
                         deadline_tick=deadline, jobs=case['jobs'], precedence=case['precedence'],
                         max_lag_ticks=case['max_lag_ticks'])
        elif kind == 'restricted_reachability':
            restrictions = {}
            for permission in case['edge_permissions']:
                edge = edges[permission['edge_id']]
                source, target = 'ground::' + edge['from'], 'ground::' + edge['to']
                restrictions[(source, target)] = (permission['required_capability'],)
                if not edge.get('directed', False):
                    restrictions[(target, source)] = (permission['required_capability'],)
            targets = graph.resolve(case['target'], 'ground')
            graph.resolve(case['non_substitutable_external_point'], 'ground')
            reachable = {r['robot_id']: _reachable(graph, r['start_node'], r['capabilities'], restrictions) for r in roster}
            if any(set(targets).intersection(nodes) for nodes in reachable.values()):
                raise ValueError('inner handoff is reachable under the supplied permissions/team')
            proof = dict(target_bindings=list(targets), reachable_nodes=reachable,
                         edge_permissions=case['edge_permissions'], restricted_graph_digest=canonical_digest({
                             'topology': topology, 'permissions': case['edge_permissions']}))
        else:
            raise ValueError('unsupported D1 proof type')
        cert = dict(schema='heterocoord.bar.d1-certificate.v1', case_id=case['case_id'],
                    status='team-state-infeasible', certification_scope='logical_contract_only',
                    parameter_status=policy['parameter_status'], physical_qualified=False,
                    paper_score_eligible=False, rankable=False, native_algorithm=False,
                    registry_digest=canonical_digest(policy), contract=deepcopy(case), roster=roster,
                    roster_digest=canonical_digest(roster), map_revision=manifest['semantic_map']['revision'],
                    semantic_binding_digest=manifest['semantic_map']['semantic_binding_digest'],
                    task_manifest_digest=canonical_digest(manifest), proof=proof,
                    submission_deadline_tick=submission, assessment_deadline_tick=assessment,
                    expected_action='not-deployable_safe-stop')
        cert['certificate_digest'] = canonical_digest(cert)
        results.append(cert)
    return tuple(results)


def verify_bar_d1_certificate(certificate, adapter=None, *, cases_path=DEFAULT_CASES):
    fresh = {c['case_id']: c for c in bar_d1_certificates(adapter, cases_path=cases_path)}
    if certificate.get('case_id') not in fresh or certificate != fresh[certificate['case_id']]:
        raise ValueError('stale, tampered or unregistered bar D1 certificate')
    return True


def bar_d1_infeasible_specs(adapter=None, *, cases_path=DEFAULT_CASES):
    return tuple(OrganizationInstanceV3(
        instance_id='bar/d1/' + cert['case_id'], cell=cert['case_id'],
        feasibility='team_state_infeasible', certificate_digest=cert['certificate_digest'],
        contract_digest=canonical_digest(cert['contract']),
        physical_model_digest=canonical_digest({'scope': 'logical_only', 'certificate': cert['certificate_digest']}),
        submission_deadline_tick=cert['submission_deadline_tick'],
        assessment_deadline_tick=cert['assessment_deadline_tick'], stream_id='bar-d1-logical-probe',
    ) for cert in bar_d1_certificates(adapter, cases_path=cases_path))


def bar_d1_deployable_spec(*, tier=4, adapter=None):
    """Normal tasks have witness runs but no independent deployability oracle.

    Keep them excluded from D1 scoring until an independent certificate and
    concrete per-obligation check registry replace this registration stub.
    """
    adapter = adapter or BarScenarioAdapter()
    contract = adapter.contract(tier=tier)
    return OrganizationInstanceV3(
        instance_id=f'bar/d1/normal/t{tier}', cell=f'bar_t{tier}',
        feasibility='feasibility_unverified', certificate_digest='pending-independent-deployability-certificate',
        contract_digest=contract['digest'], physical_model_digest=canonical_digest({'scope': 'logical_only', 'contract': contract}),
        submission_deadline_tick=100, assessment_deadline_tick=200,
        checks=tuple(OrganizationCheckV3(f'bar.t{tier}.{c}', c, ('independent_evaluator',)) for c in CATEGORIES),
        stream_id='bar-d1-normal-unverified',
    )


class BarD1Journal:
    """Evaluator-owned append-only first-commit journal; no self-reported stop.

    The caller must record every dispatch, including rejected attempts. This
    diagnostic bridge is not a certified isolation boundary. Candidate plans
    and later commits remain visible without replacing the first commit.
    """
    def __init__(self, spec, *, certificate=None, adapter=None, cases_path=DEFAULT_CASES):
        self.spec = spec
        self._expected_roster = None
        if spec.feasibility == 'team_state_infeasible':
            if certificate is None:
                raise ValueError('validated bar D1 certificate required')
            verify_bar_d1_certificate(certificate, adapter, cases_path=cases_path)
            if spec.certificate_digest != certificate['certificate_digest']:
                raise ValueError('D1 instance and certificate mismatch')
            self._expected_roster = {r['robot_id']: r for r in certificate['roster']}
        self._events = []
        self._payloads = {}
        self._evidence = {}

    @property
    def events(self):
        return tuple(self._events)

    def _append(self, tick, kind, evidence, **kwargs):
        _integer(tick, 'journal tick')
        if self._events and tick < self._events[-1].tick:
            raise ValueError('journal cannot move backwards')
        digest = canonical_digest(evidence)
        event = OrganizationEventV3(f'bar-d1-{len(self._events)}', tick, len(self._events), kind, digest, **kwargs)
        self._events.append(event)
        self._evidence[digest] = deepcopy(evidence)
        return event

    def submit_plan(self, payload, *, tick, committed=True):
        # Syntactic parsing alone earns nothing; external check facts bind the
        # first committed digest below, never the most recent valid plan.
        digest = canonical_digest(payload)
        parsable = isinstance(payload, dict) and payload.get('schema') == 'heterocoord.bar.first-plan.v1'
        self._payloads[digest] = deepcopy(payload)
        return self._append(tick, 'commit' if committed else 'candidate', payload,
                            plan_digest=digest, parsable=parsable)

    def declare(self, declaration, *, tick):
        return self._append(tick, 'declaration', {'declaration': declaration}, declaration=declaration)

    def dispatch(self, robot_id, task_id, *, tick):
        return self._append(tick, 'dispatch', {'robot_id': robot_id, 'task_id': task_id})

    def observe_safe_stop(self, runtime):
        # Read the evaluator-owned runtime, never a method-provided boolean.
        state = dict(robots={rid: {'status': r.status, 'edge_progress_m': r.edge_progress_m,
                     'task_id': r.task_id, 'load_id': r.load_id, 'domain': r.spec.domain,
                     'active_capabilities': sorted(r.active_capabilities), 'node_id': r.node_id,
                     'start_node': r.spec.start_node, 'distance_m': r.distance_m}
                    for rid, r in runtime.robot_states.items()},
                     resources=runtime.resources.snapshot(), terminal=runtime.terminal,
                     runtime_history=deepcopy(runtime.events))
        forbidden = {'task_assigned', 'task_started', 'task_completed', 'dispatch',
                     'logical_skill_session', 'object_picked', 'object_placed',
                     'handoff_accept', 'handoff_release_ready', 'joint_action_completed'}
        verified = (not state['terminal']
                    and not any(e.get('kind') in forbidden for e in state['runtime_history'])
                    and all(r['status'] in {'idle', 'hold', 'stopped'} and not r['task_id']
                        and not r['load_id'] and r['edge_progress_m'] == 0
                        and r['node_id'] == r['start_node'] and r['distance_m'] == 0
                        for r in state['robots'].values())
                    and not any(state['resources']['active'].values())
                    and not any(state['resources']['queues'].values()))
        if self._expected_roster is not None:
            verified = verified and set(state['robots']) == set(self._expected_roster)
            verified = verified and all(r['domain'] == 'ground'
                and r['active_capabilities'] == self._expected_roster[rid]['capabilities']
                and r['start_node'] == self._expected_roster[rid]['start_node']
                for rid, r in state['robots'].items() if rid in self._expected_roster)
        return self._append(runtime.tick, 'safe_stop', state, safe_stop_verified=verified)

    def to_run(self, *, system_id, repeat_id, observed_through_tick, infrastructure_valid,
               checks=(), checked_plan_digest='', checked_physical_model_digest=''):
        _integer(observed_through_tick, 'observed journal prefix')
        if self._events and observed_through_tick < self._events[-1].tick:
            raise ValueError('observation prefix does not cover the journal')
        return OrganizationRunV3(self.spec.instance_id, repeat_id, system_id, self.spec.digest(),
            canonical_digest(self.export()), infrastructure_valid, observed_through_tick,
            events=self.events, checks=tuple(checks), checked_plan_digest=checked_plan_digest,
            checked_physical_model_digest=checked_physical_model_digest)

    def export(self):
        return deepcopy(dict(schema='heterocoord.bar.d1-journal.v1', instance_digest=self.spec.digest(),
                             events=[asdict(e) for e in self._events], plans=self._payloads,
                             evidence=self._evidence, paper_score_eligible=False))


def evaluate_bar_d1_declaration(spec, run, *, journal=None):
    """Require a live evaluator-owned journal matching all run evidence.

    A run JSON or an algorithm's safe-stop claim alone is never sufficient.
    Cross-process readback/isolation certification remains a separate gate.
    """
    if (not isinstance(journal, BarD1Journal) or journal.spec.digest() != spec.digest()
            or run.events != journal.events
            or run.evidence_digest != canonical_digest(journal.export())):
        raise ValueError('D1 run requires its evaluator-owned journal and matching evidence')
    result = evaluate_organization_run(spec, run)
    return dict(result, evidence_scope='logical_diagnostic_only', paper_score_eligible=False,
                physical_qualified=False, rankable=False)


def main():
    import argparse
    import hashlib
    parser = argparse.ArgumentParser(description='Audit bar D1 logical probe contracts; no algorithm scores.')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'results/bar_d1_contract_audit.v1.json')
    args = parser.parse_args()
    adapter = BarScenarioAdapter()
    certificates = bar_d1_certificates(adapter)
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__), DEFAULT_CASES, adapter.manifest_path,
             root / 'benchmark/scenarios/bar.py', root / 'benchmark/organization_v3.py',
             root / 'benchmark/hetero_diagnostic_runtime_v1.py']
    report = dict(schema='heterocoord.bar.d1-contract-audit.v1', paper_commit='0ffd470f',
                  certificates=certificates, infeasible_instances=[asdict(s) for s in bar_d1_infeasible_specs(adapter)],
                  normal_instances=[asdict(bar_d1_deployable_spec(tier=t, adapter=adapter)) for t in range(1, 5)],
                  evidence_scope='logical_probe_contracts_no_tested_algorithm_runs',
                  paper_score_eligible=False, physical_qualified=False, rankable=False,
                  source_hashes={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'certificates': len(certificates), 'normal_D1': 'feasibility_unverified',
                      'paper_score_eligible': False}))


if __name__ == '__main__':
    main()
