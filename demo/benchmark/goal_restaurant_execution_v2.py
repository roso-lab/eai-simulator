"""Incremental native Restaurant execution over one physical/transport clock.

This opt-in pump preserves participant-authored plans and dispatch identities.
It starts work only after a complete downlink command is delivered, advances
the full-eight fleet at most once per pump tick, samples native observations
without stepping, and exposes terminal skill results only after their full
uplink payload is delivered.  The archive is diagnostic evidence; it does not
claim contact-based Restaurant business validation or release qualification.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .communication import CommunicationRuntime
from .goal_execution_downlink import GoalExecutionDownlink
from .goal_restaurant_autonomous_v2 import RestaurantAutonomousContext
from .goal_restaurant_fleet import CLOSED_VALIDATION_FLAGS, FLEET_CONTROLLER_REGISTRY
from .goal_restaurant_transport import RestaurantReadinessTransport
from .goal_session_audit import replay_goal_session_audit
from .provenance import canonical_sha256


RESULT_RECIPIENT = "trusted_execution_host"
RESULT_CHANNEL = "goal_execution_result"
SUPPORTED_OBSERVATION_MODE = "service_ground_readiness"
READINESS_MESSAGE_KIND = "restaurant_readiness_receipt"
REQUIRED_READINESS_REGIONS = ("kitchen_service", "dining_service")


def _positive(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{label} must be a finite positive number")
    return float(value)


def _sealed(row: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(row))
    result["evidence_ref"] = "sha256:" + canonical_sha256(result)
    return result


class RestaurantExecutionPump:
    """One bounded native tick pump for the Stage53 Restaurant profile."""

    def __init__(
        self,
        context: RestaurantAutonomousContext,
        *,
        runtime: CommunicationRuntime,
        observation_provider: Any,
        dispatch_ttl_s: float = 30.0,
        result_ttl_s: float = 30.0,
        relay_ttl_s: float = 30.0,
        participant_tick_interval_s: float = 0.5,
    ):
        if not isinstance(context, RestaurantAutonomousContext):
            raise ValueError("context must be a RestaurantAutonomousContext")
        if not isinstance(runtime, CommunicationRuntime):
            raise ValueError("runtime must be a CommunicationRuntime")
        if runtime.time_s != 0.0 or context.fleet.time_s != 0.0:
            raise ValueError("execution pump must bind the pristine native episode epoch")
        self.context = context
        self.runtime = runtime
        self.observation_provider = observation_provider
        self.dispatch_ttl_s = _positive(dispatch_ttl_s, "dispatch_ttl_s")
        self.result_ttl_s = _positive(result_ttl_s, "result_ttl_s")
        self.relay_ttl_s = _positive(relay_ttl_s, "relay_ttl_s")
        self.participant_tick_interval_s = _positive(
            participant_tick_interval_s, "participant_tick_interval_s")
        self.episode_id = context.binding["episode_id"]
        public_task = context.contract["public_task"]
        self.downlink = GoalExecutionDownlink(
            public_task, episode_id=self.episode_id, runtime=runtime,
            ttl_s=self.dispatch_ttl_s)
        metadata = context.fleet.episode_metadata()
        physics_dt = float(context.fleet.session.base_env.sim.get_physics_dt())
        bindings = {
            public_id: {"native_agent_id": row["native_agent_id"]}
            for public_id, row in FLEET_CONTROLLER_REGISTRY.items()
        }
        self.readiness = RestaurantReadinessTransport(
            episode_id=self.episode_id, runtime=runtime,
            robot_bindings=bindings,
            episode_step_origin=metadata["origin_physics_step"],
            physics_dt_s=physics_dt)
        self._task_rows: dict[str, dict[str, Any]] = {}
        self._result_messages: dict[str, dict[str, Any]] = {}
        self._visible_observations: list[dict[str, Any]] = []
        self._physical_records: list[dict[str, Any]] = []
        self._delivery_records: list[dict[str, Any]] = []
        self._region_records: list[dict[str, Any]] = []
        self._platform_gaps: list[dict[str, Any]] = []
        self._ticks: list[dict[str, Any]] = []
        self._result_sequence = 0
        self._next_participant_tick_s = self.participant_tick_interval_s
        self._scheduled_wakeups_emitted: set[str] = set()
        self._initialized = False
        self._closed = False
        self._current_agents = set(public_task_row["id"] for public_task_row in
                                   public_task["candidate_robot_pool"])
        self._last_robot_regions = deepcopy(context.initial_state["robot_regions"])
        self._mobility = {
            row["id"]: row["mobility"]
            for row in public_task["candidate_robot_pool"]
        }
        self._profile = self._build_profile()
        self._profile_ack = context.initialize_execution(self._profile)
        context.execution_loop_connected = True

    def _build_profile(self) -> dict[str, Any]:
        return {
            "schema": "eai.restaurant.execution_profile",
            "version": 1,
            "episode_id": self.episode_id,
            "public_task_sha256": canonical_sha256(
                self.context.contract["public_task"]),
            "incremental_step": "one_full8_fleet_control_tick",
            "dispatch": {
                "channel": "goal_execution_dispatch",
                "cancel_channel": "goal_execution_cancel",
                "pool": "backhaul_downlink",
                "ttl_s": self.dispatch_ttl_s,
                "full_task_payload_metered": True,
                "earliest_physical_start": "max(delivery,task.execution.start_s)",
            },
            "result": {
                "channel": RESULT_CHANNEL,
                "pool": "backhaul_uplink",
                "recipient": RESULT_RECIPIENT,
                "ttl_s": self.result_ttl_s,
                "physical_completed_at_s_separate_from_result_delivered_at_s": True,
            },
            "readiness_relay": {
                "message_kind": READINESS_MESSAGE_KIND,
                "selection_version": 1,
                "selection_time": "dispatch_skill_acceptance",
                "explicit_dispatch_evidence_refs_take_precedence": True,
                "empty_refs": (
                    "latest_measurement_time_among_fresh_participant_visible_"
                    "successful_ground_readiness_observations_per_required_region"),
                "required_regions": list(REQUIRED_READINESS_REGIONS),
                "equal_latest_conflicts": "fail_closed",
                "missing_region": "fail_closed",
                "producer_or_prerequisite_constraints_added": False,
            },
            "participant_visibility": (
                "dispatch/result/observation effects appear only after transport delivery"),
            "participant_clock": {
                "event_type": "tick",
                "heartbeat_interval_s": self.participant_tick_interval_s,
                "heartbeat_scope": (
                    "pending_cancel_requested_or_scheduled_inside_execution_window"),
                "scheduled_start_wakeup": True,
                "physical_fleet_ticks_remain_independent": True,
            },
        }

    def profile(self) -> dict[str, Any]:
        return deepcopy(self._profile)

    def _session_report(self) -> dict[str, Any]:
        report = self.context.session.report()
        self._current_agents = set(report["current_agents"])
        return report

    def _task_state_view(self, report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            task_id: {
                "status": row["status"],
                "invalidated_by": list(row.get("invalidated_by", [])),
            }
            for task_id, row in report.get("tasks", {}).items()
        }

    def _dispatch_evidence_refs(
        self, report: Mapping[str, Any], action_id: str, task: Mapping[str, Any],
    ) -> list[str]:
        action = next((row for row in report["actions"]
                       if row["action_id"] == action_id), None)
        if action is None or action["type"] != "dispatch_skill":
            raise RuntimeError("accepted dispatch action disappeared from session")
        return sorted(set(task.get("evidence_refs", []))
                      | set(action["payload"].get("evidence_refs", [])))

    def _freeze_relay_selection(
        self, task: Mapping[str, Any], dispatch_refs: list[str], accepted_at_s: float,
    ) -> dict[str, Any]:
        candidates = [deepcopy(row) for row in self._visible_observations
                      if (row["result_delivered_at_s"] <= accepted_at_s
                          and accepted_at_s < row["valid_until_s"])]
        selected: dict[str, dict[str, Any]] = {}
        error = None
        if dispatch_refs:
            matches = [row for row in candidates
                       if row["participant_message_id"] in dispatch_refs]
            if (len(dispatch_refs) != len(REQUIRED_READINESS_REGIONS)
                    or set(dispatch_refs)
                    != {row["participant_message_id"] for row in matches}):
                error = "explicit_refs_include_unknown_extra_or_duplicate_observations"
            for region in REQUIRED_READINESS_REGIONS:
                rows = [row for row in matches if row["region_id"] == region]
                if len(rows) != 1:
                    error = "explicit_refs_do_not_select_one_observation_per_region"
                    break
                selected[region] = rows[0]
            if error is None and len(matches) != len(REQUIRED_READINESS_REGIONS):
                error = "explicit_refs_include_unknown_or_extra_observations"
        elif task.get("arguments", {}).get("message_kind") == READINESS_MESSAGE_KIND:
            for region in REQUIRED_READINESS_REGIONS:
                rows = [row for row in candidates if row["region_id"] == region]
                if not rows:
                    error = f"missing_visible_observation:{region}"
                    break
                latest = max(row["physical_completed_at_s"] for row in rows)
                at_latest = [row for row in rows
                             if row["physical_completed_at_s"] == latest]
                refs = {row["native_evidence_ref"] for row in at_latest}
                if len(refs) != 1:
                    error = f"ambiguous_equal_latest_observation:{region}"
                    break
                selected[region] = at_latest[0]
        else:
            error = "unsupported_relay_message_kind"
        return {
            "selection_version": 1,
            "accepted_at_s": accepted_at_s,
            "dispatch_evidence_refs": list(dispatch_refs),
            "candidates": candidates,
            "selected": deepcopy(selected),
            "error": error,
        }

    def _ingest_host_actions(self, results: list[Mapping[str, Any]]) -> None:
        if not results:
            return
        report = self._session_report()
        for result in results:
            kind = result.get("type")
            if kind not in {"dispatch_skill", "cancel_skill"}:
                continue
            pending = result.get("pending_task")
            if not isinstance(pending, Mapping):
                raise RuntimeError("accepted execution action lacks its pending task")
            task = deepcopy(pending["task"])
            task_id = task["task_id"]
            if kind == "dispatch_skill":
                action_id = result["action_id"]
                operation = self.downlink.queue_dispatch(
                    task, dispatch_action_id=action_id,
                    time_s=float(pending["dispatched_at_s"]))
                refs = self._dispatch_evidence_refs(report, action_id, task)
                row = {
                    "task": task,
                    "dispatch_action_id": action_id,
                    "dispatch_message_id": operation["message_id"],
                    "dispatch_accepted_at_s": float(pending["dispatched_at_s"]),
                    "dispatch_evidence_refs": refs,
                    "phase": "downlink_pending",
                    "ready_at_s": None,
                    "physical_started_at_s": None,
                    "physical_terminal_at_s": None,
                    "physical_status": None,
                    "physical_records": [],
                    "route_index": 0,
                    "operation_ids": [],
                    "relay_message_id": None,
                    "relay_selection": None,
                    "current_result_message_id": None,
                    "terminal_delivery": None,
                }
                if task["skill"] == "relay":
                    row["relay_selection"] = self._freeze_relay_selection(
                        task, refs, float(pending["dispatched_at_s"]))
                self._task_rows[task_id] = row
            else:
                row = self._task_rows.get(task_id)
                if row is None:
                    raise RuntimeError("accepted cancel has no queued native dispatch")
                operation = self.downlink.queue_cancel(
                    task, cancel_action_id=result["action_id"],
                    time_s=self.context.fleet.time_s)
                row["cancel_action_id"] = result["action_id"]
                row["cancel_message_id"] = operation["message_id"]

    def _host_turn(self) -> list[dict[str, Any]]:
        results = self.context.host.request_actions()
        self._ingest_host_actions(results)
        return results

    def _stamp_for_agent(
        self, public_id: str, snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = snapshot or self.context.fleet.snapshot()
        sample = snapshot["agents"][public_id]
        return {
            "producer_id": public_id,
            "native_agent_id": sample["native_agent_id"],
            "simulation_step": sample["physics_step"],
            "time_s": sample["time_s"],
            "root_state_w": list(sample["root_state_w"]),
        }

    def _measure_regions(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        bindings = self.context.contract["deployment"]["region_bindings"]
        current = self._current_agents
        mapped: dict[str, str] = {}
        details: dict[str, Any] = {}
        for public_id, sample in snapshot["agents"].items():
            matches = []
            mobility = self._mobility[public_id]
            for region_id, binding in bindings.items():
                if binding.get("geometry_status") != "defined":
                    continue
                center = binding.get("center_xyz_m", binding.get("center_xy_m"))
                if center is None:
                    continue
                modes = set(binding.get("access_modes", []))
                if (mobility == "aerial" and "aerial_navigation" not in modes):
                    continue
                if (mobility == "ground"
                        and not modes.intersection({"ground_navigation", "stair_traversal"})):
                    continue
                distance = math.dist(sample["position_m"][:len(center)], center)
                if distance <= float(binding["tolerance_m"]):
                    matches.append({"region_id": region_id, "distance_m": distance,
                                    "tolerance_m": float(binding["tolerance_m"])})
            selected = None
            selection_basis = None
            previous = self._last_robot_regions.get(public_id)
            matched_ids = {row["region_id"] for row in matches}
            actively_moving = any(
                operation.get("public_agent_id") == public_id
                and operation.get("status") == "active"
                for operation in snapshot.get("operations", {}).values())
            arrived = [operation for operation in
                       snapshot.get("operations", {}).values()
                       if (operation.get("public_agent_id") == public_id
                           and operation.get("status") == "arrived"
                           and operation.get("goal_binding", {}).get("region_id")
                           in matched_ids)]
            latest_arrived_target = (
                arrived[-1].get("goal_binding", {}).get("region_id")
                if arrived else None)
            if actively_moving:
                selection_basis = "active_navigation_region_omitted"
            elif latest_arrived_target is not None:
                selected = latest_arrived_target
                selection_basis = "arrived_operation_target_measured_inside"
            elif previous in matched_ids:
                selected = previous
                selection_basis = "previous_region_still_measured_inside"
            elif len(matches) == 1:
                selected = matches[0]["region_id"]
                selection_basis = "unique_measured_membership"
            details[public_id] = {
                "matches": matches,
                "selected_region_id": selected,
                "selection_basis": selection_basis,
                "root_state_w": list(sample["root_state_w"]),
            }
            if public_id in current and selected is not None:
                mapped[public_id] = selected
        self._last_robot_regions.update(mapped)
        body = {
            "schema": "eai.restaurant.measured_region_membership",
            "version": 1,
            "episode_id": self.episode_id,
            "physics_step": snapshot["physics_step"],
            "time_s": snapshot["time_s"],
            "robot_regions": mapped,
            "agents": details,
            "membership_rule": "maintained_geometry_and_tolerance_only",
        }
        row = _sealed(body)
        self._region_records.append(row)
        return row

    def _refresh_runtime_state(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        regions = self._measure_regions(snapshot)
        self.context.session.update_runtime_state(
            float(snapshot["time_s"]), regions["robot_regions"], {})
        return regions

    def _record_gap(self, task_id: str, now: float, reason: str, **data: Any) -> dict[str, Any]:
        row = _sealed({
            "schema": "eai.restaurant.execution_platform_gap",
            "version": 1,
            "episode_id": self.episode_id,
            "task_id": task_id,
            "time_s": now,
            "reason": reason,
            **deepcopy(data),
        })
        self._platform_gaps.append(row)
        return row

    def _physical_record(
        self, task_id: str, *, status: str, now: float,
        snapshot: Mapping[str, Any], region_record: Mapping[str, Any],
        evidence: list[Mapping[str, Any]], disposition: str,
    ) -> dict[str, Any]:
        row = _sealed({
            "schema": "eai.restaurant.native_task_terminal",
            "version": 1,
            "episode_id": self.episode_id,
            "task_id": task_id,
            "dispatch_action_id": self._task_rows[task_id]["dispatch_action_id"],
            "status": status,
            "physical_terminal_at_s": now,
            "disposition": disposition,
            "task": deepcopy(self._task_rows[task_id]["task"]),
            "fleet_snapshot": deepcopy(dict(snapshot)),
            "fleet_snapshot_sha256": canonical_sha256(snapshot),
            "region_record": deepcopy(dict(region_record)),
            "region_record_sha256": canonical_sha256(region_record),
            "native_evidence": deepcopy(evidence),
            **CLOSED_VALIDATION_FLAGS,
        })
        self._physical_records.append(row)
        task_row = self._task_rows[task_id]
        task_row["physical_records"].append(row["evidence_ref"])
        task_row["physical_terminal_at_s"] = now
        task_row["physical_status"] = status
        return row

    def _publish_result(
        self, task_id: str, *, status: str, physical_completed_at_s: float,
        evidence_refs: list[str], source_record_ref: str,
    ) -> dict[str, Any]:
        row = self._task_rows[task_id]
        producer = row["task"]["assigned_agents"][0]
        self._result_sequence += 1
        message_id = (
            f"{self.episode_id}:execution:result:{row['dispatch_action_id']}:"
            f"r{self._result_sequence}")
        receipt = {
            "backend_id": "restaurant-native-execution-v2",
            "receipt_ref": f"{self.episode_id}/{task_id}/result/{self._result_sequence}",
            "task_id": task_id,
            "action_id": row["dispatch_action_id"],
            "status": status,
            "completed_at_s": physical_completed_at_s,
            "episode_id": self.episode_id,
            "evidence_refs": list(dict.fromkeys(evidence_refs)),
        }
        participant_observation = deepcopy(row.get("participant_observation"))
        estimate = {
            "schema": "eai.restaurant.execution_result_wire",
            "version": 1,
            "episode_id": self.episode_id,
            "task_id": task_id,
            "dispatch_action_id": row["dispatch_action_id"],
            "physical_completed_at_s": physical_completed_at_s,
            "status": status,
            "receipt": receipt,
            "source_record_ref": source_record_ref,
            "participant_observation": participant_observation,
        }
        message = self.runtime.publish(
            message_id=message_id, producer_id=producer,
            recipients=[RESULT_RECIPIENT], channel=RESULT_CHANNEL,
            estimate=estimate, published_at_s=physical_completed_at_s,
            valid_until_s=physical_completed_at_s + self.result_ttl_s,
            pool="backhaul_uplink", direction="uplink",
            access_robot_id=producer)
        record = {
            "message_id": message_id,
            "task_id": task_id,
            "status": status,
            "physical_completed_at_s": physical_completed_at_s,
            "receipt": receipt,
            "source_record_ref": source_record_ref,
            "wire_size_bytes": message["size_bytes"],
            "publication_status": "published",
            "delivered_at_s": None,
            "consumed_at_s": None,
            "terminal_reason": None,
            "superseded": False,
            "participant_observation": participant_observation,
            "participant_observation_emitted": False,
        }
        previous = row.get("current_result_message_id")
        if previous is not None and previous in self._result_messages:
            self._result_messages[previous]["superseded"] = True
        row["current_result_message_id"] = message_id
        row["phase"] = "result_in_transit"
        self._result_messages[message_id] = record
        return record

    def _finalize_physical(
        self, task_id: str, *, status: str, snapshot: Mapping[str, Any],
        region_record: Mapping[str, Any], evidence: list[Mapping[str, Any]],
        disposition: str,
    ) -> None:
        now = float(snapshot["time_s"])
        record = self._physical_record(
            task_id, status=status, now=now, snapshot=snapshot,
            region_record=region_record, evidence=evidence,
            disposition=disposition)
        refs = [record["evidence_ref"]]
        refs.extend(row["evidence_ref"] for row in evidence
                    if isinstance(row, Mapping) and row.get("evidence_ref"))
        self._publish_result(
            task_id, status=status, physical_completed_at_s=now,
            evidence_refs=refs, source_record_ref=record["evidence_ref"])

    def _start_navigation(self, task_id: str, snapshot: Mapping[str, Any]) -> None:
        row = self._task_rows[task_id]
        task = row["task"]
        routes = task["arguments"].get("route_ids", [])
        if not routes:
            raise ValueError("navigation task has no public route chain")
        route_id = routes[row["route_index"]]
        declared = next((item for item in self.context.contract["public_task"][
            "public_world"]["routes"] if item["id"] == route_id), None)
        if declared is None:
            raise ValueError("navigation route is absent from public topology")
        agent = task["assigned_agents"][0]
        planned = self.context.fleet.plan_route(
            agent, route_id, declared["to_region_id"])
        operation_id = (
            f"{self.episode_id}:{row['dispatch_action_id']}:"
            f"leg{row['route_index']}")
        operation = self.context.fleet.begin_navigation(
            operation_id, planned,
            deadline_s=(float(task["execution"]["start_s"])
                        + float(task["execution"]["duration_s"])))
        row["operation_ids"].append(operation_id)
        row["phase"] = "running_navigation"
        if row["physical_started_at_s"] is None:
            row["physical_started_at_s"] = operation["started_at_s"]
            self.downlink.mark_started(
                task_id, dispatch_action_id=row["dispatch_action_id"],
                time_s=operation["started_at_s"])

    def _start_observation(self, task_id: str, snapshot: Mapping[str, Any]) -> None:
        row = self._task_rows[task_id]
        task = row["task"]
        producer = task["assigned_agents"][0]
        now = float(snapshot["time_s"])
        row["physical_started_at_s"] = now
        self.downlink.mark_started(
            task_id, dispatch_action_id=row["dispatch_action_id"], time_s=now)
        mode = task["arguments"].get("observation_mode")
        region = task["arguments"].get("region_id")
        if mode != SUPPORTED_OBSERVATION_MODE:
            gap = self._record_gap(
                task_id, now, "unsupported_observation_mode", observation_mode=mode)
            region_record = self._measure_regions(snapshot)
            self._finalize_physical(
                task_id, status="failed", snapshot=snapshot,
                region_record=region_record, evidence=[gap],
                disposition="platform_gap")
            return
        try:
            scene = self.observation_provider.ground_readiness(producer, region)
            bound = self.observation_provider.bind_transport_stamp(scene)
            native = self.readiness.record_observation(
                task_id=task_id, producer_id=producer,
                observation_id=f"{self.episode_id}:observation:{task_id}",
                scene_observation=bound)
        except Exception as exc:
            gap = self._record_gap(
                task_id, now, "native_observation_unavailable",
                exception_type=type(exc).__name__, exception_message=str(exc))
            region_record = self._measure_regions(snapshot)
            self._finalize_physical(
                task_id, status="failed", snapshot=snapshot,
                region_record=region_record, evidence=[gap],
                disposition="native_observation_failure")
            return
        region_record = self._measure_regions(snapshot)
        row["observation_candidate"] = {
            "task_id": task_id,
            "region_id": region,
            "producer_id": producer,
            "native_evidence_ref": native["evidence_ref"],
            "service_observation_id": native["service_record"]["observation_id"],
            "physical_completed_at_s": now,
        }
        row["participant_observation"] = {
            "kind": "restaurant_ground_readiness_observation",
            "data": {
                "task_id": task_id,
                "region_id": region,
                "producer_id": producer,
                "service_observation_id": native["service_record"]["observation_id"],
                "evidence_ref": native["evidence_ref"],
                "scene_observation_validated": bool(
                    scene.get("scene_observation_validated")),
                "measured_at_s": now,
            },
        }
        self._finalize_physical(
            task_id, status="succeeded", snapshot=snapshot,
            region_record=region_record, evidence=[native],
            disposition="native_ground_readiness_observation")

    def _start_relay(self, task_id: str, snapshot: Mapping[str, Any]) -> None:
        row = self._task_rows[task_id]
        task = row["task"]
        producer = task["assigned_agents"][0]
        now = float(snapshot["time_s"])
        row["physical_started_at_s"] = now
        self.downlink.mark_started(
            task_id, dispatch_action_id=row["dispatch_action_id"], time_s=now)
        selection = row["relay_selection"]
        if selection is None or selection["error"] is not None:
            gap = self._record_gap(
                task_id, now, (selection or {}).get("error") or "relay_selection_missing",
                relay_selection=selection)
            region_record = self._measure_regions(snapshot)
            self._finalize_physical(
                task_id, status="failed", snapshot=snapshot,
                region_record=region_record, evidence=[gap],
                disposition="readiness_selection_failure")
            return
        refs = [selection["selected"][region]["service_observation_id"]
                for region in REQUIRED_READINESS_REGIONS]
        message_id = f"{self.episode_id}:readiness:{row['dispatch_action_id']}"
        deadline = (float(task["execution"]["start_s"])
                    + float(task["execution"]["duration_s"]))
        expiry = min(now + self.relay_ttl_s, deadline)
        if expiry <= now:
            gap = self._record_gap(task_id, now, "relay_deadline_elapsed")
            region_record = self._measure_regions(snapshot)
            self._finalize_physical(
                task_id, status="failed", snapshot=snapshot,
                region_record=region_record, evidence=[gap],
                disposition="execution_deadline")
            return
        publication = self.readiness.publish_relay(
            task_id=task_id, producer_id=producer, message_id=message_id,
            observation_refs=refs, stamp=self._stamp_for_agent(producer, snapshot),
            valid_until_s=expiry)
        row["relay_message_id"] = message_id
        row["relay_publication"] = publication
        row["phase"] = "running_relay"

    def _start_ready_tasks(
        self, now: float, snapshot: Mapping[str, Any],
        report: Mapping[str, Any],
    ) -> list[str]:
        started = []
        terminated = bool(report.get("deployment_assessment", {}).get("terminated"))
        if terminated:
            return started
        for task_id, row in self._task_rows.items():
            if row["phase"] != "ready" or now < float(row["task"]["execution"]["start_s"]):
                continue
            state = report["tasks"].get(task_id, {})
            if state.get("status") != "pending" or state.get("invalidated_by"):
                continue
            try:
                skill = row["task"]["skill"]
                if skill in {"ground_navigation", "aerial_navigation"}:
                    self._start_navigation(task_id, snapshot)
                elif skill == "observe":
                    self._start_observation(task_id, snapshot)
                elif skill == "relay":
                    self._start_relay(task_id, snapshot)
                else:
                    gap = self._record_gap(
                        task_id, now, "unsupported_restaurant_skill", skill=skill)
                    region_record = self._measure_regions(snapshot)
                    self._finalize_physical(
                        task_id, status="failed", snapshot=snapshot,
                        region_record=region_record, evidence=[gap],
                        disposition="platform_gap")
                started.append(task_id)
            except Exception as exc:
                gap = self._record_gap(
                    task_id, now, "native_start_failure",
                    exception_type=type(exc).__name__, exception_message=str(exc))
                region_record = self._measure_regions(snapshot)
                self._finalize_physical(
                    task_id, status="failed", snapshot=snapshot,
                    region_record=region_record, evidence=[gap],
                    disposition="native_start_failure")
                started.append(task_id)
        return started

    def _cancel_or_fail_task(
        self, task_id: str, *, status: str, reason: str,
        snapshot: Mapping[str, Any], transport: Mapping[str, Any],
    ) -> None:
        row = self._task_rows[task_id]
        if row["terminal_delivery"] is not None:
            return
        evidence: list[Mapping[str, Any]] = []
        if row["phase"] == "running_navigation" and row["operation_ids"]:
            evidence.append(self.context.fleet.cancel_navigation(
                row["operation_ids"][-1]))
        evidence.append(_sealed({
            "kind": "execution_transport_disposition",
            "task_id": task_id,
            "status": status,
            "reason": reason,
            "transport": deepcopy(dict(transport)),
        }))
        region_record = self._measure_regions(snapshot)
        self._finalize_physical(
            task_id, status=status, snapshot=snapshot,
            region_record=region_record, evidence=evidence,
            disposition=reason)

    def _advance_downlink(
        self, now: float, snapshot: Mapping[str, Any], report: Mapping[str, Any],
    ) -> dict[str, Any]:
        for task_id, session_row in report.get("tasks", {}).items():
            native = self._task_rows.get(task_id)
            if (native is None or session_row.get("status") != "cancel_requested"
                    or native.get("cancel_message_id") is not None
                    or native["terminal_delivery"] is not None):
                continue
            operation = self.downlink.queue_cancel(
                native["task"], cancel_action_id=None, time_s=now)
            native["cancel_action_id"] = operation["action_id"]
            native["cancel_message_id"] = operation["message_id"]
        outcomes = self.downlink.advance(
            now, active_agent_ids=report["current_agents"],
            task_states=self._task_state_view(report),
            terminated=bool(report.get("deployment_assessment", {}).get("terminated")))
        for item in outcomes["ready_dispatches"]:
            row = self._task_rows[item["task_id"]]
            if row["phase"] == "downlink_pending":
                row["phase"] = "ready"
                row["ready_at_s"] = item["delivered_at_s"]
        for item in outcomes["failed_dispatches"]:
            row = self._task_rows.get(item["task_id"])
            if row is not None and row["terminal_delivery"] is None:
                self._cancel_or_fail_task(
                    item["task_id"], status="failed", reason=item["reason"],
                    snapshot=snapshot, transport=item)
        for item in outcomes["ready_cancels"]:
            self._cancel_or_fail_task(
                item["task_id"], status="cancelled",
                reason=item["disposition"], snapshot=snapshot, transport=item)
        return outcomes

    def _poll_navigation(
        self, snapshot: Mapping[str, Any], region_record: Mapping[str, Any],
    ) -> list[str]:
        terminals = []
        operations = snapshot["operations"]
        for task_id, row in self._task_rows.items():
            if row["phase"] != "running_navigation":
                continue
            operation = operations[row["operation_ids"][-1]]
            if operation["status"] == "active":
                continue
            if operation["status"] == "arrived":
                routes = row["task"]["arguments"]["route_ids"]
                if row["route_index"] + 1 < len(routes):
                    row["route_index"] += 1
                    try:
                        self._start_navigation(task_id, snapshot)
                    except Exception as exc:
                        gap = self._record_gap(
                            task_id, float(snapshot["time_s"]),
                            "route_chain_continuation_failure",
                            exception_type=type(exc).__name__,
                            exception_message=str(exc))
                        self._finalize_physical(
                            task_id, status="failed", snapshot=snapshot,
                            region_record=region_record,
                            evidence=[operation, gap],
                            disposition="route_chain_failure")
                        terminals.append(task_id)
                    continue
                expected = row["task"]["arguments"]["region_id"]
                measured = region_record["robot_regions"].get(
                    row["task"]["assigned_agents"][0])
                if measured != expected:
                    gap = self._record_gap(
                        task_id, float(snapshot["time_s"]),
                        "arrival_outside_region_membership",
                        expected_region_id=expected, measured_region_id=measured)
                    self._finalize_physical(
                        task_id, status="failed", snapshot=snapshot,
                        region_record=region_record, evidence=[operation, gap],
                        disposition="measured_region_membership_failure")
                else:
                    self._finalize_physical(
                        task_id, status="succeeded", snapshot=snapshot,
                        region_record=region_record, evidence=[operation],
                        disposition="native_navigation_arrival")
            else:
                self._finalize_physical(
                    task_id, status=("cancelled" if operation["status"] == "cancelled"
                                     else "failed"),
                    snapshot=snapshot, region_record=region_record,
                    evidence=[operation], disposition=operation["terminal_reason"])
            terminals.append(task_id)
        return terminals

    def _poll_relays(
        self, snapshot: Mapping[str, Any], region_record: Mapping[str, Any],
    ) -> list[str]:
        terminals = []
        for task_id, row in self._task_rows.items():
            if row["phase"] != "running_relay":
                continue
            producer = row["task"]["assigned_agents"][0]
            try:
                result = self.readiness.poll_relay(
                    message_id=row["relay_message_id"],
                    stamp=self._stamp_for_agent(producer, snapshot),
                    receipt_id=f"{self.episode_id}:readiness-receipt:{task_id}")
            except Exception as exc:
                gap = self._record_gap(
                    task_id, float(snapshot["time_s"]), "readiness_poll_failure",
                    exception_type=type(exc).__name__, exception_message=str(exc))
                self._finalize_physical(
                    task_id, status="failed", snapshot=snapshot,
                    region_record=region_record, evidence=[gap],
                    disposition="readiness_poll_failure")
                terminals.append(task_id)
                continue
            if result["status"] == "pending":
                continue
            if result["status"] == "failed":
                evidence = [_sealed({"kind": "readiness_transport_failure", **result})]
                status, disposition = "failed", result["reason"]
            else:
                evidence = [result["native_evidence"]]
                deadline = (float(row["task"]["execution"]["start_s"])
                            + float(row["task"]["execution"]["duration_s"]))
                if float(snapshot["time_s"]) > deadline:
                    evidence.append(self._record_gap(
                        task_id, float(snapshot["time_s"]),
                        "readiness_receipt_after_execution_deadline"))
                    status, disposition = "failed", "execution_deadline"
                else:
                    status, disposition = "succeeded", "delivered_readiness_receipt"
            self._finalize_physical(
                task_id, status=status, snapshot=snapshot,
                region_record=region_record, evidence=evidence,
                disposition=disposition)
            terminals.append(task_id)
        return terminals

    def _deliver_results(self, now: float) -> list[dict[str, Any]]:
        self.runtime.advance(now)
        messages = self.runtime.report()["messages"]
        delivered = []
        for message_id, record in self._result_messages.items():
            if record["publication_status"] != "published":
                continue
            copy = messages[message_id]["recipients"][RESULT_RECIPIENT]
            status = copy["status"]
            if status in {"queued", "in_transit"}:
                continue
            if status in {"dropped", "expired"}:
                record["publication_status"] = status
                record["terminal_reason"] = copy.get("drop_reason") or status
                self._delivery_records.append(deepcopy(record))
                continue
            self.runtime.consume(
                message_id, recipient=RESULT_RECIPIENT,
                consumer_id=f"restaurant_execution_pump:{record['task_id']}",
                time_s=now)
            record["publication_status"] = "delivered"
            record["delivered_at_s"] = copy["delivered_at_s"]
            record["consumed_at_s"] = now
            task_row = self._task_rows[record["task_id"]]
            if record["superseded"] or task_row["current_result_message_id"] != message_id:
                record["terminal_reason"] = "superseded_before_delivery"
                self._delivery_records.append(deepcopy(record))
                continue
            session_task = self._session_report()["tasks"][record["task_id"]]
            if session_task["status"] not in {"pending", "cancel_requested"}:
                record["terminal_reason"] = "session_task_already_terminal"
                self._delivery_records.append(deepcopy(record))
                continue
            if (record["status"] == "succeeded"
                    and (session_task["status"] == "cancel_requested"
                         or session_task.get("invalidated_by"))):
                record["terminal_reason"] = "terminal_race_lost"
                self._delivery_records.append(deepcopy(record))
                continue
            completion = self.context.session.complete_skill_delivery(
                record["task_id"],
                physical_completed_at_s=record["physical_completed_at_s"],
                delivered_at_s=now, status=record["status"],
                receipt=record["receipt"])
            participant_observation = record.get("participant_observation")
            if participant_observation is not None and record["status"] == "succeeded":
                message = messages[message_id]
                self.context.session.emit("observation", now, {
                    "message_id": message_id,
                    "producer_id": message["payload"]["producer_id"],
                    "kind": participant_observation["kind"],
                    "data": deepcopy(participant_observation["data"]),
                    "valid_until_s": message["payload"]["valid_until_s"],
                })
                record["participant_observation_emitted"] = True
            task_row["terminal_delivery"] = {
                "message_id": message_id,
                "physical_completed_at_s": record["physical_completed_at_s"],
                "result_delivered_at_s": now,
                "status": record["status"],
                "completion": completion,
            }
            task_row["phase"] = "terminal"
            self.downlink.mark_terminal(
                record["task_id"],
                dispatch_action_id=task_row["dispatch_action_id"],
                status=record["status"], time_s=now,
                disposition="result_uplink_delivered")
            if record["status"] == "succeeded" and "observation_candidate" in task_row:
                self._visible_observations.append({
                    **deepcopy(task_row["observation_candidate"]),
                    "participant_message_id": message_id,
                    "result_message_id": message_id,
                    "result_delivered_at_s": now,
                    "valid_until_s": messages[message_id]["payload"]["valid_until_s"],
                })
            self._delivery_records.append(deepcopy(record))
            delivered.append(deepcopy(task_row["terminal_delivery"]))
        return delivered

    def _participant_tick_due(self, now: float) -> bool:
        report = self._session_report()
        scheduled_due = []
        for task_id, row in report.get("tasks", {}).items():
            if (row["status"] == "scheduled"
                    and task_id not in self._scheduled_wakeups_emitted
                    and float(row["task"]["execution"]["start_s"]) <= now):
                scheduled_due.append(task_id)
        if scheduled_due:
            self._scheduled_wakeups_emitted.update(scheduled_due)
            return True
        actionable = any(
            (row["status"] in {"pending", "cancel_requested"}
             or (row["status"] == "scheduled"
                 and float(row["task"]["execution"]["start_s"]) <= now
                 < (float(row["task"]["execution"]["start_s"])
                    + float(row["task"]["execution"]["duration_s"]))))
            for row in report.get("tasks", {}).values())
        if actionable and now + 1e-9 >= self._next_participant_tick_s:
            while self._next_participant_tick_s <= now + 1e-9:
                self._next_participant_tick_s += self.participant_tick_interval_s
            return True
        return False

    def step_once(self) -> dict[str, Any]:
        """Advance one bounded incremental iteration and at most one fleet tick."""
        if self._closed:
            raise RuntimeError("Restaurant execution pump is closed")
        host_actions = []
        if not self._initialized:
            host_actions = self._host_turn()
            self._initialized = True
        else:
            # Deliver externally injected lifecycle/deployment events at the
            # current native boundary before any silent runtime-state advance.
            host_actions = self._host_turn()
        before = self.context.fleet.snapshot()
        now = float(before["time_s"])
        report = self._session_report()
        downlink = self._advance_downlink(now, before, report)
        started = self._start_ready_tasks(now, before, report)

        report = self._session_report()
        terminated = bool(report.get("deployment_assessment", {}).get("terminated"))
        tick = None
        if not terminated:
            tick = self.context.fleet.step_once()
            after = tick
        else:
            after = before
        region_record = self._refresh_runtime_state(after)
        navigation_terminals = self._poll_navigation(after, region_record)
        relay_terminals = self._poll_relays(after, region_record)
        delivered = self._deliver_results(float(after["time_s"]))
        # Completion requires a fresh snapshot before the participant can
        # dispatch dependent work. Rebind to the same measured native state.
        if delivered:
            region_record = self._refresh_runtime_state(after)

        emitted_tick = False
        if (not terminated and tick is not None
                and self._participant_tick_due(float(after["time_s"]))):
            self.context.session.emit("tick", float(after["time_s"]), {
                "state_digest": region_record["evidence_ref"],
            })
            emitted_tick = True
        # New action_result, lifecycle, assistance, or due tick events are the
        # only participant wakeups. request_actions returns immediately if an
        # external caller has not queued any new public event.
        more_actions = self._host_turn()
        host_actions.extend(more_actions)
        final_report = self._session_report()
        finally_terminated = bool(
            final_report.get("deployment_assessment", {}).get("terminated"))
        if finally_terminated:
            for task_id, task_row in self._task_rows.items():
                if (task_row["terminal_delivery"] is None
                        and task_row["phase"] != "terminal"):
                    self._cancel_or_fail_task(
                        task_id, status="cancelled",
                        reason="terminal_human_assistance",
                        snapshot=after,
                        transport={"disposition": "local_terminal_safety_stop"})
        row = {
            "schema": "eai.restaurant.execution_pump_tick",
            "version": 1,
            "index": len(self._ticks),
            "start_time_s": now,
            "time_s": float(after["time_s"]),
            "fleet_tick_advanced": tick is not None,
            "fleet_tick_physics_step": after["physics_step"],
            "host_action_ids": [item.get("action_id") for item in host_actions],
            "downlink_outcome_counts": {
                key: len(value) for key, value in downlink.items()},
            "started_task_ids": started,
            "navigation_terminal_task_ids": navigation_terminals,
            "relay_terminal_task_ids": relay_terminals,
            "delivered_result_task_ids": [item["completion"]["task_id"]
                                          for item in delivered],
            "public_tick_emitted": emitted_tick,
            "region_record_ref": region_record["evidence_ref"],
            "terminated": finally_terminated,
        }
        self._ticks.append(row)
        return deepcopy(row)

    def _all_terminal(self) -> bool:
        report = self._session_report()
        tasks = report.get("tasks", {})
        if not tasks:
            return False
        return (all(row["status"] in {"succeeded", "failed", "cancelled", "superseded"}
                    for row in tasks.values())
                and not self.downlink.has_pending_work()
                and all(row["publication_status"] != "published"
                        for row in self._result_messages.values()))

    def run(self, *, max_ticks: int) -> dict[str, Any]:
        if isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or max_ticks <= 0:
            raise ValueError("max_ticks must be a positive integer")
        for _ in range(max_ticks):
            self.step_once()
            report = self._session_report()
            if (report.get("deployment_assessment", {}).get("terminated")
                    or self._all_terminal()):
                break
        return self.report()

    def report(self) -> dict[str, Any]:
        """Build the complete evaluator archive once, after incremental work."""
        context_archive = self.context.archive()
        replay = replay_goal_session_audit(
            context_archive["session_audit"],
            self.context.contract["public_task"],
            self.context.contract["participant_api"])
        return {
            "schema": "eai.restaurant.execution_pump",
            "version": 1,
            "episode_id": self.episode_id,
            "profile": deepcopy(self._profile),
            "profile_ack": deepcopy(self._profile_ack),
            "ticks": deepcopy(self._ticks),
            "tasks": deepcopy(self._task_rows),
            "physical_records": deepcopy(self._physical_records),
            "result_delivery_records": deepcopy(self._delivery_records),
            "region_records": deepcopy(self._region_records),
            "platform_gaps": deepcopy(self._platform_gaps),
            "visible_readiness_observations": deepcopy(self._visible_observations),
            "context": context_archive,
            "session_audit_replay": replay,
            "fleet": self.context.fleet.snapshot(),
            "communication": self.runtime.report(),
            "downlink": self.downlink.report(),
            "readiness": self.readiness.snapshot(),
            "bounded_incremental_execution": True,
            "synchronous_drive_to_completion_fallback": False,
            "business_arrival_contact_evidence_available": False,
            **CLOSED_VALIDATION_FLAGS,
        }


__all__ = ["RestaurantExecutionPump"]
