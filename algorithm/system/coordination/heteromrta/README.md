# HeteroMRTA → EAI

HeteroMRTA (IEEE RA-L 2025) publishes an AttentionNet/REINFORCE checkpoint
and trains on 3–5 robot species, three agents per species, 15–50 tasks and
five-dimensional traits.  Fire Rescue has four robots and four indivisible
skills, so padding is exposed only as an auditable native-input descriptor.

The EAI physical track uses the scenario's declared default assignment and
records `allocation_provider=fixed_default_assignment;heteromrta_native_decision_N/A`.
Run the official checkpoint on its native test set for a separate decision-only
result.  No EAI retraining or cross-domain checkpoint success is claimed.
