"""Parse the MCP refresh sentinel into a receipt and an explicit pending action."""
from __future__ import annotations

import re
from typing import Final, TypedDict

from automation.deploy_all_kinds import ObservationError

_RESULT: Final = re.compile(
    r"(current|installed|skipped|failed)\|(-|sha256:[0-9a-f]{64})"
    r"\|(-|sha256:[0-9a-f]{64})\|([a-z-]{1,64})\Z"
)


class RefreshResult(TypedDict):
    status: str
    image_before: str
    image_after: str
    detail: str


def parse_refresh(entry: str) -> RefreshResult:
    """Reject malformed remote results rather than silently dropping a failure."""
    matched = _RESULT.fullmatch(entry)
    if matched is None:
        raise ObservationError("malformed RAG MCP refresh result")
    return RefreshResult(status=matched[1], image_before=matched[2],
                         image_after=matched[3], detail=matched[4])
