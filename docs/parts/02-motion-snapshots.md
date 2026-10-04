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
    "pose_id": null
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

## Revision과 실패 복구

같은 motion ID의 이미지·시간·socket·annotation이 바뀌면 새 digest를 만든다. 변경된 motion을 참조하는 active PartPack·catalog·appearance·lock을 보수적으로 식별한다. v1 review의 composite subject hash만으로 의존성을 복원할 수 없으므로 active motion 변경 시 review는 모두 분리한다. 오래된 파츠를 active index에서 분리하고 catalog options/default/preset에서 해당 reference를 제거한다. 기록은 `changes/<motion-id>/<digest>/invalidation.json`에 이전 reference와 경로로 남긴다. 기존 immutable motion/part와 과거 파일을 삭제하거나 다시 그리지 않는다.

active appearance가 새 motion과 다르면 분리하며, 이후 `--complete`/release에는 새 binding과 외형이 필요하다. 다른 motion ID는 별도 모션이다. 이전 ID의 객체를 새로운 ID의 포즈 호환성 증거로 사용하지 않는다.

assembly 갱신은 읽은 index hash를 compare-and-swap한다. 진행 중 다른 작업이 index를 바꿨으면 덮어쓰지 않는다. 완성한 frozen object는 보존되고 활성화는 재시도가 필요하다. snapshot 쓰기 실패는 부분 객체를 공개하지 않으며 이전 active assembly를 유지한다.

## 제한과 검증

v1은 JSON 4 MiB/64 nesting level, binary 파일 64 MiB, snapshot 256 MiB/4096 files, clip 2048 samples, frame 4,194,304 pixels, decoded clip 67,108,864 pixels를 제한으로 사용한다. frame/cell 크기와 이미지 header는 decode 전에 검사한다. library 경로는 portable relative path이며 symlink/junction을 허용하지 않는다. POSIX에서는 directory FD와 `O_NOFOLLOW`로 경로를 따라간다. Windows는 resolve/junction 검사와 bounded regular-file IO를 사용한다.

[어댑터 테스트](../../tests/spec/test_parts_assets.py)와 [모션 단계 테스트](../../tests/curate/test_parts_motion.py)는 실제 finalized fixture와 video strip, decimal 시간, 반복 이미지·curated clone, socket 누락/미검증/stale raster, source 교체, hostile header, disk failure, index 동시 변경, 이전 snapshot 보존과 CLI 경로를 검사한다. 실제 사용자 아트의 motion/fitting 승인이나 96 clip 데모 완료를 의미하지 않는다.
