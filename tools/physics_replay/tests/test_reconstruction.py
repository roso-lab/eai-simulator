import numpy as np
import pytest

from tools.physics_replay.reconstruction import (
    ReconstructionMismatch, ReconstructionRecorder, ReconstructionVerifier,
)


def record(directory):
    writer = ReconstructionRecorder(directory, {"task": "fixture", "seed": 0})
    writer.append({"force_N": np.array([1.0])}, task_state={"phase": "carry"}, checkpoint="carried")
    writer.append({"force_N": np.array([2.0, 3.0])}, task_state={"phase": "move"})
    writer.close()
    return writer


def verifier(directory, monkeypatch):
    monkeypatch.setattr("tools.physics_replay.reconstruction.os.getpid", lambda: -1)
    return ReconstructionVerifier(directory, {"task": "fixture", "seed": 0}, checkpoint="carried")


def test_uninterrupted_reference_and_variable_contacts(tmp_path, monkeypatch):
    record(tmp_path)
    reader = verifier(tmp_path, monkeypatch)
    with pytest.raises(ReconstructionMismatch, match="exact verified"):
        reader.release()
    reader.compare({"force_N": [1.0]}, task_state={"phase": "carry"}, checkpoint="carried")
    assert reader.prefix_verified
    reader.compare({"force_N": [2.0, 3.0]}, task_state={"phase": "move"})
    assert reader.report()["original_continuation_verified"]
    assert not reader.report()["complete_physical_state_proven"]
    assert all(error == 0 for error in reader.max_errors.values())
    with pytest.raises(ReconstructionMismatch):
        reader.release()
    reader.close()


def test_release_only_at_target_then_do_not_certify_new_branch(tmp_path, monkeypatch):
    record(tmp_path)
    reader = verifier(tmp_path, monkeypatch)
    reader.compare({"force_N": [1.0]}, task_state={"phase": "carry"}, checkpoint="carried")
    reader.release()
    assert reader.report()["released_for_live_continuation"]
    assert not reader.report()["original_continuation_verified"]
    with pytest.raises(ReconstructionMismatch, match="released"):
        reader.compare({"force_N": [2.0, 3.0]}, task_state={"phase": "move"})
    reader.close()


@pytest.mark.parametrize("frame,state,checkpoint,channel", [
    ({"force_N": [1.0 + 1e-12]}, {"phase": "carry"}, "carried", "force_N"),
    ({"force_N": [1.0, 0.0]}, {"phase": "carry"}, "carried", "force_N"),
    ({"force_N": np.array([1.0], dtype=np.float32)}, {"phase": "carry"}, "carried", "force_N"),
    ({"force_N": [float("nan")]}, {"phase": "carry"}, "carried", "journal"),
    ({"other": [1.0]}, {"phase": "carry"}, "carried", "channels"),
    ({"force_N": [1.0]}, {"phase": "move"}, "carried", "task_state"),
    ({"force_N": [1.0]}, {"phase": "carry"}, None, "task_state"),
])
def test_fail_closed_on_any_deviation(tmp_path, monkeypatch, frame, state, checkpoint, channel):
    record(tmp_path)
    reader = verifier(tmp_path, monkeypatch)
    with pytest.raises(ReconstructionMismatch):
        reader.compare(frame, task_state=state, checkpoint=checkpoint)
    assert reader.report()["first_divergence"]["channel"] == channel
    assert not reader.prefix_verified
    with pytest.raises(ReconstructionMismatch):
        reader.release()
    reader.close()


def test_integrity_and_incomplete_recording(tmp_path, monkeypatch):
    writer = ReconstructionRecorder(tmp_path, {"task": "fixture", "seed": 0})
    writer.append({"force_N": [1.0]}, task_state={}, checkpoint="carried")
    writer.close(complete=False)
    with pytest.raises(ValueError, match="complete"):
        verifier(tmp_path, monkeypatch)


@pytest.mark.parametrize("filename", ["frames.jsonl", "frame_00000000.npz"])
def test_tampered_artifact_is_rejected(tmp_path, monkeypatch, filename):
    record(tmp_path)
    with (tmp_path / filename).open("ab") as stream:
        stream.write(b"corrupted")
    if filename.endswith("jsonl"):
        with pytest.raises(ValueError, match="integrity"):
            verifier(tmp_path, monkeypatch)
    else:
        reader = verifier(tmp_path, monkeypatch)
        with pytest.raises(ReconstructionMismatch, match="integrity"):
            reader.compare({"force_N": [1.0]}, task_state={"phase": "carry"}, checkpoint="carried")
        reader.close()


def test_same_process_and_changed_fingerprint_rejected(tmp_path, monkeypatch):
    record(tmp_path)
    with pytest.raises(ValueError, match="independent process"):
        ReconstructionVerifier(tmp_path, {"task": "fixture", "seed": 0}, checkpoint="carried")
    monkeypatch.setattr("tools.physics_replay.reconstruction.os.getpid", lambda: -1)
    with pytest.raises(ValueError, match="fingerprint"):
        ReconstructionVerifier(tmp_path, {"task": "fixture", "seed": 1}, checkpoint="carried")


def test_original_cannot_be_overwritten_or_checkpoint_reused(tmp_path):
    writer = record(tmp_path)
    with pytest.raises(FileExistsError):
        ReconstructionRecorder(tmp_path, {"seed": 0})
    with pytest.raises(RuntimeError, match="closed"):
        writer.append({"value": [1.0]}, task_state={})
    other = ReconstructionRecorder(tmp_path / "other", {"seed": 0})
    other.append({"value": [1.0]}, task_state={}, checkpoint="carried")
    with pytest.raises(ValueError, match="unique"):
        other.append({"value": [1.0]}, task_state={}, checkpoint="carried")
    other.close()


def test_missing_last_frame_cannot_certify_continuation(tmp_path, monkeypatch):
    record(tmp_path)
    reader = verifier(tmp_path, monkeypatch)
    reader.compare({"force_N": [1.0]}, task_state={"phase": "carry"}, checkpoint="carried")
    (tmp_path / "frame_00000001.npz").unlink()
    with pytest.raises(ReconstructionMismatch):
        reader.compare({"force_N": [2.0, 3.0]}, task_state={"phase": "move"})
    assert not reader.report()["original_continuation_verified"]
    reader.close()


def test_index_changed_before_live_release_blocks_execution(tmp_path, monkeypatch):
    record(tmp_path)
    reader = verifier(tmp_path, monkeypatch)
    reader.compare({"force_N": [1.0]}, task_state={"phase": "carry"}, checkpoint="carried")
    with (tmp_path / "frames.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ReconstructionMismatch, match="index changed"):
        reader.release()
    assert not reader.report()["released_for_live_continuation"]
    reader.close()


def test_extra_step_invalidates_verification(tmp_path, monkeypatch):
    record(tmp_path)
    reader = verifier(tmp_path, monkeypatch)
    reader.compare({"force_N": [1.0]}, task_state={"phase": "carry"}, checkpoint="carried")
    reader.compare({"force_N": [2.0, 3.0]}, task_state={"phase": "move"})
    with pytest.raises(ReconstructionMismatch, match="extra"):
        reader.compare({"force_N": [2.0, 3.0]}, task_state={"phase": "move"})
    assert not reader.report()["original_continuation_verified"]
    reader.close()


@pytest.mark.parametrize("arguments", [
    ["--checkpoint-step", "0"], ["--steps", "80"], ["--mode", "reconstruct"],
    ["--release-at-checkpoint"], ["--live-speed", "nan"], ["--seed", "-1"],
])
def test_acceptance_cli_rejects_invalid_configuration_without_isaac(tmp_path, monkeypatch, arguments):
    pytest.importorskip("demo", reason="optional external Fire Rescue acceptance CLI is not bundled")
    from demo.fire_rescue.checks import reconstruct_physics

    monkeypatch.setattr("sys.argv", ["reconstruct_physics", "--output", str(tmp_path / "run"), *arguments])
    with pytest.raises(SystemExit) as error:
        reconstruct_physics.main()
    assert error.value.code == 2
    assert not (tmp_path / "run").exists()


def test_supervisor_does_not_accept_kit_exit_zero_after_worker_error(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    pytest.importorskip("demo", reason="optional external Fire Rescue acceptance CLI is not bundled")
    from demo.fire_rescue.checks import reconstruct_physics

    def run(command, **kwargs):
        directory = Path(command[command.index("--output") + 1])
        (directory / "run.json").write_text(json.dumps({"completed": False, "error": "simulated Kit failure"}))
        assert kwargs["env"]["PYTHONHASHSEED"] == "0"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(reconstruct_physics.subprocess, "run", run)
    args = reconstruct_physics.build_parser().parse_args(["--mode", "record", "--output", str(tmp_path / "run")])
    report = reconstruct_physics.run_check(args)
    assert not report["passed"]
    assert report["runs"][0]["error"] == "simulated Kit failure"
