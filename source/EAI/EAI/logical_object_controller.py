"""Read live articulation arrival at host-bound semantic operation nodes.

Targets are navigation-node positions, never object geometry. Ground targets
use the registered articulation root-height offset; aerial targets are world
positions. No task object, controller command or algorithm success flag is
read here. The reader does not advance simulation or select a coalition.
"""
from copy import deepcopy
import math
from collections.abc import Mapping

from task.logical_objects import ControllerObservation, digest, tick


def _vector(value, length):
    for name in ("detach", "cpu", "tolist"):
        method = getattr(value, name, None)
        if callable(method):
            value = method()
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError("invalid articulation readback shape")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ValueError("articulation readback must be finite numeric")
    return [float(x) for x in value]


class IsaacLogicalArrivalReader:
    """ControllerObservation callback for LogicalObjectRuntime.

    Host bindings map instance IDs to agent names and root heights. Each
    instance has its own semantic-point -> list of navigation-node XYZ targets.
    Radius and speed tolerances must be explicitly registered by the host.
    Pepper's fixed root is corrected using measured virtual base joints.
    Evidence is retained by digest so receipts can archive its full preimage.
    """

    def __init__(self, *, base_env, instance_agent_map, root_heights,
                 operation_nodes, arrival_radius_m, max_linear_speed_m_s,
                 max_angular_speed_rad_s):
        self._env = base_env
        self._agents = dict(instance_agent_map)
        self._heights = dict(root_heights)
        self._nodes = deepcopy(operation_nodes)
        if (not self._agents or set(self._agents) != set(self._heights)
                or set(self._agents) != set(self._nodes)
                or any(not isinstance(x, str) or not x for x in self._agents.values())
                or len(set(self._agents.values())) != len(self._agents)):
            raise ValueError("arrival binding must exactly cover unique runtime agents")
        for height in self._heights.values():
            if isinstance(height, bool) or not isinstance(height, (int, float)) or not math.isfinite(height) or height < 0:
                raise ValueError("root height must be finite and nonnegative")
        for points in self._nodes.values():
            if not isinstance(points, dict):
                raise ValueError("operation nodes must map semantic points")
            for point, nodes in points.items():
                if not isinstance(point, str) or not point or not isinstance(nodes, list) or not nodes:
                    raise ValueError("operation point requires explicit navigation nodes")
                for node in nodes:
                    _vector(node, 3)
        limits = (arrival_radius_m, max_linear_speed_m_s, max_angular_speed_rad_s)
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in limits):
            raise ValueError("arrival tolerances must be explicitly positive and finite")
        self._radius, self._linear, self._angular = limits
        self._evidence = {}

    def __call__(self, robot_id, semantic_point):
        if robot_id not in self._agents or semantic_point not in self._nodes[robot_id]:
            raise ValueError("operation point is not bound for this robot")
        if self._env.num_envs != 1:
            raise ValueError("logical arrival reader requires one environment")
        now = tick(self._env.common_step_counter)
        agent = self._agents[robot_id]
        robot = self._env.scene.articulations[agent]
        state = _vector(robot.data.root_state_w[0], 13)
        if not math.isclose(math.hypot(*state[3:7]), 1.0, abs_tol=1e-4):
            raise ValueError("articulation orientation must be a unit quaternion")
        position, velocity, angular = state[:3], state[7:10], state[10:13]
        joints = None
        if agent.startswith("pepper_"):
            names = ("XDisp", "YDisp", "ZRot")
            indices = [robot.joint_names.index(name) for name in names]
            positions = [_vector(robot.data.joint_pos[0], len(robot.joint_names))[i] for i in indices]
            speeds = [_vector(robot.data.joint_vel[0], len(robot.joint_names))[i] for i in indices]
            w, x, y, z = state[3:7]
            yaw = math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))
            c, s = math.cos(yaw), math.sin(yaw)
            position = [position[0] + c*positions[0] - s*positions[1],
                        position[1] + s*positions[0] + c*positions[1], position[2]]
            velocity = [velocity[0] + c*speeds[0] - s*speeds[1],
                        velocity[1] + s*speeds[0] + c*speeds[1], velocity[2]]
            angular = [angular[0], angular[1], angular[2] + speeds[2]]
            joints = {"names": names, "positions": positions, "velocities": speeds}
        targets = [[p[0], p[1], p[2] + self._heights[robot_id]] for p in self._nodes[robot_id][semantic_point]]
        distance = min(math.dist(position, p) for p in targets)
        arrived = (distance <= self._radius and math.hypot(*velocity) <= self._linear
                   and math.hypot(*angular) <= self._angular)
        if tick(self._env.common_step_counter) != now:
            raise ValueError("simulation advanced during arrival readback")
        evidence = {"robot_id": robot_id, "agent": agent, "semantic_point": semantic_point,
            "tick": now, "root_state": state, "virtual_base": joints,
            "targets": targets, "position": position, "distance_m": distance,
            "linear_velocity": velocity, "angular_velocity": angular,
            "limits": [self._radius, self._linear, self._angular], "arrived": arrived}
        key = digest(evidence)
        self._evidence[key] = evidence
        return ControllerObservation(robot_id, semantic_point, now, arrived, key)

    def evidence_record(self, evidence_digest):
        return deepcopy(self._evidence[evidence_digest])


def operation_nodes_from_semantic_products(*, semantic_points, topology, domain="ground"):
    """Resolve semantic labels to world XYZ navigation-node alternatives.

    This is a pure map projection. It does not infer a route or select a robot;
    every bound node must exist in the supplied topology and its published
    world position is retained for the controller arrival check.
    """
    if not isinstance(semantic_points, list) or not isinstance(topology, Mapping):
        raise ValueError("semantic products must be mappings/lists")
    if domain not in {"ground", "aerial"} or topology.get("domain", domain) != domain:
        raise ValueError("navigation topology domain mismatch")
    nodes = {}
    for row in topology.get("nodes", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("navigation node identity is missing")
        if row["id"] in nodes:
            raise ValueError("duplicate navigation node identity")
        nodes[row["id"]] = _vector(row.get("position_xyz"), 3)
    result = {}
    for row in semantic_points:
        if not isinstance(row, Mapping) or not isinstance(row.get("label"), str):
            raise ValueError("semantic point label is missing")
        if not row["label"].strip():
            continue  # Unnamed waypoints are not semantic operation points.
        if row["label"] in result:
            raise ValueError("duplicate semantic point label")
        bindings = row.get("navigation_bindings")
        if not isinstance(bindings, list) or not bindings:
            raise ValueError("semantic point navigation bindings are missing")
        alternatives = []
        for binding in bindings:
            if not isinstance(binding, Mapping):
                raise ValueError("invalid navigation binding")
            if binding.get("domain") != domain:
                continue
            node = nodes.get(binding.get("node_id"))
            if not isinstance(node, list) or len(node) != 3:
                raise ValueError(f"semantic point references an unknown {domain} node")
            alternatives.append(list(node))
        if not alternatives:
            continue  # Other-domain points remain visible, but are not arrivals.
        result[row["label"]] = alternatives
    return result
