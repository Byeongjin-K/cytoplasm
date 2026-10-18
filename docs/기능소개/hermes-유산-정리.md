# Hermes 유산 정리: 화면·메모리 타이머를 끄고 업무 데이터는 보존

소유자 지시(2026-10-18):
> 헤르메스 시절의 유산 중 불필요한것 모두 없애줘. 그리고 헤르메스 메모리 시스템도 omo메모리로 하게 해줘 recall은 유지

첫 묶음의 화면·입력 정리에 Q4 메모리 저장 경로 전환과 타이머 정지를 더한다.
기본 정지 모드는 기존 Hermes 메모리 파일과 백업을 삭제하거나 덮어쓰지 않는다.
아래 카드 C 제품 제거는 별도 스위치와 같은 HEAD의 승인이 필요하다.

## 무엇이 달라지나

- **doctor:** 유돌 발신 override가 있으면 실제 유돌 채널과 그 채널에 있는
  릴리스·공급망 승인만 점검한다. 옛 `agents-log`·interop 채널 접근 실패를
  봇 고장으로 알리거나 멈춘 Hermes 게이트웨이의 재시동을 안내하지 않는다.
  Hermes 실행 파일·게이트웨이·무멘션 응답 검사는 제거하고 운영자 모드도 agent만 읽는다.
- **report-hub:** ops의 `report-hub-collector.service`와
  `report-hub-dashboard.service`를 릴리스가 정지하고 실제 정지를 재조회한다.
  세 report-hub 검사는 헬스체크 카탈로그와 파생 SSH 허용 목록에서 제거한다.
  유닛·DB·인증 파일은 복구를 위해 남긴다.
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
승인 표면·카드 문구는 이미 스테이징되는 `release_spec.py`에 두므로,
스킬 승인 런타임이 서비스 제어 모듈을 새로 요구하지 않는다.

다시 켜려면 **해당 호스트 파일만 삭제하고 재배포**한다.
이 배포기가 정지시킨 유닛만 다시 시작하고 정지 표식을 걷는다.
자격증명 교체도 다시 유효해진다. report-hub 헬스체크는 복구와 무관하게 제거된 상태다.
처음부터 스위치·정지 표식이 없는 설치에는 유닛을 새로 만들거나 시작하지 않는다.

RAG 입력을 다시 쓰려면 계정의 `~/.hermes/rag-ingest/config.json`에서
`index_hermes_conversations: true`와 `discord.index_agents_log: true`를 필요한 것만 켠다.
다음 수집부터 다시 읽는다. 파일 소스·team 설정은 바꾸지 않는다.

## 유지하는 것

`hermes` CLI, `~/.hermes/config.yaml`의 주 모델·폴백, 로그인(`auth.json`),
`~/.hermes/kanban.db`는 기본 정지 모드에서 지우지 않는다.
배치 모델 호출은 기존 CLI 경로를 쓰고, 수리 티켓과 회의 action item은
장부 이관 전까지 `hermes kanban`을 사용한다. 화면 정지는 데이터 제거가 아니다.

## Hermes 제품 제거(카드 C)

릴리스 호스트 `~/.hermes/release-flags/hermes-product`에 줄바꿈 없는 `retired`를
두면 `hermes-product:retired` 표면과 제거 범위가 릴리스 승인 카드에 붙는다.
같은 HEAD의 소유자 승인을 확인한 뒤 기존 P5 제거 다음에 agent 계정으로 실행한다.
스위치 부재는 무동작이며 잘못된 값은 거부한다. 스위치 삭제는 **자동 복원하지 않는다**.

제거 범위는 `~/.hermes/` 아래 `state.db`와 `-wal`·`-shm`, `sessions/`,
`memories/`, `SOUL.md`, `skills/`, `models_dev_cache.json`·`.etag`,
`gateway_state.json`, `gateway-generation.json`, `gateway-starts.log`,
`gateway-stopped-by-release`뿐이다. `kanban.db`는 작은 장부의 `metadata`에
같은 트랜잭션으로 기록된 `migration`(보드·카드·상태 개수)·`source` 지문과
활성 `mode=on`을 확인하고, 현재 원본 전체 내보내기·원본 DB 개수·지문이 다시
일치할 때만 추가한다. 증적이 없거나 달라졌거나 SQLite 보조 파일이 있으면 남기고 이유를 알린다.

**`.hermes` 자체는 우리 런타임이라 남긴다.** `.local/bin/hermes`,
`.hermes/hermes-agent/`, `config.yaml`, `auth.json`과 나머지 모든 경로도 남긴다.
`codex_llm`의 배치 CLI 의존을 끊었다는 검증 없이 바이너리·설정·로그인을 지우지 않는다.

예: 승인 후 사용하지 않는 세션·캐시만 제거되고 배치 호출과 우리 워처는 유지된다.
반대로 대상 경로를 사용자 유닛(드롭인 포함)·같은 uid의 프로세스 열린 파일/cwd·
런타임 배포 선언 목적지가 사용하면 이유별 한 줄을 내고 **첫 삭제 전 거부**한다.
판독 불가도 거부한다. 현재 `memory_relocate` 선언의 `SOUL.md`처럼 제품 경로가
선언에 남아 있으면 먼저 그 의존을 정리해야 하며 자동으로 선언을 무시하지 않는다.

Q248 백업은 `~/.hermes/retired/hermes-product/<승인 HEAD>/`의 `SOUL.md`와
`kanban.db` 원본 및 `sha256.json`뿐이다(0600, fsync, 해시 재조회).
세션·메모리·state.db는 백업하지 않아 복구할 수 없다. P5가 먼저 제거한 파일의
원본은 기존 P5 백업에 남으며 카드 C가 없는 원본을 만들어내지는 않는다.
중단 후 재실행도 기존 백업·원본 해시를 대조하고, 완료 표식은 삭제가 끝난 마지막에 쓴다.
완료 뒤 캐시 등이 다시 생기면 사용 경로 검사를 다시 하고 제거한다.

복원할 때는 제거 스위치를 먼저 없애 다음 릴리스의 재삭제를 막고,
사용 중인 경로가 없는 상태에서 `sha256.json`과 백업 파일의 SHA-256을 대조한 뒤
필요한 `SOUL.md`·`kanban.db`만 원래 `~/.hermes/` 위치에 0600으로 복사한다.
장부 DB는 삭제하거나 역동기화하지 않는다. 배치 호출 설정·로그인 파일은 애초에 제거하지 않는다.

관련: `automation/hermes_product.py`, `automation/hermes_product_live.py`,
`automation/ledger/migration.py`, `automation/{release.sh,release_approval.py,deploy_all.sh}`,
`tests/unit/test_hermes_product_retire.py`.

## 메모리

짧은 사실·선호·규칙의 저장 경로는 **유돌 omo 메모리**다.
소유자 선호·규칙은 매 세션에 실리는 `system/human.md`에 두고,
문서·긴 기록은 `obsidian-request`로 보낸다. 역할 템플릿이 이 구분을 안내하며
호출자가 없는 옛 기억 분류·위키 정본·MEMORY.md 병기 패키지는 은퇴한다.
기존 파일의 omo 시드는 이 배포기가 수행하지 않는다.

릴리스 호스트 `~/.hermes/release-flags/hermes-memory-off`에 **줄바꿈 없는 `off`**를
두면 승인 표면 `hermes-memory:off`와 아래 카드 한 줄에 묶인다.

> Hermes 메모리 타이머 끄기: memory-curator·memory-relocate 정지(형 기억은 유돌 omo 메모리, 파일 삭제 후 재배포로 다시 켬)

승인 뒤 agent의 `autophagy-cron-memory-curator-watch.timer`(30분)와
`autophagy-cron-memory-relocate-watch.timer`(2분)만 disable·stop한다.
서비스·USER.md·MEMORY.md·백업은 지우지 않는다. 정지 전에 기존 relocation pending
판독기로 미결을 확인하며, 하나라도 남으면 `MEMORY-OFF-REFUSED: 미결 메모리 선택 …`과
비정상 종료로 **두 타이머 모두 그대로** 둔다. 선택을 이미 했어도 실행·정산이
끝나지 않은 `approved`·`written`·`ingested`는 미결이다. 상태가 깨졌거나 워처가
실행 중이어도 정지하지 않는다. 워처와 같은 잠금을 잡은 동안 판독과 정지를 이어
새 카드가 끼어드는 것을 막는다.

예: “짧은 한국어 답변을 선호한다고 기억해”는 유돌 omo의 `system/human.md`에,
“회의 결정 기록을 노트로 저장해”는 `obsidian-request`로 간다.
미결 메모리 카드가 있는 릴리스는 먼저 그 카드가 종결돼야 정지를 완료할 수 있다.

파일 삭제 후 재배포하면 이 배포기가 정지시킨 두 타이머만 enable·start하고
표식을 걷는다. 스위치·표식이 없는 설치는 기존대로다. 릴리스 관측은 정지 모드에서
타이머 활성 대신 disabled·inactive를 재조회하고, doctor도 그 표식과 실제 상태를
함께 확인한다. 따라서 기대 정지는 고장이 아니며, 다시 켜진 타이머는 드리프트다.
헬스체크의 전량 반영 영수증 검사는 이 관측을 통과한 릴리스를 계속 확인한다.
**recall·RAG·기존 색인과 검색 경로는 유지한다.**

관련: `automation/legacy_services.py`, `automation/legacy_services/deploy-manifest.txt`,
`automation/{release.sh,release_approval.py,deploy_all_observe.py,healthcheck_registry.sh}`,
`automation/doctor/`, `automation/rag_ingest/`, `automation/credential_rotation/`,
`tests/unit/test_legacy_services*.py`, `tests/unit/test_rag_legacy_sources.py`,
`tests/unit/test_doctor_yoodori_channels.py`, `tests/unit/test_q236_approval_channel.py`,
`tests/unit/test_hermes_memory_off.py`, `dori/roles/yoodori/AGENTS.md.tmpl`.

## 최소 감시(Q250·Q251)

헬스체크는 서비스·배포를, doctor는 로그인·작업 결과를 본다.
Hermes 게이트웨이 둘·peer 채널 무시·자가 스킬 루트·report-hub 셋은 감시 목록에서 뺐다.
알림 목적지 전환(Q252)은 별도 변경이며 아래 표는 현재 통지 경로다.

| 무엇을 감시하나 | 어디서 | 누구에게 알리나 |
|---|---|---|
| RAG 서비스·이미지, 업데이트 신뢰, root 도우미, 스킬 마운트, 런타임 패키지, 저장소 용량, 전량 반영 영수증, 통지 자격증명 | 5분 헬스체크 | 기존 소유자 통지 + FAIL 수리 티켓 |
| 관측 미러 지연·허용 목록·워처 래퍼 드리프트 | 헬스체크의 **확인** | 로컬 로그만, 사건·수리 티켓 없음 |
| 미러 dirty·ahead | 헬스체크 | 기존 FAIL·수리 티켓 유지 |
| 주 모델·폴백, Discord 봇·승인 표면, Google·Drive·기관메일, 로컬 전사·필수 설정·발신 봇 | 매시 doctor(agent) | 문제 집합이 바뀔 때 기존 소유자 통지 |
| 타이머 결과, 평일 수집 0건 6회 이상, mailon 런타임 드리프트, 공급망 리마인더 실패 | doctor의 정기 작업 | 같은 doctor 통지 |
| 승인 실행·수리 승인·릴리스 수렴 | 기존 시스템 타이머 | 기존 카드 결과·수렴 통지 |

예: 평일 메일 수집이 6회 성공했는데 새 메일이 계속 0건이면
`~/.local/state/autophagy/mail-sync/collection.json` 결과를 읽은 doctor가 확인한다. 주말 0건은 세지 않는다.
실행 실패는 성공으로 숨기지 않고 타이머 러너에 넘긴다.
옛 `watch-failure` 장부는 읽거나 갱신하지 않으며 기존 파일은 지우지 않는다.
과거 순수 장부 모듈은 이전 증적 재생용일 뿐 현재 래퍼는 호출하지 않는다.

허용 목록은 같은 HEAD·기대 지문의 갱신이 현재 릴리스 카드에 포함된 것을 읽을 수 있을 때만
**갱신 대기**로 표시한다. 카드를 읽지 못하면 갱신을 추측하지 않고 **확인**으로 남긴다.
이번 카탈로그 축소는 래퍼 지문을 바꾸며 다음 릴리스의 기존 갱신 절차가 반영한다.

공급망 리마인더는 릴리스 카드까지 `skill-deploy`로 검증하던 종류 오류를 고쳤다.
저장된 종류·채널을 그대로 검증하고, 실패는 `supply-chain-watch/tick.json`에 누적한다.
2회 이상 실패하면 doctor의 정기 작업 실패로 보이며, 실제 전송 성공 뒤에만 누적을 걷는다.
새 승인 표면이나 별도 워처는 만들지 않는다.
