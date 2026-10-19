"""Reconcile notice wire compatibility; cadence tests stay in their original files."""
from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from automation import deploy_reconcile as reconcile, owner_notice
from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref, Result

DRIFT: Final = 'prod has not converged to origin/main.\n  origin/main : bbb\n  runtime     : aaa\n  실패 3회 · 미수렴 10분\n자동 재시도는 계속됩니다. 반복되면 노드에서 원인을 확인하세요 (재시작·포인터 수정은 하지 마세요).'
SKIP: Final = 'prod has not converged to origin/main.\n  origin/main : unresolved\n  runtime     : blocked: update-trust-block\n  실패 3회 · 미수렴 10분\n자동 재시도는 계속됩니다. 반복되면 노드에서 원인을 확인하세요 (재시작·포인터 수정은 하지 마세요).'
RECOVERY: Final = 'prod가 origin/main에 다시 도달했습니다: bbb'
BACKLOG_CLEAN: Final = '릴리스 대기 중인 머지가 쌓여 있습니다 (머지=축적, 릴리스=배포).\n  미배포 커밋 : 4건 · 3일 경과\n  origin/main : bbb\n  runtime     : aaa\n릴리스하려면 워크스테이션에서 `automation/release.sh` 를 실행하세요 (소유자 ✅ 1회).\n노드는 서명 없는 head 를 설치하지 않으며, 이 상태는 사고가 아닙니다.'
BACKLOG_BEHIND: Final = '릴리스 대기 중인 머지가 쌓여 있습니다 (머지=축적, 릴리스=배포).\n  미배포 커밋 : 4건 · 3일 경과\n  origin/main : bbb\n  runtime     : aaa\n릴리스하려면 워크스테이션에서 `automation/release.sh` 를 실행하세요 (소유자 ✅ 1회).\n노드는 서명 없는 head 를 설치하지 않으며, 이 상태는 사고가 아닙니다.\n관측 미러 `/srv/autophagy-agents`는 릴리스 후 origin/main을 따라갑니다.'
BACKLOG_DIRTY: Final = '릴리스 대기 중인 머지가 쌓여 있습니다 (머지=축적, 릴리스=배포).\n  미배포 커밋 : 4건 · 3일 경과\n  origin/main : bbb\n  runtime     : aaa\n릴리스하려면 워크스테이션에서 `automation/release.sh` 를 실행하세요 (소유자 ✅ 1회).\n노드는 서명 없는 head 를 설치하지 않으며, 이 상태는 사고가 아닙니다.\n관측 미러 `/srv/autophagy-agents`가 미커밋/미푸시 작업으로 동결되어 origin/main을 따라갈 수 없습니다.\n미러에서 `git format-patch` → 개발 체크아웃에서 적용 → commit/push 하세요; `git reset --hard`는 절대 사용하지 마세요.'

CASES: Final = (("drift", DRIFT), ("skip", SKIP), ("rollback", DRIFT),
                ("recovery", RECOVERY), ("clean", BACKLOG_CLEAN),
                ("behind", BACKLOG_BEHIND), ("dirty", BACKLOG_DIRTY), ("ahead", BACKLOG_DIRTY))


# Independent fixed-coordinate oracles: never built from a producer or renderer.
COMMIT_REF: Final = Ref(scope="resource", space="unknown", guild_id=None,
    channel_id=None, message_id=None, url=None, search=("Git 커밋", "bbb"))
BLOCKED_REF: Final = Ref(scope="none", space="unknown", guild_id=None,
    channel_id=None, message_id=None, url=None, search=None)
INVESTIGATE: Final = Action("open", Ref(scope="resource", space="unknown", guild_id=None,
    channel_id=None, message_id=None, url=None, search=("운영 점검", "bbb")),
    "원인 확인; 재시작·포인터 수정 금지")
INVESTIGATE_BLOCK: Final = Action("open", Ref(scope="resource", space="unknown", guild_id=None,
    channel_id=None, message_id=None, url=None, search=("운영 점검", "update-trust-block")),
    "원인 확인; 재시작·포인터 수정 금지")
RELEASE_REF: Final = Ref(scope="resource", space="unknown", guild_id=None,
    channel_id=None, message_id=None, url=None, search=("명령", "automation/release.sh"))
RELEASE: Final = Action("open", RELEASE_REF, "워크스테이션에서 실행; 소유자 ✅ 1회")
TRANSFER_RELEASE: Final = Action("open", RELEASE_REF,
    "먼저 git format-patch → 개발 체크아웃 적용·commit/push; reset --hard 금지; 워크스테이션에서 실행; 소유자 ✅ 1회")
DRIFT_WINDOW: Final = Periodic(datetime(1970, 1, 1, tzinfo=UTC), datetime(1970, 1, 1, 0, 10, tzinfo=UTC))
BACKLOG_WINDOW: Final = Periodic(datetime(1970, 1, 1, tzinfo=UTC), datetime(1970, 1, 4, tzinfo=UTC))
DRIFT_MESSAGE: Final = OwnerMessage(
    subject_key="bbb", subject="수렴 점검", fact="runtime aaa; 실패 3회 · 미수렴 10분",
    location=COMMIT_REF, owner=INVESTIGATE, agent_next="자동 재시도 계속",
    recovery="not_applicable", detail=DRIFT_WINDOW)
SKIP_MESSAGE: Final = OwnerMessage(
    subject_key="update-trust-block", subject="수렴 점검", fact="runtime blocked: update-trust-block; 실패 3회 · 미수렴 10분",
    location=BLOCKED_REF, owner=INVESTIGATE_BLOCK, agent_next="자동 재시도 계속",
    recovery="not_applicable", detail=DRIFT_WINDOW)
RECOVERY_MESSAGE: Final = OwnerMessage(
    subject_key="bbb", subject="수렴 회복", fact="runtime이 목표에 도달",
    location=COMMIT_REF, owner=Action("none", None, None), agent_next="다음 틱에서 상태 관측",
    recovery="not_applicable", detail=Result("executed"))
BACKLOG_MESSAGE: Final = OwnerMessage(
    subject_key="bbb", subject="릴리스 백로그", fact="미배포 4건 · 3일 경과; runtime aaa",
    location=COMMIT_REF, owner=RELEASE, agent_next="서명된 릴리스만 수렴; 미러 안전 판정 유지",
    recovery="not_applicable", detail=BACKLOG_WINDOW)
FROZEN_MESSAGE: Final = OwnerMessage(
    subject_key="bbb", subject="릴리스 백로그", fact="미배포 4건 · 3일 경과; runtime aaa; 관측 미러 동결",
    location=COMMIT_REF, owner=TRANSFER_RELEASE, agent_next="서명된 릴리스만 수렴; 미러 안전 판정 유지",
    recovery="not_applicable", detail=BACKLOG_WINDOW)
DRIFT_RETRY: Final = OwnerMessage(
    subject_key="bbb", subject="수렴 점검", fact="runtime aaa; 실패 3회 · 미수렴 10분",
    location=COMMIT_REF, owner=INVESTIGATE, agent_next="자동 재시도 계속",
    recovery="not_applicable", detail=Result("executed"))
SKIP_RETRY: Final = OwnerMessage(
    subject_key="update-trust-block", subject="수렴 점검", fact="runtime blocked: update-trust-block; 실패 3회 · 미수렴 10분",
    location=BLOCKED_REF, owner=INVESTIGATE_BLOCK, agent_next="자동 재시도 계속",
    recovery="not_applicable", detail=Result("executed"))
BACKLOG_RETRY: Final = OwnerMessage(
    subject_key="bbb", subject="릴리스 백로그", fact="미배포 4건 · 3일 경과; runtime aaa",
    location=COMMIT_REF, owner=RELEASE, agent_next="서명된 릴리스만 수렴; 미러 안전 판정 유지",
    recovery="not_applicable", detail=Result("executed"))
FROZEN_RETRY: Final = OwnerMessage(
    subject_key="bbb", subject="릴리스 백로그", fact="미배포 4건 · 3일 경과; runtime aaa; 관측 미러 동결",
    location=COMMIT_REF, owner=TRANSFER_RELEASE, agent_next="서명된 릴리스만 수렴; 미러 안전 판정 유지",
    recovery="not_applicable", detail=Result("executed"))
MESSAGES: Final = {"drift": DRIFT_MESSAGE, "rollback": DRIFT_MESSAGE, "skip": SKIP_MESSAGE,
    "recovery": RECOVERY_MESSAGE, "clean": BACKLOG_MESSAGE, "behind": BACKLOG_MESSAGE,
    "dirty": FROZEN_MESSAGE, "ahead": FROZEN_MESSAGE}
RETRIES: Final = {"drift": DRIFT_RETRY, "rollback": DRIFT_RETRY, "skip": SKIP_RETRY,
    "recovery": RECOVERY_MESSAGE, "clean": BACKLOG_RETRY, "behind": BACKLOG_RETRY,
    "dirty": FROZEN_RETRY, "ahead": FROZEN_RETRY}
DRIFT_WIRE: Final = '대상: 수렴 점검 (bbb)\n사실: runtime aaa; 실패 3회 · 미수렴 10분 (관측: 1970-01-01T00:00:00+00:00 ~ 1970-01-01T00:10:00+00:00)\n위치: 링크 없음 (주소 없음); 검색: Git 커밋 / bbb\n인계: 소유자: 링크 없음 (주소 없음); 검색: 운영 점검 / bbb · 열기 원인 확인; 재시작·포인터 수정 금지; 다음: 자동 재시도 계속\n되돌리기: 해당 없음'
SKIP_WIRE: Final = '대상: 수렴 점검 (update-trust-block)\n사실: runtime blocked: update-trust-block; 실패 3회 · 미수렴 10분 (관측: 1970-01-01T00:00:00+00:00 ~ 1970-01-01T00:10:00+00:00)\n위치: 해당 없음\n인계: 소유자: 링크 없음 (주소 없음); 검색: 운영 점검 / update-trust-block · 열기 원인 확인; 재시작·포인터 수정 금지; 다음: 자동 재시도 계속\n되돌리기: 해당 없음'
RECOVERY_WIRE: Final = '대상: 수렴 회복 (bbb)\n사실: runtime이 목표에 도달 (실행 완료)\n위치: 링크 없음 (주소 없음); 검색: Git 커밋 / bbb\n인계: 소유자: 조치 없음; 다음: 다음 틱에서 상태 관측\n되돌리기: 해당 없음'
BACKLOG_WIRE: Final = '대상: 릴리스 백로그 (bbb)\n사실: 미배포 4건 · 3일 경과; runtime aaa (관측: 1970-01-01T00:00:00+00:00 ~ 1970-01-04T00:00:00+00:00)\n위치: 링크 없음 (주소 없음); 검색: Git 커밋 / bbb\n인계: 소유자: 링크 없음 (주소 없음); 검색: 명령 / automation/release.sh · 열기 워크스테이션에서 실행; 소유자 ✅ 1회; 다음: 서명된 릴리스만 수렴; 미러 안전 판정 유지\n되돌리기: 해당 없음'
FROZEN_WIRE: Final = '대상: 릴리스 백로그 (bbb)\n사실: 미배포 4건 · 3일 경과; runtime aaa; 관측 미러 동결 (관측: 1970-01-01T00:00:00+00:00 ~ 1970-01-04T00:00:00+00:00)\n위치: 링크 없음 (주소 없음); 검색: Git 커밋 / bbb\n인계: 소유자: 링크 없음 (주소 없음); 검색: 명령 / automation/release.sh · 열기 먼저 git format-patch → 개발 체크아웃 적용·commit/push; reset --hard 금지; 워크스테이션에서 실행; 소유자 ✅ 1회; 다음: 서명된 릴리스만 수렴; 미러 안전 판정 유지\n되돌리기: 해당 없음'
DRIFT_RETRY_WIRE: Final = '대상: 수렴 점검 (bbb)\n사실: runtime aaa; 실패 3회 · 미수렴 10분 (실행 완료)\n위치: 링크 없음 (주소 없음); 검색: Git 커밋 / bbb\n인계: 소유자: 링크 없음 (주소 없음); 검색: 운영 점검 / bbb · 열기 원인 확인; 재시작·포인터 수정 금지; 다음: 자동 재시도 계속\n되돌리기: 해당 없음'
SKIP_RETRY_WIRE: Final = '대상: 수렴 점검 (update-trust-block)\n사실: runtime blocked: update-trust-block; 실패 3회 · 미수렴 10분 (실행 완료)\n위치: 해당 없음\n인계: 소유자: 링크 없음 (주소 없음); 검색: 운영 점검 / update-trust-block · 열기 원인 확인; 재시작·포인터 수정 금지; 다음: 자동 재시도 계속\n되돌리기: 해당 없음'
BACKLOG_RETRY_WIRE: Final = '대상: 릴리스 백로그 (bbb)\n사실: 미배포 4건 · 3일 경과; runtime aaa (실행 완료)\n위치: 링크 없음 (주소 없음); 검색: Git 커밋 / bbb\n인계: 소유자: 링크 없음 (주소 없음); 검색: 명령 / automation/release.sh · 열기 워크스테이션에서 실행; 소유자 ✅ 1회; 다음: 서명된 릴리스만 수렴; 미러 안전 판정 유지\n되돌리기: 해당 없음'
FROZEN_RETRY_WIRE: Final = '대상: 릴리스 백로그 (bbb)\n사실: 미배포 4건 · 3일 경과; runtime aaa; 관측 미러 동결 (실행 완료)\n위치: 링크 없음 (주소 없음); 검색: Git 커밋 / bbb\n인계: 소유자: 링크 없음 (주소 없음); 검색: 명령 / automation/release.sh · 열기 먼저 git format-patch → 개발 체크아웃 적용·commit/push; reset --hard 금지; 워크스테이션에서 실행; 소유자 ✅ 1회; 다음: 서명된 릴리스만 수렴; 미러 안전 판정 유지\n되돌리기: 해당 없음'
WIRES: Final = {"drift": DRIFT_WIRE, "rollback": DRIFT_WIRE, "skip": SKIP_WIRE,
    "recovery": RECOVERY_WIRE, "clean": BACKLOG_WIRE, "behind": BACKLOG_WIRE,
    "dirty": FROZEN_WIRE, "ahead": FROZEN_WIRE}
RETRY_WIRES: Final = {"drift": DRIFT_RETRY_WIRE, "rollback": DRIFT_RETRY_WIRE, "skip": SKIP_RETRY_WIRE,
    "recovery": RECOVERY_WIRE, "clean": BACKLOG_RETRY_WIRE, "behind": BACKLOG_RETRY_WIRE,
    "dirty": FROZEN_RETRY_WIRE, "ahead": FROZEN_RETRY_WIRE}


def emit(kind: str, callback: Callable[[str], bool]) -> reconcile.ReconcileState:
    """Fixed observation inputs exercise real state-machine send sites."""
    match kind:
        case "skip":
            return reconcile.reconcile_skip(
                reconcile.ReconcileState(consecutive_failures=2, drift_since=0,
                                         skip_reason="update-trust-block"),
                reason="update-trust-block", now=600, deliver=callback,
            )
        case "clean" | "behind" | "dirty" | "ahead":
            return reconcile.reconcile_unsigned_head(
                reconcile.ReconcileState(drift_since=0, skip_reason="release-backlog"),
                remote_head="bbb", current_sha="aaa", now=259200, commit_count=4,
                mirror_state=kind, deliver=callback,
            )
        case _:
            return reconcile.reconcile_tick(
                reconcile.ReconcileState(consecutive_failures=2, drift_since=0,
                    incident_open=kind == "recovery",
                    skip_reason="rollback-pending" if kind == "rollback" else None),
                origin_sha="bbb", current_sha="bbb" if kind == "recovery" else "aaa",
                now=600, converge=lambda: 6 if kind == "rollback" else 1, deliver=callback,
            )


@pytest.mark.parametrize(("kind", "expected"), CASES)
def test_legacy_bytes_when_callback_is_injected(kind: str, expected: str) -> None:
    # Given: the old single-argument delivery seam.
    sent: list[str] = []
    # When: one observation reaches its existing notification threshold.
    emit(kind, lambda content: not sent.append(content))
    # Then: literal base bytes, including whitespace, are preserved.
    assert sent == [expected]


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Keep the actual facade/renderer; replace only its final transport."""
    sent: list[str] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-credential")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: sent.append(body))
    return sent


@pytest.mark.parametrize(("kind", "expected"), CASES)
def test_legacy_bytes_when_facade_has_no_capability(
    monkeypatch: pytest.MonkeyPatch, wire: list[str], kind: str, expected: str,
) -> None:
    # Given: an older facade with the original positional-only contract.
    monkeypatch.delattr(owner_notice, "ACCEPTS_OWNER_MESSAGE")
    # When: the actual producer sends through that facade.
    emit(kind, owner_notice.notify_owner)
    # Then: exactly today's content reaches the wire.
    assert wire == ([] if kind == "recovery" else [expected])


@pytest.mark.parametrize(("kind", "expected"), CASES)
def test_legacy_bytes_when_envelope_import_is_missing(
    monkeypatch: pytest.MonkeyPatch, wire: list[str], kind: str, expected: str,
) -> None:
    # Given: a deployed copy without the envelope leaf.
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    # When: the producer uses the real facade.
    emit(kind, owner_notice.notify_owner)
    # Then: its current wire bytes survive the failed import.
    assert wire == ([] if kind == "recovery" else [expected])


@pytest.fixture
def envelopes(monkeypatch: pytest.MonkeyPatch, wire: list[str]) -> list[OwnerMessage | None]:
    captured: list[OwnerMessage | None] = []
    original = owner_notice.notify_owner

    def capture(content: str, *, message: OwnerMessage | None = None, fault: bool = False) -> bool:
        captured.append(message)
        return original(content, message=message)

    monkeypatch.setattr(owner_notice, "notify_owner", capture)
    return captured


@pytest.mark.parametrize("kind", [kind for kind, _ in CASES])
def test_envelope_when_notice_is_due(envelopes: list[OwnerMessage | None], kind: str) -> None:
    # Given: a capable facade and a due observation.
    # When: the state machine sends its notice.
    _ = emit(kind, owner_notice.notify_owner)
    # Then: every field, including absent coordinates, equals the independent literal oracle.
    assert envelopes == ([] if kind == "recovery" else [MESSAGES[kind]])


@pytest.mark.parametrize("kind", [kind for kind, _ in CASES])
@pytest.mark.parametrize("retry", (False, True), ids=("fresh", "retry"))
def test_rendered_wire_when_facade_supports_envelope(wire: list[str], kind: str, retry: bool) -> None:
    # Given: the real facade/renderer, optionally with a pending original notice.
    state = emit(kind, lambda content: False) if retry else reconcile.ReconcileState()
    expected = RETRY_WIRES[kind] if retry else WIRES[kind]
    # When: a due notice or its queued retry crosses that boundary.
    if retry:
        _ = reconcile.reconcile_skip(state, reason="different", now=300000, deliver=owner_notice.notify_owner)
    else:
        _ = emit(kind, owner_notice.notify_owner)
    # Then: the complete delivered copy equals a fixed literal, not a second rendering.
    assert wire == ([] if kind == "recovery" else [expected])


@pytest.mark.parametrize(("kind", "expected"), CASES)
def test_pending_bytes_when_delivery_fails_and_state_is_reloaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, expected: str,
) -> None:
    from automation.deploy_reconcile_state import load_state, save_state
    # Given: a failed delivery, persisted by the actual state adapter.
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-credential")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")

    def fail(token: str, channel: str, body: str) -> None:
        raise OSError("injected transport failure")

    monkeypatch.setattr(owner_notice, "send_notice", fail)
    state = emit(kind, owner_notice.notify_owner)
    save_state(tmp_path / "state.json", state)
    # When: the next process reads the pending notice.
    restored = load_state(tmp_path / "state.json")
    # Then: the durable retry payload remains the literal legacy bytes.
    assert restored.pending_notice == (None if kind == "recovery" else expected)


@pytest.mark.parametrize("kind", [kind for kind, _ in CASES])
def test_envelope_when_durable_notice_is_retried(
    envelopes: list[OwnerMessage | None], tmp_path: Path, kind: str,
) -> None:
    from automation.deploy_reconcile_state import load_state, save_state
    # Given: the actual queue persisted after a refused single-argument delivery.
    state = emit(kind, lambda content: False)
    save_state(tmp_path / "state.json", state)
    restored = load_state(tmp_path / "state.json")
    # When: the next tick retries it (new observation is below its own threshold).
    updated = reconcile.reconcile_skip(restored, reason="different", now=300000,
                                       deliver=owner_notice.notify_owner)
    # Then: all original fields survive, with the honest timeless Result detail.
    assert updated.pending_notice is None
    assert envelopes == ([] if kind == "recovery" else [RETRIES[kind]])


@pytest.mark.parametrize("window", [(float("inf"), 600), (0, float("nan")), (0, 1e30)])
def test_original_bytes_when_persisted_time_is_unrepresentable(
    wire: list[str], window: tuple[float, float],
) -> None:
    from automation.deploy_reconcile_notice import send_notice
    # Given: a corrupt or out-of-range persisted observation time.
    # When: the adapter cannot represent it as a datetime.
    assert send_notice(owner_notice.notify_owner, DRIFT, window) is True
    # Then: notification still reaches the wire in its original format.
    assert wire == [DRIFT]


def test_original_bytes_when_queued_format_is_unknown(wire: list[str]) -> None:
    # Given: a queue written by a version whose text format is unknown.
    state = reconcile.ReconcileState(pending_notice="legacy-queued-notice")
    # When: a quiet tick replays that queue.
    reconcile.reconcile_skip(state, reason="different", now=600, deliver=owner_notice.notify_owner)
    # Then: the unknown message is not lost or rewritten.
    assert wire == ["legacy-queued-notice"]


def test_envelope_when_cli_runs_a_due_tick(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, envelopes: list[OwnerMessage | None],
) -> None:
    from types import SimpleNamespace

    from automation import deploy_reconcile_cli as cli
    from automation.deploy_reconcile_state import save_state
    # Given: a durable due incident and injected node I/O, not a mocked state machine.
    path = tmp_path / "state.json"
    save_state(path, reconcile.ReconcileState(consecutive_failures=2, drift_since=0))
    monkeypatch.setattr(cli, "DEFAULT_STATE_PATH", path)
    monkeypatch.setattr(cli, "UPDATE_CHANNEL_STATE", tmp_path / "channel.json")
    monkeypatch.setattr(cli, "unconfigured_reason", lambda config: None)
    monkeypatch.setattr(cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(cli, "candidate_update_sha", lambda: "bbb")
    monkeypatch.setattr(cli, "current_release_sha", lambda: "aaa")
    monkeypatch.setattr(cli, "_mirror_state", lambda channel: "clean")
    monkeypatch.setattr(cli, "observe_release_backlog", lambda *args, **kwargs: reconcile.Backlog())
    monkeypatch.setattr(cli, "run_release_update", lambda target, current: 1)
    monkeypatch.setattr(cli, "sync_mirror", lambda target: "in-sync")
    monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: 600))
    monkeypatch.setattr(cli, "notify_owner", owner_notice.notify_owner)
    # When: the actual runnable entrypoint executes.
    result = cli.main()
    # Then: it sends the due envelope through the actual facade.
    assert result == 0
    assert len(envelopes) == 1
    assert envelopes[0] is not None
    assert envelopes[0].subject_key == "bbb"
