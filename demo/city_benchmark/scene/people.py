"""Background pedestrians following short authored sidewalk segments."""
from __future__ import annotations

import hashlib
import math
from copy import deepcopy
from typing import Any, Mapping

from demo.city_benchmark.scene.scenario import _distance_to_segment_xy

ASSET_IDS = ("rp-aaron-rigged-001", "rp-amit-rigged-008-yup-t",
             "synbody-0000001", "synbody-0000020")


class EpisodeAffectedPersonRoster:
    """Validated episode binding for real human actors.

    Background scene records are deliberately not consumed here.  The host
    must name the actors selected for this episode and provide their service
    motion parameters explicitly.
    """

    def __init__(self, people_runtime: Any, bindings: Mapping[str, Mapping[str, Any]], *,
                 episode_id: str, source_id: str):
        known = {str(actor_id) for actor_id in getattr(people_runtime, "actor_ids", ())}
        if not isinstance(bindings, Mapping) or not bindings:
            raise ValueError("affected-person bindings must be a nonempty mapping")
        self._rows: dict[str, dict[str, Any]] = {}
        for actor_id, spec in bindings.items():
            actor_id = str(actor_id)
            if actor_id not in known:
                raise ValueError(f"affected-person binding names unknown actor: {actor_id}")
            if not isinstance(spec, Mapping):
                raise ValueError("affected-person binding rows must be mappings")
            speed = float(spec.get("speed_mps", 0.0))
            if not math.isfinite(speed) or speed <= 0:
                raise ValueError("affected-person speed_mps must be positive and finite")
            self._rows[actor_id] = {
                **dict(spec), "id": actor_id, "kind": "person",
                "mode": "affected_episode_roster", "speed_mps": speed,
                "episode_id": str(episode_id), "source_id": str(source_id),
            }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._rows)


class EpisodeAffectedPeopleController:
    """Install bounded, non-looping assembly paths on existing USD humans."""

    def __init__(self, people_runtime: Any, roster: EpisodeAffectedPersonRoster):
        self.people_runtime = people_runtime
        self.roster = roster
        self._assignments: dict[str, dict[str, Any]] = {}
        self._paused_by_failure: set[str] = set()

    def guide(self, actor_id: str, *, assembly_region_id: str | None = None,
              waypoints: Any = None) -> dict[str, Any]:
        actor_id = str(actor_id)
        spec = self.roster.snapshot().get(actor_id)
        if spec is None:
            raise ValueError("guidance actor is outside the affected-person roster")
        requested = waypoints is not None
        selected = tuple(tuple(float(value) for value in point)
                         for point in (waypoints if requested else
                                       spec.get("assembly_waypoints", ())))
        if not selected:
            raise ValueError("affected person requires assembly_waypoints")
        current = tuple(float(value) for value in self.people_runtime.position(actor_id))
        if len(current) != 3 or any(len(point) != 3 for point in selected):
            raise ValueError("assembly waypoints must contain XYZ triples")
        if requested:
            if not isinstance(assembly_region_id, str) or not assembly_region_id:
                raise ValueError("requested guidance requires an assembly region")
            if len(selected) < 2 or any(abs(selected[0][axis] - current[axis]) > 1e-9
                                        for axis in range(3)):
                raise ValueError("requested guidance route must start at the current pose")
            path = selected
            target_region = assembly_region_id
        else:
            path = (current, *selected)
            target_region = str(spec["assembly_region_id"])
        from EAI_assets.humans.path_follower import PathFollower
        actor = self.people_runtime._actor(actor_id)
        follower = PathFollower(
            actor_id=actor_id, waypoints=path,
            speed=float(spec["speed_mps"]), loop=False,
            locomotion=str(spec.get("motion", "walk")),
            movement_gate=actor._movement_gate)
        actor._follower = follower
        actor._movement_mode = "path"
        pose = follower.reset()
        actor._pending_pose = pose
        self.people_runtime._write_pose(actor_id, pose)
        self._assignments[actor_id] = {
            "assembly_region_id": target_region,
            "final_waypoint": list(path[-1]), "loop": False,
        }
        if actor_id in self._paused_by_failure:
            self.people_runtime.resume(actor_id)
            self._paused_by_failure.remove(actor_id)
        return {"entity_id": actor_id, "status_before": "awaiting_guidance",
                "status_after": "guidance_applied", "position_xyz": list(current),
                "assembly_region_id": target_region, "loop": False}

    def stop(self, actor_id: str) -> dict[str, Any]:
        actor_id = str(actor_id)
        if actor_id not in self.roster.snapshot():
            raise ValueError("stop actor is outside the affected-person roster")
        self.people_runtime.pause(actor_id)
        self._paused_by_failure.add(actor_id)
        self._assignments.pop(actor_id, None)
        return {"entity_id": actor_id, "status_before": "guidance_applied",
                "status_after": "paused", "position_xyz": list(self.people_runtime.position(actor_id))}

    def readback(self, actor_id: str, assembly_regions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        result = self.motion_readback(actor_id)
        region = assembly_regions[result["assembly_region_id"]]
        low = tuple(float(value) for value in region["min"])
        high = tuple(float(value) for value in region["max"])
        position = tuple(result["position_xyz"])
        inside = all(low[index] <= position[index] <= high[index] for index in range(3))
        return {**result, "inside_assembly": inside,
                "arrived": result["path_finished"] and inside}

    def motion_readback(self, actor_id: str) -> dict[str, Any]:
        """Return assignment and motion state without imposing region geometry."""
        actor_id = str(actor_id)
        assignment = self._assignments.get(actor_id)
        if assignment is None:
            raise ValueError("affected person has no assembly assignment")
        actor = self.people_runtime._actor(actor_id)
        position = tuple(float(value) for value in self.people_runtime.position(actor_id))
        finished = bool(actor.pending_pose is not None and actor.pending_pose.finished)
        return {"person_id": actor_id, "position_xyz": list(position),
                "assembly_region_id": assignment["assembly_region_id"],
                "path_finished": finished, "loop": False}


def sidewalk_paths(scene, routes, count):
    """Use non-crosswalk segments, rejecting building and incident-route overlap."""
    if count <= 0:
        return []
    selected = []
    for route in routes:
        for index, (a, b) in enumerate(zip(route["points"], route["points"][1:])):
            if route["segment_crosswalk_ids"][index] is not None:
                continue
            length = math.dist(a, b)
            if length < 10:
                continue
            mid = tuple((a[i] + b[i]) / 2 for i in range(2))
            direction = tuple((b[i] - a[i]) / length for i in range(2))
            # Six-metre out-and-back walk, away from crossing endpoints.
            samples = [tuple(mid[i] + t * direction[i] for i in range(2)) for t in range(-3, 4)]
            unsafe = False
            for point in samples:
                if any(item.bounds[0][0] - 0.8 <= point[0] <= item.bounds[1][0] + 0.8
                       and item.bounds[0][1] - 0.8 <= point[1] <= item.bounds[1][1] + 0.8
                       for item in scene.buildings):
                    unsafe = True
                if min(_distance_to_segment_xy(point, p, q) for p, q in
                       zip(scene.plan.route.points, scene.plan.route.points[1:])) < 6.0:
                    unsafe = True
                if math.dist(point, scene.plan.facade_point) < 12.0:
                    unsafe = True
                if any(math.dist(point, robot) < 5.0 for robot in ((-69, 49), (-65, 49), (-61, 49))):
                    unsafe = True
            if unsafe or any(math.dist(mid, entry["center"]) < 14 for entry in selected):
                continue
            selected.append({"center": mid, "route_id": route["route_id"],
                "yaw": math.atan2(direction[1], direction[0]),
                "waypoints": [(*samples[0], scene.config.ground_height),
                              (*samples[-1], scene.config.ground_height),
                              (*samples[0], scene.config.ground_height)]})
            if len(selected) == count:
                return selected
    return selected


def spawn_background_people(stage, scene, routes, count=4):
    """Reuse EAI's registry, retarget caches, grounding and path-following runtime."""
    if count == 0:
        return None, []
    from pxr import Usd, UsdPhysics
    from EAI_assets.humans.registry import HumanAssetRegistry
    from EAI_assets.humans.stage_runtime import HumanActorConfig, UsdHumanStageRuntime
    from EAI_assets.humans.asset_placement import grounding_world_range

    registry = HumanAssetRegistry.load_default()
    runtime = UsdHumanStageRuntime(stage, registry, cache_root=registry.human_root / "motions/cache")
    records = []
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for index, placement in enumerate(sidewalk_paths(scene, routes, count)):
            asset = registry.asset(ASSET_IDS[index % len(ASSET_IDS)])
            if not asset.usd_path.is_file():
                raise FileNotFoundError(f"Background human asset missing: {asset.usd_path}; use --people 0 to disable")
            actor_id = f"person_{index:02d}"
            path = f"/World/CityPeople/{actor_id}"
            waypoints, support = bind_sidewalk_support(
                stage, placement["waypoints"], city_root_path=scene.config.city_root_path)
            before = hashlib.sha256(asset.usd_path.read_bytes()).hexdigest()
            speed = 0.65 + index * 0.05
            runtime.spawn(HumanActorConfig(actor_id=actor_id, asset_id=asset.id,
                prim_path=path, initial_pose=(*waypoints[0], placement["yaw"]),
                waypoints=waypoints, speed=speed, loop=True, locomotion_motion_id="walk"))
            for prim in Usd.PrimRange(stage.GetPrimAtPath(path)):
                if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False)
                if prim.HasAPI(UsdPhysics.CollisionAPI):
                    UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
            bounds = grounding_world_range(stage.GetPrimAtPath(path))
            records.append({"actor_id": actor_id, "path": path, "asset_id": asset.id,
                "source": str(asset.usd_path), "source_hash": before,
                "initial_position": list(runtime.position(actor_id)),
                "waypoints": waypoints, "route_id": placement["route_id"], "speed_mps": speed,
                "support_binding": support,
                "pose": "walk", "mode": "background_path", "collision": False,
                "height_m": float(bounds.GetMax()[2] - bounds.GetMin()[2]),
                "foot_z": float(bounds.GetMin()[2]),
                "source_unchanged": hashlib.sha256(asset.usd_path.read_bytes()).hexdigest() == before})
    return runtime, records


def bind_sidewalk_support(stage, waypoints, *, city_root_path):
    """Bind spawn-time path heights to authored sidewalk triangles.

    This is a source-geometry binding, not a PhysX clearance claim. Require
    level, continuous sampled support across a 1m-wide corridor; runtime
    sweeps still check obstacles. Nothing is moved after simulation starts.
    """
    import numpy as np
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
    triangles, paths = [], []
    for prim in Usd.PrimRange(stage.GetPrimAtPath(city_root_path)):
        if not prim.IsA(UsdGeom.Mesh) or "/Generated/Sidewalks/" not in str(prim.GetPath()):
            continue
        collision = UsdPhysics.CollisionAPI(prim)
        if not prim.HasAPI(UsdPhysics.CollisionAPI) or collision.GetCollisionEnabledAttr().Get() is False:
            continue
        mesh = UsdGeom.Mesh(prim)
        transform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        points = [tuple(transform.Transform(Gf.Vec3d(*p))) for p in mesh.GetPointsAttr().Get()]
        indices = list(mesh.GetFaceVertexIndicesAttr().Get())
        cursor = 0
        for count in mesh.GetFaceVertexCountsAttr().Get():
            face = indices[cursor:cursor+count]
            cursor += count
            for index in range(1, count-1):
                tri = [points[face[k]] for k in (0, index, index+1)]
                # City sidewalk tops are planar and horizontal. Vertical faces
                # and sloped/unknown surfaces must not become support.
                if max(p[2] for p in tri)-min(p[2] for p in tri) < 1e-5:
                    triangles.append(tri)
                    paths.append(str(prim.GetPath()))
    if not triangles:
        raise ValueError("no collidable sidewalk support triangles")
    tri = np.asarray(triangles, dtype=float)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    denominator = (b[:, 1]-c[:, 1])*(a[:, 0]-c[:, 0]) + (c[:, 0]-b[:, 0])*(a[:, 1]-c[:, 1])
    valid = np.abs(denominator) > 1e-10
    safe_denominator = np.where(valid, denominator, 1.)
    samples = []
    for start, end in zip(waypoints, waypoints[1:]):
        length = math.dist(start[:2], end[:2])
        if length <= 1e-8:
            continue
        normal = (-(end[1]-start[1])/length, (end[0]-start[0])/length)
        for index in range(math.ceil(length/.25)+1):
            fraction = min(index*.25/length, 1.)
            for lateral in (-.5, 0., .5):
                x, y = (start[k]+fraction*(end[k]-start[k])+lateral*normal[k] for k in range(2))
                u = ((b[:, 1]-c[:, 1])*(x-c[:, 0])+(c[:, 0]-b[:, 0])*(y-c[:, 1]))/safe_denominator
                v = ((c[:, 1]-a[:, 1])*(x-c[:, 0])+(a[:, 0]-c[:, 0])*(y-c[:, 1]))/safe_denominator
                hits = np.flatnonzero(valid & (u >= -1e-7) & (v >= -1e-7) & (u+v <= 1.+1e-7))
                if not len(hits):
                    raise ValueError(f"sidewalk support missing at {(x, y)}")
                hit = max(hits, key=lambda j: a[j, 2])
                samples.append({"position_m": [float(x), float(y), float(a[hit, 2])],
                                "collision_path": paths[hit]})
    if not samples or max(r["position_m"][2] for r in samples)-min(r["position_m"][2] for r in samples) > .005:
        raise ValueError("sidewalk path lacks continuous level support")
    height = max(r["position_m"][2] for r in samples)
    return tuple((float(p[0]), float(p[1]), height) for p in waypoints), {
        "source": "authored_collidable_sidewalk_triangles", "height_m": height,
        "sample_spacing_m": .25, "corridor_half_width_m": .5, "samples": samples}
