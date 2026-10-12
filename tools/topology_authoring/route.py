"""List landmarks or export an XYZ route without starting Isaac Sim."""
from __future__ import annotations
import argparse
import json
import os
import tempfile
from pathlib import Path

from algorithm.skills.navigation.landmarks import landmark_inventory, resolve_landmark_route
from tools.topology_authoring.product import load_product


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--list-landmarks", action="store_true")
    parser.add_argument("--origin")
    parser.add_argument("--destination")
    parser.add_argument("--origin-level")
    parser.add_argument("--destination-level")
    parser.add_argument("--capability", action="append", default=None,
                        help="Repeat for actual controller capabilities (default: walk)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.list_landmarks and (not args.origin or not args.destination):
        parser.error("route export requires --origin and --destination")
    if args.output and (args.output.resolve() == args.topology.resolve() or
                       (args.output.exists() and args.topology.exists()
                        and os.path.samefile(args.output,args.topology))):
        parser.error("route output must not overwrite the topology input")
    try:
        document = load_product(args.topology)
        if args.list_landmarks:
            result = {"landmarks": landmark_inventory(document)}
        else:
            result = resolve_landmark_route(document, origin_ref=args.origin,
                destination_ref=args.destination, origin_level_id=args.origin_level,
                destination_level_id=args.destination_level,
                controller_capabilities=args.capability or ("walk",))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=args.output.parent,
                                             prefix=f".{args.output.name}.",delete=False) as stream:
                temporary=Path(stream.name)
                stream.write(payload)
            os.replace(temporary,args.output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(payload, end="")
    return 0 if result.get("admitted", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
