"""Explicit control boundary for traffic behind the scripted accident.

The incident vehicle remains exogenous.  This runtime only models vehicles
that approach the affected area afterwards, and it separates a submitted
control request from an acknowledged service action.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Sequence

from .response import _Polyline, _point


@dataclass(frozen=True)
class FollowingTrafficSpec:
    vehicle_id: str
    route: tuple[tuple[float, float, float], ...]
    speed_mps: float = 5.0
    following_incident: bool = True
    diversion_route: tuple[tuple[float, float, float], ...] | None = None

    def __post_init__(self) -> None:
        if not self.vehicle_id or self.speed_mps <= 0 or not math.isfinite(self.speed_mps):
            raise ValueError("traffic vehicle id and speed must be valid")
        _Polyline(self.route)
        if self.diversion_route is not None:
            _Polyline(self.diversion_route)


@dataclass
class FollowingTrafficState:
    spec: FollowingTrafficSpec
    position_xyz: tuple[float, float, float]
    distance_m: float = 0.0
    mode: str = "normal"
    acknowledged_request_id: str | None = None
    acknowledged_time_s: float | None = None
    trajectory_samples_after_ack: int = 0


class TrafficControlRuntime:
    """A small service model that requires acknowledgement before control."""

    VALID_MODES = frozenset({"hold", "divert", "release"})

    def __init__(self, vehicles: Iterable[FollowingTrafficSpec], *, response_window_s: float = 1.0):
        specs = tuple(vehicles)
        ids = [spec.vehicle_id for spec in specs]
        if len(ids) != len(set(ids)):
            raise ValueError("following traffic ids must be unique")
        self._routes = {spec.vehicle_id: _Polyline(spec.route) for spec in specs}
        self._diversions = {
            spec.vehicle_id: _Polyline(spec.diversion_route)
            for spec in specs if spec.diversion_route is not None
        }
        self._states = {
            spec.vehicle_id: FollowingTrafficState(spec, _point(spec.route[0]))
            for spec in specs
        }
        self.time_s = 0.0
        if not math.isfinite(float(response_window_s)) or float(response_window_s) <= 0:
            raise ValueError("response_window_s must be finite and positive")
        self.response_window_s = float(response_window_s)
        self._observation_counter = 0
        self._observations: dict[str, tuple[str, ...]] = {}
        self._request_counter = 0
        self._requests: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []

    def observe(self) -> list[dict[str, Any]]:
        self._observation_counter += 1
        observation_ref = f"traffic_observation_{self._observation_counter:03d}"
        vehicle_ids = tuple(sorted(self._states))
        self._observations[observation_ref] = vehicle_ids
        rows = [{"vehicle_id": state.spec.vehicle_id, "position_xyz": list(state.position_xyz),
                 "mode": state.mode, "following_incident": state.spec.following_incident}
                for state in (self._states[vehicle_id] for vehicle_id in vehicle_ids)]
        for row in rows:
            row["observation_ref"] = observation_ref
        self.events.append({"type": "traffic_observation", "observation_ref": observation_ref,
                            "vehicle_ids": list(vehicle_ids), "time_s": self.time_s})
        return rows

    def request(self, vehicle_ids: Iterable[str], mode: str, *,
                observation_refs: Iterable[str] | None = None) -> dict[str, Any]:
        mode = str(mode)
        if mode not in self.VALID_MODES:
            raise ValueError(f"unsupported following traffic mode: {mode}")
        selected = tuple(str(vehicle_id) for vehicle_id in vehicle_ids)
        if not selected:
            raise ValueError("traffic control requires at least one vehicle")
        refs = tuple(str(ref) for ref in (observation_refs or ()))
        if not refs or any(ref not in self._observations for ref in refs):
            raise ValueError("traffic control requires delivered observation references")
        observed_ids = {vehicle_id for ref in refs for vehicle_id in self._observations[ref]}
        if not set(selected) <= observed_ids:
            raise ValueError("traffic control may reference only observed vehicles")
        for vehicle_id in selected:
            if vehicle_id not in self._states:
                raise KeyError(f"unknown observed traffic vehicle: {vehicle_id}")
            if not self._states[vehicle_id].spec.following_incident:
                raise ValueError(f"vehicle '{vehicle_id}' is not following incident traffic")
        if mode == "divert" and any(vehicle_id not in self._diversions for vehicle_id in selected):
            raise ValueError("divert request requires an admitted diversion route")
        self._request_counter += 1
        request_id = f"traffic_request_{self._request_counter:03d}"
        request = {"request_id": request_id, "vehicle_ids": list(selected), "mode": mode,
                   "requested_time_s": self.time_s, "status": "pending_acknowledgement"}
        self._requests[request_id] = request
        self.events.append({"type": "traffic_control_requested", **request})
        return dict(request)

    def acknowledge(self, request_id: str, *, now_s: float | None = None) -> dict[str, Any]:
        try:
            request = self._requests[str(request_id)]
        except KeyError as exc:
            raise KeyError(f"unknown traffic request: {request_id}") from exc
        if request["status"] != "pending_acknowledgement":
            raise ValueError(f"traffic request '{request_id}' is already resolved")
        timestamp = self.time_s if now_s is None else float(now_s)
        if timestamp < self.time_s:
            raise ValueError("traffic acknowledgement time cannot move backwards")
        request["status"] = "acknowledged"
        request["acknowledged_time_s"] = timestamp
        for vehicle_id in request["vehicle_ids"]:
            state = self._states[vehicle_id]
            state.mode = request["mode"]
            state.acknowledged_request_id = request["request_id"]
            state.acknowledged_time_s = timestamp
            state.trajectory_samples_after_ack = 0
            if state.mode == "divert":
                state.distance_m = 0.0
                state.position_xyz = self._diversions[vehicle_id].sample(0.0)
        self.time_s = timestamp
        event = {"type": "traffic_control_acknowledged", **request}
        self.events.append(event)
        return dict(request)

    def step(self, dt: float) -> None:
        dt = float(dt)
        if not math.isfinite(dt) or dt < 0:
            raise ValueError("dt must be non-negative and finite")
        self.time_s += dt
        for state in self._states.values():
            if (state.acknowledged_time_s is not None
                    and self.time_s > state.acknowledged_time_s):
                state.trajectory_samples_after_ack += 1
            if state.mode == "hold":
                continue
            route = self._diversions[state.spec.vehicle_id] if state.mode == "divert" else self._routes[state.spec.vehicle_id]
            state.distance_m = min(route.total, state.distance_m + state.spec.speed_mps * dt)
            state.position_xyz = route.sample(state.distance_m)

    def evidence(self, vehicle_ids: Iterable[str] | None = None) -> dict[str, Any]:
        selected = tuple(sorted(self._states if vehicle_ids is None else (str(v) for v in vehicle_ids)))
        states = [self._states[vehicle_id] for vehicle_id in selected]
        return {
            "observed_vehicle_ids": list(selected),
            "acknowledged_vehicle_ids": [state.spec.vehicle_id for state in states
                                          if state.acknowledged_request_id is not None],
            "protected_vehicle_ids": [state.spec.vehicle_id for state in states
                                       if state.mode in {"hold", "divert"}
                                       and state.trajectory_samples_after_ack > 0
                                       and state.acknowledged_time_s is not None
                                       and self.time_s - state.acknowledged_time_s >= self.response_window_s],
            "all_selected_control_acknowledged": all(state.acknowledged_request_id is not None for state in states),
            "control_modes": {state.spec.vehicle_id: state.mode for state in states},
            "trajectory_sampled_vehicle_ids": [state.spec.vehicle_id for state in states
                                                if state.trajectory_samples_after_ack > 0],
            "response_window_s": self.response_window_s,
        }


def build_following_traffic(semantic_map: dict[str, Any], *, count: int = 3) -> list[FollowingTrafficSpec]:
    routes = tuple(semantic_map.get("vehicle_routes", ()))
    if count <= 0 or count > len(routes):
        raise ValueError("following traffic count must fit authored vehicle routes")
    return [FollowingTrafficSpec(
        vehicle_id=f"following_vehicle_{index:02d}",
        route=tuple(_point(point) for point in route["points"]),
    ) for index, route in enumerate(routes[:count])]


__all__ = ["FollowingTrafficSpec", "TrafficControlRuntime", "build_following_traffic"]
