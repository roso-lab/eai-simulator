"""Collision-surface queries; these are geometry tests, not RL certification."""
import json
import math

import numpy as np
import pytest

from tools.topology_authoring.surfaces import SupportSurface
from tools.topology_authoring.adapters.usd_scene import UsdSceneAdapter


def stacked_surface():
    points=[]; triangles=[]
    for z in (0.,3.,6.):
        offset=len(points)
        points.extend([[-2,-2,z],[2,-2,z],[2,2,z],[-2,2,z]])
        triangles.extend([[offset,offset+1,offset+2],[offset,offset+2,offset+3]])
    return SupportSurface(points,triangles)


def test_same_xy_queries_choose_surface_below_root_not_the_roof():
    surface=stacked_surface()
    for z in (0,3,6):
        assert surface.below((0,0,z+.45)) == z
        assert surface.nearest((0,0,z+.02)) == (0,0,z)
    with pytest.raises(ValueError,match="no collision support"):
        surface.below((0,0,2.5),max_drop_m=1)
    with pytest.raises(ValueError):
        surface.nearest((0,0,1.5))


def test_spatial_cache_keeps_boundary_and_sloped_triangle_hits():
    surface=SupportSurface([[0,0,0],[2,0,1],[2,2,1],[0,2,0]],[[0,1,2],[0,2,3]])
    for x in (0,.499999,.5,.500001,1,1.5,2):
        assert surface.below((x,.5,1.5)) == pytest.approx(x*.5)
    with pytest.raises(ValueError):
        surface.below((2.01,2.01,2))


@pytest.mark.parametrize("position", [(math.nan,0,0),(0,math.inf,0),(0,0,math.nan)])
def test_nonfinite_pose_cannot_be_a_support_observation(position):
    with pytest.raises(ValueError):
        stacked_surface().below(position)
    with pytest.raises(ValueError):
        stacked_surface().nearest(position)


@pytest.mark.parametrize("tolerance", [math.nan, math.inf, -1, 0])
def test_invalid_support_tolerance_fails_closed(tolerance):
    with pytest.raises(ValueError):
        stacked_surface().nearest((0,0,0),max_distance_m=tolerance)
    with pytest.raises(ValueError):
        stacked_surface().below((0,0,.5),max_drop_m=tolerance)


def test_steep_wall_is_not_support_but_remains_in_sensor_geometry():
    surface=SupportSurface([[0,0,0],[.1,0,4],[.1,1,4]],[[0,1,2]])
    assert surface.intersections(.05,.1) == []
    assert len(surface.triangles) == 1


def test_usd_adapter_uses_real_colliders_transforms_and_pins_sources(tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics, Gf
    stage=Usd.Stage.CreateNew(str(tmp_path/"scene.usda"))
    UsdGeom.SetStageMetersPerUnit(stage,1.); UsdGeom.SetStageUpAxis(stage,"Z")
    root=UsdGeom.Xform.Define(stage,"/World"); stage.SetDefaultPrim(root.GetPrim())
    root.AddTranslateOp().Set(Gf.Vec3d(0,0,3))
    mesh=UsdGeom.Mesh.Define(stage,"/World/Floor")
    mesh.CreatePointsAttr().Set([(-2,-2,0),(2,-2,0),(2,2,0),(-2,2,0)])
    mesh.CreateFaceVertexCountsAttr().Set([3,3]); mesh.CreateFaceVertexIndicesAttr().Set([0,1,2,0,2,3])
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    stage.GetRootLayer().Save()
    descriptor={"schema":"eai.topology.usd_scene","version":1,"scene_key":"test","usd_path":"scene.usda",
        "levels":[{"id":"2F","label":"Second","support_z_m":3}],
        "semantic_targets":[{"ref":"place:hall","label":"Hall","position_xyz":[0,0,3]}]}
    (tmp_path/"scene.json").write_text(json.dumps(descriptor))
    adapter=UsdSceneAdapter("test"); source=adapter.load_source(tmp_path)
    adapter.set_active_level("2F",source)
    assert adapter.project_to_support_surface((0,0,9),source)==(0,0,3)
    assert adapter.validate_polyline([(0,0,3),(1,1,3)],"ground_small",source)==[]
    assert adapter.validate_polyline([(0,0,3),(3,3,3)],"ground_small",source)
    digests=adapter.source_digests(source)
    assert len(digests)==2 and all(len(v)==64 for v in digests.values())
    # Proxy is a sensor view with the world transform baked once, not another collider.
    target=Usd.Stage.CreateInMemory(); UsdGeom.SetStageMetersPerUnit(target,1); UsdGeom.SetStageUpAxis(target,"Z")
    proxy=adapter._geometry(source).create_raycast_proxy(target,"/Terrain")
    assert proxy.GetPointsAttr().Get()[0][2]==3
    assert not proxy.GetPrim().HasAPI(UsdPhysics.CollisionAPI)
    # A source reload must not hash new bytes while projecting onto old geometry.
    root.GetOrderedXformOps()[0].Set(Gf.Vec3d(0,0,3.19))
    stage.GetRootLayer().Save()
    reloaded=adapter.load_source(tmp_path)
    assert adapter.source_digests(reloaded) != digests
    assert adapter.project_to_support_surface((0,0,3),reloaded)[2] == pytest.approx(3.19)


def test_explicit_support_mesh_inventory_cannot_silently_drop_a_collider():
    from pxr import Usd, UsdGeom
    stage=Usd.Stage.CreateInMemory(); UsdGeom.SetStageMetersPerUnit(stage,1); UsdGeom.SetStageUpAxis(stage,"Z")
    with pytest.raises(ValueError,match="missing/disabled"):
        SupportSurface.from_stage(stage,mesh_paths=["/Missing"])


@pytest.mark.parametrize("field,value", [
    ("version",True), ("levels",[]), ("support_mesh_paths",["/World/../Floor"]),
    ("level_view_hide_paths",{"Unknown":["/World/Roof"]}),
    ("level_view_cut_z_m",{"1F":0}), ("camera",{"eye":[0,True,3]}),
])
def test_descriptor_rejects_invalid_scene_and_view_configuration(tmp_path,field,value):
    (tmp_path/"scene.usda").write_text("#usda 1.0\n")
    data={"schema":"eai.topology.usd_scene","version":1,"usd_path":"scene.usda",
          "levels":[{"id":"1F","label":"Floor 1","support_z_m":0}]}
    data[field]=value
    (tmp_path/"scene.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        UsdSceneAdapter().load_source(tmp_path)


@pytest.mark.parametrize("moving", ["rigid_body", "transform", "points"])
def test_static_support_reader_rejects_moving_collision_sources(moving):
    from pxr import Usd, UsdGeom, UsdPhysics
    stage=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage,1); UsdGeom.SetStageUpAxis(stage,"Z")
    root=UsdGeom.Xform.Define(stage,"/World")
    mesh=UsdGeom.Mesh.Define(stage,"/World/Floor")
    mesh.CreatePointsAttr().Set([(-1,-1,0),(1,-1,0),(0,1,0)])
    mesh.CreateFaceVertexCountsAttr().Set([3]);mesh.CreateFaceVertexIndicesAttr().Set([0,1,2])
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    if moving == "rigid_body":
        UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    elif moving == "transform":
        op=root.AddTranslateOp();op.Set((0,0,0),0);op.Set((0,0,3),1)
    else:
        mesh.GetPointsAttr().Set([(-1,-1,0),(1,-1,0),(0,1,0)],0)
        mesh.GetPointsAttr().Set([(-1,-1,3),(1,-1,3),(0,1,3)],1)
    with pytest.raises(ValueError,match="static|animated"):
        SupportSurface.from_stage(stage)


@pytest.mark.parametrize("stop_fails", [False,True])
def test_post_step_diagnostic_failure_attempts_zero_and_retains_failure(tmp_path,stop_fails):
    from types import SimpleNamespace
    from tools.topology_authoring.deploy import diagnostic_failure_guard
    commands=[]
    def step(command):
        commands.append(command)
        if stop_fails:
            raise RuntimeError("controller offline")
    report={"physical_pass":True}
    with diagnostic_failure_guard(SimpleNamespace(step=step),tmp_path,report,zero_action=[[0,0,0]]):
        raise ValueError("support missing after motion")
    saved=json.loads((tmp_path/"result.json").read_text())
    assert commands==[{"go2_1":[[0,0,0]]}]
    assert not saved["physical_pass"]
    assert saved["navigation_state"]["status"]=="failed"
    assert saved["zero_command_applied"] is (not stop_fails)
    assert ("stop_error" in saved) is stop_fails


def test_deployment_hook_defers_usd_loading_until_after_application_start():
    from tools.topology_authoring.deploy import scene_hook
    class Adapter:
        def _geometry(self, source):
            raise AssertionError("USD must not load while preparing AppLauncher arguments")
    assert callable(scene_hook(Adapter(),object(),mixed_stairs=True))


def test_fallen_posture_terminates_existing_executor_with_zero_command():
    from tools.topology_authoring.deploy import upright_cosine
    from task.tests.test_ground_navigation_executor import _Env
    from algorithm.skills.tests.test_ground_navigation import _request, _topology
    from task.executors.ground_navigation import GroundNavigationExecutor
    env = _Env()
    def validate(sequence,motion):
        if motion and upright_cosine(env.scene["carter_1"].data.root_quat_w[0]) < .5:
            raise ValueError("robot_fallen")
    executor=GroundNavigationExecutor(env,topology=_topology(),world_state_validator=validate,
        action_factory=lambda _env,_robot,velocity:[list(velocity)])
    executor.dispatch(_request())
    executor.control(_request())
    env.scene["carter_1"].data.root_quat_w=[[0,2,0,0]]
    failed=executor.control(_request("continue"))
    assert failed.state["status"] == "failed"
    assert failed.private_evidence["zero_command_applied"]
    assert env.commands[-1][1] == [0,0,0]
    with pytest.raises(ValueError):
        upright_cosine([0,0,0,0])


@pytest.mark.parametrize("capture_fails", [False, True])
def test_screenshot_does_not_step_physics_and_restores_player_setting(monkeypatch,capture_fails):
    import sys
    from types import ModuleType,SimpleNamespace
    from tools.topology_authoring.deploy import capture_frame
    setting={"/app/player/playSimulations":True}
    settings=SimpleNamespace(get=lambda name:setting[name],set_bool=lambda name,v:setting.update({name:v}))
    carb=ModuleType("carb");carb.settings=SimpleNamespace(get_settings=lambda:settings)
    omni=ModuleType("omni");omni.kit=ModuleType("omni.kit")
    def capture(path):
        if capture_fails:
            raise RuntimeError("capture failed")
    omni.kit.renderer_capture=SimpleNamespace(acquire_renderer_capture_interface=lambda:
        SimpleNamespace(capture_next_frame_swapchain=capture))
    for name,module in (("carb",carb),("carb.settings",carb.settings),("omni",omni),
                        ("omni.kit",omni.kit),("omni.kit.renderer_capture",omni.kit.renderer_capture)):
        monkeypatch.setitem(sys.modules,name,module)
    ticks=[]
    def update():
        assert setting["/app/player/playSimulations"] is False
        ticks.append(1)
    app=SimpleNamespace(update=update)
    if capture_fails:
        with pytest.raises(RuntimeError,match="capture failed"):
            capture_frame(app,"test.png")
    else:
        capture_frame(app,"test.png")
        assert len(ticks)==12
    assert setting["/app/player/playSimulations"] is True
