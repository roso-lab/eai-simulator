"""Pre-registered R4 minimal slice on the fused Warehouse checkpoint engine.

The driver adapts ``demo.warehouse_benchmark.checkpoint_branches`` to the R4
minimal-slice protocol (``demo/benchmark/RELEASE_ROADMAP.zh-CN.md``) without
modifying the engine.  In one Isaac process with one physical env row it runs:

    settle -> persisted snapshot S (CheckpointStore write + verified re-read)
           -> Arm R  (reference): change window -> REAL restore -> readback
           -> Arm S1 (sham):       change window -> no restore  -> readback
           -> infrastructure restore back to S (recorded, not a treatment)
           -> Arm S2 (sham):       change window -> no restore  -> readback

Batch slots encode these sequential treatment arms, not parallel physical
capacity: the batch is opened with ``requested=3`` and ``formal=False`` and the
manifest metadata says so explicitly.

Pre-registered criteria (frozen constants evaluated by :func:`evaluate_criteria`;
the thresholds are also written into the run report before any arm runs):

- ``CR-RESTORE``  Arm R post-restore public-physics readback max-abs error is
  at most ``restore_tolerance`` (engine default ``1e-5``).
- ``CR-SHAM``     Every sham arm readback max-abs error is at least
  ``sham_min_drift`` and at least ``sham_ratio`` times Arm R's restore error.
- ``CR-CHANGE``   Every arm's change window displaces the commanded robot by at
  least ``min_displacement_m`` (the change window is a real change).
- ``CR-RNG``      Arm R post-restore python/numpy/torch/cuda RNG streams are
  bitwise-equal to the snapshot.
- ``CR-BUFFERS``  Arm R post-restore numeric environment buffers differ from
  the snapshot by at most ``restore_tolerance``.

Restore support scope (the engine's public checkpoint): articulation root
transforms/velocities and dof positions/velocities, rigid-object
transforms/velocities, controller dictionaries, actuator state, replay
resources, environment buffers including step counters and command
dictionaries, and python/numpy/torch/cuda RNG state.

Recorded unsupported domains (archived limitations, by design or slice scope):
private PhysX solver/contact/warm-start caches (design exclusion), render and
camera state (dormant camera disabled; the checkpoint only carries a NullCamera
generation counter), participant and communication queues (no participants are
configured; network rows record zeros with an explicit scope), wall-clock time,
ROS bridges (disabled in this slice), and RNG consumption during the change
window (deterministic controllers consume none in this slice, so RNG drift is
not exercised by the change window).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import resource
import sys
import time
import traceback

from demo.benchmark.branch_data import (
    BranchIdentity,
    digest_value,
    validate_run,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = ROOT / "demo/warehouse_benchmark/scenarios/business_logistics_01.json"
R4_SCENE_KEY = "warehouse_nvd"
R4_TASK_ID = "W-DAG-01"
R4_CONDITION_ID = "r4_restore_vs_sham"
R4_SNAPSHOT_SCHEMA = "warehouse.public_physics_task_rng.v1"
RESTORE_ERROR_SOURCE = "max_abs_public_physics_tensor_error"

UNSUPPORTED_DOMAINS = (
    "private PhysX solver/contact/warm-start caches (design exclusion of the "
    "public checkpoint)",
    "render and camera state (dormant camera disabled; checkpoint carries only "
    "a NullCamera generation counter)",
    "participant and communication queues (no participants configured; network "
    "rows record zeros with an explicit scope)",
    "wall-clock time (not part of the checkpoint; report carries measured wall "
    "durations separately)",
    "ROS bridges (disabled in this slice: enable_ros_bridge_extension=False)",
    "RNG consumption during the change window (deterministic controllers "
    "consume none in this slice, so sham arms cannot show RNG drift)",
)


@dataclass(frozen=True)
class ArmSpec:
    slot: int
    arm_id: str
    branch_type: str
    restore_treatment: bool
    label: str


def build_arm_plan() -> tuple[ArmSpec, ...]:
    """Three sequential treatment arms sharing one snapshot digest."""

    return (
        ArmSpec(0, "R", "reference", True,
                "change window + real restore + immediate readback"),
        ArmSpec(1, "S1", "sham", False,
                "change window + no restore + immediate readback"),
        ArmSpec(2, "S2", "sham", False,
                "change window + no restore + immediate readback, after an "
                "infrastructure restore back to the snapshot"),
    )


def preregistered_thresholds(
    *,
    restore_tolerance: float = 1e-5,
    sham_min_drift: float = 1e-3,
    sham_ratio: float = 100.0,
    min_displacement_m: float = 0.01,
) -> dict[str, float]:
    values = {
        "restore_tolerance": float(restore_tolerance),
        "sham_min_drift": float(sham_min_drift),
        "sham_ratio": float(sham_ratio),
        "min_displacement_m": float(min_displacement_m),
    }
    for name, value in values.items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    if values["sham_min_drift"] <= values["restore_tolerance"]:
        raise ValueError("sham_min_drift must exceed restore_tolerance")
    return values


def evaluate_criteria(
    arm_results: dict[str, dict],
    thresholds: dict[str, float],
) -> dict:
    """Judge a finished slice against the frozen pre-registered criteria."""

    plan = {arm.arm_id: arm for arm in build_arm_plan()}
    missing = sorted(set(plan) - set(arm_results))
    if missing:
        raise ValueError(f"arm results missing: {missing}")
    real = arm_results["R"]
    criteria: dict[str, dict] = {}
    restore_error = float(real["restore_error"])
    criteria["CR-RESTORE"] = {
        "observed": restore_error,
        "threshold": thresholds["restore_tolerance"],
        "passed": bool(restore_error <= thresholds["restore_tolerance"]),
        "definition": "arm R post-restore public-physics readback max-abs error",
    }
    sham_checks = []
    for arm_id in ("S1", "S2"):
        drift = float(arm_results[arm_id]["readback_max_abs_error"])
        passed = bool(
            drift >= thresholds["sham_min_drift"]
            and drift >= thresholds["sham_ratio"] * restore_error
        )
        sham_checks.append({
            "arm": arm_id,
            "observed": drift,
            "min_drift": thresholds["sham_min_drift"],
            "ratio_to_restore_error": (
                drift / restore_error if restore_error > 0 else None),
            "required_ratio": thresholds["sham_ratio"],
            "passed": passed,
        })
    criteria["CR-SHAM"] = {
        "arms": sham_checks,
        "passed": bool(all(item["passed"] for item in sham_checks)),
        "definition": "every sham readback >= sham_min_drift and >= sham_ratio x arm R error",
    }
    displacements = {
        arm_id: float(arm_results[arm_id]["displacement_m"])
        for arm_id in plan
    }
    criteria["CR-CHANGE"] = {
        "observed": displacements,
        "threshold": thresholds["min_displacement_m"],
        "passed": bool(all(
            value >= thresholds["min_displacement_m"]
            for value in displacements.values())),
        "definition": "every arm's change window displaces the commanded robot",
    }
    rng_streams = {
        name: bool(value) for name, value in real.get("rng_streams_equal", {}).items()
    }
    criteria["CR-RNG"] = {
        "observed": rng_streams,
        "passed": bool(rng_streams) and all(rng_streams.values()),
        "definition": "post-restore RNG streams bitwise-equal the snapshot",
    }
    buffers_error = float(real.get("buffers_max_abs_error", math.inf))
    criteria["CR-BUFFERS"] = {
        "observed": buffers_error,
        "threshold": thresholds["restore_tolerance"],
        "passed": bool(buffers_error <= thresholds["restore_tolerance"]),
        "definition": "post-restore numeric environment buffers max-abs error",
    }
    return {
        "criteria": criteria,
        "passed": bool(all(item["passed"] for item in criteria.values())),
    }


def r4_base_instance_id(scenario: dict) -> str:
    return f"{R4_TASK_ID}:{digest_value(dict(scenario))[:16]}"


def r4_pair_ids(scenario: dict, base_instance_id: str | None = None) -> tuple[int, str]:
    pair_seed = int(scenario.get("seed", 0))
    base = base_instance_id or r4_base_instance_id(scenario)
    return pair_seed, f"{base}:{R4_CONDITION_ID}:s{pair_seed}"


def r4_branch_identity(
    spec: ArmSpec,
    scenario: dict,
    *,
    checkpoint_digest: str,
    source_revision_value: str,
    execution_scope: str = "measured",
) -> BranchIdentity:
    pair_seed, pair_group_id = r4_pair_ids(scenario)
    return BranchIdentity(
        scene_key=R4_SCENE_KEY,
        task_id=R4_TASK_ID,
        base_instance_id=r4_base_instance_id(scenario),
        condition_id=R4_CONDITION_ID,
        pair_seed=pair_seed,
        pair_group_id=pair_group_id,
        branch_id=f"{pair_group_id}:{spec.branch_type}:arm-{spec.arm_id.lower()}",
        branch_type=spec.branch_type,
        slot=spec.slot,
        checkpoint_digest=checkpoint_digest,
        task_version=f"warehouse.task.v{int(scenario.get('version', 0))}",
        source_revision=source_revision_value,
        execution_scope=execution_scope,
    )


def record_snapshot_row(batch, branch_id: str, *, simulator_step: int,
                        snapshot_digest: str, payload: dict | None = None) -> dict:
    """Ledger row for the shared base snapshot (no restore attempted yet)."""

    return batch.record_checkpoint(
        branch_id,
        checkpoint_id="r4_base_snapshot",
        simulator_step=simulator_step,
        snapshot_digest=snapshot_digest,
        snapshot_schema=R4_SNAPSHOT_SCHEMA,
        restore_error=None,
        restore_passed=None,
        restore_status="pending",
        restore_error_source="base_snapshot_capture_not_restored",
        payload=payload,
    )


def record_real_restore_row(batch, branch_id: str, *, simulator_step: int,
                            snapshot_digest: str, restore_error: float,
                            restore_tolerance: float,
                            payload: dict | None = None) -> dict:
    """Ledger row for the treatment arm's measured restore."""

    return batch.record_checkpoint(
        branch_id,
        checkpoint_id="r4_real_restore",
        simulator_step=simulator_step,
        snapshot_digest=snapshot_digest,
        snapshot_schema=R4_SNAPSHOT_SCHEMA,
        restore_error=float(restore_error),
        restore_passed=bool(restore_error <= restore_tolerance),
        restore_status="measured",
        restore_error_source=RESTORE_ERROR_SOURCE,
        payload=payload,
    )


def record_sham_row(batch, branch_id: str, *, simulator_step: int,
                    snapshot_digest: str, readback_error: float,
                    displacement_m: float,
                    payload: dict | None = None) -> dict:
    """Ledger row for an untreated sham arm.

    The sham arm performs no restore, so it has no restore measurement.  The
    observed untreated readback drift is evidence, not a restore error, and is
    recorded in the payload instead of the ``restore_error`` field.
    """

    return batch.record_checkpoint(
        branch_id,
        checkpoint_id="r4_sham_no_restore",
        simulator_step=simulator_step,
        snapshot_digest=snapshot_digest,
        snapshot_schema=R4_SNAPSHOT_SCHEMA,
        restore_error=None,
        restore_passed=None,
        restore_status="unavailable",
        restore_error_source="sham_arm_performed_no_restore",
        payload={
            "readback_max_abs_error": float(readback_error),
            "change_window_displacement_m": float(displacement_m),
            **(payload or {}),
        },
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=lambda item: item.tolist() if hasattr(item, "tolist") else str(item),
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    from hashlib import sha256

    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rng_state() -> dict:
    import random

    import numpy as np
    import torch

    return {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all(),
    }


def _rng_streams_equal(current: dict, snapshot: dict) -> dict[str, bool]:
    import numpy as np
    import torch

    result: dict[str, bool] = {}
    result["python"] = bool(current["python"] == snapshot["python"])
    numpy_a, numpy_b = current["numpy"], snapshot["numpy"]
    result["numpy"] = bool(
        numpy_a[0] == numpy_b[0]
        and np.array_equal(np.asarray(numpy_a[1]), np.asarray(numpy_b[1]))
        and numpy_a[2] == numpy_b[2]
        and numpy_a[3] == numpy_b[3]
        and numpy_a[4] == numpy_b[4]
    )
    result["torch"] = bool(torch.equal(
        torch.as_tensor(current["torch"]), torch.as_tensor(snapshot["torch"])))
    cuda_a, cuda_b = current["cuda"], snapshot["cuda"]
    result["cuda"] = bool(
        len(cuda_a) == len(cuda_b)
        and all(
            torch.equal(torch.as_tensor(x), torch.as_tensor(y))
            for x, y in zip(cuda_a, cuda_b))
    )
    return result


def _numeric_drift(current, snapshot) -> tuple[float, int, int]:
    """Max-abs drift over nested numeric state; returns (error, compared, skipped)."""

    import numpy as np
    import torch

    if isinstance(current, torch.Tensor) or isinstance(snapshot, torch.Tensor):
        if not (isinstance(current, torch.Tensor) and isinstance(snapshot, torch.Tensor)):
            return 0.0, 0, 1
        if current.shape != snapshot.shape or current.device != snapshot.device:
            return 0.0, 0, 1
        if current.numel() == 0:
            return 0.0, 1, 0
        return float((current.double() - snapshot.double()).abs().max()), 1, 0
    if isinstance(current, np.ndarray) or isinstance(snapshot, np.ndarray):
        if not (isinstance(current, np.ndarray) and isinstance(snapshot, np.ndarray)):
            return 0.0, 0, 1
        if current.shape != snapshot.shape:
            return 0.0, 0, 1
        if current.size == 0:
            return 0.0, 1, 0
        return float(np.abs(current.astype("float64")
                            - snapshot.astype("float64")).max()), 1, 0
    if isinstance(current, dict) and isinstance(snapshot, dict):
        error, compared, skipped = 0.0, 0, 0
        for key in snapshot:
            if key not in current:
                skipped += 1
                continue
            item_error, item_compared, item_skipped = _numeric_drift(
                current[key], snapshot[key])
            error = max(error, item_error)
            compared += item_compared
            skipped += item_skipped
        return error, compared, skipped
    if isinstance(current, (list, tuple)) and isinstance(snapshot, (list, tuple)):
        error, compared, skipped = 0.0, 0, 0
        if len(current) != len(snapshot):
            return 0.0, 0, 1
        for item_current, item_snapshot in zip(current, snapshot):
            item_error, item_compared, item_skipped = _numeric_drift(
                item_current, item_snapshot)
            error = max(error, item_error)
            compared += item_compared
            skipped += item_skipped
        return error, compared, skipped
    if isinstance(current, (int, float, bool)) and isinstance(
            snapshot, (int, float, bool)):
        return abs(float(current) - float(snapshot)), 1, 0
    return 0.0, 0, 1


def run_slice(args: argparse.Namespace) -> int:
    from contextlib import ExitStack
    from functools import partial

    import torch

    from simulator import SimulatorLaunchConfig, open_simulator_session
    from tools.physics_replay.checkpoints import CheckpointStore
    from tools.physics_replay.components import ComponentSnapshot
    from demo.benchmark.branch_data import (
        digest_state,
        open_batch,
        source_revision,
    )
    from demo.warehouse_benchmark.checkpoint_branches import (
        WarehouseCheckpoint,
        _configure_env,
        _scenario_selection,
    )

    scenario_path = args.scenario.resolve()
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    if scenario.get("benchmark_task_id") != R4_TASK_ID:
        raise ValueError("the R4 slice driver accepts only Warehouse W-DAG-01")
    if scenario.get("scene_key") != R4_SCENE_KEY:
        raise ValueError("the R4 slice driver requires scene_key=warehouse_nvd")

    from EAI_assets.scenario_assets import resolve_usd_path

    missing_props = sorted({
        str(entity["asset_key"])
        for entity in scenario["composition"]["entities"]
        if resolve_usd_path(str(entity["asset_key"])) is None
    })
    if missing_props:
        raise FileNotFoundError(
            "Warehouse native scenario props are unavailable before Isaac startup: "
            f"{missing_props}; set EAI_NVD_ASSET_ROOT to the prepared shadow root"
        )

    plan = build_arm_plan()
    thresholds = preregistered_thresholds(
        restore_tolerance=args.restore_tolerance,
        sham_min_drift=args.sham_min_drift,
        sham_ratio=args.sham_ratio,
        min_displacement_m=args.min_displacement,
    )
    selection = _scenario_selection(scenario, include_dormant_camera=False)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    store = CheckpointStore(output)
    started = time.monotonic()
    report: dict = {
        "passed": False,
        "stage": "starting",
        "runner": "demo.benchmark.goal_r4_slice_driver",
        "runner_sha256": _sha256(Path(__file__)),
        "scenario": str(scenario_path),
        "scenario_sha256": _sha256(scenario_path),
        "source_revision": source_revision(ROOT),
        "execution_scope": "measured",
        "preregistered_thresholds": thresholds,
        "preregistered_criteria": {
            "CR-RESTORE": "arm R post-restore public-physics readback <= restore_tolerance",
            "CR-SHAM": "every sham readback >= sham_min_drift and >= sham_ratio x arm R error",
            "CR-CHANGE": "every arm displaces the commanded robot >= min_displacement_m",
            "CR-RNG": "post-restore RNG streams bitwise-equal the snapshot",
            "CR-BUFFERS": "post-restore numeric environment buffers <= restore_tolerance",
        },
        "arm_plan": [
            {
                "slot": arm.slot,
                "arm_id": arm.arm_id,
                "branch_type": arm.branch_type,
                "restore_treatment": arm.restore_treatment,
                "label": arm.label,
            }
            for arm in plan
        ],
        "supported_state_domains": (
            "articulation root transforms/velocities + dof positions/velocities; "
            "rigid-object transforms/velocities; controller dictionaries; actuator "
            "state; replay resources; environment buffers including step counters "
            "and command dictionaries; python/numpy/torch/cuda RNG"
        ),
        "unsupported_domains": list(UNSUPPORTED_DOMAINS),
        "parameters": {
            "settle_steps": args.settle_steps,
            "change_steps": args.change_steps,
            "change_command": args.change_command,
            "physical_env_rows": 1,
            "device": "cuda:0",
        },
        "arms": {},
        "checkpoint_store_root": str(store.root),
    }
    save = lambda: _write_json(output / "r4_report.json", report)
    save()

    batch = open_batch(
        output / "branch_data",
        scene_key=R4_SCENE_KEY,
        task_id=R4_TASK_ID,
        requested=len(plan),
        execution_scope="measured",
        task_version=f"warehouse.task.v{int(scenario.get('version', 0))}",
        source_revision_value=report["source_revision"],
        formal=False,
        metadata={
            "scenario": str(scenario_path),
            "scenario_sha256": report["scenario_sha256"],
            "runner": report["runner"],
            "condition": R4_CONDITION_ID,
            "slot_semantics": (
                "slots encode sequential treatment arms of the pre-registered R4 "
                "restore-vs-sham slice inside ONE Isaac process with ONE physical "
                "env row; they are NOT parallel physical branch capacity"
            ),
            "preregistered_thresholds": thresholds,
            "transport_scope": (
                "benchmark-declared kinematic transport baseline; no grasp, arm IK, "
                "contact-load, or payload-dynamics claim"
            ),
        },
        autosave_every=64,
    )

    clone_stack = ExitStack()
    config = SimulatorLaunchConfig(
        env=selection["task_name"],
        resolved_env_name=selection["task_name"],
        selection_data=selection,
        scenario_file=str(scenario_path),
        num_envs=1,
        device="cuda:0",
        seed=int(scenario.get("seed", 17)),
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        aerial_sensor_backend="disabled",
        env_cfg_hook=partial(
            _configure_env,
            scenario=scenario,
            slots=1,
            static_audit=None,
            clone_mode="copy",
            clone_stack=clone_stack,
            include_dormant_camera=False,
        ),
        app_launcher_args={
            "headless": True,
            "enable_cameras": False,
            "device": "cuda:0",
            "kit_args": "--/app/settings/persistent=false",
        },
    )

    branch_id_by_arm: dict[str, str] = {}
    arm_results: dict[str, dict] = {}
    try:
        with open_simulator_session(config) as session:
            from isaacsim.core.simulation_manager import SimulationManager
            from pxr import PhysxSchema

            env = session.base_env
            origins = env.scene.env_origins
            report["actual_device"] = str(env.sim.device)
            report["simulation_manager_device"] = SimulationManager.get_physics_sim_device()
            scene_api = PhysxSchema.PhysxSceneAPI(
                env.scene.stage.GetPrimAtPath(env.cfg.sim.physics_prim_path))
            report["gpu_dynamics"] = bool(scene_api.GetEnableGPUDynamicsAttr().Get())
            if (report["actual_device"] != "cuda:0"
                    or report["simulation_manager_device"] != "cuda:0"):
                raise RuntimeError("R4 slice requires actual CUDA PhysX")
            expected_agents = {"iris_1", "m20_1", "carter_1"}
            if expected_agents - set(session.possible_agents):
                raise RuntimeError(
                    f"unexpected Warehouse agent set: {session.possible_agents}")
            required_rigid = {
                "scenario_oversized_crate_proxy", "scenario_priority_carton_proxy"}
            if not required_rigid <= set(env.scene.rigid_objects):
                raise RuntimeError(
                    f"scenario props missing: {sorted(required_rigid - set(env.scene.rigid_objects))}")
            report["control_dt_s"] = float(env.step_dt)
            report["physics_dt_s"] = float(env.physics_dt)
            report["articulation_counts"] = {
                name: int(robot.root_physx_view.count)
                for name, robot in env.scene.articulations.items()
            }
            report["rigid_object_counts"] = {
                name: int(body.root_physx_view.count)
                for name, body in env.scene.rigid_objects.items()
            }
            report["stage"] = "settle"
            save()

            checkpoint = WarehouseCheckpoint(env)
            actions = {
                name: torch.zeros((1, 3), device=session.device)
                for name in session.possible_agents
            }
            iris = env.scene.articulations["iris_1"]
            hold_goal = iris.root_physx_view.get_root_transforms().clone()[:, :3]
            control_step = 0

            def advance() -> None:
                nonlocal control_step
                before = int(env._sim_step_counter)
                env.step(actions)
                if int(env._sim_step_counter) - before != env.cfg.decimation:
                    raise RuntimeError("R4 slice barrier advanced unexpected substeps")
                control_step += 1

            def carter_world_xy() -> list[float]:
                root = env.scene.articulations["carter_1"].data.root_pos_w
                return root[0, :2].detach().cpu().tolist()

            def root_rows(name: str) -> dict:
                entity = env.scene.articulations.get(name) or env.scene.rigid_objects[name]
                import torch as _torch

                values = _torch.cat((
                    entity.data.root_pos_w - origins,
                    entity.data.root_quat_w,
                    entity.data.root_lin_vel_w,
                    entity.data.root_ang_vel_w,
                ), dim=1)
                return values[0].detach().cpu().tolist()

            def physics_error_breakdown(expected: dict) -> dict[str, float]:
                actual = checkpoint.physics()
                breakdown: dict[str, float] = {}
                for kind, entities in expected.items():
                    for name, fields in entities.items():
                        for field, want in fields.items():
                            if want.numel():
                                breakdown[f"{kind}/{name}/{field}"] = float(
                                    (actual[kind][name][field] - want).abs().max())
                return breakdown

            # Settle with every agent held (Iris goal fixed at its spawn world pose).
            for _ in range(args.settle_steps):
                actions["iris_1"] = hold_goal.clone()
                advance()
            report["settle_control_steps"] = control_step

            # Pre-change snapshot, persisted through the CheckpointStore and
            # re-read from disk exactly like the engine's save_checkpoint path.
            capture_state = checkpoint.capture({
                "checkpoint": "r4_base_snapshot",
                "control_step": control_step,
                "arms": [arm.arm_id for arm in plan],
            })
            store.write(control_step, {"warehouse": ComponentSnapshot(
                "warehouse", 1, capture_state)})
            persisted = store.read(control_step)["warehouse"].state
            snapshot_values = checkpoint.codec.decode(persisted["values"])
            snapshot_physics = snapshot_values["physics"]
            snapshot_digest = digest_state(snapshot_physics)
            report["snapshot"] = {
                "control_step": control_step,
                "physics_digest": snapshot_digest,
                "checkpoint_store_step": control_step,
                "json": f"checkpoints/{control_step:08d}.json",
                "npz": f"checkpoints/{control_step:08d}.npz",
                "verified_steps": list(store.verify_all()),
            }
            save()

            for arm in plan:
                report["stage"] = f"arm_{arm.arm_id}"
                save()
                arm_started = time.monotonic()
                identity = r4_branch_identity(
                    arm,
                    scenario,
                    checkpoint_digest=snapshot_digest,
                    source_revision_value=batch.manifest["source_revision"],
                )
                batch.register_branch(identity)
                branch_id = identity.branch_id
                branch_id_by_arm[arm.arm_id] = branch_id
                if arm.arm_id == "R":
                    record_snapshot_row(
                        batch,
                        branch_id,
                        simulator_step=control_step,
                        snapshot_digest=snapshot_digest,
                        payload={
                            "checkpoint_store_step": control_step,
                            "shared_by_arms": [item.arm_id for item in plan],
                        },
                    )
                elif arm.arm_id == "S2":
                    # Infrastructure restore so every arm starts from the same
                    # base state; recorded as an event, not a treatment.
                    _, infra_error = checkpoint.restore(persisted)
                    batch.record_event(
                        branch_id,
                        event_type="between_arm_restore",
                        simulator_step=control_step,
                        sim_time_s=control_step * float(env.step_dt),
                        payload={
                            "purpose": "return live state to the shared snapshot "
                                       "before the second sham arm",
                            "restore_error": float(infra_error),
                        },
                    )

                batch.record_event(
                    branch_id,
                    event_type="change_window_started",
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    payload={
                        "steps": args.change_steps,
                        "commanded_agent": "carter_1",
                        "command_forward": args.change_command,
                        "restore_treatment": arm.restore_treatment,
                    },
                )
                start_xy = carter_world_xy()
                for window_step in range(args.change_steps):
                    actions["iris_1"] = hold_goal.clone()
                    actions["m20_1"].zero_()
                    actions["carter_1"].zero_()
                    actions["carter_1"][:, 0] = args.change_command
                    advance()
                    batch.record_step(
                        branch_id,
                        simulator_step=control_step,
                        sim_time_s=control_step * float(env.step_dt),
                        commands={
                            name: value[0].detach().cpu().tolist()
                            for name, value in actions.items()
                        },
                        post_step_state={
                            "carter_1_local_root": root_rows("carter_1"),
                            "m20_1_local_root": root_rows("m20_1"),
                            "iris_1_local_root": root_rows("iris_1"),
                            "scenario_oversized_crate_proxy_local_root": root_rows(
                                "scenario_oversized_crate_proxy"),
                            "scenario_priority_carton_proxy_local_root": root_rows(
                                "scenario_priority_carton_proxy"),
                        },
                        phase_state={
                            "phase": R4_CONDITION_ID,
                            "arm_id": arm.arm_id,
                            "change_window_step": window_step + 1,
                            "change_window_steps": args.change_steps,
                        },
                        task_state={
                            "arm_id": arm.arm_id,
                            "restore_treatment": arm.restore_treatment,
                            "snapshot_control_step": report["snapshot"]["control_step"],
                        },
                        active=True,
                        completed=window_step == args.change_steps - 1,
                        scene_payload={
                            "carter_world_xy": carter_world_xy(),
                        },
                    )
                actions["carter_1"].zero_()
                end_xy = carter_world_xy()
                displacement = float(math.dist(start_xy, end_xy))
                drift_before_treatment = max(
                    physics_error_breakdown(snapshot_physics).values(), default=0.0)
                batch.record_event(
                    branch_id,
                    event_type="change_window_completed",
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    payload={
                        "displacement_m": displacement,
                        "readback_max_abs_error_before_treatment": (
                            drift_before_treatment),
                    },
                )

                arm_result: dict = {
                    "branch_id": branch_id,
                    "branch_type": arm.branch_type,
                    "slot": arm.slot,
                    "displacement_m": displacement,
                    "drift_before_treatment": drift_before_treatment,
                    "change_window_steps": args.change_steps,
                }
                if arm.restore_treatment:
                    restored_task, restore_error = checkpoint.restore(persisted)
                    breakdown = physics_error_breakdown(snapshot_physics)
                    post_error = max(breakdown.values(), default=0.0)
                    rng_streams = _rng_streams_equal(_rng_state(), snapshot_values["rng"])
                    buffers_error, buffers_compared, buffers_skipped = _numeric_drift(
                        {key: getattr(env, key) for key in snapshot_values["buffers"]},
                        snapshot_values["buffers"],
                    )
                    arm_result.update({
                        "restore_error": float(restore_error),
                        "post_restore_readback_max_abs_error": post_error,
                        "post_restore_readback_by_domain": breakdown,
                        "rng_streams_equal": rng_streams,
                        "buffers_max_abs_error": buffers_error,
                        "buffers_compared": buffers_compared,
                        "buffers_skipped": buffers_skipped,
                        "restored_task_checkpoint": restored_task.get("checkpoint"),
                        "restored_control_step": restored_task.get("control_step"),
                    })
                    record_real_restore_row(
                        batch,
                        branch_id,
                        simulator_step=control_step,
                        snapshot_digest=snapshot_digest,
                        restore_error=restore_error,
                        restore_tolerance=thresholds["restore_tolerance"],
                        payload={
                            "post_restore_readback_by_domain": breakdown,
                            "rng_streams_equal": rng_streams,
                            "buffers_max_abs_error": buffers_error,
                            "checkpoint_store_step": report["snapshot"][
                                "checkpoint_store_step"],
                        },
                    )
                    batch.record_event(
                        branch_id,
                        event_type="restore_applied",
                        simulator_step=control_step,
                        sim_time_s=control_step * float(env.step_dt),
                        payload={
                            "restore_error": float(restore_error),
                            "rng_streams_equal": rng_streams,
                            "buffers_max_abs_error": buffers_error,
                        },
                    )
                else:
                    breakdown = physics_error_breakdown(snapshot_physics)
                    sham_error = max(breakdown.values(), default=0.0)
                    buffers_error, buffers_compared, buffers_skipped = _numeric_drift(
                        {key: getattr(env, key) for key in snapshot_values["buffers"]},
                        snapshot_values["buffers"],
                    )
                    rng_unchanged = _rng_streams_equal(
                        _rng_state(), snapshot_values["rng"])
                    arm_result.update({
                        "readback_max_abs_error": sham_error,
                        "readback_by_domain": breakdown,
                        "buffers_max_abs_error": buffers_error,
                        "buffers_compared": buffers_compared,
                        "buffers_skipped": buffers_skipped,
                        "rng_streams_equal_snapshot": rng_unchanged,
                        "restore_applied": False,
                    })
                    record_sham_row(
                        batch,
                        branch_id,
                        simulator_step=control_step,
                        snapshot_digest=snapshot_digest,
                        readback_error=sham_error,
                        displacement_m=displacement,
                        payload={
                            "readback_by_domain": breakdown,
                            "buffers_max_abs_error": buffers_error,
                            "rng_streams_equal_snapshot": rng_unchanged,
                        },
                    )
                    batch.record_event(
                        branch_id,
                        event_type="sham_readback",
                        simulator_step=control_step,
                        sim_time_s=control_step * float(env.step_dt),
                        payload={
                            "readback_max_abs_error": sham_error,
                            "treatment": "none",
                        },
                    )
                batch.record_network(
                    branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    network={
                        "messages_offered": 0,
                        "messages_delivered": 0,
                        "offered_bits": 0,
                        "delivered_bits": 0,
                        "scope": "no participant communication configured",
                        "final": True,
                    },
                )
                arm_result["wall_s"] = time.monotonic() - arm_started
                arm_results[arm.arm_id] = arm_result
                report["arms"][arm.arm_id] = arm_result
                save()

            criteria = evaluate_criteria(arm_results, thresholds)
            report["criteria"] = criteria
            simulator_step = control_step
            sim_time_s = simulator_step * float(env.step_dt)
            for arm in plan:
                result = arm_results[arm.arm_id]
                if arm.restore_treatment:
                    arm_passed = bool(
                        result["restore_error"] <= thresholds["restore_tolerance"])
                else:
                    arm_passed = bool(
                        result["readback_max_abs_error"] >= thresholds["sham_min_drift"])
                batch.record_cost(
                    branch_id_by_arm[arm.arm_id],
                    simulator_step=simulator_step,
                    sim_time_s=sim_time_s,
                    cost={
                        "control_steps": result["change_window_steps"],
                        "arm_wall_s": result["wall_s"],
                        "participant_messages": 0,
                        "system_commands": result["change_window_steps"],
                        "effective_execution": arm_passed,
                    },
                )
                batch.finish_branch(
                    branch_id_by_arm[arm.arm_id],
                    completed=True,
                    independently_verified=arm_passed,
                    simulator_step=simulator_step,
                    sim_time_s=sim_time_s,
                    outcome={
                        "arm_id": arm.arm_id,
                        "branch_type": arm.branch_type,
                        "displacement_m": result["displacement_m"],
                        **(
                            {"restore_error": result["restore_error"]}
                            if arm.restore_treatment else
                            {"readback_max_abs_error": result["readback_max_abs_error"]}
                        ),
                    },
                )
            batch.close_batch(
                status="completed",
                resident=len(plan),
                peak_active=1,
                queued=0,
                details={
                    "slot_semantics": "sequential treatment arms, one physical env row",
                    "criteria_passed": criteria["passed"],
                },
            )
            report["branch_data_validation"] = validate_run(batch.output)
            report["branch_data_output"] = str(batch.output)
            report["criteria_passed"] = criteria["passed"]
            report["passed"] = bool(
                criteria["passed"]
                and report["branch_data_validation"]["status"] == "passed")
            report["stage"] = "finished" if report["passed"] else "criteria_failed"
            report["wall_s"] = time.monotonic() - started
            report["process_peak_ram_gib"] = (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2)
            save()
    except BaseException as error:
        batch.close_batch(
            status="failed",
            resident=len(branch_id_by_arm),
            peak_active=1 if branch_id_by_arm else 0,
            queued=len(plan) - len(branch_id_by_arm),
            details={"error": f"{type(error).__name__}: {error}"},
        )
        report["branch_data_validation"] = validate_run(batch.output)
        report.update(
            passed=False,
            stage="failed",
            error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(),
        )
        report["wall_s"] = time.monotonic() - started
        save()
        raise
    finally:
        clone_stack.close()
    return 0 if report["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settle-steps", type=int, default=40)
    parser.add_argument("--change-steps", type=int, default=20)
    parser.add_argument("--change-command", type=float, default=0.25)
    parser.add_argument("--restore-tolerance", type=float, default=1e-5)
    parser.add_argument("--sham-min-drift", type=float, default=1e-3)
    parser.add_argument("--sham-ratio", type=float, default=100.0)
    parser.add_argument("--min-displacement", type=float, default=0.01)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    positive = (
        args.settle_steps, args.change_steps, args.change_command,
        args.restore_tolerance, args.sham_min_drift, args.sham_ratio,
        args.min_displacement,
    )
    if any(not math.isfinite(float(value)) or float(value) <= 0 for value in positive):
        parser.error("step counts, commands and thresholds must be positive")
    try:
        return run_slice(args)
    except BaseException as error:
        report_path = args.output / "r4_report.json"
        if not report_path.exists():
            args.output.mkdir(parents=True, exist_ok=True)
            _write_json(report_path, {
                "passed": False,
                "stage": "failed",
                "runner": "demo.benchmark.goal_r4_slice_driver",
                "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc(),
            })
        print(
            f"[R4 slice] failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
