"""A reproducible public-input policy, not a witness or an evaluated LLM.

This deliberately simple baseline picks two distinct nearby compatible ground
members and fixed time windows. Its always-deploy declaration is a participant
decision, never an evaluator label. It has no recovery/replanning strategy.
Nothing in this module reads hidden schedules, runtime roots or oracle data.
"""

from copy import deepcopy
import itertools
import math

from .provenance import canonical_sha256


class PublicRestaurantParticipant:
    """Nearest distinct pair with delivered-result prerequisite tracking."""

    def __init__(self):
        self.events = []
        self.actions = []
        self.sent = set()
        self.finished = {}
        self.consumed = set()
        self.plan_sent = False
        self.closed = False

    def manifest(self):
        return {"kind": "participant", "id": "public-nearest-two-diagnostic", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven", "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize_deployment(self, deployment):
        self.deployment = deepcopy(deployment)
        return {"schema": "eai.restaurant.deployment_ack", "version": 2,
                "profile": deployment["profile"], "deployment_sha256": canonical_sha256(deployment),
                "public_task_sha256": deployment["public_task_sha256"]}

    def initialize(self, public_task, participant_api):
        self.public = deepcopy(public_task)
        self.api = deepcopy(participant_api)
        regions = [r["id"] for r in self.public["public_world"]["regions"]
                   if r["id"] in {"kitchen_service", "dining_service"}]
        if len(regions) != 2:
            raise ValueError("nearest-pair policy needs both public service regions")
        candidates = [r for r in self.public["candidate_robot_pool"]
                      if {"ground_navigation", "observe", "relay"}
                      <= set(r["public_capabilities"]) and r["mobility"] == "ground"]
        if len(candidates) < 2:
            raise ValueError("nearest-pair policy needs two compatible members; this is not an oracle")
        geometry = self.deployment["region_bindings"]

        def distance(robot, region):
            spawn = self.deployment["spawn_regions"][robot["id"]]
            return math.dist(geometry[spawn]["center_xy_m"], geometry[region]["center_xy_m"])

        chosen = min(itertools.permutations(candidates, 2),
                     key=lambda pair: (sum(distance(r, region) for r, region in zip(pair, regions)),
                                       tuple(r["id"] for r in pair)))
        self.team = [r["id"] for r in chosen]
        tasks = []
        for index, (robot, region) in enumerate(zip(chosen, regions)):
            producer = robot["id"]
            origin = self.deployment["spawn_regions"][producer]
            route = next(r for r in self.public["public_world"]["routes"]
                         if r["from_region_id"] == origin and r["to_region_id"] == region
                         and "ground_navigation" in r["access_modes"])
            tasks += [self.task(f"nav-{index}", "ground_navigation", producer,
                                {"region_id": region, "route_ids": [route["id"]]}, [], 0., 60.),
                      self.task(f"observe-{index}", "observe", producer,
                                {"region_id": region, "subject_ref": region,
                                 "observation_mode": "service_ground_readiness"},
                                [f"nav-{index}"], 61., 10.)]
        tasks.append(self.task("relay", "relay", self.team[0],
                               {"message_kind": "restaurant_readiness_receipt", "region_id": regions[0]},
                               ["observe-0", "observe-1"], 72., 10.))
        self.plan = {"schema": "eai.benchmark.goal_plan", "version": 3,
                     "plan_id": "nearest-public-pair", "assigned_team": self.team, "tasks": tasks}

    def initialize_execution(self, profile):
        self.execution_profile = deepcopy(profile)
        return {"schema": "eai.restaurant.execution_profile_ack", "version": 1,
                "profile_sha256": canonical_sha256(profile)}

    @staticmethod
    def task(ident, skill, producer, arguments, prerequisites, start, duration):
        return {"task_id": ident, "skill": skill, "arguments": arguments,
                "prerequisites": prerequisites, "assigned_agents": [producer],
                "required_capabilities": [skill], "evidence_refs": [],
                "execution": {"start_s": start, "duration_s": duration}}

    def action(self, ident, kind, payload, now):
        action = {"schema": "eai.benchmark.goal_action", "version": 1, "action_id": ident,
                  "submitted_at_s": now, "type": kind, "payload": payload}
        self.actions.append(deepcopy(action))
        return action

    def act(self, events):
        self.events.extend(deepcopy(events))
        now = max((e["time_s"] for e in events), default=0.)
        actions = []
        for event in events:
            payload = event["payload"]
            if event["type"] == "deployment_assessment_required":
                actions.append(self.action(f"assessment-{payload['assessment_epoch']}",
                    "deployment_assessment", {**{k: payload[k] for k in (
                        "assessment_id", "assessment_epoch", "roster_version", "capability_versions")},
                        "decision": "deployable", "reason_codes": [], "evidence_refs": []}, now))
                if not self.plan_sent:
                    self.plan_sent = True
                    actions += [self.action("select", "select_team", {"agent_ids": self.team}, now),
                                self.action("plan", "submit_plan", {"plan": self.plan}, now)]
            if event["type"] == "action_result" and payload["action_id"].startswith("dispatch-"):
                if payload["status"] in {"succeeded", "failed", "cancelled"}:
                    self.finished[payload["action_id"][9:]] = payload["status"]
            if event["type"] == "observation" and payload["message_id"] not in self.consumed:
                self.consumed.add(payload["message_id"])
                actions.append(self.action(f"consume-{len(self.consumed)}", "consume_message",
                                           {"message_id": payload["message_id"]}, now))
        for task in self.plan["tasks"]:
            if (self.plan_sent and task["task_id"] not in self.sent
                    and now >= task["execution"]["start_s"]
                    and now < task["execution"]["start_s"] + task["execution"]["duration_s"]
                    and all(self.finished.get(p) == "succeeded" for p in task["prerequisites"])):
                self.sent.add(task["task_id"])
                actions.append(self.action("dispatch-" + task["task_id"], "dispatch_skill",
                                           {"task_id": task["task_id"], "evidence_refs": []}, now))
        return actions

    def close(self):
        self.closed = True
