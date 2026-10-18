# Obsidian 노트 저장 요청 — 표준 승인 카드와 링크 확인

## 무엇을

경로와 본문이 정해진 Obsidian 노트 저장 요청을 명령 하나(`python3 -m automation.obsidian_write request`)로 받는다. 이 명령은 요청을 승인 카드 한 장으로 올리고, 카드에 **전체 경로·본문 해시·action_hash** 를 묶는다. 소유자가 그 카드에 ✅ 를 한 번 누르면 워처(`obsidian-note-watch`, 2분)가 정확히 그 경로와 본문을 볼트에 쓰고 push 한다. 그다음 원격에서 다시 읽어 해시가 같은지 확인하고 영수증을 같은 스레드에 남긴다. 카드를 올리기 전에는 본문의 `[[링크]]` 가 볼트에 실제로 있는지 읽기 사본으로 대조하고, 관련 노트 후보 3~5개를 검색 인덱스에서 골라 카드에 함께 싣는다.

## 왜

2026-10-18 Q233 소유자 지시 “6개 다 카드 없이 바로 + 하루 요약에 남기기”에 따라, 소유자 원 지시와 `auto-write-obsidian` 전환을 확인한 유돌 잡은 같은 인자의 `write-now`로 카드 없이 저장하고, 원격 해시 검증 뒤 `OWNER-COMMAND-WRITTEN obsidian <relpath> <sha256 앞 12자>`가 나올 때만 하루 요약 원장에 남긴다(저장 감사는 `method=owner_command`와 지시 좌표를 보존).

2026-10-07 대리 요청으로 "이 경로에 새 노트를 만들어 줘"가 왔을 때, 임의 노트를 쓰는 승인 요청 경로는 저장소에 없었다. 승인 카드를 만드는 생산자는 plaud 동기화와 메모리 재배치뿐이었다. 에이전트는 초안을 손으로 만들고 소유자에게 **자기 채팅 메시지에 ✅ 를 눌러 달라고** 했다. 그 ✅ 를 읽는 워처는 없었으므로 노트는 저장되지 않았다. 답장에 적힌 경로도 앞의 PARA 접두가 빠진 채였다. 이 변경으로 그 요청이 갈 표준 경로가 생겼다. 경로는 소유자가 준 그대로 검증하고, 줄이거나 다시 짓지 않는다.

링크 확인이 필요한 이유도 있다. Obsidian 은 해석되지 않는 `[[링크]]` 를 빈 페이지로 보여 줄 뿐 오류를 내지 않는다. 그래서 지어낸 링크는 누군가 눌러 보기 전까지 진짜 링크처럼 보인다.

## 사용 시나리오

- **정상**: 소유자(또는 대리 봇)가 경로와 본문을 준다. 에이전트는 먼저 recall 로 관련 노트를 찾아 실제 제목으로 `[[ ]]` 를 단다. 그다음 본문을 파일로 저장하고 아래 명령을 실행한다.
  ```bash
  cd /srv/autophagy-agent-current && python3 -m automation.obsidian_write request \
    --relpath '000_PARA/Resource/<폴더>/<이름>.md' --body-file ~/.hermes/tmp/note.md \
    --origin-channel-id <지시 채널> --origin-message-id <지시 메시지>
  ```
  출력의 `APPROVAL-THREAD … url=` 링크를 답장에 싣는다. 소유자가 카드에 ✅ 를 누르면 2분 안에 노트가 볼트에 생기고, 스레드에 「저장 완료 · 원격 sha256 일치」가 남은 뒤 스레드가 닫힌다.
- **없는 링크**: 출력에 `LINK-UNRESOLVED [[x]]` 가 나오고 카드에도 「⚠ 볼트에 없는 링크」로 표시된다. 에이전트는 링크를 고치거나 평문으로 바꿔 **같은 경로로 다시 요청**한다. 소유자가 아직 누르지 않은 이전 카드는 새 카드로 대체된다.
- **취소**: 카드에 ⛔ 를 누르면(✅ 와 함께 눌려도 ⛔ 가 이긴다) 볼트는 바뀌지 않고, 스레드에 「저장 취소」가 남는다.
- **본문 변조**: 승인 뒤 동결 본문 파일이 바뀌면 push 하지 않고 `failed` 로 끝낸다. 해시가 맞지 않기 때문이다.
- **기존 노트**: 같은 경로에 노트가 이미 있으면 카드 첫 줄이 「기존 노트 덮어쓰기 ⚠」다.

## 관련

- 구현: `automation/obsidian_write/note_request*.py`, `links.py`, `cron/obsidian_note_watch.py`, `deploy.sh`·`deploy-manifest.txt`
- 승인: `ApprovalKind.OBSIDIAN_WRITE` 요청별 스레드, 공용 `approval_lifecycle` 파사드, push 직전 외부효과 게이트 `obsidian_write_note_push`
- 지침: `skills/doctype/SKILL.md` 「Obsidian 노트 저장 요청」
- 회귀: `tests/unit/test_obsidian_note_request.py`
