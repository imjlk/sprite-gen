# 파츠 시스템의 기준선과 회귀 보호

> Owns: Fork baseline, legacy output pins and release policy · Index: [docs/README.md](../README.md)

기준 커밋은 upstream `aldegad/sprite-gen`의 `d993e5300b4255111e8ee0e29779caea1b3b97bd` (v2.20.0)다. 작업 브랜치는 `codex/parts-system`이며, 기존 Python engine과 legacy run 계약을 유지한다. 새 명령은 별도 `assembly.json`을 명시적으로 입력받는다.

## 기록한 실행 환경

2026-10-04 변경 전 전체 pytest 결과는 **3163 passed, 2 skipped, 74 warnings, 326.38초**다. 환경은 macOS arm64, CPython 3.14.0rc2, Pillow 12.3.0, NumPy 2.5.3, pytest 9.1.1이었다. [동일 커밋의 upstream CI](https://github.com/aldegad/sprite-gen/actions/runs/37122262255)도 통과했다.

구현 검증에는 안정 버전 **CPython 3.13.2**로 만든 프로젝트 `.venv`를 사용한다. 의존성은 Pillow 12.3.0, NumPy 2.5.3, pytest 9.1.1, pytest-timeout 2.4.0이다. 프로젝트는 CPython 3.11 이상을 지원하며, 기존 Linux/Python 3.14 CI는 유지한다. 로컬 실행이 Linux CI 또는 안정 버전 Python 3.14 검증을 대신한다고 주장하지 않는다.

PR00–01 구현 후 동일한 전체 명령을 실행한 결과는 **3241 passed, 2 skipped, 74 warnings, 326.78초**다. 기준선보다 78개 테스트가 추가됐으며 기존 skip과 warning은 동일하다. 일반/파츠 CLI, 다섯 profile의 다중 piece/sample binding, 실제 wheel의 preset 포함 여부와 기존 전체 회귀 테스트가 통과했다.

PR02 모션 snapshot 구현 후 전체 결과는 **3274 passed, 2 skipped, 74 warnings, 323.06초**다. 이후 malformed annotation/lock 예외 처리 2개 테스트를 추가했고, 해당 변경을 포함한 adapter·motion·contract·workspace·CLI 관련 **130개 테스트**가 통과했다. 원본 finalized run의 내용 불변, source 교체·pixel budget·stale annotation·revision invalidation과 기존 atlas/layer golden도 확인했다.

PR03 body/part import 구현 후 전체 결과는 **3298 passed, 2 skipped, 74 warnings, 325.72초**다. Crop/full-canvas 복원 픽셀 일치, tiny RGBA 보존, 다섯 체형의 같은 import 경로, 다중 body piece, mask/region 크기, missing coverage, snapshot 쓰기 실패 및 index 동시 변경을 확인했다. Legacy golden과 기존 전체 테스트도 통과했다. Renderer와 실제 아트의 96 clip 검수는 이 결과의 범위에 포함하지 않는다.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q -ra
```

이번 프로젝트 환경은 `uv venv --python /opt/homebrew/bin/python3.13 .venv`와 `uv pip install --python .venv/bin/python -e ".[dev]"`로 설치했다. `uv`로 만든 환경에 pip를 별도로 seed하지 않았다면 설치에는 `uv pip`를 사용한다.

ffmpeg와 `-exact`을 지원하는 img2webp가 필요하다. 현재 제외된 검증은 `SPRITE_GEN_CHROMA_REAL_STILLS`가 지정된 실제 still 검사와 실제 RIFE 바이너리 검사다. 기존 74개 경고는 Pillow `getdata()` deprecation에서 발생했다. 새 파츠 테스트는 생성 API, 네트워크 또는 유료 모델 호출을 사용하지 않는다.

## 고정한 legacy 결과

[회귀 테스트](../../tests/packaging/test_parts_legacy_baseline.py)는 기준 커밋을 `git archive`로 별도 디렉터리에 풀어 직접 실행한 결과를 고정한다. 다음 값은 새 구현에서 계산한 값으로 기준선을 덮어쓰지 않았다.

| 경로 | 보존할 결과 | RGBA 픽셀 SHA-256 |
|---|---|---|
| non-layer: 기존 run fixture의 extract → compose-atlas | 요청 바이트, 전체 runtime manifest, 384×192 RGBA | `f36f6fccfdc911e685344baffd721d469cd36a6b840db9a63e54e5f7a96fc36c` |
| layer: 기존 rigbot의 extract → compose-layers | 요청 바이트, 전체 composite manifest/report, 192×96 RGBA | `35b48c54533c1848bd3b1b87a6d16d860ed94d886ac79776f8263a75242a5cbd` |

기록은 [non-layer golden](../../tests/fixtures/parts/legacy-baseline.json), [layer golden](../../tests/fixtures/parts/legacy-layer-baseline.json)에 있다. PNG encoder 차이를 픽셀 변경으로 오인하지 않도록 RGBA 바이트를 비교한다. 기존 전체 테스트는 curation·mask·접합·clipping·export·CLI 결과도 계속 검사한다. legacy 실행이 `assembly.json`이나 `parts/`를 새로 만드는 것도 금지한다.

finalize된 이미지와 기존 landmark가 같은 변형을 거친다고 가정하지 않는다. `curation.dx=6` 재현에서는 body bbox만 이동하고 `hand_r`는 유지됐다. PR02는 최종 raster 해시에 연결된 annotation으로 socket을 확정해야 한다. 이 초기 구현에서 legacy landmark 출력을 수정하지 않는다.

## Fork release 정책

[Release workflow](../../.github/workflows/release.yml)의 tag 게시 job은 `aldegad/sprite-gen`에서만 자동 실행한다. fork의 `v*` tag push는 게시를 실행하지 않는다. `workflow_dispatch`는 각 이벤트의 `github.repository`를 대상으로 기존 `--dry-run`을 수행한다. release script의 upstream repository 기본값에 fork 작업을 맡기지 않는다.

[정책 테스트](../../tests/release/test_fork_release_policy.py)는 실제 workflow 조건에 fork/upstream/tag/dispatch 입력을 적용하고, repository 인자와 dispatch의 dry-run을 확인한다. 패키지 이름, upstream release 동작 및 CI 검증 범위는 유지한다. fork 배포를 켜거나 별도 distribution을 게시하는 기능은 이번 구현에 없다.
