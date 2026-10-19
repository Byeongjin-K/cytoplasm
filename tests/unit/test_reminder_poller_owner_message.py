"""리마인더 봉투의 출처와 혼합 세대 배달 계약."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

from automation import owner_notice
from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref, Result
from automation.reminder_poller import poll_reminders, poller_core

EVENT = '⏰ 일정 리마인더: 약 60분 후 「실험 미팅」 시작 — 2026-07-15 13:00 KST'
MILESTONE = '📌 마일스톤 D-1: 논문 제출 (마감 2026-07-16)'
NOW = datetime.fromisoformat('2026-07-15T12:00:00+09:00')


@pytest.fixture
def poll_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = tmp_path / 'events.json'
    events.write_text(json.dumps({'items': [{
        'id': 'evt1', 'summary': '실험 미팅',
        'htmlLink': 'https://calendar.example.test/events/evt1',
        'start': {'dateTime': '2026-07-15T13:00:00+09:00'},
    }]}), encoding='utf-8')
    milestones = tmp_path / 'milestones.yaml'
    milestones.write_text(
        'milestones:\n  - title: "논문 제출"\n    deadline: "2026-07-16"\n'
        '    source: "meeting:sample.md"\n', encoding='utf-8',
    )
    monkeypatch.setenv('REMINDER_EVENTS_FILE', str(events))
    monkeypatch.setenv('REMINDER_MILESTONES_FILE', str(milestones))
    monkeypatch.setenv('REMINDER_DB', str(tmp_path / 'reminders.db'))
    monkeypatch.setenv('REMINDER_NOW', NOW.isoformat())
    monkeypatch.setenv('REMINDER_CALENDAR_EVENTS', '1')  # the node calendar leg is off by default (Q276)
    monkeypatch.setenv('DISCORD_BOT_TOKEN', 'test-token')
    monkeypatch.delenv('REMINDER_DRY_RUN', raising=False)


@pytest.mark.parametrize('legacy', ['missing_module', 'missing_flag'])
def test_legacy_bytes_when_runtime_is_old(
    poll_input: None, monkeypatch: pytest.MonkeyPatch, legacy: str,
) -> None:
    # Given: a literal captured from the base, not recomputed by a formatter.
    sent: list[str] = []
    monkeypatch.setattr(owner_notice, 'notify_owner', lambda content: sent.append(content) or True)
    if legacy == 'missing_module':
        monkeypatch.setitem(sys.modules, 'automation.interop.owner_message', None)
    else:
        monkeypatch.delattr(owner_notice, 'ACCEPTS_OWNER_MESSAGE')
    # When
    code = poll_reminders.main()
    # Then
    assert code == 0
    assert sent == [EVENT, MILESTONE]


def test_envelopes_keep_sources_when_poll_is_due(
    poll_input: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    sent: list[OwnerMessage | None] = []

    def capture(content: str, *, message: OwnerMessage | None = None) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(owner_notice, 'notify_owner', capture)
    # When
    code = poll_reminders.main()
    # Then
    assert code == 0
    assert len(sent) == 2
    event, milestone = sent
    assert event is not None and milestone is not None
    assert event == OwnerMessage(
        subject_key='evt1|2026-07-15T13:00:00+09:00', subject='리마인더', fact=EVENT,
        location=Ref(scope='resource', space='unknown', guild_id=None, channel_id=None,
                     message_id=None, url='https://calendar.example.test/events/evt1',
                     search=('일정 검색', 'evt1')),
        owner=Action(verb='none', target=None, argument=None),
        agent_next='다음 틱에 새 대상 확인', recovery='not_applicable',
        detail=Periodic(datetime.fromisoformat('2026-07-15T12:00:00+09:00'),
                        datetime.fromisoformat('2026-07-15T13:30:00+09:00')),
        render_version='owner-ko-v5',
    )
    assert milestone == OwnerMessage(
        subject_key='논문 제출|2026-07-16|D-1', subject='리마인더', fact=MILESTONE,
        location=Ref(scope='resource', space='unknown', guild_id=None, channel_id=None,
                     message_id=None, url=None, search=('마일스톤 출처', 'meeting:sample.md')),
        owner=Action(verb='none', target=None, argument=None),
        agent_next='다음 틱에 새 대상 확인', recovery='not_applicable',
        detail=Result(outcome='executed'), render_version='owner-ko-v5',
    )


@pytest.mark.parametrize('url', [None, 5, 'https://calendar.example.test/events/evt1'])
def test_source_url_is_parsed_when_calendar_returns_optional_link(url: str | int | None) -> None:
    # Given
    payload = json.dumps({'items': [{'id': 'evt1', 'htmlLink': url,
        'start': {'dateTime': '2026-07-15T13:00:00+09:00'}}]})
    # When
    event = poller_core.parse_events(payload)[0]
    # Then
    assert event.source_url == (url if isinstance(url, str) else None)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    bodies: list[str] = []
    monkeypatch.setattr(owner_notice, 'owner_notice_channel', lambda: '222')

    def capture(token: str, channel_id: str, body: str) -> None:
        bodies.append(body)

    monkeypatch.setattr(owner_notice, 'send_notice', capture)
    return bodies


def test_source_is_visible_when_real_facade_renders(
    poll_input: None, transport: list[str],
) -> None:
    # Given: real facade and renderer; only the transport is injected.
    # When
    code = poll_reminders.main()
    # Then
    assert code == 0
    assert transport == [  # owner-ko-v5 since 2026-10-19: one line, the envelope stays in the record
        '⏰ 일정 리마인더: 약 60분 후 「실험 미팅」 시작 — 2026-07-15 13:00 KST'
        ' · <https://calendar.example.test/events/evt1>',
        '📌 마일스톤 D-1: 논문 제출 (마감 2026-07-16)',
    ]


def test_next_tick_retries_when_real_facade_send_failed(
    poll_input: None, monkeypatch: pytest.MonkeyPatch, transport: list[str],
) -> None:
    # Given: one failed attempt through the real facade releases the SQLite claim.
    with monkeypatch.context() as failed:
        def refuse(token: str, channel_id: str, body: str) -> None:
            raise OSError('injected transport failure')
        failed.setattr(owner_notice, 'send_notice', refuse)
        with pytest.raises(RuntimeError, match='owner notice delivery failed'):
            poll_reminders.main()
    # When
    code = poll_reminders.main()
    # Then
    assert code == 0
    assert len(transport) == 2
    assert 'https://calendar.example.test/events/evt1' in transport[0]


def test_cli_runs_when_only_flat_reminder_runtime_is_deployed(
    poll_input: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: only the deployer's payload supplies the flat runtime, not this fixture.
    import subprocess

    source = Path(poll_reminders.__file__).parent
    repo = source.parents[1]
    interop = tmp_path / '.hermes' / 'interop_runtime' / 'automation'
    interop.mkdir(parents=True)
    (interop / '__init__.py').write_text('', encoding='utf-8')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('PYTHONPATH', str(interop.parent))
    monkeypatch.setenv('REMINDER_DRY_RUN', '1')
    # Run the deployer's payload verbatim, replacing only SSH/account switching locally.
    deploy = (source / 'deploy.sh').read_text(encoding='utf-8')
    payload = deploy[deploy.index('source "$repo_root/automation/deploy_tree.sh"'):]
    local = 'run_account() { bash -c "$2"; }\n' + payload
    from tests.unit.cron_fixture import declared_cron, listing_only_hermes, timer_env
    hermes = tmp_path / '.local' / 'bin' / 'hermes'
    hermes.parent.mkdir(parents=True)
    hermes.write_text(listing_only_hermes(declared_cron('automation/reminder_poller')), encoding='utf-8')
    hermes.chmod(0o755)
    subprocess.run(['bash', '-euo', 'pipefail', '-c', local], check=True,
                   env={**os.environ, 'repo_root': str(repo), 'NODE_AGENT_ACCOUNT': 'agent',
                        **timer_env(tmp_path / 'timer')},
                   capture_output=True, text=True, timeout=10)
    wrapper = tmp_path / '.hermes' / 'scripts' / 'poll_reminders.py'
    # When
    result = subprocess.run([sys.executable, str(wrapper)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10, check=False)
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == (
        'DRY-RUN ⏰ 일정 리마인더: 약 60분 후 「실험 미팅」 시작 — 2026-07-15 13:00 KST\n'
        'DRY-RUN 📌 마일스톤 D-1: 논문 제출 (마감 2026-07-16)\n'
    )
