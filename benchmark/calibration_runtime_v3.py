"""Metered command/feedback protocol for physical calibration preflight.

The coordinator only learns terminal status through delivered robot feedback.
Local controller callbacks stay on the robot node. This is a measurement
producer, not an oracle, skill certificate or a D6 reference-table publisher.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math

from .transport_v3 import SharedChannelV3


MEASUREMENT_PLAN = {
    "schema": "heterocoord.resource_calibration_preflight.v1",
    "coordinator_id": "fixed-route-command-feedback-v1",
    "attempts": [{"attempt_id": "preflight-01", "seed": 17}],
    "scope": "measurement_preflight_not_formal_calibration",
    "feedback_period_ticks": 10,
    "channel": {"bits_per_tick": 8192, "max_queue_bits": 65536,
                "delay_ticks": 2, "ttl_ticks": 20, "loss_probability": 0., "seed": 17},
    "wire_format": "utf8_sorted_compact_json_sender_receiver_kind_payload_no_compression",
    "topology": "one_shared_medium_coordinator_robot_bidirectional",
    "operation_order": "deliver_then_local_control_then_physics_then_feedback_submission",
    "end_rule": "delivered_stop_confirmed_terminal_feedback_or_registered_physical_horizon",
    "rights": {"coordinator": ["public_contract", "delivered_robot_feedback"],
               "robot": ["delivered_route_command", "local_pose_velocity", "frozen_topology"]},
}


class MeteredNavigationProtocol:
    def __init__(self, contract, *, plan=None):
        self.plan = deepcopy(MEASUREMENT_PLAN if plan is None else plan)
        self.contract = deepcopy(contract)
        self.channel = SharedChannelV3(**self.plan["channel"])
        self.events = []
        self.authorized = False
        self.terminal_feedback = None
        self._terminal_sent = False
        self._tick = -1
        self.channel.submit(message_id="route/0", sender_id="coordinator",
            receiver_id=contract["robot_id"], kind="route_command",
            payload={"task_id": contract["task_id"], "target_ref": contract["target_ref"]}, tick=0)

    def advance(self, tick):
        if type(tick) is not int or tick != self._tick + 1:
            raise ValueError("measurement must advance once per physical step")
        self._tick = tick
        self.channel.advance(tick)
        robot = self.contract["robot_id"]
        for frame in self.channel.receive(receiver_id=robot, tick=tick):
            if frame.kind != "route_command" or frame.payload != {
                    "task_id": self.contract["task_id"], "target_ref": self.contract["target_ref"]}:
                raise ValueError("unexpected delivered route command")
            self.authorized = True
            self.events.append({"tick": tick, "kind": "robot_command_consumed", "message_id": frame.message_id})
        for frame in self.channel.receive(receiver_id="coordinator", tick=tick):
            self.events.append({"tick": tick, "kind": "coordinator_feedback_consumed",
                                "message_id": frame.message_id, "payload": deepcopy(frame.payload)})
            if frame.kind == "terminal_feedback":
                self.terminal_feedback = deepcopy(frame.payload)
        return self.authorized

    def observe(self, tick, sample, state):
        if tick != self._tick + 1:
            raise ValueError("feedback must follow exactly one physical step")
        if state is None or self._terminal_sent:
            return
        terminal = (state["status"] != "pending" and
                    (state["status"] != "completed" or state.get("stop_confirmed") is True))
        if not terminal and tick % self.plan["feedback_period_ticks"]:
            return
        payload = {"task_id": self.contract["task_id"], "status": state["status"],
                   "stop_confirmed": state.get("stop_confirmed", False),
                   "position_m": list(sample["position_m"]), "speed_m_s": sample["speed_m_s"],
                   "physical_time_s": sample["simulation_time_s"]}
        self.channel.submit(message_id=f"feedback/{tick}", sender_id=self.contract["robot_id"],
            receiver_id="coordinator", kind="terminal_feedback" if terminal else "progress_feedback",
            payload=payload, tick=tick)
        self._terminal_sent = terminal

    def result(self, *, start_time_s, end_time_s, physics_step_s):
        if not all(math.isfinite(x) for x in (start_time_s, end_time_s, physics_step_s)) or physics_step_s <= 0 or end_time_s < start_time_s:
            raise ValueError("invalid measured physical clock")
        snapshot = self.channel.snapshot()
        frames = list(snapshot["frames"].values())
        # Recompute lengths from raw payloads at the declared meter point.
        for frame in frames:
            expected = SharedChannelV3._bits(frame["sender_id"], frame["receiver_id"], frame["kind"], frame["payload"])
            if frame["bits"] != expected:
                raise ValueError("serialized frame differs from metered length")
        submitted = sum(frame["bits"] for frame in frames)
        transmitted = sum(frame["bits"] for frame in frames if frame["started_tick"] is not None)
        # This preflight is nominal and requires every frame fully transmitted.
        complete = (all(frame["status"] == "transmitted" and frame["remaining_bits"] == 0 for frame in frames)
                    and snapshot["counters"]["queued_bits"] == 0
                    and not snapshot["in_flight"] and not snapshot["delivered"])
        accounting_consistent = submitted == snapshot["counters"]["submitted_bits"] and (
            not complete or transmitted == snapshot["counters"]["transmitted_bits"])
        return {"plan": deepcopy(self.plan), "frames": frames, "events": deepcopy(self.events),
                "accounting": snapshot["counters"], "accounting_consistent": accounting_consistent,
                "traffic_complete": complete, "terminal_feedback": deepcopy(self.terminal_feedback),
                "physical_start_s": start_time_s, "physical_end_s": end_time_s,
                "physical_elapsed_s": end_time_s - start_time_s,
                "nominal_capacity_bps": self.plan["channel"]["bits_per_tick"] / physics_step_s,
                "paper_score_eligible": False, "reference_scales": None,
                "qualification_missing": ["certified_deployable_instance", "physical_skill_validation",
                                          "registered_full_calibration_batch"]}


__all__ = ["MEASUREMENT_PLAN", "MeteredNavigationProtocol"]
