# BT client review validation — 2026-10-28

Only native_adapter.py, tests/test_native_adapter.py and tests/test_dag_native.py were edited. Endpoint/shared/upstream were not edited by this task.

Receipt validation binds mission_id, canonical task_contract_sha256, graph_mode/native_tree, source hashes and assignment task/robot/bid eligibility. Single feedback and batch feedback both validate the returned receipt.

Input validation rejects empty graphs, unknown supplied bid IDs, and runtime/package robot capability roster differences (robot IDs, capabilities, active flags; order may differ). Source and contract fixtures use calculated SHA-256 digests; batch errors assert specific exception types and messages.

## Preserved results

- `review_green_20261028T172619_842698`: 76 tests, 0 failures, 0 errors, 0 skipped; see pytest.log and pytest.xml.
- `review_live_20261028T172658_067066`: 68 tests, 0 failures, 0 errors, 0 skipped; see pytest.log and pytest.xml.
- `review_red_20261028T172550_433489`: 61 tests, 20 failures, 0 errors, 0 skipped; see pytest.log and pytest.xml.
- `run_20261028T172354_579280`: 46 tests, 0 failures, 0 errors, 0 skipped; see pytest.log and pytest.xml.

The review_red run intentionally exposed 20 missing client checks before fixing them. The first live run passed all 46 then-existing BT tests. After the fix, ordinary BT + runner tests passed 76 tests (7 live deselected); the final complete BT suite passed 68 tests including all 7 live tests.

All 7 final live receipts match the three upstream source hashes. Post-test health reports no active missions. No endpoint logic failure was observed.

## Endpoint provenance

The running endpoint adapter differs from the current local endpoint file. Tests validate the running revision below; no claim is made that the local endpoint revision was exercised.

Running endpoint SHA-256: `08f5ed366c55e1d7a249c93e7020b0e787f2f2d1a29606d99251dc0f2af78502`

Local endpoint SHA-256: `18b0e354428ab7687a7b2d8a21a9f1e202be1144d2c939ecf804bddb03b662f7`

Evidence is native ROS protocol and behavior-tree feedback only. Protocol acknowledgements do not establish physical task success.
