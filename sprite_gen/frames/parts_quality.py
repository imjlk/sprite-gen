# SPDX-License-Identifier: Apache-2.0
"""Explain check scope and compare only explicitly declared stable source regions."""
from PIL import Image
from sprite_gen.qa.parts_stability import measure


def criteria(metrics, *, mode="raster"):
    specs = [("required_pixels", "필수 덮임 누락", 0, "error"),
             ("protected_pixels", "보호 영역 침범", 0, "error"),
             ("allowed_pixels", "허용 영역 밖", 0, "error"),
             ("off_palette", "지정 팔레트 밖", 0, "warning"),
             ("holes", "작은 내부 구멍", 0, "warning"),
             ("specks", "4px 미만 잔조각", 0, "warning"),
             ("area_jump", "인접 포즈 면적 변화", .45, "warning")]
    if "body_reference_mismatches" in metrics:
        specs.insert(0,("body_reference_mismatches", "바디 레이어 소유 불일치", 0, "error"))
    rows = [{"id": key, "label": label, "value": metrics.get(key), "limit": limit,
             "status": ("미계산" if key == "area_jump" else "미설정") if key not in metrics else level if metrics[key] > limit else "범위 내",
             "basis": "authored body ownership" if key == "body_reference_mismatches" else "authored interface" if key.endswith("_pixels") else "inspection heuristic"}
            for key, label, limit, level in specs]
    if mode == "held":
        for row in rows:
            if row["id"] == "specks":
                row.update(limit=None, status="참고", basis="held details may be intentional")
    if not metrics.get("stable_regions"):
        rows.append({"id": "stable_regions", "label": "고정 영역 프레임 일관성", "value": None,
                     "limit": None, "status": "미설정", "basis": "authored stable region"})
    for region in metrics.get("stable_regions", []):
        for key, maximum in (("alpha_pixels", "max_alpha_pixels"), ("rgb_pixels", "max_rgb_pixels")):
            rows.append({"id": region["id"]+"."+key, "label": region["id"]+" · "+key,
                         "value": region.get(key), "limit": region[maximum],
                         "status": region["status"], "basis": "authored stable region"})
    for identity, edge in metrics.get('straight_edges', {}).items():
        rows.append({'id': identity, 'label': identity + ' · 직선 윤곽 편차',
                     'value': round(edge['max_deviation'], 3) if edge['max_deviation'] is not None else None,
                     'limit': edge['limit'], 'status': '범위 내' if edge['status'] == 'pass' else 'error',
                     'basis': 'explicitly authored rigid edge; not general art approval'})
    return rows


def temporal(root, rows, pose, regions, first):
    result = []
    for rule in regions:
        reference = rule["reference_pose"] or next(iter(first))
        row = {**rule, "reference_pose": reference, "status": "미검사"}
        if reference not in first or not rows.get(reference, {}).get("selected"):
            result.append(row); continue
        def crop(which):
            sample = first[which][1]
            socket = sample["sockets"].get(rule["socket"])
            if not socket or not socket["visible"] or socket["confidence"] != "verified":
                return None
            x, y = socket["position"]
            a, b, c, d = rule["bounds"]
            return Image.open(root / rows[which]["selected"] / "part.png").convert("RGBA").crop((x+a,y+b,x+c,y+d))
        before, after = crop(reference), crop(pose)
        if before is not None and after is not None:
            delta = measure(before, after, rgb_threshold=rule["rgb_threshold"])
            row.update(delta, status="warning" if (delta["alpha_pixels"] > rule["max_alpha_pixels"] or
                       delta["rgb_pixels"] > rule["max_rgb_pixels"] or not delta["reference_pixels"] or
                       not delta["current_pixels"]) else "범위 내")
        result.append(row)
    return result
