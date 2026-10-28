from copy import deepcopy
from types import SimpleNamespace

import pytest

from task.checkpoint import SkillRuntimeComponent
from task.resources import ResourceManager, ResourceQuarantinedError
from tools.physics_replay.components import ComponentRegistry


class Adapter:
    def __init__(self):
        self.state = {"phase": "carry", "attached": True, "tick": 12}

    def capture_state(self):
        return deepcopy(self.state)

    def validate_state(self, state):
        if set(state) != set(self.state) or type(state['attached']) is not bool:
            raise ValueError('invalid skill state')

    def restore_state(self, state):
        self.state = deepcopy(state)


def fixture(reconcile=lambda state: True, boundary=lambda: True):
    resources = ResourceManager()
    lease = resources.acquire('request', ('robot:pepper', 'object:box'))
    adapter = Adapter()
    component = SkillRuntimeComponent(scene_id='plane', dependency_digest='source-v1',
        resources=resources, skills={'request':adapter}, boundary_check=boundary, reconcile=reconcile)
    return component, adapter, resources, lease


def test_registry_restore_preserves_authentic_lease_and_restores_held_state():
    component, adapter, resources, lease = fixture()
    registry = ComponentRegistry((component,))
    saved = registry.capture_all()
    adapter.state.update(phase='place', tick=30)
    registry.restore_all(saved)
    assert adapter.state == {'phase':'carry','attached':True,'tick':12}
    assert resources.snapshot()['leases'][0]['lease_id'] == lease.lease_id
    resources.release(lease)


def test_changed_ownership_or_session_rejected_before_writes():
    component, adapter, resources, lease = fixture()
    saved = component.capture_state()
    resources.release(lease)
    resources.acquire('new', ('object:box',))
    adapter.state['tick'] = 99
    with pytest.raises(ValueError, match='ownership'):
        component.restore_state(saved)
    assert adapter.state['tick'] == 99
    other, *_ = fixture()
    with pytest.raises(ValueError, match='binding'):
        other.restore_state(saved)


def test_failed_logical_reconciliation_quarantines_and_latches_failure():
    component, adapter, resources, lease = fixture(reconcile=lambda state: False)
    saved = component.capture_state()
    with pytest.raises(RuntimeError, match='session reconciliation'):
        component.restore_state(saved)
    with pytest.raises(ResourceQuarantinedError):
        resources.release(lease)
    assert component.quarantined_leases == (lease,)
    with pytest.raises(RuntimeError, match='restore failed'):
        component.capture_state()


def test_wrong_boundary_and_dependency_rejected():
    component, *_ = fixture(boundary=lambda: 1)
    with pytest.raises(RuntimeError, match='boundary'):
        component.capture_state()
    component, *_ = fixture()
    saved = component.capture_state()
    saved['dependency_digest'] = 'different'
    with pytest.raises(ValueError, match='binding'):
        component.restore_state(saved)


@pytest.mark.parametrize('failure', ['later_component', 'final_readback'])
def test_outer_replay_failure_quarantines_even_after_skill_restore(tmp_path, failure):
    from tools.physics_replay.live import LiveCheckpointBackend, LiveCheckpoint
    from tools.physics_replay.components import CallbackComponent, ComponentSnapshot
    component, adapter, resources, lease = fixture()
    def later(state):
        if failure == 'later_component':
            raise RuntimeError('later restore failed')
    registry = ComponentRegistry((component,CallbackComponent('later',1,lambda:{},later,priority=100)))
    snapshots = registry.capture_all()
    snapshots['physics'] = ComponentSnapshot('physics',1,{'step_index':12,'targets':[], 'state':{'robot/velocity':[0.]}})
    backend = LiveCheckpointBackend.__new__(LiveCheckpointBackend)
    backend.checkpoints = [LiveCheckpoint(0,12,'test')]
    backend.restore_check = lambda:None
    backend.store = SimpleNamespace(read=lambda identifier:deepcopy(snapshots))
    backend.targets = []
    backend.registry = registry
    backend.replayer = SimpleNamespace(validate_snapshot=lambda s:None,
        restore_snapshot=lambda s,i:SimpleNamespace(ok=True,summary='ok'))
    backend.read_physics = lambda:{'robot/velocity':[1.]}
    backend.output_dir = tmp_path
    backend.tolerance = 1e-5
    with pytest.raises(RuntimeError):
        backend.restore(0)
    assert component.failed
    with pytest.raises(ResourceQuarantinedError):
        resources.release(lease)


def test_missing_skill_adapter_for_lease_owner_is_rejected():
    component, _, resources, _ = fixture()
    resources.acquire('other',('robot:second',))
    with pytest.raises(ValueError,match='every active lease owner'):
        component.capture_state()
