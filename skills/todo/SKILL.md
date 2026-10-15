---
name: todo
description: 할 일을 등록하거나 할 일 목록을 보여 달라고 할 때 사용. Google Tasks 할 일 등록·조회 스킬. 등록(mutate)은 외부효과 승인 게이트를 반드시 경유하고, 쓰기 성공 뒤 tasks.tasks.get 재조회로 저장된 제목·식별자를 검증한다. 조회(list)는 READ이므로 게이트 대상이 아니다. 터미널에서 raw `gws tasks tasks insert`를 직접 실행하지 말 것 — 같은 명령이 denylist 규칙 gws_tasks_mutation에 매칭되어 승인 없이는 차단된다.
version: 1.4.6
author: autophagy-agents
license: proprietary
metadata:
  hermes:
    tags: [tasks, todo, external-effect, approval-gate, gws]
---

# todo — 승인 게이트 경유 Google Tasks

## 이 스킬이 존재하는 이유

Google Tasks 쓰기에는 **repo 측 코드가 아예 없었다.** 에이전트가 터미널 도구로
`gws tasks tasks insert`를 치면 denylist 어느 규칙에도 매칭되지 않아 프로덕션 게이트가
이를 *읽기*로 분류하고 그대로 통과시켰다. 그 경로로 잘못 옮겨 적은 개인 고유명사가
소유자 ✅ 없이 외부 시스템에 기록됐다.

이 스킬은 그 구멍을 두 겹으로 막는다.

1. **승인 레코드 없으면 쓰지 않는다.** 실행할 argv를 먼저 동결하고 기존
   `automation/interop/external_effect_gate.py`에 그대로 넘긴다. ✅ 하나는 argv 하나만
   승인한다 — 제목이 한 글자라도 다르면 `action_hash`가 달라져 거부된다. `request`는
   공용 승인 lifecycle을 통해 소유자에게 확인 카드를 보내고, 체크아웃 밖 세대형
   pending/archive store에 메시지와 승인 표면 바인딩을 함께 남긴다.
2. **재조회로 증명하지 않으면 성공이라 말하지 않는다.** `insert` 뒤 반드시
   `gws tasks tasks get`으로 다시 읽어 저장된 제목·id가 보낸 값과 같은지 대조한다.
   불일치·빈 응답·재조회 실패는 전부 명시적 실패(비-0 종료)다.
3. **승인 하나는 한 번만, 그러나 반드시 실행된다.** 워처가 ✅를 원장과 불변 archive에
   기록하고 **같은 틱에서 그 generation을 경쟁 안전 claim한 뒤 등록까지 수행한다**. 성공
   receipt가 있거나 `write_started`가 남은 generation은 자동 재실행하지 않는다 — 상태는
   저장하지 않고 (archive된 approved 세대 × claim receipt)로 매 틱 유도하므로, 중간에
   죽어도 다음 틱이 이어받고 두 번 쓰지 않는다.
4. **요청 하나가 스레드 하나다 (2026-09-01, 소유자 지시 — 전 스킬 공통).** `request`는 이
   할 일 전용 승인 스레드 `할 일 · <제목>`(제목만, notes 제외)을 열어 확인 카드를 거기에
   게시하고, 그 스레드 id를 레코드의 `approval_thread_id`로 남긴다. cha가 채널에서 등록을
   지시하면 `request`/`create`에 `--origin-channel-id <채널ID>`(지시 메시지 id를 알면
   `--origin-message-id`도)를 전달한다 — 그 지시 메시지가 승인 채널에 있으면 요청 스레드가
   거기에 앵커된다. 등록 완료(`✅ 할일 등록 완료: <제목> (task <id>)`)와 워처의 ⛔ 취소 통지는
   **같은 스레드**에 게시되고, 그 스레드는 상태 접두어로 이름이 바뀐 뒤(`✅ 완료 · …` /
   `⛔ 취소 · …`) 아카이브된다 — 열려 있는 스레드가 곧 진행 중인 요청이다. 만료된 요청은
   승인 스레드를 `⌛ 만료 · …`로 닫는다. 스레드가 없는 옛
   레코드는 origin 스레드, 그마저 없으면 저장된 승인 채널로 폴백한다. 통지는
   best-effort(실패 시 `NOTIFY-THREAD-FAIL`→저장 채널 폴백, `NOTIFY-FAIL`, 종결 표시 실패는
   `THREAD-CLOSE-FAIL`)라 receipt·원장·exit code를 바꾸지 않으며, E2E 승인은 `NOTIFY-SKIP`으로
   실제 통지를 열지 않는다. 결과는 `OwnerMessage`의 대상·사실·위치·인계·되돌리기
   다섯 필드로 보낸다. 사실 문구와 제목·id의 기존 노출 범위를 유지하고, 승인만 받은 상태를
   실행 완료로 표시하지 않는다. 다른 채널 폴백에는 저장된 승인 카드 좌표의 링크를 싣고,
   길드가 없는 옛 레코드는 `Discord 검색`과 승인 id를 표시한다. 같은 스레드 폴백은 자기
   링크를 생략한다. 현재 실행 경로는 task URL 없이 id만 보유하므로 되돌리기는 해당 없음이다.
   봉투 모듈이나 파사드의 봉투 지원이 없는 옛 런타임은 기존 문자열 바이트로 내려간다.

## 사용법

```bash
# ① 조회 (READ — 게이트 대상 아님)
python3 /srv/autophagy-skills/live/todo/scripts/todo_cli.py list --tasklist @default

# ② 승인 대상 확인 (READ — 실행하지 않고 hash/target만 계산)
python3 /srv/autophagy-skills/live/todo/scripts/todo_cli.py plan --title "실험 노트 정리"

# ③ 승인 요청 (owner 확인 카드의 유일한 공개 producer)
python3 /srv/autophagy-skills/live/todo/scripts/todo_cli.py request --title "실험 노트 정리" \
  --tasklist @default --notes "합성 메모" --due 2026-08-01T00:00:00Z

# ④ 이 요청의 스레드 `할 일 · <제목>` 에서 ✅ 또는 ⛔ 선택
#    ✅ 를 누르면 그것으로 끝난다 — 다음 워처 틱(매 1분)이 동결된 인자로 등록까지 수행하고
#    `tasks.tasks.get` 재조회로 검증한 뒤 결과를 같은 스레드에 통지하고 그 스레드를 닫는다.
#    사용자에게 "✅ 를 누르면 자동 등록된다"고 안내해도 된다.

# ⑤ (진단용) 수동 등록 — 정상 흐름에서는 필요 없다
#    워처가 이미 실행했으므로 같은 generation 으로 부르면 exit 4(이미 소비됨)로 거부된다.
#    워처가 멈춘 상황을 진단할 때만 쓴다.
python3 /srv/autophagy-skills/live/todo/scripts/todo_cli.py create --title "실험 노트 정리" \
  --tasklist @default --notes "합성 메모" --due 2026-08-01T00:00:00Z
```

변경 명령은 `/srv/autophagy-skills/live/todo/scripts/` 밖의 사본에서 실행하면 `STALE-SKILL-COPY-BLOCK`으로 거부한다.

출력 계약:

- `PLAN … external_effect=True approved=<bool> hash=sha256:… target=tool:gws_tasks_mutation:gws`
- `REQUESTED hash=sha256:… target=tool:gws_tasks_mutation:gws`
  + `APPROVAL-THREAD hash=sha256:… url=<승인 스레드 링크>` — **소유자에게 "승인 스레드에서 ✅" 라고
  답할 때 이 `url=` 값을 그대로 붙인다**(2026-09-23 소유자 지시: 링크가 없으면 스레드를 직접 찾아야
  한다). `url=unavailable search=<값>` 이면 링크를 지어내지 말고 그 값으로 검색하라고 적는다.
- `CREATED id=… tasklist=… hash=…` + `VERIFIED reread=tasks.tasks.get id=… title_match=true`
- 실패는 stderr에 `TODO-FAIL …`

종료 코드: `0` 성공 · `3` 게이트 모듈/설정/gws 불가 · `4` 소유자 승인 없음 ·
`5` gws 실행 실패 · `6` 재조회 검증 실패 · `7` write_started 조정 필요.

## 라우팅 규칙 (에이전트용)

- 소유자가 "할 일 추가/투두 등록"을 요청하면 이 스킬의 `create`를 쓴다.
  **`gws tasks tasks insert`를 터미널에서 직접 실행하지 않는다** — 게이트가 차단한다.
- 일정(특정 시각이 있는 약속)은 todo가 아니라 `calendar` 스킬이다. 마감일만 있는
  작업 항목이 todo다. 애매하면 소유자에게 되묻는다(fail-closed).
- 사람·기관 등 개인 고유명사가 제목에 들어가면 쓰기 전에 해석 preflight를 거친다
  (`docs/guide/personal-entity-preflight.md`). `create_task`가 유일한 쓰기 진입점이므로
  가드는 이 함수 하나만 감싸면 된다.

## 배포 경로 및 승인 실행 계약

- `plan`과 실행 경로는 공용 runtime-root 정책(`AUTOPHAGY_RUNTIME_ROOT` → 불변 릴리스
  `/srv/autophagy-agent-current` → 상주 미러 `/srv/autophagy-agents`)으로 현재 SSOT를
  해석한다. denylist 또는 runtime root를 읽을 수 없으면 fail-closed하며, 경로를
  추측하거나 게이트를 우회하지 않는다. 진단에는 `todo_cli.py runtime-root`를 쓴다.
- `request`가 동결된 tasklist·제목·notes·due를 승인 레코드에 함께 적고(그 4개가 없으면
  승인만으로는 무엇을 쓸지 복원할 수 없다 — `argv_summary`는 마스킹되어 있다) action hash와
  확인 카드를 `approval_surface`가 정한 표면(정책 v7 = 요청별 스레드 `할 일 · <제목>`)에 만들고,
  owner 본인의 ✅/⛔만 `todo_confirm_reaction_watch.py`가 판정한다. 워처는 결정과 메시지
  바인딩을 `manual_reaction` 원장 및 불변 archive generation에 함께 기록하며 ⛔를 항상
  우선한다. raw `gws tasks tasks insert` 또는 수동 승인 레코드 작성은 금지한다.
- **✅ 이후 등록은 워처가 수행한다.** 매 틱 `execute_approved_writes()`가 archive된 approved
  세대를 훑어 claim receipt가 없는 것만 골라 `todo_cli.create_task`(단일 쓰기 경로)로 실행한다.
  실행 전 동결된 4개 인자로 argv를 재구성해 action hash를 다시 계산하고 승인된 해시와 다르면
  쓰지 않는다. `write_started`가 남은 세대는 재실행하지 않고 조정 대상으로 보고한다.
  실행 파라미터가 없는 옛 레코드는 복원할 수 없으므로 건너뛴다. 이것은 바뀔 수 없는 상태이지
  사건이 아니므로 매 틱 저널에 남기지 않는다(`already-verified` 와 같은 취급) — 결과는
  호출자에게 `legacy-unreplayable` 로 그대로 반환된다.
- `create`는 유효한 미소비 approved generation을 경쟁 안전하게 단 한 번 claim한 뒤
  동결된 argv로만 insert한다. 성공 후에는 `tasks.tasks.get` 재조회로 저장된 id와 제목을
  검증하고 receipt를 남긴다. 승인 없음·재사용·불확실한 `write_started` 상태에서는
  외부 호출 없이 거부하거나 명시적 조정을 요구한다.

## 환경 변수

| 변수 | 기본값 | 용도 |
|------|--------|------|
| `TODO_APPROVAL_LOG` | `/srv/autophagy-agents/logs/approvals.jsonl` | 소유자 승인 레코드 원장 |
| `TODO_APPROVAL_ROOT` | `~/.hermes/todo-approvals` | pending/archive 세대·claim/receipt·lifecycle lease/journal |
| `TODO_APPROVAL_TTL` | `86400` | pending 요청 유효 시간(초); 원장 레코드에는 적용하지 않음 |
| `TODO_DENYLIST` | `<repo>/configs/external-effect-tools.yaml` | 외부효과 denylist |
| `TODO_OWNER_ID` | interop config의 `owner_id` | 승인자 판정 |
| `TODO_GWS_BIN` | `which gws` → `~/.local/bin/gws` | gws CLI 경로 |
| `DISCORD_BOT_TOKEN` | 없음 | 확인 카드 게시용 봇 자격증명. 대화형 CLI 환경에 상속되지 않아 `todo approval identity is unavailable`가 나오면 토큰 값을 출력하지 않고 `~/.env.secrets`를 환경에 로드한 뒤 같은 `request`를 재시도한다. |
| `AUTOPHAGY_RUNTIME_ROOT` | 공용 runtime-root 정책 | 게이트 모듈 import 루트(진단: `todo_cli.py runtime-root`) |

## 관련

- 게이트: `automation/interop/external_effect_gate.py` (`load_denylist` / `evaluate_tool_call`)
- denylist 규칙: `configs/external-effect-tools.yaml` → `gws_tasks_mutation`
  (`generic_*` catch-all보다 **위**에 있어야 한다 — 로더는 first-match)
- 테스트: `tests/unit/test_todo_skill.py` · 샌드박스 시나리오: `scripts/scenario.sh` (완전 오프라인)
- 승인 워처: `scripts/todo_confirm_reaction_watch.py` · 설치 정의: `skills/todo/deploy.sh`
- 기능 소개: `docs/기능소개/google-tasks-승인-쓰기.md`
- preflight 계약: `docs/guide/personal-entity-preflight.md`
