from dataclasses import replace
from types import SimpleNamespace

import pytest

from algorithm.common_coordination.contract import Assignment, CoordinationTask
from algorithm.skills.contracts import SkillRequest
from algorithm.skills.aerial_transport.geometry import RigidPose
from algorithm.skills.navigation.aerial_topology import FrozenAerialTopology
from task.contracts import DependencyOutcome, TaskRunRequest
from task.executors.aerial_runtime import AerialSkillRuntime
from task.executors.cooperative import CooperativeBatchExecutor
from task.resources import ResourceManager
from tools.topology_authoring.model import TopologyDocument
from benchmark.aerial_adapter import AerialLifecycleSkillAdapter, AerialTaskExecutor


class Env:
    num_envs = 1

    def __init__(self):
        self.positions = {r: (0., 0., 1.) for r in ("r1", "r2")}
        self.goals = dict(self.positions)
        self.ticks = 0
        self.actions = []
        self._controller_configs = {r: SimpleNamespace(command_name="goal_position", control_mode="position", robot_type="PegasusIris") for r in self.positions}

    def set_command(self, robot, name, value):
        assert name == "goal_position"
        self.goals[robot] = tuple(value)

    def step(self, actions):
        self.actions.append(actions)
        self.positions.update({r: self.goals[r] for r in actions})
        self.ticks += 1


def setup_runtime(*, time_reader=None, velocity_reader=None, settle_timeout_s=5.):
    env = Env()
    doc = TopologyDocument("fixture", domain="aerial")
    doc.add_node((0, 0, 1), node_id="source", label="loading")
    doc.add_node((0, 0, 2), node_id="destination", label="meeting")
    doc.add_edge("source", "destination", [(0, 0, 1), (0, 0, 2)])
    runtime = AerialSkillRuntime(env, topology=FrozenAerialTopology(doc.to_dict(), scene_key="fixture", source_digests={}),
        source_reader=lambda: ("fixture", {}), resources=ResourceManager(),
        sequence_reader=lambda: env.ticks, pose_reader=lambda r: RigidPose(env.positions[r]),
        time_reader=(lambda: env.ticks * .1) if time_reader is None else lambda: time_reader(env),
        velocity_reader=velocity_reader or (lambda r: [0.] * 6),
        action_factory=lambda env, r, xyz: list(xyz),
        settle_timeout_s=settle_timeout_s)
    return env, runtime


def request(robot="r1", task="a", *, request_id=None, sequence=0):
    return SkillRequest(request_id or "request:" + task, "eai.navigation.aerial_navigation", robot, task_id=task,
        target_ref="destination", parameters={"world_state_seq": sequence, "origin_ref": "source"})


def test_two_navigation_handles_use_one_shared_step_and_authentic_leases():
    env, runtime = setup_runtime()
    tasks = [CoordinationTask(t, "diagnostic") for t in ("a", "b")]
    assignments = [Assignment(t, r, "test") for t, r in (("a", "r1"), ("b", "r2"))]
    batch = CooperativeBatchExecutor(lambda a, task, scene: runtime.binding(request(a.robot_id, a.task_id)),
        step=lambda scene, actions: env.step(actions), neutral_actions=lambda scene: {r: [0, 0, 0] for r in env.positions},
        resources=runtime.resources, max_ticks=20, max_cancel_ticks=5, expected_scene_id="fixture")
    from task.tests.test_cooperative import package
    batch.bind_run(TaskRunRequest(replace(package(tasks), scene_id="fixture")))
    updates = batch.execute_batch(assignments, {t.task_id: t for t in tasks}, scene=env)
    assert all(u.dependency_outcome is DependencyOutcome.COMPLETED for u in updates)
    assert env.ticks == 2
    assert all(set(action) == {"r1", "r2"} for action in env.actions)
    assert runtime.resources.snapshot()["leases"] == []


def test_benchmark_lifecycle_does_not_report_physical_success():
    env, runtime = setup_runtime()
    plan = AerialLifecycleSkillAdapter(runtime).plan(request())
    result = AerialTaskExecutor(runtime, max_ticks=20).execute(Assignment("a", "r1", "test"),
        CoordinationTask("a", "diagnostic"), scene=env, plan=plan, step=env.step)
    assert result.outcome == "command_applied"
    assert result.world_changes["physical_outcome"] == "unknown"
    assert env.ticks == 2


def test_queue_only_host_quarantines_instead_of_releasing():
    env, runtime = setup_runtime()
    plan = runtime.plan(request())
    result = AerialTaskExecutor(runtime, max_ticks=20).execute(Assignment("a", "r1", "test"),
        CoordinationTask("a", "diagnostic"), scene=env, plan=plan, step=lambda actions: None)
    assert result.outcome == "execution_failed"
    assert runtime.resources.snapshot()["leases"][0]["state"] == "quarantined"
    assert env.ticks == 0


def test_source_change_and_plan_change_rejected_before_movement():
    env, runtime = setup_runtime()
    req = request()
    binding = runtime.binding(req)
    plan = runtime.plan(req)
    runtime.source_reader = lambda: ("other", {})
    with pytest.raises(ValueError, match="source changed"):
        binding.validate(scene=env, request=req)
    runtime.source_reader = lambda: ("fixture", {})
    lease = runtime.resources.acquire(req.request_id, ("robot:r1",))
    with pytest.raises(ValueError, match="plan"):
        binding.start(replace(plan, commands=()), scene=env, lease=lease)
    assert env.ticks == 0 and env.goals == env.positions


def test_scene_binding_accepts_direct_env_and_session_wrapper():
    for wrapped in (False, True):
        env, runtime = setup_runtime()
        req = request()
        plan = runtime.plan(req)
        scene = SimpleNamespace(base_env=env) if wrapped else env
        binding = runtime.binding(req)
        binding.validate(scene=scene, request=req)
        lease = runtime.resources.acquire(req.request_id, ("robot:r1",))
        handle = binding.start(plan, scene=scene, lease=lease)
        assert runtime.handles["a"] is handle
        runtime.resources.release(lease)


def test_foreign_scene_rejected_before_command_or_step_effects():
    env, runtime = setup_runtime()
    foreign = Env()
    req = request()
    plan = runtime.plan(req)
    binding = runtime.binding(req)

    with pytest.raises(ValueError, match="runtime environment"):
        binding.validate(scene=foreign, request=req)
    lease = runtime.resources.acquire(req.request_id, ("robot:r1",))
    with pytest.raises(ValueError, match="runtime environment"):
        binding.start(plan, scene=foreign, lease=lease)
    runtime.resources.release(lease)

    feedback = AerialTaskExecutor(runtime, max_ticks=20).execute(
        Assignment("a", "r1", "test"),
        CoordinationTask("a", "diagnostic"),
        scene=foreign,
        plan=plan,
        step=foreign.step,
    )
    assert feedback.outcome == "execution_failed"
    assert feedback.failure_code.value == "invalid_plan"
    assert env.ticks == foreign.ticks == 0
    assert env.actions == foreign.actions == []


@pytest.mark.parametrize(
    "time_reader",
    [
        lambda env: float("nan") if env.ticks >= 2 else env.ticks * .1,
        lambda env: float("inf") if env.ticks >= 2 else env.ticks * .1,
        lambda env: True if env.ticks >= 2 else env.ticks * .1,
        lambda env: .05 if env.ticks >= 2 else env.ticks * .1,
    ],
    ids=("nan", "infinity", "boolean", "backwards"),
)
def test_invalid_navigation_clock_cannot_produce_receipt_or_release(time_reader):
    env, runtime = setup_runtime(time_reader=time_reader)
    plan = runtime.plan(request())

    feedback = AerialTaskExecutor(runtime, max_ticks=20).execute(
        Assignment("a", "r1", "test"),
        CoordinationTask("a", "diagnostic"),
        scene=env,
        plan=plan,
        step=env.step,
    )

    assert feedback.outcome == "execution_failed"
    assert env.ticks == 2
    assert runtime.handles["a"] is not None
    lease, = runtime.resources.snapshot()["leases"]
    assert lease["state"] == "quarantined"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -float("inf")])
def test_settle_timeout_requires_positive_finite_nonboolean(value):
    with pytest.raises(ValueError, match="finite numeric"):
        setup_runtime(settle_timeout_s=value)


def test_completed_task_id_can_retry_with_fresh_request_id():
    env, runtime = setup_runtime()
    executor = AerialTaskExecutor(runtime, max_ticks=20)
    first = request()
    first_feedback = executor.execute(
        Assignment("a", "r1", "test"),
        CoordinationTask("a", "diagnostic"),
        scene=env,
        plan=runtime.plan(first),
        step=env.step,
    )
    assert first_feedback.outcome == "command_applied"
    assert runtime.handles == {}

    env.positions["r1"] = env.goals["r1"] = (0., 0., 1.)
    retry = request(request_id="request:a:retry", sequence=env.ticks)
    retry_feedback = executor.execute(
        Assignment("a", "r1", "test"),
        CoordinationTask("a", "diagnostic"),
        scene=env,
        plan=runtime.plan(retry),
        step=env.step,
    )
    assert retry_feedback.outcome == "command_applied"
    assert runtime.handles == {}
    assert runtime.resources.snapshot() == {"leases": [], "resources": {}}


def test_overlapping_active_task_id_still_rejects_fresh_request():
    env, runtime = setup_runtime()
    first = request()
    first_binding = runtime.binding(first)
    first_lease = runtime.resources.acquire(first.request_id, ("robot:r1",))
    first_handle = first_binding.start(
        runtime.plan(first),
        scene=env,
        lease=first_lease,
    )

    overlapping = request(
        robot="r2",
        request_id="request:a:overlap",
        sequence=env.ticks,
    )
    overlapping_binding = runtime.binding(overlapping)
    overlapping_lease = runtime.resources.acquire(
        overlapping.request_id,
        ("robot:r2",),
    )
    with pytest.raises(ValueError, match="already started"):
        overlapping_binding.start(
            runtime.plan(overlapping),
            scene=env,
            lease=overlapping_lease,
        )

    assert runtime.handles == {"a": first_handle}
    assert env.ticks == 0 and env.actions == []
    runtime.resources.release(overlapping_lease)
    runtime.resources.release(first_lease)


def test_benchmark_cleanup_failures_do_not_replace_step_error():
    env, runtime = setup_runtime()
    original_binding = runtime.binding

    def binding_with_failing_cleanup(value):
        bound = original_binding(value)

        def start(plan, *, scene, lease):
            handle = bound.start(plan, scene=scene, lease=lease)

            def fail_cancel(reason):
                raise KeyboardInterrupt("cancel cleanup interrupt")

            def fail_quarantine(reason):
                raise SystemExit("quarantine cleanup exit")

            handle.cancel = fail_cancel
            handle.quarantine = fail_quarantine
            return handle

        return replace(bound, start=start)

    runtime.binding = binding_with_failing_cleanup

    def fail_step(actions):
        raise RuntimeError("primary shared-step error")

    feedback = AerialTaskExecutor(runtime, max_ticks=20).execute(
        Assignment("a", "r1", "test"),
        CoordinationTask("a", "diagnostic"),
        scene=env,
        plan=runtime.plan(request()),
        step=fail_step,
    )

    assert feedback.outcome == "execution_failed"
    assert "RuntimeError: primary shared-step error" in feedback.message
    assert "cancel failed: KeyboardInterrupt: cancel cleanup interrupt" in feedback.message
    assert "quarantine failed: SystemExit: quarantine cleanup exit" in feedback.message


def test_transport_lifecycle_uses_host_ticks_and_releases_outer_lease():
    from task.executors.aerial_pick_place import AerialPickPlaceExecutor
    env, runtime = setup_runtime()

    class Bridge:
        held = support = disabled = False
        object_position = (0., 0., .2)

        def bind(self, *args):
            pass

        def unbind(self, key):
            assert not (self.held or self.support or self.disabled)

        def measure(self, key):
            root = env.positions["r1"]
            return root, root if self.held else self.object_position

        def attached(self, key):
            return self.held

        def evidence(self, key):
            return {"joint_path": "/joint" if self.held else None, "collision_disabled": self.disabled}

        def apply(self, key, operation, confirmation_id):
            if operation == "create_support": self.support = True
            elif operation == "disable_collision": self.disabled = True
            elif operation == "attach": self.held = True
            elif operation == "remove_support": self.support = False
            elif operation in {"release", "rollback"}:
                if self.held: self.object_position = env.positions["r1"]
                self.held = self.support = self.disabled = False
            return {"operation": operation, "confirmation_id": confirmation_id, "applied": True}

    bridge = Bridge()
    runtime.transport_factory = lambda env, **kw: AerialPickPlaceExecutor(env, **kw, bridge=bridge,
        reference_resolver=lambda kind, ref: "/World/" + ref,
        pose_reader=runtime.pose, simulation_time_reader=runtime.time,
        action_factory=runtime.action, quiescence_reader=lambda key, robot: True)
    req = SkillRequest("carry", "eai.transport.aerial_pick_place", "r1", task_id="carry",
        parameters={"world_state_seq": 0, "object_ref": "box", "source_ref": "source", "destination_ref": "destination",
            "carry_mode": "fixed_joint", "robot_ids": ["r1"], "pick_anchor_world_m": [0, 0, .2],
            "place_anchor_world_m": [0, 0, .2]})
    plan = runtime.plan(req)
    feedback = AerialTaskExecutor(runtime, max_ticks=800).execute(Assignment("carry", "r1", "test"),
        CoordinationTask("carry", "diagnostic"), scene=env, plan=plan, step=env.step)
    assert feedback.outcome == "command_applied", feedback
    assert runtime.resources.snapshot()["leases"] == []
    assert feedback.world_changes["steps"] == env.ticks
    assert not bridge.held and not bridge.disabled
