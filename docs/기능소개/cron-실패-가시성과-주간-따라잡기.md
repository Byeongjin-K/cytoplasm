# cron 실패 가시성 + 주간 따라잡기 — 스트릭 통지와 delivered-week 워터마크

**완료:** 2026-08-24 · **계획:** `.omo/plans/cron-error-remediation.md` · **대상:** `notes-weekly-organize` · `budget-watch`

> 이력: 당시 대상이던 노드 주간 연구 동향 작업은 이후 노드에서 제거됐다. 주간 연구동향 보고서와 키워드는 공도리가 맡는다.

## 무엇을

no-agent cron 워처 세 개가 mail 워처들이 쓰던 공유 헬퍼 `watch_failure_streak`를 그대로 채택했다.
건강한 tick은 예전처럼 무음이고, **연속 실패가 임계치에 닿는 그 tick에 1줄**, 복구되는 tick에 다시
1줄만 말한다(주간 워처 임계치 1, `budget-watch` 3). 여기에 주간 리포트 두 개는 `delivered-week`
ISO 주 워터마크를 얻어 **평일 매일 시도하되 발송은 주 1회**가 됐다 — 월요일에 실패해도 화요일이
그 주를 대신 채운다.

## 왜

2026-08 실측이 세 번 같은 모양으로 반복됐다.

- `notes-weekly-organize`: 07-20, 08-17 주간 작업이 나가지 않았다. 스케줄이 월요일 1회뿐이라 그 하루가
  실패하면 그 주는 그대로 비었다.
- `budget-watch`: 08-23 23:30 Sheets 503. 이쪽은 다음 tick에 스스로 나았다 — 같은 실패라도
  "기다리면 낫는 것"과 "사람이 고쳐야 하는 것"이 섞여 있다는 증거다.

두 문제가 겹쳐 있었다. 하나는 **실패가 아무 데도 보이지 않는다**는 것, 다른 하나는 주간 작업이
**단 한 번의 기회만 갖는다**는 것이다. 임계치 통지가 앞을, 워터마크가 뒤를 담당한다.

## 어떻게 (구조)

- **스트릭 통지**: 워처가 `watch_failure_streak.record(name, ok=…, threshold=…)`을 호출하고 돌아온
  줄만 stdout에 낸다. 상태는 워처 이름별 파일이라 서로의 인시던트를 공유하지 않는다. 통지 경로가
  깨져도(상태 쓰기 불가, 헬퍼 미배포) tick의 성패 판정은 절대 바뀌지 않는다.
- **임계치 근거**: 주간 워처는 1 — 실패 한 번이 곧 그 주 산출물이다. `*/30`인 budget은 3(약
  1.5시간) — 일시적 503을 통지로 바꾸지 않는다.
- **주간 따라잡기**: 스케줄을 `0 8 * * 1-5`(notes)로 넓히고, 성공한 발송만
  현재 ISO 주를 소진한다. 이미 소진된 주의 tick은 아무 일도 하지 않는 무음 성공이다.
- **배포**: 세 `deploy.sh`가 공유 헬퍼를 provenance 검사를 거쳐 `~/.hermes/scripts/`로 함께 올리고,
  기존 cron job의 스케줄과 전달 대상을 `hermes cron edit`으로 제자리 수렴시킨다.

## 사용 시나리오

- **정상(happy path)**: 월요일 08:00, `notes-weekly-organize`가 주간 정리를 마치고 그 주를
  소진한다. 화~금 tick은 워터마크를 보고 즉시 조용히 끝난다 — 소유자 쪽에서 보이는 변화는 없다.
- **따라잡기**: 월요일 작업이 실패한다. 워터마크는 전진하지 않았으므로 화요일
  08:00 tick이 같은 주를 대신 채운다. 예전이라면 그 주는 빈 주였다.
- **실패 경로**: `budget-watch`가 세 tick 연속 실패하면 `budget-watch failed 3 ticks in a row: …`
  한 줄이 나오고, 그 뒤로는 고장이 이어져도 다시 말하지 않는다. 회복되는 첫 tick에
  `budget-watch recovered after N consecutive failures` 한 줄로 닫는다. 오류 문구의 메일 주소와
  긴 숫자는 마스킹된다.

## 알아둘 것 — 이 줄이 지금 닿는 곳

세 job 모두 mail 선례처럼 `--deliver discord`로 수렴하므로 임계치·복구 통지는 소유자에게 닿는다.
전환 순서는 의도적이다. 먼저 평범한 실패 tick의 stdout을 없애고 마스킹한 상세를 사고 개시 notice
안으로 옮긴 뒤 전달 대상을 바꿨다. 따라서 `budget-watch`가 계속 실패해도 30분마다 DM하지 않고,
3번째 실패의 개시 1건과 처음 회복한 tick의 종료 1건만 보낸다. 자식 stdout/stderr 및
`RETRY-RESOLVED` 같은 성공 출력도 래퍼가 전달하지 않는다.

## 관련

- 코드: `automation/notes_organize/notes_organize.py` ·
  `skills/budget/scripts/budget_watch.py` · 공유 헬퍼 `skills/mail/scripts/watch_failure_streak.py`
- 배포: 위 셋의 `deploy.sh`(헬퍼 동반 배포 + 스케줄 수렴)
- 테스트: `tests/unit/test_notes_organize_watermark_streak.py` ·
  `tests/unit/test_budget_watch_streak.py` · `tests/unit/test_budget_retry_queue_consumption.py`
- 규약: `docs/guide/watcher-cron-설계규약.md` (m) 연속 실패 통지, (f) 성공 이후 상태 마킹
- 승인·게이트: 통지는 소유자 데이터를 바꾸지 않는 진단 출력이라 별도 승인 게이트를 두지 않는다.
  budget의 메일 발송 승인 게이트는 그대로다.
