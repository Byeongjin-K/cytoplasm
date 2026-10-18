# 대화·알림 채널을 유돌 채널로 합치기

노드가 새 다이제스트·승인 요청·결과·정기 통지를 유돌 채널에 올리게 한다.
기존 대화 채널과 알림 채널을 삭제하기 전에 목적지를 릴리스로 바꾸는 기능이다.
이 변경은 게시 위치만 옮긴다. 소유자 ID·비봇 반응·승인 해시 검증과
✅ 실행·⛔ 우선 규칙은 그대로이며 승인 자체를 대신하지 않는다.

## 전환과 복구

릴리스 호스트의 `~/.hermes/release-flags/yoodori-channel`에 대상 채널의
17~20자리 숫자 ID만 넣는다(줄바꿈 없음). 파일이 없으면 기존 동작이며,
잘못된 파일은 SSH 전에 거부한다. 대상은 릴리스 승인 해시에 묶이고 카드에
다음 한 줄이 표시된다.

> 채널 합치기: 노드의 대화(#agent-chat)·알림(#notifications)을 채널 `<id>`(유돌 채널)로

같은 HEAD·채널의 소유자 승인이 없으면 배포가 멈춘다. 승인 후 선언된 수렴기가:

- agent `~/.hermes/interop/config.json`의 `agent_chat_channel_id`와
  `owner_notice_channel_id`를 변경하고 각각의 `*_before_release`에 원래 값을 보존한다.
- peer의 같은 파일에서는 `owner_notice_channel_id`만 변경·복원한다.
- ops는 노드 설정 `private_root` 아래 `channel-override.json`에 두 새 ID와 복원 정보를 쓴다
  (기본 `/srv/autophagy-private/channel-override.json`). 두 수리 유닛의 `ProtectHome=yes`가
  홈을 숨기므로 홈 파일은 읽지 않는다. 기존 ops 전용 디렉터리에 ops로 쓰며 root 권한을 늘리지 않는다.
  root 소유 `/etc/autophagy/interop-ops.json`과 `repair-approval.env`는 수정하지 않는다.
  이 파일은 읽는 계정 소유·0600 일반 파일이고 두 ID가 17~20자리 숫자일 때만
  `/etc`와 `OWNER_NOTICE_CHANNEL_ID`보다 우선한다. 잘못된 override는
  값을 노출하지 않는 경고 한 줄을 남기고 기존 설정으로 돌아간다.
- agent Hermes의 무시 목록은 옛 대화 채널과 새 채널을 함께 포함한다.
  옛 대화 채널은 자유 응답 목록에서 제거한다. peer의 무시 목록은 건드리지 않는다.

설정에 소유자 guild ID가 있으면 쓰기 전에 Discord GET으로 대상의 guild·텍스트
채널 여부를 검증한다. 런타임 override 조회는 Discord를 호출하지 않는다.
doctor·헬스체크에는 override 사용 시 `노드 설정 private_root/channel-override.json`이
우선한다는 정상(PASS) 한 줄이 표시된다.
파일을 지우고 재배포하면 두 계정의 저장값을 복원하고 ops override를 제거한다.
같은 설정 재배포는 무동작이며 변경 시 백업·원자 교체·재조회로 확인한다.

**/etc 두 파일의 옛 값은 다음에 형이 sudo 할 일이 있을 때 정리(그때까지 덮어쓰기 파일이 우선).**

## 기존 카드를 먼저 끝내기

기존 승인 레코드·메시지·해시는 바꾸거나 재게시하지 않는다. 스위치 적용 전에 해당
계정에서 다음 읽기 전용 명령으로 두 옛 채널 아래의 미결 카드를 확인한다.

```bash
python3 -m automation.interop.channel_move pending --old <옛 대화 ID> <옛 알림 ID>
```

출력은 카드마다 JSON 한 줄(`kind`, `id`, 마스킹된 `title`, `link`)과
마지막 `CHANNEL-MOVE-PENDING count=N`이다. 메일·일정·할 일·위키·프로젝트
제안·Obsidian 저장·메모리 이동을 읽고, 스레드 부모와 소유자 반응을 GET으로
확인한다. 읽기 실패는 `CHANNEL-MOVE-PENDING-UNKNOWN`으로 끝나며 삭제 가능으로
간주하지 않는다. 미결은 원 카드에서 결정하거나 기존 만료 절차로 끝낸 뒤 전환하고
채널을 삭제한다. 저장된 스레드도 현재 부모 채널을 검증하므로 미결을 남긴 채 설정만
먼저 바꾸지 않는다.

종결 카드의 결과 통지와 과거 다이제스트 스레드 이름 변경·종결에서 404는 조용히
넘긴다. 미결 카드의 승인 안전 검사를 404 면제로 우회하지 않는다.
채널 삭제·수리 티켓 생성·승인 실행은 이 명령이 하지 않는다.

ops 수리 카드는 정책 v8의 요청별 승인 스레드다.
`repair_ops_discord` → `repair_ops_binding.new_binding` → `approval_directory.agent_chat`
경로도 override를 읽으므로 신규 카드는 유돌 채널 아래에 열린다.
`REPAIR_APPROVALS_CHANNEL_ID`는 이 경로에서 읽지 않으며 수정하지 않는다.

## 남는 옛 ID

복원 키·설정 백업, 변경하지 않은 기존 승인·첨부·과거 다이제스트 기록에는 옛 ID가
남는다. Hermes 캐시·peer 무시 목록과 미실행 일회성 `create_*.py`도 이 기능이
정리하지 않는다. root 파일의 옛 ID는 override 아래에 그대로 남는다.
복원 정보는 릴리스 수렴기만 읽으며 일반 발신 경로는 새 설정·override를 사용한다.

선언의 `private/channel-override.json`은 HOME 상대 파일이 아니라 알고리즘이
노드 설정 `private_root`에 바인딩하는 논리 목적지다. 읽기·쓰기·관측은 같은 경로 함수를 쓴다.

관련: `automation/yoodori_channel.py`, `automation/memory_curator/deploy-manifest.txt`,
`automation/interop/approval_directory.py`, `automation/owner_notice.py`,
`automation/interop/channel_move.py`, `automation/interop/origin_notice.py`.
