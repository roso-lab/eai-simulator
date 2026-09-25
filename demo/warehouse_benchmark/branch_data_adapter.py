"""Warehouse adapter for the shared branch data tool.

The existing checkpoint branch runner remains the owner of Isaac scene setup,
controllers, task progression and checkpoint physics.  This adapter only
projects that live state into scene-agnostic branch identities and JSON-safe
per-slot records.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from demo.benchmark.branch_data import BranchIdentity, digest_value


@dataclass
class WarehouseBranchDataAdapter:
    scenario: Mapping[str, Any]
    scenario_path: Path
    targets: list[tuple[float, float]]
    branch_routes: list[list[tuple[float, float]]]
    scene_factory: Callable[..., Any] | None = None

    scene_key = "warehouse_nvd"
    task_id = "W-DAG-01"
    snapshot_schema = "warehouse.public_physics_task_rng.v1"

    def __post_init__(self) -> None:
        if self.scenario.get("scene_key") != self.scene_key:
            raise ValueError("Warehouse adapter requires scene_key=warehouse_nvd")
        if self.scenario.get("benchmark_task_id") != self.task_id:
            raise ValueError("Warehouse adapter requires benchmark_task_id=W-DAG-01")
        if len(self.targets) != len(self.branch_routes):
            raise ValueError("target and route counts differ")
        self.task_version = f"warehouse.task.v{int(self.scenario.get('version', 0))}"
        self.base_instance_id = (
            f"{self.task_id}:{digest_value(dict(self.scenario))[:16]}"
        )

    def create_scene(self, *, slots: int, headless: bool) -> Any:
        if self.scene_factory is None:
            raise RuntimeError("scene creation remains owned by checkpoint_branches")
        return self.scene_factory(slots=slots, headless=headless)

    def identity(
        self,
        slot: int,
        *,
        checkpoint_digest: str,
        source_revision: str,
        execution_scope: str = "measured",
    ) -> BranchIdentity:
        pair_seed = int(self.scenario.get("seed", 0))
        pair_group_id = f"{self.base_instance_id}:warehouse_closeout:s{pair_seed}"
        return BranchIdentity(
            scene_key=self.scene_key,
            task_id=self.task_id,
            base_instance_id=self.base_instance_id,
            condition_id="warehouse_closeout",
            pair_seed=pair_seed,
            pair_group_id=pair_group_id,
            branch_id=f"{pair_group_id}:fact:slot-{slot:02d}",
            branch_type="fact",
            slot=slot,
            checkpoint_digest=checkpoint_digest,
            task_version=self.task_version,
            source_revision=source_revision,
            execution_scope=execution_scope,
        )

    @staticmethod
    def _root_rows(entity: Any, origins: Any) -> list[list[float]]:
        import torch

        values = torch.cat((
            entity.data.root_pos_w - origins,
            entity.data.root_quat_w,
            entity.data.root_lin_vel_w,
            entity.data.root_ang_vel_w,
        ), dim=1)
        return values.detach().cpu().tolist()

    def read_all_branch_states(self, runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
        env = runtime["env"]
        origins = runtime["origins"]
        articulations = {
            name: self._root_rows(entity, origins)
            for name, entity in env.scene.articulations.items()
        }
        rigid_objects = {
            name: self._root_rows(entity, origins)
            for name, entity in env.scene.rigid_objects.items()
        }
        return [
            {
                "articulations": {
                    name: rows[slot] for name, rows in articulations.items()
                },
                "rigid_objects": {
                    name: rows[slot] for name, rows in rigid_objects.items()
                },
            }
            for slot in range(len(self.targets))
        ]

    def read_branch_state(self, runtime: Mapping[str, Any], slot: int) -> Mapping[str, Any]:
        cached = runtime.get("post_step_states")
        if cached is not None:
            return cached[slot]
        return self.read_all_branch_states(runtime)[slot]

    @staticmethod
    def command_rows(actions: Mapping[str, Any]) -> list[dict[str, list[float]]]:
        matrices = {
            name: value.detach().cpu().tolist() for name, value in actions.items()
        }
        if not matrices:
            return []
        count = len(next(iter(matrices.values())))
        return [
            {name: rows[slot] for name, rows in matrices.items()}
            for slot in range(count)
        ]

    def apply_participant_actions(
        self, runtime: Mapping[str, Any], actions: Mapping[str, Any]
    ) -> None:
        runtime["actions"].update(actions)

    @staticmethod
    def step_simulation(runtime: Mapping[str, Any]) -> int:
        runtime["advance"]()
        return int(runtime["control_step"]())

    @staticmethod
    def capture_checkpoint(runtime: Mapping[str, Any]) -> Mapping[str, Any]:
        return runtime["checkpoint"].capture(dict(runtime["task_state"]()))

    @staticmethod
    def restore_checkpoint(
        runtime: Mapping[str, Any], snapshot: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        task_state, restore_error = runtime["checkpoint"].restore(dict(snapshot))
        return {
            "task_state": task_state,
            "restore_error": float(restore_error),
            "restore_error_source": "max_abs_public_physics_tensor_error",
        }

    @staticmethod
    def task_status(runtime: Mapping[str, Any], slot: int) -> Mapping[str, Any]:
        return dict(runtime["task_status"](slot))

    @staticmethod
    def scene_payload(runtime: Mapping[str, Any], slot: int) -> Mapping[str, Any]:
        return dict(runtime["scene_payload"](slot))


__all__ = ["WarehouseBranchDataAdapter"]
