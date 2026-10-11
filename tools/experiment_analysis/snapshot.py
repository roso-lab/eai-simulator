"""Bounded byte copies through no-follow directory descriptors (Linux/POSIX)."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

from .schema import AnalysisError, integer, text


@dataclass(frozen=True)
class SnapshotLimits:
    max_files: int = 100000
    max_file_bytes: int = 256 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self):
        for value in (self.max_files, self.max_file_bytes, self.max_total_bytes):
            integer(value, 1)


class Budget:
    def __init__(self, limits: SnapshotLimits):
        self.limits, self.files, self.bytes = limits, 0, 0

    def reserve(self, size: int):
        if size > self.limits.max_file_bytes:
            raise AnalysisError("snapshot file byte limit exceeded")
        if self.files + 1 > self.limits.max_files or self.bytes + size > self.limits.max_total_bytes:
            raise AnalysisError("snapshot total byte/file limit exceeded")
        self.files += 1
        self.bytes += size


def relative_name(name: str) -> str:
    text(name)
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or path.as_posix() != name or name == "."
            or "\\" in name or any(c.isspace() for c in name) or len(path.parts) > 32 or len(name) > 4096):
        raise AnalysisError("unsafe or noncanonical snapshot path")
    return name


@contextmanager
def directory(path: str | Path):
    # Do not resolve symlinks before opening: every path component must be a
    # directory itself, including ancestors of a user-provided root.
    absolute = Path(os.path.abspath(path))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def regular(root_fd: int, name: str):
    parts = relative_name(name).split("/")
    parent = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise AnalysisError("snapshot member is not a regular file")
            yield stream
    finally:
        os.close(parent)


def read_small(root_fd: int, name: str, *, limit: int = 8 * 1024 * 1024) -> bytes:
    with regular(root_fd, name) as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            raise AnalysisError("snapshot metadata byte limit exceeded")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise AnalysisError("snapshot metadata byte limit exceeded")
        return raw


def write_bytes(path: Path, data: bytes, budget: Budget) -> dict:
    budget.reserve(len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def copy_file(root_fd: int, name: str, target: Path, expected: str, budget: Budget) -> dict:
    with regular(root_fd, name) as source:
        before = os.fstat(source.fileno())
        budget.reserve(before.st_size)
        target.parent.mkdir(parents=True, exist_ok=True)
        checksum, size = hashlib.sha256(), 0
        with target.open("xb") as destination:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > before.st_size:
                    raise AnalysisError("snapshot source grew during copy")
                checksum.update(chunk)
                destination.write(chunk)
        after = os.fstat(source.fileno())
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != before.st_size):
            raise AnalysisError("snapshot source changed during copy")
        if checksum.hexdigest() != expected:
            raise AnalysisError(f"snapshot checksum mismatch: {name}")
        return {"sha256": checksum.hexdigest(), "bytes": size}


def inventory(root_fd: int, limits: SnapshotLimits) -> tuple[set[str], set[str]]:
    """Enumerate exact tree shape without following links or special files."""
    files, dirs = set(), set()

    def visit(fd, prefix="", depth=0):
        if depth > 32:
            raise AnalysisError("snapshot directory depth limit exceeded")
        with os.scandir(fd) as iterator:
            for entry in iterator:
                name = relative_name(prefix + entry.name)
                if len(files) + len(dirs) >= limits.max_files * 2 + 32:
                    raise AnalysisError("snapshot tree entry limit exceeded")
                if entry.is_dir(follow_symlinks=False):
                    dirs.add(name)
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        visit(child, name + "/", depth + 1)
                    finally:
                        os.close(child)
                elif entry.is_file(follow_symlinks=False):
                    files.add(name)
                else:
                    raise AnalysisError("snapshot contains a symlink or nonregular member")
    visit(root_fd)
    return files, dirs


def parent_directories(files: set[str]) -> set[str]:
    return {p.as_posix() for name in files for p in PurePosixPath(name).parents if p.as_posix() != "."}


def publish_directory(source: Path, target: Path) -> None:
    """Atomic no-replace publication on the project's supported Linux host."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError("atomic no-replace directory publication requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


@contextmanager
def staged_directory(path: str | Path):
    """Build privately, publish atomically on normal exit, discard on exception.

    The caller validates the payload before leaving this context. A process
    killed without Python cleanup may leave hidden staging, never a partly
    published destination. Output parents must remain under caller control.
    """
    target = Path(path).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError("analysis output already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with directory(target.parent):
        # Validate ancestors without resolving symlinks before staging begins.
        with tempfile.TemporaryDirectory(prefix=".eai-analysis-build-", dir=target.parent) as temp:
            payload = Path(temp) / "payload"
            payload.mkdir()
            yield payload
            publish_directory(payload, target)


def separate_output(path: str | Path, sources: list[str | Path]) -> None:
    """Do not publish analysis inside a read-only collector source tree."""
    target = Path(path).resolve()
    if any(target.is_relative_to(Path(source).resolve()) for source in sources):
        raise AnalysisError("analysis output cannot be inside its collector sources")
