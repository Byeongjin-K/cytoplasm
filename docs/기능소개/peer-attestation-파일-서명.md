# Peer attestation 파일 서명 모드 (W-F2.5-A)

## 무엇을

스킬 배포의 독립 peer 검증 결과를 Discord 두 번째 봇 답글 대신 peer 계정의 SSH 서명으로
증명할 수 있다. 신규 설치는 봇 하나만 운영하면서도 owner 승인과 peer 검증을 모두 통과해야
MOUNT되는 기존 4단계 게이트를 유지한다.

## 왜

기존 Discord 모드는 agent bot과 peer bot 두 개가 필요했다. 봇을 하나로 줄이면서 단순히 peer
답글 검사를 제거하면, 프롬프트 인젝션된 agent가 자기 리뷰를 스스로 통과시킬 수 있다. 파일
서명 모드는 agent가 읽을 수만 있는 root-owned 공개키와 peer만 읽는 private key로 발행 주체를
분리하고, 요청 nonce·스킬 digest·승인 채널·메시지·시각을 함께 서명해 같은 보안 속성을 보존한다.

## 사용 시나리오

### 정상 흐름

1. 신규 설치기가 peer 홈에 서명 keypair를 만들고 공개키만 `/etc/autophagy/`에 0644로 게시한다.
2. 스킬 배포가 peer 샌드박스 리뷰를 실행한다. peer는 stdout에 단일 signed attestation을 내보낸다.
3. gate가 고정 namespace·공개키·요청 바인딩·30분 TTL을 검증한다. 승인 메시지는 게시 당시
   본문 그대로 보존하며 peer verdict를 PATCH하지 않는다. 이미 PASS로 갱신된 옛 카드도 인식한다.
4. 소유자가 같은 메시지에 ✅를 누르고 서명 검증도 통과하면 MOUNT한다. 신규 카드는 v2 봉투이며
   저장된 본문 digest로 바인딩을 확인한다. signed blob은 파일이나 공유 큐에 남기지 않는다.

### 실패·거부 흐름

모드가 비어 있거나, 키·namespace·필드·nonce·digest가 다르거나, attestation이 요청보다 이르거나
TTL을 1초라도 넘기거나, 공개키 또는 부모가 agent-writable이면 gate가 fail-closed로 거부한다.
기존 `peer_attest_mode = "discord"` 설치는 종전 bot-id·reply-reference 검증을 그대로 사용한다.

### 기존 discord 설치의 전환

2026-10-06에 소유자는 `#approvals`의 `[skill-attest] … verdict=PASS` 답글이 필요 없다고 판단했다.
이 설치는 노드 쪽(peer 키·공개키·노드 설정)이 이미 signed로 준비돼 있었다. 그런데 배포를 실제로
돌리는 릴리스 호스트의 `~/.hermes/node.toml`만 `discord`로 남아 답글이 계속 나왔다. 모드는 배포
스크립트를 실행하는 머신의 설정이 정한다. 그래서 그 머신의 값을 `signed`로 바꾸면 전환이 끝난다.
전환 직후 첫 실측 배포에서 peer가 `signed attestation mode requires --channel-id`로 멈췄다.
`#approvals`를 이름으로 찾는 설치에는 `deploy_approvals_channel_id` 핀이 없는데,
배포 스크립트는 그 핀만 읽었다(discord 모드는 peer가 길드를 스캔해 이 문제를 가렸다).
이제는 핀이 없으면 게이트가 카드를 실제로 게시한 대기 레코드의 `channel_id`를 쓴다.
게이트가 서명을 검증할 때도 같은 값을 쓴다.
`--release-approval` 배포는 핀이 있어도 `pending/release.json`의 `channel_id`를
항상 쓴다. 릴리스 카드의 요청별 스레드가 서명 바인딩이며, 부모 채널 핀으로 대체하지 않는다.
최초 증명과 TTL 갱신 모두 이 값을 쓰고, 레코드나 채널이 없으면 peer 호출 전에 거부한다.
그 수정 뒤에도 `[skill-attest]` 답글이 한 번 더 남았다. peer는 게이트웨이용 봇 토큰을 갖고 있고,
signed 모드는 토큰이 있으면 전환기 호환을 위해 답글도 게시했기 때문이다. 이제 peer CLI는
signed 모드에서 Discord에 쓰지 않는다. 게이트는 이 모드에서 서명 레코드만 읽는다.
절차와 되돌리는 방법은 [install.md §3.1](../guide/install.md#31-기존-discord-설치를-signed로-옮기기)에 있다.

## 관련

- 계약·검증: `automation/peer_attestation.py`, `automation/peer_signed_attestation.py`
- producer/runtime: `automation/peer_attest.py`, `automation/peer_attest_runtime.py`
- gate·courier: `automation/skill_gate.py`, `automation/skill_gate_refresh.py`,
  `automation/deploy-skill.sh`
- 설치기: `automation/install/plan.py`, `automation/install/peer_attest_key.py`,
  `automation/install/apply.py`, `automation/install/state.py`
- 설계/작업: `.omo/plans/public-release.md` §W-F2.5-A
