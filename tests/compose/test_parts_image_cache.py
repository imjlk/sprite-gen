# SPDX-License-Identifier: Apache-2.0
"""Long motion masks retain exact pixels while decoded memory stays bounded."""
from PIL import Image
import pytest

from sprite_gen.compose.compose_parts import AssetLoader
from sprite_gen.spec import parts_assets as a, parts_contract as c


def test_evicted_motion_images_redecode_from_verified_snapshot(tmp_path,monkeypatch):
    monkeypatch.setattr(a,'MAX_TOTAL_PIXELS',8)
    refs=[]
    for i,color in enumerate(['red','green','blue']):
        image=Image.new('RGBA',(2,2),color)
        data=a.png_bytes(image);name=f'{i}.png';(tmp_path/name).write_bytes(data)
        refs.append({'file':name,'sha256':a.sha256(data)})
    loader=AssetLoader(tmp_path,{'documents':{'shoe':'shoe.json'}})
    pack={'id':'shoe'}
    first=loader(pack,refs[0])
    loader(pack,refs[1]);loader(pack,refs[2])
    assert loader.total_pixels==8 and len(loader.images)==2
    # Eviction must not read a changed file into a previously frozen sequence.
    (tmp_path/'0.png').write_bytes(a.png_bytes(Image.new('RGBA',(2,2),'white')))
    reloaded=loader(pack,refs[0])
    assert reloaded.tobytes()==first.tobytes()
    reloaded.putpixel((0,0),(0,0,0,0))
    assert loader(pack,refs[0]).tobytes()==first.tobytes()
    assert loader.total_pixels<=8


def test_one_image_exceeding_decoded_cache_budget_still_fails(tmp_path,monkeypatch):
    monkeypatch.setattr(a,'MAX_TOTAL_PIXELS',8)
    data=a.png_bytes(Image.new('RGBA',(3,3),'red'));(tmp_path/'shoe.png').write_bytes(data)
    loader=AssetLoader(tmp_path,{'documents':{'shoe':'shoe.json'}})
    with pytest.raises(c.PartsContractError,match='pixel budget'):
        loader({'id':'shoe'},{'file':'shoe.png','sha256':a.sha256(data)})
    assert loader.total_pixels==0 and not loader.images
