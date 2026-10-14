from dataclasses import replace
import pytest
from benchmark.dynamic_events import AvailabilityEvent, AvailabilityScheduler
from benchmark.fleet import FleetLifecycle
from benchmark.tests.test_fleet import _runtime, _binding
from types import SimpleNamespace


def event(**changes):
    return replace(AvailabilityEvent('loss', 'robot-a', 'navigate', 'dep.v1', 2, 0,
        ('obligation',), aftermath_contract='retain-current-world'), **changes)


def receipt(event, available, tick):
    return dict(event_id=event.event_id, robot_id=event.robot_id, function=event.function,
        available=available, tick=tick, dependency_version=event.dependency_version,
        aftermath_contract=event.aftermath_contract, evidence_hash=f'readback-{tick}',
        guard_verified=True, world_preserved=True)


def fleet():
    f, _ = _runtime()
    f.start(SimpleNamespace(), _binding())
    return f


def test_loss_restore_invalidates_commands_preserves_other_components_and_locks():
    f = fleet()
    e = event(restoration_policy='fixed', restoration_tick=4, notification_policy='onset',
              notice_tick=2, recipients=('robot-b',), public_fields={'function': 'navigate'})
    schedule = AvailabilityScheduler((e,), operation=receipt)
    schedule.advance(0, f)
    schedule.advance(2, f)
    assert f.available_capabilities('robot-a') == frozenset()
    assert f.available_capabilities('robot-b') == frozenset({'handoff'})
    assert f.command_generation('robot-a') == 1
    assert f.receive('robot-b', tick=2)[0].payload == {'function': 'navigate'}
    schedule.advance(2, f)
    assert f.command_generation('robot-a') == 1
    schedule.advance(4, f)
    assert f.command_generation('robot-a') == 2
    assert f.available_capabilities('robot-a') == frozenset({'navigate'})
    assert schedule.snapshot()['actual_restoration_ticks'] == {'loss': 4}
    assert schedule.snapshot()['strict_replay_qualified'] is False


def test_unverified_or_partial_operation_requests_global_stop():
    f = fleet()
    def bad(e, a, t):
        return {**receipt(e, a, t), 'world_preserved': False}
    schedule = AvailabilityScheduler((event(),), operation=bad)
    with pytest.raises(ValueError, match='preservation'):
        schedule.advance(2, f)
    assert f.lifecycle is FleetLifecycle.STOP_REQUESTED
    assert not f.safe_stop.status().acknowledged_robot_ids


def test_timing_and_maintenance_contracts():
    f = fleet()
    schedule = AvailabilityScheduler((event(restoration_policy='maintenance'),), operation=receipt)
    with pytest.raises(RuntimeError, match='skipped'):
        schedule.advance(3, f)
    schedule.advance(2, f)
    with pytest.raises(ValueError):
        schedule.restore_after_maintenance('loss', tick=2, evidence_hash='action', fleet=f)
    with pytest.raises(ValueError):
        schedule.restore_after_maintenance('loss', tick=3, evidence_hash='', fleet=f)
    schedule.restore_after_maintenance('loss', tick=3, evidence_hash='action-readback', fleet=f)
    assert schedule.snapshot()['last_tick'] == 3
    with pytest.raises(ValueError):
        schedule.advance(2, f)
    with pytest.raises(ValueError):
        event(notification_policy='advance_notice', notice_tick=0, recipients=('robot-b',))
    with pytest.raises(ValueError):
        event(public_fields={'hidden': True})


def test_component_overlap_is_rejected_and_notice_payload_detached():
    with pytest.raises(ValueError, match='overlapping'):
        AvailabilityScheduler((event(), event(event_id='second', onset_tick=4)), operation=receipt)
    public = {'component': {'name': 'navigate'}}
    e = event(notification_policy='advance_notice', notice_tick=1, recipients=('robot-b',), public_fields=public)
    public['component']['name'] = 'hidden evaluator field'
    f = fleet()
    AvailabilityScheduler((e,), operation=receipt).advance(1, f)
    assert f.receive('robot-b', tick=1)[0].payload == {'component': {'name': 'navigate'}}
