"""관리형 격리 도착 통지: 호환 본문과 저널 전용 거부."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from automation import owner_notice
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
from automation.managed_sync.cron import managed_sync_watch as watch

STAGED = 'managed-sync: 새 관리형 스킬 릴리스가 격리(quarantine)에 도착했습니다.\n- managed-lab seq=4 digest=aaaaaaaaaaaa\n격리에서 꺼내 마운트하려면 본인의 ✅가 필요합니다 — 자동으로 동작하지 않습니다. 절차는 구독자 매뉴얼을 보세요.'
OUTPUT = 'SYNC-STAGED skill=managed-lab sequence=4 digest=aaaaaaaaaaaaaaaa\n'


@pytest.fixture
def staged_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, 'LEASE_ROOT', tmp_path / 'leases')
    monkeypatch.setattr(watch, 'load_secrets', lambda: {})
    monkeypatch.setattr(watch, 'run_sync_once', lambda: (0, OUTPUT))
    monkeypatch.setattr(watch, 'run_roster_once', lambda: None)


@pytest.mark.parametrize('legacy', ['missing_module', 'missing_flag'])
def test_legacy_bytes_when_runtime_is_old(
    staged_tick: None, monkeypatch: pytest.MonkeyPatch, legacy: str,
) -> None:
    # Given
    sent: list[str] = []
    monkeypatch.setattr(watch, 'notify_owner', lambda content, **_: sent.append(content) or True)
    if legacy == 'missing_module':
        monkeypatch.setitem(sys.modules, 'automation.interop.owner_message', None)
    else:
        monkeypatch.delattr(owner_notice, 'ACCEPTS_OWNER_MESSAGE')
    # When
    code = watch.run_tick()
    # Then
    assert code == 0
    assert sent == [STAGED]


def test_envelope_is_sent_when_release_is_staged(
    staged_tick: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    sent: list[OwnerMessage | None] = []

    def capture(content: str, *, message: OwnerMessage | None = None, fault: bool = False) -> bool:
        sent.append(message)
        return False  # failure must not undo staging

    monkeypatch.setattr(watch, 'notify_owner', capture)
    # When
    code = watch.run_tick()
    # Then
    assert code == 0
    assert len(sent) == 1
    message = sent[0]
    assert message is not None
    assert message == OwnerMessage(
        subject_key='managed-sync', subject='관리형 스킬 격리 도착', fact=STAGED,
        location=Ref(scope='resource', space='unknown', guild_id=None, channel_id=None,
                     message_id=None, url=None,
                     search=('관리형 릴리스', '- managed-lab seq=4 digest=aaaaaaaaaaaa')),
        owner=Action(verb='none', target=None, argument=None),
        agent_next='격리 유지; 자동 마운트 없음', recovery='not_applicable',
        detail=Result(outcome='executed'),
    )


def test_staged_output_when_real_facade_renders(
    staged_tick: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real facade/renderer, with only the external transport replaced.
    monkeypatch.setenv('DISCORD_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('OWNER_NOTICE_CHANNEL_ID', '222')
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(owner_notice, 'send_notice', lambda token, channel, body: sent.append((channel, body)))
    # When
    code = watch.run_tick()
    # Then
    assert code == 0
    assert sent == [('222',
        '대상: 관리형 스킬 격리 도착 (managed-sync)\n'
        '사실: managed-sync: 새 관리형 스킬 릴리스가 격리(quarantine)에 도착했습니다. '
        '- managed-lab seq=4 digest=aaaaaaaaaaaa 격리에서 꺼내 마운트하려면 본인의 ✅가 필요합니다 '
        '— 자동으로 동작하지 않습니다. 절차는 구독자 매뉴얼을 보세요. (실행 완료)\n'
        '위치: 링크 없음 (주소 없음); 검색: 관리형 릴리스 / - managed-lab seq=4 digest=aaaaaaaaaaaa\n'
        '인계: 소유자: 조치 없음; 다음: 격리 유지; 자동 마운트 없음\n'
        '되돌리기: 해당 없음',
    )]


def test_refusal_stays_journal_only_when_sync_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: exercise run_sync_once's actual journal forwarding in the tick.
    import subprocess

    reason = 'SYNC-FAILED skill=managed-lab reason=BAD-SIGNATURE\n'
    monkeypatch.setattr(watch, 'LEASE_ROOT', tmp_path / 'leases')
    monkeypatch.setattr(watch, 'load_secrets', lambda: {})
    monkeypatch.setattr(watch.subprocess, 'run', lambda *args, **kwargs:
                      subprocess.CompletedProcess(args[0], 1, reason, ''))
    sent: list[str] = []
    monkeypatch.setattr(watch, 'notify_owner', lambda content, **kwargs: sent.append(content) or True)
    # When
    code = watch.run_tick()
    # Then
    assert code == 1
    assert capsys.readouterr().out == reason
    assert sent == []
