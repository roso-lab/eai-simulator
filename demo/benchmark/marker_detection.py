"""Image-only fiducial decoding with pinhole pose estimates and explicit inputs."""

import math

import cv2
import numpy as np


DICTIONARY_NAME = "DICT_4X4_50"
MARKER_SIDE_M = 0.09


def pinhole_intrinsics(calibration):
    parameters = calibration["parameters"]
    if parameters["cameraModel"] != "pinhole":
        raise ValueError("marker pose estimation requires a pinhole camera")
    projection = np.asarray(parameters["cameraProjection"], dtype=float).reshape(4, 4)
    width, height = parameters["renderProductResolution"]
    if (not np.isfinite(projection).all() or width <= 0 or height <= 0
            or projection[0, 0] <= 0 or projection[1, 1] <= 0
            or not np.isclose(projection[2, 3], -1)
            or not np.allclose(projection[[0, 1, 3], 3], 0)
            or not np.allclose(projection[[0, 1, 3, 3], [1, 0, 0, 1]], 0)):
        raise ValueError("unsupported pinhole projection matrix")
    return np.array([[width * projection[0, 0] / 2, 0, width * (1 - projection[2, 0]) / 2],
                     [0, height * projection[1, 1] / 2, height * (1 + projection[2, 1]) / 2],
                     [0, 0, 1]], dtype=float)


def estimate_marker_pose(corners_px, calibration, *, marker_side_m=MARKER_SIDE_M):
    if (isinstance(marker_side_m, bool) or not isinstance(marker_side_m, (int, float))
            or not math.isfinite(marker_side_m) or marker_side_m <= 0):
        raise ValueError("marker size must be finite and positive")
    corners = np.asarray(corners_px, dtype=float)
    if corners.shape != (4, 2) or not np.isfinite(corners).all():
        raise ValueError("marker corners must be four finite image points")
    half = marker_side_m / 2
    object_points = np.array([[-half, half, 0], [half, half, 0],
                              [half, -half, 0], [-half, -half, 0]], dtype=float)
    intrinsics = pinhole_intrinsics(calibration)
    candidates = []
    for method, flag in (("IPPE_SQUARE", cv2.SOLVEPNP_IPPE_SQUARE), ("ITERATIVE", cv2.SOLVEPNP_ITERATIVE)):
        solved, rotations, translations, _ = cv2.solvePnPGeneric(
            object_points, corners, intrinsics, np.zeros(5), flags=flag)
        if not solved:
            continue
        for rotation, translation in zip(rotations, translations):
            transformed = object_points @ cv2.Rodrigues(rotation)[0].T + translation.reshape(1, 3)
            if not np.isfinite(transformed).all() or np.any(transformed[:, 2] <= 0):
                continue
            projected = cv2.projectPoints(object_points, rotation, translation, intrinsics, np.zeros(5))[0]
            error = float(np.sqrt(np.mean(np.sum((projected.reshape(4, 2) - corners) ** 2, axis=1))))
            candidates.append((error, translation.reshape(3), method))
    if not candidates:
        raise ValueError("marker has no finite forward pose solution")
    candidates.sort(key=lambda candidate: candidate[0])
    parameters = calibration["parameters"]
    units = float(parameters["metersPerSceneUnit"])
    view = np.asarray(parameters["cameraViewTransform"], dtype=float).reshape(4, 4)
    if not math.isfinite(units) or units <= 0 or not np.isfinite(view).all():
        raise ValueError("marker world pose requires finite camera parameters and scene units")
    translation = candidates[0][1]
    camera_usd = np.array([translation[0] / units, -translation[1] / units, -translation[2] / units, 1])
    world = camera_usd @ np.linalg.inv(view)
    if not np.isfinite(world).all() or not np.isclose(world[3], 1):
        raise ValueError("camera transform is not a finite affine transform")
    return {"estimated_position_m": (world[:3] * units).tolist(),
            "camera_translation_m": translation.tolist(), "reprojection_rmse_px": candidates[0][0],
            "pose_solver": candidates[0][2],
            "alternative_reprojection_rmse_px": [candidate[0] for candidate in candidates[1:]],
            "alternative_pose_solvers": [candidate[2] for candidate in candidates[1:]],
            "position_subject": "printed_marker_center", "world_pose_source": "simulator_camera_extrinsics"}


def detect_markers(frame, calibration, *, marker_side_m=MARKER_SIDE_M):
    pixels = np.asarray(frame)
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError("marker detection requires uint8 RGB or RGBA pixels")
    if list(calibration["parameters"]["renderProductResolution"]) != [pixels.shape[1], pixels.shape[0]]:
        raise ValueError("marker frame resolution differs from camera calibration")
    pinhole_intrinsics(calibration)
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGBA2GRAY if pixels.shape[2] == 4 else cv2.COLOR_RGB2GRAY)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_APRILTAG
    detector = cv2.aruco.ArucoDetector(dictionary, parameters)
    corners, identifiers, rejected = detector.detectMarkers(gray)
    detections = []
    for marker_corners, identifier in zip(corners, identifiers.flatten() if identifiers is not None else []):
        record = {"marker_id": int(identifier), "target_id": f"inventory_marker:{int(identifier)}",
                  "corners_px": marker_corners.reshape(4, 2).astype(float).tolist(), "confidence": None}
        try:
            record.update(estimate_marker_pose(record["corners_px"], calibration, marker_side_m=marker_side_m))
            record["pose_valid"] = True
        except (ValueError, np.linalg.LinAlgError, cv2.error) as error:
            record.update(pose_valid=False, pose_error=str(error))
        detections.append(record)
    return {"schema": "eai.marker_frame_detection", "version": 2,
            "detector": "opencv_aruco_planar_pnp", "detector_version": cv2.__version__,
            "corner_refinement": "CORNER_REFINE_APRILTAG",
            "dictionary": DICTIONARY_NAME, "marker_side_m": marker_side_m,
            "rejected_candidate_count": len(rejected), "detections": detections,
            "limitations": ["Printed marker decoding does not identify unlabelled objects",
                            "Known printed size supplies metric scale; camera extrinsics come from the simulator",
                            "Reprojection error is not a calibrated confidence or localization error",
                            "Planar pose ambiguity is retained through alternative solution residuals"]}


def fuse_marker_views(frame_results, observations):
    by_id = {observation["observation_id"]: observation for observation in observations}
    grouped = {}
    for frame in frame_results:
        observation = by_id[frame["source_observation_id"]]
        node = observation["observation_id"].split(":frame:")[0]
        for detection in frame["detections"]:
            if not detection["pose_valid"]:
                continue
            key = (node, observation["frame_id"], detection["marker_id"])
            grouped.setdefault(key, []).append((observation, detection))
    return [{"view_node": node, "view": view, "marker_id": marker_id,
             "estimated_position_m": np.median([detection["estimated_position_m"]
                                                 for _, detection in entries], axis=0).tolist(),
             "source_observation_ids": [observation["observation_id"] for observation, _ in entries],
             "sample_count": len(entries), "confidence": None,
             "timestamp_start_s": min(observation["timestamp_s"] for observation, _ in entries),
             "timestamp_end_s": max(observation["timestamp_s"] for observation, _ in entries),
             "method": "coordinate_median_all_valid_poses_per_view_and_decoded_id",
             "assumption": "static printed marker during this observation window"}
            for (node, view, marker_id), entries in sorted(grouped.items())]
