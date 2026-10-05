# SPDX-License-Identifier: Apache-2.0
"""Pixel grid helpers: clean clusters versus speckle, and layer frames that stand apart."""
from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.frames import pixel_grid


def blob(size=12, noisy=False):
    image = Image.new("RGBA", (20, 20))
    ImageDraw.Draw(image).rectangle((4, 4, 4+size, 4+size), fill=(30, 140, 140, 255))
    if noisy:
        pixels = np.asarray(image).copy()
        pixels[4:4+size:2, 4:4+size:2, :3] = (240, 230, 200)
        image = Image.fromarray(pixels)
    return image


def test_speckle_share_separates_clusters_from_texture_noise():
    assert pixel_grid.speckle_share(blob()) == 0.0
    assert pixel_grid.speckle_share(blob(noisy=True)) > 0.15


def test_outlier_frames_flag_speckled_and_jumping_frames():
    frames = [blob(), blob(), blob(noisy=True), blob(), blob(size=4), blob()]
    flagged = {row["index"]: row["reasons"] for row in pixel_grid.outlier_frames(frames)}
    assert flagged[2] == ["speckle"] and "area" in flagged[4] and 0 not in flagged


def test_fill_small_holes_closes_skin_showing_through_but_keeps_open_gaps():
    image = blob(size=10)
    pixels = np.asarray(image).copy()
    pixels[8:10, 8:10] = 0          # a 4 px hole inside the garment
    pixels[4:15, 14] = 0            # a slit open to the outside
    filled, count = pixel_grid.fill_small_holes(Image.fromarray(pixels), 6)
    out = np.asarray(filled)
    assert count == 1 and out[8, 8, 3] == 255 and tuple(out[8, 8, :3]) == (30, 140, 140) and out[10, 14, 3] == 0


def test_close_over_fills_notches_inside_the_body_only():
    body = Image.new("RGBA", (20, 20))
    ImageDraw.Draw(body).rectangle((2, 2, 17, 17), fill=(240, 190, 130, 255))
    part = blob(size=10)
    pixels = np.asarray(part).copy()
    pixels[9, 14] = 0                 # a notch on the garment's edge
    closed, count = pixel_grid.close_over(Image.fromarray(pixels), body)
    out = np.asarray(closed)
    assert out[9, 14, 3] == 255 and count >= 1 and out[1, 1, 3] == 0
