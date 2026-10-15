---
name: recall
description: "사람·과거 일·보낸 메일·내 노트를 묻거나 일에 그게 필요하면 먼저 기억 검색. 개인 RAG(personal_cha) 검색 스킬. `!recall <질문>` 명시 호출 + 개인/프로젝트 지식 질문에 자동 사용. 검색 결과를 출처(위키 경로/회의/보고 id)와 함께 인용하고, 결과가 없으면 반드시 '기억 없음'이라고 답한다(지어내기 금지). RAG 노드 다운 시 '검색 불가' 안내 후 일반 답변. W2-5."
version: 1.5.0
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [RAG, Memory, Recall, Source-Attribution, Autophagy]
prerequisites:
  commands: [python3]
---

# 개인 기억 회수 (recall)

cha의 개인 RAG(`personal_cha` 컬렉션, W2-1 MCP memory server)를 검색해
**출처가 붙은 컨텍스트**로 답한다. 검색은 로컬 MCP 한 곳으로만 나가며,
외부 임베딩/LLM API를 절대 호출하지 않는다.

## 언제 쓰나 (2가지 진입점)

1. **명시 호출**: 사용자 메시지가 `!recall <질문>` 형태면 **반드시** 아래
   검색 명령을 실행하고 그 결과로만 답한다.
2. **자동 사용**: cha의 개인/프로젝트/팀 지식(위키에 정리한 내용, 회의,
   동료 보고, 과거 작업/대화)에 대한 질문에 답하기 전에 **먼저 검색**한다.
   임계값 이상의 결과(status=hit)가 있으면 그 컨텍스트를 근거로 답한다.
   일반 상식·코딩 등 개인 기억과 무관한 질문에는 검색을 생략해도 된다.

## 검색 명령

```bash
python3 /srv/autophagy-skills/live/recall/scripts/recall_cli.py search "<질문>" --json
```

출력은 `recall-v1` JSON 한 개다(스키마는 `scripts/recall_core.py` 문서화):
`status`가 `hit`(결과+출처) / `no_memory`(기억 없음) / `unavailable`(검색 불가)
중 하나다. `--json`을 빼면 사람이 읽는 텍스트 렌더링이 나온다. 이 `search` 표면은
기존 호출자의 byte-level 계약을 위한 호환 경로다.

기본 검색은 저장소 전체를 최대 3번 찾아(검색마다 새 노트 60개·노트당 두 조각) 계정 주 모델이 질문에 맞춘
300자 창으로 최대 8개를 추리고, 추린 노트의 두 조각 전문을 다시 읽어 필요한 사실이 적힌 노트를 최대 4개
고른다(형 Q170 "다 보류하고 고르는 판단 한 번 더(새 세트 H로)", 자동 회상과 같은 고르기, 봉인 세트 H 찾음
59/69). 그래서 한 번 호출에 약 10–20초가 걸린다. 고른 결과는 점수 문턱을 다시 거치지 않고
`search.picked=true`로 표시된다. 모델이 답하지 못하거나 검색 서버가 예전 판이면 예전 검색(점수 문턱)으로
돌아가고 `search.picked=false`다. `RECALL_PICK=0`이면 끈다.

## 세 저장소 근거 명령

대화 답변에 Obsidian 원천 노트, 승인 wiki 판단, 기타 RAG 기록을 함께 쓰려면 단일
지식 파사드 전면을 호출한다.

```bash
python3 /srv/autophagy-skills/live/recall/scripts/recall_cli.py evidence "<질문>" \
  --purpose cite --json
```

`--purpose`는 `cite|synthesize|entity|judgment`다. JSON은 `knowledge-v1` verdict,
`evidence_count`, 계층 상태, 건수·사유 notes, 그리고
`render_citations`가 만든 출처 블록만 내보내며 근거 원문은 내보내지 않는다.
텍스트 모드는 같은 출처 블록과 결정론적 `근거 없음`/`근거 수집 불가` 문구를 쓴다.
팩 밖 `[En]`은 `validate_citations`로 제거해야 한다. 직접 wiki/RAG를 추가 검색하거나
출처 형식을 재구현하지 않는다. 정본은
[`docs/guide/지식-계층-규약.md`](../../docs/guide/지식-계층-규약.md)다.

### 엔터티 보조 검색 (기본 off)

`--entity-fallback`을 주면 사람/기관 후보와 최근성·관계 표현이 함께 있는
질의가 1차 검색에서 `no_memory`일 때만, 엔터티 앵커로 보조 검색을 정확히 한
번 수행한다. 두 후보군은 문서 식별자로 중복 제거하며, 엔터티 원문이 content
또는 metadata에 실제 있는 행만 기존 score/grounding 임계값으로 판정한다.
검색 limit과 임계값은 바꾸지 않는다. `search.searches`는 실제 검색 수(1 또는
2), `search.entity_hint_count`는 힌트 수만 제공하며 엔터티 원문은 추가로
출력하지 않는다. flag가 없으면 기존 단일 검색 동작 그대로다.

```bash
python3 /srv/autophagy-skills/live/recall/scripts/recall_cli.py search "<관계 질문>" \
  --entity-fallback --json
```

## 참고자료 조회 (소유자 Drive 폴더)

소유자가 Drive 「내 드라이브/KIMM」에 모아 둔 자료에서 **근거 구절**을 찾는다. RAG 검색과
다른 표면이다 — 이쪽은 인제스트된 기억이 아니라 소유자가 방금 올려 둔 원본 파일을 본다.

```bash
python3 /srv/autophagy-skills/live/recall/scripts/recall_reference.py "<질의>" [--limit 3] [--json]
```

- 루트는 `DRIVE_REFERENCE_ROOT`(기본 `KIMM`, `/`로 중첩 경로 가능), Drive 접근은 다른 Drive
  경로와 같은 옵트인 `DRIVE_PUBLISH_ENABLED=1`을 따른다. 꺼져 있으면 아무것도 조회하지 않는다.
- **읽기 전용**이다. 그 폴더에 폴더·파일을 만들지 않으며, 루트가 없으면 만들지 않고
  `REFERENCE-ROOT-MISSING` 사유만 낸다.
- 읽는 형식은 pdf·pptx·docx·hwpx·xlsx·md·txt·csv와 Google 문서·슬라이드·스프레드시트(내보내기)다.
  Google 설문처럼 내보낼 수 없는 것, 구형 `.hwp`, 64MiB 초과 파일은 **내려받기 전에** 사유만
  남기고 자리를 양보한다 — 읽을 수 있는 자료가 먼저 조회된다. `.hwp` 는 소유자에게 hwpx 나
  pdf 로 다시 저장하라고 안내한다.
- **종료 코드는 항상 0**이고 사유는 본문에 적힌다 — 근거를 못 찾은 것은 실패가 아니다.
  못 찾았으면 지어내지 말고 "참고자료에서 근거를 찾지 못했다"고 답한다.

## 답변 규칙 (절대 준수)

1. **status=hit** → `results[*].excerpt`를 근거로 답하고, 답변 끝에 반드시
   출처를 표기한다:

   ```
   <답변 본문>

   출처:
   - 위키: w2-5-노트.md (score 0.61)
   - 회의: 2026-07-15-회의요약.md (score 0.55)
   ```

   출처 문자열은 `results[*].attribution`을 그대로 쓴다(위키 경로/회의
   문서/`#agents-log` 메시지 id/task id/세션 id — W2-4가 붙인 메타데이터).
   컨텍스트에 **없는** 내용은 답에 보태지 않는다.
2. **status=no_memory** → 정확히 **"기억 없음"** 이라고 답한다. 추측·지어내기
   금지. 필요하면 "기억 없음 — 관련 내용이 개인 RAG에 없습니다. 위키에
   정리해 두면 다음에 기억할 수 있어요." 처럼 한 문장만 덧붙인다.
3. **status=unavailable** → "지금 기억 검색이 불가합니다(RAG 노드 응답
   없음)"라고 먼저 알린 뒤, 일반 지식으로만 답한다. **재시도 루프 금지** —
   각 검색은 1회만 시도하며, 같은 턴에서 CLI를 다시 호출하지 않는다. 위 flag의
   조건부 보조 검색은 장애 재시도가 아니라 별도 엔터티 검색이다.
## Sandbox scenario

배포 파이프라인용 `scripts/scenario.sh`: 오프라인 픽스처로 hit 스키마/출처
표기, 임계값 미달 → `기억 없음`, RAG 다운 → `검색 불가`(1회 시도, 재시도
없음)를 검증하고 `SCENARIO-PASS`를 출력한다. 네트워크 호출·실시크릿 없음.

## 검색어 규칙

검색어는 소유자의 문장을 그대로 넣지 말고 핵심 낱말 2~4개(사람 이름, 장소, 과제명)로 만든다. 결과가 없으면 고유명사만 남겨 한 번 더 검색한다.
