"""Focused tests for dashboard replay-branch command handling."""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from demo.fire_rescue.dashboard import server


@pytest.fixture()
def dashboard_url():
    server.reset_branch_commands()
    httpd = HTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.reset_branch_commands()


def post(url: str, payload):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_assignment_edit_is_queued_and_visible(dashboard_url):
    status, body = post(
        f"{dashboard_url}/api/replay/assignment",
        {"assignments": {"m20_1": {"subtask": "red"}}},
    )

    assert status == 200
    assert body == {"ok": True, "accepted": 1}
    assert server.get_pending_branch_assignment_edits()[0]["assignments"] == {
        "m20_1": {"subtask": "red"}
    }
    assert server.get_pending_branch_assignment_edits() == []
    with server._state_lock:
        assert server._state["replay"]["phase"] == "assignment_edit_pending"
        assert server._state["replay"]["assignments"]["m20_1"]["subtask"] == "red"


def test_start_branch_accepts_optional_checkpoint(dashboard_url):
    status, body = post(
        f"{dashboard_url}/api/replay/start",
        {"checkpoint_id": "assignment-0001"},
    )

    assert status == 200
    assert body == {"ok": True, "checkpoint_id": "assignment-0001"}
    assert server.get_pending_branch_starts()[0]["checkpoint_id"] == "assignment-0001"
    with server._state_lock:
        assert server._state["replay"]["phase"] == "branch_start_pending"


def test_start_branch_allows_empty_object_without_checkpoint(dashboard_url):
    status, body = post(f"{dashboard_url}/api/replay/start", {})

    assert status == 200
    assert body == {"ok": True}
    assert server.get_pending_branch_starts()[0]["checkpoint_id"] is None


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("assignment", {}),
        ("assignment", {"assignments": []}),
        ("assignment", {"assignments": {}}),
        ("start", {"checkpoint_id": 3}),
        ("start", {"checkpoint_id": ""}),
    ],
)
def test_branch_commands_reject_invalid_payloads(dashboard_url, path, payload):
    status, body = post(f"{dashboard_url}/api/replay/{path}", payload)

    assert status == 400
    assert body["ok"] is False
    assert body["error"]
    assert server.get_pending_branch_assignment_edits() == []
    assert server.get_pending_branch_starts() == []


def test_reset_branch_commands_clears_queues_and_state(dashboard_url):
    post(
        f"{dashboard_url}/api/replay/assignment",
        {"assignments": {"scout_1": {"subtask": "blue"}}},
    )
    post(f"{dashboard_url}/api/replay/start", {"checkpoint_id": "cp-1"})

    server.reset_branch_commands()

    assert server.get_pending_branch_assignment_edits() == []
    assert server.get_pending_branch_starts() == []
    with server._state_lock:
        assert server._state["replay"]["phase"] == "idle"
        assert server._state["replay"]["assignments"] == {}


def test_legacy_reassign_endpoint_still_works(dashboard_url):
    status, body = post(
        f"{dashboard_url}/api/emos/reassign",
        {"assignments": {"m20_1": {"subtask": "yellow"}}},
    )

    assert status == 200
    assert body == {"ok": True}
    assert server.get_pending_reassignments()[0]["assignments"]["m20_1"]["subtask"] == "yellow"
