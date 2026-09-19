"""Export the connected EAI robot capability coverage without inventing scores."""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.scenario_generation.capability_profile import (
    profile_for_dossier,
    validate_configuration_coverage,
)
from tools.scenario_generation.robot_dossier import all_robot_dossiers, all_dossiers_snapshot_hash, robot_dossier


def build_report(measurement_path: Path | None = None) -> dict:
    dossiers = all_robot_dossiers()
    measurements = {key: [] for key in dossiers}
    measurement_dossiers = {}
    if measurement_path is not None:
        from tools.scenario_generation.configuration_microtest import load_records, measurement_for_profile
        for record in load_records(measurement_path):
            if record["robot_key"] not in measurements:
                raise ValueError(f"measurement contains unknown robot {record['robot_key']!r}")
            attachments = record.get("condition", {}).get("attachments", [])
            candidate = robot_dossier(record["robot_key"], attachments=attachments)
            candidate_profile = profile_for_dossier(candidate)
            if candidate_profile["identity"]["configuration_id"] != record["configuration_id"]:
                raise ValueError("measurement configuration_id does not match its declared attachments")
            previous = measurement_dossiers.setdefault(record["robot_key"], candidate)
            if profile_for_dossier(previous)["identity"]["configuration_id"] != record["configuration_id"]:
                raise ValueError("measurement file mixes configurations for one robot key")
            measurements[record["robot_key"]].append(measurement_for_profile(record))
    profiles = {key: profile_for_dossier(measurement_dossiers.get(key, dossiers[key]), measurements[key])
                for key in dossiers}
    rows = validate_configuration_coverage(dossiers)
    for row in rows:
        profile = profiles[row["robot_key"]]
        row.update({"configuration_id": profile["identity"]["configuration_id"],
                    "sensors": list(profile["identity"]["sensors"]),
                    "attachments": list(profile["identity"]["attachments"]),
                    "radar_status": {axis["axis"]: axis["status"] for axis in profile["radar"]}})
    return {
        "schema": "eai.robot_capability_coverage",
        "version": 2,
        "robot_count": len(rows),
        "dossier_snapshot_sha256": all_dossiers_snapshot_hash(dossiers),
        "coverage": rows,
        "profiles": profiles,
        "measured_configuration_ids": sorted({profile["identity"]["configuration_id"]
                                               for profile in profiles.values()
                                               if profile["measurement_count"] or profile["protocol_check_count"]}),
        "radar_policy": {
            "unknown_axes": "render as unknown; never coerce to zero",
            "measured_values": "configuration-specific measurements only",
            "ranking": "not provided",
        },
        "deployment_evidence_policy": {
            "stationary_mount_to_dynamic_manipulation": "forbidden",
            "component_e7_to_mobile_configuration": "forbidden",
            "candidate_only_registration": "not selectable in Env DIY",
            "normalized_score_inference": "forbidden",
        },
        "limitations": [
            "static catalog and controller declarations do not establish runtime success",
            "unknown radar axes have no normalized score",
            "payload, grasp force, collision margin, wind resistance, and load dynamics remain unknown",
        ],
    }


def _radar_svg(profile: dict, *, size: int = 220) -> str:
    """Render measured axes only; unknown axes are deliberately left blank."""
    center = size / 2
    radius = size * 0.34
    axes = profile["radar"]
    points = []
    for index, axis in enumerate(axes):
        angle = -math.pi / 2 + (2 * math.pi * index / len(axes))
        x = center + radius * math.cos(angle)
        y = center + radius * math.sin(angle)
        points.append((x, y, angle, axis))

    layers = [
        f'<svg class="radar" viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="Capability radar for {html.escape(profile["identity"]["robot_key"])}">',
        '<circle cx="110" cy="110" r="74" fill="none" stroke="#c7cdd4"/>',
        '<circle cx="110" cy="110" r="37" fill="none" stroke="#e1e5e9"/>',
    ]
    for axis_x, axis_y, axis_angle, axis in points:
        label = html.escape(axis["axis"].replace("_", " "))
        layers.append(f'<line x1="{center:.1f}" y1="{center:.1f}" x2="{axis_x:.1f}" y2="{axis_y:.1f}" stroke="#c7cdd4"/>')
        label_x = center + (radius + 12) * math.cos(axis_angle)
        label_y = center + (radius + 12) * math.sin(axis_angle)
        layers.append(f'<text x="{label_x:.1f}" y="{label_y:.1f}" font-size="7" text-anchor="middle">{label}</text>')

    measured = []
    for point in points:
        axis_angle, axis = point[2], point[3]
        normalized = axis.get("normalized")
        if normalized is None:
            continue
        value = max(0.0, min(1.0, float(normalized)))
        measured.append((center + radius * value * math.cos(axis_angle),
                         center + radius * value * math.sin(axis_angle)))
    if measured:
        layers.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in measured) +
                      '" fill="none" stroke="#1769aa" stroke-width="2"/>')
        layers.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#1769aa"/>'
                      for x, y in measured)
    layers.append('</svg>')
    return "".join(layers)


def _html(report: dict) -> str:
    rows = []
    for item in report["coverage"]:
        profile = report["profiles"][item["robot_key"]]
        radar = "<br>".join(
            f"{axis['axis']}: {axis['status']}"
            + (f" ({axis['normalized']:.3f})" if axis["normalized"] is not None else "")
            for axis in profile["radar"]
        )
        checks = "".join(
            f'<li>{html.escape(check["protocol_id"])} — '
            f'{html.escape(check["scope"]["target"]["id"])}: '
            f'{html.escape(json.dumps(check["conditions"]["raw_metrics"], ensure_ascii=False))}</li>'
            for check in profile["protocol_checks"])
        deployment_lines = []
        for group, label in (("env_diy_variants", "Env DIY"), ("candidate_only", "candidate")):
            for evidence in profile["deployment_evidence"][group]:
                warning = " ".join(evidence["warnings"])
                deployment_lines.append(
                    f'<li><strong>{html.escape(evidence["manipulator"])}</strong> '
                    f'[{label}]: {html.escape(evidence["mount_evidence"]["status"])}; '
                    f'dynamic={html.escape(evidence["dynamic_manipulation_admission"]["status"])}'
                    + (f'<br><em>{html.escape(warning)}</em>' if warning else "")
                    + "</li>"
                )
        deployments = "".join(deployment_lines)
        rows.append(
            "<tr>"
            f"<td>{item['robot_key']}</td><td>{item['domain']}</td>"
            f"<td>{item['locomotion_mode']}</td><td>{item['control_adapter']}</td>"
            f"<td>{_radar_svg(profile)}<div>{radar}</div></td>"
            f"<td><ul>{deployments}</ul></td>"
            f"<td><ul>{checks}</ul></td>"
            "</tr>"
        )
    return """<!doctype html>
<meta charset="utf-8">
<title>EAI capability coverage</title>
<style>body{font:14px sans-serif;margin:2rem}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:.45rem;vertical-align:top}.radar{width:220px;height:220px}.radar+div{font-size:12px}br{line-height:1.5}</style>
<h1>EAI robot capability coverage</h1>
<p>Unknown axes remain unknown. Static mount and component E7 evidence do not fill radar axes, admit dynamic manipulation, or rank robots.</p>
<table><thead><tr><th>Robot</th><th>Domain</th><th>Locomotion</th><th>Control adapter</th><th>Radar status</th><th>Manipulator deployment evidence</th><th>Scoped protocol checks</th></tr></thead>
<tbody>""" + "".join(rows) + "</tbody></table>"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "html"), default="json")
    parser.add_argument("--measurements", type=Path,
                        help="Validated configuration micro-test records to overlay")
    args = parser.parse_args(argv)
    report = build_report(args.measurements)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "html":
        args.output.write_text(_html(report), encoding="utf-8")
    else:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
