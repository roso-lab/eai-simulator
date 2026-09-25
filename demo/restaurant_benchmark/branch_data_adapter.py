"""Restaurant adapter for the shared Branch Data Tool.

This adapter intentionally exposes the bounded scene-lifecycle probe only.  It
does not infer Restaurant business success from a scene load or from a zero
command window; the runner records that boundary in ``scene_payload``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from demo.benchmark.branch_data import BranchIdentity, digest_value


@dataclass
class RestaurantBranchDataAdapter:
    """Project Restaurant articulation state into the common ledgers."""

    scenario: Mapping[str, Any]
    scenario_path: Path
    slots: int

    scene_key = "restaurant"
    task_id = "RESTAURANT-SCENE-LIFECYCLE-01"
    snapshot_schema = "restaurant.public_articulation_rng.v1"

    def __post_init__(self) -> None:
        if self.scenario.get("scene_key") != self.scene_key:
            raise ValueError("Restaurant adapter requires scene_key=restaurant")
        if isinstance(self.slots, bool) or not 1 <= int(self.slots) <= 32:
            raise ValueError("Restaurant slots must be between 1 and 32")
        self.slots = int(self.slots)
        self.task_version = "restaurant.scene_lifecycle.v1"
        self.base_instance_id = (
            f"{self.task_id}:{digest_value(dict(self.scenario))[:16]}"
        )
        self.targets: Sequence[tuple[float, float]] = tuple(
            (0.0, 0.0) for _ in range(self.slots)
        )

    def identity(
        self,
        slot: int,
        *,
        checkpoint_digest: str,
        source_revision: str,
        execution_scope: str = "measured",
    ) -> BranchIdentity:
        pair_seed = int(self.scenario.get("seed", 17))
        pair_group_id = f"{self.base_instance_id}:scene_lifecycle:s{pair_seed}"
        return BranchIdentity(
            scene_key=self.scene_key,
            task_id=self.task_id,
            base_instance_id=self.base_instance_id,
            condition_id="restaurant_scene_lifecycle",
            pair_seed=pair_seed,
            pair_group_id=pair_group_id,
            branch_id=f"{pair_group_id}:fact:slot-{int(slot):02d}",
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

        data = entity.data
        root_state = getattr(data, "root_state_w", None)
        position = getattr(data, "root_pos_w", None)
        quaternion = getattr(data, "root_quat_w", None)
        linear = getattr(data, "root_lin_vel_w", None)
        angular = getattr(data, "root_ang_vel_w", None)
        if root_state is not None:
            position = position if position is not None else root_state[:, :3]
            quaternion = quaternion if quaternion is not None else root_state[:, 3:7]
            linear = linear if linear is not None else root_state[:, 7:10]
            angular = angular if angular is not None else root_state[:, 10:13]
        if any(value is None for value in (position, quaternion, linear, angular)):
            raise AttributeError("Restaurant entity lacks root state fields")
        values = torch.cat((position - origins, quaternion, linear, angular), dim=1)
        return values.detach().cpu().tolist()

    def read_all_branch_states(self, runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
        env = runtime["env"]
        origins = runtime.get("origins", env.scene.env_origins)
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
            for slot in range(self.slots)
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

    def phase_state(self, *, simulator_step: int, phase: str) -> Mapping[str, Any]:
        return {
            "phase": str(phase),
            "simulator_step": int(simulator_step),
            "task_contract": "scene_lifecycle_only",
        }

    def task_state(self, *, completed: bool) -> Mapping[str, Any]:
        return {
            "probe_completed": bool(completed),
            "full_task_completed": False,
            "business_episode_validated": False,
            "formal_suite_registered": False,
        }

    def scene_payload(
        self,
        slot: int,
        *,
        state: Mapping[str, Any],
        phase: str,
        completed: bool,
    ) -> Mapping[str, Any]:
        articulation_names = sorted(state.get("articulations", {}))
        return {
            "phase": str(phase),
            "articulation_names": articulation_names,
            "rigid_object_names": sorted(state.get("rigid_objects", {})),
            "probe_completed": bool(completed),
            "task_contract": "scene_lifecycle_only",
            "business_scope": "not measured by this adapter",
        }


__all__ = ["RestaurantBranchDataAdapter"]
