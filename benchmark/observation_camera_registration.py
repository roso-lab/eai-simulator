"""Export builder camera registration into a resume-sensing patch.

Gate D of the observation resurrection task: every robot already carries a
builder-configured default camera (``RobotOption`` in
``source/EAI_hmrs/EAI_hmrs/env_builder.py``), while the robot resume
``sensing`` fields still hold ``camera_mount_pose: null`` and
``camera_publication: "pending_binding"``.  This tool extracts the actual
mount/rotation facts **statically** (AST, no Isaac imports) and emits a
patch document the resume owner applies; it never writes the resume files
themselves.

Usage::

    python -m benchmark.observation_camera_registration \
        --builder source/EAI_hmrs/EAI_hmrs/env_builder.py \
        --output benchmark/observation_camera_registration_patch.v1.json
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

_CAMERA_FIELDS = (
    "camera_mount_link", "camera_offset", "camera_rot",
    "realsense_mount_link", "realsense_offset", "realsense_rot",
    "orsus_mount_link", "orsus_offset",
)
_QUAT_FIELDS = {"camera_rot", "realsense_rot"}


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_camera_registration(builder_source: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Parse RobotOption constructor calls and collect camera registration facts."""
    if isinstance(builder_source, Mapping):
        text = builder_source.get("text", "")
        source_name = str(builder_source.get("name", "<memory>"))
        source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        path = Path(builder_source)
        text = path.read_text(encoding="utf-8")
        source_name = str(path)
        source_sha = _sha256_file(path)
    tree = ast.parse(text)
    robots: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "RobotOption"):
            continue
        entry: dict[str, Any] = {}
        # RobotOption(key, label, ...) takes identity positionally.
        if len(node.args) >= 1:
            value = _literal(node.args[0])
            if isinstance(value, str):
                entry["key"] = value
        if len(node.args) >= 2:
            value = _literal(node.args[1])
            if isinstance(value, str):
                entry["label"] = value
        for kw in node.keywords:
            if kw.arg is None or kw.arg not in _CAMERA_FIELDS + ("key", "label"):
                continue
            value = _literal(kw.value)
            if value is None:
                continue
            if kw.arg in ("key", "label"):
                entry[kw.arg] = value
            else:
                entry.setdefault("camera_registration", {})[kw.arg] = (
                    list(value) if isinstance(value, tuple) else value)
        robots.append(entry)

    registrations = []
    for entry in sorted((r for r in robots if r.get("key")), key=lambda r: r["key"]):
        reg = entry.get("camera_registration", {})
        key = entry["key"]
        # Publication rule mirrored from env_builder: a camera publishes when a
        # mount link (camera/realsense/orsus) is configured on the option.
        publishes = bool(reg.get("camera_mount_link")
                         or reg.get("realsense_mount_link")
                         or reg.get("orsus_mount_link"))
        path_kind = None
        if reg.get("camera_mount_link"):
            path_kind = "camera_mount"
        if reg.get("realsense_mount_link"):
            path_kind = path_kind or "realsense_mount"
        if reg.get("orsus_mount_link"):
            path_kind = path_kind or "orsus_mount"
        registrations.append({
            "builder_key": key,
            "label": entry.get("label", ""),
            "camera_registration": reg,
            "camera_path_kind": path_kind,
            "enable_camera_publish": publishes,
            "camera_publication_patch": (
                "builder_registered_default" if publishes else "not_configured_in_builder"),
        })
    return {
        "schema": "heterocoord.observation.camera_registration_patch.v1",
        "generated_from": {"file": source_name, "sha256": source_sha},
        "patch_semantics": (
            "resume sensing.camera_mount_pose ← camera_registration mount link/offset/rot; "
            "resume sensing.camera_publication ← camera_publication_patch; "
            "applied by the robot-resume owner, not by this tool"),
        "registrations": registrations,
    }


def preview_resume_patch(resume: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Return a proposed resume update without writing any resume file.

    Resume ownership remains with the robot-resume maintainer.  This helper is
    intentionally pure so Gate D can inspect the extracted values while the
    observation handoff cannot mutate a live resume or another session's file.
    """
    resume = json.loads(json.dumps(resume, ensure_ascii=False))
    by_key = {row["builder_key"]: row for row in patch["registrations"]}
    filled, publications, skipped = [], [], []
    for profile in resume.get("configuration_profiles", []):
        base = str(profile.get("profile_id", "")).split(".", 1)[0]
        reg = by_key.get(base)
        sensing = profile.get("sensing")
        if not isinstance(sensing, dict):
            continue
        if reg is None:
            skipped.append(profile.get("profile_id"))
            continue
        if sensing.get("camera_mount_pose") is None:
            mount = reg.get("camera_registration", {})
            link = (mount.get("realsense_mount_link") or mount.get("camera_mount_link")
                    or mount.get("orsus_mount_link"))
            offset = mount.get("realsense_offset") or mount.get("camera_offset") \
                or mount.get("orsus_offset") or [0.0, 0.0, 0.0]
            rot = mount.get("realsense_rot") or mount.get("camera_rot") \
                or [1.0, 0.0, 0.0, 0.0]
            sensing["camera_mount_pose"] = {
                "translation_m": [float(v) for v in offset[:3]],
                "rotation_wxyz": [float(v) for v in rot[:4]],
                "reference_frame": str(link),
                "mounting": str(reg.get("camera_path_kind")),
                "visual_shell_calibration": {
                    "translate_m": [0.0, 0.0, 0.0],
                    "rotate_xyz_deg": [0.0, 0.0, 0.0],
                    "scale": [1.0, 1.0, 1.0],
                    "reference_frame": "asset_default",
                },
            }
            filled.append(profile.get("profile_id"))
        if sensing.get("camera_publication") == "pending_binding":
            sensing["camera_publication"] = reg["camera_publication_patch"]
            publications.append(profile.get("profile_id"))
    return {"resume": resume, "filled_mount_pose": filled,
            "publication_bound": publications, "no_builder_entry": skipped}


def apply_to_resume(resume_path: str | Path, patch: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility preview; never writes ``resume_path``.

    The old helper name is retained for callers that only need a summary, but
    applying a Gate D patch is deliberately outside this package's ownership.
    """
    resume = json.loads(Path(resume_path).read_text(encoding="utf-8"))
    return preview_resume_patch(resume, patch)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--builder", help="path to env_builder.py")
    parser.add_argument("--output", help="patch JSON output path")
    parser.add_argument("--resume", help="preview a patch against this resume JSON (never writes it)")
    parser.add_argument("--patch", help="patch JSON used with --resume")
    args = parser.parse_args()
    if args.resume:
        if not args.patch:
            parser.error("--resume requires --patch")
        summary = apply_to_resume(args.resume, json.loads(Path(args.patch).read_text()))
        print(f"previewed {args.resume}: "
              f"{len(summary['filled_mount_pose'])} mounts filled, "
              f"{len(summary['publication_bound'])} publications bound")
        return 0
    if not (args.builder and args.output):
        parser.error("--builder and --output are required without --resume")
    patch = extract_camera_registration(args.builder)
    patch["generated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    Path(args.output).write_text(
        json.dumps(patch, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output} with {len(patch['registrations'])} registrations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
