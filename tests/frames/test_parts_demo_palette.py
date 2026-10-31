# SPDX-License-Identifier: Apache-2.0
"""Regression guards for the source-specific demo authoring helpers."""
import numpy as np
import pytest

from scripts.dev.parts_motion_skin_palette import palette_pixels
from scripts.dev.parts_full_motion import foot_matrix
from scripts.dev.parts_motion_preview import preview
from scripts.dev.parts_full_motion_ankle_bridge import cuff_cap


def test_skin_correction_keeps_alpha_head_and_garment_neighbourhood():
    source=np.zeros((410,80,4),dtype=np.uint8)
    source[360:401,18:31]=[210,210,208,255]
    source[100,20]=[248,150,165,255]  # Head is outside the declared edit region.
    source[400,2]=[248,225,150,173]   # Yellow flash, with antialias alpha.
    source[400,10]=[248,150,165,255]  # Pink flash.
    source[400,20]=[210,210,208,255]  # Neutral garment.
    source[400,33]=[230,180,130,255] # Garment's protected warm edge.
    source[400,50]=[185,80,90,255]   # Unstable red contour.
    source[400,39]=[75,43,25,255]    # Stable dark contour.
    corrected,region=palette_pixels(source)
    assert np.array_equal(corrected[...,3],source[...,3])
    for y,x in [(100,20),(400,20),(400,33),(400,39)]:
        assert np.array_equal(corrected[y,x],source[y,x])
    for x in (2,10):
        red,green,blue=corrected[400,x,:3].astype(int)
        assert 42<=red-green<=55 and 38<=green-blue<=50
    assert corrected[400,50,1]>corrected[400,50,2]  # Brown, not pink.


def test_product_length_does_not_follow_flexing_bare_toe_length():
    points={'ankle':[120,90],'heel':[100,110],'toe':[150,110],'footwear_length':80}
    first=foot_matrix(points,length=100)
    second=foot_matrix({**points,'toe':[130,140]},length=120)
    for matrix in (first,second):
        scales=np.linalg.svd(matrix[:2,:2],compute_uv=False)
        assert np.allclose(scales,[80/1310,80/1310])
        assert np.allclose(matrix[:2,:2]@np.array([410,438])+matrix[:2,2],points['ankle'])


def test_preview_refuses_a_directory_still_being_rendered(tmp_path):
    folder=tmp_path/'pending';folder.mkdir();(folder/'frames').mkdir()
    with pytest.raises(ValueError,match='complete rendered evidence'):
        preview(folder,folder,tmp_path/'preview')
    assert not (tmp_path/'preview').exists()


def test_cuff_hem_rounds_both_sides_and_has_fractional_edge_alpha():
    across=np.array([-20.,-10.,0.,10.,20.])
    profile,alpha=cuff_cap(np.full(5,27.8),across,30,20,4)
    assert np.allclose(profile,[26,29,30,29,26])
    assert np.allclose(alpha,[0,1,1,1,0])
    _,edge=cuff_cap(np.array([29.8]),np.array([0.]),30,20,4)
    assert np.allclose(edge,[.7])
    shifted,_=cuff_cap(np.zeros(5),across-12,30,20,4,center=-12)
    assert np.allclose(shifted,profile)  # An ankle marker need not be the calf center.
