---
name: topics
description: "노드에 남은 연구 주제 목록을 추가·확인·삭제할 때 사용. `!topics add|list|remove`는 로컬 레지스트리만 관리한다. 주간 연구 보고서와 그 키워드 요청은 별도 연구 에이전트 공도리 채널로 안내한다."
version: 1.1.3
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Research, arXiv, Weekly, Safety, Autophagy]
prerequisites:
  commands: [python3]
---

# 보존된 노드 연구 주제 목록 (topics)

등록 키워드는 agent 개인 레지스트리 `~/.hermes/state/research-topics.yaml`에만
저장된다. 노드 `research-trends` 예약은 은퇴했다. 주간 연구 보고서와 그 키워드
목록은 cytoplasm 밖 별도 연구 에이전트 공도리가 관리한다. 주간 보고서나 키워드
변경 요청은 공도리 채널에서 하도록 안내하고, 유돌이 이 로컬 목록을 바꾸어
주간 보고서에 반영된다고 약속하지 않는다. 기존 데이터는 그대로이며 자동 이관·연동은 없다.

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

로컬 연구 주제 목록을 따로 관리해 달라는 요청에서 **일반 연구 분야**가 보이면 저장 전에 다음으로
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

주간 보고서 생성·발송·키워드 관리는 공도리 소관이며 공도리 채널에서 요청한다.
이 스킬은 노드 주간 보고서를 예약하거나 발송하지 않는다.
다음 승인된 릴리스가 기존 노드 타이머·서비스를 제거하고 기존 보고서·주제 데이터는 보존한다.
상세: [노드 예약 은퇴](../../docs/기능소개/주간-연구동향-노드-예약-은퇴.md).
