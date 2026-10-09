# 독립 파츠 생성과 첫 걷기 결과물

> Owns: `part-only` 생성 규격, 동결된 포즈 인터페이스, 2026-10-09 첫 결과물과 재현 경로 · Index: [docs/README.md](../README.md)

동결된 body와 motion을 위치 참조로 사용해 **옷 자체만** 생성하고, 포즈별 독립 PNG를 기존 레이어 엔진으로 재생한다. 새 코디마다 캐릭터 영상·동작 아틀라스를 만들지 않는다. 기존 dressed-sheet 차분은 별도 입력 방식으로 유지한다.

## 생성 규격

```json
{
  "cut": {"method": "part-only"},
  "pose_checks": {
    "walk.01": {
      "required": "inputs/interfaces/walk.01/required.png",
      "protected": "inputs/interfaces/walk.01/protected.png",
      "allowed": "inputs/interfaces/walk.01/allowed.png",
      "occluded": "inputs/interfaces/walk.01/occluded.png"
    }
  }
}
```

각 PNG는 rig canvas와 같은 크기다. 흰색은 해당 영역이며, `allowed`만 흰색 밖을 검사한다. `required`는 반드시 옷으로 덮을 영역, `protected`는 침범하면 안 되는 영역이다. `occluded`는 앞에 놓이는 머리·손에 가려질 영역이다. 원본 옷 그림은 보존하고 가림만 적용한 파생 레이어를 import한다. 그림 크기나 위치를 자동 보정하지 않는다.

이 규격은 체형 공통 규칙이 아니다. body revision에 맞는 인터페이스를 작가가 정해야 한다. 데모 스크립트의 회색 속옷 판별과 손끝 추정은 Mina fixture 전용이다. 다른 체형에서는 별도 binding과 인터페이스가 필요하며 이번 데모가 체형 확장을 검증한 것은 아니다.

시트 프롬프트에는 파츠만 출력하라는 규격과 이전 오류·반려 사유를 붙인다. provider/model, 시도별 성공·실패, 원본 그림, 후보와 hash가 남는다. 실패 후 같은 `parts-part-draw`를 재실행하면 그림이 없는 페이지만 시도한다. 생성에 성공했지만 검사에 실패한 포즈는 `parts-part-sheet`가 다시 대상으로 잡는다. 무제한 자동 재생성은 하지 않는다. 검사 통과는 시각 승인이 아니며 release 관문은 유지된다.

## 리베이스

2026-10-09 확인한 upstream 최신 릴리즈는 **v2.43.0**, `9935d674128e512ea9727168252bb544e68c8f69`다. 기존 `codex/parts-system`의 46개 커밋을 별도 managed worktree의 `codex/parts-production-v243`에 리베이스했다. 원래 checkout의 미커밋 수정·GIF·ignored 생성 자산은 그대로 보존했다. 최신 upstream에서 삭제한 release workflow는 복원하지 않았고, 그 파일의 존재를 전제로 한 fork 전용 legacy 테스트도 제거했다. 원격 push는 수행하지 않았다.

## 첫 결과물

실험 경로는 `assets/parts-first-result-20261009/`다. 공급자는 codex, 모델은 `gpt-5.6-sol`이며 실제 이미지 생성은 public `parts-part-draw` 경로를 사용했다. 두 디자인에 총 9페이지를 생성했다. 첫 6개 대표 포즈에서 시작하고, 누락·검사 실패 포즈를 추가한 뒤 실패 포즈를 한 번 더 재작업했다. 모든 원본과 이전 후보를 보존했다.

| 항목 | 결과 |
|---|---|
| body | `sd.mina.body`, 원본 digest 유지 |
| motion | `sd.mina.motion`, 원본 digest 유지 |
| 범위 | 걷기 14 samples / 14 poses, 공통 클립 1개 |
| 새 파츠 | `demo.upper.teal` / `demo.upper.red` |
| 파츠 조각 | 뒷소매 / 몸통 / 앞소매, 기존 z-order 재사용 |
| 청록 검사 | 통과 7, error 7 |
| 후드 검사 | 통과 10, error 4 |
| 사람의 승인 | 0 / 28, 모두 `unverified` |
| 런타임 코디 클립 생성 | 0 |

청록 실패 포즈는 `.01/.02/.03/.05/.06/.07/.14`, 후드는 `.05/.06/.10/.12`다. 속옷 미덮임, 허용 영역 밖 실루엣이 남아 있어 출시용 아트로 승인하지 않았다. 초기 일부 생성물에는 피부까지 섞였고, 원본·이전 후보에 남아 있다. 손·얼굴이 옷보다 앞으로 보이는 가림과 별개로 원본 그림도 확인해야 한다. 수치가 통과해도 디자인·소매·프레임 간 연속성이 좋은지는 작가 검수 대상이다.

`source-lock.json`은 body/motion 참조와 걷기 원본 hash를 기록하고, `result.json`은 최종 검사·빌드·export 결과를 기록한다. `manual_art_edits`는 비어 있다. 인터페이스 지도는 작성·수정했으며 그 작업 시간을 측정하지 않았으므로 수작업 0분으로 계산하지 않는다.

`comparison.png`, `comparison-all.png`, `comparison.gif`는 몸/청록/후드를 같은 시점에 비교하는 **검수 증거**다. GIF는 표시용으로 시간을 10ms 단위로 반올림한다. 실제 portable export는 원본 tick/duration과 독립 텍스처를 유지한다. `export/preview.json`이 standalone HTML·manifest·PNG를 가리킨다. 생성 자산은 Git의 ignored 로컬 실험 파일이며 소스 커밋만으로 자동 다운로드되지 않는다.

## 이어서 확인하기

아래 명령은 이 worktree에서 실행한다. 기존 실험에 `prepare`를 재실행하면 덮어쓰지 않고 실패한다. 처음 재현할 때는 새 `--out` 디렉터리를 지정한다. 원본 library와 디자인 참조 이미지가 필요하다.

```bash
.venv/bin/python scripts/dev/parts_first_result.py prepare \
  --source /Users/imjlk/repos/imjlk/sprite-gen/assets/sd-avatar-mina-20261006 \
  --teal-ref /Users/imjlk/repos/imjlk/sprite-gen/assets/sd-explorer-hq-20261006/design/upper.a.raw.png \
  --out assets/parts-first-result-new
```

`sheet → draw → slice`를 `--design teal`/`red`별로 실행하고 `extend → draw → slice`로 누락·실패 포즈를 채운다. `finish`는 검사·빌드·미승인 검수 이미지·appearance·독립 레이어 export를 만든다. 현재 실험의 재작업 설정은 `configs/*-retry.json`에 있으며 production history에도 남는다. 재생 서버는 시작 시 snapshot을 잡으므로 파츠를 다시 빌드한 뒤 재시작해야 한다.

```bash
.venv/bin/python -m sprite_gen.cli parts-play \
  --assembly assets/parts-first-result-20261009/library/assembly.json \
  --port 8775 --interpolation frame --title '동결된 걷기 · 독립 상의 2종'
.venv/bin/python -m sprite_gen.cli parts-review-serve \
  --assembly assets/parts-first-result-20261009/library/assembly.json --port 8776
```

재생 화면은 `http://127.0.0.1:8775/`, 검수는 `http://127.0.0.1:8776/`다. 검수 쓰기는 서버가 출력하는 세션 토큰 주소로 열어야 한다. 일반 주소는 읽기 전용이다. 기존 파츠 옵션도 복사된 library에 남아 있으며 새 상의 기본값은 청록이다. 이번 결과는 걷기 상의 제작 경로만 검증하며 뛰기·신발 품질을 해결한 것으로 보지 않는다.

## 확인한 범위와 다음 단계

리베이스 직후 전체 회귀 검사는 10,207 passed / 7 skipped였다. 이후 변경된 production·pose sheet·분할·runtime·export·player 관련 검사는 89개 통과했다. 실제 생성 자산으로 body 14 samples의 alpha·보이는 RGB가 원본과 일치하고, 28회 교체에서 같은 sample·시각을 유지하며 독립 레이어와 CPU 검수 결과가 일치함을 확인했다. 브라우저에서도 정지한 13번째 프레임의 상의 교체가 재생 위치를 유지했다. 새 출력 디렉터리에서 `prepare`를 다시 실행해 입력 복사와 설정 생성도 확인했다.

다음 아트 작업은 실패 포즈의 목·허리 덮임과 소매 위치를 먼저 재작업하고, 검사 통과 포즈까지 포함해 피부 혼입·프레임 간 형태 변화·소매 가림을 시각 검수하는 것이다. 몸이나 공통 걷기를 다시 생성해 위치 오류를 숨기지 않는다. 그 이후에만 뛰기·하의·신발과 다른 체형 binding으로 넓힌다. `rebase-verification.json`과 `result.json`이 다음 세션에서 검사 범위와 현재 미승인 상태를 확인할 근거다.
