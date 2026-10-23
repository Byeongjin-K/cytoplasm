"""Reply-draft closing: absent marker keeps today's prompt; omit drops the sign-off."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills/mail/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import triage_core  # noqa: E402
from automation import mail_reply_closing  # noqa: E402
from test_mail_reply_quote import _detail, _only_draft, _run_cli, _sent_body, _setup_env, _write_stub  # noqa: E402

PROMPTS = REPO / "skills/mail/prompts"
SIGNED_REPLY = "참석 가능합니다.\n\n검토하신 후 의견 주시면 감사하겠습니다.\n\n감사합니다.\n홍길동 올림"


@pytest.fixture
def drafting(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path))
    capture = tmp_path / "draft-prompt.txt"
    hermes = _write_stub(
        tmp_path / "hermes-closing-stub",
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "prompt = sys.argv[sys.argv.index('-z') + 1]\n"
        "if '\"category\"' in prompt:\n"
        "    print(json.dumps({'category': 'important', 'reply_needed': True, 'schedule_needed': False,"
        " 'budget': False, 'schedule_text': '', 'reason': 'test'}))\n"
        "    sys.exit(0)\n"
        "pathlib.Path(" + repr(str(capture)) + ").write_text(prompt, encoding='utf-8')\n"
        f"print(json.dumps({{'subject': '', 'body': {SIGNED_REPLY!r}}}))\n",
    )
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))
    return home / ".hermes/mail-reply-closing.mode", capture


def _draft(monkeypatch) -> str:
    assert _run_cli(monkeypatch, "draft", "--uid", "u-1", "--instruction", "참석", "--no-post") == 0
    return _sent_body(_only_draft()[1])


@pytest.mark.parametrize("marker", [None, b"keep"])
def test_keep_sends_the_unchanged_v2_prompt_and_body(drafting, monkeypatch, marker):
    # Given: no marker (every node today) or an explicit keep.
    path, capture = drafting
    if marker is not None:
        path.write_bytes(marker)
    # When
    body = _draft(monkeypatch)
    # Then: the model saw exactly today's prompt and the reply keeps the model's closing.
    template = triage_core.load_prompt_template(PROMPTS / "reply-draft-v2.md")
    received = capture.read_text(encoding="utf-8")
    assert received.startswith(template.split("{{SUBJECT}}")[0])
    assert received.endswith("참석" + template.split("{{INSTRUCTION}}")[1])
    assert '"감사합니다." → "<owner-name> 올림" 순으로 고정한다' in received
    assert body.startswith("참석 가능합니다.\n\n검토하신 후 의견 주시면 감사하겠습니다.\n\n감사합니다.\n홍길동 올림\n")


def test_omit_asks_for_no_closing_and_drops_one_the_model_still_wrote(drafting, monkeypatch):
    # Given: the released omit marker.
    path, capture = drafting
    path.write_bytes(b"omit")
    # When
    body = _draft(monkeypatch)
    # Then: no closing instruction, the signature note is explicit, the body ends at the request.
    received = capture.read_text(encoding="utf-8")
    assert "올림\" 순으로 고정" not in received
    assert "발송 계정의 기본 서명이 맺음말과 이름을 붙인다" in received
    reply = body.split("-----원본 메시지-----")[0].rstrip()
    assert reply == "참석 가능합니다.\n\n검토하신 후 의견 주시면 감사하겠습니다."


@pytest.mark.parametrize("marker", [b"on", b"omit\n", b"", b"OMIT"])
def test_unreadable_marker_refuses_the_draft(drafting, monkeypatch, capsys, marker):
    # Given
    path, _capture = drafting
    path.write_bytes(marker)
    # When
    code = _run_cli(monkeypatch, "draft", "--uid", "u-1", "--instruction", "참석", "--no-post")
    # Then: fail closed instead of guessing either closing.
    assert code == 3
    assert "맺음말 표식" in capsys.readouterr().err


def test_v1_prompt_closing_rule_is_replaced_too():
    template = triage_core.load_prompt_template(PROMPTS / "reply-draft-v1.md")
    changed = mail_reply_closing.apply_to_template(template, "omit")
    assert '"감사합니다."로 끝낸다' not in changed and "맺음말을 쓰지 않는다" in changed
    assert mail_reply_closing.apply_to_template(template, "keep") == template


def test_omit_refuses_a_prompt_without_a_known_closing_rule():
    with pytest.raises(ValueError, match="no known closing rule"):
        mail_reply_closing.apply_to_template("{{SUBJECT}} {{SENDER}} {{BODY}}", "omit")


@pytest.mark.parametrize("body,expected", [
    ("확인 부탁드립니다.\n\n감사합니다.\n홍길동 올림", "확인 부탁드립니다."),
    ("확인 부탁드립니다.\n감사합니다.", "확인 부탁드립니다."),
    ("확인 부탁드립니다.\n\n가상기관 홍길동 드림.", "확인 부탁드립니다."),
    ("감사합니다. 자료는 내일 보내겠습니다.", "감사합니다. 자료는 내일 보내겠습니다."),
    ("검토 결과를 표로 정리해 올림", "검토 결과를 표로 정리해 올림"),
    ("감사합니다.", "감사합니다."),
])
def test_strip_removes_only_trailing_sign_off_lines(body, expected):
    assert mail_reply_closing.strip_sign_off(body, "omit") == expected
    assert mail_reply_closing.strip_sign_off(body, "keep") == body
