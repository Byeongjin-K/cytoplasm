# agent-chat 대화를 유돌에게 전환

## 무엇을·왜

P3 전환은 **기본 꺼짐**이다. 켜면 agent 계정의 Hermes 게이트웨이가 `#agent-chat`에서 답하지 않고
준비된 유돌(YooDori)이 대화를 맡는다. 도리를 설치하지 않은 환경의 응답을 끊지 않으면서
두 에이전트가 같은 요청에 답하는 일을 막는다.
Hermes 최상위 `discord.ignored_channels`가 `free_response_channels`·허용 목록보다 우선한다.
승인 카드·리액션 워처·메일 등 기존 실행 경로를 옮기는 변경은 아니다.

켜짐 여부는 릴리스 호스트의 `~/.hermes/release-flags/dori-agent-chat` 파일 하나로 정한다.
내용은 대화를 받을 도리 봇의 숫자 ID뿐이어야 한다(공백·줄바꿈도 넣지 않는다).
파일이 없으면 기존 메모리 프로필을 적용하고 이 채널의 무시만 제거한다.
잘못된 내용이나 판독 실패는 꺼짐으로 넘어가지 않고 거부한다.

채널 ID는 노드 계정의 `~/.hermes/interop/config.json`의 `agent_chat_channel_id`에서만 읽는다.
노드는 릴리스 플래그를 읽지 않고 호스트가 전달한 `--dori-agent-chat on|off`를 받는다.
agent의 `hermes-agent-profile-v1`이 메모리 상한·자동 회상과 이 선택을 함께 적용한다.
백업 한 번 → 임시 파일 → 원자 교체 → 두 설정 재조회 순서이며,
peer는 기존 `hermes-memory-profile-with-recall-v1`을 유지한다.
`activation=gateway`가 릴리스의 agent+peer 동시 재시동을 요구한다.

## 사용 시나리오

- **기본:** 전환 파일 없이 릴리스하면 기존 메모리 설정만 맞춘다. 채널이 free 목록에 있으면
  doctor는 “도리 전환 꺼짐 — agent-chat 채널은 @ 없이도 답한다”를 PASS로 표시한다.
- **켜기:** 유돌 수신을 준비한 뒤 릴리스 호스트에 위 파일을 만들고 숫자 봇 ID만 저장한다.
  표준 릴리스 카드에 다음 줄이 **한 번** 표시된 것을 보고 승인한다.

  > 도리 전환 켬: 노드 agent 게이트웨이가 #agent-chat 무시(유돌이 받음)

  `dori-agent-chat:<봇 ID>`와 그 지문이 승인 해시에 묶인다. 배포기가 agent 설정을 맞추고
  두 게이트웨이를 재시동한다. 이후 `#agent-chat`에 말하면 Hermes agent는 답하지 않는다.
  doctor는 “agent-chat 채널은 유돌이 맡는다(이 게이트웨이는 무시)”를 PASS로 표시한다.
  free 목록에 같은 채널이 남아 있어도 ignored가 우선하며, 시작 전 자동 등록기는 무시 목록을 지우지 않는다.
- **승인 누락:** 전환 파일은 있지만 배포 HEAD의 승인 카드가 같은 봇 ID를 포함하지 않거나
  소유자 ✅를 확인하지 못하면 `DORI-SWITCH-UNAUTHORIZED`로 배포를 막고 노드 설정은 보존한다.
- **설정 거부:** 켜짐에서 채널 ID가 없으면 종료 코드 3, 쓰기·백업 없음이다.
  꺼짐·`--show`에서는 interop 설정이나 채널 ID가 없어도 오류가 아니며 `agent_chat_ignored=no`다.
  중복 키·인라인 Discord 매핑·별칭·해석할 수 없는 목록도 고치지 않는다.
  숫자 ID의 쉼표 문자열·한 줄 목록·블록 목록은 지원한다. `--check`는 불일치 1,
  재조회 불일치는 5다. 기존 메모리 프로필의 외부 제공자·상한 낮추기 거부(7)도 유지한다.
- **읽기 점검:** agent 계정의 릴리스 런타임에서
  `python3 -m automation.memory_provider.agent_config --show`로 두 설정을 함께 본다.
  `--check`는 `--dori-agent-chat on|off`를 명시해야 한다.
  `agent_chat_ignored=yes`는 저장된 ID가 무시 목록에 있다는 뜻이며 ID 자체는 출력하지 않는다.

## 되돌리기

릴리스 호스트의 `~/.hermes/release-flags/dori-agent-chat`를 **삭제하고 재배포**한다.
호스트는 `off`를 전달하며 기존 합성 선언 그대로 해당 채널만 무시 목록에서 뺀다.
다른 무시 채널·메모리 설정을 보존하고 agent+peer를 함께 재시동한다.
코드·선언 변경이나 노드의 수동 `--remove`는 필요 없다.

## 관련

- 선언·배포: `automation/memory_curator/{deploy-manifest.txt,deploy.sh}`, 공용 `automation/deploy_config.sh`
- 합성: `automation/memory_provider/agent_config.py`, 목록 편집: `automation/hermes_agent_chat_ignore.py`
- 점검: `automation/doctor/capabilities.py`, [연결·승인 점검](../guide/연결-승인-점검.md)
- 회귀: `tests/unit/test_hermes_agent_chat_ignore.py`, `test_hermes_agent_profile.py`, `test_doctor.py`
- 승인: 기존 릴리스 승인으로만 배포하며 별도 카드·워처는 추가하지 않는다.
