"""Robot-independent world-XYZ pick/place request compilation."""
from __future__ import annotations
import hashlib
import json
from algorithm.skills.contracts import SkillCommand, SkillKind, SkillPlan
from algorithm.skills.simulator_schema import validate_simulator_skill_request

SKILL_ID = 'eai.manipulation.pick_place'


class PickPlaceProvider:
    skill_id = SKILL_ID
    kind = SkillKind.MANIPULATION
    provider_id = 'pick_place.provider'
    provider_version = '1.0'

    def plan(self, request, *, observation=None):
        try:
            validate_simulator_skill_request(request)
            if request.skill_id != self.skill_id:
                raise ValueError('incorrect pick/place skill ID')
            if any(v is not None for v in (request.target_ref, request.target_pose, request.timing, request.budget)):
                raise ValueError('pick/place uses its world-XYZ parameters only')
            if any(value is None for value in request.parameters.values()):
                raise ValueError('pick/place parameters cannot be null')
            if request.parameters.get('frame', 'world') != 'world':
                raise ValueError('pick/place frame must be world')
        except (TypeError, ValueError) as exc:
            return SkillPlan(request.request_id, request.skill_id, request.robot_id, status='invalid_request', metadata={'reason': str(exc)})
        digest = hashlib.sha256(json.dumps(request.to_mapping(), sort_keys=True, allow_nan=False).encode()).hexdigest()
        return SkillPlan(request.request_id, request.skill_id, request.robot_id,
            (SkillCommand('pick_place', {**request.parameters, 'frame': 'world'}),), metadata={
                'schema_version': request.skill_version, 'provider_id': self.provider_id,
                'provider_version': self.provider_version, 'request_digest': digest,
                'command_route': 'task.executors.manipulation.ManipulationSkillRuntime',
                'physical_outcome': 'external_evaluator_required'})
