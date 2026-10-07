# Hybrid SUSD MRTA → EAI

This adapter exposes the ICRA 2023 Hybrid SUSD based task allocation method
through the EAI typed coordination boundary. It follows the public MATLAB
pipeline: a market pass creates a feasible assignment, then a derivative free
SUSD style neighborhood search improves its utility. The implementation is a
dependency light Python interface equivalent; it does not claim byte identical
MATLAB execution.

Use `algorithm.system.coordination.hybrid_susd.fire_rescue_policy:assign` with the task-layer
runner. Z1 execution, navigation, manipulation, evaluator milestones and
replay remain owned by EAI. If execution times out, the adapter can rerun the
open tasks after a member failure or delay. The paper's native failure repair
is reported as `N/A`; the ledger labels this result as adapter level
reallocation.
