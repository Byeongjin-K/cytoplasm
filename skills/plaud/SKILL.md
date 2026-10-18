---
name: plaud
description: "Plaud 녹음·라이프로그 동기화 상태나 전사본 위치를 물을 때 사용. Plaud 라이프로그 동기화(plaud-sync 워처)의 상태를 읽기 전용으로 보고한다 — 로컬 전사 대기·승인 대기·저장 대기 건수와 로컬 전사본(.md) 경로. 트리거: 'plaud 상태', '라이프로그 동기화 상태', 'plaud 승인 대기 몇 건', 'plaud 마지막 폴', 'plaud 전사본 어디', 'plaud 녹음 회의록으로'. READ-only — Plaud·Discord·vault 어디에도 쓰지 않는다. 승인 카드(✅/⛔)는 이 스킬이 아니라 #agent-chat 요청별 스레드에 있다."
version: 1.3.2
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Plaud, Lifelog, Status, Read-Only]
prerequisites:
  commands: [python3]
---

# plaud — 라이프로그 동기화 상태 (읽기 전용)

## 무엇을

`plaud-sync` 워처(no-agent cron, 10분 틱)가 Plaud 녹음을 발견해 **오디오를 내려받아 노드에서
직접 전사**(whisper.cpp + 화자 분리)하고, 그 전사본으로 노트를 동결한 뒤
Obsidian vault 에 밀어 넣는다. 기본은 소유자 승인 카드 경로다. Q231의 릴리스 스위치
`auto-write-plaud`가 켜지면 동결 SHA를 확인해 카드 없이 같은 writer로 저장하고 매일
21:30 KST의 「오늘 자동으로 쓴 것」 요약에 남긴다. 스위치 삭제+재배포는 카드 경로 복구다.
상태가 `planned`여도 자동 모드이면 승인 대기가 아니라 쓰기·재시도 대기일 수 있으므로
카드를 찾으라고 안내하기 전에 모드를 구별한다. 이 스킬은 **상태 파일**을 읽어 한눈에 보고한다:

- 마지막 Plaud 폴 시각(UTC + KST)
- 레코드 수와 status 별 건수 — `transcribing`(발견·오디오 로컬 전사 대기) · `planned`(전사 완료·미게시) ·
  `posted`(카드 게시, ✅ 대기) · `approved`(✅ 받음, 다음 틱에 저장) · `written`(vault 저장 완료) ·
  `abandoned`(⛔·만료·최소 길이 미만·전사 포기)
- 전사 대기 중인 녹음: 녹음 id · 시도 횟수 · 마지막 사유(예: `rc=4 로컬 전사 도구를 찾지 못했습니다`)
- 승인 대기 중인 녹음 목록: 녹음 id · 클릭 가능한 승인 스레드 URL(좌표 미상 시 기존 id) · 노트 파일명
- **로컬 전사본 목록** — `~/.hermes/plaud-sync/transcripts/<노트 stem>.md`. speechtotext 전사본과 같은
  형식(헤더 `- 화자: …` 범례 + `---` + `[HH:MM:SS] 화자N · 이름` 블록)이라 회의록 체인이 그대로 읽는다.

## 언제

소유자가 "plaud 상태", "라이프로그 동기화 상태 알려줘", "plaud 승인 대기 몇 건이야",
"plaud 마지막 폴 언제였어", "plaud 전사본 어디 있어" 처럼 물으면 **질문하지 말고** 바로 실행해
결과를 그대로 전한다.

## 명령

```bash
python3 /srv/autophagy-skills/live/plaud/scripts/plaud_cli.py status          # 사람용
python3 /srv/autophagy-skills/live/plaud/scripts/plaud_cli.py status --json   # 기계용 (transcripts[].path 포함)
```

사람용 출력 첫 줄은 항상 `PLAUD-STATUS state=<present|absent>` 다.

`--json`의 `pending[]`는 기존 `thread_id` 등 모든 키를 유지하고 `thread_url`을 더한다.
저장된 `approval_guild_id`와 `approval_thread_id`가 모두 양의 ASCII 숫자 문자열일 때만
URL을 만든다. 길드가 없는 옛 레코드나 불완전한 좌표는 `thread_url: null`이며, DM 링크를
추측하지 않는다. 링크가 없으면 함께 출력된 노트 파일명(스레드 제목)과 녹음 id로 찾는다.
예: "plaud 승인 대기 몇 건이야"에 표시된 URL을 열어 승인 카드를 보고, URL이 없으면
노트 파일명으로 Discord를 검색한다. 상태 조회는 네트워크 호출이나 상태 파일 수정을 하지 않는다.

- `state=absent` — 워처가 아직 한 번도 성공적으로 돌지 않았다(상태 파일 없음). 그대로
  전하고, 필요하면 `hermes cron list --all | grep -A12 plaud-sync` 로 마지막 틱 결과를 본다.
- exit 2 + stderr `PLAUD-STATUS state=unreadable reason=…` — 상태 파일이 깨졌다. 추측해서
  요약하지 말고 그 사유를 소유자에게 전한다.
- `전사 대기(transcribing) N건` 의 사유가 `rc=4 …` 면 노드에 whisper/sherpa 도구가 없거나 경로 설정이
  빠진 것이다 — 녹음이 아니라 노드 문제이므로 시도 횟수는 늘지 않고 매 틱 다시 시도한다. 녹음 자체의
  실패(빈 전사·잘림·시간 초과)는 시도가 늘고 상한(기본 2)에 닿으면 Plaud 클라우드 전사로 노트를
  만들며 노트 출처 줄에 그 사실이 적힌다. 클라우드 요약·전사도 비면 로컬 재시도는
  1·2·4·8·16·24시간(상한) 백오프로 미루고, 클라우드만 매 틱 다시 확인한다. 총 실패 상한
  `PLAUD_SYNC_TRANSCRIBE_GIVE_UP`(기본 5)에 닿아도 클라우드가 비어 있으면 `abandoned`로 닫는다.
- 틱 저널의 `outcome=waiting next_transcribe_at=…`는 전사 슬롯을 쓰지 않는 백오프 대기다.
  `outcome=abandoned`는 폐기 사유를 동반한다. 재확인 오류는 아래 별도 필드로 구분한다.
- `PLAUD_SYNC_MIN_DURATION_MS`(기본 5000) 미만은 발견 시 건너뛰어 레코드·노트·카드를 만들지 않는다.
  이미 전사 대기 중이면 원본 길이를 확인하는 다음 처리에서 폐기한다.
- Plaud 요약이 없으면 기존 필드 추출과 같은 LLM 호출에서 얻은 한국어 요약을 노트에 쓴다.
  Plaud 요약이 있으면 그것이 우선하며 내용 분류 없이 추출한다. 건별 ✅는 기본 모드에서만 받는다.

## 폴더 이동 별칭과 클라우드 재확인 오류

폴더 정리로 녹음 id에 `of_`가 붙어도 접두어를 뺀 id가 같으면 기존 녹음이다.
발견 단계에서 기존 키·녹음 id·`aliases`를 비교하므로 새 레코드나 승인 카드를 만들지 않는다.
원본 id를 일괄 변경하지 않아 이미 승인된 해시와 노트 경로는 유지된다.
이미 생긴 중복은 운영자가 다음 명령으로 계획을 검토한 뒤 적용한다(일반 틱은 병합하지 않는다):

```bash
python3 ~/.hermes/scripts/plaud_sync_watch.py --migrate-aliases
python3 ~/.hermes/scripts/plaud_sync_watch.py --migrate-aliases --apply
```

우선순위는 `written > approved > posted > planned > transcribing > abandoned`이며,
같은 상태면 키의 사전순으로 선택한다. 남는 레코드의 승인 바인딩·본문 경로는 그대로 두고
다른 id를 선택적 `aliases` 목록에 보존한다. 적용은 워처 잠금 안에서
`state.json.bak-<UTC stamp>`에 원본을 먼저 백업한 뒤 상태만 원자적으로 교체한다.
카드·노트·외부 승인 원장을 삭제하지 않으며, 이 명령은 폴·전사·승인 처리를 실행하지 않는다.

백오프 중 클라우드 재확인의 비집계 오류는 `last_recheck_error`에 따로 남는다.
`last_block_reason`과 시도 횟수·재시도 시각을 보존하므로 원래 녹음 실패 사유를 잃지 않는다.
`plaud 상태`는 전사 대기의 원래 사유와 마지막 재확인 오류를 함께 보여 주며,
`status --json`의 `transcribing[]`에는 오류가 있을 때만 `last_recheck_error`가 추가된다.
이 값은 마지막 오류 이력이지 현재 클라우드 장애 여부 판정은 아니다. 옛 상태 파일도 그대로 읽는다.

## 원본 오디오 보관과 전사 품질

워처에서 `DRIVE_PUBLISH_ENABLED=1`을 켜면 내려받은 **원본 바이트를 재인코딩하지 않고**
공용 Drive 파사드의 `audio` 종류, `lifelog` 과제에 보관한다. owner-only와 재조회 검증을 통과한
오디오 SHA만 `STT_EVAL_ROOT/manifest.jsonl`에 기록한다(기본 `~/.hermes/stt-eval`). 같은 SHA는
원장 한 행으로 재사용하며, 업로드·검증·원장 쓰기가 실패하면 `AUDIO-ARCHIVE-FAIL`을 남기고
로컬 오디오를 지우지 않는다. **옵트아웃은 기존 삭제 정책**을 유지하므로 보관을 보장하지 않는다.
전사 자식의 `DRIVE_PUBLISH_ENABLED=0`은 중복 발행 방지이며 부모 워처의 원본 보관과 별개다.

단어 시각이 있는 전사는 **단어별 화자 배정 후 문장 조립**을 한다. 근거 없는 화자를 앞 문장에서
상속하지 않고 미상·겹침을 `화자0`으로 남긴다(이름 배정 대상 아님). 분리 자체가 실패하면 기존처럼
화자 없이 계속한다. `SPEECHTOTEXT_WHISPER_DTW`의 `--dtw`와 `SPEECHTOTEXT_ALIGN_BACKEND=whisperx`는
명시 옵트인이고, 변경된 인식 조건은 창 캐시 지문을 바꾸므로 옛 인식 결과를 재사용하지 않는다.
전사 가설 스냅샷과 소유자 편집 정답은 비공개 평가 루트에 모이며, 비교 보고가 기본 모델을 자동으로
교체하지는 않는다. 이 상태 스킬은 여전히 읽기 전용이고 오디오 업로드나 재전사를 직접 하지 않는다.

## 전사본을 회의록으로 보내기 (소유자 지시가 있을 때만)

lifelog 는 회의록 체인을 **자동 호출하지 않는다**. 소유자가 "이 plaud 녹음 회의록으로 만들어" 라고
지시하면 `status --json` 의 `transcripts[]` 에서 그 녹음의 `path` 를 찾아 기존 meeting CLI 에 넘긴다:

```bash
python3 /srv/autophagy-skills/live/meeting/scripts/meeting_cli.py ingest \
  --file ~/.hermes/plaud-sync/transcripts/<노트 stem>.md --label <회의 라벨> [--project <과제명>]
```

회의록 도메인(칸반·통지·Drive 발행·관리번호)은 meeting 스킬이 그대로 소유한다 — 여기서
재구현하지 않는다. `--file`은 전사 텍스트를 넘길 뿐 원음을 외부 전사 API로 보내지 않는다.
다만 위 보관 옵트인을 켰다면 원음의 owner-only Drive 사본이 있을 수 있다.

## 워처의 결과 통지

자동 모드에서 새로 쓴 노트는 개별 승인 스레드를 만들지 않는다.
비공개 공용 원장 `~/.hermes/auto-write/ledger.jsonl`에 저장 영수증만 남기고,
기존 owner notice 경로의 21:30 일일 요약에 녹음 시각·노트 경로를 모은다.
이하 스레드 통지는 기존 승인 카드를 가진 레코드와 기본 모드에 적용된다.

저장 완료(`written`)·취소(`abandoned`) 결과는 워처가 `origin_notice.deliver(message=)`의
소유자 메시지 봉투로 **승인 카드를 올린 같은 스레드**에 보낸다. 스레드 전송 실패 시에도
폴백은 `approval_thread_id`(없으면 저장된 `channel_id`)로 가므로 두 경로 모두 자기 링크를
붙이지 않는다. 위치는 승인 카드 좌표이며, 길드를 모르는 옛 레코드는 링크를 추측하지 않고
녹음 id 검색 키를 보존한다. 봉투 모듈이나 지원 시그니처가 없는 옛 런타임은 기존 문구로 보낸다.
종결·아카이브는 기존처럼 스레드 게시 성공 뒤 종결 결과에만 적용하며 진행 중 요청은 닫지 않는다.
노트 본문·전문은 통지에 싣지 않는다 — 승인 카드의 `summary_preview` 인용과 vault가 그 자리다.
승인 해시와 이미 게시된 카드는 그대로 두며, 이 통지 변경을 위해 재게시하지 않는다.

## 하지 않는 것

- Plaud 를 폴하지 않고, 오디오를 내려받거나 전사하지 않으며(그건 워처의 일), Discord 에 아무것도
  올리지 않고, vault 에 쓰지 않는다.
- 노트 본문을 읽지 않는다(파일명만 보고한다). 본문은 승인 카드의 미리보기와 vault 에 있다.
- 승인 결정을 대신하지 않는다 — ✅/⛔ 는 `#agent-chat` 의 노트 파일명을 제목으로 한
  요청별 스레드에서 소유자만 누른다.

## 운영자 참고 — 승인 카드를 새 형식으로 다시 올릴 때

카드 렌더 형식이 바뀐 뒤 이미 게시된 카드를 새로 만들려면(소유자 요청이 있을 때만),
agent 계정에서 워처를 재게시 모드로 한 번 돌린다. 옛 카드를 지우고 **같은 스레드**에
새 카드를 올리며, 이미 ✅ 를 받은 건은 먼저 정상 틱으로 소비되므로 잃지 않는다:

```bash
python3 ~/.hermes/scripts/plaud_sync_watch.py --repost-posted
```

로컬 전사를 잠시 끄려면(옛 동작 = Plaud 클라우드 전사로 바로 `planned`) 노드 `~/.env.secrets` 에
`PLAUD_SYNC_TRANSCRIBE=0` 을 둔다. 이미 `transcribing` 인 레코드는 그대로 남으므로 되돌릴 때 다시 잡힌다.

## 관련

- 워처·저장 경로·승인 흐름: `docs/기능소개/plaud-lifelog-동기화.md`
- 로컬 전사 스테이지(오디오 다운로드·전사·잠금·폴백): `docs/기능소개/plaud-녹음-로컬-전사.md`
- 백오프·최소 길이·요약 정책: `docs/기능소개/plaud-전사-정책-게이트.md`
- 사용 중 정확도 개선·오디오 보관: [소개](../../docs/기능소개/전사-정확도-사용중-개선-루프.md)
- 코드: `automation/plaud_sync/` (상태 스키마 `model.py`, 카드 `render.py`, 재게시 `repost.py`,
  전사 스텝 `transcribe.py`·`transcribe_live.py`, 오디오 `audio.py`)
