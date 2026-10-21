"""V7 regular-notice migration coverage, separated from FS3-pinned DM inventory tests.

These tests exercise only final stub transports: configured
``owner_notice_channel_id`` selects the notice channel, while its absence selects
owner DM. Patent export deliberately is not covered: its completion notice
contains a private patent Drive link and must remain DM-only.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "procurement" / "scripts"))

from automation import owner_notice  # noqa: E402
from automation.selfskill_audit import report  # noqa: E402

procure_review = importlib.import_module("procure_review")


@pytest.fixture
def notice_attempts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    attempts: list[tuple[str, str]] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setattr(owner_notice, "_config_owner_id", lambda: "owner-1")
    monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda _token, _owner: "owner-dm")
    monkeypatch.setattr(
        owner_notice,
        "send_notice",
        lambda _token, channel_id, body: attempts.append((channel_id, body)),
    )
    return attempts


@pytest.mark.parametrize(
    ("channel", "expected_target"),
    (("notice-channel", "notice-channel"), ("", "owner-dm")),
)
def test_procurement_review_uses_facade_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
    channel: str,
    expected_target: str,
) -> None:
    """Attachment delivery stays inside owner_notice — target AND transport."""
    del notice_attempts
    target = tmp_path / "review.txt"
    _ = target.write_text("review", encoding="utf-8")
    posted: list[tuple[str, tuple[Path, ...]]] = []
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: channel)
    monkeypatch.setattr(
        owner_notice,
        "send_notice_files",
        lambda _token, channel_id, _body, attachments: posted.append((channel_id, tuple(attachments))),
        raising=False,
    )

    procure_review.send_review(target, "검토")

    assert posted == [(expected_target, (target,))]


@pytest.mark.parametrize(
    ("channel", "expected_target"),
    (("notice-channel", "notice-channel"), ("", "owner-dm")),
)
def test_selfskill_report_uses_facade_transport(
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
    channel: str,
    expected_target: str,
) -> None:
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: channel)

    assert report.send_report((), account_label="agent") is True

    assert len(notice_attempts) == 1
    assert notice_attempts[0][0] == expected_target
    assert "[자체 스킬 감사]" in notice_attempts[0][1]
