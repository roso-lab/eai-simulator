import json

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_session import GoalSession
from demo.benchmark.goal_warehouse_episode import (
    OverloadDiagnosticParticipant, WarehouseNativeOverloadBackend,
)
from demo.benchmark.provenance import canonical_sha256, file_sha256


def _pending():
    bundle = build_goal_task_package("warehouse_nvd")
    public = bundle["public_task"]
    participant = OverloadDiagnosticParticipant()
    participant.initialize(public, bundle["participant_api"])
    session = GoalSession(public, bundle["participant_api"], episode_id="test-native-episode")
    for action in participant.act(session.report()["events"]):
        session.submit_action(action)
    row = session.report()["tasks"]["participant_overload_crate"]
    return public, session.report(), row["task"]


@pytest.mark.parametrize("tamper", [None, "episode", "pose", "mass", "hash", "action", "snapshot",
                                   "coherent_forgery", "ledger_receipt"])
def test_native_overload_backend_rejects_unbound_evidence(tmp_path, monkeypatch, tamper):
    public, trusted, task = _pending()
    root = tmp_path / "episode"
    backend = WarehouseNativeOverloadBackend(public, root)
    native = root / "native_attachment"
    native.mkdir(parents=True)
    deployment = {"public_task": public}
    (native / "deployment.json").write_text(json.dumps(deployment), encoding="utf-8")
    evidence = {"episode_id": trusted["episode_id"], "task_id": task["task_id"],
                "object_id": "order_crate_01", "object_mass_kg": 12.0,
                "failure_reason": "member allocated load exceeds public payload",
                "source": "native_physx_attachment_admission", "object_moved": False,
                "task_advanced": False, "zero_stage_effect": True, "zero_pose_effect": True,
                "before": {"body_path": "/World/crate", "position_m": [0, 0, 0]},
                "after": {"body_path": "/World/crate", "position_m": [0, 0, 0]}}
    ref = "sha256:" + canonical_sha256(evidence)
    receipt = {"backend_id": "native-test", "receipt_ref": ref,
               "task_id": task["task_id"], "action_id": "participant_dispatch",
               "status": "failed", "completed_at_s": 4.0,
               "episode_id": trusted["episode_id"], "evidence_refs": [ref]}
    formal = {"episode_id": trusted["episode_id"], "evidence": evidence,
              "receipt": receipt, "completion": {"accepted": True},
              "ledger": {"episode_id": trusted["episode_id"],
                         "tasks": {task["task_id"]: {"status": "failed", "receipt": dict(receipt)}}}}
    sample_pose = dict(evidence["before"])
    report = {"run_id": "native-test-run", "formal_goal_episode": {"receipt_ref": ref},
              "body_bindings": {"objects": {"order_crate_01": {"body_path": "/World/crate"}}},
              "samples": [
                  {"phase": "settle", "bodies": {"/World/crate": sample_pose}},
                  {"phase": "attached_reverse", "bodies": {"/World/crate": sample_pose}}],
              "final_native_snapshot": {"bodies": {"/World/crate": sample_pose}}}
    if tamper == "episode":
        formal["episode_id"] = "different-episode"
    elif tamper == "pose":
        evidence["after"] = {"position_m": [1, 0, 0]}
    elif tamper == "mass":
        evidence["object_mass_kg"] = 80.0
    elif tamper == "action":
        receipt["action_id"] = "other-dispatch"
    elif tamper == "snapshot":
        report["samples"][0]["bodies"]["/World/crate"] = {
            "body_path": "/World/crate", "position_m": [0, 0, 1]}
    elif tamper == "coherent_forgery":
        evidence["before"] = {"body_path": "/World/crate", "position_m": [0, 0, 1]}
        evidence["after"] = dict(evidence["before"])
        updated_ref = "sha256:" + canonical_sha256(evidence)
        receipt["receipt_ref"] = updated_ref
        receipt["evidence_refs"] = [updated_ref]
        formal["ledger"]["tasks"][task["task_id"]]["receipt"] = dict(receipt)
        report["formal_goal_episode"]["receipt_ref"] = updated_ref
    elif tamper == "ledger_receipt":
        formal["ledger"]["tasks"][task["task_id"]]["receipt"]["action_id"] = "other-dispatch"
    for name, value in (("formal_goal_ledger_after.json", formal),
                        ("attachment_report.json", report)):
        (native / name).write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr("demo.benchmark.goal_warehouse_episode.prepare_probe",
                        lambda path, **kwargs: {"run_id": "native-test-run"})
    report_hash = file_sha256(native / "attachment_report.json")
    monkeypatch.setattr("demo.benchmark.goal_warehouse_episode.execute_probe",
                        lambda path: {"diagnostic_completed": True, "returncode": 0,
                                      "attachment_report_sha256": "incorrect" if tamper == "hash" else report_hash})
    if tamper:
        with pytest.raises(ValueError, match="native failure evidence|same-step crate readback"):
            backend.execute(task, episode_id=trusted["episode_id"], trusted_report={"session": trusted})
    else:
        result = backend.execute(task, episode_id=trusted["episode_id"], trusted_report={"session": trusted})
        assert result == {"status": "failed", "completed_at_s": 4.0, "receipt": receipt,
                          "public_observation": {
                              "skill_succeeded": False, "object_moved": False,
                              "task_advanced": False, "carrier_load_monitor": [{
                                  "agent_id": task["assigned_agents"][0],
                                  "requested_load_kg": 12.0, "effective_payload_kg": 8.0}]}}
