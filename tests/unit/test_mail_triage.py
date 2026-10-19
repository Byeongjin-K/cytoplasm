"""W4-2 mail triage — shared-model calls, LLM contract parsing,
draft binding, claim idempotency, consecutive-failure mail-mode downgrade,
#approvals sanitization, and hash parity with the deployed external-effect
gate's mailon_send rule."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_core  # noqa: E402
import triage_llm  # noqa: E402
import triage_store  # noqa: E402
from automation.interop import effect_approval  # noqa: E402

CANARY = "PSEUDOSECRET-cafe0123"  # synthetic; must never surface in approvals text


def test_call_codex_pins_provider_and_honors_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: argv 를 그대로 기록하는 hermes 대역.
    argv_log = tmp_path / "argv.json"
    hermes = _write_stub(
        tmp_path / "hermes-stub",
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "pathlib.Path(" + repr(str(argv_log)) + ").write_text(json.dumps(sys.argv))\n"
        "print('{}')\n",
    )
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))

    # When: 일반 분류 요청 한 건이 나간다.
    result = triage_llm.call_codex("return JSON", timeout=30.0)

    # Then: 주 경로는 Codex 로 고정되고, 사용자 설정(fallback_providers 체인)은 살아 있다.
    argv = json.loads(argv_log.read_text(encoding="utf-8"))
    assert result.text == "{}"
    assert "--ignore-user-config" not in argv  # 붙으면 Hermes 폴백 체인이 꺼진다
    assert not {"--provider", "-m"} & set(argv)  # 모델은 계정 설정이 정한다
    assert argv[argv.index("-z") + 1] == "return JSON"


# --- ② classification contract ---------------------------------------------------

def test_parse_classification_happy() -> None:
    raw = 'noise {"category": "Important", "reply_needed": true, "schedule_needed": false, "budget": false, "schedule_text": "", "reason": "회신 요청"} tail'
    cls = triage_core.parse_classification(raw)
    assert cls.category == "important"
    assert cls.reply_needed is True
    assert cls.flags() == ("reply_needed",)


def test_parse_classification_rejects_unknown_category() -> None:
    with pytest.raises(triage_core.LlmParseError):
        triage_core.parse_classification('{"category": "urgent"}')


def test_parse_classification_rejects_stringy_bool_flags() -> None:
    # The classifier sometimes emits the JSON booleans as strings; "false" must
    # NOT become True and spuriously trigger a calendar draft.
    raw = ('{"category": "important", "reply_needed": "false", '
           '"schedule_needed": "false", "budget": "false", '
           '"schedule_text": "", "reason": "x"}')
    cls = triage_core.parse_classification(raw)
    assert cls.reply_needed is False
    assert cls.schedule_needed is False
    assert cls.budget is False
    assert cls.flags() == ()


def test_parse_classification_accepts_real_bool_flags() -> None:
    raw = ('{"category": "important", "reply_needed": true, '
           '"schedule_needed": false, "budget": true, '
           '"schedule_text": "", "reason": "x"}')
    cls = triage_core.parse_classification(raw)
    assert cls.reply_needed is True
    assert cls.schedule_needed is False
    assert cls.budget is True


def test_parse_reply_requires_body() -> None:
    subject, body = triage_core.parse_reply('{"subject": "Re: x", "body": "감사합니다."}')
    assert (subject, body) == ("Re: x", "감사합니다.")
    with pytest.raises(triage_core.LlmParseError):
        triage_core.parse_reply('{"subject": "Re: x", "body": ""}')


def test_prompt_template_is_line_anchored_and_substitutes() -> None:
    template = triage_core.load_prompt_template(
        _REPO / "skills" / "mail" / "prompts" / "triage-classify-v1.md"
    )
    assert "버전 파일명" not in template  # header prose must not leak (W2-3 lesson)
    prompt = triage_core.build_prompt(template, subject="S", sender="X", body="B")
    assert "S" in prompt and "{{SUBJECT}}" not in prompt


def test_prompt_body_is_truncated() -> None:
    template = "s={{SUBJECT}} f={{SENDER}} b={{BODY}}"
    prompt = triage_core.build_prompt(template, subject="s", sender="f", body="x" * 10000)
    assert len(prompt) < 6100


# --- reply address / subject helpers ---------------------------------------------

def test_extract_reply_address() -> None:
    assert triage_core.extract_reply_address("홍길동 <a.b@inst.re.kr>") == "a.b@inst.re.kr"
    assert triage_core.extract_reply_address("no-address-here") == ""


def test_reply_subject_fallback() -> None:
    assert triage_core.reply_subject("", "회의 안내") == "Re: 회의 안내"
    assert triage_core.reply_subject("", "RE: 회의 안내") == "RE: 회의 안내"
    assert triage_core.reply_subject("Re: 직접", "x") == "Re: 직접"


# --- draft binding + owner-DM rendering --------------------------------------------

def _draft(*, sensitive: bool) -> dict:
    record = {
        "argv": ["py", "-m", "mailon.main", "send", "--to", "me@inst.re.kr",
                 "--subject", f"Re: 특허 {CANARY}", "--body", f"본문 {CANARY}",
                 "--confirm-send", "--json"],
        "body": f"본문 {CANARY}",
        "category": "important",
        "flags": ["reply_needed"],
        "id": "abc123",
        "mail_subject": f"특허 출원 {CANARY}",
        "message_id": "",
        "sender": "발신자 <p@inst.re.kr>",
        "sender_masked": triage_core.mask_value("발신자 <p@inst.re.kr>"),
        "sensitive": sensitive,
        "status": "pending",
        "subject": f"Re: 특허 {CANARY}",
        "tags": ["private"] if sensitive else [],
        "to": "me@inst.re.kr",
        "uid": "u-1",
        "uid_opaque": triage_core.mask_value("u-1"),
    }
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def test_non_sensitive_approvals_message_shows_reply_text() -> None:
    draft = _draft(sensitive=False)
    draft["subject"] = "Re: 일정 회신"
    draft["body"] = "가능합니다."
    draft["mail_subject"] = "일정 문의"
    message = triage_core.render_approvals_message(draft)
    assert "가능합니다." in message and "abc123" in message
    assert "p@inst.re.kr" not in message  # sender always masked


def test_draft_sha_binds_content() -> None:
    draft = _draft(sensitive=True)
    tampered = {**draft, "body": "다른 본문"}
    assert triage_core.draft_sha256(tampered) != draft["sha256"]


# --- external-effect gate parity (deployed pre_tool_call rule mailon_send) --------

def test_action_hash_parity_with_deployed_gate(tmp_path: Path) -> None:
    argv = tuple(_draft(sensitive=False)["argv"])
    call = effect_approval.ToolCall(
        tool_name=triage_core.EXTERNAL_EFFECT_TOOL,
        arguments={"command": shlex.join(argv)},
    )
    log = tmp_path / "approvals.jsonl"
    context = effect_approval.ApprovalContext(
        approval_log=log, owner_id="owner-1", e2e_test_mode=False
    )
    decision = effect_approval.validate_approval(call, triage_core.EXTERNAL_EFFECT_TARGET_ID, context)
    assert decision.external_effect is True and decision.allowed is False
    assert decision.target_id == triage_core.EXTERNAL_EFFECT_TARGET_ID
    assert decision.action_hash == triage_core.external_effect_action_hash(argv)
    log.write_text(json.dumps({
        "action": "external_effect.approval",
        "approval": {"channel": "approvals", "message_id": "m", "method": "manual_reaction",
                     "owner_id": "owner-1"},
        "hash": triage_core.external_effect_action_hash(argv),
        "result": {"status": "approved"},
        "target_id": triage_core.EXTERNAL_EFFECT_TARGET_ID,
        "timestamp": "2026-07-16T00:00:00Z",
    }) + "\n", encoding="utf-8")
    assert effect_approval.validate_approval(call, triage_core.EXTERNAL_EFFECT_TARGET_ID, context).allowed is True


# --- idempotency: claim-before-draft ----------------------------------------------

def test_claim_mail_is_single_winner(tmp_path: Path) -> None:
    db = tmp_path / "triage.db"
    assert triage_store.claim_mail(db, "u-1", "t0") is True
    assert triage_store.claim_mail(db, "u-1", "t1") is False
    triage_store.release_mail(db, "u-1")
    assert triage_store.claim_mail(db, "u-1", "t2") is True


def test_processed_marker_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "triage.db"
    assert triage_store.is_processed(db, "u-1") is False
    triage_store.record_processed(
        db, "u-1", category="important", action="draft:abc", processed_at="t"
    )
    assert triage_store.is_processed(db, "u-1") is True


# --- consecutive send failures → NO-GO downgrade -----------------------------------

def test_failure_counter_bumps_and_resets(tmp_path: Path) -> None:
    db = tmp_path / "triage.db"
    assert triage_store.consecutive_send_failures(db) == 0
    assert triage_store.bump_send_failures(db) == 1
    assert triage_store.bump_send_failures(db) == 2
    triage_store.reset_send_failures(db)
    assert triage_store.consecutive_send_failures(db) == 0


def test_downgrade_writes_runtime_no_go_and_switch_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import triage_mode
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_FILE", str(tmp_path / "gate" / "mail-mode.json"))
    repo_mode = tmp_path / "repo-mail-mode.json"
    repo_mode.write_text(json.dumps({"mode": "full-go", "decided_at": "x", "source": "W0-7c"}))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_REPO", str(repo_mode))
    assert triage_mode.effective_mode() == "full-go"
    triage_mode.downgrade_to_no_go("approved mailon send failed 2 consecutive times")
    assert triage_mode.effective_mode() == "no-go"  # runtime override beats repo full-go
    runtime = json.loads((tmp_path / "gate" / "mail-mode.json").read_text())
    assert runtime["mode"] == "no-go" and runtime["source"] == "W4-2-runtime"
    switch = json.loads((tmp_path / "gate" / "mode-switch.jsonl").read_text().splitlines()[0])
    assert switch["event"] == "w4-1n-switch" and switch["to"] == "no-go"


def test_effective_mode_reads_seed_when_runtime_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import triage_mode
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_FILE", str(tmp_path / "runtime" / "mail-mode.json"))
    seed = tmp_path / "checkout" / "configs" / "mail-mode.default.json"
    seed.parent.mkdir(parents=True)
    seed.write_text(
        json.dumps({"mode": "full-go", "decided_at": "2026-07-15T12:29:23Z", "source": "W0-7c"})
    )
    monkeypatch.setenv("TRIAGE_MAIL_MODE_REPO", str(seed))
    assert triage_mode.effective_mode() == "full-go"


def test_downgrade_writes_only_runtime_file_seed_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import triage_mode
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    runtime = tmp_path / "runtime" / "mail-mode.json"
    monkeypatch.setenv("TRIAGE_MAIL_MODE_FILE", str(runtime))
    seed = tmp_path / "checkout" / "configs" / "mail-mode.default.json"
    seed.parent.mkdir(parents=True)
    seed.write_text(
        json.dumps({"mode": "full-go", "decided_at": "2026-07-15T12:29:23Z", "source": "W0-7c"})
    )
    monkeypatch.setenv("TRIAGE_MAIL_MODE_REPO", str(seed))
    before = seed.read_bytes()
    triage_mode.downgrade_to_no_go("x")
    written = json.loads(runtime.read_text())
    assert written["mode"] == "no-go" and written["source"] == "W4-2-runtime"
    assert seed.read_bytes() == before
    assert list(seed.parent.iterdir()) == [seed]


@pytest.mark.parametrize("runtime_name", ("mail-mode.default.json", "mail-mode.json"))
def test_runtime_path_inside_checkout_fails_closed(
    runtime_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import triage_mode
    seed = tmp_path / "checkout" / "configs" / "mail-mode.default.json"
    seed.parent.mkdir(parents=True)
    seed.write_text(
        json.dumps({"mode": "full-go", "decided_at": "2026-07-15T12:29:23Z", "source": "W0-7c"})
    )
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_FILE", str(seed.parent / runtime_name))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_REPO", str(seed))
    before = seed.read_bytes()
    # full-go seed must NOT leak through a runtime path shadowing the checkout dir
    assert triage_mode.effective_mode() == "no-go"
    triage_mode.downgrade_to_no_go("guard")
    assert seed.read_bytes() == before
    assert set(seed.parent.iterdir()) == {seed}  # nothing written into the checkout
    assert (tmp_path / "gate" / "mode-switch.jsonl").exists()  # audit still lands


def test_repo_mode_file_default_is_the_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    import triage_mode
    monkeypatch.delenv("TRIAGE_MAIL_MODE_REPO", raising=False)
    assert triage_mode.repo_mode_file() == Path(
        "/srv/autophagy-agents/configs/mail-mode.default.json"
    )


def test_effective_mode_fails_closed_to_no_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import triage_mode
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_FILE", str(tmp_path / "rt" / "absent.json"))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_REPO", str(tmp_path / "seed" / "also-absent.json"))
    assert triage_mode.effective_mode() == "no-go"


# --- masking helpers ---------------------------------------------------------------

def test_mask_value_shape_and_determinism() -> None:
    masked = triage_core.mask_value("발신자 <p@inst.re.kr>")
    assert masked.startswith("sha256:") and len(masked) == len("sha256:") + 16
    assert masked == triage_core.mask_value("발신자 <p@inst.re.kr>")


def test_redact_hides_emails_and_long_digits() -> None:
    redacted = triage_core.redact("fail p@inst.re.kr code 1234567890")
    assert "p@inst.re.kr" not in redacted and "1234567890" not in redacted


def test_send_argv_shape_matches_w07b_contract() -> None:
    argv = triage_core.build_send_argv("py", "a@b.kr", "Re: s", "b")
    assert argv[:4] == ("py", "-m", "mailon.main", "send")
    assert argv[-2:] == ("--confirm-send", "--json")


def test_wrapper_subprocess_env_prepends_user_local_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mailon needs agent-browser from ~/.local/bin even under bare cron/sudo PATH."""
    import mail_wrapper
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = mail_wrapper.build_subprocess_env({"env_file": tmp_path / "absent"})
    assert env["PATH"].split(":")[0] == str(tmp_path / ".local/bin")
    env2 = {**env}
    monkeypatch.setenv("PATH", env2["PATH"])
    assert mail_wrapper.build_subprocess_env({"env_file": tmp_path / "absent"})["PATH"].count(
        str(tmp_path / ".local/bin")
    ) == 1



# --- instruction-aware prompts + digest summary (W4-6) -----------------------------

_PROMPTS = _REPO / "skills" / "mail" / "prompts"


def _write_stub(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_build_prompt_replaces_instruction_placeholder() -> None:
    template = "s={{SUBJECT}} f={{SENDER}} b={{BODY}} i={{INSTRUCTION}}"
    prompt = triage_core.build_prompt(
        template, subject="S", sender="X", body="B", instruction="짧게 답해라"
    )
    assert "짧게 답해라" in prompt and "{{INSTRUCTION}}" not in prompt


def test_build_prompt_rejects_instruction_without_placeholder() -> None:
    template = "s={{SUBJECT}} f={{SENDER}} b={{BODY}}"  # v1-style, no {{INSTRUCTION}}
    with pytest.raises(ValueError, match="INSTRUCTION"):
        triage_core.build_prompt(
            template, subject="S", sender="X", body="B", instruction="지시 있음"
        )


def test_build_prompt_empty_instruction_uses_default_marker() -> None:
    template = "s={{SUBJECT}} f={{SENDER}} b={{BODY}} i={{INSTRUCTION}}"
    prompt = triage_core.build_prompt(template, subject="S", sender="X", body="B")
    assert "(별도 지시 없음)" in prompt and "{{INSTRUCTION}}" not in prompt


def test_reply_v2_template_has_all_placeholders() -> None:
    template = triage_core.load_prompt_template(_PROMPTS / "reply-draft-v2.md")
    assert "버전 파일명" not in template  # header prose must not leak (W2-3 lesson)
    for placeholder in ("{{SUBJECT}}", "{{SENDER}}", "{{BODY}}", "{{INSTRUCTION}}"):
        assert placeholder in template, placeholder


def test_digest_summary_template_loads_and_substitutes() -> None:
    template = triage_core.load_prompt_template(_PROMPTS / "digest-summary-v1.md")
    assert "버전 파일명" not in template
    prompt = triage_core.build_prompt(template, subject="S1", sender="X1", body="B1")
    assert "S1" in prompt and "X1" in prompt and "B1" in prompt
    assert "{{SUBJECT}}" not in prompt


def test_parse_digest_summary_happy_and_rejects_empty() -> None:
    raw = 'noise {"summary": "장비 사용 일정 확인 요청"} tail'
    assert triage_core.parse_digest_summary(raw) == "장비 사용 일정 확인 요청"
    with pytest.raises(triage_core.LlmParseError):
        triage_core.parse_digest_summary('{"summary": ""}')
    with pytest.raises(triage_core.LlmParseError):
        triage_core.parse_digest_summary('{"other": "x"}')


def test_summarize_non_sensitive_uses_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = _write_stub(
        tmp_path / "hermes-stub",
        "#!/usr/bin/env python3\n"
        "print('{\"summary\": \"ok\"}')\n",
    )
    log = tmp_path / "llm-calls.jsonl"
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(codex))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(log))
    summary = triage_llm.summarize(
        subject="S", sender="X", body="B",
        uid_opaque="sha256:u", prompt_path=_PROMPTS / "digest-summary-v1.md",
    )
    assert summary == "ok"
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert record["purpose"] == "digest_summary"
    assert record["provider"] == triage_llm.CODEX_PROVIDER
    assert record["model"] == triage_llm.codex_model()


def test_summarize_sensitive_routes_to_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hermes = _write_stub(
        tmp_path / "hermes-stub",
        "#!/usr/bin/env python3\n"
        "print('{\"summary\": \"ok\"}')\n",
    )
    log = tmp_path / "llm-calls.jsonl"
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(log))
    summary = triage_llm.summarize(
        subject=f"특허 {CANARY}", sender="X", body=f"본문 {CANARY}",         uid_opaque="sha256:u", prompt_path=_PROMPTS / "digest-summary-v1.md",
    )
    assert summary == "ok"
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert record["purpose"] == "digest_summary"
    assert record["provider"] == triage_llm.CODEX_PROVIDER


def test_draft_reply_passes_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "received-prompt.txt"
    hermes = _write_stub(
        tmp_path / "hermes-stub",
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "pathlib.Path(" + repr(str(capture)) + ").write_text(sys.argv[sys.argv.index('-z') + 1], encoding='utf-8')\n"
        "print('{\"subject\": \"Re: S\", \"body\": \"감사합니다.\"}')\n",
    )
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(tmp_path / "llm-calls.jsonl"))
    instruction = "CANARY-지시: 회의 일정은 다음 달로 미룬다고 답하라"
    subject, body, provider = triage_llm.draft_reply(
        subject="S", sender="X", body="B",
        uid_opaque="sha256:u", prompt_path=_PROMPTS / "reply-draft-v2.md",
        instruction=instruction,
    )
    assert instruction in capture.read_text(encoding="utf-8")
    assert (subject, body, provider) == ("Re: S", "감사합니다.", triage_llm.CODEX_PROVIDER)
