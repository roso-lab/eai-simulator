"""Versioned autonomous City entry for an external GoalAlgorithmAdapter.

This opt-in path gives the adapter only the public City task/API and public
GoalSession events.  It never builds or passes the maintained witness plan,
task decomposition, timing, hidden evaluator package, native context, or
backend evidence.  The complete three-robot v1 City pool remains available for
the participant's own team selection and plan.

The public task is upgraded to the version-3 deployment-assessment contract
and every run enables the GoalSession audit.  Until a separately certified
native feasibility oracle is supplied, the hidden assessment record remains a
platform gap (failed/null/invalid) rather than fabricated verification.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from .goal_algorithm_adapter import (
    GoalAlgorithmAdapter,
    validate_goal_adapter_manifest,
)
from .goal_city_episode import (
    DEFAULT_CITY_EPISODE_BINDING,
    CityNativeGoalBackend,
    _plain_evidence,
    _write_json,
    build_acceptance_evidence,
    city_business_evaluator,
    city_robot_regions,
    stamp_arrival_times,
)
from .goal_deployment_assessment import enable_deployment_assessment
from .goal_driven_contract import build_goal_participant_api
from .goal_driven_tasks import build_goal_task_package
from .goal_episode_runner import FormalGoalEpisodeRunner
from .provenance import file_sha256
from demo.city_benchmark.task_logic import (
    default_deadline_policy,
    evaluate_city_acceptance,
)


CITY_AUTONOMOUS_ENTRY_SCHEMA = "eai.benchmark.goal_city_autonomous_entry"
CITY_AUTONOMOUS_ENTRY_VERSION = 1
CITY_AUTONOMOUS_POOL = "v1"
CITY_AUTONOMOUS_ROBOT_IDS = ("go2_1", "carter_1", "iris_1")
CITY_AUTONOMOUS_ORACLE_GAP_REF = (
    "demo.benchmark.goal_city_autonomous.native_feasibility_unverified")
CITY_PROGRESS_SOURCE_SCHEMA = "eai.benchmark.goal_city_progress_sources"
CITY_PROGRESS_EVALUATOR_REF = (
    "demo.benchmark.goal_city_autonomous.read_only_acceptance_progress")

_CITY_PARTIAL_ACCEPTANCE_PROJECTIONS = {
    "incident_assessed": {
        "sections": ("incident_assessed",),
        "required_outcomes": ("incident_assessed",),
    },
    "response_dispatch": {
        "sections": (
            "responder_dispatch",
            "incident_assessed.observation_delivery_time_s",
        ),
        "required_outcomes": ("response_dispatch",),
    },
    "exposed_people_accounted_for": {
        "sections": ("people",),
        "required_outcomes": ("exposed_people_accounted_for",),
    },
    "secondary_traffic_protected": {
        "sections": ("traffic",),
        "required_outcomes": ("secondary_traffic_protected",),
    },
    "response_access_preserved": {
        "sections": ("incident_report", "response_access"),
        "required_outcomes": ("response_access_preserved",),
    },
}

AssessmentEvaluator = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
BusinessEvaluator = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
ProgressReader = Callable[..., Mapping[str, Any]]


class _ManifestBoundAdapter:
    """Freeze one declared identity while forwarding optional adapter methods."""

    def __init__(self, adapter: GoalAlgorithmAdapter):
        self._adapter = adapter
        self._manifest = validate_goal_adapter_manifest(adapter.manifest())

    def manifest(self) -> Mapping[str, Any]:
        return deepcopy(self._manifest)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)


def _bind_adapter(adapter: GoalAlgorithmAdapter) -> _ManifestBoundAdapter:
    return adapter if isinstance(adapter, _ManifestBoundAdapter) else _ManifestBoundAdapter(adapter)


def _source_hashes() -> dict[str, Any]:
    from demo.city_benchmark.service_probe import _hashes

    # Include the full benchmark Python surface so a transitive contract,
    # communication or validator edit cannot evade the entry/backend guard.
    paths = sorted(Path(__file__).parent.glob("*.py"))
    city_root = Path(__file__).parent.parent / "city_benchmark"
    city_paths = sorted([*city_root.glob("*.py"), *(city_root / "scene").glob("*.py")])
    return {"goal_modules": {path.name: file_sha256(path) for path in paths},
            "city_modules": {str(path.relative_to(city_root)): file_sha256(path)
                             for path in city_paths},
            "service_probe_sources": _hashes()}


def _has_pending_timestamp(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            (key.endswith("_time_s") and item is None)
            or _has_pending_timestamp(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_pending_timestamp(item) for item in value)
    return False


def _validate_nonnull_timestamps(value: Any, *, path: str = "acceptance_envelope") -> None:
    """Reject malformed measured timestamps while retaining null as pending."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if isinstance(key, str) and key.endswith("_time_s") and item is not None:
                if (isinstance(item, bool) or not isinstance(item, (int, float))
                        or not math.isfinite(item) or item < 0):
                    raise ValueError(
                        f"{item_path} must be a finite nonnegative time or null")
            _validate_nonnull_timestamps(item, path=item_path)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_nonnull_timestamps(item, path=f"{path}[{index}]")


def _strict_json_evidence(
        value: Any, *, path: tuple[str | int, ...] = (),
        special_floats: list[dict[str, Any]] | None = None) -> Any:
    """Encode rejected non-finite floats without losing their path or kind."""
    if special_floats is None:
        special_floats = []
    if isinstance(value, Mapping):
        return {
            str(key): _strict_json_evidence(
                item, path=(*path, str(key)), special_floats=special_floats)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _strict_json_evidence(
                item, path=(*path, index), special_floats=special_floats)
            for index, item in enumerate(value)
        ]
    if isinstance(value, float) and not math.isfinite(value):
        kind = "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
        special_floats.append({"path": list(path), "value": kind})
        return {"nonfinite_float": kind}
    return deepcopy(value)


def evaluate_city_partial_acceptance(
        acceptance_envelope: Mapping[str, Any], *,
        expected_affected_person_ids: set[str],
        expected_following_vehicle_ids: set[str],
        outcome_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Evaluate independently complete City milestones with explicit projections.

    Null timestamps remain pending evidence. Every non-null timestamp in the raw
    envelope is validated before projection so malformed evidence cannot hide
    behind an unrelated pending service. Each reviewed projection is passed to
    the canonical City acceptance evaluator unchanged.
    """
    if not isinstance(acceptance_envelope, Mapping):
        raise ValueError("city acceptance evidence must be an object")
    _validate_nonnull_timestamps(acceptance_envelope)
    public_outcomes = tuple(
        outcome_id for outcome_id in _CITY_PARTIAL_ACCEPTANCE_PROJECTIONS
        if outcome_id != "response_dispatch")
    requested = (public_outcomes
                 if outcome_ids is None else tuple(outcome_ids))
    if len(requested) != len(set(requested)):
        raise ValueError("City partial acceptance outcome ids must be unique")
    unknown = sorted(set(requested) - set(public_outcomes))
    if unknown:
        raise ValueError(f"unsupported City partial acceptance outcomes: {unknown}")

    partial_reviews: dict[str, dict[str, Any]] = {}
    predicate_vector: dict[str, bool | None] = {}
    policy = default_deadline_policy()
    review_outcomes = (*requested, "response_dispatch")
    review_satisfaction: dict[str, bool | None] = {}
    for outcome_id in review_outcomes:
        contract = _CITY_PARTIAL_ACCEPTANCE_PROJECTIONS[outcome_id]
        sections = contract["sections"]
        required_outcomes = contract["required_outcomes"]
        missing_dependencies: list[str] = []
        if outcome_id == "response_dispatch":
            projection = {}
            if "responder_dispatch" in acceptance_envelope:
                projection["responder_dispatch"] = deepcopy(
                    acceptance_envelope["responder_dispatch"])
            incident = acceptance_envelope.get("incident_assessed")
            if (isinstance(incident, Mapping)
                    and "observation_delivery_time_s" in incident):
                projection["incident_assessed"] = {
                    "observation_delivery_time_s": deepcopy(
                        incident["observation_delivery_time_s"]),
                }
            else:
                missing_dependencies.append(
                    "incident_assessed.observation_delivery_time_s")
        else:
            projection = {
                section: deepcopy(acceptance_envelope[section])
                for section in sections if section in acceptance_envelope
            }
        if missing_dependencies or _has_pending_timestamp(projection):
            status = "pending"
            review = None
            satisfied = None
        else:
            review = evaluate_city_acceptance(
                projection,
                policy,
                expected_affected_person_ids=expected_affected_person_ids,
                expected_following_vehicle_ids=expected_following_vehicle_ids,
            )
            reviewed_outcomes = review.get("outcomes") or {}
            satisfied = bool(
                not review.get("failure_codes")
                and all(
                    isinstance(reviewed_outcomes.get(required_id), Mapping)
                    and reviewed_outcomes[required_id].get("success") is True
                    for required_id in required_outcomes
                )
            )
            status = "satisfied" if satisfied else "not_satisfied"
            if not satisfied:
                satisfied = None
        partial_reviews[outcome_id] = {
            "sections": list(sections),
            "required_evaluator_outcomes": list(required_outcomes),
            "missing_dependencies": missing_dependencies,
            "projection": projection,
            "status": status,
            "budget_decision": {
                "response_budget_s": policy["response_budget_s"],
                "status": (
                    "pending" if review is None else
                    "exceeded" if "response_budget_exceeded" in review["failure_codes"]
                    else "within_budget"),
            },
            "review": deepcopy(review),
        }
        review_satisfaction[outcome_id] = satisfied
        if outcome_id in requested:
            predicate_vector[outcome_id] = satisfied
    complete_public_request = (
        len(requested) == len(public_outcomes)
        and set(requested) == set(public_outcomes))
    public_complete = (
        complete_public_request
        and all(value is True for value in predicate_vector.values()))
    dispatch_complete = review_satisfaction["response_dispatch"] is True
    return {
        "partial_acceptance_reviews": partial_reviews,
        "predicate_vector": predicate_vector,
        "hard_conditions": {
            "response_dispatch": review_satisfaction["response_dispatch"],
        },
        "whole_task_completion_available": {
            "evidence_available": public_complete and dispatch_complete,
            "meaning": (
                "all public progress predicates and the hidden response_dispatch "
                "condition have positive projected evidence; this is not a "
                "mission-success or scoring claim"),
            "complete_public_predicate_set_requested": complete_public_request,
            "all_public_predicates_satisfied": public_complete,
            "response_dispatch_satisfied": dispatch_complete,
            "mission_success": None,
        },
    }


def _autonomous_entry(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": CITY_AUTONOMOUS_ENTRY_SCHEMA,
        "version": CITY_AUTONOMOUS_ENTRY_VERSION,
        "adapter_manifest": deepcopy(dict(manifest)),
        "candidate_robot_ids": list(CITY_AUTONOMOUS_ROBOT_IDS),
        "deployment_assessment_required": True,
        "session_audit_required": True,
        "author_plan_supplied": False,
        "hidden_package_supplied": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }


def _refresh_scoring_archive_index(
    output_root: str | Path,
    public_task: Mapping[str, Any],
    *,
    episode_id: str,
) -> None:
    """Refresh sidecar hashes without dropping runner-added metadata fields."""
    from .goal_scoring_adapter import build_scoring_adapter_metadata

    root = Path(output_root)
    path = root / "scoring_adapter_metadata.json"
    existing = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError("scoring adapter metadata must be an object")
    rebuilt = build_scoring_adapter_metadata(
        public_task,
        episode_id=episode_id,
        output_root=root,
    )
    # The runner appends episode_result after its initial metadata build. Keep
    # that and any other runner-owned fields byte-for-value while refreshing
    # only the index after this City layer adds evaluator-side artifacts.
    existing["archive_index"] = rebuilt["archive_index"]
    _write_json(path, existing)


def build_city_autonomous_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the public v3 contract while preserving the complete v1 pool."""
    package = build_goal_task_package("city", city_roster_profile="bound_native_v1")
    public = enable_deployment_assessment(package["public_task"])
    robot_ids = tuple(row["id"] for row in public["candidate_robot_pool"])
    if robot_ids != CITY_AUTONOMOUS_ROBOT_IDS:
        raise ValueError("autonomous City entry requires the complete three-robot pool")
    return public, build_goal_participant_api(public)


class CityAcceptanceProgressReader:
    """Read-only native acceptance snapshots for runner progress boundaries.

    False acceptance at an intermediate boundary may mean only that evidence
    has not arrived yet, so this reader reports ``None`` unless the frozen
    acceptance predicate is already positively satisfied. Every sample keeps
    its own envelope/review snapshot in ``city_progress_sources.json``.
    """

    def __init__(self, evidence_log: list[Mapping[str, Any]],
                 ctx: Mapping[str, Any], output_root: str | Path, *,
                 clock_epoch_s: float = 0.0):
        if (isinstance(clock_epoch_s, bool)
                or not isinstance(clock_epoch_s, (int, float))
                or not math.isfinite(clock_epoch_s) or clock_epoch_s < 0):
            raise ValueError("clock_epoch_s must be finite and nonnegative")
        self._evidence_log = evidence_log
        self._ctx = ctx
        self._path = Path(output_root) / "city_progress_sources.json"
        self._episode_id: str | None = None
        self._samples: list[dict[str, Any]] = []
        self._clock_epoch_s = clock_epoch_s

    def __call__(self, *, episode_id: str, boundary: str,
                 trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        if self._episode_id is None:
            self._episode_id = episode_id
        elif self._episode_id != episode_id:
            raise ValueError("City progress reader cannot cross episode identity")
        native_time_s = float(self._ctx["city"].elapsed)
        time_s = native_time_s - self._clock_epoch_s
        envelope = build_acceptance_evidence(
            self._evidence_log, self._ctx, clock_epoch_s=self._clock_epoch_s)
        error = None
        acceptance_error: Exception | None = None
        acceptance = None
        partial_acceptance = None
        required = [
            row["id"] for row in
            trusted_report["session"]["business_outcomes"]
        ]
        # The raw native envelope intentionally retains null timestamps for
        # pending readbacks. Independently complete milestones are evaluated
        # through explicit projections; malformed measured timestamps still
        # fail the entire sample as infrastructure evidence.
        try:
            partial_acceptance = evaluate_city_partial_acceptance(
                envelope,
                expected_affected_person_ids=set(self._ctx["roster_ids"]),
                expected_following_vehicle_ids=set(self._ctx["vehicle_ids"]),
                outcome_ids=required,
            )
            # Preserve the historical complete-envelope review for archive
            # consumers once no timestamp remains pending.
            if not _has_pending_timestamp(envelope):
                acceptance = evaluate_city_acceptance(
                    envelope,
                    default_deadline_policy(),
                    expected_affected_person_ids=set(self._ctx["roster_ids"]),
                    expected_following_vehicle_ids=set(self._ctx["vehicle_ids"]),
                )
        except Exception as exc:
            acceptance_error = exc
            error = f"{type(exc).__name__}: {exc}"
            # A boundary with any infrastructure evaluation failure cannot
            # retain a partially trusted projected vector.
            partial_acceptance = None
        source_index = len(self._samples)
        special_floats: list[dict[str, Any]] = []
        archived_envelope = _strict_json_evidence(
            envelope, special_floats=special_floats)
        self._samples.append({
            "index": source_index,
            "episode_id": episode_id,
            "boundary": boundary,
            "native_time_s": native_time_s,
            "clock_epoch_s": self._clock_epoch_s,
            "time_s": time_s,
            "acceptance_envelope": archived_envelope,
            "acceptance_envelope_special_floats": special_floats,
            "acceptance_review": deepcopy(acceptance),
            "expected_affected_person_ids": sorted(self._ctx["roster_ids"]),
            "expected_following_vehicle_ids": sorted(self._ctx["vehicle_ids"]),
            "partial_acceptance_reviews": deepcopy(
                (partial_acceptance or {}).get("partial_acceptance_reviews")),
            "predicate_vector": deepcopy(
                (partial_acceptance or {}).get("predicate_vector")),
            "hard_conditions": deepcopy(
                (partial_acceptance or {}).get("hard_conditions")),
            "whole_task_completion_available": deepcopy(
                (partial_acceptance or {}).get(
                    "whole_task_completion_available")),
            "error": error,
        })
        if acceptance_error is not None:
            raise RuntimeError(
                "City acceptance progress evaluation failed") from acceptance_error
        predicates: dict[str, dict[str, Any]] = {}
        for outcome_id in required:
            satisfied = (partial_acceptance or {})["predicate_vector"][outcome_id]
            refs = ([f"city_progress_sources.json#sample={source_index}"
                     f"&outcome={outcome_id}"] if satisfied is True else [])
            predicates[outcome_id] = {
                "satisfied": satisfied,
                "evidence_refs": refs,
            }
        return {
            "episode_id": episode_id,
            "time_s": time_s,
            "clock_source": "native_simulation",
            "evaluator_ref": CITY_PROGRESS_EVALUATOR_REF,
            "predicates": predicates,
        }

    def write(self) -> None:
        if self._episode_id is None:
            return
        _write_json(self._path, {
            "schema": CITY_PROGRESS_SOURCE_SCHEMA,
            "version": 2,
            "episode_id": self._episode_id,
            "clock_source": "native_simulation",
            "evaluator_ref": CITY_PROGRESS_EVALUATOR_REF,
            "sampling_scope": "runner_boundaries_read_only_native_acceptance",
            "clock_epoch_s": self._clock_epoch_s,
            "samples": deepcopy(self._samples),
            "physical_source_authenticated": False,
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
        })


def city_autonomous_platform_gap_evaluator(
    episode_id: str, trusted_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Archive an uncertified platform gap without inventing feasibility."""
    del episode_id
    gate = trusted_report["session"]["deployment_assessment"]
    return {
        "assessment_id": gate["assessment_id"],
        "assessment_epoch": gate["assessment_epoch"],
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": [],
        "evaluator_ref": CITY_AUTONOMOUS_ORACLE_GAP_REF,
    }


def run_city_autonomous_session(
    adapter: GoalAlgorithmAdapter,
    backend: Any,
    *,
    output_root: str | Path,
    evaluator: BusinessEvaluator | None = None,
    assessment_evaluator: AssessmentEvaluator | None = None,
    progress_reader: ProgressReader | None = None,
    episode_id: str | None = None,
    communication_runtime: Any = None,
    execution_observer: Any = None,
    downlink: Any = None,
) -> dict[str, Any]:
    """Run an external adapter through the real formal runner boundary.

    ``backend`` and evaluator hooks are trusted host inputs. They are never
    passed to the participant. This split also permits CPU-only contract tests
    with a controlled backend while the native wrapper below supplies
    ``CityNativeGoalBackend`` and the City acceptance evaluator.
    """
    adapter = _bind_adapter(adapter)
    manifest = adapter.manifest()
    public, participant_api = build_city_autonomous_contract()
    runner_kwargs: dict[str, Any] = {
        "output_root": output_root,
        "episode_id": episode_id,
        "evaluator": evaluator,
        "assessment_evaluator": (
            assessment_evaluator or city_autonomous_platform_gap_evaluator),
        "session_audit": True,
        "shared_communication_runtime": communication_runtime,
        "execution_observer": execution_observer,
        "downlink": downlink,
    }
    if progress_reader is not None:
        # Stage39's trusted progress hook belongs to the runner. Keeping it
        # optional here preserves compatibility until that host capability is
        # present, while never deriving progress from participant ACKs.
        runner_kwargs["progress_reader"] = progress_reader
    runner = FormalGoalEpisodeRunner(
        public, participant_api, adapter, backend, **runner_kwargs)
    result = runner.run(initial_state={
        "robot_regions": city_robot_regions(public),
        "object_states": {},
    })
    _write_json(
        Path(output_root) / "goal_city_autonomous_entry.json",
        _autonomous_entry(manifest),
    )
    _refresh_scoring_archive_index(
        output_root, public, episode_id=result["episode_id"])
    return result


def run_city_autonomous_context(
    ctx: Mapping[str, Any],
    adapter: GoalAlgorithmAdapter,
    *,
    output_root: str | Path,
    assessment_evaluator: AssessmentEvaluator | None = None,
    progress_reader: ProgressReader | None = None,
    episode_id: str | None = None,
    communication_runtime: Any = None,
    guide_route_provider: Any = None,
    guide_execution_profile: str | None = None,
    assembly_footprint_profile: Mapping[str, Any] | None = None,
    assembly_support_reader: Any = None,
) -> tuple[dict[str, Any], CityNativeGoalBackend]:
    """Bind one prepared native City context to the autonomous session."""
    from demo.city_benchmark.guide_admission import (
        REQUESTED_TARGET_PROFILE,
        REQUESTED_TARGET_PROFILES,
        REQUESTED_TARGET_V2_PROFILE,
    )

    if guide_execution_profile is not None \
            and guide_execution_profile not in REQUESTED_TARGET_PROFILES:
        raise ValueError("unsupported requested-target guidance profile")
    if guide_route_provider is None:
        if (guide_execution_profile is not None
                or assembly_footprint_profile is not None
                or assembly_support_reader is not None):
            raise ValueError("requested-target guidance profile requires a route provider")
        selected_guide_profile = None
    else:
        selected_guide_profile = guide_execution_profile or REQUESTED_TARGET_PROFILE
        if communication_runtime is None:
            raise ValueError("requested-target guidance requires metered communication")
        if selected_guide_profile == REQUESTED_TARGET_V2_PROFILE:
            if assembly_footprint_profile is None or not callable(assembly_support_reader):
                raise ValueError(
                    "requested-target v2 requires footprint and trusted support inputs")
        elif assembly_footprint_profile is not None or assembly_support_reader is not None:
            raise ValueError("assembly footprint inputs require requested-target v2")
    public, _participant_api = build_city_autonomous_contract()
    clock_epoch_s = float(ctx["city"].elapsed)
    if not math.isfinite(clock_epoch_s) or clock_epoch_s < 0:
        raise ValueError("native City epoch must be finite and nonnegative")
    arrival_stamps: dict[str, float] = {}
    native_step = ctx["step"]

    def stamped_step(session, city, actions, count=1):
        native_step(session, city, actions, count)
        stamp_arrival_times(ctx, arrival_stamps)

    # The service probe intentionally supplies a mutable context dictionary.
    # Refuse a read-only lookalike instead of silently losing arrival stamps.
    try:
        ctx["step"] = stamped_step  # type: ignore[index]
    except TypeError as exc:
        raise ValueError("native City context must permit the trusted step wrapper") from exc
    default_progress = None
    try:
        assembly_reader = None
        initial_qualification = None
        if guide_route_provider is not None:
            service = ctx["city"]._episode_service
            service.enable_requested_target_guidance(
                route_provider=guide_route_provider,
                semantic_map_sha256=guide_route_provider.semantic_map_sha256,
                profile=selected_guide_profile,
                assembly_footprint_profile=assembly_footprint_profile,
                assembly_support_reader=assembly_support_reader)
            assembly_reader = service.assembly_affordances
            # Freeze the evaluator's initial evidence before the adapter is
            # initialized or allowed to assess/select/plan. This diagnostic
            # does not supply a feasibility label or participant information.
            from .goal_city_initial_qualification import capture_city_initial_qualification

            episode_id = episode_id or uuid.uuid4().hex
            initial_qualification = capture_city_initial_qualification(
                ctx, public, episode_id=episode_id, clock_epoch_s=clock_epoch_s)
        backend = CityNativeGoalBackend(
            ctx, public_task=public, clock_epoch_s=clock_epoch_s, strict_dispatch_clock=True)
        if progress_reader is None:
            default_progress = CityAcceptanceProgressReader(
                backend.evidence, ctx, output_root, clock_epoch_s=clock_epoch_s)
            progress_reader = default_progress
        if initial_qualification is not None:
            frozen_initial = deepcopy(initial_qualification)
            delegated_progress = progress_reader

            def archive_initial_progress(*, episode_id, boundary, trusted_report):
                # The runner owns output-directory creation. Its initial
                # progress boundary occurs before participant initialize.
                # Persist the earlier capture here; never recapture later.
                if boundary == "initial":
                    _write_json(Path(output_root) / "city_initial_qualification.json", frozen_initial)
                return delegated_progress(
                    episode_id=episode_id, boundary=boundary, trusted_report=trusted_report)

            progress_reader = archive_initial_progress
        observer = downlink = None
        if communication_runtime is not None:
            from .goal_city_runtime_observations import (
                CityRuntimeObservationReader, GoalCityRuntimeIntake)
            from .goal_execution_downlink import GoalExecutionDownlink

            episode_id = episode_id or uuid.uuid4().hex
            observer = GoalCityRuntimeIntake(
                public, episode_id=episode_id, runtime=communication_runtime,
                reader=CityRuntimeObservationReader(
                    ctx, assembly_reader=assembly_reader,
                    assembly_profile=selected_guide_profile),
                native_episode_id=ctx["runner"].adapter.episode_id,
                native_source_id=ctx["runner"].adapter.source_id,
                clock_epoch_s=clock_epoch_s,
                recipient_service_id=ctx["endpoint"]["service_id"])
            downlink = GoalExecutionDownlink(
                public, episode_id=episode_id, runtime=communication_runtime)
        result = run_city_autonomous_session(
            adapter,
            backend,
            output_root=output_root,
            evaluator=city_business_evaluator(
                backend.evidence, ctx, arrival_stamps=arrival_stamps,
                clock_epoch_s=clock_epoch_s),
            assessment_evaluator=assessment_evaluator,
            progress_reader=progress_reader,
            episode_id=episode_id,
            communication_runtime=communication_runtime,
            execution_observer=observer,
            downlink=downlink,
        )
    finally:
        ctx["step"] = native_step  # type: ignore[index]
    if default_progress is not None:
        default_progress.write()
        _refresh_scoring_archive_index(
            output_root, public, episode_id=result["episode_id"])
    return result, backend


def run_city_autonomous_episode(
    output_root: str | Path,
    adapter: GoalAlgorithmAdapter,
    *,
    device: str = "cpu",
    episode_binding: str | Path | None = None,
    assessment_evaluator: AssessmentEvaluator | None = None,
    progress_reader: ProgressReader | None = None,
    episode_id: str | None = None,
    communication_runtime: Any = None,
    guide_semantic_map_path: str | Path | None = None,
    guide_execution_profile: str | None = None,
) -> dict[str, Any]:
    """Run the opt-in requested-target entry against the prepared native probe."""
    from demo.city_benchmark.service_probe import execute_probe, prepare_probe
    from demo.city_benchmark.guide_admission import (
        REQUESTED_TARGET_PROFILE,
        REQUESTED_TARGET_PROFILES,
        REQUESTED_TARGET_V2_PROFILE,
    )

    adapter = _bind_adapter(adapter)
    sources_before = _source_hashes()
    root = Path(output_root).absolute()
    binding = (Path(episode_binding) if episode_binding is not None
               else DEFAULT_CITY_EPISODE_BINDING)
    if guide_execution_profile is not None \
            and guide_execution_profile not in REQUESTED_TARGET_PROFILES:
        raise ValueError("unsupported requested-target guidance profile")
    route_provider = None
    footprint_profile = None
    selected_guide_profile = None
    if guide_semantic_map_path is not None:
        from .goal_city_assembly import CityRequestedTargetRouteProvider

        route_provider = CityRequestedTargetRouteProvider.from_path(guide_semantic_map_path)
        selected_guide_profile = guide_execution_profile or REQUESTED_TARGET_PROFILE
        if communication_runtime is None:
            raise ValueError("requested-target guidance requires metered communication")
        if selected_guide_profile == REQUESTED_TARGET_V2_PROFILE:
            from demo.city_benchmark.assembly_support import (
                resolve_assembly_footprint_profile,
            )

            public, _participant_api = build_city_autonomous_contract()
            footprint_profile = resolve_assembly_footprint_profile(
                semantic_map_path=guide_semantic_map_path,
                expected_semantic_map_sha256=route_provider.semantic_map_sha256,
                public_regions=public["public_world"]["regions"])
    elif guide_execution_profile is not None:
        raise ValueError("requested-target guidance profile requires a semantic map")
    prepare_probe(root, device=device, episode_binding=binding, pool=CITY_AUTONOMOUS_POOL)

    def driver(ctx: Mapping[str, Any]) -> None:
        episode_root = Path(ctx["report_path"]).parent / "goal_autonomous_episode"
        support_reader = None
        if selected_guide_profile == REQUESTED_TARGET_V2_PROFILE:
            from demo.city_benchmark.assembly_support import measure_assembly_supports

            service = ctx["city"]._episode_service
            native_reader = service.region_reader
            public, _participant_api = build_city_autonomous_contract()

            def support_reader():
                return measure_assembly_supports(
                    ctx["city"].stage, native_reader.query,
                    semantic_map_path=guide_semantic_map_path,
                    expected_semantic_map_sha256=route_provider.semantic_map_sha256,
                    public_regions=public["public_world"]["regions"],
                    episode_id=ctx["runner"].adapter.episode_id,
                    source_id=ctx["runner"].adapter.source_id,
                    native_clock=lambda: float(ctx["city"].elapsed),
                    scene_queries_enabled=native_reader.scene_queries_enabled,
                    device=native_reader.device,
                    current_gpu_geometry=native_reader.current_gpu_geometry)
        result, backend = run_city_autonomous_context(
            ctx,
            adapter,
            output_root=episode_root,
            assessment_evaluator=assessment_evaluator,
            progress_reader=progress_reader,
            episode_id=episode_id,
            communication_runtime=communication_runtime,
            guide_route_provider=route_provider,
            guide_execution_profile=selected_guide_profile,
            assembly_footprint_profile=footprint_profile,
            assembly_support_reader=support_reader,
        )
        sources_after = _source_hashes()
        sources_unchanged = sources_before == sources_after
        source_sha256 = sources_before["goal_modules"]["goal_city_autonomous.py"]
        ctx["report"]["trace"]["goal_autonomous_episode"] = {
            "schema": CITY_AUTONOMOUS_ENTRY_SCHEMA,
            "version": CITY_AUTONOMOUS_ENTRY_VERSION,
            "episode_id": result["episode_id"],
            "output_root": str(episode_root),
            "error": result["error"],
            "turns": result["turns"],
            "skill_receipt_count": result["skill_receipt_count"],
            "backend": type(backend).__name__,
            "adapter_manifest": validate_goal_adapter_manifest(adapter.manifest()),
            "candidate_robot_ids": list(CITY_AUTONOMOUS_ROBOT_IDS),
            "author_plan_supplied": False,
            "hidden_package_supplied": False,
            "session_audit_validated": result.get("session_audit_validated") is True,
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
            "source_sha256": source_sha256,
            "sources_unchanged": sources_unchanged,
            "clock_epoch_s": backend.clock_epoch_s,
            "guide_admission_profile": selected_guide_profile or "legacy_bound_target",
            "initial_qualification_artifact": (
                "city_initial_qualification.json" if route_provider else None),
        }
        if not sources_unchanged:
            ctx["report"].update(
                status="blocked", blocker="autonomous City source files changed during execution")
        elif result["error"] is None:
            ctx["report"].update(
                status="completed",
                device_actual=str(ctx["session"].device),
                episode_time_s=float(ctx["city"].elapsed) - backend.clock_epoch_s,
                native_time_s=float(ctx["city"].elapsed),
            )
        else:
            ctx["report"].update(
                status="blocked",
                blocker=f"autonomous goal episode error: {result['error']}",
            )
        _write_json(episode_root / "goal_city_autonomous_source.json", {
            "schema": CITY_AUTONOMOUS_ENTRY_SCHEMA,
            "version": CITY_AUTONOMOUS_ENTRY_VERSION,
            "module": __name__,
            "source_path": str(Path(__file__)),
            "source_sha256": source_sha256,
            "source_hashes_before": sources_before,
            "source_hashes_after": sources_after,
            "sources_unchanged": sources_unchanged,
            "clock_epoch_s": backend.clock_epoch_s,
            "guide_admission_profile": selected_guide_profile or "legacy_bound_target",
            "episode_id": result["episode_id"],
            "candidate_robot_ids": list(CITY_AUTONOMOUS_ROBOT_IDS),
            "author_plan_supplied": False,
            "hidden_package_supplied": False,
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
        })
        _write_json(
            episode_root / "city_acceptance_evidence.json",
            build_acceptance_evidence(
                backend.evidence, ctx, clock_epoch_s=backend.clock_epoch_s),
        )
        _write_json(episode_root / "native_evidence_log.json", {
            "schema": "eai.benchmark.goal_city_autonomous_native_evidence",
            "version": 1,
            "episode_id": result["episode_id"],
            "records": _plain_evidence(deepcopy(backend.evidence)),
        })
        public, _participant_api = build_city_autonomous_contract()
        _refresh_scoring_archive_index(
            episode_root, public, episode_id=result["episode_id"])

    return execute_probe(
        root, device=device, driver=driver, pool=CITY_AUTONOMOUS_POOL)


__all__ = [
    "CITY_AUTONOMOUS_ENTRY_SCHEMA",
    "CITY_AUTONOMOUS_ENTRY_VERSION",
    "CITY_AUTONOMOUS_ORACLE_GAP_REF",
    "CITY_AUTONOMOUS_POOL",
    "CITY_AUTONOMOUS_ROBOT_IDS",
    "CITY_PROGRESS_EVALUATOR_REF",
    "CITY_PROGRESS_SOURCE_SCHEMA",
    "CityAcceptanceProgressReader",
    "build_city_autonomous_contract",
    "city_autonomous_platform_gap_evaluator",
    "evaluate_city_partial_acceptance",
    "run_city_autonomous_context",
    "run_city_autonomous_episode",
    "run_city_autonomous_session",
]
