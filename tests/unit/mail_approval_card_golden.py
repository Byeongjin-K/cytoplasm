"""Literal card captures; never rebuilt from production helpers at test runtime."""
from __future__ import annotations
import datetime

FIELDS = {
    'original': {'subject_key': 'abc123', 'subject': '수신메일 회신 발송 승인 요청', 'fact': '- 분류: important / 플래그: reply_needed · - 발신(마스킹): `sha256:masked` · - 원문 제목: 문의 · - Cc: `cc@example.invalid` · - 회신 제목: Re: 문의 · - 회신 본문: · 회신 본문', 'location': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'owner': {'verb': 'react', 'target': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'argument': '이 메시지에 ✅ 실행 / ⛔ 취소'}, 'agent_next': '승인된 메일만 발송', 'recovery': 'irreversible', 'detail': {'expires_at': None, 'cancel_effect': '메일을 발송하지 않음'}, 'contract_version': 1, 'render_version': 'owner-ko-v1'},
    'compose': {'subject_key': 'abc123', 'subject': '새 메일 발송 승인 요청 (DM 확정)', 'fact': '- To: `to@example.invalid` · - Cc: `cc@example.invalid` · - 제목: `Re: 문의` · - 본문: · 회신 본문', 'location': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'owner': {'verb': 'react', 'target': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'argument': '이 메시지에 ✅ 실행 / ⛔ 취소'}, 'agent_next': '승인된 메일만 발송', 'recovery': 'irreversible', 'detail': {'expires_at': None, 'cancel_effect': '메일을 발송하지 않음'}, 'contract_version': 1, 'render_version': 'owner-ko-v1'},
    'gmail': {'subject_key': 'abc123', 'subject': 'Gmail 발송 승인 요청 (DM 확정)', 'fact': '- 발신 계정: `sender@example.invalid` · - 작업: `reply` · - 수신자: `to@example.invalid` · - Cc: `cc@example.invalid` · - 회신 대상: `reply-1` · - 제목: `Re: 문의` · - 본문: · 회신 본문', 'location': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'owner': {'verb': 'react', 'target': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'argument': '이 메시지에 ✅ 실행 / ⛔ 취소'}, 'agent_next': '승인된 메일만 발송', 'recovery': 'irreversible', 'detail': {'expires_at': None, 'cancel_effect': '메일을 발송하지 않음'}, 'contract_version': 1, 'render_version': 'owner-ko-v1'},
    'calendar': {'subject_key': 'abc123', 'subject': '캘린더 변경', 'fact': 'create', 'location': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'owner': {'verb': 'react', 'target': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'argument': '이 메시지에 ✅ 실행 / ⛔ 취소'}, 'agent_next': '승인된 캘린더 변경만 실행', 'recovery': 'not_applicable', 'detail': {'expires_at': None, 'cancel_effect': '캘린더를 변경하지 않음'}, 'contract_version': 1, 'render_version': 'owner-ko-v1'},
    'coordination': {'subject_key': 'abc123', 'subject': '일정 조율', 'fact': '📅 일정 조율 (coord-1): 상대 에이전트(peer)가 2026-09-01 (화) 09:00~09:30 KST 슬롯을 승인했습니다. · 제목: 일정 제목', 'location': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'owner': {'verb': 'react', 'target': {'scope': 'self', 'space': 'unknown', 'guild_id': None, 'channel_id': None, 'message_id': None, 'url': None, 'search': None}, 'argument': '이 메시지에 ✅ 실행 / ⛔ 취소'}, 'agent_next': '승인된 일정을 캘린더에 등록', 'recovery': 'not_applicable', 'detail': {'expires_at': datetime.datetime(2026, 9, 2, 0, 0, tzinfo=datetime.timezone.utc), 'cancel_effect': '일정을 등록하지 않음'}, 'contract_version': 1, 'render_version': 'owner-ko-v1'},
}

BYTES = {
    'original': '대상: 수신메일 회신 발송 승인 요청 (abc123)\n사실: - 분류: important / 플래그: reply_needed · - 발신(마스킹): `sha256:masked` · - 원문 제목: 문의 · - Cc: `cc@example.invalid` · - 회신 제목: Re: 문의 · - 회신 본문: · 회신 본문 (승인 요청; 만료: 기한 없음)\n위치: 이 메시지\n인계: 소유자: 위 위치 · 반응 이 메시지에 ✅ 실행 / ⛔ 취소; 다음: 승인된 메일만 발송\n되돌리기: 되돌릴 수 없음; 취소 시: 메일을 발송하지 않음\n- draft: `abc123` sha256: `digest-1`',
    'compose': '대상: 새 메일 발송 승인 요청 (DM 확정) (abc123)\n사실: - To: `to@example.invalid` · - Cc: `cc@example.invalid` · - 제목: `Re: 문의` · - 본문: · 회신 본문 (승인 요청; 만료: 기한 없음)\n위치: 이 메시지\n인계: 소유자: 위 위치 · 반응 이 메시지에 ✅ 실행 / ⛔ 취소; 다음: 승인된 메일만 발송\n되돌리기: 되돌릴 수 없음; 취소 시: 메일을 발송하지 않음\n- draft: `abc123` sha256: `digest-1`',
    'gmail': '대상: Gmail 발송 승인 요청 (DM 확정) (abc123)\n사실: - 발신 계정: `sender@example.invalid` · - 작업: `reply` · - 수신자: `to@example.invalid` · - Cc: `cc@example.invalid` · - 회신 대상: `reply-1` · - 제목: `Re: 문의` · - 본문: · 회신 본문 (승인 요청; 만료: 기한 없음)\n위치: 이 메시지\n인계: 소유자: 위 위치 · 반응 이 메시지에 ✅ 실행 / ⛔ 취소; 다음: 승인된 메일만 발송\n되돌리기: 되돌릴 수 없음; 취소 시: 메일을 발송하지 않음\n- draft: `abc123` sha256: `digest-1`\n- action hash: `sha256:action-1`',
    'calendar': '대상: 캘린더 변경 (abc123)\n사실: create (승인 요청; 만료: 기한 없음)\n위치: 이 메시지\n인계: 소유자: 위 위치 · 반응 이 메시지에 ✅ 실행 / ⛔ 취소; 다음: 승인된 캘린더 변경만 실행\n되돌리기: 해당 없음; 취소 시: 캘린더를 변경하지 않음\nsha256:digest-1',
    'coordination': '대상: 일정 조율 (abc123)\n사실: 📅 일정 조율 (coord-1): 상대 에이전트(peer)가 2026-09-01 (화) 09:00~09:30 KST 슬롯을 승인했습니다. · 제목: 일정 제목 (승인 요청; 만료: 2026-09-02T00:00:00+00:00)\n위치: 이 메시지\n인계: 소유자: 위 위치 · 반응 이 메시지에 ✅ 실행 / ⛔ 취소; 다음: 승인된 일정을 캘린더에 등록\n되돌리기: 해당 없음; 취소 시: 일정을 등록하지 않음\nsha256:digest-1',
}

OWNER_LOCAL_V2 = """**🔔 S42**
> F42

**결정:** 여기 · 반응 A42 · 만료 없음
취소 시: C42
다음: N42
되돌리기: 여기 · 답글 R42
-# 참조: `01234567`"""

CALENDAR_V3 = (
    "대상: 캘린더 변경 (abc123)\n"
    "사실: 생성: 아래 일정 추가 · 제목: Synthetic appointment A · "
    "시작: 2031-10-01T09:00:00+09:00 · 종료: 2031-10-01T10:00:00+09:00 "
    "(승인 요청; 만료: 기한 없음)\n"
    "위치: 이 메시지\n"
    "인계: 소유자: 위 위치 · 반응 이 메시지에 ✅ 실행 / ⛔ 취소; "
    "다음: 승인된 캘린더 변경만 실행\n"
    "되돌리기: 해당 없음; 취소 시: 캘린더를 변경하지 않음\n"
    "sha256:e9c6614713eba5d23c3982f540306b3bfffddb7b79768b4ebc5b8dff4052dedf"
)

CALENDAR_V4 = """**🔔 캘린더 변경**
> 작업: 생성: 아래 일정 추가
> 제목: Synthetic appointment A
> 시작: 2031-10-01 09:00 (+09:00)
> 종료: 2031-10-01 10:00 (+09:00)

**결정:** 이 메시지에 ✅ 실행 / ⛔ 취소 · 만료 없음
취소 시: 캘린더를 변경하지 않음
다음: 승인된 캘린더 변경만 실행
-# 참조: `abc123`
해시: `sha256:e9c6614713eba5d23c3982f540306b3bfffddb7b79768b4ebc5b8dff4052dedf`"""

MAIL_ATTACHMENT_V3 = """**🔔 메일 발송 승인**
> 제목: S42
> To: to@example.invalid
> Cc: -
> 본문: 첨부 파일 참조 (3 bytes)

**결정:** 이 메시지에 A42 · 만료 없음
취소 시: 메일을 발송하지 않음
다음: 승인된 메일만 발송
되돌리기: 되돌릴 수 없음
-# 참조: `abc123`
- draft: `abc123` sha256: `digest-1`
- body sha256: `72294ba1616ddcaee48e56501040e876d26bd0c1839f3cddd34900f590f717a4`
- action hash: `digest-1`"""

# New v3 captures are appended; the posted v1/v2 captures above stay immutable.
BYTES_V3 = {
    "original": """**🔔 수신메일 회신 발송 승인 요청**
> - 분류: important / 플래그: reply_needed
> - 발신(마스킹): `sha256:masked`
> - 원문 제목: 문의
> - Cc: `cc@example.invalid`
> - 회신 제목: Re: 문의
> - 회신 본문:
> ```
> 회신 본문
> ```

**결정:** 이 메시지에 ✅ 실행 / ⛔ 취소 · 만료 없음
취소 시: 메일을 발송하지 않음
다음: 승인된 메일만 발송
되돌리기: 되돌릴 수 없음
-# 참조: `abc123`
- draft: `abc123` sha256: `digest-1`""",
    "compose": """**🔔 새 메일 발송 승인 요청 (DM 확정)**
> - To: `to@example.invalid`
> - Cc: `cc@example.invalid`
> - 제목: `Re: 문의`
> - 본문:
> ```
> 회신 본문
> ```

**결정:** 이 메시지에 ✅ 실행 / ⛔ 취소 · 만료 없음
취소 시: 메일을 발송하지 않음
다음: 승인된 메일만 발송
되돌리기: 되돌릴 수 없음
-# 참조: `abc123`
- draft: `abc123` sha256: `digest-1`""",
    "gmail": """**🔔 Gmail 발송 승인 요청 (DM 확정)**
> - 발신 계정: `sender@example.invalid`
> - 작업: `reply`
> - 수신자: `to@example.invalid`
> - Cc: `cc@example.invalid`
> - 회신 대상: `reply-1`
> - 제목: `Re: 문의`
> - 본문:
> ```
> 회신 본문
> ```

**결정:** 이 메시지에 ✅ 실행 / ⛔ 취소 · 만료 없음
취소 시: 메일을 발송하지 않음
다음: 승인된 메일만 발송
되돌리기: 되돌릴 수 없음
-# 참조: `abc123`
- draft: `abc123` sha256: `digest-1`
- action hash: `sha256:action-1`""",
    "coordination": """**🔔 일정 조율**
> 📅 일정 조율 (coord-1): 상대 에이전트(peer)가 2026-09-01 (화) 09:00~09:30 KST 슬롯을 승인했습니다.
> 제목: 일정 제목

**결정:** 이 메시지에 ✅ 실행 / ⛔ 취소 · 만료 2026-09-02 00:00 (+00:00)
취소 시: 일정을 등록하지 않음
다음: 승인된 일정을 캘린더에 등록
-# 참조: `abc123`
sha256:digest-1""",
}
