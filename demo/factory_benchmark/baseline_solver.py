"""Reference information policies for Factory inventory dispatch.

These policies are comparison baselines.  They are not task requirements and
their outputs are never embedded in the public task specification.
"""

from __future__ import annotations

import math
from copy import deepcopy

from demo.factory_benchmark.inventory_dispatch import decide_dispatch, select_destination


POLICIES = ("immediate", "wait_for_message")


class BaselineDispatchAdapter:
    """Reference adapter that consumes only participant-visible event payloads."""

    def __init__(self, *, policy: str, max_wait_s: float):
        if policy not in POLICIES:
            raise ValueError("unknown dispatch baseline policy")
        if (isinstance(max_wait_s, bool) or not isinstance(max_wait_s, (int, float))
                or not math.isfinite(max_wait_s) or max_wait_s < 0):
            raise ValueError("baseline wait budget must be nonnegative")
        self.policy = policy
        self.max_wait_s = float(max_wait_s)
        self._phase = None
        self._messages = {}
        self._committed = False
        self._wait_emitted = False
        self._next_action = 0

    def manifest(self):
        return {
            "kind": "baseline_solver",
            "id": self.policy,
            "version": 1,
            "parameters": {"max_wait_s": self.max_wait_s},
            "input_scope": "participant_visible_events_only",
        }

    def _action(self, action_type, submitted_at_s, payload):
        self._next_action += 1
        return {
            "schema": "eai.benchmark.participant_action",
            "version": 1,
            "action_id": f"{self.policy}:{self._next_action}",
            "submitted_at_s": submitted_at_s,
            "type": action_type,
            "payload": payload,
        }

    def act(self, events):
        if self._committed:
            return []
        now = None
        for event in events:
            now = event["time_s"] if now is None else max(now, event["time_s"])
            if event["type"] == "phase_ready":
                self._phase = {
                    "node_id": event["payload"]["node_id"],
                    "agent_id": event["payload"]["eligible_agent_ids"][0],
                    "ready_at_s": event["time_s"],
                }
            elif event["type"] == "observation_message":
                payload = event["payload"]
                self._messages[payload["message_id"]] = {
                    "message_id": payload["message_id"],
                    "estimate": deepcopy(payload["estimate"]),
                }
        if self._phase is None or now is None:
            return []
        selection = select_destination(list(self._messages.values()))
        informed = selection["reason"] == "decoded_identity"
        waited = now - self._phase["ready_at_s"]
        if self.policy == "wait_for_message" and not informed and waited + 1e-9 < self.max_wait_s:
            if self._wait_emitted:
                return []
            self._wait_emitted = True
            return [self._action("wait", now, {
                "node_id": self._phase["node_id"],
                "agent_id": self._phase["agent_id"],
                "duration_s": self.max_wait_s,
            })]
        actions = [self._action("consume_message", now, {
            "message_id": message_id,
            "consumer_id": self._phase["node_id"],
        }) for message_id in selection["used_message_ids"]]
        actions.append(self._action("navigate_goal", now, {
            "node_id": self._phase["node_id"],
            "agent_id": self._phase["agent_id"],
            "target_ref": selection["target_ref"],
        }))
        self._committed = True
        return actions


def dispatch_policy_decision(messages, *, mode, delay_s, time_s, policy,
                             ready_at_s, max_wait_s):
    """Return a baseline commitment, or ``None`` while this baseline waits."""
    if policy not in POLICIES:
        raise ValueError("unknown dispatch decision policy")
    values = (ready_at_s, max_wait_s, time_s)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("dispatch times must be finite and nonnegative")
    ready_at, max_wait, now = map(float, values)
    if any(not math.isfinite(value) or value < 0 for value in (ready_at, max_wait, now)):
        raise ValueError("dispatch times must be finite and nonnegative")
    if now < ready_at:
        raise ValueError("dispatch decision time cannot precede stage readiness")
    decision = decide_dispatch(messages, mode=mode, delay_s=delay_s, time_s=now)
    waited = now - ready_at
    pending = any(item["status"] == "not_yet_available" for item in decision["channel_ledger"])
    informed = decision["reason"] == "decoded_identity"
    if policy == "wait_for_message" and pending and not informed and waited + 1e-9 < max_wait:
        return None
    return {**decision, "decision_policy": policy, "ready_at_s": ready_at,
            "waited_s": waited, "max_wait_s": max_wait,
            "commit_reason": ("information_available" if informed else
                              "wait_budget_exhausted" if pending else "no_pending_information")}


__all__ = ["BaselineDispatchAdapter", "POLICIES", "dispatch_policy_decision"]
