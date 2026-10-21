# 기관메일 페이지가 load 를 못 받아도 승인된 메일이 나간다 — 실패하면 멈춘 단계를 승인 스레드에 알린다

## 무엇을

- 기관 웹메일 페이지가 스스로를 다 만들지 못한 채 멈춰 있으면(목록 0행, 메일쓰기 불가), mailon 이
  페이지 자신의 load 처리기를 대신 돌려 페이지를 완성한 뒤 발송한다. 그래도 메일쓰기가 안 열리면
  새로고침(2026-10-12 목록 복구와 같은 길) 뒤 다시 연다.
- 발송이 실패하면 기록이 **멈춘 단계 · 실패한 브라우저 명령 · 오류 종류**를 원인 코드와 함께 남기고,
  실패도 `send-attempts.jsonl` 에 한 줄 남는다.
- 승인된 초안이 실패하면 워처가 **그 초안의 승인 스레드**에 「⚠️ 발송 실패」 안내를 올리고, 틱은
  실패로 끝난다. 자동 재시도는 초안별로 10·20·40·80·160분 간격, 최대 6회까지만 한다.

## 왜

2026-10-13 소유자가 ✅ 한 메일 전달(FW)이 한 시간 넘게 6분마다 같은 자리에서 실패했는데 아무 안내가
없었다. 노드 로그의 실패 명령은 메일쓰기를 여는 `eval`(`window._tbar.compose()`)이었고 오류는
`TypeError: tabPanel._getMenuById is not a function` — FW 고유 문제가 아니라 그 시간대의 모든 발송이
같은 자리에서 멈췄다. 읽기 전용 실측(발송·제출 없음)으로 원인을 찾았다:

- 웹메일 페이지는 `cr.onLoad` 로 등록한 콜백에서 탭·목록을 만들고, 그 콜백은 window **load**
  이벤트 처리기(`cr.ev.processOnLoad`)가 실행한다.
- 느린 구간에서는 load 가 끝내 오지 않는다 — `readyState` 가 60초 넘게 `interactive`, load 시작
  시각 0. 새로고침해도 같았다. 그래서 목록은 0행이고 탭 객체 자리에 같은 id 의 DOM 요소가 남아
  `_getMenuById` 가 없었다.
- 그 처리기를 직접 돌리자 3초 안에 목록 20행이 그려지고 메일쓰기가 수신자 칸·CSRF 토큰과 함께 열렸다.
  처리기는 자기 대기열을 비우므로 나중에 진짜 load 가 와도 두 번 돌지 않는다.

안내가 없던 이유: 브라우저 단계 실패(`external_service_error`)는 실패로 세지 않아 초안이 `pending` 으로
남았고, 워처는 매 틱 같은 메일을 다시 시도했으며 그 실패를 소유자에게 알리는 길이 없었다.

## 사용 시나리오

- **느린 구간에 승인**: 소유자 ✅ → 로그인, 페이지가 15초 안에 스스로 완성되지 않음 → load 처리기를
  대신 실행 → 목록·메일쓰기 정상 → 발송. 발송 기록에 `"page_boot":"onload-run"`.
- **그래도 실패**: 승인 스레드에
  `⚠️ 발송 실패: FW: … (draft 5ff53c)` / `멈춘 단계: compose_open · 원인: compose_not_ready · 실패한 명령: eval · 오류: js_type_error` /
  `시도 1/6 · 다음 자동 재시도: 10-13 16:10 KST`. 다음 틱은 그 시각 전에는 `RETRY-WAIT` 만 남긴다.
- **느린 구간에 이름으로 주소 찾기**(2026-10-21): `mail_wrapper.py resolve --name` 도 메일쓰기를 열기 전에
  같은 load 처리기 보정을 한다. 이전에는 이 길만 빠져 있어 로그인은 되는데 매번 exit 2
  (`auth_or_browser_error`)로 끝났고, 보정 뒤 같은 조회가 조직도 후보를 돌려줬다.
- **6회 모두 실패**: 마지막 안내가 「자동 재시도를 멈췄습니다. 메일은 발송되지 않았습니다」를 말하고
  초안은 `blocked`(`retry_exhausted`). 다시 보내려면 새로 요청한다.

## 관련

- 페이지 부팅 보정: `skills/mail/vendor/mailon/folders.py` `finish_page_boot`(동기화·발송·수신자 이름 조회 `resolve.py` 공용,
  `recover_inbox_uid` 가 새로고침 전후로 사용), 발송 `send.py` `_open_compose_with_recovery`,
  실패 단계 `SendStepError`·`failure_details`·`record_send_failure`
- 재시도 상한·안내 문구: `skills/mail/scripts/mail_send_retry.py`, 기록 `triage_gate_mailon.py`,
  워처 `triage_cli.cmd_watch`(`SEND-FAILED`·`RETRY-WAIT`). 승인 해시·중복 발송 방지·보낸메일함 확인은
  그대로이고, 전체 mail-mode 강등(연속 발송 실패 2회 → no-go)은 브라우저 단계 실패에 쓰지 않는다 —
  웹메일이 회복되면 승인된 메일이 그대로 나가야 하기 때문이다.
- 시험: `tests/unit/test_mailon_page_boot.py`, `tests/unit/test_mailon_resolve_page_boot.py`, `tests/unit/test_mail_send_failure_notice.py`,
  `skills/mail/vendor/tests/test_send_failure_record.py`, 샌드박스 `skills/mail/scripts/scenario.sh` 11b
- 앞선 완화: [목록 미표시 새로고침 우선](기관메일-목록-미표시-새로고침-우선.md)
