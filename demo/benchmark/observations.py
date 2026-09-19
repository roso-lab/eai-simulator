"""Evidence-safe observation contracts for benchmark task communication."""

from __future__ import annotations

import math
import hashlib
from copy import deepcopy
from typing import Any, Mapping


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _position(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} must contain three coordinates")
    return [_finite(item, label) for item in value]


class ObservationStore:
    """Store observations and explicit consumption events for one episode."""

    def __init__(self) -> None:
        self._observations: dict[str, dict[str, Any]] = {}
        self._consumptions: list[dict[str, Any]] = []
        self._detections: list[dict[str, Any]] = []

    def publish(self, *, observation_id: str, producer: str, sensor: str,
                sensor_config: Mapping[str, Any], timestamp_s: float,
                frame_id: str, target_ref: str, position_m: Any,
                quality: Mapping[str, Any], valid_for_s: float,
                estimate_kind: str = "estimated_target") -> dict[str, Any]:
        if not isinstance(observation_id, str) or not observation_id:
            raise ValueError("observation_id is required")
        if observation_id in self._observations:
            raise ValueError(f"duplicate observation_id {observation_id!r}")
        for value, label in ((producer, "producer"), (sensor, "sensor"),
                             (frame_id, "frame_id"), (target_ref, "target_ref")):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{label} is required")
        if not isinstance(estimate_kind, str) or not estimate_kind:
            raise ValueError("estimate_kind is required")
        timestamp = _finite(timestamp_s, "timestamp_s")
        valid_for = _finite(valid_for_s, "valid_for_s")
        if valid_for <= 0:
            raise ValueError("valid_for_s must be positive")
        if not isinstance(sensor_config, Mapping) or not isinstance(quality, Mapping):
            raise ValueError("sensor_config and quality must be mappings")
        normalized_quality = dict(quality)
        if "score" in normalized_quality:
            score = _finite(normalized_quality["score"], "quality.score")
            if not 0 <= score <= 1:
                raise ValueError("quality.score must be in [0, 1]")
            normalized_quality["score"] = score
        record = {
            "observation_id": observation_id,
            "producer": producer,
            "sensor": sensor,
            "sensor_config": deepcopy(dict(sensor_config)),
            "timestamp_s": timestamp,
            "frame_id": frame_id,
            "target_ref": target_ref,
            "target_estimate": {
                "kind": estimate_kind,
                "position_m": _position(position_m, "position_m"),
            },
            "quality": normalized_quality,
            "valid_until_s": timestamp + valid_for,
            "consumed_by": [],
        }
        self._observations[observation_id] = record
        return deepcopy(record)

    def publish_frame(self, *, observation_id: str, producer: str, sensor: str,
                      sensor_config: Mapping[str, Any], timestamp_s: float,
                      frame_id: str, target_ref: str, position_m: Any,
                      frame: Any, valid_for_s: float,
                      archive_path: str | None = None) -> dict[str, Any]:
        """Publish metadata for a frame obtained from a live sensor.

        The frame bytes are represented by a digest and optional archive path;
        the store deliberately does not interpret pixels or inject ground
        truth.  Callers must provide a detector if a task needs an object
        estimate rather than evidence that a frame was acquired.
        """
        array = frame
        shape = tuple(int(value) for value in getattr(array, "shape", ()))
        size = int(getattr(array, "size", 0))
        if not shape or size <= 0:
            raise ValueError("frame must contain at least one pixel")
        digest = hashlib.sha256(array.tobytes()).hexdigest()
        config = dict(sensor_config)
        config.update({"frame_sha256": digest, "frame_shape": list(shape),
                       "frame_dtype": str(getattr(array, "dtype", "unknown"))})
        normalized_quality = {"valid_pixel_count": size, "frame_acquired": True}
        if archive_path is not None:
            config["archive_path"] = str(archive_path)
        return self.publish(
            observation_id=observation_id, producer=producer, sensor=sensor,
            sensor_config=config, timestamp_s=timestamp_s, frame_id=frame_id,
            target_ref=target_ref, position_m=position_m,
            estimate_kind="sensor_pose_only",
            quality=normalized_quality, valid_for_s=valid_for_s,
        )

    def latest_for_target(self, target_ref: str) -> dict[str, Any] | None:
        candidates = [record for record in self._observations.values()
                      if record["target_ref"] == target_ref]
        if not candidates:
            return None
        return deepcopy(max(candidates, key=lambda record: record["timestamp_s"]))

    def consume(self, observation_id: str, *, consumer: str, timestamp_s: float) -> dict[str, Any]:
        record = self._observations.get(observation_id)
        if record is None:
            raise KeyError(f"unknown observation_id {observation_id!r}")
        if not isinstance(consumer, str) or not consumer:
            raise ValueError("consumer is required")
        timestamp = _finite(timestamp_s, "timestamp_s")
        if timestamp < record["timestamp_s"]:
            raise ValueError("consumption timestamp precedes observation")
        if timestamp > record["valid_until_s"]:
            raise ValueError(f"observation {observation_id!r} is expired")
        event = {"consumer": consumer, "timestamp_s": timestamp}
        record["consumed_by"].append(event)
        self._consumptions.append({"observation_id": observation_id, **event})
        return deepcopy(event)

    def publish_detection(self, *, detection_id: str, source_observation_id: str,
                          detector_source: str, detector_version: str,
                          target_id: str, estimated_position_m: Any,
                          confidence: float | None, timestamp_s: float,
                          item_code: str | None = None) -> dict[str, Any]:
        """Record detector output without adding evaluator ground truth.

        A detector result is attached to a previously acquired observation.
        The store records what the detector said; target identity and position
        accuracy are evaluated separately against evaluator-owned truth.
        """
        if not isinstance(detection_id, str) or not detection_id:
            raise ValueError("detection_id is required")
        if any(item["detection_id"] == detection_id for item in self._detections):
            raise ValueError(f"duplicate detection_id {detection_id!r}")
        source = self._observations.get(source_observation_id)
        if source is None:
            raise KeyError(f"unknown source_observation_id {source_observation_id!r}")
        for value, label in ((detector_source, "detector_source"),
                             (detector_version, "detector_version"),
                             (target_id, "target_id")):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{label} is required")
        timestamp = _finite(timestamp_s, "timestamp_s")
        if timestamp < source["timestamp_s"] or timestamp > source["valid_until_s"]:
            raise ValueError("detection timestamp must be within source observation validity")
        if confidence is not None:
            confidence = _finite(confidence, "confidence")
            if not 0 <= confidence <= 1:
                raise ValueError("confidence must be in [0, 1]")
        if item_code is not None and (not isinstance(item_code, str) or not item_code):
            raise ValueError("item_code must be a nonempty string when supplied")
        record = {
            "detection_id": detection_id,
            "source_observation_id": source_observation_id,
            "detector_source": detector_source,
            "detector_version": detector_version,
            "target_id": target_id,
            "estimated_position_m": _position(estimated_position_m, "estimated_position_m"),
            "confidence": confidence,
            "timestamp_s": timestamp,
        }
        if item_code is not None:
            record["item_code"] = item_code
        self._detections.append(record)
        return deepcopy(record)

    def get(self, observation_id: str) -> dict[str, Any]:
        record = self._observations.get(observation_id)
        if record is None:
            raise KeyError(f"unknown observation_id {observation_id!r}")
        return deepcopy(record)

    def report(self) -> dict[str, Any]:
        return {
            "schema": "eai.observation_store",
            "version": 2,
            "observations": [deepcopy(self._observations[key]) for key in sorted(self._observations)],
            "consumptions": deepcopy(self._consumptions),
            "detections": deepcopy(self._detections),
            "limitations": [
                "publishing an observation does not establish sensor accuracy",
                "the task producer must supply a real sensor callback and configuration evidence",
                "detector output records claims but does not establish target identity or localization accuracy",
                "ground truth remains evaluator-only and is not inserted by this store",
            ],
        }


__all__ = ["ObservationStore"]
