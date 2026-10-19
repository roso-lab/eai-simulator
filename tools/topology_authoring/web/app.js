"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const els = {};
  const ids = [
    "sceneSubtitle", "projectPickerWrap", "categoryPicker", "projectPicker", "sceneSyncStatus", "dirtyBadge",
    "revisionBadge", "undoBtn", "redoBtn", "saveBtn", "errorBanner", "errorText",
    "closeErrorBtn", "noticeBanner", "projectPath", "floorList", "showRegions",
    "showObstacles", "showGraph", "showInteractions", "showRoutes", "recordCount",
    "recordSearch", "recordTabs", "recordList", "warningsSection", "warningsList",
    "modeTools", "modeHint", "cancelDraftBtn", "confirmPolygonBtn", "zoomOutBtn",
    "zoomInBtn", "fitBtn", "toggle3dBtn", "mapStage", "mapCanvas", "loadingLayer",
    "isaacStatus", "isaacSelectionText", "isaacNodeBtn", "isaacEntityBtn", "isaacClearBtn",
    "cursorCoords", "xyzPanel", "xyzCanvas", "close3dBtn", "selectionType",
    "selectionTitle", "clearSelectionBtn", "emptyInspector", "inspectorForm",
    "stableIdGroup", "fieldId", "labelGroup", "fieldLabel", "kindGroup", "fieldKind",
    "levelGroup", "fieldLevel", "coordinateGroup", "fieldX", "fieldY", "fieldZ",
    "headingGroup", "fieldHeading", "ownerGroup", "fieldOwner", "regionGroup",
    "fieldRegion", "heightGroup", "fieldHeight", "edgeGroup", "fieldFrom", "fieldTo",
    "fieldDirected", "fieldTraversal", "interactionKindGroup", "fieldInteractionKind",
    "aliasesGroup", "fieldAliases", "advancedDetails", "fieldJson", "deleteBtn", "applyBtn"
  ];
  ids.forEach((id) => { els[id] = $(id); });

  const state = {
    server: null,
    projects: [],
    currentProjectId: null,
    openingProject: false,
    isaacRuntime: null,
    currentLevel: null,
    layerImage: null,
    layerRequest: 0,
    mode: "select",
    recordFilter: "all",
    selection: null,
    polygon: [],
    edgeStartId: null,
    edgeOptions: { directed: false, traversal_mode: "walk" },
    zoom: 1,
    panX: 0,
    panY: 0,
    dragging: null,
    hoverWorld: null,
    advancedBaseline: "",
    busy: false,
    xyzOpen: false,
    isaac: null,
    isaacTimer: null
  };

  const collectionLabels = {
    regions: "语义区域", entities: "物体/障碍", interaction_poses: "交互点", routes: "命名路线"
  };
  const modeHints = {
    select: "选择元素查看和编辑属性；拖动空白处可平移",
    "add-node": "点击地图放置 XYZ 节点，再在右侧确认属性",
    "add-edge": "依次点击两个真实节点；第一个节点可跨楼层保留",
    "add-region": "逐点绘制房间/区域，多边形至少三个点",
    "bind-obstacle": "圈选障碍；服务端会裁剪到真实语义障碍单元并拒绝重叠",
    "add-interaction": "点击添加抓取、放置或观察点，再指定所属对象"
  };

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function finiteNumber(value, label) {
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error(`${label}必须是有限数字`);
    return number;
  }

  function optionalNumber(value, label) {
    if (String(value).trim() === "") return null;
    return finiteNumber(value, label);
  }

  function uniqueId(prefix) {
    const token = globalThis.crypto && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    return `${prefix}:${token}`;
  }

  function setHidden(element, hidden) {
    element.classList.toggle("hidden", Boolean(hidden));
  }

  function showError(error) {
    const message = error instanceof Error ? error.message : String(error || "未知错误");
    els.errorText.textContent = message;
    setHidden(els.errorBanner, false);
    console.error(error);
  }

  function clearError() {
    setHidden(els.errorBanner, true);
    els.errorText.textContent = "";
  }

  let noticeTimer = null;
  function showNotice(message, timeout = 3500) {
    els.noticeBanner.textContent = message;
    setHidden(els.noticeBanner, false);
    clearTimeout(noticeTimer);
    if (timeout) noticeTimer = setTimeout(() => setHidden(els.noticeBanner, true), timeout);
  }

  async function api(path, options = {}) {
    const config = { ...options, headers: { ...(options.headers || {}) } };
    if (config.method === "POST" && config.body && typeof config.body === "object") {
      // Other tabs and scene changes can rotate the editor credential. Refresh
      // it only for the same project revision, never replay into another scene.
      const current = await api("/api/state");
      if (!state.server || current.project_path !== state.server.project_path ||
          current.scene_key !== state.server.scene_key || current.revision !== config.body.revision) {
        throw new Error("项目已在其他窗口更新，请刷新网页后再编辑。");
      }
      config.body = { ...config.body, csrf_token: current.csrf_token };
    }
    if (config.body && typeof config.body !== "string") {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(config.body);
    }
    const response = await fetch(path, config);
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message = payload && typeof payload === "object" ? payload.error : payload;
      throw new Error(message || `请求失败（HTTP ${response.status}）`);
    }
    return payload;
  }

  function writeBody(extra = {}) {
    if (!state.server) throw new Error("尚未加载项目状态");
    return { revision: state.server.revision, csrf_token: state.server.csrf_token, ...extra };
  }

  async function post(path, extra = {}, successMessage = "") {
    if (state.busy) return null;
    state.busy = true;
    updateHeader();
    clearError();
    try {
      const fresh = await api(path, { method: "POST", body: writeBody(extra) });
      acceptState(fresh, { preserveSelection: true });
      if (successMessage) showNotice(successMessage);
      return fresh;
    } catch (error) {
      showError(error);
      return null;
    } finally {
      state.busy = false;
      updateHeader();
    }
  }

  async function edit(operation, args, successMessage = "修改已写入工作修订") {
    return post("/api/edit", { operation, ...args }, successMessage);
  }

  function doc() {
    return state.server && state.server.document ? state.server.document : {};
  }

  function isaacPoint() {
    return state.isaac && Array.isArray(state.isaac.position_xyz) ? state.isaac.position_xyz : null;
  }

  function renderIsaacSelection() {
    const selection = state.isaac;
    const point = isaacPoint();
    els.isaacStatus.textContent = selection ? "已接收" : "等待 Isaac Sim";
    els.isaacStatus.className = selection ? "status-badge dirty" : "muted";
    els.isaacNodeBtn.disabled = !point || state.busy;
    els.isaacEntityBtn.disabled = !point || state.busy;
    if (!selection || !point) {
      els.isaacSelectionText.textContent = "在 Isaac Sim 发送一个对象或视口点，地图会显示紫色预览。";
    } else {
      const prims = Array.isArray(selection.prim_paths) && selection.prim_paths.length ? selection.prim_paths.join(", ") : "视口点";
      els.isaacSelectionText.textContent = `${prims}\nX ${point[0].toFixed(3)} · Y ${point[1].toFixed(3)} · Z ${point[2].toFixed(3)}${selection.level_id ? ` · ${selection.level_id}` : ""}`;
    }
    drawMap();
  }

  function followIsaacFloor(selection) {
    const targetLevel = selection && selection.level_id;
    if (!targetLevel || targetLevel === state.currentLevel || !levelById(targetLevel)) return;
    selectFloor(targetLevel);
    showNotice(`已跟随 Isaac Sim 切换到 ${targetLevel}`, 1800);
  }

  function renderIsaacRuntime() {
    const runtime = state.isaacRuntime;
    let status = "unknown";
    let text = "等待 Isaac Sim 状态";
    if (state.openingProject) {
      status = "loading";
      text = "正在打开场景…";
    } else if (runtime) {
      status = runtime.state;
      if (runtime.scene_key && state.server && runtime.scene_key !== state.server.scene_key && status !== "disabled") {
        status = "loading";
      }
      text = {
        disabled: "Isaac Sim 未启用联动",
        loading: "Isaac Sim 正在加载场景…",
        ready: "Isaac Sim 场景已同步",
        error: "Isaac Sim 场景加载失败"
      }[status] || "等待 Isaac Sim 状态";
    }
    els.sceneSyncStatus.textContent = text;
    els.sceneSyncStatus.dataset.state = status || "unknown";
    els.sceneSyncStatus.title = runtime && runtime.error ? String(runtime.error) : text;
  }

  async function pollIsaacSelection() {
    if (state.openingProject) return;
    const projectAtRequest = state.currentProjectId;
    const sceneAtRequest = state.server && state.server.scene_key;
    try {
      const payload = await api("/api/isaac/selection");
      if (state.openingProject || projectAtRequest !== state.currentProjectId || sceneAtRequest !== (state.server && state.server.scene_key)) return;
      if (payload.runtime) {
        state.isaacRuntime = payload.runtime;
        renderIsaacRuntime();
      }
      const next = payload && payload.selection ? payload.selection : null;
      const old = state.isaac && `${state.isaac.session_id}:${state.isaac.sequence}`;
      const fresh = next && `${next.session_id}:${next.sequence}`;
      if (old !== fresh) {
        state.isaac = next;
        followIsaacFloor(next);
        renderIsaacSelection();
      }
    } catch (_) { /* server may be restarting; retry on next poll */ }
  }

  function setupIsaacPolling() {
    clearInterval(state.isaacTimer);
    state.isaacTimer = setInterval(pollIsaacSelection, 700);
    pollIsaacSelection();
  }

  async function clearIsaacSelection() {
    if (!state.server) return;
    const response = await api("/api/isaac/selection", { method: "POST", body: {
      csrf_token: state.server.csrf_token, scene_key: state.server.scene_key,
      selection_kind: "clear"
    }});
    state.isaac = response.selection || null;
    renderIsaacSelection();
  }

  async function convertIsaac(kind) {
    const selection = state.isaac;
    const point = isaacPoint();
    if (!selection || !point) return;
    const level = selection.level_id || state.currentLevel;
    const prim = Array.isArray(selection.prim_paths) && selection.prim_paths.length ? selection.prim_paths[0] : "";
    try {
      let fresh;
      if (kind === "node") {
        // A viewport raycast is an explicit 3-D waypoint (for example, each
        // end of the same staircase). A whole-asset selection has no such
        // point, so retain the older support-surface placement for that mode.
        const nodePoint = selection.selection_kind === "point" ? point : [
          point[0], point[1], selection.bbox_min_xyz && Number.isFinite(Number(selection.bbox_min_xyz[2]))
            ? Number(selection.bbox_min_xyz[2]) : supportZ(level)
        ];
        fresh = await edit("add_node", { position_xyz: nodePoint, level_id: level,
          label: prim ? prim.split("/").pop() : "Isaac 选点", kind: "isaac_point", source_ref: prim || null }, "Isaac 选点已转为拓扑节点");
      } else {
        const id = uniqueId("entity");
        fresh = await edit("upsert_semantic", { collection: "entities", record: {
          id, label: prim ? prim.split("/").pop() : "Isaac 对象", kind: "object", level_id: level,
          position_xyz: point, source_ref: prim || null, aliases: [], properties: {
            source: "isaac_sim", isaac_prim_path: prim || null,
            bbox_min_xyz: selection.bbox_min_xyz || null, bbox_max_xyz: selection.bbox_max_xyz || null
          }
        }}, "Isaac 选点已转为对象语义");
      }
      if (fresh) await clearIsaacSelection();
    } catch (error) { showError(error); }
  }

  function levels() {
    return state.server && Array.isArray(state.server.levels) ? state.server.levels : [];
  }

  function levelById(id) {
    return levels().find((row) => row.id === id) || null;
  }

  function topologyLevels() {
    return Array.isArray(doc().levels) ? doc().levels : [];
  }

  function supportZ(levelId = state.currentLevel) {
    const serverLevel = levelById(levelId);
    if (serverLevel && Number.isFinite(Number(serverLevel.support_z_m))) return Number(serverLevel.support_z_m);
    const topologyLevel = topologyLevels().find((row) => row.id === levelId);
    return topologyLevel && Number.isFinite(Number(topologyLevel.support_z_m))
      ? Number(topologyLevel.support_z_m) : 0;
  }

  function semantics() {
    const raw = doc().semantics;
    return raw && typeof raw === "object" ? raw : {};
  }

  function semanticRows(collection) {
    return Array.isArray(semantics()[collection]) ? semantics()[collection] : [];
  }

  function nodes() {
    return Array.isArray(doc().nodes) ? doc().nodes : [];
  }

  function edges() {
    return Array.isArray(doc().edges) ? doc().edges : [];
  }

  function nodeById(id) {
    return nodes().find((row) => row.id === id) || null;
  }

  function semanticById(collection, id) {
    return semanticRows(collection).find((row) => row.id === id) || null;
  }

  function recordLevel(record, collection) {
    if (!record) return null;
    if (record.level_id != null) return record.level_id;
    if (collection === "entities" && record.region_id) {
      const region = semanticById("regions", record.region_id);
      return region ? region.level_id : null;
    }
    if (collection === "interaction_poses" && record.owner_id) {
      for (const name of ["entities", "regions"]) {
        const owner = semanticById(name, record.owner_id);
        if (owner) return recordLevel(owner, name);
      }
    }
    return null;
  }

  function edgeTouchesLevel(edge, levelId = state.currentLevel) {
    const first = nodeById(edge.from);
    const second = nodeById(edge.to);
    return Boolean(first && second && (first.level_id === levelId || second.level_id === levelId));
  }

  function acceptState(fresh, options = {}) {
    if (!fresh || typeof fresh !== "object" || !fresh.document) {
      throw new Error("服务端返回的项目状态无效");
    }
    const previousLevel = state.currentLevel;
    const previousSelection = options.preserveSelection ? state.selection : null;
    state.server = fresh;
    if (fresh.isaac_runtime) state.isaacRuntime = fresh.isaac_runtime;
    const available = levels();
    state.currentLevel = available.some((row) => row.id === previousLevel)
      ? previousLevel : (available[0] ? available[0].id : null);
    if (previousSelection && locateSelection(previousSelection.type, previousSelection.id, previousSelection.collection)) {
      state.selection = previousSelection;
    } else if (!state.selection || !state.selection.draft) {
      state.selection = null;
    }
    updateHeader();
    renderFloors();
    renderWarnings();
    renderRecords();
    populateLevelOptions();
    if (state.selection) renderInspector(); else clearSelection(false);
    loadLayer();
    drawXYZ();
  }

  function updateHeader() {
    const fresh = state.server;
    const project = state.projects.find((row) => row.id === state.currentProjectId);
    els.sceneSubtitle.textContent = fresh
      ? `${project ? sceneName(project) : fresh.scene_key || "未命名场景"} · 本地可编辑项目`
      : "正在连接本地地图服务…";
    els.projectPath.textContent = fresh && (fresh.source_path || fresh.project_path) || "";
    els.projectPath.title = fresh && fresh.project_path ? fresh.project_path : "";
    els.revisionBadge.textContent = fresh ? `修订 ${fresh.revision}` : "修订 —";
    const dirty = Boolean(fresh && fresh.dirty);
    els.dirtyBadge.textContent = dirty ? "有未保存修改" : "已保存";
    els.dirtyBadge.className = `status-badge ${dirty ? "dirty" : "saved"}`;
    els.saveBtn.disabled = !fresh || state.busy || !dirty;
    els.undoBtn.disabled = !fresh || state.busy;
    els.redoBtn.disabled = !fresh || state.busy;
    els.categoryPicker.disabled = !fresh || state.busy;
    els.projectPicker.disabled = !fresh || state.busy;
    renderIsaacRuntime();
  }

  function projectCategory(project) {
    return project.category || "未分类";
  }

  function sceneName(project) {
    return project.scene_name || String(project.scene_key || "未命名场景").split(":").pop().replace(/_/g, " ");
  }

  function renderSceneOptions(category, selectedId = null) {
    const rows = state.projects.filter((project) => projectCategory(project) === category)
      .sort((a, b) => sceneName(a).localeCompare(sceneName(b), "zh-CN", { numeric: true }));
    els.projectPicker.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "选择场景以联动打开…";
    placeholder.disabled = true;
    els.projectPicker.append(placeholder);
    rows.forEach((project) => {
      const option = document.createElement("option");
      option.value = project.id;
      option.textContent = sceneName(project);
      els.projectPicker.append(option);
    });
    els.projectPicker.value = rows.some((row) => row.id === selectedId) ? selectedId : "";
  }

  function renderProjectPickers() {
    const current = state.projects.find((row) => row.id === state.currentProjectId);
    const order = ["Living and public space", "Production and logistics", "Service and operations"];
    const categories = [...new Set(state.projects.map(projectCategory))].sort((a, b) => {
      const rank = (category) => order.includes(category) ? order.indexOf(category) : order.length;
      return rank(a) - rank(b) || a.localeCompare(b);
    });
    els.categoryPicker.replaceChildren();
    categories.forEach((category) => {
      const option = document.createElement("option");
      option.value = category;
      option.textContent = category;
      els.categoryPicker.append(option);
    });
    const category = current ? projectCategory(current) : categories[0];
    if (category) els.categoryPicker.value = category;
    renderSceneOptions(category, state.currentProjectId);
    setHidden(els.projectPickerWrap, !state.projects.length);
    updateHeader();
  }

  async function loadProjects() {
    try {
      const payload = await api("/api/projects");
      state.projects = Array.isArray(payload.projects) ? payload.projects : [];
      state.currentProjectId = payload.current_id || null;
      renderProjectPickers();
    } catch (error) {
      // A server without catalog support remains fully usable for one project.
      state.projects = [];
      state.currentProjectId = null;
      setHidden(els.projectPickerWrap, true);
    }
  }

  async function openSelectedProject() {
    const id = els.projectPicker.value;
    if (!id || !state.server || state.busy ||
        (id === state.currentProjectId && (!state.isaacRuntime || state.isaacRuntime.state !== "error"))) return;
    const selectedProject = state.projects.find((row) => row.id === id);
    let discard = false;
    if (state.server.dirty) {
      discard = globalThis.confirm("当前项目有未保存修改。确认丢弃这些修改并打开另一个项目吗？");
      if (!discard) {
        renderProjectPickers();
        return;
      }
    }
    state.busy = true;
    state.openingProject = true;
    updateHeader();
    clearError();
    try {
      const fresh = await api("/api/open", {
        method: "POST",
        body: writeBody({ id, discard_unsaved: discard })
      });
      state.selection = null;
      state.edgeStartId = null;
      state.polygon = [];
      state.isaac = null;
      state.currentLevel = null;
      state.zoom = 1;
      state.panX = 0;
      state.panY = 0;
      state.layerImage = null;
      state.layerRequest += 1;
      state.currentProjectId = id;
      acceptState(fresh);
      renderIsaacSelection();
      await loadProjects();
      showNotice(`已打开 ${selectedProject ? sceneName(selectedProject) : fresh.scene_key}`);
    } catch (error) {
      showError(error);
      renderProjectPickers();
    } finally {
      state.busy = false;
      state.openingProject = false;
      updateHeader();
      pollIsaacSelection();
    }
  }

  function renderFloors() {
    els.floorList.replaceChildren();
    levels().forEach((level) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `floor-item${level.id === state.currentLevel ? " active" : ""}`;
      button.dataset.level = level.id;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(level.id === state.currentLevel));
      const strong = document.createElement("strong");
      strong.textContent = level.label || level.id;
      const span = document.createElement("span");
      span.textContent = level.id;
      const meta = document.createElement("b");
      meta.textContent = `Z ${Number(level.support_z_m).toFixed(2)} m`;
      button.append(strong, span, meta);
      button.addEventListener("click", () => selectFloor(level.id));
      els.floorList.append(button);
    });
  }

  function selectFloor(levelId) {
    if (levelId === state.currentLevel) return;
    state.currentLevel = levelId;
    state.polygon = [];
    state.zoom = 1;
    state.panX = 0;
    state.panY = 0;
    if (state.selection && !state.selection.draft) {
      const item = locateSelection(state.selection.type, state.selection.id, state.selection.collection);
      if (state.selection.type === "node" && item && item.level_id !== levelId) state.selection = null;
      if (state.selection.collection && item && recordLevel(item, state.selection.collection) !== levelId) state.selection = null;
    }
    renderFloors();
    renderRecords();
    renderInspector();
    loadLayer();
    updateModeUI();
  }

  function renderWarnings() {
    const warnings = state.server && Array.isArray(state.server.warnings) ? state.server.warnings : [];
    els.warningsList.replaceChildren();
    warnings.forEach((warning) => {
      const li = document.createElement("li");
      li.textContent = String(warning);
      els.warningsList.append(li);
    });
    setHidden(els.warningsSection, warnings.length === 0);
  }

  function listRecords() {
    const rows = [];
    nodes().forEach((record) => {
      if (record.level_id == null || record.level_id === state.currentLevel) rows.push({ type: "node", id: record.id, record });
    });
    edges().forEach((record) => {
      if (edgeTouchesLevel(record)) rows.push({ type: "edge", id: record.id, record });
    });
    for (const collection of ["regions", "entities", "interaction_poses", "routes"]) {
      semanticRows(collection).forEach((record) => {
        const level = recordLevel(record, collection);
        if (collection === "routes" || level == null || level === state.currentLevel) {
          rows.push({ type: "semantic", collection, id: record.id, record });
        }
      });
    }
    return rows;
  }

  function recordCategory(item) {
    if (item.type === "node") return "nodes";
    if (item.type === "edge") return "edges";
    return item.collection;
  }

  function recordSymbol(item) {
    if (item.type === "node") return ["N", "node"];
    if (item.type === "edge") return ["↔", "edge"];
    if (item.collection === "regions") return ["区", "region"];
    if (item.collection === "entities") return ["物", "entity"];
    if (item.collection === "interaction_poses") return ["点", "interaction"];
    return ["路", "route"];
  }

  function renderRecords() {
    const query = els.recordSearch.value.trim().toLocaleLowerCase();
    const rows = listRecords().filter((item) => {
      const category = recordCategory(item);
      if (state.recordFilter !== "all" && category !== state.recordFilter) return false;
      const haystack = `${item.id} ${item.record.label || ""} ${item.record.kind || ""}`.toLocaleLowerCase();
      return !query || haystack.includes(query);
    });
    els.recordList.replaceChildren();
    els.recordCount.textContent = String(rows.length);
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "record-empty";
      empty.textContent = "当前楼层没有匹配记录";
      els.recordList.append(empty);
      return;
    }
    rows.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "record-item";
      if (state.selection && state.selection.id === item.id && state.selection.collection === item.collection) button.classList.add("active");
      const [glyph, className] = recordSymbol(item);
      const symbol = document.createElement("span");
      symbol.className = `record-symbol ${className}`;
      symbol.textContent = glyph;
      const copy = document.createElement("span");
      copy.className = "record-copy";
      const title = document.createElement("strong");
      title.textContent = item.record.label || item.id;
      const detail = document.createElement("span");
      detail.textContent = `${item.record.kind || collectionLabels[item.collection] || item.type} · ${item.id}`;
      copy.append(title, detail);
      button.append(symbol, copy);
      button.addEventListener("click", () => selectRecord(item));
      els.recordList.append(button);
    });
  }

  function locateSelection(type, id, collection) {
    if (type === "node") return nodeById(id);
    if (type === "edge") return edges().find((row) => row.id === id) || null;
    if (type === "semantic") return semanticById(collection, id);
    return null;
  }

  function selectRecord(item) {
    state.selection = { type: item.type, collection: item.collection || null, id: item.id, draft: false };
    const record = item.record;
    const targetLevel = item.type === "node" ? record.level_id
      : item.type === "semantic" ? recordLevel(record, item.collection) : null;
    if (targetLevel && targetLevel !== state.currentLevel && levelById(targetLevel)) selectFloor(targetLevel);
    renderRecords();
    renderInspector();
    drawMap();
  }

  function clearSelection(redraw = true) {
    state.selection = null;
    setHidden(els.emptyInspector, false);
    setHidden(els.inspectorForm, true);
    setHidden(els.clearSelectionBtn, true);
    els.selectionType.textContent = "未选择";
    els.selectionTitle.textContent = "属性检查器";
    if (redraw) {
      renderRecords();
      drawMap();
    }
  }

  function populateLevelOptions() {
    const selected = els.fieldLevel.value;
    els.fieldLevel.replaceChildren();
    levels().forEach((level) => {
      const option = document.createElement("option");
      option.value = level.id;
      option.textContent = `${level.label || level.id} (${level.id})`;
      els.fieldLevel.append(option);
    });
    if ([...els.fieldLevel.options].some((option) => option.value === selected)) els.fieldLevel.value = selected;
  }

  function populateOwnerOptions() {
    const current = els.fieldOwner.value;
    els.fieldOwner.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "请选择所属区域或物体";
    els.fieldOwner.append(placeholder);
    for (const collection of ["regions", "entities"]) {
      semanticRows(collection).forEach((record) => {
        const option = document.createElement("option");
        option.value = record.id;
        option.textContent = `${collection === "regions" ? "区域" : "物体"} · ${record.label || record.id}`;
        els.fieldOwner.append(option);
      });
    }
    if ([...els.fieldOwner.options].some((option) => option.value === current)) els.fieldOwner.value = current;
  }

  function populateRegionOptions() {
    const current = els.fieldRegion.value;
    els.fieldRegion.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "无所属区域";
    els.fieldRegion.append(empty);
    semanticRows("regions").forEach((record) => {
      const option = document.createElement("option");
      option.value = record.id;
      option.textContent = record.label || record.id;
      els.fieldRegion.append(option);
    });
    if ([...els.fieldRegion.options].some((option) => option.value === current)) els.fieldRegion.value = current;
  }

  function inspectorRecord() {
    if (!state.selection) return null;
    if (state.selection.draft) return state.selection.record;
    return locateSelection(state.selection.type, state.selection.id, state.selection.collection);
  }

  function showGroups(names) {
    const all = ["stableIdGroup", "labelGroup", "kindGroup", "levelGroup", "coordinateGroup",
      "headingGroup", "ownerGroup", "regionGroup", "heightGroup", "edgeGroup",
      "interactionKindGroup", "aliasesGroup", "advancedDetails"];
    all.forEach((name) => setHidden(els[name], !names.includes(name)));
  }

  function setCoordinate(position) {
    const point = Array.isArray(position) ? position : [0, 0, supportZ()];
    els.fieldX.value = Number(point[0] || 0).toFixed(3);
    els.fieldY.value = Number(point[1] || 0).toFixed(3);
    els.fieldZ.value = Number(point[2] || 0).toFixed(3);
  }

  function renderInspector() {
    const record = inspectorRecord();
    if (!state.selection || !record) {
      clearSelection(false);
      return;
    }
    setHidden(els.emptyInspector, true);
    setHidden(els.inspectorForm, false);
    setHidden(els.clearSelectionBtn, false);
    els.fieldId.value = record.id || "";
    els.fieldLabel.value = record.label || "";
    els.fieldKind.value = record.kind || "";
    els.fieldLevel.value = record.level_id == null ? (state.currentLevel || "") : record.level_id;
    els.fieldHeading.value = record.heading_rad == null ? "" : String(record.heading_rad);
    els.fieldAliases.value = Array.isArray(record.aliases) ? record.aliases.join("\n") : "";
    populateOwnerOptions();
    populateRegionOptions();
    els.fieldOwner.value = record.owner_id || "";
    els.fieldRegion.value = record.region_id || "";
    els.fieldHeight.value = record.height_m == null ? "2.5" : String(record.height_m);
    els.fieldDirected.checked = Boolean(record.directed);
    els.fieldTraversal.value = record.traversal_mode || "walk";
    els.fieldFrom.value = record.from || "";
    els.fieldTo.value = record.to || "";
    els.fieldInteractionKind.value = ["grasp", "place", "observe"].includes(record.kind) ? record.kind : "observe";
    els.deleteBtn.textContent = state.selection.draft ? "取消" : "删除";
    els.applyBtn.textContent = state.selection.draft ? "创建" : "应用修改";

    if (state.selection.type === "node") {
      els.selectionType.textContent = state.selection.draft ? "新拓扑节点" : "拓扑节点";
      els.selectionTitle.textContent = record.label || record.id || "新节点";
      setCoordinate(record.position_xyz);
      showGroups(["stableIdGroup", "labelGroup", "kindGroup", "levelGroup", "coordinateGroup", "headingGroup"]);
    } else if (state.selection.type === "edge") {
      els.selectionType.textContent = state.selection.draft ? "新拓扑边" : "拓扑边";
      els.selectionTitle.textContent = state.selection.draft ? "连接节点" : record.id;
      showGroups(["stableIdGroup", "edgeGroup"]);
    } else {
      const collection = state.selection.collection;
      els.selectionType.textContent = `${state.selection.draft ? "新" : ""}${collectionLabels[collection] || "语义记录"}`;
      els.selectionTitle.textContent = record.label || record.id || "新记录";
      if (collection === "regions") {
        showGroups(["stableIdGroup", "labelGroup", "kindGroup", "levelGroup", "heightGroup", "aliasesGroup", "advancedDetails"]);
      } else if (collection === "entities") {
        const bound = obstaclePolygon(record);
        if (record.position_xyz) setCoordinate(record.position_xyz);
        showGroups(["stableIdGroup", "labelGroup", "kindGroup", "levelGroup", ...(bound ? [] : ["coordinateGroup"]), "regionGroup", "aliasesGroup", "advancedDetails"]);
      } else if (collection === "interaction_poses") {
        setCoordinate(record.position_xyz);
        showGroups(["stableIdGroup", "labelGroup", "levelGroup", "coordinateGroup", "headingGroup", "ownerGroup", "interactionKindGroup", "advancedDetails"]);
      } else {
        showGroups(["stableIdGroup", "labelGroup", "kindGroup", "advancedDetails"]);
      }
      const pretty = JSON.stringify(record, null, 2);
      els.fieldJson.value = pretty;
      state.advancedBaseline = pretty;
    }
  }

  function aliasesFromField() {
    return [...new Set(els.fieldAliases.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean))];
  }

  function advancedBase(record) {
    if (els.advancedDetails.classList.contains("hidden")) return clone(record);
    const text = els.fieldJson.value.trim();
    if (!text || text === state.advancedBaseline) return clone(record);
    let value;
    try { value = JSON.parse(text); } catch (error) { throw new Error(`高级 JSON 无法解析：${error.message}`); }
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("高级 JSON 必须是对象");
    if (record.id && value.id !== record.id) throw new Error("稳定 ID 不可修改；如需新记录请使用新增工具");
    return value;
  }

  async function applyInspector(event) {
    event.preventDefault();
    const selection = state.selection;
    const record = inspectorRecord();
    if (!selection || !record) return;
    try {
      if (selection.type === "node") {
        const point = [finiteNumber(els.fieldX.value, "X"), finiteNumber(els.fieldY.value, "Y"), finiteNumber(els.fieldZ.value, "Z")];
        const heading = optionalNumber(els.fieldHeading.value, "朝向");
        if (selection.draft) {
          const fresh = await edit("add_node", {
            position_xyz: point,
            level_id: els.fieldLevel.value || null,
            label: els.fieldLabel.value.trim(),
            kind: els.fieldKind.value.trim() || "custom",
            heading_rad: heading
          }, "拓扑节点已创建");
          if (fresh) state.selection = null;
        } else {
          const original = nodeById(selection.id);
          const moved = original && original.position_xyz.some((value, index) => Math.abs(Number(value) - point[index]) > 1e-9);
          if (moved && !await edit("move_node", { id: selection.id, position_xyz: point }, "节点坐标已更新")) return;
          await edit("update_node", {
            id: selection.id,
            label: els.fieldLabel.value.trim(),
            level_id: els.fieldLevel.value || null,
            kind: els.fieldKind.value.trim() || "custom",
            heading_rad: heading
          }, "节点属性已更新");
        }
      } else if (selection.type === "edge") {
        const directed = els.fieldDirected.checked;
        const traversal = els.fieldTraversal.value;
        if (selection.draft) {
          if (!record.from || !record.to) throw new Error("请在地图上选择边的起点和终点节点");
          const fresh = await edit("add_edge", {
            from_id: record.from, to_id: record.to, directed, traversal_mode: traversal
          }, "拓扑边已创建");
          if (fresh) {
            state.edgeStartId = null;
            setMode("select");
          }
        } else {
          await edit("update_edge", { id: selection.id, directed, traversal_mode: traversal }, "边属性已更新");
        }
      } else {
        await applySemantic(selection, record);
      }
      renderInspector();
      renderRecords();
      drawMap();
    } catch (error) {
      showError(error);
    }
  }

  async function applySemantic(selection, record) {
    const collection = selection.collection;
    if (selection.obstacleDraft) {
      const fresh = await edit("bind_obstacle", {
        id: record.id,
        label: els.fieldLabel.value.trim() || "未命名障碍",
        kind: els.fieldKind.value.trim() || "obstacle",
        level_id: els.fieldLevel.value,
        polygon_xy: clone(record._polygon_xy),
        region_id: els.fieldRegion.value || null
      }, "障碍语义已绑定到真实占用格");
      if (fresh) setMode("select");
      return;
    }
    const next = advancedBase(record);
    next.id = record.id;
    next.label = els.fieldLabel.value.trim();
    if (collection === "regions") {
      next.kind = els.fieldKind.value.trim() || "room";
      next.level_id = els.fieldLevel.value;
      next.height_m = finiteNumber(els.fieldHeight.value, "区域高度");
      next.aliases = aliasesFromField();
    } else if (collection === "entities") {
      next.kind = els.fieldKind.value.trim() || "object";
      next.level_id = els.fieldLevel.value;
      next.region_id = els.fieldRegion.value || null;
      next.aliases = aliasesFromField();
      if (record.position_xyz || selection.draft) {
        next.position_xyz = [finiteNumber(els.fieldX.value, "X"), finiteNumber(els.fieldY.value, "Y"), finiteNumber(els.fieldZ.value, "Z")];
      }
    } else if (collection === "interaction_poses") {
      if (!els.fieldOwner.value) throw new Error("交互点必须选择所属区域或物体");
      next.kind = els.fieldInteractionKind.value;
      next.owner_id = els.fieldOwner.value;
      next.level_id = els.fieldLevel.value;
      next.position_xyz = [finiteNumber(els.fieldX.value, "X"), finiteNumber(els.fieldY.value, "Y"), finiteNumber(els.fieldZ.value, "Z")];
      next.heading_rad = optionalNumber(els.fieldHeading.value, "朝向");
    } else {
      next.kind = els.fieldKind.value.trim() || next.kind;
    }
    if (!Array.isArray(next.aliases)) next.aliases = [];
    if (!next.properties || typeof next.properties !== "object") next.properties = {};
    const fresh = await edit("upsert_semantic", { collection, record: next }, `${collectionLabels[collection]}已${selection.draft ? "创建" : "更新"}`);
    if (fresh && selection.draft) setMode("select");
  }

  async function deleteSelection() {
    const selection = state.selection;
    if (!selection) return;
    if (selection.draft) {
      state.selection = null;
      state.polygon = [];
      if (selection.type === "edge") state.edgeStartId = null;
      clearSelection();
      updateModeUI();
      return;
    }
    const record = inspectorRecord();
    if (!record) return;
    const label = record.label || record.id;
    if (!globalThis.confirm(`确认删除“${label}”吗？将同时清理相关连线和路线引用；删除区域会解除物体的区域绑定，并删除直接属于该区域的交互点。可使用撤销恢复。`)) return;
    let result = null;
    if (selection.type === "node") result = await edit("remove_node", { id: selection.id }, "节点已删除");
    else if (selection.type === "edge") result = await edit("remove_edge", { id: selection.id }, "边已删除");
    else result = await edit("remove_semantic", { collection: selection.collection, id: selection.id }, "语义记录已删除");
    if (result) {
      state.selection = null;
      clearSelection();
    }
  }

  function setMode(mode) {
    state.mode = mode;
    state.polygon = [];
    if (mode !== "add-edge") state.edgeStartId = null;
    if (state.selection && state.selection.draft) state.selection = null;
    updateModeUI();
    clearSelection();
    drawMap();
  }

  function updateModeUI() {
    [...els.modeTools.querySelectorAll("[data-mode]")].forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === state.mode);
    });
    let hint = modeHints[state.mode] || "";
    if (state.mode === "add-edge" && state.edgeStartId) {
      const node = nodeById(state.edgeStartId);
      hint = `起点：${node ? node.label || node.id : state.edgeStartId}。可切换楼层后选择终点。`;
    } else if ((state.mode === "add-region" || state.mode === "bind-obstacle") && state.polygon.length) {
      hint = `已添加 ${state.polygon.length} 个顶点；至少 3 个点后完成。`;
    }
    els.modeHint.textContent = hint;
    const polygonMode = state.mode === "add-region" || state.mode === "bind-obstacle";
    setHidden(els.confirmPolygonBtn, !(polygonMode && state.polygon.length >= 3));
    setHidden(els.cancelDraftBtn, !(polygonMode && state.polygon.length));
  }

  function createNodeDraft(point) {
    state.selection = {
      type: "node", id: "", draft: true,
      record: { id: "", kind: "custom", label: "新拓扑节点", level_id: state.currentLevel, position_xyz: [point[0], point[1], supportZ()], heading_rad: null }
    };
    renderInspector();
    drawMap();
  }

  function createInteractionDraft(point) {
    state.selection = {
      type: "semantic", collection: "interaction_poses", id: "", draft: true,
      record: {
        id: uniqueId("interaction"), kind: "observe", label: "新交互点",
        owner_id: "", level_id: state.currentLevel,
        position_xyz: [point[0], point[1], supportZ()], node_id: null,
        heading_rad: null, properties: {}, aliases: []
      }
    };
    renderInspector();
    drawMap();
  }

  function handleEdgeNode(node) {
    if (!state.edgeStartId) {
      state.edgeStartId = node.id;
      state.selection = {
        type: "edge", id: "", draft: true,
        record: { id: "", from: node.id, to: "", directed: false, traversal_mode: "walk" }
      };
      renderInspector();
      updateModeUI();
      return;
    }
    if (node.id === state.edgeStartId) {
      showError("边的起点和终点不能是同一节点");
      return;
    }
    const draft = state.selection && state.selection.type === "edge" && state.selection.draft
      ? state.selection.record : { id: "", from: state.edgeStartId };
    draft.to = node.id;
    state.selection = { type: "edge", id: "", draft: true, record: draft };
    renderInspector();
    updateModeUI();
  }

  function confirmPolygon() {
    if (state.polygon.length < 3) return;
    const polygon = clone(state.polygon);
    state.polygon = [];
    if (state.mode === "add-region") {
      const z = supportZ();
      const record = {
        id: uniqueId("region"), kind: "room", label: "新语义区域", level_id: state.currentLevel,
        polygon_xyz: polygon.map(([x, y]) => [x, y, z]), height_m: 2.5,
        entrance_node_ids: [], aliases: [], properties: {}
      };
      state.selection = { type: "semantic", collection: "regions", id: record.id, draft: true, record };
    } else {
      const record = {
        id: uniqueId("obstacle"), kind: "obstacle", label: "新障碍物", level_id: state.currentLevel,
        region_id: null, aliases: [], properties: {}, _polygon_xy: polygon
      };
      state.selection = { type: "semantic", collection: "entities", id: record.id, draft: true, obstacleDraft: true, record };
    }
    renderInspector();
    updateModeUI();
    drawMap();
  }

  function currentExtent() {
    const level = levelById(state.currentLevel);
    return level && Array.isArray(level.extent_xy) && level.extent_xy.length === 4
      ? level.extent_xy.map(Number) : [-10, 10, -10, 10];
  }

  function canvasMetrics(canvas = els.mapCanvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.max(1, globalThis.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return { width, height, dpr, cssWidth: rect.width, cssHeight: rect.height };
  }

  function viewTransform() {
    const { width, height, dpr } = canvasMetrics();
    const [xmin, xmax, ymin, ymax] = currentExtent();
    const worldWidth = Math.max(1e-9, xmax - xmin);
    const worldHeight = Math.max(1e-9, ymax - ymin);
    const fit = Math.min(width / worldWidth, height / worldHeight) * .91;
    return {
      width, height, dpr, xmin, xmax, ymin, ymax,
      centerX: (xmin + xmax) / 2,
      centerY: (ymin + ymax) / 2,
      scale: fit * state.zoom,
      panX: state.panX * dpr,
      panY: state.panY * dpr
    };
  }

  function worldToScreen(x, y, transform = viewTransform()) {
    return [
      transform.width / 2 + (x - transform.centerX) * transform.scale + transform.panX,
      transform.height / 2 - (y - transform.centerY) * transform.scale + transform.panY
    ];
  }

  function screenToWorld(clientX, clientY) {
    const rect = els.mapCanvas.getBoundingClientRect();
    const transform = viewTransform();
    const sx = (clientX - rect.left) * transform.dpr;
    const sy = (clientY - rect.top) * transform.dpr;
    return [
      transform.centerX + (sx - transform.width / 2 - transform.panX) / transform.scale,
      transform.centerY - (sy - transform.height / 2 - transform.panY) / transform.scale
    ];
  }

  async function loadLayer() {
    if (!state.server || !state.currentLevel) return;
    const request = ++state.layerRequest;
    setHidden(els.loadingLayer, false);
    const image = new Image();
    image.decoding = "async";
    image.onload = () => {
      if (request !== state.layerRequest) return;
      state.layerImage = image;
      setHidden(els.loadingLayer, true);
      drawMap();
    };
    image.onerror = () => {
      if (request !== state.layerRequest) return;
      state.layerImage = null;
      setHidden(els.loadingLayer, true);
      showError("二维栅格图层加载失败");
      drawMap();
    };
    image.src = `/api/layer?level=${encodeURIComponent(state.currentLevel)}&revision=${encodeURIComponent(state.server.revision)}&_=${Date.now()}`;
  }

  function pathPolygon(context, points, transform) {
    if (!points || !points.length) return false;
    const first = worldToScreen(points[0][0], points[0][1], transform);
    context.beginPath();
    context.moveTo(first[0], first[1]);
    for (let index = 1; index < points.length; index += 1) {
      const point = worldToScreen(points[index][0], points[index][1], transform);
      context.lineTo(point[0], point[1]);
    }
    context.closePath();
    return true;
  }

  function obstacleShapes(entity) {
    const project = entity && entity.properties && entity.properties.map_project;
    const candidates = project && [project.polygon_xy, project.bound_polygon_xy, project.selection_polygon_xy];
    const explicit = candidates && candidates.find((value) => Array.isArray(value) && value.length >= 3);
    if (explicit) return [[explicit]];
    const properties = entity && entity.properties || {};
    const geometry = properties.geometry_binding && properties.geometry_binding.footprint_geojson;
    if (geometry && geometry.type === "Polygon") return [geometry.coordinates];
    if (geometry && geometry.type === "MultiPolygon") return geometry.coordinates;
    const lo = properties.bbox_min_xyz, hi = properties.bbox_max_xyz;
    if (Array.isArray(lo) && Array.isArray(hi) && hi[0] > lo[0] && hi[1] > lo[1]) {
      return [[[[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]]]];
    }
    return [];
  }

  function obstaclePoint(entity) {
    return Array.isArray(entity.position_xyz) ? entity.position_xyz : null;
  }

  function regionCentroid(points) {
    if (!Array.isArray(points) || !points.length) return null;
    const sum = points.reduce((acc, point) => [acc[0] + Number(point[0]), acc[1] + Number(point[1])], [0, 0]);
    return [sum[0] / points.length, sum[1] / points.length];
  }

  function selected(type, id, collection = null) {
    return Boolean(state.selection && !state.selection.draft && state.selection.type === type
      && state.selection.id === id && state.selection.collection === collection);
  }

  function drawMap() {
    const canvas = els.mapCanvas;
    const transform = viewTransform();
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, transform.width, transform.height);
    context.fillStyle = "#070c12";
    context.fillRect(0, 0, transform.width, transform.height);

    if (state.layerImage) {
      const topLeft = worldToScreen(transform.xmin, transform.ymax, transform);
      context.imageSmoothingEnabled = false;
      context.drawImage(state.layerImage, topLeft[0], topLeft[1],
        (transform.xmax - transform.xmin) * transform.scale,
        (transform.ymax - transform.ymin) * transform.scale);
    }

    context.save();
    context.lineJoin = "round";
    context.lineCap = "round";
    if (els.showRegions.checked) drawRegions(context, transform);
    if (els.showObstacles.checked) drawObstacles(context, transform);
    if (els.showRoutes.checked) drawRoutes(context, transform);
    if (els.showGraph.checked) drawGraph(context, transform);
    if (els.showInteractions.checked) drawInteractions(context, transform);
    drawDraft(context, transform);
    drawIsaacSelection(context, transform);
    context.restore();
  }

  function drawIsaacSelection(context, transform) {
    const point = isaacPoint();
    if (!point || state.isaac.level_id && state.isaac.level_id !== state.currentLevel) return;
    const screen = worldToScreen(point[0], point[1], transform);
    const pulse = 9 + 2 * Math.sin(Date.now() / 180);
    context.beginPath(); context.arc(screen[0], screen[1], pulse * transform.dpr, 0, Math.PI * 2);
    context.fillStyle = "rgba(189,116,255,.22)"; context.fill();
    context.strokeStyle = "#d9a7ff"; context.lineWidth = 2 * transform.dpr; context.stroke();
    context.beginPath(); context.arc(screen[0], screen[1], 3 * transform.dpr, 0, Math.PI * 2);
    context.fillStyle = "#f0d7ff"; context.fill();
    drawLabel(context, transform, point, "Isaac Sim", "#e7c7ff", 10, -14);
  }

  function drawRegions(context, transform) {
    semanticRows("regions").forEach((region) => {
      if (recordLevel(region, "regions") !== state.currentLevel || !Array.isArray(region.polygon_xyz)) return;
      const points = region.polygon_xyz.map((point) => point.slice(0, 2));
      if (!pathPolygon(context, points, transform)) return;
      const active = selected("semantic", region.id, "regions");
      context.fillStyle = active ? "rgba(192,143,255,.25)" : "rgba(166,126,225,.10)";
      context.strokeStyle = active ? "#e3c4ff" : "#a789df";
      context.lineWidth = (active ? 3 : 1.5) * transform.dpr;
      context.fill(); context.stroke();
      const center = regionCentroid(points);
      if (center) drawLabel(context, transform, center, region.label || region.id, "#dfcaf8");
    });
  }

  function drawObstacles(context, transform) {
    semanticRows("entities").forEach((entity) => {
      if (recordLevel(entity, "entities") !== state.currentLevel) return;
      const shapes = obstacleShapes(entity);
      const active = selected("semantic", entity.id, "entities");
      shapes.forEach((rings) => {
        context.beginPath();
        rings.forEach((ring) => {
          ring.forEach((point, index) => {
            const screen = worldToScreen(point[0], point[1], transform);
            if (index === 0) context.moveTo(...screen); else context.lineTo(...screen);
          });
          context.closePath();
        });
        context.fillStyle = active ? "rgba(255,153,88,.22)" : "rgba(235,139,74,.08)";
        context.strokeStyle = active ? "#ffc095" : "#de8b54";
        context.lineWidth = (active ? 3 : 1.3) * transform.dpr;
        context.fill("evenodd"); context.stroke();
      });
      const center = obstaclePoint(entity) || (shapes.length && regionCentroid(shapes[0][0]));
      if (center) {
        if (!shapes.length) {
          const screen = worldToScreen(center[0], center[1], transform);
          context.beginPath(); context.arc(...screen, (active ? 7 : 5) * transform.dpr, 0, Math.PI * 2);
          context.fillStyle = active ? "#ffc095" : "#de8b54"; context.fill();
        }
        drawLabel(context, transform, center, entity.label || entity.id, "#ffd0ad");
      }
    });
  }

  function drawGraph(context, transform) {
    edges().forEach((edge) => {
      if (!edgeTouchesLevel(edge)) return;
      const first = nodeById(edge.from), second = nodeById(edge.to);
      if (!first || !second) return;
      const a = worldToScreen(first.position_xyz[0], first.position_xyz[1], transform);
      const b = worldToScreen(second.position_xyz[0], second.position_xyz[1], transform);
      const active = selected("edge", edge.id);
      context.beginPath(); context.moveTo(...a); context.lineTo(...b);
      context.strokeStyle = active ? "#ffed8a" : edge.traversal_mode === "stairs" ? "#e7a7ff" : edge.traversal_mode === "ramp" ? "#62bcff" : "#27c995";
      context.lineWidth = (active ? 4 : 2) * transform.dpr;
      context.setLineDash(first.level_id !== second.level_id ? [7 * transform.dpr, 5 * transform.dpr] : []);
      context.stroke(); context.setLineDash([]);
      if (edge.directed) drawArrow(context, a, b, transform.dpr);
    });
    nodes().forEach((node) => {
      if (node.level_id != null && node.level_id !== state.currentLevel) return;
      drawNode(context, transform, node, selected("node", node.id));
    });
  }

  function drawNode(context, transform, node, active) {
    if (!Array.isArray(node.position_xyz)) return;
    const point = worldToScreen(node.position_xyz[0], node.position_xyz[1], transform);
    const radius = (active ? 7 : 5) * transform.dpr;
    context.beginPath(); context.arc(point[0], point[1], radius, 0, Math.PI * 2);
    context.fillStyle = node.kind === "transition" ? "#c895ef" : "#48ddb1";
    context.fill();
    context.strokeStyle = active ? "#fff3a7" : "#071b16";
    context.lineWidth = (active ? 3 : 1.5) * transform.dpr;
    context.stroke();
    const generatedRouteEndpoint = node.label === "几何路线起点" || node.label === "几何路线终点"
      || String(node.id || "").startsWith("route:");
    const duplicatedRegionAnchor = node.kind === "region_anchor";
    if (active || node.kind === "transition" || node.label && !generatedRouteEndpoint && !duplicatedRegionAnchor) {
      drawLabel(context, transform, node.position_xyz, node.label || node.id, "#c9f9ec", 9, -11);
    }
  }

  function drawArrow(context, a, b, dpr) {
    const angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
    const x = (a[0] + b[0]) / 2, y = (a[1] + b[1]) / 2, size = 7 * dpr;
    context.beginPath();
    context.moveTo(x + Math.cos(angle) * size, y + Math.sin(angle) * size);
    context.lineTo(x + Math.cos(angle + 2.5) * size, y + Math.sin(angle + 2.5) * size);
    context.lineTo(x + Math.cos(angle - 2.5) * size, y + Math.sin(angle - 2.5) * size);
    context.closePath(); context.fillStyle = context.strokeStyle; context.fill();
  }

  function drawInteractions(context, transform) {
    semanticRows("interaction_poses").forEach((pose) => {
      if (recordLevel(pose, "interaction_poses") !== state.currentLevel || !Array.isArray(pose.position_xyz)) return;
      const point = worldToScreen(pose.position_xyz[0], pose.position_xyz[1], transform);
      const active = selected("semantic", pose.id, "interaction_poses");
      const size = (active ? 8 : 6) * transform.dpr;
      context.beginPath();
      context.moveTo(point[0], point[1] - size);
      context.lineTo(point[0] + size, point[1]);
      context.lineTo(point[0], point[1] + size);
      context.lineTo(point[0] - size, point[1]);
      context.closePath();
      context.fillStyle = pose.kind === "grasp" ? "#ffcc5d" : pose.kind === "place" ? "#65baff" : "#f38fb2";
      context.fill(); context.strokeStyle = active ? "#fff" : "#251b08"; context.lineWidth = 1.5 * transform.dpr; context.stroke();
      drawLabel(context, transform, pose.position_xyz, pose.label || pose.id, "#ffe8a9", 9, -11);
    });
  }

  function drawRoutes(context, transform) {
    semanticRows("routes").forEach((route) => {
      const routeNodes = Array.isArray(route.node_ids) ? route.node_ids.map(nodeById).filter(Boolean) : [];
      const floorNodes = routeNodes.filter((node) => node.level_id === state.currentLevel);
      if (floorNodes.length < 2) return;
      context.beginPath();
      floorNodes.forEach((node, index) => {
        const point = worldToScreen(node.position_xyz[0], node.position_xyz[1], transform);
        if (!index) context.moveTo(...point); else context.lineTo(...point);
      });
      context.strokeStyle = "rgba(76,227,116,.78)";
      context.lineWidth = 3 * transform.dpr;
      context.setLineDash([8 * transform.dpr, 4 * transform.dpr]);
      context.stroke(); context.setLineDash([]);
    });
  }

  function drawDraft(context, transform) {
    if (state.polygon.length) {
      context.beginPath();
      state.polygon.forEach((point, index) => {
        const screen = worldToScreen(point[0], point[1], transform);
        if (!index) context.moveTo(...screen); else context.lineTo(...screen);
      });
      context.strokeStyle = state.mode === "bind-obstacle" ? "#ff9b61" : "#c9a8ff";
      context.lineWidth = 2 * transform.dpr;
      context.setLineDash([6 * transform.dpr, 4 * transform.dpr]); context.stroke(); context.setLineDash([]);
      state.polygon.forEach((point) => {
        const screen = worldToScreen(point[0], point[1], transform);
        context.beginPath(); context.arc(screen[0], screen[1], 4 * transform.dpr, 0, Math.PI * 2);
        context.fillStyle = context.strokeStyle; context.fill();
      });
    }
    const record = inspectorRecord();
    if (state.selection && state.selection.draft && record) {
      if (state.selection.type === "node") drawNode(context, transform, record, true);
      if (state.selection.collection === "interaction_poses") {
        const point = worldToScreen(record.position_xyz[0], record.position_xyz[1], transform);
        context.beginPath(); context.arc(...point, 8 * transform.dpr, 0, Math.PI * 2);
        context.strokeStyle = "#ffdc72"; context.lineWidth = 3 * transform.dpr; context.stroke();
      }
      if (state.selection.collection === "regions") {
        pathPolygon(context, record.polygon_xyz, transform); context.strokeStyle = "#e4c6ff"; context.lineWidth = 3 * transform.dpr; context.stroke();
      }
      if (state.selection.obstacleDraft) {
        pathPolygon(context, record._polygon_xy, transform); context.strokeStyle = "#ffc098"; context.lineWidth = 3 * transform.dpr; context.stroke();
      }
    }
  }

  function drawLabel(context, transform, point, text, color, size = 10, offsetY = -8) {
    const screen = worldToScreen(point[0], point[1], transform);
    if (screen[0] < 0 || screen[0] > transform.width || screen[1] < 0 || screen[1] > transform.height) return;
    const fontSize = size * transform.dpr;
    context.font = `${fontSize}px system-ui, sans-serif`;
    let label = String(text || "");
    const maxWidth = Math.max(20, Math.min(280 * transform.dpr, transform.width - 16 * transform.dpr));
    if (context.measureText(label).width > maxWidth) {
      while (label.length && context.measureText(label + "…").width > maxWidth) label = label.slice(0, -1);
      label += "…";
    }
    const width = context.measureText(label).width + 8 * transform.dpr;
    const x = Math.max(4 * transform.dpr, Math.min(screen[0] - width / 2, transform.width - width - 4 * transform.dpr));
    const y = Math.max(2 * transform.dpr, Math.min(screen[1] + offsetY * transform.dpr - fontSize, transform.height - fontSize - 4 * transform.dpr));
    context.fillStyle = "rgba(7,14,22,.82)";
    context.fillRect(x, y - 2 * transform.dpr, width, fontSize + 5 * transform.dpr);
    context.fillStyle = color;
    context.textAlign = "center"; context.textBaseline = "top";
    context.fillText(label, x + width / 2, y);
  }

  function distanceToSegment(point, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    if (!dx && !dy) return Math.hypot(point[0] - a[0], point[1] - a[1]);
    const t = Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy));
  }

  function pointInPolygon(point, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const xi = polygon[i][0], yi = polygon[i][1], xj = polygon[j][0], yj = polygon[j][1];
      const intersect = ((yi > point[1]) !== (yj > point[1]))
        && point[0] < (xj - xi) * (point[1] - yi) / (yj - yi || 1e-12) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function hitTest(clientX, clientY, nodesOnly = false) {
    const world = screenToWorld(clientX, clientY);
    const transform = viewTransform();
    const tolerance = 10 * transform.dpr / transform.scale;
    const node = nodes().slice().reverse().find((row) => (row.level_id == null || row.level_id === state.currentLevel)
      && Math.hypot(row.position_xyz[0] - world[0], row.position_xyz[1] - world[1]) <= tolerance);
    if (node) return { type: "node", id: node.id, record: node };
    if (nodesOnly) return null;
    const pose = semanticRows("interaction_poses").slice().reverse().find((row) => recordLevel(row, "interaction_poses") === state.currentLevel
      && Array.isArray(row.position_xyz) && Math.hypot(row.position_xyz[0] - world[0], row.position_xyz[1] - world[1]) <= tolerance);
    if (pose) return { type: "semantic", collection: "interaction_poses", id: pose.id, record: pose };
    const edge = edges().slice().reverse().find((row) => {
      if (!edgeTouchesLevel(row)) return false;
      const a = nodeById(row.from), b = nodeById(row.to);
      return a && b && distanceToSegment(world, a.position_xyz, b.position_xyz) <= tolerance;
    });
    if (edge) return { type: "edge", id: edge.id, record: edge };
    for (const collection of ["entities", "regions"]) {
      const rows = semanticRows(collection).slice().reverse();
      const record = rows.find((row) => {
        if (recordLevel(row, collection) !== state.currentLevel) return false;
        if (collection === "entities") {
          const point = obstaclePoint(row);
          return (point && Math.hypot(point[0] - world[0], point[1] - world[1]) <= tolerance)
            || obstacleShapes(row).some((rings) => pointInPolygon(world, rings[0])
              && !rings.slice(1).some((hole) => pointInPolygon(world, hole)));
        }
        const polygon = row.polygon_xyz;
        return Array.isArray(polygon) && polygon.length >= 3 && pointInPolygon(world, polygon);
      });
      if (record) return { type: "semantic", collection, id: record.id, record };
    }
    return null;
  }

  function onCanvasPointerDown(event) {
    if (!state.server || event.button === 2 || event.button === 1 || event.altKey || state.mode === "select") {
      state.dragging = { x: event.clientX, y: event.clientY, panX: state.panX, panY: state.panY, moved: false, selectOnRelease: state.mode === "select" && event.button === 0 && !event.altKey };
      els.mapCanvas.setPointerCapture(event.pointerId);
      return;
    }
    if (event.button !== 0) return;
    const point = screenToWorld(event.clientX, event.clientY);
    if (state.mode === "add-node") createNodeDraft(point);
    else if (state.mode === "add-interaction") createInteractionDraft(point);
    else if (state.mode === "add-edge") {
      const hit = hitTest(event.clientX, event.clientY, true);
      if (hit) handleEdgeNode(hit.record); else showError("连边工具需要点击已有拓扑节点");
    } else if (state.mode === "add-region" || state.mode === "bind-obstacle") {
      state.polygon.push(point);
      updateModeUI(); drawMap();
    }
  }

  function onCanvasPointerMove(event) {
    const point = screenToWorld(event.clientX, event.clientY);
    state.hoverWorld = point;
    els.cursorCoords.textContent = `X ${point[0].toFixed(2)} · Y ${point[1].toFixed(2)} · ${state.currentLevel || "—"}`;
    if (!state.dragging) return;
    const dx = event.clientX - state.dragging.x, dy = event.clientY - state.dragging.y;
    if (Math.hypot(dx, dy) > 3) state.dragging.moved = true;
    if (state.dragging.moved) {
      state.panX = state.dragging.panX + dx;
      state.panY = state.dragging.panY + dy;
      drawMap();
    }
  }

  function onCanvasPointerUp(event) {
    const dragging = state.dragging;
    state.dragging = null;
    if (!dragging || dragging.moved || !dragging.selectOnRelease) return;
    const hit = hitTest(event.clientX, event.clientY);
    if (hit) selectRecord(hit); else clearSelection();
  }

  function zoomAt(factor, clientX = null, clientY = null) {
    const before = clientX == null ? null : screenToWorld(clientX, clientY);
    state.zoom = Math.max(.2, Math.min(10, state.zoom * factor));
    if (before && clientX != null) {
      const after = screenToWorld(clientX, clientY);
      const transform = viewTransform();
      state.panX += (after[0] - before[0]) * transform.scale / transform.dpr;
      state.panY -= (after[1] - before[1]) * transform.scale / transform.dpr;
    }
    drawMap();
  }

  function toggleXYZ(open = !state.xyzOpen) {
    state.xyzOpen = open;
    setHidden(els.xyzPanel, !open);
    if (open) requestAnimationFrame(drawXYZ);
  }

  function drawXYZ() {
    if (!state.xyzOpen || !state.server) return;
    const metrics = canvasMetrics(els.xyzCanvas);
    const context = els.xyzCanvas.getContext("2d");
    context.clearRect(0, 0, metrics.width, metrics.height);
    context.fillStyle = "#0a111b"; context.fillRect(0, 0, metrics.width, metrics.height);
    const points = [];
    levels().forEach((level) => {
      const [xmin, xmax, ymin, ymax] = level.extent_xy;
      [[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax]].forEach(([x,y]) => points.push([x,y,Number(level.support_z_m)]));
    });
    nodes().forEach((node) => { if (Array.isArray(node.position_xyz)) points.push(node.position_xyz.map(Number)); });
    if (!points.length) return;
    const rawProject = ([x,y,z]) => [x - y * .55, -(x + y) * .22 - z * 1.8];
    const projected = points.map(rawProject);
    const xs = projected.map((point) => point[0]), ys = projected.map((point) => point[1]);
    const bounds = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const scale = Math.min((metrics.width - 80 * metrics.dpr) / Math.max(1,bounds[1]-bounds[0]), (metrics.height - 70 * metrics.dpr) / Math.max(1,bounds[3]-bounds[2]));
    const project = (point) => {
      const value = rawProject(point);
      return [metrics.width/2 + (value[0]-(bounds[0]+bounds[1])/2)*scale, metrics.height/2 + (value[1]-(bounds[2]+bounds[3])/2)*scale];
    };
    levels().forEach((level, index) => {
      const [xmin,xmax,ymin,ymax] = level.extent_xy.map(Number), z=Number(level.support_z_m);
      const corners=[[xmin,ymin,z],[xmax,ymin,z],[xmax,ymax,z],[xmin,ymax,z]].map(project);
      context.beginPath(); corners.forEach((point,i)=>i?context.lineTo(...point):context.moveTo(...point)); context.closePath();
      context.fillStyle = index%2 ? "rgba(70,116,153,.07)" : "rgba(55,198,160,.055)";
      context.strokeStyle = level.id===state.currentLevel ? "#57dfb9" : "#42627e";
      context.lineWidth = (level.id===state.currentLevel?2:1)*metrics.dpr; context.fill(); context.stroke();
      context.fillStyle="#9bb2c7"; context.font=`${11*metrics.dpr}px system-ui`; context.fillText(`${level.label||level.id} · z=${z.toFixed(2)}m`,corners[0][0]+5*metrics.dpr,corners[0][1]-5*metrics.dpr);
    });
    edges().forEach((edge)=>{
      const a=nodeById(edge.from),b=nodeById(edge.to); if(!a||!b)return;
      const pa=project(a.position_xyz),pb=project(b.position_xyz); context.beginPath();context.moveTo(...pa);context.lineTo(...pb);
      context.strokeStyle=a.level_id!==b.level_id?"#df9cff":"#33d39e";context.lineWidth=2*metrics.dpr;context.setLineDash(a.level_id!==b.level_id?[6*metrics.dpr,4*metrics.dpr]:[]);context.stroke();context.setLineDash([]);
    });
    nodes().forEach((node)=>{if(!Array.isArray(node.position_xyz))return;const point=project(node.position_xyz);context.beginPath();context.arc(...point,4*metrics.dpr,0,Math.PI*2);context.fillStyle=node.kind==="transition"?"#d89cf6":"#54deb6";context.fill();});
  }

  function bindEvents() {
    els.closeErrorBtn.addEventListener("click", clearError);
    els.categoryPicker.addEventListener("change", () => {
      renderSceneOptions(els.categoryPicker.value, state.currentProjectId);
    });
    els.projectPicker.addEventListener("change", openSelectedProject);
    els.saveBtn.addEventListener("click", async () => {
      const fresh = await post("/api/save", {}, "已原子保存当前修订，并重新生成 topology、footprints、GeoJSON、NPZ 与 PNG 导出");
      if (fresh) await loadProjects();
    });
    els.undoBtn.addEventListener("click", () => post("/api/undo", {}, "已撤销"));
    els.redoBtn.addEventListener("click", () => post("/api/redo", {}, "已重做"));
    els.recordSearch.addEventListener("input", renderRecords);
    els.recordTabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-filter]"); if (!button) return;
      state.recordFilter = button.dataset.filter;
      [...els.recordTabs.querySelectorAll("[data-filter]")].forEach((row) => row.classList.toggle("active", row === button));
      renderRecords();
    });
    [els.showRegions,els.showObstacles,els.showGraph,els.showInteractions,els.showRoutes].forEach((input)=>input.addEventListener("change",drawMap));
    els.modeTools.addEventListener("click", (event) => { const button=event.target.closest("[data-mode]"); if(button)setMode(button.dataset.mode); });
    els.cancelDraftBtn.addEventListener("click",()=>{state.polygon=[];updateModeUI();drawMap();});
    els.confirmPolygonBtn.addEventListener("click",confirmPolygon);
    els.fitBtn.addEventListener("click",()=>{state.zoom=1;state.panX=0;state.panY=0;drawMap();});
    els.zoomInBtn.addEventListener("click",()=>zoomAt(1.25));
    els.zoomOutBtn.addEventListener("click",()=>zoomAt(.8));
    els.toggle3dBtn.addEventListener("click",()=>toggleXYZ());
    els.close3dBtn.addEventListener("click",()=>toggleXYZ(false));
    els.isaacNodeBtn.addEventListener("click",()=>convertIsaac("node"));
    els.isaacEntityBtn.addEventListener("click",()=>convertIsaac("entity"));
    els.isaacClearBtn.addEventListener("click",()=>clearIsaacSelection().catch(showError));
    els.clearSelectionBtn.addEventListener("click",()=>clearSelection());
    els.inspectorForm.addEventListener("submit",applyInspector);
    els.deleteBtn.addEventListener("click",deleteSelection);
    els.mapCanvas.addEventListener("pointerdown",onCanvasPointerDown);
    els.mapCanvas.addEventListener("pointermove",onCanvasPointerMove);
    els.mapCanvas.addEventListener("pointerup",onCanvasPointerUp);
    els.mapCanvas.addEventListener("pointercancel",()=>{state.dragging=null;});
    els.mapCanvas.addEventListener("contextmenu",(event)=>event.preventDefault());
    els.mapCanvas.addEventListener("wheel",(event)=>{event.preventDefault();zoomAt(event.deltaY<0?1.12:.89,event.clientX,event.clientY);},{passive:false});
    new ResizeObserver(()=>{drawMap();drawXYZ();}).observe(els.mapStage);
    globalThis.addEventListener("keydown",(event)=>{
      const modifier=event.ctrlKey||event.metaKey;
      if(modifier&&event.key.toLowerCase()==="s"){event.preventDefault();els.saveBtn.click();}
      else if(modifier&&event.key.toLowerCase()==="z"){event.preventDefault();(event.shiftKey?els.redoBtn:els.undoBtn).click();}
      else if(modifier&&event.key.toLowerCase()==="y"){event.preventDefault();els.redoBtn.click();}
      else if(event.key==="Escape"){
        if(state.xyzOpen)toggleXYZ(false);else if(state.polygon.length){state.polygon=[];updateModeUI();drawMap();}else clearSelection();
      } else if(event.key==="Enter"&&(state.mode==="add-region"||state.mode==="bind-obstacle")&&state.polygon.length>=3){event.preventDefault();confirmPolygon();}
    });
  }

  async function init() {
    bindEvents();
    updateModeUI();
    try {
      const [fresh] = await Promise.all([api("/api/state"), loadProjects()]);
      acceptState(fresh);
      renderIsaacSelection();
      setupIsaacPolling();
    } catch (error) {
      setHidden(els.loadingLayer, true);
      showError(`无法加载地图项目：${error.message}`);
    }
  }

  init();
})();
