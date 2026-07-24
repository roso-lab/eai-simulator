"""
High-level robot actions for LLM agents in Isaac Lab.

These actions are designed to be called by LLM agents through function calls.
Each action converts high-level commands to low-level robot control commands.

对应 EMOS 中的:
- habitat_mas/agents/actions/arm_actions.py
- habitat_mas/agents/actions/base_actions.py
"""

from typing import Any, Dict, List, Optional

# 支持作为包导入或直接运行
try:
    from ..API.crab_core import action, Action
except ImportError:
    from API.crab_core import action, Action


# ============================================================================
# Navigation Actions
# ============================================================================

@action
def nav_to_obj(
    target_obj: str,
    robot: str = "",
) -> Dict[str, Any]:
    """
    Navigate the robot to a target object.
    
    Args:
        target_obj: Name or ID of the target object to navigate to.
        robot: Name of the robot to control. Will be auto-filled by the agent.
    
    Returns:
        A dictionary containing the navigation result:
        - success: Whether the navigation was initiated successfully
        - target_position: The position of the target object
        - message: Status message
    """
    return {
        "action": "nav_to_obj",
        "target_obj": target_obj,
        "robot": robot,
    }


@action
def nav_to_position(
    target_x: float,
    target_y: float,
    target_z: float = 0.0,
    robot: str = "",
) -> Dict[str, Any]:
    """
    Navigate the robot to a specific position in the world.
    
    Args:
        target_x: Target X coordinate in world frame.
        target_y: Target Y coordinate in world frame.
        target_z: Target Z coordinate in world frame (default 0.0 for ground).
        robot: Name of the robot to control. Will be auto-filled by the agent.
    
    Returns:
        A dictionary containing the navigation result.
    """
    return {
        "action": "nav_to_position",
        "target_position": [target_x, target_y, target_z],
        "robot": robot,
    }


@action
def nav_to_robot(
    target_robot: str,
    robot: str = "",
) -> Dict[str, Any]:
    """
    Navigate to another robot's current position.
    
    Args:
        target_robot: Name of the target robot to navigate to.
        robot: Name of the robot to control. Will be auto-filled by the agent.
    
    Returns:
        A dictionary containing the navigation result.
    """
    return {
        "action": "nav_to_robot",
        "target_robot": target_robot,
        "robot": robot,
    }


# ============================================================================
# Manipulation Actions
# ============================================================================

@action
def pick(
    target_obj: str,
    robot: str = "",
) -> Dict[str, Any]:
    """
    Pick up a target object.
    
    The robot must be close enough to the object to pick it up.
    Use nav_to_obj first if the robot is far from the object.
    
    Args:
        target_obj: Name or ID of the object to pick up.
        robot: Name of the robot to control. Will be auto-filled by the agent.
    
    Returns:
        A dictionary containing the pick result:
        - success: Whether the pick was successful
        - message: Status message
    """
    return {
        "action": "pick",
        "target_obj": target_obj,
        "robot": robot,
    }


@action
def place(
    target_obj: str,
    target_location: str,
    robot: str = "",
) -> Dict[str, Any]:
    """
    Place the currently held object at a target location.
    
    The robot must be holding an object and be close enough to the target location.
    
    Args:
        target_obj: Name or ID of the object being placed (for verification).
        target_location: Name or ID of the location to place the object at.
        robot: Name of the robot to control. Will be auto-filled by the agent.
    
    Returns:
        A dictionary containing the place result:
        - success: Whether the place was successful
        - message: Status message
    """
    return {
        "action": "place",
        "target_obj": target_obj,
        "target_location": target_location,
        "robot": robot,
    }


@action
def reset_arm(
    robot: str = "",
) -> Dict[str, Any]:
    """
    Reset the robot's arm to its default position.
    
    This is useful after manipulation actions or when preparing for new tasks.
    
    Args:
        robot: Name of the robot to control. Will be auto-filled by the agent.
    
    Returns:
        A dictionary containing the reset result.
    """
    return {
        "action": "reset_arm",
        "robot": robot,
    }


# ============================================================================
# Coordination Actions
# ============================================================================

@action
def wait(
    duration_ms: int = 500,
    robot: str = "",
) -> Dict[str, Any]:
    """
    Wait for a specified duration before taking the next action.
    
    This is useful for coordination between robots or waiting for
    other processes to complete.
    
    Args:
        duration_ms: Duration to wait in milliseconds (default 500ms).
        robot: Name of the robot to control. Will be auto-filled by the agent.
    
    Returns:
        A dictionary containing the wait result.
    """
    return {
        "action": "wait",
        "duration_ms": duration_ms,
        "robot": robot,
    }


@action
def send_request(
    target_agent: str,
    request: str,
    source_agent: str = "",
) -> Dict[str, Any]:
    """
    Send a request message to another agent for coordination.
    
    Use this action when you need help from another robot or want to
    coordinate a task that requires multiple robots.
    
    Args:
        target_agent: Name of the agent to send the request to.
        request: The request message describing what you need help with.
        source_agent: Name of the sending agent. Will be auto-filled.
    
    Returns:
        A dictionary containing the request result:
        - success: Whether the request was sent
        - message: Confirmation message
    """
    return {
        "action": "send_request",
        "target_agent": target_agent,
        "request": request,
        "source_agent": source_agent,
    }


# ============================================================================
# Action Pool - All available actions for LLM agents
# ============================================================================

ACTION_POOL: List[Action] = [
    nav_to_obj,
    nav_to_position,
    nav_to_robot,
    pick,
    place,
    reset_arm,
    wait,
    send_request,
]

# Action name to action mapping
ACTION_MAP: Dict[str, Action] = {action.name: action for action in ACTION_POOL}


def get_action_by_name(name: str) -> Optional[Action]:
    """Get an action by its name."""
    return ACTION_MAP.get(name)


def get_action_descriptions() -> str:
    """Get formatted descriptions of all available actions."""
    descriptions = []
    for action in ACTION_POOL:
        desc = f"[**{action.name}**:\n"
        desc += f"  description: {action.description}\n"
        desc += f"  parameters: {action.parameters.model_json_schema()}\n"
        desc += "]\n"
        descriptions.append(desc)
    return "\n".join(descriptions)
