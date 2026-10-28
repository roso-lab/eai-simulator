"""Clone frozen upstream versions outside the project, without editing them.

Run from the project root: python3 -m tools.algorithm_adapters.setup_sources.
Existing directories are validated, never reset or removed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from algorithm.system.coordination.native_runtime import verify_source


REGISTER = Path(__file__).resolve().parents[2] / "algorithm/heterocoord/paper_algorithms.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path,
                        default=Path.home() / ".cache/heterocoord/upstream")
    parser.add_argument("--algorithm", action="append")
    parser.add_argument("--local-sources", type=Path,
                        help="JSON mapping algorithm keys to existing upstream git roots")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    spec = json.loads(REGISTER.read_text())
    keys = args.algorithm or list(spec["algorithms"])
    unknown = set(keys) - set(spec["algorithms"])
    if unknown:
        parser.error(f"unknown algorithms: {sorted(unknown)}")
    local = json.loads(args.local_sources.read_text()) if args.local_sources else {}
    args.cache_root.mkdir(parents=True, exist_ok=True)
    records = {}
    for key in keys:
        row = spec["algorithms"][key]
        destination = args.cache_root / row["cache_name"]
        if not destination.exists():
            source = str(local.get(key, row["repository"]))
            if key in local:
                verify_source(source, row["commit"])
            subprocess.run(["git", "clone", "--no-hardlinks", "--no-checkout", source,
                            str(destination)], check=True, timeout=900)
            # This is a new owned checkout; all existing ones stay untouched.
            subprocess.run(["git", "-C", str(destination), "checkout", "--detach", row["commit"]],
                           check=True, timeout=300)
        receipt = verify_source(destination, row["commit"])
        records[key] = {**receipt.to_dict(), "official_repository": row["repository"]}
        print(key, receipt.commit, receipt.code_sha256, flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"schema": "heterocoord.source_receipts.v1",
                                          "sources": records}, indent=2) + "\n")


if __name__ == "__main__":
    main()
