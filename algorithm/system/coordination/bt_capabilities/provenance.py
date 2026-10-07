"""Provenance for the ICRA 2024 BT capability-auction condition."""

from algorithm.common_coordination import AlgorithmCondition


def condition() -> AlgorithmCondition:
    return AlgorithmCondition(
        algorithm_id="BT-capabilities-adapted-to-EAI",
        source_reference="ICRA 2024 / arXiv:2402.02833 / ros_bt_py v1.1.0",
        implementation="adapted_ros_independent_capability_bridge",
        execution_scope="behavior_tree_capability_auction",
        planner="remote-capability-slot auction with utility/precondition checks",
        model_or_api="classical behavior tree and capability auction",
        checkpoint="not_applicable_no_learned_weights",
        supported_primitives=("mission_tree", "capability_auction", "precondition", "member_failure", "reauction"),
        unsupported_dimensions=("ROS1_runtime", "shared_object_store_until_eai_bridge", "physical_success_without_executor"),
        physical_executor_owner="eai_runtime",
        serial_action_loop=False,
        notes=(
            "The public ros_bt_py release is ROS1/catkin. This boundary mirrors "
            "its capability/utility/remote-slot semantics without importing rospy; "
            "EAI remains the physical executor."
        ),
    )
