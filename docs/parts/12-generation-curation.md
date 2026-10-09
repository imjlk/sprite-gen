# 생성 입력을 기준으로 하는 파츠 검수·큐레이션

> Owns: 생성 전 입력 고정, 독립 파츠 검수 화면, 후보 비교·선택, 포즈 단위 재작업 · Index: [docs/README.md](../README.md)

2026-10-10 기준으로 제작 도구의 중심을 **프로그램이 만든 생성 기준 → 독립 파츠 생성 → 후보 검수·큐레이션 → 레이어 빌드**로 연결했다. body·공통 모션은 다시 생성하지 않는다. 생성 기준도 캐릭터나 옷을 새로 그리는 AI 호출이 아니라 기존 동결된 body/motion, authored 인터페이스, 배치 규격을 묶는 결정적인 작업이다.

## 생성 전에 고정하는 입력

`parts-part-sheet`는 기존 포즈 시트 외에 `productions/<part>/references/inputs/<digest>/reference.json`을 게시하고 결과 JSON의 `reference`로 가리킨다.

| 입력 | 묶음에 남기는 것 |
|---|---|
| 기준 body / rig / motion | revision 참조, 캔버스·스타일, rig·topology·motion JSON 사본 |
| 대상 포즈 | clip / sample / pose ID, socket 위치, pivot, 생성 당시 전체 캔버스 reference PNG |
| 인터페이스 | required / protected / allowed / occluded 지도와 hash, 지도 시각화 PNG |
| 페이지 | 실제 공급자에게 주는 포즈 시트·guide 사본, 결과 그림 경로 |
| 디자인 / 지시문 | 디자인 이미지와 실제 프롬프트 사본, 원래 참조 파일 경로 |
| 출력 처리 | cut·sheet·piece 규격, 팔레트·against 파일 사본 |

파일별 SHA와 묶음 digest를 검증한다. 공급자는 원래의 가변 파일 대신 묶음의 시트·디자인·프롬프트를 읽는다. slice도 같은 guide·cut 규격·검사 지도를 읽고 후보에 `generation_reference`를 남긴다. 원본 참조 이미지나 설정을 나중에 바꿔도 이전 후보의 판정 기준은 바뀌지 않는다. 고정 사본 자체가 달라졌다면 작업을 중단한다. body/rig/motion revision이 바뀐 참조로 slice하려는 경우도 거부한다.

이 묶음은 생성 공급자가 읽을 참조 입력이다. 실행용 avatar export와는 다르며 motion JSON에 이름이 나온 모든 클립의 PNG를 포함하는 엔진 프로젝트는 아니다. 대상 포즈의 참조 PNG와 생성에 필요한 자산을 담는다. 인터페이스가 설정되지 않은 경우 없다고 그대로 기록한다. 도구가 손목·목둘레 같은 작가의 의도를 추측해 승인하지 않는다.

## 화면에서 구분하는 그림

- **몸 위 레이어:** 현재 선택을 반영한 빌드만 실제 공통 레이어 순서로 렌더한다. 후보를 바꿔 빌드가 오래됐으면 참조 위 배치 미리보기로 표시한다.
- **독립 원본 파츠:** `part.png` 자체이며 몸과 합성하지 않는다. 가림·변환 전 후보의 파츠 픽셀을 확인한다.
- **가림 적용 파츠:** `render_part.png`, 없으면 원래 파츠다.
- **생성 참조 / 원본 칸:** 생성 당시 `under.png`와 `drawn.png`다. 공급자의 원본 페이지와 출처 hash도 검사 패널에 남는다.
- **검사 영역 / 오류 위치:** 후보에 고정된 지도와 실제 미덮임·침범 위치다. 오래된 후보에 지도가 없으면 없는 상태를 보여 준다.

그림은 모두 같은 캔버스와 배율로 표시한다. 작은 화면에서는 선택 포즈의 참조/파츠 비교를 먼저 배치한다. 포즈를 눌러 검사 패널을 열고, 이전 후보를 비교한다. 비교만으로 현재 파츠가 바뀌거나 승인되지는 않는다.

## 후보 선택과 재작업

```bash
sprite-gen parts-part-select --assembly library/assembly.json \
  --part demo.upper.red --pose walk.06 \
  --candidate productions/demo.upper.red/candidates/walk.06/CANDIDATE_DIGEST
sprite-gen parts-part-check --assembly library/assembly.json --part demo.upper.red
sprite-gen parts-part-build --assembly library/assembly.json --part demo.upper.red
```

이미 등록된 해당 part/track/pose의 후보만 선택할 수 있다. 다른 포즈·다른 모션의 후보, 변경된 후보 파일은 거부한다. 같은 후보를 다시 선택하면 판정을 유지하는 no-op이며, 다른 후보로 바꾸면 승인·검사 상태를 초기화한다. 다른 raster 포즈의 승인은 유지한다. held 그림은 여러 포즈에서 공유하므로 모두 초기화한다. release는 현재 production의 선택·승인까지 확인해 과거 승인 문서로 우회하지 못한다.

UI의 **이 포즈 재작업 시트**는 `parts-part-sheet --poses`에 연결된다. 최신 시트를 생성하고 분리·검사·빌드한 다음 사람의 시각 판정을 받는다. 실패·반려·누락 포즈 전체 시트도 기존 경로로 사용할 수 있다. 공급자 호출을 무제한 재시도하거나 숫자 검사를 사람의 승인으로 바꾸지 않는다.

`/api/reference/<part>/<batch>.zip`은 고정 입력 묶음을 반환한다. `/api/candidates/<part>/<pose>`는 후보별 원본·배치·검사·생성 출처를 보여 준다. 쓰기는 기존 세션 토큰을 요구한다. 새로운 후보는 별도 pack 형식 없이 기존 레이어 엔진과 export에 연결된다.

## 실제 확인 결과

첫 데모의 `demo.upper.teal / walk.01`, `demo.upper.red / walk.06`를 새 묶음으로 다시 생성했다. 두 그림 모두 옷만 출력했다. 현재 청록의 미덮임은 3 + 허용 영역 밖 6 px에서 미덮임 2 px로, 후드는 미덮임 3 + 허용 영역 밖 55 px에서 허용 영역 밖 2 px로 줄었다. **여전히 error이며 승인하지 않았다.** 현재 전체 오류 포즈 수는 청록 7 / 후드 4다.

새 후드 후보와 이전 후보를 브라우저에서 비교·선택했다가 복원했다. 선택 변경 시 `빌드 필요`, `unchecked`, `unverified`가 표시됐고 새 후보를 복원하고 검사한 뒤 최신 빌드와 오류 상태가 다시 표시됐다. 승인·반려 조작은 수행하지 않았다. body와 motion digest는 유지했고 14프레임 원본 body 일치 및 28회 같은 시각 교체를 재확인했다.

현재 확인용 검수는 `http://127.0.0.1:8778/`, 갱신된 레이어 재생은 `http://127.0.0.1:8780/`다. 기존 검수 주소 8776도 새 화면으로 갱신했다. 두 서버의 쓰기 토큰은 서로 다르다. artifact는 기존 `assets/parts-first-result-20261009/`에 보존했다. 새 생성 기준 ZIP은 검수 화면에서 내려받는다. 구버전 후보에는 생성 전 입력 묶음이 없다는 안내가 붙는다.

관련 회귀 테스트 116개가 통과했다. 이후 ZIP 출력의 결정성을 확인하고, production·release·review 검사 40개를 다시 통과했다. 별도 `curation-result.json`에 새 후보와 검사 출처를 기록했다. 기존 checkout의 미커밋 작업은 수정하지 않았다.

## 아직 충족하지 않은 범위

이제 독립 파츠 제작을 위한 입력 고정·후보 비교·국소 재작업의 기본 흐름은 연결됐다. 다만 인터페이스 자동 작성은 Mina 예제의 규칙이며 모든 체형·모든 파츠에 일반화한 것은 아니다. 소매·신발의 디자인 일관성, 프레임 간 형태 변화, 다른 체형 binding, 런타임 조합의 실제 가림까지 자동으로 승인하는 기능도 아니다. 다음 단계는 슬롯별 입력 규격과 시간축 검수를 확장하고, 독립 파츠 생성 성공률을 같은 조건에서 측정하는 것이다.
