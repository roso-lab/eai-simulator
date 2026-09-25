from demo.benchmark.goal_restaurant_reassessment_collection import (
    build_extension_input,
    collect_reassessment_epoch_inputs,
    review_reassessment_collection,
)


def _history_row(epoch, trigger, at_s, *, sequence=None, agents=None,
                 roster_version=None, remaining=None):
    return {
        "assessment_id": f"as-{epoch}", "assessment_epoch": epoch,
        "trigger_event_type": trigger,
        "trigger_event_sequence": sequence if sequence is not None else 100 + epoch,
        "triggered_at_s": at_s,
        "current_agent_ids": agents or [
            "restaurant_carter_1", "restaurant_carter_2", "restaurant_iris_1"],
        "roster_version": roster_version if roster_version is not None else 1 + epoch,
        "capability_versions": {
            "restaurant_carter_1": 1, "restaurant_carter_2": 1,
            "restaurant_iris_1": 1},
        "resource_versions": {"service_time_budget": 1},
        "deployment_change_versions": {},
    }


def _session(history):
    return {"episode_id": "ep-r", "report": {
        "episode_id": "ep-r",
        "deployment_assessment": {"history": history}}}


def test_collection_replays_runtime_epochs_and_skips_epoch_zero():
    collection = collect_reassessment_epoch_inputs(
        _session([_history_row(0, "initial_deployment", 0.0),
                  _history_row(1, "member_exit", 30.0),
                  _history_row(2, "capability_change", 80.0)]),
        episode_id="ep-r")
    assert collection["schema"] == "eai.benchmark.restaurant_reassessment_collection"
    assert collection["epoch_count"] == 2
    domains = collection["run_state_domains"]
    assert [row["assessment_epoch"] for row in domains] == [1, 2]
    assert domains[0]["trigger_event_type"] == "member_exit"
    assert domains[0]["measurement_id"] == "restaurant_runtime_epoch_1"
    assert domains[0]["max_active_agents"] == 3
    assert collection["scoreable"] is False


def test_collection_budget_is_derived_from_trigger_time():
    collection = collect_reassessment_epoch_inputs(
        _session([_history_row(1, "milestone_state_change", 72.18)]),
        episode_id="ep-r")
    domain = collection["run_state_domains"][0]
    assert domain["time_s"] == 72.18
    assert domain["remaining_time_ms"] == 240_000 - 72_180


def test_clean_collection_passes_review_with_stable_digest():
    collection = collect_reassessment_epoch_inputs(
        _session([_history_row(1, "member_exit", 30.0),
                  _history_row(2, "capability_change", 80.0)]),
        episode_id="ep-r")
    review = review_reassessment_collection(collection, expected_episode_id="ep-r")
    assert review["status"] == "verified"
    assert review["quality_failures"] == []
    assert review["review_digest"]
    again = review_reassessment_collection(collection, expected_episode_id="ep-r")
    assert again["review_digest"] == review["review_digest"]


def test_review_rejects_non_monotonic_triggers():
    collection = collect_reassessment_epoch_inputs(
        _session([_history_row(2, "member_exit", 80.0, sequence=102),
                  _history_row(1, "capability_change", 30.0, sequence=101)]),
        episode_id="ep-r")
    # epochs are sorted ascending on collect; simulate broken sequence by
    # tampering the collected order values after collection.
    collection["run_state_domains"][0]["trigger_event_sequence"] = 999
    collection["run_state_domains"][1]["trigger_event_sequence"] = 5
    review = review_reassessment_collection(collection)
    assert review["status"] == "failed"
    assert any(item.startswith("trigger_sequence_not_increasing")
               for item in review["quality_failures"])
    assert review["review_digest"] is None


def test_review_rejects_budget_inconsistency():
    collection = collect_reassessment_epoch_inputs(
        _session([_history_row(1, "member_exit", 30.0)]), episode_id="ep-r")
    collection["run_state_domains"][0]["remaining_time_ms"] = 123
    review = review_reassessment_collection(collection)
    assert "budget_inconsistent:1" in review["quality_failures"]


def test_review_rejects_unknown_trigger_and_missing_versions():
    collection = collect_reassessment_epoch_inputs(
        _session([_history_row(1, "member_exit", 30.0)]), episode_id="ep-r")
    collection["run_state_domains"][0]["trigger_event_type"] = "spontaneous"
    collection["run_state_domains"][0]["capability_versions"] = None
    review = review_reassessment_collection(collection)
    assert "unknown_trigger:1" in review["quality_failures"]
    assert "version_field_missing:1:capability_versions" in review[
        "quality_failures"]


def test_empty_collection_review_is_explicit_failure():
    collection = collect_reassessment_epoch_inputs(
        _session([_history_row(0, "initial_deployment", 0.0)]), episode_id="ep-r")
    review = review_reassessment_collection(collection)
    assert review["status"] == "empty_collection"
    assert review["quality_failures"] == ["no_runtime_epochs_collected"]


def test_extension_input_requires_verified_review_and_binds_certificate():
    import pytest

    history = [_history_row(1, "member_exit", 30.0)]
    collection = collect_reassessment_epoch_inputs(
        _session(history), episode_id="ep-r")
    review = review_reassessment_collection(collection)
    with pytest.raises(ValueError):
        build_extension_input(collection, review, certificate_sha256="short")
    failed = dict(review)
    failed["status"] = "failed"
    with pytest.raises(ValueError):
        build_extension_input(collection, failed, certificate_sha256="a" * 64)
    extension_input = build_extension_input(
        collection, review, certificate_sha256="b" * 64)
    assert extension_input["bound_certificate_sha256"] == "b" * 64
    assert extension_input["episode_id"] == "ep-r"
    assert extension_input["qualification"]["review_digest"] == (
        review["review_digest"])
    assert len(extension_input["run_state_domains"]) == 1
    assert extension_input["scoreable"] is False
