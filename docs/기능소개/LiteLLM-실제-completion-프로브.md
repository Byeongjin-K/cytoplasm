# LiteLLM 실제 completion 프로브

> **과거 기록 · 폐기(2026-10-19)**: 소유자 "이거 아예 삭제해 litellm" 결정으로
> 이 프로브·게이트웨이를 설치하거나 다시 켜지 않는다. 아래는 2026-09-03의 구현 이력이다.
> 현재 제거·백업·원장 복구는 [LiteLLM 지우기](litellm-지우기.md)에 있다.

## 무엇을 확인하는가

healthcheck는 기존 `/health/liveliness` 확인을 유지하면서, `glm-main` 별칭으로 실제 `/chat/completions` 요청을 한 번 더 보낸다. 요청은 사용자 메시지 `ping`과 `max_tokens: 1`로 제한된다. HTTP 200 응답에 `choices[0]`이 있을 때만 PASS이며, 그 밖의 HTTP 상태나 응답 형태는 FAIL이다. 실패 로그에는 HTTP 상태와 응답의 오류 type/code만 남고 응답 본문과 인증 키는 남지 않는다.

## 왜 필요한가

2026-09-03 상위 제공자의 요청이 429로 거부된 동안에도 LiteLLM의 liveliness는 계속 200을 반환했다. 그 결과 실제 completion 장애가 11시간 동안 healthcheck와 수리 티켓 경로에서 침묵했다. 프로세스 생존 여부와 실제 모델 제공 가능 여부를 분리해 확인해야 같은 유형의 장애가 즉시 드러난다.

## 사용 시나리오

- 정상: liveliness와 실제 completion이 각각 PASS로 보고된다.
- 상위 제공자 429 또는 잔액 소진: liveliness는 PASS일 수 있지만 completion은 FAIL로 보고되고 수리 티켓이 생성된다.
- HTTP 200이지만 `choices[0]`이 없음: completion은 FAIL로 보고된다.

## 과거 소유자 설치 단계

당시에는 강제명령 래퍼 갱신이 별도로 필요했다. 폐기 후에는 설치하지 않는다.

## 비용

30분 틱 기준으로 실제 completion 요청은 하루 48건이며, 요청당 출력은 최대 1토큰이다.

## 검토 항목

LiteLLM alerting의 outage 유형과 이 실패 신호를 연동하는 작업은 이번 변경에서 보류한다.
