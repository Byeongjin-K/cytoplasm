---
name: proposal
description: "제안서를 쓰거나 고치거나 섹션 진행을 관리하라고 할 때 사용. 개인 제안서 워크스페이스에서 섹션 Kanban·초안·인간 기여분·취합·Codex 최종 검토를 안전하게 관리한다. W5-4."
version: 2.4.1
author: autophagy-agents
license: MIT
metadata:
  hermes:
    tags: [Proposal, Private-Workspace, Kanban, Codex-Review]
prerequisites:
  commands: [python3, hermes]
---

# proposal — 개인 제안서 작성 워크스페이스

변경 명령은 `/srv/autophagy-skills/live/proposal/scripts/`에서만 실행하며, 오래된 사본은 STALE-SKILL-COPY-BLOCK으로 거부한다.

모든 본문은 `~/proposals/<slug>/`(0700)과 그 하위 0600 파일에만 둔다. `PROPOSAL_STATUS_ROOT`는
본문 없이 `slug`, 섹션 키/제목/상태, Kanban 카드 ID만 가진 상태 메타데이터 위치다. 운영에서 repo
메타 worktree를 쓸 때만 이 환경변수로 지정한다. 기본 `~/.hermes/proposal-status/`도 0700이다.

## v2.4.0 변경

본문 낱말에 따른 분류·태그·렌더/그림/윤문 제한을 제거했다. 출처 접근·경로·권한·승인은 유지한다.
기존 데이터는 그대로이며, 이전 파일·카드를 이관하거나 재처리하지 않는다.
관련: [제안서 내용 동일 처리](../../docs/기능소개/제안서-내용-동일-처리.md).

## 절대 규칙

1. `draft --brief-file`은 전체 제안서와 브리프를 공유 Hermes 경로(계정 설정의 주 모델·폴백)로
   처리한다. 특허·기밀 등 본문 낱말은 초안·그림·윤문·렌더·발행 경로를 바꾸지 않는다.
2. `review`는 `hermes -z -t todo`(모델은 계정 설정이 정한다) **1회만** 실행한다. 검토는
   취합본에 저장하고, `PROPOSAL_DM_TARGET` 또는 `~/.hermes/proposal/config.json`의 `dm_target`으로
   cha에게 DM한다. 재검토 대신 사람이 검토 결과를 직접 반영한다.
3. 타인 기여분은 사람이 전달한 `--file` 또는 `--text`만 `contribute`로 섹션에 접는다. 웹/외부
   문서 수집은 **리서치 단계에서만** 허용하며 세 조건을 모두 만족해야 한다. (a) 수집물은
   `inputs/SYNTHESIS.md`의 `## Verified Claims` 표로만 유입되고 각 행에 출처 URL이 반드시 있다
   (`| claim | CONFIRMED | https://... |`). (b) 그 결과가 `corpus/*.md`로 내려갈 때 KD
   `corpus-lint` 게이트를 통과해야 하고 exit 3이면 파이프라인이 멈춘다. (c) 사람이 준
   `--file/--text` 밖의 본문 직접 붙여넣기는 단계와 무관하게 계속 금지다. `--with-evidence`의
   읽기 전용 개인 지식 조회는 수집이 아니며 아래 지식 파사드 규약(파사드 경유)만 따른다.
4. 섹션 카드는 전용 `proposal-<slug>` 보드에 `needs_input` 사유가 있는 `blocked`로 만든다. 이는
   실제 인간 입력 대기 상태이며, Ready 주차 용도가 아니다. 초안이 생기면 Ready로 옮기지 않고
   직접 완료해 디스패처 LLM 워커를 만들지 않는다.
5. 제안서 본문·검토 코멘트·외부 기여분을 repo, `docs/qa`, 공개 채널에 붙이지 않는다. 경로·SHA256·
   구조 assert만 증적으로 남긴다.

## v2 파이프라인 계약

버전 루트는 `~/proposals/<slug>/versions/vNNNNNN/`이고, 아래 8단계는 이 순서로만 돈다.

| 단계 | 입력 | 출력 경로 | 게이트/비고 |
| --- | --- | --- | --- |
| research | 주제·브리프 요청 | `inputs/RESEARCH_BRIEF.md`, `inputs/SYNTHESIS.md` | 웹 수집 허용 구간. `## Verified Claims` 행마다 출처 URL 필수 |
| corpus | `inputs/SYNTHESIS.md`, `inputs/PLAN.md` | `corpus/*.md`, `corpus/plan-brief.md` | 엔진 `corpus-lint` 통과 필수, exit 3이면 차단. PLAN.md 는 render 경로 가드 통과 필수 |
| images | corpus, 도해 지시 | `images/*.png`, `figures.json` | 프롬프트에 `no text, no labels, no numerals`. `figures.json` 캡션은 **번호 없는 설명만** 쓴다 — 렌더러가 `그림 N. `을 붙이고, 이미 붙어 오면 걷어 낸다. 렌더 시 그림은 글자처럼 취급되는 한 줄로 문단 중앙 정렬, 최대 142.9mm(엔진 캡 40,500 HWPUNIT)까지 표시되고 캡션은 그 바로 다음 문단이다. 쪽 끝에 들어가지 않는 그림은 다음 쪽으로 넘어가 앞 쪽 하단이 빌 수 있다(본문을 덮는 것보다 낫다는 2026-09-30 결정). 전송기는 `PROPOSAL_IMAGE_TRANSPORT=fake\|live\|codex`이며, `codex`는 Codex CLI OAuth 세션의 내장 `image_gen`으로 생성하므로 OpenAI API 키가 필요 없다. 지출 원장은 전송기별 청구 주체를 기록해 `live`는 `openai-api` USD를 예약하고, `codex`는 `chatgpt-subscription` 건수·USD 0으로 기록하며 `openai-api`만 `PROPOSAL_IMAGE_MONTHLY_CAP_USD`에 센다 |
| draft | corpus (+`figures.json`) | `out/drafts.json`(+`.planspec.json`, `.pms.json`) | `proposal_cli.py compose` 뒤 `figures` 로 그림 자리 배치 — 엔진 planner·writer·critic·reviser를 Hermes Codex OAuth로 live 실행. 이전 윤문 산출물은 낡으므로 지운다. 끝난 호출은 `out/.llm-resume-cache.json` 에 남아 중단 뒤 재실행이 이어서 끝낸다 |
| refine | `out/drafts.json` | 변경 시 `out/drafts.refined.json`, 항상 `out/refine-report.json` | 공용 Hermes 경로(계정 설정의 주 모델·폴백) 윤문 — 규칙은 im-not-ai humanize-korean quick-rules, markdown 단계, **렌더 이전**. 결정론 전처리로 그림-주어 문장(`[[FIG:x]]은 …를 나타낸다`)을 주장+괄호 인용(`…를 개발한다 ([[FIG:x]]).`)으로 재작성하고 건수를 `figure_citation_recasts`에 기록. 무변경·호스트 불가 시 refined 파일을 만들지 않고 사유 기록 |
| render | `out/drafts.refined.json` | `out/proposal.hwpx`, `out/proposal.hwpx.traceability.md` | 엔진 `render`, `--profile 30-page\|10-page`. 근거 추적성(Coverage)은 본문이 아니라 사이드카 md 로만 나간다. `tables.json`에 `kind: "gantt"` 표(행: `[연차, 꼭지, 시작월, 종료월]`, 월은 연차 안 1..12)가 있으면 추진 내용 표를 전 연차로 채운다 — 연차마다 꼭지 정확히 8개, 마지막 연차 종료 전까지 비는 달이 없어야 하며 위반은 렌더 중단 |
| publish | `out/proposal.hwpx` | Drive `autophagy/제안서/<YYYY>/`, `manifest.json`, `publish-receipt.json` | 게시 수신증 보관 |
| version | 게시 결과 | `HEAD`, `changelog.json`, `CHANGELOG.md` | 다음 판은 `improve --since vN`으로 v_{n+1} |

## 새 제안서 만들기 — 이 순서만 쓴다

```bash
CLI=/srv/autophagy-skills/live/proposal/scripts/proposal_cli.py
for stage in draft images refine; do python3 $CLI preflight --stage $stage >/dev/null || break; done   # PREFLIGHT-BLOCK 이면 멈추고 소유자에게 보고
python3 $CLI research --slug <slug> --goal "<과제 목표>"                    # 새 버전 v000001 + RESEARCH_BRIEF.md
#   inputs/SYNTHESIS.md 의 ## Verified Claims 를 이번 주제의 웹 근거로 채운다(행마다 출처 URL)
#   inputs/PLAN.md 에 과제 설계(개요·기술 내용·KPI 줄·일정·TRL)를 쓴다 — 엔진은 숫자를 지어내지 않는다
python3 $CLI research --slug <slug> --validate-only --json
python3 $CLI corpus  --slug <slug> --json
python3 $CLI compose --slug <slug> --profile 10-page --json              # 본문·계획(planspec)·PMS 를 엔진이 만든다(수십 분) — 끊기면 같은 명령을 다시 실행해 이어서 끝낸다
#   figures.json(그림별 figure_id·section_id·source_claim_ids·prompt·caption·png_sha256·band_index — png_sha256 은 "" 로 두면 images 가 채운다)과 tables.json·cover.json 을 작성한다
python3 $CLI figures --slug <slug> --json                                 # 그림 자리 [[FIG:…]] 를 본문에 놓는다(멱등)
python3 $CLI images  --slug <slug> --json                                 # 그림은 이 명령만 만든다
python3 $CLI refine  --slug <slug> --json
python3 $CLI render  --slug <slug> --profile 10-page --json
#   out/proposal.hwpx.quality.json 의 findings 가 빌 때까지 아래 「평가 감점 점검」대로 고치고 다시 render 한다
python3 $CLI visual-review --slug <slug> --json                           # 모든 쪽 PNG 를 직접 연다
python3 $CLI publish --slug <slug> --version <vNNNNNN> --json
```

**금지 (2026-09-28 사고의 원인):** `drafts.json`·`*.planspec.json`을 손으로 쓰거나 다른 제안서의 planspec·SYNTHESIS를
복사해 고치는 것, PIL 등으로 그림을 직접 그리는 것, 양식 소제목을 문자열 치환으로 끼워 맞추는 것. 이렇게 만든
문서는 엔진의 계획·작성·검토를 한 번도 거치지 않아 참조 제안서(v000011)의 구성·밀도에 닿지 못한다.
단계가 실패하면 우회하지 말고 실패 마커와 필요한 소유자 조치를 그대로 보고한다.

- **`inputs/PLAN.md` 는 필수 설계 입력이다.** 엔진 planner 는 KPI·작업 기간·TRL 을 근거 문장에서만 뽑고 만들어 내지 않는다.
  v000011 이 좋았던 이유는 코퍼스에 소유자가 정리한 계획 문서(KPI·일정·기술 내용 각 2~4KB)가 있었기 때문이다.
  `corpus` 가 PLAN.md 를 `corpus/plan-brief.md`(public)로 싣는다. KPI 는 한 줄에 하나,
  `<지표>; baseline: 6%; target: 3%; unit: %; weight: 40%; method: …; env: …으로 설정한다` 형식이고 가중치 합은 100,
  `method` 에는 **반복 시험 횟수와 산정식**을 함께 쓴다(예: `method: 지형 3종 × 각 30회 시행, 성공률 = 완주 사이클 수 ÷ 전체
  시행 수 × 100`). baseline 은 한 값만 둔다 — 원자료의 소수 자리 값을 따로 적으면 본문이 기준값 둘을 병기한다.
  일정은 `전체 연구 일정은 1-24개월이며, 1-6개월 …, 7-12개월 …` 처럼 월 구간으로 쓴다. 수치는 소유자 확인 전까지 가안으로
  보고하고, 비공개 노트 표지(`obsidian:` 등)가 있으면 render 경로 가드가 거부한다. 없으면 `compose` 가
  `No public KPI evidence … add KPI lines to inputs/PLAN.md` 로 멈춘다.
- **평가 감점 점검(소유자 평가에서 짚인 감점 사유)**: `render` 는 최종 본문·계획·표지를 결정적으로 읽어
  `out/proposal.hwpx.quality.json` 에 감점 사유를 남기고 `{"event": "rubric_findings", "count": N}` 을 찍는다.
  N 이 0 이 아니면 발행하지 말고 코드별로 고친다 — 문구를 손으로 고치는 것이 아니라 입력을 고쳐 다시 만든다.
  - `KPI_VALUE_DRIFT`(논리적 일관성): 한 지표에 기준값이 둘이다 → PLAN.md 의 baseline 을 하나로 정하고 그 밖의 원자료
    값을 지운 뒤 `compose`.
  - `KPI_PROTOCOL_MISSING`(구체성): 반복 시험 횟수·산정식이 없다 → PLAN.md 해당 KPI 의 `method` 에 채우고 `compose`.
    부수 지표(예: 에너지 절감)도 측정·계산 절차를 PLAN.md 기술 내용에 적는다.
  - `KPI_RESTATED`·`NUMBER_RESTATED`·`SCHEDULE_RESTATED`(간결성): 같은 목표·수치·월 구간이 여러 절이나 표지와 요약에
    되풀이된다 → 표지 칸(`cover.json` 의 최종목표·연차목표·기대효과)이 요약 본문과 같은 목록을 싣지 않게 줄이고,
    본문이 되풀이하면 `compose` 를 다시 돌린다(작성기는 KPI 수치를 요약 1번·2절에만 쓰라는 지시를 받는다).
  - `TERM_VARIANT`(단어 적절성): 비표준 외래어 표기 — 렌더가 생성 본문·성과지표·키워드·표지를 표준 표기로 바꾸므로
    남는 것은 근거를 글자 그대로 인용한 구절뿐이다. PLAN.md·SYNTHESIS.md 의 표기를 고쳐 `corpus`·`compose` 를 다시 돌린다.
    그림 캡션(`figures.json`)과 `tables.json` 은 이 검사 밖이니 같은 표기로 직접 맞춘다.
  성과지표 표의 머리행은 렌더가 `성과지표 | 현 수준 → 목표 (가중치) | 측정 방법 · 시험 환경 · 설정 근거` 로 쓴다.
- `render` 는 `images` 가 만들지 않은 그림(레코드에 `model` 출처 없음)을 `UNGENERATED-FIGURES`(exit 5)로 거부한다.
  `--allow-missing-figures` 는 draft preview 로만 렌더하고 `publish` 는 draft preview 를 받지 않는다.
- `images`(codex 전송기)만 노드 agent 의 **Codex CLI 로그인**(`codex login`, `~/.codex/auth.json`)이 필요하다.
  `refine` 은 2026-09-29 부터 다른 모든 모델 호출처럼 공용 Hermes 경로로 가서 계정 `~/.hermes/config.yaml` 의 주 모델·폴백이
  답한다. 윤문 규칙은 `PROPOSAL_REFINE_ROOT`(기본 `~/.hermes/im-not-ai`)의 `skills/humanize-korean/references/quick-rules.md`
  를 프롬프트에 싣는다 — 그 파일이 없으면 `REFINEMENT-HOST-SKIPPED reason=host-unavailable`(exit 6), 주 모델 로그인이 없으면
  청크가 모두 `host-unauthenticated` 로 실패하고 `REFINEMENT-REMEDY` 가 `hermes auth add` 를 안내한다.
  청크 전송이 전부 실패하면 `REFINEMENT_TRANSPORT_FAILED reason=host-unauthenticated|transport-failed`(exit 6)다 —
  `REFINEMENT_INVARIANT_FAILED`(exit 7)는 호스트가 답한 문장을 검사가 거부했을 때만 나온다.
- `compose` 는 Hermes(`hermes -z`, 모델은 계정 설정이 정한다)를 쓰므로 Codex CLI 로그인과 무관하다.
- **`compose` 는 수십 분 걸리고 도중에 끊길 수 있다 — 끊기면 같은 명령을 다시 실행한다.** 터미널은 `background=true`,
  `notify_on_complete=true` 로 띄우고 `process poll/wait`·파일 확인으로 기다리지 않는다. 기다리는 호출마다 턴의 반복 한도를
  쓰기 때문이다(2026-09-29: 한도 소진으로 턴이 끝났고, 이어서 릴리스 수렴의 게이트웨이 재시작이 8분째 compose 를 죽였다).
  완료 알림이 오면 `figures` 부터 잇는다. 재시작 뒤에는 알림이 오지 않으므로 `out/drafts.json` 이 없으면 끊긴 것이다.
  끝난 모델 호출은 버전의 `out/.llm-resume-cache.json`(0600)에 남아 있으므로, 같은 명령을 다시 실행하면 남은 호출만 한다.
  몇 개를 되살렸는지는 출력의 `resumed_llm_calls` 가 말하고, 성공하면 캐시는 지워진다. 같은 버전에서 compose 가 아직 돌고
  있으면 두 번째 실행은 `another compose is still running` 로 거부된다. 끊긴 상태는 "완료"가 아니라
  "중단 — 같은 명령으로 이어감"으로 보고한다.
- Codex CLI 로그인은 **agent 계정**의 것이어야 한다(`sudo -u agent -H bash -lc 'codex login status'`). 운영자 계정의 로그인은
  파이프라인에 쓰이지 않는다. `images` 가 그림을 못 받으면 `IMAGE-MISSING <id>: <사유>` 를 그대로 보고한다.
- 엔진 writer 는 `figures.json` 을 보지 않으므로 compose 직후 본문에는 그림 자리가 없다 — `figures` 를 돌리지 않으면
  render 가 `UNREFERENCED_FIGURE` 로 멈춘다. `figures.json` 이 compose 전에 있으면 compose 가 같은 배치를 함께 한다.
  그림 계획을 바꾸면 `figures` 만 다시 돌린다(윤문 산출물은 낡으므로 지워진다).

윤문이 렌더 앞이라는 순서 자체가 계약이다. refine을 render 뒤로 미루면 다듬은 문장이 산출
HWPX에 들어가지 못하고, 그 시점에는 고칠 표면이 바이너리뿐이라 되돌릴 방법이 없다.
`refine-report.json`의 `refined`, `no_op_detected`, `changed_sentence_count`, `source_equals_output`,
`rules_applied`, `failure_reason`을 확인하면 실제 변경 여부를 본문을 열지 않고 판정할 수 있다.
호스트를 호출하지 못했거나 결과가 원문과 같으면 `refined=false`이며, 원문 사본을
`drafts.refined.json`으로 만들지 않는다. render는 이 명시적 skip 리포트를 확인한 뒤 원본을 사용한다.

윤문 입력은 출력 상한을 채우지 않고 **상한의 85% 이하**를 목표로 작성한다.
`char_budget`은 입력 분량 목표가 아니라 출력 검사의 기준이며, 기존 출력 상한
`int(char_budget * 1.1)`과 원문 대비 길이 비율 0.7~1.3은 그대로 유지한다.
예를 들어 `char_budget=900`이면 출력은 최대 990자이고 입력 목표는 841자 이하다.
840자 초안을 980자로 다듬는 것은 가능하지만, 900자를 1,013자로 늘린 결과는
상한을 넘으므로 여전히 거부된다. 통과시키려고 검사 상한을 넓히지 않는다.

렌더 엔진은 이 저장소가 소유한다 — `skills/proposal/engine/` 의 코드가 같은 프로세스에서 그대로 돈다.
외부 체크아웃도 고정 SHA 핀도 없으므로 엔진을 고치면 그 커밋이 곧 렌더에 반영되고, 어느 엔진이 산출을
만들었는지는 manifest 의 `engine_sha` — 엔진 소스·공유 레이아웃 계약·양식의 내용 digest — 가 기록한다. 그 값은 손에 든
체크아웃만으로 재현되므로 다른 저장소가 없어도 판정할 수 있다.
쪽 목표·그림 슬롯·본문 예산은 공개 계약 `layout_profile.py`의 `LAYOUT_PROFILES`에서 정의한다.
비공개 엔진의 `contracts/layout_profile.py`는 이를 재수출하고, `proposal_ir.PROFILES`는 기존 절 단위 API로 변환한다.
윤문과 렌더의 표를 따로 고치지 않으며, 엔진을 제외한 공개 반출본에서도 IR 명령을 실행할 수 있다.
로컬 렌더는 LLM 클라이언트를 만들거나 네트워크에 본문을 보내지 않는다.
모든 본문은 같은 처리 경로를 쓰며, 출처 기반 개인 노트 원문 경계·소유자 전용 Drive 권한 검사는 유지한다.
렌더는 기록 직전 모든 `Contents/section*.xml` 에서 `hp:linesegarray`(한/글 라인 레이아웃 캐시)를 버리고,
하나라도 살아남으면 `validate` 가 산출을 거부한다 — 문단을 지우거나 밴드를 끼우면 그 뒤 문단이 전부 다른 쪽으로 밀려
캐시가 낡고, 한/글이 그 옛 좌표를 믿어 글줄이 뭉치고 자간이 무시되기 때문이다.
개인 노트 원문을 직접 외부 호스트에 보내지 않는다. 인용·렌더에는 출처 검증을 통과한 근거를 쓰며,
원문 공개 대신 출처 키·요약을 전달하는 경계는 유지한다.

## Commands

CLI는 시작 시 `~/.env.secrets`의 `PROPOSAL_*`·`KIMM_DOCBOT_*`를 fill-only로 읽으며, 기존 환경값이 우선한다.

```bash
# 섹션 구조와 실제 Hermes Kanban 카드 생성
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py create \
  --slug <kebab-slug> --title "제안서 제목" \
  --section need:필요성 --section approach:추진전략 --section impact:기대효과

# 섹션 현황과 추가
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py sections --slug <kebab-slug>
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py section-add \
  --slug <kebab-slug> --key budget --title 예산계획

# 사람이 준 본문을 섹션 초안으로 저장하거나, brief 기반 초안을 생성
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py draft \
  --slug <kebab-slug> --section need --file <local-file>
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py draft \
  --slug <kebab-slug> --section approach --brief-file <local-file> --with-evidence

# 생성 전에 원문을 노출하지 않는 팩 요약 또는 파사드 렌더 출처를 미리 확인
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py evidence \
  --slug <kebab-slug> --section approach --brief-file <local-file> --json

# 사람이 전달한 자료만 관련 섹션에 취합
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py contribute \
  --slug <kebab-slug> --section approach --source collaborator --file <local-file>

# 취합: 누락 섹션은 표지+리마인더를 출력하고 실패하지 않음
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py assemble --slug <kebab-slug>

# 최종 Codex 검토 1회와 cha DM
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py review --slug <kebab-slug>

# 최종 HWPX를 페이지별 PNG로 펼쳐 에이전트가 직접 육안 검토
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py visual-review \
  --slug <kebab-slug> --json
```

## 페이지 시각 검토

`visual-review`는 현재 불변 버전의 `out/proposal.hwpx`를 직접 읽어 페이지별 PNG와 PDF를 만든다.
결과는 원본 버전 안이 아니라
`~/.hermes/proposal/visual-reviews/<slug>/<version>/<hwpx-sha256>/`에 둔다. 같은 바이트는
재사용하므로 v10 같은 발행본을 수정하지 않는다.

시인성이나 가독성을 평가할 때는 XML 수치·문단 길이·쪽수만으로 완료를 주장하지 않는다.
`visual-review --json`이 돌려준 `pages`를 전부 직접 열어 제목 고립, 그림만 있는 쪽, 표 머리글
고립, 과도한 공백, 본문·그림 밀도 편차를 확인한다. 이 미리보기는 양식 판형·글꼴·들여쓰기·표·
그림 크기·캡션을 재현하는 QA 표면이며, 한/글 정밀 렌더러나 제출용 PDF를 대신하지 않는다.
이 미리보기는 브라우저 조판이라 그림·캡션이 본문과 겹치는지는 보여 주지 못한다 — 겹침은 한/글이나
rhwp(VS Code 한글 확장)에서만 드러난다.

배포된 스킬 파일(`/srv/autophagy-skills/live/proposal/…`)은 root 소유 읽기 전용이다. 이 문서나
엔진이 실제 동작과 다르다는 것을 알게 되면 그 파일을 고치려 하지 말고(Permission denied 로
거부된다) 소유자에게 무엇이 어긋났는지 보고한다. 수정은 저장소에서 커밋·릴리스로만 반영된다.

## 지식 근거

근거 조회는 opt-in `--with-evidence`일 때만 [`지식 계층 규약`](../../docs/guide/지식-계층-규약.md)의
읽기 전용 `automation.knowledge` 파사드를 경유한다. proposal은 RAG/wiki/Obsidian을 직접 검색하거나
검색 임계값을 바꾸지 않는다. 초안의 `### 근거` 각주와 취합본 말미의 `## 근거 목록`은 모두
`render_citations`가 만든 단일 출처 형식을 쓰며, 팩 밖 인용은 생성 직후 제거한다. 팩은 섹션 옆
`*.evidence.json`(0600)에 보관한다.

관련 근거가 없으면 초안 머리에 "근거 없음"을 명시하고 소유자의 과거·노트에 관한 사실 주장을
근거 있는 것처럼 쓰지 않는다. 계층 조회가 불가능하면 "근거 수집 불가"를 표시하되 생성은 계속하며
재시도하거나 자체 검색으로 우회하지 않는다. 내용 낱말로 별도 태그나 모델 제한을 만들지 않는다.

## Sandbox

`scripts/scenario.sh`은 더미 시크릿과 임시 0700 워크스페이스만 사용한다. Kanban과 DM을 비활성화한
상태로 섹션 생성·인간 기여분 취합·전체/누락 취합·상태 메타 무본문과
`KNOWLEDGE_FAKE_PACK` 기반 오프라인 근거 초안·각주·사이드카를 검증한다.

## Drive 게시 (최종본)
취합 산출물은 `DRIVE_PUBLISH_ENABLED=1`일 때 assemble 직후 cha 본인 Drive의 `autophagy/제안서/<YYYY>/<YYYY-MM-DD>_<slug>.<확장자>`에 리뷰·기록용으로 자동 업로드된다(초안 제외, 게이트 없음). 날짜는 **최초 발행일로 고정**되어 재취합해도 사본이 늘지 않는다. `assemble --companion <경로>`로 명시 지정한 동반 자료(예: 이미지 프롬프트 원본)가 있으면 산출물과 companion이 `<YYYY-MM-DD>_<slug>/` 번들 폴더에 함께 저장되며, companion은 **원본 파일명을 그대로** 유지한다. 자동 발견·일괄 업로드는 금지다. 발행은 공용 파사드 `automation.drive_outputs`만 쓴다. 상세: `docs/guide/drive-publish.md`.

v2 `publish` 서브커맨드는 리뷰 아티팩트 관례에 따라 승인 없이 검증된 `DriveClient` 경로(owner-only 권한 검사 + SHA-256 재다운로드 대조)를 사용한다. gws 는 JSON 파일을 내려받을 때 자기 형식으로 다시 직렬화하므로, JSON 은 같은 값이 돌아오고 Drive 의 `sha256Checksum` 이 로컬 바이트와 같을 때 통과한다 — 로컬 JSON 을 gws 형식으로 고쳐 쓰는 우회는 필요 없다. 공유·권한 변경·알림은 이 스킬의 범위 밖이며 반드시 external-effect gate를 거쳐야 한다.
