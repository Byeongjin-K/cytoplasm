# 메일 전달(FW) — 원문·첨부 그대로, 같은 승인 카드로

## 무엇을

이미 받은(또는 보낸) 메일을 원문과 원 첨부 그대로 다른 주소로 전달한다. 기관메일은 `compose --forward <uid>`, Gmail 메시지는 `compose --forward-gmail <message-id>` 를 쓴다. 새 메일 작성(`compose`)과 같은 초안, 같은 승인 카드(✅/⛔), 같은 발송 경로를 쓴다.

## 왜

2026-10-12 소유자가 "앞으로 메일 필요하면 gmail로 보내라고 해서 받아" 라고 했다. 그런데 에이전트는 기관메일을 Gmail 로 전달하지 못했다. 승인 CLI 에 새 메일과 회신만 있었고, 원문과 첨부를 보존하는 전달이 없었기 때문이다. 대리 요청(`[도리 대리 · 소유자 요청 …]`)도 같은 이유로 멈췄다.

## 어떻게 동작하나

- 확장한 함수는 `skills/mail/scripts/triage_cli.py` 의 `cmd_compose` 다. 원문을 읽어 오는 일만 `mail_forward.py` 가 맡는다. 기관메일은 MailOn 이 저장한 원문 문서와 `state.db` 의 첨부 행·파일을 읽고, Gmail 은 `gws gmail users messages get`/`attachments get` 으로 읽는다. 읽은 결과는 기존 `compose_and_post` → `create_draft` 에 그대로 넘긴다.
- 발송 본문은 메모(`--body`, 생략 가능) 아래에 `-----원본 메시지-----` 헤더와 원문 본문을 붙인 것이다. 회신 인용과 같은 `mail_quote` 를 쓰며, 제목을 주지 않으면 `FW: <원 제목>` 이 된다.
- 원 첨부는 비공개 폴더 `~/.hermes/mail-triage/forward/`(0700)에 바이트 그대로 복사한다. 그 사본이 기존 첨부 manifest(이름·크기·MIME·sha256)로 승인에 묶이므로, 승인 뒤 파일이 바뀌면 발송이 거부된다.
- 수신자는 `--to`/`--cc` 로 지시한 주소뿐이다. 원문의 보낸 사람·받는 사람·참조는 인용에만 남고 수신자가 되지 않는다. 첨부 10개·25 MiB 한도도 새 메일과 같다. 결국 전달로 새 메일보다 더 할 수 있는 일은 없다.

## 사용 시나리오

**Happy path** — cha: "어제 온 협약서 메일 내 Gmail 로 전달해줘"
1. 에이전트가 `list` 로 uid 를 찾고 `compose --forward <uid> --to <Gmail 주소> --origin-channel-id … --origin-message-id …` 를 실행한다.
2. 승인 스레드에 `메일 전달(FW) 발송 승인 요청` 카드가 올라온다. 카드에는 To, 제목 `FW: 과제 협약서 송부`, 「전달 원문: 기관메일 원문(헤더+본문)이 발송 본문 하단에, 원 첨부 2개가 바이트 그대로 붙습니다」 줄, 첨부별 `이름 · 바이트 · MIME` 이 표시된다. 에이전트는 답장에 `APPROVAL-THREAD … url=` 링크를 싣는다.
3. cha 가 ✅ 를 누르면 다음 watch tick 에 기관메일 계정이 원문과 첨부 2개를 그대로 담아 발송하고, 결과는 같은 스레드에 남는다.

**거부** — 첨부 하나가 아직 내려받아지지 않은 메일(`status=fail`)이면 `GATE-REFUSED FORWARD-REFUSED reason=attachment-incomplete …`(exit 2)로 멈춘다. 초안도 복사본도 남지 않는다. 에이전트는 첨부를 뺀 채 보내지 않고 사유를 보고한다. Gmail 첨부 크기가 선언과 다르거나 읽기에 실패해도 같은 방식으로 멈춘다.

## 관련

- 스킬: `skills/mail/SKILL.md` §4-1, 코드 `skills/mail/scripts/mail_forward.py`, `triage_cli.py`(`cmd_compose`)
- 승인: 기존 mail compose 승인 카드와 `watch` cron(`mail-triage-watch`). 새 승인 경로는 없다.
- 테스트: `tests/unit/test_mail_forward.py`(첨부 2개 픽스처 통합 테스트 포함)
- 한계: 발송 계정은 지금처럼 기관메일(mailon)이다. Gmail 원본도 기관메일 계정에서 전달된다. 원문 본문은 회신 인용과 같이 20,000자에서 잘린다.
