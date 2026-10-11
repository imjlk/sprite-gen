# 걷기 원본 교체와 신발·양말 교정 — 2026-10-12

최신 workspace는 `assets/parts-motion-natural-20261012/production-r5/library/assembly.json`이다. 걷기 27프레임과 뛰기 23프레임에 독립 헤어·양말·신발을 적용한다. 같은 공통 모션 위에서 파츠만 교체하며 코디별 런타임 클립은 생성하지 않는다. 아트 승인 0, release-ready false인 검수용 결과다.

## 해결한 문제

기존 걷기는 24fps에서 같은 그림을 반복하는 구간이 많았다. 기존 엔진의 cycle-align 실험도 held-drawings 재촬영 판정을 받아 채택하지 않았다. 같은 SD 바디의 새 Grok 로그인 경로 영상을 추출·정리해 시작 8, 길이 27, 1.125초의 공통 사이클을 등록했다. [실제 프롬프트](assets/motion-natural-20261012/walk-prompt.txt), [제공자 기록](assets/motion-natural-20261012/walk-provider.json), [루프 분석](assets/motion-natural-20261012/walk-loop.json)을 보존했다. 두 걸음 수는 `--steps 2` 입력값이며 자동 확정됐다는 뜻이 아니다.

뛰기 `run.02/14` front와 `run.05/17` back은 신발 입구가 발목에서 12~28px 벌어져 있었다. 입구를 실제 발목에 맞추고 인접 6개 레이어에는 부분 가중치를 적용했다. [측정 기록](assets/motion-natural-20261012/shoe-entry-measurements.json)은 크기·회전을 보존한 이동임을 확인한다. [현재 신발을 회전·크기 제거 후 비교](assets/motion-natural-20261012/shoe-normalized-current-r2.png)하면 run.04/05/06의 앞코 디자인도 유지된다. 실제 뷰의 원근 인상을 아트 승인으로 바꾸지는 않는다.

새 걷기의 초기 수동 좌표에는 실제 발에서 최대 38px 벗어난 발끝이 있었다. 기존 nearest-body-node 검사는 바디 밖 좌표도 가장 가까운 픽셀로 비교해 이를 놓쳤다. `parts_motion_landmark_qc.py`와 body 등록 단계에 실제 원본의 불투명 실루엣까지의 거리 검사를 추가했다. 문제가 있던 12프레임은 두 프레임씩 나눈 네이티브 이미지 관찰로 다시 추출했다. [교정 전 거리](assets/motion-natural-20261012/final-r5/landmark-before.json), [교정 좌표](assets/motion-natural-20261012/final-r5/geometry.json), [200개 좌표 검사](assets/motion-natural-20261012/final-r5/landmark-qc.json)를 보존했다. 현재 보이는 heel/toe 200개는 모두 원본 바디에서 8px 이내다. 이 검사는 필요조건이며 해부학·가림·아트 승인이 아니다.

좌표 관찰 6페이지 중 5개는 정상 JSON이었다. 마지막 원문은 최상위 객체 닫는 중괄호 하나가 빠져 파싱 실패로 남겼다. 원문을 바꾸지 않고 중괄호 한 글자만 추가한 별도 파일을 파싱했으며 숫자나 의미는 수정하지 않았다. 입력 이미지, 프롬프트, 원문, 실패 기록과 별도 복구 기록은 [네이티브 좌표 관찰](assets/motion-natural-20261012/final-r5/native-coordinates/)에 있다. walk.14/15의 near/far 역할은 앞뒤 프레임의 같은 다리 궤적에 맞춰 교환했다. 화면 좌우를 깊이로 해석하지 않는다.

`walk.26`은 앞종아리 일부가 뒷발·뒷다리에 들어가 신발을 신었을 때 지워졌다. 바디 그림은 그대로 두고 foot_back의 821픽셀과 leg_back의 480픽셀을 leg_front로 옮겼다. [첫 교정](assets/motion-natural-20261012/walk26-result.json), [추가 교정](assets/motion-natural-20261012/walk26-complete-result.json)을 보존했다. 원본 알파가 충분히 불투명한 종아리 1,240픽셀을 별도 검사하며 누락은 0이다. 원본 알파 1~14의 희미한 외곽은 완전 불투명한 구멍으로 오인하지 않는다. `parts_motion_calf_ownership.py`의 이동을 재실행해 실제 수정본과 같은 RGBA임을 확인했다.

신발 입구가 바뀌면 양말 연결부도 해당 입구에 맞춰 다시 만든다. 닫힌 신발 안쪽에는 발과 발목 아래의 같은 다리 영역을 사용하고, 바디 윤곽·신발 밑창 위로 제한한다. walk.12에는 발에서 가져온 연결부가 양말 윗선을 넘는 41픽셀이 생겼다. 이 프레임의 윗선을 같은 다리의 직선 높이 34px에 맞춰 이어 뾰족한 조각을 정리했다. 바디 변경이나 승인 복사는 없다.

## 검증과 결과 확인

걷기·뛰기 50프레임을 실제 착용 contact sheet에서 확인했다. 기술 검사는 50 × 헤어/양말/신발 8조합 = 400건이다. Python과 portable JS의 시계·레이어·마스크가 같고, 모두 끈 바디 50개는 원본 RGBA와 같다. 양말은 같은 다리/발의 알파 밖으로 나가지 않고, 필수 발목과 보호 종아리에 구멍이 없으며, 발에서 가져온 양말이 신발 밑창 아래로 새지 않는다. 두 모션의 루프 복귀도 확인한다. 조합별 baked appearance clip은 0이다.

수정한 실제 PNG의 해시를 기준으로 네이티브 관찰을 연결한다. 다른 body/part 참조를 자동 승인하지 않는다. 겹친 뒤쪽 신발은 unknown으로 남으며 사람의 아트 승인과 별개다. 이미지 관찰은 동작 속도·보행 품질을 승인하지 않는다. 브라우저에서 착용 걷기·뛰기를 재생하고 파츠 교체 시 현재 프레임 유지, 루프 복귀, 위치 교정 활성화를 확인한다.

바디만의 걷기 alpha-jolt는 1.0400 → 0.0834다. 최종 착용 PNG의 걷기는 0.8892 → 0.0909이며, 뛰기는 0.2967 → 0.3014다. 이 지표는 알파 변화량이며 보행 품질 점수가 아니다. [측정](assets/motion-natural-20261012/final-r6/playback.json), [걷기 비교 영상](assets/motion-natural-20261012/final-r6/walk-comparison.mp4), [뛰기 비교 영상](assets/motion-natural-20261012/final-r6/run-comparison.mp4)을 보존했다. 뛰기의 공통 바디 23프레임 및 161개 body-node RGBA는 그대로 보존했다. 뛰기 전체의 튐이 개선됐다고 주장하지 않는다.

현재 결과는 [최종 400건 증거](assets/motion-natural-20261012/final-r6/evidence.json), [걷기 전 프레임](assets/motion-natural-20261012/final-r6/walk-all-frames.png), [뛰기 전 프레임](assets/motion-natural-20261012/final-r6/run-all-frames.png), [양말 윗선 전후](assets/motion-natural-20261012/final-r6/cuff-before-after.png), [실제 뷰어](assets/motion-natural-20261012/final-r6/viewer-walk.png)에서 확인한다. 양말 수정 전 네이티브 관찰 15개 중 최종 PNG 바이트가 같은 14개만 재사용했고, 변경된 walk.12는 확대 전후와 뷰어에서 직접 확인했다. [관찰 연결과 UI 확인](assets/motion-natural-20261012/final-r6/current-observation.json)은 가려진 뒤쪽 접합부의 unknown과 미승인 상태를 유지한다.

## 범위와 남은 아트 검수

신발은 기존 고해상도 제품 마스터를 방향·체형 기준 크기에 맞춰 강체 배치한 프레임별 독립 PNG다. 각 포즈의 3D 원근까지 새로 그린 AI 신발 이미지가 완성됐다는 뜻은 아니다. 원근·가림과 손 피부색 변화는 별도 아트 검수 대상이다. [RGB-only 피부색 교정](assets/motion-natural-20261012/colour-fit.json)은 부분 교정이며 원본의 일부 손 색 변화가 남는다. 알파와 모션 시간은 바꾸지 않았다.

바디는 head/torso/두 다리/두 발/root의 7노드 데모다. 팔·손·상의·하의의 완전한 자산화와 다른 체형 피팅은 이번 수정 범위 밖이다. 이전 제작물, 실패 실험과 관찰은 작업 corpus에 보존한다. 초기 alpha 검사가 통과해도 실제 좌표나 그림이 맞다는 뜻은 아니므로, 이전 통과 기록과 나중에 확인한 실패를 함께 남긴다.

## 실행

```bash
.venv/bin/sprite-gen parts-play \
  --assembly assets/parts-motion-natural-20261012/production-r5/library/assembly.json \
  --port 63839 --interpolation frame
.venv/bin/sprite-gen parts-review-serve \
  --assembly assets/parts-motion-natural-20261012/production-r5/library/assembly.json --port 8789
PYTHONPATH=. .venv/bin/python scripts/dev/parts_motion_landmark_qc.py \
  --root assets/parts-motion-natural-20261012/production-r5/library \
  --geometry assets/parts-motion-natural-20261012/production-r5/library/inputs/geometry.json \
  --out NEW-LANDMARK-QC.json
PYTHONPATH=. .venv/bin/python scripts/dev/parts_full_motion.py \
  --out assets/parts-motion-natural-20261012/production-r5 --stage evidence \
  --evidence NEW-EVIDENCE-DIRECTORY
```

동영상 원본·library·deduplicated workspace archive는 로컬 작업 corpus에 보존하고, 검수 증거와 재실행 코드는 Git에 기록한다. 최종 `natural-motion-r6.workspace.zip`은 새 `restored-r6` 폴더로 복원했고 11,479개 파일의 SHA가 모두 일치했다. [저장 기록](assets/motion-natural-20261012/final-r6/workspace-archive.json), [복원 검증](assets/motion-natural-20261012/final-r6/workspace-restore.json)을 보존한다. 현재 모션이나 생성물을 덮어쓰지 않고 새 revision/directory를 사용한다.
