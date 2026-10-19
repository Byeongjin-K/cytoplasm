# 개인 고유명사 preflight 계약

**상태:** Todo·Calendar·Mail 외부 쓰기 연결 완료. Mail은 기존 승인 게이트 바로 앞에서 같은
`guarded_write(...)`를 통과하며, Gmail 승인 snapshot도 정규화된 수신자·제목·본문으로 다시 만든다.

## 무엇을

Todo·메일·캘린더에 쓰기 전 개인 이름·조직·장소 같은 고유명사를 personal RAG, Hermes memory,
기관 주소록 후보와 대조하는 공통 계약이다. 단일 고신뢰 후보는 자동 선택하고, 충돌·저신뢰일 때만
**같은 대화에서** 소유자에게 되묻는다.

## 왜

“아이 기관 행사 추가”처럼 개인 관계를 알아야 하는 요청을 추측으로 쓰면 잘못된 수신자·일정·할 일이
생길 수 있다. 반대로 매번 확인하면 자동화 가치가 낮아진다. 출처별 confidence와 추적 가능한 정책으로
안전한 자동 선택 범위를 명시한다. 모호할 때만 되묻는데, 이때 승인 DM을 열면 소유자가 다른 화면으로
가서 리액션을 달아야 하므로, 단순한 되물음은 **대화 내 clarify**로 처리한다(승인 레코드 미생성).

## 사용 시나리오

- 성공: 합성 person mention → personal RAG에서 기관 후보 1개(유효 confidence ≥0.85) → 자동
  정규화 → 기존 owner-confirm write → Todo·Calendar는 외부 API 재조회, Mail은 기존 sender의
  verified-response 계약 일치 → `VERIFIED`. Todo는
  `todo_preflight.create_task`, Calendar는 `calendar_preflight.guarded_execute_draft`, Mail은
  `mail_preflight.guarded_execute_draft`가 이 경계를 소유한다. Mail의 Gmail snapshot은 정규화 뒤에
  다시 만들므로, 최종 수신자·제목·본문만 기존 승인 게이트에 전달된다.
- 충돌: 주소록 contacts와 organization이 서로 다른 기관을 반환 → 쓰기 0건. CLI가 `ENTITY-CLARIFY`
  한 번 + 후보·근거를 출력하고 non-zero로 종료한다. 소유자는 그 대화에서 바로 답하면 된다 —
  승인 메시지도, 리액션 대기도, 새 watcher도 없다.
- 미검출: 고유명사 없는 일반 할 일 → preflight 확인 없이 기존 Tasks 게이트로 통과한다.

원문·후보 값은 mode-700/600 private 감사 저장소에만 저장한다. 일반 로그에는 유형·개수·결정·원문
hash만 남긴다. clarify 텍스트는 소유자 본인에게만 가므로 후보 표시값을 포함하지만, private
`source_ref`와 원문 전체는 넣지 않는다.

## 관련

- 계약/정책: `automation/entity_preflight/`
- 임계값(단일 위치): `configs/entity-preflight.json` — 계약은 `policy.POLICY_SEED_PATH`로 이를 노출
- 상세 호출 흐름: `docs/guide/personal-entity-preflight.md`
- 공통 가드: `automation/entity_preflight/gate.py`
- Todo 연결: `skills/todo/scripts/todo_cli.py` → `todo_preflight.create_task`
- Calendar 연결: `skills/calendar/scripts/calendar_cli.py` → `calendar_preflight.guarded_execute_draft`
- Mail 연결: `skills/mail/scripts/triage_cli.py` → `mail_preflight.guarded_execute_draft` → 기존
  `triage_gate.execute_draft`
- 테스트: `tests/unit/test_entity_preflight_gate.py` · `tests/unit/test_entity_preflight_contract.py` ·
  `tests/unit/test_mail_preflight.py`
- 증적: `docs/qa/RTS-2/ef5-gate.txt` · `docs/qa/RTS-3/ef5b-mail-preflight.txt`
- 승인 범위: dori-gate가 릴리스·계정 밖 경로를 카드로 분류한다. `automation/interop/effect_approval.py`는 기존 카드 검증을 보존한다. 계정 내부 쓰기는 소유자 Discord 명령을 확인한 뒤 카드 없이 처리한다.
- clarify 선례: `skills/calendar/scripts/calendar_cli.py`의 `ROUTING-CLARIFY` exit
