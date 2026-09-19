"""Scene-neutral participant protocol episode harness.

This harness exercises the public event/action contract without starting Isaac.
It is used for adapter conformance and replay; Isaac execution remains owned by
the scene runtime until its action-to-scheduler integration is enabled.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .participant_runtime import ParticipantAdapter, ParticipantSession


def run_public_episode(bundle: Mapping[str, Mapping[str, Any]], *, adapter: ParticipantAdapter,
                       step_s: float = 1.0) -> dict[str, Any]:
    """Run a deterministic public-only event sequence through an adapter."""
    if step_s <= 0:
        raise ValueError("step_s must be positive")
    public = deepcopy(dict(bundle["public_task"]))
    session = ParticipantSession(adapter=adapter, api=bundle["participant_api"], task_spec=public)
    fleet = [robot["id"] for robot in public["initial_fleet"]]
    session.emit("episode_started", time_s=0.0, payload={
        "fleet": fleet,
        "objectives": public["objectives"],
        "resources": public.get("resources", []),
    })
    actions_by_objective = {}
    controlled = set(public["participant"]["controlled_objectives"])
    for index, objective in enumerate(
            item for item in public["objectives"] if item["id"] in controlled):
        eligible = list(fleet)
        session.emit("phase_ready", time_s=(index + 1) * step_s, payload={
            "node_id": objective["id"], "eligible_agent_ids": eligible,
        })
        actions = session.request_actions()
        actions_by_objective[objective["id"]] = [action["action_id"] for action in actions]
        for action in actions:
            session.emit("action_result", time_s=(index + 1) * step_s,
                         payload={"action_id": action["action_id"], "status": "accepted", "reason": "protocol_harness"})
    report = session.report()
    report["harness"] = {
        "kind": "public_protocol_only",
        "action_effect": "validated_and_logged; no Isaac scheduler mutation",
        "actions_by_objective": actions_by_objective,
    }
    return report


__all__ = ["run_public_episode"]
