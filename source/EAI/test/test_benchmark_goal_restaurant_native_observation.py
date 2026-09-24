"""Synthetic query tests; native execution is a separate evidence gate."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from demo.benchmark.goal_restaurant_fleet import (
    FLEET_CONTROLLER_REGISTRY, RESTAURANT_TASK_ID, RESTAURANT_PUBLIC_TASK_SHA256,
)
from demo.benchmark.goal_restaurant_native_observation import (
    RestaurantNativeObservationProvider, RestaurantObservationCoverageError,
)
from demo.benchmark.goal_restaurant_business_v2 import _verify_observation_physics
from demo.benchmark.goal_restaurant_transport import RestaurantReadinessTransport
from demo.benchmark.communication import CommunicationRuntime
from demo.restaurant_benchmark import contact_probe, route_admission
from source.EAI.test import test_benchmark_goal_restaurant_business_v2 as business


GROUND = [p for p, b in FLEET_CONTROLLER_REGISTRY.items()
          if b["command_profile"] != "aerial_position"]
AERIAL = [p for p, b in FLEET_CONTROLLER_REGISTRY.items()
          if b["command_profile"] == "aerial_position"]
CENTERS = {"kitchen_service": list(route_admission.KITCHEN_SERVICE_XY_M),
           "dining_service": list(route_admission.DINING_SERVICE_XY_M)}


class Fleet:
    def __init__(self, producer, region="kitchen_service", *, step=1):
        self.session = SimpleNamespace(
            env_cfg=SimpleNamespace(sim=SimpleNamespace(enable_scene_query_support=True)),
            base_env=SimpleNamespace(sim=SimpleNamespace(get_physics_dt=lambda: .1)))
        self.meta = {"schema": "eai.restaurant.native_episode_epoch", "version": 1,
                     "task_id": RESTAURANT_TASK_ID,
                     "public_task_sha256": RESTAURANT_PUBLIC_TASK_SHA256,
                     "lifecycle_state": "post_warmup", "native_initial_state_ready": True,
                     "episode_id": business.EPISODE, "origin_physics_step": 0,
                     "origin_time_s": 0., "archive": {"path": "synthetic", "sha256": "x"}}
        self.data = {"physics_step": step, "time_s": step*.1,
                     "episode": deepcopy(self.meta), "agents": {producer: {
                         "public_agent_id": producer,
                         "native_agent_id": FLEET_CONTROLLER_REGISTRY[producer]["native_agent_id"],
                         "physics_step": step, "time_s": step*.1,
                         "root_state_w": [*CENTERS[region], .2, 1., 0., 0., 0.,
                                          0., 0., 0., 0., 0., 0.]}}}

    def episode_metadata(self):
        if self.meta is None:
            raise RuntimeError("not begun")
        return deepcopy(self.meta)

    def snapshot(self):
        return deepcopy(self.data)


class Query:
    def __init__(self, change=None):
        self.calls = []
        self.change = change

    def raycast_closest(self, origin, direction, distance):
        self.calls.append((origin, direction, distance))
        row = {"hit": True, "position": [origin[0], origin[1], 0.],
               "normal": [0., 0., 1.], "collision": contact_probe.GROUND_COLLISION_PATH}
        if self.change:
            self.change(row)
        return row


def provider(producer=GROUND[0], region="kitchen_service", change=None, *, step=1):
    fleet, query = Fleet(producer, region, step=step), Query(change)
    return RestaurantNativeObservationProvider(fleet, scene_query=query), fleet, query


@pytest.mark.parametrize("producer", GROUND)
@pytest.mark.parametrize("region", CENTERS)
def test_all_ground_identities_preserve_independent_physics_predicate(producer, region):
    source, fleet, query = provider(producer, region)
    before = fleet.snapshot()
    scene = source.ground_readiness(producer, region)
    assert scene["scene_observation_validated"] is True
    _verify_observation_physics(
        {"producer_id": producer, "region_id": region, "stamp": scene["stamp"],
         "scene_observation": scene}, producer=producer, region=region,
        region_bindings={region: {"center_xy_m": CENTERS[region]}})
    assert len(query.calls) == len(scene["support_samples"])
    assert fleet.snapshot() == before  # No time advancement or private stepping.
    bound = source.bind_transport_stamp(scene)
    assert bound["stamp"]["native_agent_id"] == FLEET_CONTROLLER_REGISTRY[producer]["native_agent_id"]


@pytest.mark.parametrize("change", [
    lambda hit: hit.update(hit=False),
    lambda hit: hit.update(collision="/World/table"),
    lambda hit: hit["position"].__setitem__(2, .1),
    lambda hit: hit.update(normal=[0., 0., -1.]),
])
def test_actual_negative_query_results_remain_facts_not_readiness(change):
    source, _, _ = provider(change=change)
    scene = source.ground_readiness(GROUND[0], "kitchen_service")
    assert scene["scene_observation_validated"] is False
    assert scene["passed_support_samples"] == 0
    with pytest.raises(ValueError):
        source.bind_transport_stamp(scene)


def test_arrival_half_metre_is_not_quarter_metre_observation():
    source, fleet, _ = provider("restaurant_m20_1")
    fleet.data["agents"]["restaurant_m20_1"]["root_state_w"][0] += .4
    scene = source.ground_readiness("restaurant_m20_1", "kitchen_service")
    assert scene["passed_support_samples"] == len(scene["support_samples"])
    assert scene["scene_observation_validated"] is False
    with pytest.raises(ValueError, match="outside its region"):
        source.bind_transport_stamp(scene)


def test_one_of_four_support_hits_is_a_recorded_failure():
    source, _, query = provider()
    query.change = lambda hit: hit.update(hit=len(query.calls) == 1)
    scene = source.ground_readiness(GROUND[0], "kitchen_service")
    assert len(scene["support_samples"]) == 4
    assert scene["passed_support_samples"] == 1
    assert scene["scene_observation_validated"] is False
    with pytest.raises(ValueError):
        source.bind_transport_stamp(scene)


@pytest.mark.parametrize("producer", AERIAL)
def test_aerial_raw_probe_cannot_be_ground_readiness(producer):
    source, _, _ = provider(producer)
    raw = source.local_surface_probe(producer)
    assert raw["camera_image_produced"] is False
    assert raw["visibility_verified"] is False
    assert raw["business_ground_readiness"] is False
    assert raw["scoreable"] is False
    with pytest.raises(RestaurantObservationCoverageError, match="aerial"):
        source.ground_readiness(producer, "kitchen_service")
    with pytest.raises(ValueError, match="aerial"):
        source.bind_transport_stamp(raw)


@pytest.mark.parametrize("producer", AERIAL)
def test_aerial_overview_membership_is_measured_in_3d_and_not_visibility(producer):
    source, fleet, _ = provider(producer)
    assert source.aerial_overview_probe(producer)["region_membership_validated"] is False
    root = fleet.data["agents"][producer]["root_state_w"]
    root[:3] = route_admission.OVERVIEW_ANCHOR_XYZ_M
    row = source.aerial_overview_probe(producer)
    assert row["region_membership_validated"] is True
    assert row["visibility_verified"] is False
    assert row["business_ground_readiness"] is False
    assert "support_samples" not in row
    root[2] += 1.
    assert source.aerial_overview_probe(producer)["region_membership_validated"] is False


def test_ground_cannot_claim_aerial_overview():
    source, _, query = provider()
    with pytest.raises(RestaurantObservationCoverageError, match="aerial"):
        source.aerial_overview_probe(GROUND[0])
    assert query.calls == []


@pytest.mark.parametrize("region", ["staging", "overview", "standby_ground", "invented"])
def test_missing_region_coverage_is_explicit_platform_gap(region):
    source, _, query = provider()
    with pytest.raises(RestaurantObservationCoverageError):
        source.ground_readiness(GROUND[0], region)
    assert query.calls == []


def test_missing_lifecycle_or_query_support_is_platform_gap():
    source, fleet, query = provider()
    fleet.meta = None
    with pytest.raises(RestaurantObservationCoverageError, match="epoch"):
        source.local_surface_probe(GROUND[0])
    assert query.calls == []
    fleet.session.env_cfg.sim.enable_scene_query_support = False
    with pytest.raises(RestaurantObservationCoverageError, match="not enabled"):
        RestaurantNativeObservationProvider(fleet, scene_query=query)
    fleet.session.env_cfg.sim.enable_scene_query_support = True
    with pytest.raises(RestaurantObservationCoverageError, match="unavailable"):
        RestaurantNativeObservationProvider(fleet, scene_query=object())


@pytest.mark.parametrize("mutate", [
    lambda f: f.data["agents"][GROUND[0]].update(native_agent_id="carter_2"),
    lambda f: f.data["agents"][GROUND[0]].update(physics_step=17),
    lambda f: f.data["episode"].update(episode_id="foreign"),
    lambda f: f.meta.update(task_id="foreign"),
    lambda f: f.meta.update(public_task_sha256="0"*64),
    lambda f: f.data["episode"].update(task_id="foreign"),
    lambda f: f.data.update(time_s=float("nan")),
    lambda f: f.data["agents"][GROUND[0]]["root_state_w"].__setitem__(1, float("inf")),
    lambda f: f.data["agents"][GROUND[0]]["root_state_w"].__setitem__(3, 0.),
])
def test_foreign_identity_epoch_or_malformed_measurement_fails_before_query(mutate):
    source, fleet, query = provider()
    mutate(fleet)
    with pytest.raises((ValueError, RuntimeError)):
        source.local_surface_probe(GROUND[0])
    assert query.calls == []


@pytest.mark.parametrize("change", [
    lambda h: h["position"].__setitem__(0, float("nan")),
    lambda h: h["normal"].__setitem__(1, float("inf")),
    lambda h: h.update(distance=-1.),
    lambda h: h.update(collision=None),
    lambda h: h.update(hit=1),
])
def test_malformed_query_is_infrastructure_failure(change):
    source, _, _ = provider(change=change)
    with pytest.raises((ValueError, RuntimeError)):
        source.ground_readiness(GROUND[0], "kitchen_service")


def test_external_step_during_query_invalidates_whole_observation():
    source, fleet, query = provider()
    query.change = lambda _: fleet.data["agents"][GROUND[0]].update(physics_step=2)
    with pytest.raises(RuntimeError, match="state changed"):
        source.ground_readiness(GROUND[0], "kitchen_service")


def test_produced_scene_survives_transport_and_complete_business_replay(monkeypatch):
    def produced_scene(producer, region, step, *, time_by_step=None):
        assert time_by_step is None
        source, _, _ = provider(producer, region, step=step)
        return source.ground_readiness(producer, region)

    monkeypatch.setattr(business, "_scene", produced_scene)
    case = business._build_case(shared_observer=False, reverse=False)
    original = deepcopy(case["native_evidence"])
    bridge = RestaurantReadinessTransport(
        episode_id=business.EPISODE, runtime=CommunicationRuntime(),
        robot_bindings=case["robot_bindings"], episode_step_origin=0, physics_dt_s=.1)
    for row in original:
        def bound(stamp):
            return {**stamp, "producer_id": row["producer_id"],
                    "native_agent_id": row["native_agent_id"]}

        if row["kind"] == "observation":
            actual = bridge.record_observation(
                task_id=row["task_id"], producer_id=row["producer_id"],
                observation_id=row["service_record"]["observation_id"],
                scene_observation=RestaurantNativeObservationProvider.bind_transport_stamp(
                    row["scene_observation"]))
            assert actual == row
        elif row["kind"] == "relay_publication":
            bridge.publish_relay(
                task_id=row["task_id"], producer_id=row["producer_id"],
                message_id=row["relay_publication"]["message_id"],
                observation_refs=row["relay_publication"]["observation_refs"],
                stamp=bound(row["publication_stamp"]), valid_until_s=100.)
        elif row["kind"] == "readiness_receipt":
            bridge.poll_relay(message_id=row["business_receipt"]["relay_message_id"],
                              stamp=bound(row["receipt_stamp"]), receipt_id="ready")
    snapshot = bridge.snapshot()
    case.update({key: snapshot[key] for key in ("readiness_service", "transport_report")})
    case["native_evidence"] = ([r for r in original if r["kind"] == "arrival"]
                               + snapshot["native_evidence"])
    review = business._verify(case)
    assert review["validated"] is True
    assert review["native_worker_wiring_verified"] is False
    assert snapshot["scoreable"] is False
