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

## 포즈마다 한 번 그리기

motion의 sample에 [`pose_id`](02-motion-snapshots.md#포즈-id)가 있으면 piece는 `sample_map` 대신 `pose_map`(포즈 ID → placement)으로 그림을 준다. import는 그 포즈를 보여 주는 모든 clip의 모든 sample에 같은 placement를 펼쳐 `sample_map`에 기록하고, 정규화한 `pose_map`도 함께 남긴다. 걷기와 뛰기가 같은 포즈를 공유하거나, 한 포즈를 두 번 재생해도 그림은 한 장이다.

```json
"pieces": [{
  "id": "torso", "slot": "upper", "node": "torso", "mode": "raster",
  "pose_map": {
    "run.01": {"image": {"file": "torso/run.01.png", "sha256": "<...>"}, "format": "full-canvas",
               "pivot": [64,104], "target_socket": "root"},
    "run.02": {"image": {"file": "torso/run.02.png", "sha256": "<...>"}, "format": "full-canvas",
               "pivot": [64,104], "target_socket": "root"}
  },
  "sample_map": {"run.right": {"sample.7": {"image": {"file": "torso/run.02-held.png", "sha256": "<...>"},
                                            "format": "full-canvas", "pivot": [64,104], "target_socket": "root"}}}
}]
```

같은 sample을 `sample_map`에도 적으면 그 항목이 이긴다(특정 프레임만 다르게 그린 경우). 어떤 sample에도 없는 포즈 ID는 오류다. 계약은 `pose_map`의 각 포즈가 그 포즈의 모든 sample에 펼쳐져 있는지 검사한다. `pose_map`을 쓰지 않는 기존 spec과 pack의 digest는 바뀌지 않는다.

## 기본 body와 교체

Body spec은 `role: body`, `occupies: []`이며 각 piece의 `slot`은 null이다. `provides`로 해당 그림의 node interface를 선언하고 body piece는 자기 primary node를 포함해야 한다. Body를 가짜 hair/upper/footwear 옵션으로 등록하지 않는다.

`body_mode: segmented`는 실제로 분리한 node 그림과 그 sample mapping을 등록하는 경로다. `body_mode: flat`은 body piece 하나로 제한하고 root interface만 제공할 수 있다. 평면 body에 foot/ear/hand가 그려져 있다는 사실만으로 분리된 interface가 있다고 선언할 수 없다. 숨겨진 신체 그림을 복원하거나 기존 귀·꼬리·발 그림을 제거할 자료가 없으면 overlay-only 제약이 남는다. Node 교체와 중복 제공/가림의 최종 판정은 PR04 renderer에서 검사한다.

Body를 등록하면 assembly의 명시적 `body` reference 목록에 추가한다. Appearance도 정확한 body references를 가진다. Rig의 `default_body_ids`는 논리 이름이며 mutable library의 최신 body를 자동으로 찾아 선택하지 않는다.

같은 `design_id`의 SD/tall/canine fitting도 실제 각 rig/motion/view를 가리키는 별도 snapshot으로 제작할 수 있다. 서로 다른 canvas/style/view 또는 수정 전 motion reference는 거절한다. Source를 자동 resize/retarget해 다른 체형에 호환 표시하지 않는다.

## 포즈 가이드 시트로 포즈별 파츠 만들기

포즈마다 다른 그림이 필요한 파츠(신발·하의·장갑 등)는 frozen motion의 최종 body frame 위에 직접 그려야 정확히 맞는다. `parts-pose-sheet`는 요청 state의 모든 sample body를 정수 배율로 한 장의 단색 key 시트에 배치하고, 격자·crop·배율·sample별 body hash를 `pose-sheet.json`에 기록한다. 결과의 `prompt_hint`는 이미지 모델이나 작가에게 줄 지시문 예시다.

```bash
.venv/bin/sprite-gen parts-pose-sheet --assembly assets/my-parts/assembly.json \
  --out pose-sheet/boots --crop 24,64,104,128 --scale 4 --part boots
# pose-sheet/boots/pose-sheet.png 에 각 칸의 포즈에 맞는 신발만 그린다 (나머지는 key 색 유지)
.venv/bin/sprite-gen parts-pose-slice --assembly assets/my-parts/assembly.json \
  --guide pose-sheet/boots/pose-sheet.json --art boots-drawn.png --out authored-parts/boots \
  --part-id explorer.boots-a --slot footwear --node foot_l --socket root
.venv/bin/sprite-gen parts-import --assembly assets/my-parts/assembly.json \
  --spec authored-parts/boots/import.json --source-root authored-parts/boots
```

`parts-pose-slice`는 같은 격자로 각 칸을 자른다. key 색과 `--tolerance` 이내의 픽셀, key 계열로 물든 가장자리를 지운다. 그다음 그려 온 그림 자체의 블록 격자(크기 `scale × 출력 배율`)의 위상을 찾아 블록마다 다수결 alpha와 중앙값 색으로 native 픽셀을 복원한다. 모델이 블록을 반 칸 밀어 그려도 1px 외곽선이 두 칸으로 갈라지지 않는다. 모델이 시트를 같은 비율로 확대·축소해 돌려줘도 guide 크기로 되돌린다. 비율이 다르거나, motion revision 또는 body hash가 guide와 다르면 거절한다. 각 sample은 rig canvas 크기의 `full-canvas` PNG가 되고, pivot을 그 sample의 target socket 좌표로 기록하므로 정확히 `(0,0)`에 놓인다. 빈 칸은 `visible: false`가 된다.

모델이 몸까지 그린 시트를 돌려준 경우 `--body-threshold N`은 원래 body와 채널 차이가 N 이하인 픽셀을 지운다. 파츠 색이 같은 위치의 몸 색과 같으면(예: 같은 외곽선) 그 픽셀도 지워지므로 파츠만 그린 시트가 기본 경로다. 이 명령은 import·palette 정규화·위치 보정·승인을 하지 않는다. 잘린 결과는 일반 `parts-import`, [피팅과 bounded repair](08-layer-export.md)와 review를 그대로 거친다.

### 페이지, 비율과 몸을 입혀 온 시트

이미지 모델은 한 장에 담긴 칸이 많을수록 정확도가 떨어지고, 출력 크기도 정해져 있다(예: 1024×1024). `--page-size 16`은 시트를 `page-N/` 폴더 여러 장으로 나눈다. `--aspect 1:1`은 각 장을 key 색으로 채워 모델의 출력 비율에 맞춘다. 잘라낼 때는 `--page GUIDE=ART`를 장마다 반복하고, 결과 spec은 하나로 합쳐진다. 모델이 시트를 같은 비율로 확대·축소해 돌려줘도 되지만, 비율 차이는 1%까지만 허용한다.

모델에는 "이 시트의 인물에게 이 옷을 입혀라"라고 요청하고 원래 옷 그림을 두 번째 참조로 주는 편이 가장 안정적이었다. 이렇게 몸까지 다시 그려 온 시트는 다음 정리 옵션으로 옷만 남긴다.

| 옵션 | 동작 |
|---|---|
| `--body-threshold N` | 원래 body와 색 차이가 N 이하인 픽셀을 지운다. 다시 그리며 1px 밀린 외곽선과 음영을 흡수하도록 반경 1px 안의 body 픽셀과 비교한다. |
| `--min-island N` | N px보다 작은 조각, 외곽선 색이 대부분인 조각, body 색이 대부분인 작은 조각을 지운다. |
| `--outline` | 남은 옷에 닿은 어두운 픽셀을 다시 붙인다. 옷 외곽선이 몸 외곽선과 겹친 자리를 되살린다. |
| `--within-body N` | body 윤곽에서 N px보다 먼 가장자리 번짐을 지운다. |
| `--region L,T,R,B` | 저작한 rig 좌표 영역 밖을 지운다. |
| `--palette` | 색을 지정 팔레트에 맞춘다. |

몸을 다시 그려 온 칸마다 결과는 `alignment`(그린 인물과 원래 몸 실루엣의 겹침 비율)를 기록하고, 0.7 미만인 칸을 `misaligned`로 보고한다. 이미지 모델이 페이지를 확대하거나 밀어 그리면 여기서 드러나므로, 그 페이지는 다시 생성한다. 실제로 맞게 그린 칸은 0.90–0.97, 모델이 키워 그린 페이지는 0.35 안팎이었다. 페이지를 나누면 짧은 마지막 페이지도 첫 페이지와 같은 격자와 크기를 쓴다. 칸 수가 적어 시트가 작아지면 모델이 내용을 출력 크기에 맞춰 키우기 때문이다.

모두 측정 가능한 정리일 뿐 그림을 승인하지 않는다. 결과는 일반 피팅·검수 경로를 거친다.

### 포즈 그룹으로 변형 재사용

`parts-pose-classes --socket head --bounds=-22,-2,22,30 --classes 4`는 socket 주변 그림이 비슷한 sample끼리 묶는다. 정렬한 영역의 alpha·색 차이로 k-medoids를 돌리며, 시작점은 결정적으로 고른다. 그룹마다 대표 sample이 하나씩 있다. `parts-pose-sheet --classes`는 대표만 그리고, `parts-pose-slice --classes`는 같은 그룹의 sample이 대표 그림을 자기 socket 위치에서 재사용하게 한다. 머리 각도별 헤어 변형처럼 그림 몇 장으로 많은 포즈를 맡을 때 쓴다.

같은 clip 안에서 그룹이 번갈아 나오면 변형 사이의 그림 차이가 깜빡임이 된다. SD 실험에서 모델이 그린 헤어 4종은 스타일이 미세하게 달라 걷기에서 번갈아 보였다. 그래서 헤어는 원본 한 장 + socket 추적 + follow-through를 유지했다. 그룹 변형은 일관되게 저작한 그림에 맞는다.

### 포즈 단위 시트

`parts-pose-sheet --by-pose`는 포즈 ID마다 칸 하나만 만든다(첫 sample). `parts-pose-slice --by-pose`는 잘라낸 그림을 `pose_map`으로 기록하므로, 같은 포즈의 다른 sample과 나중에 그 포즈를 재사용하는 클립이 자동으로 같은 그림을 쓴다. 모든 sample에 `pose_id`가 있어야 하며 `--classes`와 함께 쓰지 않는다.

## 몸을 조각으로 나누고 옷을 조각으로 나누기

몸이 한 장(flat)이면 옷은 "몸 위로 보이는 부분"만 담을 수 있다. 뒷소매를 몸통 뒤에, 앞소매를 몸통 앞에 두려면 몸이 포즈마다 저작한 몇 개의 node 조각(예: 뒷팔 / 뒷다리 / 몸통+머리 / 앞다리 / 앞팔)이어야 한다. 런타임에 몸을 자르거나 관절을 변형하지 않는다. 조각은 저작 시 한 번 나누고 고정한다.

`parts-body-segments`는 포즈마다 칠한 라벨로 motion의 몸 raster를 node 조각으로 자른다. 라벨은 조각마다 단색이다. 키가 조각의 node이고, `provides`로 한 조각이 여러 node를 맡을 수 있다(손까지 그린 팔). 이렇게 묶은 node는 따로 교체할 수 없다.

```json
{"arm_l": {"color": "#3050ff", "provides": ["hand_l"]},
 "leg_l": {"color": "#30c0ff", "provides": ["foot_l"]},
 "root":  {"color": "#ff3030", "provides": ["torso", "head"]},
 "leg_r": {"color": "#ffa030", "provides": ["foot_r"]},
 "arm_r": {"color": "#a040ff", "provides": ["hand_r"]}}
```

오른쪽을 보는 측면에서는 인물의 오른쪽이 관객 쪽이므로, 위 예시는 왼팔·왼다리가 뒤, 오른팔·오른다리가 앞이다.

```bash
.venv/bin/sprite-gen parts-pose-sheet --assembly assets/my-parts/assembly.json \
  --out sheets/labels --by-pose --scale 4 --page-size 12 --aspect 1:1
# 각 칸의 몸을 node별 단색으로 칠한다 (사람 또는 이미지 모델)
.venv/bin/sprite-gen parts-body-segments --assembly assets/my-parts/assembly.json \
  --colors segment-colors.json --page sheets/labels/page-1/pose-sheet.json=labels-1.png \
  --page sheets/labels/page-2/pose-sheet.json=labels-2.png \
  --body-id explorer.body.segmented --out authored-parts/body
.venv/bin/sprite-gen parts-import --assembly assets/my-parts/assembly.json \
  --spec authored-parts/body/import.json --source-root authored-parts/body
```

라벨은 `--labels DIR`(`<pose_id>.png` 또는 `<clip>/<sample>.png`, rig canvas 크기)이나 칠한 포즈 시트 `--page`로 준다. 라벨 색에서 `--tolerance`(기본 40) 안의 픽셀만 씨앗이 되고, 몸의 모든 픽셀은 가장 가까운 씨앗의 node를 받는다(8방향, 고정된 동률 순서). 그래서 이미지 모델이 몸 윤곽을 조금 벗어나게 칠해도 조각은 몸 raster의 픽셀로만 나뉘고, 조각의 합은 원래 몸과 정확히 같다. `colors`의 순서가 piece 순서이고, 각 piece는 자기 node와 `provides`의 node를 제공한다. 모든 sample에 포즈 ID가 있으면 기본적으로 포즈마다 한 번 자른다(`--by-sample`로 끌 수 있다). 결과에는 `segmented` body spec, sample별 라벨 지도(`labels/<clip>/<sample>.png`)와 `segments.json`이 있다. `unprovided_required_nodes`는 topology가 required로 선언했지만 라벨이 없는 node다. 그런 topology에서는 조합이 실패하므로 rig 저작 단계에서 node 목록을 조각 목록에 맞춘다. 칠한 비율이 절반 미만인 sample은 `loosely_painted`로 보고한다.

옷은 같은 라벨로 조각낸다. `parts-piece-split`은 import spec의 한 piece를, 각 픽셀 아래(또는 가장 가까운) 몸 조각에 따라 여러 piece로 나눈다.

```bash
.venv/bin/sprite-gen parts-piece-split --spec authored-parts/jacket/import.json \
  --segments authored-parts/body/segments.json --piece jacket \
  --into sleeve_back:arm_l=arm_l --into torso:torso=root,leg_l,leg_r \
  --into sleeve_front:arm_r=arm_r --out authored-parts/jacket-split
```

`--into PIECE:NODE=SEGMENT[,SEGMENT]`의 새 piece는 원래 piece의 slot·mode·cover rule을 물려받고 NODE에 놓인다(slot이 허용한 node여야 한다). `*`는 나머지 조각을 받는다. 어느 piece에도 배정되지 않은 조각에 픽셀이 있으면 거절한다. 장갑·신발처럼 따로 떨어진 물건은 `--by-component`를 준다. 연결된 획 하나를 가장 많이 덮은 조각이 통째로 가져가므로, 앞장갑 가장자리가 몸통 위에 걸쳐 있어도 뒷장갑 조각으로 잘려 나가지 않는다. `full-canvas` 그림만 나눌 수 있고, `pose_map` piece는 포즈를 자른 sample의 라벨을 쓴다. spec의 다른 파일(다른 piece, mask, palette region)은 `--out` 아래로 함께 복사한다. 조각의 앞뒤는 [그리기 순서표](04-composition.md#draw-table-and-cover-rules)가 정한다.

## 모션을 바꾼 뒤 body와 고정 파츠 옮기기

기준 모션을 새로 freeze하면 이전 모션을 참조하던 body·파츠·appearance는 분리된다. Body가 모션 프레임 자체라면 `parts-body-from-motion`이 sample마다 그 raster를 쓰는 flat body spec을 만든다. 이미지 한 장을 socket에 거는 held 파츠(헤어·표정·모자 등)는 `parts-rebind`가 같은 그림·pivot·순서를 새 sample 전체에 반복한 spec을 만든다.

```bash
.venv/bin/sprite-gen parts-body-from-motion --assembly assets/my-parts/assembly.json \
  --out import/body.json --body-id explorer.body
.venv/bin/sprite-gen parts-rebind --assembly assets/my-parts/assembly.json \
  --part old-library/parts/explorer.hair-a/<digest>/part.json --out import/hair-a.json
.venv/bin/sprite-gen parts-rebind --assembly assets/my-parts/assembly.json \
  --part old-library/parts/explorer.pack/<digest>/part.json --out import/pack.json \
  --follow pack=torso:64,69
```

`--secondary PIECE=SOCKET[:GAIN[:MAX_PX]]`는 follow-through를 준다. 직전 sample에서 socket이 움직인 만큼에 GAIN(기본 1/2)을 곱하고 최대 MAX_PX(기본 1)로 자른 정수 offset을 더해, 그림이 socket을 한 박자 늦게 따라가게 한다. 헤어나 배낭에 쓴다.

두 명령 모두 결과의 `spec`·`source_root`를 그대로 `parts-import`에 넘긴다. `--socket PIECE=SOCKET[:X,Y]`는 piece를 다른 socket에 건다. 단, slot이 허용한 socket이어야 한다. slot이 허용하지 않으면 `--follow PIECE=SOCKET:X,Y`를 쓴다. 이 옵션은 원래 socket은 유지하고, 지정 socket이 `(X,Y)`에서 움직인 만큼 sample별 offset을 더한다. 예를 들어 root에 걸린 등 장비가 몸통의 상하 움직임을 따라가게 된다. `pose_map`으로 그린 piece는 포즈 단위로 옮긴다. 새 motion의 요청 state에 있는 모든 포즈를 그 piece가 그렸으면 같은 그림을 그대로 쓰고, 빠진 포즈가 있으면 목록과 함께 거절한다. [기존 포즈로 만든 클립](02-motion-snapshots.md#기존-포즈로-클립-만들기)은 그림 추가 없이 대응한다. 이때 개별 sample만 다르게 그린 `sample_map` 항목은 옮기지 않는다. 포즈 ID가 없는 sample별 raster piece와 이전 포즈에 묶인 rig-canvas mask는 거절한다. 그런 파츠는 [포즈 가이드 시트](#포즈-가이드-시트로-포즈별-파츠-만들기)로 새 포즈에 다시 그린다.

## Coverage와 revision

spec의 `states`를 생략하면 assembly의 requested states를 검사한다. 모든 piece는 각 요청 clip의 모든 sample에 mapping이 있어야 한다. 아직 만드는 중인 pack만 `--allow-incomplete`로 가져올 수 있으며 결과의 `missing_coverage`와 snapshot의 `import.coverage: partial`에 누락을 남긴다. 완성 preview/release를 승인하는 기능이 아니다.

`parts-coverage`는 active motion 기준으로 body와 catalog의 모든 pack에 대해 piece × clip 매트릭스(덮은 sample/전체, 보이는 sample, 서로 다른 그림 수, 포즈로 채운 sample)와 그리지 않은 포즈, 요청 state의 빈칸을 보고한다. `--markdown FILE`은 같은 표를 Markdown으로 쓴다. 새 포즈를 추가하기 전에 영향을 받는 파츠 수를 여기서 확인한다.

결과는 `parts/<id>/<digest>/part.json`, normalized images와 source byte snapshots다. 이미지 경로는 `part.json`의 부모 기준이다. Catalog에는 실제 piece가 존재하는 slot의 option으로 등록하며 기본 선택을 자동으로 변경하지 않는다.

같은 ID의 새 revision은 새로운 catalog digest를 만들고 이전 part/catalog를 참조하는 appearance/lock과 review를 보수적으로 분리한다. Body 추가/변경도 appearance를 분리한다. 기존 immutable 파일을 지우지 않고 `changes/`에 이전 reference와 경로를 기록한다. Index 갱신에는 CAS를 적용하며 다른 작업의 편집을 덮어쓰지 않는다. 동일 revision 재등록은 기존 snapshot을 검증해 재사용한다.

## 확인한 범위

[Import tests](../../tests/frames/test_parts_import.py)는 full-canvas/cropped 복원의 픽셀 일치, 5×4px 조각·반투명 edge·alpha-hole 보존, 다중 body piece, SD/tall/사족/블롭/6지의 같은 importer, mask/region 크기, missing coverage, style/rig/view 오류, clipping, symlink, source 보존, immutable revision, disk failure, index 동시 변경 및 실제 CLI를 검사한다. 실제 사용자 아트의 교체 성공, draw-order 또는 96 clip 데모의 완료를 뜻하지 않는다.
