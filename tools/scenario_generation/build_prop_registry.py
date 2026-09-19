"""Generate the tracked SimReady prop registry from an NVD audit JSON.

Selects props by name, estimates mass from volume and a per-category density
table, and writes ``source/EAI_assets/EAI_assets/scenario_props.json``. The
registry stores paths relative to the NVD assets root; resolving them to real
files happens at runtime via ``EAI_NVD_ASSET_ROOT``, so a clean checkout
without the downloaded assets simply has an empty prop registry.

Usage:
  python tools/scenario_generation/build_prop_registry.py \
    --audit tmp/nvd_audit/simready_props.json --nvd-root /home/pupil/下载/NVD_assets
"""

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    import common
else:
    from . import common

# name substring -> (category, density kg/m^3, default modes)
CATEGORY_RULES = [
    ("trafficcone", "cone", 220.0, ("dynamic", "static")),
    ("popupcone", "cone", 120.0, ("dynamic", "static")),
    ("pallet", "pallet", 90.0, ("dynamic", "static")),
    ("chair", "furniture", 120.0, ("dynamic",)),
    ("stool", "furniture", 120.0, ("dynamic",)),
    ("table", "furniture", 150.0, ("dynamic", "static")),
    ("orange", "produce", 950.0, ("dynamic",)),
    ("lemon", "produce", 950.0, ("dynamic",)),
    ("avocado", "produce", 950.0, ("dynamic",)),
    ("pomegranate", "produce", 950.0, ("dynamic",)),
    ("lime", "produce", 950.0, ("dynamic",)),
    ("lychee", "produce", 950.0, ("dynamic",)),
]

SELECTED = [
    "trafficcone_a01",
    "heavydutytrafficcone_a01",
    "popupcone_a01",
    "orange_01",
    "lemon_01",
    "avocado01",
    "pallet_asm",
    "chair_wire",
]


def classify(name: str, volume_m3: float) -> tuple[str, float, tuple[str, ...]]:
    for substring, category, density, modes in CATEGORY_RULES:
        if substring in name:
            return category, round(max(volume_m3, 0.0002) * density, 2), modes
    return "misc", round(max(volume_m3, 0.0005) * 200.0, 2), ("dynamic",)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--nvd-root", required=True, help="Local NVD_assets root (recorded, not embedded)")
    parser.add_argument("--output", default="source/EAI_assets/EAI_assets/scenario_props.json")
    parser.add_argument("--select", nargs="*", default=SELECTED,
                        help="Prop names to include (default: curated first batch)")
    args = parser.parse_args(argv)

    audit = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    by_name = {p["prop"]: p for p in audit.get("props", []) if "size_m" in p}
    nvd_root = Path(args.nvd_root)

    selected = []
    missing = []
    for name in args.select:
        prop = by_name.get(name)
        if prop is None:
            missing.append(name)
            continue
        main_usd = Path(prop["usd"])
        # Spawn uses the non-instanced geometry layer: the main USD wraps the
        # mesh in an instanced prim, which Isaac Lab's RigidObject cannot
        # back with rigid-body state.
        usd = main_usd.with_name(f"{main_usd.stem}_base.usd")
        if not usd.exists():
            usd = main_usd
        try:
            # USD treats "@" as a path delimiter, so package dirs like
            # "SimReady_Warehouse_01_NVD@10010" break reference resolution.
            # The registry stores sanitized relative paths ("_v"); point
            # EAI_NVD_ASSET_ROOT at a shadow root of sanitized symlinks.
            usd_rel = str(usd.relative_to(nvd_root)).replace("@", "_v")
        except ValueError:
            print(f"[registry] {name}: usd not under nvd root, skipping", file=sys.stderr)
            continue
        # sanity: the file must be reachable under the sanitized shadow root
        shadow = Path(str(nvd_root).rstrip("/"))  # sanitized tree sits beside it
        shadow = shadow.parent / (shadow.name + "_sanitized")
        if not (shadow / usd_rel).exists():
            print(f"[registry] {name}: sanitized usd missing under {shadow}, skipping", file=sys.stderr)
            continue
        size = prop["size_m"]
        volume = size[0] * size[1] * size[2]
        category, mass, modes = classify(name, volume)
        selected.append({
            "key": f"nvd_{name}",
            "category": category,
            "usd_rel": usd_rel,
            "size_m": size,
            "suggested_mass_kg": mass,
            "modes": list(modes),
        })

    if missing:
        print(f"[registry] not found in audit: {missing}", file=sys.stderr)

    registry = {
        "version": 1,
        "source": "NVIDIA SimReady props (NVD download); audit of the untouched packages",
        "license_note": "NVIDIA Omniverse asset terms apply; local research use. "
                        "Confirm redistribution rights before publishing derived datasets.",
        "nvd_root_note": "Resolve usd_rel against the EAI_NVD_ASSET_ROOT environment variable "
                         "at runtime; props are absent from the registry when the root is unset.",
        "props": sorted(selected, key=lambda item: item["key"]),
    }
    output = Path(args.output)
    output.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[registry] wrote {output} with {len(selected)} props")
    for item in selected:
        print(f"  {item['key']}: {item['category']} mass={item['suggested_mass_kg']}kg "
              f"size={item['size_m']}")
    return 0 if len(selected) == len(args.select) else 1


if __name__ == "__main__":
    raise SystemExit(main())
