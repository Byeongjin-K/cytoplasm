# Hermes 유산 1차 정리: 화면·죽은 입력만 끄고 업무 데이터는 보존

소유자 지시(2026-10-18):
> 헤르메스 시절의 유산 중 불필요한것 모두 없애줘. 그리고 헤르메스 메모리 시스템도 omo메모리로 하게 해줘 recall은 유지

첫 묶음은 아래 범위만 처리한다. Hermes 메모리의 omo 이관과 저장소 전반의
코드·문서 제거는 이 묶음에 포함하지 않는다.

## 무엇이 달라지나

- **doctor:** 유돌 발신 override가 있으면 실제 유돌 채널과 그 채널에 있는
  릴리스·공급망 승인만 점검한다. 옛 `agents-log`·interop 채널 접근 실패를
  봇 고장으로 알리거나 멈춘 Hermes 게이트웨이의 재시동을 안내하지 않는다.
  override가 없는 레거시 설치의 검사는 유지한다.
- **report-hub:** ops의 `report-hub-collector.service`와
  `report-hub-dashboard.service`를 릴리스가 정지하고 실제 정지를 재조회한다.
  정지 표식이 있는 동안 활성 헬스체크 목록에서 세 report-hub 검사를 뺀다.
  복구를 위해 전체 카탈로그·SSH 허용 목록·유닛·DB·인증 파일은 남긴다.
- **Hermes 보드 화면:** agent의 `hermes-dashboard.service`만 같은 방식으로 정지한다.
  자격증명 교체 대상 `kanban`은 제거한다. report-hub 교체 구현은 복구용으로 남기되
  실행 시 릴리스 정지 표식을 확인해 꺼진 유닛의 재시작을 거부한다.
  import 때 목록을 고정하지 않고 실행 직전에 판정하므로 오래 살아 있는 프로세스에도 적용된다.
- **recall:** RAG·검색·기존 색인·위키·노트·회의록·Obsidian·Discord team 수집은 유지한다.
  기존 로컬 설정도 이제 Hermes 대화 DB와 `agents-log`의 신규 수집만 기본으로 끈다.
  이 변경은 이미 색인한 기록을 삭제하거나 재색인하지 않는다.
- **승인 채널(Q236):** “#yoodori 그대로 — 한 곳에서 다 보기”.
  릴리스·공급망 새 승인 카드는 발신 봇 채널을 따른다. override가 있으면
  다른 `personal_approvals_channel_id`보다 우선하고, 없으면 개인 승인 채널을 쓴다.
  이미 게시된 카드의 좌표·결정은 바꾸지 않는다.

## 릴리스로 끄기·되돌리기

릴리스 호스트에서 아래 두 파일 중 끌 대상에만 **줄바꿈 없는 `off`**를 둔다.
파일 내용을 승인 표면에 묶으므로 다른 값·읽기 실패는 계획을 거부한다.

| 호스트 파일 | 카드에 추가되는 문구 |
|---|---|
| `~/.hermes/release-flags/report-hub-off` | report-hub 끄기: 옛 #agents-log 보고 대시보드 유닛 정지(제거 아님, 파일 삭제 후 재배포로 다시 켬) |
| `~/.hermes/release-flags/hermes-dashboard-off` | Hermes 보드 화면 끄기: hermes-dashboard 유닛 정지(보드 데이터 kanban.db는 그대로, 파일 삭제 후 재배포로 다시 켬) |

예: 두 파일을 두고 정상 릴리스 카드를 승인하면 기존 배포기가 해당 계정의 유닛을
정지한다. 같은 HEAD의 승인에 정지 표면이 없으면 쓰기 전에 거부한다.
선언 `legacy-service-v1`은 호스트가 넘긴 모드와 실제 유닛 상태까지 검사하며,
어긋나거나 읽을 수 없으면 전량 반영 영수증을 내지 않는다. 별도 노드 수작업은 없다.
doctor도 자기 계정의 정지 표식과 유닛이 일치하면 정상으로 표시한다.

다시 켜려면 **해당 호스트 파일만 삭제하고 재배포**한다.
이 배포기가 정지시킨 유닛만 다시 시작하고 정지 표식을 걷는다.
report-hub의 세 헬스체크와 자격증명 교체도 다시 유효해진다.
처음부터 스위치·정지 표식이 없는 설치에는 유닛을 새로 만들거나 시작하지 않는다.

RAG 입력을 다시 쓰려면 계정의 `~/.hermes/rag-ingest/config.json`에서
`index_hermes_conversations: true`와 `discord.index_agents_log: true`를 필요한 것만 켠다.
다음 수집부터 다시 읽는다. 파일 소스·team 설정은 바꾸지 않는다.

## 유지하는 것

`hermes` CLI, `~/.hermes/config.yaml`의 주 모델·폴백, 로그인(`auth.json`),
`~/.hermes/kanban.db`는 지우지 않는다.
배치 모델 호출은 기존 CLI 경로를 쓰고, 수리 티켓과 회의 action item은
계속 `hermes kanban`을 사용한다. 화면 정지는 데이터 제거가 아니다.

관련: `automation/legacy_services.py`, `automation/legacy_services/deploy-manifest.txt`,
`automation/{release.sh,release_approval.py,deploy_all_observe.py,healthcheck_registry.sh}`,
`automation/doctor/`, `automation/rag_ingest/`, `automation/credential_rotation/`,
`tests/unit/test_legacy_services*.py`, `tests/unit/test_rag_legacy_sources.py`,
`tests/unit/test_doctor_yoodori_channels.py`, `tests/unit/test_q236_approval_channel.py`.
