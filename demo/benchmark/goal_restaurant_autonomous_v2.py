"""Opt-in full-eight Restaurant participant context for an incremental host.

This is the shared preparation boundary, not a synchronous fallback executor.
The caller owns the single fleet tick/event loop and must separately provide
native execution and business verification. No witness expansion occurs here.
Legacy algorithm participants are never silently upgraded to this profile.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from .goal_algorithm_adapter import GoalAlgorithmHost, validate_goal_adapter_manifest
from .goal_restaurant_fleet import CLOSED_VALIDATION_FLAGS, FLEET_CONTROLLER_REGISTRY
from .goal_restaurant_native_state import build_restaurant_native_initial_state
from .goal_session_audit import AuditedGoalSession
from .provenance import canonical_sha256


class RestaurantDeploymentContextError(RuntimeError):
    """Missing native/platform context, never a participant feasibility answer."""


@dataclass
class RestaurantAutonomousContext:
    """Trusted host-only handles; only host public events reach the adapter."""

    host: GoalAlgorithmHost
    session: AuditedGoalSession
    fleet: Any
    contract: dict[str, Any]
    initial_state: dict[str, Any]
    binding: dict[str, Any]
    execution_profile: dict[str, Any] | None = None
    execution_profile_ack: dict[str, Any] | None = None
    execution_loop_connected: bool = False
    _execution_initializer: Any = field(default=None, repr=False)

    def initialize_execution(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Expose one opt-in native execution profile to the participant."""
        if self.execution_profile is not None:
            if canonical_sha256(profile) != canonical_sha256(self.execution_profile):
                raise RestaurantDeploymentContextError(
                    "execution profile is already bound to another contract")
            return deepcopy(self.execution_profile_ack)
        if not callable(self._execution_initializer):
            raise RestaurantDeploymentContextError(
                "adapter has not opted into the native execution profile")
        public = deepcopy(dict(profile))
        digest = canonical_sha256(public)
        acknowledged = self._execution_initializer(deepcopy(public))
        expected = {
            "schema": "eai.restaurant.execution_profile_ack",
            "version": 1,
            "profile_sha256": digest,
        }
        if acknowledged != expected:
            raise RestaurantDeploymentContextError(
                "adapter execution-profile acknowledgement mismatch")
        self.execution_profile = public
        self.execution_profile_ack = deepcopy(expected)
        return deepcopy(expected)

    def archive(self) -> dict[str, Any]:
        """Evaluator-only context evidence, including actual participant plans."""
        return {"schema": "eai.restaurant.autonomous_context", "version": 2,
                "binding": deepcopy(self.binding),
                "initial_state": deepcopy(self.initial_state),
                "public_contract": deepcopy(self.contract),
                "execution_profile": deepcopy(self.execution_profile),
                "execution_profile_ack": deepcopy(self.execution_profile_ack),
                "session_audit": self.session.audit_journal(),
                "execution_profile_acknowledged": self.execution_profile is not None,
                "execution_loop_connected": self.execution_loop_connected,
                **CLOSED_VALIDATION_FLAGS}


@contextmanager
def open_restaurant_autonomous_context(
    fleet: Any, adapter: Any, *, deployment: Mapping[str, Any],
    assessment_evaluator: Any = None,
) -> Iterator[RestaurantAutonomousContext]:
    """Bind full8 measured positions before evaluator/participant initialization.

    ``initialize_deployment`` explicitly acknowledges the exact public companion
    identity. Existing adapters without that method remain valid for old entries;
    this entry rejects them before creating a participant session. The evaluator
    sees native initial facts and binding, never a participant-supplied truth.
    Missing oracle evidence stays failed/null and is not exposed as a third answer.
    """
    from .goal_restaurant_deployment_v2 import build_restaurant_deployment_v2

    contract = build_restaurant_deployment_v2()
    if canonical_sha256(deployment) != canonical_sha256(contract["deployment"]):
        raise RestaurantDeploymentContextError("deployment companion differs from maintained profile")
    initialize = getattr(adapter, "initialize_deployment", None)
    if not callable(initialize):
        raise RestaurantDeploymentContextError("adapter has not opted into the deployment companion")
    validate_goal_adapter_manifest(adapter.manifest())
    state = build_restaurant_native_initial_state(fleet)
    if (state.get("status") != "measured"
            or state.get("all_eight_initial_regions_available") is not True
            or set(state.get("robot_regions", {})) != set(FLEET_CONTROLLER_REGISTRY)
            or state.get("deployment_sha256") != canonical_sha256(deployment)
            or state.get("task_id") != contract["public_task"]["task_id"]):
        raise RestaurantDeploymentContextError("complete measured full8 deployment state is unavailable")
    identity = {"schema": "eai.restaurant.deployment_ack", "version": 2,
                "profile": deployment["profile"],
                "deployment_sha256": canonical_sha256(deployment),
                "public_task_sha256": canonical_sha256(contract["public_task"])}
    binding = {**identity, "episode_id": state["episode_id"],
               "native_initial_state_sha256": canonical_sha256(state),
               "native_epoch_archive": deepcopy(state["source_epoch_archive"])}
    session = AuditedGoalSession(contract["public_task"], contract["participant_api"],
                                 episode_id=state["episode_id"])
    # Seed the pristine epoch before evaluator/participant exposure, without a
    # fabricated lifecycle event. Trigger/evaluation/declaration share one state.
    session.initialize_deployment_runtime_state(state["robot_regions"], {})
    assessment = session.report()["deployment_assessment"]
    failure = {"assessment_id": assessment["assessment_id"],
               "assessment_epoch": assessment["assessment_epoch"],
               "feasibility_label": None, "oracle_status": "failed",
               "platform_valid": False, "evidence_refs": [],
               "evaluator_ref": "restaurant_native_oracle_not_configured"}
    if assessment_evaluator is None:
        evaluation = failure
    else:
        try:
            evaluation = assessment_evaluator(state["episode_id"], {
                "session": session.report(), "deployment_binding": deepcopy(binding),
                "native_initial_state": deepcopy(state)})
        except Exception:
            session.record_deployment_assessment_evaluation({
                **failure, "evaluator_ref": "restaurant_native_oracle_evaluation_failed"})
            raise
    session.record_deployment_assessment_evaluation(evaluation)
    # Acknowledgement data are public constants. Native roots/quality/oracle and
    # source filesystem paths remain host-private, not an unmetered pose stream.
    try:
        acknowledged = initialize(deepcopy(contract["deployment"]))
        if canonical_sha256(acknowledged) != canonical_sha256(identity):
            raise RestaurantDeploymentContextError("adapter deployment acknowledgement mismatch")
    except BaseException:
        adapter.close()
        raise
    with GoalAlgorithmHost(contract["public_task"], contract["participant_api"],
                           adapter, session=session) as host:
        yield RestaurantAutonomousContext(
            host, session, fleet, contract, state, binding,
            _execution_initializer=getattr(adapter, "initialize_execution", None))


__all__ = ["RestaurantAutonomousContext", "RestaurantDeploymentContextError",
           "open_restaurant_autonomous_context"]
