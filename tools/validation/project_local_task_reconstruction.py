"""Measure fresh-process reconstruction of an active physical navigation task.

The coordinator, real channel and locks are diagnostic fixtures, not a submitted
baseline. Every process re-executes its own commands and observations. No stored
action, pose or software state is injected to force equality.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import random
import shutil

from tools.project_runtime import PROJECT_ROOT, activate_project_runtime, provenance_report
from tools.validation.project_local_command_loop import _state
from tools.validation.project_local_node_route import CONTRACT, digest, persist_failure, write


CHECKPOINT_STEP = 120
TOTAL_STEPS = 160
POLICY = {"bits_per_tick": 8192, "max_queue_bits": 65536, "delay_ticks": 2,
          "ttl_ticks": 20, "loss_probability": 0., "seed": 0}


class PublicCoordinator:
    """Fixed public prefix policy whose dispatch actually depends on delivery."""

    def __init__(self):
        from benchmark.transport_v3 import SharedChannelV3
        from benchmark.fleet import TypedLockManager
        self.channel = SharedChannelV3(**POLICY)
        self.locks = TypedLockManager(session_identity="factory-active-prefix-v1", branch_key="common")
        self.events = []
        self.authorized = False
        self.started = False
        self.lease_id = None
        self.tick = -1

    def advance(self, tick):
        from benchmark.fleet import LockRequest
        if tick != self.tick + 1:
            raise ValueError("coordinator must advance exactly once per physical step")
        self.tick = tick
        if tick == 40 or (tick >= 49 and (tick - 49) % 10 == 0):
            kind = "navigation_authorization" if tick == 40 else "progress_request"
            frame = self.channel.submit(message_id=f"public/{tick}", sender_id="coordinator",
                receiver_id=CONTRACT["robot_id"], kind=kind,
                payload={"task": CONTRACT["task_id"], "target": CONTRACT["target_ref"]}, tick=tick)
            self.events.append({"tick": tick, "event": "submitted", "message_id": frame.message_id})
        for frame in self.channel.advance(tick):
            self.events.append({"tick": tick, "event": "delivered", "message_id": frame.message_id})
        for frame in self.channel.receive(receiver_id=CONTRACT["robot_id"], tick=tick):
            self.events.append({"tick": tick, "event": "consumed", "message_id": frame.message_id})
            if frame.kind == "navigation_authorization":
                receipt = self.locks.acquire(LockRequest("route-permit", "factory-L", CONTRACT["robot_id"],
                    "exclusive", "authored-navigation", tick, session_identity="factory-active-prefix-v1",
                    branch_key="common"), active_owners=(CONTRACT["robot_id"],))
                if receipt.status.value != "acquired":
                    raise RuntimeError("public route lease was not acquired")
                self.lease_id = receipt.lease_id
                self.authorized = True
        return self.authorized and not self.started

    def dispatched(self):
        if not self.authorized or self.started:
            raise RuntimeError("dispatch must follow exactly one delivered authorization")
        self.started = True
        self.events.append({"tick": self.tick, "event": "navigation_dispatched"})

    def snapshot(self):
        # JSON round-trip prevents the journal caller from mutating live state.
        return json.loads(json.dumps({"policy": "public-route-permission-v1", "tick": self.tick,
            "authorized": self.authorized, "started": self.started, "lease_id": self.lease_id,
            "events": self.events, "transport": self.channel.snapshot(),
            "locks": self.locks.measurement_snapshot()}, allow_nan=False))


def assert_active_boundary(executor, task_id, coordinator):
    state = executor.response(task_id).state
    if state["status"] != "pending" or state["quarantined"] or task_id not in executor.active_task_ids:
        raise RuntimeError("registered checkpoint must contain an active task")
    if executor.needs_host_tick(task_id):
        raise RuntimeError("registered checkpoint cannot split a prepared physical step")
    if not coordinator.channel.snapshot()["in_flight"]:
        raise RuntimeError("registered checkpoint must exercise an in-flight message")
    if not coordinator.locks.snapshot():
        raise RuntimeError("registered checkpoint must retain the actual route lease")


def validate_sham(path, fingerprint, reference):
    """Require the successful full sham for this exact original and source."""
    result = json.loads(path.read_text())
    verification = result.get("verification", {})
    original = json.loads((reference / "reconstruction.json").read_text())
    if not (result.get("passed") is True and result.get("status") == "verified"
            and result.get("inputs_unchanged") is True and result.get("fingerprint") == fingerprint
            and result.get("reference_manifest_sha256") == digest(reference / "reconstruction.json")
            and type(result.get("pid")) is int and result["pid"] not in {os.getpid(), original["pid"]}
            and verification.get("prefix_verified") is True
            and verification.get("original_continuation_verified") is True
            and verification.get("released_for_live_continuation") is False
            and verification.get("compared_steps") == TOTAL_STEPS
            and verification.get("target_steps") == CHECKPOINT_STEP
            and verification.get("first_divergence") is None):
        raise ValueError("live branch needs a matching successful independent sham receipt")
    return digest(path)


def run(output, reference=None, branch=None, sham_evidence=None):
    activate_project_runtime()
    output = output.resolve()
    if not output.is_relative_to(PROJECT_ROOT):
        raise ValueError("output must remain in project")
    if reference is not None:
        reference = reference.resolve()
        if not reference.is_relative_to(PROJECT_ROOT):
            raise ValueError("reference must remain in project")
    if branch not in {None, "continue", "hold"} or ((branch is None) != (sham_evidence is None)):
        raise ValueError("live branch and sham evidence must be supplied together")
    if branch is not None and reference is None:
        raise ValueError("live branch requires an original journal")
    if sham_evidence is not None:
        sham_evidence = sham_evidence.resolve()
        if not sham_evidence.is_relative_to(PROJECT_ROOT):
            raise ValueError("sham evidence must remain in project")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "heterocoord.active_task_reconstruction.v1", "pid": os.getpid(),
        "status": "starting", "passed": False, "paper_score_eligible": False,
        "strict_causal_replay_qualified": False, "physical_skill_qualified": False,
        "qualification_scope": "measured active-task prefix and continuation only",
        "branch": branch, "branch_steps": 0,
        "missing_coverage": ["hidden_contact_solver_state", "submitted_algorithm_state",
                             "full_fleet_runtime", "qualified_catalog_and_independent_calibration"],
        "provenance": provenance_report(), "session_close_observed": False}
    write(output / "result.json", report)
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from algorithm.skills.navigation import FrozenNavigationTopology, NavigationPose, ground_navigation_profile
    from task.resources import ResourceManager
    from task.executors.navigation import NavigationExecutor
    from task.executors.navigation_backends import PlanarVelocityNavigationAdapter
    selection_path = PROJECT_ROOT / "source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-Z1-v0.json"
    config = SimulatorLaunchConfig(env="EAI-Factory-Z1-v0", resolved_env_name="EAI-Factory-Z1-v0",
        selection_data=json.loads(selection_path.read_text()), device="cpu", seed=0, headless=True,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        app_launcher_args={"headless": True, "device": "cpu"})
    with open_simulator_session(config) as session, persist_failure(output, report):
        import numpy as np
        import torch
        import omni.usd
        from tools.physics_replay.live import LiveCheckpointBackend
        from tools.physics_replay.reconstruction import ReconstructionRecorder, ReconstructionVerifier
        env, robot = session.base_env, CONTRACT["robot_id"]
        readback = LiveCheckpointBackend(session, output / "readback")
        paths = {selection_path, Path(__file__).resolve(), PROJECT_ROOT / "tools/project_runtime.py",
                 PROJECT_ROOT / "simulator.py", PROJECT_ROOT / "tools/validation/project_local_node_route.py",
                 PROJECT_ROOT / "tools/validation/project_local_command_loop.py",
                 PROJECT_ROOT / "benchmark/fleet.py", PROJECT_ROOT / "benchmark/transport_v3.py"}
        for root in ("source/EAI/EAI", "source/EAI_assets/EAI_assets", "source/EAI_hmrs/EAI_hmrs",
                     "task", "algorithm/skills", "tools/physics_replay"):
            paths.update(p.resolve() for p in (PROJECT_ROOT / root).rglob("*.py") if "tests" not in p.parts)
        for layer in omni.usd.get_context().get_stage().GetUsedLayers():
            if layer.realPath:
                path = Path(layer.realPath).resolve()
                if not path.is_relative_to(PROJECT_ROOT):
                    raise ValueError(f"loaded layer outside project: {path}")
                paths.add(path)
        if any(not path.is_relative_to(PROJECT_ROOT) for path in paths):
            raise ValueError("source symlink escapes project")
        inventory = {str(p.relative_to(PROJECT_ROOT)): digest(p) for p in sorted(paths)}
        for path in paths:
            if path.suffix in {".py", ".json"}:
                saved = output / "input_sources" / path.relative_to(PROJECT_ROOT)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, saved)
        stamps = {p: (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in paths}

        def source_reader():
            for path, stamp in stamps.items():
                stat = path.stat()
                if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != stamp:
                    raise RuntimeError(f"execution source changed: {path}")
            return CONTRACT["scene_key"], inventory

        ids = CONTRACT["required_route"]
        topology = FrozenNavigationTopology({"schema": "eai.navigation.topology", "version": 1,
            "scene_key": "factory", "source": inventory, "coordinate_frame": "scene_world",
            "meters_per_unit": 1., "up_axis": "Z",
            "nodes": [{"id": name, "position_xyz": point, "target_refs": []}
                      for name, point in zip(ids, CONTRACT["points_m"])],
            "edges": [{"id": f"edge:{i}", "from": ids[i], "to": ids[i+1], "directed": True}
                      for i in range(2)]}, scene_key="factory", source_digests=inventory)
        fingerprint = {"source": inventory, "contract": CONTRACT, "topology_digest": topology.product_digest,
            "seed": 0, "device": "cpu", "checkpoint_step": CHECKPOINT_STEP, "steps": TOTAL_STEPS,
            "coordinator": "public-route-permission-v1", "channel_policy": POLICY,
            "branch_diagnostic": {"hold_max_speed_m_s": .04, "continue_min_displacement_m": .02},
            "coverage": "physical channels, clock, commands, RNG, declared navigation and coordinator measurements"}
        report["fingerprint"] = fingerprint
        report["reference_manifest_sha256"] = digest(reference / "reconstruction.json") if reference else None
        if branch is not None:
            report["sham_receipt_sha256"] = validate_sham(sham_evidence, fingerprint, reference)
        journal = (ReconstructionVerifier(reference, fingerprint, checkpoint="active-common-start")
                   if reference is not None else ReconstructionRecorder(output / "journal", fingerprint))
        branch_journal = None
        coordinator = PublicCoordinator()
        zero = torch.zeros((1, 3), device=env.device)
        actions = {robot: zero}
        command_history = []

        def seq():
            return int(env.common_step_counter)

        def now():
            return float(env.sim.current_time)

        def pose_reader():
            state = _state(session, robot)
            return NavigationPose(tuple(state["position_m"]), state["yaw_rad"])

        def velocity_reader():
            state = _state(session, robot)
            return (*state["linear_velocity_m_s"], *state["angular_velocity_rad_s"])

        def sink(command):
            if command.command_type != "base_velocity":
                raise ValueError("unexpected controller command")
            value = command.payload["velocity"]
            actions[robot] = torch.tensor([value], device=env.device)
            env.set_command(robot, "base_velocity", actions[robot])
            command_history.append({"tick": seq(), "value": list(value)})

        adapter = PlanarVelocityNavigationAdapter(robot, pose_reader=pose_reader, command_sink=sink,
            sequence_reader=seq, time_reader=now, velocity_reader=velocity_reader,
            profile=ground_navigation_profile(arrival_radius_m=CONTRACT["arrival_radius_m"]),
            cruise_speed_m_s=CONTRACT["cruise_speed_m_s"], max_yaw_rate_rad_s=CONTRACT["max_yaw_rate_rad_s"])
        executor = NavigationExecutor(topology=topology, simulation_time=now,
            resources=ResourceManager(identity_namespace="factory-active-prefix-v1"),
            owner_id="public-navigation", source_reader=source_reader,
            snapshot_validator=lambda s, motion: s == seq(), timeout_s=CONTRACT["timeout_s"])
        executor.register_adapter(adapter)

        def request(intent):
            return {"request_id": f"request/{seq()}", "task_id": CONTRACT["task_id"],
                "skill_id": "eai.navigation.node_route", "skill_version": "simulator.skill.v1",
                "robot_id": robot, "target_ref": CONTRACT["target_ref"],
                "parameters": {"intent": intent, "world_state_seq": seq(),
                               "route_graph_digest": topology.route_graph_digest}}

        try:
            for index in range(TOTAL_STEPS):
                source_reader()
                dispatch = coordinator.advance(index)
                if dispatch:
                    executor.dispatch(request("start"))
                    coordinator.dispatched()
                if coordinator.started:
                    intent = "hold" if branch == "hold" and index >= CHECKPOINT_STEP else (
                        "start" if dispatch else "continue")
                    executor.prepare(request(intent))
                env.step(actions)
                if coordinator.started:
                    executor.after_step(CONTRACT["task_id"])
                frame = readback.read_physics()
                frame["clock"] = np.array([seq(), now(), env.sim.current_time_step_index])
                frame["command/carter"] = actions[robot]
                frame["rng/torch"] = torch.get_rng_state()
                python_rng, numpy_rng = random.getstate(), np.random.get_state()
                frame["rng/python"] = np.asarray(python_rng[1], dtype=np.uint64)
                frame["rng/python/gauss"] = np.asarray([python_rng[2] is not None, python_rng[2] or 0.])
                frame["rng/numpy"] = numpy_rng[1]
                frame["rng/numpy/metadata"] = np.array(numpy_rng[2:])
                state = {"coordinator": coordinator.snapshot(), "executor": executor.measurement_snapshot(),
                         "adapter": adapter.measurement_snapshot(), "commands": command_history}
                checkpoint = "active-common-start" if index + 1 == CHECKPOINT_STEP else None
                if checkpoint:
                    assert_active_boundary(executor, CONTRACT["task_id"], coordinator)
                    report["checkpoint_state"] = json.loads(json.dumps(state, allow_nan=False))
                    report["checkpoint_physics"] = _state(session, robot)
                if reference is None:
                    journal.append(frame, task_state=state, checkpoint=checkpoint)
                elif not journal.released:
                    journal.compare(frame, task_state=state, checkpoint=checkpoint)
                    if checkpoint and branch is not None:
                        journal.release()
                        report["release_physics"] = _state(session, robot)
                        branch_journal = ReconstructionRecorder(output / "branch_journal", {
                            **fingerprint, "live_branch": branch,
                            "parent_manifest_sha256": report["reference_manifest_sha256"]})
                else:
                    report["branch_steps"] += 1
                    branch_journal.append(frame, task_state=state)
            report["inputs_unchanged"] = all(digest(p) == inventory[str(p.relative_to(PROJECT_ROOT))] for p in paths)
            if not report["inputs_unchanged"]:
                raise RuntimeError("source changed during execution")
            report["final_physics"] = _state(session, robot)
            report["final_task_state"] = state
            if reference is None:
                journal.close(complete=True)
                report.update(status="recorded", passed=True)
            else:
                verification = journal.report()
                if branch is None:
                    report.update(status="verified", passed=verification["prefix_verified"]
                                  and verification["original_continuation_verified"])
                else:
                    report["intervention_observed"] = (
                        report["final_physics"]["speed_m_s"] <= .04
                        and executor.response(CONTRACT["task_id"]).state["safe_action"] == "hold"
                        if branch == "hold" else math.dist(report["release_physics"]["position_m"][:2],
                                                           report["final_physics"]["position_m"][:2]) > .02)
                    report.update(status="completed", passed=verification["prefix_verified"]
                        and verification["released_for_live_continuation"]
                        and report["branch_steps"] == TOTAL_STEPS - CHECKPOINT_STEP
                        and report["intervention_observed"])
        finally:
            if branch_journal is not None:
                branch_journal.close(complete=report["passed"])
            if reference is not None:
                report["verification"] = journal.report()
                journal.close()
            elif not journal.closed:
                journal.close(complete=False)
            write(output / "result.json", report)
        print(json.dumps({key: report[key] for key in ("status", "passed", "pid", "paper_score_eligible")}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--branch", choices=("continue", "hold"))
    parser.add_argument("--sham-evidence", type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.reference, args.branch, args.sham_evidence))
