# SD 뛰기 전체 주기 수정, 2026-10-05

이 기록은 전체 주기를 바로잡은 첫 checkpoint다. 사용자는 이후 자글거림과
신발 표현을 반려했다. 현재 데모는 [공통 픽셀 규격·신발 재작업](sd-explorer-native-parts-20261005.md)이며,
아래의 신발 그림과 정지 화면은 품질 승인 근거로 사용하지 않는다.

사용자는 앞선 수정본의 실제 뛰기를 다시 반려했다. 원본에서 연속 8프레임을
전체 주기로 잘못 채택했다. 반대쪽 팔 스윙은 다음 8프레임에 있어 한쪽 동작만
반복됐다. 구조 검사와 정지 화면으로 뛰기가 정상화됐다고 보고한 판단을
철회한다. [이전 8프레임 기록](sd-explorer-motion-repair-20261005.md)은 반려된
checkpoint로 남긴다.

원래 Grok run 영상에서 양쪽 스텝이 들어 있는 연속 **60–75, 16프레임**을
선택하고 **원본 24fps, 2/3초**로 재생한다. 시작 구간보다 안정된 뒤쪽 주기를
선택했다. 원본 몸 PNG·좌표·머리 기준점은 그대로이며, 몸 분리나 리깅은 없다.
idle 4장/1fps, walk 8장/8fps는 이전 결과를 유지한다. 세 상태가 모든 파츠
조합에 공통으로 쓰이며 외형 조합마다 완성 모션을 만들지 않는다.

새 run 16포즈 기준으로 상의·하의·신발 A/B, 여섯 시트를 생성했다. 각 포즈에
해당하는 개별 그림을 제작 단계에서 한 번 맞춘다. 눈·헤어·의상은 독립
레이어이며 `--interpolation frame`으로 몸과 파츠를 같은 포즈에서 넘긴다.
가려진 발 위치에 떨어진 신발을 그리지 않도록 신발 레이어에만 명시한 발
영역과 원본 몸 윤곽의 1픽셀 여유를 적용한다. 몸은 수정·마스킹하지 않는다.
기존 걷기의 신발 참조는 이전 시트에 고정하여 새 뛰기 시트와 섞이지 않는다.

```sh
.venv/bin/python -m sprite_gen.cli parts-play \
  --assembly assets/sd-explorer-run-cycle-20261005-v1/library/assembly.json \
  --interpolation frame --port 8766
```

[실행 패키지](assets/sd-explorer-full-run-cycle-20261005.tar.gz),
[생성 prompt](assets/sd-explorer-full-run-cycle-20261005.prompts.json),
[출처·해시·검증 범위](assets/sd-explorer-full-run-cycle-20261005.provenance.json),
[주기 진단](assets/sd-explorer-full-run-cycle-20261005.cycle.json),
[파츠 검사 전체 결과](assets/sd-explorer-full-run-cycle-20261005-fit.json)를 보존한다.
[원본 16포즈](assets/sd-explorer-full-run-cycle-20261005-source.png),
[A 외형](assets/sd-explorer-full-run-cycle-20261005-a.png),
[B 외형](assets/sd-explorer-full-run-cycle-20261005-b.png),
[실제 뷰어](assets/sd-explorer-full-run-cycle-viewer-20261005.jpg)를 함께 검수한다.

[뛰기 재생 영상](assets/sd-explorer-full-run-cycle-20261005.mp4)은 원본 몸,
A 파츠, B 파츠를 같은 시점에서 네 주기 나란히 재생한다.
[걷기 영상](assets/sd-explorer-full-run-cycle-20261005-walk.mp4)도 보존한다.
두 영상은 검수용 녹화이며 런타임 asset은 개별 몸·파츠 PNG와 세 공통 timeline이다.

기본 32외형 × 공통 28포즈의 896 scene에서 원본 몸 hash/(0,0), 파츠 교체
시점, clipping 0을 확인했다. 기존 idle/walk의 몸만/A/B, 총 36개 RGBA 결과는
이전 checkpoint와 같다. 브라우저에서 양쪽 스윙을 재생하고, 같은 run 포즈에서
다섯 파츠를 모두 해제·복원해 몸 texture signature·좌표·sample·slider가
유지됨을 확인했다. A/B·혼합 선택도 검수했다. 새 모션 digest는
`cd6df9e05443f973fc0a9a402f4feb924423e2ff97ae2ae273a16fd9e5b370d4`이다.

주기 검사는 선택한 source sample과 fps가 **직접 검수하여 선언한 전체 주기**와
일치하는지 확인한다. 반주기 누락·중복·역순·묵시적인 감속을 거부하는 7개
회귀 사례를 추가했다. 주기나 발의 정체를 자동 추론하지 않으며, 잘못 선언한
기준 자체의 해부학적 품질은 사람이 확인해야 한다.

전체 8slot의 보이는 piece/pose 384건을 검사했다. **의상·신발 16사례에서
1–13픽셀, 합계 64픽셀의 선언 영역 미피복**이 남는다. 걷기 2사례는 기존
결함이며 나머지는 새 뛰기 아트의 재작업 대상이다. 보정으로 억지로 통과시키거나
아트 승인으로 처리하지 않는다. 전체 동작의 최종 시각 검수도 여전히 필요하다.
`art_approved=false`, `release_ready=false`를 유지한다.

관련 회귀 **369 passed / 43.46초**, JS 문법·Python compile 검사를 통과했다.
이 수량은 위 동작·아트 검수를 대신하지 않는다.
[제작 recipe](../../scripts/dev/sd_explorer_matched_layers.py)는 패키지의
`pose-guide.json`, `pose-layer-calibration.json`, `raw-art`, `raw-run-art`를
새 `--base`에 두고 이전
[헤어 source checkpoint](assets/sd-explorer-hair-repair-20261005.tar.gz)를
`--source`로 받는다. `library`가 없는 새 목적지에서 실행하며 `--review-video`는
ffmpeg로 검수 영상만 만든다. 패키지의 현재 `library`는 바로 실행 가능하다.
