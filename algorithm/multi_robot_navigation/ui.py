"""Isaac Sim viewport interaction for in-process EAI navigation."""

from __future__ import annotations

import math
import weakref
from typing import Any

import omni.ui as ui
from omni.ui import color as cl
from omni.ui import scene as sc
import omni.usd

from algorithm.multi_robot_navigation.interaction import (
    discover_robot_prim_paths,
    resolve_robot_from_prim_path,
)


_ROBOT_COLORS = (
    "#22D3EE",
    "#F97316",
    "#E879F9",
    "#34D399",
    "#60A5FA",
    "#FB7185",
)


def _viewport_api(viewport_desc: Any) -> Any | None:
    if hasattr(viewport_desc, "get"):
        return viewport_desc.get("viewport_api")
    return getattr(viewport_desc, "viewport_api", None)


class _NavigationClickGesture(sc.ClickGesture):
    def __init__(self, viewport_api: Any, owner: "EaiNavigationUI") -> None:
        super().__init__(mouse_button=0)
        self._viewport_api = viewport_api
        self._owner = weakref.ref(owner)

    def _query_completed(self, prim_path: object, world_position: Any, *_args) -> None:
        owner = self._owner()
        if owner is not None:
            owner.handle_viewport_hit(prim_path, world_position)

    def on_ended(self, *_args) -> None:
        if self.state == sc.GestureState.CANCELED:
            return
        mouse_ndc = self.sender.gesture_payload.mouse
        mouse_pixel, viewport_api = self._viewport_api.map_ndc_to_texture_pixel(
            mouse_ndc
        )
        if mouse_pixel is None or viewport_api is None:
            return
        from omni.kit.viewport.window.raycast import perform_raycast_query

        perform_raycast_query(
            viewport_api=viewport_api,
            mouse_ndc=mouse_ndc,
            mouse_pixel=mouse_pixel,
            on_complete_fn=self._query_completed,
            query_name="EAI multi-robot navigation goal",
        )


class _NavigationOverlay(sc.Manipulator):
    def __init__(self, viewport_api: Any, owner: "EaiNavigationUI") -> None:
        super().__init__()
        self._viewport_api = viewport_api
        self._owner = weakref.ref(owner)
        self._root = None
        self._screen = None

    def on_build(self) -> None:
        owner = self._owner()
        if owner is None:
            return
        self._root = sc.Transform()
        with self._root:
            self._screen = sc.Screen(
                gesture=_NavigationClickGesture(self._viewport_api, owner)
            )
            owner.build_viewport_markers()

    def refresh(self) -> None:
        self.invalidate()

    def destroy(self) -> None:
        self.clear()
        self._screen = None
        self._root = None


class _NavigationViewportScene:
    def __init__(self, viewport_desc: Any, owner: "EaiNavigationUI") -> None:
        self._owner = weakref.ref(owner)
        self._manipulator = _NavigationOverlay(_viewport_api(viewport_desc), owner)
        owner._attach_scene(self)

    @property
    def visible(self) -> bool:
        return bool(self._manipulator and self._manipulator.visible)

    @visible.setter
    def visible(self, value: bool) -> None:
        if self._manipulator is not None:
            self._manipulator.visible = bool(value)

    def refresh(self) -> None:
        if self._manipulator is not None:
            self._manipulator.refresh()

    def clear(self) -> None:
        if self._manipulator is not None:
            self._manipulator.clear()

    def destroy(self) -> None:
        owner = self._owner()
        if owner is not None:
            owner._detach_scene(self)
        if self._manipulator is not None:
            self._manipulator.destroy()
        self._manipulator = None

    def __del__(self) -> None:
        self.destroy()


class EaiNavigationUI:
    """Dockable click-to-select navigation controls for an EAI plugin."""

    def __init__(self, navigation: Any) -> None:
        self.navigation = navigation
        self._destroyed = False
        self._scenes: weakref.WeakSet[_NavigationViewportScene] = weakref.WeakSet()
        self._last_panel_state = None
        self._last_visual_state = None
        self._was_planning = False
        self._was_navigating = False
        self._goal_heights: dict[str, float] = {}
        self._active_goals: dict[str, tuple[float, float]] = {}
        self._robot_prim_paths = discover_robot_prim_paths(
            navigation.base_env, navigation.possible_agents
        )
        self._colors = {
            name: _ROBOT_COLORS[index % len(_ROBOT_COLORS)]
            for index, name in enumerate(navigation.possible_agents)
        }

        self._usd_context = omni.usd.get_context()
        self._stage_event_sub = (
            self._usd_context.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event,
                name="EAI multi-robot navigation selection",
            )
        )

        from omni.kit.viewport.registry import RegisterScene

        self._scene_registration = RegisterScene(
            lambda desc: _NavigationViewportScene(desc, self),
            "eai.multi_robot_navigation.interaction",
        )
        self._create_window()
        self.refresh(force=True)

    def _create_window(self) -> None:
        dock = getattr(getattr(ui, "DockPreference", None), "RIGHT_TOP", None)
        kwargs: dict[str, Any] = {"width": 360, "height": 390}
        if dock is not None:
            kwargs["dock_preference"] = dock
        try:
            self._window = ui.Window("EAI Multi-Robot Navigation", **kwargs)
        except TypeError:
            if dock is not None:
                kwargs["dockPreference"] = kwargs.pop("dock_preference")
            self._window = ui.Window("EAI Multi-Robot Navigation", **kwargs)

        with self._window.frame:
            with ui.VStack(spacing=8, style={"margin": 10}):
                ui.Label(
                    "Multi-Robot Navigation",
                    height=28,
                    style={"font_size": 20},
                )
                self._status = ui.Label("Ready", height=38, word_wrap=True)
                ui.Separator()
                with ui.HStack(height=26):
                    ui.Label("Selected", width=90)
                    self._selected = ui.Label("None")
                ui.Label("Assigned Goals", height=24, style={"font_size": 16})
                self._goals_frame = ui.Frame(height=120)
                self._goals_frame.set_build_fn(self._build_goal_rows)
                ui.Spacer()
                self._start_button = ui.Button(
                    "Start Navigation",
                    height=38,
                    clicked_fn=self._start_navigation,
                )
                with ui.HStack(spacing=8, height=34):
                    ui.Button(
                        "Clear Goals",
                        clicked_fn=self._clear_goals,
                    )
                    ui.Button(
                        "Stop",
                        clicked_fn=self._stop_navigation,
                    )

    def _build_goal_rows(self) -> None:
        goals = self.navigation.state().pending_goals
        with ui.VStack(spacing=4):
            if not goals:
                ui.Label("None", height=24, style={"color": 0xFF888888})
                return
            for name, (x, y) in goals.items():
                with ui.HStack(height=25, spacing=6):
                    ui.Rectangle(
                        width=8,
                        height=8,
                        style={"background_color": self._colors[name]},
                    )
                    ui.Label(name, width=105)
                    ui.Label(f"x {x:.2f}   y {y:.2f}")

    def _attach_scene(self, scene: _NavigationViewportScene) -> None:
        self._scenes.add(scene)

    def _detach_scene(self, scene: _NavigationViewportScene) -> None:
        self._scenes.discard(scene)

    def _on_stage_event(self, event: Any) -> None:
        if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
            return
        selected = self._usd_context.get_selection().get_selected_prim_paths()
        for prim_path in selected:
            robot_name = resolve_robot_from_prim_path(
                prim_path, self._robot_prim_paths
            )
            if robot_name is not None:
                self._select_robot(robot_name, update_usd_selection=False)
                return

    def _select_robot(
        self, robot_name: str, *, update_usd_selection: bool = True
    ) -> None:
        self.navigation.select_robot(robot_name)
        if update_usd_selection:
            self._usd_context.get_selection().set_selected_prim_paths(
                [self._robot_prim_paths[robot_name]], True
            )
        self._set_status(f"Selected {robot_name}")
        self.refresh(force=True)

    def handle_viewport_hit(self, prim_path: object, world_position: Any) -> None:
        if self._destroyed:
            return
        robot_name = resolve_robot_from_prim_path(
            prim_path, self._robot_prim_paths
        )
        if robot_name is not None:
            self._select_robot(robot_name)
            return

        selected = self.navigation.state().selected_robot
        if selected is None or not prim_path:
            return
        try:
            point = tuple(float(world_position[index]) for index in range(3))
        except (IndexError, TypeError, ValueError):
            self._set_status("The selected surface has no valid world position")
            return
        if not all(math.isfinite(value) for value in point):
            self._set_status("The selected surface has no valid world position")
            return

        self.navigation.set_selected_goal(point[:2])
        self._goal_heights[selected] = point[2] + 0.06
        self._set_status(
            f"Goal set for {selected}: ({point[0]:.2f}, {point[1]:.2f})"
        )
        self.refresh(force=True)

    def _start_navigation(self) -> None:
        requested = dict(self.navigation.state().pending_goals)
        try:
            result = self.navigation.start_navigation()
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_status(str(exc))
            return
        failed = [name for name, succeeded in result.items() if not succeeded]
        if failed:
            self._set_status("Planning failed: " + ", ".join(failed))
            self.refresh(force=True)
            return

        self._active_goals = requested
        self._was_navigating = bool(self.navigation.state().navigating_robots)
        self._usd_context.get_selection().set_selected_prim_paths([], True)
        self._set_status("Navigation started: " + ", ".join(result))
        self.refresh(force=True)

    def _clear_goals(self) -> None:
        self.navigation.clear_pending_goals()
        self._set_status("Pending goals cleared")
        self.refresh(force=True)

    def _stop_navigation(self) -> None:
        self.navigation.stop_navigation()
        self._active_goals.clear()
        self._was_planning = False
        self._was_navigating = False
        self._usd_context.get_selection().set_selected_prim_paths([], True)
        self._set_status("Navigation stopped")
        self.refresh(force=True)

    def _set_status(self, message: str) -> None:
        if self._status is not None:
            self._status.text = str(message)

    def refresh(self, *, force: bool = False) -> None:
        if self._destroyed:
            return
        state = self.navigation.state()
        panel_state = (
            state.selected_robot,
            tuple(state.pending_goals.items()),
            state.navigating_robots,
            state.safety_stop,
            state.planning,
            state.planning_error,
            state.replanning,
            state.replan_event,
            state.replan_attempts,
            state.replan_error,
        )
        if force or panel_state != self._last_panel_state:
            self._last_panel_state = panel_state
            self._selected.text = state.selected_robot or "None"
            self._start_button.enabled = bool(state.pending_goals) and not (
                state.planning or state.replanning
            )
            self._goals_frame.rebuild()

        navigating = bool(state.navigating_robots)
        if state.planning:
            self._set_status("Planning multi-robot route...")
        elif state.planning_error:
            self._set_status(state.planning_error)
        elif state.replanning and state.replan_event is not None:
            left, right, distance, threshold = state.replan_event
            message = (
                f"Replanning attempt {state.replan_attempts}: {left} / {right} "
                f"distance {distance:.2f} m, threshold {threshold:.2f} m"
            )
            if state.replan_error:
                message += f"; retry pending ({state.replan_error})"
            self._set_status(message)
        elif state.replan_event is not None:
            left, right, distance, threshold = state.replan_event
            self._set_status(
                f"Path replanned for {left} / {right}: distance {distance:.2f} m, "
                f"threshold {threshold:.2f} m"
            )
        elif state.safety_stop is not None:
            left, right, distance, required = state.safety_stop
            self._set_status(
                f"Safety stop: {left} / {right} distance {distance:.2f} m, "
                f"required {required:.2f} m"
            )
        elif self._was_planning and navigating:
            self._set_status("Navigation started")
        elif self._was_navigating and not navigating:
            self._set_status("Mission complete")
        self._was_planning = state.planning
        self._was_navigating = navigating

        motion_state = []
        navigator = self.navigation.session.navigator
        for name in self._active_goals:
            x, y, _ = self.navigation.robot_position(name)
            motion_state.append(
                (name, round(x, 1), round(y, 1), navigator.get_index(name))
            )
        selected_position = None
        if state.selected_robot is not None:
            selected_position = tuple(
                round(value, 2)
                for value in self.navigation.robot_position(state.selected_robot)
            )
        visual_state = (
            panel_state,
            tuple(self._active_goals.items()),
            tuple(motion_state),
            selected_position,
        )
        if force or visual_state != self._last_visual_state:
            self._last_visual_state = visual_state
            for scene in tuple(self._scenes):
                scene.refresh()

    def _height_for(self, robot_name: str) -> float:
        return self._goal_heights.get(robot_name, 0.08)

    @staticmethod
    def _selection_radius(robot_name: str) -> float:
        token = robot_name.casefold()
        if "scout" in token:
            return 0.9
        if "carter" in token:
            return 0.7
        if any(name in token for name in ("go2", "lite3")):
            return 0.65
        return 0.6

    def build_viewport_markers(self) -> None:
        state = self.navigation.state()
        if state.selected_robot is not None:
            name = state.selected_robot
            x, y, z = self.navigation.robot_position(name)
            with sc.Transform(
                transform=sc.Matrix44.get_translation_matrix(x, y, z + 0.06)
            ):
                sc.Arc(
                    self._selection_radius(name),
                    wireframe=True,
                    tesselation=96,
                    thickness=5,
                    color=cl("#FACC15"),
                )

        visible_goals = dict(self._active_goals)
        visible_goals.update(state.pending_goals)
        for name, (x, y) in visible_goals.items():
            color = cl(self._colors[name])
            z = self._height_for(name)
            with sc.Transform(
                transform=sc.Matrix44.get_translation_matrix(x, y, z)
            ):
                sc.Arc(
                    0.34,
                    wireframe=True,
                    tesselation=64,
                    thickness=4,
                    color=color,
                )
                sc.Line((-0.24, 0.0, 0.0), (0.24, 0.0, 0.0), color=color, thickness=3)
                sc.Line((0.0, -0.24, 0.0), (0.0, 0.24, 0.0), color=color, thickness=3)
                with sc.Transform(
                    look_at=sc.Transform.LookAt.CAMERA,
                    transform=sc.Matrix44.get_translation_matrix(0.0, 0.0, 0.25),
                ):
                    sc.Label(name, color=color, size=15)

        paths = self.navigation.planned_paths()
        for name in self._active_goals:
            waypoints = paths.get(name, ())
            if not waypoints:
                continue
            z = self._height_for(name)
            x, y, _ = self.navigation.robot_position(name)
            points = [(x, y, z), *((px, py, z) for px, py in waypoints)]
            if len(points) >= 2:
                sc.Curve(
                    points,
                    curve_type=sc.Curve.CurveType.LINEAR,
                    colors=[cl(self._colors[name])],
                    thicknesses=[3.0],
                )

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._stage_event_sub = None
        registration, self._scene_registration = self._scene_registration, None
        if registration is not None:
            registration.destroy()
        self._scenes.clear()
        window, self._window = self._window, None
        if window is not None:
            window.destroy()
        self._goals_frame = None
        self._start_button = None
        self._selected = None
        self._status = None
        self._usd_context = None
        self.navigation = None


__all__ = ["EaiNavigationUI"]
