"""Offline evidence for a preregistered Restaurant positive-domain certificate.

Calibration acceptance is not an episode truth or a release qualification. The
issuer consumes independently pinned reviews from the hosts that checked the
raw archives. Review hashes are an external trust input, not self-authentication.
Neither this module nor the certificate is exposed to a participant.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from .provenance import canonical_sha256


MANIFEST_SCHEMA = "eai.restaurant.initial_calibration"
REVIEW_SCHEMA = "eai.restaurant.initial_calibration_review"
FLAGS = {"scoreable": False, "execution_validated": False,
         "business_outcomes_validated": False, "release_ready": False}
WINDOWS = {"ground_navigation": (0, 60), "observe": (61, 71), "relay": (72, 82)}
CARTERS = {"restaurant_carter_1", "restaurant_carter_2"}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_calibration_manifest(manifest, *, expected_manifest_sha256):
    """Validate the fixed domain and slots, without certifying any trial."""
    from .goal_restaurant_initial_oracle import (
        build_restaurant_initial_coexistence_domain, _build_registration,
        validate_restaurant_initial_positive_certificate,
    )

    _require(canonical_sha256(manifest) == expected_manifest_sha256,
             "calibration manifest differs from its independent pin")
    _require(manifest.get("schema") == MANIFEST_SCHEMA
             and type(manifest.get("version")) is int and manifest["version"] == 2,
             "unsupported calibration manifest")
    _require(manifest["run"] == {
        "device": "cpu", "warmup_ticks": 240, "max_ticks": 4200,
        "timeout_s": 900, "native_retries": 0}, "unsupported bounded run protocol")
    template = manifest["certificate_template"]
    _require(type(template.get("version")) is int and template["version"] == 2,
             "calibration requires an explicit v2 certificate template")
    _require(template["coexistence_domain"] == build_restaurant_initial_coexistence_domain(),
             "coexistence domain differs from maintained deployment")
    _require(set(template["initial_domain"]["root_domains"]) == CARTERS,
             "action domain must name exactly the two constructor actors")
    qualification = template["qualification"]
    _require(qualification["scope"] == "native_empirical_domain"
             and qualification["calibration_trials"] == []
             and qualification["evidence_refs"] == []
             and qualification["calibration_manifest_sha256"] == ""
             and template["registration_sha256"] == "",
             "template cannot contain retrospective qualification")
    profiles = manifest["runtime_profiles"]
    authorities = manifest["authorities_by_host"]
    _require(set(profiles) == set(authorities) and bool(profiles), "host authority coverage mismatch")
    _require(sorted(qualification["allowed_runtime_profile_sha256"])
             == sorted({canonical_sha256(value) for value in profiles.values()}),
             "allowed runtime profiles differ from preregistration")
    slots = set()
    episode_ids = set()
    for trial in manifest["trials"]:
        _require(set(trial) == {"host", "seed", "episode_id", "output"}, "invalid trial fields")
        host, seed = trial["host"], trial["seed"]
        _require(host in profiles and type(seed) is int, "unknown trial host or seed")
        _require((host, seed) not in slots and trial["episode_id"] not in episode_ids,
                 "duplicate calibration slot or episode")
        _require(isinstance(trial["episode_id"], str) and bool(trial["episode_id"])
                 and isinstance(trial["output"], str) and bool(trial["output"]), "empty trial identity")
        slots.add((host, seed))
        episode_ids.add(trial["episode_id"])
    _require({host for host, _seed in slots} == set(profiles), "not every host has a fixed trial")
    for host, authority in authorities.items():
        identity = template["identities"]
        _require(authority["evidence_scope"] == "native_empirical_domain"
                 and authority["runtime_profile_sha256"] == canonical_sha256(profiles[host]),
                 "runtime authority mismatch")
        for field in ("asset_manifest_sha256", "controller_source_sha256", "task_condition_binding_sha256"):
            _require(authority[field] == identity[field], f"{field} authority mismatch")
        _require(canonical_sha256(authority["execution_dependency_manifest"])
                 == identity["execution_dependency_manifest_sha256"], "execution source identity mismatch")
        _require(list(authority["execution_dependency_manifest"]) == template["execution_dependency_paths"],
                 "execution source path coverage mismatch")
    # Exercise the complete versioned schema before consuming a native slot.
    # These placeholders validate structure only and never leave this function.
    structural_probe = deepcopy(template)
    placeholder_trials = [dict(host=row["host"], seed=row["seed"],
        raw_archive_sha256=canonical_sha256({"structural_placeholder": row}),
        runtime_profile_sha256=canonical_sha256(profiles[row["host"]])) for row in manifest["trials"]]
    structural_probe["qualification"].update(
        calibration_manifest_sha256=expected_manifest_sha256,
        calibration_trials=placeholder_trials,
        evidence_refs=["native:calibration:" + row["raw_archive_sha256"] for row in placeholder_trials])
    structural_probe["registration_sha256"] = _build_registration(structural_probe)["registration_sha256"]
    validate_restaurant_initial_positive_certificate(
        structural_probe, expected_certificate_sha256=canonical_sha256(structural_probe))
    return deepcopy(manifest)


def _check_root_domain(state, template):
    """Measured coexistence and action-domain checks; no feasibility label."""
    from .goal_restaurant_initial_oracle import validate_restaurant_initial_coexistence

    validate_restaurant_initial_coexistence(state, template["coexistence_domain"])
    roots = state["native_initial_roots"]
    for agent, domain in template["initial_domain"]["root_domains"].items():
        values = roots[agent]["root_state_w"]
        _require(len(values) == len(domain["center"]) == len(domain["abs_tolerance"]) == 13,
                 "invalid action root dimensions")
        _require(all(math.isfinite(float(value)) and abs(value - center) <= tolerance
                     for value, center, tolerance in zip(values, domain["center"], domain["abs_tolerance"])),
                 f"action root outside frozen domain: {agent}")


def _check_coexistence_trajectory(pump, template):
    bindings = template["coexistence_domain"]["spawn_region_bindings"]
    idle = set(bindings) - CARTERS
    records = pump["region_records"]
    _require(bool(records), "missing measured coexistence trajectory")
    by_ref = {}
    for row in records:
        reference = "sha256:" + canonical_sha256({key: value for key, value in row.items() if key != "evidence_ref"})
        _require(row["evidence_ref"] == reference, "coexistence evidence digest mismatch")
        by_ref[reference] = row
    ticks = pump["ticks"]
    _require(bool(ticks), "missing native ticks")
    previous = 0.0
    for index, tick in enumerate(ticks):
        _require(tick["index"] == index and tick["start_time_s"] == previous
                 and tick["time_s"] > previous and tick["fleet_tick_advanced"] is True,
                 "native tick coverage has a gap")
        row = by_ref.get(tick["region_record_ref"])
        _require(row is not None and row["time_s"] == tick["time_s"]
                 and row["physics_step"] == tick["fleet_tick_physics_step"],
                 "coexistence sample is not from the same native tick")
        _require(set(row["agents"]) == set(bindings), "incomplete full8 coexistence sample")
        for agent in idle:
            vector = row["agents"][agent]["root_state_w"]
            binding = bindings[agent]
            center = binding["center_m"]
            _require(len(vector) == 13 and all(type(x) in (int, float) and math.isfinite(x) for x in vector),
                     "invalid coexistence root")
            _require(math.dist(vector[:len(center)], center) <= binding["tolerance_m"],
                     f"idle actor outside maintained region: {agent}")
        previous = tick["time_s"]
    return {"tick_count": len(ticks), "last_time_s": previous,
            "idle_agents": sorted(idle), "region_sample_count": len(records)}


def _check_construction(pump):
    """Bind the hidden calibration construction, never participant admission."""
    expected = {}
    for index, region in enumerate(("kitchen_service", "dining_service")):
        agent = f"restaurant_carter_{index + 1}"
        for skill, task_id, args, prerequisites, start, duration in (
            ("ground_navigation", f"nav-{index}",
             {"region_id": region, "route_ids": [f"restaurant_spawn_carter_{index + 1}_to_{region}"]}, [], 0., 60.),
            ("observe", f"observe-{index}",
             {"region_id": region, "subject_ref": region, "observation_mode": "service_ground_readiness"},
             [f"nav-{index}"], 61., 10.),
        ):
            expected[task_id] = dict(task_id=task_id, skill=skill, arguments=args,
                prerequisites=prerequisites, assigned_agents=[agent], required_capabilities=[skill],
                evidence_refs=[], execution={"start_s": start, "duration_s": duration})
    expected["relay"] = dict(task_id="relay", skill="relay",
        arguments={"message_kind": "restaurant_readiness_receipt", "region_id": "kitchen_service"},
        prerequisites=["observe-0", "observe-1"], assigned_agents=["restaurant_carter_1"],
        required_capabilities=["relay"], evidence_refs=[], execution={"start_s": 72., "duration_s": 10.})
    tasks = pump["tasks"]
    _require(set(tasks) == set(expected), "constructor task coverage mismatch")
    nav = []
    for task_id, spec in expected.items():
        task = tasks[task_id]
        _require(task["task"] == spec, f"constructor task differs: {task_id}")
        low, high = WINDOWS[spec["skill"]]
        _require(low <= task["physical_started_at_s"] <= task["physical_terminal_at_s"] < high
                 and task["terminal_delivery"]["status"] == "succeeded"
                 and task["physical_terminal_at_s"] <= task["terminal_delivery"]["result_delivered_at_s"] < high,
                 "action or receipt outside fixed window")
        if spec["skill"] == "ground_navigation":
            nav.append((task["physical_started_at_s"], task["physical_terminal_at_s"]))
    overlap = min(end for _, end in nav) - max(start for start, _ in nav)
    _require(overlap > 0, "constructor navigation did not overlap")
    visible = pump["visible_readiness_observations"]
    _require(len(visible) == 2 and {row["task_id"] for row in visible} == {"observe-0", "observe-1"},
             "constructor visible observation coverage mismatch")
    service = pump["readiness"]["readiness_service"]
    observation_ids = []
    for row in visible:
        spec = expected[row["task_id"]]
        terminal = tasks[row["task_id"]]["terminal_delivery"]
        observation_id = row["service_observation_id"]
        measured = service["observations"][observation_id]
        _require(row["producer_id"] == measured["producer_id"] == spec["assigned_agents"][0]
                 and row["region_id"] == measured["region_id"] == spec["arguments"]["region_id"]
                 and row["result_message_id"] == terminal["message_id"]
                 and row["result_delivered_at_s"] == terminal["result_delivered_at_s"],
                 "constructor observation delivery binding mismatch")
        observation_ids.append(observation_id)
    _require(len(set(observation_ids)) == 2, "constructor observations must be distinct")
    publications = service["relay_publications"]
    receipt = service["receipt"]
    _require(len(publications) == 1
             and sorted(publications[0]["observation_refs"]) == sorted(observation_ids)
             and sorted(receipt["observation_refs"]) == sorted(observation_ids)
             and receipt["relay_message_id"] == publications[0]["message_id"],
             "constructor relay does not bind both delivered observations")
    _require(all(row["result_delivered_at_s"] <= publications[0]["time_s"] < row["valid_until_s"] for row in visible)
             and 72 <= publications[0]["time_s"] <= receipt["time_s"] < 82,
             "constructor relay observation freshness mismatch")
    return overlap


def _check_bounded_completion(report, pump, run):
    _require(report["all_plan_tasks_terminal"] is True
             and report["bounded_tick_limit_reached"] is False
             and report["trusted_episode_horizon_termination"] is False
             and 0 < len(pump["ticks"]) <= run["max_ticks"],
             "construction did not complete within the bounded run")


def verify_calibration_trial(manifest, *, expected_manifest_sha256, host, seed):
    """Recompute one owning-host archive; never rewrite its initial truth."""
    from .communication import validate_communication_report
    from .goal_session_audit import replay_goal_session_audit
    from .goal_restaurant_business_facts import build_restaurant_business_fact_archive
    from .goal_restaurant_publication_treatment import verify_restaurant_publication_treatment
    from .goal_driven_tasks import build_goal_task_package

    result = {"schema": REVIEW_SCHEMA, "version": 2, "visibility": "evaluator_only",
              "manifest_sha256": expected_manifest_sha256, "host": host, "seed": seed,
              "accepted": False, "failure": None, **FLAGS}
    try:
        validate_calibration_manifest(manifest, expected_manifest_sha256=expected_manifest_sha256)
        trial = next(row for row in manifest["trials"] if row["host"] == host and row["seed"] == seed)
        root = Path(trial["output"])
        load = lambda name: _load(root / name)
        process = _load(root.with_suffix(".process.json"))
        _require(process["status"] == "process_finished" and process["returncode"] == 0,
                 "native process did not finish successfully")
        _require(process["trial"] == trial and process["manifest_sha256"] == expected_manifest_sha256,
                 "native process was not bound to the fixed slot")
        request, report, context = load("request.json"), load("report.json"), load("context.json")
        _require(request["episode_id"] == trial["episode_id"] and request["seed"] == seed,
                 "actual trial identity mismatch")
        for key in ("device", "warmup_ticks", "max_ticks"):
            _require(request[key] == manifest["run"][key], f"requested {key} mismatch")
        _require(request["communication_contract"] == manifest["communication_contract"]
                 and request.get("publication_treatment_extension") == "shared_fifo_anchored_window_v1",
                 "communication condition mismatch")
        _require("initial_oracle_certificate" not in request, "calibration cannot start with a certificate")
        _require(load("runtime-configuration.json") == {
            "requested_seed": seed, "effective_seed": seed,
            "requested_device": "cpu", "effective_device": "cpu"}, "applied runtime differs")
        inputs = load("initial-oracle-inputs.json")
        _require(inputs["authorities"] == manifest["authorities_by_host"][host]
                 and inputs["runtime_profile"] == manifest["runtime_profiles"][host]
                 and canonical_sha256(inputs["asset_manifest"]) == inputs["authorities"]["asset_manifest_sha256"],
                 "measured source/asset/runtime authority mismatch")
        _require(report["source_drift"] == [] and report["error"] is None
                 and report["status"] == "diagnostic_finished" and report["time_s"] < 82,
                 "native run incomplete or source drifted")
        _require(report["oracle_status"] == "failed" and all(report[key] is False for key in FLAGS),
                 "calibration rewrote initial truth or release status")
        _require(len(report["task_statuses"]) == 5 and set(report["task_statuses"].values()) == {"succeeded"},
                 "bounded construction did not complete")
        for path, digest in request["source_sha256"].items():
            _require(_sha(path) == digest, "live execution source differs from archived request")
        from .goal_restaurant_oracle_inputs import normalized_execution_sources
        from .goal_restaurant_fleet import restaurant_fleet_source_manifest

        normalized, _controllers = normalized_execution_sources(
            request["source_sha256"], restaurant_fleet_source_manifest())
        _require(normalized == inputs["authorities"]["execution_dependency_manifest"],
                 "request source coverage differs from measured execution authority")
        template = manifest["certificate_template"]
        _require(canonical_sha256(request["public_task"]) == template["identities"]["public_task_sha256"]
                 and canonical_sha256(request["deployment"]) == template["identities"]["deployment_sha256"],
                 "task or deployment identity mismatch")
        state = context["initial_state"]
        _require(state["platform_gaps"] == [] and state["time_s"] == 0.0, "invalid initial state")
        _check_root_domain(state, template)
        session, journal, communication = load("session.json"), load("audit.json"), load("communication.json")
        audit_review = replay_goal_session_audit(journal, request["public_task"], request["participant_api"])
        comm_review = validate_communication_report(communication, expected_initial_contract=manifest["communication_contract"])
        _require(audit_review["validated"] is True and audit_review == load("audit-review.json")
                 and comm_review["validated"] is True and comm_review == load("communication-review.json"),
                 "archive replay did not validate")
        pump = load("pump.json")
        _require(pump["platform_gaps"] == [], "execution pump platform gaps")
        _check_bounded_completion(report, pump, manifest["run"])
        trajectory = _check_coexistence_trajectory(pump, template)
        _require(trajectory["last_time_s"] == report["time_s"], "trajectory does not reach final time")
        navigation_overlap = _check_construction(pump)
        treatment = verify_restaurant_publication_treatment(
            pump_report=pump, public_task=request["public_task"], participant_api=request["participant_api"],
            hidden_evaluator=build_goal_task_package("restaurant")["hidden_evaluator"],
            communication_contract=manifest["communication_contract"], deployment_companion=request["deployment"])
        _require(treatment == load("publication-treatment-review.json")
                 and treatment["trigger_status"] == "not_triggered"
                 and treatment["application_status"] == "not_applied", "treatment condition outside calibration")
        archive = build_restaurant_business_fact_archive(request["public_task"], episode_id=trial["episode_id"],
            verifier_inputs=dict(public_task=request["public_task"], deployment_companion=request["deployment"],
                episode_metadata=load("episode-metadata.json"), context_report=context, session_report=session,
                audit_journal=journal, audit_replay=audit_review, pump_report=pump,
                communication_report=communication, communication_replay=comm_review,
                archived_source_sha256=request["source_sha256"], expected_source_sha256=request["source_sha256"]))
        _require(archive == load("scoring/restaurant_business_facts.json"), "business archive differs from recomputation")
        facts = archive["facts"]["physical_milestone_facts"]
        _require(len(facts) == 3 and all(row["satisfied_at_measurement"]
                 and row["availability_status"] == "delivered" and row["available_at_s"] < 82 for row in facts),
                 "physical business construction incomplete")
        raw = {str(path.relative_to(root)): _sha(path) for path in sorted(root.rglob("*.json"))}
        raw["external_native_epoch"] = _sha(state["source_epoch_archive"]["path"])
        raw["process_record"] = _sha(root.with_suffix(".process.json"))
        result.update(accepted=True, episode_id=trial["episode_id"], raw_files=raw,
                      raw_archive_sha256=canonical_sha256(raw), runtime_profile_sha256=inputs["authorities"]["runtime_profile_sha256"],
                      time_s=report["time_s"], coexistence=trajectory,
                      navigation_overlap_s=navigation_overlap,
                      physical_milestone_facts=facts)
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
        result["failure"] = f"{type(exc).__name__}: {exc}"
    return result


def issue_certificate(manifest, *, expected_manifest_sha256, reviews, expected_review_sha256):
    """Issue from externally pinned, complete accepted reviews; never self-sign."""
    from .goal_restaurant_initial_oracle import (
        _build_registration, validate_restaurant_initial_positive_certificate,
    )

    validate_calibration_manifest(manifest, expected_manifest_sha256=expected_manifest_sha256)
    _require(len(reviews) == len(expected_review_sha256) == len(manifest["trials"]), "calibration review coverage mismatch")
    slots = {(row["host"], row["seed"]): row for row in manifest["trials"]}
    used = set()
    trial_rows = []
    for report, pin in zip(reviews, expected_review_sha256):
        _require(canonical_sha256(report) == pin, "calibration review differs from independent pin")
        slot = (report["host"], report["seed"])
        _require(slot in slots and slot not in used and report["episode_id"] == slots[slot]["episode_id"],
                 "unknown or repeated review slot")
        _require(report["schema"] == REVIEW_SCHEMA and type(report["version"]) is int
                 and report["version"] == 2 and report["accepted"] is True
                 and report["manifest_sha256"] == expected_manifest_sha256
                 and all(report[key] is False for key in FLAGS), "unqualified calibration review")
        _require(report["runtime_profile_sha256"] == canonical_sha256(manifest["runtime_profiles"][slot[0]])
                 and canonical_sha256(report["raw_files"]) == report["raw_archive_sha256"], "calibration review identity mismatch")
        used.add(slot)
        trial_rows.append({key: report[key] for key in ("host", "seed", "raw_archive_sha256", "runtime_profile_sha256")})
    certificate = deepcopy(manifest["certificate_template"])
    certificate["qualification"].update(
        calibration_manifest_sha256=expected_manifest_sha256,
        calibration_trials=sorted(trial_rows, key=lambda row: (row["host"], row["seed"])),
        evidence_refs=sorted("native:calibration:" + row["raw_archive_sha256"] for row in trial_rows))
    certificate["registration_sha256"] = _build_registration(certificate)["registration_sha256"]
    pin = canonical_sha256(certificate)
    validate_restaurant_initial_positive_certificate(certificate, expected_certificate_sha256=pin)
    return {"certificate": certificate, "certificate_sha256": pin,
            "calibration_review_sha256": list(expected_review_sha256), **FLAGS}


def main(argv=None):
    """Bounded offline CLI; output paths are new and never overwritten."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("review", "issue"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True, help="independently pinned canonical JSON SHA-256")
    parser.add_argument("--host")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--review", type=Path, action="append", default=[])
    parser.add_argument("--review-sha256", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output already exists")
    manifest = _load(args.manifest)
    if args.operation == "review":
        if args.host is None or args.seed is None:
            parser.error("review requires --host and --seed")
        result = verify_calibration_trial(manifest, expected_manifest_sha256=args.manifest_sha256,
                                          host=args.host, seed=args.seed)
    else:
        result = issue_certificate(manifest, expected_manifest_sha256=args.manifest_sha256,
                                   reviews=[_load(path) for path in args.review],
                                   expected_review_sha256=args.review_sha256)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return 0 if result.get("accepted", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
