"""Best-effort host resource observations for audit ledgers.

The probe never turns unavailable telemetry into a capacity claim.  Missing
GPU fields are represented as ``None`` and the run remains synthetic or
pending until a real adapter supplies measured evidence.
"""

from __future__ import annotations

from dataclasses import asdict
import os
import shutil
import subprocess
import time
from typing import Any, Mapping

from .models import ResourceSnapshot


class ResourceProbe:
    def __init__(self, *, gpu_sample_interval_s: float = 1.0) -> None:
        self.gpu_sample_interval_s = max(0.0, float(gpu_sample_interval_s))
        self._last_gpu_sample_s = -float("inf")
        self._cached_vram: tuple[int | None, int | None] = (None, None)
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._last_cpu_ticks: tuple[int, int] | None = None

    def sample(self, *, active: int = 0, worker_pids: tuple[int, ...] = ()) -> ResourceSnapshot:
        available = total = None
        try:
            fields = {}
            for line in open("/proc/meminfo", encoding="ascii"):
                key, value = line.split(":", 1)
                fields[key] = int(value.strip().split()[0]) * 1024
            total, available = fields.get("MemTotal"), fields.get("MemAvailable")
        except (OSError, ValueError):
            pass
        cpu_percent = self._sample_cpu_percent()
        now = time.monotonic()
        if self._nvidia_smi and now - self._last_gpu_sample_s >= self.gpu_sample_interval_s:
            self._cached_vram = self._query_vram()
            self._last_gpu_sample_s = now
        vram_free, vram_total = self._cached_vram
        return ResourceSnapshot(timestamp_ns=time.time_ns(), host_pid=os.getpid(),
                                cpu_percent=cpu_percent,
                                ram_available_bytes=available, ram_total_bytes=total,
                                vram_free_bytes=vram_free, vram_total_bytes=vram_total,
                                worker_pids=tuple(worker_pids), observed_active=int(active))

    def _sample_cpu_percent(self) -> float | None:
        """Sample aggregate host CPU utilization from ``/proc/stat``.

        The first sample has no interval and therefore returns ``None``;
        subsequent samples use the delta of total versus idle ticks.  This is
        deliberately best-effort so the tool remains portable to hosts
        without procfs.
        """

        try:
            with open("/proc/stat", encoding="ascii") as stream:
                line = next(line for line in stream if line.startswith("cpu "))
            values = [int(item) for item in line.split()[1:]]
            if len(values) < 4:
                return None
            total = sum(values)
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            previous = self._last_cpu_ticks
            self._last_cpu_ticks = (total, idle)
            if previous is None:
                return None
            total_delta = total - previous[0]
            idle_delta = idle - previous[1]
            if total_delta <= 0:
                return None
            return max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta)))
        except (OSError, StopIteration, ValueError):
            return None

    def _query_vram(self) -> tuple[int | None, int | None]:
        """Read GPU 0 telemetry once per interval without making it a gate."""

        if not self._nvidia_smi:
            return None, None
        try:
            output = subprocess.check_output(
                [self._nvidia_smi, "--query-gpu=memory.free,memory.total",
                 "--format=csv,noheader,nounits"],
                text=True, stderr=subprocess.DEVNULL, timeout=1.0,
            ).splitlines()
            free, total = (int(item.strip()) * 1024 * 1024 for item in output[0].split(",", 1))
            return free, total
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return None, None

    def admission(self, requested: int, limits: Mapping[str, Any] | None = None) -> tuple[int, str]:
        """Return a conservative worker admission and an auditable reason.

        Limits are opt-in.  Without a configured per-worker budget the probe
        remains observational, so a synthetic 32-worker test is never reduced
        merely because telemetry is unavailable.
        """

        limits = dict(limits or {})
        admitted = max(1, int(requested))
        reasons: list[str] = []
        if limits.get("max_workers") is not None:
            ceiling = max(1, int(limits["max_workers"]))
            if ceiling < admitted:
                admitted = ceiling
                reasons.append("configured max_workers")
        snapshot = self.sample()
        min_ram = int(limits.get("min_ram_available_bytes", 0) or 0)
        per_ram = int(limits.get("per_worker_ram_bytes", 0) or 0)
        if per_ram and snapshot.ram_available_bytes is not None:
            budget = max(0, snapshot.ram_available_bytes - min_ram)
            ceiling = max(1, budget // per_ram)
            if ceiling < admitted:
                admitted = ceiling
                reasons.append("host RAM budget")
        min_vram = int(limits.get("min_vram_free_bytes", 0) or 0)
        per_vram = int(limits.get("per_worker_vram_bytes", 0) or 0)
        if per_vram and snapshot.vram_free_bytes is not None:
            budget = max(0, snapshot.vram_free_bytes - min_vram)
            ceiling = max(1, budget // per_vram)
            if ceiling < admitted:
                admitted = ceiling
                reasons.append("GPU VRAM budget")
        return admitted, "; ".join(reasons)


def resource_row(probe: ResourceProbe | None = None, *, active: int = 0,
                 worker_pids: tuple[int, ...] = ()) -> Mapping[str, Any]:
    return asdict((probe or ResourceProbe()).sample(active=active, worker_pids=worker_pids))


__all__ = ["ResourceProbe", "resource_row"]
