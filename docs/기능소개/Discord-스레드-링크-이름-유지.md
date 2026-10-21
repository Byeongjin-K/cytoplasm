# Discord 스레드 링크 이름 유지

Discord에 보내는 스레드 안내는 주소만 노출하지 않고, 「승인스레드」·「승인 카드」·「전문」·
「원글」 같은 문구를 가진 마스킹 링크로 표시한다.
읽는 사람의 Discord 캐시에 보관된 스레드가 없더라도 링크 이름이 「알 수 없음」으로 바뀌지 않는다.

## 사용 시나리오

메일 발송 승인 요청을 올리면 CLI가 에이전트에게 마스킹된 승인 스레드 링크를 넘긴다.
에이전트가 그 값을 답장에 그대로 붙이면 소유자는 「승인스레드」를 눌러 승인 카드로 이동한다.
스레드가 완료 표시로 바뀌고 보관된 뒤에도 답장의 문구는 유지된다.

승인 리마인더와 릴리스 상세의 역링크는 「승인 카드」, 메일 전문 안내는 「전문」,
페어 채널 보고는 「원글」로 표시한다. 메모리 큐레이터의 보존된 리마인더와 Plaud 상태의
사람용 출력도 같은 형태다. 좌표가 없으면 기존 검색 안내나 식별자만 남기며 주소를 추측하지 않는다.
동적 문구는 대괄호를 제거하고 공백을 접어 링크 문법을 보존한다.

## 유지하는 경계

`discord_link`는 계속 원시 URL을 돌려준다. HTML 대시보드, JSON 필드, 명령 근거 원장,
에이전트 입력은 바꾸지 않는다. 캘린더의 `DIGEST-CARD` URL도 기계 필드이며 메일 다이제스트가
이미 마스킹해 표시한다. 저장된 승인 카드의 렌더 판본과 해시 검증 바이트는 그대로 보존한다.
릴리스 v6의 상세 참조는 과거 카드 재생 계약이고 신규 v8 카드는 해당 참조를 게시하지 않는다.
승인·취소 판정, 상태 이모지, 스레드 보관 규칙은 바꾸지 않는다.

Plaud 상태 CLI는 저장소 런타임 없이도 실행해야 하므로 고정 문구의 표시만 자체 조립한다.
그 외 안내는 `owner_message.masked_discord_link`를 공유한다.

## 관련

- 공유 표시: `automation/interop/owner_message.py`
- CLI 인계: `automation/interop/thread_pointer.py`
- 리마인더: `automation/interop/approval_reminder.py`, `automation/memory_curator/reminder.py`
- 릴리스 역링크: `automation/release_request_gate.py`
- 메일 전문: `skills/mail/scripts/digest_reaction_actions.py`
- 읽기 전용 상태: `skills/plaud/scripts/plaud_cli.py`
