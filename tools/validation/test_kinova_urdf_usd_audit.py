from kinova_urdf_usd_audit import compare, read_urdf


def test_explicit_zero_mass_source_is_not_missing():
    result = compare(
        {"/gen3/end_effector_link": {"mass_kg": 1e-7, "centerOfMass": None, "diagonalInertia": None}},
        {"end_effector_link": {"mass_kg": 0.0, "centerOfMass": [0, 0, 0],
                                "inertia_kg_m2": {"ixx": 0, "ixy": 0, "ixz": 0, "iyy": 0, "iyz": 0, "izz": 0}}},
    )
    assert result["matched_mass_links"] == 1
    assert result["zero_mass_source_links"] == ["end_effector_link"]


def test_urdf_parser_reads_inertial_fields(tmp_path):
    path = tmp_path / "arm.urdf"
    path.write_text("""<robot name='x'><link name='base_link'><inertial><origin xyz='1 2 3'/><mass value='2'/><inertia ixx='1' ixy='0' ixz='0' iyy='2' iyz='0' izz='3'/></inertial></link></robot>""")
    assert read_urdf(path)["base_link"]["mass_kg"] == 2.0
