"""The node runs mail_sync_watch.py as a lone copy in ~/.hermes/scripts (owner card Q60, 2026-11-16).

v2.7.41 imported mail_user_folders_mode from beside the script, so every 10-minute collection failed
there with No module named 'mail_user_folders_mode'. The copy must find the mail modules beside the
wrapper, in the live mail skill.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills/mail/scripts"


def test_a_lone_copy_of_the_watcher_reads_the_switch_from_the_mail_skill(tmp_path) -> None:
    alone = tmp_path / "scripts"
    alone.mkdir()
    shutil.copy2(SCRIPTS / "mail_sync_watch.py", alone / "mail_sync_watch.py")
    code = "import mail_sync_watch as w; print(w._scope())"
    result = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(alone)!r}); {code}"],
        cwd=alone, capture_output=True, text=True, timeout=60, check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "MAIL_SYNC_WRAPPER_CLI": str(SCRIPTS / "mail_wrapper.py")},
    )
    assert result.returncode == 0, result.stderr
    assert "No module named" not in result.stderr
    assert result.stdout.strip() in ("[]", "['--user-folders']")
