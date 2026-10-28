"""Fixed current-package publication, conflicts and interruption recovery."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest

from tools.topology_authoring import map_project as storage
from tools.topology_authoring.map_project import MapProject, read_json, write_json, sha256


@pytest.fixture
def project(tmp_path):
    return MapProject.create_blank(tmp_path / "map", "scene", [-2, -2, 2, 2], .5)


def package_hashes(root):
    return {p.name: sha256(p) for p in (root / "current").iterdir()}


def test_saves_replace_one_directory_and_stale_process_cannot_overwrite(project):
    stale = MapProject(project.root)
    path = project.root / "current"
    old_id = project.manifest["save_id"]
    project.edit("add_node", domain="aerial", position_xyz=[0, 0, 2])
    assert project.save() == path
    project.edit("add_semantic_point", label="Target")
    assert project.save() == path
    assert project.manifest["save_id"] != old_id
    assert MapProject(project.root)._snapshot() == project._snapshot()
    assert sorted(p.name for p in project.root.iterdir()) == [".write.lock", "base", "current", "project.json"]
    committed = package_hashes(project.root)
    with pytest.raises(ValueError, match="another process"):
        stale.save()
    assert package_hashes(project.root) == committed


@pytest.mark.parametrize("after_replace", [False, True])
def test_index_failure_rolls_back_bytes_all_products_and_pending_edits(project, monkeypatch, after_replace):
    original_text = (project.root / "project.json").read_bytes()
    original_package = package_hashes(project.root)
    project.edit("add_node", domain="aerial", position_xyz=[1, 1, 2])
    pending = deepcopy(project._snapshot())
    previous_revision = project.revision
    write = storage.write_json

    def fail_index(path, value):
        if path == project.root / "project.json":
            if after_replace:
                write(path, value)
            raise OSError("index disk failure")
        return write(path, value)

    monkeypatch.setattr(storage, "write_json", fail_index)
    with pytest.raises(OSError, match="index disk failure"):
        project.save()
    assert (project.root / "project.json").read_bytes() == original_text
    assert package_hashes(project.root) == original_package
    assert project._snapshot() == pending and project.dirty
    assert project.revision == previous_revision
    assert not MapProject(project.root).document_for("aerial").nodes
    assert not list(project.root.glob(".pending-save-*"))
    assert not (project.root / ".save-transaction.json").exists()


@pytest.mark.parametrize("commit_index", [False, True])
def test_reopen_recovers_process_interruption_before_or_after_commit(project, monkeypatch, commit_index):
    previous = MapProject(project.root)._snapshot()
    project.edit("add_semantic_point", label="New")
    pending = project._snapshot()
    write = storage.write_json

    def stop_process(path, value):
        if path == project.root / "project.json":
            if commit_index:
                write(path, value)
            raise SystemExit("simulated process interruption")
        return write(path, value)

    monkeypatch.setattr(storage, "write_json", stop_process)
    with pytest.raises(SystemExit):
        project.save()
    reopened = MapProject(project.root)
    assert reopened._snapshot() == (pending if commit_index else previous)
    assert not list(project.root.glob(".pending-save-*"))
    assert not (project.root / ".save-transaction.json").exists()


def test_legacy_revision_migrates_only_after_complete_publication(project, monkeypatch):
    legacy = project.root / "revisions" / "old"
    legacy.parent.mkdir()
    (project.root / "current").rename(legacy)
    manifest = read_json(project.root / "project.json")
    manifest["current_revision"] = "revisions/old"
    write_json(project.root / "project.json", manifest)
    loaded = MapProject(project.root)
    expected = loaded._snapshot()
    export = loaded._export

    def fail_export(*args):
        export(*args)
        raise OSError("export failed")

    monkeypatch.setattr(loaded, "_export", fail_export)
    with pytest.raises(OSError):
        loaded.save()
    assert legacy.exists() and MapProject(project.root)._snapshot() == expected
    monkeypatch.setattr(loaded, "_export", export)
    loaded.save()
    assert loaded.saved_revision == "current"
    assert not legacy.parent.exists()
    assert MapProject(project.root)._snapshot() == expected


def test_interrupted_rollback_resumes_even_after_new_index_was_written(project, monkeypatch):
    previous = project._snapshot()
    original_index = (project.root / "project.json").read_bytes()
    project.edit("add_semantic_point", label="Pending")
    write, write_text = storage.write_json, storage._write_text

    def fail_after_index(path, value):
        write(path, value)
        if path == project.root / "project.json":
            raise OSError("index sync failed")

    def interrupt_index_restoration(path, value):
        if path == project.root / "project.json" and value.encode() == original_index:
            raise SystemExit("interrupted rollback after restoring directory")
        write_text(path, value)

    monkeypatch.setattr(storage, "write_json", fail_after_index)
    monkeypatch.setattr(storage, "_write_text", interrupt_index_restoration)
    with pytest.raises(SystemExit):
        project.save()
    monkeypatch.setattr(storage, "write_json", write)
    monkeypatch.setattr(storage, "_write_text", write_text)
    assert MapProject(project.root)._snapshot() == previous
    assert (project.root / "project.json").read_bytes() == original_index
    assert not (project.root / ".save-transaction.json").exists()


def test_committed_cleanup_warning_is_visible_and_retried(project, monkeypatch):
    remove = storage.shutil.rmtree
    def fail_cleanup(path, *args, **kwargs):
        if path.name.startswith(".pending-save-"):
            raise OSError("cleanup unavailable")
        return remove(path, *args, **kwargs)
    monkeypatch.setattr(storage.shutil, "rmtree", fail_cleanup)
    project.edit("add_semantic_point", label="Saved")
    project.save()
    assert any("cleanup pending" in warning for warning in project.state()["warnings"])
    monkeypatch.setattr(storage.shutil, "rmtree", remove)
    assert MapProject(project.root)._snapshot() == project._snapshot()
    assert not (project.root / ".save-transaction.json").exists()


def test_reader_waits_for_directory_and_index_transaction(project, monkeypatch):
    project.edit("add_node", domain="aerial", position_xyz=[0, 0, 3])
    swapped, release = threading.Event(), threading.Event()
    write = storage.write_json

    def pause_index(path, value):
        if path == project.root / "project.json":
            swapped.set()
            assert release.wait(5)
        return write(path, value)

    monkeypatch.setattr(storage, "write_json", pause_index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(project.save)
        assert swapped.wait(5)
        opening = pool.submit(MapProject, project.root)
        try:
            assert not opening.done()
        finally:
            release.set()
        saving.result(timeout=5)
        reopened = opening.result(timeout=5)
    assert reopened._snapshot() == project._snapshot()


def test_base_or_product_changes_are_rejected_before_publication(project):
    old_index = (project.root / "project.json").read_bytes()
    product = project.root / "current" / "semantic-points.json"
    payload = product.read_bytes()
    product.write_bytes(payload + b"\n")
    with pytest.raises(ValueError, match="outside tool"):
        project.save()
    product.write_bytes(payload)
    base = project.root / project.manifest["base_grids"][0]["file"]
    base.write_bytes(base.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="base geometry"):
        project.save()
    assert (project.root / "project.json").read_bytes() == old_index
