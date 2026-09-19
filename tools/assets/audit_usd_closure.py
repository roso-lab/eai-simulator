"""Audit local USD dependencies without starting Isaac Sim or downloading assets."""

import argparse
import json
from pathlib import Path

if __package__ in (None, ""):
    from usd_closure import audit
else:
    from .usd_closure import audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--boundary", type=Path, default=Path(__file__).resolve().parents[2] / "usd")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--hashes", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    report = audit(args.roots, boundary=args.boundary, hashes=args.hashes)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, ensure_ascii=False, indent=2))
    return int(args.strict and not report["validated"])


if __name__ == "__main__":
    raise SystemExit(main())
