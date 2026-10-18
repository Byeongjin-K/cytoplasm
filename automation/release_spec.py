"""Release approval binding: version, HEAD and complete surface digest set.
Stored digests validate posted cards without rendering; pre-digest v1-v3 use frozen replay.
Record/detail replay stays staged here; producer-only preflight lives in release_card.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .release_spec_message import (
    DETAIL_BACKLINK_BUDGET,
    render_v1,
    render_v2,
    render_v3,
    render_v5,
    render_v6,
    split_messages,
)
from automation.notice_bot import SURFACE as NOTICE_BOT_SURFACE
from automation.omo_relay_policy import SURFACE as OMO_RELAY_SURFACE
from automation.interop.approval_surface import ApprovalBinding
from automation.skill_gate_specs import (
    _APPROVAL_LINE,
    StoredBinding,
    _hash,
    binding_fields,
)

RELEASE_ACTION: Final = "release.deploy"
#: 신규 카드가 쓰는 판본. 1~5 는 이미 게시된 카드의 재생 전용이며 문구가 동결이다.
NEW_RENDER_VERSION: Final = 6
#: 헬스체크 강제명령 래퍼(허용 목록) 갱신을 릴리스 ✅ 에 묶는 표면 이름의 머리. 값은 새 릴리스가
#: 그 노드에 기대하는 `# wrapper-inputs:` 지문이고, 승인 해시에 다른 표면과 똑같이 들어간다.
#: 이 표면이 없는 레코드(지금까지의 모든 카드)는 해시도 카드 바이트도 그대로다.
WRAPPER_SURFACE_PREFIX: Final = "healthcheck-wrapper:"
DORI_SURFACE_PREFIX: Final = "dori-agent-chat:"
GATEWAY_STOP_SURFACE: Final = "hermes-gateways:stopped"
YOODORI_CHANNEL_PREFIX: Final = "yoodori-channel:"
RAG_MCP_SURFACE: Final = "rag-mcp"


def wrapper_surface(node: str) -> str:
    return f"{WRAPPER_SURFACE_PREFIX}{node}"


def dori_surface(bot_id: str) -> tuple[str, str]:
    """Bind the takeover bot to the same release surface authorization as wrappers."""
    if re.fullmatch(r"[0-9]+", bot_id) is None:
        raise ReleaseSpecError("DORI-SWITCH-REFUSED: expected a decimal bot id only")
    return f"{DORI_SURFACE_PREFIX}{bot_id}", hashlib.sha256(bot_id.encode()).hexdigest()


def gateway_stop_surface() -> tuple[str, str]:
    """The reversible stop is an additional release effect, like the Dori cutover."""
    return GATEWAY_STOP_SURFACE, hashlib.sha256(b"stopped").hexdigest()
def yoodori_channel_surface(channel_id: str) -> tuple[str, str]:
    """Bind the destination to this release's existing owner authorization."""
    if re.fullmatch(r"[1-9][0-9]{16,19}", channel_id) is None:
        raise ReleaseSpecError("YOODORI-CHANNEL-REFUSED: expected a decimal channel id only")
    return f"{YOODORI_CHANNEL_PREFIX}{channel_id}", hashlib.sha256(channel_id.encode()).hexdigest()

_RELEASE_VERSION: Final = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
_COMMIT_SHA: Final = re.compile(r"[0-9a-f]{40}\Z")
_NONCE: Final = re.compile(r"[0-9a-f]{32}\Z")
_SURFACE_NAME: Final = re.compile(r"[a-z0-9][a-z0-9:._/-]{0,99}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_SNOWFLAKE: Final = re.compile(r"[1-9][0-9]{0,19}\Z")


class ReleaseSpecError(ValueError):
    """A release approval cannot be rendered or bound safely."""


class EnvelopeUnavailable(ReleaseSpecError):
    """봉투를 부를 수 없다 — 신규 카드만 직전 판본으로 내려가고, 저장된 판본은 fail-closed."""


def spec_from_record(record: Mapping[str, str]) -> ReleaseSpec:
    """Replay a stored pending record into the exact spec the owner decided on.

    Single copy on purpose: the producer's decision polling AND the staged gate's
    ``release-authorize`` both replay records, and two replays drift apart exactly
    where a drifted replay authorizes the wrong bytes.
    """
    raw = json.loads(record.get("surface_digests", "[]"))
    if not isinstance(raw, list):
        raise ReleaseSpecError("stored surface digests are malformed")
    try:
        render_version = int(record.get("render_version", "1"))
    except ValueError as error:
        raise ReleaseSpecError("stored render version is malformed") from error
    detail_ids: tuple[str, ...] = ()
    if render_version >= 6:
        try:
            raw_detail_ids = json.loads(record.get("detail_message_ids", "[]"))
        except json.JSONDecodeError as error:
            raise ReleaseSpecError("stored detail message ids are malformed") from error
        if not isinstance(raw_detail_ids, list) or any(
            not isinstance(value, str) for value in raw_detail_ids
        ):
            raise ReleaseSpecError("stored detail message ids are malformed")
        detail_ids = tuple(raw_detail_ids)
    return ReleaseSpec(
        version=record.get("version", ""),
        head_sha=record.get("head_sha", ""),
        release_nonce=record.get("release_nonce", ""),
        surface_digests=tuple((str(row[0]), str(row[1])) for row in raw),
        patch_notes=record.get("patch_notes", ""),
        render_version=render_version,
        major_note=record.get("major_note", ""),
        detail_message_ids=detail_ids,
        detail_channel_id=record.get("detail_channel_id", ""),
        detail_guild_id=record.get("detail_guild_id", ""),
    )


@dataclass(frozen=True, slots=True)
class ReleaseSpec:
    """release: one owner decision over version + HEAD + the complete surface digest set."""

    version: str
    head_sha: str
    release_nonce: str
    surface_digests: tuple[tuple[str, str], ...]
    patch_notes: str
    render_version: int = NEW_RENDER_VERSION
    major_note: str = ""
    posted_text: str = ""
    detail_message_ids: tuple[str, ...] = ()
    detail_channel_id: str = ""
    detail_guild_id: str = ""

    def __post_init__(self) -> None:
        if _RELEASE_VERSION.fullmatch(self.version) is None:
            raise ReleaseSpecError(f"invalid release version: {self.version!r}")
        if _COMMIT_SHA.fullmatch(self.head_sha) is None:
            raise ReleaseSpecError("release HEAD must be a 40-character lowercase sha")
        if _NONCE.fullmatch(self.release_nonce) is None:
            raise ReleaseSpecError("release nonce must be 32 lowercase hex characters")
        try:
            rows = tuple(sorted(self.surface_digests))
        except (TypeError, ValueError) as error:
            raise ReleaseSpecError("surface digests are malformed") from error
        if len({name for name, _digest in rows}) != len(rows):
            raise ReleaseSpecError("surface names must be unique")
        if any(
            _SURFACE_NAME.fullmatch(name) is None or _SHA256.fullmatch(digest) is None
            for name, digest in rows
        ):
            raise ReleaseSpecError("surface names and sha256 digests must be canonical")
        if not isinstance(self.patch_notes, str) or not self.patch_notes.strip():
            raise ReleaseSpecError("release patch notes must not be empty")
        if self.render_version not in (1, 2, 3, 4, 5, 6):
            raise ReleaseSpecError("release render version must be 1, 2, 3, 4, 5 or 6")
        if "\n" in self.major_note:
            raise ReleaseSpecError("the operator note must stay on one card line")
        coordinates = (
            *self.detail_message_ids,
            self.detail_channel_id,
            self.detail_guild_id,
        )
        if any(value and _SNOWFLAKE.fullmatch(value) is None for value in coordinates):
            raise ReleaseSpecError("release detail coordinates must be Discord snowflakes")
        if self.detail_message_ids and not self.detail_channel_id:
            raise ReleaseSpecError("release detail message ids require a channel id")
        object.__setattr__(self, "surface_digests", rows)

    def key(self) -> str:
        return "release"

    def record_name(self) -> str:
        return "release"

    def action_hash(self) -> str:
        return _hash(
            "release",
            self.version,
            self.head_sha,
            *(f"{name}={digest}" for name, digest in self.surface_digests),
        )

    def stored(self, record: Mapping[str, str]) -> StoredBinding | None:
        action_hash = record.get("action_hash", "")
        message_id = record.get("message_id", "")
        nonce = record.get("release_nonce", "")
        if not action_hash or not message_id or not nonce:
            return None
        return StoredBinding(action_hash, message_id, nonce)

    def detail_messages(self) -> tuple[str, ...]:
        """카드가 가리키는 변경 상세 메시지 전부 — 레코드의 원문에서 그대로 재생된다."""
        body = self.patch_notes
        new_card = self.render_version >= 6
        if new_card:
            bundles = "\n".join(
                f"- `{name}`" for name, _digest in self.surface_digests
            ) or "- 변경 없음"
            body = f"### 배포 묶음 전체\n{bundles}\n{body}"
        return split_messages(
            version=self.version,
            head_sha=self.head_sha,
            body=body,
            suffix_budget=DETAIL_BACKLINK_BUDGET if new_card else 0,
            preserve_urls=new_card,
        )

    def render(self) -> str:
        """게시할 카드 본문. 버전형이며 과거 판본은 저장된 레코드를 위해 동결이다."""
        if self.posted_text:
            return self.posted_text
        renderers = {
            1: lambda: render_v1(self, approval_line=_APPROVAL_LINE),
            2: lambda: render_v2(
                self,
                bundle_names=self._bundle_names(),
                approval_line=_APPROVAL_LINE,
            ),
            3: lambda: render_v3(
                self,
                bundle_names=self._bundle_names(),
                approval_line=_APPROVAL_LINE,
            ),
            4: self._render_v4,
            5: lambda: render_v5(
                self, bundle_names=self._bundle_names()
            ),
            6: lambda: render_v6(
                self, bundle_summary=self._bundle_summary()
            ),
        }
        content = renderers[self.render_version]()
        if len(content) > 1900:
            raise ReleaseSpecError(
                f"release approval message exceeds 1900 characters: {len(content)}"
            )
        return content

    def _bundle_names(self) -> str:
        return ", ".join(f"`{name}`" for name, _digest in self.surface_digests) or "변경 없음"

    def _bundle_summary(self) -> str:
        skills = sum(name.startswith("skill:") for name, _digest in self.surface_digests)
        homes = sum(name.startswith("home:") for name, _digest in self.surface_digests)
        summary: list[str] = []
        if skills:
            summary.append(f"스킬 {skills}")
        if homes:
            summary.append(f"홈 패키지 {homes}")
        summary.extend(
            name
            for name, _digest in self.surface_digests
            if not name.startswith(("skill:", "home:", WRAPPER_SURFACE_PREFIX, DORI_SURFACE_PREFIX,
                                    YOODORI_CHANNEL_PREFIX))
            and name not in (RAG_MCP_SURFACE, GATEWAY_STOP_SURFACE, NOTICE_BOT_SURFACE, OMO_RELAY_SURFACE)
            and not name.startswith("auto-write:")
        )
        return " · ".join(summary) or "변경 없음"

    def _render_v4(self) -> str:
        """v4/v5 봉투. 상세 분할 정책만 판본으로 고정하며 시계·계획은 읽지 않는다."""
        from automation.release_spec_message import render_v4

        return render_v4(self, bundle_names=self._bundle_names())

    def new_record(self, message_id: str, binding: ApprovalBinding) -> dict[str, str]:
        detail_fields = (
            {
                "detail_message_ids": json.dumps(
                    self.detail_message_ids, separators=(",", ":")
                ),
                "detail_channel_id": self.detail_channel_id,
                "detail_guild_id": self.detail_guild_id,
            }
            if self.detail_message_ids
            else {}
        )
        return {
            "version": self.version,
            "head_sha": self.head_sha,
            "release_nonce": self.release_nonce,
            "surface_digests": json.dumps(
                self.surface_digests, separators=(",", ":"), ensure_ascii=True
            ),
            "patch_notes": self.patch_notes,
            "render_version": str(self.render_version),
            "major_note": self.major_note,
            "message_id": message_id,
            "action_hash": self.action_hash(),
            "content_sha256": _hash(self.render()),
            "approval_action": RELEASE_ACTION,
            "approval_destination": f"release:{self.version}",
            **detail_fields,
            **binding_fields(binding),
        }

    def serialize(self, record: Mapping[str, str]) -> str:
        return json.dumps(dict(record), sort_keys=True)

    def bound(self, content: str, record: Mapping[str, str]) -> bool:
        try:
            replay = spec_from_record(record)
        except (json.JSONDecodeError, ReleaseSpecError, TypeError):
            return False
        return (
            record.get("action_hash", "") == replay.action_hash() == self.action_hash()
            and (_hash(content) == record["content_sha256"] if "content_sha256" in record
                 else replay.render_version < 4 and content == replay.render())
        )
