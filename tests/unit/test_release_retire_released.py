"""서명 태그로 이미 나간 릴리스 레코드는 카드 반응이 빠져도 실행 완료로 history 에 간다.

2026-10-17 v1.46.1: 소유자 ✅ 로 태그·배포까지 끝난 카드에서 ✅ 반응이 지금 보이지 않자, 다음
release.sh 의 retire 가 반응을 다시 읽어 bound_pending 으로 판정했고 그 레코드는 "낡은 요청"으로
회수되며 카드에 "(취소됨)" 답글이 붙었다. 기존 rollover 시험은 반응 판정만 다루므로 여기에 따로
둔다: 태그 근거(--released)로 history 로 가는지, 이미 잘못 회수된 기록에 정정 답글이 한 번만
붙는지.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation import release_approval, release_released, skill_gate
from automation.interop.approval_types import Probe

_HEAD = "a" * 40
_RECORD = {
    "version": "v1.2.3",
    "head_sha": _HEAD,
    "release_nonce": "b" * 32,
    "surface_digests": json.dumps([["skill:demo", "c" * 64]]),
    "patch_notes": "- demo change",
    "message_id": "111",
    "channel_id": "222",
}


class _Gate:
    def __init__(self, probe: Probe) -> None:
        self._probe = probe

    def outstanding(self, key: str) -> tuple[str, ...]:
        return ("live-request",)

    def probe(self, request: str) -> Probe:
        return self._probe


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe: Probe) -> Path:
    pending = tmp_path / "pending" / "release.json"
    pending.parent.mkdir()
    pending.write_text(json.dumps(_RECORD, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _Gate(probe))
    return pending


def _retire(head: str, tip: str = "", released: str = "") -> int:
    return release_approval.cmd_retire(SimpleNamespace(head=head, tip=tip, released=released))


def test_a_tagged_release_whose_reaction_reads_pending_goes_to_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = _setup(tmp_path, monkeypatch, Probe.BOUND_PENDING)
    before = pending.read_bytes()

    assert _retire(_HEAD, "f" * 40, f"v1.2.3={_HEAD}") == 0

    assert not pending.exists()
    assert (tmp_path / "release-history" / f"{_HEAD}.json").read_bytes() == before
    assert not (tmp_path / "release-abandoned").exists()


def test_an_approved_release_behind_a_newer_tag_is_history_not_abandon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(tmp_path, monkeypatch, Probe.APPROVED)

    assert _retire("d" * 40, "e" * 40, f"v1.2.4={'d' * 40},v1.2.3={_HEAD}") == 0

    assert (tmp_path / "release-history" / f"{_HEAD}.json").exists()
    assert not (tmp_path / "release-abandoned").exists()


def test_without_tag_evidence_a_pending_record_stays_for_the_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = _setup(tmp_path, monkeypatch, Probe.BOUND_PENDING)

    assert _retire(_HEAD, "f" * 40, f"v1.2.3={'9' * 40}") == 0

    assert pending.exists()
    assert not (tmp_path / "release-history").exists()


def test_a_malformed_released_list_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _setup(tmp_path, monkeypatch, Probe.BOUND_PENDING)

    assert _retire(_HEAD, "", "v1.2.3=short") == 4
    assert pending.exists()


def _abandoned(tmp_path: Path, record: dict[str, str], name: str) -> Path:
    root = tmp_path / "release-abandoned"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return path


def test_a_wrongly_abandoned_released_record_gets_one_correction_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    released_one = _abandoned(tmp_path, _RECORD, f"{_HEAD}.000000000001.json")
    _abandoned(tmp_path, {**_RECORD, "version": "v1.2.2", "head_sha": "c" * 40}, f"{'c' * 40}.0002.json")
    posts: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(skill_gate, "_api", lambda method, path, body: posts.append((method, path, body)))

    tags = {"v1.2.3": _HEAD}
    assert release_released.correct_abandoned(tmp_path, tags) == 1
    assert release_released.correct_abandoned(tmp_path, tags) == 0

    assert len(posts) == 1
    method, path, body = posts[0]
    assert (method, path) == ("POST", "/channels/222/messages")
    assert body["message_reference"] == {"message_id": "111", "fail_if_not_exists": True}
    assert "이미 배포" in str(body["content"]) and "취소되지 않" in str(body["content"])
    assert "취소됨" not in str(body["content"])
    assert released_one.with_name(released_one.name + ".corrected").exists()


def test_a_failed_correction_reply_is_retried_next_time(tmp_path: Path) -> None:
    path = _abandoned(tmp_path, _RECORD, f"{_HEAD}.000000000001.json")

    def fail(record: dict[str, str]) -> None:
        raise OSError("discord down")

    assert release_released.correct_abandoned(tmp_path, {"v1.2.3": _HEAD}, fail) == 0
    assert not path.with_name(path.name + ".corrected").exists()


def test_release_passes_the_signed_tag_map_to_retire() -> None:
    source = (Path(__file__).resolve().parents[2] / "automation" / "release.sh").read_text(encoding="utf-8")

    assert '--released "$(released_tags_map "$REPO_ROOT")"' in source
