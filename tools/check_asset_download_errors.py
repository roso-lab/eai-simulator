#!/usr/bin/env python3
"""Lightweight checks for visible asset preflight download failures."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import types
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
for source_root in (REPO_ROOT, REPO_ROOT / "source" / "EAI_assets"):
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

import simulator  # noqa: E402
from EAI_assets import asset_resolver  # noqa: E402


def _expect_system_exit(operation, expected_code: int = 1) -> None:
    try:
        operation()
    except SystemExit as exc:
        if exc.code != expected_code:
            raise AssertionError(f"Expected exit {expected_code}, got {exc.code}") from exc
        return
    raise AssertionError(f"Expected SystemExit({expected_code})")


def _check_network_error_normalization() -> None:
    error = asset_resolver._normalize_hf_download_error(
        "owner/assets",
        ["usd/robot/**"],
        ConnectionRefusedError("connection refused"),
    )
    if not isinstance(error, asset_resolver.AssetDownloadNetworkError):
        raise AssertionError(f"Expected network error, got {type(error).__name__}")
    message = str(error)
    if "Unable to reach Hugging Face" not in message or "Proxy environment variables:" not in message:
        raise AssertionError(f"Network guidance is incomplete: {message}")


def _check_access_error_remains_distinct() -> None:
    class ForbiddenError(RuntimeError):
        status_code = 403

    error = asset_resolver._normalize_hf_download_error(
        "owner/assets",
        ["usd/robot/**"],
        ForbiddenError("HTTP 403 Forbidden"),
    )
    if not isinstance(error, asset_resolver.AssetDownloadAccessError):
        raise AssertionError(f"Expected access error, got {type(error).__name__}")


def _check_structured_parent_report() -> None:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        _expect_system_exit(
            lambda: simulator._handle_preflight_payload(
                {
                    "asset_error": {
                        "kind": "network",
                        "exception_type": "AssetDownloadNetworkError",
                        "message": "Unable to reach Hugging Face: connection refused",
                    }
                }
            )
        )
    output = stderr.getvalue()
    for expected in (
        "Asset preparation failed / 资产准备失败",
        "Failure type / 错误类型: network",
        "connection refused",
    ):
        if expected not in output:
            raise AssertionError(f"Missing terminal diagnostic {expected!r}: {output}")
    if "Traceback" in output:
        raise AssertionError(f"Expected concise output without a traceback: {output}")


def _check_parent_download_exception() -> None:
    def fail_download(_paths):
        raise asset_resolver.AssetDownloadNetworkError("network unavailable")

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        _expect_system_exit(
            lambda: simulator._handle_preflight_payload(
                {
                    "task_name": "network_test",
                    "selection": {},
                    "usd_paths": ["missing.usd"],
                    "controller_paths": [],
                },
                ensure_usd_assets=fail_download,
                ensure_controller_assets=lambda _paths: None,
            )
        )
    if "错误类型: network" not in stderr.getvalue():
        raise AssertionError(f"Network failure was not categorized: {stderr.getvalue()}")

    unexpected = RuntimeError("unexpected programming error")

    def fail_unexpectedly(_paths):
        raise unexpected

    try:
        simulator._handle_preflight_payload(
            {
                "task_name": "unexpected_test",
                "selection": {},
                "usd_paths": ["missing.usd"],
                "controller_paths": [],
            },
            ensure_usd_assets=fail_unexpectedly,
            ensure_controller_assets=lambda _paths: None,
        )
    except RuntimeError as exc:
        if exc is not unexpected:
            raise
    else:
        raise AssertionError("Unexpected non-asset exception was swallowed")


def _check_worker_error_payload() -> None:
    closed = Mock()

    class FakeAppLauncher:
        def __init__(self, _options):
            self.app = SimpleNamespace(close=closed)

    isaaclab = types.ModuleType("isaaclab")
    isaaclab.__path__ = []
    isaaclab_app = types.ModuleType("isaaclab.app")
    isaaclab_app.AppLauncher = FakeAppLauncher
    isaaclab.app = isaaclab_app

    with tempfile.TemporaryDirectory() as temporary_directory:
        output_path = Path(temporary_directory) / "assets.json"
        args = SimpleNamespace(preflight_output=str(output_path), device="cpu", num_envs=1)
        with (
            patch.dict(sys.modules, {"isaaclab": isaaclab, "isaaclab.app": isaaclab_app}),
            patch.object(
                simulator,
                "_resolve_task_request_before_app",
                return_value=simulator.TaskRequest(task_name="network_test"),
            ),
            patch.object(
                simulator,
                "_collect_asset_payload_after_app",
                side_effect=asset_resolver.AssetDownloadNetworkError("network unavailable"),
            ),
        ):
            simulator._run_asset_preflight_worker(args)
        payload = json.loads(output_path.read_text(encoding="utf-8"))

    failure = payload.get("asset_error", {})
    if failure.get("kind") != "network" or "network unavailable" not in failure.get("message", ""):
        raise AssertionError(f"Unexpected worker failure payload: {payload}")
    closed.assert_called_once_with()


def main() -> int:
    _check_network_error_normalization()
    _check_access_error_remains_distinct()
    _check_structured_parent_report()
    _check_parent_download_exception()
    _check_worker_error_payload()
    print("PASS: asset download failures remain visible and return exit status 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
