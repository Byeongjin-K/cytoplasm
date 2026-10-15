# 플랫폼 운영자 매뉴얼 — 업스트림 릴리스를 컷한다 (W-M3)

**독자**: 업스트림 저장소의 소유자(유지보수자). 이 소프트웨어를 **만들어서 내보내는**
쪽이며, 자기 노드를 쓰는 사용자이기도 하지만 이 문서에서는 그 역할이 아니다.

**이 문서가 소유하는 것**: 저장소들의 관계, 릴리스 컷 절차, 업데이트 신뢰키의 보관과
회전, 나쁜 릴리스 대응.

**이 문서가 소유하지 **않는** 것** — 링크만 하고 복사하지 않는다. 같은 절차를 두 문서가
설명하면 반드시 한쪽이 낡는다.

| 주제 | 소유 문서 |
|---|---|
| 내 노드 운영(게이트웨이 재시동·운영 제약) | 개발 저장소 전용 `operations.md` |
| 장애 대응 절차 | 개발 저장소 전용 `incident-response.md` · `reboot-recovery.md` |
| 취약점 보고 접수 경로·응답 약속 | [`SECURITY.md`](../../SECURITY.md) |
| 버저닝 규칙·스키마 상승 기준·상태 마이그레이션 | [versioning-support.md](versioning-support.md) |
| 노드 설치 절차 | [install.md](install.md) |
| 그룹 관리자 / 팀원 역할 | [manual-group-admin.md](manual-group-admin.md) · [manual-member.md](manual-member.md) |

> 위 표의 `operations.md`·`incident-response.md`·`reboot-recovery.md`는 **개발 저장소
> 전용**이라 공개 저장소에는 없다. 공개 저장소에서 이 문서를 읽고 있다면 그 세 문서는 열리지 않는다.

---

## 0. 먼저 — 개발은 공개 저장소에서 한다 (2026-10-07 공개 우선 전환)

**개발 정본은 공개 `orientpine/cytoplasm` 의 main 이다. 변경은 브랜치 → PR → 필수 체크 →
`automation/merge-pr.sh` 머지로만 main 에 들어가고, 릴리스는 `automation/release.sh` 가 그
main 커밋에 서명 태그를 다는 것이다.**

이 절은 원래 반대 내용이었다. v1.0.0(2026-08-15) 직후 소유자가 *"이제 나는 공개 repo에서
작업을 이어가야 하는가, private repo는 불필요해지는가?"* 라고 물었고, 그때의 답은 "아니오 —
개발은 private 저장소에서만 하고 공개 저장소는 export 스크립트가 다시 만드는 파생물" 이었다.
2026-10-07 공개 우선 전환으로 그 답이 뒤집혔다. 그 날짜 이전의 문서·증적이 "private 에서
개발한다" 고 말하면 옛 흐름의 기록이다.

| | public `orientpine/cytoplasm` | private `orientpine/autophagy-ops` | private `orientpine/autophagy-agents` |
|---|---|---|---|
| 성격 | **개발 정본 + 배포본** | 비공개 운영 자료(계획·QA 증적·누출 금칙어) | 옛 개발 저장소 — 2026-10-10 archive(읽기 전용, 이력 보관) |
| 누가 쓰나 | 사람·에이전트·수리 자동화가 브랜치·PR | 운영자 | 아무도 |
| main 갱신 | PR + 필수 체크 verify·clean-host-install·leak-guard + `merge-pr.sh` (브랜치 보호, 관리자 포함) | 직접 커밋 | 쓸 수 없다(archive) |
| 배포 provenance 기준 | `origin/main` (「배포 provenance 규칙」) | 해당 없음 | 해당 없음 |
| 노드의 `origin` | **맞음** | 아님 | 아님 |

개발 체크아웃은 운영 저장소를 `.omo`·`docs/qa` 로 링크해 쓴다. `automation/ops_link.sh` 가
링크와 누출 게이트 훅 셋(pre-commit·commit-msg·pre-push)을 설치하고, 경로는
`git config autophagy.opsRepo` 에 둔다. 그 두 경로는 공개 저장소에 추적되지 않는다.

**커밋이 곧 공개다.** 커밋 메시지·PR 본문까지 그대로 공개되므로 실명·과제명·내부 호스트·
Discord id·비공개 경로 세부를 쓰지 않는다. `automation/public_gate.py` 가 훅·
`automation/local_ci.sh`·CI `leak-guard`·수리 push 직전에서 같은 검사를 돈다.

그리고 **그룹 스킬 채널이 따로 있다** — 그룹 관리자가 소유하는 관리형 스킬 채널(현재 `orientpine/ribosome`)다.
위 표에 그것이 없는 것은 빠뜨렸기 때문이 아니라, 이 문서가 다루는 **소프트웨어 배포 경로와
아무 관계가 없기** 때문이다. 서명키도 신뢰 파일도 검증 코드도 별개고
(`update_trust.py` vs `managed_sync/verify.py`), 설계상 그쪽 주인은 유지보수자가 아니어도 된다.
그쪽 절차는 [manual-group-admin.md](manual-group-admin.md)가 소유하며, 세 저장소의 구분은
루트 `AGENTS.md`의 「세 저장소 구분 규칙」이 한 표로 들고 있다.

### main 에 손으로 push하면

브랜치 보호가 서버에서 거부한다(관리자 포함). 그래서 옛 흐름의 위험 — 다음 export 가 손
변경을 덮고, 그 사이 공개 `main` 이 서명 태그 커밋에서 벗어나는 것 — 은 지금 흐름에서는
생기지 않는다. 태그와 태그 사이의 main 커밋은 서명이 없으므로 노드가 설치하지 않을 뿐이다.
노드는 게시된 서명 릴리스 태그 중 가장 최신의 것으로 수렴하므로, main 이 마지막 태그 뒤로
전진해도 멈추지 않고 다음 태그를 기다린다(루트 `AGENTS.md` 「릴리스 태그 규칙」).

---

## 1. 릴리스 절차 — 순서가 전부다

### 매 턴 자동 회상 제공자

`automation/memory_provider/deploy.sh`는 선언된 agent·peer 홈
`~/.hermes/plugins/autophagy_recall/__init__.py`만 배포한다. Hermes의 **메모리 제공자 로더**가
이 홈 디렉터리를 직접 검색하므로 일반 플러그인 `register()`나 `hermes plugins enable`을
사용하지 않는다. 워커는 불변 릴리스 트리에서 실행되고, 파일 배포만으로 기능이 켜지지는 않는다.

활성화는 릴리스의 기존 config 수렴 경로가 한다. `memory_curator/deploy-manifest.txt`의
agent·peer별 `.hermes/config.yaml` **기존 두 행**이
`hermes-memory-profile-with-recall-v1`을 선택하고, 소유 배포기가 `converge_config`를 호출한다.
`automation.memory_provider.config`는 `hermes_memory_config.LIMITS`의 상한과
`memory.provider: autophagy_recall`을 공용 블록 편집기로 한 번에 작성한다.
백업·원자 교체·read-back은 기존 상한 writer를 재사용하며, `activation=gateway`가
agent+peer 게이트웨이를 함께 재시동한다. 내장 USER.md·MEMORY.md는 수정하지 않는다.
다른 외부 제공자가 이미 선택돼 있으면 `external-provider-selected`(rc 7)로 거부한다.
운영자가 기존 선택을 정리하기 전에는 조용히 덮어쓰지 않는다.
중첩 provider·중복 키·지원하지 않는 메모리 매핑은 쓰기 전 rc 3으로 거부한다.

해제는 원하는 릴리스의 기존 config 두 행을 상한 전용 `hermes-memory-limits-v1`로 되돌리고,
`memory.provider` 키를 지운 뒤 두 게이트웨이를 함께 재시동하는 것이다.
제공자 파일은 남아 있어도 선택되지 않으면 실행되지 않는다. 선언을 그대로 둔 채 키만
지우면 다음 수렴이 다시 켜므로 선언과 설정을 함께 바꾼다.
`codex_app_server` 또는 비문자열 멀티모달 경로는 사용자 API 사본 주입을 우회하므로
활성화 전 실제 운영 API 경로를 확인한다.

**동일 목적지의 선언을 추가하지 않는다.** `(account, destination)` 유일성은 그대로이며
제공자 패키지는 홈 플러그인 파일 선언만 소유한다. config 관측 알고리즘 등록은
`automation/deploy_all_config.py`에 있고 기존 상한·`hermes-soul-recall-v1`도 유지한다.
`--show`·`--check`·`--apply`는 `python3 -m automation.memory_provider.config`의 계약이다.
재시동 후 실제 질문으로 회상을 확인하는 것은 릴리스 이후 운영 검증이다.

턴 대기 상한은 같은 프로필이 쓰는 `memory.recall_wait_seconds`(15, 형 Q173)다. 값을 바꾸려면
`automation/memory_provider/config.py` 의 `RECALL_WAIT_SECONDS` 를 고쳐 릴리스한다 — 노드에서 손으로 고치면 다음 수렴이 되돌린다.
제공자는 RAG 검색 서버의 `search_memory(per_note, exclude_notes)`와 `memory_core_notes`를 쓴다. 이 둘은
`configs/rag/mcp`에 있으므로 같은 릴리스 ✅ 아래 `automation/rag_stack`이 RAG 노드의 검색(mcp) 컨테이너만 다시
빌드해야 들어간다(health·인증 검색 확인, 실패 시 이전 이미지로 되돌림, 임베딩은 그대로). 서버가 예전 판이면
제공자는 핵심 블록을 비우고 예전 검색으로 돌아갈 뿐 턴을 막지 않는다.

[기능 소개와 설치본 계약·제한](../기능소개/매-턴-자동-회상.md)

### 1.0 공개 우선 릴리스 — 정상 경로 (2026-10-07~)

1. 변경이 PR 로 cytoplasm main 에 머지됐다(필수 체크 셋 green, `automation/merge-pr.sh`).
2. 서명키를 가진 워크스테이션의 메인 체크아웃에서 `automation/local_ci.sh run` 으로 그 트리의
   영수증을 만든다(루트 `AGENTS.md` 「릴리스 태그 규칙」의 워크스테이션 전제).
3. `automation/release.sh [--bump …] --notes-file <노트>` — 승인 카드가 올라가고, 소유자 ✅ 뒤 그 main 커밋에
   update-trust 서명 태그가 잘린다. 노드는 2분 리컨실러로 그 태그에 수렴하고
   `deploy_all.sh --apply` 까지 이어진다.
4. 노트는 3의 같은 실행이 게시한다 — `release.sh --notes-file <노트>` 로 초안을 승인 요청 **전에**
   준다(§1.7). 태그 직후 GitHub Release(초안 + PR 목록 + 신뢰키 지문)와 `update-trust.pub` 자산이
   생기고, 실패하면 exit 11 이다. 초안 없이는 승인 요청도 올라가지 않는다.
5. §1.8 로 확인한다.

반출 스냅샷·fresh history·공개 원장 기록은 이 경로에 없다.

### 헬스체크 허용 목록도 같은 ✅ 로 (2026-10-14~)

헬스체크 전용 ssh 키의 강제명령 래퍼(허용 목록, 운영자 홈의
`~/.local/libexec/autophagy-healthcheck-probe`)는 릴리스 트리 밖 파일이라 수렴이 올리지 못한다.
예전에는 체크가 바뀐 릴리스마다 영수증의 「남은 소유자 조치」로 남아 사람이 노드에서 `--install` 을 돌렸다.
이제 `release.sh` 가 한다.

1. **승인 전** — `automation/healthcheck_wrapper_release.sh plan <HEAD>` 가 새 트리를 주 노드의 전용 빈
   임시 디렉터리에 **파일로** 보내 풀고(셸 stdin 에 데이터를 싣지 않는다), 노드마다 그 릴리스가
   기대할 `# wrapper-inputs:` 지문을 계산해 설치본과 대조한다. 다른 노드는
   `healthcheck-wrapper:<node>` 표면으로 승인 해시에 들어가고, 카드에 한 줄이 붙는다:
   `헬스체크 허용 목록도 갱신: <primary-node>, <rag-node>`. 다른 노드가 없으면 줄도 없고 카드는
   예전 바이트 그대로다. 지문을 판정하지 못하면(노드 불통) `WRAPPER-PLAN-UNKNOWN` 을 남기고 줄 없이
   진행한다 — 그 릴리스는 래퍼를 설치하지 않는다.
2. **✅ 뒤** — `deploy_all.sh --apply` 가 영수증을 쓰기 직전에 `refresh` 를 돌린다. 노드 릴리스 트리로
   기대 지문을 다시 계산하고, 설치본과 다르면 `release_approval authorize-wrapper` 로 그 지문이 지금 배포된
   릴리스의 승인된 카드에 있었는지 묻는다(skill 의 `--release-approval` 과 같은 판정). 인가되면 운영자
   계정으로 설치한다 — 주 노드는 릴리스 트리의 `--install`, 릴리스 트리가 없는 노드는 주 노드의
   `--print <node>` 출력을 `provision-healthcheck-probe.sh --from-file` 로. 설치 뒤 헤더 지문을 다시 읽어
   대조한다. sudo 도 authorized_keys 도 건드리지 않는다.
3. **결과** — 영수증이 그 자리에서 위임 프로브를 다시 돌리므로 설치가 되면 `pending_owner_actions` 에서
   빠진다. 실패·보류는 영수증 `wrapper_refresh` 에 남고 적용 통지의 「남은 소유자 조치」에
   `헬스체크 허용 목록 갱신 실패 — <node>: <사유>`(설치 뒤 지문 불일치 포함) 또는 `… 보류 —`
   (카드가 그 지문을 싣지 않았음·⛔·승인 확인 불가) 줄로 온다. 실패해도 영수증은 쓰인다.

손으로 다시 설치하는 길(`bash /srv/autophagy-agent-current/automation/healthcheck_probe_wrapper.sh --install <node>`)은
그대로다 — 카드가 갱신을 싣지 못한 릴리스의 남은 조치는 다음 릴리스 카드가 다시 싣거나 그 명령으로 닫는다.
[기능 소개](../기능소개/릴리스-카드-헬스체크-허용목록-갱신.md)

### RAG 검색 컨테이너도 같은 승인으로 갱신한다

RAG 노드가 설정된 설치에서는 승인 전에 기존 `rag_stack_current`의 `rag|tree` 선택으로
소스와 실행 중 MCP 이미지를 릴리스와 대조한다. stale일 때만 `rag-mcp` 트리 지문을 승인 해시에
추가하고 카드에 `검색 잠깐 끊김(~1분)·되돌림 있음` 한 줄을 표시한다. 판독 불가면
`RAG-MCP-PLAN-UNKNOWN` 경고를 남기고 해당 갱신만 카드에서 빼고 릴리스를 진행한다.
미상을 최신이라고 추측하거나 승인에 없는 RAG 갱신을 실행하지 않는다. 수렴 뒤에도 판정할 수
없으면 영수증과 적용 통지의 남은 소유자 조치에 남는다.

소유자 승인 뒤 `deploy_all.sh --apply`가 source sync와 MCP 전용
`docker compose up -d --build --no-deps mcp`를 ops로 실행한다. 노드의 비공개 설정을 보존하고,
`/health`와 인증된 `search_memory` 1회가 통과해야 성공이다. 실패하면 기록한 이전 이미지로
`--no-build --pull never --no-deps mcp`를 실행한 뒤 이미지·health·검색을 다시 확인한다.
embedding과 Qdrant는 재빌드하거나 재기동하지 않는다. embedding 변경 창은 계속 별도 보류다.
배포기는 provenance 검사 전에 `deploy_runtime.sh`의 고정 실행 함수와 관측 헬퍼를 로드한다.
검사는 실행 함수의 소스까지 포함하며, 통과 뒤에만 그 함수를 호출한다.

릴리스 호스트가 RAG 노드 자신이면(`rag_node_name == hostname -s`) 관측과 갱신 모두
자기 자신에게 SSH하지 않고 로컬 `sudo -n -u ops -H`로 실행한다. 파일은 ops가 읽을 수 있는
전용 임시 폴더에서 ops 소유 실행 폴더로 복사하고, 끝나면 둘 다 정리한다. 별도 RAG 노드는
기존 SSH/SCP 경로다. 두 경로의 노드 판정 override는 `RAG_STACK_LOCAL_NODE` 하나다.

영수증의 `rag_mcp_refresh`에 status와 전후 이미지 ID가 남는다. 실패·승인 불일치는
`pending_owner_actions`와 적용 통지에 남으며, 성공은 최신 프로브로 재확인하므로 RAG 갱신을
위한 별도 노드 활성화 명령이 필요 없다.
[기능 소개](../기능소개/릴리스-RAG-mcp-자동갱신.md)

### Hermes 설정 값도 릴리스가 맞춘다 (2026-10-14~)

계정 `~/.hermes/config.yaml` 의 키 가운데 저장소가 값을 정하는 것은 배포 선언 `v2:config;algorithm=<이름>` 으로
선언한다(첫 사례: 메모리 상한 `hermes-memory-limits-v1` — `automation/memory_curator/deploy-manifest.txt`).
`deploy_all.sh` 가 계정마다 그 키를 관측해 다르면 소유 배포기를 부르고, 배포기는 공용 헬퍼 `converge_config`
(`automation/deploy_config.sh`) 하나로만 그 파일을 쓴다 — 노드 릴리스 트리의 모듈이 계정 안에서 백업 → 원자적
교체 → 다시 읽기를 한다. 선언이 `activation=gateway` 면 바뀐 릴리스에서 agent+peer 를 함께 재시동한다.

- **값 바꾸기** — `automation/hermes_memory_config.py` 의 `LIMITS` 만 고쳐 PR → 릴리스. 큐레이터 `CAPS` 도 그 값을 읽는다.
  노드에서 손으로 고치면 다음 릴리스가 저장소 값으로 되돌린다.
- **내리기 주의** — 파일이 새 상한보다 긴 계정은 거부(rc 7)한다. 업스트림 결함 NousResearch/hermes-agent#133028:
  상한보다 큰 파일은 정리·삭제 쓰기까지 막혀 갇힌다. 먼저 파일을 줄인다.
- **판독 불가** — memory 블록 모양을 해석하지 못하면 관측이 `unknown` 이고 배포기를 부르지 않는다. 영수증이 막히므로
  노드에서 그 계정의 `config.yaml` 을 직접 본다.
[기능 소개](../기능소개/메모리-상한-올리기.md)

### 릴리스 트레인 (2026-09-03 정책)

릴리스는 매일 18:00 KST에 `automation/release.sh`로 한 묶음씩 컷한다. 즉시 컷하는 예외는 긴급 핫픽스뿐이며, 3일 릴리스 백로그 다이제스트 정책은 그대로 유지한다.

`automation/release.sh --bump {major,minor,patch}`의 기본값은 `patch`다. `base..head`에 `POLICY_VERSION`·`SCHEMA_VERSION` 상수, `node_config` 필수 필드, 인터롭 필수 키 변경이 있으면 `--bump patch`는 exit 4로 거부되며, `--bump major`는 승인 노트에 `MAJOR: 운영자 조치 필요` 줄을 추가한다.

**재실행은 재개다.** 태그가 잘린 뒤 전량 반영이 실패해(`release.sh` exit 10) 사람이 다시 돌리거나 완결 타이머가 이어받으면 `release_version_for`가 HEAD에 이미 붙은 태그를 그대로 쓴다 — 다음 버전을 새로 계산하지 않는다(2026-09-03 v1.1.1: next를 다시 계산해 v1.1.2를 요청하자 이름 불일치 검사가 자기 태그를 거부했다). 손 태그와 요청 버전이 다른 경우만 `tag at HEAD is … not requested …`로 거부된다. 세션의 `release.sh`와 완결 타이머가 같은 ✅를 보고 `deploy_all`을 동시에 돌리면 스킬별 실행 lock이 서로를 `EXECUTION-LOCK-BLOCK`으로 막아 둘 다 `incomplete`로 끝날 수 있다 — 마운트는 합집합으로 완료되고 `deploy_all.sh --verify`가 영수증을 쓰지만, 한 실행의 rc=10을 사고로 읽지 말 것(릴리스 단위 lock은 `docs/follow-ups.md`).

### 승인 뒤 main이 전진한 경우

태그 전에 `origin/main`이 전진하면 옛 ✅는 새 tip에 사용할 수 없다. 최신 clean tip과 로컬 CI 영수증을 갖춘 `automation/release.sh`는 시작 시 `retire --head <직전 릴리스 base> --tip <확인한 tip>`으로 APPROVED 레코드를 검사한다. 이전에 태그된 base의 승인은 기존 `release-history/`로, base도 현재 tip도 아닌 미실행 승인은 **감사형 abandon**으로 보관한 뒤 새 요청을 한 번 게시한다. 새 카드의 ✅를 기다리며, 옛 승인으로 새 tip을 태그하지 않는다. 취소·미확인 레코드는 이 승인 회수 분기의 대상이 아니다. 기존 bound_pending 자가 회수도 유지한다.

완결 타이머는 요청을 회수하거나 새로 게시하지 않는다. `decision --head <tip> --notify-stale`이 APPROVED+HEAD 불일치를 발견하면 `owner_notice`를 통해 설정된 소유자 통지 채널(미설정 시 DM)에 `release.sh` 재실행 안내를 남긴다. 노드 agent의 `<skill-gate>/release-stale-notified/`에 version·옛 HEAD·message_id의 지문별 성공 마커(0600)를 남겨, **tip이 계속 전진해도 같은 요청은 한 번만 통지**한다. 전송 실패는 마커 없이 다음 틱 재시도하며, 동시 발신은 리스로 직렬화한다. 발신 성공과 로컬 마커 기록 사이의 프로세스 중단까지 원격 exactly-once를 보장하는 것은 아니다.

수동 `release_approval_remote.sh abandon --version <v> --head <sha> --message-id <id> --reason <사유>`도 같은 감사 경계를 쓴다. A3는 **원 카드와 리액션 보존**이다: 결정 레코드를 바이트 그대로 보관하고, 원 승인 메시지를 참조하는 `⛔ 만료 — <사유>` 회신만 추가한다. 카드 본문은 승인 바인딩이므로 수정하지 않는다. 회신 실패는 `RELEASE-ABANDON-NOTICE-FAIL`로 기록할 뿐 abandon 종료코드나 감사 결과를 바꾸지 않고, 이미 회수된 요청을 다시 회수해도 회신을 중복 게시하지 않는다.

[기능 소개](../기능소개/릴리스-승인-자가-회수.md)

> 옛 반출 절차 §1.1~§1.6(`automation/public_export.sh`)은 롤백 기간(공개 우선 릴리스 3회
> 무사고)이 끝나 2026-10-10 에 지웠다. 옛 개발 저장소는 archive 되어 읽기 전용으로 남는다.

### 1.7 릴리스 노트 게시 (사이클의 마지막 단계 — 선택이 아니다)

**공개 우선 흐름(2026-10-09~)에서는 `release.sh` 가 이 절을 자동으로 한다.** 초안 형식:

```markdown
# <한 줄 요약 — Release 제목이 `vX.Y.Z — <요약>` 이 된다>

<한두 문장 개요>

## 바뀐 것
- **<무엇>** — <왜·어떻게>

## 사용자에게 보이는 변화
- …

## 깨지는 변경 · 운영자 할 일
- 없음 / MAJOR 면 거부되는 것·오류 문자열·조치
```

`release.sh --notes-file <초안>` 이 초안을 `~/.hermes/release-notes/<sha>.md` 에 두고, ✅ 뒤 태그를 자른
같은 단계에서 `ensure_release_note` 가 초안 아래에 `## 포함된 PR (<직전 태그> 이후)` 와
`## 업데이트 신뢰키`(지문)를 붙여 Release 를 만들고 `update-trust.pub` 를 첨부한 뒤 다시 읽어 확인한다.
가장 높은 버전만 Latest 로 표시한다. 실패 신호와 재개:

- 초안이 없거나 제목 줄·본문이 비면 승인 전에 exit 4(`no release note draft`·`RELEASE-NOTE-FAIL`).
- 게시 실패는 exit 11 `RELEASE-NOTE-FAIL` — 태그는 남고, 같은 명령을 다시 돌리면 노트만 이어서 게시한다.
- 직전 릴리스 태그에 Release 나 `update-trust.pub` 가 없으면 새 릴리스를 열지 않는다(exit 4
  `RELEASE-NOTE-MISSING`). 보충: `automation/release-note.sh <tag> --notes-file <초안>`(이미 있으면 무동작).

아래 손 절차는 자동 게시가 실패했을 때 손으로 보충하는 길이다(평소에는 `release-note.sh` 가 같은 일을 한다).
그렇다고 이것이 부록은 아니다 —
릴리스 객체와 거기 붙는 `update-trust.pub` 자산·지문 공지가 없으면 신규 설치는 시작조차
하지 못한다(루트 `AGENTS.md` 「공개 릴리스 규칙」의 종결 조건 셋 중 둘이 여기서 생긴다).

산문으로만 두었을 때 실제로 잊혔다 — 2026-09-09 v1.6.7은 태그만 올라가 목록의 Latest가
v1.6.1에 멈춰 있었다. 그래서 지금은 `release.sh` 가 태그와 같은 단계에서 게시한다.

**제목은 `vX.Y.Z` 하나로 두지 않는다.** 무엇이 바뀌었는지 한 줄이 제목에 있어야 목록에서
읽힌다(루트 「릴리스 패치노트 작성 규칙」과 같은 기준). 실측 반례: v1.6.9는 제목이 맨
`v1.6.9`라 목록만 보고는 그 릴리스가 무엇인지 알 수 없다. 형식은
`v1.6.8 — 공개 저장소 첫 화면 개편 · 반출 사이클에 릴리스 노트 게시 포함`처럼 쓴다.

```bash
gh release create v1.0.1 --repo orientpine/cytoplasm \
    --title 'v1.0.1' --notes-file <노트 파일>

# 신뢰키 공개키를 asset 으로 붙인다. 붙이지 않으면 install.md §1·manual-member.md §1 의
# "릴리스에 동봉" 이 거짓이 되고, `--update-trust-key` 가 필수 인자라 신규 설치가 시작조차
# 하지 못한다(2026-09-07 제3자 설치 실측). 공개키이므로 공개 저장소에 올려도 무해하다.
# 올리는 것은 반드시 `.pub` 이다. 개인키 경로를 그대로 넣으면 개인키가 공개 asset 이 된다.
# 업로드 전에 첫 낱말이 `ssh-ed25519` 인지 본다.
head -c 11 ~/.ssh/autophagy_update_trust.pub; echo   # → ssh-ed25519

# asset 이름은 올리는 **파일 이름 그대로**가 된다. `gh release upload <파일>#<이름>` 의 `#` 뒤는
# 라벨일 뿐이라 이름을 바꾸지 못한다 — 2026-09-08 v1.6.0 실측에서 asset 이
# `autophagy_update_trust.pub` 로 올라가 install.md §1·manual-member.md §1 이 안내하는
# `<bundle>/update-trust.pub` 와 어긋났다. 문서가 약속한 이름으로 **복사한 뒤** 올린다.
cp ~/.ssh/autophagy_update_trust.pub /tmp/update-trust.pub
gh release upload v1.0.1 --repo orientpine/cytoplasm /tmp/update-trust.pub
rm -f /tmp/update-trust.pub
```

`gh release view v1.0.1 --repo orientpine/cytoplasm --json assets` 로 `update-trust.pub`
가 실제로 붙었는지 확인한다. 지문 대조는 그대로 대역외로 한다 — asset 은 키를 **전달**할
뿐 그 키가 옳다고 증명하지 않는다.

노트에 **반드시** 들어가야 하는 것:

- 업데이트 신뢰키 **지문**(현재 `SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA`).
  신규 설치는 설치기 번들의 키를 **설치기가 아닌 경로로** 대조해야 하는데, 그 대역외
  경로가 바로 README와 이 릴리스 노트다(P0-6 부트스트랩).
- 이 릴리스가 MAJOR라면 무엇이 거부되는지·어떤 오류 문자열이 보이는지·운영자가 무엇을
  하면 되는지(→ [versioning-support.md](versioning-support.md) §2).
- 되돌림 릴리스라면 무엇을 되돌렸고 어떤 버전이 영향을 받았는지(→ §4).

### 1.8 컷 이후 확인

```bash
# 공개 저장소에서 태그가 실제로 검증되는가 (신뢰키 파일이 설치된 노드에서)
git -c gpg.format=ssh \
    -c gpg.ssh.allowedSignersFile=/etc/autophagy/update-allowed-signers \
    verify-tag v1.0.1

# 원격 상태
git ls-remote https://github.com/orientpine/cytoplasm.git refs/heads/main 'refs/tags/v1.0.1*'

# 내 노드가 전진했는가 (2분 이내)
readlink /srv/autophagy-agent-current
```

`ls-remote`에서 태그가 `refs/tags/v1.0.1`과 `refs/tags/v1.0.1^{}` 두 줄로 보이면
annotated 태그가 맞다. `^{}` 줄이 없으면 lightweight 태그이고, 그것은 후보조차 되지
않는다.

**외부효과 규칙을 더한 릴리스는 규칙 수도 확인한다.** `configs/external-effect-tools.yaml` 의
규칙은 릴리스의 `deploy_all --apply` 가 인터롭 묶음으로 agent·peer 홈 사본에 나른다. 그 묶음이
roster 보류(`INTEROP-DEPLOY-HELD`)로 멈추면 새 규칙이 적용되지 않은 채 남는다. 2026-10-14 에는 Drive
사람별 공유 규칙 `gws_drive_permission_grant` 가 더해져 규칙이 9개다. 소유자 지시는
"오토파지에 \"드라이브 업로드·공유\" 승인 경로를 새로 만들고 그걸로 보냄 (몇 시간, 다음에도 씀)"
이었다([소개](../기능소개/메일-Drive-공유-링크.md)).

```bash
# 두 계정의 규칙 사본이 릴리스 트리와 같은가 (노드에서)
sha256sum /srv/autophagy-agent-current/configs/external-effect-tools.yaml
sudo -n -u agent -H sh -c 'sha256sum ~/.hermes/interop/external-effect-tools.yaml'
sudo -n -u agent -H sh -c "grep -c '^  - id:' ~/.hermes/interop/external-effect-tools.yaml"
```

---

## 2. 업데이트 신뢰키 — 보관

### 2.1 이 키가 무엇이고, 무엇이 **아닌가**

**서명키가 둘이다. 혼동하면 안 된다**(D8). 소유자도, 파일도, 검증자도 다르다.

| | 업데이트 신뢰키 | 그룹 스킬 서명키 |
|---|---|---|
| 무엇에 서명하나 | **공개 저장소의 릴리스 태그** | 관리형 스킬 태그·roster 스냅샷 |
| principal | `update-trust@autophagy` | `publisher-<slug>@autophagy` |
| namespace | `git` | `git,autophagy-roster` |
| 노드의 신뢰 파일 | `/etc/autophagy/update-allowed-signers` | `/etc/autophagy/managed-skills-allowed-signers` |
| 검증하는 코드 | `automation/update_trust.py` | `automation/managed_sync/verify.py` |
| 개인키 소유자 | **업스트림 유지보수자 (나)** | 각 그룹 관리자 |
| 이 문서 | 소유한다 | [manual-group-admin.md](manual-group-admin.md)가 소유한다 |

설치 계획 코드가 두 파일을 섞는 것을 거부한다 —
`WRONG-FILE: 업데이트 신뢰키와 그룹 스킬 서명키는 서로 다른 파일에 설치해야 한다(D8)`.

### 2.2 키 생성과 지문

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/autophagy_update_trust -C "update-trust@autophagy"
ssh-keygen -lf ~/.ssh/autophagy_update_trust.pub
```

개인키 0600, 공개키 0644. 지문(`SHA256:...`)이 대역외 공지값이다. 설치기의
`python3 automation/install/trust_key_bootstrap.py fingerprint --key <pub>`도 같은 값을
낸다(같은 계산 — `sha256(base64-decoded key material)`).

지원 알고리즘은 `ssh-ed25519` · `sk-ssh-ed25519@openssh.com` · `ecdsa-sha2-nistp256` ·
`ssh-rsa`다. 그 밖은 `BUNDLED-KEY-ALGORITHM`으로 거부된다.

### 2.3 ⚠️ 개인키는 어떤 저장소에도 들어가지 않는다

**`~/.ssh/autophagy_update_trust`(개인키)를 public이든 private이든 **어떤 git
저장소에도 커밋하지 않는다.** 노드에도 배포하지 않는다. 노드에 놓이는 것은 언제나
공개키 한 줄뿐이다.**

이 원칙은 그룹 스킬 서명키에 대해 이미 확립된 것과 같다
([manual-group-admin.md §2.3](manual-group-admin.md) — "개인키는 관리자 워크스테이션
밖으로 나가지 않는다 — 노드에 배포 금지"). **다른 키에 같은 규율**을 적용하는 것이지,
같은 키를 두 번 말하는 것이 아니다.

왜 이 키가 특별한지 구체적으로.

- **이 키는 모든 설치의 유일한 신뢰 근원이다.** 내 노드도, 연합의 제3자 설치도, 새
  릴리스를 받을지 말지를 오직 이 키의 서명으로 판단한다. 그리고 수렴은 **root 권한**의
  헬퍼가 수행한다.
- **유출되면**, 공격자가 임의의 코드에 "정상" 서명을 붙일 수 있다. 노드는 서명이
  맞으므로 정상 릴리스로 받아들이고 실행한다. W-F1-C의 자동 롤백은 스모크 실패를
  되돌릴 뿐 **이미 실행된 악성 코드를 되돌리지 못한다.**
- **분실되면**, 앞으로 어떤 릴리스도 컷할 수 없다. 회전(§3)밖에 길이 없는데, 회전은
  이미 배포된 **모든** 노드의 `/etc/autophagy/update-allowed-signers`를 손대야 하고
  그 작업에는 자동 경로가 없다(§3.2).

그래서 **백업이 필수다.** 이 문서는 백업 방식을 규정하지 않는다 — 패스워드 매니저,
오프라인 매체, 봉인 보관 무엇이든 소유자가 통제하는 곳이면 된다. 규정하는 것은
불변식 하나뿐이다: **지금 이 워크스테이션이 통째로 사라져도 개인키는 살아남아야 한다.**
그리고 그 백업 사본도 저장소가 아니어야 한다.

발행 실무는 그룹 키와 같다: 패스프레이즈를 걸고 ssh-agent로 올린다. 에이전트 런타임
환경에서 릴리스를 컷하지 않는다.

---

## 3. 업데이트 신뢰키 — 회전

> **이 저장소는 아직 한 번도 회전한 적이 없다.** 아래는 랜딩된 코드의 실제 거동에서
> 역산한 절차이며, 첫 실수행 때 관측값으로 갱신한다.
>
> **첫 회전(2026-10-13, 진행 중)**: 릴리스 호스트를 옮기면서(루트 `AGENTS.md` 「릴리스 태그 규칙」) 키를 복사하지 않고 새 머신에서 새 키를 만들었다. 구 키 `SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA` → 신 키 `SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang`(ED25519, `update-trust@autophagy`). 신 키 첫 릴리스의 노트가 신 지문을 공지한다. 병기 기간에는 두 지문이 모두 유효하고, 6단계(구 키 줄 제거)는 노드가 신 키 릴리스로 전진한 것을 확인한 뒤에 한다.

### 3.1 불변식 — 이것 하나만 지키면 된다

**새 키로 서명한 릴리스를 컷하기 전에, 이미 배포된 모든 노드의
`/etc/autophagy/update-allowed-signers`에 새 지문이 들어가 있어야 한다.**

순서를 뒤집으면 어떻게 되는지 정확히 말한다. 노드는 새 태그를 후보로 올리고
(`main`과 같은 커밋이므로), `git verify-tag`가 실패해
`BAD-SIGNATURE: git returned <n>: ...`가 되거나, 키는 통과하지만 principal이 다르면
`WRONG-PRINCIPAL: verified principal is not update-trust@autophagy`가 된다.
`resolve_signed_update`는 그 후보를 버리고 다음 후보로 넘어가며, 후보가 다 떨어지면
마지막 오류를 올린다. 리컨실러는 그것을 잡아
`[deploy-reconcile] UPDATE-TRUST-BLOCK <오류> — skipping tick`을 찍고 **rc 0으로
정상 종료**한다.

즉 **아무도 알람을 받지 않는다.** 노드는 조용히 옛 릴리스에 머물고, 매 2분 같은
저널 줄만 쌓인다. 노드 소유자 쪽에서는 healthcheck의 signed-update probe가 기존 repair
티켓 경로로 사유를 올려 주지만, **유지보수자는 남의 노드를 관측할 수 없다.**

### 3.2 왜 자동화할 수 없나

`/etc/autophagy/*-allowed-signers`는 **root 소유이고 업데이트 채널이 쓰지 않는다.**
릴리스 수렴은 `/srv/autophagy-agent-releases/<sha>` 아래에 코드를 놓고 `current`
심링크를 옮길 뿐 `/etc`를 건드리지 않는다. 그리고 그것이 옳다 — 업데이트 채널이 자기
신뢰 근원을 갱신할 수 있다면, 그 채널을 한 번 장악한 자가 **영구히** 발행자가 된다.

닭-달걀이므로 **회전 공지는 대역외**다. 설치 부트스트랩에서 지문을 대역외로 대조하게
한 것과 정확히 같은 이유이며, 같은 이유로 그룹 채널·업데이트 채널로 지문을 보내면
안 된다.

### 3.3 전환기에 두 키를 병기할 수 있는가 → 있다

신뢰 파일 형식이 이미 여러 줄을 지원한다.

- `render_allowed_signers(entries, target)`는 `Sequence[SignerEntry]`를 받아 줄마다
  하나씩 렌더한다.
- `parse_allowed_signers(text)`는 주석·빈 줄을 건너뛰고 줄마다 엔트리를 만든다.
- 실제 검증은 `git verify-tag`의 `gpg.ssh.allowedSignersFile` 의미 그대로이므로
  **어느 한 줄이라도 맞으면 통과**한다.
- 설치 검증(`trust_key_bootstrap verify --expect-fingerprint`)도
  `any(fingerprints_match(...) for entry in entries)`이므로, 여러 엔트리 중 하나만
  기대 지문과 같으면 PASS다.

같은 principal의 서로 다른 키 두 줄도 정상이다 — 엔트리는 `(principal, namespaces, key)`
이고 principal은 `update-trust@autophagy`로 고정이기 때문이다.

`trust_key_bootstrap install`은 기본적으로 **단일 엔트리**로 파일 전체를 렌더해
교체하므로, 아무 옵션 없이 새 키를 넣으면 구 키가 사라진다 — 정확히 §3.1이 금지하는
상태다. 전환기에는 **`--add`를 붙인다**: 설치본을 읽어 기존 엔트리를 보존한 채 새 키를
덧붙이고, 같은 키를 다시 넣으면 줄이 늘지 않는다(멱등). 설치본이 파싱되지 않으면
`SIGNERS-EMPTY`로 멈춘다 — 어떤 릴리스를 진짜로 인정할지 정하는 파일을 추측으로
다시 쓰지 않는다. 손으로 파일을 조립할 필요는 없어졌다.

전환기 파일의 정확한 형태(헤더는 `UPDATE_TRUST_TARGET.header` 그대로):

```
# autophagy UPDATE TRUST key — verifies public-repo release tags (plan D8).
# NOT the group skill signing key (/etc/autophagy/managed-skills-allowed-signers).
# Installed by automation/install/trust_key_bootstrap.py; root:root 0644.
update-trust@autophagy namespaces="git" ssh-ed25519 AAAA...<구키> update-trust@autophagy
update-trust@autophagy namespaces="git" ssh-ed25519 AAAA...<신키> update-trust@autophagy
```

`root:root`, 모드 정확히 `0644`, 부모 디렉터리도 root 소유이고 group/other 쓰기 비트가
없어야 한다. 하나라도 어긋나면 `TRUST-KEY-WRONG-OWNER` · `TRUST-KEY-WRITABLE` ·
`TRUST-KEY-WRONG-MODE`로 FAIL이다. 파일이 아예 없으면
`TRUST-KEY-MISSING: <path>가 없어 새 릴리스 검증을 fail-closed 거부한다`.

### 3.4 절차 (순서 고정)

1. **새 키페어 생성** (워크스테이션에서만).
   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/autophagy_update_trust_2 -C "update-trust@autophagy"
   ssh-keygen -lf ~/.ssh/autophagy_update_trust_2.pub     # 새 지문 확보
   ```
2. **병기 파일을 만든다.** 노드마다 아래를 root로 실행한다 — 구키 줄은 보존된다.
   ```bash
   sudo python3 automation/install/trust_key_bootstrap.py install --add \
       --key <bundle>/update-trust-2.pub \
       --expect-fingerprint 'SHA256:<신키>'
   ```
   먼저 `--dry-run`을 붙여 렌더될 파일에 **두 줄이 다 들어있는지** 눈으로 확인한다.
3. **모든 배포된 노드에 병기 파일을 배포**하고, 노드마다 **두 지문 각각**으로 확인한다.
   ```bash
   python3 automation/install/trust_key_bootstrap.py verify --expect-fingerprint 'SHA256:<구키>'
   python3 automation/install/trust_key_bootstrap.py verify --expect-fingerprint 'SHA256:<신키>'
   ```
   정상이면 마지막 줄이 `--- TRUSTED: 2건 중 실패 0 / 경고 0`이고 rc 0이다. 지문이 하나도
   맞지 않으면 `[FAIL] trust-key.fingerprint: TRUST-KEY-FINGERPRINT-MISMATCH: 설치본 ... != 공지 ...`과
   `--- NOT-TRUSTED: ...`, rc 1이다. **둘 다 통과해야** 병기가 확인된 것이다 — 하나만
   확인하면 다른 하나가 지워진 것을 못 본다(검증은 `any(...)`라 한 줄만 맞아도 PASS다).
   제3자 설치는 **대역외 공지 + 각 소유자의 수동 실행**이다(§3.2).
4. **전 노드에서 3이 끝났음을 확인한다.** 여기가 유일한 게이트다. 확인되지 않은 노드가
   하나라도 있으면 그 노드는 5 이후 조용히 멈춘다.
5. **새 키로 다음 릴리스를 컷한다.** `release.sh` 의 서명키(`UPDATE_TRUST_SIGNING_KEY`)만 바꾼다.
   스크립트가 push **전에** 자기 공개키로 태그를 자체 검증하므로, 키를 잘못 지정했으면
   `release tag does not verify as update-trust@autophagy in namespace git`에서 멈춘다.
6. **노드들이 그 릴리스로 전진한 것을 확인한 뒤**(`readlink /srv/autophagy-agent-current`),
   구키 줄을 뺀 파일을 배포한다. 이 단계는 단일 엔트리 교체가 곧 원하는 동작이므로
   설치기 CLI를 그대로 쓸 수 있다.
   ```bash
   sudo python3 automation/install/trust_key_bootstrap.py install \
       --key ~/.ssh/autophagy_update_trust_2.pub \
       --expect-fingerprint 'SHA256:<신키>'
   ```
   `--dry-run`을 먼저 붙여 계획(경로·소유·모드·principal·지문·내용)을 눈으로 본다.
7. **구 개인키를 폐기한다.** 백업 사본까지 포함해서.

⚠️ 3을 건너뛰고 5를 먼저 하면 **그 순간 모든 노드가 조용히 멈추고, 업데이트 채널로는
고칠 수 없다** — 각 노드에 수동으로 들어가야 한다. 연합 규모에 비례해 비용이 커지는
유일한 실수다.

⚠️ 회전 중에도 **릴리스 버전은 앞으로만 간다**(§5.1의 floor). 회전이 다운그레이드 예외를
만들지 않는다.

### 3.5 유출로 인한 긴급 회전

순서를 줄일 수 없다는 사실이 여기서 가장 아프다. 공격자가 이미 서명 능력을 가졌다면,
**구키 줄을 각 노드에서 제거하는 것**만이 즉효 조치인데 그것도 수동이다. 절차는 §3.4와
같되 6을 앞당겨 — 병기 없이 **신키만 담은 파일로 곧장 교체**하고, 교체가 끝난 노드부터
새 릴리스를 받게 한다. 교체 전 노드는 그동안 전진하지 않는다(그것이 원하는 상태다).

그래서 §2.3의 보관 규율이 이 절차보다 중요하다. 회전은 사고를 수습하지 못하고, 사고를
겪은 뒤에 하는 일일 뿐이다.

---

## 4. push 후 사용자 노드에서 실제로 일어나는 일

타이머는 `automation/systemd/autophagy-deploy-reconcile.timer` —
`OnBootSec=2min`, `OnUnitActiveSec=2min`, 실행 주체는 `ops`.

한 틱(`automation/deploy_reconcile_cli.py::main`)은 이렇게 흐른다.

1. roster의 `update_channel`을 읽는다. 없으면(v1 기본값) 노드의 `origin`을 쓴다.
2. **대상 SHA 해석** — `require_signed_updates`면 `resolve_signed_update`:
   - `git ls-remote <remote> refs/heads/main refs/tags/*`
   - `parse_remote_release_refs`: **peeled commit이 `main`과 같은** annotated 태그만
     후보, 이름 역순 정렬. 후보 0이면 `UNSIGNED-HEAD`.
   - 후보마다: 임시 bare repo에 `--depth=1`로 그 태그만 fetch → 태그 객체 SHA 재대조
     (`TAG-RACE: release tag changed while verifying: <tag>`) →
     `git verify-tag`(allowedSignersFile=`/etc/autophagy/update-allowed-signers`) →
     principal exact match → peeled commit 재대조.
   - 성공 즉시 **롤백 방지 floor를 전진**시킨다(§4.1). 순서가 중요하다 — 검증되지 않은
     태그 이름이 floor를 움직일 수 있으면, 서명 없이 `v99.0.0`을 밀어 넣는 것만으로
     채널을 영구히 닫을 수 있다.
3. 해석 실패는 **드리프트가 아니다.** `UPDATE-TRUST-BLOCK ... — skipping tick`을 찍고
   rc 0으로 끝난다. 잠깐의 네트워크 장애로 사건을 만들지 않기 위한 선택이다.
4. 전진이 필요하면 `apply_release_update`(`automation/release_rollback.py`):
   - `current`가 예상 prior가 아니면 `LOCK_CONTENTION_RC` — 다른 수렴이 진행 중이다.
   - **converge** — root 헬퍼가 서명을 **독립적으로 다시 검증**한다(ops pre-gate만으로는
     검증 직후 mutable `main`이 바뀌는 TOCTOU가 남는다).
   - `current`가 목표 SHA로 실제 전환됐는지 확인.
   - **agent + peer 게이트웨이를 함께 재시작**한다(`autophagy-gateway-pair restart`,
     상한 120초). 한쪽만 재시동하지 않는다 — 개발 저장소 전용 `operations.md`의
     게이트웨이 재시동 규칙.
   - `automation/deploy-smoke.sh` 실행(상한 900초).
   - 성공 → 실패 지문 파일 삭제, rc 0.
5. 재시작 또는 스모크가 실패하면 **롤백 트랜잭션**:
   - 실패 지문을 `<private_root>/deploy-reconcile/failed-release.json`(0600)에 원자
     기록: `failed_sha` · `prior_sha` · `reason`(`gateway-restart` 또는 `deploy-smoke`) ·
     `phase`(`rollback-pending`/`failed`) · `notice_sent`.
   - `autophagy-install-release rollback --failed-sha <..> --sha <prior>`로 `current`
     원자 복귀.
   - **양 게이트웨이 재시작 + health 재확인** — 포인터만 되돌리면 새 릴리스로 기동된
     프로세스가 그대로 남는다.
   - 기존 owner notice 표면으로 **정확히 1회** 알림. 실제 문구:
     `업데이트 검증 실패로 이전 릴리스 자동 롤백을 시작했습니다.` + 실패 릴리스 · 복귀
     릴리스 · 실패 단계 · 게이트웨이 복구 여부.
   - 복구가 미완이면 `rollback-pending`으로 남아 **다음 틱이 이어서 시도**한다.
6. 같은 `failed_sha`는 다시 시도하지 않는다. **그 릴리스에 대해서만** 멈춘 것이며,
   **다른 SHA의 다음 릴리스가 오면 정상 진행**한다. 이것이 §5.2가 성립하는 이유다.

> **유지보수자가 보는 것은 아무것도 없다.** 이 알림은 그 설치의 소유자에게 간다.
> 연합에는 유지보수자가 남의 노드를 관측하거나 조작하는 경로가 없다 — 설계상 그렇다.
> 그래서 나쁜 릴리스에 대한 나의 조치는 "노드를 고친다"가 아니라 "빨리 다음 릴리스를
> 낸다"이다.

---

## 5. 나쁜 릴리스 대응

### 5.1 ⚠️ 옛 태그를 다시 올려 되돌릴 수 **없다**

`automation/update_trust_state.py`의 롤백 방지 floor 앵커는 root 소유
`/var/lib/autophagy/update-trust/release-floor.json`에 있다. 부모 디렉터리도 root 소유
0755이며, ops 사전 게이트는 읽고 비교만 하고 root 헬퍼만 서명 재검증 뒤 단조 전진시킨다 —
`tag` · `commit_sha` · `major.minor.patch` 삼중항.

`refuse_release_rollback`의 판정:

| 상황 | 결과 |
|---|---|
| 후보 ordering < floor ordering | `RELEASE-ROLLBACK: release <새> does not advance verified release <옛>` |
| ordering은 같은데 tag나 commit이 다름 | `RELEASE-ROLLBACK: release <새> reuses the version of <옛> at another commit` |
| tag·commit 모두 동일 | 통과 (같은 틱에서 두 검증 경로가 같은 릴리스를 해석하므로 필요하다) |

즉 **v1.0.5가 나빴다고 v1.0.4를 다시 공개 `main`에 올려도, 이미 v1.0.5를 검증한 노드는
거부한다.** 태그를 지우고 같은 번호를 다른 커밋에 다시 붙여도 두 번째 규칙에 걸린다.

이 floor는 채널별이 아니라 **설치 전체**에 하나다. private 채널에서 v2.0.0을 검증한
노드를 v1.0.0까지만 있는 공개 채널로 바꾸면, 두 채널의 서명이 모두 진짜여도
`RELEASE-ROLLBACK`으로 계속 거부한다. 런타임이 스모크 실패로 이전 커밋에 의도적으로
롤백돼도 floor 파일은 내려가지 않는다. 따라서 채널 전환 전에 대상 채널 버전이 현재
floor 이상인지 확인해야 하며, 잘못 전환한 뒤 태그 삭제나 런타임 롤백으로는 복구할 수
없다. 더 높은 버전을 컷해 앞으로 전진하는 것만이 복구 경로다.

**이 비대칭은 의도된 것이다.** floor는 서명 능력이 없는 공격자가 origin의 `main`을
과거의 (진짜로 서명됐던) 커밋으로 force-push해 알려진 취약 버전으로 "업그레이드"시키는
공격을 막는 장치다(감사 C1, TUF rollback / CWE-345). 유지보수자의 취소 편의를 위한
장치가 아니고, 그 둘을 동시에 만족시킬 수는 없다.

부수적으로: **pre-release 접미사는 순서를 만들지 않는다.** ordering은
`major.minor.patch` 삼중항만 본다. `v1.0.1-rc1`과 `v1.0.1`은 같은 삼중항이라 서로를
전진시키지 못하며, 이때의 복구는 다음 patch 번호를 컷하는 것이다.

### 5.2 그래서 실제로 하는 일 — **앞으로 나가서 되돌린다**

"내리는 것(un-ship)"은 언제나 "앞으로 내보내는 것"으로 한다. 뒤로 가는 경로는 없다.

1. **private에서 고친다.** 문제 커밋을 `git revert`하거나 수정 커밋을 만든다. 평소대로
   커밋 → 푸시 → `origin/main` 랜딩(「작업 종결 규칙」).
2. **버전을 올려 새 릴리스를 컷한다.** v1.0.5가 나빴으면 **v1.0.6**이다. v1.0.5를
   재사용하면 `destination tag already exists: v1.0.5`로 막히고, 설령 원격 태그를 지워도
   §5.1의 두 번째 규칙에 걸린다.
3. **릴리스 노트에 명시한다** — 무엇을 되돌렸는지, 어떤 버전 범위가 영향을 받는지,
   노드 소유자가 확인할 것이 있는지. 연합에는 유지보수자가 밀어 보낼 수 있는 알림
   채널이 없다. **공개 릴리스 노트가 유일한 방송 수단이다.**
4. **수렴을 기다린다.** v1.0.5로 이미 전진한 노드는 v1.0.6으로 전진한다. v1.0.5의
   스모크 실패로 **자동 롤백돼 v1.0.4에 머문 노드도** v1.0.6으로 전진한다 — 실패 지문은
   그 `failed_sha`만 막기 때문이다(§4-6).

### 5.3 순서

**롤백(노드가 자동으로 한다) → 원인 확정 → 상위 버전 릴리스 → 공지.**

- 공지를 수정보다 **먼저 하지 않는다.** 조율된 공개(coordinated disclosure)가 기본이며
  보고자가 있으면 공개 시점을 합의한다([`SECURITY.md`](../../SECURITY.md)).
- 원인 확정 전에 재시작·설정변경·키 재발급을 하지 않는다(개발 저장소 전용
  `incident-response.md`가 소유하는 내 노드 규율이며 이 문서가 다시 쓰지 않는다).

### 5.4 공개 저장소에서 태그를 지우는 것

**지우지 않는다.** 이미 검증한 노드의 floor는 로컬에 남아 있어 태그 삭제로 되돌아가지
않고, 삭제는 감사 추적만 잃는다. 관리형 스킬 채널이 "upstream 태그 삭제는 취소가
아니다"(decision 16)로 같은 결론을 이미 채택하고 있다. 회수는 삭제가 아니라 **상위
버전 발행**이다.

---

## 6. 보안 신고 접수·대응

접수 경로·응답 약속·심각도 기준·지원 범위는 [`SECURITY.md`](../../SECURITY.md)가
단독으로 소유한다. 여기서 더하는 것은 유지보수자 쪽 순서 한 줄뿐이다.

**접수 → 판정 → 수정은 §1의 릴리스 절차로 → 되돌림이 필요하면 §5로 → 공지는 마지막.**

기억할 점 둘:

- **지원 대상은 최신 릴리스 태그 하나뿐이고 백포트는 없다.** 그래서 보안 수정도 §5.2와
  같은 "앞으로 나가기"다. 옛 태그에 패치를 얹는 선택지는 존재하지 않는다.
- 신고자에게 진행 상황을 계속 공유하고, 목표(접수 확인 5영업일 / 초기 판정 10일)를
  못 지킬 상황이면 침묵하지 않고 알린다.

---

## 7. 스키마 버전을 올려야 할 때

판단 기준·마이그레이션 정책·운영자 대응 순서는
[versioning-support.md](versioning-support.md)가 단독으로 소유한다. 여기서 더하는 것은
릴리스를 컷하는 사람의 의무 하나다.

**스키마 상승이나 필수 설정 필드 추가는 MAJOR다.** "운영자 조치가 필요하면 MAJOR"가
기준인데, 그것이 MINOR로 조용히 들어오면 자동 업데이트가 노드를 멈춰 세우고 노드
소유자는 그것을 릴리스 번호로 예측할 수 없다.

**MAJOR 릴리스 노트에는 반드시 셋을 적는다**: ① 무엇이 거부되는가 ② 어떤 오류 문자열이
보이는가 ③ 운영자가 무엇을 하면 되는가. 노드 소유자가 가진 정보원은 릴리스 노트뿐이다.

---

## 8. 체크리스트

한 번의 릴리스를 한 화면으로:

- [ ] 변경이 PR 로 `orientpine/cytoplasm` main 에 머지됐다
      (필수 체크 verify·clean-host-install·leak-guard green, `automation/merge-pr.sh`)
- [ ] 키 보유 워크스테이션에 그 트리의 `automation/local_ci.sh run` 영수증이 있다
- [ ] `automation/release.sh` → 소유자 ✅ → 그 커밋에 서명 태그
- [ ] GitHub Release 노트 게시 — `release.sh --notes-file` 이 태그와 같은 단계에서 한다
      (**지문 재게시** + MAJOR면 조치 안내, 제목에 한 줄 요약)
- [ ] 그 릴리스에 `update-trust.pub` 자산이 붙었다 → `gh release view <version> --json assets`
- [ ] 공개 저장소에서 `git verify-tag <version>` 통과
- [ ] 내 노드 `readlink /srv/autophagy-agent-current`가 2분 내 전진
- [ ] main 에 손으로 push한 것이 없다 (브랜치 보호가 거부한다)

---

## 관련

- [`SECURITY.md`](../../SECURITY.md) — 취약점 보고·응답·지원 범위
- [versioning-support.md](versioning-support.md) — 버저닝 규칙·스키마·마이그레이션
- [install.md](install.md) — 설치 절차의 단일 진실 (지문 대조 포함)
- [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md) — 제3자 설치 전제
- [manual-group-admin.md](manual-group-admin.md) · [manual-member.md](manual-member.md)
- `operations.md` · `incident-response.md` · `reboot-recovery.md` — 내 노드 운영
  *(개발 저장소 전용 — 공개본에 포함되지 않는다)*
- 핵심 코드: `automation/release.sh` · `automation/public_gate.py` · `automation/ops_link.sh` ·
  `automation/update_trust.py` ·
  `automation/update_trust_state.py` · `automation/deploy_reconcile_cli.py` ·
  `automation/release_rollback.py` · `automation/install/trust_key_bootstrap.py` ·
  `automation/install/allowed_signers.py`
