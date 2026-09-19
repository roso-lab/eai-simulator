"""Concrete in-process City native service runner.

This runner is called from the process that already owns ``CityEaiRuntime``.
It reads and drives the runtime through ``CityDemoRuntimeWitnessAdapter`` and
keeps the episode host private.  It intentionally supports bounded partial
execution while reporting the missing dependencies for a complete service
receipt gate.
"""
from __future__ import annotations

from typing import Any, Mapping

from .episode_adapter import CityEpisodeEvidenceHost
from .live_witness import (
    CityDemoRuntimeWitnessAdapter,
    CityIncidentReceiver,
    CityLiveEpisodeWitness,
    CityRuntimeWitnessBlocked,
    LiveEvidence,
)


class CityNativeServiceRunner:
    """Own one episode's concrete City request/receipt trace."""

    def __init__(self, runtime: Any, *, episode_id: str, agent_id: str,
                 region_id: str, assembly_region_id: str, recipient_service_id: str,
                 affected_people: Mapping[str, Mapping[str, Any]] | None = None,
                 affected_person_roster=None, following_vehicle_roster=None,
                 person_controller=None, person_readback=None,
                 vehicle_controller: Any = None, admission: Any = None,
                 vehicle_pose_reader=None, hazard_readback=None, access_readback=None,
                 role_source_id: str | None = None, admission_source_id: str | None = None,
                 incident_receiver: CityIncidentReceiver | None = None,
                 scene_query_support: bool | None = None,
                 clearance_query_provenance: str | None = None,
                 physx_readback_suppressed: bool | None = None,
                 current_gpu_tensor_clearance: bool = False):
        self.runtime = runtime
        binding_factory = getattr(runtime, "city_service_native_binding", None)
        binding = binding_factory() if callable(binding_factory) else {}
        if binding and binding.get("episode_id") != episode_id:
            raise ValueError("native runner episode id does not match City runtime")
        affected_person_roster = affected_person_roster or binding.get("affected_person_roster")
        following_vehicle_roster = following_vehicle_roster or binding.get("following_vehicle_roster")
        person_controller = person_controller or binding.get("person_controller")
        person_readback = person_readback or binding.get("person_readback")
        vehicle_controller = vehicle_controller or binding.get("vehicle_controller")
        vehicle_pose_reader = vehicle_pose_reader or binding.get("vehicle_pose_reader")
        hazard_readback = hazard_readback or binding.get("hazard_readback")
        access_readback = access_readback or binding.get("access_readback")
        admission = admission or binding.get("admission")
        incident_receiver = incident_receiver or binding.get("incident_receiver")
        self.agent_id = str(agent_id)
        self.region_id = str(region_id)
        self.assembly_region_id = str(assembly_region_id)
        self.recipient_service_id = str(recipient_service_id)
        self.adapter = CityDemoRuntimeWitnessAdapter(
            runtime, episode_id=episode_id,
            source_id=binding.get("source_id", "isaac.city.runtime"),
            affected_people=affected_people,
            affected_person_roster=affected_person_roster,
            following_vehicle_roster=following_vehicle_roster,
            person_controller=person_controller, person_readback=person_readback,
            vehicle_controller=vehicle_controller, admission=admission,
            vehicle_pose_reader=vehicle_pose_reader, hazard_readback=hazard_readback,
            access_readback=access_readback,
            incident_receiver=incident_receiver,
            scene_query_support=scene_query_support,
            clearance_query_provenance=clearance_query_provenance,
            physx_readback_suppressed=physx_readback_suppressed,
            current_gpu_tensor_clearance=current_gpu_tensor_clearance)
        self.role_source_id = role_source_id
        self.admission_source_id = admission_source_id
        self.host = CityEpisodeEvidenceHost(
            admission=admission if callable(admission) else lambda *args: False,
            episode_id=episode_id, source_id=self.adapter.source_id,
            # Partial diagnostic runs keep the legacy pose/incident path
            # usable.  A complete live service run turns roster enforcement on
            # only when both episode-owned roster callbacks are present.
            require_episode_rosters=(callable(affected_person_roster)
                                     and callable(following_vehicle_roster)))
        self.witness = CityLiveEpisodeWitness(self.host, episode_id=episode_id)
        self.receiver = incident_receiver
        self._observation_ref: str | None = None

    def _bind_rosters(self) -> None:
        if (callable(self.adapter.affected_person_roster)
                and callable(self.adapter.following_vehicle_roster)):
            self.adapter.bind_episode_rosters(self.host)

    @property
    def readiness(self) -> dict[str, Any]:
        report = self.adapter.readiness()
        blockers = list(report["blockers"])
        if not isinstance(self.role_source_id, str) or not self.role_source_id.strip():
            blockers.append("missing_episode_owned_field_role_source")
        elif self.role_source_id.lower() in {"reference", "backend", "participant"}:
            blockers.append("field_role_source_is_not_live")
        if not isinstance(self.admission_source_id, str) or not self.admission_source_id.strip():
            blockers.append("missing_episode_owned_admission_source")
        elif self.admission_source_id.lower() in {"reference", "backend", "participant"}:
            blockers.append("admission_source_is_not_live")
        return {
            "schema": "eai.city.native_service_runner",
            "version": 1, "episode_id": self.adapter.episode_id,
            "status": "ready" if not blockers else "blocked",
            "real_witness_ready": not blockers, "execution_validated": False,
            "scoreable": False, "blockers": list(dict.fromkeys(blockers)),
            "scene_query_requirement": self.adapter.scene_query_requirement(),
        }

    def _advance_to_runtime(self) -> None:
        now = self.adapter.now_s()
        self.host.advance(now)

    def observe(self) -> str:
        """Capture actual people/vehicle poses into the episode host."""
        self._advance_to_runtime()
        self._bind_rosters()
        now = self.adapter.now_s()
        rows = self.adapter.observe_people() + self.adapter.observe_vehicles()
        if not rows:
            raise CityRuntimeWitnessBlocked("native City runtime has no observable people or vehicles")
        ref = f"city_native_observation_{int(now * 1000):09d}"
        row = {
            "id": ref, "episode_id": self.adapter.episode_id,
            "source_id": self.adapter.source_id, "producer_agent_id": "city_runtime",
            "region_id": self.region_id, "captured_at_s": now, "delivered_at_s": now,
            "valid_until_s": now + 1.0, "entities": rows,
        }
        entity_ids = tuple(str(item["id"]) for item in rows)
        evidence = LiveEvidence(
            episode_id=self.adapter.episode_id, evidence_ref=ref,
            kind="observation", source_id=self.adapter.source_id,
            entity_ids=entity_ids, observed_time_s=now,
            status_before={"status": "not_delivered"}, status_after={"status": "delivered"})
        self.witness.deliver_observation(row, evidence)
        self._observation_ref = ref
        return ref

    def record_role(self, role: str, *, start_time_s: float, end_time_s: float) -> None:
        """Record a measured field-role interval supplied by the City host."""
        self._advance_to_runtime()
        self.host.record_role_occupancy(
            self.agent_id, role, self.region_id,
            start_time_s=start_time_s, end_time_s=end_time_s)

    def guide_people(self, person_ids: tuple[str, ...], *, observation_ref: str | None = None) -> dict[str, Any]:
        ref = observation_ref or self._observation_ref
        if ref is None:
            raise CityRuntimeWitnessBlocked("native guidance requires an actual delivered observation")
        request, actions = self.adapter.submit_guidance(
            self.witness, agent_id=self.agent_id, region_id=self.region_id,
            assembly_region_id=self.assembly_region_id,
            person_ids=person_ids, observation_refs=(ref,))
        return {"request": request, "actions": list(actions), "scoreable": False}

    def control_following_traffic(self, vehicle_ids: tuple[str, ...], mode: str,
                                  *, observation_ref: str | None = None) -> dict[str, Any]:
        ref = observation_ref or self._observation_ref
        if ref is None:
            raise CityRuntimeWitnessBlocked("native traffic control requires an actual delivered observation")
        request, actions = self.adapter.submit_traffic(
            self.witness, agent_id=self.agent_id, region_id=self.region_id,
            vehicle_ids=vehicle_ids, mode=mode, observation_refs=(ref,))
        return {"request": request, "actions": list(actions), "scoreable": False}

    def bounded_people_trace(self, person_id: str, *, duration_s: float,
                             dt_s: float = 0.1) -> tuple[dict[str, Any], ...]:
        return self.adapter.bounded_person_trace(person_id, duration_s=duration_s, dt_s=dt_s)

    def record_person_arrival(self, request_id: str, person_id: str, *, previous_position,
                               assembly_region_id: str, arrived: bool,
                               safe_route_and_assembly: bool = False) -> dict[str, Any]:
        self._advance_to_runtime()
        row = self.adapter.capture_person_readback(
            person_id, previous_position=previous_position, status_before="guidance_applied",
            arrived=arrived, safe_route_and_assembly=safe_route_and_assembly)
        self.witness.record_person_readback(
            request_id, person_id, arrival_time_s=self.adapter.now_s(),
            assembly_region_id=assembly_region_id,
            trajectory_evidence=row["trajectory_evidence"], safety_evidence=row["safety_evidence"],
            measured_trajectory=row["measured_trajectory"],
            safe_route_and_assembly=row["safe_route_and_assembly"])
        return row

    def record_vehicle_readback(self, request_id: str, vehicle_id: str, *, previous_position,
                                status_before: str, no_hazard_ingress: bool = False,
                                rescue_access_preserved: bool = False) -> dict[str, Any]:
        self._advance_to_runtime()
        row = self.adapter.capture_vehicle_readback(
            vehicle_id, previous_position=previous_position, status_before=status_before,
            no_hazard_ingress=no_hazard_ingress,
            rescue_access_preserved=rescue_access_preserved)
        self.witness.record_traffic_readback(
            request_id, vehicle_id, trajectory_check_time_s=row["trajectory_check_time_s"],
            trajectory_evidence=row["trajectory_evidence"],
            hazard_evidence=row["hazard_evidence"], access_evidence=row["access_evidence"],
            measured_trajectory=row["measured_trajectory"],
            no_hazard_ingress=row["no_hazard_ingress"],
            rescue_access_preserved=row["rescue_access_preserved"])
        return row

    def report_incident(self, *, observation_ref: str | None = None) -> dict[str, Any]:
        ref = observation_ref or self._observation_ref
        if ref is None:
            raise CityRuntimeWitnessBlocked("native incident report requires an actual delivered observation")
        if self.receiver is None:
            raise CityRuntimeWitnessBlocked("native incident report has no independent receiver endpoint")
        request = self.witness.report_incident(
            agent_id=self.agent_id, region_id=self.region_id,
            observation_refs=(ref,), recipient_service_id=self.recipient_service_id)
        receipt = self.adapter.deliver_incident_receipt(self.witness, request)
        return {"request": request, "receipt": receipt, "scoreable": False}

    def record_continuous_access(self, request: Mapping[str, Any], *, start_time_s: float,
                                 end_time_s: float) -> dict[str, Any]:
        self._advance_to_runtime()
        return self.adapter.record_access_readback(
            self.witness, request, start_time_s=start_time_s, end_time_s=end_time_s)

    def review(self, request_id: str) -> dict[str, Any]:
        return self.witness.review(request_id)


__all__ = ["CityNativeServiceRunner"]
