"""Read-only Linux/glibc memory diagnostics and explicit worker allocator profiles."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ElementTree

ENVIRONMENT_KEYS = ("MALLOC_ARENA_MAX", "MALLOC_TRIM_THRESHOLD_", "MALLOC_MMAP_THRESHOLD_",
                    "GLIBC_TUNABLES", "LD_PRELOAD", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS")


def read_kib_fields(path):
    result = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] == "kB":
            result[fields[0].rstrip(":")] = int(fields[1]) * 1024
    return result


def allocator_environment(environment, profile):
    result = dict(environment)
    if profile == "baseline":
        return result
    if profile not in ("arena2", "arena2_trim"):
        raise ValueError(f"unknown memory profile: {profile}")
    if result.get("LD_PRELOAD") or "glibc.malloc." in result.get("GLIBC_TUNABLES", ""):
        raise ValueError("arena2 requires no allocator interposer or conflicting glibc malloc tunables")
    # Keep the low arena count that the profile was introduced for. The
    # optional trim variant also asks glibc to return larger freed native
    # chunks to the kernel. These affect host RSS only; CUDA/PhysX allocations
    # and simulator semantics are unchanged.
    result["MALLOC_ARENA_MAX"] = "2"
    if profile == "arena2_trim":
        result["MALLOC_TRIM_THRESHOLD_"] = "131072"
    return result


def environment_fingerprint(environment=None):
    environment = os.environ if environment is None else environment
    return {name: environment.get(name) for name in ENVIRONMENT_KEYS}


def parse_allocator_info(payload):
    root = ElementTree.fromstring(payload)
    if root.tag != "malloc":
        raise ValueError("unsupported malloc_info XML")
    totals = {element.attrib["type"]: int(element.attrib["size"]) for element in root.findall("total")}
    systems = {element.attrib["type"]: int(element.attrib["size"]) for element in root.findall("system")}
    return {"arena_count": len(root.findall("heap")), "arena_system_bytes": systems["current"],
            "free_chunk_bytes": totals["fast"] + totals["rest"], "mmap_bytes": totals["mmap"],
            "scope": "glibc allocator totals, not resident/reclaimable memory or all native allocators"}


def allocator_info():
    library = ctypes.CDLL("libc.so.6", use_errno=True)
    library.fdopen.argtypes = [ctypes.c_int, ctypes.c_char_p]
    library.fdopen.restype = ctypes.c_void_p
    library.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
    library.malloc_info.restype = ctypes.c_int
    library.fclose.argtypes = [ctypes.c_void_p]
    library.fclose.restype = ctypes.c_int
    with tempfile.TemporaryFile() as temporary:
        descriptor = os.dup(temporary.fileno())
        stream = library.fdopen(descriptor, b"w")
        if not stream:
            os.close(descriptor)
            raise OSError(ctypes.get_errno(), "fdopen failed for allocator diagnostics")
        try:
            result = library.malloc_info(0, stream)
        finally:
            library.fclose(stream)
        if result != 0:
            raise RuntimeError("malloc_info failed")
        temporary.seek(0)
        return parse_allocator_info(temporary.read())


def trim_free_native_pages():
    """Return free glibc arena pages at an explicit initialization boundary."""
    import gc

    before = read_kib_fields(Path("/proc/self/status"))["VmRSS"]
    gc.collect()
    library = ctypes.CDLL("libc.so.6", use_errno=True)
    library.malloc_trim.argtypes = [ctypes.c_size_t]
    library.malloc_trim.restype = ctypes.c_int
    released = bool(library.malloc_trim(0))
    after = read_kib_fields(Path("/proc/self/status"))["VmRSS"]
    return {"rss_before": before, "rss_after": after, "allocator_released_pages": released,
            "scope": "free host allocator pages only; live objects and CUDA allocations retained"}


def mapping_category(path):
    if not path:
        return "anonymous"
    if path == "[heap]":
        return "heap"
    if path.startswith("/dev/"):
        return "device"
    if path.startswith("["):
        return "special"
    if ".so" in Path(path).name:
        return "shared_library"
    return "file"


def parse_smaps(lines):
    fields = {"Rss", "Pss", "Private_Clean", "Private_Dirty", "Shared_Clean", "Shared_Dirty", "Anonymous"}
    groups = {}
    paths = {}
    current = None
    for line in lines:
        values = line.split(maxsplit=5)
        if values and re.fullmatch(r"[0-9a-f]+-[0-9a-f]+", values[0]):
            path = values[5].strip() if len(values) == 6 else ""
            category = mapping_category(path)
            current = (groups.setdefault(category, {name: 0 for name in fields}),
                       paths.setdefault(path or "[anonymous]", {name: 0 for name in fields}))
        elif current is not None:
            key = values[0].rstrip(":") if values else ""
            if key in fields and len(values) == 3 and values[2] == "kB":
                for target in current:
                    target[key] += int(values[1]) * 1024
    return {"categories": groups,
            "largest_mappings": [{"path": path, **counts} for path, counts in
                                 sorted(paths.items(), key=lambda item: item[1]["Rss"], reverse=True)[:20]]}


def memory_snapshot(proc=Path("/proc/self")):
    with (proc / "smaps").open() as stream:
        mappings = parse_smaps(stream)
    return {"pid": os.getpid(), "status": read_kib_fields(proc / "status"),
            "mappings": mappings, "allocator": allocator_info(),
            "environment": environment_fingerprint()}
