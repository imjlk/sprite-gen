# SPDX-License-Identifier: Apache-2.0
"""Local review desk for part productions: see each pose, approve or reject, redraw the rejected.

Every request reads the workspace afresh, so decisions and rebuilt parts show at once.
Writes (decisions and production steps) need the session token printed at start; the
server listens on 127.0.0.1 only. Nothing here releases anything.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import unquote, urlsplit

from PIL import Image

from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as production
from sprite_gen.spec import parts_contract as contract, parts_workspace as workspace

STATIC = Path(__file__).resolve().parent / "parts_player"
MAX_BODY = 64 * 1024


class ReviewDesk:
    def __init__(self, assembly):
        self.assembly = Path(assembly).absolute()
        self.token = secrets.token_urlsafe(18)
        self.lock = threading.Lock()
        self._renders = {}

    def _state(self):
        return parts_motion._active(self.assembly)

    def productions(self):
        return production.status(self.assembly)

    @staticmethod
    def _split(name):
        part, _, track = name.partition("@")
        return part, track or None

    def part(self, name):
        part, track = self._split(name)
        root, index, registry, _ = self._state()
        state = production._load(root, part)
        rows = production._track(state, track)["poses"]
        motion = registry[index["motion"]["id"]]
        order, first = production._poses(motion, production._states(state, index))
        poses = []
        for pose in order:
            row = rows.get(pose) or {}
            poses.append({"pose": pose, "clip": first[pose][0], "selected": row.get("selected"),
                          "candidates": len(row.get("candidates", [])), "decision": row.get("decision"),
                          "note": row.get("note"), "reviewer": row.get("reviewer"), "check": row.get("check")})
        return {"part": name, "config": state["config"], "track": track or production.MAIN, "poses": poses,
                "built": state["built"]["part"] if state["built"] else None,
                "built_current": production._current(state, registry, order),
                "batches": production._track(state, track)["batches"][-3:]}

    def render(self, name, pose, kind):
        """PNG of one pose: the part on the body alone (built), or the drawn cell / cut on what it was drawn over."""
        part, track = self._split(name)
        root, index, registry, _ = self._state()
        state = production._load(root, part)
        row = production._track(state, track)["poses"].get(pose)
        if row is None or row.get("selected") is None:
            raise contract.PartsContractError(f"{pose} has no drawing yet")
        base = root / row["selected"]
        built = state["built"]
        box = None
        if built is not None and registry.get(part, {}).get("digest") == built["part"]["digest"]:
            digest = built["part"]["digest"]
            with self.lock:
                if (digest, pose) not in self._renders:
                    body = state["config"]["role"] == "body"
                    frames, _ = production._render_alone(root, index, registry, None if body else built["part"],
                                                         production._states(state, index))
                    for other, image in frames.items():
                        self._renders[(digest, other)] = image
                    # One box for every pose, so poses compare in place.
                    boxes = [image.getbbox() for image in frames.values() if image.getbbox()]
                    self._renders[(digest, "box")] = (min(b[0] for b in boxes)-2, min(b[1] for b in boxes)-2,
                                                      max(b[2] for b in boxes)+2, max(b[3] for b in boxes)+2) if boxes else None
                box = self._renders[(digest, "box")]
        if kind == "body" and box is not None and track is None:
            image = self._renders[(built["part"]["digest"], pose)]
        elif kind == "drawn" and (base / "drawn.png").exists():
            image = Image.open(base / "drawn.png").convert("RGBA")
        else:
            image = production.drawn_over(root, row["selected"])
        if box is not None and image.size == self._renders[(built["part"]["digest"], pose)].size:
            image = image.crop(box)
        scale = 4
        large = image.resize((image.width*scale, image.height*scale), Image.Resampling.NEAREST)
        out = BytesIO()
        large.save(out, "PNG")
        return out.getvalue()

    def act(self, name, action, body):
        part, track = self._split(name)
        with self.lock:
            self._renders.clear()
            if action == "review":
                return production.review(self.assembly, part, reviewer=body.get("reviewer"), track=track,
                                         approve=body.get("approve", []), reject=body.get("reject", []),
                                         note=body.get("note") or None)
            if action == "sheet":
                return production.sheet(self.assembly, part, poses=body.get("poses") or None, track=track)
            if action == "draw":
                return production.draw(self.assembly, part, provider=body.get("provider"), track=track,
                                       model=body.get("model"), dry_run=bool(body.get("dry_run")))
            if action == "slice":
                return production.slice_batch(self.assembly, part, track=track)
            if action == "check":
                return production.check(self.assembly, part, track=track)
            if action == "build":
                return production.build(self.assembly, part)
        raise contract.PartsContractError(f"unknown action {action}")


def handler_for(desk):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def json(self, value, status=200):
            self.reply(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path in ("/", "/review.html"):
                    self.reply(200, (STATIC / "review.html").read_bytes(), "text/html; charset=utf-8")
                elif path == "/api/status":
                    self.json(desk.productions())
                elif path.startswith("/api/part/"):
                    self.json(desk.part(unquote(path.removeprefix("/api/part/"))))
                elif path.startswith("/api/render/") and path.endswith(".png"):
                    name, pose, kind = unquote(path.removeprefix("/api/render/").removesuffix(".png")).split("/")
                    self.reply(200, desk.render(name, pose, kind), "image/png")
                else:
                    self.json({"error": "not found"}, 404)
            except (contract.PartsContractError, ValueError, OSError) as exc:
                self.json({"error": str(exc)}, 400)

        def do_POST(self):
            path = urlsplit(self.path).path
            if self.headers.get("X-Review-Token") != desk.token:
                self.json({"error": "missing or wrong review token"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_BODY:
                    raise ValueError("request too large")
                body = json.loads(self.rfile.read(length) or b"{}")
                if not path.startswith("/api/act/") or not isinstance(body, dict):
                    raise ValueError("expected POST /api/act/<part>/<action> with a JSON object")
                name, action = unquote(path.removeprefix("/api/act/")).rsplit("/", 1)
                self.json(desk.act(name, action, body))
            except (contract.PartsContractError, ValueError, OSError) as exc:
                self.json({"error": str(exc)}, 400)
    return Handler


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8767)


def run(*, assembly, port=8767):
    desk = ReviewDesk(assembly)
    desk.productions()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(desk))
    print(f"Parts review desk: http://127.0.0.1:{server.server_port}/#token={desk.token}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
