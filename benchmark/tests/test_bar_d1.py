from copy import deepcopy
from dataclasses import replace
import json

import pytest

from benchmark.bar_d1 import (
    BarD1Journal, CASE_IDS, DEFAULT_CASES, bar_d1_certificates,
    bar_d1_deployable_spec, bar_d1_infeasible_specs,
    evaluate_bar_d1_declaration, verify_bar_d1_certificate,
)
from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime, EpisodeSpec, RobotSpec
from benchmark.organization_v3 import OrganizationCheckObservationV3, OrganizationEventV3, OrganizationRunV3
from benchmark.scenarios.bar import BarScenarioAdapter


def policy_file(tmp_path, mutate):
    policy = json.loads(DEFAULT_CASES.read_text())
    mutate(policy)
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps(policy))
    return path


def stopped_runtime(cert):
    a = BarScenarioAdapter()
    robots = tuple(RobotSpec(r['robot_id'], 'ground', r['start_node'],
                            capabilities=tuple(r['capabilities'])) for r in cert['roster'])
    # An evaluator-owned, quiescent initial team state, not a method's report.
    return DiagnosticRuntime(EpisodeSpec(a.semantic_graph(), robots, (), resources=a.build_episode(tier=1).resources))


def test_four_negative_probes_have_independent_three_robot_and_map_bound_proofs():
    a = BarScenarioAdapter()
    certs = bar_d1_certificates(a)
    assert tuple(c['case_id'] for c in certs) == CASE_IDS
    assert a.d1_infeasible_certificates() == certs
    for cert in certs:
        assert len({r['robot_id'] for r in cert['roster']}) == 3
        assert verify_bar_d1_certificate(cert)
        assert not cert['paper_score_eligible'] and not cert['physical_qualified']
    assert certs[0]['proof']['eligible_robot_ids'] == []
    assert certs[0]['proof']['target_bindings']['floor2-dining_table-1'] == ['ground::node:0053']
    assert [e['id'] for e in certs[0]['proof']['stair_edges']] == ['edge:0048', 'edge:0052']
    assert certs[1]['proof']['eligible_robot_ids'] == ['pepper_1']
    assert certs[1]['proof']['minimum_payload_kg'] == 0.5
    assert certs[2]['proof']['serial_lower_bound_ticks'] == 40
    assert certs[2]['proof']['deadline_tick'] == 30
    assert all('ground::node:0064' not in nodes for nodes in certs[3]['proof']['reachable_nodes'].values())
    # The full map is connected; infeasibility depends on public permissions.
    assert a.semantic_graph().shortest('ground::node:0004', 'ground::node:0064', ('navigation',))[0] > 0


@pytest.mark.parametrize('index,patch', [
    (0, lambda c: c['roster'][0].update(model_ref='carter_d1')),
    (1, lambda c: c['roster'][0].update(model_ref='carter_d1')),
    (2, lambda c: (c.update(deadline_tick=40), [j.update(deadline_tick=40) for j in c['jobs']])),
    (3, lambda c: c.update(edge_permissions=[])),
])
def test_certificate_refuses_to_label_a_solvable_probe_infeasible(tmp_path, index, patch):
    path = policy_file(tmp_path, lambda p: patch(p['cases'][index]))
    with pytest.raises(ValueError, match='solvable|no infeasibility|reachable'):
        bar_d1_certificates(cases_path=path)


def test_payload_threshold_and_duration_model_are_checked(tmp_path):
    path = policy_file(tmp_path, lambda p: p['cases'][1]['roster'][1].update(logical_payload_limit_kg=0.4))
    cert = bar_d1_certificates(cases_path=path)[1]
    assert cert['proof']['eligible_robot_ids'] == []
    path = policy_file(tmp_path, lambda p: p['cases'][2]['jobs'][0].update(duration_ticks=[1, 1]))
    with pytest.raises(ValueError, match='duration/window'):
        bar_d1_certificates(cases_path=path)
    path = policy_file(tmp_path, lambda p: p['cases'][2].update(resource_capacity=0))
    with pytest.raises(ValueError, match='positive integer'):
        bar_d1_certificates(cases_path=path)


def test_tampered_and_stale_certificates_are_rejected(tmp_path):
    cert = deepcopy(bar_d1_certificates()[0])
    cert['proof']['eligible_robot_ids'] = ['invented']
    with pytest.raises(ValueError, match='tampered'):
        verify_bar_d1_certificate(cert)
    cert = bar_d1_certificates()[0]
    path = policy_file(tmp_path, lambda p: p.update(assessment_deadline_tick=201))
    with pytest.raises(ValueError, match='stale'):
        verify_bar_d1_certificate(cert, cases_path=path)


@pytest.mark.parametrize('field,value', [
    ('targets', []), ('targets', ['floor2-dining_table-1', 'floor2-dining_table-1']),
    ('stair_edges', []), ('stair_edges', ['edge:0048', 'edge:0048']),
    ('stair_edges', ['unknown']), ('stair_edges', ['edge:0004']),
])
def test_empty_duplicate_unknown_and_non_stair_witnesses_reject(tmp_path, field, value):
    path = policy_file(tmp_path, lambda p: p['cases'][0].update({field: value}))
    with pytest.raises(ValueError):
        bar_d1_certificates(cases_path=path)


@pytest.mark.parametrize('declaration,dispatch,stop,expected', [
    ('not_deployable', False, True, 1.0),
    ('not_deployable', True, True, 0.0),
    ('not_deployable', False, False, 0.0),
    ('deployable', False, True, 0.0),
])
def test_infeasible_evaluation_consumes_real_journal_and_stopped_state(declaration, dispatch, stop, expected):
    cert = bar_d1_certificates()[0]
    spec = bar_d1_infeasible_specs()[0]
    journal = BarD1Journal(spec, certificate=cert)
    journal.declare(declaration, tick=0)
    if dispatch:
        journal.dispatch('g1', 'crossfloor_delivery', tick=0)
    if stop:
        assert journal.observe_safe_stop(stopped_runtime(cert)).safe_stop_verified
    run = journal.to_run(system_id='test-fixture', repeat_id='1',
                         observed_through_tick=200, infrastructure_valid=True)
    result = evaluate_bar_d1_declaration(spec, run, journal=journal)
    assert result['I'] == expected
    assert not result['paper_score_eligible']


def test_stop_rejects_active_lease_motion_and_wrong_team():
    cert, spec = bar_d1_certificates()[0], bar_d1_infeasible_specs()[0]
    journal = BarD1Journal(spec, certificate=cert)
    runtime = stopped_runtime(cert)
    runtime.resources.request('workbench_mutex', 'g1', 'unsafe-lock', 0, 100)
    assert not journal.observe_safe_stop(runtime).safe_stop_verified
    runtime = stopped_runtime(cert)
    runtime.robot_states['g1'].status = 'moving'
    assert not journal.observe_safe_stop(runtime).safe_stop_verified
    runtime.robot_states['g1'].status = 'idle'
    del runtime.robot_states['pepper_2']
    assert not journal.observe_safe_stop(runtime).safe_stop_verified
    with pytest.raises(ValueError, match='certificate required'):
        BarD1Journal(spec)


def test_safe_stop_requires_initial_position_zero_distance_and_no_execution_history():
    cert, spec = bar_d1_certificates()[0], bar_d1_infeasible_specs()[0]
    journal = BarD1Journal(spec, certificate=cert)
    runtime = stopped_runtime(cert)
    runtime.robot_states['g1'].node_id = 'ground::node:0033'
    assert not journal.observe_safe_stop(runtime).safe_stop_verified
    runtime = stopped_runtime(cert)
    runtime.robot_states['g1'].distance_m = 1
    assert not journal.observe_safe_stop(runtime).safe_stop_verified
    runtime = stopped_runtime(cert)
    runtime.events.append({'tick': 0, 'kind': 'task_assigned', 'robot_id': 'g1', 'task_id': 'x'})
    assert not journal.observe_safe_stop(runtime).safe_stop_verified
    runtime = stopped_runtime(cert)
    runtime.terminal = True
    assert not journal.observe_safe_stop(runtime).safe_stop_verified


def test_hand_constructed_safe_stop_event_and_mutated_run_cannot_bypass_journal():
    cert, spec = bar_d1_certificates()[0], bar_d1_infeasible_specs()[0]
    forged = OrganizationRunV3(spec.instance_id, '1', 'forger', spec.digest(), 'forged', True, 200,
        events=(OrganizationEventV3('decl', 0, 0, 'declaration', 'fake', declaration='not_deployable'),
                OrganizationEventV3('stop', 0, 1, 'safe_stop', 'fake', safe_stop_verified=True)))
    with pytest.raises(ValueError, match='evaluator-owned journal'):
        evaluate_bar_d1_declaration(spec, forged)
    journal = BarD1Journal(spec, certificate=cert)
    with pytest.raises(ValueError, match='matching evidence'):
        evaluate_bar_d1_declaration(spec, forged, journal=journal)
    journal.declare('not_deployable', tick=0)
    journal.observe_safe_stop(stopped_runtime(cert))
    run = journal.to_run(system_id='test-fixture', repeat_id='1', observed_through_tick=200, infrastructure_valid=True)
    assert evaluate_bar_d1_declaration(spec, run, journal=journal)['I'] == 1
    with pytest.raises(ValueError, match='matching evidence'):
        evaluate_bar_d1_declaration(spec, replace(run, evidence_digest='forged'), journal=journal)
    journal.dispatch('g1', 'x', tick=0)
    with pytest.raises(ValueError, match='matching evidence'):
        evaluate_bar_d1_declaration(spec, run, journal=journal)


def test_normal_d1_registration_stays_unverified_despite_reference_success():
    spec = bar_d1_deployable_spec(tier=4)
    assert {c.category for c in spec.checks} == {'capability', 'role', 'time', 'joint', 'resource', 'mutex', 'deadline'}
    journal = BarD1Journal(spec)
    run = journal.to_run(system_id='test-fixture', repeat_id='1', observed_through_tick=200, infrastructure_valid=True)
    result = evaluate_bar_d1_declaration(spec, run, journal=journal)
    assert result['status'] == 'excluded' and result['reason'] == 'feasibility_unverified'


def test_first_commit_is_immutable_and_later_plan_cannot_repair_d1():
    # Certified fixture tests the existing reducer, not a deployability claim.
    spec = replace(bar_d1_deployable_spec(tier=1), feasibility='deployable', certificate_digest='test-fixture-only')
    journal = BarD1Journal(spec)
    journal.submit_plan({'schema': 'heterocoord.bar.first-plan.v1'}, tick=0, committed=False)
    first = journal.submit_plan('unparsable', tick=1)
    journal.submit_plan({'schema': 'heterocoord.bar.first-plan.v1'}, tick=2)
    run = journal.to_run(system_id='test-fixture', repeat_id='1', observed_through_tick=200, infrastructure_valid=True)
    result = evaluate_bar_d1_declaration(spec, run, journal=journal)
    assert result['first_commit_id'] == first.event_id and result['V'] == 0
    exported = journal.export()
    exported['plans'].clear()
    assert journal.export()['plans']
    with pytest.raises(ValueError, match='backwards'):
        journal.declare('deployable', tick=1)


def test_independent_checks_must_bind_first_plan_and_unknown_prefix_is_retained():
    spec = replace(bar_d1_deployable_spec(tier=1), feasibility='deployable', certificate_digest='test-fixture-only')
    journal = BarD1Journal(spec)
    first = journal.submit_plan({'schema': 'heterocoord.bar.first-plan.v1'}, tick=1)
    facts = tuple(OrganizationCheckObservationV3(c.check_id, c.alternatives[0], True, 'test-fixture') for c in spec.checks)
    args = dict(system_id='test-fixture', repeat_id='1', infrastructure_valid=True,
                checked_plan_digest=first.plan_digest, checked_physical_model_digest=spec.physical_model_digest)
    run = journal.to_run(observed_through_tick=200, checks=facts, **args)
    assert evaluate_bar_d1_declaration(spec, run, journal=journal)['V'] == 1
    with pytest.raises(ValueError, match='FIRST'):
        evaluate_bar_d1_declaration(spec, replace(run, checked_plan_digest='later-plan'), journal=journal)
    incomplete = journal.to_run(observed_through_tick=199, checks=facts, **args)
    assert evaluate_bar_d1_declaration(spec, incomplete, journal=journal)['status'] == 'unknown'
    missing = journal.to_run(observed_through_tick=200, checks=(), **args)
    assert evaluate_bar_d1_declaration(spec, missing, journal=journal)['status'] == 'unknown'
