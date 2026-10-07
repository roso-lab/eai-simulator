# FMC-ATA → EAI adapter

The isolated official source is a Java FisherMarketSimulator. This Python
boundary keeps its experimentally relevant semantics visible: capability-aware
buyers, divisible workload fractions, market prices, task-arrival triggers, and
deterministic delay/loss events. EAI translates fractional allocations to
typed skills only when the selected skill explicitly supports divisibility;
physical execution and evidence remain EAI-owned.
# FMC-ATA → EAI

The adapter preserves Fisher-market prices, asynchronous communication, and
fractional allocation records. Fire Rescue's red/green/blue/yellow tasks are
physical skills with no declared divisible workload, so the Fire Rescue
callback intentionally reports an explicit N/A condition. This prevents a
fractional market share from being mistaken for an executable robot action.
Use `FireRescueCoordinationBridge` with an explicit decomposition only for a
separately registered decomposable task condition.
