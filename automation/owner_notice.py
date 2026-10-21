"""소유자에게 사건 통지 한 건을 전달한다 — 실패해도 절대 예외를 던지지 않는다.

2026-08-02 실측이 이 모듈의 이유다. 재조정 타이머는 15시간·약 450회 돌면서 한 번도
수렴하지 못했고, 같은 날 healthcheck 는 배포 체크아웃 드리프트를 **52번 FAIL 로 정확히
탐지하고도** 소유자에게 닿지 못했다. 탐지는 여러 겹으로 있는데 도달이 없었다.

두 소비자(재조정 tick · healthcheck 스윕)가 같은 경로를 쓰도록 여기 한 벌만 둔다.
사본이 둘이면 드리프트하고, 깨지는 쪽은 언제나 둘째 사본이다.

자격증명은 새로 만들지 않는다. `/etc/autophagy/repair-approval.env` 가 이미 존재하고
(`root:ops 0640`) 수리 워처가 같은 파일을 쓰며, 두 유닛 모두 `User=ops` 라 읽을 수 있다.
따라서 새 시크릿·새 파일·새 토큰·새 sudoers 가 없다.

**절대 예외를 던지지 않는다**는 것이 가장 중요한 계약이다. 호출자는 False 를 받아야
통지를 큐잉하고 다음 틱에 재시도한다. 예외가 빠져나가면 그 복구가 통째로 무력화되고
호출자의 본래 일(수렴·스윕)까지 함께 죽는다 — 알림을 붙이려다 프로덕션을 멈추는 셈이다.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.request import Request, urlopen
from automation.notice_bot import fault_channel, notice_channel, resolve_token

if TYPE_CHECKING:
    from collections.abc import Sequence

    from automation.interop.owner_message import OwnerMessage

ACCEPTS_OWNER_MESSAGE: Final = True
_DISCORD_API: Final = "https://discord.com/api/v10"
_USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"


def owner_notice_channel(home: Path | None = None) -> str:
    """ON-1: 검증된 계정 override → env → 이 계정의 interop config.

    확정(2026-08-28 §10-6): 정기 통지는 별도 `#notifications` 채널로 분리한다. 키는
    `owner_notice_channel_id`(`agent_chat_channel_id` 와 같은 해석 위치). **설정되면
    그 채널로만 보낸다 — DM 폴백 없음**(게시 실패는 False 로 호출자 큐잉·재시도).
    미설정이면 지금처럼 DM(레거시 설치 무영향).

    확인 불가는 "" 로 답한다 — ProtectHome 유닛에서 홈을 찌르는 코드는 답해야지
    던지면 안 된다(2026-08-21 repair 워처 5일 정지, 9e1b7ad0).
    """
    from automation.interop.approval_directory import channel_override

    selected = notice_channel(home)
    if selected is not None:
        return selected

    override = channel_override(home)
    if override is not None:
        return override["owner_notice_channel_id"]
    from_env = os.environ.get("OWNER_NOTICE_CHANNEL_ID", "").strip()
    if from_env:
        return from_env
    config = (Path.home() if home is None else home) / ".hermes" / "interop" / "config.json"
    try:
        document = json.loads(config.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - EACCES 포함 어떤 실패도 "미설정"으로 답한다
        return ""
    value = document.get("owner_notice_channel_id", "") if isinstance(document, dict) else ""
    return value.strip() if isinstance(value, str) else ""


def owner_dm_channel(token: str, owner_id: str) -> str:
    """The DM channel this bot opened with the owner — notices only, never a gate."""
    from automation.interop.approval_directory import DiscordChannelDirectory

    selected = notice_channel()
    if selected is not None:
        return selected

    return DiscordChannelDirectory(token=token, owner_id=owner_id).owner_dm()


def _config_owner_id() -> str:
    """owner id — env 가 먼저, 없으면 이 계정의 interop config(확인 불가는 "")."""
    from_env = os.environ.get("AUTOPHAGY_OWNER_ID", "").strip()
    if from_env:
        return from_env
    config = Path.home() / ".hermes" / "interop" / "config.json"
    try:
        document = json.loads(config.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - ProtectHome: 답해야지 던지면 안 된다
        return ""
    value = document.get("owner_id", "") if isinstance(document, dict) else ""
    return value.strip() if isinstance(value, str) else ""


def resolve_notice_target(token: str) -> str:
    """통지가 갈 채널 id — 지정 채널이 있으면 그것뿐, 없으면 소유자 DM.

    ON-2 이관 발신자 중 첨부를 보내는 쪽(procurement)이 채널 id 자체를 필요로 해서
    여기서만 해석한다 — DM 오픈이 파사드 밖으로 새지 않도록(실제 오픈은 central directory).
    "" = 미설정(자격 부족). DM 해석의 네트워크 실패는 호출자가 감싼다.
    """
    channel = owner_notice_channel()
    if channel:
        return channel
    owner_id = _config_owner_id()
    if not owner_id:
        return ""
    return owner_dm_channel(token, owner_id)


def send_notice(token: str, channel_id: str, body: str) -> None:
    """The shared sender — it already chunks and honours Discord's Retry-After."""
    from automation.interop.discord_transport import DiscordTransport

    _ = DiscordTransport(token=token, channel_id=channel_id).send(body)


def _multipart(body: str, files: Sequence[Path]) -> tuple[str, bytes]:
    """Discord 첨부 한 벌의 multipart 본문 — 순수 인코딩, 네트워크 없음."""
    boundary = f"----owner-notice{secrets.token_hex(12)}"
    payload = json.dumps(
        {"content": body,
         "attachments": [{"id": index, "filename": file.name} for index, file in enumerate(files)]},
        ensure_ascii=False,
    ).encode("utf-8")
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
        "Content-Type: application/json\r\n\r\n".encode("utf-8"),
        payload,
    ]
    for index, file in enumerate(files):
        parts.append(
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[{index}]"; '
            f'filename="{file.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8"),
        )
        parts.append(file.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


def _post_multipart(token: str, channel_id: str, body: str, files: Sequence[Path]) -> None:

    boundary, data = _multipart(body, files)
    request = Request(
        f"{_DISCORD_API}/channels/{channel_id}/messages",
        data=data,
        headers={
            "Authorization": f"Bot {resolve_token(token)}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=120) as response:  # noqa: S310
        _ = response.read()


def send_notice_files(token: str, channel_id: str, body: str, files: Sequence[Path]) -> None:
    """첨부가 붙는 통지 — 첫 청크가 파일을 태우고 남은 본문은 공용 전송이 잇는다.

    `DiscordTransport` 는 JSON 본문만 보낸다. 두 번째 전송 계층을 만들지 않기 위해
    multipart 인코딩만 파사드 안에 두고, 청크 경계는 공용 `chunk_message` 를 그대로 쓴다.
    FOLLOW_UP: mail 장기 승인 레인이 `discord_transport` 를 확장하면 이 인코딩을 그리로 합친다.
    """
    from automation.interop.chunker import chunk_message

    head = chunk_message(body)[0]
    _post_multipart(token, channel_id, head, files)
    rest = body[len(head):]
    if rest:
        send_notice(token, channel_id, rest)


def notify_owner(
    content: str | None = None, *, notice: str | None = None, message: OwnerMessage | None = None,
    attachments: Sequence[Path] = (), fault: bool = False,
) -> bool:
    """Deliver one notice. False means "not delivered" — never an exception.

    `message` 가 있으면 가드 안에서 렌더하고, 없으면 본문을 그대로 보낸다.
    `notice=`는 기존 본문 별칭이다. 본문 누락·서로 다른 두 본문은 False로 거부한다.
    정기 통지 목적지는 참조 스레드 밖이므로 봉투의 원본 링크를 숨기지 않는다.
    `attachments=` 는 구매 검토처럼 초안 파일을 함께 올리는 통지용이다. 목적지 규칙·최선노력·
    실패 마커는 첨부가 있든 없든 같다 — 첨부 때문에 발신자가 자기 전송을 갖게 두면 목적지
    규칙이 두 벌이 되고, 드리프트하는 쪽은 언제나 둘째 사본이다(ON-2/ON-3).
    `fault=True`는 release가 정한 페어 채널을 우선한다. 미설정은 기존 목적지 그대로다.
    RETAINED: content: str 계약의 영구 예외는 calendar_confirm.send_owner_dm, approval_reminder._PointerSender,
    카드 없는 obsidian_write.gate_binding 위임도 포함한다. meeting 게이트웨이의 별도 문자열
    ACK는 INTEROP_RUNTIME 미보장 경계다. 이 경계·능력 폴백을 대체해 예외가 모두 해소되고
    격리 meeting 배달이 검증될 때만 문자열 경로의 퇴역을 재판정한다(채택 원장 참조).

    The except is broad **on purpose**: a narrower tuple would let one unanticipated
    error stop prod from converging, which is precisely the failure this exists to
    remove. See the module docstring.

    Failure is LOUD. A queued-but-undelivered notice is the same silence the feature
    exists to remove, so every failed attempt leaves a journal line — without the token.
    """

    try:
        token = resolve_token()
        fault_target = fault_channel() if fault else None
        if not token or not (fault_target or owner_notice_channel() or _config_owner_id()):
            print(
                "[owner-notice] NOTIFY-UNCONFIGURED: owner credential missing, notice not sent",
                file=sys.stderr,
            )
            return False
        body = content if content is not None else notice
        if body is None or (notice is not None and content is not None and notice != content):
            print("[owner-notice] NOTIFY-FAILED: TypeError", file=sys.stderr)
            return False
        # 채널이 지정되면 그 채널로만 — DM 폴백 없음("해당 채널에서만"이 요구다).
        channel_id = fault_target or resolve_notice_target(token)
        if message is not None:
            from automation.interop.owner_message import Ref, render, render_report

            # 페어 채널은 사람처럼 한 줄(리드 Q304) — 칸 형식 봉투는 형 채널 판본에만 남는다.
            shape = render_report if fault_target else render
            body = shape(message, destination=Ref(scope="channel", space="unknown", channel_id=channel_id))
        if not fault_target:
            # 형 채널에는 사람 말만(형 지시 2026-10-20 00:10 KST). 경고 코드가 든 통지는 형 할 일이
            # 아니므로 통째 페어 채널로(봉투를 줄 단위로 자르지 않는다), JSON 결과 줄은 언제나 빼다.
            from automation.interop.owner_text import split

            view = split(body)
            pair = fault_channel() if view.diagnostics else None
            if view.data or pair:
                print(f"[owner-notice] NOTIFY-TRIMMED: data={len(view.data)} to_pair={bool(pair)}", file=sys.stderr)
            body = view.without_data if view.data else body
            if pair and message is not None:
                body = render_report(message, destination=Ref(scope="channel", space="unknown", channel_id=pair))
            channel_id = pair or channel_id
            if view.data and not body:
                return False
        if attachments:
            send_notice_files(token, channel_id, body, attachments)
        else:
            send_notice(token, channel_id, body)
    except Exception as error:  # noqa: BLE001 - see docstring: escaping would stop prod
        print(f"[owner-notice] NOTIFY-FAILED: {type(error).__name__}", file=sys.stderr)
        return False
    return True


def notify_owner_dm(
    content: str | None = None, *, notice: str | None = None, message: OwnerMessage | None = None,
) -> bool:
    """DM 으로만 배달한다 — 소유자가 DM 을 명시한 통지 전용.

    첫 소비자였던 릴리스 적용 완료는 2026-09-10 소유자 지시로 `notify_owner`(#notifications)
    로 옮겨갔다. 이 함수는 남는다 — 파사드가 인정하는 **유일한** DM 전용 경로이고, 없애면
    다음에 \"이건 DM 으로\" 요구가 왔을 때 파사드 밖에 DM 오픈이 다시 생긴다(ON-2/ON-3 이
    막으려는 바로 그 모양).

    `notify_owner` 와 갈라지는 지점은 단 하나: 통지 채널이 설정돼 있어도 그리로 새지
    않는다. 지시가 "승인 요청 채널에만 머물지 말고 소유자 DM"이라 대상 자체가 요구다.
    DM 오픈은 여전히 이 파사드 안에서만 일어난다(ON-2/ON-3).
    `message` 가 있으면 열린 DM 을 목적지로 렌더하고, 없으면 본문 그대로다.
    `notice=` 별칭과 본문 누락·충돌 거부는 `notify_owner`와 같다.
    import·렌더 실패도 False, 예외는 절대 나가지 않는다(모듈 docstring 의 계약 그대로).
    RETAINED: content: str은 능력 폴백의 호환 계약이다. notify_owner에 적은 영구 예외
    (budget·calendar 문자열 수신, 최소정보 리마인더, Obsidian 위임)와 격리 meeting 경계가
    남아 있어 전체 문자열 계약을 퇴역하지 않는다. 그 경계·폴백을 대체하고 예외 해소와
    격리 meeting 배달을 검증한 뒤에만 재판정한다.
    """

    try:
        token = resolve_token()
        owner_id = _config_owner_id()
        if not token or not owner_id:
            print(
                "[owner-notice] NOTIFY-UNCONFIGURED: owner credential missing, notice not sent",
                file=sys.stderr,
            )
            return False
        body = content if content is not None else notice
        if body is None or (notice is not None and content is not None and notice != content):
            print("[owner-notice] NOTIFY-FAILED: TypeError", file=sys.stderr)
            return False
        channel_id = owner_dm_channel(token, owner_id)
        if message is not None:
            from automation.interop.owner_message import Ref, render

            body = render(message, destination=Ref(scope="channel", space="dm", channel_id=channel_id))
        send_notice(token, channel_id, body)
    except Exception as error:  # noqa: BLE001 - see docstring: escaping would stop prod
        print(f"[owner-notice] NOTIFY-FAILED: {type(error).__name__}", file=sys.stderr)
        return False
    return True
