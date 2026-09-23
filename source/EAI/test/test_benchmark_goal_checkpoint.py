"""Offline checkpoint/sham contract tests; no simulator or PhysX required."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_checkpoint import (
    STATE_DOMAINS,
    build_checkpoint,
    build_offline_sham_fixture,
    restore_checkpoint,
    validate_checkpoint,
    validate_offline_sham_fixture,
)


def _checkpoint(*, statuses=None):
    statuses = statuses or {domain: "missing" for domain in STATE_DOMAINS}
    payloads = {
        domain: {"fixture": domain}
        for domain, status in statuses.items() if status == "verified"
    }
    return build_checkpoint(
        checkpoint_id="cp-initial-01",
        task_id="warehouse-demo",
        task_contract_version="goal-v2",
        scene_version="warehouse-v1",
        code_version="code-test",
        asset_version="asset-test",
        entity_keys=["robot-a", "crate-a"],
        condition_id="healthy",
        pair_repeat="s01",
        trigger_event_key="episode_started",
        state_domains=statuses,
        state_payloads=payloads,
        clock={"sim_time_s": 0.0, "decision_step": 0, "resolution_s": 0.005},
    )


def test_checkpoint_is_versioned_and_raw_only_even_with_symbolic_payloads():
    checkpoint = _checkpoint()
    assert checkpoint["schema"] == "eai.benchmark.goal_checkpoint"
    assert checkpoint["version"] == 1
    assert checkpoint["raw_only"] is True
    assert checkpoint["restore_status"] == "unverified"
    assert checkpoint["strict_branch_comparison_eligible"] is False
    assert validate_checkpoint(checkpoint)["sha256"] == checkpoint["sha256"]


def test_verified_domain_requires_payload_and_missing_domain_cannot_carry_payload():
    statuses = {domain: "missing" for domain in STATE_DOMAINS}
    statuses["visible_history"] = "verified"
    with pytest.raises(ValueError, match="requires a payload"):
        build_checkpoint(
            checkpoint_id="cp", task_id="task", task_contract_version="v1",
            scene_version="scene", code_version="code", asset_version="asset",
            entity_keys=["robot"], condition_id="healthy", pair_repeat="s01",
            trigger_event_key="e0", state_domains=statuses,
        )
    checkpoint = _checkpoint(statuses=statuses)
    tampered = deepcopy(checkpoint)
    tampered["state_domains"]["visible_history"] = "missing"
    from demo.benchmark.goal_checkpoint import _digest
    tampered["sha256"] = _digest({key: value for key, value in tampered.items() if key != "sha256"})
    with pytest.raises(ValueError, match="missing domain"):
        validate_checkpoint(tampered)


def test_checkpoint_digest_and_payload_hash_fail_closed():
    checkpoint = _checkpoint()
    tampered = deepcopy(checkpoint)
    tampered["clock"]["sim_time_s"] = 1.0
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_checkpoint(tampered)

    statuses = {domain: "missing" for domain in STATE_DOMAINS}
    statuses["visible_history"] = "verified"
    checkpoint = _checkpoint(statuses=statuses)
    tampered = deepcopy(checkpoint)
    tampered["state_payloads"]["visible_history"] = {"fixture": "changed"}
    tampered["sha256"] = __import__("demo.benchmark.goal_checkpoint", fromlist=["_digest"])._digest(
        {key: value for key, value in tampered.items() if key != "sha256"})
    with pytest.raises(ValueError, match="payload digest mismatch"):
        validate_checkpoint(tampered)


def test_restore_refuses_symbolic_checkpoint():
    with pytest.raises(RuntimeError, match="native environment"):
        restore_checkpoint(_checkpoint())


def test_offline_sham_fixture_keeps_same_snapshot_and_keyed_draws():
    fixture = build_offline_sham_fixture(
        _checkpoint(), seed=17, process="communication", entity="robot-a",
        event_keys=["message-1", "message-2", "message-3"],
        branch_order={"fact": ["message-1", "message-2", "message-3"],
                      "sham": ["message-3", "message-1", "message-2"]},
    )
    assert fixture["fixture_only"] is True
    assert fixture["restore_status"] == "unverified_for_protocol_replay"
    assert validate_offline_sham_fixture(fixture)["sha256"] == fixture["sha256"]
    assert fixture["rng"]["branch_draws"]["fact"] == fixture["rng"]["branch_draws"]["sham"]
    assert fixture["branches"]["fact"]["state_sha256"] == fixture["branches"]["sham"]["state_sha256"]


def test_sham_fixture_rejects_branch_snapshot_or_draw_mismatch():
    fixture = build_offline_sham_fixture(
        _checkpoint(), seed=1, process="noise", entity="robot-a", event_keys=["e1", "e2"])
    tampered = deepcopy(fixture)
    tampered["rng"]["branch_draws"]["sham"]["e1"] += 0.1
    from demo.benchmark.goal_checkpoint import _digest
    tampered["sha256"] = _digest({key: value for key, value in tampered.items() if key != "sha256"})
    with pytest.raises(ValueError, match="random draws differ"):
        validate_offline_sham_fixture(tampered)


def test_branch_order_must_be_a_permutation():
    with pytest.raises(ValueError, match="permutations"):
        build_offline_sham_fixture(
            _checkpoint(), seed=1, process="noise", entity="robot-a",
            event_keys=["e1", "e2"], branch_order={"fact": ["e1"], "sham": ["e2", "e1"]})
