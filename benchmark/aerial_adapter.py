"""Use the aerial lifecycle through the benchmark's existing step/evaluator seam.

EpisodeRunner remains sequential. Parallel callers use runtime.binding(request)
with CooperativeBatchExecutor; both paths share the same non-stepping handles.
"""
from algorithm.common_coordination.contract import ExecutionFeedback, FailureCode
from algorithm.skills.contracts import SkillPlan, SkillRequest
from task.contracts import DependencyOutcome


class AerialLifecycleSkillAdapter:
    def __init__(self, runtime):
        self.runtime = runtime

    def plan(self, request, *, observation=None):
        try:
            return self.runtime.plan(request)
        except (ValueError, KeyError, TypeError) as exc:
            return SkillPlan(request.request_id, request.skill_id, request.robot_id,
                status="invalid_request", metadata={"reason": str(exc), "physical_outcome": "unknown"})


class AerialTaskExecutor:
    def __init__(self, runtime, *, max_ticks, max_cancel_ticks=300):
        if any(type(n) is not int or n < 1 for n in (max_ticks, max_cancel_ticks)):
            raise ValueError("tick budgets must be positive integers")
        self.runtime = runtime
        self.max_ticks, self.max_cancel_ticks = max_ticks, max_cancel_ticks
        self.last_handle = None

    def execute(self, assignment, task, *, scene, plan, step):
        lease = handle = None
        steps = 0
        try:
            if plan.status != "planned" or plan.robot_id != assignment.robot_id:
                raise ValueError("aerial plan is invalid or belongs to another robot")
            request = SkillRequest.from_mapping(plan.metadata["lifecycle_request"])
            if request.task_id is not None and request.task_id != assignment.task_id:
                raise ValueError("aerial request belongs to another task")
            binding = self.runtime.binding(request)
            binding.validate(scene=scene, request=request)
            lease = self.runtime.resources.acquire(request.request_id, ("robot:" + request.robot_id, *binding.resources))
            handle = binding.start(plan, scene=scene, lease=lease)
            required_resources = ("robot:" + request.robot_id, *binding.resources)
            self.runtime.resources.validate_lease(
                lease,
                required_resources=required_resources,
            )
            self.last_handle = handle
            for tick in range(self.max_ticks + self.max_cancel_ticks):
                if tick == self.max_ticks:
                    handle.cancel("benchmark execution tick budget exhausted")
                action = handle.command()
                self.runtime.resources.validate_lease(
                    lease,
                    required_resources=required_resources,
                )
                try:
                    step({request.robot_id: action})
                except BaseException:
                    # The outer recovery path records callback cleanup errors
                    # without allowing them to replace this step failure.
                    raise
                steps += 1
                receipt = handle.after_step()
                self.runtime.check_scene()
                if receipt is not None:
                    self.runtime.resources.validate_lease(
                        lease,
                        required_resources=required_resources,
                    )
                    if not receipt.quiescent:
                        raise RuntimeError("aerial runtime returned an unconfirmed stop")
                    self.runtime.resources.release(lease)
                    completed = receipt.outcome is DependencyOutcome.COMPLETED and tick < self.max_ticks
                    return ExecutionFeedback(assignment.task_id, assignment.robot_id,
                        outcome="command_applied" if completed else "execution_failed",
                        failure_code=FailureCode.NONE if completed else FailureCode.EXECUTION_FAILURE,
                        timestamp_s=self.runtime.time(), message=receipt.message,
                        world_changes={"steps": steps, "physical_outcome": "unknown",
                            "runtime_state": dict(handle.response.state),
                            "route_graph_digest": self.runtime.topology.route_graph_digest,
                            "topology_product_digest": self.runtime.topology.product_digest})
            raise RuntimeError("aerial cancellation did not confirm quiescence")
        except BaseException as exc:
            cleanup_errors = []
            if lease is not None:
                if handle is None:
                    # Start may have bound native state before raising.
                    try:
                        self.runtime.resources.quarantine_active(
                            "aerial start failed with unresolved ownership"
                        )
                    except BaseException as cleanup_exc:
                        cleanup_errors.append(
                            f"start quarantine failed: {type(cleanup_exc).__name__}: {cleanup_exc}"
                        )
                else:
                    try:
                        handle.cancel(str(exc))
                    except BaseException as cleanup_exc:
                        cleanup_errors.append(
                            f"cancel failed: {type(cleanup_exc).__name__}: {cleanup_exc}"
                        )
                    try:
                        handle.quarantine("aerial execution requires host recovery")
                    except BaseException as cleanup_exc:
                        cleanup_errors.append(
                            f"quarantine failed: {type(cleanup_exc).__name__}: {cleanup_exc}"
                        )
            if not isinstance(exc, Exception):
                raise
            message = f"{type(exc).__name__}: {exc}"
            if cleanup_errors:
                message += "; cleanup errors: " + "; ".join(cleanup_errors)
            return ExecutionFeedback(assignment.task_id, assignment.robot_id, outcome="execution_failed",
                failure_code=FailureCode.INVALID_PLAN if lease is None else FailureCode.SAFE_STOP,
                message=message, world_changes={"steps": steps, "physical_outcome": "unknown"})
