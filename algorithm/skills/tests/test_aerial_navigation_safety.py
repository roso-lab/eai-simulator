from dataclasses import replace

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation.aerial_navigation import AerialNavigationProvider
from algorithm.skills.tests.test_aerial_navigation import request, observation, topology


@pytest.mark.parametrize("change", [
    {"budget": None}, {"timing": None}, {"budget": {"duration_s": 100}},
    {"branch_context": {"branch": "other"}},
])
def test_control_cannot_redefine_fixed_task_context(change):
    p = AerialNavigationProvider()
    initial = request(budget={"duration_s": 1}, timing={"deadline_s": 1}, branch_context={"branch": "fact"})
    p.dispatch(initial, observation=observation(simulation_time_s=0))
    p.control(initial, observation=observation(simulation_time_s=.1))
    with pytest.raises(SkillContractError, match="immutable"):
        p.control(replace(initial, **change), observation=observation(simulation_time_s=10))
    failed = p.control(replace(initial, request_id="continue", parameters={"intent": "continue", "world_state_seq": 1}),
                       observation=observation(simulation_time_s=10))
    assert failed.state["reason_code"] == "navigation_timeout"


def test_hold_tracks_clock_even_after_deadline_and_rejects_rewind():
    p = AerialNavigationProvider()
    initial = request(timing={"deadline_s": 5})
    p.dispatch(initial, observation=observation(simulation_time_s=0))
    p.control(initial, observation=observation(simulation_time_s=1))
    held = p.control(replace(initial, parameters={"intent": "hold", "world_state_seq": 1}),
                     observation=observation(simulation_time_s=20))
    assert held.state["reason_code"] == "system_hold"
    resumed = p.control(replace(initial, parameters={"intent": "continue", "world_state_seq": 1}),
                        observation=observation(simulation_time_s=2))
    assert resumed.state["status"] == "failed"
    assert resumed.state["execution_state"] == "holding"


def test_graph_frozen_at_first_route_and_observations_have_provenance():
    p = AerialNavigationProvider()
    initial = request(branch_context={"branch": "fact"})
    p.dispatch(initial, observation=observation(simulation_time_s=0))
    first = p.control(initial, observation=observation(simulation_time_s=.1))
    assert first.plan.metadata["observation_digest"]
    assert first.plan.metadata["task_id"] == "flight"
    assert first.plan.metadata["branch_context"] == {"branch": "fact"}
    changed = topology()
    changed["nodes"][1]["position_xyz"] = [6, 6, 3]
    result = p.control(request("replan", branch_context={"branch": "fact"}),
                       observation=observation(topology=changed, simulation_time_s=.2))
    assert result.state["status"] == "failed"
