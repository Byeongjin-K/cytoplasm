# doctor — 주 모델의 일시 한도는 고장으로 알리지 않는다

## 무엇을

`python3 -m automation.doctor` 의 「주 모델」 점검이 Hermes 로그인 상태 `exhausted`(사용 한도에 걸려
잠깐 쉬는 중)와 `dead`(토큰 폐기·만료 같은 영구 인증 실패)를 구분한다. 고장으로 알리고 다시 로그인하라고
하는 것은 `dead` 일 때뿐이다.

## 왜

2026-10-15 05:18 UTC 매시 점검이 agent 계정에서 「고장 · 주 모델 — 마지막 상태가 exhausted — 다시
로그인한다」를 보냈고, 06:17 점검에서야 「해결됨」이 나왔다. 실제로는 이랬다.

- 그 직전(04:55~04:57 UTC) 같은 계정에서 동시 12개로 돈 실험용 판정 호출 가운데 40건이 Sol 대신 폴백
  Grok 으로 답했다. Hermes 는 그때 Codex 자격에 `exhausted` 를 적는다.
- 혼자인 자격의 일시 한도는 Hermes 가 1분 뒤 다시 쓰고(길어야 1시간), 그동안 폴백이 받는다. 표시는 다음
  선택 때까지 남는다. auth.json 은 05:33:53 에 `ok` 로 돌아왔고, 게이트웨이는 05:49·06:14 소유자 메시지를
  Sol 로 처리했다.
- doctor 는 그 표시를 그대로 「로그인 고장」으로 읽었고, 매시 점검이라 해결 통지가 한 시간 늦었다.

다시 로그인해도 바뀌는 것이 없는 상태를 고장으로 보내면 소유자가 알림을 믿지 않게 된다.

## 사용 시나리오

- 실험이나 바쁜 시간대에 Codex 가 잠깐 한도에 걸린다. 쉬는 시간이 끝났거나 1시간 안에 끝나고, 로그인된
  폴백이 있으면 doctor 는 통과다(`주 모델 … 사용 한도로 잠깐 쉬는 중(약 N분 남음) — 그동안 폴백 … 가
  받는다`). 알림은 오지 않는다.
- ChatGPT 의 5시간·주간 한도처럼 공급자가 재개 시각을 1시간 넘게 뒤로 주거나, 남은 시간을 알 수 없거나,
  폴백이 없으면 「확인」으로 한 번 알린다. 문구는 다시 로그인할 필요가 없다고 말한다.
- 토큰이 폐기·만료돼 Hermes 가 `dead` 로 적으면 예전처럼 「고장 · 다시 로그인한다」와 로그인 절차가 나온다.

## 재발 확인

- 노드에서 `cd /srv/autophagy-agent-current && python3 -m automation.doctor` 의 `primary-model` 줄을 본다.
- 실험처럼 같은 계정으로 모델을 많이 부르는 일은 동시 호출을 낮춘다(2026-10-15 판정기 12 → 2). 계정
  자체를 나누려면 실험 전용 계정에 따로 로그인한다. 운영 계정의 auth.json 을 복사하면 OAuth 갱신 토큰이
  한쪽에서 바뀌어 다른 쪽이 끊기므로 복사하지 않는다.

## 관련

- 코드: `automation/doctor/capabilities.py` `_primary`·`_cooldown`, `automation/doctor/facts.py` `_cooldowns`
- 회귀: `tests/unit/test_doctor_primary_cooldown.py`
- 가이드: [연결·승인 점검](../guide/연결-승인-점검.md) §2
- 판정 근거(Hermes): `agent/credential_pool.py` 의 `STATUS_EXHAUSTED`·`STATUS_DEAD`·`_exhausted_ttl`
