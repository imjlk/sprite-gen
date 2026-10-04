# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy

import pytest
from PIL import Image

from parts_helpers import composition_fixture
from sprite_gen.qa import inspect_parts
from sprite_gen.spec import parts_assets as assets, parts_contract as contract


def annotations(docs):
    motion = docs[2]
    return {"kind": "parts-contact-annotations", "schema_version": 1, "motion": contract.reference(motion),
        "clips": {"walk.right": {"world_offsets": {"sample.0": [0, 0], "sample.1": [-1, 0], "sample.2": [0, 0]},
            "stances": [{"node": "foot_l", "socket": "foot_l", "samples": ["sample.0", "sample.1", "sample.2"], "max_slip_px": 0}]}}}


def test_declared_world_travel_cancels_in_place_contact_motion_without_silhouette_guess():
    docs, _ = composition_fixture()
    checks = inspect_parts._contact_checks(annotations(docs), docs[2], docs[0], False, 128)
    assert checks[0]["status"] == "passed" and checks[0]["max_drift_px"] == 0
    assert checks[-1]["status"] == "unverified"  # Other feet/clips were not annotated.
    data = annotations(docs)
    data["clips"]["walk.right"]["world_offsets"]["sample.1"] = [0, 0]
    assert inspect_parts._contact_checks(data, docs[2], docs[0], False, 128)[0]["status"] == "error"


@pytest.mark.parametrize("change", ["node", "socket", "motion", "order", "travel", "tolerance"])
def test_contact_annotation_errors_do_not_become_passed(change):
    docs, _ = composition_fixture()
    data = annotations(docs)
    stance = data["clips"]["walk.right"]["stances"][0]
    if change == "node": stance["node"] = "head"
    elif change == "socket": stance["socket"] = "head"
    elif change == "motion": data["motion"]["digest"] = "a" * 64
    elif change == "order": stance["samples"].reverse()
    elif change == "travel": data["clips"]["walk.right"]["world_offsets"].pop("sample.1")
    else: stance["max_slip_px"] = -1
    with pytest.raises(contract.PartsContractError):
        inspect_parts._contact_checks(data, docs[2], docs[0], False, 128)


def test_blob_does_not_acquire_humanoid_contact_requirements():
    docs, _ = composition_fixture("blob.fixture.v1", states=("pulse",))
    assert inspect_parts._contact_checks(None, docs[2], docs[0], False, 96)[0]["status"] == "passed"


def world_preview_input():
    image = Image.new('RGBA', (4, 4))
    image.putpixel((2, 1), (50, 120, 200, 255))
    payloads = {'source.png': assets.png_bytes(image)}
    samples = [{'id': f'sample.{i}', 'duration_ticks': 3+i, 'image': {'file': 'source.png'}} for i in range(3)]
    clips = [{'id': 'walk.right', 'state': 'walk', 'timebase': 24, 'loop': True, 'samples': samples}]
    contacts = {'clips': {'walk.right': {'world_offsets': {'sample.0': [0, 0], 'sample.1': [3, -2],
                'sample.2': [-1, 1]}}}}
    return {'canvas': {'width': 4, 'height': 4}}, clips, payloads, contacts


def test_world_preview_moves_the_whole_sprite_and_preserves_pixels_timing_and_sources():
    plan, clips, payloads, contacts = world_preview_input()
    source = payloads['source.png']
    report = inspect_parts._world_travel_previews(plan, clips, payloads, contacts, [])[0]
    assert report['world_origin'] == [-1, -2] and report['canvas'] == {'width': 8, 'height': 7}
    assert report['coverage'] == 'complete' and report['timebase'] == 24 and report['loop'] is True
    assert [row['sprite_position'] for row in report['samples']] == [[1, 2], [4, 0], [0, 3]]
    for index, row in enumerate(report['samples']):
        image = assets.image_bytes(payloads[row['image']['file']])
        x, y = row['sprite_position']
        assert image.crop((x, y, x+4, y+4)).tobytes() == assets.image_bytes(source).tobytes()
        assert row['duration_ticks'] == 3+index
        assert row['image']['sha256'] == assets.sha256(payloads[row['image']['file']])
    assert payloads['source.png'] == source
    assert report['contact_truth'].startswith('unverified')


def test_world_preview_keeps_partial_annotations_partial_and_does_not_mirror_world_travel():
    plan, clips, payloads, contacts = world_preview_input()
    plan['mirror'] = True  # The shared renderer already reflected the sprite pixels.
    contacts['clips']['walk.right']['world_offsets'] = {'sample.1': [3, -2]}
    report = inspect_parts._world_travel_previews(plan, clips, payloads, contacts, [])[0]
    assert report['coverage'] == 'partial' and report['world_origin'] == [3, -2]
    assert len(report['samples']) == 1 and report['samples'][0]['id'] == 'sample.1'
    assert report['samples'][0]['world_offset'] == [3, -2]
    assert inspect_parts._world_travel_previews(plan, clips, payloads, None, []) == []


@pytest.mark.parametrize('case', ['extreme-travel', 'many-world-frames'])
def test_world_preview_rejects_extreme_travel_before_allocating_an_image(case):
    plan, clips, payloads, contacts = world_preview_input()
    if case == 'extreme-travel':
        contacts['clips']['walk.right']['world_offsets']['sample.1'] = [10**12, 0]
    else:
        clips[0]['samples'] = [{'id': f'sample.{i}', 'duration_ticks': 1,
                              'image': {'file': 'source.png'}} for i in range(100)]
        contacts['clips']['walk.right']['world_offsets'] = {
            f'sample.{i}': [1000, 1000] if i == 99 else [0, 0] for i in range(100)}
    with pytest.raises(contract.PartsContractError, match='pixel budget'):
        inspect_parts._world_travel_previews(plan, clips, payloads, contacts, [])


def test_world_preview_sample_ids_cannot_overwrite_the_contact_sheet():
    plan, clips, payloads, contacts = world_preview_input()
    clips[0]['samples'][0]['id'] = 'contact-sheet'
    offsets = contacts['clips']['walk.right']['world_offsets']
    offsets['contact-sheet'] = offsets.pop('sample.0')
    report = inspect_parts._world_travel_previews(plan, clips, payloads, contacts, [])[0]
    assert report['samples'][0]['image']['file'] != report['contact_sheet']['file']
    assert assets.sha256(payloads[report['samples'][0]['image']['file']]) == report['samples'][0]['image']['sha256']
