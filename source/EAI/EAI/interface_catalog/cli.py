from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .loader import Catalog, CatalogError, load_catalog
from .models import CatalogEntry, InterfaceSpec
from .probes import probe_interface
from .query import query_interfaces, resolve_scene_interfaces
from .snapshot import read_snapshot, snapshot_age_seconds


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search robot and sensor communication interfaces.")
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root(), help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List all declared interfaces.")
    list_parser.add_argument("--json", action="store_true")

    search = subparsers.add_parser("search", help="Filter the interface catalog.")
    search.add_argument("--robot")
    search.add_argument("--sensor")
    search.add_argument("--protocol")
    search.add_argument("--data-type")
    search.add_argument("--text")
    search.add_argument("--json", action="store_true")

    show = subparsers.add_parser("show", help="Show one interface in detail.")
    show.add_argument("interface_id")
    show.add_argument("--json", action="store_true")

    scene = subparsers.add_parser("scene", help="Resolve interfaces for a saved simulator environment.")
    scene.add_argument("--env", required=True)
    scene.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="Show the current simulator runtime snapshot.")
    status.add_argument("--snapshot", type=Path)
    status.add_argument("--probe", action="store_true", help="Probe read-only runtime interfaces before display.")
    status.add_argument("--json", action="store_true")

    test = subparsers.add_parser("test", help="Run a safe read-only interface probe.")
    test.add_argument("interface_id")
    test.add_argument("--endpoint")
    test.add_argument("--snapshot", type=Path)
    test.add_argument("--mode", choices=("presence", "sample", "hz"), default="presence")
    test.add_argument("--json", action="store_true")

    subparsers.add_parser("menu", help="Open an interactive catalog menu.")
    return parser


def _catalog_payload(entries: list[CatalogEntry]) -> list[dict[str, Any]]:
    return [
        {
            "id": entry.interface.id,
            "name": entry.interface.name,
            "device_id": entry.device.id,
            "device": entry.device.name,
            "category": entry.device.category,
            "models": list(entry.device.models),
            "protocol": entry.interface.protocol,
            "direction": entry.interface.direction,
            "kind": entry.interface.kind,
            "endpoint": entry.interface.endpoint,
            "data_type": entry.interface.data_type,
            "description": entry.interface.description,
            "example": entry.interface.example,
        }
        for entry in entries
    ]


def _print_table(rows: list[dict[str, Any]], columns: tuple[tuple[str, str], ...]) -> None:
    if not rows:
        print("No interfaces matched.")
        return
    widths = {
        key: min(60, max(len(title), *(len(str(row.get(key, ""))) for row in rows)))
        for key, title in columns
    }
    print("  ".join(title.ljust(widths[key]) for key, title in columns))
    print("  ".join("-" * widths[key] for key, _title in columns))
    for row in rows:
        print("  ".join(str(row.get(key, ""))[: widths[key]].ljust(widths[key]) for key, _title in columns))


def _print_catalog(entries: list[CatalogEntry], *, json_output: bool) -> None:
    payload = _catalog_payload(entries)
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    _print_table(
        payload,
        (("id", "ID"), ("category", "TYPE"), ("protocol", "PROTOCOL"), ("direction", "DIRECTION"), ("endpoint", "ENDPOINT")),
    )


def _load_selection(repo_root: Path, env_name: str) -> dict[str, Any]:
    path = repo_root / "source" / "EAI_hmrs" / "EAI_hmrs" / "envs" / f"{env_name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Saved environment not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Saved environment must contain an object: {path}")
    return payload


def _snapshot_path(repo_root: Path, value: Path | None) -> Path:
    return value or repo_root / "tmp" / "runtime_interfaces.json"


def _find_runtime_endpoint(snapshot: dict[str, Any], interface_id: str) -> str | None:
    for item in snapshot.get("interfaces", []):
        if item.get("id") == interface_id:
            return str(item.get("endpoint"))
    return None


def _probe_snapshot_interfaces(catalog: Catalog, snapshot: dict[str, Any]) -> dict[str, Any]:
    updated = dict(snapshot)
    runtime_interfaces = []
    for item in snapshot.get("interfaces", []):
        runtime_item = dict(item)
        try:
            interface = catalog.interface(str(item.get("id")))
        except KeyError:
            runtime_item["state"] = "unknown"
            runtime_item["probe_message"] = "Interface is not present in the static catalog"
        else:
            if interface.is_read_only:
                result = probe_interface(interface, endpoint=str(item.get("endpoint", interface.endpoint)), mode="presence")
                runtime_item["state"] = result.state
                runtime_item["probe_message"] = result.message
                runtime_item["probe_details"] = result.details
            else:
                runtime_item["probe_message"] = "Write interface was not probed"
        runtime_interfaces.append(runtime_item)
    updated["interfaces"] = runtime_interfaces
    return updated


def _run_menu(repo_root: Path) -> int:
    while True:
        print("\nInterface Catalog")
        print("1. List all interfaces")
        print("2. Search")
        print("3. Runtime status")
        print("0. Exit")
        try:
            choice = input("Select: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "0":
            return 0
        if choice == "1":
            main(["--repo-root", str(repo_root), "list"])
        elif choice == "2":
            text = input("Robot, sensor, type, or keyword: ").strip()
            main(["--repo-root", str(repo_root), "search", "--text", text])
        elif choice == "3":
            main(["--repo-root", str(repo_root), "status"])
        else:
            print("Unknown selection.")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.command == "menu":
        return _run_menu(repo_root)
    try:
        catalog = load_catalog()
        if args.command == "list":
            _print_catalog(query_interfaces(catalog), json_output=args.json)
            return 0
        if args.command == "search":
            entries = query_interfaces(
                catalog,
                robot=args.robot,
                sensor=args.sensor,
                protocol=args.protocol,
                data_type=args.data_type,
                text=args.text,
            )
            _print_catalog(entries, json_output=args.json)
            return 0 if entries else 1
        if args.command == "show":
            interface = catalog.interface(args.interface_id)
            device = catalog.device_for_interface(args.interface_id)
            entry = CatalogEntry(device=device, interface=interface)
            _print_catalog([entry], json_output=args.json)
            if not args.json:
                print(f"\nDescription: {interface.description or '-'}")
                print(f"Example: {interface.example or '-'}")
            return 0
        if args.command == "scene":
            selection = _load_selection(repo_root, args.env)
            resolved = resolve_scene_interfaces(catalog, selection, env_name=args.env)
            payload = [entry.to_dict() for entry in resolved]
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                _print_table(payload, (("instance_name", "INSTANCE"), ("id", "ID"), ("protocol", "PROTOCOL"), ("endpoint", "ENDPOINT")))
            return 0
        if args.command == "status":
            path = _snapshot_path(repo_root, args.snapshot)
            if not path.is_file():
                print(f"No runtime snapshot found at {path}. Start simulator.py first.", file=sys.stderr)
                return 1
            snapshot = read_snapshot(path)
            payload = _probe_snapshot_interfaces(catalog, snapshot) if args.probe else dict(snapshot)
            payload["age_seconds"] = round(snapshot_age_seconds(snapshot), 3)
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(
                    f"Environment: {snapshot.get('env_name')}  PID: {snapshot.get('pid')}  "
                    f"Age: {payload['age_seconds']:.1f}s"
                )
                _print_table(
                    list(snapshot.get("interfaces", [])),
                    (("instance_name", "INSTANCE"), ("id", "ID"), ("state", "STATE"), ("endpoint", "ENDPOINT")),
                )
            return 0
        if args.command == "test":
            interface = catalog.interface(args.interface_id)
            endpoint = args.endpoint
            if endpoint is None:
                path = _snapshot_path(repo_root, args.snapshot)
                if path.is_file():
                    endpoint = _find_runtime_endpoint(read_snapshot(path), args.interface_id)
            endpoint = endpoint or interface.endpoint
            result = probe_interface(interface, endpoint=endpoint, mode=args.mode)
            payload = {"id": interface.id, "endpoint": endpoint, "state": result.state, "message": result.message, **result.details}
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(f"{result.state.upper()}: {interface.id} {endpoint} - {result.message}")
            return 0 if result.state == "available" else 1
    except (CatalogError, FileNotFoundError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
