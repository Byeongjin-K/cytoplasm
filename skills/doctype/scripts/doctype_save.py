from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
try:
    override = getattr(__import__("typing"), "override")
except AttributeError:
    def override(method):
        return method

from automation.drive_client import DriveClient
from automation.interop.effect_approval import ApprovalContext
from automation.obsidian_write import (
    ObsidianWriteConfig,
    ObsidianWriteError,
    load_config,
    plan_note,
    write_note,
)
from skills.doctype.scripts import doctype_extract
from skills.doctype.scripts.doctype_routing import SaveRoute

_OBSIDIAN_CONFIG_ENV = "OBSIDIAN_WRITE_CONFIG"


@dataclass(frozen=True, slots=True)
class DocumentSaveError(Exception):
    destination: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.destination} save failed: {self.detail}"


GitRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class SaveAdapters:
    obsidian_config: ObsidianWriteConfig | None
    drive_client: DriveClient | None = None
    git_runner: GitRunner = subprocess.run
    approval_context: ApprovalContext | None = None


def adapters_from_environment(route: SaveRoute) -> SaveAdapters:
    obsidian_config = _obsidian_config() if "obsidian" in route.destinations else None
    return SaveAdapters(obsidian_config)


def save_from_environment(artifact: Path, route: SaveRoute) -> None:
    save_artifact(artifact, route, adapters_from_environment(route))


def save_artifact(artifact: Path, route: SaveRoute, adapters: SaveAdapters) -> None:
    """Execute every selected adapter or surface one explicit failed destination."""
    if "obsidian" in route.destinations:
        _save_to_obsidian(artifact, adapters)
    if "drive" in route.destinations:
        _save_to_drive(artifact, adapters)


def _obsidian_config() -> ObsidianWriteConfig:
    configured_path = os.environ.get(_OBSIDIAN_CONFIG_ENV)
    return load_config(Path(configured_path).expanduser()) if configured_path else load_config()


def _save_to_obsidian(artifact: Path, adapters: SaveAdapters) -> None:
    config = adapters.obsidian_config
    if config is None:
        raise DocumentSaveError("obsidian", "adapter configuration is missing")
    try:
        source = doctype_extract.read_document(artifact)
        plan = plan_note(artifact.stem, source.text, institutional=False, bucket_hint="resource")
        _ = write_note(
            plan,
            config,
            adapters.git_runner,
            approval_context=adapters.approval_context,
        )
    except (ObsidianWriteError, doctype_extract.ExtractionError, OSError) as error:
        raise DocumentSaveError("obsidian", str(error)) from error


def _save_to_drive(artifact: Path, adapters: SaveAdapters) -> None:
    """Strictly publish a routing-selected Drive document through the facade."""
    try:
        from automation.drive_outputs import publish

        if adapters.drive_client is None:
            _ = publish("doctype", artifact.stem, [(artifact, artifact.stem)])
        else:
            _ = publish(
                "doctype", artifact.stem, [(artifact, artifact.stem)], client=adapters.drive_client
            )
    except Exception as error:
        raise DocumentSaveError("drive", str(error)) from error
