"""Archive unmodified sensor pixels and parameters from their render product."""

import hashlib
from pathlib import Path

import numpy as np


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_value(value):
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value.tolist() if hasattr(value, "tolist") else value


def archive_frame(directory, identifier, frame, calibration):
    from PIL import Image

    pixels = np.asarray(frame)
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError("camera archive requires uint8 RGB or RGBA pixels")
    parameters = calibration["parameters"]
    if list(parameters["renderProductResolution"]) != [pixels.shape[1], pixels.shape[0]]:
        raise ValueError("camera parameters and RGB dimensions differ")
    key = hashlib.sha256(identifier.encode()).hexdigest()[:24]
    path = Path(directory) / f"{key}.npy"
    preview = path.with_suffix(".png")
    with path.open("xb") as stream:
        np.save(stream, pixels, allow_pickle=False)
    Image.fromarray(pixels).save(preview)
    return {"archive_path": str(path.resolve()), "archive_sha256": file_digest(path),
            "preview_path": str(preview.resolve()), "preview_sha256": file_digest(preview),
            "camera_calibration": json_value(calibration)}


def verify_archived_frame(config):
    path = config.get("archive_path")
    if path is None:
        return
    if file_digest(path) != config.get("archive_sha256"):
        raise ValueError("archived camera pixels changed")
    pixels = np.load(path, allow_pickle=False)
    if (list(pixels.shape) != config["frame_shape"] or str(pixels.dtype) != config["frame_dtype"]
            or hashlib.sha256(pixels.tobytes()).hexdigest() != config["frame_sha256"]):
        raise ValueError("archived camera array differs from its observation")
    if file_digest(config["preview_path"]) != config.get("preview_sha256"):
        raise ValueError("archived camera preview changed")


def project_world_points(points_m, calibration):
    parameters = calibration["parameters"]
    if parameters["cameraModel"] != "pinhole":
        raise ValueError("projection audit currently requires a pinhole camera")
    points = np.asarray(points_m, dtype=float)
    view = np.asarray(parameters["cameraViewTransform"], dtype=float).reshape(4, 4)
    projection = np.asarray(parameters["cameraProjection"], dtype=float).reshape(4, 4)
    units = float(parameters["metersPerSceneUnit"])
    width, height = parameters["renderProductResolution"]
    if (points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all()
            or not np.isfinite(view).all() or not np.isfinite(projection).all()
            or not np.isfinite(units) or units <= 0 or width <= 0 or height <= 0):
        raise ValueError("camera projection requires finite metric points and parameters")
    homogeneous = np.column_stack((points / units, np.ones(len(points))))
    camera_points = homogeneous @ view
    clip = camera_points @ projection
    front = clip[:, 3] > 0
    pixels = np.full((len(points), 2), np.nan)
    normalized = clip[front, :2] / clip[front, 3:4]
    pixels[front] = np.column_stack(((normalized[:, 0] + 1) * width / 2,
                                    (1 - normalized[:, 1]) * height / 2))
    return {"pixels": [list(point) if visible else None for point, visible in zip(pixels, front)],
            "in_front": front.tolist(),
            "in_image": (front & (pixels[:, 0] >= 0) & (pixels[:, 0] < width)
                         & (pixels[:, 1] >= 0) & (pixels[:, 1] < height)).tolist(),
            "depth_m": (-camera_points[:, 2] * units).tolist()}


class CameraFrameArchive:
    def __init__(self, directory, managers):
        import omni.replicator.core as rep
        import omni.usd

        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.bindings = {}
        stage = omni.usd.get_context().get_stage()
        try:
            for manager in managers:
                if manager is None:
                    continue
                for agent, spec in manager._specs.items():
                    if not spec.camera:
                        continue
                    root = f"/World/envs/env_0/{agent}/"
                    mount = getattr(spec, "camera_left_mount_relative_path", None)
                    if mount is None:
                        mount = spec.camera_mount_link
                    suffix = f"/{mount.strip('/')}/Camera"
                    matches = []
                    for product in manager._runtime._render_products:
                        product_path = str(getattr(product, "path", product))
                        prim = stage.GetPrimAtPath(product_path)
                        cameras = prim.GetRelationship("camera").GetTargets() if prim else []
                        if len(cameras) == 1 and str(cameras[0]).startswith(root) and str(cameras[0]).endswith(suffix):
                            matches.append((product, product_path, str(cameras[0])))
                    if len(matches) != 1:
                        raise ValueError(f"{agent}: camera archive requires one matching owned render product")
                    product, product_path, camera_path = matches[0]
                    parameters = rep.AnnotatorRegistry.get_annotator("CameraParams")
                    reference = rep.AnnotatorRegistry.get_annotator("ReferenceTime")
                    self.bindings[agent] = {"parameters": parameters, "reference": reference,
                                            "camera_prim_path": camera_path, "render_product_path": product_path}
                    parameters.attach([product])
                    reference.attach([product])
        except Exception:
            self.close()
            raise

    def store(self, agent, identifier, frame):
        binding = self.bindings[agent]
        parameters = json_value(binding["parameters"].get_data())
        calibration = {"parameters": parameters,
                       "reference_time": json_value(binding["reference"].get_data()),
                       "camera_prim_path": binding["camera_prim_path"],
                       "render_product_path": binding["render_product_path"],
                       "source": "CameraParams_and_ReferenceTime_on_RGB_render_product",
                       "matrix_convention": "row_vector_world_to_USD_camera_then_projection",
                       "limitations": ["Simulator camera parameters; not hardware calibration",
                                       "Reference time belongs to rendering, not necessarily the current control tick"]}
        return archive_frame(self.directory, identifier, frame, calibration)

    def close(self):
        for binding in self.bindings.values():
            for key in ("parameters", "reference"):
                binding[key].detach()
        self.bindings.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
