"""Readiness evidence permits alternate plans without weakening causality."""

from copy import deepcopy
import hashlib
import json

import pytest

from demo.restaurant_benchmark.service_backend import (
    RestaurantReadinessBackend, evaluate_readiness_receipt,
)
from demo.restaurant_benchmark.service_backend_v2 import (
    RestaurantReadinessBackendV2, evaluate_readiness_receipt_v2,
)


def _observe(service, name, region, step, producer="carter_1"):
    return service.record_observation(
        observation_id=name, region_id=region, simulation_step=step,
        time_s=step * 0.1, producer_id=producer, evidence_digest=f"native:{name}")


def _review(snapshot):
    return evaluate_readiness_receipt_v2(
        episode_id=snapshot["episode_id"], receipt=snapshot["receipt"],
        observations=snapshot["observations"],
        relay_publications=snapshot["relay_publications"])


def _chain(*, reverse=False, second_producer="carter_1", simultaneous=False):
    service = RestaurantReadinessBackendV2(episode_id="test")
    first, second = (("dining_service", "kitchen_service") if reverse
                     else ("kitchen_service", "dining_service"))
    _observe(service, "first", first, 1)
    _observe(service, "second", second, 1 if simultaneous else 2, second_producer)
    service.publish_relay(message_id="relay-old", simulation_step=3, time_s=0.3,
                          observation_refs=["first", "second"])
    # A second inspection does not invalidate an already valid earlier one.
    _observe(service, "repeat", first, 4, second_producer)
    service.publish_relay(message_id="relay-retry", simulation_step=5, time_s=0.5,
                          observation_refs=["repeat", "second"])
    service.commit_readiness_receipt(
        receipt_id="ready", simulation_step=6, time_s=0.6,
        observation_refs=["second", "first"], relay_message_id="relay-old")
    return service


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("second_producer", ["carter_1", "carter_2"])
@pytest.mark.parametrize("simultaneous", [False, True])
def test_alternate_order_agent_reuse_reinspection_and_relay_retries(
        reverse, second_producer, simultaneous):
    report = _review(_chain(reverse=reverse, second_producer=second_producer,
                            simultaneous=simultaneous).snapshot())
    assert report["validated"]
    assert report["selected_observation_refs"] == ["first", "second"]
    assert report["selected_relay_message_id"] == "relay-old"
    assert report["observation_count"] == 3
    assert report["relay_publication_count"] == 2
    assert report["native_execution_verified"] is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_bad_observation_timestamp_does_not_mutate_clock(value):
    service = RestaurantReadinessBackendV2(episode_id="test")
    before = service.snapshot()
    with pytest.raises(ValueError):
        service.record_observation(
            observation_id="bad", region_id="kitchen_service", simulation_step=1,
            time_s=value, producer_id="carter_1", evidence_digest="native")
    assert service.snapshot() == before
    _observe(service, "valid", "kitchen_service", 1)


def test_rejected_attempts_do_not_consume_ids_or_native_time():
    service = RestaurantReadinessBackendV2(episode_id="test")
    _observe(service, "k", "kitchen_service", 1)
    _observe(service, "d", "dining_service", 2)
    before = service.snapshot()
    with pytest.raises(ValueError, match="duplicate observation_id"):
        _observe(service, "k", "kitchen_service", 100)
    assert service.snapshot() == before
    with pytest.raises(ValueError, match="one recorded observation"):
        service.publish_relay(message_id="r", simulation_step=100, time_s=10,
                              observation_refs=["k", "absent"])
    assert service.snapshot() == before
    service.publish_relay(message_id="r", simulation_step=3, time_s=0.3,
                          observation_refs=["k", "d"])
    before = service.snapshot()
    with pytest.raises(ValueError, match="duplicate relay"):
        service.publish_relay(message_id="r", simulation_step=100, time_s=10,
                              observation_refs=["k", "d"])
    assert service.snapshot() == before
    with pytest.raises(ValueError, match="precede terminal receipt"):
        service.commit_readiness_receipt(
            receipt_id="ready", simulation_step=3, time_s=0.3,
            observation_refs=["k", "d"], relay_message_id="r")
    assert service.snapshot() == before
    service.commit_readiness_receipt(receipt_id="ready", simulation_step=4, time_s=0.4,
                                     observation_refs=["d", "k"], relay_message_id="r")
    assert _review(service.snapshot())["validated"]


@pytest.mark.parametrize("target", ["selected_observation", "extra_observation", "relay", "receipt"])
def test_foreign_episode_cannot_hide_in_unselected_evidence(target):
    snapshot = _chain().snapshot()
    row = {"selected_observation": snapshot["observations"]["first"],
           "extra_observation": snapshot["observations"]["repeat"],
           "relay": snapshot["relay_publications"][1],
           "receipt": snapshot["receipt"]}[target]
    row["episode_id"] = "foreign"
    with pytest.raises(ValueError, match="episode_id"):
        _review(snapshot)


@pytest.mark.parametrize("mutation", [
    "selected_observation_digest", "selected_producer", "receipt_time", "relay_time",
    "missing_observation", "duplicate_refs", "misfiled_observation", "duplicate_relay",
    "noncausal_extra_relay", "nonfinite_extra_observation", "wrong_relay_selection",
])
def test_archive_rejects_forgery_and_invalid_references(mutation):
    snapshot = _chain().snapshot()
    observations, receipt = snapshot["observations"], snapshot["receipt"]
    publications = snapshot["relay_publications"]
    if mutation == "selected_observation_digest":
        observations["first"]["evidence_digest"] = "forged"
    elif mutation == "selected_producer":
        observations["first"]["producer_id"] = "other"
    elif mutation == "receipt_time":
        receipt["time_s"] += 0.01
    elif mutation == "relay_time":
        publications[0]["time_s"] += 0.01
    elif mutation == "missing_observation":
        del observations["second"]
    elif mutation == "duplicate_refs":
        receipt["observation_refs"] = ["first", "first"]
    elif mutation == "misfiled_observation":
        observations["wrong"] = observations.pop("first")
    elif mutation == "duplicate_relay":
        publications.append(deepcopy(publications[0]))
    elif mutation == "noncausal_extra_relay":
        publications[1]["simulation_step"] = 1
        publications[1]["time_s"] = 0.1
    elif mutation == "nonfinite_extra_observation":
        observations["repeat"]["time_s"] = float("nan")
    elif mutation == "wrong_relay_selection":
        receipt["relay_message_id"] = "relay-retry"
    with pytest.raises(ValueError):
        _review(snapshot)


def test_same_tick_is_allowed_only_for_independent_observations():
    service = RestaurantReadinessBackendV2(episode_id="test")
    _observe(service, "k", "kitchen_service", 1)
    _observe(service, "d", "dining_service", 1)
    before = service.snapshot()
    with pytest.raises(ValueError, match="follow its observations"):
        service.publish_relay(message_id="r", simulation_step=1, time_s=0.1,
                              observation_refs=["k", "d"])
    assert service.snapshot() == before


def test_unselected_reinspection_at_receipt_tick_does_not_create_a_dependency():
    service = RestaurantReadinessBackendV2(episode_id="test")
    _observe(service, "k", "kitchen_service", 1)
    _observe(service, "d", "dining_service", 1)
    service.publish_relay(message_id="r", simulation_step=2, time_s=0.2,
                          observation_refs=["k", "d"])
    _observe(service, "unused", "kitchen_service", 3)
    service.commit_readiness_receipt(
        receipt_id="ready", simulation_step=3, time_s=3 * 0.1,
        observation_refs=["d", "k"], relay_message_id="r")
    assert _review(service.snapshot())["validated"]


def test_service_is_terminal_and_snapshots_do_not_alias():
    service = _chain()
    snapshot = service.snapshot()
    snapshot["observations"]["first"]["producer_id"] = "changed"
    assert _review(service.snapshot())["validated"]
    with pytest.raises(ValueError, match="terminated"):
        _observe(service, "late", "kitchen_service", 7)


@pytest.mark.parametrize("step,time", [(1, 0.11), (4, 0.1)])
def test_unused_evidence_cannot_contradict_shared_native_clock(step, time):
    snapshot = _chain().snapshot()
    snapshot["observations"]["extra"] = {
        **snapshot["observations"]["first"], "observation_id": "extra",
        "simulation_step": step, "time_s": time,
    }
    with pytest.raises(ValueError, match="step/time mapping"):
        _review(snapshot)


def test_legacy_contract_is_not_silently_upgraded():
    legacy = RestaurantReadinessBackend(episode_id="test")
    _observe(legacy, "first", "kitchen_service", 1)
    with pytest.raises(ValueError, match="already recorded"):
        _observe(legacy, "again", "kitchen_service", 2)
    snapshot = _chain().snapshot()
    with pytest.raises(ValueError, match="invalid Restaurant readiness receipt"):
        evaluate_readiness_receipt(
            episode_id="test", receipt=snapshot["receipt"],
            observations=snapshot["observations"],
            relay_publications=snapshot["relay_publications"])


@pytest.mark.parametrize("target", ["observation", "publication", "receipt"])
@pytest.mark.parametrize("mutation", ["unknown", "missing"])
def test_record_fields_are_closed_even_with_a_recomputed_receipt_digest(target, mutation):
    snapshot = _chain().snapshot()
    row, required = {
        "observation": (snapshot["observations"]["repeat"], "producer_id"),
        "publication": (snapshot["relay_publications"][1], "message_id"),
        "receipt": (snapshot["receipt"], "relay_digest"),
    }[target]
    if mutation == "unknown":
        row["native_execution_verified"] = True
    else:
        del row[required]
    receipt = snapshot["receipt"]
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    receipt["receipt_digest"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match="unknown or missing keys"):
        _review(snapshot)
