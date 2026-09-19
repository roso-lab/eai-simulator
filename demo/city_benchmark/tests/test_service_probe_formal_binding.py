"""Formal episode binding consumption by the City service probe.

Offline checks only: no Isaac, GPU, ROS, network or provider assets. They
cover the ``--episode-binding`` prepare path, the fail-closed binding gate,
the binding-derived geometry, and the declared-vs-readback review that turns
``formal_episode_binding`` true. Runtime dispatch is verified against a fake
simulator boundary, never a real session.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType
import sys

import pytest

from demo.benchmark.provenance import canonical_sha256
from demo.city_benchmark import service_probe
from demo.city_benchmark.contracts import build_episode_binding
from demo.city_benchmark.task_logic import default_deadline_policy


def _binding_map():
    return {
        "surface_heights_m": {"sidewalks": 0.3, "ground": 0.22},
        "pedestrian_routes": [
            {"id": f"p{index}", "points": [[float(index), 6.0], [float(index) + 3.0, 6.0]]}
            for index in range(4)
        ],
        "bindings": [
            {"id": "assembly_candidate_001", "candidate_radius_m": 0.75,
             "geometry": {"type": "Point", "coordinates": [10.0, 6.0]}},
            {"id": "assembly_candidate_002", "candidate_radius_m": 0.75,
             "geometry": {"type": "Point", "coordinates": [10.0, 7.0]}},
        ],
        "regions": [
            {"id": "road_surface", "kind": "road_network", "public_description": "城市道路网络",
             "access_modes": ["ground_navigation", "aerial_navigation"], "resource_capacity": 2},
            {"id": "assembly_candidate_001", "kind": "assembly_candidate",
             "public_description": "候选集合位置", "access_modes": ["ground_navigation"],
             "resource_capacity": 1},
            {"id": "assembly_candidate_002", "kind": "assembly_candidate",
             "public_description": "候选集合位置", "access_modes": ["ground_navigation"],
             "resource_capacity": 1},
        ],
    }


def _binding():
    return build_episode_binding(_binding_map(), people_count=4, seed=7)


def _write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Prepare: default diagnostic contract stays, formal mode freezes the binding.
# ---------------------------------------------------------------------------

def test_prepare_without_binding_keeps_the_diagnostic_contract(tmp_path):
    result = service_probe.prepare_probe(tmp_path, device="cpu", seed=17)
    stored = json.loads((tmp_path / "service_probe_prepare.json").read_text())
    assert stored == result
    assert stored["formal_episode_binding"] is False
    assert stored["geometry_scope"] == "runtime_selected_candidate_diagnostic"
    assert "episode_binding" not in stored
    assert stored["requires"] == ["scene_query_support", "cpu_current_physx_queries",
                                  "one_city_person", "one_following_vehicle"]


def test_prepare_with_standalone_binding_document_enters_formal_mode(tmp_path):
    document = _write_json(tmp_path / "bind.json", _binding())
    result = service_probe.prepare_probe(tmp_path, device="cpu", seed=17,
                                         episode_binding=document)
    assert result["formal_episode_binding"] is True
    assert result["geometry_scope"] == "author_frozen_episode_binding"
    assert result["episode_binding"] == _binding()
    assert result["episode_binding_sha256"] == canonical_sha256(_binding())
    assert result["episode_binding_source_kind"] == "binding_document"
    assert result["episode_binding_map_review"] is None
    assert result["scoreable"] is False
    assert result["requires"] == ["scene_query_support", "cpu_current_physx_queries",
                                  "bound_roster_spawn_from_episode_binding",
                                  "all_active_following_vehicles_bound_at_instantiation"]


def test_prepare_with_authoring_private_reviews_binding_against_packaged_map(tmp_path):
    package = tmp_path / "prepared"
    _write_json(package / "authoring" / "private.json",
                {"schema": "eai.city.authoring_private", "episode_binding": _binding()})
    _write_json(package / "release" / "maps" / "semantic_map.json", _binding_map())
    result = service_probe.prepare_probe(tmp_path / "probe", episode_binding=(
        package / "authoring" / "private.json"))
    assert result["episode_binding_source_kind"] == "authoring_private"
    assert result["episode_binding_map_review"]["validated"] is True
    assert result["episode_binding_map_review"]["runtime_admission_pending"] is True


def test_prepare_with_authoring_private_without_map_skips_map_review(tmp_path):
    package = tmp_path / "prepared"
    _write_json(package / "authoring" / "private.json", {"episode_binding": _binding()})
    result = service_probe.prepare_probe(tmp_path / "probe", episode_binding=(
        package / "authoring" / "private.json"))
    assert result["episode_binding_map_review"] is None


def test_prepare_rejects_binding_that_drifted_from_the_packaged_map(tmp_path):
    package = tmp_path / "prepared"
    binding = _binding()
    binding["assembly_capacity"][0]["center_xy_m"] = [42.0, 6.0]
    _write_json(package / "authoring" / "private.json", {"episode_binding": binding})
    _write_json(package / "release" / "maps" / "semantic_map.json", _binding_map())
    with pytest.raises(ValueError, match="episode binding rejected"):
        service_probe.prepare_probe(tmp_path / "probe", episode_binding=(
            package / "authoring" / "private.json"))


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(schema="eai.city.episode_binding_v2"),
    lambda b: b.update(version=2),
    lambda b: b.update(status="runtime_selected_candidate_diagnostic"),
    lambda b: b.pop("response_service_endpoint"),
    lambda b: b["affected_person_roster"].pop(),
    lambda b: b["affected_person_roster"][0].update(assembly_region_id=""),
    lambda b: b["assembly_capacity"][0].update(capacity_people=9),
    lambda b: b["dynamic_hazard_boundaries"].update(exclusion_half_extents_m=[0.0, 4.0]),
    lambda b: b["safe_ingress_control_points"].update(candidate_offsets_m=[]),
    lambda b: b["following_traffic_response_window"].update(control_modes=["hold"]),
    lambda b: b["response_service_endpoint"].update(service_id=""),
])
def test_prepare_rejects_non_author_frozen_bindings(mutation, tmp_path):
    binding = _binding()
    mutation(binding)
    document = _write_json(tmp_path / "bind.json", binding)
    with pytest.raises(ValueError, match="invalid episode binding"):
        service_probe.prepare_probe(tmp_path, episode_binding=document)


def test_cli_binds_only_to_prepare_and_writes_the_formal_record(tmp_path, capsys):
    document = _write_json(tmp_path / "bind.json", _binding())
    code = service_probe.main(["--prepare", "--output-root", str(tmp_path / "probe"),
                               "--episode-binding", str(document)])
    assert code == 0
    stored = json.loads((tmp_path / "probe" / "service_probe_prepare.json").read_text())
    assert stored["formal_episode_binding"] is True
    with pytest.raises(SystemExit) as exc:
        service_probe.main(["--execute", "--output-root", str(tmp_path / "probe"),
                            "--episode-binding", str(document)])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Execute-side binding gate (fail closed, offline).
# ---------------------------------------------------------------------------

def _formal_prepared(binding=None):
    binding = deepcopy(binding or _binding())
    return {"schema": service_probe.SCHEMA, "status": "prepared", "seed": 17,
            "device": "cpu", "source_hashes": {}, "formal_episode_binding": True,
            "geometry_scope": "author_frozen_episode_binding",
            "episode_binding": binding,
            "episode_binding_sha256": canonical_sha256(binding)}


def test_prepared_binding_gate_is_fail_closed():
    assert service_probe._prepared_binding({"seed": 17, "device": "cpu"}) is None
    prepared = _formal_prepared()
    assert service_probe._prepared_binding(prepared) == prepared["episode_binding"]
    drifted = _formal_prepared()
    drifted["episode_binding"]["seed"] = 8
    with pytest.raises(RuntimeError, match="changed after prepare"):
        service_probe._prepared_binding(drifted)
    unflagged = _formal_prepared()
    unflagged["formal_episode_binding"] = False
    with pytest.raises(RuntimeError, match="without the formal flag"):
        service_probe._prepared_binding(unflagged)
    hollow = _formal_prepared()
    hollow.pop("episode_binding")
    with pytest.raises(RuntimeError, match="carries none"):
        service_probe._prepared_binding(hollow)


def test_formal_execute_enters_session_before_city_or_usd_imports(monkeypatch, tmp_path):
    class StopAfterLifecycleCheck(RuntimeError):
        pass

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    @contextmanager
    def open_session(config):
        assert config.kwargs["selection_data"] == service_probe._selection(17)
        assert "demo.city_benchmark.scene.main" not in sys.modules
        assert "demo.city_benchmark.scene.runtime" not in sys.modules
        raise StopAfterLifecycleCheck
        yield

    fake_simulator = ModuleType("simulator")
    fake_simulator.SimulatorLaunchConfig = Config
    fake_simulator.open_simulator_session = open_session
    monkeypatch.setitem(sys.modules, "simulator", fake_simulator)
    for name in ("demo.city_benchmark.scene.main", "demo.city_benchmark.scene.runtime",
                 "demo.city_benchmark.scene.usd_scene"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    with pytest.raises(StopAfterLifecycleCheck):
        service_probe._execute(tmp_path, _formal_prepared(), run_id="episode",
                               headless=True)
    # The diagnostic path is untouched when the prepare record binds nothing.
    with pytest.raises(StopAfterLifecycleCheck):
        service_probe._execute(tmp_path, {"seed": 17, "device": "cpu"},
                               run_id="episode", headless=True)


def test_formal_report_guard_fails_closed_on_blocked_readback(tmp_path):
    report = {"status": "starting", "formal_episode_binding": True,
              "formal_binding_review": {"formal_episode_binding": True, "mismatches": []},
              "full_city_service": "formal_binding_execution_incomplete",
              "trace": {"geometry": {"hazard": "measured"}}}
    path = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="later phase failed"):
        with service_probe._service_report_guard(
                path, report, pending_label="formal_binding_execution_incomplete"):
            raise RuntimeError("later phase failed")
    stored = json.loads(path.read_text())
    assert stored["status"] == "blocked"
    assert stored["full_city_service"] == "formal_binding_execution_incomplete"
    assert stored["formal_episode_binding"] is False
    assert stored["formal_binding_review"]["formal_episode_binding"] is False
    assert stored["formal_binding_review"]["mismatches"] == [
        "execution_blocked_before_completed_readback"]


# ---------------------------------------------------------------------------
# Binding-derived geometry (offline helper level).
# ---------------------------------------------------------------------------

def test_binding_roster_specs_rebuild_validated_response_people():
    specs = service_probe._binding_roster_specs(_binding())
    roster = _binding()["affected_person_roster"]
    assert [spec.person_id for spec in specs] == [row["person_id"] for row in roster]
    by_id = {row["person_id"]: row for row in roster}
    for spec in specs:
        row = by_id[spec.person_id]
        assert spec.start_xyz == tuple(row["start_xyz"])
        assert spec.evacuation_route[-1] == tuple(row["evacuation_route"][-1])
        assert spec.assembly_region_id == row["assembly_region_id"]


def test_binding_assembly_regions_use_capacity_and_envelope_containment():
    binding = _binding()
    targets = service_probe._binding_assembly_targets(binding)
    half = {row["person_id"]: (0.45, 0.31, 0.94) for row in binding["affected_person_roster"]}
    offset = {row["person_id"]: (0.075, -0.006, 0.99)
              for row in binding["affected_person_roster"]}
    regions = service_probe._binding_assembly_regions(binding, targets, half, offset)
    for capacity in binding["assembly_capacity"]:
        region = regions[capacity["region_id"]]
        target = targets[capacity["region_id"]]
        assert region["capacity"] == capacity["capacity_people"]
        for axis in range(2):
            # Authored candidate radius floors the extents, envelope containment
            # can only widen them.
            assert region["min"][axis] <= target[axis] - capacity["radius_m"] + 1e-9
            assert region["max"][axis] >= target[axis] + capacity["radius_m"] - 1e-9
        for person_id in capacity["bound_person_ids"]:
            center = tuple(target[axis] + offset[person_id][axis] for axis in range(3))
            assert all(region["min"][axis] < center[axis] - half[person_id][axis]
                       and center[axis] + half[person_id][axis] < region["max"][axis]
                       for axis in range(3))
        assert region["query_min_z"] > target[2]


def test_binding_hazard_region_follows_the_frozen_template():
    binding = _binding()
    template = binding["dynamic_hazard_boundaries"]
    region = service_probe._binding_hazard_region(binding, (12.0, -3.0, 0.2), 0.2)
    assert region["min"] == [12.0 - 4.0, -3.0 - 4.0, 0.2 + 0.05]
    assert region["max"] == [12.0 + 4.0, -3.0 + 4.0, 0.2 + 3.0]
    assert region["derivation"] == template["derivation"]


def test_binding_access_candidates_apply_declared_offsets_to_the_anchor():
    binding = _binding()
    targets = service_probe._binding_assembly_targets(binding)
    anchor_region = binding["assembly_capacity"][0]["region_id"]
    candidates = service_probe._binding_access_candidates(binding, targets[anchor_region])
    offsets = binding["safe_ingress_control_points"]["candidate_offsets_m"]
    assert len(candidates) == len(offsets)
    assert candidates[0][:2] == (targets[anchor_region][0] + offsets[0][0],
                                 targets[anchor_region][1] + offsets[0][1])


def test_select_access_uses_binding_probe_template_and_first_clear_candidate():
    binding = _binding()
    ingress = binding["safe_ingress_control_points"]
    offsets = ingress["candidate_offsets_m"]
    anchor = (5.0, 5.0, 0.3)
    candidates = service_probe._binding_access_candidates(binding, anchor)

    def reader_factory(regions):
        region_id = next(iter(regions))
        calls.append((region_id, deepcopy(regions[region_id])))

        class Reader:
            def read(self, region_id, *, episode_id, time_s):
                return ({"status": "measured", "clear": region_id == "rescue_access_1"}
                        if region_id in {"rescue_access_0", "rescue_access_1"}
                        else {"status": "unknown"})
        return Reader()

    calls = []
    region_id, row = service_probe._select_access(
        reader_factory, candidates, episode_id="e", time_s=0.0,
        half_xy=tuple(ingress["probe_half_extents_xy_m"]),
        z_window=tuple(ingress["probe_z_window_m"]))
    assert region_id == "rescue_access_1"
    first_offset = offsets[0]
    assert calls[0][1]["min"] == [anchor[0] + first_offset[0] - 1.0,
                                  anchor[1] + first_offset[1] - 1.0,
                                  anchor[2] + 0.05]
    assert calls[0][1]["max"] == [anchor[0] + first_offset[0] + 1.0,
                                  anchor[1] + first_offset[1] + 1.0,
                                  anchor[2] + 2.5]


# ---------------------------------------------------------------------------
# Declared-vs-readback review: the formal_episode_binding flag logic.
# ---------------------------------------------------------------------------

def _access_row(binding, index):
    ingress = binding["safe_ingress_control_points"]
    targets = service_probe._binding_assembly_targets(binding)
    anchor_region = binding["assembly_capacity"][0]["region_id"]
    candidates = service_probe._binding_access_candidates(binding, targets[anchor_region])
    center = candidates[index]
    half_xy, z_window = ingress["probe_half_extents_xy_m"], ingress["probe_z_window_m"]
    return {"min": [center[0] - half_xy[0], center[1] - half_xy[1], center[2] + z_window[0]],
            "max": [center[0] + half_xy[0], center[1] + half_xy[1], center[2] + z_window[1]]}


def _consistent_evidence(binding=None):
    binding = binding or _binding()
    targets = service_probe._binding_assembly_targets(binding)
    vehicle_ids = ["following_vehicle_00", "following_vehicle_01"]
    policy = default_deadline_policy()
    evidence = {
        "person_spawn": {row["person_id"]: {
            "bound_start_xyz": list(row["start_xyz"]),
            "readback_start_xyz": list(row["start_xyz"])}
            for row in binding["affected_person_roster"]},
        "person_guidance": {row["person_id"]: {
            "bound_assembly_region_id": row["assembly_region_id"],
            "request_assembly_region_id": row["assembly_region_id"],
            "readback_assembly_region_id": row["assembly_region_id"],
            "arrived": True, "inside_assembly": True}
            for row in binding["affected_person_roster"]},
        "assembly_regions": {row["region_id"]: {
            "registered_capacity": row["capacity_people"],
            "readback_status": "measured",
            "occupants": list(row["bound_person_ids"])}
            for row in binding["assembly_capacity"]},
        "hazard": {"incident_xyz": [12.0, -3.0, 0.2], "road_height_m": 0.2,
                   "registered_region": service_probe._binding_hazard_region(
                       binding, (12.0, -3.0, 0.2), 0.2),
                   "readback_status": "measured"},
        "access": {
            "anchor_region_id": binding["assembly_capacity"][0]["region_id"],
            "anchor_xyz": list(targets[binding["assembly_capacity"][0]["region_id"]]),
            "declared_region_ids": [f"rescue_access_{index}" for index in range(6)],
            "selected_region_id": "rescue_access_1",
            "registered_region": _access_row(binding, 1),
            "readback_status": "measured"},
        "window": {"runtime_policy": policy, "bound_vehicle_ids": list(vehicle_ids),
                   "controlled_vehicle_ids": list(vehicle_ids),
                   "executed_modes": ["hold", "release", "divert"],
                   "reaction_span_s": 5.2},
        "endpoint": {
            "receiver_runtime_class": "demo.city_benchmark.live_witness.CityIncidentReceiver",
            "recipient_service_id": "city_dispatch", "runtime_policy": policy,
            "continuous_access_span_s": 0.54},
    }
    return evidence


def test_review_sets_formal_binding_true_only_when_every_item_matches():
    review = service_probe._review_formal_binding(_binding(), _consistent_evidence())
    assert review["formal_episode_binding"] is True
    assert review["mismatches"] == []
    assert {row["item"] for row in review["checks"]} == {
        "affected_person_roster", "assembly_capacity", "dynamic_hazard_boundaries",
        "safe_ingress_control_points", "following_traffic_response_window",
        "response_service_endpoint"}
    assert all(row["consistent"] for row in review["checks"])


def test_review_with_no_evidence_lists_every_mismatch_fail_closed():
    review = service_probe._review_formal_binding(_binding(), {})
    assert review["formal_episode_binding"] is False
    assert sorted(review["mismatches"]) == sorted(
        ["affected_person_roster", "assembly_capacity", "dynamic_hazard_boundaries",
         "safe_ingress_control_points", "following_traffic_response_window",
         "response_service_endpoint"])


@pytest.mark.parametrize("mutation,expected_mismatch", [
    (lambda e: e["person_spawn"].popitem(), "affected_person_roster"),
    (lambda e: next(iter(e["person_spawn"].values()))["readback_start_xyz"].__setitem__(0, 99.0),
     "affected_person_roster"),
    (lambda e: next(iter(e["person_guidance"].values())).__setitem__("request_assembly_region_id",
                                                                    "assembly_candidate_999"),
     "affected_person_roster"),
    (lambda e: next(iter(e["person_guidance"].values())).__setitem__("arrived", False),
     "affected_person_roster"),
    (lambda e: next(iter(e["assembly_regions"].values())).__setitem__("registered_capacity", 9),
     "assembly_capacity"),
    (lambda e: next(iter(e["assembly_regions"].values()))["occupants"].append("intruder"),
     "assembly_capacity"),
    (lambda e: next(iter(e["assembly_regions"].values())).__setitem__("readback_status", "unknown"),
     "assembly_capacity"),
    (lambda e: e["hazard"]["registered_region"]["min"].__setitem__(0, -50.0),
     "dynamic_hazard_boundaries"),
    (lambda e: e["hazard"].__setitem__("readback_status", "unknown"),
     "dynamic_hazard_boundaries"),
    (lambda e: e["hazard"].pop("registered_region"), "dynamic_hazard_boundaries"),
    (lambda e: e["access"]["registered_region"]["min"].__setitem__(0, -50.0),
     "safe_ingress_control_points"),
    (lambda e: e["access"].__setitem__("readback_status", "unknown"),
     "safe_ingress_control_points"),
    (lambda e: e["access"]["registered_region"].update(_access_row(_binding(), 0))
     or e["access"].__setitem__("selected_region_id", "rescue_access_2"),
     "safe_ingress_control_points"),
    (lambda e: e["window"]["runtime_policy"].__setitem__("traffic_control_ack_s", 11.0),
     "following_traffic_response_window"),
    (lambda e: e["window"]["controlled_vehicle_ids"].pop(), "following_traffic_response_window"),
    (lambda e: e["window"]["executed_modes"].remove("release"), "following_traffic_response_window"),
    (lambda e: e["window"].__setitem__("reaction_span_s", 4.9), "following_traffic_response_window"),
    (lambda e: e["endpoint"].__setitem__("receiver_runtime_class",
                                         "demo.city_benchmark.service_backend.CityServiceBackend"),
     "response_service_endpoint"),
    (lambda e: e["endpoint"].__setitem__("recipient_service_id", "other_dispatch"),
     "response_service_endpoint"),
    (lambda e: e["endpoint"].__setitem__("continuous_access_span_s", 0.3),
     "response_service_endpoint"),
])
def test_review_fails_closed_with_the_mismatch_listed(mutation, expected_mismatch):
    evidence = _consistent_evidence()
    mutation(evidence)
    review = service_probe._review_formal_binding(_binding(), evidence)
    assert review["formal_episode_binding"] is False
    assert expected_mismatch in review["mismatches"]
    failed = {row["item"] for row in review["checks"] if not row["consistent"]}
    assert review["mismatches"] == [row["item"] for row in review["checks"]
                                    if not row["consistent"]]
    assert failed == set(review["mismatches"])


def test_review_accepts_binding_window_that_matches_the_runtime_policy():
    binding = _binding()
    evidence = _consistent_evidence(binding)
    window = binding["following_traffic_response_window"]
    assert evidence["window"]["runtime_policy"]["traffic_control_ack_s"] == \
        window["control_ack_deadline_s"]
    assert evidence["window"]["runtime_policy"]["traffic_reaction_window_s"] == \
        window["trajectory_reaction_window_s"]
    assert service_probe._review_formal_binding(binding, evidence)["formal_episode_binding"] is True
