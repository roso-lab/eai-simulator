"""Millimetre storage and shared road-axis guarantees."""
from copy import deepcopy
import json
import math

import pytest

from tools.topology_authoring.precision import millimetres, quantize_geometry
from tools.topology_authoring.road_builder import plan_navigation_road, snap_road_start
from tools.topology_authoring.map_project import MapProject, read_json, sha256, write_json


@pytest.mark.parametrize('value,expected', [
    (1.2345, 1.235), (-1.2345, -1.235), (.0005, .001), (-.0005, -.001),
    (-.0004, 0.0), (1.23449, 1.234), (12, 12.0)])
def test_rounding(value, expected):
    result = millimetres(value)
    assert result == expected
    assert math.copysign(1, result) == (1 if expected >= 0 else -1)
    assert millimetres(result) == result


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), '1.2'])
def test_bad_coordinates(value):
    with pytest.raises(ValueError):
        millimetres(value)


def graph(domain='ground'):
    return {'domain': domain, 'version': 2, 'levels': [{'id': 'ground'}],
            'nodes': [], 'edges': []}


def node(g, identifier, point):
    g['nodes'].append({'id': identifier, 'position_xyz': point, 'level_id': 'ground'})


@pytest.mark.parametrize('domain', ['ground', 'aerial'])
@pytest.mark.parametrize('radius', [.05, .1, .5])
def test_axis_survives_start_snap_and_rejects_off_axis_end(domain, radius):
    g = graph(domain)
    node(g, 'start', [0, 0, 0]); node(g, 'off-axis', [2, .04, 0])
    plan = plan_navigation_road(g, [.03, .02, 0], [2, .02, 0],
                               level_id='ground', snap_radius_m=radius, axis_snap=True)
    assert plan['start'] == [0, 0, 0]
    assert plan['end'] == [2, 0, 0]
    assert plan['axis'] == 'x'
    assert plan['snap']['end']['node_id'] is None
    assert g['nodes'][1]['position_xyz'] == [2, .04, 0]
    assert all(p[1] == 0 for e in plan['document']['edges'] for p in e['polyline_xyz'])


def test_axis_edge_intersection_instead_of_nearest_projection():
    g = graph()
    node(g, 'a', [1, -1, 0]); node(g, 'b', [3, 1, 0])
    g['edges'] = [{'id': 'e1', 'from': 'a', 'to': 'b', 'polyline_xyz': [[1,-1,0],[3,1,0]], 'directed': True}]
    p = plan_navigation_road(g, [0,0,0], [2.1,.02,0], level_id='ground',
                             snap_radius_m=.2, axis_snap=True)
    assert p['end'] == [2,0,0]
    assert p['snap']['end']['edge_id'] == 'e1'
    assert next(e for e in p['document']['edges'] if e['id']=='e1')['directed']


def test_fixed_start_and_c_use_raw_end_without_moving_start():
    g = graph(); node(g, 'n', [0,0,0])
    fixed = snap_road_start(g, [.03,.02,0], level_id='ground', snap_radius_m=.1)
    args = dict(level_id='ground', snap_radius_m=.1, axis_snap=True, fixed_start=fixed)
    p = plan_navigation_road(g, [.03,.02,0], [2,.05,0], **args)
    free = plan_navigation_road(g, [.03,.02,0], [2,.05,0], disable_snap=True, **args)
    restored = plan_navigation_road(g, [.03,.02,0], [2,.05,0], **args)
    assert p['end'] == restored['end'] == [2,0,0]
    assert free['end'] == [2,.05,0] and free['axis'] is None
    assert free['start'] == p['start'] == [0,0,0]
    g['nodes'][0]['position_xyz'] = [1,0,0]
    with pytest.raises(ValueError, match='changed'):
        plan_navigation_road(g, [0,0,0], [2,0,0], **args)


@pytest.mark.parametrize('degrees,axis', [(9.9,'x'), (10.1,None), (80.1,'y'), (79.9,None)])
def test_axis_threshold(degrees, axis):
    angle = math.radians(degrees)
    p = plan_navigation_road(graph(), [0,0,0], [2*math.cos(angle),2*math.sin(angle),1.12345],
                             level_id='ground', axis_snap=True)
    assert p['axis'] == axis
    assert p['end'][2] == 1.123


@pytest.fixture
def project(tmp_path):
    return MapProject.create_blank(tmp_path/'map', 'test', [-2,-2,2,2], .5)


@pytest.mark.parametrize('domain', ['ground','aerial'])
def test_edit_history_and_saved_coordinates(project, domain):
    project.edit('add_node', domain=domain, position_xyz=[.12345,-.67855,1.2345], level_id='ground')
    project.edit('add_node', domain=domain, position_xyz=[1.12345,.12345,2.1111], level_id='ground')
    project.edit('add_edge', domain=domain, from_id='node:0001', to_id='node:0002')
    before = project._snapshot()
    project.edit('translate_edge_xy', domain=domain, id='edge:0001', delta_xy=[.0006,-.0006])
    doc = project.document_for(domain)
    assert doc.nodes[0]['position_xyz'] == [.124,-.680,1.235]
    assert doc.edges[0]['polyline_xyz'][0] == doc.nodes[0]['position_xyz']
    after = project._snapshot()
    project.undo(); assert project._snapshot() == before
    project.redo(); assert project._snapshot() == after
    project.save()
    assert MapProject(project.root)._snapshot() == project._snapshot()


def test_new_rounding_collision_rolls_back(project):
    project.edit('add_node', position_xyz=[0,0,0], level_id='ground')
    before, rev = project._snapshot(), project.revision
    with pytest.raises(ValueError, match='collides nodes'):
        project.edit('add_node', position_xyz=[.0004,0,0], level_id='ground')
    assert project._snapshot() == before and project.revision == rev


def test_legacy_save_migrates_only_navigation_and_is_stable(project):
    project.edit('add_node', position_xyz=[0,0,0], level_id='ground')
    project.edit('add_node', position_xyz=[1,0,1], level_id='ground')
    project.edit('add_edge', from_id='node:0001', to_id='node:0002')
    project.save()
    # Build a hash-valid old package without going through new edit normalization.
    folder = project.root/'current'
    data = read_json(folder/'topology.json')
    data['nodes'][0]['position_xyz'] = [.123456, .234567, .345678]
    data['edges'][0]['polyline_xyz'][0] = data['nodes'][0]['position_xyz'][:]
    write_json(folder/'topology.json', data)
    manifest = read_json(folder/'manifest.json')
    manifest['files']['topology.json'] = sha256(folder/'topology.json')
    write_json(folder/'manifest.json', manifest)
    opened = MapProject(project.root)
    assert opened.document.nodes[0]['position_xyz'] == [.123456,.234567,.345678]
    old_bytes = (folder/'topology.json').read_bytes()
    opened.save()
    assert opened.document.nodes[0]['position_xyz'] == [.123,.235,.346]
    assert (folder/'topology.json').read_bytes() != old_bytes
    assert MapProject(project.root)._snapshot() == opened._snapshot()
    saved = (folder/'topology.json').read_bytes()
    opened.save()
    assert (folder/'topology.json').read_bytes() == saved


def test_save_collision_does_not_write_or_mutate(project):
    project.edit('add_node', position_xyz=[0,0,0], level_id='ground')
    project.edit('add_node', position_xyz=[1,0,0], level_id='ground')
    project.save()
    project.document.nodes[1]['position_xyz'] = [.0004,0,0]
    before, manifest = project._snapshot(), (project.root/'project.json').read_bytes()
    with pytest.raises(ValueError, match='collides nodes'):
        project.save()
    assert project._snapshot() == before
    assert (project.root/'project.json').read_bytes() == manifest


def test_polyline_collapse_is_rejected():
    g=graph(); node(g,'a',[0,0,0]); node(g,'b',[1,0,0])
    g['edges']=[{'id':'short-segment','from':'a','to':'b','polyline_xyz':[[0,0,0],[.0004,0,0],[1,0,0]]}]
    with pytest.raises(ValueError, match='short-segment'):
        quantize_geometry(g)
