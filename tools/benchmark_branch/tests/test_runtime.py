"""Runtime contract fixtures, deliberately independent of production tasks."""
from contextlib import contextmanager
from dataclasses import replace
import json
import threading
import sys
from types import SimpleNamespace

import pytest

from tools.benchmark_branch import BranchPlan, BranchRunStore, BranchExecutor, DummyAdapter
from tools.benchmark_branch.evidence import write_evaluator_evidence, verify_evaluator_evidence
from tools.benchmark_branch.eai_session import EaiSessionBackend, probe_eai_devices, CudaResourceProbe
from tools.benchmark_branch.main import main
from tools.benchmark_branch.runtime import RuntimeComponents, load_runtime
from tools.benchmark_branch.validation import validate_run
from tools.physics_replay.execution.scheduler import ReplayCapacity, ReplayScheduler


VERDICT = dict(status="valid_terminal", completed=True, physical_success=True,
               terminal_predicate=True, safety_predicate=True, feasibility_predicate=True)


class ContractAdapter(DummyAdapter):
    supported_execution_scopes = {"measured", "measured_diagnostic"}


class ContractBackend:
    execution_affinity = "caller_thread"

    def __init__(self, root):
        self.root = root
        self.calls = []

    def open_scene(self, scene_id, *, scheduler):
        assert threading.current_thread() is threading.main_thread()
        self.calls.append("open")
        return SimpleNamespace(actual_physics_device="cpu", controller_device="cpu")

    def warmup(self, session, *, ticks):
        self.calls.append("warmup")

    def open_branch(self, session, job, *, lease):
        return {"job": job}

    def run_branch(self, runtime, job, *, emit):
        assert threading.current_thread() is threading.main_thread()
        emit("checkpoints", {"restore_passed": True, "readback_digest": job.checkpoint_digest})
        emit("metrics", {"kind": "heartbeat"})
        emit("steps", {"simulator_step": 1})
        return write_evaluator_evidence(self.root, job, evaluator_version="contract-fixture.v1",
                                        result=VERDICT, observations={"fixture": True})

    def close_branch(self, runtime):
        self.calls.append("close_branch")

    def close_scene(self, session):
        self.calls.append("close_scene")


def contract_runtime(*, plan, run_dir):
    return RuntimeComponents(ContractAdapter(), ContractBackend(run_dir),
                             ReplayScheduler(ReplayCapacity(1, 1, 1)),
                             "contract-fixture", "1", {"requested_device": "cpu"})


PROVENANCE_FACTORY_CALLS = []


def provenance_runtime(*, plan, run_dir):
    PROVENANCE_FACTORY_CALLS.append((plan, run_dir))
    return contract_runtime(plan=plan, run_dir=run_dir)


def test_cli_runtime_run_resume_and_artifact_revalidation(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"slots": 1, "arms": ["fact"],
                                 "execution_scope": "measured_diagnostic",
                                 "runtime_factory": f"{__name__}:contract_runtime"}))
    root = tmp_path / "run"
    assert main(["run", "--config", str(config), "--output", str(root)]) == 0
    report = validate_run(root)
    assert report["status"] == "passed", report
    assert report["metrics"]["observed_peak_branch_workers"] == 1
    assert report["metrics"]["formal_pairing_eligible"] is False
    assert main(["resume", "--run-dir", str(root)]) == 0
    rows = (root / "episodes.jsonl").read_text().splitlines()
    assert len(rows) == 1
    evidence = next((root / "evidence").iterdir())
    evidence.write_text("{}")
    assert validate_run(root)["status"] == "failed"


def test_runtime_pin_rejects_changed_factory(tmp_path):
    config = {"runtime_factory": f"{__name__}:contract_runtime"}
    plan = BranchPlan()
    _, pin = load_runtime(config, plan=plan, run_dir=tmp_path)
    assert "runtime_dependencies" not in pin
    with pytest.raises(ValueError, match="changed"):
        load_runtime(config, plan=plan, run_dir=tmp_path, expected={**pin, "factory_sha256": "changed"})


def test_runtime_pin_records_only_declared_dependency_groups(tmp_path):
    paths = {}
    for group in ("controllers", "models", "assets", "external_inputs"):
        path = tmp_path / f"{group}.bin"
        path.write_bytes(group.encode())
        paths[group] = path
    config = {
        "runtime_factory": f"{__name__}:provenance_runtime",
        "runtime_dependencies": {group: [str(path)] for group, path in paths.items()},
    }
    PROVENANCE_FACTORY_CALLS.clear()
    _, pin = load_runtime(config, plan=BranchPlan(), run_dir=tmp_path)

    assert len(PROVENANCE_FACTORY_CALLS) == 1
    assert set(pin["runtime_dependencies"]) == set(paths)
    for group, path in paths.items():
        entry = pin["runtime_dependencies"][group][0]
        assert entry["path"] == str(path.resolve())
        assert entry["kind"] == "file"
        assert len(entry["sha256"]) == 64


def test_runtime_resume_rejects_mutated_dependency_before_factory_side_effect(tmp_path):
    dependency = tmp_path / "controller.py"
    dependency.write_text("original")
    config = {
        "runtime_factory": f"{__name__}:provenance_runtime",
        "runtime_dependencies": {"controllers": [str(dependency)]},
    }
    PROVENANCE_FACTORY_CALLS.clear()
    _, pin = load_runtime(config, plan=BranchPlan(), run_dir=tmp_path)
    assert len(PROVENANCE_FACTORY_CALLS) == 1
    dependency.write_text("mutated")

    with pytest.raises(ValueError, match="dependenc.*(digest|changed)"):
        load_runtime(config, plan=BranchPlan(), run_dir=tmp_path, expected=pin)
    assert len(PROVENANCE_FACTORY_CALLS) == 1


def test_runtime_resume_rejects_missing_dependency_before_factory_side_effect(tmp_path):
    dependency = tmp_path / "model.bin"
    dependency.write_bytes(b"model")
    config = {
        "runtime_factory": f"{__name__}:provenance_runtime",
        "runtime_dependencies": {"models": [str(dependency)]},
    }
    PROVENANCE_FACTORY_CALLS.clear()
    _, pin = load_runtime(config, plan=BranchPlan(), run_dir=tmp_path)
    dependency.unlink()

    with pytest.raises(ValueError, match="dependency is missing"):
        load_runtime(config, plan=BranchPlan(), run_dir=tmp_path, expected=pin)
    assert len(PROVENANCE_FACTORY_CALLS) == 1


def test_runtime_resume_rejects_changed_declaration_before_factory_side_effect(tmp_path):
    first = tmp_path / "input-a.json"
    second = tmp_path / "input-b.json"
    first.write_text("same-content")
    second.write_text("same-content")
    config = {
        "runtime_factory": f"{__name__}:provenance_runtime",
        "runtime_dependencies": {"external_inputs": [str(first)]},
    }
    PROVENANCE_FACTORY_CALLS.clear()
    _, pin = load_runtime(config, plan=BranchPlan(), run_dir=tmp_path)
    changed = {
        **config,
        "runtime_dependencies": {"external_inputs": [str(second)]},
    }

    with pytest.raises(ValueError, match="dependencies changed"):
        load_runtime(changed, plan=BranchPlan(), run_dir=tmp_path, expected=pin)
    assert len(PROVENANCE_FACTORY_CALLS) == 1


def test_evidence_rejects_other_branch_retry_and_inconsistent_predicates(tmp_path):
    job = BranchPlan(slots=1).jobs()[0]
    result = write_evaluator_evidence(tmp_path, job, evaluator_version="test.v1",
                                      result=VERDICT, observations=[{"position": [1, 2, 3]}])
    for changed in (job.with_attempt(1), replace(job, checkpoint_digest="another-checkpoint")):
        with pytest.raises(ValueError, match="identity"):
            verify_evaluator_evidence(tmp_path, changed, result)
    with pytest.raises(ValueError, match="verdict mismatch"):
        verify_evaluator_evidence(tmp_path, job, {**result, "physical_success": False})
    with pytest.raises(ValueError, match="contradicts"):
        write_evaluator_evidence(tmp_path, job, evaluator_version="test.v1",
                                  result={**VERDICT, "safety_predicate": False}, observations=[1])
    with pytest.raises(ValueError, match="inside"):
        verify_evaluator_evidence(tmp_path, job, {**result, "evidence_refs": ["../outside.json"]})


def test_formal_measured_requires_authoritative_identity_before_open(tmp_path):
    plan = BranchPlan(execution_scope="measured")
    store = BranchRunStore.create(tmp_path, plan)
    runtime = contract_runtime(plan=plan, run_dir=tmp_path)
    with pytest.raises(ValueError, match="benchmark-owned"):
        BranchExecutor(plan, runtime.adapter, store, session_backend=runtime.session_backend,
                       scheduler=runtime.scheduler).run()
    assert runtime.session_backend.calls == []


def test_runtime_cannot_override_identity_or_overwrite_existing_run(tmp_path):
    plan = BranchPlan()
    store = BranchRunStore.create(tmp_path, plan)
    with pytest.raises(ValueError, match="identity"):
        store.append("steps", {"branch_id": "other"}, job=store.plan.jobs()[0])
    store.finish()
    with pytest.raises(FileExistsError):
        BranchRunStore.create(tmp_path, plan)


def test_session_teardown_failure_cannot_report_clean_completion(tmp_path):
    plan = BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic")
    store = BranchRunStore.create(tmp_path, plan)
    runtime = contract_runtime(plan=plan, run_dir=tmp_path)

    def fail_close(session):
        raise RuntimeError("teardown failed")

    runtime.session_backend.close_scene = fail_close
    result = BranchExecutor(plan, runtime.adapter, store, session_backend=runtime.session_backend,
                            scheduler=runtime.scheduler, requested_device="cpu").run()
    assert result["failed"] == 1
    assert result["completed"] == 0
    assert validate_run(tmp_path)["status"] == "failed"


def test_resume_refuses_corruption_without_resealing(tmp_path):
    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path, plan)
    BranchExecutor(plan, DummyAdapter(steps=1), store, max_workers=1).run()
    before = (tmp_path / "checksums.sha256").read_bytes()
    with (tmp_path / "steps.jsonl").open("a") as stream:
        stream.write('{}\n')
    with pytest.raises(ValueError, match="resume refused.*checksum mismatch"):
        BranchRunStore.resume(tmp_path)
    assert (tmp_path / "checksums.sha256").read_bytes() == before
    assert validate_run(tmp_path)["status"] == "failed"


@pytest.mark.parametrize("removed", ["manifest.json", "plan.json", "seal"])
def test_resume_requires_complete_seal(tmp_path, removed):
    store = BranchRunStore.create(tmp_path, BranchPlan())
    store.finish()
    seal = tmp_path / "checksums.sha256"
    if removed == "seal":
        seal.unlink()
    else:
        seal.write_text("\n".join(line for line in seal.read_text().splitlines()
                                   if line.split()[1] != removed) + "\n")
    with pytest.raises(ValueError, match="resume refused"):
        BranchRunStore.resume(tmp_path)
    assert validate_run(tmp_path)["status"] == "failed"


def test_validator_rechecks_evidence_from_superseded_attempt(tmp_path):
    plan = BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic")
    store = BranchRunStore.create(tmp_path, plan)
    job = store.plan.jobs()[0]
    refs = []
    for attempt in (0, 1):
        current = job.with_attempt(attempt)
        store.append("branch_manifest", current.identity(), job=current)
        result = write_evaluator_evidence(tmp_path, current, evaluator_version="fixture.v1",
                                          result={**VERDICT, "physical_success": bool(attempt)},
                                          observations={"attempt": attempt})
        refs.append(result["evidence_refs"][0])
        store.append("episodes", {**result, "worker_instance_id": f"worker-{attempt}",
                                   "worker_session_id": f"session-{attempt}"}, job=current)
    store.finish()
    (tmp_path / refs[0]).write_text('{}')
    # Even a newly generated general file seal cannot repair the evaluator's
    # original digest/identity. The historical attempt must still be checked.
    store.finish()
    report = validate_run(tmp_path)
    assert any("attempt-0" in error and "hash mismatch" in error for error in report["errors"])


def test_eai_session_uses_context_and_evaluator_not_driver_success(tmp_path):
    events = []

    @contextmanager
    def opener(config):
        events.append("enter")
        yield SimpleNamespace()
        events.append("exit")

    class Driver:
        def warmup(self, session, *, ticks):
            events.append("warmup")
        def open_branch(self, session, job, *, lease):
            return {"session": session}
        def run_branch(self, runtime, job, *, emit):
            return {"physical_success": True}
        def close_branch(self, runtime):
            events.append("close_branch")

    class Evaluator:
        def evaluate(self, runtime, job, *, execution, run_dir):
            return {"status": "not_evaluable", "physical_success": None}

    backend = EaiSessionBackend(scene_id="fixture", launch_config=None, branch_driver=Driver(),
                                evaluator=Evaluator(), run_dir=tmp_path, session_opener=opener)
    session = backend.open_scene("fixture", scheduler=None)
    backend.warmup(session, ticks=1)
    runtime = backend.open_branch(session, None, lease=None)
    assert backend.run_branch(runtime, None, emit=None)["physical_success"] is None
    backend.close_branch(runtime)
    backend.close_scene(session)
    backend.close_scene(session)
    assert events == ["enter", "warmup", "close_branch", "exit"]


def test_device_probe_reads_runtime_context_not_requested_device(monkeypatch):
    class Tensor:
        device = "cuda:1"

    fake_torch = SimpleNamespace(
        Tensor=Tensor, device=lambda value: value,
        cuda=SimpleNamespace(get_device_properties=lambda device: SimpleNamespace(uuid=f"uuid-{device}"),
                             mem_get_info=lambda device: (1024**3, 2 * 1024**3)))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    physics = SimpleNamespace(device="cuda:1", is_gpu_dynamics_enabled=lambda: True, use_gpu_pipeline=True)
    session = SimpleNamespace(base_env=SimpleNamespace(sim=SimpleNamespace(
        device="cuda:0", get_physics_context=lambda: physics)), benchmark_action_tensor=Tensor())
    report = probe_eai_devices(session, None)
    assert report["actual_physics_device"] == "cuda:1"
    assert report["gpu_uuid"] == "uuid-cuda:1"
    sample = CudaResourceProbe("cuda:1").sample()
    assert sample["gpu_uuid"] == "uuid-cuda:1"
    assert sample["gpu_free_memory_mb"] == 1024
    physics.use_gpu_pipeline = False
    assert probe_eai_devices(session, None)["actual_physics_device"] == "cpu"
