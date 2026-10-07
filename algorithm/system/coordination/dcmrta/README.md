# DCMRTA → EAI

DCMRTA (ICRA 2024) publishes an official REINFORCE checkpoint, but its native
domain contains 10–20 agents, 20–50 tasks, five-dimensional requirements and
coalition tasks. Fire Rescue contains four robots and four indivisible skills.

The EAI condition therefore keeps two results separate:

1. native checkpoint evaluation on the official DCMRTA test set; and
2. a Fire Rescue full-flow track that uses the scenario's declared default
   mapping, then runs Z1 navigation, manipulation, feedback, timeout and
   replay. The latter is an execution adapter, not zero-shot checkpoint
   generalization. Padding is documented as `cross_domain_mismatch`.
