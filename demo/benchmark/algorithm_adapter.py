"""Stable integration boundary for external multi-agent algorithms.

The benchmark owns the public event/action protocol and the low-level control
stack.  TeamWeaver and EMOS are heterogeneous multi-robot systems under test;
navigation, locomotion and manipulation remain benchmark-managed ideal
defaults so those variables stay fixed across algorithms.  This module keeps
the decision layer behind one small adapter and records enough provenance to
compare independent runs safely.

This module is deliberately Isaac-free. Its callback adapters serve fixed-DAG
diagnostics via ParticipantSession. Goal-driven algorithms instead implement
GoalAlgorithmAdapter in goal_algorithm_adapter.py. Run manifests and statistics
below can describe either track; task hashes must keep them distinct.
"""

from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import random
import re
import signal
import subprocess
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .participant_runtime import ParticipantAdapter, validate_adapter_manifest
from .provenance import canonical_sha256


RUN_MANIFEST_SCHEMA = "eai.benchmark.algorithm_run_manifest"
ALGORITHM_RESULT_SCHEMA = "eai.benchmark.algorithm_result"
ADAPTER_INTERFACE_VERSION = 1
DEFAULT_CONTROL_STACK = {
    "navigation": "benchmark_default_ideal",
    "locomotion": "benchmark_default_ideal",
    "manipulation": "benchmark_default_ideal",
    "participant_scope": "multi_robot_decision_only",
}
_LOW_LEVEL_ACTION_TYPES = frozenset({
    "cmd_vel", "velocity_command", "raw_velocity", "trajectory", "raw_trajectory",
    "joint_command", "joint_positions", "joint_torques", "grasp_command",
    "low_level_action", "controller_action",
})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset({"completed", "failed", "timeout", "invalid"})
_HIDDEN_FIELDS = frozenset({"ground_truth", "reference_decision", "scenario_snapshot", "hidden_evaluator"})


class AlgorithmAdapter(ParticipantAdapter, Protocol):
    """Participant adapter plus optional lifecycle hooks for external runners."""

    def initialize(self, context: Mapping[str, Any]) -> None: ...

    def close(self) -> None: ...


class CallableAlgorithmAdapter:
    """Adapt a decision callback to the fixed-DAG participant protocol.

    ``decide`` receives a deep copy of participant-visible events only.  The
    callback must return a list of public participant actions.  No evaluator
    object is accepted by this class, which makes accidental truth leakage
    visible at the integration boundary.
    """

    def __init__(
        self,
        algorithm_id: str,
        version: int,
        decide: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
        *,
        action_types: Iterable[str] = (),
        backend: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        initialize: Callable[[Mapping[str, Any]], None] | None = None,
        close: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(algorithm_id, str) or not algorithm_id.strip():
            raise ValueError("algorithm_id must be a nonempty string")
        # ParticipantSession's published protocol currently requires an
        # integer adapter version.  Keep richer semantic versions in
        # ``metadata`` until the protocol itself is versioned.
        if type(version) is not int:
            raise ValueError("algorithm adapter version must be an integer")
        if not callable(decide):
            raise ValueError("decide must be callable")
        actions = tuple(action_types)
        if any(not isinstance(item, str) or not item for item in actions):
            raise ValueError("action_types must contain nonempty strings")
        if _LOW_LEVEL_ACTION_TYPES & set(actions):
            raise ValueError("adapter action_types may not declare low-level control actions")
        self._algorithm_id = algorithm_id
        self._version = version
        self._decide = decide
        self._initialize = initialize
        self._close = close
        self._manifest = {
            "kind": "participant",
            "id": algorithm_id,
            "version": version,
            "interface": "eai.benchmark.participant_adapter",
            "interface_version": ADAPTER_INTERFACE_VERSION,
            "evaluation_track": "fixed_dag_diagnostic",
            "input_scope": "participant_visible_events_only",
            "decision_scope": "allocation_coordination_within_published_dag",
            "control_scope": "benchmark_managed_low_level_control",
            "action_types": list(dict.fromkeys(actions)),
            "backend": backend or algorithm_id,
            "metadata": _json_object(metadata, "adapter metadata"),
        }
        validate_adapter_manifest(self._manifest)

    def manifest(self) -> Mapping[str, Any]:
        return deepcopy(self._manifest)

    def initialize(self, context: Mapping[str, Any]) -> None:
        if self._initialize is not None:
            self._initialize(deepcopy(dict(context)))

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        visible_events = deepcopy(events)
        result = self._decide(visible_events)
        if not isinstance(result, list):
            raise ValueError("algorithm adapter decide callback must return a list")
        for action in result:
            if isinstance(action, Mapping) and action.get("type") in _LOW_LEVEL_ACTION_TYPES:
                raise ValueError("algorithm adapter may emit decisions only, not low-level control actions")
        return deepcopy(result)

    def close(self) -> None:
        if self._close is not None:
            self._close()


def make_algorithm_adapter(
    algorithm_id: str,
    version: int,
    decide: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    **kwargs: Any,
) -> CallableAlgorithmAdapter:
    """Factory for fixed-DAG diagnostic integrations."""
    return CallableAlgorithmAdapter(algorithm_id, version, decide, **kwargs)


class PlannerAdapter(CallableAlgorithmAdapter):
    """Bridge a model plan/assignment output through an action encoder.

    ``plan`` can call a TeamWeaver/EMOS model. ``encode`` is the only place
    that translates that decision output to the benchmark's public action
    vocabulary. Raw trajectories, low-level controller commands and custom
    grasp/navigation implementations are outside this adapter.
    """

    def __init__(
        self,
        algorithm_id: str,
        version: int,
        plan: Callable[[list[dict[str, Any]]], Any],
        encode: Callable[[Any, list[dict[str, Any]]], list[dict[str, Any]]],
        **kwargs: Any,
    ) -> None:
        if not callable(plan) or not callable(encode):
            raise ValueError("plan and encode must be callable")

        def decide(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return encode(plan(events), events)

        super().__init__(algorithm_id, version, decide, **kwargs)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite")
    return float(value)


def _comparison_context(manifest):
    """Frozen variables that must agree before algorithms can be paired."""
    return {key: deepcopy(manifest[key]) for key in (
        "benchmark", "scenario", "seed", "branch_id", "control_stack",
        "factor_treatment", "runtime_config")}


def _json_object(value: Mapping[str, Any] | None, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result = deepcopy(dict(value))
    if _contains_hidden_key(result):
        raise ValueError(f"{field} contains evaluator-only fields")
    canonical_sha256(result)
    return result


def _contains_hidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(_HIDDEN_FIELDS & set(value)) or any(
            _contains_hidden_key(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_hidden_key(item) for item in value)
    return False


def build_algorithm_run_manifest(
    *,
    algorithm_id: str,
    algorithm_version: str | int,
    benchmark_commit: str,
    task_bundle_sha256: str,
    scenario_sha256: str,
    scene_key: str,
    task_id: str,
    provider_revision: str,
    seed: int,
    run_root: str,
    episode_id: str | None = None,
    factor_treatment: Mapping[str, Any] | None = None,
    runtime_config: Mapping[str, Any] | None = None,
    branch_id: str = "main",
    comparison_key: str | None = None,
) -> dict[str, Any]:
    """Create one immutable case identity for a multi-algorithm run."""
    for name, value in (("algorithm_id", algorithm_id), ("benchmark_commit", benchmark_commit),
                        ("task_bundle_sha256", task_bundle_sha256), ("scenario_sha256", scenario_sha256),
                        ("scene_key", scene_key), ("task_id", task_id),
                        ("provider_revision", provider_revision), ("run_root", run_root),
                        ("branch_id", branch_id)):
        _required_text(value, name)
    if not isinstance(algorithm_version, (str, int)) or isinstance(algorithm_version, bool):
        raise ValueError("algorithm_version must be a string or integer")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if not _HASH_RE.fullmatch(task_bundle_sha256) or not _HASH_RE.fullmatch(scenario_sha256):
        raise ValueError("task_bundle_sha256 and scenario_sha256 must be lowercase SHA-256 values")
    factor = _json_object(factor_treatment, "factor_treatment")
    runtime = _json_object(runtime_config, "runtime_config")
    manifest = {
        "schema": RUN_MANIFEST_SCHEMA,
        "version": 1,
        "algorithm": {"id": algorithm_id, "version": algorithm_version},
        "benchmark": {"commit": benchmark_commit, "task_bundle_sha256": task_bundle_sha256},
        "scenario": {"sha256": scenario_sha256, "scene_key": scene_key, "task_id": task_id,
                      "provider_revision": provider_revision},
        "seed": seed,
        "branch_id": branch_id,
        "run_root": run_root,
        "control_stack": deepcopy(DEFAULT_CONTROL_STACK),
        "factor_treatment": factor,
        "runtime_config": runtime,
    }
    context = _comparison_context(manifest)
    manifest["comparison_key"] = comparison_key or canonical_sha256(context)[:24]
    manifest["episode_id"] = episode_id or canonical_sha256({
        "algorithm": manifest["algorithm"], "context": context})[:24]
    validate_algorithm_run_manifest(manifest)
    return manifest


def validate_algorithm_run_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ValueError("algorithm run manifest must be an object")
    value = deepcopy(dict(manifest))
    required = {"schema", "version", "algorithm", "benchmark", "scenario", "seed",
                "episode_id", "comparison_key", "branch_id", "run_root", "control_stack",
                "factor_treatment", "runtime_config"}
    if not required <= value.keys() or value["schema"] != RUN_MANIFEST_SCHEMA or value["version"] != 1:
        raise ValueError("unsupported algorithm run manifest")
    for section in ("algorithm", "benchmark", "scenario", "factor_treatment", "runtime_config"):
        if not isinstance(value[section], Mapping):
            raise ValueError(f"manifest {section} must be an object")
    _required_text(value["algorithm"].get("id"), "algorithm.id")
    if not isinstance(value["algorithm"].get("version"), (str, int)) or isinstance(value["algorithm"].get("version"), bool):
        raise ValueError("algorithm.version must be a string or integer")
    _required_text(value["benchmark"].get("commit"), "benchmark.commit")
    _required_text(value["scenario"].get("scene_key"), "scenario.scene_key")
    _required_text(value["scenario"].get("task_id"), "scenario.task_id")
    _required_text(value["scenario"].get("provider_revision"), "scenario.provider_revision")
    _required_text(value["episode_id"], "episode_id")
    _required_text(value["comparison_key"], "comparison_key")
    _required_text(value["run_root"], "run_root")
    _required_text(value["branch_id"], "branch_id")
    if value["control_stack"] != DEFAULT_CONTROL_STACK:
        raise ValueError("manifest control_stack must use benchmark default ideal controls")
    if type(value["seed"]) is not int:
        raise ValueError("manifest seed must be an integer")
    for name in ("task_bundle_sha256",):
        if not _HASH_RE.fullmatch(str(value["benchmark"].get(name, ""))):
            raise ValueError(f"manifest benchmark.{name} must be SHA-256")
    if not _HASH_RE.fullmatch(str(value["scenario"].get("sha256", ""))):
        raise ValueError("manifest scenario.sha256 must be SHA-256")
    _json_object(value["factor_treatment"], "factor_treatment")
    _json_object(value["runtime_config"], "runtime_config")
    return value


def build_algorithm_result(
    manifest: Mapping[str, Any],
    *,
    status: str,
    metrics: Mapping[str, Any] | None = None,
    participant_report: Mapping[str, Any] | None = None,
    failure_class: str | None = None,
    evaluation: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = validate_algorithm_run_manifest(manifest)
    if status not in _STATUSES:
        raise ValueError(f"unknown algorithm result status: {status}")
    if status == "completed" and failure_class is not None:
        raise ValueError("completed result cannot have failure_class")
    metric_values = _json_object(metrics, "metrics")
    for key, value in metric_values.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(float(value)):
            raise ValueError(f"metrics.{key} must be finite")
    participant = _json_object(participant_report, "participant_report")
    eval_value = _json_object(evaluation, "evaluation")
    evidence_value = _json_object(evidence, "evidence")
    result = {
        "schema": ALGORITHM_RESULT_SCHEMA,
        "version": 1,
        "manifest": manifest,
        "manifest_sha256": canonical_sha256(manifest),
        "status": status,
        "failure_class": failure_class,
        "metrics": metric_values,
        "participant_report": participant,
        "evaluation": eval_value,
        "evidence": evidence_value,
    }
    validate_algorithm_result(result)
    return result


def validate_algorithm_result(result: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise ValueError("algorithm result must be an object")
    value = deepcopy(dict(result))
    if value.get("schema") != ALGORITHM_RESULT_SCHEMA or value.get("version") != 1:
        raise ValueError("unsupported algorithm result schema")
    manifest = validate_algorithm_run_manifest(value.get("manifest"))
    if value.get("manifest_sha256") != canonical_sha256(manifest):
        raise ValueError("algorithm result manifest binding mismatch")
    if value.get("status") not in _STATUSES:
        raise ValueError("invalid algorithm result status")
    for field in ("metrics", "participant_report", "evaluation", "evidence"):
        if not isinstance(value.get(field), Mapping):
            raise ValueError(f"algorithm result {field} must be an object")
        _json_object(value[field], f"algorithm result {field}")
    for key, metric in value["metrics"].items():
        if isinstance(metric, (int, float)) and not isinstance(metric, bool) and not math.isfinite(float(metric)):
            raise ValueError(f"algorithm result metrics.{key} must be finite")
    if value["status"] == "completed" and value.get("failure_class") is not None:
        raise ValueError("completed result cannot have failure_class")
    return value


def expand_algorithm_cases(
    algorithms: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
    seeds: Sequence[int],
    *,
    run_root: str,
    factor_treatments: Sequence[Mapping[str, Any]] = ({},),
    branch_ids: Sequence[str] = ("main",),
) -> list[dict[str, Any]]:
    """Expand algorithm × task × seed × factor × branch into isolated cases."""
    _required_text(run_root, "run_root")
    if not algorithms or not tasks or not seeds:
        raise ValueError("algorithms, tasks and seeds must be nonempty")
    cases: list[dict[str, Any]] = []
    used_roots: set[str] = set()
    for algorithm in algorithms:
        aid = _required_text(algorithm.get("id"), "algorithm.id")
        aversion = algorithm.get("version", 1)
        for task in tasks:
            common = {key: task.get(key) for key in (
                "benchmark_commit", "task_bundle_sha256", "scenario_sha256", "scene_key",
                "task_id", "provider_revision")}
            for key, value in common.items():
                _required_text(value, f"task.{key}")
            for seed in seeds:
                if type(seed) is not int:
                    raise ValueError("seeds must contain integers")
                for factor in factor_treatments:
                    for branch in branch_ids:
                        manifest = build_algorithm_run_manifest(
                            algorithm_id=aid, algorithm_version=aversion,
                            benchmark_commit=common["benchmark_commit"],
                            task_bundle_sha256=common["task_bundle_sha256"],
                            scenario_sha256=common["scenario_sha256"], scene_key=common["scene_key"],
                            task_id=common["task_id"], provider_revision=common["provider_revision"],
                            seed=seed, run_root=run_root, factor_treatment=factor,
                            runtime_config=task.get("runtime_config"), branch_id=branch)
                        root = f"{run_root.rstrip('/')}/{manifest['episode_id']}"
                        if root in used_roots:
                            raise ValueError("expanded cases must have unique episode run roots")
                        used_roots.add(root)
                        manifest["run_root"] = root
                        validate_algorithm_run_manifest(manifest)
                        cases.append(manifest)
    return cases


def run_algorithm_batch(
    manifests: Sequence[Mapping[str, Any]],
    runner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    *,
    max_workers: int = 1,
) -> list[dict[str, Any]]:
    """Run independent case callbacks with explicit ownership of parallelism.

    Parallel callbacks run in threads, so use them only for thread-safe offline
    work or submission to an external process scheduler. They must not create
    multiple Isaac applications in this process. This helper is not an Isaac
    multi-process or multi-machine scheduler; that orchestration remains an
    external developer-owned layer.
    """
    if not callable(runner) or type(max_workers) is not int or max_workers < 1:
        raise ValueError("runner must be callable and max_workers must be positive")
    cases = [validate_algorithm_run_manifest(case) for case in manifests]
    roots = [case["run_root"] for case in cases]
    if len(roots) != len(set(roots)):
        raise ValueError("each algorithm case must have an independent run_root")

    def one(case: Mapping[str, Any]) -> dict[str, Any]:
        try:
            raw = runner(deepcopy(case))
            if not isinstance(raw, Mapping):
                raise ValueError("runner must return an algorithm result object")
            result = validate_algorithm_result(raw)
            if result["manifest_sha256"] != canonical_sha256(case):
                raise ValueError("runner result is bound to a different manifest")
            return result
        except Exception as exc:
            return build_algorithm_result(
                case, status="failed", failure_class="runner_exception",
                evidence={"exception_type": type(exc).__name__, "message": str(exc)})

    if max_workers == 1 or len(cases) <= 1:
        return [one(case) for case in cases]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(one, case) for case in cases]
        return [future.result() for future in futures]


def run_algorithm_subprocess(
    command: Sequence[str],
    *,
    manifest: Mapping[str, Any],
    context: Mapping[str, Any],
    timeout_s: float,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one external decision process with a JSON-only stdin/stdout boundary.

    The process receives ``{"manifest": ..., "context": ...}`` on stdin and
    must emit exactly one JSON object on stdout.  It never receives a session,
    evaluator artifact or Isaac object; hidden evaluator keys are rejected
  before launch.  The child inherits only ``PATH`` unless explicit public
  environment variables are supplied.  This is an episode-level
  crash/timeout boundary only.  It
    deliberately does not schedule batches or remote machines.
    """
    if (not isinstance(command, Sequence) or isinstance(command, (str, bytes))
            or not command or any(not isinstance(item, str) or not item for item in command)):
        raise ValueError("command must be a nonempty argument list")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) \
            or not math.isfinite(float(timeout_s)) or timeout_s <= 0:
        raise ValueError("timeout_s must be a finite positive number")
    if not isinstance(manifest, Mapping):
        raise ValueError("algorithm manifest must be a public JSON object")
    if _contains_hidden_key(manifest):
        raise ValueError("algorithm manifest contains hidden evaluator fields")
    if not isinstance(context, Mapping):
        raise ValueError("algorithm context must be a public JSON object")
    if _contains_hidden_key(context):
        raise ValueError("algorithm context contains hidden evaluator fields")
    try:
        payload = json.dumps({"manifest": deepcopy(dict(manifest)),
                             "context": deepcopy(dict(context))},
                            sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("manifest/context must be JSON serializable") from exc
    # Do not inherit the host's full environment: deployments may carry
    # evaluator paths or credentials there.  Callers must opt in to any
    # additional public variables explicitly.
    process_env = {"PATH": os.environ.get("PATH", "")}
    if env is not None:
        if not isinstance(env, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in env.items()):
            raise ValueError("env must map strings to strings")
        process_env.update(env)
    process = None
    try:
        process = subprocess.Popen(
            list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=False, cwd=cwd, env=process_env,
            shell=False, start_new_session=(os.name == "posix"))
        try:
            stdout, stderr = process.communicate(
                (payload + "\n").encode("utf-8"), timeout=float(timeout_s))
        except subprocess.TimeoutExpired as exc:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
            return {"status": "timeout", "failure_class": "algorithm_timeout",
                    "returncode": process.returncode, "output": None,
                    "stdout": stdout.decode("utf-8", errors="replace") if stdout else "",
                    "stderr": stderr_text or str(exc)}
    except OSError as exc:
        return {"status": "failed", "failure_class": "algorithm_exception",
                "returncode": None, "output": None, "stdout": "", "stderr": str(exc)}
    try:
        stdout_text = stdout.decode("utf-8") if stdout else ""
    except UnicodeDecodeError as exc:
        return {"status": "invalid", "failure_class": "algorithm_protocol_invalid",
                "returncode": process.returncode, "output": None, "stdout": "",
                "stderr": f"stdout is not valid UTF-8: {exc}"}
    stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
    if process.returncode != 0:
        return {"status": "failed", "failure_class": "algorithm_exception",
                "returncode": process.returncode, "output": None,
                "stdout": stdout_text, "stderr": stderr_text}
    try:
        output = json.loads(
            stdout_text,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {token}")),
        )
    except (TypeError, ValueError) as exc:
        return {"status": "invalid", "failure_class": "algorithm_protocol_invalid",
                "returncode": process.returncode, "output": None,
                "stdout": stdout_text, "stderr": f"invalid JSON stdout: {exc}; {stderr_text}"}
    if not isinstance(output, Mapping) or _contains_hidden_key(output):
        return {"status": "invalid", "failure_class": "algorithm_protocol_invalid",
                "returncode": process.returncode, "output": None,
                "stdout": stdout_text, "stderr": "stdout must be one public JSON object"}
    return {"status": "completed", "failure_class": None,
            "returncode": process.returncode, "output": deepcopy(dict(output)),
            "stdout": stdout_text, "stderr": stderr_text}


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot compute a percentile of an empty sample")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _bootstrap_mean(values: Sequence[float], *, seed: int, samples: int) -> tuple[float, float]:
    if not values:
        raise ValueError("at least one numeric observation is required")
    if type(samples) is not int or samples < 1:
        raise ValueError("bootstrap samples must be positive")
    rng = random.Random(seed)
    if len(values) == 1:
        return values[0], values[0]
    draws = []
    for _ in range(samples):
        draws.append(sum(values[rng.randrange(len(values))] for _ in values) / len(values))
    return _percentile(draws, 0.025), _percentile(draws, 0.975)


def summarize_algorithm_metric(
    results: Sequence[Mapping[str, Any]],
    metric: str,
    *,
    bootstrap_samples: int = 2000,
    seed: int = 0,
) -> dict[str, Any]:
    """Summarize a metric by algorithm with deterministic bootstrap CIs."""
    if not isinstance(metric, str) or not metric:
        raise ValueError("metric must be a nonempty string")
    groups: dict[str, list[float]] = {}
    statuses: dict[str, dict[str, int]] = {}
    versions = {}
    episodes = set()
    for raw in results:
        result = validate_algorithm_result(raw)
        aid = result["manifest"]["algorithm"]["id"]
        version = canonical_sha256(result["manifest"]["algorithm"])
        if aid in versions and versions[aid] != version:
            raise ValueError("cannot pool different algorithm versions in one summary")
        versions[aid] = version
        episode = (aid, result["manifest"]["episode_id"])
        if episode in episodes:
            raise ValueError("duplicate algorithm episode in metric summary")
        episodes.add(episode)
        statuses.setdefault(aid, {}).setdefault(result["status"], 0)
        statuses[aid][result["status"]] += 1
        value = result["metrics"].get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        groups.setdefault(aid, []).append(float(value))
    summary = {}
    for aid, values in sorted(groups.items()):
        ci = _bootstrap_mean(values, seed=seed, samples=bootstrap_samples)
        summary[aid] = {
            "n": len(values), "mean": sum(values) / len(values), "median": median(values),
            "bootstrap_ci95": list(ci), "statuses": statuses.get(aid, {}),
            "missing_metric": sum(statuses[aid].values()) - len(values),
        }
    for aid, counts in statuses.items():
        summary.setdefault(aid, {"n": 0, "mean": None, "median": None,
                                  "bootstrap_ci95": None, "statuses": counts,
                                  "missing_metric": sum(counts.values())})
    return {"schema": "eai.benchmark.metric_summary", "version": 1,
            "metric": metric, "algorithms": summary, "bootstrap_samples": bootstrap_samples,
            "seed": seed}


def compare_paired_metric(
    results: Sequence[Mapping[str, Any]],
    baseline_id: str,
    candidate_id: str,
    metric: str,
    *,
    bootstrap_samples: int = 2000,
    seed: int = 0,
    higher_is_better: bool = True,
) -> dict[str, Any]:
    """Compare algorithms on identical task/seed/factor comparison keys."""
    _required_text(baseline_id, "baseline_id")
    _required_text(candidate_id, "candidate_id")
    if baseline_id == candidate_id:
        raise ValueError("baseline and candidate must differ")
    if type(higher_is_better) is not bool:
        raise ValueError("higher_is_better must be boolean")
    by_key: dict[str, dict[str, float]] = {}
    contexts = {}
    versions = {}
    seen = set()
    for raw in results:
        result = validate_algorithm_result(raw)
        aid = result["manifest"]["algorithm"]["id"]
        if aid not in {baseline_id, candidate_id}:
            continue
        algorithm = result["manifest"]["algorithm"]
        version = canonical_sha256(algorithm)
        if aid in versions and versions[aid] != version:
            raise ValueError("cannot pool different algorithm versions in one comparison")
        versions[aid] = version
        key = result["manifest"]["comparison_key"]
        identity = (aid, key)
        if identity in seen:
            raise ValueError(f"duplicate {aid} result for comparison key {key}")
        seen.add(identity)
        context = canonical_sha256(_comparison_context(result["manifest"]))
        if key in contexts and contexts[key] != context:
            raise ValueError("paired comparison has mismatched frozen inputs or controls")
        contexts[key] = context
        by_key.setdefault(key, {})
        value = result["metrics"].get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        by_key[key][aid] = float(value)
    paired = [(row[baseline_id], row[candidate_id]) for row in by_key.values()
              if baseline_id in row and candidate_id in row]
    differences = [candidate - baseline for baseline, candidate in paired]
    if differences:
        ci = _bootstrap_mean(differences, seed=seed, samples=bootstrap_samples)
        mean_difference = sum(differences) / len(differences)
    else:
        ci = None
        mean_difference = None
    return {
        "schema": "eai.benchmark.paired_comparison", "version": 1, "metric": metric,
        "baseline_id": baseline_id, "candidate_id": candidate_id, "n_pairs": len(paired),
        "higher_is_better": higher_is_better,
        "mean_difference_candidate_minus_baseline": mean_difference,
        "bootstrap_ci95": list(ci) if ci is not None else None,
        "wins": sum((value > 0 if higher_is_better else value < 0) for value in differences),
        "ties": sum(value == 0 for value in differences),
        "losses": sum((value < 0 if higher_is_better else value > 0) for value in differences),
        "missing_both_metrics": sum(not row for row in by_key.values()),
        "missing_baseline": sum(baseline_id not in row and candidate_id in row for row in by_key.values()),
        "missing_candidate": sum(candidate_id not in row and baseline_id in row for row in by_key.values()),
    }


def holm_bonferroni(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return Holm-adjusted p-values for a family of comparisons."""
    if not isinstance(p_values, Mapping):
        raise ValueError("p_values must be an object")
    rows = []
    for key, value in p_values.items():
        if not isinstance(key, str) or not key:
            raise ValueError("p-value keys must be nonempty strings")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("p-values must be between 0 and 1")
        rows.append((key, float(value)))
    rows.sort(key=lambda row: row[1])
    count = len(rows)
    adjusted: dict[str, float] = {}
    running = 0.0
    for index, (key, value) in enumerate(rows):
        corrected = min(1.0, (count - index) * value)
        running = max(running, corrected)
        adjusted[key] = running
    return adjusted


__all__ = [
    "ADAPTER_INTERFACE_VERSION", "ALGORITHM_RESULT_SCHEMA", "AlgorithmAdapter",
    "CallableAlgorithmAdapter", "PlannerAdapter", "RUN_MANIFEST_SCHEMA",
    "DEFAULT_CONTROL_STACK",
    "build_algorithm_result", "build_algorithm_run_manifest", "compare_paired_metric",
    "expand_algorithm_cases", "holm_bonferroni", "make_algorithm_adapter",
    "run_algorithm_batch", "run_algorithm_subprocess", "summarize_algorithm_metric",
    "validate_algorithm_result",
    "validate_algorithm_run_manifest",
]
