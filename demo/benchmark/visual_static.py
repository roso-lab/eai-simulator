"""Shared visual-composition checks for benchmark scene cloning."""

from __future__ import annotations


def validate_visual_composition(original, instance):
    """Compare composed geometry, visibility, transforms, and materials."""
    from pxr import Usd, UsdGeom, UsdShade

    root = str(original.GetPath())
    target = str(instance.GetPath())
    count = 0
    for prim in Usd.PrimRange(original, Usd.TraverseInstanceProxies()):
        if not (prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.PointInstancer)):
            continue
        suffix = str(prim.GetPath())[len(root):]
        other = instance.GetStage().GetPrimAtPath(target + suffix)
        if not other or prim.GetTypeName() != other.GetTypeName():
            raise ValueError(f"visual prototype topology mismatch: {prim.GetPath()}")
        for name in (
            "points",
            "faceVertexCounts",
            "faceVertexIndices",
            "normals",
            "primvars:st",
            "primvars:displayColor",
            "primvars:displayOpacity",
            "positions",
            "orientations",
            "scales",
            "protoIndices",
            "invisibleIds",
        ):
            if prim.GetAttribute(name).Get() != other.GetAttribute(name).Get():
                raise ValueError(f"visual prototype changed {name}: {prim.GetPath()}")
        for schema, method in (
            (UsdGeom.Imageable, "ComputeVisibility"),
            (UsdGeom.Xformable, "ComputeLocalToWorldTransform"),
        ):
            if getattr(schema(prim), method)(0) != getattr(schema(other), method)(0):
                raise ValueError(f"visual prototype changed {method}: {prim.GetPath()}")
        materials = [
            str(UsdShade.MaterialBindingAPI(candidate).ComputeBoundMaterial()[0].GetPath())
            .replace("/RenderInstance", "")
            for candidate in (prim, other)
        ]
        if materials[0] != materials[1]:
            raise ValueError(
                f"visual prototype changed material binding: {prim.GetPath()}: {materials}"
            )
        if prim.IsA(UsdGeom.PointInstancer):
            prototypes = [
                [
                    str(path).replace("/RenderInstance", "")
                    for path in UsdGeom.PointInstancer(candidate)
                    .GetPrototypesRel()
                    .GetTargets()
                ]
                for candidate in (prim, other)
            ]
            if prototypes[0] != prototypes[1]:
                raise ValueError(
                    f"visual prototype changed point instances: {prim.GetPath()}"
                )
        count += 1
    return count


def retain_static_physics(source_root, native_colliders):
    """Prune visual-only subtrees while retaining collision and material state."""
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    root_path = source_root.GetPath()
    stage = source_root.GetStage()
    retain = {root_path}
    subtrees = {prim.GetPath() for prim in native_colliders}
    materials_before = {}
    for collider in native_colliders:
        path = collider.GetPath()
        materials_before[path] = str(
            UsdShade.MaterialBindingAPI(collider)
            .ComputeBoundMaterial(materialPurpose="physics")[0]
            .GetPath()
        )
        current = collider
        binding_prims = list(Usd.PrimRange(collider))
        while current and current.GetPath().HasPrefix(root_path):
            retain.add(current.GetPath())
            binding_prims.append(current)
            current = current.GetParent()
        for prim in binding_prims:
            for relationship in prim.GetRelationships():
                if not relationship.GetName().startswith("material:binding"):
                    continue
                for target in relationship.GetTargets():
                    target = target.GetPrimPath()
                    if target.HasPrefix(root_path):
                        subtrees.add(target)
    for path in subtrees:
        current = path
        while current.HasPrefix(root_path):
            retain.add(current)
            current = current.GetParentPath()

    removed = []

    def prune(prim):
        if prim.GetPath() in subtrees:
            return
        for child in list(prim.GetChildren()):
            if child.GetPath() in retain:
                prune(child)
            else:
                removed.append(str(child.GetPath()))
                child.SetActive(False)

    prune(source_root)
    remaining = [
        prim
        for prim in Usd.PrimRange(source_root, Usd.TraverseInstanceProxies())
        if prim.HasAPI(UsdPhysics.CollisionAPI)
    ]
    if {prim.GetPath() for prim in remaining} != {
        prim.GetPath() for prim in native_colliders
    }:
        raise ValueError("static visual pruning changed native collision coverage")
    for path, material in materials_before.items():
        retained_material = str(
            UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path))
            .ComputeBoundMaterial(materialPurpose="physics")[0]
            .GetPath()
        )
        if retained_material != material:
            raise ValueError(f"static visual pruning changed physics material: {path}")
    UsdGeom.Imageable(source_root).CreateVisibilityAttr(UsdGeom.Tokens.invisible)
    return {
        "deactivated_visual_only_subtrees": len(removed),
        "retained_native_colliders": len(remaining),
    }
