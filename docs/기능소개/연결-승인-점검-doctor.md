# 연결·승인 점검과 알람 (doctor)

## 무엇을

`python3 -m automation.doctor` 가 서비스 계정 안에서 사람 몫의 연결 — 주 모델 로그인, Discord 봇,
승인 채널, Google Workspace 동의, Drive 발행, 기관메일, 로컬 전사, 정기 작업 — 을 읽기만 해서
통과·확인·고장으로 판정하고, 끊긴 항목마다 **누가 어디서 무엇을 승인할지** 절차를 낸다.
설치 마법사는 설치 전에 준비할 승인을 보여 주고 설치 끝에 doctor 로 하나씩 안내하며, 설치 뒤에는
`doctor-watch` 가 매시간 점검해 **새 문제가 생길 때만** 소유자에게 알린다.

## 왜

설치기와 healthcheck 는 인프라만 본다. 둘이 초록이어도 로그인·토큰·동의가 빠지면 에이전트가 조용히
멈췄고, 설치 흐름 어디에서도 그 승인들을 말하지 않았다. 운영 노드 첫 실측에서 일일 비용 보고가
26회, 메일 분류 보정 작업이 6회 연속 실패 중이었는데 아무에게도 알려지지 않고 있었다.
Google Workspace 동의 절차 문서는 공개 배포본에 없어 새 설치자는 안내를 받을 곳도 없었다.

## 사용 시나리오

### 새 노드 설치 — 승인까지 한 흐름으로

1. 마법사를 실행하면 ① 전제 확인 아래에 「설치가 끝나면 사람이 승인·연결해야 하는 것」 9가지가 나온다.
2. `yes` 로 설치가 끝나면 ⑦ 에서 doctor 가 돈다. 예: `[FAIL] primary-model — openai-codex 로그인이 없다`
   와 함께 `hermes auth add openai-codex --type oauth` 명령과 기기 코드 승인 방법이 나온다.
3. 브라우저에서 승인한 뒤 Enter 를 누르면 다시 점검한다. 고장이 사라질 때까지 반복하고, 남은 `[WARN]`
   (예: 기관메일 미구성)은 선택 기능이라 그대로 두어도 된다. `q` 로 나중에 할 수도 있다.

### 운영 중 — 끊기면 알림이 온다

- Google 토큰이 만료되면 다음 정각 점검에서 `#notifications` 에 「새로 확인된 문제 1건: 고장 · Google
  Workspace」가 온다. 다시 동의하고 나면 통지 없이 해결 상태를 기록한다. 같은 문제는 되풀이하지 않는다.
- 운영자가 원인을 볼 때는 `cd /srv/autophagy-agent-current && python3 -m automation.doctor` 한 줄로
  agent·peer 를 함께 본다.

### 거부·판정 불가

- 운영자 계정에 `sudo -n` 권한이 없으면 그 계정은 `[WARN] doctor-run` 과 수동 명령으로 남는다 — 통과로
  세지 않으므로 마법사의 재점검 루프도 끝나지 않는다.
- 주 모델이 `custom:<이름>` 처럼 사용자 정의 공급자면 로그인을 요구하지 않는다(선언만 확인).
- 알람 전송이 실패하면 상태를 전진시키지 않아 다음 점검이 다시 보낸다.

2026-10-18 소유자 질문 “이거 계속 필요해?” 반영: 새 문제가 없는 해결·정상 점검은 통지하지 않고 상태와 점검 시각만 갱신하며, 새 문제가 있으면 같은 메시지에 해결 항목도 담는다.

## 관련

- 가이드(절차의 정본): [연결·승인 점검](../guide/연결-승인-점검.md)
- 코드: `automation/doctor/`(`capabilities.py` 판정 레지스트리 · `facts.py` 읽기 전용 관측 ·
  `alarm.py` 변화 알람 · `cron/doctor_watch.py` 워처), 설치 연동 `automation/install/wizard.py` ·
  `quickstart.sh` · 컴포넌트 `doctor-watch`(`automation/install/components.py`, 네 프로필 기본 포함)
- 승인 게이트: 관여하지 않는다 — 읽기 전용 진단이며 외부효과를 실행하지 않는다.
- 테스트: `tests/unit/test_doctor.py` · `test_doctor_install_component.py` · `test_install_wizard.py`
