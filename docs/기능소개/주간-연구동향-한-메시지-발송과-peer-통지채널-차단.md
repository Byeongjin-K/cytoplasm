# peer 통지채널 차단 프로브

> 주간 연구동향 보고서와 키워드는 공도리가 맡는다. 이 문서가 처음 함께 다루던 주간 연구 동향 한 메시지 발송은 그 작업과 함께 노드에서
> 제거됐다([노드에서 완전 제거](주간-연구동향-노드-예약-은퇴.md)). 아래는 남은 peer 통지채널 차단만 다룬다.

## 무엇을

healthcheck 프로브 `peer_ignored_channels` 가 peer 게이트웨이의 `discord.ignored_channels` 에
`#approvals` 뿐 아니라 **`#notifications` 도** 들어 있어야 PASS 한다(peer 채널 디렉터리에 그 채널이 보일 때).

## 왜

2026-09-21 실측: 같은 통지 채널을 수신하던 **peer LLM 게이트웨이가 통지마다 스레드를 열어**
「No home channel is set for Discord…」 nag 와 논평을 남겼다. 2026-09-05 에 `#approvals` 에서
닫은 것과 같은 병이다. nag 는 Hermes 가 새 세션마다 한 번 내는 안내라 스레드가 생길 때마다 반복됐고,
peer 계정에는 home channel 이 없었다.

## 사용 시나리오

- **정상**: peer config 에 두 채널이 모두 있으면 PASS, 통지에 peer 스레드가 붙지 않는다.
- **peer 설정 드리프트**: config 가 재생성돼 `#notifications` 가 빠지면 healthcheck 가
  `PEER-IGNORED-CHANNELS-MISSING` + `PEER-IGNORED-CHANNELS-RECOVERY` 로 FAIL 한다.
  `#notifications` 가 없는 설치(레거시 DM)는 `#approvals` 만으로 PASS 한다. 두 채널 이름이
  중복이면 fail-closed(`INVALID`).

## 소유자 절차 (노드, 1회)

1. peer `~/.hermes/config.yaml` 최상위 `discord.ignored_channels` 에 `#notifications` id 를
   더한다(`"<approvals id>,<notifications id>"`).
2. peer `~/.hermes/.env` 에 `DISCORD_HOME_CHANNEL=<채널 id>` 를 둔다. home channel 이 없으면
   Hermes 가 새 세션마다 `/sethome` nag 를 낸다.
3. 게이트웨이 쌍(agent·peer) 재시동(`docs/guide/operations.md` §2).
4. `automation/healthcheck.sh` 의 `peer_ignored_channels` 가 PASS 인지, 다음 통지에 peer
   스레드가 붙지 않는지 확인한다.

## 관련

- `automation/peer_gateway_probe.sh` · `automation/owner_notice.py`
- 회귀: `tests/unit/test_healthcheck_peer_gateway_probe.py`
- [peer 게이트웨이 승인채널 차단 프로브](peer-게이트웨이-승인채널-차단-프로브.md) ·
  [오너 통지 단일 채널](오너-통지-단일-채널.md)
