"""CPU protocol/scheduler checks only. Synthetic samples are NOT physical evidence."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from threading import get_ident
from types import SimpleNamespace

import pytest

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment, ExecutionFeedback
from algorithm.system.coordination.bt_capabilities.native_adapter import encode_task
from task.contracts import AlgorithmDecision, EaiTaskPackage
from tools.algorithm_adapters import isaac_bt_multi_robot as host

# Run in the documented env_isaaclab environment with greenlet installed.

from algorithm.skills import PICK_PLACE_SKILL_ID, SkillRequest
from algorithm.skills.pick_place import BackendFeedback


# Local test backend: no dependency on another branch's task test suite.
def _request(request_id, operation, simulation_time=None):
    del simulation_time
    return SkillRequest(
        request_id,
        PICK_PLACE_SKILL_ID,
        "robot_1",
        parameters={
            "operation": operation,
            "object_ref": "box_1",
            "timeout_s": 2.0,
        },
    )


class _FakeAdapter:
    robot_id = "robot_1"

    def __init__(self):
        self.command = None
        self.held = False
        self.stopped = False
        self.steps = 0
        self.starts = 0

    def resource_key(self, object_ref):
        return "object:" + object_ref

    def validate(self, command):
        if command.operation not in {"pick", "place"} or not command.object_ref:
            raise ValueError("invalid fixed action")

    def start(self, command):
        self.command = command
        self.steps = 0
        self.stopped = False
        self.starts += 1

    def before_step(self):
        pass

    def after_step(self):
        self.steps += 1
        if self.steps < 2:
            return
        self.stopped = True
        self.held = self.command.operation == "pick"

    def feedback(self):
        completed = self.stopped
        return BackendFeedback(
            self.command.request_id,
            "completed" if completed else "running",
            "holding" if self.held else "empty",
            self.command.object_ref if self.held else None,
            ready=completed,
            stopped=completed,
            clean=not self.held,
        )

    def request_stop(self, reason):
        self.stopped = True


CONDITION = AlgorithmCondition('unit-test-only', 'synthetic', 'fixture', 'protocol_only', 'fixture')


@pytest.fixture
def package(tmp_path):
    path = tmp_path / 'task.json'
    host.write_json(path, host.case_document())
    return EaiTaskPackage.from_json(path)


def decision(package):
    assignments = tuple(Assignment(task, rid, 'synthetic-test-only') for task, rid in zip(host.MOVES, host.ROSTER))
    raw = {'ok': True, 'graph_mode': 'dag', 'native_tree': 'ros_bt_py.nodes.parallel.Parallel',
           'tree_state': 'RUNNING', 'prerequisites': {t.task_id: list(t.prerequisites) for t in package.graph.tasks},
           'assignments': [vars(a) for a in assignments], 'task_contract_sha256': hashlib.sha256(json.dumps(
               encode_task(package, package.robots), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()}
    return AlgorithmDecision(CONDITION, assignments, ('inspect_join',), {'native_evidence': raw})


def test_fixture_dag_and_nonforced_costs(package):
    payload = encode_task(package, package.robots)
    assert payload['graph_mode'] == 'dag'
    assert payload['robot_ids'] == list(host.ROSTER)
    assert [t['prerequisites'] for t in payload['tasks']] == [[], [], list(host.MOVES)]
    for task in payload['tasks']:
        assert set(task['bids']) == set(host.ROSTER)
        assert 'robot_id' not in task
    for node in package.graph.tasks[:2]:
        assert node.metadata['pick_place']['object_position_world_m'] != node.metadata['pick_place']['target_position']


def test_valid_native_initial_output(package):
    host.validate_native(decision(package), package, initial=True)


@pytest.mark.parametrize('field,value', [('graph_mode', 'linear'), ('tree_state', 'SUCCEEDED'),
    ('native_tree', 'invented'), ('prerequisites', {}), ('task_contract_sha256', 'wrong')])
def test_native_contract_rejections(package, field, value):
    d = decision(package)
    d.provenance['native_evidence'][field] = value
    with pytest.raises(ValueError):
        host.validate_native(d, package, initial=True)


def test_missing_parallel_assignment_rejected(package):
    d = decision(package)
    d.provenance['native_evidence']['assignments'] = [vars(d.assignments[0])]
    with pytest.raises(ValueError, match='both independent'):
        host.validate_native(replace(d, assignments=d.assignments[:1]), package, initial=True)


@pytest.mark.parametrize('robot_ids', [('ur5_1', 'ur5_1'), ('ur5_2', 'ur5_1'), ('alien', 'ur5_2')])
def test_no_host_repair_of_native_selection(package, robot_ids):
    d = decision(package)
    assignments = tuple(replace(a, robot_id=rid) for a, rid in zip(d.assignments, robot_ids))
    d.provenance['native_evidence']['assignments'] = [vars(a) for a in assignments]
    with pytest.raises(ValueError):
        host.validate_native(replace(d, assignments=assignments), package, initial=True)


def test_raw_output_mismatch_rejected(package):
    d = decision(package)
    with pytest.raises(ValueError, match='raw native'):
        host.validate_native(replace(d, assignments=tuple(reversed(d.assignments))), package)


def test_reissued_completed_task_rejected(package):
    with pytest.raises(ValueError, match='non-pending'):
        host.validate_native(decision(package), package, pending=('inspect_join',))


def test_cooperative_jobs_share_thread_and_tick():
    pytest.importorskip('greenlet')
    parent = get_ident()
    clock, rows = [0], []
    def job(name, count):
        def run(advance):
            for _ in range(count):
                assert get_ident() == parent
                before = clock[0]
                advance()
                assert clock[0] == before + 1
            return name
        return run
    def step(waiting):
        assert get_ident() == parent
        clock[0] += 1
        rows.append(waiting)
    result = host.cooperative_batch({'a': job('a', 2), 'b': job('b', 4)}, step)
    assert result == {'a': 'a', 'b': 'b'}
    assert rows == [('a', 'b'), ('a', 'b'), ('b',), ('b',)]


def test_cooperative_real_pick_place_executor_contract():
    """Real executor with test-only backend: validates thread/lease semantics only."""
    pytest.importorskip('greenlet')
    from task.executors import PickPlaceExecutor
    from task.resources import ResourceManager
    clock = [0.]
    resources = ResourceManager()
    ticks = []
    def job(rid):
        def run(advance):
            adapter = _FakeAdapter()
            adapter.robot_id = rid
            executor = PickPlaceExecutor(advance=advance, simulation_time=lambda: clock[0], resources=resources,
                physical_object_experiment_authorization="unit_test_synthetic_backend_only")
            executor.register_adapter(adapter)
            def request(operation):
                request = _request(rid + operation, operation, clock[0])
                return replace(request, robot_id=rid, parameters={**request.parameters, 'object_ref': rid + '_box'})
            picked = executor.execute(request('pick'))
            placed = executor.execute(request('place'))
            return picked, placed
        return run
    def step(active):
        clock[0] += host.DT
        ticks.append(active)
    result = host.cooperative_batch({rid: job(rid) for rid in host.ROSTER}, step)
    assert result == {rid: (True, True) for rid in host.ROSTER}
    assert len(ticks) == 4 and all(set(t) == set(host.ROSTER) for t in ticks)
    assert resources.snapshot()['resources'] == {}


def test_tick_budget_does_not_report_execution_success():
    pytest.importorskip('greenlet')
    def forever(advance):
        while True:
            advance()
    with pytest.raises(RuntimeError, match='budget exhausted'):
        host.cooperative_batch({'a': forever}, lambda active: None, max_ticks=2)


def sample(tick, active, moving):
    return {'tick': tick, 'active_tasks': active,
            'robots': {rid: {'joint_position_rad': [tick * .01 if moving else 0.] * 6} for rid in host.ROSTER}}


def test_serial_or_static_ticks_are_not_physical_concurrency():
    for active, moving in [(list(host.MOVES), False), (['move_left'], True)]:
        evidence = host.ConcurrencyEvidence()
        for tick in range(1, 50):
            evidence.observe(sample(tick, active, moving))
        assert evidence.report()['passed'] is False


def test_concurrency_counts_measured_overlapping_ticks():
    evidence = host.ConcurrencyEvidence()
    for tick in range(1, 26):
        evidence.observe(sample(tick, list(host.MOVES), True))
    report = evidence.report()
    assert report['shared_tick_overlap_count'] == 25
    assert report['simultaneous_measured_joint_motion_ticks'] == 24
    assert report['passed'] is True
    assert report['shared_tick_overlap_simulation_s'] == 25 * host.DT
    assert report['overlap_tick_intervals_inclusive'] == [[1, 25]]


def test_tick_gaps_rejected():
    evidence = host.ConcurrencyEvidence()
    evidence.observe(sample(1, [], False))
    with pytest.raises(ValueError, match='non-contiguous'):
        evidence.observe(sample(3, [], False))


def test_no_receipts_never_passes_evaluator(tmp_path):
    physical = host.IsaacTwoUR5Host(tmp_path)
    assert physical.evaluate()['status'] == 'failure'
    with pytest.raises(RuntimeError, match='before both measured'):
        physical.inspect(Assignment('inspect_join', 'ur5_1', 'test-only'))


def test_feedback_must_be_echoed_and_raw_is_recorded(tmp_path):
    item = ExecutionFeedback('move_left', 'ur5_1', 'success')
    adapter = SimpleNamespace(last_native_state={'ok': True})
    adapter.feedback_batch = lambda *a, **k: {'leaf_states': []}
    provider = host.RecordedProvider(adapter, tmp_path)
    with pytest.raises(RuntimeError, match='did not echo'):
        provider.feedback_batch((item,), succeeded=(True,))
    assert json.loads((tmp_path / 'native_events.json').read_text())[0]['kind'] == 'feedback_batch'


def test_offline_prepare_cannot_launch_or_claim_physics(tmp_path, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('offline preparation must not construct a simulator')
    monkeypatch.setattr(host.IsaacTwoUR5Host, 'build', forbidden)
    output = tmp_path / 'prepare'
    assert host.main(['--output', str(output)]) == 0
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['status'] == 'prepared_not_executed'
    assert receipt['passed'] is False and receipt['physical_success'] is None
    assert receipt['physical_execution_started'] is False
    assert receipt['physical_concurrency'] is None
    assert not (output / 'shared_ticks.jsonl').exists()
    assert 'isaaclab.sim' not in sys.modules


def test_invalid_native_prevents_host_build(tmp_path, package, monkeypatch):
    from task.runner import TaskRunner
    from task.contracts import TaskRunRequest
    d = decision(package)
    d.provenance['native_evidence']['graph_mode'] = 'linear'
    adapter = SimpleNamespace(last_native_state=d.provenance['native_evidence'])
    class Adapter:
        last_native_state = adapter.last_native_state
        def __call__(self, *args): return d
    class Never:
        def execute_batch(self, *a, **kw): raise AssertionError('physical batch must never run')
    with pytest.raises(ValueError, match='Parallel DAG'):
        TaskRunner(orchestration_executor=Never()).run_orchestrated(TaskRunRequest(package), host.RecordedProvider(Adapter(), tmp_path))
    assert (tmp_path / 'native_events.json').is_file()


def test_receipt_is_durable_before_kit_can_terminate_python(tmp_path, monkeypatch):
    """Regression: Kit close previously blocked before receipt.json existed."""
    from benchmark import native_algorithms
    events = []
    adapter = SimpleNamespace(last_native_state={'tree_state': 'SUCCEEDED'},
                              close=lambda: events.append('native_closed'))
    monkeypatch.setattr(native_algorithms, 'build_native_adapter', lambda *a, **k: adapter)

    class Host:
        started = True
        tick = 5053
        concurrency = host.ConcurrencyEvidence()
        def __init__(self, output, **kwargs):
            self.output = output
        def close(self):
            events.append('executor_closed')
        def shutdown_application(self, code):
            receipt = json.loads((self.output / 'receipt.json').read_text())
            assert receipt['passed'] is True
            assert receipt['input_files_unchanged'] is True
            assert events == ['native_closed', 'executor_closed']
            assert code == 0
            raise SystemExit(code)  # real Kit may never return

    result = SimpleNamespace(verdict={'status': 'success'}, task_states={'task': 'completed'}, to_dict=lambda: {})
    monkeypatch.setattr(host, 'IsaacTwoUR5Host', Host)
    monkeypatch.setattr(host, 'TaskRunner', lambda **kwargs: SimpleNamespace(run_orchestrated=lambda *a, **k: result))
    with pytest.raises(SystemExit) as stopped:
        host.main(['--output', str(tmp_path / 'run'), '--execute'])
    assert stopped.value.code == 0
