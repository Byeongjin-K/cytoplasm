from __future__ import annotations

from pathlib import Path

import pytest

from automation.deploy_reconcile_trust import observe_stale_trust_key
from automation.update_trust_stale import StaleTrustKey


def _stale(tag: str = "v1.40.0", signer: str = "SHA256:new") -> StaleTrustKey:
    return StaleTrustKey("v1.22.2", tag, "BAD-SIGNATURE", signer)


class _Notify:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.sent: list[str] = []

    def __call__(self, stale: StaleTrustKey, warning: str) -> bool:
        self.sent.append(warning)
        return self.delivered


def test_stale_key_notifies_once_then_only_logs_every_tick(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust-key-notice.txt"
    notify = _Notify()

    observe_stale_trust_key(_stale(), path=path, notify=notify)
    observe_stale_trust_key(_stale(), path=path, notify=notify)

    assert len(notify.sent) == 1
    assert "SHA256:new" in notify.sent[0]
    logged = capsys.readouterr().err.splitlines()
    assert len(logged) == 2
    assert all(line.startswith("[deploy-reconcile] UPDATE-TRUST-STALE-KEY") for line in logged)


def test_stale_key_when_a_different_key_or_release_appears_then_notifies_again(tmp_path: Path) -> None:
    path = tmp_path / "trust-key-notice.txt"
    notify = _Notify()

    observe_stale_trust_key(_stale(), path=path, notify=notify)
    observe_stale_trust_key(_stale(signer="SHA256:other"), path=path, notify=notify)
    observe_stale_trust_key(_stale(tag="v1.41.0", signer="SHA256:other"), path=path, notify=notify)

    assert len(notify.sent) == 3


def test_stale_key_when_delivery_fails_then_the_next_tick_retries(tmp_path: Path) -> None:
    path = tmp_path / "trust-key-notice.txt"
    failing = _Notify(delivered=False)

    observe_stale_trust_key(_stale(), path=path, notify=failing)
    observe_stale_trust_key(_stale(), path=path, notify=failing)

    assert len(failing.sent) == 2
    assert not path.exists()


def test_stale_key_when_the_node_catches_up_then_forgets_so_the_next_rotation_alerts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust-key-notice.txt"
    notify = _Notify()
    observe_stale_trust_key(_stale(), path=path, notify=notify)
    _ = capsys.readouterr()

    observe_stale_trust_key(None, path=path, notify=notify)
    observe_stale_trust_key(_stale(), path=path, notify=notify)

    assert len(notify.sent) == 2
    assert capsys.readouterr().err.count("UPDATE-TRUST-STALE-KEY") == 1


def test_stale_key_when_the_state_directory_is_unwritable_then_the_tick_survives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    blocker = tmp_path / "not-a-directory"
    _ = blocker.write_text("x", encoding="utf-8")

    observe_stale_trust_key(_stale(), path=blocker / "notice.txt", notify=_Notify())

    assert "TRUST-KEY-NOTICE-STATE-FAIL" in capsys.readouterr().err
