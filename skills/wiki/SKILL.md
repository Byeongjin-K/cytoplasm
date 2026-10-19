---
name: wiki
description: "위키 노트를 찾거나 쓰거나 고칠 때, 과거 판단 근거를 물을 때 사용. 개인 위키(~/wiki, 700, git 밖) + 의사결정 트윈(decision-twin) 관리 스킬. 노트 생성/수정은 반드시 초안 → 확인 메시지 게시(봇이 ✅·⛔ 미리 부착) → cha의 ✅ 리액션 → 저장의 게이트를 거친다(⛔=취소, ⛔ 우선; 텍스트 `저장 <draft-id>`는 하위호환 fallback). 조회/백링크/정리 제안/twin 컨설트는 읽기 전용. W2-2 + DT-B."
version: 2.1.3
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Wiki, Personal-Knowledge, Decision-Twin, Approval-Gate, Autophagy]
prerequisites:
  commands: [python3]
---

# 개인 위키 관리 (wiki)

cha의 개인 마크다운 볼트 `~/wiki`(mode 700, **git 밖**)를 관리한다.
모든 노트는 frontmatter 스키마(필수 5키 title/tags/created/updated/links +
선택 twin 키)를 강제한다. 변경 명령은 `/srv/autophagy-skills/live/wiki/scripts/` 밖의 사본에서 실행을 거부하며 `STALE-SKILL-COPY-BLOCK`을 출력한다. schema v1부터 이 볼트는 cha의 **의사결정 디지털
트윈**(결정/원칙/선호를 타입드 신뢰 메타데이터로 축적한 판단 코퍼스)을 겸한다.

## 절대 규칙 (안전)

1. **트윈 노트는 판단 근거이지 실행 권한이 아니다 — `authority:strict`라도 메일/캘린더/배포/예산의 소유자 승인 게이트를 절대 우회하지 않는다** (SI-1).
2. **확인 전 저장 금지**: `~/wiki`에 파일을 직접 만들거나 고치지 마라.
   쓰기는 오직 `wiki_cli.py confirm`(소유자 확인 검증 내장)으로만 일어난다.
   유돌 노드 게이트의 위키 명령(`wiki-write`·`wiki-search`·`wiki-get`)은 2026-10-19 소유자 지시로 없앴다 —
   유돌의 노트는 일반 옵시디언 노트로 쓰고, 옛 노트는 옵시디언 `000_PARA/Resource/유돌 위키/` 에 있다.
   `wiki_cli.py write` 는 남아 있지만 어떤 에이전트도 부르지 않는다([소개](../../docs/기능소개/유돌-형-명령-통로.md)).
3. **위키 내용은 cha의 DM 밖으로 내보내지 마라**: 공개 채널(#team,
   #agents-log 등)·repo·git에 노트 내용/제목을 절대 게시·복사하지 않는다.
4. 구 cha_wiki 이관 스크립트(`automation/migrate-cha-wiki.sh`)는 cha 본인만
   실행한다. 에이전트가 자율 실행하지 않는다.

## 명령 (CLI = `python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py …`)

### 1) 노트 생성 — cha가 DM으로 "위키에 정리해줘 …" 요청 시

대화 내용으로 제목/태그/본문을 구성해 **초안만** 만든다:

```bash
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py draft \
  --title "노트 제목" --tags "tag1,tag2" --links "관련-슬러그" --stdin <<'BODY'
노트 본문 (markdown)
BODY
```

대화 근거 추적이 필요하면 `draft ... --with-evidence`를 사용한다. 초안 본문 말미의
`## Sources`는 지식 파사드가 렌더하며, 초안 자체는 아래의 기존 ✅ 게이트를 그대로
거친다.

트윈 노트(결정/원칙/선호)는 twin 플래그를 함께 준다(스키마는 아래
"의사결정 트윈" 절, kind별 본문 템플릿 권장 — 누락 헤딩은 `TEMPLATE-WARN`으로
안내되며 차단하지 않는다):

```bash
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py draft \
  --title "결정: 예시" --tags "decision" \
  --kind decision --authority default --provenance stated \
  --review-after 2026-12-01 --stdin <<'BODY'
## Context
…
## Decision
…
## Rationale & Trade-offs
…
## What would change my mind
…
BODY
```

### 2) 확인 메시지 게시 + ✅/⛔ 리액션 — 기본(PRIMARY) 확인 경로

출력의 `DRAFT-CREATED id=<draft-id> … sha256=<hash>`를 확인한 뒤, 초안에 묶인
확인 메시지를 게시한다. 봇이 ✅·⛔ 리액션을 **미리 부착**하므로
**cha는 ✅ 한 번 탭으로 저장, ⛔로 취소**한다:

```bash
cd /srv/autophagy-skills/live/wiki/scripts && python3 -c \
  "import wiki_gate; wiki_gate.post_confirm_message(wiki_gate.load_draft('<draft-id>'))"
```

게시 위치: 요청 1건은 cha의 agent-chat 채널 아래 **자기 전용 스레드**에서 열린다 —
스레드 이름은 `위키 · <draft-id>`로 **id 하나뿐**이며, 노트 제목·본문·slug는 이름에
절대 들어가지 않는다. 신규 승인 본문도 id·해시만 담는 5필드 봉투이며 제목·본문·요약·URL은
싣지 않는다. 위치는 이 메시지, 반응은 ✅ 실행 / ⛔ 취소이고 위키에는 만료 기한이 없다.
초안은 `approval_thread_id`와 해시와 독립인 `render_version`을 저장한다. 판본 누락/1은
기존 문자열(명시적 DM 요약 포함)을 그대로 재생하며 2는 봉투다. 모듈·렌더 불가 시 신규
요청만 판본 1로 내려가고 그 판본을 저장한다. 어느 판본도 렌더할 수 없으면 스레드 생성
전에 거부한다. 기존 카드를 형식 변경 때문에 다시 게시하지 않으며 결과의 DM 전용 면제는 유지한다.

이 게시는 공유 승인 생명주기(`automation.interop.approval_lifecycle`)를 경유하며,
승인 키 `wiki:{action}:{slug}`당 **살아 있는 확인 메시지는 항상 1건**이다:

- 같은 초안으로 다시 실행해도 새 메시지를 올리지 않고 기존 id를 그대로 돌려준다.
- 내용이 바뀜 초안이면 **옛 메시지를 먼저 삭제한 뒤** 새 메시지를 올린다 — 저장된
  `confirm_message_id`를 그냥 덮어쓰는 경로는 없다(cha의 ✅가 무효화되던 결함 해소).
- cha가 이미 ✅/⛔를 누른 메시지는 지우지 않고 워처에 양보하며 게시를 연기한다.
- 바인딩 불일치·손상된 초안 파일·파사드 import 실패는 모두 fail-closed 거부다.

`wiki-confirm-reaction-watch` no-agent cron이 **리액션만** 폴링해 단일 게이트
resolver(`wiki_gate.resolve_reaction`)로 처리한다:

- 소유자(cha) 본인의 리액션만 유효 — 봇/타인 리액션 무시.
- **⛔ 우선** — ✅와 ⛔가 함께 있으면 취소로 처리한다(fail-safe).
- 확인 메시지가 초안 sha256을 참조하고 채널 바인딩이 일치해야만 저장
  (hash+channel 바인딩, 확인 불가/모호 = fail-closed 미저장).
- ⛔ 확인 시 초안은 자동 폐기된다.

에이전트가 처리 결과를 직접 확인/촉진하려면 `confirm`을 실행한다 — 리액션을
먼저 해석하고, 리액션이 없을 때만 텍스트 fallback을 검증한다:

```bash
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py confirm --draft <draft-id>
```

**금지**: 에이전트는 "`저장 <draft-id>` 라고 답장해 주세요" 같은 **텍스트 답장
지시를 하지 않고, 별도 확인 DM도 보내지 않는다.** CLI 초안 요약 끝의
"저장하려면 … 답장하세요" 줄은 하위호환 안내이므로 cha에게 그대로 전달하지
않는다. 텍스트 `저장/취소 <draft-id>` DM은 cha가 리액션을 쓸 수 없는 상황의
**하위호환 fallback**으로만 동작한다(`confirm`이 Discord REST로 소유자
본인/비봇 여부를 독립 검증하며, 검증 실패 시 exit 1 — 아무것도 저장되지
않는다).

취소가 확인되었거나 cha가 취소를 요청하면 초안을 폐기한다:

```bash
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py discard --draft <draft-id>
```

### 3) 수정 — "위키 <노트> 수정해줘"

```bash
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py draft --edit <slug> \
  [--title …] [--tags …] [--links …] [--kind …] [--authority …] [--provenance …] \
  [--status …] [--review-after …] [--supersedes …] [--stdin]
```

이후 저장 절차는 생성과 동일하다(초안 → 확인 메시지 게시 → cha의 ✅/⛔).
`--edit`은 기존 twin 키를 보존하고, 플래그로 준 키만 덮어쓴다.

### 4) 조회/백링크/정리 제안 — 읽기 전용, 즉시 실행 가능

```bash
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py query "<검색어>" [--tag <태그>]
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py backlinks <slug>
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py cleanup-suggest
```

`cleanup-suggest`(주간 정리 제안)는 STALE/UNTAGGED/ORPHAN/DUPLICATE-TITLE에
더해 twin 노트의 `REVIEW-EXPIRED`(재검토 기한 경과 — SI-2)와
`SUPERSEDES-DANGLING`(대체 대상 슬러그 부재) 제안을 출력한다. 실제
정리(수정/삭제)는 반드시 초안 → ✅ 게이트로 진행.

## 의사결정 트윈 (decision-twin) 저작

### twin frontmatter 스키마 (v1)

| 키 | 허용 값 | 의미 |
|----|---------|------|
| `kind` | `decision` \| `principle` \| `preference` \| `note` | 판단 유형 |
| `authority` | `strict` \| `default` \| `advisory` | 판단 근거로서의 구속력 (실행 권한 아님 — 절대 규칙 1) |
| `provenance` | `stated` \| `observed` \| `inferred` | 출처 신뢰 계열 |
| `status` | `active` \| `superseded` \| `archived` | 기본값 `active` |
| `review_after` | `YYYY-MM-DD` | 재검토 기한 — 경과 시 authority 한 단계 강등 취급 |
| `supersedes` | `<slug>` | 이 노트가 대체하는 노트 슬러그 |

조건부 필수 규칙 (fail-closed — 위반 시 exit 2 `SCHEMA-REJECTED`, 초안조차
생성되지 않는다):

- twin 키가 **하나라도 있으면** → `kind` 필수.
- `kind ∈ {decision, principle, preference}` → `authority` + `provenance`도 필수.
- `kind: note`는 다른 twin 키 없이 단독 사용 가능.
- twin 키가 전혀 없는 레거시 5키 노트는 그대로 유효(기존 볼트 전체 무영향).

### kind별 본문 템플릿 (권장 — 누락 시 `TEMPLATE-WARN`, 차단 아님)

- `decision`: `## Context` / `## Decision` / `## Rationale & Trade-offs` / `## What would change my mind`
- `principle`: `## Trigger` / `## Rule` / `## Exceptions`
- `preference`: `## Preference` / `## Boundary`

### 저작 지침 — provenance 의미와 authority 상한

- `stated` = cha가 직접 선언한 판단. **`strict`는 오직 `stated`에만 허용.**
- `observed` = 게이트 승인/거부 이력에서 관찰된 경향 제안. **`advisory`가 상한.**
- `inferred` = LLM이 증거에서 증류한 규칙. **`default`가 상한** (strict 금지).
- observed/inferred 제안 파이프라인은 코드로 상한이 강제되며, 어떤 twin
  노트도 cha의 게이트 ✅ 승인 없이는 활성화되지 않는다 (SI-3).

### 선언 흐름 (DT-C1) — cha가 규칙·원칙·선호를 선언하면

cha가 DM에서 **지속적 판단을 선언**하면(예: “앞으로 …는 이렇게 해줘”, “나는 …를
선호한다”, “…를 원칙으로 진행해줘”) 에이전트는 그것을 twin 초안으로 구조화해
**기존 게이트**에 올린다 — 별도 승인 표면을 만들지 않는다(SI-6):

1. **kind 추론** — `principle`(트리거+규칙) / `preference`(선호+경계) /
   `decision`(맥락+결정+근거) 중에서 발화 성격으로 판정한다.
2. **`provenance: stated` 고정** — cha가 직접 선언했으므로. 기본 `authority`는
   `default`로 제안하고, cha가 강한 구속을 원할 때만 `strict`로 올린다
   (`strict`는 stated에서만 허용).
3. **본문** — kind별 권장 헤딩을 채운다.
4. **초안** — `wiki_cli draft --kind … --authority … --provenance stated …`로
   초안만 만든다(볼트 무접촉).
5. **확인** — 게이트가 확인 메시지에 ✅·⛔를 미리 부착한다. cha는 **✅ 한 번**으로
   저장된다. 에이전트는 `저장 <id>`류 텍스트 답장을 **요구하지 않는다**.

**어디에 담나(자체 메모리 vs 트윈)**: 원칙·결정·선호처럼 **지속적 판단**은 여기
트윈으로 — 자동정리(REVIEW-EXPIRED 등)·무제한 용량·출처추적·recall이 필요하기
때문. 이름·호칭·언어·말투 같은 **짧고 안정적인 사실**과 임시 상태는 트윈 대상이
아니다(자체 메모리 소관).

**SI-1 재확인**: 이렇게 저장된 원칙(예: “호의를 베푼 상대를 배려한다”)은 컨설트·
초안의 **판단 근거일 뿐**, 메일·예산·일정·배포의 소유자 승인 게이트를 우회하는
실행 권한이 아니다. 특정인을 위해 게이트를 건너뛰는 자동 실행은 없다.

## 지식 파사드 컨설트 — "<owner-name>이라면?" (decide-as-cha)

수동 `twin_consult → recall` 순차 조회는 사용하지 않는다. 질의 토큰과 위키의 실제
태그 어휘가 정확히 겹치는 태그만 결정론적으로 선택한 뒤, `purpose="judgment"`인
읽기 전용 지식 파사드를 **한 번만** 호출한다.

```bash
python3 /srv/autophagy-skills/live/wiki/scripts/wiki_cli.py consult \
  "budget 범위에서 cha라면 어떻게 판단할까?"
```

출처 문자열은 전부 파사드 `render_citations(pack, "consult")` 결과이며 다음 라벨만
팩 메타데이터로 붙인다.

- `[위키 규칙]`: `store=wiki`, 비만료, authority가 `strict/default`인 규칙.
- `[RAG 선례]`: 그 밖의 Obsidian/RAG 선례.
- `[불확실·충돌]`: `expired`, `advisory`, authority 부재 또는 팩 계층 `conflict`.

`--json`은 원문 없이 `evidence_count`와 `layers`만 표시한다. 근거가 없으면
"근거 없음", 계층 조회 불가면 "근거 수집 불가"를 표시하며, 재시도·임계값 하향·
직접 `twin_consult`/recall 우회를 하지 않는다. `none/conflict`, 만료·advisory만 존재,
외부효과가 포함되면 cha에게 묻는다. 실제 결정은 통상의 decision-record
초안 → ✅ 게이트로만 축적한다.

**SI-1 (판단 ≠ 권한)**: `authority: strict`라도 메일·캘린더·예산·배포·위키 저장의
소유자 승인 게이트를 우회하지 않는다. **SI-2**: 만료 규칙만으로 자율 행동하지 않는다.

## 지식 근거 규칙

wiki의 자기 저장소 `query`/backlinks/cleanup과 컨설트 태그 어휘 읽기만 R3 예외다.
새 컨설트의 근거 수집·랭킹·출처는 반드시
[`지식 계층 규약`](../../docs/guide/지식-계층-규약.md)의 파사드를 경유한다. 팩 밖
`[En]`은 제거하고 `draft --with-evidence`의 `## Sources`와 0600 게이트 사이드카에
추적성을 남긴다. 위키 쓰기는 여전히 초안 → 확인 메시지 → 소유자 ✅ → `wiki_gate`
한 경로뿐이다.

## 스키마 위반 시

CLI가 exit 2 + `SCHEMA-REJECTED` + 스키마 안내를 출력한다. 그 안내를 cha에게
전달하고 초안을 고쳐 다시 시도한다. 저장은 일어나지 않는다.

## 반응 감시 cron

`wiki_confirm_reaction_watch.py`(no-agent cron, 배포명은 `~/.hermes/scripts/`
안에서 고유)는 pending 초안의 확인 메시지 **리액션만** 폴링한다 — 메시지
텍스트/첨부는 소비하지 않는다(경쟁 소비자 금지). CLI `confirm`과 cron이
같은 draft 저장소·render·`resolve_reaction`을 공유한다(병렬 confirm 표면
신설 금지, SI-6).

## Sandbox scenario

배포 파이프라인용 `scripts/scenario.sh`: 임시 디렉터리에서 초안 무기록,
fail-closed confirm, 스키마 거부, (어댑터 가용 시) 서명 주입 확인 저장과
**twin 초안의 ✅ 리액션 주입 저장 / ✅+⛔ 동시 주입 시 ⛔ 우선 폐기**,
twin bad-enum `SCHEMA-REJECTED`, 만료 `review_after` 픽스처의
`REVIEW-EXPIRED`, query/backlinks/cleanup, 그리고 twin_consult **읽기 전용
증명**(conflict/expired-강등/none verdict 검증 + 컨설트 전후 볼트 전 파일
sha256·디렉터리 목록 불변 비교), `KNOWLEDGE_FAKE_PACK` 기반 단일 파사드 컨설트와
`## Sources` 초안·0600 사이드카를 검증하고 `SCENARIO-PASS`를 출력한다. 네트워크
호출·실시크릿 없음.
