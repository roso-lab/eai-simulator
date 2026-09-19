"""Compose logical team changes, message delivery and the public participant API.

This harness applies protocol actions. It does not spawn a robot, move a body,
acknowledge an order, or mark a task completed. The Isaac owner must separately
confirm those effects before producing execution or business evidence.
"""

from copy import copy, deepcopy
import math

from .communication import CommunicationRuntime
from .team_lifecycle import TeamLifecycle


class InterventionSession:
    def __init__(self, participant_session, *, communication_contract=None, recipient_id="participant"):
        self.participant = participant_session
        public = participant_session.task_spec
        initial = []
        for robot in public["initial_fleet"]:
            capabilities = robot.get("capability_declaration")
            if not capabilities:
                raise ValueError("intervention harness requires explicit public capabilities")
            initial.append({"id": robot["id"], "capabilities": deepcopy(capabilities)})
        self.team = TeamLifecycle(initial, public.get("participant_requirements", []))
        self.communication = CommunicationRuntime(communication_contract)
        self.recipient_id = recipient_id
        self._delivered = set()
        self._time_s = 0.
        self._wait_until = {}
        self._ready_nodes = set()

    def _time(self, time_s):
        if (isinstance(time_s, bool) or not isinstance(time_s, (float, int))
                or not math.isfinite(time_s) or time_s < self._time_s):
            raise ValueError("intervention time must be finite and monotonic")
        return float(time_s)

    def register_reserve(self, member):
        """Register evaluator-owned availability without revealing a future join."""
        return self.team.register_reserve(member, time_s=self._time_s)

    def lifecycle_event(self, event):
        time_s = self._time(event["time_s"])
        candidate = deepcopy(self.team)
        visible = candidate.public_event(event)
        # Stage both public delivery and fleet updates. A rejected event must
        # not advance either clock, drain a queue, or expose a partial join.
        staged = copy(self)
        staged.communication = deepcopy(self.communication)
        staged._delivered = set(self._delivered)
        staged.participant = copy(self.participant)
        staged.participant.__dict__ = {
            key: value if key == "adapter" else deepcopy(value)
            for key, value in self.participant.__dict__.items()}
        staged.advance(time_s)
        staged.participant.emit(visible["type"], time_s=time_s, payload=visible["payload"])
        self.participant.__dict__.update(staged.participant.__dict__)
        self.communication = staged.communication
        self._delivered = staged._delivered
        self._time_s = time_s
        self.team = candidate
        return visible

    def communication_change(self, conditions, *, time_s, channel=None):
        """Evaluator intervention; the participant sees only actual messages."""
        self.advance(time_s)
        return self.communication.set_conditions(conditions, recipient=self.recipient_id,
                                                  channel=channel, time_s=self._time_s)

    def publish(self, *, message_id, producer_id, estimate, valid_until_s, channel="observation"):
        if self.team.snapshot()["members"].get(producer_id, {}).get("status") != "active":
            raise ValueError("only an active member can publish an observation")
        self.communication.publish(message_id=message_id, producer_id=producer_id,
            recipients=[self.recipient_id], channel=channel, estimate=estimate,
            valid_until_s=valid_until_s, published_at_s=self._time_s)

    def advance(self, time_s):
        time_s = self._time(time_s)
        self.communication.advance(time_s)
        self._time_s = time_s
        for payload in self.communication.public_messages(recipient=self.recipient_id):
            if payload["message_id"] not in self._delivered:
                self.participant.emit("observation_message", time_s=time_s, payload=payload)
                self._delivered.add(payload["message_id"])

    def ready(self, node_id, *, time_s):
        eligible = self.team.qualified_members(node_id)
        self.advance(time_s)
        self.participant.emit("phase_ready", time_s=self._time_s, payload={
            "node_id": node_id, "eligible_agent_ids": eligible})
        self._ready_nodes.add(node_id)

    def tick(self, time_s):
        self.advance(time_s)
        self.participant.emit("tick", time_s=self._time_s, payload={
            "robot_states": {identifier: {"status": state["status"], "capability_version": state["capability_version"]}
                             for identifier, state in self.team.snapshot()["members"].items()},
            "phase_states": {}, "resource_states": {}})

    def request_actions(self):
        actions = self.participant.request_actions()
        results = []
        for action in actions:
            payload = action["payload"]
            status, reason = "accepted", "protocol_effect_only_no_physical_execution"
            try:
                if action["type"] in {"allocate", "reassign"}:
                    if payload["node_id"] not in self._ready_nodes:
                        raise ValueError("allocation needs a runtime-ready objective")
                    self.team.assign(payload["node_id"], payload["node_id"], payload["agent_ids"], time_s=self._time_s)
                elif action["type"] == "consume_message":
                    self.communication.consume(payload["message_id"], recipient=self.recipient_id,
                        consumer_id=payload["consumer_id"], time_s=self._time_s)
                elif action["type"] == "wait":
                    assignment = self.team.snapshot()["objectives"].get(payload["node_id"], {})
                    if payload["agent_id"] not in assignment.get("agent_ids", []):
                        raise ValueError("waiting needs a current assignment")
                    self._wait_until[payload["node_id"]] = self._time_s + payload["duration_s"]
                elif action["type"] == "navigate_goal":
                    assignment = self.team.snapshot()["objectives"].get(payload["node_id"], {})
                    if payload["agent_id"] not in assignment.get("agent_ids", []):
                        raise ValueError("navigation needs a current assignment")
                    if self._time_s < self._wait_until.get(payload["node_id"], 0.):
                        raise ValueError("objective is waiting until the requested deadline")
                else:
                    raise ValueError("unsupported protocol action")
            except ValueError as exc:
                status, reason = "rejected", str(exc)
            result = {"action_id": action["action_id"], "status": status, "reason": reason}
            self.participant.emit("action_result", time_s=self._time_s, payload=result)
            results.append(result)
        return results

    def report(self):
        return {"schema": "eai.benchmark.intervention_session", "version": 1,
                "harness": "protocol_components_no_motion", "execution_validated": False,
                "participant_protocol": self.participant.report(),
                "communication": self.communication.report(),
                "team_lifecycle": self.team.report(),
                "wait_until_s": deepcopy(self._wait_until)}


__all__ = ["InterventionSession"]
