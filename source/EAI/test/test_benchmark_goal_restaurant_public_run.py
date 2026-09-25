"""Public-policy integration and diagnostic CLI ownership boundaries."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
from demo.benchmark.goal_restaurant_execution_v2 import RestaurantExecutionPump
from demo.benchmark.goal_restaurant_public_policy import PublicRestaurantParticipant
from demo.benchmark import goal_restaurant_public_run as runner
from test_benchmark_goal_restaurant_execution_v2 import _context, FakeObservationProvider


def _participant():
    contract = build_restaurant_deployment_v2()
    participant = PublicRestaurantParticipant()
    participant.initialize_deployment(contract["deployment"])
    return participant, contract


def test_public_policy_full_pump_keeps_failed_oracle_and_requires_delivered_results(tmp_path):
    participant, contract = _participant()
    context, fleet, _ = _context(tmp_path, participant)
    runtime = CommunicationRuntime({"pools": {
        name: {"capacity_bps": 1000000., "fixed_delay_s": .2}
        for name in ("backhaul_uplink", "backhaul_downlink", "p2p")}})
    pump = RestaurantExecutionPump(context, runtime=runtime,
                                   observation_provider=FakeObservationProvider(fleet))
    archive = pump.run(max_ticks=850)
    session = context.session.report()
    assert len(session["tasks"]) == 5
    assert {row["status"] for row in session["tasks"].values()} == {"succeeded"}
    assert fleet.max_active == 2
    assert len(participant.consumed) == 2
    assert session["deployment_assessment"]["external_evaluation"]["oracle_status"] == "failed"
    assert archive["scoreable"] is False
    assert archive["session_audit_replay"]["validated"] is True
    assert set(participant.team) <= {r["id"] for r in contract["public_task"]["candidate_robot_pool"]}
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from demo.benchmark.goal_restaurant_conditions import build_restaurant_task_conditions
    communication = runtime.report()
    request = {"public_task": contract["public_task"], "task_conditions":
               build_restaurant_task_conditions(
                   public_task=contract["public_task"],
                   hidden_evaluator=build_goal_task_package("restaurant")["hidden_evaluator"],
                   communication_contract=communication["initial_contract"],
                   deployment_companion=contract["deployment"])}
    condition = runner.ground_policy_condition_event(request, session, communication, archive)
    assert condition["event"]["trigger_status"] == "not_triggered"
    assert condition["condition_effect_qualified"] is False
    modified = deepcopy(session)
    modified["tasks"]["relay"]["task"]["assigned_agents"] = ["restaurant_iris_1"]
    with pytest.raises(ValueError, match="does not cover"):
        runner.ground_policy_condition_event(request, modified, communication, archive)


def test_lost_observation_result_does_not_dispatch_dependent_relay(tmp_path):
    participant, _ = _participant()
    context, fleet, _ = _context(tmp_path, participant)
    runtime = CommunicationRuntime({"pools": {
        name: {"capacity_bps": 1000000.}
        for name in ("backhaul_uplink", "backhaul_downlink", "p2p")}})
    pump = RestaurantExecutionPump(context, runtime=runtime,
                                   observation_provider=FakeObservationProvider(fleet))
    # Let navigation results arrive, then lose observation publications only.
    for _ in range(100):
        pump.step_once()
    runtime.set_conditions({"drop": True}, pool="backhaul_uplink")
    archive = pump.run(max_ticks=850)
    assert "relay" not in participant.sent
    observed = [row for row in archive["physical_records"]
                if row["task"]["skill"] == "observe"]
    assert len(observed) == 2
    assert all(row["status"] == "succeeded" for row in observed)
    assert participant.consumed == set()


def test_policy_assignment_changes_with_public_geometry_not_witness():
    participant, contract = _participant()
    participant.initialize(contract["public_task"], contract["participant_api"])
    initial = list(participant.team)
    changed = deepcopy(contract["deployment"])
    targets = ["kitchen_service", "dining_service"]
    for index, robot in enumerate(reversed(initial)):
        spawn = changed["spawn_regions"][robot]
        changed["region_bindings"][spawn]["center_xy_m"] = list(
            changed["region_bindings"][targets[index]]["center_xy_m"])
    other = PublicRestaurantParticipant()
    other.initialize_deployment(changed)
    other.initialize(contract["public_task"], contract["participant_api"])
    assert other.team == list(reversed(initial))


def test_cli_existing_output_is_preserved_before_preparation(tmp_path, monkeypatch):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "marker"
    marker.write_text("do not overwrite")
    monkeypatch.setattr(runner, "prepare_request", lambda _: pytest.fail("must not prepare existing output"))
    with pytest.raises(FileExistsError):
        runner.main(["--products-root", str(tmp_path), "--output", str(output),
                     "--episode-id", "test", "--communication-contract", str(tmp_path / "absent")])
    assert marker.read_text() == "do not overwrite"


def test_real_prepare_binds_derived_task_and_explicit_transport(tmp_path, monkeypatch):
    pools = tmp_path / "pools.json"
    pools.write_text(json.dumps({"pools": {
        name: {"bytes_per_second": 1000000., "fixed_delay_s": .04}
        for name in ("backhaul_uplink", "backhaul_downlink", "p2p")}}))
    monkeypatch.setattr(runner, "_source_snapshot", lambda: {"fixture": "source-sha"})
    args = runner.parser().parse_args([
        "--products-root", str(tmp_path), "--output", str(tmp_path / "new"),
        "--episode-id", "prepare-only", "--communication-contract", str(pools)])
    request = runner.prepare_request(args)
    identity = request["task_conditions"]["task_identity"]
    assert identity["source_task_id"] == "RESTAURANT-GOAL-01"
    assert identity["effective_task_id"] == "RESTAURANT-GOAL-NATIVE-02"
    assert request["task_conditions"]["c0_status"] == "not_declared"
    assert request["communication_contract"]["pools"]["backhaul_uplink"]["capacity_bps"] == 1000000.
    assert not args.output.exists()


def test_preflight_runs_without_native_start_and_retains_closed_flags(tmp_path, monkeypatch):
    from tools.assets import prepare_restaurant_benchmark_assets

    output = tmp_path / "preflight"
    monkeypatch.setattr(runner, "prepare_request", lambda _: {"scope": "test-static-inputs"})
    monkeypatch.setattr(prepare_restaurant_benchmark_assets, "inspect_candidate", lambda _: {"status": "ready"})
    monkeypatch.setattr(runner, "run_native", lambda *_: pytest.fail("preflight must not start Isaac"))
    assert runner.main(["--products-root", str(tmp_path), "--output", str(output),
                        "--episode-id", "test", "--communication-contract", str(tmp_path / "absent"),
                        "--preflight-only"]) == 0
    result = json.loads((output / "preflight-result.json").read_text())
    assert result["native_started"] is False
    assert result["oracle_status"] == "failed"
    assert result["feasibility_label"] is None
    assert result["scoreable"] is False


def test_asset_failure_is_archived_without_participant_failure_inference(tmp_path, monkeypatch):
    from tools.assets import prepare_restaurant_benchmark_assets

    output = tmp_path / "bad-assets"
    monkeypatch.setattr(runner, "prepare_request", lambda _: {"scope": "test-static-inputs"})
    monkeypatch.setattr(prepare_restaurant_benchmark_assets, "inspect_candidate", lambda _: {"status": "failed"})
    with pytest.raises(RuntimeError, match="asset closure"):
        runner.main(["--products-root", str(tmp_path), "--output", str(output),
                     "--episode-id", "test", "--communication-contract", str(tmp_path / "absent"),
                     "--preflight-only"])
    failure = json.loads((output / "failure.json").read_text())
    assert failure["participant_failure_inferred"] is False
    assert not (output / "report.json").exists()
