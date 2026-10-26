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
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        value: "", textContent: "", disabled: false,
        classList: { toggle(name, value) { this[name] = value; } },
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
    write = async (path, body) => { calls.push({path, body}); return null; };
    globalThis.editor = { state, els, calls, renderPointSettings, updateToolUI,
      renderInspector, applyInspector, createNode, createSemanticPoint, syncPointSettings,
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

test("aerial and semantic point settings and creation remain independent", async () => {
  const api = editor();
  api.state.activeLayer = api.state.activeDomain = "aerial";
  api.renderPointSettings(); api.updateToolUI();
  assert.equal(api.els.newPointZ.value, "7");
  assert.equal(api.els.newPointModeGroup.classList.hidden, false);
  await api.createNode([1, 2]);
  let body = plain(api.calls.at(-1).body);
  assert.deepEqual(body.position_xyz, [1, 2, 7]);
  assert.equal(body.edit_mode, "2.5d");
  assert.ok(!Object.hasOwn(body, "level_id"));
  api.state.activeLayer = "semantic_points";
  api.state.server.editor_tool = "add-semantic-point";
  api.renderPointSettings(); api.updateToolUI();
  assert.equal(api.els.newPointZ.value, "8");
  await api.createSemanticPoint([1, 2]);
  body = plain(api.calls.at(-1).body);
  assert.deepEqual(body.position_xyz, [1, 2, 8]);
  assert.equal(body.edit_mode, "3d");
  api.state.activeLayer = api.state.activeDomain = "ground";
  delete api.state.server.new_point_settings.ground;
  api.renderPointSettings();
  assert.equal(api.els.newPointZ.value, "3");
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
  assert.match(html, /点击“连接导航点”后，在地图上逐个点选/);
  assert.match(html, /id="pickNavigationBtn"[^>]*>连接导航点/);
  assert.match(app, /连接模式：点击地面或空中导航节点加入\/移除绑定/);
  assert.match(app, /bindingKeys\.has\(`/);
  assert.match(app, /bound \? "#ffd166"/);
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
