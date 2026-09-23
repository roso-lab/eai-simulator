"""Print the restaurant source audit without starting Isaac Sim."""

from __future__ import annotations

import argparse
import json

from .scene_audit import audit_restaurant_scene


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usd-root")
    args = parser.parse_args(argv)
    if args.usd_root is None:
        from EAI_assets.asset_resolver import usd_root

        root = usd_root()
    else:
        root = args.usd_root
    report = audit_restaurant_scene(root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(not report["authoring_preflight_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
