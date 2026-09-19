"""Reference domain providers emit claims; no test here certifies a task."""

from types import SimpleNamespace

import numpy as np
import pytest

from demo.benchmark.domain_adapters import SceneDomainEvidenceAdapter
from demo.benchmark.live_domain_collectors import LiveSceneDomainCollector
from demo.benchmark.observations import ObservationStore
from demo.benchmark.reference_domain_providers import (
    ModernHousePathReferenceProvider,
    ReferenceFrameDetector,
    WarehouseDockReferenceProvider,
    build_modern_house_reference_collector,
    build_warehouse_reference_collector,
    modern_house_reference_providers,
    warehouse_reference_providers,
)


MAP_SHA = "a" * 64


def _public_task(scene_key, objectives):
    return {
        "scene": {"scene_key": scene_key},
        "objectives": objectives,
    }


def _binding(scene_key, public_task, *, occupancy=None):
    return {
        "schema": "eai.benchmark.domain_runtime_binding_v1",
        "scene_key": scene_key,
        "base_env": object(),
        "occupancy": occupancy,
        "observation_store": object(),
        "public_task": public_task,
    }


def _frame(scene_key, *, observation_id, node_id, target_ref, timestamp_s,
           processing_time_s=None):
    store = ObservationStore()
    pixels = np.ones((3, 4, 3), dtype=np.uint8)
    store.publish_frame(
        observation_id=observation_id, producer="iris_1", sensor="rgb",
        sensor_config={"calibration": "live"}, timestamp_s=timestamp_s,
        frame_id="iris_1/rgb", target_ref=target_ref,
        position_m=[1.0, 2.0, 2.5], frame=pixels, valid_for_s=5.0)
    return {
        "schema": "eai.benchmark.domain_frame_v1",
        "scene_key": scene_key,
        "observation_id": observation_id,
        "observation": store.get(observation_id),
        "frame": pixels,
        "node_id": node_id,
        "time_s": timestamp_s if processing_time_s is None else processing_time_s,
    }


def _collection(scene_key, time_s=20.0):
    return {
        "schema": "eai.benchmark.domain_collector_context_v1",
        "scene_key": scene_key,
        "phase": "episode_finalized",
        "time_s": time_s,
    }


def _assert_no_decision_fields(value):
    forbidden = {"success", "validated", "business_success"}
    if isinstance(value, dict):
        assert not forbidden & set(value)
        for nested in value.values():
            _assert_no_decision_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_no_decision_fields(nested)


def _warehouse_task():
    return _public_task("warehouse_nvd", [
        {"id": "rack_overview_scan", "target_ref": "rack_scan_zone", "prerequisites": []},
        {"id": "outbound_inventory_scan", "target_ref": "outbound_dock_zone",
         "prerequisites": ["priority_carton_dispatch"]},
        {"id": "priority_carton_dispatch", "target_ref": "outbound_dock_zone",
         "prerequisites": []},
        {"id": "dock_confirmation_hold", "target_ref": "outbound_dock_zone",
         "prerequisites": ["outbound_inventory_scan"]},
    ])


def _house_task():
    return _public_task("modern_house_nvd", [
        {"id": "room_entry_scan", "target_ref": "entry_scan_zone", "prerequisites": []},
        {"id": "furniture_clearance_scan", "target_ref": "furniture_review_zone",
         "prerequisites": ["room_entry_scan"]},
        {"id": "hall_release", "target_ref": "primary_standby_zone",
         "prerequisites": ["furniture_clearance_scan"]},
        {"id": "room_condition_scan", "target_ref": "room_review_zone",
         "prerequisites": ["hall_release"]},
    ])


class DetectorBackend:
    def __init__(self):
        self.bindings = []
        self.frames = []

    def bind_runtime(self, context):
        self.bindings.append(context)

    def detect(self, context):
        self.frames.append(context)
        return [{
            "detection_id": "sku-live-1",
            "detector_source": "warehouse_rgb_sku",
            "detector_version": "weights-sha256:123",
            "target_id": "opaque-runtime-track-7",
            "item_code": "W-PRIORITY-01",
            "estimated_position_m": [1.0, 2.0, 0.5],
            "confidence": 0.91,
            "timestamp_s": context["time_s"],
        }]


def test_reference_detector_runs_only_on_designated_frame_and_preserves_provenance():
    backend = DetectorBackend()
    provider = ReferenceFrameDetector(
        "warehouse_nvd", backend=backend, scan_node_ids={"rack_overview_scan"})
    provider.bind_runtime(_binding("warehouse_nvd", _warehouse_task()))

    ignored = provider(_frame(
        "warehouse_nvd", observation_id="dock", node_id="outbound_inventory_scan",
        target_ref="outbound_dock_zone", timestamp_s=3.0))
    claims = provider(_frame(
        "warehouse_nvd", observation_id="rack", node_id="rack_overview_scan",
        target_ref="rack_scan_zone", timestamp_s=4.0))

    assert ignored == []
    assert len(backend.frames) == 1
    assert claims[0]["target_id"] == "opaque-runtime-track-7"
    assert claims[0]["item_code"] == "W-PRIORITY-01"
    assert "domain_contract" not in backend.bindings[0]
    assert "base_env" not in backend.bindings[0]
    _assert_no_decision_fields(claims)


@pytest.mark.parametrize("forbidden", ["success", "validated", "ground_truth", "object_ids"])
def test_reference_detector_rejects_truth_or_decision_fields(forbidden):
    def backend(context):
        row = DetectorBackend().detect(context)[0]
        row[forbidden] = True
        return [row]

    provider = ReferenceFrameDetector(
        "warehouse_nvd", backend=backend, scan_node_ids=["rack_overview_scan"])
    provider.bind_runtime(_binding("warehouse_nvd", _warehouse_task()))

    with pytest.raises(ValueError, match="forbidden decision/truth fields"):
        provider(_frame(
            "warehouse_nvd", observation_id="rack", node_id="rack_overview_scan",
            target_ref="rack_scan_zone", timestamp_s=4.0))


class DockServiceBackend:
    def __init__(self):
        self.requests = []

    def issue_handoff(self, context):
        self.requests.append(context)
        return {
            "source": "benchmark_dock_controller",
            "target_ref": "outbound_dock_zone",
            "observation_id": context["observation_id"],
            "accepted_object_ids": ["runtime-carton-9"],
            "state": {"id": "dock-state-1", "state": "ready",
                      "time_s": 9.9, "valid_until_s": 12.0},
            "receipt": {"id": "dock-receipt-1", "time_s": 10.1},
        }


class PoseBackend:
    def __init__(self):
        self.requests = []

    def sample_pose(self, context):
        self.requests.append(context)
        return {
            "object_id": context["object_id"],
            "position_m": [8.0, -3.0, 0.35],
            "sample_time_s": 10.05,
            "source": "isaac_stage_world_pose",
        }


def test_warehouse_receipt_binds_service_source_frame_object_and_live_pose():
    service = DockServiceBackend()
    pose = PoseBackend()
    provider = WarehouseDockReferenceProvider(
        service_backend=service, object_pose_backend=pose)
    provider.bind_runtime(_binding("warehouse_nvd", _warehouse_task()))
    frame = _frame(
        "warehouse_nvd", observation_id="dock-frame", node_id="dock_confirmation_hold",
        target_ref="outbound_dock_zone", timestamp_s=10.0, processing_time_s=10.2)

    assert provider.process_frame(frame) is None
    events = provider.collect(_collection("warehouse_nvd", 11.0))

    state, receipt = events
    assert state["source"] == receipt["source"] == "benchmark_dock_controller"
    assert receipt["observation_id"] == "dock-frame"
    assert receipt["observation_frame_sha256"] == frame["observation"]["sensor_config"]["frame_sha256"]
    assert receipt["accepted_object_ids"] == ["runtime-carton-9"]
    assert receipt["object_pose_m"] == [8.0, -3.0, 0.35]
    assert receipt["object_pose_time_s"] == 10.05
    assert pose.requests == [{
        "schema": "eai.benchmark.warehouse_object_pose_request_v1",
        "scene_key": "warehouse_nvd", "object_id": "runtime-carton-9",
        "target_ref": "outbound_dock_zone", "time_s": 10.1,
    }]
    assert "domain_contract" not in service.requests[0]
    _assert_no_decision_fields(events)


@pytest.mark.parametrize("corruption,match", [
    ("source", "not bound"),
    ("observation", "not bound"),
    ("stale_pose", "future or stale"),
    ("wrong_pose_object", "another object"),
    ("decision", "forbidden decision/truth fields"),
])
def test_warehouse_provider_fails_closed_on_unbound_receipt_or_pose(corruption, match):
    class Service(DockServiceBackend):
        def issue_handoff(self, context):
            result = super().issue_handoff(context)
            if corruption == "source":
                result["source"] = "participant_report"
            elif corruption == "observation":
                result["observation_id"] = "another-frame"
            elif corruption == "decision":
                result["receipt"]["validated"] = True
            return result

    class Pose(PoseBackend):
        def sample_pose(self, context):
            result = super().sample_pose(context)
            if corruption == "stale_pose":
                result["sample_time_s"] = 9.0
            elif corruption == "wrong_pose_object":
                result["object_id"] = "another-object"
            return result

    provider = WarehouseDockReferenceProvider(
        service_backend=Service(), object_pose_backend=Pose())
    provider.bind_runtime(_binding("warehouse_nvd", _warehouse_task()))

    with pytest.raises(ValueError, match=match):
        provider.process_frame(_frame(
            "warehouse_nvd", observation_id="dock-frame",
            node_id="dock_confirmation_hold", target_ref="outbound_dock_zone",
            timestamp_s=10.0, processing_time_s=10.2))
    assert provider.collect(_collection("warehouse_nvd")) == []


class PathBackend:
    def __init__(self):
        self.bindings = []
        self.requests = []

    def bind_runtime(self, context):
        self.bindings.append(context)

    def measure_path(self, context):
        self.requests.append(context)
        return {
            "observation_id": context["observation_id"],
            "occupancy_sha256": context["occupancy_sha256"],
            "points": [[0.0, 0.0], [1.0, 0.5], [2.0, 1.0]],
            "sample_time_s": context["time_s"] - 0.05,
        }


def test_house_paths_sample_only_on_designated_frames_and_bind_same_map_hash():
    occupancy = SimpleNamespace(sha256=MAP_SHA)
    backend = PathBackend()
    provider = ModernHousePathReferenceProvider(path_backend=backend)
    provider.bind_runtime(_binding(
        "modern_house_nvd", _house_task(), occupancy=occupancy))

    provider.process_frame(_frame(
        "modern_house_nvd", observation_id="unrelated", node_id="furniture_clearance_scan",
        target_ref="furniture_review_zone", timestamp_s=1.0))
    provider.process_frame(_frame(
        "modern_house_nvd", observation_id="before-frame", node_id="room_entry_scan",
        target_ref="entry_scan_zone", timestamp_s=2.0, processing_time_s=2.1))
    provider.process_event({
        "schema": "eai.benchmark.domain_runtime_event_v1",
        "scene_key": "modern_house_nvd", "event_type": "node_completed",
        "node_id": "hall_release", "time_s": 8.0,
    })
    provider.process_frame(_frame(
        "modern_house_nvd", observation_id="after-frame", node_id="room_condition_scan",
        target_ref="room_review_zone", timestamp_s=9.0, processing_time_s=9.1))
    result = provider.collect(_collection("modern_house_nvd", 10.0))

    assert [request["phase"] for request in backend.requests] == ["before", "after"]
    assert all(request["occupancy_sha256"] == MAP_SHA for request in backend.requests)
    assert result["access_map_sha256"] == MAP_SHA
    assert result["access_paths"]["before"]["observation_id"] == "before-frame"
    assert result["access_paths"]["after"]["observation_id"] == "after-frame"
    assert result["access_paths"]["after"]["time_s"] > 8.0
    assert "domain_contract" not in backend.bindings[0]
    _assert_no_decision_fields(result)


def test_house_after_frame_requires_observed_closeout_and_never_calls_backend_early():
    backend = PathBackend()
    provider = ModernHousePathReferenceProvider(path_backend=backend)
    provider.bind_runtime(_binding(
        "modern_house_nvd", _house_task(),
        occupancy=SimpleNamespace(sha256=MAP_SHA)))

    with pytest.raises(ValueError, match="requires observed closeout"):
        provider.process_frame(_frame(
            "modern_house_nvd", observation_id="after-frame",
            node_id="room_condition_scan", target_ref="room_review_zone",
            timestamp_s=9.0, processing_time_s=9.1))
    assert backend.requests == []
    with pytest.raises(ValueError, match="both designated"):
        provider.collect(_collection("modern_house_nvd"))


def test_house_provider_rejects_frame_at_closeout_and_occupancy_hash_drift():
    occupancy = SimpleNamespace(sha256=MAP_SHA)
    backend = PathBackend()
    provider = ModernHousePathReferenceProvider(path_backend=backend)
    provider.bind_runtime(_binding(
        "modern_house_nvd", _house_task(), occupancy=occupancy))
    provider.record_closeout(node_id="hall_release", time_s=8.0)

    with pytest.raises(ValueError, match="after closeout"):
        provider.process_frame(_frame(
            "modern_house_nvd", observation_id="same-time",
            node_id="room_condition_scan", target_ref="room_review_zone",
            timestamp_s=8.0, processing_time_s=8.1))
    occupancy.sha256 = "b" * 64
    with pytest.raises(ValueError, match="occupancy hash changed"):
        provider.process_frame(_frame(
            "modern_house_nvd", observation_id="later",
            node_id="room_condition_scan", target_ref="room_review_zone",
            timestamp_s=9.0, processing_time_s=9.1))
    assert backend.requests == []


def test_reference_provider_builders_return_live_collector_injection_shape():
    warehouse = warehouse_reference_providers(
        detector_backend=DetectorBackend(), dock_service_backend=DockServiceBackend(),
        object_pose_backend=PoseBackend())
    house = modern_house_reference_providers(
        detector_backend=DetectorBackend(), path_backend=PathBackend())

    assert set(warehouse) == {"detector", "service_provider"}
    assert isinstance(warehouse["detector"], ReferenceFrameDetector)
    assert isinstance(warehouse["service_provider"], WarehouseDockReferenceProvider)
    assert set(house) == {"detector", "access_provider"}
    assert isinstance(house["detector"], ReferenceFrameDetector)
    assert isinstance(house["access_provider"], ModernHousePathReferenceProvider)


def test_complete_reference_collectors_are_built_only_from_public_requirements():
    warehouse_task = _warehouse_task()
    warehouse_task["domain_requirements"] = {"items": [{
        "id": "priority", "item_code": "W-PRIORITY-01", "quantity": 1,
        "consumer_node": "priority_carton_stage",
    }]}
    house_task = _house_task()
    house_task["domain_requirements"] = {"items": [{
        "id": "keepsake", "item_code": "personal_book_stack", "quantity": 1,
        "consumer_node": "keepsake_relocation",
    }]}

    warehouse = build_warehouse_reference_collector(
        warehouse_task, detector_backend=DetectorBackend(),
        dock_service_backend=DockServiceBackend(),
        object_pose_backend=PoseBackend())
    house = build_modern_house_reference_collector(
        house_task, detector_backend=DetectorBackend(), path_backend=PathBackend())

    assert isinstance(warehouse, LiveSceneDomainCollector)
    assert warehouse.scene_key == "warehouse_nvd"
    assert isinstance(house, LiveSceneDomainCollector)
    assert house.scene_key == "modern_house_nvd"


def test_warehouse_components_integrate_with_live_collector_and_observation_store():
    task = _warehouse_task()
    requirement = {
        "id": "priority", "item_code": "W-PRIORITY-01", "quantity": 1,
        "consumer_node": "priority_carton_stage",
    }
    task["domain_requirements"] = {"items": [requirement]}
    providers = warehouse_reference_providers(
        detector_backend=DetectorBackend(), dock_service_backend=DockServiceBackend(),
        object_pose_backend=PoseBackend())
    collector = LiveSceneDomainCollector(
        "warehouse_nvd", public_identity_requirements=[requirement], **providers)
    store = ObservationStore()
    adapter = SceneDomainEvidenceAdapter(
        "warehouse_nvd", observation_store=store, collector=collector)
    assert adapter.bind_runtime(
        base_env=object(), occupancy=object(), public_task=task)["status"] == "bound"

    rack_pixels = np.ones((3, 4, 3), dtype=np.uint8)
    store.publish_frame(
        observation_id="rack-frame", producer="iris_1", sensor="rgb",
        sensor_config={"calibration": "live"}, timestamp_s=4.0,
        frame_id="iris_1/rgb", target_ref="rack_scan_zone",
        position_m=[1.0, 2.0, 2.5], frame=rack_pixels, valid_for_s=4.0)
    assert adapter.process_frame(
        observation_id="rack-frame", frame=rack_pixels,
        node_id="rack_overview_scan", time_s=4.0)["status"] == "processed"
    store.consume("rack-frame", consumer="priority_carton_stage", timestamp_s=5.0)

    dock_pixels = np.ones((3, 4, 3), dtype=np.uint8)
    store.publish_frame(
        observation_id="dock-frame", producer="iris_1", sensor="rgb",
        sensor_config={"calibration": "live"}, timestamp_s=10.0,
        frame_id="iris_1/rgb", target_ref="outbound_dock_zone",
        position_m=[8.0, -3.0, 2.5], frame=dock_pixels, valid_for_s=3.0)
    assert adapter.process_frame(
        observation_id="dock-frame", frame=dock_pixels,
        node_id="dock_confirmation_hold", time_s=10.2)["status"] == "processed"
    assert adapter.collect(phase="episode_finalized", time_s=11.0)["status"] == "claims_ingested"

    report = adapter.report(contract_sha256="c" * 64)
    assert report["identity_checks"] == [{
        "requirement_id": "priority", "detection_ids": ["sku-live-1"],
        "consumer_node": "priority_carton_stage",
    }]
    assert report["dock_receipt_id"] == "dock-receipt-1"
    assert report["domain_service_events"][-1]["object_pose_source"] == "isaac_stage_world_pose"
    _assert_no_decision_fields(report)


def test_dock_provider_keeps_first_receipt_when_wait_node_publishes_more_frames():
    service = DockServiceBackend()
    provider = WarehouseDockReferenceProvider(
        service_backend=service, object_pose_backend=PoseBackend())
    provider.bind_runtime(_binding("warehouse_nvd", _warehouse_task()))
    first = _frame(
        "warehouse_nvd", observation_id="dock-1",
        node_id="dock_confirmation_hold", target_ref="outbound_dock_zone",
        timestamp_s=10.0, processing_time_s=10.2)
    provider.process_frame(first)
    provider.process_frame(_frame(
        "warehouse_nvd", observation_id="dock-2",
        node_id="dock_confirmation_hold", target_ref="outbound_dock_zone",
        timestamp_s=11.0, processing_time_s=11.2))

    events = provider.collect(_collection("warehouse_nvd", 12.0))
    assert len(service.requests) == 1
    assert events[-1]["observation_id"] == "dock-1"
