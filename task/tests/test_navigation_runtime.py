import pytest

from algorithm.skills.contracts import SkillRequest
from algorithm.skills.navigation.provider import NavigationProvider
from algorithm.skills.navigation.runtime import NavigationHandle
from task import DependencyOutcome


def plan(planner=None):
    provider = NavigationProvider(planner or (lambda start, target: [start, target]), planner_version="fixture")
    return provider.plan(SkillRequest("n1", provider.skill_id, "r1", target_pose=(1, 2)),
                         observation={"robots": [{"robot_id": "r1", "active": True, "position": [0, 0, 0]}]})


def test_plan_uses_existing_planner_and_rejects_wrong_goal():
    calls = []
    def planner(start, target):
        calls.append((start, target))
        return [start, target]
    assert plan(planner).commands[0].command_type == "navigate_waypoints"
    assert calls == [((0, 0), (1, 2))]
    with pytest.raises(ValueError, match="requested target"):
        plan(lambda start, target: [start, (7, 7)])
    assert plan(lambda start, target: []).status == "unreachable"


def test_goal_requires_measured_arrival_and_stop():
    live = {"pose": (0, 0, 0), "stopped": False}
    handle = NavigationHandle(plan(), install_path=lambda *a: None, track=lambda *a: "track",
        pose=lambda: live["pose"], stopped=lambda: live["stopped"], make_action=lambda x: x,
        stop_action=lambda: "stop", arrival_radius=0.05, settle_ticks=2)
    assert handle.command() == "track"
    live["pose"] = (1, 2, 0)
    assert handle.command() == "stop" and handle.after_step() is None
    live["stopped"] = True
    assert handle.after_step() is None
    assert handle.after_step().outcome is DependencyOutcome.COMPLETED


def test_cancel_waits_for_measured_stopping_and_does_not_complete():
    live = {"stopped": False}
    handle = NavigationHandle(plan(), install_path=lambda *a: None, track=lambda *a: "track",
        pose=lambda: (0, 0, 0), stopped=lambda: live["stopped"], make_action=lambda x: x,
        stop_action=lambda: "stop", arrival_radius=0.05, settle_ticks=1)
    handle.cancel("requested")
    assert handle.command() == "stop" and handle.after_step() is None
    live["stopped"] = True
    assert handle.after_step().outcome is DependencyOutcome.TERMINAL_FAILURE


def test_grid_endpoint_tolerance_keeps_original_goal_and_bounds_admission():
    provider = NavigationProvider(lambda start, target: [start, (1.025, 2.025)],
                                  planner_version='grid', endpoint_tolerance_m=.04)
    request = SkillRequest('grid',provider.skill_id,'r1',target_pose=(1,2))
    planned = provider.plan(request, observation={'robots':[{'robot_id':'r1','active':True,'position':[0,0,0]}]})
    assert planned.commands[0].payload['target'] == (1.,2.)
    installed = []
    with pytest.raises(ValueError,match='arrival radius'):
        NavigationHandle(planned, install_path=lambda *a:installed.append(a),track=lambda *a:None,
            pose=lambda:(1,2,0),stopped=lambda:True,make_action=lambda v:v,stop_action=lambda:0,
            arrival_radius=.02)
    assert not installed


def test_unsupported_elevation_and_waypoint_dimensions_are_rejected():
    provider = NavigationProvider(lambda a,b:[a,b],planner_version='test')
    with pytest.raises(ValueError,match='elevation'):
        provider.plan(SkillRequest('z',provider.skill_id,'r1',target_pose=(1,2,3)))
    with pytest.raises(ValueError,match='waypoint'):
        plan(lambda a,b:[(0,0,0,7),b])
