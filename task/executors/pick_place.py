"""Boolean local manipulation facade driven by the caller's physics loop.

The executor never creates a simulator, chooses a route, or advances a second
clock. Backend feedback and leases are private execution details.
"""
from __future__ import annotations

from dataclasses import replace
import logging
import math
from threading import Event, Lock, get_ident
from uuid import uuid4

from algorithm.skills.pick_place import (
    BackendFeedback, PickPlaceStateMachine, compile_request,
)
from ..resources import ResourceManager

_LOG = logging.getLogger(__name__)


class PickPlaceExecutor:
    def __init__(self, *, advance, simulation_time, resources=None,
                 snapshot_validator=None, max_snapshot_age_s=.5,
                 max_steps=100_000, stop_steps=120):
        if not callable(advance) or not callable(simulation_time):
            raise ValueError("advance and simulation_time must be host callbacks")
        if type(max_steps) is not int or max_steps <= 0 or type(stop_steps) is not int or stop_steps <= 0:
            raise ValueError("step limits must be positive integers")
        if not math.isfinite(max_snapshot_age_s) or max_snapshot_age_s < 0:
            raise ValueError("max_snapshot_age_s must be nonnegative")
        self._advance, self._time = advance, simulation_time
        self._resources = resources if resources is not None else ResourceManager()
        self._snapshot_validator = snapshot_validator
        self._max_age, self._max_steps, self._stop_steps = max_snapshot_age_s, max_steps, stop_steps
        self._adapters, self._machines, self._object_leases = {}, {}, {}
        self._results = {}
        self._lock, self._cancel = Lock(), Event()
        self._owner = "pick_place:" + uuid4().hex
        self._thread = get_ident()

    def register_adapter(self, adapter):
        """Register one host-created adapter under its concrete ``robot_id``."""
        if not isinstance(adapter.robot_id, str) or not adapter.robot_id.strip():
            raise ValueError("adapter requires a robot_id")
        if adapter.robot_id in self._adapters:
            raise ValueError("robot adapter is already registered")
        required = ("resource_key", "validate", "start", "before_step",
                    "after_step", "feedback", "request_stop")
        if any(not callable(getattr(adapter, name, None)) for name in required):
            raise TypeError("adapter does not implement the PickPlaceAdapter protocol")
        self._adapters[adapter.robot_id] = adapter
        self._machines[adapter.robot_id] = PickPlaceStateMachine()

    def cancel(self):
        """May be called by the host or another thread during execute."""
        self._cancel.set()

    @staticmethod
    def _check_feedback(feedback, command):
        if not isinstance(feedback, BackendFeedback) or feedback.request_id != command.request_id:
            raise ValueError("backend returned feedback for another operation")
        if feedback.status not in {"running", "completed", "failed", "stopped"}:
            raise ValueError("invalid backend status")
        if feedback.grasp_state not in {"empty", "holding", "unknown"}:
            raise ValueError("invalid grasp state")
        if any(type(v) is not bool for v in (feedback.ready, feedback.stopped, feedback.clean)):
            raise ValueError("backend flags must be bool")
        if feedback.grasp_state == "holding" and feedback.object_ref != command.object_ref:
            raise ValueError("backend reports a different held object")
        return feedback

    def _clock(self):
        value = self._time()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("host simulation clock must be finite")
        return float(value)

    def _step(self, backend):
        backend.before_step()
        self._advance()
        backend.after_step()

    def _stop(self, backend, command, reason):
        # A rejected stop or failed host step must retain occupancy. It must
        # never be interpreted as permission to release a physical attachment.
        try:
            backend.request_stop(reason)
            for _ in range(self._stop_steps):
                feedback = self._check_feedback(backend.feedback(), command)
                if feedback.stopped:
                    return feedback
                self._step(backend)
        except Exception:
            _LOG.exception("pick/place safe stop failed: %s", command.request_id)
        return BackendFeedback(command.request_id, "failed", "unknown", reason=reason)

    def execute(self, request, *, cancelled=None) -> bool:
        """Finish one pick or place and return exactly bool, including failures.

        Replaying the identical request id is idempotent. Use a new request id
        to retry a failed operation or perform the next operation.
        """
        if get_ident() != self._thread or not self._lock.acquire(blocking=False):
            return False
        self._cancel.clear()
        command = backend = machine = motion = None
        began = False
        result = False
        feedback = None
        try:
            command = compile_request(request)
            cached = self._results.get(command.request_id)
            if cached is not None:
                return cached[1] if cached[0] == command else False
            backend = self._adapters[command.robot_id]
            machine = self._machines[command.robot_id]
            key = backend.resource_key(command.object_ref)
            state_command = replace(command, object_ref=key)
            # Admission is pure: failed validation does not change control or
            # the carried object's state.
            trial = replace(machine)
            trial.begin(state_command)
            now = self._clock()
            age = now - command.simulation_time_s
            if age < -1e-6 or age > self._max_age:
                raise ValueError("caller pose snapshot is stale or from the future")
            if self._snapshot_validator is not None:
                if self._snapshot_validator(command) is not True:
                    raise ValueError("host rejected the semantic-map/pose snapshot")
            backend.validate(command)
            motion = self._resources.acquire(self._owner + ":motion:" + command.robot_id,
                                             ("robot:" + command.robot_id,))
            if command.operation == "pick":
                try:
                    self._object_leases[command.robot_id] = self._resources.acquire(
                        self._owner + ":object:" + command.robot_id, (key,))
                except Exception:
                    self._resources.release(motion)
                    motion = None
                    raise
            else:
                self._resources.validate_lease(self._object_leases[command.robot_id], required_resources=(key,))
            machine.begin(state_command)
            began = True  # start can mutate before raising
            backend.start(command)
            previous = now
            for _ in range(self._max_steps):
                feedback = self._check_feedback(backend.feedback(), command)
                if self._cancel.is_set() or (cancelled is not None and cancelled()):
                    feedback = self._stop(backend, command, "cancelled")
                    break
                current = self._clock()
                if current < previous or current - now >= command.timeout_s:
                    feedback = self._stop(backend, command, "simulation timeout or clock reset")
                    break
                previous = current
                if feedback.status == "completed":
                    desired = "holding" if command.operation == "pick" else "empty"
                    result = feedback.ready and feedback.stopped and feedback.grasp_state == desired
                    if command.operation == "place":
                        result = result and feedback.clean
                    if not result:
                        feedback = self._stop(backend, command, "invalid completion feedback")
                    break
                if feedback.status in {"failed", "stopped"}:
                    feedback = self._stop(backend, command, feedback.reason or "backend failed")
                    break
                self._step(backend)
            else:
                feedback = self._stop(backend, command, "host step limit exceeded")
        except Exception as exc:
            _LOG.warning("pick/place returned False: %s", exc, exc_info=True)
            if began:
                feedback = self._stop(backend, command, str(exc))
        finally:
            try:
                if began:
                    final = feedback or BackendFeedback(command.request_id, "failed", "unknown")
                    key = machine.object_ref
                    internal = replace(final, object_ref=key if final.grasp_state == "holding" else None)
                    machine.finish(internal, succeeded=result)
                    if final.grasp_state == "empty" and final.clean and final.stopped:
                        self._resources.release(self._object_leases.pop(command.robot_id))
                    if motion is not None:
                        if final.stopped:
                            self._resources.release(motion)
                        else:
                            self._resources.quarantine(motion, final.reason or "stop unconfirmed")
                    _LOG.info("pick/place %s %s -> %s (%s, %s)", command.request_id,
                              command.operation, result, machine.phase, final.reason)
                elif motion is not None:
                    self._resources.release(motion)
            except Exception:
                result = False
                if machine is not None:
                    machine.phase = "fault"
                _LOG.exception("pick/place resource retirement failed; ownership retained")
            finally:
                if command is not None and command.request_id not in self._results:
                    self._results[command.request_id] = command, bool(result)
                self._lock.release()
        return bool(result)


__all__ = ["PickPlaceExecutor"]
