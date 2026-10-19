"""Coordinate-aligned material-preserving floor views, session edits only."""
import argparse
import gc
import hashlib
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--output-root',type=Path,required=True)
    a=p.parse_args();a.output_root.mkdir(parents=True,exist_ok=False)
    from isaacsim import SimulationApp
    app=SimulationApp({'headless':False,'fast_shutdown':False,'width':1200,'height':1400})
    stage=adapter=None
    try:
        import numpy as np
        import carb.settings
        import omni.usd
        from pxr import UsdGeom, UsdLux, UsdPhysics, Vt, Gf
        from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
        from tools.topology_authoring.adapters.usd_scene import UsdSceneAdapter
        adapter=UsdSceneAdapter('office_semantic')
        source=adapter.load_source(Path(__file__).resolve().parents[1]/'maps/office')
        before=adapter.source_digests(source)
        adapter.load_stage(source);stage=omni.usd.get_context().get_stage()
        carb.settings.get_settings().set_bool('/rtx/useViewLightingMode',True)
        UsdLux.DomeLight.Define(stage,'/World/ObservationLight').CreateIntensityAttr(350)
        cam=UsdGeom.Camera.Define(stage,'/World/SemanticTopCamera')
        cam.CreateProjectionAttr('orthographic')
        cam.CreateHorizontalApertureAttr(80.)
        cam.CreateVerticalApertureAttr(90.)
        cam.AddTranslateOp().Set(Gf.Vec3d(0,0,10))
        viewport=get_active_viewport();viewport.camera_path=cam.GetPath()
        viewport.set_texture_resolution((1200,1350))
        cache=UsdGeom.XformCache(); originals=[]
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):continue
            mesh=UsdGeom.Mesh(prim)
            if UsdPhysics.CollisionAPI(prim):
                UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Set(False)
            points=np.asarray(mesh.GetPointsAttr().Get());counts=np.asarray(mesh.GetFaceVertexCountsAttr().Get())
            indices=np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
            if not len(points) or not np.all(counts==3):raise ValueError(str(prim.GetPath()))
            xyz=(np.c_[points,np.ones(len(points))]@np.asarray(cache.GetLocalToWorldTransform(prim)))[:,:3]
            triangles=xyz[indices.reshape(-1,3)]
            primvars=[]
            for pv in UsdGeom.PrimvarsAPI(prim).GetPrimvars():
                if pv.GetInterpolation() not in ('faceVarying','uniform'):continue
                values=pv.GetIndices() if pv.IsIndexed() else pv.Get()
                if values is not None:
                    primvars.append((pv,pv.IsIndexed(),pv.GetInterpolation(),values))
            normals=mesh.GetNormalsAttr().Get()
            originals.append((mesh,triangles,indices,primvars,normals,mesh.GetNormalsInterpolation()))
        stats=[]
        for level in source.payload['levels']:
            z=level['support_z_m'];retained=0
            for mesh,tri,idx,pvs,normals,norminterp in originals:
                keep=(tri[:,:,2].min(1)>=z-.02)&(tri[:,:,2].max(1)<=z+.78)
                retained+=int(keep.sum());corner=np.repeat(keep,3)
                mesh.GetVisibilityAttr().Set('inherited' if keep.any() else 'invisible')
                mesh.GetFaceVertexCountsAttr().Set([3]*int(keep.sum()))
                mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(idx[corner].astype('int32')))
                for pv,indexed,interp,values in pvs:
                    mask=corner if interp=='faceVarying' else keep
                    selected=type(values)([values[int(i)] for i in np.flatnonzero(mask)])
                    (pv.SetIndices if indexed else pv.Set)(selected)
                if normals is not None and norminterp in ('faceVarying','uniform'):
                    mask=corner if norminterp=='faceVarying' else keep
                    mesh.GetNormalsAttr().Set(type(normals)([normals[int(i)] for i in np.flatnonzero(mask)]))
            for _ in range(40):app.update()
            output=a.output_root/f"{level['id']}-material-top.png"
            capture=capture_viewport_to_file(viewport,str(output))
            for _ in range(30):app.update()
            if not output.is_file():raise RuntimeError('capture did not write '+str(output))
            stats.append({'level':level['id'],'floor_z':z,'max_z':z+.78,'faces':retained,'image':str(output),
                          'projection':'orthographic X-right Y-up','center_xy':[0,0],'width_m':8,'height_m':9})
            print('CAPTURE',output,flush=True)
            capture=None
        unchanged=adapter.source_digests(source)==before
        (a.output_root/'capture.json').write_text(json.dumps({'source_unchanged':unchanged,'source_digests':before,
            'views':stats,'camera_light':True,'note':'session-only face subset; cross-band faces omitted; observation not occupancy'},indent=2)+'\n')
        if not unchanged:raise RuntimeError('source changed')
    finally:
        stage=None
        if adapter:adapter.close()
        adapter=None;gc.collect()
        from simulator import _prepare_gui_for_app_close
        _prepare_gui_for_app_close(app);app.close()


if __name__=='__main__':main()
