# SPDX-License-Identifier: Apache-2.0
"""Local review desk for part productions: see each pose, approve or reject, redraw the rejected.

Every request reads the workspace afresh, so decisions and rebuilt parts show at once.
Writes (decisions and production steps) need the session token supplied to the local page; the
server listens on 127.0.0.1 only. Nothing here releases anything.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import secrets
import threading
import time
import tempfile
import zipfile
from urllib.parse import parse_qs, unquote, urlsplit

from PIL import Image
from sprite_gen._deps import np

from sprite_gen.curate import parts_motion
from sprite_gen.frames import parts_production as production
from sprite_gen.frames import parts_reference
from sprite_gen.frames import parts_quality
from sprite_gen.frames import parts_segments
from sprite_gen.frames import parts_part_edit
from sprite_gen.frames import parts_revalidation
from sprite_gen.frames import parts_independent, parts_vision
from sprite_gen.spec import parts_assets as assets, parts_contract as contract, parts_workspace as workspace
from sprite_gen.spec.runio import atomic_write_text

STATIC = Path(__file__).resolve().parent / "parts_player"
MAX_BODY = 64 * 1024


class ReviewDesk:
    def __init__(self, assembly):
        self.assembly = Path(assembly).absolute()
        self.token = secrets.token_urlsafe(18)
        self.lock = threading.Lock()
        self.actions = threading.Lock()
        self.jobs_lock = threading.Lock()
        self.jobs = {}
        self._renders = {}
        root, _, _, _ = self._state()
        self.jobs_folder = root / "review-jobs"
        if self.jobs_folder.exists():
            for file in self.jobs_folder.glob("*.json"):
                job = workspace.decode_json(file.read_bytes())
                if job["state"] == "running":
                    job.update(state="failed", error="Server restarted during rework; inspect the retained candidate and request a retry.", finished=time.time())
                    atomic_write_text(file, workspace.json_bytes(job).decode())
                    part, track = self._split(job["part"])
                    state = production._load(root, part)
                    for pose in job["poses"]:
                        row = production._track(state, track)["poses"].get(pose, {})
                        if row.get("rework") and job.get('type') != 'vision':
                            row["rework"].update(status="failed", error=job["error"])
                    production._save(root, state)
                self.jobs[job["id"]] = job

    @contextmanager
    def exclusive(self):
        if not self.actions.acquire(blocking=False):
            raise contract.PartsContractError("another production action is running")
        try:
            yield
        finally:
            self.actions.release()

    def update_job(self, identity, **updates):
        with self.jobs_lock:
            self.jobs[identity].update(updates)
            self.jobs_folder.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.jobs_folder / f"{identity}.json", workspace.json_bytes(self.jobs[identity]).decode())

    def _state(self):
        return parts_motion._active(self.assembly)

    def productions(self):
        result = production.status(self.assembly)
        with self.jobs_lock:
            result["active_job"] = next((deepcopy(j) for j in self.jobs.values() if j["state"] == "running"), None)
        return result

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
                          "note": row.get("note"), "reviewer": row.get("reviewer"), "check": row.get("check"),
                          "rework": row.get("rework")})
            poses[-1]['vision'] = parts_vision.current(root,index,state,pose,track=track)
        return {"part": name, "config": state["config"], "track": track or production.MAIN, "poses": poses,
                "revalidation": parts_revalidation.latest(self.assembly, part, track=track),
                "canvas": registry[index["rig"]["id"]]["canvas"],
                "clips": [{"id": c["id"], "state": c["state"], "loop": c["loop"],
                           "samples": [{"pose": s["pose_id"], "duration_ms": s["duration_ticks"] * 1000 / c["timebase"]}
                                       for s in c["samples"]]} for c in motion["clips"]
                          if c["state"] in production._states(state, index)],
                "sampling": registry[index["rig"]["id"]].get("sampling", "nearest"),
                "built": state["built"]["part"] if state["built"] else None,
                "built_current": production._current(state, registry, order),
                "batches": production._track(state, track)["batches"][-3:]}

    def candidates(self, name, pose):
        part, track = self._split(name)
        root, index, registry, _ = self._state()
        state = production._load(root, part)
        row = production._track(state, track)["poses"].get(pose)
        if row is None:
            raise contract.PartsContractError("unknown pose")
        motion = registry[index["motion"]["id"]]
        context = (motion, index, registry, registry[registry[index["rig"]["id"]]["topology"]["id"]],
                   production.compose_parts.AssetLoader(root, index))
        result = []
        for candidate in reversed(row["candidates"]):
            record = production.candidate_record(root, state, pose, candidate, track=track)
            if candidate == row["selected"] and row.get("check"):
                metrics = row["check"]["metrics"]
                findings = [("warning", f) for f in row["check"]["findings"]]
            else:
                metrics, findings = production._measure(root, state["config"], production._settings(state["config"], track), candidate, context)
            result.append({"id": record["digest"], "path": candidate, "selected": candidate == row["selected"],
                           "images": list(record["images"]), "placement": record["placement"], "source": record.get("source"),
                           "method": record["method"], "reference": record.get("generation_reference") or record.get('independent_reference'),
                           "compatible": record["motion"] == index["motion"],
                           "layers": [item['id'] for item in record.get('independent_layers',[])],
                           "metrics": metrics, "findings": [message for _, message in findings],
                           "criteria": parts_quality.criteria(metrics, mode=state["config"]["mode"]),
                           "status": row["check"]["status"] if candidate == row["selected"] and row.get("check") else
                           "error" if any(level == "error" for level, _ in findings) else "warning" if findings else "partial"})
        return {"part": name, "pose": pose, "candidates": result}

    def reference_archive(self, name, batch):
        part, track = self._split(name)
        root, _, _, _ = self._state()
        state = production._load(root, part)
        entry = production._batch(production._track(state, track), batch)
        if not entry.get("reference"):
            raise contract.PartsContractError("this legacy batch has no frozen generation reference")
        if entry.get('layout') == 'independent':
            document, base = parts_independent.read_reference(root,entry['reference'])
            refs = document['files'].values()
            reader = assets.SourceReader(root/base)
        else:
            document, base = parts_reference.read(root, entry["reference"])
            refs = parts_reference.files(document)
            reader = parts_reference.verify(root, document, base)
        data = BytesIO()
        seen = set()
        with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            def put(file, encoded):
                info = zipfile.ZipInfo(file, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, encoded)
            put("reference.json", assets.read_file(root, entry["reference"]["file"]))
            for ref in refs:
                if ref["file"] not in seen:
                    put(ref["file"], reader.read(ref["file"], ref["sha256"]))
                    seen.add(ref["file"])
        return data.getvalue()

    def render(self, name, pose, kind, candidate=None, *, dx=0, dy=0, piece=None):
        """PNG of one pose: the part on the body alone (built), or the drawn cell / cut on what it was drawn over."""
        part, track = self._split(name)
        root, index, registry, _ = self._state()
        state = production._load(root, part)
        row = production._track(state, track)["poses"].get(pose)
        if row is None or row.get("selected") is None:
            raise contract.PartsContractError(f"{pose} has no drawing yet")
        allowed = ("position", "body", "body-raw", "body-zraw", "cut", "part", "visible", "drawn", "reference", "interfaces", "errors")
        if kind not in allowed:
            raise contract.PartsContractError("unknown render view")
        location = row["selected"] if candidate is None else next(
            (c for c in row["candidates"] if Path(c).name == candidate), None)
        if location is None:
            raise contract.PartsContractError("unknown candidate")
        record = production.candidate_record(root, state, pose, location, track=track)
        base = root / location
        built = state["built"]
        box = None
        render_key = None
        cached_body_image = None
        motion = registry[index["motion"]["id"]]
        order, _ = production._poses(motion, production._states(state, index))
        if kind in ("body", "body-zraw") and location == row["selected"] and production._current(state, registry, order):
            digest = contract.content_digest({"part": built["part"], "body": index["body"],
                "rig": index["rig"], "motion": index["motion"],
                "states": production._states(state, index)})
            render_key = (digest, pose)
            with self.lock:
                if render_key not in self._renders:
                    # A body ownership revision changes z-order even when its
                    # pixels and the selected part's digest remain identical.
                    # Keep one context, bounding native-frame cache memory.
                    self._renders.clear()
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
                cached_body_image = self._renders[render_key]
        if kind == "position":
            from sprite_gen.frames import parts_candidate_scene
            if record["motion"] != index["motion"] or state["config"]["role"] != "part":
                raise contract.PartsContractError("position preview needs a compatible part candidate")
            raw = Image.open(base / record["images"]["part"]["file"]).convert("RGBA")
            raw = parts_part_edit.translate_image(raw, dx, dy)
            if state["config"]["visibility"] == "mask" and "render_mask" in record["images"]:
                raw = production._visible_part(raw, Image.open(base / record["images"]["render_mask"]["file"]))
            layers = parts_independent.candidate_layers(record,base) or None
            if piece is not None and (not layers or piece not in {item['id'] for item in layers}):
                raise contract.PartsContractError('unknown independent position layer')
            if layers:
                for layer in layers:
                    if piece is None or layer['id']==piece:layer['image'] = parts_part_edit.translate_image(layer['image'],dx,dy)
            plan, sample, load = parts_candidate_scene.scene(root, index, registry, state["config"], pose, raw, record["placement"], independent_layers=layers)
            image = production.compose_parts.render_sample(plan, sample, load)[0]
        elif kind == "body-zraw":
            if box is None or track is not None or state["config"]["role"] != "part":
                raise contract.PartsContractError("zmap-only preview needs the selected main-track part and a current build")
            image = self.raw_zmap(root, index, registry, state, pose, record, base, motion)
        elif kind == "body" and box is not None and track is None:
            image = cached_body_image
        elif kind in ("part", "cut", "visible", "drawn", "reference"):
            key = {"cut": "part", "visible": "render_part", "reference": "under"}.get(kind, kind)
            if key not in record["images"]:
                if kind in ("reference", "drawn"):
                    raise contract.PartsContractError("this candidate did not retain this source view")
                key = "part"
            image = Image.open(base / record["images"][key]["file"]).convert("RGBA")
        elif kind in ("interfaces", "errors"):
            image = Image.open(base / "under.png").convert("RGBA")
            masks = {n: Image.open(base / ref["file"]).convert("L") for n in parts_reference.COLORS
                     if (ref := record["images"].get(f"qa_{n}"))}
            if kind == "errors":
                raw = Image.open(base / record["images"]["part"]["file"]).convert("RGBA")
                ink = np.asarray(raw)[..., 3] > 0
                if state["config"]["visibility"] == "zmap" and (state["config"]["segments"] or state['config']['independent']):
                    from sprite_gen.frames import parts_candidate_scene
                    required = None
                    if ref := record["images"].get("qa_required"):
                        encoded = assets.SourceReader(base).read(ref["file"],ref["sha256"])
                        required = np.asarray(Image.open(BytesIO(encoded)).convert("L")) > 0
                    allowed_pixels = parts_candidate_scene.visibility(root,index,registry,state["config"],pose,raw,record["placement"],required=required,
                        independent_layers=parts_independent.candidate_layers(record,base) or None)
                else:
                    allowed_pixels = np.asarray(Image.open(base / "render_mask.png").convert("L")) > 0 if state["config"]["visibility"] == "mask" and "render_mask" in record["images"] else np.ones_like(ink)
                ink &= allowed_pixels
                masks = {n: Image.fromarray((255 * ((np.asarray(m) > 0) & allowed_pixels & ~ink if n == "required" else
                                (np.asarray(m) > 0) & ink if n == "protected" else ~(np.asarray(m) > 0) & ink)).astype("uint8"))
                         for n, m in masks.items() if n != "occluded"}
            image = parts_reference.overlay(image, masks)
        else:
            image = Image.open(base / "under.png").convert("RGBA")
            ref = record["images"]["part"] if kind == "body-raw" else record["images"].get("render_part", record["images"]["part"])
            image.alpha_composite(Image.open(base / ref["file"]).convert("RGBA"))
        smooth = registry[index["rig"]["id"]].get("sampling", "nearest") == "smooth"
        scale = min(1, 768/max(image.size)) if smooth else 4
        large = image.resize((max(1, round(image.width*scale)), max(1, round(image.height*scale))),
                             Image.Resampling.LANCZOS if smooth else Image.Resampling.NEAREST)
        out = BytesIO()
        large.save(out, "PNG")
        return out.getvalue()

    def raw_zmap(self, root, index, registry, state, pose, record, base, motion):
        """Restore raw garment pieces while retaining the actual resolver's draw plan.

        Partitioning uses the same split_piece command as production. Temporary
        preview files never activate packs, replace source images or change QC.
        """
        config = state["config"]
        if record.get('independent_layers'):
            from sprite_gen.frames import parts_candidate_scene
            raw=Image.open(base/record['images']['part']['file']).convert('RGBA')
            plan,sample,load=parts_candidate_scene.scene(root,index,registry,config,pose,raw,record['placement'],
                independent_layers=parts_independent.candidate_layers(record,base))
            return production.compose_parts.render_sample(plan,sample,load)[0]
        _, first = production._poses(motion, production._states(state, index))
        clip_id, sample = first[pose]
        raw = Image.open(base / record["images"]["part"]["file"]).convert("RGBA")
        images = {config["slot"]: raw}
        if config["pieces"]:
            with tempfile.TemporaryDirectory(prefix="parts-zmap-preview-") as temporary:
                folder = Path(temporary)
                placement = {**record["placement"], "image": {
                    "file": (base / record["images"]["part"]["file"]).relative_to(root).as_posix(),
                    "sha256": record["images"]["part"]["sha256"]}}
                request = {"motion": index["motion"], "pieces": [{"id": config["slot"],
                           "node": config["node"], "pose_map": {pose: placement}}]}
                spec = folder / "source.json"
                spec.write_bytes(workspace.json_bytes(request))
                result = parts_segments.split_piece(spec, root / config["segments"], folder / "split",
                    piece=config["slot"], source_root=root, by_component=config["by_component"],
                    into=[(p["id"], p["node"], p["segments"]) for p in config["pieces"]])
                split = workspace.decode_json(Path(result["spec"]).read_bytes())
                images = {p["id"]: Image.open(Path(result["source_root"]) / p["pose_map"][pose]["image"]["file"]).convert("RGBA")
                          for p in split["pieces"]}
        load = production.compose_parts.AssetLoader(root, index)
        # Re-resolve rather than swap rasters in an already filtered plan: a
        # masked piece may have become empty and hidden, but its raw art is visible.
        pack = deepcopy(registry[state["built"]["part"]["id"]])
        pack.pop("digest")
        replacements = {}
        for binding in pack["bindings"]:
            for piece in binding["pieces"]:
                if piece["id"] not in images:
                    continue
                image = images[piece["id"]]
                reference = {"file": f"preview-raw/{piece['id']}.png", "sha256": assets.sha256(assets.png_bytes(image))}
                replacements[reference["file"]] = image
                for clip in motion["clips"]:
                    for current in clip["samples"]:
                        if current["pose_id"] == pose:
                            piece["sample_map"][clip["id"]][current["id"]].update(
                                image=reference, visible=image.getbbox() is not None, source=None)
        pack = contract.seal(pack)
        preview_ref = contract.reference(pack)
        catalog = production.parts_import._replace_refs(registry[index["catalog"]["id"]], state["built"]["part"], preview_ref)
        catalog.pop("digest")
        catalog = contract.seal(catalog)
        preview_index = {**index, "catalog": contract.reference(catalog)}
        preview_registry = {name: document for name, document in registry.items() if document["kind"] not in
                            {"parts-review", "parts-build-lock", "parts-appearance", "parts-fitting", "parts-stability"}}
        preview_registry.update({pack["id"]: pack, catalog["id"]: catalog})
        plan = production._part_plan(preview_index, preview_registry, preview_ref, production._states(state, index))
        ordered = next(c for c in plan["clips"] if c["id"] == clip_id)
        frame = next(s for s in ordered["samples"] if s["id"] == sample["id"])
        def unmasked(pack, reference):
            if pack == preview_ref and reference["file"] in replacements:
                return replacements[reference["file"]].copy()
            return load(pack, reference)
        return production.compose_parts.render_sample(plan, frame, unmasked)[0]

    def act(self, name, action, body):
        part, track = self._split(name)
        with self.exclusive():
            with self.lock:
                self._renders.clear()
            if action == "review":
                return production.review(self.assembly, part, reviewer=body.get("reviewer"), track=track,
                                         approve=body.get("approve", []), reject=body.get("reject", []),
                                         note=body.get("note") or None)
            if action == "sheet":
                return production.sheet(self.assembly, part, poses=body.get("poses") or None, track=track,layers=body.get('layers'))
            if action == "draw":
                return production.draw(self.assembly, part, provider=body.get("provider"), track=track,
                                       model=body.get("model"), dry_run=bool(body.get("dry_run")))
            if action == "slice":
                return production.slice_batch(self.assembly, part, track=track)
            if action == "check":
                return production.check(self.assembly, part, track=track)
            if action == "revalidate":
                return parts_revalidation.prepare(self.assembly, part, track=track)
            if action == "build":
                return self.build_preserving_appearance(part)
            if action == "select":
                return production.select_candidate(self.assembly, part, body.get("pose"), body.get("candidate"), track=track)
            if action == "edit":
                return parts_part_edit.edit(self.assembly, part, body.get("pose"), body.get("operations"), candidate=body.get("candidate"), track=track)
            if action == "apply":
                selected = production.select_candidate(self.assembly, part, body.get("pose"), body.get("candidate"), track=track)
                checked = production.check(self.assembly, part, track=track)
                built = self.build_preserving_appearance(part)
                return {"selected": selected, "check": checked, "build": built, "art_approved": False}
            if action == "visibility":
                mode = contract._enum("zmap", "mask")(body.get("visibility"), "visibility")
                root, _, _, _ = self._state()
                state = production._load(root,part)
                state["config"]["visibility"] = mode
                with tempfile.TemporaryDirectory(prefix="parts-visibility-") as folder:
                    file = Path(folder)/"config.json"
                    file.write_bytes(workspace.json_bytes(state["config"]))
                    production.configure(self.assembly,part,file)
                return {"visibility":mode,"check":production.check(self.assembly,part),"build":self.build_preserving_appearance(part),"art_approved":False}
        raise contract.PartsContractError(f"unknown action {action}")

    def build_preserving_appearance(self, part):
        """Rebind the saved outfit after a part revision; never carry art approval."""
        from sprite_gen.curate import parts_appearance
        _, before, documents, _ = self._state()
        appearance = documents[before["appearance"]["id"]] if before["appearance"] else None
        result = production.build(self.assembly, part)
        _, after, current, _ = self._state()
        if appearance and after["appearance"] is None:
            def rebind(value):
                if isinstance(value, dict):
                    if set(value) == {"id", "digest"} and value["id"] in current:
                        return contract.reference(current[value["id"]])
                    return {k: rebind(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [rebind(v) for v in value]
                return value
            selection = {k: rebind(appearance[k]) for k in ("id", "selections", "palette", "mirror", "presets") if k in appearance}
            selection.update(kind="parts-appearance-selection", schema_version=1)
            with tempfile.TemporaryDirectory(prefix="parts-outfit-rebind-") as folder:
                file = Path(folder)/"selection.json"
                file.write_bytes(workspace.json_bytes(selection))
                result["appearance"] = parts_appearance.create(self.assembly, file)
        return result

    def job(self, identity):
        with self.jobs_lock:
            if identity not in self.jobs:
                raise contract.PartsContractError("unknown rework job")
            job = deepcopy(self.jobs[identity])
        if job['state'] == 'running' and job['step'] == 'draw' and job.get('batch'):
            part, track = self._split(job['part'])
            state = production._load(self.assembly.parent, part)
            batch = production._batch(production._track(state, track), job['batch'])
            pages = batch['pages']
            job['progress'] = {'completed': sum(bool(page.get('reuse')) or (self.assembly.parent/page['art']).is_file() for page in pages), 'total': len(pages)}
        return job

    def start_rework(self, name, body):
        part, track = self._split(name)
        if not self.actions.acquire(blocking=False):
            raise contract.PartsContractError("another production action is running")
        try:
            request = production.request_rework(self.assembly, part, body.get("poses"), body.get("note"), track=track)
            identity = secrets.token_hex(8)
            with self.jobs_lock:
                self.jobs[identity] = {"id": identity, "part": name, "poses": request["poses"],
                                       "state": "running", "step": "requested", "started": time.time(), "results": {}}
            self.update_job(identity)
            def execute():
                try:
                    batch = None
                    stages = [("sheet", lambda: production.sheet(self.assembly, part, poses=request["draw_poses"], track=track,layers=body.get('layers'))),
                              ("draw", lambda: production.draw(self.assembly, part, batch=batch, track=track)),
                              ("slice", lambda: production.slice_batch(self.assembly, part, batch=batch, track=track)),
                              ("check", lambda: production.check(self.assembly, part, track=track)),
                              ("build", lambda: self.build_preserving_appearance(part))]
                    config = production._load(self.assembly.parent,part)['config']
                    if config['independent'] and config['independent']['require_vision']:
                        stages.append(('vision',lambda: {'poses':[parts_vision.inspect(self.assembly,part,pose,track=track) for pose in request['poses']]}))
                    for step, operation in stages:
                        self.update_job(identity, step=step)
                        result = operation()
                        if step == "sheet":
                            batch = result["batch"]
                            self.update_job(identity, batch=batch)
                        results = self.job(identity)["results"]
                        results[step] = result
                        self.update_job(identity, results=results)
                        if step == "draw" and any(p["status"] == "failed" for p in result["pages"]):
                            raise contract.PartsContractError("provider failed: " + str(result["pages"]))
                    self.update_job(identity, state="completed", step="completed", finished=time.time())
                except Exception as exc:
                    self.update_job(identity, state="failed", error=str(exc), finished=time.time())
                    root, _, _, _ = self._state()
                    state = production._load(root, part)
                    for pose in request["poses"]:
                        row = production._track(state, track)["poses"][pose]
                        if row.get("rework"):
                            row["rework"].update(status="failed", error=str(exc))
                    production._save(root, state)
                finally:
                    with self.lock:
                        self._renders.clear()
                    self.actions.release()
            threading.Thread(target=execute, daemon=True).start()
            return self.job(identity)
        except Exception:
            self.actions.release()
            raise


    def start_vision(self,name,body):
        part,track=self._split(name)
        if not self.actions.acquire(blocking=False):raise contract.PartsContractError('another production action is running')
        try:
            root,_,_,_=self._state();state=production._load(root,part);pose=body.get('pose')
            if not production._track(state,track)['poses'].get(pose,{}).get('selected'):
                raise contract.PartsContractError('select a produced candidate before vision inspection')
            identity=secrets.token_hex(8)
            with self.jobs_lock:
                self.jobs[identity]={'id':identity,'part':name,'poses':[pose],'type':'vision','state':'running','step':'vision','started':time.time(),'results':{}}
            self.update_job(identity)
            def execute():
                try:
                    result=parts_vision.inspect(self.assembly,part,pose,provider=body.get('provider','codex'),track=track)
                    self.update_job(identity,state='completed',step='completed',results={'vision':result},finished=time.time())
                except BaseException as exc:
                    self.update_job(identity,state='failed',error=str(exc),finished=time.time())
                finally:self.actions.release()
            threading.Thread(target=execute,daemon=True).start()
            return self.job(identity)
        except BaseException:
            self.actions.release();raise


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
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'")
            self.end_headers()
            self.wfile.write(data)

        def json(self, value, status=200):
            self.reply(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def valid_host(self):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.json({"error": "wrong review host"}, 403)
                return False
            return True

        def do_GET(self):
            if not self.valid_host():
                return
            path = urlsplit(self.path).path
            try:
                if path in ("/", "/review.html"):
                    page = (STATIC / "review.html").read_text()
                    readonly = parse_qs(urlsplit(self.path).query).get("readonly") == ["1"]
                    access = '<meta name="review-mode" content="readonly">' if readonly else f'<meta name="review-token" content="{desk.token}">'
                    self.reply(200, page.replace('<title>', access + '<title>', 1).encode(), "text/html; charset=utf-8")
                elif path in ("/review.js", "/review.css"):
                    self.reply(200, (STATIC / path[1:]).read_bytes(),
                               "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
                elif path == "/api/status":
                    self.json(desk.productions())
                elif path.startswith("/api/jobs/"):
                    self.json(desk.job(path.removeprefix("/api/jobs/")))
                elif path.startswith("/api/part/"):
                    self.json(desk.part(unquote(path.removeprefix("/api/part/"))))
                elif path.startswith("/api/candidates/"):
                    name, pose = unquote(path.removeprefix("/api/candidates/")).split("/")
                    self.json(desk.candidates(name, pose))
                elif path.startswith("/api/reference/") and path.endswith(".zip"):
                    name, batch = unquote(path.removeprefix("/api/reference/").removesuffix(".zip")).split("/")
                    self.reply(200, desk.reference_archive(name, batch), "application/zip")
                elif path.startswith("/api/revalidation/") and path.endswith(".zip"):
                    name, identity = unquote(path.removeprefix("/api/revalidation/").removesuffix(".zip")).split("/")
                    part, track = desk._split(name)
                    self.reply(200, parts_revalidation.archive(desk.assembly, part, identity, track=track), "application/zip")
                elif path.startswith("/api/render/") and path.endswith(".png"):
                    name, pose, kind = unquote(path.removeprefix("/api/render/").removesuffix(".png")).split("/")
                    query = parse_qs(urlsplit(self.path).query)
                    candidate = query.get("candidate", [None])[0]
                    dx, dy = (int(query.get(axis, ["0"])[0]) for axis in ("dx", "dy"))
                    self.reply(200, desk.render(name, pose, kind, candidate, dx=dx, dy=dy, piece=query.get('piece',[None])[0]), "image/png")
                else:
                    self.json({"error": "not found"}, 404)
            except (contract.PartsContractError, ValueError, OSError) as exc:
                self.json({"error": str(exc)}, 400)

        def do_POST(self):
            if not self.valid_host():
                return
            path = urlsplit(self.path).path
            origin = self.headers.get("Origin")
            allowed_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if origin and origin not in allowed_origins:
                self.json({"error": "wrong review origin"}, 403)
                return
            if self.headers.get("X-Review-Token") != desk.token:
                self.json({"error": "missing or wrong review token"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= MAX_BODY:
                    raise ValueError("request too large")
                body = json.loads(self.rfile.read(length) or b"{}")
                if not path.startswith("/api/act/") or not isinstance(body, dict):
                    raise ValueError("expected POST /api/act/<part>/<action> with a JSON object")
                name, action = unquote(path.removeprefix("/api/act/")).rsplit("/", 1)
                self.json(desk.start_rework(name, body) if action == "rework" else desk.start_vision(name,body) if action=='vision' else desk.act(name, action, body))
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
    print(f"Parts review desk: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
