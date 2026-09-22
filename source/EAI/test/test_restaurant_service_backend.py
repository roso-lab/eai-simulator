"""Causal and episode binding checks for the Restaurant service boundary."""

import pytest

from demo.restaurant_benchmark.service_backend import (
    RestaurantReadinessBackend,
    evaluate_readiness_receipt,
)


def test_relay_publication_does_not_create_business_receipt():
    backend = RestaurantReadinessBackend(episode_id="episode-1")
    backend.publish_relay(
        message_id="relay-1", simulation_step=1, time_s=1.0,
        observation_refs=["kitchen-observation", "dining-observation"],
    )
    assert backend.snapshot()["receipt"] is None


def test_receipt_requires_both_observations_and_is_episode_bound():
    backend = RestaurantReadinessBackend(episode_id="episode-1")
    kitchen = backend.record_observation(
        region_id="kitchen_service", observation_id="kitchen-observation",
        simulation_step=10, time_s=1.0, producer_id="carter_1",
        evidence_digest="native:kitchen",
    )
    dining = backend.record_observation(
        region_id="dining_service", observation_id="dining-observation",
        simulation_step=20, time_s=2.0, producer_id="carter_1",
        evidence_digest="native:dining",
    )
    with pytest.raises(ValueError, match="both kitchen and dining"):
        backend.commit_readiness_receipt(
            receipt_id="receipt-1", simulation_step=30, time_s=3.0,
            observation_refs=[kitchen["observation_id"]],
        )
    with pytest.raises(ValueError, match="prior relay publication"):
        backend.commit_readiness_receipt(
            receipt_id="receipt-1", simulation_step=30, time_s=3.0,
            observation_refs=[kitchen["observation_id"], dining["observation_id"]],
        )
    backend.publish_relay(
        message_id="relay-1", simulation_step=25, time_s=2.5,
        observation_refs=[kitchen["observation_id"], dining["observation_id"]],
    )
    receipt = backend.commit_readiness_receipt(
        receipt_id="receipt-1", simulation_step=30, time_s=3.0,
        observation_refs=[kitchen["observation_id"], dining["observation_id"]],
    )
    assert evaluate_readiness_receipt(
        episode_id="episode-1",
        receipt=receipt,
        observations={kitchen["observation_id"]: kitchen, dining["observation_id"]: dining},
    )["validated"] is True


def test_receipt_rejects_wrong_episode_and_post_step_time_fill_in():
    backend = RestaurantReadinessBackend(episode_id="episode-1")
    kitchen = backend.record_observation(
        region_id="kitchen_service", observation_id="kitchen-observation",
        simulation_step=2, time_s=2.0, producer_id="carter_1",
        evidence_digest="native:kitchen",
    )
    backend.record_observation(
        region_id="dining_service", observation_id="dining-observation",
        simulation_step=3, time_s=3.0, producer_id="carter_1",
        evidence_digest="native:dining",
    )
    with pytest.raises(ValueError, match="increase monotonically"):
        backend.commit_readiness_receipt(
            receipt_id="receipt-1", simulation_step=4, time_s=3.0,
            observation_refs=[kitchen["observation_id"], "dining-observation"],
        )
    with pytest.raises(ValueError, match="episode_id mismatch"):
        evaluate_readiness_receipt(
            episode_id="episode-2",
            receipt={"schema": "eai.restaurant.readiness_service.v1",
                     "kind": "readiness_receipt", "episode_id": "episode-1",
                     "observation_refs": [], "simulation_step": 4, "time_s": 4.0,
                     "receipt_id": "receipt-1"},
            observations={},
        )
