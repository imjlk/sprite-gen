# 걷기·뛰기 전체 프레임의 독립 파츠 검증 — 2026-10-11~12

이 작업은 [이전 4개 참고 포즈](23-sock-and-attachment-masks.md)를 실제 공통 걷기·뛰기 사이클로 확장한다. **걷기 20프레임과 뛰기 23프레임**에 헤어·양말·신발을 각각 독립 자산으로 제작한다. 외형 조합별 영상이나 완성 캐릭터 클립을 만들지 않는다. 공통 모션은 2개이고 런타임은 같은 sample에 대응하는 파츠 PNG를 zmap 순서로 그린다.

관리 worktree `codex/parts-production-v243`의 작업 corpus는 `assets/parts-full-motion-20261011`이다. 원본 영상, 선택하지 않은 take, 실패·중간 제작안과 검수 결과를 보존한다. 현재 workspace와 최종 판정은 아래 증거 파일 및 실행 방법을 따른다. 사람의 아트 승인은 별도이며 자동 통과로 대체하지 않는다.

## 소스와 제작

공통 body 영상은 실제 Grok 로그인 경로로 생성했다. 뛰기는 `body-video/side-run/clip.mp4`를 다시 추출한 `run-clean-r2/loop`, 걷기는 세 번째 take인 `walk-video-r3/loop`를 선택했다. 기존 `video-frames`와 `video-loop`가 추출·cycle 선택·jump repair·strip을 담당한다. 생성 첫 take의 달리기 같은 걸음과 chroma 잔상, 두 번째 걷기의 큰 jolt는 선택하지 않았다.

- [걷기 실제 프롬프트](assets/full-motion-20261011/walk-video-prompt.txt), [걷기 engine 보고서](assets/full-motion-20261011/walk-source-loop.report.json), [뛰기 engine 보고서](assets/full-motion-20261011/run-source-loop.report.json)
- [native 새 신발·걷기 시작 이미지의 provenance](assets/full-motion-20261011/native-provenance.json): 여기의 intent는 제작 의도 요약이며 native 호출의 byte-exact 프롬프트로 주장하지 않는다.
- [걷기 등록](assets/full-motion-20261011/walk-source-registration.json), [뛰기 등록](assets/full-motion-20261011/run-source-registration.json)

고해상도 engine strip의 픽셀을 768×768 공통 rig에 등록한 뒤, 공통 body의 황색·분홍 chroma outlier RGB를 별도로 교정했다. 이 단계를 engine 추출이나 파츠 생성으로 부르지 않는다. **alpha·형태·타이밍은 그대로**이고 이전 source는 보존한다. [원본/수정 SHA 및 교정 픽셀 수](assets/full-motion-20261011/body-colour-repair.json), [정확한 재생용 RGB patch](assets/full-motion-20261011/body-rgb-patch.json)를 제공한다. `parts_full_motion_body_patch.py`가 원본 SHA를 확인한 뒤 기록된 patch를 적용하고 수정본 SHA와 alpha를 검증한다. 43프레임의 정확한 재현을 확인했다. 이것은 해당 source의 제작 교정이며 범용 색 검출기가 아니다.

Body는 head·torso·앞/뒤 leg·앞/뒤 foot으로 분리한다. 각 분리 이미지의 합성은 등록된 공통 body RGBA와 정확히 같다. `torso`에는 아직 팔과 회색 기본 복장이 함께 들어 있다. 팔·손의 세분화, 상의·하의, 다른 체형의 완성 검증을 주장하지 않는다.

헤어는 이전 native 독립 헤어 수정본을 기준으로 실제 head 크기와 귀 윤곽에 제한적으로 등록한다. 머리 위 피복을 교정하면서 귀의 기준 위치를 유지한다. 신발은 새로 생성한 짧은 SD 제품 master를 **균일 크기·회전·뒤꿈치 기준**로 등록한다. bare foot의 변화하는 세 점에 맞춘 비균일 affine은 얇고 찌그러진 신발을 만들어 제외했다. 밑창 직선 검사는 기준을 완화하지 않는다. 양말 RGB는 프로그램으로 구성한 크림색 소재이며 body RGB를 복사하지 않는다. native 소재 파일은 참고 자료이고 각 양말 PNG를 AI가 직접 그린 것은 아니다.

**기본 가림은 zmap**이다. 신발 뒤 칼라 립을 여는 rig-canvas KEEP 마스크를 사용한다. 종아리 경계를 줄이는 예외 마스크 실험은 [범위와 원본 SHA](assets/full-motion-20261011/sock-fit-r2-authoring.json)에 보존했지만 일부 발목의 틈이 남아 최종 연결 방법으로 쓰지 않는다. 최신 양말은 실제 발목 부근 종아리의 방향을 추정하고 **근위 발 영역을 cuff의 발목 연결부에 포함**한다. 초기 입구 제한은 `160<u<700, v<600`이었으나 연결부를 일찍 끊어 제외했다. 현재는 밑창 위 `v<785`, 앞끝 이전 `u<700`과 source 발목 범위를 함께 사용하고, calf 소속은 그대로 보존한다. [입구 제한 재작업](assets/full-motion-20261011/ankle-bridge-r4-authoring.json), [추가 3포즈 교정](assets/full-motion-20261011/ankle-bridge-r5-authoring.json)의 calf 마스크는 전부 KEEP이며 실제로 몸을 자르는 픽셀은 0이다. body 원본은 그대로다. 헤어에는 런타임 마스크가 없다. 신발이 있으면 양말 발 레이어를 `covers/cover_rule`로 숨기지만 이 cuff 연결부는 남긴다. 신발을 벗으면 양말 발, 양말까지 벗으면 맨발이 돌아온다. 런타임에서 파츠를 비틀지 않는다.

## 재작업의 근거

[수정 전 전 프레임 비전 기록](assets/full-motion-20261011/vision-before-rework/summary.json)은 실제 Codex 로그인 이미지 관찰 8회, 43프레임이다. 헤어 43 pass, 양말 17 rework, 신발 15 rework와 2 unknown을 기록했다. 뒤 파츠가 앞 정강이에 가려진 경우의 unknown은 실패나 통과로 바꾸지 않는다. 모든 실제 입력·프롬프트·원문 응답·SHA와 당시 body/motion/part/appearance digest를 보존했다. 이 관찰은 큐레이터 승인이 아니다.

교정은 source에 붙은 [해부학 좌표와 소속](assets/full-motion-20261011/geometry.json), [재작업 기록](assets/full-motion-20261011/joint-rework.json)에 남긴다. 앞발의 발등과 접힌 뒤꿈치가 calf에 남던 분리 오류, `run.02/14`의 잘못된 발목·heel 좌표, 굽은 `run.01` 종아리의 cuff 방향, 일부 좁은 cuff를 수정했다. `walk.03/04`는 foreground shin의 실제 소속을 명시하고 완전히 가려진 rear cuff 및 foot을 구별한다. 보이지 않는 발가락의 위치를 앞 다리 픽셀 소속의 근거로 사용하지 않는다. 이 명시적 포즈 주석은 해당 고정 source의 제작 자료이며 범용 자동 해부학 추정으로 표현하지 않는다.

첫 재작업 후에도 [실제 관찰](assets/full-motion-20261011/vision-before-sock-fit/summary.json)에서 발목의 탭/피부 돌출이 남았다. [다음 국소 교정 관찰](assets/full-motion-20261011/vision-sock-fit-r1/summary.json)을 통과로 바꾸지 않고 보존했다. `parts_full_motion_sock_fit.py`의 첫 안은 14포즈의 cuff에 source 종아리 폭을 참고한 범위를 적용하고, 변경한 calf 접합부만 명시 마스크로 고정했다. 이 안은 최종 상태가 아니다. 발 레이어와 다른 포즈의 선택 원본은 보존한 부분 재작업이다. `walk.03/04`의 뒤 신발은 앞 정강이에 실제로 가려지므로 보이지 않는 전체 형태를 자동 통과시키지 않는다.

소매 폭을 줄이는 두 번째 안도 완전한 해결이 아니었다. `--ankle-bridge`는 cuff를 calf alpha에만 제한하던 오류를 고친다. body의 같은 쪽 leg/foot alpha 합집합 안에서 발목 근처의 연결부를 만들며, 원본 calf를 추가로 잘라내지 않는다. cuff가 자기 calf/ankle anatomy 밖으로 떠 있는지 검사한다. 교차 다리에서 국소 tangent가 빗나간 `run.03:back`과 피부가 남은 `walk.12/13:back`은 `--poses`로 별도 재작업하고 주석된 calf 방향을 사용했다. 이 제작 좌표는 특정 body source에 대한 수동 기준이며 일반적인 체형 추정 모델이 아니다. 검수 보드는 확대 맨발 기준과 실제 착용 발목을 나란히 제공해, 원본 몸의 돌출과 파츠의 잘못된 외곽을 구분할 수 있게 했다.

또한 JSON sample 키의 문자열 순회로 `sample.2`가 `walk.12`에 연결되는 제작 버그를 발견했다. sample의 숫자 인덱스로 pose를 연결하고 frozen timeline의 순서 전체를 assert한다. 이 잘못된 제작안은 뷰어에 게시하지 않았다.

교차 프레임의 큰 밝은 영역을 양말 덩어리로 오인했던 관찰도 남아 있다. 실제 RGBA를 조사한 결과 **`run.03/15`에서 신발이 교체하는 `foot_front`가 반대쪽 calf까지 포함하여 몸이 사라진 구멍**이었다. cuff를 옮기는 것으로는 고쳐지지 않았다. [첫 소속 교정](assets/full-motion-20261011/crossing-body-r1.json) 후에도 구멍이 남아, source에 맞는 [전체 보호 영역](assets/full-motion-20261011/crossing-body-r2.json)을 다시 지정했다. 원본 body RGBA는 그대로이고 해당 영역의 픽셀 소속만 `foot_front`에서 `leg_back`으로 바꿨다. 양말의 front foot도 수정된 소속 alpha로 재제작했다. 해당 보호 calf가 착용 상태에서 사라지는 픽셀 수를 별도 검사하며 0이 아니면 evidence 생성을 실패시킨다. 이는 두 source 프레임에 대한 명시적 보호 기준이며 모든 체형의 자동 검출기로 주장하지 않는다.

`--exact-anatomy` 재작업은 걷기 7개 인터페이스의 형태학적 수축을 제거해 원본 신체 윤곽에 바로 붙인다. 신발 입구 UV 제한과 같은 쪽 신체 소속 검사는 유지한다.

## 검증과 한계

기술 검증은 **8조합 × 43프레임 = 344경우**, 등록된 bare body 43장 정확한 복원, 걷기·뛰기의 wrap, Python/portable JavaScript clock·레이어·마스크 descriptor와 texture SHA를 확인한다. 두 렌더러의 전 픽셀 동일성이나 아트 품질 승인을 뜻하지 않는다. 외형별 baked clip은 0이다. 전 프레임의 actual rendering과 확대 접합 관찰을 기술 검사와 분리한다.

공통 모션 소스에 남은 한계도 보존한다. 걷기 20 sample은 on-twos로 추정되는 **10 drawing/12 drawing fps**이며 jolt 1.0356이 권장 0.43을 넘는다. 뛰기는 hold 1/24 drawing fps, jolt 0.2987이지만 12프레임 하위 주기에 대한 engine의 해석상 suspect가 있다. 반복 경계 seam-pop은 두 모션 모두 false다. 이러한 source motion 판정과 사람의 디자인·동작 승인을 파츠 접합 pass로 대신하지 않는다.

Core 회귀 검사 **133개 통과**: 교차하는 파츠의 전체 required guide를 각 레이어에 중복 귀속시키던 오류, rig-canvas 마스크가 있는 smooth raster를 trim하지 못해 portable texture budget을 초과하던 오류를 고쳤다. 잘못된 body owner나 자기 target 밖의 기준 픽셀은 계속 실패한다. part-local mask는 trim하지 않는다. 64MP 제한을 올리지 않았다.

완전히 가려진 파츠는 sparse `independent.layers[].visible[pose]=false`로 명시한다. 해당 job은 이미지 제공자를 호출하지 않고 빈 alpha를 만들며, candidate와 build/export의 visibility에 고정한다. 보이는 job의 빈 이미지는 계속 실패하고, 숨긴 job에 그림을 넣어도 실패한다. visibility 변경은 현재 빌드·검수 및 부분 재작업에서 보존하려던 레이어를 무효화한다. 비교 뷰에도 같은 visibility를 적용한다. `walk.04`의 source back foot에는 alpha 2인 4개 fringe 픽셀이 있어 원본 body에 보존하지만 숨김 양말 자산은 정확히 비운다. [수정 이력](assets/full-motion-20261011/hidden-layer-alpha-cleanup.json)을 남겼고 visible-art 기준 16을 바꾸지 않았다.

추가 관찰에서 발목 기준 등록이 실제 뒤꿈치/발바닥보다 신발을 높게 놓는 프레임을 확인했다. [현재 heel 등록](assets/full-motion-20261011/heel-fit.json)은 균일 scale/rotation을 유지하며 native heel `[120,795]`를 source heel에 일치시킨다. `footwear_anchor=heel`은 별도의 제품 배치 기준이다. source body RGBA와 공통 motion은 그대로다. 완전히 가려진 `walk.04` rear shoe는 투명한 자산과 `visible=false`로 명시하며 관찰 불가능한 직선 검사를 적용하지 않는다.

신발 기준이 바뀌면 이전 foot bridge를 계속 누적하지 않는다. [최종 bridge](assets/full-motion-20261011/sole-bounded-bridge.json)는 기존 cuff 중 calf 소속만 보존하고 foot 소속은 현재 sole 기준으로 재계산한다. 발목 필수 영역 누락과 **foot 소속 양말이 밑창 아래로 나오는 픽셀**을 전 프레임에서 각각 0으로 검사한다. 이 조건과 source heel 오차는 아트 승인이 아니다.

반복 작업 중 socks 상태 JSON이 4,319,639 bytes로 기존 4MiB 한도를 넘었다. 원문을 보존하고 배치별 JSON을 불변 SHA 파일로 분리하여 상태를 1,541,510 bytes로 복구했다. `parts_production._save/_load`는 큰 이력을 별도 보존·검증하며 작은 기존 상태는 형식을 유지한다. 한도를 올리지 않았다. 이력의 동일한 복원과 변조 검출, 단일 과대 배치가 기존 정상 상태를 덮지 않는 회귀 검사도 추가했다.

## 현재 결과물과 판정

- [걷기 20프레임](assets/full-motion-20261011/walk-all-frames.png), [뛰기 23프레임](assets/full-motion-20261011/run-all-frames.png)
- [현재 43프레임 시각 관찰](assets/full-motion-20261011/current-visual-observations.json), [마지막 수정 페이지의 실제 비전 입력·원문](assets/full-motion-20261011/vision-final-walk-06/summary.json)
- [현재 기술 증거](assets/full-motion-20261011/evidence.json), [현재 파츠별 검사·빌드·승인 수](assets/full-motion-20261011/current-production.json)
- [뷰어 걷기 화면](assets/full-motion-20261011/player-walk-screen.png), [뷰어 뛰기 화면](assets/full-motion-20261011/player-run-screen.png)

양말은 `sock-fit-r7` → `crossing-r2-sock-feet` → `heel-fit-r1` → `sole-bounded-bridge-r4` → `source-floor-r1` → `sole-bounded-bridge-r5` → `heel-ownership-r1` → `sole-bounded-bridge-r6` 순서로 교정했으며 이전 실패안은 따로 남아 있다. 신발은 rigid registration을 사용하는 독립 자산이다. 신발을 끄면 양말 발이, 양말까지 끄면 원본 맨발이 같은 모션 시점에서 복원되는 것을 실제 브라우저에서 확인했다. 걷기·뛰기 재생과 프레임 이동도 확인했다. 뷰어가 표시하는 “조합 검사 기준 미등록”은 범용 fitting/stability 기준이 아직 등록되지 않았다는 뜻이며, 본 작업의 별도 증거를 UI의 자동 승인으로 주장하지 않는다.

body 소속을 고친 뒤 이미지가 같은 후보도 생성 당시 `context.body`가 이전 digest이면 위치 교정·승인 UI는 이전 기준으로 차단된다. `parts_full_motion_rebind.py`는 과거 문서의 digest를 편집하지 않고, **현재 body 기준의 새 sheet/batch/candidate에 기존 PNG를 등록**한다. 모든 채택 레이어의 RGBA가 그대로임을 assert하며 제공자 호출과 승인 복사는 0이다. 수정 이전 독립 생성 참조는 보존한다.

[종아리 소속 교정 후 43프레임 관찰](assets/full-motion-20261011/vision-before-sole-bridge/summary.json)에서도 신발의 `covers`가 양말 발 레이어를 숨길 때 남는 발목 틈이 확인됐다. `parts_full_motion_ankle_bridge.py`는 기존 cuff의 상단과 신체 윤곽을 유지하고, **source foot의 발목 연결부를 native 신발의 밑창 위에만 추가**한다. native UV 밑창 795보다 위인 `v<785`, forefoot보다 뒤인 `u<700`, source 발목 70px 범위를 사용한다. 이전 `v<600` 제한은 연결부를 너무 일찍 끊었다. 이 기준은 현재 native master에 대한 제작 기준으로서 다른 제품의 일반 마스크 규칙이 아니다. 새 body/neck guide가 실제 착용 렌더에서 비는 픽셀도 0이어야 한다.

[새 body 기준 재등록](assets/full-motion-20261011/current-body-adoption.json)은 3파츠 × 43포즈, 총 301개 layer의 RGBA 동일성을 검증했다. 이전 생성 출처를 변경하지 않고 새 기준의 후보를 만들었으므로 위치 교정 UI가 현재 참조를 사용할 수 있다.

전 프레임 비전 보드는 착용 상태에 대한 추가 관찰이며, 검수 UI의 파츠별 엄격한 후보 비전 실행/사람 승인을 대신하지 않는다. 파츠별 “미실행” 표시는 자동 승인을 만들지 않았다는 뜻이다.

아트 승인은 세 파츠 모두 **0**, `release_ready=false`다. 신발의 정적 검사 41 pass/2 warning와 시각 관찰을 구분한다. 헤어는 43개의 작은 pinhole warning, 양말은 37 warning/6 pass가 남아 있다. warning의 수치와 원문은 보존하며 실패를 숨기기 위해 threshold를 올리지 않았다.

[비전 원문](assets/full-motion-20261011/vision-before-floor-rework/summary.json)의 추가 판정은 자동으로 pass로 덮지 않았다. `run.05`의 압축 의심은 [회전·크기를 되돌린 실제 part 비교](assets/full-motion-20261011/shoe-normalized-review.png)에서 같은 프로파일임을 확인했다. `run.02`의 발끝 주변에는 [원본 피부색 잔류가 0](assets/full-motion-20261011/run02-toe-review.json)이었다. 겹친 종아리와 발목의 회색 틈 의심은 [실제 alpha 비교](assets/full-motion-20261011/disputed-frame-alpha-review.json)에서 확인되지 않았다. 이것은 원문 보존과 관찰의 불확실성 해소이며 사람의 아트 승인으로 바꾸지 않는다.

`walk.08/11`은 별도 [source-foot 하단 윤곽 제안](assets/full-motion-20261011/source-floor-prototype.png)을 실제 body와 겹쳐 보고 [신발 배치](assets/full-motion-20261011/source-floor-rework.json)를 추가 교정했다. 원래 anatomy landmark는 보존하고 `footwear_heel/toe`를 별도로 사용한다. 이 두 pose의 제안은 특정 source alpha에서 나온 제작 자료이며 범용 foot 추정기로 주장하지 않는다.

`walk.08`의 신발 밖 크림색 뒤꿈치 돌출은 source foot 연결부가 제품 뒤쪽까지 포함된 문제였다. 마지막 재작업은 이 pose의 front 연결부에만 `sock_bridge_u_min=160`을 지정한다. 기존 calf 윤곽과 body 마스크를 바꾸지 않고 foot 소속 소재만 제품 뒤쪽 안으로 제한한다. 43개 착용 PNG 중 42개는 직전 검증본과 byte-identical이고 `walk.08`만 변경되었다. [현재 이미지 비교](assets/full-motion-20261011/visual-input-comparison-final.json)와 재검수 원문을 별도로 보존한다.

입구 UV 제한 뒤에도 [실제 관찰](assets/full-motion-20261011/vision-before-heel-ownership/summary.json)에서 `walk.08` front cuff의 삼각 돌출이 남았다. 이 53픽셀은 foot bridge가 아니라 `leg_front`에 포함되어 있었다. [마지막 소속 교정](assets/full-motion-20261011/walk08-heel-ownership.json)은 이를 `foot_front`로 옮기고 양말 cuff에서는 제거하며 양말 foot에 반영한다. 공통 body RGBA와 타이밍은 정확히 보존한다. 원본 그림을 자른 런타임 마스크가 아니며, 신발 교체 시 남는 바디 영역을 바로잡은 것이다. [최종 body 기준 재등록](assets/full-motion-20261011/heel-current-body-adoption.json)은 세 파츠 43포즈의 301개 레이어 RGBA가 재등록 전과 정확히 같음을 검증했다. 제공자 호출과 승인 복사는 0이다.

최종 수정 페이지의 실제 Codex 이미지 관찰에서 `walk.06~11`의 헤어·양말·신발은 모두 attachment pass로 관찰되었다. 42개의 동일한 착용 PNG와 새 페이지를 연결한 43프레임 관찰 기록은 헤어 43 pass, 양말 36 pass/4 unknown/3 rework, 신발 38 pass/3 unknown/2 rework다. 남은 원문 의심은 앞서 기록한 `run.02/05`, `walk.14~16`의 진단과 함께 보존한다. 실제 RGBA와 회전·크기를 되돌린 제품 비교에서 확인되지 않은 의심을 승인으로 바꾸지 않는다. 이 수치는 사람의 아트 승인이나 동작 소스 승인과 별개다.

## 실행 방법

최종 workspace의 library와 본 작업의 증거를 보존한 뒤 아래 명령으로 이어간다. 이전 4포즈 workspace는 별도 역사이며 지우지 않는다.

```bash
.venv/bin/sprite-gen parts-play \
  --assembly assets/parts-full-motion-20261011/production-r12/library/assembly.json \
  --port 63839 --interpolation frame
.venv/bin/sprite-gen parts-review-serve \
  --assembly assets/parts-full-motion-20261011/production-r12/library/assembly.json --port 8789
PYTHONPATH=. .venv/bin/python scripts/dev/parts_full_motion.py \
  --out assets/parts-full-motion-20261011/production-r12 --stage evidence \
  --evidence NEW-EVIDENCE-DIRECTORY
PYTHONPATH=. .venv/bin/python scripts/dev/parts_full_motion_vision.py \
  --evidence NEW-EVIDENCE-DIRECTORY --revision NEW-VISION-REVISION
```

최종 library는 해시로 중복을 제거한 로컬 archive에도 보존한다. [archive 파일 수·SHA](assets/full-motion-20261011/workspace-archive.json), [새 폴더에 복원한 파일 SHA 검증](assets/full-motion-20261011/workspace-restore.json)을 참고한다. 55,518개 파일을 새 폴더로 복원하여 모든 SHA가 일치했다. 큰 archive는 Git에 넣지 않고 해당 작업 corpus에 보존한다.

```bash
.venv/bin/python scripts/dev/parts_workspace_snapshot.py restore \
  --archive assets/parts-full-motion-20261011/full-motion-final-r12.workspace.zip \
  --root NEW-RESTORE-DIRECTORY
```

제작은 library → parts `--defer-check` → 실제 이미지 확인 → build → evidence → 실제 vision 순서다. configure/sheet/slice/check/build는 기존 sprite-gen 경로를 사용하며 image generation reference와 검수 기준을 source SHA에 묶는다. source나 파츠가 바뀌면 새 revision 및 새 evidence에서 재검증한다. 이전 실패를 삭제하거나 승인 판정을 복사하지 않는다.
