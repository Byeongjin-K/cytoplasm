"""Google observations and verdicts; gate mode never touches old credentials."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, assert_never

from automation.google_gate.mode import read_mode
from automation.google_gate.token_client import GateError, access_token
from automation.install.checks import Status

GWS_FILES: Final = ("client_secret.json", "credentials.enc", ".encryption_key")
_G: Final = "https://www.googleapis.com/auth/"
GWS_SERVICES: Final = (
    ("캘린더", (f"{_G}calendar",)),
    ("메일 발송", (f"{_G}gmail.send", "https://mail.google.com/")),
    ("메일 읽기", (f"{_G}gmail.readonly", f"{_G}gmail.modify", "https://mail.google.com/")),
    ("Drive", (f"{_G}drive",)),
    ("과제비 시트", (f"{_G}spreadsheets",)),
    ("할 일", (f"{_G}tasks",)),
    ("회의 주소(Meet)", (f"{_G}meetings.space.created",)),
)


@dataclass(frozen=True, slots=True)
class GwsStatus:
    token_valid: bool
    scopes: tuple[str, ...]


class GoogleFacts(Protocol):
    @property
    def google_gate(self) -> bool: ...
    @property
    def gws_installed(self) -> bool: ...
    @property
    def gws_files(self) -> frozenset[str]: ...
    @property
    def gws(self) -> GwsStatus | str | None: ...


def legacy_status(
    binary: str, run: Callable[[tuple[str, ...], Mapping[str, str]], tuple[int, str]],
) -> GwsStatus | str:
    code, out = run((binary, "auth", "status"), {})
    if code != 0:
        return f"gws auth status rc={code}"
    try:
        raw = json.loads(out)
    except ValueError:
        return "gws auth status 응답을 해석하지 못했다"
    if not isinstance(raw, dict):
        return "gws auth status 응답을 해석하지 못했다"
    scopes = raw.get("scopes")
    return GwsStatus(
        token_valid=raw.get("token_valid") is True,
        scopes=tuple(str(scope) for scope in scopes) if isinstance(scopes, list) else (),
    )


def gate_status(home: Path, online: bool) -> GwsStatus | str | None:
    try:
        mode = read_mode(home)
        if not online:
            return None
        token = access_token(mode.port)
        return GwsStatus(True, token.scopes)
    except GateError as error:
        return error.code


def judge(f: GoogleFacts) -> tuple[Status, str, tuple[str, ...]]:
    if f.google_gate:
        match f.gws:
            case GwsStatus(token_valid=True, scopes=scopes):
                return Status.PASS, f"노드 Google 게이트 토큰 유효, 권한 {len(scopes)}개", ()
            case None:
                return Status.PASS, "노드 Google 게이트 모드(토큰 검사 생략)", ()
            case str() | GwsStatus():
                return Status.FAIL, "노드 Google 게이트 토큰 발급 실패 — 노드에서 gatectl status로 게이트 상태를 확인한다", ()
            case unreachable:
                assert_never(unreachable)
    if not f.gws_files:
        return Status.WARN, "미연결 — 메일 발송·캘린더·Drive·과제비·할 일 기능이 동작하지 않는다", ()
    if not f.gws_installed:
        return Status.FAIL, "자격증명은 있으나 gws 실행 파일이 없다", ()
    absent = tuple(name for name in GWS_FILES if name not in f.gws_files)
    if absent:
        return Status.FAIL, f"자격증명 파일 없음: {', '.join(absent)} — Google 승인을 다시 옮긴다", absent
    match f.gws:
        case None:
            return Status.PASS, "자격증명 파일 있음(토큰 검사 생략)", ()
        case str() as reason:
            return Status.FAIL, f"{reason} — 토큰을 확인하지 못했다", ()
        case GwsStatus(token_valid=False):
            return Status.FAIL, "토큰이 유효하지 않다(만료·폐기) — Google 승인을 다시 한다", ()
        case GwsStatus(scopes=scopes):
            missing = tuple(name for name, accepted in GWS_SERVICES if not set(accepted) & set(scopes))
            if missing:
                return Status.WARN, f"권한 없음: {', '.join(missing)} — 그 기능만 실패한다", missing
            return Status.PASS, f"토큰 유효, {len(GWS_SERVICES)}개 서비스 권한", ()
        case unreachable:
            assert_never(unreachable)
