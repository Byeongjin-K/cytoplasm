---
name: hello-autophagy
description: "스킬 배포 파이프라인이 동작하는지 데모로 확인하고 싶을 때만 사용. Deterministic demo skill that validates the W1-8 skill deploy pipeline (sandbox → owner approval → mount). Prints a fixed Korean greeting via scripts/hello.sh."
version: 1.0.2
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Demo, Pipeline-Validation, Autophagy]
prerequisites:
  commands: [bash]
---

# Hello Autophagy (demo)

W1-8 배포 파이프라인(샌드박스 → peer 서명 증명 → 소유자 승인 → 마운트) 검증 전용 데모 스킬.
실제 외부 효과가 전혀 없는 결정론적 스킬이다: 인사말 한 줄을 출력한다.

## Usage

사용자가 "autophagy 인사" 또는 "hello autophagy demo"를 요청하면:

```bash
bash /srv/autophagy-skills/live/hello-autophagy/scripts/hello.sh
```

출력 마커는 항상 `HELLO-AUTOPHAGY`로 시작한다. 이 마커가 없으면 실패다.

## Sandbox scenario

배포 파이프라인의 샌드박스 단계는 `scripts/scenario.sh`를 DUMMY 시크릿과 함께
실행한다. 시나리오는 다음을 강제한다:

- `AUTOPHAGY_DEMO_SECRET` 가 존재하고 `DUMMY-` 접두사를 가질 것 (실시크릿 거부)
- 실시크릿 모양(`sk-`, `ghp_`, `Bot ` 토큰 형태)이면 즉시 실패
- `hello.sh` 출력에 `HELLO-AUTOPHAGY` 마커가 있을 것
- 성공 시 마지막 줄에 `SCENARIO-PASS` 출력, 실패 시 non-zero exit

## Safety

- 네트워크 호출 없음, 파일 쓰기 없음, 시크릿 읽기 없음.
- 이 스킬은 검증 후 인스턴스에서 제거되는 것을 전제로 한다.
