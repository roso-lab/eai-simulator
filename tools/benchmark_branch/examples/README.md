# Examples and dependencies

- `dummy_32.json`: standard-library protocol test; 32 slots × 4 arms = 128 logical branches.
- `dummy_matrix.json`: two scene names, two conditions and two repeats to show Cartesian plan expansion. Names are placeholders; no Isaac scene is loaded.
- `modern_house.json`, `warehouse.json`: synthetic adapter configuration for the upstream task plugin.
- `modern_house_measured.json`, `warehouse_measured.json`: one-scene measured adapter configurations.
- `modern_house_shared_measured.json`, `warehouse_shared_measured.json`: one Isaac session per scene with 32 vector slots, supplied by the upstream plugin.
- `plugin_template.py`: adapter skeleton.

The Modern House and Warehouse JSON files reference `demo.benchmark.branch_plugins`
and scenario paths that are deliberately outside this tool-only publication. Run
them only from a checkout containing that package, assets, Isaac/Lab runtime and
its own GPU/device configuration. The tool does not download, copy or publish
those scene files.
