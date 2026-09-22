"""A waiting human must accept guidance without using idle as a gait."""
from types import SimpleNamespace
from dataclasses import replace

from EAI_assets.humans.path_follower import PathFollower
from EAI_assets.humans.spawner import SpawnedHuman
from demo.city_benchmark.scene.people import (
    EpisodeAffectedPeopleController, EpisodeAffectedPersonRoster,
)
from demo.city_benchmark.scene.runtime import CityEpisodeServiceRuntime


def test_guidance_uses_declared_motion_after_waiting_idle():
    actor = SpawnedHuman("person", "asset", movement_mode="path",
                         follower=PathFollower("person", [(0, 0, 0), (4, 0, 0)], speed=.8))
    actor.reset_path()
    lease = actor.acquire_movement_pause("manual", "test")
    # The stage runtime publishes an idle pose while the movement gate waits.
    actor._pending_pose = replace(actor.pending_pose, locomotion="idle")
    assert actor.pending_pose.locomotion == "idle"
    runtime = SimpleNamespace(
        actor_ids=["person"], position=lambda _: actor.pending_pose.position,
        _actor=lambda _: actor, _write_pose=lambda *_: None)
    roster = EpisodeAffectedPersonRoster(runtime, {"person": {
        "speed_mps": 1.8, "motion": "jog", "assembly_region_id": "assembly",
        "assembly_waypoints": [[4, 0, 0]],
    }}, episode_id="episode", source_id="test")
    controller = EpisodeAffectedPeopleController(runtime, roster)
    assert controller.guide("person")["status_after"] == "guidance_applied"
    lease.release()
    actor.advance_path(1.)
    assert actor.pending_pose.position[0] == 1.8


def test_partial_failure_pauses_native_person_and_retry_resumes():
    actor = SpawnedHuman("person", "asset", movement_mode="path",
                         follower=PathFollower("person", [(0, 0, 0), (4, 0, 0)], speed=.8))
    actor.reset_path()
    leases = {}

    def pause(actor_id):
        leases[actor_id] = actor.acquire_movement_pause("manual", "partial_failure")

    def resume(actor_id):
        leases.pop(actor_id).release()

    runtime = SimpleNamespace(
        actor_ids=["person"], position=lambda _: actor.pending_pose.position,
        _actor=lambda _: actor, _write_pose=lambda *_: None,
        pause=pause, resume=resume)
    roster = EpisodeAffectedPersonRoster(runtime, {"person": {
        "speed_mps": 1.8, "motion": "jog", "assembly_region_id": "assembly",
        "assembly_waypoints": [[4, 0, 0]],
    }}, episode_id="episode", source_id="test")
    controller = EpisodeAffectedPeopleController(runtime, roster)
    controller.guide("person")
    assert controller.stop("person")["status_after"] == "paused"
    actor.advance_path(1.)
    assert actor.pending_pose.position[0] == 0.
    controller.guide("person")
    actor.advance_path(1.)
    assert actor.pending_pose.position[0] == 1.8


def test_native_vehicle_stop_uses_hold_and_clears_inflight_claim():
    calls = []
    service = CityEpisodeServiceRuntime.__new__(CityEpisodeServiceRuntime)
    service.city = SimpleNamespace(
        elapsed=5., episode_id="episode", city_service_source_id="native",
        control_following_vehicle=lambda vehicle_id, mode, **kwargs: (
            calls.append((vehicle_id, mode)) or
            {"vehicle_id": vehicle_id, "status_before": "divert", "status_after": mode,
             "episode_id": "episode", "source_id": "native", "observed_time_s": 5.,
             "evidence_ref": "native:hold:v1"}),
        following_vehicle_pose=lambda _: (1., 2., 0.))
    service._vehicle_service_starts = {"v1": 3.}
    service.divert_routes = {}
    acknowledgement = service.control_vehicle("v1", "hold")
    receipt = service.stop_vehicle("v1")
    assert calls == [("v1", "hold"), ("v1", "hold")]
    assert receipt["status_after"] == "hold" and receipt["position_xyz"] == [1., 2., 0.]
    assert acknowledgement["evidence_ref"] != receipt["evidence_ref"]
    assert receipt["evidence_ref"] == "native:vehicle_stop:v1:000005000"
    assert receipt["episode_id"] == acknowledgement["episode_id"] == "episode"
    assert receipt["source_id"] == acknowledgement["source_id"] == "native"
    assert receipt["observed_time_s"] == acknowledgement["observed_time_s"] == 5.
    assert service._vehicle_service_starts == {}


def test_native_guidance_admission_counts_people_already_in_flight():
    service = CityEpisodeServiceRuntime.__new__(CityEpisodeServiceRuntime)
    service.city = SimpleNamespace(elapsed=5.)
    roster = {person_id: {"assembly_region_id": "assembly_1"}
              for person_id in ("p1", "p2", "p3")}
    service.affected_person_roster = lambda: roster
    service._person_route = lambda _: {"clear": True}
    region = {"status": "measured", "static_hits": [], "dynamic_envelopes": {},
              "region_metadata": {"capacity": 1}}
    service._latest_region = lambda _: region
    service._person_service_starts = {}

    def admitted(*people, target="assembly_1"):
        return service.admit("guide_people", people, "road_surface", target, None, 5.)

    assert admitted("p1")
    assert not admitted("p1", "p2")
    service._person_service_starts["p1"] = 5.
    assert not admitted("p2")
    assert admitted("p1")  # The same person's claim occupies only one slot.
    assert not admitted("p1", target="assembly_2")
    region["dynamic_envelopes"] = {"p1": {}}
    assert admitted("p1")
    assert not admitted("p2")
    region["region_metadata"]["capacity"] = 2
    assert admitted("p2")
    service._person_service_starts["p2"] = 5.
    assert not admitted("p3")
