"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const els = {};
  [
    "sceneSubtitle","projectPickerWrap","categoryPicker","projectPicker","sceneSyncStatus",
    "dirtyBadge","revisionBadge","undoBtn","redoBtn","saveBtn","errorBanner","errorText",
    "closeErrorBtn","noticeBanner","projectPath","domainToggle","floorList","recordCount",
    "recordSearch","recordTabs","recordList","warningsSection","warningsList","modeTools",
    "modeHint","cancelDraftBtn","confirmPolygonBtn","zoomOutBtn","zoomInBtn","fitBtn",
    "mapStage","mapCanvas","loadingLayer","cursorCoords","selectionType","selectionTitle",
    "clearSelectionBtn","emptyInspector","inspectorForm","fieldId","labelGroup","fieldLabel",
    "kindGroup","fieldKind","levelGroup","fieldLevel","coordinateGroup","fieldX","fieldY",
    "fieldZ","editModeGroup","fieldEditMode","heightGroup","fieldHeight","vertexGroup",
    "addVertexBtn","vertexHint","edgeGroup","fieldFrom","fieldTo","fieldDirected",
    "deleteBtn","applyBtn","newNodeModeGroup","newNodeMode","confirmEdgeBtn",
    "newEdgeTraversalGroup","newEdgeTraversal",
    "pendingDraftBanner","pendingDraftText","retryDraftBtn","discardDraftBtn","fieldTraversal"
  ].forEach((id) => { els[id] = $(id); });

  class ConflictError extends Error {}

  const state = {
    server: null,
    projects: [],
    currentProjectId: null,
    currentLevel: null,
    filter: "all",
    selection: null,
    boundary: null,
    regionDraft: [],
    edgeStartId: null,
    edgeEndId: null,
    edgeIdentity: null,
    regionIdentity: null,
    pendingEdit: null,
    inspectorDraft: null,
    disconnected: false,
    preview: null,
    drag: null,
    zoom: 1,
    panX: 0,
    panY: 0,
    layerImage: null,
    layerToken: 0,
    pollTimer: null,
    polling: false,
    writing: false,
    selectionSent: "null",
    suppressSelectionPush: false
  };

  const hints = {
    "add-node": "空白处添加节点；点击已有元素可选择并拖动",
    "add-edge": "依次点击两个节点连边；点击已有边可选择",
    "add-region": "逐点绘制区域；点击首点或“完成区域”闭合"
  };

  const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
  const hidden = (element, value) => element.classList.toggle("hidden", Boolean(value));
  const finite = (value, name) => {
    const result = Number(value);
    if (!Number.isFinite(result)) throw new Error(`${name}必须是有限数字`);
    return result;
  };
  const uniqueId = (prefix) => `${prefix}:${globalThis.crypto && crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2)}`;

  function showError(value) {
    els.errorText.textContent = value instanceof Error ? value.message : String(value);
    hidden(els.errorBanner, false);
  }
  function clearError() { hidden(els.errorBanner, true); }
  let noticeTimer = null;
  function notice(text) {
    els.noticeBanner.textContent = text;
    hidden(els.noticeBanner, false);
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => hidden(els.noticeBanner, true), 2600);
  }

  async function request(path, options = {}) {
    const config = { ...options, headers: { ...(options.headers || {}) } };
    if (config.body && typeof config.body === "object") {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(config.body);
    }
    const response = await fetch(path, config);
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok) {
      const error = response.status === 409
        ? new ConflictError(payload.error || "编辑上下文已变化")
        : new Error(payload.error || `请求失败（HTTP ${response.status}）`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function identity(extra = {}) {
    const value = state.server;
    if (!value) throw new Error("编辑器尚未连接");
    return {
      revision: value.revision,
      csrf_token: value.csrf_token,
      scene_key: value.scene_key,
      scene_generation: value.scene_generation,
      context_generation: value.context_generation,
      domain: value.active_domain,
      ...extra
    };
  }

  async function refresh({ preserveDraft = true } = {}) {
    const fresh = await request("/api/state");
    acceptState(fresh, { preserveDraft });
    return fresh;
  }

  async function write(path, extra, success, expected = null) {
    if (state.writing) return null;
    state.writing = true;
    clearError();
    const captured = expected || identity();
    try {
      const fresh = await request(path, { method: "POST", body: { ...captured, ...extra } });
      acceptState(fresh, { preserveSelection: true, preserveDraft: true });
      if (success) notice(success);
      return fresh;
    } catch (error) {
      if (error instanceof ConflictError || !error.status) {
        if (path === "/api/edit") state.pendingEdit = { extra: clone(extra), identity: captured, level: state.currentLevel, uncertain: !error.status };
        try { await refresh({ preserveDraft: true }); } catch (_) { state.disconnected = true; cancelGesture(); }
        renderPending();
        showError(error.status ? "编辑未提交，草稿已保留。请确认当前图和楼层后重新提交。"
          : "连接中断，提交结果未知。请核对当前数据；草稿已保留。请勿重复创建相同元素。");
        return null;
      }
      showError(error);
      return null;
    } finally {
      state.writing = false;
    }
  }

  const edit = (operation, args, success, expected = null) => write("/api/edit", { operation, ...args }, success, expected);
  const documentValue = () => state.server && state.server.document || {};
  const nodes = () => Array.isArray(documentValue().nodes) ? documentValue().nodes : [];
  const edges = () => Array.isArray(documentValue().edges) ? documentValue().edges : [];
  const regions = () => {
    const semantics = documentValue().semantics || {};
    return Array.isArray(semantics.regions) ? semantics.regions : [];
  };
  const levels = () => state.server && Array.isArray(state.server.levels) ? state.server.levels : [];
  const nodeById = (id) => nodes().find((row) => row.id === id) || null;
  const edgeById = (id) => edges().find((row) => row.id === id) || null;
  const regionById = (id) => regions().find((row) => row.id === id) || null;
  const visibleNode = (row) => row && (row.level_id == null || row.level_id === state.currentLevel);
  const visibleEdge = (row) => visibleNode(nodeById(row.from)) || visibleNode(nodeById(row.to));
  function recordFor(selection = state.selection) {
    if (!selection) return null;
    if (selection.type === "node") return nodeById(selection.id);
    if (selection.type === "edge") return edgeById(selection.id);
    if (selection.type === "region") return regionById(selection.id);
    return null;
  }

  function contextKey(value) {
    return value ? `${value.scene_key}:${value.scene_generation}:${value.context_generation}:${value.active_domain}:${value.active_level}` : "";
  }

  function cancelGesture({ keepRegionDraft = false } = {}) {
    state.preview = null;
    state.drag = null;
    state.edgeStartId = null;
    state.edgeEndId = null;
    state.edgeIdentity = null;
    state.boundary = null;
    if (!keepRegionDraft) { state.regionDraft = []; state.regionIdentity = null; }
    updateToolUI();
    draw();
  }

  function renderPending() {
    const pending = state.pendingEdit;
    hidden(els.pendingDraftBanner, !pending);
    els.pendingDraftText.textContent = pending && pending.uncertain
      ? "提交结果未知，请核对当前数据。草稿已保留，请确认后丢弃或重新操作。"
      : "有待处理的编辑草稿。确认当前状态后可重新提交。";
    els.retryDraftBtn.disabled = !pending || pending.uncertain || !state.server || state.disconnected
      || pending.identity.scene_key !== state.server.scene_key
      || pending.identity.scene_generation !== state.server.scene_generation
      || pending.identity.domain !== state.server.active_domain || pending.level !== state.currentLevel;
  }

  async function retryPending() {
    const pending = state.pendingEdit;
    if (!pending || els.retryDraftBtn.disabled) return;
    const fresh = await write("/api/edit", pending.extra, "草稿已提交", identity());
    if (fresh) { state.pendingEdit = null; state.inspectorDraft = null; cancelGesture(); renderPending(); renderInspector(); }
  }

  function selectionFromServer(value) {
    if (!value) return null;
    if (value.kind === "node" && nodeById(value.id)) return { type: "node", id: value.id };
    if (value.kind === "edge" && edgeById(value.id)) return { type: "edge", id: value.id };
    if (value.kind === "semantic" && value.collection === "regions" && regionById(value.id)) {
      return { type: "region", id: value.id };
    }
    return null;
  }

  function acceptState(fresh, options = {}) {
    if (!fresh || typeof fresh !== "object" || !fresh.document) throw new Error("服务端状态无效");
    const oldKey = contextKey(state.server);
    const previous = state.selection;
    const draft = clone(state.regionDraft);
    state.server = fresh;
    state.currentLevel = fresh.active_level || (levels()[0] && levels()[0].id) || null;
    const changed = Boolean(oldKey && oldKey !== contextKey(fresh));
    if (changed) {
      state.inspectorDraft = null;
      state.selectionSent = JSON.stringify(fresh.web_selection || null);
      cancelGesture();
    }
    else if (options.preserveDraft) state.regionDraft = draft;
    let next = selectionFromServer(fresh.web_selection);
    if (!changed && previous && recordFor(previous) && (state.inspectorDraft || !next && options.preserveSelection)) next = previous;
    if (!state.drag) state.selection = next;
    renderAll();
  }

  function renderAll() {
    const fresh = state.server;
    els.sceneSubtitle.textContent = fresh ? `${fresh.scene_key} · ${fresh.active_domain === "aerial" ? "空中图" : "地面图"}` : "正在连接…";
    els.projectPath.textContent = fresh && (fresh.source_path || fresh.project_path) || "";
    els.revisionBadge.textContent = fresh ? `修订 ${fresh.revision}` : "修订 —";
    els.dirtyBadge.textContent = fresh && fresh.dirty ? "有未保存修改" : "已保存";
    els.dirtyBadge.className = `status-badge ${fresh && fresh.dirty ? "dirty" : "saved"}`;
    els.undoBtn.disabled = !fresh || !fresh.can_undo;
    els.redoBtn.disabled = !fresh || !fresh.can_redo;
    [...els.domainToggle.querySelectorAll("[data-domain]")].forEach((button) =>
      button.classList.toggle("active", fresh && button.dataset.domain === fresh.active_domain));
    renderFloors();
    renderRecords();
    renderWarnings();
    renderInspector();
    renderPending();
    const runtime = fresh && fresh.isaac_runtime;
    els.sceneSyncStatus.textContent = !runtime || runtime.state === "disabled" ? "仅网页模式"
      : runtime.state === "ready" ? "Isaac Sim 场景已同步"
      : runtime.state === "error" ? `Isaac: ${runtime.error || "连接失败"}` : "Isaac 场景加载中…";
    updateToolUI();
    loadLayer();
    draw();
  }

  function renderFloors() {
    els.floorList.textContent = "";
    levels().forEach((level) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `floor-item ${level.id === state.currentLevel ? "active" : ""}`;
      button.dataset.level = level.id;
      button.innerHTML = `<strong></strong><span></span><b></b>`;
      button.querySelector("strong").textContent = level.label || level.id;
      button.querySelector("span").textContent = level.id;
      button.querySelector("b").textContent = `Z ${Number(level.support_z_m).toFixed(2)}`;
      els.floorList.appendChild(button);
    });
    els.fieldLevel.textContent = "";
    const noLevel = document.createElement("option"); noLevel.value = ""; noLevel.textContent = "跨层 / 未绑定楼层"; els.fieldLevel.appendChild(noLevel);
    levels().forEach((level) => {
      const option = document.createElement("option");
      option.value = level.id;
      option.textContent = level.label || level.id;
      els.fieldLevel.appendChild(option);
    });
  }

  function visibleRecords() {
    const query = els.recordSearch.value.trim().toLowerCase();
    const rows = [
      ...nodes().map((record) => ({ type: "node", record })),
      ...edges().map((record) => ({ type: "edge", record })),
      ...regions().map((record) => ({ type: "region", record }))
    ];
    return rows.filter((item) => {
      const category = item.type === "node" ? "nodes" : item.type === "edge" ? "edges" : "regions";
      return (state.filter === "all" || state.filter === category)
        && (!query || `${item.record.id} ${item.record.label || ""}`.toLowerCase().includes(query));
    });
  }

  function renderRecords() {
    const rows = visibleRecords();
    els.recordCount.textContent = String(rows.length);
    els.recordList.textContent = "";
    rows.forEach(({ type, record }) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `record-item ${state.selection && state.selection.type === type && state.selection.id === record.id ? "active" : ""}`;
      button.dataset.type = type;
      button.dataset.id = record.id;
      button.innerHTML = `<span class="record-symbol ${type}"></span><span class="record-copy"><strong></strong><span></span></span>`;
      button.querySelector(".record-symbol").textContent = type === "node" ? "●" : type === "edge" ? "↔" : "▱";
      button.querySelector("strong").textContent = record.label || record.id;
      button.querySelector(".record-copy span").textContent = record.id;
      els.recordList.appendChild(button);
    });
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "record-empty";
      empty.textContent = "当前筛选没有记录";
      els.recordList.appendChild(empty);
    }
  }

  function renderWarnings() {
    const warnings = state.server && state.server.warnings || [];
    hidden(els.warningsSection, !warnings.length);
    els.warningsList.textContent = "";
    warnings.forEach((warning) => {
      const li = document.createElement("li");
      li.textContent = warning;
      els.warningsList.appendChild(li);
    });
  }

  function setGroup(element, show) { hidden(element, !show); }
  function renderInspector() {
    const record = recordFor();
    hidden(els.emptyInspector, Boolean(record));
    hidden(els.inspectorForm, !record);
    hidden(els.clearSelectionBtn, !record);
    if (!record) {
      els.selectionType.textContent = "未选择";
      els.selectionTitle.textContent = "属性检查器";
      return;
    }
    const type = state.selection.type;
    els.fieldId.value = record.id;
    els.fieldLabel.value = record.label || "";
    els.fieldKind.value = record.kind || "";
    els.fieldLevel.value = record.level_id || "";
    setGroup(els.labelGroup, type !== "edge");
    setGroup(els.kindGroup, type !== "edge");
    setGroup(els.levelGroup, type !== "edge");
    setGroup(els.coordinateGroup, type === "node");
    setGroup(els.editModeGroup, type === "node" && state.server.active_domain === "aerial");
    setGroup(els.heightGroup, type === "region");
    setGroup(els.vertexGroup, type === "region");
    setGroup(els.edgeGroup, type === "edge");
    hidden(els.addVertexBtn, !(type === "region" && state.boundary && state.boundary.id === record.id));
    if (type === "node") {
      const point = previewNode(record);
      [els.fieldX.value, els.fieldY.value, els.fieldZ.value] = point.map((value) => Number(value).toFixed(3));
      els.fieldEditMode.value = record.edit_mode || "3d";
      els.selectionType.textContent = state.server.active_domain === "aerial" ? "空中节点" : "地面节点";
    } else if (type === "edge") {
      els.fieldFrom.value = record.from;
      els.fieldTo.value = record.to;
      els.fieldDirected.checked = Boolean(record.directed);
      els.fieldTraversal.value = record.traversal_mode || "walk";
      els.selectionType.textContent = "拓扑边";
    } else {
      els.fieldHeight.value = String(record.height_m == null ? 2.5 : record.height_m);
      els.selectionType.textContent = "共享区域";
    }
    els.selectionTitle.textContent = record.label || record.id;
    const draft = state.inspectorDraft;
    if (draft && draft.selection.type === type && draft.selection.id === record.id) {
      for (const [key, value] of Object.entries(draft.values)) {
        if (key === "fieldDirected") els[key].checked = value;
        else els[key].value = value;
      }
    }
  }

  function captureInspector() {
    const record = recordFor();
    if (!record || !state.selection) return;
    if (!state.inspectorDraft) state.inspectorDraft = { selection: clone(state.selection), record: clone(record), identity: identity() };
    state.inspectorDraft.values = {};
    for (const key of ["fieldLabel", "fieldKind", "fieldLevel", "fieldX", "fieldY", "fieldZ", "fieldEditMode", "fieldHeight", "fieldTraversal"])
      state.inspectorDraft.values[key] = els[key].value;
    state.inspectorDraft.values.fieldDirected = els.fieldDirected.checked;
  }

  function updateToolUI() {
    const mode = state.server && state.server.editor_tool || "add-node";
    [...els.modeTools.querySelectorAll("[data-mode]")].forEach((button) =>
      button.classList.toggle("active", button.dataset.mode === mode));
    els.modeHint.textContent = state.edgeEndId ? "连线预览已就绪，点击确认连线"
      : state.edgeStartId ? `已选择起点 ${state.edgeStartId}，请选择终点`
      : state.regionDraft.length
        ? `区域草稿 ${state.regionDraft.length} 点；点击首点或完成按钮闭合`
        : hints[mode];
    hidden(els.cancelDraftBtn, !(state.regionDraft.length || state.edgeStartId || state.preview));
    hidden(els.confirmPolygonBtn, !(mode === "add-region" && state.regionDraft.length >= 3));
    hidden(els.confirmEdgeBtn, !state.edgeEndId);
    hidden(els.newNodeModeGroup, !(mode === "add-node" && state.server && state.server.active_domain === "aerial"));
    hidden(els.newEdgeTraversalGroup, !(mode === "add-edge" && state.server && state.server.active_domain === "ground"));
  }

  async function setContext(changes) {
    if (!state.server) return;
    const body = identity({
      active_domain: changes.domain || state.server.active_domain,
      tool: changes.tool || state.server.editor_tool,
      level_id: changes.level_id || state.currentLevel
    });
    await write("/api/editor/context", body);
  }

  function selectionPayload() {
    const value = state.selection;
    if (!value) return null;
    if (value.type === "node" || value.type === "edge") return { kind: value.type, collection: null, id: value.id };
    if (value.type === "region") return { kind: "semantic", collection: "regions", id: value.id };
    return null;
  }

  async function pushSelection() {
    if (!state.server || state.suppressSelectionPush) return;
    const selection = selectionPayload();
    const signature = JSON.stringify(selection);
    if (signature === state.selectionSent) return;
    try {
      await request("/api/web/selection", { method: "POST", body: identity({ selection }) });
      state.selectionSent = signature;
    } catch (error) {
      if (!(error instanceof ConflictError)) showError(error);
    }
  }

  function select(value, { push = true } = {}) {
    if (JSON.stringify(value) !== JSON.stringify(state.selection)) state.inspectorDraft = null;
    state.selection = value;
    state.boundary = null;
    renderRecords();
    renderInspector();
    draw();
    if (push) pushSelection();
  }

  function clearSelection(push = true) { select(null, { push }); }
  function currentExtent() {
    const level = levels().find((row) => row.id === state.currentLevel);
    return level && level.extent_xy || [-10, 10, -10, 10];
  }
  function canvasMetrics() {
    const rect = els.mapCanvas.getBoundingClientRect();
    const dpr = globalThis.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (els.mapCanvas.width !== width || els.mapCanvas.height !== height) {
      els.mapCanvas.width = width; els.mapCanvas.height = height;
    }
    return { rect, dpr, width, height };
  }
  function transform() {
    const metrics = canvasMetrics();
    const [xmin, xmax, ymin, ymax] = currentExtent().map(Number);
    const scale = Math.min(metrics.width / Math.max(.001, xmax - xmin), metrics.height / Math.max(.001, ymax - ymin)) * .92 * state.zoom;
    return { ...metrics, scale, cx: metrics.width / 2 + state.panX * metrics.dpr, cy: metrics.height / 2 + state.panY * metrics.dpr, mx: (xmin + xmax) / 2, my: (ymin + ymax) / 2 };
  }
  function worldToScreen(point, t = transform()) { return [t.cx + (point[0] - t.mx) * t.scale, t.cy - (point[1] - t.my) * t.scale]; }
  function screenToWorld(clientX, clientY) {
    const t = transform();
    const x = (clientX - t.rect.left) * t.dpr;
    const y = (clientY - t.rect.top) * t.dpr;
    return [t.mx + (x - t.cx) / t.scale, t.my - (y - t.cy) / t.scale];
  }
  function previewNode(node) {
    return state.preview && state.preview.kind === "node" && state.preview.id === node.id
      ? state.preview.position : node.position_xyz;
  }
  function previewRegion(region) {
    const points = clone(region.polygon_xyz || []);
    if (state.preview && state.preview.kind === "vertex" && state.preview.id === region.id && points[state.preview.index]) {
      points[state.preview.index] = [state.preview.position[0], state.preview.position[1], points[state.preview.index][2]];
    }
    return points;
  }

  function distanceSegment(point, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const length = dx * dx + dy * dy;
    const ratio = length ? Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) : 0;
    const projection = [a[0] + ratio * dx, a[1] + ratio * dy];
    return { distance: Math.hypot(point[0] - projection[0], point[1] - projection[1]), projection };
  }
  function pointInPolygon(point, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const a = polygon[i], b = polygon[j];
      if ((a[1] > point[1]) !== (b[1] > point[1])
          && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / ((b[1] - a[1]) || 1e-12) + a[0]) inside = !inside;
    }
    return inside;
  }

  function hitTest(clientX, clientY) {
    const world = screenToWorld(clientX, clientY);
    const t = transform();
    const tolerance = 10 * t.dpr / t.scale;
    for (const region of regions().slice().reverse()) {
      if (region.level_id !== state.currentLevel) continue;
      const polygon = previewRegion(region);
      if (state.selection && state.selection.type === "region" && state.selection.id === region.id) {
        for (let i = polygon.length - 1; i >= 0; i--) {
          if (Math.hypot(world[0] - polygon[i][0], world[1] - polygon[i][1]) <= tolerance) {
            return { type: "vertex", id: region.id, index: i, record: region };
          }
        }
      }
    }
    for (const node of nodes().slice().reverse()) {
      if (node.level_id != null && node.level_id !== state.currentLevel) continue;
      const point = previewNode(node);
      if (Math.hypot(world[0] - point[0], world[1] - point[1]) <= tolerance) return { type: "node", id: node.id, record: node };
    }
    for (const edge of edges().slice().reverse()) {
      if (!visibleEdge(edge)) continue;
      const line = Array.isArray(edge.polyline_xyz) && edge.polyline_xyz.length > 1
        ? edge.polyline_xyz : [nodeById(edge.from)?.position_xyz, nodeById(edge.to)?.position_xyz].filter(Boolean);
      for (let index = 0; index + 1 < line.length; index++) {
        if (distanceSegment(world, line[index], line[index + 1]).distance <= tolerance) return { type: "edge", id: edge.id, record: edge };
      }
    }
    for (const region of regions().slice().reverse()) {
      if (region.level_id !== state.currentLevel) continue;
      const polygon = previewRegion(region);
      let best = null;
      for (let index = 0; index < polygon.length; index++) {
        const result = distanceSegment(world, polygon[index], polygon[(index + 1) % polygon.length]);
        if (!best || result.distance < best.distance) best = { ...result, index };
      }
      if (best && best.distance <= tolerance) return { type: "boundary", id: region.id, edgeIndex: best.index, projection: best.projection, record: region };
      if (pointInPolygon(world, polygon)) return { type: "region", id: region.id, record: region };
    }
    return null;
  }

  function drawPath(context, points, t, close = false) {
    if (!points.length) return;
    context.beginPath();
    points.forEach((point, index) => {
      const screen = worldToScreen(point, t);
      if (index) context.lineTo(...screen); else context.moveTo(...screen);
    });
    if (close) context.closePath();
  }
  function draw() {
    if (!state.server) return;
    const t = transform(), context = els.mapCanvas.getContext("2d");
    context.clearRect(0, 0, t.width, t.height);
    context.fillStyle = "#080d13"; context.fillRect(0, 0, t.width, t.height);
    if (state.layerImage) {
      const [xmin, xmax, ymin, ymax] = currentExtent();
      const a = worldToScreen([xmin, ymax], t), b = worldToScreen([xmax, ymin], t);
      context.globalAlpha = .86; context.drawImage(state.layerImage, a[0], a[1], b[0] - a[0], b[1] - a[1]); context.globalAlpha = 1;
    }
    for (const region of regions()) {
      if (region.level_id !== state.currentLevel) continue;
      const selected = state.selection && state.selection.type === "region" && state.selection.id === region.id;
      const polygon = previewRegion(region);
      drawPath(context, polygon, t, true);
      context.fillStyle = selected ? "rgba(197,151,255,.22)" : "rgba(157,101,220,.1)";
      context.strokeStyle = selected ? "#ead9ff" : "#b283e3";
      context.lineWidth = (selected ? 3 : 2) * t.dpr; context.fill(); context.stroke();
      if (selected) polygon.forEach((point, index) => {
        const screen = worldToScreen(point, t);
        context.beginPath(); context.arc(screen[0], screen[1], 5 * t.dpr, 0, Math.PI * 2);
        context.fillStyle = state.preview && state.preview.kind === "vertex" && state.preview.index === index ? "#fff" : "#c39bef";
        context.fill(); context.strokeStyle = "#1d102b"; context.lineWidth = t.dpr; context.stroke();
      });
    }
    for (const edge of edges()) {
      const a = nodeById(edge.from), b = nodeById(edge.to);
      if (!a || !b || !visibleEdge(edge)) continue;
      const line = edge.polyline_xyz && edge.polyline_xyz.length > 1 ? clone(edge.polyline_xyz) : [previewNode(a), previewNode(b)];
      line[0] = previewNode(a); line[line.length - 1] = previewNode(b);
      drawPath(context, line, t);
      const selected = state.selection && state.selection.type === "edge" && state.selection.id === edge.id;
      context.strokeStyle = selected ? "#e8fff8" : state.server.active_domain === "aerial" ? "#6ab5ff" : "#2dd4a7";
      context.lineWidth = (selected ? 4 : 2.5) * t.dpr; context.stroke();
      if (edge.directed) {
        const p = worldToScreen(line[line.length - 1], t), q = worldToScreen(line[line.length - 2], t);
        const angle = Math.atan2(p[1] - q[1], p[0] - q[0]);
        context.beginPath(); context.moveTo(p[0], p[1]); context.lineTo(p[0] - 10 * t.dpr * Math.cos(angle - .45), p[1] - 10 * t.dpr * Math.sin(angle - .45)); context.lineTo(p[0] - 10 * t.dpr * Math.cos(angle + .45), p[1] - 10 * t.dpr * Math.sin(angle + .45)); context.closePath(); context.fillStyle = context.strokeStyle; context.fill();
      }
    }
    for (const node of nodes()) {
      if (node.level_id != null && node.level_id !== state.currentLevel) continue;
      const point = worldToScreen(previewNode(node), t);
      const selected = state.selection && state.selection.type === "node" && state.selection.id === node.id;
      context.beginPath(); context.arc(point[0], point[1], (selected ? 7 : 5) * t.dpr, 0, Math.PI * 2);
      context.fillStyle = selected ? "#fff" : state.server.active_domain === "aerial" ? "#6ab5ff" : "#43dfb8";
      context.fill(); context.strokeStyle = "#07150f"; context.lineWidth = 2 * t.dpr; context.stroke();
    }
    if (state.edgeStartId) {
      const a = nodeById(state.edgeStartId), b = nodeById(state.edgeEndId);
      if (a && b) {
        drawPath(context, [previewNode(a), previewNode(b)], t);
        context.strokeStyle = "#ffe5a0"; context.lineWidth = 3 * t.dpr; context.setLineDash([7 * t.dpr, 4 * t.dpr]); context.stroke(); context.setLineDash([]);
      }
    }
    if (state.regionDraft.length) {
      drawPath(context, state.regionDraft, t);
      context.strokeStyle = "#d5b5ff"; context.lineWidth = 2 * t.dpr; context.setLineDash([6 * t.dpr, 4 * t.dpr]); context.stroke(); context.setLineDash([]);
      state.regionDraft.forEach((point, index) => {
        const screen = worldToScreen(point, t);
        context.beginPath(); context.arc(screen[0], screen[1], (index ? 4 : 6) * t.dpr, 0, Math.PI * 2);
        context.fillStyle = index ? "#b88ce8" : "#fff"; context.fill();
      });
    }
  }

  async function loadLayer() {
    const level = levels().find((row) => row.id === state.currentLevel);
    if (!level) return;
    const token = ++state.layerToken;
    hidden(els.loadingLayer, false);
    const image = new Image();
    image.onload = () => {
      if (token !== state.layerToken) return;
      state.layerImage = image; hidden(els.loadingLayer, true); draw();
    };
    image.onerror = () => { if (token === state.layerToken) hidden(els.loadingLayer, true); };
    image.src = `/api/layer?level=${encodeURIComponent(level.id)}&revision=${state.server.revision}`;
  }

  async function createNode(point) {
    const z = Number(levels().find((row) => row.id === state.currentLevel)?.support_z_m || 0);
    await edit("add_node", {
      position_xyz: [point[0], point[1], z],
      level_id: state.currentLevel,
      label: "",
      kind: "custom",
      edit_mode: state.server.active_domain === "aerial" ? els.newNodeMode.value : undefined
    }, "节点已创建");
  }

  async function edgeNode(node) {
    if (!state.edgeStartId) {
      state.edgeStartId = node.id; state.edgeIdentity = identity(); select({ type: "node", id: node.id }); updateToolUI(); return;
    }
    if (state.edgeStartId === node.id) { showError("边的起点和终点必须不同"); return; }
    state.edgeEndId = node.id;
    updateToolUI(); draw();
  }

  async function confirmEdge() {
    if (!state.edgeStartId || !state.edgeEndId) return;
    const traversal = state.server.active_domain === "ground" ? els.newEdgeTraversal.value : "walk";
    const fresh = await edit("add_edge", { from_id: state.edgeStartId, to_id: state.edgeEndId, directed: false, traversal_mode: traversal }, "边已创建", state.edgeIdentity);
    if (fresh) cancelGesture();
  }

  async function finishRegion() {
    if (state.regionDraft.length < 3) return;
    const polygon = state.regionDraft.map((point) => [point[0], point[1], Number(levels().find((row) => row.id === state.currentLevel)?.support_z_m || 0)]);
    const record = { id: uniqueId("region"), label: "", kind: "room", level_id: state.currentLevel, polygon_xyz: polygon, height_m: 2.5, aliases: [], properties: {}, entrance_node_ids: [] };
    const fresh = await edit("upsert_semantic", { collection: "regions", record }, "区域已创建", state.regionIdentity);
    if (fresh) { state.regionDraft = []; state.regionIdentity = null; }
    updateToolUI(); draw();
  }

  async function applyInspector(event) {
    event.preventDefault();
    const selection = state.selection;
    const savedDraft = state.inspectorDraft;
    const record = savedDraft ? savedDraft.record : recordFor();
    const expected = savedDraft ? savedDraft.identity : identity();
    if (!selection || !record) return;
    let fresh = null;
    if (selection.type === "node") {
      const point = [finite(els.fieldX.value, "X"), finite(els.fieldY.value, "Y"), finite(els.fieldZ.value, "Z")];
      fresh = await edit("update_node", {
        id: record.id,
        position_xyz: point,
        label: els.fieldLabel.value.trim(),
        kind: els.fieldKind.value.trim() || "custom",
        level_id: els.fieldLevel.value || null,
        ...(state.server.active_domain === "aerial" ? { edit_mode: els.fieldEditMode.value } : {})
      }, "节点属性已更新", expected);
    } else if (selection.type === "edge") {
      fresh = await edit("update_edge", { id: record.id, directed: els.fieldDirected.checked, traversal_mode: els.fieldTraversal.value }, "边方向已更新", expected);
    } else {
      const next = clone(record);
      next.label = els.fieldLabel.value.trim();
      next.kind = els.fieldKind.value.trim() || "room";
      next.level_id = els.fieldLevel.value;
      next.height_m = finite(els.fieldHeight.value, "区域高度");
      fresh = await edit("upsert_semantic", { collection: "regions", record: next }, "区域属性已更新", expected);
    }
    if (fresh) { state.inspectorDraft = null; renderInspector(); }
  }

  async function deleteSelection() {
    const value = state.selection;
    if (!value) return;
    const operation = value.type === "node" ? "remove_node" : value.type === "edge" ? "remove_edge" : "remove_semantic";
    const args = value.type === "region" ? { collection: "regions", id: value.id } : { id: value.id };
    const fresh = await edit(operation, args, "已删除；可撤销恢复");
    if (fresh) clearSelection();
  }

  async function addBoundaryVertex() {
    const boundary = state.boundary;
    const region = boundary && regionById(boundary.id);
    if (!region) return;
    const z = region.polygon_xyz[boundary.edgeIndex][2];
    const fresh = await edit("insert_region_vertex", {
      id: boundary.id,
      index: boundary.edgeIndex,
      position_xyz: [boundary.projection[0], boundary.projection[1], z]
    }, "区域顶点已添加", boundary.identity);
    if (fresh) {
      state.boundary = null;
      select({ type: "region", id: region.id });
    }
  }

  function onPointerDown(event) {
    if (!state.server || state.disconnected || state.writing) return;
    const world = screenToWorld(event.clientX, event.clientY);
    const hit = hitTest(event.clientX, event.clientY);
    const mode = state.server.editor_tool;
    if (event.button !== 0 || event.altKey) {
      state.drag = { kind: "pan", start: [event.clientX, event.clientY], pan: [state.panX, state.panY], moved: false };
      els.mapCanvas.setPointerCapture(event.pointerId); return;
    }
    if (mode === "add-region" && state.regionDraft.length) {
      const t = transform(), first = worldToScreen(state.regionDraft[0], t);
      const cursor = [(event.clientX - t.rect.left) * t.dpr, (event.clientY - t.rect.top) * t.dpr];
      if (state.regionDraft.length >= 3 && Math.hypot(first[0] - cursor[0], first[1] - cursor[1]) <= 12 * t.dpr) finishRegion();
      else { state.regionDraft.push(world); updateToolUI(); draw(); }
      return;
    }
    if (hit && hit.type === "vertex") {
      select({ type: "region", id: hit.id });
      state.drag = { kind: "vertex", id: hit.id, index: hit.index, start: world, moved: false, identity: identity() };
      els.mapCanvas.setPointerCapture(event.pointerId); return;
    }
    if (hit && hit.type === "node") {
      if (mode === "add-edge") { edgeNode(hit.record); return; }
      select({ type: "node", id: hit.id });
      state.drag = { kind: "node", id: hit.id, start: world, moved: false, identity: identity() };
      els.mapCanvas.setPointerCapture(event.pointerId); return;
    }
    if (hit) {
      if (hit.type === "boundary") {
        select({ type: "region", id: hit.id });
        state.boundary = { id: hit.id, edgeIndex: hit.edgeIndex, projection: hit.projection, identity: identity() };
        renderInspector();
      } else select({ type: hit.type, id: hit.id });
      return;
    }
    if (mode === "add-node") createNode(world);
    else if (mode === "add-edge") showError("边工具需要点击已有节点");
    else {
      if (state.regionDraft.length) {
        const t = transform(), first = worldToScreen(state.regionDraft[0], t);
        const cursor = [(event.clientX - t.rect.left) * t.dpr, (event.clientY - t.rect.top) * t.dpr];
        if (state.regionDraft.length >= 3 && Math.hypot(first[0] - cursor[0], first[1] - cursor[1]) <= 12 * t.dpr) {
          finishRegion(); return;
        }
      }
      if (!state.regionDraft.length) state.regionIdentity = identity();
      state.regionDraft.push(world); updateToolUI(); draw();
    }
  }

  function onPointerMove(event) {
    const world = screenToWorld(event.clientX, event.clientY);
    els.cursorCoords.textContent = `X ${world[0].toFixed(2)} · Y ${world[1].toFixed(2)} · ${state.currentLevel || "—"}`;
    if (!state.drag) return;
    if (state.drag.kind === "pan") {
      const dx = event.clientX - state.drag.start[0], dy = event.clientY - state.drag.start[1];
      if (Math.hypot(dx, dy) > 2) state.drag.moved = true;
      state.panX = state.drag.pan[0] + dx; state.panY = state.drag.pan[1] + dy; draw(); return;
    }
    if (Math.hypot(world[0] - state.drag.start[0], world[1] - state.drag.start[1]) > 1e-5) state.drag.moved = true;
    if (state.drag.kind === "node") {
      const node = nodeById(state.drag.id);
      if (node) state.preview = { kind: "node", id: node.id, position: [world[0], world[1], node.position_xyz[2]] };
    } else {
      const region = regionById(state.drag.id);
      if (region) state.preview = { kind: "vertex", id: region.id, index: state.drag.index, position: [world[0], world[1], region.polygon_xyz[state.drag.index][2]] };
    }
    renderInspector(); draw();
  }

  async function onPointerUp() {
    const drag = state.drag, preview = state.preview;
    state.drag = null;
    if (!drag || drag.kind === "pan" || !drag.moved || !preview) { state.preview = null; draw(); return; }
    state.preview = null;
    if (drag.kind === "node") await edit("move_node_xy", { id: drag.id, position_xy: preview.position.slice(0, 2) }, "节点已移动", drag.identity);
    else await edit("move_region_vertex", { id: drag.id, index: drag.index, position_xy: preview.position.slice(0, 2) }, "区域顶点已移动", drag.identity);
    renderInspector(); draw();
  }

  async function poll() {
    if (state.polling || state.writing) return;
    state.polling = true;
    try {
      const fresh = await request("/api/state");
      const old = state.server;
      const reconnected = state.disconnected || old && fresh.csrf_token !== old.csrf_token;
      const changed = reconnected || !old || fresh.revision !== old.revision || contextKey(fresh) !== contextKey(old)
        || JSON.stringify(fresh.web_selection) !== JSON.stringify(old.web_selection)
        || JSON.stringify(fresh.isaac_runtime) !== JSON.stringify(old.isaac_runtime);
      state.disconnected = false;
      if (reconnected) { cancelGesture(); state.inspectorDraft = null; }
      if (changed) {
        state.suppressSelectionPush = true;
        acceptState(fresh, { preserveSelection: false, preserveDraft: true });
        state.selectionSent = JSON.stringify(fresh.web_selection || null);
        state.suppressSelectionPush = false;
      }
    } catch (_) {
      if (!state.disconnected) {
        cancelGesture(); state.inspectorDraft = null;
        showError("地图服务已断开；当前手势已取消，正在重新连接。");
      }
      state.disconnected = true; renderPending();
    }
    finally { state.polling = false; }
  }

  function bindEvents() {
    els.closeErrorBtn.addEventListener("click", clearError);
    els.domainToggle.addEventListener("click", (event) => {
      const button = event.target.closest("[data-domain]");
      if (button) setContext({ domain: button.dataset.domain }).catch(showError);
    });
    els.floorList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-level]");
      if (button) setContext({ level_id: button.dataset.level }).catch(showError);
    });
    els.modeTools.addEventListener("click", (event) => {
      const button = event.target.closest("[data-mode]");
      if (button) setContext({ tool: button.dataset.mode }).catch(showError);
    });
    els.recordSearch.addEventListener("input", renderRecords);
    els.recordTabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-filter]");
      if (!button) return;
      state.filter = button.dataset.filter;
      [...els.recordTabs.querySelectorAll("[data-filter]")].forEach((item) => item.classList.toggle("active", item === button));
      renderRecords();
    });
    els.recordList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-id]");
      if (button) select({ type: button.dataset.type, id: button.dataset.id });
    });
    els.inspectorForm.addEventListener("submit", applyInspector);
    els.inspectorForm.addEventListener("input", captureInspector);
    els.inspectorForm.addEventListener("change", captureInspector);
    els.deleteBtn.addEventListener("click", deleteSelection);
    els.addVertexBtn.addEventListener("click", addBoundaryVertex);
    els.clearSelectionBtn.addEventListener("click", () => clearSelection());
    els.cancelDraftBtn.addEventListener("click", () => cancelGesture());
    els.confirmPolygonBtn.addEventListener("click", finishRegion);
    els.confirmEdgeBtn.addEventListener("click", confirmEdge);
    els.retryDraftBtn.addEventListener("click", retryPending);
    els.discardDraftBtn.addEventListener("click", () => { state.pendingEdit = null; state.inspectorDraft = null; cancelGesture(); renderPending(); renderInspector(); });
    els.projectPicker.addEventListener("change", () => openProject(els.projectPicker.value));
    els.categoryPicker.addEventListener("change", () => {
      renderProjectOptions();
      if (els.projectPicker.value !== state.currentProjectId) openProject(els.projectPicker.value);
    });
    els.undoBtn.addEventListener("click", () => write("/api/undo", {}, "已撤销"));
    els.redoBtn.addEventListener("click", () => write("/api/redo", {}, "已重做"));
    els.saveBtn.addEventListener("click", () => write("/api/save", {}, "已保存并导出"));
    els.fitBtn.addEventListener("click", () => { state.zoom = 1; state.panX = 0; state.panY = 0; draw(); });
    els.zoomInBtn.addEventListener("click", () => { state.zoom = Math.min(10, state.zoom * 1.25); draw(); });
    els.zoomOutBtn.addEventListener("click", () => { state.zoom = Math.max(.2, state.zoom * .8); draw(); });
    els.mapCanvas.addEventListener("pointerdown", onPointerDown);
    els.mapCanvas.addEventListener("pointermove", onPointerMove);
    els.mapCanvas.addEventListener("pointerup", onPointerUp);
    els.mapCanvas.addEventListener("pointercancel", () => { state.drag = null; state.preview = null; draw(); });
    els.mapCanvas.addEventListener("contextmenu", (event) => event.preventDefault());
    els.mapCanvas.addEventListener("wheel", (event) => { event.preventDefault(); state.zoom = Math.max(.2, Math.min(10, state.zoom * (event.deltaY < 0 ? 1.12 : .89))); draw(); }, { passive: false });
    new ResizeObserver(draw).observe(els.mapStage);
    globalThis.addEventListener("keydown", (event) => {
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier && event.key.toLowerCase() === "s") { event.preventDefault(); els.saveBtn.click(); }
      else if (modifier && event.key.toLowerCase() === "z") { event.preventDefault(); (event.shiftKey ? els.redoBtn : els.undoBtn).click(); }
      else if (modifier && event.key.toLowerCase() === "y") { event.preventDefault(); els.redoBtn.click(); }
      else if (event.key === "Escape") { event.preventDefault(); cancelGesture(); }
      else if (event.key === "Enter" && state.edgeEndId) { event.preventDefault(); confirmEdge(); }
      else if (event.key === "Enter" && state.regionDraft.length >= 3) { event.preventDefault(); finishRegion(); }
    });
  }

  async function loadProjects() {
    try {
      const payload = await request("/api/projects");
      state.projects = payload.projects || [];
      state.currentProjectId = payload.current_id || null;
      hidden(els.projectPickerWrap, state.projects.length < 2);
      const categories = [...new Set(state.projects.map((row) => row.category || "场景"))];
      els.categoryPicker.textContent = "";
      categories.forEach((value) => { const option = document.createElement("option"); option.value = value; option.textContent = value; els.categoryPicker.appendChild(option); });
      const current = state.projects.find((row) => row.id === state.currentProjectId);
      if (current) els.categoryPicker.value = current.category || "场景";
      renderProjectOptions();
    } catch (_) { hidden(els.projectPickerWrap, true); }
  }

  function renderProjectOptions() {
    els.projectPicker.textContent = "";
    state.projects.filter((row) => (row.category || "场景") === els.categoryPicker.value).forEach((row) => {
      const option = document.createElement("option"); option.value = row.id;
      option.textContent = row.display_name || row.scene_key || row.id; els.projectPicker.appendChild(option);
    });
    if ([...els.projectPicker.options].some((option) => option.value === state.currentProjectId)) els.projectPicker.value = state.currentProjectId;
  }

  async function openProject(id) {
    if (!id || id === state.currentProjectId) return;
    if (state.server.dirty && !globalThis.confirm("当前工程有未保存修改，是否放弃修改并切换场景？")) { renderProjectOptions(); return; }
    const fresh = await write("/api/open", { id, discard_unsaved: true }, "场景已切换");
    if (fresh) await loadProjects();
    else renderProjectOptions();
  }

  async function init() {
    bindEvents();
    state.pollTimer = setInterval(poll, 500);
    try {
      const [fresh] = await Promise.all([request("/api/state"), loadProjects()]);
      acceptState(fresh);
      state.selectionSent = JSON.stringify(fresh.web_selection || null);
    } catch (error) {
      hidden(els.loadingLayer, true);
      showError(`无法加载地图项目：${error.message}`);
    }
  }
  init();
})();
