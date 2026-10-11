from types import SimpleNamespace

import pytest

from algorithm.skills.pepper_transport.checkpoint import PepperTransportCheckpoint


class GraspState:
    def __init__(self):
        self.config = SimpleNamespace(box_size=(.2,.3,.3))
        self.attached = True

    def snapshot(self):
        return {'attached':self.attached}


class Transport:
    def __init__(self):
        self.config = SimpleNamespace(destination=(0,.75,0))
        self.tick = 2

    def snapshot(self):
        return {'tick':self.tick}


def adapter():
    handle = SimpleNamespace(plan=SimpleNamespace(request_id='r1',robot_id='pepper_1'),
        controller={'runtime':SimpleNamespace(state=GraspState()),'transport':Transport(),
                    'robot_type':'Pepper','locomotion_controller':'PEPPER_HOLONOMIC_CFG',
                    'auto_start':True,'collision_filter_installed':True,'execution_enabled':True},
        env=SimpleNamespace(),ticks=10,stable_ticks=0,settle_ticks=3,cancel_reason=None,_receipt=None)
    return PepperTransportCheckpoint(handle), handle


@pytest.mark.parametrize('mutation',['gate','fields','stable','cancel'])
def test_corrupt_checkpoint_rejected_without_controller_mutation(mutation):
    component,handle=adapter()
    state=component.capture_state()
    if mutation in ('gate','fields'):
        values=component.codec.decode(state['controller'])
        if mutation=='gate':values['execution_enabled']=False
        else:del values['auto_start']
        state['controller']=component.codec.encode(values)
    elif mutation=='stable':state['handle']['stable_ticks']=3
    else:state['handle']['cancel_reason']=''
    with pytest.raises(ValueError):component.restore_state(state)
    assert handle.controller['execution_enabled'] is True and handle.ticks==10


def test_active_checkpoint_restores_in_place_and_rejects_infinite_tolerance():
    component,handle=adapter()
    state=component.capture_state()
    runtime=handle.controller['runtime']
    handle.ticks=20
    handle.controller['runtime'].state.attached=False
    component.restore_state(state)
    assert handle.controller['runtime'] is runtime
    assert runtime.state.attached and handle.ticks==10
    with pytest.raises(ValueError,match='tolerance'):
        component.attachment_matches_physics(tolerance_m=float('inf'))
