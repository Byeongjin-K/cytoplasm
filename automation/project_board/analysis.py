"""Turn one collected mail or minutes into a proposal — or into nothing when it is unrelated.

The model's answer is parsed strictly: one JSON object, the documented keys, dates as
``YYYY-MM-DD`` or null. A malformed answer is an error, not a best-effort guess, because
whatever is parsed here becomes the text the owner approves.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Final

from .llm import LlmProvider
from .models import GRADES, Proposal, ProjectBoardError, SourceRef, proposal_from_dict
from .sources import SourceDocument

_PROMPTS: Final = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_VERSIONS: Final = {
    "v1": _PROMPTS / "project-analysis-v1.md",
    "v2": _PROMPTS / "project-analysis-v2.md",
    "v3": _PROMPTS / "project-analysis-v3.md",
}
DEFAULT_PROMPT: Final = "v3"
UNCLASSIFIED: Final = "미분류"


def build_prompt(
    document: SourceDocument,
    known_projects: tuple[str, ...],
    today: date,
    *,
    open_todos: tuple[str, ...] = (),
    version: str = DEFAULT_PROMPT,
    owner: tuple[str, ...] = (),
    team: tuple[str, ...] = (),
) -> str:
    if version not in PROMPT_VERSIONS:
        raise ProjectBoardError(f"unknown analysis prompt version: {version}")
    template = PROMPT_VERSIONS[version].read_text(encoding="utf-8")
    ref = document.ref
    values = {
        "{{TODAY}}": today.isoformat(),
        "{{KNOWN_PROJECTS}}": "\n".join(f"- {line}" for line in known_projects) or "(없음)",
        "{{OWNER}}": " / ".join(owner) or "(설정 없음 — 받는 사람)",
        "{{TEAM}}": ", ".join(team) or "(설정 없음)",
        "{{OPEN_TODOS}}": "\n".join(f"- {line}" for line in open_todos) or "(없음)",
        "{{SOURCE_KIND}}": "기관 메일" if ref.kind == "mail" else "회의록",
        "{{SUBJECT}}": ref.subject or "(제목 없음)",
        "{{SENDER}}": ref.sender or "(없음)",
        "{{DATE}}": ref.date or "(없음)",
        "{{ATTACHMENTS}}": ", ".join(ref.attachments) or "(없음)",
        "{{TEXT}}": document.text,
    }
    for marker, value in values.items():
        template = template.replace(marker, value)
    return template


def _json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    try:
        value = json.loads(stripped)
    except ValueError as error:
        raise ProjectBoardError("model answer is not one JSON object", retryable=True) from error
    if not isinstance(value, dict):
        raise ProjectBoardError("model answer is not a JSON object", retryable=True)
    return value


def parse_answer(text: str, source: SourceRef, *, provider: str, model: str) -> Proposal | None:
    raw = _json_object(text)
    relevant = raw.get("relevant")
    if relevant is False:
        return None
    if relevant is not True:
        raise ProjectBoardError("model answer lacks a boolean 'relevant'", retryable=True)
    todos = raw.get("todos")
    if not isinstance(todos, list):
        raise ProjectBoardError("model answer lacks a 'todos' list", retryable=True)
    try:
        return proposal_from_dict(
            {
                "project": raw.get("project"),
                "project_owner": raw.get("project_owner"),
                "summary": raw.get("summary"),
                "todos": todos,
                "source": {
                    "kind": source.kind, "source_id": source.source_id, "subject": source.subject,
                    "sender": source.sender, "date": source.date, "attachments": list(source.attachments),
                },
                "provider": provider,
                "model": model,
                "grade": raw.get("grade") if raw.get("grade") in GRADES else "",
            }
        )
    except ProjectBoardError as error:
        raise ProjectBoardError(f"model answer rejected: {error}", retryable=True) from error


def analyze(
    document: SourceDocument,
    provider: LlmProvider,
    *,
    known_projects: tuple[str, ...],
    today: date,
    open_todos: tuple[str, ...] = (),
    version: str = DEFAULT_PROMPT,
    owner: tuple[str, ...] = (),
    team: tuple[str, ...] = (),
) -> Proposal | None:
    prompt = build_prompt(
        document, known_projects, today, open_todos=open_todos, version=version, owner=owner, team=team
    )
    answer = provider.complete(prompt)
    return parse_answer(answer.text, document.ref, provider=answer.provider, model=answer.model)
