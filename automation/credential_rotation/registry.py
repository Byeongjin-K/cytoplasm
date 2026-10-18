"""Immutable rotation target registry and target-specific pure operations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Mapping, Protocol

from automation.node_config import load_node_config


@dataclass(frozen=True, slots=True)
class RotationError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ProviderContext:
    interpreter: Path
    working_directory: Path


@dataclass(frozen=True, slots=True)
class HttpProbe:
    url: str
    method: str
    headers: tuple[tuple[str, str], ...]
    body: bytes | None


class ProviderHashRunner(Protocol):
    def __call__(self, provider: ProviderContext, password: str) -> str:
        ...


class PasswordHasher(Protocol):
    def __call__(
        self,
        password: str,
        provider: ProviderContext | None,
        runner: ProviderHashRunner,
    ) -> str:
        ...


class ProbeBuilder(Protocol):
    def __call__(self, url: str, username: str, password: str) -> HttpProbe:
        ...


@dataclass(frozen=True, slots=True)
class RotationTarget:
    account: str
    verifier_path: Path
    note_path: Path
    username_key: str
    password_hash_key: str
    session_secret_key: str | None
    password_hasher: PasswordHasher
    provider: ProviderContext | None
    unit_name: str
    probe_url: str
    probe_builder: ProbeBuilder
    probe_success_status: int


def hash_with_dashboard_provider(
    password: str,
    provider: ProviderContext | None,
    runner: ProviderHashRunner,
) -> str:
    """Delegate Kanban hashing to its running provider to prevent encoding drift."""
    if provider is None:
        raise RotationError("dashboard provider configuration is missing")
    return runner(provider, password)


def hash_with_sha256(
    password: str,
    _provider: ProviderContext | None,
    _runner: ProviderHashRunner,
) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def make_password_login_probe(url: str, username: str, password: str) -> HttpProbe:
    body = json.dumps(
        {"provider": "basic", "username": username, "password": password, "next": "/"},
        separators=(",", ":"),
    ).encode("utf-8")
    return HttpProbe(url, "POST", (("Content-Type", "application/json"),), body)


def make_basic_auth_probe(url: str, username: str, password: str) -> HttpProbe:
    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return HttpProbe(url, "GET", (("Authorization", f"Basic {encoded}"),), None)



# The dashboards bind to this installation's tailnet interface, not to loopback
# (docs/guide/report-hub.md), so the rotation probe needs a reachable host name. Until
# 2026-09-07 that was one installation's literal address, sitting in a file the public
# export publishes. The host is installation data: it comes from the node configuration,
# and an operator whose tailnet name does not resolve locally overrides it by env.
def _dashboard_host() -> str:
    override = os.environ.get("CREDENTIAL_ROTATION_DASHBOARD_HOST", "").strip()
    if override:
        return override
    return load_node_config().primary_node_name


_DASHBOARD_HOST: Final = _dashboard_host()

ROTATION_TARGETS: Final[Mapping[str, RotationTarget]] = MappingProxyType(
    {
        "report-hub": RotationTarget(
            account="ops",
            verifier_path=Path("/home/ops/report-hub/hub.env"),
            note_path=Path("/home/ops/report-hub/dashboard-cha-credentials.txt"),
            username_key="REPORT_HUB_DASHBOARD_USER",
            password_hash_key="REPORT_HUB_DASHBOARD_PASSWORD_SHA256",
            session_secret_key=None,
            password_hasher=hash_with_sha256,
            provider=None,
            unit_name="report-hub-dashboard.service",
            probe_url=f"http://{_DASHBOARD_HOST}:8800/",
            probe_builder=make_basic_auth_probe,
            probe_success_status=200,
        ),
    }
)
