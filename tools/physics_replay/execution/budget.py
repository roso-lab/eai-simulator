"""Linux resource observations and conservative admission for isolated workers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
import fcntl
from hashlib import sha256
import math
import os
from pathlib import Path
import subprocess

from tools.physics_replay.execution.memory_profile import read_kib_fields


GIB = 1024 ** 3
MIB = 1024 ** 2


def positive(value, name):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


@dataclass(frozen=True)
class Reservation:
    ram: int
    vram: int

    def __post_init__(self):
        positive(self.ram, "reserved RAM")
        positive(self.vram, "reserved VRAM")


@dataclass(frozen=True)
class Budget:
    ram: int = 12 * GIB
    vram: int = 16 * GIB
    host_headroom: int = 6 * GIB
    gpu_headroom: int = 4 * GIB

    def __post_init__(self):
        for name, value in asdict(self).items():
            positive(value, name)

    def admission(self, sample, active, reservation):
        """Reserve future growth as well as memory already allocated by workers."""
        reserved_ram = sum(item.ram for item in active.values()) + reservation.ram
        reserved_vram = sum(item.vram for item in active.values()) + reservation.vram
        if reserved_ram > self.ram or reserved_vram > self.vram:
            return "reservation exceeds experiment budget"
        pending_ram = reservation.ram
        pending_vram = reservation.vram
        for pid, reserved in active.items():
            measured = sample["workers"].get(str(pid), {})
            pending_ram += max(0, reserved.ram - measured.get("rss", 0))
            pending_vram += max(0, reserved.vram - measured.get("vram", 0))
        if sample["host_available"] < self.host_headroom + pending_ram:
            return "insufficient host headroom for reserved growth"
        if sample["gpu_free"] < self.gpu_headroom + pending_vram:
            return "insufficient GPU headroom for reserved growth"
        return None

    def worker_ceiling(self, sample, reservation):
        """Return the conservative number of reservations that fit before startup."""
        ram_capacity = self.ram // reservation.ram
        vram_capacity = self.vram // reservation.vram
        host_capacity = max(0, (sample["host_available"] - self.host_headroom) // reservation.ram)
        gpu_capacity = max(0, (sample["gpu_free"] - self.gpu_headroom) // reservation.vram)
        return max(0, int(min(ram_capacity, vram_capacity, host_capacity, gpu_capacity)))

    def violation(self, sample, active, *, enforce_worker_limits=True):
        if sample["host_available"] < self.host_headroom:
            return "host headroom exhausted"
        if sample["gpu_free"] < self.gpu_headroom:
            return "GPU headroom exhausted"
        if not enforce_worker_limits:
            return None
        for pid, reservation in active.items():
            measured = sample["workers"].get(str(pid), {})
            if max(measured.get("rss", 0), measured.get("hwm", 0)) > reservation.ram:
                return f"worker {pid} exceeded RAM reservation"
            if measured.get("vram", 0) > reservation.vram:
                return f"worker {pid} exceeded VRAM reservation"
        return None


def process_members(records, root):
    members = {root}
    while True:
        children = {pid for pid, record in records.items()
                    if record["parent"] in members or record["group"] == root}
        expanded = members | children
        if expanded == members:
            return members & records.keys()
        members = expanded


class LinuxResourceProbe:
    """Sample process trees and GPU allocations; unavailable telemetry is an error."""

    def __init__(self, gpu_uuid=None, proc=Path("/proc"), command=subprocess.check_output):
        self.gpu_uuid = gpu_uuid
        self.proc = proc
        self.command = command

    def nvidia(self, query):
        command = ["nvidia-smi", query, "--format=csv,noheader,nounits"]
        try:
            output = self.command(command, text=True, timeout=10, stderr=subprocess.PIPE)
        except FileNotFoundError as exc:
            raise RuntimeError("GPU telemetry unavailable: nvidia-smi was not found") from exc
        except PermissionError as exc:
            raise RuntimeError("GPU telemetry unavailable: nvidia-smi permission was denied") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("GPU telemetry unavailable: nvidia-smi timed out") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip().replace("\n", " ")
            suffix = f"; {detail}" if detail else ""
            raise RuntimeError(
                f"GPU telemetry unavailable: nvidia-smi exited with status {exc.returncode}{suffix}"
            ) from exc
        return [[field.strip() for field in line.split(",")] for line in output.splitlines() if line.strip()]

    def sample(self, roots=()):
        memory = read_kib_fields(self.proc / "meminfo")
        cards = self.nvidia("--query-gpu=uuid,memory.total,memory.used,memory.free")
        if len(cards) != 1:
            raise ValueError("this backend requires one GPU; multi-GPU Kit renderer affinity is not validated")
        if self.gpu_uuid is None:
            self.gpu_uuid = cards[0][0]
        selected = [card for card in cards if card[0] == self.gpu_uuid]
        if len(selected) != 1:
            raise ValueError("selected GPU is unavailable")
        card = selected[0]
        allocations = {}
        for uuid, pid, used in self.nvidia("--query-compute-apps=gpu_uuid,pid,used_gpu_memory"):
            if uuid == self.gpu_uuid:
                allocations[int(pid)] = int(used) * MIB
        records = {}
        for directory in self.proc.iterdir():
            if not directory.name.isdecimal():
                continue
            try:
                fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
                records[int(directory.name)] = {"parent": int(fields[1]), "group": int(fields[2])}
            except (FileNotFoundError, ProcessLookupError):
                continue
        workers = {}
        for root in roots:
            members = process_members(records, root)
            measured = {"rss": 0, "pss": 0, "hwm": 0, "vram": 0, "pids": sorted(members)}
            for pid in members:
                directory = self.proc / str(pid)
                try:
                    status = read_kib_fields(directory / "status")
                    measured["rss"] += status.get("VmRSS", 0)
                    measured["hwm"] += status.get("VmHWM", 0)
                    try:
                        measured["pss"] += read_kib_fields(directory / "smaps_rollup").get("Pss", 0)
                    except (PermissionError, FileNotFoundError, ProcessLookupError):
                        measured["pss"] += status.get("VmRSS", 0)
                    measured["vram"] += allocations.get(pid, 0)
                except (FileNotFoundError, ProcessLookupError):
                    continue
            workers[str(root)] = measured
        vmstat = dict(line.split() for line in (self.proc / "vmstat").read_text().splitlines())
        process_vram = sum(allocations.values())
        return {"host_total": memory["MemTotal"], "host_available": memory["MemAvailable"],
                "gpu_uuid": self.gpu_uuid, "gpu_total": int(card[1]) * MIB,
                "gpu_used": int(card[2]) * MIB, "gpu_free": int(card[3]) * MIB,
                "gpu_process_vram": process_vram,
                "gpu_vram_accounting_scope": "compute applications; device total is authoritative",
                "system_oom_kill": int(vmstat["oom_kill"]), "workers": workers}


def process_identity():
    return {"hostname": os.uname().nodename, "machine": os.uname().machine,
            "logical_cpus": os.cpu_count()}


@contextmanager
def gpu_lease(gpu_uuid, directory=Path("/tmp")):
    """Prevent this user's supervisors from racing independent reservations."""
    key = sha256(gpu_uuid.encode()).hexdigest()
    path = directory / f"eai-replay-{os.getuid()}-{key}.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another budgeted supervisor owns this GPU; use one admission queue") from exc
        yield
    finally:
        os.close(descriptor)
