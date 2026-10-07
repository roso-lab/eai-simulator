"""Pure-Python Fire Rescue task/skill fixture for COHERENT adapter tests."""

from __future__ import annotations

from .contract import CapabilityProfile, SkillSpec, TaskSpec


def fire_rescue_fixture() -> tuple[tuple[CapabilityProfile, ...], tuple[SkillSpec, ...], tuple[TaskSpec, ...]]:
    """Return a small typed contract, not an Isaac scene or physics result.

    The task graph deliberately contains a handoff: the scout must locate the
    fire before suppression, while the carrier can clear the route in parallel
    at the planning level.  EAI callers map these skill names to typed
    controllers and must perform physical milestone validation themselves.
    """

    robots = (
        CapabilityProfile("scout_uav", frozenset({"navigate", "thermal_scan", "report"})),
        CapabilityProfile("carrier_ugv", frozenset({"navigate", "carry", "clear_route"})),
        CapabilityProfile("suppression_arm", frozenset({"navigate", "extinguish", "verify"})),
    )
    skills = (
        SkillSpec("scan_fire", frozenset({"thermal_scan"})),
        SkillSpec("clear_route", frozenset({"clear_route"})),
        SkillSpec("deliver_extinguisher", frozenset({"carry"})),
        SkillSpec("suppress_fire", frozenset({"extinguish"})),
        SkillSpec("verify_safe", frozenset({"verify"})),
    )
    tasks = (
        TaskSpec("locate_fire", "Locate and report the fire source.", frozenset({"thermal_scan"}), preferred_skills=("scan_fire",)),
        TaskSpec("clear_route", "Clear the route to the fire zone.", frozenset({"clear_route"}), preferred_skills=("clear_route",)),
        TaskSpec("deliver_extinguisher", "Deliver an extinguisher to the suppression robot.", frozenset({"carry"}), dependencies=("locate_fire",), preferred_skills=("deliver_extinguisher",)),
        TaskSpec("suppress_fire", "Suppress the located fire.", frozenset({"extinguish"}), dependencies=("locate_fire", "deliver_extinguisher"), preferred_skills=("suppress_fire",)),
        TaskSpec("verify_safe", "Verify the area is safe for re-entry.", frozenset({"verify"}), dependencies=("suppress_fire",), preferred_skills=("verify_safe",)),
    )
    return robots, skills, tasks
