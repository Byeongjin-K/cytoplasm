# Google Tasks 소유자 명령 쓰기

## 무엇을
계정 안 할 일 등록은 dori-gate가 확인한 소유자 Discord 명령으로 바로 처리한다.
새 승인 카드는 만들지 않는다. `todo_cli create`와 `write-now`는 같은 경로이며
등록 직후 `gws tasks tasks get`으로 제목·ID를 재조회한다.

## 왜
모든 쓰기에 카드를 요구하던 규칙은 은퇴했다. 새 할 일에 승인 세대를 만드는
`request` 명령은 제거하되, 과거 카드의 일회 실행·변조 방지 계약은 보존한다.

## 사용 시나리오
소유자가 “회의 준비를 할 일에 넣어줘”라고 Discord에서 지시하면 YooDori가
dori-gate로 원문을 확인하고 원 채널·메시지 좌표와 함께 즉시 쓰기 경로를 호출한다.
등록과 API 재조회가 일치하면 결과와 변경 영수증을 남긴다. 승인 카드 게시나
추가 반응을 기다리지 않는다.

원문 좌표가 없거나 소유자 명령을 확인할 수 없으면 쓰지 않는다.
같은 명령의 중복 실행이나 중간 `write_started`는 자동 재삽입하지 않고
영수증 확인 대상으로 남긴다. 재조회가 다르면 성공으로 보고하지 않는다.

## 관련
- `skills/todo/scripts/todo_cli.py`, `todo_owner_command.py`
- `todo_confirm_reaction_watch.py`, `todo_execution_claim.py`: 이미 저장된 승인 세대의
  배수 호환만 유지하며 새 CLI 승인 생산자는 없다.
- `automation/interop/effect_approval.py`: 과거 카드의 정확한 해시·소유자 검증
- `tests/unit/test_todo_write_now.py`, `tests/unit/test_todo_skill.py`
- [현재 승인 범위](승인-범위-정렬.md)
