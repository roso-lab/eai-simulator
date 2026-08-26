from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest


def _missing_optional_dependencies() -> tuple[str, ...]:
    missing = []
    for name in ("PIL.Image", "numpy", "torch", "yaml"):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            missing.append(name)
    return tuple(missing)


_MISSING_OPTIONAL_DEPENDENCIES = _missing_optional_dependencies()
pytestmark = pytest.mark.skipif(
    bool(_MISSING_OPTIONAL_DEPENDENCIES),
    reason=(
        "requires optional multi-robot navigation test dependencies: "
        + ", ".join(_MISSING_OPTIONAL_DEPENDENCIES)
    ),
)

if not _MISSING_OPTIONAL_DEPENDENCIES:
    from PIL import Image
    import torch
    import yaml

    from algorithm.multi_robot_navigation.map_environment import Environment
    from algorithm.multi_robot_navigation.fetch_motion_primitives import (
        MotionPrimitiveError,
        fetch_motion_primitives,
    )
    from algorithm.multi_robot_navigation.planner import DbcbsPlan, PlanSample
    from algorithm.multi_robot_navigation.planner import _validate_pairwise_clearance
    from algorithm.multi_robot_navigation.session import (
        DbcbsNavigationSession,
        PreparedDbcbsMission,
    )
    from algorithm.multi_robot_navigation.eai_plugin import (
        EaiMultiRobotNavigationPlugin,
        builtin_scene_map,
        is_aerial_robot,
    )
    from algorithm.multi_robot_navigation.interaction import (
        discover_robot_prim_paths,
        resolve_robot_from_prim_path,
    )
else:
    Image = None
    torch = None
    yaml = None
    Environment = None
    MotionPrimitiveError = None
    fetch_motion_primitives = None
    DbcbsPlan = None
    PlanSample = None
    _validate_pairwise_clearance = None
    DbcbsNavigationSession = None
    PreparedDbcbsMission = None
    EaiMultiRobotNavigationPlugin = None
    builtin_scene_map = None
    is_aerial_robot = None
    discover_robot_prim_paths = None
    resolve_robot_from_prim_path = None


class _FakeRobot:
    def __init__(
        self,
        xyz: tuple[float, float, float],
        prim_path: str | None = None,
    ) -> None:
        self.data = SimpleNamespace(
            root_pos_w=torch.tensor([xyz], dtype=torch.float32),
            root_quat_w=torch.tensor(
                [[1.0, 0.0, 0.0, 0.0]], dtype=torch.float32
            ),
        )
        if prim_path is not None:
            self.root_physx_view = SimpleNamespace(prim_paths=[prim_path])

    def find_bodies(self, *_args, **_kwargs):
        return [], []


def _write_scene_map(root: Path, scene: str = "plane") -> Path:
    map_dir = root / "scene" / scene
    map_dir.mkdir(parents=True)
    image_path = map_dir / f"{scene}_map.png"
    Image.new("L", (160, 160), color=255).save(image_path)
    yaml_path = map_dir / f"{scene}_map.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "image": image_path.name,
                "resolution": 0.05,
                "origin": [-4.0, -4.0, 0.0],
                "negate": 0,
                "occupied_thresh": 0.65,
                "free_thresh": 0.196,
            }
        ),
        encoding="utf-8",
    )
    return yaml_path


def _plugin(
    tmp_path: Path, *, planner_backend: str = "global", **plugin_kwargs
) -> EaiMultiRobotNavigationPlugin:
    robots = {
        "carter_1": _FakeRobot((-3.0, 0.0, 0.2)),
        "go2_1": _FakeRobot((3.0, 0.0, 0.4)),
        "pegasus_1": _FakeRobot((0.0, 0.0, 3.0)),
    }
    controllers = {
        "carter_1": SimpleNamespace(
            robot_type="Carter", command_name="base_velocity"
        ),
        "go2_1": SimpleNamespace(
            robot_type="Go2", command_name="base_velocity"
        ),
        "pegasus_1": SimpleNamespace(
            robot_type="Pegasus X4", command_name="goal_position"
        ),
    }
    base_env = SimpleNamespace(
        scene=SimpleNamespace(articulations=robots),
        step_dt=0.02,
    )
    env_cfg = SimpleNamespace(controllers=controllers)
    return EaiMultiRobotNavigationPlugin(
        base_env,
        list(robots),
        env_cfg,
        "cpu",
        1,
        _write_scene_map(tmp_path),
        planner_backend=planner_backend,
        controller_normalizer=lambda entry: (entry, ()),
        **plugin_kwargs,
    )


def _prepared_mission(starts, goals) -> PreparedDbcbsMission:
    trajectories = {
        name: (
            PlanSample(
                0.0,
                float(starts[name][0]),
                float(starts[name][1]),
                0.0,
                0.0,
            ),
            PlanSample(1.0, float(goal[0]), float(goal[1]), 0.0, 0.0),
        )
        for name, goal in goals.items()
    }
    return PreparedDbcbsMission(
        result={name: True for name in goals},
        plan=DbcbsPlan(trajectories=trajectories),
    )


def _wait_for_planner(plugin, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        future = plugin._planning_future
        if future is not None and future.done():
            return
        time.sleep(0.001)
    pytest.fail("background db-CBS planner did not finish in time")


def _payload_metadata(payload: bytes) -> tuple[int, str]:
    return len(payload), hashlib.sha256(payload).hexdigest()


def test_motion_primitive_fetch_downloads_and_verifies_file_url(tmp_path: Path):
    payload = b"maintained db-CBS motion primitive fixture"
    expected_size, expected_sha256 = _payload_metadata(payload)
    source = tmp_path / "provider.msgpack"
    target = tmp_path / "motions" / "installed.msgpack"
    source.write_bytes(payload)

    result = fetch_motion_primitives(
        target=target,
        url=source.as_uri(),
        expected_size=expected_size,
        expected_sha256=expected_sha256,
        username=None,
    )

    assert result == target
    assert target.read_bytes() == payload


def test_motion_primitive_fetch_uses_valid_cached_file_without_network(tmp_path: Path):
    payload = b"already installed"
    expected_size, expected_sha256 = _payload_metadata(payload)
    target = tmp_path / "installed.msgpack"
    target.write_bytes(payload)

    result = fetch_motion_primitives(
        target=target,
        url=(tmp_path / "missing-provider.msgpack").as_uri(),
        expected_size=expected_size,
        expected_sha256=expected_sha256,
        username=None,
    )

    assert result == target
    assert target.read_bytes() == payload


def test_motion_primitive_fetch_rejects_bad_hash_without_installing(tmp_path: Path):
    source = tmp_path / "provider.msgpack"
    target = tmp_path / "installed.msgpack"
    source.write_bytes(b"unexpected payload")

    with pytest.raises(MotionPrimitiveError, match="Invalid motion primitives"):
        fetch_motion_primitives(
            target=target,
            url=source.as_uri(),
            expected_size=len(b"unexpected payload"),
            expected_sha256="0" * 64,
            username=None,
        )

    assert not target.exists()
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_motion_primitive_fetch_leaves_invalid_cached_file_untouched(tmp_path: Path):
    target = tmp_path / "installed.msgpack"
    target.write_bytes(b"invalid cached payload")

    with pytest.raises(MotionPrimitiveError, match="Invalid motion primitives"):
        fetch_motion_primitives(
            target=target,
            url=(tmp_path / "missing-provider.msgpack").as_uri(),
            expected_size=10,
            expected_sha256="0" * 64,
            username=None,
        )

    assert target.read_bytes() == b"invalid cached payload"


def test_filters_aerial_robots_from_the_managed_team(tmp_path: Path):
    plugin = _plugin(tmp_path)

    assert plugin.possible_agents == ["carter_1", "go2_1"]
    assert plugin.excluded_agents == ["pegasus_1"]
    assert plugin.robot_radii == {"carter_1": 0.35, "go2_1": 0.30}
    assert is_aerial_robot("Quadcopter", "cf2x_1")
    assert not is_aerial_robot("Carter", "carter_1")


def test_builtin_scene_map_uses_external_usd_root_and_ensures_pair(tmp_path: Path):
    expected = _write_scene_map(tmp_path, "factory")
    ensured = []
    resolver = SimpleNamespace(
        usd_root=lambda: tmp_path,
        ensure_usd_assets_for_paths=lambda paths: ensured.extend(paths),
    )

    path = builtin_scene_map("Factory", asset_resolver=resolver)

    assert path == expected
    assert ensured == [str(expected), str(expected.with_suffix(".png"))]


def test_builtin_scene_map_rejects_unregistered_scene(tmp_path: Path):
    resolver = SimpleNamespace(
        usd_root=lambda: tmp_path,
        ensure_usd_assets_for_paths=lambda _paths: None,
    )

    with pytest.raises(ValueError, match="Unknown scene map key"):
        builtin_scene_map("../factory", asset_resolver=resolver)


def test_builtin_scene_map_requires_complete_pair(tmp_path: Path):
    map_dir = tmp_path / "scene" / "plane"
    map_dir.mkdir(parents=True)
    yaml_path = map_dir / "plane_map.yaml"
    yaml_path.write_text("image: plane_map.png\n", encoding="utf-8")
    resolver = SimpleNamespace(
        usd_root=lambda: tmp_path,
        ensure_usd_assets_for_paths=lambda _paths: None,
    )

    with pytest.raises(FileNotFoundError, match="complete occupancy map"):
        builtin_scene_map("plane", asset_resolver=resolver)


def test_partial_mission_reserves_holder_and_clears_selection_after_start(tmp_path: Path):
    plugin = _plugin(tmp_path)
    plugin.select_robot("carter_1")
    plugin.set_selected_goal((0.0, 2.0))

    assert plugin.start_navigation() == {"carter_1": True}
    assert plugin.selected_robot is None
    assert plugin.pending_goals == {}

    actions = plugin.compute_actions()
    assert set(actions) == {"carter_1", "go2_1"}
    assert torch.count_nonzero(actions["carter_1"]) > 0
    assert torch.count_nonzero(actions["go2_1"]) == 0


def test_requires_a_selection_and_at_least_one_goal(tmp_path: Path):
    plugin = _plugin(tmp_path)

    with pytest.raises(RuntimeError, match="Select a robot"):
        plugin.set_selected_goal((1.0, 1.0))
    with pytest.raises(RuntimeError, match="at least one"):
        plugin.start_navigation()


def test_visualization_exposes_positions_and_remaining_paths(tmp_path: Path):
    plugin = _plugin(tmp_path)
    plugin.set_goal("carter_1", (0.0, 2.0))
    assert plugin.start_navigation() == {"carter_1": True}

    assert plugin.robot_position("carter_1") == pytest.approx((-3.0, 0.0, 0.2))
    paths = plugin.planned_paths()
    assert "carter_1" in paths
    assert paths["carter_1"][-1] == pytest.approx((0.0, 2.0), abs=0.04)


def test_viewport_hit_resolves_any_robot_descendant():
    robots = {
        "carter_1": _FakeRobot(
            (0.0, 0.0, 0.2),
            "/World/envs/env_0/carter_1/chassis_link",
        ),
        "go2_1": _FakeRobot(
            (1.0, 0.0, 0.4),
            "/World/envs/env_0/go2_1/trunk",
        ),
    }
    base_env = SimpleNamespace(scene=SimpleNamespace(articulations=robots))

    paths = discover_robot_prim_paths(base_env, tuple(robots))
    assert paths == {
        "carter_1": "/World/envs/env_0/carter_1",
        "go2_1": "/World/envs/env_0/go2_1",
    }
    assert (
        resolve_robot_from_prim_path(
            "/World/envs/env_0/go2_1/trunk/FR_calf", paths
        )
        == "go2_1"
    )
    assert resolve_robot_from_prim_path("/World/Factory/Floor", paths) is None


def test_partial_planning_failure_does_not_start_any_robot(monkeypatch, tmp_path: Path):
    plugin = _plugin(tmp_path)
    plugin.set_goal("carter_1", (0.0, 2.0))
    plugin.set_goal("go2_1", (0.0, -2.0))

    def partial_result(requests, **_kwargs):
        carter_request = next(item for item in requests if item[0] == "carter_1")
        plugin.session.navigator.set_waypoints(
            "carter_1",
            [
                (*carter_request[1], 0.0),
                (*carter_request[2], 0.0),
            ],
            active=True,
        )
        return {"carter_1": plugin.session.navigator.get_waypoints("carter_1")}

    monkeypatch.setattr(plugin.session, "plan_batch", partial_result)

    assert plugin.start_navigation() == {"carter_1": True, "go2_1": False}
    assert plugin.state().navigating_robots == ()
    assert set(plugin.pending_goals) == {"carter_1", "go2_1"}


def test_dbcbs_session_reserves_unselected_ground_robot_as_obstacle():
    planner_call = {}

    def fake_planner(**kwargs):
        planner_call.update(kwargs)
        trajectories = {
            agent.name: (
                PlanSample(0.0, *agent.start, 0.0, 0.0),
                PlanSample(0.1, *agent.goal, 0.0, 0.0),
            )
            for agent in kwargs["agents"]
        }
        return DbcbsPlan(trajectories=trajectories)

    frame = SimpleNamespace(
        environment=Environment(min=(-5.0, -5.0), max=(5.0, 5.0), boxes=()),
        scale=1.0,
        snap=lambda x, y, **_kwargs: (x, y),
    )
    session = DbcbsNavigationSession(
        "unused.yaml",
        planning_frame=frame,
        planner=fake_planner,
        robot_radius=0.5,
        safety_margin=0.1,
    )

    result = session.plan_mission(
        starts={"carter_1": (-2.0, 0.0), "go2_1": (0.0, 0.0)},
        goals={"carter_1": (2.0, 0.0)},
    )

    assert result == {"carter_1": True}
    assert [agent.name for agent in planner_call["agents"]] == ["carter_1"]
    assert planner_call["agents"][0].radius == pytest.approx(0.6)
    assert planner_call["obstacles"] == [
        {"type": "box", "center": [0.0, 0.0], "size": [1.2, 1.2]}
    ]
    assert session.is_navigating("carter_1")
    assert session.get_waypoints("carter_1")[-1][:2] == (2.0, 0.0)
    assert session.unsafe_pairs(
        {"carter_1": (0.0, 0.0), "go2_1": (1.1, 0.0)}
    ) == (("carter_1", "go2_1", 1.1, 1.2),)
    assert session.proximity_pairs(
        {"carter_1": (0.0, 0.0), "go2_1": (1.3, 0.0)},
        extra_clearance=0.2,
    ) == (("carter_1", "go2_1", 1.3, 1.4),)


def test_failed_dbcbs_replan_restores_the_active_plan():
    def initial_planner(**kwargs):
        return DbcbsPlan(
            trajectories={
                agent.name: (
                    PlanSample(0.0, *agent.start, 0.0, 0.0),
                    PlanSample(0.1, *agent.goal, 0.0, 0.0),
                )
                for agent in kwargs["agents"]
            }
        )

    frame = SimpleNamespace(
        environment=Environment(min=(-5.0, -5.0), max=(5.0, 5.0), boxes=()),
        scale=1.0,
        snap=lambda x, y, **_kwargs: (x, y),
    )
    session = DbcbsNavigationSession(
        "unused.yaml", planning_frame=frame, planner=initial_planner
    )
    session.plan_mission(
        starts={"carter_1": (-2.0, 0.0)},
        goals={"carter_1": (2.0, 0.0)},
    )
    previous_plan = session.plan
    previous_paths = dict(session.paths)

    def failed_planner(**_kwargs):
        raise RuntimeError("no replacement path")

    session._run_planner = failed_planner
    with pytest.raises(RuntimeError, match="no replacement path"):
        session.replan_mission(
            starts={"carter_1": (-1.5, 0.0)},
            goals={"carter_1": (2.0, 0.0)},
        )

    assert session.plan is previous_plan
    assert session.paths == previous_paths
    assert session.is_navigating("carter_1")


def test_dbcbs_initial_planning_does_not_block_the_simulation_loop(monkeypatch, tmp_path: Path):
    plugin = _plugin(
        tmp_path,
        planner_backend="dbcbs",
        dbcbs_replan_clearance=0.25,
        dbcbs_replan_retry_interval=0.0,
    )
    planner_started = threading.Event()
    release_planner = threading.Event()

    def prepare(starts, goals):
        planner_started.set()
        if not release_planner.wait(timeout=1.0):
            raise TimeoutError("test planner was not released")
        return _prepared_mission(starts, goals)

    monkeypatch.setattr(plugin.session, "prepare_mission", prepare)
    try:
        plugin.set_goal("carter_1", (2.0, 2.0))
        started_at = time.perf_counter()
        assert plugin.start_navigation() == {"carter_1": True}
        assert time.perf_counter() - started_at < 0.5
        assert planner_started.wait(timeout=0.5)
        assert plugin.state().planning

        for _ in range(3):
            frame_started = time.perf_counter()
            actions = plugin.compute_actions()
            assert time.perf_counter() - frame_started < 0.5
            assert all(torch.count_nonzero(action) == 0 for action in actions.values())

        release_planner.set()
        _wait_for_planner(plugin)
        actions = plugin.compute_actions()

        assert all(torch.count_nonzero(action) == 0 for action in actions.values())
        assert not plugin.state().planning
        assert plugin.state().navigating_robots == ("carter_1",)
    finally:
        release_planner.set()
        plugin.close()


def test_failed_async_initial_plan_restores_pending_goals(monkeypatch, tmp_path: Path):
    plugin = _plugin(tmp_path, planner_backend="dbcbs")

    def prepare(_starts, _goals):
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(plugin.session, "prepare_mission", prepare)
    try:
        plugin.set_goal("carter_1", (2.0, 2.0))
        assert plugin.start_navigation() == {"carter_1": True}
        _wait_for_planner(plugin)
        actions = plugin.compute_actions()

        state = plugin.state()
        assert all(torch.count_nonzero(action) == 0 for action in actions.values())
        assert not state.planning
        assert state.planning_error == "RuntimeError: planner unavailable"
        assert state.navigating_robots == ()
        assert state.pending_goals == {"carter_1": (2.0, 2.0)}
    finally:
        plugin.close()


def test_clearance_replanning_is_async_and_keeps_the_mission(monkeypatch, tmp_path: Path):
    plugin = _plugin(
        tmp_path,
        planner_backend="dbcbs",
        dbcbs_replan_clearance=0.25,
        dbcbs_replan_retry_interval=0.0,
    )
    calls = []
    replan_started = threading.Event()
    release_replan = threading.Event()

    def prepare(starts, goals):
        calls.append((dict(starts), dict(goals)))
        if len(calls) > 1:
            replan_started.set()
            if not release_replan.wait(timeout=1.0):
                raise TimeoutError("test replanner was not released")
        return _prepared_mission(starts, goals)

    monkeypatch.setattr(plugin.session, "prepare_mission", prepare)
    try:
        plugin.set_goal("carter_1", (2.0, 2.0))
        assert plugin.start_navigation() == {"carter_1": True}
        _wait_for_planner(plugin)
        plugin.compute_actions()

        robots = plugin.base_env.scene.articulations
        robots["carter_1"].data.root_pos_w[0, :2] = torch.tensor([-0.5, 0.0])
        robots["go2_1"].data.root_pos_w[0, :2] = torch.tensor([0.5, 0.0])
        actions = plugin.compute_actions()

        assert replan_started.wait(timeout=0.5)
        assert len(calls) == 2
        assert calls[-1][1] == {"carter_1": (2.0, 2.0)}
        assert all(torch.count_nonzero(action) == 0 for action in actions.values())
        assert plugin.state().replanning
        assert plugin.state().replan_attempts == 1

        frame_started = time.perf_counter()
        actions = plugin.compute_actions()
        assert time.perf_counter() - frame_started < 0.5
        assert all(torch.count_nonzero(action) == 0 for action in actions.values())

        release_replan.set()
        _wait_for_planner(plugin)
        plugin.compute_actions()

        state = plugin.state()
        assert not state.replanning
        assert state.replan_event == pytest.approx(
            ("carter_1", "go2_1", 1.0, 1.1)
        )
        assert state.navigating_robots == ("carter_1",)
        assert state.safety_stop is None
    finally:
        release_replan.set()
        plugin.close()


def test_failed_async_replan_keeps_the_goal_and_retries(monkeypatch, tmp_path: Path):
    plugin = _plugin(
        tmp_path,
        planner_backend="dbcbs",
        dbcbs_replan_clearance=0.25,
        dbcbs_replan_retry_interval=0.0,
    )
    attempts = 0

    def prepare(starts, goals):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("temporary planner failure")
        return _prepared_mission(starts, goals)

    monkeypatch.setattr(plugin.session, "prepare_mission", prepare)
    try:
        plugin.set_goal("carter_1", (2.0, 2.0))
        assert plugin.start_navigation() == {"carter_1": True}
        _wait_for_planner(plugin)
        plugin.compute_actions()

        robots = plugin.base_env.scene.articulations
        robots["carter_1"].data.root_pos_w[0, :2] = torch.tensor([-0.5, 0.0])
        robots["go2_1"].data.root_pos_w[0, :2] = torch.tensor([0.5, 0.0])
        plugin.compute_actions()
        _wait_for_planner(plugin)
        plugin.compute_actions()

        failed_state = plugin.state()
        assert failed_state.replanning
        assert failed_state.navigating_robots == ("carter_1",)
        assert failed_state.replan_error == "RuntimeError: temporary planner failure"
        assert plugin._mission_goals == {"carter_1": (2.0, 2.0)}

        plugin.compute_actions()
        _wait_for_planner(plugin)
        plugin.compute_actions()
        recovered_state = plugin.state()
        assert attempts == 3
        assert not recovered_state.replanning
        assert recovered_state.replan_error is None
        assert recovered_state.navigating_robots == ("carter_1",)
    finally:
        plugin.close()


def test_rejects_optimized_trajectory_inside_robot_footprints():
    plan = DbcbsPlan(
        trajectories={
            "carter_1": (
                PlanSample(0.0, -1.0, 0.0, 1.0, 0.0),
                PlanSample(0.1, 1.0, 0.0, 1.0, 0.0),
            ),
            "go2_1": (
                PlanSample(0.0, 1.0, 0.0, -1.0, 0.0),
                PlanSample(0.1, -1.0, 0.0, -1.0, 0.0),
            ),
        }
    )

    with pytest.raises(RuntimeError, match="footprint clearance"):
        _validate_pairwise_clearance(
            plan, {"carter_1": 0.175, "go2_1": 0.10}
        )


def test_clearance_validation_uses_heterogeneous_pair_requirements():
    def stationary(name, x):
        return name, (PlanSample(0.0, x, 0.0, 0.0, 0.0),)

    plan = DbcbsPlan(
        trajectories=dict((
            stationary("small_a", 0.0),
            stationary("small_b", 1.0),
            stationary("large_a", 10.0),
            stationary("large_b", 12.0),
        ))
    )

    with pytest.raises(RuntimeError, match="large_a vs large_b"):
        _validate_pairwise_clearance(
            plan,
            {"small_a": 0.1, "small_b": 0.1, "large_a": 1.5, "large_b": 1.5},
        )
