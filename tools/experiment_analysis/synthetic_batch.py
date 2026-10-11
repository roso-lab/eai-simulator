"""Pre-execution registration and two real collector runs with scripted outcomes.

This is a transport/selection control, not a task evaluator or physics scene.
All generated plans, ledgers and reports belong in ignored tmp storage.
"""

from dataclasses import replace
from pathlib import Path

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore, DummyAdapter
from tools.physics_replay.execution.scheduler import ReplayCapacity, ReplayScheduler, ResourceReservation

from . import ANALYSIS_VERSION
from .batch import infrastructure_reason, read_sources
from .bundle import export_batch, output_directory, verify_bundle, write_json
from .collector import collector_handoff
from .registration import execution_identity, freeze
from .schema import digest, read_json


class SyntheticBackend:
    """A CPU scripted session; explicit synthetic labels survive all exports."""

    def open_scene(self, scene_id, *, scheduler):
        return {"scene_id": scene_id}

    def open_branch(self, session, job, *, lease):
        return {"branch_id": job.branch_id}

    def run_branch(self, runtime, job, *, emit):
        emit("steps", {"simulator_step": 0, "sim_time_s": 0., "active": False,
                       "completed": False, "terminated": True})
        first = job.run_id == "synthetic-batch-a"
        if first and job.base_instance_id == "c0/u0" and job.repeat_id == "r0":
            return {"status": "infrastructure_invalid", "completed": False,
                    "infrastructure_reason": "synthetic_transport_failure", "physical_success": None,
                    "outcome": {"synthetic": True}}
        if first and job.base_instance_id == "c0/u1" and job.repeat_id == "r0":
            return {"status": "method_timeout", "completed": False, "physical_success": None,
                    "outcome": {"synthetic": True, "value": 0}}
        return {"status": "valid_terminal", "completed": True, "physical_success": None,
                "outcome": {"synthetic": True, "value": 1 if job.base_instance_id.startswith("c0/") else 0}}

    def close_branch(self, runtime):
        pass

    def close_scene(self, session):
        pass


def prepare() -> tuple[dict, dict, list[BranchPlan]]:
    """All design, retry context and run plans are defined before any execution."""
    cells = [dict(id=f"c{c}", domain="synthetic", primary_label="P2", difficulty="fixed",
                  min_instances=2, min_coverage=1) for c in range(2)]
    units = [dict(id=f"c{c}/u{u}", cell_id=f"c{c}", members={"main": f"c{c}/u{u}"},
                  repeats=["r0", "r1", "r2"], supports=["support"], infrastructure_status="valid")
             for c in range(2) for u in range(2)]
    design = dict(schema=ANALYSIS_VERSION + ".design", id="synthetic-batch-control", authority="synthetic_fixture",
                  protocol_digest=digest("synthetic-batch-v1"), systems=["system"], cells=cells, units=units,
                  measures=[dict(id="outcome", kind="scalar", unit="unit", bounds=[0, 1])],
                  estimands=[dict(id="endpoint", kind="mean", cells=["c0", "c1"], support_id="support",
                                  terms=[dict(member="main", condition_id="z0", branch_role="fact", checkpoint_round="e0", measure="outcome")],
                                  min_repeats=3, min_units=2, min_coverage=1)], bootstrap=dict(seed=773, resamples=80))
    observations, jobs = {}, {"synthetic-batch-a": [], "synthetic-batch-b": []}
    for unit in units:
        for repeat in unit["repeats"]:
            base = unit["id"]
            ident = dict(unit_id=base, base_instance_id=base, member="main", system_id="system", condition_id="z0",
                         branch_role="fact", checkpoint_round="e0", repeat_id=repeat)
            runs = ["synthetic-batch-a"] if base.startswith("c0/") else ["synthetic-batch-b"]
            if base.startswith("c0/") and repeat == "r0":
                runs.append("synthetic-batch-b")
            entry = {"identity": ident, "attempts": []}
            for run in runs:
                plan = BranchPlan(run_id=run, scene_key="synthetic", task_id="transport_control", system_id="system", condition_id="z0")
                seed_job = plan.jobs()[0]
                job = replace(seed_job, branch_id=f"{run}/{base}/{repeat}/fact#attempt-0", pair_group_id=f"{base}/{repeat}",
                              base_instance_id=base, repeat_id=repeat, source_revision="synthetic-control-v1",
                              checkpoint_digest=digest("synthetic-checkpoint"), restore_checkpoint_digest=digest("synthetic-checkpoint"),
                              task_contract_digest=digest("synthetic-contract"), scene_build_digest=digest("synthetic-scene"),
                              asset_resolution_digest=digest("synthetic-no-assets"), scene_build_order_digest=digest("synthetic-order"),
                              skill_adapter_version="synthetic-v1", rng_namespace=f"rng/{base}/{repeat}",
                              communication_rng_namespace=f"comm/{base}/{repeat}", metadata={"checkpoint_round": "e0"})
                jobs[run].append(job)
                entry["execution_identity"] = execution_identity(job.identity())
                entry["attempts"].append(dict(run_id=run, branch_id=job.branch_id, attempt=0))
            observations[(base, repeat)] = entry
    plans = [BranchPlan(run_id=run, scene_key="synthetic", task_id="transport_control", system_id="system", condition_id="z0",
                        explicit_jobs=tuple(run_jobs)) for run, run_jobs in jobs.items()]
    schedule = dict(schema=ANALYSIS_VERSION + ".collection_schedule", design_digest=digest(design),
                    evaluator=dict(id="scripted_transport_control", version="v1", source_digest=digest("scripted-transport-v1")),
                    execution_scope="protocol_synthetic",
                    retry_policy=dict(mode="infrastructure_retry", infrastructure_reasons=["synthetic_transport_failure"]),
                    batches=[dict(run_id=p.run_id, plan_digest=digest(p.row())) for p in plans],
                    observations=list(observations.values()))
    return design, freeze(design, schedule), plans


def evaluate(design: dict, registration: dict, handoff: dict) -> dict:
    """Only this fixture's scripted values; no general success inference."""
    rows = []
    for attempt in handoff["attempts"]:
        ident, episode = attempt["identity"], attempt["episode"]
        infra = episode["status"] == "infrastructure_invalid"
        method_failure = episode["status"] == "method_timeout"
        outcome = dict(unit_id=ident["base_instance_id"], base_instance_id=ident["base_instance_id"], member="main",
                       system_id=ident["system_id"], condition_id=ident["condition_id"], branch_role=ident["branch_type"],
                       repeat_id=ident["repeat_id"], checkpoint_round="e0", status="infrastructure_invalid" if infra else "valid",
                       method_outcome="not_run" if infra else "timeout" if method_failure else "completed",
                       values={} if infra else {"outcome": episode["outcome"]["value"]},
                       pair_context_digest=digest([ident["base_instance_id"], ident["repeat_id"], "synthetic-context"]))
        if infra:
            outcome["reason"] = infrastructure_reason(episode)
        rows.append(dict(branch_id=ident["branch_id"], identity_digest=attempt["identity_digest"], outcome=outcome))
    return dict(schema=ANALYSIS_VERSION + ".evaluated_outcomes", design_digest=digest(design),
                collector_seal_sha256=handoff["seal_sha256"], evaluator=registration["schedule"]["evaluator"],
                selection_policy_digest=registration["registration_digest"], records=rows)


def collect(path: str | Path) -> tuple[Path, dict, dict, list[dict]]:
    root = output_directory(path)
    design, registration, plans = prepare()
    write_json(root / "design.json", design)
    write_json(root / "registration.json", registration)
    for plan in plans:
        write_json(root / (plan.run_id + "-plan.json"), plan.row())
    # Persisted registration and both plans now exist; only now start collectors.
    manifest = {"schema": ANALYSIS_VERSION + ".batch_sources", "sources": []}
    for plan in plans:
        store = BranchRunStore.create(root / plan.run_id, plan, requested_max_workers=2,
                                      execution_scope="protocol_synthetic", adapter="dummy")
        scheduler = ReplayScheduler(ReplayCapacity(max_parallel_scenes=1, max_parallel_branches=2, max_branches_per_scene=2),
                                    device_probe=lambda session, request: {"actual_physics_device": "cpu", "controller_device": "cpu", "gpu_uuid": ""})
        BranchExecutor(plan, DummyAdapter(steps=1), store, max_workers=2, session_backend=SyntheticBackend(), scheduler=scheduler,
                       requested_device="cpu", scene_reservation=ResourceReservation(0, 0), branch_reservation=ResourceReservation(0, 0)).run()
        handoff = collector_handoff(store.root)
        outcomes = evaluate(design, registration, handoff)
        name = plan.run_id + "-outcomes.json"
        write_json(root / name, outcomes)
        manifest["sources"].append({"run_dir": plan.run_id, "outcomes": name})
    write_json(root / "source_manifest.json", manifest)
    sources = read_sources(manifest, base_dir=root)
    return root, design, registration, sources


def run(path: str | Path) -> dict:
    root, design, registration, sources = collect(path)
    bundle = export_batch(root / "result", design, registration, sources)
    verification = verify_bundle(bundle, recompute=True)
    report = read_json(bundle / "report.json")
    expected = 5 / 12
    if abs(report["results"][0]["estimate"] - expected) > 1e-12:
        raise ValueError("synthetic batch analytic control failed")
    result = {"output": str(root), "batches": 2, "attempts": sum(len(s["handoff"]["attempts"]) for s in sources),
              "selected_observations": report["records"], "expected": expected,
              "actual": report["results"][0]["estimate"], "verification": verification, "publishable": False}
    write_json(root / "verification.json", result)
    return result
