---
name: coordination
description: "다른 사람과 미팅 시간을 맞춰 달라고 할 때(예: OO님과 미팅 잡아줘) 사용. 에이전트간 일정 조율 스킬 (W3-3). cha가 'OO님과 미팅 잡아줘'라고 하면 상대 에이전트에 §2 가용시간 질의 → 교집합 후보 ≤3개를 조회한다. 공개 v1에서는 상대 소유자 승인 경로가 없어 자동 합의·캘린더 등록을 하지 않으며, 실제 양측 승인 흐름은 W-F2.5-D(v2) 범위다. §2 조율 봉투(envelope)는 #autophagy-agents(인터롭 채널)에서 오가며, #team에는 간결한 확정 통지만 게시된다. 10분 무응답/후보 0개는 에스컬레이션 DM 후 종료(캘린더 쓰기 0건). 거절은 재협상 1회 후 종료. 조율은 피어+시간 범위(예: '오전') 요청 전용 — 피어가 명시돼도 '정확한 단일 시각'이면 본인 단독 일정이므로 request 진입 즉시 ROUTING-REJECT(exit 2)로 calendar로 되돌린다(피어 질의 전 차단). 상대 미지정 요청은 calendar 소유."
version: 1.2.7
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Coordination, Interop, Calendar, Approval-Gate, Autophagy]
prerequisites:
  commands: [python3]
---

# 에이전트간 일정 조율 (coordination)

변경을 수행하는 명령은 `/srv/autophagy-skills/live/coordination/scripts/` 밖의 사본에서 실행하지 않으며 `STALE-SKILL-COPY-BLOCK`으로 거부한다.

cha의 "OO님과 미팅 잡아줘" 요청을 인터롭 규약 §2.3(조율 프로토콜)으로 처리한다.

## 절대 규칙 (안전)

1. **양측 승인 전 캘린더 변경 금지**: 캘린더 쓰기는 오직 W3-1 calendar 스킬의
   confirm 게이트를 통해서만 일어난다. `gws calendar events …` 직접 실행 금지
   (pre_tool_call 외부효과 게이트가 차단).
2. **#team에는 간결한 확정 통지만**: §2 조율 봉투(envelope)는 #autophagy-agents(인터롭 채널)에서 교환하며, #team에는 액션 아이템/지시/멘션/질문을 게시하지 마라(봇 캐스케이드 위험 — W2-3 사고). 상세 결과는 지시가 온 채널의 스레드로 돌려보낸다(6번 규칙).
3. **일정 제목 등 캘린더 내용은 공개 채널에 게시하지 않는다** (§2 봉투 payload에도
   제목을 넣지 않는다 — slot 시각만).
4. **Deadlock 규칙**: 상대 무응답 10분(기본 600s) 또는 공통 후보 0개 → cha에게
   에스컬레이션 DM("인간 협의 필요") 후 종료. 캘린더 쓰기 0건. 거절 시 재협상은
   정확히 1회.
5. **정확한 단일 시각은 조율하지 않는다(calendar와 상호 가드)**: `request`는 피어에게
   질의를 보내기 전에 해속된 범위가 `--duration-min`과 같은 **정확한 단일 슬롯**인지
   검사한다(`_reject_calendar_intent`). 조율 의사(`조율`/`가능한 시간`/`물어봐` 등)
   없이 단일 슬롯이면 exit 2 `ROUTING-REJECT`로 calendar 스킬을 안내하고 종료한다 —
   피어 네트워크 I/O 전에 차단(선례 사고 2026-07-20: `오전 10시` 고정 요청이 07-29 09:00
   조율로 표류). 캘린더 쓰기 0건.
6. **요청 하나 = 스레드 하나**(2026-09-01 소유자 결정, 2026-08-23 지시의 후속): 조율
   승인 요청은 자기 스레드(`일정 조율 · <조율 라벨>` — #team 확정 통지가 이미 쓰는
   correlation, 없으면 pending id)에 게시되고, 완료·취소·만료 **결과**도 그 스레드로
   돌아간 뒤 이름 앞에 상태(`✅ 완료`/`⛔ 취소`/`⌛ 만료`)를 붙여 아카이브된다 — 열려
   있는 스레드 목록이 곧 진행 중인 조율 목록이다. `request`에 넘긴
   `--origin-channel-id`/`--origin-message-id`(pending 레코드에 저장됨)는 지시가 승인
   채널에서 왔을 때 그 메시지에 스레드를 앵커하고, 아니면 결과가 그 채널의 스레드로
   간다. 스레드가 없는 옛 레코드는 기존 소유자 통지 경로 그대로다. 종결 통지는
   `OwnerMessage`의 5필드로 렌더하며 기존 사실 문구와 실제 실행·취소·만료 상태를
   담는다. 완료에는 기존 제목을, 취소·만료에는 기존 draft id만 싣는다. 같은 스레드는
   자기 링크를 생략하고, 다른 표면 폴백은 저장된 길드·스레드·`dm_message_id`의 승인
   카드 링크를 싣는다. 길드 미상은 링크 대신 `Discord 검색`과 draft id를 쓴다.
   선택 봉투 모듈 또는 파사드 능력이 없는 옛 런타임은 기존 문자열을 그대로 보낸다.
   폴백 콜백은 이미 목적지별로 렌더된 문자열만 받는다. 3번 규칙(#team·§2 봉투에는
   제목 금지)과 피어 `team_notice`·`coord-` 프로토콜은 불변이다.
   스레드 게시 실패는 `NOTIFY-THREAD-FAIL`을 남기고 소유자 통지로
   폴백하고, 종결 표시 실패는 `THREAD-CLOSE-FAIL`만 남긴다. 결과 통지 실패가 CLI
   종료코드나 watcher tick을 바꾸지 않는다(라우팅은
   `automation.interop.origin_notice.deliver` 공유 구현이 소유).

## 사용 (CLI = `python3 /srv/autophagy-skills/live/coordination/scripts/coordinate_cli.py …`)

### 1) cha가 "피어와 내일 미팅 잡아줘" 요청 시

날짜를 손으로 계산하거나 ISO 날짜를 작성하지 않는다. `--when`에 cha가 말한
자연어 날짜/시간대(예: `내일 오후`)를 그대로 전달한다. 모호하면 cha에게 되묻기
(예: "내일 몇 시부터 몇 시 사이가 좋으세요? 길이는 30분?"), 실행:

```bash
python3 /srv/autophagy-skills/live/coordination/scripts/coordinate_cli.py request \
  --peer peer-test --summary "피어 미팅" \
  --when "내일 오후" \
  --duration-min 30 \
  --origin-channel-id <지시가 온 채널 id> --origin-message-id <지시 메시지 id>
```

`--origin-*`는 cha의 지시가 온 채널/메시지를 그대로 넘긴다 — 지시가 승인 채널에서 온
것이면 요청 스레드가 그 메시지에 걸리고, 아니면 결과(완료·취소·만료)가 그 채널의
스레드로 돌아간다. 채널에서 시작된 지시라면 항상 넘겨라. 생략해도 요청은 자기 스레드를
열고 결과는 거기서 종결된다.

`--when`은 calendar 스킬의 KST 날짜 해석으로 결정론적으로 범위로 바뀐다
(오전 09:00–12:00, 오후 12:00–18:00, 저녁 18:00–21:00, 종일/미지정
09:00–18:00). `--range-start`와 `--range-end`는 이미 확정된 ISO 범위가 있을 때만
함께 사용하며, `--when`과 동시에 사용하지 않는다.

- 상대 에이전트 가용시간 질의 → 내 캘린더와 교집합 → 후보 ≤3개 → 상대 승인.
- 성공 시 `PENDING-OWNER draft=<draft-id> …` (exit 7)를 출력하고 cha DM으로
  후보 슬롯을 보낸다. **cha는 그 DM에서 ✅를 눌러 확정하거나 ⛔를 눌러 취소한다.**
  `coordination-confirm-watch` no-agent cron이 1분마다 소유자 반응만 독립 확인한다.
  **이 단계까지 캘린더에 아무것도 쓰지 않는다.**
  다음 줄의 `APPROVAL-THREAD draft=<id> url=<승인 스레드 링크>` 를 **소유자 답장에 그대로 붙인다**
  (2026-09-23 소유자 지시). `url=unavailable search=<id>` 이면 링크를 지어내지 말고 draft id 로
  검색하라고 적는다.
- `DEADLOCK …` (exit 4): 에스컬레이션 DM이 이미 발송됨. cha에게 결과만 전달.
- `REFUSED …` (exit 5): 재협상 1회 후 종료됨. cha에게 결과만 전달.
- `ROUTING-REJECT …` (exit 2): cha가 **정확한 단일 시각**(예: `오전 10시`)을 지정했고
  조율 의사가 없는 요청이다 — 피어 질의 전에 거부된다. 이 요청은 calendar 스킬의
  `draft-create`로 등록한다(피어 이름은 제목 토큰). 조율이 필요하면 `--when`을
  범위(예: `오전`)로 주거나 summary/요청에 `조율` 의사를 담아 다시 실행한다.

### 2) 기본 UX: DM 반응

- ✅ (`\u2705`, `:white_check_mark:`): 확정. watcher가 저장된 draft 해시·DM 메시지와
  cha의 비봇 반응을 다시 바인딩한 뒤 finalize를 실행한다.
- ⛔ (`\u26d4`, `:no_entry:`): 취소. ⛔가 ✅보다 우선하며 `calendar_cli.py discard --draft`
  로 초안을 폐기한다.
- 24시간이 지나면 watcher가 초안을 폐기하고 만료를 알린다.
- ✅/⛔ 처리 결과와 만료 통지는 승인이 게시된 그 요청 스레드(`approval_thread_id`)로
  가고, 그 스레드는 상태 접두어가 붙어 아카이브된다. 스레드가 없는 옛 레코드는 저장된
  origin의 채널 스레드로, 그것도 없으면 소유자 통지로 간다. 확정 요청 메시지 자체
  (승인 표면)는 그대로 둔다.

### 3) 텍스트 fallback: cha가 `실행 <draft-id>` 라고 DM 답장한 뒤에만

에이전트는 이 fallback 을 먼저 제안하거나 요청하지 않는다 — 답장은 승인 카드 링크만 안내하고, 채팅 메시지에 ✅ 를
누르라고 쓰지 않는다. 아래 명령은 소유자가 스스로 `실행 <draft-id>` 를 보낸 뒤에만 쓴다.

```bash
python3 /srv/autophagy-skills/live/coordination/scripts/coordinate_cli.py finalize \
  --draft <draft-id> --slot <승인된 ISO 슬롯> --summary "피어 미팅" \
  --duration-min 30 --correlation <coord-id>
```

finalize는 calendar 스킬 confirm(소유자 DM 재검증, fail-closed)을 거쳐 실행하고,
#team에 간결 확정 통지 1건(조율 봉투는 #autophagy-agents) + cha DM 결과를 보낸다. cha가 `취소 <draft-id>` 라고
하면 `calendar_cli.py discard --draft <draft-id>`로 폐기하고 종료한다.

### 4) watcher 설치

배포 파이프라인은 `skills/coordination/deploy.sh`에서 기존 cron이 없을 때만 다음을
설치한다. 이름은 `coordination-confirm-watch`, 간격은 1분, no-agent/local delivery다.

```bash
hermes cron create "*/1 * * * *" --name coordination-confirm-watch \
  --no-agent --script confirm_reaction_watch.py --deliver local
```

## Sandbox scenario

`scripts/scenario.sh`: 오프라인으로 순수 상태머신 불변식(양측 승인 전 쓰기 명령
0건, deadlock/거절 종결, 재협상 1회 상한, 후보 ≤3)과 토큰 부재 시 fail-closed를
검증하고 `SCENARIO-PASS`를 출력한다. 네트워크 호출·실시크릿 없음.
