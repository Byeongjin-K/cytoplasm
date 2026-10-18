"""Public install routing: Dori by default, named legacy profiles by explicit choice."""
from __future__ import annotations

import argparse
from collections.abc import Sequence

from automation.install.profiles import DEFAULT_PROFILE, PROFILES


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.install",
        description="도리 기본 설치. 기존 노드의 Hermes 설치기는 명시적 레거시 프로필로만 선택한다.",
        add_help=False,
    )
    parser.add_argument("--profile", choices=("dori", *PROFILES), default=DEFAULT_PROFILE)
    args, remaining = parser.parse_known_args(argv)
    if args.profile == "dori":
        from automation.install.dori import main as install_dori

        return install_dori(remaining)
    from automation.install.installer import main as install_legacy

    return install_legacy(("--profile", args.profile, *remaining))
