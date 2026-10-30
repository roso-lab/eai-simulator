"""Deterministic allocation witnesses for the subway logical track."""
from typing import Any, Mapping

class _Base:
    uses_distance=True
    def initialize(self, observation: Mapping[str,Any]): self.calls=0; self.provenance={"native_endpoint":False,"scope":"allocation_only"}
    def decide(self, observation: Mapping[str,Any]):
        available=observation.get("available_robots",{}); used=set(); out={}
        goals=sorted(observation.get("pending_goals",()), key=lambda g:(0 if g.get("kind") in {"inspection","confirmation"} else 1, len(g.get("eligible_robots",())), g.get("goal_key","")))
        for goal in goals:
            choices=[r for r in goal.get("eligible_robots",()) if r in available and r not in used]
            if choices:
                rid=min(choices,key=lambda r:(float(available[r].get("distance",0)),r)); out[rid]=goal["goal_key"]; used.add(rid)
        self.calls+=1; return out
    def snapshot(self): return {"calls":self.calls,"provenance":self.provenance}
    def restore(self,snapshot): self.calls=int(snapshot.get("calls",0)); self.provenance=dict(snapshot.get("provenance",{}))
class EmosSubwayAlgorithm(_Base): algorithm_id="EMOS-subway-search-rescue-diagnostic-v1"
class TeamWeaverSubwayAlgorithm(_Base): algorithm_id="TeamWeaver-subway-allocation-diagnostic-v1"
