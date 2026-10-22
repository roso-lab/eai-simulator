from types import SimpleNamespace

from algorithm.common_coordination.contract import RobotCapability
from benchmark.fleet import DeliveryStatus, FleetIdentity, FleetMessage, FleetRuntime
from benchmark.protocol import BranchRole, RunKey


def test_fleet_runtime_can_bind_shared_v3_channel_and_account_bits():
    key = RunKey("base", "system", "condition", "repeat", "round", BranchRole.FACT)
    runtime = FleetRuntime(key, transport_options={"mode": "shared_v3", "bits_per_tick": 64, "max_queue_bits": 4096})
    binding = SimpleNamespace(
        session_identity="session-v3",
        scene_id="cpu-fixture",
        robots=(
            SimpleNamespace(capability=RobotCapability("a", frozenset({"send"})), metadata={}),
            SimpleNamespace(capability=RobotCapability("b", frozenset({"recv"})), metadata={}),
        ),
    )
    runtime.start(SimpleNamespace(env_name="cpu-fixture"), binding)
    receipt = runtime.send(FleetMessage("m", "a", "b", "notice", 0, {"payload": "x" * 80}))
    assert receipt.status is DeliveryStatus.QUEUED
    runtime.record_step(tick=30, sim_time_s=.6, phase="execution")
    assert runtime.receive("b", tick=30)[0].message_id == "m"
    summary = runtime.close(tick=31)
    assert summary.transport_accounting["wire_qualified"] is True
    assert summary.transport_accounting["transmitted_bits"] == summary.transport_accounting["delivered_bits"]
