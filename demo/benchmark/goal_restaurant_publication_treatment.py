"""Opt-in evaluator hook for one prospective cross-mobility observation.

The participant chooses the plan. A direct prerequisite edge supplies only a
prospective information consumer; neither later consumption nor success is a
trigger input. No policy, consumer identities or hidden schedule enter the wire.
This extension executes the frozen event through versioned shared FIFO/window
semantics; it does not qualify a native experiment or calibrate link capacity.
"""

from collections.abc import Mapping
from copy import deepcopy
import math

from .communication import validate_communication_report
from .goal_restaurant_conditions import build_restaurant_task_conditions
from .goal_session_audit import replay_goal_session_audit
from .provenance import canonical_sha256


SCHEMA = "eai.restaurant.publication_treatment_hook"
FLAGS = {"scoreable": False, "execution_validated": False,
         "business_outcomes_validated": False, "task_exact_communication_treatment_validated": False}


def _sealed(value):
    row = deepcopy(value)
    row["decision_sha256"] = canonical_sha256(row)
    return row


def _verify_physical(record, *, episode_id, task_id, producer, time_s):
    if not isinstance(record, Mapping):
        raise ValueError("physical observation record is missing")
    digest = canonical_sha256({k: v for k, v in record.items() if k != "evidence_ref"})
    if record.get("evidence_ref") != "sha256:" + digest:
        raise ValueError("physical observation record digest differs")
    if (record.get("episode_id") != episode_id or record.get("task_id") != task_id
            or record.get("physical_terminal_at_s") != time_s
            or record.get("status") != "succeeded"
            or record.get("disposition") != "native_ground_readiness_observation"
            or record.get("task", {}).get("skill") != "observe"
            or record["task"].get("assigned_agents") != [producer]):
        raise ValueError("physical observation identity differs")


class RestaurantPublicationTreatment:
    """Host-owned one-shot policy, bound before the first physical tick."""

    def __init__(self, *, public_task, hidden_evaluator, communication_contract,
                 deployment_companion=None):
        self.conditions = build_restaurant_task_conditions(
            public_task=public_task, hidden_evaluator=hidden_evaluator,
            communication_contract=communication_contract,
            deployment_companion=deployment_companion)
        self.public_task = deepcopy(public_task)
        self._used = False
        self._decisions = []

    def select(self, *, message_id, producer_id, task_id, published_at_s,
               physical_record, audit_journal, readiness_receipt):
        """Called immediately before the real result publication, never afterwards."""
        if (isinstance(published_at_s, bool) or not isinstance(published_at_s, (int, float))
                or not math.isfinite(published_at_s) or published_at_s < 0):
            raise ValueError("publication time must be finite and nonnegative")
        session = audit_journal["final_report"]
        if (audit_journal["public_task_sha256"] != canonical_sha256(self.public_task)
                or session["current_time_s"] != published_at_s):
            raise ValueError("observation requires the current task and publication-time session")
        _verify_physical(physical_record, episode_id=session["episode_id"], task_id=task_id,
                         producer=producer_id, time_s=published_at_s)
        source = session["tasks"].get(task_id)
        if (source is None or source["status"] != "pending" or source["invalidated_by"]
                or source["plan_id"] != session["current_plan_id"]
                or source["plan_version"] != session["plan_version"]
                or source["task"] != physical_record["task"]
                or source["dispatch_action_id"] != physical_record["dispatch_action_id"]):
            raise ValueError("observation is not bound to its accepted dispatch")
        plan = next((row for row in session["plans"]
                     if row["plan"]["plan_id"] == session["current_plan_id"]
                     and row["version"] == session["plan_version"]), None)
        mobility = self.conditions["agent_mobility"]
        if producer_id not in mobility:
            raise ValueError("observation producer has no declared mobility")
        consumers = []
        if plan is not None:
            for task in plan["plan"]["tasks"]:
                state = session["tasks"].get(task["task_id"])
                if (task_id in task["prerequisites"] and state
                        and state["plan_id"] == session["current_plan_id"]
                        and state["plan_version"] == session["plan_version"]
                        and state["status"] == "scheduled" and not state["invalidated_by"]):
                    for agent in task["assigned_agents"]:
                        if agent not in mobility:
                            raise ValueError("prospective consumer has no declared mobility")
                        if agent in session["current_agents"] and mobility[agent] != mobility[producer_id]:
                            consumers.append({"task_id": task["task_id"], "agent_id": agent})
        consumers.sort(key=lambda row: (row["task_id"], row["agent_id"]))
        commit = None
        if readiness_receipt is not None:
            if (readiness_receipt.get("episode_id") != session["episode_id"]
                    or not isinstance(readiness_receipt.get("time_s"), (int, float))
                    or isinstance(readiness_receipt["time_s"], bool)
                    or not math.isfinite(readiness_receipt["time_s"])
                    or not 0 <= readiness_receipt["time_s"] <= published_at_s):
                raise ValueError("business receipt is foreign, invalid or from the future")
            commit = readiness_receipt["time_s"]
        reason = ("first_trigger_already_selected" if self._used else
                  "business_commit_already_exists" if commit is not None else
                  "no_prospective_cross_mobility_consumer" if not consumers else "selected")
        treatment = None
        if reason == "selected":
            frozen = self.conditions["treatment"]
            treatment = {"schema": "eai.benchmark.communication_publication_treatment",
                         "version": 1, "semantics": "shared_fifo_anchored_window_v1",
                         "binding_sha256": self.conditions["binding_sha256"],
                         "event_id": frozen["id"], "trigger": frozen["trigger"],
                         **{key: frozen[key] for key in (
                             "operation", "delay_s", "drop", "bytes_per_window", "window_s",
                             "publication_validity_s")}}
            self._used = True
        decision = _sealed({
            "message_id": message_id, "episode_id": session["episode_id"],
            "producer_id": producer_id, "task_id": task_id, "published_at_s": published_at_s,
            "source_record_ref": physical_record["evidence_ref"],
            "plan_id": session["current_plan_id"], "plan_version": session["plan_version"],
            "prospective_consumers": consumers, "consumer_semantics": "accepted_direct_prerequisite_intent",
            "actual_consumption_inferred": False, "reason": reason,
            "publication_treatment": treatment, "audit_prefix": deepcopy(audit_journal),
            "readiness_receipt_at_selection": deepcopy(readiness_receipt), **FLAGS})
        self._decisions.append(decision)
        return deepcopy(treatment)

    def report(self):
        return {"schema": SCHEMA, "version": 1, "conditions": deepcopy(self.conditions),
                "decisions": deepcopy(self._decisions), "selected_count": int(self._used),
                "source_scope": "host_inputs_require_archive_replay_not_native_authentication", **FLAGS}


def verify_restaurant_publication_treatment(*, pump_report, public_task, participant_api,
                                          hidden_evaluator, communication_contract,
                                          deployment_companion=None):
    """Rebuild causal selections from audited prefixes and actual wire publications.

    This verifies archive consistency and application, not physical provenance,
    formal treatment qualification, or actual downstream message consumption.
    """
    archive = pump_report["publication_treatment"]
    communication = pump_report["communication"]
    replay = validate_communication_report(
        communication, expected_initial_contract=communication_contract)
    if not replay["validated"]:
        raise ValueError("communication treatment replay failed")
    hook = RestaurantPublicationTreatment(
        public_task=public_task, hidden_evaluator=hidden_evaluator,
        communication_contract=communication_contract,
        deployment_companion=deployment_companion)
    full_audit = pump_report["context"]["session_audit"]
    if any(row["event"] in {"channel_conditions_changed", "channel_condition_changed",
                            "contract_updated"} for row in communication["ledger"]):
        raise ValueError("publication treatment archive contains global condition changes")
    if not replay_goal_session_audit(full_audit, public_task, participant_api)["validated"]:
        raise ValueError("full session audit replay failed")
    physical = {row["evidence_ref"]: row for row in pump_report["physical_records"]}
    published = {row["message_id"]: row for row in communication["ledger"]
                 if row["event"] == "published"}
    seen = set()
    previous_calls = 0
    receipt = pump_report["readiness"]["readiness_service"]["receipt"]
    outcomes = []
    for decision in archive["decisions"]:
        prefix = decision["audit_prefix"]
        count = len(prefix["calls"])
        if (count < previous_calls or prefix["calls"] != full_audit["calls"][:count]
                or not replay_goal_session_audit(prefix, public_task, participant_api)["validated"]):
            raise ValueError("selection does not bind a chronological audited prefix")
        previous_calls = count
        message_id = decision["message_id"]
        if message_id in seen:
            raise ValueError("duplicate publication decision")
        seen.add(message_id)
        source = physical[decision["source_record_ref"]]
        message = communication["messages"][message_id]
        wire = message["payload"]
        if (wire["published_at_s"] != decision["published_at_s"]
                or wire["producer_id"] != decision["producer_id"]
                or message["channel"] != "goal_execution_result"
                or wire["estimate"]["source_record_ref"] != source["evidence_ref"]
                or wire["estimate"]["task_id"] != decision["task_id"]):
            raise ValueError("treatment does not join the actual observation publication")
        expected_receipt = (receipt if receipt and receipt["time_s"] <= decision["published_at_s"] else None)
        if decision["readiness_receipt_at_selection"] != expected_receipt:
            raise ValueError("business commit boundary differs from native service archive")
        treatment = hook.select(
            message_id=message_id, producer_id=decision["producer_id"], task_id=decision["task_id"],
            published_at_s=decision["published_at_s"], physical_record=source,
            audit_journal=prefix, readiness_receipt=expected_receipt)
        if published[message_id].get("publication_treatment") != treatment:
            raise ValueError("selected treatment differs from transport application")
        if treatment is not None:
            delivery = message["recipients"]["trusted_execution_host"]
            outcomes.append({"message_id": message_id, "status": delivery["status"],
                             "drop_reason": delivery.get("drop_reason"),
                             "bytes_sent": delivery["bytes_sent"],
                             "delivered_at_s": delivery["delivered_at_s"]})
    expected_ids = {mid for mid, message in communication["messages"].items()
                    if message["channel"] == "goal_execution_result"
                    and message["payload"]["estimate"].get("status") == "succeeded"
                    and message["payload"]["estimate"].get("participant_observation") is not None}
    treated_ids = {mid for mid, row in published.items() if row.get("publication_treatment") is not None}
    if seen != expected_ids or treated_ids != {row["message_id"] for row in outcomes}:
        raise ValueError("observation selection coverage or targeted publication set differs")
    if hook.report() != archive:
        raise ValueError("treatment decisions differ from independent reconstruction")
    return {"schema": "eai.restaurant.publication_treatment_review", "version": 1,
            "validated": True, "application_count": len(outcomes), "outcomes": outcomes,
            "binding_status": "bound", "trigger_status": "triggered" if outcomes else "not_triggered",
            "application_status": "applied" if outcomes else "not_applied",
            "effect_status": "unverified" if outcomes else "not_observed",
            "verified_effect": False,
            "integrity_scope": "causal_archive_consistency_not_native_authentication",
            "formal_statistics_eligible": False, **FLAGS}
