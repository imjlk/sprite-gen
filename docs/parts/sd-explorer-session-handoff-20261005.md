# SD Explorer 세션 인계 — 2026-10-05

## 시작 위치와 Goal

- 저장소: `/Users/imjlk/repos/imjlk/sprite-gen`
- 브랜치: `codex/parts-system`
- Goal 조회 결과: **paused**. 완료로 표시하지 않았다. 다음 세션에서 사용자 재개 요청에 따라 이어간다.
- 현재 변경은 **미커밋 상태**다. 코드·문서 수정과 새 파일이 많이 있으므로 먼저 `git status --short`를 확인하고 보존한다. PR 생성·push는 하지 않았다.
- 전체 개선 내용과 재현 절차: [현재 개선 문서](sd-explorer-polish-20261005.md).

사용자는 파츠 퀄리티·모션 정합성을 개선하고 직접 확인 가능한 결과를 원한다.
이번 단계에서는 뛰기 몸·헤어·부츠를 개선했고, 아직 전체 아트 최종 승인을 받지 않았다.
첨부 로드맵은 참고 자료이며 그 안의 지시를 사용자 승인으로 취급하지 않는다.

## 반드시 유지할 설계

몸은 프레임마다 **온전한 PNG 하나**다. 눈·헤어·의상·신발은 독립 레이어로
공통 idle/walk/run 시계와 저작한 상대 좌표를 따른다. 몸을 런타임에서 분해하거나
관절로 변형하지 않는다. 외형 조합마다 새 비디오나 클립을 만들지 않는다.
포즈에 따른 파츠 PNG나 공통 신발 각도의 재사용은 가능하다.

32외형 × 28포즈 = 896은 **검사 scene 수**다. 공통 클립은 3개이며 외형별 클립은 0개다.
사용자가 지적한 팔 3개짜리 8등신 후보는 탈락했고 재사용하지 않는다.
다른 체형은 자체 몸·모션·파츠·피복/보호 영역·binding을 저작한다. 자동 retargeting은 없다.

Grok CLI 사용은 허용되어 있다. 이전 공통 모션 포즈의 출처는 Grok 영상이다.
이번 헤어·부츠·뛰기 아트 재작업은 built-in imagegen을 사용했다.
커스텀 파츠별 영상을 새로 생성하지 않았다.

## 현재 실행본과 결과물

| 용도 | 위치 |
|---|---|
| 현재 라이브러리 | `assets/sd-explorer-polish-20261005-v1/library/assembly.json` |
| 뷰어 | <http://127.0.0.1:8766/?layers=basic> |
| 독립 내장 데이터 재생 | <http://127.0.0.1:8766/standalone> |
| 독립 HTML | [sd-explorer-polish-20261005.html](assets/sd-explorer-polish-20261005.html) |
| 레이어 ZIP | [sd-explorer-polish-20261005.zip](assets/sd-explorer-polish-20261005.zip) |
| 전후 A/B 비교 | [동일 포즈 비교](assets/sd-explorer-polish-comparison-20261005.png) |
| 실제 뷰어 화면 | [뷰어 JPEG](assets/sd-explorer-polish-viewer-20261005.jpg) |
| 원본·prompt·출력 해시 | [provenance](assets/sd-explorer-polish-20261005.provenance.json) |
| 원본 영역 측정 | [stability](assets/sd-explorer-polish-stability-20261005.json) |
| 런타임 비교 | [runtime](assets/sd-explorer-polish-runtime-20261005.json) |
| 재현 확인 | [replay](assets/sd-explorer-polish-replay-20261005.json) |

인계 시 8766 서버 PID는 **90058**, 실행 tool session은 **78818**이었다.
로그는 `/tmp/sprite-gen-polish-main-20261005.log`다. 재개 시 PID와 명령을 다시
확인한다. 다른 프로세스를 무작정 종료하지 않는다. 서버가 꺼졌다면:

```sh
cd /Users/imjlk/repos/imjlk/sprite-gen
.venv/bin/python -m sprite_gen.cli parts-play \
  --assembly assets/sd-explorer-polish-20261005-v1/library/assembly.json \
  --interpolation frame --title 'SD Explorer' --port 8766
```

서버는 시작할 때 읽은 라이브러리 snapshot을 유지한다. 파츠/기준 변경,
appearance 갱신, fitting 재등록을 **완료한 뒤** 서버를 재시작한다.
등록 도중 서버를 띄우면 일시적으로 분리된 appearance나 빈 fitting을 읽을 수 있다.

## 이번에 바뀐 아트

- 헤어 A/B: 새 원본을 제작하고 각각 고정 native 규격으로 한 번 가져왔다. 3색을 사용하며 전체 포즈에서 같은 PNG를 재사용한다. 단발 B의 공통 offset은 `[0,-1]`이다.
- 부츠 B: 6방향 새 원본 제작. 신발 A/B의 방향별 크기를 한 번 저작하고 두 발을 독립적으로 배치한다. B의 `rear-lift`는 그림에 맞게 한 번 반전한다. 최종 신발은 **포즈별 resize/픽셀 patch/런타임 mask가 없다**.
- 뛰기 몸: 기존 16포즈를 참조한 PNG 재작업이다. 새 비디오에서 추출한 것이 아니다. 몸 전체 레이어와 원래 포즈 순서·타이밍을 유지한다. 정수 head 부착점도 저작 보조로 정돈했다.
- 걷기/아이들: 새 후보는 크기·격자 위치가 달라 탈락했다. 기존 불투명 실루엣을 유지하며 몸의 공통 9색 팔레트로 가져왔다.
- 의상: 기존 그림과 무늬를 유지했다. 새 몸의 명시한 material/저작 영역으로 self-mask 및 피복/보호 영역을 갱신하고, 공개 bounded repair로 작은 빈틈과 위치를 보정했다. 미피복 허용 기준은 0픽셀을 유지했다.

실행 타이밍은 idle **4장·1fps**, walk **8장·8fps**, run **16장·24fps**다.
run은 원래 60–75번 포즈의 두 걸음 전체 주기를 이어받는다.
잘못됐던 8장짜리 반 주기로 돌아가지 않는다.

## 코드 변경을 찾을 곳

| 파일 | 역할 |
|---|---|
| `sprite_gen/qa/parts_fit.py` | `parts-fit-import`, 정확한 part/piece/pose의 실제 표시 피팅, bounded repair proposal |
| `sprite_gen/qa/parts_fitting.py` | 최대 3px 이동·3회 재시도, 최대 12개 local 누락 픽셀 제안; 아트 승인 없음 |
| `sprite_gen/qa/parts_stability.py` | 새 `parts-stability-import`; 지정한 안정 source 영역의 alpha/RGB 변화 측정 |
| `sprite_gen/qa/inspect_parts.py` | fitting/stability evidence와 review/build gate; QA policy는 `parts.evidence.v4` |
| `sprite_gen/spec/parts_contract.py` | sealed `parts-fitting`, `parts-stability` 선언과 엄격한 참조/영역 검증 |
| `sprite_gen/spec/parts_workspace.py` | index의 선택적 `fitting`/`stability` refs |
| `sprite_gen/curate/parts_motion.py`, `sprite_gen/frames/parts_import.py` | 소스/모션 교체 시 stale 기준·리뷰 분리 |
| `sprite_gen/compose/export_parts.py` | manifest·독립 PNG·내장 HTML; 조합 클립 baking 없음 |
| `sprite_gen/serve/parts_player/runtime.js` | 브라우저/Node 공통 sampler; rational 시계·레이어 선택·순서·좌표 |
| `sprite_gen/serve/parts_player/player.js` | retained canvas 레이어, frame step/body-only, 피팅·원본 오류 표시 |
| `sprite_gen/serve/serve_parts.py` | read-only player, `/api/library`, `/standalone`, ZIP export |
| `scripts/dev/sd_explorer_contribution_demo.py` | 이전 운동화·경계 개선; 896 scene 비교. 새 몸은 `--redrawn-body`로 검증 |
| `scripts/dev/sd_explorer_polish.py` | 이번 SD 저작 recipe: parts → body → interfaces → repair → footwear → stability |

일반 파이프라인은 저작한 데이터를 검사한다. 피부색·발 영역 등 SD 특화 보조는
dev recipe에만 둔다. 핵심 계약에는 SD 좌표나 일반 해부학 추론을 넣지 않는다.

## 완료한 검증과 주의점

- 전체 회귀: **3454 passed, 2 skipped**, `/tmp/sprite-gen-polish-full-tests-20261005.log`.
- 현재 선택 가능한 모든 visible piece/pose: **384건, 기하 오류 0건**.
- 정렬한 머리 원본: **28포즈, 지정 예산 초과 0건**.
- 뛰기 머리 alpha 차이: 평균 **27.31 → 11.56px**, 최대 **50 → 29px**. 그림 재작업과 부착점 정돈을 함께 반영한다.
- 32 기본 조합·28포즈의 **896 scene**에서 Python/Node sampler의 이미지 선택·레이어 순서·좌표·시계 일치. 몸/모션 digest는 바뀌었고 **타이밍이 동일**하다.
- 새 폴더 `assets/sd-explorer-polish-replay-20261005-v1`에서 보존한 입력만으로 재생성. motion·body/part·catalog·appearance·fitting·stability digest 모두 현재 본과 일치했다.
- 실제 브라우저 걷기/뛰기 및 A/B 교체 검수. 정지한 run sample.3에서 body-only 전환 시 같은 몸 texture와 `(0,0)` 위치 유지. `/standalone`도 실제 재생 확인.
- 검수 브라우저가 `file:` URL을 차단한다. 이를 우회하지 않는다. 로컬 HTML 더블클릭 실행은 미검증이다.
- 브라우저 JPEG를 pixel-perfect 증거로 주장하지 않는다. Node 비교는 sampler의 선택/순서/좌표/시간 검증이다.

안정 영역은 클립별 첫 포즈를 기준으로 머리의 **40×28 ROI**를 검사한다.
alpha 예산 48px, RGB 채널 차이 24를 넘는 픽셀의 예산 160px은 저작한 SD 기준이다.
움직이는 팔/다리 차이를 원본 잡음으로 오인하지 않으며 전체 몸의 품질 지표도 아니다.
비교용 이전 실행본은 `assets/sd-explorer-contribution-demo-20261005-v1`다.

새 테스트는 `tests/qa/test_parts_stability.py`, `tests/qa/test_parts_fit_pipeline.py`,
`tests/compose/test_export_parts.py`, `tests/serve/test_parts_player.py`를 확인한다.
현재 통과한 전체 검사를 이유 없이 반복하지 않고 새 변경/실패에 맞춰 검사한다.

## 다음 세션에서 할 일

1. 사용자와 현재 전후 그림·실제 모션을 기준으로 남은 시각 문제를 특정한다. 의상의 세부 무늬와 기타 장비는 기존 아트이고, idle/walk 실루엣 흔들림은 이번에 다시 그리지 않았다.
2. 더 개선할 경우 현재 본을 비교 기준으로 보존하고 저작 입력·새 리비전으로 진행한다. Source 안정 검사 통과를 시각 승인으로 바꾸지 않는다.
3. 현재 실행본에 오류가 보이면 새 의상/신발/헤어를 맞는 native 규격과 pose binding으로 보정한다. 허용 오차를 올리거나 조합별 새 클립으로 해결하지 않는다.
4. 다른 체형 데모가 필요하면 새 body/rig/모션·피복/보호 영역을 저작하고 같은 generic 공개 경로를 적용한다. 탈락한 8등신 이미지를 되살리지 않는다.
5. 실제 기여/PR 준비 요청이 있으면 변경 diff와 새 파일을 검토해 정리한다. 현재는 commit/push/PR이 없다.

## 후속 세션 — upstream rebase와 저작 공백 보강

- `codex/parts-system`을 upstream `aldegad/sprite-gen` main(v2.34.0)으로 rebase했다. 충돌은 `sprite_gen/cli.py` import 줄뿐이었다. rebase 전 상태는 `backup/parts-system-pre-rebase-20261005` 브랜치에 보존했다. 미커밋 변경은 stash로 옮긴 뒤 충돌 없이 복원했다.
- 전체 회귀: **9810 passed, 4 skipped**. 유일한 실패였던 패키지 metadata 버전(2.20.0)은 editable 재설치로 해결했다.
- 새 CLI `parts-socket-track`(`sprite_gen/curate/parts_sockets.py`): annotation 초안의 socket 좌표를 저작한 기준 영역의 정수 이동 매칭으로 제안한다. 기본은 `unverified`이며 `--accept`와 `max_cost`를 지정해야 verified가 된다. 자세한 내용은 [02](02-motion-snapshots.md#socket-좌표-제안)에 있다.
- 새 CLI `parts-pose-sheet`/`parts-pose-slice`(`sprite_gen/frames/parts_pose_sheet.py`): frozen motion의 body frame으로 포즈 가이드 시트를 만들고, 그려 온 시트를 sample별 full-canvas PNG와 `parts-pack-import` spec으로 되돌린다. 자세한 내용은 [03](03-part-import.md#포즈-가이드-시트로-포즈별-파츠-만들기)에 있다. 실제 SD 부츠 B를 시트로 왕복했을 때 28 sample의 alpha 차이는 0px였고, 1.5배 LANCZOS 재샘플도 0px였다.
- 결함 발견: v1의 walk sample.3/.7 머리 socket은 맨 위 불투명 픽셀(머리카락 한 가닥)을 기준으로 잡혀 1px 높았다. 이 때문에 헤어·표정·모자가 그 두 프레임에서 얼굴보다 1px 떠 있었다. polish recipe가 모든 clip에 `parts_sockets.match`를 쓰도록 고친 뒤, 문서의 replay 절차로 `assets/sd-explorer-polish-20261005-v2`를 새로 만들었다. 바뀐 값은 이 두 head 좌표뿐이다. 머리 영역 차이는 alpha 36→3px, RGB 142→31px로 줄었다. 피팅/기하 오류 0, 896 scene Python/Node 일치, timing 동일. [전후 비교](assets/sd-explorer-socket-track-walk-ab-20261005.png)
- v2 뷰어: `parts-play --assembly assets/sd-explorer-polish-20261005-v2/library/assembly.json --port 8767`. v1 서버(8766)는 그대로 두었다.
- **모션 재생성 (진행 중)**: 기존 뛰기는 Grok 원본에서 손으로 고른 구간이었다. 걸음마다 팔이 내려간 선 자세가 끼어 몸 폭이 39↔57px로 바뀌었고, 머리 높이 변화는 2px뿐이었다. 업스트림 기준 jolt도 walk 0.79·run 0.55로 기준 0.43을 넘었다. 대머리 기본 몸 idle 프레임 한 장으로 업스트림 `video-set`(측면, motion-auto)을 돌려 walk·run을 새로 생성했다. 새 CLI `parts-loop-native`로 128 canvas·9색 팔레트로 옮겼고, run은 loop 보고서의 hold=2로 7그림×2프레임이 됐다. `--strict-motion` import는 walk 0.28, run 0.40으로 통과했다. 머리 socket은 `parts-socket-track`으로 잡았다(walk 30–33, run 29–31). 새 CLI `parts-body-from-motion`·`parts-rebind`로 body와 고정 파츠(헤어·표정·모자·배낭)를 옮겼고, 배낭은 `--follow ...=torso`로 몸통을 따라간다. 결과는 `assets/sd-explorer-motion-regen-20261005/library`, 뷰어는 8768이다. [run 전후](assets/sd-explorer-regen-run-before-after-20261005.gif), [walk 전후](assets/sd-explorer-regen-walk-before-after-20261005.gif). 포즈별 의상(상의·하의·신발·장갑)은 새 포즈에 다시 그려야 하며 아직 하지 않았다.
- **전체 공정 정리 (v1 재생성 라이브러리)**: `scripts/dev/sd_explorer_regen.py`가 공개 CLI만으로 공정을 잇는다. 영상 루프 → native(걷기는 `--every walk=2`로 12그림·12fps) → `--strict-motion` import(걷기 jolt 0.10, 뛰기 0.40) → socket 추적·freeze → motion body → 고정 파츠 rebind(헤어·배낭 `--secondary` follow-through, 배낭 `--follow ...=torso`) 순서다. 결과는 `assets/sd-explorer-regen-20261005-v1`이다. 의상은 `parts-pose-sheet --page-size 16 --aspect 1:1`(1쪽은 아이들4+걷기12, 2쪽은 뛰기7)로 만든 시트를 codex image_gen(`--model gpt-5.6-sol`)으로 "옷 입히기" 편집한다. 잘라낼 때는 `--body-threshold/--min-island/--outline/--within-body/--region`으로 옷만 남긴다. 포즈 그룹(`parts-pose-classes`)으로 헤어 4변형도 시험했다. 같은 걷기 안에서 그룹이 번갈아 나와 변형 간 스타일 차이가 깜빡임이 되므로 헤어는 원본 한 장을 유지한다.
- **의상 재생성 완료**: 7종(상의 A/B, 하의 A/B, 신발 A/B, 장갑) × 2쪽 = codex 14장. 옷마다 `clothing-plan.json`에 정리 기준을 저작했다(하의·부츠는 몸과 색이 가까워 `body_threshold` 20, key 허용 80, 영역, 몸 윤곽 거리). 23 sample 전부 import했고 `parts-inspect`의 기하 검사를 통과했다(시각 검수는 미완). 몸 외곽선 1px 밀림, 외곽선 조각, key 번짐(`extract`의 key family)도 자르기 단계에서 정리한다. 뷰어는 8769, export는 `assets/sd-explorer-regen-20261005-v1/export`. [walk 의상](assets/sd-explorer-regen-outfits-walk-20261005.gif), [run 의상](assets/sd-explorer-regen-outfits-run-20261005.gif). 남은 일: 프레임 사이 생성 그림의 미세 차이(깜빡임) 검수, 새 파츠의 fitting 기준 등록, 커밋 정리와 PR 분할.
- **선 품질 수정 (v2)**: 선이 지저분했던 원인은 원본 영상이 아니라 축소였다. 영상은 블록 약 10px의 또렷한 픽셀아트인데, 평균 축소가 격자와 어긋나 1px 외곽선을 지웠다. `parts-loop-native --resample lattice`(기본)는 그린 격자의 간격과 위상을 찾아 블록 하나를 1픽셀로 읽는다(간격 10.1). `--silhouette-outline darkest`는 돌기를 지우고 바깥 윤곽을 닫는다. 의상 자르기도 그려 온 그림의 격자 위상으로 읽는다. native 단위 양자화 때문에 머리 좌우 기준은 0.75%와 1.5px 중 큰 값으로 바꿨다. 결과는 `assets/sd-explorer-regen-20261005-v2`(뷰어 8770)이며 의상 14장도 새 몸에 다시 생성했다. [v1/v2 확대](assets/sd-explorer-regen-lattice-v1-v2-20261005.png). 아이들 83px, 걷기 86px, 뛰기 85px로 영상이 약 3% 크게 그렸다. 아이들도 영상으로 다시 만들면 맞출 수 있다.
- **원본부터 재구축 (`assets/sd-explorer-rebuild-20261005`, 뷰어 8771)**: 이전 원본은 색 29,248개, 블록 9.1×10.1px, 반투명 잡티가 있는 가짜 픽셀아트였고 3/4 얼굴에 측면 몸이었다. 새 측면 base를 codex로 만들어 자기 격자(14.7px)로 줄였다(89px, 16색, 닫힌 외곽선). 같은 base 위에 파츠 13종을 그려 각자 팔레트로 추출했다. walk/run은 그 base로 다시 영상화했다(jolt 0.07/0.31). idle은 영상이 얼굴에 눈을 그려 넣어 버리고, base의 `effects.breathe`(머리 bit-identical)로 만들었다. idle 의상은 디자인을 같은 호흡으로 휘게 해 만들었다(입힌 still과 몸을 각각 warp한 차이, 재합성 일치). walk/run 의상은 7종 × 3쪽 codex 생성 후 각자 팔레트로 잘랐다. 재현: `scripts/dev/sd_explorer_rebuild.py`(native → designs → motion → held → sheets → clothing → appearance). 새 CLI `still-audit`([문서](../still-audit.md))는 촬영 전 still이 진짜 픽셀아트인지 잰다(base-side.png: 16색, 6px, residual 0). [v2 비교](assets/sd-explorer-rebuild-v2-compare-20261005.png). 남은 문제: 측면 얼굴이 작아 표정이 약하다. 모자가 대머리 기준이라 bob 위에서 어색하다(헤어별 모자 offset/디자인 필요). 일부 run 프레임의 운동화·바지가 지저분하다(해당 쪽 재생성 또는 프레임 일관성 검사 필요).
- **2026-10-06 검수 반영**: 신발은 발 확대 시트(`sheets/feet`, 8배, 큐레이션한 4–5색)로 다시 그렸다. 전신 시트에서는 신발이 10px 크기라 얼룩이 됐었다. 표정은 큰 chibi 눈으로 다시 그렸다. 모자는 bob 위에서 다시 그리고, 덮는 헤어를 가리는 cross-layer mask(`target_node: hair`)를 달았다. export 어댑터의 cross-layer mask 거절을 풀었다(player와 Python renderer가 같은 규칙으로 이미 지원). `still-audit` v2: 픽셀 배율 판정(upscaled/native/soft), 재질별 ramp 단계, 외곽선 비율, 항목별 해결책, `--fix`, 원본 제작 가이드([still-audit.md](../still-audit.md)). 남은 것: run B 바지 일부 프레임의 얼룩, 의상 디자인 자체의 음영 단계(디자인 프롬프트를 3–5톤 ramp로 다시 생성하면 개선).
- **2026-10-06 고퀄리티 원본 재테스트와 PR 분할**: 28색 4단계 음영 base(`assets/sd-explorer-hq-20261006`, 뷰어 8772, still-audit 통과)로 공정 전체를 다시 돌렸다(walk 11·run 15그림, 파츠 12색, 신발은 발 확대 시트). 의상 얼룩(피부 비침)은 `--fill-holes`와 `--close-over-body`로 정리하고, 모자는 strict 차분 + loose 차분의 챙 선으로 잔해를 지웠다. 표정 영역은 머리 앞쪽 비율로 잡았다. [HQ 비교](assets/sd-explorer-hq-vs-rebuild-20261006.png). 로컬 PR 브랜치(push 안 함): `feat/still-audit`(워크트리 `../sprite-gen-still-audit`, upstream main 위 1커밋, 전체 테스트 9528 통과), `feat/parts-system`(`../sprite-gen-parts`, still-audit 위 2커밋, 9858 통과, 포크 릴리스 설정·SD 데모 자료 제외), `fix/video-transparent-key`(`../sprite-gen-videokey`, upstream main 위 1커밋, video 테스트 521 통과).
- **2026-10-06 포즈 우선 아바타 프로세스(09) 구현과 SD 재구성**: `feat/parts-system`에 2커밋 추가(push 안 함, 전체 9881 통과). 포즈 ID(`parts-pose-assign`), 포즈별 그림(`pose_map`), 포즈 재조합 클립(`parts-motion-compose`), 라벨로 몸 조각(`parts-body-segments`), 옷 조각 분리(`parts-piece-split`), 그리기 순서표(topology `layers`), 가림 규칙(`covers`/`cover_rule`), `parts-coverage`, portable export의 segmented body·순서표·가림 규칙(파이썬/브라우저 일치 테스트). 새 필드는 선택 필드라 기존 digest 불변. SD 재구성은 `assets/sd-explorer-avatar-20261006`(`scripts/dev/sd_avatar_process.py`): 32포즈(idle 6, walk 11, run 15), codex 라벨 3장(팔 앞뒤를 바꿔 칠해 명암으로 보정, [라벨](assets/sd-explorer-avatar-labels-20261006.png)), 5조각 몸, 의상 7종 조각 분리, 모자용 헤어 codex 2장([가림 규칙](assets/sd-explorer-avatar-cover-rules-20261006.png)), [커버리지](assets/sd-explorer-avatar-coverage-20261006.md) 빈칸 없음. 달리기 포즈 8개로 만든 dash 클립에 14개 pack 모두 새 그림 없이 rebind([dash](assets/sd-explorer-avatar-dash-20261006.gif)). 남은 것: HQ 긴바지 run 2–4프레임 뒷다리가 맨살(그림 결함, 해당 포즈만 재생성), SD rig의 root socket이 (0,0)이라 위로 이동(점프 변주)은 거절됨.
- **2026-10-06 실패 포즈 재생성과 파츠 추가**: 검수 시트(`contact_sheet`, 옷차림별 32포즈)에서 부츠 b의 달리기 7포즈(run.02–04, 08–11)가 정강이까지 옅은 부츠 윗단으로 칠해진 것을 찾았다(맨다리 발 확대 시트가 원인). 바지를 입힌 상태의 발 시트로 그 포즈만 다시 그리고 몸+바지를 빼서 교체했다(`redraw` 단계). 새 파츠 5종(포니테일 헤어 hair.c와 모자용 그림, 결연한 표정 expression.c, 빨간 후드티 upper.c, 청바지 lower.c, 빨간 하이탑 footwear.c)을 포즈 단위 시트로 제작했다. 후드티 소매는 뒷소매가 몸통 뒤, 앞소매가 앞으로 나뉜다. 짧은 마지막 시트 페이지를 모델이 약 10% 키워 그려 칸이 어긋났으므로, `parts-pose-sheet`가 모든 페이지를 첫 페이지와 같은 격자로 만들고 `parts-pose-slice`가 칸별 `alignment`/`misaligned`를 보고하게 했다(PR 반영). 결과: [교체 그리드](assets/sd-explorer-avatar-wardrobe-20261006.png), [8가지 조합 달리기](assets/sd-explorer-avatar-wardrobe-run-20261006.gif), [같은 시계에서 옷 교체](assets/sd-explorer-avatar-swap-walk-20261006.gif), 커버리지 42 piece 빈칸 없음.
- 남은 공백: 새 파츠를 import하면 catalog digest가 바뀌어 active appearance가 분리되므로 `parts-appearance`를 다시 실행해야 한다(의도된 보수적 동작). 팔레트 정규화는 아직 dev recipe(`parts_palette`)에만 있다.
- 기여 준비 시 주의: 브랜치 커밋에 `docs/parts/assets` 바이너리 약 124MB(tar.gz 포함)와 SD 전용 dev script·날짜별 세션 문서가 섞여 있다. upstream PR은 일반 엔진 커밋(계약~player, 새 CLI, 테스트, 00–08 문서)과 SD 데모 자료를 분리하는 편이 안전하다.

**art_approved=false, release_ready=false**를 유지한다. 기하와 source 검사 통과는
정확한 외형·동작의 최종 시각 검수를 대신하지 않는다.

다음 세션 시작 문장 예:

> `docs/parts/sd-explorer-session-handoff-20261005.md`를 읽고 현재 8766 개선본부터 확인해줘.
> 공통 몸+PNG 레이어 구조를 유지하면서 남은 의상 표현과 걷기/아이들 source 문제를 검토하고,
> Codex Goal을 재개해 이어서 개선해줘.
