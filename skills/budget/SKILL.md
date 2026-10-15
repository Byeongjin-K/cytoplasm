---
name: budget
description: "과제비 잔액·집행 내역을 묻거나 과제비 변경에 따른 요청 메일을 보내야 할 때 이 스킬을 사용. 과제비 원장(W0-10 Google Sheet) 조회 + 변경 감지 스킬 — 과제별×년도별 다중 시트 레지스트리(~/.hermes/budget/sheets.json) 지원. `!budget [항목]`은 게이트 없이 즉시 조회. 변경 감지 cron(30분)이 잔액 탭을 SQLite 스냅샷과 diff해 변경 시 규정 요청메일 초안을 만들고, 발송은 반드시 그 요청 전용 승인 스레드(`과제비 메일 · <제목>`)의 승인 메시지에서 cha 본인의 ✅/⛔ 리액션 확인(봇이 두 반응을 미리 추가, 제약 1) 이후에만 일어난다. 승인 표면은 `approval_surface.py` 정책과 초안에 저장된 바인딩으로 결정된다. W4-3."
version: 1.5.5
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Budget, GWS, Approval-Gate, Autophagy]
prerequisites:
  commands: [python3]
---

# 과제비 조회 + 변경 감지 (budget)

cha의 과제비 원장 Sheet를 비공개 런타임 설정으로 지정해 gws CLI(OAuth, W0-6)로 읽는다.
시트 지정은 두 모드다: 레지스트리 `~/.hermes/budget/sheets.json`(과제별×년도별 다중 시트,
`BUDGET_SHEETS_FILE` 오버라이드)이 있으면 그것이 유일한 소스이고, 없으면 기존
`BUDGET_SHEET_ID` 단일 시트 모드다(완전 호환). 레지스트리가 있는데 `BUDGET_SHEET_ID`가
미등재면 fail-closed(exit 3)다. `configs/budget-sheet.md`·`configs/budget-sheets.example.json`
에는 공개용 형식 예시만 둔다. Sheet는 읽기 전용 — 값 수정 권한은 오너(cha)에게만 있다.
설정이 없으면 조회·감지는 fail-closed로 중단된다. 변경(mutating) 명령은 `/srv/autophagy-skills/live/budget/scripts/` 밖의 사본에서 `STALE-SKILL-COPY-BLOCK`으로 실행을 거부한다.

## 절대 규칙 (안전)

1. **Sheet를 절대 수정하지 마라**: 이 스킬의 모든 경로는 읽기 전용이다.
   `gws sheets ... update/batchUpdate`를 실행하지 않는다.
2. **승인 전 발송 금지**: 요청 메일은 오직 `budget_cli.py confirm`/cron `watch`
   (소유자 ✅/⛔ 리액션 검증 내장, ⛔ 우선)로만 발송된다. `gws gmail +send`를 터미널에서
   직접 실행하지 마라 — pre_tool_call 외부효과 게이트(gws_gmail_send)가 승인
   없는 호출을 차단한다.
3. **금액을 공개 채널에 올리지 마라**: 조회 결과(금액/잔액)는 cha의 DM으로만
   전달한다. 승인 요청 메시지의 금액은 CLI가 자동 마스킹한다.

## 명령 (CLI = `python3 /srv/autophagy-skills/live/budget/scripts/budget_cli.py …`)

### 1) `!budget [항목]` — cha가 DM으로 물어볼 때 (게이트 불요, 즉시)

```bash
python3 /srv/autophagy-skills/live/budget/scripts/budget_cli.py query            # 전체 항목
python3 /srv/autophagy-skills/live/budget/scripts/budget_cli.py query --item 재료비
python3 /srv/autophagy-skills/live/budget/scripts/budget_cli.py query --project 무인굴착기 --year 2026
python3 /srv/autophagy-skills/live/budget/scripts/budget_cli.py sheets           # 활성 시트 목록 (ID 마스킹)
```

레지스트리 모드에서는 `--project`로 과제를 고른다 — 다과제인데 지정하지 않으면 exit 2로
알려진 과제 목록을 안내하고, `--year` 생략은 그 과제의 최신 년도다. `BUDGET-OK` 끝의
`sheet=<과제>/<년도>`가 어느 시트를 읽었는지 말한다.

출력의 `ROW {...}` JSON 라인들(항목/예산/집행액/잔액/최종수정)을 읽기 좋은
표로 정리해 cha의 DM으로 답한다. `BUDGET-OK n=…`이 없으면 실패다 — exit 4는
Sheet 접근 실패이니 stderr의 `SHEET-FAIL …` 내용을 그대로 cha에게 보고한다.
`NOT-FOUND`(exit 2)면 알려진 항목 목록을 안내한다.

### 2) 변경 감지 파이프라인 — cron이 자동 수행 (30분, budget-watch)

`snapshot [--origin-channel-id <채널ID>] [--origin-message-id <메시지ID>]`이 잔액 탭(7행 이후)을
SQLite 이전 스냅샷과 diff하고, 변경이 있으면 규정 요청메일 초안을 만들어 **그 요청 전용
스레드에 마스킹된 승인 요청을 게시**한다(origin 인자는 스레드 앵커이자 결과 통지 목적지 — §3).
같은 변경은 claim 키로 멱등 — 초안이 중복 생성되지 않는다.

레지스트리 모드에서는 tick이 **등록된 전 시트**를 순회한다. 스냅샷 스트림·claim 키·승인
요청 키가 시트별(`<과제>/<년도>` 스코프)로 격리되어 같은 tick에 두 과제가 변해도 서로의
승인 메시지를 대체하지 않으며, 승인 요청·요청 메일에 대상 과제/년도가 표기된다. 한 시트의
읽기 실패는 그 시트만 재시도 큐(`[<과제>/<년도>] …`)에 남기고 나머지는 계속 처리한다
(실패가 있으면 exit 4).

게시는 공유 승인 생명주기(`budget_approval` → `automation/interop/approval_lifecycle.py`)를
거치며 승인 키 `budget:{mail_to}`에는 **라이브 승인 메시지가 항상 1건뿐**이다: 같은 초안·같은
해시의 재요청은 아무것도 게시하지 않고, 더 최신 원장 변경이 오면 옛 메시지를 **먼저 삭제한
뒤** 그 초안을 superseded로 내리고 새로 1건만 게시하며, 메시지가 사라졌으면 재게시한다.
cha가 이미 ✅/⛔ 한 요청은 파괴하지 않고 다음 tick이 소비하도록 연기한다. 초안의
`message_id`와 승인 바인딩(`surface`/`channel_id`/`policy_version`/`approval_thread_id`)은 이
게이트의 commit만 기록하고, 초안을 읽지 못하면 거부한다(fail-closed). **새 요청은 요청마다
자기 스레드를 연다** — 이름은 `과제비 메일 · <메일 제목>`(금액·잔액 없음)이고, 지시가 승인
채널에서 왔고 `--origin-message-id`를 넘겼으면 그 지시 메시지에 앵커한다. 이전 정책 버전에
저장된 초안은 레코드에 적힌 원래 표면에서 그대로 소비된다.
에이전트 턴과 30분 `budget-watch` tick은 `~/.hermes/budget-gate/approval-leases/`의 flock으로
직렬화된다 — 진 쪽은 아무것도 바꾸지 않고 `deferred:lease-held`로 물러난다.

### 3) 반응 확인과 발송 — cha가 요청 스레드의 승인 메시지에 ✅/⛔ 리액션

초안 게시 직후 봇이 해당 승인 메시지에 ✅와 ⛔를 이 순서로 미리 추가한다.
cha는 **그 메시지에** ✅로 확정하거나 ⛔로 취소한다. 다음 cron tick의 `watch`가
소유자 반응(봇/타인 거부), 초안 sha256 결합, ⛔ 우선을 독립 재검증한다. ✅만 유효하면
approvals.jsonl 기록 후 `gws gmail +send`를 실행하며, 유효한 ⛔면 초안을 폐기한다.

**결과 통지 (소유자 메시지 봉투)**: 발송 완료/취소/만료 결과는 `OwnerMessage`로 전달한다.
대상은 제목·draft id, 사실은 기존 제목·수신자·사유 문구를 그대로 쓰며 **금액·잔액은 어떤
봉투 필드에도 싣지 않는다**. 실행 완료는 발송 성공 뒤에만 표시하고 종결 결과의 소유자 조치는
없다. 같은 요청 스레드에서는 자기 링크를 생략하고, 다른 표면으로 폴백하면 저장된
`approval_guild_id`·`approval_thread_id`·`message_id`로 승인 카드 URL을 만든다(조회 없음).
길드가 없는 옛 초안은 링크를 추측하지 않고 `Discord 검색`의 draft id로 찾게 한다. 봉투 모듈이
없거나 파사드가 `message=`를 지원하지 않으면 기존 문자열 바이트를 그대로 보내며, 렌더 실패도
같은 경로로 내려간다. 결과는 **승인 요청이 있던 바로 그 요청 스레드**
(`approval_thread_id`)에 게시되고, 그 스레드는 상태 접두어로 이름이 바뀐 뒤(`✅ 완료 · …` /
`⛔ 취소 · …`) 아카이브된다 — 열려 있는 스레드 목록이 곧 진행 중인 요청 목록이다. 만료된
요청은 승인 스레드를 `⌛ 만료 · …`로 닫는다. cha가
**채널에서 요청**해 에이전트가 `snapshot`을 실행하는 경우 `--origin-channel-id <채널ID>`(지시
메시지 id를 알면 `--origin-message-id`도)를 전달한다 — 그 지시 메시지가 승인 채널에 있으면
요청 스레드가 거기에 앵커된다. 스레드가 없는 옛 초안은 origin 스레드, 그마저 없으면 기존
소유자 통지 경로로 폴백한다. 스레드 게시 실패는 `NOTIFY-THREAD-FAIL` 후 폴백이고 이름 변경·
아카이브 실패는 `THREAD-CLOSE-FAIL`이며, 어떤 통지 실패도 tick을 깨지 않는다.
게시된 초안은 `DRAFT-CREATED …` 다음 줄에 `APPROVAL-THREAD draft=<id> url=<승인 스레드 링크>` 를
낸다 — 에이전트가 `snapshot` 결과를 소유자에게 전할 때 **그 `url=` 값을 그대로 붙인다**(2026-09-23
소유자 지시). `url=unavailable search=<id>` 이면 링크를 지어내지 말고 draft id 로 검색하라고 적는다.

승인이 없으면 pending 상태이며 **아무것도 발송되지 않는다**. CTA의
`실행/취소 <id>`는 문서화된 대체 수단일 뿐, 기본 확인은 리액션이다. 소유자에게
텍스트 답장이나 별도 텍스트-confirm DM을 요청하지 않는다.

### 4) 상태 확인

```bash
python3 …/budget_cli.py list-drafts    # 초안 목록 (pending/executed)
python3 …/budget_cli.py retry-queue    # Sheet 접근 실패 재시도 큐
```

Sheet 접근 실패는 조용히 사라지지 않는다: 오류가 보고되고 재시도 큐에
적재되며, 다음 성공 tick이 큐를 resolve한다.

**재시도 큐 의미 (2026-08-24 고정)**: `SHEET-FAIL` 한 건이 `retry_queue`에 한 행을 쌓고,
다음 성공 tick의 `snapshot`이 **열린 행 전부를 한 번에** resolve한다(`RETRY-RESOLVED n=…`).
즉 이 큐는 실패했던 조회를 되돌려 재생하는 replay 큐가 아니라 "언제부터 언제까지 Sheet에
닿지 못했나"를 남기는 **인시던트 표식**이다 — 다음 성공 스냅샷이 현재 잔액을 그대로 읽으므로
개별 재생은 필요 없다. 회귀 고정: `tests/unit/test_budget_retry_queue_consumption.py`.

### 5) 연속 실패 통지 (`budget-watch`, 임계치 3)

건강한 tick은 예전처럼 무음이다(빈 stdout + exit 0). 달라진 것은 실패가 이어질 때다:
`budget_watch.py`가 공유 헬퍼 `watch_failure_streak`로 스트릭을 세어 **3회 연속 실패에
도달한 그 tick에 1줄**, 이후 처음 성공하는 tick에 복구 1줄만 낸다. 30분 주기라 3회는 약
1.5시간 — 2026-08-23 23:30 Sheets 503처럼 다음 tick에 낫는 장애는 조용히 지나가고, 권한
회수나 탭 삭제처럼 지속되는 고장은 반드시 말한다. 스트릭에 **기록된** 실패 tick은 stdout이
비고 **exit 0**이다 — `--deliver discord`에서 스케줄러는 rc≠0이면 stdout과 무관하게 자체
실패 배너를 게시하므로(2026-08-24 18:30·20:30 KST 실측), 침묵은 기록된 실패의 exit 0으로만
산다. 마스킹한 상세는 임계치 notice 안에만 들어간다. 기록하지 못한 tick(헬퍼 미배포·record
예외)과 래퍼 크래시만 exit 1로 남아 스케줄러 배너가 최후 방어선이 된다.

**전달 경로**: deploy가 기존 job까지 `--deliver discord`로 수렴시키므로 임계치·복구 두 줄은
소유자에게 닿는다. 자식 stdout/stderr와 매 tick 진단 줄은 전달하지 않아 지속 장애도 사고당
개시 1건·복구 1건을 넘지 않는다. 헬퍼가 미배포된 경우에만 마스킹한 호환 폴백 한 줄을 낸다.

## Sandbox scenario

배포 파이프라인용 `scripts/scenario.sh`: 픽스처 Sheet로 query 패리티,
초안 무발송, 마스킹, fail-closed confirm, (어댑터 가용 시) 서명 주입 발송 +
승인 기록 + 발송 로그, Sheet 실패→재시도 큐 왕복을 검증하고
`SCENARIO-PASS`를 출력한다. 네트워크 호출·실시크릿 없음.
