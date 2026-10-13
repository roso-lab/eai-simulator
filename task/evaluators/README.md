# Physical evaluators staging note

The public evaluator boundary is `benchmark.adapters.EvidenceEvaluator` and
the typed result is `benchmark.evidence.EvaluationReport`. Keep this directory
as a migration marker; do not create a competing verdict schema here.

Evaluator 只读取 EAI runtime 产生的物理证据、事件和 milestone，并返回可审计的
verdict。计划、assignment 和进程退出码不能被当作物理成功。

`ExecutionUpdate.dependency_outcome=completed` 也只是 DAG scheduler 解锁事件，
不构成物理成功。executor 缺失时 runner 不调用 evaluator，并固定返回
`N/A / physical_evidence_required`；executor 存在时 evaluator 仍需独立核验当前
session、task identity 和 predicate provenance，不能仅根据 scheduler state 判定成功。
