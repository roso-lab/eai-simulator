from __future__ import annotations

import socket
import subprocess
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import InterfaceSpec


@dataclass(frozen=True)
class ProbeResult:
    state: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _probe_ros_topic(endpoint: str, *, runner: Callable[..., Any], timeout: float) -> ProbeResult:
    try:
        completed = runner(["ros2", "topic", "list", "-t"], capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return ProbeResult("unknown", "ros2 CLI is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return ProbeResult("unknown", "ros2 topic discovery timed out")
    if completed.returncode != 0:
        return ProbeResult("unknown", completed.stderr.strip() or "ros2 topic discovery failed")
    for line in completed.stdout.splitlines():
        topic, _, raw_type = line.partition(" [")
        if topic.strip() == endpoint:
            return ProbeResult("available", "ROS 2 topic is present", {"data_type": raw_type.rstrip("]")})
    return ProbeResult("unavailable", "ROS 2 topic is not present")


def _completed_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _probe_ros_sample(endpoint: str, *, runner: Callable[..., Any], timeout: float) -> ProbeResult:
    command = ["ros2", "topic", "echo", "--once", endpoint]
    try:
        completed = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return ProbeResult("unknown", "ros2 CLI is not installed or not on PATH")
    except subprocess.TimeoutExpired as exc:
        partial = _completed_output(exc.output).strip()
        return ProbeResult("unavailable", "No message arrived before timeout", {"summary": partial[:2000]})
    if completed.returncode != 0:
        return ProbeResult("unavailable", completed.stderr.strip() or "Could not sample ROS 2 topic")
    lines = []
    for line in completed.stdout.splitlines():
        if line.lstrip().startswith("data:"):
            lines.append("data: <payload omitted>")
        else:
            lines.append(line)
        if len(lines) >= 30:
            break
    return ProbeResult("available", "Received one ROS 2 message", {"summary": "\n".join(lines)[:2000]})


def _probe_ros_frequency(endpoint: str, *, runner: Callable[..., Any], timeout: float) -> ProbeResult:
    command = ["ros2", "topic", "hz", endpoint, "--window", "5"]
    output = ""
    try:
        completed = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = completed.stdout
        if completed.returncode != 0:
            return ProbeResult("unavailable", completed.stderr.strip() or "Could not measure ROS 2 topic frequency")
    except FileNotFoundError:
        return ProbeResult("unknown", "ros2 CLI is not installed or not on PATH")
    except subprocess.TimeoutExpired as exc:
        output = _completed_output(exc.output)
    match = re.search(r"average rate:\s*([0-9]+(?:\.[0-9]+)?)", output)
    if not match:
        return ProbeResult("unavailable", "No frequency sample was collected", {"summary": output.strip()[:2000]})
    hz = float(match.group(1))
    return ProbeResult("available", f"Average rate {hz:g} Hz", {"hz": hz, "summary": output.strip()[:2000]})


def _probe_http(endpoint: str, *, timeout: float) -> ProbeResult:
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as response:
            return ProbeResult("available", f"HTTP {response.status}", {"status": response.status})
    except (urllib.error.URLError, ValueError) as exc:
        return ProbeResult("unavailable", str(exc))


def _probe_tcp(endpoint: str, *, timeout: float) -> ProbeResult:
    host, separator, port_text = endpoint.rpartition(":")
    if not separator:
        return ProbeResult("unknown", "TCP endpoint must use host:port")
    try:
        with socket.create_connection((host, int(port_text)), timeout=timeout):
            return ProbeResult("available", "TCP connection succeeded")
    except (OSError, ValueError) as exc:
        return ProbeResult("unavailable", str(exc))


def probe_interface(
    interface: InterfaceSpec,
    *,
    endpoint: str,
    mode: str = "presence",
    runner: Callable[..., Any] = subprocess.run,
    timeout: float = 3.0,
) -> ProbeResult:
    if not interface.is_read_only:
        return ProbeResult("blocked", "Only read-only interfaces can be tested")
    protocol = interface.protocol.casefold()
    if protocol == "ros2" and interface.kind == "topic":
        if mode == "sample":
            return _probe_ros_sample(endpoint, runner=runner, timeout=timeout)
        if mode == "hz":
            return _probe_ros_frequency(endpoint, runner=runner, timeout=timeout)
        return _probe_ros_topic(endpoint, runner=runner, timeout=timeout)
    if protocol in {"http", "https"}:
        return _probe_http(endpoint, timeout=timeout)
    if protocol == "tcp":
        return _probe_tcp(endpoint, timeout=timeout)
    return ProbeResult("unknown", f"No read-only probe is available for {interface.protocol}/{interface.kind}")
