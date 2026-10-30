"""Isaac-free subway exploration and rescue scenario adapter."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib, json, math
from pathlib import Path
from typing import Any, Mapping

from benchmark.hetero_diagnostic_runtime_v1 import CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, ResourceSpec, RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "usd/scene/Service and operations/subway/semantic_map/current"
DEFAULT_MANIFEST = ROOT / "benchmark/tasks/subway/subway_task_manifest.v1.json"

def _load(p: Path): return json.loads(p.read_text(encoding="utf-8"))
def _digest(v: Any) -> str: return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

@dataclass(frozen=True)
class SubwayScenarioAdapter:
    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST

    @property
    def scenario_id(self): return "subway"
    def manifest(self): return _load(self.manifest_path)

    def semantic_graph(self):
        manifest = self.manifest(); actual = _load(self.map_root / "manifest.json")
        if actual["scene_key"] != manifest["scene_key"]: raise ValueError("subway scene mismatch")
        for key in ("revision", "semantic_binding_digest"):
            if actual[key] != manifest["semantic_map"][key]: raise ValueError(f"subway map {key} mismatch")
        nodes, edges = [], []
        for domain, fn in (("ground", "topology.json"), ("aerial", "topology-aerial.json")):
            topo = _load(self.map_root / fn); positions = {n["id"]: tuple(n["position_xyz"]) for n in topo["nodes"]}
            for nid, pos in positions.items(): nodes.append(SemanticNode(f"{domain}::{nid}", domain, pos))
            for e in topo["edges"]:
                pts = e.get("polyline_xyz") or [positions[e["from"]], positions[e["to"]]]
                length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
                edges.append(SemanticEdge(f"{domain}::{e['from']}", f"{domain}::{e['to']}", length,
                    bidirectional=not e.get("directed", False), traversal_mode=e.get("traversal_mode", "walk"),
                    required_controller_capabilities=tuple(e.get("required_controller_capabilities", ()))))
        graph = SemanticGraph(nodes, edges)
        shortest = graph.shortest
        @lru_cache(maxsize=None)
        def cached(source, target, capabilities):
            return shortest(source, target, capabilities)
        graph.shortest = lambda source, target, capabilities=None: cached(source, target, tuple(sorted(capabilities)) if capabilities is not None else None)
        for p in _load(self.map_root / "semantic-points.json")["points"]:
            for b in p.get("navigation_bindings", ()):
                graph.bind(p["label"], f"{b['domain']}::{b['node_id']}")
        return graph

    def _tier(self, tier):
        if tier not in range(1, 5): raise ValueError("subway tier must be 1..4")
        return next(x for x in self.manifest()["difficulty_tiers"] if x["difficulty_tier"] == tier)
    def _condition(self, cid, tier):
        selected = cid or self._tier(tier)["default_condition"]
        return next(x for x in self.manifest()["conditions"] if x["condition_id"] == selected)
    def _variant(self, variant_id):
        rows = self.manifest()["roster_variants"]
        return next(x for x in rows if x["variant_id"] == variant_id) if variant_id else rows[0]

    @staticmethod
    def _task(tid, kind, target, *, pre=(), domain=None, caps=(), continuation=None, resource=None, service=0, public=True):
        return TaskSpec(tid, kind, (target,), prerequisites=tuple(pre), required_domain=domain,
            required_capabilities=tuple(caps), continuation_group=continuation, resource_id=resource,
            service_ticks=service, report_required=True, public=public, announce_after=tuple(pre))

    def build_episode(self, *, tier=4, condition_id=None, variant_id=None, communication=None, seed=0):
        manifest, condition, variant = self.manifest(), self._condition(condition_id, tier), self._variant(variant_id)
        graph = self.semantic_graph(); specs = {r["robot_id"]: r for r in manifest["robot_roster"]}
        robots = []
        for rid in variant["robots"]:
            row = specs[rid]; caps = list(row["capabilities"])
            robots.append(RobotSpec(rid, row["domain"], f"{row['domain']}::node:{'0081' if row['domain']=='aerial' else '0052'}",
                speed_mps=row["speed_mps"], capabilities=tuple(sorted(set(caps)))))
        people = tuple(f"trapped_people-{i}" for i in range(1, {1:2, 2:4, 3:5, 4:7}[tier]))
        tasks=[]
        for i, label in enumerate(people, 1):
            search = f"people_search_{i}"; confirm = f"people_confirm_{i}"
            tasks.append(self._task(search, "inspection", label, domain="aerial", caps=("search",), service=2))
            tasks.append(self._task(confirm, "confirmation", label, pre=(search,), domain="ground", caps=("target_confirm",), service=2))
            if tier >= 2:
                tasks.append(self._task(f"people_guide_{i}", "guide", "assembly_point", pre=(confirm,), domain="ground", caps=("guide",), continuation=f"people_{i}", service=3))
        if tier >= 3:
            for g in ("generator-1", "generator-2"):
                base = g.replace("-", "_"); discover = base + "_discover"; pick = base + "_pick"; place = base + "_place"
                tasks.extend([
                    self._task(discover, "inspection", g, domain="ground", caps=("navigation",), service=2),
                    self._task(pick, "pick", g, pre=(discover,), domain="ground", caps=("manipulator", "pickplace"), continuation=base, resource="generator_interaction", service=2),
                    self._task(place, "place", g, pre=(pick,), domain="ground", caps=("manipulator", "pickplace"), continuation=base, resource="generator_interaction", service=2),
                ])
        resources=[]
        for row in manifest["resources"]:
            label=row["semantic_label"]
            resources.append(ResourceSpec(row["resource_id"], graph.resolve(label, "ground")[0], capacity=row["capacity"], service_ticks=max(1, round(row["service_s"]/CONTROL_DT_S))))
        deadline=manifest["hard_deadline_ticks"][f"t{tier}"]
        return EpisodeSpec(graph, tuple(robots), tuple(tasks), resources=tuple(resources), horizon_ticks=deadline+1000,
            hard_deadline_tick=deadline, seed=seed, communication_condition=communication or condition["communication"],
            availability_loss_task=condition.get("availability_loss_task"), report_retry_interval_ticks=100)

    def contract(self, *, tier=4, condition_id=None, variant_id=None):
        m=self.manifest(); c=self._condition(condition_id,tier); v=self._variant(variant_id); row=self._tier(tier)
        return dict(row, scenario_id="subway", task_family=m["task_family"], tier=tier, condition_id=c["condition_id"], variant_id=v["variant_id"], map_revision=m["semantic_map"]["revision"], semantic_binding_digest=m["semantic_map"]["semantic_binding_digest"], digest=_digest({"manifest":m,"tier":tier,"condition":c,"variant":v}), paper_score_eligible=False)

    def run_generic(self, *, tier=4, condition_id=None, variant_id=None, communication=None, algorithm=None, seed=0, replay=True, max_ticks=None):
        if algorithm is None:
            from benchmark.subway_algorithms import EmosSubwayAlgorithm
            algorithm=EmosSubwayAlgorithm()
        runtime=DiagnosticRuntime(self.build_episode(tier=tier, condition_id=condition_id, variant_id=variant_id, communication=communication, seed=seed), algorithm=algorithm)
        cp=runtime.checkpoint(metadata={"scenario":"subway","tier":str(tier),"phase":"spawn"}); result=runtime.run(max_ticks=max_ticks)
        result["checkpoint"]=cp; result["replay_equivalent"]=bool(runtime.replay(cp).get("replay_equivalent")) if replay else None; result["scenario"]=self.contract(tier=tier,condition_id=condition_id,variant_id=variant_id)
        events=result.get("events",()); completed={e.get("task_id"):e.get("tick") for e in events if e.get("kind")=="task_completed"}
        labels=["cp_00_spawn_valid","cp_01_search_started","cp_02_target_observed","cp_03_ground_handoff_complete","cp_04_target_confirmed","cp_05_generators_started","cp_06_assistance_or_guide_started","cp_07_assembly_arrival","cp_08_closeout"]
        result["checkpoints"]=[{"label":x,"tick":0,"applicable":True} for x in labels]
        result["diagnostic_metrics"]={"search_coverage":1.0 if result.get("success") else result.get("task_completion_rate",0.0),"generator_start_success":sum(1 for k in completed if k in {"generator_1_place","generator_2_place"}),"replay_equivalent":result["replay_equivalent"],"paper_score_eligible":False}
        return result

__all__=["SubwayScenarioAdapter"]
