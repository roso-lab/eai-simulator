from __future__ import annotations


def matches_robot_ee_path(path: str, robot_name: str, *, link_name: str = "wrist_3_link") -> bool:
    normalized_path = str(path).casefold()
    normalized_robot = str(robot_name).strip().casefold()
    if not normalized_robot or not normalized_path.endswith(f"/{link_name.casefold()}"):
        return False
    arm_segment = f"/{normalized_robot}_arm/"
    robot_segment = f"/{normalized_robot}/"
    return arm_segment in normalized_path or robot_segment in normalized_path
