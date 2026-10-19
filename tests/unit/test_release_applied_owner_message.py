"""릴리스 적용 통지의 봉투·레거시 본문. 판정 순서 회귀는 기존 파일에 둔다."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from automation import owner_notice, release_applied_notice as release
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result

RELEASE = '✅ v1.2.4 적용됨'
HEAD = 'aaaaaaaaaaaaaaaa'


@pytest.fixture
def active_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pointer = tmp_path / 'current'
    pointer.symlink_to(tmp_path / HEAD)
    monkeypatch.setenv('NODE_RELEASE_CURRENT', str(pointer))


@pytest.mark.parametrize('legacy', ['missing_module', 'missing_flag'])
def test_legacy_bytes_when_runtime_is_old(
    active_pointer: None, monkeypatch: pytest.MonkeyPatch, legacy: str,
) -> None:
    # Given
    sent: list[str] = []
    monkeypatch.setattr(owner_notice, 'notify_owner', lambda content, **_kw: sent.append(content) or True)
    if legacy == 'missing_module':
        monkeypatch.setitem(sys.modules, 'automation.interop.owner_message', None)
    else:
        monkeypatch.delattr(owner_notice, 'ACCEPTS_OWNER_MESSAGE')
    # When
    code = release.main(['send', '--version', 'v1.2.4', '--head', HEAD])
    # Then
    assert code == 0
    assert sent == [RELEASE]


def test_envelope_identifies_release_when_pointer_matches(
    active_pointer: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    sent: list[OwnerMessage | None] = []

    def capture(content: str, *, message: OwnerMessage | None = None, fault: bool = False) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(owner_notice, 'notify_owner', capture)
    # When
    code = release.main(['send', '--version', 'v1.2.4', '--head', HEAD])
    # Then
    assert code == 0
    assert len(sent) == 1
    message = sent[0]
    assert message is not None
    assert message == OwnerMessage(
        subject_key='aaaaaaaaaaaaaaaa', subject='릴리스 v1.2.4',
        fact=RELEASE,
        location=Ref(scope='resource', space='unknown', guild_id=None, channel_id=None,
                     message_id=None, url=None, search=('릴리스 검색', 'v1.2.4 aaaaaaaaaaaaaaaa')),
        owner=Action(verb='none', target=None, argument=None),
        agent_next='추가 실행 없음', recovery='not_applicable', detail=Result(outcome='executed'),
        render_version='owner-ko-v5',
    )


def test_notice_destination_is_used_when_notice_channel_is_configured(
    active_pointer: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the actual facade opens its destination; only network edges are replaced.
    monkeypatch.setenv('DISCORD_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('OWNER_NOTICE_CHANNEL_ID', '111')
    monkeypatch.setenv('AUTOPHAGY_OWNER_ID', '333')
    monkeypatch.setattr(owner_notice, 'owner_dm_channel', lambda token, owner: '222')
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(owner_notice, 'send_notice', lambda token, channel, body: sent.append((channel, body)))
    # When
    code = release.main(['send', '--version', 'v1.2.4', '--head', HEAD])
    # Then
    assert code == 0
    assert len(sent) == 1
    channel, body = sent[0]
    assert channel == '111'
    assert body == '✅ v1.2.4 적용됨'
