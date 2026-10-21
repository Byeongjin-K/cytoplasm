"""VA-1 release approval binding: one version, HEAD, and complete surface digest set."""
from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import pytest

from automation import release_approval, release_spec, skill_gate, skill_gate_request
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    required_surface,
)
from automation.release_approval import (
    DECISION_APPROVED,
    DECISION_DENIED,
    DECISION_PENDING,
    DECISION_UNAVAILABLE,
    decision_exit,
    spec_from_plan,
    spec_from_record,
)
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRequest,
    ApprovalSurfaceError,
    PostedApproval,
    Probe,
)
from automation.release_card import (
    CARD_REFUSED_PREFIX,
    card_for_new_request,
)
from automation.release_spec import (
    ReleaseSpec,
    ReleaseSpecError,
)
from automation.release_spec_message import MESSAGE_LIMIT
from automation.skill_gate_approval import GateSurface, SkillApprovalGate
from automation.skill_gate_surface import JsonValue


def _spec() -> ReleaseSpec:
    return ReleaseSpec(
        version="v1.2.3",
        head_sha="a" * 40,
        release_nonce="b" * 32,
        surface_digests=(
            ("home:skills/mail", "c" * 64),
            ("skill:meeting", "d" * 64),
        ),
        patch_notes="- mail wrapper\n- meeting skill",
    )


_MESSAGE_ID = "1538547247604525816"
_REFUSAL = "REFUSED: approval request not posted outcome=deferred reason=binding-mismatch"


def _binding() -> ApprovalBinding:
    return ApprovalBinding(
        ApprovalKind.RELEASE, ApprovalSurface.SKILL_APPROVALS, "999", POLICY_VERSION
    )


def _pending(gate_dir: Path) -> dict[str, str]:
    record = _spec().new_record(_MESSAGE_ID, _binding())
    path = gate_dir / "pending" / "release.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return record


def _plan_file(
    tmp_path: Path,
    patch_notes: str = "",
    surface_digests: tuple[tuple[str, str], ...] = (),
    major_note: str = "",
) -> str:
    spec = _spec()
    path = tmp_path / "plan.json"
    _ = path.write_text(
        json.dumps(
            {
                "version": spec.version,
                "head": spec.head_sha,
                "surface_digests": [
                    list(row) for row in (surface_digests or spec.surface_digests)
                ],
                "patch_notes": patch_notes or spec.patch_notes,
                "major_note": major_note,
            }
        ),
        encoding="utf-8",
    )
    return str(path)


_LONG_NOTES = "\n".join(f"- 변경 {index} " + "가" * 60 for index in range(60))

#: `_spec()` 의 action_hash — 렌더러가 아니라 바인딩 입력에서 나오는 독립 상수다.
_ACTION_HASH = "980a7cea657cf3ff0328e212ae6b14a699fff8b5f6ba739cd9401be7994c2801"
#: 이미 게시된 판본의 바이트. 기준 fc1c7a0f8 에서 그대로 떠 왔고, 여기서 다시 렌더하지 않는다.
_V1_POSTED = (
    "[release] v1.2.3 배포 승인 요청\n"
    "- version: `v1.2.3`\n"
    "- HEAD: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- release_nonce: `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`\n"
    "- surface `home:skills/mail`: `cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc`\n"
    "- surface `skill:meeting`: `dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd`\n"
    "- 패치노트:\n"
    "- mail wrapper\n"
    "- meeting skill\n"
    "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
)
_V2_POSTED = (
    "[release] v1.2.3 배포 승인 요청\n"
    "- 배포 기준: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- 배포 번들 (2): `home:skills/mail`, `skill:meeting`\n"
    "- 승인 바인딩: `980a7cea657cf3ff0328e212ae6b14a699fff8b5f6ba739cd9401be7994c2801`\n"
    "- 변경 내용:\n"
    "- mail wrapper\n"
    "- meeting skill\n"
    "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
)
#: 봉투 판본(v4)의 바이트. 다섯 필드 · 한 필드 한 줄이며, 기계 판독 줄은 이 카드에 없다.
_V4_POSTED = (
    "대상: 릴리스 배포 승인 (release:v1.2.3)\n"
    "사실: 배포 기준 `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`;"
    " 배포 번들 (2): `home:skills/mail`, `skill:meeting`;"
    " 승인 바인딩 `980a7cea657cf3ff0328e212ae6b14a699fff8b5f6ba739cd9401be7994c2801`;"
    " 변경 상세 아래 1개 메시지 (같은 릴리스 v1.2.3 · 기준 aaaaaaaaaaaa)"
    " (승인 요청; 만료: 기한 없음)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨);"
    " 다음: 승인된 릴리스만 태그·배포\n"
    "되돌리기: 해당 없음; 취소 시: 배포 미실행, 요청 폐기"
)
_MAJOR_NOTE = "MAJOR: 운영자 조치 필요 — automation/x.py:SCHEMA_VERSION"
_V4_MAJOR_POSTED = (
    "대상: 릴리스 배포 승인 (release:v1.2.3)\n"
    "사실: 배포 기준 `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`;"
    " 배포 번들 (2): `home:skills/mail`, `skill:meeting`;"
    " 승인 바인딩 `980a7cea657cf3ff0328e212ae6b14a699fff8b5f6ba739cd9401be7994c2801`;"
    " 변경 상세 아래 1개 메시지 (같은 릴리스 v1.2.3 · 기준 aaaaaaaaaaaa);"
    " MAJOR: 운영자 조치 필요 — automation/x.py:SCHEMA_VERSION"
    " (승인 요청; 만료: 기한 없음)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 ✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨);"
    " 다음: 승인된 릴리스만 태그·배포\n"
    "되돌리기: 해당 없음; 취소 시: 배포 미실행, 요청 폐기"
)
_V3_POSTED = (
    "[release] v1.2.3 배포 승인 요청\n"
    "- 배포 기준: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- 배포 번들 (2): `home:skills/mail`, `skill:meeting`\n"
    "- 승인 바인딩: `980a7cea657cf3ff0328e212ae6b14a699fff8b5f6ba739cd9401be7994c2801`\n"
    "- 변경 상세: 아래 1개 메시지 (같은 릴리스 v1.2.3 · 기준 aaaaaaaaaaaa)\n"
    "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
)
_V5_POSTED = (
    "**🔔 릴리스 v1.2.3 배포 승인**\n"
    "> 배포 기준: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "> 배포 묶음 (2): `home:skills/mail`, `skill:meeting`\n"
    "> 승인 식별값: `980a7cea657cf3ff0328e212ae6b14a699fff8b5f6ba739cd9401be7994c2801`\n"
    "> 변경 상세: 아래 1개 메시지 (같은 릴리스 v1.2.3 · 기준 aaaaaaaaaaaa)\n"
    "\n"
    "**결정:** 이 메시지에 ✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨)"
    " · 만료 없음\n"
    "취소 시: 배포 미실행, 요청 폐기\n"
    "다음: 승인된 릴리스만 태그·배포\n"
    "-# 참조: `release:`"
)


#: The release thread the fake wire opens under the approval channel 222.
_THREAD_ID: Final = "333333333333333333"


class _DetailWire:
    """실제 producer가 쓰는 gate API 모양만 기록하는 오프라인 Discord."""

    def __init__(
        self,
        *,
        guild_id: str | None = "111111111111111111",
        fail_post_after: int | None = None,
        first_post_id: str | None = None,
    ) -> None:
        self.guild_id = guild_id
        self.fail_post_after = fail_post_after
        self.first_post_id = first_post_id
        self.posts: list[dict[str, JsonValue]] = []
        self.threads: list[tuple[str, dict[str, JsonValue]]] = []
        self.existing_threads: list[dict[str, JsonValue]] = []
        self.patches: list[tuple[str, dict[str, JsonValue]]] = []
        self.deletes: list[str] = []

    def api(
        self, method: str, path: str, payload: dict[str, JsonValue] | None = None
    ) -> JsonValue:
        if method == "GET" and "/threads" in path:
            return {"threads": [dict(thread) for thread in self.existing_threads
                                if ("/archived/" in path) == bool(thread.get("archived"))]}
        if method == "GET":
            channel: dict[str, JsonValue] = {"id": "222"}
            if self.guild_id is not None:
                channel["guild_id"] = self.guild_id
            return channel
        if method == "POST" and path.endswith("/threads"):
            assert payload is not None
            self.threads.append((path, payload))
            return {"id": _THREAD_ID}
        if method == "POST":
            assert payload is not None
            if (
                self.fail_post_after is not None
                and len(self.posts) >= self.fail_post_after
            ):
                raise OSError("discord is unreachable")
            self.posts.append(payload)
            if len(self.posts) == 1 and self.first_post_id is not None:
                return {"id": self.first_post_id}
            return {"id": str(700000000000000000 + len(self.posts))}
        if method == "PATCH":
            assert payload is not None
            self.patches.append((path, payload))
            return {"id": path.rsplit("/", 1)[-1]}
        if method == "DELETE":
            self.deletes.append(path)
            return None
        if method == "PUT":
            return None
        raise AssertionError(f"unexpected wire call: {method} {path}")

    def gate_api(
        self, method: str, path: str, payload: dict[str, str] | None = None
    ) -> object:
        wire_payload: dict[str, JsonValue] | None = (
            None if payload is None else dict(payload)
        )
        return self.api(method, path, wire_payload)


@dataclass(frozen=True, slots=True)
class _Bindings:
    kind: ApprovalKind = ApprovalKind.RELEASE

    def new(self) -> ApprovalBinding:
        return ApprovalBinding(
            ApprovalKind.RELEASE, ApprovalSurface.SKILL_APPROVALS, "222", POLICY_VERSION
        )

    def stored(self, record: Mapping[str, str]) -> ApprovalBinding:
        del record
        return self.new()


def _actual_gate(
    gate_dir: Path, wire: _DetailWire
) -> Callable[[ReleaseSpec], SkillApprovalGate]:
    surface = GateSurface(
        wire.gate_api,
        gate_dir,
        lambda: "111111111111111111",
        _Bindings,
    )
    return lambda spec: SkillApprovalGate(surface, spec)


@dataclass(frozen=True, slots=True)
class _GateDir:
    """`post_request` 가 리스·저널 경로를 얻는 표면 — 디렉터리 하나면 충분하다."""

    gate_dir: Path


class _LifecycleGate:
    """실제 lifecycle 을 그대로 태우는 게이트 — 게시·레코드·저널만 관찰한다."""

    def __init__(self, gate_dir: Path, spec: ReleaseSpec) -> None:
        self.surface = _GateDir(gate_dir)
        self.spec = spec
        self.posted: list[str] = []

    def channel_id(self) -> str:
        return "222"

    def path(self) -> Path:
        return self.surface.gate_dir / "pending" / "release.json"

    def stored(self) -> dict[str, str] | None:
        return None

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        return ()

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        self.posted.append(intent.action_hash)
        return PostedApproval(message_id=_MESSAGE_ID, channel_id=intent.channel_id)

    def new_record(self, posted: PostedApproval) -> dict[str, str]:
        return self.spec.new_record(posted.message_id, _binding())

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        path = self.path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ = path.write_text(
            self.spec.serialize(self.new_record(posted)), encoding="utf-8"
        )


class _StubGate:
    """저장된 레코드의 프로브만 흉내낸다 — Discord 도, 실제 게이트 상태도 건드리지 않는다."""

    def __init__(self, probe: Probe) -> None:
        self._probe = probe

    def outstanding(self, key: str) -> tuple[str, ...]:
        return ("live",)

    def probe(self, request: str) -> Probe:
        return self._probe


def _refused(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    monkeypatch.setattr(skill_gate_request, "reuse", lambda gate: None)
    monkeypatch.setattr(
        skill_gate_request,
        "post_request",
        lambda gate, *, fresh: skill_gate_request.Requested(None, 6, message),
    )


def test_the_abandon_subcommand_delegates_to_the_audited_release_abandon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """워크스테이션에는 게이트 상태가 없다 — 같은 producer 표면으로 감사된 abandon 에 닿는다."""
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")
    monkeypatch.setenv("SUDO_USER", "cha")

    exit_code = release_approval.main(
        [
            "abandon",
            "--version",
            record["version"],
            "--head",
            record["head_sha"],
            "--message-id",
            record["message_id"],
            "--reason",
            "stale pending release superseded by origin/main advance",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "RELEASE-ABANDONED" in captured.out
    assert not (tmp_path / "pending" / "release.json").exists()
    audited = [
        json.loads(line)
        for line in (tmp_path / "logs" / "approval-abandons.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [line["event"] for line in audited] == ["release-abandon"]
    assert audited[0]["actor"] == "cha"
    assert audited[0]["head_sha"] == record["head_sha"]
    assert [path.name for path in (tmp_path / "release-abandoned").iterdir()]


def test_a_binding_mismatch_refusal_names_the_blocking_record_and_its_own_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """release.sh 의 자동 복구는 이 한 줄만 읽는다 — 거절 메시지와 종료 코드는 그대로다."""
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.BOUND_PENDING))
    _refused(monkeypatch, _REFUSAL)

    exit_code = release_approval.main(["request", "--plan-file", _plan_file(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 6
    lines = captured.err.splitlines()
    assert _REFUSAL in lines
    assert (
        f"RELEASE-REQUEST-STALE: version={record['version']} head={record['head_sha']}"
        f" message_id={record['message_id']} probe=bound_pending"
    ) in lines


@pytest.mark.parametrize(
    ("message", "stored"),
    (
        (_REFUSAL, False),  # 레코드를 읽을 수 없으면 아무 것도 덧붙이지 않는다
        ("REFUSED: approval request not posted outcome=deferred reason=owner-decided", True),
    ),
)
def test_only_a_readable_binding_mismatch_earns_the_extra_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    message: str,
    stored: bool,
) -> None:
    if stored:
        _ = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.BOUND_PENDING))
    _refused(monkeypatch, message)

    exit_code = release_approval.main(["request", "--plan-file", _plan_file(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 6
    assert message in captured.err.splitlines()
    assert "RELEASE-REQUEST-STALE:" not in captured.err


def test_decision_names_the_version_bound_to_the_current_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A caller can reuse a live request's immutable version without changing its record."""
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.APPROVED))

    exit_code = release_approval.main(["decision", "--head", record["head_sha"]])

    captured = capsys.readouterr()
    assert exit_code == DECISION_APPROVED
    assert captured.err == f"RELEASE-DECISION: approved version={record['version']}\n"


_TIP = "e" * 40
_OTHER_RELEASE = "f" * 40


def _never_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        release_approval,
        "notify_stale_approval",
        lambda record, tip: pytest.fail("실행된 릴리스는 소유자에게 경고하지 않는다"),
    )


def test_an_executed_release_awaiting_retirement_is_not_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """✅ 를 받고 태그까지 잘린 릴리스의 레코드는 낡은 것이 아니다 — 회수를 기다릴 뿐이다.

    2026-09-10 소유자 관측: "릴리스 v1.6.7 가 적용되었습니다" 와 "⛔ … 자동 완결할 수
    없습니다" 가 함께 왔다. 실측 원인은 완결기가 **origin/main 팁**을 들고 결정을 물었기
    때문이다 — v1.6.7(4c3250aa7b17)은 09-09 14:24 에 완결되고 14:27 에 적용 통지까지
    나갔는데, 그 뒤 팁이 389c555b3 → 8aef847ab 로 전진하자 매 틱이 HEAD 불일치로 서면서
    ⛔ 를 한 번 냈다. 그 문구는 거짓이다: 그 릴리스는 자동 완결에 성공했다.

    그래서 완결기가 "그 sha 는 이미 잘린 릴리스"라는 사실(`--tagged`)을 함께 주면, 이
    경로는 경고하지 않고 판독용 한 줄만 남긴다. 승인 의미는 그대로다 — 옛 ✅ 로 새 팁을
    인가하는 문은 여전히 잠겨 있고(rc 는 변함없이 UNAVAILABLE), 회수는 다음 release.sh 의
    감사 회수가 한다.
    """
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    _never_notify(monkeypatch)
    monkeypatch.setattr(
        release_approval,
        "_gate",
        lambda spec: pytest.fail("실행된 릴리스는 Discord 를 다시 조회하지 않는다"),
    )

    exit_code = release_approval.main(
        ["decision", "--head", _TIP, "--notify-stale", "--tagged", record["head_sha"]]
    )

    assert exit_code == DECISION_UNAVAILABLE
    assert capsys.readouterr().err == (
        f"RELEASE-DECISION: executed release {record['head_sha'][:12]} awaiting retirement\n"
    )


def test_a_record_bound_to_an_untagged_head_still_warns_the_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """진짜 낡은 승인 — 승인은 받았지만 태그가 잘리지 않은 채 팁이 지나간 경우는 그대로 알린다."""
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.APPROVED))
    warned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        release_approval,
        "notify_stale_approval",
        lambda stored, tip: warned.append((stored["head_sha"], tip)),
    )

    exit_code = release_approval.main(
        ["decision", "--head", _TIP, "--notify-stale", "--tagged", _OTHER_RELEASE]
    )

    assert exit_code == DECISION_UNAVAILABLE
    assert warned == [(record["head_sha"], _TIP)]
    assert "live request is bound to a different HEAD" in capsys.readouterr().err


def test_without_the_tagged_fact_the_warning_path_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--tagged` 없는 호출(release.sh 의 대기 루프)은 바이트 그대로 예전 경로다."""
    record = _pending(tmp_path)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.APPROVED))
    warned: list[str] = []
    monkeypatch.setattr(
        release_approval, "notify_stale_approval", lambda stored, tip: warned.append(tip)
    )

    exit_code = release_approval.main(["decision", "--head", _TIP, "--notify-stale"])

    assert exit_code == DECISION_UNAVAILABLE
    assert warned == [_TIP]
    assert "live request is bound to a different HEAD" in capsys.readouterr().err
    assert record["head_sha"] != _TIP


def test_release_kind_is_permanently_routed_to_approvals() -> None:
    assert ApprovalKind.RELEASE.value == "release"
    assert required_surface(ApprovalKind.RELEASE) is ApprovalSurface.SKILL_APPROVALS


def test_action_hash_binds_version_head_and_sorted_surface_digests() -> None:
    spec = _spec()
    reordered = replace(spec, surface_digests=tuple(reversed(spec.surface_digests)))

    assert spec.action_hash() == reordered.action_hash()
    assert spec.action_hash() != replace(spec, version="v1.2.4").action_hash()
    assert spec.action_hash() != replace(spec, head_sha="e" * 40).action_hash()
    assert spec.action_hash() != replace(
        spec,
        surface_digests=(("skill:meeting", "f" * 64),),
    ).action_hash()


def test_action_hash_excludes_the_random_nonce() -> None:
    spec = _spec()

    assert spec.action_hash() == replace(spec, release_nonce="0" * 32).action_hash()


def test_record_persists_every_authorizing_field_and_surface_binding() -> None:
    spec = _spec()
    binding = ApprovalBinding(
        ApprovalKind.RELEASE,
        ApprovalSurface.SKILL_APPROVALS,
        "1528936606946122421",
        POLICY_VERSION,
    )

    record = spec.new_record("1538547247604525816", binding)

    assert record["version"] == spec.version
    assert record["head_sha"] == spec.head_sha
    assert record["release_nonce"] == spec.release_nonce
    assert record["surface_digests"]
    assert record["kind"] == "release"
    assert record["surface"] == "skill-approvals"
    assert record["channel_id"] == binding.channel_id
    assert record["policy_version"] == str(POLICY_VERSION)
    assert record["render_version"] == "8"
    assert spec.bound(spec.render(), record)


def test_any_record_or_message_change_breaks_the_binding() -> None:
    spec = _spec()
    binding = ApprovalBinding(
        ApprovalKind.RELEASE,
        ApprovalSurface.SKILL_APPROVALS,
        "1528936606946122421",
        POLICY_VERSION,
    )
    record = spec.new_record("1538547247604525816", binding)

    assert not spec.bound(spec.render() + "\nchanged", record)
    assert not spec.bound(spec.render(), {**record, "head_sha": "0" * 40})


def test_release_message_is_fail_closed_above_1900_characters() -> None:
    """카드 자체가 한도를 넘으면 게시하지 않는다 — 잘라내기는 어디에도 없다."""
    with pytest.raises(ReleaseSpecError, match="1900"):
        _ = replace(_spec(), major_note="MAJOR: " + "가" * 2000).render()


def test_the_card_points_at_the_detail_messages_instead_of_carrying_them() -> None:
    """Given a v3 release spec / When the card renders / Then it names the bundles,
    the binding and how many detail messages follow — and no patch-note line."""
    spec = replace(_spec(), render_version=3)

    lines = spec.render().splitlines()

    assert lines[:5] == [
        "[release] v1.2.3 배포 승인 요청",
        f"- 배포 기준: `{spec.head_sha}`",
        "- 배포 번들 (2): `home:skills/mail`, `skill:meeting`",
        f"- 승인 바인딩: `{spec.action_hash()}`",
        f"- 변경 상세: 아래 1개 메시지 (같은 릴리스 v1.2.3 · 기준 {spec.head_sha[:12]})",
    ]
    assert lines[5].startswith("- 승인 방법:")
    assert len(lines) == 6
    assert spec.patch_notes not in spec.render()
    assert spec.release_nonce not in spec.render()
    assert all(digest not in spec.render() for _name, digest in spec.surface_digests)


def test_a_major_release_card_carries_the_operator_line_before_the_detail_pointer() -> None:
    """Given machine-contract signals / When the card renders /
    Then the operator line stands above the detail pointer."""
    spec = replace(
        _spec(), render_version=3, major_note=_MAJOR_NOTE
    )

    lines = spec.render().splitlines()

    assert lines[4] == spec.major_note
    assert lines[5].startswith("- 변경 상세: ")


def test_the_card_counts_every_detail_message_it_points_at() -> None:
    """Given change notes longer than one message / When the card renders /
    Then its count equals the messages the same spec produces."""
    spec = replace(
        _spec(),
        render_version=3,
        patch_notes="\n".join(f"- 변경 {index} " + "가" * 60 for index in range(60)),
    )

    messages = spec.detail_messages()

    assert len(messages) > 1
    assert f"- 변경 상세: 아래 {len(messages)}개 메시지 " in spec.render()
    assert f"변경 상세 아래 {len(messages)}개 메시지 " in replace(
        spec, render_version=4
    ).render()


@pytest.mark.parametrize(
    ("version", "posted"),
    (
        (1, _V1_POSTED),
        (2, _V2_POSTED),
        (3, _V3_POSTED),
        (4, _V4_POSTED),
        (5, _V5_POSTED),
    ),
)
def test_the_frozen_renders_stay_byte_identical(version: int, posted: str) -> None:
    """이미 게시된 승인은 그 바이트로만 재검증된다 — 옛 판본 문구는 영구 동결이다."""
    assert replace(_spec(), render_version=version).render() == posted


@pytest.mark.parametrize(
    ("version", "posted"),
    (
        (1, _V1_POSTED),
        (2, _V2_POSTED),
        (3, _V3_POSTED),
        (4, _V4_POSTED),
        (5, _V5_POSTED),
    ),
)
def test_a_stored_record_replays_its_own_version_and_stays_bound(
    version: int, posted: str
) -> None:
    """소유자가 이미 누른 카드: 레코드가 적은 판본이 그대로 재생되고 프로브가 받아들인다."""
    spec = replace(_spec(), render_version=version)
    record = spec.new_record(_MESSAGE_ID, _binding())

    replay = spec_from_record(record)

    assert record["render_version"] == str(version)
    assert replay.render() == posted
    assert spec.bound(posted, record)
    assert not spec.bound(posted + " ", record)


def test_a_bound_request_is_reused_without_ever_calling_the_renderer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """살아 있는 카드는 그 바이트에 소유자 결정이 묶여 있다 — 재사용은 렌더러를 부르지 않는다.

    재사용이 재렌더에 의존하면 렌더 실패 한 번이 이미 유효한 바인딩을 거절로 바꾼다.
    """
    record = _pending(tmp_path)
    rendered: list[int] = []

    def _observed(self: ReleaseSpec) -> str:
        rendered.append(self.render_version)
        raise AssertionError("the bound reuse path must not render the card")

    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(ReleaseSpec, "render", _observed)

    exit_code = release_approval.main(["request", "--plan-file", _plan_file(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert rendered == []
    assert json.loads(captured.out)["message_id"] == record["message_id"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("version", "latest"),
        ("head_sha", "not-a-sha"),
        ("release_nonce", "short"),
        ("surface_digests", (("skill:mail", "short"),)),
    ),
)
def test_malformed_authorizing_fields_are_refused(field: str, value: object) -> None:
    with pytest.raises(ReleaseSpecError):
        _ = replace(_spec(), **{field: value})


def test_a_stored_record_replays_into_the_same_action_hash() -> None:
    """세션이 죽은 뒤의 decision 폴링은 레코드만으로 같은 스펙을 복원해야 한다."""
    spec = _spec()
    binding = ApprovalBinding(
        ApprovalKind.RELEASE,
        ApprovalSurface.SKILL_APPROVALS,
        "1528936606946122421",
        POLICY_VERSION,
    )
    record = spec.new_record("1538547247604525816", binding)

    replay = spec_from_record(record)

    assert replay.action_hash() == spec.action_hash()
    assert replay.render() == spec.render()


def test_legacy_record_without_version_replays_the_frozen_v1_message() -> None:
    spec = _spec()
    binding = ApprovalBinding(
        ApprovalKind.RELEASE,
        ApprovalSurface.SKILL_APPROVALS,
        "1528936606946122421",
        POLICY_VERSION,
    )
    legacy = spec.new_record("1538547247604525816", binding)
    legacy.pop("render_version", None)

    replay = spec_from_record(legacy)
    rendered = replay.render()

    assert replay.render_version == 1
    assert replay.release_nonce in rendered
    assert all(digest in rendered for _name, digest in replay.surface_digests)
    assert replay.action_hash() not in rendered


def test_a_plan_payload_builds_the_spec_the_request_posts() -> None:
    spec = _spec()
    payload = {
        "version": spec.version,
        "head": spec.head_sha,
        "surface_digests": [list(row) for row in spec.surface_digests],
        "patch_notes": spec.patch_notes,
    }

    built = spec_from_plan(payload, spec.release_nonce)

    assert built.action_hash() == spec.action_hash()


def test_decision_exit_maps_owner_probes_and_keeps_uncertainty_pending() -> None:
    assert decision_exit(Probe.APPROVED) == DECISION_APPROVED
    assert decision_exit(Probe.CANCELLED) == DECISION_DENIED
    assert decision_exit(Probe.BOUND_PENDING) == DECISION_PENDING
    assert decision_exit(Probe.UNVERIFIABLE) == DECISION_PENDING
    assert decision_exit(Probe.MISSING) == DECISION_PENDING


def test_the_detail_messages_follow_the_freshly_posted_card_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a card that was just posted / When the request command finishes /
    Then every detail message reaches the same channel, in order, and is recorded."""
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire()
    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)
    plan_file = _plan_file(tmp_path, _LONG_NOTES)

    exit_code = release_approval.main(["request", "--plan-file", plan_file])

    captured = capsys.readouterr()
    assert exit_code == 0
    emitted = json.loads(captured.out)
    stored = json.loads(
        (tmp_path / "pending" / "release.json").read_text(encoding="utf-8")
    )
    detail_ids = json.loads(emitted["detail_message_ids"])
    detail_posts = [post for post in wire.posts if "message_reference" not in post]
    assert len(detail_posts) > 1
    assert detail_ids == [
        str(700000000000000000 + index)
        for index in range(1, len(detail_posts) + 1)
    ]
    assert json.loads(stored["detail_message_ids"]) == detail_ids
    assert stored["action_hash"] == emitted["action_hash"]


def test_a_reused_request_posts_no_detail_messages_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a live request the owner has not answered / When the command re-runs /
    Then nothing is posted a second time."""
    wire = _DetailWire()
    record = _spec().new_record(_MESSAGE_ID, _binding())
    monkeypatch.setattr(
        skill_gate_request, "reuse", lambda gate: skill_gate_request.Requested(record, 0)
    )
    monkeypatch.setattr(
        release_approval, "_gate", lambda spec: _LifecycleGate(tmp_path, spec)
    )
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    exit_code = release_approval.main(
        ["request", "--plan-file", _plan_file(tmp_path, _LONG_NOTES)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert wire.posts == []
    assert "detail_message_ids" not in json.loads(captured.out)


def test_a_detail_post_failure_is_loud_and_cleans_up_without_a_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given Discord fails midway through details / When the request runs /
    Then the partial detail is removed and no card or record survives."""
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire(fail_post_after=1)
    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)
    plan_file = _plan_file(tmp_path, _LONG_NOTES)
    expected = replace(
        spec_from_plan(json.loads(Path(plan_file).read_text("utf-8")), "0" * 32), render_version=6,
    )

    with pytest.raises(ApprovalSurfaceError):
        _ = release_approval.main(["request", "--plan-file", plan_file])

    captured = capsys.readouterr()
    assert (
        f"RELEASE-DETAIL-POST-FAIL OSError posted=1/{len(expected.detail_messages())}"
        in captured.err.splitlines()
    )
    assert wire.deletes == [f"/channels/{_THREAD_ID}/messages/700000000000000001", f"/channels/{_THREAD_ID}"]
    assert not (tmp_path / "pending" / "release.json").exists()


def _recording_gates(
    gates: list[_LifecycleGate], gate_dir: Path
) -> Callable[[ReleaseSpec], _LifecycleGate]:
    """`_gate` 대체 — 만들어진 게이트를 전부 붙잡아 효과를 나중에 셀 수 있게 한다."""

    def build(spec: ReleaseSpec) -> _LifecycleGate:
        gate = _LifecycleGate(gate_dir, spec)
        gates.append(gate)
        return gate

    return build


def test_a_stored_v4_release_card_keeps_the_frozen_envelope() -> None:
    """Given a new release / When the card renders / Then five envelope fields carry
    every fact the frozen card carried, each on one line and inside the budget."""
    spec = replace(_spec(), render_version=4)

    content = spec.render()

    assert spec.render_version == 4
    assert content == _V4_POSTED
    assert len(content.splitlines()) == 5
    assert len(content) <= MESSAGE_LIMIT
    assert spec.patch_notes not in content
    assert spec.release_nonce not in content
    assert all(digest not in content for _name, digest in spec.surface_digests)


def test_a_major_release_carries_the_operator_note_inside_the_envelope_fact() -> None:
    """Given a machine-contract signal / When the envelope card renders /
    Then the operator note rides the fact line instead of adding a sixth line."""
    spec = replace(_spec(), render_version=4, major_note=_MAJOR_NOTE)

    content = spec.render()

    assert content == _V4_MAJOR_POSTED
    assert len(content.splitlines()) == 5


def test_the_render_version_never_enters_the_action_hash() -> None:
    """판본은 표현이지 승인 대상이 아니다 — 같은 입력이면 어느 판본이든 같은 해시다."""
    digests = {
        replace(_spec(), render_version=version).action_hash()
        for version in (1, 2, 3, 4, 5, 6)
    }

    assert digests == {_ACTION_HASH}


def test_an_unknown_stored_render_version_is_refused_and_never_binds() -> None:
    """모르는 판본은 오늘과 똑같이 거절된다 — 넓히는 것은 6 하나뿐이다."""
    record = {**_spec().new_record(_MESSAGE_ID, _binding()), "render_version": "9"}

    with pytest.raises(ReleaseSpecError):
        _ = spec_from_record(record)
    assert not _spec().bound(_V4_POSTED, record)


def test_a_missing_envelope_falls_back_to_the_previous_version_and_records_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """봉투를 import 할 수 없으면 직전 판본을 렌더하고 레코드도 그 판본을 적는다."""
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)

    card, refusal = card_for_new_request(_spec())

    assert refusal == ""
    assert card is not None
    assert card.render_version == 3
    assert card.render() == _V3_POSTED
    record = card.new_record(_MESSAGE_ID, _binding())
    assert record["render_version"] == "3"
    assert card.bound(_V3_POSTED, record)


def test_a_card_over_the_limit_is_refused_before_the_post_record_or_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a release whose card cannot fit / When the request runs / Then it is refused
    with the card unposted, no pending record and no journal reservation."""
    gates: list[_LifecycleGate] = []
    wire = _DetailWire()
    monkeypatch.setattr(release_approval, "_gate", _recording_gates(gates, tmp_path))
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    exit_code = release_approval.main(
        [
            "request",
            "--plan-file",
            _plan_file(tmp_path, major_note="MAJOR: " + "가" * 2000),
        ]
    )

    captured = capsys.readouterr()
    assert all(gate.posted == [] for gate in gates), "the card was posted anyway"
    assert not (tmp_path / "pending" / "release.json").exists(), "a pending record survived"
    assert not (tmp_path / "posting-journal").exists(), "a journal reservation survived"
    assert not (tmp_path / "approval-leases").exists(), "the request took the lease"
    assert wire.posts == []
    assert exit_code == 6
    assert captured.err.startswith(CARD_REFUSED_PREFIX)
    assert "1900" in captured.err
    assert captured.out == ""


def test_a_card_within_the_limit_is_posted_with_the_version_it_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a release that fits / When the request runs / Then the same seam posts it
    and the stored record replays the exact bytes the owner is looking at."""
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire()

    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    exit_code = release_approval.main(["request", "--plan-file", _plan_file(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    stored = json.loads((tmp_path / "pending" / "release.json").read_text("utf-8"))
    assert stored["render_version"] == "6"
    replay = spec_from_record(stored)
    rendered = replay.render()
    detail_ids = json.loads(stored["detail_message_ids"])
    assert detail_ids == ["700000000000000001"]
    assert f"https://discord.com/channels/{wire.guild_id}/{_THREAD_ID}/{detail_ids[0]}" in rendered
    assert "-# 참조: 변경 상세 → https://discord.com/channels/" in rendered
    assert "배포 묶음: 스킬 1 · 홈 패키지 1" in rendered
    assert "`home:skills/mail`, `skill:meeting`" not in rendered
    assert replay.bound(rendered, stored)
    assert json.loads(captured.out)["render_version"] == "6"
    assert any(
        "### 배포 묶음 전체" in str(post["content"])
        for post in wire.posts
        if "message_reference" not in post
    )
    card_posts = [post for post in wire.posts if "message_reference" in post]
    assert card_posts == [
        {
            "content": rendered,
            "message_reference": {
                "message_id": detail_ids[0],
                "fail_if_not_exists": True,
            },
            "allowed_mentions": {"parse": [], "replied_user": False},
        }
    ]
    # 상세마다 카드 URL 이 PATCH 로 되돌아가야 한다 — `all()` 만 쓰면 빈 patch 목록도
    # 통과하므로 대상 경로를 먼저 못박는다.
    assert [path for path, _payload in wire.patches] == [
        f"/channels/{_THREAD_ID}/messages/{detail_id}" for detail_id in detail_ids
    ]
    assert all(
        f"[승인 카드](<https://discord.com/channels/{wire.guild_id}/{_THREAD_ID}/{stored['message_id']}>)"
        in str(payload["content"])
        for _path, payload in wire.patches
    )
    assert all(
        len(str(payload["content"])) <= MESSAGE_LIMIT
        for _path, payload in wire.patches
    )


@pytest.mark.parametrize("guild_id", [None, "01"])
def test_missing_guild_id_uses_a_search_reference_without_a_fake_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    guild_id: str | None,
) -> None:
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire(guild_id=guild_id)
    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    assert release_approval.main(
        ["request", "--plan-file", _plan_file(tmp_path)]
    ) == 0

    record = json.loads(capsys.readouterr().out)
    rendered = spec_from_record(record).render()
    assert "discord.com/channels" not in rendered
    assert "검색: 변경 상세" in rendered
    assert record.get("detail_guild_id", "") == ""


def test_malformed_detail_id_is_cleaned_up_before_any_card_or_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire(first_post_id="01")
    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    with pytest.raises(ApprovalSurfaceError):
        _ = release_approval.main(
            ["request", "--plan-file", _plan_file(tmp_path)]
        )

    assert wire.deletes == [f"/channels/{_THREAD_ID}/messages/01", f"/channels/{_THREAD_ID}"]
    assert not (tmp_path / "pending" / "release.json").exists()


def test_v5_fallback_does_not_append_an_over_budget_backlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire()

    def unavailable(spec: ReleaseSpec, *, bundle_summary: str) -> str:
        del spec, bundle_summary
        raise release_spec.EnvelopeUnavailable("synthetic old runtime")

    monkeypatch.setattr(release_spec, "render_v6", unavailable)
    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)
    long_notes = "\n".join(f"- 변경 {index} " + "가" * 300 for index in range(20))

    assert release_approval.main(
        ["request", "--plan-file", _plan_file(tmp_path, long_notes)]
    ) == 0

    stored = json.loads(
        (tmp_path / "pending" / "release.json").read_text(encoding="utf-8")
    )
    assert stored["render_version"] == "5"
    assert wire.patches == []
    assert all(len(str(post["content"])) <= MESSAGE_LIMIT for post in wire.posts)


def test_v6_compacts_many_bundles_and_moves_the_full_list_into_details() -> None:
    surfaces = (
        *((f"skill:demo-{index:02d}", f"{index:064x}") for index in range(19)),
        *((f"home:package-{index:02d}", f"{index + 19:064x}") for index in range(23)),
        ("repo", "e" * 64),
        ("runtime", "f" * 64),
    )
    spec = replace(
        _spec(),
        surface_digests=surfaces,
        render_version=6,
        detail_message_ids=("700000000000000001",),
        detail_channel_id="222",
        detail_guild_id="111111111111111111",
    )

    card = spec.render()
    details = "\n".join(spec.detail_messages())

    assert "배포 묶음: 스킬 19 · 홈 패키지 23 · repo · runtime" in card
    assert "skill:demo-00" not in card
    assert "home:package-00" not in card
    assert all(name in details for name, _digest in surfaces)
    assert len(card) <= MESSAGE_LIMIT


def test_lifecycle_refusal_posts_no_orphan_detail_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wire = _DetailWire()

    class RefusingGate(_LifecycleGate):
        def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
            return (ApprovalRequest(key, "other", "999", "222", ""),)

        def probe(self, request: ApprovalRequest) -> Probe:
            del request
            return Probe.BINDING_MISMATCH

    monkeypatch.setattr(
        release_approval, "_gate", lambda spec: RefusingGate(tmp_path, spec)
    )
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    exit_code = release_approval.main(
        ["request", "--plan-file", _plan_file(tmp_path)]
    )

    assert exit_code == skill_gate_request.LIFECYCLE_REFUSAL_EXIT
    assert wire.posts == []
    assert "reason=binding-mismatch" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("version", "posted"),
    (
        (1, _V1_POSTED),
        (2, _V2_POSTED),
        (3, _V3_POSTED),
        (4, _V4_POSTED),
        (5, _V5_POSTED),
    ),
)
def test_the_record_persists_the_posted_digest(version: int, posted: str) -> None:
    from hashlib import sha256

    spec = replace(_spec(), render_version=version)
    record = spec.new_record(_MESSAGE_ID, _binding())

    assert record["content_sha256"] == sha256(posted.encode("utf-8")).hexdigest()


def test_card_finalization_pins_the_posted_bytes_before_recording_their_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hashlib import sha256

    calls: list[int] = []

    def observed(spec: ReleaseSpec) -> str:
        calls.append(spec.render_version)
        return "posted-release-v7"

    monkeypatch.setattr(release_spec, "render_v8", observed)
    card, refusal = card_for_new_request(_spec())
    assert card is not None and refusal == ""
    assert card.render() == "posted-release-v7"
    record = card.new_record(_MESSAGE_ID, _binding())
    assert record["content_sha256"] == sha256(b"posted-release-v7").hexdigest()
    assert calls and all(version == 8 for version in calls)

    def forbidden(spec: ReleaseSpec) -> str:
        del spec
        raise AssertionError("finalized card must not render again")

    monkeypatch.setattr(release_spec, "render_v7", forbidden)
    assert card.render() == "posted-release-v7"
    assert spec_from_record(record).bound("posted-release-v7", record)


@pytest.mark.parametrize(
    ("version", "posted"), ((1, _V1_POSTED), (2, _V2_POSTED), (3, _V3_POSTED)),
)
def test_pre_digest_records_keep_the_frozen_legacy_binding(version: int, posted: str) -> None:
    spec = replace(_spec(), render_version=version)
    record = spec.new_record(_MESSAGE_ID, _binding())
    del record["content_sha256"]

    assert spec.bound(posted, record)
    assert not spec.bound(posted + " ", record)


def test_a_v4_record_without_its_digest_fails_closed() -> None:
    record = replace(_spec(), render_version=4).new_record(_MESSAGE_ID, _binding())
    del record["content_sha256"]

    assert not _spec().bound(_V4_POSTED, record)


def test_a_clicked_fallback_card_stays_bound_after_the_envelope_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as missing:
        missing.setitem(sys.modules, "automation.interop.owner_message", None)
        card, refusal = card_for_new_request(_spec())
        assert card is not None and refusal == ""
        record = card.new_record(_MESSAGE_ID, _binding())
    assert record["render_version"] == "3"
    calls: list[int] = []

    def forbidden(spec: ReleaseSpec) -> str:
        calls.append(spec.render_version)
        raise AssertionError("clicked fallback approval must not render")

    monkeypatch.setattr(ReleaseSpec, "render", forbidden)
    assert spec_from_record(record).bound(_V3_POSTED, record)
    assert not spec_from_record(record).bound(_V3_POSTED + " ", record)
    assert calls == []


def test_a_new_release_card_opens_its_own_thread_and_records_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given a new request / When it posts / Then a thread opens under the approval channel,
    every detail and the card land inside it, and the record points at the thread."""
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire()
    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    assert release_approval.main(["request", "--plan-file", _plan_file(tmp_path)]) == 0

    _ = capsys.readouterr()
    assert [path for path, _ in wire.threads] == ["/channels/222/threads"]
    assert wire.threads[0][1]["name"] == "🏷️ 릴리스 v1.2.3"
    stored = json.loads((tmp_path / "pending" / "release.json").read_text("utf-8"))
    assert stored["channel_id"] == _THREAD_ID


def test_a_release_card_is_not_posted_when_its_thread_cannot_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given Discord refuses the thread / When the request runs / Then nothing is posted."""
    wire = _DetailWire()

    def refuse(method: str, path: str, payload: dict[str, JsonValue] | None = None) -> JsonValue:
        if path.endswith("/threads"):
            raise OSError("discord is unreachable")
        return wire.api(method, path, payload)

    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", refuse)

    with pytest.raises(ApprovalSurfaceError):
        _ = release_approval.main(["request", "--plan-file", _plan_file(tmp_path)])

    _ = capsys.readouterr()
    assert wire.posts == []
    assert not (tmp_path / "pending" / "release.json").exists()


@pytest.mark.parametrize("archived", [False, True])
def test_a_replaced_request_posts_into_the_same_release_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    archived: bool,
) -> None:
    """Given this version's thread is already open (or archived) / When a new card posts /
    Then no second thread opens and the card lands in that thread (owner 2026-10-19 00:00 KST)."""
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire()
    wire.existing_threads = [
        {"id": "444444444444444444", "parent_id": "999", "name": "🏷️ 릴리스 v1.2.3"},
        {"id": "555555555555555555", "parent_id": "222", "name": "🏷️ 릴리스 v1.2.3", "archived": archived},
    ]
    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    assert release_approval.main(["request", "--plan-file", _plan_file(tmp_path)]) == 0

    _ = capsys.readouterr()
    assert wire.threads == []
    stored = json.loads((tmp_path / "pending" / "release.json").read_text("utf-8"))
    assert stored["channel_id"] == "555555555555555555"


def test_a_failed_card_in_a_reused_thread_keeps_that_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given the version's thread holds earlier cards / When the new card fails / Then the
    thread is not deleted; only this attempt's detail message is cleaned up."""
    monkeypatch.setattr("automation.release_card.NEW_RENDER_VERSION", 6)
    wire = _DetailWire(fail_post_after=1)
    wire.existing_threads = [{"id": "555555555555555555", "parent_id": "222", "name": "🏷️ 릴리스 v1.2.3"}]
    monkeypatch.setattr(release_approval, "_gate", _actual_gate(tmp_path, wire))
    monkeypatch.setattr(skill_gate, "_api", wire.api)

    with pytest.raises(ApprovalSurfaceError):
        _ = release_approval.main(["request", "--plan-file", _plan_file(tmp_path, _LONG_NOTES)])

    _ = capsys.readouterr()
    assert "/channels/555555555555555555" not in wire.deletes
    assert wire.deletes == ["/channels/555555555555555555/messages/700000000000000001"]
