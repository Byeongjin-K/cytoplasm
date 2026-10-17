# agent-chat 대화를 유돌에게 전환

## 무엇을·왜

P3 전환 릴리스는 agent 계정의 Hermes 게이트웨이가 `#agent-chat`에서 답하지 않게 한다.
이미 준비된 유돌(YooDori)이 대화를 맡을 때 두 에이전트가 같은 요청에 답하는 일을 막는다.
Hermes 최상위 `discord.ignored_channels`가 `free_response_channels`·허용 목록보다 우선한다.
승인 카드·리액션 워처·메일 등 기존 실행 경로를 옮기는 변경은 아니다.

채널 ID는 노드 계정의 `~/.hermes/interop/config.json`에 있는 `agent_chat_channel_id`에서만 읽는다.
저장소에는 ID를 넣지 않는다. agent의 기존 `config.yaml` 선언을
`hermes-agent-profile-v1`로 교체해 메모리 상한·자동 회상 설정과 채널 무시를 함께 적용한다.
백업 한 번 → 임시 파일 → 원자 교체 → 두 설정 재조회 순서이며,
peer는 기존 `hermes-memory-profile-with-recall-v1`을 유지한다.
`activation=gateway`가 릴리스의 agent+peer 동시 재시동을 요구한다.

## 사용 시나리오

- **정상:** 유돌이 준비된 상태에서 형이 표준 릴리스를 승인한다. 배포기가 agent 설정을 맞추고
  두 게이트웨이를 재시동한다. 이후 `#agent-chat`에 말하면 Hermes agent는 답하지 않는다.
  doctor는 “agent-chat 채널은 유돌이 맡는다(이 게이트웨이는 무시)”를 PASS로 표시한다.
  free 목록에 같은 채널이 남아 있어도 ignored가 우선하며, 시작 전 자동 등록기는 무시 목록을 지우지 않는다.
- **거부:** 채널 ID가 없거나 파일을 읽지 못하면 종료 코드 3, 쓰기·백업 없음이다.
  중복 키·인라인 Discord 매핑·별칭·해석할 수 없는 목록도 고치지 않는다.
  숫자 ID의 쉼표 문자열·한 줄 목록·블록 목록은 지원한다. `--check`는 불일치 1,
  재조회 불일치는 5다. 기존 메모리 프로필의 외부 제공자·상한 낮추기 거부(7)도 유지한다.
- **읽기 점검:** agent 계정의 릴리스 런타임에서
  `python3 -m automation.memory_provider.agent_config --show` 또는 `--check`로 두 설정을 함께 본다.
  `agent_chat_ignored=yes`는 저장된 ID가 무시 목록에 있다는 뜻이며 ID 자체는 출력하지 않는다.

## 되돌리기

**선언 행을 지우는 것만으로는 무시가 해제되지 않는다.** 영구 복구는 agent 행을 기존 메모리 전용
알고리즘·소스(`automation/memory_provider/config.py`)로 되돌리고 배포기의 agent `converge_config`
호출도 `hermes-memory-profile-with-recall-v1`로 되돌리는 릴리스를 먼저 적용한다.
그다음 agent 계정의 릴리스 런타임에서 다음 수동 명령을 실행한다.

```bash
python3 -m automation.hermes_agent_chat_ignore --remove
```

이 명령은 백업 후 해당 채널만 빼고 다른 무시 채널·나머지 설정은 보존한다.
운영 가이드의 절차로 agent+peer를 함께 재시동해야 실행 중 설정도 바뀐다.
새 합성 선언이 남아 있으면 다음 수렴 때 채널이 다시 무시되므로, 단독 실행은 임시 복구에만 쓴다.

## 관련

- 선언·배포: `automation/memory_curator/{deploy-manifest.txt,deploy.sh}`, 공용 `automation/deploy_config.sh`
- 합성: `automation/memory_provider/agent_config.py`, 목록 편집: `automation/hermes_agent_chat_ignore.py`
- 점검: `automation/doctor/capabilities.py`, [연결·승인 점검](../guide/연결-승인-점검.md)
- 회귀: `tests/unit/test_hermes_agent_chat_ignore.py`, `test_hermes_agent_profile.py`, `test_doctor.py`
- 승인: 기존 릴리스 승인으로만 배포하며 별도 카드·워처는 추가하지 않는다.
