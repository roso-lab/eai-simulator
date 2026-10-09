"""Copy this template into an upstream task package and fill in task semantics."""

from tools.benchmark_branch import BranchJob, BranchPlan, CheckpointArtifact


class TaskPlugin:
    """Minimal adapter skeleton; no core collector changes are required."""

    def validate_contract(self, plan: BranchPlan):
        # Freeze task_id, DAG, milestones, weights, rights, locks, horizon and
        # evaluator thresholds here.  Return a contract digest.
        return {"passed": True, "task_contract_digest": "replace-with-frozen-digest"}

    def create_scene(self, job: BranchJob):
        return self._create_isolated_scene(job)

    def prepare_base_instance(self, job: BranchJob, checkpoint: CheckpointArtifact):
        return self._prepare_runtime(job, checkpoint)

    def capture_checkpoint(self, runtime, job: BranchJob):
        return {"state": self._public_state(runtime), "metadata": {"task_contract_digest": "replace"}}

    def restore_checkpoint(self, runtime, checkpoint):
        return self._restore_public_state(runtime, checkpoint.state)

    def validate_restore(self, runtime, checkpoint, restored):
        readback = self._public_state(runtime)
        return {"passed": self._digest(readback) == checkpoint.digest,
                "readback_digest": self._digest(readback), "max_state_error": 0.0,
                "error_threshold": 1e-6, "verification_steps": ["public_readback"]}

    def apply_treatment(self, runtime, job): ...
    def step(self, runtime, job): ...
    def observe_external_state(self, runtime, job): ...
    def collect_events(self, runtime, job): return ()
    def collect_progress(self, runtime, job): return {}
    def collect_network(self, runtime, job): return {}
    def collect_cost(self, runtime, job): return {}
    def finish_episode(self, runtime, job): ...
    def close(self, runtime): ...

    # Replace these helpers with the simulator/task implementation.
    def _create_isolated_scene(self, job): raise NotImplementedError
    def _prepare_runtime(self, job, checkpoint): raise NotImplementedError
    def _public_state(self, runtime): raise NotImplementedError
    def _restore_public_state(self, runtime, state): raise NotImplementedError
    def _digest(self, state): raise NotImplementedError
