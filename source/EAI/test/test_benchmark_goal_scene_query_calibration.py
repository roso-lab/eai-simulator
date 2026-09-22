from demo.benchmark.goal_scene_query_calibration import calibrate_scene_queries
from types import SimpleNamespace
import sys


class _Query:
    def __init__(self):
        self.hits = [{"collision": "/World/iris/body", "rigid_body": "/World/iris",
                      "protoIndex": 256, "distance": .2},
                     {"collision": "/World/m20/base_link", "rigid_body": "/World/m20",
                      "protoIndex": 0xFFFFFFFF, "distance": .3}]

    def overlap_box(self, half, center, rotation, callback, any_hit):
        assert not any_hit
        if center[2] == 100:
            return 0
        count = 0
        for hit in self.hits:
            count += 1
            if not callback(hit):
                break
        return count

    def sweep_box_all(self, half, center, rotation, direction, distance, callback, both_sides):
        assert both_sides and direction == (1., 0., 0.)
        for hit in self.hits:
            if not callback(hit):
                break
        return True


class _Prim:
    def __init__(self, path, valid=True):
        self.path, self.valid = path, valid

    def IsValid(self):
        return self.valid

    def GetTypeName(self):
        return "Mesh"

    def IsInstance(self):
        return False

    def IsInstanceProxy(self):
        return False

    def IsInstanceable(self):
        return False

    def GetPath(self):
        return self.path

    def GetParent(self):
        if self.path == "/":
            return None
        return _Prim(self.path.rsplit("/", 1)[0] or "/")


class _Stage:
    def GetPrimAtPath(self, path):
        return _Prim(path)

    def Traverse(self):
        return iter((_Prim("/World/iris/body"), _Prim("/World/m20/base_link")))


def test_calibration_preserves_non_sentinel_and_early_stop_without_promoting_clearance():
    entities = []
    for identifier, path in (("warehouse_aerial_1", "/World/iris/body"),
                             ("warehouse_ground_1", "/World/m20/base_link")):
        entities.append({"id": identifier, "bodies": [
            {"body_id": path, "collision_paths": [path],
             "world_aabb_m": [[0., 0., 0.], [1., 1., 1.]]}]})
    objects = {"objects": [{"public_id": "order_crate_01", "prim_path": "/World/crate",
                            "physx": {"position_m": [3., 4., .3]}}]}
    result = calibrate_scene_queries(_Query(), {"entities": entities}, _Stage(),
                                     physics_step=42, object_readback=objects)
    first = result["cases"][0]["queries"]
    assert first[0]["raw_return"] == first[0]["callback_count"] == 2
    assert first[0]["callbacks"][0]["protoIndex"] == 256
    assert first[0]["callbacks"][0]["collision_encoded"] is None
    assert first[1]["argument_mode"] == "sdk_types"
    assert first[2]["raw_return"] == first[2]["callback_count"] == 1
    assert first[3]["raw_return"] is True
    assert result["cases"][2]["id"] == "order_crate_01"
    assert result["cases"][2]["queries"][0]["center_xyz_m"] == [3., 4., .3]
    assert result["hit_prim_metadata"][0]["is_instance_proxy"] is False
    assert result["cases"][-1]["queries"][0]["raw_return"] == 0
    assert result["physics_step"] == 42
    assert result["stage_instance_inventory"]["prim_count"] == 2
    assert result["stage_instance_inventory"]["point_instancer_paths"] == []
    assert result["scene_clearance_validated"] is False
    assert result["scoreable"] is False


def test_property_cross_check_distinguishes_exact_self_and_foreign_hits(monkeypatch):
    paths = {"warehouse_aerial_1": ("/World/iris/body", "/World/iris/body/collider"),
             "warehouse_ground_1": ("/World/m20/base_link", "/World/m20/base_link/collider")}
    encoded = {1: paths["warehouse_aerial_1"][1], 2: paths["warehouse_aerial_1"][0],
               3: paths["warehouse_ground_1"][1], 4: paths["warehouse_ground_1"][0]}
    monkeypatch.setitem(sys.modules, "pxr", SimpleNamespace(PhysicsSchemaTools=SimpleNamespace(
        decodeSdfPath=lambda first, second: encoded[first])))
    query = _Query()
    query.hits = [
        {"collision": collision, "rigid_body": body, "collision_encoded": [collider_code, 0],
         "rigid_body_encoded": [body_code, 0], "protoIndex": 256, "distance": .2}
        for (body, collision), collider_code, body_code in (
            (paths["warehouse_aerial_1"], 1, 2), (paths["warehouse_ground_1"], 3, 4))]
    entities = []
    for identifier, (body, collision) in paths.items():
        property_query = {"complete": True, "physics_step": 42}
        entities.append({"id": identifier, "bodies": [{
            "body_id": body, "world_aabb_m": [[0., 0., 0.], [1., 1., 1.]],
            "collision_paths": [collision],
            "pose_source": {"physics_step": 42, "configuration_revision": "fixed"},
            "bounds_source": {"kind": "physx_collision_bounds", "physics_step": 42,
                "configuration_revision": "fixed", "covers_all_enabled_colliders": True,
                "collider_measurements": [{"collision_path": collision,
                    "property_callback": {"path": collision}, "query": property_query}]},
        }]})
    objects = {"objects": [{"public_id": "order_crate_01", "prim_path": "/World/crate",
                            "physx": {"position_m": [3., 4., .3]}}]}
    result = calibrate_scene_queries(query, {"entities": entities}, _Stage(),
                                     physics_step=42, object_readback=objects)
    aerial = [row for row in result["hit_path_cross_checks"]
              if row["case_id"] == "warehouse_aerial_1" and row["operation"] == "overlap"]
    assert aerial[0]["relation"] == "property_matched_self_path"
    assert aerial[1]["relation"] == "property_matched_other_robot_path"
    assert aerial[0]["encoded_paths_agree"] is True
    assert result["scene_clearance_validated"] is False

    entities[0]["bodies"][0]["bounds_source"]["covers_all_enabled_colliders"] = False
    result = calibrate_scene_queries(query, {"entities": entities}, _Stage(),
                                     physics_step=42, object_readback=objects)
    aerial = [row for row in result["hit_path_cross_checks"]
              if row["case_id"] == "warehouse_aerial_1" and row["operation"] == "overlap"]
    assert aerial[0]["relation"] == "unknown_identity"
    assert aerial[1]["relation"] == "property_matched_other_robot_path"

    ground = entities[1]["bodies"][0]
    ground["bounds_source"] = {"kind": "usd_collision_bounds", "physics_step": 42}
    ground["bounds_source"]["configuration_revision"] = "fixed"
    snapshot = {"bodies": [{"id": ground["body_id"],
        "local_collision_bounds_m": [[-.5] * 3, [.5] * 3],
        "bounds_source": {"physics_step": 42, "configuration_revision": "fixed"}}]}
    collision = ground["collision_paths"][0]
    measurement = {"collision_path": collision, "property_callback": {"path": collision},
                   "query": {"complete": True, "physics_step": 42, "body_path": ground["body_id"]}}
    result = calibrate_scene_queries(query, {"entities": entities}, _Stage(),
                                     physics_step=42, object_readback=objects,
                                     snapshot=snapshot,
                                     property_reader=lambda stage, body, colliders:
                                         ([[-.5] * 3, [.5] * 3], [measurement]))
    assert result["property_controls"][0]["status"] == "property_matched"
    aerial = [row for row in result["hit_path_cross_checks"]
              if row["case_id"] == "warehouse_aerial_1" and row["operation"] == "overlap"]
    assert aerial[1]["relation"] == "property_matched_other_robot_path"

    measurement["query"]["complete"] = False
    result = calibrate_scene_queries(query, {"entities": entities}, _Stage(),
                                     physics_step=42, object_readback=objects,
                                     snapshot=snapshot,
                                     property_reader=lambda stage, body, colliders:
                                         ([[-.5] * 3, [.5] * 3], [measurement]))
    assert result["property_controls"][0]["status"] == "unknown"
    assert [row for row in result["hit_path_cross_checks"]
            if row["case_id"] == "warehouse_aerial_1" and row["operation"] == "overlap"][1]["relation"] == "unknown_identity"

    measurement["query"]["complete"] = True
    measurement["query"]["physics_step"] = 41
    result = calibrate_scene_queries(query, {"entities": entities}, _Stage(),
                                     physics_step=42, object_readback=objects,
                                     snapshot=snapshot,
                                     property_reader=lambda stage, body, colliders:
                                         ([[-.5] * 3, [.5] * 3], [measurement]))
    assert result["property_controls"][0]["status"] == "unknown"

    measurement["query"]["physics_step"] = 42
    query.hits[1]["collision_encoded"] = [1, 0]
    result = calibrate_scene_queries(query, {"entities": entities}, _Stage(),
                                     physics_step=42, object_readback=objects,
                                     snapshot=snapshot,
                                     property_reader=lambda stage, body, colliders:
                                         ([[-.5] * 3, [.5] * 3], [measurement]))
    assert result["property_controls"][0]["status"] == "property_matched"
    assert [row for row in result["hit_path_cross_checks"]
            if row["case_id"] == "warehouse_aerial_1" and row["operation"] == "overlap"][1]["relation"] == "unknown_identity"
