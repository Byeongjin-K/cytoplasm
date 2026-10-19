# 단위 시험 병렬 실행과 샌드박스 설치 고정본 — 변경 한 건의 CI 대기를 줄인다

## 무엇을

단위 시험(`tests/unit`, 약 13,240개)을 로컬 CI와 GitHub `verify` 둘 다 `pytest -n auto`(pytest-xdist)로 모든 코어에서 돌린다. GitHub `verify`는 시나리오 샌드박스(bubblewrap)를 매번 `apt-get update`로 설치하지 않고, sha256을 고정한 `.deb`를 받아 설치한다. 시험·검사는 하나도 빠지지 않는다.

## 왜

변경 한 건이 머지되기까지 약 25~27분이 걸렸다. 그중 23분 이상이 같은 단위 시험을 1코어로 두 번 도는 시간이었다(2026-10-19 실측). 로컬 CI의 단위 시험이 549~563초였고, GitHub `verify`는 590~908초에 샌드박스 설치 102~111초가 더해졌다. 기계는 20코어, 러너는 4 vCPU인데 둘 다 1코어만 썼다.

시험을 덜 돌려 시간을 사는 방법(바뀐 파일만 시험, GitHub가 로컬 영수증을 믿고 단위 시험 생략)은 쓰지 않는다. 저장소 전체를 훑는 conformance 시험이 많아, 고르는 순간 깨진 코드가 main에 들어갈 길이 생긴다.

## 사용 시나리오

**정상 경로.** 커밋한 뒤 `automation/local_ci.sh run`을 돌린다. 단위 시험 단계가 모든 코어에서 돌고, 전 단계가 통과하면 영수증이 나온다. push하면 GitHub `verify`도 같은 명령을 4코어로 돌린다. 필수 검사 4개, push 전 영수증, `automation/merge-pr.sh` 판정은 그대로다.

**로컬 파이썬에 플러그인이 없을 때.** `local_ci.sh`가 어떤 단계도 시작하기 전에 설치 명령 한 줄을 알려 주고 멈춘다. 영수증은 나오지 않는다. 로컬 CI는 개발 환경에 도구를 직접 설치하지 않는다(bubblewrap과 같은 원칙). `requirements-dev.txt`의 고정판을 그 `python3`에 넣은 뒤 다시 돌린다.

**고정 `.deb`를 받을 수 없을 때.** 내려받기, sha256 대조, 설치 중 하나라도 실패하면 경고 한 줄을 남기고 원래 `apt-get update` + `apt-get install` 경로로 설치한다. 그 뒤의 AppArmor 프로필 적재와 `bwrap --unshare-all` 확인은 두 경로가 같다. 고정본도 `apt-get`으로 설치한다. `dpkg -i`는 의존성이 빠지면 패키지를 반쯤 풀어 둔 채 실패하고, 그러면 apt 폴백까지 깨진다(컨테이너 실측).

**병렬에서만 깨지는 시험이 생기면.** 순서나 공유 상태에 기대는 시험이다. 끄지 않고 고친다(`tests/AGENTS.md` 「규칙」).

## 관련

- 구현: `.github/workflows/ci.yml`(`verify`의 샌드박스 설치·Unit tests), `automation/local_ci.sh`, `requirements-dev.txt`(`pytest-xdist`·`execnet` 고정)
- 드리프트 가드: `tests/unit/test_local_ci_push_gate.py` — 워크플로가 돌리는 명령이 로컬 세트에 그대로 있는지 대조한다
- 배포 대상 아님: CI·로컬 CI·개발 의존성 변경이라 노드에 반영할 것이 없고, 릴리스도 필요 없다
- 관련 소개: [push 게이트 — 로컬 CI 영수증](push-게이트-로컬-CI-영수증.md), [CI·테스트 위생](ci-테스트-위생.md)
