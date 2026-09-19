"""Compare Kinova Gen3 USD inertials with an external Kortex URDF."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_urdf(path: Path) -> dict[str, dict[str, object]]:
    root = ET.parse(path).getroot()
    result = {}
    for link in root.findall("link"):
        inertial = link.find("inertial")
        if inertial is None:
            continue
        mass = inertial.find("mass")
        inertia = inertial.find("inertia")
        origin = inertial.find("origin")
        if mass is None or inertia is None:
            continue
        result[link.attrib["name"]] = {
            "mass_kg": float(mass.attrib["value"]),
            "centerOfMass": [float(v) for v in (origin.get("xyz", "0 0 0") if origin is not None else "0 0 0").split()],
            "inertial_rpy_rad": [float(v) for v in (origin.get("rpy", "0 0 0") if origin is not None else "0 0 0").split()],
            "inertia_kg_m2": {key: float(inertia.attrib[key])
                              for key in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")},
        }
    return result


def compare(usd_inertials: dict[str, dict], urdf: dict[str, dict[str, object]]) -> dict:
    rows = []
    for usd_path, fields in usd_inertials.items():
        name = usd_path.rsplit("/", 1)[-1]
        source = urdf.get(name)
        if source is None:
            rows.append({"link": name, "status": "no_urdf_link"})
            continue
        usd_mass = fields.get("mass_kg")
        source_mass = source["mass_kg"]
        mass_match = usd_mass is not None and math.isclose(float(usd_mass), float(source_mass), rel_tol=2e-3, abs_tol=2e-4)
        rows.append({"link": name, "status": "match" if mass_match else "mass_mismatch",
                     "usd_mass_kg": usd_mass, "urdf_mass_kg": source_mass,
                     "usd_com": fields.get("centerOfMass"), "urdf_com": source["centerOfMass"],
                     "usd_diagonal_inertia": fields.get("diagonalInertia"),
                     "urdf_inertia": source["inertia_kg_m2"]})
    return {"rows": rows, "matched_mass_links": sum(row["status"] == "match" for row in rows),
            "mass_mismatch_links": [row["link"] for row in rows if row["status"] == "mass_mismatch"],
            "zero_mass_source_links": [row["link"] for row in rows if row.get("urdf_mass_kg") == 0.0]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--usd-report", type=Path, required=True)
    parser.add_argument("--urdf", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.usd_report.read_text())
    urdf = read_urdf(args.urdf)
    result = compare(data["authored_inertials"], urdf)
    result.update({"schema": "eai.kinova_gen3_urdf_usd_audit.v1",
                   "usd_report": str(args.usd_report.resolve()),
                   "urdf": str(args.urdf.resolve()),
                   "usd_report_sha256": sha256(args.usd_report),
                   "urdf_sha256": sha256(args.urdf),
                   "zero_mass_source_links": [row["link"] for row in result["rows"]
                                               if row.get("urdf_mass_kg") == 0.0],
                   "limitations": ["URDF and USD inertial frames are listed but not automatically transformed",
                                   "Agreement with URDF is source consistency, not hardware measurement"]})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("matched_mass_links", "mass_mismatch_links", "zero_mass_source_links")}))


if __name__ == "__main__":
    main()
