# Discord 승인·통지 채널 이름 변경

## 무엇을

공급망 승인 채널과 소유자 통지 채널은 표시 이름이 아니라 설정된 ID로 찾는다.
`#approvals`·`#notifications`는 관례적인 예시 이름이며, 배포 후 같은 채널을
원하는 이름으로 바꿀 수 있다.

## 왜

기존 승인 표면 검증과 peer 차단 점검은 이름까지 비교해, 같은 채널을
이름만 바꿔도 승인이나 헬스체크가 실패했다. 이제 승인 표면은 텍스트 채널
type 0과 설정 ID의 일치를 확인한다. 다른 ID는 이름이 같아도 거부한다.

## 사용 시나리오

- **이름 변경:** `personal_approvals_channel_id`와 `owner_notice_channel_id`를
  각 계정 interop 설정에 유지하고 같은 Discord 채널의 이름만 변경한다.
  승인 카드·기존 바인딩·통지 목적지는 유지되며 설치 점검도 통과한다.
- **다른 채널:** 저장된 승인 ID가 현재 설정 또는 명시적 공급망 핀과 다르면
  `ApprovalSurfaceError`로 거부한다. 승인 규칙과 소유자 반응 검증은 그대로다.
- **설정 없는 레거시 설치:** 공급망 디렉터리의 캐시와 유일한 `approvals`
  이름 검색은 호환용으로만 남는다. 이름 변경 전 설정 ID를 기록한다.
  설정 ID가 있으면 캐시·이름 검색은 사용하지 않는다.
- **peer 점검:** peer의 `personal_approvals_channel_id`·`deploy_approvals_channel_id`
  중 하나 이상과 선택적 `owner_notice_channel_id`가 모두 최상위
  `discord.ignored_channels`에 있어야 한다. 빠짐·잘못된 ID·판독 불가는 FAIL이다.

## 운영자 할 일

기존 설치는 `automation/sudoers.d/autophagy-healthcheck-peer-read` 자산을
한 번 다시 설치한다. 추가 권한은 peer `~/.hermes/interop/config.json`의
`/usr/bin/cat -- <경로>` 읽기 한 줄뿐이다. 절차는
[기존 프로브 소개](peer-게이트웨이-승인채널-차단-프로브.md)의 권한 복구 절을 따른다.
이 작업 전 프로브는 판독 불가로 실패하며 자동 편집·재시동하지 않는다.
SSH 강제명령 바이트는 유지되므로 강제명령 허용 목록 재프로비저닝은 필요 없다.
peer 설정에는 agent가 사용하는 승인·통지 ID를 함께 기록해야 한다.

`POLICY_VERSION=8`과 저장 레코드 형식은 바꾸지 않는다. 목적지 종류는 같고,
검증 근거만 이름에서 신원으로 바뀐다.

## 관련

- `automation/interop/approval_surface.py`·`approval_directory.py`
- `automation/skill_gate_surface.py`·`automation/install/discord_check.py`
- `automation/peer_gateway_probe.sh`·`automation/sudoers.d/autophagy-healthcheck-peer-read`
- `tests/unit/test_approval_surface.py`·`test_healthcheck_peer_gateway_probe.py`
