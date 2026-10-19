from EAI.hmrs_env.env_diy import catalog


def test_resume_controller_defaults_and_formal_choices_are_fixed() -> None:
    assert catalog.default_controller_cfg("g1") == "G1_DWAQ_CFG"
    assert catalog.controller_cfgs_for_robot("g1") == ("G1_DWAQ_CFG",)

    assert catalog.default_controller_cfg("pepper") == "PEPPER_PICK_PLACE_CFG"
    assert catalog.controller_cfgs_for_robot("pepper") == ("PEPPER_PICK_PLACE_CFG",)

    assert catalog.default_controller_cfg("go2") == "GO2_MIXED_STAIRS_CFG"
    assert catalog.controller_cfgs_for_robot("go2") == (
        "GO2_MIXED_STAIRS_CFG",
        "GO2_EXTREME_PARKOUR_TEACHER_CFG",
    )

    assert catalog.default_controller_cfg("b2") == "B2_MIXED_STAIRS_CFG"


def test_legacy_g1_go2_and_pepper_variants_are_not_formal_choices() -> None:
    registered = set(catalog.controller_cfg_names())
    assert {
        "G1_SKRL_CFG",
        "G1_BOX_GRASP_CFG",
        "G1_BOX_TRANSPORT_CFG",
        "GO2_VELOCITY_RSL_CFG",
        "PEPPER_HOLONOMIC_CFG",
        "PEPPER_BOX_GRASP_CFG",
    }.isdisjoint(registered)


def test_mounted_ur5_variants_are_excluded_from_every_robot() -> None:
    assert catalog.attachment_entry("ur5").supported_robots == ()
    assert catalog.attachment_entry("ur5_bare").supported_robots == ()
    for robot_type in catalog.ROBOT_KEYS:
        assert not catalog.attachment_supported(robot_type, "ur5")
        assert not catalog.attachment_supported(robot_type, "ur5_bare")
        for attachment_type in ("ur5", "ur5_bare"):
            try:
                catalog.validate_attachment_types(robot_type, (attachment_type,))
            except ValueError as exc:
                assert "is not supported" in str(exc)
            else:
                raise AssertionError(
                    f"{robot_type} unexpectedly accepted mounted {attachment_type}"
                )
