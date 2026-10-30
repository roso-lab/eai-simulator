"""Bar logical execution: typed task graph + the shared task-object ledger.

Controller/session receipts below are graph-simulator evidence, never physics
receipts. The tested allocator cannot edit these callbacks or the object ledger.
"""
from copy import deepcopy
from dataclasses import replace, asdict

from benchmark.hetero_diagnostic_runtime_v1 import (
    CONTROL_DT_S, DiagnosticRuntime, TaskSpec, CommunicationModel, ResourceLedger, _digest,
)
from benchmark.replay_state_v3 import SnapshotParticipantV3
from task.runtime.logical_objects import ControllerObservation, LogicalObjectRuntime, SkillSessionResult


class BarResourceLedger(ResourceLedger):
    """Registered background demand is external work, not a deployed robot."""
    def advance(self, tick):
        return tuple(x for x in super().advance(tick) if not x['robot_id'].startswith('__external__'))


class BarRuntime(DiagnosticRuntime):
    def __init__(self, episode, algorithm=None, *, config):
        self.config = deepcopy(config)
        self.charge_resume = {}
        self.energy_used = {r.robot_id: 0.0 for r in episode.robots}
        self.checkpoints = []
        self.service_samples = self.service_covered = self.service_gap = self.max_service_gap = 0
        self._skill_zone = {}
        self._skill_point = None
        self.fault_state = None
        self.joint_evidence = {}
        self.service_trace = []
        self.coordinator_pending = None
        self.coordinator_signature = None
        self.resource_peaks = {}
        self.inventory = {}
        super().__init__(episode, algorithm)
        self.communication = CommunicationModel(episode.communication_condition, seed=episode.seed,
            bits_per_tick=config['communication_capacity'], max_queue_bits=1048576)
        if config['condition_id'] == 'resource_contention':
            self.resources.request('workbench_mutex', '__external__order', '__external__order', 0, 1000)
            self.events.append({'tick':0, 'kind':'external_resource_demand', 'resource_id':'workbench_mutex',
                                'start_tick':0, 'end_tick':1000, 'capacity':1})
        for obj in episode.objects:
            self.inventory[obj['source']] = self.inventory.get(obj['source'], 0) + obj['quantity']
            self.object_state[obj['object_id']].update({key:obj[key] for key in ('order_id','beverage_type','quantity','inventory_version')})
        for rid, state in self.robot_states.items():
            if state.battery is not None:
                state.battery = float(config['initial_battery'])
        self._initial_snapshot = self.snapshot()
        self.mark('cp_00_spawn_valid')
        self.mark('cp_01_material_ready')

    def _build_registry(self):
        self.resources = BarResourceLedger(tuple(self.resources.specs.values()))
        registry = super()._build_registry()
        self.logical = LogicalObjectRuntime(
            scene_id='production_bar',
            objects=[{'entity_id': o['object_id'], 'category': 'drink_order',
                      'metadata': {'minimum_holders': 1, 'mass_kg': 0.5, **{key:o[key] for key in ('order_id','beverage_type','quantity','inventory_version')}},
                      'initial_semantic_point': o['source'],
                      'target_semantic_point': o['destination']} for o in self.episode.objects],
            robot_ids=[r.robot_id for r in self.episode.robots], clock=lambda: self.tick,
            observe=self._observe_skill, run_session=self._session,
            admission=lambda rid, definition, n: 'manipulator' in self.robot_states[rid].active_capabilities,
        )
        registry.register(SnapshotParticipantV3('bar_logic', 'v1', self._capture_bar, self._restore_bar, self._capture_bar))
        return registry

    def _capture_bar(self):
        return {'logical': self.logical.capture_state(), 'charge_resume': deepcopy(self.charge_resume),
                'energy_used': dict(self.energy_used), 'fault_state': deepcopy(self.fault_state), 'joint_evidence': deepcopy(self.joint_evidence),
                'service_trace': deepcopy(self.service_trace), 'coordinator_pending': deepcopy(self.coordinator_pending), 'coordinator_signature': self.coordinator_signature, 'resource_peaks': dict(self.resource_peaks), 'inventory':dict(self.inventory),
                'transient_specs': [asdict(t) for key, t in self.tasks.specs.items() if key.startswith('__')], 'service': [self.service_samples, self.service_covered,
                self.service_gap, self.max_service_gap]}

    def _restore_bar(self, data):
        self.logical.restore_state(data['logical'], expected_digest=data['logical']['state_digest'])
        self.charge_resume = deepcopy(data['charge_resume'])
        self.energy_used = dict(data['energy_used'])
        self.fault_state = deepcopy(data['fault_state'])
        self.joint_evidence = deepcopy(data['joint_evidence'])
        self.service_trace = deepcopy(data['service_trace'])
        self.coordinator_pending = deepcopy(data['coordinator_pending'])
        self.coordinator_signature = data['coordinator_signature']
        self.resource_peaks = dict(data['resource_peaks'])
        self.inventory = dict(data['inventory'])
        for row in data['transient_specs']:
            self.tasks.specs[row['task_id']] = TaskSpec(**row)
        self.service_samples, self.service_covered, self.service_gap, self.max_service_gap = data['service']

    def _observe_skill(self, robot_id, point):
        state = self.robot_states[robot_id]
        allowed = self._skill_zone.get(robot_id, self.episode.graph.resolve(point, 'ground'))
        arrived = state.node_id in allowed and state.edge_progress_m == 0
        return ControllerObservation(robot_id, point, self.tick, arrived,
            _digest({'kind': 'logical_graph_arrival', 'robot': robot_id, 'node': state.node_id,
                     'point': point, 'tick': self.tick, 'arrived': arrived}))

    def _session(self, command):
        self.events.append({'tick': self.tick, 'kind': 'logical_skill_session', **command})
        return SkillSessionResult('session-' + command['request_id'], command['request_id'],
            tuple(command['robot_ids']), command['object_id'], command['operation'],
            command['semantic_point'], self.tick, 'completed', _digest(command))

    def _complete_target(self, robot):
        task = self.tasks.specs.get(robot.task_id)
        if task is None:
            return super()._complete_target(robot)
        if task.kind == 'hold_return':
            robot.task_id = robot.target_key = robot.resource_id = None
            robot.route = []; robot.status = 'hold'
            return
        if task.kind == 'charge':
            rid = robot.spec.robot_id
            robot.battery = robot.spec.battery_capacity_m
            self.events.append({'tick': self.tick, 'kind': 'battery_full', 'robot_id': rid,
                                'resource_id': 'charging_point'})
            saved = self.charge_resume.pop(rid)
            robot.resource_id = None
            robot.task_id, robot.target_key, robot.status = saved['task_id'], saved['target_key'], saved['status']
            if robot.task_id:
                target = self.episode.graph.resolve(robot.target_key, 'ground')[0]
                _, robot.route = self.episode.graph.shortest(robot.node_id, target, robot.active_capabilities)
                robot.route_index = 0; robot.edge_progress_m = 0.0; robot.status = 'moving'
                if len(robot.route) == 1:
                    self._arrive(robot)
            elif saved['status'] == 'hold' and any(self.tasks.status.get(t) != 'completed' for t in robot.hold_until_tasks):
                robot.task_id, robot.target_key = '__return__' + rid, saved['node_id']
                self.tasks.specs[robot.task_id] = TaskSpec(robot.task_id, 'hold_return', (saved['node_id'],), required_for_success=False)
                self.tasks.status[robot.task_id] = 'running'; self.tasks.remaining[robot.task_id] = [saved['node_id']]
                _, robot.route = self.episode.graph.shortest(robot.node_id, saved['node_id'], robot.active_capabilities)
                robot.route_index = 0; robot.edge_progress_m = 0; robot.status = 'moving'
                if len(robot.route) == 1:
                    self._arrive(robot)
            else:
                robot.status = 'idle'; robot.route = []
            return
        if task.kind == 'prepare':
            order_id = task.task_id.removesuffix('_prepare')
            if self.logical.object_state(order_id)['holders'] != [robot.spec.robot_id]:
                self._fail('prepare_without_material:' + order_id); return
            self.object_state[order_id]['prepared'] = True
            self.events.append({'tick':self.tick, 'kind':'order_verified', 'order_id':order_id,
                                'beverage_type':self.object_state[order_id]['beverage_type'],
                                'quantity':self.object_state[order_id]['quantity'], 'robot_id':robot.spec.robot_id})
        if task.object_id and task.object_action:
            action = task.object_action
            if action == 'handoff_ready':
                obj = self.logical.object_state(task.object_id)
                if obj['holders'] != [robot.spec.robot_id]:
                    self._fail('invalid_handoff_owner:' + task.task_id); return
                self.object_state[task.object_id]['location'] = robot.target_key
                self.events.append({'tick':self.tick, 'kind':'handoff_release_ready', 'object_id':task.object_id,
                    'owner':robot.spec.robot_id, 'target':robot.target_key, 'task_id':task.task_id})
            else:
                operation = {'handoff_receive': 'receive', 'pick': 'pick', 'place': 'place'}[action]
                self._skill_zone = {}
                if operation == 'receive':
                    # Inner/external counter poses are one authored interaction
                    # window. Each participant must be at its own fixed pose.
                    holders = self.logical.object_state(task.object_id)['holders']
                    release = self.tasks.specs[task.prerequisites[0]]
                    for holder in holders:
                        self._skill_zone[holder] = self.episode.graph.resolve(release.targets[0], 'ground')
                if operation == 'pick' and self.inventory[self.object_state[task.object_id]['source']] < self.object_state[task.object_id]['quantity']:
                    self._fail('inventory_exhausted:' + task.object_id); return
                try:
                    self.logical.execute(request_id=task.task_id, operation=operation,
                        object_id=task.object_id, robot_ids=[robot.spec.robot_id], semantic_point=robot.target_key)
                except ValueError as exc:
                    self._fail('logical_skill_rejected:' + str(exc)); return
                finally:
                    self._skill_zone = {}
                if operation == 'pick':
                    self.inventory[self.object_state[task.object_id]['source']] -= self.object_state[task.object_id]['quantity']
                obj = self.logical.object_state(task.object_id)
                for s in self.robot_states.values():
                    if s.load_id == task.object_id:
                        s.load_id = None
                for holder in obj['holders']:
                    self.robot_states[holder].load_id = task.object_id
                self.object_state[task.object_id].update(
                    status='held' if obj['holders'] else 'placed',
                    held_by=obj['holders'][0] if obj['holders'] else None,
                    location=obj['semantic_point'] or robot.target_key)
                self.events.append({'tick': self.tick, 'kind': {'pick': 'object_picked', 'receive': 'handoff_accept',
                    'place': 'object_placed'}[operation], 'robot_id': robot.spec.robot_id,
                    'object_id': task.object_id, 'target': robot.target_key,
                    'previous_holders':holders if operation == 'receive' else [], 'holders':list(obj['holders'])})
            # Let the shared runtime handle reports/holds, not a second object mutation.
            self.tasks.specs[task.task_id] = replace(task, object_action=None)
            try:
                super()._complete_target(robot)
            finally:
                self.tasks.specs[task.task_id] = task
        else:
            super()._complete_target(robot)

    def _observation(self):
        observation = super()._observation()
        observation['scene_description'] = observation['scene_description'].replace('city_01 semantic-map logical benchmark', 'bar semantic-map logical benchmark')
        for goal in observation['pending_goals']:
            obj = self.object_state.get(goal.get('object_id'))
            allowed = set(goal.get('eligible_robots', ()))
            if obj:
                if goal.get('object_action') == 'handoff_receive':
                    receive_task = self.tasks.specs[goal['task_id']]
                    release = self.tasks.specs[receive_task.prerequisites[0]]
                    owner = self.robot_states[obj['held_by']]
                    if owner.node_id not in self.episode.graph.resolve(release.targets[0], 'ground') or owner.edge_progress_m != 0:
                        allowed = set()
                    allowed.discard(obj.get('held_by'))
                    allowed = {rid for rid in allowed if not self.robot_states[rid].load_id}
                if goal.get('object_action') == 'pick':
                    allowed = {rid for rid in allowed if not self.robot_states[rid].load_id}
            goal['eligible_robots'] = sorted(allowed)
        return observation

    def _arrive_cooperative(self, robot, task):
        group = task.cooperative_group
        evidence = self.joint_evidence.setdefault(group, {'arrivals': {}, 'samples': 0, 'valid': True})
        evidence['arrivals'][robot.spec.robot_id] = self.tick
        super()._arrive_cooperative(robot, task)

    def _complete_cooperative_group(self, group):
        evidence = self.joint_evidence[group]
        arrivals = evidence['arrivals']
        if (len(arrivals) != 2 or max(arrivals.values()) - min(arrivals.values()) > self.config['joint_sync_window_ticks']
                or not evidence['valid'] or evidence['samples'] < 25):
            self._fail('joint_action_invalid:' + group); return
        super()._complete_cooperative_group(group)
        object_id = group.removesuffix('_joint')
        self.events.append({'tick': self.tick, 'kind': 'joint_action_completed', 'group': group,
            'object_id': object_id, 'participants': sorted(arrivals), 'arrival_ticks': dict(arrivals),
            'support_samples': evidence['samples'], 'support_valid': evidence['valid'],
            'carrier': self.logical.object_state(object_id)['holders'][0], 'evidence_scope': 'logical_graph'})

    def _arrive(self, robot):
        task = self.tasks.specs.get(robot.task_id)
        if task and task.kind == 'handoff_ready':
            rid = robot.spec.robot_id
            accepted = self.resources.request('handoff_window', rid, task.task_id, self.tick,
                                              self.episode.horizon_ticks + 1)
            robot.resource_id = 'handoff_window'
            robot.status = 'servicing' if accepted else 'handoff_queue'
            if accepted:
                robot.service_until_tick = self.tick + task.service_ticks
            self.events.append({'tick': self.tick, 'kind': 'handoff_window_request',
                'robot_id': rid, 'task_id': task.task_id, 'accepted': accepted})
            return
        return super()._arrive(robot)

    def _process_reports(self, frames):
        for frame in frames:
            if (frame.kind == 'coordination_state' and self.coordinator_pending
                    and frame.message_id in self.coordinator_pending['frame_ids']):
                self.coordinator_pending['delivered'] = True
                self.coordinator_pending['observation'] = deepcopy(frame.payload)
                self.events.append({'tick': self.tick, 'kind': 'coordination_state_delivered',
                                    'message_id': frame.message_id})
        super()._process_reports(frames)

    def _assign(self):
        if self.coordinator_pending is None:
            observation = self._observation()
            if not observation['pending_goals']:
                return
            signature = _digest({'goals': observation['pending_goals'], 'resources': observation['resources'],
                'robots': {rid: {key:r[key] for key in ('position','active_capabilities')} for rid,r in observation['available_robots'].items()}})
            if signature == self.coordinator_signature:
                return
            self.coordinator_signature = signature
            self.coordinator_pending = {'observation': observation, 'attempts': 0,
                'next_tick': self.tick, 'frame_ids': [], 'delivered': False}
        packet = self.coordinator_pending
        if not packet['delivered']:
            if self.tick >= packet['next_tick']:
                if packet['attempts'] >= self.episode.report_retry_limit:
                    self._fail('coordinator_notification_timeout'); return
                self._message_counter += 1
                message_id = 'coordination-' + str(self._message_counter)
                frame = self.communication.send(message_id=message_id, sender_id='team', receiver_id='coordinator',
                    kind='coordination_state', payload=packet['observation'], tick=self.tick)
                packet['frame_ids'].append(message_id); packet['attempts'] += 1
                packet['next_tick'] = self.tick + self.episode.report_retry_interval_ticks + (frame.bits + self.config['communication_capacity'] - 1) // self.config['communication_capacity']
                self.events.append({'tick': self.tick, 'kind': 'coordination_state_submitted',
                                    'message_id': message_id, 'attempt': packet['attempts']})
            return
        decision = self.algorithm.decide(packet['observation'])
        self.events.append({'tick':self.tick, 'kind':'allocation_proposal', 'decision':dict(decision)})
        self.coordinator_pending = None
        admitted = dict(decision)
        for rid, goal in decision.items():
            robot = self.robot_states.get(rid)
            task = self.tasks.specs.get(goal.split(':',1)[0])
            if task and task.object_action == 'handoff_receive':
                owner = self.robot_states[self.object_state[task.object_id]['held_by']]
                release = self.tasks.specs[task.prerequisites[0]]
                if owner.node_id not in self.episode.graph.resolve(release.targets[0], 'ground') or owner.edge_progress_m != 0:
                    self.events.append({'tick':self.tick, 'kind':'assignment_deferred', 'robot_id':rid,
                                        'task_id':task.task_id, 'reason':'handoff_owner_away'})
                    admitted.pop(rid); continue
            if task and robot and not set(task.required_capabilities).issubset(robot.active_capabilities):
                continue  # Keep invalid capability proposals for the normal evaluator.
            if robot is None or robot.battery is None or robot.status != 'idle':
                continue
            try:
                target = self.episode.graph.resolve(goal.split(':', 1)[1], 'ground')[0]
                home = self.episode.graph.resolve('floor1-charging_point', 'ground')[0]
                outbound = self.episode.graph.shortest(robot.node_id, target, robot.active_capabilities)[0]
                inbound = self.episode.graph.shortest(target, home, robot.active_capabilities)[0]
            except ValueError:
                continue
            budget = (outbound + inbound) / robot.spec.speed_mps * self.config['drain_per_s'] + robot.spec.battery_reserve_m
            if robot.battery < budget:
                self.events.append({'tick':self.tick, 'kind':'energy_admission_deferred', 'robot_id':rid,
                                    'proposal':goal, 'required_battery':budget, 'battery':robot.battery})
                self._begin_charge(robot)
                admitted.pop(rid)
        super()._assign(admitted)
        for robot in self.robot_states.values():
            task = self.tasks.specs.get(robot.task_id)
            if task and task.kind == 'stair_transport' and robot.resource_id != 'stair_mutex':
                accepted = self.resources.request('stair_mutex', robot.spec.robot_id,
                    task.task_id, self.tick, self.episode.horizon_ticks + 1)
                robot.resource_id = 'stair_mutex'
                if not accepted:
                    robot.status = 'stair_queue'
                self.events.append({'tick': self.tick, 'kind': 'stair_entry_acquire',
                    'robot_id': robot.spec.robot_id, 'task_id': task.task_id, 'accepted': accepted})

    def _begin_charge(self, robot):
        rid = robot.spec.robot_id
        self.charge_resume[rid] = {'task_id': robot.task_id, 'target_key': robot.target_key, 'status': robot.status, 'node_id': robot.node_id}
        charge_id = '__charge__' + rid
        self.tasks.specs[charge_id] = TaskSpec(charge_id, 'charge', ('floor1-charging_point',),
            resource_id='charging_point', required_for_success=False)
        self.tasks.status[charge_id] = 'running'; self.tasks.remaining[charge_id] = ['floor1-charging_point']
        robot.task_id, robot.target_key = charge_id, 'floor1-charging_point'
        try:
            _, robot.route = self.episode.graph.shortest(robot.node_id,
                self.episode.graph.resolve(robot.target_key, 'ground')[0], robot.active_capabilities)
        except ValueError:
            self._fail('charging_point_unreachable:' + rid); return
        robot.route_index = 0; robot.edge_progress_m = 0.0; robot.status = 'moving'
        self.events.append({'tick': self.tick, 'kind': 'low_battery', 'robot_id': rid, 'battery': robot.battery})
        if len(robot.route) == 1:
            self._arrive(robot)

    def step(self):
        if self.terminal:
            return
        if (all(self.tasks.status[t] == 'completed' for t in self.tasks.required_task_ids if t != 'episode_closeout')
                and not any(self.resources.active.values()) and not any(self.resources.queues.values())):
            self.tasks.status['episode_closeout'] = 'completed'
            self.tasks.completed_tick['episode_closeout'] = self.tick
        if self.config['battery_enabled']:
            for robot in self.robot_states.values():
                if robot.status == 'lost':
                    continue
                charging = robot.spec.robot_id in self.charge_resume and robot.status == 'servicing'
                if not charging:
                    drain = self.config['drain_per_s'] * CONTROL_DT_S
                    robot.battery -= drain
                    self.energy_used[robot.spec.robot_id] += drain
                    if robot.battery < 0:
                        self._fail('battery_exhausted:' + robot.spec.robot_id); return
                home = self.episode.graph.resolve('floor1-charging_point', 'ground')[0]
                try:
                    distance_home = self.episode.graph.shortest(robot.node_id, home, robot.active_capabilities)[0]
                except ValueError:
                    self._fail('charging_point_unreachable:' + robot.spec.robot_id); return
                return_budget = distance_home / robot.spec.speed_mps * self.config['drain_per_s']
                protected_handoff = robot.load_id and any(t.object_id == robot.load_id and t.object_action == 'handoff_receive'
                    and self.tasks.status[t.task_id] == 'running' for t in self.episode.tasks)
                if (not protected_handoff and robot.spec.robot_id not in self.charge_resume
                    and robot.battery <= robot.spec.battery_reserve_m + return_budget
                    and robot.edge_progress_m == 0 and robot.status in {'idle', 'moving', 'hold', 'cooperative_wait'}):
                    self._begin_charge(robot)
        for robot in self.robot_states.values():
            if robot.status in {'handoff_queue', 'stair_queue'}:
                if any(x['robot_id'] == robot.spec.robot_id for x in self.resources.active[robot.resource_id]):
                    wait = next(x for x in self.resources.active[robot.resource_id] if x['robot_id'] == robot.spec.robot_id)
                    self.events.append({'tick': self.tick, 'kind': 'lease_admitted', 'resource_id': robot.resource_id,
                                        'robot_id': robot.spec.robot_id, 'wait_ticks': self.tick - wait['requested_tick']})
                    robot.status = 'moving' if robot.status == 'stair_queue' else 'servicing'
                    if robot.status == 'servicing':
                        robot.service_until_tick = self.tick + self.tasks.specs[robot.task_id].service_ticks
        for group, state in self._cooperative_states.items():
            if state['status'] == 'servicing':
                members = self._cooperative_members(group)
                obj_id = group.removesuffix('_joint')
                at_entry = self.episode.graph.resolve('stair_entry', 'ground')[0]
                valid = (len({r.spec.robot_id for _, r in members}) == 2 and
                    all(r.node_id == at_entry and r.edge_progress_m == 0 and 'manipulator' in r.active_capabilities
                        and set(t.required_capabilities).issubset(r.active_capabilities) for t, r in members) and
                    len(self.logical.object_state(obj_id)['holders']) == 1)
                self.joint_evidence[group]['samples'] += 1
                self.joint_evidence[group]['valid'] &= valid
                self.events.append({'tick': self.tick, 'kind': 'joint_support_sample', 'group': group,
                                    'object_id': obj_id, 'valid': valid})
                if not valid:
                    self._fail('joint_support_lost:' + group); return
        super().step()
        completed = self.tasks.completed_tick
        if self.config['tier'] >= 3 and 'service_cover' in completed:
            self.service_samples += 1
            station = self.episode.graph.resolve(self.config['service_point'], 'ground')[0]
            covered = any(r.node_id == station and r.edge_progress_m == 0 and r.status != 'lost'
                          for r in self.robot_states.values())
            monitor = self.tasks.assigned.get('__service_replacement', self.tasks.assigned['service_cover'])
            tolerated = (monitor in self.charge_resume or self.robot_states[monitor].task_id == '__return__' + monitor
                         or self.tasks.status.get('__service_replacement') in {'pending', 'running'})
            value = {'covered': covered, 'tolerated_registered_gap': bool(not covered and tolerated)}
            sample_tick = self.tick - 1
            if self.service_trace and all(self.service_trace[-1][key] == value[key] for key in value):
                self.service_trace[-1]['end_tick'] = sample_tick
            else:
                self.service_trace.append(dict(value, start_tick=sample_tick, end_tick=sample_tick))
            if not covered and not tolerated:
                self._fail('unregistered_service_gap'); return
            if covered:
                self.service_covered += 1; self.service_gap = 0
            else:
                self.service_gap += 1; self.max_service_gap = max(self.max_service_gap, self.service_gap)
        for resource_id, leases in self.resources.active.items():
            self.resource_peaks[resource_id] = max(self.resource_peaks.get(resource_id, 0), len(leases))
            if len(leases) > self.resources.specs[resource_id].capacity:
                self._fail('resource_capacity_violation:' + resource_id); return
        self._milestones()
        if self.success and self.service_samples:
            if (self.service_covered / self.service_samples < self.config['minimum_coverage']
                    or self.max_service_gap * CONTROL_DT_S > self.config['maximum_gap_s']):
                self._fail('continuous_service_coverage')

    def _complete_report(self, robot):
        task_id = robot.task_id
        trigger = task_id == self.config.get('availability_loss_task')
        task = self.tasks.specs[task_id]
        super()._complete_report(robot)
        if task_id.endswith('_accept'):
            release_id = task.prerequisites[0]
            owner = self.tasks.assigned[release_id]
            self.resources.release('handoff_window', owner, release_id, self.tick)
            self.events.append({'tick': self.tick, 'kind': 'handoff_window_release', 'task_id': release_id,
                                'robot_id': owner})
        if task.kind == 'stair_transport':
            self.resources.release('stair_mutex', robot.spec.robot_id, task_id, self.tick)
            self.events.append({'tick': self.tick, 'kind': 'stair_exit_release', 'task_id': task_id,
                                'robot_id': robot.spec.robot_id})
        if trigger and not self.availability_events:
            choices = [s for s in self.robot_states.values() if not s.load_id and s.status == 'idle' and 'handoff_receive' in s.active_capabilities]
            if choices:
                self.mark('cp_fault_pre_event')
                victim = sorted(choices, key=lambda s: s.spec.robot_id)[0]
                removed = victim.active_capabilities & {'handoff_receive'}
                victim.active_capabilities -= removed
                self.lost_robots.add(victim.spec.robot_id)
                affected = [t.task_id for t in self.episode.tasks if self.tasks.status[t.task_id] != 'completed']
                event = {'tick': self.tick, 'kind': 'availability_loss', 'robot_id': victim.spec.robot_id,
                         'lost_function': 'handoff_receive', 'removed_capabilities': sorted(removed),
                         'trigger_task': self.config['availability_loss_task'], 'persistent': True,
                         'affected_work': affected, 'pre_event_digest': _digest(self.snapshot())}
                self.availability_events.append(event); self.events.append(event)
                self.send_message('evaluator', 'coordinator', 'availability_loss', event)
                # Retask the standing service obligation; the disabled robot
                # retains navigation and can cover while the former monitor works.
                if self.config['tier'] >= 3:
                    owner = self.tasks.assigned.get('service_cover')
                    if owner:
                        old = self.robot_states[owner]
                        if old.status == 'hold':
                            old.status = 'idle'; old.hold_until_tasks = ()
                    task_id = '__service_replacement'
                    task = TaskSpec(task_id, 'service_cover', (self.config['service_point'],),
                        required_capabilities=('navigation',), hold_until_prerequisites=('service_end',),
                        report_required=True, required_for_success=True)
                    self.tasks.specs[task_id] = task
                    self.tasks.status[task_id] = 'pending'; self.tasks.remaining[task_id] = list(task.targets)
                self.reallocation_count += 1

    def mark(self, label):
        if any(cp['label'] == label for cp in self.checkpoints):
            return
        self.checkpoints.append({'label': label, **self.checkpoint(metadata={'scenario': 'bar', 'phase': label})})

    def _milestones(self):
        done = set(self.tasks.completed_tick)
        required = [t for t in self.episode.tasks if t.required_for_success]
        if all(t.task_id in done for t in required if t.task_id.endswith('_acquire')):
            self.mark('cp_01_material_ready')
        if any(t.task_id in done for t in required if t.task_id.endswith('_prepare')):
            self.mark('cp_02_pre_handoff')
        if all(t.task_id in done for t in required if t.task_id.endswith('_accept')):
            self.mark('cp_03_handoff_complete')
        if all(t.task_id in done for t in required if t.task_id.endswith('_place')):
            self.mark('cp_04_delivery_complete')
        if self.success:
            self.mark('cp_05_closeout')

    def checkpoint(self, *args, **kwargs):
        receipt = super().checkpoint(*args, **kwargs)
        receipt.update(contract_digest=self.config['contract_digest'],
                       skill_provider_version='2.0.0', evidence_scope='logical_graph_diagnostic')
        return receipt

    def fresh_branch(self, checkpoint, *, algorithm):
        """Evaluator restart with fresh submitted state; no private-state restore.

        Public observation/history is reconstructed from restored evaluator
        events. This local branch does not certify process isolation or paper
        eligibility. D2 uses cp_00; D4 uses its registered pre-event boundary.
        """
        if checkpoint.get('contract_digest') != self.config['contract_digest']:
            raise ValueError('checkpoint task/roster/condition contract mismatch')
        if checkpoint['digest'] != _digest(checkpoint['snapshot']):
            raise ValueError('checkpoint digest mismatch')
        if checkpoint['snapshot']['transport']['state']['condition'] != self.episode.communication_condition:
            raise ValueError('checkpoint communication condition mismatch')
        class Bootstrap:
            uses_distance = False
            def initialize(self, observation): pass
            def decide(self, observation): return {}
            def snapshot(self): return {}
            def restore(self, state): pass
        clone = type(self)(self.episode, algorithm=Bootstrap(), config=self.config)
        state = deepcopy(checkpoint['snapshot'])
        state['algorithm'] = clone.snapshot()['algorithm']
        clone.restore(state)
        clone.algorithm = algorithm
        clone.algorithm.initialize(clone._observation())
        clone._initial_snapshot = clone.snapshot()
        clone.checkpoints = []
        return clone

    def fork(self, checkpoint=None):
        clone = type(self)(self.episode, algorithm=deepcopy(self.algorithm), config=self.config)
        clone.restore((checkpoint or self.snapshot()).get('snapshot', checkpoint or self.snapshot()))
        clone.checkpoints = []
        return clone

    def metrics(self):
        result = super().metrics()
        result.update(communication_capacity_bits_per_tick=self.config['communication_capacity'],
            D1_eligible=False, D2_eligible=False, D3_eligible=False, D4_eligible=False, D5_eligible=False, D6_eligible=False,
            object_state=deepcopy(self.object_state), logical_objects=self.logical.capture_state(),
            energy_used=dict(self.energy_used), service_samples=self.service_samples,
            service_covered=self.service_covered, service_coverage=(self.service_covered / self.service_samples if self.service_samples else None),
            max_service_gap_s=self.max_service_gap * CONTROL_DT_S,
            charge_services=sum(e['kind'] == 'battery_full' for e in self.events),
            service_trace=deepcopy(self.service_trace), joint_evidence=deepcopy(self.joint_evidence),
            resource_state=self.resources.snapshot(), resource_peak_occupancy=dict(self.resource_peaks), inventory=dict(self.inventory),
            distance_m={rid: r.distance_m for rid, r in self.robot_states.items()})
        return result
