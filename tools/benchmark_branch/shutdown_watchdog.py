"""Dependency-light shutdown provenance watchdog.

Shared-session shutdown evidence must come from a collector-owned observer.
The report being written and an optional caller receipt are never accepted as
proof of in-process restore equivalence.
"""
from __future__ import annotations

import json
import hashlib
import hmac
import math
from pathlib import Path
import secrets
import threading
from copy import deepcopy
from typing import Any, Callable, Mapping


SHARED_EXECUTION_MODEL = "shared_session_vectorized"
FORMAL_QUALIFICATION_GATES = (
    "rankable",
    "formal_measured_qualified",
    "native_full_state_qualified",
    "process_isolation_qualified",
    "physical_qualified",
    "paper_score_eligible",
)


def _force_unqualified(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {**dict(payload), **{key: False for key in FORMAL_QUALIFICATION_GATES}}


_CAPABILITY_SECRET = secrets.token_bytes(32)
_CAPABILITY_IDENTITY_KEYS = (
    "shared_session_id", "block_id", "pair_group_id",
    "checkpoint_digest", "environment_slot", "slot_generation", "slot_token",
    "run_id", "base_instance_id",
)


class _UntrustedObservation(ValueError):
    pass


class _ObservedIdentityMismatch(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _observation_identity(report: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
    equivalence = observed.get("equivalence_receipt")
    if not isinstance(equivalence, Mapping):
        equivalence = observed
    experiment = equivalence.get("experiment_identity")
    if not isinstance(experiment, Mapping):
        experiment = {}
    values = {
        "shared_session_id": equivalence.get("shared_session_id"),
        "block_id": equivalence.get("block_id"),
        "pair_group_id": equivalence.get("pair_group_id"),
        "checkpoint_digest": equivalence.get("checkpoint_digest"),
        "environment_slot": equivalence.get("environment_slot"),
        "slot_generation": equivalence.get("slot_generation"),
        "slot_token": equivalence.get("slot_token"),
        "run_id": experiment.get("run_id"),
        "base_instance_id": experiment.get("base_instance_id"),
    }
    missing = [key for key, value in values.items() if value in (None, "")]
    if missing:
        raise ValueError(f"collector observation identity is incomplete: {missing}")
    for key in _CAPABILITY_IDENTITY_KEYS:
        if key in report and report[key] != values[key]:
            raise ValueError(f"collector observation identity disagrees with report: {key}")
    absent_report_keys = [key for key in _CAPABILITY_IDENTITY_KEYS if key not in report]
    if absent_report_keys:
        raise ValueError(f"report identity is incomplete: {absent_report_keys}")
    return values


class _CollectorObservationCapability:
    """Opaque, single-use collector-issued observation capability.

    The watchdog accepts this exact internal type only.  Its signed identity
    binds the observation to the report's run/session/block/checkpoint/slot;
    ordinary callables and copied dictionaries cannot satisfy that boundary.
    """

    __slots__ = ("_observed", "_identity", "_signature", "_used")

    def __init__(self, observed: Mapping[str, Any], identity: Mapping[str, Any], signature: str):
        self._observed = deepcopy(dict(observed))
        self._identity = deepcopy(dict(identity))
        self._signature = str(signature)
        self._used = False

    def _consume(self, report: Mapping[str, Any]) -> dict[str, Any]:
        if self._used:
            raise _UntrustedObservation("collector observation capability was already consumed")
        self._used = True
        expected = hmac.new(
            _CAPABILITY_SECRET,
            _canonical_bytes({"identity": self._identity, "observed": self._observed}),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, self._signature):
            raise _UntrustedObservation("collector observation capability signature is invalid")
        try:
            current = _observation_identity(report, self._observed)
        except ValueError as exc:
            raise _ObservedIdentityMismatch(str(exc)) from exc
        if current != self._identity:
            raise _ObservedIdentityMismatch("collector observation capability identity mismatch")
        return deepcopy(self._observed)


class _DeferredCollectorObservationCapability:
    """Collector capability whose durable readback is taken after shutdown.

    The factory is intentionally not accepted directly by the watchdog.  It
    is wrapped in this private type so a caller cannot pass an arbitrary
    callable and claim collector authority.  The factory is invoked only
    from ``_consume``, after the watchdog's shutdown function returns.
    """

    __slots__ = ("_factory", "_used")

    def __init__(self, factory: Callable[[Mapping[str, Any]], Mapping[str, Any]]):
        self._factory = factory
        self._used = False

    def _consume(self, report: Mapping[str, Any]) -> dict[str, Any]:
        if self._used:
            raise _UntrustedObservation("collector observation capability was already consumed")
        self._used = True
        observed = self._factory(report)
        capability = _issue_collector_observation(report, observed)
        return capability._consume(report)


def _issue_collector_observation(report: Mapping[str, Any], observed: Mapping[str, Any]) -> _CollectorObservationCapability:
    """Issue a capability inside the collector trust boundary.

    This issuer is intentionally private and omitted from ``__all__``.  A
    production collector must call it only after obtaining its own durable
    observation, then pass the opaque capability to the watchdog.
    """

    if not isinstance(observed, Mapping):
        raise TypeError("collector observation must be a mapping")
    identity = _observation_identity(report, observed)
    frozen = deepcopy(dict(observed))
    signature = hmac.new(
        _CAPABILITY_SECRET,
        _canonical_bytes({"identity": identity, "observed": frozen}),
        hashlib.sha256,
    ).hexdigest()
    return _CollectorObservationCapability(frozen, identity, signature)


def _validate_collector_observation(report: Mapping[str, Any], observed: Any) -> dict[str, Any]:
    if not isinstance(observed, Mapping):
        raise _UntrustedObservation("collector observation is not a mapping")
    observed = dict(observed)
    if observed.get("shared_session") is not True or observed.get("process_terminal") is not True:
        raise _UntrustedObservation("collector observation is not terminal")
    equivalence = observed.get("equivalence_receipt")
    if equivalence is None and "experiment_identity" in observed:
        equivalence = observed
    if not isinstance(equivalence, Mapping):
        raise _ObservedIdentityMismatch("collector observation lacks equivalence receipt")
    validated = validate_shared_session_receipt(equivalence)
    return {
        "process_provenance": {
            key: value for key, value in observed.items() if key != "equivalence_receipt"
        },
        "shared_session_equivalence": validated,
    }


def validate_shared_session_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one branch's observed in-process restore-equivalence chain.

    Passing this structural check is admission evidence only.  Until the paper
    contract is revised and independently realigned it cannot open any formal,
    physical, process-isolation, ranking, or paper-score gate.
    """

    required_text = (
        "shared_session_id", "slot_token", "branch_id", "block_id",
        "pair_group_id", "checkpoint_digest", "archive_digest",
        "random_stream_digest", "controller_substream_digest",
    )
    required = (
        "execution_model", "environment_slot", "slot_generation",
        "expected_slot_generation", "experiment_identity",
        "process_failure_scope", "shutdown_status", "participant_digests",
        "snapshot_digest", "snapshot_readback_digest",
        "normalized_deviation", "deviation_tolerance", "restore_receipt",
        *required_text,
    )
    if not isinstance(receipt, Mapping) or any(key not in receipt for key in required):
        raise ValueError("shared-session receipt is incomplete")
    if receipt["execution_model"] != SHARED_EXECUTION_MODEL:
        raise ValueError("shared-session execution model is invalid")
    if receipt["process_failure_scope"] != "shared_session":
        raise ValueError("shared-session process failure scope is invalid")
    if any(not isinstance(receipt[key], str) or not receipt[key].strip() for key in required_text):
        raise ValueError("shared-session identity chain is incomplete")
    if not isinstance(receipt["shutdown_status"], str) or not receipt["shutdown_status"].strip():
        raise ValueError("shared-session shutdown status is missing")

    slot = receipt["environment_slot"]
    generation = receipt["slot_generation"]
    expected_generation = receipt["expected_slot_generation"]
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        raise ValueError("environment slot is invalid")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("slot generation is invalid")
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
        raise ValueError("expected slot generation is invalid")
    if generation != expected_generation:
        raise ValueError("stale slot generation")

    experiment = receipt["experiment_identity"]
    experiment_keys = ("run_id", "base_instance_id", "pair_group_id")
    if (not isinstance(experiment, Mapping)
            or any(not isinstance(experiment.get(key), str) or not experiment[key].strip()
                   for key in experiment_keys)
            or experiment["pair_group_id"] != receipt["pair_group_id"]):
        raise ValueError("experiment identity is incomplete or inconsistent")

    participants = receipt["participant_digests"]
    if not isinstance(participants, Mapping) or not participants:
        raise ValueError("participant digests are missing")
    for name, digest in participants.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(digest, str) or not digest.strip():
            raise ValueError("participant digest is malformed")

    restore = receipt["restore_receipt"]
    if (not isinstance(restore, Mapping)
            or restore.get("observer_verified") is not True
            or restore.get("restored") is not True
            or not isinstance(restore.get("observer_source"), str)
            or not restore["observer_source"].strip()):
        raise ValueError("restore receipt lacks trusted observer verification")

    deviation, tolerance = receipt["normalized_deviation"], receipt["deviation_tolerance"]
    if (isinstance(deviation, bool) or isinstance(tolerance, bool)
            or not isinstance(deviation, (int, float)) or not isinstance(tolerance, (int, float))
            or not math.isfinite(float(deviation)) or not math.isfinite(float(tolerance))
            or float(deviation) < 0 or float(tolerance) < 0
            or float(deviation) > float(tolerance)):
        raise ValueError("normalized restore deviation exceeds manifest tolerance")
    if (not isinstance(receipt["snapshot_digest"], str)
            or not receipt["snapshot_digest"].strip()
            or receipt["snapshot_digest"] != receipt["snapshot_readback_digest"]):
        raise ValueError("snapshot digest readback mismatch")
    return _force_unqualified({
        **dict(receipt),
        "qualification_status": "pending_paper_realignment",
    })


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _persist_status(report: dict[str, Any], output: Path, status: str, **extra: Any) -> str:
    report.update(_force_unqualified({"shutdown_status": status, **extra}))
    _atomic_write_json(output, report)
    return status


def shutdown_with_watchdog(
    report: dict[str, Any], output: Path, shutdown_fn: Callable[[], str], *, timeout_s: float = 20.0,
    process_receipt: Mapping[str, Any] | None = None,
    process_observer: Any = None,
) -> str:
    """Persist shutdown progress without promoting shared-session evidence.

    ``process_receipt`` remains supported for the independent-process legacy
    path.  The shared path deliberately ignores it: only an invoked observer
    can attest liveness and return the complete restore-equivalence receipt.
    """

    if timeout_s <= 0:
        raise ValueError("shutdown watchdog timeout must be positive")
    if report.get("diagnostic_body_completed") is not True:
        raise ValueError("shutdown watchdog requires a completed diagnostic body")
    scope = report.get("process_failure_scope")
    if scope not in {"shared_session", "single_reference_process"}:
        raise ValueError("shutdown watchdog requires an explicit process_failure_scope")
    report.update(_force_unqualified(report))

    if scope == "single_reference_process":
        if process_receipt is None and process_observer is None:
            return _persist_status(report, output, "unknown-record")
        try:
            observed = dict(process_observer()) if process_observer is not None else dict(process_receipt or {})
        except Exception as exc:
            return _persist_status(report, output, "unknown-record", observer_error=f"{type(exc).__name__}: {exc}")
        if observed.get("shared_session") is True:
            return _persist_status(report, output, "pair-invalid")
        if observed.get("process_terminal") is not True or observed.get("process_group_terminal") is not True:
            return _persist_status(report, output, "unknown-record")
        report["process_provenance"] = observed
    else:
        if report.get("execution_model") != SHARED_EXECUTION_MODEL:
            return _persist_status(report, output, "pair-invalid")
        # A shared-session caller receipt is self-report and cannot substitute
        # for collector-owned observation, even when it contains every field.
        if not isinstance(process_observer, (_CollectorObservationCapability, _DeferredCollectorObservationCapability)):
            return _persist_status(report, output, "unknown-record")
        deferred_observer = isinstance(process_observer, _DeferredCollectorObservationCapability)
        if not deferred_observer:
            try:
                observed = process_observer._consume(report)
            except _UntrustedObservation as exc:
                return _persist_status(report, output, "unknown-record", observer_error=f"{type(exc).__name__}: {exc}")
            except _ObservedIdentityMismatch as exc:
                return _persist_status(report, output, "pair-invalid", observer_error=f"{type(exc).__name__}: {exc}")
            except Exception as exc:
                return _persist_status(report, output, "unknown-record", observer_error=f"{type(exc).__name__}: {exc}")
            try:
                validated_fields = _validate_collector_observation(report, observed)
            except _UntrustedObservation as exc:
                return _persist_status(report, output, "unknown-record", observer_error=f"{type(exc).__name__}: {exc}")
            except _ObservedIdentityMismatch as exc:
                return _persist_status(report, output, "pair-invalid", observer_error=f"{type(exc).__name__}: {exc}")
            except (TypeError, ValueError) as exc:
                return _persist_status(
                    report, output, "pair-invalid",
                    equivalence_error=f"{type(exc).__name__}: {exc}",
                )
            report.update(validated_fields)

    _persist_status(report, output, "shutdown_started")
    timeout_state = {"triggered": False}
    timeout_lock = threading.Lock()

    def mark_timeout() -> None:
        with timeout_lock:
            timeout_state["triggered"] = True
            snapshot = _force_unqualified({
                **report,
                "shutdown_status": "shutdown_timeout",
                "shutdown_timeout_s": float(timeout_s),
            })
            # Serialize the timeout snapshot with terminal writes so a slow
            # filesystem cannot overwrite a timeout+exception receipt with a
            # stale snapshot that lacks the exception metadata.
            _atomic_write_json(output, snapshot)

    timer = threading.Timer(float(timeout_s), mark_timeout)
    timer.daemon = True
    timer.start()

    def watchdog_timed_out() -> bool:
        with timeout_lock:
            return bool(timeout_state["triggered"])

    def persist_terminal(status: str, **extra: Any) -> str:
        """Persist a terminal status without overwriting an observed timeout."""
        # Hold the same lock used by ``mark_timeout`` through the durable write.
        # Otherwise the timer could set the timeout bit immediately after this
        # check and then write a stricter timeout receipt which is overwritten
        # by a stale clean/unknown terminal write.
        with timeout_lock:
            if timeout_state["triggered"]:
                extra.setdefault("shutdown_timeout_s", float(timeout_s))
                status = "shutdown_timeout"
            return _persist_status(report, output, status, **extra)

    try:
        returned_status = shutdown_fn()
        # The close callback is not allowed to invent a terminal state.  A
        # malformed/unknown return is an execution failure and must remain
        # visible in the durable receipt (subject to the sticky timeout
        # rule), rather than becoming an arbitrary status string.
        if returned_status not in {"clean", "unknown-record", "pair-invalid"}:
            status = "unknown-record"
            return_value_error = f"invalid shutdown status returned: {returned_status!r}"
        else:
            status = str(returned_status)
            return_value_error = None
    except BaseException as exc:
        # A shared crash while shutdown is in flight cannot be represented as
        # clean or left at the nonterminal ``shutdown_started`` state.  Persist
        # the conservative pair record before propagating the original error.
        persist_terminal(
            "unknown-record",
            shutdown_exception=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        timer.cancel()
    if scope == "shared_session" and deferred_observer:
        try:
            observed = process_observer._consume(report)
            report.update(_validate_collector_observation(report, observed))
        except _UntrustedObservation as exc:
            return persist_terminal("unknown-record", observer_error=f"{type(exc).__name__}: {exc}")
        except _ObservedIdentityMismatch as exc:
            return persist_terminal("pair-invalid", observer_error=f"{type(exc).__name__}: {exc}")
        except (TypeError, ValueError) as exc:
            return persist_terminal("pair-invalid", equivalence_error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            return persist_terminal("unknown-record", observer_error=f"{type(exc).__name__}: {exc}")
    if return_value_error is not None:
        return persist_terminal(status, shutdown_return_error=return_value_error)
    return persist_terminal(status)


__all__ = [
    "FORMAL_QUALIFICATION_GATES",
    "SHARED_EXECUTION_MODEL",
    "shutdown_with_watchdog",
    "validate_shared_session_receipt",
]
