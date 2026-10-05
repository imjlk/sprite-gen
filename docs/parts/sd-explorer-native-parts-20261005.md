# SD 몸·파츠의 공통 픽셀 규격과 신발 재작업, 2026-10-05

이 문서는 native 기준 실행본의 기록이다. 현재 실행본과 이후 의상·운동화
보정은 [레이어 데모 개선](sd-explorer-contribution-demo-20261005.md)을 따른다.

사용자는 전체 주기를 복원한 뒤에도 자글거림과 신발 표현을 반려했다.
몸은 기존 Grok 영상에서 추출했고 파츠는 별도로 생성한 PNG를 축소했다.
파츠용 새 영상 클립을 생성하거나 외형 조합마다 클립을 만드는 방식이 아니다.
몸/파츠의 픽셀 밀도·색 수·외곽선과 포즈별 신발 확대·축소/잘라내기를
제작 단계에서 일치시키지 못한 것이 품질 차이의 원인이었다.

현재 실행 파일은 `assets/sd-explorer-run-cleanup-20261005-v1/library/assembly.json`이다.
공통 몸의 idle 4장/1fps, walk 8장/8fps, run 16장/24fps는 모든 외형이 공유한다.
run은 원본 60–75, 양쪽 스텝의 2/3초 주기다. 몸은 항상 온전한 한 레이어이고,
눈·헤어·상의·하의·신발 PNG를 독립적으로 덮는다.

몸과 파츠는 기존 **128px native 격자와 고정 48색 팔레트**를 공유한다.
기존 extraction의 shared-palette 산출과 exact recolor bake를 사용한다.
원본은 별도로 보존하며, 색만 정리한 리비전의 크기·윤곽·alpha·좌표·시간은
원본과 같다. 몸 색 수는 run 프레임당 1,024–1,293에서 41–45로 감소했다.
**RGB가 달라져 원본 PNG와 바이트가 같지는 않다.** 현재 motion digest는
`c3a5347550a914485961c85b14910d73f9b66a661c5b0d0028f5090ec36bae45`이며
모든 binding을 다시 연결했다. 런타임 색상 필터나 몸 분리는 없다.

신발은 built-in imagegen으로 만든 디자인별 6방향, 총 12개 그림을 한 번만
native 크기에 맞춰 idle/walk/run에서 재사용한다. 포즈마다 신발 크기를
바꾸거나 몸 윤곽으로 신발을 잘라내지 않는다. 명시한 발 좌표 주변의 제한된
제작 보정만 사용한다. 두 발 영역이 겹치면 한 발의 영역에 다른 발의 피부가
포함되므로 **두 신발의 합쳐진 피복**을 검사한다. 각 신발이 다른 발까지 덮도록
맞추던 위치 오류를 제거했다. 실제로 보이지 않는 발은 명시적으로 숨긴다.
허용 영역은 QA 자료이고 런타임 clipping stencil이 아니다.

```sh
.venv/bin/python -m sprite_gen.cli parts-play \
  --assembly assets/sd-explorer-run-cleanup-20261005-v1/library/assembly.json \
  --interpolation frame --port 8766
```

[실행 패키지](assets/sd-explorer-native-parts-20261005.tar.gz),
[생성 prompt·참조](assets/sd-explorer-native-parts-20261005.prompts.json),
[출처·해시·검증 범위](assets/sd-explorer-native-parts-20261005.provenance.json),
[전체 파츠 검사](assets/sd-explorer-native-parts-20261005-fit.json)를 보존한다.
[전후 재생](assets/sd-explorer-native-parts-before-after-20261005.mp4),
[현재 뛰기 BODY/A/B](assets/sd-explorer-native-parts-20261005.mp4),
[걷기 BODY/A/B](assets/sd-explorer-native-parts-20261005-walk.mp4)는 검수용 녹화다.
런타임은 PNG 레이어와 세 공통 timeline을 사용한다.
[실제 뷰어](assets/sd-explorer-native-parts-viewer-20261005.jpg),
[A 전체 포즈](assets/sd-explorer-native-parts-20261005-a.png),
[B 전체 포즈](assets/sd-explorer-native-parts-20261005-b.png)를 함께 확인한다.

896개의 기본 외형/포즈 scene에서 body hash/(0,0)·교체 시점·clipping 0과
원본 body alpha의 동일성을 확인했다. 신발 56개 상태/디자인의 선언 피복·크기·위치·
허용 윤곽 검사는 통과했다. 110개의 보이는 신발 instance는 같은 디자인/방향의
PNG hash와 크기를 재사용한다. 브라우저에서 두 외형의 걷기·뛰기를 재생했고,
같은 run 포즈에서 다섯 파츠를 해제·복원해 body signature/위치/시점이 같았다.

**기하 검사 통과는 신발 그림의 품질 승인이 아니다.** 전체 8slot의 보이는
piece/pose 384건 중 의상 경계 11건에서 1–10픽셀의 미피복이 남는다.
RGB 정리는 원본 영상의 프레임별 윤곽 흔들림을 바꾸지 않는다. 최종 시각 검수와
의상 그림 재작업은 남아 있으며 `art_approved=false`, `release_ready=false`다.
뷰어에도 최종 시각 검수 미승인을 표시한다.

관련 회귀는 **372 passed / 44.08초**다. 새 palette 검사는 source/alpha 보존,
정책 변경 시 cache 분리, legacy off 동작과 잘못된 정책 거부를 포함한다.
source-cycle 누락·중복·역순·속도 변경 검사도 포함한다. JS 문법·Python compile
검사와 archive 모든 member SHA 검증을 통과했다.

[제작 recipe](../../scripts/dev/sd_explorer_matched_layers.py)에 패키지 입력과
`palette-policy.json`을 새 `--base`로 제공하고 이전
[헤어 source checkpoint](assets/sd-explorer-hair-repair-20261005.tar.gz)를 `--source`로
사용한다. `library`가 없는 새 목적지에서 실행한다. 팔레트 정책이 없으면 기존
source RGB를 유지한다. 변환은 [parts_palette](../../sprite_gen/frames/parts_palette.py)의
source+policy cache와 기존 recolor 보고서로 추적한다. 다른 체형은 자체 좌표·
발 인터페이스·그림 크기·팔레트가 필요하며, 이 SD 보정은 자동 해부학/retarget 모델이 아니다.
