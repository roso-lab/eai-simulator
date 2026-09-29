"""Current PhysX property bounds for the paired carry diagnostic.

Local AABBs are transformed using the collider-to-body USD matrix, as in
Isaac's OgnPhysXPropertyQueryRigidBody. Callback local_pos/local_rot are
archived but are not applied a second time. No public robot dimensions enter
this calculation. Goal occupancy uses it only for narrowly supported cooked
or Xform colliders with complete same-step property callbacks.
"""
from __future__ import annotations

import itertools
import math
import time


def query_body(query, *, stage_id, body_id, body_path, decode, pump, step,
               query_mode=None, timeout_s=2., allow_incomplete=False):
    start_step = step()
    rows, bodies, errors, finished = [], [], [], []

    def identity(response):
        if str(getattr(response.result, "name", response.result)).split(".")[-1] != "VALID":
            raise ValueError(f"native query failed: {response.result}")
        if response.stage_id != stage_id:
            raise ValueError("foreign stage in property callback")
        return str(decode(response.path_id))

    def vector(value, length):
        result = [float(x) for x in value]
        if len(result) != length or not all(math.isfinite(x) for x in result):
            raise ValueError("nonfinite or incomplete property vector")
        return result

    def body(response):
        try:
            if identity(response) != body_path:
                raise ValueError("foreign body in property callback")
            bodies.append({"path": body_path, "mass_kg": float(response.mass)})
        except Exception as exc:
            errors.append(str(exc))

    def collider(response):
        try:
            path = identity(response)
            low, high = vector(response.aabb_local_min, 3), vector(response.aabb_local_max, 3)
            if any(a >= b for a, b in zip(low, high)):
                raise ValueError("degenerate property bounds")
            if not math.isfinite(float(response.volume)) or float(response.volume) <= 0:
                raise ValueError("invalid property shape volume")
            rows.append({"path": path, "aabb_local_min": low, "aabb_local_max": high,
                         "local_pos": vector(response.local_pos, 3),
                         "local_rot_xyzw": vector(response.local_rot, 4),
                         "volume": float(response.volume), "ordinal": len(rows)})
        except Exception as exc:
            errors.append(str(exc))

    if query_mode is None:
        try:
            from omni.physx.bindings._physx import PhysxPropertyQueryMode
            query_mode = PhysxPropertyQueryMode.QUERY_RIGID_BODY_WITH_COLLIDERS
        except ModuleNotFoundError:
            # Lightweight contract fakes do not load Kit. Isaac's enum value
            # is zero, matching QUERY_RIGID_BODY_WITH_COLLIDERS.
            query_mode = 0
    query.query_prim(stage_id=stage_id, prim_id=body_id, query_mode=query_mode,
                     timeout_ms=round(timeout_s*1000),
                     rigid_body_fn=body, collider_fn=collider, finished_fn=lambda: finished.append(True))
    deadline = time.monotonic()+timeout_s
    while not finished and time.monotonic() < deadline:
        pump()
        if step() != start_step:
            raise ValueError("physics advanced during shape query")
    incomplete = step() != start_step or len(finished) != 1 or len(bodies) != 1 or not rows or bool(errors)
    if incomplete and not (allow_incomplete and not errors and len(finished) == 1 and len(bodies) == 1):
        raise ValueError(f"incomplete native shape query: {errors}, finished={len(finished)}, bodies={len(bodies)}")
    return {"body_path": body_path, "physics_step": start_step, "stage_cache_id": stage_id,
            "body": bodies[0] if bodies else None, "colliders": rows,
            "finished_count": len(finished), "complete": not incomplete}


def cook_meshes(cooking, *, stage_id, collider_ids, collider_paths=(), pump, step, timeout_s=8.):
    """Request and verify every deferred mesh representation in one step."""
    start_step = step()
    records = []
    for ordinal, collider_id in enumerate(collider_ids):
        collider_path = (tuple(collider_paths)[ordinal]
                         if ordinal < len(tuple(collider_paths)) else str(collider_id))
        state = {"finished": 0, "result": None, "convex_count": 0, "errors": []}

        def result(value, convexes, state=state):
            state["result"] = str(getattr(value, "name", value))
            try:
                state["convex_count"] = len(convexes)
            except TypeError:
                state["errors"].append("convex_result_not_sized")
            state["finished"] += 1

        try:
            task = cooking.request_convex_collision_representation(
                stage_id=stage_id, collision_prim_id=collider_id,
                run_asynchronously=True, on_result=result)
        except (TypeError, RuntimeError, AttributeError) as exc:
            raise ValueError(f"convex cooking request failed for {collider_path}: {exc}") from exc
        deadline = time.monotonic() + timeout_s
        while state["finished"] == 0 and time.monotonic() < deadline:
            pump()
            if step() != start_step:
                raise ValueError("physics advanced during convex cooking")
        if (state["finished"] != 1 or state["result"] not in
                ("RESULT_VALID", "PhysxCollisionRepresentationResult.RESULT_VALID")
                or state["convex_count"] <= 0 or state["errors"]):
            cancel = getattr(cooking, "cancel_collision_representation_task", None)
            if state["finished"] == 0 and cancel is not None:
                cancel(task, invoke_callback=False)
            raise ValueError(f"convex cooking incomplete for {collider_path}: {state}")
        records.append({"collision_prim_id": collider_id, "collision_path": collider_path, **state})
    if step() != start_step:
        raise ValueError("physics advanced during convex cooking")
    return records


def body_geometry(stage, body, colliders, *, session, expected_step=None):
    import omni.physx
    from pxr import Gf, PhysicsSchemaTools, UsdGeom, UsdUtils
    from demo.benchmark import goal_native_occupancy as occupancy

    from demo.benchmark.gpu_evidence_contract import admit_live_session

    # Stage141 CUDA evidence stays valid. A later readback gate uses the
    # synchronized GPU seam. CPU may still run as exploratory debug, and a
    # suppressed or unsynchronized GPU query fails closed instead of falling
    # back to CPU.
    admitted, readback = admit_live_session(session)
    if str(session.device).startswith("cuda") and not admitted:
        raise ValueError(f"geometry readback fail-closed: {readback['reason']}")
    if not str(session.device).startswith("cuda"):
        suppressed = omni.physx.get_physx_interface().is_readback_suppressed()
        if suppressed is not False:
            raise ValueError("exploratory CPU geometry requires an unsuppressed readback")
    del readback
    stage_id = UsdUtils.StageCache.Get().Insert(stage).ToLongInt()
    from omni.physx.bindings._physx import PhysxPropertyQueryMode
    step = lambda: int(session.base_env.sim.current_time_step_index)
    query = omni.physx.get_physx_property_query_interface()
    query_kwargs = dict(stage_id=stage_id, body_id=PhysicsSchemaTools.sdfPathToInt(body.GetPath()),
        body_path=str(body.GetPath()), decode=PhysicsSchemaTools.intToSdfPath,
        pump=session.simulation_app.update, step=step,
        query_mode=PhysxPropertyQueryMode.QUERY_RIGID_BODY_WITH_COLLIDERS)
    query_kwargs["allow_incomplete"] = True
    report = query_body(query, **query_kwargs)
    cooking_evidence = []
    if not report["colliders"]:
        cooking = omni.physx.get_physx_cooking_interface()
        collider_ids = [PhysicsSchemaTools.sdfPathToInt(prim.GetPath()) for prim in colliders]
        cooking_evidence = cook_meshes(cooking, stage_id=stage_id, collider_ids=collider_ids,
                                       collider_paths=[str(prim.GetPath()) for prim in colliders],
                                       pump=session.simulation_app.update, step=step)
        report = query_body(query, **query_kwargs)
    if (not report["complete"] or (expected_step is not None and
            (report["physics_step"] != expected_step or step() != expected_step))):
        raise ValueError("incomplete or advanced PhysX property query")
    expected = {str(p.GetPath()): p for p in colliders}
    if len(report["colliders"]) != len(expected) or {row["path"] for row in report["colliders"]} != set(expected):
        raise ValueError("native/USD enabled collider coverage differs")
    cache = UsdGeom.XformCache()
    world = cache.GetLocalToWorldTransform(body)
    basis = [world.TransformDir(Gf.Vec3d(*(int(i == j) for j in range(3)))) for i in range(3)]
    scales = [float(v.GetLength()) for v in basis]
    if (any(not math.isfinite(s) or s <= 0 for s in scales) or world.GetDeterminant() <= 0
            or any(abs(float(basis[i]*basis[j])/(scales[i]*scales[j])) > 1e-6
                   for i in range(3) for j in range(i))):
        raise ValueError("unsupported sheared/reflected body frame")
    boxes, evidence = [], []
    for row in report["colliders"]:
        prim = expected[row["path"]]
        cursor = prim
        while cursor != body:
            xform = UsdGeom.Xformable(cursor)
            if xform and (xform.GetResetXformStack() or xform.TransformMightBeTimeVarying()):
                raise ValueError("animated/reset collider transform")
            cursor = cursor.GetParent()
        relative, reset = cache.ComputeRelativeTransform(prim, body)
        if reset:
            raise ValueError("collider resets body frame")
        low, high = row["aabb_local_min"], row["aabb_local_max"]
        # Known primitive shapes independently calibrate coordinate/scale
        # conventions. Mesh decomposition is supplied only by native callbacks.
        calibration = None
        if not prim.IsA(UsdGeom.Mesh) and prim.IsA(UsdGeom.Gprim):
            expected_low, expected_high, _ = occupancy._local_geometry(prim)
            error = max(abs(a-b) for a, b in zip(low+high, expected_low+expected_high))
            if error > 1e-4:
                raise ValueError(f"native primitive AABB calibration failed: {row['path']}: {error}")
            calibration = {"max_local_error_m": error}
        points = [relative.Transform(Gf.Vec3d(*p)) for p in itertools.product(*zip(low, high))]
        points = [[float(p[i])*scales[i] for i in range(3)] for p in points]
        bounds = [[min(p[i] for p in points) for i in range(3)],
                  [max(p[i] for p in points) for i in range(3)]]
        boxes.append(bounds)
        evidence.append({"collision_path": row["path"], "property_callback": row,
            "body_local_bounds_m": bounds, "collider_to_body_matrix": [list(r) for r in relative],
            "effective_body_scale": scales, "primitive_calibration": calibration,
            "cooking": cooking_evidence,
            "query": {k: v for k, v in report.items() if k != "colliders"}})
    return [[min(b[0][i] for b in boxes) for i in range(3)],
            [max(b[1][i] for b in boxes) for i in range(3)]], evidence
