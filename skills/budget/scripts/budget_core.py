"""Pure logic for the budget skill (W4-3): balance-tab parsing/validation,
snapshot canonicalization + diff, regulation request-mail rendering, masked
approval-request rendering, gws gmail argv building, and gate-parity action hashing.

No I/O, no subprocess, no network — everything here is pytest-able.
Sheet coordinates are supplied through private runtime configuration.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Final, NotRequired, TypeAlias, TypedDict

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = "JsonScalar | list[JsonValue] | dict[str, JsonValue]"
JsonObject: TypeAlias = "dict[str, JsonValue]"

class BudgetCardDraft(TypedDict):
    id: str
    sha256: str
    changes: list[list[str]]
    prev_hash: str
    new_hash: str
    claim_key: NotRequired[str]
    project: NotRequired[str]
    year: NotRequired[int]
    created: NotRequired[str]
    render_version: NotRequired[str]
    message_id: NotRequired[str]
    approval_thread_id: NotRequired[str]
    approval_guild_id: NotRequired[str]
    origin_channel_id: NotRequired[str]
    origin_message_id: NotRequired[str]
    kind: NotRequired[str]
    surface: NotRequired[str]
    channel_id: NotRequired[str]
    policy_version: NotRequired[int]


BUDGET_APPROVAL_TTL: Final = timedelta(hours=24)

KST = timezone(timedelta(hours=9), "KST")
BALANCE_TAB = "항목별 잔액"
BALANCE_READ_RANGE = f"{BALANCE_TAB}!A1:E200"
HEADER_ROW_INDEX = 5  # row 6 (1-based) — configs/budget-sheet.md balance_header_range
DATA_START_INDEX = 6  # row 7 (1-based) — balance_data_start_row
HEADER_EXPECTED = ("항목", "예산", "집행액", "잔액", "최종수정")
FIELDS = HEADER_EXPECTED[1:]
EXTERNAL_EFFECT_TARGET_ID = "tool:gws_gmail_send:gws"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


class SheetSchemaError(ValueError):
    """Balance tab does not match the fixed W0-10 schema — diff must stop."""


@dataclass(frozen=True, slots=True)
class Change:
    """One detected balance-tab change (item x field, old -> new)."""

    item: str
    field: str
    old: str
    new: str


def first_json(text: str) -> JsonObject:
    """Extract the first JSON object from mixed CLI output (gws banner lines)."""
    decoder = json.JSONDecoder()
    for offset, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise SheetSchemaError("응답에서 JSON 객체를 찾지 못했습니다")


def parse_balance_payload(raw: str) -> list[list[str]]:
    """Parse a gws `sheets +read` payload into the raw values grid."""
    payload = first_json(raw)
    values = payload.get("values")
    if not isinstance(values, list):
        raise SheetSchemaError("values 배열이 없습니다")
    return [[str(cell) for cell in row] for row in values if isinstance(row, list)]


def validate_header(values: list[list[str]]) -> None:
    """Enforce the fixed header contract before any diff (W0-10 rule)."""
    if len(values) <= HEADER_ROW_INDEX:
        raise SheetSchemaError(f"헤더 행(6행)이 없습니다 (행 수={len(values)})")
    header = tuple(values[HEADER_ROW_INDEX][: len(HEADER_EXPECTED)])
    if header != HEADER_EXPECTED:
        raise SheetSchemaError(
            f"헤더 불일치: 기대={','.join(HEADER_EXPECTED)} 실제={','.join(header)}"
        )


def data_rows(values: list[list[str]]) -> list[tuple[str, ...]]:
    """Rows from row 7 down, padded to 5 columns; fully-empty rows dropped."""
    rows: list[tuple[str, ...]] = []
    for row in values[DATA_START_INDEX:]:
        padded = tuple((row + [""] * len(HEADER_EXPECTED))[: len(HEADER_EXPECTED)])
        if any(cell.strip() for cell in padded):
            rows.append(padded)
    return rows


def snapshot_hash(rows: list[tuple[str, ...]]) -> str:
    canonical = json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def claim_key(prev_hash: str, new_hash: str, *, sheet_key: str = "") -> str:
    key = f"{prev_hash[:16]}->{new_hash[:16]}"
    return f"{sheet_key}:{key}" if sheet_key else key


def diff_rows(old: list[tuple[str, ...]], new: list[tuple[str, ...]]) -> list[Change]:
    """Item-keyed field diff; added/removed rows surface as (신규)/(삭제)."""
    old_map = {row[0]: row for row in old}
    new_map = {row[0]: row for row in new}
    changes: list[Change] = []
    for item, row in new_map.items():
        if item not in old_map:
            changes.append(Change(item, "(신규 항목)", "", " / ".join(row[1:])))
            continue
        before = old_map[item]
        changes.extend(
            Change(item, field, before[index + 1], row[index + 1])
            for index, field in enumerate(FIELDS)
            if before[index + 1] != row[index + 1]
        )
    changes.extend(
        Change(item, "(항목 삭제)", " / ".join(row[1:]), "")
        for item, row in old_map.items()
        if item not in new_map
    )
    return changes


def mask_value(value: str) -> str:
    """Deterministic opaque token — figures never appear in Discord/QA text."""
    if not value:
        return "[없음]"
    return f"[MASKED-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:6]}]"


def redact(text: str) -> str:
    """Mask emails and long digit runs in error/report lines."""
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def render_mail(
    changes: list[Change], *, prev_hash: str, new_hash: str, now: datetime, context: str = ""
) -> tuple[str, str]:
    """Regulation-compliant request mail (real values; stays in 600 draft/mail)."""
    when = now.astimezone(KST)
    scope = f" {context}" if context else ""
    subject = f"[과제비]{scope} 원장 변경 통지 및 처리 요청 ({when.strftime('%Y-%m-%d')})"
    lines = [
        "과제비 원장(항목별 잔액 탭)에서 아래 변경이 감지되어 과제비 운영 규칙에 따라 통지드리며,",
        "해당 변경 내역의 확인 및 필요한 행정 처리를 요청드립니다.",
        "",
        "1. 감지된 변경 내역",
    ]
    if context:
        lines.append(f"   - 대상 과제/년도: {context}")
    lines.extend(
        f"   - {change.item} / {change.field}: {change.old or '(없음)'} → {change.new or '(없음)'}"
        for change in changes
    )
    lines.extend([
        "",
        "2. 근거 규정 (과제비 운영 규칙, W0-10)",
        "   - 원장 Sheet가 유일한 진실(Single Source of Truth)이며 값 수정 권한은 오너(cha) 본인에게 있음.",
        "   - 기관 시스템과의 대사는 월 1회 수행하며, 변경 발생 시 본 통지 메일로 처리를 요청함.",
        "",
        "3. 본 메일은 소유자의 명시적 승인(✅) 이후에만 발송되었습니다.",
        f"   - 감지 시각: {when.strftime('%Y-%m-%d %H:%M %Z')}",
        f"   - 스냅샷: {prev_hash[:12]} → {new_hash[:12]}",
    ])
    return subject, "\n".join(lines)


def _render_v1(draft: BudgetCardDraft, *, instruction: str = "") -> str:
    """Sanitized approval request: item/field visible, every value masked.

    ``instruction`` is the owner-facing reaction line and MUST come from
    ``approval_surface.reaction_instruction`` — a draft rendered before its
    surface is known simply omits it rather than guessing one.
    """
    changes = [Change(*entry) for entry in draft["changes"]]
    lines = ["[budget-mail] 과제비 변경 감지 — 요청 메일 발송 승인 요청"]
    project = draft.get("project")
    if isinstance(project, str) and project:
        lines.append(f"- 과제: {project} ({draft.get('year')}년)")
    lines.append(f"- 변경 {len(changes)}건 (금액은 마스킹 — 원문은 `!budget` 조회):")
    lines.extend(
        f"  - {change.item} / {change.field}: {mask_value(change.old)} → {mask_value(change.new)}"
        for change in changes
    )
    lines.append(f"- 스냅샷: `{draft['prev_hash'][:12]}` → `{draft['new_hash'][:12]}`")
    lines.append(f"- draft: `{draft['id']}` sha256: `{draft['sha256']}`")
    if instruction:
        lines.append(f"- 반응(기본): {instruction}")
    lines.append(
        f"- 텍스트 대체: `실행/취소 {draft['id']}` — 반응 사용이 기본이며,"
        " 확정 시 다음 30분 tick에 발송"
    )
    return "\n".join(lines)


def render_approvals_message(draft: BudgetCardDraft, *, instruction: str = "") -> str:
    """Replay v1/v2 bytes; v3 selects multiline facts and the owner-ko-v2 envelope."""
    version = draft.get("render_version", "1")
    if version == "1":
        return _render_v1(draft, instruction=instruction)
    from automation.interop.approval_card import CardRenderError
    if version not in ("2", "3"):
        raise CardRenderError("unknown budget card render version")
    from automation.interop import owner_message
    from automation.interop.approval_surface import ApprovalKind, reaction_instruction, required_surface
    if not callable(getattr(owner_message, "render", None)):
        raise CardRenderError("owner envelope unavailable")
    here = owner_message.Ref(scope="self")
    kind = ApprovalKind.BUDGET_MAIL
    created = draft.get("created")
    expiry = None if created is None else datetime.fromisoformat(created) + BUDGET_APPROVAL_TTL
    message = owner_message.OwnerMessage(
        subject_key=str(draft["id"]), subject="예산 변경 메일",
        fact=("\n" if version == "3" else " · ").join(_render_v1(draft).splitlines()[1:-2]), location=here,
        owner=owner_message.Action("react", here, reaction_instruction(kind, required_surface(kind))),
        agent_next="승인 시 다음 30분 tick에 발송", recovery="irreversible",
        detail=owner_message.Approval(expiry, "메일을 발송하지 않음"),
        render_version="owner-ko-v2" if version == "3" else "owner-ko-v1",
    )
    try:
        body = owner_message.render(message, destination=here)
    except owner_message.OwnerMessageError as error:
        raise CardRenderError("budget envelope cannot render") from error
    return f"{body}\n- draft: `{draft['id']}` sha256: `{draft['sha256']}`"


def build_gmail_argv(to: str, subject: str, body: str) -> tuple[str, ...]:
    return ("gws", "gmail", "+send", "--to", to, "--subject", subject, "--body", body)


def external_effect_action_hash(argv: tuple[str, ...]) -> str:
    """Hash-parity with automation.interop.effect_approval.action_hash.

    Canonical ToolCall: tool_name="gws", arguments={"command": shlex.join(argv)}
    — the binding the deployed pre_tool_call gate computes for a terminal
    `gws gmail +send …` call (rule id gws_gmail_send).
    """
    payload = {
        "action": "external_effect.tool_call",
        "arguments": {"command": shlex.join(argv)},
        "target_id": EXTERNAL_EFFECT_TARGET_ID,
        "tool_name": "gws",
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def draft_sha256(record: dict) -> str:
    """Content hash binding a draft to the exact mail it will send."""
    bound = {
        key: record[key]
        for key in ("argv", "changes", "claim_key", "mail_to", "new_hash", "prev_hash", "subject")
    }
    canonical = json.dumps(bound, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
