"""Check the full-body clearance gate, including non-TCP penetrations."""
import numpy as np
import pytest

from tools.validation.pick_place_clearance import box_table_gaps, MeshHull


def gap(centre, half=(.05,.05,.05), rotation=None):
    return box_table_gaps(np.array([centre]), np.array([np.eye(3) if rotation is None else rotation]),
        np.array([half]), np.array([.5,0,.5]), np.array([.35,.35,.025]))[0]


def test_arm_above_table_and_outside_footprint_are_separated():
    assert gap((.5,0,.6)) == pytest.approx(.025)
    assert gap((0,0,.5)) == pytest.approx(.10)


def test_box_crossing_table_is_rejected_without_vertices_inside():
    # A thin long link spans the whole slab: vertex-only tests miss this.
    assert gap((.5,0,.5), half=(.03,.03,.2)) < 0
    assert gap((.5,0,.48)) < 0


def test_rotated_link_can_penetrate_with_its_origin_above_table():
    c = 2**-.5
    rotation = np.array([[c,0,c],[0,1,0],[-c,0,c]])
    assert gap((.5,0,.62), half=(.2,.02,.02), rotation=rotation) < 0
    assert gap((.5,0,.72), half=(.2,.02,.02), rotation=rotation) > 0


def test_boundary_contact_is_not_misreported_as_deep_overlap():
    assert gap((.5,0,.575)) == pytest.approx(0, abs=1e-12)


def test_convex_hull_rejects_only_real_overlap_not_empty_box_corners():
    # Octahedron's enclosing box overlaps the small cube at its empty corner.
    vertices = np.concatenate((np.eye(3), -np.eye(3)))
    hull = MeshHull(vertices)
    assert hull.gap(np.zeros(3), np.eye(3), np.array([.8, .8, 0]), np.full(3, .1)) > 0
    # A box contained inside the mesh still counts as overlap.
    assert hull.gap(np.zeros(3), np.eye(3), np.zeros(3), np.full(3, .1)) < 0
    assert hull.gap(np.zeros(3), np.eye(3), np.array([.8, 0, 0]), np.full(3, .1)) < 0


def test_hull_box_collision_is_invariant_under_rigid_rotation():
    vertices = np.array([[x, y, z] for x in (-.2, .2) for y in (-.02, .02) for z in (-.02, .02)])
    hull = MeshHull(vertices)
    c = 2 ** -.5
    rotation = np.array([[c, 0, c], [0, 1, 0], [-c, 0, c]])
    table_centre, table_half = np.array([.5, 0, .5]), np.array([.35, .35, .025])
    for centre in (np.array([.5, 0, .62]), np.array([.5, 0, .72])):
        assert hull.gap(centre, rotation, table_centre, table_half) == pytest.approx(
            gap(centre, half=(.2, .02, .02), rotation=rotation), abs=1e-12)


def test_hull_matches_oriented_box_separation_for_oblique_edge_cases():
    # Arbitrary 3D rotations exercise edge cross products as well as normals.
    rng = np.random.default_rng(20261020)
    half = np.array([.3, .07, .04])
    vertices = np.array([[x, y, z] for x in (-half[0], half[0])
                         for y in (-half[1], half[1]) for z in (-half[2], half[2])])
    hull = MeshHull(vertices)
    for _ in range(128):
        rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        centre = rng.uniform([.1, -.5, .3], [.9, .5, .8])
        expected = gap(centre, half=half, rotation=rotation)
        actual = hull.gap(centre, rotation, np.array([.5, 0, .5]), np.array([.35, .35, .025]))
        assert (actual > 0) == (expected > 0)
