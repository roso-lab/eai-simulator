from __future__ import annotations

import hashlib
import json

from tools.assets.check_factory_fixture_admission import inspect_factory_fixture_admission


def _fixture(path, expected):
    path.write_text(json.dumps({"source_hashes": {"usd_sha256": expected}}), encoding="utf-8")


def test_factory_admission_finds_matching_candidate_without_mutating(tmp_path):
    asset = tmp_path / "factory.usd"
    candidate = tmp_path / "cache" / "factory.usd"
    candidate.parent.mkdir()
    candidate.write_bytes(b"frozen")
    expected = hashlib.sha256(candidate.read_bytes()).hexdigest()
    fixture = tmp_path / "fixture.json"
    _fixture(fixture, expected)
    report = inspect_factory_fixture_admission(asset, fixture, [candidate])
    assert report["status"] == "ready"
    assert report["matching_candidates"] == [str(candidate.resolve())]
    assert report["mutated"] is False
    assert not asset.exists()


def test_factory_admission_classifies_existing_wrong_version(tmp_path):
    asset = tmp_path / "factory.usd"
    asset.write_bytes(b"current")
    fixture = tmp_path / "fixture.json"
    _fixture(fixture, "0" * 64)
    report = inspect_factory_fixture_admission(asset, fixture)
    assert report["status"] == "blocked"
    assert report["reason"] == "factory_usd_hash_mismatch"
    assert report["scoreable"] is False


def test_factory_admission_keeps_missing_asset_as_infrastructure_block(tmp_path):
    fixture = tmp_path / "fixture.json"
    _fixture(fixture, "1" * 64)
    report = inspect_factory_fixture_admission(tmp_path / "missing.usd", fixture)
    assert report["status"] == "blocked"
    assert report["reason"] == "factory_usd_missing"
    assert report["actual_sha256"] is None

