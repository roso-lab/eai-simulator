from dataclasses import replace
from types import SimpleNamespace
import pytest
from algorithm.common_coordination.contract import Assignment, CoordinationTask, ExecutionFeedback, FailureCode, TaskGraph
from algorithm.skills.contracts import SkillPlan, SkillRequest
from task.contracts import AlgorithmDecision, OrchestrationLimits
from benchmark.fleet import FleetRuntime
from benchmark.runtime import EpisodeRunner
from benchmark.tests.test_runtime import FakeRuntime, _p2_binding
from algorithm.common_coordination.contract import AlgorithmCondition


def _condition():
    return AlgorithmCondition('fixture', 'test', 'fixture', 'decision_only', 'fixture')


def binding_dag():
    b = _p2_binding()
    tasks = (CoordinationTask('first', 'first', required_capabilities=frozenset({'navigate'})),
             CoordinationTask('dependent', 'next', required_capabilities=frozenset({'navigate'}), prerequisites=('first',)),
             CoordinationTask('independent', 'separate', required_capabilities=frozenset({'navigate'})))
    return replace(b, task=replace(b.task, graph=TaskGraph(tasks=tasks, source='fixture')))


class Skill:
    def plan(self, request, *, observation=None):
        return SkillPlan(request.request_id, request.skill_id, request.robot_id, commands=())


def request(assignment, task, *, request_id):
    return SkillRequest(request_id, 'fixture', assignment.robot_id)


def decide(task, robots):
    return AlgorithmDecision(_condition(), tuple(Assignment(t.task_id, 'robot-1', source='fixture') for t in task.graph.tasks))


class Executor:
    def __init__(self, *, complete=False, quiescent=True, illegal=False):
        self.calls = []
        self.complete, self.quiescent, self.illegal = complete, quiescent, illegal

    def execute(self, assignment, task, *, scene, plan, step):
        self.calls.append(task.task_id)
        step({assignment.robot_id: 'command'})
        if self.illegal:
            return SimpleNamespace(task_id=assignment.task_id, robot_id=assignment.robot_id)
        changes = {}
        if self.complete:
            changes.update(dependency_completed=True, dependency_evidence_hash='external-progress')
        if self.quiescent:
            changes.update(quiescent=True, quiescence_evidence_hash='external-stop')
        return ExecutionFeedback(assignment.task_id, assignment.robot_id, 'command_applied', world_changes=changes)


def run(b, executor, *, options=None, orchestrated=False, wait=0):
    from benchmark.protocol import RunKey, BranchRole
    key = RunKey('base', 'system', 'nominal', 'r0', 'round', BranchRole.FACT)
    fleet = FleetRuntime(key, transport_options=options)
    runtime = FakeRuntime()
    runner = EpisodeRunner(runtime, run_key=key, fleet=fleet, assignment_wait_ticks=wait)
    method = runner.run_orchestrated if orchestrated else runner.run
    kwargs = {'limits': OrchestrationLimits(max_rounds=4, max_dispatches=4)} if orchestrated else {}
    result = method(b, decision_provider=decide, request_factory=request, skill=Skill(), executor=executor,
                    evaluator=None, scene=SimpleNamespace(), **kwargs)
    return result, runtime, fleet


def test_unverified_predecessor_blocks_only_successor():
    ex = Executor()
    result, runtime, fleet = run(binding_dag(), ex)
    assert ex.calls == ['first', 'independent']
    assert any(e.kind == 'dependency_blocked' for e in result.ledger_events)
    ex = Executor(complete=True)
    run(binding_dag(), ex)
    assert ex.calls == ['first', 'dependent', 'independent']


@pytest.mark.parametrize('options', [{'loss_probability': 1.}, {'delay_ticks': 2}])
def test_undelivered_assignment_never_executes(options):
    ex = Executor()
    result, runtime, fleet = run(_p2_binding(), ex, options=options)
    assert ex.calls == [] and runtime.actions == [] and not result.executions


def test_delayed_assignment_wait_budget_controls_dispatch():
    ex = Executor()
    result, runtime, fleet = run(_p2_binding(), ex, options={'delay_ticks': 2}, wait=2)
    assert len(ex.calls) == 1
    assert runtime.actions[:2] == [{}, {}]


def test_controller_quarantine_requires_measured_release():
    ex = Executor(complete=True, quiescent=False)
    result, runtime, fleet = run(binding_dag(), ex)
    assert ex.calls == ['first']
    assert any(e.kind == 'controller_quarantined' for e in result.ledger_events)
    with pytest.raises(TypeError, match='ExecutionFeedback'):
        run(_p2_binding(), Executor(illegal=True))


def test_orchestrated_path_reuses_dag_scheduler_and_terminates():
    ex = Executor(complete=True)
    result, runtime, fleet = run(binding_dag(), ex, orchestrated=True)
    assert ex.calls == ['first', 'dependent', 'independent']
    terminal = next(e.payload for e in result.ledger_events if e.kind == 'orchestration_terminal')
    assert terminal['reason'] == 'all_dependencies_completed'
    assert result.evaluation.reason == 'physical_evidence_required'
    assert fleet.ledger.sealed


def test_loss_and_restore_during_execution_never_revalidates_old_command():
    from benchmark.protocol import RunKey, BranchRole
    key = RunKey('base', 'system', 'nominal', 'r0', 'round', BranchRole.FACT)
    fleet = FleetRuntime(key)
    runtime = FakeRuntime()
    class Schedule:
        def advance(self, tick, f):
            if tick == 1 and f.command_generation('robot-1') == 0:
                f.set_function_available('robot-1', 'navigate', False, tick=tick)
                f.set_function_available('robot-1', 'navigate', True, tick=tick)
    class LongExecutor(Executor):
        def execute(self, assignment, task, **kw):
            kw['step']({'robot-1': 'first'})
            kw['step']({'robot-1': 'stale'})
    runner = EpisodeRunner(runtime, run_key=key, fleet=fleet, event_scheduler=Schedule())
    with pytest.raises(RuntimeError, match='generation'):
        runner.run(_p2_binding(), decision_provider=decide, request_factory=request, skill=Skill(),
                   executor=LongExecutor(), evaluator=None, scene=SimpleNamespace())
    assert runtime.actions == [{'robot-1': 'first'}]
    assert fleet.ledger.sealed
