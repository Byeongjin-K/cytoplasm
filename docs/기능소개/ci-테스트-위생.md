# CI·테스트 위생 (G4)

## 무엇을
CI와 단위 테스트가 새 체크아웃, 선택적 vendor 의존성, 반복 실행에서도 같은 결과를 내도록 정리한다.
루트 개발 의존성을 고정하고, 수집 중단·임시 디렉터리 누적·환경변수 상속에 따른 거짓 통과를 막는다.

## 왜
기존 스위트는 로컬에 우연히 설치된 PyYAML과 현재의 얕은 `mailon` import 면에 기대고 있었다.
또한 `land.sh` 경합 테스트는 실행할 때마다 `/tmp`에 git clone을 남겼고, owner-DM drain check
테스트는 부모의 `PYTHONPATH`에 따라 fail-closed 단언이 반대로 바뀌었다. 이 상태에서는 CI green이
격리된 실행을 증명하지 못한다.

## 사용 시나리오

### 정상 경로
1. 기여자가 새 체크아웃에서 `requirements-dev.txt`를 설치한다.
2. `python3 -m pytest tests/unit -q -n auto`와 `ruff check . --exclude skills/mail/vendor`를 실행한다
   (`-n auto` 는 pytest-xdist 로 모든 코어를 쓴다 — [단위 시험 병렬](ci-단위-시험-병렬.md)).
3. 선택적 `mailon` 의존성이 있으면 메일 compose 검증을 실행하고, 없으면 그 모듈만 skip한다.
4. 전체 스위트가 끝나도 `land.sh` 경합용 git clone은 pytest의 `tmp_path`와 함께 회수된다.

### 실패·격리 경로
- `mailon` 또는 그 하위 import가 없으면 전체 수집을 중단하지 않고 해당 테스트 모듈만 skip한다.
- 부모 셸의 `PYTHONPATH`가 비어 있거나 리포 루트를 가리켜도 drain check 테스트는 자식에게 fixture
  경로를 명시해 동일하게 판정한다.
- 이 수정은 **프로덕션 drain check 동작을 바꾸지 않는다**. 프로덕션 스크립트는 subprocess나
  `env=`를 사용하지 않고 현재 프로세스에서 직접 import한다. 결함은 테스트 하네스에만 있었다.

## 관련
- 계획: `.omo/plans/parallel-followup-sweep.md` §5 G4
- CI·의존성: `.github/workflows/ci.yml`, `requirements-dev.txt`
- 테스트: `tests/unit/test_mail_compose_form_verify.py`, `tests/unit/test_land_ops_sync.py`,
  `tests/unit/test_owner_dm_drain_check.py`
- lint 정리: `automation/interop/loop_guard.py`
- 승인·배포: 관여하지 않음(테스트·CI 위생 변경이며 배포 대상 없음)
