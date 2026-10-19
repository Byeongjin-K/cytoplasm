from __future__ import annotations

import shutil
import importlib.util
import json
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

_NOW = datetime(2026, 8, 15, 0, 0, tzinfo=UTC)
_REPO = Path(__file__).resolve().parents[2]
_WATCH_PATH = _REPO / "automation" / "selfskill_audit" / "cron" / "selfskill_audit_watch.py"
sys.path.insert(0, str(_REPO))

from automation.selfskill_audit import ledger, report  # noqa: E402


def _skill(home: Path, name: str, body: str) -> Path:
    skill = home / ".hermes" / "skills" / name
    skill.mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text(body, encoding="utf-8")
    return skill


def _usage(home: Path, payload: dict[str, dict[str, str | bool | None]]) -> None:
    path = home / ".hermes" / "skills" / ".usage.json"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")


def _load_watch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("selfskill_audit_watch_test", _WATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ledger_when_a_skill_is_created_then_records_a_content_hashed_delta(tmp_path: Path) -> None:
    # Given
    home = tmp_path / "agent"
    skill = _skill(home, "agent-notes", "---\nname: agent-notes\n---\nbody-v1\n")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert [(delta.action.value, delta.name) for delta in result.deltas] == [("created", "agent-notes")]
    assert result.deltas[0].sha256 == ledger.skill_digest(skill)
    assert result.deltas[0].provenance == "agent"
    stored = json.loads(result.ledger_path.read_text(encoding="utf-8"))
    assert stored["sha256"] == result.deltas[0].sha256
    assert stored["timestamp"] == "2026-08-15T00:00:00Z"

def test_ledger_when_a_skill_sits_under_a_category_then_it_is_still_detected(tmp_path: Path) -> None:
    # Given: Hermes files agent-authored skills under a category directory, which is where
    # the peer account's live self-authored skills actually sit (software-development/<name>).
    home = tmp_path / "agent"
    skill = home / ".hermes" / "skills" / "software-development" / "agent-notes"
    skill.mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text("---\nname: agent-notes\n---\nbody-v1\n", encoding="utf-8")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert [(delta.action.value, delta.name) for delta in result.deltas] == [("created", "agent-notes")]
    assert result.deltas[0].sha256 == ledger.skill_digest(skill)
    assert result.deltas[0].provenance == "agent"


def test_ledger_when_a_category_holds_no_skill_then_it_is_not_reported_as_one(tmp_path: Path) -> None:
    # Given: an empty category directory and Hermes' own state directories must not be
    # mistaken for skills just because the scan now descends one level.
    home = tmp_path / "agent"
    root = home / ".hermes" / "skills"
    (root / "empty-category").mkdir(parents=True)
    (root / ".hub").mkdir()
    _ = (root / ".hub" / "taps.json").write_text("{}\n", encoding="utf-8")

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert result.deltas == ()


def test_ledger_when_nothing_changed_then_produces_no_delta_and_no_dm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    home = tmp_path / "agent"
    _skill(home, "agent-notes", "---\nname: agent-notes\n---\nstable\n")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})
    sent: list[str] = []
    monkeypatch.setenv("HERMES_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.delattr("automation.owner_notice.ACCEPTS_OWNER_MESSAGE")
    monkeypatch.setattr(report, "notify_owner", lambda body, **_: sent.append(body) is None or True)
    assert report.run_once(home=home, account_label="agent", now=_NOW) == 0
    sent.clear()

    # When
    result = ledger.audit(home, now=_NOW)
    exit_code = report.run_once(home=home, account_label="agent", now=_NOW)

    # Then
    assert result.deltas == ()
    assert exit_code == 0
    assert sent == []
    log_path = tmp_path / "state" / "logs" / "selfskill-audit" / "2026-08.jsonl"
    assert json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1]) == {
        "account": "agent",
        "delta_counts": {"archived": 0, "created": 0, "edited": 0, "removed": 0, "restored": 0},
        "notified": False,
        "overlaps": [],
        "shadowed": [],
        "ts": "2026-08-15T00:00:00Z",
    }


def test_ledger_when_a_skill_is_archived_then_records_the_archive_delta(tmp_path: Path) -> None:
    # Given
    home = tmp_path / "agent"
    active = _skill(home, "agent-notes", "---\nname: agent-notes\n---\nbody-v1\n")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})
    _ = ledger.audit(home, now=_NOW)
    archive = home / ".hermes" / "skills" / ".archive" / "agent-notes"
    archive.parent.mkdir()
    active.rename(archive)
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": "2026-08-15T01:00:00Z"}})

    # When
    result = ledger.audit(home, now=datetime(2026, 8, 15, 1, 0, tzinfo=UTC))

    # Then
    assert [(delta.action.value, delta.name) for delta in result.deltas] == [("archived", "agent-notes")]
    assert result.deltas[0].sha256 == ledger.skill_digest(archive)


def test_ledger_when_skill_states_change_then_records_every_delta_kind(tmp_path: Path) -> None:
    # Given: records in active and archive roots.
    home = tmp_path / "agent"
    edited = _skill(home, "edited-note", "body-v1\n")
    archived = _skill(home, "archived-note", "body\n")
    removed = _skill(home, "removed-note", "body\n")
    restored = _skill(home, "restored-note", "body\n")
    archive_root = home / ".hermes" / "skills" / ".archive"
    archive_root.mkdir()
    restored.rename(archive_root / "restored-note")
    _ = ledger.audit(home, now=_NOW)

    # When: one skill is added, edited, archived, restored, and removed.
    _ = edited.joinpath("SKILL.md").write_text("body-v2\n", encoding="utf-8")
    archived.rename(archive_root / "archived-note")
    (archive_root / "restored-note").rename(home / ".hermes" / "skills" / "restored-note")
    shutil.rmtree(removed)
    _skill(home, "created-note", "body\n")
    result = ledger.audit(home, now=_NOW)

    # Then
    assert [(delta.action.value, delta.name) for delta in result.deltas] == [
        ("created", "created-note"),
        ("edited", "edited-note"),
        ("restored", "restored-note"),
        ("archived", "archived-note"),
        ("removed", "removed-note"),
    ]


def test_report_when_rendered_then_contains_no_file_bodies_and_no_surface_literals(tmp_path: Path) -> None:
    # Given
    home = tmp_path / "agent"
    _skill(home, "agent-notes", "PRIVATE-BODY-MARKER\n#approvals\nowner-dm\n")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})
    result = ledger.audit(home, now=_NOW)

    # When
    rendered = report.render_summary(result.deltas, account_label="agent")

    # Then
    assert "agent-notes" in rendered
    assert result.deltas[0].sha256[:12] in rendered
    assert "PRIVATE-BODY-MARKER" not in rendered
    assert "#approvals" not in rendered
    assert "owner-dm" not in rendered.lower()
    assert "✅" not in rendered
    assert "⛔" not in rendered


def test_state_files_when_written_then_live_outside_the_checkout_with_0600(tmp_path: Path) -> None:
    # Given
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = tmp_path / "agent-home"
    _skill(home, "agent-notes", "content")
    _usage(home, {"agent-notes": {"created_by": "agent", "agent_created": True, "pinned": False, "archived_at": None}})

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert result.state_path == home / ".hermes" / "selfskill-audit" / "state.json"
    assert result.ledger_path == home / ".hermes" / "selfskill-audit" / "ledger.jsonl"
    assert checkout not in result.state_path.parents
    assert stat.S_IMODE(result.state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.ledger_path.stat().st_mode) == 0o600


def test_watch_when_spawning_children_then_passes_credentials_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    watch = _load_watch()
    home = tmp_path / "agent"
    home.mkdir()
    secrets = home / ".env.secrets"
    _ = secrets.write_text("DISCORD_BOT_TOKEN=fixture-token\nAUTOPHAGY_OWNER_ID=fixture-owner\n", encoding="utf-8")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("AUTOPHAGY_OWNER_ID", raising=False)
    captured: list[dict[str, str]] = []

    def run_child(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        captured.append(environment)
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(watch.subprocess, "run", run_child)
    watch._load_env_secrets(secrets)

    # When
    exit_code = watch.run_once(tmp_path / "repo")

    # Then
    assert exit_code == 0
    assert captured[0]["DISCORD_BOT_TOKEN"] == "fixture-token"
    assert captured[0]["AUTOPHAGY_OWNER_ID"] == "fixture-owner"
    assert captured[0]["AUTOPHAGY_REPO_ROOT"] == str(tmp_path / "repo")


def test_watch_when_resolving_repo_root_then_prefers_release_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    watch = _load_watch()
    current = tmp_path / "release-current"
    mirror = tmp_path / "mirror"
    (current / "automation").mkdir(parents=True)
    (mirror / "automation").mkdir(parents=True)
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
    monkeypatch.setattr(watch, "RELEASE_CURRENT", current)
    monkeypatch.setattr(watch, "RESIDENT_MIRROR", mirror)

    # When
    resolved = watch._runtime_root()

    # Then
    assert resolved == current


def test_ledger_when_bundled_skills_are_seeded_then_they_are_not_reported(tmp_path: Path) -> None:
    # Given: making the primary root writable let Hermes seed its bundled catalogue into it
    # (2026-08-16 실측: 반전 직후 재기동에서 번들 68종·43MB가 카테고리 아래로 들어왔다).
    # Those are vendor skills, not something the agent authored — reporting them would bury
    # the one signal this ledger exists to carry.
    home = tmp_path / "agent"
    root = home / ".hermes" / "skills"
    bundled = root / "productivity" / "arxiv"
    bundled.mkdir(parents=True)
    _ = (bundled / "SKILL.md").write_text("---\nname: arxiv\n---\nvendor\n", encoding="utf-8")
    _ = (root / ".bundled_manifest").write_text("arxiv:e3627375503516a02e1711aa78a27d10\n", encoding="utf-8")
    mine = root / "software-development" / "agent-notes"
    mine.mkdir(parents=True)
    _ = (mine / "SKILL.md").write_text("---\nname: agent-notes\n---\nmine\n", encoding="utf-8")

    # When
    result = ledger.audit(home, now=_NOW)

    # Then
    assert [(d.action.value, d.name) for d in result.deltas] == [("created", "agent-notes")]


def test_ledger_when_a_self_skill_shadows_a_governed_one_then_it_is_flagged(tmp_path: Path) -> None:
    """Hermes' own collision check cannot see our governed store.

    2026-08-16 실측: `_find_skill` 은 `rglob("SKILL.md")` 로 훑는데 `<skill_store>/live` 는
    릴리스로 가는 **심링크 팜**이고 `rglob` 은 디렉터리 심링크를 따라가지 않는다 — 그래서
    `_find_skill("recall")` 이 None 이고, 에이전트가 governed 이름으로 자가 스킬을 만들 수 있었다.
    1차 루트가 발견에서 이기므로 그 자가 스킬은 승인 게이트를 강제하는 배포본을 **가린다**.
    벤더 쪽을 고칠 수 없으니 최소한 소유자에게 즉시 보이게 한다.
    """
    # Given: a governed store holding `mail`, and a self-authored skill claiming the same name
    governed = tmp_path / "live"
    (governed / "mail").mkdir(parents=True)
    home = tmp_path / "agent"
    mine = home / ".hermes" / "skills" / "software-development" / "mail"
    mine.mkdir(parents=True)
    _ = (mine / "SKILL.md").write_text("---\nname: mail\n---\nshadow\n", encoding="utf-8")

    # When
    result = ledger.audit(home, now=_NOW, governed_root=governed)

    # Then
    assert result.shadowed == ("mail",)
    assert "SHADOWS-GOVERNED" in report.render_summary(
        result.deltas, account_label="agent", shadowed=result.shadowed
    )


def test_report_when_owner_id_is_absent_from_env_then_it_comes_from_the_interop_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cron 은 `~/.env.secrets` 만 자가 로드하는데 거기에 owner id 가 없다.

    2026-08-16 실측: agent 의 `.env.secrets` 에는 `DISCORD_BOT_TOKEN` 만 있어
    `owner_notice` 가 `NOTIFY-UNCONFIGURED` 로 조용히 끝났다 — 이 원장의 유일한
    소유자 출력이 매일 사라진다는 뜻이다. 같은 계정의 skill_generation 플러그인이
    이미 쓰는 표준 출처(`~/.hermes/interop/config.json`)에서 해석한다.
    """
    # Given
    home = tmp_path / "agent"
    interop = home / ".hermes" / "interop"
    interop.mkdir(parents=True)
    _ = (interop / "config.json").write_text('{"owner_id": "123456789012345678"}', encoding="utf-8")
    monkeypatch.delenv("AUTOPHAGY_OWNER_ID", raising=False)

    # When
    resolved = report.resolve_owner_id(home)

    # Then
    assert resolved == "123456789012345678"


def test_report_when_owner_id_is_in_env_then_the_config_is_not_consulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: env wins, and a missing config must not turn that into a failure
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "987654321098765432")

    # When / Then
    assert report.resolve_owner_id(tmp_path / "nohome") == "987654321098765432"


def test_ledger_when_a_self_skill_is_deleted_outright_then_it_is_reported(tmp_path: Path) -> None:
    """`.archive` 로 가지 않고 통째로 사라지는 경로가 있다 — 그때도 소유자는 알아야 한다.

    2026-08-16 실측: 검증용 자가 스킬을 `rm -rf` 로 지우자 원장에 아무 델타도 남지 않았다.
    curator 는 아카이브로 옮기지만 `skill_manage(delete)` 나 손 삭제는 흔적 없이 사라진다 —
    "무엇이 생겼나"만 말하고 "무엇이 사라졌나"를 말하지 않으면 감사가 반쪽이다.
    """
    # Given: a recorded self skill
    home = tmp_path / "agent"
    skill = home / ".hermes" / "skills" / "software-development" / "gone-note"
    skill.mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text("---\nname: gone-note\n---\nbody\n", encoding="utf-8")
    first = ledger.audit(home, now=_NOW)
    ledger.mark_reported(first)

    # When: it disappears without being archived
    shutil.rmtree(skill)
    result = ledger.audit(home, now=_NOW)

    # Then
    assert [(d.action.value, d.name) for d in result.deltas] == [("removed", "gone-note")]
