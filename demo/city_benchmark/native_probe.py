"""Serial native City incident-receiver microdiagnostic.

``--prepare`` writes the executable contract without starting Isaac.
``--execute`` must be run by the caller in ``env_isaaclab``.  It takes the
shared stage17 lock, opens one City session, reads one real robot tensor pose,
and sends that pose observation through a diagnostic incident request and an
independent receiver.  The result is a native receipt trace, not full City
service success.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import hashlib
import uuid
import math
from pathlib import Path
from typing import Any, Iterator, Mapping

from .episode_adapter import CityEpisodeEvidenceHost
from .live_witness import CityIncidentReceiver, CityLiveEpisodeWitness, LiveEvidence


ROOT = Path(__file__).resolve().parents[2]
SERIAL_LOCK = ROOT / "tmp" / "goal_physical_probe.lock"
SCHEMA = "eai.city.native_incident_probe_v1"
DIAGNOSTIC_AGENT = "go2_1"
DIAGNOSTIC_REGION_ID = "city_native_probe_region"
DIAGNOSTIC_REGION_CENTER_XY = (-69.0, 49.0)
DIAGNOSTIC_REGION_RADIUS_M = 2.5


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def _source_hashes() -> dict[str, str]:
    from demo.city.usd_scene import CITY_STATIC_USD, SUPPORTED_VEHICLES, VEHICLE_ROOT

    paths = [
        CITY_STATIC_USD,
        *(VEHICLE_ROOT / name for name, _ in SUPPORTED_VEHICLES),
        ROOT / "demo/city_benchmark/native_probe.py",
        ROOT / "demo/city_benchmark/live_witness.py",
        ROOT / "demo/city_benchmark/episode_adapter.py",
        ROOT / "demo/city/main.py",
        ROOT / "demo/city/runtime.py",
        ROOT / "demo/city/usd_scene.py",
        ROOT / "simulator.py",
    ]
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _prepared(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("native probe requires --prepare before --execute") from exc
    if value.get("schema") != SCHEMA or value.get("status") != "prepared":
        raise RuntimeError("native probe prepare record is invalid or already executed")
    return value


def _finite_pose(pose: Any) -> tuple[float, float, float]:
    values = tuple(float(value) for value in pose)
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("robot tensor pose must contain three finite values")
    return values


def _robot_tensor_pose(session: Any, agent_id: str = DIAGNOSTIC_AGENT) -> tuple[float, float, float]:
    articulation = session.base_env.scene.articulations.get(agent_id)
    if articulation is None:
        raise RuntimeError(f"diagnostic articulation is missing: {agent_id}")
    tensor = articulation.data.root_pos_w[0]
    if hasattr(tensor, "detach"):
        tensor = tensor.detach()
    if hasattr(tensor, "cpu"):
        tensor = tensor.cpu()
    if hasattr(tensor, "tolist"):
        tensor = tensor.tolist()
    return _finite_pose(tensor)


def _region_distance(pose: tuple[float, float, float]) -> float:
    return math.dist(pose[:2], DIAGNOSTIC_REGION_CENTER_XY)


@contextmanager
def _serial_lock() -> Iterator[None]:
    SERIAL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with SERIAL_LOCK.open("a+") as descriptor:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"canonical City native probe lock is busy: {SERIAL_LOCK}") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _report_guard(path: Path, report: dict[str, Any]) -> Iterator[None]:
    """Persist the latest report before an enclosing Kit context tears down."""
    try:
        yield
    except BaseException as exc:
        report.update(
            status="blocked",
            blocker=f"{type(exc).__name__}: {exc}",
            full_city_service="pending",
        )
        _write(path, report)
        raise
    else:
        _write(path, report)


def prepare_probe(output_root: Path, *, device: str = "cpu", seed: int = 17,
                  overwrite: bool = False) -> dict[str, Any]:
    """Write a source/config contract without importing or launching Isaac."""
    prepare_path = output_root / "native_probe_prepare.json"
    if prepare_path.exists() and not overwrite:
        existing = _prepared(prepare_path)
        if existing.get("device") != device or existing.get("seed") != int(seed):
            raise FileExistsError("prepare record has different device/seed; use --overwrite explicitly")
        raise FileExistsError("prepare record already exists; use --overwrite explicitly")
    source_hashes = _source_hashes()
    report = {
        "schema": SCHEMA, "version": 1, "status": "prepared", "scoreable": False,
        "execution_status": "pending", "execution_validated": False,
        "scene_key": "city", "task_name": "city_native_probe",
        "device": device, "seed": int(seed), "people": 1, "use_flow": False,
        "diagnostic_agent_id": DIAGNOSTIC_AGENT, "diagnostic_region_id": DIAGNOSTIC_REGION_ID,
        "diagnostic_region_center_xy": list(DIAGNOSTIC_REGION_CENTER_XY),
        "diagnostic_region_radius_m": DIAGNOSTIC_REGION_RADIUS_M,
        "serial_lock": str(SERIAL_LOCK),
        "source_hashes": source_hashes,
        "runtime_boundary": "simulator.open_simulator_session -> CityEaiRuntime",
        "observation_source": "session.base_env.scene.articulations.go2_1.data.root_pos_w",
        "receiver_boundary": "request -> CityIncidentReceiver.receive -> stored receipt",
        "full_city_service": "pending",
        "blockers": [
            "diagnostic probe does not establish public guide_people or control_traffic service",
            "continuous response-access readback remains pending",
        ],
    }
    _write(prepare_path, report)
    return report


def record_diagnostic_incident(*, episode_id: str, pose: tuple[float, float, float],
                               now_s: float, agent_id: str = DIAGNOSTIC_AGENT) -> dict[str, Any]:
    """Run the request -> independent receiver trace for one measured pose."""
    pose = _finite_pose(pose)
    now_s = float(now_s)
    if not math.isfinite(now_s) or now_s <= 0:
        raise ValueError("diagnostic episode time must be finite and positive")
    distance = _region_distance(pose)
    admission_trace = {
        "service": "report_incident", "agent_id": agent_id,
        "region_id": DIAGNOSTIC_REGION_ID, "pose_xyz": list(pose),
        "distance_to_region_m": distance,
        "admitted": distance <= DIAGNOSTIC_REGION_RADIUS_M,
        "source": "native_robot_tensor_pose",
    }
    if not admission_trace["admitted"]:
        raise RuntimeError(f"robot tensor pose is outside diagnostic region: {distance:.3f} m")

    source_id = "isaac.city.native_probe"
    host = CityEpisodeEvidenceHost(
        admission=lambda service, ids, region, target, mode, timestamp:
        service == "report_incident"
        and region == DIAGNOSTIC_REGION_ID
        and timestamp == now_s
        and _region_distance(pose) <= DIAGNOSTIC_REGION_RADIUS_M,
        episode_id=episode_id, source_id=source_id)
    host.advance(now_s)
    witness = CityLiveEpisodeWitness(host, episode_id=episode_id)
    observation_ref = f"city_native_pose_observation_{int(now_s * 1000):09d}"
    observation = {
        "id": observation_ref, "episode_id": episode_id, "source_id": source_id,
        "producer_agent_id": DIAGNOSTIC_AGENT, "region_id": DIAGNOSTIC_REGION_ID,
        "captured_at_s": now_s, "delivered_at_s": now_s, "valid_until_s": now_s + 5.0,
        "entities": [{"id": agent_id, "kind": "robot", "position_xyz": list(pose),
                      "state": "native_tensor_pose_readback"}],
    }
    observation_evidence = LiveEvidence(
        episode_id=episode_id, evidence_ref=observation_ref, kind="observation",
        source_id=source_id, entity_ids=(agent_id,), observed_time_s=now_s,
        status_before={"status": "not_delivered"}, status_after={"status": "delivered"})
    witness.deliver_observation(observation, observation_evidence)
    host.record_role_occupancy(agent_id, "incident_observer", DIAGNOSTIC_REGION_ID,
                               start_time_s=0.0, end_time_s=now_s)
    request = witness.report_incident(
        agent_id=agent_id, region_id=DIAGNOSTIC_REGION_ID,
        observation_refs=(observation_ref,), recipient_service_id="city_dispatch")

    receiver = CityIncidentReceiver(episode_id=episode_id, service_id="city_dispatch")
    receiver_payload = dict(request)
    receiver_payload["episode_id"] = episode_id
    receipt = receiver.receive(receiver_payload, now_s=now_s)
    receipt_evidence = LiveEvidence(
        episode_id=episode_id, evidence_ref=receipt["receipt_evidence_ref"],
        kind="service_receipt", source_id=receipt["source_id"], entity_ids=(),
        observed_time_s=now_s, status_before={"status": "queued"},
        status_after={"status": "received"})
    witness.record_receipt(
        request["request_id"], recipient_service_id="city_dispatch",
        observation_refs=[observation_ref], received_time_s=now_s,
        evidence=receipt_evidence)
    stored = receiver.receipt(request["request_id"])
    return {
        "episode_id": episode_id, "observation": observation,
        "observation_evidence": observation_evidence.as_dict(),
        "admission_trace": admission_trace, "request": request,
        "receiver_payload": receiver_payload, "receipt": receipt,
        "stored_receipt": stored, "service_review": witness.review(request["request_id"]),
        "full_city_service": "pending", "scoreable": False,
    }


def _execute_locked(output_root: Path, *, prepared: Mapping[str, Any], run_id: str,
                    headless: bool) -> dict[str, Any]:
    from functools import partial
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from demo.city.main import _zero_actions, build_selection, configure_city_env_cfg
    from demo.city.runtime import CityEaiRuntime, CityRuntimeConfig

    device = str(prepared["device"])
    seed = int(prepared["seed"])
    selection = build_selection(seed)
    report: dict[str, Any] = {
        "schema": SCHEMA, "version": 1, "status": "starting", "scoreable": False,
        "execution_validated": False, "device_requested": device,
        "run_id": run_id, "episode_id": run_id,
        "scene_key": "city", "seed": seed,
        "source_hashes_before": dict(prepared["source_hashes"]),
        "query_support_configured": False, "people_requested": 1, "use_flow": False,
    }

    def configure(env_cfg: Any) -> None:
        configure_city_env_cfg(env_cfg, seed=seed, use_flow=False, people=1)
        env_cfg.sim.enable_scene_query_support = True
        report["query_support_configured"] = env_cfg.sim.enable_scene_query_support is True

    config = SimulatorLaunchConfig(
        env="city_native_probe", resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=device, seed=seed,
        headless=headless, enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, env_cfg_hook=partial(configure),
    )
    _write(output_root / "native_probe_start.json", report)
    try:
        with open_simulator_session(config) as session, _report_guard(
                output_root / "native_probe_report.json", report):
            city = CityEaiRuntime.from_current_stage(
                config=CityRuntimeConfig(seed=seed, use_flow=False))
            city.reset()
            dt = float(session.base_env.step_dt)
            session.env.step(_zero_actions(session))
            city.step(dt=dt)
            pose = _robot_tensor_pose(session)
            report.update({
                "device_actual": str(session.device), "episode_time_s": city.elapsed,
                "robot_pose_xyz": list(pose), "diagnostic_region_id": DIAGNOSTIC_REGION_ID,
                "diagnostic_region_center_xy": list(DIAGNOSTIC_REGION_CENTER_XY),
                "diagnostic_region_radius_m": DIAGNOSTIC_REGION_RADIUS_M,
                "query_evidence": {
                    "status": "not_used_for_receipt",
                    "reason": "native incident admission uses robot tensor pose only",
                },
            })
            report["incident_trace"] = record_diagnostic_incident(
                episode_id=run_id,
                pose=pose, now_s=city.elapsed)
            report["status"] = "diagnostic_completed"
            report["full_city_service"] = "pending"
            report["sources_unchanged"] = _source_hashes() == dict(prepared["source_hashes"])
            if not report["sources_unchanged"]:
                raise RuntimeError("City source hash changed during native probe")
            # Persist the complete report while the simulator context is still
            # alive; Kit teardown must not be able to erase the only evidence.
            _write(output_root / "native_probe_report.json", report)
    except BaseException as exc:
        report.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}")
        report["full_city_service"] = "pending"
    _write(output_root / "native_probe_report.json", report)
    return report


def execute_probe(output_root: Path, *, device: str = "cpu", seed: int = 17,
                  headless: bool = True, overwrite: bool = False) -> dict[str, Any]:
    with _serial_lock():
        prepared = _prepared(output_root / "native_probe_prepare.json")
        if prepared.get("device") != device or prepared.get("seed") != int(seed):
            raise ValueError("execute device/seed must exactly match --prepare")
        if prepared.get("source_hashes") != _source_hashes():
            raise RuntimeError("City source hashes differ from --prepare; prepare again explicitly")
        report_path = output_root / "native_probe_report.json"
        start_path = output_root / "native_probe_start.json"
        if start_path.exists() and not overwrite:
            raise FileExistsError(
                "native probe launch already exists; use a fresh directory or --overwrite explicitly")
        if report_path.exists() and not overwrite:
            raise FileExistsError("native probe report already exists; use --overwrite explicitly")
        return _execute_locked(output_root, prepared=prepared, run_id=str(uuid.uuid4()),
                               headless=headless)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--overwrite", action="store_true",
                        help="explicitly replace an existing prepare/report artifact")
    args = parser.parse_args(argv)
    if args.prepare:
        result = prepare_probe(args.output_root, device=args.device, seed=args.seed,
                               overwrite=args.overwrite)
    else:
        result = execute_probe(args.output_root, device=args.device, seed=args.seed,
                               headless=not args.gui, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not args.execute or result.get("status") == "diagnostic_completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
