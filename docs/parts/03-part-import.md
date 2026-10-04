# 공통 좌표를 보존하는 파츠 등록

> Owns: Implemented PR03 part/body image import, source snapshots and active registration · Index: [docs/README.md](../README.md)

`parts-import`는 이미 제작한 파츠를 frozen motion의 sample에 등록한다. 작은 이미지를 확대하거나 가운데·바닥에 정렬하지 않는다. Source hash, crop offset, pivot, mask와 palette region을 보존하고, 기본 body는 커스터마이징 슬롯과 별도로 선택한다. 등록 성공은 모션/피팅/조합의 시각적 승인과 별개이며 `release_ready`는 false다.

## 입력과 실행

[모션 freeze](02-motion-snapshots.md)를 먼저 완료한다. Spec은 `parts-pack-import`, schema version 1이며 active rig/motion의 정확한 reference, rig의 style/view, binding ID와 pose-indexed pieces를 선언한다.

```json
{
  "kind": "parts-pack-import",
  "schema_version": 1,
  "id": "explorer.jacket-a.sd",
  "design_id": "explorer.jacket-a",
  "role": "part",
  "style": "pixel.v1",
  "rig": {"id": "human.sd.v1", "digest": "<exact rig digest>"},
  "motion": {"id": "explorer.motion.v1", "digest": "<exact motion digest>"},
  "view": "right",
  "binding_id": "jacket-a.right",
  "occupies": ["upper"],
  "pieces": [{
    "id": "torso",
    "slot": "upper",
    "node": "torso",
    "mode": "raster",
    "sample_map": {"walk.right": {"sample.0": {
      "image": {"file": "jacket/torso-0.png", "sha256": "<source bytes SHA-256>"},
      "format": "cropped",
      "crop_offset": [44,48],
      "pivot": [58,52],
      "target_socket": "shoulder_l"
    }}}
  }]
}
```

angle-bracket 값은 실제 값으로 바꿔야 한다. 예시의 한 sample만으로는 완성 coverage 검증을 통과하지 않는다. 실제 spec에는 요청한 모든 state/sample과 torso·좌우 sleeve 등 해당 디자인의 모든 piece mapping을 넣는다.

```bash
.venv/bin/sprite-gen parts-import \
  --assembly assets/my-parts/assembly.json \
  --spec jacket-import.json --source-root authored-parts/
```

source image/mask 경로는 `--source-root` 기준이다. 모든 참조는 원본 파일 바이트 SHA-256을 요구한다. 공통 bounded reader와 기존 RGBA decoder를 사용하며, 경로 traversal·symlink·image bomb·크기 제한을 그대로 적용한다. 입력을 snapshot으로 복사해 원본을 수정하지 않는다.

## 좌표

| format | 입력 image와 pivot | normalized 결과 |
|---|---|---|
| `full-canvas` | 이미지가 rig canvas와 동일 크기; pivot은 그 canvas 좌표 | 원래 크기와 pivot, crop offset 0 |
| `cropped` | 이미 잘라낸 이미지; `crop_offset`은 rig-canvas에서 crop의 좌상단; pivot은 원래 rig-canvas 좌표 | 원래 작은 이미지, `local_pivot = pivot - crop_offset` |
| `local` | 독립 작은 이미지; pivot이 그 이미지의 local 좌표 | 원래 크기와 local pivot, crop offset 0 |

모든 좌표는 정수다. 정상 배치 위치는 `sample_socket - local_pivot + offset`이다. 예를 들어 rig pivot `[64,64]`, crop offset `[60,61]`이면 local pivot은 `[4,3]`이 되어 작은 그림을 정확히 `[60,61]`로 복원한다. 각 normalized placement의 `source`에는 원본 image hash/format/crop offset/pivot을 기록한다. PNG와 source 바이트의 content hashes를 part digest에 포함한다.

visible placement는 해당 최종 sample의 verified socket이 있어야 한다. Alpha 픽셀이 canvas를 벗어나면 기존 clipping 측정으로 거절한다. 의도한 clip만 `allow_clip: true`로 명시할 수 있고 후속 QA/review에 그 사실을 전달한다. 작은 신발·귀·장식의 크기를 full body처럼 키우는 옵션은 없다.

## Piece, mask와 palette region

한 slot 선택은 여러 piece를 포함할 수 있다. `sample_map`은 clip ID → sample ID → placement다. `held`도 모든 target sample의 mapping을 명시해야 하며, 같은 normalized 이미지 하나만 재사용할 수 있다. 이미지가 같아도 pivot/socket/visibility/mask/ordering은 sample별로 달라질 수 있다.

mask의 `target_node`는 명시적인 대상 node다. `part-local` mask는 해당 part 이미지 크기, `rig-canvas` mask는 rig canvas 크기여야 한다. `channel`은 `luma` 또는 `alpha`이며 기본값은 luma다. Alpha-hole와 반투명 가장자리를 유지하고 등록 단계에서 합성이나 자동 body segmentation을 수행하지 않는다.

palette region은 grayscale coverage image를 참조한다. Piece-level region은 모든 mapping의 이미지와 크기가 같아야 한다. 포즈별 mask/크기가 달라지면 placement의 `palette_regions`를 사용한다. 실제 palette 적용과 before/after draw-order 해석은 PR04의 공통 renderer가 담당한다.

## 기본 body와 교체

Body spec은 `role: body`, `occupies: []`이며 각 piece의 `slot`은 null이다. `provides`로 해당 그림의 node interface를 선언하고 body piece는 자기 primary node를 포함해야 한다. Body를 가짜 hair/upper/footwear 옵션으로 등록하지 않는다.

`body_mode: segmented`는 실제로 분리한 node 그림과 그 sample mapping을 등록하는 경로다. `body_mode: flat`은 body piece 하나로 제한하고 root interface만 제공할 수 있다. 평면 body에 foot/ear/hand가 그려져 있다는 사실만으로 분리된 interface가 있다고 선언할 수 없다. 숨겨진 신체 그림을 복원하거나 기존 귀·꼬리·발 그림을 제거할 자료가 없으면 overlay-only 제약이 남는다. Node 교체와 중복 제공/가림의 최종 판정은 PR04 renderer에서 검사한다.

Body를 등록하면 assembly의 명시적 `body` reference 목록에 추가한다. Appearance도 정확한 body references를 가진다. Rig의 `default_body_ids`는 논리 이름이며 mutable library의 최신 body를 자동으로 찾아 선택하지 않는다.

같은 `design_id`의 SD/tall/canine fitting도 실제 각 rig/motion/view를 가리키는 별도 snapshot으로 제작할 수 있다. 서로 다른 canvas/style/view 또는 수정 전 motion reference는 거절한다. Source를 자동 resize/retarget해 다른 체형에 호환 표시하지 않는다.

## Coverage와 revision

spec의 `states`를 생략하면 assembly의 requested states를 검사한다. 모든 piece는 각 요청 clip의 모든 sample에 mapping이 있어야 한다. 아직 만드는 중인 pack만 `--allow-incomplete`로 가져올 수 있으며 결과의 `missing_coverage`와 snapshot의 `import.coverage: partial`에 누락을 남긴다. 완성 preview/release를 승인하는 기능이 아니다.

결과는 `parts/<id>/<digest>/part.json`, normalized images와 source byte snapshots다. 이미지 경로는 `part.json`의 부모 기준이다. Catalog에는 실제 piece가 존재하는 slot의 option으로 등록하며 기본 선택을 자동으로 변경하지 않는다.

같은 ID의 새 revision은 새로운 catalog digest를 만들고 이전 part/catalog를 참조하는 appearance/lock과 review를 보수적으로 분리한다. Body 추가/변경도 appearance를 분리한다. 기존 immutable 파일을 지우지 않고 `changes/`에 이전 reference와 경로를 기록한다. Index 갱신에는 CAS를 적용하며 다른 작업의 편집을 덮어쓰지 않는다. 동일 revision 재등록은 기존 snapshot을 검증해 재사용한다.

## 확인한 범위

[Import tests](../../tests/frames/test_parts_import.py)는 full-canvas/cropped 복원의 픽셀 일치, 5×4px 조각·반투명 edge·alpha-hole 보존, 다중 body piece, SD/tall/사족/블롭/6지의 같은 importer, mask/region 크기, missing coverage, style/rig/view 오류, clipping, symlink, source 보존, immutable revision, disk failure, index 동시 변경 및 실제 CLI를 검사한다. 실제 사용자 아트의 교체 성공, draw-order 또는 96 clip 데모의 완료를 뜻하지 않는다.
