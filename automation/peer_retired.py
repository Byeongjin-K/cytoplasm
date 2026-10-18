"""Release-bound P5 retirement; no root execution or account deletion."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Final

SURFACE: Final = "peer-retired:retired"
MARKER: Final = ".hermes/peer-retired"
CARD_LINES: Final = (
    "P5 제거: peer 계정·봇·attestation, Hermes 게이트웨이 유닛·플러그인·드롭인·번들 스킬·자가 스킬 감사·SOUL·옛 설정 백업 제거(백업 뒤)",
    "멈춘 Hermes 잡 33개 삭제(형 Q238, 백업 뒤)",
)
# The account, bot and /etc cleanup is an explicit owner operation, not this release's
# non-root effect. Keep that distinction on the card as well as in the release notes.
ROOT_NOTE: Final = "peer 계정·봇·/etc 제거는 별도 소유자 블록(이번 릴리스는 비루트 제거만)"


def surface() -> tuple[str, str]:
    return SURFACE, hashlib.sha256(b"retired").hexdigest()


def release_switch(home: Path) -> str:
    try:
        value = (home / ".hermes/release-flags/peer-retired").read_bytes()
    except FileNotFoundError:
        return "active"
    if value != b"retired":
        raise ValueError("PEER-RETIRED-REFUSED: expected the literal word retired only")
    return "retired"


def retired(home: Path) -> bool:
    try:
        return (home / MARKER).read_bytes() == b"retired"
    except FileNotFoundError:
        return False


def retired_destination(account: str, destination: str, source: str = "") -> bool:
    """Project retirement without changing the default legacy installation contract."""
    return account == "peer" or (
        destination.startswith((
            ".hermes/plugins/", ".hermes/interop_runtime/", ".hermes/skill-generation/",
            ".config/systemd/user/hermes-gateway.service",
        ))
        or destination in (
            ".hermes/interop/production_guard.sh", ".hermes/interop/pin_import_root.sh",
            ".hermes/interop/gate_driver.py", ".hermes/scripts/selfskill_audit_watch.py",
            "selfskill-audit-watch",
        )
        or source.startswith("automation/hermes_memory_config.py")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--release-switch", action="store_true")
    modes.add_argument("--retired-destination", nargs=3)
    args = parser.parse_args()
    if args.retired_destination is not None:
        return 0 if retired_destination(*args.retired_destination) else 1
    print(release_switch(Path.home()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
