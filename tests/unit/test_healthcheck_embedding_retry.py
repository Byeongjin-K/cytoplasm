"""The embedding probe tolerates one slow answer but not two.

Kept in its own file: tests/AGENTS.md forbids adding cases to files whose output hashes the FS3
replay gate pins.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

import pytest

_PROBES: Final = Path(__file__).resolve().parents[2] / "automation" / "healthcheck_probes.sh"
_OK: Final = '{"dimensions":1024,"model":"BAAI/bge-m3","status":"ok"}'


def _probe(tmp_path: Path, answers: list[str]) -> tuple[int, list[str]]:
    calls = tmp_path / "calls"
    script = f"""
source "{_PROBES}"
valid_account() {{ return 0; }}
valid_http_url() {{ return 0; }}
answers=({" ".join(repr(a) for a in answers)})
capture_on_node() {{ printf '%s\\n' "$2" >> "{calls}"; n=$(wc -l < "{calls}"); a="${{answers[$((n-1))]}}"; [ "$a" = FAIL ] && return 28; printf '%s' "$a"; }}
probe_embedding_health node ops http://127.0.0.1:8001/health
"""
    result = subprocess.run(("bash", "-c", script), capture_output=True, text=True, timeout=30, check=False)
    return result.returncode, calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize(("answers", "rc", "tries"), [
    ([_OK], 0, 1),
    (["FAIL", _OK], 0, 2),
    (["FAIL", "FAIL"], 1, 2),
    (["{}", "{}"], 1, 2),
])
def test_one_slow_answer_is_retried_once_and_two_stay_a_failure(
    tmp_path: Path, answers: list[str], rc: int, tries: int,
) -> None:
    # Given / When
    code, calls = _probe(tmp_path, answers)
    # Then
    assert code == rc
    assert len(calls) == tries
    assert all("--max-time 10" in call for call in calls)
