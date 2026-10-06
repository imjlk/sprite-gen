# 파츠 제작 기록과 파츠 단위 승인

> Owns: 파츠 제작 기록(production), 포즈별 후보·마스크·출처 보존, 포즈별 검사, 코디와 무관한 파츠 승인, 반려 포즈만 다시 그리는 루프, 검수 데스크, 릴리스 관문 · Index: [docs/README.md](../README.md)

[아바타 제작 프로세스](09-avatar-process.md)의 5단계(파츠 제작)를 실제로 돌리는 도구다. 한 파츠를 동결된 모션의 포즈마다 그리고, 그린 그림마다 무엇을 어떻게 잘라 냈는지 보존하고, 포즈별로 검사한다. 그다음 몸 위에서 그 파츠만 보고 승인하거나 반려하고, 반려한 포즈만 다시 그린다. 조합 엔진은 바뀌지 않는다. 결과는 일반 파츠 pack으로 import된다.

```bash
sprite-gen parts-part-init   --assembly lib/assembly.json --config hoodie.json
sprite-gen parts-part-sheet  --assembly lib/assembly.json --part sd.wardrobe.upper.c
sprite-gen parts-part-draw   --assembly lib/assembly.json --part sd.wardrobe.upper.c      # 선택: 이미지 공급자
sprite-gen parts-part-slice  --assembly lib/assembly.json --part sd.wardrobe.upper.c
sprite-gen parts-part-check  --assembly lib/assembly.json --part sd.wardrobe.upper.c
sprite-gen parts-part-build  --assembly lib/assembly.json --part sd.wardrobe.upper.c
sprite-gen parts-review-serve --assembly lib/assembly.json                                 # 검수 데스크
sprite-gen parts-part-status --assembly lib/assembly.json
```

## 제작 기록

`productions/<part>/production.json`이 한 파츠의 기록이다. 설정(`config`), 포즈별 상태(`poses`), 시트 묶음(`batches`), 마지막 빌드(`built`)를 가진다. 포즈별 상태는 후보 목록, 선택한 후보, 판정(`unverified` / `approved` / `rejected`), 검수자·메모, 검사 결과다.

설정(`parts-production-config`)의 주요 항목:

| 항목 | 뜻 |
|---|---|
| `part_id`, `design_id`, `slot`, `node`, `socket` | 만들 pack과 그 piece가 놓일 slot·node·부착점 |
| `role`, `mode` | `part` 또는 `body`. `raster`(포즈마다 그림) 또는 `held`(기준 포즈 한 그림) |
| `sheet` | 시트 crop·배율·페이지 크기·비율 |
| `cut` | 자르는 방식(`difference`/`covering`)과 파라미터(몸 차이 임계값, 몸에서 허용 거리, 잔조각, 외곽선, 구멍 메우기, 영역, 팔레트) |
| `wearing` | 이 파츠를 그릴 때 이미 입혀 둘 파츠 ID(부츠는 바지 위에서) |
| `pieces`, `segments`, `by_component` | 몸 조각 라벨로 나눌 조각(몸통·앞소매·뒷소매), 덩어리 단위 배정 |
| `cover_rule`, `covers`, `variants`, `follow`, `trail`, `body` | 아래 "한 장짜리 파츠, 변형, 몸" 참고 |
| `prompt`, `draw` | 이미지 공급자에 줄 지시문과 참조 그림, 공급자와 모델(`parts-part-draw`와 검수 데스크가 쓴다) |

`parts-part-config`는 앞으로 그릴 그림에 쓸 설정(지시문, 시트, 자르기, `wearing`, 조각)을 바꾼다. 이미 만든 후보는 만들 때의 설정을 유지하고, 바뀐 설정은 `history`에 남는다. ID·slot·node·부착점·mode는 그림이 생긴 뒤에는 바꿀 수 없다.

## 한 장짜리 파츠, 변형, 몸

**held 파츠**(헤어·표정·모자·가방)는 `mode: held`다. `reference_pose`(기본: 첫 포즈) 하나에만 그리고, 그 그림을 모든 포즈의 부착점에 건다. 시트는 기준 포즈 한 칸뿐이다. 어느 포즈에서든 반려하면 그 한 장을 다시 그린다. `follow: {socket}`는 다른 부착점이 기준 포즈에서 움직인 만큼 그림을 옮긴다(root에 건 가방이 몸통을 따라감). `trail: {socket, gain, max_px}`는 한 박자 늦게 따라간다. 헤어에는 쓰지 않는다. 1px 지연이 두피 위에서 덜컹거림으로 보였다.

**변형**(`variants`)은 같은 pack 안에서 가림 규칙이 다른 조각이다. 예를 들어 헤어의 본 그림은 `cover_rule: {tag: headwear, show: uncovered}`, 모자용 변형은 `show: covered`다. 변형은 자기 `wearing`(모자)을 입힌 몸 위에 그리므로, 모델은 모자 아래 보이는 머리만 그리고 자를 때도 모자를 뺀다. 모든 명령은 `--variant ID`로 그 트랙을 다루고, 빌드는 본 조각과 변형 조각을 한 pack에 넣는다. 모자 pack은 `covers: [headwear]`를 설정한다.

**몸 production**(`role: body`)은 포즈별 라벨 지도를 그린다. `body.colors`(조각 node마다 색과 `provides`), `near_lighter`(명암으로 앞뒤를 바꿀 쌍)를 설정한다. 칠한 칸을 자르면 몸 픽셀마다 가장 가까운 라벨을 받은 후보가 된다. 검사는 칠한 비율과 앞뒤 교체를 경고한다. 빌드는 몸 raster를 조각으로 잘라 segmented body로 import하고 `segments.json`을 남긴다. 의상 production의 `segments`가 이 파일을 가리킨다. 이미 칠한 라벨은 `parts-part-adopt --labels DIR`로 들여온다. 몸 검수는 파츠 없이 몸만 렌더한다.

## 후보: 그린 그림, 영역, 결과를 함께 보존

`parts-part-slice`는 그려 온 페이지의 칸마다 후보 하나를 `productions/<part>/candidates/<pose>/<digest>/`에 불변으로 게시한다.

| 파일 | 내용 |
|---|---|
| `drawn.png` | 모델이나 작가가 그린 칸 그대로(rig canvas 위치) |
| `under.png` | 그 칸이 그려진 바탕: 몸, 또는 `wearing` 파츠를 입은 몸 |
| `mask.png` | 파츠로 잡은 영역(흰색) |
| `part.png` | 잘라 낸 파츠 |
| `candidate.json` | 포즈·sample·몸 raster hash·바탕 hash, 자르기 방식과 파라미터, 원본 페이지와 guide의 hash·칸 위치, 배치(pivot·socket), 정렬도 |

색 차이로 자르는 것은 영역을 제안하는 한 방법일 뿐이다. 결과 영역은 마스크 파일로 남고, 틀렸으면 `parts-part-mask --pose P --mask region.png`로 명시적인 마스크를 주어 새 후보를 만든다. 자르는 방식은 두 가지다.

- `difference`: 몸과 같은 픽셀을 지운다. 몸을 그대로 둔 채 옷을 입혀 그린 시트용이다. 몸 밖으로 크게 나오는 옷(후드)은 `near_body`를 넓힌다.
- `covering`: 그림이 바꾼 실루엣을 통째로 잡고, 안쪽 빈틈은 그림의 픽셀로 채운다. 머리카락·모자처럼 몸을 덮는 파츠용이다. 픽셀 차분은 머리 외곽선·명암과 비슷한 머리카락이나 띠를 지워 두피를 비치게 했다.

이미지 모델은 그대로 두라고 한 몸까지 다시 칠하는 일이 잦다. 예를 들어 헤어를 그리면서 얼굴을 더 어둡게 칠한다. 이렇게 바뀐 픽셀은 차분이나 덮는 방식 모두 파츠로 잡는다. `keep_palette`(최대 채널 거리)를 주면 파츠 팔레트의 어느 색과도 먼 픽셀을 버린다. 원래 그 자리에 있던 몸 색(외곽선 제외)에 더 가까운 픽셀도 함께 버린다. `prune`은 끝이 풀린 1 px 꼬리(챙 아래 머리카락 잔재)를 그 횟수만큼 다듬는다. 자르기 설정은 `parts-part-config`로 바꾼 뒤 `parts-part-slice --batch`로 이미 그린 페이지를 다시 자르면 된다. 다시 그릴 필요는 없다.

이미 만들어 둔 pack은 `parts-part-adopt --pack part.json`으로 들여온다. 조각으로 나뉜 pack은 포즈마다 한 그림으로 합쳐 후보가 된다.

## 포즈별 검사

`parts-part-check`는 선택된 후보마다 다음을 잰다. 결과는 `passed` / `warning` / `error`이며 승인이 아니다.

| 검사 | 기준 |
|---|---|
| `alignment`, `coverage` | 몸까지 다시 그린 칸과 원래 몸의 겹침(IoU), 그리고 그 칸이 덮은 몸의 비율. IoU가 0.5 미만이면 error다(페이지가 확대·이동됨). IoU가 0.7 미만이면서 몸을 80 % 미만 덮어도 error다(모델이 파츠만 그렸거나 인물을 옮김). 큰 헤어처럼 덧그린 양이 많으면 IoU가 0.6–0.75로 낮아지지만, 몸을 거의 다 덮으므로 통과한다 |
| `wearing` | 입고 그린 파츠(모자 밑 헤어의 모자)가 그 뒤 다시 그려졌으면 error. 지금 모자 위에 다시 그린다 |
| `dropped` | 그림이 바꿨는데 파츠에서 빠진 픽셀. 파츠의 4 % 또는 8 px를 넘으면 warning(자르기가 너무 빡빡함) |
| `repainted` | `keep_palette`가 다시 칠해진 몸으로 보고 버린 픽셀. `dropped`에는 넣지 않는다. 파츠 크기를 넘으면 warning(모델이 몸을 많이 다시 그림) |
| `area_jump` | 같은 클립의 앞뒤 포즈 대비 면적 차이. 45 %를 넘으면 warning(포즈마다 그림이 들쭉날쭉) |
| `specks`, `holes` | 4 px 미만 잔조각, 12 px 이하의 갇힌 구멍(몸이 비침). 다리 사이처럼 큰 틈은 모양이라 세지 않는다 |
| `off_palette` | 설정 팔레트 밖의 색 |
| `pixels` | 0이면 warning(가려진 포즈인지, 자르다 잃었는지 확인) |

## 파츠 단위 승인

`parts-part-build`는 선택된 후보로 pose_map piece를 만들고, `pieces`가 있으면 [몸 조각](03-part-import.md#몸을-조각으로-나누고-옷을-조각으로-나누기)으로 나눠 import한다. `parts-part-review`는 그 pack을 **몸 위에 그것만** 올려 포즈마다 렌더하고(다른 slot은 비움), 포즈별 판정을 `parts-review`(scope `part`, `evidence.appearance: null`)로 봉인한다. 판정은 `pose.<pose_id>` 항목이며, 모든 포즈가 승인되어야 검수 상태가 `approved`가 된다.

이 검수는 정확한 pack revision에 묶인다. 다른 파츠를 import해도 유지되고, 이 파츠의 그림·몸·모션이 바뀌면 사라진다. 조합의 가림·충돌은 기존 appearance 검수가 따로 판단한다. 승인된 신발은 어느 코디에서든 재사용하고, 새 조합만 추가로 검수한다.

## 반려한 포즈만 다시 그리기

할 일 목록에는 반려한 포즈, 아직 그림이 없는 포즈, 검사에서 error가 나온 포즈(누가 승인하지 않은 경우)가 들어간다. `parts-part-sheet`는 인자 없이 그 포즈만 시트로 만들고(이미 승인된 포즈는 손대지 않는다), `prompt.txt`를 함께 쓴다. 다시 그려 온 칸은 새 후보가 되어 그 포즈에 선택되고 판정은 `unverified`로 돌아간다. 이전 후보는 기록에 남는다. 모션에 포즈를 추가한 경우도 같은 경로다. 새 포즈가 할 일 목록에 들어온다.

## 검수 데스크

`parts-review-serve`는 127.0.0.1에서 제작 기록마다 포즈 그리드를 보여 준다.

- **보기:** 몸 위 파츠, 자른 결과, 모델이 그린 칸 사이를 전환할 수 있다. 각 포즈에 검사 결과와 판정이 붙는다.
- **판정:** 포즈를 눌러 승인·반려를 표시하고 사유와 함께 저장한다.
- **재작업:** 반려·누락 포즈 시트, 그리기, 자르기, 검사, 빌드를 차례로 실행할 수 있다.
- **보안:** 쓰기 요청은 서버를 시작할 때 출력한 주소의 `#token=…`이 있어야 받는다.

## 상태와 릴리스

`parts-part-status`는 제작 기록마다 포즈 수, 그림 없음, 반려, 미검사, 검사 error·warning, 승인, 미정과 함께 다음 할 일을 알려 준다. 빌드가 현재 선택을 반영하는지(`built_current`)도 보인다. 모션이 바뀌어 pack이 분리되어도 제작 기록은 포즈 ID로 남으므로, 같은 그림의 포즈는 그대로 이어지고 새 포즈만 할 일이 된다.

릴리스 빌드(`parts-build --mode release`)는 appearance 승인에 더해, **제작 기록이 있는 선택 파츠마다 현재 pack에 대한 승인된 파츠 검수**를 요구한다. 제작 기록 없이 import한 pack은 기존 규칙(appearance 승인)만 따른다.
