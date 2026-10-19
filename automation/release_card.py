"""Producer-only release card preflight; stored-record replay does not import this."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Final, TypeAlias

from automation.release_spec import (
    NEW_RENDER_VERSION,
    RAG_MCP_SURFACE,
    EnvelopeUnavailable,
    ReleaseSpec,
    ReleaseSpecError,
)

#: 봉투를 부를 수 없을 때 신규 카드가 차례로 내려가는 동결 판본들.
PREVIOUS_RENDER_VERSION: Final = 5
LEGACY_ENVELOPE_VERSION: Final = 4
LEGACY_PLAIN_VERSION: Final = 3
#: 게시 전 거절이 stderr 에 내는 머리말 — release.sh 는 이 줄을 그대로 되울린다.
CARD_REFUSED_PREFIX: Final = "RELEASE-CARD-REFUSED:"
PlanValue: TypeAlias = str | list[list[str]]


def spec_from_plan(payload: Mapping[str, PlanValue], release_nonce: str) -> ReleaseSpec:
    """Parse the release.sh plan JSON into the producer's immutable spec."""
    surfaces = payload.get("surface_digests")
    if not isinstance(surfaces, list):
        raise ReleaseSpecError("plan payload carries no surface digest list")
    return ReleaseSpec(
        version=str(payload.get("version", "")),
        head_sha=str(payload.get("head", "")),
        release_nonce=release_nonce,
        surface_digests=tuple((str(row[0]), str(row[1])) for row in surfaces),
        patch_notes=str(payload.get("patch_notes", "")),
        major_note=str(payload.get("major_note", "")),
        card_summary=str(payload.get("card_summary", "")),
        previous_surfaces=(None if not isinstance(payload.get("previous_surfaces"), list)
                           else tuple((str(row[0]), str(row[1])) for row in payload["previous_surfaces"])),
    )


def preflight_new_request(spec: ReleaseSpec) -> tuple[ReleaseSpec | None, str]:
    """Select one render version and prove its worst-case linked card fits."""
    # 판본은 여기서 한 번만 정해진다: 레코드가 적은 판본과 실제로 게시된 바이트가 어긋나면
    # 소유자의 ✅ 가 묶인 카드를 두 번 다시 만들 수 없다. 봉투를 부를 수 없으면 직전 판본으로
    # 내려가 그 판본을 기록하고, 한도를 넘으면 카드도 레코드도 저널도 남기지 않고 거절한다.
    for version in (
        NEW_RENDER_VERSION,
        PREVIOUS_RENDER_VERSION,
        LEGACY_ENVELOPE_VERSION,
        LEGACY_PLAIN_VERSION,
    ):
        if version != NEW_RENDER_VERSION and any(
            name in (RAG_MCP_SURFACE, "peer-retired:retired", "ledger:on")
            or name.startswith("cron-timer:") for name, _digest in spec.surface_digests
        ):
            break  # Never approve an effect on a fallback card that omits its notice.
        candidate = replace(spec, render_version=version)
        probe = candidate
        if version == 6 and not candidate.detail_message_ids:
            probe = replace(
                candidate,
                detail_message_ids=("9" * 20,),
                detail_channel_id="9" * 20,
                detail_guild_id="9" * 20,
            )
        try:
            _ = probe.render()
        except EnvelopeUnavailable:
            continue
        except ReleaseSpecError as error:
            return None, f"{CARD_REFUSED_PREFIX} {error}"
        return candidate, ""
    return None, f"{CARD_REFUSED_PREFIX} release owner envelope is unavailable"


def finalize_card(spec: ReleaseSpec) -> tuple[ReleaseSpec | None, str]:
    """Pin the exact card bytes after the producer has stored its detail coordinates."""
    try:
        content = spec.render()
    except (EnvelopeUnavailable, ReleaseSpecError) as error:
        return None, f"{CARD_REFUSED_PREFIX} {error}"
    return replace(spec, posted_text=content), ""


def card_for_new_request(spec: ReleaseSpec) -> tuple[ReleaseSpec | None, str]:
    """신규 카드를 게시 전에 완성한다 — 판본 확정도 1900자 예산도 첫 효과보다 먼저다."""
    candidate, refusal = preflight_new_request(spec)
    if candidate is None:
        return None, refusal
    return finalize_card(candidate)
