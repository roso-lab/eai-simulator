"""Scene-local integration configuration, without simulator imports."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from demo.city.usd_scene import CITY_ROOT


@dataclass(frozen=True)
class CityPreparationConfig:
    asset_root: Path = CITY_ROOT
    resolution_m: float = 0.25
    inflation_m: float = 0.45
    response_budget_s: float = 180.0
    max_active_agents: int = 3

    def __post_init__(self):
        for name in ("resolution_m", "inflation_m", "response_budget_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.resolution_m < 0.05:
            raise ValueError("resolution_m below 0.05 is outside the bounded authoring exporter")
        if type(self.max_active_agents) is not int or not 1 <= self.max_active_agents <= 3:
            raise ValueError("max_active_agents must be between 1 and 3")


def integration_manifest() -> dict:
    """A handoff declaration, not global registry activation or a solver."""
    return {
        "schema": "eai.city.integration_manifest", "version": 1,
        "scene_key": "city", "task_family": "urban_incident_response",
        "authoring_entrypoint": "demo.city_benchmark.prepare",
        "public_builder": "demo.city_benchmark.contracts.build_public_task",
        "public_validator": "demo.benchmark.goal_driven_contract.validate_goal_task_spec",
        "api_builder": "demo.benchmark.goal_driven_contract.build_goal_participant_api",
        "simulation_entrypoint": "demo.city.main",
        "session_owner": "simulator.open_simulator_session",
        "environment_hook": "demo.city.main.configure_city_env_cfg",
        "demo_selection_factory": "demo.city.main.build_selection",
        "response_people_factory": "demo.city_benchmark.response.build_response_people",
        "response_people_spawner": "demo.city_benchmark.response.spawn_response_people",
        "human_response_binding": "demo.city_benchmark.response.HumanEvacuationBinding",
        "response_services": "demo.city_benchmark.response.CityResponseServices",
        "trusted_service_backend": "demo.city_benchmark.service_backend.CityServiceBackend",
        "trusted_service_request_adapter": "demo.city_benchmark.service_backend.CityServiceRequestAdapter",
        "runtime_readback_adapter": "demo.city_benchmark.service_backend.CityRuntimeReadbackAdapter",
        "episode_evidence_host": "demo.city_benchmark.episode_adapter.CityEpisodeEvidenceHost",
        "following_traffic_runtime": "demo.city_benchmark.traffic.TrafficControlRuntime",
        "incident_report_runtime": "demo.city_benchmark.incident.IncidentReportRuntime",
        "participant_service_coverage": "demo.city_benchmark.contracts.participant_service_coverage",
        "selection_scaffold_scene_key": "modern_house_nvd",
        "scene_identity_rule": "city is the task identity; the builder scaffold is not the world asset",
        "global_registry_registered": False,
        "participant_execution_adapter": None,
        "release_status": "authoring_draft", "execution_validated": False, "scoreable": False,
        "runtime_policy": {
            "benchmark_loop": False, "response_clock": "separate_from_accident_visual_sequence",
            "incident_vehicle": "scripted_exogenous_event_not_a_stoppable_participant_target",
            "traffic_interception": "hold_or_divert_following_traffic_with_acknowledged_service",
            "people": "background_animation_does_not_prove_evacuation",
            "fire": "Flow_is_visual_not_a_temperature_or_toxicity_measurement",
        },
    }
