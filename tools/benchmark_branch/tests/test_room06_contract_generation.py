"""Regression for provenance and renamed pilot scenes in the new projection."""
import hashlib
import json
from pathlib import Path

from tools.benchmark_branch.generate_anchor_contracts import main


def test_room06_and_historical_pilots_keep_independent_sources(tmp_path):
    root = Path(__file__).resolve().parents[3]
    decisions = root / 'benchmark/docs/anchor_drafts/BINDING_DECISION_SHEET_20261024.zh-CN.md'
    extension = root / 'benchmark/docs/anchor_drafts/room_06.draft.zh-CN.md'
    catalog_path = root / 'benchmark/task_catalog.json'
    output = tmp_path / 'contracts.json'
    assert main([
        '--catalog', str(catalog_path), '--decision-sheet', str(decisions),
        '--additional-binding-source', str(extension),
        '--pilot-candidates', str(Path(__file__).parent / 'fixtures/pilot-candidates-20261022.json'),
        '--paper-sha', '0b006d4efcd25d5160d4b0a6e75cddd9e5265e44', '--output', str(output),
    ]) == 0
    result = json.loads(output.read_text())
    assert result['candidate_count'] == 31
    assert result['binding_sources'] == [
        {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (decisions, extension)
    ]
    by_id = {row['anchor_id']: row for row in result['contracts']}
    catalog = json.loads(catalog_path.read_text())
    assert set(by_id) == {row['anchor_id'] for row in catalog['anchors']}
    for row in catalog['anchors']:
        contract = by_id[row['anchor_id']]
        assert contract['identity']['scene_key'] == row['scene_key']
        assert contract['certification']['case_certified'] is False
        assert contract['certification']['paper_score_eligible'] is False
    assert by_id['liv-p4-01']['identity']['scene_key'] == 'pending_reassignment'
    assert by_id['liv-p3-01']['identity']['scene_key'] == 'house'
    assert by_id['liv-t-04']['identity']['scene_key'] == 'room_06'
