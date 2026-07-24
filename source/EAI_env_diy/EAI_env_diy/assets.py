"""Threaded, non-interactive asset downloads for the Env DIY extension."""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Callable, Iterable

from EAI_assets import asset_resolver


@dataclass(frozen=True)
class _DownloadJob:
    requirement: object
    callback: Callable[[object], None] | None


class AssetDownloadManager:
    """Serialize network/file work and hand all UI callbacks to Kit's thread."""

    def __init__(
        self,
        *,
        resolver=asset_resolver,
        dispatch_to_kit: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self.resolver = resolver
        self._dispatch = dispatch_to_kit or (lambda callback: callback())
        self._queue: queue.Queue[_DownloadJob | None] = queue.Queue()
        self._statuses: dict[str, object] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="eai-env-diy-assets", daemon=True)
        self._worker.start()

    def status(self, requirement_id: str):
        with self._lock:
            return self._statuses.get(str(requirement_id))

    def inspect(self, requirement):
        status = self.resolver.inspect_requirement(requirement)
        with self._lock:
            self._statuses[requirement.id] = status
        return status

    def submit(self, requirement, callback: Callable[[object], None] | None = None):
        with self._lock:
            if self._closed:
                raise RuntimeError("Asset download manager is closed.")
            status = self.resolver.inspect_requirement(requirement)
            self._statuses[requirement.id] = status
            if status.state == self.resolver.RequirementState.READY:
                self._deliver(status, callback)
                return status
            downloading = self.resolver.AssetStatus(
                requirement,
                self.resolver.RequirementState.DOWNLOADING,
                status.missing_paths,
                f"Downloading {getattr(requirement, 'label', requirement.id)}...",
            )
            self._statuses[requirement.id] = downloading
            self._queue.put(_DownloadJob(requirement, callback))
            return downloading

    def submit_all(self, graph, callback: Callable[[tuple[object, ...]], None] | None = None):
        requirements = tuple(graph.requirements)
        if not requirements:
            if callback is not None:
                self._deliver((), callback)
            return ()
        terminal: list[object] = []
        remaining = len(requirements)
        lock = threading.Lock()

        def one_done(status):
            nonlocal remaining
            with lock:
                terminal.append(status)
                remaining -= 1
                if remaining:
                    return
                result = tuple(terminal)
            if callback is not None:
                self._deliver(result, callback)

        return tuple(self.submit(requirement, one_done) for requirement in requirements)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(None)
        self._worker.join(timeout=2.0)

    def _deliver(self, value, callback) -> None:
        if callback is None:
            return

        def invoke() -> None:
            with self._lock:
                if self._closed:
                    return
            callback(value)

        self._dispatch(invoke)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                status = self.resolver.download_requirement(job.requirement)
            except Exception as exc:
                status = self.resolver.AssetStatus(
                    job.requirement,
                    self.resolver.RequirementState.FAILED,
                    (),
                    str(exc),
                )
            with self._lock:
                self._statuses[job.requirement.id] = status
            self._deliver(status, job.callback)
