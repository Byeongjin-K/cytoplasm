"""New read-command cases are separate from FS3-pinned gate tests; network is injected."""
from __future__ import annotations

import json
import http.client
import urllib.error

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate


def test_release_notes_builds_and_prints_public_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a strict tag and an offline API body.
    tag = "v12.34.56"
    expected = "# 변경\n전체 릴리스 노트"
    assert gate.build(["release-notes", tag]) == ("release-notes", [tag], gate.HOME)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", f"release-notes {tag}")
    read = gate.release_notes
    monkeypatch.setattr(gate, "release_notes", lambda value: read(
        value, fetcher=lambda requested: json.dumps({"body": expected if requested == tag else ""}).encode(),
    ))
    # When
    rc = gate.main()
    # Then
    assert rc == 0
    assert capsys.readouterr().out == expected + "\n"


@pytest.mark.parametrize("args", [
    [], ["v1.2"], ["v1.2.3", "extra"], ["v1000.1.2"], ["v1.2.3/evil"],
    ["v1.2.3\n"], ["v１.2.3"], ["v1.2.3-rc1"],
])
def test_release_notes_refuses_noncanonical_tags(args: list[str]) -> None:
    # Given / When / Then: malformed tags never reach a fetcher.
    with pytest.raises(gate.Refused):
        gate.build(["release-notes", *args])


@pytest.mark.parametrize("raw", [
    b"{}", b"[]", b'{"body":null}', b'{"body":""}', b"invalid",
    b"x" * (gate.RELEASE_NOTES_LIMIT + 1),
])
def test_release_notes_refuses_invalid_or_oversized_responses(raw: bytes) -> None:
    # Given / When / Then
    with pytest.raises(gate.Refused, match="release notes unavailable"):
        gate.release_notes("v1.2.3", fetcher=lambda tag: raw)


@pytest.mark.parametrize("error", [
    OSError("offline"), urllib.error.HTTPError("test", 404, "missing", None, None),
    http.client.IncompleteRead(b"partial"),
])
def test_release_notes_network_failure_returns_gate_rc_two(
    error: OSError | http.client.HTTPException,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    def fail(tag: str) -> bytes:
        raise error

    read = gate.release_notes
    monkeypatch.setattr(gate, "release_notes", lambda tag: read(tag, fetcher=fail))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "release-notes v1.2.3")
    # When
    rc = gate.main()
    # Then
    assert rc == 2
    assert capsys.readouterr().err == "YOODORI-GATE-REFUSED release notes unavailable\n"
