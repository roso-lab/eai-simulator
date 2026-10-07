from algorithm.common_coordination import CoordinationTask, RobotCapability
from algorithm.system.coordination.fmc_ata import CommunicationModel, FMCATAPlanner


def test_fmc_market_keeps_fractional_allocations_and_prices():
    robots = (
        RobotCapability("r1", frozenset({"inspect"})),
        RobotCapability("r2", frozenset({"inspect"})),
    )
    tasks = (CoordinationTask("t", "inspect", frozenset({"inspect"})),)
    planner = FMCATAPlanner(robots)
    result = planner.allocate(tasks, utilities={("r1", "t"): 1.0, ("r2", "t"): 0.5}, demand={"t": 2.0})
    assert result
    assert all(0.0 < item.fraction <= 1.0 for item in result)
    assert sum(item.fraction for item in result) <= 1.0
    assert planner.prices["t"] >= 1.0


def test_fmc_communication_records_loss_and_delay():
    comm = CommunicationModel(delay_steps=2, drop_recipients={"lost"})
    comm.send("r1", "ok", "bid", {})
    comm.send("r1", "lost", "bid", {})
    assert comm.tick() == ()
    delivered = comm.tick()
    assert len(delivered) == 1 and delivered[0].recipient == "ok"
    assert len(comm.dropped) == 1
