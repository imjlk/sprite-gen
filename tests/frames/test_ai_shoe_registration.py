# SPDX-License-Identifier: Apache-2.0
"""The native registration kernel must not create alpha rings or black fringes."""
from PIL import Image, ImageDraw
import numpy as np
from pathlib import Path
import importlib.util
from sprite_gen.frames.parts_production import _pinholes, _islands

_spec = importlib.util.spec_from_file_location(
    'ai_shoe_registration', Path(__file__).resolve().parents[2] / 'scripts/dev/parts_ai_shoes_pilot.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
resize_registered = _module.resize_registered


def test_positive_registration_preserves_closed_silhouette_and_transparent_rgb():
    # A smooth high-resolution cutout with unrelated RGB behind zero alpha.
    source=Image.new('RGBA',(800,600),(255,255,255,0))
    ImageDraw.Draw(source).ellipse((40,60,760,540),fill=(32,60,105,255))
    policy={'resampling':'bilinear'}
    result=resize_registered(source,.13,policy)
    a=np.asarray(result);mask=a[...,3]>0
    assert result.size==(104,78)
    assert _pinholes(mask)==0 and _islands(mask,4)==0
    assert np.all(a[a[...,3]==255,:3]==[32,60,105])
    # Edge quantization is bounded in premultiplied space; unrelated white RGB
    # under transparent source pixels must never contaminate the silhouette.
    expected=np.array([32,60,105])*a[...,3,None]/255
    actual=a[...,:3].astype(float)*a[...,3,None]/255
    assert np.max(np.abs(actual-expected))<2
    assert np.all(a[~mask,:3]==0)


def test_low_alpha_anti_alias_is_smooth_without_an_opaque_color_halo():
    source=Image.new('RGBA',(400,400))
    draw=ImageDraw.Draw(source)
    for inset,alpha in [(10,1),(20,16),(30,128),(40,255)]:
        draw.rectangle((inset,inset,399-inset,399-inset),fill=(100,70,40,alpha))
    result=resize_registered(source,.1,{'resampling':'bilinear'})
    a=np.asarray(result);row=a[20,:,3]
    assert (np.diff(row[:20].astype(int))>=0).all()
    assert (np.diff(row[20:].astype(int))<=0).all()
    assert _pinholes(a[...,3]>0)==0
