"""Support alignment is measured without resizing candidates or claiming coverage."""
from copy import deepcopy
import hashlib
import json

import pytest
from pxr import Usd, UsdGeom, UsdPhysics

from demo.city_benchmark.assembly_support import measure_assembly_supports


class Query:
    height = .3
    hit = True
    collision = "/World/Sidewalk"

    def raycast_closest(self, origin, direction, distance):
        assert direction == (0., 0., -1.) and distance == 1.
        return {"hit": self.hit, "position": [origin[0], origin[1], self.height],
                "normal": [0., 0., 1.], "collision": self.collision,
                "distance": origin[2] - self.height}


@pytest.fixture
def setup(tmp_path):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    floor = UsdGeom.Mesh.Define(stage, "/World/Sidewalk")
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim()).CreateCollisionEnabledAttr(True)
    regions = [{"id": f"assembly_candidate_{i:03d}", "kind": "assembly_candidate",
                "resource_capacity": 1} for i in range(1, 5)]
    data = {"schema": "eai.city.semantic_map", "version": 1,
            "coordinate_frame": "city_world", "up_axis": "Z", "meters_per_unit": 1.,
            "regions": regions, "surface_heights_m": {"sidewalks": .3},
            "bindings": [{"id": row["id"], "geometry": {
                "type": "Point", "coordinates": [i * 5., 0.]}, "candidate_radius_m": .75}
                for i, row in enumerate(regions)]}
    path = tmp_path / "map.json"

    def measure(query=None, *, mutate=None, **kwargs):
        content = deepcopy(data)
        if mutate:
            mutate(content)
        payload = json.dumps(content).encode()
        path.write_bytes(payload)
        params = {"semantic_map_path": path,
                  "expected_semantic_map_sha256": hashlib.sha256(payload).hexdigest(),
                  "public_regions": deepcopy(regions), "episode_id": "native-1",
                  "source_id": "city-native", "native_clock": lambda: 10.,
                  "scene_queries_enabled": True, "device": "cpu"}
        params.update(kwargs)
        return measure_assembly_supports(stage, query or Query(), **params)

    return stage, floor, measure


def test_support_sampling_keeps_geometry_and_explicit_limits(setup):
    stage, _, measure = setup
    before = stage.GetRootLayer().ExportToString()
    result = measure()
    assert stage.GetRootLayer().ExportToString() == before
    assert result["all_candidates_sampled_supported"] and result["same_tick"]
    assert len(result["candidates"]) == 4
    assert sum(len(row["support_samples"]) for row in result["candidates"]) == 36
    assert all(row["radius_m"] == .75 and row["capacity"] == 1
               and row["support_plane_z_m"] == .3 for row in result["candidates"])
    for key in ("continuous_support_validated", "execution_profile_enabled", "scoreable",
                "execution_validated", "business_outcomes_validated"):
        assert result[key] is False
    # Empty USD mesh is intentional: this API only reports actual ray samples
    # and collider identity. It never treats them as continuous polygon proof.
    assert "not_continuous_support" in result["qualification_scope"]


def test_private_binding_or_height_metadata_cannot_resize_disk(setup):
    _, _, measure = setup
    normal = measure()
    def contaminate(data):
        for index, row in enumerate(data["bindings"]):
            row.update(bound_person_ids=[f"private-{index}"], body_height_m=100.,
                       author_waypoints=[[999., 999., 999.]])
    changed = measure(mutate=contaminate)
    assert normal["candidate_geometry_sha256"] == changed["candidate_geometry_sha256"]
    assert normal["candidates"] == changed["candidates"]
    assert normal["semantic_map_sha256"] != changed["semantic_map_sha256"]


@pytest.mark.parametrize("mutate", [
    lambda data: data["bindings"][0].update(candidate_radius_m=.8),
    lambda data: data["regions"][0].update(resource_capacity=2),
    lambda data: data["bindings"].append(deepcopy(data["bindings"][0])),
    lambda data: data["bindings"].pop(),
    lambda data: data.update(up_axis="Y"),
    lambda data: data.update(version=True),
    lambda data: data.update(meters_per_unit=True),
    lambda data: data["surface_heights_m"].update(sidewalks=.31),
    lambda data: data["bindings"][0]["geometry"].update(coordinates=[float("nan"), 0.]),
])
def test_changed_contract_rejected_before_native_query(setup, mutate):
    _, _, measure = setup
    class NeverQuery:
        def raycast_closest(self, *_args):
            pytest.fail("invalid frozen geometry must fail before query")
    with pytest.raises(ValueError):
        measure(NeverQuery(), mutate=mutate)


def test_wrong_digest_rejected(setup):
    _, _, measure = setup
    with pytest.raises(ValueError, match="frozen expected digest"):
        measure(expected_semantic_map_sha256="f" * 64)


@pytest.mark.parametrize("kind", ["missing", "raised", "normal", "malformed", "wrong_ray"])
def test_failed_or_contradictory_support_does_not_get_repaired(setup, kind):
    _, _, measure = setup
    class BadQuery(Query):
        def raycast_closest(self, *args):
            row = super().raycast_closest(*args)
            if kind == "missing": row["hit"] = False
            if kind == "raised":
                row["position"][2] += .1
                row["distance"] -= .1
            if kind == "normal": row["normal"] = [0., 1., 0.]
            if kind == "malformed": row["distance"] = True
            if kind == "wrong_ray": row["position"][0] += .1
            return row
    result = measure(BadQuery())
    assert not result["all_candidates_sampled_supported"]
    expected = "unknown" if kind in {"malformed", "wrong_ray"} else "unsupported"
    assert all(row["status"] == expected and row["radius_m"] == .75
               and row["support_plane_z_m"] == .3 for row in result["candidates"])


@pytest.mark.parametrize("mode", ["disabled", "dynamic", "unknown_path", "wrong_stage_units"])
def test_untrusted_collider_source_is_unknown(setup, mode):
    stage, floor, measure = setup
    query = Query()
    if mode == "disabled": UsdPhysics.CollisionAPI(floor.GetPrim()).GetCollisionEnabledAttr().Set(False)
    if mode == "dynamic": UsdPhysics.RigidBodyAPI.Apply(floor.GetPrim())
    if mode == "unknown_path": query.collision = "/World/Missing"
    if mode == "wrong_stage_units": UsdGeom.SetStageMetersPerUnit(stage, .01)
    result = measure(query)
    assert all(row["status"] == "unknown" for row in result["candidates"])


@pytest.mark.parametrize("kwargs", [{"scene_queries_enabled": False}, {"device": "cuda:0"}])
def test_unavailable_native_query_keeps_all_four_records(setup, kwargs):
    _, _, measure = setup
    result = measure(**kwargs)
    assert len(result["candidates"]) == 4
    assert all(row["status"] == "unknown" for row in result["candidates"])


def test_changed_tick_downgrades_successful_samples(setup):
    _, _, measure = setup
    clock = iter([10., 10.02])
    result = measure(native_clock=lambda: next(clock))
    assert not result["same_tick"] and not result["all_candidates_sampled_supported"]
    assert all(row["status"] == "unknown" for row in result["candidates"])


@pytest.mark.parametrize("kind", ["units_and_miss", "dynamic_and_miss", "raised_and_malformed"])
def test_infrastructure_unknown_takes_precedence_over_factual_missing_support(setup, kind):
    stage, floor, measure = setup
    if kind == "units_and_miss": UsdGeom.SetStageMetersPerUnit(stage, .01)
    if kind == "dynamic_and_miss": UsdPhysics.RigidBodyAPI.Apply(floor.GetPrim())
    class MixedQuery(Query):
        count = 0
        def raycast_closest(self, *args):
            self.count += 1
            row = super().raycast_closest(*args)
            if kind != "raised_and_malformed":
                if self.count % 9 == 2: row["hit"] = False
            elif self.count % 9 == 1:
                row["position"][2] += .1
                row["distance"] -= .1
            elif self.count % 9 == 2:
                row["distance"] = True
            return row
    result = measure(MixedQuery())
    assert all(row["status"] == "unknown" for row in result["candidates"])
    if kind != "units_and_miss":
        assert all(len(row["support_samples"]) == 9 for row in result["candidates"])


@pytest.mark.parametrize("world_y", [-75., 75.])
def test_float32_ray_quantization_at_75m_is_not_a_geometric_failure(setup, world_y):
    _, _, measure = setup
    class Quantized(Query):
        def raycast_closest(self, *args):
            row = super().raycast_closest(*args)
            row["position"][1] += 2 * 2 ** -17  # Two float32 ULPs at 75m.
            return row
    def far_map(data):
        for binding in data["bindings"]:
            binding["geometry"]["coordinates"][1] = world_y
    result = measure(Quantized(), mutate=far_map)
    assert result["all_candidates_sampled_supported"]
    assert all(row["center_xy_m"][1] == world_y and row["radius_m"] == .75
               for row in result["candidates"])
    assert result["candidates"][0]["support_samples"][0]["ray_xy_consistency_tolerance_m"][1] == 4 * 2 ** -17


def test_negative_after_clock_is_rejected(setup):
    _, _, measure = setup
    clock = iter([10., -1.])
    with pytest.raises(ValueError, match="after capture"):
        measure(native_clock=lambda: next(clock))
