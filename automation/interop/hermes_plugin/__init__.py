"""Official Hermes plugin wiring for Interop Protocol v0."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from automation import group_roster, owner_notice
from automation.interop import policy_paths
from automation.interop.discord_transport import DiscordTransport
from automation.interop.coordination import CORRELATION_PREFIX, TEAM_NOTICE_PREFIX
from automation.interop.delegation import InteropEnvelope, format_envelope, parse_envelope, response_for, result_message
from automation.interop.external_effect_gate import ApprovalContext, DenylistConfigurationError, ToolCall, evaluate_tool_call, load_denylist
from automation.interop.injection_adapter import InboundEvent, accept_test_event
from automation.interop.killswitch import PauseStore
from automation.interop import chat_approval_guard, owner_proxy, owner_proxy_watch, voice_transcript
from automation.interop.loop_guard import LoopGuard
from automation.interop.report import ReportStatus, TaskReport, format_report, parse_report


KST: Final = ZoneInfo("Asia/Seoul")
LOGGER: Final = logging.getLogger("autophagy.interop")
LOOP_GUARD: Final = LoopGuard()
EXTERNAL_EFFECT_APPROVAL_LOG: Final = Path(os.environ.get("EXTERNAL_EFFECT_APPROVAL_LOG", "/srv/autophagy-agents/logs/approvals.jsonl"))
EXTERNAL_EFFECT_DENYLIST: Final = policy_paths.denylist_path()
ROSTER_ENV: Final = "AUTOPHAGY_ROSTER"
OWNER_PROXY_LEDGER: Final = Path("~/.hermes/interop/owner-proxy-ledger.json").expanduser()


def register(ctx) -> None:
    """Register every officially supported gateway and Kanban hook."""
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("transform_llm_output", transform_llm_output)
    ctx.register_hook("kanban_task_claimed", kanban_task_claimed)
    ctx.register_hook("kanban_task_completed", kanban_task_completed)
    ctx.register_hook("kanban_task_blocked", kanban_task_blocked)
    LOGGER.warning("interop plugin registered proxy_watch_resumed=%d", owner_proxy_watch.resume())


def pre_tool_call(tool_name, args, **kwargs):
    """Block configured external effects until an owner-bound audit record exists."""
    del kwargs
    try:
        decision = evaluate_tool_call(
            ToolCall(tool_name=str(tool_name), arguments=args if isinstance(args, dict) else {}),
            load_denylist(EXTERNAL_EFFECT_DENYLIST),
            ApprovalContext(
                approval_log=EXTERNAL_EFFECT_APPROVAL_LOG,
                owner_id=_config()["owner_id"],
                e2e_test_mode=os.environ.get("E2E_TEST_MODE") == "1",
            ),
        )
    except DenylistConfigurationError:
        LOGGER.warning("external-effect gate configuration failure tool=%s", tool_name)
        return {"action": "block", "message": "BLOCKED: external-effect approval gate unavailable"}
    if not decision.external_effect or decision.allowed:
        return None
    LOGGER.warning("external-effect gate blocked tool=%s target=%s action=%s", tool_name, decision.target_id, decision.action_hash[:16])
    return {
        "action": "block",
        "message": f"BLOCKED: owner approval required for {decision.target_id} action={decision.action_hash}",
    }


def pre_gateway_dispatch(event, gateway, session_store, **kwargs):
    """Apply signed-test, kill-switch, and bot-loop policy before dispatch."""
    del gateway, session_store, kwargs
    text, actor_id, injection_rejected = _signed_text_and_actor(event)
    if injection_rejected:
        return {"action": "skip", "reason": "interop_invalid_signature"}
    command = text.strip()
    pause_store = _pause_store()

    if command in {"!pause-agents", "!resume-agents"}:
        result = pause_store.handle(command=command, actor_id=actor_id)
        if result.accepted:
            LOGGER.warning("interop kill-switch command=%s paused=%s", command, result.paused)
            return {"action": "skip", "reason": "interop_kill_switch"}
        LOGGER.warning("interop kill-switch refusal actor=%s", _masked_actor(actor_id))
        return {"action": "allow"}

    if pause_store.is_paused():
        LOGGER.warning("interop paused inbound skipped")
        return {"action": "skip", "reason": "interop_paused"}

    voice = voice_transcript.gateway_voice(event, _config()["owner_id"])
    if voice is not None:
        return voice

    source = event.source
    envelope = parse_envelope(text)
    if envelope is not None and envelope.recipient_id == _config()["agent_id"]:
        try:
            raw_roster_path = os.environ.get(ROSTER_ENV, "").strip()
            roster_path = Path(raw_roster_path) if raw_roster_path else group_roster.DEFAULT_ROSTER_PATH
            roster = group_roster.load_roster(roster_path.expanduser())
        except group_roster.RosterError:
            rejection_reason = "roster_unavailable"
        else:
            expected_sender_id = roster.sender_id_for_discord_author(actor_id)
            rejection_reason = (
                "sender_mismatch" if expected_sender_id != envelope.sender_id else None
            )
        if rejection_reason is not None:
            LOGGER.warning(
                "interop sender identity rejected reason=%s actor=%s claimed_sender=%s",
                rejection_reason,
                _masked_actor(actor_id),
                envelope.sender_id,
            )
            return {"action": "skip", "reason": "interop_sender_identity_rejected"}
        if envelope.intent.startswith("query_"):
            config = _config()
            response = response_for(envelope, sender_id=config["agent_id"])
            channel_id = response_channel_for(
                envelope.correlation_id,
                source_channel_id=str(source.chat_id),
                interop_channel_id=config["interop_channel_id"],
            )
            _send_to_channel(channel_id=channel_id, content=format_envelope(response))
            LOGGER.warning("interop delegation response correlation=%s", envelope.correlation_id)
            return {"action": "skip", "reason": "interop_delegation_response"}
        if envelope.intent.startswith("response_"):
            if envelope.correlation_id.startswith(CORRELATION_PREFIX):
                LOGGER.warning("interop coordination response observed correlation=%s", envelope.correlation_id)
                return {"action": "skip", "reason": "interop_coordination_response"}
            _send_direct_result(envelope.correlation_id, envelope, str(source.thread_id or source.chat_id))
            LOGGER.warning("interop delegation delivered correlation=%s", envelope.correlation_id)
            return {"action": "skip", "reason": "interop_delegation_delivered"}

    if not source.is_bot:
        return {"action": "rewrite", "text": text} if text != event.text else {"action": "allow"}

    if text.startswith(TEAM_NOTICE_PREFIX):
        LOGGER.warning("interop coordination notice skipped (cascade safety)")
        return {"action": "skip", "reason": "interop_coordination_notice"}

    # 봇끼리는 **프로토콜로만** 말한다. 봉투도 보고도 아닌 자유 산문에 답하는 것을
    # 요구하는 흐름은 하나도 없다 — peer 증명은 `peer_attest.py` 가 직접 게시하고,
    # 보고는 report_hub 가 수집하며, 승인은 소유자 리액션을 cron 워처가 폴링한다.
    # 그런데도 두 봇이 서로의 인사말에 답하며 2026-08-20 에 12번을 오갔고,
    # 그 사이에 정작 소유자가 눌러야 할 승인 요청은 화면 밖으로 밀려났다.
    #
    # 아래 LOOP_GUARD 는 분당 5회를 허용하고 저정보 판정도 휴리스틱이라 그 반복을
    # 임계 안으로 보았다. 임계값을 느슨하게 조이는 대신 **첫 홉에서** 끊는다 —
    # 위의 TEAM_NOTICE cascade safety 와 같은 방식이고, 가드는 2차 방어로 남는다.
    if parse_envelope(text) is None and parse_report(text) is None:
        proxied = owner_proxy.gateway_text(event, text, actor_id, _config_payload, OWNER_PROXY_LEDGER)
        if proxied is not None:
            return {"action": "rewrite", "text": proxied}
        LOGGER.warning("interop bot prose skipped (no protocol payload)")
        return {"action": "skip", "reason": "interop_bot_prose"}

    LOGGER.warning("interop bot predispatch received")
    thread_id = source.thread_id or source.chat_id
    decision = LOOP_GUARD.evaluate(thread_id=str(thread_id), body=text, now=time.monotonic())
    if decision.suppressed:
        LOGGER.warning("interop loop guard suppressed reason=%s", decision.reason)
        return {"action": "skip", "reason": f"interop_{decision.reason}"}
    return {"action": "rewrite", "text": text} if text != event.text else {"action": "allow"}


def response_channel_for(correlation_id: str, *, source_channel_id: str, interop_channel_id: str) -> str:
    """Route coordination (coord-) responses to the interop channel; echo others to source."""
    if correlation_id.startswith(CORRELATION_PREFIX) and interop_channel_id:
        return interop_channel_id
    return source_channel_id


def transform_llm_output(response_text: str, session_id: str, model: str, platform: str, **kwargs) -> str | None:
    """Silence a paused reply; replace any chat-✅ request that cites no approval card."""
    del session_id, model, platform, kwargs
    if _pause_store().is_paused():
        LOGGER.warning("interop paused outbound suppressed")
        return "SILENT"
    guarded = chat_approval_guard.guard(response_text)
    if guarded != response_text:
        LOGGER.warning("interop chat approval request replaced")
    return guarded


def kanban_task_claimed(**kwargs) -> None:
    """Report a durable Kanban claim as protocol status ``start``."""
    _send_kanban_report(status=ReportStatus.START, **kwargs)


def kanban_task_completed(**kwargs) -> None:
    """Report a durable Kanban completion as protocol status ``done``."""
    _send_kanban_report(status=ReportStatus.DONE, **kwargs)


def kanban_task_blocked(**kwargs) -> None:
    """Report a durable Kanban block as protocol status ``blocked``."""
    _send_kanban_report(status=ReportStatus.BLOCKED, **kwargs)


def _send_kanban_report(*, status: ReportStatus, **kwargs) -> None:
    LOGGER.warning("interop kanban report entered status=%s", status.value)
    config = _config()
    task_id = str(kwargs.get("task_id", "kanban-unknown"))
    summary = str(kwargs.get("summary") or kwargs.get("reason") or f"Kanban {status.value}")
    report = TaskReport(
        agent_id=config["agent_id"],
        task_id=task_id,
        status=status,
        summary=summary,
        links=(),
        timestamp=datetime.now(KST),
    )
    sent = DiscordTransport(
        token=os.environ["DISCORD_BOT_TOKEN"],
        channel_id=config["agents_log_channel_id"],
    ).send(format_report(report))
    LOGGER.info("interop kanban report status=%s chunks=%d", status.value, len(sent))


def _send_to_channel(*, channel_id: str, content: str) -> None:
    """Send a deterministic protocol message to the given channel (source thread OR interop channel)."""
    _transport(channel_id).send(content)


def _send_direct_result(
    correlation_id: str, envelope: InteropEnvelope | None = None, channel_id: str | None = None,
) -> None:
    """Deliver the delegation receipt through the owner-notice facade (ON-2)."""
    content = f"Interop delegation result: {correlation_id}"
    message = result_message(correlation_id, envelope, channel_id)
    if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
        ok = owner_notice.notify_owner(content, message=message)
    else:
        ok = owner_notice.notify_owner(content)
    if not ok:
        raise RuntimeError("delegation result notice delivery failed")


def _transport(channel_id: str) -> DiscordTransport:
    return DiscordTransport(token=os.environ["DISCORD_BOT_TOKEN"], channel_id=channel_id)


def _signed_text_and_actor(event) -> tuple[str, str, bool]:
    source = event.source
    actor_id = str(source.user_id)
    if os.environ.get("E2E_TEST_MODE") != "1":
        return event.text, actor_id, False
    envelope = None
    try:
        envelope = json.loads(event.text)
        payload = envelope["event"]
        signature = envelope["signature"]
        injected = InboundEvent(
            event_id=payload["event_id"],
            user_id=payload["user_id"],
            channel_id=payload["channel_id"],
            text=payload["text"],
        )
        secret = os.environ["INTEROP_E2E_SECRET"].encode("utf-8")
    except json.JSONDecodeError:
        return event.text, actor_id, False
    except (KeyError, TypeError, ValueError):
        if isinstance(envelope, dict) and "event" in envelope and "signature" in envelope:
            LOGGER.warning("interop signed injection rejected")
            return "", actor_id, True
        return event.text, actor_id, False
    if accept_test_event(injected, signature, secret, e2e_test_mode=True):
        LOGGER.info("interop signed injection accepted")
        return injected.text, injected.user_id, False
    LOGGER.warning("interop signed injection rejected")
    return "", actor_id, True


def _config_payload() -> dict[str, object]:
    config_path = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid private interop config")
    return payload


def _config() -> dict[str, str]:
    payload = _config_payload()
    agent_id = payload.get("agent_id")
    channel_id = payload.get("agents_log_channel_id")
    owner_id = payload.get("owner_id")
    if not isinstance(agent_id, str) or not isinstance(channel_id, str) or not isinstance(owner_id, str):
        raise ValueError("invalid private interop config")
    interop_channel_id = payload.get("interop_channel_id")
    interop_channel_id = interop_channel_id if isinstance(interop_channel_id, str) else ""
    return {
        "agent_id": agent_id,
        "agents_log_channel_id": channel_id,
        "owner_id": owner_id,
        "interop_channel_id": interop_channel_id,
    }


def _pause_store() -> PauseStore:
    config = _config()
    return PauseStore(
        state_file=Path("~/.hermes/interop/paused").expanduser(),
        owner_id=config["owner_id"],
    )


def _masked_actor(actor_id: str) -> str:
    return f"…{actor_id[-4:]}" if len(actor_id) > 4 else "<short>"
