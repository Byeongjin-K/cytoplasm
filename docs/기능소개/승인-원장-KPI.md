# 승인 원장 KPI (K9)

> 이력: budget 스킬과 30분 워처 `budget-watch`는 2026-10-22 형의 요청으로 제거됐다([과제비 감시 제거](과제비-감시-제거.md)). 아래는 당시 기록이며 적힌 경로는 더는 없다.

## 무엇을

이미 쌓여 있는 승인 원장을 열어 **kind별 승인 부담**을 숫자로 뽑는 읽기 전용 모듈이다. 건수,
하루 평균, 요청→결정 대기의 p50/p95, 같은 건을 다시 물어본 재요청률을 계산하고, 상수를 직접
읽어 만든 **정적 TTL/리마인더 표**와 함께 마크다운(또는 JSON)으로 출력한다.

읽는 포맷은 두 가지다. skill-gate가 append 하는 `approvals.jsonl`(`automation/skill_gate.py:52`)
과 `PostingJournal`이 남기는 `*.posting.json` 예약(`automation/interop/approval_lease.py`). 요청
시각은 승인 메시지 id(snowflake)에서 복원하는데, 리마인더가 쓰는 계산과 같다
(`automation/supply_chain_remind.py:91`). 해석이 확실하지 않은 레코드 — 파손·정체불명 action·E2E
주입·kind 미상 — 는 **추측하지 않고 건너뛴 뒤 이유별로 센다**.

## 왜

`release` 말고 다른 kind의 승인 부담은 아무도 재본 적이 없다. 하루에 소유자를 몇 번 부르는지,
✅까지 얼마나 걸리는지, 같은 건을 몇 번 다시 올리는지 모르는 채로 TTL과 리마인더만 늘어났다.
게다가 kind마다 정책이 제각각이라 — 어떤 kind는 24시간 TTL과 리마인더가 다 있고, 어떤 kind는
둘 다 없어 요청이 무한정 매달린다 — 어디를 먼저 손봐야 할지 판단할 근거가 없었다. 이 모듈은
그 판단 근거를 만드는 계측기다. 계측기 자신이 승인을 만들면 관측이 오염되므로, 쓰기·게시는
전혀 하지 않고 소유자 승인도 필요 없다.

## 사용 시나리오

- **주간 점검**: `python3 -m automation.approval_kpi --root /srv/autophagy-agents`로 kind별 표를
  뽑아, 어떤 kind가 소유자 탭을 가장 많이 먹는지 본다.
- **정책 구멍 찾기**: 출력 마지막 줄 `no TTL and no reminder`가 만료도 재알림도 없는 kind를
  이름으로 알려준다(현재 `mail-compose`, `coordination`, `obsidian-write`).
- **재요청 추적**: `re-request` 열이 높은 kind는 요청이 한 번에 끝나지 않는다는 뜻 —
  supersede/재게시 경로를 볼 차례다.
- **cron 안전**: 원장이 아직 없거나 비어 있으면 `no records`만 찍고 종료 코드 0이라 스케줄에
  그대로 걸 수 있다.
- **기계 소비**: `--json`으로 같은 값을 dict로 받아 다른 리포트에 붙인다.

## 관련 파일

- 코드: `automation/approval_kpi/{model.py, readers.py, aggregate.py, policy_table.py, __main__.py}`
- 사용법·kind 표: [승인 원장 KPI 사용법](../guide/approval-kpi.md)
- 읽는 원장의 생산자: `automation/skill_gate.py`, `automation/interop/approval_lease.py`,
  `automation/interop/approval_lifecycle.py`
- 정책 상수 출처: `skills/todo/scripts/todo_approval_store.py`,
  `automation/repair/repair_ops_reaction_watch.py`, `skills/budget/scripts/budget_confirm.py`,
  `skills/mail/scripts/triage_gate_gmail.py`, `automation/interop/approval_reminder_config.py`
- 강제: `tests/unit/test_approval_kpi.py`
