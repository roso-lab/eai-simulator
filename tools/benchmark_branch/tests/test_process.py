"""Real lightweight subprocess checks for isolated process supervision."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tools.benchmark_branch.process import (
    GpuAdmissionPolicy,
    GpuAdmissionUnavailable,
    ProcessSupervisor,
)


def _wait_for(paths, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(Path(path).exists() for path in paths):
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {paths}")


def test_attempts_preserve_output_artifacts_and_literal_argv(tmp_path):
    supervisor = ProcessSupervisor(tmp_path / "attempts")
    literal = "literal; echo shell-was-not-used"
    code = (
        "import os, pathlib, sys; "
        "print(sys.argv[1]); print('stderr-record', file=sys.stderr); "
        "pathlib.Path(os.environ['EAI_PROCESS_ARTIFACT_DIR'], 'result.txt').write_text(sys.argv[2])"
    )
    first = supervisor.run([sys.executable, "-c", code, literal, "first"])
    first_stdout = Path(first.stdout_path).read_text()
    first_stderr = Path(first.stderr_path).read_text()
    first_artifact = Path(first.artifact_directory) / "result.txt"

    second = supervisor.run([sys.executable, "-c", code, literal, "second"])

    assert first.attempt_id != second.attempt_id
    assert Path(first.attempt_directory) != Path(second.attempt_directory)
    assert first_stdout == literal + "\n"
    assert first_stderr == "stderr-record\n"
    assert first_artifact.read_text() == "first"
    assert (Path(second.artifact_directory) / "result.txt").read_text() == "second"
    assert Path(first.stdout_path).read_text() == first_stdout
    assert Path(first.stderr_path).read_text() == first_stderr
    assert first.state == "exited" and first.returncode == 0
    assert first.process_terminal is True
    assert first.process_group_terminal is True
    assert first.receipt_scope == "process_termination_only"
    assert first.physical_evaluator_success is None
    persisted = json.loads((Path(first.attempt_directory) / "process_receipt.json").read_text())
    assert persisted["receipt_scope"] == "process_termination_only"
    assert persisted["physical_evaluator_success"] is None


def test_timeout_escalates_term_then_kill_for_the_process_group(tmp_path):
    escaped = tmp_path / "escaped.txt"
    grandchild = (
        "import pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('escaped')"
    )
    parent = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
        "print('started', flush=True); time.sleep(30)"
    )
    receipt = ProcessSupervisor(tmp_path / "attempts", terminate_grace_s=0.05).run(
        [sys.executable, "-c", parent, grandchild, str(escaped)], timeout_s=0.15
    )

    assert receipt.state == "timed_out"
    assert receipt.timed_out is True
    assert receipt.term_sent is True
    assert receipt.kill_sent is True
    # The leader accepts TERM; its child ignores TERM. Escalation must use
    # process-group liveness rather than mistaking the leader exit for cleanup.
    assert receipt.returncode == -signal.SIGTERM
    assert receipt.process_group_terminal is True
    assert Path(receipt.stdout_path).read_text() == "started\n"
    time.sleep(0.9)
    assert not escaped.exists()


def test_normal_leader_exit_cleans_remaining_process_group(tmp_path):
    escaped = tmp_path / "escaped-after-parent-exit"
    ready = tmp_path / "grandchild-ready"
    grandchild = (
        "import pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[2]).write_text('ready'); "
        "time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('escaped')"
    )
    parent = (
        "import pathlib, subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]]); "
        "ready=pathlib.Path(sys.argv[3]); "
        "[(time.sleep(0.01)) for _ in range(200) if not ready.exists()]; "
        "print('leader done', flush=True)"
    )
    receipt = ProcessSupervisor(tmp_path / "attempts", terminate_grace_s=0.05).run(
        [sys.executable, "-c", parent, grandchild, str(escaped), str(ready)], timeout_s=2
    )

    assert receipt.state == "exited_group_cleaned"
    assert receipt.returncode == 0
    assert receipt.timed_out is False
    assert receipt.term_sent is True and receipt.kill_sent is True
    assert receipt.process_group_terminal is True
    time.sleep(0.9)
    assert not escaped.exists()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_timeout_and_grace_are_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="finite"):
        ProcessSupervisor(tmp_path / "attempts", terminate_grace_s=value)
    supervisor = ProcessSupervisor(tmp_path / "attempts")
    with pytest.raises(ValueError, match="finite"):
        supervisor.run([sys.executable, "-c", "pass"], timeout_s=value)
    assert not (tmp_path / "attempts").exists()


@pytest.mark.parametrize("slots", [1, 2, 4])
def test_declared_slot_capacity_is_enforced_without_oversubscription(tmp_path, slots):
    admission = tmp_path / "admission"
    attempts = tmp_path / "attempts"
    release = tmp_path / "release"
    ready = [tmp_path / f"ready-{index}" for index in range(slots)]
    child = """\
import pathlib
import sys
import time
pathlib.Path(sys.argv[1]).write_text('ready')
release = pathlib.Path(sys.argv[2])
while not release.exists():
    time.sleep(0.01)
"""
    policy = GpuAdmissionPolicy(admission, slots, capacity_mb_per_slot=1024, request_mb=1024)

    def occupy(path):
        return ProcessSupervisor(attempts, admission_policy=policy).run(
            [sys.executable, "-c", child, str(path), str(release)], timeout_s=5
        )

    with ThreadPoolExecutor(max_workers=slots) as pool:
        futures = [pool.submit(occupy, path) for path in ready]
        _wait_for(ready)
        denied = ProcessSupervisor(attempts, admission_policy=policy)
        with pytest.raises(GpuAdmissionUnavailable) as error:
            denied.run([sys.executable, "-c", "raise SystemExit('must not start')"])
        assert error.value.receipt.state == "admission_denied"
        assert error.value.receipt.launched is False
        assert error.value.receipt.process_terminal is False
        release.write_text("release")
        receipts = [future.result(timeout=5) for future in futures]

    assert {receipt.slot_index for receipt in receipts} == set(range(slots))
    assert all(receipt.returncode == 0 for receipt in receipts)


def test_memory_policy_denies_before_launch(tmp_path):
    marker = tmp_path / "launched"
    policy = GpuAdmissionPolicy(
        tmp_path / "admission", slots=2, capacity_mb_per_slot=512, request_mb=513
    )
    with pytest.raises(GpuAdmissionUnavailable) as error:
        ProcessSupervisor(tmp_path / "attempts", admission_policy=policy).run(
            [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker)]
        )
    assert not marker.exists()
    assert error.value.receipt.requested_memory_mb == 513
    assert error.value.receipt.configured_capacity_mb_per_slot == 512


def test_shared_admission_directory_pins_policy_and_metadata_is_valid_json(tmp_path):
    admission = tmp_path / "admission"
    attempts = tmp_path / "attempts"
    policy = GpuAdmissionPolicy(admission, slots=1, capacity_mb_per_slot=512, request_mb=128)
    receipt = ProcessSupervisor(attempts, admission_policy=policy).run(
        [sys.executable, "-c", "pass"]
    )
    reservation = json.loads((admission / "slot-0000.lock").read_text())
    assert reservation["child_pid"] == receipt.pid
    assert json.loads((admission / "policy.json").read_text()) == {
        "schema": "eai.gpu-slot-policy.v1",
        "slots": 1,
        "capacity_mb_per_slot": 512,
    }

    for changed in (
        GpuAdmissionPolicy(admission, slots=2, capacity_mb_per_slot=512, request_mb=128),
        GpuAdmissionPolicy(admission, slots=1, capacity_mb_per_slot=1024, request_mb=128),
    ):
        with pytest.raises(ValueError, match="different slot/capacity policy"):
            ProcessSupervisor(attempts, admission_policy=changed).run(
                [sys.executable, "-c", "raise SystemExit('must not launch')"]
            )

    # Per-attempt requests may differ without redefining shared capacity.
    compatible = GpuAdmissionPolicy(admission, slots=1, capacity_mb_per_slot=512, request_mb=256)
    assert ProcessSupervisor(attempts, admission_policy=compatible).run(
        [sys.executable, "-c", "pass"]
    ).returncode == 0


def test_parent_holds_slot_when_child_closes_inherited_descriptors(tmp_path):
    admission = tmp_path / "admission"
    attempts = tmp_path / "attempts"
    ready = tmp_path / "closed-fds"
    policy = GpuAdmissionPolicy(admission, 1, 64, 64)
    child = """\
import os
import pathlib
import sys
import time
for fd in range(3, 256):
    try:
        os.close(fd)
    except OSError:
        pass
pathlib.Path(sys.argv[1]).write_text('ready')
time.sleep(0.6)
"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            ProcessSupervisor(attempts, admission_policy=policy).run,
            [sys.executable, "-c", child, str(ready)],
        )
        _wait_for([ready])
        with pytest.raises(GpuAdmissionUnavailable):
            ProcessSupervisor(attempts, admission_policy=policy).run(
                [sys.executable, "-c", "raise SystemExit('oversubscribed')"]
            )
        assert future.result(timeout=3).returncode == 0


def test_parent_crash_keeps_child_lease_and_recovery_uses_new_attempt(tmp_path):
    attempts = tmp_path / "attempts"
    admission = tmp_path / "admission"
    marker = tmp_path / "child-pid"
    done = tmp_path / "child-done"
    child = (
        "import os, pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(1.0); pathlib.Path(sys.argv[2]).write_text('done')"
    )
    wrapper = (
        "import pathlib, sys; "
        "from tools.benchmark_branch.process import GpuAdmissionPolicy, ProcessSupervisor; "
        "policy=GpuAdmissionPolicy(pathlib.Path(sys.argv[1]),1,64,64); "
        "ProcessSupervisor(pathlib.Path(sys.argv[2]), admission_policy=policy).run("
        "[sys.executable,'-c',sys.argv[3],sys.argv[4],sys.argv[5]],timeout_s=5)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", wrapper, str(admission), str(attempts), child, str(marker), str(done)],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for([marker])
        child_pid = int(marker.read_text())
        parent.kill()
        parent.wait(timeout=5)
        assert parent.returncode == -signal.SIGKILL
        os.kill(child_pid, 0)

        policy = GpuAdmissionPolicy(admission, 1, 64, 64)
        with pytest.raises(GpuAdmissionUnavailable):
            ProcessSupervisor(attempts, admission_policy=policy).run(
                [sys.executable, "-c", "raise SystemExit('capacity was released early')"]
            )

        _wait_for([done])
        time.sleep(0.1)
        recovered = ProcessSupervisor(attempts, admission_policy=policy).run(
            [sys.executable, "-c", "print('fresh attempt')"], timeout_s=2
        )
        assert recovered.returncode == 0
        assert Path(recovered.stdout_path).read_text() == "fresh attempt\n"
        directories = [path for path in attempts.iterdir() if path.is_dir()]
        assert len(directories) >= 3
        assert len({path.name for path in directories}) == len(directories)
        assert any(not (path / "process_receipt.json").exists() for path in directories)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
