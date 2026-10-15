---
name: prompt
description: "저장해 둔 프롬프트를 찾거나 보거나 새로 추가하라고 할 때 사용. 버전형 프롬프트 자산을 canonical·overlay·legacy 계층에서 결정적으로 검색·조회·추가한다. 모든 새 본문은 동일한 overlay 형식으로 저장한다. W5-1."
version: 1.1.1
author: autophagy-agents
license: MIT
metadata:
  hermes:
    tags: [Prompt-Library, Versioning, Private-Storage, Deterministic]
prerequisites:
  commands: [python3, hermes]
---

# prompt

CLI: `python3 /srv/autophagy-skills/live/prompt/scripts/prompt_cli.py`
Mutating commands run only from /srv/autophagy-skills/live/prompt/scripts/ (stale copies refused with STALE-SKILL-COPY-BLOCK).

## Commands

- `!prompt search <q>` → `prompt_cli.py search "<q>"`
- `!prompt get <id> [--version N]` → `prompt_cli.py get <id> [--version N]`
- `!prompt add` → create a mode-600 body file, then run:

```bash
python3 /srv/autophagy-skills/live/prompt/scripts/prompt_cli.py add \
  --id <id> --category <task|research-background> --purpose "<one line>" \
  --model <openai-codex|any> --tags "tag1,tag2" --body-file <600-file>
```

`add` only writes `~/.hermes/prompt-library/entries/<id>/v<N>.md`; an existing id
creates the next immutable version. Canonical and legacy files are read-only.

모든 새 본문은 overlay에 inline으로 저장하며 내용에 따라 저장소나 호출 경로를 바꾸지 않는다.
기존 데이터는 그대로. 이전에 분리 저장한 본문은 기존 불투명 참조로 읽으며 이동하거나 재처리하지 않는다.

## Sandbox

`scripts/scenario.sh` is offline and verifies version increments, keyword parity,
legacy read-only indexing, file permissions, and isolated imports.
