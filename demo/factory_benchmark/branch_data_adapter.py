"""Factory adapter for the shared branch data tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from demo.benchmark.branch_data import BranchIdentity, digest_value


@dataclass
class FactoryBranchDataAdapter:
    scenario: Mapping[str, Any]
    scenario_path: Path
    targets: Sequence[tuple[float, float]]
    routes: Sequence[Sequence[tuple[float, float]]]

    scene_key = "factory"
    task_id = "F-INS-01"
    snapshot_schema = "factory.public_physics_task_rng.v1"

    def __post_init__(self) -> None:
        if self.scenario.get("scene_key") != self.scene_key:
            raise ValueError("Factory adapter requires scene_key=factory")
        if len(self.targets) != len(self.routes) or not self.targets:
            raise ValueError("Factory target and route counts differ")
        self.task_version = f"factory.task.v{int(self.scenario.get('version', 1))}"
        self.base_instance_id = (
            f"{self.task_id}:{digest_value(dict(self.scenario))[:16]}"
        )

    def identity(
        self,
        slot: int,
        *,
        checkpoint_digest: str,
        source_revision: str,
        execution_scope: str = "measured",
    ) -> BranchIdentity:
        pair_seed = int(self.scenario.get("seed", 43))
        pair_group_id = f"{self.base_instance_id}:inspection:s{pair_seed}"
        return BranchIdentity(
            scene_key=self.scene_key,
            task_id=self.task_id,
            base_instance_id=self.base_instance_id,
            condition_id="factory_route_confirmation",
            pair_seed=pair_seed,
            pair_group_id=pair_group_id,
            branch_id=f"{pair_group_id}:fact:slot-{slot:02d}",
            branch_type="fact",
            slot=int(slot),
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
        origins = runtime.get("origins", env.scene.env_origins)
        articulations = {
            name: self._root_rows(entity, origins)
            for name, entity in env.scene.articulations.items()
        }
        return [
            {"articulations": {
                name: rows[slot] for name, rows in articulations.items()
            }}
            for slot in range(len(self.targets))
        ]

    @staticmethod
    def command_rows(actions: Mapping[str, Any]) -> list[dict[str, Any]]:
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

    def scene_payload(
        self,
        slot: int,
        *,
        position_xy: Sequence[float],
        route_index: int,
        completed: bool,
    ) -> Mapping[str, Any]:
        return {
            "target_xy": list(self.targets[slot]),
            "position_xy": [float(value) for value in position_xy],
            "route_index": int(route_index),
            "route_waypoints": len(self.routes[slot]),
            "target_error_m": float(
                ((float(position_xy[0]) - self.targets[slot][0]) ** 2
                 + (float(position_xy[1]) - self.targets[slot][1]) ** 2) ** 0.5
            ),
            "completed": bool(completed),
        }


__all__ = ["FactoryBranchDataAdapter"]
