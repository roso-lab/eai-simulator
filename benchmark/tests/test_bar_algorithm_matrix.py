from copy import deepcopy
from dataclasses import replace
import json

import pytest

from benchmark.bar_algorithms import BarGoalAlgorithm
from benchmark.bar_runtime_v1 import BarRuntime
from benchmark.case_input_audit_v1 import audit_semantic_map
from benchmark.scenarios.bar import BarScenarioAdapter


@pytest.mark.parametrize('tier', [1,2,3,4])
def test_default_tiers_complete_with_identity_and_replay(tier):
    result = BarScenarioAdapter().run_generic(tier=tier, seed=17)
    assert result['success'] and result['deadline_met'] and result['replay_equivalent']
    assert all(o['status']=='placed' and o['location']==o['destination'] for o in result['object_state'].values())
    assert result['paper_score_eligible'] is False
    assert any(c['label']=='cp_05_closeout' for c in result['checkpoints'])
    if tier == 4:
        assert all(o['destination'].startswith('floor2-') for o in result['object_state'].values())
        joint = [e for e in result['events'] if e['kind']=='joint_action_completed']
        assert len(joint)==2
        assert all(len(set(e['participants']))==2 and e['support_samples']>=25 and e['support_valid'] for e in joint)


def test_ground_only_audit_and_stair_capability_preserved():
    a = BarScenarioAdapter()
    assert not audit_semantic_map(a.map_root)['case_ready']
    assert audit_semantic_map(a.map_root, allow_empty_domains=('aerial',))['case_ready']
    g = a.semantic_graph()
    with pytest.raises(ValueError, match='no route'):
        g.shortest('ground::node:0004','ground::node:0053',('navigation',))
    assert g.shortest('ground::node:0004','ground::node:0053',('stair_capable',))[0]>0


def test_map_digest_mismatch_rejected(tmp_path):
    a = BarScenarioAdapter(); m = a.manifest(); m['semantic_map']['revision']='stale'
    p=tmp_path/'manifest.json';p.write_text(json.dumps(m))
    with pytest.raises(ValueError, match='revision mismatch'):
        BarScenarioAdapter(manifest_path=p).build_episode(tier=1)


def test_excluded_t4_rosters_are_not_silently_changed():
    with pytest.raises(ValueError, match='excludes G1'):
        BarScenarioAdapter().build_episode(tier=4,variant_id='bar_roster_g1_pepper_carter')


def test_charge_drain_queue_and_mid_charge_checkpoint_resume():
    rt=BarScenarioAdapter().runtime(tier=4, seed=17)
    while not rt.terminal and not rt.charge_resume:
        rt.step()
    assert rt.charge_resume
    cp=rt.checkpoint(); first=rt.fork(cp); second=rt.fork(cp)
    a=first.run(); b=second.run()
    assert a['success'] and a['events']==b['events']
    assert a['charge_services']>=1
    assert all(v>0 for v in a['energy_used'].values())
    assert a['service_samples']==sum(t['end_tick']-t['start_tick']+1 for t in a['service_trace'])


def test_charging_point_is_capacity_one_and_four_seconds():
    rt=BarScenarioAdapter().runtime(tier=4)
    spec=rt.resources.specs['charging_point']
    assert spec.capacity==1 and spec.service_ticks==200
    assert rt.resources.request('charging_point','carter_d1','a',0)
    assert not rt.resources.request('charging_point','m20_d1','b',0)
    assert not rt.resources.advance(199)
    released=rt.resources.advance(200)
    assert released[0]['task_id']=='a'
    assert rt.resources.active['charging_point'][0]['task_id']=='b'


def test_battery_exhaustion_is_failure_not_infeasibility():
    a=BarScenarioAdapter(); rt=a.runtime(tier=3)
    rt.robot_states['carter_d1'].battery=0.001
    rt.step()
    assert not rt.success and rt.failure_reason.startswith('battery_exhausted')


def test_closed_report_channel_blocks_task_progress():
    rt=BarScenarioAdapter().runtime(tier=1)
    rt.communication.channel.loss_probability=1.0
    result=rt.run()
    assert not result['success']
    assert 'notification_timeout' in result['failure_reason']
    assert not result['notification_delivered']


def test_actual_availability_fault_has_affected_work_and_reallocation():
    result=BarScenarioAdapter().run_generic(tier=4,condition_id='availability_loss',seed=17)
    assert result['success']
    event=result['availability_events'][0]
    assert event['persistent'] and event['removed_capabilities'] and event['affected_work']
    assert result['reallocation_count']>=1


def test_fresh_branch_does_not_restore_private_algorithm_state():
    rt=BarScenarioAdapter().runtime(tier=1)
    for _ in range(100): rt.step()
    rt.algorithm.assignments['private_poison']='never_replay'
    cp=rt.checkpoint()
    fresh=rt.fresh_branch(cp,algorithm=BarGoalAlgorithm())
    assert fresh.algorithm.snapshot()['assignments']=={}
    assert fresh.logical.capture_state()==rt.logical.capture_state()
    assert fresh.run()['success']
    tampered=deepcopy(cp);tampered['snapshot']['clock']['state']['tick']+=1
    with pytest.raises(ValueError, match='digest mismatch'):
        rt.fresh_branch(tampered,algorithm=BarGoalAlgorithm())


def test_communication_capacity_factor_is_strictly_ordered():
    a=BarScenarioAdapter()
    rates=[a.runtime(tier=1,communication=c).config['communication_capacity'] for c in ('C0','C1','C2','C3')]
    assert all(x>y for x,y in zip(rates,rates[1:]))


def test_no_implicit_renaming_of_diagnostic_events_to_public_skill():
    from algorithm.skills.contracts import SkillRequest
    from algorithm.skills.simulator_schema import validate_simulator_skill_request, SimulatorSchemaError
    for operation in ('prepare','release','accept','receive','join'):
        with pytest.raises(SimulatorSchemaError):
            validate_simulator_skill_request(SkillRequest('bad', 'eai.manipulation.pick_place','robot',
                parameters={'operation':operation,'object_ref':'order_1'}))


def test_resource_contention_condition_exercises_real_queue():
    result=BarScenarioAdapter().run_generic(tier=3,condition_id='resource_contention',seed=17)
    assert result['success'] and result['resource_contention_requests']>0 and result['resource_wait_ticks']>0


def test_joint_support_violation_fails_instead_of_counting_arrivals():
    rt=BarScenarioAdapter().runtime(tier=4)
    while not rt.terminal:
        rt.step()
        active=[g for g,s in rt._cooperative_states.items() if s['status']=='servicing']
        if active:
            _, robot=rt._cooperative_members(active[0])[0]
            robot.active_capabilities.discard('manipulator')
            rt.step()
            assert rt.failure_reason.startswith('joint_support_lost')
            return
    pytest.fail('joint action was never exercised')


def test_service_abandonment_is_a_whole_process_failure():
    rt=BarScenarioAdapter().runtime(tier=3)
    while 'service_cover' not in rt.tasks.completed_tick: rt.step()
    for s in rt.robot_states.values():s.node_id='ground::node:0004'
    rt.step()
    assert rt.failure_reason=='unregistered_service_gap'


def test_capacity_violation_is_not_a_successful_mutex_result():
    rt=BarScenarioAdapter().runtime(tier=1)
    rt.resources.request('workbench_mutex','carter_d1','forged',0,1000)
    rt.resources.active['workbench_mutex'].append(dict(rt.resources.active['workbench_mutex'][0]))
    rt.step()
    assert rt.failure_reason=='resource_capacity_violation:workbench_mutex'


def test_coordinator_cannot_replan_before_state_delivery():
    rt=BarScenarioAdapter().runtime(tier=1, communication='C3')
    rt.communication.channel.loss_probability=1.0
    for _ in range(100): rt.step()
    assert not any(e['kind']=='allocation_proposal' for e in rt.events)
    assert not rt.tasks.assigned
    assert any(e['kind']=='coordination_state_submitted' for e in rt.events)


def test_checkpoint_restores_the_active_bar_resource_ledger_and_queue():
    rt=BarScenarioAdapter().runtime(tier=4)
    assert rt.resources.request('charging_point','carter_d1','lease-a',rt.tick,200)
    assert not rt.resources.request('charging_point','m20_d1','lease-b',rt.tick,200)
    cp=rt.checkpoint()
    clone=rt.fork(cp)
    assert clone.resources.snapshot()==rt.resources.snapshot()
    assert len(clone.resources.active['charging_point'])==1
    assert len(clone.resources.queues['charging_point'])==1
    fresh=rt.fresh_branch(cp,algorithm=BarGoalAlgorithm())
    assert fresh.resources.snapshot()==rt.resources.snapshot()
    clone.resources.release('charging_point','carter_d1','lease-a',rt.tick)
    assert fresh.resources.active['charging_point'][0]['robot_id']=='carter_d1'


def test_fresh_algorithm_is_initialized_once_on_restored_public_observation():
    class Once(BarGoalAlgorithm):
        def __init__(self): self.count=0
        def initialize(self, observation):
            self.count+=1
            assert self.count==1
            super().initialize(observation)
    rt=BarScenarioAdapter().runtime(tier=1)
    alg=Once(); clone=rt.fresh_branch(rt.checkpoint(),algorithm=alg)
    assert alg.count==1 and clone.run()['success']


def test_p2_requires_release_endpoint_and_owner_evidence():
    from benchmark.bar_evaluator_v1 import evaluate_bar
    a=BarScenarioAdapter(); result=a.run_generic(tier=1)
    assert next(r['q'] for r in result['evaluation']['records'] if r['primitive']=='P2')==1
    result['events']=[e for e in result['events'] if e['kind']!='handoff_release_ready']
    evaluation=evaluate_bar(result,a.build_episode(tier=1))
    assert next(r['q'] for r in evaluation['records'] if r['primitive']=='P2')==0


def test_authored_matrix_does_not_enable_battery_for_t1_t2():
    from benchmark.run_bar_matrix import jobs
    assert len(jobs((17,18,19)))==504
    assert not any(tier in (1,2) and c in ('battery_hard','battery_very_hard') for _,_,tier,c,_,_ in jobs((17,)))
