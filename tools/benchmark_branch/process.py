"""Isolated subprocess attempts with conservative cross-process admission.

This module supervises operating-system processes only.  A successful process
receipt is not evidence that a simulator or physical evaluator succeeded.
Commands must remain in the foreground process group created here.  A child
that deliberately daemonizes with ``setsid``/``setpgid`` escapes this contract
and must be rejected by the caller rather than treated as supervised work.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Mapping, Sequence
from uuid import uuid4


RECEIPT_SCOPE = "process_termination_only"
ADMISSION_POLICY_SCHEMA = "eai.gpu-slot-policy.v1"
GROUP_KILL_WAIT_S = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class GpuAdmissionPolicy:
    """Declared slot policy; it does not probe or claim any physical GPU."""

    shared_directory: Path
    slots: int
    capacity_mb_per_slot: int
    request_mb: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "shared_directory", Path(self.shared_directory).resolve())
        for name in ("slots", "capacity_mb_per_slot", "request_mb"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ProcessAttempt:
    attempt_id: str
    directory: Path
    artifact_directory: Path
    stdout_path: Path
    stderr_path: Path
    receipt_path: Path


@dataclass(frozen=True)
class ProcessReceipt:
    """Terminal operating-system facts, never a physical-success verdict."""

    attempt_id: str
    argv: tuple[str, ...]
    state: str
    launched: bool
    process_terminal: bool
    process_group_terminal: bool
    group_cleanup_required: bool
    returncode: int | None
    timed_out: bool
    term_sent: bool
    kill_sent: bool
    pid: int | None
    process_group_id: int | None
    slot_index: int | None
    configured_slots: int | None
    configured_capacity_mb_per_slot: int | None
    requested_memory_mb: int | None
    started_at: str
    finished_at: str
    duration_s: float
    attempt_directory: str
    artifact_directory: str
    stdout_path: str
    stderr_path: str
    receipt_scope: str = RECEIPT_SCOPE
    physical_evaluator_success: None = field(default=None, init=False)

    def row(self) -> dict[str, object]:
        return asdict(self)


class ProcessSupervisorError(RuntimeError):
    """Base class for an attempt rejected before a terminal child result."""

    def __init__(self, message: str, receipt: ProcessReceipt):
        super().__init__(message)
        self.receipt = receipt


class GpuAdmissionUnavailable(ProcessSupervisorError):
    """No declared slot could admit the attempt without oversubscription."""


class ProcessLaunchError(ProcessSupervisorError):
    """The child process could not be created."""


class ProcessSupervisor:
    """Run each foreground argv in a fresh directory and process group."""

    def __init__(
        self,
        attempt_root: str | Path,
        *,
        admission_policy: GpuAdmissionPolicy | None = None,
        terminate_grace_s: float = 5.0,
    ) -> None:
        self.attempt_root = Path(attempt_root).resolve()
        self.admission_policy = admission_policy
        if isinstance(terminate_grace_s, bool):
            raise ValueError("terminate_grace_s must be non-negative")
        try:
            grace = float(terminate_grace_s)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("terminate_grace_s must be finite and non-negative") from exc
        if not math.isfinite(grace) or grace < 0:
            raise ValueError("terminate_grace_s must be finite and non-negative")
        self.terminate_grace_s = grace
        # An unkillable process group must keep its capacity reserved.  These
        # descriptors are intentionally retained until this supervisor process
        # exits; a successful cleanup always closes its descriptor immediately.
        self._retained_slot_fds: list[int] = []

    def _new_attempt(self) -> ProcessAttempt:
        self.attempt_root.mkdir(parents=True, exist_ok=True)
        while True:
            attempt_id = f"attempt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{os.getpid()}-{uuid4().hex}"
            directory = self.attempt_root / attempt_id
            try:
                directory.mkdir()
            except FileExistsError:
                continue
            artifacts = directory / "artifacts"
            artifacts.mkdir()
            return ProcessAttempt(
                attempt_id=attempt_id,
                directory=directory,
                artifact_directory=artifacts,
                stdout_path=directory / "stdout.log",
                stderr_path=directory / "stderr.log",
                receipt_path=directory / "process_receipt.json",
            )

    @staticmethod
    def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
        if isinstance(argv, (str, bytes)) or not argv:
            raise ValueError("argv must be a non-empty sequence of strings")
        result = tuple(argv)
        if any(not isinstance(value, str) or not value for value in result):
            raise ValueError("argv must contain only non-empty strings")
        return result

    def _policy_fields(self) -> tuple[int | None, int | None, int | None]:
        if self.admission_policy is None:
            return None, None, None
        return (
            self.admission_policy.slots,
            self.admission_policy.capacity_mb_per_slot,
            self.admission_policy.request_mb,
        )

    def _ensure_admission_policy(self) -> None:
        policy = self.admission_policy
        if policy is None:
            return
        policy.shared_directory.mkdir(parents=True, exist_ok=True)
        lock_path = policy.shared_directory / ".policy.lock"
        manifest_path = policy.shared_directory / "policy.json"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        expected = {
            "schema": ADMISSION_POLICY_SCHEMA,
            "slots": policy.slots,
            "capacity_mb_per_slot": policy.capacity_mb_per_slot,
        }
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if manifest_path.exists():
                try:
                    actual = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError("shared GPU admission policy is unreadable") from exc
                if actual != expected:
                    raise ValueError(
                        "shared GPU admission directory already has a different slot/capacity policy"
                    )
            else:
                with manifest_path.open("x", encoding="utf-8") as stream:
                    json.dump(expected, stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
        finally:
            os.close(lock_fd)

    def _acquire_slot(self, attempt: ProcessAttempt) -> tuple[int | None, int | None]:
        policy = self.admission_policy
        if policy is None:
            return None, None
        policy.shared_directory.mkdir(parents=True, exist_ok=True)
        if policy.request_mb > policy.capacity_mb_per_slot:
            return None, None
        for slot in range(policy.slots):
            path = policy.shared_directory / f"slot-{slot:04d}.lock"
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            reservation = {
                "attempt_id": attempt.attempt_id,
                "supervisor_pid": os.getpid(),
                "slot_index": slot,
                "configured_capacity_mb": policy.capacity_mb_per_slot,
                "requested_memory_mb": policy.request_mb,
            }
            payload = (json.dumps(reservation, sort_keys=True) + "\n").encode("utf-8")
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, payload)
            os.fsync(fd)
            return slot, fd
        return None, None

    @staticmethod
    def _group_alive(process_group_id: int) -> bool:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _wait_group_gone(
        cls, process: subprocess.Popen[bytes], process_group_id: int, timeout_s: float
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            process.poll()  # Reap the group leader as soon as it exits.
            if not cls._group_alive(process_group_id):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    @classmethod
    def _signal_group(cls, process_group_id: int, signum: int) -> bool:
        if not cls._group_alive(process_group_id):
            return False
        try:
            os.killpg(process_group_id, signum)
        except ProcessLookupError:
            return False
        return True

    def _clean_process_group(
        self, process: subprocess.Popen[bytes]
    ) -> tuple[bool, bool, bool]:
        process_group_id = process.pid
        term_sent = self._signal_group(process_group_id, signal.SIGTERM)
        if not term_sent:
            process.poll()
            return False, False, not self._group_alive(process_group_id)
        if self._wait_group_gone(process, process_group_id, self.terminate_grace_s):
            return True, False, True
        kill_sent = self._signal_group(process_group_id, signal.SIGKILL)
        terminal = self._wait_group_gone(process, process_group_id, GROUP_KILL_WAIT_S)
        return True, kill_sent, terminal

    @staticmethod
    def _write_receipt(attempt: ProcessAttempt, receipt: ProcessReceipt) -> None:
        with attempt.receipt_path.open("x", encoding="utf-8") as stream:
            json.dump(receipt.row(), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _receipt(
        self,
        attempt: ProcessAttempt,
        argv: tuple[str, ...],
        *,
        state: str,
        launched: bool,
        process_terminal: bool,
        process_group_terminal: bool,
        group_cleanup_required: bool,
        returncode: int | None,
        timed_out: bool,
        term_sent: bool,
        kill_sent: bool,
        pid: int | None,
        slot_index: int | None,
        started_at: str,
        started_clock: float,
    ) -> ProcessReceipt:
        slots, capacity, request = self._policy_fields()
        return ProcessReceipt(
            attempt_id=attempt.attempt_id,
            argv=argv,
            state=state,
            launched=launched,
            process_terminal=process_terminal,
            process_group_terminal=process_group_terminal,
            group_cleanup_required=group_cleanup_required,
            returncode=returncode,
            timed_out=timed_out,
            term_sent=term_sent,
            kill_sent=kill_sent,
            pid=pid,
            process_group_id=pid if launched else None,
            slot_index=slot_index,
            configured_slots=slots,
            configured_capacity_mb_per_slot=capacity,
            requested_memory_mb=request,
            started_at=started_at,
            finished_at=_now(),
            duration_s=max(0.0, time.monotonic() - started_clock),
            attempt_directory=str(attempt.directory),
            artifact_directory=str(attempt.artifact_directory),
            stdout_path=str(attempt.stdout_path),
            stderr_path=str(attempt.stderr_path),
        )

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float | None = None,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ProcessReceipt:
        command = self._validate_argv(argv)
        timeout = None
        if timeout_s is not None:
            if isinstance(timeout_s, bool):
                raise ValueError("timeout_s must be finite and positive when provided")
            try:
                timeout = float(timeout_s)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("timeout_s must be finite and positive when provided") from exc
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeout_s must be finite and positive when provided")
        if env is not None and any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in env.items()
        ):
            raise ValueError("env must map strings to strings")
        self._ensure_admission_policy()
        attempt = self._new_attempt()
        started_at = _now()
        started_clock = time.monotonic()
        slot_index, slot_fd = self._acquire_slot(attempt)
        if self.admission_policy is not None and slot_fd is None:
            attempt.stdout_path.touch(exist_ok=False)
            attempt.stderr_path.touch(exist_ok=False)
            receipt = self._receipt(
                attempt, command, state="admission_denied", launched=False,
                process_terminal=False, process_group_terminal=False,
                group_cleanup_required=False, returncode=None, timed_out=False,
                term_sent=False, kill_sent=False, pid=None, slot_index=None,
                started_at=started_at, started_clock=started_clock,
            )
            self._write_receipt(attempt, receipt)
            raise GpuAdmissionUnavailable(
                "no configured GPU slot has sufficient declared capacity", receipt
            )

        child_env = os.environ.copy()
        if env is not None:
            child_env.update(env)
        child_env["EAI_PROCESS_ATTEMPT_DIR"] = str(attempt.directory)
        child_env["EAI_PROCESS_ARTIFACT_DIR"] = str(attempt.artifact_directory)
        if slot_index is not None:
            child_env["EAI_GPU_SLOT_INDEX"] = str(slot_index)

        process: subprocess.Popen[bytes] | None = None
        try:
            with attempt.stdout_path.open("xb") as stdout, attempt.stderr_path.open("xb") as stderr:
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=None if cwd is None else Path(cwd),
                        env=child_env,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        shell=False,
                        start_new_session=True,
                        pass_fds=() if slot_fd is None else (slot_fd,),
                    )
                except (OSError, ValueError) as exc:
                    receipt = self._receipt(
                        attempt, command, state="launch_failed", launched=False,
                        process_terminal=False, process_group_terminal=False,
                        group_cleanup_required=False, returncode=None, timed_out=False,
                        term_sent=False, kill_sent=False, pid=None, slot_index=slot_index,
                        started_at=started_at, started_clock=started_clock,
                    )
                    self._write_receipt(attempt, receipt)
                    raise ProcessLaunchError(str(exc), receipt) from exc

                try:
                    if slot_fd is not None:
                        reservation = {
                            "attempt_id": attempt.attempt_id,
                            "child_pid": process.pid,
                            "process_group_id": process.pid,
                            "slot_index": slot_index,
                            "configured_capacity_mb": self.admission_policy.capacity_mb_per_slot,
                            "requested_memory_mb": self.admission_policy.request_mb,
                        }
                        os.ftruncate(slot_fd, 0)
                        os.lseek(slot_fd, 0, os.SEEK_SET)
                        os.write(
                            slot_fd,
                            (json.dumps(reservation, sort_keys=True) + "\n").encode("utf-8"),
                        )
                        os.fsync(slot_fd)
                        # Parent and child both retain this locked open-file
                        # description. The child protects a supervisor crash;
                        # the parent protects descendants that close inherited
                        # descriptors before their work is terminal.

                    timed_out = False
                    term_sent = False
                    kill_sent = False
                    try:
                        returncode = process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        term_sent, kill_sent, group_terminal = self._clean_process_group(process)
                    else:
                        group_terminal = not self._group_alive(process.pid)
                        if not group_terminal:
                            term_sent, kill_sent, group_terminal = self._clean_process_group(process)
                    returncode = process.poll()
                except BaseException:
                    try:
                        _, _, emergency_terminal = self._clean_process_group(process)
                    except BaseException:
                        emergency_terminal = False
                    if not emergency_terminal and slot_fd is not None:
                        self._retained_slot_fds.append(slot_fd)
                        slot_fd = None
                    raise

                if not group_terminal and slot_fd is not None:
                    self._retained_slot_fds.append(slot_fd)
                    slot_fd = None
        finally:
            if slot_fd is not None:
                os.close(slot_fd)

        process_terminal = process.poll() is not None and group_terminal
        if not group_terminal:
            state = "group_cleanup_failed"
        elif timed_out:
            state = "timed_out"
        elif term_sent:
            state = "exited_group_cleaned"
        else:
            state = "exited"
        receipt = self._receipt(
            attempt, command, state=state,
            launched=True, process_terminal=process_terminal,
            process_group_terminal=group_terminal,
            group_cleanup_required=term_sent, returncode=returncode,
            timed_out=timed_out, term_sent=term_sent, kill_sent=kill_sent,
            pid=process.pid, slot_index=slot_index,
            started_at=started_at, started_clock=started_clock,
        )
        self._write_receipt(attempt, receipt)
        return receipt
