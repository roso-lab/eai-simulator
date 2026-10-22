from dataclasses import replace
import json
import time

import pytest

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore
from tools.benchmark_branch.examples.vectorized_reference import (
    NumpyVectorAdapter,
    NumpyVectorBackend,
)
from tools.benchmark_branch.shutdown_watchdog import (
    FORMAL_QUALIFICATION_GATES,
    SHARED_EXECUTION_MODEL,
    shutdown_with_watchdog,
    validate_shared_session_receipt,
)
from tools.benchmark_branch.shutdown_watchdog import _issue_collector_observation
from tools.benchmark_branch.shutdown_watchdog import _DeferredCollectorObservationCapability
from tools.benchmark_branch.vectorized import IsolationError
from tools.benchmark_branch import vectorized_executor
from tools.physics_replay.execution.scheduler import ReplayCapacity, ReplayScheduler


def receipt():
    return {
        "execution_model": SHARED_EXECUTION_MODEL,
        "shared_session_id": "session-1",
        "environment_slot": 1,
        "slot_generation": 2,
        "expected_slot_generation": 2,
        "slot_token": "token-1",
        "branch_id": "branch-1#attempt-0",
        "block_id": "block-1",
        "pair_group_id": "pair-1",
        "experiment_identity": {
            "run_id": "run-1",
            "base_instance_id": "base-1",
            "pair_group_id": "pair-1",
        },
        "checkpoint_digest": "checkpoint",
        "archive_digest": "archive",
        "random_stream_digest": "random",
        "controller_substream_digest": "controller",
        "process_failure_scope": "shared_session",
        "shutdown_status": "pending",
        "participant_digests": {"world": "world-d", "robot": "robot-d", "rng": "rng-d"},
        "snapshot_digest": "snap",
        "snapshot_readback_digest": "snap",
        "normalized_deviation": 0.01,
        "deviation_tolerance": 0.05,
        "restore_receipt": {
            "observer_verified": True,
            "restored": True,
            "observer_source": "collector-test-observer",
        },
    }


def observer(value=None, *, terminal=True):
    return {
        "shared_session": True,
        "process_terminal": terminal,
        "observer_id": "collector-observer",
        "equivalence_receipt": receipt() if value is None else value,
    }


def shared_report(value=None):
    value = receipt() if value is None else value
    experiment = value["experiment_identity"]
    return {
        "diagnostic_body_completed": True,
        "process_failure_scope": "shared_session",
        "execution_model": SHARED_EXECUTION_MODEL,
        "shared_session_id": value["shared_session_id"],
        "block_id": value["block_id"],
        "pair_group_id": value["pair_group_id"],
        "checkpoint_digest": value["checkpoint_digest"],
        "environment_slot": value["environment_slot"],
        "slot_generation": value["slot_generation"],
        "slot_token": value["slot_token"],
        "run_id": experiment["run_id"],
        "base_instance_id": experiment["base_instance_id"],
    }


def capability(report, value=None, *, terminal=True):
    return _issue_collector_observation(report, observer(value, terminal=terminal))


def flat_observer(value=None):
    return {
        "shared_session": True,
        "process_terminal": True,
        **(receipt() if value is None else value),
    }


def execute(tmp_path, *, backend=None, metadata=None):
    plan = BranchPlan(slots=1, metadata=dict(metadata or {}))
    store = BranchRunStore.create(
        tmp_path, plan, execution_mode="shared_session", requested_max_workers=4,
    )
    backend = backend or NumpyVectorBackend(horizon=2)
    scheduler = ReplayScheduler(
        ReplayCapacity(1, 4, 4), device_probe=lambda session, request: session,
    )
    result = BranchExecutor(
        plan, NumpyVectorAdapter(), store, max_workers=4,
        session_backend=backend, scheduler=scheduler, requested_device="cpu",
    ).run()
    return result, store, backend


def test_shared_vectorized_receipt_is_admitted_but_never_qualified():
    result = validate_shared_session_receipt(receipt())
    assert result["qualification_status"] == "pending_paper_realignment"
    assert all(result[key] is False for key in FORMAL_QUALIFICATION_GATES)


@pytest.mark.parametrize(
    "field",
    [
        "restore_receipt", "snapshot_readback_digest", "random_stream_digest",
        "controller_substream_digest", "slot_generation", "experiment_identity",
    ],
)
def test_shared_receipt_missing_stale_or_inconsistent_identity_fails_closed(field):
    value = receipt()
    value[field] = None if field != "slot_generation" else 1
    if field == "snapshot_readback_digest":
        value[field] = "wrong"
    with pytest.raises(ValueError):
        validate_shared_session_receipt(value)


@pytest.mark.parametrize("deviation,tolerance", [(0.2, 0.1), (float("nan"), 0.1), (0.1, -1)])
def test_shared_receipt_deviation_and_tolerance_fail_closed(deviation, tolerance):
    value = receipt()
    value.update(normalized_deviation=deviation, deviation_tolerance=tolerance)
    with pytest.raises(ValueError):
        validate_shared_session_receipt(value)


def test_shared_shutdown_requires_collector_observer_and_complete_equivalence(tmp_path):
    output = tmp_path / "shutdown.json"
    report = shared_report()
    # Even a complete caller-provided receipt is self-report on this path.
    assert shutdown_with_watchdog(
        dict(report), output, lambda: "clean", timeout_s=0.01,
        process_receipt=observer(),
    ) == "unknown-record"
    assert shutdown_with_watchdog(
        dict(report), output, lambda: "clean", timeout_s=0.01,
        process_observer=capability(report, terminal=False),
    ) == "unknown-record"

    invalid = receipt()
    invalid["snapshot_readback_digest"] = "forged"
    assert shutdown_with_watchdog(
        dict(report), output, lambda: "clean", timeout_s=0.01,
        process_observer=capability(report, invalid),
    ) == "pair-invalid"

    accepted = dict(report)
    assert shutdown_with_watchdog(
        accepted, output, lambda: "clean", timeout_s=0.01,
        process_observer=capability(accepted),
    ) == "clean"
    persisted = json.loads(output.read_text())
    assert persisted["shared_session_equivalence"]["rankable"] is False
    assert all(persisted[key] is False for key in FORMAL_QUALIFICATION_GATES)

    assert shutdown_with_watchdog(
        dict(report), output, lambda: "clean", timeout_s=0.01,
        process_observer=flat_observer,
    ) == "unknown-record"


@pytest.mark.parametrize(
    "forged_observer",
    [
        lambda: observer(),
        lambda: dict(observer()),
        lambda: observer({
            **receipt(),
            "restore_receipt": {
                "observer_verified": True,
                "restored": True,
                "observer_source": "forged-collector-source",
            },
        }),
    ],
)
def test_plain_callable_or_copied_mapping_cannot_be_a_shared_observer(tmp_path, forged_observer):
    report = shared_report()
    assert shutdown_with_watchdog(
        report, tmp_path / "forged.json", lambda: "clean", timeout_s=0.01,
        process_observer=forged_observer,
    ) == "unknown-record"


def test_collector_capability_observer_source_tampering_is_untrusted(tmp_path):
    report = shared_report()
    forged = capability(report)
    forged._observed["equivalence_receipt"]["restore_receipt"]["observer_source"] = "tampered"
    assert shutdown_with_watchdog(
        report, tmp_path / "tampered.json", lambda: "clean", timeout_s=0.01,
        process_observer=forged,
    ) == "unknown-record"


def test_trusted_terminal_with_wrong_execution_model_is_pair_invalid(tmp_path):
    report = shared_report()
    value = receipt()
    value["execution_model"] = "isolated_process"
    forged = capability(report, value)
    assert shutdown_with_watchdog(
        report, tmp_path / "wrong-model.json", lambda: "clean", timeout_s=0.01,
        process_observer=forged,
    ) == "pair-invalid"


def test_shutdown_exception_persists_unknown_before_propagating(tmp_path):
    output = tmp_path / "shutdown-crash.json"
    report = shared_report()

    def crash():
        raise RuntimeError("shared shutdown crashed")

    with pytest.raises(RuntimeError, match="shared shutdown crashed"):
        shutdown_with_watchdog(
            report, output, crash, timeout_s=0.01,
            process_observer=capability(report),
        )
    persisted = json.loads(output.read_text())
    assert persisted["shutdown_status"] == "unknown-record"
    assert all(persisted[key] is False for key in FORMAL_QUALIFICATION_GATES)


def test_shutdown_exception_after_timeout_keeps_timeout_sticky(tmp_path):
    output = tmp_path / "shutdown-timeout-crash.json"
    report = shared_report()

    def crash_after_timeout():
        time.sleep(0.03)
        raise RuntimeError("shared shutdown crashed after timeout")

    with pytest.raises(RuntimeError, match="shared shutdown crashed after timeout"):
        shutdown_with_watchdog(
            report, output, crash_after_timeout, timeout_s=0.005,
            process_observer=capability(report),
        )
    persisted = json.loads(output.read_text())
    assert persisted["shutdown_status"] == "shutdown_timeout"
    assert "shared shutdown crashed after timeout" in persisted["shutdown_exception"]
    assert all(persisted[key] is False for key in FORMAL_QUALIFICATION_GATES)


@pytest.mark.parametrize("factory_kind", ["raises", "invalid", "identity-mismatch"])
def test_deferred_observer_errors_after_timeout_keep_timeout_sticky(tmp_path, factory_kind):
    output = tmp_path / f"deferred-{factory_kind}.json"
    report = shared_report()

    def deferred_factory(_report):
        if factory_kind == "raises":
            raise RuntimeError("deferred observer failed")
        if factory_kind == "invalid":
            return []
        forged = observer()
        forged["equivalence_receipt"] = {
            **forged["equivalence_receipt"],
            "shared_session_id": "wrong-session",
        }
        return forged

    status = shutdown_with_watchdog(
        report, output, lambda: (time.sleep(0.03) or "clean"), timeout_s=0.005,
        process_observer=_DeferredCollectorObservationCapability(deferred_factory),
    )
    persisted = json.loads(output.read_text())
    assert status == persisted["shutdown_status"] == "shutdown_timeout"
    assert all(persisted[key] is False for key in FORMAL_QUALIFICATION_GATES)


def test_shutdown_timeout_is_sticky_and_cannot_open_formal_gates(tmp_path):
    output = tmp_path / "timeout.json"
    report = {**shared_report(), "rankable": True, "paper_score_eligible": True}
    status = shutdown_with_watchdog(
        report, output, lambda: (time.sleep(0.04) or "clean"), timeout_s=0.005,
        process_observer=capability(report),
    )
    persisted = json.loads(output.read_text())
    assert status == persisted["shutdown_status"] == "shutdown_timeout"
    assert all(persisted[key] is False for key in FORMAL_QUALIFICATION_GATES)


def test_production_vectorized_path_validates_every_bind_before_first_step(monkeypatch, tmp_path):
    observed = []
    original = vectorized_executor.validate_shared_session_receipt

    class OrderedBackend(NumpyVectorBackend):
        def step_batch(self, session, actions):
            assert len(observed) == 4
            return super().step_batch(session, actions)

    def recording_validator(value):
        validated = original(value)
        observed.append(validated)
        return validated

    monkeypatch.setattr(vectorized_executor, "validate_shared_session_receipt", recording_validator)
    result, store, backend = execute(tmp_path, backend=OrderedBackend(horizon=2))

    assert result["completed"] == 4
    assert backend.batch_calls == 2
    assert len(observed) == 4
    assert all(item["execution_model"] == SHARED_EXECUTION_MODEL for item in observed)
    assert all(item["restore_receipt"]["observer_verified"] is True for item in observed)
    checkpoints = [row for row in store.read_ledger("checkpoints")
                   if "shared_session_equivalence" in row]
    assert len(checkpoints) == 4
    assert all(result[key] is False and store.manifest[key] is False
               for key in FORMAL_QUALIFICATION_GATES)
    assert all(row[key] is False for row in store.read_ledger("episodes")
               for key in FORMAL_QUALIFICATION_GATES)


def test_production_path_records_unknown_without_collector_shutdown_observer(tmp_path):
    result, store, backend = execute(tmp_path, backend=NumpyVectorBackend(horizon=1))

    shutdown_rows = [row for row in store.read_ledger("resources")
                     if row.get("kind") == "shared_session_shutdown"]
    assert len(shutdown_rows) == 4
    assert {row["shutdown_status"] for row in shutdown_rows} == {"unknown-record"}
    assert all(row["collector_observer_present"] is False for row in shutdown_rows)
    assert all((tmp_path / row["shutdown_receipt_path"]).is_file() for row in shutdown_rows)
    assert store.manifest["shutdown_status"] == "unknown-record"
    assert store.manifest["shutdown_observer_present"] is False
    assert all(result[key] is False for key in FORMAL_QUALIFICATION_GATES)
    persisted = json.loads((tmp_path / shutdown_rows[0]["shutdown_receipt_path"]).read_text())
    assert persisted["shutdown_status"] == "unknown-record"
    assert persisted["process_failure_scope"] == "shared_session"
    assert all(persisted[key] is False for key in FORMAL_QUALIFICATION_GATES)


def test_production_path_accepts_only_backend_owned_post_close_observer(tmp_path):
    class ObservedBackend(NumpyVectorBackend):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.observed_after_close = []

        def collect_shutdown_observation(self, session, identity):
            # This is the backend-owned readback contract used only to test
            # the deferred collector capability.  It is invoked after
            # close_scene, so the caller cannot inject a report receipt.
            self.observed_after_close.append(not session["slots"])
            value = receipt()
            value.update({
                "shared_session_id": identity["shared_session_id"],
                "block_id": identity["block_id"],
                "pair_group_id": identity["pair_group_id"],
                "checkpoint_digest": identity["checkpoint_digest"],
                "environment_slot": identity["environment_slot"],
                "slot_generation": identity["slot_generation"],
                "slot_token": identity["slot_token"],
                "expected_slot_generation": identity["slot_generation"],
                "experiment_identity": {
                    "run_id": identity["run_id"],
                    "base_instance_id": identity["base_instance_id"],
                    "pair_group_id": identity["pair_group_id"],
                },
            })
            return {"shared_session": True, "process_terminal": True,
                    "observer_id": "backend-owned", "equivalence_receipt": value}

    backend = ObservedBackend(horizon=1)
    result, store, _ = execute(tmp_path, backend=backend)
    shutdown_rows = [row for row in store.read_ledger("resources")
                     if row.get("kind") == "shared_session_shutdown"]
    assert len(shutdown_rows) == 4
    assert {row["shutdown_status"] for row in shutdown_rows} == {"clean"}
    assert all(row["collector_observer_verified"] is True for row in shutdown_rows)
    assert store.manifest["shutdown_status"] == "clean"
    assert store.manifest["shutdown_observer_present"] is True
    assert backend.observed_after_close == [True, True, True, True]
    assert all(result[key] is False for key in FORMAL_QUALIFICATION_GATES)


def test_post_close_observer_is_not_used_when_scene_cleanup_fails(tmp_path):
    class BrokenCloseBackend(NumpyVectorBackend):
        def close_scene(self, session):
            raise RuntimeError("scene close failed")

        def collect_shutdown_observation(self, session, identity):
            raise AssertionError("observer must not run after failed close")

    result, store, _ = execute(tmp_path, backend=BrokenCloseBackend(horizon=1))
    shutdown_rows = [row for row in store.read_ledger("resources")
                     if row.get("kind") == "shared_session_shutdown"]
    assert len(shutdown_rows) == 4
    assert {row["shutdown_status"] for row in shutdown_rows} == {"unknown-record"}
    assert store.manifest["shutdown_status"] == "unknown-record"
    assert all(result[key] is False for key in FORMAL_QUALIFICATION_GATES)


def test_vectorized_ledger_preserves_watchdog_timeout_when_close_raises(monkeypatch, tmp_path):
    def timeout_then_raise(report, output, shutdown_fn, **kwargs):
        persisted = {**report, "shutdown_status": "shutdown_timeout"}
        persisted.update({key: False for key in FORMAL_QUALIFICATION_GATES})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(persisted) + "\n", encoding="utf-8")
        raise RuntimeError("late scene close failure")

    monkeypatch.setattr(vectorized_executor, "shutdown_with_watchdog", timeout_then_raise)
    result, store, _ = execute(tmp_path, backend=NumpyVectorBackend(horizon=1))
    shutdown_rows = [row for row in store.read_ledger("resources")
                     if row.get("kind") == "shared_session_shutdown"]
    assert len(shutdown_rows) == 4
    assert {row["shutdown_status"] for row in shutdown_rows} == {"shutdown_timeout"}
    assert all(result[key] is False for key in FORMAL_QUALIFICATION_GATES)


def test_equivalence_rejection_prevents_any_shared_step(monkeypatch, tmp_path):
    def reject(_value):
        raise ValueError("observer chain rejected")

    monkeypatch.setattr(vectorized_executor, "validate_shared_session_receipt", reject)
    result, store, backend = execute(tmp_path)
    assert backend.batch_calls == 0
    # The equivalence rejection occurs after the first slot binds; later queued
    # branches are deliberately left without terminal episode rows.
    episodes = store.read_ledger("episodes")
    assert result["completed"] == 0 and result["failed"] == 1
    assert len(episodes) == 1
    assert all(row["status"] == "infrastructure_invalid" for row in episodes)
    assert all(result[key] is False for key in FORMAL_QUALIFICATION_GATES)


def test_normalized_restore_deviation_over_manifest_tolerance_prevents_step(tmp_path):
    class DeviatingBackend(NumpyVectorBackend):
        def open_slot(self, *args, **kwargs):
            value = dict(super().open_slot(*args, **kwargs))
            value["normalized_deviation"] = 0.2
            return value

    result, store, backend = execute(
        tmp_path,
        backend=DeviatingBackend(),
        metadata={"restore_deviation_tolerance": 0.1},
    )
    assert backend.batch_calls == 0
    assert result["completed"] == 0
    assert all("manifest tolerance" in row["error"] for row in store.read_ledger("episodes"))


def test_paired_terminal_receipt_is_frozen_and_conflicts_fail_closed():
    job = BranchPlan(slots=1).jobs()[0]
    registration = {
        "block_id": "block-1",
        "branch_to_original": {job.logical_branch_id: "original-1"},
        "checkpoint_digest": "checkpoint-1",
        "random_stream_digest": "random-1",
        "controller_substream_digest": "controller-1",
        "participant_digests": {"world": "world-1"},
    }
    job = replace(
        job,
        checkpoint_digest="checkpoint-1",
        metadata={"paired_block_rerun": registration},
    )
    row = vectorized_executor._paired_terminal_receipt(
        job,
        {"status": "infrastructure_invalid", "completed": False, "vector_seq": 3},
    )
    assert row["block_id"] == "block-1"
    assert row["failure_class"] == "infrastructure"
    assert row["result_digest"] == vectorized_executor._paired_result_digest(row)
    with pytest.raises(IsolationError, match="conflicts"):
        vectorized_executor._paired_terminal_receipt(
            job,
            {"status": "infrastructure_invalid", "completed": False, "block_id": "forged"},
        )
