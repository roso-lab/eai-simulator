from types import SimpleNamespace

import pytest

from demo.city_benchmark.live_witness import CityDemoRuntimeWitnessAdapter, CityIncidentReceiver


def test_receipt_identity_is_bound_to_entire_request_and_defensive_copy():
    receiver = CityIncidentReceiver(episode_id="episode", service_id="dispatch")
    payload = {"request_id": "request", "episode_id": "episode", "recipient_service_id": "dispatch",
               "observation_refs": ["observation"], "submitted_time_s": 1., "agent_id": "robot"}
    receipt = receiver.receive(payload, now_s=2.)
    receipt["observation_refs"].append("forged")
    assert receiver.receipt("request")["observation_refs"] == ["observation"]
    assert receiver.receive(payload, now_s=3.)["received_time_s"] == 2.
    for changed in ({"submitted_time_s": 1.5}, {"agent_id": "other"}):
        with pytest.raises(ValueError, match="different payload"):
            receiver.receive({**payload, **changed}, now_s=3.)


def test_pose_change_does_not_default_to_safe_evacuation():
    runtime = SimpleNamespace(elapsed=1., people=SimpleNamespace(position=lambda _: [1., 0., 0.]))
    adapter = CityDemoRuntimeWitnessAdapter(runtime, episode_id="episode")
    result = adapter.capture_person_readback("person", previous_position=[0., 0., 0.],
                                            status_before="awaiting", arrived=True)
    assert result["measured_trajectory"]
    assert result["safe_route_and_assembly"] is False
    with pytest.raises(ValueError, match="explicit host booleans"):
        adapter.capture_person_readback("person", previous_position=[0., 0., 0.],
                                       status_before="awaiting", arrived="false")
