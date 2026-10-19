# AGENTS.md — autophagy-agents repo, orchestrator/agent instructions
> **Tool-call encoding**: Always write Korean (and other non-ASCII) strings in tool-call parameters as literal UTF-8; never as `\uXXXX` unicode escapes.


**Generated:** 2026-07-20 · **Updated:** 2026-07-30 · **Commit:** 157dbd1 · **Branch:** main

## OVERVIEW
P6 공개 설치 기본값은 도리다(`automation.install`·마법사·quickstart).
고정 포크와 리드+YooDori 역할을 설치하며 Hermes 인프라는 명시적 레거시
`--profile core|rag|report-hub|full`에만 남긴다. 아래 Hermes 런타임 설명은 기존 노드에
적용된다. 기존 노드는 서명 릴리스로 업데이트하며 기본적으로 멈춘 잡·peer·유닛을 보존한다.
P5 제거는 `peer-retired` 스위치가 같은 HEAD의 릴리스 승인에 묶인 경우에만 백업 뒤 실행한다.
계정·/etc 삭제는 별도 소유자 블록이다. [메이저 소개](docs/기능소개/도리-기반-메이저.md) · [P5 제거](docs/기능소개/p5-제거.md).

Q245 배치 예외: 릴리스 호스트의 `~/.hermes/release-flags/omo-relay`가 정확한 `on`이면
`codex_llm` 배치는 노드 Hermes 대신 이 PC의 도구 없는 omo로 간다. 모델은 이 PC omo 설정만
정하고, 파일 삭제 후 재배포하면 아래 레거시 Hermes 경로로 복귀한다.

cha의 개인 Hermes 에이전트 시스템. Discord로 타 연구자 에이전트와 상호운용하며, 설치별 `<primary-node>` / `<rag-node>` 역할로 구동한다. Python 3.12 **stdlib 중심**. 모든 대화·배치 모델 호출은 계정 `~/.hermes/config.yaml` 이 정한 주 모델(현재 `openai-codex/gpt-6.1-sol`, reasoning effort `medium` — 2026-10-02)을 쓰고, 그것이 답하지 못하면(쿼터·인증·전송) 같은 파일의 `fallback_providers`(현재 `xai-oauth/grok-4.7`)로 넘어가며 — 모델은 그 파일 한 곳이 정하고 코드는 적지 않는다(2026-09-29 소유자 결정) — 체인 전체가 실패하면 fail-closed로 전파한다. **모든 외부효과는 소유자(cha) 승인 게이트 경유** — 이것이 아키텍처의 중심 불변식이다.

## STRUCTURE
```
autophagy/
├── skills/       # 19개 스킬 디렉터리 (18 기능 + hello-autophagy 데모). 각 <name>/{SKILL.md, scripts/*_cli.py, scripts/*watch.py, deploy.sh}. 에이전트가 스스로 만든 자가 스킬은 이 트리에 없다(각 계정의 `~/.hermes/skills`)
├── automation/   # 오케스트레이션·게이트·워처·배치 코어 (interop, repair, rag_ingest, report_hub, skill_generation, selfskill_audit, twin_distill, twin_observe, managed_skills, managed_sync ...)
├── configs/      # routing-policy.md, peers.example.yaml, external-effect-tools.yaml, rag/
├── dori/         # 도리 기반 사이토플라즘 저장소 층(2026-10-18 소유자 결정 Q219) — pin.toml(도리 포크 고정 커밋)·roles/(리드·비서 기본, 옥돌·무돌 선택 역할 템플릿+아바타). 개인값 없음, 설치는 automation/install/dori.py
├── prompts/      # 버전형 LLM 프롬프트 자산
├── docs/         # guide/(설계규약) patch/(인프라 변경) troubleshooting/ · qa/(W#-# 웨이브 증적)는 추적하지 않는 비공개 운영 저장소 링크다(아래 .omo/ 와 같음)
├── tests/        # unit/ + e2e/(drivers, scenarios, fixtures)
├── .omo/         # 계획·오케스트레이션 상태(설계 단일 진실 plans/autophagy-agents.md 포함) — 공개 저장소에 추적되지 않는다. 비공개 운영 저장소 `orientpine/autophagy-ops` 의 `omo/`·`qa/` 를 `automation/ops_link.sh` 가 `.omo`·`docs/qa` 로 링크한다(경로는 `git config autophagy.opsRepo`)
└── AGENTS.md · README.md
```

## WHERE TO LOOK

Discord의 `#approvals`·`#notifications`는 아래 표와 예시의 관례 이름이다.
설정 ID가 있으면 표시 이름은 자유롭게 바꿀 수 있다. 공급망은
`personal_approvals_channel_id` 또는 명시적 `deploy_approvals_channel_id`와
type 0을 검증하고, 통지는 `owner_notice_channel_id`를 쓴다.
설정 없는 레거시 공급망 조회에만 캐시·이름 검색이 남는다.
peer 차단 프로브는 이름으로 한 번 찾은 id를 ops 메모에 남겨 이름이 바뀐 뒤에도 같은 id를 요구한다([기능 소개](docs/기능소개/Discord-승인-통지-채널-ID.md)).
| 작업 | 위치 | 비고 |
|------|------|------|
| 새 스킬 제작·배포 | `skills/<name>/`, `automation/deploy-skill.sh` | 4단계 게이트, `docs/guide/스킬-제작.md` |
| 도리 층(포크 고정·기본 도리 구성) | `dori/`, `automation/dori_pin.py`, `automation/install/dori.py` | 소유자 결정 2026-10-18 00:10 KST "나는 도리 기반의 사이토플라즘을 계속 배포하고싶은데, 내가 지금 구성한 기본 도리 구성을 배포하도록 하고, 기존 사이토 플라즘의 모든 기능이 녹아들면 좋겠어", Q216 "추천대로 Q1~Q7 진행", Q219 "전부 추천대로 — 저장소 층부터 시작 (추천)". 포크는 `dori/pin.toml` 커밋 하나로만 설치되고(SHA 재확인, main 자동 추종 없음), 올릴 때는 그 SHA 를 바꾸는 PR + 릴리스 노트 한 줄. 영수증 `dori_pin`. 이 단계는 층만 더하고 노드 게이트웨이 동작은 불변. [소개](docs/기능소개/도리-저장소-층.md) |
| 외부효과 승인 게이트 | `automation/interop/external_effect_gate.py` | fail-closed, 해시 바인딩 |
| 인터롭 규약(보고/질의/조율) | `docs/guide/interop-규약.md`, `automation/interop/` | 단일 진실 |
| 워처/cron 설계 규칙 | `docs/guide/watcher-cron-설계규약.md` | no-agent cron 필수 규약 |
| 음성 녹취 → 회의록 | `skills/speechtotext/` | Drive 감시 폴더 → 전사본(.md) → meeting 체인. 전사는 기본 로컬(whisper.cpp) — 원음을 노드 밖으로 내보내지 않는다 |
| 기능 현황·웨이브 맵 | `docs/features.md` → `.omo/plans/autophagy-agents.md` | 계획 문서가 단일 진실 |
| 의사결정 트윈 스키마 | `docs/guide/decision-twin-스키마.md` | 트윈 키·판단 근거 규약 |
| 라우팅·모델·예산 정책 | `configs/routing-policy.md`, `configs/*.yaml` | 주 모델과 폴백은 계정 `~/.hermes/config.yaml` 의 `model:`·`fallback_providers` **한 곳**이 정한다(2026-09-29 소유자 결정 — 현재(2026-10-02) `openai-codex/gpt-6.1-sol`·reasoning effort `medium` → `xai-oauth/grok-4.7`). 코드·스킬·환경변수는 모델을 적지 않고 argv 에 `--provider`·`-m` 을 넣지 않는다(`tests/unit/test_model_single_source_conformance.py`). 확인은 `python3 -m automation.doctor` 의 「주 모델」·「폴백」 줄. peer 도 같은 쌍을 쓰며 `python3 -m automation.model_sync --apply`(운영자)가 agent 블록을 peer 에 맞추고 운영자 모드 doctor 가 `model-parity` 로 어긋남을 알린다. 주 모델을 바꾼 뒤에는 모델 고정 없는 agent 모드 cron 을 같은 정의로 다시 만든다 — Hermes 가 권하는 잡별 모델 고정은 단일 출처를 깨므로 하지 않는다(`configs/routing-policy.md` 「Changing the main model」, doctor 가 `drift_skip` 을 첫 회부터 고장으로 알린다). 제안서 윤문도 이 경로다(Codex CLI 는 codex 그림 생성만) — 게이트웨이와 배치(`automation/codex_llm.py`, `--ignore-user-config` 없음)가 같은 체인을 쓴다. 폴백 추가·제거는 `~/.hermes/config.yaml` 한 곳뿐. 라우팅 로그는 실제로 답한 모델을 `served_provider`·`served_model` 로 적는다(`codex_llm.complete_served`). 내용에 따라 경로를 고르거나 막지 않는다(2026-10-09 콘텐츠 민감 판정 제거) |
| 참고자료(근거) 조회 | `automation/drive_reference.py`, `skills/recall/scripts/recall_reference.py` | 소유자가 Drive 에 모아 둔 참고자료에서 근거 구절을 찾는 **읽기 전용** 경로. 루트는 `DRIVE_REFERENCE_ROOT`(기본 `KIMM`)이며 산출물 루트(`autophagy/`)와 **다른 트리**다 — 그 폴더는 소유자의 보관함이라 폴더를 만들지 않는다(`ensure_folder_path` 금지, `find_folder_path` 사용). 회의록은 참고자료를 발표자료와 같은 `Deck` 경로로 받아 추출 재료로 쓰며, 질의는 회의 라벨·과제명에 **전사 본문 상위 낱말**을 더해 만든다. 읽을 수 없는 형식과 크기는 `reference_rank.refusal` 이 **내려받기 전에** 판정한다 |
| Drive 산출물 발행 | `automation/drive_outputs.py`, `docs/guide/drive-publish.md` | 스킬 산출물은 단일 루트 `autophagy/`에 파사드로만 발행 — 규약은 guide가 단독 소유. `project=` 를 주면 카테고리와 연도 사이에 **과제 한 단**이 들어가고(전사본·회의록), 주지 않으면 경로는 예전과 동일해 나머지 스킬은 무영향 |
| QA 증적 | `docs/qa/<wave-id>/` | 마스킹된 증적. 비공개 운영 저장소의 `qa/` 로 링크된 경로라 공개 저장소에는 없다(`automation/ops_link.sh`) — 공개 문서는 근거로 경로만 인용하고 내용을 옮기지 않는다 |
| Discord 2-서버·승인 채널 구조 | `docs/guide/discord-server-architecture.md`, `automation/interop/approval_surface.py` | **소유자 전용 승인=개인 서버 `#agent-chat`의 **요청별 스레드**(2026-09-01 — 요청 하나에 스레드 하나, 승인·리마인더·결과 통지가 거기서 완결되고 종결 시 상태 접두어+아카이브, 「요청별 승인 스레드 규칙」; v7~v8 은 kind별 고정 스레드 `승인-<kind>` 였고 v8 부터 repair 포함·Ops 봇 초대 전제, 초대 전 릴리스 금지) / 스킬 공급망 승인=개인 서버 `#approvals`**(배포·peer attest·발행·managed 활성화·release — 2차 주체인 peer 봇 **신원**이 같은 채널에 REST 로 게시·리액션 조회해야 하므로; peer 의 **LLM 게이트웨이는 그 채널을 수신하지 않는다** — peer config 최상위 `discord.ignored_channels`, 스레드 포함·allowed 보다 우선, 2026-09-05 patch. 게이트웨이가 카드를 읽으면 자가 스킬이 승인 카드를 즉석 심사한다). 893da68은 승인을 공유 Lab이 아닌 **개인 서버**로 옮긴 결정이며 공급망에 한정된다. 표면은 `approval_surface.py`가 단일 결정하고 conformance 테스트가 강제한다 — 산문이 아니라 코드가 진실. 해석 키는 `~/.hermes/interop/config.json`의 `agent_chat_channel_id`(미설정=fail-closed). 정기 통지(일일 지출·주간 동향·아침 감사·헬스체크·리마인더)는 별도 `#notifications` — `owner_notice_channel_id`(미설정=DM), 발신은 `automation/owner_notice.py` 파사드 단일 경로(ON-2 이관, ON-3 conformance: 파사드 밖 DM 오픈 신설=RED) |
| 장애 대응·운영 제약 | `docs/guide/incident-response.md`, `docs/guide/operations.md` | 원인 확인 전 재시작/설정변경/키 재발급 금지 |
| 연결·승인 점검·알람 | `automation/doctor/`, `docs/guide/연결-승인-점검.md` | 끊긴 로그인·토큰·동의·반복 실패 작업을 `python3 -m automation.doctor` 로 본다(읽기 전용). 설치 마법사가 설치 끝에 같은 점검으로 승인을 안내하고, 매시 워처가 바뀔 때만 알린다 |
| 관리형 스킬 발행 | `automation/managed_skills/`, `docs/guide/managed-skill-channel.md`, `docs/guide/manual-group-admin.md` | 발행자(cha) 전용. 대상 repo = 그룹 스킬 채널 `orientpine/ribosome`(private) — 코드 repo도, 공개 배포본도 아니다(「세 저장소 구분 규칙」) |
| 개인→그룹 스킬 제출 | `automation/managed_skills/submission_*.py` | personal provenance 재사용, 그룹 `#approvals` 검토, 자동 import 없음 |
| 관리형 스킬 구독 | `automation/managed_sync/`, `~/.hermes/managed-sync/` | 구독자(연구원) 전용 — `orientpine/ribosome`에서 받는다(소프트웨어 업데이트는 `cytoplasm`에서 별도로 온다). 틱 배포는 `deploy.sh`(Hermes cron) 또는 설치기 **opt-in** `--with-component managed-sync`(systemd) — 둘 다 같은 래퍼를 돌린다. **배달은 자동, 마운트는 아니다**(D3) |
| 공개 릴리스 컷 | `automation/release.sh`, `docs/guide/manual-maintainer.md` | 개발 정본 = 공개 `orientpine/cytoplasm` main(2026-10-07 공개 우선 전환). 릴리스 = `release.sh --notes-file <노트>`(소유자 ✅) → cytoplasm 커밋에 서명 태그 + 같은 단계의 GitHub Release 노트(`ensure_release_note`). 노트 없이 남은 태그 보충은 `automation/release-note.sh`. 옛 반출 스크립트 `public_export.sh` 는 롤백 기간이 끝나 2026-10-10 에 지웠다(「공개 릴리스 규칙」) |
| 에이전트 자가 스킬 루트·감사 원장 | `<계정 홈>/.hermes/skills`(계정 소유 0700, 1차 루트), `automation/selfskill_audit/`, `~/.hermes/selfskill-audit/{state.json,ledger.jsonl}` | 자가 저작은 **승인 없이 착륙**하고 사후 감사한다(소유자 결정 2026-08-15 옵션 B). 관리자 배포 스킬은 `/srv/autophagy-skills/live`에 남아 Hermes `skills.external_dirs`로 읽기 전용 발견된다. 회수는 `hermes curator archive/pin`. 상세: [소개](docs/기능소개/에이전트-자가-스킬.md) |

## CODE MAP

모듈별 코드 지도(경로·역할·계약)는 [CODE-MAP.md](CODE-MAP.md)에 있다. 이 파일은 모든 세션이 매 호출 읽으므로 지도를 여기 두면 세션마다 문맥 약 6만 토큰이 든다(2026-10-19 실측, 옮긴 이유). 모듈을 찾을 때는 CODE-MAP.md를 통째로 읽지 말고 경로·이름으로 grep해 그 줄만 읽는다. 「문서 갱신 규칙」이 말하는 CODE MAP 갱신은 CODE-MAP.md에 한다.

## CONVENTIONS (repo 고유)

### 2026-09-19 수리 통합의 추가 계약

- 장문 메일 승인은 같은 메시지의 본문 첨부를 파일명·크기·SHA로 검증한다. `mail_approval_attachment`가 이를 소유하며 짧은 카드와 기존 승인 해시는 보존한다([소개](docs/기능소개/kfx-메일-본문-보기와-장문-승인-첨부.md)).
- 일정 다이제스트는 `calendar_digest`가 게시일별 바인딩과 일정별 카드를 기존 lifecycle에 연결한다. 개별 결과는 자기 카드의 답글이고 일별 스레드를 종결하지 않는다([소개](docs/기능소개/kfx-일정-다이제스트-일별-스레드.md)).
- Plaud의 `of_` 별칭은 기존 녹음 신원을 재사용한다. 상태 병합은 명시적 마이그레이션만 수행하고 먼저 백업한다. 클라우드 재확인 오류는 원래 전사 실패를 덮지 않는다([소개](docs/기능소개/kfx-plaud-폴더-이동-별칭과-재확인-오류.md)).
- 수리 CLI의 승인 대기는 exit 5다. 적용 직전 승인 digest를 대조한 바이트 스냅샷만 Git stdin에 전달하며, 읽기용 bootstrap은 스레드를 만들지 않는다. PR 생성·재사용 뒤 정리는 병합된 유휴 로컬 ref의 작업 클론에 한정한다([소개](docs/기능소개/kfx-수리-잔여-결함-7건.md)).
- 제안서 총기간은 `PlanSpec.total_months`가 명시된 월 구간과 기존 순차 작업에서 유도한다. 표지와 연차 검사가 같은 값을 쓰며 계산된 총기간은 JSON에 추가 저장하지 않는다([소개](docs/기능소개/kfx-제안서-계획과-렌더-정합.md)).
- 이 문서의 구현 설명은 라이브 반영 영수증을 대신하지 않는다. 수정 동결된 배포기 변경이 필요한 PR #502는 별도 소유자 판단 전까지 통합하지 않는다.

- **stdlib 전용 지향.** `from __future__ import annotations` + `@dataclass(frozen=True, slots=True)` + 엄격 타입(`TypeAlias`/`Protocol`). 외부 의존은 함수 내부 lazy import + fail-closed 가드(참조 procurement `_import()`).
- **모든 외부효과(메일·캘린더·예산·배포·위키)는 소유자 승인 게이트 경유** — 직접 실행 금지. 승인 이모지 ✅/⛔ (아래 Owner-confirm 규칙).
- **"커밋됨 ≠ 배포됨".** 배포 표면은 **셋이고 서로 닿지 않는다**: ① 릴리스 트리 `/srv/autophagy-agent-current` — 2분 리컨실러가 서명 태그를 보고 자동 수렴. ② 스킬 마운트 — 판정은 `readlink /srv/autophagy-skills/live/<skill>` 해시, 갱신은 소유자 ✅. ③ **계정 홈**(`~/.hermes/scripts/`·`~/.hermes/plugins/`) — 선언된 홈 패키지는 VA-2(2026-08-31)부터 `release.sh` → `deploy_all.sh --apply`로 자동 수렴하고, 선언 밖 사본만 사람이 해당 `deploy.sh`를 돌려 갱신해야 한다; 낡아도 ①②는 멀쩡해 보인다. ③의 드리프트 탐지는 선언 행의 종류에 따라 두 갈래다 — 네 필드 legacy 행은 중앙 표 `configs/watcher-deploy-manifest.txt` + healthcheck 프로브가 상시로 대조하고(릴리스 판정도 같은 행을 다시 본다), v2 행은 중앙 표에 투영되지 않으므로 릴리스 판정이 노드에서 직접 관측한다(아래 ⑤; 일부 런타임 트리는 `configs/runtime-package-manifest.txt` 상시 프로브도 본다) — **등록 지점은 각 배포기 옆 `<package>/deploy-manifest.txt`**(RC-1, 2026-08-28)이고 중앙 표는 `python3 -m automation.watcher_manifest emit` 의 파생물이다(손 편집 금지). 손 편집·emit 누락·선언 없는 배포기는 전부 `tests/unit/test_watcher_manifest_declarations.py` 가 RED 로 막는다. 2026-09-03: 손배포로 돌던 `send_cost_report.py`·`poll_reminders.py`·`repair_report_consume_watch.py`·`05-skill-generation` 플러그인도 `automation/{cost-report,reminder_poller,repair,skill_generation}/deploy-manifest.txt` 로 선언됐다 — 선언이 없으면 프로브가 드리프트 '0' 을 보고하고, 이제 선언 밖 사본은 `deploy_all_probe` 의 `undeclared` 경고로 드러난다. 게이트웨이 플러그인은 여기에 더해 **프로세스 시작 시 로드**되므로 파일이 맞아도 재시동 전까지 도는 코드가 다르다 — 배포와 반영이 별개인 유일한 표면이다(2026-08-28: 홈 플러그인이 5일 낡아 `!meeting` 이 이미 없어진 규칙으로 거부됐고, 매니페스트 정규식이 `scripts/` 만 봐서 사각지대였다). **④ 그리고 배포됨 ≠ 실행됨(2026-09-01)**: 세 표면이 전부 맞아도 에이전트가 **다른 사본**을 실행하면 무효다 — 원문 인용 릴리스가 마운트된 87분 뒤의 회신이 관측 미러 `/srv/autophagy-agents/skills/mail/...`(미커밋 편집으로 08-29부터 동결, 121커밋 뒤)에서 만들어져 인용 없이 나갔다. 유일한 배포본은 `readlink /srv/autophagy-skills/live/<skill>` 의 실경로뿐이며, SKILL.md 예시 경로는 그 live 경로로 적고(`~/.hermes/skills/<skill>` 은 SS-1 이후 없다), mutating CLI 는 자기 사본이 그 실경로인지 판정한다(단일 정의 `skill_mount.governed_copy_refusal`, 채택은 `<skill>_governed.py`). **2026-09-09 에 같은 병이 수리 탐지기에서 났다**: `repair_reporter.REPAIR_CLI` 기본값과 `skills/repair/SKILL.md` 의 명령이 둘 다 계정 홈 사본 `~/.hermes/repair/…` 를 가리켰는데 그 경로는 배포 선언에도 `HOME_DEPLOYED_PATTERN` 에도 없어 7월 세대가 조용히 돌았고, 소유자의 수리 요청 2건이 종결 카드에 묻혀 보드에서 사라졌다. 이제 둘 다 릴리스 런타임 사본을 실행하고 두 선언이 갈라지는 것은 `tests/unit/test_repair_exec_path.py` 가 막는다. **⑤ 그리고 릴리스가 판정하는 범위(RCB, 2026-10)**: 각 배포기 옆 `deploy-manifest.txt` 가 선언 문법 v2(네 번째 필드 `v2:<kind>;key=value;…`, kind=`file`·`tree`·`derived`·`cron`(실행 주체 `runner=hermes|systemd` — 2026-10-18 P2 부터 사용자 타이머 가능, 수렴기 `converge_timer`), policy=`required`·`optional`·`held`·`retired` — 정본은 `automation/deploy_declarations.py`)로 런타임 트리·빌드 산출물·cron 등록·게이트웨이 드롭인까지 선언하고, `deploy_all.sh` 의 릴리스 판정이 그것을 노드에서 관측해 어긋나면 소유 배포기를 돌리며 게이트웨이 세대 기록(`automation/gateway_generation.py`)까지 확인한다. v2 행은 중앙 표에 투영되지 않으므로 cron 등록·게이트웨이 세대·v2 file 행은 상시 헬스체크가 아니라 릴리스 때만 판정된다(`deploy_receipt.JUDGED_AT_RELEASE_ONLY`). 릴리스가 스스로 올리지 못하는 것은 두 갈래로 남는다 — 보류(`held`, 예: roster 가 없어 인터롭 묶음을 어느 계정에도 놓지 않음)와 「남은 소유자 조치」(root 자산·헬스체크 래퍼·RAG 위임 프로브 가운데 PASS 가 아닌 것). 둘 다 영수증 v2 와 릴리스 적용 통지에 실리고 영수증을 막지 않는다. 배포기가 계정 홈에 쓰는 길은 `push_file`·`deploy_tree_swap`·`converge_cron` 셋뿐이고 선언 밖 쓰기는 `tests/unit/test_deploy_write_inventory.py` 가 빌드에서 막는다(예외 원장 `configs/deploy-write-exemptions.txt`) — 규약 `docs/guide/watcher-cron-설계규약.md` (o), 사용 시나리오 [소개](docs/기능소개/릴리스-수렴-사각지대-해소.md).
- **상태 마킹은 성공 이후** — claim → 작업 → 성공 시에만 processed 기록, 실패 시 release.
- `fail-closed`가 반복 원칙 — 설정/권한/확인 불가 시 실행하지 않는다.
- `configs/stt-engines/`는 pyannote·WhisperX를 별도 venv에 가둔 서드파티 격리 예외다. `uv.lock` 고정 설치·모델 캐시는 체크아웃 밖이며, CLI 계약은 해당 README가 소유한다. 소스 공개와 비공개 원음·평가 상태 보관은 별개다.
- `configs/rag/*` 하위 서비스는 `uv` + Ruff `ALL`(line-length 100) + basedpyright `all`. 메인 트리는 위 코드 스타일을 관찰로 강제(루트 매니페스트 없음).
- **vendored 하위 패키지도 격리 예외.** `skills/mail/vendor/mailon/`은 외부 리포(구 `orientpine/emailAutomation`)를 통합한 **무수정 vendoring** 소스로, 4개 서드파티(`pyotp`/`python-dotenv`/`beautifulsoup4`/`lxml`)를 `vendor/requirements.txt` 고정 버전 + 스코프 venv로 쓴다(메인 트리 stdlib-전용의 명시적 예외, RAG 하위서비스와 동일 취지). 소스는 byte-identical 유지 — 고칠 것이 있으면 upstream 성격에 맞게 vendor 소스를 직접 고치고 재배포하되, PROJECT_ROOT 상대 런타임 쓰기(data/logs) 규약은 건드리지 않는다. 런타임은 체크아웃 밖 `~/.hermes/mailon-runtime/`(위 「불변 시드」와 동일 이유). 상세: `docs/guide/기관메일-인터페이스.md`.
- **추적 config = 불변 시드.** 런타임 상태는 체크아웃 밖(~/.hermes/…, /srv/autophagy-private/…)에만 기록한다 — 추적 파일을 런타임에 mutate하면 ops 체크아웃이 dirty해져 git pull --ff-only / peer-attest sync가 막힌다. 선례: configs/mail-mode.default.json(시드) vs ~/.hermes/mail-triage/mail-mode.json(런타임). triage_mode 가드가 시드/체크아웃 경로 쓰기를 fail-closed 거부.
- **`managed-` 접두사 예약.** 관리형 스킬은 반드시 `managed-`로 시작하며, 일반 스킬 배포는 이 접두사를 사용할 수 없다.
- **충돌 시 우선순위 없음.** 일반 스킬과 관리형 스킬 이름 충돌 시 양방향 fail-closed 차단 — 소유자가 하나를 제거(`--remove`)해야 한다.
- **자가 스킬 루트는 통제 공간이 아니다.** 각 계정의 `~/.hermes/skills`는 그 계정이 소유한 쓰기 가능 1차 루트이며, 에이전트가 만든 스킬은 승인 게이트를 거치지 않고 그대로 착지한다. 관리자 배포본은 그 루트에 마운트되지 않고 `/srv/autophagy-skills/live`(root 소유 read-only)에서 `skills.external_dirs`로 발견된다 — 즉 `~/.hermes/skills` 아래에 있는 것을 관리자 배포본으로 읽으면 안 된다(반전 전에는 그 경로가 live의 read-only bind였다).

**자가 스킬 이름 충돌은 한쪽만 막힌다 — 반대쪽은 탐지로 메운다(2026-08-16 정정).** self→governed 방향(자가 스킬이 배포 스킬 이름을 선점)은 **Hermes가 막지 못한다**: `skill_manage(create)`의 충돌 검사 `_find_skill`은 `rglob("SKILL.md")`로 훑는데 우리 governed 루트 `/srv/autophagy-skills/live`는 릴리스로 가는 **심링크 팜**이고 파이썬 `rglob`은 디렉터리 심링크를 따라가지 않는다 — 실측으로 `_find_skill("recall")`·`_find_skill("mail")`이 모두 `None`이었고, 에이전트가 `recall` 이름의 자가 스킬을 실제로 만들었다(즉시 제거). 1차 루트가 발견에서 이기므로 그런 자가 스킬은 **승인 게이트를 강제하는 배포본을 가린다**. 벤더 쪽을 고칠 수 없으므로 `selfskill_audit`이 자기 루트와 live 이름을 대조해 `SHADOWS-GOVERNED`로 소유자에게 알린다(델타가 없어도 보고). **SC-1(2026-08-30)로 이 탐지는 일 1회에서 2분으로 당겨졌다** — `automation/supply_chain_shadow_watch.py`가 같은 walk(`scan._skill_dirs`)의 이름 대조만을 기존 `supply_chain_watch` 2분 틱에 편입해 새 그림자에 통지 1건(전송 실패 시 다음 틱 재시도), 지속 중 저널 한 줄, 해소 후 재발은 새 사건으로 재통지한다(fail-soft — 탐지 실패가 승인 재개 틱을 세우지 않는다). governed→self 방향(배포가 자가 저작물을 덮어씀)은 `deploy-skill.sh`가 막는다 — agent 루트와 peer 루트를 각각 분류해 자가 저작물이면 `SELF-SKILL-COLLISION-BLOCK`(exit 4)으로 멈추고, 읽거나 분류할 수 없어도 같은 코드로 멈춘다(fail-closed). 관리형 스킬 충돌과 같은 원칙이다 — 우선순위는 없고, 소유자가 한쪽을 치워야(`hermes curator archive <name>` 또는 해당 계정 `~/.hermes/skills`에서 디렉터리 제거) 배포가 재개된다.

## ANTI-PATTERNS (금지)
- cron 워처가 Discord **메시지/첨부를 폴링** — 실시간 에이전트와 경쟁 소비자. 워처는 **리액션만** 폴링.
- 자식 subprocess에 자격증명 미전파(`env=` 누락) — no-agent cron은 os.environ에 시크릿을 안 넣으므로 명시 전파 필수.
- 채널·기능별 **병렬 confirm 워처/resolve 신설** — 기존 게이트에 `channel_id` 바인딩으로 재사용.
- 도메인 중첩 스킬에서 SKILL.md 라우팅에만 의존 — mutating 경로에 결정론적 코드 가드 필수(선례 calendar `94d625f`). **가드는 양방향이어야 한다**: 한쪽만 막으면 LLM이 두 경로를 동시 발동해 이중 쓰기(선례 사고 2026-07-20: `peer-test 오전 10시` 요청이 calendar 07-22 10:00 + coordination 07-29 09:00 이중 등록). calendar↔coordination는 공유 판정 `calendar_routing.classify_meeting_request`(calendar|coordination|clarify)로 정확-단일-시각=calendar / 피어+범위+조율의사=coordination / 모호=clarify(fail-closed)를 양쪽 CLI가 함께 강제한다.
- 토큰 모양 문자열(`Bot `, `sk-`, `ghp_`)을 **주석/문서에도** 사용 — secret-scan 오탐이 배포를 막는다.
- 프로덕션 게이트웨이에 `E2E_TEST_MODE=1` — production guard가 부팅 거부.

## COMMANDS
```bash
pytest tests/unit                     # 단위 테스트 (루트에서 실행, .pytest_cache)
ruff check .                          # 린트
automation/deploy-skill.sh <skill>    # 스킬 배포 (4단계 게이트; --sandbox-only / --request-only / --remove)
automation/local_ci.sh run            # PR 전 로컬 CI (통과 시 push 게이트 영수증 발급)
automation/merge-pr.sh <pr>           # 체크 green 확인 → 머지 → 릴리스 태그
automation/deploy_all.sh --plan       # 전 표면 전량 수렴 판정 (--verify 영수증 / --apply 수렴)
automation/healthcheck.sh             # 양 노드 read-only 헬스체크
python3 -m automation.rag_ingest      # 개인 RAG 인제스트
```

## NOTES
- 설계·작업분해의 단일 진실: `.omo/plans/autophagy-agents.md` (Work Plan). `docs/features.md`는 요약 뷰 + 아이디어 수집함.
- 배포는 오케스트레이터에서 `DEPLOY_SSH_HOST`(`<primary-node>`)로 SSH + `sudo -n -u agent|peer`.
  - **prod 접속 경로 (2026-09-28 실측)**: 설치별 실제 값은 추적 파일이 아니라 오케스트레이터의 `~/.hermes/node.toml` `deploy_ssh_host` 에 둔다(「개인화 코드 금지 규칙」). 그 값은 `~/.ssh/config` 의 `Host` **별칭**이어야 한다 — 별칭이 노드 간 직결 주소·`IdentityFile`·`StrictHostKeyChecking accept-new` 를 싣는다. 같은 노드라도 mDNS(`.lan`) 이름이나 tailnet 주소는 known_hosts 에 없으면 `BatchMode` 에서 `Host key verification failed` 로 실패하므로 스크립트에 쓰지 않는다. 접속 확인은 `ssh -o BatchMode=yes <deploy_ssh_host> 'hostname && sudo -n -u agent -H true'` 한 줄이다. 오케스트레이터 호스트가 바뀌면 `node.toml` 부터 만든다 — 없으면 배포 스크립트가 `DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset` 으로 멈춘다.
- `logs/`는 git 미추적(디렉토리만 유지). `configs/rag/*/.venv`·`__pycache__`·`*_cache`는 소스 아님.
- `.git/hooks/`는 미추적 — clone마다 gitleaks pre-commit 훅 재설치 필요. `gitleaks` 바이너리가 PATH에 없으면 커밋이 fail-closed 차단된다(`docs/troubleshooting/gitleaks-setup.md`).


## 커뮤니케이션 언어 (사용자 지시, 2026-07-14)

**사용자(cha)에게 말하는 모든 내용(채팅 응답, 상태 보고, 질문, 요약)은 한국어로 작성한다.**

- 적용 대상: Atlas(오케스트레이터)가 cha에게 보내는 모든 메시지.
- 적용 제외: 코드, 커밋 메시지, 파일 내용, 서브에이전트에게 보내는 delegation 프롬프트,
  `.omo/plans/`·`.omo/notepads/`·`docs/` 등 산출물 자체의 언어는 이 규칙과 무관 — 기존
  계획 문서가 이미 한국어/영어 혼용이므로 각 문서의 기존 스타일을 따른다.
- 이 지침은 세션이 바뀌어도 유지되어야 하므로 repo 루트에 기록한다(사용자 요청: "지침에 추가해").

## Owner-confirm 이모지 컨벤션 (cha 지시, 2026-07-17)

모든 소유자 확정/승인 흐름(mail·budget·calendar·coordination 및 향후 추가되는 모든 confirm)은 동일한 이모지 리액션으로 처리한다:

- **✅ (U+2705, `:white_check_mark:`) = 승인 / 확정 / 실행**
- **⛔ (U+26D4, `:no_entry:`) = 거부 / 취소**

공통 규칙 (모든 confirm 게이트·워처):
- **소유자(cha) 리액션만 유효** — 봇/타인 리액션은 무시(reactor id == owner_id AND bot == false).
- **⛔ 우선** — ✅와 ⛔가 함께 있으면 취소로 처리한다(외부효과 fail-safe).
- **hash 바인딩 + fail-closed** — 대상 메시지가 드래프트 sha256을 참조해야 하며, 확인 불가/모호하면 실행하지 않는다. 메일 카드 v4(2026-10-19 형 "이런 것도 짱도리에게 남기지 말고, 내부용으로만 쓰라고 해. 디스코드에서는 안보이게 해.")는 draft id·sha256·취소/다음/되돌리기 줄을 디스코드에 내지 않고, 게시한 글 전체의 sha256(`card_sha256`)을 레코드에 묶어 같은 글일 때만 승인한다(`skills/mail/scripts/mail_card_compact.py`). v5(2026-10-19, 형 "앞으로 이런 실수 없도록 해")는 본문이 길어 텍스트 파일로 옮겨진 카드에도 첨부 이름·크기와 Drive 공유 대상을 싣고, 구글 계정이 없는 받는 사람에게는 구글 초대 알림 메일도 간다고 적는다(형 Q273). 같은 형 스레드에서 고친 새 초안이 올라오면 옛 미결 메일 카드는 「새 판으로 대체됨」으로 닫혀 ✅ 해도 실행되지 않는다(`triage_supersede.py`).
- 확정 메시지에는 봇이 ✅·⛔를 미리 부착한다(cha는 탭 한 번). 텍스트 `실행/취소 <id>`는 하위호환 fallback일 뿐, 에이전트는 "실행 <id> 답장"류 텍스트 지시나 별도 DM을 보내지 않는다.
- 신규 confirm 워처(Hermes no-agent cron)의 배포 파일명은 `~/.hermes/scripts/` 안에서 **스킬별로 고유**해야 한다(예: `calendar_confirm_reaction_watch.py`) — 동명 파일 충돌 금지.
- **워처(cron)는 Discord 리액션만 폴링한다 — 메시지 텍스트/첨부의 수신·처리는 실시간 에이전트 단독 소유(경쟁 소비자 금지)**
- no-agent cron은 `~/.env.secrets` self-load + repo sys.path 래퍼 필수 — 상세: `docs/guide/watcher-cron-설계규약.md` — 워처가 자식 프로세스를 spawn할 때 자격증명을 자식 env에 명시 전달(자식 폴백 의존 금지, 규약 (b-2)).
- 도메인 중첩 스킬(calendar↔coordination 등)은 SKILL.md 라우팅만으로 부족, 양보 스킬의 mutating 경로에 결정론적 가드(등록 레지스트리 매칭, 소유자 제외, fail-closed)로 거부+안내(선례 calendar `94d625f`), 상세: `docs/guide/watcher-cron-설계규약.md`.
- **단일 게이트 절차 재사용 — 병렬 confirm 구조 신설 금지 (2026-07-19 mail compose 사후 반영)**: 신규 confirm 표면(owner DM 등)이 필요하면 해당 스킬의 **기존** 게이트를 재사용한다 — draft/pending 레코드·render·resolve·watch cron은 하나만 존재하며, 승인 채널은 레코드에 바인딩된 `channel_id` 필드로만 달라진다. 채널별·기능별로 별도 render/별도 resolve/별도 워처를 만드는 것은 금지. 선례: mail 스킬의 개인 서버 #approvals 회신 초안과 owner-DM compose 초안은 같은 `watch` cron·같은 `resolve_reaction`·같은 render 함수를 공유한다.

## 문서 갱신 규칙 (cha 지시, 2026-07-20)

**코드 작업은 관련 문서 갱신까지 마쳐야 종결된 것으로 간주한다.**

- 기능을 추가/변경/제거하면 **같은 커밋·배포 사이클에서** 해당 리포의 AGENTS.md·docs/·SKILL.md 중 낡아지는 문구를 함께 갱신한다.
- 적용 대상: autophagy-agents와 에이전트가 참조하는 모든 산출물 리포(emailAutomation 등). 에이전트는 문서를 사실 근거로 답하므로, 낡은 문서는 곧 소유자에게 잘못된 안내가 된다.
- 배경(사후 반영): 2026-07-20 mailon 보낸메일함 동기화가 배포됐으나 emailAutomation AGENTS.md의 "받은편지함만 지원" 문구가 남아, 에이전트가 기능이 없다고 잘못 안내함.

## 다른 세션 작업 덮어쓰기 방지 (cha 지시, 2026-07-21)

**작업 시작 전, 대상 영역의 최신 상태를 반드시 확인한다 — 특히 설계·아키텍처 결정.** 여러 세션/에이전트가 같은 repo에서 동시·연속으로 작업하므로, 내 기억이나 이전 세션의 이해는 이미 낡았을 수 있다.

- **설계·규약 문서를 수정하기 전에**: 해당 문서의 `git log`를 확인해 **최근 커밋이 다른 세션의 확정 결정인지** 살핀다. 최근에 그 주제를 바꾼 커밋이 있으면 그 커밋 메시지·diff를 읽어 **왜 그렇게 결정됐는지(rationale)** 파악한 뒤에만 손댄다. 내 이해와 충돌하면 덮어쓰지 말고 사용자에게 확인한다.
- **"내가 아는 설계"와 repo의 현재 상태가 다르면, repo가 진실이다.** 옛 이해로 최신 결정을 되돌리지 않는다. 사용자가 "최근 세션에서 해결했다"고 하면 반드시 그 커밋/세션을 먼저 찾아 확인한다(`git log --oneline -S <키워드>`, session 도구, 커밋 diff).
- **다른 세션의 미커밋 작업(dirty 워킹트리)은 되돌리지 않는다.** stash/reset/checkout/discard 금지 — 소유자 명시 승인이 있을 때만, 복원 가능한 방식(stash 보관 후 pop)으로 처리하고 작업 종료 시 반드시 복원한다.
- **배경(사후 반영)**: 2026-07-21, 한 세션이 확정·커밋한 Discord 2-서버 설계(893da68 — "모든 승인=개인 서버, peer attestation의 peer는 자기 두 번째 봇")를 다른 세션이 옛 이해("배포 승인은 공유 Lab")로 문서·config를 덮어써, 존재하지 않는 공유 Lab 채널을 가리켜 배포가 404로 실패했다. 최신 커밋을 먼저 확인했다면 예방 가능했다.

## 게이트웨이 재시동 규칙 (cha 지시, 2026-07-22)

**게이트웨이 재시동은 항상 agent와 peer를 포함한 모든 Hermes gateway를 함께 재시동한다 — 한쪽만 재시동 금지.**

- 기존 불변식 유지: healthy 서비스는 재시동하지 않고, 원인 확인 후에만 재시동한다. 이 규칙은 **재시동이 결정된 뒤의 범위**(항상 agent+peer 전 계정 세트, 향후 게이트웨이 계정 추가 시 그 계정도 포함)만 규정한다.
- 적용 대상: gateway를 재시동하는 모든 경로 — 수동 운영, 장애 대응, 재부팅 복구, 배포 스크립트(`automation/skill_generation/deploy.sh` 등). 상세 절차: `docs/guide/operations.md` §2 게이트웨이 재시동 규칙(incident-response.md·reboot-recovery.md 동기 갱신됨).
- caveat: 재시동 창 동안 진행 중인 peer attestation/승인 응답은 fail-closed 타임아웃될 수 있다 → 재시동 완료 후 재요청(차단 아님).
- ops·dashboard 등 다른 unit은 이 규칙 대상이 아니다 — Hermes gateway(agent·peer) 세트에만 적용.

## 기능 소개 문서 규칙 (cha 지시, 2026-07-25)

**기능(작업)이 완료되면, 완료된 기능을 소개하는 문서를 함께 만든다.**

- 위치: `docs/기능소개/<기능-slug>.md` (기능당 1개). 작성 후 `docs/features.md`의 해당 항목에서 링크한다.
- 길이: 1페이지 내외로 **간결하게**. 내부 구현 덤프가 아니라 "무엇을/왜/어떻게 쓰는가" 중심.
- 필수 포함:
  - **무엇을**: 이 기능이 무엇을 하는지 한두 문장.
  - **왜**: 어떤 문제를 해결하는지(기존 한계/장애).
  - **사용 시나리오**: 소유자(cha) 관점의 구체적 예시(입력→동작→결과). 최소 1개 happy-path + 실패/거부 경로가 있으면 함께.
  - **관련**: 스킬/티켓/핵심 파일 경로. 승인·게이트가 관여하면 명시.
- 민감정보 규칙 동일: 토큰·원문 로그·실제 문서 본문은 넣지 않는다(마스킹).
- 「문서 갱신 규칙」과 병행한다: 코드 완료 = 낡은 문구 갱신 + 이 소개 문서 작성까지가 종결 조건.
- 배경: 2026-07-25 cha 지시 — 완료 기능을 소유자가 바로 이해·사용할 수 있도록, 사용 시나리오 중심의 짧은 소개 문서를 남긴다.

## 커밋 규칙 (cha 지시, 2026-07-25)

**작업할 때는 단위 작업(logical unit)마다 커밋한다 — 여러 변경을 하나의 큰 커밋으로 몰지 않는다.**

- 하나의 논리적 변경(기능 추가·버그 수정·문서 갱신·리팩터 등)이 끝날 때마다 그 단위로 커밋한다. 서로 무관한 변경을 한 커밋에 섞지 않는다.
- 적용 대상: autophagy-agents에서 작업하는 모든 에이전트·세션. 커밋 메시지는 repo 기존 스타일(Conventional Commits: `feat(scope):`, `fix(scope):`, `docs(scope):` 등)을 따른다.
- 이유: 리뷰·되돌리기(revert)·이분 탐색(bisect)·다른 세션과의 병합을 쉽게 하고, "커밋됨 ≠ 배포됨" 판정과 프로덕션 핫픽스 유실 방지에 도움이 된다.
- 「문서 갱신 규칙」과 병행한다: 코드 단위 작업이 문서를 낡게 만들면 같은 단위·커밋 사이클에서 문서도 함께 갱신한다.

## 배포 provenance 규칙 (cha 지시, 2026-07-25)

**prod에는 `origin/main`에 있는 코드만 배포한다 — 미커밋·미푸시·뒤처진 체크아웃에서의 배포는 차단된다.**

- 모든 deploy 스크립트는 로컬 체크아웃의 파일을 prod로 복사한다. 병렬 세션 환경에서는 (a) 미커밋 파일 배포, (b) 커밋했지만 push 안 한 파일 배포, (c) origin/main보다 **뒤처진 체크아웃**에서의 배포가 쉽게 일어난다. 그러면 prod가 git에 없는 코드를 돌리고, 다음에 누군가 깨끗한 체크아웃에서 같은 스크립트를 돌리면 **말없이 되돌려진다**.
- 강제 수단: `automation/deploy_provenance.sh`의 `deploy_provenance_check <repo_root> <경로>...`가 각 파일의 워킹트리 blob 해시와 배포 기준(`origin/main`)의 blob 해시를 대조해 불일치면 `DEPLOY-BLOCK`으로 중단한다. 신규 deploy 스크립트는 **반드시** 이 가드를 호출해야 한다.
- **기준 `origin/main` 은 공개 `orientpine/cytoplasm` 의 main 이다(2026-10-07 공개 우선 전환).** main 은 브랜치 보호(PR 필수 · 필수 체크 verify·clean-host-install·leak-guard · 관리자 포함)로만 전진하므로, 이 규칙이 요구하는 "origin/main 에 있는 코드" 는 곧 PR 과 필수 체크를 지나 공개된 코드다. 전환 전에는 private `orientpine/autophagy-agents` 의 main 이 기준이었다.
- **디렉터리 인자는 tracked 대조만으로 부족하다 (2026-08-01 보강).** 호출자는 디렉터리를 통째로 `tar`/`rsync`로 보내는데 가드는 `git ls-files`(tracked)만 열거했다 — 그래서 디렉터리 안의 **untracked 새 파일**이 `OK`를 받고 prod로 실려, 커밋·`origin/main`·PR을 **전부 우회**했다(실측: `automation/hermes_compat`에 untracked `.py` 1개 → `OK: 17 file(s) match origin/main`, rc 0). 이제 디렉터리 인자는 `ls-files --others --exclude-standard`로 untracked 비-ignored 파일을 함께 검사해 있으면 경로를 적시하고 `DEPLOY-BLOCK`한다. `--exclude-standard`는 의도적이다 — `.gitignore`가 비소스로 선언한 잔여물(`__pycache__`·`.venv`)까지 막으면 모든 실배포가 깨진다. **디렉터리 인자 호출자에는 `deploy-skill.sh`(스킬 마운트 경로)도 포함**되므로 이 보강은 최고 권한 경로에도 적용된다.
- 예외(의도적): `deploy-skill.sh --sandbox-only` / `--activate-managed` / 내부 staged-source용 `SKILL_SRC_DIR` 지정, 그리고 워킹트리를 통째로 rsync하는 `regression_bank/deploy_lab_node.sh`(푸시 전 테스트가 목적). 개인 저작은 이 우회를 쓰지 않고 `--personal <name>`이 별도 `personal_provenance_check`(독립 repo의 clean·committed HEAD + untracked 비-ignored 부재 + 승인 SHA 바인딩)를 통과한다.
- 탈출구 `DEPLOY_ALLOW_UNPUSHED=1`은 **샌드박스/실험 전용**이다. 배포를 통과시키려고 상습적으로 쓰면 가드가 무의미해진다 — 올바른 순서는 상시 "커밋 → 푸시 → 배포"다.
- 배경(사후 반영): 2026-07-25 mail 승인 워처 수리 중, 수정된 `mail_digest_watch.py`가 미커밋 상태로 prod에 배포돼 다른 세션의 배포 한 번으로 유실될 수 있는 상태였다. 같은 계열의 선례: 2026-07-21 옛 체크아웃이 최신 Discord 2-서버 결정을 덮어써 배포가 404로 실패.

## 작업 종결 규칙 (cha 지시, 2026-07-25)

**작업이 끝나면 커밋 → 푸시 → 배포까지 마쳐야 종결이다 — "코드만 고쳐두고 보고"는 미완이다.**

- 순서는 항상 **커밋 → 푸시 → 배포**다. 「배포 provenance 규칙」의 가드가 미푸시 코드의 배포를 차단하므로 순서를 바꾸면 배포가 막힌다. 이 순서 자체가 프로덕션 핫픽스 유실 방지 장치다.
- 배포 대상 판정: 스킬 = `automation/deploy-skill.sh`(4단계 게이트), 워처 = 각 `deploy.sh`, ops 체크아웃에서 실행되는 코드(`/srv/autophagy-agents/automation/**`) = ops 계정 `git pull --ff-only`. 배포할 것이 없는 작업(문서·테스트만)은 커밋·푸시로 종결한다.
- 배포 후 **검증까지가 종결 조건**이다: live 심링크 해시 / 배포본 파일 해시 / 실제 실행(cron tick·import·smoke) 중 해당하는 증적을 남긴다("커밋됨 ≠ 배포됨").
- 소유자 승인이 필요한 배포는 승인 요청 게시까지 진행하고, cha의 ✅ 이후 남은 단계(MOUNT·검증·kanban 종결)도 같은 세션에서 마무리한다.
- 다른 세션의 미커밋 작업·로컬 전용 커밋은 절대 함께 커밋하지 않는다. 로컬 브랜치가 갈라져 있으면 `origin/main` 기반 worktree에서 **내 변경만** 커밋·푸시한다(선례 2026-07-25).

## 릴리스 패치노트 작성 규칙 (cha 지시, 2026-08-30)

**릴리스 승인 메시지는 소유자와 다른 참여자가 무엇을 받아들이는지 이해할 수 있어야
한다. 해시 목록은 패치노트가 아니다.**

- 패치노트는 **무엇이 바뀌는지 → 왜 필요한지 → 사용자·운영 영향**이 드러나는
  제목으로 쓴다. 티켓 번호·내부 파일명·`fix` 같은 분류만 있는 제목은 불충분하다.
- `release.sh`는 직전 릴리스 이후의 커밋 제목을 패치노트 원문으로 쓴다. 따라서 logical
  commit 제목 자체가 위 기준을 만족해야 한다. 릴리스 직전에 이해 불가능한 제목을
  발견하면 승인 메시지를 억지로 줄이지 말고 해당 변경 설명을 보강한 뒤 다시 계획한다.
- **소유자 결정(2026-10-18 20:50 KST)**: "유돌이 릴리즈노트 간단하게 하고 나머지 내용은 어디 문서로 저장해놨다가 내가 물어볼 때만 올리도록 하고 지금처럼 길게 다 릴리즈노트 적게 하지마".
- 신규 승인 카드(`render_version=8`, 2026-10-19)는 v7 과 같은 짧은 형식에, 요약 줄을 `--notes-file` 첫 줄(`# …`)에서 가져오고(없으면 커밋 제목 하나) 스위치는 **이번 릴리스에서 새로 켜지거나 값이 바뀐 것만** 줄로 싣는다. 직전 릴리스와 같은 스위치는 「계속 켜져 있는 스위치 N개(바뀐 것 없음)」 한 줄이다(직전 카드의 표면은 릴리스 호스트 `~/.hermes/release-notes/<sha>.surfaces.json`, 모르면 전부 표시). 요약·직전 표면은 승인 해시 밖 레코드 필드다. 아래 v7 설명은 v7 레코드 재생에 그대로 쓴다.
- 신규 승인 카드(`render_version=7`)는 **최대 12줄·1900자**로 버전·짧은 HEAD·한두 변경 요약과
  승인에 필요한 표면/스위치·운영자 경보·전체 `action_hash`·결정 방법만 싣는다.
  스위치가 여러 개면 같은 줄에서 구분하고 내용을 빠뜨리지 않는다. 끝에는
  "자세한 건 유돌에게 물어보면 올려요"를 적으며 상세 메시지를 자동 게시하지 않는다.
  1900자 초과는 승인 대상 줄을 줄이는 대신 게시를 거부한다.
- 전체 번들·커밋 원문은 호스트 `plan`이 승인 전에
  `${RELEASE_NOTES_DIR:-~/.hermes/release-notes}/<sha>.details.md`에 원자 저장한다(0600).
  기존 `<sha>.md` 초안 옆의 이 파일을 `ensure_release_note`가 GitHub Release 본문에 합친다.
  게시 실패에도 로컬 파일은 남고 재실행이 같은 문서를 쓴다. 유돌은 형이 물을 때만
  `release-notes <vX.Y.Z>`로 공개 Release 노트를 읽어 올린다(토큰·쓰기 없음).
- 저장된 v1~v6 카드와 v6 상세 메시지는 바이트 동결이다. `action_hash`·표면·nonce의
  바인딩은 바꾸지 않으며 승인 단일성·재사용·해시 검증은 기존 lifecycle을 그대로 쓴다.
- ✅ 이후 별도 수동 재개를 요구하지 않는다. 워크스테이션의 `release.sh`는 기본적으로
  결정을 계속 기다리고, 소유자 ✅를 감지하면 곧바로 서명 태그를 자른다. deadline은
  테스트·의도적 제한 운영에서만 명시한다.

## 릴리스 태그 규칙 (cha 지시, 2026-08-20)

**서명 릴리스 태그가 없으면 프로덕션은 전진하지 않는다. 태그는 릴리스 승인(`automation/release.sh`, 소유자 ✅ 1회)이 자른다 — 머지는 축적이다(VA-3, 2026-08-30 개정: `merge-pr.sh` 의 태그 자동 호출 제거).**

- **왜**: 2분 리컨실러가 부르는 `converge_origin_main.sh` 는 **인자를 받지 않는 것이 계약**이다(MD-1). 자동 트리거가 설치될 sha 를 고를 수 있으면 "PR 머지 = sudo 임의 코드 실행"이 되기 때문이고, 그래서 무엇을 설치할지는 **서명만이** 정한다 — **게시된 릴리스 태그 중 가장 최신의 검증된 것**(semver 순)으로 수렴하며, 게시된 릴리스가 하나도 없으면 매 틱 `UPDATE-TRUST-BLOCK` 으로 서고 rc 0 으로 안전하게 건너뛴다. **2026-09-09 이전에는 `origin/main` HEAD 가 그 태그의 peel 대상일 때만 수렴했다** — 그 요구는 태그 컷~수렴 사이에 저장소 전체의 머지 동결을 강요했고(v1.6.5 실측: 태그 push 뒤 PR 하나가 착지하자 노드가 v1.6.4 에 굳었다) 보안은 사지 못했다: 태그는 push 되어야 보이므로 그것을 심을 수 있는 자는 이미 쓰기 권한을 가졌고 서명키만 가진 자는 게시할 수 없다. 진위는 서명이, 신선도는 릴리스 floor 가 그대로 맡는다([소개](docs/기능소개/릴리스-수렴-최신-서명-릴리스.md)).
- **왜 지금 규칙이 되었나**: 태그를 자르는 코드는 `land.sh` 안에만 있었고, 「세션 워크트리 규칙」대로 브랜치 작업은 land 가 아니라 **PR 머지**로 main 에 도달한다 — 즉 우리가 실제로 쓰는 경로에는 그 단계가 아예 없었다. 2026-08-20 실측: PR 6건이 태그 없이 들어가 리컨실러가 **132회 연속 실패**했고 프로덕션이 2커밋 뒤에 얼어 있었다. 그동안 프로덕션을 밀어올린 것은 사람이 `land.sh` 를 돌릴 때뿐이었다.
- **머지 직후에 실행한다**: 태그는 **HEAD 를 정확히 맞혀야** 한다. 병렬 세션이 그 사이 머지하면 태그는 이전 커밋에 남고 노드는 계속 선다(2026-08-16 에 main 이 세 번 전진하며 실제로 그랬다). 그래서 `release-tag.sh` 는 자른 뒤 HEAD 가 아직 그 sha 인지 다시 확인하고, 어긋났으면 성공으로 끝내지 않는다 — **조치는 재실행**이며 멱등이라 안전하다.
- **묶음 단위도 괜찮다**: 매 머지마다 자를 필요는 없다. 연속 머지 후 마지막에 한 번이면 프로덕션이 그 지점으로 간다 — 태그를 자르는 순간이 곧 "여기까지를 프로덕션에 올린다"는 선언이다.
- **키는 로컬에만 둔다**: `UPDATE_TRUST_SIGNING_KEY`(기본 `~/.ssh/autophagy_update_trust.pub`)는 어떤 저장소에도 커밋하지 않고 CI 와 노드 서비스 계정(agent·ops·peer·도리 계정)이 읽을 수 있는 곳에 두지 않는다(「공개 릴리스 규칙」 D8 과 같은 키). 2026-10-19(형 Q263)부터 릴리스 호스트는 노드와 같은 기계(주 노드 `<primary-node>`)의 운영자 계정이고, 비밀 키는 그 계정 `~/.ssh`(0700)에만 있다 — 기계 경계 대신 계정 경계다. **CI 가 서명하게 만들면 MD-1 이 막으려던 그 escalation 이 그대로 되살아난다** — 자동화하려면 서명 주체가 아니라 실행 시점만 자동화한다.
- **릴리스는 릴리스 호스트(서명키를 가진 워크스테이션)에서 연 세션이 한다(cha 지시, 2026-09-29 · 2026-10-13 개정)**: 릴리스 호스트는 **리드(메신저 에이전트)가 도는 워크스테이션**이다(2026-10-13 소유자 결정 「앞으로 릴리스는 네가 있는 pc에서 진행할거야」; 2026-10-19 Q263 으로 리드와 함께 주 노드 운영자 계정으로 옮겼다 — `deploy_ssh_host` 가 자기 자신이라 그 계정에 `from=` 제한 자기 SSH 키가 있어야 한다) — 그래서 보통은 그 머신의 세션이 자기 메인 체크아웃에서 `automation/local_ci.sh run` → `automation/release.sh` → 릴리스 노트를 바로 실행한다. 세션이 다른 머신에서 돌고 있으면 릴리스·공개 반출은 그 머신에서 하지 않고 릴리스 호스트에 SSH 로 **그 머신의 셸 세션**을 열어 거기서 실행한다. 키를 다른 머신으로 복사하지 않는다 — 릴리스 호스트를 옮길 때는 키를 옮기지 않고 새 머신에서 새 키로 **회전**한다(`docs/guide/manual-maintainer.md` §3.4: 노드 신뢰 목록에 새 지문을 `--add` 로 먼저 병기 → 새 키로 릴리스 → 옛 키 줄 제거·폐기; 2026-10-13 첫 회전). 완결 타이머(`automation/release_complete_install.sh`)는 릴리스 호스트 **한 곳에만** 둔다 — 머신마다 `deploy_all` 잠금이 따로라 옛 호스트 타이머가 살아 있으면 두 곳이 동시에 배포할 수 있으므로, 옮긴 뒤 옛 호스트의 타이머는 `--uninstall` 로 끈다. 그 머신의 좌표(호스트·계정)는 설치별 값이라 이 파일이 아니라 운영자 메모리/노트에 둔다(「개인화 코드 금지 규칙」). 시작 전에 세 가지를 그 머신에서 확인한다: ① `ssh -o BatchMode=yes <deploy_ssh_host> 'hostname && sudo -n -u agent -H true'` — 완결 타이머도 이 별칭으로 노드에 가므로 여기서 막히면 ✅ 뒤 자동 완결이 조용히 멈춘다, ② 그 머신에 대상 트리의 로컬 CI 영수증이 있는가 — 영수증은 CI 를 돌린 머신에만 생기고 `release.sh` 전제 ③·완결기가 **자기 머신의** 영수증을 요구한다, ③ `gh auth status` 가 유효한가 — 로컬 CI 의 `test_fs3_final_gate` 와 공개 릴리스 노트가 gh 를 쓰고, 데스크톱 로그인이 없는 SSH 세션에서는 키링 토큰이 보이지 않으므로 `gh auth login --web` 디바이스 코드를 소유자가 승인한다(토큰을 다른 머신에서 복사하지 않는다). 유닛 `conftest` 가 HOME·XDG 를 임시 디렉터리로 옮기므로 CI 는 `GH_CONFIG_DIR=$HOME/.config/gh` 를 명시해 돌린다. 배경: 2026-09-28 v1.11.0 은 키가 없는 머신의 세션에서 요청돼 ✅ 뒤 태그 단계에서 멈췄고, 키 보유 머신의 완결 타이머는 노드 별칭이 mDNS 이름(`*.local`)을 가리켜 매 틱 `Could not resolve hostname` 으로 결정을 읽지 못했다(9/1 부터 간헐, 9/28~29 연속). 별칭을 고친 뒤에도 그 머신의 gh 토큰이 만료돼 영수증 발급이 한 번 더 막혔다 — 「NOTES」의 prod 접속 경로 규칙(별칭은 mDNS·tailnet 원문이 아니라 known_hosts 에 묶인 주소)과 같은 병이다.
- **서명 없는 head 는 사고가 아니라 릴리스 백로그다(VA-3 재설계)**: 릴리스 사이의 unsigned `origin/main` 은 정상 상태이므로 sha별 즉시 통지를 보내지 않는다. 리컨실러는 백로그 에피소드(마지막 릴리스 이후 첫 unsigned tick)의 나이를 재서 **3일 임계를 넘긴 기간마다 1건**의 다이제스트("미배포 커밋 N건 · X일 경과 · `automation/release.sh` 안내")를 보내고, 릴리스가 착지하면 리셋한다. sha 가 전진해도 나이는 이어진다 — sha별로 리셋하면 활발히 머지할수록 다이제스트가 오지 않는다. 커밋 수는 미러 `rev-list --count` 로 읽되 관측 불가면 "수 미상"으로 말한다(추측 금지). raw SHA는 통지 전용이며 설치 대상은 계속 서명 검증 결과만 쓴다([소개](docs/기능소개/반복-수렴-스킵-알림.md)).
- **✅ 뒤는 사람 손이 아니다(2026-08-31, cha 지시)**: 릴리스 승인이 스킬별 ✅를 대체해도(VA-2) 실행은 자동이 아니었다 — `release.sh` 의 폴링은 세션과 함께 죽고, 태그 뒤 마운트는 `deploy_all.sh --apply` 를 누군가 따로 돌려야 했다(v1.0.140: 태그 뒤 스킬 6개 stale). 이제 `release.sh` 는 태그 뒤 **스스로** 노드 수렴을 기다려 `deploy_all.sh --apply --wait-converge` 까지 잇고(옵트아웃 `--no-deploy`, 실패는 exit 10 — 태그는 되돌리지 않는다), 워크스테이션 `systemd --user` 타이머(`automation/release_complete_install.sh`, 2분)가 **이미 ✅된 요청만** 완결한다 — `request`/`retire`/`plan` 은 절대 부르지 않고(회귀가 subcommand 를 대조), sha 별 3회 상한으로 지속 결함의 매 틱 전량 재배포를 막는다. 완결 주체가 워크스테이션인 이유는 위 「키는 로컬에만 둔다」 그대로다. 설치는 **메인 체크아웃**에서 한다(세션 워크트리는 `finish` 때 사라진다). 상세: [소개](docs/기능소개/릴리스-승인-자동-완결.md)
  - **완결 대상은 tip 이 아니라 릴리스다(2026-09-09)**: 완결기가 매 틱 origin/main HEAD 하나만 들고 물었기 때문에, 태그 직후 새 커밋이 머지되면 결정이 `live request is bound to a different HEAD` 로 서서 **이미 ✅ 받고 태그까지 잘린 릴리스**의 전량 반영이 재개되지 않고 완결 마커도 없어 적용 완료 DM 이 유실됐다(v1.6.3 실측: 수렴이 52분 걸려 시도 상한 3회를 소진한 뒤 origin/main 이 5커밋 앞섰다). 이제 `release_completion_target` 이 **origin/main 에서 도달 가능한 가장 가까운 릴리스 태그의 커밋**을 골라 `reconcile_authorized_release` 가 그 sha 로 워크트리를 옮겨 `deploy_all.sh --apply` 만 돌리고 마커를 남긴다 — `release.sh` 를 부르지 않으므로 **태그를 자르지 않고**, 옛 ✅ 가 새 tip 을 인가하는 문은 그대로 잠겨 있다. 노드가 아직 그 릴리스가 아니면(`RELEASE-MISMATCH` rc 4) `RECONCILE-DEFER` 로 **시도를 세지 않는다** — 세면 6분 만에 영구 포기가 된다. 팁이 곧 릴리스면 `TIP-IS-RELEASE` 로 무동작이라 정상 경로는 불변이다. 상세: [소개](docs/기능소개/릴리스-완결-리컨실.md)
- **헬스체크 허용 목록 갱신도 같은 ✅ 다(cha 지시, 2026-10-14: "릴리스 승인 카드에 허용 목록 갱신을 같이 넣기(✅ 한 번으로 둘 다)")**: 헬스체크 강제명령 래퍼는 운영자 홈 파일이라 수렴이 올리지 못한다. `release.sh` 가 승인 전에 새 트리의 기대 지문을 노드별로 계산해(`healthcheck_wrapper_release.sh plan` — 주 노드의 전용 빈 임시 디렉터리에 파일로 보내고 명시한 명령만 실행, 셸 stdin 에 데이터를 섞지 않는다) 설치본과 다른 노드를 `healthcheck-wrapper:<node>` 표면으로 승인 해시에 묶고, 카드에 `헬스체크 허용 목록도 갱신: <hosts>` 한 줄을 싣는다(표면이 없으면 카드·해시는 예전 바이트 그대로). 수렴 뒤 `deploy_all.sh --apply` 는 그 지문이 **지금 배포된 HEAD 의 승인된 카드**에 있었을 때만(`release_approval authorize-wrapper` → `release-authorize --surface --head`) 운영자 계정으로 설치하고 헤더 지문을 다시 읽어 대조한다 — sudo·authorized_keys 는 건드리지 않는다. 결과는 영수증 `wrapper_refresh` 와 적용 통지의 남은 조치 줄로 간다(실패는 영수증을 막지 않되 조용히 넘어가지도 않는다). 생성기 지문은 트리 위치가 아니라 내용만 말해야 한다(스테이징 트리 = 수렴 뒤 `/srv` 트리). 회귀는 `tests/unit/test_healthcheck_wrapper_release.py`. [소개](docs/기능소개/릴리스-카드-헬스체크-허용목록-갱신.md)
- **✅ 뒤 노드 수작업은 0 이어야 한다(cha 지시, 2026-10-14: "내가 승인한 후에 자꾸 노드에서 작업해야하는거 없으면 좋겠어")**: 새 기능이 노드 준비(디렉터리·설정 파일·cron)를 요구하면 그 준비를 배포기가 릴리스 안에서 하게 만든다 — 릴리스 노트·소개 문서에 "root 가 한 번 실행" 같은 소유자 단계를 남기지 않는다. 권한은 배포가 이미 쓰는 계정(ops·agent·peer)과 기존 NOPASSWD 범위 안에서만 쓰고, root·넓은 sudo 를 새로 요구하지 않는다(선례: 메모리 선택 요청함을 ops 인계 디렉터리 안 setgid+sticky 공유 디렉터리로 옮겨 root 준비를 없앴다, `automation/memory_relocate/choice_accounts.py`). 영수증의 `pending_owner_actions` 가 비어야 그 릴리스가 끝난 것이다.
- **소유자 결정 10/14(Q149): "peer 확인 빼고 가" — 메모리 선택의 peer 독립 확인 없음.** agent가 소유자 리액션과 승인 해시를 검증해 선택을 한 번 동결하고, peer는 agent 소유 결정 파일을 요청 바인딩 대조 후 실행한다.
- **남은 위험**: peer는 agent가 쓴 요청·결정 파일만 믿는다. agent 계정이 침해되면 두 파일을 함께 써서 peer 메모리의 어떤 항목이든 지우게 할 수 있다(소유자 카드 확인이 없으므로 범위 제한도 없다). 삭제 전 백업은 남는다. 또 agent가 멈춘 사이 누른 ⛔는 peer에 전달되지 않아, 이미 동결된 삭제가 다음 peer 틱(최대 2분)에 진행될 수 있다.
- 구현은 `automation/release_tag_lib.sh` 하나이고 `land.sh` 와 `release-tag.sh` 가 공유한다(회귀 고정: `tests/unit/test_release_tag.py`). 사본을 다시 만들지 않는다 — 갈라지면 두 경로가 서로 다른 태그를 자른다.
- 이 규칙의 태그는 2026-10-07 공개 우선 전환부터 **`orientpine/cytoplasm` main 의 커밋에 직접** 잘린다 — 개발 정본이 cytoplasm 이므로 반출 스냅샷이 따로 없고, `release.sh` 가 ✅ 뒤 그 커밋에 서명 태그를 달면 노드가 그 태그로 수렴한다. 릴리스 노트 게시는 「공개 릴리스 규칙」이 소유한다 — 저장소 역할은 「세 저장소 구분 규칙」 참조.

## 승인 메시지 단일성 규칙 (cha 지시, 2026-07-25)

**승인 게이트는 동일한 논리적 요청에 대해 이미 게시된 메시지가 있다면 이를 조용히 덮어쓰거나 중복 게시하지 않는다.**

- **금지**: (1) 동일한 `action_hash`를 가진 메시지를 중복 게시하는 것, (2) 저장된 `message_id`를 새 메시지 ID로 덮어써서 이전 메시지를 고아(Orphan)로 만드는 것.
- **절차**: 모든 승인 요청은 `automation/interop/approval_lifecycle.py` 파사드를 경유한다. 파사드가 lease 점유 → 상태 probe(6종 타입) → supersede(delete 후 record drop) → journal → 게시 → 커밋까지 임계구역 전체를 소유한다. 소유자가 이미 ✅/⛔를 누른 요청은 건드리지 않고 워처에게 양보한다(DEFER).
- **강제**: 산문만으로는 부족하다(이 문서의 원칙: 「mutating 경로에 결정론적 코드 가드 필수」). `tests/unit/test_approval_lifecycle_conformance.py`가 승인 producer 인벤토리를 들고 파사드 경유 여부를 기계적으로 검증한다. 예외는 소스 주석이 아니라 테스트 내 `_EXEMPT` 맵에 사유와 함께 등록해야 한다.
- **참조**: 상세 규약은 `docs/guide/watcher-cron-설계규약.md §(j)`, 구현 불변식과 금지사항은 `automation/interop/AGENTS.md`에 있다.
- **배경(사후 반영)**: 2026-07-25 drive-archive digest 중복 게시 및 message_id 덮어쓰기로 인해 소유자의 승인 ✅가 실종된 결함을 수리하며 도입되었습니다.
## 결과 통지 원채널 스레드 규칙 (cha 지시, 2026-08-22/23)

**승인 게이트를 가진 모든 스킬은 실행/취소/만료 결과를 지시가 시작된 채널의 스레드로 통지한다 — 2026-09-01 부터 승인(✅/⛔) 요청 자체가 `#agent-chat`의 **요청별 스레드**에 게시되므로 결과는 레코드의 `approval_thread_id` 로 **그 스레드**에 돌아온다(「요청별 승인 스레드 규칙」; v7~v8 의 kind별 `승인-<kind>` 스레드는 폐지). 구현은 `automation/interop/origin_notice.py` 하나뿐이고, 앞으로 만드는 스킬도 예외가 아니다.**

- **왜**: 2026-08-22 agent-chat에서 시작한 메일 발송의 결과가 "메일 발송 취소됨" 한 줄로 DM에 흩어졌다. 지시가 온 곳으로 결과가 돌아가야 대화가 이어지고, DM에는 승인 버튼만 남아야 소유자가 무엇을 눌러야 하는지 분명하다. 같은 날 mail에 적용했고 2026-08-23 소유자 지시로 전 스킬(budget·calendar·coordination·todo·meeting)에 일반화했다(소개).
- **새 스킬이 지켜야 할 것**: ① mutating CLI에 `--origin-channel-id`/`--origin-message-id`(선택)를 받아 레코드에 `origin_channel_id`/`origin_message_id`로 저장하되 **승인 해시 바인딩 밖**에 둔다(레거시 레코드는 필드 부재 허용). ② 결과 통지는 `origin_notice.deliver(api=…, transport_factory=…, record=…, thread_name=…, content=…, fallback=…, outcome=…)` 경유 — 스레드 해석(레코드 `approval_thread_id` 최우선(생성 0) → 지시 메시지 앵커, 400=기존 스레드 재사용 → 채널 스레드)·종결 표시(`ThreadOutcome` 접두어+아카이브, `THREAD-CLOSE-FAIL`)·`NOTIFY-THREAD-FAIL` 마커·폴백 의미는 헬퍼가 소유한다. 종결 결과(실행·취소·만료)에는 `outcome=` 을 넘기고 중간 ACK 에는 넘기지 않는다. **자체 스레드 생성 코드 금지**(사본 증식은 「승인 메시지 단일성 규칙」이 막으려던 것과 같은 문제). ③ 문구는 대상·id·사유를 담는다("…됨" 무맥락 고정문구 금지). ④ 통지는 best-effort — 어떤 실패도 tick·exit code·영수증·원장을 바꾸지 않고 `NOTIFY-FAIL`로 남긴다. E2E·주입 승인은 `NOTIFY-SKIP`으로 실제 통지를 열지 않는다. ⑤ 승인 요청·리마인더·게이트 경로는 건드리지 않는다 — 이 규칙은 결과 통지의 목적지만 정한다.
- **고정 마스킹 규칙이 우선한다**(내용을 판정하는 민감 분류가 아니라 스킬별로 정해진 표시 규칙 — 2026-10-09 민감 판정 제거 뒤에도 유지): 스킬의 기존 마스킹 규칙(캘린더 내용·금액·위키 본문·특허)은 스레드 문구에도 그대로 적용된다. 채널에 내보낼 수 없는 정보는 마스킹(calendar: action 종류·draft id만)하거나 통지 자체를 제외(wiki·patent-prep)하고, 그 사유를 conformance 예외 맵에 적는다.
- **강제**: `tests/unit/test_origin_notice_adoption_conformance.py` — 승인 producer를 가진 모든 스킬(`approval_conformance_inventory.APPROVAL_PRODUCERS`)은 스크립트가 `origin_notice`를 참조해야 하고, 아니면 `_RESULT_NOTICE_EXEMPT`에 사유와 함께 등록되어야 한다. 스레드 생성 API 호출은 헬퍼 밖에서 금지(`_THREAD_API_ALLOWED` 예외: meeting 게이트웨이 플러그인 — INTEROP_RUNTIME 경로를 보장받지 못함). 산문이 아니라 코드가 진실이다.
- **배포 주의**: 헬퍼는 interop 런타임(`INTEROP_RUNTIME`)에 실려야 한다 — 스킬만 재배포하고 런타임이 낡으면 통지가 `NOTIFY-FAIL`로 남는다(실행 자체는 영향 없음).

## 요청별 승인 스레드 규칙 (cha 지시, 2026-09-01)

**소유자 전용 승인은 요청 하나에 스레드 하나다 — 승인 카드(✅/⛔)·리마인더·실행/취소/만료 결과가 `#agent-chat` 아래 같은 스레드에서 완결되고, 종결되면 이름에 상태 접두어(`✅ 완료 ·`/`⛔ 취소 ·`/`⌛ 만료 ·`)를 붙여 아카이브한다. kind별 고정 스레드(`승인-<kind>`)는 레거시 호출(`request=None`)에만 남고 범위 내 생산자는 쓰지 않는다.**

- **왜**: 승인은 kind 스레드(`approval_directory.agent_chat_thread`)에, 결과는 지시 메시지 스레드(`origin_notice`)에 갈라져 한 건의 처리 경로를 두 곳에서 따라가야 했다(2026-09-01 소유자: "일의 처리 경로가 한번에 관리"). 요청 단위로 묶고 종결 시 아카이브하면 `#agent-chat` 의 **활성 스레드 목록이 곧 진행 중 요청 보드**가 된다([소개](docs/기능소개/요청별-승인-스레드.md)).
- **새 생산자가 지켜야 할 것**: ① `resolve_new_binding(kind, directory, owner, request=RequestThread(title=…, origin_channel_id=…, origin_message_id=…))` — 지시 메시지가 `#agent-chat` 소속이면 그 메시지에 앵커(400=이미 있음 → message id 재사용), 아니면 **요청을 알리는 안내 메시지를 `#agent-chat` 에 먼저 게시하고 그 메시지에 스레드를 건다**(2026-09-09). 지시 메시지가 이미 `#agent-chat` 아래 **스레드 안**에 있으면 새 스레드를 열지 않고 그 스레드에 카드를 올린다(2026-10-18 23:00 KST 형 "모든 요청은 별도 스레드로 열게 해" — 대화와 카드가 한 스레드에 남는다). 스레드 이름은 `<종류 아이콘> <kind 라벨> · <제목≤40>` 이다(아이콘은 2026-10-16 「스레드 제목 규칙」) — 바뀐 것은 스레드가 **무엇에 매달리는가**뿐이고, 채널에는 「🔔 승인 대기 · <라벨> · <제목>」 한 줄이 남아 결정 대기가 한눈에 보인다. 안내 문구는 `request_thread_notice` 단일 정의이며 스레드 이름이 이미 보여 주는 마스킹된 제목만 담아 공개 범위를 넓히지 않는다. 제목은 **생산자가 이미 마스킹한 값**이다: mail·budget=제목, todo=할 일 제목, coordination=조율 라벨, **calendar·wiki·patent-prep·repair=id 만**, **obsidian=검토 대상 노트 파일명(확장자 포함, 디렉터리·본문 제외)** — 소유자 전용 비공개 표면에서 `옵시디언 노트 승인 요청 · <노트명>`으로 표시한다(2026-09-08 소유자 요청). ② `binding.channel_id`(=스레드 id)를 레코드에 `approval_thread_id` 로 **승인 해시 밖**에 저장한다(레거시 레코드는 필드 부재 허용 — `origin_notice` 는 그때 옛 경로를 탄다). ③ 결과 통지 dict 에 그 필드를 실어 `deliver(..., outcome=ThreadOutcome.DONE|CANCELLED|EXPIRED)` — 종결에만, 중간 ACK 는 outcome 없음. ④ 자체 스레드 생성·PATCH 코드 금지 — 종결 표시도 `origin_notice.close_thread` 하나다.
- **정책 버전은 올리지 않았다(S6)**: 요청별 스레드도 `AGENT_CHAT_THREAD` facts(type 11/12 + parent=agent-chat)를 그대로 만족하므로 저장된 v7/v8 바인딩 해석·리액션 워처·리마인더·lifecycle probe 는 전부 무변경이다(모두 레코드 `channel_id` 를 읽는다).
- **강제**: `tests/unit/test_request_thread_adoption_conformance.py` — 배포 소스의 `resolve_new_binding` 호출부 인벤토리에서 `request=` 없는 호출은 RED, 같은 패키지에 `approval_thread_id` 가 없어도 RED. 예외는 공급망 표면(`skill_gate_surface.SupplyChainSurface.new` — skill-*·release 는 peer 봇이 봐야 하는 `#approvals`)뿐이고 사유와 함께 `_KIND_THREAD_EXEMPT` 에 적는다. 산문이 아니라 코드가 진실이다.
- **배포 순서**: interop 런타임이 **먼저** 새 `resolve_new_binding` 시그니처를 가져야 한다 — 스킬만 앞서면 옛 런타임이 `request=` 를 `TypeError` 로 거부해 승인이 게시되지 않는다(fail-closed, 조용하지 않다). `release.sh` 의 전량 수렴이 둘을 같이 올린다.
- **Discord 제약**: 스레드 이름 100자(`request_thread_name` 이 절단), 길드당 활성 스레드 1,000개 — 종결 아카이브가 상한을 지키고, 아카이브된 스레드는 새 메시지에 자동으로 다시 열린다. 이름 변경·아카이브는 best-effort 라 실패해도 실행·영수증·exit code 는 불변이다.

## 스레드 제목 규칙 (cha 지시, 2026-10-16)

**오토파지가 여는 스레드는 이름이 종류 이모지로 시작하고, 첫 메시지 맨 위에 `## <이모지> <무엇·날짜·건수>` 큰 제목 한 줄이 붙는다 — 앞으로 만드는 스레드도 예외가 아니다.** 소유자 지시: "캘린더 다이제스트도 기관메일 다이제스트처럼 제일 처음에 좀 큼지막하게 제목을 써서 이모지와 함께 앞으로 모든 이런 스레드에는 제목을 좀 크게 붙여줘".

- 승인 요청 스레드는 `approval_surface.KIND_ICONS`·`request_thread_title` 이, 결과 스레드는 `origin_notice.thread_icon`·`resolve_thread_id` 가 단일 정의다. 새 승인 종류는 `KIND_ICONS` 에 이모지를 더하고(빠지면 `tests/unit/test_thread_titles.py` 가 RED), 새 결과 스레드 이름은 `_THREAD_ICONS` 에 앞말을 더한다. 스레드를 직접 여는 코드를 새로 만들지 않는다.
- 제목은 승인 카드와 **따로** 올라가는 메시지다. 승인 카드 본문·해시·참조 줄·파서가 읽는 형식은 바꾸지 않는다(`render_version` append-only 그대로).
- ✅·⛔·⌛ 는 제목 이모지로 쓰지 않는다(결정 반응·종결 접두어). 제목 게시 실패는 `THREAD-TITLE-FAIL` 한 줄이고 승인·통지를 막지 않는다. 표와 시나리오: [소개](docs/기능소개/스레드-큰-제목.md)

## 소유자 메시지 계약 규칙 (cha 지시, 2026-09-09)

**소유자 대면 메시지는 `automation/interop/owner_message.py` 의 봉투 5필드(대상 · 사실 · 위치 · 인계 · 되돌리기)로 만들고, 문구는 스킬이 조립하지 않고 `render(message, destination=…)` 하나가 만든다 — **위치는 문장이 아니라 좌표**이고 지역 참조가 아니면 유효한 링크 또는 검색 안내로 가리킨다. 앞으로 만드는 스킬도 예외가 아니다.** 이 규칙은 「결과 통지 원채널 스레드 규칙」·「요청별 승인 스레드 규칙」을 대체하지 않고 그 위에 얹힌다 — 그 둘이 **어디로** 보낼지를 정하고, 이 규칙이 **무엇이 담겨야** 하는지를 정한다.

- **왜**: 2026-09-09 결과 통지가 "스레드에 올렸다" 고만 말해 소유자가 그 스레드를 직접 찾아야 했다. 좌표를 쥐고 있던 쪽은 에이전트였다 — 위치를 **필드로 강제**하지 않으면 아는 좌표도 문구에서 사라지고, 목적지가 바뀌어도 문구가 그대로 실려 가 "이 스레드에서 ✅" 가 스레드 아닌 곳에 도착한다. 5필드 상한은 그 반대 방향의 실패(메시지가 길어져 아무도 읽지 않는 것)를 막는다 — 필드 추가는 이 규칙의 비목표다.
- **새 스킬이 지켜야 할 것**(불변식만 — 봉투 채우기·`Ref` 만들기·마스킹 우선순위·원장 등록 **절차는** [`docs/guide/스킬-제작.md` 「소유자 메시지 계약」](docs/guide/스킬-제작.md#소유자-메시지-계약-전-스킬-필수--2026-09-09)이 단독 소유하고 여기에 복사하지 않는다): ① 소유자에게 갈 문구는 봉투 하나로 만들고 스킬은 자기 문구 조립기를 갖지 않는다 — 조치가 없으면 `Action(verb="none")`, 후속 실행이 없으면 `agent_next=None` 이며 렌더러가 그것을 **명시**한다(침묵 금지). ② **목적지가 바뀌면 다시 렌더한다** — 파사드(`origin_notice.deliver`, `owner_notice.notify_owner/_dm`)가 실제 목적지마다 렌더하므로 스킬이 문자열을 두 번 만들지 않는다. ③ 렌더러의 지역 참조(URL 없음)는 `scope="none"` · `scope="self"` · `scope="channel"`·`"message"` 이면서 참조와 목적지의 `channel_id` 가 **둘 다 존재(None 아님)하고 같을 때**뿐이다 — 두 id 가 모두 없어도 지역 참조가 아니며, `scope="resource"` 는 좌표와 무관하게 **항상 비지역 참조**다. 네 번째 링크 면제는 네이티브 답글이 이미 대상을 지목할 때라는 **작성 정책**이지 렌더러가 답글을 탐지한다는 뜻은 아니다. ④ 좌표를 모르면 **추측하지 않는다** — `space="unknown"` 이나 좌표 결손은 링크를 빼고 생산자가 준 검색 라벨/키로 내려간다(`@me` 를 찍어 만든 DM 링크는 형태만 맞고 가리키는 곳이 틀리다). ⑤ 렌더 시점에 Discord API 를 부르지 않고 링크 유효성도 프로브하지 않는다 — 좌표는 바인딩 때 레코드에 `approval_guild_id`/`approval_thread_id` 로 **승인 해시 밖**에 남는다. ⑥ 봉투를 만들 수 없거나 런타임이 낡으면 기존 문자열 경로를 그대로 쓴다 — 능력 프로브(`ACCEPTS_OWNER_MESSAGE`)와 `except ImportError` 가 그 갈림길이고, **어느 쪽도 새 예외를 던지지 않는다**.
- **고정 마스킹 규칙이 링크 규칙을 이긴다**: 스킬의 기존 마스킹 규칙(캘린더 내용·금액·위키 본문·특허)이 봉투에도 그대로 적용된다 — 채널에 낼 수 없는 내용은 `fact` 에서 빼고, 좌표조차 낼 수 없으면 위치를 싣지 않는다(위키·patent-prep=좌표 없음, calendar 결과·공유 출력=draft id 만). "링크가 있어야 한다" 는 마스킹을 뚫는 근거가 되지 않으며, 제외한 사유는 conformance 원장에 적는다.
- **캘린더 승인 카드 한정 예외(2026-09-20 소유자 승인)**: 소유자 전용 승인 스레드의 v3 이상 카드에는 제목·시작/종료·생성/수정/삭제 내용을 표시한다. 기존 `approval_surface`/directory 바인딩으로 게시 직전 목적지를 검증하며 공유·미상 표면에는 상세를 보내지 않는다. 부모 채널 안내·스레드 이름·결과 통지는 계속 마스킹한다. 신규 v4는 v3의 상세를 여러 줄과 사람이 읽는 KST 시각으로 표시하고, v1/v2/v3 게시 바이트·기존 승인 해시는 불변이다. 신규 상세 카드의 렌더/길이 오류는 구버전 강등 없이 게시 거부다. 저장하지 않은 이전 값은 만들지 않는다. 상세: [calendar 절대 규칙](skills/calendar/SKILL.md#절대-규칙-안전), [소개](docs/기능소개/kfx-일정-다이제스트-일별-스레드.md).
- **한 줄 판본(2026-10-19)**: `owner-ko-v5` 는 사실 한 줄(+원본 링크)만 보이고 봉투는 기록에만 남는다. 형 채널로 가는 정기 알림(첫 채택: 일정·마일스톤 리마인더)용이다.
- **Discord 표시 판본(2026-09-20)**: `owner-ko-v1`의 다섯 줄은 바이트 동결이고 `owner-ko-v2`를 추가했다(append-only). v2는 종류별 아이콘·굵은 제목, 줄바꿈을 보존한 사실 인용, 결정·만료 한 줄, 취소 효과·다음 실행을 표시한다. 지역 위치와 해당 없는 되돌리기는 생략하되 **봉투 필드는 그대로**이며, 비지역 링크·검색 안내와 실제 되돌리기 조치는 남긴다. 참조 키는 머리글 대신 8자 하단 표시, 시각은 원래 시간대 그대로다. `owner-ko-v3`(2026-09-22, append-only)는 v2 와 같은 배치에 참조 키만 **전부** 표시한다 — 8자 절단이 `of_00924` 처럼 공통 접두어로 자리를 채워 대상을 가리키지 못했다. 첫 채택은 Plaud 노트 카드 v7(정규 녹음 id)이다. 신규 승인 생산자는 새 카드 판본에 v2를 명시하고, 저장된 카드 판본은 엄격히 재생한다. 위키는 `wiki_gate.py` 수정 동결 때문에 기존 v2 카드이며, 통지 파사드는 생산자가 명시한 봉투 판본을 바꾸지 않는다. [사용 예와 이관 범위](docs/기능소개/소유자-메시지-계약.md).
- **best-effort 는 불변**: 봉투는 통지의 **문구**만 바꾼다 — `owner_notice` 의 "절대 예외를 던지지 않는다" 와 `origin_notice` 의 best-effort 계약, tick·exit code·영수증·원장은 그대로다. `origin_notice.deliver` 는 스레드·폴백의 실제 목적지에 맞춰 재렌더하고, 그 렌더·선택 모듈 import 실패는 `NOTIFY-RENDER-FAIL` 을 남긴 뒤 기존 `content` 로 내려간다. 스레드 배달 실패는 `NOTIFY-THREAD-FAIL` 후 폴백으로 가되 폴백 자체의 예외는 호출자가 처리한다. 반면 `owner_notice.notify_owner`·`notify_owner_dm` 은 렌더·import 실패 시 **아무 문구도 보내지 않고** `NOTIFY-FAILED` 를 남겨 `False` 를 반환하며 예외를 던지지 않는다 — 기존 `content` 재전송이 아니다. 이미 게시된 문구는 고쳐 쓰지 않는다(`render_version` 은 append-only).
- **강제**: `tests/unit/test_owner_message_adoption_conformance.py` — 배포 소스의 AST 를 훑어 통지 호출의 봉투 채택과 승인 카드 생산자별 등록 렌더 경로(`_CARD_RENDER_ROOTS`)를 검사하고, 미채택 발신자나 공용 정의 밖의 `discord.com/channels` 리터럴은 RED 로 만든다. 능력 폴백 짝과 카드 렌더 도달성의 정확한 채택 조건은 위 가이드가 단독 소유한다. 예외는 소스 주석이 아니라 테스트 안 원장(빈 이관 원장 `_RAW_CONTENT_EXEMPT`·구조적 영구 예외 `_PERMANENT_RAW_CONTENT_EXEMPT`·`_LINK_LITERAL_ALLOWED`·`_NOT_OWNER_FACING`)에 사유와 함께 등록하고, 렌더 경로를 다른 모듈로 옮기면 **같은 커밋에서** 그 생산자의 루트를 고친다. `*_not_stale` 가드가 낡은 줄을 RED 로 만든다 — 산문이 아니라 코드가 진실이다.
- **배경(사후 반영)**: 2026-09-09 소유자 지시 — "스레드에 올렸다고 하고 어느 스레드인지 인용을 안해주니까 내가 찾아야해. 이런 문제와 같이, 메세지에서 사용자 경험을 개선하고 싶어. 모든 스킬과 앞으로 개발할 스킬 지침에, 사용성을 개선하도록 하는 기능 추가해줘." 계획 `.omo/plans/owner-message-ux.md` 가 그 사건의 원인 6종(공용 링크 함수 부재 · 해석한 `guild_id` 폐기 · 계약 부재 · 폴백에서 재렌더 누락 · 미상을 DM 으로 착각 · 청킹이 URL 절단)을 닫았고, 이 절은 그중 "앞으로 개발할 스킬 지침에" 를 문서로 고정한 것이다.

## 승인 요청 답장 링크 규칙 (cha 지시, 2026-09-23)

**에이전트가 실행하는 승인 요청 CLI 는 카드를 게시한 직후 `APPROVAL-THREAD <키>=<값> url=<스레드 링크>` 한 줄을 stdout 에 내고, 에이전트는 소유자 답장에 그 URL 을 그대로 싣는다. 그 줄은 `automation/interop/thread_pointer.approval_thread_line` 하나가 만든다 — 앞으로 만드는 승인 스킬도 예외가 아니다.** 「소유자 메시지 계약 규칙」이 **코드가 보내는** 문구의 위치를 필드로 강제한다면, 이 규칙은 **에이전트가 쓰는** 답장에 같은 좌표를 건넨다 — LLM 답장은 봉투를 거치지 않으므로 좌표가 CLI 출력에 없으면 어디에도 없다.

- **왜**: 2026-09-23 mail compose 와 todo 등록 답장이 "승인 스레드에 게시했습니다 … 승인 스레드에서 ✅" 로만 끝나 소유자가 스레드를 직접 찾아야 했다. 좌표는 레코드(`approval_guild_id`·`approval_thread_id`, 승인 해시 밖)에 이미 있었고 CLI 가 `COMPOSED draft=<id> posted=1`·`REQUESTED hash=…` 만 출력해 에이전트 손에 닿지 않았다.
- **새 승인 스킬이 지켜야 할 것**: ① 게시 성공 직후 저장 레코드(또는 반환 entry)의 두 좌표로 `approval_thread_line(키, 값, 레코드)` 를 출력한다 — 형식 문자열을 직접 조립하지 않는다. ② 좌표를 모르면 헬퍼가 `url=unavailable search=<값>` 을 내고 링크를 **추측하지 않는다**. 게시하지 않았으면 줄을 내지 않는다. ③ 헬퍼 import 실패는 `APPROVAL-THREAD-UNAVAILABLE`(stderr) 한 줄로 끝나고 이미 게시된 승인·exit code 를 바꾸지 않는다. ④ SKILL.md 가 답장에 그 URL 을 싣도록 지시한다. 절차는 [`docs/guide/스킬-제작.md` 「승인 요청 답장 링크」](docs/guide/스킬-제작.md#승인-요청-답장-링크-전-스킬-필수--2026-09-23)가 단독 소유한다.
- **고정 마스킹 규칙이 이긴다**: 좌표조차 낼 수 없는 생산자(위키·patent-prep)는 줄을 내지 않고 사유를 conformance 원장에 적는다. 줄에는 id 와 URL 만 실리며 제목·본문은 싣지 않는다.
- **강제**: `tests/unit/test_approval_thread_pointer_conformance.py` — `approval_conformance_inventory.APPROVAL_PRODUCERS` 의 모든 생산자가 `_ADOPTED`(채택 파일이 공용 헬퍼를 import 해 부른다) 또는 사유 있는 `_EXEMPT` 중 정확히 하나에 있어야 하고, `APPROVAL-THREAD ` 형식 리터럴이 헬퍼 밖에 있으면 RED 다. 새 승인 생산자는 lifecycle conformance 가 인벤토리 등록을 강제하므로 여기서 결정을 피할 수 없다 — 산문이 아니라 코드가 진실이다. [소개](docs/기능소개/승인-요청-답장-스레드-링크.md)

## 승인 경로 완결 규칙 (cha 지시, 2026-10-07)

**게이트 대상 외부효과는 「표준 승인 카드 생산자 + 카드 ✅ 워처 + 실행 영수증」을 한 벌로 갖는다. 에이전트는 즉석 초안을 만들거나 소유자에게 채팅 메시지에 ✅ 를 요청하지 않는다 — 전용 스킬 명령이 없는 작업은 공용 「그 밖의 외부효과」 경로(`python3 -m automation.effect_request request`)로 카드를 올린다(2026-10-17 「모든 요청 승인 경로 규칙」).** 채팅 메시지의 ✅ 는 어떤 워처도 읽지 않는다 — 승인은 해시에 묶인 카드의 ✅ 만이다.

- **왜**: 2026-10-07 경로·본문을 받은 Obsidian 노트 저장은 push 직전 게이트는 있었지만 카드를 올릴 명령도, ✅ 를 읽을 워처도 없었다. 에이전트는 초안을 손으로 만들고 "이 메시지에 ✅ 로 승인해 주십시오" 라고 썼고, 소유자가 누른 ✅ 는 아무것도 실행하지 않았다. 게이트만으로는 외부효과가 승인 가능해지지 않는다.
- **새 외부효과·승인 스킬이 지켜야 할 것**: ① `configs/external-effect-tools.yaml` 에 규칙을 더하면 그 효과의 카드 생산자(공용 `approval_lifecycle` 파사드 경유, 에이전트가 부를 수 있는 요청 명령)를 함께 만든다. ② 생산자마다 ✅ 뒤 실행하는 워처(선언된 no-agent cron 또는 systemd 유닛)와 그것을 덮는 테스트를 둔다. 사람이 실행하는 설계라면 `manual:` 사유를 적는다. ③ 실행 뒤 원격에서 다시 읽어 확인하고 결과를 `origin_notice` 로 같은 스레드에 남긴다. ④ SKILL.md 는 요청 명령만 안내한다 — 텍스트 fallback(`실행 <id>`)이 있어도 에이전트가 먼저 제안하거나 요청하지 않는다. 절차는 [`docs/guide/스킬-제작.md` 「승인 경로 완결」](docs/guide/스킬-제작.md#승인-경로-완결-전-스킬-필수--2026-10-07)이 소유한다.
- **강제(빌드)**: `tests/unit/test_gated_effect_paths_conformance.py` — denylist 의 모든 규칙이 `GATED_RULES` 에 카드 생산자(또는 사유)를, `APPROVAL_PRODUCERS` 의 모든 생산자가 `PRODUCER_WATCHERS` 에 워처(선언된 cron·트리의 systemd 유닛·`manual:` 사유)와 테스트 파일을 갖지 않으면 RED 다.
- **강제(런타임)**: 게이트웨이 `transform_llm_output` 훅이 `automation/interop/chat_approval_guard.py` 로 모든 답장을 검사한다. 승인 카드 링크 없이 ✅ 를 요청하는 줄은 "이 작업은 표준 승인 경로를 거치지 않았다" 안내로 바뀐다(회귀 `tests/unit/test_chat_approval_guard.py`). 대리 요청 본문(`owner_proxy.dispatch_text`)에도 같은 지시가 붙는다.
- **점검표**: [docs/guide/승인-경로-점검표.md](docs/guide/승인-경로-점검표.md)

## Drive 업로드·공유 승인 규칙 (cha 지시, 2026-10-14)

**파일을 Drive 에 올려 누군가에게 공유하는 일은 메일 `compose --drive-share <파일> --share-with <이메일>` 하나로만 한다 — 업로드·사람별 보기 권한·링크 메일이 승인 카드 하나의 ✅ 로 함께 실행되고, 어느 단계든 실패하면 메일은 나가지 않는다.** 소유자 지시: "오토파지에 \"드라이브 업로드·공유\" 승인 경로를 새로 만들고 그걸로 보냄 (몇 시간, 다음에도 씀)".

- **왜**: 2026-10-14 대리 요청(약 192 MB 실행 파일을 보기 전용으로 공유하고 링크를 메일로 회신)에서 메일 승인은 있었지만 Drive 업로드·공유 승인 경로가 없어 에이전트가 멈췄다.
- **무엇이 묶이나**: 파일 sha256·크기, Drive 폴더·파일명(`autophagy/공유/` 아래), 공유받는 사람, 역할 `reader` 가 compose 초안의 승인 해시(`triage_core.draft_sha256` 의 `drive_share`)에 들어간다. 새 카드·새 워처는 없다(「단일 게이트 절차 재사용」).
- **링크 공개는 없다**: 권한은 `type=user, role=reader` 만 만들고, 부여 전·후에 파일 권한이 "소유자 + 지정한 사람" 이 아니면(anyone·domain·group·writer·다른 사람) 멈춘다. 에이전트가 터미널에서 `gws drive permissions create|update` 로 직접 공유하는 것은 외부효과 규칙 `gws_drive_permission_grant` 가 막는다.
- **강제**: `tests/unit/test_mail_drive_share.py`(해시 바인딩·공개 권한 거부·실패 시 미발송·✅ 없으면 무동작), `tests/unit/test_gated_effect_paths_conformance.py`(규칙 → 카드 생산자). 절차는 `skills/mail/SKILL.md` §4-2, [소개](docs/기능소개/메일-Drive-공유-링크.md).

## 모든 요청 승인 경로 규칙 (cha 지시, 2026-10-17)

**오토파지는 어떤 요청도 "이 작업은 승인 경로가 없다"로 끝내지 않는다. 외부효과가 있는 요청은 전용 스킬 카드로, 전용 스킬이 없으면 공용 「그 밖의 외부효과」 카드(`python3 -m automation.effect_request request drive-save|command`)로 형 승인을 받고, ✅ 한 번이면 워처가 실행한다. 형만 할 수 있는 일만 명시적 예외로 거절한다.** 소유자 지시(오토파지 답을 붙여 넣고 덧붙인 말): "Bcc승인 경로랑" … "이 경로 만들어 모든 요청에 대한 경로가 있어야 해".

- **왜**: 2026-10-17 세미나 신청자 41명에게 숨은참조(BCC) 안내 메일을 보내는 요청과, 조회 결과 파일의 Drive 저장 요청이 둘 다 "승인 경로가 없다"로 멈췄다. 메일 compose·mailon 발송에 BCC 인자가 없었고, 공유 없는 Drive 저장에는 카드가 없었다.
- **BCC 다수 메일**: 메일 `compose --bcc <주소> | --bcc-file <파일> [--bcc-column <열>]` — 카드는 받는 사람 수(받는 사람·참조·숨은참조)·도메인별 인원·제목·본문 미리보기만 싣고 BCC 주소는 싣지 않는다. 주소 목록은 승인 해시와 발송 argv 에 묶이고, mailon 이 웹메일 폼의 BCC 집합이 정확히 같은지 확인한 뒤에만 보낸다(한 통에 전원 — 실패하면 발송 전 단계인지, 결과 미확인인지를 나눠 알린다). 절차 `skills/mail/SKILL.md`.
- **Drive 저장**: `effect_request request drive-save --file <파일> --summary <한 줄>` — 카드에 파일 이름·Drive 위치(`autophagy/<카테고리>/[과제/]<연도>/…`)·공유 범위(비공개, 형 계정만)·크기·sha256 이 실리고, ✅ 뒤 동결 사본을 `drive_outputs.publish` 로 올려 소유자 전용 권한과 재다운로드 sha256 을 확인한다. 다른 사람과 공유는 여전히 메일 `compose --drive-share` 다.
- **그 밖의 외부효과**: `effect_request request command --summary … --target … -- <argv>` — 카드에 할 일·영향 대상·실행 명령 전체(해시에 묶임)가 실리고, ✅ 뒤 정확히 한 번 실행해 종료 코드와 가린 출력 끝부분을 남긴다(죽은 틱이 다시 실행하지 않는다).
- **명시적 예외(형만 하는 일)**: 자격증명 변경(비밀번호·키·토큰), 결제·송금·구매, 로그인·2단계 인증·OAuth 동의, 관리자 권한(sudo), 승인 반응 대리 누름 — 명령이 `EFFECT-REQUEST-REFUSED code=owner-only:<종류>`(exit 4)와 사유로 거절하고 에이전트는 그 사유를 그대로 전한다. 전용 경로가 있는 효과(메일·일정·할 일·Obsidian·Drive 공유·특허 반출)는 `code=dedicated:<종류>` 로 그 명령을 안내한다. 목록 정본은 `automation/effect_request/policy.py`.
- **승인 경로가 아니라 도구가 없는 일**: 오토파지에 연결된 도구 자체가 없는 일(예: 카카오톡 방 게시)은 "이 일을 할 도구가 없다"로 구분해 보고한다 — 승인 경로 부재로 적지 않는다.
- **강제**: `tests/unit/test_effect_request.py`(카드 내용·소유자 ✅ 만 실행·⛔ 우선·동결본 변조 거부·한 번 실행·예외 거절), `tests/unit/test_mail_bcc.py`·`test_mailon_bcc.py`(BCC 해시·argv·정확한 집합), `tests/unit/test_gated_effect_paths_conformance.py`(일반 안전망 규칙 → `effect_request` 생산자). 점검표 [docs/guide/승인-경로-점검표.md](docs/guide/승인-경로-점검표.md), [소개](docs/기능소개/모든-요청-승인-경로.md).

## 메모리 상한 규칙 (cha 지시, 2026-10-14)

**Hermes 자체 메모리 상한은 저장소가 정하고 릴리스가 노드에 쓴다 — USER.md 3,000자 · MEMORY.md 3,300자, agent·peer 같다. 노드에서 손으로 고치지 않는다.** 소유자 결정(Q138): 「①②③ 다 (추천: ① 오늘, ②·③은 이어서)」 — ① 상한 올리기, ② 사람 정보를 USER.md 밖으로, ③ 매 턴 자동 회상.

- 값은 `automation/hermes_memory_config.LIMITS` 한 곳이고 큐레이터 `CAPS` 가 그것을 읽는다. 바꾸려면 그 상수를 고쳐 릴리스한다 — 선언 `v2:config` 가 계정 설정을 관측하고 `converge_config` 가 맞추며 agent+peer 를 함께 재시동한다.
- 상한을 내리기 전에 파일을 먼저 줄인다 — 업스트림 NousResearch/hermes-agent#133028(열림): 파일이 상한보다 크면 정리·삭제 쓰기까지 거부된다. 모듈이 그런 내리기를 rc 7 로 막는다.
- 상한은 프롬프트 예산이다(매 턴 늘어난 만큼 싣는다). 공간이 다시 차면 상한을 더 올리기 전에 사람 정보·처리 규칙이 USER.md 에 들어와 있는지(②) 먼저 본다. [소개](docs/기능소개/메모리-상한-올리기.md)
- ② USER.md 는 사용자 본인만 담는다: `PERSON_REFERENCE`는 Obsidian 사람 노트로, USER.md의 `AGENT_RULE`은 MEMORY.md로 옮긴다. **2026-10-15 소유자 지시로 새 제안은 한 줄당 메시지 하나**다. 봇이 ➡️ 이동·📌 유지·🗑️ 삭제 세 리액션을 미리 달며 소유자 반응 하나로 해당 줄만 동결한다(추가 최종 승인 없음, 무반응=보류·복수=유지). 메시지 ID·원문 해시·기존 요청 키를 검증한다. 소유자 반응 없는 미결 배치만 옛 메시지를 보존한 채 감사 보관하고 같은 스레드에 개별 재게시한다. 반응한 배치는 예전 계약 그대로다. 삭제는 agent·peer 양쪽 두 네이티브 파일에서 정확히 같은 글만 백업 후 지우고 agent는 동일 선택의 peer 영수증을 기다린다. Q149의 agent-only 검증·token-less peer는 유지한다. SOUL 자동 회상 줄과 분류 규칙은 불변이다. [소개](docs/기능소개/메모리-사람-규칙-정리.md)

## 후속 과제 기록 규칙 (cha 지시, 2026-07-26)

**작업 중 발견했으나 이번 범위에서 처리하지 않은 사항은, 요청 사항을 마무리한 뒤 `docs/features.md`에 후속 과제로 기록한다 — 기록까지 마쳐야 작업이 종결된다.**

- **왜**: 후속 과제는 작업 과정에서만 발견된다. 제때 기록하지 않으면 QA 증적이나 세션 로그 속에 묻혀 사라지며, 세션 종료 후에는 그 맥락을 아는 주체도 없어진다.
- **어디에**: **`docs/follow-ups.md`** (2026-08-03 분리 — features.md가 103KB까지 부풀어 무엇이 남았는지 안 보였다. 현황판에는 묶음별 잔량 요약표만 둔다)(2026-07-29 분리 — PLAN의 "신규 아이디어"는 이제 진짜 새 기능 아이디어 전용이다). 기능/작업 단위로 **묶음 항목 1개 + 하위 불릿**으로 작성하고, 상세 증적 경로(`docs/qa/<wave-id>/...`)는 묶음 끝에 한 번만 병기한다. 계획 문서에 이미 반영된 건은 wave ID를 병기하여 PLAN "개발 예정"으로 옮긴다(features.md 사용법 규칙 준수).
- **손댈 수 없는 것은 별도 문서로 옮긴다 (cha 지시, 2026-08-26)**: 소유자·노드에서만 닫히는 것(OWNER), 동결·벤더·외부에 막힌 것(BLOCKED), 조건이 성립하기 전에는 조치하지 않는 것(OBSERVE), 이미 닫힌 것(해소)은 **`docs/follow-ups-deferred.md`**로 옮긴다. `follow-ups.md`에는 **지금 이 저장소가 손댈 수 있는 열린 작업만** 남는다. 이것은 **삭제가 아니라 이동**이며 원 `##` 헤딩을 양쪽에서 그대로 유지해야 한다 — 회계 가드 `tests/unit/test_features_board_conformance.py` A9가 두 문서를 합쳐 읽어 이동과 삭제를 가르기 때문이다(헤딩이나 불릿 첫 줄을 고치면 삭제로 읽힌다). 배경: 그 가드가 FS3 baseline 불릿을 고정하고 있어 「계속 개선하라」는 지시에도 문서가 줄지 않았다 — 129건 중 73건이 물리적으로 삭제 불가였다.
- **무엇을 적나**: 각 불릿은 **"무엇이 문제인지 → 어떻게 조치할지"** 형태로 작성하며, **영향 범위와 심각도**를 명시한다(예: "보안 문제 아님 — 라이브 readlink가 권위 소스", "검증 메시지 오류일 뿐 마운트 로직과 무관"). 판단 근거를 남기지 않으면 다음 작업자가 처음부터 다시 조사해야 하는 낭비가 발생한다.
- **즉시 수정 vs 후속 과제**: 요청 범위를 벗어나거나, 안전 불변식을 위반하거나, 프로덕션 동작을 저해하는 결함은 **후속으로 미루지 않고 즉시 수정한다**. 후속 과제로 남기는 것은 (a) 동작은 정상이나 개선 여지가 있는 사항, (b) 범위 밖의 인접 결함, (c) 설계 판단이 필요하여 별도 논의가 필요한 사항에 한한다.
- **없으면 적지 않는다**: 형식을 채우기 위해 빈 항목이나 억지스러운 항목을 만들지 않는다.
- 「작업 종결 규칙」·「문서 갱신 규칙」·「기능 소개 문서 규칙」과 병행한다: 커밋 → 푸시 → 배포 + 낡은 문구 갱신 + 기능 소개 문서 + **후속 과제 기록**까지가 한 사이클의 종결 조건이다.
- **배경(사후 반영)**: 2026-07-26 관리형 스킬 채널(MS) 라이브 롤아웃에서 실제 결함 7건이 발견되어, 프로덕션을 저해하는 3건은 즉시 수정하고 나머지 4건은 후속으로 남겼다. 그러나 해당 4건이 초기에는 QA 증적 내에만 머물러 있어, 소유자가 후속 과제 문서화 여부를 확인하지 않았다면 유실될 위험이 있었다.

## ops 체크아웃 단방향 규칙 (cha 지시, 2026-07-27)

**노드의 배포 체크아웃(`/srv/autophagy-agents`)은 `origin/main`의 단방향 거울이다 — 그 안에서 커밋을 만들지 않는다.**

- 「배포 provenance 규칙」이 **밖으로 나가는** 코드를 통제한다면(=origin/main에 없는 코드는 배포 금지), 이 규칙은 **반대 방향**을 막는다: **배포 체크아웃이 코드의 출처가 되어서는 안 된다.** 그 안에서 허용되는 쓰기는 `git fetch` / `git pull --ff-only`뿐이며, 편집·`git add`·`git commit`·`git stash`는 금지다. 고칠 것이 있으면 개발 체크아웃에서 커밋 → 푸시 → 배포한다.
- **에이전트가 런타임에 스스로 학습한 내용도 예외가 아니다.** 소유자와의 실제 작업에서 나온 진짜 지식이라도 배포 체크아웃에 직접 커밋하면 위험이 이중이다 — (a) git에 없는 코드가 되어 다음 정렬 때 **유실**되고, (b) 그때까지 **모든 세션의 `git pull --ff-only`가 막혀** 배포 파이프라인이 선다. 「추적 config = 불변 시드」와 같은 이유로, 런타임에 생기는 것은 체크아웃 밖(`~/.hermes/…`)에 쓰거나 개발 경로로 되돌려 보낸다.
- **커밋은 반영이 아니다 — 에이전트에게도 똑같이 적용된다.** 스킬 문서를 고쳐 커밋해도 마운트본은 그대로이므로, 배포(소유자 ✅) 전까지 그 변경은 **조용히 무효**다. ff-pull 차단은 시끄럽게 실패하지만 이쪽은 아무 신호도 없어 발견이 늦는다 — 에이전트는 배웠다고 여기지만 실제로는 구버전이 돈다. 판정은 언제나 `readlink /srv/autophagy-skills/live/<skill>`("커밋됨 ≠ 배포됨").
- **규칙은 산문이 아니라 훅으로 강제된다.** `automation/bootstrap-accounts.sh`의 `install_commit_refusal_hook`이 배포 체크아웃의 `.git/hooks/pre-commit`에 **모든 커밋을 무조건 거부**하는 훅을 설치한다(멱등 — `install`이 단일 경로를 교체하므로 재실행해도 훅은 정확히 1개). 스캔하지도 판단하지도 않는다 — 조건부 거부는 "예외를 아는 사람"에게만 통하는데, 사고를 낸 주체는 언제나 예외를 모르는 쪽이었다. 거부 메시지는 개발 체크아웃에서 커밋하는 방법과 비파괴 복구 절차(format-patch→am)를 함께 안내한다. `git fetch`/`git pull --ff-only`는 커밋을 만들지 않으므로 영향이 없다(회귀 고정: `tests/unit/test_deploy_checkout_commit_refusal.py`). 이것은 거부(구조)와 탐지(아래 프로브)의 2중 방어다 — `--no-verify`로 훅을 뚫어도 프로브가 잡는다.
- **탐지는 운에 맡기지 않는다.** `automation/healthcheck.sh`의 `checkout_mirrors_origin` 프로브가 배포 체크아웃이 origin/main과 어기나는지를 확인해 FAIL·수리 티켓을 낸다. **검증은 체크아웃이 로컬인 cron 호스트(`<primary-node>`)에서 로컬로 돌리므로 ssh도 sudo도 쓰지 않는다** — 원건 이전엔 SSH allowlist와 중첩 sudo가 둘 다 rc=126으로 프로브를 죽였다(2026-07-29 실측). 검증은 세 가지다: 추적 파일 미커밋 변경(dirty), HEAD가 로컬 origin/main의 조상이 아님(ahead=로컬 커밋), 그리고 **origin보다 뒤처짐(behind)** — 뒤처짐은 `git ls-remote`(네트워크 읽기지만 로컬 ref를 쓰지 않음)로 진짜 origin과 대조해 잡는다. **뒤처짐의 심각도는 프로덕션이 무엇을 돌리는가로 채점한다**(DG-6 정합): 릴리스가 `origin/main`에 있으면 관측소만 지연된 것이므로 PASS + `MIRROR-BEHIND-WARN`, 릴리스가 낡았으면 `release-stale`로 FAIL, 릴리스가 없으면(롤백 형태) 미러가 곧 프로덕션이므로 FAIL이다 — 단 **아직 수렴 중인 드리프트는 유예한다**(`RELEASE-CONVERGING-WARN`). 그 판단은 우리가 새로 내리지 않고 리컨실러의 상태 파일을 읽어 위임한다 — 그쪽이 이미 `DRIFT_NOTICE_SECONDS`(600초)·`FAILURE_NOTICE_THRESHOLD`(3회)로 "이게 사건인가"를 소유자에게 알릴지 결정하고 있으므로, 같은 질문을 두 곳에서 따로 판정하면 방금 고친 그 어긋남이 릴리스 쪽으로 옮겨갈 뿐이다. 유예는 fail-closed다: 상태 파일이 없거나·깨졌거나·리컨실러가 자기 임계값만큼 침묵하면 그대로 FAIL이다(침묵이 조용함을 사지 못하게 한다 — 2026-08-02 실측) — 조용해지려고 탐지를 내주지 않는다(`release == origin/main`을 보는 전용 프로브가 없어 낡은 릴리스는 오직 여기서만 보인다). dirty/ahead는 모드와 무관하게 FAIL이다. 그래도 **프로브는 `git fetch`를 안 한다** — read-only 불변식은 유지된다(origin 불통은 `BEHIND-UNKNOWN`으로 degrade). `logs/` 같은 미추적 파일은 오탐이 아니다. 티켓은 체크 이름만 실지 않고 비파괴 복구 절차를 함께 실는다(`checkout_mirror_guidance`: ahead=`format-patch`, behind=`land.sh`). 프로브·채점·가이드는 `automation/checkout_mirror_probe.sh`에 분리되어 land.sh와 공유된다 — healthcheck는 **탐지**, land.sh와 리컨실러는 **수렴**이며, land.sh는 그 함수를 노드에 값으로 실어 보낸다(`declare -f`; 노드 사본을 source하면 방금 경고로 낮춘 그 미러의 셸을 ops로 실행하게 된다 — 그래서 실리는 3개 함수는 자기완결적이어야 하고, 채점 헬퍼를 거기서 호출하면 노드에서 "command not found"가 된다). **push+노드 동기화는 `automation/land.sh` 하나로 수행해 수동 2단계의 망각을 없앤다**. 상세: [소개](docs/기능소개/배포-체크아웃-지연-감지.md)·[소개](docs/기능소개/단일-랜딩-명령.md), 증적 `docs/qa/DG-1/summary.txt`.
- **수렴도 사람에게 맡기지 않는다.** 릴리스 수렴은 detached 스냅샷 워크트리에서 이뤄져 미러의 HEAD를 설계상 건들지 않는다. 그래서 예전엔 사람이 `land.sh`를 돌릴 때만 미러가 따라왔는데, **브랜치 작업은 PR 머지로 main에 도달해 `land.sh`를 거치지 않는다** — 즉 아무도 되돌리지 않았고, 헬스체크는 매 5분 같은 실패를 반복했다(실측 2026-07-29~08-03 `mirror-behind` 447회). 이제 2분 리컨실러가 같은 틱에서 미러를 `git pull --ff-only`로 따라오게 한다(`sync_mirror`) — 단, **판정이 정확히 `mirror-behind`일 때**와 **프로덕션이 이미 `origin/main`에 도달한 뒤**에만. dirty/ahead는 절대 건들지 않고(그 작업은 다른 어디에도 없다), 릴리스가 낡은 동안에는 미러의 지연이 그 사실의 유일한 증거이므로 일부러 남긴다. 이것은 이 규칙의 예외가 아니다 — `pull --ff-only`는 위에서 허용한 바로 그 쓰기다. 상세: [소개](docs/기능소개/관측-미러-자동-수렴.md).
- **DG-5 이후 이 체크아웃은 런타임이 아니다 — 관측소다.** cron 워처·마운트 스킬·`peer_attest`·`deploy-skill.sh`는 공용 리졸버로 불변 릴리스 `/srv/autophagy-agent-current`를 먼저 찾는다([소개](docs/기능소개/불변-런타임-릴리스.md)). 그래서 **`land.sh`의 계약은 노드 상태에 따라 갈린다**: 릴리스가 살아 있으면 미러의 dirty/ahead는 `LAND-MIRROR-WARN`으로 낮추고(그 상태가 프로덕션을 좌우하지 않으므로 — 무관한 랜딩을 거부하던 것이 원래 사고였다) 하드 사후조건은 **릴리스가 방금 push한 sha에 도달했는가**로 옚긴다. `current`를 지워 롤백한 상태(폴백 모드)에서는 미러가 다시 프로덕션이므로 **옛 하드 계약이 그대로 되살아난다**. 끊어진 심링크처럼 **깨진 `current`는 부재가 아니라 손상**이라 랜딩이 거부된다 — `[[ -e ]]`가 거짓이라는 이유로 릴리스 노드를 낡은 미러로 조용히 강등하면 안 된다. 이 규칙(커밋 금지·ff-pull만 허용)은 모드와 무관하게 유지된다.
  - **예외 1건(2026-08-10 시점)**: `autophagy-repair-approval-watch.service`는 system 스코프 유닛이라 DG-5의 `--user` 이관 절차가 닿지 않았고, 라이브 설치본이 여전히 미러(`/srv/autophagy-agents`)를 WorkingDirectory·PYTHONPATH·ExecStart로 쓰고 있었다 — 즉 이 한 유닛에 한해서는 미러가 아직 런타임이었다. 2026-08-10의 별도 system-scope 수렴 작업으로 해소되며, 그 전에는 위 문단을 이 유닛에 적용하지 않는다(근거: `.omo/notepads/repair-report-rollout/decisions.md`).
- **권한이 규칙을 이긴다(2026-09-01 실측).** 미러 디렉터리가 `ops:autophagy 2775`(group 쓰기)라 agent 가 파일을 만들고 추적 파일을 덮어쓸 수 있었고, 실제로 2026-08-29 에 첨부 아카이브 구현을 미러 안에 써서 3일간 미러를 동결시켰다(커밋 거부 훅은 커밋만 막는다). 미러 트리는 ops 만 쓰기 가능해야 한다 — 소유자 항목으로 기록(`docs/follow-ups-deferred.md`).
- **복구는 파괴적으로 하지 않는다.** 앞서 있는 커밋은 `git format-patch` → 개발 체크아웃에서 `git am`으로 **작성자·타임스탬프를 보존한 채** main 위에 올리고, blob 바이트 동일성을 확인한 뒤 체크아웃을 정렬한다. 되돌림 대비 ref를 노드에 남긴다(선례 `785eb34`/`67fd9e9`, 유실 0건). `reset --hard`로 먼저 맞추는 것은 학습분을 지우는 행위다.
- **배경(사후 반영)**: 2026-07-27, 노드의 agent 계정이 `skills/mail/SKILL.md`(세미나 출장 신청 안내 표준, v1.5.3→1.5.5)를 배포 체크아웃에 직접 커밋해 prod가 git에 없는 코드를 들고 있었고, ops가 origin보다 2커밋 앞서 모든 ff-pull이 막혔다. 동시에 그 학습분은 배포되지 않아 마운트본은 구버전이었다 — 두 실패가 한 원인에서 나왔다.

## 수리 반영 경로 규칙 (cha 지시, 2026-07-29)

**수리 에이전트는 배포 체크아웃에서 커밋하지 않는다. 전용 작업 클론에서 작업해 누출 게이트(`public_gate`)를 통과한 뒤 공개 `orientpine/cytoplasm` 에 `repair/t_<ticket>` 브랜치로 push하고, 그 브랜치→main PR까지 에이전트가 생성한다. main 머지는 cha가 GitHub에서 한다.**

- **작업 위치**: 수리의 apply·commit·패치문서·회귀시나리오 등록은 전용 작업 클론에서만 수행한다. 배포 체크아웃(`/srv/autophagy-agents`)은 「ops 체크아웃 단방향 규칙」그대로 `git fetch`/`git pull --ff-only`만 허용된다. 샌드박스 단계는 이미 `git clone --shared`로 격리돼 있으므로(`repair_ops_adapters.py`) 그대로 둔다.
- **자격증명**: 저장소 **한정** write deploy key를 사용한다(fine-grained PAT 아님 — 범위가 더 좁기 때문). 배포 체크아웃의 ops 키는 **계속 read-only**로 둠 — 두 키를 섞지 않는다. 키는 `/srv/autophagy-private/repair_push_key`(ops:600)에 둔다 — **홈에 두면 안 된다**: 두 수리 유닛이 `ProtectHome=yes`라 `/home`이 서비스에게 빈 디렉터리로 보이고, 파일이 디스크에 멀쩡히 있는데도 런타임에만 "키 없음"으로 실패한다(회귀 고정: `tests/unit/test_repair_push_key_sandbox.py` — 유닛 파일에서 제약을 역산하므로 `ProtectHome`이 바뀌면 테스트도 따라 바뀐다). 호스트 키도 같은 이유로 `/srv/autophagy-private/repair_known_hosts`에 **고정**한다 — ssh는 `~/.ssh/known_hosts`를 passwd 엔트리로 해석하므로 `ProtectHome`이 가리고, 노드에 `/etc/ssh/ssh_known_hosts`도 ssh_config 전역 설정도 없어 유닛 안에서는 검증할 DB가 아예 없다(실증: 호스트 키 DB를 끊으면 `ls-remote`가 실패). `accept-new`로 우회하지 않는다 — write 자격증명이 오가는 유일한 경로에서 아무 키나 신뢰하게 되기 때문이며, 파일이 없으면 역시 exit 4다. **같은 덫이 2026-08-21에 다시 걸렸다(2026-08-26 수리)**: `approval_reminder_config` 의 폴백이 `~/.hermes/config.yaml` 을 `Path.is_file()` 로 찔렀는데 CPython 이 EACCES 를 삼키지 않아 예외가 났고, 그 raise 가 `except (ImportError, ModuleNotFoundError)` 절 **안에서** 일어나 형제 `except Exception` 이 잡지 못해 그대로 탈출했다 — repair 승인 워처가 5일간 매분(5,329회) 기동 즉시 죽어 ✅ 를 아무도 소비하지 않았다. 교훈: `ProtectHome` 유닛에서 홈을 찌르는 코드는 **답해야지 던지면 안 된다**. 보이지 않는 설정은 없는 설정과 같다(`9e1b7ad0`). 경로는 `REPAIR_PUSH_KEY`로 덮어쓸 수 있고, 키가 없으면 **push를 시도하지 않고 exit 4로 실패**한다 — 조용히 ops read-only 키로 폴백하면 진짜 원인에서 먼 곳에서 실패하기 때문.
- **push 대상은 브랜치뿐**: `repair/t_<ticket>` 패턴으로만 push한다. **`main` 직접 push 금지**, 자동 ff-머지도 금지.
- **push 는 곧 공개다 — 누출 게이트가 먼저 돈다(2026-10-07 공개 우선 전환)**: 작업 클론은 배포 체크아웃의 origin 에서 만들어지고, 전환 뒤 그 origin 은 공개 `orientpine/cytoplasm` 이다. `RepairWorkClone` 은 push 직전 `public_gate range origin/main HEAD` 를 돌려(`_refuse_leaks`) 금지 경로·설치 토폴로지·비공개 금칙어 중 하나라도 걸리면 push하지 않는다. 노드에는 운영 저장소가 없으므로 금칙어 목록 사본을 노드 비공개 루트에 두고 수리 유닛 환경 `PUBLIC_GATE_DENYLIST` 로 가리킨다 — 목록이 설정되지 않은 노드는 경로·토폴로지만 검사하고 `PUBLIC-GATE-DENYLIST-UNSET` 을 남긴다. PR 본문의 공개 적합성 확인란(「개인화 코드 금지 규칙」)은 그대로 쓰되, 이제 기계가 먼저 막는다.
- **PR 생성까지가 에이전트 종착점 (cha 지시, 2026-07-31)**: 브랜치를 push했으면 에이전트가 곧바로 `repair/t_<ticket>`→`main` PR을 `gh pr create --base main --head repair/t_<ticket>`로 생성한다(제목·본문은 커밋 스타일; 본문에 티켓 id·수정 요약·검증 증적·**공개 적합성 항목**(채널 id·과제명·개인 경로 하드코딩 없음 확인 — 「개인화 코드 금지 규칙」), 실수신자/본문 등 민감정보 마스킹). **push만 하고 멈추지 않는다** — cha에게는 GitHub에서 브랜치 diff를 눈으로 확인해 Merge 버튼을 누르는 트리거가 필요하고, PR이 없으면 그 트리거 자체가 없다(2026-07-31 선례: 브랜치만 push되고 PR이 없어 cha가 머지할 방법이 없었다). 이미 열린 PR이 있으면 새로 만들지 않고 그 PR을 재사용한다(push만으로 헤드가 갱신됨). `gh` 미설치·미인증이면 설치·인증 후 진행하고, 그래도 불가하면 PR을 만들 수 없음을 명시해 cha에게 알린다.
- **main 머지 주체 = cha**: cha가 GitHub PR에서 diff를 확인하고 Merge 버튼으로 머지한다. 이것이 자동화의 의도된 종착점이다 — PR까지 자동이면 급한 불은 이미 꺼지고, main 반영만 사람 눈을 거친다. **에이전트는 main에 직접 머지·push하지 않는다.** 머지 후 노드는 평소대로 `pull --ff-only`로 받고, 에이전트는 반영을 확인한 뒤 kanban 티켓 완료 처리까지 이어간다.
- **승인은 패치 내용에 바인딩된다 (완료, 2026-07-29)**: `action_hash`는 `sha256:<canonical(ticket_id, patch_name, 패치 바이트 sha256, 변경파일·라인증감 요약)>`이고, 승인 DM은 변경 파일 목록·라인 증감·`patch_sha256`을 실는다. 패치 본문은 여전히 Discord에 노출하지 않고 `/srv/autophagy-private/`의 경로만 안내한다. 적용 게이트는 적용 직전 디스크의 패치로 해시를 재계산해 소유자가 실제로 승인한 해시와 대조하므로, **승인 뒤 `patch.diff`가 바뀌면 적용되지 않는다**. 불일치는 `False`가 아니라 **예외**로 표면화한다 — `_run`은 `AWAITING_APPROVAL`에도 exit 0을 돌려주고(`repair_ops_cli.py:174`) 워처는 자식이 0이면 레코드를 회수한 뒤 approvals 로그에 `approved`를 남기기 때문에, `False`로 거부하면 아무것도 적용하지 않은 채 감사 기록만 생긴다. 구스키마(이름만 해싱) 레코드는 읽힐 수는 있으나 **인가하지 못한다**; 소유자가 반응하지 않은 것은 새 요청이 교체하고, 이미 누른 것은 파괴하지 않고 24h TTL로 정리된다. 승인 메시지 렌더러 **v1은 동결**이다 — 이미 게시된 메시지는 저장 레코드의 재렌더와 정확히 일치해야 하며, 문구를 바꾸려면 v3을 만든다(2026-07-29 `5ef869d` 선례). 구현 `automation/repair/repair_patch_{diff,binding}.py`·`repair_approval_render.py`, 상세 [소개](docs/기능소개/수리-승인-내용-바인딩.md), 증적 `docs/qa/RTS-4/r2-content-binding.txt`.
- **롤아웃 순서(완료, 2026-07-29)**: ① apply 대상을 작업 클론으로 이관(`08a221d`) → ② systemd `ReadWritePaths`에서 `/srv/autophagy-agents` 제거(`7ea6a8c`) → ③ 브랜치 push 추가(`b86e1df`, 키 경로 수정 `d30b4a1`) → ④ 배포 체크아웃 커밋 거부 훅 롤아웃(노드 설치 완료). 순서를 바꾸면 훅이 수리 apply 경로를 깨뜨리므로, 재구축 시에도 이 순서를 지킨다. ④ 증적: 배포 체크아웃에서 `git commit --allow-empty`가 exit 1로 거부되고 HEAD 불변, `git fetch`·`git pull --ff-only`는 정상 동작.
- **선행 충돌 주의**: `.omo/plans/repair-report-{core,rollout}.md`(타 세션, 미추적 상태)가 `repair_ops_core.py`·`automation/interop/report.py`·**`automation/repair/systemd/*`(예외 없음)**·일부 승인 흐름의 변경을 금지하고 있다. 위 롤아웃 ②는 systemd 유닛을 건드리므로 **해당 계획과 순서를 조율한 뒤** 진행한다. 먼저 손대면 다른 세션의 확정 설계를 덮어쓰게 된다(2026-07-21 선례).
- **배경**: 2026-07-28 드리프트 복구 중, 배포 체크아웃의 미병합 커밋을 생산한 주체가 사람이 아니라 **수리 자동화**임이 드러났다(`repair_ops_cli.py:59` 기본값 `REPAIR_CHECKOUT=/srv/autophagy-agents`, `repair_ops_adapters.py:233-244,272`의 `git commit`/`--amend`/`revert`, 유닛의 `ReadWritePaths`). ops deploy key가 read-only라 그 커밋들은 origin에 도달할 수 없어 — 프로덕션이 git에 없는 코드를 돌고, 다음 정렬 때 유실되며, 그전까지 모든 세션의 ff-pull이 막힌다. 즉 즉시 반영이라는 장점을 유실·파이프라인 정지로 지불하는 구조였다.

## 세션 워크트리 규칙 (cha 지시, 2026-08-03)

**세션마다 워크트리를 하나 만들어 그 안에서 작업한다 — 여러 세션이 한 워킹트리를 공유하지 않는다.**

- **왜**: 워킹트리를 공유하면 같은 파일을 동시에 만진다. 2026-08-03 실측 — `docs/features.md`를 두 세션이 함께 편집해 한쪽이 남의 묶음 헤더를 덮어썼고, 커밋 스테이징에도 남의 미완성 편집(문장이 끊긴 채로)이 섞였다. 「다른 세션 작업 덮어쓰기 방지」·「커밋 전 diff 확인 규칙」이 사람의 주의로 막던 것을 구조로 막는다.
- **비용은 작다**: 추적 파일 14MB, 생성 0.1초(실측). gitignore된 대용량 잔여물(`configs/rag/*/.venv` 5.5GB)은 따라오지 않으므로, **그것이 필요한 작업은 메인 체크아웃에서** 한다.
- **훅·가드는 그대로 작동한다**: 워크트리는 `.git/hooks`를 공유하므로 gitleaks pre-commit이 그대로 돈다(고엔트로피 토큰 실제 차단 확인). provenance 가드와 전체 테스트도 워크트리에서 통과한다(3012 passed). `.git`을 디렉터리로 가정하는 6곳은 전부 노드측 경로(미러·배포 체크아웃·전용 클론)라 개발 워크트리는 도달하지 않는다.

```bash
automation/worktree.sh start <이름> [--paths <손댈 경로>...]   # 세션 시작
automation/worktree.sh finish <이름>                          # 세션 종료
```

**시작 전에 기반이 최신인지 확인한다 (cha 지시)** — 낡음에는 세 종류가 있고 각각 다른 곳에서 잡힌다:

- **① 로컬 `origin/main` ref가 낡음** → `start`가 fetch를 **강제**하고, 실패하면 시작하지 않는다. 캐시된 ref를 조용히 쓰는 것이 정확히 2026-07-21 사고(옛 체크아웃이 최신 결정을 덮어써 배포 404)의 모양이기 때문에 경고가 아니라 거부다. 따라잡은 커밋 목록을 출력해 **무엇을 놓치고 있었는지** 보여준다.
- **② 작업 중 origin/main이 전진** → 시작 시점에는 못 잡는다(2026-08-03 하루에 3회 전진). PR 시점의 mergeable 판정과 `finish`의 fetch가 잡는다.
- **③ 손댈 파일을 다른 세션이 방금 바꿈** → ref가 최신이어도 모른다. `--paths`를 주면 그 경로의 최근 커밋과 **메인 체크아웃의 미커밋 변경**을 함께 보여준다. 「다른 세션 작업 덮어쓰기 방지」가 요구하는 확인을 기억이 아니라 출력으로 만든다.

**종료 시 착지 여부를 확인한다** — `finish`는 미커밋 변경이나 `origin/main`에 착지하지 않은 커밋이 있으면 **제거를 거부**한다. 2026-08-03 실측: 이미 머지된 PR의 브랜치로 증적을 push해 브랜치에는 올라갔으나 착지하지 못했고(push가 성공하므로 아무 신호도 없다), 정리 직전 이 확인이 QA 증적 65줄을 살렸다.

**머지된 브랜치에는 다시 push하지 않는다** — 새 작업은 새 브랜치. 2026-08-03에 두 번 어겼고 한 번은 유실 직전까지 갔다. `start`가 이름·브랜치 중복을 거부하는 것이 이것을 돕는다.

**원격 브랜치는 자동으로 지우지 않는다** — 공유 영향이라 사람이 판단한다(회귀로 고정: `tests/unit/test_session_worktree.py`).

## PR 전 검증 규칙 (cha 지시, 2026-08-25)

**브랜치 push 는 그 트리의 로컬 CI 영수증이 있어야 나간다 — 「PR 전에 CI 를 돌려라」는 산문이 아니라 `automation/hooks/pre-push` 가 강제한다.**

- **왜 훅인가**: 이 저장소는 브랜치 보호를 쓸 수 없다 — private + Free 조합이라 `gh api repos/.../branches/main/protection` 이 **403** 이다. 즉 GitHub CI 는 머지를 막을 권한이 없는 **권고**였고, 2026-08-25 에 실제로 빨간 CI(결제 실패로 잡이 시작조차 못 한 것) 위에서 머지가 이뤄졌다. 지침으로 적으면 그 문장을 읽는 주체가 지키는 만큼만 작동한다 — 「배포 provenance 규칙」의 가드와 「ops 체크아웃 단방향 규칙」의 커밋 거부 훅이 산문에서 코드로 옮겨간 것과 같은 이유다.
- **순서는 커밋 → `automation/local_ci.sh run` → push** 다. `run` 은 워크플로와 같은 세트(lint · `pytest tests/unit` · 빈 `python:3.12-slim` 컨테이너의 설치기 dry-run + 서드파티 경계 테스트)를 돌리고 **전 단계 통과 시에만** 영수증을 `~/.hermes/local-ci/<tree>.json`(0700/0600, 체크아웃 밖)에 쓴다. 어느 단계든 실패하면 영수증은 만들어지지 않는다.
- **영수증은 commit 이 아니라 tree 에 묶인다.** 문구만 고쳐 amend·rebase 하면 그대로 유효하고, 내용이 한 글자라도 바뀌면 무효다. 워크플로 자신의 sha256 도 담기므로 `.github/workflows/ci.yml` 이 바뀌면 그 전 영수증은 무효가 된다. 더러운 트리에서는 발급하지 않는다 — 검사한 것과 push 하는 것이 달라지면 영수증이 거짓말이 되기 때문이며, 유일한 예외는 `.omo/senpi-task/`(하네스가 매 턴 다시 쓰는 세션 장부, 어떤 검사도 읽지 않는다)다.
- **범위는 `refs/heads/*` 중 main 이 아닌 것**이다 — **태그 push 는 대상이 아니다**. 릴리스 태그는 이미 착지한 커밋을 가리키므로 거기서 막으면 보호되는 것 없이 리컨실러만 선다(「릴리스 태그 규칙」이 경고한 132회 연속 실패가 정확히 그 모양이다). 2026-08-25 실측: 범위를 `main 이 아닌 ref`로 쓴 첫 판본은 실제로 태그 push 를 거부했다. main 착지는 `automation/land.sh` 가 자기 가드로 소유하고, 세션 워크트리의 main 직접 push 거부는 그대로다. 게이트가 생기기 전에 딴 브랜치는 리포 안에 구제 수단이 없으므로 **경고 후 통과**시킨다 — 판정 불가는 위반이 아니다(`runtime_package_probe` 의 UNKNOWN 과 같은 취급). 그 브랜치도 main 에 rebase 하는 순간 게이트에 든다.
- **로컬 세트가 CI 에서 갈라지지 않는다**: `tests/unit/test_local_ci_push_gate.py` 가 `.github/workflows/ci.yml` 의 `run:` 명령을 뽑아 `local_ci.sh` 와 **기계 대조**한다. 로컬에서 실행하면 안 되는 명령(개발자 환경을 변형시키는 `pip install -r requirements-dev.txt`)은 스크립트 주석이 아니라 그 파일의 `_LOCAL_ONLY` 에 사유와 함께 등록한다 — 주석에 적으면 grep 은 통과하고 동작은 없는 상태가 조용히 만들어진다. 예외가 낡는 것도 테스트가 잡는다.
- **탈출구는 `LOCAL_CI_ALLOW_UNVERIFIED=1` 하나뿐이고 샌드박스/실험 전용이다.** 통과시키려고 상습적으로 쓰면 가드가 무의미해진다(`DEPLOY_ALLOW_UNPUSHED` 와 같은 성격). 쓰면 매 push 마다 stderr 에 남는다.
- **비용 결정(옵션 A)**: `.github/workflows/ci.yml` 에서 **main push 트리거를 제거**했다. PR 에서 이미 통과시킨 트리를 머지 직후 다시 돌리는 중복이었고 최근 100 회 실행 중 **48 회**가 그것이었다(2026-08-25 실측). `pull_request` 실행은 남긴다 — 깨끗한 러너에서의 독립 검증은 로컬 영수증이 대신하지 못한다. 월 3,890~4,862 분 → 약 2,000 분(Free 포함분 이내).
- **한계**: 영수증은 "이 트리가 **이 기계에서** 통과했다"를 증명하지 "깨끗한 호스트에서 통과한다"를 증명하지 않는다. 그 간극은 clean-host 컨테이너 단계가 메우고 완전히는 못 메운다. 훅은 `automation/worktree.sh start` 가 설치하므로 훅을 설치한 적 없는 clone 에는 게이트가 없다 — gitleaks pre-commit 과 같은 성질이다. 상세: [소개](docs/기능소개/push-게이트-로컬-CI-영수증.md)

## 머지 규칙 (cha 지시, 2026-08-26)

**PR 머지는 `automation/merge-pr.sh <pr>` 로만 한다 — `gh pr merge` 를 직접 부르지 않는다.**

- **왜 명령에 두나**: 브랜치 보호를 쓸 수 없다(private + Free = 403). 켤 수 있어도 에이전트 토큰이 `admin` 이라 기본 설정은 그를 통과시키고, 관리자까지 묶으면 `automation/land.sh:189` 의 main 직접 착지가 서버에서 거부된다. 그래서 판정을 서버가 아니라 머지 명령 자체에 둔다. 2026-08-25 에 두 번 뚫렸다 — #267 은 빨간 CI 위에서, #269 는 체크가 큐잉되기도 전에 머지됐다(둘 다 에이전트).
- **무엇을 판정하나**: PR 이 OPEN 이고 base 가 main 이며, `.github/workflows/ci.yml` 이 선언한 잡이 **전부 보고**했고 그 전부가 완료·성공일 때만 머지한다. 진행 중이면 기다리고(기본 900초), 마감을 넘기면 거부한다. **체크 0건도, 일부만 올라온 상태도 통과가 아니라 PENDING** 이다 — 2026-08-26 첫 실사용에서 그 순간 올라와 있던 체크 1건(GitGuardian)만 보고 통과시킨 적이 있다. 기다릴 잡 이름은 워크플로에서 **파생**하며 하드코딩하지 않는다. 머지 불가(`CONFLICTING`)는 `gh` 가 실패한 뒤가 아니라 판정 단계에서 거부하고 해소 절차를 안내한다.
- **외부 앱 체크가 멈추면 보상 스캔으로 끝낸다(2026-10-01)**: 워크플로 잡이 아닌 체크(예: GitGuardian)도 실패하면 언제나 거부한다. 그러나 워크플로 잡이 전부 green 인데 외부 체크만 `MERGE_PR_EXTERNAL_GRACE_SECONDS`(기본 180초) 뒤에도 끝나지 않으면 `gh pr diff` 를 `gitleaks stdin`(저장소 `.gitleaks.toml`, `--redact`)으로 스캔해 깨끗할 때만 머지하고 `EXTERNAL-UNSETTLED` 를 stderr 에 남긴다. gitleaks 가 없거나 diff 를 읽지 못하거나 무엇이든 찾으면 거부한다. 2026-09-30 PR #557 에서 GitGuardian 이 평소 1초인 판정을 20분 넘게 `in_progress` 로 붙잡았고(재실행 API 는 404) 그때는 모든 미정 체크를 덮는 탈출구 말고 길이 없었다.
- **태그는 더 이상 여기서 자르지 않는다(VA-3, 2026-08-30)**: 머지는 축적이고, 서명 태그는 릴리스 승인(`automation/release.sh`, VA-1)이 소유자 ✅ 1회를 받아 자른다. 머지마다 태그를 자르면 그 릴리스 승인이 있으나 마나가 된다. 태그 없는 창의 리컨실러 통지는 sha별 사고가 아니라 3일 임계의 릴리스 백로그 다이제스트다(「릴리스 태그 규칙」 참조).
- **PR 체크는 로컬 영수증과 다른 것을 덮는다**: GitHub `pull_request` 실행은 **머지 결과 트리**를, `automation/local_ci.sh` 의 영수증은 **브랜치 트리**를 검사한다. 2026-08-25 에 실제로 갈렸다(머지 결과 `43c50a44` vs 영수증 `57081383`). 둘 다 필요하다.
- **탈출구는 `MERGE_PR_ALLOW_UNCHECKED=1` 하나**이고 **체크가 아예 나오지 않을 때만**을 위한 것이다(예: Actions 결제 중단). **실패한 체크는 이것으로도 통과하지 못한다.** 쓰면 매번 stderr 에 남는다.
- **강제**: `tests/unit/test_merge_pr_gate.py` 가 `automation/`·`skills/`·`tests/` 의 `*.sh`·`*.py` 에서 래퍼 밖 `gh pr merge` 호출을 금지한다 — 주석에 적으면 grep 은 통과하고 동작은 없다.
- **한계(알고 쓸 것)**: 머지는 GitHub 서버에서 일어나므로 push 훅 같은 **구조적** 강제가 원리적으로 불가능하다. 이 게이트는 명령을 쓰는 주체에게만 구속력을 갖는다. 서버측 강제를 원하면 GitHub Pro 의 브랜치 보호가 유일한 길이고, 그때는 `land.sh` 를 PR 경유로 바꾸는 것이 선행 조건이다. 상세: [소개](docs/기능소개/머지-체크-게이트.md)

## 커밋 전 diff 확인 규칙 (cha 지시, 2026-07-29)

**`git add` 전에 반드시 `git diff --stat`(필요시 `git diff`)로 의도한 파일만, 의도한 방향으로 바뀌었는지 확인한다. 설명되지 않는 삭제나 내가 만들지 않은 변경이 섞여 있으면 그대로 커밋하지 않는다.**

- **왜 별도 규칙인가**: 「다른 세션 작업 덮어쓰기 방지」는 *내가 남의 것을 되돌리는* 방향을 막는다. 이 규칙은 **반대 방향**을 막는다 — *남이 되돌린 워킹트리를 내가 모른 채 커밋해 그 삭제를 굳혀버리는* 경우다. 병렬 세션·노드 에이전트가 같은 체크아웃을 공유하므로, 내가 고친 파일만 `git add` 해도 **그 파일 안에 남의 되돌림이 이미 들어 있을 수 있다**.
- **무엇을 보나**: ① 변경 파일 목록이 내가 손대길 의도한 집합과 일치하는가. ② **삽입/삭제 줄 수의 방향**이 맞는가 — 문서를 추가했는데 순삭제(예: `+9/-20`)면 즉시 멈춘다. ③ 문서·규칙 파일이면 삭제된 줄을 직접 읽는다. ④ `version:` 문자열이 **낮아지는** 변경은 거의 항상 되돌림이다(배포본과 대조할 것).
- **발견 시 조치**: 되돌림으로 판정되면 커밋하지 말고 `git show origin/main:<path>`로 정본을 대조해 복원한다. **이미 커밋된 정본을 되찾는 복원**(`git checkout -- <path>`)은 「미커밋 작업 보존」 원칙과 충돌하지 않는다 — 반대로 남의 **생산물**을 지우는 방향이면 지우지 말고 소유자에게 묻는다.
- **push 전에 한 번 더**: `git diff --stat <직전 정상 커밋>..HEAD`로 삭제가 0이거나 의도한 것뿐인지 확인한다. push 전에 잡으면 복구가 쉽고, 놓치면 다른 세션이 그 상태를 기준으로 작업해 유실이 번진다.
- **배경(사후 반영)**: 2026-07-29 한 세션에서 두 번 발생했다. (1) `docs/features.md` 편집 시 워킹트리에 이미 되돌림이 섞여 있었고 확인 없이 커밋해 **후속 과제 4건이 유실**됐다(`373a0fa` — 문서를 더하는 커밋이 `+9/-20`이었는데 그 신호를 놓쳤다). `7ea6a8c` 정본 기준 재구성으로 복구했다(`46c82de`, 삭제 0 확인). (2) 직후 검증에서 `AGENTS.md`의 「수리 반영 경로 규칙」 18줄·CODE MAP 5행 삭제, 기능소개 문서 삭제, `doctype/SKILL.md`의 **v1.3.1→v1.2.2 후퇴**(배포본과 불일치), 코드 8건 후퇴로 테스트 10건 실패가 관측됐다 — 모두 미커밋 상태여서 origin 정본으로 복원했다.

## 공개 릴리스 규칙 (cha 지시, 2026-08-15)

**2026-10-07부터 개발 정본은 공개 `orientpine/cytoplasm` 의 main 이다(공개 우선 전환). 변경은 cytoplasm 브랜치 → PR → 필수 체크(verify·clean-host-install·leak-guard) green → `automation/merge-pr.sh` 머지로만 main 에 들어가고, 릴리스는 `automation/release.sh` 가 소유자 ✅ 뒤 그 main 커밋에 서명 태그를 다는 것이다. 운영 자료(계획·QA 증적·누출 금칙어)는 비공개 운영 저장소 `orientpine/autophagy-ops` 에 두고 공개 저장소에 추적하지 않는다.**

- **무엇이 뒤집혔나.** 옛 흐름(2026-08-15~10-06)에서는 개발이 private 저장소 `orientpine/autophagy-agents` 에서만 이뤄졌고, cytoplasm 은 `public_export.sh` 가 fresh-history 스냅샷으로만 갱신하는 일방향 파생본이었다. 이제 사람·에이전트·수리 자동화가 모두 cytoplasm 에 브랜치를 push하고 PR 을 연다. private 저장소는 롤백 기간(공개 우선 릴리스 3회 무사고) 동안 되돌리기 경로로 남았다가 2026-10-10 archive 됐다(읽기 전용, 이력 보관). 그 날짜 이전의 문서·QA 증적이 "private 에서 개발한다" 고 말하면 옛 흐름의 기록이다.
- **main 에 손으로 push하지 않는다 — 서버가 막는다.** 브랜치 보호(PR 필수 · 필수 체크 셋 · 관리자 포함)가 직접 push 를 거부하므로 main 의 모든 커밋은 PR 과 필수 체크를 지난 것이다. 머지는 「머지 규칙」대로 `automation/merge-pr.sh` 로만 한다.
- **커밋이 곧 공개다 — 누출 게이트가 먼저 돈다.** `automation/public_gate.py` 가 금지 경로(`.omo`·`docs/qa`)·설치 토폴로지·비공개 금칙어를 검사하고, 같은 검사가 pre-commit·commit-msg·pre-push 훅(`automation/ops_link.sh` 가 설치)·`automation/local_ci.sh` 의 public-gate 단계·CI `leak-guard`·수리 push 직전에서 돈다. 실명·과제명·내부 호스트·Discord id·비공개 경로 세부는 코드·문서·커밋 메시지·PR 본문 어디에도 쓰지 않는다(「개인화 코드 금지 규칙」).
- **새 릴리스 = `release.sh` → 서명 태그 → 릴리스 노트.** `automation/local_ci.sh run` → `automation/release.sh`(✅ 카드) → ✅ 뒤 cytoplasm main 커밋에 update-trust 서명 태그가 잘리고 노드가 그 태그로 수렴한다(「릴리스 태그 규칙」). 반출 스냅샷도 fresh history 도 없다 — 태그가 붙는 커밋이 곧 PR 로 들어온 그 커밋이다.
- **릴리스는 태그 push로 끝나지 않는다 — 릴리스 노트 게시까지가 한 사이클이다.** 종결 조건은 셋이고 하나라도 빠지면 그 릴리스는 끝나지 않았다: **서명 태그 · 그 버전의 GitHub Release 객체 · 거기 붙은 `update-trust.pub` 자산과 지문 공지**. 뒤의 둘이 없으면 신규 설치가 `--update-trust-key`(필수 인자)에 넣을 자산도, 번들 키를 **설치기가 아닌 경로로** 대조할 지문도 없다 — 후자는 P0-6 부트스트랩의 대역외 경로 그 자체다. 노드의 자동 수렴은 태그만 보므로 이 결손은 **조용하다**: 기존 노드는 멀쩡히 업데이트되고 새 설치자만 막힌다(2026-09-09 v1.6.7에서 태그만 올라가 목록의 Latest가 v1.6.1에 멈춰 있었고, 2026-09-07 제3자 설치 실측으로야 드러났다). 절차는 manual-maintainer §1.7이 단독으로 소유한다.
- **릴리스 = 태그 + Release 노트 — 노트도 `release.sh` 가 같은 단계에서 게시한다(cha 지시, 2026-10-09).** `automation/release.sh --notes-file <노트>` 는 승인 요청 **전에** 노트 초안(첫 줄 `# <한 줄 요약>` = Release 제목, 그 아래 한국어 본문: 바뀐 것 · 사용자에게 보이는 변화 · 깨지는 변경/운영자 할 일)을 받아 체크아웃 밖 `~/.hermes/release-notes/<sha>.md` 에 두고, 초안이 없거나 비면 승인을 쓰지 않고 exit 4 로 멈춘다. ✅ 뒤 태그를 자른 바로 그 단계에서 `release_tag_lib.sh` 의 `ensure_release_note` 가 초안 + 머지된 PR 목록 + 업데이트 신뢰키 지문으로 GitHub Release 를 만들고 `update-trust.pub`(개인키 경로면 `ssh-keygen -y` 로 유도한 공개키)를 붙인 뒤 다시 읽어 본문·자산을 확인한다. 게시 실패는 `RELEASE-NOTE-FAIL` + exit 11 이고(태그는 남는다 — 재실행이 노트를 이어서 게시한다), 직전 릴리스 태그에 Release 가 없으면 새 릴리스를 열지 않는다(`RELEASE-NOTE-MISSING`, 보충은 `automation/release-note.sh <tag> --notes-file <노트>`). 완결 타이머·`release-tag.sh` 도 태그 뒤 같은 함수를 부른다. 태그를 자르는 모든 호출부가 노트 단계를 갖는지는 `tests/unit/test_release_note_gate.py` 가, 동작은 `tests/unit/test_release_sh.py` 가 강제한다 — 2026-10-08 v1.16.0·v1.17.0 이 태그만 남아 Latest 가 v1.15.1 에 멈췄고 이미 배포된 변경이 어떤 노트에도 실리지 않았다.
- **반출 경로는 없다(2026-10-10 롤백 기간 종료).** 공개 우선 릴리스 8회(v1.15.1~v1.20.1) 뒤 `public_export.sh`·반출 원장 `configs/public-export-manifest.txt`·전용 시험을 지웠다. 공개 우선 흐름을 되돌리는 길은 더 이상 없고, 나쁜 릴리스는 앞으로 나가서 되돌린다(manual-maintainer §5).
- **서명키가 둘이다 — 혼동 금지**(D8). 업데이트 신뢰키(`update-trust@autophagy` / `/etc/autophagy/update-allowed-signers` / `update_trust.py`)는 업스트림 유지보수자가, 그룹 스킬 서명키(`publisher-<slug>@autophagy` / `/etc/autophagy/managed-skills-allowed-signers` / `managed_sync/verify.py`)는 그룹 관리자가 소유한다. **두 개인키 모두 어떤 git 저장소에도 커밋하지 않고 노드에 배포하지 않는다** — 노드에 놓이는 것은 공개키 한 줄뿐이다.
- **나쁜 릴리스는 옆으로도 뒤로도 갈 수 없고 앞으로만 되돌린다.** `update_trust_state.py`의 롤백 방지 floor가 옛 태그 재-push를 `RELEASE-ROLLBACK`으로 거부한다(공격자 강제 다운그레이드 방어 — 비대칭은 의도된 것이다). 취소는 문제를 revert해 cytoplasm 에 PR 로 랜딩하고 **더 높은 버전**을 컷하는 것이다.
- 전체 절차(공개 우선 릴리스·릴리스 노트·신뢰키 회전·나쁜 릴리스 대응)는 [docs/guide/manual-maintainer.md](docs/guide/manual-maintainer.md)가 단독으로 소유한다. 이 절은 불변식만 들고 있고 절차를 복사하지 않는다 — 같은 절차를 두 문서가 설명하면 반드시 한쪽이 낡는다.
- **배경(사후 반영)**: 2026-08-15 W-F5-A로 실제 공개 repo `orientpine/cytoplasm`이 생기고 `v1.0.0`이 서명·push되자마자 소유자가 "이제 공개 repo에서 작업하는가"를 물었다. 이 저장소는 2026-07-21에 이미 같은 계열의 사고를 겪었다 — 확정된 설계에 대한 오해가 세션을 넘어 전달돼 배포가 404로 실패했다. 답을 산문으로만 두면 같은 일이 반복되므로 불변식을 여기에 박아 둔다. 그때의 답("아니오, 개발은 private 에서 계속한다")은 2026-10-07 공개 우선 전환으로 뒤집혔다 — 같은 혼동이 반대 방향으로 생기지 않도록 이 절의 첫 문장이 지금의 흐름을 말한다.
- 저장소들(개발 정본 `cytoplasm`·그룹 스킬 채널 `ribosome`·비공개 운영 저장소·archive 된 옛 개발 저장소)의 구분은 아래 「세 저장소 구분 규칙」이 소유한다 — 이 절은 개발·릴리스 흐름만 다룬다.

## 개인화 코드 금지 규칙 (SC-2, 2026-08-30)

**개인 맞춤값(채널·메시지 id 상수, 과제명·기관명, 개인 경로·이름)은 코드에 하드코딩하지 않는다 — 자리는 `configs/*.example` 시드와 `~/.hermes/…`·`/srv/autophagy-private/…` 런타임 설정뿐이다.**

- **왜**: 이 저장소가 곧 공개 저장소다 — 2026-10-07 공개 우선 전환부터 커밋·PR 이 그대로 공개된다(전환 전에는 다음 릴리스의 반출 스크립트가 내보냈다). 시크릿은 gitleaks 가, 알려진 실명·과제명은 `public_gate` 금칙어가 잡지만 **목록에 없는 개인 맞춤 로직은 기계가 못 잡는다** — snowflake 상수 하나, 과제명 분기 하나가 그대로 공개된다. 특히 **수리(repair) 패치**는 프로덕션 관찰에서 태어나므로 개인화가 스며들기 가장 쉬운 경로다.
- **어떻게**: 설치별 값은 `configs/node.example.toml`·`configs/*.example`(파서 유효한 자리표시자) + 런타임 override(`~/.hermes/node.toml`, `/etc/autophagy/…`)의 기존 패턴을 따른다. 채널은 `approval_surface`/`approval_directory`·config 키로만 해석한다(기존 불변식 그대로). 테스트의 실측 예시 값(id·이름)은 허용된다 — 배포되는 코드가 아니다.
- **수리 PR 은 본문에 공개 적합성 항목을 싣는다**: "이 패치에 채널 id·과제명·개인 경로 하드코딩이 없다"를 확인란으로 명시한다(「수리 반영 경로 규칙」의 PR 본문 요구에 포함). PR 을 사람이 만들든 자동화(`docs/guide/수리-PR-자동생성-조율안.md`)가 만들든 같다.
- **repair 유래 파일이 `configs/`·`docs/` 에 새로 생기면** `configs/public-export-review.txt` 의 검토 절차(파일 헤더)를 따른다 — 공개/제외를 결정하기 전에 개인화 여부를 먼저 본다.
- **배경**: `.omo/plans/release-convergence-and-versioned-approval.md` §5.2 SC-2. 지금은 추적된 코드가 곧 공개 트리이므로(전환 전 반출 스크립트도 `git archive <commit>` 으로 추적된 코드만 내보냈다 — 2026-08-30 실측) — 그래서 막을 곳은 정확히 "추적되는 코드 안의 개인화"다.

## 세 저장소 구분 규칙 (cha 지시, 2026-08-16)

**코드는 공개 `orientpine/cytoplasm` 하나에서 개발되고 배포된다(2026-10-07 공개 우선 전환 — 개발 정본이자 배포본). 그룹 관리형 스킬 채널 `orientpine/ribosome`(private)과는 합쳐지지 않고, 운영 자료는 비공개 운영 저장소 `orientpine/autophagy-ops` 에 따로 있다. 옛 개발 저장소 `orientpine/autophagy-agents`(private)는 2026-10-10 archive 됐다(읽기 전용). 어느 것을 말하는지 먼저 확정하고 답한다.**

| | `cytoplasm` | `ribosome` | `autophagy-ops` | `autophagy-agents` |
|---|---|---|---|---|
| 가시성 | **public** | private | private | private |
| 역할 | **개발 정본 + 코드 배포본**(2026-10-07~) | 그룹 관리형 스킬 채널 | 비공개 운영 자료 | 옛 개발 저장소 — 2026-10-10 archive(읽기 전용) |
| 담기는 것 | 소스 전체(공개 가능한 것만) | 발행 스킬 릴리스 + 매니페스트 + `refs/heads/roster` | 계획(`omo/`)·QA 증적(`qa/`)·누출 금칙어(`leak/`) | 2026-10-06까지의 개발 이력 |
| 갱신 경로 | 브랜치 → PR → 필수 체크 → `automation/merge-pr.sh`(브랜치 보호) | `automation/managed_skills/publish_cli.py` 3단계 게이트 | 운영자의 직접 커밋 | 쓰지 않는다(롤백을 결정했을 때만) |
| 서명키 | `update-trust@autophagy` | `publisher-<slug>@autophagy` | — | — |
| 신뢰 파일 | `/etc/autophagy/update-allowed-signers` | `/etc/autophagy/managed-skills-allowed-signers` | — | — |
| 검증 코드 | `automation/update_trust.py` | `automation/managed_sync/verify.py` | — | — |
| 팀원 접근 | 공개 clone | read-only deploy key(= 제거 메커니즘) | 없음 | 없음 |
| 받는 쪽 동작 | 2분 리컨실러 자동 수렴 | fetch→검증→격리, **마운트는 본인 ✅**(D3) | 개발 체크아웃이 `.omo`·`docs/qa` 로 링크 | — |
| 소유 매뉴얼 | [manual-maintainer.md](docs/guide/manual-maintainer.md) | [manual-group-admin.md](docs/guide/manual-group-admin.md) | — | — |

- **혼동의 진짜 원인: 설계상 소프트웨어 유지보수자와 그룹 관리자는 다른 사람이다**(D2, D1.1). 팀원의 소프트웨어 업데이트는 그룹 관리자를 거치지 않고 업스트림에서 직접 온다. cha가 지금 두 역할을 겸하고 있어 두 채널이 중복처럼 보일 뿐이며, 제3자가 자기 그룹을 열면 `ribosome`에 해당하는 자기 repo만 갖고 소프트웨어는 `cytoplasm`에서 받는다.
- **합칠 수 없는 이유 ①: 접근 통제가 정반대다.** `cytoplasm`은 공개라 누구나 clone한다. 그룹 스킬 repo는 팀원마다 read-only deploy key를 발급하고 **그 키 폐기가 곧 멤버 제거**다(D1.3) — 공개 repo에서는 제거라는 개념 자체가 성립하지 않는다.
- **합칠 수 없는 이유 ②: 검증 경로가 분리되어 있다.** 위 표의 서명키·신뢰파일·검증코드가 두 열 모두 다르고, 하나가 다른 하나를 대신하지 못한다. 산문이 아니라 코드가 진실이다 — `update_trust.py`(`UPDATE_ALLOWED_SIGNERS_PATH`)와 `managed_sync/verify.py`는 서로를 호출하지 않는 별개 경로다. D8의 「서명키가 둘이다 — 혼동 금지」가 가리키는 지점이 바로 여기다.
- **합칠 수 없는 이유 ③: 업데이트 성격이 다르다.** 코드는 root 권한으로 자동 수렴하고, 스킬은 배달만 자동이고 live 마운트는 소유자 승인이다(D3, 영구 비목표).
- **운영 저장소는 코드가 아니다.** `autophagy-ops` 는 공개할 수 없는 계획·QA 증적·누출 금칙어를 담고, 개발 체크아웃에서 `.omo`·`docs/qa` 로 링크될 뿐 cytoplasm 에 추적되지 않는다(`automation/ops_link.sh`). 그래서 공개 문서는 그 안의 내용을 옮겨 적지 않고, 코드는 그 저장소 없이도 동작해야 한다(금칙어 목록이 설정되지 않으면 `public_gate` 는 경로·토폴로지만 검사한다).
- **그룹이 언제 필요한가**(소유자가 실제로 물은 판단 기준): 팀원이 각자 자기 에이전트만 쓴다 → 그룹 **불필요**, 각자 `cytoplasm`에서 설치하면 끝. 에이전트끼리 Discord로 보고·조율한다 → **roster는 필요**(발신자 신원 대조, W-F2.5-B)하지만 repo는 선택이며 `~/.hermes/roster.yaml`을 손으로 배치해도 동작한다. 팀 공통 스킬을 배포한다 → 그룹 스킬 repo **필요**.
- 절차는 여기에 복사하지 않는다 — 릴리스 컷은 [manual-maintainer.md](docs/guide/manual-maintainer.md), 발행·roster는 [manual-group-admin.md](docs/guide/manual-group-admin.md)·managed-skill-channel.md, 설치는 [install.md](docs/guide/install.md)가 단독으로 소유한다.
- **배경(사후 반영)**: 2026-08-16 그룹 스킬 채널 repo `orientpine/ribosome`이 생기자마자 소유자가 세 저장소의 관계를 이해하지 못해 "내가 이해할 수 있게 도와줘"라고 물었다. 전날에도 같은 계열의 질문(공개 repo가 private를 대체하는가)이 나왔고, 2026-07-21에는 확정 설계에 대한 오해가 세션을 넘어 전달돼 배포가 404로 실패한 전례가 있다. 한 번 나온 혼동은 다음 사람도 겪으므로 채팅으로 한 번 답하지 않고 불변식으로 박아 둔다.

## 용어 교정 위치 규칙 (cha 지시, 2026-09-05)

**용어 교정은 음성→전사본 단계가 아니라 전사본→산출 문서(회의록·lifelog 노트·보고서 …) 단계에서 한다 — 문서 종류마다 중첩된 교정 참고 문서를 쓰고, 문서에 실린 원문 인용 구역은 고치지 않는다.**

- **왜 전사본에서 고치지 않는가**: 전사본은 증거다. 치환을 거기에 새기면 원래 낱말이 어디에도 남지 않는다 — 근접 교정은 실제로 틀린 적이 있다(소유자 용어집의 「성금=선금」이 이 과제의 핵심어 「기성금」을 「기선금」으로 깨뜨렸다). 문서 단계에서 고치면 잘못된 교정은 참고 문서를 고쳐 그 문서를 다시 만들면 회복된다. 그리고 문서마다 어휘가 다르다 — 회의록은 기관명, 라이프로그는 사람·장소, 제안서는 과제 용어다. 하나의 평면 용어집을 전사본에 바르면 그 차이를 표현할 자리가 없다.
- **층은 문서 종류를 따라 중첩한다**: `autophagy/용어집.csv`(모든 산출물) → `autophagy/<문서 종류>/용어집.csv` → `autophagy/<문서 종류>/<과제>/용어집.csv`. 깊은 층이 같은 낱말을 덮어쓰고 나머지는 바깥 층이 그대로 준다. 폴더 이름의 정본은 `automation/drive_taxonomy.CATEGORIES` 이고, Drive 산출물이 아닌 문서 종류만 `term_glossary.EXTRA_FOLDERS` 가 채운다.
- **단일 정의**: 엔진 `automation/term_correction.py`(파싱·지목 치환·자모 근접 교정) · 층 해석 `automation/term_glossary.py` · 감사 로그 `automation/term_correction_log.py` 세 개뿐이다. 스킬은 위임만 하고 사본을 만들지 않는다 — 갈라지면 같은 낱말이 문서마다 달라진다.
- **새 스킬이 지켜야 할 것**: ① 렌더 **전** 해석 결과(요약·결정·액션·사람·장소)에 교정을 걸고, ② 문서에 실리는 원문 인용 구역(회의록 부록 `### C. 원문 전사본`, lifelog `## 전문`, 메일 회신의 `-----원본 메시지-----` 아래)은 바이트 그대로 둔다. ③ 교정 내역은 `term_correction_log.record(document=…, stage=…)` 로 **어절만** 남긴다(문맥을 남기면 본문이 로그로 샌다). ④ Drive 조회는 `DRIVE_PUBLISH_ENABLED` 옵트인이고 실패는 노드 캐시로 내려가며, 교정 실패는 문서 생성을 절대 막지 않는다(fail-soft). 교정은 로컬 계산이라 모델을 부르지 않는다.
- **전사 프롬프트 힌트는 교정이 아니다**: 바른 용어를 인식 **전에** 조건으로 주는 것은 남는다(전사본을 더 옳게 만든다). 이 규칙이 금지하는 것은 **인식된 전사본 본문을 사후에 치환**하는 일이다.
- **강제**: `tests/unit/test_term_correction_conformance.py` 가 산출 문서 생성 경로 인벤토리를 들고 (a) 교정 엔진의 사본이 `automation/` 밖에 있으면 RED, (b) 전사본 생성 경로가 교정을 부르면 RED, (c) 채택하지 않은 문서 종류는 `_EXEMPT` 에 사유와 함께 등록되어야 한다. 산문이 아니라 코드가 진실이다.
- 절차·형식·환경변수·실패 마커의 단독 정본은 [docs/guide/용어-교정-규약.md](docs/guide/용어-교정-규약.md) 다. 이 절은 불변식만 들고 있고 절차를 복사하지 않는다.
- **배경**: 2026-09-05 소유자 판단 — "① 음성→전사본에서 교정할지 ② 전사본→회의록·lifelog 에서 교정할지 고민했는데, ②에서 문서별 중첩 교정 참고 문서를 쓰는 게 좋겠다". 직전 사이클(PR #390/#391)이 ①로 구현돼 있었으므로 그 위치를 옮긴다.

## 산출물 출처 규칙 (cha 지시, 2026-08-26)

**공정표·계획서·보고서 등 회의에서 파생되는 산출물은 완성 회의록에서만 작성한다 — 음성 전사본은 출처가 아니다.**

- **완성 회의록이 있는 곳**: Drive `autophagy/회의록/<과제명>/<연도>/`. `meeting_cli ingest --project <과제명>` 이 그 자리에 발행하고, 경로 규칙은 `automation/drive_taxonomy.py` 의 `meeting` 카테고리가 소유한다. 산출물을 만들기 전에 **거기부터 읽는다**. 읽기는 승인 게이트 대상이 아니다(denylist 는 `gws drive files create|+upload` 만 건다). `.md` 는 Google Docs 가 아니므로 `files export` 가 아니라 `files get --params {fileId, alt:media}` 이며, `gws` 는 cwd 밖 `--output` 을 거부하므로 목적지에서 실행한다.
- **로컬 노트(`~/notes/meetings/*.md`)는 정본이 아니다.** 그 파일은 `meeting_minutes.APPENDIX_HEADING`(`## 부록 · 근거와 원문`) 아래에 **원문 전사본을 통째로 안고 있다**. 부록 위만 회의가 결론 낸 것이고, 아래는 재료다. 파일을 통째로 읽으면 음성 인식 결과를 사실로 읽게 된다.
- **경계는 코드가 소유한다.** `meeting_minutes.finalized_view(text)` 가 부록 위만 돌려준다. 그 상수 옆에 붙어 있으므로 사본을 만들면 갈라진다 — 새로 자르지 말고 그 함수를 쓴다. 전사본 절은 렌더러가 `TRANSCRIPT_WARNING` 을 바로 아래에 찍어 금지를 유혹이 있는 자리에 둔다(회귀 고정: `tests/unit/test_meeting_skill.py`).
- **회의 상태 파일 두 개도 같은 트리에 있다.** 과제의 미결·완료 action item 원장(`회의록/<과제>/action-items.csv`)과 관리번호 코드 레지스트리(`회의록/project-codes.csv`)는 날짜 없는 상태 파일이며, 규약과 진입점(`drive_outputs.publish_state_file` / `fetch_state_file`)은 [drive-publish.md](docs/guide/drive-publish.md) 가 단독으로 소유한다.
- **카드도 정본을 먼저 가리킨다.** `--project` 를 준 인제스트의 카드 본문은 `출처(정본): Drive 회의록/<과제명>/` 을 먼저 적고 로컬 사본을 뒤에 적는다. 과제명을 모르면 없는 경로를 지어내지 않고 기존 로컬 경로만 적는다.
- **배경(사후 반영)**: 2026-08-26, 전사본에서 만든 공정표 템플릿이 기관명을 틀렸다 — `한정기술`(정본은 **한국전력기술**, 다른 회사), `현대중공업 서부기계`(정본 **현대중공업터보기계**), 열교환기 `대통`(정본 **계통**) 열수력. 마감일도 `2028-02-01/04-01`(정본 **02-29/04-30**)로 어긋났고, 회의록이 규정한 열 위치(B2=기관명, B~E=대·중·소분류, H~I=시작·종료일, N=결과물)를 무시한 간트 격자를 지어냈다. 그 파일은 8개 참여기관에 배포될 것이었다. 완성 회의록에는 「용어·명칭 교정 기준」 표가 있어 이 오기들이 이미 교정돼 있었다 — 읽을 곳을 안 읽은 것이 유일한 원인이다.
## 산출물은 Drive에 둔다 (cha 지시, 2026-08-26)

**에이전트가 cha에게 내놓는 산출물(문서·표·보고서)은 로컬 경로에 남기지 않고 Drive 표준 트리에 발행한다.**

- **발행 경로는 하나다** — `automation/drive_outputs.py` 파사드. 세션에서 손으로 올릴 때는 `python3 -m automation.drive_publish_cli --kind <종류> --title <제목> [--project <과제>] <파일>` 을 쓴다. `gws drive` 를 직접 부르거나 헬퍼를 vendoring 하는 것은 금지이며 `tests/unit/test_drive_outputs_conformance.py` 가 막는다. 그 CLI 는 업로드 로직을 갖지 않고 인자만 파싱해 파사드에 넘긴다 — 두 번째 발행 경로를 만들지 않기 위해서다.
- **카테고리는 `automation/drive_taxonomy.CATEGORIES` 가 유일한 출처다.** 일반 문서 산출물은 `doctype`(문서). `meeting`(회의록) 은 회의록이 사는 곳이므로 회의에서 **파생된** 산출물을 거기 넣지 않는다 — 넣는 순간 그 폴더가 "회의가 결론 낸 것"을 뜻하지 않게 된다.
- **과제가 있으면 `--project <과제명>`** 으로 한 단을 넣어 같은 과제의 전사본·회의록과 묶는다. 과제를 쓰면 파일이 정확히 depth 5 에 놓이므로 그 발행의 산출물은 1개여야 한다(2개 이상은 번들이 되어 depth 6, `TaxonomyError` 로 거부된다).
- **워크스테이션에서 만든 파일은 그대로 올라가지 않는다.** `gws` 는 노드 `agent` 계정에만 있다. 노드로 옮긴 뒤 배포 런타임(`/srv/autophagy-agent-current`)을 `PYTHONPATH` 에 두고 발행하고, 노드의 임시 사본은 발행 직후 지운다.
- **발행했다고 말하기 전에 Drive 를 다시 조회한다.** 파사드가 sha256 재다운로드로 내용은 검증하지만, **사본이 늘지 않았는지는 별도 조회로만 보인다**. 대상 폴더에 파일이 정확히 1개인지 확인하고 그 출력을 증적으로 남긴다.
- 절차·경로·환경변수의 단독 정본은 [docs/guide/drive-publish.md](docs/guide/drive-publish.md) 다. 이 절은 불변식만 들고 있고 절차를 복사하지 않는다 — 같은 절차를 두 문서가 설명하면 반드시 한쪽이 낡는다.
- **배경(사후 반영)**: 2026-08-26, 8개 참여기관에 배포될 용역공정표 템플릿을 만들어 `~/Documents` 에 두었다. 규약은 이미 Drive 를 말하고 있었지만 파사드를 부르는 것은 스킬 코드뿐이었고 세션이 쓸 명령이 없었다. **명령 없는 규칙은 지켜지지 않는다** — 그래서 규칙과 진입점을 같은 사이클에 함께 넣는다.
