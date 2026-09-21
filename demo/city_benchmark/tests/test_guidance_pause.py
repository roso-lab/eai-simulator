"""A waiting human must accept guidance without using idle as a gait."""
from types import SimpleNamespace
from dataclasses import replace

from EAI_assets.humans.path_follower import PathFollower
from EAI_assets.humans.spawner import SpawnedHuman
from demo.city_benchmark.scene.people import (
    EpisodeAffectedPeopleController, EpisodeAffectedPersonRoster,
)


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
