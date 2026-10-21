# 후속 과제

> **이 저장소가 지금 손댈 수 있는 열린 작업만** 남긴다. 소유자·노드에서만 닫히는 것, 동결·벤더에 막힌 것,
> 조건이 충족되기 전에는 조치하지 않는 것, 이미 닫힌 것은 전부 [follow-ups-deferred.md](follow-ups-deferred.md) 로 옮겼다.
> 현황판은 [features.md](features.md), 완료 기능은 [done.md](done.md).

> 기능 단위 묶음 항목으로, 불릿마다 "문제 → 조치" + 영향 범위·심각도를 적는다. 상세 규칙: 루트 `AGENTS.md`「후속 과제 기록 규칙」.
> **2026-08-26 분리**: 열린 105건을 전수 재판정해 84건을 보류 문서로 옮기고, 저장소에서 고칠 수 있는 16건은 실제로 고쳤다.
> 문서가 줄지 않던 기계적 원인은 회계 가드였다 — `tests/unit/test_features_board_conformance.py` 의 A9 가 FS3 baseline
> (`4716602d`)의 불릿 삭제를 막는다. 그래서 **삭제가 아니라 이동**으로 처리하고, 가드가 두 문서를 합쳐 읽도록 고쳤다.
> 원 묶음 `##` 헤딩을 양쪽에서 그대로 유지하는 것이 그 가드의 대조 키다.

## 주간 연구 동향 노드 제거 후 남긴 것 (2026-10-21)

- **낮음 · report 스킬 Drive 묶음 이름이 `주간연구동향`이다**: 요청형 보고서(`skills/report/scripts/report_cli.py`)가 `autophagy/주간동향/<연도>/<주>_주간연구동향/`에 올라가, 공도리가 맡는 주간 연구동향 보고서와 이름이 겹친다 → 형 Drive의 기존 폴더를 옮길지 함께 정해 묶음 이름을 `주간보고` 같은 이름으로 바꾼다(`drive_taxonomy`·`test_report_skill.py`·`test_drive_outputs.py` 동시 수정). 영향: 이름 혼동만, 동작 정상 · 심각도 낮음.

## 기관메일 수집 시간 초과 수리에서 남긴 것 (2026-10-21)

- **중간 · 보낸메일함 32통을 틱마다 다시 읽는다**: `mailon/main.py` `_sync_folder` 는 받은메일함에 이미 기록된 uid 를 보낸메일함 건너뛰기 목록에 넣지 않아(uid 가 전역 키), 같은 32통의 본문·첨부를 매 틱 다시 받고 마크다운을 다시 쓴다(첨부 하나는 시도 139번) → 첨부는 이번 재시도 간격으로 멈췄지만, 메일 자체를 다시 읽지 않게 하려면 `(folder, uid)` 복합 키 이전 또는 다른 폴더 기록 uid 를 건너뛰기 목록에 넣는 방법을 정한다. 영향: 틱마다 10초 안팎 낭비 · 심각도 중.
- **낮음 · 수집과 첨부 복구가 같은 파일을 동시에 받을 수 있다**: 수집 틱과 `mail_attachment_retry.py`(30분 틱)가 같은 첨부를 같은 `.part` 경로에 동시에 쓸 수 있다 → 복구 쪽이 수집 잠금(`mail-sync.lock`)을 잡거나 `.part` 이름에 pid 를 붙인다. 영향: 드물게 깨진 조각, 최종 파일은 크기 확인 없이 `ok` 가 될 수 있음 · 심각도 낮음.

## 문서 글자 읽기(OCR) 연결 후속 (2026-10-21)

- **중간 · 개인 RAG 는 markdown 만**: `automation/rag_ingest/sources/files.py` 는 `*.md` 만 훑는다 → pdf·hwp 참고자료를 RAG 에 넣으려면 소스에서 `extract_document` 로 본문을 만든 뒤 같은 청크 경로로 넣는다(기본 꺼짐 소스 설정).
- **낮음 · 글자 읽기 중계기는 이 저장소 밖 설치**: 노드의 OCR 은 운영자 계정 사용자 서비스 `dori-ocr-relay`(받는 계정 목록에 agent 포함)와 GPU 큐 작업에 기대며, 이 저장소의 설치·배포기는 그것을 만들지 않는다 → 새 노드나 운영자 호스트 이전 때는 스위치를 켜기 전에 중계기·받는 계정 목록을 먼저 갖추고, 없으면 첨부 읽기가 30분 뒤 "글자 읽기로도 본문을 얻지 못했습니다"로 끝난다(동작 실패이지 데이터 유출은 아님).

## 캘린더 기본색 개인 일정 예외 (2026-10-20)

- **중간 · 개인 일정 예외(기본색 유지)**: 지금 기본색은 생성 경로 전체에 적용되고 개인·업무 분류기는 없다 → 업무 메일 digest/card에서 노드가 만드는 일정만 세이지로, 개인 일정(기일·생일·모임·병원·미용)은 기본색(기본 파랑)을 유지하도록 후속으로 구분한다. 혼용 노드는 예외 구현 전 전역 기본색을 켜지 않는다.

## v2.6.6 노드 전환 묘비 정리 (2026-10-20, 이슈 #150)

- **낮음 · 호환 묘비 제거**: v2.6.6 노드의 옛 `pin_import_root.sh` 가 은퇴 파일 4개를 요구해
  `automation/interop/` 에 묘비(`external_effect_gate.py`·`policy_paths.py`·`plugin_shim/`·`hermes_plugin/`)를
  두었다 → v2.6.7 이전 세대에 머문 노드가 없고 각 노드의 `retired-interop-v1` 정리(드롭인 제거)가 끝난 것을
  확인한 뒤 묘비와 `tests/unit/fixtures/legacy_v2_6_6/`·관련 시험을 함께 지운다.

## v2.6.7 승인 스레드 정리 후속 (2026-10-19)

- **낮음 · general RequestThread F1**: 비메일 생산자(외부 초대가 아닌 calendar·budget)는 재사용한 소유자 스레드에 카드를 게시해도 결정 접두어를 제거하지 않는다 → 각 카드 POST 직후 `origin_notice.resume_thread`/`rename_thread`를 호출한다. 영향은 낮음: 실제로 관측된 재사용은 메일뿐이며, v2.6.7에서는 메일 경로를 수정했다.

## 최소 감시 CI에서 확인한 기존 배포 진단 누락 (2026-10-18)

- **낮음 · 배포 거부 진단, CI 차단**: `automation/legacy_services/deploy.sh`와
  `automation/omo_relay/deploy.sh`는 호스트 미지정 시 SSH 전에 exit 3으로 멈추지만
  표준 `DEPLOY-BLOCK` 진단을 출력하지 않는다. 기준 커밋과 동일한 두 파일에서
  `test_deploy_host_fail_closed_all`의 해당 두 사례가 실패한다. 실행·승인 우회 문제는
  아니다. 조치: 기존 배포기와 같은 호스트 미지정 진단을 출력하고 전수 회귀를 통과시킨다.
  최소 감시 변경 범위 밖이라 이 작업에서는 배포기를 고치지 않았다.
## 기관메일 10분 수집 착지 후 남긴 것 (2026-10-19)
- MailOn 로그인 세션 재사용(쿠키 저장)으로 로그인 횟수 줄이기 — 지금은 실행마다 새 agent-browser 세션을 만들고 닫을 때 프로필을 지워 쿠키가 남지 않는다(`skills/mail/vendor/mailon/browser.py` `unique_session_name`·`ephemeral`). 재사용하려면 수집 전용 고정 프로필 + 세션 만료 감지 + 발송 세션과의 격리를 새로 설계해야 해서 이번 범위에서 뺐다. 영향: 10분 주기 하루 약 100번 로그인(전보다 8배). 조치: 1~2주 운영하며 MailOn 쪽 로그인 거부·추가 인증이 보이면 이 설계를 먼저, 5분 주기는 그 뒤.

## 기관메일 대용량 첨부 수집 고침 후 남긴 것 (2026-10-19)
- **중간 · 1KB 미만 '저장됨' 대용량 첨부**: 노드 state.db 에 네이버 대용량 메일 링크 82건, Mailplug 대용량 링크 40건이 1KB 미만 파일로 `ok` 기록돼 있다(만료 안내·리디렉트 HTML 로 추정, 이번 HTML 거부는 새로 받는 것만 막는다). 영향: 메일 목록에는 첨부가 있는 것처럼 보이고 Drive 에도 그 작은 파일이 올라갔을 수 있다. 조치: 저장 파일 머리가 HTML 이면 실패로 되돌리는 정리를 heal 에 더하고, 네이버 대용량 링크가 받는 방식(리디렉트·쿠키)을 확인한다.
- **낮음 · 다른 메일 서비스의 대용량 링크 미지원**: 다른 대학·기관 메일 서버와 카카오워크·다음 등의 대용량 링크(약 37건)는 웹메일 화면 안 내려받기가 다른 주소라 실패로 남는다. 영향: 그런 메일의 큰 첨부는 링크만 남는다(대부분 이미 만료). 조치: 서비스별로 로그인 없이 받을 수 있는지 확인해 대용량 호스트로 등록하고, 로그인을 요구하면 형 판단을 받는다.
- **보통 · 만료 링크 무한 재시도·형 미통지**: 보낸 쪽 대용량 링크가 만료되면(HTML "download has expired" 응답)
  `scraper.py` 재시도 조건(`attempts < max` 또는 대용량 링크)이 대용량 링크를 끝없이 다시 받아 10분마다 같은 실패를 내고,
  형에게는 저장되지 않은 첨부가 있다는 말이 가지 않는다 → 만료 HTML 응답을 영구 실패로 분류해 재시도를 멈추고
  기존 소진(exhausted) 경로로 한 번만 알린다. 2026-10-19 첫 치유 수집에서 3건이 이 상태였다.

## 자동 쓰기 공용 경로의 후속 연결 (2026-10-18, Q233)

- **중간 · 다음 릴리스 기능 확장**: 이번 자동 쓰기는 Plaud만 연결했다. Q233
  "6개 다 카드 없이 바로 + 하루 요약에 남기기"에 따라 calendar(소유자 본인 일정만)·todo·
  wiki·Obsidian 노트 요청·project board 제안·`effect_request drive-save`가
  `automation.auto_write.record(kind, Entry)`와 종류별 릴리스 스위치·21:30 요약을 재사용하도록
  연결한다. 공유·외부 초대·메일은 기존 승인 경계를 유지해야 한다. 현재 연결되지 않은 종류는
  스위치를 거부하므로 승인 우회는 없다. [Plaud 공용 기반 소개](기능소개/plaud-노트-자동-쓰기.md).

## 모든 요청 승인 경로 착지 후 남긴 것 (2026-10-17)

- **중간 · 도구 공백(승인 경로 아님)**: 형이 "메일이랑 카톡 올려"라고 했을 때 카카오톡 방(옥도리방) 게시는 오토파지에
  연결된 도구가 아예 없다. 공용 `effect_request command` 도 실행할 명령이 있어야 카드를 만들 수 있으므로 지금은
  "이 일을 할 도구가 없다"로 보고한다. 조치: 옥도리 쪽에 게시 API·CLI 가 생기면 그 명령을 `command` 카드로 받거나 전용
  경로를 만든다. 보안 문제 아님 — 외부효과는 계속 형 ✅ 뒤에만 나간다.
- **낮음 · 범용 명령 예외 판정은 낱말 기반**: `effect_request/policy.py` 의 형만 하는 일 목록은 명령·설명의 낱말로 판정한다.
  카드가 명령 전체를 보여 주고 ✅ 뒤에만 실행하므로 놓친 경우에도 승인 없이 나가지는 않는다. 조치: 실제 거절·통과 사례가
  쌓이면 목록을 보정한다.

## 업데이트 신뢰키 정체 경고 착지 후 남긴 것 (2026-10-16)

- **중간 · 외부 설치 기능 공백**: 자동 수렴(`release_rollback.apply_release_update`)은 코드 트리 교체·게이트웨이 재시작·스모크까지만
  하고, 홈 패키지 선언의 v2 `cron`·`file` 행(프로젝트 대시보드 cron 2개·사용자 유닛 등)은 소유자 워크스테이션의
  `deploy_all.sh` 만 놓는다. 그래서 제3자 서버는 최신 릴리스로 따라잡아도 새 홈 패키지 기능이 켜지지 않는다.
  이번에는 대시보드 수동 절차를 install.md 에 적었다. 조치: 노드 로컬에서 v2 홈 행을 수렴하는 경로(설치기 opt-in 컴포넌트나
  agent 계정 리컨실러)를 설계한다 — 권한 경계(ops→agent)가 걸려 별도 설계가 필요하다. 보안 문제 아님.

## 메모리 선택 검증 중 확인한 기존 린트 경고 (2026-10-14)

- **낮음 · 진단 노이즈**: 전체 Ruff 검사는 통과하지만 기존 7곳의 `noqa` 문법 경고가 남는다
  (`tests/unit/test_gateway_pair.py:15`, `test_repair_report_reconcile.py:3`,
  `test_entity_preflight_resolver.py:39,56`, `test_release_rollback.py:53`,
  `automation/repair/repair_report_reconcile.py:101`, `automation/group_roster/cli.py:137`).
  해당 규칙이 실제로 필요한지 확인한 뒤 올바른 코드 목록으로 좁히거나 불필요한 주석을 제거한다.
  이번 메모리 선택 변경이 만든 경고는 아니며 실행·승인 동작과 무관하다.
  근거: `docs/qa/MEMCHOICES/verification.md`.

## 현재 열린 항목 없음 (2026-08-31 전수 처리)

열린 19건을 병렬 수리로 17건 해소, 2건 재판정(BLOCKED — 기술이전 render 진리표는 KD 확인 선행, G8 분할 잔여는 FS3 재생 핀·동결 소유)했다.
2026-08-31 오후에는 제안서 노드 자율 구동 3건과 릴리스 승인 자동 완결의 낡은 pending 회복 1건도 해소했다.
전 이력은 [follow-ups-deferred.md](follow-ups-deferred.md) 의 해소 기록·BLOCKED 절에 있다. 새 후속 과제는 루트 `AGENTS.md`「후속 과제 기록 규칙」대로 여기에 다시 쌓는다.

## 2026-09-03 전수 처리 (후속 과제 스윕 4)

열린 29건(10묶음)을 mass-ulw DAG 22노드로 병렬 처리했다 — 20건 해소, OWNER 2·BLOCKED 2(repair 동결)·OBSERVE 5 이관.
기관메일 Gmail 회신 인용은 코드 없이 실측으로 닫혔다(`gws gmail +reply` 가 원문을 인용한다). 이관 사유와 해소 근거는
[follow-ups-deferred.md](follow-ups-deferred.md) 의 각 `## 원 헤딩` 아래 `↳ 처리(2026-09-03)` 줄에 있다.
착지 중 새로 발견한 것은 아래 묶음에 쌓는다.

## 후속 과제 스윕 4 착지 후 남긴 것 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 화자 식별(voice catalog ③) 착지 후 남긴 것 (2026-09-18)

> ↳ 2026-09-19 kanbanfix 통합 정산: 3건 해소(PR #505·#508·#523), 3건 OBSERVE(노드 카탈로그가 등록 1명 그대로), 1건 OWNER(`--reprocess` 재전사) 이관. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## plaud 파이프라인 실동작 검증 착지 후 남긴 것 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 4건 해소(PR #405·#412·#414) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## provenance 가드의 남은 이스케이프 경로 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 해소(PR #404) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 릴리스 승인 카드가 peer 시야에 있다 (2026-09-05)

> ↳ 2026-09-05 A+C 로 해소(소유자 결정: 인터롭은 필요하므로 peer 게이트웨이는 유지, B 는 채택하지 않음) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## peer 자가 스킬이 승인 심사 절차를 저작했다 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 2건 해소(PR #408·#409) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래. 프로브 판독 권한은 새 OWNER 항목으로 남겼다.

## 완결 타이머와 세션 release.sh 의 태그 경합 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 4건 해소(PR #406·#407) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 수리 티켓 t_bd0d3789 후속 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-03 수리 스윕(메일 인용·다이제스트 GLM 폴백)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## LiteLLM GPT 전환과 헬스체크 정리 후 남긴 것 (2026-09-03)

> ↳ 2026-09-03 v1.1.0 세션에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래. 노드 래퍼 설치는 OWNER 항목으로 남았다.

## v1.1.0 편의 릴리스 착지 후 남긴 것 (2026-09-03)

> ↳ 2026-09-05 후속 과제 스윕 5에서 해소(PR #407) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 동결 해제·repair 재발 수리 착지 후 남긴 것 (2026-09-04)

> ↳ 2026-09-04 같은 세션에서 처리 — 수리 2건은 닫았고(상태 조회 전용 timeout · 새 카드가 이전 카드를 지목), 동결 4행에 걸린 1건은 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래 BLOCKED 로 옮겼다.

## 2026-09-04 plaud 구간 전사 수리 (t_4e3d6630) 잔여

> ↳ 2026-09-05 후속 과제 스윕 5에서 2건 해소(PR #410·#411) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래 해소 기록에 있다. 기존 빈 노트 upsert 승인은 그 문서의 OWNER 기록 그대로다.

## 용어 교정 문서 단계 이동 착지 후 남긴 것 (2026-09-05)

> ↳ 2026-09-05 후속 과제 스윕 5에서 OBSERVE 로 이관 — 새 본문을 쓰는 조건이 아직 성립하지 않았다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-05 전수 처리 (후속 과제 스윕 5)

시작 시 열린 15건(7묶음)을 전부 처리했다 — PR #404~#412·#414로 14건 해소, 조건 미성립 1건은 OBSERVE 이관.
불릿은 지우지 않고 원 헤딩·본문 그대로 [follow-ups-deferred.md](follow-ups-deferred.md) 에 옮겨 `↳ 처리(2026-09-05)` 줄에 PR 근거를 붙였다.
PR #413은 동시 `--apply` 회귀의 release FIFO를 O_RDWR로 유지해 CI 경합을 닫았다(기존 후속 불릿 없음).
새 OWNER 1건·OBSERVE 1건은 보류 문서에, 저장소에서 손댈 수 있는 새 3건만 아래에 남긴다.

## 후속 과제 스윕 5 착지 후 남긴 것 (2026-09-05)

> ↳ 2026-09-06 후속 과제 스윕 6에서 3건 해소(PR #417·#418·#419·#420·#421) — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래 해소 기록에 있다.

## 2026-09-05 수리 스윕-4 후 남긴 것

> ↳ 2026-09-06 후속 처리 세션에서 해소 — `release_complete.sh` 가 워크트리 동기화 직후 origin/main 의 자기 사본으로 exec 한다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-06 전수 처리 (후속 과제 스윕 6)

시작 시 열린 3건(1묶음)을 전부 해소했다 — 공개 반출 원장 경로순 정렬(PR #417), 200–250 LOC 구간 파일 분할(PR #418·#419·#421), 형제 import 편집기 진단(PR #420).
불릿은 지우지 않고 원 헤딩·본문 그대로 [follow-ups-deferred.md](follow-ups-deferred.md) 에 옮겨 `↳ 처리(2026-09-06)` 줄에 PR 근거를 붙였다.
스윕 5가 남긴 OWNER(peer 읽기 권한)는 PR #425로 저장소 쪽 설치 자산이 갖춰졌지만 기존 노드 설치는 소유자의 sudo 한 번이 아직 남아 OWNER 그대로이고, OBSERVE(화자 임계값)는 PR #422가 두 화자 확인 녹음으로 1.35를 검증해 닫았다.
기존 불릿 없이 발견한 결함 1건은 PR #424로 고쳤다 — 로컬 전사 노트 본문이 오프셋 없는 `get_file` UTC 를 현지 시각처럼 찍던 것. 이미 vault 에 쓰인 노트 3건의 보정은 OWNER 로 남겼다.
배포 관측 2건(deploy.sh 종료 코드와 최종 프로브의 불일치, 미선언 홈 산출물)은 보류 문서의 스윕 6 헤딩 아래 OWNER·OBSERVE 로 기록했다. 저장소에서 지금 손댈 열린 건은 없다. 증적: [docs/qa/FU6/summary.md](qa/FU6/summary.md).

## 2026-09-08 전수 처리 (후속 과제 스윕 7)

시작 시 열린 13건(7묶음)을 mass-ulw DAG 11노드(병렬 lane 10 + 검증 1)로 처리했다 — 9건 해소, 1건 부분 해소(계측 완료·실측 대기),
OWNER 2·OBSERVE 2 이관. 불릿은 지우지 않고 원 `##` 헤딩·본문 그대로 [follow-ups-deferred.md](follow-ups-deferred.md) 에 옮겨
`↳ 처리(2026-09-08)` 근거 줄을 붙였다. 보류 문서의 OBSERVE·BLOCKED 69건도 전수 재판정해 66 STILL-DEFERRED · 1 NOW-ACTIONABLE ·
2 ALREADY-RESOLVED 로 판정했고, 그 1건(승인 단일성 E2E 재평가, OBSERVE#5)은 이 스윕이 바로 그 「다음 승인 생명주기 작업」이라
근거와 함께 재판정해 보류 문서에 기록했다. 착지 중 발견한 1건만 아래에 남긴다.

## 후속 과제 스윕 7 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소: PR #508 이 완결 기능을 얹기 전에 SPLIT 커밋으로 CLI 책임을 나눴다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 라이프로그 전사·화자 품질 교정 착지 후 남긴 것 (2026-09-06)

> ↳ 2026-09-08 후속 과제 스윕 7에서 2건 해소(임계값 전제 재판정 · `text_of` 조립 판정) — 남은 1건(화자 상한 8)은 실측 선행이라 OBSERVE 로 이관했다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 전사 정확도 문서 공개 검사 잔여 (2026-09-07)

> ↳ 2026-09-08 후속 과제 스윕 7에서 해소 — 두 기능 소개 문서의 계정 홈 절대 경로를 설치별 자리표시자로 바꿨다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## mailon 런타임 드리프트 프로브의 수렴 안내 (2026-09-07)

> ↳ 2026-09-08 후속 과제 스윕 7에서 해소 — 프로브가 드리프트 방향을 판정해 런타임이 앞선 창에서는 `automation/release.sh` 를 안내한다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 스킬 편집기 import 해석 복구로 드러난 타입 부채 (2026-09-07)

> ↳ 2026-09-08 후속 과제 스윕 7에서 해소 — 지목된 9건이 진단에서 사라졌다(억제·설정 되돌림 없음). 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## cytoplasm 신규 노드 설치 보고에서 드러난 공백 (2026-09-07)

> 외부 설치자가 신규 설치기(`automation/install/`)로 처음 완주하며 보고한 6건 + 문서 공백 1건.
> 판정·우회·반영의 전문은 [신규 노드 설치에서 막히는 6곳](troubleshooting/신규-노드-설치-공백.md).
> **7건 전부 2026-09-07 에 해소했다** — 열린 항목 없음. 처리 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 라이프로그 화자 분리 정정 착지 후 남긴 것 (2026-09-07)

> ↳ 2026-09-08 후속 과제 스윕 7에서 1건 해소(재처리 노트 경로 고정), 2건은 OWNER·OBSERVE 로 이관했다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 설치 마법사·프로필 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-08 설치기 착지로 이관 — 원문·처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 제안서 엔진 내제화 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-08 후속 과제 스윕 7에서 1건 해소(샌드박스 사이드카), 엔진 공개 여부는 OWNER 로 이관했다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## healthcheck 래퍼 설치 자산의 non-root 운영자 결함 (2026-09-08)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #507): 하네스가 `--operator NAME` 을 받는다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## v1.6.0 릴리스 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-08 후속 과제 스윕 7에서 2건 모두 해소 — 승인 뒤 tip 이 전진한 릴리스를 감사형 abandon 으로 자가 회수하고, 죽은 카드에 상태 회신을 남긴다([소개](기능소개/릴리스-승인-자가-회수.md)). 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 수리 스윕 5 착지 후 남긴 것 (2026-09-08)

> ↳ 2026-09-09 정산 — 3건 해소(PR #466 converge stderr · #468 원장 헤더 · #467 `_reference` 드롭 관측),
> 프롬프트 v5 요약 산문 준수 1건은 관측 조건 미성립으로 OBSERVE 이관. 원문과 처리 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 토큰 없이 설치 완주 착지 후 남긴 것 (2026-09-09)

> **2건 전부 2026-09-09 에 해소했다** — 열린 항목 없음. 원문과 처리 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.
> 이 반영이 만든 소유자 몫 1건(래퍼 재프로비저닝)은 같은 문서의 OWNER 항목에 있다.

## 수리 티켓 유실 사고 수정 중 발견한 인접 결함 (2026-09-09)

> ↳ 2026-09-09 정책 결정으로 해소 — 결론은 **켜지 않는다**이고 대체된 선행 구현을 삭제했다. 원문과 판단 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 문서 검토 안내의 전송 경계 (2026-09-11)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #513): 검토 안내 3종이 통지 파사드로 나간다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 소유자 메시지 계약 착지 후 남긴 것 (2026-09-12)

> ↳ 2026-09-19 kanbanfix 통합 정산: 3건 해소(PR #500·#504·#514), footer·publish 접두부 2건은 OBSERVE 로, 분할기 단일화 1건은 동결 파일(`automation/deploy-skill.sh`) 때문에 BLOCKED(PR #502 열림, 소유자 해제 대기)로 이관. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 승인 요청 안내 메시지·비공개 표면 허용경로 착지 후 남긴 것 (2026-09-09)

> ↳ 2026-09-19 kanbanfix 통합 정산: 장문 승인 첨부 1건 해소(PR #511, 실게시 C2 는 배포 뒤), 옛 카드 재게시는 노드 확인 선행 OWNER, `VerifiedRoute` 확장은 범위 결정 OWNER 로 이관. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 캘린더 카드 누락 백스톱과 통지 라우팅 착지 후 남긴 것 (2026-09-10)

> ↳ 2026-09-11 두 건 모두 해소 — 단위 테스트 홈 격리(`tests/unit/conftest.py`)와 리마인더의 요청 스레드 배달로 닫혔다. 원문과 판단 근거는
> [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## Google Tasks 합성 과제 유입 수리 중 발견한 인접 결함 (2026-09-11)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #498): 네 테스트의 Drive 실행 경계 스텁, 변이 증명 포함. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 반출 릴리스 노트 안내 착지 후 남긴 것 (2026-09-13)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #498): `tests/unit` executionEnvironment 와 선언 가드. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 워킹트리 전량 커밋 착지 후 남긴 것 (2026-09-14)

> ↳ 2026-09-14 같은 날 해소(소유자 지시 「gitignore 에 넣으면 좋을 것들 점검」) — `.omo/senpi-task/` 를 무시하고 인덱스에서 내렸다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## plaud ogg 형식 사고 착지 후 남긴 것 (2026-09-16)

> ↳ 2026-09-19 kanbanfix 통합 정산에서 해소(PR #501): 재확인 오류는 `last_recheck_error` 로 분리된다. 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## 2026-09-19 전수 처리 (kanbanfix 통합 정산)

시작 시 열린 22건(9묶음)을 전부 판정했다: 13건 해소(PR #498·#500·#501·#504·#505·#507·#508·#511·#513·#514·#523), OBSERVE 5·OWNER 3·BLOCKED 1 이관.
불릿은 지우지 않고 원 `##` 헤딩·본문 그대로 [follow-ups-deferred.md](follow-ups-deferred.md) 에 옮겨 `↳ 처리(2026-09-19)`·`↳ 이관(2026-09-19)` 줄에 근거를 붙였다.
해소 13건은 **머지까지**다: 프로덕션은 `b8333c5c6`(v1.9.1) 그대로이고, 장문 승인 실게시(C2)·릴리스 카드 버튼(C6) 같은 실표면 확인은 다음 릴리스 뒤 몫이다.
분할기 단일화(PR #502)는 코드·CI 가 끝났으나 수정 동결된 `automation/deploy-skill.sh` 한 줄 때문에 소유자 해제를 기다린다.
회계 전문은 `.omo/evidence/kfx/active-closeout.md`. 저장소에서 지금 손댈 열린 건은 없다.

## 2026-09-20 라이브 마감 (v1.9.2 → v1.9.4)

v1.9.2 배포 영수증이 proposal 마운트 후 스모크 실패를 덮은 것을 실측으로 잡아 세 건을 고쳐 머지했다(PR #526 실패 전파, #527 CDN User-Agent, #528 마운트 상대 import). v1.9.3 에서 남은 시나리오 fixture import(PR #531)와 소유자 지시 두 건 — 승인 스레드의 일정 상세 표시(PR #530), 승인 카드 Discord 가독성 재설계(PR #532) — 를 v1.9.4 로 배포했고 `deploy_all.sh --verify` exit 0, 배포된 v4 일정 카드·장문 메일 첨부·proposal CLI 를 노드에서 read-back 했다. 수리 티켓 7건은 전부 done. 실측 증적은 `docs/qa/KFX-LIVE/summary.md`, 세션 원장은 `.omo/evidence/kfx/live-recovery-status.md`.

## 승인 카드 가독성 재설계 착지 후 남긴 것 (2026-09-21)

- **릴리스 카드 v5 의 참조 꼬리표가 비어 보인다** — `-# 참조: \`release:\`` 로 렌더된다(다른 생산자는 draft id 앞 8자). `automation/release_spec_message.py` 의 subject_key 가 빈 접미로 끝나서다 → 버전(`v1.9.4`) 이나 배포 기준 앞 12자를 참조로 싣는다. 저장된 v1~v4 는 불변, 새 v5 만. **영향: 표시 결함 · 승인 해시·바인딩 무관 · 심각도 낮음**.
  - ↳ [해소 2026-09-21] v1~v5를 byte-frozen으로 두고 v6 카드가 실제 첫 변경 상세 Discord 링크·버전·HEAD 앞 12자를 참조로 싣는다. guild 미상은 검색 안내로 저하한다.
- **release v5·skill deploy/publish v3 에 바이트 골든이 없다** — 일관성은 `bound()`/봉투 입력 핀으로만 잡혀 있어 문구가 조용히 바뀌어도 잡히지 않는다 → `tests/unit/mail_approval_card_golden.py` 와 같은 방식으로 세 카드의 새 버전 바이트를 고정한다. **영향: 회귀 탐지 공백 · 심각도 낮음**.
- **`automation/release_card.py:25` 가 이름 없는 `3` 을 쓴다** — 위 두 줄은 이름 상수를 쓰는데 폴백 버전만 리터럴이다 → 기존 상수 이름으로 맞춘다. **영향: 가독성 · 심각도 낮음**.
  - ↳ [해소 2026-09-21] v6→v5→v4→v3 폴백 순서를 모두 이름 상수로 고정했다.

## Grok 폴백 착지 후 남긴 것 (2026-09-22)

- **라우팅 로그가 실제로 답한 모델을 적지 않는다** — mail `triage_llm._log_call`·doctype `_log_call` 등 마스킹 라우팅 로그는 `provider=openai-codex` 를 그대로 적는다. Hermes 가 `fallback_providers`(`xai-oauth/grok-4.7`)로 넘긴 호출도 Codex 로 기록되므로 특허 민감 본문이 실제로 어느 제공자에게 갔는지 로그만으로는 감사할 수 없다 → 공용 클라이언트(`automation/codex_llm.py`)가 `hermes -z … --usage-file <tmp>` 의 `provider`·`model` 을 읽어 돌려주고 각 로그가 그 값을 싣는다. **영향: 감사 정확도만 · 라우팅·민감도 게이트·실행 결과 불변 · 심각도 낮음**.
  - ↳ [해소 2026-09-22] 공용 클라이언트에 `complete_served()` 를 더해 Hermes `--usage-file` 보고서의 provider·model 을 돌려주고, 메일·doctype·특허 초안·제안서·보고서·주간 연구 동향 로그 6곳이 `served_provider`·`served_model` 을 싣는다(기존 `provider`·`model` 은 요청한 주 경로 그대로). 보고서가 없으면 `unknown`. 회귀 `tests/unit/test_served_route_logging.py`.

## ASR 후보 평가 후 남긴 것 (2026-09-22)

- **용어집 힌트가 whisper 에 닿지 않는다** — 로컬 전사는 `-mc 0`(`SPEECHTOTEXT_WHISPER_CONTEXT` 기본)으로 돌고 whisper.cpp 는 `n_max_text_ctx > 0` 일 때만 prompt 를 붙여 `--prompt` 가 통째로 버려진다(힌트 유무 출력 바이트 동일, [STS2](qa/STS2/summary.md)). `--carry-initial-prompt`+`-mc 64` 로 강제하면 대리 기준 CER 14.2→19.2%(삭제 급증) → 소유자 교정 정답 3건 이상으로 다시 재서 힌트 경로를 걷어 낼지(문서·`asr_fingerprint` 정리) carry 로 살릴지 정한다. **영향: 없는 기능을 있다고 적은 문서 · 전사 결과 불변 · 심각도 중**.

## doctor 착지 후 남긴 것 (2026-09-29)

- **사용자 정의 주 모델의 연결은 확인하지 않는다** — `custom:<이름>`(peer 의 로컬 LiteLLM)은 `custom_providers` 선언만 보고 PASS 라, 프록시가 죽어도 doctor 는 통과로 적는다 → 온라인 검사에서 그 `base_url` 의 `/models` 를 GET 으로 한 번 부른다(키는 config 에서 읽되 출력하지 않는다). **영향: peer 모델 끊김의 조기 경보만 · 심각도 낮음**.

## 제안서 compose 중단 수리 중 발견한 인접 결함 (2026-09-29)

- **doctype·patent-prep 이 아직 `gpt-5.4` 를 요청해 전부 grok 폴백으로 샌다** — 구독이 `gpt-5.4` 를 더 내주지 않아 `hermes -z --provider openai-codex -m gpt-5.4` 는 `xai-oauth/grok-4.7` 이 답한다(노드 `--usage-file` 실측, `gpt-5.6-sol` 은 Codex 가 답함). `skills/doctype/scripts/doctype_llm.py` `CODEX_MODEL`, `skills/patent-prep/scripts/patent_routing.py` `CODEX_MODEL`(+`scenario.sh` 의 `model=gpt-5.4` 단언), 두 SKILL.md·`skills/report/SKILL.md`·`docs/guide/doctype-usage.md`·`docs/guide/onboarding-kit.md` 의 문구가 그 값이다 → 제안서처럼 공용 `automation/codex_llm.DEFAULT_MODEL` 과 같게 맞추고 동일성 테스트를 둔다(선례 `tests/unit/test_proposal_codex_model.py`). **영향: 두 스킬 호출이 느린 폴백으로 가고 '특허 민감은 Codex 전용' 문구가 사실과 다름(폴백 허용은 2026-09-22 소유자 결정이라 보안 위반은 아님) · 심각도 중**.
  - ↳ [해소 2026-09-29] 소유자 결정으로 모델을 코드에서 아예 없앴다 — 모든 호출이 `--provider`·`-m` 없이 계정 `~/.hermes/config.yaml` 의 주 모델·폴백을 따르고, `tests/unit/test_model_single_source_conformance.py` 가 코드 속 모델 이름을 막는다.

## 제안서 그림·캡션 겹침 수정 중 발견한 것 (2026-09-30)

- **소유자 뷰어(rhwp)에서 표가 쪽 아래로 넘쳐 잘리고, 그 뒤 그림이 쪽 밖으로 밀린다** — 글자처럼 취급하는 표의 셀 문단에 `linesegarray` 가 없으면 rhwp 는 행을 늘리지 않는다(rhwp 이슈 #7419). 굴착기 제안서 렌더를 rhwp 0.8.6 으로 재면 선행연구·KPI·추진일정 표가 있는 쪽의 글자 200여 개가 쪽 아래(y>1066px)에 그려지고, 이번 수정 뒤에는 그 쪽에 이어지는 그림 6이 쪽 밖(y=1363px)으로 밀려 보이지 않는다. 한/글은 행 높이를 다시 계산하므로 제출본에는 영향이 없다 → 표의 선언 행 높이를 내용으로 추정해 쓸지(`skills/proposal/engine/hwpx/AGENTS.md` 의 "행 높이는 한/글 몫" 규칙과 충돌하므로 소유자 결정 필요) rhwp 의 수정을 기다릴지 정한다. **영향: 소유자 VS Code 보기에서 표 일부·그림 1개가 안 보임 · 한/글 제출본 무관 · 심각도 중**.
- **시각 검토가 겹침을 잡지 못한다** — `visual-review`(`engine/hwpx/visual_preview.py`)는 브라우저 조판이라 그림이 글을 피해 흐르고 행이 늘어나므로, 한/글·rhwp 에서만 생기는 겹침·표 넘침을 한 번도 보여 주지 못했다(이번 결함이 두 달간 통과한 이유) → rhwp(`@rhwp/core`)로 쪽별 SVG 를 만들어 그림 사각형 안 본문 줄·캡션 줄 충돌·쪽 밖 글자를 세는 검사를 `visual-review` 에 더할지 정한다(노드에 bun/npm 의존이 새로 생긴다). 이번 판정 스크립트는 워크스테이션 임시 폴더에서 돌렸다. **영향: QA 공백 · 심각도 중**.

## v1.14 공개 반출 준비 중 발견한 것 (2026-09-30)

- **공개본의 제안서 문체 자산이 소유자 개인 파일 경로와 이름을 싣고 있다** — `skills/proposal/prompts/voice.md` 의 제목과 「근거와 한계」 출처 줄이 소유자 이름, 홈 아래 개인 문서·노트의 절대 경로와 파일 제목, sha256 을 적고 있고 `skills/proposal/scripts/proposal_prompts.py` 의 출처 주석도 개인 절대 경로다. 두 파일은 공개 반출 제외 원장에 없어 이미 공개본에 실려 있다(「개인화 코드 금지 규칙」의 개인 경로·이름). 이번 사이클은 `skills/proposal/SKILL.md`·`docs/features.md` 의 평가 점수·기준값 표현만 걷었다 → 출처 줄을 경로 없는 서술(문서 종류와 해시만)로 바꾸고(프롬프트 자산이므로 버전 규칙 확인), 공개 대상 트리에서 홈 절대 경로를 찾는 검사를 공개 반출 테스트에 둘지 정한다. **영향: 공개 이력에 이미 남아 되돌릴 수 없고 노출 범위는 경로·파일 제목·이름이다(자격증명·문서 본문 아님) · 심각도 낮음**.
  - ↳ [해소 2026-10-01] voice.md(v2)·두 출처 주석과 같은 계열 6곳(speechtotext SKILL.md 예시, 화자 식별·등록 기능 소개, 지식 키 시험 2건)까지 공개 반출 파일에서 실명·개인 홈 경로를 걷었고, `tests/unit/test_public_release_hygiene.py` 가 공개 반출 파일 전부(시험 포함)를 다이제스트로 대조해 재발을 막는다. 공개본 반영은 다음 릴리스 반출 때이며, 공개 이력에 남은 옛 사본은 되돌릴 수 없다.

## 릴리스 수렴 사각지대 해소 후 남긴 것 (2026-10-01)

- **v2 행(cron 등록·게이트웨이 세대·v2 file)은 상시 헬스체크가 보지 않고 릴리스 때만 판정한다** — 중앙 표에 투영되지 않으므로(`deploy_receipt.JUDGED_AT_RELEASE_ONLY`) 릴리스 사이에 손으로 바뀐 cron 주기나 낡은 게이트웨이 세대는 다음 릴리스까지 드러나지 않는다 → 상시 프로브를 더하는 별도 설계를 한다(중앙 표 바이트가 허용 목록 지문에 들어가므로 소유자의 허용 목록 재설치가 따른다). **영향: 탐지 지연만 · 릴리스마다 재판정된다 · 심각도 낮음**.
- **기관메일 로그인 페이지 열기 타임아웃 34회의 원인이 확인되지 않았다** — 노드 쪽 원인은 배제됐다 → 재발하면 그 실행의 세션 로그를 수집해 원인을 가린다. **영향: 그 시간대 발송 지연 · 승인·안전 불변식 무관 · 심각도 낮음**.
  - ↳ [해소 2026-10-02] 원인은 `agent-browser open` 의 25초 동작 제한이었다 — 노드에서 mailon.kr 열기가 5~23초 걸리다 6회 중 3회 27초에 `Operation timed out` 으로 끊겼고, 그때도 브라우저는 `/integrated/login` 에 있었으며 로그인 폼은 6회 모두 렌더됐다. `AGENT_BROWSER_DEFAULT_TIMEOUT` 은 이 제한을 바꾸지 못했다. `mailon/login.py` 의 `_open_login_page` 가 그 시간 초과 하나만, 로그인 경로에 도달한 경우에만 견딘다(회귀 `tests/unit/test_mailon_login_open_timeout.py`). [소개](기능소개/기관메일-로그인-페이지-열기-시간초과.md)
- **vendor mailon 오프라인 시험 `test_login_flow_calls_in_order` 가 main 에서 실패한다** — 제출 뒤 대기를 `wait_url` 로 하던 옛 흐름을 기대하는데 `410dcc53` 이 그 대기를 경로 판정으로 바꿨다. 이 시험은 `pytest tests/unit` 밖이라 CI 가 보지 않고, vendor 의존성(`pyotp` 등)을 갖춘 venv 에서만 돈다 → 시험을 현재 로그인 판정에 맞추고 vendor 시험을 CI 에 넣을지 정한다. **영향: 시험 하나가 낡음 · 동작 무관 · 심각도 낮음**.
- **[소유자 결정] `mail-triage-repair-daily` 는 저장소 밖에서 등록된 에이전트 모드 잡이다** — 선언에 없어 릴리스가 주기·대상을 수렴시키지 않고 `converge_cron` 도 에이전트 모드 잡은 건드리지 않는다 → 선언해 저장소가 소유할지, 없앨지 소유자가 정한다. **영향: 저장소 밖 잡 하나 · 심각도 낮음**. → **해소(2026-10-18, N1)**: 형 지시(10/18 01:33 KST "사이토플라즘의 도리 이전 작업도 계속해줘")에 따른 PLAN N1 (a)로 `skills/mail/scripts/mail_digest_repair_watch.py` no-agent 워처 + `runner=systemd` 선언으로 저장소가 소유한다. 노드의 옛 잡은 수렴기가 일시정지한다([소개](기능소개/정기-작업-사용자-타이머-이전.md)).
- **외부효과 승인 로그 기본 경로가 관측 미러를 가리킨다** — 기존 스킬 게이트(mail·calendar·budget·todo 등)의 승인 로그 기본값은 `/srv/autophagy-agents/logs/approvals.jsonl` 이다. Hermes 전용 도구 호출 게이트·플러그인은 은퇴했지만 과거 카드의 해시·소유자를 확인하는 `automation/interop/effect_approval.py`와 저장된 할 일 카드 배수는 이 기록을 사용한다. 미러는 단방향 관측소라 런타임 쓰기 자리로 맞지 않는다 → 체크아웃 밖 경로로 옮길 때 기록하는 쪽·남은 읽는 쪽·기존 기록을 **함께** 옮긴다. 새 계정 내부 쓰기에 승인 로그나 카드를 요구하지 않는다. **영향: 과거 카드 승인 기록 호환 — 이관을 잘못하면 승인된 외부효과가 막힌다 · 현재 동작 정상 · 심각도 낮음**.
- **skill-generation 런타임 사본이 roster 가 놓인 노드에서는 읽히지 않는다** — import 루트 고정 뒤에는 게이트웨이가 릴리스 세대에서 읽으므로 `~/.hermes/skill-generation/runtime` 사본은 roster 없는 설치를 위한 것으로만 남는다 → 모든 설치가 import 루트를 쓰게 되면 선언과 사본을 걷는다. **영향: 쓰이지 않는 사본 · 심각도 낮음**.
- **meeting·skill-generation 플러그인 본문이 아직 홈 사본이다** — interop-protocol 처럼 얇은 진입점으로 바꾸면 릴리스와 홈 파일의 세대 차이가 없어진다 → 두 플러그인을 진입점 + 릴리스 세대 본문으로 나눈다. **영향: 재시동 전 세대 차 · 전량 반영이 재시동으로 맞춘다 · 심각도 낮음**.
- **스킬 배포기는 시나리오 단계에서 여전히 `~/.hermes/interop_runtime` 을 쓴다** — `automation/deploy-skill.sh` 는 수정 동결이다. roster 가 놓인 노드에서는 그 안의 interop 트리가 릴리스로 수렴되지만 최상위 헬퍼는 배포기가 얹는 것뿐이다 → 동결이 풀리면 import 루트로 옮긴다. **영향: 시나리오 실행 환경만 · 심각도 낮음**.
- **배포 헬퍼가 남기는 백업·세대·import 묶음이 배포마다 쌓인다** — `<경로>.old.<시각>-<pid>`, `<경로>.d/<세대>`, `~/.hermes/autophagy-import.d/<세대>` 를 지우지 않는다(노드 경로를 지우지 않는다는 이 계획의 가드). `--link` 배포가 세대 배치와 전환 사이에 죽으면 게시되지 않은 `<경로>.d/<세대>` 도 남는다 → 오래된 것과 게시되지 않은 세대를 소유자 승인 아래 정리하는 명령을 설계한다. **영향: 디스크만 — 트리 하나가 수십~수백 KB · 심각도 낮음**.
- **cron 목록 파서가 `Last run` 오류 문구 안에 위조된 잡 블록을 가려내지 못한다** — 관측기와 `converge_cron` 이 사람용 목록을 줄 단위로 읽기 때문이다 → hermes 의 구조화 목록(JSON)을 쓰도록 바꾼다(지원 여부 확인이 먼저다). **영향: 오류 문구를 조작할 수 있는 경우의 오판정 · 심각도 중(낮음~중간)**.
- **[소유자 결정] roster 검증기의 rc 1(파이썬 오류·모듈 없음·sudo 거부처럼 "실행하지 못함")이 `roster-required` 로 분류된다** — 그러면 `init-local` 안내가 잘못 나간다. 노드 python3 에 PyYAML 이 없을 때도 같은 모양이다. 이 분류는 todo 39 가 정한 정의라 바꾸려면 결정이 필요하다 → 검증기가 "검증 실패" 와 "실행 실패" 를 다른 종료코드로 내게 할지 소유자가 정한다. **영향: 잘못된 안내 문구 · 보류 자체는 맞다 · 심각도 중(낮음~중간)**.
- **`owner_message` 가 노드에서 온 문자열의 markdown(링크·`**`·코드 펜스)을 이스케이프하지 않는다** — 적용 통지의 프로브 안내 줄은 노드 출력이라 형식이 섞일 수 있다(기존 결함) → 렌더러가 데이터 필드를 이스케이프한다(`render_version` append-only 규칙대로 새 판본에서). **영향: 통지 표시 · 멘션은 이미 무력화 · 심각도 낮음**.
- **`configs/runtime-package-manifest.txt` 8행 머리 주석이 아직 memory_curator 를 대조군으로 적는다** — memory_curator 는 표에서 빠졌다. 이 파일 바이트가 헬스체크 래퍼 지문에 들어가 고치면 소유자의 허용 목록 재설치가 따른다 → 다음 지문 변경과 함께 고친다. **영향: 주석만 · 심각도 낮음**.
- **`tests/unit/test_deploy_archive_stream_extraction.py` 의 추출 검사 정규식이 일부 모양을 놓친다** — `source "$(dirname …)/x.sh"` 경로와 tar 긴 옵션을 보지 못한다 → 두 모양을 시험 입력에 넣고 정규식을 넓힌다. **영향: 시험 사각지대 · 심각도 낮음**.
- **간접 interop 반입은 스킬 CLI 의 정적 규칙을 통과한다** — 문자열 결합 importlib, 헬퍼를 거친 import, 다른 함수의 legacy 경로 삽입은 정적 검사가 보지 못한다 → 게이트웨이·CLI 시작 때 실제 import 출처를 감사하는 검사로 닫는다. **영향: 옛 사본을 읽을 수 있는 우회 경로 · 심각도 낮음**.
- **[소유자 결정] FS3 고정된 cron 등록 원문 다섯 줄이 공용 헬퍼를 쓰지 않는다** — budget·research-trends·notes-weekly-organize·mail 두 줄은 hermes 가 편집을 받아도 값을 바꾸지 않은 경우 성공으로 보고하고, 목록 형식이 바뀌면 중복을 만든다. 고정 시험(`tests/unit/test_watcher_deploy_coverage.py`)이 그 모양을 묶고 있다 → 재고정할지 소유자가 정하고, 정하면 다섯 줄을 `converge_cron` 으로 옮긴다. **영향: cron 주기 오보고·중복 가능성 · 심각도 중(낮음~중간)**.
  ↳ 경과(2026-10-21): research-trends 줄은 그 작업과 함께 노드에서 지워져 네 줄이 남았다.
- **[소유자 결정] 회계 가드 A9 가 FS3 기준선 뒤에 생긴 불릿을 지키지 않는다** — `tests/unit/test_features_board_conformance.py` 의 `_ledger_source_rows` 는 FS3 기준선 `4716602d` 시점 `follow-ups.md` 에 있던 불릿만 대조하므로, 그 뒤에 생긴 불릿(D029~D033, 이 묶음 자체)은 지우거나 첫 줄을 고쳐도 시험이 붉어지지 않는다. 2026-10-01 실측: 임시 사본에서 D031 불릿 첫 줄을 한 글자 바꾸면 보드·FS3 원장 시험 3종이 34 passed, 같은 실험을 기준선에 있는 불릿에 하면 `test_a9_follow_up_deletions_equal_replayed_already_fixed_rows` 가 FAILED → 가드의 대조 원천을 현재 두 원장으로 넓히거나 더 새 기준선으로 재고정한다. 어느 쪽이든 FS3 고정 시험을 고쳐야 하므로 소유자가 정한다. **영향: 회계 가드의 범위만 · 런타임 무영향 · 원장 편집은 PR 검토를 거친다 · 심각도 낮음**.
- **provenance 가드는 일부러 회피하도록 쓴 커밋 코드에 대한 샌드박스가 아니다** — 콜백을 받는 빌트인, 가드 내부 변수 재바인딩, 페이로드에 들지 않은 저장소 스크립트의 로컬 실행처럼 회피를 노리고 커밋된 배포기는 정적 닫힌 문법과 동적 클로저를 비켜 갈 수 있다. 커밋 권한이 이미 가드를 우회하므로(원하는 코드를 그냥 커밋하면 된다) 막는 몫은 코드 리뷰와 CI 다(todo 58 두 번째 Scope note) → 더 강한 격리가 필요해지면 봉인 릴리스 트리에서만 배포하는 경로를 설계한다. **영향: 고의 회피만 · 선의의 배포기는 해당 없음 · 심각도 낮음**.

- **FS3 재생 시험이 PATH 에 따라 거짓으로 실패한다** — `automation/fs3_replay_workspace.py` 는 기록된 `python3 -m pytest …` 를 부모 PATH 그대로 재생한다. 시험을 venv 인터프리터의 절대 경로로 시작하고 venv `bin` 이 PATH 에 없으면 재생의 `python3` 가 pytest 없는 시스템 인터프리터로 풀려 FS3 재생 시험 3개가 거짓 실패한다(진단: 묶음 증적 디렉터리의 `fs3-replay-diagnosis.txt`) → 재생 env 의 PATH 앞에 `dirname(sys.executable)` 을 붙이고, FS3 고정 파일은 건드리지 않은 채 **새 시험 파일**에 회귀를 둔다. **영향: 시험 하네스 견고성만 · venv 를 활성화한 `local_ci.sh` 는 무영향 · 심각도 낮음**.
- **`mail-triage-watch` 의 통지 지연이 설계 의도(약 50분)보다 짧다** — 설계 규약의 원래 의도는 `*/10` × 임계치 5 ≈ 50분이었으나 선언·노드 주기는 `*/2` 라 `FAILURE_NOTICE_THRESHOLD = 5` 에서 약 10분 만에 통지가 열린다(표는 사실대로 고쳤다) → 임계치를 약 25 로 올려 약 50분을 지킬지, 지금의 약 10분을 유지할지 설계 판단으로 정한다. 오늘의 동작은 바뀌지 않았다. **영향: 일시 장애 통지 빈도 · 심각도 낮음**.
- **`docs/guide/report-hub.md` 가 설치별 값을 그대로 적고 있다** — 대시보드 tailnet IP 주소, 운영자 홈 아래 절대 경로(배포 사본·레지스트리), 그리고 독립 검토자가 보고한 노드 이름이 본문에 있어 「개인화 코드 금지 규칙」에 어긋난다. 이 파일은 `configs/public-export-manifest.txt` 의 제외 목록에 있어 공개 배포본에는 실리지 않는다 — 노출 범위는 private 저장소와 그 이력이다 → 값을 `<tailnet-ip>`·`<operator-home>/report-hub/`·`<primary-node>` 같은 자리표시자로 바꾸고 실제 값은 `~/.hermes/node.toml`·운영자 노트로 옮긴다(웨이브 6 독립 검토에서 발견). **영향: private 문서의 설치별 값 · 공개 반출 없음 · 심각도 낮음**.
- **메일 배포기의 cron 수렴 출력에 `Exception ignored … BrokenPipeError` 가 섞인다** — `skills/mail/deploy.sh` 의 cron 등록 두 줄이 `hermes cron list --all | awk '… {print id; exit}'` 로 id 를 찾는데 awk 가 먼저 끝나 hermes 의 stdout 플러시가 끊긴 파이프에 닿는다. 잡은 정상 갱신된다(`Updated job` 이 뒤따른다) → 목록을 끝까지 읽고(drain) 고르거나 공용 `converge_cron` 으로 옮긴다. 두 줄은 FS3 고정된 cron 등록 원문이라 위 「FS3 고정된 cron 등록 원문 다섯 줄」 결정과 함께 처리한다. **영향: 배포 로그 소음만 · 심각도 낮음(외관)**.
- **트리 선택 규칙의 공유 입력 대조 시험이 없다** — 같은 선택 규칙(base-dir 아래 추적되는 `*.py` 에서 최상위 `cron/` 제외)을 릴리스 관측기와 배포 트리 헬퍼(`automation/deploy_tree.sh`), 그리고 상시 프로브 `automation/runtime_package_probe.sh` 가 각자 구현하는데 셋에 같은 입력을 먹여 결과를 비교하는 시험이 없다. 지금은 배포기↔선언 결합 시험과 라이브 `--verify` 로 간접적으로만 덮인다(최종 품질 검토 F2 의 N1) → 같은 픽스처 트리를 셋에 넣고 선택 집합이 같은지 보는 대조 시험을 더한다. **영향: 시험 범위만 · 현재 선택 결과는 일치 · 심각도 낮음**.
- **`automation/interop/deploy.sh` 가 `$account`·`$rollback` 을 원격 명령에 `printf %q` 없이 넣는다** — 두 값은 각각 검증되거나 로컬에서 만들어져 오늘 주입 가능한 입력은 없지만, 같은 모양이 여러 기존 배포기에도 있다(최종 품질 검토 F2 의 N2) → 원격 명령에 끼우는 변수를 배포기 전반에서 `printf %q` 로 일관되게 인용한다(배포기 수정 동결 범위는 동결이 풀릴 때). **영향: 심층 방어 · 현재 주입 가능 입력 없음 · 심각도 낮음**.
증적: `.omo/evidence/release-convergence-blind-spots/` (계획 `.omo/plans/release-convergence-blind-spots.md`, 실측 `.omo/drafts/release-convergence-blind-spots.md`).

## 대리 글 첨부 수락 수리 중 발견한 것 (2026-10-02)

- **대리 글 첨부를 그대로 메일에 붙이면 받는 사람에게 캐시 접두어가 붙은 이름이 보인다** — 게이트웨이는 첨부를 `~/.hermes/cache/documents/doc_<12hex>_<원래 이름>` 으로 캐시하고, 메일 `--attachment` 는 경로의 파일 이름(`path.name`)을 그대로 표시 이름으로 쓴다. 그래서 메일에는 `doc_…_보고서.md` 로 간다. 벤더 캐시 정리(`cleanup_document_cache`, 기본 24시간)가 돌면 승인 대기 중 원본이 사라질 수도 있지만, 그 경우는 발송 전 manifest 재검증이 막는다(fail-closed) → 대리 턴이 첨부를 원래 이름으로 자기 작업 디렉터리(체크아웃 밖)에 복사한 뒤 `--attachment` 로 넘기도록 메일 SKILL.md 에 절차를 싣거나, compose 에 표시 이름 인자를 더한다. **영향: 수신자에게 보이는 파일 이름 · 발송 안전성 무영향 · 심각도 낮음**.

## RCB-1 소유자 조치 처리 중 남긴 것 (2026-10-02)

- **설치기가 healthcheck 선언 파일을 다시 쓰면 대시보드 주소 줄이 사라진다** — `automation/install/assets.py` 는 프로필을 고를 때 `/etc/autophagy/healthcheck.env` 를 `HEALTHCHECK_SERVICES` 한 줄로 렌더하므로, 소유자가 더한 `HEALTHCHECK_REPORT_HUB_DASHBOARD_URL` 줄이 재설치 때 빠진다. 빠지면 프로브가 노드 이름 주소로 돌아가 `report-hub dashboard auth` 가 다시 FAIL 이 된다(조용하지 않다) → 설치기가 선언을 렌더할 때 기존 파일의 이 키를 보존하거나, `node.toml` 에 대시보드 주소 키를 두고 렌더에 함께 싣는다. **영향: 재설치 뒤 헬스체크 사건 재발 · 심각도 낮음**.
증적: `docs/qa/RCB-1/owner-actions.md`, 기능 소개 `docs/기능소개/주-모델-변경-뒤-cron-재생성.md`.

## 공개 배포본 v1.14.6 반출 보류 (2026-10-06)

> ↳ 2026-10-07 v1.14.9 사이클에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## peer attestation signed 전환 후 남긴 것 (2026-10-06)

> ↳ 2026-10-07 v1.14.9 사이클에서 해소 — 원문과 처리 근거는 [follow-ups-deferred.md](follow-ups-deferred.md) 의 같은 헤딩 아래.

## cytoplasm 이전 전수 조사에서 남긴 것 (2026-10-10)

- **공개 코드의 Discord User-Agent 가 옛 비공개 저장소 URL 을 가리킨다** — `DiscordBot (https://github.com/orientpine/autophagy-agents, 0)` 이 automation 과 스킬 스크립트 27곳(예: `automation/owner_notice.py:35`, `skills/mail/scripts/triage_confirm.py:18`)에 있다. 기능에는 영향이 없다. 스킬 파일을 고치면 그 스킬 10여 개가 다시 마운트되므로 통지 복구 릴리스에 섞지 않았다 → 한 상수(예: `automation/interop/user_agent.py`)로 모으고 cytoplasm URL 로 바꾼다. 스킬은 격리 샌드박스에서 automation 을 import 하지 못하는 것도 있어 사본이 필요할 수 있다. **영향: 공개 코드가 404 URL 을 광고 · 심각도 낮음**.
- **반출 뒤처리 두 가지가 남았다** — 롤백 기간 종료 정리(2026-10-10)는 반출 스크립트·원장·전용 시험만 지웠다. ① `automation/public_export_redaction.redact_vendor_tree` 와 그 시험 `tests/unit/test_public_export_redaction.py` 는 이제 반출 때만 쓰던 함수라 호출자가 없다. 같은 모듈의 토폴로지 규칙은 `public_gate` 가 쓰므로 모듈은 남겨야 한다. ② `skills/mail/vendor/NOTICE:87` 이 "스냅샷이 redaction 을 돈다"고 아직 말한다. 스킬 파일이라 고치면 mail 이 다시 마운트되므로 이번 정리에 넣지 않았다 → 토폴로지 규칙을 `automation/public_topology.py` 로 옮기고 redaction 함수·시험을 지운다. NOTICE 는 다음 mail 변경 때 함께 고친다. **영향: 죽은 코드와 낡은 문구 · 심각도 낮음**.

## 공개 우선 전환 준비 중 남긴 것 (2026-10-07)

- **업스트림을 따르기만 하는 설치에도 릴리스 백로그 다이제스트가 `automation/release.sh` 를 안내한다** — 공개 우선 전환 뒤에는 cytoplasm main 에 태그 사이 서명 없는 개발 커밋이 쌓이므로, 제3자 노드의 리컨실러도 3일 뒤 「미배포 커밋 N건 · 릴리스하려면 release.sh」 를 보낸다. 그 소유자는 서명키가 없어 할 일이 없다. 문구는 `automation/deploy_reconcile_backlog.py` 가 만들고 지속 재시도 바이트를 `tests/unit/test_deploy_reconcile_owner_message.py` 가 고정하므로 이번 범위에서 바꾸지 않았다 → 노드가 유지보수자 설치인지(예: `node.toml` 키) 판정해 안내 줄을 고르고, 저장된 대기 통지는 옛 바이트 그대로 재시도되게 판본을 나눈다. **영향: 제3자 소유자에게 잘못된 조치 안내 · 심각도 낮음(사고 아님 문구 유지)**.

## 다이제스트 하루 한 승인 스레드 (2026-10-16, 소유자 Q187)

- 메일 결과 통지 본문이 아직 `owner-ko-v1` 다섯 칸(`대상: …`)이다 → 부모 안내처럼 v3 로 옮길지 판단한다. 영향: 모양만(모든 메일 승인 스레드 공통), 승인·발송 무관. 근거 tests/unit/test_digest_one_thread_e2e.py 전사의 결과 줄.

## 형 채널 글 기계 출력 거름 후 남긴 것 (2026-10-20)

- **릴리스 승인 카드(v8)가 해시 두 줄과 안내 줄을 형에게 보인다** — 스레드 안 카드에 `배포 기준: <짧은 SHA>`·`승인 식별값: <64자리>`·결정/취소 시/다음 줄·`-# 참조:` 가 그대로 있다 → 메일 카드(`mail_card_compact`)처럼 해시·안내 줄을 기록에만 두는 새 렌더 판본을 만든다(action_hash 바인딩·v1~v8 바이트 동결 유지). 영향: 형이 보는 릴리스 카드 모양, 승인 판정 무관. 심각도 중.
- **일일 자동 쓰기 요약의 유돌 항목 이름이 명령어 모양이다** — `stt transcribe <파일>`·`patent export <id>` 처럼 잡 이름이 그대로 목록에 나온다 → `automation/yoodori_gate` 잡 종류를 한국어 이름(전사·특허 반출 등)으로 바꿔 원장 제목에 쓴다. 영향: 21:30 요약 가독성. 심각도 낮음.

## v2.6.7 배포 중단에서 남긴 것 (2026-10-20)

- **승인 대기 중 main 이 앞서가면 그 릴리스의 스킬 배포가 전부 막힌다** — `deploy-skill.sh` 가 자기 파일을 `origin/main` 과 대조하므로, v2.6.7(59e22c6) ✅ 대기 중 #144 가 `deploy-skill.sh` 한 줄을 바꾸자 여덟 스킬이 `DEPLOY-BLOCK` 으로 멈췄다. 노드 릴리스 트리는 수렴했고 스킬 마운트만 옛 판이다 → `deploy_all` 이 서명 태그가 `origin/main` 의 조상일 때 provenance 기준을 그 릴리스 SHA 로 넘길지 판단한다(공개 main 에 있는 코드만 배포한다는 불변식은 유지). 영향: ✅ 대기가 길수록 스킬 갱신 누락 · 심각도 중.
- **meeting 재시동 통지와 루트 AGENTS.md 가 없는 문서 `docs/guide/operations.md §2` 를 가리킨다** — 파일이 리포에 없다 → 실제 게이트웨이 재시동 절차 문서로 바꾸거나 절을 되살린다. 영향: 안내 링크가 막힘 · 심각도 낮음.
- **v2.6.8 이 P5 노드에 meeting 플러그인을 다시 밀었다** — 은퇴 목적지인 `~/.hermes/plugins/00-meeting-gate/` 에 2f3e8b845308 판이 남아 있다. 읽는 게이트웨이가 없어 동작 영향은 없다 → 다음 P5 정리에서 지울지 판단한다. 심각도 낮음.
