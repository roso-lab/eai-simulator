"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const els = {};
  [
    "sceneSubtitle", "projectPickerWrap", "categoryPicker", "projectPicker", "sceneSyncStatus",
    "dirtyBadge", "revisionBadge", "undoBtn", "redoBtn", "saveBtn", "errorBanner", "errorText",
    "closeErrorBtn", "noticeBanner", "projectPath", "layerToggle", "referenceLevel", "floorList",
    "showGround", "showAerial", "showSemanticPoints", "showNodes", "showEdges", "showPointType",
    "recordCount", "recordSearch", "recordTabs", "recordList", "warningsSection", "warningsList",
    "modeTools", "modeHint", "cancelDraftBtn", "newPointZGroup", "newPointZ", "newPointModeGroup",
    "newPointMode", "newEdgeTraversalGroup", "newEdgeTraversal", "confirmEdgeBtn", "zoomOutBtn",
    "zoomInBtn", "fitBtn", "mapStage", "mapCanvas", "loadingLayer", "cursorCoords", "selectionType",
    "selectionTitle", "clearSelectionBtn", "emptyInspector", "inspectorForm", "fieldId", "labelGroup",
    "fieldLabel", "kindGroup", "fieldKind", "levelGroup", "fieldLevel", "coordinateGroup", "fieldX",
    "fieldY", "fieldZ", "editModeGroup", "fieldEditMode", "descriptionGroup", "fieldDescription",
    "aliasesGroup", "fieldAliases", "propertiesGroup", "fieldProperties", "edgeGroup", "fieldFrom",
    "fieldTo", "fieldDirected", "fieldTraversal", "bindingGroup", "bindingStatus", "bindingPath",
    "isaacSelectionHint", "bindCurrentBtn", "unbindBtn", "navigationBindingStatus", "navigationBindingSearch",
    "navigationBindingList", "pickNavigationBtn", "clearNavigationBtn", "applyNavigationBtn", "deleteBtn", "applyBtn", "pendingDraftBanner",
    "pendingDraftText", "retryDraftBtn", "discardDraftBtn"
  ].forEach((id) => { els[id] = $(id); });

  class ConflictError extends Error {}
  const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
  const hidden = (element, value) => element.classList.toggle("hidden", Boolean(value));
  const finite = (value, name) => {
    const result = Number(value);
    if (!Number.isFinite(result)) throw new Error(`${name}必须是有限数字`);
    return result;
  };
  const state = {
    server: null, projects: [], currentProjectId: null, activeLayer: "ground", activeDomain: "ground",
    currentLevel: null, referenceLevel: null, filter: "all", selection: null, pendingEdit: null,
    inspectorDraft: null, disconnected: false, preview: null, drag: null, edgeStartId: null,
    edgeEndId: null, edgeIdentity: null, zoom: 1, panX: 0, panY: 0, layerImage: null,
    layerImageKey: null, layerToken: 0, pollTimer: null, polling: false, writing: false, selectionSent: "null",
    suppressSelectionPush: false, navigationBindingDraft: null, bindingPickMode: false
  };
  const hints = {
    "add-node": "空白处添加节点；点击已有节点选择并拖动",
    "add-edge": "依次点击两个节点连边；点击已有边选择并拖动",
    "add-semantic-point": "空白处添加语义点；可在右侧绑定 Isaac Prim"
  };

  function showError(value) { els.errorText.textContent = value instanceof Error ? value.message : String(value); hidden(els.errorBanner, false); }
  function clearError() { hidden(els.errorBanner, true); }
  let noticeTimer = null;
  function notice(text) { els.noticeBanner.textContent = text; hidden(els.noticeBanner, false); clearTimeout(noticeTimer); noticeTimer = setTimeout(() => hidden(els.noticeBanner, true), 2600); }
  async function request(path, options = {}) {
    const config = { ...options, headers: { ...(options.headers || {}) } };
    if (config.body && typeof config.body === "object") { config.headers["Content-Type"] = "application/json"; config.body = JSON.stringify(config.body); }
    const response = await fetch(path, config); let payload = {};
    try { payload = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok) { const error = response.status === 409 ? new ConflictError(payload.error || "编辑上下文已变化") : new Error(payload.error || `请求失败（HTTP ${response.status}）`); error.status = response.status; throw error; }
    return payload;
  }
  function identity(extra = {}) {
    const value = state.server; if (!value) throw new Error("编辑器尚未连接");
    return { revision: value.revision, csrf_token: value.csrf_token, scene_key: value.scene_key,
      scene_generation: value.scene_generation, context_generation: value.context_generation,
      active_layer: value.active_layer || state.activeLayer, domain: value.active_domain, ...extra };
  }
  async function refresh({ preserveDraft = true } = {}) { const fresh = await request("/api/state"); acceptState(fresh, { preserveDraft }); return fresh; }
  async function write(path, extra, success, expected = null) {
    if (state.writing) return null; state.writing = true; clearError(); const captured = expected || identity();
    try { const fresh = await request(path, { method: "POST", body: { ...captured, ...extra } }); acceptState(fresh, { preserveSelection: true, preserveDraft: true }); if (success) notice(success); return fresh; }
    catch (error) {
      if (error instanceof ConflictError || !error.status) {
        if (path === "/api/edit") state.pendingEdit = { extra: clone(extra), identity: captured, layer: state.activeLayer, uncertain: !error.status };
        try { await refresh({ preserveDraft: true }); } catch (_) { state.disconnected = true; cancelGesture(); }
        renderPending(); showError(error.status ? "编辑未提交，草稿已保留。请确认当前图层后重新提交。" : "连接中断，提交结果未知。请核对当前数据；草稿已保留。"); return null;
      }
      showError(error); return null;
    } finally { state.writing = false; }
  }
  const edit = (operation, args, success, expected = null) => write("/api/edit", { operation, ...args }, success, expected);
  const documentValue = (domain = state.activeDomain) => state.server && (state.server.documents && state.server.documents[domain] || (state.server.document && state.server.document.domain === domain ? state.server.document : null)) || {};
  const nodes = (domain = state.activeDomain) => Array.isArray(documentValue(domain).nodes) ? documentValue(domain).nodes : [];
  const edges = (domain = state.activeDomain) => Array.isArray(documentValue(domain).edges) ? documentValue(domain).edges : [];
  const semanticPoints = () => Array.isArray(state.server && state.server.semantic_points) ? state.server.semantic_points : [];
  const levels = () => state.server && Array.isArray(state.server.levels) ? state.server.levels : [];
  const nodeById = (id, domain = state.activeDomain) => nodes(domain).find((row) => row.id === id) || null;
  const edgeById = (id, domain = state.activeDomain) => edges(domain).find((row) => row.id === id) || null;
  const pointById = (id) => semanticPoints().find((row) => row.id === id) || null;
  const recordFor = (selection = state.selection) => !selection ? null : selection.type === "node" ? nodeById(selection.id, selection.domain || state.activeDomain) : selection.type === "edge" ? edgeById(selection.id, selection.domain || state.activeDomain) : pointById(selection.id);
  const layerVisible = (layer) => state.server && state.server.visible_layers ? state.server.visible_layers[layer] !== false : true;
  const typeVisible = (type) => state.server && state.server.visible_types ? state.server.visible_types[type] !== false : true;
  const nodeOnLayer = (row, domain = state.activeDomain) => {
    if (!row || !layerVisible(domain)) return false;
    return domain !== "ground" || row.level_id == null || row.level_id === state.currentLevel;
  };
  const visibleNode = (row, domain = state.activeDomain) => typeVisible("nodes") && nodeOnLayer(row, domain);
  const visibleEdge = (row, domain = state.activeDomain) => {
    if (!row || !typeVisible("edges") || !layerVisible(domain)) return false;
    return nodeOnLayer(nodeById(row.from, domain), domain) || nodeOnLayer(nodeById(row.to, domain), domain);
  };
  const toolAllows = (type, mode = state.server && state.server.editor_tool) =>
    mode === "add-semantic-point" ? type === "semantic_point"
      : mode === "add-edge" ? type === "node" || type === "edge"
        : type === "node";
  const contextKey = (value) => value ? `${value.scene_key}:${value.scene_generation}:${value.context_generation}:${value.active_layer}:${value.active_domain}:${value.active_level}` : "";
  function cancelGesture() { state.preview = null; state.drag = null; state.edgeStartId = null; state.edgeEndId = null; state.edgeIdentity = null; updateToolUI(); draw(); }
  function renderPending() {
    const pending = state.pendingEdit; hidden(els.pendingDraftBanner, !pending);
    els.pendingDraftText.textContent = pending && pending.uncertain ? "提交结果未知，请核对当前数据。草稿已保留。" : "有待处理的编辑草稿。确认当前图层后可重新提交。";
    els.retryDraftBtn.disabled = !pending || pending.uncertain || !state.server || state.disconnected || pending.identity.scene_key !== state.server.scene_key || pending.layer !== state.activeLayer;
  }
  async function retryPending() { const pending = state.pendingEdit; if (!pending || els.retryDraftBtn.disabled) return; const fresh = await write("/api/edit", pending.extra, "草稿已提交", identity()); if (fresh) { state.pendingEdit = null; state.inspectorDraft = null; cancelGesture(); renderPending(); renderInspector(); } }
  function selectionFromServer(value) {
    if (!value) return null;
    if (value.kind === "node" && toolAllows("node") && state.activeLayer !== "semantic_points" && typeVisible("nodes") && layerVisible(state.activeDomain) && visibleNode(nodeById(value.id, value.domain || state.activeDomain), value.domain || state.activeDomain)) return { type: "node", id: value.id, domain: value.domain || state.activeDomain };
    if (value.kind === "edge" && toolAllows("edge") && state.activeLayer !== "semantic_points" && typeVisible("edges") && layerVisible(state.activeDomain) && visibleEdge(edgeById(value.id, value.domain || state.activeDomain), value.domain || state.activeDomain)) return { type: "edge", id: value.id, domain: value.domain || state.activeDomain };
    if (value.kind === "semantic_point" && toolAllows("semantic_point") && pointById(value.id) && state.activeLayer === "semantic_points" && layerVisible("semantic_points") && typeVisible("semantic_points")) return { type: "semantic_point", id: value.id };
    return null;
  }
  function acceptState(fresh, options = {}) {
    if (!fresh || typeof fresh !== "object") throw new Error("服务端状态无效");
    const oldKey = contextKey(state.server), previous = state.selection, previousLayer = state.activeLayer, previousRevision = state.server && state.server.revision;
    state.server = fresh; state.activeLayer = fresh.active_layer || state.activeLayer || "ground"; state.activeDomain = fresh.active_domain || (state.activeLayer === "aerial" ? "aerial" : "ground"); state.currentLevel = fresh.active_level || (levels()[0] && levels()[0].id) || null; state.referenceLevel = Object.prototype.hasOwnProperty.call(fresh, "reference_level") ? fresh.reference_level : state.referenceLevel;
    const revisionChanged = previousRevision != null && fresh.revision !== previousRevision;
    const changed = Boolean(oldKey && oldKey !== contextKey(fresh));
    if (revisionChanged) { state.navigationBindingDraft = null; state.bindingPickMode = false; }
    if (changed || previousLayer !== state.activeLayer) { state.inspectorDraft = null; state.navigationBindingDraft = null; state.bindingPickMode = false; state.selectionSent = JSON.stringify(fresh.web_selection || null); cancelGesture(); if (previousLayer !== state.activeLayer) { state.filter = "all"; [...els.recordTabs.querySelectorAll("[data-filter]")].forEach((item) => item.classList.toggle("active", item.dataset.filter === "all")); } }
    let next = selectionFromServer(fresh.web_selection);
    if (!changed && previous && recordFor(previous) && (state.inspectorDraft || !next && options.preserveSelection)) next = previous;
    if (!state.drag) state.selection = next;
    if (fresh.binding_target_mode === "semantic_navigation" && fresh.binding_target_id === (state.selection && state.selection.id)) state.bindingPickMode = true;
    else if (fresh.binding_target_mode !== "semantic_navigation") state.bindingPickMode = false;
    renderAll();
  }
  function renderAll() {
    const fresh = state.server; els.sceneSubtitle.textContent = fresh ? `${fresh.scene_key} · ${state.activeLayer === "semantic_points" ? "语义点" : state.activeDomain === "aerial" ? "空中图" : "地面图"}` : "正在连接…";
    els.projectPath.textContent = fresh && (fresh.source_path || fresh.project_path) || ""; els.revisionBadge.textContent = fresh ? `修订 ${fresh.revision}` : "修订 —"; els.dirtyBadge.textContent = fresh && fresh.dirty ? "有未保存修改" : "已保存"; els.dirtyBadge.className = `status-badge ${fresh && fresh.dirty ? "dirty" : "saved"}`; els.undoBtn.disabled = !fresh || !fresh.can_undo; els.redoBtn.disabled = !fresh || !fresh.can_redo;
    [...els.layerToggle.querySelectorAll("[data-layer]")].forEach((button) => button.classList.toggle("active", fresh && button.dataset.layer === state.activeLayer));
    const settingLayer = state.activeLayer === "semantic_points" ? "semantic_points" : "aerial"; const settings = fresh && fresh.new_point_settings && fresh.new_point_settings[settingLayer] || { z: 1.5, edit_mode: "3d" }; if (document.activeElement !== els.newPointZ) els.newPointZ.value = String(settings.z == null ? 1.5 : settings.z); if (document.activeElement !== els.newPointMode) els.newPointMode.value = settings.edit_mode || "3d";
    renderFloors(); renderVisibility(); renderRecords(); renderWarnings(); renderInspector(); renderPending(); const runtime = fresh && fresh.isaac_runtime; els.sceneSyncStatus.textContent = !runtime || runtime.state === "disabled" ? "仅网页模式" : runtime.state === "ready" ? "Isaac Sim 场景已同步" : runtime.state === "error" ? `Isaac: ${runtime.error || "连接失败"}` : "Isaac 场景加载中…"; updateToolUI(); loadLayer(); draw();
  }
  function renderFloors() {
    hidden(els.floorList, state.activeLayer !== "ground");
    els.floorList.textContent = ""; levels().forEach((level) => { const button = document.createElement("button"); button.type = "button"; button.className = `floor-item ${level.id === state.currentLevel ? "active" : ""}`; button.dataset.level = level.id; button.innerHTML = `<strong></strong><span></span><b></b>`; button.querySelector("strong").textContent = level.label || level.id; button.querySelector("span").textContent = level.id; button.querySelector("b").textContent = `Z ${Number(level.support_z_m).toFixed(2)}`; els.floorList.appendChild(button); });
    els.fieldLevel.textContent = ""; const noLevel = document.createElement("option"); noLevel.value = ""; noLevel.textContent = "跨层 / 未绑定楼层"; els.fieldLevel.appendChild(noLevel); levels().forEach((level) => { const option = document.createElement("option"); option.value = level.id; option.textContent = level.label || level.id; els.fieldLevel.appendChild(option); });
    els.referenceLevel.textContent = ""; const none = document.createElement("option"); none.value = ""; none.textContent = "无底图"; els.referenceLevel.appendChild(none); levels().forEach((level) => { const option = document.createElement("option"); option.value = level.id; option.textContent = level.label || level.id; els.referenceLevel.appendChild(option); }); els.referenceLevel.value = state.referenceLevel || "";
  }
  function renderVisibility() {
    const layers = state.server && state.server.visible_layers || { ground: true, aerial: true, semantic_points: true }; const types = state.server && state.server.visible_types || { nodes: true, edges: true, semantic_points: true };
    els.showGround.checked = layers.ground !== false; els.showAerial.checked = layers.aerial !== false; els.showSemanticPoints.checked = layers.semantic_points !== false; els.showNodes.checked = types.nodes !== false; els.showEdges.checked = types.edges !== false; els.showPointType.checked = types.semantic_points !== false;
  }
  function visibleRecords() {
    const query = els.recordSearch.value.trim().toLowerCase(); let rows = [];
    const mode = state.server && state.server.editor_tool || "add-node";
    if (state.activeLayer === "semantic_points" || mode === "add-semantic-point") {
      rows = layerVisible("semantic_points") && typeVisible("semantic_points")
        ? semanticPoints().map((record) => ({ type: "semantic_point", record })) : [];
    } else {
      const includeNodes = mode === "add-node" || mode === "add-edge";
      const includeEdges = mode === "add-edge";
      if (includeNodes) rows.push(...nodes(state.activeDomain).filter((record) => visibleNode(record)).map((record) => ({ type: "node", record })));
      if (includeEdges) rows.push(...edges(state.activeDomain).filter((record) => visibleEdge(record)).map((record) => ({ type: "edge", record })));
    }
    return rows.filter((item) => (state.filter === "all" || state.filter === (item.type === "semantic_point" ? "semantic_points" : `${item.type}s`)) && (!query || `${item.record.id} ${item.record.label || ""} ${item.record.kind || ""}`.toLowerCase().includes(query)));
  }
  function renderRecords() {
    const rows = visibleRecords(); els.recordCount.textContent = String(rows.length); els.recordList.textContent = "";
    rows.forEach(({ type, record }) => { const button = document.createElement("button"); button.type = "button"; button.className = `record-item ${state.selection && state.selection.type === type && state.selection.id === record.id ? "active" : ""}`; button.dataset.type = type; button.dataset.id = record.id; button.innerHTML = `<span class="record-symbol ${type}"></span><span class="record-copy"><strong></strong><span></span></span>`; button.querySelector(".record-symbol").textContent = type === "node" ? "●" : type === "edge" ? "↔" : "✦"; button.querySelector("strong").textContent = record.label || record.id; button.querySelector(".record-copy span").textContent = record.id; els.recordList.appendChild(button); });
    if (!rows.length) { const empty = document.createElement("div"); empty.className = "record-empty"; empty.textContent = "当前筛选没有记录"; els.recordList.appendChild(empty); }
  }
  function renderWarnings() { const warnings = state.server && state.server.warnings || []; hidden(els.warningsSection, !warnings.length); els.warningsList.textContent = ""; warnings.forEach((warning) => { const li = document.createElement("li"); li.textContent = warning; els.warningsList.appendChild(li); }); }
  function setGroup(element, show) { hidden(element, !show); }
  function navigationCandidates() {
    const query = (els.navigationBindingSearch.value || "").trim().toLowerCase();
    const result = [];
    for (const domain of ["ground", "aerial"]) for (const node of nodes(domain)) {
      const text = `${domain} ${node.id} ${node.label || ""} ${(node.aliases || []).join(" ")}`.toLowerCase();
      if (!query || text.includes(query)) result.push({ domain, node });
    }
    return result;
  }
  function renderNavigationBindings(record) {
    if (!els.navigationBindingList) return;
    const current = state.navigationBindingDraft && state.navigationBindingDraft.id === record.id
      ? state.navigationBindingDraft.bindings : (record.navigation_bindings || []);
    if (!state.navigationBindingDraft || state.navigationBindingDraft.id !== record.id) state.navigationBindingDraft = { id: record.id, bindings: clone(current) };
    const keys = new Set(current.map((row) => `${row.domain}:${row.node_id}`));
    els.navigationBindingStatus.textContent = `${current.length} 个绑定`;
    els.navigationBindingList.textContent = "";
    const candidates = navigationCandidates();
    candidates.forEach(({ domain, node }) => {
      const label = document.createElement("label"); label.className = "toggle-row binding-node-option";
      const input = document.createElement("input"); input.type = "checkbox"; input.checked = keys.has(`${domain}:${node.id}`); input.dataset.domain = domain; input.dataset.nodeId = node.id;
      const text = document.createElement("span"); text.textContent = `${domain === "ground" ? "地面" : "空中"} · ${node.id} · ${node.label || "无名节点"} · 存在`;
      label.append(input, text); els.navigationBindingList.appendChild(label);
    });
    const candidateKeys = new Set(candidates.map(({ domain, node }) => `${domain}:${node.id}`));
    current.filter((row) => !candidateKeys.has(`${row.domain}:${row.node_id}`)).forEach((row) => {
      const label = document.createElement("label"); label.className = "toggle-row binding-node-option stale";
      const input = document.createElement("input"); input.type = "checkbox"; input.checked = true; input.disabled = true;
      const text = document.createElement("span"); text.textContent = `${row.domain === "ground" ? "地面" : "空中"} · ${row.node_id} · 不存在`;
      label.append(input, text); els.navigationBindingList.appendChild(label);
    });
    els.navigationBindingList.querySelectorAll("input").forEach((input) => input.addEventListener("change", () => {
      const key = `${input.dataset.domain}:${input.dataset.nodeId}`;
      const draft = state.navigationBindingDraft.bindings.filter((row) => `${row.domain}:${row.node_id}` !== key);
      if (input.checked) draft.push({ domain: input.dataset.domain, node_id: input.dataset.nodeId });
      state.navigationBindingDraft.bindings = draft.sort((a, b) => `${a.domain}:${a.node_id}`.localeCompare(`${b.domain}:${b.node_id}`));
      els.navigationBindingStatus.textContent = `${draft.length} 个绑定`;
    }));
  }
  function renderInspector() {
    const record = recordFor(); hidden(els.emptyInspector, Boolean(record)); hidden(els.inspectorForm, !record); hidden(els.clearSelectionBtn, !record); if (!record) { els.selectionType.textContent = "未选择"; els.selectionTitle.textContent = "属性检查器"; return; }
    const type = state.selection.type, point = type === "semantic_point" ? previewPoint(record) : type === "node" ? (state.preview && state.preview.kind === "node" && state.preview.id === record.id ? state.preview.position : record.position_xyz) : null;
    els.fieldId.value = record.id; els.fieldLabel.value = record.label || ""; els.fieldKind.value = record.kind || ""; els.fieldLevel.value = record.level_id || ""; setGroup(els.labelGroup, type !== "edge"); setGroup(els.kindGroup, type !== "edge"); setGroup(els.levelGroup, type === "node" && state.activeDomain === "ground"); setGroup(els.coordinateGroup, type !== "edge"); setGroup(els.editModeGroup, type === "node" && state.activeDomain === "aerial" || type === "semantic_point"); setGroup(els.descriptionGroup, type === "semantic_point"); setGroup(els.aliasesGroup, type === "semantic_point"); setGroup(els.propertiesGroup, type === "semantic_point"); setGroup(els.edgeGroup, type === "edge"); setGroup(els.bindingGroup, type === "semantic_point");
    if (type === "node" || type === "semantic_point") { [els.fieldX.value, els.fieldY.value, els.fieldZ.value] = point.map((value) => String(Number(value))); els.fieldEditMode.value = record.edit_mode || "3d"; }
    if (type === "edge") { els.fieldFrom.value = record.from; els.fieldTo.value = record.to; els.fieldDirected.checked = Boolean(record.directed); els.fieldTraversal.value = state.activeDomain === "aerial" ? "walk" : record.traversal_mode || "walk"; els.fieldTraversal.disabled = state.activeDomain === "aerial"; }
    if (type === "semantic_point") { els.fieldDescription.value = record.description || ""; els.fieldAliases.value = (record.aliases || []).join(", "); els.fieldProperties.value = JSON.stringify(record.properties || {}, null, 2); const binding = record.isaac_binding || {}, bound = Boolean(binding.prim_path), status = bound ? state.server && state.server.binding_status && state.server.binding_status[record.id] || "unverified" : "none"; els.bindingStatus.textContent = !bound ? "未绑定" : status === "valid" ? "有效" : status === "unverified" ? "待验证" : "无效"; els.bindingStatus.dataset.status = status; els.bindingPath.textContent = binding.prim_path || "—"; const selected = state.server && state.server.isaac_selection, bindable = selected && selected.selection_kind === "prim" && selected.session_id && selected.sequence != null && selected.prim_paths && selected.prim_paths.length === 1; els.isaacSelectionHint.textContent = selected && selected.prim_paths && selected.prim_paths.length ? `当前选择：${selected.prim_paths.join(", ")}` : "请先在 Isaac 中选择一个 Prim。"; els.bindCurrentBtn.disabled = !bindable; els.unbindBtn.disabled = !bound; els.pickNavigationBtn.textContent = state.bindingPickMode ? "结束拾取" : "地图拾取"; renderNavigationBindings(record); }
    els.selectionType.textContent = type === "node" ? (state.activeDomain === "aerial" ? "空中节点" : "地面节点") : type === "edge" ? "拓扑边" : "语义点"; els.selectionTitle.textContent = record.label || record.id;
    const endpointOnly = type === "node" && state.server && state.server.editor_tool === "add-edge";
    [els.fieldLabel, els.fieldKind, els.fieldLevel, els.fieldX, els.fieldY, els.fieldZ, els.fieldEditMode,
      els.fieldDescription, els.fieldAliases, els.fieldProperties, els.fieldDirected, els.fieldTraversal]
      .forEach((control) => { if (control) control.disabled = endpointOnly; });
    els.deleteBtn.disabled = endpointOnly;
    els.applyBtn.disabled = endpointOnly;
    const draft = state.inspectorDraft; if (draft && draft.selection.type === type && draft.selection.id === record.id) for (const [key, value] of Object.entries(draft.values)) { if (key === "fieldDirected") els[key].checked = value; else els[key].value = value; }
  }
  function captureInspector() { const record = recordFor(); if (!record || !state.selection) return; if (!state.inspectorDraft) state.inspectorDraft = { selection: clone(state.selection), record: clone(record), identity: identity() }; state.inspectorDraft.values = {}; for (const key of ["fieldLabel", "fieldKind", "fieldLevel", "fieldX", "fieldY", "fieldZ", "fieldEditMode", "fieldDescription", "fieldAliases", "fieldProperties", "fieldTraversal"]) state.inspectorDraft.values[key] = els[key].value; state.inspectorDraft.values.fieldDirected = els.fieldDirected.checked; }
  function updateToolUI() { const mode = state.server && state.server.editor_tool || "add-node", pointSettings = mode === "add-semantic-point" || mode === "add-node" && state.activeDomain === "aerial"; [...els.modeTools.querySelectorAll("[data-mode]")].forEach((button) => button.classList.toggle("active", button.dataset.mode === mode)); els.modeHint.textContent = state.edgeEndId ? "连线预览已就绪，点击确认连线" : state.edgeStartId ? `已选择起点 ${state.edgeStartId}，请选择终点` : hints[mode] || hints["add-node"]; hidden(els.cancelDraftBtn, !(state.edgeStartId || state.edgeEndId || state.preview)); hidden(els.confirmEdgeBtn, !state.edgeEndId); hidden(els.newPointZGroup, !pointSettings); hidden(els.newPointModeGroup, !pointSettings); hidden(els.newEdgeTraversalGroup, !(mode === "add-edge" && state.activeDomain === "ground")); }
  async function setContext(changes) { if (!state.server) return; const activeLayer = changes.active_layer || state.activeLayer, activeDomain = activeLayer === "aerial" ? "aerial" : activeLayer === "ground" ? "ground" : state.activeDomain; const body = identity({ active_layer: activeLayer, active_domain: changes.active_domain || activeDomain, tool: changes.tool || state.server.editor_tool, level_id: changes.level_id || state.currentLevel }); for (const key of ["reference_level", "visible_layers", "visible_types", "new_point_settings", "binding_target_mode", "binding_target_id", "binding_target_draft"]) if (Object.prototype.hasOwnProperty.call(changes, key)) body[key] = changes[key]; await write("/api/editor/context", body); }
  function selectionPayload() { const value = state.selection; if (!value) return null; if (value.type === "semantic_point") return { kind: "semantic_point", collection: null, id: value.id }; return { kind: value.type, collection: null, id: value.id, domain: value.domain || state.activeDomain }; }
  async function pushSelection() { if (!state.server || state.suppressSelectionPush) return; const selection = selectionPayload(), signature = JSON.stringify(selection); if (signature === state.selectionSent) return; try { await request("/api/web/selection", { method: "POST", body: identity({ selection }) }); state.selectionSent = signature; } catch (error) { if (!(error instanceof ConflictError)) showError(error); } }
  function select(value, { push = true } = {}) { if (state.bindingPickMode && (!value || value.type !== "semantic_point" || !state.selection || value.id !== state.selection.id)) { state.bindingPickMode = false; if (els.pickNavigationBtn) els.pickNavigationBtn.textContent = "地图拾取"; setContext({ binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError); } if (JSON.stringify(value) !== JSON.stringify(state.selection)) state.inspectorDraft = null; state.selection = value; renderRecords(); renderInspector(); draw(); if (push) pushSelection(); }
  function clearSelection(push = true) { state.bindingPickMode = false; if (els.pickNavigationBtn) els.pickNavigationBtn.textContent = "地图拾取"; setContext({ binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError); select(null, { push }); }
  function currentExtent() { const level = levels().find((row) => row.id === (state.referenceLevel || state.currentLevel)); return level && level.extent_xy || [-10, 10, -10, 10]; }
  function canvasMetrics() { const rect = els.mapCanvas.getBoundingClientRect(), dpr = globalThis.devicePixelRatio || 1, width = Math.max(1, Math.round(rect.width * dpr)), height = Math.max(1, Math.round(rect.height * dpr)); if (els.mapCanvas.width !== width || els.mapCanvas.height !== height) { els.mapCanvas.width = width; els.mapCanvas.height = height; } return { rect, dpr, width, height }; }
  function transform() { const metrics = canvasMetrics(), [xmin, xmax, ymin, ymax] = currentExtent().map(Number), scale = Math.min(metrics.width / Math.max(.001, xmax - xmin), metrics.height / Math.max(.001, ymax - ymin)) * .92 * state.zoom; return { ...metrics, scale, cx: metrics.width / 2 + state.panX * metrics.dpr, cy: metrics.height / 2 + state.panY * metrics.dpr, mx: (xmin + xmax) / 2, my: (ymin + ymax) / 2 }; }
  function worldToScreen(point, t = transform()) { return [t.cx + (point[0] - t.mx) * t.scale, t.cy - (point[1] - t.my) * t.scale]; }
  function screenToWorld(clientX, clientY) { const t = transform(), x = (clientX - t.rect.left) * t.dpr, y = (clientY - t.rect.top) * t.dpr; return [t.mx + (x - t.cx) / t.scale, t.my - (y - t.cy) / t.scale]; }
  function previewNode(node, domain = state.activeDomain) {
    if (state.preview && state.preview.kind === "node" && state.preview.id === node.id && state.preview.domain === domain) return state.preview.position;
    if (state.preview && state.preview.kind === "edge" && domain === state.activeDomain) {
      const edge = edgeById(state.preview.id, domain);
      if (edge && (edge.from === node.id || edge.to === node.id)) return [node.position_xyz[0] + state.preview.delta[0], node.position_xyz[1] + state.preview.delta[1], node.position_xyz[2]];
    }
    return node.position_xyz;
  }
  function previewPoint(point) {
    return state.preview && state.preview.kind === "semantic_point" && state.preview.id === point.id
      ? state.preview.position : point.position_xyz;
  }
  function distanceSegment(point, a, b) { const dx = b[0] - a[0], dy = b[1] - a[1], length = dx * dx + dy * dy, ratio = length ? Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) : 0, projection = [a[0] + ratio * dx, a[1] + ratio * dy]; return { distance: Math.hypot(point[0] - projection[0], point[1] - projection[1]), projection }; }
  function hitTest(clientX, clientY) {
    const world = screenToWorld(clientX, clientY), t = transform(), tolerance = 10 * t.dpr / t.scale;
    const mode = state.server && state.server.editor_tool || "add-node";
    if (state.bindingPickMode) {
      for (const domain of ["ground", "aerial"]) for (const node of nodes(domain).slice().reverse()) {
        if (visibleNode(node, domain) && Math.hypot(world[0] - previewNode(node, domain)[0], world[1] - previewNode(node, domain)[1]) <= tolerance) return { type: "node", id: node.id, domain, record: node };
      }
      return null;
    }
    if (state.activeLayer === "semantic_points" || mode === "add-semantic-point") { for (const point of semanticPoints().slice().reverse()) { if (!layerVisible("semantic_points") || !typeVisible("semantic_points")) continue; if (Math.hypot(world[0] - previewPoint(point)[0], world[1] - previewPoint(point)[1]) <= tolerance) return { type: "semantic_point", id: point.id, record: point }; } return null; }
    for (const node of nodes(state.activeDomain).slice().reverse()) { if (!visibleNode(node)) continue; const point = previewNode(node); if (Math.hypot(world[0] - point[0], world[1] - point[1]) <= tolerance) return { type: "node", id: node.id, record: node }; }
    if (mode !== "add-edge") return null;
    for (const edge of edges(state.activeDomain).slice().reverse()) { if (!visibleEdge(edge)) continue; const a = nodeById(edge.from, state.activeDomain), b = nodeById(edge.to, state.activeDomain); const line = edge.polyline_xyz && edge.polyline_xyz.length > 1 ? edge.polyline_xyz : [a && a.position_xyz, b && b.position_xyz].filter(Boolean); for (let index = 0; index + 1 < line.length; index++) if (distanceSegment(world, line[index], line[index + 1]).distance <= tolerance) return { type: "edge", id: edge.id, record: edge }; }
    return null;
  }
  function drawPath(context, points, t) { if (!points.length) return; context.beginPath(); points.forEach((point, index) => { const screen = worldToScreen(point, t); if (index) context.lineTo(...screen); else context.moveTo(...screen); }); }
  function draw() {
    if (!state.server) return; const t = transform(), context = els.mapCanvas.getContext("2d"); context.clearRect(0, 0, t.width, t.height); context.fillStyle = "#080d13"; context.fillRect(0, 0, t.width, t.height);
    if (state.layerImage) { const [xmin, xmax, ymin, ymax] = currentExtent(), a = worldToScreen([xmin, ymax], t), b = worldToScreen([xmax, ymin], t); context.globalAlpha = .86; context.drawImage(state.layerImage, a[0], a[1], b[0] - a[0], b[1] - a[1]); context.globalAlpha = 1; }
    for (const domain of ["ground", "aerial"]) { if (!layerVisible(domain)) continue; for (const edge of edges(domain)) { if (!visibleEdge(edge, domain)) continue; const a = nodeById(edge.from, domain), b = nodeById(edge.to, domain); if (!a || !b) continue; let line = edge.polyline_xyz && edge.polyline_xyz.length > 1 ? clone(edge.polyline_xyz) : [a.position_xyz, b.position_xyz]; if (state.preview && state.preview.kind === "edge" && state.preview.id === edge.id && domain === state.activeDomain) line = line.map((point) => [point[0] + state.preview.delta[0], point[1] + state.preview.delta[1], point[2]]); else { line[0] = previewNode(a, domain); line[line.length - 1] = previewNode(b, domain); } drawPath(context, line, t); const selected = state.selection && state.selection.type === "edge" && state.selection.id === edge.id && state.selection.domain === domain; context.strokeStyle = selected ? "#e8fff8" : domain === "aerial" ? "#6ab5ff" : "#2dd4a7"; context.lineWidth = (selected ? 4 : 2.5) * t.dpr; context.stroke(); }
      for (const node of nodes(domain)) { if (!visibleNode(node, domain)) continue; const point = worldToScreen(previewNode(node, domain), t), selected = state.selection && state.selection.type === "node" && state.selection.id === node.id && state.selection.domain === domain; context.beginPath(); context.arc(point[0], point[1], (selected ? 7 : 5) * t.dpr, 0, Math.PI * 2); context.fillStyle = selected ? "#fff" : domain === "aerial" ? "#6ab5ff" : "#43dfb8"; context.fill(); context.strokeStyle = "#07150f"; context.lineWidth = 2 * t.dpr; context.stroke(); }
    }
    if (layerVisible("semantic_points") && typeVisible("semantic_points")) for (const point of semanticPoints()) { const screen = worldToScreen(previewPoint(point), t), selected = state.selection && state.selection.type === "semantic_point" && state.selection.id === point.id; context.beginPath(); context.arc(screen[0], screen[1], (selected ? 8 : 6) * t.dpr, 0, Math.PI * 2); context.fillStyle = selected ? "#fff" : "#f0965a"; context.fill(); context.strokeStyle = "#3b1b0b"; context.lineWidth = 2 * t.dpr; context.stroke(); }
    if (state.edgeStartId && state.edgeEndId) { const a = nodeById(state.edgeStartId), b = nodeById(state.edgeEndId); if (a && b) { drawPath(context, [previewNode(a), previewNode(b)], t); context.strokeStyle = "#ffe5a0"; context.lineWidth = 3 * t.dpr; context.setLineDash([7 * t.dpr, 4 * t.dpr]); context.stroke(); context.setLineDash([]); } }
  }
  async function loadLayer() {
    const level = state.referenceLevel == null
      ? null : levels().find((row) => row.id === state.referenceLevel);
    const key = level ? `${state.server.scene_generation}:${level.id}` : null;
    if (key === state.layerImageKey) return;
    state.layerImageKey = key;
    if (!level) {
      ++state.layerToken;
      state.layerImage = null;
      hidden(els.loadingLayer, true);
      draw();
      return;
    }
    const token = ++state.layerToken;
    hidden(els.loadingLayer, false);
    const image = new Image();
    image.onload = () => {
      if (token !== state.layerToken) return;
      state.layerImage = image;
      hidden(els.loadingLayer, true);
      draw();
    };
    image.onerror = () => {
      if (token === state.layerToken) hidden(els.loadingLayer, true);
    };
    image.src = `/api/layer?level=${encodeURIComponent(level.id)}&geometry_only=1`;
  }
  async function createNode(point) { const level = levels().find((row) => row.id === state.currentLevel), aerial = state.activeDomain === "aerial", z = aerial ? finite(els.newPointZ.value, "Z") : Number(level && level.support_z_m || 0); await edit("add_node", { position_xyz: [point[0], point[1], z], ...(aerial ? {} : { level_id: state.currentLevel }), label: "", kind: "custom", edit_mode: aerial ? els.newPointMode.value : undefined }, "节点已创建"); }
  async function createSemanticPoint(point) { await edit("add_semantic_point", { position_xyz: [point[0], point[1], finite(els.newPointZ.value, "Z")], edit_mode: els.newPointMode.value, label: "", kind: "custom", description: "", aliases: [], properties: {} }, "语义点已创建"); }
  function edgeNode(node) { if (!state.edgeStartId) { state.edgeStartId = node.id; state.edgeIdentity = identity(); select({ type: "node", id: node.id, domain: state.activeDomain }); updateToolUI(); return; } if (state.edgeStartId === node.id) { showError("边的起点和终点必须不同"); return; } state.edgeEndId = node.id; updateToolUI(); draw(); }
  async function confirmEdge() { if (!state.edgeStartId || !state.edgeEndId) return; const traversal = state.activeDomain === "ground" ? els.newEdgeTraversal.value : "walk"; const fresh = await edit("add_edge", { from_id: state.edgeStartId, to_id: state.edgeEndId, directed: false, traversal_mode: traversal }, "边已创建", state.edgeIdentity); if (fresh) cancelGesture(); }
  function parseProperties() { try { const value = JSON.parse(els.fieldProperties.value || "{}"); if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(); return value; } catch (_) { throw new Error("自定义属性必须是 JSON 对象"); } }
  async function applyInspector(event) {
    event.preventDefault(); const selection = state.selection, savedDraft = state.inspectorDraft, record = savedDraft ? savedDraft.record : recordFor(), expected = savedDraft ? savedDraft.identity : identity(); if (!selection || !record || !toolAllows(selection.type) || selection.type === "node" && state.server.editor_tool === "add-edge") return; let fresh;
    if (selection.type === "node") { fresh = await edit("update_node", { id: record.id, position_xyz: [finite(els.fieldX.value, "X"), finite(els.fieldY.value, "Y"), finite(els.fieldZ.value, "Z")], label: els.fieldLabel.value.trim(), kind: els.fieldKind.value.trim() || "custom", ...(state.activeDomain === "ground" ? { level_id: els.fieldLevel.value || null } : {}), ...(state.activeDomain === "aerial" ? { edit_mode: els.fieldEditMode.value } : {}) }, "节点属性已更新", expected); }
    else if (selection.type === "edge") fresh = await edit("update_edge", { id: record.id, directed: els.fieldDirected.checked, traversal_mode: els.fieldTraversal.value }, "边属性已更新", expected);
    else fresh = await edit("update_semantic_point", { id: record.id, position_xyz: [finite(els.fieldX.value, "X"), finite(els.fieldY.value, "Y"), finite(els.fieldZ.value, "Z")], edit_mode: els.fieldEditMode.value, label: els.fieldLabel.value.trim(), kind: els.fieldKind.value.trim() || "custom", description: els.fieldDescription.value, aliases: [...new Set(els.fieldAliases.value.split(",").map((value) => value.trim()).filter(Boolean))], properties: parseProperties() }, "语义点属性已更新", expected);
    if (fresh) { state.inspectorDraft = null; renderInspector(); }
  }
  async function deleteSelection() { const value = state.selection; if (!value || !toolAllows(value.type) || value.type === "node" && state.server.editor_tool === "add-edge") return; const operation = value.type === "node" ? "remove_node" : value.type === "edge" ? "remove_edge" : "remove_semantic_point"; const fresh = await edit(operation, { id: value.id }, "已删除；可撤销恢复"); if (fresh) clearSelection(); }
  async function bindCurrent() { const value = state.selection, selected = state.server && state.server.isaac_selection; if (!value || value.type !== "semantic_point") return; if (!selected || !selected.session_id || selected.sequence == null || !selected.prim_paths || selected.prim_paths.length !== 1) { showError("需要一个最新且唯一的 Isaac Prim 选择"); return; } const fresh = await edit("bind_semantic_point", { id: value.id, selection_session_id: selected.session_id, selection_sequence: selected.sequence }, "已绑定当前 Isaac 选择"); if (fresh) renderInspector(); }
  async function unbind() { const value = state.selection; if (!value || value.type !== "semantic_point") return; const fresh = await edit("unbind_semantic_point", { id: value.id }, "已解除 Isaac 绑定"); if (fresh) renderInspector(); }
  async function applyNavigationBindings() {
    const value = state.selection, draft = state.navigationBindingDraft;
    if (!value || value.type !== "semantic_point" || !draft || draft.id !== value.id) return;
    const fresh = await edit("set_semantic_point_navigation_bindings", { id: value.id, bindings: draft.bindings }, "导航点绑定已更新");
    if (fresh) { state.navigationBindingDraft = null; renderInspector(); }
  }
  function clearNavigationBindings() {
    if (!state.navigationBindingDraft) return;
    state.navigationBindingDraft.bindings = [];
    renderNavigationBindings(recordFor());
    if (state.bindingPickMode && state.selection && state.selection.type === "semantic_point") {
      setContext({ binding_target_mode: "semantic_navigation", binding_target_id: state.selection.id,
        binding_target_draft: [] }).catch(showError);
    }
  }
  function toggleNavigationPick() {
    cancelGesture();
    const record = recordFor();
    if (!record || !state.selection || state.selection.type !== "semantic_point") return;
    state.bindingPickMode = !state.bindingPickMode;
    els.pickNavigationBtn.textContent = state.bindingPickMode ? "结束拾取" : "地图拾取";
    const draft = state.navigationBindingDraft && state.navigationBindingDraft.id === record.id
      ? state.navigationBindingDraft.bindings : (record.navigation_bindings || []);
    setContext(state.bindingPickMode
      ? { binding_target_mode: "semantic_navigation", binding_target_id: record.id, binding_target_draft: clone(draft) }
      : { binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError);
    notice(state.bindingPickMode ? "绑定拾取模式：点击地面或空中节点加入/移除" : "已退出绑定拾取模式");
  }
  function onPointerDown(event) {
    if (!state.server || state.disconnected || state.writing) return;
    const world = screenToWorld(event.clientX, event.clientY);
    const screen = [event.clientX, event.clientY];
    const hit = hitTest(event.clientX, event.clientY);
    const mode = state.server.editor_tool;
    if (event.button !== 0 || event.altKey) {
      state.drag = {
        kind: "pan", screenStart: screen,
        pan: [state.panX, state.panY], moved: false
      };
      els.mapCanvas.setPointerCapture(event.pointerId);
      return;
    }
    if (state.bindingPickMode && hit && hit.type === "node") {
      const point = recordFor(); if (!point || !state.navigationBindingDraft) return;
      const key = `${hit.domain}:${hit.id}`, existing = state.navigationBindingDraft.bindings.some((row) => `${row.domain}:${row.node_id}` === key);
      state.navigationBindingDraft.bindings = existing ? state.navigationBindingDraft.bindings.filter((row) => `${row.domain}:${row.node_id}` !== key) : [...state.navigationBindingDraft.bindings, { domain: hit.domain, node_id: hit.id }];
      renderNavigationBindings(point); draw();
      setContext({ binding_target_mode: "semantic_navigation", binding_target_id: point.id,
        binding_target_draft: clone(state.navigationBindingDraft.bindings) }).catch(showError);
      return;
    }
    if (hit && hit.type === "node" && mode === "add-edge") { edgeNode(hit.record); return; }
    if (hit && (hit.type === "node" || hit.type === "semantic_point")) {
      select({
        type: hit.type, id: hit.id,
        ...(hit.type === "node" ? { domain: state.activeDomain } : {})
      });
      state.drag = {
        kind: hit.type, id: hit.id, domain: state.activeDomain,
        worldStart: world, screenStart: screen,
        origin: clone(hit.record.position_xyz), moved: false,
        identity: identity()
      };
      els.mapCanvas.setPointerCapture(event.pointerId);
      return;
    }
    if (hit && hit.type === "edge") {
      if (mode !== "add-edge") return;
      select({ type: "edge", id: hit.id, domain: state.activeDomain });
      state.drag = {
        kind: "edge", id: hit.id, domain: state.activeDomain,
        worldStart: world, screenStart: screen,
        moved: false, delta: [0, 0], identity: identity()
      };
      els.mapCanvas.setPointerCapture(event.pointerId);
      return;
    }
    if (mode === "add-node") createNode(world).catch(showError);
    else if (mode === "add-semantic-point") createSemanticPoint(world).catch(showError);
    else if (mode === "add-edge") showError("边工具需要点击已有节点");
  }
  function onPointerMove(event) {
    const world = screenToWorld(event.clientX, event.clientY);
    els.cursorCoords.textContent = `X ${world[0].toFixed(2)} · Y ${world[1].toFixed(2)} · ${state.currentLevel || "—"}`;
    if (!state.drag) return;
    const dxPx = event.clientX - state.drag.screenStart[0];
    const dyPx = event.clientY - state.drag.screenStart[1];
    if (Math.hypot(dxPx, dyPx) >= 3) state.drag.moved = true;
    if (state.drag.kind === "pan") {
      state.panX = state.drag.pan[0] + dxPx;
      state.panY = state.drag.pan[1] + dyPx;
      draw();
      return;
    }
    const delta = [
      world[0] - state.drag.worldStart[0],
      world[1] - state.drag.worldStart[1]
    ];
    if (state.drag.kind === "node") {
      state.preview = {
        kind: "node", id: state.drag.id, domain: state.drag.domain,
        position: [state.drag.origin[0] + delta[0], state.drag.origin[1] + delta[1], state.drag.origin[2]]
      };
    } else if (state.drag.kind === "semantic_point") {
      state.preview = {
        kind: "semantic_point", id: state.drag.id,
        position: [state.drag.origin[0] + delta[0], state.drag.origin[1] + delta[1], state.drag.origin[2]]
      };
    } else if (state.drag.kind === "edge") {
      state.drag.delta = delta;
      state.preview = { kind: "edge", id: state.drag.id, delta };
    }
    renderInspector();
    draw();
  }
  async function onPointerUp() {
    const drag = state.drag;
    const preview = state.preview;
    state.drag = null;
    if (!drag || drag.kind === "pan" || !drag.moved || !preview) {
      state.preview = null;
      draw();
      return;
    }
    state.preview = null;
    if (drag.kind === "node") {
      await edit("move_node_xy", { id: drag.id, position_xy: preview.position.slice(0, 2) }, "节点已移动", drag.identity);
    } else if (drag.kind === "semantic_point") {
      await edit("move_semantic_point_xy", { id: drag.id, position_xy: preview.position.slice(0, 2) }, "语义点已移动", drag.identity);
    } else if (drag.kind === "edge") {
      await edit("translate_edge_xy", { id: drag.id, delta_xy: preview.delta }, "边及关联节点已移动", drag.identity);
    }
    renderInspector();
    draw();
  }
  async function poll() {
    if (state.polling || state.writing) return;
    state.polling = true;
    try {
      const fresh = await request("/api/state");
      const old = state.server;
      const reconnected = state.disconnected || Boolean(
        old && fresh.csrf_token !== old.csrf_token
      );
      const changed = reconnected || !old
        || fresh.revision !== old.revision
        || contextKey(fresh) !== contextKey(old)
        || JSON.stringify(fresh.web_selection) !== JSON.stringify(old.web_selection)
        || JSON.stringify(fresh.isaac_selection) !== JSON.stringify(old.isaac_selection)
        || JSON.stringify(fresh.binding_status) !== JSON.stringify(old.binding_status)
        || JSON.stringify(fresh.isaac_runtime) !== JSON.stringify(old.isaac_runtime);
      state.disconnected = false;
      if (reconnected) {
        cancelGesture();
        state.inspectorDraft = null;
      }
      if (changed) {
        state.suppressSelectionPush = true;
        acceptState(fresh, { preserveSelection: false, preserveDraft: true });
        state.selectionSent = JSON.stringify(fresh.web_selection || null);
        state.suppressSelectionPush = false;
      }
    } catch (_) {
      if (!state.disconnected) {
        cancelGesture();
        state.inspectorDraft = null;
        showError("地图服务已断开；当前手势已取消，正在重新连接。");
      }
      state.disconnected = true;
      renderPending();
    } finally {
      state.polling = false;
    }
  }
  function setVisibility(type, checked) { const visible_layers = clone(state.server.visible_layers || {}), visible_types = clone(state.server.visible_types || {}); if (type.startsWith("layer:")) visible_layers[type.slice(6)] = checked; else visible_types[type.slice(5)] = checked; setContext({ visible_layers, visible_types }).catch(showError); }
  function syncPointSettings() { const layer = state.activeLayer === "semantic_points" ? "semantic_points" : "aerial", settings = clone(state.server.new_point_settings || { aerial: { z: 1.5, edit_mode: "3d" }, semantic_points: { z: 1.5, edit_mode: "3d" } }); settings[layer] = { ...(settings[layer] || {}), z: finite(els.newPointZ.value, "Z"), edit_mode: els.newPointMode.value }; setContext({ new_point_settings: settings }).catch(showError); }
  function bindEvents() {
    els.closeErrorBtn.addEventListener("click", clearError); els.layerToggle.addEventListener("click", (event) => { const button = event.target.closest("[data-layer]"); if (!button) return; setContext({ active_layer: button.dataset.layer }).catch(showError); }); els.floorList.addEventListener("click", (event) => { const button = event.target.closest("[data-level]"); if (button) setContext({ active_layer: "ground", active_domain: "ground", level_id: button.dataset.level }).catch(showError); }); els.referenceLevel.addEventListener("change", () => setContext({ reference_level: els.referenceLevel.value || null }).catch(showError));
    [[els.showGround, "layer:ground"], [els.showAerial, "layer:aerial"], [els.showSemanticPoints, "layer:semantic_points"], [els.showNodes, "type:nodes"], [els.showEdges, "type:edges"], [els.showPointType, "type:semantic_points"]].forEach(([input, type]) => input.addEventListener("change", () => setVisibility(type, input.checked)));
    els.modeTools.addEventListener("click", (event) => { const button = event.target.closest("[data-mode]"); if (button) { const mode = button.dataset.mode; const activeLayer = mode === "add-semantic-point" ? "semantic_points" : state.activeLayer === "semantic_points" ? state.activeDomain : state.activeLayer; setContext({ tool: mode, active_layer: activeLayer }).catch(showError); } }); els.recordSearch.addEventListener("input", renderRecords); els.recordTabs.addEventListener("click", (event) => { const button = event.target.closest("[data-filter]"); if (!button) return; state.filter = button.dataset.filter; [...els.recordTabs.querySelectorAll("[data-filter]")].forEach((item) => item.classList.toggle("active", item === button)); renderRecords(); }); els.recordList.addEventListener("click", (event) => { const button = event.target.closest("[data-id]"); if (button && toolAllows(button.dataset.type)) select({ type: button.dataset.type, id: button.dataset.id, ...(button.dataset.type !== "semantic_point" ? { domain: state.activeDomain } : {}) }); });
    const safePointSettings = () => { try { syncPointSettings(); } catch (error) { showError(error); } }; els.newPointZ.addEventListener("change", safePointSettings); els.newPointMode.addEventListener("change", safePointSettings); els.inspectorForm.addEventListener("submit", (event) => applyInspector(event).catch(showError)); els.inspectorForm.addEventListener("input", captureInspector); els.inspectorForm.addEventListener("change", captureInspector); els.deleteBtn.addEventListener("click", deleteSelection); els.bindCurrentBtn.addEventListener("click", bindCurrent); els.unbindBtn.addEventListener("click", unbind); els.navigationBindingSearch.addEventListener("input", () => { const record = recordFor(); if (record && state.selection.type === "semantic_point") renderNavigationBindings(record); }); els.applyNavigationBtn.addEventListener("click", () => applyNavigationBindings().catch(showError)); els.clearNavigationBtn.addEventListener("click", clearNavigationBindings); els.pickNavigationBtn.addEventListener("click", toggleNavigationPick); els.clearSelectionBtn.addEventListener("click", () => clearSelection()); els.cancelDraftBtn.addEventListener("click", cancelGesture); els.confirmEdgeBtn.addEventListener("click", confirmEdge); els.retryDraftBtn.addEventListener("click", retryPending); els.discardDraftBtn.addEventListener("click", () => { state.pendingEdit = null; state.inspectorDraft = null; cancelGesture(); renderPending(); renderInspector(); }); els.projectPicker.addEventListener("change", () => openProject(els.projectPicker.value)); els.categoryPicker.addEventListener("change", () => { renderProjectOptions(); if (els.projectPicker.value !== state.currentProjectId) openProject(els.projectPicker.value); }); els.undoBtn.addEventListener("click", () => write("/api/undo", {}, "已撤销")); els.redoBtn.addEventListener("click", () => write("/api/redo", {}, "已重做")); els.saveBtn.addEventListener("click", () => write("/api/save", {}, "已保存并导出")); els.fitBtn.addEventListener("click", () => { state.zoom = 1; state.panX = 0; state.panY = 0; draw(); }); els.zoomInBtn.addEventListener("click", () => { state.zoom = Math.min(10, state.zoom * 1.25); draw(); }); els.zoomOutBtn.addEventListener("click", () => { state.zoom = Math.max(.2, state.zoom * .8); draw(); }); els.mapCanvas.addEventListener("pointerdown", onPointerDown); els.mapCanvas.addEventListener("pointermove", onPointerMove); els.mapCanvas.addEventListener("pointerup", onPointerUp); els.mapCanvas.addEventListener("pointercancel", () => { state.drag = null; state.preview = null; draw(); }); els.mapCanvas.addEventListener("contextmenu", (event) => event.preventDefault()); els.mapCanvas.addEventListener("wheel", (event) => { event.preventDefault(); state.zoom = Math.max(.2, Math.min(10, state.zoom * (event.deltaY < 0 ? 1.12 : .89))); draw(); }, { passive: false }); new ResizeObserver(draw).observe(els.mapStage);
    globalThis.addEventListener("keydown", (event) => { const modifier = event.ctrlKey || event.metaKey; if (modifier && event.key.toLowerCase() === "s") { event.preventDefault(); els.saveBtn.click(); } else if (modifier && event.key.toLowerCase() === "z") { event.preventDefault(); (event.shiftKey ? els.redoBtn : els.undoBtn).click(); } else if (modifier && event.key.toLowerCase() === "y") { event.preventDefault(); els.redoBtn.click(); } else if (event.key === "Escape") { event.preventDefault(); cancelGesture(); state.bindingPickMode = false; if (els.pickNavigationBtn) els.pickNavigationBtn.textContent = "地图拾取"; setContext({ binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError); } else if (event.key === "Enter" && state.edgeEndId) { event.preventDefault(); confirmEdge(); } });
  }
  async function loadProjects() { try { const payload = await request("/api/projects"); state.projects = payload.projects || []; state.currentProjectId = payload.current_id || null; hidden(els.projectPickerWrap, state.projects.length < 2); const categories = [...new Set(state.projects.map((row) => row.category || "场景"))]; els.categoryPicker.textContent = ""; categories.forEach((value) => { const option = document.createElement("option"); option.value = value; option.textContent = value; els.categoryPicker.appendChild(option); }); const current = state.projects.find((row) => row.id === state.currentProjectId); if (current) els.categoryPicker.value = current.category || "场景"; renderProjectOptions(); } catch (_) { hidden(els.projectPickerWrap, true); } }
  function renderProjectOptions() { els.projectPicker.textContent = ""; state.projects.filter((row) => (row.category || "场景") === els.categoryPicker.value).forEach((row) => { const option = document.createElement("option"); option.value = row.id; option.textContent = row.display_name || row.scene_key || row.id; els.projectPicker.appendChild(option); }); if ([...els.projectPicker.options].some((option) => option.value === state.currentProjectId)) els.projectPicker.value = state.currentProjectId; }
  async function openProject(id) { if (!id || id === state.currentProjectId) return; if (state.server.dirty && !globalThis.confirm("当前工程有未保存修改，是否放弃修改并切换场景？")) { renderProjectOptions(); return; } const fresh = await write("/api/open", { id, discard_unsaved: true }, "场景已切换"); if (fresh) await loadProjects(); else renderProjectOptions(); }
  async function init() { bindEvents(); state.pollTimer = setInterval(poll, 500); try { const [fresh] = await Promise.all([request("/api/state"), loadProjects()]); acceptState(fresh); state.selectionSent = JSON.stringify(fresh.web_selection || null); } catch (error) { hidden(els.loadingLayer, true); showError(`无法加载地图项目：${error.message}`); } }
  init();
})();
