from copy import deepcopy
import json
import uuid

import pytest


def test_m20_candidate_selections_bind_public_anchors_to_native_controller():
    from demo.restaurant_benchmark import candidate_admission, config

    first = candidate_admission._selection("restaurant_m20_1")
    second = candidate_admission._selection("restaurant_m20_2")
    for selection, spawn_key in ((first, "m20_1"), (second, "m20_2")):
        assert selection["scene_key"] == config.BENCHMARK_SCENE_KEY
        assert len(selection["robots"]) == 1
        robot = selection["robots"][0]
        assert robot["type"] == "m20"
        assert robot["controller"] == {
            "mode": "default",
            "cfg": "M20_ROUGH_RSL_CFG",
        }
        assert robot["spawn_pose"] == {
            "position": list(config.BENCHMARK_ROBOT_SPAWN_XYZ_M[spawn_key]),
            "rotation": [1.0, 0.0, 0.0, 0.0],
        }


def test_m20_candidate_identity_and_thresholds_are_explicit():
    from demo.restaurant_benchmark import candidate_admission

    assert tuple(candidate_admission._CANDIDATES) == (
        "restaurant_m20_1",
        "restaurant_m20_2",
    )
    for candidate_id in candidate_admission._CANDIDATES:
        spec = candidate_admission._candidate(candidate_id)
        assert spec["runtime_agent"] == "m20_1"
        assert spec["support_bodies"] == [
            "fl_wheel",
            "fr_wheel",
            "hl_wheel",
            "hr_wheel",
        ]
    assert candidate_admission._thresholds() == {
        "settle_steps": 120,
        "sample_steps": 240,
        "minimum_contact_sample_ratio": 0.80,
        "median_weight_support_ratio": [0.70, 1.30],
        "maximum_xy_excursion_m": 0.05,
        "maximum_z_excursion_m": 0.02,
        "maximum_tilt_rad": 0.10,
        "spawn_binding_tolerance_m": 0.0001,
    }
    with pytest.raises(ValueError, match="unsupported Restaurant candidate"):
        candidate_admission._candidate("restaurant_go2_1")


def _request(monkeypatch):
    from demo.restaurant_benchmark import candidate_admission, config

    source_files = {"/tmp/candidate_admission.py": "a" * 64}
    monkeypatch.setattr(
        candidate_admission, "_source_files", lambda _candidate_id: source_files
    )
    candidate_id = "restaurant_m20_1"
    return {
        "schema": "eai.restaurant.candidate_admission_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": config.BENCHMARK_SCENE_KEY,
        "candidate_id": candidate_id,
        "candidate_spec": candidate_admission._candidate(candidate_id),
        "selection": candidate_admission._selection(candidate_id),
        "requested_device": "cpu",
        "thresholds": candidate_admission._thresholds(),
        "source_sha256": config.SOURCE_SHA256,
        "product_validation": {"validated": True},
        "source_files": source_files,
        "release_gates": {
            "navigation_ready": False,
            "execution_validated": False,
            "scoreable": False,
        },
        "execution_validated": False,
        "scoreable": False,
    }


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("candidate_id", "restaurant_go2_1"),
        ("requested_device", "cuda"),
        ("thresholds", {}),
        ("execution_validated", True),
        ("scoreable", True),
    ),
)
def test_candidate_admission_request_fails_closed(monkeypatch, mutation, value):
    from demo.restaurant_benchmark import candidate_admission

    request = _request(monkeypatch)
    request[mutation] = value
    with pytest.raises(
        (ValueError, KeyError), match="Restaurant candidate|unsupported Restaurant"
    ):
        candidate_admission._validate_request_contract(request)


def test_candidate_admission_request_rejects_selection_or_source_drift(monkeypatch):
    from demo.restaurant_benchmark import candidate_admission

    request = _request(monkeypatch)
    candidate_admission._validate_request_contract(request)
    candidate_admission._validate_request_contract(json.loads(json.dumps(request)))

    changed_selection = deepcopy(request)
    changed_selection["selection"]["robots"][0]["spawn_pose"]["position"][0] += 1.0
    with pytest.raises(ValueError, match="invalid Restaurant candidate"):
        candidate_admission._validate_request_contract(changed_selection)

    changed_sources = deepcopy(request)
    changed_sources["source_files"] = {"/tmp/other.py": "b" * 64}
    with pytest.raises(ValueError, match="invalid Restaurant candidate"):
        candidate_admission._validate_request_contract(changed_sources)


def test_unadmitted_m20_candidates_do_not_change_release_roster():
    from demo.restaurant_benchmark import config

    assert config.BENCHMARK_NATIVE_VALIDATED_ROBOT_IDS == ("carter_1", "carter_2")
    assert config.EXECUTION_VALIDATED is False
    assert config.SCOREABLE is False
