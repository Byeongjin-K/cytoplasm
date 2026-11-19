"""2026-11-04: a thread rename rate limit (429) must never cost a posted mail card its binding.

Discord allows two channel-name edits per ten minutes. Withdrawing a card marked its thread 💤 and
the revised card's 🔔 rename then hit 429; the shared REST helper slept on Retry-After, the node gate
killed compose at 150 s, and the card was left without reactions, without its message id and with a
posting journal no later tick could resolve (refused:posting-journal-stale).
"""
from __future__ import annotations

from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from tests.unit.test_approval_thread_reuse import CARD, NAME, THREAD, _compose, _compose_api
from tests.unit.test_mail_compose_gate import triage_approval, triage_confirm, triage_gate

import triage_approval_gate


def _rate_limited() -> HTTPError:
    headers = Message()
    headers["Retry-After"] = "300"
    return HTTPError("https://discord.test", 429, "rate limited", headers, None)


def _marks(tmp_path: Path) -> list[str]:
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    return sorted({path.name.partition(".")[0] for path in root.glob("[!.]*")}) if root.is_dir() else []


def test_rate_limited_rename_keeps_the_card_bound_with_reactions_and_remembers_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the reused thread shows 💤 and Discord refuses its rename with 429.
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    api.responses[("PATCH", f"/channels/{THREAD}")] = _rate_limited()
    # When: the real compose command posts the revised card.
    assert _compose() == 0
    # Then: the card is bound, both reactions came before the one rename attempt, and the
    # thread is remembered for a later tick.
    draft, = triage_gate.list_drafts()
    assert draft["message_id"] == CARD and draft["status"] == "pending"
    methods = [call[0] for call in api.calls if call[0] in ("PUT", "PATCH")]
    assert methods == ["PUT", "PUT", "PATCH"]
    assert triage_approval.posting_journal().outstanding(triage_approval.approval_key(draft)) is None
    assert _marks(tmp_path) == [THREAD]
    assert "THREAD-RENAME-FAIL" in capsys.readouterr().err


def test_card_is_bound_before_anything_after_the_post_can_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the process dies right after the card POST (the gate's 150 s kill).
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    put = f"/channels/{THREAD}/messages/{CARD}/reactions/{triage_confirm.quote('✅', safe='')}/@me"
    api.responses[("PUT", put)] = KeyboardInterrupt()
    # When: compose is interrupted while adding the first reaction.
    with pytest.raises(KeyboardInterrupt):
        _compose()
    # Then: the draft already holds the posted card and no stale journal is left behind.
    draft, = triage_gate.list_drafts()
    assert draft["message_id"] == CARD and draft["channel_id"] == THREAD
    assert triage_approval.posting_journal().outstanding(triage_approval.approval_key(draft)) is None


def test_api_does_not_sleep_on_429_inside_no_wait() -> None:
    sleeps: list[float] = []
    calls: list[int] = []

    def send(_request: object) -> object:
        calls.append(1)
        raise _rate_limited()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(triage_confirm, "bot_token", lambda: "identity-a")
        patch.setattr(triage_confirm, "_send", send)
        patch.setattr(triage_confirm.time, "sleep", sleeps.append)
        with triage_confirm.no_rate_limit_wait(), pytest.raises(HTTPError):
            triage_confirm._api("PATCH", f"/channels/{THREAD}", {"name": "x"})
        assert (sleeps, len(calls)) == ([], 1)
        with pytest.raises(HTTPError):
            triage_confirm._api("GET", f"/channels/{THREAD}")
        assert sleeps == [300.0] * (triage_confirm._RATE_LIMIT_ATTEMPTS - 1)


def test_watch_tick_sets_the_pending_mark_once_the_limit_lifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: compose left a remembered thread because its rename was rate limited.
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    api.responses[("PATCH", f"/channels/{THREAD}")] = _rate_limited()
    assert _compose() == 0
    # When: a later tick runs after the limit lifted.
    api.responses[("PATCH", f"/channels/{THREAD}")] = {}
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    # Then: the thread gets 🔔 and the reminder is gone.
    assert api.calls[-1] == ("PATCH", f"/channels/{THREAD}", {"name": "🔔 ✉️ request", "archived": False})
    assert _marks(tmp_path) == []


def test_watch_tick_drops_a_mark_whose_card_is_no_longer_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    api.responses[("PATCH", f"/channels/{THREAD}")] = _rate_limited()
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    triage_gate.write_json(triage_gate._draft_path(str(draft["id"])), {**draft, "status": "sent"})
    calls = len(api.calls)
    # When: the owner decided the card before the tick (its close set the end mark).
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    # Then: the tick never renames the decided thread back and forgets it.
    assert len(api.calls) == calls
    assert _marks(tmp_path) == []


def _open_thread(api: object, name: str) -> None:
    api.responses[("GET", f"/channels/{THREAD}")] = {  # type: ignore[attr-defined]
        **api.responses[("GET", f"/channels/{THREAD}")],  # type: ignore[attr-defined]
        "name": name, "thread_metadata": {"archived": False},
    }


def _notices_through_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(triage_confirm, "_dm_transport", lambda channel: triage_confirm._CardReply(channel, ""))
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda _body: pytest.fail("the result left its thread"))


def _renames(api: object, since: int) -> list[dict]:
    return [call[2] for call in api.calls[since:] if call[0] == "PATCH"]  # type: ignore[attr-defined]


def test_a_result_whose_end_mark_is_rate_limited_is_remembered_and_a_later_tick_sets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 2026-11-12 thread 1570290417285726290 - the card was sent while the thread's two renames of
    # the last ten minutes were used up, so the ✅ rename was refused with 429.
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    _open_thread(api, "🔔 ✉️ request")
    _notices_through_api(monkeypatch)
    api.responses[("PATCH", f"/channels/{THREAD}")] = _rate_limited()
    # When: the card is sent (the watch records it first) and its result closes the thread.
    sent = {**draft, "status": "sent"}
    triage_gate.write_json(triage_gate._draft_path(str(draft["id"])), sent)
    triage_confirm.notify_result(sent, "sent", outcome=triage_confirm.SENT_OUTCOME)
    # Then: the wanted ✅ is remembered for the watch tick.
    assert _marks(tmp_path) == [THREAD]
    # When: a later tick runs after the limit lifted, with no card waiting there.
    api.responses[("PATCH", f"/channels/{THREAD}")] = {}
    calls = len(api.calls)
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    # Then: the thread gets its end mark once and is closed.
    assert _renames(api, calls) == [{"name": "✅ ✉️ request", "archived": True}]
    assert _marks(tmp_path) == []


def test_an_older_cards_result_never_marks_a_thread_where_a_newer_card_still_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the newer card waits in the thread (🔔); the owner then cancels an older card of the
    # same thread (2026-11-12 04:26 the thread turned ⛔ under the waiting card 3677a7).
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    waiting, = triage_gate.list_drafts()
    _open_thread(api, "🔔 ✉️ request")
    _notices_through_api(monkeypatch)
    calls = len(api.calls)
    older = {**waiting, "id": "0ld0ld", "status": "cancelled", "message_id": "300000000000000009"}
    # When: the older card's cancel result is delivered in that thread.
    triage_confirm.notify_result(older, "cancelled", outcome=triage_confirm.CANCELLED_OUTCOME)
    # Then: the notice is posted but the thread keeps 🔔 with no rename spent on it.
    assert ("POST", f"/channels/{THREAD}/messages") in [call[:2] for call in api.calls[calls:]]
    assert _renames(api, calls) == []
    assert _marks(tmp_path) == []


def test_a_rename_never_sleeps_on_429_even_outside_no_wait() -> None:
    # A thread joined by compose is renamed through the same helper (approval_directory._join_thread);
    # Discord's name limit answers minutes, so a rename that waited would outlive the gate's 150 s.
    sleeps: list[float] = []

    def send(_request: object) -> object:
        raise _rate_limited()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(triage_confirm, "bot_token", lambda: "identity-a")
        patch.setattr(triage_confirm, "_send", send)
        patch.setattr(triage_confirm.time, "sleep", sleeps.append)
        with pytest.raises(HTTPError):
            triage_confirm._api("PATCH", f"/channels/{THREAD}", {"name": "🔔 x", "archived": False})
        assert sleeps == []
        with pytest.raises(HTTPError):
            triage_confirm._api("PATCH", f"/channels/{THREAD}", {"archived": False})
        assert sleeps == [300.0] * (triage_confirm._RATE_LIMIT_ATTEMPTS - 1)


def test_a_finished_older_mark_attempt_never_forgets_a_newer_wanted_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a tick settles a remembered ✅ while, during its rename, another result records ✅ again
    # without the lock (gate reviews of #238: the older attempt deleted that newer record).
    _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / THREAD).write_text("✅", encoding="utf-8")
    monkeypatch.setattr(triage_approval_gate, "_THREAD_LOCK_SECONDS", 0.0)

    def apply_while_a_result_arrives(thread: str, _mark: str) -> bool:
        assert triage_approval_gate.mark_thread(thread, "✅") is False  # the lock is held: recorded only
        return True

    monkeypatch.setattr(triage_approval_gate, "_apply_mark", apply_while_a_result_arrives)
    # When: the older attempt finishes.
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    # Then: the newer wanted mark is still there for the next tick, and only it.
    assert _marks(tmp_path) == [THREAD]


def test_a_mark_that_cannot_wait_for_the_lock_is_kept_whatever_the_holder_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: another process holds the thread's lock (gate re-review of #238: a writer that gave
    # up waiting lost its ✅ to the holder's cleanup, same value or not).
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    _open_thread(api, "🔔 ✉️ request")
    triage_gate.write_json(triage_gate._draft_path(str(draft["id"])), {**draft, "status": "sent"})
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    monkeypatch.setattr(triage_approval_gate, "_THREAD_LOCK_SECONDS", 0.0)
    with triage_approval_gate._thread_lock(root / THREAD) as held:
        assert held
        # When: the sent result cannot get the lock.
        assert triage_approval_gate.mark_thread(THREAD, "✅") is False
    # Then: its ✅ waits for the next settle, which sets it.
    assert _marks(tmp_path) == [THREAD]
    calls = len(api.calls)
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    assert _renames(api, calls) == [{"name": "✅ ✉️ request", "archived": True}]
    assert _marks(tmp_path) == []


def test_a_tick_reads_the_cards_again_before_marking_never_its_old_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the remembered mark is ⛔ but a card was bound in that thread after the tick listed
    # its drafts (gate review of #238: the tick closed the thread over the new waiting card).
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    _open_thread(api, "🔔 ✉️ request")
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / THREAD).write_text("⛔", encoding="utf-8")
    calls = len(api.calls)
    # When: the tick settles marks with its stale, empty list.
    triage_approval_gate.retry_thread_marks([])
    # Then: the waiting card keeps 🔔 and no rename is spent.
    assert _renames(api, calls) == []
    assert _marks(tmp_path) == []


def test_a_result_for_a_deleted_thread_is_dropped_as_before_and_never_sent_to_the_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    _notices_through_api(monkeypatch)
    gone = HTTPError("https://discord.test", 404, "unknown channel", Message(), None)
    api.responses[("POST", f"/channels/{THREAD}/messages")] = gone
    api.responses[("GET", f"/channels/{THREAD}")] = gone
    # When: the sent result finds its thread deleted.
    triage_confirm.notify_result({**draft, "status": "sent"}, "sent", outcome=triage_confirm.SENT_OUTCOME)
    # Then: nothing reached the owner's DM (dm_owner fails the test) and nothing is left to retry.
    assert _marks(tmp_path) == []


def test_an_approved_card_waiting_for_a_send_retry_keeps_its_failed_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the owner approved the card, the send failed retryably (mail_send_retry keeps it
    # pending with last_error) and the ❗ rename met the limit.
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    _open_thread(api, "🔔 ✉️ request")
    retrying = {**draft, "status": "pending", "failed_attempts": 1, "last_error": {"retryable": True}}
    triage_gate.write_json(triage_gate._draft_path(str(draft["id"])), retrying)
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / THREAD).write_text("❗", encoding="utf-8")
    calls = len(api.calls)
    # When: the next tick settles the thread.
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    # Then: it shows ❗ (open), not 🔔: nothing is left for the owner to press.
    assert _renames(api, calls) == [{"name": "❗ ✉️ request", "archived": False}]


def test_a_cards_final_result_wins_over_its_failure_whatever_the_order_of_the_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same card recorded ✅ (sent on retry) and, in a later-named file, ❗ (gate review
    # of #238: file names ordered by clock let an older failure beat the send).
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    triage_gate.write_json(triage_gate._draft_path(str(draft["id"])), {**draft, "status": "sent"})
    _open_thread(api, "🔔 ✉️ request")
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{THREAD}.1-1-a").write_text("⛔ 300000000000000001", encoding="utf-8")
    (root / f"{THREAD}.5-1-b").write_text(f"✅ {CARD}", encoding="utf-8")
    (root / f"{THREAD}.9-1-c").write_text(f"❗ {CARD}", encoding="utf-8")
    calls = len(api.calls)
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    # Then: the newest card's final result is shown; the older card's ⛔ and the ❗ lose.
    assert _renames(api, calls) == [{"name": "✅ ✉️ request", "archived": True}]
    assert _marks(tmp_path) == []


def test_a_mark_still_being_written_is_never_seen_or_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a writer stopped after creating its record but before it was complete (gate review of
    # #238: an empty record read as 🔔 was removed, and the writer's ✅ was lost).
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    root.mkdir(parents=True)
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))

    def stop(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(triage_approval_gate.os, "replace", stop)
    with pytest.raises(KeyboardInterrupt):
        triage_approval_gate._record_mark(root, THREAD, "✅", CARD)
    # Then: no settler can see (and so remove) a record that is not complete.
    assert triage_approval_gate._recorded_marks(root, THREAD) == []


def test_a_failure_published_after_the_same_cards_send_was_shown_never_reopens_the_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the card's retried send set ✅ and its record was consumed; then an older ❗ of the same
    # card, delayed before publication, appears (gate review of #238 delta4).
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    triage_gate.write_json(triage_gate._draft_path(str(draft["id"])), {**draft, "status": "sent"})
    _open_thread(api, "🔔 ✉️ request")
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    assert triage_approval_gate.mark_thread(THREAD, "✅", card=CARD)
    _open_thread(api, "✅ ✉️ request")
    api.responses[("GET", f"/channels/{THREAD}")]["thread_metadata"] = {"archived": True}
    calls = len(api.calls)
    triage_approval_gate._record_mark(root, THREAD, "❗", CARD)
    # When: the next tick settles it.
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    # Then: the shown send stays; the late failure is dropped.
    assert _renames(api, calls) == []
    assert _marks(tmp_path) == []


def test_a_half_posted_card_is_adopted_instead_of_wedging_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the 2026-11-04 state - the card is in the thread, the draft has no message id,
    # and the posting journal holds a reservation without one.
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    posted, = triage_gate.list_drafts()
    content = next(call[2]["content"] for call in api.calls if call[:2] == ("POST", f"/channels/{THREAD}/messages"))
    key = triage_approval.approval_key(posted)
    path = tmp_path / "gate" / "drafts" / f"{posted['id']}.json"
    if not path.exists():
        path = next((tmp_path / "gate").rglob(f"{posted['id']}.json"))
    stuck = {name: value for name, value in posted.items()
             if name not in ("message_id", "render_version", "card_sha256", "approval_created_at")}
    triage_gate.write_json(path, {**stuck, "message_id": ""})
    journal = triage_approval.posting_journal()
    journal.reserve(key, triage_approval._approval_action_hash(posted), "2026-11-04T09:48:58Z")
    seen: list[tuple[str, str]] = []

    def discord(method: str, path: str, payload: dict | None = None) -> object:
        seen.append((method, path))
        if method == "GET" and path.startswith(f"/channels/{THREAD}/messages?"):
            return [{"id": "999", "author": {"bot": True}, "content": "other", "reactions": []},
                    {"id": CARD, "author": {"bot": True}, "content": content, "reactions": []}]
        if method == "GET" and path == f"/channels/{THREAD}/messages/{CARD}":
            return {"id": CARD, "content": content}
        if method == "GET" and "/reactions/" in path:
            return []
        return api(method, path, payload)

    monkeypatch.setattr(triage_confirm, "_api", discord)
    # When: the next watch tick reposts the unbound pending draft.
    assert triage_approval.post_for_approval(triage_gate.load_draft(posted["id"])) == CARD
    # Then: the existing card is bound with both reactions; no second card is posted.
    draft, = triage_gate.list_drafts()
    assert draft["message_id"] == CARD and draft["card_sha256"] == posted["card_sha256"]
    assert journal.outstanding(key) is None
    assert ("POST", f"/channels/{THREAD}/messages") not in seen
    assert [path for method, path in seen if method == "PUT"] == [
        f"/channels/{THREAD}/messages/{CARD}/reactions/{triage_confirm.quote(emoji, safe='')}/@me"
        for emoji in ("✅", "⛔")
    ]


@pytest.mark.parametrize(("first", "second", "mark"), [
    ("executed", "CANCELLED", "✅"),
    ("executed", "EXPIRED", "✅"),
    ("cancelled", "CANCELLED", "⛔"),
])
def test_a_thread_where_a_mail_was_sent_stays_done_whatever_its_other_card_ends_with(
    first: str, second: str, mark: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 2026-11-18 - the owner sent one mail of a thread (✅) while an older draft's card still waited
    # there, then cancelled that older card (⛔): the thread was renamed cancelled although a mail had gone out.
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    _open_thread(api, "🔔 ✉️ request")
    _notices_through_api(monkeypatch)
    path = triage_gate._draft_path(str(draft["id"]))
    older = {**draft, "id": "0ld0ld", "message_id": "300000000000000001"}
    triage_gate.write_json(path.with_name("0ld0ld.json"), older)
    # When: the newer card is decided while the older one waits.
    decided = {**draft, "status": first}
    triage_gate.write_json(path, decided)
    outcome = triage_confirm.SENT_OUTCOME if first == "executed" else triage_confirm.CANCELLED_OUTCOME
    calls = len(api.calls)
    triage_confirm.notify_result(decided, first, outcome=outcome)
    # Then: the waiting older card keeps the thread 🔔.
    assert _renames(api, calls) == []
    # When: the older card ends too.
    ended = {**older, "status": "cancelled" if second == "CANCELLED" else "expired"}
    triage_gate.write_json(path.with_name("0ld0ld.json"), ended)
    triage_confirm.notify_result(ended, ended["status"], outcome=second)
    # Then: a thread where a mail was sent shows done; without a send, the last result.
    assert _renames(api, calls)[-1] == {"name": f"{mark} ✉️ request", "archived": True}
    assert _marks(tmp_path) == []
