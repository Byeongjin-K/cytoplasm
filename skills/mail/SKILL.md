---
name: mail
description: "기관메일(mailon.kr) 스킬. 메일 작성·발송 지시에서 수신자가 이메일 주소가 아니라 사람 이름이면(예: '홍길동 박사님께 메일') 반드시 먼저 resolve로 이름→이메일 해석(기관 웹메일 자동완성 기반, READ-ONLY) — 메일함 검색만으로 포기하거나 주소를 추측·유추하는 것은 금지. 읽기: list/get/classify/status/resolve 래퍼(W4-1, READ-ONLY). 파이프라인(W4-2): 수신메일 중요도·업무 분류(계정 모델 체인)→다이제스트(08:00 KST)→소유자 지시 기반 회신 초안(Codex OAuth)→현재 승인 표면의 소유자 게이트→mailon 발송→approvals.jsonl. 새 메일 작성(compose)도 동일 watch·해시 바인딩 게이트를 경유한다. 발송은 반드시 승인 게이트 경유 — 직접 send 금지. 회신은 승인 메시지 한 건에 전문과 sha256을 함께 표시한다. 승인 표면은 `approval_surface.py` 정책과 draft의 저장된 바인딩으로 결정되며, 폐지 표면의 미결정 초안은 승인 없이 만료된다. 회신·후속메일은 메일 클라이언트의 회신처럼 원문(헤더+본문)을 발송 본문 하단에 인용한다(draft 기본, --reply-all 로 전체회신, compose --in-reply-to <uid> 로 후속메일). 전달(FW·포워드·'Gmail로 보내줘')은 compose --forward <uid>(기관메일) / --forward-gmail <message-id>(Gmail) — 원문 인용 + 원 첨부 바이트 그대로, 같은 승인 카드."
version: 1.10.3
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Mail, Institutional, Triage, Approval-Gated, Autophagy]
prerequisites:
  commands: [python3]
---

# 기관메일 스킬 (mail) — W4-1 읽기 래퍼 + W4-2 triage 파이프라인

`~agent/emailAutomation`의 mailon CLI를 감싸는 스킬. mailon의 명령 형태·stdout
계약·종료코드는 전부 `scripts/mail_wrapper.py` 한 파일에 상수로 캐싱되어 있다
(단일 인터페이스 파일). W4-2 파이프라인은 그 위에 승인 게이트 발송을 얹는다.

## 실행 경로 규칙 — 관리자 배포본에서만 (2026-09-01, 소유자 지시)

이 스킬의 스크립트는 **`/srv/autophagy-skills/live/mail/scripts/` 에서만** 실행한다. 그 심링크가 소유자 ✅ 로 마운트된 유일한 배포본이고, 판정은 `readlink /srv/autophagy-skills/live/mail` 이다.

- **`/srv/autophagy-agents/skills/mail/...` 을 실행하지 않는다.** 그 체크아웃은 관측용 미러이지 런타임이 아니다 — 2026-09-01 실측: 원문 인용 릴리스가 04:22Z 에 마운트됐는데 87분 뒤의 회신 초안이 미러(121커밋 뒤처짐, 08-29부터 dirty 로 동결)에서 만들어져 인용 없이 나갔다. 커밋됨 ≠ 배포됨 ≠ **실행됨**.
- **`~/.hermes/skills/mail/...` 은 존재하지 않는다**(SS-1 반전 이후 그 루트는 자가 스킬 전용). 없는 경로를 찾아 다른 사본을 뒤지지 말고 위 live 경로를 쓴다.
- 코드가 강제한다: `mail_runtime.governed_copy_refusal` — 이 호스트에 배포본 마운트가 있으면 다른 사본의 mutating 서브커맨드(`draft`·`reply-request`·`compose`·`process`·`digest`·`watch`·`confirm`·`discard`·`sign`)는 `GATE-REFUSED STALE-SKILL-COPY-BLOCK …`(exit 3)으로 멈추고 올바른 경로를 알려 준다. 읽기 전용(`mode`·`list-drafts`·`digest-items`·`evidence`)은 막지 않는다. 이 메시지를 보면 경로를 고쳐 다시 실행하면 된다 — 우회 옵션은 없다.

## 장문 승인 카드

새 승인 카드가 Discord 2,000자 한도를 넘으면 제목·수신자·draft id·본문 SHA256·승인 해시와 ✅/⛔ 안내를 담은 요약 카드로 게시합니다. `본문: 첨부 파일 참조` 아래의 같은 메시지에 본문과 원문 인용 전체를 UTF-8 텍스트 파일 하나로 첨부하므로, 긴 메일도 내용을 줄이지 않고 검토할 수 있습니다.

승인 재사용 검사와 리액션 처리 모두 저장된 첨부 이름·바이트 수·SHA256, 메시지의 첨부 선언, 내려받은 실제 바이트를 대조합니다. 첨부 누락·변조·다운로드 실패 시 ✅를 실행하지 않습니다. 첨부 검증 정보는 기존 승인 해시 밖에 보관하며, 짧은 카드와 이미 게시된 카드의 렌더링은 바꾸지 않습니다. 제목·수신자 등 때문에 요약 카드마저 한도를 넘으면 승인 스레드 생성과 posting journal 예약 전에 거부합니다.

## 읽기 명령 (W4-1, READ-ONLY)

```bash
# 최근 5건 (라이브 수집 후 state.db 읽기; 필드를 명시적으로 가릴 때는 --masked)
python3 /srv/autophagy-skills/live/mail/scripts/mail_wrapper.py list --limit 5 --sync --masked

# 로컬 읽기만 (브라우저/네트워크 0)
python3 /srv/autophagy-skills/live/mail/scripts/mail_wrapper.py list --limit 5

# 받은편지함 외 사용자 폴더까지 명시적으로 수집·조회
python3 /srv/autophagy-skills/live/mail/scripts/mail_wrapper.py list --limit 20 --sync --all-folders

# 단건 조회 (--body는 agent 홈 안에서만; 마스킹 시 본문 대신 sha256+bytes)
python3 /srv/autophagy-skills/live/mail/scripts/mail_wrapper.py get <uid> --body

# 메타데이터 분류 (제목/발신자 문자열만 사용, LLM 무경유)
python3 /srv/autophagy-skills/live/mail/scripts/mail_wrapper.py classify --uid <uid>

# 수신자 이름→이메일 해석 (기관 웹메일 자동완성 기반, READ-ONLY; 필드를 명시적으로 가릴 때는 --masked)
python3 /srv/autophagy-skills/live/mail/scripts/mail_wrapper.py resolve --name "<이름>"

# mailon 상태를 JSON으로
python3 /srv/autophagy-skills/live/mail/scripts/mail_wrapper.py status
```

stdout은 항상 **정확히 하나의 JSON 객체**다 (`"wrapper": "mail-wrapper-v1"`).
기본 `list`는 받은편지함만 조회한다. `--all-folders`를 명시한 경우에만 vendor
sync에 `--folders all`을 전달하고 DB의 모든 폴더를 조회하며, JSON에
`"scope":"all-folders"`가 추가된다.

### 수신자 이름→이메일 해석 (resolve)

- compose/발송 지시에서 수신자가 이메일 주소가 아니라 **사람 이름**이면, 주소를 추측하지 말고 반드시 `resolve --name`으로 먼저 해석한다.
- 출력은 wrapper JSON 1객체: `candidates[]` 항목 = `{group, name, email, org}`, group은 `organization`(조직도)/`contacts`(개인 주소록)/`history`(수발신 이력)/`unknown`. 래퍼가 **`organization` > `contacts` > `history` > 목록에 없는 group** 순으로 정렬해 돌려주며, 서로 다른 주소의 개수를 `distinct_address_count` 와 `ambiguous` 로 함께 알려준다.
- 후보 판정 규칙 (fail-closed): **0건 → 주소를 지어내지 않고 cha에게 해석 실패를 보고**한다. **`ambiguous=false`(주소 1종) → 그 주소를 사용**한다 — 후보가 여럿이어도 주소가 하나면 선택할 것이 없다. **`ambiguous=true` → 정렬된 순서 그대로 후보를 cha에게 보여주고 선택을 기다린다** (자동 선택 금지).
- 최종 안전장치는 기존 compose owner-✅ 게이트다 — 현재 승인 스레드에 실제 주소가 그대로 표시되므로 소유자가 최종 확인한다.
- resolve는 읽기 전용이다: 브라우저 자동완성 조회만 수행하며 발송 트리거를 절대 호출하지 않는다.

### 발송 모드 상태 보고 규칙 (추측 금지)

cha에게 메일 상태를 보고할 때, **발송 모드(full-go / read-go / no-go)는 절대 추측하지 말고 반드시 아래 권위 명령의 `effective=` 값으로만 보고**한다:

```bash
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py mode
```

출력 예: `MODE effective=full-go runtime=… repo=… consecutive_send_failures=0`.
- `effective=full-go` → 발송 가능.
- `effective=no-go` → 차단(승인 실발송 연속 2회 실패 또는 수동 강등; `mode-switch.jsonl`에 사유). 복구는 사람/오케스트레이터가 런타임 파일 삭제·재작성.
- `effective=read-go` → 읽기만.

mode 머신러리 설명("2회 실패→no-go", "둘 다 없으면 no-go 폴백")만 보고 상태를 단정하지 말 것 — 반드시 위 명령을 실행해 실제 `effective=`를 확인하고 그 값을 그대로 보고한다.


### 종료코드 (mailon → 래퍼 매핑)

| mailon | 의미 | 래퍼 동작 | 래퍼 exit |
|---:|---|---|---:|
| 0 | 성공 | 정상 payload | 0 |
| 1 | 설정 오류(env 누락) | `config_error` + 안내 | 1 |
| 2 | 로그인 오류(stderr 시그니처 `login_error`/`login_dom_ipt_id`) | `auth_error` + **재인증 안내** | 2 |
| 2 | 브라우저·페이지 오류(`browser_error`/`timeout`/`inbox_folder_uid_selector`) | `browser_error` + 브라우저 점검 안내(재인증·비밀번호 경로 금지) | 2 |
| 2 | 가릴 시그니처 없음 | `auth_or_browser_error` + **둘 중 하나로 단정하지 않는** 안내 | 2 |
| 3 | sync 구조 실패 | state.db 로컬 폴백(가능 시 exit 0 + 폴백 표기, 불가 시 3) | 0/3 |
| 10/11 | Windows 런처 사전점검(리눅스 미발생) | `environment_error` | 6 |
| — | uid 없음/DB 비어있음 | `not_found` | 5 |
| — | 서브프로세스 타임아웃 | `timeout` + 잔여 chrome 정리 안내 | 7 |

주의: **20은 종료코드가 아니라 수집 계약의 페이지 크기**(20건/page, 최대
500 page — mailon/scraper.py PAGE_SIZE)다.

## 본문 보여주기

소유자가 "본문 보여줘"라고 하면 요약 대신 다음 읽기 전용 명령을 사용한다.

```bash
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py get <uid> --body
```

- 출력된 렌더 청크를 **순서대로 그대로 붙여 넣는다. 다시 요약하거나 문장을 고치지 않는다.** 여러 청크이면 각 `(i/n)` 접미까지 한 Discord 메시지로 보낸다(접미 포함 최대 1,900자). 청크 사이의 빈 줄은 구분용이며 재결합해 한 메시지로 보내지 않는다.
- 제목·보낸 사람·받는 사람/참조·날짜·첨부 건수는 불릿 헤더, 본문은 빈 줄 한 줄로 정돈된다. 목록·표·링크 Markdown은 유지하고, 한 메시지보다 긴 문단만 줄/낱말/글자 순으로 나눈다.
- 인용 원문은 기본적으로 마지막 한 줄의 생략 줄 수로 접는다. 소유자가 인용까지 요청하면 `--full`을 붙인다. 서명 구분자(`--`)와 영문·국문 면책문은 한 줄로 줄인다. 서명까지 가공 없는 원문이 필요하면 `--raw`로 wrapper가 공개한 본문 문자열을 그대로 받는다(청크 분할 없음).
- **노출 범위는 기존 wrapper와 같다.** 공개·QA 표면에서는 기존처럼 `--masked`를 사용한다. 이 경우 `--raw`·`--full`도 마스킹을 풀지 않으며 기존 해시·바이트 수 JSON이 그대로 나온다. 본문 파일이 없으면 렌더에는 그 안내가 표시되고, 조회 실패는 기존 오류·종료코드를 유지한다.
- `mail_wrapper.py get`의 JSON 계약은 그대로다. 사람이 읽을 본문에는 위 triage 명령을 쓰고, 기계가 소비하는 조회에는 기존 wrapper 명령을 유지한다. `get`은 읽기 전용으로 배포 사본의 변경 명령 가드 대상이 아니다.

## W4-2 triage 파이프라인 (`scripts/triage_cli.py`)

두 개의 루프와 현재 정책의 승인 스레드에서 확정하는 소유자 지시 기반 회신·compose 초안 플로우로 운영된다.

### 1. 승인 및 발송 루프 (watch cron 주기)
Hermes cron(`mail-triage-watch`, no_agent)이 `watch`를 돌린다. 이 루프는 **자동으로 초안을 생성하지 않으며**, 오직 다음 작업만 수행한다:
- 아직 게시되지 않은 대기 중인 초안을 draft 레코드에 바인딩된 채널에 게시한다. 새 회신·compose 초안은 `approval_surface.py`가 정한 표면에 **요청 하나당 스레드 하나**를 열어 게시한다 — 스레드 이름은 `메일 회신 · <제목>` / `메일 발신 · <제목>`이고, 지시가 승인 채널에서 왔고 `--origin-message-id`가 전달됐으면 그 지시 메시지에 스레드를 단다. 그 스레드 id는 draft 레코드의 `approval_thread_id`(승인 해시 밖)로 남아 결과 통지가 같은 자리로 돌아온다.
- 게시된 승인 메시지의 ✅(확정) / ⛔(취소) 리액션을 확인하고 처리한다. 현재 정책에서 폐지된 표면에 남은 미결정 초안은 옛 메시지를 삭제한 뒤 `expired`로 종결하며, 승인·발송으로 간주하지 않는다. 이미 누른 소유자 결정은 항상 먼저 처리한다.
- 승인된 초안의 실제 메일 발송.

### 2. 다이제스트 루프 (매일 08:00 KST)
신규 cron `mail-daily-digest`(`0 8 * * *`, no_agent)가 `digest`를 실행한다.
- 마지막 다이제스트 이후 수신된 메일을 채널에는 머리글 메시지(건수·회신 방법) **하나만** 올리고, 메일별 카드는 그 머리글에 단 스레드 안에 **메일 1건 = Discord 메시지 1건**으로 보낸다(2026-10-08 소유자 지시 — 글자 수 청크가 카드를 중간에서 자르던 문제). 스레드는 공용 `origin_notice.resolve_thread_id` 로 열며, 열 수 없으면(DM 폴백 등) `DIGEST-THREAD-FAIL` 마커 후 같은 채널에 이어 올린다. 머리글은 ✅ 를 요청하지 않는다 — 초안의 승인은 그 초안의 정식 승인 카드로만 받는다(「승인 경로 완결 규칙」). 카드는 `### N. 제목` → `-# ↩️ 이 메시지에 답장하면 회신 초안 · 회신 키 \`MMDD-HHMM-N\`` → 배지 → 인용문 요약 → `수신 시각 · 발신 <이름>` → `참조 A, B, C 외 N명` 순서다. 이름은 헤더의 따옴표·역슬래시 이스케이프와 RFC 2047 인코딩을 한 번만 풀어 보여 주고(`scripts/mail_contacts.py`), 이름이 없을 때만 주소를 쓴다. 참조에서 소유자 자신은 빠진다. UID·발신 해시는 화면에 없고 `digest_items`(회신 키·Discord 메시지 id 포함)에만 있다. 실제 발신자·Cc는 소유자 DM projection에만 포함되고 저장 row에는 남지 않는다. 배지는 중요도와 속성에 따라 한글·이모지(🔴 중요/🔵 일반/🗑️ 스팸, ↩️ 회신 필요, 📅 일정, 💳 예산, 👀 참조(CC))로 표시된다.
- **DM 전송**: 메시지마다 `dm_owner` 를 한 번씩 부른다(제목 200자·요약 1,200자에서 잘라 메시지가 2,000자를 넘지 않는다). 공유 interop 전송기(DiscordTransport)가 순서를 보장하고 429 Retry-After 에 대응한다. 다이제스트 등 `dm_owner`를 쓰는 모든 표면에 동일 적용. 발송/취소 **결과 통지**는 그 요청의 승인 스레드(`approval_thread_id`)로 게시되며, 없으면 origin 바인딩의 원 채널 스레드로 간다(§4).
- **동기화 폴백**: mailon 동기화(`list --sync`) 실패 시 로컬 `state.db` 기준으로 폴백 발송하며, DM 최상단에 "⚠️ mailon 동기화 실패 — 로컬 DB 기준 (재인증 필요할 수 있음)" 경고를 부착한다. `--no-sync` 명시 실행의 실패는 폴백 없이 fail-closed.
- **실패 처리**: 모든 digest 실패(빌드 단계 LLM 실패, DM 전송 실패)는 기록 없이 단일 행·레닥션된 구조화 마커 `DIGEST-FAIL stage=<build|deliver|runner> retry_safe=<true|false> code=<...>`로 종료하며(주소·본문·토큰 미포함), 다음 tick에 재시도한다(DM-first/record-after 불변식 유지 — cursor는 전달 성공 후에만 기록). 워처는 `retry_safe=true` 마커만 인틱 재시도한다(전달 실패는 일부 청크가 이미 나갔을 수 있어 `retry_safe=false`). cron은 `--deliver discord`로 등록되어, no-agent 스크립트의 **stdout**(성공=빈 stdout=무음, 실패=마커 1행+exit 1)이 소유자 DM으로 전달된다 — `--deliver local`(전달 대상 0개)이었던 2026-07-31에는 실패가 소유자에게 도달하지 못했다.
- **항목 단위 fail-open (분류)**: 한 메일의 분류 LLM 호출이 실패(모델 timeout 또는 파싱 불가한 비-JSON 응답)해도 다이제스트 전체가 중단되지 않는다. 해당 항목은 보수적으로 `🔴 중요` + `⚠️ 분류 실패` 배지로 표면화되고(플래그는 모두 미부여 — 조작된 판정으로 캘린더 초안을 위임하지 않음), 나머지 메일은 정상 전달·기록된다. 요약 실패가 `(요약 실패)` fallback으로 항목을 유지하는 것과 동일한 취지이며, 요약도 분류와 같이 **1회 재시도**한 뒤에야 fallback으로 내려간다 — 재시도까지 실패한 건은 `llm-calls.jsonl`에 `purpose=digest_summary_failed` 한 줄로 예외 클래스와 레닥션·클립된 메시지, 불투명 uid만 남겨(제목·발신자·본문·주소 등 메일 내용은 절대 기록하지 않음) no-agent cron이 stderr를 버려도 원인을 추적할 수 있게 한다. 분류도 재시도 소진 시 `purpose=classify_failed`로 같은 방식으로 기록한다. (분류가 파싱은 되나 bool을 문자열로 준 경우 `"false"`가 참으로 새는 버그도 함께 차단 — `_json_bool` 엄격 파싱.)
- **티어 불가 = fail closed (2026-09-04 공급자 이관)**: 모델 경로는 공유 Codex OAuth 클라이언트(`automation/codex_llm.py`, provider `openai-codex`) 하나뿐이다. 자격 증명 없음·쿼터·전송 실패는 `LlmUnavailableError`로 구분되며, **강등할 티어가 없으므로 강등하지 않는다** — 그 단계는 재시도하지 않고(사고 당시 재시도 폭주 방지) 틱 전체가 `DIGEST-FAIL stage=build retry_safe=false code=codex_unavailable` 마커 한 줄로 종료한다. DM도 저장도 하지 않으므로 메일은 다음 틱에 그대로 남고, 원인은 `llm-calls.jsonl`의 `purpose=classify_failed`(마스킹) 한 줄로 남는다. 개별 요청 실패(1회성 rc≠0·파싱 불가)는 기존 항목 단위 fail-open 그대로다. 2026-09-03 은퇴한 2차 티어로의 강등 폴백(`fallback_from` 표식과 `⚠️ 티어 사용 불가` 알림 줄)은 그 티어와 함께 제거되었다.
- 일정 예약이 필요한 메일은 이 단계에서 캘린더 스킬에 `--digest-day`를 전달해 초안 생성과 승인 카드 게시까지 위임된다. **위임 실패는 원인별로 구분해 보고한다** — 카드의 `🗓️ 일정 초안` 노트가 `calendar-unavailable`(배포본 없음) · `calendar-refused`(rc=1 게이트 거부) · `calendar-unparsed`(rc=2 일정 문장 해석 불가) · `calendar-misconfigured`(rc=3 설정·피어 레지스트리 등) · `calendar-routing`(rc=4 단독 일정이 아님 — coordination/되묻기) · `calendar-ambiguous`(rc=5 되묻기) · `calendar-exec-failed`(rc=6) · `calendar-timeout` · `calendar-spawn-failed` · 계약 밖 종료코드는 `calendar-failed-rc<N>` 중 하나가 된다. 자식 CLI의 stderr는 카드가 아니라 운영 로그 한 줄(`CAL-FAIL uid=<마스킹> rc=… cause=… stderr=…`, 개행 제거·200자 클립)로만 나가고, 캘린더 CLI가 느리거나(timeout) 실행되지 않아도 그 항목만 잃고 다이제스트는 계속된다(항목 단위 fail-open).
- 다이제스트 게시일(KST)마다 승인 스레드 하나에 일정별 카드를 묶는다. 미결 중복·정정은 최신 카드로 교체하고 이미 결정한 일정은 다시 묻지 않는다. 게시 실패는 소유자 통지를 시도하고 다음 감시 틱에 재시도하며, 실행·취소 결과는 원래 카드 아래 답글로 남긴다.
- **참조(CC) 수신 메일**: cha가 To가 아닌 Cc로만 수신한 메일은 회신 대상이 아니다
  (frontmatter `to:`/`cc:`를 `MAILON_ID`와 대조 — `scripts/triage_recipient.py`).
  파이프라인은 `reply_needed`를 자동 억제하고 flags에 `cc`를 표기하며
  자동 회신 초안을 만들지 않는다(`cc-no-reply`). 판별 불가 시에는 기존 동작 유지(fail-open).
  **대화형 요약/보고에서도** `cc` 플래그 메일은 '회신 필요'가 아닌 '참조(CC)' 항목으로 묶어 제시한다.
- **대량 공지**: To 수신 메일이라도 no-reply 계열 발신자와 뉴스레터/대량배포/수신거부 신호가 함께 있으면 `reply_needed`를 억제한다. 이 판정은 Cc-only 판정과 독립적이며 Cc 배지를 덮어쓰지 않는다.
- `scripts/mail_search.py`는 제목·발신자·본문·스레드·첨부파일의 순수 읽기 점수와 To/Cc 역할, `mass_notice`/`direct_inquiry` 접촉 유형을 계산하며 외부효과나 저장을 수행하지 않는다.

### 3. 소유자 지시 기반 초안 생성
**메일별 답장 (2026-10-08, 소유자 지시)**: cha 가 다이제스트 스레드의 메일 메시지에 Discord **답장(Reply)** 을 달면 메시지 앞에 `[Replying to: "### N. … 회신 키 MMDD-HHMM-N …"]` 가 붙어 온다. 그러면 번호 변환 없이 그 회신 키로 바로 실행한다:

```bash
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py reply-request --key <회신 키> --author-id <답장한 사용자 id> --instruction "<답장 본문>" [--reply-all]
```

- 답장 본문이 곳 회신 요지다. 내부에서 아래 `draft` 와 **같은 초안·승인 카드 경로**를 타므로 발송은 여전히 승인 스레드 카드의 ✅ 뒤 `watch` 가 한다. 답장에는 출력의 `APPROVAL-THREAD … url=` 링크를 싣는다.
- 소유자가 아닌 사람의 답장은 `REPLY-REQUEST-IGNORED not-owner`(exit 2)로 무시된다. 모르는 키는 `REPLY-REQUEST-UNKNOWN`(exit 3) — 키를 지어내지 말고 아래 번호 경로로 간다.
- 같은 메일에 초안이 이미 있으면 `초안이 이미 있음`(exit 2)으로 막히고, 두 답장이 동시에 와도 잠금 아래에서 하나만 초안이 된다. 다시 쓰는 절차는 `draft` 의 중복 거부와 같다(이전 초안 폐기 후 재요청).

번호로 지시하면(예: "3번 메일, 참석 가능하다고 회신해줘"), 에이전트는 다음 절차를 따른다:
1. `digest-items` 명령으로 번호(N)를 실제 `uid`로 변환한다.
2. `draft --uid <uid> --instruction "<지시문>" [--reply-all]` 명령을 실행하여 초안을 생성하고 현재 mail-reply 승인 스레드에 게시한다. 답장에는 출력의 `APPROVAL-THREAD … url=` 링크를 그대로 싣는다(§4-2).
3. 초안은 `prompts/reply-draft-v2.md`를 사용하며, 항상 승인된 Codex OAuth 티어에서 생성된다.
4. **원문 인용 (2026-09-01, 소유자 지시)**: 발송 본문은 메일 클라이언트의 회신처럼 `<회신문>` + 빈 줄 + `-----원본 메시지-----` 헤더(보낸 사람·보낸 날짜·받는 사람·참조·제목) + 원문 본문이다(`scripts/mail_quote.py`, 원문은 20,000자에서 절단 마커). vendor mailon 에는 답장 명령이 없어 새 메일 compose 로 나가므로, 인용이 없으면 상대는 무엇에 대한 답인지 알 수 없다. draft 레코드의 `body` 는 소유자가 검토하는 회신문만이고 인용은 `quote` 필드·동결 argv 에 들어가 승인 해시에 함께 바인딩된다. 승인 메시지는 인용을 덤프하지 않고 `- 원문 인용: 포함` 한 줄로만 알린다(Discord 2,000자 한도). 엔티티 프리플라이트는 회신문만 정규화해 되돌려주므로 인용은 발송 직전 실행 경계(`scripts/mail_preflight.py`)에서 회신문 아래에 다시 붙인다 — 2026-09-03 전달 메일이 원문 없이 회신문만 발송된 사고가 그 이유다.
5. **전체회신**: cha 가 "전체회신"·"모두에게"·"참조 그대로" 라고 하면 `--reply-all` 을 붙인다 — 원문 frontmatter 의 To∪Cc 에서 소유자(`MAILON_ID`)와 발신자를 뺀 주소가 Cc 로 들어가 argv·해시에 바인딩되고 승인 메시지에 `- Cc:` 로 표시된다. 지시가 없으면 기본은 발신자 단독 회신이다. 원문에 다른 수신자가 없으면 `--reply-all` 도 단독 회신과 같다.

### 4. 새 메일 작성 (compose — 요청별 승인 스레드 확정)
cha가 새 메일 발송을 지시하면, 이중 승인 없이 그 요청 전용 스레드 `메일 발신 · <제목>`에서 한 번만 확정한다:
1. `compose --to <주소> [--cc <주소> ...] --subject "<제목>" --body "<본문>" [--attachment <경로> ...] [--in-reply-to <uid>] [--origin-channel-id <채널ID>] [--origin-message-id <메시지ID>]` — 초안 생성 + 현재 승인 스레드 게시(✅·⛔ 미리 부착). `--cc`는 여러 번 지정할 수 있고 To와 함께 승인 해시에 바인딩된다. 첨부는 최대 10개·개별/전체 25 MiB이며 반복 옵션 순서대로 파일명·크기·MIME·내용 해시가 승인에 바인딩된다. 승인 후 수신자·본문·첨부가 바뀌거나 일부 업로드만 성공하면 최종 발송을 거부한다.
   - **후속 메일은 원문을 인용한다 (2026-09-01, 소유자 지시)**: 이전 메일(받은 메일이든 내가 보낸 메일이든 — `list --all-folders` 로 보낸메일함 uid 도 찾을 수 있다)에 이어지는 안내·확정·변경 메일은 **반드시 `--in-reply-to <uid>`** 를 붙여 그 메일의 원문(헤더+본문)을 발송 본문 하단에 인용하고, 제목은 원문 제목에 `Re:` 를 붙인다. 회신 대상 uid 를 모르면 `--in-reply-to` 를 지어내지 말고 먼저 `list`/`get` 으로 찾는다.
   - **수신자 연속성 규칙 (2026-07-20, 소유자 지시)**: 후속·확정·변경 안내 메일은 **직전 관련 발송 메일의 전체 수신자 집합에서 시작**한다. 수신자를 제외할 때는 compose 요청에 제외 사유를 명시한다. 게이트는 최근 24시간 내 제목 토큰이 겹치는 발송 compose와 비교해 빠진 수신자를 승인 메시지에 ⚠️ 경고로 표시한다(발송 차단은 아님 — 최종 판단은 소유자 ✅). 이 결정론적 가드는 **compose 발송분만** 인식한다 — 게이트 밖 발송·회신(reply)은 비교 대상에 없으므로 LLM이 이 규칙을 1차 방어선으로 지켜야 한다.
   - **결과 통지 라우팅 (2026-09-01, 소유자 지시)**: 채널에서 발신 지시를 받았으면 **반드시 `--origin-channel-id`를 전달**하고, 지시 메시지 id를 알면 `--origin-message-id`도 함께 전달한다 — 지시가 승인 채널에서 왔으면 승인 스레드가 바로 그 메시지에 달린다. 발송 완료/취소 **결과**는 승인을 받은 그 요청별 스레드에 게시되고(문구에 제목·수신자·draft id·사유), 스레드 이름 앞에 `✅ 완료` / `⛔ 취소` / `⌛ 만료` 가 붙으며 스레드는 아카이브된다 — 승인 채널의 활성 스레드 목록이 곧 진행 중 요청 목록이다(이름 변경·아카이브 실패는 `THREAD-CLOSE-FAIL` 마커, 통지 자체는 유효). 승인 스레드가 없는 옛 초안은 origin 바인딩의 원 채널 스레드로, 그마저 없으면 기존 소유자 통지 경로로 간다. 결과는 `OwnerMessage`의 대상·사실·위치·인계·되돌리기 5필드로 전달한다. 대상은 제목과 draft id, 사실은 기존 수신자 표시·사유를 유지하며 본문·원문 인용·Cc를 새로 노출하지 않는다. 발송 성공 뒤에만 실행 완료와 **되돌릴 수 없음**(`irreversible`, 발송 회수 불가)을 표시하고, 취소·만료는 미발송 결과와 되돌리기 해당 없음으로 표시한다. 같은 스레드에서는 자기 링크를 생략한다. 스레드 게시 실패는 `NOTIFY-THREAD-FAIL` 마커 후 DM 폴백하며 **폴백 목적지로 다시 렌더**하여 승인 카드 URL을 싣는다. 길드 좌표가 없는 옛 레코드는 링크를 추측하지 않고 draft id 검색 키를 싣는다. 구현은 공유 `automation.interop.origin_notice`이며 봉투 모듈 부재·옛 함수 시그니처는 기존 문자열 그대로 배달한다. 헬퍼 자체를 import할 수 없으면 `NOTIFY-HELPER-MISSING` 마커 후 기존 DM 경로로 폴백한다.
2. cha가 승인 스레드 메시지에 ✅ 리액션 → 같은 `watch` cron이 다음 tick에 감지하여 발송(⛔ = 폐기).
   - **답장에 승인 스레드 링크를 싣는다 (2026-09-23, 소유자 지시)**: `compose`·`draft` 는 게시에 성공하면 `COMPOSED`/`DRAFTED` 바로 다음 줄에 `APPROVAL-THREAD draft=<id> url=<링크>` 를 낸다. 소유자에게 "승인 스레드에 게시했다"고 답할 때는 **그 `url=` 값을 그대로 붙여** 어느 스레드인지 바로 열 수 있게 한다 — "승인 스레드에서 ✅" 만 쓰면 소유자가 스레드를 직접 찾아야 한다. `url=unavailable search=<id>` 이면 링크를 지어내지 말고 "draft `<id>` 로 검색" 이라고 적는다. 줄이 없으면(`posted=0`) 게시되지 않은 것이므로 게시했다고 말하지 않는다.
3. 회신 초안과 완전히 동일한 게이트를 재사용한다(동결 argv·sha256 해시 바인딩·소유자 전용·⛔ 우선). 승인 채널은 draft 레코드의 검증된 `channel_id`로 바인딩되며, 해석 실패 시 다른 채널로 폴백하지 않는다(fail-closed — 전문 유출 방지).

### 4-1. 메일 전달 (FW — compose 의 한 형태, 2026-10-12 소유자 지시)

cha 가 "전달해줘"·"FW"·"포워드"·"내 Gmail 로 보내줘" 처럼 **이미 받은(또는 보낸) 메일을 원문·첨부 그대로** 누군가에게 보내라고 하면 새 메일을 쓰지 말고 전달 경로를 쓴다. 새 승인 경로가 아니라 위 compose 와 같은 초안·같은 승인 카드·같은 watch 발송이다:

```bash
# 기관메일 uid 를 전달 (list/get 으로 uid 를 먼저 찾는다 — 지어내지 않는다)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py compose --forward <uid> --to <주소> [--body "<메모>"] [--subject "<제목>"] --origin-channel-id <채널ID> --origin-message-id <메시지ID>
# Gmail 메시지를 전달 (message id 는 gws gmail +triage / users messages list 로 찾는다)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py compose --forward-gmail <message-id> --to <주소> [--body "<메모>"]
```

- **무엇이 나가나**: 발송 본문 = `--body` 메모(생략 가능) + 빈 줄 + `-----원본 메시지-----` 헤더(보낸 사람·날짜·받는 사람·참조·제목) + 원문 본문(회신 인용과 같은 `mail_quote`, 20,000자 상한). 제목을 주지 않으면 `FW: <원 제목>`. 원 첨부는 비공개 폴더(`~/.hermes/mail-triage/forward/`, 0700)에 **바이트 그대로 복사**되어 기존 첨부 manifest(파일명·크기·MIME·sha256)로 승인에 묶이고, 승인 뒤 한 바이트라도 바뀌면 발송이 거부된다. 발송은 지금과 같이 기관메일(mailon) 계정에서 나간다 — Gmail 원본도 기관메일 계정이 전달한다.
- **승인 카드**: 제목 줄이 `메일 전달(FW) 발송 승인 요청` 이고 To·Cc·제목·메모, `전달 원문: <기관메일|Gmail> 원문(헤더+본문) … 원 첨부 N개가 바이트 그대로`, 첨부마다 `이름 · 바이트 · MIME` 이 표시된다. ✅/⛔ 규칙·`APPROVAL-THREAD` 링크 답장은 compose 와 같다. 게시 성공 시 `COMPOSED` 다음에 `FORWARD draft=<id> source=<kimm|gmail> attachments=<N>` 이 나온다.
- **수신자는 새 메일과 똑같이 엄격하다**: 받는 사람은 `--to`/`--cc` 로 cha 가 지시한 주소뿐이다. 원문의 보낸 사람·받는 사람·참조는 **인용만 되고 수신자로 들어가지 않는다**. 첨부 10개·25 MiB 한도도 새 메일과 같아서 넘으면 `ATTACHMENT-REFUSED`(exit 2).
- **원 첨부가 하나라도 온전하지 않으면 전달하지 않는다**: 기관메일 첨부가 아직 내려받아지지 않았거나(`status≠ok`) 파일이 없거나, Gmail 첨부 크기가 선언과 다르거나 gws 읽기가 실패하면 `GATE-REFUSED FORWARD-REFUSED reason=<attachment-incomplete|attachment-missing|attachment-size-mismatch|gmail-read-failed|…>`(exit 2), 초안·복사본을 남기지 않는다. 첨부 빠진 전달을 대신 보내지 말고 사유를 그대로 보고한다(기관메일은 `list --sync` 후 재시도).
- **대리 요청(`[도리 대리 · 소유자 요청 <link>]`)도 같다**: 대리 봇의 글은 승인이 아니므로 위 명령으로 카드만 올리고, 대리 요청 본문이 알려 준 id 를 `--origin-channel-id`/`--origin-message-id` 에 넘긴다. 승인은 카드의 cha ✅ 뿐이다.
- `--forward`·`--forward-gmail`·`--in-reply-to` 는 함께 쓸 수 없다. 추가 파일은 `--attachment` 로 원 첨부 뒤에 붙일 수 있다.

#### 세미나 출장 신청 안내 표준 (cha 실제 발송본 기준)

cha가 세미나/워크샵 **출장 신청 안내** 또는 그에 딸린 정산·배차 안내를 요청하면 **가장 최근의 유사한 cha 보낸메일을 먼저 조회**하고 아래 형식을 기본값으로 사용한다. 일반 증빙 안내에는 이 템플릿을 적용하지 않는다. 2026-07-27 실제 발송본 `재난농업건설 필드AI 워크샵 출장 신청 및 배차 안내`가 대표 표본이다.

1. **제목:** 행사명을 먼저 쓰고 실제 행위를 붙인다. 예: `<행사명> 출장 신청 및 배차 안내`. `식비 N회 차감` 같은 단일 세부사항만 제목으로 삼지 않는다.
2. **도입:** `안녕하세요. <owner-name>입니다.` 한 줄 다음에 `출장 신청을 위한 <행사명> 안내사항을 아래와 같이 전달드립니다.`처럼 목적을 바로 밝힌다.
3. **행정 핵심 목록:** `출장명 → 기간 → 장소 → 세미나 구성 → 식비 차감 → 숙박비 결제·정산 방식 → 출장 계정(과제코드 포함)` 순서로 쓴다.
4. **비용·집결·배차:** 건별로 제공된 경우 `납부액·입금계좌·기한 → 집결 시각·장소 → 차량/운전자/동승자 배차표` 순서로 이어 쓴다. 같은 차량 구성원에게 일정 조율을 요청한다.
5. **첨부:** 세부 일정은 Markdown 일정표로 첨부하고 본문 끝에서 확인을 요청한다. 최종 발송본 파일명에는 `초안`을 남기지 않는다. 출장 신청 화면·예시가 있으면 `추신: 아래와 같이 출장 신청하시면 됩니다.` 뒤에 제공한다.
6. **종결:** `감사합니다.` 다음 `<owner-name> 올림`으로 끝낸다.
7. **값 재검증:** 계좌번호·금액·납부기한·과제코드·수신자·배차·식비 차감 횟수는 이전 행사에서 재사용하지 말고, 이번 요청·보낸메일·첨부의 최신값으로 검증한다. 값이 없으면 추측하지 않는다.

### 5. 수동 Triage (Legacy)
`process` 명령은 수동으로 전체 파이프라인(수집→분류→초안 생성→게시)을 실행할 때 사용하며, cron에서는 더 이상 호출되지 않는다.
### 6. 승인 게이트 규칙
- **현재 승인 표면 게이트**: 회신은 완성된 회신 전문과 sha256을 현재 정책의 소유자 전용 승인 스레드 한 건에 함께 표시한다. 원본 수신메일의 제목·본문은 승인 메시지에 넣지 않는다.
- **이모지 리액션**: 봇이 승인 메시지에 ✅(확정)와 ⛔(취소)를 미리 추가한다.
- **소유자 전용**: cha 본인의 리액션만 인정하며, 봇이나 타인의 리액션은 무시한다.
- **⛔ 우선**: ✅와 ⛔가 함께 있으면 취소로 처리한다.
- **Hash 바인딩**: 모든 승인은 초안의 해시와 바인딩되어 fail-closed로 작동한다.
- **텍스트 fallback**: `실행/취소 <id>` 텍스트는 대체 수단일 뿐이며, 에이전트는 소유자에게 텍스트 확인 지시를 보내지 않는다.
- **승인 채널 바인딩**: 승인 채널은 draft 레코드의 `channel_id`·`surface`·`policy_version`으로 결정된다 — 새 회신·compose 초안은 그 요청 전용으로 열린 승인 스레드이고, 같은 id가 `approval_thread_id`로도 남아 결과 통지가 되돌아온다. 폐지 표면의 미결정 초안은 승인 키 lease 안에서 리액션을 먼저 확인하고, 결정이 없을 때만 메시지 삭제 후 `expired`로 종결한다. 병렬 confirm 경로 없이 같은 watch cron·리액션 규칙·해시 바인딩을 공유한다.
- 리액션 사전 부착 실패에 `APPROVAL-REACTION-FAIL` 마커가 나타나면 소유자가 수동으로 반응한다.
- **키당 라이브 승인 메시지 1건**: 게시는 공유 승인 생명주기(`triage_approval` → `automation/interop/approval_lifecycle.py`)를 거친다. 승인 키는 `mail:{kind}:{uid}`이며 (a) 같은 초안·같은 해시의 재요청은 아무것도 게시하지 않고 기존 메시지를 재사용, (b) 내용이 바뀌면 옛 메시지를 **먼저 삭제한 뒤** 그 레코드를 unbind하고 새로 1건만 게시, (c) 승인 메시지가 사라졌으면 재게시, (d) cha가 이미 ✅/⛔ 한 요청은 파괴하지 않고 워처가 소비하도록 연기한다. 초안 레코드의 `message_id`(= Discord 승인 메시지 id)는 오직 이 게이트의 commit만 기록한다. 초안을 읽지 못하면 "미결 없음"이 아니라 **거부**다(fail-closed).
  - **수정 초안 교체 검증**: `discard --draft`는 초안 상태만 폐기하고 기존 Discord 승인 메시지를 남길 수 있다. 수정 요청에서는 구 초안의 `message_id`를 조회해 실제 메시지 삭제를 확인한 뒤 새 초안을 게시하고, 동일 UID에 라이브 승인 메시지가 정확히 1건인지 재조회한다.
- **producer/watcher lease**: 같은 키에 대한 에이전트 턴과 2분 `watch` cron tick은 `~/.hermes/mail-triage/approval-leases/`의 flock으로 직렬화된다(cron 래퍼에 단일 인스턴스 flock이 없어 실제로 겹친다). 레이스에서 진 쪽은 아무것도 바꾸지 않고 `deferred:lease-held`로 물러난다.


SQLite claim-before-draft로 중복 초안/발송을 방지하며, 승인된 실발송 연속 2회 실패 시 mail-mode를 no-go로 강등한다.

```bash
# 다이제스트 (08:00 cron; 동기화 실패 시 로컬 DB 폴백)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py digest

# 다이제스트 아이템 확인 (N -> uid 변환용)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py digest-items

# 다이제스트 메일 메시지에 단 소유자 답장 → 그 메일의 회신 초안
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py reply-request --key 1008-0800-3 --author-id <사용자 id> --instruction "참석 가능하다고 회신"

# 소유자 지시 기반 초안 생성
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py draft --uid <uid> --instruction "참석 가능하다고 회신해줘" [--with-evidence]

# 새 메일 작성 (현재 승인 스레드 확정 게이트)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py compose --to <주소> --cc <참조주소> --subject "<제목>" --body "<본문>" [--with-evidence]

# 첨부 포함 새 메일 (여러 파일은 --attachment 반복)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py compose --to <주소> --subject "<제목>" --body "<본문>" --attachment /private/report.pdf

# 메일 전달(FW) — 원문 인용 + 원 첨부 바이트 그대로 (Gmail 원본은 --forward-gmail <message-id>)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py compose --forward <uid> --to <주소> [--body "<메모>"]

# 프로덕션 watch (10분 cron)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py watch

# 수동 triage (Legacy)
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py process --limit 10

# 승인 거부(초안 폐기) / 목록 / 모드 확인
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py discard --draft <id>
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py list-drafts
python3 /srv/autophagy-skills/live/mail/scripts/triage_cli.py mode
```

## 지식 파사드 근거

`draft`와 `compose`의 `--with-evidence`는 상대·주제 관련 개인 노트를 읽기 전용 지식
파사드로 한 번 수집한다. 반드시 기존 entity preflight가 먼저 통과해야 하며
`ENTITY-CLARIFY`이면 근거 조회를 시작하지 않는다. 근거는 Codex 프롬프트 재료와
소유자 전용 승인 표면, `~/.hermes/mail-triage/evidence/*.evidence.json`(0600)에만 남는다.

**수신자에게 전송될 제목·본문에는 `[En]` 인용이나 사적 출처를 절대 넣지 않는다.**
생성 결과는 팩 기준 검증 뒤 모든 근거 id를 제거하고 승인 해시를 계산한다. 출처 확인은
owner-only 표면에서만 파사드 `sources` 형식으로 한다. 근거가 없으면 `근거 없음`, 조회
불가면 `근거 수집 불가`를 owner-only 표면에 표시하고 초안 생성은 계속한다. 메일 발송과 소유자
✅ 게이트는 바뀌지 않는다. 정본은
[`지식 계층 규약`](../../docs/guide/지식-계층-규약.md)이다.

## 메일 첨부파일 Drive 아카이브

MailOn 첨부파일은 Google Drive `autophagy/메일 첨부파일/<연도>/<월>/<inbox|sent|other>/`에 자동 보관한다. 파일명은 `<UID>__<원본명>`이고, `(uid, filename)`의 비공개 SHA-256 키와 `~/.hermes/mail-attachment-drive/archive.db`로 멱등성을 유지한다. 업로드한 파일은 `DriveClient`의 owner-only 권한과 `sha256Checksum` 메타데이터로 검증한 **뒤에만** 원장에 기록하며, 검증을 위해 다시 내려받지 않는다.

- Hermes cron `mail-attachment-drive-watch`가 30분마다 실행한다. 계정 홈에는 `~/.hermes/scripts/mail_attachment_drive_watch.py` 래퍼만 두며, 이는 `skills/mail/deploy-manifest.txt`에 선언된다.
- 수동 실행은 다음과 같다. `--workers`는 1~8만 허용한다.

  ```bash
  python3 /srv/autophagy-skills/live/mail/scripts/mail_attachment_drive_sync.py [--limit N] [--workers 1..8]
  ```

- 성공은 무음이다. 실패하면 주소·파일명·본문을 내보내지 않고 한 줄의 `MAIL-ATTACHMENT-DRIVE-FAIL code=<code>` 마커만 전달한다.
- 동기화 스크립트는 **`/srv/autophagy-skills/live/mail/scripts/mail_attachment_drive_sync.py`에서만** 실행한다. 마운트가 있는 호스트에서 다른 사본의 실행은 `mail_runtime.governed_copy_refusal`이 거부한다.
- `mail_attachment_archive.py`는 계획·명명·archive.db 상태를, `mail_attachment_drive_sync.py`는 Drive 실행 CLI를 맡는다.

## 내용 처리 변경 (2026-10-09)

특허·patent·기밀이라는 낱말로 메일을 가리거나 별도 저장·모델 경로를 고르지 않는다.
중요도·스팸·일정·예산·회신 필요 분류와 소유자 승인, 인증, 경로·첨부 검증은 그대로다.
기존 데이터는 그대로이며, 과거 초안은 기존 위치에서 읽고 새 초안은 공통 저장소에 둔다.

## 절대 규칙

1. **발송은 게이트 경유만**: mailon send를 직접 호출하지 않는다. 승인 없는
   발송 경로는 존재하지 않으며, 직접 터미널 호출은 배포된 외부효과 게이트
   (`mailon_send` 룰)가 fail-closed로 차단한다.
2. **공유 모델 체인**: 내용에 따른 별도 모델 제한 없이 계정 설정의 주 모델과 폴백을 따른다.
3. **비밀·공개 누출 검사**: 자격증명과 실제 메일 원문은 로그·공개 저장소에 쓰지 않는다.
4. **읽기 전용 래퍼 유지**: `mail_wrapper.py`의 mailon 호출 표면은 sync/status/resolve
   뿐이며 그 외 mailon 명령은 코드로 거부한다(send는 triage 게이트의 동결 argv 전용).
   래퍼 공개 서브커맨드는 list/get/classify/status/resolve다.
5. 인증 실패(exit 2) 시 자동 무한 재시도 금지 — 재인증 안내를 따르고 반복
   실패는 cha에게 보고. 연속 발송 실패 2회는 mail-mode 재판정(no-go)이다.
   발송 실패를 보고할 때는 초안의 `last_error.cause`(또는 실패 문구의 `cause=`)로
   **어느 확인에서 멈췄는지** 그대로 말한다 — 원인을 추측하지 않는다. 예:
   `login_failed`=로그인, `mailbox_list_unavailable`=로그인 뒤 메일 목록을 읽지 못해
   발송 전에 멈춤(메일은 나가지 않음), `send_post_http_error`·`send_post_missing`=발송
   요청 단계, `sent_mail_unconfirmed`=보낸 뒤 보낸메일함에서 확인 못 함(재시도 전에
   보낸메일함 확인). `cause` 가 없으면 옛 기록이다.
   로그인 뒤 목록이 안 그려지면 mailon 이 먼저 페이지를 최대 2번 새로고침하고, 그래도 안 되면
   메뉴 옆 받은메일함 번호(서버 확인 뒤)를 쓴다. 어느 길로 찾았는지는 `send-attempts.jsonl` 의
   `list_path`(`rows`·`reload-1`·`reload-2`·`sidebar`)에 남는다 — 물으면 그 값을 그대로 말한다.
   웹메일 페이지가 load 이벤트를 끝내 받지 못해 스스로를 만들지 못하면(목록 0행, compose 가
   `tabPanel._getMenuById is not a function`) mailon 이 페이지 자신의 load 처리기를 대신 돌리고,
   compose 가 그래도 안 열리면 새로고침 후 다시 연다. 어느 길이었는지는 `page_boot`
   (`booted`·`onload-run`·`not-booted`)로 남는다 (2026-10-13).
   발송 실패 기록에는 원인 코드에 더해 `step`(멈춘 단계: `page_boot`·`mailbox_check`·
   `compose_open`·`compose_fields`·`editor_fill`·`attachment_upload`·`recipient_commit`·
   `send_trigger`·`sent_verify`·`login`·`session`), `browser_command`(실패한 agent-browser
   명령), `error_kind`(`js_type_error`·`cdp_timeout` 등)가 실리고 실패도 `send-attempts.jsonl`
   에 한 줄(`status: error`) 남는다. 승인된 초안이 실패하면 워처가 그 초안의 승인 스레드에
   「⚠️ 발송 실패」 안내를 올리고 틱은 실패(rc 6)로 끝난다. 재시도는 초안별로 10·20·40·80·160분
   간격, 최대 6회(`failed_attempts`·`next_attempt_at`)이고 넘으면 `blocked`(`retry_exhausted`)로
   멈춘다 — 그 뒤 다시 보내려면 새로 요청한다. 대기 중인 틱은 `RETRY-WAIT` 을 남긴다.
6. **브라우저 세션은 요청마다 새로 만든다 (2026-09-07)**: mailon 을 부르는 프로세스는
   저마다 자기 agent-browser 세션(`<이름>-run-<pid>-<난수>`)을 열어 매번 새로 로그인한다.
   세션은 프로세스를 넘어 남지 않으므로 "세션 만료" 라는 실패 원인은 존재하지 않는다 —
   같은 이름을 공유하던 두 발송이 한 탭을 두고 다투다 서로의 로그인 폼을 깨뜨린 것이
   2026-09-07 `auth_error` 사고의 실제 원인이었다. 실제 발송(dry-run 아님)은 런타임
   `data/send.lock` 으로 단일 비행이라 같은 초안이 두 번 나가지 않는다.

## Gmail 발송 운영 (RTS-3)

### 계정 선택

- 새 Gmail 메일은 요청에서 `account=gmail`을 명시해야 한다. 계정이 없으면 임의 기본값을
  고르지 않고 거부한다.
- 답장은 원본 스레드 계정을 상속한다. Gmail 스레드는 Gmail으로, KIMM 스레드는 KIMM으로
  유지한다. 소유자가 명시한 계정만 이 상속보다 우선한다.
- KIMM은 기존 MailOn argv를 그대로 사용한다. Gmail 전용 `gws` 경로와 혼용하지 않는다.

### 소유자 전용 승인 형식

Gmail 초안은 기존 mail 승인 라이프사이클을 재사용하여 그 요청의 승인 스레드에 한 건만 게시한다. 메시지에는
발신 계정, 작업(`+send` 또는 `+reply`), 수신자, 회신 대상, 제목, 본문, draft SHA-256 및 action
hash가 들어간다. 첨부마다 파일명·바이트 크기·SHA-256을 순서대로 표시하며 로컬 원본 경로는
표시하지 않는다. cha의 ✅만 실행을 허용하고 ⛔는 우선 취소한다.

운영 발송 승인은 DM의 ✅/⛔ 리액션만 사용한다. 서명된 텍스트 승인은 오프라인 E2E에서만 허용되는
시험 경로이며 운영 발송 승인으로 사용하지 않는다.

### 감사 로그 조회

`TRIAGE_APPROVAL_LOG`가 설정되어 있으면 그 경로를, 없으면
`/srv/autophagy-agents/logs/approvals.jsonl`을 읽는다. 다음 조회는 본문·수신자·경로를 출력하지
않고 Gmail 승인/실행의 action hash와 결과만 표시한다.

```bash
python3 - "${TRIAGE_APPROVAL_LOG:-/srv/autophagy-agents/logs/approvals.jsonl}" <<'PY'
import json
import sys
from pathlib import Path

for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    record = json.loads(line)
    if record.get("action") in {"external_effect.approval", "gmail.approval_execution"}:
        print(json.dumps({key: record.get(key) for key in ("action", "hash", "result")}, ensure_ascii=False))
PY
```

### Fail-closed 오류와 재승인

- 승인 없음·거부·만료(15분)·중복 승인 레코드는 발송하지 않는다.
- 본문·수신자·회신 대상·동결 argv가 달라지거나 첨부가 교체·수정·삭제되면 발송 직전 중단한다.
- 성공 기록이 있는 동일 action hash는 재시도해도 두 번째 메일을 보내지 않는다.

이 오류가 나오면 `approvals.jsonl`이나 기존 초안을 손으로 고치지 않는다. 원래 요청에서 새 Gmail
초안을 만들고, 변경된 DM 내용을 cha가 다시 확인한 뒤 새 메시지에 ✅를 남긴다. 인증·서비스 오류는
먼저 재인증 또는 서비스 복구를 확인하고 같은 절차로 새 승인을 받는다.
