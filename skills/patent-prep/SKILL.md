---
name: patent-prep
description: "발명 신고서나 선행기술 조사를 준비하라고 할 때 사용. 발명 신고서와 선행기술 체크리스트를 보호된 개인 워크스페이스에서 준비하고, 계정 모델 설정으로 초안을 생성한다. W5-5."
version: 1.2.1
author: autophagy-agents
license: MIT
metadata:
  hermes:
    tags: [Private-Workspace, Tech-Transfer]
prerequisites:
  commands: [python3, hermes]
---

# patent-prep — 발명 신고·선행기술 준비

변경 명령은 `/srv/autophagy-skills/live/patent-prep/scripts/`에서만 실행하며, 낡은 사본은 STALE-SKILL-COPY-BLOCK으로 거부한다.

모든 원문·입력·체크리스트·초안은 `~/patent-drafts/<slug>/`(0700)와 하위 0600 파일에만
보관한다. `PATENT_STATUS_ROOT`는 `slug`, `checklist_state`, `percent_complete`만 가진
진행 메타데이터 위치이며 기본값은 `~/.hermes/patent-status/`(0700)이다. 본문, 제목,
발명자, 기술 내용은 status·repo·`docs/qa`에 쓰지 않는다.

## 절대 규칙

1. `draft`의 모델은 계정 `~/.hermes/config.yaml`의 주 모델·폴백을 따른다.
   호출이 실패하면 초안을 만들지 않는다. 실제 응답 제공자·모델은 호출 로그에 기록한다.
2. `--brief-file`은 해당 slug의 private workspace 아래 파일만 허용한다. 경로·상태·SHA256만
   공유하며, 초안이나 입력 본문을 Discord·repo·`docs/qa`·외부 RAG API로 보내지 않는다.
   **좁은 예외 — 개인 백업 반출(`export-prepare`/`export-execute`)에 한한다: 소유자(cha)의
   ✅ 승인이 대상 `draft.md`의 평문 SHA-256·목적지 폴더·mode·만료에 함께 바인딩되고,
   기본적으로 cha의 SSH ed25519
   공개키로 `age` 암호화되며(평문은 `--allow-plaintext` + 별도 ✅ 승인 시에만), 대상이 cha
   본인 Drive의 고정 개인 폴더(런타임 config의 allowlist-of-one)이고, 업로드 직전 pre-flight
   ACL 검사에서 owner:cha 외 권한이 없을 때에만 `draft.md`를 그 폴더로 반출한다. 그 외에는
   반출하지 않는다(무승인·만료·⛔·해시 불일치·nonce 재사용·비-owner 폴더 권한 = 업로드 안 함,
   fail-closed). 제3자·임의 목적지·임의 폴더 반출은 지원하지 않으며, 승인 요청·owner DM·감사
   로그·stdout에는 본문·제목·발명자를 남기지 않는다(slug·경로·SHA-256·폴더ID·webViewLink만).**
3. W2-4 RAG 적재는 on-prem MCP/embedding 경로만 허용된다. 이 스킬은 RAG 설정이나 외부 API를
   변경하거나 호출하지 않는다.

## Commands

```bash
# 0700 workspace, disclosure form, prior-art checklist, content-free progress metadata
python3 /srv/autophagy-skills/live/patent-prep/scripts/patent_cli.py create --slug <kebab-slug>

# checklist progress only
python3 /srv/autophagy-skills/live/patent-prep/scripts/patent_cli.py checklist \
  --slug <kebab-slug> --state in-progress

# brief file must already be inside ~/patent-drafts/<slug>/
python3 /srv/autophagy-skills/live/patent-prep/scripts/patent_cli.py draft \
  --slug <kebab-slug> --brief-file ~/patent-drafts/<kebab-slug>/brief.md

# safe progress metadata only
python3 /srv/autophagy-skills/live/patent-prep/scripts/patent_cli.py status --slug <kebab-slug>

# 소유자 ✅ 승인형 암호화 백업 요청 — draft.md를 cha 본인 Drive 고정 폴더로 (소유자 DM 게시, 업로드 없음)
python3 /srv/autophagy-skills/live/patent-prep/scripts/patent_cli.py export-prepare --slug <kebab-slug>

# (✅ 후 소유자가 직접 실행) age 암호화 + pre-flight ACL + 업로드; ⛔·무반응·만료 = 업로드 안 함
python3 /srv/autophagy-skills/live/patent-prep/scripts/patent_cli.py export-execute --slug <kebab-slug>

# 유돌 형 명령(2026-10-18 소유자 "5번도 카드 없이 해"): 유돌 게이트 `patent export` 가 부른다.
# 같은 스냅샷·ACL 점검·age 암호화·감사(method=owner_command, 근거 메시지 좌표)로 카드 없이 올린다.
python3 /srv/autophagy-skills/live/patent-prep/scripts/patent_cli.py export-now --slug <kebab-slug> \
  --origin-channel-id <채널> --origin-message-id <형 메시지>
# 올린 자료로 초안: 파일을 워크스페이스 brief.md 로 복사한 뒤 draft
python3 /srv/autophagy-skills/live/patent-prep/scripts/patent_cli.py draft --slug <kebab-slug> --brief-import <파일>
```

`scripts/scenario.sh` uses only a temporary workspace and a dummy secret. It never invokes an
LLM or contains invention material; it verifies 0700/0600 isolation, metadata-only progress,
and the account-configured dispatch plan.

## 승인형 반출 게이트 (개인 백업) + 위협 모델

목적: `~/patent-drafts/<slug>/draft.md`를 **cha 본인** Google Drive의 **고정 개인 폴더** 하나로
백업한다. 공유·제출이 아니라 개인 백업이며, 권위 있는 원본은 항상 로컬에 남는다. 기본
암호화(`age` + cha SSH ed25519 공개키)이므로 키 분실 시에도 백업본만 영향을 받고 원본은
무사하다. 복호는 cha 전용(`age -d -i ~/.ssh/id_ed25519`).

두 개의 **수동** 명령(워처가 업로드하지 않는다):

1. `export-prepare --slug S` (cha 실행) — `draft.md` 평문 SHA-256 계산 → 공유 승인 생명주기의
   `patent:S` 키로 **본문 없는** 요청(slug·sha256·폴더ID·만료·mode)과 ✅/⛔를 게시한다.
   요청 1건은 cha의 agent-chat 채널 아래 **자기 전용 스레드**에서 열리며, 스레드 이름은
   `특허 반출 · <slug>`로 **반출 id 하나뿐**이다 — 발명의 명칭·문서 파일명·본문 발췌는
   이름에 절대 들어가지 않는다. 게시 표면은 `automation/interop/approval_surface.py`
   정책이 결정하고, 매니페스트가 그 바인딩(`approval_thread_id`/`kind`/`surface`/
   `channel_id`/`policy_version`)을 기록한다. 정책 v4 이전에 저장된 요청은 원래 메시지가
   있는 채널에서 그대로 소비된다(재조준 없음).
   신규 카드의 봉투는 5필드이며 제목·본문·URL을 싣지 않는다. 기존 sha256·dest_folder_id·
   expiry_ts·mode 네 줄은 같은 위치·순서의 독립된 wire로 유지한다. `render_version`은
   해시와 독립적으로 저장하며 누락/1은 동결된 기존 문자열, 2는 봉투다. 봉투 import·렌더
   불가 시 신규 카드만 판본 1로 내려가 저장한다. 렌더 불가는 스레드 생성 전에 거부한다.
   만료는 이미 결정된 `expiry_ts`이며 렌더가 시계를 읽지 않는다. 형식 이관을 위해 prepare를
   재실행하지 않는다(prepare의 기존 1시간 만료 계산은 새 의미 해시를 만들 수 있다).
   commit에서만 0600 매니페스트
   `{slug, plaintext_sha256, dest_folder_id, mode(enc|plaintext), expiry_ts, nonce, state=PENDING,
   message_id}`를 쓴다. 같은 승인 내용의 PENDING은 기존 message id와 nonce를 재사용하고,
   변경된 PENDING은 옛 메시지를 먼저 삭제한 뒤 CANCELLED로 supersede하고 새 요청 1건만 게시한다.
   APPROVED는 이미 허가된 미실행 반출이므로 삭제·전이하지 않고 watcher/execute에 양보한다.
   매니페스트가 손상됐거나 façade import가 불가능하면 새 요청 없이 거부한다. **업로드 없음.**
   출력: `PATENT-EXPORT-PREPARED slug=… sha256=… expiry=…`.
2. `export-execute --slug S` (cha가 **수동** 실행 — 워처 아님) — 매니페스트 로드 →
   `state==APPROVED` && 미만료 && nonce 미사용(락으로 동시 실행 1건). 로컬 `draft.md` 재해시 ==
   `plaintext_sha256` 검증(TOCTOU fail-closed). **pre-flight ACL 검사**:
   `gws drive permissions list <dest_folder_id>` → owner:cha 외 권한이 하나라도 있으면 **업로드
   없이 중단**(업로드 후 검사·삭제는 조기 공개=신규성 상실이므로 금지). `mode==enc`이면
   `age -R ~/.ssh/id_ed25519.pub`로 임시 `draft.md.age` 생성 후 그 파일만 업로드. `dest_folder_id`로
   업로드 → 매니페스트 원자적 **CONSUMED**(nonce 소멸) → 감사 레코드 append(평문/암호문
   SHA-256·file_id·method=manual_reaction·result=approved, 본문 없음) → cha에게 위치 없는 완료 DM.
   완료 봉투에는 링크·제목·매니페스트 내용을 싣지 않는다. 구형 런타임에서는 기존 완료 문자열을
   그대로 보내며, 결과를 원 채널 스레드로 내보내지 않는 면제와 메시지 영수증 계약은 유지한다.
   출력: `PATENT-EXPORTED slug=… file=…`. 임시 암호문 정리.

상태 기계: `PENDING —(owner ✅)→ APPROVED —(execute 성공)→ CONSUMED`;
`PENDING/APPROVED —(owner ⛔)→ CANCELLED`(철회, 미실행 승인 무효화); 만료·nonce 사용·해시
불일치·비-owner 폴더 권한 = 모두 fail-closed 거부.

워처(reactions-only cron)는 **덤 상태 플리퍼**다: 기존 slug별 `manifest.lock`을 먼저 잡고,
매니페스트를 다시 읽은 뒤 같은 락 안에서 `message_id` 리액션을 조회해 ✅→APPROVED /
⛔→CANCELLED로만 바꾼다. 반응 조회 전후 `(slug, nonce, plaintext_sha256)`가 달라지면 전이를
중단한다. **draft를 읽지 않고, 암호화·업로드하지 않는다.** 되돌릴 수 없는 업로드는 오직
포그라운드 수동 `export-execute`에서만 일어난다.

위협 모델: 조기 공개(pre-flight ACL로 사전 차단, 업로드-후-검사 금지) · TOCTOU(execute 평문
SHA-256 재검증) · 리플레이/중복(nonce 단일사용 + CONSUMED + producer/watcher가 공유하는 기존
slug 락 + 승인 키당 단일 live 메시지) · 승인 위조/타인
리액션(owner-only `reactor==owner_id && !bot` + ⛔ 우선 + sha256 메시지 바인딩) · 철회(✅ 후
⛔=CANCELLED, expiry 자동 무효) · 키 위험(신규 키 없음, cha만 복호) · 본문 유출(승인·DM·감사·
stdout에 식별자·해시·링크만) · LLM 우회(raw `gws drive … patent-drafts`는 external-effect
denylist 백스톱 차단).

전제(런타임, 이 커밋 범위 밖): `apt install age`, `~/.hermes/patent-export/config.json`의
`archive_folder_id` 설정, denylist 런타임 사본 동기화.
