"""CLI for ``python -m tools.benchmark_branch``."""

from __future__ import annotations

import argparse
import ast
import importlib
import json
from pathlib import Path
from typing import Any, Mapping

from .adapters import DummyAdapter
from .executor import BranchExecutor
from .models import BRANCH_ROLES, BranchPlan
from .store import BranchRunStore, LEDGERS
from .validation import summarize_run, validate_run


def load_config(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
            value = yaml.safe_load(text)
        except ImportError:
            value = {}
            current_key = None
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"): continue
                if stripped.startswith("-") and current_key:
                    value.setdefault(current_key, []).append(stripped[1:].strip().strip("'\"")); continue
                if ":" not in stripped: raise ValueError("unsupported YAML; use JSON or install PyYAML")
                key, raw = stripped.split(":", 1); key, raw = key.strip(), raw.strip()
                if not raw: value[key] = []; current_key = key; continue
                try: parsed = ast.literal_eval(raw)
                except (ValueError, SyntaxError):
                    lowered = raw.lower(); parsed = True if lowered == "true" else False if lowered == "false" else raw.strip("'\"")
                value[key] = parsed; current_key = None
    if not isinstance(value, dict): raise ValueError("config must be an object")
    return value


def make_plan(config: dict[str, Any], args: argparse.Namespace | None = None) -> BranchPlan:
    mapping = dict(config)
    if args is not None and args.slots is not None: mapping["slots"] = args.slots
    if args is not None and args.arms: mapping["arms"] = list(args.arms)
    return BranchPlan.from_mapping(mapping)


def load_adapter(config: Mapping[str, Any] | None) -> Any:
    config = dict(config or {})
    spec = config.get("adapter")
    kwargs = dict(config.get("adapter_kwargs", {}))
    if not spec or spec in {"dummy", "DummyAdapter", "tools.benchmark_branch.adapters:DummyAdapter"}:
        return DummyAdapter(**kwargs)
    text = str(spec)
    module_name, attr = text.split(":", 1) if ":" in text else text.rsplit(".", 1)
    value = getattr(importlib.import_module(module_name), attr)
    return value(**kwargs) if isinstance(value, type) else value


def export_run(run_dir: str | Path, output: str | Path | None = None, fmt: str = "jsonl") -> dict[str, Any]:
    root = Path(run_dir).resolve()
    target = Path(output) if output else root / ("export.parquet" if fmt == "parquet" else "export.jsonl")
    records = []
    for name in LEDGERS:
        path = root / f"{name}.jsonl"
        if not path.exists(): continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip(): records.append({"ledger": name, **json.loads(line)})
    records.sort(key=lambda row: (str(row.get("branch_id", "")), int(row.get("event_seq", -1)), int(row.get("simulator_step", -1)), str(row["ledger"])))
    if fmt == "jsonl":
        target.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records), encoding="utf-8")
    else:
        try:
            import pyarrow as pa  # type: ignore
            import pyarrow.parquet as pq  # type: ignore
            pq.write_table(pa.Table.from_pylist(records), target)
        except ImportError as exc:
            raise SystemExit("Parquet export requires pyarrow; use --format jsonl") from exc
    return {"output": str(target.resolve()), "records": len(records), "format": fmt}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.benchmark_branch")
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan"); plan_parser.add_argument("--config", required=True); plan_parser.add_argument("--output")
    run_parser = sub.add_parser("run"); run_parser.add_argument("--config", required=True); run_parser.add_argument("--slots", type=int); run_parser.add_argument("--arms", nargs="+", choices=BRANCH_ROLES); run_parser.add_argument("--max-workers", type=int, default=32); run_parser.add_argument("--execution-mode", default="isolated_parallel"); run_parser.add_argument("--execution-scope", choices=("measured", "protocol_synthetic", "pending", "failed")); run_parser.add_argument("--timeout", type=float); run_parser.add_argument("--output", required=True); run_parser.add_argument("--queue-size", type=int); run_parser.add_argument("--resume", action="store_true")
    resume_parser = sub.add_parser("resume"); resume_parser.add_argument("--run-dir", required=True); resume_parser.add_argument("--max-workers", type=int)
    for command in ("validate", "summarize"): sub.add_parser(command).add_argument("--run-dir", required=True)
    export_parser = sub.add_parser("export"); export_parser.add_argument("--run-dir", required=True); export_parser.add_argument("--format", choices=("jsonl", "parquet"), default="jsonl"); export_parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.command == "plan":
        plan = make_plan(load_config(args.config)); result = plan.row(); text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output: Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(text); return 0
    if args.command == "run":
        config = load_config(args.config); plan = make_plan(config, args)
        if args.resume:
            store = BranchRunStore.resume(args.output)
            adapter = load_adapter({"adapter": store.manifest.get("adapter", config.get("adapter", "dummy")),
                                    "adapter_kwargs": store.manifest.get("adapter_kwargs", config.get("adapter_kwargs", {}))})
            result = BranchExecutor(store.plan, adapter, store, max_workers=args.max_workers).run()
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)); return 0 if result.get("failed", 0) == 0 else 1
        scope = args.execution_scope or str(config.get("execution_scope", plan.execution_scope))
        if scope != plan.execution_scope: plan = BranchPlan.from_mapping({**plan.row(), "execution_scope": scope})
        store = BranchRunStore.create(args.output, plan, requested_max_workers=args.max_workers,
                                      execution_mode=args.execution_mode, execution_scope=scope,
                                      resource_budget=config.get("resource_budget"), adapter=str(config.get("adapter", "dummy")),
                                      adapter_kwargs=config.get("adapter_kwargs", {}))
        if config.get("shared_session_envs") is not None:
            store.update_manifest(shared_session_envs=int(config["shared_session_envs"]))
        result = BranchExecutor(plan, load_adapter(config), store, max_workers=args.max_workers,
                                queue_size=args.queue_size, timeout_s=args.timeout,
                                resource_limit=config.get("resource_limit")).run()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)); return 0 if result.get("failed", 0) == 0 else 1
    if args.command == "resume":
        store = BranchRunStore.resume(args.run_dir); adapter = load_adapter({"adapter": store.manifest.get("adapter", "dummy"), "adapter_kwargs": store.manifest.get("adapter_kwargs", {})})
        result = BranchExecutor(store.plan, adapter, store, max_workers=args.max_workers or int(store.manifest.get("admitted_max_workers") or 1)).run()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)); return 0
    if args.command == "validate":
        result = validate_run(args.run_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)); return 0 if result.get("status") == "passed" else 1
    if args.command == "summarize": print(json.dumps(summarize_run(args.run_dir), ensure_ascii=False, indent=2, sort_keys=True)); return 0
    print(json.dumps(export_run(args.run_dir, args.output, args.format), ensure_ascii=False, indent=2, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
