"""Frozen task and evaluator boundaries; imports no simulator or algorithm."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from .models import canonical_digest

CONTRACT_FIELDS = (
    'schema', 'task_id', 'scene_id', 'domain', 'task_version', 'objectives',
    'dag', 'milestones', 'capability_requirements', 'role_constraints',
    'observation_permissions', 'resource_locks', 'safety_constraints',
    'fixed_timestep', 'horizon', 'deadlines', 'termination_predicates',
    'treatment', 'nuisance_factors', 'intervention_points', 'validity_thresholds',
    'feasibility_oracle', 'skill_adapter_version', 'non_target_budget',
    'public_exogenous_events',
)
OUTCOME_STATUSES = (
    'valid_terminal', 'right_censored', 'not_evaluable', 'invalid_instance',
    'feasibility_unverified', 'team_state_infeasible', 'pair_invalid',
    'sham_invalid', 'safe_stop', 'method_timeout', 'malformed_action',
    'crashed', 'budget_overrun', 'missing_response', 'undefined_zero_progress',
    'infrastructure_invalid',
)
METHOD_FAILURES = {'method_timeout', 'malformed_action', 'crashed', 'budget_overrun', 'missing_response'}
CHECKPOINT_FIELDS = (
    'simulator_step', 'sim_time_s', 'scene_state', 'robots', 'objects',
    'controllers', 'task_dag', 'milestones', 'resource_locks', 'object_ownership',
    'message_queues', 'unconsumed_messages', 'exogenous_event_counters',
    'policy_state', 'rng_state', 'communication_rng_state',
)
STEP_FIELDS = (
    'simulator_step', 'sim_time_s', 'commands', 'robots', 'objects',
    'phase_state', 'task_state', 'milestones', 'collision_contact',
    'safety_state', 'active', 'completed', 'terminated',
)


def finite_number(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be finite and >= {minimum}')
    return value


@dataclass(frozen=True)
class TaskContract:
    """The encoded source is immutable; callers receive detached dictionaries."""
    encoded: str

    @classmethod
    def freeze(cls, data: Mapping[str, Any]):
        data = dict(data)
        missing = set(CONTRACT_FIELDS) - data.keys()
        if missing:
            raise ValueError(f'task contract missing fields: {sorted(missing)}')
        for name in ('schema', 'task_id', 'scene_id', 'domain', 'task_version', 'skill_adapter_version'):
            if not isinstance(data[name], str) or not data[name]:
                raise ValueError(f'contract {name} must be nonempty text')
        finite_number(data['fixed_timestep'], 'fixed_timestep', 1e-12)
        if type(data['horizon']) is not int or data['horizon'] < 1:
            raise ValueError('horizon must be positive integer ticks')
        dag = data['dag']
        ids = [n['id'] for n in dag]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('DAG nodes must be nonempty and unique')
        remaining = {n['id']: set(n['prerequisites']) for n in dag}
        if any(deps - set(ids) for deps in remaining.values()):
            raise ValueError('unknown DAG prerequisite')
        visited = set()
        while remaining:
            ready = {key for key, deps in remaining.items() if deps <= visited}
            if not ready:
                raise ValueError('cyclic DAG')
            visited |= ready
            remaining = {k: v for k, v in remaining.items() if k not in ready}
        milestones = data['milestones']
        mid = [m['id'] for m in milestones]
        if not mid or len(mid) != len(set(mid)):
            raise ValueError('milestones must be nonempty and unique')
        for m in milestones:
            finite_number(m['weight'], 'milestone weight')
            if type(m['stable_window']) is not int or m['stable_window'] < 1 or not m.get('predicate'):
                raise ValueError('milestone predicate and positive stable_window required')
        if sum(m['weight'] for m in milestones) <= 0:
            raise ValueError('milestone denominator must be positive')
        oracle = data['feasibility_oracle']
        if oracle.get('status') not in {'deployable', 'team_state_infeasible', 'feasibility_unverified'}:
            raise ValueError('unknown feasibility oracle status')
        if oracle['status'] != 'feasibility_unverified' and not oracle.get('witness_proof_hash'):
            raise ValueError('certified feasibility requires witness/proof hash')
        if set(data['treatment']) != {'fact', 'reference', 'sham', 'intervention'}:
            raise ValueError('register treatment for all four arms')
        for name in ('restore_max_error', 'sham_max_error', 'reference_denominator_floor'):
            finite_number(data['validity_thresholds'][name], name)
        return cls(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False))

    @property
    def data(self):
        return json.loads(self.encoded)

    @property
    def digest(self):
        return canonical_digest(self.data)


def evaluator_result(result: Any, contract: TaskContract, scope: str):
    """Accept external evidence, never infer success from step/exit/assignment."""
    if not isinstance(result, Mapping):
        return {'status': 'not_evaluable', 'failure_reason': 'external evaluator missing', 'value': None}
    result = json.loads(json.dumps(result, allow_nan=False))
    status = result.get('status', 'not_evaluable')
    if status not in OUTCOME_STATUSES:
        raise ValueError(f'unknown evaluator status: {status}')
    if status in {'valid_terminal', 'safe_stop', 'team_state_infeasible'}:
        required = {'evaluator_version', 'evidence_hash', 'evidence_refs', 'milestones',
                    'terminal_predicate', 'safety_predicate', 'feasibility_predicate', 'evidence_source'}
        if required - result.keys() or not result.get('evidence_refs'):
            return {'status': 'not_evaluable', 'failure_reason': 'external evidence incomplete',
                    'value': None, 'raw_evaluator_result': result}
        if scope == 'measured' and result['evidence_source'] != 'external_evaluator':
            return {'status': 'not_evaluable', 'failure_reason': 'measured result requires external evaluator', 'value': None}
        expected = {m['id']: m for m in contract.data['milestones']}
        if set(result['milestones']) != set(expected):
            raise ValueError('evaluator changed frozen milestone set')
        for key, evidence in result['milestones'].items():
            if type(evidence.get('predicate')) is not bool:
                raise ValueError('milestone predicate must be boolean')
            if evidence.get('stable_window') != expected[key]['stable_window']:
                raise ValueError('evaluator changed stability window')
            if evidence['predicate'] and (evidence.get('first_satisfied_time_s') is None or not evidence.get('evidence_refs')):
                raise ValueError('satisfied milestone needs time and evidence')
        if status == 'safe_stop' and result['safety_predicate'] is not True:
            raise ValueError('safe_stop requires certified safety')
        if status == 'team_state_infeasible' and not result.get('infeasibility_proof_hash'):
            raise ValueError('team_state_infeasible requires proof')
    return {**result, 'status': status}
