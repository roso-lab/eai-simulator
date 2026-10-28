"""JSON-to-native-EMOS IO configuration and a live-only decision CLI.

Run: python -m tools.algorithm_adapters.emos_config --config CONFIG --report REPORT
No fixture transport, dynamic import, fallback planner or prompt override is
selectable through JSON. Endpoint configuration contains environment names only.
The shared factory can call build_emos_from_config and own source validation;
this standalone CLI checks the registered source before invoking native code.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping

from algorithm.common_coordination.contract import CoordinationTask, RobotCapability, TaskGraph
from algorithm.system.coordination.emos.native_adapter import (
    EmosNativeAdapter, MeteredNativeEndpoint, NativeEmosError, NativeEmosUnavailable,
)
from algorithm.system.coordination.emos.policy import AssignmentRequest
from algorithm.system.coordination.emos.types import (
    EMOSScenarioConfig, PositionRule, RobotProfileSpec, SubtaskConstraintSpec, SubtaskSpec,
    resolve_subtask_target_xy,
)
from algorithm.system.coordination.native_runtime import verify_source
from benchmark.emos import CaseAssignmentRequest
from task.contracts import EaiTaskPackage, RobotState


SCHEMA = "heterocoord.emos_native_config.v1"
_TOP_KEYS = {"schema", "request", "robot_resume", "observation", "history", "endpoint",
             "source_root", "log_dir", "output_bindings", "initialization_budget_calls",
             "task_package", "input_kind"}


class EmosConfigError(ValueError):
    """Invalid JSON IO contract; error messages never echo supplied values."""


def _object(value, label):
    if not isinstance(value, Mapping):
        raise EmosConfigError(label + " must be an object")
    return deepcopy(dict(value))


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise EmosConfigError(label + " must be a non-empty string")
    return value


def _ids(value, label):
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ) or len(set(value)) != len(value):
        raise EmosConfigError(label + " must be a list of unique string IDs")
    return tuple(value)


def _vector(value, size, label):
    if not isinstance(value, (list, tuple)) or len(value) != size or any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
        for item in value
    ):
        raise EmosConfigError(label + " must be a finite numeric vector")
    return tuple(float(item) for item in value)


def _positions(value, size, label):
    data = _object(value, label)
    return {_text(key, label): _vector(item, size, label) for key, item in data.items()}


def _construct(cls, data, label):
    try:
        return cls(**data)
    except (TypeError, ValueError, KeyError, AttributeError):
        raise EmosConfigError("invalid " + label + " fields") from None


def request_from_dict(data: Mapping[str, Any]) -> AssignmentRequest | CaseAssignmentRequest:
    """Explicit kind: case or fire_rescue; no fixture/default scenario selection.

    fire_rescue.scenario is the dataclass JSON representation: PositionRule uses
    'kind', RobotProfileSpec uses 'robot_type'. json.dumps(asdict(request)) works
    after adding kind='fire_rescue'. No scenario prompt generation is performed.
    """
    data = _object(data, "request")
    kind = data.pop("kind", None)
    if kind not in {"case", "fire_rescue"}:
        raise EmosConfigError("request.kind must be case or fire_rescue")
    _text(data.get("task_description"), "request.task_description")
    data["robot_positions"] = _positions(data.get("robot_positions"), 3, "request.robot_positions")
    if not data["robot_positions"]:
        raise EmosConfigError("request.robot_positions cannot be empty")
    if kind == "case":
        _text(data.get("scenario_id"), "request.scenario_id")
        data["task_ids"] = _ids(data.get("task_ids"), "request.task_ids")
        for field in ("completed_task_ids", "excluded_task_ids"):
            if field in data:
                data[field] = _ids(data[field], "request." + field)
        return _construct(CaseAssignmentRequest, data, "CaseAssignmentRequest")
    scenario = _object(data.get("scenario"), "request.scenario")
    for field in ("scenario_id", "scene_title", "anchor_label", "task_description"):
        _text(scenario.get(field), "scenario." + field)
    for field, cls in (("subtasks", SubtaskSpec), ("position_rules", PositionRule),
                       ("robot_profiles", RobotProfileSpec)):
        scenario[field] = {key: _construct(cls, _object(value, field), field)
                           for key, value in _object(scenario.get(field), field).items()}
    if "subtask_constraints" in scenario:
        if not isinstance(scenario["subtask_constraints"], list):
            raise EmosConfigError("subtask_constraints must be a list")
        scenario["subtask_constraints"] = [_construct(SubtaskConstraintSpec, item, "subtask_constraints")
                                            for item in scenario["subtask_constraints"]]
    data["scenario"] = _construct(EMOSScenarioConfig, scenario, "EMOSScenarioConfig")
    if not data["scenario"].subtasks or any(key != value.id for key, value in data["scenario"].subtasks.items()):
        raise EmosConfigError("scenario requires consistent explicit subtask IDs")
    if type(data.get("hazard_id")) is not int:
        raise EmosConfigError("request.hazard_id must be an integer")
    data["hazard_pos"] = _vector(data.get("hazard_pos"), 3, "request.hazard_pos")
    data["subtask_positions"] = _positions(data.get("subtask_positions"), 2, "request.subtask_positions")
    for field in ("completed_extinguisher", "completed_rescue", "excluded_subtask_ids"):
        data[field] = _ids(data.get(field, []), "request." + field)
    if set(data["excluded_subtask_ids"]) - set(data["scenario"].subtasks):
        raise EmosConfigError("unknown excluded subtask ID")
    request = _construct(AssignmentRequest, data, "AssignmentRequest")
    for task_id in request.scenario.subtasks:
        if task_id not in request.excluded_subtask_ids:
            try:
                resolve_subtask_target_xy(request.scenario, task_id, request.subtask_positions)
            except ValueError:
                raise EmosConfigError("invalid explicit subtask target") from None
    return request


def task_package_from_dict(data: Mapping[str, Any]) -> EaiTaskPackage:
    """Same task/robot JSON shape as EaiTaskPackage.from_json, without temp files."""
    data = _object(data, "task_package")
    if set(data) - {"task_id", "scene_id", "tasks", "robots", "metadata", "observation_schema"}:
        raise EmosConfigError("unknown task_package fields")
    _text(data.get("task_id"), "task_package.task_id")
    _text(data.get("scene_id"), "task_package.scene_id")
    tasks = data.pop("tasks", None)
    robots = data.pop("robots", None)
    if not isinstance(tasks, list) or not tasks or not isinstance(robots, list) or not robots:
        raise EmosConfigError("task_package requires explicit tasks and robots lists")
    task_records = []
    for raw in tasks:
        item = _object(raw, "task")
        for field in ("task_id", "description"):
            _text(item.get(field), "task." + field)
        for field in ("required_capabilities", "prerequisites"):
            if field in item:
                item[field] = _ids(item[field], "task." + field)
        task_records.append(_construct(CoordinationTask, item, "CoordinationTask"))
    robot_records = []
    for raw in robots:
        item = _object(raw, "robot")
        if set(item) - {"robot_id", "capabilities", "active", "position", "metadata"}:
            raise EmosConfigError("unknown robot fields")
        robot_id = _text(item.get("robot_id"), "robot.robot_id")
        capabilities = _ids(item.get("capabilities"), "robot.capabilities")
        active = item.get("active", True)
        if type(active) is not bool:
            raise EmosConfigError("robot.active must be a boolean")
        robot_records.append(RobotState(RobotCapability(robot_id, frozenset(capabilities), active),
                                        _vector(item.get("position"), 3, "robot.position"),
                                        _object(item.get("metadata", {}), "robot.metadata")))
    return _construct(EaiTaskPackage, {**data, "graph": TaskGraph(tuple(task_records), source=SCHEMA),
                                      "robots": tuple(robot_records)}, "EaiTaskPackage")


def endpoint_settings(data: Mapping[str, Any]) -> dict:
    data = _object(data, "endpoint")
    if set(data) - {"api_key_env", "base_url_env", "model_env", "reasoning_effort_env",
                    "output_contract_version", "max_calls", "timeout"}:
        raise EmosConfigError("endpoint accepts environment references and limits only")
    for field in ("api_key_env", "base_url_env", "model_env"):
        value = data.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise EmosConfigError("endpoint requires API key, base URL and model environment variable names")
    if len({data[field] for field in ("api_key_env", "base_url_env", "model_env")}) != 3:
        raise EmosConfigError("endpoint environment references must be distinct")
    if "reasoning_effort_env" in data:
        if not isinstance(data["reasoning_effort_env"], str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", data["reasoning_effort_env"]
        ):
            raise EmosConfigError("endpoint.reasoning_effort_env must be a variable name")
    if "output_contract_version" in data and data["output_contract_version"] not in {
        None, "emos.native-output.v1"
    }:
        raise EmosConfigError("unsupported EMOS output contract version")
    data.setdefault("output_contract_version", None)
    data.setdefault("max_calls", 32)
    data.setdefault("timeout", 30.0)
    if type(data["max_calls"]) is not int or data["max_calls"] < 1:
        raise EmosConfigError("endpoint.max_calls must be a positive integer")
    timeout = data["timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise EmosConfigError("endpoint.timeout must be positive and finite")
    return data


def endpoint_presence(data: Mapping[str, Any]) -> dict:
    """Names and booleans only: never URL, model or credential values."""
    settings = endpoint_settings(data)
    result = {field: {"name": settings[field], "is_set": bool(os.environ.get(settings[field]))}
              for field in ("api_key_env", "base_url_env", "model_env")}
    if "reasoning_effort_env" in settings:
        result["reasoning_effort_env"] = {
            "name": settings["reasoning_effort_env"],
            "is_set": bool(os.environ.get(settings["reasoning_effort_env"])),
        }
    return result


def _path(value, base_dir):
    path = Path(_text(value, "path")).expanduser()
    return (path if path.is_absolute() else Path(base_dir) / path).resolve()


def _validated_adapter(config, *, source_root=None, base_dir=Path(".")):
    config = _object(config, "config")
    if config.get("schema") != SCHEMA or set(config) - _TOP_KEYS:
        raise EmosConfigError("unsupported EMOS JSON config schema or fields")
    settings = endpoint_settings(config.get("endpoint"))
    request = request_from_dict(config.get("request"))
    observation = _object(config.get("observation"), "observation")
    observation["robot_positions"] = _positions(observation.get("robot_positions"), 3,
                                                "observation.robot_positions")
    root = source_root if source_root is not None else config.get("source_root")
    if root is not None:
        root = _path(str(root), base_dir)
    adapter = EmosNativeAdapter(request, robot_resume=_object(config.get("robot_resume"), "robot_resume"),
                                source_root=root, log_dir=_path(config.get("log_dir"), base_dir),
                                output_bindings=_object(config.get("output_bindings", {}), "output_bindings"),
                                initialization_budget_calls=config.get("initialization_budget_calls", 1))
    adapter.initialize(_object(config.get("history", {}), "history"), observation)
    # Validate history-updated request through the same strict JSON contract.
    kind = "case" if isinstance(adapter.request, CaseAssignmentRequest) else "fire_rescue"
    request_from_dict({"kind": kind, **asdict(adapter.request)})
    if "task_package" in config:
        task = task_package_from_dict(config["task_package"])
        positions = {r.capability.robot_id: r.position for r in task.robots}
        if positions != adapter.request.robot_positions:
            raise EmosConfigError("task_package robot positions/roster must match initialized observation")
        scenario = (adapter.request.scenario_id if kind == "case" else adapter.request.scenario.scenario_id)
        if task.task_id != scenario and task.metadata.get("scenario_id") != scenario:
            raise EmosConfigError("task_package scenario does not match initialized request")
        ids = {t.task_id for t in task.graph.tasks}
        known = (set(adapter.request.task_ids) if kind == "case" else set(adapter.request.scenario.subtasks))
        if ids != known:
            raise EmosConfigError("task_package task IDs must match initialized request")
    return adapter, settings


def build_emos_from_config(config: Mapping[str, Any], *, source_root=None,
                           base_dir: str | Path = Path(".")) -> EmosNativeAdapter:
    """Return an initialized live-only provider; parent owns its source gate.

    JSON config is the schema above, not arbitrary Python kwargs. A factory can
    omit task_package when its independently bound task package is supplied later.
    Relative config paths are interpreted against base_dir. No network call occurs
    until the returned provider is invoked. No injectable client in this entry.
    """
    adapter, settings = _validated_adapter(config, source_root=source_root, base_dir=base_dir)
    try:
        adapter.endpoint = MeteredNativeEndpoint.from_environment(**settings)
    except NativeEmosUnavailable as exc:
        # No client/requests exist yet. Preserve a counted failure for the shared
        # factory/CLI without changing its exception type or supplying fallback.
        exc.model_requests = 0
        exc.native_subtasks = {}
        exc.endpoint_receipt = {"completion_attempts": 0, "failed_attempts": 0,
                                "total_tokens": 0, "max_calls": settings["max_calls"],
                                "protocol_only": False}
        raise
    return adapter


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EmosConfigError("duplicate JSON object key")
        result[key] = value
    return result


def load_config(path: str | Path) -> dict:
    try:
        return _object(json.loads(Path(path).read_text(encoding="utf-8"),
                                  object_pairs_hook=_unique_object), "config")
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise EmosConfigError("cannot read a valid JSON configuration") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, help="explicit trusted native checkout override")
    args = parser.parse_args(argv)
    started = time.monotonic()
    report = {"schema": "heterocoord.emos_native_cli_receipt.v1", "algorithm": "EMOS",
              "timestamp_utc": datetime.now(timezone.utc).isoformat(), "mode": "native_api_only",
              "algorithm_acceptance": "not_passed", "physical_acceptance": "not_evaluated",
              "fallback_used": False, "model_requests": 0}
    adapter = None
    code = 1
    try:
        if args.config.resolve() == args.report.resolve():
            raise EmosConfigError("report cannot overwrite config")
        config = load_config(args.config)
        task = task_package_from_dict(config.get("task_package"))
        adapter, settings = _validated_adapter(config, source_root=args.source_root, base_dir=args.config.resolve().parent)
        report["endpoint_environment"] = endpoint_presence(settings)
        # Reference registry is independently owned; never accept a revision
        # supplied by a config as a substitute for the registered source.
        registry = Path(__file__).resolve().parents[2] / "algorithm/heterocoord/paper_algorithms.json"
        commit = json.loads(registry.read_text())["algorithms"]["emos"]["commit"]
        if not adapter.source_root.is_dir():
            raise NativeEmosUnavailable("native EMOS source directory is unavailable")
        report["source_receipt"] = verify_source(adapter.source_root, commit).to_dict()
        adapter.endpoint = MeteredNativeEndpoint.from_environment(**settings)
        adapter.log_dir.mkdir(parents=True, exist_ok=True)
        with (adapter.log_dir / "cli_native_stdout.txt").open("w") as stream, redirect_stdout(stream):
            decision = adapter(task, task.robots)
        report.update(status="native_decision_returned", decision=asdict(decision))
        code = 0
    except NativeEmosUnavailable as exc:
        report.update(status="blocked", error_type=type(exc).__name__, reason=str(exc))
        code = 2
    except Exception as exc:
        # No traceback/config/provider response dump: arbitrary errors may contain
        # credential values. Raw native outputs remain in the original log files.
        report.update(status="failed_closed", error_type=type(exc).__name__,
                      reason="native configuration, source validation or discussion failed; no fallback")
    finally:
        if adapter is not None:
            if adapter.endpoint is not None:
                report["endpoint"] = adapter.endpoint.receipt()
                report["model_requests"] = adapter.endpoint.calls
            report["raw_native_subtasks"] = {key: value.subtask_description
                                              for key, value in adapter.native_results.items()}
            adapter.close()
        report["elapsed_wall_s"] = time.monotonic() - started
    # Do not destroy a config even on an invalid destination.
    if args.report.resolve() == args.config.resolve():
        print(json.dumps({"status": "failed_closed", "reason": "report cannot overwrite config"}))
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"status": report["status"], "report": str(args.report.resolve()),
                      "model_requests": report["model_requests"]}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
