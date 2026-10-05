# SPDX-License-Identifier: Apache-2.0
from io import BytesIO

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo
import pytest

from sprite_gen.qa import parts_fitting as fitting
from sprite_gen.spec import parts_assets as assets, parts_contract as contract


def fixture():
    body = Image.new("RGBA", (16, 16))
    ImageDraw.Draw(body).rectangle((6, 3, 12, 12), fill=(220, 150, 90, 255))
    cap = Image.new("RGBA", body.size)
    ImageDraw.Draw(cap).rectangle((3, 2, 13, 10), outline=(60, 30, 20, 255))
    rules = fitting.FitRules((16, 16), (12, 12), (0, 0, 16, 16), (1, 1, 15, 15),
                             required_cover=((3, 2, 6, 3),), keep_clear=((9, 5, 12, 8),))
    snug = cap.copy()
    ImageDraw.Draw(snug).rectangle((3, 2, 6, 10), fill=(60, 30, 20, 255))
    return body, cap, snug, rules


def test_enclosed_fit_gap_is_detected_and_a_snug_cap_preserves_the_body():
    body, cap, snug, rules = fixture()
    before = body.tobytes(), cap.tobytes(), snug.tobytes()
    bad = fitting.measure(body, cap, rules)
    good = fitting.measure(body, snug, rules)
    assert bad["status"] == "error"
    assert next(c for c in bad["checks"] if c["id"] == "gap")["value"] > 0
    assert good["status"] == "passed" and good["art_approved"] is False
    assert before == (body.tobytes(), cap.tobytes(), snug.tobytes())


def test_open_ear_notch_is_not_an_enclosed_gap():
    body, cap, _, rules = fixture()
    # Open the cap's rear edge; the empty space connects to the outside.
    ImageDraw.Draw(cap).rectangle((3, 4, 3, 8), fill=(0, 0, 0, 0))
    assert next(c for c in fitting.measure(body, cap, rules)["checks"] if c["id"] == "gap")["value"] == 0


def test_preexisting_body_hole_is_recorded_without_requesting_a_part_redraw():
    body, _, snug, rules = fixture()
    body.putpixel((10, 7), (0, 0, 0, 0))
    report = fitting.measure(body, snug, rules)
    assert report["status"] == "passed"
    assert report["existing_body_gap_pixels"] == report["combined_gap_pixels"] == 1
    assert next(c for c in report["checks"] if c["id"] == "gap")["value"] == 0


def test_gap_is_not_inferred_for_a_part_without_a_declared_seam_region():
    body, cap, _, _ = fixture()
    rules = fitting.FitRules((16, 16), (12, 12), (0, 0, 16, 16), None)
    report = fitting.measure(body, cap, rules)
    assert "gap" not in {c["id"] for c in report["checks"]}
    assert report["combined_gap_pixels"] is None and report["art_approved"] is False


def test_size_and_protected_face_checks_distinguish_a_cap_from_a_filled_rectangle():
    body, _, snug, _ = fixture()
    large = Image.new("RGBA", body.size, (60, 30, 20, 255))
    rules = fitting.FitRules((16, 16), (12, 12), (2, 1, 14, 14), (1, 1, 15, 15),
                             keep_clear=((9, 5, 12, 8),))
    result = fitting.measure(body, large, rules)
    assert {c["id"] for c in result["checks"] if c["status"] == "error"} == {"size", "position", "occlusion"}
    assert fitting.measure(body, snug, rules)["status"] == "passed"


def test_fit_regions_follow_the_authored_attachment_delta_and_off_canvas_is_an_error():
    body, _, snug, rules = fixture()
    shifted = Image.new("RGBA", body.size)
    shifted.alpha_composite(body, (1, 1))
    assert fitting.measure(shifted, snug, rules, offset=(1, 1), reference_delta=(1, 1))["status"] == "passed"
    result = fitting.measure(body, snug, rules, offset=(-5, 0))
    assert next(c for c in result["checks"] if c["id"] == "clipping")["status"] == "error"
    with pytest.raises(contract.PartsContractError, match="leaves the canvas"):
        fitting.measure(body, snug, rules, reference_delta=(10, 0))


def test_pose_masks_detect_missing_trouser_leg_and_arm_occlusion_without_body_edits():
    body = Image.new("RGBA", (16, 16), (220, 150, 90, 255))
    leg = Image.new("L", body.size)
    ImageDraw.Draw(leg).polygon([(4, 6), (7, 6), (5, 12), (2, 12)], fill=255)
    arm = Image.new("L", body.size)
    ImageDraw.Draw(arm).rectangle((9, 5, 11, 9), fill=255)
    pants = Image.new("RGBA", body.size, (60, 50, 30, 0))
    pants.putalpha(leg)
    rules = fitting.FitRules(body.size, body.size, (0, 0, 16, 16), (0, 0, 16, 16))
    original = body.tobytes(), pants.tobytes(), leg.tobytes(), arm.tobytes()
    assert fitting.measure(body, pants, rules, required_mask=leg,
                           protected_mask=arm, allowed_mask=leg)["status"] == "passed"
    wrong_pose = Image.new("RGBA", body.size)
    wrong_pose.alpha_composite(pants, (3, 0))
    wrong_pose.putpixel((10, 7), (60, 50, 30, 255))
    report = fitting.measure(body, wrong_pose, rules, required_mask=leg,
                             protected_mask=arm, allowed_mask=leg)
    assert {c["id"] for c in report["checks"] if c["status"] == "error"} == {"coverage", "occlusion", "silhouette"}
    assert original == (body.tobytes(), pants.tobytes(), leg.tobytes(), arm.tobytes())


@pytest.mark.parametrize("mask", [Image.new("L", (8, 8)), Image.new("RGB", (16, 16))])
def test_pose_masks_reject_implicit_resizing_and_rgb_without_coverage(mask):
    body, _, snug, rules = fixture()
    with pytest.raises(contract.PartsContractError, match="native-canvas"):
        fitting.measure(body, snug, rules, required_mask=mask)


def test_retry_selects_the_corrected_part_only_after_rechecking_it():
    body, cap, snug, rules = fixture()
    candidate = fitting.FitCandidate("hair.a", assets.png_bytes(cap))
    replacement = fitting.FitCandidate("hair.a", assets.png_bytes(snug), action="regenerate-part")
    seen = []

    def correct(previous, failure, attempt):
        seen.append((previous, failure["status"], attempt))
        return replacement

    accepted, report = fitting.retry(candidate, lambda c: fitting.measure(body, assets.image_bytes(c.image), rules), correct)
    assert accepted == replacement and report["retries_used"] == 1
    assert [a["report"]["status"] for a in report["attempts"]] == ["error", "passed"]
    assert len(seen) == 1 and seen[0][2] == 1
    assert report["release_ready"] is False and report["art_approved"] is False


def test_retry_stops_on_same_pixels_even_when_png_metadata_and_action_differ():
    body, cap, _, rules = fixture()
    metadata = PngInfo()
    metadata.add_text("attempt", "changed label")
    output = BytesIO()
    cap.save(output, format="PNG", pnginfo=metadata)
    candidate = fitting.FitCandidate("hair.a", assets.png_bytes(cap))
    calls = []

    def correct(*args):
        calls.append(1)
        return fitting.FitCandidate("hair.a", output.getvalue(), action="retry")

    accepted, report = fitting.retry(candidate, lambda c: fitting.measure(body, assets.image_bytes(c.image), rules), correct)
    assert accepted is None and report["stopped"] == "no-progress" and calls == [1]
    assert report["rework"]["body_and_motion"] == "preserve"


def test_retry_exhaustion_does_not_accept_or_activate_the_last_failed_candidate():
    body, cap, _, rules = fixture()
    candidate = fitting.FitCandidate("hair.a", assets.png_bytes(cap))
    corrected = []

    def correct(previous, failure, attempt):
        corrected.append(attempt)
        return fitting.FitCandidate(previous.part_id, previous.image, offset=(attempt, 0), action="offset")

    accepted, report = fitting.retry(candidate, lambda c: fitting.measure(body, assets.image_bytes(c.image), rules, offset=c.offset), correct)
    assert accepted is None and report["stopped"] == "retries-exhausted"
    assert corrected == [1, 2, 3] and report["retries_used"] == 3
    assert report["rework"]["activation"] == "blocked"


def test_retry_cannot_change_another_part_or_hide_an_evaluator_error():
    body, cap, _, rules = fixture()
    candidate = fitting.FitCandidate("hair.a", assets.png_bytes(cap))
    with pytest.raises(contract.PartsContractError, match="only the failed part"):
        fitting.retry(candidate, lambda c: fitting.measure(body, assets.image_bytes(c.image), rules),
                      lambda *args: fitting.FitCandidate("body", candidate.image))
    with pytest.raises(RuntimeError, match="provider unavailable"):
        fitting.retry(candidate, lambda c: fitting.measure(body, assets.image_bytes(c.image), rules),
                      lambda *args: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
    with pytest.raises(contract.PartsContractError, match="measured checks"):
        fitting.retry(candidate, lambda c: {"status": "passed", "checks": []}, lambda *args: None)


@pytest.mark.parametrize("limit", [-1, 4, True])
def test_retry_budget_is_bounded(limit):
    _, cap, _, _ = fixture()
    with pytest.raises(contract.PartsContractError, match="retries"):
        fitting.retry(fitting.FitCandidate("hair.a", assets.png_bytes(cap)), None, None, max_retries=limit)
