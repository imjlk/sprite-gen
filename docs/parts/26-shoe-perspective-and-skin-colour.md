# 신발 옆모습과 피부색 교정 — 2026-10-12

작업본은 `assets/parts-appearance-polish-20261012/production-r4/library/assembly.json`이다. 기존 걷기 27·뛰기 23프레임의 순서·시간·알파·부위 분리를 유지하고, 신발 그림과 공통 바디의 피부색을 수정했다. 코디별 동작 영상은 만들지 않는다. 아트 승인과 release-ready는 별도이며 자동 승인하지 않는다.

신발은 기존 마스터를 참고해 built-in imagegen으로 오른쪽을 향한 옆모습을 다시 그렸다. 높게 튀어나온 혀와 발등 노출을 줄이고, 발끝과 밑창의 두께를 유지하는 lateral 그림을 사용한다. [생성 원본](assets/appearance-polish-20261012/shoe-side-generated.png), [실제 프롬프트](assets/appearance-polish-20261012/shoe-prompt.txt), [원본에 묶인 기준점](assets/appearance-polish-20261012/shoe-side-registration.json)을 보존한다. 원본 SHA나 크기가 다른 그림에는 이 기준점을 적용하지 않는다.

등록은 균일한 크기 조정·회전·이동이며 세 점에 맞춰 신발을 찌그러뜨리지 않는다. 모션별 신발 길이를 고정해 bare toe나 머리 폭 변화에 따라 크기가 출렁이지 않게 했다. 뒤쪽 신발은 4% 어두운 같은 소재를 사용한다. 이 옆모습 데모에서 yaw는 고정이다. 임의 방향의 3D 신발을 자동 추정하는 기능은 아니다. [100개 독립 신발 레이어 제작 기록](assets/appearance-polish-20261012/shoe-authoring.json)을 참고한다.

손 색 교정은 이 SD 원본의 warm RGB 영역에 대한 프로그램 편집이다. 회색 옷의 큰 연결 영역과 4px 경계는 보호하고, 피부 내부는 복숭아색, 붉은 가장자리 선은 갈색 계열로 통일했다. 피부 윤곽이나 손 모양을 생성하지 않는다. [RGB 편집 기록](assets/appearance-polish-20261012/skin-authoring.json), [350개 바디 레이어 알파·시간 보존](assets/appearance-polish-20261012/body-preservation.json)을 남겼다. 이 색 규칙을 다른 스타일·체형에 자동 재사용하지 않는다.

첫 교정은 손 색을 개선했지만 옷 경계가 따뜻하게 변해 반려했다. [첫 비전 검사](assets/appearance-polish-20261012/skin-vision-r1/report.json)와 [당시 그림](assets/appearance-polish-20261012/hands-before-after-rejected-r1.png)을 보존했다. 보호 영역을 수정한 [최종 전후 그림](assets/appearance-polish-20261012/hands-before-after.png)은 네이티브 이미지 관찰에서 7개 문제 프레임 모두 improved였다. [최종 비전 응답](assets/appearance-polish-20261012/skin-vision-r3/report.json)은 run.17의 작은 밝은 색이 원본 하이라이트인지 불확실하다고 남겼다. 이는 정지 그림 관찰이며 보행·타이밍이나 큐레이터 승인이 아니다.

`parts_motion_skin_palette.py`, `parts_motion_side_shoes.py`는 버전이 있는 별도 원본·제작 기록을 만든다. `parts_full_motion.py --preserve-ownership`은 RGB가 달라져도 검수한 부위 소유와 종아리 교정을 보존한다. 파츠는 새 body/motion 참조에 같은 PNG로 다시 연결하고 오래된 승인을 복사하지 않는다. 팔·손을 완전한 별도 파츠로 분리한 것은 아니다.

착용 파츠 비전 12프레임 관찰에서는 run.17 뒤쪽 신발의 입체감, run.16 양말 높이가 unknown이었다. 실제 재생의 보이는 신발은 같은 옆모습 제품과 균일한 변환을 유지하지만 이 정지 관찰을 아트 승인으로 바꾸지 않는다. [관찰 원문](assets/appearance-polish-20261012/shoe-vision-before-calf-r4/summary.json)을 남겼다. walk.12에는 실제 작은 종아리 틈이 확인돼 추가 교정했다. 앞발에 들어간 뒤종아리 83픽셀을 leg_back으로 옮기고, 원본의 불투명 종아리 73픽셀을 독립 보호 검사에 추가했다. 바디 그림은 RGBA 그대로다. [소유 교정](assets/appearance-polish-20261012/walk12-calf.json), [실제 최종 전후](assets/appearance-polish-20261012/final-r4/walk12-final-comparison.png)를 보존했다. 같은 프레임의 양말 윗선은 낮추고 작은 곡선과 안티앨리어싱으로 정리했다.

[최종 walk.12 비전](assets/appearance-polish-20261012/final-r4/walk12-vision/report.json)은 종아리·윗선 improved, 보이는 신발 pass였다. 윗선에는 각진 인상이 일부 남고 가려진 뒤 칼라는 판단할 수 없다는 한계도 남겼다. 이 관찰의 실제 PNG SHA와 body/appearance 참조는 최종 400건 증거와 정확히 같으며, 직전 작업본의 나머지 49프레임은 PNG 바이트가 같다. [최종 연결 증거](assets/appearance-polish-20261012/final-r4/final-adoption-and-observation.json)를 참고한다.

최종 착용 [걷기 전 프레임](assets/appearance-polish-20261012/final-r4/walk-all-frames.png)과 [뛰기 전 프레임](assets/appearance-polish-20261012/final-r4/run-all-frames.png)을 확인한 뒤, 브라우저에서 1×와 0.5×로 재생했다. 발 교차 구간의 신발 윤곽·발목 연결과 손 색을 시간순 화면으로 관찰했다. [실제 걷기 화면](assets/appearance-polish-20261012/final-r4/viewer-walk.png), [뛰기 화면](assets/appearance-polish-20261012/final-r4/viewer-run.png), [직접 관찰과 UI 확인](assets/appearance-polish-20261012/actual-playback-observation.json)을 보존한다. 두 모션은 마지막에서 첫 프레임으로 돌아가며, 신발 교체는 13/27 프레임을 유지했다. 위치 교정도 활성화된다.

[최종 기술 검사](assets/appearance-polish-20261012/final-r4/evidence.json)는 50프레임 × 8조합 = 400건이며 Python/JS 시계·레이어·마스크 일치, 맨몸 50프레임 RGBA 일치, 필수 발목·보호 종아리 누락 0을 확인했다. 실제 [걷기 비교 영상](assets/appearance-polish-20261012/final-r4/walk-comparison.mp4)과 [뛰기 비교 영상](assets/appearance-polish-20261012/final-r4/run-comparison.mp4)은 검수용이며 런타임 코디 영상은 0개다. [알파 변화 측정](assets/appearance-polish-20261012/final-r4/playback.json)은 걷기 0.0909→0.0902, 뛰기 0.3014→0.3031이다. 이번 개선의 근거는 색과 그림의 직접 관찰이며 뛰기의 튐 개선을 주장하지 않는다.

검수 영상의 첫 실행은 전 프레임 생성이 끝나기 전에 시작돼 뛰기 2/23프레임만 들어갔다. 이 영상은 [미채택 기록](assets/appearance-polish-20261012/partial-preview-invalid.json)을 남기고 제외했다. `parts_motion_preview.py`는 완료된 evidence와 정확한 프레임 목록이 없으면 출력 전 거부하도록 수정했고 회귀 검사를 추가했다. 최종 비교 영상은 27/23프레임 전체를 사용한다.

원본·후보·이력은 로컬 `appearance-polish-final-r4.workspace.zip`에 보존했다. 새 폴더에 32,716개 파일을 복원해 모든 SHA가 일치했다. [저장 기록](assets/appearance-polish-20261012/final-r4/workspace-archive.json), [복원 검사](assets/appearance-polish-20261012/final-r4/workspace-restore.json)를 참고한다. 큰 archive는 Git에 포함하지 않는다. 색·알파·옷 경계 보존, 고정 신발 길이, 미완료 영상 거부 회귀 검사 3개도 통과했다.

```bash
.venv/bin/sprite-gen parts-play \
  --assembly assets/parts-appearance-polish-20261012/production-r4/library/assembly.json \
  --port 63839 --interpolation frame
.venv/bin/sprite-gen parts-review-serve \
  --assembly assets/parts-appearance-polish-20261012/production-r4/library/assembly.json --port 8789
```
