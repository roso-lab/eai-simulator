from contextlib import contextmanager
import hashlib

import pytest

from tools.experiment_analysis.schema import AnalysisError
from tools.experiment_analysis.snapshot import Budget, SnapshotLimits, copy_file, directory


def test_streaming_copy_detects_in_place_source_growth(tmp_path, monkeypatch):
    import tools.experiment_analysis.snapshot as module
    root = tmp_path / "source"
    root.mkdir()
    raw = b"a" * (2 * 1024 * 1024)
    path = root / "payload"
    path.write_bytes(raw)
    real_regular = module.regular

    @contextmanager
    def changed_regular(fd, name):
        with real_regular(fd, name) as stream:
            class Reader:
                changed = False
                def fileno(self):
                    return stream.fileno()
                def read(self, size):
                    chunk = stream.read(size)
                    if not self.changed:
                        self.changed = True
                        with path.open("ab") as writer:
                            writer.write(b"growth")
                    return chunk
            yield Reader()

    monkeypatch.setattr(module, "regular", changed_regular)
    with directory(root) as fd, pytest.raises(AnalysisError, match="grew during copy"):
        copy_file(fd, "payload", tmp_path / "copied", hashlib.sha256(raw).hexdigest(), Budget(SnapshotLimits()))
