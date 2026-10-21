# DONE

> 구현·검증이 끝난 기능. 현황판은 [features.md](features.md), 열린 잔여 부채는 [follow-ups.md](follow-ups.md).

> 항목당 설명 최대 3줄. 증적: `docs/qa/<wave-id>/`.

### 기반 인프라 (Wave 0)

- **repo 골격 + 시크릿 방어 (W0-1)** — autophagy-agents repo 초기화(원격 private),
  gitleaks pre-commit 훅으로 토큰 커밋 차단.
- **레거시 시스템 철거 (W0-2)** — 구 OpenClaw/LiteLLM/vLLM 등 양 노드 레거시 전면 철거(전량 백업 선행),
  보존 대상(cha_wiki) 무손상 확인.
- **클린 인벤토리 + 노드 역할 확정 (W0-3)** — 프로덕션=`<primary-node>` / 개인 RAG=`<rag-node>`,
  설치별 예약 포트와 메모리 게이트 확정.
- **계정 구조 (W0-4)** — agent/peer/ops 계정 + linger, 시크릿 상호 격리(6방향 거부 검증),
  deploy-key 배포 체크아웃.
- **Discord 서버 + 봇 3개 (W0-5)** — Autophagy Lab 서버 + 채널 4개(#team/#agents-log/#approvals/프로젝트),
  봇 3개(에이전트/피어/허브) Message Content Intent ON.
  (2026-07-20 아키텍처 변경: #approvals는 박사별 개인 서버로 이관 — `docs/guide/discord-server-architecture.md`)
- **API 키 + GWS OAuth (W0-6)** — Z.AI 키 + Google OAuth(캘린더/Sheets/Drive/Gmail, scope 9종;
  2026-07-22 In production 게시 + Tasks 추가로 10종 — `docs/patch/2026-07-22-gws-oauth-tasks-inproduction.md`),
  승인 게이트 경유 첫 실발송(approvals.jsonl 첫 엔트리).
- **기관메일 read 경로 검증 (W0-7a)** — cha WIP emailAutomation(mailon.kr) 채택 + aarch64 이식.
  실측에서 inbox selector 불일치로 read 경로 no-go 판정. send 경로는 W0-7b/c에서 완성·**full-go**.
- **기관메일 send 완성 + 실발송 판정 (W0-7b/c)** — mailon `send` TDD 구현(RED→GREEN) + 통제된 실발송 1회 → go/no-go 최종=**full-go**(read no-go와 독립). [소개](기능소개/기관메일-mailon-리포통합.md), 증적 `docs/qa/W0-7b/`·`docs/qa/W0-7c/`.
- **Kanban 보드 결정 (W0-8)** — Hermes 내장 Kanban 채택(개인용 요건 충족 판정),
  ARM64 폴백 보드 후보 확인.
- **OpenClaw 폴백 스모크 (W0-9)** — user-space Node 경로로 ARM64 설치 검증, 2-에이전트 위임 실증(1·2단계 PASS).
  W1-5 실패 대비용 — 게이트 PASS로 미발동.
- **과제비 원장 Sheet (W0-10)** — 2026-10-22 형의 요청으로 budget 스킬과 함께 제거([과제비 감시 제거](기능소개/과제비-감시-제거.md)).

### 코어 시스템 (Wave 1)

- **LiteLLM 게이트웨이 (W1-1, 과거 기록)** — 2026-10-19 소유자 "이거 아예 삭제해 litellm" 결정으로 폐기. 아래는 당시 구현: `glm-main` 별칭 + agent/peer 가상키,
  월 `<monthly-soft-cap>`/`<monthly-hard-cap>`, patent-sensitive 태그 GLM 차단 라우팅.
- **내 Hermes 에이전트 (W1-2)** — Discord DM 한국어 왕복(≤10s), 메인=LiteLLM glm-main,
  승급=openai-codex(ChatGPT 구독 OAuth) 라우팅 실증.
- **provision 레시피 + 테스트 피어 (W1-3)** — `provision-agent.sh`(멱등)로 peer 인스턴스 기동,
  구조적 격리 검증. 온보딩 킷(W3-5)의 핵심 원료.
- **개인 Kanban 웹 보드 (W1-4)** — Hermes dashboard user unit, LAN 바인딩+인증 필수,
  상태 전이 30초 내 반영(Playwright 검증). Tailscale 전환은 cha 후속.
- **봇간 인터롭 게이트 PASS (W1-5)** — 위임 왕복(A)·보고 교환(B) 각 3회 연속 무인 성공 —
  "타 에이전트와 상호작용" 핵심 가설 검증 완료. 폴백(W1-5F) 불필요.
- **인터롭 규약 v0 + 안전장치 (W1-6)** — 보고/질의 포맷(`docs/guide/interop-규약.md`),
  루프 가드(5회/분+dedup), 킬스위치(`!pause-agents`), E2E 주입 어댑터.
- **healthcheck 모니터 + 일일 비용 리포트 (W1-7)** — 양 노드 read-only 모니터 + ops cron(TZ=Asia/Seoul) + 일일 비용 리포트 DM(계정 격리 spend 접근, `<monthly-soft-cap>` 초과 경보). 증적 `docs/qa/W1-7/`.
- **스킬 장착 파이프라인 (W1-8)** — 샌드박스(peer, 더미 시크릿) 검증 + 본인 승인(개인 서버 #approvals) 후 프로덕션 장착. 증적 `docs/qa/W1-8/`. → [소개](기능소개/스킬-게이트-단일-승인-메시지.md)

### 개인 메모리 (Wave 2)

- **개인 RAG 인프라 (W2-1)** — RAG 노드에 bge-m3 임베딩(로컬 전용) + Qdrant(`personal_cha`) +
  MCP 메모리 서버(Bearer 인증). 무키/오키 접근 401/403 거부, 재부팅 자동 기동.
- **Obsidian 승인형 쓰기 어댑터 (repair `t_1b8aab9b`)** — RAG mirror와 분리된 clone에서 PARA 노트 한 건만 commit하고, 경로·제목·본문에 hash-bound 된 owner 승인 없이는 push하지 않는다. 원격 read-back hash로 성공을 검증한다. → [소개](기능소개/obsidian-write.md), 증적 `docs/qa/RTS-2/a3-obsidian.txt`, `docs/qa/RTS-2/a4-gate.txt`. **단, 실제 볼트에 대한 쓰기는 아직 한 번도 수행되지 않았다** — `-rw` 키 등록까지만 끝났고 티켓 `t_1b8aab9b`은 `blocked`로 열려 있다(아래 「신규 아이디어」의 종단 검증 항목).
- **문서 저장 목적지 라우팅 (repair `t_929ca5ad`·`t_783134b5`)** — 저장 요청을 산문이 아닌 결정론 코드로 판정해 개인노트=Obsidian 단독, 목적지 미지정=본인 Drive 비공개, 저장 의도 없음=미저장으로 보낸다. 모호하면 `SAVE-CLARIFY`로 **부작용 0건** exit 5. 기존 “Obsidian·Drive 항상 동시 저장” 규칙은 폐기. → [소개](기능소개/저장-목적지-라우팅.md), 증적 `docs/qa/RTS-1/a1-routing.txt`, `docs/qa/RTS-2/a2-cli-guard.txt`. doctype v1.3.1은 라이브 마운트됐으나(`5520ecd2`) 티켓 `t_929ca5ad`는 아직 `todo`다 — 개인노트 분기가 Obsidian 실쓰기에 의존하기 때문.
- **개인 위키 + 위키 관리 스킬 (W2-2)** — `~agent/wiki` md 볼트, DM "위키에 정리해줘" → 초안 → 본인 확인 후 저장. 확인 메시지는 승인 키당 1건만 살아 있고 저장된 id는 덮어써지지 않는다. 증적 `docs/qa/W2-2/`. → [소개](기능소개/위키-단일-승인-메시지.md)
- **회의록 인제스트 (W2-3)** — 업로드 → 액션아이템/마일스톤 추출 → 내 Kanban 카드, 타인 건은 #team 규약 통지. 증적 `docs/qa/W2-3/`.
- **개인 RAG 인제스트 (W2-4)** — 위키/노트/팀 지식(#team·회의록·피어 보고)을 내 관점 메타로 `personal_cha`에 누적. 증적 `docs/qa/W2-4/`.
- **recall 스킬 (W2-5)** — 질문 시 RAG 검색 → 컨텍스트 주입 + 출처 표기, 미적재 사실은 "기억 없음" 명시. 증적 `docs/qa/W2-5/`.
- **W2 통합 E2E + 시나리오 뱅크 (W2-6)** — 회의록→카드/마일스톤→위키→RAG 색인→recall 체인을 `tests/e2e/scenarios/w2-personal-memory.yaml`로 기계판정, `run_bank.sh --all` exit 0(happy + 실패 3종 격리). 이후 웨이브 회귀 기반. 증적 `docs/qa/W2-6/`.

### 일정·인터롭 (Wave 3)

- **gws 캘린더 스킬 (W3-1)** — 자연어 일정 CRUD, 변경은 DM 확인 경량 게이트 + approvals.jsonl 기록. 동일 일정 확인 DM은 1건만 유지하며 legacy JSONL도 무마이그레이션으로 읽는다. 증적 `docs/qa/W3-1/`. → [소개](기능소개/캘린더-조율-단일-승인-메시지.md)
- **캘린더 승인 복구 (repair `t_3011a5f7`)** — 승인 워처 실패 전파 + Discord 중복조회 제거(HMAC 일회용). → [소개](기능소개/캘린더-승인-복구.md)
- **리마인더 폴러 (W3-2)** — 이벤트 60분 전 + 마일스톤 D-3/D-1/D-day DM, cron+SQLite 멱등. 증적 `docs/qa/W3-2/`.
- **에이전트간 일정 조율 (W3-3)** — 가용시간 규약 협상 → 양측 승인 → 확정, deadlock 규칙 포함. 슬롯별 owner-confirm DM은 공유 승인 생명주기로 단일화. 증적 `docs/qa/W3-3/`. → [소개](기능소개/캘린더-조율-단일-승인-메시지.md)
- **팀 에이전트 보고 허브 (W3-4)** — #agents-log 규약 보고 수집(콜렉터+SQLite) + 웹 대시보드(에이전트별/상태별 조회). 증적 `docs/qa/W3-4/`.
- **온보딩 킷 (W3-5)** — 타 연구원이 자기 인프라/키로 에이전트를 구축해 규약에 편입하는 패키지(리허설 검증). 증적 `docs/qa/W3-5/`.
- **W3 통합 E2E + 뱅크 등록 (W3-6)** — `run_bank.sh --all` exit 0(w2+w3 누적 13케이스 PASS), 401 실패주입으로 보고 경로·멱등·캐스케이드 안전 증명. 증적 `docs/qa/W3-6/`.
- **Google Tasks owner-DM 승인·일회 실행 (W3-7·RTS-6)** — 동결 argv 요청→owner-only ✅/⛔ 워처→불변 generation archive→경쟁 안전 claim→Tasks 재조회 receipt를 단일 경로로 강제한다. 재실행·`write_started` 복구는 외부 호출 0으로 닫히며, archive 후 pending 정리가 중단돼도 동일 terminal 전이만 멱등 재개한다. → [소개](기능소개/google-tasks-승인-쓰기.md), 증적 `docs/qa/RTS-6/04-a1-ssot.txt`~`09-review-fixes.txt`.

### 행정 자동화 (Wave 4)

- **기관메일 읽기 래퍼 (W4-1)** — `mail_wrapper.py`(list/get/classify/status), READ-ONLY 가드로 send/compose/reply 차단. full-go 분기 확정, classify는 메타데이터만(LLM 무경유), W1-8 마운트. 증적 `docs/qa/W4-1/`.
- **수신메일 분류→초안→승인→발송 (W4-2)** — `triage_*.py` + `mail-triage-watch` cron. 6단계(민감도 게이트→분류→한국어 초안→#approvals→동결 argv send→approvals.jsonl), cha 실 승인 발송 2건 라이브 확인. 증적 `docs/qa/W4-2/`.
- **과제비 감지 → 요청메일 (W4-3)** — 2026-10-22 형의 요청으로 제거([과제비 감시 제거](기능소개/과제비-감시-제거.md)).
- **수신자 이름→이메일 해석 (mail resolve)** — 기관 웹메일 자동완성 기반 read-only 조회, 후보 0=fail-closed / 1=사용 / 2+=소유자 선택. `mail_wrapper.py`(SKILL.md v1.5.0, `2f3cae3`).
- **기관메일 첨부 발송 (repair `t_c6f9e704`)** — 승인형 메일 첨부 업로드(서버 UID 검증) + 오류 분류 정정. → [소개](기능소개/기관메일-첨부-발송.md)
- **W4 통합 E2E + 뱅크 (승인 게이트 집중) (W4-5)** — `run_bank.sh --all` exit 0(w2+w3+w4 누적 7시나리오), 분기별 승인 카운트 검증 + NEGATIVE 전부 0-send. 증적 `docs/qa/W4-5/`.

### 연구 산출물 (Wave 5)

- **프롬프트 라이브러리 (W5-1)** — `!prompt search/get/add`, 민감 본문은 700 경로 분리 저장. 증적 `docs/qa/W5-1/`.
- **주간 연구동향 리포트 (W5-2)** — 키워드 등록 → arXiv 주간 요약 DM + RAG 적재. 증적 `docs/qa/W5-2/`. (이력: 이후 노드에서 제거, 주간 연구동향 보고서와 키워드는 공도리가 맡는다.)
- **노트→보고서→슬라이드→대본 (W5-3)** — `!report`/`!slides`(reveal.js)/`!script` 파이프라인, 산출물은 700 경로. 증적 `docs/qa/W5-3/`.
- **제안서 작성 지원 (W5-4)** — 섹션 구조 Kanban 관리 + 초안/취합 + 최종 검토 승급. 증적 `docs/qa/W5-4/`.
- **제안서 페이지 시각 검토** — 최종 HWPX를 SHA별 페이지 PNG·PDF로 펼쳐 에이전트가 전 페이지의 제목 고립·표 분할·공백·밀도를 직접 확인한다. → [소개](기능소개/제안서-페이지-시각검토.md)
- **제안서 노드 자율 구동** — 노드 agent가 OpenAI 키 없이 Codex OAuth로 그림을 만들고(`PROPOSAL_IMAGE_TRANSPORT=codex`), 핀 고정 엔진·윤문 호스트를 git bundle 프로비저너로 받으며, 사용자공간 chromium으로 페이지 검토까지 스스로 돈다. → [소개](기능소개/제안서-노드-자율-구동.md)
- **릴리스 승인 자동 완결** — 소유자 ✅ 하나로 서명 태그 컷→노드 수렴 대기→`deploy_all.sh --apply`→영수증까지 이어진다. `release.sh`가 태그 뒤 스스로 전량 반영을 부르고(`--no-deploy` 옵트아웃), 워크스테이션 `systemd --user` 2분 타이머(`release_complete.sh`)가 이미 승인된 요청만 완결한다(요청 게시 없음, sha별 3회 상한, 서명키는 워크스테이션에만). → [소개](기능소개/릴리스-승인-자동-완결.md)
- **특허·기술이전 준비 (W5-5)** — 발명 신고서/선행기술 초안, 전 호출 patent-sensitive 강제(GLM 차단). 암호화 Drive 백업 승인은 slug당 1건만 유지. 증적 `docs/qa/W5-5/`. → [소개](기능소개/patent-export-single-live-approval.md)
- **W5 시나리오 뱅크 등록 (W5-6)** — W5 5종 시나리오 + 오프라인 드라이버/액터, `run_bank.sh --all` exit 0(누적 12시나리오), 각 스킬 보안 관측치 PASS. 증적 `docs/qa/W5-6/`.

### 자가수리·운영 (Wave 6)

- **오류 → 수리 티켓 (W6-1)** — 오류/헬스체크 실패 시 Kanban '수리' 티켓을 기본 `ready`로 생성한 뒤 즉시 `blocked/needs_input`으로 전환한다(레닥션 발췌). → [소개](기능소개/수리-티켓-needs-input-초기화.md), 증적 `docs/qa/W6-1/`.
- **수리 에이전트 (W6-2)** — 진단 → RED 재현 → 패치 diff → 샌드박스 회귀 100% → 본인 승인 → 적용+patch 문서. 승인 요청은 티켓당 1건만 살아 있다. 증적 `docs/qa/W6-2/`. → [소개](기능소개/수리-단일-승인-메시지.md)
- **회귀 뱅크 자동 성장 (W6-3)** — 수리 시나리오 누적 + 주 1회 정기 실행, 뱅크 실패 시 신규 패치 차단. 증적 `docs/qa/W6-3/`.
- **자가학습 스킬 생성 감독 (W6-4)** — 반복 작업 감지 → 스킬 자동 제안(주 3건 상한), W1-8 파이프라인 강제. 증적 `docs/qa/W6-4/`.
- **운영 문서화 + 재부팅 복구 (W6-5)** — 운영 가이드/복구 절차/장애 대응 트리, 재부팅 실기 검증. 증적 `docs/qa/W6-5/`.
- **수리 보고 경로 재설계 (RRO)** — 티켓 종결 보고가 capability 바인드 enum-only 큐 → agent 소유 소비자(`repair-report-consumer` `*/5`) 직접 전송으로 바뀌어 유실·중복·누설을 동시에 닫았다. 유실 창은 sentinel로 게이트되는 ops 보정기(`*/10`)가 닫는다.
  → [소개](기능소개/repair-report-redesign.md), [detect 런타임 배포](guide/repair-detect-runtime-배포.md), 증적 `docs/qa/RRO-0/`, `docs/qa/RRO-1/`, `docs/qa/RRO-2/`.

### 의사결정 디지털 트윈 (DT Wave)

- **의사결정 트윈 스키마 v1 + Obsidian RAG (DT-A/B)** — 위키 5필수+트윈 키(kind/authority/provenance 등) 확장, Obsidian PARA 미러링 및 민감 태깅 적재 완료.
- **판단 근거 랭킹 및 추출 (DT-C/D 일부)** — `twin_consult` 랭킹 엔진, 게이트 이력 분석(`observed`) 및 LLM 추출(`inferred`) 파이프라인 구현 완료.
- **Obsidian 실배포 + 위키 스킬 배포 (DT-A7·DT-B7)** — Obsidian 라이브 등록 + rag_ingest 배포 + 최초 인제스트 부트스트랩(DT-A7), 위키 스킬 4단계 게이트 배포 + 리액션 워처 실 ✅ 왕복(DT-B7).

### 관리형 스킬 (MS Wave)

- **관리형 스킬 채널 (MS Wave)** — cha 발행(SSH 서명 태그) → 연구원 동기화(8단계 검증) →
  소유자 승인 활성화, 네임스페이스 격리·회수까지 완비. 카나리 `managed-hello-autophagy` v1~v5로 발행·구독·활성화·회수·롤백을 라이브 검증했다.
  증적 `docs/qa/MS-O/`·`docs/qa/MS-F/ms-f2-final-verification.txt`. → [소개](기능소개/managed-skill-channel.md)

### 산출물 아카이빙 (E Wave)

- **산출물 Drive 아카이빙 (E11)** — **제거됨(retired, 2026-07-31)**. GitHub가 이미 보존하는 추적 문서(.omo/plans·notepads, docs/features·qa·patch)의 중복 Drive 미러를 소유자 결정으로 폐기했다.
  과거 증적 `docs/qa/E11/`·`docs/qa/E12/`는 보존하며, git 미추적 스킬 산출물용 별도 `drive_publish`는 유지한다.
  → [소개](guide/drive-publish.md)

### 인터롭·승인 (AS Wave)

- **승인 표면 단일화 (AS Wave)** — 소유자 전용 승인 = 행위 봇의 오너 DM / 스킬 공급망 승인 = 개인 서버 `#approvals`.
  정책 SSOT 모듈 및 conformance 테스트로 기계적 단일화 완비. → [소개](기능소개/승인-표면-단일화.md),
  계획 [.omo/plans/approval-surface-ssot.md](../.omo/plans/approval-surface-ssot.md), 증적 `docs/qa/AS-0/`~`docs/qa/AS-3/`.
- **배포 샌드박스 자격증명 격리 (NF-1)** — 서명 주입 승인이 실제 오너 DM을 열던 경로를 승인 출처 allowlist로 닫고, 일회용 HOME으로 시크릿 폴백을 차단했다(17스킬 A/B 게이트 선행).
  반복되던 Discord `NOTIFY-FAIL`을 제거하면서 경계 실패의 원인 종류·HTTP 상태는 보존했다.
  → [소개](기능소개/배포-샌드박스-자격증명-격리.md), 증적 `docs/qa/NF-1/`.

### 운영 (2026-07-27)

- **대시보드 비밀번호 교체 절차** — 산문이 아닌 실행 가능한 도구로 고정했다(`automation/credential_rotation/`, 단위테스트 6건). Kanban 해시는 러닝 프로바이더의 `hash_password()`로 만들고 기록 전 self-verify하며, probe 실패 시 두 파일을 자동 복구한다.
  → [소개](기능소개/대시보드-비밀번호-교체.md), 규범 `docs/guide/operations.md` §7, 패치 `docs/patch/2026-07-27-dashboard-credential-rotation.md`.
- **healthcheck env 독립 실행 복구** — crontab env 없이 돌려도 9/9 `ALL_HEALTHY`다. 두 env를 crontab 값으로 기본 해석하고(명시값 우선), 전 항목이 한꺼번에 실패하면 개별 티켓 대신 `INFRA_FAILURE` 한 줄로 끝난다.

### Gmail 발송 수리 (RTS-2)

- **Gmail 승인 발송 게이트 (repair `t_72dba111`)** — owner-DM ✅와 exact argv/action hash를 결속하고,
  발송 직전 본문·수신자·회신 대상·첨부 SHA-256을 재검증한다. → [소개](기능소개/gmail-승인-발송-게이트.md), 증적 `docs/qa/RTS-2/c3-gmail-gate.txt`.
  교훈: 병렬 TDD 중에는 전체 스위트 스냅샷을 baseline으로 삼지 않는다 — 다른 트랙이 RED 단계면 무관한 실패로 읽힐 수 있다(2026-07-29 실측 9건, 전부 아티팩트).

### 스킬 배포 수리 (RTS-3)

- **Peer attestation 자동 재검증 (repair `t_24a0ea01`)** — owner 승인은 시간으로 만료시키지 않고, peer 증명만 TTL을 넘으면 같은 hash·nonce·action·destination을 독립 재검증해 배포를 계속한다. 무효 binding·취소·철회·nonce 재사용은 refresh 전에 차단한다. → [소개](기능소개/peer-attestation-auto-refresh.md), 증적 `docs/qa/RTS-3/d3-attestation-refresh.txt`.
- **개인 고유명사 preflight 통합 (EF5·EF5b)** — Todo·Calendar·Mail의 공통 pre-write guard와 private 감사를 연결했다. Todo·Calendar는 API 재조회로, Mail은 sender의 verified-response 계약으로 성공을 확인하며, 미확정 시 확인은 승인 게이트가 아닌 대화 내 `ENTITY-CLARIFY`다.
  → [소개](기능소개/개인-고유명사-preflight-계약.md), [호출 흐름](guide/personal-entity-preflight.md), 증적 `docs/qa/RTS-2/ef5-gate.txt` · `docs/qa/RTS-3/ef5b-mail-preflight.txt`. 감사 로그·품질 지표(EF6)의 잔여는 PLAN 참조.

### 자가수리 반영 경로·승인 (RTS-4)

- **배포 체크아웃 커밋 거부 + 수리 브랜치 반영 경로** — 수리 자동화가 배포 체크아웃에 직접 커밋해 prod가 git에 없는 코드를 들고 도는 경로를 닫았다. 전용 작업 클론에서 apply·commit → write deploy key로 `repair/t_<ticket>` 브랜치만 push → main 반영은 cha가 머지. 롤아웃 4단계 완료(`08a221d`→`7ea6a8c`→`b86e1df`/`d30b4a1`→훅 노드 설치).
  → [거부 훅](기능소개/배포-체크아웃-커밋-거부.md), [반영 경로](기능소개/수리-브랜치-반영-경로.md), 증적 `docs/qa/RTS-2/h1-2-guard.txt`. 키는 `ProtectHome=yes` 때문에 홈이 아닌 `/srv/autophagy-private/`에 둔다(회귀 고정 `test_repair_push_key_sandbox.py`).
- **수리 승인의 패치 내용 바인딩** — `action_hash`가 `sha256:<canonical(ticket, patch_name, 패치 바이트 sha256, 변경파일·증감 요약)>`가 되고, 승인 DM이 변경 파일 목록·라인 증감·`patch_sha256`을 실는다(본문은 비노출·경로만 안내). 적용 직전 디스크의 패치로 해시를 재계산해 대조하므로 **옛 ✅로 새 바이트를 적용할 수 없다**(`ab28621`→`09807a7`→`2e52ea2`→`1f9da4e`).
  → [소개](기능소개/수리-승인-내용-바인딩.md), 증적 `docs/qa/RTS-4/r2-content-binding.txt`. 구스키마 레코드는 읽히되 인가하지 못하고(미반응이면 교체, 이미 누른 것은 TTL 정리), v1 렌더러를 동결해 `5ef869d` 식 마비를 피한다. 잔여 후속은 PLAN 참조.
  교훈: 수동 QA가 유닛이 못 본 결함을 잡았다 — 실제 `git diff` 출력과 대조하자 `core.quotePath`로 8진 이스케이프된 한글 경로를 파서가 거부했다(수리 `1f9da4e`, 이후 실제 커밋 24건에서 git `--numstat`과 전건 일치).

### 배포 체크아웃 드리프트 가드 (DG-1)

- **배포 체크아웃 지연 감지 (DG-1 A)** — `healthcheck.sh`의 checkout 프로브를 **로컬 실행**으로 바꿔 살려냈다(추가 이래 allowlist+sudoers 이중 rc=126으로 매 tick 죽어 있었다). 이제 ops가 origin보다 **뒤처지면** `git ls-remote`(ref 미기록 = read-only 유지)로 감지하고, 앞서거나 dirty하면 종전대로 잡는다. 원격 불통은 PASS+`BEHIND-UNKNOWN`으로 degrade. 전체 SSH 장애 시 SSH-borne 체크만 세어 `INFRA_FAILURE`를 보존.
  → [소개](기능소개/배포-체크아웃-지연-감지.md), 증적 `docs/qa/DG-1/summary.txt`. 검증 `automation/checkout_mirror_probe.sh`.
- **단일 착지 명령 (DG-1 B)** — `automation/land.sh`가 push와 노드 동기화를 **한 번에** 수행하고, 방금 push한 sha가 실제로 프로덕션에서 돌기 전에는 종결하지 않는다. dirty·behind는 push 전 중단, 체크아웃-ahead는 `format-patch`로 안내(never reset), push 후 불통은 “pushed OK, … NOT converged — re-run”으로 표면화. **사후조건은 DG-6에서 갱신됐다**(아래).
  → [소개](기능소개/단일-랜딩-명령.md), 증적 `docs/qa/DG-1/summary.txt`.
- **마운트 스킬 ABI 가드 (DG-1 C)** — 라이브 스킬 스냅샷이 현재 ops 라이브러리를 아직 호출할 수 있는지 AST+`signature.bind`로 검사한다(AS-3.2가 세 라이브 승인 흐름을 동시에 깬 부류). deploy-skill.sh는 ff-pull 직후, land.sh는 동기화 후 라이브 fleet을 스캔해 **WARN**(오탐이 도구를 죽이므로 판단 불가는 전부 skip). 실 스킬은 clean 통과가 테스트로 고정.
  → [소개](기능소개/마운트-스킬-ABI-가드.md), 증적 `docs/qa/DG-1/summary.txt`. 검사기 `automation/skill_library_abi.py`.

### 배포 스냅샷 + 불변 런타임 루트 (DG-2~DG-6)

- **배포 스냅샷 핀 (DG-2)** — 배포 재료를 상주 체크아웃의 작업 트리가 아니라 **커밋 SHA로 고정된 임시 워크트리**에서 가져온다. 다른 세션의 미커밋 편집이 더 이상 배포를 막거나 오염시키지 못하고, 실행 직전 원격이 움직였으면 명령을 아예 실행하지 않는다(`SNAPSHOT-BLOCK: remote main moved`).
  → [소개](기능소개/배포-스냅샷-핀.md), 증적 `docs/qa/DG-2/snapshot-primitive.txt`. 프리미티브 `automation/origin_snapshot.sh`.
- **불변 런타임 릴리스 (DG-3 · DG-5)** — 프로덕션이 읽기 전용 릴리스(`/srv/autophagy-agent-releases/<sha>`, 0555/0444)와 원자 심링크 `current` 위에서 돈다. cron·스킬·`peer_attest`·`deploy-skill.sh`는 공용 리졸버로 경로를 찾고, 롤백은 `rm current` 한 줄이다. 라이브 롤아웃 완료(healthcheck ALL_HEALTHY); 수리 systemd 유닛 이관(4.4)은 repair 활성화 사이클로 이연.
  → [소개](기능소개/불변-런타임-릴리스.md), 증적 `docs/qa/DG-3/release-store.txt`·`docs/qa/DG-4/fallback-noop-proof.txt`·`docs/qa/DG-5/rollout-partial.txt`.
- **랜딩 계약 이중화 (DG-6)** — 상주 체크아웃이 런타임을 그만둔 뒤에도 옛 계약을 두면 **반대 방향의 사고**가 난다: 프로덕션과 무관해진 미러의 dirty/ahead가 무관한 랜딩을 거부한다. 이제 노드가 모드를 판정해 — 릴리스가 살아 있으면 미러는 `LAND-MIRROR-WARN`(복구 안내는 그대로), 하드 사후조건은 **릴리스가 push한 sha에 도달했는가**로 옮기고, `current` 부재(롤백)면 옛 하드 계약을 그대로 되살린다. 깨진 `current`는 부재가 아니라 손상으로 보고 거부한다. 수렴 중 원격 재확인·최종 `current --verify`로 경쟁 착지를 잡고, converger는 **봉인된 릴리스에서** 호출자가 못 박은 sha로 실행되며 flip은 공유 flock으로 직렬화된다.
  → [소개](기능소개/단일-랜딩-명령.md). 계획 `.omo/plans/deploy-snapshot-runtime.md`, 티켓 kanban `t_8320da14`.

### 다이제스트 실패 알림 수리 (repair `t_7707ebfd`)

- **다이제스트 실패 시 소유자 알림 (repair `t_7707ebfd`)** — 매일 08:00 다이제스트가 어떤 단계(build LLM·deliver DM·runner)에서 실패해도 한 줄 구조화 마커 `DIGEST-FAIL stage=.../retry_safe=.../code=...`(주소·본문 마스킹)를 stdout에 내고 cron `--deliver discord`가 owner DM으로 전달한다. 2026-07-31 glm timeout이 `--deliver local`(전달 대상 0개)에서 소리 없이 사라진 사고를 고친다. cursor는 전달 성공 후에만 기록(재시도 안전), `retry_safe=true`만 인틱 재시도. → [소개](기능소개/다이제스트-실패-소유자-알림.md)
- **다이제스트 분류 실패 fail-open (repair `t_digest_glm_failopen`)** — 메일 한 건의 분류(classify) LLM 실패(glm-5.2 timeout 또는 `no JSON object` 파싱 불가)가 다이제스트 전체를 중단시키던 것을, 요약과 동일하게 항목단위 fail-open으로 전환했다. 실패 항목은 보수적 `🔴 중요` + `⚠️ 분류 실패` 배지로 표면화(플래그 모두 False — 캐린더 미위임), 나머지 메일은 정상 전달·기록. `parse_classification`은 bool을 문자열로 주는 glm-5.2 응답(`"false"`→참) 버그도 `_json_bool`로 차단. 2026-07-31 08:10·23:31 실제 사고 근원. → [소개](기능소개/다이제스트-분류-실패-fail-open.md)

### 스킬 공급망 자율화 (B Wave)

- **✅ 한 번으로 끝나는 스킬 배포 (B-4~B-6)** — 소유자가 `#approvals`에서 ✅ 한 번 누르면 2분 내 마운트되고
  승인 레코드가 CAS로 회수된다. 세션 개입 0회. 2026-08-03 종단 검증: `wiki`·`hello-autophagy` 자동 마운트 후
  `budget`·`calendar`·`mail`까지 ✅ 세 번으로 반영 — **마운트 17개 전부 릴리스 소스와 일치**. → [소개](기능소개/승인-한번-자동배포.md)
- **공급망 승인 재조정 (B-4)** — 재개 직전에 이미 실현된 승인인지 판정해 `retire-done`(회수만)·`run`·`hold`로 가른다.
  MOUNT 뒤 레코드가 남는 창이 실재했고, 그 유령을 매 tick 재배포하면 rate limit이 새 승인까지 막는다. 첫 tick부터
  `procurement`을 잡아 값을 했다. 재조정은 기본값 없는 필수 인자라 호출자가 조용히 뺄 수 없다. → [소개](기능소개/공급망-승인-재조정.md)
- **⑦ 워처 타이머 프로비저너 + 활성화 (B-5)** — 이 워처의 실패 양식은 '성공한 침묵'이라(`ProtectHome=yes`나 게이트
  디렉터리 부재 시 매 tick 0건 + exit 0) 켜기 전에 6가지 전제조건을 확인하고 어긋나면 **정지**한다. 계정·경로는 전부
  유닛에서 읽어 두 번째 사본을 만들지 않는다. 2026-08-02 활성화, 2분 주기.
- **봉인된 릴리스의 배포 provenance (PR #30)** — DG-5가 런타임을 `.git` 없는 스냅샷으로 옮긴 뒤 provenance 가드가
  자율 경로를 매 tick 막고 있었다(41회 연속). 릴리스 트리 **전체**를 커밋 트리와 대조하도록 바꿔 보장을 오히려 강화했다
  (여분 파일·심링크·exec 비트까지). `.origin-sha`는 신원 주장일 뿐이라 믿지 않는다. → [소개](기능소개/릴리스-배포-provenance.md)
- **Discord 429 백오프 + 특권 경계 (PR #31·#32)** — 시스템 최고 권한 경로가 유일하게 백오프 없는 Discord 호출자였고,
  레코드당 반응 조회 2회라 열거 뒤쪽 승인이 조용히 429로 무시됐다(`wiki`가 알파벳 마지막이라 매번). 429만·유한 재시도로
  고쳤다. 그리고 `agent`에 ops/peer 쉘 대신 절대경로 하나만 열었다 — 발동은 인가가 아니다(소스는 릴리스, 승인·증명 재검증).

### 노드 정리 (2026-07~08)

- **산출물 Drive 아카이빙(E11) 폐기 — 노드측 정리까지 종결** — 코드 제거 PR `chore/retire-drive-archive`(머지 `6f2203d`), 노드 cron 2건·래퍼 2개 제거(15→13개), 스킬 3종 재배포(doctype `ac56697b`·report `086aecbd`·proposal `2536bd41`).
  교훈: 공유 라이브러리 이관으로 마운트 스킬이 실제로 깨졌다 — `scan_live_skill_abi`가 lazy import를 못 잡는다(가드 개선 시 반영할 것).
- **메모리 재배치(MC-4) 노드 배포** — 두 cron(`memory-curator-watch`·`memory-relocate-watch`) active, 라이브 회수 2건(MEMORY.md 3086→2739자). 증적 `docs/qa/MC-4/live-roundtrip.txt`.
  라이브에서만 드러난 결함 4건 즉시 수정·배포(`801a891`·`50a98ca`·`d681eaa`·`4e6bb0d`). → [소개](기능소개/메모리-재배치.md)

### 후속 과제 병렬 스윕 (2026-08-04)

- **G0 세션 브랜치 보호 (PR #40)** — linked worktree의 `main` 직접 push만 shared pre-push 훅으로 거부하고 정식 착지·세션 브랜치는 보존했다. → [소개](기능소개/세션-워크트리-브랜치-가드.md), [가이드](guide/session-worktree-branch-guard.md).
- **G1 배포 스크립트 (PR #42·#44)** — 추적 파일만 포장하고 물리 런타임 루트를 고정했으며 `--approve-only`·exact skill·권한 진단과 automation import staging 검사를 정리했다. → [소개](기능소개/배포스크립트-정리.md), [드리프트 탐지](기능소개/스킬-마운트-드리프트-탐지.md).
- **G2 승인 게이트·공급망 워처 (PR #50)** — 반응 판정·삭제 메시지 종결을 단일화하고 실패 지문 백오프·1회 경보·체크아웃 밖 tick 상태를 추가했다. 레거시/unsupported kind는 추측 없이 fail-closed로 남겼다. → [소개](기능소개/승인게이트-공급망워처-정리.md).
- **G3 릴리스 스토어 (PR #43)** — keep-last-5, fresh/re-install blob 검증, 세대·용량 healthcheck를 구현했다. → [소개](기능소개/릴리스-리텐션-검증.md).
- **G4 CI·테스트 위생 (PR #41)** — 개발 의존성 선언·유효한 lint 계약·선택적 vendor 수집·임시 clone 회수·환경 독립 drain 테스트를 고쳤다. 프로덕션 drain 경로에는 자식 `PYTHONPATH` 문제가 없었다. → [소개](기능소개/ci-테스트-위생.md).
- **G5 승인 표면·메일 다이제스트 (PR #47)** — tokenless 주입 API 경계와 격리 시나리오를 명시하고 GLM JSON/thinking 계약·항목 단위 재시도를 구현했다. 라이브 배포·canary만 열린 체크리스트로 남는다. → [소개](기능소개/승인표면-메일다이제스트-정리.md).
- **G6 entity-preflight 운영 배선 (PR #48)** — PII-free 품질 지표를 기존 주간 리포트에 연결하고 private 30일·operational 180일 보존정책을 적용했다. → [소개](기능소개/entity-preflight-품질지표-리텐션.md).
- **G7 관리형 스킬 백로그 (PR #46)** — 라이브 심링크 기반 활성 digest 기록, 자기 digest 회수의 발행 전 거부를 구현하고 승인 로그의 물리 분리는 안전 경계로 유지했다. → [소개](기능소개/관리형스킬-백로그-정리.md).
- **G8 LOC 등록부 (PR #51·#52)** — 최종 HEAD에서 29개 초과 파일을 재측정·등록하고 양방향 등록부 계약을 5개 회귀 테스트로 고정했다. → [소개](기능소개/loc-등록부-재측정.md), 증적 `docs/qa/F2/module-loc.txt`.
- **기능 현황판 DONE 재정렬 (PR #54)** — 비연대기 순서를 전용 순수-이동 커밋으로 바로잡아 분류 감사의 마지막 잔여를 종결했다. H6는 요약표 label·GitHub 앵커·열린 건·최고 심각도를 1:1 대조하는 A7 가드로 재발을 막았다.
- **관측 미러와 독립된 release-stale 가드 (H1)** — release-stale은 미러에만 얹힌 무가드 상태가 아니었고, 독립 릴리스 프로브와 같은 tick 중복 억제가 이미 구현돼 있음을 실측 정정했다. → [소개](기능소개/관측-미러-자동-수렴.md), [독립 프로브](기능소개/헬스체크-릴리스-관측성.md).
- **H5 위생 배치** — F2 vendor lint 상시 red, mail·wiki 격리 스모크 부재, AS-R3 S5·자격증명 복구 문서 부채를 종결했다. → [소개](기능소개/H5-위생-배치.md), 증적 `.omo/evidence/fs2/task-5-parallel-followup-sweep-2.txt`.
- **실측 정정 — 메모리·승인**: KEYSTONE 백업명 충돌은 `30c9338`로 해소됐고, 승인 IPC flake는 PR #14에서 timeout 근거를 확정했다. `USER.md`는 승격 진행 뒤 97.9%가 아니라 **79.0%**다.
- **실측 정정 — 배포·CI**: 마운트 ABI 가드는 이미 존재하며 열린 것은 WARN→strict 정책뿐이다(PR #42). drain flip은 테스트 하네스 전용이고 Ruff F401은 재현되지 않았다(PR #41). 배포전 automation import 검사는 PR #44로 추가됐다.
- **실측 정정 — 종결·조치 불요**: E11 폐기 가드는 PR #12에서 해소됐고 features.md 자동 포맷터는 존재하지 않는다. 설치된 특권 헬퍼의 환경 드리프트는 실제 방어가 릴리스에 있어 다음 provision 때 자연 정렬되는 조치 불요 사항이다(PR #50).
- **배포 체크아웃 self-commit 종결** — 진짜 학습 커밋은 무손실 복구했고 `8610302`의 커밋 거부+드리프트 탐지와 단방향 미러 규칙으로 재발 경로를 닫았다. `--no-verify`는 탐지가 보완하며 SSH allowlist·미러 지연은 설계대로 문서화됐다.

### 후속 개선 (E1–E10)

- **E1 coordination 날짜 계산** — KST 상대일 계산을 결정론 헬퍼로 옮겨 “내일” 범위를 정확히 계산한다(`5b4abe7`).
- **E2 skill review-PASS 게이트** — SANDBOX→REVIEW→APPROVAL→MOUNT 4단계와 hash-bound verdict를 강제했다(`20d7597`).
- **E3 owner-confirm 이모지** — coordination에서 시작한 owner-only ✅/⛔·⛔ 우선·hash-bound 규칙을 모든 confirm 흐름과 고유 워처 파일명으로 통일했다(`053dfa0`).
- **E4 skill-deploy 신뢰 모델** — source-only digest와 요청 본문의 hash-bound review 상태를 도입하고 형식-only LLM 검토는 E7로 대체했다.
- **E5 서류 종류 라이브러리** — 예시에서 작성 요지를 추출해 버전형 문서 종류로 등록·개선하고 서술형 초안을 반복 생성한다. 증적 `docs/qa/E5/`.
- **E6 문서 인박스 DM 워처** — owner DM 첨부를 private inbox로 받아 E5 등록·개선으로 전달하고 dedup·마스킹·fail-closed 파싱을 적용했다.
- **E7 peer 독립 attestation** — peer 샌드박스 바이트 검증과 peer 봇 신원 reply를 owner ✅와 함께 MOUNT 조건으로 강제했다. 증적 `docs/qa/E7/`.
- **E8 재발 클래스 하드닝** — reaction-only polling, cron env/sys.path, 라우팅, secret-scan 오탐을 감사하고 공통 watcher 규약으로 고정했다.
- **E9 root-owned read-only skills** — immutable release store와 read-only bind mount, 고정 root helper로 agent 직접쓰기 지속성 우회를 차단했다.
- **E10 mail digest·CC 인식** — stale release를 재배포하고 CC 수신 메일의 자동 회신 초안을 억제하되 일정 위임은 유지했다.


### 저장·라우팅 스윕(RTS) 종단 검증 (2026-08-04 확인)

- **Obsidian 승인 쓰기가 라이브에서 실증됐다** — 오래 “미수행”으로 남아 있던 항목이나 실측은 반대였다. `-rw` 키(`obsidian_write_key`)로 개인 볼트 `orientpine/git-obsidian`에 노트 **5건**이 upsert돼 `000_PARA/Resource/` 아래 저장됐고(2026-07-31~08-03), 클론은 `origin` 대비 `+0 -0`이다 — PARA 결정적 upsert → commit → push → 원격 read-back까지 전 구간이 돌았다는 뜻이다. 쓰기는 `memory_relocate`의 5-게이트(owner ✅ 포함)를 통과해야만 일어나므로 승인 경유도 함께 입증된다.
- **관련 티켓 3건은 이미 종결돼 있었다** — `t_1b8aab9b`(Obsidian PARA 저장·Git 원격 검증) · `t_929ca5ad`(수리: manual-repair) · `t_f92027cb`(저장 라우팅 종단 회귀 테스트) 전부 `status: done`, 2026-07-29 15:08 종료. 문서만 갱신되지 않았다.
- **`memory_relocate`는 노드에 배포돼 cron으로 돌고 있다** — `memory-relocate-watch`·`memory-curator-watch` 둘 다 `hermes cron` 등록 확인. 위 5건의 쓰기가 그 라이브 왕복의 산물이다(MC-4 잔여 해소).
- 교훈: 체크리스트를 문서로만 믿지 않고 노드 상태를 직접 조회하자 **9건 중 2건이 이미 완료**였다. 「커밋됨 ≠ 배포됨」의 반대 방향 — 배포도 끝나고 티켓도 닫혔는데 문서만 낡은 경우다.

### 승인 가독성·메모리 큐레이션 (2026-08-03~04)

- **소유자 결정이 게이트에서 유실되던 문제(429) 종결** — 소유자는 결정했는데 시스템이 그것을 잃었고, 상태만으로는 “아직 안 누름”과 구분되지 않았다.
  ⛔가 승격 레코드로 전파되지 않던 것을 종결 상태 `abandoned`로 옮기고(`effects.draft_present`로 초안 유무 주입, `reconciled`와 구분) 라이브 4건이 한 tick에 정리됐다.
  수리 `docs/patch/2026-08-03-confirm-gate-rate-limit.md`, 가독성 `docs/patch/2026-08-03-promotion-approval-legibility.md`.
- **승인 메시지가 무엇을 인가하는지 읽히게 했다** — 마스킹과 절단을 분리해 승인 요약이 원문 전체와 출처 파일을 싣는다(이전엔 나열용 28자로 잘렸다).
  같은 tick에서 같은 파일의 두 번째 회수가 매번 실패하던 백업명 충돌(tick이 시각 하나를 공유하는데 이름이 초 단위라 필연)도 덮어쓰기 금지를 유지한 채 이름만 비켜가게 고쳤다.
- **오래 대기한 승인이 다시 떠오른다** — 승인 메시지는 건드리지 않고(지운 사이 누른 반응 유실·단일성 규칙 위배) 별도 알림이 링크로 가리킨다. 주기 3시간, 밤 12시~오전 9시 무음(소유자 지시).
  조용한 창은 연기지 취소가 아니며 대기 0건이면 보내지 않는다. 실측: 미처리 2건이 DM 최신에서 51·157번째로 밀려 접근 불가였다.
- **메모리 큐레이터** — Hermes 자체 메모리를 무손실 정리하고 durable 판단을 트윈으로 승격 제안한다(오너-DM ✅, observed/advisory).
  v3 상태 머신이 게시 재시도 → 노트 해시·마커·원본 digest 검증 → 백업 후 단일 항목 삭제를 fail-closed로 수행하고, one-shot 알림은 durable outbox로 cooldown과 독립 전송한다.
  잔여였던 운영 사실 재배치(MC-4)가 끝나 종결. → [소개](기능소개/메모리-큐레이터.md)
- **배포 지침에 자동 재개를 반영** — 승인 ✅ 한 번 뒤의 자동 마운트가 기능소개 문서에만 있어 `docs/guide/스킬-제작.md`와 루트 AGENTS.md에서는 보이지 않았다.
  “신규 스킬도 등록 없이 포함”(`SUPPORTED_KINDS`가 종류 기준)과 예외(유예 시 직접 실행)을 함께 반영했다.
- **메모리 승격 확인 종결(H4)** — 종단 승격의 상태·saved/archive 초안·원격 `action_hash`를 교차 검증한 뒤 확인 메시지에 append-only 처리 완료 표기를 남기고 durable archive→drop으로 재개 가능하게 종결한다.
  불일치·`abandoned`는 `UNBOUND`, 고아 초안은 비변이 dry-run `ORPHAN`으로만 인계한다. → [소개](기능소개/메모리-승격-확인-종결.md)
  “신규 스킬도 등록 없이 포함”(`SUPPORTED_KINDS`가 종류 기준)과 예외(유예 시 직접 실행)을 함께 반영했다.
- **공급망 열거 실패 보존(H2)** — 성공한 빈 열거에서 고아 억제 레코드를 정리하고, 열거 실패에서는 전량 보존하도록 성공 신호를 계약화했다.
  자동 재개 확장 조건은 설계로 고정하고 `SUPPORTED_KINDS`는 `skill-deploy`로 동결했다. → [소개](기능소개/공급망-열거-실패-보존.md)

### 공개 배포 기반 (W-F2)

- **그룹 roster 데이터 모델·검증 (W-F2-A)** — 설치당 단일 그룹의 관리자 1명·멤버·선택적 업데이트 채널을 frozen dataclass로 파싱한다.
  중복 ID·미지 필드·잘못된 principal/OpenSSH 키·중복 YAML 키를 fail-closed로 거부하며 소비자 배선은 후속 wave로 남긴다. → [소개](기능소개/그룹-roster-검증.md)
- **공개 릴리스 컷 (W-F5-A · W-M3 문서)** — `public_export.sh` 1회 실행이 fresh-history 스냅샷 커밋·**그 커밋에** 서명 태그·atomic push를 다 한다 — 순서를 사람이 나눠 할 수 없게 구조로 묶었다(D8).
  실물 릴리스 `orientpine/cytoplasm` `v1.0.0` 완료(내보낸 트리에서 3902 passed, gitleaks 4개 스캔 0건). 절차·신뢰키 회전·나쁜 릴리스 대응은 `docs/guide/manual-maintainer.md`가 소유한다.
  → [소개](기능소개/공개-릴리스-컷.md). W-M3의 나머지 검증(실제 v1.0.1 패치 릴리스 1회 수행)은 계획에서 `[~]`로 유지된다.

### 수리 티켓 스윕-2 (2026-08-17)

- **todo 소유자-DM 승인 경로** — Google Tasks 쓰기가 오너-DM ✅ 사이클을 거쳐야만 실행되고, 일회 claim + 재조회 검증으로 중복·거짓 보고를 막는다. `f128f9a8c343356457119d6ce37f3344488c3968` (배포 샌드박스 런타임 루트 보존 `2bb42a703c3946564760f6dbd7fe3960ea8e17a2`) → [소개](기능소개/todo-승인-경로.md)
- **승인 게시 복구와 강화 저널** — 429 Retry-After 재시도, posting journal에 message/channel 바인딩을 덧붙여 게시 후 크래시에서 고아 승인을 되찾고, 커밋 뒤 리액션 부착으로 순서를 원자화했다. `cdac4722b2d721218718eb4f269d0a6f09c1a575` → [소개](기능소개/승인-게시-복구와-강화-저널.md)
- **2-store 메모리 재배치** — MEMORY.md에 더해 USER.md의 운영 사실도 재배치 대상이 됐다. MEMORY 경로·해시는 바이트 그대로, USER는 `user--` 파일명 namespace로 분리한다. `8640b2b2187174ffe0bb255e08b1671ac92910f7` → [소개](기능소개/2-store-메모리-재배치.md)

### 수리 스윕 3차·개인 서버 대화 (RTS-6, 2026-08-17)

- **개인 서버 자유 대화·스레드 지원** — 채널 응답과 무멘션 스레드 응답을 각각 실증했고 두 관찰 창에서 예상 밖 봇 응답은 0건이었다. `channel_ack`·`thread_unmentioned_ack`·`unexpected_bot` 세 축 모두 green. → [소개](기능소개/개인서버-대화-채널.md)
- **기관메일 발신자·전체 폴더·검색** — owner-DM 카드의 실제 발신자·CC와 비공개 표면 레닥션을 함께 보존하고, 사용자 폴더 수집과 read-only 다중 신호 검색을 추가했다. `3f94b445d39c6d94128e53aa540f54aecf0df1cd` → [소개](기능소개/기관메일-발신자-전체폴더-검색.md)
- **스킬 게이트 peer trust-root 진단 분리** — 신뢰근원 설정 부재를 승인 부재와 다른 오류로 드러내고 봉인 staging 정리를 복구했다. `949532a93e1615da197ce85de9c423bcdf8dbe6e` → [패치](patch/2026-08-17-skill-gate-peer-trust-root-diagnostic.md)

### 병렬 후속 스윕 3차 (FS3, 2026-08-20)

- **스킬 시나리오 단일 러너 조율안 (K2-B)** — `deploy-skill.sh`의 stage 1·post-mount 두 지점이 공용 러너와 어떻게 어긋나 있는지를 실측하고, 편집·실행 두 금지를 함께 풀어야 하는 이유와 풀린 뒤의 검증 설계·자격증명 격리안을 4절 조율안으로 냈다. 코드 변경 0. → [소개](기능소개/스킬-시나리오-단일러너-조율안.md)
- **공개 릴리스 정책 (K5)** — `docs/` 전체를 공개 결정 아래에 두되 기존 공개 경로는 grandfather하고 새 경로만 exclude하며, 공개 컷 버전을 source commit 태그에서 유도한다. rollback floor는 런타임 롤백과 채널 전환에도 유지된다. → [소개](기능소개/공개-릴리스-정책.md)
- **FS3 보드 replay 정합화 (K1)** — 최종 replay가 `ALREADY-FIXED`로 확정한 13개 행을 열린 보드에서 제거하고 원래 묶음에 정정 사유를 남겼다. 요약표는 실측 재계산했으며 총계 문장도 행 합계와 함께 검사한다.
  → [소개](기능소개/문서보드-정리.md), 근거 PR #175·#181·#178·#177·#182·#189·#188·#190, 증적 `.omo/evidence/fs3/task-16-parallel-followup-sweep-3.txt`.

### Discord 공개 표면 노출 제거 (2026-08-21)

- **Discord 공개 메시지 필터링** — 1:1 DM이 아닌 모든 Discord 표면(길드 채널·스레드·미지 chat_type)에서 이벤트 유형 allowlist로 사용자용 이벤트 5종만 내보내고, 스트리밍 초안·tool-progress·reasoning·내부 status·하트비트·백그라운드 원시 출력·승인 원시 명령을 억제한다. 문자열 세척이 아니라 유형 판정이며 미지 이벤트는 기본 비공개다.
  `hermes_compat` 패치 캐리어 2건(`discord-public-message-policy`·`discord-public-approval-details`)으로 정착했고 배포는 owner-gated로 남는다. → [소개](기능소개/discord-공개-메시지-필터링.md), [패치](patch/2026-08-21-hermes-compat-public-message-policy.md)

### Discord DM 실행 라인 숨김 (2026-08-22)

- **DM에서 도구 실행 진행 라인 제거** — 소유자 DM은 공개 표면 정책의 대상이 아니라 `🔎 Searching files …`·`📖 Reading …`·`💻 $ …` 같은 실행 라인이 그대로 보였다. 벤더 노브 `display.platforms.discord.tool_progress: "off"` 를 신규 설치 시드(`automation/provision-agent.sh`)와 노드 agent·peer 라이브 config 에 넣어 의미 있는 문장(interim assistant·최종 응답·승인 요청)만 남긴다. 전역 `display.tool_progress` 는 운영자 CLI 뷰까지 끄므로 쓰지 않았고, 주석 36줄을 지우는 `hermes config set` 대신 주석 보존 삽입 + 파싱 deep-equality 가드로 적용했다. 게이트웨이는 턴마다 config 를 다시 읽으므로 재시동 없이 다음 턴부터 적용된다.
  회귀 `tests/unit/test_provision_agent_display.py`. → [소개](기능소개/discord-공개-메시지-필터링.md)

### 서명 릴리스 태그 누락 즉시 통지 (2026-08-24)

- **태그 누락을 첫 tick에 exact SHA로 통지** — `UNSIGNED-HEAD`면 기존 2분 리컨실러가
  raw main/runtime SHA와 `release-tag.sh --wait`를 한 번 알리고 같은 SHA는 침묵한다.
- **라이브 one-shot 실증** — PR #260의 unsigned `0884af67`에서 첫 tick state가
  `pending_notice=null`이 됐고, 추가 세 tick 동안 key 하나와 서명된 runtime을 유지했다.
  → [소개](기능소개/반복-수렴-스킵-알림.md)

### 음성 녹취 → 전사본 → 회의록 자동화 (2026-08-25)

- **Drive 폴더에 놓인 녹취를 전사본(.md)으로 만들고 meeting 스킬로 이어 회의록까지 생성** —
  폴더에 파일을 놓는 행위가 명시 지시이며, 전사는 기본이 로컬(whisper.cpp)이다. meeting의
  민감도 게이트는 텍스트만 보므로 외부 API를 쓰면 원음이 게이트 전에 나가기 때문이다.
- **장시간 녹취의 조용한 잘림을 코드가 잡는다** — 구간 타임스탬프 합집합을 ffprobe 실제
  길이와 대조해 설명되지 않는 앞/뒤·내부 공백이 있으면 exit 8로 회의록 생성을 막는다.
  25MiB 상한은 API 전용이 되고, API 경로는 15분 창+10초 겹침으로 나눠 이음매만 중복 제거한다.
  → [소개](기능소개/음성-녹취-회의록-자동화.md)

### 전사본 다듬기와 Drive 두 경로 복구 (2026-08-26)

- **전사본을 읽을 수 있게 다듬어 저장한다** — 전사기가 모든 구간을 한 줄(실측 38,216자)로 잇던 것을
  문장·문단으로 나누고, 연속 완전중복만 접고, 용어집으로 고유명사를 바로잡는다. 요약은 하지 않는다
  (그건 meeting 의 몫). 이미 있는 전사본은 `polish` 동사로 오디오 없이 멱등 재정리. → [소개](기능소개/음성-녹취-회의록-자동화.md)
- **Drive 두 경로가 실은 한 번도 동작하지 않았다** — 전사본은 `DRIVE_PUBLISH_ENABLED` 가
  `~/.env.secrets` 에만 있어 CLI 가 못 봤고, 회의록은 마운트 경로에서 `automation` import 가 실패해
  매번 `DRIVE-PUBLISH-SKIP reason=ImportError` 였다. 둘 다 조용한 실패라 릴리스를 넘어 살아남았다.

### Drive 감시 루프 가동과 용어집 (2026-08-26)

- **워처가 실제로 돈다** — `speechtotext-drive-watch`(*/5) 배포. 켜기 전에 이미 처리한 녹취를 상태
  파일에 심어 회의록·칸반 카드 중복 생성을 막았고, 첫 tick 이 `{scanned:1, ingested:0}` 으로 그것을
  확인했다. 매니페스트 행은 `required` 로 승격돼 이제 래퍼 드리프트가 탐지된다.
- **용어집이 채워졌다** — 같은 전사본 안에서 교차확인된 4쌍만 넣어 실측 10건이 교정됐다
  (영무→업무 8, 고실내성→고신뢰성 1, 포스텔→포스텍 1). 교차확인 안 되는 `한정기술` 은 주석으로 남겼다.
  치환이 단순 문자열 교체라 `성금=선금` 같은 항목은 `기성금` 을 깨뜨린다 — SKILL.md 에 명시했다.

### 과제별 용어집과 과제별 산출물 트리 (2026-08-26)

- **자료가 과제 단위로 묶인다** — `publish(project=…)` 가 카테고리와 연도 사이에 과제 한 단을 넣는다.
  과제는 레지스트리 항목이 아니라 인자라서, 주지 않으면 경로가 예전과 동일하다(나머지 6개 스킬 무영향).
  과제를 쓰면 파일이 정확히 depth 5이므로 과제+번들 조합은 `TaxonomyError` 로 거부된다.
- **용어집이 과제별로 Drive 에 산다** — `전사본/<과제>/용어집.txt`. 과제 엔트리가 전역 용어집을 덮어쓴다:
  기관명은 한 과제의 사실이고 일반 오인식은 모든 회의의 사실이기 때문이다. 과제는 파일 이름의
  날짜 아닌 첫 토큰이 정하고 `--project` 로 덮어쓸 수 있으며, meeting 은 체인이 넘긴 같은 이름으로
  회의록을 그 과제 아래 둔다. cha 확인으로 `한정기술=한전기술` 이 확정됐다.
- **단위 테스트가 실제 Drive 에 닿던 구멍을 닫았다** — 용어집 조회가 환경에서 클라이언트를 만들면서
  옵트인을 확인하지 않아, CLI 를 돌리는 테스트만으로 소유자 Drive 에 픽스처 이름의 폴더 4개가 생겼다
  (빈 폴더, 즉시 회수). 이제 `DRIVE_PUBLISH_ENABLED=1` 없이는 클라이언트를 만들지 않으며, 주입
  클라이언트만 예외다(테스트 seam). 회귀는 그 클라이언트 생성을 폭발시키는 테스트로 고정했다.

### Plaud lifelog → Obsidian 동기화 (2026-09-02)

- **MCP 무등록 3계층** — no-agent cron 이 `npx @plaud-ai/mcp@0.3.10` 과 stdio JSON-RPC 로 직접 대화해 녹음
  건별 노트(요약 위+전문 아래)를 동결하고, 건별 ✅(OBSIDIAN_WRITE 재사용) 후 obsidian_write 로 push, RAG 자동
  소비. [소개](기능소개/plaud-lifelog-동기화.md) · `automation/plaud_sync/` · 롤아웃(OAuth 1회·배포)은 소유자 단계.

### v1.1.0 편의 릴리스 (2026-09-03)

- **release.sh --bump 와 MAJOR 기계 판정기** — `next_release_tag <repo> [major|minor|patch]`, `release_plan.major_signals` 가 base..head 의 정책·스키마·필수 설정 변경을 찾아 `--bump patch/minor` 를 거부하고 `--bump major` 패치노트에 `MAJOR: 운영자 조치 필요` 를 싣는다; 손 태그 불일치·prerelease 접미 결함도 함께 닫음. [소개](기능소개/릴리스-버전-자리-선택.md) · `automation/release_tag_lib.sh`·`release.sh`·`release_plan.py`.
- **실행 사본 가드 14/14** — meeting·speechtotext·doctype·proposal·report·procurement·patent-prep·prompt 가 `governed_copy_refusal` 을 채택해 mutating CLI 전부가 live 마운트 밖 사본을 exit 3 으로 거부. [소개](기능소개/실행경로-가드-일반화.md) · `tests/unit/test_governed_copy_guard_conformance.py`.
- **승인 원장 KPI 집계기** — `python3 -m automation.approval_kpi --root <dir>` 가 kind 별 건수·일평균·p50/p95·재요청률과 소스에서 읽은 TTL·리마인더 표를 낸다(읽기 전용). [소개](기능소개/승인-원장-KPI.md) · `docs/guide/approval-kpi.md`.
- **LiteLLM 실제 completion 프로브** — healthcheck 에 `litellm_completion` 행을 더해 liveliness 200 뒤의 상류 429 를 FAIL·수리 티켓으로 드러낸다. 노드 래퍼 설치는 소유자 단계. [소개](기능소개/LiteLLM-실제-completion-프로브.md).
- **후속 과제 5건 해소** — pipeline_lock class CM · RelocationStore 분리 · Drive 폴더 캐시 재검증 · 승인 카드 리액션 best-effort · LiteLLM 프로브. 원문·처리는 [follow-ups-deferred.md](follow-ups-deferred.md) 해소 기록.
- **수렴 중 핫픽스 2건(v1.1.1·v1.1.2)** — 샌드박스 `env -i` 가 `AUTOPHAGY_SKILL_LIVE_ROOT` 를 선언해 가드 채택 스킬이 stage 1 을 통과(PR #377); `release_version_for` 로 재실행이 HEAD 태그를 재사용, coordination 시나리오의 live root 전달, prompt·doctype heredoc 의 scripts 직접 import + `tests/unit/test_scenario_deployed_layout.py`(PR #378). 규약: [스킬-제작](guide/스킬-제작.md) scenario.sh 계약.


### Plaud 녹음 로컬 전사 (2026-09-04)

- **transcribing 스테이지** — 발견된 녹음은 `planned` 앞의 `transcribing` 에 놓이고, 워처가 watch.lock 을 푼 뒤
  `transcribe_live` 가 pipeline_lock(speechtotext 와 공유) 아래 `get_file` presigned URL 로 오디오를 내려받아
  speechtotext CLI(whisper.cpp + sherpa 화자 분리, `SPEECHTOTEXT_BACKEND=local` 고정·Drive 발행 0)로 전사한다.
  전사본은 `~/.hermes/plaud-sync/transcripts/<노트 stem>.md` 에 남아 `meeting_cli.py ingest --file` 이 읽을 수 있고,
  노트 `## 전문` 은 그 전사로 재조립된다. 환경 실패(rc 3/4·MCP·네트워크)는 무카운트 재시도, 녹음 실패 2회면
  클라우드 전사 폴백(출처 줄에 명시). commit 은 watch.lock blocking 재획득 + 상태·hash 재검사.
  [소개](기능소개/plaud-녹음-로컬-전사.md) · `automation/plaud_sync/{audio,transcribe,transcribe_live}.py` · plaud skill v1.1.0.

### Plaud lifelog 노트 v2 양식 — Linter 정합 frontmatter·한눈에·결정 · 할 일·접힌 전문 + 사람·장소·결정·할 일 LLM 추출 (2026-09-04)

- **양식(B안)** — 노트가 소유자 Obsidian Linter(v1.32.0) 가 그대로 두는 frontmatter(tags→title→source→created→modified,
  필요할 때만 따옴표)로 시작하고 `## 한눈에`(녹음·주제·사람·장소·한 줄 Dataview 인라인 필드) → `## 요약`(포스터 이미지 제거) →
  `## 결정 · 할 일`(없으면 생략) → 접힌 `## 전문` → 출처. vault 의 실제 플러그인 빌드를 헤드리스로 돌려 제목 따옴표 규칙 61건과
  렌더 샘플 3종의 멱등(`lint(x)==x`)을 검증했다(`docs/qa/PLV2`). `obsidian_write.render_note` 가 body 선두 frontmatter 를
  제목 위로 올리고 callout 을 생략한다(frontmatter 없는 노트는 바이트 동일). created/modified 는 `PLAUD_SYNC_TIMEZONE` 로컬 시각.
- **추출** — `lifelog_extract(_live)` 가 규칙 파일→patent-sensitive→`LITELLM_AGENT_KEY`→템플릿 순 게이트 뒤 glm-main 으로
  사람·장소·결정·할 일을 뽑는다. 로컬 전사가 들어온 `transcribe.finalize` 에서 돌고(클라우드 초안은 LLM 미호출), 생략 사유는
  한눈에 줄에 적히며, 전송·파싱 실패는 전사 시도로 세지 않고 대기한다. 승인 카드 v3 는 한눈에 줄을 먼저 인용한다.
  [소개](기능소개/plaud-lifelog-노트-v2-양식.md) · `automation/plaud_sync/{lifelog_model,lifelog_fields,lifelog_extract,lifelog_extract_live}.py` ·
  계획 `.omo/plans/plaud-lifelog-format-v2.md`.

### 닫힌 수리 카드의 재발이 새 카드를 연다 (2026-09-04)

- **재발 재발급** — `RepairRegistry.claim` 이 저장된 카드의 상태를 보고 `done`·`archived` 면 새 카드를 발급해
  occurrence 를 1 부터 다시 센다. 본문 `Supersedes closed card: t_…` 로 두 카드를 잇고, 멱등키에 그 id 를 붙여
  `--idempotency-key` 의 "non-archived 동일 키 → 기존 id 반환" 계약(`docs/qa/RRC-0/01-cli-contract.md` ④)을 피한다.
- **읽을 수 없는 보드는 예전 동작** — 상태 조회만 전용 10초 상한을 쓰고(mutation 은 60초 유지), 실패·미주입·판정 불가는
  기존 중복 제거(occurrence 증가)로 떨어진다.
  [소개](기능소개/수리-티켓-재발-재발급.md) · `automation/repair/{repair_core,repair_cli}.py` · 회귀 `tests/unit/test_repair_tickets.py`.

### 용어 교정 위치 이동 — 전사본이 아니라 문서에서 (2026-09-05)

- **위치 규칙** — 교정은 음성→전사본이 아니라 전사본→산출 문서 단계에서 하고, 참고 문서는 **문서 종류별로**
  중첩된다(`autophagy/용어집.csv` → `autophagy/<문서 종류>/용어집.csv` → `…/<과제>/용어집.csv`). 전사본은
  증거라 되돌릴 수 없는 치환을 새기지 않는다 — 근접 교정이 「기성금」을 깨뜨린 적이 있고, 원문에 새겨졌다면
  원래 낱말은 어디에도 남지 않는다. AGENTS.md 「용어 교정 위치 규칙」 · [규약](guide/용어-교정-규약.md).
- **단일 정의** — 엔진·층 해석·감사 로그가 `automation/term_correction.py`·`term_glossary.py`·
  `term_correction_log.py` 셋뿐이고 스킬은 위임한다. 채택 여부는 `tests/unit/test_term_correction_conformance.py`
  가 기계로 대조하고, 붙이지 않은 문서 종류는 사유와 함께 `_EXEMPT` 에 등록된다.
- **채택** — meeting 은 회의록 **본문만** 고치고 부록 「### C. 원문 전사본」은 바이트 그대로 두며, plaud lifelog 는
  한눈에·요약·결정 · 할 일만 고치고 `## 전문` 을 보존한다. speechtotext 는 전사본을 인식된 그대로 남기고
  인식 전 프롬프트 힌트만 유지한다. [소개](기능소개/용어-교정-문서-단계.md).

### 옵시디언 노트 승인 요청이 스스로 승인 요청임을 말한다 (2026-09-08)

- **스레드 이름이 유일한 글자다** — Discord 가 채널에 남기는 "스레드를 시작했다" 시스템 메시지는 이름만 싣는데
  옛 이름 `옵시디언 · <녹음 id>` 에는 '승인' 도 노트도 없었다. 이제 `옵시디언 노트 승인 요청 · <노트 파일명>` 이며
  생산자(plaud·memory_relocate)가 `note_relpath` 의 파일명만 넘긴다. 수리 티켓 t_8a159de8.
- **다른 kind 는 손대지 않았다** — 라벨 델타는 `obsidian-write` 한 줄이고 `POLICY_VERSION` 은 8 그대로라
  저장된 승인 레코드 해석·리액션 워처·리마인더가 무변경이다. AGENTS.md 마스킹 표도 같은 사이클에 갱신됐다.
- **카드가 세 선택지를 나눈다** — v4 첫 줄이 `옵시디언 노트 승인 요청: **<노트 제목>**`, 끝이 승인(✅)/수정 요청/
  취소(⛔) 3줄이다. v3 문구는 동결 재생되고 `action_hash` 바인딩은 불변.
  [소개](기능소개/옵시디언-노트-승인-요청-표시.md) · PR #456·#457.

### 근거 없는 호칭이 라이프로그 파일명·담당자로 굳지 않는다 (2026-09-08)

- **판정은 추출 경계 한 곳** — `lifelog_extract._reference()` 가 친족 호칭·직함·일반명사·지시어와
  미상/화자N/템플릿 빈칸을 규칙별로 갈라 판정하고 `title`·people·places·`todo.owner` 에만 걸린다.
  `김철수 형님` 처럼 실명이 앞에 있으면 보존한다. 예외를 던지지 않는 fail-soft. 수리 티켓 t_35e8a91c.
- **가장 해로운 자리가 제목이었다** — 추출 제목은 노트 이름이자 vault **파일명**이라 잘못 들은 호칭이
  영구 동결된다. 이제 그런 제목은 빈 값이 되어 녹음 자신의 이름(없으면 시각 기본값)이 노트를 이름짓는다.
  요약 산문은 모델의 글이라 사후 치환 대신 `prompts/lifelog-extraction-v5.md` 가 맡는다(v4 동결).
- **카드가 요약을 앞세운다** — 미리보기 5줄·160자 → 7줄·190자, 선택 순서가 요약→한눈에로 뒤집혔고
  전사 폴백은 둘 다 빈 노트에서만 `[전문 발췌 · 요약 없음]` 라벨과 함께 쓰인다. 1900자 fail-closed 유지.
  [소개](기능소개/라이프로그-모호-표현-정규화.md) · PR #457 및 t_35e8a91c 추출 PR.

### 라이프로그 노트에 근거 없는 호칭이 굳지 않는다 (2026-09-08)

- **제목이 곧 파일명이라 가장 해롭다** — 추출된 제목은 노트 이름이자 vault 파일명이 되므로 잘못 들은 호칭
  하나가 영구히 얼어붙었다. 이제 `parse_extraction` 의 규칙층이 모호한 제목을 빈 값으로 접어 기존 기본 제목
  경로가 이름을 짓고, 같은 판정을 사람·장소 목록과 할 일 담당자에도 건다. 수리 티켓 t_35e8a91c.
- **식별 근거의 유무만 본다** — 친족 호칭·직함·일반명사·지시어와 거부 정규식을 규칙별로 나눠 왜 떨어졌는지
  읽히게 했고 조사가 붙은 형태도 같은 판정을 받는다. 실명이 앞에 있으면(`김철수 형님`) 보존하고, 판정할 수
  없으면 추측하지 않고 버린다(예외를 던지지 않는다).
- **산문은 프롬프트가 맡는다** — 요약은 모델이 쓰는 글이라 사후 치환이 문장을 깨뜨리므로 v5 가 규칙만 덧붙이고
  v4 는 동결한다. `한눈에` 의 `사람::`·`장소::` 줄은 복원하지 않는다.
  [소개](기능소개/라이프로그-모호-표현-정규화.md) · PR #457·#458.
