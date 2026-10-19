# Semantic topology authoring acceptance — 2026-10-14

Implementation in `/home/pupil/HeteroCoord`, based on HEAD `a81e164f`, uncommitted.
Unrelated G1/manipulation changes were preserved. Scope is a reusable static
authoring/data contract and native Kit editor; physical robot traversal and
automatic symbolic-planner integration are separate work.

## Automated checks

Coordinator: **279 passed in 2.48s** with the prepared `env_isaaclab` interpreter.
Independent Sol review: **163 passed**, with all reproduced counterexamples
closed. The independent set overlaps the coordinator set; counts are not added.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest \
  --rootdir=/home/pupil/HeteroCoord -q -p no:cacheprovider \
  tools/validation/test_semantic_topology_authoring.py \
  tools/validation/test_topology_authoring.py \
  tools/validation/test_topology_route_contract.py \
  tools/validation/test_topology_authoring_ui.py \
  tools/validation/test_topology_multilevel_authoring.py \
  tools/validation/test_topology_surfaces.py \
  tools/validation/test_topology_pose_ui.py \
  algorithm/skills/tests/test_ground_navigation.py \
  algorithm/skills/tests/test_multilevel_navigation.py \
  algorithm/skills/tests/test_landmark_heading.py \
  algorithm/skills/tests/test_aerial_navigation.py \
  task/tests/test_ground_navigation_executor.py
```

Coverage includes old v1/v2 serialization, annotated v1 floor upgrade,
independent semantics versioning, finite geometry/reference/floor validation,
direction and capability checks, persistent reopen, atomic failure with history
preservation, real XYZ waypoint generation, and later tabletop pose insertion
without changing `route_graph_digest`. Full `product_digest` includes semantics.
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` avoids an unrelated ROS plugin missing `lark`;
no environment installation was needed.

## Native callback and render check

Synthetic input: `/tmp/semantic-house-20261014-02` (24 nodes, 23 edges,
two floor regions, three routes, one table annotation, no grasp pose initially).

```bash
DISPLAY=:1 PYTHONDONTWRITEBYTECODE=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.validation.semantic_topology_editor_smoke \
  --asset-root /tmp/semantic-house-20261014-02 \
  --output-root /home/pupil/Documents/Codex/agent-team/workspaces/semantic-topology-tool-20261014/artifacts/native-05
```

Isaac Sim 5.1, Quadro RTX 6000, GUI. **Final process exit: 0.** `result.json`:
44 nodes, 44 edges, three regions, four named routes, one entity, one interaction
pose, `source_unchanged=true`. The smoke invokes native UI callbacks to draw a
room and a 20-waypoint staircase, add a tabletop grasp pose, compare navigation
digest, save/generate/reload, filter floors and reload source. It does not inject
operating-system mouse events or run a robot controller.

Evidence root:
`/home/pupil/Documents/Codex/agent-team/workspaces/semantic-topology-tool-20261014/artifacts/`.
Final files: `native-05/result.json`, `native-05.log`,
`native-05/{overview,drawing-controls,semantic-records,semantic-record-selected}.png`.

The coordinator inspected actual final pixels. Drawing/record screenshots use
the editor's actual panel builders in an isolated native window, because changing
the main panel's scroll position did not yield the expected captured pixels.
They demonstrate readable controls and records; selected JSON shows ID, kind,
owner and floor, while lower XYZ/heading fields require scrolling. They do not
certify the main panel's full scrolling behavior.

ZCode GLM-5.3-Flash/max processed actual native-04 image attachments and confirmed
readable drawing controls, nine record entries, an initially empty JSON editor,
region prisms, height difference and a connecting route. Flash could not certify
individual step geometry or orange stair-edge visibility from those images;
the upper region is partly cropped, markers are small and default-light banners
obscure part of the overview. Native-05 selected JSON was inspected by the
coordinator only: the interrupted bridge no longer owned the old ZCode session,
so no final Flash observation is claimed.

## Failed attempts and remaining boundaries

- Native-01 exposed lazy initialization of a semantic model; fixed by eager
  model initialization. Native-02/03 callback checks then passed.
- Native-04 completed callbacks and rendered panels but exited **139** during
  garbage collection after extension shutdown. The smoke was adjusted to
  release the isolated window, bound panel builder and light references before
  Kit shutdown; native-05 exited 0. This is evidence for that scoped fix, not
  a general proof against native lifetime failures. Failed evidence is retained.
- USD reference-count/default-light warnings appeared; a later frame also
  displayed a `displayColor:indices` warning. No memory-leak or visual-color
  correctness conclusion is inferred from these warnings.
- Room regions are horizontal polygon prisms. Interaction records use a JSON
  editor with optional yaw; arbitrary task properties remain caller-owned.
- Generic authoring does not certify every scene's geometry. The USD adapter
  requires static, metric, Z-up triangulated collision support surfaces and
  checks support continuity, not complete swept collision or robot clearance.
- No physical stair ascent/descent, OS mouse delivery, automatic occupancy
  alignment, planner-world synchronization or grasp execution is claimed.

Independent handoff:
`/home/pupil/Documents/Codex/agent-team/workspaces/semantic-topology-tool-20261014/handoffs/acceptance-review.md`.
