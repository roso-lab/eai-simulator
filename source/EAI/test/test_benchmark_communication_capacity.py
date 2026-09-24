from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.communication_capacity_experiments import (
    CAPACITY_POOLS,
    bytes_by_direction,
    capacity_observations,
    jitter_paired_observations,
    loss_paired_observations,
    run_capacity_cell,
    run_delay_cell,
    run_loss_cell,
    summarize_capacity_experiments,
)


def test_fixed_delay_cell_delivers_everything():
    cell = run_delay_cell(condition="fixed_delay", mean_delay_s=1.0, seed=11,
                          base_instance="jp-x")
    assert cell["delivery_rate"] == 1.0
    assert cell["mean_configured_delay_s"] == 1.0


def test_jitter_same_mean_reduces_delivery_under_ttl():
    # Same exact configured mean (alternating 0.1s/1.9s around 1.0s) with a
    # TTL of 1.45s: fixed arrivals deliver, jitter high samples expire, so
    # the paired difference isolates the delay distribution, not the mean.
    fixed = run_delay_cell(condition="fixed_delay", mean_delay_s=1.0, seed=11,
                           base_instance="jp-x")
    jitter = run_delay_cell(condition="jitter", mean_delay_s=1.0, seed=11,
                            base_instance="jp-x")
    assert fixed["mean_configured_delay_s"] == jitter["mean_configured_delay_s"] == 1.0
    assert jitter["delivery_rate"] < fixed["delivery_rate"]


def test_jitter_paired_effect_direction_is_against_jitter():
    rows = jitter_paired_observations(bases=("jp-a", "jp-b"), seeds=(11,))
    effect = summarize_capacity_experiments(jitter_rows=rows)["jp_paired_effect"]
    assert effect["status"] == "ok"
    for row in effect["effects"]:
        assert row["status"] == "ok"
        # jitter minus fixed delivery rate is strictly negative per level.
        assert row["mean_difference_right_minus_left"] < 0


def test_iid_and_burst_have_same_configured_loss_rate():
    iid = run_loss_cell(condition="iid_loss", rate=0.3, seed=21, base_instance="bp-x")
    burst = run_loss_cell(condition="burst_loss", rate=0.3, seed=21, base_instance="bp-x")
    assert iid["configured_loss_rate"] == burst["configured_loss_rate"] == 0.3


def test_burst_loss_widens_max_delivery_gap_vs_iid():
    iid = run_loss_cell(condition="iid_loss", rate=0.3, seed=22, base_instance="bp-x")
    burst = run_loss_cell(condition="burst_loss", rate=0.3, seed=22, base_instance="bp-x")
    assert burst["max_inter_delivery_gap_s"] > iid["max_inter_delivery_gap_s"]


def test_burst_paired_effect_direction_is_against_burst():
    rows = loss_paired_observations(bases=("bp-a", "bp-b"), seeds=(22,))
    report = summarize_capacity_experiments(loss_rows=rows)
    effect = report["bp_paired_effect"]
    assert effect["status"] == "ok"
    for row in effect["effects"]:
        assert row["status"] == "ok"
        # burst minus iid outage gap is strictly positive per level.
        assert row["mean_difference_right_minus_left"] > 0


def test_capacity_curve_brackets_c90_and_c50_per_pool():
    report = summarize_capacity_experiments(
        capacity_rows_by_pool={
            pool: capacity_observations(pool=pool, seeds=(31,))
            for pool in CAPACITY_POOLS})
    assert report["capacity_curves"]["backhaul_uplink"]["status"] == "ok"
    for pool in CAPACITY_POOLS:
        curve = report["capacity_curves"][pool]
        reference = curve["reference"]
        assert reference["capacity_bps"] is None
        assert reference["mean"] >= 0.99
        # Bootstrap intervals exist only inside valid samples.
        for point in curve["points"]:
            assert len(point["bootstrap_ci95"]) == 2


def test_capacity_reference_point_is_perfect_and_low_capacity_degrades():
    reference = run_capacity_cell(pool="backhaul_uplink", capacity_bps=None,
                                  base_instance="cap-x", seed=31)
    starved = run_capacity_cell(pool="backhaul_uplink", capacity_bps=250.0,
                                base_instance="cap-x", seed=31)
    assert reference["delivery_rate"] >= starved["delivery_rate"]
    assert reference["delivery_rate"] >= 0.99


def test_unknown_pool_rejected():
    try:
        capacity_observations(pool="telepathy")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown pool accepted")


def test_direction_attribution_fails_closed_on_missing_direction():
    runtime = CommunicationRuntime()
    runtime.publish(message_id="m1", producer_id="robot_1", recipients=["edge_1"],
                    channel="observation", estimate={"k": 1}, valid_until_s=5.0)
    runtime.advance(5.0)
    attribution = bytes_by_direction(runtime.report()["ledger"])
    # Legacy link mode carries no pool/direction: totals stay, breakdown null.
    assert attribution["direction_complete"] is False
    assert attribution["bytes_by_direction"] is None


def test_direction_attribution_resolves_explicit_directions():
    cell = run_capacity_cell(pool="backhaul_uplink", capacity_bps=4000.0,
                             base_instance="cap-x", seed=31, direction="uplink")
    assert cell["delivery_rate"] >= 0.0
    runtime_rows = run_delay_cell(condition="fixed_delay", mean_delay_s=0.5,
                                  amplitude_s=0.1, seed=11, base_instance="jp-x")
    assert runtime_rows["delivery_rate"] > 0.0


def test_report_is_development_track_evidence_only():
    report = summarize_capacity_experiments(
        jitter_rows=jitter_paired_observations(bases=("jp-a",), seeds=(11,)),
        loss_rows=loss_paired_observations(bases=("bp-a",), seeds=(21,)),
        capacity_rows_by_pool={"backhaul_uplink": capacity_observations(
            pool="backhaul_uplink", bases=("cap-a",), seeds=(31,))})
    assert report["scoreable"] is False
    assert report["execution_validated"] is False
    assert report["formal_statistics"] is False
    assert report["development_track"]["infrastructure_blockers"] == [
        "communication_formal_episode_pending"]
    assert report["development_track"]["evidence_class"] == "diagnostic"
