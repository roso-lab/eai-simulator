"""Observe Factory schema ownership and task writes before a static-sharing trial."""

from pathlib import Path

from tools.physics_replay.store import _atomic_json_write


class FactoryStaticAudit:
    """Read-only audit of the live fixed workload, never permission to share."""

    def __init__(self, stage, count, output):
        from pxr import Tf, Usd, UsdGeom, UsdPhysics

        self.output = Path(output)
        self.groups = {}
        self.writes = {}
        physical = (UsdPhysics.RigidBodyAPI, UsdPhysics.ArticulationRootAPI)
        joints = []
        for prim in stage.Traverse():
            if prim.IsA(UsdPhysics.Joint):
                joint = UsdPhysics.Joint(prim)
                joints.extend(str(path) for relation in (joint.GetBody0Rel(), joint.GetBody1Rel())
                              for path in relation.GetTargets())
        for index in range(count):
            root = stage.GetPrimAtPath(f"/World/envs/env_{index}/Indoor")
            for group in root.GetChildren():
                path = str(group.GetPath())
                descendants = list(Usd.PrimRange(group))
                reasons = []
                if any(any(prim.HasAPI(schema) for schema in physical)
                       or prim.IsA(UsdPhysics.Joint) for prim in descendants):
                    reasons.append("rigid_body_articulation_or_joint_schema")
                if any(target == path or target.startswith(path + "/") for target in joints):
                    reasons.append("joint_endpoint")
                collider_count = sum(prim.HasAPI(UsdPhysics.CollisionAPI) for prim in descendants)
                mesh_bytes = 0
                for prim in descendants:
                    if prim.IsA(UsdGeom.Mesh):
                        mesh = UsdGeom.Mesh(prim)
                        mesh_bytes += len(mesh.GetPointsAttr().Get() or ()) * 12
                        mesh_bytes += len(mesh.GetFaceVertexIndicesAttr().Get() or ()) * 4
                self.groups[path] = {"slot": index, "prims": len(descendants),
                                     "colliders": collider_count, "raw_mesh_bytes": mesh_bytes,
                                     "task_owned_reasons": reasons}
        self.notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self.changed, stage)
        self.persist()

    def changed(self, notice, sender):
        paths = set(notice.GetResyncedPaths()) | set(notice.GetChangedInfoOnlyPaths())
        for changed in paths:
            path = str(changed.GetPrimPath())
            for group in self.groups:
                if (path == group or path.startswith(group + "/") or group.startswith(path + "/")):
                    values = self.writes.setdefault(group, set())
                    if len(values) < 64:
                        values.add(str(changed))

    def persist(self):
        groups = {path: dict(values, observed_write_examples=sorted(self.writes.get(path, ())))
                  for path, values in self.groups.items()}
        report = {"groups": groups, "static_sharing_safe": False,
                  "candidate_groups_without_schema_ownership_or_observed_writes": [
                      path for path, values in groups.items()
                      if values["colliders"] and not values["task_owned_reasons"]
                      and not values["observed_write_examples"]],
                  "scope": "USD schema and observed write candidates for this workload only; "
                           "requires task code audit and independent physical comparison before sharing"}
        _atomic_json_write(self.output, report)

    def close(self):
        self.notice.Revoke()
        self.persist()
