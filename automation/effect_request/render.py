"""Approval cards for generic effect requests (owner envelope, append-only versions)."""

from __future__ import annotations

import shlex
from typing import Final

from .model import DRIVE_SAVE, EffectRequest

RENDER_VERSION: Final = "effect-request-v1"
#: Several files or a folder id in one Drive save card (issue #241); v1 card bytes stay frozen.
RENDER_VERSION_BATCH: Final = "effect-request-v2"
#: A folder tree from a staged manifest on one card, its full list in messages below it (2026-11-23).
RENDER_VERSION_TREE: Final = "effect-request-v3"
MAX_MESSAGE_CHARS: Final = 1900
_TREE_FOLDERS_SHOWN: Final = 12
#: Characters the folder lines of a tree card may use, tried in order until the whole card fits;
#: the full paths are always in the list messages.
_TREE_FOLDER_BUDGETS: Final = (600, 300, 0)
#: List messages one tree card may bring; a longer list floods the thread, so the job is split.
MAX_LIST_MESSAGES: Final = 40
_TREE_FOLDER_NAME: Final = 50
_TREE_PLACE_NAME: Final = 60
_TREE_PLACE_PATH: Final = 120
_COMMAND_LIMIT: Final = 700
_NAME_LIMIT: Final = 60
_SHA_SHOWN: Final = 12


class RenderError(ValueError):
    pass


def version_for(record: EffectRequest) -> str:
    """The card version a record is posted and replayed with: v2 for a batch or folder payload, v3 for a tree."""
    version = record.payload.get("version")
    return {2: RENDER_VERSION_BATCH, 3: RENDER_VERSION_TREE}.get(version, RENDER_VERSION)  # type: ignore[arg-type]


def drive_location(payload: dict[str, object]) -> str:
    if payload.get("version") in (2, 3):
        if payload.get("version") == 3 or payload.get("mode") == "folder":
            return "/".join(str(part) for part in payload.get("folder_path", ()))  # type: ignore[union-attr]
        return "/".join(str(part) for part in payload.get("folder_parts", ()))  # type: ignore[union-attr]
    parts = [str(part) for part in payload.get("folder_parts", ())]
    return "/".join((*parts, str(payload.get("drive_name", ""))))


def _size(size: object) -> str:
    value = int(size) if isinstance(size, int) else 0
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def _drive_fact(record: EffectRequest) -> tuple[str, str, str, str]:
    payload = record.payload
    fact = (
        f"할 일: {record.summary}\n"
        f"파일 이름: {payload.get('drive_name')}\n"
        f"Drive 위치: {drive_location(payload)}\n"
        f"공유 범위: 비공개(형 계정만 볼 수 있음) — 다른 사람에게 보이지 않는다\n"
        f"크기: {_size(payload.get('size'))} · sha256: {payload.get('sha256')}\n"
        "같은 이름 파일이 그 폴더에 이미 있으면 그 파일을 새 내용으로 갱신한다"
    )
    return (
        "Drive 저장 승인 요청", fact, "✅ 저장 / ⛔ 취소",
        "✅ 뒤 이 파일을 이 위치에 비공개로 올리고, 다시 내려받아 sha256 이 같은지 확인한 뒤 링크를 남긴다",
    )


def _batch_fact(record: EffectRequest) -> tuple[str, str, str, str]:
    payload = record.payload
    files = [item for item in payload.get("files", ()) if isinstance(item, dict)]  # type: ignore[union-attr]
    total = sum(int(item.get("size", 0)) for item in files)
    lines = []
    for index, item in enumerate(files, start=1):
        name = str(item.get("drive_name"))
        if len(name) > _NAME_LIMIT:
            name = name[: _NAME_LIMIT - 1] + "…"
        lines.append(f"{index}. {name} · {_size(item.get('size'))} · sha256 {str(item.get('sha256'))[:_SHA_SHOWN]}…")
    if payload.get("mode") == "folder":
        place = (f"저장 폴더: {payload.get('folder_name')} (경로 {drive_location(payload)}, "
                 f"id {payload.get('folder_id')})")
    else:
        place = f"Drive 위치: {drive_location(payload)}"
    fact = (
        f"할 일: {record.summary}\n"
        f"파일 {len(files)}개(합계 {_size(total)}) — 이 목록 전체가 승인 해시에 묶인다:\n"
        + "\n".join(lines) + "\n"
        f"{place}\n"
        "공유 범위: 비공개(형 계정만 볼 수 있음) — 다른 사람에게 보이지 않는다\n"
        "같은 이름 파일이 그 폴더에 이미 있으면 그 파일을 새 내용으로 갱신한다"
    )
    return (
        "Drive 저장 승인 요청", fact, "✅ 모두 저장 / ⛔ 취소",
        "✅ 뒤 파일마다 비공개로 올리고 다시 내려받아 sha256 을 확인한다. 일부가 실패하면 나머지는 올리고 "
        "파일별 결과를 이 스레드에 남기며, 실패한 파일만 같은 승인으로 다시 시도한다",
    )


def _tree_groups(record: EffectRequest) -> list[tuple[str, list[str]]]:
    """Files under their folder, in folder order (parents first); each long path is written once."""
    files = [item for item in record.payload.get("files", ()) if isinstance(item, dict)]  # type: ignore[union-attr]
    order = ["", *(str(folder) for folder in record.payload.get("folders", ()))]  # type: ignore[union-attr]
    groups: list[tuple[str, list[str]]] = []
    for folder in order:
        lines = [f"- {item.get('drive_name')} · {_size(item.get('size'))} · sha256 {str(item.get('sha256'))[:_SHA_SHOWN]}…"
                 for item in files if item.get("folder") == folder]
        if lines:
            groups.append((folder, lines))
    return groups


def render_effect_list(record: EffectRequest) -> tuple[str, ...]:
    """The full file list of a tree card as plain messages under it; empty for every other card."""
    if version_for(record) != RENDER_VERSION_TREE:
        return ()
    chunks: list[list[str]] = [[]]
    used = 0
    budget = MAX_MESSAGE_CHARS - 80
    for folder, lines in _tree_groups(record):
        header = f"📁 {folder or '(저장 폴더 바로 아래)'}/"
        for number, line in enumerate(lines):
            first = number == 0 or not chunks[-1]
            cost = len(line) + 1 + (len(header) + 1 if first else 0)
            if chunks[-1] and used + cost > budget:
                chunks.append([])
                used, first = 0, True
                cost = len(line) + len(header) + 2
            if first:
                chunks[-1].append(header)
            chunks[-1].append(line)
            used += cost
    return tuple(f"목록 {number}/{len(chunks)} · 요청 {record.request_id}\n" + "\n".join(lines)
                 for number, lines in enumerate(chunks, start=1))


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


def _tree_fact(record: EffectRequest, folder_chars: int) -> tuple[str, str, str, str]:
    payload = record.payload
    files = [item for item in payload.get("files", ()) if isinstance(item, dict)]  # type: ignore[union-attr]
    folders = [str(folder) for folder in payload.get("folders", ())]  # type: ignore[union-attr]
    existing = set(payload.get("existing_folders", ()))  # type: ignore[arg-type]
    counts = {folder: sum(1 for item in files if item.get("folder") == folder) for folder in folders}
    shown: list[str] = []
    used = 0
    for folder in folders[:_TREE_FOLDERS_SHOWN]:
        name = folder if len(folder) <= _TREE_FOLDER_NAME else "…" + folder[-(_TREE_FOLDER_NAME - 1):]
        line = f"- {name}/ (파일 {counts[folder]}개{', 있는 폴더' if folder in existing else ''})"
        if used + len(line) > folder_chars:
            break
        shown.append(line)
        used += len(line) + 1
    if len(shown) < len(folders):
        shown.append(f"- …외 {len(folders) - len(shown)}개(전체 경로는 아래 목록)")
    top = sum(1 for item in files if not item.get("folder"))
    unchanged = len(payload.get("unchanged", ()))  # type: ignore[arg-type]
    overwrite = len(payload.get("overwrite", ()))  # type: ignore[arg-type]
    fact = (
        f"할 일: {record.summary}\n"
        f"저장 폴더: {_tail(str(payload.get('folder_name')), _TREE_PLACE_NAME)} "
        f"(경로 {_tail(drive_location(payload), _TREE_PLACE_PATH)}, id {payload.get('folder_id')}) 아래\n"
        f"폴더 {len(folders)}개(새로 만들 폴더 {len(folders) - len(existing)}개 — ✅ 뒤 위 폴더부터 차례로 만든다)"
        + (f", 바로 아래 파일 {top}개" if top else "") + ":\n"
        + "\n".join(shown) + ("\n" if shown else "")
        + f"파일 {len(files)}개(합계 {_size(sum(int(item.get('size', 0)) for item in files))}) — "
        f"이 목록 전체(경로·크기·sha256)가 승인 해시에 묶인다, 목록 sha256 {str(payload.get('manifest_sha256'))[:16]}…\n"
        f"전체 목록: 이 카드 아래 글 {len(render_effect_list(record))}개\n"
        + (f"이미 같은 내용으로 있어 건너뛰는 파일 {unchanged}개\n" if unchanged else "")
        + (f"같은 이름의 다른 파일이 있어 이 카드에서 뺀 파일 {overwrite}개 — 덮어쓰기는 따로 카드\n"
           if overwrite else "")
        + "공유 범위: 비공개(형 계정만 볼 수 있음) — 다른 사람에게 보이지 않는다\n"
        "이미 있는 파일은 덮어쓰지 않고, 목록 밖 파일은 올리지 않는다"
    )
    return (
        "Drive 폴더 저장 승인 요청", fact, "✅ 모두 저장 / ⛔ 취소",
        "✅ 뒤 폴더를 만들고 파일마다 비공개로 올려 다시 내려받아 sha256 을 확인한다. 일부가 실패하면 나머지는 올리고 "
        "파일별 결과를 남기며, 실패한 파일만 같은 승인으로 다시 시도한다",
    )


def _command_fact(record: EffectRequest) -> tuple[str, str, str, str]:
    payload = record.payload
    command = shlex.join(tuple(str(item) for item in payload.get("argv", ())))
    if len(command) > _COMMAND_LIMIT:
        command = command[:_COMMAND_LIMIT] + f" …(전체 {len(command)}자, 해시에 전부 묶임)"
    fact = (
        f"할 일: {record.summary}\n"
        f"영향 대상: {payload.get('target')}\n"
        f"실행 명령(작업 폴더 {payload.get('cwd')}):\n```\n{command}\n```\n"
        f"제한 시간: {payload.get('timeout')}초 · 한 번만 실행하고 실패해도 다시 실행하지 않는다"
    )
    return (
        "외부 작업 승인 요청", fact, "✅ 실행 / ⛔ 취소",
        "✅ 뒤 이 명령을 그대로 한 번 실행하고 종료 코드와 출력 끝부분(가린 것)을 이 스레드에 남긴다",
    )


def render_effect_approval(record: EffectRequest, *, render_version: str | None = None) -> str:
    expected = version_for(record)
    if render_version not in (None, expected):
        raise RenderError(f"unsupported effect render version: {render_version}")
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise RenderError("owner envelope unavailable") from error
    if record.effect == DRIVE_SAVE and expected == RENDER_VERSION_TREE:
        for budget in _TREE_FOLDER_BUDGETS:
            content = _envelope(record, expected, _tree_fact(record, budget), om)
            if len(content) <= MAX_MESSAGE_CHARS:
                return content
        raise RenderError("effect approval exceeds the postable length")
    if record.effect != DRIVE_SAVE:
        parts = _command_fact(record)
    elif expected == RENDER_VERSION_BATCH:
        parts = _batch_fact(record)
    else:
        parts = _drive_fact(record)
    content = _envelope(record, expected, parts, om)
    if len(content) > MAX_MESSAGE_CHARS:
        raise RenderError("effect approval exceeds the postable length")
    return content


def _envelope(record: EffectRequest, expected: str, parts: tuple[str, str, str, str], om: object) -> str:
    subject, fact, verbs, next_step = parts
    here = om.Ref(scope="self")  # type: ignore[attr-defined]
    message = om.OwnerMessage(
        subject_key=record.request_id,
        subject=subject,
        fact=f"{fact}\n판본: {expected}",
        location=here,
        owner=om.Action("react", here, verbs),
        agent_next=next_step,
        recovery="not_applicable",
        detail=om.Approval(None, "아무것도 실행하지 않는다"),  # type: ignore[attr-defined]
        render_version="owner-ko-v2",
    )
    try:
        lines = om.render(message, destination=here).splitlines()  # type: ignore[attr-defined]
    except om.OwnerMessageError as error:  # type: ignore[attr-defined]
        raise RenderError("owner envelope cannot render") from error
    return "\n".join((*lines[:-1], f"- action_hash: `{record.action_hash}`", lines[-1]))
