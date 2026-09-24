import json


def test_shared_transport_is_authoritative_replayed_and_counted_once(tmp_path):
    from demo.benchmark.communication import CommunicationRuntime, validate_communication_report
    from demo.benchmark.goal_cost_evidence import build_cost_evidence

    runtime = CommunicationRuntime({"pools": {
        "backhaul_downlink": {"capacity_bps": 1000000},
        "backhaul_uplink": {"capacity_bps": 1000000}}})
    for index, direction in enumerate(("downlink", "uplink")):
        runtime.publish(message_id=f"command-{index}", producer_id="cloud",
                        recipients=["robot-a"], channel="execution", estimate={"body": index},
                        valid_until_s=10, pool=f"backhaul_{direction}", direction=direction)
    runtime.advance(1.0)
    report = runtime.report()
    _write(tmp_path, "communication_transport_report.json", report)
    _write(tmp_path, "communication_transport_replay.json", validate_communication_report(report))
    # An older partial tool view must neither double count nor invalidate the
    # authoritative transport, even when its byte count is different.
    _write(tmp_path, "pose_tools_report.json", {"communication": {
        "ledger": [{"event": "bytes_served", "bytes": 9999999}]}})
    _write(tmp_path, "pose_communication_replay.json", {"validated": True})
    result = build_cost_evidence(tmp_path)["communication"]
    expected = sum(row["bytes"] for row in report["ledger"] if row["event"] == "bytes_served")
    assert result["total_bytes"] == expected
    assert sum(result["by_direction_bytes"].values()) == expected
    assert result["reliable_source_count"] == 1
    assert result["aggregation"] == "shared_transport_counted_once"
    next(row for row in report["ledger"] if row["event"] == "bytes_served")["bytes"] += 1
    _write(tmp_path, "communication_transport_report.json", report)
    assert build_cost_evidence(tmp_path)["communication"]["total_bytes"] is None
    (tmp_path / "communication_transport_report.json").unlink()
    assert build_cost_evidence(tmp_path)["communication"]["total_bytes"] is None


def _write(root, name, value):
    (root / name).write_text(json.dumps(value), encoding="utf-8")


def test_raw_cost_evidence_keeps_unavailable_sources_null(tmp_path):
    from demo.benchmark.goal_cost_evidence import build_cost_evidence

    result = build_cost_evidence(tmp_path)

    assert result["schema"] == "eai.benchmark.goal_raw_cost_evidence"
    assert result["version"] == 1
    assert result["clock_source"] == "goal_host_receipt_clock"
    assert result["clock_valid"] is False
    assert result["clock"]["valid"] is False
    assert result["clock"]["elapsed_s"] is None
    assert result["accepted_actions"]["total_count"] is None
    assert result["communication"]["total_bytes"] is None
    assert result["activation_cost_proxy"]["value"] is None
    assert result["planning_compute_cost"]["total_s"] is None
    assert result["validity"]["scoreable"] is False
    assert result["validity"]["execution_validated"] is False
    assert result["validity"]["business_outcomes_validated"] is False


def test_raw_cost_evidence_joins_trusted_clock_actions_proxy_and_replayed_bytes(tmp_path):
    from demo.benchmark.goal_cost_evidence import build_raw_cost_evidence

    _write(tmp_path, "goal_session_report.json", {
        "episode_id": "episode-1",
        "report": {
            "current_time_s": 4.5,
            "actions": [
                {"type": "select_team", "action_id": "a1"},
                {"type": "submit_plan", "action_id": "a2"},
                {"type": "dispatch_skill", "action_id": "a3"},
                {"type": "dispatch_skill", "action_id": "a4"},
            ],
            "planning_compute_cost_s": 0.25,
            "replanning_compute_cost_s": 0.75,
        },
    })
    _write(tmp_path, "team_usage.json", {
        "activation_cost_proxy": 2.5,
        "activation_cost_source": "candidate_pool_design.activation_cost",
    })
    _write(tmp_path, "pose_tools_report.json", {
        "communication": {
            "ledger": [
                {"event": "bytes_served", "bytes": 12,
                 "pool": "backhaul_uplink", "direction": "up"},
                {"event": "bytes_served", "bytes": 8,
                 "pool": "p2p", "direction": "down"},
                {"event": "delivered", "bytes": 999},
            ],
        },
    })
    _write(tmp_path, "pose_communication_replay.json", {"validated": True})

    result = build_raw_cost_evidence(tmp_path)

    assert result["clock"] == {
        "source": "goal_host_receipt_clock", "start_s": 0.0,
        "end_s": 4.5, "elapsed_s": 4.5, "valid": True,
    }
    assert result["clock_source"] == "goal_host_receipt_clock"
    assert result["clock_valid"] is True
    assert result["accepted_actions"] == {
        "counts_by_type": {"dispatch_skill": 2, "select_team": 1, "submit_plan": 1},
        "total_count": 4,
        "source": "goal_session.actions",
    }
    assert result["communication"]["total_bytes"] == 20
    assert result["communication"]["by_pool_bytes"] == {
        "backhaul_uplink": 12, "p2p": 8,
    }
    assert result["communication"]["by_direction_bytes"] == {"down": 8, "up": 12}
    assert result["activation_cost_proxy"] == {
        "value": 2.5,
        "source": "candidate_pool_design.activation_cost",
        "is_proxy": True,
        "valid": True,
    }
    assert result["planning_compute_cost"] == {
        "planning_s": 0.25, "replanning_s": 0.75, "total_s": 1.0,
        "source": "trusted_host_scheduler", "valid": True,
    }
    assert result["validity"]["communication_ledger_reliable"] is True


def test_unreplayed_or_malformed_communication_is_not_zero_cost(tmp_path):
    from demo.benchmark.goal_cost_evidence import build_raw_cost_evidence

    _write(tmp_path, "goal_session_report.json", {
        "report": {"current_time_s": 1.0, "actions": []},
    })
    _write(tmp_path, "pose_tools_report.json", {
        "communication": {"ledger": [
            {"event": "bytes_served", "bytes": 10},
        ]},
    })
    _write(tmp_path, "pose_communication_replay.json", {"validated": False})
    _write(tmp_path, "camera_tools_report.json", {"schema": "broken"})
    _write(tmp_path, "team_usage.json", {"activation_cost_proxy": 0})

    result = build_raw_cost_evidence(tmp_path)

    assert result["communication"]["total_bytes"] is None
    assert result["communication"]["valid"] is False
    assert result["communication"]["invalid_source_count"] == 2
    assert result["activation_cost_proxy"]["value"] is None
    assert result["accepted_actions"]["total_count"] == 0


def test_generic_communication_report_is_supported(tmp_path):
    from demo.benchmark.goal_cost_evidence import build_cost_evidence

    _write(tmp_path, "goal_session_report.json", {
        "report": {"current_time_s": 2.0, "actions": []},
    })
    _write(tmp_path, "communication_report.json", {
        "schema": "eai.benchmark.communication",
        "ledger": [{"event": "bytes_served", "bytes": 7,
                     "pool": "backhaul_downlink"}],
    })
    _write(tmp_path, "communication_replay.json", {"validated": True})

    result = build_cost_evidence(tmp_path)

    assert result["communication"]["total_bytes"] == 7
    assert result["communication"]["by_pool_bytes"] == {"backhaul_downlink": 7}


def test_direction_breakdown_fails_closed_when_pool_row_omits_direction(tmp_path):
    from demo.benchmark.goal_cost_evidence import build_raw_cost_evidence

    _write(tmp_path, "goal_session_report.json", {
        "report": {"current_time_s": 2.0, "actions": []},
    })
    _write(tmp_path, "communication_report.json", {
        "schema": "eai.benchmark.communication",
        "ledger": [{"event": "bytes_served", "bytes": 7,
                     "pool": "backhaul_uplink"}],
    })
    _write(tmp_path, "communication_replay.json", {"validated": True})

    result = build_raw_cost_evidence(tmp_path)

    assert result["communication"]["total_bytes"] == 7
    assert result["communication"]["by_pool_bytes"] == {"backhaul_uplink": 7}
    assert result["communication"]["by_direction_bytes"] is None
    assert result["communication"]["direction_complete"] is False
    assert result["communication"]["valid"] is True


def test_episode_mismatch_invalidates_explicitly_bound_transport(tmp_path):
    from demo.benchmark.goal_cost_evidence import build_raw_cost_evidence

    _write(tmp_path, "pose_tools_report.json", {
        "episode_id": "other-episode",
        "communication": {"ledger": [{"event": "bytes_served", "bytes": 4,
                                        "pool": "backhaul_uplink", "direction": "up"}]},
    })
    _write(tmp_path, "pose_communication_replay.json", {"validated": True})

    result = build_raw_cost_evidence(tmp_path, episode_id="expected-episode")

    assert result["communication"]["total_bytes"] is None
    assert result["communication"]["valid"] is False
    assert result["communication"]["invalid_source_count"] == 1


def test_mirrored_transport_archives_are_counted_once(tmp_path):
    from demo.benchmark.goal_cost_evidence import build_raw_cost_evidence

    communication = {
        "schema": "eai.benchmark.communication", "version": 2,
        "messages": {}, "time_s": 0, "contract": {}, "initial_contract": {},
        "ledger": [{"event": "bytes_served", "bytes": 9,
                     "pool": "p2p", "direction": "up"}],
    }
    report = {"episode_id": "episode-1", "communication": communication}
    replay = {"validated": True}
    _write(tmp_path, "pose_tools_report.json", report)
    _write(tmp_path, "pose_communication_replay.json", replay)
    _write(tmp_path, "communication_report.json", communication)
    _write(tmp_path, "communication_replay.json", replay)

    result = build_raw_cost_evidence(tmp_path, episode_id="episode-1")

    assert result["communication"]["total_bytes"] == 9
    assert result["communication"]["reliable_source_count"] == 1
