"""Single-session Isaac integration through the public EAI launcher.

Task packages inject a branch driver and an independent evaluator. The driver
owns restore/readback, controller commands and tick/heartbeat receipts; the
evaluator owns domain predicates. This backend never creates a task or skill.
Kit is main-thread-only: use separate processes for concurrent Isaac sessions.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import threading
from typing import Any


class EaiSessionBackend:
    execution_affinity = "caller_thread"

    def __init__(self, *, scene_id: str, launch_config: Any, branch_driver: Any,
                 evaluator: Any, run_dir: str | Path, session_opener=None):
        self.scene_id = scene_id
        self.launch_config = launch_config
        self.driver = branch_driver
        self.evaluator = evaluator
        self.run_dir = Path(run_dir).resolve()
        self._opener = session_opener
        self._context = None
        self._session = None
        self._runtime = None

    def _check_thread(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Isaac lifecycle and stepping require the main thread")

    def open_scene(self, scene_id, *, scheduler):
        self._check_thread()
        if scene_id != self.scene_id or self._context is not None:
            raise ValueError("EaiSessionBackend owns exactly one selected scene/session")
        opener = self._opener
        if opener is None:
            from simulator import open_simulator_session
            opener = open_simulator_session
            # Kit's fast shutdown can terminate Python before the collector
            # releases leases and seals ledgers. Reusable callers need control
            # to return after the session context closes.
            self.launch_config = replace(self.launch_config, app_launcher_args={
                **self.launch_config.app_launcher_args, "fast_shutdown": False})
        context = opener(self.launch_config)
        self._session = context.__enter__()
        self._context = context
        return self._session

    def warmup(self, session, *, ticks):
        self._check_thread()
        # No generic zero command: action shapes and idle policy belong to
        # the selected controller/task driver.
        self.driver.warmup(session, ticks=ticks)

    def open_branch(self, session, job, *, lease):
        self._check_thread()
        if self._runtime is not None:
            raise RuntimeError("concurrent mutable Isaac branch runtimes are unsupported")
        self._runtime = self.driver.open_branch(session, job, lease=lease)
        return self._runtime

    def run_branch(self, branch_runtime, job, *, emit):
        self._check_thread()
        execution = self.driver.run_branch(branch_runtime, job, emit=emit)
        # Do not trust a driver's physical_success flag. Only the injected
        # evaluator may supply the verdict and branch-bound evidence artifact.
        verdict = self.evaluator.evaluate(
            branch_runtime, job, execution=execution, run_dir=self.run_dir)
        receipt = {key: execution[key] for key in
                   ("first_tick", "last_tick", "restore_passed", "heartbeat")
                   if key in execution}
        return {**receipt, **verdict}

    def close_branch(self, runtime):
        self._check_thread()
        try:
            self.driver.close_branch(runtime)
        finally:
            self._runtime = None

    def close_scene(self, session):
        self._check_thread()
        context, self._context = self._context, None
        self._session = None
        if context is not None:
            context.__exit__(None, None, None)


def probe_eai_devices(session, request):
    """Observe the live physics device, controller tensor and CUDA UUID.

    The controller tensor is required from the task driver via the session's
    ``benchmark_action_tensor`` attribute. A requested/configured device is
    never substituted when the actual action tensor is unavailable.
    """
    import torch

    sim = session.base_env.sim
    context = sim.get_physics_context()
    actual = str(context.device)
    if actual.startswith("cuda") and not (
        context.is_gpu_dynamics_enabled() and context.use_gpu_pipeline
    ):
        actual = "cpu"
    action = getattr(session, "benchmark_action_tensor", None)
    controller = str(action.device) if isinstance(action, torch.Tensor) else ""
    gpu_uuid = ""
    if actual.startswith("cuda"):
        props = torch.cuda.get_device_properties(torch.device(actual))
        gpu_uuid = str(getattr(props, "uuid", "") or "")
    return {"actual_physics_device": actual, "controller_device": controller,
            "gpu_uuid": gpu_uuid}


class CudaResourceProbe:
    """Sample the selected CUDA device, respecting CUDA_VISIBLE_DEVICES.

    Pair with scheduler reservations; free memory sampling is not a cross-
    process reservation service. Missing telemetry remains missing.
    """

    def __init__(self, device: str):
        self.device = device

    def sample(self):
        import torch

        device = torch.device(self.device)
        free, total = torch.cuda.mem_get_info(device)
        props = torch.cuda.get_device_properties(device)
        host_available = None
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                host_available = int(line.split()[1]) // 1024
                break
        return {"gpu_uuid": str(getattr(props, "uuid", "") or ""),
                "gpu_free_memory_mb": free // (1024 * 1024),
                "gpu_total_memory_mb": total // (1024 * 1024),
                "host_available_memory_mb": host_available}
