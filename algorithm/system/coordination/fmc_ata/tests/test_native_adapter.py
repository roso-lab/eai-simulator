"""Contract tests plus opt-in real upstream execution (never substitute a solver).

Run with FMC_ATA_SOURCE_ROOT and optionally FMC_ATA_PYTHON. Missing sources skip
native tests explicitly. --import-mode=importlib also permits running these tests
against the frozen origin TaskRunner using that checkout on PYTHONPATH.
"""
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

import pytest

from algorithm.common_coordination.contract import CoordinationTask, RobotCapability, TaskGraph
from task.contracts import EaiTaskPackage, RobotState, TaskRunRequest
from task.runner import TaskRunner


ADAPTER_PATH = Path(__file__).resolve().parents[1] / "native_adapter.py"
SPEC = importlib.util.spec_from_file_location("fmc_ata_native_under_test", ADAPTER_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
NativeFMCATAAdapter = MODULE.NativeFMCATAAdapter
NativeFMCATAError = MODULE.NativeFMCATAError
NativeFMCATAScheduleError = MODULE.NativeFMCATAScheduleError
UnsupportedFMCATADomain = MODULE.UnsupportedFMCATADomain


def package(*, positions=(0, 1000000), targets=(1000, 999000), workload=100, importance=1000,
            abilities=("work", "work"), required=("work", "work")):
    robots = tuple(RobotState(
        RobotCapability(f"r{i}", frozenset({ability})), (x, 0, 0),
        {"fmc_ata": {"speed": 1, "productivity": 1, "status": "idle"}},
    ) for i, (x, ability) in enumerate(zip(positions, abilities)))
    tasks = tuple(CoordinationTask(
        f"t{i}", "Declared native mission", frozenset({ability}),
        metadata={"fmc_ata": {"position": [x, 0, 0], "workload": workload,
                              "importance": importance, "max_players": 1}},
    ) for i, (x, ability) in enumerate(zip(targets, required)))
    return EaiTaskPackage("native-fixture", "no-physical-scene", TaskGraph(tasks, "test-fixture"),
                          robots, metadata={"fmc_ata": {"domain": MODULE.SUPPORTED_DOMAIN}})


@pytest.fixture
def native_adapter():
    root = Path(os.environ.get("FMC_ATA_SOURCE_ROOT", MODULE.DEFAULT_SOURCE_ROOT))
    python = Path(os.environ.get("FMC_ATA_PYTHON", MODULE.DEFAULT_PYTHON))
    if not (root / "solver_fmc_distributed_asy.py").is_file() or not python.is_file():
        pytest.skip("real upstream source and isolated venv required; native evidence unavailable")
    return NativeFMCATAAdapter(root, python_executable=python, timeout_seconds=10)


def source_snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


def admitted_native(call, *, allow_known_single_player_timeout=False):
    """Known upstream failures are XFAIL, explicitly NOT native passed evidence.

    No retry: asynchronous termination can leave incomplete/stale schedules.
    The original static driver also documents its single-task termination TODO.
    The evidence harness separately retains every admitted/rejected/timeout run.
    """
    try:
        return call()
    except NativeFMCATAScheduleError as exc:
        assert exc.evidence["native_threads_joined"]
        assert exc.evidence["lifecycle"]["worker_reaped"]
        pytest.xfail(f"UPSTREAM_LIMITATION (not native pass): {exc}")
    except NativeFMCATAError as exc:
        if allow_known_single_player_timeout and exc.evidence.get("native_execution") == "timeout":
            assert exc.evidence["worker_reaped"]
            pytest.xfail(f"UPSTREAM_LIMITATION (not native pass): {exc}")
        raise


def assert_native(decision, expected_count=2):
    raw = decision.provenance["native_output"]
    assert raw["solver_class"] == "solver_fmc_distributed_asy.FMC_ATA"
    assert raw["messages"]["sent"] > 0
    assert raw["messages"]["received"] > 0
    assert raw["all_tasks_converged"] and not raw["nclo_limit_reached"]
    assert raw["native_threads_joined"]
    assert raw["lifecycle"]["worker_reaped"] and raw["lifecycle"]["worker_returncode"] == 0
    assert len(decision.assignments) == expected_count
    assert not decision.unassigned_tasks
    assert decision.provenance["simulation_evidence"] == "not_run"
    assert "solver_fmc_distributed_asy.py" in raw["imported_source_sha256"]
    with pytest.raises(ProcessLookupError):
        os.kill(raw["lifecycle"]["worker_pid"], 0)
    # JSON export retains non-integral native market shares and received contexts.
    json.dumps(decision.provenance, allow_nan=False)
    return raw


def test_real_native_solver_to_taskrunner_no_upstream_mutation(native_adapter):
    before = source_snapshot(native_adapter.source_root)
    task = package()
    try:
        result = admitted_native(lambda: TaskRunner().run(TaskRunRequest(task), native_adapter))
    finally:
        assert source_snapshot(native_adapter.source_root) == before
    raw = assert_native(result.decision)
    assert result.verdict["status"] == "N/A"
    assert result.verdict["reason"] == "physical_evidence_required"
    assert all(f.outcome == "not_executed" for f in result.feedback)
    fractions = [v for t in raw["tasks"] for allocation in t["x_jk"].values()
                 for v in allocation.values() if v is not None]
    assert any(0 < v < 1 for v in fractions)
    assert any(p["last_received_messages"] for p in raw["players"])
    assert source_snapshot(native_adapter.source_root) == before


def test_real_changed_robot_positions_and_repeat_lifecycle(native_adapter):
    first = package()
    changed = package(positions=(1000000, 0))
    first_decision = admitted_native(lambda: native_adapter(first, first.robots))
    changed_decision = admitted_native(lambda: native_adapter(changed, changed.robots))
    first_raw, changed_raw = assert_native(first_decision), assert_native(changed_decision)
    assert {(a.task_id, a.robot_id) for a in first_decision.assignments} == {("t0", "r0"), ("t1", "r1")}
    assert {(a.task_id, a.robot_id) for a in changed_decision.assignments} == {("t0", "r1"), ("t1", "r0")}
    assert first_raw["lifecycle"]["worker_pid"] != changed_raw["lifecycle"]["worker_pid"]
    assert first_raw["players"][0]["utility"] != changed_raw["players"][0]["utility"]


def test_real_changed_tasks_and_ignored_preferred_mapping(native_adapter):
    task = package(targets=(999000, 1000), workload=37, importance=400)
    task = replace(task, metadata={**task.metadata, "preferred_mapping": {"t0": "r0", "t1": "r1"}})
    decision = admitted_native(lambda: native_adapter(task, task.robots))
    raw = assert_native(decision)
    assert {(a.task_id, a.robot_id) for a in decision.assignments} == {("t0", "r1"), ("t1", "r0")}
    assert all(e["duration"] == 37 for p in raw["players"] for e in p["schedule"])
    assert raw["players"][0]["utility"]["1"]["1"]["linear_utility"] < 400


def test_real_heterogeneous_native_abilities(native_adapter):
    task = package(abilities=("inspect", "carry"), required=("inspect", "carry"))
    result = admitted_native(lambda: TaskRunner().run(TaskRunRequest(task), native_adapter))
    decision = result.decision
    assert_native(decision)
    assert result.verdict["status"] == "N/A"
    assert all(f.outcome == "not_executed" for f in result.feedback)
    assert {(a.task_id, a.robot_id) for a in decision.assignments} == {("t0", "r0"), ("t1", "r1")}


def test_real_multiple_missions_preserve_native_robot_order(native_adapter):
    task = package(positions=(0,), targets=(1000, 999000), abilities=("work",))
    decision = admitted_native(lambda: native_adapter(task, task.robots), allow_known_single_player_timeout=True)
    raw = assert_native(decision)
    native_order = [e["task_id"] for e in raw["players"][0]["schedule"]]
    eai_order = [int(a.task_id[1:]) + 1 for a in decision.assignments]
    assert eai_order == native_order


def test_real_timeout_kills_and_reaps_process(native_adapter):
    native_adapter.timeout_seconds = 0.001
    task = package()
    start = time.monotonic()
    with pytest.raises(NativeFMCATAError, match="timeout") as caught:
        native_adapter(task, task.robots)
    assert time.monotonic() - start < 2
    assert caught.value.evidence["worker_reaped"]
    with pytest.raises(ProcessLookupError):
        os.kill(caught.value.evidence["worker_pid"], 0)


def test_real_nclo_budget_cannot_count_as_passed(native_adapter):
    native_adapter.max_nclo = 1
    task = package()
    with pytest.raises(NativeFMCATAError, match="NCLO budget") as caught:
        native_adapter(task, task.robots)
    assert caught.value.evidence["nclo_limit_reached"]


@pytest.mark.parametrize("field,value", [
    ("max_players", 2), ("max_players", True), ("workload", 0),
    ("importance", float("nan")), ("position", [0, 1]), ("arrival_time", 12),
])
def test_unsupported_task_inputs_fail_before_native_launch(field, value):
    task = package()
    original = task.graph.tasks[0]
    cfg = {**original.metadata["fmc_ata"], field: value}
    bad = replace(original, metadata={"fmc_ata": cfg})
    task = replace(task, graph=TaskGraph((bad, task.graph.tasks[1]), "test"))
    with pytest.raises(UnsupportedFMCATADomain):
        NativeFMCATAAdapter("/missing")(task, task.robots)


def test_dependencies_cannot_be_silently_dropped():
    task = package()
    second = replace(task.graph.tasks[1], prerequisites=("t0",))
    task = replace(task, graph=TaskGraph((task.graph.tasks[0], second), "test"))
    with pytest.raises(UnsupportedFMCATADomain, match="independent"):
        NativeFMCATAAdapter("/missing")(task, task.robots)


def test_empty_task_graph_rejected_before_launch():
    with pytest.raises(Exception, match="task graph cannot be empty"):
        TaskGraph((), "empty")


def test_changed_runtime_roster_must_be_explicitly_bound():
    task = package()
    robots = (replace(task.robots[0], position=(5, 0, 0)), task.robots[1])
    with pytest.raises(UnsupportedFMCATADomain, match="bind_runtime"):
        NativeFMCATAAdapter("/missing")._encode(task, robots)
    assert NativeFMCATAAdapter("/missing")._encode(task.bind_runtime(robots=robots), robots)["players"][0]["position"] == [5, 0, 0]


def test_single_task_timeout_retains_native_stacks_without_dispatch(native_adapter):
    task = package(positions=(0,), targets=(.45,), abilities=("work",), required=("work",), workload=30, importance=1)
    native_adapter.timeout_seconds = 3
    with pytest.raises(NativeFMCATAError, match="wall timeout") as caught:
        native_adapter(task, task.robots)
    evidence = caught.value.evidence
    assert evidence["worker_reaped"] and evidence["assignments_emitted"] is False
    assert "solver_abstract.py" in evidence["worker_log_tail"]
    snapshot = evidence["diagnostic_snapshot"]
    assert snapshot["scope"] == "read_only_nonatomic_timeout_diagnostic_not_a_decision"
    assert snapshot["task_phase_II"] == {"1": False}
    assert snapshot["pending_mailer_messages"] == snapshot["mailer_inbox_batches"] == 0
    assert snapshot["native_player_schedules"]["-1"][0]["duration"] == 30


def test_domain_declaration_required():
    task = replace(package(), metadata={})
    with pytest.raises(UnsupportedFMCATADomain, match="declare"):
        NativeFMCATAAdapter("/missing")(task, task.robots)


def test_busy_multi_ability_and_missing_capability_rejected():
    task = package()
    for robot in (
        replace(task.robots[0], capability=RobotCapability("r0", frozenset({"work", "inspect"}))),
        replace(task.robots[0], metadata={"fmc_ata": {"status": "busy", "speed": 1, "productivity": 1}}),
        replace(task.robots[0], capability=RobotCapability("r0", frozenset({"other"}))),
    ):
        with pytest.raises(UnsupportedFMCATADomain):
            NativeFMCATAAdapter("/missing")(task, (robot,))


def test_missing_native_source_never_falls_back():
    task = package()
    with pytest.raises(NativeFMCATAError, match="unavailable"):
        NativeFMCATAAdapter("/missing")(task, task.robots)


def test_environment_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("FMC_ATA_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("FMC_ATA_PYTHON", "/explicit/python")
    adapter = NativeFMCATAAdapter()
    assert adapter.source_root == tmp_path
    assert adapter.python_executable == "/explicit/python"


def test_fractional_or_partial_native_output_rejected(native_adapter, monkeypatch):
    # Corrupt a real solver output solely to test fail-closed output validation.
    # This test is contract evidence, never a claimed native solve for that output.
    task = package(abilities=("inspect", "carry"), required=("inspect", "carry"))
    raw = native_adapter._run_native(native_adapter._encode(task, task.robots))
    raw["players"][0]["schedule"][0]["normalized_allocation"] = 0.6
    monkeypatch.setattr(native_adapter, "_run_native", lambda payload: raw)
    with pytest.raises(NativeFMCATAError, match="whole single-player"):
        native_adapter(task, task.robots)
    for player in raw["players"]:
        player["schedule"] = []
    with pytest.raises(NativeFMCATAError, match="cover"):
        native_adapter(task, task.robots)


def write_native_evidence(output_path):
    """Execute a fixed batch; retain every result, including all native failures.

    Repetitions measure asynchronous variation; they are not retries to obtain a
    successful decision. The provider itself always executes exactly one solve.
    """
    from collections import Counter
    from datetime import datetime, timezone
    import importlib.metadata
    import subprocess

    source = Path(os.environ.get("FMC_ATA_SOURCE_ROOT", MODULE.DEFAULT_SOURCE_ROOT))
    before = source_snapshot(source)
    cases = []
    for repetition in range(3):
        for name, kwargs in (
            ("baseline", {}), ("swapped_robot_positions", {"positions": (1000000, 0)}),
            ("changed_task_positions_workload_importance", {
                "targets": (999000, 1000), "workload": 37, "importance": 400}),
        ):
            cases.append((name, repetition, package(**kwargs), {}))
    cases.extend([
        ("heterogeneous", 0, package(abilities=("inspect", "carry"), required=("inspect", "carry")), {}),
        ("single_robot_two_tasks", 0, package(positions=(0,), abilities=("work",)), {}),
        ("native_single_task_termination_boundary", 0,
         package(positions=(0,), abilities=("work",), targets=(1000,), required=("work",)),
         {"timeout_seconds": 2}),
        ("nclo_budget_exhaustion", 0, package(), {"max_nclo": 1}),
    ])
    records = []
    for name, repetition, task, options in cases:
        adapter = NativeFMCATAAdapter(source, **({"timeout_seconds": 5} | options))
        record = {"case": name, "repetition": repetition, "native_input": adapter._encode(task, task.robots),
                  "timeout_seconds": adapter.timeout_seconds}
        try:
            result = TaskRunner().run(TaskRunRequest(task), adapter)
            assert_native(result.decision)
            record.update(status="native_schedule_admitted", run=result.to_dict(),
                          provenance=dict(result.decision.provenance))
        except NativeFMCATAScheduleError as exc:
            record.update(status="native_schedule_rejected", error=str(exc), native_output=exc.evidence)
        except NativeFMCATAError as exc:
            record.update(status="native_timeout" if exc.evidence.get("native_execution") == "timeout"
                          else "native_failure", error=str(exc), native_output=exc.evidence)
        records.append(record)
    after = source_snapshot(source)
    runner_path = Path(importlib.import_module("task.runner").__file__)
    evidence = {
        "schema": "heterocoord.fmc_ata.native_evidence.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source), "upstream_commit": subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
        "parent_contract_reference": "origin/HeteroCoord@769e31d8",
        "runner_file": str(runner_path), "runner_sha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
        "adapter_sha256": hashlib.sha256(ADAPTER_PATH.read_bytes()).hexdigest(),
        "source_before_sha256": before, "source_after_sha256": after,
        "source_unchanged": before == after,
        "environment_versions": {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "pytest")},
        "counts": dict(Counter(r["status"] for r in records)),
        "total_runs": len(records),
        "contract_evidence": "TaskRunner_called_for_every_case; only_whole_schedules_admitted",
        "simulation_evidence": "not_run", "physical_evidence": "not_evaluated",
        "known_limitation": "original_async_termination_may_leave_incomplete_or_stale_schedules_or_hang; no_repair_or_retry",
        "runs": records,
    }
    assert evidence["source_unchanged"]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "counts": evidence["counts"], "total_runs": len(records)}))


def unified_task_document(task_count=4, *, heterogeneous=False):
    """Independent native tasks; two tasks per robot are not split missions."""
    abilities = ("inspect", "carry") if heterogeneous else ("work", "work")
    document = {
        "task_id": f"fmc-ata-unified-{task_count}-tasks",
        "scene_id": "native-only-no-physical-scene",
        "metadata": {"fmc_ata": {"domain": MODULE.SUPPORTED_DOMAIN}},
        "robots": [{"robot_id": f"r{i}", "capabilities": [ability],
                    "position": [i * 1000000, 0, 0],
                    "metadata": {"fmc_ata": {"speed": 1, "productivity": 1, "status": "idle"}}}
                   for i, ability in enumerate(abilities)],
        "tasks": [],
    }
    for i in range(task_count):
        x = (i // 2 + 1) * 1000
        if i % 2:
            x = 1000000 - x
        document["tasks"].append({
            "task_id": f"t{i}", "description": f"Independent native mission {i}",
            "required_capabilities": [abilities[i % 2]],
            "metadata": {"fmc_ata": {"position": [x, 0, 0], "workload": 17 + 6 * i,
                                      "importance": 1000 - 100 * i, "max_players": 1}},
        })
    return document


def assert_unified_schedule(provenance, assignments, task_count):
    """Validate mapping/order/whole workload from raw schedules, not a preferred plan."""
    assert provenance["paper_algorithm"] == "fmc_ata"
    assert provenance["source_integrity"]["commit"] == "7872a94776cfe21cf58cadf44007382deb650eea"
    assert provenance["native_adapter_call"] == 1
    assert provenance["decision_is_physical_evidence"] is False
    assert provenance["simulation_evidence"] == "not_run"
    raw = provenance["native_output"]
    assert raw["solver_class"] == "solver_fmc_distributed_asy.FMC_ATA"
    assert raw["messages"]["sent"] > 0 and raw["native_threads_joined"]
    inputs = provenance["native_input"]
    tasks = {x["id"]: x for x in inputs["tasks"]}
    players = {x["id"]: x for x in inputs["players"]}
    expected_order = []
    for player in raw["players"]:
        for entry in player["schedule"]:
            native_task = tasks[entry["task_id"]]
            native_player = players[player["id"]]
            assert entry["duration"] == pytest.approx(native_task["workload"] / native_player["productivity"])
            assert entry["normalized_allocation"] == 1
            expected_order.append((native_task["task_id"], native_player["robot_id"]))
    assert [(a["task_id"], a["robot_id"]) for a in assignments] == expected_order
    assert len(expected_order) == task_count
    assert len({x[0] for x in expected_order}) == task_count
    if task_count > len(players):
        assert max(len(p["schedule"]) for p in raw["players"]) > 1


def invoke_unified_cli(task_path, config_path, source_root, report_path, python_executable):
    import subprocess

    workspace = ADAPTER_PATH.parents[4]
    command = [str(python_executable), "-B", "-m", "tools.algorithm_adapters.run",
               "--algorithm", "fmc_ata", "--task", str(task_path), "--config", str(config_path),
               "--source-root", str(source_root), "--report", str(report_path)]
    env = {**os.environ, "PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True, timeout=45)
    invocation = {"argv": command, "cwd": str(workspace), "returncode": completed.returncode,
                  "stdout": completed.stdout, "stderr": completed.stderr}
    return invocation, json.loads(Path(report_path).read_text())


def test_unified_factory_real_multi_task(native_adapter, tmp_path):
    from benchmark.native_algorithms import build_native_adapter
    from algorithm.system.coordination.fmc_ata.native_adapter import NativeFMCATAError as ProviderError

    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(unified_task_document(4)))
    task = EaiTaskPackage.from_json(task_path)
    adapter = build_native_adapter("fmc_ata", source_root=native_adapter.source_root,
                                   python_executable=native_adapter.python_executable, timeout_seconds=5)
    before = source_snapshot(native_adapter.source_root)
    try:
        result = TaskRunner().run(TaskRunRequest(task), adapter)
    except ProviderError as exc:
        pytest.xfail(f"UNIFIED_NATIVE_REJECTED (not native pass): {exc}")
    finally:
        assert source_snapshot(native_adapter.source_root) == before
    assert_unified_schedule(result.decision.provenance, result.to_dict()["assignments"], 4)
    assert result.verdict["status"] == "N/A"


@pytest.mark.parametrize("case", ["multi_task", "nclo_failure", "cooperative_scope_failure"])
def test_unified_cli_real_execution(native_adapter, tmp_path, case):
    doc = unified_task_document(4)
    config = {"python_executable": native_adapter.python_executable, "timeout_seconds": 5, "max_nclo": 10000}
    if case == "nclo_failure":
        config["max_nclo"] = 1
    if case == "cooperative_scope_failure":
        doc["tasks"][0]["metadata"]["fmc_ata"]["max_players"] = 2
    task_path, config_path, report_path = (tmp_path / x for x in ("task.json", "config.json", "report.json"))
    task_path.write_text(json.dumps(doc))
    config_path.write_text(json.dumps(config))
    before = source_snapshot(native_adapter.source_root)
    invocation, report = invoke_unified_cli(task_path, config_path, native_adapter.source_root,
                                            report_path, native_adapter.python_executable)
    assert source_snapshot(native_adapter.source_root) == before
    assert report["physical_success"] is None
    assert report["simulation_acceptance_passed"] is False
    if case != "multi_task":
        assert invocation["returncode"] == 1 and report["status"] == "not_passed"
        assert report["error_type"] == ("NativeFMCATAError" if case == "nclo_failure" else "UnsupportedFMCATADomain")
        assert "result" not in report
        if case == "nclo_failure":
            assert report["rejected_native_result"]["nclo_limit_reached"]
            assert report["rejected_native_result"]["lifecycle"]["worker_reaped"]
        return  # Failure propagation contract passed; native success is NOT asserted.
    if report["status"] == "not_passed" and report["error_type"] in ("NativeFMCATAError", "NativeFMCATAScheduleError"):
        assert invocation["returncode"] == 1
        pytest.xfail(f"UNIFIED_NATIVE_REJECTED (not native pass): {report['error']}")
    assert invocation["returncode"] == 0 and report["status"] == "native_decision_returned"
    assert_unified_schedule(report["provenance"], report["result"]["assignments"], 4)
    assert report["result"]["verdict"]["status"] == "N/A"


def write_unified_evidence(output_directory):
    """Fixed cases through factory AND actual module CLI; keep every outcome."""
    from collections import Counter
    from benchmark.native_algorithms import build_native_adapter, source_spec
    from algorithm.system.coordination.native_runtime import verify_source

    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path(os.environ.get("FMC_ATA_SOURCE_ROOT", MODULE.DEFAULT_SOURCE_ROOT))
    python = str(Path(os.environ.get("FMC_ATA_PYTHON", MODULE.DEFAULT_PYTHON)))
    before = source_snapshot(source)
    receipt_before = verify_source(source, source_spec("fmc_ata")["commit"]).to_dict()
    base_config = {"python_executable": python, "timeout_seconds": 5, "max_nclo": 10000}
    changed = unified_task_document(6)
    changed["robots"][0]["position"], changed["robots"][1]["position"] = (
        changed["robots"][1]["position"], changed["robots"][0]["position"])
    changed["tasks"][0]["metadata"]["fmc_ata"].update(workload=53, importance=321)
    cooperative = unified_task_document(2)
    cooperative["tasks"][0]["metadata"]["fmc_ata"]["max_players"] = 2
    single = unified_task_document(1)
    single["robots"] = single["robots"][:1]
    cases = [
        ("heterogeneous_2_tasks", unified_task_document(2, heterogeneous=True), {}),
        ("multi_4_tasks", unified_task_document(4), {}),
        ("multi_6_tasks", unified_task_document(6), {}),
        ("changed_6_tasks", changed, {}),
        ("nclo_limit_failure", unified_task_document(4), {"max_nclo": 1}),
        ("cooperative_scope_failure", cooperative, {}),
        ("single_task_native_boundary", single, {"timeout_seconds": 2}),
    ]
    records = []

    def write(path, value):
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")

    for name, doc, options in cases:
        directory = output / name
        directory.mkdir(exist_ok=True)
        task_path, config_path = directory / "task.json", directory / "config.json"
        config = {**base_config, **options}
        write(task_path, doc)
        write(config_path, config)
        factory_report = {"entry": "benchmark.native_algorithms.build_native_adapter",
                          "simulation_acceptance_passed": False, "physical_success": None}
        try:
            adapter = build_native_adapter("fmc_ata", source_root=source, **config)
            task = EaiTaskPackage.from_json(task_path)
            result = TaskRunner().run(TaskRunRequest(task), adapter)
            factory_report.update(status="native_decision_returned", result=result.to_dict(),
                                  provenance=dict(result.decision.provenance))
        except Exception as exc:
            factory_report.update(status="not_passed", error_type=type(exc).__name__, error=str(exc),
                                  native_error_evidence=getattr(exc, "evidence", {}))
        write(directory / "factory_report.json", factory_report)
        invocation, cli_report = invoke_unified_cli(task_path, config_path, source,
                                                    directory / "cli_report.json", python)
        write(directory / "cli_invocation.json", invocation)
        for entry, report in (("factory", factory_report), ("cli", cli_report)):
            if report["status"] == "native_decision_returned":
                assert_unified_schedule(report["provenance"], report["result"]["assignments"], len(doc["tasks"]))
                assert report["result"]["verdict"]["status"] == "N/A"
            records.append({"case": name, "entry": entry, "status": report["status"],
                            "error_type": report.get("error_type"),
                            "report": str(directory / f"{entry}_report.json")})
    after = source_snapshot(source)
    receipt_after = verify_source(source, source_spec("fmc_ata")["commit"]).to_dict()
    summary = {
        "schema": "heterocoord.fmc_ata.unified_entry.v1", "cases": len(cases),
        "runs": records, "counts": dict(Counter(f"{r['entry']}:{r['status']}" for r in records)),
        "source_before": receipt_before, "source_after": receipt_after,
        "source_files_unchanged": before == after, "source_receipt_unchanged": receipt_before == receipt_after,
        "adapter_sha256": hashlib.sha256(ADAPTER_PATH.read_bytes()).hexdigest(),
        "source_files_sha256": before,
        "entry_source_sha256": {str(p.relative_to(ADAPTER_PATH.parents[4])): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (ADAPTER_PATH.parents[4] / "benchmark/native_algorithms.py",
                                          ADAPTER_PATH.parents[4] / "tools/algorithm_adapters/run.py")},
        "retry_policy": "none; factory_and_cli_are_separate_native_runs_not_equivalent_replays",
        "cli_error_evidence": "NativeFMCATAError.native_result_alias_preserves_same_run_rejected_output",
        "simulation_evidence": "not_run", "physical_success": None,
    }
    assert before == after and receipt_before == receipt_after
    write(output / "summary.json", summary)
    print(json.dumps({"output": str(output), "counts": summary["counts"]}))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--write-evidence")
    modes.add_argument("--write-unified-evidence")
    args = parser.parse_args()
    if args.write_evidence:
        write_native_evidence(args.write_evidence)
    else:
        write_unified_evidence(args.write_unified_evidence)
