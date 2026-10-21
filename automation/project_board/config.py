"""Where project-board state lives and what the node's config file may say.

State is runtime data outside any checkout (``~/.hermes/project-board``, 0700). The config
file is optional: without it the analysis uses the Hermes main model, the analysis cron
stays idle (``analyze.enabled`` false) and the daily digest has no recipients — a
release alone never starts posting cards or mail.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .llm import LlmConfig, llm_config
from .models import DEFAULT_GRADE, GRADES, ProjectBoardError

ROOT_ENV: Final = "PROJECT_BOARD_ROOT"
CONFIG_ENV: Final = "PROJECT_BOARD_CONFIG"
DEFAULT_PORT: Final = 8810
DEFAULT_BRAND: Final = "프로젝트 대시보드"
MAX_BRAND: Final = 40


@dataclass(frozen=True, slots=True)
class ProjectEntry:
    """One known project of the owner: the exact name the model must reuse, and its cues."""

    name: str
    aliases: tuple[str, ...] = ()
    note: str = ""
    grade: str = ""

    def prompt_line(self) -> str:
        aliases = f" (별칭: {', '.join(self.aliases)})" if self.aliases else ""
        note = f" — {self.note}" if self.note else ""
        grade = f" [기본 등급: {self.grade}]" if self.grade else ""
        return f"{self.name}{aliases}{note}{grade}"


@dataclass(frozen=True, slots=True)
class BoardConfig:
    llm: LlmConfig
    analyze_enabled: bool = False
    max_per_tick: int = 5
    analyze_minutes: bool = True
    port: int = DEFAULT_PORT
    digest_to: tuple[str, ...] = ()
    digest_subject: str = "프로젝트 현황 일일 요약"
    projects: tuple[ProjectEntry, ...] = ()
    prompt_version: str = "v4"
    owner_names: tuple[str, ...] = ()
    team_names: tuple[str, ...] = ()
    tailnet_origins: tuple[str, ...] = ()
    tailnet_logins: tuple[str, ...] = ()
    tailnet_token: bool = False
    brand: str = DEFAULT_BRAND
    order_receiver: str = ""
    owner_devices: tuple[tuple[str, str], ...] = ()

    def project_grade(self, project: str) -> str:
        """The configured default grade of a project (과제내부 when the catalog says nothing)."""
        return next((entry.grade for entry in self.projects if entry.name == project and entry.grade), DEFAULT_GRADE)

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
                _project_grade(item.get("grade")),
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
        analyze_minutes=analyze.get("minutes", True) is not False,
        port=port,
        digest_to=tuple(recipients),
        digest_subject=str(digest.get("subject") or "프로젝트 현황 일일 요약"),
        projects=_projects(raw.get("projects")),
        prompt_version=str(analyze.get("prompt") or "v4"),
        owner_names=_owner_names(raw.get("owner")),
        team_names=_owner_names(raw.get("team")),
        tailnet_origins=_tailnet_origins(dashboard.get("tailnet")),
        tailnet_logins=_tailnet_logins(dashboard.get("tailnet")),
        tailnet_token=_tailnet_token(dashboard.get("tailnet")),
        brand=_brand(dashboard.get("brand")),
        order_receiver=_order_receiver(raw.get("orders")),
        owner_devices=_owner_devices(dashboard.get("tailnet")),
    )


def _order_receiver(raw: object) -> str:
    """``orders.receiver``: the Dori that takes dashboard orders, named on the order form (one short line)."""
    if raw is None:
        return ""
    receiver = raw.get("receiver") if isinstance(raw, dict) else None
    if receiver is None:
        return ""
    if not isinstance(receiver, str) or len(" ".join(receiver.split())) > 40:
        raise ProjectBoardError("config orders.receiver must be a name of at most 40 characters")
    return " ".join(receiver.split())


def _brand(raw: object) -> str:
    """The product name shown in the header, tab title and icon; one line, at most 40 characters."""
    if raw in (None, ""):
        return DEFAULT_BRAND
    if not isinstance(raw, str) or not raw.strip():
        raise ProjectBoardError("config dashboard.brand must be a non-empty name")
    name = " ".join(raw.split())
    if len(name) > MAX_BRAND:
        raise ProjectBoardError(f"config dashboard.brand must be at most {MAX_BRAND} characters")
    return name


def _project_grade(raw: object) -> str:
    if raw in (None, ""):
        return ""
    grade = str(raw).strip()
    if grade not in GRADES:
        raise ProjectBoardError(f"config project grade must be one of {', '.join(GRADES)}")
    return grade


def _tailnet_block(raw: object) -> dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ProjectBoardError("config dashboard.tailnet must be an object")
    return raw


def _tailnet_origins(raw: object) -> tuple[str, ...]:
    origins = _tailnet_block(raw).get("origins") or []
    if not isinstance(origins, list) or not all(isinstance(o, str) for o in origins):
        raise ProjectBoardError("config dashboard.tailnet.origins must be https://host[:port] strings")
    normalized = tuple(o.strip().lower() for o in origins)
    if not all(o.startswith("https://") and o[len("https://"):] and "/" not in o[len("https://"):] for o in normalized):
        raise ProjectBoardError("config dashboard.tailnet.origins must be https://host[:port] strings")
    return normalized


_STABLE_ID: Final = re.compile(r"n[A-Za-z0-9]{6,30}CNTRL")


def _owner_devices(raw: object) -> tuple[tuple[str, str], ...]:
    """``dashboard.tailnet.owner_devices``: the owner's own phone and laptops as ``{"id", "name"}``, ``id`` being the
    tailnet stable node id (``tailscale status --json`` Peer.ID). Only an order sent from one of these is the owner's
    command; servers on the same tailnet account are not.
    """
    devices = _tailnet_block(raw).get("owner_devices") or []
    if not isinstance(devices, list):
        raise ProjectBoardError("config dashboard.tailnet.owner_devices must be a list of {id, name}")
    out: list[tuple[str, str]] = []
    for item in devices:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("name", ""), str):
            raise ProjectBoardError("config dashboard.tailnet.owner_devices must be a list of {id, name}")
        if not _STABLE_ID.fullmatch(item["id"].strip()):
            raise ProjectBoardError("config dashboard.tailnet.owner_devices id must be a tailnet stable node id")
        out.append((item["id"].strip(), " ".join(item.get("name", "").split())[:80]))
    return tuple(out)


def _tailnet_token(raw: object) -> bool:
    value = _tailnet_block(raw).get("token", False)
    if not isinstance(value, bool):
        raise ProjectBoardError("config dashboard.tailnet.token must be true or false")
    return value


def _tailnet_logins(raw: object) -> tuple[str, ...]:
    logins = _tailnet_block(raw).get("logins") or []
    if not isinstance(logins, list) or not all(isinstance(x, str) and "@" in x for x in logins):
        raise ProjectBoardError("config dashboard.tailnet.logins must be login names")
    return tuple(x.strip().lower() for x in logins)


def _owner_names(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    names = raw.get("names") if isinstance(raw, dict) else None
    if not isinstance(names, list) or not all(isinstance(name, str) and name.strip() for name in names):
        raise ProjectBoardError("config owner.names and team.names must be lists of names")
    return tuple(" ".join(name.split()) for name in names)
