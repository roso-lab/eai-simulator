# Versioned schemas

[`branch_tool.v2.json`](branch_tool.v2.json) records the stable branch envelope,
identity fields, execution scopes, and required append-only ledger names. High
frequency state can be extended by a plugin, but lifecycle and identity rows
must retain the fields checked by `validate`.
