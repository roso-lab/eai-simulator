"""Injected tests for the fail-closed PhysX cooked-shape adapter."""

from types import SimpleNamespace

from demo.benchmark.goal_native_shape_query import (
    collect_property_query,
    request_convex_collision_representation,
)


CONTEXT = {
    "scene_id": "factory",
    "snapshot_id": "s-1",
    "source_id": "physx-host",
    "stage_identifier": "stage.usd",
    "physics_step": 21,
    "frame_id": "USD_world",
    "physx_view_frame": "USD_world",
    "stage_cache_id": 1,
    "configuration_revision": "cfg-1",
    "readback_frozen": True,
    "kinematics_current": True,
}


def response(**changes):
    values = dict(
        result="VALID", path="/World/Robot/collider", path_id=4, stage_id=1,
        aabb_local_min=(-.5, -.5, -.5), aabb_local_max=(.5, .5, .5),
        local_pos=(0., 0., 0.), local_rot=(0., 0., 0., 1.), volume=1., mass=4.,
    )
    values.update(changes)
    return SimpleNamespace(**values)


class Query:
    def __init__(self, *, finish=True, valid=True, duplicate_path=False):
        self.finish = finish
        self.valid = valid
        self.duplicate_path = duplicate_path

    def query_prim(self, **kwargs):
        result = "VALID" if self.valid else "ERROR_TIMEOUT"
        kwargs["rigid_body_fn"](response(result=result, path="/World/Robot"))
        kwargs["collider_fn"](response(result=result))
        if self.duplicate_path:
            kwargs["collider_fn"](response(path="/World/Robot/collider"))
        if self.finish:
            kwargs["finished_fn"]()


def run(query, **context_changes):
    context = {**CONTEXT, **context_changes}
    return collect_property_query(query, query_kwargs={"stage_id": 1, "prim_id": 2},
                                  context=context, expected_body_path="/World/Robot")


def test_valid_query_keeps_callback_provenance_and_shape_fields():
    report = run(Query())
    assert report["status"] == "completed"
    assert report["provenance"]["physics_step"] == 21
    assert report["callback_counts"] == {"rigid_body": 1, "collider": 1}
    assert report["callbacks"]["collider"][0]["aabb_local_min_m"] == [-.5, -.5, -.5]
    assert report["measurement_authenticity_validated"] is False


def test_duplicate_shape_paths_are_preserved_for_compound_audit():
    report = run(Query(duplicate_path=True))
    assert report["status"] == "completed"
    assert report["callback_counts"]["collider"] == 2
    assert len(report["callbacks"]["collider"]) == 2


def test_missing_finished_callback_is_timeout_blocker():
    report = run(Query(finish=False))
    assert report["status"] == "blocked"
    assert "query_incomplete_or_timeout" in report["blockers"]


def test_invalid_result_blocks_even_if_finished():
    report = run(Query(valid=False))
    assert report["status"] == "blocked"
    assert "missing_valid_rigid_body_callback" in report["blockers"]
    assert "missing_valid_collider_callback" in report["blockers"]


def test_bad_context_does_not_call_interface():
    query = Query()
    report = run(query, physx_view_frame="local")
    assert report["status"] == "blocked"
    assert "query_setup_or_execution_error" in report["blockers"][0]


def test_convex_request_is_acceptance_only_until_readback():
    class Request:
        def request_convex_collision_representation(self, **kwargs):
            kwargs["finished_fn"]()

    class ConvexRequest:
        def request_convex_collision_representation(self, **kwargs):
            kwargs["on_result"]("RESULT_VALID", [object()])

    report = request_convex_collision_representation(
        ConvexRequest(), stage_id=1, prim_id=2, finished_fn=lambda: None, error_fn=lambda _: None)
    assert report["status"] == "accepted_only"
    assert report["result"] == "RESULT_VALID"
    assert report["cooked_geometry_validated"] is False


def test_missing_convex_request_api_fails_closed():
    report = request_convex_collision_representation(
        object(), stage_id=1, prim_id=2, finished_fn=lambda: None, error_fn=lambda _: None)
    assert report["status"] == "blocked"
    assert report["blockers"] == ["convex_request_api_unavailable"]
