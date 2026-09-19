"""Runtime evaluation of derived success predicates against episode state.

The pure half of the benchmark evaluator: callers feed entity positions
(sampled from the live scene at chosen steps) and this module produces
per-predicate booleans, a percent-complete score, and an optional predicate
trajectory. It never imports Isaac; the rollout owner decides when and how
positions are sampled.
"""

from .evaluation import PREDICATES, json_key
from .validation import ScenarioError


class PredicateEvaluator:
    """Evaluate a frozen success contract against sampled world state."""

    def __init__(self, data):
        from .evaluation import derive_success_predicates

        self.predicates = derive_success_predicates(data)
        self.data = data

    def evaluate(self, positions):
        """positions: {entity_id: (x, y)} sampled from the scene.

        Every object referenced by a predicate must be sampled this tick;
        a missing sample is a caller bug, not an unsatisfied predicate.
        """
        from .evaluation import evaluate_predicate

        verdict = {}
        for predicate in self.predicates:
            args = predicate["args"]
            refs = args if isinstance(args, list) else list(args)
            known_objects = {entity["id"] for entity in self.data["composition"]["entities"]}
            known_robots = {robot["id"] for robot in self.data["robots"]}
            for ref in refs:
                if not isinstance(ref, str):
                    continue  # e.g. all_at_zone carries a list of object refs
                if ref in positions:
                    continue
                if ref in known_objects or ref in known_robots:
                    raise ScenarioError(
                        f"runtime evaluation missing sampled position for '{ref}'")
            verdict[json_key(predicate)] = evaluate_predicate(
                self.data, predicate, positions=positions)
        return verdict

    def percent_complete(self, positions):
        verdict = self.evaluate(positions)
        if not verdict:
            return 0.0
        return sum(verdict.values()) / len(verdict) * 100.0

    def trajectory_step(self, step, positions):
        """One sample of the predicate time series (TaskEvaluationLog shape)."""
        return {"step": step, "satisfied": dict(self.evaluate(positions)),
                "percent": round(self.percent_complete(positions), 1)}


def _is_visitation(key):
    return key.startswith("robot_visited_zone(")


def summarize_trajectory(trajectory):
    """Aggregate a predicate trajectory into a final verdict report.

    Visitation predicates count as satisfied when they ever held; terminal
    (is_at_zone) predicates must hold at the final sample.
    """
    if not trajectory:
        return {"verdict": "no_samples", "percent": None}
    final = trajectory[-1]
    if not final["satisfied"]:
        # No derived predicates at all (e.g. unpinned navigate tasks); all([])
        # must not read as success.
        return {"verdict": "no_predicates", "percent": None,
                "first_satisfied_step": {}, "ever_satisfied": {}}
    ever = {}
    for sample in trajectory:
        for key, value in sample["satisfied"].items():
            ever[key] = ever.get(key, False) or value
    verdict_values = {
        key: (ever[key] if _is_visitation(key) else final["satisfied"][key])
        for key in final["satisfied"]
    }
    percent = round(sum(verdict_values.values()) / len(verdict_values) * 100.0, 1)
    return {
        "verdict": "success" if all(verdict_values.values()) else "failure",
        "percent": percent,
        "final_state": dict(final["satisfied"]),
        "first_satisfied_step": {
            key: next((s["step"] for s in trajectory if s["satisfied"].get(key)), None)
            for key in final["satisfied"]
        },
        "ever_satisfied": ever,
    }
