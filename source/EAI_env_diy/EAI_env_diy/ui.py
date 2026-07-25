"""omni.ui authoring window for the Env DIY 3D extension."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable
import webbrowser

import omni.ui as ui
import omni.usd

from EAI.hmrs_env.env_diy import catalog
from EAI.hmrs_env.env_diy.storage import save_task_with_payload

from .assets import AssetDownloadManager
from .drop import EnvDiyViewportDropDelegate
from .notifications import post_preview_error
from .placement import (
    canonical_robot_selection,
    collision_aware_robot_drop_position,
    payload_drop_robot_id,
    placement_diagnostics,
    raycast_surface_below,
    surface_snap_position,
)
from .protocol import AuthoringResult


DRAG_PREFIX = "eai-env-diy://"


class EnvDiyWindow:
    def __init__(
        self,
        model,
        preview,
        repo_root: Path,
        on_finish: Callable,
        asset_manager: AssetDownloadManager | None = None,
    ) -> None:
        self.model = model
        self.preview = preview
        self.repo_root = repo_root
        self.on_finish = on_finish
        self.asset_manager = asset_manager
        self._pending_finish: tuple[str, bool] | None = None
        self._asset_controls: dict[str, dict[str, object]] = {}
        self.selected_robot_id: str | None = None
        self._diagnostics: list[str] = []
        self._result_sent = False
        self._canonicalizing_selection = False
        dock_preference = getattr(getattr(ui, "DockPreference", None), "RIGHT_TOP", None)
        window_kwargs = {"width": 400, "height": 760}
        if dock_preference is not None:
            window_kwargs["dock_preference"] = dock_preference
        try:
            self._window = ui.Window("EAI Env DIY 3D", **window_kwargs)
        except TypeError:
            # Isaac Sim 5.x used the camel-case spelling.
            if dock_preference is None:
                self._window = ui.Window("EAI Env DIY 3D", **window_kwargs)
            else:
                window_kwargs["dockPreference"] = window_kwargs.pop("dock_preference")
                self._window = ui.Window("EAI Env DIY 3D", **window_kwargs)
        self._drop_helper = EnvDiyViewportDropDelegate(DRAG_PREFIX, self._on_viewport_drop)
        self._build()
        self._window.set_visibility_changed_fn(self._on_visibility_changed)
        self._usd_context = omni.usd.get_context()
        self._stage_event_sub = self._usd_context.get_stage_event_stream().create_subscription_to_pop(
            self._on_stage_event
        )

    def _build(self) -> None:
        with self._window.frame:
            with ui.VStack(spacing=8, style={"margin": 10}):
                ui.Label("Env DIY 3D", height=28, style={"font_size": 20})
                self._status = ui.Label("Ready", height=34, word_wrap=True)
                with ui.ScrollingFrame(height=650):
                    with ui.VStack(spacing=8):
                        self._build_scenes()
                        self._build_robots()
                        self._build_payloads()
                        self._build_tools()
                        self._build_selection_editor()
                self._build_actions()

    def _build_scenes(self) -> None:
        with ui.CollapsableFrame("Scenes", collapsed=False):
            with ui.VStack(spacing=4):
                for key, label in catalog.scene_choices():
                    with ui.HStack(spacing=6, height=52):
                        self._asset_image("scene", key, size=48)
                        ui.Button(
                            key.upper(),
                            width=125,
                            height=40,
                            clicked_fn=lambda scene_key=key: self._select_scene(scene_key),
                            tooltip=label,
                        )
                        self._asset_card_state(f"scene:{key}")

    def _asset_path(self, group: str, key: str) -> Path | None:
        names = [str(key)]
        if str(key).lower() == "m20":
            names.insert(0, "M20")
        roots = (
            self.repo_root / "usd" / "picture" / "processed",
            self.repo_root / "usd" / "picture",
            self.repo_root / "docs" / "source" / "_extra" / "env-diy-assets",
        )
        for root in roots:
            for name in names:
                candidate = root / group / f"{name}.png"
                if candidate.is_file():
                    return candidate
        return None

    def _asset_image(self, group: str, key: str, *, size: int = 42):
        path = self._asset_path(group, key)
        if path is None:
            return ui.Spacer(width=size, height=size)
        return ui.Image(str(path), width=size, height=size, tooltip=f"{group}/{key}")

    def _asset_requirement(self, requirement_id: str):
        from EAI_assets import asset_resolver

        try:
            return asset_resolver.resolve_card_requirement(
                requirement_id,
                scene_key=self.model.scene_key or "plane",
            )
        except (TypeError, ValueError, KeyError):
            return None

    def _asset_status(self, requirement_id: str):
        from EAI_assets import asset_resolver

        requirement = self._asset_requirement(requirement_id)
        if requirement is None:
            return None
        if self.asset_manager is not None:
            status = self.asset_manager.status(requirement_id)
            if status is not None:
                return status
        return asset_resolver.inspect_requirement(requirement)

    def _download_requirement(self, requirement_id: str) -> None:
        if self.asset_manager is None:
            self._set_status("Asset download is unavailable in this authoring session.")
            return
        requirement = self._asset_requirement(requirement_id)
        if requirement is None:
            self._set_status(f"Unknown asset requirement: {requirement_id}")
            return
        self._set_status(f"Downloading {requirement.label}...")
        try:
            pending = self.asset_manager.submit(requirement, self._on_asset_downloaded)
            self._refresh_asset_control(requirement_id, pending)
        except Exception as exc:
            self._set_status(f"Asset download failed: {exc}")

    def _on_asset_downloaded(self, status) -> None:
        state = getattr(getattr(status, "state", None), "value", getattr(status, "state", "FAILED"))
        self._refresh_asset_control(status.requirement.id, status)
        self._set_status(f"{status.requirement.label}: {state}")
        self._robot_list_frame.rebuild()
        self._selection_frame.rebuild()
        if self._pending_finish is not None and state == "READY":
            action, save = self._pending_finish
            self._pending_finish = None
            self._finish(action, save=save)

    def _asset_card_state(self, requirement_id: str, *, width: int = 70) -> None:
        status = self._asset_status(requirement_id)
        if status is None:
            return
        state = getattr(getattr(status, "state", None), "value", getattr(status, "state", "FAILED"))
        label = ui.Label(state, width=width, height=24)
        download = ui.Button(
            "Download",
            width=68,
            height=24,
            clicked_fn=lambda item_id=requirement_id: self._download_requirement(item_id),
        )
        request = ui.Button(
            "Request",
            width=58,
            height=24,
            clicked_fn=self._request_asset_access,
            tooltip="Open the gated Hugging Face asset repository.",
        )
        login = ui.Button(
            "Login",
            width=48,
            height=24,
            clicked_fn=self._start_terminal_login,
            tooltip="Run hf auth login in the launching terminal.",
        )
        recheck = ui.Button(
            "Recheck",
            width=58,
            height=24,
            clicked_fn=lambda item_id=requirement_id: self._recheck_requirement(item_id),
        )
        self._asset_controls[requirement_id] = {
            "label": label,
            "download": download,
            "request": request,
            "login": login,
            "recheck": recheck,
        }
        self._refresh_asset_control(requirement_id, status)

    def _refresh_asset_control(self, requirement_id: str, status) -> None:
        controls = self._asset_controls.get(requirement_id)
        if controls is None:
            return
        state = getattr(getattr(status, "state", None), "value", getattr(status, "state", "FAILED"))
        controls["label"].text = state
        controls["download"].visible = state in {"MISSING", "FAILED"} and self.asset_manager is not None
        controls["request"].visible = state in {"AUTH_REQUIRED", "ACCESS_PENDING"}
        controls["login"].visible = state == "AUTH_REQUIRED"
        controls["recheck"].visible = state in {"ACCESS_PENDING", "FAILED"}

    def _request_asset_access(self) -> None:
        from EAI_assets import asset_resolver

        webbrowser.open(asset_resolver.hf_repo_url())
        self._set_status("Hugging Face access request page opened.")

    def _start_terminal_login(self) -> None:
        print("[Env DIY] Starting `hf auth login` in the launching terminal.")
        try:
            subprocess.Popen(["hf", "auth", "login"])
        except OSError as exc:
            self._set_status(f"Cannot start hf auth login: {exc}")
            return
        self._set_status("Complete hf auth login in the terminal, then select Recheck.")

    def _recheck_requirement(self, requirement_id: str) -> None:
        requirement = self._asset_requirement(requirement_id)
        if requirement is None or self.asset_manager is None:
            return
        status = self.asset_manager.inspect(requirement)
        self._refresh_asset_control(requirement_id, status)
        self._set_status(status.message or f"{requirement.label}: {status.state.value}")

    def _payload_card_state(self, attachment_type: str) -> None:
        from EAI_assets import asset_resolver

        requirement_id = asset_resolver.attachment_requirement_id(attachment_type)
        self._asset_card_state(requirement_id)

    def _build_robots(self) -> None:
        with ui.CollapsableFrame("Robots", collapsed=False):
            with ui.VStack(spacing=4):
                for key in catalog.robot_keys():
                    with ui.HStack(spacing=6, height=46):
                        self._asset_image("robot", key)
                        ui.Button(
                            key.upper(),
                            width=125,
                            height=40,
                            clicked_fn=lambda robot_type=key: self._add_robot(robot_type),
                            drag_fn=lambda robot_type=key: self._drag_payload("robot", robot_type),
                            tooltip=f"Add {catalog.robot_label(key)}; drag into the viewport to place.",
                        )
                        self._asset_card_state(f"robot:{key}")
                self._robot_list_frame = ui.CollapsableFrame("Placed Robots", collapsed=False)
                self._robot_list_frame.set_build_fn(self._rebuild_robot_list)

    def _rebuild_robot_list(self) -> None:
        with ui.VStack(spacing=4):
            if not self.model.robots:
                ui.Label("No robots placed.", word_wrap=True)
                return
            for robot in self.model.robots:
                robot_id = str(robot.id)
                diagnostics = self.preview.robot_diagnostics.get(robot_id, ())
                if diagnostics:
                    state = "Failed"
                    detail = str(diagnostics[-1])
                elif robot_id in self.preview.robot_prim_paths:
                    state = "Ready"
                    detail = ""
                else:
                    state = "Pending"
                    detail = "Preview asset is not available yet."
                with ui.HStack(spacing=4, height=28):
                    ui.Button(
                        f"{robot.type.upper()} ({robot_id})",
                        width=180,
                        height=26,
                        clicked_fn=lambda selected_id=robot_id: self._select_robot(selected_id),
                    )
                    ui.Label(state, width=55, height=24)
                    if diagnostics:
                        ui.Button(
                            "Retry",
                            width=55,
                            height=26,
                            clicked_fn=lambda failed_id=robot_id: self._retry_robot(failed_id),
                            tooltip="Retry only this robot's preview assembly.",
                        )
                    ui.Button(
                        "Delete",
                        width=55,
                        height=26,
                        clicked_fn=lambda deleted_id=robot_id: self._delete_robot(deleted_id),
                    )
                if detail:
                    ui.Label(detail, word_wrap=True, style={"color": 0xFFCC7777})

    def _build_payloads(self) -> None:
        with ui.CollapsableFrame("Payloads", collapsed=False):
            with ui.VStack(spacing=4):
                ui.Label("Manipulators")
                for key in ("ur5", "z1"):
                    with ui.HStack(spacing=6, height=40):
                        self._asset_image("manipulator", key)
                        ui.Button(
                            key.upper(),
                            width=125,
                            height=34,
                            clicked_fn=lambda payload=key: self._attach(payload),
                            drag_fn=lambda payload=key: self._drag_payload("attachment", payload),
                            tooltip="Attach to the selected host at its fixed mount.",
                        )
                        self._payload_card_state(key)
                ui.Label("Sensors")
                for key in ("gshub", "lidar"):
                    with ui.HStack(spacing=6, height=40):
                        self._asset_image("sensor", key)
                        ui.Button(
                            key.upper(),
                            width=125,
                            height=34,
                            clicked_fn=lambda payload=key: self._attach(payload),
                            drag_fn=lambda payload=key: self._drag_payload("attachment", payload),
                            tooltip="Attach to the selected compatible host.",
                        )
                        self._payload_card_state(key)

    def _build_tools(self) -> None:
        with ui.CollapsableFrame("Tools", collapsed=True):
            with ui.VStack(spacing=4):
                for key in ("ros", "keyboard"):
                    with ui.HStack(spacing=6, height=40):
                        self._asset_image("tool", key)
                        ui.Button(
                            key.upper(),
                            width=210,
                            height=34,
                            clicked_fn=lambda tool=key: self._attach(tool),
                            drag_fn=lambda tool=key: self._drag_payload("attachment", tool),
                        )
                        ui.Label("READY", width=70, height=24)

    def _build_selection_editor(self) -> None:
        self._selection_frame = ui.CollapsableFrame("Selected Robot", collapsed=False)
        self._selection_frame.set_build_fn(self._rebuild_selection_editor)

    def _rebuild_selection_editor(self) -> None:
        with ui.VStack(spacing=5):
            if self.selected_robot_id is None:
                ui.Label("Select or add a robot in the viewport.", word_wrap=True)
                return
            try:
                robot = self.model.robot(self.selected_robot_id)
            except KeyError:
                self.selected_robot_id = None
                ui.Label("Select or add a robot in the viewport.")
                return
            ui.Label(f"{robot.type} ({robot.id})")
            self._pose_models = [ui.SimpleFloatModel(value) for value in (*robot.position, *robot.rotation)]
            with ui.HStack(spacing=3):
                for label, value_model in zip(("X", "Y", "Z"), self._pose_models[:3]):
                    with ui.VStack():
                        ui.Label(label, height=18)
                        ui.FloatField(value_model, height=24)
            with ui.HStack(spacing=3):
                for label, value_model in zip(("W", "QX", "QY", "QZ"), self._pose_models[3:]):
                    with ui.VStack():
                        ui.Label(label, height=18)
                        ui.FloatField(value_model, height=24)
            with ui.HStack(spacing=4):
                ui.Button("Apply Pose", clicked_fn=self._apply_pose)
                ui.Button("Read Gizmo", clicked_fn=self._read_gizmo)
                ui.Button("Delete", clicked_fn=self._delete_selected)
            self._keep_z_model = ui.SimpleBoolModel(False)
            default_root_height = self.preview.default_robot_position(robot.type, 0)[2]
            self._clearance_model = ui.SimpleFloatModel(default_root_height)
            with ui.HStack(spacing=5):
                ui.CheckBox(self._keep_z_model, width=22)
                ui.Label("Keep current Z")
                ui.Label("Clearance")
                ui.FloatField(self._clearance_model, width=70)
                ui.Button("Snap", width=58, clicked_fn=self._snap_selected)
            self._build_controller_combo(robot)
            if robot.attachments:
                ui.Label("Attached")
            for attachment in robot.attachments:
                with ui.HStack(spacing=4):
                    ui.Label(attachment.type, width=90)
                    if attachment.controller is not None:
                        self._build_attachment_controller_combo(robot.id, attachment)
                    ui.Button(
                        "Remove",
                        width=72,
                        clicked_fn=lambda key=attachment.type: self._detach(key),
                    )

    def _build_controller_combo(self, robot) -> None:
        default = catalog.default_controller_cfg(robot.type)
        options = [f"Default ({default})", *catalog.controller_cfg_names()]
        selected = 0
        if robot.controller.mode == "manual" and robot.controller.cfg in options:
            selected = options.index(robot.controller.cfg)
        ui.Label("Controller cfg")
        combo = ui.ComboBox(selected, *options)

        def changed(model, item) -> None:
            index = model.get_item_value_model().as_int
            if index == 0:
                self.model.set_robot_controller(robot.id, "default", default)
            else:
                self.model.set_robot_controller(robot.id, "manual", options[index])

        combo.model.add_item_changed_fn(changed)

    def _build_attachment_controller_combo(self, robot_id: str, attachment) -> None:
        default = catalog.attachment_entry(attachment.type).controller_cfg
        options = [f"Default ({default})", *catalog.controller_cfg_names()]
        selected = 0
        if attachment.controller.mode == "manual" and attachment.controller.cfg in options:
            selected = options.index(attachment.controller.cfg)
        combo = ui.ComboBox(selected, *options)

        def changed(model, item) -> None:
            index = model.get_item_value_model().as_int
            if index == 0:
                self.model.set_attachment_controller(
                    robot_id, attachment.type, "default", default
                )
            else:
                self.model.set_attachment_controller(
                    robot_id, attachment.type, "manual", options[index]
                )

        combo.model.add_item_changed_fn(changed)

    def _build_actions(self) -> None:
        with ui.HStack(spacing=5, height=30):
            ui.Label("Name", width=42)
            self._task_name_model = ui.SimpleStringModel("env_diy_3d")
            ui.StringField(self._task_name_model, width=250, height=26)
        with ui.HStack(spacing=5, height=30):
            ui.Button("Save", width=82, height=28, clicked_fn=lambda: self._finish("save", save=True))
            ui.Button("Save & Run", width=100, height=28, clicked_fn=lambda: self._finish("run", save=True))
            ui.Button("Run", width=70, height=28, clicked_fn=lambda: self._finish("run", save=False))
            ui.Button("Cancel", width=82, height=28, clicked_fn=self._cancel)
        self._download_all_button = ui.Button(
            "Download all and run",
            height=26,
            clicked_fn=self._download_all_and_run,
        )
        self._download_all_button.visible = False

    def _download_all_and_run(self) -> None:
        if self._pending_finish is None:
            self._pending_finish = ("run", False)
        self._start_missing_downloads(start=True)

    def _on_scene_changed(self, model, item) -> None:
        index = model.get_item_value_model().as_int
        key = catalog.scene_choices()[index][0]
        self._select_scene(key)

    def _select_scene(self, key: str) -> None:
        status = self._asset_status(f"scene:{key}")
        state = getattr(getattr(status, "state", None), "value", getattr(status, "state", "READY"))
        if state != "READY":
            self._set_status(f"Scene {key} is {state}; download it before switching.")
            return
        try:
            self.model.set_scene(key)
            self.preview.load_scene(key)
            self._set_status(f"Scene: {key}")
        except Exception as exc:
            self._set_status(f"Scene load failed: {exc}")

    def _add_robot(self, robot_type: str, position=None) -> None:
        status = self._asset_status(f"robot:{robot_type}")
        state = getattr(getattr(status, "state", None), "value", getattr(status, "state", "READY"))
        if state != "READY":
            self._set_status(f"Robot {robot_type} is {state}; download it before adding.")
            return
        index = len(self.model.robots)
        position = position or self.preview.default_robot_position(robot_type, index)
        try:
            robot_id = self.model.add_robot(robot_type, position=position)
            self.selected_robot_id = robot_id
            self._rebuild_preview()
            self.preview.select_robot(robot_id)
        except Exception as exc:
            self._set_status(f"Cannot add {robot_type}: {exc}")

    def _attach(self, attachment_type: str, robot_id: str | None = None) -> None:
        target = robot_id or self.selected_robot_id
        if target is None:
            self._set_status("Select a host robot first.")
            return
        try:
            from EAI_assets import asset_resolver

            host = self.model.robot(target).type
            requirement_id = asset_resolver.attachment_requirement_id(
                attachment_type,
                robot_type=host,
            )
            status = self._asset_status(requirement_id)
            state = getattr(getattr(status, "state", None), "value", getattr(status, "state", "READY"))
            if state != "READY":
                self._set_status(f"{attachment_type} is {state}; download it before attaching.")
                return
            self.model.attach(target, attachment_type)
            self.selected_robot_id = target
            self._rebuild_preview()
        except Exception as exc:
            self._set_status(str(exc))

    def _detach(self, attachment_type: str) -> None:
        if self.selected_robot_id is None:
            return
        self.model.detach(self.selected_robot_id, attachment_type)
        self._rebuild_preview()

    def _delete_selected(self) -> None:
        if self.selected_robot_id is None:
            return
        self._delete_robot(self.selected_robot_id)

    def _select_robot(self, robot_id: str) -> None:
        try:
            self.model.robot(robot_id)
        except KeyError:
            return
        self.selected_robot_id = robot_id
        self.preview.select_robot(robot_id)
        self._selection_frame.rebuild()

    def _retry_robot(self, robot_id: str) -> None:
        try:
            self.model.robot(robot_id)
        except KeyError:
            return
        self.selected_robot_id = robot_id
        self._rebuild_preview()

    def _delete_robot(self, robot_id: str) -> None:
        try:
            self.model.delete_robot(robot_id)
        except KeyError:
            return
        if self.selected_robot_id == robot_id:
            self.selected_robot_id = None
        self._rebuild_preview()

    def _apply_pose(self) -> None:
        if self.selected_robot_id is None:
            return
        values = [model.as_float for model in self._pose_models]
        try:
            self.model.set_robot_pose(self.selected_robot_id, values[:3], values[3:])
            robot = self.model.robot(self.selected_robot_id)
            self.preview.set_pose(self.selected_robot_id, robot.position, robot.rotation)
            self._set_status("Pose applied.")
        except Exception as exc:
            self._set_status(str(exc))

    def _read_gizmo(self) -> None:
        self.preview.sync_model_from_stage(self.model)
        self._selection_frame.rebuild()
        self._set_status("Viewport transform read.")

    def _snap_selected(self) -> None:
        if self.selected_robot_id is None:
            return
        self.preview.sync_model_from_stage(self.model)
        robot = self.model.robot(self.selected_robot_id)
        prim_path = self.preview.robot_prim_paths.get(robot.id)
        hit = raycast_surface_below(robot.position, excluded_prim_prefix=prim_path)
        if hit is None:
            self._set_status("No collision surface found below robot.")
            return
        position = surface_snap_position(
            robot.position,
            hit_position=hit,
            clearance=self._clearance_model.as_float,
            keep_current_z=self._keep_z_model.as_bool,
        )
        self.model.set_robot_pose(robot.id, position, robot.rotation)
        self.preview.set_pose(robot.id, position, robot.rotation)
        self._selection_frame.rebuild()
        self._set_status("Robot snapped to collision surface.")

    def _rebuild_preview(self) -> None:
        spawn_diagnostics = self.preview.rebuild_robots(self.model)
        missing = tuple(
            robot.id for robot in self.model.robots if robot.id not in self.preview.robot_prim_paths
        )
        self._diagnostics = [
            *spawn_diagnostics,
            *placement_diagnostics(self.model, missing_robot_ids=missing),
        ]
        self._robot_list_frame.rebuild()
        self._selection_frame.rebuild()
        if self.selected_robot_id:
            self.preview.select_robot(self.selected_robot_id)
        self._set_status("; ".join(self._diagnostics) if self._diagnostics else "Preview updated.")

    @staticmethod
    def _drag_payload(kind: str, key: str) -> str:
        ui.Label(key.upper())
        return f"{DRAG_PREFIX}{kind}/{key}"

    def _on_viewport_drop(self, payload, target, world_position, _context_name):
        value = str(payload)
        if not value.startswith(DRAG_PREFIX):
            return False
        kind, key = value[len(DRAG_PREFIX):].split("/", 1)
        if kind == "robot":
            index = len(self.model.robots)
            default_position = self.preview.default_robot_position(key, index)
            probe_position = (
                float(world_position[0]),
                float(world_position[1]),
                float(world_position[2]) + 0.05,
            )
            collision_hit = raycast_surface_below(probe_position)
            try:
                position = collision_aware_robot_drop_position(
                    world_position,
                    collision_hit=collision_hit,
                    default_root_height=default_position[2],
                )
            except ValueError as exc:
                message = str(exc)
                self._set_status(message)
                post_preview_error(message)
                return False
            self._add_robot(
                key,
                position,
            )
        else:
            robot_id = payload_drop_robot_id(target, self.preview.robot_prim_paths)
            if robot_id is None:
                self._set_status("Drop payload onto a host robot.")
                return False
            self._attach(key, robot_id=robot_id)
        return True

    def _start_missing_downloads(self, *, start: bool = False) -> bool:
        if self.asset_manager is None:
            return True
        from EAI_assets import asset_resolver

        try:
            graph = asset_resolver.resolve_selection(self.model.to_selection_dict())
        except Exception as exc:
            self._set_status(f"Cannot inspect selection assets: {exc}")
            return False
        statuses = asset_resolver.inspect_graph(graph)
        missing = tuple(status.requirement for status in statuses if status.state != asset_resolver.RequirementState.READY)
        if not missing:
            self._download_all_button.visible = False
            return True
        self._download_all_button.visible = True
        if not start:
            self._set_status(
                "Missing selection assets: "
                + ", ".join(requirement.id for requirement in missing)
                + ". Select Download all and run."
            )
            return False

        def completed(results) -> None:
            failures = tuple(
                status for status in results if status.state != asset_resolver.RequirementState.READY
            )
            if failures:
                self._pending_finish = None
                self._set_status("Asset download failed: " + "; ".join(status.message for status in failures))
                post_preview_error(self._status.text)
                return
            self._download_all_button.visible = False
            if self._pending_finish is not None:
                action, save = self._pending_finish
                self._pending_finish = None
                self._finish(action, save=save)

        self.asset_manager.submit_all(graph, completed)
        self._set_status("Downloading selection assets...")
        return False

    def _finish(self, action: str, *, save: bool) -> None:
        unresolved = sorted(
            {
                *self.preview.robot_diagnostics,
                *(
                    str(robot.id)
                    for robot in self.model.robots
                    if str(robot.id) not in self.preview.robot_prim_paths
                ),
            }
        )
        if unresolved:
            message = f"Resolve preview failures before {action}: {', '.join(unresolved)}"
            self._set_status(message)
            post_preview_error(message)
            return
        if not self._start_missing_downloads():
            self._pending_finish = (action, save)
            return
        try:
            self.preview.sync_model_from_stage(self.model)
            selection = self.model.to_selection_dict()
            saved_task = None
            saved_path = None
            if save:
                saved_path_obj, saved_task = save_task_with_payload(
                    self._task_name_model.as_string,
                    selection,
                    repo_root=self.repo_root,
                )
                saved_path = str(saved_path_obj)
            self._emit_result(
                AuthoringResult(
                    status="completed",
                    action=action,
                    selection=selection,
                    saved_task=saved_task,
                    saved_path=saved_path,
                )
            )
        except Exception as exc:
            message = f"Env DIY {action} failed: {exc}"
            self._set_status(message)
            post_preview_error(message)

    def _cancel(self) -> None:
        self._emit_result(AuthoringResult.cancelled())

    def _emit_result(self, result: AuthoringResult) -> None:
        if self._result_sent:
            return
        self._result_sent = True
        try:
            self.on_finish(result)
        except Exception:
            self._result_sent = False
            raise

    def _on_visibility_changed(self, visible: bool) -> None:
        if not visible and not self._result_sent:
            self._cancel()

    def _on_stage_event(self, event) -> None:
        if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
            return
        if self._canonicalizing_selection:
            return
        selected = self._usd_context.get_selection().get_selected_prim_paths()
        canonical = canonical_robot_selection(selected, self.preview.robot_prim_paths)
        if canonical is None:
            return
        robot_id, assembly_path = canonical
        if tuple(selected) != (assembly_path,):
            self._canonicalizing_selection = True
            try:
                self._usd_context.get_selection().set_selected_prim_paths(
                    [assembly_path], True
                )
            finally:
                self._canonicalizing_selection = False
        if robot_id is not None and robot_id != self.selected_robot_id:
            self.selected_robot_id = robot_id
            self._selection_frame.rebuild()

    def _set_status(self, message: str) -> None:
        self._status.text = str(message)

    def hide(self) -> None:
        if self._window is not None:
            self._window.visible = False

    def destroy(self) -> None:
        self._stage_event_sub = None
        if self._drop_helper is not None:
            self._drop_helper.destroy()
        self._drop_helper = None
        window = self._window
        self._window = None
        if window is not None:
            window.set_visibility_changed_fn(None)
            window.destroy()
        self._selection_frame = None
        self._usd_context = None
        self.on_finish = None
        self.model = None
        self.preview = None
