"""Diagnostic feasibility witnesses. These are not native EMOS/TeamWeaver runs."""
from benchmark.living_house_algorithms import LivingHouseGoalAlgorithm


class BarGoalAlgorithm(LivingHouseGoalAlgorithm):
    algorithm_id = 'bar_contract_witness_v2'

    def decide(self, observation):
        goals = observation.get('pending_goals', ())
        available = observation.get('available_robots', {})
        used = set(); result = {}
        for goal in sorted(goals, key=lambda g: (0 if g.get('kind') == 'service_cover' else 1,
                0 if g.get('object_action') == 'handoff_receive' else 1, len(g.get('eligible_robots', ())), g['task_id'])):
            if goal.get('kind') == 'material_acquire' and len(available) - len(used) < 2:
                continue
            choices = set(goal.get('eligible_robots', available)) & set(goal.get('reachable_robots', available))
            choices &= set(available)
            choices -= used
            if choices:
                rid = min(choices, key=lambda r: (len(available[r].get('active_capabilities', ())) if goal.get('kind') == 'service_cover' else 0, available[r].get('distance', 0), r))
                result[rid] = goal['goal_key']; used.add(rid)
        return result


# Compatibility names retained, with explicit provenance in matrix output.
class EmosBarAlgorithm(BarGoalAlgorithm):
    algorithm_id = 'emos_bar_diagnostic_witness_v2'


class TeamWeaverBarAlgorithm(BarGoalAlgorithm):
    algorithm_id = 'teamweaver_bar_diagnostic_witness_v2'
