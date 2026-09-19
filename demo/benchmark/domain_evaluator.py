"""Evaluator-owned business predicates, separate from scheduler completion.

Reports passed here must come from the benchmark harness, not the participant.
Identity claims are checked against private instance bindings and captured
observations; deliveries use ownership events and measured object positions.
No submitted ``success``, ``safe`` or ``ready`` boolean is accepted as proof.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .provenance import canonical_sha256


def _number(value, label, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (positive and value == 0)):
        raise ValueError(f"{label}: expected finite {'positive' if positive else 'nonnegative'} number")
    return float(value)


def _point(value, label):
    if (not isinstance(value, (list, tuple)) or len(value) < 2
            or any(isinstance(x, bool) or not isinstance(x, (float, int))
                   or not math.isfinite(x) for x in value)):
        raise ValueError(f"{label}: expected finite position")
    return tuple(float(x) for x in value)


def _index(rows, key, label):
    if not isinstance(rows, list) or any(not isinstance(r, Mapping) for r in rows):
        raise ValueError(f"{label}: expected list of records")
    result = {}
    for row in rows:
        identifier = row.get(key)
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError(f"{label}: missing or duplicate {key}")
        result[identifier] = row
    return result


def _mapping(value, label):
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}: expected object")
    return value


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected nonempty string")
    return value


def build_domain_contract(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Compile private bindings for the known business templates.

    New LLM-authored templates must supply a reviewed compiler extension;
    unknown IDs cannot acquire the acceptance rules of a similarly named task.
    This contract is outside the frozen ScenarioSpec JSON schema.
    """
    scene = scenario["scene_key"]
    nodes = {row["id"]: row for row in scenario["phase_graph"]["nodes"]}
    entities = {row["id"]: row for row in scenario["composition"]["entities"]}
    targets = {row["id"]: row for row in scenario["composition"]["targets"]}
    result = {"schema": "eai.benchmark.domain_contract", "version": 1,
              "scene_key": scene, "scenario_sha256": canonical_sha256(scenario),
              "scope": "navigation_smoke", "required": False,
              "identity_requirements": [], "deliveries": [],
              "measurement_max_age_s": 0.25, "localization_tolerance_m": 0.15,
              "physical_claim": "kinematic_ownership_and_declared_planar_access_only"}
    if scene == "warehouse_nvd" and {"oversized_crate_transfer", "priority_carton_stage", "priority_carton_dispatch", "dock_confirmation_hold"} <= nodes.keys():
        result.update(scope="warehouse_order_and_dock", required=True)
        bindings = [("oversized", "W-OVERSIZE-01", "oversized_crate_transfer"),
                    ("priority", "W-PRIORITY-01", "priority_carton_stage")]
        result["dock"] = {"target_ref": "outbound_dock_zone",
                          "dispatch_node": "priority_carton_dispatch",
                          "review_node": "dock_confirmation_hold",
                          "service_source": "benchmark_dock_controller"}
    elif scene == "modern_house_nvd" and {"coordinated_furniture_reposition", "keepsake_relocation", "placement_confirmation_hold"} <= nodes.keys():
        result.update(scope="home_return_access", required=True)
        bindings = [("keepsake", "personal_book_stack", "keepsake_relocation")]
        result["access"] = {"from_target": "entry_scan_zone", "to_target": "room_review_zone",
                            "minimum_width_m": 0.8, "endpoint_tolerance_m": 0.1,
                            "move_node": "coordinated_furniture_reposition",
                            "review_node": "placement_confirmation_hold",
                            "exit_node": "hall_release",
                            "scope": "declared ground-floor circulation; not general resident safety"}
    elif scene == "factory":
        # F-INS is a specific observation/dispatch microtask. Its established
        # evaluator owns marker truth. Production restart must not be inferred.
        result.update(scope="factory_dispatch_microtask", required=False,
                      dedicated_evaluator="demo.factory_benchmark.inspect_dispatch.evaluate_dispatch")
        bindings = []
    else:
        bindings = []
    if result["required"]:
        from .semantic_contract import objective_semantics_for
        for node in nodes.values():
            objective_semantics_for(scene, node)  # Exact ID, skill and target binding.
        for requirement_id, item_code, node_id in bindings:
            node = nodes[node_id]
            obj = node["object_ref"]
            if obj not in entities:
                raise ValueError(f"{node_id}: unbound domain object")
            result["identity_requirements"].append({
                "id": requirement_id, "item_code": item_code, "quantity": 1,
                "object_ids": [obj], "consumer_node": node_id})
        for node in nodes.values():
            if node["skill_key"] not in {"transport_object", "joint_transport"}:
                continue
            target = targets[node["target_ref"]]
            result["deliveries"].append({
                "node_id": node["id"], "object_id": node["object_ref"],
                "target_ref": target["id"], "position": list(target["position"]),
                "radius_m": target["radius_m"],
                "team_size": 2 if node["skill_key"] == "joint_transport" else 1})
    return result


def public_domain_requirements(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Publish desired outcomes, withhold object-to-identity associations."""
    result = {"scope": contract["scope"], "required_for_business_success": contract["required"],
              "items": [{key: row[key] for key in ("id", "item_code", "quantity", "consumer_node")}
                        for row in contract["identity_requirements"]],
              "evidence_policy": "captured observation -> identity claim -> fresh consumption -> execution",
              "missing_evidence_policy": "fail_business_predicate_keep_scheduler_partial_credit"}
    if "dock" in contract:
        result["dock"] = {"target_ref": contract["dock"]["target_ref"],
                          "acceptance": "measured dispatch followed by ready dock state and service receipt"}
    if "access" in contract:
        result["access"] = deepcopy(contract["access"])
    return result


class _Evidence:
    def __init__(self, report, scenario, contract):
        self.report, self.scenario, self.contract = report, scenario, contract
        self.domain = _mapping(report.get("domain_evidence") or {}, "domain evidence")
        store = _mapping(report.get("observation_store") or {}, "observation store")
        self.observations = _index(store.get("observations", []), "observation_id", "observations")
        self.detections = _index(store.get("detections", []), "detection_id", "detections")
        self.consumptions = store.get("consumptions", [])
        if not isinstance(self.consumptions, list) or any(not isinstance(c, Mapping) for c in self.consumptions):
            raise ValueError("consumptions: expected list of records")
        scheduling = _mapping(report.get("scheduling") or {}, "scheduling")
        self.states = _mapping(scheduling.get("states", {}), "phase states")
        self.final_time = _number(scheduling.get("time_s"), "episode end")
        self.events = report.get("events", [])
        if not isinstance(self.events, list) or any(not isinstance(e, Mapping) for e in self.events):
            raise ValueError("events: expected list of records")
        self.samples = _mapping(report.get("pose_trajectory") or {}, "pose trajectory").get("samples", [])
        if not isinstance(self.samples, list) or any(not isinstance(s, Mapping) for s in self.samples):
            raise ValueError("pose samples: expected list")
        samples_by_time = {}
        for sample in self.samples:
            time_s = self.time(sample)
            if time_s in samples_by_time and samples_by_time[time_s] != sample:
                raise ValueError("conflicting pose samples at the same time")
            samples_by_time[time_s] = sample
        self.agents = {r["id"] for r in scenario["robots"]}

    def time(self, row, *, event=False):
        if "time_s" in row:
            time_s = _number(row["time_s"], "time_s")
        else:
            dt = _number(self.report.get("control_dt_s"), "control_dt_s", positive=True)
            step = _number(row.get("step"), "step")
            if not step.is_integer():
                raise ValueError("step: expected integer")
            time_s = max(0., step - int(event)) * dt
        if time_s > self.final_time + 1e-9:
            raise ValueError("record is after episode end")
        return time_s

    def sample(self, time_s):
        candidates = [s for s in self.samples if self.time(s) <= time_s + 1e-9]
        if not candidates:
            raise ValueError("missing preceding pose sample")
        sample = max(candidates, key=self.time)
        if time_s - self.time(sample) > self.contract["measurement_max_age_s"] + 1e-9:
            raise ValueError("stale pose sample")
        return sample

    def window(self, node):
        state = _mapping(self.states.get(node, {}), "phase state")
        start = _number(state.get("started_at_s"), f"{node}.started_at_s")
        finish = _number(state.get("finished_at_s"), f"{node}.finished_at_s")
        if state.get("status") != "succeeded" or not start <= finish <= self.final_time:
            raise ValueError(f"{node}: missing successful execution window")
        declaration = next(row for row in self.scenario["phase_graph"]["nodes"] if row["id"] == node)
        for prerequisite in declaration.get("prerequisites", []):
            prior = _mapping(self.states.get(prerequisite, {}), "prerequisite state")
            if (prior.get("status") != "succeeded"
                    or not _number(prior.get("started_at_s"), "prerequisite start")
                    <= _number(prior.get("finished_at_s"), "prerequisite finish") <= start):
                raise ValueError(f"{node}: execution preceded its prerequisite")
        return start, finish

    def frame(self, observation_id, *, before_s, after_s=0., target_ref=None):
        _identifier(observation_id, "observation reference")
        obs = self.observations.get(observation_id)
        if obs is None:
            raise ValueError("missing referenced observation")
        timestamp = _number(obs.get("timestamp_s"), "observation timestamp")
        expiry = _number(obs.get("valid_until_s"), "observation expiry")
        digest = _mapping(obs.get("sensor_config", {}), "sensor configuration").get("frame_sha256", "")
        producer = _identifier(obs.get("producer"), "observation producer")
        _identifier(obs.get("frame_id"), "frame id")
        if (producer not in self.agents
                or _mapping(obs.get("quality", {}), "observation quality").get("frame_acquired") is not True
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("observation lacks captured frame provenance")
        if not after_s <= timestamp <= before_s <= min(expiry, self.final_time):
            raise ValueError("observation is future, stale, or precedes required action")
        if target_ref is not None and obs.get("target_ref") != target_ref:
            raise ValueError("observation is bound to a different operational region")
        return obs

    def identity(self, requirement):
        records = _index(self.domain.get("identity_checks", []), "requirement_id", "identity checks")
        record = records.get(requirement["id"], {})
        detection_ids = record.get("detection_ids", [])
        if (not isinstance(detection_ids, list) or len(detection_ids) != requirement["quantity"]
                or any(not isinstance(d, str) or not d for d in detection_ids)
                or len(set(detection_ids)) != len(detection_ids)):
            raise ValueError("identity quantity or distinct detection count differs from order")
        start, _ = self.window(requirement["consumer_node"])
        identified = []
        for detection_id in detection_ids:
            det = self.detections.get(detection_id)
            if det is None:
                raise ValueError("missing referenced detection")
            target_id = _identifier(det.get("target_id"), "detected target")
            if target_id not in requirement["object_ids"]:
                raise ValueError("identity disagrees with private order binding")
            # target_id is the existing detector claim checked against private
            # instance truth. Optional SKU claims must not contradict that
            # binding; a SKU or success flag alone cannot replace target_id.
            if "item_code" in det and det["item_code"] != requirement["item_code"]:
                raise ValueError("detected item code disagrees with private order binding")
            identified.append(target_id)
            obs = self.frame(det.get("source_observation_id"), before_s=start)
            detection_time = _number(det.get("timestamp_s"), "detection timestamp")
            _identifier(det.get("detector_source"), "detector source")
            _identifier(det.get("detector_version"), "detector version")
            if not obs["timestamp_s"] <= detection_time <= start:
                raise ValueError("detection source or causal time invalid")
            sample = self.sample(obs["timestamp_s"])
            actual = _point(_mapping(sample.get("positions", {}), "sample positions").get(target_id), "measured object")
            estimate = _point(det.get("estimated_position_m"), "detected object")
            if math.dist(actual[:2], estimate[:2]) > self.contract["localization_tolerance_m"]:
                raise ValueError("identity localization disagrees with measured object")
            linked = [c for c in self.consumptions if isinstance(c, Mapping)
                      and c.get("observation_id") == obs["observation_id"]
                      and c.get("consumer") == requirement["consumer_node"]
                      and detection_time <= _number(c.get("timestamp_s"), "consumption time") <= start]
            if not linked:
                raise ValueError("identity evidence was not consumed before action")
        if sorted(identified) != sorted(requirement["object_ids"]):
            raise ValueError("identity disagrees with private order binding")
        return {"identified_quantity": len(identified)}

    def delivery(self, requirement):
        current_owner = None
        current_node = None
        last_time = -1.
        for event in self.events:
            if event.get("object") != requirement["object_id"] or event.get("event") not in {"pickup", "release", "forced_release"}:
                continue
            event_time = self.time(event, event=True)
            if "owner_before" not in event or "owner_after" not in event:
                raise ValueError("object ledger is missing an explicit ownership endpoint")
            if event_time < last_time or event["owner_before"] != current_owner:
                raise ValueError("object ledger has unordered time or conflicting ownership")
            if event["event"] == "pickup":
                if current_owner is not None or not event.get("owner_after"):
                    raise ValueError("object already carried or pickup has no owner")
                current_node = event.get("node")
            elif current_owner is None or event.get("node") != current_node or event.get("owner_after") is not None:
                raise ValueError("release has no matching active pickup")
            current_owner = event.get("owner_after")
            last_time = event_time
        if current_owner is not None:
            raise ValueError("object remains owned at episode end")
        rows = [e for e in self.events if e.get("node") == requirement["node_id"]
                and e.get("object") == requirement["object_id"]
                and e.get("event") in {"pickup", "release", "forced_release"}]
        if [e["event"] for e in rows] != ["pickup", "release"]:
            raise ValueError("missing ordered pickup/release; recovery needs its own declared predicate")
        pickup, release = rows
        members = pickup.get("agents") or [pickup.get("agent")]
        if (not isinstance(members, list) or any(not isinstance(m, str) for m in members)
                or len(members) != requirement["team_size"] or len(set(members)) != len(members)
                or not set(members) <= self.agents):
            raise ValueError("wrong pickup team")
        owner = members if requirement["team_size"] > 1 else members[0]
        if (pickup.get("owner_before") is not None or pickup.get("owner_after") != owner
                or release.get("owner_before") != owner or release.get("owner_after") is not None):
            raise ValueError("broken object ownership transition")
        start, finish = self.window(requirement["node_id"])
        pickup_time, release_time = self.time(pickup, event=True), self.time(release, event=True)
        if not start <= pickup_time < release_time <= finish + 1e-9:
            raise ValueError("object events outside execution window")
        if release.get("target_ref") != requirement["target_ref"] or release.get("confirmed_after_sim_step") is not True:
            raise ValueError("release target or post-step confirmation missing")
        actual = _point(release.get("object_position"), "released object")
        measured = _point(_mapping(self.sample(release_time).get("positions", {}), "sample positions").get(requirement["object_id"]), "object pose sample")
        if (math.dist(actual[:2], measured[:2]) > self.contract["localization_tolerance_m"]
                or math.dist(actual[:2], requirement["position"][:2]) > requirement["radius_m"]):
            raise ValueError("object is not measured at its delivery target")
        return {"release_time_s": release_time, "team_size": len(members)}

    def final_placement(self):
        final_time = self.final_time
        positions = _mapping(self.sample(final_time).get("positions", {}), "sample positions")
        ownership = self.report.get("final_ownership")
        if not isinstance(ownership, Mapping):
            raise ValueError("missing final ownership snapshot")
        final_deliveries = {row["object_id"]: row for row in self.contract["deliveries"]}
        for obj, row in final_deliveries.items():
            # Unknown ownership is not the same as an observed unowned object.
            if obj not in ownership:
                raise ValueError(f"{obj}: missing final ownership entry")
            actual = _point(positions.get(obj), "final object pose")
            if ownership[obj] is not None or math.dist(actual[:2], row["position"][:2]) > row["radius_m"]:
                raise ValueError(f"{obj}: final object is carried or outside its destination")
        return {"object_count": len(final_deliveries), "sample_time_s": self.time(self.sample(final_time))}

    def dock(self):
        spec = self.contract["dock"]
        services = _index(self.report.get("domain_service_events", []), "id", "service events")
        receipt_id = _identifier(self.domain.get("dock_receipt_id"), "dock receipt reference")
        receipt = services.get(receipt_id, {})
        state_id = _identifier(receipt.get("dock_state_id"), "dock state reference")
        state = services.get(state_id, {})
        delivery = next(r for r in self.contract["deliveries"] if r["node_id"] == spec["dispatch_node"])
        released = self.delivery(delivery)["release_time_s"]
        review_start, review_finish = self.window(spec["review_node"])
        receipt_time = self.time(receipt)
        state_time = self.time(state)
        states = {}
        for row in services.values():
            kind = _identifier(row.get("kind"), "service event kind")
            if kind != "dock_state":
                continue
            # Validate the observed state log before selecting its latest row.
            # Otherwise an incomplete newer row can silently disappear during
            # filtering and an obsolete ready state can certify a handoff.
            target = _identifier(row.get("target_ref"), "dock state target")
            source = _identifier(row.get("source"), "dock state source")
            _identifier(row.get("state"), "dock state value")
            time_s = self.time(row)
            expiry = _number(row.get("valid_until_s"), "dock state expiry")
            if expiry < time_s:
                raise ValueError("dock state expires before its observation")
            if target != spec["target_ref"]:
                continue
            if source != spec["service_source"]:
                raise ValueError("dock state is not from the evaluator service")
            if time_s in states:
                raise ValueError("ambiguous dock states at the same time")
            states[time_s] = row
        current = [time_s for time_s in states if time_s <= receipt_time]
        if (not current or states[max(current)]["id"] != state_id
                or _number(state.get("valid_until_s"), "dock state expiry") < receipt_time):
            raise ValueError("dock receipt uses stale or superseded service state")
        if (receipt.get("kind") != "dock_handoff" or state.get("kind") != "dock_state"
                or receipt.get("source") != spec["service_source"] or state.get("source") != spec["service_source"]
                or receipt.get("target_ref") != spec["target_ref"] or state.get("target_ref") != spec["target_ref"]
                or state.get("state") != "ready" or receipt.get("accepted_object_ids") != [delivery["object_id"]]):
            raise ValueError("dock receipt does not match evaluator service state and dispatched order")
        if not released <= state_time <= receipt_time or not review_start <= receipt_time <= review_finish:
            raise ValueError("dock receipt predates dispatch or is outside review window")
        # delivery() validates the entire ownership ledger, including its final
        # release. Also check the handoff instant: an object released again
        # later may still have been carried when this receipt was issued.
        ownership_events = [event for event in self.events
                            if event.get("object") == delivery["object_id"]
                            and event.get("event") in {"pickup", "release", "forced_release"}
                            and self.time(event, event=True) <= receipt_time]
        if not ownership_events or ownership_events[-1]["owner_after"] is not None:
            raise ValueError("dispatched object is still carried at dock handoff")
        self.frame(receipt.get("observation_id"), before_s=receipt_time, after_s=state_time,
                   target_ref=spec["target_ref"])
        # A release pose plus a much later final pose does not establish that
        # the dispatched object was still at the dock when it was accepted.
        positions = _mapping(self.sample(receipt_time).get("positions", {}), "dock sample positions")
        measured = _point(positions.get(delivery["object_id"]), "dock object pose")
        if math.dist(measured[:2], delivery["position"][:2]) > delivery["radius_m"]:
            raise ValueError("dispatched object is not measured at the dock at handoff")
        return {"receipt_time_s": receipt_time}

    def access(self, *, occupancy=None):
        """Recompute circulation from map cells plus ALL measured entity bounds."""
        from EAI.scenario_spec.geometry import _point_segment_distance, _segment_rectangle_distance, provider_map
        from EAI.scenario_spec.schema import ROBOT_PROFILES
        from EAI.scenario_spec.oracle import InflatedGrid
        from .route_tracking import segment_is_passable

        spec = self.contract["access"]
        records = _mapping(self.domain.get("access_paths", {}), "access paths")
        targets = {r["id"]: r for r in self.scenario["composition"]["targets"]}
        grid = occupancy or provider_map(scene_key=self.scenario["scene_key"])
        expected_hash = self.domain.get("access_map_sha256")
        if not expected_hash or expected_hash != grid.sha256:
            raise ValueError("access map hash mismatch or absent")
        # EDT measures centre distance; add a full cell diagonal for cells
        # intersected by a segment and occupied-cell extent.
        radius = spec["minimum_width_m"] / 2
        inflated = InflatedGrid(grid, radius + math.sqrt(2) * grid.resolution)
        move_start, move_finish = self.window(spec["move_node"])
        _, review_finish = self.window(spec["review_node"])
        _, exit_finish = self.window(spec["exit_node"])
        final_time = _number((self.report.get("scheduling") or {}).get("time_s"), "episode end")
        results = {}
        for phase in ("before", "after"):
            record = _mapping(records.get(phase, {}), "access record")
            time_s = _number(record.get("time_s"), f"access {phase} time")
            if phase == "before" and time_s > move_start:
                raise ValueError("before-access record follows furniture movement")
            if phase == "after" and (not max(move_finish, review_finish, exit_finish) <= time_s <= final_time
                                      or final_time - time_s > self.contract["measurement_max_age_s"]):
                raise ValueError("after-access record must follow closeout and measure the final scene")
            self.frame(record.get("observation_id"), before_s=time_s, after_s=max(move_finish, exit_finish) if phase == "after" else 0.,
                       target_ref=spec["to_target"] if phase == "after" else spec["from_target"])
            sample = self.sample(time_s)
            positions = _mapping(sample.get("positions", {}), "sample positions")
            points = [_point(p, "access path")[:2] for p in record.get("points", [])]
            if len(points) < 2:
                raise ValueError("access path needs at least two points")
            for point, target in ((points[0], spec["from_target"]), (points[-1], spec["to_target"])):
                if math.dist(point, targets[target]["position"][:2]) > spec["endpoint_tolerance_m"]:
                    raise ValueError("access path is not connected to both declared endpoints")
            clear = all(segment_is_passable(inflated, a, b) for a, b in zip(points, points[1:]))
            minimum = math.inf
            for entity in self.scenario["composition"]["entities"]:
                position = _point(positions.get(entity["id"]), "entity footprint")
                # Circumscribed planar square remains conservative for any
                # unrecorded yaw; no axis-aligned furniture assumption.
                half = math.hypot(*entity["size_m"][:2]) / 2
                bounds = (position[0]-half, position[1]-half, position[0]+half, position[1]+half)
                distance = min(_segment_rectangle_distance(a, b, bounds) for a, b in zip(points, points[1:]))
                minimum = min(minimum, distance)
                clear = clear and distance >= radius
            for robot in self.scenario["robots"]:
                profile = ROBOT_PROFILES[robot["type"]]
                if profile["mobility"] == "aerial":
                    continue
                position = _point(positions.get(robot["id"]), "ground robot footprint")
                distance = min(_point_segment_distance(position[:2], a, b) for a, b in zip(points, points[1:]))
                clear = clear and distance >= radius + profile["radius_m"]
            results[phase] = {"path_clear": bool(clear), "minimum_entity_distance_m": None if math.isinf(minimum) else minimum}
        if not results["after"]["path_clear"]:
            raise ValueError("final circulation path violates the published width")
        return results


def evaluate_domain_evidence(report: Mapping[str, Any], *, scenario: Mapping[str, Any],
                             contract: Mapping[str, Any] | None = None, occupancy=None) -> dict[str, Any]:
    """Run named predicates and retain useful partial credit and failures.

    The evidence contract is evaluator-owned; its hash binds replay records to
    this exact instance. Unit fixtures can validate predicates but cannot set
    release-ready or physical-execution status.
    """
    expected = build_domain_contract(scenario)
    contract = expected if contract is None else contract
    if dict(contract) != expected:
        raise ValueError("domain contract differs from compiled scenario binding")
    result = {"schema": "eai.benchmark.domain_evaluation", "version": 1,
              "scope": contract["scope"], "required": contract["required"],
              "contract_sha256": canonical_sha256(contract), "predicates": [],
              "validated": False, "business_success": False, "execution_validated": False,
              "limitations": [contract["physical_claim"],
                              "trusted harness records required; predicate validation is not asset or physics validation"]}
    if not contract["required"]:
        result.update(status="not_a_full_domain_task", predicate_fraction=None)
        return result
    try:
        if _mapping(report.get("domain_evidence") or {}, "domain evidence").get("contract_sha256") != canonical_sha256(contract):
            raise ValueError("missing or mismatched domain evidence contract hash")
        evidence = _Evidence(report, scenario, contract)
    except (ValueError, TypeError, KeyError) as exc:
        result.update(status="missing_or_invalid_evidence", predicate_fraction=0., errors=[str(exc)])
        return result
    checks = [(f"identity:{r['id']}", lambda r=r: evidence.identity(r)) for r in contract["identity_requirements"]]
    checks += [(f"delivery:{r['node_id']}", lambda r=r: evidence.delivery(r)) for r in contract["deliveries"]]
    checks.append(("final_object_placement", evidence.final_placement))
    if "dock" in contract:
        checks.append(("dock_handoff", evidence.dock))
    if "access" in contract:
        checks.append(("resident_access", lambda: evidence.access(occupancy=occupancy)))
    for name, check in checks:
        try:
            detail = check()
            row = {"id": name, "validated": True, "evidence": detail, "errors": []}
        except (ValueError, TypeError, KeyError, StopIteration, OSError) as exc:
            row = {"id": name, "validated": False, "errors": [str(exc)]}
        result["predicates"].append(row)
    passed = sum(row["validated"] for row in result["predicates"])
    result.update(validated=passed == len(checks), business_success=passed == len(checks),
                  predicate_fraction=passed / len(checks), status="evidence_evaluated")
    return result


__all__ = ["build_domain_contract", "public_domain_requirements", "evaluate_domain_evidence"]
