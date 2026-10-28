"""Gate B metering chain: request → metered bytes → ledger rows → receipt.

The chain runs against the real production seams, not stubs: the ported
``CommunicationRuntime`` meters transport bytes, the real ``BranchLedger``
from ``benchmark.evidence`` receives hash-chained observation rows, the
content-addressed ``ObservationArtifactStore`` holds the frame bytes, and
``ObservationReceipt`` is issued only for evidence that still verifies.

Every negative case asserts full cleanliness: a rejected request must leave
no ledger row, no communication bytes, and no artifact.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from benchmark.evidence import BranchLedger
from benchmark.observation_artifacts import ObservationArtifactStore
from algorithm.skills.observation.communication import CommunicationRuntime
from benchmark.protocol import BranchRole, RunKey
from algorithm.skills.contracts import SkillRequest
from algorithm.skills.observation import FakeOnDemandCameraBackend, GoalCameraTelemetry, GoalCameraTools
from algorithm.skills.observation.runtime import CameraObservationRuntime

PUBLIC = {
    "task_id": "warehouse_nvd_observation_metering",
    "mission": {"scene_key": "warehouse_nvd"},
    "candidate_robot_pool": [{"id": "r1"}],
    "budgets": {"simulation_s": 240.0},
}
HIDDEN = "hidden_joiner"
CATALOG = {"r1": {"cam": {
    "member": "primary", "modalities": ["rgb"], "encodings": ["png"],
    "max_width": 128, "max_height": 96, "max_bytes": 1000,
}}}


def make_runtime(*, contract=None, catalog=None, with_store=True, with_ledger=True):
    tools = GoalCameraTools(
        PUBLIC, episode_id="obs-meter",
        sensor_catalog=catalog or CATALOG,
        backend=FakeOnDemandCameraBackend(warmup_frames=0),
        telemetry=GoalCameraTelemetry(CommunicationRuntime(contract), message_prefix="obs-meter"),
        validity_s=20.0,
    )
    ledger = BranchLedger(RunKey("base", "system", "condition", "r0", "round0",
                                  BranchRole.FACT)) if with_ledger else None
    store = ObservationArtifactStore() if with_store else None
    runtime = CameraObservationRuntime(tools, ledger=ledger, artifact_store=store)
    return runtime, tools, ledger, store


def skill_request(*, request_id="obs-1", robot="r1", camera="cam", deadline=5,
                  max_bytes=500, encoding="png", extra=None):
    params = {"camera_ref": camera, "mode": "capture", "deadline": deadline,
              "resolution": {"width": 32, "height": 24},
              "quality": {"encoding": encoding, "max_bytes": max_bytes}}
    if extra:
        params.update(extra)
    return SkillRequest(request_id, "eai.observation.camera", robot, parameters=params)


def camera_bytes(report):
    return sum(d.get("bytes_sent", 0)
               for m in report["communication"]["messages"].values()
               for d in m["recipients"].values())


def delivered_row(report):
    return next(m for m in report["communication"]["messages"].values()
                if m["channel"] == "camera")


def test_request_meters_bytes_writes_ledger_rows_and_yields_receipt():
    runtime, tools, ledger, store = make_runtime()
    accept = runtime.request(skill_request(), time_s=0)
    assert accept["accepted"] is True
    events = runtime.tick(0)
    assert any(e.get("kind") == "camera_frame" for e in events)

    report = tools.report()
    # Metering: canonical transport bytes were charged on the camera channel.
    message = delivered_row(report)
    assert message["size_bytes"] > 0
    assert camera_bytes(report) > 0

    # Ledger rows: acceptance plus delivery, hash-chained on the real ledger.
    rows = ledger.events
    kinds = [row.kind for row in rows]
    assert kinds[0] == "accepted"
    assert "delivered" in kinds
    assert all(row.source == "observation.camera" for row in rows)
    accepted_row = rows[0]
    assert accepted_row.payload["request_id"] == "obs-1"
    delivered = next(row for row in rows if row.kind == "delivered")
    frame = delivered.payload["data"]
    assert frame["payload_sha256"] == hashlib.sha256(
        base64.b64decode(frame["payload_b64"])).hexdigest()
    for previous, row in zip(rows, rows[1:]):
        assert row.previous_hash == previous.event_hash

    # Receipt: digest + artifact identity + byte accounting, verified.
    receipt = runtime.receipt("obs-1")
    assert receipt.status == "delivered"
    assert receipt.terminal is True
    assert receipt.byte_length > 0
    assert receipt.transport_size_bytes == message["size_bytes"]
    assert store.get(receipt.artifact_sha256) == base64.b64decode(frame["payload_b64"])
    mapping = receipt.to_mapping()
    assert "payload_b64" not in mapping and "file://" not in json.dumps(mapping)

    # The ledger seals; late appends fail closed.
    ledger.seal()
    with pytest.raises(RuntimeError, match="sealed"):
        runtime.request(skill_request(request_id="obs-2"), time_s=1)


def test_negative_quality_over_limit_leaves_no_trace():
    runtime, tools, ledger, store = make_runtime()
    result = runtime.request(skill_request(max_bytes=5_000), time_s=0)
    assert result["accepted"] is False and result["error_code"] == "budget_exceeded"
    runtime.tick(0)
    report = tools.report()
    assert report["request_count"] == 0
    assert camera_bytes(report) == 0
    assert ledger.events == ()
    assert store.manifest() == {}
    with pytest.raises(KeyError):
        runtime.receipt("obs-1")


def test_negative_expired_deadline_leaves_no_trace():
    runtime, tools, ledger, store = make_runtime()
    result = runtime.request(skill_request(deadline=0), time_s=0)
    assert result["accepted"] is False and result["error_code"] == "invalid_deadline"
    runtime.tick(0)
    assert tools.report()["request_count"] == 0
    assert camera_bytes(tools.report()) == 0
    assert ledger.events == ()
    assert store.manifest() == {}


def test_negative_hidden_identity_not_enumerable_via_runtime():
    catalog = {**CATALOG, HIDDEN: {"cam": dict(CATALOG["r1"]["cam"])}}
    runtime, tools, ledger, store = make_runtime(catalog=catalog)
    profile = tools.public_profile()
    assert HIDDEN not in json.dumps(profile)
    result = runtime.request(skill_request(robot=HIDDEN), time_s=0)
    assert result["accepted"] is False and result["error_code"] == "unknown_robot"
    assert tools.report()["request_count"] == 0
    assert ledger.events == ()
    with pytest.raises(RuntimeError):
        tools.register_public_robot(HIDDEN)


def test_timeout_retains_bytes_and_suppresses_late_frame_in_ledger():
    runtime, tools, ledger, store = make_runtime(contract={"delay_s": 5})
    assert runtime.request(skill_request(deadline=2), time_s=0)["accepted"] is True
    runtime.tick(0)
    timeout_events = runtime.tick(2)
    assert any(e.get("kind") == "camera_request_timeout" for e in timeout_events)
    late = runtime.tick(5)
    assert not any(e.get("kind") == "camera_frame" for e in late)

    # Bytes already metered are retained; no rollback, no fake drop reason.
    assert camera_bytes(tools.report()) > 0
    kinds = [row.kind for row in ledger.events]
    assert kinds == ["accepted", "camera_request_timeout"]
    timeout_row = ledger.events[-1]
    assert timeout_row.payload["kind"] == "camera_request_timeout"
    assert timeout_row.payload["data"]["request_id"] == "obs-1"
    assert timeout_row.payload["data"]["response_deadline_s"] == 2.0
    assert "drop" not in json.dumps(timeout_row.payload)

    receipt = runtime.receipt("obs-1")
    assert receipt.status == "timeout" and receipt.terminal is True
    # The metered bytes stay identifiable by digest, but the suppressed
    # frame never became artifact-backed evidence.
    assert receipt.payload_sha256 is not None
    assert receipt.payload_sha256 not in store.manifest()


def test_receipt_fails_closed_on_missing_or_forged_artifact():
    runtime, tools, ledger, store = make_runtime()
    runtime.request(skill_request(), time_s=0)
    runtime.tick(0)
    receipt = runtime.receipt("obs-1")
    digest = receipt.artifact_sha256

    # Forged bytes under the claimed digest are rejected.
    store._objects[digest] = b"tampered"
    with pytest.raises(ValueError, match="digest mismatch"):
        runtime.receipt("obs-1")

    # Missing artifact bytes are rejected too, not silently waved through.
    del store._objects[digest]
    with pytest.raises(ValueError, match="bytes missing"):
        runtime.receipt("obs-1")


def test_no_opt_in_runtime_stays_silent():
    runtime, tools, ledger, store = make_runtime()
    runtime.tick(0)
    assert tools.report()["request_count"] == 0
    assert camera_bytes(tools.report()) == 0
    assert ledger.events == ()
