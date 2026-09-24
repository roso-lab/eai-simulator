"""Real host/ledger integration; fixture roots do not certify native execution."""
from copy import deepcopy

import pytest

from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
from demo.benchmark.goal_restaurant_autonomous_v2 import (
    open_restaurant_autonomous_context, RestaurantDeploymentContextError,
)
from demo.benchmark.goal_restaurant_deployment_v2 import (
    DEPLOYMENT_PROFILE, build_restaurant_deployment_v2,
)
from demo.benchmark.goal_restaurant_fleet import RestaurantFleetController
from demo.benchmark.goal_restaurant_native_observation import RestaurantNativeObservationProvider
from demo.benchmark.goal_restaurant_native_state import build_restaurant_native_initial_state
from demo.benchmark.goal_session_audit import replay_goal_session_audit
from demo.benchmark.provenance import canonical_sha256
from source.EAI.test.test_benchmark_goal_algorithm_adapter import RecordingAdapter, action
from source.EAI.test.test_benchmark_goal_restaurant_native_state import _session
from source.EAI.test.test_benchmark_goal_restaurant_native_observation import Query


def fleet_at_start(tmp_path):
    session = _session()
    # Ground query configuration is optional to fleet, mandatory to observations.
    from types import SimpleNamespace
    session.env_cfg = SimpleNamespace(sim=SimpleNamespace(enable_scene_query_support=True))
    fleet = RestaurantFleetController(session, products_root=tmp_path,
                                       deployment_profile=DEPLOYMENT_PROFILE)
    fleet.begin_episode("full8-context", warmup_ticks=2)
    return fleet


class Adapter(RecordingAdapter):
    def initialize_deployment(self, deployment):
        self.deployment = deepcopy(deployment)
        return {"schema": "eai.restaurant.deployment_ack", "version": 2,
                "profile": deployment["profile"],
                "deployment_sha256": canonical_sha256(deployment),
                "public_task_sha256": deployment["public_task_sha256"]}


def assess(events, decision="deployable"):
    required = next(e["payload"] for e in events if e["type"] == "deployment_assessment_required")
    return action("assess", "deployment_assessment", {
        **{k: required[k] for k in ("assessment_id", "assessment_epoch", "roster_version",
                                    "capability_versions")},
        "decision": decision,
        "reason_codes": ["missing_capability"] if decision == "not_deployable" else [],
        "evidence_refs": []})


def nav(contract, ident, producer, source, target, *, start=0.):
    robot = next(r for r in contract["public_task"]["candidate_robot_pool"] if r["id"] == producer)
    skill = "ground_navigation" if robot["mobility"] == "ground" else "aerial_navigation"
    route = next(r for r in contract["public_task"]["public_world"]["routes"]
                 if r["from_region_id"] == source and r["to_region_id"] == target
                 and skill in r["access_modes"])
    return {"task_id": ident, "skill": skill,
            "arguments": {"region_id": target, "route_ids": [route["id"]]},
            "prerequisites": [], "assigned_agents": [producer],
            "required_capabilities": [skill], "evidence_refs": [],
            "execution": {"start_s": start, "duration_s": 30.}}


def test_full8_native_epoch_is_bound_to_companion_and_each_region_has_one_goal(tmp_path):
    fleet = fleet_at_start(tmp_path)
    contract = build_restaurant_deployment_v2()
    meta = fleet.episode_metadata()
    assert meta["task_id"] == contract["public_task"]["task_id"]
    assert meta["deployment_sha256"] == canonical_sha256(contract["deployment"])
    state = build_restaurant_native_initial_state(fleet)
    assert state["status"] == "measured"
    assert state["robot_regions"] == contract["deployment"]["spawn_regions"]
    assert len(set(state["robot_regions"].values())) == 8
    ground = [p for p in state["robot_regions"] if "iris" not in p]
    goals = [fleet._region_goal(p, "restaurant_staging")["goal_xyz_m"] for p in ground]
    assert all(g == goals[0] for g in goals)
    with pytest.raises(ValueError, match="no maintained"):
        fleet._region_goal(ground[0], "restaurant_standby_a")
    source = RestaurantNativeObservationProvider(fleet, scene_query=Query())
    assert source.ground_readiness(ground[0], "kitchen_service")["scene_observation_validated"] is False


@pytest.mark.parametrize("parallel", [False, True])
def test_real_host_accepts_autonomous_serial_or_parallel_plans_with_full8_context(tmp_path, parallel):
    fleet, contract = fleet_at_start(tmp_path), build_restaurant_deployment_v2()
    spawns = contract["deployment"]["spawn_regions"]
    if parallel:
        team = ["restaurant_carter_2", "restaurant_go2_1"]
        tasks = [nav(contract, "mine-a", team[0], spawns[team[0]], "dining_service"),
                 nav(contract, "mine-b", team[1], spawns[team[1]], "kitchen_service")]
    else:
        team = ["restaurant_carter_1"]
        tasks = [nav(contract, "mine-a", team[0], spawns[team[0]], "kitchen_service"),
                 nav(contract, "mine-b", team[0], "kitchen_service", "dining_service", start=30.)]
        tasks[1]["prerequisites"] = ["mine-a"]
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "participant-created", "assigned_team": team, "tasks": tasks}
    adapter = Adapter(lambda events: [assess(events), action("team", "select_team", {"agent_ids": team}),
                                     action("plan", "submit_plan", {"plan": plan})])
    seen = []

    def evaluate(episode, report):
        seen.append(deepcopy(report))
        assert adapter.initialized == []
        gate = report["session"]["deployment_assessment"]
        return {"assessment_id": gate["assessment_id"], "assessment_epoch": gate["assessment_epoch"],
                "feasibility_label": None, "oracle_status": "failed", "platform_valid": False,
                "evidence_refs": [], "evaluator_ref": "synthetic_context_not_oracle_proof"}

    with open_restaurant_autonomous_context(fleet, adapter, deployment=contract["deployment"],
                                            assessment_evaluator=evaluate) as ctx:
        assert len(seen[0]["session"]["runtime_state"]["robot_regions"]) == 8
        assert seen[0]["session"]["deployment_assessment"]["runtime_state"] == seen[0]["session"]["runtime_state"]
        assert seen[0]["deployment_binding"]["deployment_sha256"] == canonical_sha256(contract["deployment"])
        results = ctx.host.request_actions()
        assert all(r["accepted"] for r in results)
        report = ctx.session.report()
        assert report["plans"][0]["plan"] == plan
        assert report["deployment_assessment"]["history"][0]["declared_runtime_state"]["robot_regions"] == spawns
        history = report["deployment_assessment"]["history"][0]
        assert history["trigger_runtime_state"] == history["declared_runtime_state"]
        assert history["trigger_runtime_state_sha256"] == history["declared_runtime_state_sha256"]
        assert report["deployment_assessment"]["history"][0]["external_evaluation"]["oracle_status"] == "failed"
        archive = ctx.archive()
        replay = replay_goal_session_audit(archive["session_audit"],
                                          public_task=contract["public_task"],
                                          participant_api=contract["participant_api"])
        assert replay["validated"] is True
        assert archive["execution_loop_connected"] is False
        assert archive["scoreable"] is False
    assert adapter.close_count == 1
    assert all("native_initial_state" not in e["payload"] for e in adapter.batches[0])


def test_initial_assessment_gate_and_human_assistance_preserved(tmp_path):
    fleet, contract = fleet_at_start(tmp_path), build_restaurant_deployment_v2()
    adapter = Adapter(lambda events: [assess(events, "not_deployable")])
    with open_restaurant_autonomous_context(fleet, adapter, deployment=contract["deployment"]) as ctx:
        ctx.host.request_actions()
        assert ctx.session.report()["deployment_assessment"]["terminated"] is True
        assert ctx.host.request_actions() == []
        assert ctx.session.report()["events"][-1]["type"] == "human_assistance_requested"
    assert fleet.time_s == 0.


def test_no_selection_before_assessment(tmp_path):
    fleet, contract = fleet_at_start(tmp_path), build_restaurant_deployment_v2()
    adapter = Adapter(lambda _: [action("team", "select_team", {"agent_ids": ["restaurant_carter_1"]})])
    with open_restaurant_autonomous_context(fleet, adapter, deployment=contract["deployment"]) as ctx:
        with pytest.raises(ValueError, match="deployment_assessment"):
            ctx.host.request_actions()


def test_foreign_companion_missing_optin_or_false_ack_rejected(tmp_path):
    fleet, contract = fleet_at_start(tmp_path), build_restaurant_deployment_v2()
    wrong = deepcopy(contract["deployment"])
    wrong["region_bindings"]["restaurant_staging"]["center_xy_m"][0] += 10.
    adapter = Adapter(lambda _: [])
    with pytest.raises(RestaurantDeploymentContextError, match="companion differs"):
        with open_restaurant_autonomous_context(fleet, adapter, deployment=wrong):
            pass
    assert adapter.initialized == []
    legacy = RecordingAdapter(lambda _: [])
    with pytest.raises(RestaurantDeploymentContextError, match="not opted"):
        with open_restaurant_autonomous_context(fleet, legacy, deployment=contract["deployment"]):
            pass
    adapter.initialize_deployment = lambda _: {}
    with pytest.raises(RestaurantDeploymentContextError, match="acknowledgement"):
        with open_restaurant_autonomous_context(fleet, adapter, deployment=contract["deployment"]):
            pass
    assert adapter.close_count == 1


def test_old_epoch_cannot_be_relabelled_as_new_deployment(tmp_path):
    fleet = RestaurantFleetController(_session(), products_root=tmp_path)
    fleet.begin_episode("legacy", warmup_ticks=1)
    contract = build_restaurant_deployment_v2()
    with pytest.raises(RestaurantDeploymentContextError, match="full8"):
        with open_restaurant_autonomous_context(fleet, Adapter(lambda _: []),
                                                deployment=contract["deployment"]):
            pass


def test_new_profile_route_id_target_and_measured_start_are_enforced(tmp_path):
    fleet, contract = fleet_at_start(tmp_path), build_restaurant_deployment_v2()
    producer = "restaurant_carter_2"
    task = nav(contract, "n", producer, contract["deployment"]["spawn_regions"][producer], "kitchen_service")
    route_id = task["arguments"]["route_ids"][0]
    with pytest.raises(ValueError, match="topology"):
        fleet.plan_route(producer, "made-up", "kitchen_service")
    with pytest.raises(ValueError, match="topology"):
        fleet.plan_route(producer, route_id, "dining_service")
    with pytest.raises(RuntimeError, match="outside.*origin"):
        fleet.plan_route("restaurant_carter_1", route_id, "kitchen_service")


def test_pristine_initializer_is_atomic_one_shot_and_legacy_stays_unchanged():
    from demo.benchmark.goal_session import GoalSession
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    contract = build_restaurant_deployment_v2()
    public, api = contract["public_task"], contract["participant_api"]
    session = GoalSession(public, api)
    before = session.report()
    assert before["deployment_assessment"]["runtime_state"] is None
    incomplete = dict(contract["deployment"]["spawn_regions"])
    incomplete.pop("restaurant_iris_2")
    with pytest.raises(ValueError, match="complete"):
        session.initialize_deployment_runtime_state(incomplete, {})
    assert session.report() == before
    session.initialize_deployment_runtime_state(contract["deployment"]["spawn_regions"], {})
    seeded = session.report()
    assert seeded["events"] == before["events"]
    assert seeded["deployment_assessment"]["assessment_epoch"] == 0
    assert seeded["deployment_assessment"]["runtime_state_sha256"] == canonical_sha256(seeded["runtime_state"])
    with pytest.raises(ValueError, match="pristine"):
        session.initialize_deployment_runtime_state(contract["deployment"]["spawn_regions"], {})
    assert session.report() == seeded
    old = GoalSession(build_goal_task_package("restaurant")["public_task"])
    with pytest.raises(ValueError, match="pristine"):
        old.initialize_deployment_runtime_state(contract["deployment"]["spawn_regions"], {})


@pytest.mark.parametrize("mutate", ["snapshot", "evaluate", "event", "action"])
def test_initializer_cannot_rebind_an_observed_or_evaluated_epoch(mutate):
    from demo.benchmark.goal_session import GoalSession
    contract = build_restaurant_deployment_v2()
    session = GoalSession(contract["public_task"], contract["participant_api"])
    spawns = contract["deployment"]["spawn_regions"]
    if mutate == "snapshot":
        session.update_runtime_state(0., spawns, {})
    elif mutate == "evaluate":
        gate = session.report()["deployment_assessment"]
        session.record_deployment_assessment_evaluation({
            "assessment_id": gate["assessment_id"], "assessment_epoch": 0,
            "feasibility_label": None, "oracle_status": "failed", "platform_valid": False,
            "evidence_refs": [], "evaluator_ref": "test"})
    elif mutate == "event":
        session.emit("member_exit", 0., {"agent_id": "restaurant_iris_2"})
    else:
        session.submit_action(assess(session.report()["events"]))
    before = session.report()
    with pytest.raises(ValueError, match="pristine"):
        session.initialize_deployment_runtime_state(spawns, {})
    assert session.report() == before
