from copy import deepcopy
from types import SimpleNamespace

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
from demo.benchmark.goal_restaurant_autonomous_v2 import RestaurantAutonomousContext
from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
from demo.benchmark.goal_restaurant_execution_v2 import RestaurantExecutionPump
from demo.benchmark.goal_restaurant_fleet import FLEET_CONTROLLER_REGISTRY
from demo.benchmark.goal_session_audit import AuditedGoalSession
from demo.benchmark.provenance import canonical_sha256


def _root(position):
    return [*map(float, position), 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class FakeFleet:
    control_dt_s = 0.1

    def __init__(self, contract, *, arrival_ticks=1):
        self.contract = contract
        self.arrival_ticks = arrival_ticks
        self._time = 0.0
        self._step = 100
        self._operations = {}
        self.step_calls = 0
        self.max_active = 0
        self.all_eight_commands = True
        self.positions = {}
        for public_id, region in contract["deployment"]["spawn_regions"].items():
            binding = contract["deployment"]["region_bindings"][region]
            center = binding.get("center_xyz_m", binding.get("center_xy_m"))
            z = center[2] if len(center) == 3 else 0.0
            self.positions[public_id] = [center[0], center[1], z]
        sim = SimpleNamespace(get_physics_dt=lambda: 0.1)
        self.session = SimpleNamespace(base_env=SimpleNamespace(sim=sim))

    @property
    def time_s(self):
        return self._time

    def episode_metadata(self):
        return {"origin_physics_step": 100, "episode_id": "pump-test"}

    def _snapshot(self):
        agents = {}
        for public_id, binding in FLEET_CONTROLLER_REGISTRY.items():
            root = _root(self.positions[public_id])
            agents[public_id] = {
                "public_agent_id": public_id,
                "native_agent_id": binding["native_agent_id"],
                "physics_step": self._step,
                "time_s": self._time,
                "root_state_w": root,
                "position_m": root[:3],
                "yaw_rad": 0.0,
                "tilt_rad": 0.0,
            }
        return {"schema": "eai.restaurant.native_fleet_snapshot", "version": 1,
                "physics_step": self._step, "time_s": self._time,
                "agents": agents, "operations": deepcopy(self._operations),
                "execution_validated": False, "business_outcomes_validated": False,
                "feasibility_oracle_verified": False, "release_ready": False,
                "scoreable": False}

    def snapshot(self):
        return self._snapshot()

    def plan_route(self, public_agent_id, route_id, region_id):
        binding = self.contract["deployment"]["region_bindings"][region_id]
        center = binding.get("center_xyz_m", binding.get("center_xy_m"))
        z = center[2] if len(center) == 3 else 0.0
        return {"public_agent_id": public_agent_id, "route_id": route_id,
                "region_id": region_id, "goal_xyz_m": [center[0], center[1], z]}

    def begin_navigation(self, operation_id, route, *, deadline_s):
        operation = {
            "operation_id": operation_id,
            "route_id": route["route_id"],
            "public_agent_id": route["public_agent_id"],
            "native_agent_id": FLEET_CONTROLLER_REGISTRY[
                route["public_agent_id"]]["native_agent_id"],
            "status": "active", "started_at_s": self._time,
            "deadline_s": deadline_s, "goal_binding": {
                "region_id": route["region_id"]},
            "goal_xyz_m": route["goal_xyz_m"], "ticks": 0,
            "terminal_reason": None,
        }
        self._operations[operation_id] = operation
        return deepcopy(operation)

    def cancel_navigation(self, operation_id):
        operation = self._operations[operation_id]
        if operation["status"] == "active":
            operation.update(status="cancelled", terminal_reason="cancel_requested",
                             completed_at_s=self._time,
                             completed_physics_step=self._step,
                             last_root_state_w=_root(
                                 self.positions[operation["public_agent_id"]]))
        return deepcopy(operation)

    def step_once(self):
        self.step_calls += 1
        active = [row for row in self._operations.values()
                  if row["status"] == "active"]
        self.max_active = max(self.max_active, len(active))
        self._time = round(self._time + self.control_dt_s, 10)
        self._step += 1
        for operation in active:
            operation["ticks"] += 1
            if self._time >= operation["deadline_s"]:
                operation.update(
                    status="failed", terminal_reason="navigation_deadline",
                    completed_at_s=self._time,
                    completed_physics_step=self._step,
                    last_root_state_w=_root(
                        self.positions[operation["public_agent_id"]]))
            elif operation["ticks"] >= self.arrival_ticks:
                self.positions[operation["public_agent_id"]] = list(
                    operation["goal_xyz_m"])
                operation.update(
                    status="arrived", terminal_reason="goal_reached",
                    completed_at_s=self._time,
                    completed_physics_step=self._step,
                    last_root_state_w=_root(operation["goal_xyz_m"]))
        submitted = {public_id: [0.0, 0.0, 0.0]
                     for public_id in FLEET_CONTROLLER_REGISTRY}
        self.all_eight_commands = self.all_eight_commands and (
            set(submitted) == set(FLEET_CONTROLLER_REGISTRY))
        result = self._snapshot()
        result.update({"schema": "eai.restaurant.native_fleet_tick",
                       "start_physics_step": self._step - 1,
                       "submitted_commands": submitted})
        return result


class FakeObservationProvider:
    def __init__(self, fleet):
        self.fleet = fleet

    def ground_readiness(self, producer_id, region_id):
        sample = self.fleet.snapshot()["agents"][producer_id]
        return {"producer_id": producer_id, "region_id": region_id,
                "scene_observation_validated": True,
                "stamp": {"simulation_step": sample["physics_step"],
                          "time_s": sample["time_s"],
                          "root_state_w": sample["root_state_w"]}}

    @staticmethod
    def bind_transport_stamp(scene):
        bound = deepcopy(scene)
        producer = bound["producer_id"]
        bound["stamp"].update(
            producer_id=producer,
            native_agent_id=FLEET_CONTROLLER_REGISTRY[producer]["native_agent_id"])
        return bound


def _task(task_id, skill, agent, arguments, prerequisites=(), *, start=0.0, duration=10.0):
    return {"task_id": task_id, "skill": skill, "arguments": arguments,
            "prerequisites": list(prerequisites), "assigned_agents": [agent],
            "required_capabilities": [skill], "evidence_refs": [],
            "execution": {"start_s": start, "duration_s": duration}}


def _route(public, source, target):
    return next(row["id"] for row in public["public_world"]["routes"]
                if row["from_region_id"] == source
                and row["to_region_id"] == target
                and "ground_navigation" in row["access_modes"])


def _parallel_plan(contract):
    public = contract["public_task"]
    a, b = "restaurant_carter_1", "restaurant_carter_2"
    sa, sb = (contract["deployment"]["spawn_regions"][a],
              contract["deployment"]["spawn_regions"][b])
    tasks = [
        _task("nav-k", "ground_navigation", a, {
            "region_id": "kitchen_service",
            "route_ids": [_route(public, sa, "kitchen_service")]}, duration=8.0),
        _task("obs-k", "observe", a, {
            "region_id": "kitchen_service", "subject_ref": "kitchen_service",
            "observation_mode": "service_ground_readiness"}, ["nav-k"],
              start=8.0, duration=4.0),
        _task("nav-d", "ground_navigation", b, {
            "region_id": "dining_service",
            "route_ids": [_route(public, sb, "dining_service")]}, duration=8.0),
        _task("obs-d", "observe", b, {
            "region_id": "dining_service", "subject_ref": "dining_service",
            "observation_mode": "service_ground_readiness"}, ["nav-d"],
              start=8.0, duration=4.0),
        _task("relay", "relay", a, {
            "message_kind": "restaurant_readiness_receipt",
            "region_id": "kitchen_service"}, ["obs-k", "obs-d"],
              start=12.0, duration=1.0),
    ]
    return {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "parallel-native", "assigned_team": [a, b], "tasks": tasks}


def _serial_plan(contract):
    public = contract["public_task"]
    agent = "restaurant_carter_1"
    spawn = contract["deployment"]["spawn_regions"][agent]
    tasks = [
        _task("nav-k", "ground_navigation", agent, {
            "region_id": "kitchen_service",
            "route_ids": [_route(public, spawn, "kitchen_service")]},
              duration=8.0),
        _task("obs-k", "observe", agent, {
            "region_id": "kitchen_service", "subject_ref": "kitchen_service",
            "observation_mode": "service_ground_readiness"}, ["nav-k"],
              start=8.0, duration=4.0),
        _task("nav-d", "ground_navigation", agent, {
            "region_id": "dining_service",
            "route_ids": [_route(public, "kitchen_service", "dining_service")]},
              ["obs-k"], start=12.0, duration=8.0),
        _task("obs-d", "observe", agent, {
            "region_id": "dining_service", "subject_ref": "dining_service",
            "observation_mode": "service_ground_readiness"}, ["nav-d"],
              start=20.0, duration=4.0),
        _task("relay", "relay", agent, {
            "message_kind": "restaurant_readiness_receipt",
            "region_id": "dining_service"}, ["obs-k", "obs-d"],
              start=24.0, duration=1.0),
    ]
    return {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "serial-native", "assigned_team": [agent], "tasks": tasks}


class Participant:
    def __init__(self, plan, *, cancel_immediately=False, cancel_at_s=None,
                 deployment_decision="deployable", explicit_relay_refs=False,
                 dispatch_delay_s=0.0):
        self.plan = deepcopy(plan)
        self.cancel_immediately = cancel_immediately
        self.cancel_at_s = cancel_at_s
        self.deployment_decision = deployment_decision
        self.explicit_relay_refs = explicit_relay_refs
        self.dispatch_delay_s = dispatch_delay_s
        self.cancelled = set()
        self.sent = set()
        self.finished = {}
        self.consumed = set()
        self.execution_profile = None
        self.plan_sent = False

    def manifest(self):
        return {"kind": "participant", "id": "pump-focused", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter",
                "interface_version": 1, "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task, participant_api):
        self.public_task = deepcopy(public_task)
        self.participant_api = deepcopy(participant_api)

    def initialize_execution(self, profile):
        self.execution_profile = deepcopy(profile)
        return {"schema": "eai.restaurant.execution_profile_ack", "version": 1,
                "profile_sha256": canonical_sha256(profile)}

    def close(self):
        pass

    @staticmethod
    def _action(ident, kind, payload, now):
        return {"schema": "eai.benchmark.goal_action", "version": 1,
                "action_id": ident, "submitted_at_s": now,
                "type": kind, "payload": payload}

    def act(self, events):
        now = max((row["time_s"] for row in events), default=0.0)
        actions = []
        for event in events:
            payload = event["payload"]
            if event["type"] == "deployment_assessment_required":
                actions.append(
                    self._action(f"assess-{payload['assessment_epoch']}",
                                 "deployment_assessment", {
                        **{key: payload[key] for key in (
                            "assessment_id", "assessment_epoch", "roster_version",
                            "capability_versions")},
                        "decision": self.deployment_decision,
                        "reason_codes": (["missing_capability"]
                                         if self.deployment_decision == "not_deployable"
                                         else []),
                        "evidence_refs": []}, now))
                if not self.plan_sent:
                    self.plan_sent = True
                    actions.extend([
                        self._action("team", "select_team", {
                        "agent_ids": self.plan["assigned_team"]}, now),
                        self._action("plan", "submit_plan", {"plan": self.plan}, now),
                    ])
            elif (event["type"] == "action_result"
                  and payload["action_id"].startswith("dispatch-")
                  and payload["status"] in {"succeeded", "failed", "cancelled"}):
                self.finished[payload["action_id"][9:]] = payload["status"]
            elif event["type"] == "observation":
                message_id = payload["message_id"]
                if message_id not in self.consumed:
                    self.consumed.add(message_id)
                    actions.append(self._action(
                        f"consume-{len(self.consumed)}", "consume_message",
                        {"message_id": message_id}, now))
        for task in self.plan["tasks"]:
            task_id = task["task_id"]
            if (task_id not in self.sent
                    and now >= task["execution"]["start_s"] + self.dispatch_delay_s
                    and now < (task["execution"]["start_s"]
                               + task["execution"]["duration_s"])
                    and all(self.finished.get(dep) == "succeeded"
                            for dep in task["prerequisites"])):
                self.sent.add(task_id)
                refs = (sorted(self.consumed) if self.explicit_relay_refs
                        and task["skill"] == "relay" else [])
                actions.append(self._action(
                    "dispatch-" + task_id, "dispatch_skill",
                    {"task_id": task_id, "evidence_refs": refs}, now))
                if self.cancel_immediately and task_id.startswith("nav"):
                    actions.append(self._action(
                        "cancel-" + task_id, "cancel_skill",
                        {"task_id": task_id, "reason": "focused cancellation"}, now))
            if (self.cancel_at_s is not None and now >= self.cancel_at_s
                    and task_id in self.sent and task_id not in self.finished
                    and task_id not in self.cancelled and task["skill"].endswith("navigation")):
                self.cancelled.add(task_id)
                actions.append(self._action(
                    "cancel-" + task_id, "cancel_skill",
                    {"task_id": task_id, "reason": "cancel during navigation"}, now))
        return actions


def _runtime(*, downlink_delay=0.1, uplink_delay=0.1, drop_result=False):
    return CommunicationRuntime({
        "pools": {
            "backhaul_downlink": {"capacity_bps": None,
                                  "fixed_delay_s": downlink_delay},
            "backhaul_uplink": {"capacity_bps": None,
                                "fixed_delay_s": uplink_delay,
                                "drop": drop_result},
            "p2p": {"capacity_bps": None, "fixed_delay_s": 0.0},
        },
    })


def _context(tmp_path, adapter, *, arrival_ticks=1):
    contract = build_restaurant_deployment_v2()
    fleet = FakeFleet(contract, arrival_ticks=arrival_ticks)
    episode = "pump-test"
    session = AuditedGoalSession(
        contract["public_task"], contract["participant_api"], episode_id=episode)
    regions = contract["deployment"]["spawn_regions"]
    session.initialize_deployment_runtime_state(regions, {})
    gate = session.report()["deployment_assessment"]
    session.record_deployment_assessment_evaluation({
        "assessment_id": gate["assessment_id"],
        "assessment_epoch": gate["assessment_epoch"],
        "feasibility_label": None, "oracle_status": "failed",
        "platform_valid": False, "evidence_refs": [],
        "evaluator_ref": "focused-test-no-oracle",
    })
    host = GoalAlgorithmHost(
        contract["public_task"], contract["participant_api"], adapter,
        session=session)
    binding = {"episode_id": episode}
    initial_state = {"robot_regions": deepcopy(regions)}
    context = RestaurantAutonomousContext(
        host, session, fleet, contract, initial_state, binding,
        _execution_initializer=adapter.initialize_execution)
    return context, fleet, contract


def test_parallel_native_pump_delays_both_commands_and_delivers_metered_observations(tmp_path):
    contract = build_restaurant_deployment_v2()
    participant = Participant(_parallel_plan(contract))
    context, fleet, _ = _context(tmp_path, participant)
    runtime = _runtime(downlink_delay=0.2, uplink_delay=0.2)
    pump = RestaurantExecutionPump(
        context, runtime=runtime,
        observation_provider=FakeObservationProvider(fleet))
    archive = pump.run(max_ticks=150)
    tasks = archive["context"]["session_audit"]["final_report"]["tasks"]
    assert {row["status"] for row in tasks.values()} == {"succeeded"}
    assert fleet.max_active == 2
    assert fleet.step_calls == len(archive["ticks"])
    assert fleet.all_eight_commands is True
    assert archive["tasks"]["nav-k"]["ready_at_s"] == pytest.approx(0.2)
    assert archive["tasks"]["nav-k"]["physical_started_at_s"] == pytest.approx(0.2)
    assert tasks["nav-k"]["result_delivered_at_s"] > tasks["nav-k"]["completed_at_s"]
    observations = archive["visible_readiness_observations"]
    assert {row["region_id"] for row in observations} == {
        "kitchen_service", "dining_service"}
    assert all(row["participant_message_id"] in participant.consumed
               for row in observations)
    selection = archive["tasks"]["relay"]["relay_selection"]
    assert selection["error"] is None
    assert selection["accepted_at_s"] >= max(
        row["result_delivered_at_s"] for row in observations)
    assert archive["session_audit_replay"]["validated"] is True
    assert archive["execution_validated"] is False
    assert archive["business_outcomes_validated"] is False
    assert participant.execution_profile["readiness_relay"]["selection_time"] == (
        "dispatch_skill_acceptance")
    explicit = [row["participant_message_id"] for row in observations]
    rejected = pump._freeze_relay_selection(
        archive["tasks"]["relay"]["task"], [*explicit, "foreign-message"],
        archive["tasks"]["relay"]["dispatch_accepted_at_s"])
    assert rejected["error"] == (
        "explicit_refs_include_unknown_extra_or_duplicate_observations")


def test_cancel_before_dispatch_delivery_never_starts_physical_navigation(tmp_path):
    contract = build_restaurant_deployment_v2()
    plan = _parallel_plan(contract)
    plan["tasks"] = [plan["tasks"][0]]
    plan["assigned_team"] = [plan["tasks"][0]["assigned_agents"][0]]
    participant = Participant(plan, cancel_immediately=True)
    context, fleet, _ = _context(tmp_path, participant, arrival_ticks=10)
    pump = RestaurantExecutionPump(
        context, runtime=_runtime(downlink_delay=0.3, uplink_delay=0.1),
        observation_provider=FakeObservationProvider(fleet))
    archive = pump.run(max_ticks=15)
    task = archive["context"]["session_audit"]["final_report"]["tasks"]["nav-k"]
    assert task["status"] == "cancelled"
    assert archive["tasks"]["nav-k"]["physical_started_at_s"] is None
    assert archive["downlink"]["dispatches"]["nav-k"]["dispatch"]["status"] == "withdrawn"
    assert archive["session_audit_replay"]["validated"] is True


def test_scheduled_start_and_heartbeat_support_deliberately_late_dispatch(tmp_path):
    contract = build_restaurant_deployment_v2()
    public = contract["public_task"]
    agent = "restaurant_carter_1"
    spawn = contract["deployment"]["spawn_regions"][agent]
    task = _task("late-nav", "ground_navigation", agent, {
        "region_id": "kitchen_service",
        "route_ids": [_route(public, spawn, "kitchen_service")]},
        start=1.0, duration=10.0)
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "late-dispatch", "assigned_team": [agent], "tasks": [task]}
    participant = Participant(plan, dispatch_delay_s=0.6)
    context, fleet, _ = _context(tmp_path, participant)
    pump = RestaurantExecutionPump(
        context, runtime=_runtime(downlink_delay=0.2),
        participant_tick_interval_s=0.2,
        observation_provider=FakeObservationProvider(fleet))
    archive = pump.run(max_ticks=30)
    execution = archive["tasks"]["late-nav"]
    assert execution["dispatch_accepted_at_s"] >= 1.6
    assert execution["ready_at_s"] >= execution["dispatch_accepted_at_s"] + 0.2
    assert execution["physical_started_at_s"] >= execution["ready_at_s"]
    assert execution["physical_started_at_s"] >= task["execution"]["start_s"]
    assert sum(row["public_tick_emitted"] for row in archive["ticks"]) >= 4
    assert archive["profile"]["participant_clock"]["heartbeat_interval_s"] == 0.2


def test_serial_participant_plan_and_explicit_delivered_observation_refs(tmp_path):
    contract = build_restaurant_deployment_v2()
    participant = Participant(_serial_plan(contract), explicit_relay_refs=True)
    context, fleet, _ = _context(tmp_path, participant)
    pump = RestaurantExecutionPump(
        context, runtime=_runtime(), participant_tick_interval_s=0.2,
        observation_provider=FakeObservationProvider(fleet))
    archive = pump.run(max_ticks=270)
    session = archive["context"]["session_audit"]["final_report"]
    assert {row["status"] for row in session["tasks"].values()} == {"succeeded"}
    selection = archive["tasks"]["relay"]["relay_selection"]
    assert selection["error"] is None
    assert set(selection["dispatch_evidence_refs"]) == {
        row["participant_message_id"]
        for row in archive["visible_readiness_observations"]}
    assert fleet.max_active == 1
    assert fleet.all_eight_commands is True


def test_cancel_during_active_navigation_publishes_cancelled_terminal(tmp_path):
    contract = build_restaurant_deployment_v2()
    plan = _parallel_plan(contract)
    plan["tasks"] = [plan["tasks"][0]]
    plan["assigned_team"] = [plan["tasks"][0]["assigned_agents"][0]]
    participant = Participant(plan, cancel_at_s=0.4)
    context, fleet, _ = _context(tmp_path, participant, arrival_ticks=50)
    pump = RestaurantExecutionPump(
        context, runtime=_runtime(), participant_tick_interval_s=0.2,
        observation_provider=FakeObservationProvider(fleet))
    archive = pump.run(max_ticks=20)
    execution = archive["tasks"]["nav-k"]
    session_task = archive["context"]["session_audit"]["final_report"][
        "tasks"]["nav-k"]
    assert execution["physical_started_at_s"] == pytest.approx(0.1)
    assert execution["physical_status"] == "cancelled"
    assert session_task["status"] == "cancelled"
    assert execution["physical_terminal_at_s"] > execution["physical_started_at_s"]


def test_member_exit_invalidates_running_task_and_terminal_help_starts_no_work(tmp_path):
    contract = build_restaurant_deployment_v2()
    plan = _parallel_plan(contract)
    plan["tasks"] = [plan["tasks"][0]]
    plan["assigned_team"] = [plan["tasks"][0]["assigned_agents"][0]]
    participant = Participant(plan)
    context, fleet, _ = _context(tmp_path, participant, arrival_ticks=50)
    pump = RestaurantExecutionPump(
        context, runtime=_runtime(),
        observation_provider=FakeObservationProvider(fleet))
    pump.step_once()
    participant.deployment_decision = "not_deployable"
    context.host.emit("member_exit", fleet.time_s, {
        "agent_id": "restaurant_carter_1"})
    archive = pump.run(max_ticks=20)
    task = archive["context"]["session_audit"]["final_report"]["tasks"]["nav-k"]
    assert task["invalidated_by"]
    assert task["status"] == "cancel_requested"
    assert archive["context"]["session_audit"]["final_report"][
        "deployment_assessment"]["terminated"] is True
    assert archive["tasks"]["nav-k"]["physical_status"] == "cancelled"
    assert archive["tasks"]["nav-k"]["terminal_delivery"] is None

    blocked = Participant(plan, deployment_decision="not_deployable")
    blocked_context, blocked_fleet, _ = _context(tmp_path, blocked)
    blocked_pump = RestaurantExecutionPump(
        blocked_context, runtime=_runtime(),
        observation_provider=FakeObservationProvider(blocked_fleet))
    blocked_archive = blocked_pump.run(max_ticks=5)
    final = blocked_archive["context"]["session_audit"]["final_report"]
    assert final["deployment_assessment"]["terminated"] is True
    assert blocked_fleet.step_calls == 0
    assert blocked_archive["tasks"] == {}


def test_navigation_deadline_and_unsupported_observation_are_honest_failures(tmp_path):
    contract = build_restaurant_deployment_v2()
    plan = _parallel_plan(contract)
    plan["tasks"] = [plan["tasks"][0]]
    plan["assigned_team"] = [plan["tasks"][0]["assigned_agents"][0]]
    participant = Participant(plan)
    context, fleet, _ = _context(tmp_path, participant, arrival_ticks=1000)
    pump = RestaurantExecutionPump(
        context, runtime=_runtime(),
        observation_provider=FakeObservationProvider(fleet))
    deadline_archive = pump.run(max_ticks=90)
    assert deadline_archive["tasks"]["nav-k"]["physical_status"] == "failed"
    assert any(row["disposition"] == "navigation_deadline"
               for row in deadline_archive["physical_records"])

    agent = "restaurant_carter_1"
    region = contract["deployment"]["spawn_regions"][agent]
    observe = _task("unsupported", "observe", agent, {
        "region_id": region, "subject_ref": region,
        "observation_mode": "unbound_private_camera_mode"},
        duration=4.0)
    observe_plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                    "plan_id": "unsupported-observe", "assigned_team": [agent],
                    "tasks": [observe]}
    unsupported_participant = Participant(observe_plan)
    unsupported_context, unsupported_fleet, _ = _context(
        tmp_path, unsupported_participant)
    unsupported_pump = RestaurantExecutionPump(
        unsupported_context, runtime=_runtime(),
        observation_provider=FakeObservationProvider(unsupported_fleet))
    unsupported_archive = unsupported_pump.run(max_ticks=10)
    final_task = unsupported_archive["context"]["session_audit"]["final_report"][
        "tasks"]["unsupported"]
    assert final_task["status"] == "failed"
    assert any(row["reason"] == "unsupported_observation_mode"
               for row in unsupported_archive["platform_gaps"])


def test_multi_leg_latest_terminal_target_wins_colocated_region_overlap(tmp_path):
    contract = build_restaurant_deployment_v2()
    public = contract["public_task"]
    agent = "restaurant_carter_1"
    spawn = contract["deployment"]["spawn_regions"][agent]
    chain = _task("round-trip", "ground_navigation", agent, {
        "region_id": "restaurant_staging",
        "route_ids": [_route(public, spawn, "kitchen_service"),
                      _route(public, "kitchen_service", "restaurant_staging")]},
        duration=8.0)
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "multi-leg-overlap", "assigned_team": [agent],
            "tasks": [chain]}
    participant = Participant(plan)
    context, fleet, _ = _context(tmp_path, participant)
    pump = RestaurantExecutionPump(
        context, runtime=_runtime(),
        observation_provider=FakeObservationProvider(fleet))
    archive = pump.run(max_ticks=20)
    assert archive["context"]["session_audit"]["final_report"]["tasks"][
        "round-trip"]["status"] == "succeeded"
    assert len(archive["tasks"]["round-trip"]["operation_ids"]) == 2
    terminal = next(row for row in reversed(archive["region_records"])
                    if row["robot_regions"].get(agent) == "restaurant_staging")
    assert len(terminal["agents"][agent]["matches"]) >= 2
    assert terminal["agents"][agent]["selection_basis"] == (
        "arrived_operation_target_measured_inside")


def test_lost_result_keeps_physical_terminal_separate_and_session_pending(tmp_path):
    contract = build_restaurant_deployment_v2()
    plan = _parallel_plan(contract)
    plan["tasks"] = [plan["tasks"][0]]
    plan["assigned_team"] = [plan["tasks"][0]["assigned_agents"][0]]
    participant = Participant(plan)
    context, fleet, _ = _context(tmp_path, participant)
    pump = RestaurantExecutionPump(
        context, runtime=_runtime(drop_result=True), result_ttl_s=0.5,
        observation_provider=FakeObservationProvider(fleet))
    archive = pump.run(max_ticks=12)
    task = archive["context"]["session_audit"]["final_report"]["tasks"]["nav-k"]
    assert archive["tasks"]["nav-k"]["physical_status"] == "succeeded"
    assert task["status"] == "pending"
    assert any(row["publication_status"] == "dropped"
               for row in archive["result_delivery_records"])
    assert archive["session_audit_replay"]["validated"] is True
