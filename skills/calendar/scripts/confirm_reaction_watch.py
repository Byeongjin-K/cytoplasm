#!/usr/bin/env python3
"""Poll owner-only reactions on pending standalone calendar confirmation DMs."""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Final, Protocol, assert_never
from urllib.error import HTTPError, URLError

_ENV_SECRETS: Final = Path.home() / ".env.secrets"

# 마운트 판정은 governed live 정의 하나(automation/skill_mount.py)에서만 온다. 노드에서
# 이 워처는 ~/.hermes/scripts/ 에 평평하게 배포되므로 코드 루트를 값으로 되짚는다 —
# 체크아웃 → 릴리스 current → 미러 (test_skill_runtime_root_fallback.py 와 같은 관용구).
for _root in (
    *Path(__file__).resolve().parents,
    Path(os.environ.get("AUTOPHAGY_RUNTIME_ROOT") or "/srv/autophagy-agent-current"),
    Path("/srv/autophagy-agents"),
):
    if (_root / "automation" / "skill_mount.py").is_file():
        sys.path.insert(0, str(_root))
        break
from automation.skill_mount import skill_scripts  # noqa: E402 — 코드 루트 확정 뒤에만 가능하다
from automation import owner_notice  # noqa: E402 — 같은 이유: 코드 루트가 정해진 뒤에만 import 된다


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron hands the wrapper no secrets, so the parent loads them itself.

    This watcher reads Discord reactions in-process and spawns `calendar_cli.py`, both
    of which need credentials. Measured 2026-08-18 on `budget-watch`: without this the
    configuration sits on disk and never reaches the code that needs it (규약 (b)).
    Runs at import time because the module-level `import_module` calls below already
    touch configuration. Inventory check: tests/unit/test_watcher_secret_propagation.py.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_env_secrets()

_SCRIPTS = skill_scripts("calendar", env_var="CALENDAR_SCRIPTS")
if _SCRIPTS.exists() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

calendar_confirm = import_module("calendar_confirm")
calendar_approval = import_module("calendar_approval")
calendar_gate = import_module("calendar_gate")
_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm
PendingConfirmStore = _pending.PendingConfirmStore
_commands = import_module("calendar_watch_commands")
_diagnostics = import_module("calendar_watch_diagnostics")
subprocess = _commands.subprocess
CliCommands = _commands.CliCommands
ConfirmBatchError = _diagnostics.ConfirmBatchError
ConfirmWatchError = _diagnostics.ConfirmWatchError
_log_failure = _diagnostics.log_failure
_redact = _diagnostics.redact
_transport_failure = _diagnostics.transport_failure

APPROVE_EMOJI = "\u2705"
CANCEL_EMOJI = "\u26d4"
EXPIRY = timedelta(hours=24)
#: 에이전트 자신의 `post-confirm` 이 이기도록 두는 시간. 정상 경로는 초안을 만든 그 턴에서
#: 곧바로 이어지므로 초 단위로 끝난다 — 이 유예를 넘겼다는 것은 그 턴이 2단계에 도달하지
#: 못했다는 뜻이고, 그때부터는 이 워처가 대신 카드를 올린다.
POST_GRACE = timedelta(minutes=3)
class DiscordClient(Protocol):
    def message_content(self, entry: PendingConfirm) -> str | None: ...

    def reaction_users(self, entry: PendingConfirm, emoji: str) -> tuple[Mapping[str, str | bool], ...]: ...

    def post_message(self, channel_id: str, content: str) -> None: ...

    def fetch_channel(self, channel_id: str) -> object: ...

    def post_reminder_message(self, channel_id: str, content: str) -> str: ...

    def edit_message(self, channel_id: str, message_id: str, content: str) -> None: ...


class CommandRunner(Protocol):
    def confirm(self, entry: PendingConfirm, owner_id: str) -> None: ...

    def discard(self, draft_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DiscordApi:
    owner_id: str

    def message_content(self, entry: PendingConfirm) -> str | None:
        """Read the posted confirmation DM before accepting its reaction."""
        try:
            return calendar_confirm.confirmation_message_content(entry)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise _transport_failure("confirmation DM read failed", "discord.message", error) from error
        except (calendar_gate.GateError, URLError, OSError) as error:
            raise _transport_failure("confirmation DM read failed", "discord.message", error) from error

    def reaction_users(self, entry: PendingConfirm, emoji: str) -> tuple[Mapping[str, str | bool], ...]:
        """Read all reaction users; unavailable reactions are an empty set."""
        try:
            return calendar_confirm.confirmation_reaction_users(entry, emoji)
        except (calendar_gate.GateError, HTTPError, URLError, OSError) as error:
            raise _transport_failure("reaction query failed", "discord.reactions", error) from error

    def post_message(self, channel_id: str, content: str) -> None:
        """Post a reminder to the channel authorized by the shared policy."""
        self.post_reminder_message(channel_id, content)

    def post_reminder_message(self, channel_id: str, content: str) -> str:
        """Post a reminder and return its id (a digest thread may edit it later)."""
        try:
            return calendar_confirm.post_message(channel_id, content)
        except (calendar_gate.GateError, HTTPError, URLError, OSError) as error:
            raise _transport_failure("reminder post failed", "discord.notify", error) from error

    def edit_message(self, channel_id: str, message_id: str, content: str) -> None:
        try:
            calendar_confirm.edit_message(channel_id, message_id, content)
        except (calendar_gate.GateError, HTTPError, URLError, OSError) as error:
            raise _transport_failure("reminder edit failed", "discord.notify", error) from error

    def fetch_channel(self, channel_id: str) -> object:
        """Read channel metadata for the reminder's server-aware source link."""
        try:
            return calendar_confirm.fetch_channel(channel_id)
        except (calendar_gate.GateError, HTTPError, URLError, OSError) as error:
            raise _transport_failure("channel query failed", "discord.channel", error) from error


_ACTION_LABELS: Final = {"create": "등록", "update": "수정", "delete": "삭제"}


def _load_draft_record(draft_id: str) -> dict:
    """Read the pending draft for result routing; an unreadable draft means 'no origin'."""
    try:
        record = calendar_gate.load_draft(draft_id)
    except (calendar_gate.GateError, OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _action_label(record: Mapping[str, object]) -> str:
    return _ACTION_LABELS.get(str(record.get("action", "")), "변경")


def _executed_notice(record: Mapping[str, object], draft_id: str) -> str:
    """Result wording carrying only the action kind and draft id (SKILL.md 반출 금지)."""
    return f"✅ 캘린더 {_action_label(record)} 실행 완료 (draft {draft_id}) — 소유자 ✅ 승인"


def _cancelled_notice(record: Mapping[str, object], draft_id: str) -> str:
    return (
        f"⛔ 캘린더 {_action_label(record)} 취소 (draft {draft_id}) — "
        "소유자 ⛔ 리액션으로 취소되었습니다."
    )


def _expired_notice(record: Mapping[str, object], draft_id: str) -> str:
    return (
        f"⌛ 캘린더 {_action_label(record)} 만료 취소 (draft {draft_id}) — "
        "확정 시간이 지나 취소되었습니다."
    )


def _failed_notice(record: Mapping[str, object], draft_id: str) -> str:
    return (
        f"⚠️ 캘린더 {_action_label(record)} 실행 실패 (draft {draft_id}) — "
        "승인은 그대로 두고 다음 감시 틱에 다시 시도합니다."
    )


def _orphan_notice(record: Mapping[str, object], draft_id: str) -> str:
    return (
        f"🧹 캘린더 {_action_label(record)} 초안 자동 정리 (draft {draft_id}) — "
        "승인 요청 DM이 게시되지 않은 채 24시간이 지나 폐기했습니다. 필요하면 다시 요청해 주세요."
    )


@dataclass(frozen=True, slots=True)
class OwnerDecision:
    entry: PendingConfirm
    owner_id: str
    discord: DiscordClient
    commands: CommandRunner
    draft_sha256: Callable[[str], str]
    store: PendingConfirmStore
    draft_record: Callable[[str], dict] = _load_draft_record

    def probe(self, _request):
        if self.draft_sha256(self.entry.draft_id) != self.entry.sha256:
            raise ConfirmWatchError("draft hash mismatch")
        return calendar_approval.probe_entry(self.entry, self.owner_id, self.discord)

    def apply(self, _request, decision) -> None:
        state = calendar_approval.lifecycle().Probe
        record = {**self.draft_record(self.entry.draft_id), "dm_message_id": self.entry.dm_message_id}
        match decision:
            case state.APPROVED:
                try:
                    self.commands.confirm(self.entry, self.owner_id)
                except ConfirmWatchError:
                    # 실패도 그 카드 아래(스레드)로 알린다 — 항목은 남겨 다음 틱에 재시도한다.
                    if record.get("digest_day"):
                        _notify_result(
                            self.discord, record, _failed_notice(record, self.entry.draft_id),
                            calendar_confirm.OUTCOME_FAILED,
                        )
                    raise
                # 승인 스레드(또는 채널 지시)로 결과를 돌려주고 닫는다.
                # 둘 다 없는 옛 초안도 기존 소유자 DM 경로로 실행 결과를 알린다.
                _notify_result(
                    self.discord,
                    record,
                    _executed_notice(record, self.entry.draft_id),
                    calendar_confirm.OUTCOME_DONE,
                )
            case state.CANCELLED:
                self.commands.discard(self.entry.draft_id)
                _notify_result(
                    self.discord, record, _cancelled_notice(record, self.entry.draft_id),
                    calendar_confirm.OUTCOME_CANCELLED,
                )
            case unreachable:
                assert_never(unreachable)

    def drop(self, _request) -> None:
        self.store.drop(self.entry)


def run_once(
    *,
    store: PendingConfirmStore,
    owner_id: str,
    discord: DiscordClient,
    commands: CommandRunner,
    draft_sha256: Callable[[str], str],
    now: datetime,
    reminder_config: object | None = None,
    draft_record: Callable[[str], dict] = _load_draft_record,
) -> None:
    """Apply only unambiguous bound owner reactions and retain uncertain entries."""
    snapshot = store.load()
    failures: list[ConfirmWatchError] = []
    _process_entries(
        snapshot, store, owner_id, discord, commands, draft_sha256, now,
        reminder_config=reminder_config, failures=failures, draft_record=draft_record,
    )
    sweep_orphan_drafts(snapshot, commands=commands, discord=discord, now=now)
    fatal_failures = tuple(error for error in failures if error.fatal)
    if fatal_failures:
        raise ConfirmBatchError(fatal_failures)


def sweep_orphan_drafts(
    snapshot: tuple[PendingConfirm, ...],
    *,
    commands: CommandRunner,
    discord: DiscordClient,
    now: datetime,
    list_drafts: Callable[[], list[dict]] | None = None,
) -> tuple[str, ...]:
    """Post the card a pending draft never got, and discard it only if that keeps failing.

    draft-create 와 post-confirm 은 별개 단계다 — post-confirm 이 불리지 않으면 초안은
    pending-confirms 원장에 없어 이 워처의 어떤 경로도 다시 보지 않는다(2026-07~08
    실측 33건 누적, 전부 행사일 경과). 게시된 확인에 묶인 초안은 건드리지 않는다.

    **2026-09-10**: 폐기만으로는 부족하다는 것이 실측으로 드러났다 — 노드 agent 계정의
    승인 lease 는 09-06 이후 하나도 늘지 않았고 posting-journal 은 비어 있는데 pending
    초안은 계속 쌓였다. 즉 `request_owner_approval` 이 **아예 호출되지 않았고**, 소유자는
    24시간 뒤 "게시되지 않은 채 폐기했습니다" 통지 3건만 받았다. 그래서 이제 두 분기다:
    `POST_GRACE` 를 넘긴 고아는 기존 승인 게이트로 **카드를 올리고**(새 승인 기계장치
    없음), EXPIRY(24h)까지도 카드가 붙지 못한 고아만 기존 discard 경로로 폐기한 뒤
    소유자에게 알린다. 나이를 알 수 없는 초안은 어느 쪽도 하지 않는다(보존이 안전한
    방향). 실패는 tick 을 죽이지 않는다 — 다음 tick 이 다시 본다.
    """
    reader = list_drafts if list_drafts is not None else calendar_gate.list_drafts
    posted = {entry.draft_id for entry in snapshot}
    swept: list[str] = []
    try:
        records = reader()
    except Exception as error:  # noqa: BLE001 — 정리는 부가물, 판독 실패는 다음 tick 몫이다.
        print(
            f"calendar-confirm-watch orphan scan failed: {_redact(str(error))}",
            file=sys.stderr,
        )
        return ()
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "pending":
            continue
        draft_id = record.get("id")
        if not isinstance(draft_id, str) or not draft_id:
            continue
        # 다이제스트 초안은 카드가 붙어 있어도 내용이 바뀌었으면(교체 실패) 게시가 안 끝난 것이다.
        digest = bool(record.get("digest_day"))
        live = {entry.sha256 for entry in snapshot if entry.draft_id == draft_id}
        if (record.get("sha256") in live) if digest else (draft_id in posted):
            continue
        if digest and live:  # 옛 카드가 살아 있는 교체 실패 — 나이와 무관하게 그 카드를 교체한다
            import_module("calendar_digest").retry(record)
            continue
        created_raw = record.get("created")
        if not isinstance(created_raw, str):
            continue
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None:
            continue
        age = now.astimezone(UTC) - created
        if age <= EXPIRY:
            # 다이제스트 카드는 3분 유예 없이 즉시 — 게시가 끝나야 정상 대기다(실패는 이미 통지됨).
            if digest:
                import_module("calendar_digest").retry(record)
            elif age > POST_GRACE:
                _post_missing_confirmation(record, draft_id)
            continue
        try:
            commands.discard(draft_id)
        except Exception as error:  # noqa: BLE001 — 초안 하나의 실패가 나머지를 막으면 안 된다.
            print(
                f"calendar-confirm-watch orphan discard failed draft={draft_id}: "
                f"{_redact(str(error))}",
                file=sys.stderr,
            )
            continue
        _notify_result(discord, record, _orphan_notice(record, draft_id))
        swept.append(draft_id)
    return tuple(swept)


def _post_missing_confirmation(record: Mapping[str, object], draft_id: str) -> None:
    """Post the approval card this draft's own ``post-confirm`` never posted.

    새 승인 기계장치를 만들지 않는다 — 에이전트가 불렀어야 할 바로 그
    `calendar_approval.request_confirmation` 을 그대로 부른다(단일 게이트 재사용). 그
    파사드는 승인 키 단위 lease 와 posting journal 로 스스로 멱등하므로, 에이전트가 같은
    순간에 post-confirm 을 돌려도 카드가 둘이 되지 않는다(둘째는 PENDING 으로 답한다).

    실패는 이 초안 하나만 건너뛴다 — 폐기하지 않고 다음 tick 이 다시 본다. EXPIRY 까지
    끝내 못 올리면 기존 폐기 경로가 소유자에게 알린다.
    """
    try:
        _ = calendar_approval.request_confirmation(record)
    except Exception as error:  # noqa: BLE001 — 초안 하나의 실패가 나머지를 막으면 안 된다.
        print(
            f"calendar-confirm-watch missing card post failed draft={draft_id}: "
            f"{_redact(str(error))}",
            file=sys.stderr,
        )
        return
    print(f"calendar-confirm-watch posted missing card draft={draft_id}", file=sys.stderr)


def _process_entries(
    entries: tuple[PendingConfirm, ...],
    store: PendingConfirmStore,
    owner_id: str,
    discord: DiscordClient,
    commands: CommandRunner,
    draft_sha256: Callable[[str], str],
    now: datetime,
    *,
    reminder_config: object | None = None,
    failures: list[ConfirmWatchError] | None = None,
    draft_record: Callable[[str], dict] = _load_draft_record,
) -> tuple[PendingConfirm, ...]:
    retained: list[PendingConfirm] = []
    reminders = []
    resolutions = []
    failed_reminders: set[str] = set()
    for entry in entries:
        try:
            decision = OwnerDecision(
                entry, owner_id, discord, commands, draft_sha256, store, draft_record
            )
            if now.astimezone(UTC) - entry.created > EXPIRY:
                with calendar_approval.confirm_lease(store.path.parent).hold(entry.key) as owned:
                    if not owned:
                        retained.append(entry)
                        continue
                    decision.probe(calendar_approval.request_of(entry))
                    record = {**draft_record(entry.draft_id), "dm_message_id": entry.dm_message_id}
                    commands.discard(entry.draft_id)
                    _notify_result(
                        discord, record, _expired_notice(record, entry.draft_id),
                        calendar_confirm.OUTCOME_EXPIRED,
                    )
                    decision.drop(calendar_approval.request_of(entry))
            else:
                request = calendar_approval.request_of(entry)
                lease = calendar_approval.confirm_lease(store.path.parent)
                if reminder_config is not None:
                    reminder = calendar_approval._repo_module("approval_reminder")
                    surface = calendar_approval._repo_module("approval_surface")
                    kind = surface.ApprovalKind(entry.kind or surface.ApprovalKind.CALENDAR)
                    guild_id = draft_record(entry.draft_id).get("approval_guild_id")
                    context = reminder.ReminderContext(
                        config=reminder_config,
                        journal=calendar_approval._lease_module().ReminderJournal(
                            store.path.parent / "reminder-journal"
                        ),
                        request_type=kind,
                        deliver=lambda channel_id, content: _deliver_reminder(discord, channel_id, content),
                        clock=lambda: now,
                        guild_id=guild_id if isinstance(guild_id, str) and guild_id else None,
                        space_for=lambda _channel, surface=entry.surface: reminder.stored_reminder_space(surface),
                        guild_id_for=reminder.channel_guild_resolver(
                            lambda channel_id: discord.fetch_channel(channel_id)
                        ),
                    )
                    reminders.append(reminder.ReminderCall(request, decision, lease, context))
                resolutions.append((entry, request, decision, lease))
        except ConfirmWatchError as error:
            _log_failure(error, entry)
            if failures is not None:
                failures.append(error)
            retained.append(entry)
        except Exception as error:  # noqa: BLE001 — per-entry cron failure boundary.
            wrapped = ConfirmWatchError(
                "pending confirmation operation raised",
                stage="entry.process",
                fatal=True,
                stderr=str(error),
                cause_type=type(error).__name__,
            )
            _log_failure(wrapped, entry)
            if failures is not None:
                failures.append(wrapped)
            retained.append(entry)
    if reminders:
        def reminder_failed(request, error):
            failed_reminders.add(request.key)
            entry = next(entry for entry in entries if entry.key == request.key)
            wrapped = ConfirmWatchError(
                "pending confirmation operation raised", stage="entry.process",
                fatal=True, stderr=str(error), cause_type=type(error).__name__,
            )
            _log_failure(wrapped, entry)
            if failures is not None:
                failures.append(wrapped)

        reminder.dispatch_owner_approval_reminders(reminders, on_error=reminder_failed)
    for entry, request, decision, lease in resolutions:
        if request.key in failed_reminders:
            retained.append(entry)
            continue
        try:
            verdict = calendar_approval.lifecycle().resolve_owner_decision(
                request, decision, lease,
            )
            outcome = calendar_approval.lifecycle().WatchOutcome
            match verdict.outcome:
                case outcome.CONSUMED:
                    pass
                case outcome.WAITING | outcome.SKIPPED:
                    retained.append(entry)
                case unreachable:
                    assert_never(unreachable)
        except Exception as error:  # noqa: BLE001 — per-entry cron failure boundary.
            wrapped = error if isinstance(error, ConfirmWatchError) else ConfirmWatchError(
                "pending confirmation operation raised", stage="entry.process",
                fatal=True, stderr=str(error), cause_type=type(error).__name__,
            )
            _log_failure(wrapped, entry)
            if failures is not None:
                failures.append(wrapped)
            retained.append(entry)
    return tuple(entry for entry in entries if entry in retained)


def _has_thread(record: Mapping[str, object]) -> bool:
    """A request thread to answer in — this request's own, else the instructing channel."""
    return bool(record.get("approval_thread_id") or record.get("origin_channel_id"))


def _deliver_reminder(discord: object, channel_id: str, content: str) -> None:
    """Calendar's reminder delivery: inside a digest thread, join a fresh reminder message (Q187)."""
    try:
        shared = calendar_approval._repo_module("digest_thread")
    except calendar_gate.GateError:
        shared = None
    if shared is None or shared.find(channel_id) is None:
        discord.post_message(channel_id, content)
        return
    shared.post_reminder(channel_id, content, post=discord.post_reminder_message, edit=discord.edit_message)


def _notify_result(
    discord: DiscordClient, record: Mapping[str, object], content: str, outcome: str = ""
) -> None:
    """Send one result notice to this request's thread, else to cha.

    소유자 결정 2026-09-01: 결과는 승인이 게시된 그 스레드로 돌아가 종결 표시된다.
    승인 스레드도 origin 도 없는 옛 초안은 종전 경로(소유자 통지)를 그대로 쓴다.
    """
    if _has_thread(record):
        _notify_thread(record, content, outcome)
        return
    _notify_owner(discord, content, record, outcome)


def _notify_thread(record: Mapping[str, object], content: str, outcome: str = "") -> None:
    """Post to the request thread; the command already committed, so failures only log."""
    try:
        calendar_confirm.notify_result(
            dict(record), content, outcome, transport=import_module("calendar_digest").reply_transport(record),
        )
    except Exception as error:  # noqa: BLE001 — 통지 실패가 완료된 tick을 되돌리면 안 된다
        print(
            f"calendar-confirm-watch thread notification failed: {_redact(str(error))}",
            file=sys.stderr,
        )


def _notify_owner(
    discord: DiscordClient, content: str, record: Mapping[str, object], outcome: str,
) -> None:
    """Send a post-action owner notification through the shared owner-notice facade.

    목적지는 이 워처가 정하지 않는다 — `automation.owner_notice.notify_owner` 가
    `owner_notice_channel_id`(#notifications)를 존중하고, 미설정이면 소유자 DM 으로
    되돌린다(ON-1/ON-2). 2026-09-10 소유자 지시로 옮겼다: 카드 없이 폐기된 초안의 정리
    통지가 DM 에만 쌓여, 소유자가 정기 통지를 모아 보는 채널에서 보이지 않았다.

    파사드는 **절대 예외를 던지지 않고** False 로만 답한다. 그 실패가 이미 끝난
    (discard/confirm) 항목을 다시 붙잡으면 매 tick 재평가되므로, 여기서는 한 줄만 남긴다.
    """
    del discord  # 목적지는 파사드 몫이다 — 이 클라이언트는 스레드·리마인더 경로가 쓴다
    try:
        from calendar_result_message import build

        # 일정 내용은 넘기지 않는다. 읽지 못한 초안도 기존 fact의 id는 보존한다.
        draft: dict[str, str | list[str]] = {
            "id": str(record.get("id", "초안 id 미상")), "action": str(record.get("action", "")),
        }
        message = build(draft, content, outcome)
        if not owner_notice.notify_owner(content, message=message):
            print("calendar-confirm-watch owner notification failed: NOTIFY-FAIL", file=sys.stderr)
    except Exception as error:  # noqa: BLE001 — import·렌더 실패도 완료된 항목을 되살리지 않는다
        print(f"calendar-confirm-watch owner notification failed: {_redact(str(error))}", file=sys.stderr)


def reaction_action(entry: PendingConfirm, owner_id: str, discord: DiscordClient) -> str:
    cancel = _owner_reacted(discord.reaction_users(entry, CANCEL_EMOJI), owner_id)
    approve = _owner_reacted(discord.reaction_users(entry, APPROVE_EMOJI), owner_id)
    if cancel:
        return CANCEL_EMOJI
    if approve:
        return APPROVE_EMOJI
    return ""


def _owner_reacted(users: tuple[Mapping[str, str | bool], ...], owner_id: str) -> bool:
    return any(user.get("id", "") == owner_id and not bool(user.get("bot", False)) for user in users)


def _draft_sha256(draft_id: str) -> str:
    try:
        draft = calendar_gate.load_draft(draft_id)
    except calendar_gate.GateError as error:
        raise ConfirmWatchError("pending draft is unavailable") from error
    sha256 = draft.get("sha256")
    if not isinstance(sha256, str):
        raise ConfirmWatchError("pending draft hash is invalid")
    return sha256


def main() -> int:
    try:
        owner = calendar_confirm.owner_id()
        scripts = skill_scripts("calendar", env_var="CALENDAR_SCRIPTS")
        config = calendar_approval._repo_module(
            "approval_reminder_config"
        ).load_approval_reminder_config()
        run_once(
            store=PendingConfirmStore(), owner_id=owner, discord=DiscordApi(owner),
            commands=CliCommands(scripts / "calendar_cli.py"), draft_sha256=_draft_sha256,
            now=datetime.now(UTC), reminder_config=config,
        )
    except ConfirmBatchError as error:
        return error.exit_code
    except Exception as error:  # noqa: BLE001,BROAD_EXCEPT_OK — final cron alert boundary.
        _log_failure(
            ConfirmWatchError(
                "unhandled watcher failure", stage="watcher.main", fatal=True,
                cause_type=type(error).__name__, stderr=str(error),
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
