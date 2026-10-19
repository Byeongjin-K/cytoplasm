"""Standalone stdlib token boundary, also copied beside the real gws binary."""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GateError(Exception):
    code: str

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class AccessToken:
    value: str = field(repr=False)
    expires_in: int
    scopes: tuple[str, ...]


def port_number(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise GateError("port-invalid")
    return value


def access_token(port: int) -> AccessToken:
    """Never echo an HTTP body, error text, or token at this boundary."""
    port_number(port)
    try:
        with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=10)) as connection:
            connection.request("GET", "/_gate/access-token?grant=jobs")
            with connection.getresponse() as response:
                if response.status != 200:
                    raise GateError(f"http-{response.status}")
                body = response.read(65537)
        if len(body) > 65536:
            raise GateError("response-too-large")
        raw = json.loads(body)
    except (OSError, http.client.HTTPException):
        raise GateError("unreachable") from None
    except (ValueError, UnicodeError):
        raise GateError("response-invalid") from None
    # JSON is untrusted here. Only this parsed value reaches callers.
    match raw:
        case {"access_token": str() as token, "expires_in": int() as expires,
              "scope": str() as scope} if (
                  type(expires) is int and expires > 0
                  and re.fullmatch(r"[!-~]+", token) and scope
              ):
            return AccessToken(token, expires, tuple(scope.split()))
        case _:
            raise GateError("response-invalid")


def execute(port: int, binary: str, argv: Sequence[str]) -> int:
    """Exec only after a successful grant mint; no credential fallback."""
    try:
        token = access_token(port)
        os.execve(binary, [binary, *argv],
                  {**os.environ, "GOOGLE_WORKSPACE_CLI_TOKEN": token.value})
    except GateError as error:
        print(f"GOOGLE-GATE-TOKEN-FAIL {error.code}", file=sys.stderr)
    except (OSError, ValueError):
        print("GOOGLE-GATE-TOKEN-FAIL exec-failed", file=sys.stderr)
    return 2
