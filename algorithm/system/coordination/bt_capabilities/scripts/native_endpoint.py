#!/usr/bin/env python3
"""Real ROS1 runtime. No ROS modules or auction/BT algorithms are emulated.

Each robot runs in a separate process because rospy and upstream auction state
are process-scoped. The HTTP service is an EAI I/O boundary, not an allocator.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def load_native(source_root):
    root = Path(source_root).resolve()
    sys.path.insert(0, str(root / "ros_bt_py/src"))
    import ros_bt_py.assignment_manager.parallel_auction_manager as auction
    import ros_bt_py.nodes.sequence as sequence
    import ros_bt_py.nodes.parallel as parallel
    for module in (auction, sequence, parallel):
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise RuntimeError("native import resolved outside requested upstream source")
    return auction, sequence, parallel


def robot_main(args):
    auction, _, _ = load_native(args.source_root)
    import rospy
    from ros_bt_py_msgs.srv import (
        GetLocalBid, GetLocalBidResponse, GetAvailableRemoteCapabilitySlots,
        GetAvailableRemoteCapabilitySlotsResponse, ReserveRemoteCapabilitySlot,
        ReserveRemoteCapabilitySlotResponse)
    rospy.init_node("assignment_manager")
    namespace = rospy.get_namespace().rstrip("/")
    reservation_param = namespace + "/eai_reservations"
    rospy.set_param(reservation_param, [])
    reservation_lock = threading.Lock()

    def get_bid(request):
        context = json.loads(rospy.get_param(args.prefix + "/requests/" + request.node_id))
        costs = context["bids"]
        if args.robot_id not in costs:
            return GetLocalBidResponse(success=False, error_message="robot not eligible")
        return GetLocalBidResponse(success=True, bid=costs[args.robot_id],
                                   implementation_name=request.node_id)

    def slots(request):
        return GetAvailableRemoteCapabilitySlotsResponse(
            available_remote_capability_slots=max(0, 1 - len(rospy.get_param(reservation_param))))

    def reserve(request):
        with reservation_lock:
            current = rospy.get_param(reservation_param)
            if current:
                return ReserveRemoteCapabilitySlotResponse(success=False, error="EAI slot occupied")
            rospy.set_param(reservation_param, [request.implementation_name])
        return ReserveRemoteCapabilitySlotResponse(success=True)

    services = [
        rospy.Service(namespace + "/mission_control/get_local_bid", GetLocalBid, get_bid),
        rospy.Service(namespace + "/mission_control/get_available_remote_slots", GetAvailableRemoteCapabilitySlots, slots),
        rospy.Service(namespace + "/mission_control/reserve_remote_capability_slot", ReserveRemoteCapabilitySlot, reserve),
    ]
    manager = auction.ParallelAuctionManager(namespace + "/", args.prefix + "/auctions")
    rospy.spin()


class Runtime:
    def __init__(self, args):
        auction, sequence, parallel = load_native(args.source_root)
        import rospy
        from ros_bt_py.node import Leaf, define_bt_node
        from ros_bt_py.node_config import NodeConfig
        from ros_bt_py_msgs.msg import Node, CapabilityInterface, AuctionMessage
        from ros_bt_py_msgs.srv import FindBestCapabilityExecutor, FindBestCapabilityExecutorRequest
        self.rospy, self.Node = rospy, Node
        self.args, self.sequence, self.missions = args, sequence, {}
        self.parallel = parallel
        self.processes, self.events = [], []
        rospy.init_node("eai_native_endpoint", disable_signals=True)
        self.commit = subprocess.check_output(
            ["git", "-c", "safe.directory=" + str(Path(args.source_root).resolve()),
             "-C", args.source_root, "rev-parse", "HEAD"], text=True).strip()
        self.hashes = {str(Path(module.__file__).resolve().relative_to(Path(args.source_root).resolve())):
                       hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                       for module in (auction, sequence, parallel)}
        self.adapter_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.subscription = rospy.Subscriber(args.prefix + "/auctions", AuctionMessage,
                                             self.record_auction, queue_size=100)
        self.namespaces = {robot: args.prefix + "/robot_" + str(index)
                           for index, robot in enumerate(args.robots)}
        # Run the exact upstream executable for the auctioneer.
        self.spawn([sys.executable, str(Path(args.source_root) / "ros_bt_py/scripts/assignment_manager_node.py")],
                   args.prefix + "/dispatcher")
        for robot, namespace in self.namespaces.items():
            self.spawn([sys.executable, __file__, "--source-root", args.source_root,
                        "--prefix", args.prefix, "--robot-id", robot], namespace)
        self.service_name = args.prefix + "/dispatcher/assignment_manager/find_best_executor"
        rospy.wait_for_service(self.service_name, timeout=20)
        for namespace in self.namespaces.values():
            rospy.wait_for_service(namespace + "/assignment_manager/find_best_executor", timeout=20)
        self.auction_service = rospy.ServiceProxy(self.service_name, FindBestCapabilityExecutor)
        time.sleep(1.0)  # bounded initial TCPROS subscription negotiation
        runtime = self

        @define_bt_node(NodeConfig(version="1.0.0", options={"request_id": str},
                                   inputs={}, outputs={}, max_children=0))
        class EaiFeedbackLeaf(Leaf):
            """I/O leaf: request a native assignment, then wait for EAI feedback."""
            def _do_setup(self):
                self.assignment = None
                self.terminal = None
                self.feedback_record = None
                self.auction_error = None
                self.waiting_for_slot = False

            def _do_tick(self):
                if self.terminal is not None:
                    return Node.SUCCEEDED if self.terminal else Node.FAILED
                if self.auction_error:
                    return Node.FAILED
                if self.assignment is None:
                    context = json.loads(rospy.get_param(args.prefix + "/requests/" + self.options["request_id"]))
                    # Public executor availability is an input, not an alternate
                    # ranking. If every eligible robot is executing, wait; do
                    # not call an auction that cannot reserve any execution slot.
                    self.waiting_for_slot = bool(context["bids"]) and all(
                        rospy.get_param(runtime.namespaces[robot] + "/eai_reservations")
                        for robot in context["bids"])
                    if self.waiting_for_slot:
                        return Node.RUNNING
                    response = runtime.auction_service(FindBestCapabilityExecutorRequest(
                        capability=CapabilityInterface(name=context["task_id"], description=context["description"]),
                        node_id=self.options["request_id"],
                        mission_control_name=args.prefix + "/dispatcher/", implementation_tags_dict="{}"))
                    if not response.success:
                        self.auction_error = response.error_message
                        return Node.FAILED
                    namespace = response.executor_mission_control_topic.rstrip("/")
                    robot = next((key for key, value in runtime.namespaces.items() if value == namespace), None)
                    if robot not in context["bids"]:
                        raise RuntimeError("native auction returned an ineligible/unknown robot")
                    if response.implementation_name != self.options["request_id"]:
                        raise RuntimeError("native auction returned an unexpected implementation")
                    if rospy.get_param(namespace + "/eai_reservations") != [response.implementation_name]:
                        raise RuntimeError("native auction winner did not reserve the EAI execution slot")
                    self.assignment = {"task_id": context["task_id"], "robot_id": robot,
                                       "implementation_name": response.implementation_name,
                                       "executor_namespace": namespace}
                return Node.RUNNING

            def _do_untick(self):
                return Node.IDLE

            def _do_reset(self):
                # Replay requires a new mission, never an implicit repeat of EAI actions.
                raise RuntimeError("EAI mission replay requires a new endpoint mission")

            def _do_shutdown(self):
                pass

        self.leaf_class = EaiFeedbackLeaf

        @define_bt_node(NodeConfig(version="1.0.0", options={"mission_id": str, "task_id": str},
                                   inputs={}, outputs={}, max_children=0))
        class EaiPrerequisiteObservation(Leaf):
            """Read executor-owned completion facts; no scheduling or allocation."""
            def _do_setup(self):
                pass

            def _do_tick(self):
                mission = runtime.missions[self.options["mission_id"]]
                prerequisites = mission["prerequisites"][self.options["task_id"]]
                leaves = mission["leaf_by_id"]
                if any(leaves[key].terminal is False or leaves[key].auction_error for key in prerequisites):
                    return Node.FAILED
                return Node.SUCCEEDED if all(leaves[key].terminal is True for key in prerequisites) else Node.RUNNING

            def _do_untick(self):
                return Node.IDLE

            def _do_reset(self):
                return Node.IDLE

            def _do_shutdown(self):
                pass

        self.prerequisite_class = EaiPrerequisiteObservation

    def spawn(self, command, namespace):
        self.rospy.set_param(namespace + "/auction_duration_sec", self.args.auction_duration)
        self.rospy.set_param(namespace + "/assignment_topic_prefix", self.args.prefix + "/auctions")
        env = dict(os.environ, ROS_NAMESPACE=namespace)
        env["PYTHONPATH"] = str(Path(self.args.source_root) / "ros_bt_py/src") + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.processes.append(subprocess.Popen(command, env=env, stdout=sys.stderr, stderr=sys.stderr))

    def record_auction(self, msg):
        self.events.append({"auction_id": msg.auction_id, "sender_id": msg.sender_id,
                            "type": msg.message_type, "bid": msg.bid,
                            "executors": msg.number_of_executors, "result_id": msg.result_id,
                            "implementation": msg.implementation_name})

    def snapshot(self, mission):
        leaves = mission["leaves"]
        running = mission["tree"].state == self.Node.RUNNING
        return {"ok": True, "mission_id": mission["id"],
                "task_contract_sha256": mission["task_contract_sha256"],
                "native_algorithm": "ros_bt_py.assignment_manager.parallel_auction_manager.ParallelAuctionManager",
                "native_tree": ("ros_bt_py.nodes.parallel.Parallel" if mission["graph_mode"] == "dag"
                                else "ros_bt_py.nodes.sequence.MemorySequence"),
                "graph_mode": mission["graph_mode"], "prerequisites": mission["prerequisites"],
                "endpoint_adapter_sha256": self.adapter_sha256,
                "source_commit": self.commit, "source_sha256": self.hashes,
                "tree_state": mission["tree"].state,
                "assignments": [leaf.assignment for leaf in leaves if running and leaf.assignment and leaf.terminal is None],
                "cancelled_assignments": [leaf.assignment for leaf in leaves if not running and leaf.assignment and leaf.terminal is None],
                "unassigned_tasks": [leaf.name for leaf in leaves if leaf.assignment is None],
                "leaf_states": [{"task_id": leaf.name, "state": leaf.state,
                                 "feedback": leaf.feedback_record, "auction_error": leaf.auction_error,
                                 "waiting_for_slot": leaf.waiting_for_slot}
                                for leaf in leaves],
                "auction_messages": self.events[mission["event_start"]:],
                "evidence_scope": "native_ros_protocol_and_bt_feedback_only",
                "simulation_evidence": "not_evaluated", "physical_evidence": "not_evaluated"}

    def validate_plan(self, body):
        if not isinstance(body, dict) or not isinstance(body.get("tasks"), list) or not body["tasks"]:
            raise ValueError("nonempty task list required")
        robots = body.get("robot_ids")
        if not isinstance(robots, list) or any(not isinstance(r, str) for r in robots) or len(set(robots)) != len(robots) or set(robots) != set(self.namespaces):
            raise ValueError("roster differs from configured native robot nodes")
        records = body["tasks"]
        if any(not isinstance(item, dict) for item in records):
            raise ValueError("task records must be objects")
        ids = [item.get("task_id") for item in records]
        if any(not isinstance(key, str) or not key for key in ids) or len(ids) != len(set(ids)):
            raise ValueError("unique nonempty task IDs required")
        deps = {}
        for index, item in enumerate(records):
            if not isinstance(item.get("description"), str) or not item["description"]:
                raise ValueError("task description required")
            dependency = item.get("prerequisites", [] if index == 0 else [ids[index-1]])
            if (not isinstance(dependency, list) or any(not isinstance(d, str) for d in dependency)
                    or len(dependency) != len(set(dependency)) or not set(dependency) <= set(ids)
                    or item["task_id"] in dependency):
                raise ValueError("invalid task prerequisites")
            deps[item["task_id"]] = dependency
            bids = item.get("bids")
            if not isinstance(bids, dict) or not set(bids) <= set(robots):
                raise ValueError("bids require known robots")
            if any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or v < 0 for v in bids.values()):
                raise ValueError("bids must be finite nonnegative costs")
        pending, visited = set(ids), set()
        while pending:
            ready = {key for key in pending if set(deps[key]) <= visited}
            if not ready:
                raise ValueError("cyclic task dependencies")
            visited.update(ready)
            pending -= ready
        mode = body.get("graph_mode", "linear")
        if mode not in ("linear", "dag"):
            raise ValueError("unsupported graph_mode")
        if mode == "linear" and any(deps[key] != ([] if i == 0 else [ids[i-1]]) for i,key in enumerate(ids)):
            raise ValueError("linear tree cannot represent supplied prerequisites")
        return mode, deps

    def apply_feedback_batch(self, mission, records):
        # Validate the entire transaction before releasing a slot or recording
        # any terminal result; duplicate/stale/wrong-owner input changes nothing.
        if not isinstance(records, list) or not records:
            raise ValueError("nonempty feedback batch required")
        selected, seen = [], set()
        for record in records:
            if not isinstance(record, dict) or type(record.get("succeeded")) is not bool:
                raise ValueError("succeeded must be an explicit boolean")
            key = record.get("task_id")
            if not isinstance(key, str) or key in seen or key not in mission["leaf_by_id"]:
                raise ValueError("duplicate or unknown feedback task identity")
            leaf = mission["leaf_by_id"][key]
            if not leaf.assignment or leaf.assignment["robot_id"] != record.get("robot_id"):
                raise ValueError("feedback identity does not match native assignment")
            if leaf.terminal is not None or mission["tree"].state != self.Node.RUNNING:
                raise ValueError("feedback already consumed or mission terminal")
            if any(not isinstance(record.get(field), str) or not record[field] for field in ("outcome","failure_code")):
                raise ValueError("explicit outcome/failure_code required")
            selected.append((leaf,record)); seen.add(key)
        for leaf, record in selected:
            self.rospy.set_param(leaf.assignment["executor_namespace"] + "/eai_reservations", [])
            leaf.terminal = record["succeeded"]
            leaf.feedback_record = {key: record[key] for key in ("task_id", "robot_id", "succeeded", "outcome", "failure_code")}
        mission["tree"].tick()

    def handle(self, route, body):
        if route == "/health":
            return {"ok": True, "source_commit": self.commit,
                    "robot_ids": list(self.namespaces),
                    "active_missions": [m["id"] for m in self.missions.values()
                                        if m["tree"].state == self.Node.RUNNING]}
        if route == "/plan":
            if self.missions:
                raise ValueError("endpoint has an active mission; complete or close it first")
            mode, prerequisites = self.validate_plan(body)
            mission_id = uuid.uuid4().hex
            tree = (self.parallel.Parallel(name="eai_" + mission_id, options={"needed_successes":len(body["tasks"])})
                    if mode == "dag" else self.sequence.MemorySequence(name="eai_" + mission_id))
            leaves = []
            for index, item in enumerate(body["tasks"]):
                request_id = mission_id + "_" + str(index)
                self.rospy.set_param(self.args.prefix + "/requests/" + request_id, json.dumps(item, allow_nan=False))
                leaf = self.leaf_class(name=item["task_id"], options={"request_id": request_id})
                if mode == "dag":
                    branch = self.sequence.MemorySequence(name="branch_" + str(index))
                    branch.add_child(self.prerequisite_class(name="dependency_" + str(index),
                        options={"mission_id":mission_id,"task_id":item["task_id"]}))
                    branch.add_child(leaf)
                    tree.add_child(branch)
                else:
                    tree.add_child(leaf)
                leaves.append(leaf)
            mission = {"id": mission_id, "tree": tree, "leaves": leaves, "event_start": len(self.events),
                       "graph_mode":mode,"prerequisites":prerequisites,
                       "leaf_by_id":{leaf.name:leaf for leaf in leaves},
                       "task_contract_sha256": hashlib.sha256(json.dumps(
                           body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()}
            self.missions[mission_id] = mission
            tree.setup()
            tree.tick()
            return self.snapshot(mission)
        mission = self.missions[body["mission_id"]]
        if route in ("/feedback", "/feedback_batch"):
            self.apply_feedback_batch(mission, [body] if route == "/feedback" else body.get("feedback"))
        elif route == "/close":
            mission["tree"].shutdown()
            for leaf in mission["leaves"]:
                if leaf.assignment:
                    self.rospy.set_param(leaf.assignment["executor_namespace"] + "/eai_reservations", [])
                self.rospy.delete_param(self.args.prefix + "/requests/" + leaf.options["request_id"])
            result = self.snapshot(mission)
            del self.missions[body["mission_id"]]
            return result
        elif route == "/state":
            if mission["tree"].state == self.Node.RUNNING:
                mission["tree"].tick()
        else:
            raise ValueError("unknown route")
        return self.snapshot(mission)

    def shutdown(self):
        for process in self.processes:
            process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--prefix", default="/heterocoord_native")
    parser.add_argument("--robot-id")
    parser.add_argument("--robots", nargs="+", default=["robot_a", "robot_b"])
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--auction-duration", type=float, default=1.0)
    args = parser.parse_args()
    if args.robot_id:
        robot_main(args)
        return
    runtime = Runtime(args)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1048576:
                    raise ValueError("request size out of bounds")
                result = runtime.handle(self.path, json.loads(self.rfile.read(length)))
                status = 200
            except Exception as exc:
                result, status = {"ok": False, "error": str(exc)}, 400
            data = json.dumps(result, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer((args.bind, args.port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        runtime.shutdown()


if __name__ == "__main__":
    main()
