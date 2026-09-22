from demo.benchmark.goal_attachment_probe import _formal_goal_overload_session
from demo.benchmark.goal_driven_tasks import build_goal_task_package


def test_formal_attachment_probe_admits_single_carrier_overload_to_goal_session():
    public = build_goal_task_package("warehouse_nvd")["public_task"]
    formal = _formal_goal_overload_session(
        public, run_id="formal-test-run", deployment={"sha256": "deployment-test"})

    assert formal["episode_id"] == "warehouse-formal-overload:formal-test-run"
    assert all(row["accepted"] for row in formal["accepted"])
    report = formal["session"].report()
    assert report["episode_id"] == formal["episode_id"]
    assert report["tasks"]["formal_overload_crate"]["status"] == "pending"
    assert report["tasks"]["formal_overload_crate"]["task"]["arguments"]["object_id"] == "order_crate_01"
