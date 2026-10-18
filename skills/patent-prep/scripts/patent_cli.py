#!/usr/bin/env python3
"""Private command surface for patent-prep workspaces."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(_SCRIPT_DIR.parent))
    __package__ = "scripts"

from . import patent_core  # noqa: E402
from .patent_llm import LlmInvocationError  # noqa: E402
from .patent_routing import plan_patent_call  # noqa: E402
from .patent_storage import ChecklistState, PatentPaths, PatentStorageError, load_progress, workspace, write_draft  # noqa: E402
from . import patent_export  # noqa: E402
from . import patent_export_gate  # noqa: E402
from . import patent_export_manifest  # noqa: E402
from . import patent_prep_governed  # noqa: E402


def _paths() -> PatentPaths:
    """Resolve production roots from the environment."""
    return PatentPaths.from_environment()


def _create(args: argparse.Namespace) -> int:
    progress = patent_core.create_disclosure(_paths(), args.slug)
    print(f"PATENT-CREATED slug={progress.slug} percent={progress.percent_complete}")
    return 0


def _checklist(args: argparse.Namespace) -> int:
    state = ChecklistState(args.state)
    progress = patent_core.update_checklist(_paths(), args.slug, state)
    print(f"CHECKLIST-UPDATED slug={progress.slug} state={progress.checklist_state.value} percent={progress.percent_complete}")
    return 0


def _draft(args: argparse.Namespace) -> int:
    paths = _paths()
    if args.response_file:
        call = plan_patent_call(())
        content = Path(args.response_file).expanduser().read_text(encoding="utf-8")
        progress = write_draft(paths, args.slug, content)
        output = workspace(paths, args.slug) / "draft.md"
    else:
        drafted = patent_core.draft_disclosure(paths, args.slug, Path(args.brief_file))
        call = drafted.response.call
        progress = drafted.progress
        output = drafted.path
    print(
        "PATENT-DRAFTED "
        f"slug={progress.slug} path={output} provider={call.provider} model={call.model} "
        f"percent={progress.percent_complete}"
    )
    return 0


def _status(args: argparse.Namespace) -> int:
    path = load_progress(_paths(), args.slug)
    print(
        f'{{"checklist_state":"{path.checklist_state.value}","percent_complete":{path.percent_complete},"slug":"{path.slug}"}}'
    )
    return 0


def _export_prepare(args: argparse.Namespace) -> int:
    mode = "plaintext" if args.allow_plaintext else "enc"
    res = patent_export.prepare_export(_paths(), args.slug, mode=mode)
    print(res)
    return 0

def _export_execute(args: argparse.Namespace) -> int:
    res = patent_export.execute_export(_paths(), args.slug)
    print(res)
    return 0

def _export_now(args: argparse.Namespace) -> int:
    print(patent_export.export_owner_command(
        _paths(), args.slug, mode=args.mode,
        channel_id=args.origin_channel_id, message_id=args.origin_message_id,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the no-content-on-stdout command surface."""
    parser = argparse.ArgumentParser(prog="patent-prep")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--slug", required=True)
    create.set_defaults(func=_create)
    checklist = commands.add_parser("checklist")
    checklist.add_argument("--slug", required=True)
    checklist.add_argument("--state", choices=tuple(item.value for item in ChecklistState), required=True)
    checklist.set_defaults(func=_checklist)
    draft = commands.add_parser("draft")
    draft.add_argument("--slug", required=True)
    source = draft.add_mutually_exclusive_group(required=True)
    source.add_argument("--brief-file")
    source.add_argument("--response-file")
    draft.set_defaults(func=_draft)
    status = commands.add_parser("status")
    status.add_argument("--slug", required=True)
    status.set_defaults(func=_status)
    export_prepare = commands.add_parser("export-prepare")
    export_prepare.add_argument("--slug", required=True)
    export_prepare.add_argument("--allow-plaintext", action="store_true")
    export_prepare.set_defaults(func=_export_prepare)
    export_execute = commands.add_parser("export-execute")
    export_execute.add_argument("--slug", required=True)
    export_execute.set_defaults(func=_export_execute)
    export_now = commands.add_parser("export-now")
    export_now.add_argument("--slug", required=True)
    export_now.add_argument("--mode", choices=("enc", "plaintext"), default="enc")
    export_now.add_argument("--origin-channel-id", required=True)
    export_now.add_argument("--origin-message-id", required=True)
    export_now.set_defaults(func=_export_now)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI without echoing private material on failure."""
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"create", "checklist", "draft", "export-prepare", "export-execute", "export-now"}:
            message = patent_prep_governed.refusal(Path(__file__))
            if message:
                print(message, file=sys.stderr)
                return 3
        return int(args.func(args))
    except (
        OSError,
        PatentStorageError,
        patent_core.PatentWorkflowError,
        LlmInvocationError,
        patent_export.PatentExportError,
        patent_export_gate.ExportGateError,
        patent_export_manifest.ManifestError,
    ):
        print("PATENT-PREP-REFUSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
