"""CPU-only contracts and counterexamples; injected queries are not PhysX evidence."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from demo.benchmark import goal_live_occupancy as occupancy


def context(**extra):
    return dict(scene_id="scene", snapshot_id="frame-10", source_id="trusted-test",
                frame_id="USD_world", meters_per_unit=1.0, observed_at_s=10.0, **extra)


def add_entity(snapshot, roster, identifier, position=(0, 0, 1), *,
               activity="active", holder=None, half=(0.5, 0.5, 0.5)):
    body_id = identifier + "-body"
    prim = "/World/" + identifier + "/body"
    paths = [prim + "/collision"]
    entity = dict(id=identifier, kind="held_object" if holder else "robot",
                  body_ids=[body_id], collision_paths=paths[:])
    if holder:
        entity["holder_id"] = holder
        snapshot["attachments"].append(context(
            object_id=identifier, holder_id=holder, kind="fixed_attachment_readback"))
    else:
        entity["activity"] = activity
    roster.append(entity)
    body = dict(id=body_id, entity_id=identifier, prim_path=prim,
                collision_paths=paths[:], position_xyz_m=list(position),
                orientation_wxyz=[1, 0, 0, 0], bounds_frame="body_local",
                local_collision_bounds_m=[[-h for h in half], list(half)],
                pose_source=context(kind="physx_body_pose", body_prim_path=prim,
                                    configuration_revision="cfg-1"),
                bounds_source=context(kind="usd_collision_bounds", body_prim_path=prim,
                                      configuration_revision="cfg-1",
                                      covers_all_enabled_colliders=True))
    snapshot["bodies"].append(body)
    return body


@pytest.fixture
def scene():
    snapshot = context(bodies=[], attachments=[])
    roster = []
    add_entity(snapshot, roster, "A")
    return snapshot, roster


class Query:
    """Scripted SDK callback protocol, recording each full-volume request."""

    def __init__(self, *, overlaps=(), sweeps=()):
        self.overlaps = overlaps
        self.sweeps = sweeps
        self.calls = []

    @staticmethod
    def emit(hits, callback):
        count = 0
        for hit in hits:
            count += 1
            if not callback(hit):
                break
        return count

    def overlap_box(self, half, pos, rot, callback, any_hit):
        assert any_hit is False
        self.calls.append(("overlap", half, pos, rot))
        return self.emit(self.overlaps, callback)

    def sweep_box_all(self, half, pos, rot, direction, distance, callback, both_sides):
        assert both_sides is True
        self.calls.append(("sweep", half, pos, rot, direction, distance))
        return bool(self.emit(self.sweeps, callback))


def collect(scene, **kwargs):
    snapshot, roster = scene
    return occupancy.collect_current_occupancy(snapshot, expected_inventory=roster,
                                               now_s=kwargs.pop("now_s", 10.05), **kwargs)


def segment(scene, **kwargs):
    snapshot, roster = scene
    kwargs.setdefault("query", Query())
    kwargs.setdefault("query_context", context(kind="physx_scene_query", static_colliders_included=True))
    kwargs.setdefault("moving_robot_id", "A")
    kwargs.setdefault("translation_xyz_m", [4, 0, 0])
    kwargs.setdefault("now_s", 10.05)
    return occupancy.check_live_segment(snapshot, expected_inventory=roster, **kwargs)


def test_complete_active_idle_and_held_occupancy_is_read_only(scene):
    snapshot, roster = scene
    add_entity(snapshot, roster, "B", (10, 0, 1), activity="idle")
    add_entity(snapshot, roster, "tool", (0, 0, 2), holder="A", half=(0.1, 0.2, 0.3))
    original = deepcopy(scene)
    report = collect(scene)
    assert report["status"] == "current_occupancy"
    assert [(e["id"], e["activity"]) for e in report["entities"]] == [
        ("A", "active"), ("B", "idle"), ("tool", None)]
    assert report["entities"][0]["world_aabb_m"] == [[-.5, -.5, .5], [.5, .5, 1.5]]
    assert report["checked_at_s"] == 10.05
    assert scene == original
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    module_path = str(Path(occupancy.__file__).resolve())
    assert report["source_files"][module_path] == hashlib.sha256(Path(module_path).read_bytes()).hexdigest()
    assert not report["measurement_authenticity_validated"]
    assert not report["scoreable"]


def test_rotated_noncentered_collision_bounds(scene):
    body = scene[0]["bodies"][0]
    body["position_xyz_m"] = [10, 20, 30]
    body["orientation_wxyz"] = [math.sqrt(.5), 0, 0, math.sqrt(.5)]
    body["local_collision_bounds_m"] = [[1, 2, 3], [3, 6, 5]]
    report = collect(scene)
    assert report["status"] == "current_occupancy"
    box = report["entities"][0]["world_aabb_m"]
    assert box[0] == pytest.approx([4, 21, 33])
    assert box[1] == pytest.approx([8, 23, 35])


def test_each_articulated_body_is_measured_and_swept_at_frozen_configuration(scene):
    snapshot, roster = scene
    link = deepcopy(snapshot["bodies"][0])
    link.update(id="A-link", prim_path="/World/A/link", collision_paths=["/World/A/link/collision"],
                position_xyz_m=[0, 2, 1])
    for key in ("pose_source", "bounds_source"):
        link[key]["body_prim_path"] = link["prim_path"]
    snapshot["bodies"].append(link)
    roster[0]["body_ids"].append(link["id"])
    roster[0]["collision_paths"].extend(link["collision_paths"])
    report = segment(scene)
    assert report["status"] == "clear_at_snapshot_for_fixed_translation"
    assert report["occupancy"]["entities"][0]["world_aabb_m"] == [[-.5, -.5, .5], [.5, 2.5, 1.5]]
    assert {r["body_id"] for r in report["scene_checks"]} == {"A-body", "A-link"}
    assert all(len(r["checks"]) == 3 for r in report["scene_checks"])


@pytest.mark.parametrize("activity", ["active", "idle"])
def test_endpoints_free_but_other_robot_blocks_middle_of_whole_segment(scene, activity):
    add_entity(*scene, "B", (2, 0, 1), activity=activity)
    report = segment(scene)
    assert report["status"] == "blocked"
    hit = report["dynamic_intersections"][0]
    assert hit["obstacle_entity_id"] == "B"
    assert hit["segment_fraction_interval"] == pytest.approx([.25, .75], abs=1e-6)
    assert all(c["status"] == "no_external_hit" for c in report["scene_checks"][0]["checks"])


def test_static_obstacle_between_free_endpoints_requires_full_box_sweep(scene):
    query = Query(sweeps=[dict(collision="/World/wall", rigid_body=None, distance=1.4)])
    report = segment(scene, query=query)
    assert report["status"] == "blocked"
    checks = report["scene_checks"][0]["checks"]
    assert [c["status"] for c in checks] == ["no_external_hit", "no_external_hit", "blocked"]
    assert checks[2]["hits"] == [dict(collision="/World/wall", rigid_body=None, distance_m=1.4)]
    assert [c[0] for c in query.calls] == ["overlap", "overlap", "sweep"]
    sweep = query.calls[-1]
    assert sweep[1] == pytest.approx([.500001] * 3)
    assert sweep[2:] == ((0., 0., 1.), (0., 0., 0., 1.), (1., 0., 0.), 4.)


@pytest.mark.parametrize("path", ["/World/A/body/collision_extra", "/World/A2/body/collision", "/World/A/body/new_collider"])
def test_only_exact_self_collider_excluded_not_all_robots_or_prefixes(scene, path):
    own = dict(collision="/World/A/body/collision", rigid_body="/World/A/body", distance=0)
    external = dict(collision=path, rigid_body="/World/A/body", distance=1)
    report = segment(scene, query=Query(sweeps=[own, external]))
    check = report["scene_checks"][0]["checks"][-1]
    assert report["status"] == "blocked"
    assert check["excluded_self_hits"][0]["collision"] == own["collision"]
    assert check["hits"][0]["collision"] == path


def test_exact_self_hits_do_not_block_empty_scene(scene):
    own = SimpleNamespace(collision="/World/A/body/collision", rigid_body="/World/A/body", distance=0)
    query = Query(overlaps=[own], sweeps=[own])
    report = segment(scene, query=query)
    assert report["status"] == "clear_at_snapshot_for_fixed_translation"
    assert all(len(c["excluded_self_hits"]) == 1 for c in report["scene_checks"][0]["checks"])
    assert not report["traversal_validated"]
    assert not report["future_dynamic_clearance_validated"]


@pytest.mark.parametrize("as_object", [False, True])
@pytest.mark.parametrize("index", [0, 7, -1, None, True, "4294967295", 0xFFFFFFFF])
def test_proto_index_cannot_alias_an_ordinary_self_hit(scene, as_object, index):
    fields = dict(collision="/World/A/body/collision", rigid_body="/World/A/body",
                  protoIndex=index, distance=0.)
    hit = SimpleNamespace(**fields) if as_object else fields
    report = segment(scene, query=Query(overlaps=[hit], sweeps=[hit]))
    ordinary = type(index) is int and index == 0xFFFFFFFF
    assert report["status"] == ("clear_at_snapshot_for_fixed_translation" if ordinary else "blocked")
    for check in report["scene_checks"][0]["checks"]:
        if ordinary:
            assert check["excluded_self_hits"][0]["protoIndex"] == index
            assert check["unsupported_instance_hits"] == []
        else:
            assert check["status"] == "unknown"
            assert check["excluded_self_hits"] == []
            assert check["unsupported_instance_hits"][0]["protoIndex"] == index
            assert "protoIndex" in check["reason"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("rigid_body", [None, "/World/other/body"])
def test_self_path_with_missing_or_conflicting_body_cannot_be_excluded(scene, rigid_body):
    own = dict(collision="/World/A/body/collision", rigid_body=rigid_body, distance=0)
    report = segment(scene, query=Query(sweeps=[own]))
    check = report["scene_checks"][0]["checks"][-1]
    assert report["status"] == "blocked"
    assert check["status"] == "unknown"
    assert check["excluded_self_hits"] == []


def test_own_held_object_extends_swept_occupancy_and_hits_other_robot(scene):
    add_entity(*scene, "tool", (0, 2, 1), holder="A")
    add_entity(*scene, "B", (2, 2, 1), activity="idle")
    report = segment(scene)
    assert report["status"] == "blocked"
    assert {h["moving_entity_id"] for h in report["dynamic_intersections"]} == {"tool"}
    assert {r["entity_id"] for r in report["scene_checks"]} == {"A", "tool"}
    assert report["excluded_self_collision_paths"] == ["/World/A/body/collision", "/World/tool/body/collision"]


def test_own_held_object_volume_also_swept_against_static_scene(scene):
    add_entity(*scene, "tool", (0, 2, 1), holder="A", half=(.1, .2, .3))

    class TallObstacle(Query):
        def sweep_box_all(self, half, pos, rot, direction, distance, callback, both_sides):
            self.sweeps = [dict(collision="/World/ledge", distance=2)] if pos[1] == 2 else []
            return super().sweep_box_all(half, pos, rot, direction, distance, callback, both_sides)

    query = TallObstacle()
    report = segment(scene, query=query)
    assert report["status"] == "blocked"
    assert report["scene_checks"][0]["checks"][-1]["status"] == "no_external_hit"
    assert report["scene_checks"][1]["checks"][-1]["hits"][0]["collision"] == "/World/ledge"
    assert query.calls[-1][1] == pytest.approx([.100001, .200001, .300001])


def test_other_robots_held_object_is_not_self(scene):
    add_entity(*scene, "B", (2, 3, 1), activity="idle")
    add_entity(*scene, "tool", (2, 0, 1), holder="B")
    report = segment(scene)
    assert report["status"] == "blocked"
    assert [h["obstacle_entity_id"] for h in report["dynamic_intersections"]] == ["tool"]
    assert "/World/tool/body/collision" not in report["excluded_self_collision_paths"]


def test_robot_cannot_claim_holder_to_disappear_from_dynamic_occupancy(scene):
    add_entity(*scene, "B", (2, 0, 1), activity="idle")
    scene[1][1]["holder_id"] = "A"
    query = Query()
    report = segment(scene, query=query)
    assert report["status"] == "blocked"
    assert report["occupancy"]["entities"] == []
    assert "self-exclusion alias" in report["occupancy"]["blockers"][0]
    assert query.calls == []


def test_z_separation_clears_and_vertical_segment_hits(scene):
    add_entity(*scene, "B", (0, 0, 3), activity="idle")
    assert segment(scene)["status"] == "clear_at_snapshot_for_fixed_translation"
    report = segment(scene, translation_xyz_m=[0, 0, 4])
    assert report["status"] == "blocked"
    assert report["dynamic_intersections"][0]["segment_fraction_interval"] == pytest.approx([.25, .75], abs=1e-6)


@pytest.mark.parametrize("delta,other", [([-4, 0, 0], [-2, 0, 1]), ([4, 4, 4], [2, 2, 3])])
def test_negative_and_diagonal_motion(scene, delta, other):
    add_entity(*scene, "B", other)
    report = segment(scene, translation_xyz_m=delta)
    assert report["status"] == "blocked"
    assert report["dynamic_intersections"][0]["segment_fraction_interval"] == pytest.approx([.25, .75], abs=1e-6)


def test_diagonal_swept_aabb_union_overlap_does_not_mean_segment_collision(scene):
    add_entity(*scene, "B", (0, 4, 1))
    assert segment(scene, translation_xyz_m=[4, 4, 0])["status"] == "clear_at_snapshot_for_fixed_translation"


def test_zero_translation_and_contact_fail_closed(scene):
    query = Query(overlaps=[dict(collision="/World/floor")])
    report = segment(scene, translation_xyz_m=[0, 0, 0], query=query)
    assert report["status"] == "blocked"
    assert [c[0] for c in query.calls] == ["overlap", "overlap"]
    add_entity(*scene, "B", (1, 0, 1))  # closed envelope contact
    report = segment(scene, translation_xyz_m=[0, 0, 0])
    assert report["dynamic_intersections"][0]["segment_fraction_interval"] == [0, 1]


def set_at(root, path, value):
    for key in path[:-1]:
        root = root[key]
    root[path[-1]] = value


@pytest.mark.parametrize("path,value", [
    (("scene_id",), ""), (("snapshot_id",), None), (("source_id",), ""),
    (("frame_id",), "map"), (("meters_per_unit",), .01), (("meters_per_unit",), True),
    (("observed_at_s",), 9), (("observed_at_s",), 11), (("observed_at_s",), math.nan),
    (("bodies", 0, "position_xyz_m"), None), (("bodies", 0, "position_xyz_m"), [0, 0, math.inf]),
    (("bodies", 0, "orientation_wxyz"), [0, 0, 0, 0]),
    (("bodies", 0, "orientation_wxyz"), [2, 0, 0, 0]),
    (("bodies", 0, "bounds_frame"), "world"),
    (("bodies", 0, "local_collision_bounds_m"), None),
    (("bodies", 0, "local_collision_bounds_m"), [[0, 0, 0], [0, 1, 1]]),
    (("bodies", 0, "local_collision_bounds_m"), [[0, 0, 0], [-1, 1, 1]]),
    (("bodies", 0, "prim_path"), "relative/body"),
    (("bodies", 0, "prim_path"), "/World/body.attribute"),
    (("bodies", 0, "position_xyz_m"), [1e30, 0, 0]),
    (("bodies", 0, "collision_paths"), ["/World/foreign/collision"]),
    (("bodies", 0, "pose_source"), None),
    (("bodies", 0, "pose_source", "body_prim_path"), "/World/other"),
    (("bodies", 0, "pose_source", "source_id"), ""),
    (("bodies", 0, "pose_source", "observed_at_s"), 9.99),
    (("bodies", 0, "pose_source", "scene_id"), "other_scene"),
    (("bodies", 0, "pose_source", "snapshot_id"), "previous"),
    (("bodies", 0, "pose_source", "meters_per_unit"), True),
    (("bodies", 0, "bounds_source", "kind"), "public_profile"),
    (("bodies", 0, "bounds_source", "configuration_revision"), "cfg-2"),
    (("bodies", 0, "bounds_source", "covers_all_enabled_colliders"), False),
])
def test_invalid_geometry_identity_coordinates_and_provenance_block(scene, path, value):
    set_at(scene[0], path, value)
    report = collect(scene)
    assert report["status"] == "blocked"
    assert report["entities"] == []
    query = Query()
    assert segment(scene, query=query)["status"] == "blocked"
    assert query.calls == []
    json.dumps(report, allow_nan=False)


def test_public_profile_cannot_supply_missing_live_bounds(scene):
    body = scene[0]["bodies"][0]
    del body["local_collision_bounds_m"]
    body["profile"] = dict(width_m=1, height_m=1, length_m=1, validated=True)
    assert collect(scene)["status"] == "blocked"


@pytest.mark.parametrize("mutation", [
    "missing_body", "extra_body", "duplicate_body", "duplicate_entity", "missing_collider",
    "extra_collider", "duplicate_collider", "duplicate_body_prim", "missing_idle_body",
    "unbound_attachment", "missing_attachment", "wrong_holder", "stale_attachment", "nonfixed_attachment",
])
def test_inventory_and_held_identity_must_be_complete(scene, mutation):
    snapshot, roster = scene
    if mutation == "missing_body":
        snapshot["bodies"] = []
    elif mutation == "extra_body":
        extra = deepcopy(snapshot["bodies"][0]); extra["id"] = "extra"
        snapshot["bodies"].append(extra)
    elif mutation == "duplicate_body":
        snapshot["bodies"] *= 2
    elif mutation == "duplicate_entity":
        roster.append(deepcopy(roster[0]))
    elif mutation == "missing_collider":
        snapshot["bodies"][0]["collision_paths"] = []
    elif mutation == "extra_collider":
        snapshot["bodies"][0]["collision_paths"].append("/World/A/body/extra")
    elif mutation == "duplicate_collider":
        snapshot["bodies"][0]["collision_paths"] *= 2
    elif mutation == "duplicate_body_prim":
        body = add_entity(snapshot, roster, "B")
        body["prim_path"] = snapshot["bodies"][0]["prim_path"]
    elif mutation == "missing_idle_body":
        add_entity(snapshot, roster, "B", activity="idle")
        snapshot["bodies"].pop()
    elif mutation == "unbound_attachment":
        snapshot["attachments"].append(context(object_id="absent", holder_id="A"))
    else:
        add_entity(snapshot, roster, "tool", holder="A")
        if mutation == "missing_attachment":
            snapshot["attachments"] = []
        elif mutation == "wrong_holder":
            snapshot["attachments"][0]["holder_id"] = "other"
        elif mutation == "stale_attachment":
            snapshot["attachments"][0]["observed_at_s"] = 9
        else:
            snapshot["attachments"][0]["kind"] = "grasp_proposal"
    report = collect(scene)
    assert report["status"] == "blocked"
    assert report["entities"] == []


@pytest.mark.parametrize("kwargs", [
    {"now_s": math.nan}, {"now_s": -1}, {"max_age_s": -1}, {"max_age_s": 2},
    {"query": None}, {"query_context": None}, {"query_context": context(kind="physx_scene_query")},
    {"moving_robot_id": "absent"}, {"moving_robot_id": {}},
    {"translation_xyz_m": [101, 0, 0]}, {"translation_xyz_m": [0, math.inf, 0]},
    {"translation_xyz_m": [0, 0]},
])
def test_invalid_segment_context_cannot_clear(scene, kwargs):
    report = segment(scene, **kwargs)
    assert report["status"] == "blocked"
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("hit", [
    {}, {"collision": "relative"}, {"collision": "/World/A/../wall", "distance": 1},
    {"collision": "/World/wall", "distance": -1},
    {"collision": "/World/wall", "distance": 5},
    {"collision": "/World/wall", "distance": math.nan},
    {"collision": "/World/wall", "distance": 1, "rigid_body": "relative"},
])
def test_malformed_scene_hit_never_produces_clear(scene, hit):
    report = segment(scene, query=Query(sweeps=[hit]))
    check = report["scene_checks"][0]["checks"][-1]
    assert report["status"] == "blocked"
    assert check["status"] == "unknown"
    assert "scene_query_failed" in check["reason"]


@pytest.mark.parametrize("failure", ["exception", "overlap_count", "sweep_count", "hit_limit"])
def test_query_failure_or_incomplete_callbacks_fail_closed(scene, failure):
    class BrokenQuery(Query):
        def overlap_box(self, *args):
            if failure == "exception":
                raise RuntimeError("physics query unavailable")
            if failure == "overlap_count":
                return 1
            return super().overlap_box(*args)

        def sweep_box_all(self, *args):
            if failure == "sweep_count":
                return True
            return super().sweep_box_all(*args)

    query = BrokenQuery()
    if failure == "hit_limit":
        query.sweeps = [dict(collision="/World/A/body/collision", rigid_body="/World/A/body", distance=0)] * (occupancy.MAX_HITS + 1)
    report = segment(scene, query=query)
    assert report["status"] == "blocked"
    assert any(c["status"] == "unknown" for c in report["scene_checks"][0]["checks"])


def test_query_context_from_other_snapshot_is_rejected_before_query(scene):
    query = Query()
    wrong = context(kind="physx_scene_query", static_colliders_included=True)
    wrong["snapshot_id"] = "old"
    assert segment(scene, query=query, query_context=wrong)["status"] == "blocked"
    assert query.calls == []


def test_segment_preserves_inputs_and_binds_changes(scene):
    original = deepcopy(scene)
    first = segment(scene)
    assert scene == original
    json.dumps(first, allow_nan=False)
    scene[0]["bodies"][0]["position_xyz_m"][0] = 1
    second = segment(scene)
    assert first["occupancy"]["input_sha256"] != second["occupancy"]["input_sha256"]
