# AGENTS.md — autophagy-agents repo, orchestrator/agent instructions
> **Tool-call encoding**: Always write Korean (and other non-ASCII) strings in tool-call parameters as literal UTF-8; never as `\uXXXX` unicode escapes.

> 이 파일은 짧은 지침문만 둔다(모든 세션이 매 호출 읽는다). 각 절의 원문(조건·예외·배경 전체)은 [RULES.md](RULES.md)의 같은 제목 절에, 코드 지도·구조·찾을 곳은 [CODE-MAP.md](CODE-MAP.md)에 있다. 지침문이 애매하거나 예외·세부가 필요하면 그 원문을 읽고 원문을 따른다. 규칙을 바꾸면 같은 커밋에서 RULES.md 원문과 이 지침문을 함께 고친다(형 결정 Q290, 2026-10-19).

## OVERVIEW

- P6 기본 설치(`automation.install`·마법사·quickstart)는 도리: 고정 포크와 리드+YooDori 역할을 설치한다. Hermes 인프라는 레거시 `--profile core|rag|report-hub|full`에만.
- 기존 노드는 서명 릴리스로 업데이트하고 기본적으로 멈춘 잡·peer·유닛을 보존한다.
- P5 제거는 `peer-retired` 스위치가 같은 HEAD 릴리스 승인에 묶인 경우에만 백업 뒤 실행. 계정·/etc 삭제는 별도 소유자 블록.
- 모델 호출은 계정 `~/.hermes/config.yaml`의 주 모델(`model:`)을 먼저 쓰고, 답하지 못할 때(쿼터·인증·전송)만 같은 파일의 `fallback_providers`로 넘어간다. 모델은 그 파일 한 곳이 정하고 코드는 적지 않는다. 체인 전체 실패 시 fail-closed 전파.
- Q245 예외: 릴리스 호스트 `~/.hermes/release-flags/omo-relay`가 정확히 `on`이면 `codex_llm` 배치는 이 PC의 도구 없는 omo로 가고 모델은 이 PC omo 설정만 따른다. 파일 삭제 후 재배포하면 레거시 Hermes 경로로 복귀.
- **모든 외부효과는 소유자(cha) 승인 게이트 경유**: 아키텍처의 중심 불변식.
전체: [RULES.md](RULES.md) 「OVERVIEW」

## STRUCTURE

디렉터리 트리는 [CODE-MAP.md](CODE-MAP.md) 참조.
전체: [CODE-MAP.md](CODE-MAP.md) 「STRUCTURE」

## WHERE TO LOOK

위치 표는 [CODE-MAP.md](CODE-MAP.md) 참조. 아래는 표에 묻힌 규칙만 발췌:
- 채널 표시 이름은 설정 ID가 있으면 바꿔도 된다. 공급망은 `personal_approvals_channel_id` 또는 명시적 `deploy_approvals_channel_id`와 type 0을 검증, 통지는 `owner_notice_channel_id`. 캐시·이름 검색은 설정 없는 레거시 공급망 조회에만.
- 새 스킬 제작·배포: `automation/deploy-skill.sh` 4단계 게이트.
- 도리 포크는 `dori/pin.toml` 커밋 하나로만 설치(SHA 재확인, main 자동 추종 없음). 올릴 때는 SHA 변경 PR + 릴리스 노트 한 줄, 영수증 `dori_pin`. 저장소 층 단계는 노드 게이트웨이 동작 불변.
- 전사는 기본 로컬 `whisper.cpp`, 원음을 노드 밖으로 내보내지 않는다(speechtotext).
- 모델: 코드·스킬·환경변수에 모델을 적지 않고 argv에 `--provider`/`-m` 넣지 않는다(`test_model_single_source_conformance.py`). 확인은 `python3 -m automation.doctor`의 「주 모델」·「폴백」 줄. 주 모델 변경 뒤 모델 고정 없는 agent 모드 cron을 같은 정의로 재생성, 잡별 모델 고정 금지(`configs/routing-policy.md` 「Changing the main model」, `drift_skip`은 첫 회부터 고장). 제안서 윤문도 이 경로(Codex CLI는 codex 그림 생성만), `codex_llm.py`에 `--ignore-user-config` 없음. 폴백 추가·제거는 `~/.hermes/config.yaml`에서만. 라우팅 로그는 `served_provider`·`served_model`(`codex_llm.complete_served`). 내용에 따라 경로를 고르거나 막지 않는다.
- Drive 참고자료: 읽기 전용, 루트 `DRIVE_REFERENCE_ROOT`(기본 `KIMM`)는 산출물 `autophagy/`와 다른 트리. `ensure_folder_path` 금지, `find_folder_path` 사용. 회의록 참고자료는 `Deck` 경로로 받고 질의는 회의 라벨·과제명+전사 본문 상위 낱말. 읽을 수 없는 형식·크기는 `reference_rank.refusal`이 내려받기 전에 판정.
- Drive 산출물: `project=`를 주면 카테고리와 연도 사이에 과제 한 단, 안 주면 기존 경로.
- QA 증적: 공개 문서는 경로만 인용, 내용을 옮기지 않는다.
- 승인 표면: 공급망 승인(배포·발행·managed 활성화·release)=개인 서버 `#approvals`. 표면은 `approval_surface.py`·conformance 테스트가 단일 진실.
- 정기 통지(일일 지출·주간 동향·아침 감사·헬스체크·리마인더)는 `owner_notice_channel_id`(미설정=DM), 발신은 `automation/owner_notice.py` 파사드만. 파사드 밖 DM 오픈 신설=RED.
- 원인 확인 전 재시작/설정변경/키 재발급 금지.
- 관리형 스킬 발행은 cha 전용, 대상 repo = `orientpine/ribosome`(private), 코드 repo·공개 배포본이 아니다(「세 저장소 구분 규칙」). 구독은 연구원 전용. 개인→그룹 제출은 personal provenance 재사용, 그룹 `#approvals` 검토, 자동 import 없음.
- 관리형 구독 틱: `deploy.sh`(Hermes cron) 또는 opt-in `--with-component managed-sync`(systemd), 같은 래퍼. 배달은 자동, 마운트는 아니다.
- 자가 저작 스킬은 `~/.hermes/skills`(계정 소유 0700)에 승인 없이 착륙, 사후 감사. 관리 배포본은 `/srv/autophagy-skills/live`(`skills.external_dirs`, 읽기 전용). 회수는 `hermes curator archive/pin`.
전체: [CODE-MAP.md](CODE-MAP.md) 「WHERE TO LOOK」

## CODE MAP

모듈별 코드 지도(경로·역할·계약)는 [CODE-MAP.md](CODE-MAP.md)에 있다. 이 파일은 모든 세션이 매 호출 읽으므로 지도를 여기 두면 세션마다 문맥 약 6만 토큰이 든다(2026-10-19 실측, 옮긴 이유). 모듈을 찾을 때는 CODE-MAP.md를 통째로 읽지 말고 경로·이름으로 grep해 그 줄만 읽는다. 「문서 갱신 규칙」이 말하는 CODE MAP 갱신은 CODE-MAP.md에 한다.

## CONVENTIONS (repo 고유)

- 장문 메일 승인은 `mail_approval_attachment`가 같은 메시지의 본문 첨부를 파일명·크기·SHA로 검증하며 짧은 카드·기존 승인 해시는 보존한다.
- `calendar_digest` 개별 결과는 자기 카드 답글, 일별 스레드를 종결하지 않는다.
- Plaud `of_` 별칭은 기존 녹음 신원 재사용. 상태 병합은 백업 후 명시적 마이그레이션만, 클라우드 재확인 오류로 원래 전사 실패를 덮지 않는다.
- 수리 CLI 승인 대기=exit 5. 적용 직전 승인 digest 대조 바이트 스냅샷만 Git stdin, 읽기용 bootstrap은 스레드 미생성, PR 뒤 정리는 병합된 유휴 로컬 ref 작업 클론만.
- `PlanSpec.total_months`는 월 구간·순차 작업에서 유도, 계산된 총기간을 JSON에 저장하지 않는다.
- 문서 설명은 라이브 반영 영수증이 아니다. PR #502는 별도 소유자 판단 전까지 통합 금지.
- **stdlib 전용 지향.** `from __future__ import annotations` + `@dataclass(frozen=True, slots=True)` + 엄격 타입(`TypeAlias`/`Protocol`). 외부 의존은 함수 내부 lazy import+fail-closed 가드.
- 격리 예외: `configs/stt-engines/`(pyannote·WhisperX 별도 venv, `uv.lock` 고정, 모델 캐시 체크아웃 밖, CLI 계약은 README). `configs/rag/*`(`uv` + Ruff `ALL` line-length 100 + basedpyright `all`). `skills/mail/vendor/mailon/`(4개 서드파티 `vendor/requirements.txt` 고정+스코프 venv, byte-identical 유지하되 고칠 땐 vendor 소스 직접 수정·재배포, PROJECT_ROOT 상대 data/logs 쓰기 규약 불변, 런타임 `~/.hermes/mailon-runtime/`).
- **모든 외부효과는 소유자 승인 게이트 경유**(✅/⛔), 직접 실행 금지.
- **커밋됨≠배포됨≠실행됨.** 독립 표면 셋: ①`/srv/autophagy-agent-current`(서명 태그 자동 수렴) ②스킬 마운트(`readlink /srv/autophagy-skills/live/<skill>` 해시, 갱신은 소유자 ✅) ③계정홈(선언 패키지는 `release.sh`→`deploy_all.sh --apply`, 선언 밖 사본만 해당 `deploy.sh`). 게이트웨이 플러그인은 재시동 전까지 반영 안 됨. 유일한 스킬 배포본은 live 실경로, SKILL.md 예시도 live 경로. mutating CLI는 `skill_mount.governed_copy_refusal`(`<skill>_governed.py`) 판정 필수. 수리는 릴리스 런타임 사본 실행(`test_repair_exec_path.py`).
- 매니페스트: `<pkg>/deploy-manifest.txt`, 중앙표 `configs/watcher-deploy-manifest.txt`는 `python3 -m automation.watcher_manifest emit`만(손편집·emit 누락·선언 없는 배포기 금지, `test_watcher_manifest_declarations.py`). `deploy-manifest.txt` v2는 네 번째 필드에 `v2:<kind>;key=value;…`를 쓴다, kind `file`·`tree`·`derived`·`cron`(`runner=hermes|systemd`), policy `required`·`optional`·`held`·`retired`, 정본 `automation/deploy_declarations.py`.
- 홈 쓰기=`push_file`/`deploy_tree_swap`/`converge_cron`만(`test_deploy_write_inventory.py`, 예외 원장 `configs/deploy-write-exemptions.txt`, 규약 `docs/guide/watcher-cron-설계규약.md` (o)).
- **상태 마킹은 성공 이후**: claim → 작업 → 성공 시에만 processed 기록, 실패 시 release. **fail-closed**: 설정/권한/확인 불가 시 실행하지 않는다.
- **추적 config=불변 시드.** 체크아웃 밖에만 런타임 상태.
- **`managed-` 접두사 예약**: 관리형 스킬은 반드시 `managed-`로 시작, 일반 배포는 사용 불가. 충돌은 우선순위 없이 양방향 fail-closed, 소유자가 하나를 `--remove`.
- **자가 스킬 루트(`~/.hermes/skills`)=비통제**: 계정 소유 쓰기 가능 1차 루트, 자가 저작은 승인 없이 착지. 관리자 배포본은 `/srv/autophagy-skills/live`(root RO, `skills.external_dirs`), `~/.hermes/skills` 아래를 관리자 배포본으로 읽지 말 것. self→governed 그림자는 `SHADOWS-GOVERNED`로 통지됨. governed→self: `deploy-skill.sh`가 분류 불가 포함 `SELF-SKILL-COLLISION-BLOCK` exit 4, 소유자가 `hermes curator archive <name>` 또는 디렉터리 제거해야 재개.
전체: [RULES.md](RULES.md) 「CONVENTIONS」

## ANTI-PATTERNS (금지)

- cron 워처: Discord **메시지/첨부 폴링** 금지, **리액션만** 폴링.
- subprocess 자격증명 미전파(`env=` 누락) 금지. no-agent cron은 명시 전파 필수.
- 채널·기능별 병렬 confirm 워처/resolve 신설 금지. 기존 게이트에 `channel_id` 바인딩으로 재사용.
- 도메인 중첩 스킬: SKILL.md 라우팅만 의존 금지. mutating 경로에 결정론적 **양방향** 코드 가드 필수. calendar↔coordination 양쪽 CLI는 공유 `calendar_routing.classify_meeting_request`를 함께 강제: 정확한 단일 시각=`calendar`, 피어+범위+조율의사=`coordination`, 모호=`clarify`(fail-closed).
- 토큰 모양 문자열(`Bot `, `sk-`, `ghp_`) 주석/문서에도 사용 금지.
- 프로덕션 게이트웨이에 `E2E_TEST_MODE=1` 금지.
전체: [RULES.md](RULES.md) 「ANTI-PATTERNS」

## COMMANDS

명령어 목록은 [CODE-MAP.md](CODE-MAP.md) 참조.
전체: [CODE-MAP.md](CODE-MAP.md) 「COMMANDS」

## NOTES

- 설계·작업분해 단일 진실: `.omo/plans/autophagy-agents.md`.
- 배포: 오케스트레이터에서 `DEPLOY_SSH_HOST`로 SSH + `sudo -n -u agent|peer`. 값은 `~/.hermes/node.toml`의 `deploy_ssh_host`, `~/.ssh/config` Host 별칭이어야 한다(별칭에 직결 주소·`IdentityFile`·`StrictHostKeyChecking accept-new`). mDNS/tailnet 주소는 스크립트에 쓰지 않는다. 오케스트레이터 호스트가 바뀌면 `node.toml`부터 만든다(없으면 `DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset`).
- `logs/`는 git 미추적(디렉터리만 유지). `configs/rag/*/.venv`·`__pycache__`·`*_cache`는 소스 아님.
- `.git/hooks/` 미추적: clone마다 gitleaks pre-commit 훅 재설치. `gitleaks` PATH 없으면 커밋 fail-closed.
전체: [RULES.md](RULES.md) 「NOTES」

## 커뮤니케이션 언어 (사용자 지시, 2026-07-14)

- cha에게 보내는 모든 말(채팅 응답, 상태 보고, 질문, 요약)은 반드시 한국어로 쓴다. 대상은 Atlas(오케스트레이터)가 cha에게 보내는 모든 메시지.
- 제외: 코드, 커밋 메시지, 파일 내용, 서브에이전트 delegation 프롬프트, `.omo/plans/`·`.omo/notepads/`·`docs/` 등 산출물. 산출물은 각 문서의 기존 언어 스타일을 따른다.
원문: [RULES.md](RULES.md) 「커뮤니케이션 언어」 · 장부 `cyto-korean-to-owner`

## Owner-confirm 이모지 컨벤션 (cha 지시, 2026-07-17)

- 모든 소유자 confirm: ✅=승인, ⛔=취소. cha 리액션만 유효(owner_id, bot==false), 둘 다면 취소. 봇이 ✅⛔ 선부착, 텍스트 지시·별도 DM 금지(`실행/취소 <id>`는 fallback).
- 대상 메시지는 드래프트 sha256 참조, 확인 불가·모호하면 실행 금지. 메일 카드는 게시 글 전체 `card_sha256`이 같을 때만 승인(`mail_card_compact.py`), draft id·sha256·취소/다음/되돌리기 줄은 Discord 비노출. 텍스트 파일로 옮긴 카드에도 첨부 이름·크기·Drive 공유 대상, 구글 계정 없는 수신자에게 초대 알림 메일이 간다는 안내 표시. 같은 형 스레드에 새 초안이 오면 옛 미결 카드는 「새 판으로 대체됨」으로 닫히고 ✅로 실행 안 됨(`triage_supersede.py`).
- 워처 cron은 리액션만 폴링(메시지 텍스트·첨부는 에이전트 단독), `~/.hermes/scripts/` 파일명 스킬별 고유, `~/.env.secrets` self-load·sys.path 래퍼·자식 env 자격증명 명시(`docs/guide/watcher-cron-설계규약.md`).
- 중첩 스킬: 양보 스킬 mutating 경로를 결정론 가드(등록 레지스트리 매칭·소유자 제외·fail-closed)로 거부+안내. 기존 게이트 재사용(레코드·render·resolve·watch 하나, 채널은 `channel_id`), 병렬 confirm 신설 금지.
원문: [RULES.md](RULES.md) 「Owner-confirm 이모지 컨벤션」 · 장부 `cyto-owner-confirm-emoji`

## 문서 갱신 규칙 (cha 지시, 2026-07-20)

- 코드 작업은 문서 갱신까지 마쳐야 종결이다. 기능 추가/변경/제거 시 같은 커밋·배포 사이클에서 그 리포의 AGENTS.md·docs/·SKILL.md 중 낡는 문구를 반드시 함께 고친다.
- 대상: autophagy-agents와 에이전트가 참조하는 모든 산출물 리포(emailAutomation 등).
원문: [RULES.md](RULES.md) 「문서 갱신 규칙」 · 장부 `cyto-docs-with-code`

## 다른 세션 작업 덮어쓰기 방지 (cha 지시, 2026-07-21)

- 작업 전 대상 영역의 최신 상태를 반드시 확인한다. 설계·규약 문서 수정 전 `git log`로 최근 커밋을 보고, 그 주제를 바꾼 커밋이 있으면 메시지·diff로 rationale을 파악한 뒤에만 손댄다. 내 이해와 충돌하면 덮어쓰지 말고 사용자에게 확인.
- 내 기억과 repo가 다르면 repo가 진실, 옛 이해로 되돌리지 않는다. 사용자가 "최근 세션에서 해결했다"고 하면 먼저 그 커밋/세션을 찾는다(`git log --oneline -S <키워드>`, session 도구, diff).
- 다른 세션의 미커밋 작업은 stash/reset/checkout/discard 금지. 소유자 명시 승인 때만 복원 가능한 방식(stash 후 pop)으로 하고 종료 시 반드시 복원.
원문: [RULES.md](RULES.md) 「다른 세션 작업 덮어쓰기 방지」 · 장부 `cyto-no-overwrite-other-session`

## 게이트웨이 재시동 규칙 (cha 지시, 2026-07-22)

- 재시동이 결정되면 모든 Hermes gateway(향후 추가 계정 포함)를 함께 재시동한다. 한쪽만 재시동 금지. healthy 서비스는 재시동하지 않고 원인 확인 후에만.
- 수동 운영·장애 대응·재부팅 복구·배포 스크립트(`automation/skill_generation/deploy.sh` 등) 모든 경로에 적용, 절차는 `docs/guide/operations.md` §2. ops·dashboard 등 다른 unit은 대상 아님.
원문: [RULES.md](RULES.md) 「게이트웨이 재시동 규칙」 · 장부 `cyto-gateway-restart-all`

## 기능 소개 문서 규칙 (cha 지시, 2026-07-25)

- 기능 완료 시 소개 문서 `docs/기능소개/<기능-slug>.md`(기능당 1개)를 만들고 `docs/features.md` 해당 항목에서 링크한다. 코드 완료 = 낡은 문구 갱신 + 소개 문서 작성.
- 1페이지 내외, 구현 덤프 말고 필수로 무엇을(한두 문장)·왜(해결한 문제)·사용 시나리오(cha 관점 입력→동작→결과, happy-path 최소 1개, 실패/거부 경로 있으면 함께)·관련(스킬/티켓/핵심 경로, 승인·게이트 관여 시 명시).
- 토큰·원문 로그·실제 문서 본문은 넣지 않는다(마스킹).
원문: [RULES.md](RULES.md) 「기능 소개 문서 규칙」 · 장부 `cyto-feature-intro-doc`

## 커밋 규칙 (cha 지시, 2026-07-25)

- autophagy-agents에서 작업하는 모든 세션은 논리적 단위(기능·버그 수정·문서·리팩터)마다 커밋한다. 무관한 변경을 한 커밋에 섞지 않는다.
- 메시지는 Conventional Commits(`feat(scope):`, `fix(scope):`, `docs(scope):` 등). 코드가 문서를 낡게 하면 같은 단위 커밋 사이클에서 문서도 갱신.
원문: [RULES.md](RULES.md) 「커밋 규칙」 · 장부 `cyto-commit-per-unit`

## 배포 provenance 규칙 (cha 지시, 2026-07-25)

- prod엔 `origin/main`(공개 `orientpine/cytoplasm` main) 코드만. 미커밋·미푸시·뒤처진 체크아웃 배포 차단.
- 신규 deploy 스크립트는 반드시 `automation/deploy_provenance.sh`의 `deploy_provenance_check <repo_root> <경로>...` 호출(불일치 `DEPLOY-BLOCK`, 디렉터리 인자는 untracked 비-ignored 파일도 차단, `deploy-skill.sh` 포함).
- 예외: `deploy-skill.sh --sandbox-only`/`--activate-managed`/내부 staged-source용 `SKILL_SRC_DIR` 지정, `regression_bank/deploy_lab_node.sh`. 개인 저작은 이 우회를 쓰지 않고 `--personal <name>`(`personal_provenance_check`: 독립 repo clean·committed HEAD, untracked 비-ignored 부재, 승인 SHA 바인딩)만.
- `DEPLOY_ALLOW_UNPUSHED=1`은 샌드박스/실험 전용, 상습 사용 금지. 순서는 커밋 → 푸시 → 배포.
원문: [RULES.md](RULES.md) 「배포 provenance 규칙」 · 장부 `cyto-deploy-provenance`

## 작업 종결 규칙 (cha 지시, 2026-07-25)

- 작업은 커밋 → 푸시 → 배포 → 검증까지 마쳐야 종결, 순서 변경 금지. 배포할 것이 없으면(문서·테스트만) 커밋·푸시로 종결.
- 배포 경로: 스킬 = `automation/deploy-skill.sh`(4단계 게이트), 워처 = 각 `deploy.sh`, `/srv/autophagy-agents/automation/**` = ops 계정 `git pull --ff-only`. 검증 증적: live 심링크 해시/배포본 해시/실제 실행(cron tick·import·smoke) 중 해당분.
- 소유자 승인이 필요하면 승인 요청 게시까지 하고 cha ✅ 뒤 남은 단계(MOUNT·검증·kanban 종결)도 같은 세션에서 마친다.
- 다른 세션 미커밋·로컬 전용 커밋은 절대 함께 커밋 금지. 브랜치가 갈라졌으면 `origin/main` 기반 worktree에서 내 변경만 커밋·푸시.
원문: [RULES.md](RULES.md) 「작업 종결 규칙」 · 장부 `cyto-done-means-deployed`

## 릴리스 패치노트 작성 규칙 (cha 지시, 2026-08-30)

- 패치노트 제목은 무엇→왜→영향(티켓·파일명·`fix`만 불충분). `release.sh`가 커밋 제목을 쓰니 커밋 제목이 기준을 만족해야 하고, 아니면 보강 후 재계획.
- 카드는 짧게, v8·v7 모두 최대 12줄·1900자. v8: 요약은 `--notes-file` 첫 줄(`# …`, 없으면 커밋 제목 하나), 스위치는 새로 켜지거나 값이 바뀐 것만, 나머지는 「계속 켜져 있는 스위치 N개(바뀐 것 없음)」(직전 표면 `~/.hermes/release-notes/<sha>.surfaces.json`, 모르면 전부 표시). v7: 버전·짧은 HEAD·한두 변경 요약·승인 표면/스위치·운영자 경보·전체 `action_hash`·결정 방법만 싣고, 스위치가 여러 개면 같은 줄에서 구분하며 내용을 빠뜨리지 않는다. 끝에 "자세한 건 유돌에게 물어보면 올려요". 1900자 초과면 승인 대상 줄을 줄이지 말고 게시 거부. 상세 자동 게시 금지.
- 상세는 호스트 `plan`이 승인 전에 `${RELEASE_NOTES_DIR:-~/.hermes/release-notes}/<sha>.details.md`(0600)로 저장. 유돌은 형이 물을 때만 `release-notes <vX.Y.Z>`로 공개 Release 노트를 읽어 올린다(토큰·쓰기 없음).
- 저장된 v1~v6 카드와 v6 상세 메시지는 바이트 동결, `action_hash`·표면·nonce 바인딩 불변, 승인 lifecycle 그대로.
- ✅ 뒤 별도 수동 재개를 요구하지 않는다(`release.sh`가 결정을 기다리다 ✅ 감지 즉시 서명 태그). deadline은 테스트·의도적 제한 운영에서만 명시.
원문: [RULES.md](RULES.md) 「릴리스 패치노트 작성 규칙」 · 장부 `release-card-short`

## 릴리스 태그 규칙 (cha 지시, 2026-08-20)

- 서명 릴리스 태그 없이는 프로덕션이 전진하지 않는다. 태그는 `automation/release.sh`(형 ✅ 1회)만 자른다(`merge-pr.sh`는 안 자름). `converge_origin_main.sh`는 인자 없음(MD-1). CI 서명 금지, 자동화는 실행 시점만.
- 키 `UPDATE_TRUST_SIGNING_KEY`(기본 `~/.ssh/autophagy_update_trust.pub`), 비밀 키는 주 노드 운영자 계정 `~/.ssh`(0700)에만. 커밋·CI/서비스 계정(agent·ops·peer·도리) 접근·다른 머신 복사 금지. 호스트 이전은 새 키로 회전(`docs/guide/manual-maintainer.md` §3.4).
- 릴리스·공개 반출은 릴리스 호스트(주 노드 운영자 계정, `deploy_ssh_host`용 `from=` 제한 자기 SSH 키 필요) 메인 체크아웃에서 `automation/local_ci.sh run`(`GH_CONFIG_DIR=$HOME/.config/gh`) → `automation/release.sh`. 다른 머신이면 SSH 셸로 거기서. 먼저 그 머신에서 `ssh -o BatchMode=yes <deploy_ssh_host> 'hostname && sudo -n -u agent -H true'`, 그 머신의 로컬 CI 영수증, `gh auth status` 확인(없으면 `gh auth login --web`을 형이 승인, 토큰 복사 금지).
- 태그 뒤 자동 배포를 생략할 때만 `--no-deploy`; `release.sh` exit 10이어도 태그는 되돌리지 않는다. 수동 `release-tag.sh`는 머지 직후, HEAD 어긋남 실패면 재실행(연속 머지 끝에 한 번이면 됨).
- 완결 타이머 `automation/release_complete_install.sh`는 호스트 한 곳 메인 체크아웃에 설치, 옛 호스트는 `--uninstall`. ✅ 뒤 노드 수작업 0: 노드 준비는 배포기가 릴리스 안에서, 노트·문서에 "root가 한 번 실행" 금지, 권한은 ops·agent·peer와 기존 NOPASSWD 안에서만(root·넓은 sudo 금지). `pending_owner_actions`가 비어야 끝.
- 태그 구현은 `automation/release_tag_lib.sh` 하나(`land.sh`·`release-tag.sh` 공유), 사본 금지(`tests/unit/test_release_tag.py`).
원문: [RULES.md](RULES.md) 「릴리스 태그 규칙」 · 장부 `cyto-signed-release-tag`

## 승인 메시지 단일성 규칙 (cha 지시, 2026-07-25)

- 같은 `action_hash` 승인 메시지 중복 게시 금지, 저장된 `message_id`를 새 ID로 덮어써 옛 메시지를 고아로 만들기 금지.
- 모든 승인 요청은 `automation/interop/approval_lifecycle.py` 파사드를 거친다. 형이 이미 ✅/⛔한 요청은 건드리지 않고 워처에 양보(DEFER).
- `tests/unit/test_approval_lifecycle_conformance.py`가 강제한다. 예외는 소스 주석이 아니라 테스트의 `_EXEMPT` 맵에 사유와 함께 등록한다. 상세: `docs/guide/watcher-cron-설계규약.md §(j)`, `automation/interop/AGENTS.md`.
원문: [RULES.md](RULES.md) 「승인 메시지 단일성 규칙」 · 장부 `cyto-approval-message-single`

## 결과 통지 원채널 스레드 규칙 (cha 지시, 2026-08-22/23)

- 승인 게이트가 있는 모든 스킬(새 스킬 포함)은 실행·취소·만료 결과를 `approval_thread_id` 스레드로 `origin_notice.deliver(...)`(`automation/interop/origin_notice.py`)만 써서 통지한다. 자체 스레드 생성 금지. 종결엔 반드시 `outcome=`, 중간 ACK엔 넘기지 않는다. 승인 요청·리마인더·게이트 경로는 건드리지 않는다.
- mutating CLI는 선택 인자 `--origin-channel-id`/`--origin-message-id`를 레코드 `origin_channel_id`/`origin_message_id`에 승인 해시 밖으로 저장(레거시 필드 부재 허용). 문구는 대상·id·사유("…됨" 고정문구 금지). 스킬 마스킹 우선: calendar는 action 종류·draft id만, patent-prep은 통지 제외.
- 실패는 `NOTIFY-FAIL`, tick·exit code·영수증·원장 불변. E2E·주입 승인은 `NOTIFY-SKIP`. 헬퍼는 `INTEROP_RUNTIME`에 실어 배포. 강제 `tests/unit/test_origin_notice_adoption_conformance.py`: `APPROVAL_PRODUCERS` 전부 `origin_notice` 참조 또는 `_RESULT_NOTICE_EXEMPT`에 사유, 헬퍼 밖 스레드 API는 `_THREAD_API_ALLOWED`(meeting 게이트웨이 플러그인)만.
원문: [RULES.md](RULES.md) 「결과 통지 원채널 스레드 규칙」 · 장부 `cyto-result-notice-origin-thread`

## 요청별 승인 스레드 규칙 (cha 지시, 2026-09-01)

- 형 전용 승인은 요청 하나에 스레드 하나: 카드·리마인더·결과가 거기서 끝나고 종결 시 `✅ 완료 ·`/`⛔ 취소 ·`/`⌛ 만료 ·` 접두어 뒤 아카이브. `승인-<kind>`는 레거시 `request=None`만, 범위 내 생산자는 금지.
- `resolve_new_binding(kind, directory, owner, request=RequestThread(title=…, origin_channel_id=…, origin_message_id=…))` 필수. 지시가 이미 스레드 안일 때만 그 스레드에. 안내 문구는 `request_thread_notice`만. 제목은 마스킹 값만: mail·budget=제목, todo=할 일 제목, coordination=조율 라벨, calendar·patent-prep·repair=id, obsidian=노트 파일명(확장자 포함, 디렉터리·본문 제외).
- `binding.channel_id`를 `approval_thread_id`로 승인 해시 밖에 저장하고 결과 통지 dict에 싣는다. 자체 스레드 생성·PATCH 금지, 종결은 `origin_notice.close_thread`. 강제 `tests/unit/test_request_thread_adoption_conformance.py`(`request=` 없는 호출 금지), 예외는 공급망 표면 `skill_gate_surface.SupplyChainSurface.new`만 `_KIND_THREAD_EXEMPT`에 사유와. 런타임 먼저 배포.
원문: [RULES.md](RULES.md) 「요청별 승인 스레드 규칙」 · 장부 `request-thread`

## 스레드 제목 규칙 (cha 지시, 2026-10-16)

- 오토파지가 여는 모든 스레드는 이름이 종류 이모지로 시작하고, 첫 메시지 맨 위에 `## <이모지> <무엇·날짜·건수>` 제목 한 줄을 승인 카드와 따로 올린다. 카드 본문·해시·참조 줄·파서 형식은 바꾸지 않는다(`render_version` append-only).
- 단일 정의: 승인 `approval_surface.KIND_ICONS`·`request_thread_title`, 결과 `origin_notice.thread_icon`·`resolve_thread_id`. 새 승인 종류는 `KIND_ICONS`에 이모지, 새 결과 스레드는 `_THREAD_ICONS`에 앞말(`tests/unit/test_thread_titles.py`). 스레드 직접 여는 코드 금지.
- ✅·⛔·⌛는 제목 이모지 금지. 제목 실패는 `THREAD-TITLE-FAIL`, 승인·통지를 막지 않는다.
원문: [RULES.md](RULES.md) 「스레드 제목 규칙」 · 장부 `cyto-thread-title`

## 소유자 메시지 계약 규칙 (cha 지시, 2026-09-09)

- 소유자 대면 문구는 반드시 `automation/interop/owner_message.py` 봉투 5필드(대상·사실·위치·인계·되돌리기)로, `render(message, destination=…)` 만 만든다. 스킬 자체 조립기 금지, 새 스킬 예외 없음. 조치 없으면 `Action(verb="none")`, 후속 없으면 `agent_next=None`(침묵 금지). 목적지 바뀌면 재렌더. 절차: `docs/guide/스킬-제작.md` 「소유자 메시지 계약」.
- 위치는 좌표: 비지역이면 링크·검색 안내. 좌표는 바인딩 때 `approval_guild_id`/`approval_thread_id`로 승인 해시 밖에. 모르거나 결손이면 추측 금지(`@me` DM 링크 금지), 검색 라벨로. 렌더 때 Discord API·링크 프로브 금지. 공용 정의 밖 `discord.com/channels` 리터럴 금지. 봉투 불가·낡은 런타임은 `ACCEPTS_OWNER_MESSAGE` 프로브·`except ImportError`로 기존 문자열 경로, 새 예외 금지.
- 마스킹(캘린더·금액·위키·특허)이 링크를 이긴다: 못 낼 내용은 `fact`에서 빼고 좌표도 못 내면 위치 없음(위키·patent-prep 좌표 없음, calendar 결과·공유 출력 draft id만), 사유는 conformance 원장에. 예외는 `approval_surface`/directory 바인딩으로 게시 직전 검증한 소유자 전용 승인 스레드의 캘린더 카드 v3+ 상세뿐(공유·미상 표면 금지, 부모 안내·스레드 이름·결과 통지는 마스킹, v1~v3 바이트·해시 불변, 오류면 강등 없이 게시 거부, 저장 안 된 이전 값 생성 금지).
- 신규 승인 생산자는 카드 판본 v2 명시, 저장된 판본은 엄격 재생, 파사드는 명시 판본을 바꾸지 않는다. `owner-ko-v5`는 형 채널 정기 알림용 한 줄 판본이다. `render_version` append-only, 게시된 문구 수정 금지. best-effort 불변(`origin_notice.deliver` 폴백 자체의 예외는 호출자가 처리). 강제 `tests/unit/test_owner_message_adoption_conformance.py`: 카드 렌더 경로는 `_CARD_RENDER_ROOTS`, 예외는 `_RAW_CONTENT_EXEMPT`·`_PERMANENT_RAW_CONTENT_EXEMPT`·`_LINK_LITERAL_ALLOWED`·`_NOT_OWNER_FACING`에 사유, 렌더 경로 이동 시 같은 커밋에 루트 수정.
- 네이티브 답글이 이미 대상을 지목하면 작성 정책상 네 번째 링크를 생략할 수 있다(렌더러가 답글을 탐지하지는 않는다).
원문: [RULES.md](RULES.md) 「소유자 메시지 계약 규칙」 · 장부 `cyto-skill-ux`

## 승인 요청 답장 링크 규칙 (cha 지시, 2026-09-23)

- 승인 요청 CLI는 카드 게시 직후 `APPROVAL-THREAD <키>=<값> url=<스레드 링크>` 한 줄을 stdout에 내고, 에이전트는 소유자 답장에 그 URL을 그대로 싣는다. 새 승인 스킬도 예외 없음. 절차: `docs/guide/스킬-제작.md` 「승인 요청 답장 링크」.
- 그 줄은 저장 레코드(또는 반환 entry)의 `approval_guild_id`·`approval_thread_id`로 `automation/interop/thread_pointer.approval_thread_line(키, 값, 레코드)`만 만든다(직접 조립 금지). 좌표 없으면 `url=unavailable search=<값>`, 추측 금지. 미게시면 줄 없음. import 실패는 stderr `APPROVAL-THREAD-UNAVAILABLE` 한 줄, 게시된 승인·exit code 불변. SKILL.md가 답장에 URL 싣기를 지시한다.
- 위키·patent-prep은 줄 없이 원장에 사유. 줄엔 id·URL만. 강제 `tests/unit/test_approval_thread_pointer_conformance.py`: `APPROVAL_PRODUCERS` 전부 `_ADOPTED` 또는 사유 있는 `_EXEMPT` 중 정확히 하나, 헬퍼 밖 `APPROVAL-THREAD ` 리터럴 금지.
원문: [RULES.md](RULES.md) 「승인 요청 답장 링크 규칙」 · 장부 `cyto-skill-ux`

## 승인 경로 완결 규칙 (cha 지시, 2026-10-07)

- 현재 카드 대상은 「모든 요청 승인 경로 규칙」의 릴리스·계정 밖 경로다. 외부효과 카드는 생산자(`approval_lifecycle` 경유 요청 명령)+✅ 워처(no-agent cron/systemd, 사람 실행은 `manual:` 사유)+테스트+실행 영수증을 한 벌로 갖는다. `GATED_RULES`에 외부효과 생산자(또는 사유), `PRODUCER_WATCHERS`에 워처·테스트 파일을 등록한다(`tests/unit/test_gated_effect_paths_conformance.py`). 계정 내부 쓰기에 카드 생산자를 요구하지 않는다. Hermes 전용 denylist·플러그인은 은퇴했다.
- 즉석 초안·채팅 메시지 ✅ 요청 금지. 승인은 해시에 묶인 카드 ✅ 만. 전용 명령 없으면 `python3 -m automation.effect_request request`.
- 카드 실행 뒤 원격 재확인, `origin_notice` 로 같은 스레드에. 카드 대상 SKILL.md는 요청 명령만, `실행 <id>` fallback은 먼저 제안·요청 금지. 내부 쓰기는 소유자 Discord 명령에서 시작해 dori-gate로 바로 처리한다.
원문: [RULES.md](RULES.md) 「승인 경로 완결 규칙」 · 장부 `cyto-approval-path-complete`

## Drive 업로드·공유 승인 규칙 (cha 지시, 2026-10-14)

- Drive에 올려 누군가와 공유하는 일은 메일 `compose --drive-share <파일> --share-with <이메일>` 로만 한다. 업로드·권한·링크 메일이 카드 하나의 ✅ 로 함께 실행되고 한 단계라도 실패하면 메일은 나가지 않는다. 새 카드·워처 없음.
- 권한은 `type=user, role=reader` 만, 링크 공개 없음. 부여 전후 권한이 소유자+지정 사람이 아니면 멈춘다. 파일은 `autophagy/공유/` 아래.
- `gws drive permissions create|update` 직접 공유 금지. 유돌의 직접 공유 시도는 dori-gate가 바깥 공유로 판정해 카드 없이 나가지 않는다(Hermes 규칙 `gws_drive_permission_grant`는 2026-10-19 은퇴). 강제 `tests/unit/test_mail_drive_share.py`(메일 경로). 절차 `skills/mail/SKILL.md` §4-2.
원문: [RULES.md](RULES.md) 「Drive 업로드·공유 승인 규칙」 · 장부 `cyto-drive-share-via-mail`

## 모든 요청 승인 경로 규칙 (cha 지시, 2026-10-17)

- 이 절의 옛 규칙(모든 요청에 승인 카드)은 2026-10-18 형 결정으로 은퇴했다. 원문은 RULES.md에 기록으로만 둔다.
- 현재 규칙: 승인 카드는 cytoplasm `release.sh` 릴리스 카드와 형 계정 밖으로 나가는 모든 경로(메일 발송, 형이 아닌 참석자 일정 초대, 형이 아닌 대상 공유·권한, 형 소유가 아니거나 공개된 일정·문서·폴더 쓰기, 바깥 URL을 불러오는 이미지 넣기, 시트 쓰기, 시간당 10건 넘는 지우기)뿐이다. dori-gate가 결정적 코드로 판정해 카드를 올리고 형 계정의 ✅만 인정한다.
- 형 계정 안의 일정·문서·할 일·Obsidian 쓰기는 dori-gate(형 메시지 확인·변경 기록·하루 요약·되살리기)를 거쳐 카드 없이 한다. 유도리의 쓰기(일정·문서·할 일·Obsidian·메일 승인 카드 올리기)는 형 디스코드 메시지에서 시작한 요청일 때만 하고, 메일·문서·웹 내용이나 다른 봇 글만으로는 쓰지 않는다. dori-gate가 쓰기마다 `X-Dori-Owner-Message`의 작성자·서버·시각을 디스코드에서 확인하고 그 링크를 변경 기록에 남긴다.
- 형만 하는 일(자격증명 변경·결제·송금·구매·로그인/2FA/OAuth 동의·sudo·승인 대리 누름)은 대신하지 않는다. 그 밖의 예외는 장부 항목대로 한다.
은퇴한 원문: [RULES.md](RULES.md) 「모든 요청 승인 경로 규칙」 · 장부 `approval-two-only`·`outbound-all-cards`·`writes-owner-command-only` (옛 `cyto-approval-every-request` retired)

## 메모리 상한 규칙 (cha 지시, 2026-10-14)

- Hermes 메모리 상한 USER.md 3,000자·MEMORY.md 3,300자(agent·peer 같음)는 `automation/hermes_memory_config.LIMITS` 만 고쳐 릴리스한다. 노드 수동 수정 금지.
- 내리기 전 파일부터 줄인다(rc 7). 더 올리기 전 USER.md의 사람 정보·규칙부터 본다.
- USER.md는 본인만: `PERSON_REFERENCE`→Obsidian 사람 노트. SOUL 자동 회상 줄·분류 규칙 불변.
원문: [RULES.md](RULES.md) 「메모리 상한 규칙」 · 장부 `cyto-hermes-memory-limits`

## 후속 과제 기록 규칙 (cha 지시, 2026-07-26)

- 작업 중 발견했으나 이번 범위에서 처리하지 않은 사항은 요청을 마친 뒤 `docs/follow-ups.md` 에 기록해야 종결이다(커밋→푸시→배포+문서 갱신+기능 소개+후속 기록). 묶음 1개+하위 불릿, 증적 `docs/qa/<wave-id>/...` 는 끝에 한 번, 불릿은 "문제→조치"+영향·심각도. 없으면 적지 않는다. `docs/features.md` 현황판엔 묶음별 잔량 요약표만, PLAN "신규 아이디어"는 새 기능 전용. 계획에 반영된 건은 wave ID 병기해 PLAN "개발 예정"으로.
- 범위 밖·안전 불변식 위반·프로덕션 저해 결함은 즉시 수정. 후속은 (a) 동작 정상이나 개선 여지 (b) 범위 밖 인접 결함 (c) 별도 논의가 필요한 설계 판단에 한한다.
- OWNER(소유자·노드에서만 닫힘)·BLOCKED(동결·벤더·외부)·OBSERVE(조건 전 조치 안 함)·해소는 `docs/follow-ups-deferred.md` 로 이동(삭제 금지), follow-ups엔 지금 손댈 수 있는 열린 작업만. `##` 헤딩·불릿 첫 줄 유지(`tests/unit/test_features_board_conformance.py` A9).
원문: [RULES.md](RULES.md) 「후속 과제 기록 규칙」 · 장부 `cyto-follow-up-record`

## ops 체크아웃 단방향 규칙 (cha 지시, 2026-07-27)

- 배포 체크아웃 `/srv/autophagy-agents`(미러)는 origin/main의 단방향 거울: `git fetch`/`git pull --ff-only`만 허용, 편집·`git add`·`git commit`·`git stash` 금지(에이전트 런타임 학습분도 예외 없음, 노드 모드 무관). 수정은 개발 체크아웃에서 커밋→푸시→배포, push+노드 동기화는 `automation/land.sh`로. 런타임 산출물은 체크아웃 밖(`~/.hermes/…`)에.
- 커밋≠배포: 스킬 반영은 `readlink /srv/autophagy-skills/live/<skill>`로 판정.
- 커밋 거부 훅(`install_commit_refusal_hook`, `tests/unit/test_deploy_checkout_commit_refusal.py`)·`checkout_mirrors_origin` 프로브·`sync_mirror`를 우회·약화 금지(`--no-verify` 포함). 프로브는 `git fetch` 금지(read-only). 프로브·채점·가이드는 `automation/checkout_mirror_probe.sh`로 공유하고, `land.sh`가 `declare -f`로 노드에 보내는 3개 함수는 자기완결로(노드 사본 source 금지). 깨진 `/srv/autophagy-agent-current`는 부재가 아니라 손상, 랜딩 거부.
- 앞선 커밋 복구는 `git format-patch`→개발 체크아웃 main 위에 `git am`(작성자·시각 보존)→blob 동일 확인→정렬, 되돌림 ref를 노드에 남김. `reset --hard` 먼저 금지.
원문: [RULES.md](RULES.md) 「ops 체크아웃 단방향 규칙」 · 장부 `cyto-ops-checkout-oneway`

## 수리 반영 경로 규칙 (cha 지시, 2026-07-29)

- 수리 에이전트는 배포 체크아웃 커밋 금지. apply·commit·패치문서·회귀시나리오는 전용 작업 클론에서만. 샌드박스 `git clone --shared` 격리(`repair_ops_adapters.py`)는 그대로 둔다.
- 키는 repo 한정 write deploy key `/srv/autophagy-private/repair_push_key`(ops:600, `REPAIR_PUSH_KEY`로 변경)와 고정 `/srv/autophagy-private/repair_known_hosts`만. 둘 중 없으면 push 없이 exit 4, ops read-only 키 폴백·혼용·홈 보관·`accept-new` 금지. `ProtectHome` 유닛에서 홈 접근 실패는 던지지 말고 없는 설정으로 답한다.
- push 전 `public_gate range origin/main HEAD` 통과 필수, 금칙어 목록 사본은 노드 비공개 루트에 두고 `PUBLIC_GATE_DENYLIST`로 가리킨다. `orientpine/cytoplasm`에 `repair/t_<ticket>`로만 push, `main` 직접 push·자동 ff-머지 금지.
- push 직후 반드시 `gh pr create --base main --head repair/t_<ticket>`(제목·본문 커밋 스타일, 티켓 id·요약·증적·공개 적합성, 마스킹). 열린 PR은 재사용, `gh` 불가면 설치·인증, 그래도 안 되면 cha에게 알림. 머지는 cha만, 반영 확인 뒤 kanban 완료.
- 승인은 패치에 바인딩(`action_hash`): 승인 뒤 `patch.diff`가 바뀌면 적용하지 않고, 불일치는 False가 아닌 예외로. 패치 본문은 Discord에 싣지 않고 `/srv/autophagy-private/` 경로만. 렌더러 v1 동결(게시본은 재렌더와 정확히 일치, 문구 변경은 v3).
- 재구축 순서: apply 대상 작업클론 이관→systemd `ReadWritePaths`에서 `/srv/autophagy-agents` 제거→브랜치 push→커밋 거부 훅. `.omo/plans/repair-report-{core,rollout}.md`가 막은 `repair_ops_core.py`·`automation/interop/report.py`·`automation/repair/systemd/*`·승인 흐름 일부는 그 계획과 조율 전 변경 금지.
원문: [RULES.md](RULES.md) 「수리 반영 경로 규칙」 · 장부 `cyto-repair-push-path`

## 세션 워크트리 규칙 (cha 지시, 2026-08-03)

- 세션마다 `automation/worktree.sh start <이름> [--paths <손댈 경로>...]`로 워크트리를 만들어 그 안에서만 작업, 끝나면 `automation/worktree.sh finish <이름>`. 워킹트리 공유 금지. 예외: `configs/rag/*/.venv`가 필요한 작업은 메인 체크아웃에서.
- `start`가 fetch 실패·이름/브랜치 중복으로 거부하면 캐시 ref로 시작하지 않는다. `--paths`는 그 경로의 최근 커밋·메인 체크아웃 미커밋 변경을 보여준다.
- `finish`가 미커밋·미착지 커밋으로 제거를 거부하면 착지 뒤 정리.
- 머지된 브랜치 재push 금지(새 작업=새 브랜치). 원격 브랜치 자동 삭제 금지(`tests/unit/test_session_worktree.py`).
원문: [RULES.md](RULES.md) 「세션 워크트리 규칙」 · 장부 `cyto-session-worktree`

## PR 전 검증 규칙 (cha 지시, 2026-08-25)

- 브랜치 push 는 그 tree 의 로컬 CI 영수증이 있어야 나간다(`automation/hooks/pre-push`). 순서: 커밋 → `automation/local_ci.sh run`(전 단계 통과 시에만 영수증) → push. 내용·`.github/workflows/ci.yml` 이 바뀌면 무효, 더러운 트리엔 미발급(`.omo/senpi-task/` 만 예외).
- 대상은 main 이 아닌 `refs/heads/*` 만, 태그 제외. main 은 `automation/land.sh` 로만, 직접 push 거부.
- 로컬 세트는 `tests/unit/test_local_ci_push_gate.py` 가 ci.yml `run:` 과 대조한다. 로컬에서 실행하면 안 되는 명령(예: 개발자 환경을 바꾸는 `pip install -r requirements-dev.txt`)은 로컬 실행에서 빼고 `tests/unit/test_local_ci_push_gate.py`의 `_LOCAL_ONLY`에 사유와 등록(스크립트 주석으로 대체 금지).
- ci.yml 에 main push 트리거를 되살리지 않고 `pull_request` 실행은 유지.
- 탈출구는 `LOCAL_CI_ALLOW_UNVERIFIED=1` 하나, 샌드박스/실험 전용, 상습 사용 금지.
원문: [RULES.md](RULES.md) 「PR 전 검증 규칙」 · 장부 `cyto-push-local-ci-receipt`

## 머지 규칙 (cha 지시, 2026-08-26)

- PR 머지는 `automation/merge-pr.sh <pr>` 로만. `gh pr merge` 직접 호출 금지(`tests/unit/test_merge_pr_gate.py`).
- 조건: OPEN, base main, `.github/workflows/ci.yml` 잡 전부 보고·성공. 진행 중·0건·일부는 PENDING 으로 대기(기본 900초), 마감 넘으면 거부. `CONFLICTING` 거부.
- 외부 체크 실패는 항상 거부. 잡 전부 green 에 외부만 `MERGE_PR_EXTERNAL_GRACE_SECONDS`(기본 180초) 넘게 미정이면 gitleaks 스캔이 깨끗할 때만 머지.
- 탈출구는 `MERGE_PR_ALLOW_UNCHECKED=1` 하나, 체크가 아예 없을 때만. 실패 체크는 못 넘는다.
- 태그는 `automation/release.sh` 소관. 서버측 강제(GitHub Pro 브랜치 보호)를 켜려면 먼저 `land.sh` 를 PR 경유로 바꾼다.
- `merge-pr.sh`가 기다릴 잡 이름은 `.github/workflows/ci.yml`에서 파생하며 하드코딩하지 않는다.
원문: [RULES.md](RULES.md) 「머지 규칙」 · 장부 `cyto-merge-pr-wrapper`

## 커밋 전 diff 확인 규칙 (cha 지시, 2026-07-29)

- `git add` 전에 반드시 `git diff --stat`(필요시 `git diff`)로 확인: 변경 파일이 의도한 집합인가, +/- 방향이 맞는가(추가인데 순삭제면 즉시 멈춤), 문서·규칙 파일은 삭제 줄을 직접 읽는다, `version:` 이 낮아지면 거의 항상 되돌림이니 배포본과 대조.
- 설명되지 않는 삭제나 내가 만들지 않은 변경이 섞이면 그대로 커밋 금지. 되돌림으로 판정되면 `git show origin/main:<path>` 로 대조해 정본 복원(`git checkout -- <path>` 는 이 복원에만). 남의 생산물을 지우는 방향이면 지우지 말고 소유자에게 묻는다.
- push 전 `git diff --stat <직전 정상 커밋>..HEAD` 로 삭제가 0 이거나 의도한 것뿐인지 다시 확인.
원문: [RULES.md](RULES.md) 「커밋 전 diff 확인 규칙」 · 장부 `cyto-pre-commit-diff-check`

## 공개 릴리스 규칙 (cha 지시, 2026-08-15)

- 정본은 공개 `orientpine/cytoplasm` main. 브랜치 → PR → 필수 체크(verify·clean-host-install·leak-guard) green → `automation/merge-pr.sh` 로만 들어간다. main 손 push 금지. 운영 자료(계획·QA 증적·금칙어)는 `orientpine/autophagy-ops` 에만, 공개 추적 금지.
- 커밋이 곧 공개다(`automation/public_gate.py`, 금지 경로 `.omo`·`docs/qa`). 실명·과제명·내부 호스트·Discord id·비공개 경로는 코드·문서·커밋·PR 본문 어디에도 금지.
- 릴리스: `automation/local_ci.sh run` → `automation/release.sh --notes-file <노트>`(승인 요청 전 초안 필수: 첫 줄 `# <한 줄 요약>`, 아래 한국어로 바뀐 것·사용자에게 보이는 변화·깨지는 변경/운영자 할 일) → ✅ → PR 로 들어온 그 main 커밋에 서명 태그(반출 스냅샷 없음) + GitHub Release + `update-trust.pub`·지문. 하나라도 없으면 미완. 게시 실패(exit 11)는 재실행으로 잇고, 직전 태그에 Release 가 없으면 `automation/release-note.sh <tag> --notes-file <노트>` 로 보충.
- 서명키 둘 혼동 금지: 업데이트 신뢰키(`update-trust@autophagy`, 유지보수자) vs 그룹 스킬 서명키(`publisher-<slug>@autophagy`, 그룹 관리자). 개인키는 git 커밋·노드 배포 금지, 노드엔 공개키만.
- 롤백 불가: revert PR 후 더 높은 버전을 컷. 절차 정본 `docs/guide/manual-maintainer.md`, 여기 복사 금지.
원문: [RULES.md](RULES.md) 「공개 릴리스 규칙」 · 장부 `cyto-public-first-release`

## 개인화 코드 금지 규칙 (SC-2, 2026-08-30)

- 개인 맞춤값(채널·메시지 id 상수, 과제명·기관명, 개인 경로·이름)은 코드에 하드코딩 금지. 자리는 `configs/node.example.toml`·`configs/*.example`(파서 유효 자리표시자)와 런타임 override(`~/.hermes/node.toml`, `/etc/autophagy/…`, `/srv/autophagy-private/…`)뿐. 채널은 `approval_surface`/`approval_directory`·config 키로만 해석. 테스트의 실측 예시 값은 허용.
- 수리 PR 본문에 "채널 id·과제명·개인 경로 하드코딩 없음" 확인란을 반드시 싣는다(사람·자동화 공통).
- repair 유래 파일이 `configs/`·`docs/` 에 새로 생기면 `configs/public-export-review.txt` 검토 절차를 따르고 개인화 여부를 먼저 본다.
원문: [RULES.md](RULES.md) 「개인화 코드 금지 규칙」 · 장부 `cyto-no-personalized-code`

## 세 저장소 구분 규칙 (cha 지시, 2026-08-16)

- 코드는 공개 `orientpine/cytoplasm`(개발 정본+배포본, 공개 가능한 것만)에서만 개발·배포. `orientpine/ribosome`(private 그룹 스킬 채널: 발행 스킬·매니페스트·`refs/heads/roster`, 갱신은 `automation/managed_skills/publish_cli.py` 3단계 게이트)과 합치지 않는다. 운영 자료는 `orientpine/autophagy-ops`(private, 운영자 직접 커밋). `orientpine/autophagy-agents`는 archive 읽기 전용, 쓰지 않는다(롤백 결정 때만).
- 합칠 수 없음: ① 접근 통제 정반대(팀원 접근은 cytoplasm 공개 clone과 ribosome 팀원별 read-only deploy key(폐기=멤버 제거, D1.3)뿐, autophagy-ops·autophagy-agents에는 없음), ② 서명키·신뢰파일·검증코드 분리, 서로 대체 불가(`update_trust.py`/`UPDATE_ALLOWED_SIGNERS_PATH` vs `managed_sync/verify.py`, D8), ③ 코드=root 자동 수렴 vs 스킬=fetch→검증→격리만 자동, live 마운트는 본인 ✅(D3).
- `autophagy-ops`(`omo/`·`qa/`·`leak/`)는 `automation/ops_link.sh` 로 체크아웃의 `.omo`·`docs/qa` 에 링크될 뿐 cytoplasm 에 추적 안 됨. 공개 문서는 그 내용을 옮겨 적지 않는다. 코드는 ops 없이 동작해야 함(금칙어 미설정 시 `public_gate`는 경로·토폴로지만 검사).
- 그룹 판단: 각자 쓰면 불필요(cytoplasm 설치), 에이전트끼리 Discord 조율이면 roster 필수(repo 선택, `~/.hermes/roster.yaml` 수동 가능), 팀 공통 스킬이면 그룹 스킬 repo 필수.
- 어느 저장소인지 반드시 먼저 확정하고 답한다. 절차는 여기 복사하지 않는다: 릴리스 컷 `docs/guide/manual-maintainer.md`, 발행·roster `docs/guide/manual-group-admin.md`·`managed-skill-channel.md`, 설치 `docs/guide/install.md`.
원문: [RULES.md](RULES.md) 「세 저장소 구분 규칙」 · 장부 `cyto-three-repos`

## 용어 교정 위치 규칙 (cha 지시, 2026-09-05)

- 용어 교정은 전사본→산출 문서 단계에서만. 전사본 사후 치환 금지. 전사 프롬프트 힌트(인식 전)는 허용.
- 층: `autophagy/용어집.csv`→`autophagy/<문서 종류>/용어집.csv`→`.../<과제>/용어집.csv`, 깊은 층이 같은 낱말을 덮어쓴다. 폴더 정본 `drive_taxonomy.CATEGORIES`, Drive 외 `term_glossary.EXTRA_FOLDERS`.
- 엔진 `automation/term_correction.py`·`term_glossary.py`·`term_correction_log.py`만. 사본 금지.
- 새 스킬: ①렌더 전 해석 결과(요약·결정·액션·사람·장소)에 교정 ②원문 인용 구역(`### C. 원문 전사본`, `## 전문`, `-----원본 메시지-----` 아래)은 바이트 그대로 ③`term_correction_log.record(document=…,stage=…)` 어절만 ④Drive 조회는 `DRIVE_PUBLISH_ENABLED` 옵트인, 실패 시 노드 캐시. 교정 실패가 문서 생성을 막으면 안 됨, 모델 금지.
- `test_term_correction_conformance.py`: (a)사본 `automation/`밖 RED (b)전사본 경로 교정 호출 RED (c)미채택 `_EXEMPT` 사유 필수. 절차·형식·환경변수·실패 마커의 정본은 `docs/guide/용어-교정-규약.md`(여기 복사하지 않는다).
원문: [RULES.md](RULES.md) 「용어 교정 위치 규칙」 · 장부 `cyto-term-correction-stage`

## 산출물 출처 규칙 (cha 지시, 2026-08-26)

- 회의 파생 산출물은 반드시 완성 회의록에서만. 전사본은 출처가 아니다.
- 회의록: Drive `autophagy/회의록/<과제명>/<연도>/`(발행 `meeting_cli ingest --project <과제명>`). 산출물 전 반드시 읽는다(읽기는 승인 불요). `.md`는 `files export` 아닌 `files get --params {fileId, alt:media}`, `gws`는 cwd 밖 `--output` 거부라 목적지에서 실행.
- 로컬(`~/notes/meetings/*.md`) 정본 아님. `APPENDIX_HEADING` 아래=전사본. `meeting_minutes.finalized_view(text)` 필수(사본 금지).
- 상태 파일: `회의록/<과제>/action-items.csv`, `회의록/project-codes.csv`. 규약·진입점(`drive_outputs.publish_state_file`/`fetch_state_file`): `docs/guide/drive-publish.md`.
- `--project` 인제스트 카드는 `출처(정본): Drive 회의록/<과제명>/` 먼저, 로컬 사본 뒤. 과제명 모르면 지어내지 않고 기존 로컬 경로만.
원문: [RULES.md](RULES.md) 「산출물 출처 규칙」 · 장부 `cyto-output-from-minutes`

## 산출물은 Drive에 둔다 (cha 지시, 2026-08-26)

- cha 에게 내놓는 산출물(문서·표·보고서)은 로컬 경로에 남기지 않고 Drive 표준 트리에 발행. 경로는 `drive_outputs.py` 파사드만. 수동: `python3 -m automation.drive_publish_cli --kind <종류> --title <제목> [--project <과제>] <파일>`. `gws drive` 직접 호출·vendoring 금지(`test_drive_outputs_conformance.py`).
- 카테고리 `drive_taxonomy.CATEGORIES`만. 일반=`doctype`. 파생 산출물을 `meeting`에 금지.
- 과제가 있으면 `--project <과제명>`, 이때 산출물은 1개만(2개↑ depth 6 `TaxonomyError`).
- 워크스테이션 파일은 노드로 옮겨 발행(`gws`는 노드 `agent`만), `PYTHONPATH`에 `/srv/autophagy-agent-current`, 임시 사본 발행 직후 삭제.
- 발행했다고 말하기 전에 Drive 재조회: 대상 폴더 파일 정확히 1개 확인, 출력을 증적으로. 절차·경로·환경변수의 정본은 `docs/guide/drive-publish.md`(여기 복사하지 않는다).
원문: [RULES.md](RULES.md) 「산출물은 Drive에 둔다」 · 장부 `cyto-output-to-drive`
