"""Independent logical D1 probes for subway; no physical qualification."""
import json
from pathlib import Path
from .scenarios.subway import SubwayScenarioAdapter

DEFAULT_CASES=Path(__file__).resolve().parent/'tasks/subway/subway_d1_cases.v1.json'

def subway_d1_certificates(adapter=None, cases_path=DEFAULT_CASES):
    adapter=adapter or SubwayScenarioAdapter(); policy=json.loads(Path(cases_path).read_text()); graph=adapter.semantic_graph(); m=adapter.manifest()
    out=[]
    for case in policy['cases']:
        req=set(case.get('required_capabilities',()))
        eligible=[r['robot_id'] for r in m['robot_roster'] if r['domain']==case.get('required_domain') and req.issubset(r['capabilities'])]
        proof={'case_id':case['case_id'],'proof_type':case['proof_type'],'required_capabilities':sorted(req),'eligible_robot_ids':eligible,'map_revision':m['semantic_map']['revision']}
        if case['proof_type']=='domain_route_empty':
            try: graph.resolve(case['target'],case['required_domain']); proof['route_candidates']=list(graph.resolve(case['target'],case['required_domain']))
            except ValueError: proof['route_candidates']=[]
        if case['proof_type']=='restricted_reachability': proof['restriction']=case['restriction']
        out.append({'case_id':case['case_id'],'status':'team-state-infeasible' if not eligible and case['proof_type']!='domain_route_empty' else 'feasibility-unverified','proof':proof,'not_deployable':not eligible,'safe_stop':'no_dispatch_and_hold_at_assembly_point','physical_qualified':False,'paper_score_eligible':False})
    return tuple(out)

if __name__=='__main__': print(json.dumps({'schema':'eai.subway.d1.audit.v1','certificates':subway_d1_certificates()},ensure_ascii=False,indent=2))
