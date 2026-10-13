from types import SimpleNamespace

from algorithm.common_coordination.contract import Assignment, FailureCode
from algorithm.skills.contracts import SkillCommand, SkillPlan, SkillRequest
from benchmark.eai_adapter import CommandNavigationSkillAdapter, EaiSessionRuntime, EaiTaskExecutor
from benchmark.protocol import BranchRole, RunKey
from benchmark.runtime import EpisodeRunner


class FakeEnv:
    def __init__(self):
        self.actions = []

    def step(self, actions):
        self.actions.append(actions)
        return {"ok": True}


class FakeBase:
    def __init__(self):
        self.commands = []
        self.scene = SimpleNamespace(articulations={})

    def set_command(self, robot, command, value):
        self.commands.append((robot, command, value))


def _session():
    base = FakeBase()
    env = FakeEnv()
    cfg = SimpleNamespace(controllers={"robot_a": SimpleNamespace(command_name="goal_position")})
    return SimpleNamespace(base_env=base, env=env, env_cfg=cfg, device="cpu", num_envs=2, possible_agents=["robot_a"])


def test_navigation_skill_and_eai_executor_apply_bounded_command():
    session = _session()
    runtime = EaiSessionRuntime(session)
    plan = CommandNavigationSkillAdapter().plan(
        SkillRequest("r0", "eai.navigation.goal_position", "robot_a", target_pose=(1, 2, 0.5), parameters={"horizon_ticks": 2})
    )
    boundaries = []

    def step(actions):
        del actions
        runtime.step({})
        boundary = SimpleNamespace(sim_time_s=float(len(boundaries) + 1) * 0.1)
        boundaries.append(boundary)
        return boundary

    feedback = EaiTaskExecutor(max_steps=2).execute(
        Assignment("deliver", "robot_a", "test"),
        SimpleNamespace(metadata={}),
        scene=session,
        plan=plan,
        step=step,
    )
    assert feedback.failure_code is FailureCode.NONE
    assert feedback.world_changes == {"commands_applied": 1, "steps": 2}
    assert len(session.base_env.commands) == 1
    assert session.base_env.commands[0][0:2] == ("robot_a", "goal_position")
    assert len(session.base_env.commands[0][2]) == 2
    assert len(session.env.actions) == 2
    assert all("robot_a" in action for action in session.env.actions)


def test_executor_fails_closed_for_unsupported_command_and_horizon():
    session = _session()
    adapter = CommandNavigationSkillAdapter()
    unsupported = adapter.plan(SkillRequest("r1", "other.skill", "robot_a"))
    feedback = EaiTaskExecutor(max_steps=1).execute(
        Assignment("deliver", "robot_a", "test"),
        SimpleNamespace(metadata={}),
        scene=session,
        plan=unsupported,
        step=lambda actions: None,
    )
    assert feedback.failure_code is FailureCode.INVALID_PLAN


    too_long = adapter.plan(
        SkillRequest("r2", "eai.navigation.goal_position", "robot_a", target_pose=(1, 2, 3), parameters={"horizon_ticks": 2})
    )
    feedback = EaiTaskExecutor(max_steps=1).execute(
        Assignment("deliver", "robot_a", "test"),
        SimpleNamespace(metadata={}),
        scene=session,
        plan=too_long,
        step=lambda actions: None,
    )
    assert feedback.failure_code is FailureCode.INVALID_PLAN


def test_velocity_command_is_passed_as_controller_action_not_only_command_storage():
    session = _session()
    session.env_cfg = SimpleNamespace(controllers={"robot_a": SimpleNamespace(command_name="base_velocity")})
    plan = SkillPlan(
        request_id="velocity-request",
        skill_id="eai.navigation.velocity",
        robot_id="robot_a",
        commands=(SkillCommand("velocity", {"value": [0.2, 0.0, 0.1], "horizon_ticks": 1}),),
    )
    feedback = EaiTaskExecutor(max_steps=1).execute(
        Assignment("drive", "robot_a", "test"),
        SimpleNamespace(metadata={}),
        scene=session,
        plan=plan,
        step=lambda actions: (session.env.actions.append(dict(actions)) or SimpleNamespace(sim_time_s=0.1)),
    )
    assert feedback.failure_code is FailureCode.NONE
    assert session.base_env.commands == []
    assert len(session.env.actions) == 1
    assert session.env.actions[0]["robot_a"] is not None


def test_custom_tensor_factory_is_batched_for_vectorized_session():
    session = _session()
    runtime = EaiSessionRuntime(session, tensor_factory=lambda values, device: list(values))
    neutral = runtime.neutral_actions()
    assert neutral["robot_a"] == [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]


def test_session_runtime_exposes_public_simulation_clock():
    session = _session()
    session.base_env.sim = SimpleNamespace(current_time_step_index=4, get_physics_dt=lambda: 0.05)
    assert EaiSessionRuntime(session).sim_time_s() == 0.2


def test_external_observation_provider_augments_public_robot_observation():
    session = _session()
    observation = EaiSessionRuntime(
        session,
        observation_provider=lambda _session: {
            "targets": {"handoff_zone": {"position": [1.0, 0.0, 0.0]}},
            "external_events": [{"event": "handoff_receipt", "robot_id": "robot_a"}],
        },
    ).observe()
    assert observation["robots"] == {}
    assert observation["targets"]["handoff_zone"]["position"] == [1.0, 0.0, 0.0]
    assert observation["external_events"][0]["event"] == "handoff_receipt"


def test_episode_runner_records_session_clock_at_step_boundary():
    session = _session()
    session.base_env.sim = SimpleNamespace(current_time_step_index=4, get_physics_dt=lambda: 0.05)
    runner = EpisodeRunner(
        EaiSessionRuntime(session),
        run_key=RunKey("base", "system", "condition", "repeat", "round", BranchRole.FACT),
    )
    boundary = runner.step({})
    assert boundary.sim_time_s == 0.2


def test_yaw_requires_declared_controller_and_applies_with_xyz_in_same_tick():
    session = _session()
    plan = CommandNavigationSkillAdapter().plan(SkillRequest(
        "yaw", "eai.navigation.goal_position", "robot_a", target_pose=(1, 2, 3),
        parameters={"target_yaw": 1.57, "horizon_ticks": 1}))
    kwargs = dict(scene=session, plan=plan, step=lambda actions: session.env.actions.append(actions))
    executor = EaiTaskExecutor(max_steps=1, tensor_factory=lambda values, device: list(values))
    feedback = executor.execute(Assignment("yaw", "robot_a", "test"), SimpleNamespace(metadata={}), **kwargs)
    assert feedback.failure_code is FailureCode.INVALID_PLAN
    assert not session.base_env.commands and not session.env.actions
    session.env_cfg.controllers["robot_a"].yaw_command_name = "goal_yaw"
    feedback = executor.execute(Assignment("yaw", "robot_a", "test"), SimpleNamespace(metadata={}), **kwargs)
    assert feedback.outcome == "command_applied"
    assert [item[1] for item in session.base_env.commands] == ["goal_yaw", "goal_position"]
    assert session.base_env.commands[0][2] == [[1.57], [1.57]]
    assert len(session.env.actions) == 1
