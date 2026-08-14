"""USD preview stage management for the Env DIY authoring extension."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
import uuid

from EAI_hmrs import env_builder

from .asset_validation import validate_usd_asset
from .placement import payload_drop_robot_id


PREVIEW_ROOT = "/World/EnvDiyPreview"
ROBOT_ROOT = f"{PREVIEW_ROOT}/Robots"
PENDING_ROOT = f"{ROBOT_ROOT}/_Pending"


class PreviewStage:
    def __init__(self) -> None:
        self.robot_prim_paths: dict[str, str] = {}
        self.robot_signatures: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.robot_diagnostics: dict[str, list[str]] = {}
        self._scene_key: str | None = None

    def initialize(self, scene_key: str = "plane") -> None:
        import omni.timeline
        import omni.usd
        from pxr import Gf, UsdGeom, UsdPhysics

        omni.timeline.get_timeline_interface().stop()
        context = omni.usd.get_context()
        context.new_stage()
        stage = context.get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        stage.DefinePrim(PREVIEW_ROOT, "Xform")
        stage.DefinePrim(ROBOT_ROOT, "Xform")
        stage.DefinePrim(PENDING_ROOT, "Xform")
        physics_scene = UsdPhysics.Scene.Define(stage, "/World/physicsScene")
        physics_scene.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
        physics_scene.CreateGravityMagnitudeAttr(9.81)
        self.load_scene(scene_key)

    def load_scene(self, scene_key: str) -> None:
        import isaaclab.sim as sim_utils
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        for path in ("/World/ground", "/World/DomeLight"):
            if stage.GetPrimAtPath(path).IsValid():
                stage.RemovePrim(path)
        previous = next(
            (option for option in env_builder.SCENE_OPTIONS if option.key == self._scene_key),
            None,
        )
        if previous is not None and stage.GetPrimAtPath(previous.prim_path).IsValid():
            stage.RemovePrim(previous.prim_path)

        scene = next(
            option for option in env_builder.SCENE_OPTIONS if option.key == scene_key
        )
        scene_cfg = env_builder._resolve_import_ref(scene.spawn_cfg)
        if scene_cfg is None:
            ground = sim_utils.GroundPlaneCfg()
            with self._use_stage(stage):
                ground.func("/World/ground", ground)
        else:
            with self._use_stage(stage):
                scene_cfg.func(
                    scene.prim_path,
                    scene_cfg,
                    translation=scene.spawn_pos,
                    orientation=(1.0, 0.0, 0.0, 0.0),
                )
            self._deactivate_preview_action_graphs(stage, scene.prim_path)
        light = sim_utils.DomeLightCfg(intensity=2000.0, color=(0.75, 0.75, 0.75))
        with self._use_stage(stage):
            light.func("/World/DomeLight", light)
        self._scene_key = scene_key

    @staticmethod
    def _deactivate_preview_action_graphs(stage, scene_root: str) -> None:
        """Disable scene automation that is irrelevant during run-before editing."""
        root = str(scene_root).rstrip("/")
        root_prefix = root + "/"
        graphs = []
        for prim in stage.Traverse():
            path = str(prim.GetPath())
            if path != root and not path.startswith(root_prefix):
                continue
            name = str(prim.GetName()).lower()
            type_name = str(prim.GetTypeName()).lower()
            if name.startswith("actiongraph") or type_name == "omnigraph":
                graphs.append(prim)
        for prim in graphs:
            prim.SetActive(False)

    @staticmethod
    @contextmanager
    def _use_stage(stage):
        """Bind the preview USD stage for IsaacLab spawners.

        IsaacLab 2.x resolves a stage through a thread-local context while
        newer Isaac Sim releases may leave the legacy global context empty
        during extension startup.  Explicitly binding the stage keeps scene,
        robot, and payload spawns in the same preview stage.
        """
        import isaaclab.sim as sim_utils

        use_stage = getattr(sim_utils, "use_stage", None)
        if callable(use_stage):
            with use_stage(stage):
                yield
            return
        yield

    def default_robot_position(self, robot_type: str, index: int) -> tuple[float, float, float]:
        robot = next(item for item in env_builder.ROBOT_OPTIONS if item.key == robot_type)
        scene = next(item for item in env_builder.SCENE_OPTIONS if item.key == self._scene_key)
        x, y = env_builder._spawn_xy(index + 1, origin=scene.robot_spawn_origin)
        return float(x), float(y), float(robot.default_z)

    def rebuild_robots(self, model) -> list[str]:
        """Synchronize preview assemblies without rebuilding unrelated robots.

        A robot whose type/attachments changed is first built below ``_Pending``.
        The existing assembly is touched only after the candidate has completed.
        """
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        if not stage.GetPrimAtPath(ROBOT_ROOT).IsValid():
            stage.DefinePrim(ROBOT_ROOT, "Xform")
        pending_root = PENDING_ROOT
        if not stage.GetPrimAtPath(pending_root).IsValid():
            stage.DefinePrim(pending_root, "Xform")

        desired_ids = {str(robot.id) for robot in model.robots}
        for robot_id in tuple(self.robot_prim_paths):
            if robot_id in desired_ids:
                continue
            path = self.robot_prim_paths.pop(robot_id)
            if stage.GetPrimAtPath(path).IsValid():
                stage.RemovePrim(path)
            self.robot_signatures.pop(robot_id, None)
            self.robot_diagnostics.pop(robot_id, None)

        for robot in model.robots:
            robot_id = str(robot.id)
            signature = self._robot_signature(robot)
            current_path = self.robot_prim_paths.get(robot_id)
            if (
                current_path
                and self.robot_signatures.get(robot_id) == signature
                and stage.GetPrimAtPath(current_path).IsValid()
            ):
                # Pose edits do not require a destructive assembly rebuild.
                try:
                    self.set_pose(robot_id, robot.position, robot.rotation)
                except Exception as exc:
                    self.robot_diagnostics[robot_id] = [str(exc)]
                continue
            try:
                self._rebuild_robot_transaction(robot, signature, stage)
            except Exception as exc:
                message = f"{robot_id} ({robot.type}): {exc}"
                self.robot_diagnostics[robot_id] = [message]
        return [
            message
            for robot_id in (str(robot.id) for robot in model.robots)
            for message in self.robot_diagnostics.get(robot_id, ())
        ]

    @staticmethod
    def _robot_signature(robot) -> tuple[str, tuple[str, ...]]:
        return (
            str(robot.type),
            tuple(str(item.type) for item in getattr(robot, "attachments", ())),
        )

    def _rebuild_robot_transaction(self, robot, signature, stage) -> None:
        robot_id = str(robot.id)
        final_path = f"{ROBOT_ROOT}/{robot_id}"
        pending_path = f"{PENDING_ROOT}/{robot_id}_{uuid.uuid4().hex}"
        old_path = self.robot_prim_paths.get(robot_id)
        backup_path = f"{PENDING_ROOT}/Backup_{robot_id}_{uuid.uuid4().hex}"
        candidate_moved = False
        try:
            self._spawn_robot(robot, assembly_path=pending_path)
            self._validate_candidate(stage, pending_path, robot)
            if old_path and stage.GetPrimAtPath(old_path).IsValid():
                self._move_prim(stage, old_path, backup_path)
            self._move_prim(stage, pending_path, final_path)
            candidate_moved = True
            self._rewrite_namespace_targets(stage, pending_path, final_path)
            # Namespace moves can rewrite relationship targets.  Validate the
            # committed path as well so a fixed joint never points back to the
            # temporary candidate namespace.
            self._validate_candidate(stage, final_path, robot)
        except Exception:
            if stage.GetPrimAtPath(pending_path).IsValid():
                stage.RemovePrim(pending_path)
            if stage.GetPrimAtPath(backup_path).IsValid():
                if stage.GetPrimAtPath(final_path).IsValid():
                    stage.RemovePrim(final_path)
                self._move_prim(stage, backup_path, final_path)
                self._rewrite_namespace_targets(stage, backup_path, final_path)
            elif candidate_moved and stage.GetPrimAtPath(final_path).IsValid():
                # A new robot has no backup to restore.  Remove a committed
                # candidate that failed after the namespace move so a retry
                # cannot collide with an orphan assembly at the final path.
                stage.RemovePrim(final_path)
            raise

        # The candidate is now the only committed assembly for this ID.
        if stage.GetPrimAtPath(backup_path).IsValid():
            stage.RemovePrim(backup_path)
        self.robot_prim_paths[robot_id] = final_path
        self.robot_signatures[robot_id] = signature
        self.robot_diagnostics.pop(robot_id, None)

    @staticmethod
    def _rewrite_namespace_targets(stage, source_root: str, destination_root: str) -> None:
        """Repair absolute relationship targets after a USD namespace move.

        Isaac Sim's MovePrim command rewrites most authored paths, but this is
        version-dependent for relationships created by Python after a USD
        payload is composed.  Candidate mount joints are authored in Python,
        so remapping targets explicitly makes the transaction independent of
        that command implementation.
        """
        try:
            from pxr import Sdf, Usd
        except ModuleNotFoundError:
            # Pure transaction tests use a lightweight fake stage without
            # Isaac Sim's pxr module; there are no USD relationships to fix.
            return

        root = stage.GetPrimAtPath(destination_root)
        if not root.IsValid():
            return
        source_prefix = source_root.rstrip("/") + "/"
        destination_prefix = destination_root.rstrip("/") + "/"
        for prim in Usd.PrimRange(root):
            for relationship in prim.GetRelationships():
                targets = relationship.GetTargets()
                rewritten = []
                changed = False
                for target in targets:
                    target_text = str(target)
                    if target_text == source_root:
                        rewritten_target = destination_root
                    elif target_text.startswith(source_prefix):
                        rewritten_target = destination_prefix + target_text[len(source_prefix):]
                    else:
                        rewritten_target = target_text
                    changed = changed or rewritten_target != target_text
                    rewritten.append(Sdf.Path(rewritten_target))
                if changed:
                    relationship.SetTargets(rewritten)

    @staticmethod
    def _validate_candidate(stage, assembly_path: str, robot) -> None:
        if not stage.GetPrimAtPath(assembly_path).IsValid():
            raise RuntimeError(f"candidate assembly was not created: {assembly_path}")
        host_path = f"{assembly_path}/Host"
        if not stage.GetPrimAtPath(host_path).IsValid():
            raise RuntimeError(f"host prim was not created: {host_path}")

        # Mounted arms are validated as one assembly.  This catches a partial
        # spawn before the candidate replaces the previous robot instance.
        manipulator = next(
            (str(item.type) for item in getattr(robot, "attachments", ()) if str(item.type) in {"ur5", "z1"}),
            None,
        )
        if manipulator is None:
            return
        options = getattr(env_builder, "ROBOT_OPTIONS", ())
        option = next((item for item in options if item.key == str(robot.type)), None)
        profile = getattr(option, f"{manipulator}_mount_profile", None) if option is not None else None
        if profile is None:
            # Isaac catalog tests may use synthetic robot types without mount
            # metadata; the real catalog always supplies a profile here.
            if option is None:
                return
            raise RuntimeError(f"{robot.type}: {manipulator} mount profile is unavailable")

        from EAI_assets.robots.manipulator_mount import find_articulation_root, find_first_valid_child

        mount_body_path = f"{host_path}/{profile.mount_body_path}"
        if not stage.GetPrimAtPath(mount_body_path).IsValid():
            raise RuntimeError(f"mount body does not exist: {mount_body_path}")
        host_root = find_articulation_root(stage, mount_body_path)
        if host_root is None or not host_root.startswith(assembly_path + "/"):
            raise RuntimeError(f"host articulation root is missing from candidate: {mount_body_path}")

        arm_path = f"{host_path}_arm"
        arm_names = ("base_link", "base", "world", "root_link") if manipulator == "ur5" else ("link00", "base_link", "base")
        arm_base_path = find_first_valid_child(stage, arm_path, arm_names)
        if arm_base_path is None or not arm_base_path.startswith(assembly_path + "/"):
            raise RuntimeError(f"arm base does not exist in candidate: {arm_path}")
        arm_base = stage.GetPrimAtPath(arm_base_path)
        if hasattr(arm_base, "HasAPI"):
            from pxr import UsdPhysics

            if not arm_base.HasAPI(UsdPhysics.ArticulationRootAPI):
                raise RuntimeError(f"arm base is not an articulation root: {arm_base_path}")

        joint_path = f"{host_root}/arm_fixed_joint_{host_path.rsplit('/', 1)[-1]}"
        joint = stage.GetPrimAtPath(joint_path)
        if not joint.IsValid():
            raise RuntimeError(f"fixed joint does not exist in candidate: {joint_path}")
        body0_targets = joint.GetRelationship("physics:body0").GetTargets()
        body1_targets = joint.GetRelationship("physics:body1").GetTargets()
        body0 = str(body0_targets[0]) if body0_targets else ""
        body1 = str(body1_targets[0]) if body1_targets else ""
        if body0 != mount_body_path:
            raise RuntimeError(f"fixed joint body0 mismatch: expected {mount_body_path}, got {body0}")
        if body1 != arm_base_path:
            raise RuntimeError(f"fixed joint body1 mismatch: expected {arm_base_path}, got {body1}")

    @staticmethod
    def _move_prim(stage, source: str, destination: str) -> None:
        try:
            import omni.kit.commands

            result = omni.kit.commands.execute(
                "MovePrim",
                path_from=source,
                path_to=destination,
            )
            if result is not False:
                return
        except Exception:
            pass
        mover = getattr(stage, "MovePrim", None)
        if callable(mover):
            result = mover(source, destination)
            if result is False:
                raise RuntimeError(f"failed to commit candidate assembly: {source} -> {destination}")
            return
        edit_target = stage.GetEditTarget()
        layer = edit_target.GetLayer()
        if not layer or not hasattr(layer, "MovePrim"):
            raise RuntimeError("USD stage does not support atomic prim move")
        if not layer.MovePrim(source, destination):
            raise RuntimeError(f"failed to commit candidate assembly: {source} -> {destination}")

    def _spawn_robot(self, robot, *, assembly_path: str | None = None) -> None:
        import omni.usd

        option = next(item for item in env_builder.ROBOT_OPTIONS if item.key == robot.type)
        assembly_path = assembly_path or f"{ROBOT_ROOT}/{robot.id}"
        host_path = f"{assembly_path}/Host"
        stage = omni.usd.get_context().get_stage()
        stage.DefinePrim(assembly_path, "Xform")
        if option.cfg is None:
            raise ValueError("robot has no preview articulation cfg")
        spawn_cfg = option.cfg.spawn
        usd_path = getattr(spawn_cfg, "usd_path", None)
        # ``UsdFileCfg`` accepts both local paths and Isaac Sim/Nucleus
        # virtual paths (for example ``/IsaacLab/Robots/...`` or
        # ``omniverse://...``).  The USD validator opens a filesystem
        # path, so applying it to a virtual path turns a valid remote
        # asset into a false ``missing_file`` failure before the actual
        # Isaac spawner gets a chance to resolve it.
        if usd_path and self._is_local_usd_path(usd_path):
            report = validate_usd_asset(
                usd_path,
                require_articulation_root=True,
            )
            if not report.ok:
                raise RuntimeError(report.format_diagnostics())
        with self._use_stage(stage):
            spawn_cfg.func(
                host_path,
                spawn_cfg,
                translation=(0.0, 0.0, 0.0),
                orientation=(1.0, 0.0, 0.0, 0.0),
            )
        manipulator = next(
            (item.type for item in robot.attachments if item.type in {"ur5", "z1"}),
            None,
        )
        if manipulator == "ur5":
            if option.ur5_mount_profile is None:
                raise ValueError("UR5 mount profile is unavailable")
            from EAI_assets.robots.ur5_mount import spawn_mounted_ur5_single_host

            arm_path = f"{host_path}_arm"
            arm_cfg = env_builder.build_mounted_ur5_asset_cfg(
                arm_path, option.ur5_mount_profile
            )
            spawn_mounted_ur5_single_host(
                host_path=host_path,
                arm_path=arm_path,
                cfg=arm_cfg.spawn,
                profile=option.ur5_mount_profile,
            )
        elif manipulator == "z1":
            if option.z1_mount_profile is None:
                raise ValueError("Z1 mount profile is unavailable")
            from EAI_assets.robots.z1_mount import spawn_mounted_z1_single_host

            arm_path = f"{host_path}_arm"
            arm_cfg = env_builder.build_mounted_z1_asset_cfg(
                arm_path, option.z1_mount_profile
            )
            spawn_mounted_z1_single_host(
                host_path=host_path,
                arm_path=arm_path,
                cfg=arm_cfg.spawn,
                profile=option.z1_mount_profile,
            )

        attachment_types = {item.type for item in robot.attachments}
        if "gshub" in attachment_types and option.gshub_mount_link:
            gshub_mount_path = f"{host_path}/{option.gshub_mount_link}"
            if not stage.GetPrimAtPath(gshub_mount_path).IsValid():
                raise RuntimeError(f"{robot.type}: GSHub mount link does not exist: {gshub_mount_path}")
            self._spawn_sensor_reference(
                f"{gshub_mount_path}/GSHub",
                "gshub",
                option.gshub_offset,
                (1.0, 0.0, 0.0, 0.0),
                stage=stage,
            )
        if "lidar" in attachment_types and option.lidar_mount_link:
            lidar_mount_path = f"{host_path}/{option.lidar_mount_link}"
            if not stage.GetPrimAtPath(lidar_mount_path).IsValid():
                raise RuntimeError(f"{robot.type}: LiDAR mount link does not exist: {lidar_mount_path}")
            self._spawn_sensor_reference(
                f"{lidar_mount_path}/Lidar",
                "lidar",
                option.lidar_offset,
                option.lidar_rot,
                stage=stage,
            )
        self._set_pose_at_path(assembly_path, robot.position, robot.rotation)

    @staticmethod
    def _is_local_usd_path(value: Any) -> bool:
        """Return whether ``value`` names a filesystem USD asset.

        Isaac Sim's asset resolver uses absolute virtual roots such as
        ``/Isaac/`` and ``/IsaacLab/`` in addition to Omniverse URLs.  Those
        paths are resolved by the running simulator and must not be passed to
        :func:`validate_usd_asset`, which intentionally uses ``Path`` and
        ``Usd.Stage.Open`` on local files only.
        """
        text = str(value).strip()
        if not text or "://" in text:
            return False
        if text.startswith(("/Isaac/", "/IsaacLab/")):
            return False
        return True

    @staticmethod
    def _spawn_sensor_reference(prim_path: str, sensor_type: str, position, rotation, *, stage) -> None:
        import isaaclab.sim as sim_utils
        import omni.usd
        from pxr import Usd

        parent_path = prim_path.rsplit("/", 1)[0]
        if not stage.GetPrimAtPath(parent_path).IsValid():
            raise RuntimeError(f"{sensor_type}: mount parent does not exist: {parent_path}")
        if sensor_type == "gshub":
            from EAI_assets.sensor.high_sensor.gs_hub import gs_hub_path as usd_path
        else:
            from EAI_assets.sensor.low_sensor.ros_lidar import (
                ros_lidar_path as usd_path,
                spawn_ros_lidar_preview_visual,
            )

        spawn_cfg = sim_utils.UsdFileCfg(usd_path=usd_path)
        with PreviewStage._use_stage(stage):
            spawn_cfg.func(
                prim_path,
                spawn_cfg,
                translation=position,
                orientation=rotation,
            )
        if sensor_type == "lidar":
            with PreviewStage._use_stage(stage):
                spawn_ros_lidar_preview_visual(stage, prim_path)
        root = stage.GetPrimAtPath(prim_path)
        for prim in Usd.PrimRange(root):
            if prim.GetName() == "Graphs":
                prim.SetActive(False)

    def select_robot(self, robot_id: str) -> None:
        import omni.usd

        prim_path = self.robot_prim_paths.get(robot_id)
        if prim_path:
            omni.usd.get_context().get_selection().set_selected_prim_paths(
                [prim_path], True
            )

    def sync_model_from_stage(self, model) -> None:
        for robot in model.robots:
            prim_path = self.robot_prim_paths.get(robot.id)
            pose = self.read_pose(prim_path) if prim_path else None
            if pose is not None:
                model.set_robot_pose(robot.id, pose[0], pose[1])

    def set_pose(self, robot_id: str, position, rotation) -> None:
        prim_path = self.robot_prim_paths[robot_id]
        self._set_pose_at_path(prim_path, position, rotation)

    @staticmethod
    def _set_pose_at_path(prim_path: str, position, rotation) -> None:
        from pxr import Gf, UsdGeom
        import omni.usd

        prim = omni.usd.get_context().get_stage().GetPrimAtPath(prim_path)
        xform = UsdGeom.Xformable(prim)
        translate_op = None
        orient_op = None
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                translate_op = op
            elif op.GetOpType() == UsdGeom.XformOp.TypeOrient:
                orient_op = op
        translate_op = translate_op or xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
        orient_op = orient_op or xform.AddOrientOp(UsdGeom.XformOp.PrecisionDouble)
        translate_op.Set(Gf.Vec3d(*position))
        orient_op.Set(Gf.Quatd(rotation[0], Gf.Vec3d(*rotation[1:])))

    @staticmethod
    def read_pose(prim_path: str | None):
        if not prim_path:
            return None
        from pxr import UsdGeom
        import omni.usd

        prim = omni.usd.get_context().get_stage().GetPrimAtPath(prim_path)
        if not prim.IsValid():
            return None
        matrix = UsdGeom.Xformable(prim).GetLocalTransformation()
        translation = tuple(round(float(item), 7) for item in matrix.ExtractTranslation())
        quaternion = matrix.ExtractRotationQuat()
        imaginary = quaternion.GetImaginary()
        rotation = (
            round(float(quaternion.GetReal()), 9),
            round(float(imaginary[0]), 9),
            round(float(imaginary[1]), 9),
            round(float(imaginary[2]), 9),
        )
        return translation, rotation

    def robot_id_from_prim_path(self, value: Any) -> str | None:
        return payload_drop_robot_id(value, self.robot_prim_paths)

    def remove_preview(self) -> None:
        import omni.usd
        import sys

        lidar_module = sys.modules.get("EAI_assets.sensor.low_sensor.ros_lidar")
        cleanup_lidar = getattr(lidar_module, "_destroy_ros_lidar_render_products", None)
        if callable(cleanup_lidar):
            cleanup_lidar()

        stage = omni.usd.get_context().get_stage()
        scene = next(
            (item for item in env_builder.SCENE_OPTIONS if item.key == self._scene_key),
            None,
        )
        paths = [PREVIEW_ROOT, "/World/ground", "/World/DomeLight", "/World/physicsScene"]
        if scene is not None:
            paths.append(scene.prim_path)
        for path in paths:
            if stage.GetPrimAtPath(path).IsValid():
                stage.RemovePrim(path)
        self.robot_prim_paths.clear()
        self.robot_signatures.clear()
        self.robot_diagnostics.clear()
