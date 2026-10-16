"""신뢰 키로 검증되지 않는 더 새 릴리스를 리컨실러가 조용히 넘기지 않는다.

2026-10-16: 업데이트 신뢰키가 회전한 뒤 제3자 노드가 며칠 동안 v1.22.2 에 머물렀다.
검증기는 새 키로 서명된 태그를 전부 건너뛰고 옛 키의 마지막 태그를 rc 0 으로 돌려줬고,
리컨실러도 헬스체크도 아무 신호를 내지 않았다. 이 모듈은 그 상태를 매 틱 저널 한 줄로
남기고, 같은 (태그, 서명 키) 에 대해 소유자 통지는 한 번만 보낸다 — 2분 틱마다 같은 경고를
보내면 통지 채널이 소음이 된다. 상태가 풀리면 기록을 지워 다음 회전 때 다시 알린다.
"""
from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

from automation.update_trust_stale import StaleTrustKey, stale_key_warning

TRUST_NOTICE_PATH: Final = Path("/srv/autophagy-private/deploy-reconcile/trust-key-notice.txt")

Notify = Callable[[StaleTrustKey, str], bool]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".trust-key-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            _ = stream.write(key + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def notify_stale_key(stale: StaleTrustKey, warning: str) -> bool:
    """소유자 통지 파사드로 한 건. 봉투를 만들 수 없으면 같은 문구를 그대로 보낸다."""
    from automation import owner_notice
    from automation.owner_notice import notify_owner

    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return notify_owner(warning)
    if not getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
        return notify_owner(warning)
    message = OwnerMessage(
        subject_key=stale.tag,
        subject="업데이트 신뢰 키 갱신 필요",
        fact=(
            f"더 새 릴리스 {stale.tag} 가 이 서버의 신뢰 키로 검증되지 않아 {stale.installed_tag} 에 머묾; "
            f"새 태그 서명 키 지문 {stale.signer_fingerprint or '알 수 없음'}"
        ),
        location=Ref(scope="resource", search=("릴리스", stale.tag)),
        owner=Action(
            "open",
            Ref(scope="resource", search=("설치 안내", "업데이트 신뢰키가 바뀌었을 때")),
            "공개 릴리스 노트의 지문과 같을 때만 신뢰 키 추가",
        ),
        agent_next="검증된 릴리스까지만 계속 수렴; 키가 추가되면 다음 틱에 전진",
        recovery="not_applicable",
        detail=Result("executed"),
    )
    return notify_owner(warning, message=message)


def observe_stale_trust_key(
    stale: StaleTrustKey | None,
    *,
    path: Path = TRUST_NOTICE_PATH,
    notify: Notify = notify_stale_key,
) -> None:
    """매 틱 저널에 한 줄, 같은 (태그, 키) 의 소유자 통지는 배달될 때까지 한 번. 예외를 던지지 않는다."""
    warning = stale_key_warning(stale)
    try:
        if stale is None:
            path.unlink(missing_ok=True)
            return
        print(f"[deploy-reconcile] {warning}", file=sys.stderr)
        key = f"{stale.tag} {stale.signer_fingerprint}"
        if _read(path) == key:
            return
        if notify(stale, warning):
            _write(path, key)
    except OSError as error:
        # 기록 실패가 수렴 틱을 막으면 안 된다 — 다음 틱이 다시 알린다.
        print(f"[deploy-reconcile] TRUST-KEY-NOTICE-STATE-FAIL {type(error).__name__}", file=sys.stderr)
