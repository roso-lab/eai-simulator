"""Preserve validation exit status across Isaac Kit application shutdown."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def run_worker_process(module_name: str) -> int:
    """Run the Isaac-owning process and return its recorded validation status."""
    with tempfile.NamedTemporaryFile(
        prefix="eai-go2-status-", suffix=".json", delete=False
    ) as stream:
        result_path = Path(stream.name)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                module_name,
                *sys.argv[1:],
                "--_result-file",
                str(result_path),
            ],
            check=False,
        )
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return completed.returncode or 1
        return 0 if completed.returncode == 0 and payload.get("ok") is True else 1
    finally:
        result_path.unlink(missing_ok=True)


def write_worker_result(path: str, *, ok: bool, failures: list[str]) -> None:
    Path(path).write_text(
        json.dumps({"ok": bool(ok), "failures": list(failures)}, ensure_ascii=True),
        encoding="utf-8",
    )
