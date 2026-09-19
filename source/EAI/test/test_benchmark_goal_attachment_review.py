"""Response evidence must distinguish an authored joint from actual following."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_attachment_review import review_response, MEMBER, OBJECT
from demo.benchmark.goal_native_attachment import _seal


@pytest.fixture
def report():
    member, obj = "/World/m20/base", "/World/carton"
    joint = {"prim_path": obj+"/joint", "body0": [member], "body1": [obj], "owner_token":"owned",
             "joint_enabled": True, "collision_enabled": True, "exclude_from_articulation": True,
             "local_pos0": [.5,0.,0.], "local_pos1": [0.,0.,0.],
             "local_rot0_wxyz": [1.,0.,0.,0.], "local_rot1_wxyz": [1.,0.,0.,0.]}
    samples=[]
    for i in range(440):
        phase = "settle" if i<240 else "attached_reverse" if i<340 else "detached_reverse"
        mx = 0. if i<240 else -.001*(i-239) if i<340 else -.1-.002*(i-339)
        ox = .5 if i<240 else .5-.001*(i-239) if i<340 else .4
        bodies={path:{"source":"physx","body_path":path,"active":True,"physics_step":(i+1)*4,
                      "position_m":[x,0.,.3],"rotation_wxyz":[1.,0.,0.,0.],"mass_kg":mass}
                for path,x,mass in [(member,mx,16.),(obj,ox,3.)]}
        samples.append({"step":i+1,"physics_step":(i+1)*4,"phase":phase,"time_s":(i+1)*.02,
                        "commands":{"m20_2":[0. if i<240 else -.1,0.,0.]},"bodies":bodies,
                        "joint_present":[240<=i<340]})
    initial=deepcopy(samples[0]["bodies"])
    for pose in initial.values():pose["physics_step"]=0
    reasons={"overload_crate":"payload","inactive":"currently active","out_of_reach":"distance exceeds reach"}
    return {"attachment_receipt":_seal({"schema":"eai.goal_attachment_receipt","object_id":OBJECT,
                "stage_identifier":"anon:fixture","token":"owned","joint_readback":[joint]}),
            "body_bindings":{"members":{MEMBER:{"body_path":member}},"objects":{OBJECT:{"body_path":obj}}},
            "samples":samples,"timing":{"physics_dt_s":.005,"control_step_dt_s":.02,"decimation":4},
            "initial_native_snapshot":{"bodies":initial},
            "initial_native_objects":{"stage_identifier":"anon:fixture"},
            "settled_native_objects":{"stage_identifier":"anon:fixture"},
            "final_native_objects":{"stage_identifier":"anon:fixture"},
            "attach_snapshot":{"bodies":deepcopy(samples[239]["bodies"])},
            "detach_snapshot":{"bodies":deepcopy(samples[339]["bodies"])},
            "final_native_snapshot":{"bodies":deepcopy(samples[439]["bodies"])},
            "detach_receipt":{"token":"owned","status":"detached","removed_joint_paths":[obj+"/joint"],
                              "before_removal_readback":[deepcopy(joint)]},
            "detach_repeat":{"token":"owned","status":"detached","before_removal_readback":[],
                             "already_detached":True,"removed_joint_paths":[]},
            "negative_checks":[{"case":c,"expected_rejection":True,"zero_stage_effect":True,
                                "error":reasons[c],"expected_error":reasons[c],
                                "live_snapshot":{"bodies":deepcopy(samples[239]["bodies"])}}
                               for c in ["overload_crate","inactive","out_of_reach"]]}


def test_follow_then_release_is_narrow_qa_evidence(report):
    result=review_response(report)
    assert result["fixed_joint_follow_and_release_observed"]
    assert not result["lifting_validated"] and not result["scoreable"]


def test_native_float32_command_roundoff_is_allowed(report):
    for sample in report["samples"][240:]: sample["commands"]["m20_2"][0]=-.10000000149011612
    assert review_response(report)["fixed_joint_follow_and_release_observed"]


def test_authored_joint_without_object_motion_is_not_a_pass(report):
    obj=report["body_bindings"]["objects"][OBJECT]["body_path"]
    for sample in report["samples"]: sample["bodies"][obj]["position_m"][0]=.5
    for label,index in [("detach_snapshot",339),("final_native_snapshot",439)]:
        report[label]["bodies"]=deepcopy(report["samples"][index]["bodies"])
    report["quality_summary"]={"attached_reverse":{"max_anchor_error_m":0.}}
    assert not review_response(report)["fixed_joint_follow_and_release_observed"]


@pytest.mark.parametrize("change",["mass","time","stale","phase","joint","detach","boundary","negative"])
def test_inconsistent_response_evidence_rejected(report,change):
    sample=report["samples"][250]
    obj=report["body_bindings"]["objects"][OBJECT]["body_path"]
    if change=="mass": sample["bodies"][obj]["mass_kg"]=.01
    elif change=="time": sample["time_s"]+=.1
    elif change=="stale": sample["bodies"][obj]["physics_step"]-=4
    elif change=="phase": sample["phase"]="settle"
    elif change=="joint": sample["joint_present"]=[False]
    elif change=="detach": report["detach_receipt"]["removed_joint_paths"]=[]
    elif change=="boundary": report["attach_snapshot"]["bodies"][obj]["position_m"][0]=9.
    else: report["negative_checks"].pop()
    with pytest.raises(ValueError): review_response(report)


@pytest.mark.parametrize("change",["offset","indices","owner","readback","raw_error","raw_poses","stage"])
def test_mutated_consistency_cannot_be_hidden_by_resealed_receipt(report,change):
    if change=="offset":
        for sample in report["samples"]:sample["time_s"]+=60.
    elif change=="indices":
        for sample in report["samples"]:sample["physics_step"]+=4
    elif change=="owner":
        receipt=report["attachment_receipt"]
        receipt["joint_readback"][0]["owner_token"]="other"
        report["attachment_receipt"]=_seal({k:v for k,v in receipt.items() if k!="sha256"})
    elif change=="readback":report["detach_receipt"].pop("before_removal_readback")
    elif change=="raw_error":report["negative_checks"][0]["error"]=None
    elif change=="raw_poses":report["negative_checks"][0]["live_snapshot"]["bodies"]={}
    else:report["final_native_objects"]["stage_identifier"]="anon:other"
    with pytest.raises(ValueError):review_response(report)
