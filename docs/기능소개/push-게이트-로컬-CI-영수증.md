# push 게이트 — 로컬 CI 영수증

## 무엇을

브랜치를 push 하려면 **그 트리가 로컬 CI 를 통과한 영수증**이 있어야 한다. 없으면 `git push` 가 훅에서 거부되고, 무엇을 실행해야 하는지 그 자리에서 알려준다.

스킬 시나리오의 프로세스 격리에는 Linux `bubblewrap`이 필요하다. Ubuntu에서는
`sudo apt-get install bubblewrap`으로 준비한다. GitHub 러너는 검증 전에 설치하지만(sha256 을
고정한 `.deb` 내려받기, 실패하면 원래 `apt-get` 경로),
로컬 CI는 개발 환경을 임의로 바꾸지 않고 `bwrap`이 없으면 안내와 함께 중단한다.
단위 시험은 CI 와 같이 `-n auto`(pytest-xdist)로 모든 코어에서 돈다. 로컬 CI 를 돌리는 `python3` 에
`requirements-dev.txt` 의 `pytest-xdist` 고정판이 없으면 단계를 시작하기 전에 설치 명령 한 줄을
알려 주고 중단한다(예: `uv pip install --python "$(command -v python3)" pytest-xdist==3.6.1`).
격리를 끄거나 시나리오를 건너뛰어 영수증을 발급하지 않는다.
Ubuntu 24.04에서 `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`가
나면 AppArmor의 사용자 네임스페이스 정책도 확인해야 한다. CI는 배포판의
`apparmor-profiles`가 제공하는 `bwrap-userns-restrict` 프로필을 설치·로드한 뒤
`--unshare-all` 실행을 먼저 검사한다. 이 프로필은 bwrap의 격리 구성 단계에 필요한
권한을 허용하고 실행되는 프로그램에서는 거둔다. 호스트 전체의 AppArmor 제한을
끄거나 `--share-net`으로 네트워크 격리를 없애지 않는다.

```
$ git push -u origin cxsess/my-work
[pre-push] REFUSED — refs/heads/cxsess/my-work has no valid local CI receipt.
  [local-ci] no local CI receipt for tree 2a44db6a… — run: automation/local_ci.sh run
  Run: automation/local_ci.sh run
```

## 왜

이 저장소는 **브랜치 보호를 쓸 수 없다** — private + Free 조합이라 `gh api repos/.../branches/main/protection` 이 403 이다. 즉 GitHub CI 는 머지를 막을 권한이 없는 **권고**였고, 2026-08-25 에 실제로 빨간 CI(사실은 결제 문제로 잡이 시작조차 못 한 것) 위에서 머지가 이뤄졌다.

"PR 전에 CI 를 돌려라"를 산문으로 적는 방법도 있었지만, 이 저장소는 같은 교훈을 이미 두 번 코드로 바꿨다 — 배포 provenance 가드와 ops 체크아웃 커밋 거부 훅이다. 후자의 주석이 그대로 이유다: *"조건부 거부는 예외를 아는 사람에게만 통하는데, 사고를 낸 주체는 언제나 예외를 모르는 쪽이었다."* 지침은 잊히고, 영수증은 안 잊힌다.

## 사용 시나리오

**정상 경로** — 커밋한 뒤 한 번 돌리고 push 한다.

```
automation/local_ci.sh run      # lint → unit tests(-n auto) → public-gate → clean-host 컨테이너 검사
git push -u origin <branch>     # 영수증이 있으므로 통과
```

`run` 은 GitHub 워크플로가 돌리는 것과 같은 세트를 돌리고, **전 단계가 통과했을 때만** 영수증을 `~/.hermes/local-ci/<tree>.json`(0700/0600) 에 쓴다. 어느 단계든 실패하면 영수증은 만들어지지 않는다.

**문구만 고쳐 amend 한 경우** — 영수증 키가 commit 이 아니라 **tree** 라서 그대로 유효하다. 내용이 한 글자라도 바뀌면 즉시 무효다.

**워크플로가 바뀐 경우** — 영수증에 `.github/workflows/ci.yml` 의 sha256 이 들어 있어, 검증 세트가 바뀌면 그 전에 받은 영수증은 무효가 된다.

**더러운 트리에서 돌린 경우** — 거부한다. 추적 파일이 수정된 채로 발급하면 영수증이 "이 트리가 통과했다"고 거짓말하게 되고, 실제로 검사한 것은 다른 내용이다.

**게이트를 모르는 옛 브랜치** — 게이트가 생기기 전에 딴 브랜치는 리포 안에 구제 수단이 없으므로 **경고 후 통과**시킨다(판정 불가는 위반이 아니다). 경고는 매 push 마다 나오고, main 에 rebase 하면 게이트가 적용된다.

**릴리스 태그 push** — 대상이 아니다. 태그는 이미 main 에 착지한 커밋을 가리키고, 그 착지는 PR 검토와 브랜치 push 게이트가 이미 판정했다. 여기서 한 번 더 막으면 보호되는 것 없이 릴리스 리컨실러만 선다.

**진짜 예외** — `LOCAL_CI_ALLOW_UNVERIFIED=1 git push …`. **샌드박스/실험 전용**이다. 통과시키려고 상습적으로 쓰면 게이트가 무의미해진다(`DEPLOY_ALLOW_UNPUSHED` 와 같은 성격).

## 같이 바뀐 것: CI 비용

`.github/workflows/ci.yml` 에서 **main push 트리거를 제거**했다. PR 에서 이미 통과시킨 트리를 머지 직후 한 번 더 돌리는 중복이었고, 최근 100 회 실행 중 **48 회**가 그것이었다(2026-08-25 실측). 월 3,890~4,862 분 → 약 2,000 분으로 줄어 Free 포함분 안에 들어간다. `pull_request` 실행은 그대로 남는다 — 깨끗한 러너에서의 독립 검증은 로컬 영수증이 대신할 수 없다.

## 한계 (알고 쓰는 것)

- 영수증이 증명하는 것은 "이 트리가 **이 기계에서** 통과했다"이지 "깨끗한 호스트에서 통과한다"가 아니다. 그 간극은 clean-host 단계(빈 `python:3.12-slim` 컨테이너에서 설치기 dry-run)가 메우고, 완전히는 못 메운다.
- 훅은 `automation/worktree.sh start` 가 설치한다. 훅을 설치한 적 없는 clone 에는 게이트가 없다 — gitleaks pre-commit 훅과 같은 성질이다.
- CI 가 설치하는 `pip install -r requirements-dev.txt` 는 로컬에서 **실행하지 않는다**(개발자 환경을 변형시키므로). 대신 실제로 쓴 도구 버전을 영수증에 기록하고, 이 예외는 `tests/unit/test_local_ci_push_gate.py` 의 `_LOCAL_ONLY` 에 사유와 함께 등록돼 있다.

## 관련

- 구현 `automation/local_ci.sh` · 훅 `automation/hooks/pre-push` · 설치 `automation/worktree.sh`
- 회귀 `tests/unit/test_local_ci_push_gate.py` — 영수증 무결성·훅 거부·드리프트 가드(로컬 세트가 워크플로에서 갈라지면 실패)
- 규약 `AGENTS.md` 「PR 전 검증 규칙」
