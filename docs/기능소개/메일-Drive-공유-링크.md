# Drive 공유 링크 메일 — 업로드·사람별 공유·발송을 ✅ 한 번으로

## 무엇을

메일에 첨부할 수 없는 큰 파일을 Drive 에 올리고, 지정한 사람에게만 보기 권한을 준 뒤, 그 링크를 넣은 메일을 보낸다. 세 단계가 승인 카드 하나의 ✅ 로 함께 실행된다. 명령은 기존 새 메일 작성에 옵션을 더한 `compose --drive-share <파일> --share-with <이메일>` 이다.

## 왜

2026-10-14 대리 요청이 약 192 MB 실행 파일을 소유자 Drive 에 올려 상대에게 보기 전용으로 공유하고, 기존 메일 스레드에 링크를 넣어 회신하라고 했다. 메일 승인 경로는 있었지만 Drive 업로드·공유 승인 경로가 없어서 에이전트가 멈췄다. 소유자 지시는 다음과 같았다: "오토파지에 \"드라이브 업로드·공유\" 승인 경로를 새로 만들고 그걸로 보냄 (몇 시간, 다음에도 씀)".

## 어떻게 동작하나

- 새 카드나 새 워처를 만들지 않았다. compose 초안 레코드에 `drive_share` 필드를 더했고, 이 필드가 승인 해시(`triage_core.draft_sha256`)에 묶인다. 묶이는 값은 파일 sha256·크기, Drive 폴더·파일명, 공유받는 사람, 역할 `reader` 다.
- 요청 시점에 파일을 비공개 폴더(`~/.hermes/mail-triage/drive-share/`, 0700)에 복사한다. ✅ 뒤 그 사본이 한 바이트라도 다르면 올리지 않는다.
- ✅ 를 받으면 같은 `mail-triage-watch` tick 에서 다음 순서로 진행한다.
  1. Drive 파사드 `automation.drive_outputs.share` 로 `autophagy/공유/<연도>/` 에 올린다.
  2. Drive 가 계산한 sha256·크기를 대조한다.
  3. 소유자 외 권한이 없는지 확인한다.
  4. 사람마다 `type=user, role=reader` 권한을 준다. Drive 알림 메일은 보내지 않는다.
  5. 권한이 정확히 "소유자 + 지정한 사람" 인지 다시 확인한다.
  6. 본문의 `<Drive 링크>` 자리에 링크를 넣어 기관메일로 보낸다.
- 링크 공개(anyone)·도메인 공유·편집 권한은 코드에 만드는 길이 없고, 파일에서 보이면 메일을 보내지 않는다.
- 에이전트가 터미널에서 `gws drive permissions create` 로 직접 공유하는 것은 외부효과 규칙 `gws_drive_permission_grant` 가 막는다.

## 사용 시나리오

**Happy path** — cha: "이 zip 을 Drive 에 올려 정 소장님 Gmail 에만 보기 공유하고, 링크를 넣어 기존 스레드에 회신해줘"
1. 에이전트가 `compose --to <소장 주소> --cc <참조> --subject "Re: …" --body "… 다운로드: <Drive 링크> …" --drive-share <zip> --share-with <Gmail 주소> --attachment 사용안내.txt --in-reply-to <uid>` 를 실행한다.
2. 승인 스레드의 카드에 받는 사람·제목·본문과 함께 `- 보낼 때 `<파일 이름>`(<크기>) 공유 링크가 채워짐(공유 대상: <주소>, 보기)` 한 줄이 표시된다(카드 v5, 2026-10-19). 본문이 길어 첨부로 가는 카드에도 같은 줄이 붙는다. 파일 sha256·Drive 폴더 같은 내부 값은 레코드에만 남는다. 에이전트는 답장에 `APPROVAL-THREAD … url=` 링크를 싣는다.
3. cha 가 ✅ 를 누르면 업로드·검증·공유 뒤 링크가 들어간 메일이 나가고, 결과가 같은 스레드에 남는다.

**실패** — 업로드가 끊기거나, Drive 체크섬이 다르거나, 파일에 다른 공유가 이미 걸려 있으면 메일은 나가지 않는다. 승인 스레드에는 `⚠️ 발송 실패 … 멈춘 단계: drive_share · 원인: drive_permission_refused` 처럼 단계와 원인이 남는다. 전송 오류는 메일 재시도와 같은 간격으로 자동 재시도하고, 체크섬·권한 이상은 재시도하지 않는다.

**거부** — 본문에 `<Drive 링크>` 자리가 없거나 `--share-with` 가 이메일 주소가 아니면 exit 2 로 끝나고, 초안도 복사본도 남지 않는다. 반대로 본문에 `<Drive 링크>` 자리가 있는데 `--drive-share` 가 없어도 카드를 올리지 않고 exit 2 로 거절한다(2026-10-19 형 "앞으로 이런 실수 없도록 해" — 전에는 발송 때에야 실패했다).

## 관련

- 스킬: `skills/mail/SKILL.md` §4-2. 코드는 `skills/mail/scripts/mail_drive_share.py`, `triage_cli.py`(`cmd_compose`), `triage_gate.py`(`execute_draft`), Drive 쪽은 `automation/drive_outputs.py`(`share_target`·`share`)와 `automation/drive_client.py`(`shared_readers`·`grant_reader`)다.
- 승인: 기존 mail compose 카드와 `mail-triage-watch`. 외부효과 규칙 `gws_drive_permission_grant` 의 카드 생산자는 `triage_approval.request_approval` 이다.
- 테스트: `tests/unit/test_mail_drive_share.py`
- 한계: 기관메일(mailon) 발송만 지원한다. Gmail 발송 초안에는 붙일 수 없다. Drive 폴더는 `autophagy/공유/` 아래로 제한된다.
