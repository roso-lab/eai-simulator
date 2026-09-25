from demo.benchmark.goal_condition_branch_plan import (
    BRANCHES_PER_BATCH,
    build_condition_inventory,
    condition_branch_plan_summary,
    plan_contrast_batches,
)

SCENES = ["factory", "warehouse_nvd", "modern_house_nvd", "city",
          "restaurant"]


def test_inventory_covers_405_conditions_and_315_pairs():
    inventory = build_condition_inventory(SCENES)
    assert inventory["condition_count"] == 405
    assert inventory["pair_count"] == 315
    assert len(inventory["task_variants"]) == 9  # base + 8 mechanisms
    baselines = {row["condition_id"] for row in inventory["conditions"]
                 if row["is_baseline"]}
    assert len(baselines) == 5 * 9 * 2  # one baseline per factor per (scene, variant)


def test_plan_batches_colocate_pairs_and_cover_every_condition():
    plan = plan_contrast_batches(SCENES, bases_per_pair=2)
    assert plan["batch_count"] == 5 * 9 * 2
    assert plan["condition_count"] == 405
    assert plan["pair_count"] == 315
    for batch in plan["batches"]:
        assert batch["branch_count"] == 9
        assert batch["branch_count"] <= BRANCHES_PER_BATCH
        # Every tier of this (scene, variant) sits on one base instance.
        tiers = {branch["condition_id"].split("/", 2)[2]
                 for branch in batch["branches"]}
        assert len(tiers) == 9
    # Pair co-location: treatment and baseline share every base.
    sample = plan["batches"][0]
    arms = {branch["condition_id"]: branch for branch in sample["branches"]}
    treatment = "factory/base/communication_quality/delayed"
    baseline = "factory/base/communication_quality/immediate"
    assert arms[treatment]["seed"] != arms[baseline]["seed"]
    assert treatment.rsplit("/", 1)[0] == baseline.rsplit("/", 1)[0]


def test_branch_seeds_are_unique_and_stable():
    first = plan_contrast_batches(SCENES[:2], bases_per_pair=1, base_seed=7)
    second = plan_contrast_batches(SCENES[:2], bases_per_pair=1, base_seed=7)
    seeds_a = [b["seed"] for batch in first["batches"] for b in batch["branches"]]
    seeds_b = [b["seed"] for batch in second["batches"] for b in batch["branches"]]
    assert seeds_a == seeds_b
    assert len(set(seeds_a)) == len(seeds_a)
    third = plan_contrast_batches(SCENES[:2], bases_per_pair=1, base_seed=8)
    seeds_c = [b["seed"] for batch in third["batches"] for b in batch["branches"]]
    assert seeds_a != seeds_c


def test_independent_unit_is_base_not_branch():
    plan = plan_contrast_batches(SCENES[:1], bases_per_pair=3)
    policy = plan["statistics_policy"]
    assert policy["independent_unit"] == "base_instance"
    assert policy["within_base"] == "paired_contrast_only"
    assert policy["branch_count_is_not_sample_count"] is True
    # Each condition replicated on exactly bases_per_pair distinct batches.
    counts: dict[str, set[str]] = {}
    for batch in plan["batches"]:
        for branch in batch["branches"]:
            counts.setdefault(branch["condition_id"], set()).add(
                batch["batch_id"])
    assert all(len(bases) == 3 for bases in counts.values())


def test_unfrozen_parameters_are_explicit():
    plan = plan_contrast_batches(SCENES[:1], bases_per_pair=1)
    unfrozen = plan["unfrozen_parameters"]
    assert "bases_per_pair" in unfrozen
    assert unfrozen["bases_per_pair"]["note"] == (
        "same-task-times-10 principle; not frozen")
    assert "spare_slot_policy" in unfrozen
    assert plan["scoreable"] is False


def test_invalid_inputs_fail_closed():
    for bad in ([], ["city", "city"], [""]):
        try:
            build_condition_inventory(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid scenes: {bad!r}")
    try:
        plan_contrast_batches(SCENES[:1], bases_per_pair=0)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted bases_per_pair=0")


def test_summary_omits_branch_rows_and_keeps_flags_false():
    plan = plan_contrast_batches(SCENES, bases_per_pair=1)
    summary = condition_branch_plan_summary(plan)
    assert "batches" not in summary
    assert summary["condition_count"] == 405
    assert summary["execution_validated"] is False
    assert summary["scoreable"] is False
