---
name: calendar
description: "내 일정을 보거나 새로 잡거나 바꾸거나 지울 때 사용(상대와 시간 조율은 제외). cha 본인 Google 캘린더 관리 스킬 (gws CLI). 조회(list)는 게이트 없이 즉시. 생성/수정/삭제는 변경 요약 초안 → 소유자 전용 승인 스레드의 ✅/⛔ 반응 확인(텍스트 실행/취소는 fallback) → 실행 + approvals.jsonl 기록 게이트를 거친다. 모호한 시간은 되묻는다. 라우팅: 상대 미지정 요청은 calendar 소유; 피어가 명시돼도 '정확한 단일 시각'이면 제목 토큰으로 보고 본인 단독 일정=calendar; 피어+범위+조율 의사면 coordination으로 ROUTING-REJECT(exit 4); 의도 모호(피어명만/시각+조율 충돌)는 ROUTING-CLARIFY(exit 4, fail-closed)로 되묻는다. W3-1."
version: 1.6.1
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Calendar, GWS, Approval-Gate, Autophagy]
prerequisites:
  commands: [python3]
---

# 내 캘린더 관리 (calendar)

cha 본인의 Google 캘린더를 gws CLI(OAuth, W0-6)로 관리한다.

## 절대 규칙 (안전)

1. **확인 전 변경 금지**: 캘린더 생성/수정/삭제는 오직
   `calendar_cli.py confirm`(소유자 확인 검증 내장)으로만 일어난다.
   `gws calendar events insert/update/patch/delete`를 터미널에서 직접 실행하지
   마라 — pre_tool_call 외부효과 게이트가 승인 없는 호출을 차단한다.
2. **모호한 시간은 추측하지 마라**: CLI가 exit 5 + `AMBIGUOUS-TIME 되묻기:`를
   내면 그 질문을 cha에게 그대로 DM으로 물어보고, 답을 받아 다시 시도한다.
3. **캘린더 내용은 소유자 DM과 검증된 소유자 전용 승인 스레드의 카드 안에서만 표시한다**
   (2026-09-20 소유자 승인). 신규 승인 v4에는 제목·시작/종료·변경 내용을 싣는다.
   공유 채널(#team 등)·미상 표면·repo에는 일정 상세를 게시하지 않는다. 부모 채널 안내,
   스레드 이름, 결과 통지는 계속 draft id·동작 종류만 사용한다. 호출자의 비공개 주장만
   믿지 않고 기존 승인 표면/디렉터리의 실제 바인딩을 POST 전에 검증한다.
4. **피어 관련 요청은 결정론적 라우팅 게이트를 거친다**: `draft-create`는 원문/명시
   제목을 `calendar_routing.classify_meeting_request`로 판정한다(등록 `agent_id`
   검사, 본인 `agent-cha` 제외). **정확한 단일 시각**이 지정되면 피어 이름이 있어도
   제목 토큰으로 보고 본인 단독 일정 초안을 만든다(선례 사고 2026-07-20: `오전 10시`
   요청이 07-29 09:00 조율로 표류). **피어+범위(오전 등)+조율 의사**면 초안 없이
   exit 4 `ROUTING-REJECT … coordination 스킬을 사용하세요`를 낸다. **의도 모호**(피어명만
   있고 시각·조율 신호 없음, 또는 정확한 시각과 조율 의사가 충돌)면 exit 4
   `ROUTING-CLARIFY`로 되묻는다(fail-closed, 초안 없음). 분류 레지스트리
   (`~/.hermes/interop/peers.yaml`, 분류가 필요한 설치에만 생성되는 선택 파일)가
   **없으면** stderr `PEER-REGISTRY-ABSENT …` 한 줄만 남기고 피어 분류 없이 본인 단독
   일정으로 진행하지만, 파일이 **있는데** 읽히지 않거나 깨졌으면 기존대로 실패로 막는다.

## 명령 (CLI = `python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py …`)

### 1) 조회 — 게이트 불요, 즉시 실행

변경 명령은 `/srv/autophagy-skills/live/calendar/scripts/` 밖의 사본에서 실행을 거부하며 `STALE-SKILL-COPY-BLOCK`을 출력한다.

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py list [--days 7] [--query 검색어]
```

### 2) 생성 — cha가 DM으로 "내일 오후 3시 실험 미팅 잡아줘" 요청 시

`peer-test와 다음주 오전에 가능한 시간 조율해줘`처럼 피어+범위+조율 의사가 모두 있는 요청에는
이 명령을 쓰지 않는다 — `ROUTING-REJECT`(exit 4)가 나며 `coordination`의
`coordinate_cli.py request --peer peer-test …` 경로로 조율한다. 반면 `peer-test랑 내일
오후 3시 미팅`처럼 정확한 시각이 지정되면 피어 이름은 제목 토큰이므로 이 명령으로 본인
단독 일정을 만든다. 의도가 모호하면 `ROUTING-CLARIFY`로 되물으니 cha에게 그대로 전달한다.

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py draft-create \
  --text "내일 오후 3시 실험 미팅"
```

출력의 `CHANGE-SUMMARY`와 `DRAFT-CREATED id=<draft-id>`를 확인한 뒤, 초안에
묶인 소유자 전용 승인 스레드에 확인 카드를 게시한다.

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py post-confirm --draft <draft-id>
```

`PENDING-OWNER draft=…` 다음 줄의 `APPROVAL-THREAD draft=<id> url=<승인 스레드 링크>` 를 **소유자
답장에 그대로 붙인다**(2026-09-23 소유자 지시 — "승인 스레드에서 ✅" 만 쓰면 소유자가 스레드를
찾아야 한다). `url=unavailable search=<id>` 이면 링크를 지어내지 말고 draft id 로 검색하라고 적는다.

이 명령은 **요청 하나마다 자기 스레드**(`캘린더 · <draft id>`)를 열고 그 안에 변경
요약과 `sha256`을 게시한 뒤 **✅를 먼저, ⛔를 다음에** 미리 단다. 신규 v4 카드는
실제 고정된 변경 명령의 동작·제목·시작·종료를 각각 인용 줄로 보여 주고, 시각은
`YYYY-MM-DD HH:MM (+09:00)`로 읽기 쉽게 표시한다. 종일 일정 날짜는 그대로이며,
수정 항목과 유지 항목을 구별한다. 카드가 너무 길면 안내 메시지·스레드·게시 journal 예약 전에 거부한다. 지시가 승인 채널에서
왔고 `--origin-message-id`를 넘겼다면 그 지시 메시지에 스레드를 앵커한다. 스레드 이름에
들어가는 것은 draft id 뿐이다(절대 규칙 3 — 제목·시각·event id 금지). pending-confirm
JSONL에는 확인 메시지의 channel/message id·초안 SHA-256·렌더 버전·표면 바인딩이,
초안에는 고정된 카드 내용과 `approval_thread_id`(승인 해시 밖)가 저장된다.
v1/v2/v3로 이미 게시된 카드와 해시는 그대로 재생하며 새 문구로 바꿔 승인받지 않는다.
v4의 마지막 줄은 전체 SHA-256을 인라인 코드로 표시한다. 이 단계도 캘린더에는 아무것도 쓰지 않는다.

### 3) 실행 — cha의 ✅ 반응이 기본, 텍스트는 fallback

cha는 게시된 **같은 확인 카드에만 ✅로 확정하거나 ⛔로 취소**한다. `calendar-confirm-watch`
no-agent cron이 매분 반응을 읽는다. 소유자 본인(봇 아님)의 반응만 인정하며,
✅와 ⛔가 함께 있으면 **⛔가 항상 우선**한다. 게시 카드 SHA-256과 현재 초안 SHA-256이
둘 다 일치하지 않으면 fail-closed로 아무것도 실행하지 않는다. 24시간이 지나면 초안을
폐기하고 cha에게 알린다. `post-confirm`이 불리지 않아 카드 없이 남은 초안(고아)은 같은
워처가 **3분 유예 뒤 그 카드를 대신 올린다** — 기존 승인 게이트를 그대로 재사용하므로
새 승인 표면이 생기지 않고, 에이전트가 같은 순간 `post-confirm`을 돌려도 카드는 하나다
(파사드가 승인 키 lease와 posting journal로 멱등하다). 게시가 계속 실패해 24시간을 넘긴
초안만 종전대로 폐기하고 알린다 — 초안 생성과 게시가 별개 단계라 게시가 누락되면 어떤
원장에도 남지 않기 때문이다(2026-07~08 실측 33건 누적, 2026-09-10 재발 3건).
폐기·취소·만료 통지 중 **승인 스레드도 지시 채널도 없는 건**은 `owner_notice` 파사드를
지나 `owner_notice_channel_id`의 통지 채널로 간다(`#notifications`는 예시 이름이며 변경 가능,
통지 채널 미설정 설치는 종전대로 소유자 DM).

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py confirm --draft <draft-id>
```

감시자는 정확한 확인 카드와 draft 해시, ⛔ 우선순위 및 소유자 반응을 한 번 검증한 뒤,
드래프트 ID/해시·DM 채널/메시지·소유자·승인 동작에 묶인 5분짜리 HMAC 서명 일회용
승인 파일을 자식 `confirm`에 전달한다. 자식은 현재 draft/pending과 모든 바인딩을 대조하고
승인 파일을 원자적으로 소비하며, Discord를 다시 조회하지 않는다. 승인 파일 없는 직접
`confirm --draft` 호출은 기존처럼 Discord를 독립 검증하므로 안전하게 호환된다.
cha가 반응을 사용할 수 없으면 `실행 <draft-id>` 또는 `취소 <draft-id>` DM을 fallback으로
보낼 수 있다. 텍스트 `실행`도 Discord REST로 소유자/비봇을 독립 검증하며, pending 반응에
⛔가 있으면 거부된다. 에이전트는 이 텍스트 fallback 을 먼저 제안하거나 요청하지 않는다 — 답장은 승인 카드
링크(`APPROVAL-THREAD … url=`)만 안내하고, 채팅 메시지에 ✅ 를 누르라고 쓰지 않는다.
텍스트 취소는 다음과 같다.

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py discard --draft <draft-id>
```

### 4) 수정/삭제 — 같은 초안 → 확인 → 실행 게이트

```bash
# 수정: 새 일시(--text)나 새 제목(--summary) 중 바꿀 것만
python3 …/calendar_cli.py draft-update --event-id <id> --text "모레 오전 10시"
# 삭제: list로 event id를 찾은 뒤
python3 …/calendar_cli.py draft-delete --event-id <id> --label "실험 미팅"
```

수정·삭제의 새 초안은 기존 읽기 API로 대상 이벤트를 조회하고, 검증된 제목·시작/종료를
기존 해시 바인딩 필드에 고정한다. 조회 실패·대상 불일치·시각 누락이면 저장하지 않는다.
`--label`은 호환 인자일 뿐 승인 제목의 근거가 아니며 실제 조회 제목을 쓴다. 수정은
실제 `events.patch --json`에 있는 항목만 변경으로 표시하고 나머지는 캡처한 값을 유지한다.
이전 값은 별도로 저장하지 않으므로 before→after를 추측하지 않는다. 종일 일정의 날짜형
종료값은 Google Calendar 규약대로 해당 날짜 미포함이다. 게시 때 다시 조회하지 않는다.
기존 해시 알고리즘·저장된 승인·승인 표면 정책 버전은 바꾸지 않는다.

이후 실행 절차는 생성과 동일하다(초안 → `post-confirm` → ✅/⛔ 또는 텍스트 fallback).

### 5) 결과 통지 — 승인이 이뤄진 그 요청 스레드로

승인 요청·리마인더·결과가 **한 스레드에서 완결된다**. 리마인더는 요청 유형·경과시간·
서버 id를 포함한 원문 링크만 담은 최소정보 포인터로, 승인 요청 자신의 스레드에 게시한다.
실행·취소·만료가 확정되면
감시자가 `post-confirm`이 연 그 스레드(`캘린더 · <draft id>`)에 결과를 올리고, 스레드
이름 앞에 상태(`✅ 완료`/`⛔ 취소`/`⌛ 만료`)를 붙여 아카이브한다 — 열려 있는 스레드
목록이 곧 진행 중인 요청 목록이다. 종결 표시가 실패해도 결과 통지는 그대로 남는다
(`THREAD-CLOSE-FAIL` 마커).

cha의 지시가 **채널에서** 왔다면 초안을 만들 때 그 채널/메시지를 함께 넘긴다.
초안 세 명령(`draft-create`/`draft-update`/`draft-delete`) 모두 같은 인자를 받는다.

```bash
python3 …/calendar_cli.py draft-create --text "내일 오후 3시 실험 미팅" \
  --origin-channel-id <채널 id> --origin-message-id <지시 메시지 id>
```

이 인자는 **스레드가 놓이는 자리**만 바꾼다: 지시가 승인 채널에서 온 것이면 요청
스레드가 그 지시 메시지에 걸리고, 아니면 결과 통지가 그 채널의 스레드로 간다. 확인
(✅/⛔)은 종전대로 요청 스레드의 확인 메시지에서만 이뤄지고, 초안 해시에는 들어가지
않는다(같은 변경이면 해시도 같다).

**결과 통지와 스레드 이름은 마스킹된다**: 절대 규칙 3의 예외는 소유자 전용 승인
카드 본문뿐이다. 제목·일시·이벤트 id·캘린더 id를 결과 통지·부모 채널 안내·스레드
이름에 넣지 않는다. 공유 채널에 나가는 것은 동작 종류(등록/수정/삭제),
draft id, 결과, 사유뿐이다. 결과 통지는 `OwnerMessage` 봉투로 보내되 `subject`에도
일정 제목·참석자·시각을 넣지 않는다. 사실은 기존 마스킹 문구를 그대로 쓰고 실행이
성공한 뒤에만 실행 완료 상태를 붙인다. 위치는 저장된 길드·요청 스레드와 pending의
`dm_message_id`로 만든다 — 실행/폐기 전에 카드 id를 보존한다. 같은 스레드에서는
자기 링크를 생략하고, 다른 표면으로 폴백하면 승인 카드 링크를 붙인다. 길드를 모르는
옛 레코드는 링크를 추측하지 않고 `Discord 검색 / <draft id>`를 안내한다.
봉투 모듈이나 파사드의 봉투 지원이 없는 옛 런타임에서는 기존 문자열을 바이트 그대로
보낸다. 폴백 전송 함수는 목적지별로 이미 렌더된 문자열을 그대로 전달한다.
승인 스레드도 origin도 없는 초안은 승인 실행·취소·만료 결과를 소유자 DM으로 알린다.
이 DM도 일정 내용 없이 동작 종류와 draft id만 담고 `Discord 검색 / <draft id>`를 안내한다.
스레드 게시가 실패하면 결과는 cha에게 폴백되고 `NOTIFY-THREAD-FAIL` 마커가 남는다.

## 다이제스트 일정 승인

메일 다이제스트가 일정 메일을 감지하면 `draft-create --digest-day <YYYY-MM-DD>` 로
위임한다. 이 플래그가 있으면 초안 저장에서 끝나지 않고 **그 자리에서 승인 카드까지
게시**한다 — 카드가 붙지 않은 초안은 24시간 뒤 고아로 폐기되던 것(2026-09 실측)이 이
경로에서는 생기지 않는다. 종료코드 0 은 "카드가 실제로 게시됐다"는 뜻이고, 게시가 끝나지
않으면 소유자에게 즉시 `캘린더 승인 카드를 게시하지 못했습니다` 통지가 가고 exit 1 로
끝난다. 저장된 초안은 감시자(`confirm_reaction_watch`)가 **다음 틱에 3분 유예 없이**
다시 게시한다. 이미 올라간 카드가 있으면 스레드 이력에서 찾아 그것을 살리므로(반응
재무장·pending 커밋) 같은 카드가 둘이 되지 않는다. 다이제스트가 아닌 초안은 종전대로
`post-confirm`/3분 백스톱 경로다.

**묶음 규칙(소유자 결정, t_bacebc3a)**: 일정마다 스레드를 열지 않는다.

- 게시일(KST)마다 승인 스레드 **하나**(`캘린더 · 다이제스트 <날짜>`), 그 안에 일정마다
  ✅/⛔ 카드 **하나**. 각 카드는 독립적으로 결정한다. 바인딩은 `CALENDAR_GATE_DIR/
  daily-threads/<날짜>.json` 에 저장돼 같은 날의 다음 위임은 그 스레드를 재사용한다.
- **같은 일정** = 캘린더 + KST 시작 시각(분) + 제목 토큰 집합(NFKC·대소문자 무시·정렬·
  중복 제거). 기간·문장부호는 동일성이 아니다. 이 키가 초안 id 이자 승인 키
  (`calendar:digest:<키>`)라서 후속·정정 메일은 **아직 결정되지 않은 카드만** 최신
  내용으로 교체한다(공유 라이프사이클의 supersede: 옛 카드 삭제 → 새 카드, 같은 스레드).
- 이미 ✅/⛔ 로 결정된 일정(`executed`/`cancelled` 레코드)은 같은 메일이 다시 와도 **다시
  묻지 않는다**(`DIGEST-DECIDED`). 다이제스트 초안의 ⛔ 는 파일을 지우지 않고
  `cancelled` 로 남겨 그 사실을 기억한다.
- 카드를 게시(또는 이미 결정된 일정을 확인)한 뒤 `DIGEST-CARD draft=<키> url=<카드 링크> start=<ISO> end=<ISO>
  summary=<제목>` 한 줄을 낸다. 메일 다이제스트가 이 줄로 메일 카드의 `🗓️ 일정 초안` 을 승인 카드
  링크와 일정 한 줄로 바꾼다. 링크는 `owner_message.discord_link` 로만 만들고, 카드 메시지 id 를
  모르면(이미 결정된 일정) 스레드 링크, 좌표가 없으면 `url=unavailable` 이다 — 추측하지 않는다.
- 결과(실행 완료/취소/실행 실패)는 그 카드 **아래 답글**로 올라가고, 일별 스레드는 다른
  카드가 남아 있으므로 개별 결과로 닫지 않는다. 실행 실패는 `⚠️ … 실행 실패` 로 카드
  아래에 알린 뒤 항목을 남겨 다음 틱에 재시도한다.

- **메일 다이제스트 스레드와 하나로 (2026-10-16, 소유자 결정 Q187)**: 그날 메일 다이제스트가 표준 승인 스레드를 열었으면(`~/.hermes/digest-threads/<날짜>.json`, `automation/interop/digest_thread.py`) 일정 카드는 `캘린더 · 다이제스트` 스레드를 따로 열지 않고 그 스레드에, 환경변수 `CALENDAR_DIGEST_REPLY_TO`·`CALENDAR_DIGEST_REPLY_CHANNEL` 이 가리키는 **메일 카드 아래 답글**로 선다(해시 밖 필드 `digest_reply_to`·`digest_reply_channel`, 채널이 다르면 답글 표시 없이 게시). 그 파일이 없으면(메일이 평면 배치로 갔거나 옛 메일 스킬) 예전처럼 이 스킬의 일별 스레드를 쓰고, 파일이 있는데 검증되지 않으면 게시하지 않는다. `digest-pending --thread <id>…` 는 스레드별 미결 일정 카드 수(`DIGEST-PENDING thread=<id> count=<n>`)를 읽기 전용으로 내며 메일 다이제스트가 스레드 표시(🔔 / ✅ 완료)를 정할 때 쓴다.

## 반응 감시 cron

`deploy.sh`는 배포 시에만 agent의 Hermes에 아래 no-agent cron을 idempotent하게 등록한다.
이 저장소에서는 실행하지 않는다.

```text
calendar-confirm-watch  */1 * * * *  --script confirm_reaction_watch.py --deliver local
```

감시자는 mounted calendar skill의 `calendar_cli.py confirm/discard`만 호출하며, gws를
직접 호출하지 않는다.

Discord 가 카드·반응 읽기에 429 를 주면 같은 요청을 `Retry-After`(10초 이하)만큼 기다렸다
최대 3번까지 다시 묻는다. 그래도 막히거나 더 오래 기다리라고 하면 그 틱은 예전처럼 실패로
끝난다(2026-10-17: 한 번의 `retry_after=5` 가 틱을 실패로 만들어 doctor 가 「최근 실패·중지」를
알렸다 — [소개](../../docs/기능소개/캘린더-확정-워처-429-재시도.md)).

## 시간 파싱 규칙

지원: 오늘/내일/모레/글피, N일 뒤, 이번주/다음주+요일, M월 D일, YYYY-MM-DD,
오전/오후 H시[M분|반], HH:MM, 정오/자정, "H시부터 H시까지", "N시간". 기본
길이 1시간. "다음주쯤"·시각 없음·오전/오후 없는 1~12시는 되묻기(exit 5).

## Sandbox scenario

배포 파이프라인용 `scripts/scenario.sh`: 스텁 gws로 초안 무변경, fail-closed
confirm, 모호시간 되묻기, (어댑터 가용 시) 서명 주입 확인 실행 + 승인 기록,
삭제 왕복, 거부 무변경을 검증하고 `SCENARIO-PASS`를 출력한다.
네트워크 호출·실시크릿 없음.
