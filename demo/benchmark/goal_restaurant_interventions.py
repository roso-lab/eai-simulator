"""Trusted Restaurant observation trigger over the shared episode transport.

The observer is opt-in and evaluator-owned.  It fires once after a successful
native ``observe`` skill for one of the two Restaurant service regions.  The
backend fact and the already accepted skill receipt are published from the
producing robot to ``cloud`` through the episode's single
``CommunicationRuntime``.  Only a fresh message that the runtime actually
delivered is emitted to the Goal participant.

Lifecycle treatments use callbacks supplied by the native owner.  A callback
must return the post-mutation runtime state before the corresponding Goal
event is emitted.  In particular, ``member_join`` never creates a logical
member when no native callback can activate and read back a real reserve.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import math
from typing import Any

from .communication import CommunicationRuntime, validate_communication_report
from .goal_communication_tool import communication_message_observation
from .provenance import canonical_sha256


PROFILE_SCHEMA = "eai.benchmark.goal_restaurant_interventions"
REPORT_SCHEMA = "eai.benchmark.goal_restaurant_interventions_report"
VERSION = 1

OBSERVATION_SCHEMA = "eai.restaurant.readiness_service.v1"
OBSERVATION_FIELDS = frozenset({
    "schema", "episode_id", "kind", "region_id", "observation_id",
    "producer_id", "simulation_step", "time_s", "evidence_digest",
})
SERVICE_REGIONS = frozenset({"kitchen_service", "dining_service"})
COMMUNICATION_LEVELS = frozenset({
    "immediate", "delayed", "dropped", "bandwidth_limited", "stale",
})
TEAM_LEVELS = frozenset({
    "stable", "member_exit", "member_join", "capability_change",
})

LifecycleCallback = Callable[..., Mapping[str, Any]]


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _time(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite nonnegative number")
    return float(value)


def _runtime_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("lifecycle callback must return runtime_state")
    robots = value.get("robot_regions")
    objects = value.get("object_states")
    if not isinstance(robots, Mapping) or not isinstance(objects, Mapping):
        raise ValueError(
            "lifecycle runtime_state must contain robot_regions and object_states")
    if any(not isinstance(agent, str) or not isinstance(region, str)
           for agent, region in robots.items()):
        raise ValueError("lifecycle robot_regions must map string ids to regions")
    if any(not isinstance(object_id, str) or not isinstance(state, Mapping)
           for object_id, state in objects.items()):
        raise ValueError("lifecycle object_states must contain object mappings")
    return {
        "robot_regions": deepcopy(dict(robots)),
        "object_states": {
            object_id: deepcopy(dict(state)) for object_id, state in objects.items()
        },
    }


def _factor_profile(public_task: Mapping[str, Any]) -> dict[str, Any]:
    mission = public_task.get("mission")
    if not isinstance(mission, Mapping) or mission.get("scene_key") != "restaurant":
        raise ValueError("restaurant interventions require a Restaurant public task")
    factor = public_task.get("experiment_factor")
    if not isinstance(factor, Mapping):
        raise ValueError("public task requires experiment_factor")
    factor_id = factor.get("id")
    factor_level = factor.get("level")
    if factor_id == "communication_quality":
        if factor_level not in COMMUNICATION_LEVELS:
            raise ValueError("unsupported Restaurant communication factor level")
        communication_level, team_level = factor_level, "stable"
    elif factor_id == "team_continuity":
        if factor_level not in TEAM_LEVELS:
            raise ValueError("unsupported Restaurant team factor level")
        communication_level, team_level = "immediate", factor_level
    else:
        raise ValueError("unsupported Restaurant experiment factor")
    budgets = public_task.get("budgets")
    if not isinstance(budgets, Mapping):
        raise ValueError("public task requires budgets")
    message_budget = budgets.get("message_bytes")
    simulation_budget = budgets.get("simulation_s")
    if type(message_budget) is not int or message_budget <= 1:
        raise ValueError("budgets.message_bytes must be an integer greater than one")
    simulation_budget = _time(simulation_budget, "budgets.simulation_s")
    if simulation_budget <= 0:
        raise ValueError("budgets.simulation_s must be positive")

    treatment = {
        "operation": "deliver",
        "fixed_delay_s": 0.0,
        "active_drop": False,
        "bytes_per_window": message_budget,
        "window_s": 1.0,
        "publication_validity_s": 30.0,
    }
    if communication_level == "delayed":
        treatment["fixed_delay_s"] = 5.0
    elif communication_level == "dropped":
        treatment.update(operation="drop", active_drop=True)
    elif communication_level == "bandwidth_limited":
        treatment["bytes_per_window"] = min(64, message_budget - 1)
    elif communication_level == "stale":
        treatment.update(fixed_delay_s=5.0, publication_validity_s=1.0)

    return {
        "factor_id": factor_id,
        "factor_level": factor_level,
        "communication_level": communication_level,
        "team_level": team_level,
        "communication_treatment": treatment,
        "simulation_budget_s": simulation_budget,
    }


class GoalRestaurantInterventions:
    """One-shot Restaurant trigger and delivery/lifecycle observer."""

    def __init__(
        self,
        public_task: Mapping[str, Any],
        *,
        episode_id: str,
        runtime: CommunicationRuntime,
        lifecycle_callbacks: Mapping[str, LifecycleCallback] | None = None,
    ) -> None:
        if not isinstance(public_task, Mapping):
            raise ValueError("public_task must be a mapping")
        if not isinstance(runtime, CommunicationRuntime):
            raise ValueError("runtime must be a CommunicationRuntime")
        callbacks = dict(lifecycle_callbacks or {})
        unknown = set(callbacks) - (TEAM_LEVELS - {"stable"})
        if unknown or any(not callable(callback) for callback in callbacks.values()):
            raise ValueError("lifecycle_callbacks contains an unsupported callback")
        self._public_task = deepcopy(dict(public_task))
        self._public_task_sha256 = canonical_sha256(public_task)
        self.public_task_sha256 = self._public_task_sha256
        self.episode_id = _text(episode_id, "episode_id")
        self.runtime = runtime
        self._factor = _factor_profile(public_task)
        self._callbacks = callbacks
        self._message_id: str | None = None
        self._message_emitted = False
        self._trigger = {"status": "untaken", "reason": "no qualifying successful observation"}
        self._lifecycle = {
            "status": "not_applicable" if self._factor["team_level"] == "stable"
            else "pending",
            "event_type": (None if self._factor["team_level"] == "stable"
                           else self._factor["team_level"]),
        }
        self._delivery: dict[str, Any] = {
            "message_id": None,
            "status": "not_published",
            "published_at_s": None,
            "valid_until_s": None,
            "delivered_at_s": None,
            "emitted_at_s": None,
        }

        contract = runtime.report()["initial_contract"]
        pools = contract.get("pools") if isinstance(contract, Mapping) else None
        if not isinstance(pools, Mapping) or "backhaul_uplink" not in pools:
            raise ValueError(
                "restaurant interventions require a shared backhaul_uplink pool")

    def profile(self) -> dict[str, Any]:
        """Return the evaluator-private, task-bound treatment declaration."""
        return {
            "schema": PROFILE_SCHEMA,
            "version": VERSION,
            "visibility": "evaluator_only",
            "episode_id": self.episode_id,
            "public_task_sha256": self._public_task_sha256,
            "trigger": {
                "skill": "observe",
                "regions": sorted(SERVICE_REGIONS),
                "requires_successful_receipt": True,
                "fires_at_most_once": True,
            },
            "factor_id": self._factor["factor_id"],
            "factor_level": self._factor["factor_level"],
            "communication_level": self._factor["communication_level"],
            "team_level": self._factor["team_level"],
            "communication_treatment": deepcopy(
                self._factor["communication_treatment"]),
            "release_eligible": False,
        }

    @staticmethod
    def _report_session(host: Any) -> dict[str, Any]:
        report = host.trusted_report()
        session = report.get("session") if isinstance(report, Mapping) else None
        if not isinstance(session, Mapping):
            raise ValueError("restaurant observer host lacks a trusted session report")
        return deepcopy(dict(session))

    def _validated_observation(
        self,
        task: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        now: float,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not isinstance(task, Mapping) or not isinstance(result, Mapping):
            return None
        arguments = task.get("arguments")
        region = arguments.get("region_id") if isinstance(arguments, Mapping) else None
        if task.get("skill") != "observe" or region not in SERVICE_REGIONS:
            return None
        if result.get("status") != "succeeded":
            return None
        receipt = result.get("receipt")
        fact = result.get("trusted_observation")
        if not isinstance(receipt, Mapping) or not isinstance(fact, Mapping):
            raise ValueError(
                "successful Restaurant observation requires receipt and trusted_observation")
        receipt = deepcopy(dict(receipt))
        fact = deepcopy(dict(fact))
        if set(fact) != OBSERVATION_FIELDS:
            raise ValueError("trusted_observation fields do not match the readiness service row")
        if (fact["schema"] != OBSERVATION_SCHEMA
                or fact["kind"] != "native_observation"
                or fact["episode_id"] != self.episode_id
                or fact["region_id"] != region):
            raise ValueError("trusted_observation is not bound to this Restaurant trigger")
        assigned = task.get("assigned_agents")
        if (not isinstance(assigned, list) or not assigned
                or any(not isinstance(agent, str) or not agent for agent in assigned)
                or fact["producer_id"] not in assigned):
            raise ValueError("trusted_observation producer is outside the observing team")
        completed_at = _time(receipt.get("completed_at_s"), "receipt.completed_at_s")
        if (receipt.get("task_id") != task.get("task_id")
                or receipt.get("status") != "succeeded"
                or receipt.get("episode_id") != self.episode_id
                or result.get("completed_at_s") != receipt.get("completed_at_s")
                or completed_at != now):
            raise ValueError("Restaurant receipt is not bound to the completed observation")
        for key in ("receipt_ref", "backend_id", "action_id"):
            _text(receipt.get(key), f"receipt.{key}")
        evidence_refs = receipt.get("evidence_refs")
        if (not isinstance(evidence_refs, list) or not evidence_refs
                or len(evidence_refs) != len(set(evidence_refs))
                or any(not isinstance(ref, str) or not ref for ref in evidence_refs)):
            raise ValueError("receipt.evidence_refs must be distinct nonempty strings")
        _text(fact["observation_id"], "trusted_observation.observation_id")
        _text(fact["producer_id"], "trusted_observation.producer_id")
        _text(fact["evidence_digest"], "trusted_observation.evidence_digest")
        if (type(fact["simulation_step"]) is not int
                or fact["simulation_step"] < 0):
            raise ValueError("trusted_observation.simulation_step must be nonnegative")
        observed_at = _time(fact["time_s"], "trusted_observation.time_s")
        if observed_at > now:
            raise ValueError("trusted observation cannot postdate its receipt")
        return fact, receipt

    def _apply_communication_treatment(self, now: float) -> None:
        treatment = self._factor["communication_treatment"]
        self.runtime.set_conditions({
            # ``None`` makes bytes_per_window authoritative even when a base
            # execution profile declared another capacity spelling.
            "capacity_bps": None,
            "fixed_delay_s": treatment["fixed_delay_s"],
            "active_drop": treatment["active_drop"],
            "bytes_per_window": treatment["bytes_per_window"],
            "window_s": treatment["window_s"],
            "ttl_s": treatment["publication_validity_s"],
        }, pool="backhaul_uplink", time_s=now)

    def _publish(self, fact: Mapping[str, Any], receipt: Mapping[str, Any], now: float) -> None:
        self._apply_communication_treatment(now)
        message_id = (
            f"{self.episode_id}:restaurant-observation:{fact['observation_id']}")
        validity = min(
            now + self._factor["communication_treatment"]["publication_validity_s"],
            self._factor["simulation_budget_s"],
        )
        if validity <= now:
            raise ValueError("Restaurant observation was completed after its publish window")
        estimate = {
            "kind": "restaurant_service_observation",
            "region_id": fact["region_id"],
            "observation_id": fact["observation_id"],
            "producer_id": fact["producer_id"],
            "observed_at_s": float(fact["time_s"]),
            "simulation_step": fact["simulation_step"],
            "evidence_digest": fact["evidence_digest"],
            "receipt_ref": receipt["receipt_ref"],
            "receipt_evidence_refs": list(receipt["evidence_refs"]),
            "receipt_completed_at_s": float(receipt["completed_at_s"]),
        }
        record = self.runtime.publish(
            message_id=message_id,
            producer_id=f"robot:{fact['producer_id']}",
            recipients=["cloud"],
            channel="observation",
            estimate=estimate,
            valid_until_s=validity,
            published_at_s=now,
            pool="backhaul_uplink",
            direction="uplink",
            access_robot_id=fact["producer_id"],
            information_id=f"restaurant:{fact['region_id']}",
        )
        delivery = record["recipients"]["cloud"]
        self._message_id = message_id
        self._delivery.update({
            "message_id": message_id,
            "status": delivery["status"],
            "published_at_s": now,
            "valid_until_s": validity,
        })

    @staticmethod
    def _selected_relay_member(session: Mapping[str, Any]) -> str:
        current = set(session.get("current_agents") or ())
        selected = set(session.get("selected_team") or ())
        declarations = session.get("current_robot_declarations")
        if not isinstance(declarations, Mapping):
            raise ValueError("trusted session lacks current robot declarations")
        eligible = sorted(
            agent for agent in current & selected
            if agent in declarations
            and "relay" in (declarations[agent].get("public_capabilities") or ())
        )
        if not eligible:
            raise ValueError("no selected active relay-capable member is available")
        return eligible[0]

    def _join_declaration(self) -> dict[str, Any]:
        pool = self._public_task.get("candidate_robot_pool") or ()
        candidates = sorted(
            (deepcopy(dict(row)) for row in pool
             if isinstance(row, Mapping)
             and row.get("mobility") == "ground"
             and "relay" in (row.get("public_capabilities") or ())),
            key=lambda row: row.get("id", ""),
        )
        if not candidates:
            raise ValueError("Restaurant task has no relay-capable join template")
        declaration = candidates[0]
        declaration["id"] = "restaurant_hidden_joiner_1"
        return declaration

    def _apply_lifecycle(self, *, host: Any, now: float) -> None:
        level = self._factor["team_level"]
        if level == "stable":
            return
        callback = self._callbacks.get(level)
        if callback is None:
            self._lifecycle.update({
                "status": "unsupported",
                "failure_class": "infrastructure_failure",
                "reason": f"native {level} apply callback is not configured",
                "applied_at_s": None,
            })
            return

        session = self._report_session(host)
        if level == "member_join":
            declaration = self._join_declaration()
            agent = declaration["id"]
            capability_version = 1
        else:
            agent = self._selected_relay_member(session)
            declaration = deepcopy(session["current_robot_declarations"][agent])
            capability_version = int(
                (session.get("capability_versions") or {}).get(agent, 0)) + 1
            if level == "capability_change":
                capabilities = list(declaration.get("public_capabilities") or ())
                if "relay" not in capabilities:
                    raise ValueError("selected capability target no longer has relay")
                declaration["public_capabilities"] = [
                    capability for capability in capabilities if capability != "relay"]

        native_event = {
            "id": f"{self.episode_id}:restaurant:{level}",
            "kind": level,
            "time_s": now,
            "agent": agent,
            "capability_version": capability_version,
            "capability_declaration": (
                deepcopy(declaration) if level != "member_exit" else None),
        }
        try:
            callback_result = callback(
                deepcopy(native_event), trusted_report={"session": session})
            if not isinstance(callback_result, Mapping):
                raise ValueError("native lifecycle callback must return a mapping")
            state = _runtime_state(callback_result.get("runtime_state"))
            robot_regions = state["robot_regions"]
            if level == "member_exit" and agent in robot_regions:
                raise ValueError("member_exit runtime_state still contains the exited member")
            if level != "member_exit" and agent not in robot_regions:
                raise ValueError(f"{level} runtime_state omits the affected member")

            payload = {"agent_id": agent}
            if level != "member_exit":
                payload.update({
                    "capability_declaration": deepcopy(declaration),
                    "capability_version": capability_version,
                })
            host.emit(level, now, payload)
            host.update_runtime_state(
                now, state["robot_regions"], state["object_states"])
            self._lifecycle.update({
                "status": "applied",
                "agent_id": agent,
                "capability_version": capability_version,
                "applied_at_s": now,
                "native_evidence": deepcopy(callback_result.get("evidence")),
            })
        except Exception as exc:
            self._lifecycle.update({
                "status": "failed",
                "failure_class": "infrastructure_failure",
                "reason": f"{type(exc).__name__}: {exc}",
                "agent_id": agent,
                "applied_at_s": None,
            })

    def after_skill(
        self,
        task: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        host: Any,
    ) -> None:
        """Take the one-shot trigger after trusted completion and state update."""
        if self._trigger["status"] == "fired":
            return
        session = self._report_session(host)
        now = _time(session.get("current_time_s"), "session.current_time_s")
        validated = self._validated_observation(task, result, now=now)
        if validated is None:
            return
        fact, receipt = validated
        self._trigger = {
            "status": "fired",
            "task_id": task["task_id"],
            "region_id": fact["region_id"],
            "producer_id": fact["producer_id"],
            "observation_id": fact["observation_id"],
            "observation_time_s": float(fact["time_s"]),
            "receipt_ref": receipt["receipt_ref"],
            "receipt_evidence_refs": list(receipt["evidence_refs"]),
            "receipt_completed_at_s": float(receipt["completed_at_s"]),
            "fired_at_s": now,
        }
        try:
            self._publish(fact, receipt, now)
            # Immediate delivery is emitted before a same-tick lifecycle event;
            # delayed/dropped/stale messages remain governed by transport.
            self.advance(host=host)
            self._apply_lifecycle(host=host, now=now)
        except Exception as exc:
            self._delivery["status"] = "failed"
            self._trigger.update({
                "intervention_status": "failed",
                "failure_class": "infrastructure_failure",
                "reason": f"{type(exc).__name__}: {exc}",
            })
            raise
        self._trigger["intervention_status"] = (
            "unsupported" if self._lifecycle["status"] == "unsupported"
            else "failed" if self._lifecycle["status"] == "failed"
            else "applied")

    def advance(self, *, host: Any) -> None:
        """Advance transport and emit only a delivered, still-fresh own message."""
        session = self._report_session(host)
        now = _time(session.get("current_time_s"), "session.current_time_s")
        self.runtime.advance(now)
        if self._message_id is None or self._message_emitted:
            return
        for payload in self.runtime.public_messages(recipient="cloud"):
            if payload["message_id"] != self._message_id:
                continue
            event_payload = communication_message_observation(
                payload, recipient_id="cloud",
                effective_valid_until_s=self._delivery["valid_until_s"])
            host.emit("observation", now, event_payload)
            self._message_emitted = True
            transport = self.runtime.report()["messages"][self._message_id]
            delivered = transport["recipients"]["cloud"].get("delivered_at_s")
            self._delivery.update({
                "status": "delivered",
                "delivered_at_s": delivered,
                "emitted_at_s": now,
            })
            return
        transport = self.runtime.report()["messages"].get(self._message_id)
        if transport is not None:
            delivery = transport["recipients"]["cloud"]
            self._delivery["status"] = delivery["status"]

    def has_pending_work(self) -> bool:
        if self._message_id is None or self._message_emitted:
            return False
        message = self.runtime.report()["messages"].get(self._message_id)
        if not isinstance(message, Mapping):
            return False
        status = message["recipients"]["cloud"]["status"]
        return status in {"queued", "in_transit"}

    def next_wakeup_s(self) -> float | None:
        return self.runtime.next_event_time_s() if self.has_pending_work() else None

    def report(self) -> dict[str, Any]:
        """Return evaluator-only causal evidence; never participant content."""
        if self._message_id is not None:
            message = self.runtime.report()["messages"].get(self._message_id)
            if isinstance(message, Mapping):
                delivery = message["recipients"]["cloud"]
                if not self._message_emitted:
                    self._delivery["status"] = delivery["status"]
                self._delivery["delivered_at_s"] = delivery.get("delivered_at_s")
        errors: list[dict[str, str]] = []
        if self._trigger["status"] != "fired":
            errors.append({
                "code": "trigger_untaken",
                "reason": "no qualifying successful Restaurant observation fired",
            })
        elif self._trigger.get("intervention_status") != "applied":
            errors.append({
                "code": "intervention_not_applied",
                "reason": str(self._trigger.get(
                    "reason", self._lifecycle.get(
                        "reason", "intervention did not apply"))),
            })
        expected_delivery = (
            "dropped" if self._factor["communication_level"] == "dropped"
            else "expired" if self._factor["communication_level"] == "stale"
            else "delivered")
        if self._trigger["status"] == "fired" and self._delivery["status"] != expected_delivery:
            errors.append({
                "code": ("communication_unresolved"
                         if self._delivery["status"] in {"queued", "in_transit"}
                         else "communication_treatment_mismatch"),
                "reason": (
                    f"expected {expected_delivery} communication outcome, got "
                    f"{self._delivery['status']}"),
            })
        measurement_valid = not errors
        return {
            "schema": REPORT_SCHEMA,
            "version": VERSION,
            "visibility": "evaluator_only",
            "episode_id": self.episode_id,
            "public_task_sha256": self._public_task_sha256,
            "profile": self.profile(),
            "trigger": deepcopy(self._trigger),
            "delivery": deepcopy(self._delivery),
            "lifecycle": deepcopy(self._lifecycle),
            "intervention_valid": measurement_valid,
            "measurement_valid": measurement_valid,
            "errors": errors,
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
            "release_eligible": False,
        }


def _native_observation_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("native_observations must be a list")
    rows: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("native observation evidence must contain mappings")
        record = deepcopy(dict(raw))
        if record.get("kind") == "observation":
            service = record.get("service_record")
            if not isinstance(service, Mapping):
                raise ValueError("native observation lacks a readiness service record")
            fact = deepcopy(dict(service))
            task_id = _text(record.get("task_id"), "native observation task_id")
            evidence_ref = _text(
                record.get("evidence_ref"), "native observation evidence_ref")
            if (record.get("region_id") != fact.get("region_id")
                    or record.get("producer_id") != fact.get("producer_id")):
                raise ValueError("native observation wrapper differs from its service fact")
        elif record.get("kind") == "native_observation":
            fact = record
            task_id = record.get("task_id")
            evidence_ref = record.get("evidence_ref")
        else:
            continue
        if set(fact) != OBSERVATION_FIELDS:
            raise ValueError("native readiness fact fields are invalid")
        if (fact.get("schema") != OBSERVATION_SCHEMA
                or fact.get("kind") != "native_observation"
                or fact.get("region_id") not in SERVICE_REGIONS):
            raise ValueError("native readiness fact is invalid")
        observed_at = _time(fact.get("time_s"), "native observation time_s")
        step = fact.get("simulation_step")
        if type(step) is not int or step < 0:
            raise ValueError("native observation simulation_step must be nonnegative")
        rows.append({
            "fact": fact,
            "task_id": task_id,
            "evidence_ref": evidence_ref,
            "sort_key": (observed_at, step, str(fact.get("observation_id"))),
        })
    rows.sort(key=lambda row: row["sort_key"])
    return rows


def verify_restaurant_intervention_report(
    report: Mapping[str, Any],
    *,
    public_task: Mapping[str, Any],
    episode_id: str,
    transport_report: Mapping[str, Any],
    native_observations: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind an archived observer report to transport replay and native facts.

    This verifier does not turn contract evidence into release evidence.  It
    independently selects the first timestamped native service observation,
    then checks that the trigger and the robot-to-cloud wire payload carry that
    same fact and its native evidence reference.  ``measurement_valid`` stays
    false for an untaken, unresolved, failed, or unsupported treatment.
    """
    if not isinstance(report, Mapping):
        raise ValueError("restaurant intervention report must be a mapping")
    report = deepcopy(dict(report))
    episode_id = _text(episode_id, "episode_id")
    factor = _factor_profile(public_task)
    public_digest = canonical_sha256(public_task)
    if (report.get("schema") != REPORT_SCHEMA or report.get("version") != VERSION
            or report.get("visibility") != "evaluator_only"
            or report.get("episode_id") != episode_id
            or report.get("public_task_sha256") != public_digest):
        raise ValueError("restaurant intervention report binding is invalid")
    if any(report.get(flag) is not False for flag in (
            "execution_validated", "business_outcomes_validated",
            "scoreable", "release_eligible")):
        raise ValueError("restaurant intervention release flags must remain false")
    profile = report.get("profile")
    if (not isinstance(profile, Mapping)
            or profile.get("schema") != PROFILE_SCHEMA
            or profile.get("version") != VERSION
            or profile.get("visibility") != "evaluator_only"
            or profile.get("episode_id") != episode_id
            or profile.get("public_task_sha256") != public_digest
            or profile.get("factor_id") != factor["factor_id"]
            or profile.get("factor_level") != factor["factor_level"]
            or profile.get("communication_level") != factor["communication_level"]
            or profile.get("team_level") != factor["team_level"]
            or profile.get("communication_treatment")
            != factor["communication_treatment"]):
        raise ValueError("restaurant intervention profile differs from the public factor")

    replay = validate_communication_report(transport_report)
    if replay.get("validated") is not True:
        raise ValueError("restaurant intervention transport report failed replay")
    messages = transport_report.get("messages")
    ledger = transport_report.get("ledger")
    if not isinstance(messages, Mapping) or not isinstance(ledger, list):
        raise ValueError("restaurant intervention transport archive is incomplete")
    native = _native_observation_rows(native_observations)
    trigger = report.get("trigger")
    delivery_report = report.get("delivery")
    lifecycle = report.get("lifecycle")
    errors = report.get("errors")
    if (not isinstance(trigger, Mapping) or not isinstance(delivery_report, Mapping)
            or not isinstance(lifecycle, Mapping) or not isinstance(errors, list)):
        raise ValueError("restaurant intervention causal report is incomplete")

    if not native:
        if (trigger.get("status") != "untaken"
                or delivery_report.get("status") != "not_published"
                or report.get("measurement_valid") is not False
                or report.get("intervention_valid") is not False
                or not any(isinstance(row, Mapping)
                           and row.get("code") == "trigger_untaken"
                           for row in errors)):
            raise ValueError("untaken Restaurant trigger is not reported fail-closed")
        return {
            "schema": "eai.benchmark.goal_restaurant_intervention_verification",
            "version": VERSION,
            "validated": True,
            "measurement_valid": False,
            "trigger_status": "untaken",
            "release_eligible": False,
        }

    first = native[0]
    fact = first["fact"]
    if trigger.get("status") != "fired":
        raise ValueError("a native Restaurant observation did not fire the trigger")
    comparisons = {
        "task_id": first["task_id"],
        "region_id": fact["region_id"],
        "producer_id": fact["producer_id"],
        "observation_id": fact["observation_id"],
        "observation_time_s": float(fact["time_s"]),
    }
    if any(trigger.get(key) != value for key, value in comparisons.items()):
        raise ValueError("Restaurant trigger differs from the first native observation")
    if (first["evidence_ref"] is not None
            and first["evidence_ref"] not in trigger.get("receipt_evidence_refs", ())):
        raise ValueError("Restaurant trigger receipt omits native observation evidence")

    message_id = delivery_report.get("message_id")
    message = messages.get(message_id) if isinstance(message_id, str) else None
    if not isinstance(message, Mapping):
        raise ValueError("Restaurant trigger message is absent from transport")
    payload = message.get("payload")
    recipients = message.get("recipients")
    cloud = recipients.get("cloud") if isinstance(recipients, Mapping) else None
    if (not isinstance(payload, Mapping) or not isinstance(cloud, Mapping)
            or set(recipients) != {"cloud"}
            or message.get("channel") != "observation"
            or message.get("direction") != "uplink"
            or payload.get("producer_id") != f"robot:{fact['producer_id']}"
            or payload.get("information_id") != f"restaurant:{fact['region_id']}"):
        raise ValueError("Restaurant observation did not use the robot-to-cloud uplink")
    estimate = payload.get("estimate")
    expected_estimate = {
        "kind": "restaurant_service_observation",
        "region_id": fact["region_id"],
        "observation_id": fact["observation_id"],
        "producer_id": fact["producer_id"],
        "observed_at_s": float(fact["time_s"]),
        "simulation_step": fact["simulation_step"],
        "evidence_digest": fact["evidence_digest"],
        "receipt_ref": trigger.get("receipt_ref"),
        "receipt_evidence_refs": trigger.get("receipt_evidence_refs"),
        "receipt_completed_at_s": trigger.get("receipt_completed_at_s"),
    }
    if estimate != expected_estimate:
        raise ValueError("Restaurant uplink payload differs from native evidence")
    final_status = cloud.get("status")
    archived_status = delivery_report.get("status")
    emitted_at = delivery_report.get("emitted_at_s")
    historical_delivery = [
        row for row in ledger
        if row.get("event") == "delivered"
        and row.get("message_id") == message_id
        and row.get("recipient") == "cloud"
    ]
    if (payload.get("published_at_s") != trigger.get("fired_at_s")
            or payload.get("published_at_s") != trigger.get("receipt_completed_at_s")
            or payload.get("valid_until_s") != delivery_report.get("valid_until_s")
            or delivery_report.get("published_at_s") != payload.get("published_at_s")
            or delivery_report.get("delivered_at_s") != cloud.get("delivered_at_s")):
        raise ValueError("Restaurant message timestamps differ from transport")
    # CommunicationRuntime eventually marks even a delivered copy expired at
    # TTL.  Preserve the participant-visible historical delivery separately:
    # an earlier fresh emission stays valid after the episode clock passes TTL.
    if emitted_at is None:
        if archived_status != final_status:
            raise ValueError("unemitted Restaurant delivery status differs from transport")
    elif (archived_status != "delivered"
            or final_status not in {"delivered", "expired"}
            or len(historical_delivery) != 1
            or historical_delivery[0].get("time_s")
            != delivery_report.get("delivered_at_s")
            or not delivery_report["delivered_at_s"] <= emitted_at
            < delivery_report["valid_until_s"]):
        raise ValueError("Restaurant historical delivery/emission evidence is invalid")

    treatment = factor["communication_treatment"]
    expected_patch = {
        "capacity_bps": None,
        "fixed_delay_s": treatment["fixed_delay_s"],
        "active_drop": treatment["active_drop"],
        "bytes_per_window": treatment["bytes_per_window"],
        "window_s": treatment["window_s"],
        "ttl_s": treatment["publication_validity_s"],
    }
    published_rows = [row for row in ledger
                      if row.get("event") == "published"
                      and row.get("message_id") == message_id]
    changes = [row for row in ledger
               if row.get("event") == "channel_condition_changed"
               and row.get("pool") == "backhaul_uplink"
               and row.get("time_s") == payload.get("published_at_s")]
    if (len(published_rows) != 1
            or published_rows[0].get("access_robot_id") != fact["producer_id"]
            or not changes
            or changes[-1].get("conditions") != expected_patch
            or changes[-1]["sequence"] >= published_rows[0]["sequence"]):
        raise ValueError("Restaurant factor treatment was not applied before publication")

    expected_delivery = (
        "dropped" if factor["communication_level"] == "dropped"
        else "expired" if factor["communication_level"] == "stale"
        else "delivered")
    lifecycle_ok = (
        lifecycle.get("status") == "not_applicable"
        if factor["team_level"] == "stable"
        else lifecycle.get("status") == "applied")
    if expected_delivery == "delivered":
        communication_ok = (
            archived_status == "delivered"
            and emitted_at is not None
            and len(historical_delivery) == 1)
    else:
        communication_ok = (
            archived_status == expected_delivery
            and final_status == expected_delivery
            and emitted_at is None)
    measurement_valid = (
        communication_ok
        and trigger.get("intervention_status") == "applied"
        and lifecycle_ok
        and errors == [])
    if (report.get("measurement_valid") is not measurement_valid
            or report.get("intervention_valid") is not measurement_valid):
        raise ValueError("Restaurant measurement validity contradicts causal evidence")
    if expected_delivery != "delivered" and emitted_at is not None:
        raise ValueError("undelivered Restaurant observation became participant-visible")
    return {
        "schema": "eai.benchmark.goal_restaurant_intervention_verification",
        "version": VERSION,
        "validated": True,
        "measurement_valid": measurement_valid,
        "trigger_status": "fired",
        "message_id": message_id,
        "native_observation_id": fact["observation_id"],
        "release_eligible": False,
    }


__all__ = [
    "GoalRestaurantInterventions",
    "OBSERVATION_FIELDS",
    "PROFILE_SCHEMA",
    "REPORT_SCHEMA",
    "VERSION",
    "verify_restaurant_intervention_report",
]
