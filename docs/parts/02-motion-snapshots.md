# 기준 모션 가져오기와 동결

> Owns: Implemented PR02 source adapters, motion snapshots and revision invalidation · Index: [docs/README.md](../README.md)

`parts-motion-import`는 기존 finalized atlas·video-loop strip·외부 frame descriptor를 별도 workspace의 draft snapshot으로 가져온다. `parts-motion-freeze`는 최종 raster에 연결된 명시적 socket annotation을 확인하고 독립 motion snapshot을 게시한다. 원본 run에 `heal_run`을 호출하거나 `frames/`, `curation.json`, manifest를 수정하지 않는다. Frozen 상태는 시각적 품질이나 release 승인 상태와 별개다.

## Import 입력

[Workspace 초기화](01-contract.md)를 먼저 실행한다. import spec은 새 계약이며, source descriptor는 기존 [공통 asset 형식](../asset-tools.md)을 따른다.

```json
{
  "kind": "parts-motion-import",
  "schema_version": 1,
  "id": "explorer.motion.v1",
  "rig": {"id": "human.sd.v1", "digest": "<profiles/rig.json의 정확한 digest>"},
  "clips": [{
    "id": "walk.right",
    "state": "walk",
    "file": "walk.asset.json",
    "timebase": 120000,
    "timing_policy": "exact",
    "source_mode": "external",
    "expected_files": {
      "walk.asset.json": "<descriptor 파일 바이트 SHA-256>",
      "pose-0.png": "<파일 바이트 SHA-256>",
      "pose-1.png": "<파일 바이트 SHA-256>"
    }
  }]
}
```

angle-bracket 값은 실제 lowercase SHA-256 값으로 교체해야 한다. `file`과 `expected_files`의 경로는 `--source-root` 기준이다. descriptor 안의 이미지 경로는 기존 형식대로 descriptor 부모 기준이다. `expected_files`는 실제로 읽는 모든 descriptor/이미지의 정확한 해시 집합이어야 한다. 누락과 사용하지 않는 추가 항목을 모두 거절한다. 이미지가 여러 파일이어도 선언한 입력 바이트에 일치하는 하나의 snapshot을 확보하며, 가져온 이후 원본 변경은 snapshot에 영향을 주지 않는다.

```bash
.venv/bin/sprite-gen parts-motion-import \
  --assembly assets/my-parts/assembly.json \
  --spec motion-import.json --source-root finalized-input/
```

CLI는 workspace-relative `candidate`와 `annotation_template`, candidate digest 및 sample 수를 JSON으로 반환한다. raw source 바이트와 normalized RGBA PNG도 함께 저장한다.

```text
workspace/
  candidates/<motion-id>/<candidate-digest>/
    candidate.json
    images/<png-sha256>.png
    sources/<source-sha256>.bin
  annotations/<candidate-digest>.draft.json
```

candidate snapshot은 내용이 같으면 재사용한다. 기존 digest 폴더의 파일이 변조됐으면 덮어쓰지 않고 오류를 낸다. 별도 annotation 초안은 사용자 편집용이다. 같은 candidate를 다시 import해도 이미 편집한 annotation을 초기화하지 않는다.

## Source 선택과 시간

| Source | 입력 설정과 보존할 정보 |
|---|---|
| native finalized atlas | `file: manifest.json`, `source_mode: native-finalized`, `source_state: walk`; 최종 sheet/rect 순서·canvas·`durations_ms`·loop |
| 외부 runtime atlas | `source_mode: external`, 필요 시 `source_state`; 같은 공통 atlas decoder |
| final video-loop strip | `file: walk.strip.json`; sidecar의 실제 `w/h`, `delay_ms`, loop/one-shot |
| `sprite-gen-asset` descriptor | frames 배열의 재생 순서·duration·파일, 선택적 원본 frame ID |
| 단일 still | 실제 canvas 그대로 한 sample; 기본 1초, 필요 시 명시적 fps override |

native 입력을 읽는 동안 기존 `read_guard`를 사용하고, 그 경우에도 expected file hashes를 검사한다. 원본 request의 cell이나 기본 bottom-center anchor를 socket으로 승격하지 않는다. 이미 bake된 최종 sheet와 strip의 geometry만 기준으로 삼는다. source와 rig의 canvas가 다르면 자동 resize·padding·재정렬을 수행하지 않고 거절한다. video strip에 crop/scale이 적용됐다면 그 최종 canvas에 맞는 별도 concrete rig를 먼저 작성해야 한다.

시간은 descriptor의 원래 JSON decimal을 `Decimal`/`Fraction`으로 읽는다. `FrameSequence.durations`의 float seconds에서 원본 시간을 역추정하지 않는다. `exact`에서는 각 duration이 timebase의 정수 tick으로 정확히 표현돼야 한다. 그렇지 않으면 timebase를 바꾸거나 `round-boundaries`를 명시해야 한다. 후자는 누적 경계를 half-up 반올림하고 인접 경계의 차이를 sample tick으로 기록한다. 0 tick이 생기면 거절한다.

예를 들어 정확한 24fps 24 sample은 timebase 120000에서 각 5000 tick이다. timebase 1000의 boundary rounding에서는 41/42 tick을 배분해 총 1000 tick을 보존한다. 이미 native manifest에 각 42ms로 bake된 24 sample은 총 1008ms를 그대로 보존한다. final strip의 166.67ms도 1/6초로 다시 해석하지 않는다. 이런 fractional ms에는 timebase 100000 또는 적절한 명시적 rounding policy가 필요하다.

`fps`는 optional rational string override(예: `"24"`)다. `loop`도 optional boolean override이며 생략 시 원본 값을 보존한다. 변경 정책과 원본 파일 해시·duration fraction·변환 오차는 clip의 `source` 필드와 motion digest에 포함한다. parser도 timing provenance가 실제 sample ticks와 같은지 검사한다.

bitmap이 반복돼도 `sample.0`, `sample.1` 같은 timeline 위치는 합치지 않는다. PNG만 content hash로 dedup한다. curated 재정렬과 clone은 finalized atlas의 재생 위치대로 가져오며, `source_frame_id`는 최종 source의 state/ordinal/rect 또는 외부 frame ID를 가리킨다. raw pool의 clone ID와 이전 landmark geometry를 추측해 복원하지 않는다.

## 영상 루프에서 기준 모션 만들기

파츠의 기준 모션은 `video-set`(또는 `video-loop`)이 자른 최종 루프를 그대로 쓰는 것이 기본 경로다. 손으로 고른 프레임 구간은 걸음 주기·드로잉 hold·흔들림 검사를 거치지 않는다. `parts-loop-native`는 루프의 `cycle/` 프레임을 rig canvas로 옮긴다.

```bash
.venv/bin/sprite-gen video-set --base side=base.png --facing right --states walk,run \
  --out-dir video --character "The bald chibi mannequin" --anchor motion-auto --key green
.venv/bin/sprite-gen parts-loop-native --assembly assets/my-parts/assembly.json --out native \
  --loop idle=idle-frames@1 --loop walk=video/side-walk/loop --loop run=video/side-run/loop \
  --body-height 83 --baseline 112 --palette body-palette.json --motion-id explorer.motion.v1
.venv/bin/sprite-gen parts-motion-import --assembly assets/my-parts/assembly.json \
  --spec native/motion-import.json --source-root native --strict-motion
```

기본 `--resample lattice`는 영상이 그린 픽셀 격자 자체를 읽는다. 업스케일한 픽셀아트 still로 찍은 클립은 still의 격자를 유지한다. 그래서 `가장 큰 포즈 높이 / --body-height` 근처(±3%)에서 셀 안 색 분산이 가장 작은 간격과 위상을 찾고, 셀 하나를 native 픽셀 하나로 읽는다(안쪽 픽셀의 중앙값 색, 다수결 alpha). 평균 축소(`smooth`)나 블록 대표색(`kcentroid`)은 격자가 어긋나면 1px 외곽선을 두 픽셀로 나눠 지운다. SD 실험에서 간격은 10.1, native 키는 85–86px였다. 이미 native 크기인 입력(간격 1.5 미만)은 그대로 쓴다. `--silhouette-outline darkest`는 바깥 윤곽(투명과 닿은 픽셀)을 팔레트의 가장 어두운 색으로 칠하고, 그 전에 한 픽셀짜리 돌기를 지워 끊긴 외곽선을 닫는다.

`smooth`/`kcentroid`에서는 루프마다 가장 큰 포즈를 `--body-height`로, 가장 낮은 발을 `--baseline` 행으로 맞추는 **한 번의** 배율·이동을 적용한다. `lattice`에서는 격자 그대로 두고 이동만 적용한다. 프레임별로 다시 맞추지 않으므로 걸음의 상하 움직임과 흔들림은 그대로 남는다. Alpha는 이진화하고, `--palette`를 주면 가장 가까운 색으로 맞춘다. 배경색이 번진 팔다리 색도 여기서 정리된다. `loop.report.json`의 `cycle_drawings.hold`가 있으면(예: 한 그림을 두 프레임씩 보여 준 클립) 그 hold로 묶어 한 sample의 길이를 늘린다. 없으면 `--hold-tolerance`(기본 0, 완전히 같은 프레임만) 안의 연속 프레임만 묶는다. 결과의 `projection`·`holds`로 무엇을 했는지 확인한다.

`parts-motion-import`의 결과에는 루프 clip마다 `motion_quality`가 붙는다. `video-loop`과 같은 jolt index와 머리 좌우 이동 기준([loop repair](../loop-repair.md) 3절)을 최종 sample에 적용한 값이다. native 크기에서는 1px이 머리가 움직일 수 있는 최소 단위다. 그래서 머리 좌우 이동 기준은 참조값(0.75%)과 1.5px 중 큰 값으로 한다. walk/run이 기준을 넘으면 `warnings`에 남고, `--strict-motion`이면 import를 거절한다. 다른 state는 측정만 기록한다. 이 값은 흔들림 신호일 뿐 동작의 시각 승인이 아니다.

## Annotation과 freeze

초안에는 최종 PNG hash와 topology의 socket 이름을 넣지만 좌표는 `[0,0]`, `visible: false`, `confidence: unverified`다. 최종 PNG를 보고 정확한 rig-canvas 좌표와 visibility를 입력해야 한다. 제공한 socket 중 하나라도 unverified이면 freeze를 거절한다. topology의 required socket은 각 sample에 반드시 있어야 한다. 후속 파츠가 쓸 optional socket도 이 단계에서 annotation하는 것이 좋다.

```json
{
  "kind": "parts-motion-annotations",
  "schema_version": 1,
  "id": "explorer.motion.v1",
  "candidate_digest": "<대상 candidate digest>",
  "clips": {"walk.right": {"sample.0": {
    "image_sha256": "<해당 normalized PNG 바이트 SHA-256>",
    "sockets": {
      "root": {"position": [64,120], "visible": true, "confidence": "verified"},
      "face": {"position": [74,40], "visible": true, "confidence": "verified"}
    },
    "pose_id": "walk.01"
  }}}
}
```

예시는 형식 설명이다. 실제 입력에는 모든 clip/sample과 필요한 socket이 있어야 한다. `verified`라는 문자열은 좌표의 시각적 정확성을 자동으로 증명하지 않는다. 이 단계는 정확한 raster 연결과 선언을 검사하며, fitting·contact·motion 품질은 후속 QA/review 단계에서 검사한다.

```bash
.venv/bin/sprite-gen parts-motion-freeze \
  --assembly assets/my-parts/assembly.json \
  --candidate candidates/explorer.motion.v1/<digest>/candidate.json \
  --annotations assets/my-parts/annotations/<digest>.draft.json
```

candidate digest와 각 sample의 raster hash가 일치해야 한다. 이미지가 6px 이동했는데 예전 hash의 좌표를 제출하면 오류다. freeze는 저장한 source 바이트로 같은 adapter를 다시 실행해 순서·시간·normalized pixel을 재구성하고 candidate와 비교한다. JSON/image hash만 다시 계산해 원본과 다른 geometry를 같은 provenance로 표시하는 것도 차단한다. 현재 PNG encoder가 다른 byte를 만들더라도 decoded pixels가 같으면 원래 PNG와 annotation hash를 보존한다.

성공하면 `motions/<id>/<digest>/motion.json`, images, sources, `annotations.json`을 staging에서 한 폴더로 게시하고 assembly의 active motion을 갱신한다. source와 motion은 독립적이다. 결과는 `status: frozen`, `release_ready: false`다.

## Socket 좌표 제안

모든 sample 좌표를 손으로 입력하는 대신 `parts-socket-track`으로 초안을 채울 수 있다. 한 sample에서 socket 위치와 그 상대 영역(`bounds`)을 한 번 저작하면, 같은 영역 그림을 다른 sample에서 정수 이동으로 찾는다. 기준 그림이 고정이므로 위치가 누적해서 밀리지 않는다. 비용은 alpha 불일치 × `alpha_weight` + RGB 차이 픽셀 수다. 각 sample은 직전 결과에서 `radius` 안을 탐색한다.

```json
{
  "kind": "parts-socket-track",
  "schema_version": 1,
  "tracks": [
    {"socket": "head", "clips": ["walk.right"], "bounds": [-20, 0, 20, 28], "radius": 3, "max_cost": 200,
     "reference": {"clip": "walk.right", "sample": "sample.0", "position": [64, 30]}},
    {"socket": "head", "clips": ["run.right"], "bounds": [-20, 0, 20, 28], "radius": 3, "max_cost": 200,
     "reference": {"clip": "run.right", "sample": "sample.0", "position": [65, 28]}},
    {"socket": "root", "mode": "constant",
     "reference": {"clip": "walk.right", "sample": "sample.0", "position": [64, 120]}}
  ]
}
```

```bash
.venv/bin/sprite-gen parts-socket-track \
  --assembly assets/my-parts/assembly.json \
  --candidate candidates/explorer.motion.v1/<digest>/candidate.json \
  --annotations assets/my-parts/annotations/<digest>.draft.json \
  --spec socket-track.json --out assets/my-parts/annotations/tracked.json \
  --preview socket-track.png
```

clip마다 그림이 다르면 같은 socket을 clip별 기준으로 나눠 선언한다. 한 clip에서 같은 socket을 두 번 추적하는 것은 거절한다. `mode: constant`는 지정 좌표를 그대로 기록한다. 결과의 각 sample은 `cost`, 2px 이상 떨어진 차선과의 차이인 `margin`, 탐색 경계에서 멈췄는지 나타내는 `at_edge`를 가진다. `max_cost`를 넘거나 경계에 닿은 sample은 `needs-review`다.

기본 결과는 좌표만 채우고 `unverified`로 남기므로 그대로는 freeze할 수 없다. `--preview` contact sheet에서 영역과 socket 위치를 확인한 뒤 `--accept`로 다시 실행하면 `needs-review`가 아닌 제안만 `verified`가 된다. 나머지와 추적하지 않은 socket은 직접 입력한다. 이 명령은 raster를 수정하지 않고 draft 파일도 덮어쓰지 않으며(`--out`에 새로 기록), 해부학적 위치를 추론하지 않는다. 가장 위 불투명 픽셀처럼 머리카락 한 가닥에 흔들리는 기준 대신, 저작한 영역 전체의 그림을 맞춘다.

## 포즈 ID

포즈는 승인된 몸 그림 하나다. 같은 raster를 보여 주는 sample은 같은 포즈이고, 파츠는 sample이 아니라 포즈마다 그린다([pose_map](03-part-import.md#포즈마다-한-번-그리기)). freeze 전에 annotation 초안의 `pose_id`를 채운다.

```bash
.venv/bin/sprite-gen parts-pose-assign \
  --assembly assets/my-parts/assembly.json \
  --annotations assets/my-parts/annotations/<digest>.draft.json \
  --pose run.right/sample.4=run.passing
```

같은 raster hash의 sample은 하나의 ID를 받는다. 우선순위는 `--pose CLIP/SAMPLE=ID`로 준 이름, 초안에 이미 있는 `pose_id`, active motion이 같은 raster에 준 이름, 마지막으로 `<clip 앞부분>.<번호>`(예: `run.03`) 순서다. 한 raster에 두 이름을 주면 거절한다. 결과는 포즈 수, 생성·재사용한 이름 수와 여러 sample이 공유하는 포즈 목록이다. 모션 revision을 다시 import해도 같은 그림은 같은 포즈 ID를 유지하므로, 포즈별 파츠가 그대로 대응한다.

## 기존 포즈로 클립 만들기

재생 속도 변경, 순서 재조합, 정지 프레임, 착지 때 몸 전체를 몇 px 내리는 변주는 새 포즈가 아니다. `parts-motion-compose`는 active frozen motion의 포즈만으로 새 클립을 만들고, 남길 기존 클립과 함께 새 motion revision으로 import·freeze한다.

```json
{
  "kind": "parts-motion-compose",
  "schema_version": 1,
  "id": "explorer.motion.v2",
  "keep": ["idle.right", "walk.right", "run.right"],
  "clips": [{"id": "hop.right", "state": "hop", "loop": false, "timebase": 120000, "samples": [
    {"pose": "run.02", "duration_ticks": 12000},
    {"pose": "run.05", "duration_ticks": 6000, "offset": [0, -3]},
    {"pose": "run.02", "duration_ticks": 12000}
  ]}]
}
```

```bash
.venv/bin/sprite-gen parts-motion-compose \
  --assembly assets/my-parts/assembly.json --spec compose.json --out assets/compose-v2
```

`keep`을 생략하면 기존 클립을 모두 남긴다. 남긴 클립은 sample 순서와 tick을 그대로 복사한다. 새 sample은 포즈의 raster와 모든 socket을 `offset`만큼 정수 이동하고, `pose_id`를 유지한다. 이동으로 불투명 픽셀이나 보이는 socket이 canvas 밖으로 나가면 거절한다. `offset`은 착지 때 몸이 내려앉는 것 같은 포즈 안의 변주다. 점프 높이처럼 캐릭터 전체의 이동은 클립이 아니라 게임 런타임이 스프라이트 위치로 준다. 클립 ID와 state는 각각 하나여야 한다. 중간 산출물(프레임, `motion-import.json`, 채운 `annotations.json`)은 `--out`에 남고, `--no-freeze`는 candidate까지만 만든다.

새 motion을 freeze하면 이전 motion에 묶인 파츠는 [일반 revision 규칙](#revision과-실패-복구)대로 분리된다. 포즈별로 그린 파츠는 `parts-rebind`가 새 클립의 같은 포즈에 그대로 옮기므로 그림을 새로 만들지 않는다. 반전(mirror)은 sample 단위가 아니라 appearance 단위다.

## Revision과 실패 복구

같은 motion ID의 이미지·시간·socket·annotation이 바뀌면 새 digest를 만든다. 변경된 motion을 참조하는 active PartPack·catalog·appearance·lock을 보수적으로 식별한다. v1 review의 composite subject hash만으로 의존성을 복원할 수 없으므로 active motion 변경 시 review는 모두 분리한다. 오래된 파츠를 active index에서 분리하고 catalog options/default/preset에서 해당 reference를 제거한다. 기록은 `changes/<motion-id>/<digest>/invalidation.json`에 이전 reference와 경로로 남긴다. 기존 immutable motion/part와 과거 파일을 삭제하거나 다시 그리지 않는다.

active appearance가 새 motion과 다르면 분리하며, 이후 `--complete`/release에는 새 binding과 외형이 필요하다. 다른 motion ID는 별도 모션이다. 이전 ID의 객체를 새로운 ID의 포즈 호환성 증거로 사용하지 않는다.

assembly 갱신은 읽은 index hash를 compare-and-swap한다. 진행 중 다른 작업이 index를 바꿨으면 덮어쓰지 않는다. 완성한 frozen object는 보존되고 활성화는 재시도가 필요하다. snapshot 쓰기 실패는 부분 객체를 공개하지 않으며 이전 active assembly를 유지한다.

## 제한과 검증

v1은 JSON 4 MiB/64 nesting level, binary 파일 64 MiB, snapshot 256 MiB/4096 files, clip 2048 samples, frame 4,194,304 pixels, decoded clip 67,108,864 pixels를 제한으로 사용한다. frame/cell 크기와 이미지 header는 decode 전에 검사한다. library 경로는 portable relative path이며 symlink/junction을 허용하지 않는다. POSIX에서는 directory FD와 `O_NOFOLLOW`로 경로를 따라간다. Windows는 resolve/junction 검사와 bounded regular-file IO를 사용한다.

[어댑터 테스트](../../tests/spec/test_parts_assets.py)와 [모션 단계 테스트](../../tests/curate/test_parts_motion.py)는 실제 finalized fixture와 video strip, decimal 시간, 반복 이미지·curated clone, socket 누락/미검증/stale raster, source 교체, hostile header, disk failure, index 동시 변경, 이전 snapshot 보존과 CLI 경로를 검사한다. 실제 사용자 아트의 motion/fitting 승인이나 96 clip 데모 완료를 의미하지 않는다.
