# entity-preflight 품질지표와 로그 보존

> 이력: 이 지표를 싣던 노드 주간 연구 동향 작업은 이후 노드에서 제거됐다. 주간 연구동향 보고서와 키워드는 공도리가 맡는다. 아래 주간 배선은 그와 함께 사라졌고, 집계·보존 모듈만 남았다.

## 무엇을

당시 월요일 09:00 KST 주간 보고서에 `entity-preflight` 품질 지표와 임계값 경보를 함께 싣는다. 같은 실행에서 감사 로그를 주간 단위로 회전하고, 개인 원문이 있는 private 감사 로그는 30일, PII가 없는 operational 로그는 180일 정책을 적용한다.

## 왜

가드는 모호한 고유명사를 계속 fail-closed로 막았지만, 집계 함수가 자동으로 호출되지 않아 품질 저하와 우회 징후를 사람이 수동 조회해야만 알 수 있었다. 두 JSONL도 append-only라 디스크 사용량과 개인정보 보존기간이 무제한으로 늘어났다. 새 watcher·cron·dashboard를 만들지 않고 이미 운영 중인 주간 보고서에 배선해 이 두 공백을 닫았다.

## 사용 시나리오

### 정상 경로

1. 월요일 주간 보고 작업이 일정대로 실행됐다.
2. operational JSONL의 PII-free `GateQualityRecord`만 복원해 자동 정규화율·clarify 요청률·미해결률·검증 실패율·p95 지연·우회 건수를 집계한다.
3. 추적 정책 임계값을 넘은 항목은 같은 보고서의 `entity-preflight 품질 지표` JSON 섹션에 경보로 표시된다.
4. private/operational 활성 로그는 timestamped `0600` 보관본으로 회전되고 각각 30일/180일보다 오래된 보관본이 제거된다.

### 실패·거부 경로

- operational 로그가 손상돼 디코딩할 수 없으면 품질 집계는 조용히 추측하지 않고 실패한다.
- private 감사 로그의 원문·mention span·후보 값은 품질 집계가 열거나 읽지 않는다. 회전 경로는 파일을 읽지 않고 잠금 아래 이름 변경·권한 고정·`fsync`만 수행한다.
- 별도 주기 실행기는 생기지 않았다. 연구 주제가 없어 기존 보고서가 생성되지 않는 실행에서는 회전과 집계도 실행되지 않는다.

## 관련

- 품질 집계·경보·operational 로더: `automation/entity_preflight/gate_metrics.py`
- 단일 보존정책·회전: `automation/entity_preflight/audit.py`
- 정책 결정: `.omo/plans/parallel-followup-sweep.md` D2 / G6
- 회귀: `tests/unit/test_entity_preflight_{weekly_metrics,retention}.py`

승인 게이트는 관여하지 않는다. 이 기능은 관측·로컬 로그 보존만 수행하며 외부 쓰기 판정은 기존 entity-preflight 계약 그대로다.
