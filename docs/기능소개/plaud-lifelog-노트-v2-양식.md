# Plaud lifelog 노트 v2 양식 (B안) — Linter 정합 frontmatter · 한눈에 · 결정 · 할 일 · 접힌 전문

## 무엇을

`plaud_sync` 가 만드는 lifelog 노트(`000_PARA/Area/Lifelog/<연도>/`)가 **소유자의 Obsidian Linter 가 그대로 두는
형식**으로 나온다. 노트는 YAML frontmatter(`tags → title → source → created → modified`)로 시작하고, `# 제목` 아래
`## 한눈에`(녹음·화자 교정 양식·진단 — Dataview 인라인 필드) → `## 요약`(Plaud 우선, 없으면 같은 추출 호출의 요약) →
`## 결정 · 할 일`(`- 결정:` / `- [ ] … — 담당 · 기한 [MM:SS]`, 없으면 절 생략) → `## 교정 기록`(동결 전 교정이 바꾼 낱말 표, [바꾼 곳이 있을 때만](plaud-전사-동결전-교정.md)) → `## 전문`(`> [!quote]-` 접힌
callout) → `---` 출처 순이다. 사람·장소·결정·할 일·요약은 Codex OAuth 단일 호출로 **로컬 전사**에서 뽑는다.

```markdown
---
tags: [lifelog, lifelog/일상-잡담, lifelog/업무]
title: 2026-09-02_0902_직장_동료들의_일상_대화_업무,_진로,_취미
source: PLAUD 녹음 mem_…
created: 2026-09-02T09:02:00
modified: 2026-09-02T09:02:00
---

# 2026-09-02_0902_직장_동료들의_일상_대화_업무,_진로,_취미

## 한눈에

- 녹음:: 2026-09-02 (수) 09:02 · 30분 30초 · 화자 2명
- 화자:: 화자1=미상 · 화자2=미상

## 요약
…(Plaud 요약 그대로, 자체 소제목 유지)

## 결정 · 할 일

- 결정: 다음 주 세미나 참석 [12:40]
- [ ] 세미나 일정 확인 — 담당 나 · 기한 다음 주 [12:41]

## 전문

> [!quote]- 전문 펼치기 (128 발화)
> [00:00:05] 화자1 · 김OO
> …

---

출처: PLAUD 녹음 mem_… · 2026-09-02T00:02:00+00:00 · 30분 30초 · 전사: 로컬 전사 local:… · 화자 분리
```

## 왜

- 소유자 결정(2026-09-04): 웹에서 인기 있는 정리법(Obsidian Properties·Bullet Journal 할 일·Interstitial
  타임스탬프·AI 회의 요약 구조) 중 자동 생성 노트에 맞는 요소만 고른 **B안** + "포매터는 내 Obsidian Linter 에 맞춰서".
- 소유자 vault 의 Linter(v1.32.0) 활성 규칙 6개는 전부 YAML 규칙(`yaml-key-sort`·`yaml-timestamp`·`yaml-title`·
  `format-yaml-array`·`insert-yaml-attributes`·`remove-yaml-keys`)이다. 그 키 순서의 frontmatter 로 시작하지 않으면
  저장할 때마다 Linter 가 노트를 고쳐 쓴다. 이제 노트는 **vault 의 실제 플러그인 빌드**를 헤드리스로 돌려도 바뀌지
  않는다(`lint(x) == x`, 렌더 샘플 3종 + 제목 따옴표 규칙 61건 대조 — `docs/qa/PLV2/linter-idempotence.txt`).
- 화자 교정: 생성 때 `## 한눈에`에 실제 분리 라벨을 `- 화자:: 화자1=미상 · …` 양식으로 제공한다.
  `미상`은 등록 워처가 무시하므로 자동 추정이 동의 없이 학습되지 않는다. 소유자가 `미상`을 실제 이름으로
  바꾸면 그 편집이 명령·동의가 되어 목소리 카탈로그에 등록된다. 전문은 접혀 있어도 텍스트라 RAG 인제스트에는
  그대로 잡힌다.

## 어떻게

1. **발견** — 새 녹음은 `transcribing` 초안으로 동결된다. 초안은 LLM 을 부르지 않고 한눈에 줄에
   `- 추출:: 생략 (로컬 전사 뒤 추출)` 만 적는다(같은 녹음에 두 번 쓰지 않고, `speaker_1` 라벨보다 나은 입력을 기다린다).
2. **로컬 전사 뒤 finalize** — `transcribe.finalize` 가 로컬 전사가 들어온 녹음에 추출기를 돌려 노트를 다시 만들고
   `planned` 로 올린다. 추출 게이트 순서는 **규칙 파일 → patent-sensitive → Codex OAuth 클라이언트 → 템플릿**:
   규칙 파일이 없으면 LLM 을 부르지 않고(fail-closed) `민감도 규칙 없음`, 특허 민감이면 `민감도 게이트`, 클라이언트를 못 쓰면
   `LLM 미설정` 으로 한눈에 줄에 사유가 적힌다. 전송·파싱 실패는 전사 시도로 세지 않고 `추출: …` 사유로 대기해 다음
   틱에 재시도한다 — 저하된 노트를 영구 동결하지 않는다.
3. **승인 카드 v3** — 미리보기가 한눈에 줄을 먼저 인용하고 요약으로 5줄을 채운다. 승인 해시는 노트 본문에 묶이므로
   frontmatter·추출 결과까지 소유자 ✅ 에 포함된다.
4. **저장** — `obsidian_write.render_note` 가 body 선두의 frontmatter 블록을 `# 제목` 위로 올리고 info callout 을
   생략한다(tags·created·modified 가 YAML 에 있어 중복). frontmatter 없는 다른 노트(wiki·memory)는 바이트 동일하다.
5. **시간대** — `created`/`modified` 와 한눈에 줄은 녹음 시작 시각을 `PLAUD_SYNC_TIMEZONE`(기본 Asia/Seoul)으로 옮긴
   로컬 시각이다. 노트 날짜·경로도 같은 시각을 따른다(노드는 UTC).

env: `PLAUD_SYNC_LLM_TIMEOUT`(120) · `PLAUD_SYNC_EXTRACT_PROMPT`(기본 `prompts/lifelog-extraction-v2.md`) ·
`PLAUD_SYNC_TIMEZONE`. 모델은 기존 Codex OAuth 구독 경로이며 별도 API 키·대체 모델은 쓰지 않는다.

## 사용 시나리오

1. **happy**: 녹음 → 로컬 전사 → 노트의 한눈에에 `- 녹음:: … · 화자 3명 / - 화자:: 화자1=미상 · 화자2=미상 · 화자3=미상`
   → ✅ → vault 에 위 양식 그대로. 소유자는 `미상`만 실제 이름으로 바꾸며, 다음 워처 틱에 해당 목소리가 등록된다.
   Obsidian 에서 열어 저장해도 Linter 가 본문을 바꾸지 않는다.
2. **특허 이야기가 섞인 녹음**: 민감도 규칙이 걸리면 LLM 에 아무것도 보내지 않고 `- 추출:: 생략 (민감도 게이트)` —
   결정론 필드(녹음·화자 교정 양식)만으로 저장된다.
3. **노드에 Codex 클라이언트가 없다**: `- 추출:: 생략 (LLM 미설정)` 이 카드에 보인다. 기존 OAuth 실행 경로를
   복구하면 **다음 녹음부터** 적용된다(동결 본문은 승인 해시에 묶여 소급되지 않는다).
4. **Codex 호출이 잠시 실패했다**: `plaud 상태` 가 `전사 대기(transcribing) 1건 · 사유 추출: …` 를 보이고 다음 틱에 재시도한다.

## 한계·주의

- Linter 의 `date-*-source-of-truth` 가 `file system` 이라, 소유자가 Obsidian 에서 그 노트를 **처음 저장**하면
  `modified` 가 그 시각으로 바뀐다(`created` 는 유지 — 실측). 녹음 시각을 보존하려면 vault Linter 설정에서 modified 의
  출처를 `frontmatter` 로 바꾼다(소유자 결정, 노트 생성 코드 무관).
- 이미 push 된 v1 노트는 소급되지 않는다(processed 원장이 재동기화를 막는다).
- finalize 에서 추출이 실패하면 전사본은 저장되지 않고 다음 틱이 전사를 다시 한다(정확성 무영향, 비용만).

## 관련

- 코드: `automation/plaud_sync/lifelog_model.py`(shape) · `lifelog_fields.py`(frontmatter·한눈에·결정·접힌 전문·
  `yaml_scalar`·`note_timezone`) · `note.py`(조립) · `lifelog_extract.py`(프롬프트·관대한 JSON 파서) ·
  `lifelog_extract_live.py`(게이트·Codex OAuth) · `sync.py`(초안은 LLM 미호출) · `transcribe.py`(finalize 에서 추출) ·
  `render.py`(카드 v3) · `automation/obsidian_write/note.py`(frontmatter 호이스트)
- 프롬프트 `prompts/lifelog-extraction-v2.md`(v1 동결) · [전사 정책 게이트](plaud-전사-정책-게이트.md) · 계획 `.omo/plans/plaud-lifelog-format-v2.md` · 증적 `docs/qa/PLV2/`
- 검증: `tests/unit/test_plaud_sync_{note,render,sync,lifelog_extract,transcribe,watch_timezone}.py`,
  `tests/unit/test_obsidian_write.py`
