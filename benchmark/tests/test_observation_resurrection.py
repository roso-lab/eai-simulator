from __future__ import annotations
import base64, json
import pytest
from algorithm.skills.contracts import SkillRequest, SkillContractError
from algorithm.skills.registry import SKILL_REGISTRY
from algorithm.skills.observation import FakeOnDemandCameraBackend, GoalCameraTelemetry, GoalCameraTools
from algorithm.skills.observation.runtime import CameraObservationRuntime, camera_call
from algorithm.skills.observation.communication import CommunicationRuntime, validate_communication_report
from benchmark.observation_artifacts import ObservationArtifactStore

PUBLIC={"candidate_robot_pool":[{"id":"r1"}],"budgets":{"simulation_s":10}}
CAT={"r1":{"cam":{"member":"primary","modalities":["rgb"],"encodings":["png"],"max_width":128,"max_height":96,"max_bytes":1000}}}
def setup(**kw):
    return GoalCameraTools(PUBLIC, episode_id="obs", sensor_catalog=CAT, backend=kw.pop("backend",FakeOnDemandCameraBackend(warmup_frames=0)), telemetry=GoalCameraTelemetry(CommunicationRuntime(kw.pop("contract",None))), **kw)
def call(i="r", **kw):
    args={"robot_id":"r1","sensor_id":"cam","modalities":["rgb"],"deadline_s":kw.pop("deadline_s",5),"resolution":{"width":32,"height":24},"quality":{"encoding":kw.pop("encoding","png"),"max_bytes":kw.pop("max_bytes",500)},"freshness":"capture_at_or_after_submit"}
    args.update(kw.pop("args",{}))
    return {"schema":"eai.benchmark.goal_camera_tool_call","version":1,"request_id":i,"submitted_at_s":kw.pop("now",0),"tool":"request_observation","arguments":args}
def link(report):
    return sum(d.get("bytes_sent",0) for m in report["communication"]["messages"].values() for d in m["recipients"].values())

def test_registry_and_profile_are_registered_and_m1_only():
    row=next(x for x in SKILL_REGISTRY if x["skill_id"]=="eai.observation.camera")
    assert row["kind"]=="inspection" and row["role"]=="on_demand_camera_observation"
    p=setup().public_profile(); assert p["subscription"] is False and p["capture_cost_ticks"]==5
    from algorithm.skills.observation.runtime import CAPTURE_TICKS
    assert p["capture_cost_ticks"]==CAPTURE_TICKS  # one registered value, two declarations must agree

def test_no_opt_in_is_zero_transport():
    t=setup(); assert t.report()["request_count"]==0 and link(t.report())==0

def test_typed_request_maps_to_frozen_call():
    req=SkillRequest("r","eai.observation.camera","r1",parameters={"camera_ref":"cam","mode":"capture","deadline":5,"resolution":{"width":32,"height":24},"quality":{"encoding":"png","max_bytes":500}})
    assert camera_call(req,time_s=0)["arguments"]["sensor_id"]=="cam"

def test_typed_request_rejects_target_and_unknown_fields():
    req=SkillRequest("r","eai.observation.camera","r1",parameters={"camera_ref":"cam","mode":"capture","deadline":5,"resolution":{"width":32,"height":24},"quality":{"encoding":"png","max_bytes":500}})
    with pytest.raises(SkillContractError): camera_call(SkillRequest("r","eai.observation.camera","r1",target_ref="x",parameters=req.parameters),time_s=0)
    with pytest.raises(SkillContractError): camera_call(SkillRequest("r","eai.observation.camera","r1",parameters={**req.parameters,"extra":1}),time_s=0)

def test_request_delivers_and_receipt_contains_digest_and_bytes():
    t=setup(); rt=CameraObservationRuntime(t, artifact_store=ObservationArtifactStore()); rt.request({"request_id":"r","skill_id":"eai.observation.camera","robot_id":"r1","parameters":{"camera_ref":"cam","deadline":5,"resolution":{"width":32,"height":24},"quality":{"encoding":"png","max_bytes":500}}},time_s=0); events=rt.tick(0); assert any(e.get("kind")=="camera_frame" for e in events); rec=rt.receipt("r"); assert rec.status=="delivered" and rec.byte_length>0 and rec.transport_size_bytes>0

def test_quality_deadline_and_enum_fail_closed():
    assert setup().submit(call("bad",max_bytes=5000),time_s=0)["error_code"]=="budget_exceeded"
    assert setup().submit(call("late",deadline_s=0),time_s=0)["error_code"]=="invalid_deadline"
    assert setup().submit(call("old",args={"requested_sim_time_s":1}),time_s=0)["error_code"]=="unsupported_field"
    assert setup().submit(call("enc",encoding="jpeg"),time_s=0)["error_code"]=="invalid_encoding"

def test_hidden_sensor_not_enumerable_and_join_only():
    hidden="secret"; cat={**CAT,hidden:{"cam":CAT["r1"]["cam"]}}; t=GoalCameraTools(PUBLIC,episode_id="obs",sensor_catalog=cat,backend=FakeOnDemandCameraBackend(warmup_frames=0),telemetry=GoalCameraTelemetry()); assert hidden not in json.dumps(t.public_profile()); assert t.submit(call("x",args={"robot_id":hidden}),time_s=0)["error_code"]=="unknown_robot"
    with pytest.raises(RuntimeError): t.register_public_robot(hidden)

def test_late_frame_suppressed_and_bytes_retained():
    t=setup(contract={"delay_s":5},validity_s=20); t.submit(call("r",deadline_s=2),time_s=0); t.tick(0); assert any(x.get("kind")=="camera_request_timeout" for x in t.tick(2)); assert not any(x.get("kind")=="camera_frame" for x in t.tick(5)); assert link(t.report())>0

def test_cancel_has_one_terminal():
    t=setup(); t.submit(call("r"),time_s=0); t.submit({"schema":"eai.benchmark.goal_camera_tool_call","version":1,"request_id":"c","submitted_at_s":0,"tool":"cancel_request","arguments":{"request_id":"r"}},time_s=0); t.tick(0); row=t.report()["requests"][0]; assert row["status"]=="cancelled" and row["terminal"]

def test_capture_failure_and_oversize_leave_no_cache():
    b=FakeOnDemandCameraBackend(warmup_frames=0,oversized_bytes=2000); t=setup(backend=b); t.submit(call("r",max_bytes=100),time_s=0); t.tick(0); assert t.report()["requests"][0]["status"]=="failed" and t.report()["cache_entries"]==0

def test_transport_report_replays():
    t=setup(); t.submit(call("r"),time_s=0); t.tick(0); assert validate_communication_report(t.report()["communication"])["validated"]

def test_cache_hit_remeters_bytes():
    t=setup(); t.submit(call("a"),time_s=0); t.tick(0); first=link(t.report()); t.submit(call("b"),time_s=0); ev=t.tick(0); assert any(e.get("data",{}).get("cache_hit") for e in ev if e.get("kind")=="camera_frame"); assert link(t.report())>first

def test_payload_ref_and_hidden_ground_truth_rejected():
    tel=GoalCameraTelemetry(); frame={"request_id":"r","frame_id":"f","capture_sim_time_s":0,"physics_step":0,"pose":{"source":"synthetic"},"calibration":{},"encoding":"png","payload_b64":"QQ==","payload_sha256":"a"*64,"byte_length":1,"cache_hit":False,"status":"fresh","payload_ref":"file:///tmp/x"}
    with pytest.raises(ValueError): tel.publish("r1",frame,valid_until_s=1)

def test_episode_finalize_closes_every_request_once():
    t=setup(contract={"delay_s":5}); t.submit(call("r"),time_s=0); t.tick(0); t.finalize_episode(5); t.close(); t.close(); row=t.report()["requests"][0]; assert row["status"]=="episode_ended" and row["terminal"]

def test_artifact_store_is_content_addressed():
    s=ObservationArtifactStore(); d=s.put(b"abc"); assert d==s.put(b"abc") and s.get(d)==b"abc" and s.manifest()[d]==3
