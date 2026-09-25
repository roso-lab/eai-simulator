"""Schema-checked, immutable common Factory geometry for task solver scenes."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


COMMON_FACTORY_ROOT = "/World/SharedFactory/Indoor"


def instance_carter_geometry(scene):
    """Share immutable Carter mesh subtrees, retaining private rigid roots.

    The collision API on the chassis group stays on the editable instance
    root. Descendants must contain no physics state, constraints or sensors.
    All composed attributes and relationships are checked after instancing.
    """
    from pxr import Sdf, Usd, UsdGeom

    names = {"chassis_link", "carter_v2_wheel_left", "carter_v2_wheel_right"}
    roots = [prim for prim in scene.stage.Traverse()
             if str(prim.GetPath()).startswith("/World/envs/env_0/carter_1/")
             and prim.GetName() in names and prim.IsA(UsdGeom.Xform)]
    report = {"groups": [], "geometry_preserved": True,
              "scope": "immutable mesh instances; independent rigid and collision roots"}
    for root in roots:
        descendants = list(Usd.PrimRange(root))
        allowed_types = {"Xform", "Mesh", "Scope", "Material", "Shader", "GeomSubset"}
        if any(prim.GetTypeName() not in allowed_types for prim in descendants):
            raise ValueError(f"unexpected schema in Carter mesh group: {root.GetPath()}")
        # Cooked convex geometry is immutable asset data, not a body or shape
        # owner. Carter stores it on the mesh below the private collision root.
        if any("phys" in api.lower() and not api.startswith("PhysxCookedDataAPI:")
               for prim in descendants[1:] for api in prim.GetAppliedSchemas()):
            raise ValueError(f"physical descendant in Carter mesh group: {root.GetPath()}")
        if any("RigidBody" in api or "Articulation" in api for api in root.GetAppliedSchemas()):
            raise ValueError(f"mutable Carter instance root: {root.GetPath()}")
        if any(attr.GetNumTimeSamples() for prim in descendants for attr in prim.GetAttributes()):
            raise ValueError(f"animated Carter geometry: {root.GetPath()}")
        source = next((spec for spec in root.GetPrimStack()
                       if spec.specifier == Sdf.SpecifierDef and spec.layer.realPath), None)
        if source is None:
            raise ValueError(f"Carter geometry lacks a provider definition: {root.GetPath()}")
        # Keep exact Vt arrays for comparison (one group at a time); never
        # hash truncated array reprs or assume source references are equivalent.
        before = {str(prim.GetPath()): (
            prim.GetTypeName(), prim.GetAppliedSchemas(),
            {attr.GetName(): attr.Get() for attr in prim.GetAttributes()},
            {rel.GetName(): rel.GetTargets() for rel in prim.GetRelationships()},
        ) for prim in descendants}
        root.GetReferences().AddReference(source.layer.realPath, source.path)
        root.SetInstanceable(True)
        if not root.IsInstance():
            raise ValueError(f"Carter geometry did not become an instance: {root.GetPath()}")
        for path, (type_name, schemas, attributes, relationships) in before.items():
            prim = scene.stage.GetPrimAtPath(path)
            if (not prim or prim.GetTypeName() != type_name or prim.GetAppliedSchemas() != schemas
                    or {attr.GetName() for attr in prim.GetAttributes()} != set(attributes)
                    or {rel.GetName() for rel in prim.GetRelationships()} != set(relationships)
                    or any(prim.GetAttribute(name).Get() != value for name, value in attributes.items())
                    or any(prim.GetRelationship(name).GetTargets() != value for name, value in relationships.items())):
                raise ValueError(f"Carter geometry changed while instancing: {path}")
        report["groups"].append({"path": str(root.GetPath()), "prim_count": len(before),
                                 "source": source.layer.realPath, "source_prim": str(source.path)})
    scene.replay_geometry_instances = report
    print(f"[SharedGeometry] instanceable Carter groups={len(roots)}", flush=True)
    return report


def is_common_factory_path(path):
    path = str(path)
    return path == COMMON_FACTORY_ROOT or path.startswith(COMMON_FACTORY_ROOT + "/")


def instance_spatial_factory_geometry(scene, audit_path):
    """Share read-only USD prototypes, retaining a physical copy at each origin."""
    from pxr import Usd, UsdPhysics
    names, digest = audited_group_names(audit_path)
    results = []
    for name in names:
        prim = scene.stage.GetPrimAtPath('/World/envs/env_0/Indoor/' + name)
        descendants = list(Usd.PrimRange(prim))
        if any(p.HasAPI(UsdPhysics.RigidBodyAPI) or p.HasAPI(UsdPhysics.ArticulationRootAPI)
               or p.IsA(UsdPhysics.Joint) for p in descendants):
            raise ValueError(f'geometry audit no longer matches immutable group: {name}')
        if any('graph' in p.GetTypeName().lower() or p.IsA(UsdPhysics.Scene) for p in descendants):
            continue
        if any(a.GetNumTimeSamples() for p in descendants for a in p.GetAttributes()):
            raise ValueError(f'animated geometry in static audit: {name}')
        prim.SetInstanceable(True)
        results.append(dict(group=name, instance=prim.IsInstance(), prim_count=len(descendants)))
    scene.replay_spatial_geometry = dict(audit_sha256=digest, groups=results,
        scope='USD geometry prototype sharing; each spatial slot keeps its own collider placement')
    print(f'[SpatialGeometry] instances={sum(x["instance"] for x in results)}/{len(results)}',flush=True)


def audited_group_names(path):
    """Select only first-slot groups explicitly covered by a task write audit."""
    payload = Path(path).read_bytes()
    audit = json.loads(payload)
    prefix = "/World/envs/env_0/Indoor/"
    names = []
    for candidate in audit["candidate_groups_without_schema_ownership_or_observed_writes"]:
        if not isinstance(candidate, str):
            raise ValueError("static audit paths must be strings")
        if not candidate.startswith(prefix):
            continue
        name = candidate[len(prefix):]
        entry = audit["groups"][candidate]
        if (not name or "/" in name or entry.get("slot") != 0 or not entry.get("colliders")
                or entry.get("task_owned_reasons") != [] or entry.get("observed_write_examples") != []):
            raise ValueError(f"static group lacks clean schema/write evidence: {candidate}")
        names.append(name)
    if not names or len(set(names)) != len(names):
        raise ValueError("static audit needs unique first-slot candidate groups")
    return tuple(sorted(names)), sha256(payload).hexdigest()


def mark_gpu_static_source_instanceable(scene, audit_path):
    """Mark audited source groups as read-only USD instances before cloning.

    This keeps the source topology intact and lets the cloner share the
    immutable mesh payloads without moving or deleting prim specs. Physics
    ownership is still finalized by the existing post-clone shared-static
    path, so this optimization does not alter task bodies or joints.
    """
    from pxr import Usd

    names, digest = audited_group_names(audit_path)
    source_root = scene.stage.GetPrimAtPath("/World/envs/env_0/Indoor")
    if not source_root.IsValid():
        raise ValueError("GPU static instancing requires the source Factory root before cloning")
    marked = []
    skipped = []
    for name in names:
        group = source_root.GetChild(name)
        if not group.IsValid():
            raise ValueError(f"audited static group is missing from source: {name}")
        descendants = list(Usd.PrimRange(group))
        # Kit 5.1 cannot instance USD OmniGraph/action-graph prims. Such
        # groups remain ordinary cloned specs and are handled by the existing
        # post-clone static audit; only geometry-only groups use instancing.
        if any("graph" in (prim.GetTypeName() or "").lower()
               or "actiongraph" in str(prim.GetPath()).lower()
               for prim in descendants):
            skipped.append(name)
            continue
        group.SetInstanceable(True)
        marked.append(str(group.GetPath()))
    scene._eai_gpu_static_instanceable = {
        "group_names": names,
        "audit_sha256": digest,
        "paths": tuple(marked),
        "skipped": tuple(skipped),
    }
    print(f"[SharedStatic][GPU] instanceable={len(marked)} static source groups; skipped_graphs={len(skipped)}", flush=True)
    return scene._eai_gpu_static_instanceable


def validate_equal_static_geometry(roots, names):
    """Compare composed properties before replacing any private static geometry."""
    from pxr import Usd

    def canonical_targets(relationship, root):
        prefix = str(root.GetPath())
        return tuple("/Indoor" + str(target)[len(prefix):]
                     if str(target) == prefix or str(target).startswith(prefix + "/") else str(target)
                     for target in relationship.GetTargets())

    for name in names:
        reference = roots[0].GetChild(name)
        for expected in Usd.PrimRange(reference):
            relative = str(expected.GetPath())[len(str(roots[0].GetPath())):]
            expected_attributes = {attribute.GetName(): attribute for attribute in expected.GetAttributes()}
            if any(attribute.GetNumTimeSamples() for attribute in expected_attributes.values()):
                raise ValueError(f"time-sampled geometry requires task ownership: {expected.GetPath()}")
            expected_relationships = {rel.GetName(): rel for rel in expected.GetRelationships()
                                      if rel.GetName() != "physics:simulationOwner"}
            for root in roots[1:]:
                actual = root.GetStage().GetPrimAtPath(str(root.GetPath()) + relative)
                if (not actual.IsValid() or actual.GetTypeName() != expected.GetTypeName()
                        or actual.GetAppliedSchemas() != expected.GetAppliedSchemas()):
                    raise ValueError(f"task static prim definitions differ: {relative}")
                attributes = {attribute.GetName(): attribute for attribute in actual.GetAttributes()}
                if attributes.keys() != expected_attributes.keys():
                    raise ValueError(f"task static attribute coverage differs: {relative}")
                for key, attribute in attributes.items():
                    other = expected_attributes[key]
                    if (attribute.GetNumTimeSamples() or attribute.GetTypeName() != other.GetTypeName()
                            or attribute.GetVariability() != other.GetVariability() or attribute.Get() != other.Get()):
                        raise ValueError(f"task static values differ: {relative}.{key}")
                relationships = {rel.GetName(): rel for rel in actual.GetRelationships()
                                 if rel.GetName() != "physics:simulationOwner"}
                if (relationships.keys() != expected_relationships.keys()
                        or any(canonical_targets(rel, root) != canonical_targets(expected_relationships[key], roots[0])
                               for key, rel in relationships.items())):
                    raise ValueError(f"task static relationships differ: {relative}")
        expected_paths = {str(prim.GetPath())[len(str(reference.GetPath())):] for prim in Usd.PrimRange(reference)}
        for root in roots[1:]:
            group = root.GetChild(name)
            if {str(prim.GetPath())[len(str(group.GetPath())):] for prim in Usd.PrimRange(group)} != expected_paths:
                raise ValueError(f"task static descendant coverage differs: {name}")


class SharedStaticFactory:
    """Observe common geometry writes; reject them before another physical tick."""

    def __init__(self, stage, collider_paths, group_names, audit_sha256, private_group_paths=()):
        from pxr import Tf, Usd

        self.collider_paths = frozenset(collider_paths)
        self.group_names = tuple(group_names)
        self.audit_sha256 = audit_sha256
        self.protected_roots = (COMMON_FACTORY_ROOT, *map(str, private_group_paths))
        self.write_examples = []
        self.render_visibility_roots = frozenset()
        self.notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._changed, stage)

    def allow_slot_render_visibility(self, roots):
        """Allow only visual masking of private slot ancestors, never statics."""
        prefix = "/World/envs/env_"
        roots = frozenset(map(str, roots))
        if any(not root.startswith(prefix) or not root[len(prefix):].isdigit() for root in roots):
            raise ValueError("render visibility exemptions must be exact environment roots")
        self.render_visibility_roots = roots

    def _changed(self, notice, sender):
        for changed in (*notice.GetResyncedPaths(), *notice.GetChangedInfoOnlyPaths()):
            path = str(changed.GetPrimPath())
            if (changed.IsPropertyPath() and changed.name == "visibility"
                    and path in self.render_visibility_roots):
                continue  # USD visibility does not change bodies or colliders.
            if (path == "/" or any(path == root or path.startswith(root + "/")
                                   or root.startswith(path + "/") for root in self.protected_roots)):
                if len(self.write_examples) < 8:
                    self.write_examples.append(str(changed))

    def validate_immutable(self):
        if self.write_examples:
            raise ValueError(f"shared Factory geometry was modified: {self.write_examples}")

    def contains_collider(self, path):
        return str(path) in self.collider_paths

    def report(self):
        return {"common_root": COMMON_FACTORY_ROOT, "group_names": list(self.group_names),
                "collider_paths": sorted(self.collider_paths), "audit_sha256": self.audit_sha256,
                "protected_roots": list(self.protected_roots),
                "write_examples": list(self.write_examples),
                "render_visibility_roots": sorted(self.render_visibility_roots),
                "scope": "schema and joint ownership plus live common-geometry write guard; "
                         "physical equivalence still requires workload comparisons"}

    def close(self):
        if self.notice is not None:
            self.notice.Revoke()
            self.notice = None


def retain_common_geometry_and_materials(root, names):
    """Keep referenced visual/physics materials alongside the shared shapes."""
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
            if any(attr.GetNumTimeSamples() for prim in descendants for attr in prim.GetAttributes()):
                raise ValueError(f"time-sampled common material: {group.GetPath()}")
        else:
            group.SetActive(False)


def prepare_gpu_shared_static_source(scene, audit_path):
    """Extract audited static groups before Isaac's GPU clone expands them.

    This is the memory-critical half of GPU shared-static mode. It must run
    after the source environment is authored but before ``clone_environments``.
    Audited groups are deactivated in the source namespace so the cloner does
    not expand their physics payload into every environment.
    """
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    names, digest = audited_group_names(audit_path)
    stage = scene.stage
    source_root = stage.GetPrimAtPath("/World/envs/env_0/Indoor")
    if not source_root.IsValid():
        raise ValueError("GPU shared statics require the source Factory root before cloning")
    common_root = stage.GetPrimAtPath(COMMON_FACTORY_ROOT)
    if common_root.IsValid():
        raise ValueError("common Factory geometry is already present")
    layer = stage.GetRootLayer()
    selected = []
    joint_targets = [str(target) for prim in stage.Traverse() if prim.IsA(UsdPhysics.Joint)
                     for relation in (UsdPhysics.Joint(prim).GetBody0Rel(), UsdPhysics.Joint(prim).GetBody1Rel())
                     for target in relation.GetTargets()]
    for name in names:
        group = source_root.GetChild(name)
        if not group.IsValid():
            raise ValueError(f"audited static group is missing from source: {name}")
        descendants = list(Usd.PrimRange(group))
        if any(prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.HasAPI(UsdPhysics.ArticulationRootAPI)
               or prim.IsA(UsdPhysics.Joint) for prim in descendants):
            raise ValueError(f"dynamic schema in GPU shared geometry: {group.GetPath()}")
        if not any(prim.HasAPI(UsdPhysics.CollisionAPI) for prim in descendants):
            raise ValueError(f"static group lacks collider coverage: {group.GetPath()}")
        path = str(group.GetPath())
        if any(target == path or target.startswith(path + "/") for target in joint_targets):
            raise ValueError(f"joint endpoint in GPU shared geometry: {path}")
        if any(attr.GetNumTimeSamples() for prim in descendants for attr in prim.GetAttributes()):
            raise ValueError(f"time-sampled GPU shared geometry: {path}")
        selected.append(str(group.GetPath()))
    # A Factory child may be composed solely through the Indoor reference.
    # Copying its root-layer over loses that referenced geometry. Copy the
    # owning reference arc, then retain only the audited common groups.
    UsdGeom.Xform.Define(stage, "/World/SharedFactory")
    if not Sdf.CopySpec(layer, source_root.GetPath(), layer, COMMON_FACTORY_ROOT):
        raise RuntimeError("failed to copy the GPU shared Factory reference")
    common_root = stage.GetPrimAtPath(COMMON_FACTORY_ROOT)
    transforms = UsdGeom.XformCache()
    if transforms.GetLocalToWorldTransform(source_root) != transforms.GetLocalToWorldTransform(common_root):
        raise ValueError("GPU common Factory world transform differs from source")
    validate_equal_static_geometry([source_root, common_root], names)
    retain_common_geometry_and_materials(common_root, names)
    # Keep the extracted common root inert while Isaac's cloner and PhysX
    # replication build the environment batch. It is activated and assigned
    # to the solver only in finalize_gpu_shared_static(), after cloning.
    common_root = stage.GetPrimAtPath(COMMON_FACTORY_ROOT)
    if common_root.IsValid():
        common_root.SetActive(False)
    # Keep the source prims in the stage but deactivate them before cloning.
    # Removing specs while InteractiveScene is composing the source scene can
    # make Kit close the stage without a Python exception.  Inactive prims are
    # excluded from the cloned physics view while preserving the source
    # namespace and its references for the rest of scene construction.
    for path in selected:
        prim = stage.GetPrimAtPath(path)
        if prim.IsValid():
            prim.SetActive(False)
    scene._eai_gpu_static_prepared = {"group_names": names, "audit_sha256": digest}
    print(f"[SharedStatic][GPU] extracted={len(names)} static source groups disabled before clone", flush=True)


def finalize_gpu_shared_static(scene, audit_path):
    """Register the pre-cloned common root and bind its colliders to PhysX."""
    from pxr import Sdf, Usd, UsdPhysics

    prepared = getattr(scene, "_eai_gpu_static_prepared", None)
    if not prepared:
        raise ValueError("GPU shared static source was not prepared before cloning")
    names, digest = audited_group_names(audit_path)
    if tuple(names) != tuple(prepared["group_names"]) or digest != prepared["audit_sha256"]:
        raise ValueError("GPU shared static audit changed between prepare and finalize")
    common = scene.stage.GetPrimAtPath(COMMON_FACTORY_ROOT)
    if not common.IsValid():
        raise ValueError("GPU shared static root disappeared during cloning")
    private_group_paths = []
    for index in range(scene.num_envs):
        for name in names:
            path = f"/World/envs/env_{index}/Indoor/{name}"
            group = scene.stage.GetPrimAtPath(path)
            if not group.IsValid() or group.IsActive():
                raise ValueError(f"GPU clone retained private shared-static geometry: {path}")
            private_group_paths.append(path)
    common.SetActive(True)
    primary = scene.replay_physics_partition["physics_scene_paths"][0]
    collider_paths = []
    # A GPU single solver owns every private slot body as well. The pre-clone
    # extraction can leave composed collision specs from a source layer, so
    # normalize their owner relation here before the first topology readback.
    for prim in scene.stage.Traverse():
        if not str(prim.GetPath()).startswith("/World/envs/"):
            continue
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            UsdPhysics.RigidBodyAPI(prim).GetSimulationOwnerRel().SetTargets([Sdf.Path(primary)])
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI(prim).GetSimulationOwnerRel().SetTargets([Sdf.Path(primary)])
    for prim in Usd.PrimRange(common):
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI(prim).GetSimulationOwnerRel().SetTargets([Sdf.Path(primary)])
            collider_paths.append(str(prim.GetPath()))
    scene.replay_static_factory = SharedStaticFactory(
        scene.stage, collider_paths, names, digest,
        private_group_paths=private_group_paths,
    )
    scene.replay_physics_partition["static_factory"] = scene.replay_static_factory.report()
    return scene.replay_static_factory


def register_fabric_shared_static(scene, audit_path):
    """Register the audited source geometry for an Isaac Fabric clone.

    Fabric keeps cloned environment data outside the USD namespace, so the
    ordinary post-clone namespace split cannot be used. The Fabric cloner
    already shares immutable mesh payloads; this guard records the source
    collider set and leaves Fabric's env-id filtering in charge of isolation.
    """
    from pxr import Sdf, Usd, UsdPhysics

    names, digest = audited_group_names(audit_path)
    stage = scene.stage
    source_root = stage.GetPrimAtPath("/World/envs/env_0/Indoor")
    if not source_root.IsValid():
        raise ValueError("Fabric shared statics require the source Factory root")
    primary = scene.replay_physics_partition["physics_scene_paths"][0]
    collider_paths = []
    source_group_paths = []
    for name in names:
        group = source_root.GetChild(name)
        if not group.IsValid():
            raise ValueError(f"audited static group is missing from Fabric source: {name}")
        descendants = list(Usd.PrimRange(group))
        if any(prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.HasAPI(UsdPhysics.ArticulationRootAPI)
               or prim.IsA(UsdPhysics.Joint) for prim in descendants):
            raise ValueError(f"dynamic schema in Fabric static group: {group.GetPath()}")
        for prim in descendants:
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI(prim).GetSimulationOwnerRel().SetTargets([Sdf.Path(primary)])
                source_path = str(prim.GetPath())
                collider_paths.append(COMMON_FACTORY_ROOT + source_path[len(str(source_root.GetPath())):])
        source_group_paths.append(str(group.GetPath()))
    guard = SharedStaticFactory(stage, collider_paths, names, digest,
                                private_group_paths=source_group_paths)
    guard.fabric_native = True
    scene.replay_static_factory = guard
    scene.replay_physics_partition["static_factory"] = guard.report()
    scene.replay_physics_partition["static_factory"]["mode"] = "fabric-native"
    scene._eai_fabric_shared_static = True
    return guard


def split_factory_static_geometry(scene, audit_path, *, gpu_shared=False):
    """Reuse common colliders while keeping every dynamic body and joint private.

    Call after ordinary task solver-scene partitioning, before environment reset.
    This does not change collider properties, local transforms or solver settings.
    """
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    names, digest = audited_group_names(audit_path)
    stage = scene.stage
    physics_paths = scene.replay_physics_partition["physics_scene_paths"]
    if len(physics_paths) != scene.num_envs:
        raise ValueError("shared statics require one existing solver scene per task")
    if gpu_shared and len(set(physics_paths)) != 1:
        raise ValueError("GPU shared statics require one solver scene reused by all slots")
    if gpu_shared and getattr(scene, "_eai_gpu_static_prepared", None):
        return finalize_gpu_shared_static(scene, audit_path)
    if stage.GetPrimAtPath(COMMON_FACTORY_ROOT).IsValid() or getattr(scene, "replay_static_factory", None):
        raise ValueError("common Factory geometry is already present")
    roots = [stage.GetPrimAtPath(f"/World/envs/env_{index}/Indoor") for index in range(scene.num_envs)]
    # Restore ordinary USD prims before any topology/signature traversal. The
    # source groups were marked instanceable only for the clone operation;
    # traversing their replicated prototypes while editing namespace specs is
    # unsupported by Kit 5.1.
    for root in roots:
        for name in names:
            group = root.GetChild(name)
            if group.IsValid() and group.IsInstance():
                group.SetInstanceable(False)
    transforms = UsdGeom.XformCache()
    world = stage.GetPrimAtPath("/World")
    world_transform = transforms.GetLocalToWorldTransform(world)
    for root in roots:
        if not root.IsValid():
            raise ValueError("each task must have its complete Factory before splitting")
        if transforms.GetLocalToWorldTransform(root.GetParent()) != world_transform:
            raise ValueError("Factory task ancestors change the common geometry world transform")
        if any(attribute.GetNumTimeSamples() for attribute in root.GetAttributes()):
            raise ValueError("time-sampled Factory root cannot be shared")
    joint_targets = [str(target) for prim in stage.Traverse() if prim.IsA(UsdPhysics.Joint)
                     for relation in (UsdPhysics.Joint(prim).GetBody0Rel(), UsdPhysics.Joint(prim).GetBody1Rel())
                     for target in relation.GetTargets()]
    selected = []
    signatures = []
    for root in roots:
        if not root.IsValid():
            raise ValueError("each task must have its complete Factory before splitting")
        ancestor = root
        while ancestor.IsValid():
            if any(ancestor.HasAPI(schema) for schema in
                   (UsdPhysics.RigidBodyAPI, UsdPhysics.ArticulationRootAPI, UsdPhysics.CollisionAPI)):
                raise ValueError("Factory ancestor has physical ownership that cannot be split")
            ancestor = ancestor.GetParent()
        groups = {prim.GetName(): prim for prim in root.GetChildren()}
        if not set(names) <= groups.keys():
            raise ValueError("audited static groups are missing from an instantiated task")
        signature = []
        for name in names:
            group = groups[name]
            path = str(group.GetPath())
            descendants = list(Usd.PrimRange(group))
            if any(any(prim.HasAPI(schema) for schema in
                       (UsdPhysics.RigidBodyAPI, UsdPhysics.ArticulationRootAPI))
                   or prim.IsA(UsdPhysics.Joint) for prim in descendants):
                raise ValueError(f"dynamic schema in proposed shared geometry: {path}")
            if any(target == path or target.startswith(path + "/") for target in joint_targets):
                raise ValueError(f"joint endpoint in proposed shared geometry: {path}")
            colliders = [prim for prim in descendants if prim.HasAPI(UsdPhysics.CollisionAPI)]
            if not colliders or any(UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False
                                    for prim in colliders):
                raise ValueError(f"static group lacks active collider coverage: {path}")
            signature.extend(str(prim.GetPath())[len(str(root.GetPath())):] for prim in colliders)
            selected.append(group)
        signatures.append(sorted(signature))
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise ValueError("task Factory static collider topology differs")
    validate_equal_static_geometry(roots, names)
    # Instanceability is a clone-time memory optimization. Before the
    # namespace edits below, restore ordinary prim specs so Sdf.CopySpec and
    # the GPU static extraction cannot operate on an instance prototype.
    for group in selected:
        if group.IsInstance():
            group.SetInstanceable(False)
    if any(transforms.GetLocalToWorldTransform(root) != transforms.GetLocalToWorldTransform(roots[0])
           for root in roots[1:]):
        raise ValueError("Factory root transforms differ across tasks")
    groups = []
    group_path = "/World/collisions/shared_factory"
    if not gpu_shared:
        # Validate the inverted collision groups before making stage edits.
        groups = [UsdPhysics.CollisionGroup(stage.GetPrimAtPath(f"/World/collisions/group{index}"))
                  for index in range(scene.num_envs)]
        if any(not group for group in groups):
            raise ValueError("shared statics require the existing isolated CPU collision groups")
        from pxr import PhysxSchema

        if any(PhysxSchema.PhysxSceneAPI(stage.GetPrimAtPath(path)).GetInvertCollisionGroupFilterAttr().Get() is not True
               for path in physics_paths):
            raise ValueError("shared statics require inverted CPU collision group filtering")
        if stage.GetPrimAtPath(group_path).IsValid():
            raise ValueError("common Factory collision group already exists")
    UsdGeom.Xform.Define(stage, "/World/SharedFactory")
    layer = stage.GetRootLayer()
    if not Sdf.CopySpec(layer, roots[0].GetPath(), layer, COMMON_FACTORY_ROOT):
        raise RuntimeError("failed to copy the Factory root for common geometry")
    common = stage.GetPrimAtPath(COMMON_FACTORY_ROOT)
    retain_common_geometry_and_materials(common, names)
    # Removing a root-layer over reveals the geometry again through the
    # Factory reference. An explicit inactive opinion excludes the subtree
    # from composition and PhysX on both devices.
    for group in selected:
        group.SetActive(False)
    collider_paths = []
    for prim in Usd.PrimRange(common):
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            targets = [physics_paths[0]] if gpu_shared else physics_paths
            UsdPhysics.CollisionAPI(prim).GetSimulationOwnerRel().SetTargets(targets)
            collider_paths.append(str(prim.GetPath()))
    if not gpu_shared:
        global_group = UsdPhysics.CollisionGroup.Define(stage, group_path)
        collection = Usd.CollectionAPI.Apply(global_group.GetPrim(), "colliders")
        collection.CreateExpansionRuleAttr().Set("expandPrims")
        collection.CreateIncludesRel().SetTargets([COMMON_FACTORY_ROOT])
        global_group.GetFilteredGroupsRel().SetTargets([group_path, *[str(group.GetPath()) for group in groups]])
        for group in groups:
            group.GetFilteredGroupsRel().AddTarget(group_path)
    guard = SharedStaticFactory(stage, collider_paths, names, digest,
                                [group.GetPath() for group in selected])
    scene.replay_static_factory = guard
    scene.replay_physics_partition["static_factory"] = guard.report()
    return guard
