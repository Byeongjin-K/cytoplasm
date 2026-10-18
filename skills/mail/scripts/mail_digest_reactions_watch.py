#!/usr/bin/env python3
"""20-second entry; mail_triage_watch loads ~/.env.secrets and passes the runtime root."""
from __future__ import annotations

from mail_triage_watch import digest_card


if __name__ == "__main__":
    raise SystemExit(digest_card("", scan=True))
