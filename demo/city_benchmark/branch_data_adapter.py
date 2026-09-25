"""City adapter for the shared branch data tool.

The City runner owns Isaac setup, the Carter articulation and the business
runtime.  This module only converts those scene-owned values into the common
branch identity and JSON-safe ledger payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from demo.benchmark.branch_data import BranchIdentity, digest_value


@dataclass
class CityBranchDataAdapter:
    scenario: Mapping[str, Any]
    scenario_path: Path
    env_data: Mapping[str, Any]
    branches: Sequence[Any]

    scene_key = "city"
    task_id = "CITY-RESPONSE-01"
    snapshot_schema = "eai.city.32_branch_checkpoint.v1"

    def __post_init__(self) -> None:
        if self.scenario_path is None:
            raise ValueError("City adapter requires scenario_path")
        if self.env_data.get("scene_key") != self.scene_key:
            raise ValueError("City adapter requires scene_key=city")
        if len(self.branches) < 1:
            raise ValueError("City adapter requires at least one branch")
        self.task_version = f"city.task.v{int(self.env_data.get('version', 1))}"
        instance_digest = digest_value({
            "scenario": dict(self.scenario),
            "env": dict(self.env_data),
        })
        self.base_instance_id = f"{self.task_id}:{instance_digest[:16]}"

    def identity(
        self,
        slot: int,
        *,
        checkpoint_digest: str,
        source_revision: str,
        execution_scope: str = "measured",
    ) -> BranchIdentity:
        pair_seed = 17
        pair_group_id = f"{self.base_instance_id}:fixture_response:s{pair_seed}"
        return BranchIdentity(
            scene_key=self.scene_key,
            task_id=self.task_id,
            base_instance_id=self.base_instance_id,
            condition_id="city_fixture_response",
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
        values = entity.data.root_pos_w - origins
        import torch

        packed = torch.cat((
            values,
            entity.data.root_quat_w,
            entity.data.root_lin_vel_w,
            entity.data.root_ang_vel_w,
        ), dim=1)
        return packed.detach().cpu().tolist()

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Mapping):
            return {
                str(key): CityBranchDataAdapter._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [CityBranchDataAdapter._json_safe(item) for item in value]
        if hasattr(value, "tolist"):
            try:
                return CityBranchDataAdapter._json_safe(value.tolist())
            except (TypeError, ValueError):
                pass
        if hasattr(value, "__dict__"):
            return {
                "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
                "fields": CityBranchDataAdapter._json_safe(vars(value)),
            }
        return repr(value)

    def read_all_branch_states(self, runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
        env = runtime["env"]
        origins = runtime.get("origins", env.scene.env_origins)
        articulations = {
            name: self._root_rows(entity, origins)
            for name, entity in env.scene.articulations.items()
        }
        return [
            {
                "articulations": {
                    name: rows[slot] for name, rows in articulations.items()
                },
                "business_state": self._json_safe(
                    self.branches[slot].snapshot()
                ),
            }
            for slot in range(len(self.branches))
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

    def phase_state(self, slot: int) -> Mapping[str, Any]:
        branch = self.branches[slot]
        return {
            "phase": branch.phase,
            "clock_s": float(branch.clock),
            "delay_s": float(branch.delay_s),
        }

    def task_state(self, slot: int) -> Mapping[str, Any]:
        branch = self.branches[slot]
        return {
            "phase": branch.phase,
            "people_complete": bool(branch.people_complete()),
            "traffic_complete": bool(
                branch.traffic_complete()
                if branch.phase in {
                    "traffic_requested", "traffic_acknowledged",
                    "secondary_traffic_protected", "report_submitted",
                    "response_access_preserved",
                }
                else False
            ),
            "report_received": branch.phase == "response_access_preserved",
        }

    def scene_payload(self, slot: int) -> Mapping[str, Any]:
        branch = self.branches[slot]
        return {
            "person_ids": list(branch.person_ids),
            "vehicle_ids": list(branch.vehicle_ids),
            "incident_ref": branch.incident_ref,
            "people_observation_ref": branch.people_observation_ref,
            "traffic_observation_ref": branch.traffic_observation_ref,
            "fixture_scope": "checked-in City pedestrian and vehicle routes",
        }


__all__ = ["CityBranchDataAdapter"]
