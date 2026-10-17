# Discord 승인·통지 채널 이름 변경

## 무엇을

공급망 승인 채널과 소유자 통지 채널은 표시 이름이 아니라 설정된 ID로 찾는다.
`#approvals`·`#notifications`는 관례적인 예시 이름이며, 배포 후 같은 채널을
원하는 이름으로 바꿀 수 있다.

## 왜

기존 승인 표면 검증과 peer 차단 점검은 이름까지 비교해, 같은 채널을
이름만 바꿔도 승인이나 헬스체크가 실패했다. 이제 승인 표면은 텍스트 채널
type 0이고 이름이 `approvals`이거나 설정 ID(또는 명시적 공급망 핀)와 같으면 받는다.
예전 기록의 의미는 그대로 두고, 이름을 바꾼 설정 채널도 받는 방향으로만 넓혔다.

## 사용 시나리오

- **이름 변경:** `personal_approvals_channel_id`와 `owner_notice_channel_id`를
  각 계정 interop 설정에 유지하고 같은 Discord 채널의 이름만 변경한다.
  승인 카드·기존 바인딩·통지 목적지는 유지되며 설치 점검도 통과한다.
- **다른 채널:** 설정 ID도 공급망 핀도 아니고 이름도 `approvals`가 아닌 채널의
  저장된 승인은 `ApprovalSurfaceError`로 거부한다. 승인 규칙과 소유자 반응 검증은 그대로다.
- **설정 없는 레거시 설치:** 공급망 디렉터리의 캐시와 유일한 `approvals`
  이름 검색은 호환용으로만 남는다. 이름 변경 전 설정 ID를 기록한다.
  설정 ID가 있으면 캐시·이름 검색은 사용하지 않는다.
- **peer 점검:** 헬스체크의 peer 차단 점검은 `#approvals`·`#notifications`를
  이름으로 한 번 찾은 뒤 그 id를 ops 소유 메모(헬스체크 로그 디렉터리의
  `peer-ignored-ids.json`, 0600, id만)에 남긴다. 이름을 바꾼 뒤에도 peer 채널
  디렉터리에 같은 id가 있는 동안 그 id가 peer `discord.ignored_channels`에 있어야
  PASS다. 이름도 메모도 없으면(점검이 한 번도 PASS하지 않은 새 노드에서 이름부터
  바꾼 경우) `INVALID`로 실패한다.

## 운영자 할 일

없다. 권한·sudoers·노드 설정은 바뀌지 않는다. 새 노드는 헬스체크가 한 번 PASS한
뒤에 채널 이름을 바꾼다. 설정 ID(`personal_approvals_channel_id`,
`owner_notice_channel_id`)가 없는 레거시 설치는 이름을 바꾸기 전에 그 ID를 먼저
설정한다.

`POLICY_VERSION=8`과 저장 레코드 형식은 바꾸지 않는다. 목적지 종류는 같고,
검증 근거만 이름에서 신원으로 바뀐다.

## 관련

- `automation/interop/approval_surface.py`·`approval_directory.py`
- `automation/skill_gate_surface.py`·`automation/install/discord_check.py`
- `automation/peer_gateway_probe.sh`
- `tests/unit/test_approval_surface.py`·`test_healthcheck_peer_gateway_probe.py`
