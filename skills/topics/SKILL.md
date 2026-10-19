---
name: topics
description: "주간 연구동향(arXiv) 키워드를 추가·확인·삭제하라고 할 때 사용. `!topics add|list|remove`로 arXiv 주간 연구동향 키워드를 관리한다. 모든 등록 주제에 동일한 조회·보고 경로를 사용한다."
version: 1.1.2
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Research, arXiv, Weekly, Safety, Autophagy]
prerequisites:
  commands: [python3]
---

# 주간 연구 동향 키워드 (topics)

등록 키워드는 agent 개인 레지스트리 `~/.hermes/state/research-topics.yaml`에만
저장된다. 노드 agent 계정의 사용자 타이머 `research-trends`(평일 09:00, 주 1회만 발송)가
등록 키워드로 arXiv API를 조회하고, 한국어 정리는 공용 배치 모델 경로(`codex_llm`, omo 중계 스위치가
켜져 있으면 릴리스 호스트의 도구 없는 omo)가 한다. 기존 데이터는 그대로.

## 명시 명령

```bash
python3 /srv/autophagy-skills/live/topics/scripts/topics_cli.py add "<일반 연구 분야>"
python3 /srv/autophagy-skills/live/topics/scripts/topics_cli.py list [--with-evidence]
python3 /srv/autophagy-skills/live/topics/scripts/topics_cli.py remove "<등록 주제>"
```

- `!topics add <주제>`: `TOPIC-ADDED`이면 저장 완료, `TOPIC-EXISTS`이면 기존
  항목을 유지한다.
- `!topics list`: 현재 등록 목록을 표시한다.
- `!topics remove <주제>`: 정확한 정규화 항목을 제거한다.

## 대화 기반 제안

대화 중 주간 추적에 적합한 **일반 연구 분야**가 보이면 저장 전에 다음으로
제안 가능 여부만 확인한다. 이 명령은 레지스트리를 바꾸지 않는다.

```bash
python3 /srv/autophagy-skills/live/topics/scripts/topics_cli.py suggest "<후보>"
```

`TOPIC-SUGGEST`일 때만 cha에게 “등록할까요?”라고 묻고, cha가 동의하면 `add`를
실행한다. 제안 후보의 원문을 외부 도구·arXiv·LLM에 전달하지 않는다.

## 지식 파사드 근거

`list --with-evidence`와 `evidence [--json]`은 등록 주제를 읽기 전용 지식 파사드에
한 번 질의해 `## 내 관련 노트`를 보여준다. 출처는 파사드 `sources` 형식만 사용하고
팩은 레지스트리 옆 mode 0600 `*.evidence.json`에 남긴다. 근거가 없으면 `근거 없음`,
조회 불가면 `근거 수집 불가`를 표시하며 자체 검색·재시도·임계값 하향은 하지 않는다.
정본은 [`지식 계층 규약`](../../docs/guide/지식-계층-규약.md)이다.

## 주간 리포트

`research-trends` 사용자 타이머가 등록 항목별 논문 링크와 한국어 동향 정리, 파사드가
렌더한 `내 관련 노트` 절을 통지 채널 메시지 하나(보고 파일 첨부)로 보내고 같은 리포트를 개인 RAG 경로로 적재한다.
재인제스트된 `note:research-trends/` 보고서는 다음 주 근거에서 제외해 자기인용 루프를
막는다. 근거 수집 불가도 보고서 생성을 막지 않으며, 모든 근거는 동일한 모델 호출 경로로 처리한다.
주 1회 발송 워터마크와 dry-run 비소진 계약은 그대로다.
외부 논문 출처 장애 항목은 실패 안내가 포함된 부분 리포트로 끝난다.
