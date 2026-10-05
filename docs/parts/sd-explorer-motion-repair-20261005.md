# SD 레이어 걷기·뛰기 재작업: 반려된 뛰기 checkpoint, 2026-10-05

이 checkpoint의 뛰기는 사용자 검수에서 다시 반려됐다. 원본 2–9의 연속
8프레임을 전체 주기로 잘못 채택했고, 반대쪽 팔 스윙을 포함하는 다음
8프레임이 빠졌다. 아래의 기술 검사·정지 화면·영상은 이 결함을 통과시킨
기록이며 뛰기 품질의 근거로 사용하지 않는다.
현재 데모는 [전체 뛰기 주기 수정](sd-explorer-full-run-cycle-20261005.md)이다.

사용자가 파츠를 붙인 걷기·뛰기를 반려한 뒤의 수정이다. 앞선 구조 검사와
헤어 정지 화면 검사로 동작 품질까지 통과한 것으로 판단하지 않는다.
당시 실행 파일은 `assets/sd-explorer-motion-repair-20261005-v2/library/assembly.json`이다.

이전 구현은 16개의 하의·신발 그림 중 가까운 포즈를 골라, 양 다리/신발이
그려진 한 장을 통째로 이동했다. 상의 한 장도 전체 모션에 사용했다.
몸은 현재 프레임을 유지하는 동안 파츠 위치만 다음 프레임으로 보간했다.
그림의 발·무릎 위치와 현재 몸 포즈가 다른 순간의 튐과 노출을 확인했다.

수정본은 각 몸 포즈에 직접 연결한 상의·하의·신발 그림을 사용한다.
온전한 몸 PNG는 원본과 같은 바이트이며, 눈·헤어·의상을 독립 레이어로 덮는다.
의상 가림은 의상 레이어에만 적용한다. 몸을 자르거나 수정하지 않는다.
양 신발은 제작 단계에서 각각 명시한 발 영역에 맞춘다. 재생 중 가까운 포즈
검색, 파츠 확대/회전/변형, 조합별 완성 모션 생성은 없다.

| 공통 상태 | 선택한 원본 sample | 재생 |
|---|---|---|
| idle | 0, 24, 48, 72 | 4장 / 1fps / 4초 |
| walk | 12, 15, 18, 21, 24, 27, 30, 33 | 한 주기 8장 / 8fps / 1초 |
| run | 2–9 | 반려된 반주기 8장 / 12fps / 2/3초; 반대 스텝 누락 |

원본은 Grok 영상에서 추출한 24fps 몸이다. 걷기는 한 주기를 3프레임마다
선택했고, 뛰기는 연속 8프레임을 두 배 느리게 재생했다. 이 반주기 선택은
후속 수정에서 60–75의 전체 16프레임/원본 24fps로 대체했다. 선택한 몸 픽셀과
좌표는 보존하지만 sample 수·순서·시간을 새로 고정했으므로 **motion digest는
변경**했다. 모든 파츠 binding도 이 새 공통 timeline에 연결했다.

```sh
.venv/bin/python -m sprite_gen.cli parts-play \
  --assembly assets/sd-explorer-motion-repair-20261005-v2/library/assembly.json \
  --interpolation frame --port 8766
```

`--interpolation frame`은 몸과 파츠를 함께 현재 포즈에 유지한다. 다른 프로젝트의
기존 `linear` 기본값과 상대좌표 보간 기능은 유지한다.

[실행 패키지](assets/sd-explorer-motion-repair-20261005.tar.gz),
[원본·생성 prompt](assets/sd-explorer-motion-repair-20261005.prompts.json),
[출처·해시·검증 범위](assets/sd-explorer-motion-repair-20261005.provenance.json),
[검수 화면](assets/sd-explorer-motion-repair-viewer-20261005.jpg)을 보존한다.
[걷기](assets/sd-explorer-motion-repair-walk-20261005.mp4)와
[뛰기](assets/sd-explorer-motion-repair-run-20261005.mp4)는 원래 몸/A/B를 같은
시점에 나란히 재생하는 **검수용 영상**이다. 런타임의 완성 외형 clip이 아니다.

기본 32외형 × 공통 20포즈의 640 scene에서 몸 hash/(0,0), 교체 시점,
의상에만 적용한 mask, canvas clipping 0을 확인했다. 브라우저에서도 걷기·뛰기,
긴바지·부츠, 혼합 선택을 재생했다. 같은 run sample에서 5개 파츠를 모두 해제한
뒤 다시 붙여도 몸 texture, 위치, slider와 sample이 같았다.

전체 설치 파츠 8slot의 보이는 piece/pose 274건을 공통 fitting 검사에 넣었다.
크기·위치·clipping을 공통 검사하고, 상의/하의에는 정확한 포즈별 필수 영역,
팔 보호 영역과 허용 윤곽을 검사한다. 신발도 양 발의 선언 영역을 합쳐 미피복을
검사하고, 헤어에는 선언한 접합 빈틈 영역을 검사한다.
영역이 선언되지 않은 파츠의 해부학적 적합성은 추정하지 않는다.
현재 **탱크탑 5포즈의 1–2픽셀과 신발 3사례의 1–4픽셀, 합계 15픽셀의 미피복**이 남아
`part-fit-evidence.json`의 재작업 대상으로 기록된다. 아트 승인/배포 완료로
처리하지 않는다. 초기에 세 신발이 생긴 생성본은 제외했다. 긴바지는 다리만
보이는 기준으로 다시 생성한 그림이며, 가림 때문에 떨어진 영역은 최종 몸 포즈에
적용한 뒤 검수했다. 첫 긴바지 생성본도 출처 기록에 보존한다.

크기와 위치의 1픽셀 간격 제작 보정도 재측정했으며, 같은 범위의 보정으로
해결되지 않는 노출은 그림 윤곽의 재작업 대상으로 남겼다. 영역 생성 recipe의
색상/y 범위는 이 SD 원본에 한정된 제작 규격이다. 다른 체형은 자체 포즈별
영역과 그림을 제공하며 같은 checker를 사용한다. 자동 해부학·retarget 모델이 아니다.

관련 회귀 **227 passed / 31.12초**, JavaScript 문법 검사를 통과했다.
모션 프레임 보간을 끈 상태의 교체, 포즈 mask의 누락/팔 침범/윤곽 초과와
오래된 layer/non-layer golden을 포함한다. 이 수량은 실제 모션 아트 승인이 아니다.
`art_approved=false`, `release_ready=false`를 유지한다.

[제작 recipe](../../scripts/dev/sd_explorer_matched_layers.py)는 이전 헤어 checkpoint를
`--source`로 받아 원본 포즈를 확인한다. `--base`에는 패키지의 guide/raw-art/calibration
입력을 두고, `library`가 없는 새 목적지를 사용한다. `--review-video`는 ffmpeg가
있을 때 두 검수 영상을 만든다. 패키지의 현재 `library`는 바로 실행 가능하다.
