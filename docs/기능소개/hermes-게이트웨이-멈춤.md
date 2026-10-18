# Hermes 게이트웨이 멈춤과 복구

## 무엇을·왜

도리가 대화를 맡을 때 노드의 agent·peer Hermes 게이트웨이를 **정지만** 한다.
서비스·설정·파일·Hermes 잡을 제거하거나 서비스를 disable하지 않는다.
기본은 실행 상태이며, P3 전환과 같은 기존 릴리스 승인 경로를 재사용한다.

릴리스 호스트의 `~/.hermes/release-flags/hermes-gateways-stopped`가 스위치다.
내용은 줄바꿈·공백 없이 `stopped`만 허용한다. 부재는 `running`이며,
다른 내용·읽기 실패는 `GATEWAY-STOP-REFUSED`(배포 exit 4)로 SSH 전에 거부한다.
노드는 이 호스트 파일을 읽지 않는다. 호스트가 한 번 읽어 모든 배포 프로브에
`--hermes-gateways stopped|running`을 `--dori-agent-chat on|off`와 함께 넘긴다.

## 사용 시나리오

1. **멈춤:** 도리 수신 준비 뒤 호스트에 스위치를 만들고 표준 릴리스를 요청한다.
   카드에는 파일이 있을 때만 다음 줄이 붙는다.

   > 게이트웨이 멈춤: 노드 agent·peer Hermes 게이트웨이 정지(제거 아님, 파일 삭제 후 재배포로 다시 켬)

   `hermes-gateways:stopped` 표면과 `stopped`의 SHA-256이 승인 해시에 묶인다.
   배포 HEAD의 소유자 승인에 이 표면이 없으면 `GATEWAY-STOP-UNAUTHORIZED`로 멈춘다.
   파일이 없으면 기존 카드 바이트·해시는 바뀌지 않는다.
2. **실행:** 승인 뒤 배포기가 두 계정에서 `systemctl --user stop hermes-gateway.service`를
   실행하고 `is-active`가 `inactive`인지 다시 확인한다. 한 계정이 실패해도 다른 계정은 시도하며,
   실패·미상 상태에는 전량 반영 영수증을 발급하지 않는다.
   정지 중에는 플러그인 세대 검사·agent+peer 재시동·세대 대기를 하지 않는다.
3. **상시 점검:** 같은 단계에서 계정별 `~/.hermes/gateway-stopped-by-release`에
   `stopped`를 기록한다. 헬스체크·doctor는 정확한 표식이 있고 서비스가 inactive일 때
   `PASS`와 “릴리스로 정지됨(정상, stopped by release — expected)” 안내를 낸다.
   표식 없이 inactive이거나 표식이 깨졌으면 기존처럼 고장이다.
   독립 세대 검사도 표식이 있으면 `stopped-by-release`로 정상 판정한다.
4. **복구:** 호스트의 스위치 파일을 삭제하고 다시 배포한다.
   배포기가 두 계정의 노드 표식만 지우고 `systemctl --user start hermes-gateway.service`를
   실행한다. 활성 상태·현재 릴리스 세대를 확인한 뒤 전량 재판정한다.
   서비스 파일·설정을 재설치하거나 enable할 필요가 없다.

영수증은 최상위 `hermes_gateways`에 `stopped` 또는 `running`을,
`surfaces.gateway_services`에 계정별 확인된 상태를 기록한다.
노드 표식은 배포기가 쓰고 지우는 운영 상태이며 호스트 스위치를 대신하는 승인 수단이 아니다.
정기 작업을 사용자 타이머로 옮긴 기능과 별개라, Hermes 게이트웨이가 수행하던 에이전트 모드 잡은
정지 중 실행되지 않는다.

## 헬스체크 허용 목록

게이트웨이 생존 프로브의 원격 명령에 표식 조회가 추가되므로 `--probe-hashes`와
래퍼 입력 지문이 바뀐다. 기존 `healthcheck-wrapper:<node>` 승인 표면·카드·자동 갱신 경로가
새 명령을 함께 승인하고 설치한다. `LIVE_CHECKS` 순서·이름과 수리 명령 매니페스트는 유지한다.

## 관련

- 호스트 스위치·표식 판독: `automation/hermes_gateways.py`
- 승인: `automation/release{.sh,_approval.py,_spec.py,_spec_message.py}`
- 배포·판정·영수증: `automation/deploy_all{.sh,.py,_probe.py,_observe.py,_gateway.py}`, `automation/deploy_receipt.py`
- 상시 점검: `automation/healthcheck_probes.sh`, `automation/doctor/`, `automation/gateway_generation.py`
- 회귀: `tests/unit/test_hermes_gateways_stop.py`, `test_gateway_stop_monitoring.py`
