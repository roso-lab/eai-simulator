from pathlib import Path

from benchmark.hetero_diagnostic_runtime_v1 import (
    CommunicationModel,
    DiagnosticRuntime,
    EpisodeSpec,
    GreedyGoalAlgorithm,
    ResourceSpec,
    RobotSpec,
    SemanticEdge,
    SemanticGraph,
    SemanticNode,
    TaskSpec,
)


def graph():
    return SemanticGraph(
        [SemanticNode("g0", "ground", semantics=("start",)), SemanticNode("g1", "ground", semantics=("goal",)), SemanticNode("g2", "ground", semantics=("recover",))],
        [SemanticEdge("g0", "g1", 1.0), SemanticEdge("g1", "g2", 1.0)],
    )


def episode(*, resources=(), tasks=None, condition="C0"):
    return EpisodeSpec(graph(), (RobotSpec("r0", "ground", "g0", speed_mps=10.0),), tuple(tasks or (TaskSpec("t0", "inspection", ("goal",)),)), tuple(resources), horizon_ticks=100, communication_condition=condition)


def test_graph_shortest_and_semantic_binding():
    g = graph()
    assert g.resolve("goal") == ("g1",)
    assert g.shortest("g0", "g2") == (2.0, ["g0", "g1", "g2"])


def test_resource_capacity_queue_and_contention():
    runtime = DiagnosticRuntime(episode(resources=(ResourceSpec("charger", "g1", capacity=1, service_ticks=3),), tasks=(TaskSpec("t0", "inspection", ("goal",), resource_id="charger"),)))
    result = runtime.run()
    assert result["success"] is True
    assert result["recover_services"] == 1


def test_communication_conditions_meter_loss_and_delay():
    comm = CommunicationModel("C1")
    comm.send(message_id="m", sender_id="a", receiver_id="b", kind="state", payload={"x": 1}, tick=0)
    comm.advance(3)
    assert comm.receive("b", 3)
    assert comm.metrics()["delay_ticks_total"] >= 2


def test_checkpoint_roundtrip_and_branch_isolation(tmp_path: Path):
    runtime = DiagnosticRuntime(episode(), algorithm=GreedyGoalAlgorithm())
    runtime.step()
    receipt = runtime.checkpoint()
    branch = runtime.fork(receipt)
    branch.step()
    assert branch.clock_tick != runtime.clock_tick
    runtime.restore(receipt["snapshot"])
    assert runtime.clock_tick == receipt["tick"]
    artifact = tmp_path / "cp"
    stored = runtime.checkpoint(artifact, metadata={"schema": runtime.schema_version, "tick": str(runtime.clock_tick)})
    restored = DiagnosticRuntime(episode())
    restored.restore_checkpoint(artifact, digest=stored["artifact_digest"], metadata=stored["metadata"])
    assert restored.snapshot() == runtime.snapshot()


def test_run_metrics_and_replay_digest():
    runtime = DiagnosticRuntime(episode())
    result = runtime.run()
    assert result["success"] is True
    assert result["task_completion_rate"] == 1.0
    assert result["replay_equivalent"] is False
    replay = runtime.replay(runtime.checkpoint(), ticks=0)
    assert replay["replay_digest"]
    assert replay["replay_equivalent"] is True
