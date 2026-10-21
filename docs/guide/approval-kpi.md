# 승인 원장 KPI (K9) 사용법

`automation/approval_kpi`는 **읽기 전용 계측기**다. 원장을 열어 kind별 승인 부담(건수·대기
p50/p95·재요청률)을 계산하고, 정적 TTL/리마인더 표와 나란히 출력한다. 쓰기·게시·승인 요청을
하지 않으므로 **소유자 승인이 필요 없고**, 원장 파일도 건드리지 않는다.

## 무엇을 읽나

| 포맷 | 위치 | 만든 곳 | 뽑는 값 |
| --- | --- | --- | --- |
| skill-gate 승인 로그(JSONL) | 노드 기본값 `/srv/autophagy-agents/logs/approvals.jsonl` (`APPROVAL_LOG_PATH`로 덮어씀) | `automation/skill_gate.py:52`, 레코드는 같은 파일 198행 근처 / 메일은 `skills/mail/scripts/gmail_approval_gate.py`의 `approval_record` | `action`→kind, `approval.channel`→surface, `result.status`→decision, `approval.method`→수동 반응 여부, `target_id`→request_key |
| PostingJournal 예약(JSON) | 각 생산자가 넘긴 gate 디렉터리 아래 `*.posting.json` (예: `~/.hermes/skill-gate` 계열) | `automation/interop/approval_lease.py`의 `PostingJournal.reserve` | `key` 접두사→kind, `at`→요청 시각(미결이라 decision 없음) |

요청 시각은 원장에 따로 없다. 그래서 승인 메시지 id(Discord snowflake)에서 게시 시각을 복원한다 —
`automation/supply_chain_remind.py:91`이 리마인더에서 쓰는 것과 **같은 계산**이다. snowflake가
아닌 message_id는 대기 시간을 알 수 없으므로 건너뛰고 `no-request-time`으로 센다.

건너뛴 레코드는 절대 추정하지 않고 이유별로 집계해 출력한다: `malformed`(JSON 파손),
`unknown-action`(정체 모를 action), `e2e-injected`(E2E 주입 승인), `unknown-kind`(kind를 못 읽는
journal key), `no-request-time`, `malformed-timestamp`.

## 노드에서 실행

```bash
# 로그와 저널이 함께 있는 상위 디렉터리를 주면 재귀로 찾는다
python3 -m automation.approval_kpi --root /srv/autophagy-agents
python3 -m automation.approval_kpi --root /srv/autophagy-agents --json   # 기계 판독용
```

- 루트가 없거나 읽을 레코드가 하나도 없으면 `no records`를 찍고 **종료 코드 0**이다(cron에서 안전).
- 출력 예:

```
| kind | count | decided | per_day | p50_s | p95_s | re-request | manual | ttl_s | reminder |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| skill-deploy | 5 | 5 | 5.00 | 900 | 7200 | 0.40 | 1.00 | UNKNOWN | yes |

skipped: none
no TTL and no reminder: mail-compose, coordination, obsidian-write
```

- `per_day` = 건수 ÷ 관측 구간(첫 요청~마지막 요청, 최소 1일로 올림).
- `p50_s`/`p95_s` = **결정된** 건의 요청→결정 초를 nearest-rank로 뽑은 값(보간 없음). 결정 시각이
  원장에 없으면 `n/a`이며, 모르는 대기를 0초로 세지 않는다.
- `re-request` = 같은 `request_key`가 두 번 이상 나온 이벤트 비율. 같은 건으로 소유자를 몇 번 더
  불렀는지를 뜻한다.

## 정적 TTL / 리마인더 표 (K9 kind 표)

상수를 **직접 읽어** 만든 표다(`automation/approval_kpi/policy_table.py`). 소스가 말하지 않는 칸은
추측하지 않고 `UNKNOWN` + 살펴본 파일을 적는다.

| kind | ttl_seconds | 출처 | reminder | 출처 |
| --- | --- | --- | --- | --- |
| todo | 86400 | `skills/todo/scripts/todo_approval_store.py:15` | yes | `skills/todo/scripts/todo_confirm_reaction_watch.py:141` |
| repair | 86400 | `automation/repair/repair_ops_reaction_watch.py:40` | yes | `automation/repair/repair_ops_reaction_watch.py:143` |
| calendar | UNKNOWN | `skills/calendar/scripts/calendar_confirm.py` 확인함 | yes | `skills/calendar/scripts/confirm_reaction_watch.py:323` |
| mail-reply | 900 | `skills/mail/scripts/triage_gate_gmail.py:20` (Gmail 발송 게이트가 승인 레코드에 stamp 하는 `expires_at`) | UNKNOWN | `skills/mail/scripts/mail_triage_watch.py` 확인함 |
| mail-compose | UNKNOWN | `skills/mail/scripts/triage_approval.py` 확인함 | UNKNOWN | `skills/mail/scripts/mail_triage_watch.py` 확인함 |
| skill-deploy | UNKNOWN | `automation/skill_gate.py` 확인함 | yes | `automation/supply_chain_remind.py:151` (supply-chain 틱이 미응답 deploy의 reminder slot claim) |
| coordination | UNKNOWN | `skills/coordination/scripts/coordination_lifecycle.py` 확인함 | UNKNOWN | 같은 파일 확인함 |
| obsidian-write | UNKNOWN | `automation/plaud_sync/sync.py` 확인함 | UNKNOWN | 같은 파일 확인함 |

리마인더 간격 자체는 kind별이 아니라 전역 설정이다 — `config.yaml`의 `approval_reminders`
(기본 initial 3h / repeat 1h, `automation/interop/approval_reminder_config.py:22-23`).

`automation/memory_relocate`는 표에 없다: 승인 kind를 페이로드에서 읽으므로
(`automation/memory_relocate/model.py:193`) 정적으로 이름을 정할 수 없다.

**TTL도 리마인더도 없는 kind**: `mail-compose`, `coordination`, `obsidian-write` — 이 셋은 요청이
무응답이면 만료도 재알림도 없이 그대로 매달려 있는다.
