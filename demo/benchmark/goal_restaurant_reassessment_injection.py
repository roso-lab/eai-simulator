"""Scripted native injection of Restaurant runtime reassessment epochs.

This module is the R1 collection/issuance adapter around the *unmodified*
public run entry and execution pump.  It composes
``demo.benchmark.goal_restaurant_public_run`` (request preparation, asset
preflight, oracle inputs) and ``RestaurantExecutionPump`` (bounded native
ticks) without editing any existing module:

1. ``run`` drives ``pump.step_once()`` itself and applies a preregistered
   injection script at exact native tick boundaries.  ``member_exit`` first
   refreshes the trusted runtime state without the exiting member (mirroring
   the lifecycle-callback discipline of ``GoalRestaurantInterventions``) and
   then emits the public lifecycle event, so the frozen deployment-assessment
   gate carries the post-change roster and a same-time runtime snapshot.
   ``capability_change`` re-declares one member with removed capabilities and
   a strictly increased capability version.
2. Every triggered epoch is dumped as a post-change snapshot (roster and
   capability versions, runtime state, established readiness facts, remaining
   budget) plus the pre-decision oracle-boundary session report; the final
   session report is replayed into the runtime-epoch collection format through
   ``collect_reassessment_epoch_inputs``.
3. ``issue`` assembles and validates one
   ``eai.restaurant.reassessment_extension`` from a verified collection
   review, and ``verify`` replays the captured boundary reports through
   ``RestaurantReassessmentOracle``.  Issuance consumes externally pinned
   inputs; nothing here self-authenticates or promotes release flags.

``milestone_state_change`` triggers are fail-closed at script validation:
the Goal v3 session vocabulary declares no such event type, so a milestone
epoch cannot be produced natively until the session contract is extended.
A successful process is a collection, never a release qualification.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import traceback
from typing import Any, Mapping

from .goal_restaurant_fleet import CLOSED_VALIDATION_FLAGS, FLEET_CONTROLLER_REGISTRY
from .goal_restaurant_reassessment import (
    EXTENSION_SCHEMA,
    EXTENSION_VERSION,
    RestaurantReassessmentOracle,
    _freeze_extension_registration,
    validate_restaurant_reassessment_extension,
)
from .goal_restaurant_reassessment_collection import (
    build_extension_input,
    collect_reassessment_epoch_inputs,
    review_reassessment_collection,
)
from .provenance import canonical_sha256


INJECTION_SCHEMA = "eai.benchmark.restaurant_reassessment_injection"
INJECTION_VERSION = 1
EPOCH_RECORD_SCHEMA = "eai.benchmark.restaurant_reassessment_epoch_record"
ISSUANCE_SCHEMA = "eai.benchmark.restaurant_reassessment_issuance"
VERIFICATION_SCHEMA = "eai.benchmark.restaurant_reassessment_verification"
SUPPORTED_EVENT_TYPES = ("member_exit", "capability_change")
UNSUPPORTED_EVENT_TYPES = ("milestone_state_change",)
PENDING_EXTENSION_EVALUATOR_REF = "restaurant_runtime_reassessment_extension_pending"
RUNTIME_EPOCH_FACTS = ("kitchen_ready", "dining_ready", "readiness_receipt_committed")
_REGION_FACTS = {"kitchen_service": "kitchen_ready", "dining_service": "dining_ready"}
_TIME_EPS_S = 1e-6
_ROSTER = frozenset(FLEET_CONTROLLER_REGISTRY)


@dataclass(frozen=True)
class ScriptedEvent:
    """One preregistered runtime change applied at a native tick boundary."""

    at_s: float
    event_type: str
    agent_id: str
    remove_capabilities: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "at_s": self.at_s,
            "event_type": self.event_type,
            "agent_id": self.agent_id,
            "remove_capabilities": list(self.remove_capabilities),
        }


def parse_injection_script(rows: list[Mapping[str, Any]] | None) -> list[ScriptedEvent]:
    """Validate the preregistered script before any simulator startup."""
    if not rows:
        raise ValueError("injection script must contain at least one event")
    events: list[ScriptedEvent] = []
    agents: set[str] = set()
    previous_at: float | None = None
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("each script row must be an object")
        at_s = raw.get("at_s")
        if (isinstance(at_s, bool) or not isinstance(at_s, (int, float))
                or not math.isfinite(at_s) or at_s <= 0.0):
            raise ValueError("script at_s must be a finite positive number")
        at_s = float(at_s)
        if previous_at is not None and at_s <= previous_at:
            raise ValueError("script at_s values must strictly increase")
        previous_at = at_s
        event_type = raw.get("event_type")
        if event_type in UNSUPPORTED_EVENT_TYPES:
            raise ValueError(
                "milestone_state_change is declared by the reassessment "
                "vocabulary but the Goal v3 session contract has no such "
                "event type; a milestone epoch cannot be injected natively "
                "until the session contract is extended")
        if event_type not in SUPPORTED_EVENT_TYPES:
            raise ValueError(
                f"unsupported injection event_type: {event_type!r}; "
                f"supported: {SUPPORTED_EVENT_TYPES}")
        agent = raw.get("agent_id")
        if not isinstance(agent, str) or agent not in _ROSTER:
            raise ValueError(f"script agent_id is outside the full8 roster: {agent!r}")
        if agent in agents:
            raise ValueError(f"script touches agent {agent} more than once")
        agents.add(agent)
        remove = raw.get("remove_capabilities", [])
        if event_type == "capability_change":
            if (not isinstance(remove, list)
                    or not remove
                    or any(not isinstance(item, str) or not item for item in remove)
                    or len(remove) != len(set(remove))):
                raise ValueError(
                    "capability_change requires a nonempty list of distinct "
                    "capabilities to remove")
        elif remove:
            raise ValueError("member_exit does not remove capabilities")
        events.append(ScriptedEvent(
            at_s=at_s, event_type=event_type, agent_id=agent,
            remove_capabilities=tuple(remove or ())))
    return events


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def established_readiness_facts(readiness_service: Mapping[str, Any]) -> list[str]:
    """Derive trusted established facts from the readiness service snapshot.

    A committed readiness receipt proves both service inspections and the
    committed receipt fact; without a receipt, each recorded native service
    observation establishes only its region's readiness fact.
    """
    facts: set[str] = set()
    receipt = readiness_service.get("receipt")
    observations = readiness_service.get("observations")
    if not isinstance(observations, Mapping):
        observations = {}
    if receipt is not None:
        facts.update(RUNTIME_EPOCH_FACTS)
    else:
        for row in observations.values():
            if isinstance(row, Mapping):
                fact = _REGION_FACTS.get(row.get("region_id"))
                if fact is not None:
                    facts.add(fact)
    return sorted(facts)


class ScriptedReassessmentInjector:
    """Apply a preregistered runtime-change script at native tick boundaries."""

    def __init__(self, context: Any, pump: Any,
                 script: list[ScriptedEvent] | list[Mapping[str, Any]], *,
                 episode_id: str | None = None) -> None:
        rows = [event.summary() if isinstance(event, ScriptedEvent) else event
                for event in script]
        self.script = parse_injection_script(rows)
        self.context = context
        self.pump = pump
        self.episode_id = episode_id or context.binding["episode_id"]
        self.records: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []

    def _established_facts(self) -> list[str]:
        service = self.pump.readiness.snapshot()["readiness_service"]
        return established_readiness_facts(service)

    def _emit_member_exit(self, event: ScriptedEvent, now: float) -> None:
        session = self.context.session
        current = session.report()
        runtime = current.get("runtime_state")
        if not isinstance(runtime, Mapping) or runtime.get("time_s") != now:
            raise ValueError("member_exit needs a same-time trusted runtime state")
        regions = runtime.get("robot_regions")
        if (not isinstance(regions, Mapping)
                or not set(regions) <= set(current["current_agents"])):
            raise ValueError(
                "member_exit runtime state does not stay inside the current roster")
        if event.agent_id not in regions:
            raise ValueError(
                "exiting member has no measured region at the boundary "
                "(moving or unmapped members cannot anchor a post-change gate)")
        post_regions = {agent: region for agent, region in regions.items()
                        if agent != event.agent_id}
        # Refresh the trusted state first so the frozen gate snapshot carries
        # the post-change roster at the same native time as the public event.
        session.update_runtime_state(now, post_regions, {})
        session.emit("member_exit", now, {"agent_id": event.agent_id})

    def _emit_capability_change(self, event: ScriptedEvent, now: float) -> None:
        session = self.context.session
        current = session.report()
        declarations = current.get("current_robot_declarations")
        if not isinstance(declarations, Mapping) or event.agent_id not in declarations:
            raise ValueError("capability_change target has no current declaration")
        declaration = deepcopy(dict(declarations[event.agent_id]))
        capabilities = list(declaration.get("public_capabilities") or ())
        removed = [item for item in capabilities if item in event.remove_capabilities]
        residual = [item for item in capabilities
                    if item not in event.remove_capabilities]
        if not removed or not residual:
            raise ValueError(
                "capability_change must remove a present capability and leave a "
                "nonempty declaration")
        declaration["public_capabilities"] = residual
        version = int(current["capability_versions"][event.agent_id]) + 1
        session.emit("capability_change", now, {
            "agent_id": event.agent_id,
            "capability_declaration": declaration,
            "capability_version": version,
        })

    def apply_event(self, event: ScriptedEvent) -> dict[str, Any]:
        """Apply one scripted event and freeze its post-change epoch snapshot."""
        session_report = self.context.session.report()
        if session_report["deployment_assessment"].get("terminated"):
            raise RuntimeError("episode already terminated; cannot inject")
        now = float(session_report["current_time_s"])
        if now + _TIME_EPS_S < event.at_s:
            raise ValueError("apply_event called before the scripted boundary")
        previous_epoch = session_report["deployment_assessment"]["assessment_epoch"] or 0
        if event.event_type == "member_exit":
            self._emit_member_exit(event, now)
        else:
            self._emit_capability_change(event, now)
        boundary = self.context.session.report()
        gate = boundary["deployment_assessment"]
        if (gate.get("status") != "required"
                or gate.get("assessment_epoch") != previous_epoch + 1
                or gate.get("trigger_event_type") != event.event_type
                or gate.get("external_evaluation") is not None):
            raise RuntimeError("injection did not open a fresh reassessment epoch")
        runtime_state = gate.get("runtime_state")
        if (not isinstance(runtime_state, Mapping)
                or runtime_state.get("time_s") != now
                or set(runtime_state.get("robot_regions") or {})
                != set(gate.get("current_agent_ids") or [])):
            raise RuntimeError(
                "post-change gate runtime state does not cover the new roster "
                "at the boundary time")
        # A lifecycle event marks the trusted snapshot stale for the v3
        # contract; re-assert the frozen post-change state at the same native
        # time so the participant may dispatch again after its new decision
        # (the same emit→update discipline as GoalRestaurantInterventions).
        self.context.session.update_runtime_state(
            now, dict(runtime_state["robot_regions"]), {})
        boundary = self.context.session.report()
        record = {
            "schema": EPOCH_RECORD_SCHEMA,
            "version": 1,
            "visibility": "evaluator_only",
            "episode_id": self.episode_id,
            "script_event": event.summary(),
            "assessment_epoch": gate["assessment_epoch"],
            "assessment_id": gate["assessment_id"],
            "trigger_event_type": gate["trigger_event_type"],
            "trigger_event_sequence": gate["trigger_event_sequence"],
            "triggered_at_s": gate["triggered_at_s"],
            "measurement_id": f"restaurant_runtime_epoch_{gate['assessment_epoch']}",
            "remaining_time_ms": 240_000 - int(round(now * 1000)),
            "roster_version": gate["roster_version"],
            "capability_versions": deepcopy(gate["capability_versions"]),
            "current_agent_ids": list(gate["current_agent_ids"]),
            "resource_versions": deepcopy(gate["resource_versions"]),
            "deployment_change_versions": deepcopy(gate["deployment_change_versions"]),
            "runtime_state": deepcopy(runtime_state),
            "runtime_state_sha256": gate["runtime_state_sha256"],
            "established_facts": self._established_facts(),
            "oracle_boundary_session_report": deepcopy(boundary),
            **CLOSED_VALIDATION_FLAGS,
        }
        # The extension for this epoch does not exist at collection time, so
        # the runtime evaluation stays fail-closed (null label), exactly like
        # an unconfigured initial oracle.  The captured boundary above is the
        # input the later offline oracle replays.
        self.context.session.record_deployment_assessment_evaluation({
            "assessment_id": gate["assessment_id"],
            "assessment_epoch": gate["assessment_epoch"],
            "feasibility_label": None,
            "oracle_status": "failed",
            "platform_valid": False,
            "evidence_refs": [],
            "evaluator_ref": PENDING_EXTENSION_EVALUATOR_REF,
        })
        self.records.append(record)
        return deepcopy(record)

    def _decided_runtime_epochs(self) -> int:
        history = self.context.session.report()["deployment_assessment"].get(
            "history") or []
        return len([row for row in history
                    if isinstance(row, Mapping)
                    and isinstance(row.get("assessment_epoch"), int)
                    and row["assessment_epoch"] >= 1])

    def run(self, *, max_ticks: int) -> dict[str, Any]:
        """Step the pump and inject at boundaries until every epoch is decided."""
        if isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or max_ticks <= 0:
            raise ValueError("max_ticks must be a positive integer")
        pending = list(self.script)
        last_tick: dict[str, Any] | None = None
        for _ in range(max_ticks):
            last_tick = self.pump.step_once()
            now = float(last_tick["time_s"])
            while pending and pending[0].at_s <= now + _TIME_EPS_S:
                event = pending.pop(0)
                try:
                    self.apply_event(event)
                except Exception as exc:
                    # Fail closed: record the infrastructure failure and stop
                    # applying further scripted changes to the live episode.
                    self.failures.append({
                        "at_s": event.at_s,
                        "event_type": event.event_type,
                        "agent_id": event.agent_id,
                        "type": type(exc).__name__,
                        "error": str(exc),
                    })
                    pending = []
                    break
            gate = self.context.session.report()["deployment_assessment"]
            if gate.get("terminated"):
                break
            if not pending and self._decided_runtime_epochs() >= len(self.records):
                break
        collection = collect_reassessment_epoch_inputs(
            self.context.session.report(), episode_id=self.episode_id)
        return {
            "schema": INJECTION_SCHEMA,
            "version": INJECTION_VERSION,
            "visibility": "evaluator_only",
            "episode_id": self.episode_id,
            "script": [event.summary() for event in self.script],
            "epoch_records": deepcopy(self.records),
            "injection_failures": deepcopy(self.failures),
            "decided_runtime_epochs": self._decided_runtime_epochs(),
            "last_tick": deepcopy(last_tick) if last_tick else None,
            "reassessment_collection": collection,
            **CLOSED_VALIDATION_FLAGS,
        }


def run_native_with_injections(args: argparse.Namespace, request: Mapping[str, Any],
                               script: list[ScriptedEvent]) -> dict[str, Any]:
    """Mirror of the public-run native path with the scripted injector."""
    from .communication import CommunicationRuntime, validate_communication_report
    from .goal_restaurant_autonomous_v2 import open_restaurant_autonomous_context
    from .goal_restaurant_business_facts import export_restaurant_business_facts
    from .goal_restaurant_deployment_v2 import DEPLOYMENT_PROFILE
    from .goal_restaurant_execution_v2 import RestaurantExecutionPump
    from .goal_restaurant_fleet import RestaurantFleetController
    from .goal_restaurant_native_observation import RestaurantNativeObservationProvider
    from .goal_restaurant_public_policy import PublicRestaurantParticipant
    from .goal_restaurant_public_run import (
        ground_policy_condition_event,
        initial_assessment_evaluator,
    )
    from .goal_session_audit import replay_goal_session_audit

    inputs = (json.loads((args.output / "initial-oracle-inputs.json").read_text(
        encoding="utf-8")) if request.get("initial_oracle_inputs_requested") else None)
    evaluator = initial_assessment_evaluator(request, inputs)

    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    from simulator import SimulatorLaunchConfig, open_simulator_session

    def configure(env_cfg):
        env_cfg.episode_length_s = 3600.
        env_cfg.sim.enable_scene_query_support = True

    selection = request["selection"]
    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=args.device, seed=args.seed,
        headless=True, enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, env_cfg_hook=configure)
    with open_simulator_session(config) as simulator:
        applied = {"requested_seed": args.seed,
                   "effective_seed": simulator.base_env.cfg.seed,
                   "requested_device": args.device,
                   "effective_device": str(simulator.base_env.cfg.sim.device)}
        _write(args.output / "runtime-configuration.json", applied)
        if request.get("initial_oracle_inputs_requested") and (
                applied["effective_seed"] != args.seed
                or applied["effective_device"] != args.device):
            raise RuntimeError(
                "oracle input runtime profile differs from applied seed or device")
        fleet = RestaurantFleetController(
            simulator, products_root=args.products_root.resolve(),
            deployment_profile=DEPLOYMENT_PROFILE)
        metadata = fleet.begin_episode(args.episode_id, warmup_ticks=args.warmup_ticks)
        _write(args.output / "episode-metadata.json", metadata)
        _write(args.output / "initial.json", fleet.snapshot())
        adapter = PublicRestaurantParticipant()
        runtime = CommunicationRuntime(request["communication_contract"])
        provider = RestaurantNativeObservationProvider(fleet)
        with open_restaurant_autonomous_context(
                fleet, adapter, deployment=request["deployment"],
                assessment_evaluator=evaluator) as context:
            if evaluator is not None:
                _write(args.output / "initial-oracle-report.json", evaluator.report())
            hook = None
            if request.get("publication_treatment_extension"):
                from .goal_driven_tasks import build_goal_task_package
                from .goal_restaurant_publication_treatment import (
                    RestaurantPublicationTreatment,
                )
                hook = RestaurantPublicationTreatment(
                    public_task=request["public_task"],
                    hidden_evaluator=build_goal_task_package("restaurant")[
                        "hidden_evaluator"],
                    communication_contract=request["communication_contract"],
                    deployment_companion=request["deployment"])
            pump = RestaurantExecutionPump(context, runtime=runtime,
                                           observation_provider=provider,
                                           publication_treatment=hook)
            injector = ScriptedReassessmentInjector(
                context, pump, script, episode_id=args.episode_id)
            try:
                injection = injector.run(max_ticks=args.max_ticks)
            except Exception as exc:
                _write(args.output / "injection-error.json", {
                    "type": type(exc).__name__, "error": str(exc),
                    "traceback": traceback.format_exc()})
                # Preserve the primary native evidence for the failed run too.
                for name, value in {
                    "context.json": context.archive(),
                    "session.json": context.session.report(),
                    "audit.json": context.session.audit_journal(),
                    "communication.json": runtime.report(),
                    "final.json": fleet.snapshot(),
                    "participant.json": {"plan": adapter.plan,
                                         "received_events": adapter.events,
                                         "submitted_actions": adapter.actions,
                                         "finished": adapter.finished},
                }.items():
                    _write(args.output / name, value)
                _write(args.output / "pump.json", pump.report())
                raise
            # Preserve primary facts before replay/scoring operations may reject.
            documents = {
                "context.json": context.archive(),
                "session.json": context.session.report(),
                "audit.json": context.session.audit_journal(),
                "communication.json": runtime.report(),
                "final.json": fleet.snapshot(),
                "participant.json": {"plan": adapter.plan,
                                     "received_events": adapter.events,
                                     "submitted_actions": adapter.actions,
                                     "finished": adapter.finished},
            }
            for name, value in documents.items():
                _write(args.output / name, value)
            archive = pump.report()
            _write(args.output / "pump.json", archive)
            epochs_root = args.output / "reassessment-epochs"
            epochs_root.mkdir(parents=True, exist_ok=True)
            for record in injection["epoch_records"]:
                _write(epochs_root / f"epoch-{record['assessment_epoch']}.json",
                       record)
            _write(args.output / "reassessment-injection.json", injection)
            _write(args.output / "reassessment-collection.json",
                   injection["reassessment_collection"])
            audit = replay_goal_session_audit(
                documents["audit.json"], request["public_task"],
                request["participant_api"])
            communication = validate_communication_report(
                documents["communication.json"],
                expected_initial_contract=request["communication_contract"])
            _write(args.output / "audit-review.json", audit)
            _write(args.output / "communication-review.json", communication)
            if hook is not None:
                from .goal_restaurant_publication_treatment import (
                    verify_restaurant_publication_treatment,
                )
                treatment_review = verify_restaurant_publication_treatment(
                    pump_report=archive,
                    public_task=request["public_task"],
                    participant_api=request["participant_api"],
                    hidden_evaluator=build_goal_task_package("restaurant")[
                        "hidden_evaluator"],
                    communication_contract=request["communication_contract"],
                    deployment_companion=request["deployment"])
                _write(args.output / "publication-treatment-review.json",
                       treatment_review)
            drift = [path for path, digest in request["source_sha256"].items()
                     if not Path(path).is_file() or _sha256(Path(path)) != digest]
            session = documents["session.json"]
            condition_review = ground_policy_condition_event(
                request, session, documents["communication.json"], archive)
            _write(args.output / "condition-review.json", condition_review)
            terminal_statuses = {"succeeded", "failed", "cancelled", "superseded"}
            all_terminal = bool(session["tasks"]) and all(
                row["status"] in terminal_statuses for row in session["tasks"].values())
            report = {
                "status": "execution_archived",
                "error": None, "source_drift": drift,
                "time_s": fleet.time_s,
                "task_statuses": {key: row["status"]
                                  for key, row in session["tasks"].items()},
                "all_plan_tasks_terminal": all_terminal,
                "bounded_tick_limit_reached": len(archive["ticks"]) == args.max_ticks,
                "trusted_episode_horizon_termination": False,
                "oracle_status": session["deployment_assessment"][
                    "external_evaluation"]["oracle_status"],
                "injected_runtime_epochs": [record["assessment_epoch"]
                                            for record in injection["epoch_records"]],
                "injection_failures": injection["injection_failures"],
                "task_exact_communication_treatment_validated": False,
                "condition_trigger_status": condition_review["event"]["trigger_status"],
                "formal_statistics_eligible": False, **CLOSED_VALIDATION_FLAGS,
            }
            _write(args.output / "report.json", report)
            if drift:
                raise RuntimeError("native diagnostic archived a source-drift error")
            inputs = dict(
                public_task=request["public_task"],
                deployment_companion=request["deployment"],
                episode_metadata=metadata, context_report=documents["context.json"],
                session_report=session, audit_journal=documents["audit.json"],
                audit_replay=audit, pump_report=archive,
                communication_report=documents["communication.json"],
                communication_replay=communication,
                archived_source_sha256=request["source_sha256"],
                expected_source_sha256=request["source_sha256"])
            export_restaurant_business_facts(
                args.output / "scoring", request["public_task"],
                episode_id=args.episode_id, verifier_inputs=inputs)
            report.update(status="diagnostic_finished", scoring_exported=True)
            _write(args.output / "report.json", report)
            return report


def _raw_archive_digest(run_dir: Path) -> dict[str, Any]:
    files = {str(path.relative_to(run_dir)): _sha256(path)
             for path in sorted(run_dir.rglob("*.json"))}
    if not files:
        raise ValueError(f"no raw archive files under {run_dir}")
    return {"raw_files": files, "raw_archive_sha256": canonical_sha256(files)}


def issue_reassessment_extension(
    extension_input: Mapping[str, Any],
    *,
    certificate: Mapping[str, Any],
    established_facts_by_epoch: Mapping[int, set[str] | list[str] | tuple[str, ...]],
    host: str,
    seed: int,
    raw_archive_sha256: str,
    runtime_profile_sha256: str,
) -> dict[str, Any]:
    """Fill and validate one extension from the verified extension input.

    Fact partitions come from the collected per-epoch established facts, the
    runtime profile must stay inside the certificate allowlist, and the only
    native evidence is the externally pinned raw collection archive.  This
    function assembles and self-checks the document; trusting it is the
    issuer's decision, expressed by independently pinning its digest.
    """
    allowed = certificate["qualification"]["allowed_runtime_profile_sha256"]
    if runtime_profile_sha256 not in allowed:
        raise ValueError(
            "collection runtime profile is outside the certificate allowlist")
    scope = certificate["qualification"]["scope"]
    if scope not in {"synthetic_fixture", "native_empirical_domain"}:
        raise ValueError("certificate qualification scope is unsupported")
    extension_fields = (
        "assessment_epoch", "trigger_event_type", "measurement_id",
        "measurement_point", "time_s", "remaining_time_ms", "max_active_agents",
        "roster_version", "capability_versions", "current_agent_ids",
        "resource_versions", "deployment_change_versions",
    )
    domains = []
    for row in extension_input["run_state_domains"]:
        # Collection rows carry collection-only metadata (for example the
        # trigger event sequence); the extension declares exactly the frozen
        # run-state domain vocabulary.
        domain = {key: deepcopy(row[key]) for key in extension_fields}
        epoch = domain["assessment_epoch"]
        facts = established_facts_by_epoch.get(epoch)
        if facts is None:
            raise ValueError(f"no established facts collected for epoch {epoch}")
        facts = sorted(set(facts))
        unknown = [fact for fact in facts if fact not in RUNTIME_EPOCH_FACTS]
        if unknown:
            raise ValueError(f"epoch {epoch} carries unknown facts: {unknown}")
        domain["measurement_point"] = {
            "id": domain["measurement_id"],
            "trigger": domain["trigger_event_type"],
            "required_true": facts,
            "required_false": [],
            "varying_facts": [fact for fact in RUNTIME_EPOCH_FACTS
                              if fact not in facts],
        }
        domains.append(domain)
    if not domains:
        raise ValueError("extension input carries no run-state domains")
    prefix = "fixture:" if scope == "synthetic_fixture" else "native:"
    trials: list[dict[str, Any]] = [] if scope == "synthetic_fixture" else [{
        "host": host,
        "seed": seed,
        "raw_archive_sha256": raw_archive_sha256,
        "runtime_profile_sha256": runtime_profile_sha256,
    }]
    extension = {
        "schema": EXTENSION_SCHEMA,
        "version": EXTENSION_VERSION,
        "visibility": "evaluator_only",
        "bound_certificate_sha256": extension_input["bound_certificate_sha256"],
        "episode_id": extension_input["episode_id"],
        "qualification": {
            "scope": scope,
            "evidence_refs": [f"{prefix}reassessment:{raw_archive_sha256}"],
            "extension_manifest_sha256": canonical_sha256(extension_input),
            "allowed_runtime_profile_sha256": [runtime_profile_sha256],
            "extension_trials": deepcopy(trials),
        },
        "run_state_domains": domains,
        "registration_sha256": "",
    }
    extension["registration_sha256"] = _freeze_extension_registration(
        extension, certificate)["registration_sha256"]
    digest = canonical_sha256(extension)
    validate_restaurant_reassessment_extension(
        extension, expected_extension_sha256=digest, certificate=certificate,
        certificate_sha256=extension_input["bound_certificate_sha256"])
    return {
        "schema": ISSUANCE_SCHEMA,
        "version": 1,
        "visibility": "evaluator_only",
        "extension": deepcopy(extension),
        "extension_sha256": digest,
        "bound_certificate_sha256": extension_input["bound_certificate_sha256"],
        "episode_id": extension_input["episode_id"],
        "raw_archive_sha256": raw_archive_sha256,
        "runtime_profile_sha256": runtime_profile_sha256,
        "source_manifest_caveat": (
            "the raw collection archive was produced on the current working "
            "tree; the certificate's execution dependency manifest matches the "
            "Stage60 tree, so the initial epoch of this episode fails closed "
            "and the extension evidence covers only the collected runtime "
            "epochs under the pinned runtime profile"),
        **CLOSED_VALIDATION_FLAGS,
    }


def _issue_command(args: argparse.Namespace) -> int:
    certificate = _load(args.certificate)
    if canonical_sha256(certificate) != args.certificate_sha256:
        raise ValueError("certificate differs from its pinned digest")
    run_dir = args.run_dir
    collection = _load(run_dir / "reassessment-collection.json")
    review = review_reassessment_collection(
        collection, expected_episode_id=collection["episode_id"])
    review_path = args.review_output or args.output.with_name(
        args.output.name + ".review.json")
    if review_path.exists():
        raise FileExistsError(f"refusing to overwrite: {review_path}")
    _write(review_path, review)
    if review["status"] != "verified":
        raise ValueError(f"collection review failed: {review['quality_failures']}")
    extension_input = build_extension_input(
        collection, review, certificate_sha256=args.certificate_sha256)
    facts: dict[int, list[str]] = {}
    for path in sorted((run_dir / "reassessment-epochs").glob("epoch-*.json")):
        record = _load(path)
        facts[int(record["assessment_epoch"])] = list(record["established_facts"])
    if sorted(facts) != sorted(domain["assessment_epoch"]
                               for domain in collection["run_state_domains"]):
        raise ValueError("collected epoch records do not cover the collection")
    inputs = _load(run_dir / "initial-oracle-inputs.json")
    profile = inputs["authorities"]["runtime_profile_sha256"]
    raw = _raw_archive_digest(run_dir)
    allowed = certificate["qualification"]["allowed_runtime_profile_sha256"]
    if profile not in allowed:
        # Fail closed at the last issuer-side step: the collection ran under a
        # runtime profile the pinned certificate never calibrated, so binding
        # an extension to that certificate here would fabricate qualification.
        # Everything the issuer needs stays archived one step before issuance.
        result = {
            "schema": ISSUANCE_SCHEMA,
            "version": 1,
            "visibility": "evaluator_only",
            "status": "blocked_runtime_profile_outside_certificate_domain",
            "episode_id": collection["episode_id"],
            "bound_certificate_sha256": args.certificate_sha256,
            "extension_input": extension_input,
            "established_facts_by_epoch": {str(key): sorted(value)
                                           for key, value in sorted(facts.items())},
            "review_digest": review["review_digest"],
            "collection_runtime_profile_sha256": profile,
            "collection_runtime_profile": deepcopy(inputs["runtime_profile"]),
            "certificate_allowed_runtime_profile_sha256": list(allowed),
            "raw_files": raw["raw_files"],
            "raw_archive_sha256": raw["raw_archive_sha256"],
            "missing_materials": [
                "an initial positive certificate (or a revision of "
                f"{args.certificate_sha256}) whose "
                "allowed_runtime_profile_sha256 admits the collection runtime "
                f"profile {profile} (device "
                f"{inputs['runtime_profile'].get('device')}); the Stage60 v2 "
                "certificate calibrated cpu-physics profiles only",
                "the collection-tree execution dependency manifest still "
                "differs from the certificate manifest (post-Stage60 "
                "demo/benchmark source drift), so the episode's initial epoch "
                "fails closed regardless of device",
            ],
            **CLOSED_VALIDATION_FLAGS,
        }
        _write(args.output, result)
        print(json.dumps({"status": result["status"],
                          "collection_runtime_profile_sha256": profile,
                          "review_digest": review["review_digest"],
                          "raw_archive_sha256": raw["raw_archive_sha256"]}))
        return 1
    result = issue_reassessment_extension(
        extension_input, certificate=certificate, established_facts_by_epoch=facts,
        host=args.host, seed=args.seed,
        raw_archive_sha256=raw["raw_archive_sha256"],
        runtime_profile_sha256=profile)
    result["review_digest"] = review["review_digest"]
    result["raw_files"] = raw["raw_files"]
    _write(args.output, result)
    print(json.dumps({"extension_sha256": result["extension_sha256"],
                      "review_digest": review["review_digest"],
                      "raw_archive_sha256": raw["raw_archive_sha256"],
                      "runtime_profile_sha256": profile}))
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    certificate = _load(args.certificate)
    if canonical_sha256(certificate) != args.certificate_sha256:
        raise ValueError("certificate differs from its pinned digest")
    extensions: list[dict[str, Any]] = []
    pins: list[str] = []
    extension_sha256 = None
    if args.extension is not None:
        issuance = _load(args.extension)
        if issuance.get("schema") != ISSUANCE_SCHEMA:
            raise ValueError("extension file is not a reassessment issuance document")
        extension = issuance["extension"]
        if canonical_sha256(extension) != issuance["extension_sha256"]:
            raise ValueError("extension differs from its recorded digest")
        extensions = [extension]
        pins = [issuance["extension_sha256"]]
        extension_sha256 = issuance["extension_sha256"]
    authorities = _load(args.authorities)["authorities"]
    episode_id = _load(args.run_dir / "reassessment-collection.json")["episode_id"]
    oracle = RestaurantReassessmentOracle(
        certificate, expected_certificate_sha256=args.certificate_sha256,
        trusted_authorities=authorities, extensions=extensions,
        expected_extension_sha256=pins)
    results = []
    for path in sorted(args.run_dir.glob("reassessment-epochs/epoch-*.json")):
        record = _load(path)
        boundary = record["oracle_boundary_session_report"]
        results.append({
            "assessment_epoch": record["assessment_epoch"],
            "measurement_id": record["measurement_id"],
            "established_facts": record["established_facts"],
            "evaluation": oracle(episode_id, {"session": boundary}),
        })
    report = oracle.reassessment_report()
    result = {
        "schema": VERIFICATION_SCHEMA,
        "version": 1,
        "visibility": "evaluator_only",
        "episode_id": episode_id,
        "extension_sha256": extension_sha256,
        "authorities_runtime_profile_sha256": authorities["runtime_profile_sha256"],
        "configuration_status": report["configuration_status"],
        "epoch_results": results,
        "verified_label_count": sum(
            row["evaluation"]["oracle_status"] == "verified" for row in results),
        "feasibility_labels": [row["evaluation"]["feasibility_label"]
                               for row in results],
        "reassessment_report": report,
        **CLOSED_VALIDATION_FLAGS,
    }
    _write(args.output, result)
    print(json.dumps({"configuration_status": report["configuration_status"],
                      "feasibility_labels": result["feasibility_labels"]}))
    return 0


def _run_command(args: argparse.Namespace) -> int:
    from .goal_restaurant_public_run import prepare_request

    for name in ("warmup_ticks", "max_ticks"):
        value = getattr(args, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    script = parse_injection_script(args.inject)
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing archive: {args.output}")
    request = prepare_request(args)
    args.output.mkdir(parents=True, exist_ok=False)
    _write(args.output / "request.json", request)
    _write(args.output / "injection-script.json", {
        "schema": INJECTION_SCHEMA, "version": INJECTION_VERSION,
        "visibility": "evaluator_only",
        "episode_id": args.episode_id,
        "script": [event.summary() for event in script], **CLOSED_VALIDATION_FLAGS})
    try:
        from tools.assets.prepare_restaurant_benchmark_assets import inspect_candidate
        assets = inspect_candidate(args.products_root.resolve())
        _write(args.output / "asset-preflight.json", assets)
        if assets["status"] != "ready":
            raise RuntimeError("frozen Restaurant asset closure is not ready")
        if request.get("initial_oracle_inputs_requested"):
            from .goal_restaurant_oracle_inputs import collect_initial_oracle_inputs
            _write(args.output / "initial-oracle-inputs.json",
                   collect_initial_oracle_inputs(args, request, assets))
        result = run_native_with_injections(args, request, script)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        _write(args.output / "failure.json", {
            "status": "diagnostic_runner_error", "type": type(exc).__name__,
            "error": str(exc), "traceback": traceback.format_exc(),
            "participant_failure_inferred": False, **CLOSED_VALIDATION_FLAGS})
        raise


def _parse_inject(value: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for part in value.split(","):
        key, separator, item = part.partition("=")
        if not separator or not key:
            raise argparse.ArgumentTypeError(f"invalid inject fragment: {part!r}")
        if key == "at":
            try:
                row["at_s"] = float(item)
            except ValueError as exc:
                raise argparse.ArgumentTypeError(f"invalid at value: {item!r}") from exc
        elif key in ("type", "event_type"):
            row["event_type"] = item
        elif key == "agent":
            row["agent_id"] = item
        elif key == "remove":
            row["remove_capabilities"] = item.split("+")
        else:
            raise argparse.ArgumentTypeError(f"unknown inject key: {key!r}")
    return row


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="native episode with scripted injections")
    run.add_argument("--products-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True,
                     help="new directory; never overwritten")
    run.add_argument("--episode-id", required=True)
    run.add_argument("--communication-contract", type=Path, required=True)
    run.add_argument("--publication-treatment", action="store_true")
    run.add_argument("--initial-oracle-inputs", action="store_true")
    run.add_argument("--initial-oracle-certificate", type=Path)
    run.add_argument("--initial-oracle-sha256")
    run.add_argument("--inject", type=_parse_inject, action="append", required=True,
                     help="at=<s>,type=member_exit|capability_change,agent=<id>"
                          "[,remove=cap1+cap2]; milestones fail closed")
    run.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    run.add_argument("--seed", type=int, default=17)
    run.add_argument("--warmup-ticks", type=int, default=240)
    run.add_argument("--max-ticks", type=int, default=4200)

    issue = commands.add_parser("issue", help="offline extension issuance")
    issue.add_argument("--run-dir", type=Path, required=True)
    issue.add_argument("--certificate", type=Path, required=True)
    issue.add_argument("--certificate-sha256", required=True)
    issue.add_argument("--host", default="ljy")
    issue.add_argument("--seed", type=int, required=True)
    issue.add_argument("--output", type=Path, required=True,
                       help="new issuance JSON path; never overwritten")
    issue.add_argument("--review-output", type=Path)

    verify = commands.add_parser(
        "verify", help="offline end-to-end oracle replay over boundary reports")
    verify.add_argument("--run-dir", type=Path, required=True)
    verify.add_argument("--certificate", type=Path, required=True)
    verify.add_argument("--certificate-sha256", required=True)
    verify.add_argument("--authorities", type=Path, required=True,
                        help="initial-oracle-inputs.json carrying the trusted stack")
    verify.add_argument("--extension", type=Path,
                        help="issuance document; omitted replays fail-closed "
                             "without any issued extension")
    verify.add_argument("--output", type=Path, required=True,
                        help="new verification JSON path; never overwritten")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command in {"issue", "verify"} and args.output.exists():
        raise FileExistsError(f"refusing to overwrite: {args.output}")
    if args.command == "run":
        return _run_command(args)
    if args.command == "issue":
        return _issue_command(args)
    if args.command == "verify":
        return _verify_command(args)
    raise ValueError(f"unknown command: {args.command}")


__all__ = [
    "EPOCH_RECORD_SCHEMA", "INJECTION_SCHEMA", "INJECTION_VERSION",
    "ISSUANCE_SCHEMA", "PENDING_EXTENSION_EVALUATOR_REF", "RUNTIME_EPOCH_FACTS",
    "SUPPORTED_EVENT_TYPES", "ScriptedEvent", "ScriptedReassessmentInjector",
    "VERIFICATION_SCHEMA", "established_readiness_facts",
    "issue_reassessment_extension", "main", "parse_injection_script",
    "run_native_with_injections",
]


if __name__ == "__main__":
    raise SystemExit(main())
