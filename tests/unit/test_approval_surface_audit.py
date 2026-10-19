from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias

import pytest

import automation.interop.approval_surface_audit as audit

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_REPO = Path(__file__).resolve().parents[2]
_DRIVER = _REPO / "tests" / "e2e" / "drivers" / "as_legacy_drain_probe.py"
_GUILD = "1528936606946122421"


def _record(**updates: JsonValue) -> dict[str, JsonValue]:
    return {
        "id": "opaque-draft",
        "kind": "mail_reply",
        "surface": "skill-approvals",
        "channel_id": _GUILD,
        "policy_version": 1,
        "status": "pending",
        **updates,
    }


def _write(path: Path, payload: dict[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _roots(tmp_path: Path) -> dict[audit.Flow, Path]:
    return {flow: tmp_path / flow.value for flow in audit.Flow}


@pytest.mark.parametrize(
    ("flow", "kind"),
    (
        (audit.Flow.MAIL, "mail_reply"),
        (audit.Flow.BUDGET, "budget_mail"),
        (audit.Flow.PATENT, "patent_export"),
        (audit.Flow.REPAIR, "repair"),
    ),
)
def test_flow_discovers_its_pending_record(
    tmp_path: Path,
    flow: audit.Flow,
    kind: str,
) -> None:
    # Given: one generically shaped pending record under each supported flow root.
    root = tmp_path / flow.value
    directory = root / "public" if flow is audit.Flow.MAIL else root
    _write(directory / "record.json", _record(kind=kind))

    # When: the read-only audit observes that root.
    result = audit.audit_flow(flow, root)

    # Then: it finds exactly the non-terminal guild-bound approval.
    assert (result.total, result.non_terminal, result.guild_bound_non_terminal) == (1, 1, 1)


def test_terminal_record_is_not_non_terminal(tmp_path: Path) -> None:
    # Given: a completed mail approval remains as historical state.
    root = tmp_path / "mail"
    _write(root / "public" / "done.json", _record(status="consumed"))

    # When: the audit observes the record.
    result = audit.audit_flow(audit.Flow.MAIL, root)

    # Then: it never blocks the migration drain gate.
    assert (result.non_terminal, result.guild_bound_non_terminal, result.blocking) == (0, 0, 0)


@pytest.mark.parametrize(
    ("channel_id", "surface"),
    (("", "skill-approvals"), (None, "skill-approvals"), ("dm", "owner-dm")),
)
def test_legacy_channel_sentinels_are_classified_without_a_directory(
    tmp_path: Path,
    channel_id: str | None,
    surface: str,
) -> None:
    # Given: a pre-migration record carries only its legacy channel sentinel.
    root = tmp_path / "mail"
    _write(
        root / "public" / "legacy.json",
        _record(surface=None, channel_id=channel_id, policy_version=None),
    )

    # When: the audit classifies the legacy binding locally.
    result = audit.audit_flow(audit.Flow.MAIL, root)

    # Then: policy zero semantics select its historical surface without Discord I/O.
    record = result.records[0]
    assert (record.surface, record.policy_version) == (surface, 0)


def test_unreadable_record_counts_as_blocking(tmp_path: Path) -> None:
    # Given: a corrupt record in an otherwise valid fixture root.
    root = tmp_path / "mail"
    path = root / "public" / "corrupt.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    # When: the audit cannot parse it.
    result = audit.audit_flow(audit.Flow.MAIL, root)

    # Then: uncertainty blocks the cleanup gate instead of being silently skipped.
    assert result.blocking == 1


def test_empty_root_reports_zeroes(tmp_path: Path) -> None:
    # Given: an existing empty root.
    root = tmp_path / "budget"
    root.mkdir()

    # When: it is audited.
    result = audit.audit_flow(audit.Flow.BUDGET, root)

    # Then: no approval is inferred.
    assert (result.missing, result.total, result.non_terminal, result.blocking) == (False, 0, 0, 0)


def test_missing_root_is_not_an_error(tmp_path: Path) -> None:
    # Given: an unprovisioned state root.
    root = tmp_path / "absent"

    # When: it is audited without creating it.
    result = audit.audit_flow(audit.Flow.REPAIR, root)

    # Then: the absence is an explicit, harmless observation.
    assert (result.missing, result.total, result.blocking) == (True, 0, 0)


def test_json_shape_is_stable(tmp_path: Path) -> None:
    # Given: a report containing one pending mail record and four empty roots.
    roots = _roots(tmp_path)
    _write(roots[audit.Flow.MAIL] / "public" / "record.json", _record())

    # When: the complete report becomes JSON-compatible evidence.
    payload = audit.audit_roots(roots).to_json()

    # Then: consumers receive the fixed top-level, flow, and record shapes.
    assert set(payload) == {"policy_version", "flows"}
    mail = payload["flows"][audit.Flow.MAIL.value]
    assert set(mail) == {
        "root", "missing", "total", "non_terminal", "guild_bound_non_terminal", "blocking", "records"
    }
    assert set(mail["records"][0]) == {"id", "kind", "surface", "policy_version", "state"}


def test_state_root_redirects_only_its_named_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: isolated defaults and a separate mail override carrying one pending record.
    defaults = _roots(tmp_path / "defaults")
    override = tmp_path / "mail-override"
    _write(override / "public" / "record.json", _record())
    monkeypatch.setattr(audit, "DEFAULT_ROOTS", defaults)

    # When: the CLI redirects only mail.
    assert audit.main(["--json", "--state-root", f"mail={override}"]) == 0

    # Then: its JSON keeps every other flow at its default root.
    payload = json.loads(capsys.readouterr().out)
    assert payload["flows"]["mail"]["root"] == str(override)
    assert payload["flows"]["budget"]["root"] == str(defaults[audit.Flow.BUDGET])


def test_fail_on_guild_bound_returns_one_then_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one old guild-bound pending and isolated roots for every flow.
    roots = _roots(tmp_path)
    _write(roots[audit.Flow.MAIL] / "public" / "record.json", _record())
    monkeypatch.setattr(audit, "DEFAULT_ROOTS", roots)

    # When: the binary drain gate sees it, then sees its terminal consumed state.
    blocked = audit.main(["--json", "--fail-on-guild-bound"])
    _write(roots[audit.Flow.MAIL] / "public" / "record.json", _record(status="consumed"))
    clear = audit.main(["--json", "--fail-on-guild-bound"])

    # Then: only an undecided guild approval returns the stop signal.
    assert (blocked, clear) == (1, 0)


def test_unknown_state_root_flow_is_a_usage_error() -> None:
    # Given: a state-root override outside the closed flow vocabulary.
    invalid = "unknown=/tmp/approval-audit"

    # When / Then: argparse rejects it with a non-zero usage exit.
    with pytest.raises(SystemExit) as exited:
        audit.main(["--state-root", invalid])
    assert exited.value.code == 2


def test_audit_module_has_no_write_or_network_primitives() -> None:
    # Given: the source that will produce QA evidence.
    source = Path(audit.__file__).read_text(encoding="utf-8")

    # When / Then: forbidden mutating and network primitives remain absent.
    assert all(token not in source for token in ("write_text", "urlopen", "mkdir", "unlink"))


def test_driver_refuses_a_production_state_root() -> None:
    # Given: a path syntactically beneath the operator's production state home.
    production_fixture = Path.home() / ".hermes" / "mail-triage" / "drafts"

    # When: the fixture driver is asked to seed it.
    result = subprocess.run(
        [sys.executable, str(_DRIVER), "--fixture", str(production_fixture), "--seed-only"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: it refuses before touching that path.
    assert result.returncode != 0
    assert "refuses production fixture" in result.stderr


def test_unreadable_root_counts_as_blocking_instead_of_crashing(tmp_path: Path) -> None:
    # Given: a state root the running account cannot stat -- exactly repair's
    # /srv/autophagy-private/repair/pending seen from the agent account.
    denied = tmp_path / "denied"
    denied.mkdir()
    (denied / "pending").mkdir()
    denied.chmod(0o000)
    try:
        # When: the flow is audited.
        result = audit.audit_flow(audit.Flow.REPAIR, denied / "pending")

        # Then: it fails closed as BLOCKING rather than raising, so the R3 gate stays
        # usable from either account and can never read "clean" because it crashed.
        assert result.blocking == 1
        assert result.missing is False
        assert result.guild_bound_non_terminal == 0
    finally:
        denied.chmod(0o755)


def audit_main_exit(argv: list[str]) -> int:
    """`main` with stdout swallowed; the exit code is what the R3 gate reads."""
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        return audit.main(argv)


def test_flow_filter_restricts_the_audit_to_the_account_that_owns_them(tmp_path: Path) -> None:
    # Given: repair state is ops-owned, so the agent account can never read it and
    # would report BLOCKING forever -- making "EXIT=0 on both accounts" unsatisfiable.
    root = tmp_path / "mail"
    (root / "public").mkdir(parents=True)
    (root / "sensitive").mkdir()
    (root / "public" / "d.json").write_text(json.dumps(_record()), encoding="utf-8")

    # When: the caller audits only the flows this account owns.
    code = audit_main_exit(
        ["--json", "--flow", "mail", "--state-root", f"mail={root}", "--fail-on-guild-bound"]
    )

    # Then: the unowned flows are absent from the report entirely, so a root this
    # account cannot see can never veto the gate.
    assert code == 1  # the seeded mail record IS guild-bound and pending
    absent = tmp_path / "repair-pending"  # the real default is unreadable on the node
    assert audit_main_exit(
        ["--json", "--flow", "repair", "--state-root", f"repair={absent}", "--fail-on-guild-bound"]
    ) == 0
