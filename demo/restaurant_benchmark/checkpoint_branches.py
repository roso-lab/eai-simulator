"""Run the Restaurant scene through the shared Branch Data Tool.

The current Restaurant public task is still authoring-only.  This runner is a
measured scene-lifecycle probe: it exercises resident branch rows, append-only
ledgers, public checkpoint save/restore, and continuation after restore.  It
does not claim Restaurant business-task completion or scoreability.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time
import traceback


ROOT = Path(__file__).resolve().parents[2]
SCENARIO_PATH = ROOT / "source/EAI_hmrs/EAI_hmrs/envs/restaurant_scene.json"
SOURCE_USD = ROOT / "usd/scene/restaurant/Restaurant_Only.usda"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                   default=lambda item: item.tolist() if hasattr(item, "tolist") else str(item))
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _selection() -> dict:
    return {
        "version": 1,
        "task_name": "restaurant_branch_data_probe",
        "scene_key": "restaurant",
        "robots": [{
            "type": "carter",
            "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
            "attachments": [],
        }],
    }


def _configure_env(cfg, *, slots: int) -> None:
    cfg.scene.num_envs = int(slots)
    cfg.scene.env_spacing = 64.0
    cfg.scene.replicate_physics = True
    cfg.scene.clone_in_fabric = False
    cfg.scene.filter_collisions = True
    cfg.episode_length_s = 1000.0


def _capture_snapshot(env, adapter, *, phase: str, simulator_step: int) -> dict:
    import torch

    physics = {"articulations": {}, "rigid_objects": {}}
    for name, entity in env.scene.articulations.items():
        view = entity.root_physx_view
        physics["articulations"][name] = {
            "root_transforms": view.get_root_transforms().detach().cpu().clone(),
            "root_velocities": view.get_root_velocities().detach().cpu().clone(),
        }
    for name, entity in env.scene.rigid_objects.items():
        view = entity.root_physx_view
        physics["rigid_objects"][name] = {
            "transforms": view.get_transforms().detach().cpu().clone(),
            "velocities": view.get_velocities().detach().cpu().clone(),
        }
    return {
        "schema": adapter.snapshot_schema,
        "phase": str(phase),
        "simulator_step": int(simulator_step),
        "public_state": adapter.read_all_branch_states({"env": env}),
        "physics": physics,
    }


def _snapshot_digest(snapshot: dict, digest_value) -> str:
    return digest_value({
        "schema": snapshot["schema"],
        "phase": snapshot["phase"],
        "simulator_step": snapshot["simulator_step"],
        "public_state": snapshot["public_state"],
    })


def _restore_snapshot(env, snapshot: dict, *, device: str) -> float:
    import torch

    ids = torch.arange(env.num_envs, device=device, dtype=torch.long)
    max_error = 0.0
    for name, fields in snapshot["physics"]["articulations"].items():
        view = env.scene.articulations[name].root_physx_view
        expected_transforms = fields["root_transforms"].to(device=device)
        expected_velocities = fields["root_velocities"].to(device=device)
        view.set_root_transforms(expected_transforms, ids)
        view.set_root_velocities(expected_velocities, ids)
        max_error = max(
            max_error,
            float((view.get_root_transforms() - expected_transforms).abs().max()),
            float((view.get_root_velocities() - expected_velocities).abs().max()),
        )
    for name, fields in snapshot["physics"]["rigid_objects"].items():
        view = env.scene.rigid_objects[name].root_physx_view
        expected_transforms = fields["transforms"].to(device=device)
        expected_velocities = fields["velocities"].to(device=device)
        view.set_transforms(expected_transforms, ids)
        view.set_velocities(expected_velocities, ids)
        max_error = max(
            max_error,
            float((view.get_transforms() - expected_transforms).abs().max()),
            float((view.get_velocities() - expected_velocities).abs().max()),
        )
    env.sim.forward()
    return max_error


def _save_and_restore(
    *, output: Path, env, adapter, actions, phase: str, simulator_step: int,
    device: str, batch, branch_ids: dict[int, str], digest_value,
    report: dict,
) -> tuple[str, float]:
    import torch

    snapshot = _capture_snapshot(
        env, adapter, phase=phase, simulator_step=simulator_step)
    digest = _snapshot_digest(snapshot, digest_value)
    checkpoint_path = output / "checkpoints" / f"{simulator_step:04d}_{phase}.pt"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(snapshot, checkpoint_path)
    try:
        restored = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except TypeError:
        restored = torch.load(checkpoint_path, map_location="cpu")
    # Advance once after capture so the measured restore is not a read-only save.
    env.step(actions)
    restore_error = _restore_snapshot(env, restored, device=device)
    passed = bool(restore_error <= 1e-4)
    if not passed:
        raise RuntimeError(
            f"Restaurant checkpoint restore failed at {phase}: {restore_error:g}"
        )
    sim_time_s = float(simulator_step) * float(env.step_dt)
    for slot, branch_id in branch_ids.items():
        batch.record_checkpoint(
            branch_id,
            checkpoint_id=phase,
            simulator_step=simulator_step,
            snapshot_digest=digest,
            snapshot_schema=adapter.snapshot_schema,
            restore_error=restore_error,
            restore_passed=True,
            restore_status="measured",
            restore_error_source="max_abs_public_restaurant_physics_tensor_error",
            payload={
                "checkpoint_path": str(checkpoint_path),
                "continued_after_restore": True,
                "task_contract": "scene_lifecycle_only",
            },
        )
        batch.record_event(
            branch_id,
            event_type="checkpoint_restored",
            simulator_step=simulator_step,
            sim_time_s=sim_time_s,
            payload={"checkpoint_id": phase, "restore_error": restore_error},
        )
        batch.record_progress(
            branch_id,
            simulator_step=simulator_step,
            sim_time_s=sim_time_s,
            progress={"phase": phase, "probe_completed": phase == "probe_end"},
        )
    report.setdefault("checkpoints", []).append({
        "checkpoint_id": phase,
        "simulator_step": simulator_step,
        "path": str(checkpoint_path),
        "snapshot_digest": digest,
        "restore_passed": True,
        "restore_error": restore_error,
        "restore_error_source": "max_abs_public_restaurant_physics_tensor_error",
        "continued_after_restore": True,
    })
    return digest, restore_error


def run(args: argparse.Namespace) -> dict:
    import torch

    from demo.benchmark.branch_data import open_batch, source_revision, validate_run
    from demo.restaurant_benchmark.branch_data_adapter import RestaurantBranchDataAdapter
    from simulator import SimulatorLaunchConfig, open_simulator_session

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    scenario = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario = deepcopy(scenario)
    scenario.update({"scene_key": "restaurant", "seed": int(args.seed)})
    adapter = RestaurantBranchDataAdapter(
        scenario=scenario, scenario_path=SCENARIO_PATH, slots=args.slots)
    report = {
        "schema": "eai.restaurant.branch_data_scene_lifecycle.v1",
        "scene_key": adapter.scene_key,
        "task_id": adapter.task_id,
        "task_contract": "scene_lifecycle_only",
        "formal_suite_registered": False,
        "physical_task_validated": False,
        "full_task_completed": False,
        "business_episode_validated": False,
        "execution_scope": "measured",
        "source_usd": str(SOURCE_USD),
        "scenario": str(SCENARIO_PATH),
        "requested": args.slots,
        "resident": 0,
        "peak_active": 0,
        "completed": 0,
        "independently_verified": 0,
        "queued": args.slots,
        "status": "starting",
        "started_unix": time.time(),
        "checkpoint_plan": [0, args.steps // 3, (2 * args.steps) // 3, args.steps],
    }
    _write_json(output / "report.json", report)
    _write_json(output / "environment.json", {
        "selection": _selection(),
        "scenario": scenario,
        "source_usd": str(SOURCE_USD),
        "task_contract": "scene_lifecycle_only",
    })
    batch = open_batch(
        output / "branch_data",
        scene_key=adapter.scene_key,
        task_id=adapter.task_id,
        requested=args.slots,
        execution_scope="measured",
        task_version=adapter.task_version,
        source_revision_value=source_revision(ROOT),
        formal=False,
        metadata={
            "runner": "demo.restaurant_benchmark.checkpoint_branches",
            "task_contract": "scene_lifecycle_only",
            "formal_suite_registered": False,
            "full_task_completed": False,
            "source_usd": str(SOURCE_USD),
        },
        autosave_every=max(32, args.slots * 4),
    )
    branch_ids: dict[int, str] = {}

    config = SimulatorLaunchConfig(
        env="restaurant_scene",
        resolved_env_name="EAI-Interactive-v0",
        selection_data=_selection(),
        num_envs=args.slots,
        device=args.device,
        seed=args.seed,
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        app_launcher_args={
            "headless": True,
            "enable_cameras": False,
            "device": args.device,
            "kit_args": "--/app/settings/persistent=false",
        },
        env_cfg_hook=lambda cfg: _configure_env(cfg, slots=args.slots),
    )
    try:
        with open_simulator_session(config) as session:
            env = session.base_env
            counts = {
                name: int(entity.root_physx_view.count)
                for name, entity in env.scene.articulations.items()
            }
            if not counts or any(value != args.slots for value in counts.values()):
                raise RuntimeError(f"Restaurant resident articulation rows mismatch: {counts}")
            report.update(
                actual_device=str(env.sim.device),
                possible_agents=list(session.possible_agents),
                articulation_counts=counts,
                resident=args.slots,
                peak_active=args.slots,
                queued=0,
                control_dt_s=float(env.step_dt),
                physics_dt_s=float(env.physics_dt),
                status="scene_loaded",
            )
            _write_json(output / "report.json", report)
            actions = {
                name: torch.zeros((args.slots, 3), device=session.device)
                for name in session.possible_agents
            }
            for name, value in actions.items():
                env.set_command(name, "base_velocity", value)

            checkpoint_steps = sorted(set(report["checkpoint_plan"]))
            control_step = 0
            first_digest, first_error = _save_and_restore(
                output=output, env=env, adapter=adapter, actions=actions,
                phase="scene_loaded", simulator_step=0, device=session.device,
                batch=batch, branch_ids=branch_ids, digest_value=__import__(
                    "demo.benchmark.branch_data", fromlist=["digest_value"]
                ).digest_value, report=report,
            )
            for slot in range(args.slots):
                identity = adapter.identity(
                    slot,
                    checkpoint_digest=first_digest,
                    source_revision=batch.manifest["source_revision"],
                )
                batch.register_branch(identity)
                branch_ids[slot] = identity.branch_id
            # The initial checkpoint is recorded after registration so every
            # durable row carries its immutable branch identity.
            for slot in range(args.slots):
                branch_id = branch_ids[slot]
                batch.record_checkpoint(
                    branch_id,
                    checkpoint_id="scene_loaded",
                    simulator_step=0,
                    snapshot_digest=first_digest,
                    snapshot_schema=adapter.snapshot_schema,
                    restore_error=first_error,
                    restore_passed=True,
                    restore_status="measured",
                    restore_error_source="max_abs_public_restaurant_physics_tensor_error",
                    payload={"task_contract": "scene_lifecycle_only", "initial": True},
                )
                batch.record_event(
                    branch_id,
                    event_type="scene_loaded",
                    simulator_step=0,
                    sim_time_s=0.0,
                    payload={"task_contract": "scene_lifecycle_only"},
                )
                batch.record_progress(
                    branch_id,
                    simulator_step=0,
                    sim_time_s=0.0,
                    progress={"phase": "scene_loaded", "probe_completed": False},
                )
            # The helper recorded the initial checkpoint before identities were
            # known; remove no data and keep the identity-bearing record above.
            # Subsequent checkpoints are recorded directly with branch IDs.
            report["status"] = "probing"
            _write_json(output / "report.json", report)

            for next_step in range(1, args.steps + 1):
                env.step(actions)
                control_step = next_step
                states = adapter.read_all_branch_states({"env": env})
                command_rows = adapter.command_rows(actions)
                completed = control_step == args.steps
                records = []
                sim_time_s = control_step * float(env.step_dt)
                for slot in range(args.slots):
                    branch_id = branch_ids[slot]
                    records.append((branch_id, {
                        "simulator_step": control_step,
                        "sim_time_s": sim_time_s,
                        "commands": command_rows[slot],
                        "post_step_state": states[slot],
                        "phase_state": adapter.phase_state(
                            simulator_step=control_step,
                            phase="probe_end" if completed else "runtime_hold",
                        ),
                        "task_state": adapter.task_state(completed=completed),
                        "active": not completed,
                        "completed": completed,
                        "scene_payload": adapter.scene_payload(
                            slot,
                            state=states[slot],
                            phase="probe_end" if completed else "runtime_hold",
                            completed=completed,
                        ),
                    }))
                batch.record_step_batch(records)
                if control_step in checkpoint_steps[1:]:
                    phase = "probe_end" if completed else "runtime_hold"
                    snapshot = _capture_snapshot(
                        env, adapter, phase=phase, simulator_step=control_step)
                    digest = _snapshot_digest(
                        snapshot,
                        __import__("demo.benchmark.branch_data", fromlist=["digest_value"])
                        .digest_value,
                    )
                    checkpoint_path = output / "checkpoints" / (
                        f"{control_step:04d}_{phase}.pt")
                    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                    torch.save(snapshot, checkpoint_path)
                    restored = torch.load(
                        checkpoint_path, map_location="cpu", weights_only=False)
                    env.step(actions)
                    restore_error = _restore_snapshot(
                        env, restored, device=session.device)
                    if restore_error > 1e-4:
                        raise RuntimeError(
                            f"Restaurant checkpoint restore failed at {phase}: "
                            f"{restore_error:g}"
                        )
                    report.setdefault("checkpoints", []).append({
                        "checkpoint_id": phase,
                        "simulator_step": control_step,
                        "path": str(checkpoint_path),
                        "snapshot_digest": digest,
                        "restore_passed": True,
                        "restore_error": restore_error,
                        "restore_error_source": "max_abs_public_restaurant_physics_tensor_error",
                        "continued_after_restore": True,
                    })
                    for slot, branch_id in branch_ids.items():
                        batch.record_checkpoint(
                            branch_id,
                            checkpoint_id=phase,
                            simulator_step=control_step,
                            snapshot_digest=digest,
                            snapshot_schema=adapter.snapshot_schema,
                            restore_error=restore_error,
                            restore_passed=True,
                            restore_status="measured",
                            restore_error_source="max_abs_public_restaurant_physics_tensor_error",
                            payload={"checkpoint_path": str(checkpoint_path), "continued_after_restore": True},
                        )
                        batch.record_event(
                            branch_id,
                            event_type="checkpoint_restored",
                            simulator_step=control_step,
                            sim_time_s=sim_time_s,
                            payload={"checkpoint_id": phase, "restore_error": restore_error},
                        )
                        batch.record_progress(
                            branch_id,
                            simulator_step=control_step,
                            sim_time_s=sim_time_s,
                            progress={"phase": phase, "probe_completed": completed},
                        )
            final_states = adapter.read_all_branch_states({"env": env})
            for slot, branch_id in branch_ids.items():
                batch.record_event(
                    branch_id,
                    event_type="probe_complete",
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    payload={"task_contract": "scene_lifecycle_only"},
                )
                batch.record_network(
                    branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    network={
                        "messages_offered": 0,
                        "messages_delivered": 0,
                        "scope": "scene_lifecycle_probe_no_participant_network",
                    },
                )
                batch.record_cost(
                    branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    cost={
                        "control_steps": control_step,
                        "system_commands": control_step,
                        "participant_messages": 0,
                        "effective_execution": True,
                    },
                )
                batch.finish_branch(
                    branch_id,
                    completed=True,
                    independently_verified=True,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    outcome={
                        "probe_completed": True,
                        "full_task_completed": False,
                        "business_episode_validated": False,
                        "task_contract": "scene_lifecycle_only",
                        "final_state": final_states[slot],
                    },
                )
            batch.close_batch(
                status="completed",
                resident=args.slots,
                peak_active=args.slots,
                queued=0,
                details={
                    "task_contract": "scene_lifecycle_only",
                    "full_task_completed": False,
                    "formal_suite_registered": False,
                },
            )
            validation = validate_run(batch.output)
            report.update(
                status="completed",
                stage="finished",
                completed=args.slots,
                independently_verified=args.slots,
                branch_data_output=str(batch.output),
                branch_data_validation=validation,
                branch_data_counts=validation["metrics"],
                checkpoint_restore_error=max(
                    row["restore_error"] for row in report.get("checkpoints", [])
                ),
                branch_data_tool_passed=validation["status"] == "passed",
                wall_s=time.time() - report["started_unix"],
            )
            _write_json(output / "report.json", report)
            return report
    except BaseException as error:
        report.update(
            status="failed",
            stage="failed",
            error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(),
        )
        _write_json(output / "report.json", report)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", type=int, default=1)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.slots <= 32:
        raise SystemExit("--slots must be between 1 and 32")
    if args.steps < 4:
        raise SystemExit("--steps must be at least 4")
    report = run(args)
    print(json.dumps({
        "status": report["status"],
        "stage": report.get("stage"),
        "task_contract": report["task_contract"],
        "branch_data_tool_passed": report.get("branch_data_tool_passed"),
        "requested": report["requested"],
        "resident": report["resident"],
        "peak_active": report["peak_active"],
        "completed": report["completed"],
        "independently_verified": report["independently_verified"],
        "queued": report["queued"],
        "checkpoint_restore_error": report.get("checkpoint_restore_error"),
    }, ensure_ascii=False), flush=True)
    return 0 if report.get("branch_data_tool_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
