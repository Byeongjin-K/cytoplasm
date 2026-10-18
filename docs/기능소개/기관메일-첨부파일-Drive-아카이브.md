# 기능 소개 — 기관메일 첨부파일 Drive 아카이브

**완료:** 2026-09-01 · **스킬:** `mail` · **시작:** 2026-08-29 소유자 지시

## 무엇을
MailOn의 첨부파일을 Google Drive `autophagy/메일 첨부파일/<연도>/<월>/<inbox|sent|other>/`에 자동 보관한다. 파일명은 `<UID>__<원본명>`이며, 2026-08-29 이후 보관된 첨부파일은 3,342건이다.

## 왜
소유자가 채팅에서 "모든 MailOn 첨부를 Google Drive에 저장하고 계속 자동으로 해 달라"고 요청한 뒤, 초기 구현은 ops 미러 체크아웃에서만 만들어져 커밋되지 않은 채 홈 사본과 cron으로 운영됐다. 동작은 했지만 배포본·Drive I/O 경계·공개 여부가 관리되지 않았다. 이번 착지가 이를 governed 기능으로 만든다.

## 핵심 동작 (불변식)
- `(uid, filename)`의 비공개 SHA-256 키와 `~/.hermes/mail-attachment-drive/archive.db`로 중복 보관을 막는다. 업로드가 owner-only 권한과 `sha256Checksum` 메타데이터 검증을 통과한 뒤에만 원장을 기록한다. 검증을 위해 다시 내려받지 않는다.
- Drive I/O는 `automation.drive_client.DriveClient`만 사용해 폴더 보장과 파일 upsert를 수행한다. 이 보관물은 산출물이 아니라 저수준 클라이언트 재사용 기능이므로 `automation/state_backup`과 같은 이유로 Drive 산출물 taxonomy를 적용하지 않는다.
- 동기화는 `/srv/autophagy-skills/live/mail/scripts/mail_attachment_drive_sync.py`에서만 실행한다. 마운트가 있는 호스트에서 다른 사본을 실행하면 `mail_runtime.governed_copy_refusal`이 거부한다.
- Hermes cron `mail-attachment-drive-watch`가 30분마다 live 동기화를 호출한다. 홈에는 `~/.hermes/scripts/mail_attachment_drive_watch.py` 래퍼만 있으며 `skills/mail/deploy-manifest.txt`가 이를 선언한다.
- 성공은 무음이다. 실패는 주소·파일명·본문을 감춘 단일 `MAIL-ATTACHMENT-DRIVE-FAIL code=<code>` 마커로만 전달한다.
- 다운로드 실패·로컬 파일 누락은 수신일(없으면 `first_seen`)이 최근 14일이고 MailOn 자체 시도가 5회 미만인 행만 복구한다. 오래된 행·MailOn 소진 행은 조용히 넘긴다. 진행 중 재시도는 성공·무음이며, 별도 복구 예산 5회 소진은 틱당 한 통(건수·첨부명 최대 3개)으로 페어 채널에 알린다. 고장 채널 미설정은 기존 통지 경로다(전송 실패는 다음 실행에 재시도, 성공한 Drive 파일은 재업로드하지 않음).

## 사용 시나리오
1. **(happy)** 소유자가 새 메일을 받거나 첨부를 보낸다 → 다음 30분 cron tick이 파일을 찾는다 → 연도·월·메일함 폴더에 `<UID>__<원본명>`으로 upsert → 권한과 checksum 메타데이터를 확인 → 성공한 파일만 `archive.db`에 기록한다. 필요하면 다음 명령으로 수동 실행한다.

   ```bash
   python3 /srv/autophagy-skills/live/mail/scripts/mail_attachment_drive_sync.py [--limit N] [--workers 1..8]
   ```

2. **(실패)** Drive 업로드 또는 검증이 실패하면 원장 기록을 남기지 않아 다음 tick이 안전하게 재시도한다. `MAIL-ATTACHMENT-DRIVE-FAIL code=<code>` 진단은 설정된 페어 채널에서 유돌이 먼저 받는다. 고장 채널이 없는 설치는 기존 경로를 유지한다.
3. **(거부)** 관측 미러·리뷰 체크아웃 등 live 마운트 밖의 사본으로 동기화를 실행하면 실행 경로 가드가 거부한다. 낡은 사본이 보관 상태를 바꾸지 못한다.

## 관련
- `skills/mail/scripts/mail_attachment_drive_sync.py`, `skills/mail/scripts/mail_attachment_drive_watch.py`
- `automation/drive_client.py`, `skills/mail/scripts/mail_runtime.py`, `skills/mail/deploy-manifest.txt`
- 회귀: `tests/unit/test_mail_attachment_drive_sync.py`
