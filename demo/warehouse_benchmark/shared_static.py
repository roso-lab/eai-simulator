"""Audited immutable Warehouse collision geometry for GPU branch batches."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


SOURCE_ROOT = "/World/envs/env_0/Warehouse"
COMMON_ROOT = "/World/SharedWarehouse/Warehouse"


def audited_group_names(path):
    """Return clean first-slot Warehouse groups from a live write audit."""
    payload = Path(path).read_bytes()
    audit = json.loads(payload)
    prefix = SOURCE_ROOT + "/"
    names = []
    for candidate in audit["candidate_groups_without_schema_ownership_or_observed_writes"]:
        if not isinstance(candidate, str):
            raise ValueError("Warehouse static audit paths must be strings")
        if not candidate.startswith(prefix):
            continue
        name = candidate[len(prefix):]
        entry = audit["groups"][candidate]
        if (not name or "/" in name or entry.get("slot") != 0
                or not entry.get("colliders")
                or entry.get("task_owned_reasons") != []
                or entry.get("observed_write_examples") != []):
            raise ValueError(f"Warehouse static group lacks clean evidence: {candidate}")
        names.append(name)
    if not names or len(set(names)) != len(names):
        raise ValueError("Warehouse static audit needs unique first-slot candidates")
    return tuple(sorted(names)), sha256(payload).hexdigest()


class WarehouseStaticAudit:
    """Observe schema ownership and writes during one complete task run."""

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
                joints.extend(
                    str(target)
                    for relation in (joint.GetBody0Rel(), joint.GetBody1Rel())
                    for target in relation.GetTargets()
                )
        for index in range(count):
            root = stage.GetPrimAtPath(f"/World/envs/env_{index}/Warehouse")
            if not root.IsValid():
                raise ValueError(f"Warehouse audit root is missing for slot {index}")
            for group in root.GetChildren():
                path = str(group.GetPath())
                descendants = list(Usd.PrimRange(group))
                reasons = []
                if any(any(prim.HasAPI(schema) for schema in physical)
                       or prim.IsA(UsdPhysics.Joint) for prim in descendants):
                    reasons.append("rigid_body_articulation_or_joint_schema")
                if any(target == path or target.startswith(path + "/") for target in joints):
                    reasons.append("joint_endpoint")
                collider_count = sum(
                    prim.HasAPI(UsdPhysics.CollisionAPI) for prim in descendants)
                mesh_bytes = 0
                for prim in descendants:
                    if prim.IsA(UsdGeom.Mesh):
                        mesh = UsdGeom.Mesh(prim)
                        mesh_bytes += len(mesh.GetPointsAttr().Get() or ()) * 12
                        mesh_bytes += len(mesh.GetFaceVertexIndicesAttr().Get() or ()) * 4
                self.groups[path] = {
                    "slot": index,
                    "prims": len(descendants),
                    "colliders": collider_count,
                    "raw_mesh_bytes": mesh_bytes,
                    "task_owned_reasons": reasons,
                }
        self.notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self.changed, stage)
        self.persist()

    def changed(self, notice, sender):
        paths = set(notice.GetResyncedPaths()) | set(notice.GetChangedInfoOnlyPaths())
        for changed in paths:
            path = str(changed.GetPrimPath())
            for group in self.groups:
                if path == group or path.startswith(group + "/") or group.startswith(path + "/"):
                    values = self.writes.setdefault(group, set())
                    if len(values) < 64:
                        values.add(str(changed))

    def persist(self):
        from tools.physics_replay.store import _atomic_json_write

        groups = {
            path: dict(values, observed_write_examples=sorted(self.writes.get(path, ())))
            for path, values in self.groups.items()
        }
        report = {
            "groups": groups,
            "static_sharing_safe": False,
            "candidate_groups_without_schema_ownership_or_observed_writes": [
                path for path, values in groups.items()
                if values["colliders"] and not values["task_owned_reasons"]
                and not values["observed_write_examples"]
            ],
            "scope": (
                "Warehouse USD schema and observed-write candidates for this complete workload; "
                "the branch runner independently revalidates topology and live immutability"
            ),
        }
        _atomic_json_write(self.output, report)

    def close(self):
        self.notice.Revoke()
        self.persist()


class SharedStaticWarehouse:
    """Reject writes to the common Warehouse collision geometry."""

    def __init__(self, stage, collider_paths, group_names, audit_sha256,
                 private_group_paths=()):
        from pxr import Tf, Usd

        self.collider_paths = frozenset(map(str, collider_paths))
        self.group_names = tuple(group_names)
        self.audit_sha256 = audit_sha256
        self.protected_roots = (COMMON_ROOT, *map(str, private_group_paths))
        self.write_examples = []
        self.notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._changed, stage)

    def _changed(self, notice, sender):
        for changed in (*notice.GetResyncedPaths(), *notice.GetChangedInfoOnlyPaths()):
            path = str(changed.GetPrimPath())
            if path == "/" or any(
                path == root or path.startswith(root + "/") or root.startswith(path + "/")
                for root in self.protected_roots
            ):
                if len(self.write_examples) < 8:
                    self.write_examples.append(str(changed))

    def validate_immutable(self):
        if self.write_examples:
            raise ValueError(
                f"shared Warehouse geometry was modified: {self.write_examples}")

    def contains_collider(self, path):
        return str(path) in self.collider_paths

    def report(self):
        return {
            "common_root": COMMON_ROOT,
            "group_names": list(self.group_names),
            "collider_paths": sorted(self.collider_paths),
            "audit_sha256": self.audit_sha256,
            "protected_roots": list(self.protected_roots),
            "write_examples": list(self.write_examples),
            "scope": (
                "schema/joint ownership, one-slot complete-workload write audit, "
                "and live common-geometry write guard"
            ),
        }

    def close(self):
        if self.notice is not None:
            self.notice.Revoke()
            self.notice = None


def _retain_groups_and_materials(root, names):
    from pxr import Usd, UsdShade

    for group in root.GetChildren():
        if group.GetName() in names:
            continue
        descendants = list(Usd.PrimRange(group))
        material_scope = (
            any(prim.IsA(UsdShade.Material) for prim in descendants)
            and all(prim.GetTypeName() in ("Scope", "Material", "Shader", "NodeGraph")
                    for prim in descendants)
        )
        if material_scope:
            if any(attr.GetNumTimeSamples() for prim in descendants
                   for attr in prim.GetAttributes()):
                raise ValueError(f"time-sampled common material: {group.GetPath()}")
        else:
            group.SetActive(False)


def prepare_gpu_shared_static_source(scene, audit_path):
    """Extract audited Warehouse colliders before GPU clone expansion."""
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    names, digest = audited_group_names(audit_path)
    stage = scene.stage
    source_root = stage.GetPrimAtPath(SOURCE_ROOT)
    if not source_root.IsValid():
        raise ValueError("GPU shared statics require the Warehouse source root")
    if stage.GetPrimAtPath(COMMON_ROOT).IsValid():
        raise ValueError("common Warehouse geometry is already present")
    joint_targets = [
        str(target)
        for prim in stage.Traverse() if prim.IsA(UsdPhysics.Joint)
        for relation in (UsdPhysics.Joint(prim).GetBody0Rel(),
                         UsdPhysics.Joint(prim).GetBody1Rel())
        for target in relation.GetTargets()
    ]
    selected = []
    for name in names:
        group = source_root.GetChild(name)
        if not group.IsValid():
            raise ValueError(f"audited Warehouse group is missing: {name}")
        descendants = list(Usd.PrimRange(group))
        if any(prim.HasAPI(UsdPhysics.RigidBodyAPI)
               or prim.HasAPI(UsdPhysics.ArticulationRootAPI)
               or prim.IsA(UsdPhysics.Joint) for prim in descendants):
            raise ValueError(f"dynamic schema in shared Warehouse group: {group.GetPath()}")
        if not any(prim.HasAPI(UsdPhysics.CollisionAPI) for prim in descendants):
            raise ValueError(f"shared Warehouse group lacks colliders: {group.GetPath()}")
        path = str(group.GetPath())
        if any(target == path or target.startswith(path + "/") for target in joint_targets):
            raise ValueError(f"joint endpoint in shared Warehouse group: {path}")
        if any(attr.GetNumTimeSamples() for prim in descendants
               for attr in prim.GetAttributes()):
            raise ValueError(f"time-sampled shared Warehouse group: {path}")
        selected.append(path)

    UsdGeom.Xform.Define(stage, "/World/SharedWarehouse")
    layer = stage.GetRootLayer()
    if not Sdf.CopySpec(layer, source_root.GetPath(), layer, COMMON_ROOT):
        raise RuntimeError("failed to copy the shared Warehouse reference")
    common = stage.GetPrimAtPath(COMMON_ROOT)
    transforms = UsdGeom.XformCache()
    if transforms.GetLocalToWorldTransform(source_root) != transforms.GetLocalToWorldTransform(common):
        raise ValueError("common Warehouse world transform differs from source")
    _retain_groups_and_materials(common, names)
    common.SetActive(False)
    for path in selected:
        stage.GetPrimAtPath(path).SetActive(False)
    scene._eai_gpu_warehouse_static_prepared = {
        "group_names": names,
        "audit_sha256": digest,
    }
    print(f"[WarehouseStatic][GPU] extracted={len(names)} groups before clone", flush=True)


def finalize_gpu_shared_static(scene, audit_path):
    """Activate the common Warehouse colliders in the single GPU solver."""
    from pxr import Sdf, Usd, UsdPhysics

    prepared = getattr(scene, "_eai_gpu_warehouse_static_prepared", None)
    if not prepared:
        raise ValueError("Warehouse static source was not prepared before cloning")
    names, digest = audited_group_names(audit_path)
    if tuple(names) != tuple(prepared["group_names"]) or digest != prepared["audit_sha256"]:
        raise ValueError("Warehouse static audit changed between prepare and finalize")
    common = scene.stage.GetPrimAtPath(COMMON_ROOT)
    if not common.IsValid():
        raise ValueError("common Warehouse root disappeared during cloning")
    private_group_paths = []
    for index in range(scene.num_envs):
        for name in names:
            path = f"/World/envs/env_{index}/Warehouse/{name}"
            group = scene.stage.GetPrimAtPath(path)
            if not group.IsValid() or group.IsActive():
                raise ValueError(f"GPU clone retained private Warehouse static: {path}")
            private_group_paths.append(path)
    common.SetActive(True)
    primary = scene.replay_physics_partition["physics_scene_paths"][0]
    collider_paths = []
    for prim in scene.stage.Traverse():
        if str(prim.GetPath()).startswith("/World/envs/"):
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                UsdPhysics.RigidBodyAPI(prim).GetSimulationOwnerRel().SetTargets(
                    [Sdf.Path(primary)])
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI(prim).GetSimulationOwnerRel().SetTargets(
                    [Sdf.Path(primary)])
    for prim in Usd.PrimRange(common):
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI(prim).GetSimulationOwnerRel().SetTargets(
                [Sdf.Path(primary)])
            collider_paths.append(str(prim.GetPath()))
    guard = SharedStaticWarehouse(
        scene.stage, collider_paths, names, digest,
        private_group_paths=private_group_paths,
    )
    scene.replay_static_warehouse = guard
    scene.replay_physics_partition["static_warehouse"] = guard.report()
    return guard


def validate_slot_topology(stage, count, *, common_static, allow_slot_inheritance):
    """Verify identical private physics rows plus one shared static collider set."""
    from pxr import Usd, UsdPhysics

    common_static.validate_immutable()
    common_paths = common_static.collider_paths
    physical = [[] for _ in range(count)]

    def owner(path):
        for index in range(count):
            root = f"/World/envs/env_{index}"
            if path == root or path.startswith(root + "/"):
                return index
        return None

    def canonical(path, index):
        root = f"/World/envs/env_{index}"
        if path in common_paths:
            return SOURCE_ROOT + path[len(COMMON_ROOT):]
        if path == root or path.startswith(root + "/"):
            return "/World/envs/env_0" + path[len(root):]
        raise ValueError(f"physical path is outside Warehouse slot {index}: {path}")

    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        path = str(prim.GetPath())
        for target in prim.GetInherits().GetAllDirectInherits():
            target = str(target)
            index = owner(path)
            valid = (allow_slot_inheritance and index is not None and index > 0
                     and (target == "/World/envs/env_0"
                          or target.startswith("/World/envs/env_0/")))
            if owner(target) is not None and not valid:
                raise ValueError(f"mutable Warehouse slot inheritance is forbidden: {path} -> {target}")
        if not (prim.HasAPI(UsdPhysics.RigidBodyAPI)
                or prim.HasAPI(UsdPhysics.CollisionAPI)
                or prim.IsA(UsdPhysics.Joint)):
            continue
        if path in common_paths:
            if prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.IsA(UsdPhysics.Joint):
                raise ValueError(f"shared Warehouse geometry acquired dynamics: {path}")
            for index in range(count):
                physical[index].append(canonical(path, index))
            continue
        index = owner(path)
        if index is None:
            raise ValueError(f"unscoped physical prim in Warehouse branch batch: {path}")
        physical[index].append(canonical(path, index))
        if prim.IsA(UsdPhysics.Joint):
            joint = UsdPhysics.Joint(prim)
            for relation in (joint.GetBody0Rel(), joint.GetBody1Rel()):
                if any(owner(str(target)) != index for target in relation.GetTargets()):
                    raise ValueError(f"Warehouse joint crosses slots: {path}")
    if not physical[0] or any(sorted(row) != sorted(physical[0]) for row in physical[1:]):
        raise ValueError("Warehouse slots do not have identical physical topology")
    env_id_scenes = []
    for prim in stage.Traverse():
        attr = prim.GetAttribute("physxScene:envIdInBoundsBitCount")
        if attr.IsValid() and attr.Get() is not None:
            env_id_scenes.append(str(prim.GetPath()))
    if count > 1 and not env_id_scenes:
        raise ValueError("GPU env-id collision filtering marker was not created")
    return {
        "physical_prims_per_slot": len(physical[0]),
        "slot_count": count,
        "joint_endpoints_isolated": True,
        "shared_static_colliders": len(common_paths),
        "collision_filtering": "gpu_env_ids",
        "usd_slot_composition": (
            "source_inheritance" if allow_slot_inheritance else "independent_specs"),
    }


__all__ = [
    "COMMON_ROOT",
    "SOURCE_ROOT",
    "SharedStaticWarehouse",
    "WarehouseStaticAudit",
    "audited_group_names",
    "finalize_gpu_shared_static",
    "prepare_gpu_shared_static_source",
    "validate_slot_topology",
]
