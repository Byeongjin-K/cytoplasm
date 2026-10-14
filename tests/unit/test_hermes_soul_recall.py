"""Agent SOUL.md recall line (`automation.hermes_soul_recall`) — one line ensured, nothing else touched."""
from __future__ import annotations

from pathlib import Path

import pytest

from automation import hermes_soul_recall as soul

DEFAULT = "You are a helpful assistant.\nBe direct.\n"


def _home(tmp_path: Path, text: str | None) -> Path:
    (tmp_path / ".hermes").mkdir()
    if text is not None:
        _ = (tmp_path / ".hermes" / "SOUL.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_apply_appends_the_line_once_and_keeps_the_owner_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = _home(tmp_path, DEFAULT)
    assert soul.main(["--check"], home=home) == 1
    assert soul.main(["--apply"], home=home) == 0
    after = (home / ".hermes" / "SOUL.md").read_text(encoding="utf-8")
    assert after == f"{DEFAULT.rstrip()}\n\n{soul.LINE}\n"
    assert soul.main(["--apply"], home=home) == 0
    assert "unchanged" in capsys.readouterr().out
    assert (home / ".hermes" / "SOUL.md").read_text(encoding="utf-8") == after
    assert len(list((home / ".hermes").glob("SOUL.md.bak-soul-recall-*"))) == 1
    assert soul.main(["--check"], home=home) == 0


def test_a_missing_soul_is_never_created(tmp_path: Path) -> None:
    home = _home(tmp_path, None)
    assert soul.main(["--apply"], home=home) == 3
    assert not (home / ".hermes" / "SOUL.md").exists()
