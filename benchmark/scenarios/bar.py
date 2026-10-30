"""Authored bar T1--T4 contracts on the shared logical skill/session ledger."""
from dataclasses import dataclass, replace
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

from benchmark.hetero_diagnostic_runtime_v1 import (
    CONTROL_DT_S, EpisodeSpec, ResourceSpec, RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec,
)
from benchmark.bar_runtime_v1 import BarRuntime

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / 'usd/scene/Production and logistics/bar/semantic_map/current'
DEFAULT_MANIFEST = ROOT / 'benchmark/tasks/bar/bar_task_manifest.v1.json'


def _load(path):
    return json.loads(path.read_text())


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class BarScenarioAdapter:
    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST

    @property
    def scenario_id(self):
        return 'bar'

    def manifest(self):
        return _load(self.manifest_path)

    def semantic_graph(self):
        m = self.manifest(); actual = _load(self.map_root / 'manifest.json')
        if actual['scene_key'] != m['scene_key']:
            raise ValueError('bar map scene mismatch')
        for key in ('revision', 'semantic_binding_digest'):
            if actual[key] != m['semantic_map'][key]:
                raise ValueError('bar map ' + key + ' mismatch')
        for name, digest in actual['files'].items():
            if hashlib.sha256((self.map_root / name).read_bytes()).hexdigest() != digest:
                raise ValueError('bar map file digest mismatch: ' + name)
        from benchmark.case_input_audit_v1 import audit_semantic_map
        audit = audit_semantic_map(self.map_root, allow_empty_domains=('aerial',))
        if not audit['case_ready']:
            raise ValueError('bar map audit failed: ' + str(audit['failures']))
        d = _load(self.map_root / 'topology.json')
        positions = {n['id']: tuple(n['position_xyz']) for n in d['nodes']}
        nodes = [SemanticNode('ground::' + nid, 'ground', p) for nid, p in positions.items()]
        edges = []
        for e in d['edges']:
            pts = e.get('polyline_xyz') or [positions[e['from']], positions[e['to']]]
            length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
            edges.append(SemanticEdge('ground::' + e['from'], 'ground::' + e['to'], length,
                bidirectional=not e.get('directed', False), traversal_mode=e.get('traversal_mode', 'walk'),
                required_controller_capabilities=tuple(e.get('required_controller_capabilities', ()))))
        graph = SemanticGraph(nodes, edges)
        shortest = graph.shortest
        @lru_cache(maxsize=None)
        def cached(source, target, capabilities):
            return shortest(source, target, capabilities)
        graph.shortest = lambda source, target, capabilities=None: cached(source, target,
            tuple(sorted(capabilities)) if capabilities is not None else None)
        for p in _load(self.map_root / 'semantic-points.json')['points']:
            for b in p['navigation_bindings']:
                graph.bind(p['label'], b['domain'] + '::' + b['node_id'])
        # Authored stair entry is a topology point; no new navigation edges.
        graph.bind('stair_entry', 'ground::node:0046')
        return graph

    def _condition(self, cid, tier):
        m = self.manifest()
        selected = cid or next(t['default_condition'] for t in m['difficulty_tiers'] if t['difficulty_tier'] == tier)
        condition = dict(next(c for c in m['conditions'] if c['condition_id'] == selected))
        if tier >= 3 and selected in {'communication_stress', 'availability_loss', 'resource_contention'}:
            base = next(c for c in m['conditions'] if c['condition_id'] == ('battery_hard' if tier == 3 else 'battery_very_hard'))
            for key in ('battery_enabled', 'battery_capacity', 'battery_reserve', 'charge_wait_s', 'initial_battery', 'drain_per_s'):
                condition[key] = base[key]
        return condition

    def _variant(self, variant_id):
        rows = self.manifest()['roster_variants']
        return next(v for v in rows if v['variant_id'] == variant_id) if variant_id else rows[0]

    def build_episode(self, *, tier=4, condition_id=None, variant_id=None, communication=None, seed=0):
        if tier not in range(1, 5):
            raise ValueError('bar tier must be 1..4')
        m = self.manifest(); c = self._condition(condition_id, tier); v = self._variant(variant_id)
        if tier == 4 and any(r in {'g1', 'lite3'} for r in v['robots']):
            raise ValueError('authored T4 roster excludes G1 and lite3')
        graph = self.semantic_graph(); byid = {r['robot_id']: r for r in m['robot_roster']}
        robots = tuple(RobotSpec(rid, 'ground', 'ground::' + m['spawn_points'][start],
            speed_mps=byid[rid]['speed_mps'], capabilities=tuple(byid[rid]['capabilities']),
            battery_capacity_m=100.0 if c['battery_enabled'] else None, battery_per_meter=0.0,
            battery_reserve_m=c.get('battery_reserve', 0))
            for rid, start in zip(v['robots'], ('storage', 'production', 'reserve')))
        tasks = []; objects = []; count = {1: 1, 2: 2, 3: 3, 4: 2}[tier]
        counter = 3 if tier == 3 else 1
        inner, outer = (f'floor1-bar_counter-{counter}-{side}' for side in ('inner', 'external'))
        deliveries = tuple(f'order_{i}_place' for i in range(1, count + 1))
        def task(name, kind, target, pre=(), caps=('manipulator',), resource=None, service=0,
                 continuation=None, obj=None, action=None, **kwargs):
            tasks.append(TaskSpec(name, kind, (target,), prerequisites=tuple(pre), required_domain='ground',
                required_capabilities=tuple(caps), resource_id=resource, service_ticks=service,
                continuation_group=continuation, object_id=obj, object_action=action,
                report_required=True, announce_after=tuple(pre), **kwargs))
        if tier >= 3:
            task('service_cover', 'service_cover', inner, caps=('navigation',), hold_until_prerequisites=('service_end',))
        for i in range(1, count + 1):
            prefix = f'order_{i}'; source = 'refrigerator' if i % 2 else 'floor1-storage_area'
            destination = f'floor2-dining_table-{i}' if tier == 4 else f'floor1-dining_table-{i + (3 if tier == 3 else 1 if tier == 2 else 0)}'
            objects.append({'object_id': prefix, 'source': source, 'destination': destination,
                'order_id': prefix, 'beverage_type': 'tray_drinks' if tier == 4 else 'cold_drink' if i % 2 else 'no_ice_drink',
                'quantity': 1, 'inventory_version': 'bar-inventory-v1'})
            pre = ('service_cover',) if tier >= 3 else ()
            if tier == 4 and i > 1:
                pre = (f'order_{i-1}_place',)
            task(prefix+'_acquire', 'material_acquire', source, pre=pre, continuation=prefix+'_producer',
                 obj=prefix, action='pick', resource='refrigerator_mutex' if source == 'refrigerator' else None, service=10)
            task(prefix+'_prepare', 'prepare', 'workbench-1' if i == 1 else 'workbench-2', pre=(prefix+'_acquire',),
                 continuation=prefix+'_producer', resource='workbench_mutex', service=20)
            task(prefix+'_release', 'handoff_ready', inner, pre=(prefix+'_prepare',),
                 continuation=prefix+'_producer', obj=prefix, action='handoff_ready',
                 service=50, hold_until_prerequisites=(prefix+'_accept',))
            task(prefix+'_accept', 'handoff_receive', outer, pre=(prefix+'_release',),
                 caps=('manipulator', 'handoff_receive'), continuation=prefix+'_carrier', obj=prefix, action='handoff_receive')
            place_pre = [prefix+'_accept']
            if tier == 4:
                # Two distinct robots jointly perform the authored stair-entry
                # handoff. Persistent stair transport belongs to the carrier.
                group = prefix+'_joint'
                for role in ('carrier', 'support'):
                    task(prefix+'_joint_'+role, 'synchronized_handoff', 'stair_entry', pre=(prefix+'_accept',),
                         continuation=prefix+'_carrier' if role == 'carrier' else None,
                         caps=('manipulator', 'stair_capable') if role == 'carrier' else ('manipulator', 'loader'), cooperative_group=group,
                         distinct_group=group, service=25)
                joint = (prefix+'_joint_carrier', prefix+'_joint_support')
                task(prefix+'_stair', 'stair_transport', destination, pre=joint, caps=('manipulator', 'stair_capable'),
                     continuation=prefix+'_carrier', service=25)
                task(prefix+'_floor2_accept', 'floor2_accept', destination, pre=(prefix+'_stair',),
                     continuation=prefix+'_carrier', service=1)
                place_pre = [prefix+'_floor2_accept']
            elif i > 1:
                place_pre.append(f'order_{i-1}_place')
            task(prefix+'_place', 'delivery', destination, pre=place_pre, continuation=prefix+'_carrier',
                 obj=prefix, action='place', service=10)
        if tier >= 3:
            tasks.append(TaskSpec('service_end', 'event', ('service',), activation_tasks=deliveries,
                                  activation_count=count))
        tasks.append(TaskSpec('episode_closeout', 'closeout', ('closeout',), public=False))
        resources = tuple(ResourceSpec(r['resource_id'], graph.resolve(r['semantic_label'], 'ground')[0],
            capacity=r['capacity'], service_ticks=max(1, round((c['charge_wait_s'] if r['resource_id'] == 'charging_point'
                and c['battery_enabled'] else r['service_s']) / CONTROL_DT_S)),
            recharges=r.get('recharges', False) and c['battery_enabled']) for r in m['resources'])
        deadline = m['hard_deadline_ticks'][f't{tier}']
        return EpisodeSpec(graph, robots, tuple(tasks), resources=resources, objects=tuple(objects),
            horizon_ticks=deadline+1000, hard_deadline_tick=deadline, seed=seed,
            communication_condition=communication or c['communication'], report_retry_interval_ticks=100)

    def contract(self, *, tier=4, condition_id=None, variant_id=None):
        m = self.manifest(); c = self._condition(condition_id, tier); v = self._variant(variant_id)
        row = next(t for t in m['difficulty_tiers'] if t['difficulty_tier'] == tier)
        return dict(row, scenario_id='bar', tier=tier, condition_id=c['condition_id'], variant_id=v['variant_id'],
            map_revision=m['semantic_map']['revision'], semantic_binding_digest=m['semantic_map']['semantic_binding_digest'],
            digest=_digest({'manifest': m, 'tier': tier, 'condition': c, 'variant': v}), paper_score_eligible=False)

    def runtime(self, *, tier=4, condition_id=None, variant_id=None, communication=None, algorithm=None, seed=0):
        from benchmark.bar_algorithms import BarGoalAlgorithm
        c = self._condition(condition_id, tier)
        config = dict(c, communication_capacity=self.manifest()['communication_capacity_bits_per_tick'][communication or c['communication']], contract_digest=self.contract(tier=tier, condition_id=condition_id, variant_id=variant_id)['digest'], tier=tier, initial_battery=c.get('initial_battery', 100),
                      drain_per_s=c.get('drain_per_s', 0), joint_sync_window_ticks=1500, service_point='floor1-bar_counter-3-inner' if tier == 3 else 'floor1-bar_counter-1-inner',
                      **self.manifest()['service_policy'])
        return BarRuntime(self.build_episode(tier=tier, condition_id=condition_id, variant_id=variant_id,
            communication=communication, seed=seed), algorithm=algorithm or BarGoalAlgorithm(), config=config)

    def run_generic(self, *, tier=4, condition_id=None, variant_id=None, communication=None, algorithm=None, seed=0):
        runtime = self.runtime(tier=tier, condition_id=condition_id, variant_id=variant_id,
                               communication=communication, algorithm=algorithm, seed=seed)
        cp = runtime.checkpoint(); result = runtime.run()
        result['scenario'] = self.contract(tier=tier, condition_id=condition_id, variant_id=variant_id)
        result['checkpoint'] = cp
        result['paired_state_digest'] = _digest({k:v for k,v in cp['snapshot'].items() if k != 'algorithm'})
        result['checkpoints'] = runtime.checkpoints
        result['replay_equivalent'] = runtime.replay(cp)['replay_equivalent']
        from benchmark.bar_evaluator_v1 import evaluate_bar
        result['evaluation'] = evaluate_bar(result, runtime.episode)
        result['diagnostic_metrics'] = self.diagnostic_metrics(result)
        return result

    @staticmethod
    def diagnostic_metrics(result):
        return {k: result.get(k) for k in ('success', 'makespan_s', 'distance_m', 'energy_used',
            'charge_services', 'service_coverage', 'max_service_gap_s', 'resource_wait_ticks',
            'resource_contention_requests', 'communication_bits', 'communication_delay_ticks',
            'communication_loss_bits', 'notification_delivered', 'reallocation_count', 'availability_events',
            'replay_equivalent', 'paper_score_eligible')}
