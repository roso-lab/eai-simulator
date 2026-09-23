"""Private finite-state remaining-task oracle with preregistered measurements.

Reachability is exact for the supplied bounded model. Its correspondence to
native terminal predicates and legal skills must be independently certified;
a successful graph search alone is not a physical validation certificate.
No participant decision, witness plan, allocation or execution outcome enters
the query. Failed grounding/model checks are infrastructure failures, never a
third feasibility label.
"""

from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from itertools import permutations
from typing import Any

from .provenance import canonical_sha256


def _names(value, field):
    if (not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))):
        raise ValueError(f"{field} must contain distinct nonempty strings")
    return set(value)


def _amounts(value, field):
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not key or type(amount) is not int or amount < 0
        for key, amount in value.items()
    ):
        raise ValueError(f"{field} must contain nonnegative integer resource units")
    return dict(value)


def _failed_result(*, episode_id: str, measurement_id: str | None,
                   assessment_id: str, assessment_epoch: int,
                   quality_failure: str) -> dict[str, Any]:
    """Build the evaluator-only platform failure shape.

    Native reader gaps are evidence failures, never a third feasibility label.
    Keeping this constructor shared by the query and reader adapter also makes
    malformed provider output deterministic in the archived evidence report.
    """
    return {
        "assessment_id": assessment_id,
        "assessment_epoch": assessment_epoch,
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": [],
        "evaluator_ref": "remaining_task_reachability_v1",
        "episode_id": episode_id,
        "measurement_id": measurement_id,
        "visibility": "evaluator_only",
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "quality_failure": quality_failure,
    }


def validate_oracle_model(model: Mapping[str, Any]) -> str:
    """Validate a finite, closed transition model; return its immutable hash."""
    facts = _names(model["fact_ids"], "fact_ids")
    terminal = _names(model["terminal_all"], "terminal_all")
    if not terminal or not terminal <= facts:
        raise ValueError("terminal predicates must be a nonempty subset of facts")
    resources = _amounts(model["resource_bounds"], "resource_bounds")
    seen = set()
    for transition in model["transitions"]:
        name = transition["id"]
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("transitions must have unique nonempty ids")
        seen.add(name)
        for field in ("requires_all", "requires_absent", "adds", "deletes"):
            if not _names(transition[field], field) <= facts:
                raise ValueError("transition references an unknown predicate")
        if set(transition["adds"]) & set(transition["deletes"]):
            raise ValueError("a transition cannot add and delete the same predicate")
        costs = _amounts(transition["resource_costs"], "resource_costs")
        if not set(costs) <= set(resources):
            raise ValueError("transition references an unknown resource")
        for slot in transition["capability_slots"]:
            _names(slot["requires_all"], "slot requires_all")
            payload = slot.get("min_payload_kg", 0)
            if type(payload) not in (int, float) or not 0 <= payload < float("inf"):
                raise ValueError("slot payload must be finite and nonnegative")
    return canonical_sha256(model)


def freeze_oracle_registration(
    model: Mapping[str, Any], *, task_sha256: str, measurement_points: list[Mapping[str, Any]],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze model, state domains and validation before participant execution.

    A measurement point fixes a predicate partition and finite resource domain.
    State samples may arrive at runtime, but coverage/quality rules cannot be
    chosen after seeing a participant declaration. Native certification is an
    external input; authoring fixtures must use a non-native validation scope.
    """
    model_hash = validate_oracle_model(model)
    points = deepcopy(measurement_points)
    ids = set()
    facts = set(model["fact_ids"])
    for point in points:
        if not isinstance(point.get("id"), str) or not point["id"] or point["id"] in ids:
            raise ValueError("measurement points require distinct ids")
        ids.add(point["id"])
        if point["trigger"] not in {"initial_deployment", "member_exit", "capability_change", "milestone_state_change"}:
            raise ValueError("unsupported formal measurement trigger")
        required = _names(point["required_true"], "required_true")
        absent = _names(point["required_false"], "required_false")
        varying = _names(point["varying_facts"], "varying_facts")
        if required & absent or required & varying or absent & varying or required | absent | varying != facts:
            raise ValueError("measurement point must partition every model predicate")
    if not points or not task_sha256:
        raise ValueError("registration requires task identity and measurement points")
    if validation.get("status") not in {"verified", "failed"}:
        raise ValueError("model validation status must be verified or failed")
    if validation["status"] == "verified" and (not validation.get("evidence_refs") or not validation.get("scope")):
        raise ValueError("verified model needs an independent validity certificate and scope")
    result = {"schema": "eai.benchmark.remaining_feasibility_registration", "version": 1,
              "visibility": "evaluator_only", "task_sha256": task_sha256,
              "model": deepcopy(model), "model_sha256": model_hash,
              "measurement_points": points, "validation": deepcopy(validation)}
    result["registration_sha256"] = canonical_sha256(result)
    return result


def _can_staff(slots, robots, max_active_agents):
    if len(slots) > max_active_agents:
        return False
    return any(all(
        set(slot["requires_all"]) <= set(robot["capabilities"])
        and slot.get("min_payload_kg", 0) <= robot["payload_kg"]
        for slot, robot in zip(slots, assigned)
    ) for assigned in permutations(robots, len(slots)))


def query_remaining_feasibility(
    registration: Mapping[str, Any], *, measurement_id: str,
    episode_id: str, assessment_id: str, assessment_epoch: int,
    task_sha256: str, state: Mapping[str, Any],
    grounding: Mapping[str, Any], max_states: int = 100000,
) -> dict[str, Any]:
    """Return verified binary truth or a separate deterministic quality failure.

    ``state`` contains trusted predicates, available robots, bounded resources
    and pool versions. Native grounding must prove each predicate's truth and
    identify the simulation time. The finite search explores all legal action
    alternatives, including serialization and alternative teams; it returns
    no solution plan to either the caller or participant.
    """
    result = _failed_result(
        episode_id=episode_id, measurement_id=measurement_id,
        assessment_id=assessment_id, assessment_epoch=assessment_epoch,
        quality_failure="not_evaluated")
    def fail(reason):
        result["quality_failure"] = reason
        return result
    frozen = {k: deepcopy(v) for k, v in registration.items() if k != "registration_sha256"}
    if canonical_sha256(frozen) != registration.get("registration_sha256"):
        return fail("registration_changed")
    if registration["task_sha256"] != task_sha256:
        return fail("task_binding_mismatch")
    if registration["validation"]["status"] != "verified":
        return fail("model_validity_not_certified")
    point = next((p for p in registration["measurement_points"] if p["id"] == measurement_id), None)
    if point is None:
        return fail("unregistered_measurement_point")
    if (grounding.get("status") != "verified" or not grounding.get("evidence_refs")
            or grounding.get("state_sha256") != canonical_sha256(state)):
        return fail("trusted_state_grounding_failed")
    model = registration["model"]
    try:
        if not episode_id or not assessment_id or type(assessment_epoch) is not int or assessment_epoch < 0:
            return fail("invalid_assessment_identity")
        if type(state["time_s"]) not in (int, float) or not 0 <= state["time_s"] < float("inf"):
            return fail("invalid_trusted_state_time")
        facts = _names(state["true_facts"], "true_facts")
        if (not facts <= set(model["fact_ids"]) or not set(point["required_true"]) <= facts
                or set(point["required_false"]) & facts):
            return fail("state_outside_preregistered_domain")
        remaining = _amounts(state["resources"], "resources")
        if set(remaining) != set(model["resource_bounds"]) or any(
            value > model["resource_bounds"][key] for key, value in remaining.items()
        ):
            return fail("resources_outside_preregistered_domain")
        if type(state["max_active_agents"]) is not int or state["max_active_agents"] < 0:
            return fail("invalid_current_team_budget")
        ids = _names([robot["id"] for robot in state["robots"]], "robot ids")
        if set(state["capability_versions"]) != ids:
            return fail("team_version_binding_mismatch")
        for robot in state["robots"]:
            _names(robot["capabilities"], "robot capabilities")
            payload = robot["payload_kg"]
            if type(payload) not in (int, float) or not 0 <= payload < float("inf"):
                return fail("invalid_robot_payload")
        for version in [state["roster_version"], *state["capability_versions"].values()]:
            if type(version) is not int or version < 0:
                return fail("invalid_team_version")
    except (ValueError, KeyError, TypeError):
        return fail("invalid_trusted_state")
    if type(max_states) is not int or max_states < 1:
        raise ValueError("max_states must be positive")
    keys = sorted(remaining)
    initial = (frozenset(facts), tuple(remaining[key] for key in keys))
    pending, visited = deque([initial]), {initial}
    enabled = [t for t in model["transitions"]
               if _can_staff(t["capability_slots"], state["robots"], state["max_active_agents"])]
    label = "not_deployable"
    terminal = set(model["terminal_all"])
    while pending:
        current, resources = pending.popleft()
        if terminal <= current:
            label = "deployable"
            break
        for transition in enabled:
            if not set(transition["requires_all"]) <= current or set(transition["requires_absent"]) & current:
                continue
            spent = tuple(transition["resource_costs"].get(key, 0) for key in keys)
            if any(cost > available for cost, available in zip(spent, resources)):
                continue
            successor = (frozenset((current - set(transition["deletes"])) | set(transition["adds"])),
                         tuple(available - cost for cost, available in zip(spent, resources)))
            if successor not in visited:
                if len(visited) >= max_states:
                    return fail("oracle_search_budget_exhausted")
                visited.add(successor)
                pending.append(successor)
    result.update(feasibility_label=label, oracle_status="verified", platform_valid=True,
                  state_sha256=canonical_sha256(state), registration_sha256=registration["registration_sha256"],
                  explored_state_count=len(visited), validation_scope=registration["validation"]["scope"],
                  evidence_refs=list(registration["validation"]["evidence_refs"]) + list(grounding["evidence_refs"]))
    return result


class RegisteredRemainingTaskOracle:
    """Runner hook that obtains trusted state before the participant answers.

    The provider sees only the current requirement snapshot, never the
    participant's decision, plan, witness, action history or future outcomes.
    It returns a preregistered measurement ID plus state and grounding proof.
    """

    def __init__(self, registration, state_provider):
        self._registration = deepcopy(registration)
        self._provider = state_provider
        self._results = []

    def __call__(self, episode_id, trusted_report):
        session = trusted_report["session"]
        gate = session["deployment_assessment"]
        if session["episode_id"] != episode_id or gate["status"] != "required":
            raise ValueError("oracle must run for the current required epoch before declaration")
        permitted = ("assessment_id", "assessment_epoch", "trigger_event_type", "trigger_event_sequence",
                     "triggered_at_s", "public_task_sha256", "current_robot_pool", "capability_versions",
                     "roster_version", "resource_versions", "deployment_change_versions", "runtime_state")
        snapshot = {key: deepcopy(gate.get(key)) for key in permitted}
        # Initial assessment_required is emitted before the runner installs
        # the first runtime snapshot. Ground at the actual pre-decision query,
        # retaining the trigger identity separately.
        snapshot["runtime_state"] = deepcopy(session.get("runtime_state"))
        snapshot["oracle_at_s"] = session["current_time_s"]
        observation = None
        try:
            observation = self._provider(snapshot)
            if not isinstance(observation, Mapping):
                raise TypeError("state provider must return an object")
            observed_state = observation["state"]
            measurement_id = observation["measurement_id"]
            grounding = observation["grounding"]
            if not isinstance(observed_state, Mapping):
                raise TypeError("state provider state must be an object")
            if not isinstance(grounding, Mapping):
                raise TypeError("state provider grounding must be an object")
        except Exception as exc:
            # A missing native reader, malformed readback, or provider error
            # is a platform evidence failure. Preserve only a stable category
            # in the evaluator report; never let ``unverified`` reach the
            # participant-facing assessment contract.
            result = _failed_result(
                episode_id=episode_id,
                measurement_id=(observation.get("measurement_id")
                                if isinstance(observation, Mapping) else None),
                assessment_id=gate["assessment_id"],
                assessment_epoch=gate["assessment_epoch"],
                quality_failure=f"state_provider_failed:{type(exc).__name__}")
            self._results.append(result)
            return {key: deepcopy(result[key]) for key in (
                "assessment_id", "assessment_epoch", "feasibility_label", "oracle_status",
                "platform_valid", "evidence_refs", "evaluator_ref")}
        binding_matches = (
            observed_state.get("time_s") == session["current_time_s"]
            and observed_state.get("roster_version") == gate.get("roster_version")
            and observed_state.get("capability_versions") == gate.get("capability_versions")
            and observed_state.get("resource_versions", {}) == gate.get("resource_versions", {}))
        grounding = grounding if binding_matches else {"status": "failed"}
        result = query_remaining_feasibility(
            self._registration, measurement_id=measurement_id, episode_id=episode_id,
            assessment_id=gate["assessment_id"], assessment_epoch=gate["assessment_epoch"],
            task_sha256=gate["public_task_sha256"], state=observed_state, grounding=grounding)
        if not binding_matches:
            result["quality_failure"] = "measurement_snapshot_binding_mismatch"
        self._results.append(result)
        return {key: deepcopy(result[key]) for key in (
            "assessment_id", "assessment_epoch", "feasibility_label", "oracle_status",
            "platform_valid", "evidence_refs", "evaluator_ref")}

    def report(self):
        return {"schema": "eai.benchmark.remaining_feasibility_evidence", "version": 1,
                "visibility": "evaluator_only", "registration": deepcopy(self._registration),
                "results": deepcopy(self._results),
                "release_review": formal_oracle_release_review(self._registration, self._results),
                "execution_validated": False, "business_outcomes_validated": False, "scoreable": False}


def formal_oracle_release_review(registration: Mapping[str, Any], results: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Fail release unless all registered points have native verified evidence."""
    expected = {point["id"] for point in registration["measurement_points"]}
    covered = {row.get("measurement_id") for row in results
               if row.get("registration_sha256") == registration["registration_sha256"]
               and row.get("oracle_status") == "verified"
               and row.get("feasibility_label") in {"deployable", "not_deployable"}
               and row.get("validation_scope") == "native_remaining_task"}
    failed = [row.get("measurement_id") for row in results if row.get("oracle_status") != "verified"]
    frozen = {key: value for key, value in registration.items() if key != "registration_sha256"}
    registration_valid = canonical_sha256(frozen) == registration["registration_sha256"]
    return {"oracle_release_ready": registration_valid and expected <= covered and not failed
            and registration["validation"].get("status") == "verified"
            and registration["validation"].get("scope") == "native_remaining_task",
            "missing_measurement_ids": sorted(expected - covered),
            "failed_measurement_ids": failed, "scoreable": False}
