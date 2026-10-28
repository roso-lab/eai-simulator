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
    "modeTools", "modeHint", "roadBuildMenu", "roadHeightStatus", "cancelDraftBtn", "newPointZGroup", "newPointZ", "newPointModeGroup",
    "newPointMode", "newEdgeTraversalGroup", "newEdgeTraversal", "confirmEdgeBtn", "zoomOutBtn",
    "zoomInBtn", "fitBtn", "mapStage", "mapCanvas", "loadingLayer", "cursorCoords", "selectionType",
    "selectionTitle", "clearSelectionBtn", "emptyInspector", "inspectorForm", "fieldId", "labelGroup",
    "fieldLabel", "kindGroup", "fieldKind", "levelGroup", "fieldLevel", "coordinateGroup", "fieldX",
    "fieldY", "fieldZ", "editModeGroup", "fieldEditMode", "descriptionGroup", "fieldDescription",
    "aliasesGroup", "fieldAliases", "propertiesGroup", "fieldProperties", "edgeGroup", "fieldFrom",
    "fieldTo", "fieldDirected", "fieldTraversal", "bindingGroup", "navigationBindingStatus", "navigationBindingSearch",
    "navigationBindingList", "pickNavigationBtn", "clearNavigationBtn", "applyNavigationBtn", "deleteBtn", "applyBtn", "pendingDraftBanner",
    "pendingDraftText", "retryDraftBtn", "discardDraftBtn",
    "labelCreation", "annotationNew", "cancelNavigationBtn", "annotationForm", "annotationLabel", "annotationKind",
    "annotationDescription", "annotationAliases", "annotationProperties", "annotationCancel"
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
    inspectorDraft: null, disconnected: false, preview: null, drag: null, box: null, selectionItems: [], edgeStartId: null,
    edgeEndId: null, edgeIdentity: null, zoom: 1, panX: 0, panY: 0, layerImage: null,
    layerImageKey: null, layerToken: 0, pollTimer: null, polling: false, writing: false, selectionSent: "null",
    suppressSelectionPush: false, navigationBindingDraft: null, bindingPickMode: false,
    pointSettingsKey: null, roadDraft: null, roadPlan: null, roadRequest: 0, cHeld: false,
    roadHeightStep: 0.1,
    errorMessage: null, noticeMessage: null, annotationDraft: null
  };
  const hints = {
    "add-node": "空白处添加节点；点击已有节点选择并拖动",
    "add-edge": "依次点击两个节点连边；点击已有边选择并拖动",
    "add-semantic-point": "先新建标签，再从列表选择标签，点击“连接导航点”进行绑定",
    "build-ground-road": "按住左键拖动修路；松开后选择连接方式。按住 C 暂停吸附",
    "delete-ground-edge": "点击地面边删除，保留端点节点",
    "build-aerial-road": "按下左键前用 PageUp/PageDown 调整起点 Z；拖动时调整终点 Z，C 暂停吸附",
    "delete-aerial-edge": "点击空中边删除，保留端点节点"
  };
  const roadBuildTools = new Set(["build-ground-road", "build-aerial-road"]);
  const roadDeleteTools = new Set(["delete-ground-edge", "delete-aerial-edge"]);
  const isRoadTool = (mode) => roadBuildTools.has(mode);
  const isRoadDeleteTool = (mode) => roadDeleteTools.has(mode);

  function renderNotifications() {
    const hasPriorityNotice = Boolean(state.errorMessage || state.pendingEdit);
    hidden(els.errorBanner, !state.errorMessage);
    hidden(els.pendingDraftBanner, !state.pendingEdit);
    hidden(els.noticeBanner, hasPriorityNotice || !state.noticeMessage);
  }
  function showError(value) { state.errorMessage = value instanceof Error ? value.message : String(value); els.errorText.textContent = state.errorMessage; renderNotifications(); }
  function clearError() { state.errorMessage = null; renderNotifications(); }
  let noticeTimer = null;
  function notice(text) { state.noticeMessage = text; els.noticeBanner.textContent = text; renderNotifications(); clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { state.noticeMessage = null; renderNotifications(); }, 2600); }
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
  const bindingNodeVisible = (row, domain) => row
    && (domain !== "ground" || row.level_id == null || row.level_id === state.currentLevel);
  const visibleEdge = (row, domain = state.activeDomain) => {
    if (!row || !typeVisible("edges") || !layerVisible(domain)) return false;
    return nodeOnLayer(nodeById(row.from, domain), domain) || nodeOnLayer(nodeById(row.to, domain), domain);
  };
  const toolAllows = (type, mode = state.server && state.server.editor_tool) =>
    mode === "add-semantic-point" ? type === "semantic_point"
      : mode === "add-edge" ? type === "node" || type === "edge"
        : isRoadDeleteTool(mode) ? type === "edge"
        : type === "node";
  const contextKey = (value) => value ? `${value.scene_key}:${value.scene_generation}:${value.context_generation}:${value.active_layer}:${value.active_domain}:${value.active_level}` : "";
  function cancelGesture() { state.preview = null; state.drag = null; state.box = null; state.roadDraft = null; state.roadPlan = null; state.edgeStartId = null; state.edgeEndId = null; state.edgeIdentity = null; hidden(els.roadBuildMenu, true); updateToolUI(); draw(); }
  function selectionItems() { return state.selectionItems.length ? state.selectionItems : (state.selection ? [{ type: state.selection.type, id: state.selection.id, ...(state.selection.domain ? { domain: state.selection.domain } : {}) }] : []); }
  function setSelectionItems(items) { state.selectionItems = items; state.selection = items[0] || null; renderRecords(); renderInspector(); draw(); }
  function isTextTarget(target) { return target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable); }
  function renderPending() {
    const pending = state.pendingEdit;
    els.pendingDraftText.textContent = pending && pending.uncertain ? "提交结果未知，请核对当前数据。草稿已保留。" : "有待处理的编辑草稿。确认当前图层后可重新提交。";
    els.retryDraftBtn.disabled = !pending || pending.uncertain || !state.server || state.disconnected || pending.identity.scene_key !== state.server.scene_key || pending.layer !== state.activeLayer;
    renderNotifications();
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
    const previousServer = state.server, oldKey = contextKey(previousServer), previous = state.selection, previousLayer = state.activeLayer, previousRevision = previousServer && previousServer.revision;
    state.server = fresh; state.activeLayer = fresh.active_layer || state.activeLayer || "ground"; state.activeDomain = fresh.active_domain || (state.activeLayer === "aerial" ? "aerial" : "ground"); state.currentLevel = fresh.active_level || (levels()[0] && levels()[0].id) || null; state.referenceLevel = Object.prototype.hasOwnProperty.call(fresh, "reference_level") ? fresh.reference_level : state.referenceLevel;
    if (oldKey !== contextKey(fresh)) state.annotationDraft = null;
    const revisionChanged = previousRevision != null && fresh.revision !== previousRevision;
    const changed = Boolean(oldKey && oldKey !== contextKey(fresh));
    const pendingBindings = state.navigationBindingDraft;
    const keepBindingDraft = Boolean(pendingBindings && previous && previous.type === "semantic_point"
      && previous.id === pendingBindings.id && !revisionChanged
      && fresh.scene_key === previousServer.scene_key
      && fresh.scene_generation === previousServer.scene_generation
      && fresh.active_layer === previousLayer
      && fresh.active_level === previousServer.active_level
      && fresh.editor_tool === previousServer.editor_tool);
    if (revisionChanged) { state.navigationBindingDraft = null; state.bindingPickMode = false; }
    if (changed || previousLayer !== state.activeLayer) { state.inspectorDraft = null; state.navigationBindingDraft = keepBindingDraft ? pendingBindings : null; state.bindingPickMode = false; state.selectionSent = JSON.stringify(fresh.web_selection || null); cancelGesture(); if (previousLayer !== state.activeLayer) { state.filter = "all"; [...els.recordTabs.querySelectorAll("[data-filter]")].forEach((item) => item.classList.toggle("active", item.dataset.filter === "all")); } }
    let next = selectionFromServer(fresh.web_selection);
    if (!changed && previous && recordFor(previous) && (state.inspectorDraft || !next && options.preserveSelection)) next = previous;
    if (!state.drag) {
      if (JSON.stringify(next) !== JSON.stringify(previous)) {
        state.annotationDraft = null;
        state.selectionItems = next ? [clone(next)] : [];
        if (!next || next.type !== "semantic_point" || next.id !== (pendingBindings && pendingBindings.id)) state.navigationBindingDraft = null;
      }
      state.selection = next;
    }
    if (fresh.binding_target_mode === "semantic_navigation" && fresh.binding_target_id === (state.selection && state.selection.id)) {
      state.bindingPickMode = true;
      state.navigationBindingDraft = { id: fresh.binding_target_id, bindings: clone(fresh.binding_target_draft || []) };
    }
    else if (fresh.binding_target_mode !== "semantic_navigation") state.bindingPickMode = false;
    renderAll();
  }
  function renderAll() {
    const fresh = state.server; els.sceneSubtitle.textContent = fresh ? `${fresh.scene_key} · ${state.activeLayer === "semantic_points" ? "语义管理" : state.activeDomain === "aerial" ? "空中图" : "地面图"}` : "正在连接…";
    els.projectPath.textContent = fresh && (fresh.source_path || fresh.project_path) || ""; els.revisionBadge.textContent = fresh ? `修订 ${fresh.revision}` : "修订 —"; els.dirtyBadge.textContent = fresh && fresh.dirty ? "有未保存修改" : "已保存"; els.dirtyBadge.className = `status-badge ${fresh && fresh.dirty ? "dirty" : "saved"}`; els.undoBtn.disabled = !fresh || !fresh.can_undo; els.redoBtn.disabled = !fresh || !fresh.can_redo;
    [...els.layerToggle.querySelectorAll("[data-layer]")].forEach((button) => button.classList.toggle("active", fresh && button.dataset.layer === state.activeLayer));
    renderPointSettings();
    renderFloors(); renderVisibility(); renderRecords(); renderWarnings(); renderInspector(); renderPending(); const runtime = fresh && fresh.isaac_runtime; els.sceneSyncStatus.textContent = !runtime || runtime.state === "disabled" ? "仅网页模式" : runtime.state === "ready" ? "Isaac Sim 场景已同步" : runtime.state === "error" ? `Isaac: ${runtime.error || "连接失败"}` : "Isaac 场景加载中…"; updateToolUI(); loadLayer(); draw();
  }
  function renderPointSettings() {
    const fresh = state.server;
    const settingLayer = state.activeLayer;
    const defaultZ = settingLayer === "ground" ? Number((levels().find((row) => row.id === state.currentLevel) || {}).support_z_m || 0) : 1.5;
    const settings = fresh && fresh.new_point_settings && fresh.new_point_settings[settingLayer] || { z: defaultZ, edit_mode: "3d" };
    const key = JSON.stringify([fresh && fresh.scene_key, fresh && fresh.scene_generation, settingLayer, settingLayer === "ground" ? state.currentLevel : null]);
    const switched = key !== state.pointSettingsKey;
    state.pointSettingsKey = key;
    if (switched || document.activeElement !== els.newPointZ) els.newPointZ.value = String(settings.z == null ? defaultZ : settings.z);
    if (switched || document.activeElement !== els.newPointMode) els.newPointMode.value = settings.edit_mode || "3d";
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
      const includeNodes = mode === "add-node" || mode === "add-edge" || isRoadTool(mode);
      const includeEdges = mode === "add-edge" || isRoadTool(mode) || isRoadDeleteTool(mode);
      if (includeNodes) rows.push(...nodes(state.activeDomain).filter((record) => visibleNode(record)).map((record) => ({ type: "node", record })));
      if (includeEdges) rows.push(...edges(state.activeDomain).filter((record) => visibleEdge(record)).map((record) => ({ type: "edge", record })));
    }
    return rows.filter((item) => (state.filter === "all" || state.filter === (item.type === "semantic_point" ? "semantic_points" : `${item.type}s`)) && (!query || `${item.record.id} ${item.record.label || ""} ${item.record.kind || ""} ${(item.record.aliases || []).join(" ")}`.toLowerCase().includes(query)));
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
    els.navigationBindingStatus.textContent = `${current.length} 个绑定${state.bindingPickMode ? " · 地图连接中" : ""}`;
    els.navigationBindingList.textContent = "";
    const candidates = navigationCandidates();
    candidates.forEach(({ domain, node }) => {
      const label = document.createElement("label"); label.className = "toggle-row binding-node-option";
      const input = document.createElement("input"); input.type = "checkbox"; input.checked = keys.has(`${domain}:${node.id}`); input.dataset.domain = domain; input.dataset.nodeId = node.id;
      const text = document.createElement("span"); text.textContent = `${domain === "ground" ? "地面" : "空中"} · ${node.id} · ${node.label || "无名节点"} · 存在`;
      label.append(input, text); els.navigationBindingList.appendChild(label);
    });
    const candidateKeys = new Set(["ground", "aerial"].flatMap(domain => nodes(domain).map(node => `${domain}:${node.id}`)));
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
      els.navigationBindingStatus.textContent = `${draft.length} 个绑定${state.bindingPickMode ? " · 地图连接中" : ""}`;
      if (state.bindingPickMode) syncNavigationBindingContext();
      draw();
    }));
  }
  function renderLabelCreation() {
    hidden(els.labelCreation, state.activeLayer !== "semantic_points");
    hidden(els.annotationForm, !state.annotationDraft);
    els.annotationNew.disabled = Boolean(state.annotationDraft);
  }
  function startAnnotation() {
    if (state.activeLayer !== "semantic_points" || state.writing) return;
    state.annotationDraft = { identity: identity() };
    els.annotationLabel.value = "";
    els.annotationKind.value = "custom";
    els.annotationDescription.value = "";
    els.annotationAliases.value = "";
    els.annotationProperties.value = "{}";
    renderLabelCreation();
  }
  function cancelAnnotation() {
    state.annotationDraft = null;
    renderLabelCreation();
  }
  async function saveAnnotation(event) {
    event.preventDefault();
    const draft = state.annotationDraft;
    if (!draft || state.activeLayer !== "semantic_points") return;
    const properties = JSON.parse(els.annotationProperties.value || "{}");
    if (!properties || Array.isArray(properties) || typeof properties !== "object") throw new Error("属性必须是 JSON 对象");
    const fresh = await edit("add_semantic_point", {
      label: els.annotationLabel.value.trim(), kind: els.annotationKind.value.trim() || "custom",
      description: els.annotationDescription.value,
      aliases: [...new Set(els.annotationAliases.value.split(",").map(s => s.trim()).filter(Boolean))], properties
    }, "标签已创建；可连接导航点", draft.identity);
    if (fresh) {
      state.annotationDraft = null; state.inspectorDraft = null;
      select(selectionFromServer(fresh.web_selection), { push: false });
    }
  }
  function bindAnnotationEvents() {
    els.annotationNew.addEventListener("click", startAnnotation);
    els.annotationCancel.addEventListener("click", cancelAnnotation);
    els.annotationForm.addEventListener("submit", event => saveAnnotation(event).catch(showError));
    els.cancelNavigationBtn.addEventListener("click", cancelNavigationBindings);
  }
  function renderInspector() {
    renderLabelCreation();
    if (state.selectionItems.length > 1) {
      hidden(els.emptyInspector, false); hidden(els.inspectorForm, true); hidden(els.clearSelectionBtn, false);
      els.selectionType.textContent = "多选"; els.selectionTitle.textContent = `已选择 ${state.selectionItems.length} 个对象`;
      return;
    }
    const record = recordFor(); hidden(els.emptyInspector, Boolean(record) || state.activeLayer === "semantic_points"); hidden(els.inspectorForm, !record); hidden(els.clearSelectionBtn, !record); if (!record) { els.selectionType.textContent = "未选择"; els.selectionTitle.textContent = "属性检查器"; return; }
    const type = state.selection.type, point = type === "semantic_point" ? null : type === "node" ? (state.preview && state.preview.kind === "node" && state.preview.id === record.id ? state.preview.position : record.position_xyz) : null;
    els.fieldId.value = record.id; els.fieldLabel.value = record.label || ""; els.fieldKind.value = record.kind || ""; els.fieldLevel.value = record.level_id || ""; setGroup(els.labelGroup, type !== "edge"); setGroup(els.kindGroup, type !== "edge"); setGroup(els.levelGroup, type === "node" && state.activeDomain === "ground"); setGroup(els.coordinateGroup, type === "node"); setGroup(els.editModeGroup, type === "node" && state.activeDomain === "aerial"); setGroup(els.descriptionGroup, type === "semantic_point"); setGroup(els.aliasesGroup, type === "semantic_point"); setGroup(els.propertiesGroup, type === "semantic_point"); setGroup(els.edgeGroup, type === "edge"); setGroup(els.bindingGroup, type === "semantic_point");
    if (type === "node") { [els.fieldX.value, els.fieldY.value, els.fieldZ.value] = point.map((value) => String(Number(value))); els.fieldEditMode.value = record.edit_mode || "3d"; }
    if (type === "edge") { els.fieldFrom.value = record.from; els.fieldTo.value = record.to; els.fieldDirected.checked = Boolean(record.directed); els.fieldTraversal.value = state.activeDomain === "aerial" ? "walk" : record.traversal_mode || "walk"; els.fieldTraversal.disabled = state.activeDomain === "aerial"; }
    if (type === "semantic_point") { els.fieldDescription.value = record.description || ""; els.fieldAliases.value = (record.aliases || []).join(", "); els.fieldProperties.value = JSON.stringify(record.properties || {}, null, 2); els.pickNavigationBtn.textContent = state.bindingPickMode ? "结束连接" : "连接导航点"; renderNavigationBindings(record); }
    els.selectionType.textContent = type === "node" ? (state.activeDomain === "aerial" ? "空中节点" : "地面节点") : type === "edge" ? "拓扑边" : "语义标签"; els.selectionTitle.textContent = (record.label || record.id) + (type === "semantic_point" && !(record.navigation_bindings || []).length ? " · 未绑定语义" : "");
    hidden(els.pickNavigationBtn, type !== "semantic_point");
    const endpointOnly = type === "node" && state.server && state.server.editor_tool === "add-edge";
    [els.fieldLabel, els.fieldKind, els.fieldLevel, els.fieldX, els.fieldY, els.fieldZ, els.fieldEditMode,
      els.fieldDescription, els.fieldAliases, els.fieldProperties, els.fieldDirected, els.fieldTraversal]
      .forEach((control) => { if (control) control.disabled = endpointOnly; });
    els.deleteBtn.disabled = endpointOnly;
    els.applyBtn.disabled = endpointOnly;
    const draft = state.inspectorDraft; if (draft && draft.selection.type === type && draft.selection.id === record.id) for (const [key, value] of Object.entries(draft.values)) { if (key === "fieldDirected") els[key].checked = value; else els[key].value = value; }
  }
  function captureInspector() { const record = recordFor(); if (!record || !state.selection) return; if (!state.inspectorDraft) state.inspectorDraft = { selection: clone(state.selection), record: clone(record), identity: identity() }; state.inspectorDraft.values = {}; for (const key of ["fieldLabel", "fieldKind", "fieldLevel", "fieldX", "fieldY", "fieldZ", "fieldEditMode", "fieldDescription", "fieldAliases", "fieldProperties", "fieldTraversal"]) state.inspectorDraft.values[key] = els[key].value; state.inspectorDraft.values.fieldDirected = els.fieldDirected.checked; }
  function updateToolUI() { const mode = state.server && state.server.editor_tool || "add-node", pointSettings = mode === "add-node" || isRoadTool(mode); [...els.modeTools.querySelectorAll("[data-mode]")].forEach((button) => { button.classList.toggle("active", button.dataset.mode === mode); const groundTool = button.dataset.mode === "build-ground-road" || button.dataset.mode === "delete-ground-edge", aerialTool = button.dataset.mode === "build-aerial-road" || button.dataset.mode === "delete-aerial-edge"; if (groundTool || aerialTool) hidden(button, state.activeLayer !== (groundTool ? "ground" : "aerial")); }); els.modeHint.textContent = state.bindingPickMode ? "连接模式：点击地面或空中导航节点加入/移除绑定；完成后应用绑定" : state.edgeEndId ? "连线预览已就绪，点击确认连线" : state.edgeStartId ? `已选择起点 ${state.edgeStartId}，请选择终点` : hints[mode] || hints["add-node"]; const showRoadHeight = mode === "build-aerial-road" && state.roadDraft; hidden(els.roadHeightStatus, !showRoadHeight); if (showRoadHeight) els.roadHeightStatus.textContent = `终点 Z ${state.roadDraft.endZ.toFixed(2)} m`; hidden(els.cancelDraftBtn, !(state.edgeStartId || state.edgeEndId || state.preview || state.roadDraft)); hidden(els.confirmEdgeBtn, !state.edgeEndId); hidden(els.newPointZGroup, !pointSettings); hidden(els.newPointModeGroup, !pointSettings || state.activeLayer === "ground"); hidden(els.newEdgeTraversalGroup, !(mode === "add-edge" && state.activeDomain === "ground")); }
  async function setContext(changes) { if (!state.server) return; const activeLayer = changes.active_layer || state.activeLayer, activeDomain = activeLayer === "aerial" ? "aerial" : activeLayer === "ground" ? "ground" : state.activeDomain; const body = identity({ active_layer: activeLayer, active_domain: changes.active_domain || activeDomain, tool: changes.tool || state.server.editor_tool, level_id: changes.level_id || state.currentLevel }); for (const key of ["reference_level", "visible_layers", "visible_types", "new_point_settings", "binding_target_mode", "binding_target_id", "binding_target_draft"]) if (Object.prototype.hasOwnProperty.call(changes, key)) body[key] = changes[key]; await write("/api/editor/context", body); }
  function selectionPayload() { const value = state.selection; if (!value) return null; if (value.type === "semantic_point") return { kind: "semantic_point", collection: null, id: value.id }; return { kind: value.type, collection: null, id: value.id, domain: value.domain || state.activeDomain }; }
  async function pushSelection() { if (!state.server || state.suppressSelectionPush) return; const selection = selectionPayload(), signature = JSON.stringify(selection); if (signature === state.selectionSent) return; try { await request("/api/web/selection", { method: "POST", body: identity({ selection }) }); state.selectionSent = signature; } catch (error) { if (!(error instanceof ConflictError)) showError(error); } }
  function select(value, { push = true } = {}) {
    const changed = JSON.stringify(value) !== JSON.stringify(state.selection);
    const samePoint = value && value.type === "semantic_point" && state.selection
      && state.selection.type === "semantic_point" && value.id === state.selection.id;
    if (state.bindingPickMode && !samePoint) {
      state.bindingPickMode = false;
      setContext({ binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError);
    }
    if (changed && !samePoint) state.navigationBindingDraft = null;
    if (changed) { state.inspectorDraft = null; state.annotationDraft = null; }
    state.selection = value; state.selectionItems = value ? [{ type: value.type, id: value.id, ...(value.domain ? { domain: value.domain } : {}) }] : []; renderRecords(); renderInspector(); draw();
    if (push) pushSelection();
  }
  function clearSelection(push = true) {
    state.bindingPickMode = false; state.navigationBindingDraft = null;
    if (els.pickNavigationBtn) els.pickNavigationBtn.textContent = "连接导航点";
    setContext({ binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError);
    state.selectionItems = []; select(null, { push });
  }
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

  function distanceSegment(point, a, b) { const dx = b[0] - a[0], dy = b[1] - a[1], length = dx * dx + dy * dy, ratio = length ? Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) : 0, projection = [a[0] + ratio * dx, a[1] + ratio * dy]; return { distance: Math.hypot(point[0] - projection[0], point[1] - projection[1]), projection }; }
  function hitTest(clientX, clientY) {
    const world = screenToWorld(clientX, clientY), t = transform(), tolerance = 10 * t.dpr / t.scale;
    const mode = state.server && state.server.editor_tool || "add-node";
    if (state.bindingPickMode) {
      for (const domain of ["ground", "aerial"]) for (const node of nodes(domain).slice().reverse()) {
        if (bindingNodeVisible(node, domain) && Math.hypot(world[0] - previewNode(node, domain)[0], world[1] - previewNode(node, domain)[1]) <= tolerance) return { type: "node", id: node.id, domain, record: node };
      }
      // Edges remain visible for orientation but are never binding targets.
      return null;
    }
    if (isRoadDeleteTool(mode)) {
      for (const edge of edges(state.activeDomain).slice().reverse()) {
        if (!visibleEdge(edge, state.activeDomain)) continue;
        const a = nodeById(edge.from, state.activeDomain), b = nodeById(edge.to, state.activeDomain);
        const line = edge.polyline_xyz && edge.polyline_xyz.length > 1 ? edge.polyline_xyz : [a && a.position_xyz, b && b.position_xyz].filter(Boolean);
        for (let index = 0; index + 1 < line.length; index++) if (distanceSegment(world, line[index], line[index+1]).distance <= tolerance) return { type: "edge", id: edge.id, record: edge };
      }
      return null;
    }
    if (state.activeLayer === "semantic_points" || mode === "add-semantic-point") return null;
    for (const node of nodes(state.activeDomain).slice().reverse()) { if (!visibleNode(node)) continue; const point = previewNode(node); if (Math.hypot(world[0] - point[0], world[1] - point[1]) <= tolerance) return { type: "node", id: node.id, record: node }; }
    if (mode !== "add-edge" && !isRoadTool(mode) && !isRoadDeleteTool(mode)) return null;
    for (const edge of edges(state.activeDomain).slice().reverse()) { if (!visibleEdge(edge)) continue; const a = nodeById(edge.from, state.activeDomain), b = nodeById(edge.to, state.activeDomain); const line = edge.polyline_xyz && edge.polyline_xyz.length > 1 ? edge.polyline_xyz : [a && a.position_xyz, b && b.position_xyz].filter(Boolean); for (let index = 0; index + 1 < line.length; index++) if (distanceSegment(world, line[index], line[index + 1]).distance <= tolerance) return { type: "edge", id: edge.id, record: edge }; }
    return null;
  }
  function boxSelection(start, end) {
    const t = transform(), sx = (value) => (value - t.rect.left) * t.dpr, sy = (value) => (value - t.rect.top) * t.dpr;
    const left = Math.min(sx(start[0]), sx(end[0])), right = Math.max(sx(start[0]), sx(end[0]));
    const top = Math.min(sy(start[1]), sy(end[1])), bottom = Math.max(sy(start[1]), sy(end[1]));
    const inside = (point) => { const p = worldToScreen(point, t); return p[0] >= left && p[0] <= right && p[1] >= top && p[1] <= bottom; };
    const result = [];
    if (state.activeLayer === "semantic_points") return [];
    const domain = state.activeDomain;
    for (const node of nodes(domain)) if (visibleNode(node, domain) && inside(previewNode(node, domain))) result.push({ type: "node", id: node.id, domain });
    for (const edge of edges(domain)) {
      if (!visibleEdge(edge, domain)) continue;
      const a = nodeById(edge.from, domain), b = nodeById(edge.to, domain);
      const line = edge.polyline_xyz && edge.polyline_xyz.length > 1 ? edge.polyline_xyz : [a && a.position_xyz, b && b.position_xyz].filter(Boolean);
      if (line.length > 1 && line.every(inside)) result.push({ type: "edge", id: edge.id, domain });
    }
    return result;
  }
  function drawPath(context, points, t) { if (!points.length) return; context.beginPath(); points.forEach((point, index) => { const screen = worldToScreen(point, t); if (index) context.lineTo(...screen); else context.moveTo(...screen); }); }
  function draw() {
    if (!state.server) return; const t = transform(), context = els.mapCanvas.getContext("2d"); context.clearRect(0, 0, t.width, t.height); context.fillStyle = "#080d13"; context.fillRect(0, 0, t.width, t.height);
    if (state.layerImage) { const [xmin, xmax, ymin, ymax] = currentExtent(), a = worldToScreen([xmin, ymax], t), b = worldToScreen([xmax, ymin], t); context.globalAlpha = .86; context.drawImage(state.layerImage, a[0], a[1], b[0] - a[0], b[1] - a[1]); context.globalAlpha = 1; }
    const bindingKeys = new Set(state.bindingPickMode && state.selection && state.selection.type === "semantic_point"
      && state.navigationBindingDraft && state.navigationBindingDraft.id === state.selection.id
      ? state.navigationBindingDraft.bindings.map((row) => `${row.domain}:${row.node_id}`) : []);
    const annotated = new Set(semanticPoints().flatMap(p => (p.navigation_bindings || []).map(b => `${b.domain}:${b.node_id}`)));
    const selectedKeys = new Set(selectionItems().map((item) => `${item.type}:${item.id}:${item.domain || ""}`));
    for (const domain of ["ground", "aerial"]) { if (!layerVisible(domain) && !state.bindingPickMode) continue; for (const edge of edges(domain)) { if (!visibleEdge(edge, domain) && !state.bindingPickMode) continue; const a = nodeById(edge.from, domain), b = nodeById(edge.to, domain); if (!a || !b) continue; let line = edge.polyline_xyz && edge.polyline_xyz.length > 1 ? clone(edge.polyline_xyz) : [a.position_xyz, b.position_xyz]; if (state.preview && state.preview.kind === "edge" && state.preview.id === edge.id && domain === state.activeDomain) line = line.map((point) => [point[0] + state.preview.delta[0], point[1] + state.preview.delta[1], point[2]]); else { line[0] = previewNode(a, domain); line[line.length - 1] = previewNode(b, domain); } drawPath(context, line, t); const selected = selectedKeys.has(`edge:${edge.id}:${domain}`); context.strokeStyle = selected ? "#e8fff8" : domain === "aerial" ? "#6ab5ff" : "#2dd4a7"; context.lineWidth = (selected ? 4 : 2.5) * t.dpr; context.stroke(); }
      for (const node of nodes(domain)) { if (!(state.bindingPickMode ? bindingNodeVisible(node, domain) : visibleNode(node, domain))) continue; const point = worldToScreen(previewNode(node, domain), t), selected = selectedKeys.has(`node:${node.id}:${domain}`), bound = bindingKeys.has(`${domain}:${node.id}`); context.globalAlpha = state.bindingPickMode && !bound ? .28 : 1; context.beginPath(); context.arc(point[0], point[1], (bound || selected ? 7 : 5) * t.dpr, 0, Math.PI * 2); context.fillStyle = bound || !state.bindingPickMode && annotated.has(`${domain}:${node.id}`) ? "#ffd166" : selected ? "#fff" : domain === "aerial" ? "#6ab5ff" : "#43dfb8"; context.fill(); context.strokeStyle = bound ? "#fff4cc" : "#07150f"; context.lineWidth = (bound ? 2.5 : 2) * t.dpr; context.stroke(); context.globalAlpha = 1; }
    }
    if (state.box) { const x = Math.min(state.box.start[0], state.box.end[0]), y = Math.min(state.box.start[1], state.box.end[1]), w = Math.abs(state.box.end[0] - state.box.start[0]), h = Math.abs(state.box.end[1] - state.box.start[1]); context.fillStyle = "rgba(80,180,255,.15)"; context.fillRect(x - t.rect.left, y - t.rect.top, w, h); context.strokeStyle = "#9bdcff"; context.setLineDash([6, 4]); context.strokeRect(x - t.rect.left, y - t.rect.top, w, h); context.setLineDash([]); }
    if (state.edgeStartId && state.edgeEndId) { const a = nodeById(state.edgeStartId), b = nodeById(state.edgeEndId); if (a && b) { drawPath(context, [previewNode(a), previewNode(b)], t); context.strokeStyle = "#ffe5a0"; context.lineWidth = 3 * t.dpr; context.setLineDash([7 * t.dpr, 4 * t.dpr]); context.stroke(); context.setLineDash([]); } }
    if (state.roadPlan) { const points = [state.roadPlan.start, ...(state.roadPlan.crossings || []), state.roadPlan.end]; drawPath(context, points, t); context.strokeStyle = "#ffe29a"; context.lineWidth = 3 * t.dpr; context.setLineDash([8*t.dpr, 5*t.dpr]); context.stroke(); context.setLineDash([]); for (const point of state.roadPlan.crossings || []) { const p = worldToScreen(point, t); context.beginPath(); context.arc(p[0], p[1], 5*t.dpr, 0, Math.PI*2); context.fillStyle = "#fff0b5"; context.fill(); } }
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
  async function createNode(point) { const aerial = state.activeDomain === "aerial", z = finite(els.newPointZ.value, "Z"); await edit("add_node", { position_xyz: [point[0], point[1], z], ...(aerial ? {} : { level_id: state.currentLevel }), label: "", kind: "custom", edit_mode: aerial ? els.newPointMode.value : undefined }, "节点已创建"); }
  function edgeNode(node) { if (!state.edgeStartId) { state.edgeStartId = node.id; state.edgeIdentity = identity(); select({ type: "node", id: node.id, domain: state.activeDomain }); updateToolUI(); return; } if (state.edgeStartId === node.id) { showError("边的起点和终点必须不同"); return; } state.edgeEndId = node.id; updateToolUI(); draw(); }
  function roadPoint(world) { return [world[0], world[1], finite(els.newPointZ.value, "Z")]; }
  function roadEnd(world, start) {
    const point = [world[0], world[1], state.roadDraft ? state.roadDraft.endZ : start[2]]; if (state.cHeld) return point;
    const dx = point[0]-start[0], dy = point[1]-start[1], angle = Math.atan2(Math.abs(dy), Math.abs(dx));
    if (angle <= Math.PI/18) point[1] = start[1];
    else if (Math.PI/2-angle <= Math.PI/18) point[0] = start[0];
    return point;
  }
  function adjustRoadEndHeight(delta) {
    if (!state.roadDraft || state.roadDraft.domain !== "aerial") return false;
    state.roadDraft.endZ += delta;
    if (state.roadDraft.screenEnd) refreshRoadPlan(screenToWorld(...state.roadDraft.screenEnd));
    updateToolUI();
    return true;
  }
  function adjustAerialRoadStartHeight(delta) {
    if (state.activeLayer !== "aerial" || !state.server || state.server.editor_tool !== "build-aerial-road") return false;
    els.newPointZ.value = String(finite(els.newPointZ.value, "Z") + delta);
    syncPointSettings();
    return true;
  }
  async function refreshRoadPlan(endWorld) {
    const draft = state.roadDraft; if (!draft) return;
    const sequence = ++state.roadRequest, start = draft.start, end = roadEnd(endWorld, start), t = transform();
    const body = identity({ start_xyz: start, end_xyz: end,
      ...(state.activeDomain === "ground" ? { level_id: state.currentLevel } : {}),
      snap_radius_m: 10*t.dpr/t.scale, disable_snap: state.cHeld, connect_intersections: true });
    try { const plan = await request("/api/road/preview", { method: "POST", body }); if (sequence === state.roadRequest && state.roadDraft) { state.roadPlan = plan; state.roadDraft.end = end; state.roadDraft.screenEnd = draft.screenEnd; draw(); return plan; } }
    catch (error) { if (sequence === state.roadRequest && error.status !== 409) { state.roadPlan = null; draw(); } }
  }
  function showRoadMenu(clientX, clientY) { const stage = els.mapStage.getBoundingClientRect(), menu = els.roadBuildMenu; hidden(menu, false); const width = menu.offsetWidth, height = menu.offsetHeight; menu.style.left = `${Math.max(4, Math.min(stage.width-width-4, clientX-stage.left))}px`; menu.style.top = `${Math.max(4, Math.min(stage.height-height-4, clientY-stage.top))}px`; }
  async function chooseRoadBuild(choice) {
    const draft = state.roadDraft; if (!draft) return;
    if (choice === "cancel") { cancelGesture(); return; }
    const connect = choice === "connect", end = draft.end;
    const operation = draft.domain === "aerial" ? "build_aerial_road" : "build_ground_road";
    const fresh = await edit(operation, { start_xyz: draft.start, end_xyz: end,
      ...(draft.domain === "ground" ? { level_id: state.currentLevel } : {}), snap_radius_m: draft.snapRadius, disable_snap: draft.disableSnap,
      connect_intersections: connect }, "道路已建设", draft.identity);
    if (fresh) cancelGesture();
  }
  async function confirmEdge() { if (!state.edgeStartId || !state.edgeEndId) return; const traversal = state.activeDomain === "ground" ? els.newEdgeTraversal.value : "walk"; const fresh = await edit("add_edge", { from_id: state.edgeStartId, to_id: state.edgeEndId, directed: false, traversal_mode: traversal }, "边已创建", state.edgeIdentity); if (fresh) cancelGesture(); }
  function parseProperties() { try { const value = JSON.parse(els.fieldProperties.value || "{}"); if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(); return value; } catch (_) { throw new Error("自定义属性必须是 JSON 对象"); } }
  async function applyInspector(event) {
    event.preventDefault(); const selection = state.selection, savedDraft = state.inspectorDraft, record = savedDraft ? savedDraft.record : recordFor(), expected = savedDraft ? savedDraft.identity : identity(); if (!selection || !record || !toolAllows(selection.type) || selection.type === "node" && state.server.editor_tool === "add-edge") return; let fresh;
    if (selection.type === "node") { fresh = await edit("update_node", { id: record.id, position_xyz: [finite(els.fieldX.value, "X"), finite(els.fieldY.value, "Y"), finite(els.fieldZ.value, "Z")], label: els.fieldLabel.value.trim(), kind: els.fieldKind.value.trim() || "custom", ...(state.activeDomain === "ground" ? { level_id: els.fieldLevel.value || null } : {}), ...(state.activeDomain === "aerial" ? { edit_mode: els.fieldEditMode.value } : {}) }, "节点属性已更新", expected); }
    else if (selection.type === "edge") fresh = await edit("update_edge", { id: record.id, directed: els.fieldDirected.checked, traversal_mode: els.fieldTraversal.value }, "边属性已更新", expected);
    else fresh = await edit("update_semantic_point", { id: record.id, label: els.fieldLabel.value.trim(), kind: els.fieldKind.value.trim() || "custom", description: els.fieldDescription.value, aliases: [...new Set(els.fieldAliases.value.split(",").map((value) => value.trim()).filter(Boolean))], properties: parseProperties() }, "标签属性已更新", expected);
    if (fresh) { state.inspectorDraft = null; renderInspector(); }
  }
  async function deleteSelection() { const items = selectionItems(); if (!items.length || state.server.editor_tool === "add-edge") return; if (items.some(i => i.type === "semantic_point") && !globalThis.confirm("删除整条语义将移除其全部导航绑定（共 " + items.reduce((n, i) => n + ((pointById(i.id) || {}).navigation_bindings || []).length, 0) + " 个），是否继续？")) return; const fresh = await edit("remove_selection", { items: items.map((item) => ({ kind: item.type, id: item.id })) }, "已删除；可撤销恢复"); if (fresh) { state.selectionItems = []; clearSelection(); } }
  async function applyNavigationBindings() {
    const value = state.selection, draft = state.navigationBindingDraft;
    if (!value || value.type !== "semantic_point" || !draft || draft.id !== value.id) return;
    const fresh = await edit("set_semantic_point_navigation_bindings", { id: value.id, bindings: draft.bindings }, "导航点绑定已更新");
    if (fresh) { state.navigationBindingDraft = null; renderInspector(); }
  }
  function syncNavigationBindingContext() {
    if (!state.bindingPickMode || !state.selection || state.selection.type !== "semantic_point" || !state.navigationBindingDraft) return;
    setContext({ binding_target_mode: "semantic_navigation", binding_target_id: state.selection.id,
      binding_target_draft: clone(state.navigationBindingDraft.bindings) }).catch(showError);
  }
  function toggleNavigationBindingFromMap(hit) {
    const point = recordFor();
    if (!state.bindingPickMode || !point || !state.selection
        || state.selection.type !== "semantic_point" || !state.navigationBindingDraft
        || !hit || hit.type !== "node" || !["ground", "aerial"].includes(hit.domain)) return false;
    const key = `${hit.domain}:${hit.id}`;
    const exists = state.navigationBindingDraft.bindings.some((row) => `${row.domain}:${row.node_id}` === key);
    state.navigationBindingDraft.bindings = exists
      ? state.navigationBindingDraft.bindings.filter((row) => `${row.domain}:${row.node_id}` !== key)
      : [...state.navigationBindingDraft.bindings, { domain: hit.domain, node_id: hit.id }];
    state.navigationBindingDraft.bindings.sort((a, b) => `${a.domain}:${a.node_id}`.localeCompare(`${b.domain}:${b.node_id}`));
    syncNavigationBindingContext();
    return true;
  }
  function cancelNavigationBindings() {
    state.bindingPickMode = false;
    state.navigationBindingDraft = null;
    setContext({ binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError);
    renderInspector(); updateToolUI(); draw();
  }
  function clearNavigationBindings() {
    if (!state.navigationBindingDraft) return;
    state.navigationBindingDraft.bindings = [];
    renderNavigationBindings(recordFor());
    if (state.bindingPickMode) syncNavigationBindingContext();
    draw();
  }
  function toggleNavigationPick() {
    cancelGesture();
    const record = recordFor();
    if (!record || !state.selection || state.selection.type !== "semantic_point") return;
    state.bindingPickMode = !state.bindingPickMode;
    els.pickNavigationBtn.textContent = state.bindingPickMode ? "结束连接" : "连接导航点";
    const draft = state.navigationBindingDraft && state.navigationBindingDraft.id === record.id
      ? state.navigationBindingDraft.bindings : (record.navigation_bindings || []);
    setContext(state.bindingPickMode
      ? { binding_target_mode: "semantic_navigation", binding_target_id: record.id, binding_target_draft: clone(draft) }
      : { binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError);
    renderNavigationBindings(record); updateToolUI(); draw();
    notice(state.bindingPickMode ? "连接模式已开启：地图点击可加入或移除地面、空中导航点；应用前仅修改草稿" : "已结束地图连接；草稿尚未应用");
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
    if (isRoadTool(mode) && state.activeLayer === state.activeDomain) {
      const start = roadPoint(world);
      state.roadDraft = { start, endZ: start[2], domain: state.activeDomain, screenStart: screen, moved: false, identity: identity(),
        snapRadius: 10*transform().dpr/transform().scale, disableSnap: state.cHeld };
      state.drag = { kind: "road", screenStart: screen, moved: false };
      updateToolUI(); els.mapCanvas.setPointerCapture(event.pointerId); return;
    }
    if (isRoadDeleteTool(mode) && hit && hit.type === "edge") {
      select({ type: "edge", id: hit.id, domain: state.activeDomain });
      edit("remove_edge", { id: hit.id }, "边已删除；端点节点保留").then(() => clearSelection()).catch(showError); return;
    }
    if (state.bindingPickMode && hit && hit.type === "node") {
      if (!toggleNavigationBindingFromMap(hit)) return;
      renderNavigationBindings(recordFor()); draw();
      return;
    }
    if (state.bindingPickMode) return;
    if (hit && hit.type === "node" && mode === "add-edge") { edgeNode(hit.record); return; }
    if (hit && hit.type === "node") {
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
    if (mode === "add-node") {
      state.box = { start: [event.clientX, event.clientY], end: [event.clientX, event.clientY] };
      state.drag = { kind: "box", screenStart: [event.clientX, event.clientY], moved: false, world };
      els.mapCanvas.setPointerCapture(event.pointerId);
    }
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
    if (state.drag.kind === "road") {
      state.roadDraft.moved = state.drag.moved;
      if (!state.roadDraft.moved) return;
      state.roadDraft.screenEnd = [event.clientX, event.clientY];
      refreshRoadPlan(world).catch(showError); return;
    }
    if (state.drag.kind === "box") {
      state.box.end = [event.clientX, event.clientY];
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
    } else if (state.drag.kind === "edge") {
      state.drag.delta = delta;
      state.preview = { kind: "edge", id: state.drag.id, delta };
    }
    renderInspector();
    draw();
  }
  async function onPointerUp(event) {
    const drag = state.drag;
    const preview = state.preview;
    state.drag = null;
    if (drag && drag.kind === "box") {
      if (!drag.moved) {
        state.box = null;
        if (state.activeLayer !== "semantic_points") createNode(drag.world).catch(showError);
      } else {
        const items = boxSelection(drag.screenStart, [event.clientX, event.clientY]);
        setSelectionItems(items);
      }
      state.box = null; draw(); return;
    }
    if (drag && drag.kind === "road") {
      if (drag.moved && event) await refreshRoadPlan(screenToWorld(event.clientX, event.clientY));
      if (!drag.moved || !state.roadPlan || !state.roadDraft) { cancelGesture(); return; }
      state.roadDraft.end = state.roadPlan.end; state.roadDraft.disableSnap = state.cHeld;
      state.roadDraft.snapRadius = 10*transform().dpr/transform().scale;
      showRoadMenu(event.clientX, event.clientY); return;
    }
    if (!drag || drag.kind === "pan" || !drag.moved || !preview) {
      state.preview = null;
      draw();
      return;
    }
    state.preview = null;
    if (drag.kind === "node") {
      await edit("move_node_xy", { id: drag.id, position_xy: preview.position.slice(0, 2) }, "节点已移动", drag.identity);
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
  function syncPointSettings() {
    const layer = state.activeLayer;
    const settings = { z: finite(els.newPointZ.value, "Z"), ...(layer === "ground" ? {} : { edit_mode: els.newPointMode.value }) };
    setContext({ new_point_settings: { [layer]: settings } }).catch(showError);
  }
  function bindEvents() {
    bindAnnotationEvents();
    els.closeErrorBtn.addEventListener("click", clearError); els.layerToggle.addEventListener("click", (event) => { const button = event.target.closest("[data-layer]"); if (!button) return; setContext({ active_layer: button.dataset.layer }).catch(showError); }); els.floorList.addEventListener("click", (event) => { const button = event.target.closest("[data-level]"); if (button) setContext({ active_layer: "ground", active_domain: "ground", level_id: button.dataset.level }).catch(showError); }); els.referenceLevel.addEventListener("change", () => setContext({ reference_level: els.referenceLevel.value || null }).catch(showError));
    [[els.showGround, "layer:ground"], [els.showAerial, "layer:aerial"], [els.showSemanticPoints, "layer:semantic_points"], [els.showNodes, "type:nodes"], [els.showEdges, "type:edges"], [els.showPointType, "type:semantic_points"]].forEach(([input, type]) => input.addEventListener("change", () => setVisibility(type, input.checked)));
    els.modeTools.addEventListener("click", (event) => { const button = event.target.closest("[data-mode]"); if (button) { const mode = button.dataset.mode; const activeLayer = mode === "add-semantic-point" ? "semantic_points" : mode === "build-ground-road" || mode === "delete-ground-edge" ? "ground" : mode === "build-aerial-road" || mode === "delete-aerial-edge" ? "aerial" : state.activeLayer === "semantic_points" ? state.activeDomain : state.activeLayer; setContext({ tool: mode, active_layer: activeLayer }).catch(showError); } });
    els.roadBuildMenu.addEventListener("click", (event) => { const button = event.target.closest("[data-road-choice]"); if (button) chooseRoadBuild(button.dataset.roadChoice).catch(showError); });
    els.recordSearch.addEventListener("input", renderRecords); els.recordTabs.addEventListener("click", (event) => { const button = event.target.closest("[data-filter]"); if (!button) return; state.filter = button.dataset.filter; [...els.recordTabs.querySelectorAll("[data-filter]")].forEach((item) => item.classList.toggle("active", item === button)); renderRecords(); }); els.recordList.addEventListener("click", (event) => { const button = event.target.closest("[data-id]"); if (button && toolAllows(button.dataset.type)) select({ type: button.dataset.type, id: button.dataset.id, ...(button.dataset.type !== "semantic_point" ? { domain: state.activeDomain } : {}) }); });
    const safePointSettings = () => { try { syncPointSettings(); } catch (error) { showError(error); } }; els.newPointZ.addEventListener("change", safePointSettings); els.newPointMode.addEventListener("change", safePointSettings); els.inspectorForm.addEventListener("submit", (event) => applyInspector(event).catch(showError)); els.inspectorForm.addEventListener("input", captureInspector); els.inspectorForm.addEventListener("change", captureInspector); els.deleteBtn.addEventListener("click", deleteSelection); els.navigationBindingSearch.addEventListener("input", () => { const record = recordFor(); if (record && state.selection.type === "semantic_point") renderNavigationBindings(record); }); els.applyNavigationBtn.addEventListener("click", () => applyNavigationBindings().catch(showError)); els.clearNavigationBtn.addEventListener("click", clearNavigationBindings); els.pickNavigationBtn.addEventListener("click", toggleNavigationPick); els.clearSelectionBtn.addEventListener("click", () => clearSelection()); els.cancelDraftBtn.addEventListener("click", cancelGesture); els.confirmEdgeBtn.addEventListener("click", confirmEdge); els.retryDraftBtn.addEventListener("click", retryPending); els.discardDraftBtn.addEventListener("click", () => { state.pendingEdit = null; state.inspectorDraft = null; cancelGesture(); renderPending(); renderInspector(); }); els.projectPicker.addEventListener("change", () => openProject(els.projectPicker.value)); els.categoryPicker.addEventListener("change", () => { renderProjectOptions(); if (els.projectPicker.value !== state.currentProjectId) openProject(els.projectPicker.value); }); els.undoBtn.addEventListener("click", () => write("/api/undo", {}, "已撤销")); els.redoBtn.addEventListener("click", () => write("/api/redo", {}, "已重做")); els.saveBtn.addEventListener("click", () => write("/api/save", {}, "已保存并导出")); els.fitBtn.addEventListener("click", () => { state.zoom = 1; state.panX = 0; state.panY = 0; draw(); }); els.zoomInBtn.addEventListener("click", () => { state.zoom = Math.min(10, state.zoom * 1.25); draw(); }); els.zoomOutBtn.addEventListener("click", () => { state.zoom = Math.max(.2, state.zoom * .8); draw(); }); els.mapCanvas.addEventListener("pointerdown", onPointerDown); els.mapCanvas.addEventListener("pointermove", onPointerMove); els.mapCanvas.addEventListener("pointerup", onPointerUp); els.mapCanvas.addEventListener("pointercancel", () => { state.drag = null; state.preview = null; draw(); }); els.mapCanvas.addEventListener("contextmenu", (event) => event.preventDefault()); els.mapCanvas.addEventListener("wheel", (event) => { event.preventDefault(); state.zoom = Math.max(.2, Math.min(10, state.zoom * (event.deltaY < 0 ? 1.12 : .89))); draw(); }, { passive: false }); new ResizeObserver(draw).observe(els.mapStage);
    globalThis.addEventListener("keydown", (event) => { const modifier = event.ctrlKey || event.metaKey; if ((event.key === "Delete" || event.key === "Backspace") && !isTextTarget(event.target)) { event.preventDefault(); deleteSelection().catch(showError); } else if (event.key === "PageUp" || event.key === "PageDown") { const delta = event.key === "PageUp" ? state.roadHeightStep : -state.roadHeightStep; const adjusted = state.drag && state.drag.kind === "road" ? adjustRoadEndHeight(delta) : adjustAerialRoadStartHeight(delta); if (adjusted) event.preventDefault(); } else if (event.key.toLowerCase() === "c" && !event.repeat) { state.cHeld = true; if (state.roadDraft && state.roadDraft.screenEnd) refreshRoadPlan(screenToWorld(...state.roadDraft.screenEnd)); } else if (modifier && event.key.toLowerCase() === "s") { event.preventDefault(); els.saveBtn.click(); } else if (modifier && event.key.toLowerCase() === "z") { event.preventDefault(); (event.shiftKey ? els.redoBtn : els.undoBtn).click(); } else if (modifier && event.key.toLowerCase() === "y") { event.preventDefault(); els.redoBtn.click(); } else if (event.key === "Escape") { event.preventDefault(); cancelGesture(); state.bindingPickMode = false; if (els.pickNavigationBtn) els.pickNavigationBtn.textContent = "连接导航点"; setContext({ binding_target_mode: null, binding_target_id: null, binding_target_draft: null }).catch(showError); } else if (event.key === "Enter" && state.edgeEndId) { event.preventDefault(); confirmEdge(); } });
    globalThis.addEventListener("keyup", (event) => { if (event.key.toLowerCase() === "c") { state.cHeld = false; if (state.roadDraft && state.roadDraft.screenEnd) refreshRoadPlan(screenToWorld(...state.roadDraft.screenEnd)); } });
    globalThis.addEventListener("blur", () => { state.cHeld = false; cancelGesture(); });
  }
  async function loadProjects() { try { const payload = await request("/api/projects"); state.projects = payload.projects || []; state.currentProjectId = payload.current_id || null; hidden(els.projectPickerWrap, state.projects.length < 2); const categories = [...new Set(state.projects.map((row) => row.category || "场景"))]; els.categoryPicker.textContent = ""; categories.forEach((value) => { const option = document.createElement("option"); option.value = value; option.textContent = value; els.categoryPicker.appendChild(option); }); const current = state.projects.find((row) => row.id === state.currentProjectId); if (current) els.categoryPicker.value = current.category || "场景"; renderProjectOptions(); } catch (_) { hidden(els.projectPickerWrap, true); } }
  function renderProjectOptions() { els.projectPicker.textContent = ""; state.projects.filter((row) => (row.category || "场景") === els.categoryPicker.value).forEach((row) => { const option = document.createElement("option"); option.value = row.id; option.textContent = row.display_name || row.scene_key || row.id; els.projectPicker.appendChild(option); }); if ([...els.projectPicker.options].some((option) => option.value === state.currentProjectId)) els.projectPicker.value = state.currentProjectId; }
  async function openProject(id) { if (!id || id === state.currentProjectId) return; if (state.server.dirty && !globalThis.confirm("当前工程有未保存修改，是否放弃修改并切换场景？")) { renderProjectOptions(); return; } const fresh = await write("/api/open", { id, discard_unsaved: true }, "场景已切换"); if (fresh) await loadProjects(); else renderProjectOptions(); }
  async function init() { bindEvents(); state.pollTimer = setInterval(poll, 500); try { const [fresh] = await Promise.all([request("/api/state"), loadProjects()]); acceptState(fresh); state.selectionSent = JSON.stringify(fresh.web_selection || null); } catch (error) { hidden(els.loadingLayer, true); showError(`无法加载地图项目：${error.message}`); } }
  init();
})();
