---
name: report
description: "내 노트로 보고서·발표 슬라이드·대본을 만들라고 할 때 사용. 개인 노트로 보고서·reveal.js 슬라이드·발표 대본을 생성한다. 모든 입력은 같은 모델·발행 경로를 사용한다. W5-3."
version: 1.1.3
author: autophagy-agents
license: MIT
metadata:
  hermes:
    tags: [Private-Notes, Report, RevealJS]
prerequisites:
  commands: [python3, hermes]
---

# report — 노트 → 보고서 → 슬라이드 → 대본

이 스킬은 요청받은 노트 보고서를 만든다. 주간 연구 보고서와 그 키워드는
cytoplasm 밖 별도 연구 에이전트 공도리가 관리하므로 공도리 채널에서 요청한다.
유돌은 노드 `research-trends` 예약이나 `topics` 목록 변경으로 주간 보고서가
생성·갱신된다고 안내하지 않는다.

모든 입력은 `~/notes/`(700)에서만 읽고, 모든 산출물은 `~/outputs/`(700)에만
쓴다. 공유(Drive/repo 이동)는 자동으로 하지 않으며, cha의 명시적 공유 결정 후에만
별도 작업으로 수행한다.

## 절대 규칙

1. `!report`는 모든 노트와 근거를 공유 Hermes 경로(계정 설정의 주 모델·폴백)로 처리한다.
2. 노트·보고서·슬라이드·대본 본문을 Discord 공개 채널, repo 또는 docs/qa에 붙이지
   않는다. CLI 출력의 경로·provider·건수만 응답에 사용한다.
3. `!slides`, `!script`는 이미 `~/outputs/`에 있는 보고서에서만 파생 산출물을 만든다.
   Drive/repo 이동 명령은 이 스킬에 없다.
변경 명령은 `/srv/autophagy-skills/live/report/scripts/`에서만 실행하며, 낡은 사본은 STALE-SKILL-COPY-BLOCK으로 거부한다.

## Commands

```bash
# !report [선택 키워드] — 최근 노트에서 보고서 초안 생성
python3 /srv/autophagy-skills/live/report/scripts/report_cli.py report \
  [--query "키워드"] [--limit 12] [--title "보고서 제목"] [--with-evidence]

# 원문 없이 근거 수와 계층 상태 미리보기
python3 /srv/autophagy-skills/live/report/scripts/report_cli.py evidence \
  [--query "키워드"] [--limit 12] [--title "보고서 제목"] [--json]

# !slides <report-path> — reveal.js HTML 생성
python3 /srv/autophagy-skills/live/report/scripts/report_cli.py slides --report <~/outputs/report-*.md>

# !script <report-path> [slides-path] — 발표 대본 생성
python3 /srv/autophagy-skills/live/report/scripts/report_cli.py script \
  --report <~/outputs/report-*.md> [--slides <~/outputs/slides-*.html>]
```

`!report`가 `자료 부족`을 반환하면 노트를 먼저 추가하거나 `--query`를 넓힌다.
기존 데이터는 그대로.

## 지식 근거 규칙

`--with-evidence`는 기존 `~/notes` 선택 결과에 읽기 전용 지식 파사드의 RAG/wiki/twin
근거를 추가한다. 검색·랭킹·출처 형식은 직접 만들지 않고 반드시
[`지식 계층 규약`](../../docs/guide/지식-계층-규약.md)의 `automation.knowledge`를
경유한다. 보고서의 `## 근거`는 파사드 `sources` 형식만 쓰며 원본 팩은 같은
`report-*.evidence.json`(0600)에 보관한다. 팩 밖 `[En]` 인용은 제거하고,
근거가 없으면 "근거 없음", 조회 불가면 "근거 수집 불가"를 표시한 뒤 생성을
계속한다.

## Sandbox

`scripts/scenario.sh`은 더미 시크릿과 임시 노트만 써서 보고서·슬라이드·대본의 구조,
빈 노트 처리, 키워드 입력의 동일 처리, `KNOWLEDGE_FAKE_PACK` 오프라인
근거·0600 사이드카를 검증한다.

## Drive 게시 (최종본)
최종 산출물은 `DRIVE_PUBLISH_ENABLED=1`일 때 cha 본인 Drive의 `autophagy/주간동향/<YYYY>/<YYYY-Www>_주간연구동향/`에 생성 즉시 자동 업로드된다(초안 제외, 리뷰용, 게이트 없음). 이 경로명은 요청형 보고서의 기존 저장 규약이며 노드 주간 보고 예약을 뜻하지 않는다. 공도리의 보고서·키워드 목록과 자동 연동하지 않는다. 보고서·슬라이드·대본 3종이 그 주간 번들 폴더 한 곳에 모이며, 같은 주에 다시 만들면 새 파일이 생기지 않고 기존 파일이 갱신된다. 발행은 공용 파사드 `automation.drive_outputs`만 쓴다. 상세: `docs/guide/drive-publish.md`.

## 재료 파일과 입력 한도 (2026-10-19)

- 보고서 제목은 생성기가 맨 위에 한 번만 둔다. 모델 초안이 첫 줄에 H1 제목을 반환하면 그 줄만 제외하고 본문·하위 절·근거 노트는 유지한다.
- `report --material-file <파일>`(여러 번 가능)을 주면 노트 폴더 대신 그 파일들만 재료로 쓴다. 유돌은 옵시디언 노트를 `note-get` 으로 읽어 `stage` 한 뒤 그 경로를 넘긴다.
- 모델 중계는 256 KiB 넘는 입력을 받지 않는다. 재료는 200 KiB 안으로 맞추고(앞에서부터 통째로, 넘치는 노트는 앞부분만, 나머지는 뺌), 보고서 끝 `근거` 아래 `자료 줄임(모델 입력 한도):` 한 줄에 무엇을 얼마나 줄였는지 적는다. 조용히 빠지지 않는다.
- 모델 호출이 실패하면 `REPORT-REFUSED <종류>: <원인>` 으로 원인을 함께 출력한다.
