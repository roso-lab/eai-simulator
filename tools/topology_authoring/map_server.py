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
from urllib.parse import parse_qs, urlsplit

from .map_project import MapProject, read_json
from .product import _unique_object, _reject_constant
from .scene_catalog import discover_scene_projects, scene_metadata, source_usd, resolve_scene


def make_server(project, port=8765, catalog=None, *, asset_root=None, isaac_linked=False):
    lock = threading.RLock()
    token = secrets.token_urlsafe(32)
    assets = Path(__file__).parent / "web"
    projects = {}
    isaac_selection = {"selection": None}
    isaac_sequences = {}
    scene_generation = 0
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
            return {"scene_key": project.document.scene_key, "source_usd": source,
                    "source_path": scene_metadata(project.manifest)["source_path"],
                    "scene_generation": scene_generation, "runtime": dict(runtime),
                    "levels": project.state().get("levels", []), "csrf_token": token}

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
            return {**project.state(), "csrf_token": token, "isaac_runtime": dict(runtime),
                    "source_path": scene_metadata(project.manifest)["source_path"]}

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
                        image = project.layer_image(lid)
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
            nonlocal project, token, isaac_selection, scene_generation
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
                    if route == "/api/isaac/selection":
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
                    if type(body.get("revision")) is not int:
                        raise ValueError("expected editor revision is required")
                    if route == "/api/edit":
                        operation = body.pop("operation")
                        project.edit(operation, **body)
                    elif route == "/api/save":
                        project.save(**body)
                    elif route == "/api/undo":
                        project.undo(**body)
                    elif route == "/api/redo":
                        project.redo(**body)
                    elif route == "/api/open":
                        project._expect(body["revision"])
                        if project.dirty and body.get("discard_unsaved") is not True:
                            raise ValueError("save current project or explicitly discard unsaved edits")
                        replacement = MapProject(projects[body["id"]]["path"])
                        if asset_root:
                            resolve_scene(replacement.manifest, asset_root)
                        replacement.revision = project.revision + 1
                        project = replacement
                        token = secrets.token_urlsafe(32)
                        isaac_selection["selection"] = None
                        scene_generation += 1
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
