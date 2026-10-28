"""Source identity checks shared by the I/O-only coordination adapters.

These checks establish which code ran, not whether an algorithm is correct or
whether a physical task succeeded. Algorithm implementations live upstream.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import subprocess


class SourceIntegrityError(RuntimeError):
    """The registered upstream checkout differs from its frozen revision."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceReceipt:
    root: str
    commit: str
    tree: str
    code_sha256: str
    code_files: int

    def to_dict(self) -> dict:
        return asdict(self)


def verify_source(root: str | Path, expected_commit: str) -> SourceReceipt:
    """Check HEAD, tracked changes, executable/config blobs and import shadows.

    Reading blobs from HEAD also detects changed code marked assume-unchanged.
    Bytecode and generated output are allowed; untracked Python is rejected.
    """
    root = Path(root).expanduser().resolve(strict=True)

    def git(*args: str) -> bytes:
        result = subprocess.run(["git", "-C", str(root), *args],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise SourceIntegrityError(f"upstream git check failed: {' '.join(args)}")
        return result.stdout

    if git("rev-parse", "--show-toplevel").decode().strip() != str(root):
        raise SourceIntegrityError("source_root must be the upstream repository root")
    commit = git("rev-parse", "HEAD").decode().strip()
    if commit != expected_commit:
        raise SourceIntegrityError(f"upstream revision mismatch: {commit} != {expected_commit}")
    if git("status", "--porcelain", "--untracked-files=no").strip():
        raise SourceIntegrityError("upstream has tracked modifications")
    untracked = git("ls-files", "--others", "-z").split(b"\0")
    shadows = [p.decode() for p in untracked if p and p.endswith((b".py", b".so"))]
    if shadows:
        raise SourceIntegrityError(f"untracked importable upstream files: {shadows[:5]}")
    digest = hashlib.sha256()
    count = 0
    suffixes = {".py", ".yaml", ".yml", ".json", ".toml", ".txt", ".md", ".xml", ".launch"}
    for entry in git("ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0"):
        if not entry:
            continue
        header, raw_name = entry.split(b"\t", 1)
        mode, kind, blob = header.split()
        name = raw_name.decode()
        if kind != b"blob" or Path(name).suffix not in suffixes:
            continue
        path = root / name
        if mode == b"120000":
            # Git stores the symlink text rather than the target bytes.
            import os
            data = os.readlink(path).encode()
        else:
            data = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != blob.decode():
            raise SourceIntegrityError(f"upstream file differs from registered blob: {name}")
        digest.update(raw_name + b"\0" + hashlib.sha256(data).digest())
        count += 1
    return SourceReceipt(str(root), commit, git("rev-parse", "HEAD^{tree}").decode().strip(),
                         digest.hexdigest(), count)
