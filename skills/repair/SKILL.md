---
name: repair
description: "오류나 스킬 실패를 고쳐 달라거나 수리 요청을 남기라고 할 때 사용. 오류·스킬 실패·헬스체크 실패를 레닥션된 개인 Kanban 수리 티켓으로 기록한다. 전체 로그는 ops 전용 경로에만 보관한다. W6-1."
version: 1.0.2
author: autophagy-agents
license: MIT
metadata:
  hermes:
    tags: [Repair, Kanban, Private-Logs, Redaction]
prerequisites:
  commands: [python3, hermes, ssh]
---

# repair — 오류 수리 티켓

`!repair`, “수리해줘”, “이상해” 요청은 모두 같은 수리 티켓 경로를 사용한다.
카드는 unassigned + `blocked/needs_input`으로 만들어져 dispatcher가 LLM worker를
시작하지 않는다.

## 절대 규칙

1. 입력 오류 전문은 Kanban, Discord, repo 또는 docs/qa에 출력하지 않는다.
2. 공개 표면에는 레닥션된 발췌, SHA-256, ops private-log 경로만 남긴다.
3. 동일한 오류 signature는 새 카드를 만들지 않고 기존 카드의 occurrence comment를 늘린다.
   수동 요청의 signature는 **메시지 전체**로 정해진다 — 첫 낱말이 같아도 내용이 다르면 다른 카드다.
4. 개인 수리는 자동 실행하지 않는다. 카드 상태는 사람이 검토할 때까지 blocked로 유지한다.
5. 명령은 **불변 릴리스 런타임의 사본만** 실행한다. 계정 홈 사본(`~/.hermes/repair/…`)은 배포
   선언에도 드리프트 프로브에도 없어 낡은 채 방치된다.

## Command

```bash
# !repair [증상]
python3 -I /srv/autophagy-agent-current/automation/repair/repair_cli.py manual "!repair [증상]"
```

명령 출력은 ticket id와 occurrence count만 포함한다.

## Ops 승인 적용 결과

소유자 승인 뒤 ops 적용 경로는 planner·sandbox 실행 전에 현재 패치의 승인 해시를
대조한다. 실제 Git 적용 직전에도 저장된 `patch_sha256`을 확인하고, 승인된 바이트를
stdin으로 적용한다. 내용이 달라지면 적용하지 않으며, 삭제·이름 변경은 양쪽 경로를
같은 diff 파서로 검사하고 함께 커밋한다.

`repair_ops_cli`의 exit 5는 승인 대기다. 워처는 이 결과를 성공 승인으로 기록하거나
대기 레코드를 회수하지 않는다. exit 3은 회귀 뱅크 차단, exit 4는 push 실패다.
적용·push 성공 뒤 `repair/t_<ticket>` → `main` PR을 생성하거나 열린 PR을 재사용한다.
JSON의 `pr_url`·`pr_error`와 자식 stderr는 워처 출력에도 남는다. PR 실패는 이미 끝난
적용을 취소하지 않으므로, 종료코드만 보지 말고 `pr_error`를 확인해 `gh` 설치·인증을
복구하고 해당 브랜치 PR을 생성한다. main 머지는 소유자가 한다.

PR 발행 성공 뒤에만 종결(done/archived) 티켓의 `task/`·`kanban/`·`wt/`·`fix/`·`repair/`
로컬 참조를 정리한다. `origin/main`에 병합되지 않은 커밋, 사용 중인 워크트리,
백업 참조와 원격 참조는 보존한다. 카드 조회 불가는 삭제 대신 `prune_error`로 남는다.
