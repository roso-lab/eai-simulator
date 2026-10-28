from pathlib import Path
import subprocess

import pytest

from algorithm.system.coordination.native_runtime import SourceIntegrityError, verify_source


def upstream(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    git("init", "-q")
    (root / "policy.py").write_text("def act(): return 7\n")
    git("add", "policy.py")
    git("-c", "user.name=Adapter Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "upstream")
    return root, git("rev-parse", "HEAD"), git


def test_frozen_source_and_ignored_bytecode(tmp_path):
    root, commit, _ = upstream(tmp_path)
    a = verify_source(root, commit)
    (root / "__pycache__").mkdir()
    (root / "__pycache__/policy.pyc").write_bytes(b"bytecode")
    b = verify_source(root, commit)
    assert a == b
    assert a.code_files == 1


def test_source_rejects_wrong_revision_and_mutation(tmp_path):
    root, commit, _ = upstream(tmp_path)
    with pytest.raises(SourceIntegrityError, match="revision mismatch"):
        verify_source(root, "0" * 40)
    (root / "policy.py").write_text("def act(): return 42\n")
    with pytest.raises(SourceIntegrityError, match="modifications"):
        verify_source(root, commit)


def test_assume_unchanged_cannot_hide_policy_edits(tmp_path):
    root, commit, git = upstream(tmp_path)
    git("update-index", "--assume-unchanged", "policy.py")
    (root / "policy.py").write_text("def act(): return 42\n")
    with pytest.raises(SourceIntegrityError, match="differs from registered blob"):
        verify_source(root, commit)


def test_untracked_import_shadow_rejected_even_if_ignored(tmp_path):
    root, commit, _ = upstream(tmp_path)
    (root / ".git/info/exclude").write_text("shadow.py\n")
    (root / "shadow.py").write_text("raise RuntimeError('wrong implementation')\n")
    with pytest.raises(SourceIntegrityError, match="untracked importable"):
        verify_source(root, commit)
