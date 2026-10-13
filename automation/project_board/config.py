"""Where project-board state lives and what the node's config file may say.

State is runtime data outside any checkout (``~/.hermes/project-board``, 0700). The config
file is optional: without it the analysis uses the Hermes main model, the analysis cron
stays idle (``analyze.enabled`` false) and the daily digest has no recipients — a
release alone never starts posting cards or mail.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .llm import LlmConfig, llm_config
from .models import ProjectBoardError

ROOT_ENV: Final = "PROJECT_BOARD_ROOT"
CONFIG_ENV: Final = "PROJECT_BOARD_CONFIG"
DEFAULT_PORT: Final = 8810


@dataclass(frozen=True, slots=True)
class ProjectEntry:
    """One known project of the owner: the exact name the model must reuse, and its cues."""

    name: str
    aliases: tuple[str, ...] = ()
    note: str = ""

    def prompt_line(self) -> str:
        aliases = f" (별칭: {', '.join(self.aliases)})" if self.aliases else ""
        note = f" — {self.note}" if self.note else ""
        return f"{self.name}{aliases}{note}"


@dataclass(frozen=True, slots=True)
class BoardConfig:
    llm: LlmConfig
    analyze_enabled: bool = False
    max_per_tick: int = 5
    port: int = DEFAULT_PORT
    digest_to: tuple[str, ...] = ()
    digest_subject: str = "프로젝트 현황 일일 요약"
    projects: tuple[ProjectEntry, ...] = ()
    prompt_version: str = "v2"

    def catalog_lines(self, stored: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Configured projects first (with cues), then approved names the catalog lacks."""
        named = {entry.name for entry in self.projects}
        return (*(entry.prompt_line() for entry in self.projects), *(n for n in stored if n not in named))


def _projects(raw: object) -> tuple[ProjectEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ProjectBoardError("config projects must be a list")
    entries = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise ProjectBoardError("each config project needs a name")
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            raise ProjectBoardError("config project aliases must be a list")
        entries.append(
            ProjectEntry(
                " ".join(str(item["name"]).split())[:60],
                tuple(" ".join(str(a).split()) for a in aliases if str(a).strip()),
                " ".join(str(item.get("note") or "").split())[:200],
            )
        )
    return tuple(entries)


def state_root(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    override = (source.get(ROOT_ENV) or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".hermes" / "project-board"


def config_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    override = (source.get(CONFIG_ENV) or "").strip()
    return Path(override).expanduser() if override else state_root(env) / "config.json"


def load_config(env: Mapping[str, str] | None = None) -> BoardConfig:
    path = config_path(env)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return BoardConfig(llm=LlmConfig())
    except (OSError, ValueError) as error:
        raise ProjectBoardError(f"project-board config is unreadable: {path.name}") from error
    if not isinstance(raw, dict):
        raise ProjectBoardError("project-board config must be a JSON object")
    analyze = raw.get("analyze") or {}
    dashboard = raw.get("dashboard") or {}
    digest = raw.get("digest") or {}
    if not all(isinstance(block, dict) for block in (analyze, dashboard, digest)):
        raise ProjectBoardError("config analyze/dashboard/digest must be objects")
    recipients = digest.get("to") or []
    if not isinstance(recipients, list) or not all(isinstance(item, str) and "@" in item for item in recipients):
        raise ProjectBoardError("config digest.to must be a list of mail addresses")
    port = int(dashboard.get("port") or DEFAULT_PORT)
    if not 1024 <= port <= 65535:
        raise ProjectBoardError("config dashboard.port must be 1024-65535")
    return BoardConfig(
        llm=llm_config(raw.get("llm")),
        analyze_enabled=bool(analyze.get("enabled", False)),
        max_per_tick=max(1, min(20, int(analyze.get("max_per_tick") or 5))),
        port=port,
        digest_to=tuple(recipients),
        digest_subject=str(digest.get("subject") or "프로젝트 현황 일일 요약"),
        projects=_projects(raw.get("projects")),
        prompt_version=str(analyze.get("prompt") or "v2"),
    )
