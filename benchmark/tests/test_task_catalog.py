from __future__ import annotations

import json

import pytest

from benchmark.task_catalog import CatalogError, catalog_report, load_default_catalog, validate_catalog


def test_default_catalog_is_three_domains_with_liv_t04_extension() -> None:
    catalog = load_default_catalog()
    assert len(catalog) == 31
    baseline = tuple(item for item in catalog if item.anchor_id != "liv-t-04")
    assert len(baseline) == 30
    assert {domain: sum(item.domain == domain for item in baseline) for domain in {item.domain for item in baseline}} == {
        "production": 10,
        "service": 10,
        "living_public": 10,
    }
    extension = next(item for item in catalog if item.anchor_id == "liv-t-04")
    assert extension.scene_key == "room_06"
    assert extension.relation_signature == ("P2", "P3")
    assert {item.difficulty_tier for item in catalog} == {1, 2, 3, 4}
    assert {item.difficulty_certification_status for item in catalog} == {
        "difficulty_certification_pending"
    }


def test_common_anchor_contracts_are_scene_invariant() -> None:
    catalog = load_default_catalog()
    groups: dict[str, set[str]] = {}
    for item in catalog:
        if item.common_anchor_id:
            groups.setdefault(item.common_anchor_id, set()).add(
                json.dumps(item.semantic_contract(), sort_keys=True)
            )
    assert len(groups) == 9
    assert all(len(fingerprints) == 1 for fingerprints in groups.values())


def test_catalog_anchor_projects_to_runtime_contract_without_losing_metadata() -> None:
    anchor = next(item for item in load_default_catalog() if item.anchor_id == "prod-p1-01")
    contract = anchor.to_protocol_contract()
    assert contract.relation_signature == ("P1",)
    assert contract.horizon_ticks == round(anchor.horizon_s / 0.02)
    assert contract.metadata["difficulty_vector"] == anchor.difficulty.to_dict()


def test_table_s2_common_cells_and_p3_subtypes_are_preserved() -> None:
    catalog = load_default_catalog()
    for item in catalog:
        if item.domain == "production" and item.primary_label == "P3":
            assert item.typical_or_common == "typical"
        if item.domain == "service" and item.primary_label == "P4":
            assert item.typical_or_common == "typical"
        if item.domain == "living_public" and item.primary_label == "P1":
            assert item.typical_or_common == "typical"
    assert {item.p3_subtype for item in catalog if item.p3_subtype} == {"P3-J", "P3-S"}


def test_catalog_rejects_primary_label_that_breaks_tie_break() -> None:
    catalog = list(load_default_catalog())
    data = catalog[0].to_dict()
    data["primary_label"] = "P4"
    from benchmark.task_catalog import TaskAnchor

    with pytest.raises(CatalogError):
        validate_catalog([TaskAnchor.from_mapping(data), *catalog[1:]])


def test_catalog_report_keeps_common_certification_separate_from_coverage() -> None:
    report = catalog_report(load_default_catalog())
    assert report["anchor_count"] == 31
    assert len(report["common_anchor_groups"]) == 9
    assert report["common_certification"] == {"planned": 21}
    assert report["cells"]["production/P3"]["common"] == 0
    assert report["difficulty_certification"] == {"difficulty_certification_pending": 31}


def test_common_pair_rejects_difficulty_tier_mismatch() -> None:
    from benchmark.task_catalog import TaskAnchor

    catalog = list(load_default_catalog())
    index = next(index for index, item in enumerate(catalog) if item.anchor_id == "svc-p1-01")
    value = catalog[index].to_dict()
    value["difficulty_vector"]["dependency_depth"] = 1
    catalog[index] = TaskAnchor.from_mapping(value)
    with pytest.raises(CatalogError, match="difficulty tier mismatch"):
        validate_catalog(catalog)


def test_manual_bins_cannot_self_assert_difficulty_certification() -> None:
    from benchmark.task_catalog import TaskAnchor

    value = load_default_catalog()[0].to_dict()
    value["difficulty_certification_status"] = "difficulty_certified"
    with pytest.raises(CatalogError, match="must remain pending"):
        TaskAnchor.from_mapping(value)
