# 통과 포즈 재작업과 명시적 검수 기준

> Owns: 시각 재작업 지시, 생성 작업 상태·실패 보존, 검사 기준 설명, 슬롯/body 인터페이스 초안 · Index: [docs/README.md](../README.md)

## 화면 사용

아래 재작업 버튼은 **새 이미지 생성** 경로다. 기존 원본을 편집할 때는 [원본 수정 후보와 zmap 빌드](14-body-zmap-source-edit.md)의 **프로그램으로 원본 수정**을 사용한다. 기본 실제 빌드는 마스크 없이 zmap이며, `visibility: mask`를 명시한 경우만 가림 마스크를 사용한다.

`parts-review-serve --assembly library/assembly.json --port 8776`을 실행하고 기본 주소를 연다. 기본 주소는 편집 가능하며 `?readonly=1`은 읽기 전용이다. 작업 API의 세션 토큰 검증은 유지하며 다른 Host·Origin을 거부한다.

1. 포즈를 선택한다. 검사 통과·시각 승인 포즈도 선택 가능하다.
2. **재작업 지시**에 변경할 형태·위치·디자인을 구체적으로 적는다.
3. **이 포즈 새 이미지 생성**을 누른다. 여러 대상은 각 포즈 체크박스 → **표시한 포즈 새 이미지 생성**을 사용한다. 이때 검사 필터 밖에 표시해 둔 포즈도 포함된다.
4. 요청 저장 → 참조 시트 → 독립 파츠 생성 → 분리 → 전체 검사 → 빌드가 순서대로 실행된다. 생성과 분리는 동일한 batch ID를 사용한다. 지시는 실제 생성 프롬프트와 고정 기준 묶음에 남는다.
5. 새 후보를 이전 후보와 비교한다. 기계 검사 오류를 해결하고 현재 빌드 위에서 시각 검수 후 승인한다.

대상 포즈의 시각 승인은 요청 시 초기화한다. raster의 다른 포즈 판정은 유지한다. held 파츠는 공유 그림이므로 모든 포즈를 초기화하고 설정된 reference pose 한 장을 다시 그린다. 새 후보가 나오기 전까지 현재 선택과 원본을 보존한다. 이전 후보로 바꿀 경우 새 선택에 대해 검사·빌드·시각 검수를 수행한다.

공급자 실패 시 분리 이후 단계는 실행하지 않는다. 실패와 입력 지시는 남고 사용자가 명시적으로 재시도한다. 무한 자동 재생성은 없다. 작업 기록은 `review-jobs/<id>.json`에 저장하며 새로고침·서버 재시작 후 확인할 수 있다. 실행 중 서버가 재시작되면 중단으로 표시하고 자동으로 공급자를 호출하지 않는다. 완료는 품질 통과를 의미하지 않는다.

CLI에서 요청만 저장하려면 다음을 사용한다. 이후 기존 sheet/draw/slice/check/build 명령으로 같은 흐름을 수행한다.

```bash
sprite-gen parts-part-rework --assembly library/assembly.json \
  --part demo.upper.red --poses walk.02 --note '주머니를 한 개로 정리하고 소매는 손목까지만 유지'
```

## 검사 통과의 범위

| 항목 | 현재 기준 | 판정 의미 |
|---|---|---|
| required / protected / allowed | 후보 생성 때 고정된 지도에 대해 위반 0 native px | 위반은 오류. 지도 없는 항목은 미설정 |
| 작은 구멍 / 잔조각 | 12px 이하 내부 구멍, 4px 미만 잔조각 | 경고. held의 작은 장식은 참고 |
| 팔레트 | 지정 팔레트와 불일치 0px | 경고. 팔레트 없으면 미설정 |
| 인접 포즈 면적 | 인접 면적 중간값 대비 변화 45% | 경험적 경고이며 해부학·디자인 검사 아님 |
| 고정 영역 일관성 | 명시된 socket-relative region과 reference pose, 픽셀 변화 허용값 | authored region만 raw `part.png` 비교. 미지정은 미설정, 참조 불가면 미검사 경고 |
| 시각 품질·손발 접합·가림·디자인 | 사람의 판정 | 기계 검사로 자동 승인하지 않음 |

이전 후보는 기본 단독 검사 결과를 보여 준다. 인접 포즈 전체 검사는 후보 선택 후 **전체 검사**로 확인한다. 수치 경고를 실제 아트 합격으로 해석하지 않는다. `qa.stable_regions`를 변경하면 기존 검사·시각 승인을 초기화한다. 후보의 고정 인터페이스를 현재 파일로 바꾸어 과거 오류를 숨기지 않는다.

```json
{
  "qa": {
    "stable_regions": [{
      "id": "chest-pattern", "socket": "torso", "bounds": [-3, -4, 3, 0],
      "reference_pose": "walk.02", "max_alpha_pixels": 0,
      "max_rgb_pixels": 0, "rgb_threshold": 24
    }]
  }
}
```

위 예시는 엄격한 차이 검출 규칙이다. 회전하는 가슴·소매를 고정 영역으로 선언하면 정상 변형도 경고할 수 있다. 실제 디자인에서 고정돼야 하는 영역과 허용량을 정한 뒤 활성화해야 한다. `qa`는 생성 기준 묶음에도 보존된다.

## 슬롯/body 인터페이스 확장

`parts-part-interfaces`는 RGB 색상 추측 대신 명시된 body segment 소유권과 검증된 socket 상대 사각형에서 포즈별 지도를 만든다. 규칙은 합집합이고 각 규칙의 node/body/bounds 조건은 교집합이다. `pad`는 native px 단위 정방형 확장이다. body·rig revision과 slot이 다른 프로필은 거부한다. body가 바뀌면 동일한 작성 방식으로 새 revision의 node/socket/bounds를 설정한다.

```bash
sprite-gen parts-part-interfaces --assembly library/assembly.json \
  --part demo.upper.red --profile upper.body-profile.json --out upper.proposed-config.json
# 지도를 검토한 뒤 새 생산 설정에 반영:
sprite-gen parts-part-config --assembly library/assembly.json \
  --part demo.upper.red --config upper.proposed-config.json
```

프로필은 `kind: parts-interface-profile`, `schema_version: 1`, `id`, `slot`, 정확한 `rig`·`body` 참조와 `rules`를 갖는다. `rules`의 required/protected/allowed/occluded 각각은 `{nodes, socket, bounds, body_only, pad}` 목록이다. node 조건에는 실제 해당 모션의 body segment PNG가 필요하다. 양발 socket을 별도로 선언하면 신발 지도를 각 발 위치에 맞춰 작성할 수 있다. 파츠 자체는 전체 캔버스 프레임별 이미지이고 런타임 배치는 기존 공통 모션과 레이어 resolver를 사용한다.

명령은 해시가 있는 불변 지도를 게시하고 **제안 설정**을 저장한다. 기존 생산 설정을 자동 활성화하지 않는다. Mina 상의 14포즈 지도를 `assets/parts-first-result-20261009/interface-profiles/`에 생성했으며, 새 지도·고정 영역 정책은 초안이다. 현재 검수 기준을 완화해 기존 아트를 통과시키지 않았다. 이 단계는 규칙 작성 방식의 확장이고, 다른 체형의 아트 제작·승인을 완료한 것은 아니다.

## 실제 실행 증거 (2026-10-10)

기본 검수 주소 8776에서 기계 검사 통과였던 `demo.upper.red/walk.02`를 선택해 주머니·소매 수정 지시를 입력하고 재작업을 시작했다. 단일 공급자 호출 후 sheet/draw/slice/check/build가 완료됐으며 작업 `0b149b624d458dfc`, batch `b2beb79dbdffa`로 저장됐다. 새 독립 후드 후보는 `409eba8943…`이고 이전 두 후보도 남았다. 새 그림의 허용 영역 밖 11px는 오류로 표시됐고 승인하지 않았다. 후드 오류 포즈는 5, 청록은 7이며 시각 승인 0이다.

공급자 실패·명시적 재시도는 공급자 모형을 쓰는 통합 테스트로 확인했다. 실제 실패 공급자를 반복 호출해 비용을 발생시키지는 않았다. 기존 body 14샘플과 shared clock 28회 교체 증거를 갱신했다. 런타임 조합별 클립을 생성하지 않는다.

관련 회귀 테스트 103개가 통과했다. 단일·다중 통과 포즈 재작업, held 기준 포즈, 공급자 실패·재시도, 중단 기록 복구, 승인 무효화, 실제 양발 socket 지도, CLI taxonomy·release·stability를 포함한다. UI에서도 통과 포즈 두 개를 표시해 일괄 재작업 버튼이 활성화되는 것을 확인했다.
