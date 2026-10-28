// Exercise the editor's actual handlers with a small DOM/network stand-in.
// Run with: node --test tools/validation/test_topology_ground_height_web.cjs
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

function editor() {
  const elements = new Map();
  const timers = new Map();
  let nextTimer = 0;
  const document = {
    activeElement: null,
    createElement() { return {
      value: "", textContent: "", dataset: {}, classList: { toggle() {} },
      append() {}, appendChild() {}, addEventListener() {}, querySelector: () => ({ textContent: "" })
    }; },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        value: "", textContent: "", disabled: false,
        classList: { toggle(name, value) { this[name] = value; } },
        replaceChildren() { this.children = []; },
        addEventListener() {},
        appendChild(child) { (this.children ||= []).push(child); },
        getBoundingClientRect: () => ({left: 0, top: 0, width: 400, height: 400}),
        querySelectorAll: () => []
      });
      return elements.get(id);
    }
  };
  const sandbox = {
    document, timers,
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); }
  };
  const source = readFileSync(join(__dirname, "../topology_authoring/web/app.js"), "utf8");
  assert.ok(source.includes("  init();"));
  vm.runInNewContext(source.replace("  init();", `
    const calls = [];
    let reply = null;
    write = async (path, body) => {
      calls.push({path, body});
      if (reply) { const fresh = reply; reply = null; acceptState(fresh, {preserveSelection: true}); return fresh; }
      return null;
    };
    draw = () => {};
    renderAll = () => renderInspector();
    globalThis.editor = { state, els, calls, renderPointSettings, updateToolUI,
      renderInspector, applyInspector, createNode, syncPointSettings, startAnnotation, saveAnnotation, cancelAnnotation, cancelNavigationBindings,
      toggleNavigationPick, acceptState, onPointerDown, worldToScreen, hitTest,
      respondWith: (value) => { reply = value; },
      renderPending, showError, clearError, notice, timers, roadEnd, adjustRoadEndHeight,
      adjustAerialRoadStartHeight, toggleNavigationBindingFromMap,
      applyNavigationBindings };
  `), sandbox);
  const api = sandbox.editor;
  api.document = document;
  api.state.server = {
    editor_tool: "add-node", active_domain: "ground", active_layer: "ground",
    revision: 1, csrf_token: "token", scene_key: "scene", scene_generation: 0, context_generation: 0,
    levels: [{ id: "upper", support_z_m: 3 }],
    new_point_settings: { ground: { z: 3 }, aerial: { z: 7, edit_mode: "2.5d" },
      semantic_points: { z: 8, edit_mode: "3d" } },
    documents: { ground: { nodes: [{ id: "g1", position_xyz: [1, 2, 4.5], level_id: "upper" }] } }
  };
  api.state.currentLevel = "upper";
  return api;
}

const plain = (value) => JSON.parse(JSON.stringify(value));

test("ground toolbar defaults to floor height and creates custom Z without an edit mode", async () => {
  const api = editor();
  api.renderPointSettings(); api.updateToolUI();
  assert.equal(api.els.newPointZ.value, "3");
  assert.equal(api.els.newPointZGroup.classList.hidden, false);
  assert.equal(api.els.newPointModeGroup.classList.hidden, true);
  await api.createNode([1, 2]);
  assert.deepEqual(plain(api.calls.at(-1).body.position_xyz), [1, 2, 3]);
  api.els.newPointZ.value = "4.5";
  api.syncPointSettings();
  assert.deepEqual(plain(api.calls.at(-1).body.new_point_settings), { ground: { z: 4.5 } });
  await api.createNode([1, 2]);
  const body = plain(api.calls.at(-1).body);
  assert.deepEqual(body.position_xyz, [1, 2, 4.5]);
  assert.equal(body.level_id, "upper");
  assert.ok(!Object.hasOwn(body, "edit_mode"));
  api.els.newPointZ.value = "Infinity";
  await assert.rejects(api.createNode([1, 2]));
});

test("ground inspector accepts height while retaining floor identity", async () => {
  const api = editor();
  api.state.selection = { type: "node", id: "g1", domain: "ground" };
  api.renderInspector();
  assert.equal(api.els.fieldZ.value, "4.5");
  assert.equal(api.els.fieldZ.disabled, false);
  assert.equal(api.els.editModeGroup.classList.hidden, true);
  api.els.fieldZ.value = "6";
  await api.applyInspector({ preventDefault() {} });
  assert.deepEqual(plain(api.calls.at(-1).body.position_xyz), [1, 2, 6]);
  assert.equal(api.calls.at(-1).body.level_id, "upper");
});

test("remote floor switch resets ground height even while the input has focus", () => {
  const api = editor();
  api.renderPointSettings();
  api.document.activeElement = api.els.newPointZ;
  api.els.newPointZ.value = "4.5";
  api.renderPointSettings();
  assert.equal(api.els.newPointZ.value, "4.5");
  api.state.currentLevel = "lower";
  api.state.server.new_point_settings.ground.z = 0;
  api.renderPointSettings();
  assert.equal(api.els.newPointZ.value, "0");
  assert.equal(api.state.server.new_point_settings.aerial.z, 7);
  assert.equal(api.state.server.new_point_settings.semantic_points.z, 8);
});

test("label creation needs no node and submits no spatial or binding fields", async () => {
  const api = editor();
  api.state.activeLayer = api.state.server.active_layer = "semantic_points";
  api.state.server.editor_tool = "add-semantic-point";
  api.startAnnotation();
  assert.equal(api.state.selection, null);
  api.els.annotationLabel.value = "Desk";
  await api.saveAnnotation({ preventDefault() {} });
  const body = api.calls.at(-1).body;
  assert.equal(body.operation, "add_semantic_point");
  assert.equal(body.label, "Desk");
  for (const key of ["position_xyz", "node_id", "annotation_node_id", "navigation_bindings"])
    assert.ok(!(key in body));
  assert.equal(api.state.activeLayer, "semantic_points");
  assert.ok(api.state.annotationDraft); // Failed/no response preserves the form.
  api.cancelAnnotation();
  assert.equal(api.state.annotationDraft, null);
  assert.equal(api.calls.length, 1);
});

test("label creation is unavailable outside semantic management", () => {
  const api = editor();
  api.state.selection = { type: "node", id: "g1", domain: "ground" };
  api.startAnnotation();
  assert.equal(api.state.annotationDraft, null);
  api.renderInspector();
  assert.equal(api.els.labelCreation.classList.hidden, true);
});

test("binding entry is visible and finish preserves draft while discard restores saved bindings", async () => {
  const api = editor();
  api.state.activeLayer = api.state.server.active_layer = "semantic_points";
  api.state.server.editor_tool = "add-semantic-point";
  api.state.server.semantic_points = [{ id: "desk", label: "Desk", kind: "furniture",
    navigation_bindings: [{ domain: "aerial", node_id: "a1" }] }];
  api.state.selection = { type: "semantic_point", id: "desk" };
  api.renderInspector();
  assert.equal(api.els.pickNavigationBtn.classList.hidden, false);
  api.toggleNavigationPick();
  assert.equal(api.state.bindingPickMode, true);
  api.toggleNavigationBindingFromMap({type: "node", id: "g1", domain: "ground"});
  assert.equal(api.state.navigationBindingDraft.bindings.length, 2);
  api.toggleNavigationPick();
  assert.equal(api.state.bindingPickMode, false);
  assert.equal(api.state.navigationBindingDraft.bindings.length, 2);
  api.cancelNavigationBindings();
  assert.deepEqual(plain(api.state.navigationBindingDraft.bindings), [{ domain: "aerial", node_id: "a1" }]);
  assert.equal(api.state.selection.id, "desk");
  assert.equal(api.state.activeLayer, "semantic_points");
  assert.ok(api.calls.every(call => call.path !== "/api/edit"));
});

test("aerial road uses the configured start Z and adjusts only its endpoint by 0.1m", () => {
  const api = editor();
  api.state.activeLayer = api.state.activeDomain = "aerial";
  api.state.server.editor_tool = "build-aerial-road";
  api.state.roadDraft = { domain: "aerial", start: [0, 0, 7], endZ: 7 };
  assert.deepEqual(plain(api.roadEnd([1, 2, 99], api.state.roadDraft.start)), [1, 2, 7]);
  assert.equal(api.adjustRoadEndHeight(.1), true);
  assert.deepEqual(plain(api.roadEnd([1, 2, 99], api.state.roadDraft.start)), [1, 2, 7.1]);
  assert.deepEqual(plain(api.state.roadDraft.start), [0, 0, 7]);
  assert.equal(api.els.roadHeightStatus.textContent, "终点 Z 7.10 m");
  api.state.roadDraft.domain = "ground";
  assert.equal(api.adjustRoadEndHeight(.1), false);
  assert.equal(api.state.roadDraft.endZ, 7.1);
});

test("PageUp and PageDown before aerial road drag update the shared new-point Z", () => {
  const api = editor();
  api.state.activeLayer = api.state.activeDomain = "aerial";
  api.state.server.active_layer = api.state.server.active_domain = "aerial";
  api.state.server.editor_tool = "build-aerial-road";
  api.els.newPointZ.value = "7";
  api.els.newPointMode.value = "3d";
  assert.equal(api.adjustAerialRoadStartHeight(.1), true);
  assert.equal(api.els.newPointZ.value, "7.1");
  assert.deepEqual(plain(api.calls.at(-1).body.new_point_settings), { aerial: { z: 7.1, edit_mode: "3d" } });
  assert.equal(api.adjustAerialRoadStartHeight(-.1), true);
  assert.equal(api.els.newPointZ.value, "7");
  api.state.server.editor_tool = "add-node";
  assert.equal(api.adjustAerialRoadStartHeight(.1), false);
  assert.equal(api.els.newPointZ.value, "7");
});

test("road and delete tools are available independently for the aerial layer", () => {
  const html = readFileSync(join(__dirname, "../topology_authoring/web/index.html"), "utf8");
  const app = readFileSync(join(__dirname, "../topology_authoring/web/app.js"), "utf8");
  assert.match(html, /data-mode="build-aerial-road"/);
  assert.match(html, /data-mode="delete-aerial-edge"/);
  assert.match(app, /event\.key === "PageUp" \|\| event\.key === "PageDown"/);
  assert.match(app, /mode === "build-aerial-road" \|\| mode === "delete-aerial-edge" \? "aerial"/);
});

test("semantic navigation map picking edits a draft across both graph domains", async () => {
  const api = editor();
  api.state.server.active_layer = "semantic_points";
  api.state.activeLayer = "semantic_points";
  api.state.server.semantic_points = [{ id: "s1", position_xyz: [0, 0, 0], navigation_bindings: [
    { domain: "ground", node_id: "g1" }
  ] }];
  api.state.selection = { type: "semantic_point", id: "s1" };
  api.state.navigationBindingDraft = { id: "s1", bindings: [{ domain: "ground", node_id: "g1" }] };
  assert.equal(api.toggleNavigationBindingFromMap({ type: "node", domain: "aerial", id: "n2" }), false);
  api.state.bindingPickMode = true;
  assert.equal(api.toggleNavigationBindingFromMap({ type: "node", domain: "aerial", id: "n2" }), true);
  assert.deepEqual(plain(api.state.navigationBindingDraft.bindings), [
    { domain: "aerial", node_id: "n2" }, { domain: "ground", node_id: "g1" }
  ]);
  assert.equal(api.calls.at(-1).path, "/api/editor/context");
  assert.deepEqual(plain(api.calls.at(-1).body.binding_target_draft), plain(api.state.navigationBindingDraft.bindings));
  assert.equal(api.toggleNavigationBindingFromMap({ type: "node", domain: "ground", id: "g1" }), true);
  assert.deepEqual(plain(api.state.navigationBindingDraft.bindings), [{ domain: "aerial", node_id: "n2" }]);
  assert.equal(api.calls.at(-1).body.binding_target_id, "s1");
  api.state.bindingPickMode = false;
  assert.deepEqual(plain(api.state.navigationBindingDraft.bindings), [{ domain: "aerial", node_id: "n2" }]);
  await api.applyNavigationBindings();
  assert.equal(api.calls.at(-1).path, "/api/edit");
  assert.equal(api.calls.at(-1).body.operation, "set_semantic_point_navigation_bindings");
  assert.deepEqual(plain(api.calls.at(-1).body.bindings), [{ domain: "aerial", node_id: "n2" }]);
});

test("semantic binding controls and copy describe explicit map connection mode", () => {
  const html = readFileSync(join(__dirname, "../topology_authoring/web/index.html"), "utf8");
  const app = readFileSync(join(__dirname, "../topology_authoring/web/app.js"), "utf8");
  assert.match(html, /先创建标签/);
  assert.match(html, /id="pickNavigationBtn"[^>]*>连接导航点/);
  assert.match(app, /连接模式：点击地面或空中导航节点加入\/移除绑定/);
  assert.match(app, /bindingKeys\.has\(`/);
  assert.match(app, /annotated.has/);
});

test("semantic binding mode keeps the semantic layer active while exposing both navigation graphs", () => {
  const app = readFileSync(join(__dirname, "../topology_authoring/web/app.js"), "utf8");
  assert.match(app, /bindingNodeVisible/);
  assert.match(app, /state\.bindingPickMode \? bindingNodeVisible/);
  assert.match(app, /if \(!layerVisible\(domain\) && !state\.bindingPickMode\) continue/);
});

test("notification slot keeps fixed desktop and narrow-screen space", () => {
  const html = readFileSync(join(__dirname, "../topology_authoring/web/index.html"), "utf8");
  const css = readFileSync(join(__dirname, "../topology_authoring/web/style.css"), "utf8");
  const mobileCss = css.split("@media (max-width: 960px)")[1].split("@media (max-width: 560px)")[0];
  assert.match(html, /class="notification-slot"[\s\S]*id="errorBanner"[\s\S]*id="noticeBanner"[\s\S]*id="pendingDraftBanner"/);
  assert.match(css, /\.notification-slot\s*\{[^}]*height:\s*96px;[^}]*flex:\s*0 0 96px/s);
  assert.match(css, /\.banner\s*\{[^}]*min-height:\s*44px;\s*flex:\s*0 0 44px/s);
  assert.match(mobileCss, /\.notification-slot\s*\{\s*height:\s*132px;\s*min-height:\s*132px/s);
});

test("error and pending draft take priority over transient success notices", () => {
  const api = editor();
  api.renderPending();
  api.notice("操作完成");
  assert.equal(api.els.noticeBanner.classList.hidden, false);

  api.showError("保存失败");
  assert.equal(api.els.errorBanner.classList.hidden, false);
  assert.equal(api.els.noticeBanner.classList.hidden, true);

  api.state.pendingEdit = { uncertain: false, identity: { scene_key: "scene" }, layer: "ground" };
  api.renderPending();
  assert.equal(api.els.pendingDraftBanner.classList.hidden, false);
  assert.equal(api.els.errorBanner.classList.hidden, false);
  assert.equal(api.els.noticeBanner.classList.hidden, true);

  api.clearError();
  assert.equal(api.els.pendingDraftBanner.classList.hidden, false);
  assert.equal(api.els.noticeBanner.classList.hidden, true);
  api.state.pendingEdit = null;
  api.renderPending();
  assert.equal(api.els.noticeBanner.classList.hidden, false);
});


test("creation response selects the new label even with an old inspector draft", async () => {
  const api = editor();
  api.state.activeLayer = api.state.server.active_layer = "semantic_points";
  api.state.server.editor_tool = "add-semantic-point";
  api.state.server.active_level = "upper";
  api.state.server.semantic_points = [{id: "old", label: "Old", kind: "custom"}];
  api.state.selection = {type: "semantic_point", id: "old"};
  api.state.inspectorDraft = {selection: plain(api.state.selection), values: {fieldLabel: "unsaved"}};
  api.startAnnotation();
  api.els.annotationLabel.value = "New";
  const fresh = plain(api.state.server);
  fresh.revision += 1;
  fresh.semantic_points.push({id: "new", label: "New", kind: "custom"});
  fresh.web_selection = {kind: "semantic_point", id: "new"};
  api.respondWith(fresh);
  await api.saveAnnotation({preventDefault() {}});
  assert.equal(api.state.selection.id, "new");
  assert.equal(api.state.selectionItems[0].id, "new");
  assert.equal(api.state.annotationDraft, null);
  assert.equal(api.els.fieldLabel.value, "New");
});

test("actual canvas hit and pointer handlers bind both domains without selecting an edge", () => {
  const api = editor();
  api.state.activeLayer = api.state.server.active_layer = "semantic_points";
  api.state.server.editor_tool = "add-semantic-point";
  api.state.server.visible_layers = {ground: false, aerial: false};
  api.state.server.visible_types = {nodes: false};
  api.state.server.documents.aerial = {nodes: [{id: "a1", position_xyz: [-2, -2, 7]}], edges: []};
  api.state.server.documents.ground.edges = [{id: "e1", from: "g1", to: "g2", polyline_xyz: [[1, 2, 0], [5, 2, 0]]}];
  api.state.server.semantic_points = [{id: "s1", kind: "custom"}];
  api.state.selection = {type: "semantic_point", id: "s1"};
  api.renderInspector();
  const click = (point) => { const [clientX, clientY] = api.worldToScreen(point); api.onPointerDown({clientX, clientY, button: 0}); };
  click([1, 2]);
  assert.equal(api.state.navigationBindingDraft.bindings.length, 0);
  api.toggleNavigationPick();
  click([1, 2]); click([-2, -2]); click([3, 2]); click([9, 9]);
  assert.deepEqual(plain(api.state.navigationBindingDraft.bindings), [
    {domain: "aerial", node_id: "a1"}, {domain: "ground", node_id: "g1"}]);
  assert.equal(api.state.selection.id, "s1");
  assert.equal(api.state.activeLayer, "semantic_points");
  click([1, 2]);
  assert.deepEqual(plain(api.state.navigationBindingDraft.bindings), [{domain: "aerial", node_id: "a1"}]);
});

for (const change of [{active_level: "lower"}, {active_layer: "aerial", editor_tool: "add-node"},
                       {scene_generation: 3}, {editor_tool: "add-edge"}]) {
  test("context switch discards label binding draft: " + JSON.stringify(change), () => {
    const api = editor();
    api.state.activeLayer = api.state.server.active_layer = "semantic_points";
    api.state.server.editor_tool = "add-semantic-point";
    api.state.server.active_level = "upper";
    api.state.server.semantic_points = [{id: "s1", kind: "custom"}];
    api.state.selection = {type: "semantic_point", id: "s1"};
    api.state.navigationBindingDraft = {id: "s1", bindings: [{domain: "ground", node_id: "g1"}]};
    api.state.bindingPickMode = true;
    const fresh = {...plain(api.state.server), ...change, context_generation: 1, web_selection: null};
    api.acceptState(fresh);
    assert.equal(api.state.bindingPickMode, false);
    assert.equal(api.state.navigationBindingDraft, null);
  });
}
