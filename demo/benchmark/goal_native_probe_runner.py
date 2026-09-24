"""Native admission probe harness for the remaining-task readers.

This module is the offline, pure-Python assembly point between the five scene
readers (Modern House, City, Warehouse, Factory, Restaurant) and
host-installed native probes.  It defines protocols and wiring only: nothing here imports Isaac,
ROS, or torch.  A real PhysX/USD probe is a host-installed callable that
closes over the caller-owned simulator session (ljy / headless execution);
offline development uses deterministic fake probes that exercise exactly the
same grounding -> oracle -> certificate-gate -> marker chain.

Wiring model per scene (:class:`NativeProbeSpec`):

- ``registration_builder`` / ``contract_builder`` freeze the oracle model and
  the trusted state contract exactly as the synthetic development backend
  does, so a native run and a synthetic run over the same facts must produce
  the same solver answer.
- ``source_probe_factories`` (or the ``probe_factories`` argument to
  :func:`build_probe_harness`) maps each registered source id to a callable
  invoked once per measurement with the trusted state observation as its
  native query context.  It must return ``{fact_id: bool}`` covering exactly
  that source's bound predicates.  The harness passes these directly as the
  ``native_probes`` of the scene's ``make_*_source_readers``.
- ``native_state_provider`` (per :meth:`NativeProbeHarness.run_measurement`
  call) supplies the native clock time, entity versions, aggregated true
  facts, and the clock binding.  ``ground_state`` independently cross-checks
  the per-source probe output against the provider's aggregated truth, so a
  native admission needs both paths to agree.

Failure policy (fail-closed, never a third feasibility label):

- a probe or native state provider exception classifies as ``probe_error``;
- a native clock contract violation classifies as ``native_clock_violation``;
- an entity version set that does not match the frozen state contract
  classifies as ``version_binding_mismatch``.

All three return ``oracle_status=failed`` with ``feasibility_label=null``;
they can never masquerade as a solver ``not_deployable`` answer.  Every
result is stamped with a development marker (release flags stay false);
archived evidence is diagnostic only until native admission completes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from .development_track_marker import (
    FACTORY_FIXTURE_HASH_MISMATCH,
    build_development_marker,
    merge_development_markers,
    stamp_evidence_record,
)
from .goal_alternative_path_certificate import gate_not_deployable_result
from .goal_reader_city import (
    CITY_DEV_CASES,
    CITY_ENTITY_IDS,
    CITY_MEASUREMENT_POINTS,
    CITY_NATIVE_BLOCKER,
    CITY_PREDICATE_BINDINGS,
    CITY_SCENE_KEY,
    CITY_SOURCE_VERSIONS,
    build_city_development_certificate,
    build_city_registration,
    build_city_state_contract,
    make_city_source_readers,
)
from .goal_reader_factory import (
    FACTORY_DEV_CASES,
    FACTORY_ENTITY_IDS,
    FACTORY_MEASUREMENT_POINTS,
    FACTORY_PREDICATE_BINDINGS,
    FACTORY_SCENE_KEY,
    FACTORY_SOURCE_VERSIONS,
    build_factory_development_certificate,
    build_factory_registration,
    build_factory_state_contract,
    make_factory_source_readers,
)
from .goal_reader_house import (
    HOUSE_DEV_CASES,
    HOUSE_ENTITY_IDS,
    HOUSE_MEASUREMENT_POINTS,
    HOUSE_NATIVE_BLOCKER,
    HOUSE_PREDICATE_BINDINGS,
    HOUSE_SCENE_KEY,
    HOUSE_SOURCE_VERSIONS,
    build_house_development_certificate,
    build_house_registration,
    build_house_state_contract,
    make_house_source_readers,
)
from .goal_reader_restaurant import (
    RESTAURANT_DEV_CASES,
    RESTAURANT_ENTITY_IDS,
    RESTAURANT_MEASUREMENT_POINTS,
    RESTAURANT_NATIVE_BLOCKER,
    RESTAURANT_PREDICATE_BINDINGS,
    RESTAURANT_SCENE_KEY,
    RESTAURANT_SOURCE_VERSIONS,
    build_restaurant_development_certificate,
    build_restaurant_registration,
    build_restaurant_state_contract,
    make_restaurant_source_readers,
)
from .goal_reader_warehouse import (
    WAREHOUSE_DEV_CASES,
    WAREHOUSE_ENTITY_IDS,
    WAREHOUSE_MEASUREMENT_POINTS,
    WAREHOUSE_NATIVE_BLOCKER,
    WAREHOUSE_PREDICATE_BINDINGS,
    WAREHOUSE_SCENE_KEY,
    WAREHOUSE_SOURCE_VERSIONS,
    build_warehouse_development_certificate,
    build_warehouse_registration,
    build_warehouse_state_contract,
    make_warehouse_source_readers,
)
from .goal_remaining_feasibility_oracle import query_remaining_feasibility
from .goal_trusted_state import ground_state
from .provenance import canonical_sha256

NATIVE_PROBE_EVIDENCE_SCHEMA = "eai.benchmark.native_probe_evidence"
NATIVE_PROBE_COLLECTION_PLAN_SCHEMA = "eai.benchmark.native_probe_collection_plan"
OFFLINE_PROBE_MODE = "offline_deterministic_fake"
HOST_PROBE_MODE = "host_installed"
PROBE_ERROR_CATEGORY = "probe_error"
NATIVE_CLOCK_VIOLATION_CATEGORY = "native_clock_violation"
VERSION_BINDING_MISMATCH_CATEGORY = "version_binding_mismatch"
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}

ProbeFactory = Callable[[Mapping[str, Any]], Mapping[str, bool]]


@dataclass(frozen=True)
class NativeProbeSpec:
    """One scene's native probe wiring.

    ``registration_builder`` and ``contract_builder`` are the scene reader's
    frozen builders.  ``source_probe_factories`` optionally carries
    host-installed probe factories; ``None`` means the harness installs
    deterministic offline fakes.  Probes are host-owned evaluators, never
    participant data.
    """

    scene_key: str
    registration_builder: Callable[..., Mapping[str, Any]]
    contract_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    certificate_builder: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    source_readers_factory: Callable[[Mapping[str, ProbeFactory] | None],
                                     Mapping[str, Callable[..., Mapping[str, Any]]]]
    measurement_points: tuple[Mapping[str, Any], ...]
    source_versions: Mapping[str, str]
    predicate_bindings: Mapping[str, Mapping[str, Any]]
    entity_ids: tuple[str, ...]
    dev_cases: Mapping[str, tuple[str, list[dict[str, Any]], Mapping[str, int]]]
    native_blockers: tuple[str, ...]
    source_probe_factories: Mapping[str, ProbeFactory] | None = None


_SCENE_SPECS: dict[str, NativeProbeSpec] = {
    HOUSE_SCENE_KEY: NativeProbeSpec(
        scene_key=HOUSE_SCENE_KEY,
        registration_builder=build_house_registration,
        contract_builder=build_house_state_contract,
        certificate_builder=build_house_development_certificate,
        source_readers_factory=make_house_source_readers,
        measurement_points=tuple(HOUSE_MEASUREMENT_POINTS),
        source_versions=dict(HOUSE_SOURCE_VERSIONS),
        predicate_bindings=dict(HOUSE_PREDICATE_BINDINGS),
        entity_ids=tuple(HOUSE_ENTITY_IDS),
        dev_cases=dict(HOUSE_DEV_CASES),
        native_blockers=(HOUSE_NATIVE_BLOCKER,),
    ),
    CITY_SCENE_KEY: NativeProbeSpec(
        scene_key=CITY_SCENE_KEY,
        registration_builder=build_city_registration,
        contract_builder=build_city_state_contract,
        certificate_builder=build_city_development_certificate,
        source_readers_factory=make_city_source_readers,
        measurement_points=tuple(CITY_MEASUREMENT_POINTS),
        source_versions=dict(CITY_SOURCE_VERSIONS),
        predicate_bindings=dict(CITY_PREDICATE_BINDINGS),
        entity_ids=tuple(CITY_ENTITY_IDS),
        dev_cases=dict(CITY_DEV_CASES),
        native_blockers=(CITY_NATIVE_BLOCKER,),
    ),
    WAREHOUSE_SCENE_KEY: NativeProbeSpec(
        scene_key=WAREHOUSE_SCENE_KEY,
        registration_builder=build_warehouse_registration,
        contract_builder=build_warehouse_state_contract,
        certificate_builder=build_warehouse_development_certificate,
        source_readers_factory=make_warehouse_source_readers,
        measurement_points=tuple(WAREHOUSE_MEASUREMENT_POINTS),
        source_versions=dict(WAREHOUSE_SOURCE_VERSIONS),
        predicate_bindings=dict(WAREHOUSE_PREDICATE_BINDINGS),
        entity_ids=tuple(WAREHOUSE_ENTITY_IDS),
        dev_cases=dict(WAREHOUSE_DEV_CASES),
        native_blockers=(WAREHOUSE_NATIVE_BLOCKER,),
    ),
    FACTORY_SCENE_KEY: NativeProbeSpec(
        scene_key=FACTORY_SCENE_KEY,
        registration_builder=build_factory_registration,
        contract_builder=build_factory_state_contract,
        certificate_builder=build_factory_development_certificate,
        source_readers_factory=make_factory_source_readers,
        measurement_points=tuple(FACTORY_MEASUREMENT_POINTS),
        source_versions=dict(FACTORY_SOURCE_VERSIONS),
        predicate_bindings=dict(FACTORY_PREDICATE_BINDINGS),
        entity_ids=tuple(FACTORY_ENTITY_IDS),
        dev_cases=dict(FACTORY_DEV_CASES),
        # Factory is blocked by the frozen-evaluator hash mismatch first; the
        # pending native admission blocker is recorded alongside it.
        native_blockers=(FACTORY_FIXTURE_HASH_MISMATCH,
                         f"{FACTORY_SCENE_KEY}_native_admission_pending"),
    ),
    RESTAURANT_SCENE_KEY: NativeProbeSpec(
        scene_key=RESTAURANT_SCENE_KEY,
        registration_builder=build_restaurant_registration,
        contract_builder=build_restaurant_state_contract,
        certificate_builder=build_restaurant_development_certificate,
        source_readers_factory=make_restaurant_source_readers,
        measurement_points=tuple(RESTAURANT_MEASUREMENT_POINTS),
        source_versions=dict(RESTAURANT_SOURCE_VERSIONS),
        predicate_bindings=dict(RESTAURANT_PREDICATE_BINDINGS),
        entity_ids=tuple(RESTAURANT_ENTITY_IDS),
        dev_cases=dict(RESTAURANT_DEV_CASES),
        # Restaurant stays authoring-only until every pool candidate has its
        # native controller/route/contact evidence (STAGE32 audit).
        native_blockers=(RESTAURANT_NATIVE_BLOCKER,),
    ),
}


def registered_probe_scenes() -> tuple[str, ...]:
    """Scenes that currently have a probe harness wiring."""
    return tuple(sorted(_SCENE_SPECS))


def _scene_spec(scene_key: str) -> NativeProbeSpec:
    spec = _SCENE_SPECS.get(scene_key)
    if spec is None:
        raise ValueError(f"unknown native probe scene: {scene_key!r}; "
                         f"registered scenes: {sorted(_SCENE_SPECS)}")
    return spec


def _deterministic_fake_probe(
        source_id: str,
        predicate_bindings: Mapping[str, Mapping[str, Any]]) -> ProbeFactory:
    """Offline fake probe: replays the state's aggregated facts per source.

    It performs no PhysX/USD query and measures nothing.  It exists only so
    the harness chain (readers -> grounding -> oracle -> gate -> marker) can
    be developed and tested without Isaac; its evidence class stays
    ``diagnostic`` and can never support native admission.
    """
    expected = tuple(sorted(fact for fact, binding in predicate_bindings.items()
                            if binding["source_id"] == source_id))

    def probe(native_query_context: Mapping[str, Any]) -> dict[str, bool]:
        facts = set(native_query_context["true_facts"])
        return {fact: fact in facts for fact in expected}
    return probe


def _entity_version_mismatch(versions: Any, entity_ids: Iterable[str]) -> str | None:
    entities = set(entity_ids)
    if not isinstance(versions, Mapping):
        return "entity_versions_not_a_mapping"
    if set(versions) != entities:
        missing = sorted(entities - set(versions))
        if missing:
            return f"missing_entities:{','.join(missing)}"
        return f"unknown_entities:{','.join(sorted(set(versions) - entities))}"
    invalid = sorted(key for key, value in versions.items()
                     if isinstance(value, bool) or type(value) is not int or value < 0)
    if invalid:
        return f"invalid_entity_versions:{','.join(invalid)}"
    return None


def validate_native_clock(samples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate the native clock contract over ordered measurement samples.

    Contract: ``time_s`` is episode-relative, finite, nonnegative, and
    non-decreasing within one ``clock_epoch`` (equal times are legal for
    same-step reassessments); one epoch is measured by exactly one
    ``clock_id`` (one simulation clock); one measurement point never spans
    two epochs (prevents cross-episode clock mixing).  Violations are
    reported, never raised: callers turn them into fail-closed oracle
    records.
    """
    try:
        rows = list(samples)
    except TypeError:
        return {"status": "failed",
                "violations": ["malformed_clock_samples:not_iterable"],
                "sample_count": 0, "clock_epochs": [], "clock_ids": []}
    violations: list[str] = []
    epoch_clocks: dict[str, str] = {}
    measurement_epochs: dict[str, str] = {}
    last_time: dict[str, float] = {}
    for index, sample in enumerate(rows):
        label = f"sample[{index}]"
        if not isinstance(sample, Mapping):
            violations.append(f"malformed_clock_sample:{label}:not_a_mapping")
            continue
        epoch = sample.get("clock_epoch")
        clock_id = sample.get("clock_id")
        time_s = sample.get("time_s")
        if not isinstance(epoch, str) or not epoch.strip():
            violations.append(f"malformed_clock_sample:{label}:clock_epoch_required")
            continue
        if not isinstance(clock_id, str) or not clock_id.strip():
            violations.append(f"malformed_clock_sample:{label}:clock_id_required")
            continue
        if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                or not math.isfinite(time_s)):
            violations.append(f"malformed_clock_sample:{label}:time_s_not_finite_number")
            continue
        if time_s < 0:
            violations.append(f"negative_time_s:{label}")
            continue
        if epoch in epoch_clocks and epoch_clocks[epoch] != clock_id:
            violations.append(f"clock_epoch_mixed_clocks:{epoch}")
        else:
            epoch_clocks[epoch] = clock_id
        measurement_id = sample.get("measurement_id")
        if isinstance(measurement_id, str) and measurement_id:
            if measurement_epochs.setdefault(measurement_id, epoch) != epoch:
                violations.append(
                    f"measurement_rebound_across_clock_epochs:{measurement_id}")
        if epoch in last_time and time_s < last_time[epoch]:
            violations.append(f"time_moved_backwards:{epoch}")
        last_time[epoch] = time_s
    return {
        "status": "failed" if violations else "verified",
        "violations": violations,
        "sample_count": len(rows),
        "clock_epochs": sorted(epoch_clocks),
        "clock_ids": sorted(set(epoch_clocks.values())),
    }


def _json_safe_clock(binding: Mapping[str, Any]) -> dict[str, Any]:
    time_s = binding.get("time_s")
    if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
            or not math.isfinite(time_s)):
        time_s = None
    epoch = binding.get("clock_epoch")
    clock_id = binding.get("clock_id")
    return {
        "measurement_id": binding.get("measurement_id"),
        "clock_epoch": epoch if isinstance(epoch, str) else None,
        "clock_id": clock_id if isinstance(clock_id, str) else None,
        "time_s": time_s,
    }


class NativeProbeHarness:
    """Assembled per-scene probe harness running the full admission chain.

    ``run_measurement`` wires the installed probes into the scene's source
    readers, grounds the state, queries the registered remaining-task
    oracle, applies the alternative-path certificate gate, and stamps the
    result with the development marker.  Clock violations, version
    mismatches, and probe exceptions fail closed as classified quality
    failures with a null feasibility label.
    """

    def __init__(self, spec: NativeProbeSpec, *, registration,
                 state_contract, certificate, source_readers, marker,
                 probe_mode: str, episode_id: str):
        if (set(source_readers) != set(spec.source_versions)
                or any(not callable(reader) for reader in source_readers.values())):
            raise ValueError("every registered source requires a trusted reader")
        self._spec = spec
        self._registration = deepcopy(registration)
        self._state_contract = deepcopy(state_contract)
        self._certificate = deepcopy(certificate)
        self._source_readers = dict(source_readers)
        self._marker = deepcopy(marker)
        self._probe_mode = probe_mode
        self._episode_id = episode_id
        self._results: list[dict[str, Any]] = []
        self._clock_samples: list[dict[str, Any]] = []
        self._run_count = 0

    @property
    def spec(self) -> NativeProbeSpec:
        return self._spec

    @property
    def scene_key(self) -> str:
        return self._spec.scene_key

    @property
    def probe_mode(self) -> str:
        return self._probe_mode

    @property
    def results(self) -> list[dict[str, Any]]:
        return deepcopy(self._results)

    @property
    def clock_samples(self) -> list[dict[str, Any]]:
        return deepcopy(self._clock_samples)

    def _finish(self, record: Mapping[str, Any]) -> dict[str, Any]:
        stamped = stamp_evidence_record(record, self._marker)
        self._results.append(deepcopy(stamped))
        return stamped

    def _failure_record(self, *, assessment_id: str, assessment_epoch: int,
                        case_id: str, measurement_id: str, quality_failure: str,
                        category: str, clock_binding: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "assessment_id": assessment_id,
            "assessment_epoch": assessment_epoch,
            "episode_id": self._episode_id,
            "case_id": case_id,
            "scene_key": self.scene_key,
            "measurement_id": measurement_id,
            "feasibility_label": None,
            "oracle_status": "failed",
            "platform_valid": False,
            "evidence_refs": [],
            "evaluator_ref": "remaining_task_reachability_v1",
            "quality_failure": quality_failure,
            "failure_category": category,
            "probe_mode": self._probe_mode,
            "native_clock": _json_safe_clock(clock_binding),
            "visibility": "evaluator_only",
            **_RELEASE,
        }

    def run_measurement(self, case_id: str, *, native_state_provider) -> dict[str, Any]:
        """Run one preregistered development case end-to-end with native probes.

        ``native_state_provider`` is a host-installed callable receiving the
        measurement context (scene/case/measurement/assessment identity and
        the harness probe mode) and returning a mapping with ``time_s``
        (native clock), ``entity_versions``, ``true_facts`` (aggregated
        native predicate truth), ``clock_epoch``, and ``clock_id``.  The
        per-source probes independently measure their bound predicates and
        ``ground_state`` fails closed unless both paths agree.
        """
        if case_id not in self._spec.dev_cases:
            raise ValueError(f"unknown {self.scene_key} development case: {case_id}")
        if not callable(native_state_provider):
            raise ValueError("native_state_provider must be callable")
        measurement_id, robots, resources = self._spec.dev_cases[case_id]
        self._run_count += 1
        assessment_id = f"{case_id}-probe-a{self._run_count}"
        assessment_epoch = self._run_count - 1
        clock_binding: dict[str, Any] = {"measurement_id": measurement_id,
                                         "clock_epoch": None, "clock_id": None,
                                         "time_s": None}

        def fail(quality_failure: str, category: str) -> dict[str, Any]:
            record = self._failure_record(
                assessment_id=assessment_id, assessment_epoch=assessment_epoch,
                case_id=case_id, measurement_id=measurement_id,
                quality_failure=quality_failure, category=category,
                clock_binding=clock_binding)
            return self._finish(record)

        context = {"scene_key": self.scene_key, "case_id": case_id,
                   "measurement_id": measurement_id,
                   "assessment_id": assessment_id, "probe_mode": self._probe_mode}
        try:
            native = native_state_provider(deepcopy(context))
            if not isinstance(native, Mapping):
                raise TypeError("native_state_provider must return a mapping")
        except Exception as exc:
            return fail(f"probe_error:native_state_provider:{type(exc).__name__}:{exc}",
                        PROBE_ERROR_CATEGORY)
        raw_facts = native.get("true_facts")
        if (not isinstance(raw_facts, (list, tuple, set, frozenset))
                or any(not isinstance(fact, str) for fact in raw_facts)):
            return fail("probe_error:native_state_provider:true_facts_require_strings",
                        PROBE_ERROR_CATEGORY)
        clock_binding.update(clock_epoch=native.get("clock_epoch"),
                             clock_id=native.get("clock_id"),
                             time_s=native.get("time_s"))
        self._clock_samples.append(deepcopy(clock_binding))
        clock_review = validate_native_clock(self._clock_samples)
        if clock_review["status"] != "verified":
            return fail(f"native_clock_violation:{clock_review['violations'][0]}",
                        NATIVE_CLOCK_VIOLATION_CATEGORY)
        mismatch = _entity_version_mismatch(native.get("entity_versions"),
                                            self._state_contract["entity_ids"])
        if mismatch is not None:
            return fail(f"version_binding_mismatch:{mismatch}",
                        VERSION_BINDING_MISMATCH_CATEGORY)
        robots_run = deepcopy(robots)
        resources_run = dict(resources)
        state = {
            "time_s": native["time_s"],
            "true_facts": sorted(set(raw_facts)),
            "entity_versions": dict(native["entity_versions"]),
            "resources": resources_run,
            "max_active_agents": len(robots_run),
            "robots": robots_run,
            "capability_versions": {robot["id"]: 1 for robot in robots_run},
            "roster_version": 1,
            "resource_versions": {key: 1 for key in resources_run},
        }
        snapshot = {"assessment_id": assessment_id, "assessment_epoch": assessment_epoch}
        observation = {"measurement_id": measurement_id, "state": state}
        packets: dict[str, Mapping[str, Any]] = {}
        for source in sorted(self._source_readers):
            try:
                packets[source] = self._source_readers[source](deepcopy(snapshot),
                                                               observation)
            except Exception as exc:
                return fail(f"probe_error:{source}:{type(exc).__name__}:{exc}",
                            PROBE_ERROR_CATEGORY)
        grounding = ground_state(self._state_contract, self._registration,
                                 measurement_id=measurement_id, state=state,
                                 readbacks=packets)
        result = query_remaining_feasibility(
            self._registration, measurement_id=measurement_id,
            episode_id=self._episode_id, assessment_id=assessment_id,
            assessment_epoch=assessment_epoch,
            task_sha256=self._registration["task_sha256"],
            state=state, grounding=grounding)
        gated = gate_not_deployable_result(result, self._certificate,
                                           self._registration)
        gated.update(case_id=case_id, scene_key=self.scene_key,
                     probe_mode=self._probe_mode,
                     native_clock=_json_safe_clock(clock_binding),
                     grounding_status=grounding.get("status"),
                     grounding_evidence_refs=list(grounding.get("evidence_refs", [])))
        if grounding.get("status") != "verified":
            gated["grounding_quality_failure"] = grounding.get("quality_failure")
        return self._finish(gated)


def build_probe_harness(
        scene_key: str,
        *,
        probe_factories: Mapping[str, ProbeFactory] | None = None,
        asset_status: str = "development_unverified",
        evidence_class: str = "diagnostic",
        infrastructure_blockers: Iterable[str] | None = None,
        episode_id: str | None = None,
) -> NativeProbeHarness:
    """Assemble one scene's harness from its spec.

    ``probe_factories`` maps source id to a host-installed factory that is
    invoked once per measurement with the trusted state observation as its
    native query context and must return ``{fact_id: bool}`` covering exactly
    that source's bound predicates.  ``None`` installs deterministic fake
    probes (offline mode): they replay the state's aggregated facts and
    measure nothing, so their evidence stays diagnostic.  The marker uses
    ``asset_status`` (default ``development_unverified``) with the scene's
    native admission blocker and ``evidence_class`` (default ``diagnostic``).
    """
    spec = _scene_spec(scene_key)
    factories = (probe_factories if probe_factories is not None
                 else spec.source_probe_factories)
    if factories is None:
        probes = {source: _deterministic_fake_probe(source, spec.predicate_bindings)
                  for source in spec.source_versions}
        probe_mode = OFFLINE_PROBE_MODE
    else:
        if not isinstance(factories, Mapping):
            raise ValueError("probe_factories must map source ids to callables")
        missing = sorted(set(spec.source_versions) - set(factories))
        unknown = sorted(set(factories) - set(spec.source_versions))
        if missing or unknown:
            raise ValueError("probe_factories must cover every registered source "
                             f"exactly: missing={missing} unknown={unknown}")
        if any(not callable(factory) for factory in factories.values()):
            raise ValueError("every probe factory must be callable")
        probes = dict(factories)
        probe_mode = HOST_PROBE_MODE
    registration = spec.registration_builder()
    state_contract = spec.contract_builder(registration)
    certificate = spec.certificate_builder(registration)
    readers = spec.source_readers_factory(probes)
    blockers = (tuple(spec.native_blockers) if infrastructure_blockers is None
                else tuple(infrastructure_blockers))
    marker = build_development_marker(scene_key, asset_status=asset_status,
                                      evidence_class=evidence_class,
                                      infrastructure_blockers=blockers)
    episode = episode_id or f"{scene_key}-native-probe-episode"
    return NativeProbeHarness(spec, registration=registration,
                              state_contract=state_contract, certificate=certificate,
                              source_readers=readers, marker=marker,
                              probe_mode=probe_mode, episode_id=episode)


def archive_probe_evidence(harness_results: Iterable[Mapping[str, Any]],
                           output_path: str | Path) -> dict[str, Any]:
    """Archive probe-run evidence as diagnostic JSON; release stays false.

    Each measurement row records the grounding status and evidence refs, the
    solver label, the quality-failure category, and the native clock binding
    (epoch, clock id, time).  The archive re-validates the clock contract
    over the recorded bindings, stamps the merged development marker, and
    forces every release flag to false.
    """
    rows: list[dict[str, Any]] = []
    for row in harness_results:
        if not isinstance(row, Mapping):
            raise ValueError("harness results must be evidence mappings")
        rows.append(deepcopy(dict(row)))
    measurements: list[dict[str, Any]] = []
    clock_samples: list[dict[str, Any]] = []
    for row in rows:
        clock = row.get("native_clock")
        grounding = {"status": row.get("grounding_status"),
                     "evidence_refs": list(row.get("grounding_evidence_refs") or [])}
        if row.get("grounding_quality_failure") is not None:
            grounding["quality_failure"] = row["grounding_quality_failure"]
        measurements.append({
            "scene_key": row.get("scene_key"),
            "case_id": row.get("case_id"),
            "measurement_id": row.get("measurement_id"),
            "probe_mode": row.get("probe_mode"),
            "native_clock": (deepcopy(dict(clock))
                             if isinstance(clock, Mapping) else None),
            "grounding": grounding,
            "feasibility_label": row.get("feasibility_label"),
            "oracle_status": row.get("oracle_status"),
            "quality_failure": row.get("quality_failure"),
            "failure_category": row.get("failure_category"),
            "evidence_refs": list(row.get("evidence_refs") or []),
            "development_track": deepcopy(row.get("development_track")),
            "formal_statistics": False,
            "release_eligible": False,
        })
        if isinstance(clock, Mapping):
            clock_samples.append({
                "measurement_id": (clock.get("measurement_id")
                                   if clock.get("measurement_id") is not None
                                   else row.get("measurement_id")),
                "clock_epoch": clock.get("clock_epoch"),
                "clock_id": clock.get("clock_id"),
                "time_s": clock.get("time_s"),
            })
        else:
            clock_samples.append({"measurement_id": row.get("measurement_id"),
                                  "clock_epoch": None, "clock_id": None,
                                  "time_s": None})
    markers = [row["development_track"] for row in measurements
               if isinstance(row["development_track"], Mapping)]
    archive: dict[str, Any] = {
        "schema": NATIVE_PROBE_EVIDENCE_SCHEMA,
        "version": 1,
        "visibility": "evaluator_only",
        "scene_keys": sorted({row["scene_key"] for row in measurements
                              if row["scene_key"]}),
        "measurement_count": len(measurements),
        "measurements": measurements,
        "native_clock_validation": validate_native_clock(clock_samples),
        "development_track": merge_development_markers(markers) if markers else None,
        "release_eligible": False,
        "formal_statistics": False,
        **_RELEASE,
    }
    archive["evidence_sha256"] = canonical_sha256(measurements)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(archive, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return archive


def probe_collection_plan(scene_key: str) -> dict[str, Any]:
    """Derive what a headless/native executor must collect for this scene.

    The plan is derived from the frozen registration (measurement points and
    their predicate partitions) and state contract (per-source predicate and
    entity bindings): which predicates each source must measure, which
    entity version set every measurement point needs, and the preregistered
    contrast cases.  It is the collection manual handed to the ljy/headless
    execution host.
    """
    spec = _scene_spec(scene_key)
    registration = spec.registration_builder()
    contract = spec.contract_builder(registration)
    sources: dict[str, dict[str, Any]] = {}
    for fact, binding in sorted(spec.predicate_bindings.items()):
        row = sources.setdefault(binding["source_id"], {
            "source_version": spec.source_versions[binding["source_id"]],
            "predicates": [], "entity_ids": set(), "evidence_kinds": set()})
        row["predicates"].append(fact)
        row["entity_ids"].update(binding["entity_ids"])
        row["evidence_kinds"].add(binding["evidence_kind"])
    source_rows = {source: {
        "source_version": row["source_version"],
        "predicates": sorted(row["predicates"]),
        "entity_ids": sorted(row["entity_ids"]),
        "evidence_kinds": sorted(row["evidence_kinds"]),
    } for source, row in sorted(sources.items())}
    fact_ids = sorted(registration["model"]["fact_ids"])
    entity_ids = sorted(contract["entity_ids"])
    points = [{
        "id": point["id"],
        "trigger": point["trigger"],
        "required_true": list(point["required_true"]),
        "required_false": list(point["required_false"]),
        "varying_facts": list(point["varying_facts"]),
        # Every fact, true and false, needs its own measurement at every
        # point; explicit false differs from missing.
        "facts_to_measure": list(fact_ids),
        "required_entity_version_ids": list(entity_ids),
    } for point in registration["measurement_points"]]
    contrast_cases = [{
        "case_id": case_id,
        "measurement_id": measurement_id,
        "robots": deepcopy(robots),
        "resources": dict(resources),
    } for case_id, (measurement_id, robots, resources) in sorted(spec.dev_cases.items())]
    return {
        "schema": NATIVE_PROBE_COLLECTION_PLAN_SCHEMA,
        "version": 1,
        "visibility": "evaluator_only",
        "scene_key": scene_key,
        "registration_sha256": registration["registration_sha256"],
        "state_contract_sha256": contract["contract_sha256"],
        "validation_scope": registration["validation"]["scope"],
        "native_blockers": list(spec.native_blockers),
        "sources": source_rows,
        "measurement_points": points,
        "contrast_cases": contrast_cases,
        "native_state_provider_contract": {
            "receives": ["scene_key", "case_id", "measurement_id",
                         "assessment_id", "probe_mode"],
            "returns": {
                "time_s": "episode-relative native clock seconds",
                "entity_versions": "exact state-contract entity set -> nonnegative int",
                "true_facts": "aggregated native predicate truth (list of fact ids)",
                "clock_epoch": "episode identity; never reused across episodes",
                "clock_id": "simulation clock identity; exactly one per epoch",
            },
        },
        "probe_contract": {
            "receives": "trusted state observation (time_s, true_facts, "
                        "entity_versions, robots, resources, ...)",
            "returns": "{fact_id: bool} covering exactly the source's bound predicates",
        },
        "native_clock_requirements": {
            "time_s": "finite, nonnegative, non-decreasing within one clock_epoch",
            "clock_epoch": "one per episode; restart at zero only in a new epoch",
            "clock_id": "one simulation clock per epoch; mixing ids in one epoch fails",
            "measurement_binding": "a measurement point never spans two epochs",
        },
        "failure_categories": {
            PROBE_ERROR_CATEGORY: "probe or native state provider exception",
            NATIVE_CLOCK_VIOLATION_CATEGORY: "native clock contract violation",
            VERSION_BINDING_MISMATCH_CATEGORY:
                "entity versions differ from the frozen state contract",
        },
        "release_eligible": False,
        "formal_statistics": False,
        **_RELEASE,
    }


__all__ = [
    "HOST_PROBE_MODE",
    "NATIVE_CLOCK_VIOLATION_CATEGORY",
    "NATIVE_PROBE_COLLECTION_PLAN_SCHEMA",
    "NATIVE_PROBE_EVIDENCE_SCHEMA",
    "OFFLINE_PROBE_MODE",
    "NativeProbeHarness",
    "NativeProbeSpec",
    "ProbeFactory",
    "PROBE_ERROR_CATEGORY",
    "VERSION_BINDING_MISMATCH_CATEGORY",
    "archive_probe_evidence",
    "build_probe_harness",
    "probe_collection_plan",
    "registered_probe_scenes",
    "validate_native_clock",
]
