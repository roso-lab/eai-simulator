"""Local QA of the ideal fixed-joint microdiagnostic, never task scoring."""

import argparse
import json
import math
from pathlib import Path

from .goal_attachment_probe import load_probe, PHASES, MEMBER, OBJECT, anchor_quality, _command
from .goal_native_attachment import _check_seal, _sample, _digest
from .provenance import canonical_sha256, file_sha256


def review_response(report, *, public_task=None):
    """Derive response from raw poses; do not trust stored error/pass summaries."""
    receipt = report["attachment_receipt"]
    _check_seal(receipt)
    if (receipt.get("schema") != "eai.goal_attachment_receipt" or receipt.get("object_id") != OBJECT
            or not receipt.get("stage_identifier")
            or any(report[k].get("stage_identifier") != receipt["stage_identifier"] for k in
                   ("initial_native_objects","settled_native_objects","final_native_objects"))
            or (public_task is not None and receipt.get("public_task_sha256") != _digest(public_task))):
        raise ValueError("attachment receipt object/stage/public-task identity mismatch")
    joints = receipt["joint_readback"]
    bindings = report["body_bindings"]
    member_path = bindings["members"][MEMBER]["body_path"]
    object_path = bindings["objects"][OBJECT]["body_path"]
    if (len(joints) != 1 or joints[0]["body0"] != [member_path] or joints[0]["body1"] != [object_path]
            or joints[0]["joint_enabled"] is not True or joints[0]["collision_enabled"] is not True
            or joints[0]["exclude_from_articulation"] is not True or joints[0].get("owner_token") != receipt["token"]):
        raise ValueError("joint targets/semantics differ from bound diagnostic entities")
    samples = report["samples"]
    expected_phases = [phase for phase, count, _ in PHASES for _ in range(count)]
    if len(samples) != len(expected_phases) or [s["phase"] for s in samples] != expected_phases:
        raise ValueError("attachment diagnostic phases incomplete")
    timing = report["timing"]
    dt, step_dt, decimation = (timing[k] for k in ("physics_dt_s", "control_step_dt_s", "decimation"))
    if (type(dt) not in (int,float) or not math.isfinite(dt) or dt <= 0
            or type(step_dt) not in (int,float) or not math.isfinite(step_dt) or step_dt <= 0
            or type(decimation) is not int or decimation < 1 or not math.isclose(dt*decimation,step_dt)):
        raise ValueError("invalid attachment diagnostic timing")
    paths = set(report["initial_native_snapshot"]["bodies"])
    initial_poses = report["initial_native_snapshot"]["bodies"]
    initial_steps = {_sample(initial_poses.__getitem__,p)["physics_step"] for p in paths}
    if len(initial_steps) != 1:
        raise ValueError("initial attachment body clocks disagree")
    initial_step = next(iter(initial_steps))
    velocities = {phase: velocity for phase, _, velocity in PHASES}
    errors = {"attached_reverse": [], "detached_reverse": []}
    previous = None
    for index, sample in enumerate(samples):
        if (sample["step"] != index+1 or type(sample["physics_step"]) is not int
                or sample["physics_step"] != initial_step+(index+1)*decimation):
            raise ValueError("invalid attachment diagnostic step")
        now = sample["time_s"]
        if (type(now) not in (int,float) or not math.isfinite(now) or now < 0
                or not math.isclose(now,sample["physics_step"]*dt,abs_tol=1e-6,rel_tol=1e-6)):
            raise ValueError("invalid attachment simulation time")
        if previous and (sample["physics_step"]-previous["physics_step"] != decimation
                         or not math.isclose(now-previous["time_s"], step_dt, abs_tol=1e-6)):
            raise ValueError("attachment diagnostic time/step discontinuity")
        previous = sample
        command = sample["commands"]["m20_2"]
        if (set(sample["bodies"]) != paths or not isinstance(command,list) or len(command)!=3
                or any(type(a) not in (int,float) or not math.isfinite(a) or not math.isclose(a,b,abs_tol=1e-7)
                       for a,b in zip(command,velocities[sample["phase"]]))):
            raise ValueError("attachment sample inventory/command mismatch")
        for path in paths:
            pose = _sample(sample["bodies"].__getitem__, path)
            if pose["physics_step"] != sample["physics_step"]:
                raise ValueError("attachment poses are stale")
        if not math.isclose(sample["bodies"][object_path]["mass_kg"], 3., rel_tol=1e-5):
            raise ValueError("diagnostic changed object mass")
        phase = sample["phase"]
        if phase != "settle":
            present = [phase == "attached_reverse"]
            if sample.get("joint_present") != present:
                raise ValueError("joint presence disagrees with phase")
            errors[phase].extend(anchor_quality(receipt, sample["bodies"]))
    for name, boundary in (("attach_snapshot",239),("detach_snapshot",339),("final_native_snapshot",439)):
        if report[name]["bodies"] != samples[boundary]["bodies"]:
            raise ValueError("attachment transition does not match sampled bodies")
    detach = report["detach_receipt"]
    repeated = report["detach_repeat"]
    if (detach.get("token") != receipt["token"] or detach.get("status") != "detached"
            or detach.get("removed_joint_paths") != [joints[0]["prim_path"]]
            or detach.get("before_removal_readback") != joints
            or repeated.get("token") != receipt["token"] or repeated.get("status") != "detached"
            or repeated.get("before_removal_readback") != [] or repeated.get("already_detached") is not True
            or repeated.get("removed_joint_paths") != []):
        raise ValueError("attachment release evidence incomplete")
    checks = report["negative_checks"]
    if ({r["case"] for r in checks} != {"overload_crate", "inactive", "out_of_reach"} or len(checks) != 3
            or any(r.get("expected_rejection") is not True or r.get("zero_stage_effect") is not True for r in checks)):
        raise ValueError("attachment negative case evidence incomplete")
    reasons={"overload_crate":"payload", "inactive":"currently active", "out_of_reach":"distance exceeds reach"}
    for check in checks:
        if (not isinstance(check.get("error"),str) or reasons[check["case"]] not in check["error"]
                or check.get("expected_error") != reasons[check["case"]]
                or check.get("live_snapshot",{}).get("bodies") != report["attach_snapshot"]["bodies"]):
            raise ValueError("negative rejection lacks matching raw error/pose evidence")
    def displacement(path, start, end):
        a,b = (samples[i]["bodies"][path]["position_m"] for i in (start,end))
        return [y-x for x,y in zip(a,b)]
    attached_member = displacement(member_path,239,339)
    attached_object = displacement(object_path,239,339)
    detached_member = displacement(member_path,339,439)
    detached_object = displacement(object_path,339,439)
    max_anchor = max(q["anchor_error_m"] for q in errors["attached_reverse"])
    max_angle = max(q["orientation_error_rad"] for q in errors["attached_reverse"])
    # QA tolerances are explicit and deliberately separate from task scores.
    limits = {"attached_translation_min_m": .02, "anchor_max_m": .005,
              "orientation_max_rad": .01, "detached_member_min_m": .05,
              "detached_object_max_m": .01}
    observed = (attached_member[0] < -.02 and attached_object[0] < -.02
                and max_anchor <= .005 and max_angle <= .01
                and detached_member[0] < -.05 and math.dist(detached_object,[0.,0.,0.]) <= .01)
    return {"fixed_joint_follow_and_release_observed": observed, "qa_limits": limits,
            "attached_member_displacement_m": attached_member, "attached_object_displacement_m": attached_object,
            "detached_member_displacement_m": detached_member, "detached_object_displacement_m": detached_object,
            "max_attached_anchor_error_m": max_anchor, "max_attached_orientation_error_rad": max_angle,
            "scope": "one_3kg_object_ideal_connector_and_short_reverse_motion",
            "lifting_validated": False, "joint_load_sharing_validated": False,
            "full_skill_conformance_validated": False, "business_outcomes_validated": False, "scoreable": False}


def audit_attachment_probe(root):
    root = Path(root).absolute()
    request, deployment, _ = load_probe(root)
    report = json.loads((root/"attachment_report.json").read_text())
    process = json.loads((root/"process_result.json").read_text())
    launch = json.loads((root/"launch.json").read_text())
    if (process.get("returncode") != 0 or process.get("timed_out") is not False
            or process.get("diagnostic_completed") is not True or report.get("status") != "diagnostic_completed"
            or report.get("actual_device") != request["device"]):
        raise ValueError("attachment diagnostic did not complete in requested configuration")
    for payload in (report,process,launch):
        if payload.get("run_id") != request["run_id"] or payload.get("request_sha256") != canonical_sha256(request):
            raise ValueError("attachment archive launch identity mismatch")
    if (launch.get("command") != _command(root)
            or process.get("launch_sha256") != file_sha256(root/"launch.json")
            or process.get("attachment_report_sha256") != file_sha256(root/"attachment_report.json")):
        raise ValueError("attachment archive hash mismatch")
    for field in ("runtime_source_files", "runtime_source_files_final"):
        sources = report.get(field)
        if not isinstance(sources,dict) or not sources:
            raise ValueError("missing native source inventory")
        for path,digest in sources.items():
            if file_sha256(Path(path)) != digest:
                raise ValueError("attachment runtime source changed")
    result = {"schema": "eai.benchmark.goal_attachment_review", "version": 1,
              "run_id": request["run_id"], "attachment_report_sha256": file_sha256(root/"attachment_report.json"),
              **review_response(report, public_task=deployment["public_task"])}
    return {**result, "sha256": canonical_sha256(result)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root",type=Path)
    args=parser.parse_args(argv)
    print(json.dumps(audit_attachment_probe(args.output_root),indent=2,allow_nan=False))


if __name__ == "__main__":
    main()
