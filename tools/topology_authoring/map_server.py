"""Local browser editor for a unified map project. Run with --project DIR."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import threading
import math
import time
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

from .map_project import MapProject, read_json
from .product import _unique_object, _reject_constant
from .scene_catalog import discover_scene_projects, scene_metadata, source_usd, resolve_scene
from .web_overlay import compact_document_snapshot, normalize_web_selection
from .road_builder import plan_navigation_road, snap_road_start


def make_server(project, port=8765, catalog=None, *, asset_root=None, isaac_linked=False):
    lock = threading.RLock()
    token = secrets.token_urlsafe(32)
    assets = Path(__file__).parent / "web"
    projects = {}
    isaac_selection = {"selection": None}
    web_selection = {"selection": None}
    snapshot_cache = {"key": None, "value": None}
    isaac_sequences = {}
    scene_generation = 0
    context_generation = 0
    def default_editor(value):
        first = value.document.levels[0]["id"] if value.document.levels else None
        return {"active_domain": "ground", "active_layer": "ground", "last_navigation_layer": "ground",
                "editor_tool": "add-node", "active_level": first, "reference_level": first,
                "binding_target_mode": None, "binding_target_id": None,
                "binding_target_draft": None,
                "visible_layers": {"ground": True, "aerial": False, "semantic_points": False},
                "visible_types": {"nodes": True, "edges": True, "semantic_points": True},
                "new_point_settings": {
                    "ground": {"z": value.document.levels[0]["support_z_m"]
                               if first is not None else 0.0},
                    **{key: {"z": 1.5, "edit_mode": "3d"}
                       for key in ("aerial", "semantic_points")}}}
    editor = default_editor(project)
    scene_editors = {}
    runtime = {"state": "loading" if isaac_linked else "disabled", "error": None,
               "scene_key": project.document.scene_key, **scene_metadata(project.manifest)}
    if catalog is not None:
        catalog = Path(catalog).resolve()
        for path in sorted(catalog.iterdir()):
            if path.is_dir() and not path.is_symlink() and (path / "project.json").is_file():
                manifest = read_json(path / "project.json")
                projects[path.name] = {"path": path, "scene_key": manifest["scene_key"],
                                       **scene_metadata(manifest)}
    elif asset_root is not None:
        for key, path in discover_scene_projects(asset_root).items():
            manifest = read_json(path / "project.json")
            projects[key] = {"path": path, "scene_key": manifest["scene_key"],
                             **scene_metadata(manifest)}
    if (catalog is not None or asset_root is not None) and not any(value["path"] == project.root for value in projects.values()):
        # A caller may open a revised project alongside an older catalog.  Keep
        # that exact project selectable so the browser never presents the first
        # catalog entry as if it were the current scene.
        current_id = project.root.name
        suffix = 2
        while current_id in projects:
            current_id = f"{project.root.name}-{suffix}"
            suffix += 1
        projects[current_id] = {
            "path": project.root,
            "scene_key": project.document.scene_key,
            **scene_metadata(project.manifest),
        }

    # One selectable entry per scene. Prefer the exact currently opened revision
    # over an older catalog copy, without deleting either project's files.
    chosen = {}
    for key, value in projects.items():
        if value["scene_key"] not in chosen or value["path"] == project.root:
            chosen[value["scene_key"]] = key
    projects = {key: value for key, value in projects.items() if chosen[value["scene_key"]] == key}

    def current_context():
        with lock:
            source = str(resolve_scene(project.manifest, asset_root)) if asset_root else source_usd(project.manifest)
            # Every edit/undo/redo replaces the document and bumps revision;
            # /api/open swaps the project and bumps scene_generation, so this
            # key misses exactly when snapshot content actually moved.
            document = project.document_for(editor["active_domain"])
            cache_key = (id(document), project.revision, scene_generation, context_generation)
            if snapshot_cache["key"] != cache_key:
                snapshot_cache["key"] = cache_key
                snapshot_cache["value"] = compact_document_snapshot(document, project.revision)
            return {"scene_key": project.document.scene_key, "source_usd": source,
                    "source_path": scene_metadata(project.manifest)["source_path"],
                    "scene_generation": scene_generation, "runtime": dict(runtime),
                    "context_generation": context_generation, "revision": project.revision, **editor,
                    "levels": project.state().get("levels", []), "csrf_token": token,
                    "web_selection": web_selection["selection"],
                    "document_snapshot": snapshot_cache["value"],
                    "document_snapshots": {domain: compact_document_snapshot(doc, project.revision)
                                           for domain, doc in project.documents.items()},
                    "semantic_points": public_semantic_points(),
                    "semantic_binding_digest": project.semantic_binding_digest}

    def public_semantic_points():
        # Empty navigation bindings remain omitted for existing v1 clients.
        rows = deepcopy(project.semantic_points)
        for row in rows:
            if not row.get("navigation_bindings"):
                row.pop("navigation_bindings", None)
        return rows

    def normalize_binding_draft(value):
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("binding target draft must be an array")
        nodes = {domain: {row["id"] for row in project.document_for(domain).nodes}
                 for domain in ("ground", "aerial")}
        result, seen = [], set()
        for row in value:
            if not isinstance(row, dict) or set(row) != {"domain", "node_id"}:
                raise ValueError("invalid binding target draft item")
            domain, node_id = row["domain"], row["node_id"]
            if domain not in nodes or not isinstance(node_id, str) or not node_id.strip():
                raise ValueError("invalid binding target draft node")
            key = (domain, node_id)
            if key in seen:
                raise ValueError("duplicate binding target draft node")
            if node_id not in nodes[domain]:
                raise ValueError("binding target draft references an unknown node")
            seen.add(key)
            result.append({"domain": domain, "node_id": node_id})
        return sorted(result, key=lambda row: (row["domain"], row["node_id"]))

    def normalize_selection(selection):
        if selection is None:
            return None
        if not isinstance(selection, dict):
            raise ValueError("selection must be an object or null")
        layer = editor["active_layer"]
        if not editor["visible_layers"][layer]:
            raise ValueError("hidden layers cannot be selected")
        if isinstance(selection, dict) and selection.get("kind") == "semantic_point":
            if (layer != "semantic_points" or selection.get("collection") is not None
                    or not editor["visible_types"]["semantic_points"] or not any(
                    point["id"] == selection.get("id") for point in project.semantic_points)):
                raise ValueError("semantic point is outside the current layer")
            return {"kind": "semantic_point", "collection": None, "id": selection["id"]}
        if layer == "semantic_points":
            raise ValueError("navigation selection is outside the current layer")
        if selection.get("domain", editor["active_domain"]) != editor["active_domain"]:
            raise ValueError("selection is outside the current navigation graph")
        document = project.document_for(editor["active_domain"])
        selected = normalize_web_selection(document, selection)
        if selected and selected["kind"] not in ("node", "edge"):
            raise ValueError("legacy semantic selection is unavailable in the unified editor")
        if selected and editor["editor_tool"] == "add-node" and selected["kind"] != "node":
            raise ValueError("point tool only selects nodes")
        if selected:
            if not editor["visible_types"]["nodes" if selected["kind"] == "node" else "edges"]:
                raise ValueError("hidden objects cannot be selected")
            def on_floor(node):
                return layer != "ground" or node.get("level_id") in (None, editor["active_level"])
            if selected["kind"] == "node":
                visible = on_floor(document.node(selected["id"]))
            else:
                edge = document.edge(selected["id"])
                visible = on_floor(document.node(edge["from"])) or on_floor(document.node(edge["to"]))
            if not visible:
                raise ValueError("selection is outside the current ground floor")
        return selected

    def drop_selection():
        try:
            web_selection["selection"] = normalize_selection(web_selection["selection"])
        except (ValueError, KeyError):
            web_selection["selection"] = None
        if editor.get("binding_target_mode") == "semantic_navigation":
            selected = web_selection["selection"] or {}
            if selected.get("kind") != "semantic_point" or selected.get("id") != editor.get("binding_target_id"):
                editor.update(binding_target_mode=None, binding_target_id=None, binding_target_draft=None)

    def update_runtime(generation, status, error=None):
        with lock:
            if generation != scene_generation:
                return False
            runtime.update(state=status, error=error)
            return True

    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, payload, content_type="application/json; charset=utf-8"):
            if isinstance(payload, dict):
                payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def local_request(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {"http://"+host for host in allowed}

        def state(self):
            payload = project.state(editor["active_domain"])
            payload["semantic_points"] = public_semantic_points()
            return {**payload, "csrf_token": token, "isaac_runtime": dict(runtime),
                    "scene_generation": scene_generation, "context_generation": context_generation, **editor,
                    "web_selection": web_selection["selection"],
                    "source_path": scene_metadata(project.manifest)["source_path"]}

        def check_context(self, body):
            # Legacy callers without a context remain bound to the ground graph.
            # New clients always carry the complete identity captured at gesture start.
            for field, expected in (("scene_key", project.document.scene_key),
                                    ("scene_generation", scene_generation),
                                    ("context_generation", context_generation)):
                if field in body and (body[field] != expected or type(body[field]) is not type(expected)):
                    raise ValueError(f"stale editor {field}; refresh context before editing")
            if "domain" in body and body["domain"] != editor["active_domain"]:
                raise ValueError("stale editor domain; refresh context before editing")
            if "active_layer" in body and body["active_layer"] != editor["active_layer"]:
                raise ValueError("stale editor layer; refresh context before editing")

        def pop_context(self, body):
            self.check_context(body)
            domain = body.pop("domain", "ground")
            for field in ("scene_key", "scene_generation", "context_generation", "active_layer"):
                body.pop(field, None)
            return domain

        def isaac_context(self):
            return current_context()

        def do_GET(self):
            if not self.local_request():
                self.reply(403, {"error": "local same-origin access required"})
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/api/state":
                    with lock:
                        self.reply(200, self.state())
                elif url.path == "/api/editor/context":
                    with lock:
                        self.reply(200, self.state())
                elif url.path == "/api/isaac/context":
                    with lock:
                        self.reply(200, self.isaac_context())
                elif url.path == "/api/isaac/selection":
                    with lock:
                        self.reply(200, {"selection": isaac_selection["selection"], "runtime": dict(runtime)})
                elif url.path == "/api/projects":
                    with lock:
                        self.reply(200, {"projects": [{"id": key, **{k: v for k, v in value.items() if k != "path"}}
                                                       for key, value in projects.items()],
                                         "current_id": next((key for key, value in projects.items()
                                                             if value["path"] == project.root), None)})
                elif url.path == "/api/layer":
                    lid = parse_qs(url.query)["level"][0]
                    with lock:
                        image = project.layer_image(lid, geometry_only=parse_qs(url.query).get("geometry_only") == ["1"])
                        data = io.BytesIO()
                        image.save(data, format="PNG")
                    self.reply(200, data.getvalue(), "image/png")
                elif url.path in ("/", "/index.html", "/app.js", "/style.css"):
                    name = "index.html" if url.path == "/" else url.path[1:]
                    types = {"html": "text/html", "js": "text/javascript", "css": "text/css"}
                    self.reply(200, (assets / name).read_bytes(), types[name.rsplit(".", 1)[1]]+"; charset=utf-8")
                else:
                    self.reply(404, {"error": "not found"})
            except (KeyError, ValueError, OSError) as exc:
                self.reply(400, {"error": str(exc)})

        def do_POST(self):
            nonlocal project, token, isaac_selection, scene_generation, context_generation
            if not self.local_request():
                self.reply(403, {"error": "local same-origin access required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4_000_000:
                    raise ValueError("invalid request size")
                body = json.loads(self.rfile.read(length), object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant)
                with lock:
                    if not isinstance(body, dict) or body.pop("csrf_token", None) != token:
                        self.reply(403, {"error": "editor token required"})
                        return
                    route = urlsplit(self.path).path
                    if route == "/api/editor/context":
                        # This endpoint has no legacy clients. Context switches
                        # must compare the identity of the state being replaced.
                        for field, expected_type in (("revision", int), ("scene_key", str),
                                                     ("scene_generation", int), ("context_generation", int)):
                            if type(body.get(field)) is not expected_type:
                                raise ValueError(f"expected editor {field} is required")
                        # domain is the requested destination on this endpoint.
                        context_body = {key: value for key, value in body.items() if key not in {"domain", "active_layer"}}
                        self.check_context(context_body)
                        project._expect(body.get("revision"))
                        domain = body.get("active_domain", body.get("domain", editor["active_domain"]))
                        project.document_for(domain)
                        tool = body.get("tool", body.get("editor_tool", editor["editor_tool"]))
                        if tool not in {"add-node", "add-edge", "add-semantic-point", "build-ground-road", "delete-ground-edge", "build-aerial-road", "delete-aerial-edge"}:
                            raise ValueError("unknown editor tool")
                        required_layer = ("ground" if tool in {"build-ground-road", "delete-ground-edge"}
                                          else "aerial" if tool in {"build-aerial-road", "delete-aerial-edge"}
                                          else None)
                        if required_layer and body.get("active_layer", editor["active_layer"]) != required_layer:
                            raise ValueError(f"{required_layer} road tools require the {required_layer} layer")
                        level = body.get("level_id", body.get("active_level", editor["active_level"]))
                        if level not in {row["id"] for row in project.document.levels} and not (level is None and not project.document.levels):
                            raise ValueError("unknown editor level")
                        candidate = deepcopy(editor)
                        layer = body.get("active_layer")
                        # active_layer is also carried in the frozen identity.
                        # An explicit tool change must override that old value.
                        if tool != editor["editor_tool"]:
                            if tool == "add-semantic-point":
                                layer = "semantic_points"
                            elif editor["active_layer"] == "semantic_points" and layer in (None, "semantic_points"):
                                layer = editor["last_navigation_layer"]
                        if domain != editor["active_domain"] and layer == editor["active_layer"]:
                            layer = domain
                        if layer is None:
                            if "tool" in body or "editor_tool" in body:
                                layer = ("semantic_points" if tool == "add-semantic-point" else
                                         editor["last_navigation_layer"] if editor["active_layer"] == "semantic_points" else domain)
                            elif domain != editor["active_domain"]:
                                layer = domain
                            else:
                                layer = editor["active_layer"]
                        if layer not in {"ground", "aerial", "semantic_points"}:
                            raise ValueError("unknown editor layer")
                        if layer == "semantic_points":
                            tool = "add-semantic-point"
                        else:
                            domain = layer
                            if tool == "add-semantic-point":
                                tool = "add-node"
                            candidate["last_navigation_layer"] = layer
                        candidate.update(active_layer=layer, active_domain=domain, editor_tool=tool, active_level=level)
                        if level != editor["active_level"]:
                            candidate["new_point_settings"]["ground"]["z"] = next(
                                (row["support_z_m"] for row in project.document.levels
                                 if row["id"] == level), 0.0)
                        reference = body.get("reference_level", candidate["reference_level"])
                        if reference is not None and reference not in {row["id"] for row in project.document.levels}:
                            raise ValueError("unknown reference level")
                        candidate["reference_level"] = reference
                        if layer != editor["active_layer"] and "visible_layers" not in body:
                            candidate["visible_layers"] = {key: key == layer for key in candidate["visible_layers"]}
                        for setting in ("visible_layers", "visible_types"):
                            if setting in body:
                                changes = body[setting]
                                if (not isinstance(changes, dict) or set(changes) - set(candidate[setting])
                                        or any(type(v) is not bool for v in changes.values())):
                                    raise ValueError("invalid visibility settings")
                                candidate[setting].update(changes)
                        if "new_point_settings" in body:
                            changes = body["new_point_settings"]
                            if not isinstance(changes, dict) or set(changes) - {"ground", "aerial", "semantic_points"}:
                                raise ValueError("invalid point settings")
                            for key, values in changes.items():
                                allowed = {"z"} if key == "ground" else {"z", "edit_mode"}
                                if not isinstance(values, dict) or set(values) - allowed:
                                    raise ValueError("invalid new point settings")
                                candidate["new_point_settings"][key].update(values)
                                settings = candidate["new_point_settings"][key]
                                if (type(settings["z"]) not in (int, float) or not math.isfinite(settings["z"])
                                        or key != "ground" and settings["edit_mode"] not in {"2.5d", "3d"}):
                                    raise ValueError("new point height must be finite and edit mode valid")
                        # Navigation-node binding is a transient session mode.
                        # Keep it in the shared editor context so web and Isaac
                        # show the same draft, while clearing it on any actual
                        # tool/layer/scene editing-context switch.
                        binding_mode = body.get("binding_target_mode", candidate.get("binding_target_mode"))
                        binding_id = body.get("binding_target_id", candidate.get("binding_target_id"))
                        binding_draft = body.get("binding_target_draft", candidate.get("binding_target_draft"))
                        if binding_mode not in (None, "semantic_navigation"):
                            raise ValueError("invalid binding target mode")
                        if binding_mode is None:
                            binding_id, binding_draft = None, None
                        else:
                            if layer != "semantic_points" or tool != "add-semantic-point":
                                raise ValueError("navigation binding mode requires the semantic point layer")
                            if not isinstance(binding_id, str) or not any(
                                    row["id"] == binding_id for row in project.semantic_points):
                                raise ValueError("navigation binding mode requires a selected semantic point")
                            binding_draft = normalize_binding_draft(binding_draft or [])
                        candidate.update(binding_target_mode=binding_mode,
                                        binding_target_id=binding_id,
                                        binding_target_draft=binding_draft)
                        context_identity_changed = any(
                            candidate.get(key) != editor.get(key)
                            for key in ("active_domain", "active_layer", "last_navigation_layer",
                                        "editor_tool", "active_level", "reference_level",
                                        "visible_layers", "visible_types", "new_point_settings"))
                        if context_identity_changed:
                            candidate.update(binding_target_mode=None,
                                            binding_target_id=None,
                                            binding_target_draft=None)
                        changed = candidate != editor
                        editor.update(candidate)
                        if context_identity_changed:
                            context_generation += 1
                            web_selection["selection"] = None
                            isaac_selection["selection"] = None
                        self.reply(200, self.state())
                        return
                    if route == "/api/isaac/selection":
                        self.check_context(body)
                        scene_key = body.get("scene_key")
                        if scene_key != project.document.scene_key:
                            raise ValueError("Isaac selection scene_key does not match current project")
                        kind = body.get("selection_kind")
                        if kind not in {"prim", "point", "clear"}:
                            raise ValueError("selection_kind must be prim, point or clear")
                        if kind == "clear" and "session_id" not in body and "sequence" not in body:
                            isaac_selection["selection"] = None
                            self.reply(200, {"selection": None})
                            return
                        timestamp = body.get("timestamp")
                        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or (timestamp != 0 and abs(time.time() - timestamp) > 30):
                            raise ValueError("stale Isaac selection timestamp (must be within 30 seconds)")
                        session = body.get("session_id")
                        sequence = body.get("sequence")
                        if not isinstance(session, str) or not 1 <= len(session) <= 128 or type(sequence) is not int or sequence < 0:
                            raise ValueError("Isaac session_id and nonnegative sequence required")
                        if sequence <= isaac_sequences.get(session, -1):
                            raise ValueError("stale Isaac selection sequence")
                        if kind == "clear":
                            isaac_sequences[session] = sequence
                            if len(isaac_sequences) > 128:
                                del isaac_sequences[next(iter(isaac_sequences))]
                            isaac_selection["selection"] = None
                            self.reply(200, {"selection": None})
                            return
                        prims = body.get("prim_paths")
                        if not isinstance(prims, list) or len(prims) > 128 or (kind == "prim" and not prims) or not all(isinstance(p, str) and p.startswith("/") and len(p) <= 4096 for p in prims):
                            raise ValueError("Isaac prim_paths must contain absolute USD prim paths")
                        point = body.get("position_xyz")
                        if not isinstance(point, list) or len(point) != 3 or not all(type(v) in (int, float) and math.isfinite(v) for v in point):
                            raise ValueError("position_xyz must contain three finite numbers")
                        source = body.get("source_usd")
                        expected = self.isaac_context().get("source_usd")
                        if expected and str(source) != str(expected):
                            raise ValueError("Isaac source_usd does not match current project")
                        if body.get("up_axis", "Z") != "Z":
                            raise ValueError("only Z-up Isaac stages are supported")
                        level_id = body.get("level_id")
                        level_ids = {row["id"] for row in self.isaac_context()["levels"]}
                        if level_id is not None and level_id not in level_ids:
                            raise ValueError("unknown Isaac level_id")
                        for name in ("bbox_min_xyz", "bbox_max_xyz"):
                            value = body.get(name)
                            if value is not None and (not isinstance(value, list) or len(value) != 3 or not all(type(v) in (int, float) and math.isfinite(v) for v in value)):
                                raise ValueError(f"{name} must contain three finite numbers")
                        low, high = body.get("bbox_min_xyz"), body.get("bbox_max_xyz")
                        if (low is None) != (high is None) or low is not None and any(a > b for a, b in zip(low, high)):
                            raise ValueError("invalid Isaac bounding box")
                        if level_id is None:
                            # Prefer the supporting floor below the object's base, never its centre.
                            height = low[2] if low is not None and kind == "prim" else point[2]
                            below = [row for row in self.isaac_context()["levels"] if row["support_z_m"] <= height + .15]
                            if below:
                                level_id = max(below, key=lambda row: row["support_z_m"])["id"]
                        selection = {"scene_key": scene_key, "source_usd": source,
                                     "scene_generation": body.get("scene_generation"),
                                     "context_generation": body.get("context_generation"),
                                     "prim_paths": body.get("prim_paths", []),
                                     "position_xyz": [float(v) for v in point],
                                     "level_id": level_id, "selection_kind": kind,
                                     "source": body.get("source", "isaac_sim"),
                                     "timestamp": body.get("timestamp", time.time()),
                                     "session_id": body.get("session_id"),
                                     "sequence": body.get("sequence"),
                                     "bbox_min_xyz": body.get("bbox_min_xyz"),
                                     "bbox_max_xyz": body.get("bbox_max_xyz")}
                        isaac_selection["selection"] = selection
                        isaac_sequences[session] = sequence
                        if len(isaac_sequences) > 128:
                            del isaac_sequences[next(iter(isaac_sequences))]
                        self.reply(200, {"selection": selection})
                        return
                    elif route == "/api/web/selection":
                        self.check_context(body)
                        # Ephemeral browser selection: no revision gate, the
                        # scene switch below rotates the token instead.
                        selection = body.get("selection")
                        if (editor.get("binding_target_mode") == "semantic_navigation"
                                and isinstance(selection, dict)
                                and selection.get("kind") in {"node", "edge"}):
                            # Binding picks are transient navigation targets;
                            # keep the semantic point as the authoritative
                            # editor selection while the draft is active.
                            current = web_selection.get("selection")
                            if not (current and current.get("kind") == "semantic_point"
                                    and current.get("id") == editor.get("binding_target_id")):
                                current = {"kind": "semantic_point", "collection": None,
                                           "id": editor.get("binding_target_id")}
                            if current.get("id") == editor.get("binding_target_id"):
                                self.reply(200, {"selection": current})
                                return
                        selection = normalize_selection(selection)
                        web_selection["selection"] = selection
                        if editor.get("binding_target_mode") == "semantic_navigation":
                            selected_id = selection.get("id") if selection and selection.get("kind") == "semantic_point" else None
                            if selected_id != editor.get("binding_target_id"):
                                editor.update(binding_target_mode=None,
                                              binding_target_id=None,
                                              binding_target_draft=None)
                                context_generation += 1
                        self.reply(200, {"selection": selection})
                        return
                    domain = self.pop_context(body)
                    if type(body.get("revision")) is not int:
                        raise ValueError("expected editor revision is required")
                    if route in {"/api/road/preview", "/api/road/start"}:
                        project._expect(body["revision"])
                        if editor["editor_tool"] not in {"build-ground-road", "build-aerial-road"} or editor["active_layer"] != editor["active_domain"]:
                            raise ValueError("road preview requires an active navigation road tool")
                        if route == "/api/road/start":
                            snap = snap_road_start(project.document_for(editor["active_domain"]), body["start_xyz"],
                                level_id=editor["active_level"] if editor["active_domain"] == "ground" else None,
                                snap_radius_m=body.get("snap_radius_m", 0.0), disable_snap=body.get("disable_snap", False))
                            self.reply(200, snap)
                            return
                        plan = plan_navigation_road(project.document_for(editor["active_domain"]), body["start_xyz"], body["end_xyz"],
                            level_id=editor["active_level"] if editor["active_domain"] == "ground" else None,
                            snap_radius_m=body.get("snap_radius_m", 0.0),
                            disable_snap=body.get("disable_snap", False),
                            connect_intersections=body.get("connect_intersections", True),
                            axis_snap=body.get("axis_snap", False), fixed_start=body.get("fixed_start"))
                        self.reply(200, {key: value for key, value in plan.items() if key != "document"})
                        return
                    if route == "/api/edit":
                        operation = body.pop("operation")
                        semantic_operations = {"add_semantic_point", "update_semantic_point",
                                               "remove_semantic_point",
                                               "set_semantic_point_navigation_bindings"}
                        allowed = semantic_operations | {"add_node", "update_node", "move_node_xy", "move_node",
                                                         "remove_node", "add_edge", "update_edge", "remove_edge",
                                                         "translate_edge_xy", "build_ground_road", "build_aerial_road",
                                                         "remove_selection"}
                        if operation not in allowed:
                            raise ValueError("operation is unavailable in the unified editor")
                        operations_by_tool = {
                            "add-node": {"add_node", "update_node", "move_node_xy", "move_node", "remove_node", "remove_selection"},
                            "add-edge": {"add_edge", "update_edge", "remove_edge", "translate_edge_xy"},
                            "add-semantic-point": semantic_operations | {"remove_selection"},
                            "build-ground-road": {"build_ground_road"},
                            "delete-ground-edge": {"remove_edge", "remove_selection"},
                            "build-aerial-road": {"build_aerial_road"},
                            "delete-aerial-edge": {"remove_edge", "remove_selection"},
                        }
                        if operation not in operations_by_tool[editor["editor_tool"]]:
                            raise ValueError("operation is outside the active editor tool")
                        semantic_operation = operation in semantic_operations
                        if operation == "remove_selection":
                            semantic_operation = editor["active_layer"] == "semantic_points"
                        if semantic_operation != (editor["active_layer"] == "semantic_points"):
                            raise ValueError("operation is outside the active editor layer")
                        required_layer = ("ground" if editor["editor_tool"] in {"build-ground-road", "delete-ground-edge"}
                                          else "aerial" if editor["editor_tool"] in {"build-aerial-road", "delete-aerial-edge"}
                                          else None)
                        if required_layer and editor["active_layer"] != required_layer:
                            raise ValueError(f"{required_layer} road tools require the {required_layer} layer")
                        if operation == "build_ground_road":
                            # Preview and commit must use the same authoritative floor.
                            # Browser frozen preview requests need not carry level_id.
                            if body.get("level_id", editor["active_level"]) != editor["active_level"]:
                                raise ValueError("road level is outside the active ground floor")
                            body["level_id"] = editor["active_level"]
                        project.edit(operation, domain=domain, **body)
                        if operation == "add_semantic_point":
                            web_selection["selection"] = {
                                "kind": "semantic_point", "id": project.semantic_points[-1]["id"]}
                            editor.update(binding_target_mode=None, binding_target_id=None,
                                          binding_target_draft=None)
                        if operation == "set_semantic_point_navigation_bindings" and editor.get("binding_target_mode"):
                            editor.update(binding_target_mode=None, binding_target_id=None,
                                         binding_target_draft=None)
                            context_generation += 1
                        drop_selection()
                    elif route == "/api/save":
                        project.save(**body)
                    elif route == "/api/undo":
                        project.undo(**body)
                        drop_selection()
                    elif route == "/api/redo":
                        project.redo(**body)
                        drop_selection()
                    elif route == "/api/open":
                        project._expect(body["revision"])
                        if project.dirty and body.get("discard_unsaved") is not True:
                            raise ValueError("save current project or explicitly discard unsaved edits")
                        replacement = MapProject(projects[body["id"]]["path"])
                        if asset_root:
                            resolve_scene(replacement.manifest, asset_root)
                        replacement.revision = project.revision + 1
                        saved_editor = deepcopy(editor)
                        saved_editor.update(binding_target_mode=None,
                                            binding_target_id=None,
                                            binding_target_draft=None)
                        scene_editors[str(project.root)] = saved_editor
                        project = replacement
                        token = secrets.token_urlsafe(32)
                        isaac_selection["selection"] = None
                        web_selection["selection"] = None
                        scene_generation += 1
                        context_generation += 1
                        editor.clear()
                        editor.update(deepcopy(scene_editors.get(str(project.root), default_editor(project))))
                        editor.update(binding_target_mode=None, binding_target_id=None,
                                      binding_target_draft=None)
                        runtime.update(state="loading" if isaac_linked else "disabled", error=None,
                                       scene_key=project.document.scene_key, **scene_metadata(project.manifest))
                    else:
                        self.reply(404, {"error": "not found"})
                        return
                    self.reply(200, self.state())
            except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
                self.reply(409 if "stale" in str(exc) or "another process" in str(exc) else 400,
                           {"error": str(exc)})

        def log_message(self, fmt, *args):
            if len(args) > 1 and str(args[1]).startswith(("4", "5")):
                super().log_message(fmt, *args)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.isaac_context = current_context
    server.update_isaac_runtime = update_runtime
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--catalog", type=Path, help="optional directory of map projects for scene switching")
    args = parser.parse_args(argv)
    server = make_server(MapProject(args.project), args.port, args.catalog)
    print(f"Map editor: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
