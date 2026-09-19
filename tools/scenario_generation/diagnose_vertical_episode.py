"""Read-only XYZ evidence extraction; does not launch Isaac or certify climbing."""

import argparse
import json
from pathlib import Path

from demo.benchmark.vertical_traversal import summarize_vertical_episode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--robot", default="go2_1")
    parser.add_argument("--reference-root-z-m", type=float,
                        help="Optional same-floor reference for numerical height-drop comparison only")
    args = parser.parse_args(argv)
    summaries = []
    for path in args.reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        summaries.append({"report": str(path.resolve()), **summarize_vertical_episode(
            report, robot_id=args.robot, reference_root_z_m=args.reference_root_z_m)})
    print(json.dumps({"protocol": "vertical_episode_diagnosis_v1", "reports": summaries},
                     indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
