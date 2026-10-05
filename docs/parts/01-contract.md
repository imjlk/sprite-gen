# 파츠 선언 계약과 workspace CLI

> Owns: Implemented experimental v1 declarations and workspace commands · Index: [docs/README.md](../README.md)

PR00–01의 초기 선언 계약이다. `parts-init`과 `parts-validate`는 실행할 수 있다. topology·rig·모션·다중 piece 파츠·catalog·외형 선택의 선언과 정확한 해시 참조를 검사한다. PR02의 [모션 import·freeze](02-motion-snapshots.md)와 PR03의 [body/파츠 import](03-part-import.md)도 구현했다. 합성, 검수 승인과 export는 후속 구현이다. 이 문서의 계약은 experimental schema version 1이다.

## 실행

프로젝트 가상환경에 설치한 `sprite-gen`을 사용한다. 새 workspace는 기존 생성 run과 다른 디렉터리여야 하며, 이미 내용이 있으면 초기화를 거절한다.

```bash
.venv/bin/sprite-gen parts-init --dir assets/my-parts --preset human.sd.v1
.venv/bin/sprite-gen parts-validate --assembly assets/my-parts/assembly.json
```

초기 결과는 `status: draft`, `release_ready: false`다. 다음 네 파일을 만든다.

```text
assets/my-parts/
  assembly.json
  profiles/topology.json
  profiles/rig.json
  catalog.json
```

| preset | canvas | 선언된 customization |
|---|---|---|
| `human.sd.v1` | 128×128 | 헤어·표정·상의·하의·신발과 세부/추가 슬롯, 총 20개 |
| `human.tall.v1` | 128×192 | SD와 동일 humanoid topology, 별도 rig와 catalog |
| `canine.medium.v1` | 192×128 | coat·expression·ears·tail·neckwear·torso gear·pawwear, 네 발의 고유 ID |
| `blob.fixture.v1` | 96×96 | surface·expression·ornament·body accessory, 다리 필수 조건 없음 |
| `asymmetric.six-limb.v1` | 160×128 | surface와 여섯 독립 appendage, 대칭 pair 가정 없음 |

SD/tall의 20개 슬롯은 `hair`, `expression`, `upper`, `lower`, `footwear`, `headwear`, `bangs`, `eyes`, `brows`, `mouth`, `eyewear`, `gloves`, `back_accessory`, `neckwear`, `sleeve_l/r`, `footwear_l/r`, `hand_item_l/r`다. 기본 5개가 required이며 나머지는 optional이다. palette에는 skin/hair/fabric_primary/fabric_secondary/leather/eyes가 있다. 초기 options는 비어 있다. 슬롯이 존재한다는 사실은 파츠 그림이나 variation을 제작했다는 뜻이 아니다.

```bash
.venv/bin/sprite-gen parts-validate --assembly assets/my-parts/assembly.json --complete
```

초기 workspace에는 모션과 appearance가 없으므로 위 명령은 종료 코드 2와 JSON 오류를 반환한다. 완성된 선언 그래프에서는 requested state, 필수 슬롯의 실제 piece, 선택 파츠의 모든 sample mapping도 검사한다. 성공해도 `release_ready`는 항상 false다. raster와 socket의 시각적 정확성, body 분리, draw-order cycle, mask 피팅, QA 및 review 승인은 검사하지 않는다.

## 공통 필드와 digest

sealed 선언에는 `kind`, `schema_version: 1`, `id`, `digest`가 있다. ID는 소문자로 시작하는 최대 128자의 소문자·숫자·`.`·`_`·`-` 문자열이다. body/state/slot 이름은 열린 ID 집합이며 core에 SD/사람/동물별 분기가 없다. 라벨은 한국어를 포함한 nonempty string이다.

unknown field, bool/float을 정수로 사용한 좌표·tick·canvas, duplicate ID, 잘못된 reference, parent cycle과 비상호 대칭 선언을 거절한다. path는 portable relative path이고 절대 경로·`..`·역슬래시·URL을 허용하지 않는다. 이미지 참조는 `{ "file": "parts/shoe.png", "sha256": "<64 lowercase hex>" }`다. parser는 해당 파일을 열거나 이미지 바이트의 진위를 검사하지 않는다.

`seal`은 타입/기본값/로컬 관계를 검증한 normalized JSON에 digest를 붙인다. 해시는 `sprite-gen.parts.canonical.v1\0` domain prefix, UTF-8, key 정렬, `separators=(",", ":")`, `ensure_ascii=False`, `allow_nan=False`를 사용한다. 자기 `digest` 필드만 제외하고 모든 배열 순서를 유지한다. 생략한 기본값과 같은 기본값을 명시한 입력은 같은 해시를 갖는다. 시간, sample 순서, socket, visibility, pivot, mask, palette region과 참조 revision은 해시 대상이다.

이미 digest가 있는 문서를 `seal`하면 그 값을 검증한다. 수정한 문서를 자동으로 다시 승인하거나 기존 digest를 덮어쓰지 않는다. 새 revision을 작성할 때는 기존 digest를 명시적으로 제거하고 `seal`한 뒤, 그 문서를 참조하는 downstream 문서도 갱신한다.

## 선언 종류

| kind | 현재 검사하는 선언 |
|---|---|
| `parts-topology` | 하나의 root, nodes/parent/required/contact/symmetry, socket의 node, slot의 허용 node/socket·cardinality·empty policy, 선택 그리기 순서표 `layers` |
| `parts-rig` | 정확한 topology reference, 열린 style/view, canvas, default body 논리 ID와 mirror 정책 |
| `parts-motion` | rig reference, 고유 state/view clip, 순서가 고정된 sample·이미지·ticks·socket·visibility·confidence, optional source/freeze provenance |
| `parts-pack` | design ID, body/part role, segmented/flat body, rig/motion/view binding, occupies/requires/excludes/covers, 다중 piece, sample별·포즈별 placement·mask·provides/replaces·join·palette region·cover rule·source provenance |
| `parts-catalog` | rig/topology, group tree, slot별 options/default, palette channel, flat preset assignments |
| `parts-appearance` | rig/motion/catalog, 정확한 body references, 상태 목록, slot 선택·preset·palette override |
| `parts-fitting` | 정확한 rig/motion/part/binding/clip/sample reference, native 크기의 required/protected/allowed mask와 위치·크기·피복 기준 |
| `parts-stability` | 정확한 rig/motion/part/binding/piece reference, 상대 좌표의 안정 영역·기준 sample·검사 clips, 원본 alpha/RGB 변화 기준과 source bytes |
| `parts-review` | subject digest, part/binding/appearance scope, 상태 및 render/QA policy ID |
| `parts-build-lock` | appearance·입력·review의 정확한 reference, toolchain·policy·output asset hash |

review/build-lock은 저장 형식과 참조만 검사한다. 서명, 승인 주체, 실제 입력의 전체 집합 및 release gate를 구현한 것으로 취급하지 않는다.

참조는 `{ "id": "human.sd.v1", "digest": "<content digest>" }` 형식이다. 한 bundle에는 같은 ID의 활성 revision 하나만 존재한다. 같은 ID의 다른 내용, 잘못된 kind, 다른 rig/view 또는 수정 전 motion digest를 참조하는 파츠는 오류다. 의존 방향은 topology → rig → motion → binding → catalog → appearance다. rig의 body ID는 논리 이름이며 미래 PartPack digest를 역참조하지 않는다.

## Slot, preset과 piece

topology slot은 `nodes`, `sockets`, `optional`(기본 false), `max_count`(기본 1), `empty_policy`(기본 `body-default`)와 nullable `semantic_role`을 선언한다. 허용 node/socket 목록이 비어 있으면 특정 부착 interface로 제한하지 않는다. 목록이 있으면 piece의 node와 target socket이 그 목록에 속해야 한다.

slot 선택은 null, 하나의 PartPack reference 또는 고유 reference 배열이다. resolver는 catalog default → appearance에 적힌 순서의 preset → 명시적 selection override를 적용하고 입력을 변경하지 않는다. preset은 slot 선택만 확장하며 rig, motion, timing을 변경하지 않는다. 여러 슬롯을 점유하는 outfit과 여러 piece로 된 헤어/소매/신발을 표현할 수 있다. `occupies`의 충돌, `requires`의 누락, `excludes`와 join interface 불일치는 오류다. 필수 슬롯은 단순한 occupies 예약만으로 채워졌다고 처리하지 않는다.

`empty_policy`는 후속 renderer를 위한 선언이다. 현재 `body-default`가 기본 artwork를 만들거나 누락된 파츠를 통과시키지 않는다. 완성 검증에는 required slot을 제공하는 선택 piece가 필요하다. palette override는 catalog에 있는 channel만 허용하며 RGB는 0..255의 정수 3개다. 실제 recolor는 후속 구현이다.

placement에는 `image`, `pivot`, `target_socket`, `visible`, `offset`, optional `mask`, `before`/`after`가 있다. 정수 좌표를 보존하며 파일 이름/그림 크기로 scale이나 정렬을 추론하지 않는다. `mode: held`도 sample map을 명시해야 하며 모든 mapping이 동일한 file/hash 이미지 하나를 사용한다. visible piece의 target socket은 해당 motion sample에 있어야 한다. 가려진 piece도 완성 검증에서는 `visible: false` mapping으로 sample을 명시한다.

PR03은 body를 null slot의 별도 pack으로 등록하고 appearance/assembly의 `body` references로 고정한다. Flat body는 root interface만 제공한다. Placement에 `allow_clip`, sample별 `palette_regions`와 원본 crop/pivot 정보를 추가했으며 mask channel은 luma/alpha를 명시한다. 이 초기 schema의 확장도 새 normalized content digest를 만든다. 기존 digest를 조용히 바꿔 호환 표시하지 않는다.

## 선택 필드: 포즈, 그리기 순서표, 가림 규칙

다음 필드는 schema version 1의 선택 필드다. 적었을 때만 normalized 문서에 들어가므로, 이 필드가 없는 기존 문서의 내용과 digest는 그대로다.

| 위치 | 필드 | 뜻 |
|---|---|---|
| topology | `layers` | node ID의 뒤→앞 순서(그리기 순서표, zmap). 모든 항목이 topology node여야 한다. [조합 규칙](04-composition.md#draw-table-and-cover-rules) |
| pack binding | `covers` | 이 binding이 덮는 태그 목록(예: `headwear`) |
| piece | `cover_rule` | `{"tag": ID, "show": "covered" \| "uncovered"}`: 그 태그가 덮였을 때만, 또는 덮이지 않았을 때만 보인다 |
| piece | `pose_map` | 포즈 ID → placement. import가 그 포즈의 모든 sample로 `sample_map`에 펼친다 |

`pose_map`의 키는 해당 motion sample의 `pose_id`여야 한다. 계약은 각 포즈를 보여 주는 모든 sample이 `sample_map`에 있는지 검사한다. 같은 sample의 `sample_map` 항목이 `pose_map`보다 우선한다. `held` piece는 `pose_map`의 그림까지 포함해 한 장만 쓴다.

## 시간

clip의 `timebase`는 ticks/second의 양의 정수이고 sample의 `duration_ticks`도 양의 정수다. 특정 fps나 120000 timebase를 core에 고정하지 않는다. 같은 bitmap을 세 번 재생하면 서로 다른 sample ID 세 개를 기록한다. 이미지 dedup이 timeline sample을 합치지 않는다.

native/external 시간의 정확한 변환과 provenance, 최종 raster에 연결된 annotation 검증은 [PR02](02-motion-snapshots.md)가 담당한다. `source`가 있으면 fraction·변환 오차·timing policy가 sample ticks와 일치해야 한다. `freeze`는 candidate digest와 annotation/source asset hashes를 담는다. 선언 검증은 정수 timing을 보존하며 원본 float에서 시간을 추정하지 않는다.

## Python API와 파일 경계

```python
from sprite_gen.spec.parts_contract import seal, parse, reference, validate_bundle

topology = seal({
    "kind": "parts-topology", "schema_version": 1, "id": "custom.root.v1",
    "nodes": [{"id": "root"}], "sockets": [{"id": "mount", "node": "root"}],
    "slots": [{"id": "ornament", "nodes": ["root"], "sockets": ["mount"],
               "optional": True, "max_count": 3, "empty_policy": "no-piece"}],
})
parse(topology)                         # verifies content; no file IO
reference(topology)                     # id + exact digest
validate_bundle([topology])             # verifies a closed declaration graph
```

`legacy_topology(profile, landmarks=...)`는 명시적인 legacy profile adapter다. 기존 `layers.PROFILES`와 landmark validator를 유지하고 이름들을 root socket으로 옮긴다. 신체 분할이나 최종 raster의 socket 좌표를 추측하지 않는다.

`assembly.json`은 `kind: parts-workspace`, version 1인 사람 편집용 index다. `documents`는 ID → workspace 상대 JSON 경로이며, 선택 rig/catalog/motion/appearance의 정확한 reference와 requested states를 갖는다. 이 mutable index 자체에는 content digest가 없다. index가 가리키는 각 선언은 digest 검증을 통과해야 한다. PR02의 frozen motion은 content-addressed 폴더에 게시하며, 이미지 경로는 해당 `motion.json` 부모 기준이다. 초기 profile/catalog 파일은 digest가 있는 편집 초안이다.

workspace reader는 root 바깥의 path/symlink, duplicate JSON field, 비정규 파일, 4 MiB를 넘는 JSON, 64 level을 넘는 nesting과 nonfinite JSON을 거절한다. 이미지는 읽지 않는다. `parts-init`은 staging에서 먼저 검증한 뒤 새/빈 디렉터리에 게시하며, 기존 run 또는 nonempty 디렉터리 내용을 교체하지 않는다. 두 CLI는 `_modules.py`의 spec domain과 일반 console entrypoint에 등록돼 있고, preset JSON 다섯 개는 wheel에 포함된다.
