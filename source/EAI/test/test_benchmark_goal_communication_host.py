"""Host-level integration of the companion communication tool (P2 phase 2).

Drives ``GoalAlgorithmHost`` with an opt-in adapter through the acceptance
chain from the frozen interface design §4.2: publish tool call → delivery
observation under a restricted pool → frozen ``consume_message`` action →
the message id referenced as ``dispatch_skill`` evidence.  Adapters without
the initialization hook must see no new events whether the companion is
configured or not.
"""

from __future__ import annotations

import pytest

from demo.benchmark.communication import (
    CommunicationRuntime,
    validate_communication_report,
)
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_communication_tool import GoalCommunicationTools
from demo.benchmark.goal_driven_tasks import build_goal_task_package


@pytest.fixture(scope="module")
def bundle():
    return build_goal_task_package("warehouse_nvd")


def _communication_tools(bundle, *, pools=None):
    ids = [row["id"] for row in bundle["public_task"]["candidate_robot_pool"]]
    return GoalCommunicationTools(
        bundle["public_task"], episode_id="comm-host",
        endpoints={"edges": {"edge_1": [ids[0]]}, "robots": ids[:3]},
        runtime=CommunicationRuntime(pools))


def _tool_call(call_id, *, producer, recipient, now=0.0):
    return {
        "schema": "eai.benchmark.goal_communication_tool_call",
        "version": 1,
        "call_id": call_id,
        "submitted_at_s": now,
        "operation": "publish",
        "arguments": {
            "producer_id": producer,
            "recipients": [recipient],
            "channel": "relay",
            "estimate": {"note": "handoff"},
            "valid_until_s": now + 30.0,
        },
    }


def _goal_action(action_id, action_type, payload, *, now=0.0):
    return {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": action_id, "submitted_at_s": now,
        "type": action_type, "payload": payload,
    }


class BaseAdapter:
    """Legacy adapter without any companion hook; action script is mutable."""

    def __init__(self, bundle):
        self.bundle = bundle
        self.actions: list = []
        self.seen: list = []
        self.profiles: list = []
        self.act_turns = 0

    def manifest(self):
        return {
            "kind": "participant", "id": "comm-host-adapter", "version": 1,
            "interface": "eai.benchmark.goal_algorithm_adapter",
            "interface_version": 1, "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        pass

    def act(self, events):
        self.act_turns += 1
        self.seen.extend(events)
        actions, self.actions = list(self.actions), []
        return actions

    def close(self):
        pass


class CompanionAdapter(BaseAdapter):
    """Opt-in adapter: adds exactly the initialization hook."""

    def initialize_communication_tools(self, profile):
        self.profiles.append(profile)


def _team(bundle):
    return [row["id"] for row in bundle["public_task"]["candidate_robot_pool"][:2]]


def test_publish_delivery_consume_and_dispatch_evidence_chain(bundle):
    from demo.benchmark.goal_session import GoalSession

    tools = _communication_tools(
        bundle, pools={"pools": {"p2p": {"capacity_bps": 5000.0}}})
    adapter = CompanionAdapter(bundle)
    ids = _team(bundle)
    session = GoalSession(bundle["public_task"], bundle["participant_api"],
                          episode_id="comm-host")
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"],
                           adapter, session=session,
                           communication_tools=tools) as host:
        # The opt-in hook is the only path that receives the profile.
        assert adapter.profiles and adapter.profiles[0]["optional"] is True
        # episode_started is delivered on the first request_actions, so the
        # action script must be queued before that call.
        adapter.actions = [
            _goal_action("select-1", "select_team", {"agent_ids": ids})]
        host.request_actions()
        adapter.actions = [
            _tool_call("comm-1", producer=f"robot:{ids[0]}",
                       recipient=f"robot:{ids[1]}")]
        results = host.request_actions()
        assert results[0]["accepted"] is True
        assert results[0]["message_id"] == "comm-host:comm:0"
        message_id = results[0]["message_id"]
        # The observation becomes participant-visible on the next turn only.
        assert not any(event["type"] == "observation" for event in adapter.seen)
        # Advance the trusted session clock past the pool service window so
        # the pump can process the delivery event; the frozen consume verb is
        # queued now because the delivery turn is the adapter's next act().
        host.emit("tick", 1.0, {"state_digest": "comm-delivery-window"})
        # Plan validation at time > 0 requires a complete trusted snapshot.
        objects = {row["id"]: {
            "region_id": row["physical_properties"]["initial_region_id"]}
            for row in bundle["public_task"]["public_world"]["objects"]}
        pool = {row["id"]: row for row in
                bundle["public_task"]["candidate_robot_pool"]}
        regions = {row["id"]: set(row["access_modes"])
                   for row in bundle["public_task"]["public_world"]["regions"]}
        def allows(modes, mobility):
            if mobility == "aerial":
                return "aerial_navigation" in modes
            return bool(modes & {"ground_navigation", "stair_traversal"})

        positions = {agent: next(region for region, modes in regions.items()
                                 if allows(modes, pool[agent]["mobility"]))
                     for agent in ids}
        # The dispatched route starts at the consolidation area; keep the
        # assigned ground robot there for region continuity.
        positions[ids[1]] = "consolidation_area"
        session.update_runtime_state(1.0, positions, objects)
        adapter.actions = [
            _goal_action("consume-1", "consume_message",
                         {"message_id": message_id}, now=1.0)]
        host.request_actions()
        delivered = [event for event in adapter.seen
                     if event["type"] == "observation"
                     and event["payload"]["kind"] == "communication_message"]
        assert [row["payload"]["message_id"] for row in delivered] == [
            "comm-host:comm:0"]
        assert message_id in host.trusted_report()["session"]["consumed_message_ids"]
        task = {
            "task_id": "nav-handoff", "skill": "ground_navigation",
            "arguments": {"region_id": "rack_aisle_a",
                          "route_ids": ["consolidation_to_rack_a"]},
            "required_capabilities": ["ground_navigation"],
            "assigned_agents": [ids[1]],
            "prerequisites": [], "evidence_refs": [],
            "execution": {"start_s": 1.0, "duration_s": 8.0},
        }
        plan = {"schema": "eai.benchmark.goal_plan", "version": 3,
                "plan_id": "plan-relay", "assigned_team": list(ids),
                "tasks": [task]}
        # The consume result event drives the next turn: submit the plan and
        # dispatch referencing the consumed message as evidence in one batch.
        adapter.actions = [
            _goal_action("plan-1", "submit_plan", {"plan": plan}, now=1.0),
            _goal_action("dispatch-1", "dispatch_skill",
                         {"task_id": "nav-handoff",
                          "evidence_refs": [message_id]}, now=1.0)]
        results = host.request_actions()
        assert results[-1]["accepted"] is True
        # The dispatch action archives the consumed message id as evidence.
        dispatch = next(row for row in
                        host.trusted_report()["session"]["actions"]
                        if row["action_id"] == "dispatch-1")
        assert message_id in dispatch["payload"]["evidence_refs"]


def test_transport_ledger_replays_and_directions_are_complete(bundle):
    tools = _communication_tools(
        bundle, pools={"pools": {"p2p": {"capacity_bps": 5000.0}}})
    adapter = CompanionAdapter(bundle)
    ids = _team(bundle)
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"],
                           adapter, communication_tools=tools):
        pass
    report = tools.communication_report()
    assert report["messages"] == {}
    assert validate_communication_report(report)["validated"] is True


def test_adapter_without_hook_is_unchanged_with_companion_on_or_off(bundle):
    """A legacy adapter runs one identical real episode either way.

    Both configurations bind an explicit episode ledger and drive five turns
    that each deliver new events, so every queued goal action (select_team,
    submit_plan, dispatch_skill, consume_message) is genuinely submitted to
    ``GoalSession`` instead of being skipped by an empty-event early return.
    The complete delivered event stream, the accepted action results (each
    carrying its ``action_result`` evidence) and the trusted session state are
    compared across the companion-off and companion-on runs.
    """
    from copy import deepcopy

    from demo.benchmark.goal_session import GoalSession

    def run(with_tools):
        session = GoalSession(bundle["public_task"], bundle["participant_api"],
                              episode_id="comm-host")
        tools = _communication_tools(bundle) if with_tools else None
        adapter = BaseAdapter(bundle)
        plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
        host = GoalAlgorithmHost(bundle["public_task"],
                                 bundle["participant_api"], adapter,
                                 session=session,
                                 communication_tools=tools)
        with host:
            # Turn 1 delivers episode_started; select_team is executed now.
            adapter.actions = [
                _goal_action("select-1", "select_team",
                             {"agent_ids": plan["assigned_team"]})]
            results = [host.request_actions()]
            # Turn 2 delivers the select_team action_result; submit the
            # witness plan so the ledger owns a real current plan.
            adapter.actions = [
                _goal_action("plan-1", "submit_plan", {"plan": plan})]
            results.append(host.request_actions())
            # Turn 3 delivers the submit_plan action_result; dispatch the
            # plan's first task, producing a pending runtime request.
            adapter.actions = [
                _goal_action("dispatch-1", "dispatch_skill",
                             {"task_id": plan["tasks"][0]["task_id"],
                              "evidence_refs": []})]
            results.append(host.request_actions())
            # Turn 4 delivers the dispatch action_result plus one trusted
            # observation, which the adapter consumes like any public message.
            host.emit("observation", 1.0, {
                "message_id": "invariance-observation-1",
                "producer_id": plan["assigned_team"][0],
                "kind": "incident_update",
                "data": {"status": "visible"},
                "valid_until_s": 10.0,
            })
            adapter.actions = [
                _goal_action("consume-1", "consume_message",
                             {"message_id": "invariance-observation-1"},
                             now=1.0)]
            results.append(host.request_actions())
            # Turn 5 delivers the consume_message action_result; the adapter
            # sees it and returns no further action.
            results.append(host.request_actions())
            # With no undelivered events left the next request must not wake
            # the adapter, whether or not an unconnected companion exists.
            idle_results = host.request_actions()
            turns_after_idle = adapter.act_turns
            trusted = host.trusted_report()
        return {
            "events": deepcopy(adapter.seen),
            "results": results,
            "idle_results": idle_results,
            "act_turns": adapter.act_turns,
            "turns_after_idle": turns_after_idle,
            "executed": [(turn[0]["type"], turn[0]["accepted"])
                         for turn in results if turn],
            "hooks": hasattr(adapter, "initialize_communication_tools"),
            "profiles": list(adapter.profiles),
            "failed": trusted["failed"],
            "selected_team": trusted["session"]["selected_team"],
            "current_plan_id": trusted["session"]["current_plan_id"],
            "task_statuses": {task_id: row["status"] for task_id, row
                              in trusted["session"]["tasks"].items()},
            "consumed": trusted["session"]["consumed_message_ids"],
        }

    off = run(False)
    on = run(True)

    # The legacy four-method adapter was never asked for anything new.
    assert off["hooks"] is False and on["hooks"] is False
    assert off["profiles"] == [] and on["profiles"] == []
    assert off["failed"] is False and on["failed"] is False
    # Actions were really executed: four accepted turns, one act() per turn.
    expected = [("select_team", True), ("submit_plan", True),
                ("dispatch_skill", True), ("consume_message", True)]
    assert off["executed"] == expected
    assert on["executed"] == expected
    # Five event-delivered turns (the consume action_result closes the stream);
    # the final idle request wakes nobody on either side.
    assert off["act_turns"] == on["act_turns"] == 5
    assert off["turns_after_idle"] == on["turns_after_idle"] == 5
    # No companion observations and no idle wake-ups on either side.
    for side in (off, on):
        assert not any(event["type"] == "observation"
                       and event["payload"].get("kind") == "communication_message"
                       for event in side["events"])
        assert side["idle_results"] == []
    # Complete delivered event streams (including action_result frames with
    # sequence numbers) and accepted action results are identical.
    assert off["events"] == on["events"]
    assert off["results"] == on["results"]
    # The trusted ledger state reached the same point in both runs.
    assert off["selected_team"] == on["selected_team"]
    assert off["current_plan_id"] == on["current_plan_id"]
    assert off["task_statuses"] == on["task_statuses"]
    first_task_id = (bundle["hidden_evaluator"]["witness_plans"][0]
                     ["plan"]["tasks"][0]["task_id"])
    assert off["task_statuses"][first_task_id] == "pending"
    assert off["consumed"] == on["consumed"] == ["invariance-observation-1"]


def test_tool_call_without_the_hook_fails_closed(bundle):
    tools = _communication_tools(bundle)
    adapter = BaseAdapter(bundle)
    ids = _team(bundle)
    host = GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"],
                             adapter, communication_tools=tools)
    with host:
        adapter.actions = [
            _tool_call("comm-x", producer=f"robot:{ids[0]}",
                       recipient=f"robot:{ids[1]}")]
        with pytest.raises(ValueError,
                           match="communication tool interface was not connected"):
            host.request_actions()
    assert host.trusted_report()["failed"] is True


def test_goal_action_id_colliding_with_a_communication_call_is_rejected(bundle):
    tools = _communication_tools(bundle)
    adapter = CompanionAdapter(bundle)
    ids = _team(bundle)
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"],
                           adapter, communication_tools=tools) as host:
        adapter.actions = [
            _tool_call("comm-1", producer=f"robot:{ids[0]}",
                       recipient=f"robot:{ids[1]}")]
        host.request_actions()
        adapter.actions = [
            _goal_action("comm-1", "select_team", {"agent_ids": ids})]
        with pytest.raises(ValueError, match="collides with a communication call"):
            host.request_actions()


def test_comm_call_colliding_with_goal_action_lands_in_service_ledger(bundle):
    # Batch-one decision 4: goal-action collisions are rejected at the
    # service boundary so the stable ``call_id_collision`` code is archived
    # in the service call ledger, not only raised by the host.
    from demo.benchmark.goal_communication_tool import GoalCommunicationToolError

    tools = _communication_tools(bundle)
    adapter = CompanionAdapter(bundle)
    ids = _team(bundle)
    with GoalAlgorithmHost(bundle["public_task"], bundle["participant_api"],
                           adapter, communication_tools=tools) as host:
        adapter.actions = [
            _goal_action("goal-act-1", "select_team", {"agent_ids": ids})]
        host.request_actions()
        adapter.actions = [
            _tool_call("goal-act-1", producer=f"robot:{ids[0]}",
                       recipient=f"robot:{ids[1]}")]
        with pytest.raises(GoalCommunicationToolError) as excinfo:
            host.request_actions()
        assert excinfo.value.error_code == "call_id_collision"
        calls = tools.report()["calls"]
        rejected = [row for row in calls if row.get("call_id") == "goal-act-1"]
        assert rejected and rejected[0]["status"] == "rejected"
        assert rejected[0]["error_code"] == "call_id_collision"
