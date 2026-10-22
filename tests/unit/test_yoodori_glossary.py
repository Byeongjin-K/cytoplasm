"""YooDori's correction ledger commands (owner 2026-10-22 Q359): glossary-add and glossary-list.

New file rather than more cases in test_yoodori_gate.py, which pins the read/card argv byte for
byte. The add job is run through the real term_glossary CLI so the gate argv and the writer are
checked together.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import unit_node

from automation.yoodori_gate import yoodori_read_gate as gate

REPO = Path(__file__).resolve().parents[2]
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]


def test_glossary_add_is_an_owner_command_write_job():
    # Given the owner's correction relayed by YooDori with his message as origin.
    job = gate.build_job(["glossary-add", "주간기관", "주관기관", *ORIGIN])
    # When the gate builds the job, then it is a write that needs the owner's message.
    assert job.argv == ["/usr/bin/python3", "-m", "automation.term_glossary", "add",
                        "--kind", "lifelog", "주간기관", "주관기관"]
    assert job.write is True and job.needs_origin is True
    assert (job.channel_id, job.message_id) == ("123456789", "987654321")
    assert job.label == "용어집 주간기관 → 주관기관"


def test_glossary_add_takes_the_meeting_kind():
    # Given a meeting-minutes correction.
    job = gate.build_job(["glossary-add", "--kind", "meeting", "한건전", "한건연", *ORIGIN])
    # When built, then it writes the meeting layer.
    assert job.argv[-4:] == ["--kind", "meeting", "한건전", "한건연"]


@pytest.mark.parametrize("words", [
    ["glossary-add", "주간기관", *ORIGIN],
    ["glossary-add", "주간기관", "주관기관", "세번째", *ORIGIN],
    ["glossary-add", "--kind", "mail", "가", "나", *ORIGIN],
    ["glossary-add", "주간기관", "--out", *ORIGIN],
    ["glossary-add", "주간기관", "주관기관"],
])
def test_glossary_add_refuses_malformed_requests(words):
    # Given a missing word, an extra word, an unknown kind, a smuggled flag or no origin.
    # When the gate parses it, then it refuses.
    with pytest.raises(gate.Refused):
        gate.build_job(words)


def test_glossary_list_is_a_read():
    # Given YooDori showing the owner the current ledger.
    name, argv, _ = gate.build(["glossary-list"])
    # When built, then it only lists.
    assert name == "glossary-list"
    assert argv == ["/usr/bin/python3", "-m", "automation.term_glossary", "list"]
    assert gate.build(["glossary-list", "--kind", "meeting"])[1][-2:] == ["--kind", "meeting"]
    with pytest.raises(gate.Refused):
        gate.build(["glossary-list", "extra"])


def test_add_job_argv_writes_the_row_through_the_real_cli(tmp_path):
    # Given the add job's argv and a node with Drive publishing off.
    job = gate.build_job(["glossary-add", "주간기관", "주관기관", *ORIGIN])
    env = {**unit_node.env(), "HOME": str(tmp_path), "TERM_GLOSSARY_CACHE": str(tmp_path / "cache"),
           "PYTHONPATH": str(REPO)}
    env.pop("DRIVE_PUBLISH_ENABLED", None)
    # When it runs, then the row lands in the node copy and the list shows it.
    added = subprocess.run([sys.executable, *job.argv[1:]], capture_output=True, text=True,
                           env=env, cwd=REPO, check=False)
    assert added.returncode == 0, added.stderr
    assert "주간기관,주관기관" in (tmp_path / "cache" / "lifelog.csv").read_text(encoding="utf-8")
    _, argv, _ = gate.build(["glossary-list"])
    listed = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True,
                            env=env, cwd=REPO, check=False)
    assert listed.stdout == "주간기관,주관기관\n"
    assert os.path.exists(tmp_path / "cache" / "lifelog.csv")


def test_list_without_kind_merges_lifelog_and_meeting(tmp_path):
    # Given one row in each kind's node copy, the same wrong form spelled differently in meeting.
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "lifelog.csv").write_text("주간기관,주관기관\n한건전,한건영\n", encoding="utf-8")
    (cache / "meeting.csv").write_text("한건전,한건연\n", encoding="utf-8")
    env = {**unit_node.env(), "HOME": str(tmp_path), "TERM_GLOSSARY_CACHE": str(cache), "PYTHONPATH": str(REPO)}
    env.pop("DRIVE_PUBLISH_ENABLED", None)
    # When the ledger is listed with no kind, then both kinds come out as wrong,right rows, meeting winning.
    listed = subprocess.run([sys.executable, "-m", "automation.term_glossary", "list"],
                            capture_output=True, text=True, env=env, cwd=REPO, check=False)
    assert listed.stdout == "주간기관,주관기관\n한건전,한건연\n"
