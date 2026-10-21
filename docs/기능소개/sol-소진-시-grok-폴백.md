# Sol 이 답하지 못하면 Grok 으로 넘어간다

## 무엇을

모든 모델 호출(Discord 대화와 배치 자동화)은 지금처럼 `openai-codex/gpt-5.6-sol` 로 먼저 나간다.
Sol 이 답하지 못하면(쿼터 소진·레이트리밋·인증·전송 실패) Hermes 내장 `fallback_providers` 가
같은 호출을 xAI `grok-4.7`(SuperGrok 구독 OAuth, provider `xai-oauth`)로 넘긴다. 체인 전체가
실패할 때만 에러가 호출자에게 전파된다.

## 왜

2026-09-04 부터 모델 경로는 Codex 하나뿐이었고, 배치 클라이언트(`automation/codex_llm.py`)는
`--ignore-user-config` 로 Hermes 폴백을 **일부러** 막았다. 그래서 Sol 주간 한도가 차면 메일 요약·
회의록·lifelog 추출·대화가 한꺼번에 멈췄다. 소유자가 2026-09-22 "Sol 이 다 되면 Grok 을 쓰라"고
결정했고, 특허 민감 내용도 Grok 으로 보내도 된다고 정했다.

## 어떻게 동작하나

- **폴백 목록은 한 곳뿐**: 에이전트 계정 `~/.hermes/config.yaml` 의 `fallback_providers`.
  게이트웨이와 배치가 같은 목록을 읽는다 — 배치 호출이 더 이상 `--ignore-user-config` 를
  넘기지 않기 때문이다. 폴백을 바꾸거나 끄려면 그 목록만 고치면 된다(`[]` 이면 예전과 같다).
- **주 경로는 그대로 고정**: 배치 argv 는 여전히 `--provider openai-codex -m gpt-5.6-sol` 이다.
  폴백은 호출(배치)·턴(대화) 단위이고, 다음 호출은 다시 Sol 부터 시도한다.
- **민감도 게이트는 그대로**: 결정적 분류가 모든 호출 앞에 있다. 달라진 것은 patent-sensitive
  가 허용되는 경로가 "Codex 만"에서 "공용 Hermes 경로(Codex + 설정된 폴백)"로 넓어진 것이다.
  공용 클라이언트 밖의 경로는 여전히 호출 전에 거부된다(doctype `PatentRoutingError` 등).
- **로그가 실제로 답한 모델을 적는다**: 메일·doctype·특허 초안·제안서·보고서의
  `llm-calls.jsonl` 은 요청한 주 경로(`provider`·`model`) 옆에 Hermes 사용량 보고서에서 읽은
  `served_provider`·`served_model` 을 싣는다. Grok 이 답했으면 `xai-oauth`·`grok-4.7` 이 남으므로
  특허 민감 본문이 어디로 갔는지 로그만으로 확인할 수 있다. 보고서가 없으면 `unknown` 이다.

## 사용 시나리오

- **정상**: Sol 한도가 남아 있다 → 모든 호출이 Sol 로 나가고 아무것도 달라지지 않는다.
- **Sol 소진**: 주간 한도 429 → Hermes 가 같은 프롬프트를 `grok-4.7` 로 보내고 답을 돌려준다.
  메일 다이제스트·회의록이 멈추지 않는다.
- **Grok 로그인 없음**: 폴백도 인증 실패 → 예전과 같이 Sol 의 에러가 호출자에게 전파된다
  (fail-closed). 노드에서 agent 계정으로 `hermes auth add xai-oauth` 로 로그인하면 풀린다.

## 소유자 몫

- 노드 agent 계정에서 xAI Grok OAuth 로그인: `hermes auth add xai-oauth` (브라우저 로그인).
- 확인: `hermes fallback list` 가 `xai-oauth / grok-4.7` 을 보여야 한다.

## 관련

- 정책: `configs/routing-policy.md`, `configs/sensitivity-rules.yaml`
- 코드: `automation/codex_llm.py`(배치 공용 클라이언트), `skills/doctype/scripts/doctype_llm.py`(경로 가드),
  `automation/provision-agent.sh`(신규 설치 config 시드)
- 승인·게이트: 없음 — 모델 경로 변경이며 외부효과 게이트는 그대로다.
