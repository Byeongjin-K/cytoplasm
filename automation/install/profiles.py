"""Install profiles — the operator's one choice that fixes the healthcheck declaration.

`healthcheck_registry.sh` accepts `HEALTHCHECK_SERVICES` as a space-separated subset of
its service groups; a profile is a named, validated value for it. The installer renders
that value into a root-owned file so a fresh node never derives the declaration by hand.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final


class UnknownProfileError(RuntimeError):
    """A requested profile name is not in the registry; nothing is installed."""


@dataclass(frozen=True, slots=True)
class InstallProfile:
    name: str
    #: Service groups in `HEALTHCHECK_SERVICE_GROUPS` order (core report-hub rag).
    healthcheck_services: tuple[str, ...]
    components: tuple[str, ...] = ()


HEALTHCHECK_DECLARATION_PATH: Final = Path("/etc/autophagy/healthcheck.env")
DEFAULT_PROFILE: Final = "dori"

#: 프로필로 설치하면 doctor 알람 워처가 함께 온다 — 설치 뒤 끊긴 연결을 사람이 기억해 확인할 필요가 없다.
_DOCTOR: Final = "doctor-watch"

PROFILES: Final[Mapping[str, InstallProfile]] = {
    "core": InstallProfile("core", ("core",), (_DOCTOR,)),
    # RAG는 RAG_NODE에 deploy.sh가 SSH로 배포하므로 주 노드 설치기 컴포넌트가 아니다.
    "rag": InstallProfile("rag", ("core", "rag"), (_DOCTOR,)),
    "report-hub": InstallProfile("report-hub", ("core", "report-hub"), (_DOCTOR, "report-hub")),
    "full": InstallProfile("full", ("core", "report-hub", "rag"), (_DOCTOR, "report-hub")),
}


def resolve_profile(name: str) -> InstallProfile:
    profile = PROFILES.get(name)
    if profile is None:
        known = ", ".join(sorted(PROFILES))
        raise UnknownProfileError(f"unknown install profile: {name}; known: {known}")
    return profile


def healthcheck_env(profile: InstallProfile) -> str:
    return f'HEALTHCHECK_SERVICES="{" ".join(profile.healthcheck_services)}"\n'
