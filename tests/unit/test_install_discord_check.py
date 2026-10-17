"""Pure-logic tests for the read-only Discord prerequisite checker.

Every Discord answer is injected. No socket is opened, no credential is real,
and each test asserts on the *diagnosis category* an operator would act on —
the exact wording is free to change, the category is not.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from automation.install.checks import Status, exit_code, redact, render
from automation.install.discord_check import (
    REQUIRED_CHANNELS,
    REQUIRED_PERMISSIONS,
    ApiResponse,
    ChannelRequirement,
    evaluate_channel,
    evaluate_intents,
    evaluate_permissions,
    evaluate_token,
    load_channel_ids,
    main,
    run_checks,
)

# A value shaped like a bot token. It is fed in wherever a token would flow so
# any assertion that it is absent from output proves the redaction boundary.
TOKEN_CANARY = "CANARY.token.value-do-not-leak"

_MESSAGE_CONTENT = 1 << 18
_MESSAGE_CONTENT_LIMITED = 1 << 19
_PERMISSION_BITS: Mapping[str, int] = {
    "ADD_REACTIONS": 1 << 6,
    "VIEW_CHANNEL": 1 << 10,
    "SEND_MESSAGES": 1 << 11,
    "ATTACH_FILES": 1 << 15,
    "READ_MESSAGE_HISTORY": 1 << 16,
    "CREATE_PUBLIC_THREADS": 1 << 35,
    "SEND_MESSAGES_IN_THREADS": 1 << 38,
    "ADMINISTRATOR": 1 << 3,
    "MANAGE_GUILD": 1 << 5,
    "MANAGE_ROLES": 1 << 28,
}
_MINIMAL_PERMISSIONS = sum(_PERMISSION_BITS[name] for name in REQUIRED_PERMISSIONS)

_AGENTS_LOG = REQUIRED_CHANNELS[0]
_INTEROP = REQUIRED_CHANNELS[1]
_APPROVALS = REQUIRED_CHANNELS[2]


def _ok_bot() -> ApiResponse:
    return ApiResponse(200, {"id": "1", "username": "Example-Agent", "bot": True})


def _ok_channel(name: str) -> ApiResponse:
    return ApiResponse(200, {"id": "9", "name": name, "type": 0, "guild_id": "7"})


class FakeFetch:
    """Records every requested path and replays a canned answer per path."""

    def __init__(self, responses: Mapping[str, ApiResponse]) -> None:
        self.responses = responses
        self.paths: list[str] = []

    def __call__(self, path: str) -> ApiResponse:
        self.paths.append(path)
        return self.responses.get(path, ApiResponse(404))


def test_valid_bot_token_passes() -> None:
    assert evaluate_token(_ok_bot()).status is Status.PASS


@pytest.mark.parametrize(
    ("response", "marker"),
    [
        (ApiResponse(401), "INVALID-TOKEN"),
        (ApiResponse(0, transport_error="connection"), "INVALID-TOKEN"),
        (ApiResponse(200, {"id": "1", "username": "u"}), "NOT-A-BOT"),
        (ApiResponse(200, {"username": "u", "bot": True}), "INVALID-TOKEN"),
    ],
)
def test_token_failures_name_their_category(response: ApiResponse, marker: str) -> None:
    result = evaluate_token(response)
    assert result.status is Status.FAIL
    assert marker in result.detail


@pytest.mark.parametrize("flags", [_MESSAGE_CONTENT, _MESSAGE_CONTENT_LIMITED])
def test_message_content_intent_accepts_both_flag_variants(flags: int) -> None:
    assert evaluate_intents(ApiResponse(200, {"flags": flags})).status is Status.PASS


def test_message_content_intent_off_is_pinpointed() -> None:
    result = evaluate_intents(ApiResponse(200, {"flags": 1 << 17}))
    assert result.status is Status.FAIL
    assert "MESSAGE-CONTENT-INTENT-OFF" in result.detail
    assert "Privileged Gateway Intents" in result.detail


def test_intent_unknown_when_application_lookup_fails() -> None:
    result = evaluate_intents(ApiResponse(403))
    assert result.status is Status.FAIL
    assert "INTENT-UNKNOWN" in result.detail


def test_minimal_invite_permissions_pass() -> None:
    response = ApiResponse(200, [{"id": "7", "permissions": str(_MINIMAL_PERMISSIONS)}])
    (result,) = evaluate_permissions(response)
    assert result.status is Status.PASS


def test_missing_permission_is_named() -> None:
    reduced = _MINIMAL_PERMISSIONS & ~_PERMISSION_BITS["READ_MESSAGE_HISTORY"]
    (result,) = evaluate_permissions(ApiResponse(200, [{"id": "7", "permissions": str(reduced)}]))
    assert result.status is Status.FAIL
    assert "MISSING-PERMISSION" in result.detail
    assert "READ_MESSAGE_HISTORY" in result.detail


@pytest.mark.parametrize("forbidden", ["ADMINISTRATOR", "MANAGE_GUILD", "MANAGE_ROLES"])
def test_over_privileged_bot_is_rejected(forbidden: str) -> None:
    granted = _MINIMAL_PERMISSIONS | _PERMISSION_BITS[forbidden]
    (result,) = evaluate_permissions(ApiResponse(200, [{"id": "7", "permissions": str(granted)}]))
    assert result.status is Status.FAIL
    assert "OVER-PRIVILEGED" in result.detail
    assert forbidden in result.detail


def test_bot_in_no_guild_is_rejected() -> None:
    (result,) = evaluate_permissions(ApiResponse(200, []))
    assert result.status is Status.FAIL
    assert "NO-GUILD" in result.detail


def test_permissions_reported_per_guild() -> None:
    results = evaluate_permissions(
        ApiResponse(
            200,
            [
                {"id": "7", "permissions": str(_MINIMAL_PERMISSIONS)},
                {"id": "8", "permissions": "0"},
            ],
        )
    )
    assert [result.status for result in results] == [Status.PASS, Status.FAIL]
    assert "8" in results[1].name


def test_channel_visible_and_readable_passes() -> None:
    result = evaluate_channel(_AGENTS_LOG, "9", _ok_channel("agents-log"), ApiResponse(200, []))
    assert result.status is Status.PASS


def test_absent_channel_id_is_reported_as_missing() -> None:
    result = evaluate_channel(_APPROVALS, None, None, None)
    assert result.status is Status.FAIL
    assert "MISSING-CHANNEL-ID" in result.detail
    assert _APPROVALS.config_key in result.detail


@pytest.mark.parametrize(
    ("channel", "history", "marker"),
    [
        (ApiResponse(404), None, "MISSING-CHANNEL"),
        (ApiResponse(403), None, "NO-ACCESS"),
        (ApiResponse(200, {"id": "9", "name": "voice", "type": 2}), None, "WRONG-CHANNEL-TYPE"),
        (_ok_channel("agents-log"), ApiResponse(403), "NO-HISTORY"),
        (_ok_channel("agents-log"), ApiResponse(500), "HISTORY-UNKNOWN"),
    ],
)
def test_channel_failures_name_their_category(
    channel: ApiResponse, history: ApiResponse | None, marker: str
) -> None:
    result = evaluate_channel(_AGENTS_LOG, "9", channel, history)
    assert result.status is Status.FAIL
    assert marker in result.detail


def test_channel_name_drift_warns_but_does_not_fail() -> None:
    result = evaluate_channel(_INTEROP, "9", _ok_channel("bot-coord"), ApiResponse(200, []))
    assert result.status is Status.WARN
    assert _INTEROP.expected_name in result.detail


def test_approvals_channel_passes_when_renamed() -> None:
    # Given: the configured id is readable and its name is arbitrary.
    channel = _ok_channel("supply-chain")
    # When
    result = evaluate_channel(_APPROVALS, "9", channel, ApiResponse(200, []))
    # Then
    assert result.status is Status.PASS


def test_run_checks_is_read_only_and_covers_every_required_channel() -> None:
    responses = {
        "/users/@me": _ok_bot(),
        "/applications/@me": ApiResponse(200, {"flags": _MESSAGE_CONTENT}),
        "/users/@me/guilds": ApiResponse(200, [{"id": "7", "permissions": str(_MINIMAL_PERMISSIONS)}]),
    }
    channel_ids = {}
    for index, requirement in enumerate(REQUIRED_CHANNELS, start=1):
        channel_ids[requirement.role] = str(index)
        responses[f"/channels/{index}"] = _ok_channel(requirement.expected_name)
        responses[f"/channels/{index}/messages?limit=1"] = ApiResponse(200, [])
    fetch = FakeFetch(responses)

    results = run_checks(fetch, channel_ids)

    assert exit_code(results) == 0
    # Every *check* passes. The one WARN is the owner-DM coverage declaration,
    # which is not a check and must never be able to turn the run red.
    assert all(
        result.status is Status.PASS
        for result in results
        if not result.name.startswith("surface[")
    )
    assert {requirement.role for requirement in REQUIRED_CHANNELS} == {
        result.name.removeprefix("channel[").removesuffix("]")
        for result in results
        if result.name.startswith("channel[")
    }
    # Read-only contract: the checker never issues a write-shaped request.
    assert all(path.startswith(("/users/", "/applications/", "/channels/")) for path in fetch.paths)


def test_run_checks_stops_at_a_dead_token() -> None:
    fetch = FakeFetch({"/users/@me": ApiResponse(401)})

    results = run_checks(fetch, {requirement.role: "1" for requirement in REQUIRED_CHANNELS})

    assert len(results) == 1
    assert fetch.paths == ["/users/@me"]
    assert exit_code(results) == 1


def test_history_is_not_requested_for_an_unreachable_channel() -> None:
    fetch = FakeFetch(
        {
            "/users/@me": _ok_bot(),
            "/applications/@me": ApiResponse(200, {"flags": _MESSAGE_CONTENT}),
            "/users/@me/guilds": ApiResponse(200, [{"id": "7", "permissions": str(_MINIMAL_PERMISSIONS)}]),
            "/channels/1": ApiResponse(403),
        }
    )

    run_checks(fetch, {_AGENTS_LOG.role: "1"})

    assert "/channels/1/messages?limit=1" not in fetch.paths


def test_config_supplies_channel_ids_and_flags_win(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({requirement.config_key: f"cfg-{requirement.role}" for requirement in REQUIRED_CHANNELS}),
        encoding="utf-8",
    )

    resolved = load_channel_ids(config, {_AGENTS_LOG.role: "flag-wins", _INTEROP.role: None})

    assert resolved[_AGENTS_LOG.role] == "flag-wins"
    assert resolved[_INTEROP.role] == f"cfg-{_INTEROP.role}"
    assert resolved[_APPROVALS.role] == f"cfg-{_APPROVALS.role}"


def test_unreadable_config_degrades_to_flags_only(tmp_path: Path) -> None:
    assert load_channel_ids(tmp_path / "absent.json", {_INTEROP.role: "x"}) == {_INTEROP.role: "x"}


def test_report_never_carries_the_token() -> None:
    requirement = ChannelRequirement("r", "n", "k", "p")
    leaky = evaluate_channel(requirement, TOKEN_CANARY, ApiResponse(404), None)
    assert TOKEN_CANARY in leaky.detail  # the id itself is echoed back by design
    assert TOKEN_CANARY not in redact(render([leaky]), TOKEN_CANARY)


def test_missing_token_env_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--token-env", "AUTOPHAGY_ABSENT_TOKEN_ENV_FOR_TEST"]) == 2
    assert "USAGE-ERROR" in capsys.readouterr().err
