from copy import deepcopy
import json
import pytest
from benchmark.analysis_adapter import analyze_registered_collection, validate_paper_design
from tools.experiment_analysis.synthetic_batch import prepare, collect
from tools.experiment_analysis.schema import AnalysisError


def policy_design():
    design, _, _ = prepare()
    for c in design['cells']:
        c.update(min_instances=30, min_coverage=.8)
    for e in design['estimands']:
        e.update(min_units=30, min_coverage=.8)
    return design


def test_formal_policy_checks_frozen_k3_and_support_floors():
    design = policy_design()
    policy = dict(repeat_ids=('r0', 'r1', 'r2'), min_units=30, min_coverage=.8)
    validate_paper_design(design, **policy)
    for attack in ('repeat', 'estimand', 'cell'):
        altered = deepcopy(design)
        if attack == 'repeat':
            altered['units'][0]['repeats'][-1] = 'other'
        elif attack == 'estimand':
            altered['estimands'][0]['min_repeats'] = 2
        else:
            altered['cells'][0]['min_instances'] = 1
        with pytest.raises(AnalysisError):
            validate_paper_design(altered, **policy)
    with pytest.raises(AnalysisError):
        validate_paper_design(design, **{**policy, 'min_units': 29})


def test_verified_collector_bridge_and_missing_sources_keep_frozen_support(tmp_path):
    root, design, registration, sources = collect(tmp_path / 'collector')
    manifest = json.loads((root / 'source_manifest.json').read_text())
    result = analyze_registered_collection(design, registration, manifest, base_dir=root,
                                          destination=tmp_path / 'analysis')
    assert result['report']['results'][0]['estimate'] == pytest.approx(5 / 12)
    assert result['inferentially_ready'] is False
    missing = {**manifest, 'sources': manifest['sources'][:1]}
    incomplete = analyze_registered_collection(design, registration, missing, base_dir=root)
    assert incomplete['report']['results'][0]['estimate'] is None
    assert incomplete['audit']['status'] == 'incomplete'
    altered = deepcopy(registration)
    altered['schedule']['retry_policy']['infrastructure_reasons'].append('hidden')
    with pytest.raises(AnalysisError, match='changed'):
        analyze_registered_collection(design, altered, manifest, base_dir=root)
