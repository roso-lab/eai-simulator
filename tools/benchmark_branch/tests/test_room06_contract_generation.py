"""Regression for provenance and renamed pilot scenes in the new projection."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from benchmark.scorebook_v3 import PAPER_COMMIT
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
        '--paper-sha', PAPER_COMMIT, '--output', str(output),
    ]) == 0
    result = json.loads(output.read_text())
    assert result['candidate_count'] == 31
    assert result['binding_sources'] == [
        {'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (decisions, extension)
    ]
    assert result['source_catalog'] == 'benchmark/task_catalog.json'
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


def _copy_generation_inputs(source: Path, target: Path) -> dict[str, Path]:
    relatives = {
        'catalog': Path('benchmark/task_catalog.json'),
        'decision': Path('benchmark/docs/anchor_drafts/BINDING_DECISION_SHEET_20261024.zh-CN.md'),
        'extension': Path('benchmark/docs/anchor_drafts/room_06.draft.zh-CN.md'),
        'binding': Path('benchmark/docs/progress/PREDICATE_BINDING_20261023.zh-CN.md'),
        'pilots': Path('tools/benchmark_branch/tests/fixtures/pilot-candidates-20261022.json'),
    }
    result = {}
    for name, relative in relatives.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
        result[name] = destination
    return result


def test_generation_is_byte_identical_across_clean_worktree_roots(tmp_path):
    source = Path(__file__).resolve().parents[3]
    roots = [tmp_path / 'clean-a', tmp_path / 'clean-b']
    outputs = []
    for index, root in enumerate(roots):
        paths = _copy_generation_inputs(source, root)
        output = tmp_path / f'output-{index}.json'
        assert main([
            '--project-root', str(root),
            '--catalog', str(paths['catalog']),
            '--decision-sheet', str(paths['decision']),
            '--additional-binding-source', str(paths['extension']),
            '--additional-binding-source', str(paths['binding']),
            '--pilot-candidates', str(paths['pilots']),
            '--paper-sha', PAPER_COMMIT,
            '--output', str(output),
        ]) == 0
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
    assert b'/home/pupil/' not in outputs[0]


def test_generation_rejects_project_input_escape(tmp_path):
    source = Path(__file__).resolve().parents[3]
    root = tmp_path / 'clean'
    paths = _copy_generation_inputs(source, root)
    outside = tmp_path / 'outside.md'
    outside.write_text('not project local', encoding='utf-8')
    with pytest.raises(ValueError, match='inside project root'):
        main([
            '--project-root', str(root),
            '--catalog', str(paths['catalog']),
            '--decision-sheet', str(outside),
            '--paper-sha', PAPER_COMMIT,
            '--output', str(tmp_path / 'unused.json'),
        ])
