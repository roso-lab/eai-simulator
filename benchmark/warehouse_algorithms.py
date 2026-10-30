from __future__ import annotations
class _WarehouseBase:
    uses_distance=True
    def initialize(self, observation): self.calls=0
    def decide(self, observation):
        available=observation.get('available_robots',{}); used=set(); out={}
        goals=sorted(observation.get('pending_goals',()), key=lambda g:(len(g.get('eligible_robots',())),str(g.get('task_id',''))))
        for goal in goals:
            choices=[rid for rid in goal.get('eligible_robots',()) if rid in available and rid not in used]
            if choices:
                rid=min(choices,key=lambda r:(float(available[r].get('distance',0)),r)); out[rid]=goal['goal_key']; used.add(rid)
        self.calls+=1; return out
    def snapshot(self): return {'calls':self.calls}
    def restore(self,snapshot): self.calls=int(snapshot.get('calls',0))
class EmosWarehouseAlgorithm(_WarehouseBase): algorithm_id='EMOS-warehouse-order-pick-v1'
class TeamWeaverWarehouseAlgorithm(_WarehouseBase): algorithm_id='TeamWeaver-warehouse-allocation-v1'
