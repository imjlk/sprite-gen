# SPDX-License-Identifier: Apache-2.0
"""Prepare native motion revalidation evidence and concrete rework requests.

Uses existing body clips. No provider calls, part selection, build, or approval.
"""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw
from io import BytesIO
from sprite_gen._deps import np
from sprite_gen.frames import parts_revalidation as revalidation, parts_production as production
from sprite_gen.spec import parts_workspace as workspace, parts_assets as assets

PARTS = ("footwear.sneakers", "socks.crew", "upper.cream", "lower.navy", "hair.bob", "eyes.open")
VISUAL_TRIAGE = {
    "footwear.sneakers": {
        "poses": ["run.02", "run.05", "run.08", "run.13", "run.17"],
        "note": "현재 바디 포즈·발 소유권 지도를 먼저 확인한다. 발에서 떨어진 신발 및 발끝/발바닥 각도 불일치를 수정한다. 양쪽 발을 개별 기준으로 맞추고, 같은 디자인의 독립 신발 파츠를 이 프레임 좌표에 제작한다. 바디 발을 지워 덮임 검사를 우회하지 않는다. 앞뒤 프레임에서 크기·방향·앞뒤 발 정체성을 함께 검수한다.",
    },
    "socks.crew": {
        "poses": ["run.02", "run.05", "run.08", "run.13", "run.17"],
        "note": "닫힌 발 전체 양말 디자인이다. 발을 포함한 양말 영역을 종아리 방향에 통째로 회전·확대하지 않는다. 종아리/발목/뒤꿈치/발끝의 실제 포즈를 별도 참조하여 독립 파츠를 제작한다. 신발과 양말은 각각 검수하고 함께 켠 조합도 확인한다. 필수 발 지도와 앞뒤 프레임을 확인하고 바디 발을 숨겨 통과시키지 않는다.",
    },
    "upper.cream": {
        "poses": ["run.02", "run.05", "run.08", "run.17"],
        "note": "시각 비교에서 어깨 위 바디 기본 의상이 드러나고 소매 연결이 팔 각도와 맞지 않는다. 현재 프레임의 목/어깨/몸통/양쪽 소매를 기준으로 독립 상의 이미지를 제작한다. 허용 의상 영역과 보호할 손·얼굴 지도를 먼저 작성한다. 팔을 올린 프레임은 소매 형태와 손의 앞뒤 가림을 zmap 빌드로 확인한다. 단순 전체 PNG 이동으로 승인하지 않는다.",
    },
    "lower.navy": {
        "poses": ["run.02", "run.05", "run.13", "run.17", "walk.22"],
        "note": "시각 비교에서 바디 기본 반바지가 드러나며 새 하의가 올린 허벅지 포즈를 따르지 않는다. 허리·양쪽 바지통을 실제 허벅지 방향에 맞춘 프레임별 독립 파츠로 제작한다. 허용 의상 영역/허리 덮임 기준을 먼저 작성한다. 다리가 겹치는 프레임에서 앞뒤 소유권 지도와 zmap 순서를 함께 검수하고 가림 없는 원본과 실제 빌드를 비교한다.",
    },
}


def audit_registration(out):
    """Check the guessed anchors against body-foot ownership, also unapproved.

    This is a consistency check, not independent anatomical ground truth.
    An empty foot guide can mean occlusion OR bad ownership. Never hide art on
    that signal alone; a shoe can protrude beyond an occluded bare foot.
    """
    source = out / "registration-provenance.json"
    profile = workspace.decode_json(source.read_bytes())
    root = out / "library"
    index, registry = workspace.read_workspace(root / "assembly.json")
    motion = registry[index["motion"]["id"]]
    frames = []
    for clip in motion["clips"]:
        for sample in clip["samples"]:
            pose = sample["pose_id"]
            geometry = profile["geometry"][pose]
            points = geometry["points"]
            feet = []
            for i, node in enumerate(("foot_front", "foot_back")):
                path = f"inputs/body-segments/{node}/{clip['id']}/{sample['id']}.png"
                encoded = assets.SourceReader(root).read(path)
                image = Image.open(BytesIO(encoded)).convert("RGBA")
                ys, xs = np.nonzero(np.asarray(image)[..., 3] > 0)
                def distance(point):
                    return round(float(np.sqrt((xs - point[0]) ** 2 + (ys - point[1]) ** 2).min()), 2) if len(xs) else None
                ankle, toe = points[2 + i], points[4 + i]
                scales = {name: round(float(np.sqrt(abs(np.linalg.det(np.array(profile["parts"][name]["registration_matrices"][pose][i])[:2, :2])))), 4)
                          for name in ("footwear.sneakers", "socks.crew")}
                feet.append({"node": node, "guide": {"file": path, "sha256": assets.sha256(encoded)},
                             "visible_foot_pixels": len(xs), "bbox": image.getbbox(), "ankle": ankle, "toe": toe,
                             "ankle_distance_to_foot_pixels": distance(ankle), "toe_distance_to_foot_pixels": distance(toe),
                             "uniform_registration_scales": scales,
                             "needs_anchor_review": not len(xs) or distance(toe) > 12})
            frames.append({"pose": pose, "feet": feet, "needs_review": any(f["needs_anchor_review"] for f in feet)})
    report = {"motion": index["motion"], "body": index["body"], "frames": frames,
              "profile_sha256": assets.sha256(source.read_bytes()), "body_ownership_visually_approved": False,
              "threshold_px": 12, "threshold_is_triage_only": True,
              "policy": "Review guessed ankle/toe coordinates, near/far identity and occlusion before generating replacement art. Uniform scaling from a projected toe vector can collapse the whole shoe. Empty ownership is not permission to delete a shoe."}
    (out / "registration-anchor-audit.json").write_bytes(workspace.json_bytes(report))
    return report


def run(out, *, reuse_current=False, queue=False):
    assembly = out / "library/assembly.json"
    reports = {}
    for name in PARTS:
        part = "illustration.motion." + name
        previous = revalidation.latest(assembly, part) if reuse_current else None
        print("Analyzing", name, flush=True)
        reports[name] = previous if previous and previous["current"] else revalidation.prepare(assembly, part)
        print("Ready", name, reports[name]["id"], flush=True)
    summary = {name: {"id": r["id"], "frames": len(r["frames"]),
                      "errors": sum(f["status"] == "error" for f in r["frames"]),
                      "without_placement_guides": sum(not f.get("criteria_registered") for f in r["frames"]),
                      "worst": [{k: f.get(k) for k in ("pose", "coverage", "translation", "next_step")}
                                for f in r["frames"][:5]]} for name, r in reports.items()}
    requests = []
    registration = audit_registration(out)
    if queue:
        for name, triage in VISUAL_TRIAGE.items():
            part = "illustration.motion." + name
            rows = production._load(assembly.parent, part)["tracks"]["main"]["poses"]
            pending = all(rows[p].get("rework", {}).get("status") == "requested"
                          and rows[p]["rework"].get("note") == triage["note"]
                          and rows[p]["rework"].get("replaces") == rows[p]["selected"] for p in triage["poses"])
            request = {"part": part, **triage, "already_requested": True, "art_approved": False} if pending else production.request_rework(assembly, part, triage["poses"], triage["note"])
            requests.append(request)
    result = {"summary": summary, "visual_triage": VISUAL_TRIAGE, "requests": requests,
              "registration_anchor_review_frames": [f["pose"] for f in registration["frames"] if f["needs_review"]],
              "requests_are_running": False, "provider_calls": 0, "art_approved": False,
              "scope": "Frozen references and rework requests; independent motion-specific art is still to be produced."}
    (out / "revalidation-plan.json").write_bytes(workspace.json_bytes(result))
    # Diagnostic board: raw placement, with zmap-measured missing pixels.
    shoe = reports["footwear.sneakers"]
    folder = revalidation._folder(assembly.parent, shoe["part"], shoe["track"]) / shoe["id"]
    reader = assets.SourceReader(folder)
    board = Image.new("RGB", (1200, 960), (235, 235, 240))
    draw = ImageDraw.Draw(board)
    for i, pose in enumerate(["run.17", "run.13", "run.08", "run.05", "walk.12", "walk.11"]):
        row = next(r for r in shoe["frames"] if r["pose"] == pose)
        refs = row["evidence"]
        def image(name):
            ref = refs[name]
            return Image.open(BytesIO(reader.read(ref["file"], ref["sha256"]))).convert("RGBA")
        body = image("active_body")
        body.alpha_composite(image("part"))
        body.alpha_composite(image("errors"))
        body = body.crop((150, 310, 610, 755))
        body.thumbnail((390, 410))
        x, y = i % 3 * 400, i // 3 * 470
        board.paste(body, (x, y + 40), body)
        c = row["coverage"]
        draw.text((x + 8, y + 6), f"{pose}: missing {c['missing']}/{c['required_visible']}px ({c['missing_ratio']:.1%})", fill="black")
    draw.text((8, 942), "RAW PLACEMENT / PINK = ZMAP COVERAGE ERRORS / OWNERSHIP GUIDE NEEDS REVIEW / ART NOT APPROVED", fill="black")
    board.save(out / "revalidation-foot-errors.jpg", quality=92)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reuse-current", action="store_true", help="reuse verified current reports")
    parser.add_argument("--queue", action="store_true", help="save concrete requests without starting image generation")
    args = parser.parse_args()
    run(args.out.absolute(), reuse_current=args.reuse_current, queue=args.queue)
