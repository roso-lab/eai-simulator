"""Client contract tests plus opt-in real ROS DAG tests (never simulated auctions)."""
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from algorithm.common_coordination.contract import Assignment, CoordinationTask, ExecutionFeedback, FailureCode, TaskGraph
from algorithm.system.coordination.bt_capabilities.native_adapter import NativeBTCapabilitiesAdapter, NativeBTError, NativeBTFeedbackExecutor, encode_task
from task.contracts import DependencyOutcome, ExecutionUpdate, TaskRunRequest
from task.runner import TaskRunner
from algorithm.system.coordination.bt_capabilities.tests.test_native_adapter import native, package


AUCTION = 'ros_bt_py/src/ros_bt_py/assignment_manager/parallel_auction_manager.py'
SEQUENCE = 'ros_bt_py/src/ros_bt_py/nodes/sequence.py'
PARALLEL = 'ros_bt_py/src/ros_bt_py/nodes/parallel.py'


def contract_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def dag_package(*, same_robot=False):
    base = package()
    def node(name, deps=(), bids=None):
        return CoordinationTask(name, 'generic task ' + name, frozenset({'camera'}), deps,
                                target_ref='/World/Targets/' + name,
                                metadata={'bt_native': {'bids': bids or {'robot_a': 1, 'robot_b': 9}}})
    # Deliberately non-topological input; the endpoint owns readiness.
    nodes = (node('join', ('left', 'right')), node('right', ('root',),
             {'robot_a': 1, 'robot_b': 9} if same_robot else {'robot_a': 9, 'robot_b': 1}),
             node('root'), node('left', ('root',)))
    return replace(base, graph=TaskGraph(nodes, 'generic_fork_join'))


def test_dag_payload_retains_targets_bids_dependencies_and_input_order():
    task = dag_package()
    payload = encode_task(task, task.robots)
    assert payload['graph_mode'] == 'dag'
    assert [t['task_id'] for t in payload['tasks']] == ['join', 'right', 'root', 'left']
    for original, record in zip(task.graph.tasks, payload['tasks']):
        assert record['prerequisites'] == list(original.prerequisites)
        assert record['target_ref'] == original.target_ref
        assert record['bids'] == original.metadata['bt_native']['bids']


def test_dag_missing_bid_fails_without_defaults():
    task = dag_package()
    node = replace(task.graph.tasks[0], metadata={'bt_native': {'bids': {'robot_a': 2}}})
    task = replace(task, graph=TaskGraph((node,) + task.graph.tasks[1:], 'missing'))
    with pytest.raises(ValueError, match='missing explicit bid'):
        encode_task(task, task.robots)


@pytest.fixture
def client_receipt(tmp_path):
    hashes = {}
    for name in (AUCTION, SEQUENCE, PARALLEL):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('receipt fixture: ' + name)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    adapter = NativeBTCapabilitiesAdapter(tmp_path, endpoint='http://127.0.0.1:1')
    adapter._payload = encode_task(dag_package(), dag_package().robots)
    adapter.mission_id = 'fixture'
    result = dict(ok=True, mission_id='fixture', graph_mode='dag',
                  task_contract_sha256=contract_hash(adapter._payload),
                  native_algorithm='ros_bt_py.assignment_manager.parallel_auction_manager.ParallelAuctionManager',
                  native_tree='ros_bt_py.nodes.parallel.Parallel', source_commit='fixture',
                  source_sha256=hashes, assignments=[], unassigned_tasks=[], tree_state='RUNNING')
    return adapter, result


def test_dag_source_receipt_requires_parallel(client_receipt):
    adapter, result = client_receipt
    assert adapter._decision(result).provenance['native_evidence'] is result
    result['source_sha256'].pop(PARALLEL)
    with pytest.raises(NativeBTError, match='incomplete'):
        adapter._decision(result)


def test_dag_source_receipt_rejects_modified_parallel(client_receipt):
    adapter, result = client_receipt
    (adapter.source_root / PARALLEL).write_text('modified')
    with pytest.raises(NativeBTError, match='differs'):
        adapter._decision(result)


@pytest.mark.parametrize('include_parallel', [False, True])
def test_linear_source_receipt_keeps_original_two(client_receipt, include_parallel):
    adapter, result = client_receipt
    task = package(chain=True)
    adapter._payload = encode_task(task, task.robots)
    result['graph_mode'] = 'linear'
    result['native_tree'] = 'ros_bt_py.nodes.sequence.MemorySequence'
    result['task_contract_sha256'] = contract_hash(adapter._payload)
    if not include_parallel:
        result['source_sha256'].pop(PARALLEL)
    assert adapter._decision(result).assignments == ()


def test_mode_and_slot_mismatch_fail_closed(client_receipt):
    adapter, result = client_receipt
    with pytest.raises(NativeBTError, match='graph_mode'):
        adapter._decision(dict(result, graph_mode='linear'))
    result['assignments'] = [{'task_id': task, 'robot_id': 'robot_a'} for task in ('left', 'right')]
    with pytest.raises(NativeBTError, match='same robot slot'):
        adapter._decision(result)


def test_feedback_batch_wire_contract_and_duplicate_validation(client_receipt, monkeypatch):
    adapter, result = client_receipt
    calls = []
    def request(route, body):
        calls.append((route, body))
        return result
    monkeypatch.setattr(adapter, '_request', request)
    receipts = (ExecutionFeedback('left', 'robot_a', 'ack'), ExecutionFeedback('right', 'robot_b', 'failed', FailureCode.EXECUTION_FAILURE))
    adapter.feedback_batch(receipts, succeeded=(True, False))
    assert calls[0][0] == '/feedback_batch'
    assert [r['succeeded'] for r in calls[0][1]['feedback']] == [True, False]
    for values, flags, error in [(receipts * 2, (True,) * 4, NativeBTError),
                                 (receipts, (True,), ValueError), (receipts, (True, 1), TypeError)]:
        with pytest.raises(error):
            adapter.feedback_batch(values, succeeded=flags)
    assert len(calls) == 1


class ReceiptSink:
    """Transport recorder only, not an auction or BT implementation."""
    def __init__(self):
        self.calls = []
    def feedback_batch(self, feedback, *, succeeded):
        self.calls.append((feedback, succeeded))


class BatchExecutor:
    def __init__(self, updates):
        self.updates, self.calls = updates, []
    def execute_batch(self, assignments, tasks, *, scene):
        self.calls.append((assignments, tasks, scene))
        return self.updates
    def execute(self, *args, **kwargs):
        raise AssertionError('batch executor must be forwarded directly')


def updates():
    return (ExecutionUpdate(ExecutionFeedback('left', 'robot_a', 'measured'), DependencyOutcome.COMPLETED),
            ExecutionUpdate(ExecutionFeedback('right', 'robot_b', 'failed', FailureCode.EXECUTION_FAILURE), DependencyOutcome.TERMINAL_FAILURE))


def assignments():
    return tuple(Assignment(t, r, 'test') for t, r in [('left', 'robot_a'), ('right', 'robot_b')])


def test_batch_forwards_existing_executor_preserves_real_updates_and_failure():
    receipts = updates()
    executor, sink = BatchExecutor(tuple(reversed(receipts))), ReceiptSink()
    wrapper = NativeBTFeedbackExecutor(sink, executor)
    tasks, scene = {}, object()
    result = wrapper.execute_batch(assignments(), tasks, scene=scene)
    assert result == receipts
    assert all(a is b for a, b in zip(result, receipts))
    assert executor.calls == [(assignments(), tasks, scene)]
    assert sink.calls == [(tuple(r.feedback for r in receipts), (True, False))]


@pytest.mark.parametrize('case,error,message', [
    ('identity', NativeBTError, 'identity mismatch'),
    ('duplicate', NativeBTError, 'duplicate executor feedback'),
    ('missing', NativeBTError, 'identity mismatch'),
    ('contradiction', NativeBTError, 'contradicts execution failure code'),
    ('raw', TypeError, 'executor must return ExecutionUpdate'),
])
def test_invalid_executor_batch_is_not_partially_submitted(case, error, message):
    first, second = updates()
    values = {'identity': (first, replace(second, feedback=replace(second.feedback, robot_id='wrong'))),
              'duplicate': (first, first), 'missing': (first,),
              'contradiction': (first, replace(second, dependency_outcome=DependencyOutcome.COMPLETED)),
              'raw': (first.feedback, second.feedback)}[case]
    sink = ReceiptSink()
    wrapper = NativeBTFeedbackExecutor(sink, BatchExecutor(values))
    with pytest.raises(error, match=message):
        wrapper.execute_batch(assignments(), {}, scene=None)
    assert sink.calls == []


def test_busy_same_robot_batch_rejected_before_executor():
    executor, sink = BatchExecutor(updates()), ReceiptSink()
    wrapper = NativeBTFeedbackExecutor(sink, executor)
    batch = tuple(replace(a, robot_id='robot_a') for a in assignments())
    with pytest.raises(NativeBTError, match='same robot slot'):
        wrapper.execute_batch(batch, {}, scene=None)
    assert executor.calls == sink.calls == []


class ProtocolBatchExecutor:
    """Explicit protocol acknowledgements, never physical-success evidence."""
    def __init__(self):
        self.batches = []
    def execute_batch(self, assignments, tasks, *, scene):
        self.batches.append(tuple(assignments))
        return tuple(ExecutionUpdate(ExecutionFeedback(a.task_id, a.robot_id, 'protocol_ack'),
                                     DependencyOutcome.COMPLETED) for a in assignments)


def test_live_multi_robot_fork_join(native):
    task, executor = dag_package(), ProtocolBatchExecutor()
    wrapper = NativeBTFeedbackExecutor(native, executor)
    result = TaskRunner(orchestration_executor=wrapper).run_orchestrated(TaskRunRequest(task), native, replan=native.replan)
    native.runner_result = result.to_dict()
    assert all(value == 'completed' for value in result.task_states.values())
    assert [set(a.task_id for a in batch) for batch in executor.batches] == [{'root'}, {'left', 'right'}, {'join'}]
    assert len({a.robot_id for a in executor.batches[1]}) == 2
    assert native.last_native_state['graph_mode'] == 'dag'
    assert 'Parallel' in str(native.last_native_state['native_tree'])
    assert native.last_native_state['tree_state'] == 'SUCCEEDED'
    assert result.verdict['status'] == 'N/A'


def test_live_independent_tasks_share_one_slot_serially(native):
    base = package()
    robots = (base.robots[0], replace(base.robots[1], capability=replace(base.robots[1].capability, active=False)))
    nodes = tuple(replace(base.graph.tasks[0], task_id=name, metadata={'bt_native': {'bids': {'robot_a': 1}}}) for name in ('one', 'two'))
    task = replace(base, robots=robots, graph=TaskGraph(nodes, 'single_slot'))
    decision = native(task, task.robots)
    assert len(decision.assignments) == 1
    assert native.replan(task, task.robots).assignments == decision.assignments
    first = decision.assignments[0]
    native.feedback_batch((ExecutionFeedback(first.task_id, first.robot_id, 'ack'),), succeeded=(True,))
    second = native.replan(task, task.robots).assignments
    assert len(second) == 1 and second[0].task_id != first.task_id
    assert second[0].robot_id == first.robot_id
    native.feedback_batch((ExecutionFeedback(second[0].task_id, second[0].robot_id, 'ack'),), succeeded=(True,))
    assert native.last_native_state['tree_state'] == 'SUCCEEDED'
    before = native.last_native_state
    native.replan(task, task.robots)
    assert native.last_native_state == before


def test_live_bad_batch_atomic_then_failure_blocks_join(native):
    task = dag_package()
    root = native(task, task.robots).assignments[0]
    native.feedback(ExecutionFeedback(root.task_id, root.robot_id, 'ack'), succeeded=True)
    branches = native.replan(task, task.robots).assignments
    assert {a.task_id for a in branches} == {'left', 'right'}
    receipts = tuple(ExecutionFeedback(a.task_id, a.robot_id, 'ack') for a in branches)
    before = native.last_native_state
    # Bypass local duplicate checks to exercise endpoint all-or-nothing validation.
    records = [{'task_id': f.task_id, 'robot_id': f.robot_id, 'outcome': f.outcome,
                'failure_code': 'none', 'succeeded': True} for f in receipts]
    for invalid in ([records[0], records[0]], [records[0], dict(records[1], robot_id='wrong')]):
        with pytest.raises(NativeBTError):
            native._request('/feedback_batch', {'mission_id': native.mission_id, 'feedback': invalid})
        native.replan(task, task.robots)
        assert native.last_native_state['leaf_states'] == before['leaf_states']
    failed = replace(receipts[1], failure_code=FailureCode.EXECUTION_FAILURE, outcome='failed')
    native.feedback_batch((receipts[0], failed), succeeded=(True, False))
    assert native.last_native_state['tree_state'] == 'FAILED'
    assert next(s for s in native.last_native_state['leaf_states'] if s['task_id'] == 'join')['feedback'] is None


def test_serial_executor_fallback_collects_all_receipts_before_one_submission():
    sink = ReceiptSink()
    class Executor:
        def execute(self, assignment, task, *, scene):
            assert not sink.calls
            return ExecutionFeedback(assignment.task_id, assignment.robot_id, 'executor_ack')
    wrapper = NativeBTFeedbackExecutor(sink, Executor(), success_predicate=lambda f: f.outcome == 'executor_ack')
    result = wrapper.execute_batch(assignments(), {'left': object(), 'right': object()}, scene=None)
    assert all(isinstance(r, ExecutionUpdate) for r in result)
    assert len(sink.calls) == 1
    assert sink.calls[0][1] == (True, True)


def test_executor_exception_is_not_converted_to_success():
    sink = ReceiptSink()
    class Executor:
        def execute_batch(self, *args, **kwargs):
            raise RuntimeError('execution interrupted')
    wrapper = NativeBTFeedbackExecutor(sink, Executor())
    with pytest.raises(RuntimeError, match='execution interrupted'):
        wrapper.execute_batch(assignments(), {}, scene=None)
    assert sink.calls == []


@pytest.mark.parametrize('predicate,error,message', [
    (lambda f: 1, TypeError, 'explicit boolean'),
    (lambda f: True, NativeBTError, 'contradicts execution failure code'),
])
def test_predicate_cannot_mask_failure_or_supply_truthy_non_boolean(predicate, error, message):
    sink = ReceiptSink()
    executor = BatchExecutor(tuple(r.feedback for r in updates()))
    wrapper = NativeBTFeedbackExecutor(sink, executor, success_predicate=predicate)
    with pytest.raises(error, match=message):
        wrapper.execute_batch(assignments(), {}, scene=None)
    assert sink.calls == []


def test_wrapper_forwards_runner_binding():
    calls = []
    class Executor:
        def bind_run(self, request):
            calls.append(request)
    request = TaskRunRequest(dag_package())
    NativeBTFeedbackExecutor(ReceiptSink(), Executor()).bind_run(request)
    assert calls == [request]


def test_replan_does_not_invent_feedback_from_scheduler_state(client_receipt, monkeypatch):
    adapter, result = client_receipt
    calls = []
    def request(route, body):
        calls.append((route, body))
        return result
    monkeypatch.setattr(adapter, '_request', request)
    task = dag_package()
    adapter.replan(task, task.robots, state={'completed_task_ids': ('root', 'left', 'right')})
    assert calls == [('/state', {'mission_id': 'fixture'})]


@pytest.mark.parametrize('field,value,message', [
    ('mission_id', 'other-mission', 'mission_id'),
    ('mission_id', '', 'mission_id'),
    ('mission_id', None, 'mission_id'),
    ('task_contract_sha256', '0' * 64, 'task_contract_sha256'),
    ('task_contract_sha256', None, 'task_contract_sha256'),
    ('native_tree', 'ros_bt_py.nodes.sequence.MemorySequence', 'native_tree'),
    ('native_tree', None, 'native_tree'),
])
def test_receipt_must_match_active_mission_contract_and_tree(client_receipt, field, value, message):
    adapter, result = client_receipt
    result[field] = value
    with pytest.raises(NativeBTError, match=message):
        adapter._decision(result)
    assert adapter.last_native_state is result


def test_payload_mutation_invalidates_existing_receipt(client_receipt):
    adapter, result = client_receipt
    adapter._payload['tasks'][0]['target_ref'] = '/World/DifferentTarget'
    with pytest.raises(NativeBTError, match='task_contract_sha256'):
        adapter._decision(result)


@pytest.mark.parametrize('case,message', [('task', 'unknown task'), ('robot', 'unknown robot'), ('bid', 'bid eligibility')])
def test_assignments_require_known_task_robot_and_explicit_eligibility(client_receipt, case, message):
    adapter, result = client_receipt
    assignment = {'task_id': 'root', 'robot_id': 'robot_a'}
    if case == 'task':
        assignment['task_id'] = 'unknown_task'
    elif case == 'robot':
        assignment['robot_id'] = 'unknown_robot'
    else:
        adapter._payload['tasks'][2]['bids'].pop('robot_a')
        result['task_contract_sha256'] = contract_hash(adapter._payload)
    result['assignments'] = [assignment]
    with pytest.raises(NativeBTError, match=message):
        adapter._decision(result)


def test_valid_assignment_accepts_only_exact_receipt_binding(client_receipt):
    adapter, result = client_receipt
    result['assignments'] = [{'task_id': 'root', 'robot_id': 'robot_a'}]
    decision = adapter._decision(result)
    assert decision.assignments == (Assignment('root', 'robot_a', 'BT-capabilities-native', 'fixture'),)
    assert decision.provenance['native_evidence']['task_contract_sha256'] == contract_hash(adapter._payload)


@pytest.mark.parametrize('route', ['/feedback', '/feedback_batch'])
def test_feedback_response_uses_full_receipt_validation(client_receipt, monkeypatch, route):
    adapter, result = client_receipt
    result['task_contract_sha256'] = '0' * 64
    calls = []
    def request(actual_route, body):
        calls.append((actual_route, body))
        return result
    monkeypatch.setattr(adapter, '_request', request)
    feedback = ExecutionFeedback('root', 'robot_a', 'executor_ack')
    with pytest.raises(NativeBTError, match='task_contract_sha256'):
        if route == '/feedback':
            adapter.feedback(feedback, succeeded=True)
        else:
            adapter.feedback_batch((feedback,), succeeded=(True,))
    assert len(calls) == 1 and calls[0][0] == route
    assert adapter.last_native_state is result


def test_empty_graph_is_rejected_at_encoder_boundary():
    task = replace(package(), graph=SimpleNamespace(tasks=(), valid=True))
    with pytest.raises(ValueError, match='nonempty task graph'):
        encode_task(task, task.robots)


@pytest.mark.parametrize('case', ['subset', 'replacement', 'empty_package', 'capabilities', 'active'])
def test_runtime_roster_must_match_package(case):
    task = package()
    robots = task.robots
    if case == 'subset':
        robots = robots[:1]
    elif case == 'replacement':
        robots = (robots[0], replace(robots[1], capability=replace(robots[1].capability, robot_id='robot_c')))
    elif case == 'empty_package':
        task = replace(task, robots=())
    elif case == 'capabilities':
        robots = (robots[0], replace(robots[1], capability=replace(robots[1].capability, capabilities=frozenset())))
    else:
        robots = (robots[0], replace(robots[1], capability=replace(robots[1].capability, active=False)))
    with pytest.raises(ValueError, match='runtime robot roster differs from task package'):
        encode_task(task, robots)


def test_roster_order_can_differ_without_changing_eligibility():
    task = package()
    encoded = encode_task(task, tuple(reversed(task.robots)))
    assert encoded['robot_ids'] == ['robot_b', 'robot_a']
    assert encoded['tasks'][0]['bids'] == {'robot_a': 9.0, 'robot_b': 2.0}


def test_unknown_supplied_bid_is_rejected_not_silently_filtered():
    task = package(bids={'robot_a': 1, 'robot_b': 2, 'typo_robot': 3})
    with pytest.raises(ValueError, match='unknown supplied bid robot IDs'):
        encode_task(task, task.robots)
