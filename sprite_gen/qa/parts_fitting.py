# SPDX-License-Identifier: Apache-2.0
"""Authored layer-fit checks and bounded, part-only repair attempts.

These checks measure an explicitly calibrated interface, not anatomy or art
quality. Corrections happen during authoring/import. Playback still uses an
immutable picture and its relative offset; this module never changes a body.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.spec import parts_assets as assets, parts_contract as contract

FIT_POLICY = "parts.authored-layer-fit.v1"
MAX_RETRIES = 3


@dataclass(frozen=True)
class FitRules:
    canvas: tuple[int, int]
    max_size: tuple[int, int]
    bounds: tuple[int, int, int, int]
    gap_region: tuple[int, int, int, int] | None
    required_cover: tuple[tuple[int, int, int, int], ...] = ()
    keep_clear: tuple[tuple[int, int, int, int], ...] = ()
    max_gap_pixels: int = 0
    max_uncovered_pixels: int = 0
    max_occluded_pixels: int = 0

    def __post_init__(self):
        for point in (self.canvas, self.max_size):
            if len(point) != 2 or any(type(n) is not int or n <= 0 for n in point):
                raise contract.PartsContractError("fit canvas and size must be positive integer pairs")
        if self.canvas[0]*self.canvas[1] > assets.MAX_FRAME_PIXELS:
            raise contract.PartsContractError("fit canvas exceeds the pixel budget")
        rectangles = (self.bounds, *self.required_cover, *self.keep_clear)
        if self.gap_region is not None:
            rectangles += (self.gap_region,)
        for rect in rectangles:
            if (len(rect) != 4 or any(type(n) is not int for n in rect)
                or not 0 <= rect[0] < rect[2] <= self.canvas[0]
                or not 0 <= rect[1] < rect[3] <= self.canvas[1]):
                raise contract.PartsContractError("fit region must be a nonempty rectangle inside the canvas")
        for limit in (self.max_gap_pixels, self.max_uncovered_pixels, self.max_occluded_pixels):
            if type(limit) is not int or limit < 0:
                raise contract.PartsContractError("fit pixel tolerances must be nonnegative integers")


@dataclass(frozen=True)
class FitCandidate:
    part_id: str
    image: bytes
    offset: tuple[int, int] = (0, 0)
    action: str = "initial"

    def __post_init__(self):
        contract._id(self.part_id, "fit.part_id")
        if not isinstance(self.image, bytes):
            raise contract.PartsContractError("fit candidate must contain immutable image bytes")
        if len(self.offset) != 2 or any(type(n) is not int for n in self.offset):
            raise contract.PartsContractError("fit offset must be an integer pair")

    @property
    def fingerprint(self):
        # Changing a label alone is not progress. Moving the same picture is.
        image = assets.image_bytes(self.image)
        return assets.sha256(str(image.size).encode()+b"\0"+image.tobytes()), self.offset


def _region_mask(rectangles, canvas, delta):
    mask = np.zeros((canvas[1], canvas[0]), dtype=bool)
    for x0, y0, x1, y1 in rectangles:
        x0, x1 = x0+delta[0], x1+delta[0]
        y0, y1 = y0+delta[1], y1+delta[1]
        if not 0 <= x0 < x1 <= canvas[0] or not 0 <= y0 < y1 <= canvas[1]:
            raise contract.PartsContractError("translated fit region leaves the canvas")
        mask[y0:y1, x0:x1] = True
    return mask


def _enclosed_mask(occupied):
    """Find transparent holes; an ear notch open to outside is not a hole."""
    h, w = occupied.shape
    outside = np.zeros_like(occupied)
    pending = deque()
    for y in range(h):
        for x in (0, w-1):
            if not occupied[y, x] and not outside[y, x]:
                outside[y, x] = True
                pending.append((y, x))
    for x in range(w):
        for y in (0, h-1):
            if not occupied[y, x] and not outside[y, x]:
                outside[y, x] = True
                pending.append((y, x))
    while pending:
        y, x = pending.popleft()
        for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
            if 0 <= ny < h and 0 <= nx < w and not occupied[ny, nx] and not outside[ny, nx]:
                outside[ny, nx] = True
                pending.append((ny, nx))
    return ~occupied & ~outside


def _pixel_region(image, canvas):
    if not isinstance(image, Image.Image) or image.size != canvas or image.mode not in ("L", "RGBA"):
        raise contract.PartsContractError("fit pixel regions must be native-canvas L or RGBA images")
    return np.asarray(image if image.mode == "L" else image.getchannel("A")) >= 128


def measure(body, part, rules, *, offset=(0, 0), reference_delta=(0, 0),
            required_mask=None, protected_mask=None, allowed_mask=None):
    """Inspect native images against authored rectangles or exact pose masks.

    Pixel masks are already in the current frame's canvas coordinates; they
    are not inferred from anatomy or moved by reference_delta. This supports
    clothing/leg coverage and arm protection as well as hair/face fitting.
    """
    if body.size != rules.canvas or part.size != rules.canvas:
        raise contract.PartsContractError("fit images must match the authored canvas")
    for point in (offset, reference_delta):
        if len(point) != 2 or any(type(n) is not int for n in point):
            raise contract.PartsContractError("fit positions must be integer pairs")
    # Force annotation bounds to validate even when the current picture is empty.
    gap_mask = _region_mask((rules.gap_region,), rules.canvas, reference_delta) if rules.gap_region else None
    required = _region_mask(rules.required_cover, rules.canvas, reference_delta)
    protected = _region_mask(rules.keep_clear, rules.canvas, reference_delta)
    if required_mask is not None:
        required |= _pixel_region(required_mask, rules.canvas)
    if protected_mask is not None:
        protected |= _pixel_region(protected_mask, rules.canvas)
    allowed = _pixel_region(allowed_mask, rules.canvas) if allowed_mask is not None else None
    raw_alpha = np.asarray(part.convert("RGBA"))[..., 3] >= 128
    placed = Image.new("RGBA", rules.canvas)
    placed.alpha_composite(part.convert("RGBA"), offset)
    part_alpha = np.asarray(placed)[..., 3] >= 128
    body_alpha = np.asarray(body.convert("RGBA"))[..., 3] >= 128
    bbox = part.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox()
    checks = []

    def check(name, passed, value, limit, action):
        checks.append({"id": name, "status": "passed" if passed else "error",
                       "value": value, "limit": limit, "repair_action": action})

    check("nonempty", bbox is not None, int(raw_alpha.sum()), ">0", "regenerate-part")
    if bbox is not None:
        size = [bbox[2]-bbox[0], bbox[3]-bbox[1]]
        bounds = [bbox[i]+offset[i % 2] for i in range(4)]
        target = [rules.bounds[i]+reference_delta[i % 2] for i in range(4)]
        check("size", all(a <= b for a, b in zip(size, rules.max_size)), size,
              list(rules.max_size), "calibrate-import-size")
        check("position", bounds[0] >= target[0] and bounds[1] >= target[1]
              and bounds[2] <= target[2] and bounds[3] <= target[3], bounds, target,
              "adjust-binding-offset")
    clipped = int(raw_alpha.sum()-part_alpha.sum())
    check("clipping", clipped == 0, clipped, 0, "adjust-binding-offset")
    uncovered = int((required & ~part_alpha).sum())
    check("coverage", uncovered <= rules.max_uncovered_pixels, uncovered,
          rules.max_uncovered_pixels, "adjust-offset-or-regenerate-part")
    occluded = int((protected & part_alpha).sum())
    check("occlusion", occluded <= rules.max_occluded_pixels, occluded,
          rules.max_occluded_pixels, "adjust-offset-or-regenerate-part")
    if allowed is not None:
        outside = int((part_alpha & ~allowed).sum())
        check("silhouette", outside == 0, outside, 0, "adjust-offset-or-regenerate-part")
    existing_pixels = combined_pixels = None
    if gap_mask is not None:
        ys, xs = np.where(gap_mask)
        region = np.s_[ys.min():ys.max()+1, xs.min():xs.max()+1]
        existing = _enclosed_mask(body_alpha[region])
        combined = _enclosed_mask((body_alpha | part_alpha)[region])
        # Subtract coordinates: a preexisting body flaw is not a redraw request.
        gaps = int((combined & ~existing).sum())
        check("gap", gaps <= rules.max_gap_pixels, gaps, rules.max_gap_pixels, "regenerate-part")
        existing_pixels, combined_pixels = int(existing.sum()), int(combined.sum())
    return {"fit_policy": FIT_POLICY, "status": "error" if any(c["status"] == "error" for c in checks) else "passed",
            "checks": checks, "existing_body_gap_pixels": existing_pixels,
            "combined_gap_pixels": combined_pixels, "art_approved": False, "release_ready": False}


def retry(candidate: FitCandidate, evaluate: Callable, correct: Callable, *, max_retries=MAX_RETRIES):
    """Recheck each revision; stop on exhaustion, repeated output or no correction.

    `correct(candidate, failed_report, attempt)` is an authoring adapter. It may
    calibrate an import, change that part's binding, or return a regenerated
    image. Provider/network failures are deliberately not swallowed as fitting
    errors. A passing report selects a candidate; it never activates or approves it.
    """
    if type(max_retries) is not int or not 0 <= max_retries <= MAX_RETRIES:
        raise contract.PartsContractError(f"fit retries must be between 0 and {MAX_RETRIES}")
    original_id, seen, history = candidate.part_id, set(), []
    accepted = None
    for attempt in range(max_retries+1):
        seen.add(candidate.fingerprint)
        report = evaluate(candidate)
        if report.get("status") not in ("passed", "error"):
            raise contract.PartsContractError("fit evaluator must report passed or error")
        if (not isinstance(report.get("checks"), list) or not report["checks"]
            or any(c.get("status") not in ("passed", "error") for c in report["checks"])):
            raise contract.PartsContractError("fit evaluator must provide measured checks")
        failed = [c for c in report["checks"] if c["status"] == "error"]
        if (report["status"] == "passed") != (not failed):
            raise contract.PartsContractError("fit status contradicts measured checks")
        history.append({"attempt": attempt, "part_id": candidate.part_id,
                        "image_sha256": assets.sha256(candidate.image),
                        "rgba_sha256": candidate.fingerprint[0], "offset": list(candidate.offset),
                        "action": candidate.action, "report": report})
        if not failed:
            accepted, stopped = candidate, "measured-fit-passed"
            break
        if attempt == max_retries:
            stopped = "retries-exhausted"
            break
        next_candidate = correct(candidate, report, attempt+1)
        if next_candidate is None:
            stopped = "correction-unavailable"
            break
        if not isinstance(next_candidate, FitCandidate) or next_candidate.part_id != original_id:
            raise contract.PartsContractError("fit repair may change only the failed part")
        if next_candidate.fingerprint in seen:
            stopped = "no-progress"
            break
        candidate = next_candidate
    result = {"kind": "parts-fit-repair", "schema_version": 1, "fit_policy": FIT_POLICY,
              "part_id": original_id, "status": "passed" if accepted is not None else "error",
              "max_retries": max_retries, "retries_used": len(history)-1,
              "stopped": stopped, "attempts": history, "art_approved": False, "release_ready": False}
    if accepted is None:
        result["rework"] = {"part_id": original_id, "failed_checks": failed,
                            "body_and_motion": "preserve", "activation": "blocked"}
    return accepted, result
