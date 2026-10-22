"""Reproducible synthetic replay-cache measurement; no simulator or GPU claims.

Run from the project root with ``python -m tools.physics_replay.validation.cache_benchmark
--output /absolute/new-report.json``. Compares retention policies in the same
reader, not different physics implementations. Array byte counts are not RSS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time

import numpy as np

from ..store import EpisodeReader, EpisodeWriter
from ..types import FrameRecord, Manifest


def measure(root: Path, frame_count: int, **options) -> dict:
    reader = EpisodeReader(root, **options)
    started = time.perf_counter()
    reader.validate_integrity()
    validation_seconds = time.perf_counter() - started
    peak_retained_bytes = reader.cache_info["bytes"]
    digest = hashlib.sha256()
    started = time.perf_counter()
    for step in range(frame_count + 1):
        for key, value in sorted(reader.read_state(step).items()):
            digest.update(key.encode())
            digest.update(value.tobytes())
        peak_retained_bytes = max(peak_retained_bytes, reader.cache_info["bytes"])
    return {"validation_seconds": validation_seconds,
            "read_seconds": time.perf_counter() - started,
            "peak_retained_array_bytes": peak_retained_bytes,
            "state_digest": digest.hexdigest(), "cache": reader.cache_info}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--segments", type=int, default=64)
    parser.add_argument("--frames-per-segment", type=int, default=8)
    parser.add_argument("--width", type=int, default=4096)
    args = parser.parse_args(argv)
    if min(args.segments, args.frames_per_segment, args.width) <= 0:
        parser.error("segment/frame/width counts must be positive")
    if args.output.exists():
        parser.error("output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = args.segments * args.frames_per_segment
    with tempfile.TemporaryDirectory(prefix="replay-cache-", dir=args.output.parent) as directory:
        root = Path(directory) / "episode"
        values = np.arange(args.width, dtype=np.float32)
        with EpisodeWriter(root, Manifest(environment={"synthetic": True}, targets=[],
                                          segment_frames=args.frames_per_segment)) as writer:
            for step in range(count):
                writer.append(FrameRecord(step, step * 0.01, {"state": values + step},
                                          {"state": values + step + 1}, {"action": values}))
        retain_all = measure(root, count, max_cached_segments=args.segments, max_cache_bytes=2**63-1)
        bounded = measure(root, count)
    report = {"scope": "synthetic_cache_retention", "segments": args.segments, "frames": count,
              "array_width": args.width, "retain_all": retain_all, "bounded_default": bounded,
              "states_equal": retain_all["state_digest"] == bounded["state_digest"],
              "retained_byte_reduction_fraction": 1 - bounded["peak_retained_array_bytes"] / retain_all["peak_retained_array_bytes"],
              "limits": "Retained decompressed array bytes only; not peak RSS, GPU capacity, physical fidelity or speedup."}
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report, indent=2))
    return 0 if report["states_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
