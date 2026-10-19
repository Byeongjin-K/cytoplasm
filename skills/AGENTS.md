# skills/ — 에이전트 스킬 (autophagy 고유 저작 규약)

스킬 하나 = `skills/<name>/` 디렉터리 하나. Hermes는 스킬 루트에 `<name>/`이 드롭되면
자동 발견한다(install 불필요). 배포는 루트 `automation/deploy-skill.sh` 4단계 게이트.

## 이 트리에 없는 것 — 에이전트 자가 스킬 (SS-1, 2026-08-15)

에이전트(agent·peer)가 **스스로 지은** Hermes 스킬은 이 리포에 없다. 그것들은 각 계정이
소유한 **쓰기 가능한 자기 Hermes 1차 루트**(`~/.hermes/skills`, 계정 소유 0700)에만 산다.

- **W1-8을 거치지 않는다.** 샌드박스·peer 검토·소유자 ✅·live 마운트는 이 트리에 착지한
  스킬에만 적용된다. 자가 스킬을 그 파이프라인에 올리려면 먼저 `skills/<name>/`으로 옮겨
  커밋·푸시하는 것이 유일한 경로다 — 그 전에는 배포 판정
  (`readlink <skill_store>/live/<name>`)에 아무 지분도 없다.
- **통제는 배포 게이트가 아니라 감사 원장 + `hermes curator`다.** 자가 저작은 소유자 승인
  없이 착지하고(소유자 결정 2026-08-15: 자유 저작 + 사후 감사), `automation/selfskill_audit/`가
  콘텐츠 해시 델타를 원장에 적고 주기적으로 마스킹 요약을 소유자에게 DM한다. 회수는
  `hermes curator archive/pin`이다.
- **이름은 양방향으로 막힌다.** 자가 스킬이 배포 스킬 이름을 선점하려 하면 Hermes가
  `skill_manage(create)`에서 거부하고, 배포가 자가 저작물을 덮어쓰려 하면 `deploy-skill.sh`가
  `SELF-SKILL-COLLISION-BLOCK`(exit 4)으로 멈춘다.
- **`~/.hermes/skills` 아래에 있는 것을 관리자 배포본으로 읽지 않는다.** 반전 전에는 그
  경로가 live 스토어의 read-only bind였기에 그 가정이 통했지만, 지금 관리자 배포본은
  `<skill_store>/live`에 남아 Hermes `skills.external_dirs`로 읽기 전용 발견된다.
  상세: [docs/기능소개/에이전트-자가-스킬.md](../docs/기능소개/에이전트-자가-스킬.md).

## 디렉터리 형식
```
skills/<name>/
├── SKILL.md              # 필수. frontmatter + 사용법 본문
├── scripts/
│   ├── <name>_cli.py     # 실행 진입점 (거의 모든 스킬)
│   ├── *watch.py / confirm_reaction_watch.py  # no-agent cron 워처 (있으면)
│   └── scenario.sh       # 필수(배포용). 샌드박스 검증 시나리오
├── prompts/  configs/    # 선택 (LLM 프롬프트, 스킬별 설정 등)
└── deploy.sh             # 선택 (스킬 자체 배포 훅)
```

## SKILL.md frontmatter (파이프라인 lint가 강제)
- `name`: 디렉터리명과 정확 일치 — `^[a-z0-9][a-z0-9-]{1,40}$`
- `description`: 10자 이상. 라우팅 근거이므로 트리거·게이트·READ/mutate 구분 명시
- `version` / `license` / `metadata.hermes.tags`: 권장

## scenario.sh 계약 (샌드박스 통과 조건)
- `env -i HOME=… AUTOPHAGY_DEMO_SECRET=DUMMY-…` 격리 환경에서 실행 — **실시크릿 미주입**.
- 성공 시 stdout에 `SCENARIO-PASS` + exit 0. 그 외 전부 실패.
- 참조 구현: [hello-autophagy/scripts/scenario.sh](hello-autophagy/scripts/scenario.sh).

## 스킬 목록 (18 기능 + hello-autophagy 데모 = 19 디렉터리)
calendar · coordination · mail · budget · repair · patent-prep · proposal · report ·
topics · prompt · meeting · recall · wiki · todo · doctype · procurement · speechtotext ·
plaud · (hello-autophagy=데모).

## 예외 매트릭스 (표준 계약 밖)
- 승인 워처 보유: calendar(외부 참석자 초대) · coordination · mail(triage+digest 2개) · budget · patent-prep(export confirm). wiki의 새 배포·확인 워처는 은퇴했으며 저장 형식·지식 조회 라이브러리만 보존한다.
- CLI 명명 예외: mail=`triage_cli.py`, coordination=`coordinate_cli.py`, procurement=`procure_cli.py`+`procure_registry_cli.py`(2개), recall=`recall_cli.py`+`recall_reference.py`(2개 — 후자는 소유자 참고자료 폴더 조회. `recall_cli.py` 가 250 pure-LOC 상한에 정확히 붙어 있어 서브커맨드를 넣으면 F2 등록부에 자기 예외를 쌓게 된다).
- meeting: 워처 대신 게이트웨이 **플러그인 훅**(`meeting/plugin/plugin.yaml`).
- plaud: **읽기 전용** 상태 보고만(`plaud_cli.py status`, stdlib 전용). 워처·승인 카드·재게시는 스킬이 아니라 `automation/plaud_sync/`(홈 배포물 `plaud_sync_watch.py`)가 소유한다 — 스킬은 그 상태 파일을 읽을 뿐이다.
- repair: repo-local CLI 아님 — `~/.hermes/repair/automation/repair/repair_cli.py` 외부 런타임 호출.
- wiki: 본문·제목은 owner DM 밖 유출 금지. 트윈 판단은 `review_after` 만료 시 자율 행동에 사용 금지(`docs/guide/decision-twin-스키마.md`).
- automation 공유 코드: calendar/coordination/mail/budget/wiki/meeting/todo는 `automation.interop.*`(injection_adapter · discord_transport · coordination)에 의존 — interop 시그니처 변경 시 동반 갱신. Hermes 전용 도구 호출 게이트는 은퇴했다.
- **지식 읽기 경계(R3)**: 스킬은 Obsidian/wiki/RAG를 직접 검색하지 않고 `automation.knowledge` 파사드만 호출한다. wiki 자체 CLI, rag_ingest, twin_* 쓰기 측만 예외다. `search_memory()`/`query_notes()`/`consult()` 직접 호출과 스킬별 `[En]` 출처 렌더 구현은 금지하며 `tests/unit/test_knowledge_adoption_conformance.py`가 강제한다. 정본은 [지식 계층 규약](../docs/guide/지식-계층-규약.md)이다.

## 이 디렉터리의 필수 규칙
- **카드 대상은 릴리스와 계정 밖 경로뿐이다.** 계정 안 일정·문서·할 일·Obsidian 쓰기는 소유자 Discord 명령에서 시작해 dori-gate로 카드 없이 처리한다. 카드 대상은 draft/pending → render → resolve_reaction → watch cron을 거친다. 게이트 재사용: 새 승인 표면은 레코드의 `channel_id`로만 분기 — 별도 워처 신설 금지.
- **도메인 중첩 스킬은 결정론적 코드 가드 필수.** SKILL.md 라우팅 설명만으로 부족(LLM 확률적).
  가드는 **양방향**이어야 한다 — 한쪽만 막으면 두 경로가 동시 발동해 이중 쓰기(선례 사고
  2026-07-20: `peer-test 오전 10시`가 calendar 07-22 + coordination 07-29 이중 등록).
  calendar↔coordination은 공유 판정 `calendar_routing.classify_meeting_request`
  (calendar|coordination|clarify)를 두 CLI가 함께 강제: 정확-단일-시각=calendar,
  피어+범위+조율의사=coordination, 모호=clarify(fail-closed). calendar는 비-calendar를
  exit 4 `ROUTING-REJECT`/`ROUTING-CLARIFY`로, coordination은 정확-단일-시각을 피어 질의
  전 exit 2 `ROUTING-REJECT`로 거부. READ는 가드 대상 아님.
- **Drive 산출물 발행은 `automation.drive_outputs` 파사드만 경유**: 스킬 안에서 `gws drive`를 직접 부르거나 발행 헬퍼를 사본으로 들고 있는 것은 금지다(사본이 갈라지면 중복 파일이 다시 쌓인다). 폴더 구조·기간 키·단일 사본 upsert·번들/companion 규약은 [docs/guide/drive-publish.md](../docs/guide/drive-publish.md)가 단독 소유한다. 강제: `tests/unit/test_drive_outputs_conformance.py`.
- **cron 워처 파일명은 `~/.hermes/scripts/` 안에서 스킬별 고유** (예: `calendar_confirm_reaction_watch.py`).
- **추적 config는 시드 전용** — 스킬 런타임 상태(mode 재판정 등)는 ~/.hermes 등 체크아웃 밖에 기록한다. 런타임 경로가 시드/체크아웃 경로를 가리키면 코드 가드로 fail-closed 거부(선례: mail triage_mode._runtime_path_shadows_seed).
- 상세: [docs/guide/스킬-제작.md](../docs/guide/스킬-제작.md), [docs/guide/watcher-cron-설계규약.md](../docs/guide/watcher-cron-설계규약.md).
- **결과 통지는 지시가 온 채널의 스레드로 — `automation.interop.origin_notice` 경유 (2026-08-23, 소유자 지시)**: 승인 게이트 스킬의 실행/취소/만료 통지는 레코드의 `origin_channel_id`/`origin_message_id`(mutating CLI `--origin-channel-id`/`--origin-message-id`, 승인 해시 밖)로 원 채널 스레드에 게시하고, 없으면 기존 경로(소유자 DM/저장된 승인 채널)로 폴백한다. 승인 요청 자체는 2026-09-01 부터 `#agent-chat` 의 **요청별 스레드**다 — `resolve_new_binding(request=RequestThread(title, origin_channel_id, origin_message_id))` 로 열고 레코드에 `approval_thread_id`(승인 해시 밖)를 저장하며, 결과 통지는 그 스레드로 돌아와 `deliver(..., outcome=ThreadOutcome.DONE|CANCELLED|EXPIRED)` 로 종결 표시(접두어+아카이브)한다. 마스킹 kind(calendar·wiki·patent-prep·obsidian)는 스레드 이름에 id 만. 강제: `tests/unit/test_request_thread_adoption_conformance.py`, 상세는 루트 AGENTS.md 「요청별 승인 스레드 규칙」. 문구는 대상·id·사유 포함, 통지는 best-effort(`NOTIFY-THREAD-FAIL`/`NOTIFY-FAIL`/`NOTIFY-SKIP`). 스킬의 마스킹 규칙이 우선한다(calendar=draft id만, wiki·patent-prep=제외). 자체 스레드 생성 코드 금지. 강제: `tests/unit/test_origin_notice_adoption_conformance.py` — 상세는 루트 AGENTS.md 「결과 통지 원채널 스레드 규칙」.
- **새로 만드는 소유자 대면 발신자도 `automation.interop.owner_message` 봉투를 반드시 경유한다 (2026-09-09, 소유자 지시)** — 절차는 [스킬 제작 가이드 「소유자 메시지 계약」](../docs/guide/스킬-제작.md#소유자-메시지-계약-전-스킬-필수--2026-09-09).
- **게이트 대상 외부효과는 카드 생산자 + ✅ 워처 + 영수증을 한 벌로 만든다 (2026-10-07, 소유자 지시)** — 채팅 메시지의 ✅ 는 승인이 아니다. SKILL.md 는 채팅 ✅ 나 텍스트 승인을 요청하라고 쓰지 않고, 전용 요청 명령이 없는 작업은 공용 `python3 -m automation.effect_request request` 로 카드를 올린다(2026-10-17, "승인 경로가 없다"로 끝내지 않는다). 강제: `tests/unit/test_gated_effect_paths_conformance.py`, 런타임 가드 `automation/interop/chat_approval_guard.py`. 절차는 [스킬 제작 가이드 「승인 경로 완결」](../docs/guide/스킬-제작.md#승인-경로-완결-전-스킬-필수--2026-10-07).
- **승인 카드를 게시한 CLI 는 직후 `APPROVAL-THREAD <키>=<값> url=<링크>` 한 줄을 낸다 (2026-09-23, 소유자 지시)** — `automation.interop.thread_pointer.approval_thread_line` 만 쓰고 SKILL.md 가 답장에 그 URL 을 싣게 지시한다. 좌표를 낼 수 없는 스킬은 사유를 conformance 원장에 적는다. 강제: `tests/unit/test_approval_thread_pointer_conformance.py`, 절차는 [스킬 제작 가이드 「승인 요청 답장 링크」](../docs/guide/스킬-제작.md#승인-요청-답장-링크-전-스킬-필수--2026-09-23).
- **승인 메시지를 게시하고 `message_id`를 저장하는 모든 스킬은 `automation.interop.approval_lifecycle.request_owner_approval`을 경유해야 함**: 이는 `dm_message_id`, `confirm_message_id` 등을 포함한 모든 소유자 승인 표면에 적용됩니다. 각 스킬은 `<skill>_approval.py` 어댑터를 통해 파사드를 호출하며, 저장소 참조는 lazy import + `AUTOPHAGY_REPO_ROOT` `sys.path` 삽입을 사용합니다. `ImportError` 발생 시 무가드 게시로 폴백하지 않고 요청을 거부(fail-closed)해야 합니다. 강제 수단으로 `tests/unit/test_approval_lifecycle_conformance.py` 준수 테스트가 실행됩니다.
