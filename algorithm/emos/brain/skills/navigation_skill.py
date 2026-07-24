"""
Navigation skill for Isaac Lab robots.

Handles path planning and trajectory following to navigate robots
to target positions or objects.

Supports multiple robot types with specialized navigation controllers:
- G1: Humanoid robot with PID control
- Go2: Quadruped robot with optimized PID control
- M20: Wheeled-legged robot with high-speed navigation
- Quadcopter: UAV with 3D navigation and altitude safety
- Oracle: NavMesh-based A* pathfinding with velocity control state machine

Reference: 
- EAI.hmrs_scene (NavMesh path planning)
- EAI/hmrs_scene/demo/demo_path_planning.py (NavMesh generation)
"""

import math
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
import numpy as np
import torch

from .base_skill import BaseSkill, SkillResult, SkillStatus
from EAI_assets.controller.nav_mesh_config import NavMeshAgentConfig, get_nav_config_for_robot

# Import NavMesh-based path planning
NAVMESH_AVAILABLE = False
NavMeshPathfinder = None
HMRSPathPlanner = None
create_hmrs_path_planner = None

try:
    from EAI.hmrs_scene import (
        NavMeshPathfinder,
        HMRSPathPlanner,
        NavMeshPathResult,
        create_hmrs_path_planner,
    )
    NAVMESH_AVAILABLE = True
except ImportError:
    pass


# ============================================================================
# OracleNavigationController - NavMesh-based Navigation
# ============================================================================

@dataclass
class OracleNavConfig:
    """Configuration for Oracle navigation controller."""
    # Distance threshold to consider waypoint reached
    dist_thresh: float = 0.2
    # Angle threshold for turning (radians)
    turn_thresh: float = 0.1
    # Forward velocity (m/s)
    forward_velocity: float = 1.0
    # Turn velocity (rad/s)
    turn_velocity: float = 1.0
    # Whether to use NavMesh-based path planning
    use_path_planning: bool = True
    # NavMesh max triangle area (for NavMesh generation)
    max_triangle_area: float = 50.0


class OracleNavigationController:
    """
    NavMesh-based Oracle navigation controller.
    
    Implements navigation using NavMesh path planning:
    - A* pathfinding on NavMesh triangles
    - Turn-then-move velocity control state machine
    - Waypoint following with smooth transitions
    
    State Machine:
    1. If at goal → stop
    2. If close to final position but not facing target → turn in place
    3. If facing waypoint → move forward
    4. Otherwise → turn towards waypoint
    
    Reference: EAI.hmrs_scene.scene_graph.navigation.navmesh_generator
    """
    
    def __init__(
        self,
        config: Optional[OracleNavConfig] = None,
        robot_name: str = "",
        navmesh_pathfinder = None,
    ):
        """
        Initialize Oracle navigation controller.
        
        Args:
            config: Navigation configuration
            robot_name: Name of the robot (for logging)
            navmesh_pathfinder: NavMeshPathfinder instance
        """
        self.config = config or OracleNavConfig()
        self.robot_name = robot_name
        
        # Use NavMesh pathfinder
        self.navmesh_pathfinder = navmesh_pathfinder
        
        # Navigation state
        self.goal_position: Optional[np.ndarray] = None
        self.lookat_position: Optional[np.ndarray] = None
        self.path: List[np.ndarray] = []
        self.current_waypoint_idx: int = 0
        self.skill_done: bool = False
        self._initialized: bool = False
        
        # Debug logging
        self._debug = False
    
    def set_navmesh_pathfinder(self, pathfinder) -> None:
        """
        Set NavMesh pathfinder.
        
        Args:
            pathfinder: NavMeshPathfinder instance
        """
        self.navmesh_pathfinder = pathfinder
    
    def set_goal(self, x: float, y: float, theta: float = 0.0):
        """
        Set navigation goal.
        
        Args:
            x: Goal X position
            y: Goal Y position (this is Z in Isaac Lab for ground robots)
            theta: Goal orientation (unused for now)
        """
        # Note: In Isaac Lab, Y is up. We use XZ plane for ground navigation.
        # The 'y' parameter here is actually the Z coordinate for ground robots.
        self.goal_position = np.array([x, 0.0, y])  # [x, height, z]
        self.lookat_position = self.goal_position.copy()
        self.path = []
        self.current_waypoint_idx = 0
        self.skill_done = False
        self._initialized = True
    
    def set_goal_with_lookat(
        self, 
        goal_x: float, 
        goal_z: float, 
        lookat_x: float, 
        lookat_z: float
    ):
        """
        Set navigation goal with separate lookat position (EMOS-style).
        
        Args:
            goal_x: Goal X position
            goal_z: Goal Z position  
            lookat_x: Lookat X position
            lookat_z: Lookat Z position
        """
        self.goal_position = np.array([goal_x, 0.0, goal_z])
        self.lookat_position = np.array([lookat_x, 0.0, lookat_z])
        self.path = []
        self.current_waypoint_idx = 0
        self.skill_done = False
        self._initialized = True
    
    def set_goal_3d(self, x: float, y: float, z: float):
        """Set 3D navigation goal (for flying robots)."""
        self.goal_position = np.array([x, y, z])
        self.lookat_position = self.goal_position.copy()
        self.path = []
        self.current_waypoint_idx = 0
        self.skill_done = False
        self._initialized = True
    
    def plan_path(self, current_pos: np.ndarray) -> bool:
        """
        Plan path from current position to goal using NavMesh A*.
        
        Args:
            current_pos: Current robot position [x, y, z]
            
        Returns:
            True if path was found
        """
        if self.goal_position is None:
            return False
        
        if not self.config.use_path_planning:
            # Direct path when path planning is disabled
            self.path = [current_pos.copy(), self.goal_position.copy()]
            self.current_waypoint_idx = 0
            return True
        
        # Use NavMesh pathfinder
        if self.navmesh_pathfinder is not None:
            try:
                # NavMesh uses 2D coordinates [x, y] in XZ plane
                start_2d = np.array([current_pos[0], current_pos[2]])
                goal_2d = np.array([self.goal_position[0], self.goal_position[2]])
                
                result = self.navmesh_pathfinder.find_path(start_2d, goal_2d)
                
                if result.found and len(result.path) > 0:
                    # Convert 2D path to 3D (Y-up: x, height, z)
                    self.path = [np.array([p[0], 0.0, p[1]]) for p in result.path]
                    self.current_waypoint_idx = 0
                    
                    if self._debug:
                        print(f"[OracleNav] NavMesh path found: {len(self.path)} waypoints, "
                              f"distance: {result.distance:.2f}m")
                    return True
                else:
                    if self._debug:
                        print(f"[OracleNav] NavMesh path not found, using direct path")
            except Exception as e:
                if self._debug:
                    print(f"[OracleNav] NavMesh path planning error: {e}")
        
        # Fallback: direct path
        if self._debug:
            print(f"[OracleNav] Using direct path")
        self.path = [current_pos.copy(), self.goal_position.copy()]
        self.current_waypoint_idx = 0
        return True
    
    def compute_velocity_command(
        self, 
        current_pose: Tuple[float, float, float]
    ) -> Tuple[float, float, float]:
        """
        Compute velocity command using EMOS-style state machine.
        
        The state machine logic:
        1. If at goal → stop
        2. If close to waypoint but not facing target → turn in place
        3. If facing waypoint → move forward
        4. Otherwise → turn towards waypoint
        
        Args:
            current_pose: (x, z, theta) - position and heading
            
        Returns:
            (vx, vy, wz) velocity command
        """
        if not self._initialized or self.goal_position is None:
            return (0.0, 0.0, 0.0)
        
        current_pos = np.array([current_pose[0], 0.0, current_pose[1]])
        current_heading = current_pose[2]
        
        # Plan path if needed
        if len(self.path) == 0:
            self.plan_path(current_pos)
        
        if len(self.path) == 0:
            return (0.0, 0.0, 0.0)
        
        # Get current waypoint (next point in path)
        waypoint_idx = min(self.current_waypoint_idx + 1, len(self.path) - 1)
        current_waypoint = self.path[waypoint_idx]
        final_target = self.path[-1]
        
        # Compute relative vectors (XZ plane)
        robot_forward = np.array([np.cos(current_heading), np.sin(current_heading)])
        
        rel_targ = current_waypoint - current_pos
        rel_targ_2d = np.array([rel_targ[0], rel_targ[2]])
        
        rel_goal = final_target - current_pos
        rel_goal_2d = np.array([rel_goal[0], rel_goal[2]])
        
        # Compute angles
        angle_to_waypoint = self._get_angle(robot_forward, rel_targ_2d)
        angle_to_goal = self._get_angle(robot_forward, rel_goal_2d)
        
        # Compute distances
        dist_to_waypoint = np.linalg.norm(rel_targ_2d)
        dist_to_goal = np.linalg.norm(rel_goal_2d)
        
        # Check if at final goal
        at_goal = (
            dist_to_goal < self.config.dist_thresh and
            angle_to_goal < self.config.turn_thresh
        )
        
        if at_goal:
            self.skill_done = True
            return (0.0, 0.0, 0.0)
        
        # Advance waypoint if close enough
        if dist_to_waypoint < self.config.dist_thresh:
            self.current_waypoint_idx = min(
                self.current_waypoint_idx + 1, 
                len(self.path) - 1
            )
            # Recompute for new waypoint
            if self.current_waypoint_idx < len(self.path) - 1:
                current_waypoint = self.path[self.current_waypoint_idx + 1]
                rel_targ = current_waypoint - current_pos
                rel_targ_2d = np.array([rel_targ[0], rel_targ[2]])
                angle_to_waypoint = self._get_angle(robot_forward, rel_targ_2d)
                dist_to_waypoint = np.linalg.norm(rel_targ_2d)
        
        # EMOS velocity control state machine
        if dist_to_goal < self.config.dist_thresh:
            # Close to goal, turn to face goal
            turn_vel = self._compute_turn(rel_goal_2d, robot_forward)
            return (0.0, 0.0, turn_vel)
        elif angle_to_waypoint < self.config.turn_thresh:
            # Facing waypoint, move forward
            return (self.config.forward_velocity, 0.0, 0.0)
        else:
            # Turn towards waypoint
            turn_vel = self._compute_turn(rel_targ_2d, robot_forward)
            return (0.0, 0.0, turn_vel)
    
    def _get_angle(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Get angle between two 2D vectors."""
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 < 1e-6 or norm2 < 1e-6:
            return 0.0
        cos_angle = np.clip(np.dot(vec1, vec2) / (norm1 * norm2), -1.0, 1.0)
        return np.arccos(cos_angle)
    
    def _compute_turn(self, rel_target: np.ndarray, robot_forward: np.ndarray) -> float:
        """
        Compute turn velocity to face target.
        
        Args:
            rel_target: Relative target position (2D)
            robot_forward: Robot forward direction (2D)
            
        Returns:
            Angular velocity (positive = left, negative = right)
        """
        # Cross product to determine turn direction
        cross = np.cross(robot_forward, rel_target)
        
        if cross > 0:
            # Turn left
            return self.config.turn_velocity
        else:
            # Turn right
            return -self.config.turn_velocity
    
    def is_at_goal(self, current_pose: Tuple[float, float, float]) -> bool:
        """Check if robot is at goal."""
        if self.goal_position is None:
            return False
        
        current_pos = np.array([current_pose[0], 0.0, current_pose[1]])
        dist = np.linalg.norm(
            np.array([current_pos[0] - self.goal_position[0],
                      current_pos[2] - self.goal_position[2]])
        )
        return dist < self.config.dist_thresh or self.skill_done
    
    def stop(self):
        """Stop navigation."""
        self.skill_done = True
        self.path = []
    
    def reset(self):
        """Reset controller state."""
        self.goal_position = None
        self.path = []
        self.current_waypoint_idx = 0
        self.skill_done = False
        self._initialized = False


def _create_oracle_nav_controller(
    scene_graph: Any = None,
    nav_params: Dict[str, Any] = None,
    robot_name: str = "",
    env: Any = None,
    navmesh_pathfinder = None
) -> OracleNavigationController:
    """
    Create Oracle navigation controller with NavMesh-based A* pathfinding.
    
    Args:
        scene_graph: Scene graph with pathfinder
        nav_params: Navigation parameters
        robot_name: Robot name
        env: Optional Isaac Lab environment
        navmesh_pathfinder: Optional pre-created NavMeshPathfinder
        
    Returns:
        OracleNavigationController instance
    """
    navmesh_pf = navmesh_pathfinder
    
    # Try to get NavMesh pathfinder from scene graph first
    if navmesh_pf is None and scene_graph is not None:
        if hasattr(scene_graph, 'navmesh_pathfinder'):
            navmesh_pf = scene_graph.navmesh_pathfinder
        elif hasattr(scene_graph, 'pathfinder'):
            # Check if it's a NavMesh pathfinder
            pf = scene_graph.pathfinder
            if NAVMESH_AVAILABLE and isinstance(pf, NavMeshPathfinder):
                navmesh_pf = pf
    
    # If no NavMesh pathfinder available, try to create from scene graph NavMesh data
    if navmesh_pf is None and NAVMESH_AVAILABLE:
        if scene_graph is not None and hasattr(scene_graph, 'navmesh_data'):
            try:
                navmesh_data = scene_graph.navmesh_data
                if navmesh_data is not None:
                    vertices = navmesh_data.get('vertices_2d')
                    triangles = navmesh_data.get('triangles')
                    walkable_polygon = navmesh_data.get('walkable_polygon')
                    
                    if vertices is not None and triangles is not None:
                        navmesh_pf = NavMeshPathfinder(
                            vertices=vertices,
                            triangles=triangles,
                            walkable_polygon=walkable_polygon,
                            verbose=False
                        )
                        print(f"[OracleNav] Created NavMesh pathfinder from scene graph for {robot_name}")
            except Exception as e:
                print(f"[OracleNav] Failed to create NavMesh pathfinder: {e}")
    
    # Log pathfinder type being used
    if navmesh_pf is not None:
        print(f"[OracleNav] Using NavMesh pathfinder for {robot_name}")
    else:
        print(f"[OracleNav] No pathfinder available for {robot_name}, will use direct paths")
    
    # Build config from params
    config = OracleNavConfig()
    if nav_params:
        if 'dist_thresh' in nav_params:
            config.dist_thresh = nav_params['dist_thresh']
        if 'turn_thresh' in nav_params:
            config.turn_thresh = nav_params['turn_thresh']
        if 'forward_velocity' in nav_params:
            config.forward_velocity = nav_params['forward_velocity']
        if 'turn_velocity' in nav_params:
            config.turn_velocity = nav_params['turn_velocity']
        if 'goal_tolerance' in nav_params:
            config.dist_thresh = nav_params['goal_tolerance']
        if 'max_triangle_area' in nav_params:
            config.max_triangle_area = nav_params['max_triangle_area']
    
    return OracleNavigationController(
        config=config,
        robot_name=robot_name,
        navmesh_pathfinder=navmesh_pf,
    )


# ============================================================================
# Specialized Navigation Controllers (Lazy Loading)
# ============================================================================

def _create_g1_nav_controller(nav_params: Dict[str, Any] = None):
    """Create G1 navigation controller."""
    try:
        from EAI_assets.controller.rl.g1_skrl.g1_navigation import (
            G1NavigationController, G1_NAVIGATION_CFG
        )
        return G1NavigationController(G1_NAVIGATION_CFG, nav_params or {})
    except ImportError:
        return None


def _create_go2_nav_controller(nav_params: Dict[str, Any] = None):
    """Create Go2 navigation controller."""
    try:
        from EAI_assets.controller.rl.go2_skrl.go2_navigation import (
            Go2NavigationController, Go2_NAVIGATION_CFG
        )
        return Go2NavigationController(Go2_NAVIGATION_CFG, nav_params or {})
    except ImportError:
        return None


def _create_quadcopter_nav_controller(nav_params: Dict[str, Any] = None):
    """Create Quadcopter navigation controller."""
    try:
        from EAI_assets.controller.rl.quadcopter_goal_skrl.quadcopter_navigation import (
            QuadcopterNavigationController, QUADCOPTER_NAVIGATION_CFG
        )
        return QuadcopterNavigationController(QUADCOPTER_NAVIGATION_CFG, nav_params or {})
    except ImportError:
        return None


def _create_m20_nav_controller(nav_params: Dict[str, Any] = None):
    """Create M20 wheeled-legged navigation controller."""
    try:
        from EAI_assets.controller.rl.m20_rough_rsl.m20_navigation import (
            M20NavigationController, M20_NAVIGATION_CFG
        )
        return M20NavigationController(M20_NAVIGATION_CFG, nav_params or {})
    except ImportError:
        return None


def _infer_robot_type(robot_name: str, env: Any = None) -> str:
    """Infer robot type from name or environment."""
    name_lower = robot_name.lower()
    
    # Check by name patterns
    if "carter" in name_lower:
        return "carter"
    elif "g1" in name_lower or "humanoid" in name_lower:
        return "g1"
    elif "go2" in name_lower or "quadruped" in name_lower or "dog" in name_lower:
        return "go2"
    elif "m20" in name_lower or "deeprobotics" in name_lower or "wheeled_leg" in name_lower:
        return "m20"
    elif "quadcopter" in name_lower or "drone" in name_lower or "uav" in name_lower:
        return "quadcopter"
    
    # Try to infer from environment
    if env is not None and hasattr(env, 'scene') and hasattr(env.scene, 'articulations'):
        if robot_name in env.scene.articulations:
            robot = env.scene.articulations[robot_name]
            # Check number of joints or robot class name
            if hasattr(robot, 'num_joints'):
                num_joints = robot.num_joints
                if num_joints >= 20:  # G1 humanoid has many joints
                    return "g1"
                elif num_joints == 16:  # M20 has 12 leg + 4 wheel joints
                    return "m20"
                elif num_joints == 12:  # Go2 has 12 leg joints
                    return "go2"
                elif num_joints <= 4:  # Quadcopter has 4 rotors
                    return "quadcopter"
    
    return "unknown"


class NavigationSkill(BaseSkill):
    """
    Navigation skill that moves a robot to a target position.
    
    Automatically selects the appropriate navigation controller based on robot type:
    - G1: Uses G1NavigationController with PID control
    - Go2: Uses Go2NavigationController with quadruped-optimized PID
    - M20: Uses M20NavigationController with wheeled-legged optimized control (fastest ground robot)
    - Quadcopter: Uses QuadcopterNavigationController with 3D navigation
    - Carter: Uses OracleNavigationController with A* pathfinding (EMOS-style)
    - Unknown: Falls back to OracleNavigationController or simple proportional control
    """
    
    def __init__(
        self,
        env: Any,
        robot_name: str,
        scene_graph: Any = None,
        robot_type: str = None,
        use_oracle: bool = False,
        **kwargs
    ):
        super().__init__("navigation", env, robot_name, **kwargs)
        self.scene_graph = scene_graph
        self.use_oracle = use_oracle
        
        # Infer robot type if not provided
        self.robot_type = robot_type or _infer_robot_type(robot_name, env)
        print(f"[NavigationSkill] Robot '{robot_name}' detected as type: {self.robot_type}")
        
        # Oracle controller (for A* pathfinding)
        self.oracle_controller: Optional[OracleNavigationController] = None
        
        # Try to create specialized navigation controller
        self.specialized_controller = self._create_specialized_controller()
        self.use_specialized = self.specialized_controller is not None
        
        if self.use_specialized:
            controller_name = self.robot_type.upper()
            if isinstance(self.specialized_controller, OracleNavigationController):
                controller_name = "ORACLE"
            print(f"[NavigationSkill] Using specialized {controller_name} navigation controller")
        else:
            print(f"[NavigationSkill] Using default proportional navigation controller")
        
        # Default navigation parameters (used if no specialized controller)
        self.position_threshold = kwargs.get("position_threshold", 0.3)
        self.angle_threshold = kwargs.get("angle_threshold", 0.1)
        self.max_linear_speed = kwargs.get("max_linear_speed", 1.0)
        self.max_angular_speed = kwargs.get("max_angular_speed", 1.0)
        self.linear_gain = kwargs.get("linear_gain", 1.0)
        self.angular_gain = kwargs.get("angular_gain", 2.0)
        
        # Path planning
        self.path: List[np.ndarray] = []
        self.current_waypoint_idx = 0
        self.target_position: Optional[np.ndarray] = None
        
        # For quadcopter 3D navigation
        self.is_3d_navigation = self.robot_type == "quadcopter"

        # Oracle navigation controller (EMOS-style)
        self.oracle_controller = None
        self.use_oracle_nav = kwargs.get("use_oracle_nav", True)
    
    def _create_specialized_controller(self):
        """Create specialized navigation controller based on robot type."""
        nav_params = {
            'goal_tolerance': 0.1,
            'forward_velocity': 1.0,
            'turn_velocity': 1.0,
            'max_triangle_area': 50.0,  # NavMesh parameter
        }
        
        # Check if Oracle navigation is requested or needed
        use_oracle_nav = self.use_oracle or self.robot_type in ["carter", "unknown"]
        
        if self.robot_type == "g1":
            controller = _create_g1_nav_controller(nav_params)
            if controller is not None:
                return controller
            # Fallback to Oracle if RL controller not available
            use_oracle_nav = True
        elif self.robot_type == "go2":
            controller = _create_go2_nav_controller(nav_params)
            if controller is not None:
                return controller
            use_oracle_nav = True
        elif self.robot_type == "m20":
            controller = _create_m20_nav_controller(nav_params)
            if controller is not None:
                return controller
            use_oracle_nav = True
        elif self.robot_type == "quadcopter":
            controller = _create_quadcopter_nav_controller(nav_params)
            if controller is not None:
                return controller
            # No Oracle fallback for quadcopter (needs 3D)
            return None
        
        # Create Oracle controller for ground robots (NavMesh-based A* navigation)
        if use_oracle_nav:
            # Try to get NavMesh pathfinder from scene graph
            navmesh_pf = None
            if self.scene_graph is not None:
                if hasattr(self.scene_graph, 'navmesh_pathfinder'):
                    navmesh_pf = self.scene_graph.navmesh_pathfinder
            
            self.oracle_controller = _create_oracle_nav_controller(
                scene_graph=self.scene_graph,
                nav_params=nav_params,
                robot_name=self.robot_name,
                env=self.env,
                navmesh_pathfinder=navmesh_pf
            )
            return self.oracle_controller
        
        return None
    
    def reset(self, target: Any, **kwargs) -> None:
        """
        Reset navigation to a new target.
        
        Args:
            target: Can be:
                - np.ndarray or list: Direct position [x, y, z]
                - str: Object name to navigate to (requires scene_graph)
                - dict: {"position": [x,y,z]} or {"object": "name"}
        """
        super().reset(target, **kwargs)
        
        # Parse target
        if isinstance(target, (np.ndarray, list, tuple)):
            self.target_position = np.array(target[:3] if len(target) >= 3 else list(target) + [0.0])
        elif isinstance(target, str):
            # Object name - look up in scene graph
            self.target_position = self._get_object_position(target)
        elif isinstance(target, dict):
            if "position" in target:
                pos = target["position"]
                self.target_position = np.array(pos[:3] if len(pos) >= 3 else list(pos) + [0.0])
            elif "object" in target:
                self.target_position = self._get_object_position(target["object"])
            elif "robot" in target:
                self.target_position = self._get_robot_position(target["robot"])
        
        if self.target_position is None:
            self.status = SkillStatus.FAILED
            return
        
        # Set goal on specialized controller if available
        if self.use_specialized and self.specialized_controller is not None:
            if self.is_3d_navigation:
                # Quadcopter: 3D goal
                self.specialized_controller.set_goal(
                    float(self.target_position[0]),
                    float(self.target_position[1]),
                    float(self.target_position[2])
                )
            else:
                # Ground robots: 2D goal
                self.specialized_controller.set_goal(
                    float(self.target_position[0]),
                    float(self.target_position[1]),
                    0.0  # theta
                )
        
        # Plan path (for fallback controller)
        self._plan_path()
    
    def _get_object_position(self, object_name: str) -> Optional[np.ndarray]:
        """Get position of an object from scene graph."""
        if self.scene_graph is None:
            print(f"Warning: No scene graph available for object lookup")
            return None
        
        # Try to find object in scene graph
        if hasattr(self.scene_graph, 'object_layer'):
            for obj in self.scene_graph.object_layer.objects:
                if obj.label == object_name or obj.class_name == object_name:
                    return np.array(obj.center)
        
        # Try environment scene
        if hasattr(self.env, 'scene'):
            scene = self.env.scene
            # Check rigid objects
            if hasattr(scene, 'rigid_objects') and object_name in scene.rigid_objects:
                obj = scene.rigid_objects[object_name]
                if hasattr(obj, 'data') and hasattr(obj.data, 'root_pos_w'):
                    pos = obj.data.root_pos_w
                    if pos is not None and len(pos) > 0:
                        return pos[0].cpu().numpy()
        
        print(f"Warning: Object '{object_name}' not found")
        return None
    
    def _get_robot_position(self, robot_name: str) -> Optional[np.ndarray]:
        """Get position of another robot."""
        if hasattr(self.env, 'scene') and hasattr(self.env.scene, 'articulations'):
            if robot_name in self.env.scene.articulations:
                robot = self.env.scene.articulations[robot_name]
                if hasattr(robot, 'data') and hasattr(robot.data, 'root_pos_w'):
                    pos = robot.data.root_pos_w
                    if pos is not None and len(pos) > 0:
                        return pos[0].cpu().numpy()
        return None
    
    def _plan_path(self) -> None:
        """Plan path to target using A* if available."""
        current_pos = self.get_robot_position()
        if current_pos is None or self.target_position is None:
            self.path = []
            return
        
        # Use Oracle controller's A* pathfinding if available
        if self.oracle_controller is not None:
            self.oracle_controller.plan_path(current_pos)
            self.path = self.oracle_controller.path
            self.current_waypoint_idx = 0
            return
        
        # Try to use scene graph pathfinder
        if self.scene_graph is not None and hasattr(self.scene_graph, 'pathfinder'):
            try:
                pathfinder = self.scene_graph.pathfinder
                self.path = pathfinder.find_path_to_point(current_pos, self.target_position)
                self.current_waypoint_idx = 0
                return
            except Exception as e:
                print(f"[NavigationSkill] A* path planning failed: {e}")
        
        # Fallback: Simple direct path
        self.path = [self.target_position]
        self.current_waypoint_idx = 0
    
    def step(self) -> SkillResult:
        """Execute one navigation step."""
        if self._check_timeout():
            if self.use_specialized and self.specialized_controller is not None:
                self.specialized_controller.stop()
            return SkillResult(
                status=SkillStatus.FAILED,
                message="Navigation timed out"
            )
        
        current_pos = self.get_robot_position()
        current_quat = self.get_robot_orientation()
        
        if current_pos is None or self.target_position is None:
            self.status = SkillStatus.FAILED
            return SkillResult(
                status=SkillStatus.FAILED,
                message="Cannot get robot position"
            )
        
        # Use specialized controller if available
        if self.use_specialized and self.specialized_controller is not None:
            return self._step_specialized(current_pos, current_quat)
        
        # Fallback to default proportional control
        return self._step_default(current_pos, current_quat)
    
    def _step_specialized(self, current_pos: np.ndarray, current_quat: Optional[np.ndarray]) -> SkillResult:
        """Execute step using specialized navigation controller."""
        if self.is_3d_navigation:
            # Quadcopter: 3D navigation
            current_pose = (float(current_pos[0]), float(current_pos[1]), float(current_pos[2]))
            
            # Debug: print positions
            if self._step_count <= 1:
                print(f"[{self.robot_name}] Current pos: {current_pose}, Target: {self.target_position}")
            
            # Check if at goal (require minimum steps to avoid instant completion)
            if self._step_count > 10 and self.specialized_controller.is_at_goal(current_pose):
                self.status = SkillStatus.SUCCEEDED
                self.specialized_controller.stop()
                return SkillResult(
                    status=SkillStatus.SUCCEEDED,
                    command=self._create_stop_command(),
                    message="Navigation complete (Quadcopter)",
                    progress=1.0
                )
            
            # Get goal command for quadcopter
            goal_cmd = self.specialized_controller.compute_goal_command(current_pose)
            
            # Create command tensor [goal_x, goal_y, goal_z]
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            command = torch.tensor(
                [goal_cmd[0], goal_cmd[1], goal_cmd[2]],
                device=device,
                dtype=torch.float32
            ).unsqueeze(0)
            
            # Calculate progress
            distance = math.sqrt(
                (self.target_position[0] - current_pos[0])**2 +
                (self.target_position[1] - current_pos[1])**2 +
                (self.target_position[2] - current_pos[2])**2
            )
            
            return SkillResult(
                status=SkillStatus.RUNNING,
                command=command,
                message=f"Navigating (Quadcopter): {distance:.2f}m to target",
                progress=max(0.0, 1.0 - distance / 10.0),
                data={"distance": distance, "altitude": current_pos[2]}
            )
        else:
            # Ground robots (G1, Go2): 2D navigation
            current_heading = self._quat_to_yaw(current_quat) if current_quat is not None else 0.0
            current_pose = (float(current_pos[0]), float(current_pos[1]), float(current_heading))
            
            # Debug: print positions on first step
            if self._step_count <= 1:
                goal = self.specialized_controller.goal_position
                print(f"[{self.robot_name}] Current pos: ({current_pos[0]:.2f}, {current_pos[1]:.2f}), Goal: {goal}, Step: {self._step_count}")
            
            # Check if at goal (require minimum steps to avoid instant completion due to bugs)
            if self._step_count > 10 and self.specialized_controller.is_at_goal(current_pose):
                self.status = SkillStatus.SUCCEEDED
                self.specialized_controller.stop()
                return SkillResult(
                    status=SkillStatus.SUCCEEDED,
                    command=self._create_stop_command(),
                    message=f"Navigation complete ({self.robot_type.upper()})",
                    progress=1.0
                )
            
            # Get velocity command from specialized controller
            vel_cmd = self.specialized_controller.compute_velocity_command(current_pose)
            
            # Create command tensor [vx, vy, wz]
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            command = torch.tensor(
                [vel_cmd[0], vel_cmd[1], vel_cmd[2]],
                device=device,
                dtype=torch.float32
            ).unsqueeze(0)
            
            # Calculate progress
            distance = math.sqrt(
                (self.target_position[0] - current_pos[0])**2 +
                (self.target_position[1] - current_pos[1])**2
            )
            
            return SkillResult(
                status=SkillStatus.RUNNING,
                command=command,
                message=f"Navigating ({self.robot_type.upper()}): {distance:.2f}m to target",
                progress=max(0.0, 1.0 - distance / 10.0),
                data={"distance": distance, "heading": current_heading, "velocity": vel_cmd}
            )
    
    def _step_default(self, current_pos: np.ndarray, current_quat: Optional[np.ndarray]) -> SkillResult:
        """Execute step using default proportional control."""
        # Get current waypoint
        if self.current_waypoint_idx >= len(self.path):
            self.status = SkillStatus.SUCCEEDED
            return SkillResult(
                status=SkillStatus.SUCCEEDED,
                command=self._create_stop_command(),
                message="Navigation complete"
            )
        
        waypoint = self.path[self.current_waypoint_idx]
        
        # Calculate distance and direction to waypoint
        diff = waypoint - current_pos
        if not self.is_3d_navigation:
            diff[2] = 0  # Ignore vertical for ground robots
        distance = np.linalg.norm(diff[:2]) if not self.is_3d_navigation else np.linalg.norm(diff)
        
        # Check if reached waypoint
        if distance < self.position_threshold:
            self.current_waypoint_idx += 1
            if self.current_waypoint_idx >= len(self.path):
                self.status = SkillStatus.SUCCEEDED
                return SkillResult(
                    status=SkillStatus.SUCCEEDED,
                    command=self._create_stop_command(),
                    message="Navigation complete",
                    progress=1.0
                )
        
        # Calculate heading to waypoint
        target_heading = math.atan2(diff[1], diff[0])
        current_heading = self._quat_to_yaw(current_quat) if current_quat is not None else 0.0
        
        # Calculate heading error
        heading_error = self._normalize_angle(target_heading - current_heading)
        
        # Generate velocity command
        command = self._compute_velocity_command(distance, heading_error)
        
        # Calculate progress
        start_distance = np.linalg.norm(self.path[0] - current_pos) if self._step_count == 1 else 10.0
        progress = 1.0 - (distance / max(start_distance, 0.01))
        
        return SkillResult(
            status=SkillStatus.RUNNING,
            command=command,
            message=f"Navigating: {distance:.2f}m to target",
            progress=min(max(progress, 0.0), 1.0),
            data={"distance": distance, "heading_error": heading_error}
        )
    
    def _compute_velocity_command(
        self, 
        distance: float, 
        heading_error: float
    ) -> torch.Tensor:
        """Compute velocity command using proportional control."""
        # Angular velocity (turn towards target)
        angular_vel = self.angular_gain * heading_error
        angular_vel = np.clip(angular_vel, -self.max_angular_speed, self.max_angular_speed)
        
        # Linear velocity (move forward if roughly aligned)
        if abs(heading_error) < 0.5:  # ~30 degrees
            linear_vel = self.linear_gain * distance
            linear_vel = np.clip(linear_vel, 0, self.max_linear_speed)
            # Reduce speed when turning
            linear_vel *= (1.0 - abs(heading_error) / math.pi)
        else:
            linear_vel = 0.0  # Turn in place first
        
        # Create command tensor [vx, vy, wz]
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        command = torch.tensor(
            [linear_vel, 0.0, angular_vel],
            device=device,
            dtype=torch.float32
        ).unsqueeze(0)
        
        return command
    
    def _create_stop_command(self) -> torch.Tensor:
        """Create a zero velocity command."""
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return torch.zeros(1, 3, device=device, dtype=torch.float32)
    
    def _quat_to_yaw(self, quat: np.ndarray) -> float:
        """Convert quaternion [w, x, y, z] to yaw angle."""
        w, x, y, z = quat
        # Yaw (z-axis rotation)
        siny_cosp = 2 * (w * z + x * y)
        cosy_cosp = 1 - 2 * (y * y + z * z)
        return math.atan2(siny_cosp, cosy_cosp)
    
    def _normalize_angle(self, angle: float) -> float:
        """Normalize angle to [-pi, pi]."""
        while angle > math.pi:
            angle -= 2 * math.pi
        while angle < -math.pi:
            angle += 2 * math.pi
        return angle
    
    def is_done(self) -> bool:
        """Check if navigation is complete."""
        return self.status in [
            SkillStatus.SUCCEEDED, 
            SkillStatus.FAILED, 
            SkillStatus.CANCELLED
        ]
    
    def cancel(self) -> None:
        """Cancel navigation."""
        super().cancel()
        if self.use_specialized and self.specialized_controller is not None:
            self.specialized_controller.stop()
