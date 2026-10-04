# SPDX-License-Identifier: Apache-2.0
"""Local read-only parts player: current sample only, no appearance baking."""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from sprite_gen.compose import compose_parts
from sprite_gen.compose.parts_runtime import PartsPlayer
from sprite_gen.curate import parts_motion
from sprite_gen.spec import parts_assets as assets, parts_contract as contract

STATIC = Path(__file__).resolve().parent / "parts_player"


class PartsSession:
    """Immutable workspace snapshot with a bounded cache of resolved selections."""

    def __init__(self, assembly):
        root, index, registry, _ = parts_motion._active(assembly)
        if index["appearance"] is None:
            raise contract.PartsContractError("parts player requires an active appearance")
        self.appearance = registry[index["appearance"]["id"]]
        if self.appearance["states"] != index["states"]:
            raise contract.PartsContractError("player states differ from active appearance")
        self.documents = tuple(registry.values())
        self.load = compose_parts.AssetLoader(root, index)
        self.players, self.lock = OrderedDict(), threading.Lock()
        catalog = registry[index["catalog"]["id"]]
        rig = registry[index["rig"]["id"]]
        topology = registry[rig["topology"]["id"]]
        slots = {s["id"]:s for s in topology["slots"]}
        optional = {s["id"] for s in slots.values() if s["optional"]}
        self.options = {s["id"]: {r["id"]: r for r in s["options"]} for s in catalog["slots"]}
        for slot in optional:
            self.options[slot][""] = None
        resolved = contract.resolve_selections(self.appearance, catalog)
        self.metadata = {"id": self.appearance["id"], "canvas": registry[index["rig"]["id"]]["canvas"],
            "motion": index["motion"], "states": [], "slots": [], "release_ready": False,
            "mode": "live-current-sample", "appearance_clips_baked": 0}
        motion = registry[index["motion"]["id"]]
        for state in index["states"]:
            clip = next(c for c in motion["clips"] if c["state"] == state)
            self.metadata["states"].append({"id": state, "samples": len(clip["samples"]),
                "duration_ms": str(Fraction(sum(s["duration_ticks"] for s in clip["samples"])*1000,clip["timebase"]))})
        for slot in catalog["slots"]:
            if slot["options"]:
                current = resolved.get(slot["id"])
                selected = current["id"] if isinstance(current,dict) else ""
                options = [{"id": r["id"], "label": r["id"]} for r in slot["options"]]
                if slot["id"] in optional:
                    options.insert(0,{"id":"","label":"기본 파츠" if slots[slot["id"]]["overrides"] else "없음"})
                self.metadata["slots"].append({"id": slot["id"], "label": slot["label"], "selected": selected,
                    "options": options})

    def frame(self, query):
        if set(query) - {"state", "time_ms", *self.options} or any(len(v)!=1 for v in query.values()):
            raise contract.PartsContractError("unknown or duplicate player parameter")
        state = query.get("state", [self.appearance["states"][0]])[0]
        try:
            raw = query.get("time_ms", ["0"])[0]
            if len(raw)>16 or not raw.isascii() or not raw.isdecimal():
                raise ValueError()
            milliseconds = int(raw)
        except (TypeError,ValueError):
            raise contract.PartsContractError("time_ms requires a bounded nonnegative integer") from None
        selections = {}
        for slot, values in query.items():
            if slot in {"state","time_ms"}:
                continue
            if values[0] not in self.options[slot]:
                raise contract.PartsContractError(f"unknown option for {slot}")
            selections[slot] = self.options[slot][values[0]]
        key = tuple(sorted((slot,ref["id"] if ref else "") for slot,ref in selections.items()))
        with self.lock:
            player = self.players.get(key)
            if player is None:
                player = PartsPlayer(self.appearance,self.documents,self.load)
                player.set_selection(selections)
                self.players[key] = player
                if len(self.players)>32:
                    self.players.popitem(last=False)
            self.players.move_to_end(key)
            player.set_state(state,restart=False)
            player.seek(Fraction(milliseconds,1000))
            image, evidence = player.frame()
            return {"image":"data:image/png;base64,"+base64.b64encode(assets.png_bytes(image)).decode("ascii"),
                **evidence}


def handler_for(session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, content_type):
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if len(self.path)>4096:
                self.reply(414,b'{"error":"request too long"}',"application/json")
                return
            request = urlsplit(self.path)
            try:
                if request.path == "/api/metadata":
                    result = session.metadata
                elif request.path == "/api/frame":
                    result = session.frame(parse_qs(request.query,keep_blank_values=True,max_num_fields=24))
                elif request.path in {"/","/index.html","/player.js","/player.css"}:
                    name = "index.html" if request.path=="/" else request.path[1:]
                    content_type = {"index.html":"text/html","player.js":"text/javascript","player.css":"text/css"}[name]
                    self.reply(200,(STATIC/name).read_bytes(),content_type+"; charset=utf-8")
                    return
                else:
                    self.reply(404,b'{"error":"not found"}',"application/json")
                    return
                self.reply(200,json.dumps(result,ensure_ascii=False).encode(),"application/json; charset=utf-8")
            except (contract.PartsContractError,ValueError) as exc:
                self.reply(400,json.dumps({"error":str(exc)}).encode(),"application/json")
    return Handler


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--assembly",required=True,type=Path)
    parser.add_argument("--port",type=int,default=8766)


def run(*,assembly,port=8766):
    session = PartsSession(assembly)
    server = ThreadingHTTPServer(("127.0.0.1",port),handler_for(session))
    print(f"Parts player: http://127.0.0.1:{server.server_port}",flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
