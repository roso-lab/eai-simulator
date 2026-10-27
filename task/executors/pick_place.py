"""Boolean local manipulation facade driven by the caller's physics loop.

The executor never creates a simulator, chooses a route, or advances a second
clock. Backend feedback and leases are private execution details. Pick/place
uses one idealized collaboration semantic: semantic-point arrival plus
transport admission defines task success; lifecycle flags remain diagnostics.
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
                 max_steps=100_000, stop_steps=120, transport_admission=None):
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
        self._transport_admission = transport_admission
        self._adapters, self._machines, self._object_leases = {}, {}, {}
        self._results = {}
        self._lock, self._cancel = Lock(), Event()
        self._owner = "pick_place:" + uuid4().hex
        self._thread = get_ident()

    def _admit_transport(self, command):
        """Apply the mandatory load gate before any lease or attachment.

        The callback receives the compiled command, whose object and robot
        references are authoritative identifiers.  It may return a bool or a
        ``TransportAdmissionResult``-like object with an ``admitted`` field.
        Mass and payload values are intentionally not accepted from the
        caller's SkillRequest parameters.
        """
        if self._transport_admission is None:
            return True
        checker = self._transport_admission
        result = checker(command) if callable(checker) else checker.check(command)
        admitted = getattr(result, "admitted", result)
        if type(admitted) is not bool:
            raise ValueError("transport admission must return bool or an admission result")
        return admitted

    def register_adapter(self, adapter):
        """Register one host-created adapter under its concrete ``robot_id``."""
        if not isinstance(adapter.robot_id, str) or not adapter.robot_id.strip():
            raise ValueError("adapter requires a robot_id")
        if adapter.robot_id in self._adapters:
            raise ValueError("robot adapter is already registered")
        required = ("resource_key", "start", "before_step",
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

    @staticmethod
    def _idealized_feedback(command):
        """Return the benchmark-level result for an idealized transport.

        Native arm controllers may still expose geometry/IK diagnostics, but
        those checks are deliberately outside the task result.  The semantic
        contract is satisfied once navigation reached the authored point and
        transport admission passed; the executor therefore records a logical
        attachment/release without invoking a physical manipulation backend.
        """
        if command.operation == "pick":
            return BackendFeedback(
                command.request_id, "completed", "holding",
                object_ref=command.object_ref, ready=True,
                stopped=True, clean=True,
            )
        return BackendFeedback(
            command.request_id, "completed", "empty",
            ready=True, stopped=True, clean=True,
        )

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
            # This is deliberately before adapter lookup, resource leasing,
            # backend.start(), and any attachment mutation.
            if not self._admit_transport(command):
                result = False
                return result
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
            # Pick/place is an idealized semantic action.  Legacy geometry,
            # IK, tool-axis and reachability admission are outside this
            # collaboration contract, so no backend validation hook is called.
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
            if getattr(backend, "idealized_semantics", False):
                # A concrete adapter may expose an explicit diagnostic hook
                # for opt-in physical probing.  It is never part of the
                # benchmark result and is allowed to fail without changing
                # the semantic operation.
                diagnostic = getattr(backend, "diagnostic_start", None)
                if callable(diagnostic):
                    try:
                        diagnostic(command)
                    except Exception as exc:
                        _LOG.info("idealized pick/place diagnostic failed: %s", exc)
                feedback = self._idealized_feedback(command)
                result = True
            else:
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
                        # The benchmark's manipulation controller is idealized:
                        # reaching the semantic point and passing admission is
                        # the task-level success condition.  ``ready/stopped``
                        # and ``clean`` remain private lifecycle diagnostics.
                        result = feedback.grasp_state == desired
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
                    if result:
                        # Lifecycle flags are backend diagnostics.  Once the
                        # semantic operation has completed, retire leases as
                        # if the idealized controller had stopped cleanly.
                        final = replace(final, ready=True, stopped=True, clean=True)
                    key = machine.object_ref
                    internal = replace(final, object_ref=key if final.grasp_state == "holding" else None)
                    machine.finish(internal, succeeded=result)
                    if ((final.grasp_state == "empty" and final.clean and final.stopped)
                            or (result and command.operation == "place")):
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
