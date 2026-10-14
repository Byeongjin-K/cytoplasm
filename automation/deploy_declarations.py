"""배포 선언의 타입 있는 뷰 — 기존 네 필드 행(legacy)과 `v2:<kind>;key=value;…` 행.

선언 파일(`<package>/deploy-manifest.txt`)과 네 필드 분해는 `watcher_manifest` 의 것을 그대로
쓴다. 네 번째 필드가 `required`·`optional:<사유>` 면 legacy 행이고 의미는 예전과 같다(종류는
file, 소유 패키지는 소스 앞 두 조각). `v2:` 로 시작하면 종류(file·tree·derived·cron·config)와 정책
(required·optional·held·retired)과 속성을 싣고, 소유 패키지는 **선언 파일이 놓인 디렉터리**다 —
`configs/…` 소스에 앞 두 조각 규칙을 쓰면 엉뚱한 배포기를 가리킨다.

cron 행의 선택 키 `env=<이름>[,<이름>…]` 은 그 스크립트가 기본값 없이 요구하는 환경값이다. 배포기는
이 키를 쓰지 않고, doctor 가 등록된 작업마다 값이 있는지 확인한다(`automation/doctor/cron_env.py`).

v2 행은 중앙 표(`configs/watcher-deploy-manifest.txt`)에 나오지 않는다. 그 표의 바이트는
헬스체크 허용 목록 지문에 들어가므로, 표는 legacy 행만 투영한다(`watcher_manifest.derive_manifest`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, TypeAlias

from automation.watcher_manifest import (
    V2_PREFIX as _V2_PREFIX,
)
from automation.watcher_manifest import (
    ManifestError,
    Row,
    declaration_files,
    parse_rows,
    read_declaration_text,
)

KINDS: Final = ("file", "tree", "derived", "cron", "config")
POLICIES: Final = ("required", "optional", "held", "retired")

Attrs: TypeAlias = tuple[tuple[str, str], ...]
_COMMON_KEYS: Final = frozenset({"policy", "reason", "activation", "requires", "node"})
_KIND_KEYS: Final[dict[str, frozenset[str]]] = {
    "file": frozenset(),
    "tree": frozenset({"files", "profile", "prefix", "lock"}),
    "derived": frozenset({"algorithm", "requirements"}),
    "cron": frozenset({"schedule", "script", "deliver", "mode", "env"}),
    "config": frozenset({"algorithm"}),
}
_ENV_NAME: Final = re.compile(r"[A-Z_][A-Z0-9_]*")
_REQUIRED_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "cron": ("schedule", "script", "deliver", "mode"),
    "config": ("algorithm",),
}
_ENUMS: Final[dict[str, tuple[str, ...]]] = {
    "policy": POLICIES,
    "profile": ("python", "tree"),
    "activation": ("none", "gateway"),
    "requires": ("roster",),
    "node": ("default", "rag"),
}
_DEFAULTS: Final[dict[str, str]] = {"profile": "python", "activation": "none", "node": "default"}
_PATH_KEYS: Final = frozenset({"prefix", "lock", "requirements", "script"})


@dataclass(frozen=True, slots=True)
class Declaration:
    account: str
    source: str
    destination: str
    kind: str
    policy: str
    reason: str
    attrs: Attrs
    owner: str
    legacy: bool

    def attr(self, name: str, default: str = "") -> str:
        """명시한 값 → 문법의 기본값(profile·activation·node) → `default` 순."""
        for key, value in self.attrs:
            if key == name:
                return value
        return _DEFAULTS.get(name, default)


def _relative_path(value: str, what: str) -> str:
    parts = value.split("/")
    if value.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ManifestError(f"{what} must be a plain relative path: {value[:80]}")
    return value


def _check_value(key: str, value: str) -> None:
    if not value:
        raise ManifestError(f"empty value for key: {key}")
    if any(char in value for char in "|\n\r"):
        raise ManifestError(f"forbidden character in value of key: {key}")
    allowed = _ENUMS.get(key)
    if allowed is not None and value not in allowed:
        raise ManifestError(f"unsupported {key}: {value[:40]}")
    if key in _PATH_KEYS:
        _ = _relative_path(value, key)
    if key == "files":
        for name in value.split(","):
            _ = _relative_path(name, "files entry")
    if key == "env" and not all(_ENV_NAME.fullmatch(name) for name in value.split(",")):
        raise ManifestError(f"env must list variable names: {value[:80]}")


def _parse_v2(field: str) -> tuple[str, dict[str, str]]:
    kind, *pieces = field[len(_V2_PREFIX) :].split(";")
    if kind not in KINDS:
        raise ManifestError(f"unsupported declaration kind: {kind[:40]}")
    allowed = _COMMON_KEYS | _KIND_KEYS[kind]
    values: dict[str, str] = {}
    for piece in pieces:
        key, sep, value = piece.partition("=")
        if not sep or not key:
            raise ManifestError(f"malformed attribute: {piece[:40]}")
        if key not in allowed:
            raise ManifestError(f"unsupported key for {kind}: {key[:40]}")
        if key in values:
            raise ManifestError(f"duplicate key: {key}")
        _check_value(key, value)
        values[key] = value
    missing = [key for key in _REQUIRED_KEYS.get(kind, ()) if key not in values]
    if missing:
        raise ManifestError(f"{kind} declaration lacks: {', '.join(missing)}")
    return kind, values


def _typed(row: Row, owner_dir: str) -> Declaration:
    _ = _relative_path(row.source, "source")
    _ = _relative_path(row.destination, "destination")
    if not row.policy.startswith(_V2_PREFIX):
        policy, sep, reason = row.policy.partition(":")
        if (policy, sep) not in (("required", ""), ("optional", ":")) or (sep and not reason):
            raise ManifestError(f"unsupported legacy policy: {row.policy[:40]}")
        return Declaration(
            row.account, row.source, row.destination, "file", policy, reason, (),
            row.owning_package, True,
        )
    kind, values = _parse_v2(row.policy)
    policy = values.pop("policy", "required")
    reason = values.pop("reason", "")
    if policy != "required" and not reason:
        raise ManifestError(f"policy {policy} requires a reason: {row.destination[:80]}")
    return Declaration(
        row.account, row.source, row.destination, kind, policy, reason,
        tuple(values.items()), owner_dir, False,
    )


def parse_declaration_file(relative: str, text: str) -> tuple[Declaration, ...]:
    """선언 파일 하나(`relative` 는 저장소 상대 경로)의 모든 행. 빈 선언은 오류다."""
    rows = parse_rows(text)
    if not rows:
        raise ManifestError(f"declaration has no rows: {relative}")
    owner_dir = PurePosixPath(relative).parent.as_posix()
    return tuple(_typed(row, owner_dir) for row in rows)


def all_declarations(repo: Path) -> tuple[Declaration, ...]:
    """저장소의 모든 선언(legacy + v2) — `declaration_files` 의 결정적 순서."""
    return tuple(
        declaration
        for path in declaration_files(repo)
        for declaration in parse_declaration_file(
            path.relative_to(repo).as_posix(), read_declaration_text(path)
        )
    )
