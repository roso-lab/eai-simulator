"""Protocol 3.2 task-performance communication metrics (development track)."""

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_communication_metrics import (
    BYTE_TO_BIT_FACTOR,
    capacity_thresholds,
    communication_condition_metrics,
    communication_dependency,
    distribution_contrast,
    retention_curve,
)

_RELEASE_FLAGS = ("execution_validated", "business_outcomes_validated",
                  "scoreable")


def _obs(milestone, at, stability=0.0):
    return {"milestone_id": milestone, "satisfied_at_s": at,
            "stability_duration_s": stability}


def _progress(active, observations, horizon=100.0, **extra):
    return {"active_milestones": list(active), "observations": observations,
            "horizon_s": horizon, **extra}


def _mixed_runtime():
    """One ledger with a delivered copy, a TTL loss, and a channel drop."""
    runtime = CommunicationRuntime({"pools": {
        "backhaul_uplink": {"fixed_delay_s": 2.0},
        "p2p": {"drop": True},
    }})
    runtime.publish(message_id="m1", producer_id="robot_1",
                    recipients=["edge_1"], channel="observation",
                    estimate={"k": 1}, valid_until_s=10.0,
                    pool="backhaul_uplink")
    # valid_until 1.0 < arrival 2.0: TTL timeout after entering the link.
    runtime.publish(message_id="m2", producer_id="robot_1",
                    recipients=["edge_1"], channel="observation",
                    estimate={"k": 2}, valid_until_s=1.0,
                    pool="backhaul_uplink")
    runtime.publish(message_id="m3", producer_id="robot_1",
                    recipients=["robot_2"], channel="p2p_relay",
                    estimate={"k": 3}, valid_until_s=10.0, pool="p2p")
    runtime.advance(5.0)
    return runtime


def test_sr_mcr_aup_end_to_end_from_progress_curve():
    partial = communication_condition_metrics(
        None, _progress(["a", "b", "c"], [_obs("a", 10.0), _obs("b", 50.0)]),
        condition_id="loss_0.2")
    performance = partial["task_performance"]
    assert performance["status"] == "computed"
    assert performance["SR_comm"] == 0.0  # Q(H) = 2/3 != 1
    assert performance["MCR_comm"] == pytest.approx(2 / 3)
    # AUP = (1/3*40 + 2/3*50) / 100
    assert performance["AUP_comm"] == pytest.approx(140 / 300)
    curve = performance["progress_curve"]
    assert performance["MCR_comm"] == curve["MCR"] == curve["Q_H"]
    assert performance["AUP_comm"] == pytest.approx(curve["AUP"])

    full = communication_condition_metrics(
        None, _progress(["a", "b"], [_obs("a", 1.0), _obs("b", 2.0)]),
        condition_id="perfect")["task_performance"]
    assert full["SR_comm"] == 1.0
    assert full["MCR_comm"] == 1.0


def test_task_performance_na_when_milestones_missing():
    for missing in (None, {}, {"active_milestones": ["a"]},
                    {"horizon_s": 10.0}):
        result = communication_condition_metrics(
            None, missing, condition_id="c")["task_performance"]
        assert result["status"] == "na_missing_milestones"
        assert result["SR_comm"] is None
        assert result["MCR_comm"] is None
        assert result["AUP_comm"] is None


def test_e_step_normalization_and_zero_decision_step_na():
    inputs = _progress(["a", "b"], [_obs("a", 5.0)], decision_step_count=10)
    result = communication_condition_metrics(
        None, inputs, condition_id="c")["task_performance"]
    assert result["e_step"] == pytest.approx(0.05)
    zero = communication_condition_metrics(
        None, _progress(["a", "b"], [_obs("a", 5.0), _obs("b", 6.0)],
                        decision_step_count=0),
        condition_id="c")["task_performance"]
    assert zero["e_step"] is None
    assert zero["e_step_note"] == "zero_decision_step_success_na"
    assert zero["MCR_comm"] == 1.0
    absent = communication_condition_metrics(
        None, _progress(["a"], []), condition_id="c")["task_performance"]
    assert absent["e_step"] is None
    assert absent["e_step_note"] == "decision_steps_not_recorded"


def test_transport_companion_aggregates_three_pool_ledger():
    runtime = _mixed_runtime()
    report = runtime.report()
    result = communication_condition_metrics(report, None, condition_id="c")
    assert result["status"] == "computed"
    companion = result["transport_companion"]
    counts = companion["counts"]
    assert counts["offer_messages"] == 3
    assert counts["copies"] == 3
    assert counts["delivered"] == 1
    assert counts["expired_ttl"] == 1
    assert counts["dropped_channel_loss"] == 1
    assert companion["actual_loss_rate"] == pytest.approx(2 / 3)
    delays = companion["end_to_end_delay_s"]
    assert delays["count"] == 1 and delays["p50_s"] == pytest.approx(2.0)
    assert delays["max_s"] == pytest.approx(2.0)
    size = report["messages"]["m1"]["size_bytes"]
    assert companion["delivered_bits"] == size * BYTE_TO_BIT_FACTOR
    assert companion["throughput_delivered_bits_per_s"] == pytest.approx(
        size * BYTE_TO_BIT_FACTOR / 5.0)
    aoi = companion["aoi"]
    assert aoi["sample_count"] == 1
    assert aoi["time_mean_s"] is not None
    cost = result["communication_cost"]
    total = sum(report["messages"][mid]["size_bytes"]
                for mid in ("m1", "m2", "m3"))
    assert cost["B_offer_bits"] == total * BYTE_TO_BIT_FACTOR
    # TTL loss and channel loss still occupied the link.
    assert cost["B_link_bits"] == cost["B_offer_bits"]


def test_jitter_uses_successive_delivery_delays():
    runtime = CommunicationRuntime({"pools": {
        "backhaul_uplink": {"fixed_delay_s": 2.0}}})
    runtime.publish(message_id="m1", producer_id="robot_1",
                    recipients=["edge_1"], channel="observation",
                    estimate={"k": 1}, valid_until_s=20.0,
                    pool="backhaul_uplink")
    runtime.set_conditions({"fixed_delay_s": 4.0}, pool="backhaul_uplink")
    runtime.publish(message_id="m2", producer_id="robot_1",
                    recipients=["edge_1"], channel="observation",
                    estimate={"k": 2}, valid_until_s=20.0,
                    pool="backhaul_uplink")
    runtime.advance(10.0)
    companion = communication_condition_metrics(
        runtime.report(), None, condition_id="jitter")["transport_companion"]
    delays = companion["end_to_end_delay_s"]
    assert delays["count"] == 2
    assert delays["p50_s"] == pytest.approx(3.0)
    assert delays["p95_s"] == pytest.approx(3.9)
    assert delays["max_s"] == pytest.approx(4.0)
    assert companion["jitter_s"] == pytest.approx(2.0)


def test_b_offer_vs_b_link_broadcast_receiver_weighted():
    runtime = CommunicationRuntime({"pools": {"backhaul_uplink": {}}})
    runtime.publish(message_id="b1", producer_id="robot_1",
                    recipients=["edge_1", "edge_2", "edge_3"],
                    channel="observation", estimate={"k": 1},
                    valid_until_s=10.0, pool="backhaul_uplink")
    runtime.advance(5.0)
    cost = communication_condition_metrics(
        runtime.report(), None, condition_id="c")["communication_cost"]
    size = runtime.report()["messages"]["b1"]["size_bytes"]
    assert cost["B_offer_bits"] == size * BYTE_TO_BIT_FACTOR
    # Broadcast is charged once per admitted receiving copy.
    assert cost["B_link_bits"] == 3 * size * BYTE_TO_BIT_FACTOR
    assert cost["delivered_bits"] == 3 * size * BYTE_TO_BIT_FACTOR


def test_b_link_excludes_interface_rejection_but_keeps_loss_and_ttl():
    # buffer admits exactly one of two broadcast copies.
    overflow = CommunicationRuntime({"pools": {
        "backhaul_uplink": {"buffer_bytes": 350}}})
    overflow.publish(message_id="o1", producer_id="robot_1",
                     recipients=["edge_1", "edge_2"], channel="observation",
                     estimate={"blob": "x" * 200}, valid_until_s=10.0,
                     pool="backhaul_uplink")
    overflow.advance(5.0)
    overflow_report = overflow.report()
    cost = communication_condition_metrics(
        overflow_report, None, condition_id="c")["communication_cost"]
    size = overflow_report["messages"]["o1"]["size_bytes"]
    # B_offer charges the submitted payload once per message; only the one
    # admitted copy occupies the link, the rejected copy stays offer-only.
    assert cost["B_offer_bits"] == size * BYTE_TO_BIT_FACTOR
    assert cost["B_link_bits"] == size * BYTE_TO_BIT_FACTOR
    assert cost["interface_rejected_copies"] == 1
    assert cost["interface_rejected_bits"] == size * BYTE_TO_BIT_FACTOR

    ttl = CommunicationRuntime({"pools": {
        "backhaul_uplink": {"fixed_delay_s": 50.0}}})
    ttl.publish(message_id="t1", producer_id="robot_1", recipients=["edge_1"],
                channel="observation", estimate={"k": 1}, valid_until_s=10.0,
                pool="backhaul_uplink")
    ttl.advance(10.0)
    ttl_cost = communication_condition_metrics(
        ttl.report(), None, condition_id="c")["communication_cost"]
    assert ttl_cost["B_link_bits"] == ttl_cost["B_offer_bits"]

    channel = CommunicationRuntime({"pools": {"p2p": {"drop": True}}})
    channel.publish(message_id="c1", producer_id="robot_1",
                    recipients=["robot_2"], channel="p2p_relay",
                    estimate={"k": 1}, valid_until_s=10.0, pool="p2p")
    channel.advance(5.0)
    channel_cost = communication_condition_metrics(
        channel.report(), None, condition_id="c")["communication_cost"]
    assert channel_cost["B_link_bits"] == channel_cost["B_offer_bits"]


def test_excluded_message_ids_leave_all_totals():
    runtime = _mixed_runtime()
    result = communication_condition_metrics(
        runtime.report(), None, condition_id="c",
        excluded_message_ids=["m1"])
    counts = result["transport_companion"]["counts"]
    assert counts["offer_messages"] == 2
    assert counts["copies"] == 2
    assert counts["delivered"] == 0
    assert result["transport_companion"]["actual_loss_rate"] == 1.0
    cost = result["communication_cost"]
    total = sum(runtime.report()["messages"][mid]["size_bytes"]
                for mid in ("m2", "m3"))
    assert cost["B_offer_bits"] == total * BYTE_TO_BIT_FACTOR


def test_missing_communication_report_is_na_not_zero():
    result = communication_condition_metrics(
        None, _progress(["a"], [_obs("a", 5.0)]), condition_id="c")
    assert result["status"] == "na_missing_communication_report"
    companion = result["transport_companion"]
    assert companion["status"] == "na_missing_communication_report"
    assert companion["counts"] is None
    assert companion["actual_loss_rate"] is None
    cost = result["communication_cost"]
    assert cost["status"] == "na_missing_communication_report"
    assert cost["B_offer_bits"] is None
    assert cost["B_link_bits"] is None
    # Independent degradation: the task side still computed.
    assert result["task_performance"]["SR_comm"] == 1.0


def test_retention_curve_first_drop_brackets_without_extrapolation():
    curve = retention_curve({"d0": 0.9, "d1": 0.8, "d2": 0.4, "d4": 0.2},
                            reference_condition="d0")
    assert curve["status"] == "ok"
    assert curve["reference"] == {"condition": "d0", "performance": 0.9,
                                  "valid": True}
    rows = {row["condition"]: row["retention"] for row in curve["rows"]}
    assert rows["d0"] == 1.0
    assert rows["d1"] == pytest.approx(0.8 / 0.9)
    x90 = curve["thresholds"]["0.9"]
    assert x90["status"] == "bracketed"
    assert (x90["x_lower_condition"], x90["x_upper_condition"]) == ("d0", "d1")
    x50 = curve["thresholds"]["0.5"]
    assert x50["status"] == "bracketed"
    assert (x50["x_lower_condition"], x50["x_upper_condition"]) == ("d1", "d2")
    assert curve["no_extrapolation"] is True

    held = retention_curve({"d0": 0.9, "d1": 0.85, "d2": 0.82},
                           reference_condition="d0")
    assert held["thresholds"]["0.5"]["status"] == "above_tested_maximum"
    assert held["thresholds"]["0.5"]["x_upper_bound_condition"] == "d2"


def test_retention_curve_reference_gates_ordering_and_unknown_rows():
    gated = retention_curve({"d0": 0.4, "d1": 0.2}, reference_condition="d0",
                            min_reference=0.5)
    assert gated["status"] == "reference_below_minimum"
    assert all(row["retention"] is None for row in gated["rows"])
    assert gated["thresholds"]["0.9"]["status"] == "reference_below_minimum"

    missing = retention_curve({"d0": None, "d1": 0.2},
                              reference_condition="d0")
    assert missing["status"] == "na_reference_missing"

    # Unknown-M conditions never act as bracket endpoints.
    sparse = retention_curve({"d0": 0.9, "d1": None, "d2": 0.4},
                             reference_condition="d0")
    bracket = sparse["thresholds"]["0.9"]
    assert bracket["status"] == "bracketed"
    assert (bracket["x_lower_condition"],
            bracket["x_upper_condition"]) == ("d0", "d2")

    with pytest.raises(ValueError, match="lowest-severity"):
        retention_curve({"d1": 0.5, "d0": 0.9}, reference_condition="d0")


def test_capacity_thresholds_use_task_performance():
    result = capacity_thresholds(
        {None: 0.9, 8000.0: 0.88, 4000.0: 0.5, 2000.0: 0.3}, c0_bps=8000.0)
    assert result["status"] == "ok"
    assert result["measurement_scope"] == "task_performance_communication_capacity"
    points = {point["capacity_bits_per_s"]: point for point in result["points"]}
    # Retention comes from the supplied task performance M, not from any
    # synthetic delivery-rate diagnostic.
    assert points[8000.0]["retention"] == pytest.approx(0.88 / 0.9)
    assert points[4000.0]["retention"] == pytest.approx(0.5 / 0.9)
    assert points[4000.0]["capacity_ratio_c0"] == pytest.approx(0.5)
    c90 = result["thresholds"]["0.9"]
    assert c90["status"] == "bracketed"
    assert (c90["capacity_lower_bits_per_s"],
            c90["capacity_upper_bits_per_s"]) == (4000.0, 8000.0)
    c50 = result["thresholds"]["0.5"]
    assert c50["status"] == "bracketed"
    assert (c50["capacity_lower_bits_per_s"],
            c50["capacity_upper_bits_per_s"]) == (2000.0, 4000.0)
    assert result["reference"]["capacity_mode"] == "unlimited"
    assert result["no_extrapolation"] is True


def test_capacity_thresholds_edge_statuses_and_boundary():
    floor = capacity_thresholds({None: 1.0, 2000.0: 0.95, 1000.0: 0.92},
                                c0_bps=4000.0)
    c90 = floor["thresholds"]["0.9"]
    assert c90["status"] == "at_or_below_tested_minimum"
    assert c90["capacity_upper_bound_bits_per_s"] == 1000.0

    ceiling = capacity_thresholds({None: 1.0, 2000.0: 0.5}, c0_bps=4000.0)
    above = ceiling["thresholds"]["0.9"]
    assert above["status"] == "above_tested_maximum"
    assert above["capacity_lower_bound_bits_per_s"] == 2000.0

    boundary = capacity_thresholds({None: 0.8, 1000.0: 0.72}, c0_bps=4000.0)
    exact = boundary["thresholds"]["0.9"]
    assert exact["status"] == "at_or_below_tested_minimum"
    assert boundary["points"][0]["retention"] == pytest.approx(0.9)

    missing = capacity_thresholds({1000.0: 0.5}, c0_bps=4000.0)
    assert missing["status"] == "missing_reference"
    gated = capacity_thresholds({None: 0.3, 1000.0: 0.2}, c0_bps=4000.0,
                                min_reference=0.5)
    assert gated["status"] == "reference_below_minimum"
    assert gated["reference"]["valid"] is False


def test_communication_dependency_gated_by_m_min():
    computed = communication_dependency(0.8, 0.4, m_min=0.5)
    assert computed["status"] == "computed"
    assert computed["D_comm"] == pytest.approx(0.5)
    assert computed["absolute_difference"] == pytest.approx(0.4)
    assert computed["reference_valid"] is True
    assert computed["diagnostic_only"] is True
    assert computed["not_communication_score"] is True

    gated = communication_dependency(0.4, 0.2, m_min=0.5)
    assert gated["status"] == "reference_below_minimum"
    assert gated["D_comm"] is None
    assert gated["reference_valid"] is False

    missing = communication_dependency(None, 0.2, m_min=0.5)
    assert missing["status"] == "na_missing_performance"
    assert missing["D_comm"] is None
    zero = communication_dependency(0.0, 0.0, m_min=0.0)
    assert zero["status"] == "na_zero_reference_performance"
    assert zero["D_comm"] is None


def test_distribution_contrast_jp_bp_default_orientation():
    rows = []
    for base, fixed, jitter in (("i1", .8, .6), ("i2", .7, .5)):
        for channel in ("s1", "s2"):
            rows.extend([
                {"base_instance_id": base, "channel_seed": channel,
                 "comparison_level": "mean-1", "condition": "fixed_delay",
                 "metric": "MCR_comm", "metric_value": fixed},
                {"base_instance_id": base, "channel_seed": channel,
                 "comparison_level": "mean-1", "condition": "jitter",
                 "metric": "MCR_comm", "metric_value": jitter},
            ])
    jp = distribution_contrast(rows, metric="MCR_comm", bootstrap_samples=50)
    assert jp["protocol_effect"] == "JP"
    assert (jp["left_condition"], jp["right_condition"]) == (
        "fixed_delay", "jitter")
    effect = jp["effects"][0]
    assert effect["status"] == "ok"
    assert effect["n_base_instances"] == 2
    assert effect["n_pairs"] == 4
    assert effect["mean_difference_right_minus_left"] == pytest.approx(-0.2)
    assert effect["bootstrap_ci95"][0] <= effect["bootstrap_ci95"][1]

    bp_rows = [
        {"base_instance_id": "i1", "channel_seed": "s1",
         "comparison_level": "loss-0.2", "condition": "iid_loss",
         "metric": "SR_comm", "metric_value": .8},
        {"base_instance_id": "i1", "channel_seed": "s1",
         "comparison_level": "loss-0.2", "condition": "burst_loss",
         "metric": "SR_comm", "metric_value": .5},
    ]
    bp = distribution_contrast(bp_rows, metric="SR_comm",
                               bootstrap_samples=10)
    assert bp["protocol_effect"] == "BP"
    assert bp["effects"][0]["mean_difference_right_minus_left"] == (
        pytest.approx(-0.3))


def test_distribution_contrast_explicit_missing_pair_and_duplicates():
    rows = [
        {"base_instance_id": "i1", "channel_seed": "s1",
         "comparison_level": "L1", "condition": "flat",
         "metric_value": .9},
        {"base_instance_id": "i1", "channel_seed": "s1",
         "comparison_level": "L1", "condition": "shaped",
         "metric_value": .6},
        # L2 has no complete pair (shaped leg absent).
        {"base_instance_id": "i1", "channel_seed": "s1",
         "comparison_level": "L2", "condition": "flat",
         "metric_value": .8},
    ]
    result = distribution_contrast(rows, metric="AUP_comm",
                                   left_condition="flat",
                                   right_condition="shaped",
                                   bootstrap_samples=10)
    assert result["protocol_effect"] is None
    by_level = {row["comparison_level"]: row for row in result["effects"]}
    assert by_level["L1"]["mean_difference_right_minus_left"] == (
        pytest.approx(-0.3))
    assert by_level["L2"]["status"] == "missing_pair"
    assert by_level["L2"]["mean_difference_right_minus_left"] is None
    assert result["no_unmatched_pair_inference"] is True

    with pytest.raises(ValueError, match="duplicate"):
        distribution_contrast(rows + [rows[0]], metric="AUP_comm",
                              left_condition="flat", right_condition="shaped")
    with pytest.raises(ValueError, match="outside the requested pair"):
        distribution_contrast(rows, metric="AUP_comm",
                              left_condition="flat", right_condition="other")
    mismatched = [dict(rows[0], metric="other_metric")]
    with pytest.raises(ValueError, match="different metric"):
        distribution_contrast(mismatched, metric="AUP_comm",
                              left_condition="flat", right_condition="shaped")
    unknown_pair = [dict(rows[0]), dict(rows[1])]
    with pytest.raises(ValueError, match="known condition pair"):
        distribution_contrast(unknown_pair, metric="AUP_comm")


def test_release_flags_false_on_all_outputs():
    runtime = _mixed_runtime()
    outputs = [
        communication_condition_metrics(
            runtime.report(),
            _progress(["a"], [_obs("a", 5.0)]), condition_id="c"),
        retention_curve({"d0": 1.0, "d1": 0.8}, reference_condition="d0"),
        capacity_thresholds({None: 1.0, 100.0: 0.8}, c0_bps=200.0),
        communication_dependency(0.9, 0.3, m_min=0.5),
        distribution_contrast([
            {"base_instance_id": "i", "channel_seed": "s",
             "comparison_level": "L", "condition": "fixed_delay",
             "metric_value": 1.0},
            {"base_instance_id": "i", "channel_seed": "s",
             "comparison_level": "L", "condition": "jitter",
             "metric_value": 0.9},
        ], metric="MCR_comm"),
    ]
    for output in outputs:
        for flag in _RELEASE_FLAGS:
            assert output[flag] is False
