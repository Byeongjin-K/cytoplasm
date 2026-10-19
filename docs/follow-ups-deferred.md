# 보류·인계 후속 과제

## 릴리스 수렴 사각지대 해소 후 남긴 것 (2026-10-01)

- **[소유자 결정] `daily-cost-report` 가 2026-10-01 실행까지 28회 연속 실패하고 있다** — 노드 cron 캡처(2026-10-01 05:01Z)의 2026-10-01 09:00:40 KST 실행이 code 1 로 끝났고 출력은 `cost-report error: 'COST_REPORT_SOFT_CAP'  (28 failures in a row)` 다(2026-09-30 실행도 rc 1 로 관측됐었다). 단서: `automation/cost-report/send_cost_report.py:211` 이 필수 환경변수 `COST_REPORT_SOFT_CAP` 을 읽는다. 이 계획에서는 노드 실행 환경을 읽지 않았으므로 근본 원인은 확인되지 않았다 → 기존 보류 항목 「일일 비용 보고 필수 임계값 미설정 (2026-09-19) — 소유자」(`docs/follow-ups-deferred.md`)의 소유자 조치대로, USD 경보 임계값을 정해 실제 cron 실행 환경에 전달하거나 이 보고의 사용 중단을 결정한다. **영향: 일일 지출 통지 중단(반복) · 심각도 중**.
  ↳ 폐기로 종결(2026-10-19): 소유자 "이것도 이제 없애라", "이거 아예 삭제해 litellm". 설정 복구 대신 코드·선언을 제거한다.

> [follow-ups.md](follow-ups.md) 에서 **이 저장소가 지금 손댈 수 없는** 항목을 옮겨 둔 곳이다.
> 버려진 것이 아니라 조건이 걸린 것이다 — 조건이 풀리면 해당 불릿을 `follow-ups.md` 로 되돌린다.
> 원 묶음 `##` 헤딩을 그대로 유지한다: 후속 과제 회계 가드(`tests/unit/test_features_board_conformance.py` A9)가
> 두 문서를 합쳐 읽어 **이동과 삭제를 구분**하기 때문이다. 헤딩이나 불릿 첫 줄을 고치면 그 가드가 삭제로 읽는다.
> 현황판 요약표([features.md](features.md))는 `follow-ups.md` 만 센다 — 여기 있는 것은 "지금 남은 일"이 아니기 때문이다.

> **2026-09-19 전수 재판정:** 기존 보류 불릿 337개와 이후 추가된 정적 수렴 헬퍼·비용 보고 2개를
> 각각 추적했다. `D001`~`D339`의 원문 위치·개별 근거·미결 조건은 비공개 감사 원장
> `.omo/evidence/kfx/deferred-dispositions.json`에 보존한다. 이번에 열린 원장에서 옮긴
> 22건은 `.omo/evidence/kfx/active-closeout.md`로 따로 정산한다.
> `RESOLVED-SOURCE`는 소스 수준 해소이며 배포 증거가 아니다. `HISTORY-UNVERIFIED`·
> `NODE-VERIFY`·`NEEDS-EVIDENCE`는 현재 증거의 공백을 명시한 것으로, 과거 기록이나
> 조건부 보류를 새 완료 주장으로 바꾸지 않는다.

| 분류 | 뜻 | 되돌아오는 조건 |
|---|---|---|
| OWNER | 소유자·노드에서만 닫힌다(승인 ✅, 라이브 확인, root 권한, 외부 계정) | 소유자가 그 실행을 마치면 |
| BLOCKED | 동결 파일·벤더 코드·외부 저장소에 막혀 있다 | freeze 해제 또는 업스트림 반영 시 |
| OBSERVE | 지금 조치하면 쓰이지 않을 추상을 얹는다 — 관측 후 판단 | 불릿이 적은 관측 조건이 실제로 성립하면 |
| 해소 | 이미 닫혔다. 회계 가드가 원문 보존을 요구해 남긴다 | 되돌아오지 않는다 |

# OWNER — 소유자·노드에서만 닫힌다

## 매 턴 자동 회상 활성화 전 확인 (2026-10-14)

- **[OWNER] 실제 게이트웨이의 회상 활성화·검색 지연은 아직 검증하지 않았다** — 릴리스 뒤 제공자 선택과 agent+peer 재시동, 재배치 사실 질의로 확인한다. 텍스트 API 주입이 없는 `codex_app_server`·비문자열 경로는 활성화 대상에서 제외한다. 영향: 해당 경로의 자동 회상 없음 · 심각도 중, 내장 메모리와 명시적 recall은 무영향.


## 제안서 품질 복구 뒤 노드 자격증명 (2026-09-28) — 소유자

- **[OWNER] 노드 agent 계정의 Codex CLI 가 한 번도 로그인되지 않아 제안서 그림 생성과 윤문이 401 로 실패한다** — `codex login status` 가 `Not logged in`(rc 1), `~/.codex/auth.json` 이 없다. Hermes 게이트웨이 OAuth(`~/.hermes/auth.json`)는 별개라 대화·`compose` 는 동작한다.
  ↳ **조치(OWNER)**: 노드에서 `sudo -u agent -H bash -lc 'codex login --device-auth'` 를 한 번 수행하고(codex 는 agent 의 `~/.local/bin` 에만 있어 로그인 셸이어야 찾는다 — 운영자 계정에 `snap install codex` 로 따로 설치할 필요는 없다), `proposal_cli.py preflight --stage refine`·`--stage images` 가 present 인지 확인한다. Hermes 의 토큰을 `~/.codex/auth.json` 으로 복사하지 않는다 — refresh token 회전으로 게이트웨이 인증이 깨질 수 있다.
  ↳ **영향**: 그림 없는(draft preview) 제안서만 가능하고 윤문이 빠진다 · 심각도 중. 코드는 이제 `host-unauthenticated`·`UNGENERATED-FIGURES` 로 이 상태를 숨기지 않는다.
- **[OWNER] 노드 Hermes 의 웹 검색 도구가 설정되지 않았다(Firecrawl 키 없음)** — `research` 단계의 새 공개 근거 수집이 막혀, 2026-09-28 실행은 이전 제안서의 SYNTHESIS 를 복사해 썼다.
  ↳ **조치(OWNER)**: `FIRECRAWL_API_KEY` 또는 자체 Firecrawl URL 을 설정하거나, 소유자가 SYNTHESIS 근거를 직접 준다.
  ↳ **영향**: 새 주제의 선행기술·동향 근거가 얇아진다 · 심각도 중.


## 일일 비용 보고 필수 임계값 미설정 (2026-09-19) — 소유자

> **폐기로 종결(2026-10-19)**: 소유자 "이것도 이제 없애라", "이거 아예 삭제해 litellm".
> 아래 원문은 회계 이력이며 임계값 설정·보고 복구를 요청하지 않는다([제거 소개](기능소개/litellm-지우기.md)).

- **[OWNER] 일일 비용 보고가 필수 경보 임계값 부재로 16회 연속 실패했다** — 실제 cron의 오류는 `COST_REPORT_SOFT_CAP`이며 agent 설정 파일에도 키가 없다.
  ↳ **조치(OWNER)**: USD 경보 임계값을 선택해 실제 cron 실행 환경에 전달하거나 이 비용 보고의 사용 중단을 결정한다. 현재 발신자는 설정 파일에서 이 키를 자가 로드하지 않으므로 파일에 추가한 것만으로 적용됐다고 보지 않는다. 값을 지어내거나 cron을 끄지 않았다.
  ↳ **영향**: 일일 지출 통지 누락 · 심각도 중. 이후 스냅샷 경로는 아직 검증하지 않았으므로 키 설정만으로 전체 복구를 약속하지 않는다.
  ↳ 재판정(2026-09-19, D339): OWNER-DECISION — 실제 목록과 키 존재 여부만 읽었으며 원장에 증거를 남겼다.
  ↳ 처리(2026-10-01): 2026-10-01 09:00 KST 실행이 같은 오류 문구(`cost-report error: 'COST_REPORT_SOFT_CAP'`)로 28회째 연속 실패했다. 조치는 그대로 소유자 몫이다. 출처: 릴리스 수렴 사각지대 계획 todo 27 의 노드 cron 읽기 전용 캡처(2026-10-01 05:01Z).



## 릴리스 스냅샷 가드 수정의 노드 반영 (2026-09-18) — 소유자

- **노드의 수렴 헬퍼는 root 정적 사본이라 저장소 수정이 저절로 닿지 않는다** — `automation/origin_snapshot.sh` 가
  fetch 뒤 `origin/main == expected_sha` 를 요구해, v1.9.0 이 승인·서명 태그까지 끝나고도 문서 PR 세 건 뒤에서
  `SNAPSHOT-BLOCK: remote main moved` 로 영영 수렴하지 못했다(2026-09-18 실측 — 2026-09-09 의
  `parse_remote_release_refs` 수정은 신뢰 계층만 풀었고 이 스냅샷 계층이 남아 있었다). 저장소에서는 조상 검사
  (`merge-base --is-ancestor expected origin/main`)로 고쳤고 회귀 두 건이 고정한다(뒤처진 pin 은 그 sha 로 실체화 ·
  사이드 브랜치 커밋은 여전히 차단). 그러나 노드는 `/usr/local/libexec/autophagy-converge.d/origin_snapshot.sh`
  (`provision-deploy-converge.sh` 가 root 로 설치한 사본)를 돌린다.
  ↳ **조치(OWNER)**: 이 수정이 들어간 릴리스가 main 에 착지한 뒤 노드에서 `sudo automation/provision-deploy-converge.sh`
  를 한 번 다시 돌린다. 그 전까지는 「태그 컷 뒤 수렴 완료까지 머지 동결」이 유일한 우회이고, `release_helper_probe`
  가 저장소와 설치 사본의 불일치를 healthcheck 에서 드러낸다. **영향: 릴리스가 조용히 수렴하지 못한다(잘못 배포되는
  방향은 아님) · 심각도 중**.
  ↳ **재판정(2026-09-19): OWNER 유지** — 읽기 전용 프로브는 PASS였지만 비교 대상 릴리스도
  `b8333c5c6`이었다. 설치 사본의 SHA-256 `e83109ac…`는 그 구버전 소스와 정확히 같고,
  수정된 저장소 소스는 `920cc2f5…`이므로 재설치 완료로 볼 수 없다. 소유자 재프로비저닝 전에는
  위 머지 동결 우회를 유지한다. 프로브·지문 조회만 했으며 root 자산은 수정하지 않았다.

## 2026-09-07 기관메일 동시 발송 수리 잔여

- **[OWNER] 노드 `/tmp` 에 비정상 종료가 남긴 agent-browser 크롬 프로필 5개(약 38 MB, 2026-08-27~09-04)가 있다 → 노드에서 소유자·ops 가 지운다.**
  정상 종료는 `close` 가 프로필을 지운다(2026-09-07 실측: 세션을 열고 닫으면 개수가 제자리로 돌아왔다) — 남은 것은 timeout-kill 된
  실행의 흔적이다. 요청별 세션으로 바뀌어도 실행당 프로필 1개라 누적 속도는 예전과 같고, 자격증명·쿠키는 세션 종료와 함께 사라지므로
  영향은 디스크 38 MB 뿐이다. 코드가 남의 프로필을 지우면 **돌고 있는 실행을 깨뜨리므로** 자동 청소는 넣지 않았다. 심각도 낮음.

  ↳ 재판정(2026-09-19, D001): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 2026-09-04 plaud 구간 전사 수리 (t_4e3d6630) 잔여

- **사고로 게시된 빈 노트는 아직 vault 에 그대로다** — `000_PARA/Area/Lifelog/2026/2026-09-04-180427--7df8fc0f016b.md`
  는 요약·전문이 없는 649 B 노트다(정상 노트는 62 KB). 코드 결함은 이 수리로 닫혔지만 **이미 쓰인 노트는 스스로 고쳐지지
  않는다** → 수정본이 릴리스·마운트된 뒤 그 레코드를 `transcribing` 으로 되돌리면 파이프라인이 다시 전사해 승인 카드를
  새로 올린다. 상태 되돌림은 프로덕션 상태 쓰기라 소유자/노드 작업이며 이 브랜치에서는 하지 않았다. 영향: 라이프로그 1건,
  심각도 낮음.
  ↳ 처리(2026-09-05): 실동작 검증 세션에서 그 레코드를 노드 `state.json` 에서 `transcribing` 으로 되돌렸다(watch.lock 아래, 원본은 `~/.hermes/plaud-sync/backup-20260905-121833/`). 틱 1회로 로컬 전사(46 KB · 블록 529 · 격리 0)가 끝나 노트가 649 B → 52 KB 로 재조립되고 같은 경로(`…7df8fc0f016b.md`)의 승인 카드가 `posted` 됐다 — 소유자 ✅ 가 upsert 로 빈 노트를 덮는다(OWNER 잔여). 재발견이 막혀 있던 원인(클라우드-빈 녹음 skip)은 `9292d680c` 로 닫았다.

  ↳ 재판정(2026-09-19, D002): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 2026-09-05 수리 스윕-4 후 소유자 항목

- **[OWNER] 노드 agent 계정의 `~/.hermes/config.yaml` 에 `fallback_providers` 의 `custom:litellm/glm-main` 잔여 줄이 남아 있다 → 소유자가 그 계정에서 해당 줄을 지운다.** 자동화 경로는 `automation/codex_llm.py` 가 `--ignore-user-config` 로 돌아 무영향이고, 잔액·라우팅이 어긋난 별칭으로 품질이 떨어질 수 있는 것은 대화형 세션뿐이다(심각도 낮음). [해소 2026-09-06: 소유자 지시("follow-up 후속 처리")로 세션이 대신 처리 — agent 계정 `config.yaml` 을 `config.yaml.bak-20260906-052524` 로 백업한 뒤 `fallback_providers: []` 로 바꾸고(YAML 파싱 확인, 기본 모델 `openai-codex/gpt-5.6-sol` 불변, `providers.litellm` 정의는 유지, peer 는 해당 키 없음) agent+peer 게이트웨이를 함께 재시동했다(05:25:26Z 둘 다 active, 저널 오류 0). 이제 대화형 세션도 OAuth 실패 시 GLM 으로 조용히 강등되지 않고 fail-closed 다.]

  ↳ 재판정(2026-09-19, D003): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Plaud lifelog 노트 v2 양식 착지 후 소유자만 닫을 수 있는 것 (2026-09-04)

- **[OWNER] vault Linter 의 `yaml-timestamp` 가 `date-modified-source-of-truth: file system` 이라 소유자가 Obsidian 에서 lifelog 노트를 처음 저장(lintOnSave)하면 `modified` 가 그 시각으로 바뀐다(`created` 는 유지 — 실제 플러그인 빌드 헤드리스 실측, `docs/qa/PLV2/linter-idempotence.txt`) → 녹음 시각을 두 값 모두에 보존하려면 vault Linter 설정에서 modified 의 source of truth 를 `frontmatter` 로 바꾼다(소유자 결정; 노트 생성 코드는 어느 쪽이든 바뀔 것이 없다).** 본문은 그대로이고 메타데이터 한 줄만 움직이는 표시 문제(심각도 낮음).
  ↳ 재판정(2026-09-19, D004): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 노드 agent 계정의 `~/.env.secrets` 에 `LITELLM_AGENT_KEY`(·`LITELLM_BASE_URL`) 가 있어야 사람·장소·결정·할 일이 뽑힌다 → 릴리스 뒤 첫 `planned` 카드의 한눈에 줄을 본다: `- 추출:: 생략 (LLM 미설정)` 이면 키를 넣는다. 동결 본문은 승인 해시에 묶여 소급되지 않으므로 다음 녹음부터 적용된다.** 키가 없어도 노트는 결정론 필드(녹음·주제·한 줄)만으로 저장된다(심각도 낮음).
  ↳ 재판정(2026-09-19, D005): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 2026-09-02 에 이미 push 된 lifelog 노트(직장 동료 대화)는 v1 양식이고 첫 줄의 깨진 포스터 이미지가 그대로다 → vault 에서 그 줄을 지우거나 v2 로 다시 쓰려면 소유자가 직접 편집한다.** processed 원장이 같은 녹음의 재동기화를 막으므로 코드는 소급하지 않는다(심각도 낮음).

  ↳ 재판정(2026-09-19, D006): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 릴리스 승인 카드에 대한 피어 봇 자체 심사 (2026-09-03)

- **[OWNER] peer 에이전트가 `[release]` 승인 카드(v1.1.2)에 `⛔ DO-NOT-APPROVE — HEAD a75057c0 확인 불가(unpushed tip)` 를 붙이고 "✅ 하지 말고 push 뒤 재게시하라" 고 안내했다 → peer 에게 `[release]` 카드는 심사 범위 밖(VA-1 은 소유자 단독 ✅, 피어 attestation 은 스킬 단위)임을 지시하거나, HEAD 존재 확인을 `git ls-remote origin` 으로만 하게 자가 스킬 참고 문서를 고친다(peer 홈 `~/.hermes/skills/software-development/skill-deploy-review/`, `hermes curator`).** 판정 주체는 저장소 코드가 아니다 — 그 자가 스킬은 `[skill-deploy]` 전용 결정적 리포터(git 호출 0)이고, peer 가 그 어휘만 빌려 즉석 심사를 하며 HEAD 를 노드 로컬 체크아웃(`origin/main=7e130408`)에서 찾았다. 미러는 설계상 릴리스가 수렴한 **뒤에야** origin/main 을 따라오므로(「ops 체크아웃 단방향 규칙」 sync_mirror) 새 릴리스 HEAD 는 로컬에서 항상 "unpushed" 로 읽힌다. 실측: a75057c0 은 PR #378 머지(13:01:22Z)로 origin/main 에 있었고 요청 게시는 그 뒤, v1.1.2 태그·`agent-current`·deploy 영수증 전부 그 HEAD. 영향: 카드마다 거짓 ⛔ 안내가 붙어 소유자가 멀쩡한 릴리스를 막을 수 있다 — 심각도 중(릴리스·노드는 무영향).
  ↳ 처리(2026-09-04): 적어 둔 두 선택지 중 어느 것도 아니라 **E7 결정 복원**으로 닫았다. 그 자가 스킬의 SKILL.md 는 이미 트리거를 `[skill-deploy]` 로 한정하고 리포터 출력을 verbatim 중계하라며 `Do not add an LLM judgment.` 를 명시하고 있었는데도 peer 가 `[release]` 카드에 LLM 판단을 붙였다 — 참고 문서를 한 번 더 고치는 것은 이미 실패한 통제의 반복이다. `docs/patch/2026-07-17-e7-peer-attestation.md` 가 "That skill directory is removed: an agent must not read a Discord request and be instructed to run a reviewer as part of deployment" 로 그 리뷰어를 은퇴시켰으나 2026-08-15 루트 반전이 자작 스킬 보호 목적으로 `provision-skill-roots.sh` 의 `PEER_PINNED_SKILLS` 에 이름을 넣어 매 프로비저닝이 되살리고 있었다(의도적 재검토가 아닌 부수 효과). 조치: pin 목록을 `(autophagy-interop)` 으로 줄이고 회귀를 `test_provision_when_the_retired_reviewer_is_present_then_it_is_not_pinned` 로 고정(**존재**를 전제로 검사 — 부재면 `PEER-PIN-SKIP` 이라 고치지 않아도 통과한다), 노드에서는 tarball 백업 후 `hermes curator unpin` → `archive`(삭제 아님, `restore` 가능). 사후 실측: 1차 루트에 `autophagy-interop` 만, `list-archived` 에 등재, `hermes skills list` 무영향. peer attestation 은 ops 체크아웃의 `peer_attest.py` 가 자체 봇 토큰으로 회신을 게시하는 경로라 무관하다(그 3파일에 자가 스킬 루트·curator 참조 0건). 상세 `docs/patch/2026-09-04-peer-retired-reviewer-unpin.md`.
  ↳ 처리(2026-09-05): 위 09-04 진단은 불완전했다 — v1.2.0~v1.2.2 카드에도 같은 심사(`⛔ 배포 승인 보류`)가 붙었다. 지시 문서는 pin 된 `autophagy-interop`(2026-09-01 자가 저작 `[release]` 절차)이었고, 소유자 결정(인터롭 유지, A+C)에 따라 peer 게이트웨이의 `discord.ignored_channels` 로 `#approvals` 수신을 끊고(A) 그 절차를 스킬에서 제거했다(C). 원문·근거는 「릴리스 승인 카드가 peer 시야에 있다 (2026-09-05)」의 처리 줄과 `docs/patch/2026-09-05-peer-gateway-ignores-approvals.md`.

  ↳ 재판정(2026-09-19, D007): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 티켓 t_bd0d3789 종결 중 소유자만 닫을 수 있는 것 (2026-09-03)

- **[OWNER] 09-02·09-03 야간 재처리로 과제 원장 `회의록/해양고신뢰성/action-items.csv` 에 같은 회의의 미결 행이 중복 발급됐고(09-03 actions_new=4, 관리번호도 그만큼 소모), 옛 `회의록/2026` 폴더에는 야간 회의록 `2026-08-25_회의록-2026-08-26_20260825_해양고신뢰성.md` 가 남아 있다 → 소유자가 중복 행을 검토해 병합·종결하고 그 회의록 파일을 정본과 합치거나 지운다.** 이 PR 의 수정은 새 중복이 생기는 것을 멈출 뿐, 이미 쌓인 원장 행과 잘못된 자리의 파일을 되돌려 쓰지 않는다 — 원장 편집과 Drive 파일 정리는 소유자 판단이다(심각도: 낮음).

  ↳ 재판정(2026-09-19, D008): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 2026-09-03 수리 스윕(메일 인용·다이제스트 GLM 폴백)

- **[OWNER] glm-main 뒤 제공자의 잔액이 2026-09-02 15:21Z부터 소진돼 다이제스트·twin_distill·peer의 모든 비민감 GLM 호출이 HTTP 429를 받는다 → 소유자가 잔액을 충전하거나 별칭을 재바인딩한다.** FIX 2 뒤 다이제스트는 그때까지 codex 티어로 계속 처리된다(심각도: 중). [해소 2026-09-03: 잔액 충전이 아니라 별칭 재바인딩으로 닫혔다 — PR #370 이 `gpt-5-mini` 로 잠시 옮겼고 PR #373 이 `openai/gpt-5.6-luna`(`reasoning_effort: none`) 로 확정했다(routing-policy §2 바인딩 표 동기). 노드 `litellm-gateway/config.yaml` 에 luna 바인딩이 적용돼 재기동됐고 agent 키로 `glm-main` 완료 1건 성공을 SpendLogs 에서 확인했다. z.ai 는 별칭 뒤에서 빠졌으므로 충전할 것이 없다 — 남은 잔액·계정 정리는 소유자 선택이지 조치 항목이 아니다.]
  ↳ 재판정(2026-09-19, D009): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] FIX 3의 LiteLLM compose 변경은 ops가 <primary-node>에 파일을 복사한 뒤 `litellm-gateway.service`를 재시작해야 적용된다 → 컨테이너 재시작은 소유자가 결정한다.** 적용 전에는 이전 healthcheck가 계속 provider completion을 만들 수 있다(심각도: 중). [해소 2026-09-03: 노드 compose 가 `/health/liveliness` 로 갱신됐고 `litellm-gateway.service` 재기동으로 컨테이너가 healthy 로 올라왔다(PR #373 배포와 같은 사이클). 재기동 뒤 SpendLogs 에 `litellm-internal-health-check` 행이 더 이상 늘지 않는 것을 확인했다.]
  ↳ 재판정(2026-09-19, D010): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **노드의 live mail 스킬 마운트가 배포 릴리스 안에 있는 `c709c852`보다 앞서 있어 09-03 분류 실패가 그곳에는 기록되지 않았다 → 다음 릴리스 뒤 live/mail이 HEAD의 `triage_digest.py`를 담는지 확인한다.** 영향: 관측 결손, 동작 결함 아님(심각도: 낮음). [해소 2026-09-03: 릴리스 v1.0.154(`6e8f7c33`) 의 `deploy-skill.sh mail --release-approval` 로 live/mail 이 `7cf8afe2…`(SKILL 1.7.7) 로 올라갔고, `triage_llm_routing.py`·`mail_preflight.py` sha 가 HEAD 와 일치함을 노드에서 확인했다 — 분류 실패 로그 줄(`classify_failed`)을 남기는 `c709c852` 이 포함된 트리다.]
  ↳ 재판정(2026-09-19, D011): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`triage_confirm.add_reaction` 이 승인 메시지를 게시한 뒤 ✅/⛔를 미리 붙이는 중 Discord HTTP 503을 받으면 draft는 게시됐는데 `compose`가 traceback과 exit 1로 끝난다 → 리액션 사전 부착을 `APPROVAL-REACTION-FAIL` 마커의 best-effort로 바꾸고 exit 0으로 끝내 소유자가 수동으로 반응할 수 있게 한다.** 영향: 운영 불편, 외부효과 없음(심각도: 낮음).
  ↳ 처리(2026-09-03): `fix(mail): 승인 카드의 ✅/⛔ 사전 부착 실패를 APPROVAL-REACTION-FAIL 마커의 best-effort 로 바꾼다` — `triage_approval.MailApprovalGate.post` 가 HTTPError 를 잡아 stderr 한 줄, 반환값·레코드 불변, tests/unit/test_mail_reaction_confirm.py

  ↳ 재판정(2026-09-19, D012): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 티켓 t_ecb1654b 종결 중 소유자만 닫을 수 있는 것 (2026-09-02)

- **[OWNER] 피어 분류 레지스트리 `~/.hermes/interop/peers.yaml` 이 노드 agent 계정에 없다 → 피어 이름 기반 라우팅(coordination 위임 판정)이 필요하면 `python3 -m automation.group_roster.cli peers-seed --output ~/.hermes/interop/peers.yaml` 로 생성한다(선택, 분류 전용 — trust root 아님).** PR #358 이후 부재는 fail-soft(`PEER-REGISTRY-ABSENT` stderr 1줄)라 단독 일정 draft-create 는 정상이고, 없는 동안 피어 이름은 제목 토큰으로만 취급된다(심각도: 낮음).

  ↳ 재판정(2026-09-19, D013): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Plaud lifelog 롤아웃 — 소유자만 닫을 수 있는 것 (2026-09-02)

- **Plaud OAuth 는 브라우저가 필요하다 → 워크스테이션에서 `npm install -g @plaud-ai/cli` 후 `plaud login`,
  토큰 캐시 `~/.plaud`(tokens.json) 를 노드 agent 홈으로 복사하고 `automation/plaud_sync/deploy.sh` 를 돌린다.**
  `@plaud-ai/mcp install` 은 로컬 클라이언트에 MCP 설정을 쓰는 부작용이 있어 로그인 용도로 쓰지 않는다.
  복사 후 워처의 MCP 서버가 그 토큰을 실제로 읽는지(같은 `~/.plaud` 공유) 첫 틱에서 1회 검증한다.
  그 전까지 워처 discovery 는 "Not authenticated" 로 조용히 재시도만 한다.
  ↳ 재판정(2026-09-20, D014): VERIFIED-SNAPSHOT — 배포된 상태 CLI에서 최근 정상 폴 시각과 27건 집계를 확인했다. 재로그인·자격증명 복사 근거는 없으며 향후 인증 수명이나 별칭 이관 완료를 뜻하지 않는다.
- **기존 Zapier→Drive `PLAUD/raw` 내보내기 구상은 이 동기화가 대체한다 → Zapier 잽을 둘지 정리할지 소유자 결정.**
  남겨두면 무해하나 사본 두 벌이 쌓인다(심각도: 낮음).
  ↳ 재판정(2026-09-19, D015): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **MCP 는 `~/.plaud/tokens-mcp.json` 을, CLI 는 `tokens.json` 을 쓴다(같은 디렉터리·다른 파일) → 재설치·토큰 회전 때마다
  `tokens.json` 을 `tokens-mcp.json` 으로 복사(씨앗)해야 워처가 인증된다.** 2026-09-02 최초 롤아웃은 이 브리지를 만들어
  노드 `~agent/.plaud` 로 복사했다. 자동 씨앗(deploy/워처가 없으면 복사)을 넣을지는 리프레시 분기 위험과 함께 판단(심각도: 낮음).

  ↳ 재판정(2026-09-19, D016): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 참고자료 후속 5건 종결 중 남긴 것 (2026-08-27)

> [이관 2026-08-31] 배포 후 노드에서 `--slides` 실측이 선행 조건이다 — 소유자·노드 작업.

- **`meeting_slides` 가 런타임(`automation`)에 새로 의존한다** — 추출을 `document_text` 로
  위임하면서 마운트 스킬이 릴리스 런타임을 해석해야 한다. 스킬만 재배포하고 릴리스가 낡으면
  발표자료를 못 읽고 사유도 부정확해진다(fail-soft라 회의록 자체는 나온다). 조치: 배포 후
  노드에서 `--slides` 를 실측하고, 버전 스큐를 사유 문구로 구분한다. **회수율 · 심각도 중**.

  ↳ 재판정(2026-09-19, D017): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #521. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 회의록 과제 양식·Action Item 데이터베이스 중 남긴 것 (2026-08-27)

> [이관 2026-08-31] 아래는 소유자 결정·노드 실행이 선행 조건이다(토큰 형태 확정 · 정본 판정 · 연달아 배포).

- **본문만 있는 `!meeting` 은 과제를 지정할 방법이 없다** — 첨부가 있으면 파일명(라벨)에서
  `detect_project` 가 찾지만, `!meeting` 뒤에 회의 내용을 바로 붙이는 경로는 라벨이
  `!meeting 본문` 고정이라 대조할 단서가 없다. 그때는 관리번호 없이 진행되고 통지에 그 사실이
  실린다. 조치: 플러그인이 `!meeting --project <과제명>` 형태의 선행 토큰을 파싱해 본문에서
  떼어내고 CLI 에 넘긴다(형태는 소유자 확정 필요). **회의록 생성은 정상 · 심각도 중**(과제
  회의를 본문으로 넣으면 번호가 빠진다).
  ↳ 재판정(2026-09-19, D018): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **Drive 에 손으로 쓴 회의록 2건이 고아 번호를 달고 남아 있다** —
  `2026-08-25_해양고신뢰성_참여기관업무협의_회의록.md`(임시번호 `AI-260825-*`, 원장으로 임포트됨)과
  `2026-08-25_해양고신뢰성_전사본기반_회의록.md`(임시번호 `TR-260825-*`, 원장과 무관)이다. 후자의 16건은
  전자의 22건과 내용이 겹칠 수 있으나 어느 것이 같은 항목인지는 사람만 판단할 수 있다. 조치: 소유자가
  두 문서 중 정본을 정하면 비정본을 trash 로 보내고, 겹치는 항목은 원장의 기존 번호로 통일한다.
  **원장 자체는 정상 · 심각도 중**(같은 일이 두 번호로 남아 있는 상태다).
  ↳ 재판정(2026-09-19, D019): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **공유 lock 전환 창에는 배제가 없다** — 두 워처 래퍼는 계정 홈에 각각 배포되므로 한쪽만
  새 lock 을 쓰는 순간이 생긴다(`deploy.sh` 두 번 사이). 그 창에서 자정 배치와 5분 틱이
  겹치면 예전과 같은 이중 처리가 가능하다. 조치: 두 래퍼를 **연달아** 배포하고, 배포 직후
  `hermes cron list` 의 Last run 으로 그 사이에 실행이 없었는지 확인한다. 창이 분 단위라
  실제 충돌 확률은 낮다. **일회성 · 심각도 낮음**.

  ↳ 재판정(2026-09-19, D020): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 노드 배포 표면 감사(2026-09-01) 중 소유자만 닫을 수 있는 것

> 2026-08-29 첨부 아카이브 자가 배포 사건을 계기로 노드 배포 표면(미러·계정 홈·cron·세션 기록·자가 스킬)을 읽기 전용으로 전수 실측했다. 증적: 이 세션의 감사 보고서 `docs/qa/audit-2026-09-01/node-deploy-surface-audit.md`.

- **[OWNER] 에이전트가 만든 게이트웨이 재시동 헬퍼가 상주한다 → 회수하고 재시동은 승인된 노드 런북(agent·peer 함께, 원인 확인 후)으로만 한다.** `~agent/.hermes/scripts/restart-hermes-gateway-once.sh`(95B, 2026-08-14, 내용은 `systemctl --user restart hermes-gateway.service` 한 줄, 리포 소스 없음; 세션 기록상 08-14~16 사이 6회 생성·실행). 「게이트웨이 재시동 규칙」을 우회하는 수단이 남아 있다(심각도: 높음).
  ↳ 재판정(2026-09-19, D021): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ [해소 2026-10-01] 소유자 지시(「남긴 문제 모두 해결」, 항목 판단 위임)로 `~agent/.hermes/quarantine-20261001/` 로 옮겼다 — 삭제가 아니라 이동이고 같은 디렉터리 `INDEX.txt` 에 원경로·크기·sha256·시각이 있다. 옮기기 직전 릴리스 트리 코드 참조·cron 25개·실행 프로세스 어디에도 없음을 다시 확인했고 cron 목록 지문은 전후 같다.
- **[OWNER] agent 홈에 별도 클론이 있고 로컬 전용 커밋 2건이 있다 → 살릴지 버릴지 결정한다.** `/home/agent/src/autophagy-agents`: 작업트리는 clean 이지만 origin 에 없는 커밋 2건(2026-07-30, 08-02)이 있고 origin/main 보다 크게 뒤처져 있다 — 미러 사건과 같은 계열의 "리포 밖 개발" 흔적이며, 에이전트가 이 경로의 스크립트를 실행하면 낡은 사본 실행이 된다(심각도: 중).
  ↳ 재판정(2026-09-19, D022): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 소유자 없는 홈 자산 2건의 출처를 판정한다.** `~agent/.hermes/scripts/regression_bank_weekly.py`(1657B, 2026-07-16; 리포 소스·매니페스트·cron 등록 모두 없음)와 `~agent/.hermes/plugins/hermes-achievements/`(JSON 3개 1.3MB, 코드 없음, 2026-08-16). 회수하거나 벤더 산출물로 기록한다(심각도: 낮음).
  ↳ 재판정(2026-09-19, D023): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ [해소 2026-10-01] 두 자산 모두 회수했다 — 같은 격리 디렉터리로 옮겼고(`hermes-achievements` 는 코드 없는 상태 데이터이고 플러그인은 활성 목록에 없다) 참조·cron·프로세스 확인과 INDEX 기록은 위 D021 처리와 같다.
- **[OWNER] 자가 스킬 5개 중 governed 와 겹치는 것의 승격·폐기를 결정한다.** `meeting-minutes-authoring`(meeting 과 겹침)·`document-publishing`(doctype·report 와 겹침) 등 `~agent/.hermes/skills/{documents,devops}/…` 5개(2026-08-18~28). 결정 전까지 `hermes curator pin/archive` 로 상태를 고정한다(심각도: 중).
  ↳ 재판정(2026-09-19, D024): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 미러가 agent 자격증명으로는 원격을 읽지 못한다.** `/srv/autophagy-agents` 에서 `git ls-remote origin` 이 `Repository not found` 인데도 `origin/main` ref 는 최신이다(ops 가 fetch). 미러를 읽기 전용 관측소로 고정하면 자연 해소되며, fetch 주체를 운영 가이드에 적는다(심각도: 낮음).

  ↳ 재판정(2026-09-19, D025): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 관측 미러가 미커밋 편집으로 동결돼 121 커밋 뒤에 있었다 (2026-09-01 실측)

- **`/srv/autophagy-agents` 가 `ec39b11f`(#319) 에 멈춰 있고 08-29 00:54Z 부터 dirty 다 → 소유자가 그 미커밋 편집을 살릴지 버릴지 결정한 뒤 미러를 정본으로 되돌린다.** dirty 내용은 `skills/mail/SKILL.md`(v1.6.3 "메일 첨부파일 Drive 아카이브" 절)와 `skills/mail/deploy.sh`(`mail_attachment_drive_sync.py`·`mail_attachment_drive_watch.py` 배포 행) — 리포에 없는 기능이라 살리려면 `git diff` + untracked 파일을 워크스테이션 브랜치로 옮겨 PR 로 착지시키고, 그 뒤 미러에서 `git checkout -- .` 후 `git pull --ff-only`(리컨실러 `sync_mirror` 는 dirty 를 건드리지 않으므로 사람이 한 번 해야 한다). 이 동결이 2026-09-01 김경호 회신 무인용 사고의 두 번째 원인이다 — 에이전트가 실행한 미러 사본에 `mail_quote` 도 승인 정책 v8 도 없었다. `checkout_mirrors_origin` 프로브는 dirty 를 FAIL 로 내고 있었으나 아무도 닫지 않았다(심각도: 중 — mail 은 `governed_copy_refusal` 로 사본 실행이 막혔지만 다른 스킬과 문서 읽기는 여전히 낡은 미러를 본다). [해소 2026-09-01: 미커밋 작업은 PR #352 로 정식 착지(v1.0.146, mail v1.7.2 마운트 `7ea8af3f…`)했고, ops 가 추적 2파일 `git checkout --`·agent 소유 untracked 3파일 제거·`git pull --ff-only` 로 미러를 `7c0ed308` == origin/main 으로 복원했다(clean, 0 behind).]
  ↳ 재판정(2026-09-19, D026): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 미러가 agent 에게 쓰기 가능하다 → root 가 `/srv/autophagy-agents` 트리의 group 쓰기 비트를 내린다(`chmod -R g-w`, setgid 디렉터리 `2775`→`2755`; ops 만 쓰기).** 2026-09-01 실측: 디렉터리가 `ops:autophagy 2775` 라 group `autophagy` 의 일원인 agent 가 파일을 만들고 추적 파일(`skills/mail/SKILL.md` 가 agent 소유로 바뀜)까지 덮어썼다. 커밋 거부 훅은 커밋만 막고 편집·untracked 는 못 막는다 — 「ops 체크아웃 단방향 규칙」이 산문에 그친 구조적 원인이다(심각도: 높음).
  ↳ 재판정(2026-09-19, D027): OWNER — 미러 루트 2750과 AGENTS.md 0644는 보호되지만 automation/ 2775는 agent·peer 모두 쓰기 가능하다. 검사한 경로의 사실이며 전체 트리 보호를 주장하지 않는다. 권한 수렴은 소유자에게 남긴다.
- **게이트웨이 수준 경로 정책은 위 「게이트웨이 플러그인 배포 경로(2026-08-28) 후속」 이 풀린 뒤에 싣는다.** `pre_tool_call` 에서 `…/skills/<skill>/scripts/…` 참조가 live 루트 밖이면 막는 순수 판정을 `automation/interop` 에 두고, 플러그인 배포 경로 신설과 같은 사이클에 agent·peer 재시동으로 반영한다(심각도: 중).

  ↳ 재판정(2026-09-19, D028): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 게이트웨이 플러그인 배포 경로(2026-08-28) 후속 — interop-protocol

> [이관 2026-08-31] 소유자 roster 배치 결정이 선행 조건이다 — 순서를 바꾸면 인터롭이 fail-closed 로 막힌다.

`00-meeting-gate` 의 배포 경로를 만들며 같은 계열을 전수 조사했다. 노드 홈의 플러그인 4개 중
리포에 소스가 있는 것은 셋이고, 그중 둘은 정상(`00-meeting-gate` 이번에 신설,
`05-skill-generation` 은 `automation/skill_generation/deploy.sh:31` 이 이미 민다). 나머지 하나가
문제다(`hermes-achievements` 는 리포에 소스가 없는 외부 자산이라 대상이 아니다).

- **`interop-protocol` 이 5주 낡았고 배포 경로가 없다** — 노드 사본 `d229b105`(268줄)은
  `2bf695ac`(2026-07-20) 시점 코드고 리포 `575d04f0`(302줄)은 `6623bf74`(2026-08-15,
  "bind envelopes to Discord roster identity") 이후다. 손 편집이 아니라 그냥 낡은 것이라
  유실 걱정은 없다. 결과: **roster 기반 발신자 신원 대조(W-F2.5-B)가 프로덕션에서 돌지 않는다** —
  봉투의 `sender_id` 자칭을 아무도 막지 않고, 봇 자유 산문 무시(cascade 안전)도 없다.
  agent·peer 양쪽 동일하다.
  ↳ 재판정(2026-09-19, D029): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ 처리(2026-10-01): 배포 경로가 생겼다 — `automation/interop/deploy.sh` 가 선언(`automation/interop/deploy-manifest.txt`)대로 플러그인·가드·드롭인을 agent·peer 에 함께 올리고, 플러그인 본문은 게이트웨이가 고정한 릴리스 세대에서 읽으며 그 세대를 기록한다. 릴리스 판정은 그 기록까지 본다 — `f2ef2ac5`, `43c5617a`, `2b74f092`. 코드·배포기는 준비됐고 노드 반영은 웨이브 6 todo 33(roster 배치 → 묶음 수렴 → agent+peer 쌍 재시동)의 소유자 승인에 달려 있다.
- **그런데 그냥 배포하면 인터롭이 멈춘다** — 새 코드는 `~/.hermes/roster.yaml` 을 읽고 없으면
  `RosterError` → 모든 봉투를 `roster_unavailable` 로 거부한다. 실측: agent·peer 둘 다 파일이
  없고(`NONE`), 릴리스에 `group_roster` 패키지는 이미 있다. 즉 **roster 배치가 선행 조건**이며
  그 내용(누가 어떤 `sender_id` 인가)은 소유자가 정할 사안이다.
  ↳ 재판정(2026-09-19, D030): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ 처리(2026-10-01): roster 는 손으로 쓰지 않고 노드 신뢰 루트에서 `python3 -m automation.group_roster init-local` 로 만든다 — `521871a7`. 배포기는 두 계정 roster 가 모두 유효할 때만 묶음을 놓고, 아니면 `INTEROP-DEPLOY-HELD` 로 어느 계정에도 놓지 않는다 — `f2ef2ac5`. 코드·배포기는 준비됐고 노드 반영은 웨이브 6 todo 33(roster 배치 → 묶음 수렴 → agent+peer 쌍 재시동)의 소유자 승인에 달려 있다.
- 조치 순서: ① 소유자가 roster 배치를 결정 → ② 플러그인을 agent·peer 양쪽
  `~/.hermes/plugins/interop-protocol/` 로 미는 배포 경로 신설 → ③ 매니페스트에 2계정 등록 →
  ④ agent·peer 함께 재시동. **순서를 바꾸면 인터롭이 fail-closed 로 막힌다.** 매니페스트 등록을
  먼저 하면 프로브가 FAIL 하며 "deploy.sh 를 돌려라"라는 **잘못된 자동 조치를 유도**하므로,
  등록은 roster 결정과 같은 사이클에 넣는다.
  ↳ 재판정(2026-09-19, D031): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ 처리(2026-10-01): 순서는 코드가 지킨다 — 묶음 행이 `requires=roster` 로 선언돼 roster 가 없으면 판정이 `held`(`roster-required`)로 보류하고 배포기를 부르지 않으므로, 선언이 "deploy.sh 를 돌려라"를 잘못 유도하지 않는다. 계정별 사유는 `3a5d30a4`·`641d3323`, 선언·배포기는 `f2ef2ac5`. 코드·배포기는 준비됐고 노드 반영은 웨이브 6 todo 33(roster 배치 → 묶음 수렴 → agent+peer 쌍 재시동)의 소유자 승인에 달려 있다.
- **심각도 중** — 방어가 꺼져 있으나 해당 채널 참여자가 제한적이고, 잘못 고치면 인터롭이 멈춘다.
  되돌리기는 쉽다(옛 사본 복구 + 재시동). 증적: 이 세션의 해시·diff·`load_roster` 실측.
  ↳ 재판정(2026-09-19, D032): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ 처리(2026-10-01): 되돌리기가 절차가 됐다 — 배포기가 바꾸기 직전 묶음을 `~/.hermes/interop/rollback/<UTC>-<pid>/` 에 보관하고(`f2ef2ac5`), 복원 절차는 `docs/guide/operations.md` §2 「인터롭 묶음 되돌리기」가 소유한다. 코드·배포기는 준비됐고 노드 반영은 웨이브 6 todo 33(roster 배치 → 묶음 수렴 → agent+peer 쌍 재시동)의 소유자 승인에 달려 있다.
- **[2026-09-01 실측 추가] agent·peer 두 계정 모두 같은 2026-07-20 사본이다** — `~/.hermes/plugins/interop-protocol/__init__.py` sha256 `d229b105…`(268줄) 양쪽 동일, 리포 `automation/interop/hermes_plugin/__init__.py` 는 288줄(최종 9e481bb1). 배포 경로 신설 시 두 계정을 함께 민다(심각도: 중).



  ↳ 재판정(2026-09-20, D033): OWNER — agent·peer 설치본은 모두 기존 d229b105 지문이고 현재 릴리스 소스와 다르다. roster 선택이 선행한다는 원래 순서를 유지해 플러그인·매니페스트·roster를 바꾸지 않았다.
  ↳ 처리(2026-10-01): 배포기는 두 계정에 같은 바이트를 함께 올리고 계정마다 선언과 대조한다 — `f2ef2ac5`, `856f8c31`. 설치본 지문은 바뀌지 않았다(노드 반영 전). 코드·배포기는 준비됐고 노드 반영은 웨이브 6 todo 33(roster 배치 → 묶음 수렴 → agent+peer 쌍 재시동)의 소유자 승인에 달려 있다.
## 제안서 HWPX 품질 수리 중 남긴 것 (2026-08-26)

> 이 저장소가 지금 손댈 수 없거나 이미 닫힌 항목이다. 원 묶음 헤딩은 회계 가드 대조 키라 그대로 둔다.

- **밴드 사이에 빈 페이지가 생긴다 — 48쪽 중 10쪽** — `render_layout_bands` 가 밴드 앞뒤로
  `pageBreak="1"` 빈 문단을 하나씩 내보내 밴드 경계마다 연속 페이지 나눔이 발생한다. 이 두 번째
  경계는 커밋 `1103be4` 가 "estimate-tier validation 이 같은 밴드 모델을 읽도록" 의도적으로 넣었고
  `test_rendered_band_persists_both_page_boundaries` 가 `count(pageBreak="1") == 2` 로 고정한다.
  조치: 경계 표시를 빈 문단이 아니라 주석 마커로 옮기고 검증기가 마커를 읽게 바꾼다 — 그냥 지우면
  다른 세션이 세운 밴드 검증 모델이 조용히 무너진다. v000001 에도 동일하게 존재했다.
  **분량·인쇄 품질 · 심각도 중**(본문 내용에는 영향 없음).
  [해소 확인 2026-08-27] "검증 모델이 무너진다"는 우려는 코드를 읽어 **사실이 아님**을 확인했다 —
  `estimate_pages` 는 `fixed_template_pe + prose_pe + figure_pe + table_pe + heading_pe` 로 page-break 항이
  아예 없고, `validate_density_xml` 은 밴드 **선행** 나눔 하나만 확인한다(`BAND_PAGE_BREAK_MISSING`).
  후행 빈 문단을 고정하던 것은 테스트 한 줄(`count(pageBreak="1") == 2`)뿐이었고, 밴드 끝은 이미
  `<!--/LAYOUT-BAND index="N"-->` 주석이 표시한다. 즉 그 문단은 정보가 없고 페이지만 먹었다.
  제거 후 실측: **48쪽 → 33쪽, 빈 쪽 10 → 0**. 고정 테스트는 새 계약(선행 나눔 1개 + 종료 마커)으로
  갱신했다 — kimm-docbot `ee6d7e7`.
  ↳ 재판정(2026-09-19, D034): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **양식의 미기입 안내문이 최종본에 그대로 남는다** — 표지의 `과제명 (국문/영문): 연구의 한 줄
  요약이 드러나도록 기재`, `전체 연구기간: YYYY.MM.DD ~ YYYY.MM.DD` 같은 placeholder 를 채우는
  단계가 파이프라인에 없다. 조치: seed 의 표지 슬롯을 `planspec` 값으로 채우는 단계를 render 앞에
  둔다(양식 파일 자체는 편집 금지이므로 렌더 대상 XML 에서만 채운다).
  **제출 완성도 · 심각도 중**.
  [해소 확인 2026-08-27] `hwpx/seed_fill.py` 신설로 닫혔다 — 표지 9필드를 `PlanSpec` 에서 파생해 채우고
  (`render --cover <json>` 으로 계획에 없는 값만 덮어쓴다), 양식 메타 4문단과 안내 불릿 31개를 삭제하며,
  삭제로 밀린 `anchor_map` 서수를 같은 호출에서 재매핑한다. 실측: 안내문 35건 → 0, 표지 0/9 → 9/9.
  삭제는 **앵커가 쓰는 문단을 건드리지 않는다** — `hs:sec/hp:p[74]` 는 안내 불릿이면서 동시에
  `section.4.traceability` 의 쓰기 대상이라, 지우면 슬롯이 사라지고 `write_traceability_table` 이
  `KeyError` 를 삼켜 근거 부록이 **오류 없이 조용히** 사라졌다(kimm-docbot `5d0cbf7`).
  요약 본문이 표지 위에 삽입돼 과제명·연구기간이 3쪽 하단으로 밀리던 것도 함께 고쳤다(`65337943`).
  ↳ 재판정(2026-09-19, D035): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **양식의 소제목과 우리 본문의 소제목이 중복된다** — 시드는 `1-1. 기술적 배경 및 국내외 동향` 같은
  소제목 12개를 갖고 있고, 집필 본문도 자체 `## 1.1 …` 구조를 갖는다. 안내 불릿만 지웠으므로 목차가
  두 겹으로 보인다. 조치: 시드 소제목을 지우거나 본문 소제목을 없애 한쪽으로 통일한다 — **정부 양식이
  요구하는 목차를 임의로 지우는 것은 소유자 판단**이라 이번 범위에서 하지 않았다. `2-3 성과지표`·
  `3-3 추진 일정` 은 KPI·간트 표의 라벨이라 함께 지우면 표가 이름을 잃는다. **가독성 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D036): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **제출 분량이 33쪽으로 양식 권장(10쪽 내외)의 세 배다** — `resource/rule.md` 의 「HWPX 결과물 가이드」는
  "10 페이지 내외 (A4 / 돋움 11pt / 줄간격 160%)" 를 요구하고, 조판은 이번에 그 규칙에 맞췄지만 분량은
  그대로다. 프로파일 `10-page` 가 이미 있으므로 기계적 축약은 가능하나, 어떤 근거·그림을 버릴지는
  소유자 판단이다. 조치: 소유자에게 `30-page` 유지와 `10-page` 재작성 중 선택을 받는다.
  **평가 규칙 대비 초과 · 심각도 중 · 소유자 결정 필요**.

  ↳ 재판정(2026-09-19, D037): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## budget 다중 시트·Drive 정리 중 남긴 잔재 (2026-08-24)

- **레거시 `proposal/excavator`·`doctype/vendor.md`가 산출물 루트에 남아 있다** — taxonomy
  랜딩(8/23) 전 발행분이고 마이그레이터는 `<type>/<YYYY-MM>` 형태만 다뤄 계획에 안 잡힌다.
  소유자 지시("이전 문서는 무시")로 존치 → 거슬리면 Drive 수동 정리. **동작 영향 없음 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D038): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **노드에 다중 시트 레지스트리가 아직 없다** — 코드는 레거시 `BUDGET_SHEET_ID` 모드로 계속
  돌고, 과제를 나누려면 agent 계정 `~/.hermes/budget/sheets.json`에 기존 원장 ID까지 등재해야
  한다(미등재 env=exit 3) → 형식은 `configs/budget-sheets.example.json`. **활성화 대기 · 심각도 낮음**.

관련 기능: [과제별×년도별 예산 시트](기능소개/과제별-연도별-예산-시트.md).

  ↳ 재판정(2026-09-19, D039): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## DM→#agent-chat 이관(승인 표면 v7) 중 발견한 후속 과제 (2026-08-24)

- **repair 승인은 아직 소유자 DM이다** — 발신 주체인 Ops 봇이 어떤 길드에도 참여하지
  않아 v7에서 의도적으로 제외했다. 조치: 소유자가 Ops 봇을 개인 서버에 초대한 뒤 별도
  정책 버전(v8)으로 REPAIR 전환. **의도된 예외 · 심각도 낮음**(수리 승인 왕복 자체는 정상).
  ↳ 재판정(2026-09-19, D040): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **Hermes cron `--deliver discord`의 대상은 여전히 owner DM 고정** — mail digest의 실패
  마커 등 cron 레벨 전달이 해당(다이제스트 본문은 이번에 채널로 이관됨). Hermes CLI가
  채널 대상을 지원하는지 노드에서 `hermes cron create --help`로 확인 후 지원 시 전환.
  **실패 마커만 DM으로 옴 · 심각도 낮음**.

관련 기능: [소유자향 통지·승인의 #agent-chat 이관](기능소개/승인-agent-chat-스레드.md).

  ↳ 재판정(2026-09-19, D041): PARTIAL — 설치된 CLI는 platform:chat_id를 지원한다. 현재 mail-daily-digest는 deliver=discord이며 최근 실행은 ok다. 실제 해석된 채널/DM 목적지는 목록에 없어 이관 완료로 보지 않는다.
## RAG 런타임 배포·탐지 수리 중 발견한 후속 과제 (2026-08-22)

- **embedding 소스와 실행 이미지의 갱신 시점이 갈린다** — `automation/rag_stack/deploy.sh`는 `embedding/` 정본도 노드에 동기화하지만 의도적으로 컨테이너를 재빌드하지 않고, 후속 활성화 명령도 검색 중단 범위를 줄이려고 MCP만 빌드한다. 따라서 embedding 소스는 최신이어도 실행 이미지는 2026-07 빌드본으로 남는다 → 모델 캐시와 긴 빌드 시간을 고려해 embedding 재빌드 변경 창을 소유자와 정한다. MCP 이미지는 새 healthcheck 프로브가 실행 컨테이너 내부 핵심 모듈 해시로 검증한다. **현재 동작과 무관 · 심각도: 중**.
  ↳ 재판정(2026-09-19, D042): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **RAG 노드의 `personal-rag.service` ops user unit이 2026-08-08 21:53 UTC부터 failed다** — 부팅 때 `docker compose up -d --build`가 `ghcr.io/astral-sh/uv:0.9.18` 메타데이터 DNS 조회(`127.0.0.53`, `server misbehaving`)에 실패했다. 컨테이너는 12일째 healthy이고 현재 `ghcr.io` 도달도 정상(HEAD `/v2/` → 405)이라 현 서비스 영향은 없지만, 같은 실패가 재부팅 때 나면 RAG 스택 전체가 올라오지 않는다 → 원인 해소를 확인한 뒤 유닛을 복구·재시작하고, 빌드 시 레지스트리 실패의 재시도·캐시 전략을 검토한다. **재부팅 복구성 영향 · 심각도: 중**.

관련 기능: [런타임 패키지 배포와 드리프트 탐지](기능소개/런타임-패키지-드리프트-탐지.md). 실측일 2026-08-22, 릴리스 `v1.0.46` (`99b9d25e`).

  ↳ 재판정(2026-09-20, D043): PARTIAL — personal-rag 사용자 유닛은 08-08 실패 상태지만 현재 MCP·Qdrant·embedding 컨테이너는 모두 healthy다. 보이는 사용자 저널은 08-12부터여서 당시 원인은 확인하지 못했고 정상 컨테이너를 재시작하지 않았다.
## mailon 런타임이 19일간 옥 릴리스에 고정돼 있었다 (2026-08-18)

소유자의 논문 메일을 머춰 상태에서 수리하다, 진짜 원인이 드러났다. `035e767d`(2026-07-29, exact-set 수신자 검증)가 결함 **2건**을 함께 들여왔는데, mailon 런타임이 07-30 릴리스(`96599b22b6e6fdda`)에 **19일간 고정**돼 있어 프로덕션은 그 코드를 한 번도 실행하지 않았다. 그래서 발송은 8/14까지 멀줦히 동작했다. 오늘 compose 기동 경쟁(PR #152)을 고치려 vendor 트리를 배포하자 **둘이 동시에** 올라와 모든 발송이 즉시 실패했고, 2회 연속 실패가 mail-mode를 `no-go`로 강등시켰다. 두 결함은 PR #157·#158로 고쳤고 발송도 확인했다(보낸편지함 재조회 증적).

- **런타임 고정을 healthcheck 가 못 본다 — 다이제스트 틱이 대신 본다** — 이번에 넣은 드리프트 프로브는 agent 계정 경로에 두고 다이제스트 워처가 태운다. `automation/healthcheck.sh` 에 넣지 않은 이유는 그쪽이 ops 계정으로 도는데 런타임은 `~agent/.hermes`(0700)라 읽을 수 없고, 중첩 sudo 는 이 저장소에서 rc=126 으로 프로브를 죽인 실측 선례가 있기 때문이다 → healthcheck 에서도 보이게 하려면 권한 설계(전용 read-only 노출 또는 ops 가 읽을 수 있는 상태 파일)를 먼저 정한다. **동작 결함 아님 · 심각도: 낮음(탐지 경로가 하나뿐)**.

증적: PR #157(빈 method) · PR #158(body probe), 릴리스 `85aaf5ca488f37a6`, 발송 확인 `2026-08-18T08:55:40Z` 보낸편지함. 기능 소개: [mailon 런타임 고정 탐지](기능소개/mailon-런타임-고정-탐지.md).

  ↳ 재판정(2026-09-19, D044): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

- **같은 업데이트가 agent의 `hermes_compat` 패치 3종을 통째로 떨괴뜨렸다 — 재작성은 끝났고(PR #147) 배포만 남았다** — 장애 조사 중 부수적으로 드러났다. agent의 `gateway/run.py`·`plugins/platforms/discord/adapter.py`에서 마커 `_hermes_pgd_done`·`_hermes_busyfifo_done`·`_hermes_receipts_done`이 **세 개 모두 없고**, 변경분은 08-16 업데이트가 만든 autostash(2파일 252줄)에 남아 있었다 — stash는 됐지만 복원이 안 됐다. 빠진 것 중 `busy-path-pre-gateway-dispatch`는 busy 경로 메시지가 skill-generation 관찰(W6-4)과 **meeting-gate fail-closed veto(W2-3)** 훅을 타지 못하는 문제를 메우는 것이라 단순 편의 패치가 아니다. **제거가 아닌 재작성임을 먼저 확인했다** — v0.20.3에서도 `pre_gateway_dispatch` 호출 지점은 `_handle_message` 한 곳뿐이고(`run.py:16309`) `_handle_active_session_busy_message`(`run.py:9920`)는 여전히 busy 핸들러로 배선돼 있어 세 `removal_condition` 중 어느 것도 충족되지 않았다. 원본은 `archive/hermes-compat-v0.18.2-patches`(`ab5d6d97c`)와 `~agent/.hermes/hermes-compat-patches-v0.18.2.diff`(sha256 동일 확인, 0600)로 이중 보존해 둔 것을 기준으로 썼다 → 남은 일은 PR #147 머지 후 배포와 마커 3개 verify뿐이며, **그전까지는 그 두 훅이 busy 경로에서 미발동임을 전제한다**. **프로덕션이 08-16부터 무패치로 구동 중 · 심각도: 높음(게이트 인접 훅이 꺼져 있다)**.
  ↳ 재판정(2026-09-19, D045): VERIFIED-SNAPSHOT — 현재 agent는 원래 3종을 포함한 5개 마커가 모두 적용됐다. peer에는 없지만 기존 배포기는 agent 소스에만 적용한다. peer 재패치·재시동으로 범위를 넓히지 않았다.
- **패치 carrier를 올리는 deploy 스크립트가 둘이고 목적지도 다르다 — 한 쌓만 돌리면 절반만 복구된다** — 노드의 `~/.hermes/hermes-compat/hermes_compat/`에는 `patch_busy_dispatch.py`만 있다. 실측해 보니 누락이 아니라 설계가 그렇다 — `deploy.sh`는 `patch_busy_dispatch.py`를 `hermes_compat/`에, `deploy-owner-dm.sh`는 `patch_busy_fifo.py`·`patch_discord_receipts.py`를 **`appliers/`라는 다른 디렉터리**에 올리며, 후자의 preflight도 그 둘만 검증한다("prove BOTH patches") → 3종을 다 올리려면 **두 스크립트를 모두** 실행하고 끝에 마커 3개를 일괄 verify해야 한다. 장기적으로는 한 경로로 모으는 것이 맞지만, 그러면 `owner-dm-txn.sh`·`owner-dm-restore.sh`의 롤백 매니페스트도 함께 바꿉다. **배포 절차의 선행 조건 · 심각도: 중(모르고 시작하면 절반만 복구된다)**.

  ↳ 재판정(2026-09-19, D046): VERIFIED-SNAPSHOT — 두 설치 경로의 원래 applier 3개가 현재 릴리스와 각각 SHA 일치한다. 다른 런타임 파일 전체나 배포 경로 통합의 증거는 아니다.
## 에이전트 자가 스킬 공존(SS-1) 작업 중 발견한 후속 과제 (2026-08-15)

자가 스킬 루트 반전과 감사 원장을 만들며 발견했다. 기능은 [소개](기능소개/에이전트-자가-스킬.md).

- **Hermes 네이티브 충돌 거부는 우리 토폴로지에서 아예 동작하지 않는다(2026-08-16 정정 · 예방 미구현)** — `_find_skill`이 `rglob("SKILL.md")`로 훑는데 governed 루트가 심링크 팜이라 한 건도 못 본다(`_find_skill("mail")` → `None`). 그래서 자가 스킬이 배포본 이름을 선점해 **승인 게이트를 가릴 수 있다** → 예방은 live 루트를 심링크가 아닌 실디렉터리로 두거나 업스트림에 보고해야 하고, 지금은 `selfskill_audit`의 `SHADOWS-GOVERNED` **탐지**만 있다. **upstream 보고서 작성 완료**: [hermes-find-skill-symlink-blindness.md](troubleshooting/hermes-find-skill-symlink-blindness.md) — 재현 실측과 제안 패치(`os.walk(followlinks=True)` + 심링크 순환 가드)를 담았다. 소유자가 벤더에 전달하면 된다. **두 선택지의 비용을 실측했다(2026-08-16)**: 실디렉터리 전환은 심링크 팜을 만드는 주체가 `skill_store.py`=root NOPASSWD 3종 중 하나인 `/usr/local/libexec/autophagy-install-skill`이라 **가장 특권 있는 경로를 고치는 일**이고, 더 큰 문제는 이 저장소의 배포 판정 자체가 `readlink /srv/autophagy-skills/live/<skill>` 해시(「커밋됨 ≠ 배포됨」)라는 점이다 — 실디렉터리로 바꾸면 그 판정 기전이 사라지고 `skill_mount_drift.py`·`skill_mount_probe.sh`·`selfskill_root_probe.sh`·`deploy-skill.sh`·`land.sh`가 함께 따라온다(원자적 교체도 rename 춤으로 다시 만들어야 한다). 반면 업스트림 보고는 `rglob`→`os.walk(followlinks=True)` 한 줄이고 반영 시점만 남의 손이다 → **탐지로 버티며 업스트림 보고를 먼저 하는 쪽을 권고**하되, 선택은 소유자 판단으로 남긴다. **심각도 높음(가려지면 게이트 우회)**. curator external-write 가드와 curator가 configured-external 스킬을 건드리지 않는 성질은 우리 코드가 아니라 벤더 동작이며, 회귀로 못박을 수단이 없다 → **`hermes update`마다 S4·S5 QA 명령을 재실행**해 두 성질이 유지되는지 확인한다(S4: 배포 스킬 이름으로 생성 지시 후 디렉터리 미생성, S5: `hermes curator archive <배포 스킬>` 거부 + live readlink 불변 + 쓰기 `Permission denied`). **현재 동작 정상 · 심각도 중간(업데이트로 이름 선점 방어가 조용히 약해질 수 있음)**.
  ↳ 재판정(2026-09-19, D047): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **peer의 `~/.hermes/skills/prompt` 잔여물은 배포를 막을 뿐 아니라 지금 배포본을 가리고 있다(2026-08-16 실측 보강)** — 루트 반전 이후 그 경로는 read-only bind가 아니라 peer가 소유한 **1차 루트**이고 1차 루트가 발견에서 이긴다. `/srv/autophagy-skills/live/prompt`가 존재하므로 이것은 위 S4 항목이 말하는 **`SHADOWS-GOVERNED` 조건이 실제로 성립한 상태**다(peer 자가 루트 3건 = 현역 2 + 이 잔여물). `prompt`는 외부효과 스킬이 아니라 프롬프트 자산이라 승인 게이트 우회는 아니고 **peer가 낡은 사본을 쓰는 문제**지만, 다음 감사 리포트에 `SHADOWS-GOVERNED`로 뜨는 것은 오탐이 아니라 설계대로의 탐지다 — 그 사본의 SKILL.md에는 `author: autophagy-agents` 마커가 없어(리포 원본에도 없다) 새 분류기가 `foreign`으로 판정해 fail-closed로 차단한다 — 설계대로의 동작이지만 원인이 오래된 잔여물이다(2026-08-01 배포 잔재, 모드 775) → 소유자가 `sudo -n -u peer rm -rf ~peer/.hermes/skills/prompt` 한 번으로 정리하면 이후엔 자가 치유된다(정리 수리가 이번 PR에 포함). 다른 5종은 이번 검증에서 실제로 정리됐다(봉인된 `coordination` 잔여물 포함). **심각도 낮음(해당 스킬 1종 배포만 지연)**.
  ↳ 재판정(2026-09-19, D048): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **노드의 `/root/.hermes/node.toml`을 확인할 수 없다 — 자동 재개가 이것에 달려 있다** — 승인 재개 헬퍼는 `env -i HOME=/root`로 파이프라인을 돌리므로(`autophagy-resume-deploy:60`) 노드 설정을 `/root/.hermes/node.toml`에서 읽는다. 그 파일이 없으면 시드 기본값(`peer_attest_mode = signed`)으로 해석돼 `discord` 바인딩 레코드와 어긋나고, 소유자가 ✅를 눌러도 마운트가 조용히 실패한다. 이번 5종은 결국 착지했으나 **오케스트레이터는 root 읽기 권한이 없어 그 파일의 존재를 확인하지 못했다** → 다음 배포 전에 소유자가 존재·내용을 한 번 확인하고, 없으면 워크스테이션과 같은 내용으로 만든다. **현재 배포는 동작 · 심각도 중간(다음 자동 재개가 원인 모르게 멈출 수 있다)**.
  ↳ 재판정(2026-09-19, D049): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **번들 카탈로그는 제거했지만 재시딩 경로가 완전히 닫힌 것은 아니다** — agent·peer 모두 `hermes skills opt-out --remove`로 정리하고 `~/.hermes/.no-bundled-skills` 마커를 남겼다. 다만 (a) 빈 카테고리 디렉터리와 `.bundled_manifest`는 그대로 남아 있고, (b) 마커를 존중하지 않는 경로(`hermes update`·프로필 재생성 등)가 있으면 다시 시드될 수 있다 → `hermes update` 직후 `hermes skills list`로 builtin 수를 확인한다. 원장 쪽 방어는 이미 있다(`.bundled_manifest` 기준 제외, PR #113). **현재 정상 · 심각도 낮음(재발 시 감사 리포트가 아니라 프롬프트 크기 문제)**.

  ↳ 재판정(2026-09-19, D050): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 승인된 배포 마운트가 아직 실물로 확인되지 않았다 (2026-08-17)

장벽 3겹(설정 미해석 → 재개 재게시 거부 → 릴리스 `__pycache__` 오염)을 차례로 걷어냈고 전제 조건은
모두 확인했으나, 실제 마운트는 백오프(약 45분) 만료 후에야 일어나므로 세션 안에서 보지 못했다.

- **`todo`·`mail` 마운트가 미확인이다** — 재개 헬퍼의 승인 바인딩 전달(설치본 반영 확인), 리컨실러의
  `PYTHONDONTWRITEBYTECODE`(유닛 반영 확인), 릴리스 `__pycache__` 0개까지 전부 확인했고 백오프도
  `attempt 11 → 1`로 리셋됐다(릴리스 변경으로 실패 지문 갱신). 그러나 `readlink live/todo` 는 여전히
  `aff99eb0…` 다 → 다음 재개 틱 뒤 `readlink /srv/autophagy-skills/live/{todo,mail}` 로 확인하고,
  또 실패하면 `journalctl -u autophagy-supply-chain-watch` 가 다음 장벽을 가리킨다(오늘 세 번 그랬다).
  **소유자 ✅ 2건과 pending 레코드는 보존됨 · 심각도 중**.
  ↳ 재판정(2026-09-20, D051): NODE-VERIFIED-DIGEST — 소유자 승인 v1.9.2의 `deploy_all.sh --verify`가 exit 0으로 전량 마운트 일치를 확인했다. 과거의 마운트 미확인은 닫되, digest 영수증을 실행 정상 증거로 확대하지 않는다([실측](qa/KFX-LIVE/summary.md)).
- **mail 의 게시↔판독 레이스가 재현되는지 미검증이다** — 증명 게시 1초 뒤 `absent` 판정이 반복되던
  현상은, 재개가 재게시 대신 곧장 검증·마운트로 가게 되면서 그 재증명 루프 자체가 사라졌을 수 있다.
  그러나 실물로 확인하지 않았다 → 위 마운트 확인 시 `REJECTED: valid peer attestation absent` 가
  다시 나오는지 함께 본다. **심각도 낮음(재발 시 별도 수정 필요)**.

증적: PR #132·#133, `journalctl -u autophagy-supply-chain-watch`(2026-08-17 06:11 `RELEASE-STORE-BLOCK`).

  ↳ 재판정(2026-09-19, D052): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 메모리 승격 확인 종결 H4 — 라이브 14건은 OWNER 인계 (2026-08-05)

- **코드와 dry-run 계약만 완성됐고 라이브 14건은 의도적으로 건드리지 않았다** → PR 머지와 배포 뒤 OWNER가 `closure_cli --dry-run`의 `CLOSE`·`UNBOUND`·`ORPHAN` 원장을 검토한 다음 기존 큐레이터 tick으로 정리한다. **동작·보안 결함 아님 · 심각도 중(운영 인계)** — 이 작업에서 실 Discord 편집·실 배포·라이브 정리는 금지 범위다.
  ↳ 재판정(2026-09-19, D053): OWNER — 실제 dry-run은 종결 후보 12·바인딩 불가 8·분류된 고아 0건이다. 카드 편집·초안 회수 없이 관측만 했으며 라이브 종결 승인은 그대로 남는다.
- **`abandoned`는 ⛔ 시점에 saved 초안이 unlink되어 교차 바인딩이 소멸한다** → 현재는 항상 `UNBOUND`로 열거하고 편집·archive 0회를 보장한다. 미래 취소 경로가 삭제 전 archive하도록 바꾸는 일은 kanban-routing 소유 계획과 조율한다. **안전 우선 fail-closed · 심각도 낮음(정리 자동화 한계)**.

증적: `.omo/evidence/fs2/task-4-parallel-followup-sweep-2.txt`

  ↳ 재판정(2026-09-19, D054): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 스킬 배포 파이프라인이 3겹으로 막혀 있었다 (2026-08-04 실측 · 전부 해소)

FS3 K2-A는 기존 `release_helper_probe.sh`의 실설치 자산 검사를 랜딩 출력에도 재사용했다.
금지된 `deploy-skill.sh` 실행 없이 저장소 회귀와 읽기 전용 프로브 계약만 검증했다.

위 「마운트된 스킬 5종…」의 **근본 원인**이다. 재배포를 실제로 시도해서야 드러났다 — 마운트가 낡은 것이 아니라 **마운트할 수가 없었다**. 하나를 풀자 다음 것이 드러나길 세 번 반복했고, 세 개 모두 **이번 스윕(2026-08-03)이 직접 만들었거나 드러낸** 것이다. 실제 재배포 없이는 세 겹 중 어느 것도 보이지 않았다.

- **세 겹 모두 “실제로 돌려보기 전에는” 보이지 않았다** — 유닛 3112건·ruff·보드 conformance가 전부 green이었고 릴리스도 최신이었다. 공통 성질은 **노드 설치본·외부 CLI 출력·파일 모드처럼 레포 밖에 사는 상태**에 의존한다는 점이다. **심각도 중** — 조치: 배포 경로에 주기적인 스모크(예: 하루 1회 `--sandbox-only` 드라이런)를 두어, 다음 실배포가 아니라 그 드라이런이 먼저 깨지게 한다.

  ↳ 재판정(2026-09-19, D055): OWNER — 타이머·서비스는 설치됐으나 disabled·inactive이며 실행 시각은 비어 있다. 설치와 실제 일일 실행을 구별하고 자동 활성화하지 않는다.
## 처리 끝난 승인 메시지가 소유자 DM에 쌓인다 (2026-08-04 실측)

소유자가 “1개만 승인했는데 나머지는 안 보인다”고 보고해 조사한 결과다.

- **메모리 승격 확인 메시지 14건이 전부 살아있다 — 이미 처리된 것도 지워지지 않는다** — `wiki-gate/drafts`에 `memory-promoted-*` 초안이 14건(전부 `status=saved`), 대응 Discord 메시지도 14건 전부 ALIVE이며 그중 10건에는 **소유자 ✅가 이미 달려 있다**. 반면 `memory-curator/state.json`의 승격 레코드는 10건이고 상태는 `reconciled` 6 · `abandoned` 4로 **파이프라인은 정상 종결됐다**. 즉 결정은 소비됐는데 그 사실을 알리는 메시지가 그대로 남아, 소유자 DM이 “대기 중인 승인”처럼 보인다. **동작·보안 결함 아님**(안전 불변식은 지켜졌고 `USER.md`는 1086자로 이미 회수 반영된 값) **· 심각도 중(UX·관측성)** — 소유자가 무엇을 더 눌러야 하는지 알 수 없게 된다. 조치: `reconciled`·`abandoned`로 종결된 승격의 확인 메시지를 삭제하거나 처리됨 표시로 바꾸는 종결 단계를 두고, 초안 레코드(`saved`)도 함께 회수한다. 삭제는 외부효과라 소유자 승인 경로를 거친다.
  ↳ 재판정(2026-09-19, D056): OWNER — 현재 원격 카드 12건의 해시 바인딩을 확인했다. 과거 14건 전체의 잔존·소멸이나 이미 종결된 문구 여부까지 증명한 것은 아니다.
- **초안 14건 vs 승격 레코드 10건의 불일치** — 4건은 `state.json`에 대응 레코드가 없다(조회 시 반응 사용자 목록도 비어 있었다). 레거시 또는 중단된 시도의 잔재로 보인다. **심각도 낮음** — fail-closed라 임의 실행은 없다. 조치: 위 종결 단계를 만들 때 고아 초안 판별을 함께 넣는다.

  ↳ 재판정(2026-09-19, D057): PARTIAL — 현재 saved+승격 마커 기준 고아는 0건이다. 별도로 저장 초안 부재 6·메시지 id 불일치 2건은 남아 있어 추측 재바인딩·삭제하지 않았다.
## 마운트된 스킬 5종이 릴리스보다 낡았다 — 머지된 수정이 미발효 (2026-08-04 실측)

OWNER 체크리스트 9번·F4 판정 중 발견. `skill_mount_drift.py`를 라이브에 직접 돌렸다.

- **`budget`·`calendar`·`coordination`·`mail`·`wiki` 다섯 종 전부 `SKILL-STALE`이다** — 릴리스와 마운트 digest가 전면 불일치한다(예: mail 릴리스 `019a405f…` vs 마운트 `091ddd36…`). 즉 **코드에는 있지만 프로덕션에서 도지 않는 수정이 다섯 스킬에 걸쳐 쌓여 있다** — 429 백오프(PR #31·#32), G2 승인 판정 단일화(PR #50), G5 다이제스트 JSON 계약 하드닝(PR #47)이 여기 해당한다. **동작·보안 결함은 아니다**(구버전이 정상 동작 중이고 게이트는 fail-closed) **· 심각도 중** — 고쳐놓은 결함이 계속 재발한다는 뜻이다. 조치: `automation/deploy-skill.sh`로 다섯 종을 재배포한다(스킬당 소유자 ✅ 1회, 총 5회). 배포 뒤 `readlink /srv/autophagy-skills/live/<skill>`이 릴리스 digest와 같아지는지로 판정한다.

  ↳ 재판정(2026-09-20, D058): NODE-VERIFIED-DIGEST — v1.9.2 전량 검증 exit 0과 정상 헬스체크의 skill-mount PASS로 현재 마운트 지문을 확인했다. 8월의 SKILL-STALE 관측을 현재 장애로 재사용하지 않으며, 실행 검증은 별도로 남긴다([실측](qa/KFX-LIVE/summary.md)).
## 배포 미러 임시 워크트리 누수 — trap 정리 실패 (2026-08-04 실측)

OWNER 체크리스트 1번(`.git` 권한 회수) 조사 중 발견. 권한 자체는 해소됐다(2775→2755, 그룹쓰기 1629개→0, agent 소유 356개→0).

- **수리 워크트리 1건이 스테이징된 미커밋 변경 12건과 함께 남아 있다** — `/tmp/t_6f29fb9b-review`(브랜치 `fix/memory-relocate-single-approval-t_6f29fb9b`, HEAD `1c3c33e`). HEAD는 origin/main 조상이고 **미착지 커밋은 0건**이라 손실된 커밋은 없지만, 인덱스에 `memory_relocate` 계열 12파일이 스테이징된 채 남아 그 내용이 현재 origin/main과 다르다(같은 주제의 수리는 `docs/patch/2026-08-02-memory-relocate-approval-channel-binding.md`로 이미 반영됨 — 이 인덱스는 그 시점의 중간 스냅샷으로 보인다). **동작·보안 무관 · 심각도 낮음**(15MB 점유). 「다른 세션의 미커밋 작업은 되돌리지 않는다」에 따라 **의도적으로 보존했다**. 조치: 소유자가 인덱스 diff를 확인해 버릴지 판단하고, 버린다면 `git diff --cached HEAD`를 패치로 뽑아 `/srv/autophagy-private/`에 보관한 뒤 제거한다.
  ↳ 재판정(2026-09-19, D059): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **수리 자동화가 미러에 남긴 로컬 브랜치 다수** — `task/*` 3건·`kanban/*` 8건·`wt/*` 2건·`fix/*` 1건·`repair/*` 1건이 미러의 로컬 ref로 남아 있다(전부 미착지 0건 확인). **동작 무관 · 심각도 낮음**. `backup/pre-*`·`pre-realign-*`는 정렬 사고 대비로 **의도적으로 남긴 것**이므로 제외한다. 조치: 수리 라이프사이클이 종료 브랜치를 정리하도록 하거나, 주기적 정리 기준을 정한다.

  ↳ 재판정(2026-09-19, D060): PARTIAL-SOURCE-OWNER — 작업 클론의 예방 코드는 해소했지만 기존 미러 정리는 소유자 몫이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 메모리 재배치 승인 채널 바인딩 유실(MC-4) 수리 중 발견한 후속 과제

수리는 `docs/patch/2026-08-02-memory-relocate-approval-channel-binding.md`. 노드 자율 제안 경로에서만 드러난 결함이다.

- **`USER.md`는 cap의 79.0%(1086/1375자)다** — 승격 진행분을 반영한 실측이며 97.9%라는 옛 수치는 폐기한다. **심각도 중** — 유일한 회수 경로는 트윈 승격이다. → 소유자가 대기 중인 승격 초안 6건에 ✅/⛔를 처리한다(전부 승인 시 754자 회수).

  ↳ 재판정(2026-09-19, D061): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 배포 스냅샷 + 불변 런타임 루트(DG-2~DG-6) 작업 중 발견한 후속 과제

기능은 DONE「배포 스냅샷 + 불변 런타임 루트 (DG-2~DG-6)」 참조, 계획 `.omo/plans/deploy-snapshot-runtime.md`

- **DG-5 4.4(수리 systemd 유닛 이관)는 부분 해소 상태다** — `docs/qa/DG-5/rollout-partial.txt`는 4.4를 defer로 기록했으나, 이후 repair-report-rollout의 coordination amendment로 `autophagy-repair-agent.service`는 `/srv/autophagy-agent-current`로 이관됐다(라이브 확인: `WorkingDirectory=/srv/autophagy-agent-current`, `NeedDaemonReload=no`). **정정(2026-08-10)**: `autophagy-repair-approval-watch.service`는 "미설치"가 아니다 — 도입 커밋 `aae36d2`부터 의도적으로 **system 스코프**(`/etc/systemd/system/` + `.timer`, `User=ops`)로 설치돼 있으며, 이전의 "빈 `FragmentPath`" 관측은 잘못된 `systemctl --user` 조회의 산물이었다. 진짜 문제는 **설치본 내용이 낙았다**는 것이다 — `fdc995e`(DG-5)도 `7ea6a8c`(미러 쓰기 제거)도 반영되지 않은 pre-`7ea6a8c` 바이트라 `ReadWritePaths`에 `/srv/autophagy-agents` 미러 쓰기가 남고 런타임도 미러를 가리켰다. **심각도 중간** — 이 유닛은 이번 rollout의 주 보고 경로(cha ✅ → approval-watch → complete/reopen → enqueue)를 타므로 활성화 전에 수렴되어야 한다. 조치: 2026-08-10 별도 system-scope 수렴 runbook(소유자 root 게이트)으로 설치본을 post-DG-5 `BASE` 바이트로 맞춘 뒤 `repair-report-rollout` B1.2 ⓔ·F4가 system 스코프로 검증한다(근거 사슬: `.omo/notepads/repair-report-rollout/decisions.md`).

  ↳ 재판정(2026-09-19, D062): PARTIAL — 설치된 승인 워처는 WorkingDirectory·ExecStart·PYTHONPATH 모두 릴리스 런타임이고 미러 쓰기 경로가 없으며 NeedDaemonReload=no다. repair-agent 유닛은 not-found로, 두 소스 유닛 모두 설치됐다고 주장하지 않는다.
## G5 — 스킬 위생 + mail 다이제스트

구현 내용은 [소개](기능소개/승인표면-메일다이제스트-정리.md). 아래는 코드 완료 뒤에도 소유자 권한·라이브 관측이 필요해 이번 PR에서 실행하지 않은 체크리스트다.

- **[배포 체크리스트·미완료] 라이브 cron은 아직 `--deliver local`로 관측됐고 코드만 `--deliver discord`로 수렴해 있다** → G1 착지 뒤 owner-approved 세션에서 mail·calendar·coordination·wiki를 각각 해시 승인·재배포하고, `skills/mail/deploy.sh` 실행 후 cron의 `Deliver: discord`를 확인한다. 07-31 누락분은 dry-run 건수 확인 뒤 한 번만 재전송한다. **심각도 중** — 배포 전까지 다음 실패 알림도 조용할 수 있으나 이번 세션은 노드 동작을 전혀 시도하지 않았다.
  ↳ 재판정(2026-09-20, D063): PARTIAL — 실제 mail-triage-watch·mail-daily-digest는 active·no-agent·deliver=discord이며 최근 실행은 ok였다. 목적지 해석·스킬 전체 배포·07-31 누락분 재전송은 이 관측으로 증명하지 않는다.
- **[배포 체크리스트·미완료] vendored mailon의 미사용 import 7건은 소스에서 제거됐지만 라이브 mailon 릴리스에는 아직 반영되지 않았다** → 수정 커밋이 `origin/main`에 착지한 뒤 별도 owner-approved mail 재배포를 요청하고 `~/.hermes/mailon-runtime/current`가 새 vendor digest를 가리키는지 확인한다. **동작·보안 문제 없음 · 심각도 낮음(배포 대기)** — 제거된 import는 실행에 쓰이지 않았고 unit 3391건·vendor offline 58건·저장소 전체 Ruff가 통과했으며, 이번 repair-report rollout에서는 승인 게이트가 필요한 외부효과를 수행하지 않는다.
  ↳ 재판정(2026-09-19, D064): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ 처리(2026-10-01): 이 계획의 사전 점검 실측(todo 1)에서도 노드 메일 런타임은 아직 릴리스 vendor 와 다르다 — `current` 의 src digest `78ee65a2371c687c` · 릴리스 `1854c9815a26af2d`, 드리프트 프로브는 `direction=unknown`. 반영은 아직이다. 달라진 것은 수렴 주체다: 메일 런타임이 `v2:derived` 선언이 되어 릴리스 판정이 vendor·requirements 신원을 대조하고 어긋나면 `skills/mail/deploy.sh` 를 돌린다(`1d08d8c0`), 같은 신원의 재배포는 쓰이는 디렉터리를 지우지 않는 무동작이다(`f5beb7c8`). 노드 수렴은 todo 1(소유자 승인) 또는 다음 릴리스의 전량 반영이다.
- **[canary 체크리스트·미완료] GLM payload 전달 여부는 worktree에서 증명할 수 없다** → owner 세션에서 비민감 합성 입력으로 비-4xx·유효 JSON·reasoning tokens 0을 확인한다. 기존 proxy가 필드를 버린다는 증거가 생길 때만 `configs/litellm-staging/config.yaml`을 별도 변경한다. **심각도 중** — 현재는 fail-open과 항목 재시도가 동작을 보전하며, 이 PR은 gateway config를 수정하지 않았다.
  ↳ 재판정(2026-09-19, D065): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[조사·노드 확인 미완료] 리포 증적상 mail은 pending 0이고 최신 skill-gate 잔재 11종 목록에도 mail·wiki가 없다** → 재배포 직전 노드에서 두 스킬의 실제 pending 상태를 read-only로 다시 확인한다. 성공한 배포는 stage 4 직후 정확한 `(skill, hash, message_id)`만 `consume`하므로 새 요청은 자동 정리되지만, 이미 결정된 구레코드가 발견되면 무조건 덮어쓰지 않고 소유자 판단으로 `skill_gate abandon`을 사용한다. **보안 문제 아님·심각도 낮음** — fail-closed 잔재가 있으면 배포가 멈추는 가용성 문제다.

  ↳ 재판정(2026-09-20, D066): VERIFIED-SNAPSHOT — 배포 게이트의 mail.json·wiki.json pending 경로 모두 ENOENT였다. 권한 거부가 아닌 시점 관측이며 실제 배포 게이트는 실행 때 다시 확인한다. 요청·consume·abandon은 호출하지 않았다.
## H3 배포 위생 도구 반영 후 OWNER 실행 항목

기능은 [소개](기능소개/H3-배포-위생.md).

- **일일 sandbox 스모크는 코드·격리 검증까지만 완료되어 노드 timer가 아직 설치되지 않았다** → PR 머지 뒤 OWNER가 `provision-deploy-smoke.sh`를 실행하고 첫 tick의 `~/.hermes/deploy-smoke/tick.json`을 확인한다. **배포 안전 관측성·심각도 중** — 설치 전에는 다음 실배포가 여전히 첫 노드 종단 검증이다.
  ↳ 재판정(2026-09-19, D067): OWNER — 타이머가 disabled이며 기본 tick.json은 2026-08-20 파일이다. 메타데이터만 확인했고 과거 성공이나 현재 정상 주기를 추정하지 않는다. 활성화·새 실행 확인은 남는다.
- **미러 writer inventory와 로컬 브랜치 후보는 실제 노드에서 실행하지 않았다** → OWNER가 read-only inventory와 `docs/guide/미러-로컬-브랜치-정리.md` 기준을 적용해 검토하며, 삭제는 별도 OWNER 판단으로 수행한다. **동작·보안 영향 없음·심각도 낮음** — 도구 개발 중 실제 미러·ref는 변경하지 않았다.

  ↳ 재판정(2026-09-19, D068): OWNER — 현행 미러 정리 가이드가 실제 writer inventory와 브랜치 삭제를 OWNER 전용으로 명시한다. 그 경계를 유지해 inventory·ref 후보 조사·삭제를 실행하지 않았다.
## H1 — 헬스체크 릴리스 관측성 OWNER 인계 (2026-08-05)

기능은 [소개](기능소개/헬스체크-릴리스-관측성.md), 증적은 `.omo/evidence/fs2/task-1-parallel-followup-sweep-2.txt`다.

- **`RELEASE_STALE_PROBE_ENFORCE`는 기본 0(WARN)이다** — 독립 프로브가 먼저 shadow 관측되도록 강제 승격하지 않았다. **릴리스 stale 단독 경로는 경고만 남는 롤아웃 단계 · 심각도 중** → OWNER가 오탐 여부를 확인한 뒤 원장에 승격 결정을 기록하고 1로 전환한다.

  ↳ 재판정(2026-09-19, D069): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 플랫폼 운영자 매뉴얼(W-M3) 작성 중 발견한 후속 과제

F4 해소 기록(2026-08-21): 404 링크 제거와 신규 링크 conformance는 완료됐다. 남은 owner
action은 네 제외 런북을 공개 대상으로 승격할지 결정하는 것뿐이며 OWNER-29가 그 범위만 소유한다.

아래 불릿은 초기 131행과 결합된 회계 정본 원문이라 수정하지 않는다. 현재 열린 범위는 위
해소 기록의 owner action으로 대체됐다.

- **공개본에서 끊기는 문서 링크가 있다** — 공개 원장에 있는 매뉴얼들이 export 제외 문서를 링크한다: `manual-group-admin.md` → `managed-skill-channel.md`(기존), `manual-maintainer.md` → `operations.md`·`incident-response.md`·`reboot-recovery.md`(신규). 공개 트리에서는 그 대상이 존재하지 않아 404가 된다. **보안·동작 무관, 문서 탐색성만 영향 · 심각도 낮음** → 세 가지 중 하나를 고른다: (a) 해당 문서를 공개 대상으로 승격 (b) 공개되는 문서에서 링크를 걷어내고 산문으로만 언급 (c) export 시 제외 대상 링크를 검출하는 conformance 테스트를 추가해 최소한 새로 늘지 않게 한다. 신규 3건은 이번에 `*(개발 저장소 전용 — 공개본에 포함되지 않는다)*` 주석을 달아 오해만 막아 두었다.

증적: `.omo/notepads/public-release/learnings.md`의 「[2026-08-15] Task: W-M3 플랫폼 운영자 매뉴얼」 항목.

  ↳ 재판정(2026-09-19, D070): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## FS3 K4-a 공급망·그룹 채널 위생 후속 과제

F4 해소 기록(2026-08-21): posting journal의 감사형 복구 절차와 CLI는 완료됐다. 남은 owner
action은 라이브 Discord 메시지가 `delivered`인지 `not-delivered`인지 판정하는 것뿐이며
OWNER-37이 그 외부 상태 판정만 소유한다.

아래 불릿은 초기 131행과 결합된 회계 정본 원문이라 수정하지 않는다. 현재 열린 범위는 위
해소 기록의 owner action으로 대체됐다.

- **공지 전송 결과가 모호한 posting journal은 자동으로 지울 수 없다** — 재시도하면 이미
  전달된 공지를 중복 게시할 수 있어 이번의 외부효과 0 검증만으로 안전한 복구를 결정할
  수 없다 → 소유자가 해당 Discord 메시지 존재를 확인한 뒤 journal 유지·제거를 선택하는
  절차를 정한다. **발행·구독 동작 영향 없음 · 심각도 낮음(공지 재개에 사람 판단 필요)**.

증적: `.omo/evidence/fs3/task-11-parallel-followup-sweep-3.txt`

  ↳ 재판정(2026-09-19, D071): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 연구계획서 자동생성 v2 실증 중 발견한 후속 과제 (2026-08-23)

완료 기능은 [연구계획서 자동생성](기능소개/연구계획서-자동생성.md)이다. 실제 문서 본문·개인정보·자격증명은 이 기록에 포함하지 않는다.

- **실제 쪽수 acceptance가 estimate-tier에 머문다** — 현재 워크스테이션 판정이 권위이고, LibreOffice 24.2는 Java 21과 H2Orestart v0.7.13을 설치한 뒤에도 seed HWPX를 읽지 못해 원인을 밝히지 못했다 → Windows/한글(Hancom) PDF 러너를 도입할 때 실제 쪽수 판정을 정밀 acceptance로 승격한다. **현재는 소유자 열람 확인으로 대체하는 비차단 항목 · 영향 범위: 페이지 수 검증 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D072): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **실 이미지 API의 비용·자격증명 경로가 아직 검증되지 않았다** — 워크스테이션에 OPENAI 키가 없어 images 단계는 fake로 검증했고 월 10달러 소프트캡 뒤의 실제 지출 관측도 없다 → 키를 승인된 비밀 경로로 조달하고 월 지출 모니터링·경보를 운영한다. **영향 범위: 실 그림 생성과 비용 통제 · 심각도 중**.
  ↳ 재판정(2026-09-19, D073): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **정규 picture-carrier fixture가 없다** — 현재 픽스처는 공개 honeypot 템플릿 유래 파생본이라 한/글이 직접 만든 carrier와의 호환성을 대표하지 못한다 → 한/글에서 수작업으로 정규 fixture를 만들고 구조·렌더 회귀의 기준으로 고정한다. **영향 범위: HWPX 그림 호환성 · 심각도 중**.
  ↳ 재판정(2026-09-19, D074): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **노드의 완전한 live E2E가 남았다** — 워크스테이션 E2E의 research·images·draft는 fake/replay였고 그 결과 인용도 5건으로 목표 15건보다 적었다 → 노드에서 Hermes 딥리서치, 실 LLM 작성, 실 이미지 생성을 한 번에 재실행해 F3 인용 15건 이상과 자원 매핑 60% 이상을 확인한다. **영향 범위: 최종 콘텐츠 품질 게이트 · 심각도 높음**.

  ↳ 재판정(2026-09-19, D075): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 음성 녹취 회의록 자동화(speechtotext) 착지 후 남긴 것 (2026-08-25)

- **`Meet Recordings` 의 mp4 는 파일명에 확장자가 없다** — Drive 가 `rnm-yzkx-tub (…)` 형태로 저장해
  suffix allowlist 가 하나도 보지 못한다. 지금은 감시 대상이 아니라 무해하지만, 그 폴더를 붙이려면
  mimeType 판별이 필요하다(Gemini 회의록과 중복 여부도 함께 판단). **미사용 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D076): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`PLAUD/raw` 18건은 이미 전사된 `.txt`** — 기기가 STT 를 끝낸 결과물이라 전사 단계가 불필요하다.
  조치: 붙인다면 `meeting ingest` 직행 경로가 맞다. **미사용 · 심각도 낮음**.

  ↳ 재판정(2026-09-19, D077): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 화자 구분·문장 단위 출력 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · OWNER] 소유자·노드에서만 닫힌다.

- **화자 분리는 CPU, 전사는 GPU 로 갈려 있다** — whisper.cpp 는 CUDA 빌드(`build-cuda`)를 쓰지만 sherpa-onnx 는 CPU 빌드다. 긴 녹취에서 화자 분리가 전체 처리 시간의 하한을 만들 수 있고, 상한은 `SPEECHTOTEXT_DIARIZE_TIMEOUT`(3600초)에서 끊긴다. 조치: 실측 소요를 먼저 남기고, 필요해지면 sherpa-onnx GPU 빌드를 노드에 올린다(노드 작업). **영향 범위: 처리 지연뿐이며 타임아웃도 fail-soft(`DIARIZE-FAIL` 후 화자 없이 진행) · 심각도 낮음**.
  ↳ 처리(2026-09-03): 노드 작업 — 실측 소요를 남기고 필요해지면 sherpa-onnx GPU 빌드를 노드에 올린다


  ↳ 재판정(2026-09-19, D078): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## healthcheck 폭주 수리(PR #347) 중 발견한 인접 결함

> [이관 2026-09-03 · OWNER] 소유자·노드에서만 닫힌다.

- **healthcheck 상시 FAIL 4건이 PR #347 범위 밖에 남는다 → 노드 운영자(OWNER)가 폭주 해소 후 재판정하고, 남는 것만 조치한다.** `ops checkout mirrors origin/main`(mirror-dirty), `privileged release helpers match release`(HELPER-DRIFT `autophagy-converge-origin-main` → 노드에서 `sudo bash <release>/automation/provision-deploy-converge.sh`), `watcher wrappers match the release`·`runtime packages match the release`(RUNTIME-PACKAGE DIFF `memory_curator effects.py` → `automation/memory_curator/deploy.sh`)가 해당한다. 나머지 UNKNOWN 은 폭주 중 ssh 불통 탓일 가능성이 있어 폭주 해소 후 재판정한다. 영향 범위: 운영 드리프트, 보안 문제 아님, **심각도 낮음**.
  ↳ 처리(2026-09-03): 노드 운영자가 폭주 해소 후 재판정 — mirror-dirty 는 이번 스윕의 미러 동결 문구가, HELPER-DRIFT·RUNTIME-PACKAGE DIFF 는 provision-deploy-converge.sh·memory_curator/deploy.sh 실행이 닫는다


  ↳ 재판정(2026-09-20, D079): OWNER — 정상 스윕에서 root 헬퍼 `origin_snapshot.sh` 차이와 memory_curator 런타임 5개 DIFF·`notice.py` ABSENT를 확인했다. 워처 3개는 미배포가 아니라 조회 UNKNOWN이고, 미러 판정은 과거 dirty가 아닌 미릴리스 main과의 차이다. 기존 운영자 수렴 조건을 유지한다([원인·범위](qa/KFX-LIVE/summary.md)).
  [해소 확인 2026-10-01] memory_curator 런타임 — 워처 래퍼가 홈 사본 대신 릴리스 트리의 패키지를 import 하게 바뀌어 낡은 사본이 실행 경로에서 빠졌고, 그 사본은 `policy=retired` 로 선언됐다 — `1cd0051f`. root 헬퍼 — v1.14.0 컷 전에 소유자가 프로비저너를 다시 돌려 2026-09-30 재측정 `HELPER-DRIFT-PASS`(소유자 조치, 커밋 없음). **잔여**: 워처 3개의 조회 UNKNOWN 은 그대로 남는다.
## 후속 과제 스윕 4 착지 후 소유자만 닫을 수 있는 것 (2026-09-03)

- **[OWNER] 중앙 매니페스트(`configs/watcher-deploy-manifest.txt`) 바이트가 바뀌었다 → 노드에서 `automation/healthcheck_probe_wrapper.sh --install` 을 다시 돌리고, 새 배포기 `automation/cost-report/deploy.sh`·`automation/reminder_poller/deploy.sh`·`automation/repair/deploy.sh`·`automation/skill_generation/deploy.sh` 를 한 번씩 실행해 손배포 사본을 선언된 배포본으로 바꾼다.** 그 전까지 `healthcheck_wrapper_current` 프로브가 지문 불일치를 알린다(심각도: 중).
  ↳ 재판정(2026-09-20, D080): OWNER-PROBE-ASSET — 주 노드의 설치된 allowlist 입력 지문과 현재 생성기 지문이 다르다. RAG 노드는 header 조회 자체가 거부/불통으로 남아 부재를 단정하지 않는다. 전량 배포 영수증은 이 운영자 자산을 대신 검증하지 않으며, 재생성은 원래의 소유자 실행 절차로 남긴다([실측](qa/KFX-LIVE/summary.md)).
  ↳ 재관측(2026-10-01, RCB-1): 릴리스 v1.14.1 반영 뒤에도 두 노드 모두 `healthcheck_wrapper_current` 가 FAIL 이다 — 운영자 계정이 각 노드에서 `bash /srv/autophagy-agent-current/automation/healthcheck_probe_wrapper.sh --install <node>` 를 1회 실행한다. 명령·근거·함께 풀릴 증상은 [RCB-1 소유자 조치](qa/RCB-1/owner-actions.md) ①이 단독으로 소유한다.
  ↳ 처리(2026-10-02 · 해소): 래퍼와 배포기 둘 다 닫혔다. 래퍼 — 두 노드 모두 헬스체크 틱 `healthcheck-20261002T002001Z` 의 FAIL 뒤 `T002501Z` 부터 `healthcheck probe allowlist matches the checks` 가 PASS 이고, v1.14.4(`1fc11c72`) 위임 프로브도 두 노드 `WRAPPER-PASS` 다. 주 노드 설치본은 `--print <primary-node>` 출력과 바이트가 같아 `--install` 은 무동작이고, 10-01 에 함께 보이던 `WATCHER-DRIFT-UNKNOWN`·수리 티켓 rc 126 도 사라졌다. 배포기 — 네 배포기의 산출물(`send_cost_report.py`·`poll_reminders.py`·`repair_report_consume_watch.py`·`05-skill-generation`)은 중앙 매니페스트의 `required` 행이고, 노드 판정이 「전량 일치, 할 일 없음」, 영수증의 `pending_owner_actions`·`undeclared` 가 빈 목록이다. 근거는 [RCB-1 소유자 조치](qa/RCB-1/owner-actions.md) 「① 해소 확인」.
- **[OWNER] 08-29~09-01 미러 동결 동안 `checkout_mirrors_origin` 프로브가 실제로 FAIL·수리 티켓을 냈는지, 그리고 이번 릴리스 뒤 `state.json` 의 `mirror_state` 가 노드에서 값을 갖는지 노드 로그로 1회 확인한다.** 리컨실러 문구 보강은 코드로 끝났고 이것은 관측 확인이다(심각도: 낮음).
  ↳ 재판정(2026-09-20, D081): PARTIAL — 기존 상태 API가 mirror_state=behind와 release-backlog를 읽었다. 보이는 시스템 저널은 09-12부터라 08-29~09-01 티켓 발생은 증명하지 못했다. 저장 카운터를 이번 릴리스의 실패 횟수로 해석하지 않는다.
- **[OWNER] `~/.hermes/selfskill-audit/pending-overlaps.json` 의 미결 겹침(실측: `meeting-minutes-authoring`·`document-publishing` ↔ governed meeting·doctype·report)을 승격(governed 로 제출) 또는 폐기(`hermes curator archive`)로 결정한다.** 원장은 코드가 유지하고 결정만 남았다(심각도: 중).
  ↳ 재판정(2026-09-19, D082): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] obsidian write clone 전환 뒤 첫 fetch 로그에서 origin 이 `uploadpack.allowfilter` 를 허용하는지(blob 없는 fetch 가 실제로 작아졌는지) 와 `tmp_pack_*` 잔해가 더 생기지 않는지 1주 관측한다.** 거부되면 fetch 는 전량이지만 실패하지는 않는다(심각도: 낮음).
  ↳ 재판정(2026-09-19, D083): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 이번 스윕이 SKILL.md·스크립트를 바꾼 스킬(mail·calendar·budget·todo·wiki·coordination·speechtotext·doctype·meeting·patent-prep·procurement·prompt·proposal·recall·report·topics·hello-autophagy)은 릴리스 ✅ 1회로 전량 마운트된다 — `automation/release.sh` 실행.** 그 전까지 노드는 옛 마운트로 돈다("커밋됨 ≠ 배포됨")(심각도: 중).
  ↳ 해소(2026-09-04) 저장소 절반만: 브랜치 베이스가 릴리스 태그 v1.1.4 에 거리 0 커밋으로 앉아 있어 릴리스는 이미 끊겼다. 노드 마운트 실물 확인은 아직 아니며 소유자 몫으로 열려 있다.

  ↳ 재판정(2026-09-19, D084): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
# BLOCKED — 동결·벤더·외부 의존으로 지금 손댈 수 없다

## Discord 승인 카드 판본 이관의 동결 경계 (2026-09-20)

- **BLOCKED — 위키 카드만 기존 v2 유지**: 신규 카드 선택과 재생이 모두 수정 동결된
  `skills/wiki/scripts/wiki_gate.py` 안에 있다(`configs/freeze-inventory.txt`).
  승인 동작·마스킹에는 결함이 없고 가독성 이관만 남았다. 동결 해제 뒤 카드 v3를 추가해
  `owner-ko-v2`를 선택하고, 저장 v1/v2 바이트 재현을 검증한다. 게이트 밖에서 판본을 덮어쓰는
  우회는 하지 않는다. 증적: `.omo/evidence/kfx/card-readability/core.md`.

## 동결 해제·repair 재발 수리 착지 후 남긴 것 (2026-09-04)

> [이관 2026-09-04] follow-ups.md 에서 옮겨 왔다 — 네 행이 동결로 남아 이 저장소가 지금 손댈 수 없다. 해제는 「고칠 코드가 그 파일 안에 있는 행만」 기준으로 요청한다.

- **`automation/repair/repair_ops_core.py`·`repair_lifecycle.py`·`repair/systemd/*`·`gate-ledger-inventory.md` 는 동결로 남았다** — 이번 해제는 실제로 고칠 것이 그 파일 안에 있는 행(`repair_core.py`)과 원장이 거짓이던 행만 풀었다. 그 네 행에 걸린 BLOCKED 항목(감사용 전용 exit code·TOCTOU·삭제 패치 범위·유닛 ExecStart 인자·문서 지연)은 [follow-ups-deferred.md](follow-ups-deferred.md) 에 그대로 있다. 조치: 그 항목들을 실제로 고칠 사이클이 잡힐 때 같은 방식(고칠 코드가 그 파일에 있는 행만)으로 해제를 요청한다. **의도된 잔여 · 심각도 낮음**.

  ↳ 재판정(2026-09-19, D085): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## G8 — LOC 등록부

기능은 [소개](기능소개/loc-등록부-재측정.md), 작업 배분은 `.omo/plans/parallel-followup-sweep.md` §5 G8이다. 8개 코드 그룹이 전부 머지된 HEAD(`2383a92`)에서 전수 재측정해 **초과 29건 · 등록 3건**이던 상태를 등록 29건으로 맞췄고, LOC 게이트는 `EXCEPTION 29 / VIOLATION 0 · LOC RESULT: PASS`가 됐다.

- **진짜 분할 후보 5건은 "naturally large"가 아니라 "미룸 + 왜"로 등록했다** — `deploy-skill.sh`(550, 인자 파싱·실행 lease·ABI 스캔 분리 가능) · `triage_gate.py`(467, mailon/gmail 두 실행 백엔드) · `calendar_confirm.py`(437, 워처 HMAC 인가 블록 약 100줄) · `skill_gate.py`(429, G2 요청분) · `research_trends.py`(262, 수집/LLM/전달 계층). **현재 동작·보안 영향 없음 · 품질 부채** → 각각 별도 사이클에서 다룬다. 나머지 24건은 지금 쪼개면 오히려 나빠지는 것들이라 사유만 남겼다(vendored 6 · 승인 게이트 단일 절차 7 · argparse 표면 2 · 특권/주입 경계 3 · 순수 로직 5 · 셸 자기완결 1).
  **심각도 낮음** — 분할은 각 별도 사이클이 소유하는 품질 부채다.

  [해소 부분·이관 2026-08-31] 이 사이클에서 착수한 결과 — triage_gate 467→231(mailon/gmail 백엔드 분리) 완료 · calendar_confirm 437→300(HMAC 인가 `calendar_confirm_authz.py` 분리, 잔여 사유 재등록) 부분 · research_trends 는 분할했다가 되돌림(FS3 재생 핀 `.omo/evidence/fs3/task-12-already-fixed-probe.py` 의 `_loc_registry_entries` 가 그 파일의 250 초과+등록을 살아있는 불변식으로 고정) · skill_gate(717)·deploy-skill.sh 는 동결(todo 14 조율)+`test_public_release_hygiene` trust-root 정확일치 가드에 막힘. 되돌아오는 조건: 핀·동결 소유 사이클.


  ↳ 재판정(2026-09-19, D086): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 제안서 HWPX 품질 수리 중 남긴 것 (2026-08-26)

> [이관 2026-08-31] 아래 항목은 외부 저장소 kimm-docbot(고정 SHA 엔진) 내부 결함·개선이다 — 이 저장소에서 닫을 수 없고, KD 사이클 + 엔진 핀 전진이 되돌아오는 조건이다.

- **버전 디렉터리를 손으로 조립하면 `refine` 이 멈춘다** — `REFINEMENT-INPUT-ERROR: version manifest
  is missing`. (정정: 처음에 이를 "manifest 는 publish 가 만드는데 refine 이 더 앞이라 순서가 어긋난다"고
  적었으나 **틀렸다**. `manifest.json` 은 `VersionStore.promote(slug, staging, {parent, request,
  schema_version})` 이 버전 승격 시점에 쓴다 — 정상 파이프라인에는 항상 존재한다.) 이번처럼 기존 버전을
  복제해 손으로 만든 디렉터리에만 없다. 조치: 진단 편의를 위해 오류 문구에 "버전이 VersionStore 를 거쳐
  생성되었는지 확인" 안내를 덧붙인다. **정상 경로 영향 없음 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D087): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #522. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **대제목이 홀로 한 쪽을 차지한다** — 밴드는 그림을 쪽 상단에 두려고 선행 페이지 나눔을 강제하므로,
  각 섹션 대제목 다음이 곧 나눔이라 제목만 있는 쪽이 생긴다(33쪽 중 1쪽이 113자 수준). 조치: 섹션의
  첫 밴드에 한해 나눔을 생략하려면 `FIGURE_TOP_QUARTER` 계약을 함께 손봐야 한다. **분량 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D088): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **KPI 근거의 `env` 값이 인제스트에서 잘린다** — 코퍼스 원문 `env: TensorFlow 2.13 학습 서버로
  설정한다` 가 증거 단위 `fact` 에는 `env: TensorFlow 2` 로 들어온다. 플래너는 무죄다 — 같은 문장을
  직접 주면 `TensorFlow 2.13 학습 서버` 로 온전히 파싱된다(회귀 고정: `tests/contract/test_planner.py`).
  즉 절단은 corpus→evidence 변환 단계이고, 소수점 `2.13` 의 마침표를 문장 끝으로 본 결과로 보인다.
  조치: `converter` 의 문장 분할이 숫자 사이 마침표를 경계로 삼지 않게 고친다.
  **제출본 표에 불완전한 값이 실린다 · 심각도 중**.
  ↳ 재판정(2026-09-19, D089): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #503. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`remove_direct_paragraphs` 는 중첩 문단을 품은 문단을 지우면 XML 을 깨뜨린다** — 바이트 추출이
  `<hp:p>` 다음의 **첫 `</hp:p>`** 에서 끝나는데, 그림 캡션(`hp:caption`)이나 표 셀은 그 안에 문단을
  또 갖는다. 그런 문단을 삭제 대상에 넣으면 잘린 조각이 남아 문서가 파싱되지 않는다. 지금은 도달하지
  않는다 — 삭제 집합(안내문·양식 소제목)이 시드에서 유도해 동결돼 있고 거기에 중첩 문단이 없으며,
  앵커 보호가 한 겹 더 있다. 실제로 밟은 것은 QA 뮤테이션 도구뿐이다. 조치: 중첩 `hp:p` 를 가진
  문단이 삭제 대상에 들어오면 잘린 조각을 내보내지 말고 이름 있는 오류로 거부한다(같은 계열의
  `_body_template_ordinal` 은 kimm-docbot `4d29058` 에서 이미 건너뛰기로 닫았다).
  **현재 경로 무영향 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D090): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #510. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **윤문 폴백이 1/5 에서 3/5 로 늘었다** — 본문을 양식 목차(`## 1-1. …` 처럼 번호로 시작하는 제목)로
  재구성한 뒤 refine 의 청크 5개 중 3개가 불변식에 걸려 원문 폴백했다(직전 사이클은 1개). 폴백은
  재구성 원문을 그대로 두므로 손상은 없고 리포트에 `PASS_WITH_FALLBACK` 으로 정직하게 남지만,
  윤문이 닿는 범위가 줄었다. 조치: `refine-report.json` 의 청크별 실패 불변식을 확인해, 숫자로 시작하는
  제목 줄을 구조 필드로 오인하는지부터 본다. **문체 다듬기 범위 축소 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D091): NEEDS-EVIDENCE — 현재 판정에 필요한 원본·실측 증거가 부족하며 결함을 지어내지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`render_paragraph_blocks` 가 렌더 경로에서 빠졌는데 공개 API 로 남아 있다** — 그림 없는 경로가
  `figure_density.render_body_paragraphs` 로 옮겨가면서 이 함수는 이제 테스트만 부른다. 그런데 이웃 시드
  문단을 복제해 문자 속성을 물려받는 그 성질이 그대로라, 다음 사람이 "본문 삽입용 함수"로 다시 집으면
  같은 결함이 되살아난다. 조치: 남은 호출부(`tests/hwpx/test_seed_fill.py` 3곳)를 새 진입점으로 옮기고
  함수를 지우거나, 최소한 문자 속성을 필수 인자로 만든다. **현재 산출물 무영향 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D092): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #510. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **필수 섹션 별칭이 양식 제목 문자열에 묶여 있다** — `hwpx/validate._canonical_section_text` 가
  `1. 연구 배경 및 필요성` 같은 **문자열**을 별칭 목록에 들고 있어, 양식이 제목 문구를 한 글자만 바꿔도
  게이트가 조용히 "섹션 누락"으로 돌아선다. 시드에서 제목을 유도해 대조하는 편이 안전하다
  (`seed_fill.FORM_SUBHEADINGS` 가 소제목에 대해 이미 그렇게 한다). 조치: 대제목도 시드에서 유도해
  별칭을 파생시키고 회귀로 고정한다. **양식이 바뀔 때만 발현 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D093): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #510. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **플래너가 작업 패키지를 한 덩어리로 내고 `lead` 에 무관한 과제명을 담는다** — 이 코퍼스에서 36개월
  짜리 패키지 1개가 나왔고 그 `title` 은 네 구간을 모두 열거한 문장, `deliverables` 는 같은 문장, `lead` 는
  **다른 과제**(`**주요사업: 협력형배송체 (Cooperative Delivery)**`)였다. 마크다운 굵게 표시까지 그대로 남아
  있어 인제스트 단계에서 잘못된 블록을 집은 것으로 보인다. 추진 일정 표는 이번에 이름 절단으로 방어했지만
  근본 원인은 남아 있다. 조치: `contract/planner.py` 의 work package 추출이 어떤 증거 블록을 고르는지
  확인하고 과제 경계를 넘지 않게 고정한다. **표·표지 파생값에 잘못된 값이 실린다 · 심각도 중**.
  ↳ 재판정(2026-09-19, D094): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #503. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **추진 일정의 구간 파싱이 바로 위 결함에 얹혀 있다** — `pipeline/orchestrator._plan_phases` 는 작업
  패키지 서술에서 `N~M개월 <이름>` 을 읽어 행을 만든다. 그런데 그 서술이 네 구간을 한 문장에 열거하는
  것은 위 항목이 지적한 **플래너의 한 덩어리 출력** 때문이다. 플래너를 고쳐 구간별 패키지를 따로 내면
  문장에 `N~M개월` 이 사라져 파싱이 0건이 되고, 표는 조용히 패키지 순차 배치 폴백으로 돌아간다 — 그
  폴백은 지금도 정상 동작이라 실패로 보이지 않는다. 조치: 플래너를 고칠 때 구간 정보를 `WorkPackage` 의
  구조 필드(착수·종료 월)로 올리고, `_plan_phases` 는 그 필드를 먼저 보고 문자열 파싱을 폴백으로 내린다.
  **지금은 정상 · 플래너 수리 시 조용히 퇴행 · 심각도 중**.
  ↳ 재판정(2026-09-19, D095): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #503. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **윤문 예산이 입력에 적용되어 다듬을 여지를 남기지 않는다** — `char-budget` 불변식은 윤문 **결과**를
  절 예산의 1.1배와 비교하는데, 다듬기는 문장을 늘릴 수 있다. 예산에 맞춰 쓴 절(900자/예산 900)이
  윤문 뒤 1,013자가 되어 거부됐고, 전 절에 9~59% 여유를 남겨서야 통과했다. 조치: 입력 예산과 출력
  상한을 분리하거나, 입력 목표를 상한의 85% 수준으로 문서화한다. **저자가 이유를 알 수 없다 · 심각도 중**.
  ↳ 재판정(2026-09-19, D096): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #522. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **같은 상수가 두 저장소에 사본으로 존재한다** — 레이아웃 프로파일(쪽 목표·그림 슬롯·예산)이 KD
  `contracts/layout_profile.py` 와 AT `skills/proposal/scripts/proposal_ir.py` 양쪽에 있고, 이번에
  한쪽만 고쳐 두 번 실패했다. AT 는 KD 를 import 할 수 없다(고정 SHA 서브프로세스). 조치: 렌더 시
  엔진이 자기 프로파일을 산출물에 기록하고 AT 가 그것을 읽게 하거나, 두 표를 대조하는 적합성 검사를
  핀 체크아웃 기준으로 돌린다. **한쪽만 고치면 조용히 어긋난다 · 심각도 중**.
  ↳ 재판정(2026-09-19, D097): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #519. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **산출 HWPX 가 26MB 다** — 그림 15장을 1024x1024 원본 그대로 임베드한다. 조치: 렌더 시 본문 배치
  크기(120mm)에 맞춘 다운스케일 옵션을 둔다. **제출·공유 편의 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D098): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #510. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **양식이 요구하는 「기술이전 계획」을 본문에 쓰면 렌더가 막힌다** — `sensitivity-rules.yaml` 이 `기술이전`
  을 patent-sensitive 키워드로 잡고, patent-sensitive 는 `render` 목적지에서 거부된다. 양식의 4-1 안내문은
  "기업화 / 기술이전 / 추가 연구 / 표준화 계획"을 요구하므로 규약과 게이트가 정면으로 부딪힌다. 이번에는
  규칙이 아니라 문구를 「산업 연계」로 바꿔 우회했다(2026-08-26 이미지 프롬프트 `claims` 건과 같은 처리).
  조치: patent-sensitive 를 render 에서 거부할 이유가 실제로 있는지(렌더는 로컬 서브프로세스다) 재검토하고,
  없다면 destination 진리표를 고친다. **본문 어휘가 게이트에 갇힌다 · 심각도 중**.
  [이관 2026-08-31] 재검토 실측: render 경로가 KD 엔진 서브프로세스에 자격증명을 전달한다(`skills/proposal/scripts/proposal_render.py:26-29`) — KD 엔진이 LLM 클라이언트를 구성하지 않음을 확인하기 전에는 차단이 정당하다. 되돌아오는 조건: KD 확인 → dead-weight 전달 제거 → 진리표 재검토.

  ↳ 재판정(2026-09-19, D099): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #519. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 시나리오가 두 개의 환경 계약으로 실행된다 (2026-08-17 실측)

> [이관 2026-08-31] `deploy-skill.sh` 동결(todo 14 조율 범위)에 막혀 있다 — 해제 조율 후 credential-free 러너 분리를 진행한다.

FS3 K2-A는 비동결 `skill_review`·`peer_attest` 실행 환경을 공용 러너로 통일했다. 동결된
`deploy-skill.sh` 내부 구성과 자격증명 격리는 todo 14 조율 범위로 남긴다.

승인이 끝난 `todo` 배포가 `PEER-ATTEST-BLOCK` 으로 마운트되지 못한 사고에서 드러났다. 스킬 쪽 원인은
수정했으나(`skills/todo/scripts/scenario.sh`), 그 사고를 가능하게 한 구조는 그대로 남아 있다.

- **peer 검토는 자격증명을 든 계정에서 untrusted 시나리오를 실행한다** — stage 3 은 peer 의 `.env.secrets` 를
  로드한 뒤 같은 계정에서 시나리오를 돌린다. `env -i` 와 임시 `HOME` 은 파일시스템 접근을 막지 않으므로,
  악의적 시나리오가 실제 peer home 을 역산해 봇 토큰·서명키에 접근할 여지가 있다 → 시나리오 실행을
  credential-free 전용 UID 또는 동등한 mount namespace 로 분리한다. **현재 악용 사례 없음 · 심각도 중**.

증적: PR #139, peer 계정 실측(`scenario` False→True), Oracle 판정(단일 러너 권고).


  ↳ 재판정(2026-09-19, D100): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

## 수리 티켓 스윕-2 종결 중 발견한 후속 과제 (2026-08-17)

TRACK-A(PR #125) · TRACK-BC(PR #123) · TRACK-D(PR #129)를 착지시키고 보드·증적을 정리하며 남은 것들. 기능은 [todo 소유자-DM 승인 경로](기능소개/todo-승인-경로.md) · [승인 게시 복구와 강화 저널](기능소개/승인-게시-복구와-강화-저널.md) · [2-store 메모리 재배치](기능소개/2-store-메모리-재배치.md).

- **`docs/guide/gate-ledger-inventory.md`가 이번 변경만큼 낡았다** — 그 문서는 스윕-2 freeze 목록에 있어 이번 사이클에서 손대지 않았다(변경 0 확인). 그 사이 `todo` 승인이 `~/.hermes/todo-approvals/` 아래 approval store·lease·posting-journal 디렉터리를 새로 쓰고, 메모리 재배치의 posting journal 레코드가 3필드에서 5필드(`message_id`·`channel_id` 추가)로 늘었다 → freeze가 풀리면 등록부에 이 경로·권한(0600/0700)과 레코드 스키마를 반영한다. **경로 계약은 코드가 정본이라 동작 영향 없음 · 심각도: 낮음(문서 지연)**.
  ↳ 재판정(2026-09-19, D101): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **수리 systemd 유닛의 `ExecStart`에 티켓 인자가 없다(현재도 유효한 잠복 관측)** — `autophagy-repair-agent.service`는 `repair_ops_cli.py`를 인자 없이 띄우지만 CLI는 티켓 id 하나를 필수로 요구한다. 현재 유닛은 static/inactive이고 실제 승인 워처는 CLI에 티켓 id를 직접 넘기므로 라이브 장애는 아니다. freeze가 풀려 이 유닛을 활성 경로로 쓸 때 큐 래퍼 또는 `%i` 템플릿으로 인자를 공급하고 회귀 검사를 추가한다. **동결 파일은 읽기만 함 · 심각도: 중(직접 기동 시 즉시 실패)**.

  ↳ 재판정(2026-09-19, D102): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 스윕 3차·개인 서버 대화 채널 후속 과제 (2026-08-17)

완료 기능은 [개인 서버 대화 채널](기능소개/개인서버-대화-채널.md)과 [기관메일 발신자·전체 폴더·검색](기능소개/기관메일-발신자-전체폴더-검색.md), 완료 수리는 [peer trust-root 진단 분리](patch/2026-08-17-skill-gate-peer-trust-root-diagnostic.md)다.

- **Discord의 중간 진행·도구 출력 억제는 Hermes vendor 수정이 필요하다 (`t_db6a60e8`)** — repo의 protocol transport는 일반 agent turn 렌더링을 소유하지 않아 동결 경로를 우회해도 해결되지 않는다 → vendor freeze가 풀린 별도 계획에서 gateway/Discord adapter 또는 `hermes_compat` 패치와 회귀를 함께 설계한다. **현재 계획에서는 BLOCKED-freeze · 심각도: 중(대화 표면의 불필요한 내부 상태 노출)**.

  ↳ 재판정(2026-09-19, D103): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 배포 가드 보강(DG-7) 작업 중 발견한 후속 과제

본 작업은 `land.sh`의 부수 push와 provenance 디렉터리 blind spot을 닫았다. 아래는 같은 작업에서 드러난 잔여부채다.

- **`gh pr create`가 문서에만 있고 구현이 없다 — BLOCKED 유지, 조율안 산출됨(2026-08-05, H7)** — `AGENTS.md`「수리 반영 경로 규칙」은 브랜치 push 후 PR 생성을 요구하지만 코드에 호출이 0건이다(2026-07-31 선례: 브랜치만 push되고 PR이 없어 cha가 머지할 방법이 없었다). **유실 위험·심각도 중.** 이음새 `repair_ops_work_clone.py`는 비동결이나 (a) 동결된 호출자 `repair_ops_cli.py:163`이 `branch`·`push_error`만 계약에 실고, (b) 실제 소실 경계는 그 앞의 승인 워처다 — `repair_ops_reaction_watch.py:74-86`이 자식 stdout을 `capture_output`으로 삼킨 뒤 불리언만 반환하고 `main()`(214-230)은 항상 0을 반환하므로 `pr_error`는 journal에도 남지 않는다. (c) 자격증명 배선도 공유 `/etc/autophagy/repair-approval.env`(root:ops 0640, 재조정 유닛과 공유)에 손대야 한다. 조치: 구현 전 freeze 해제 조율이 선행 조건이며, 해제 조건 3절(결과 계약 확장·전용 drop-in 배선·비대화식 gh 스케치)을 [조율안](guide/수리-PR-자동생성-조율안.md)으로 명세했다 → `.omo/plans/repair-report-core.md` 소유자 결정 대기.
증적: `docs/qa/DG-7/summary.txt`

  ↳ 재판정(2026-09-19, D104): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #515. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 승인 내용 바인딩(RTS-4) 작업 중 발견한 후속 과제

기능은 [소개](기능소개/수리-승인-내용-바인딩.md), 증적 `docs/qa/RTS-4/r2-content-binding.txt`

- **적용 직전 TOCTOU가 완전히 닫히지 않았다** — `ManualOwnerApproval.permits`가 바이트를 읽은 뒤 `GitRepository.apply`가 같은 파일을 다시 읽고(`repair_ops_git.py:48`), `git apply <path>`가 또 한 번 읽는다. 즉 같은 실행 내부에 짧은 교체 창이 남는다. **심각도 낮음** — 교차 실행 간 공격(승인 후 패치 교체)은 이번에 닫혔고, 남은 창은 ops 전용 0700 경로에 대한 동시 쓰기 권한을 요구한다. 조치: `GitRepository`에 `expected_patch_sha256`를 두고 `_apply_approved`→`_run`→`_agent`로 스레딩한 뒤 `git apply -`로 검증된 바이트를 stdin으로 넘긴다. **이번에 미수행한 이유**: `repair_ops_cli.py`가 `.omo/plans/repair-report-core.md:220,237`에서 불변으로 선언되어 기계 검사(`git diff BASE..HEAD | wc -l == 0`)로 강제되며, 같은 계획이 :189에서 그 불변성을 전제로 다른 설계 타협을 소유자 승인으로 수용했다. 해당 계획과 순서를 조율한 뒤 진행한다.
  ↳ 재판정(2026-09-19, D105): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #515. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`_run`이 `AWAITING_APPROVAL`에도 exit 0을 돌려준다**(`repair_ops_cli.py:174`). 워처는 자식이 0이면 성공으로 보고 레코드를 회수한 뒤 approvals 로그에 `"status":"approved"`를 남긴다 — 아무것도 적용하지 않은 채로. **이번에는 거부를 `False`가 아닌 예외로 만들어 우회했다**(non-zero 종료 → 레코드 보존, 감사 위조 없음). 근본 수리는 위와 같은 이유로 보류. 조치: 적용 경로의 `AWAITING_APPROVAL`을 전용 exit code로 분리한다. 심각도 중 — 감사 무결성 문제이지 오적용은 아니다.
  ↳ 재판정(2026-09-19, D106): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #515. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **승인 거부 시 TTL까지 매 tick마다 샌드박스가 다시 돌아간다** — `RepairAgent.repair()`가 planner·sandbox를 먼저 돌리고 그 다음에야 `approval.permits`를 부른다(`repair_ops_core.py:134-144`). 내용 불일치로 거부되는 티켓은 24h TTL로 자가치유되기까지 매번 최대 900초 샌드박스를 소모한다. **동작·보안 문제 아니고 빈도도 낮다**(patch.diff는 티켓당 한 번 쓰이고 planner가 재기록하지 않음). 조치: `_apply_approved`에 값싼 사전 가드를 두어 planner 이전에 끝낸다 — 역시 `repair_ops_cli.py` 조율 후.
  ↳ 재판정(2026-09-19, D107): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #515. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`_assert_scope`가 삭제 패치를 표현하지 못하고 rename의 원본 경로를 검사하지 않는다** — `+++ b/` 스캔이라 `+++ /dev/null`(삭제)은 조용히 거부되고 rename은 삭제측이 unstaged로 남는다. 이번에 만든 `parse_patch_changes`가 양쪽을 정확히 알므로 그것을 소비하면 “승인한 것 = 범위 검사한 것 = 적용한 것”이 된다. **이번에 미수행한 이유**: 보안 수리 중에 게이트의 허용 집합을 넓히는 것은 별개 결정이다. 심각도 낮음 — 파일 삭제가 필요한 수리를 지금은 아예 적용할 수 없다는 기능 제약이지 구멍은 아니다.

  ↳ 재판정(2026-09-19, D108): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #515. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## H1 — 헬스체크 릴리스 관측성 OWNER 인계 (2026-08-05)

기능은 [소개](기능소개/헬스체크-릴리스-관측성.md), 증적은 `.omo/evidence/fs2/task-1-parallel-followup-sweep-2.txt`다.

- **문서 갱신 대기: `operations.md:105` (freeze 해제 후)** — 현재 문구는 수리 티켓 allowlist가 없다고 적어 manifest 도입 뒤 낡지만 freeze 때문에 수정하지 않았다. **운영 안내 정확성만 영향 · 심각도 낮음** → freeze 해제 후 D1 명령과 manifest/OWNER 등록 절차로 갱신한다.

  ↳ 재판정(2026-09-19, D109): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

- **게이트가 import하는 모듈은 `deploy-skill.sh`가 동결인 동안 분할할 수 없다** — `peer_attestation.py`의 진단을 별도 모듈로 떼자 `validate_gate_staging_imports`가 `STAGE-BLOCK: imported gate module is not staged`로 배포를 막았고(실측), staging 목록은 동결된 `deploy-skill.sh` 안에 있다. 그래서 그 모듈은 250줄 천장 안에서 해결해야 했다(현재 249). 같은 제약이 `skill_gate.py`(553, 예외 등록)에도 이미 걸려 있다. **현재 동작·보안 영향 없음 · 심각도 중(구조적 — 천장에 닿은 게이트 모듈이 늘수록 선택지가 사라진다)** → staging 목록을 `deploy-skill.sh` 밖의 기계 판독 파일로 옮기는 안을 todo 14 조율안의 해제 조건과 함께 검토한다.

증적: `.omo/evidence/fs3/task-12-parallel-followup-sweep-3.txt`, 소개 [설치기 위생과 신뢰키 회전 병합](기능소개/설치기-위생과-신뢰키-회전-병합.md).

  ↳ 재판정(2026-09-19, D110): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

보드의 열린 카드 8건을 정리하며 healthcheck 허용목록 래퍼(`<primary-node>`·`<rag-node>`)를 재생성하고
runtime-package 프로브의 `cron/` 오탐을 고쳤다. 그 과정에서 드러난 구조적 틈이다.


## healthcheck 폭주 수리(PR #347) 중 발견한 인접 결함

> [이관 2026-09-03 · BLOCKED] 동결 파일에 막혀 있다 — freeze 해제 뒤 되돌린다.



## 요청별 승인 스레드 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · BLOCKED] 동결 파일에 막혀 있다 — freeze 해제 뒤 되돌린다.

- **repair 워처와 `--apply-approved` 의 read-only 부트스트랩이 여전히 kind 스레드 `승인-repair` 를 find-or-create 한다 → `RepairDiscordApi` 를 바인딩 없이 만들고 `for_pending` 에서만 저장 레코드로 바인딩하도록 transport·워처·CLI 를 정리해 kind 스레드 의존을 없앤다.** 승인 게시(`_approval`)는 이미 티켓별 스레드 `수리 · t_<ticket>` 로 가고 이 경로는 아무것도 게시하지 않으므로 기능 결함은 아니다 — 빈 `승인-repair` 스레드 하나가 채널에 남는 표시 문제(심각도 낮음). 디렉터리는 요청별 스레드를 조회 없이 열기 때문에 부트스트랩에 티켓을 넘기는 손쉬운 우회는 틱마다 빈 스레드를 하나씩 더 만든다 — 그래서 미루었다.
  ↳ 처리(2026-09-03): `automation/repair/repair_ops_*.py` 는 configs/freeze-inventory.txt closed 동결 — repair 소유 계획에 제출하고 cha 가 해제를 승인한 뒤 별도 사이클

  ↳ 재판정(2026-09-19, D111): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #515. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
# OBSERVE — 조건이 성립하기 전에는 조치하지 않는다

## 용어 교정 문서 단계 이동 착지 후 남긴 것 (2026-09-05)

> [이관 2026-09-05 · OBSERVE] 후속 과제 스윕 5 — 새 본문을 쓰는 조건이 성립하기 전에는 조치하지 않는다.

- **문서 종류 넷(report·proposal·doctype·procurement)은 아직 문서 단계 교정을 붙이지 않았다 → `tests/unit/test_term_correction_conformance.py` 의 `_EXEMPT` 에 사유와 함께 등록돼 있다.** 지금은 넷 다 소유자가 준 원문을 옮기거나 인용만 하므로 고칠 "새로 쓴 문장"이 없다 — 주간동향이 수집한 기사를 요약해 **새 본문을 쓰게 되거나** 제안서 자동 생성 본문이 과제 용어를 스스로 쓰게 되면 그때 `term_glossary.glossary_for("<kind>", project)` + `term_correction.apply` 를 렌더 직전에 붙이고 `_EXEMPT` 에서 `_ADOPTED` 로 옮긴다. 영향 범위는 그 두 스킬의 산출물뿐이고 **심각도 낮음** — 채택 전에도 회의록·라이프로그 교정은 정상 동작하며, 등록부가 비어 있는 채로 새 문서 종류가 규칙을 지나치는 일은 conformance 가 RED 로 막는다.
  ↳ 처리(2026-09-05): OBSERVE — "새 본문을 쓰게 되면" 조건이 아직 성립하지 않아 코드 변경·해소 PR 없음. 조건 성립 시 원 헤딩으로 열린 원장에 되돌려 문서 단계 교정을 채택한다. 별도 「용어 교정 문서 단계 이동 (2026-09-05) — 소유자·관측」과 합치지 않는다.

  ↳ 재판정(2026-09-19, D112): PARTIAL-SOURCE-OBSERVE — proposal 생성 본문은 해소했고 나머지 세 종류는 원래 생성 본문 조건을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Plaud lifelog 노트 v2 양식 착지 후 관측할 것 (2026-09-04)

- **[OBSERVE] `transcribe.finalize` 에서 추출(glm-main)이 실패하면 그 녹음은 `추출: …` 사유로 `transcribing` 에 남고, 전사본은 추출 뒤에 저장되므로 다음 틱이 오디오 다운로드·로컬 전사를 통째로 다시 한다 → 노드 stderr 에 같은 녹음의 `추출:` 대기가 2회 이상 반복되면 전사본을 추출 **전에** 저장하고 finalize 가 저장된 전사본을 재사용하도록 순서를 바꾼다.** 지금 바꾸면 stale 판정·저장 경로가 복잡해지는데 실제로 반복되는지 모른다(117분 녹음 = 약 45분 재전사; 정확성 무영향, 비용만 — 심각도 낮음).

  ↳ 재판정(2026-09-19, D113): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 참고자료 후속 5건 종결 중 남긴 것 (2026-08-27)

> [이관 2026-08-31] 관측 조건이 성립하기 전에는 조치하지 않는다 — 조건은 각 불릿의 조치 문구가 적고 있다.

- **xlsx 는 셀 값만 읽는다** — 차트·도형·메모의 텍스트는 빠지고 수식은 저장된 계산 결과가
  있을 때만 읽힌다. 조치: 실제로 그 형태의 근거가 필요해질 때 확장한다.
  **회수율 · 심각도 낮음**(그 형식으로만 존재하는 근거는 아직 관측되지 않았다).
  ↳ 재판정(2026-09-19, D114): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **전사 질의어의 구어 불용어 목록은 휴리스틱이다** — 실제 회의를 몇 회분 돌려 보기 전에는
  어떤 낱말이 검색을 흐리는지 알 수 없다. 조치: 질의 로그 몇 건을 보고 목록을 조정한다.
  **회수율 · 심각도 낮음**(커버리지 우선 랭킹이 오염을 상당 부분 흡수한다).

증적: 이 문서의 수치는 실제 Drive 읽기 전용 조회 결과를 마스킹한 것이다.

  ↳ 재판정(2026-09-19, D115): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 회의록 과제 양식·Action Item 데이터베이스 중 남긴 것 (2026-08-27)

> [이관 2026-08-31] 관측 조건이 성립하기 전에는 조치하지 않는다 — 조건은 각 불릿의 조치 문구가 적고 있다.

- **미처리 전사본이 여러 건일 때 `!meeting` 한 마디로 고를 수단은 여전히 없다** — CLI 는 목록을 통지하고
  멈추지만(exit 7), 소유자가 그중 하나를 지정하려면 그 전사본을 Drive 에서 내려받아 첨부하거나
  에이전트에게 `--file` 을 지정해 달라고 해야 한다. 조치: `!meeting <전사본 파일명>` 또는
  `--pending-name <이름>` 은 이미 있고(야간 배치가 그것으로 순차 처리한다) 남은 것은 대화형 선택자뿐이다.
  **오동작 아님 · 심각도 낮음**(다건이 쌓여도 자정 배치가 처리하므로 급하지 않다).
  ↳ 재판정(2026-09-19, D116): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **야간 배치의 한 틱 상한 5건은 실측 없는 값이다** — 회의록 1건 생성에 드는 시간·LLM 비용을
  재본 적이 없어, 5건이 자정 창에 들어가는지 모른다. `MEETING_PENDING_LIMIT` 로 조정할 수 있고
  초과분은 다음 밤으로 넘어가므로 밀리지는 않는다. 다만 2026-08-28 실측에서 91,894바이트 전사본
  하나(38,499자)의 추출 왕복이 **258.9초**였다(그래서 예산을 180 → 600초로 올렸다).
  상한 5건이면 최악 50분이고, 더 긴 회의는 `MEETING_LLM_TIMEOUT` 으로 노드에서 올린다.
  조치: 첫 몇 번의 실행 소요를 보고 상한과 예산을 함께 정한다. **오동작 아님 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D117): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`.hwp`·`.docx` 양식은 보고만 하고 읽지 못한다** — `TEMPLATE-UNREADABLE` 을 남기고 내장 골격으로
  되돌아가므로 소유자가 올린 양식이 조용히 무시되지는 않지만, 기관 양식의 다수가 그 두 형식이다.
  조치: 소유자에게 `.md` 변환본을 같은 폴더에 함께 두도록 안내하거나, 요구가 반복되면 읽기 전용
  변환기(예: `hwp5txt`)를 별도 사이클에서 검토한다. **회의록은 계속 생성됨 · 심각도 낮음**.


  ↳ 재판정(2026-09-19, D118): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 회의록 서식 재설계 중 남긴 것 (2026-08-26)

- **`[근n]` 마커는 클릭 이동이 안 된다** — 렌더러 이식성과 빈 제목 artifact 때문에 `[^1]` 각주
  문법을 일부러 피했고, Drive 프리뷰의 각주 지원은 미확인이다. 조치: 실측 후 지원되면 단일
  함수 `meeting_minutes.evidence_marker`만 교체. **가독성 여지 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D119): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **pptx 파싱이 `xml.etree`를 쓴다** — `<!DOCTYPE`/`<!ENTITY` 선언은 거부하지만 엔티티 확장
  계열 표면이 원리적으로 남는다. 입력은 소유자가 직접 준 파일뿐이다. 조치: 표면이 넓어지면
  `defusedxml` 또는 텍스트 런만 뽑는 스트리밍 파서로 교체. **owner-only 입력 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D120): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`agenda`(안건) 키는 v4에 넣지 않았다** — 리서치는 필수로 권하지만 사후 전사본에서 뽑으면
  `discussion` 주제와 거의 겹쳐 LLM 실패 표면만 늘어난다. 조치: 실사용에서 안건이 논의와
  갈린다고 느껴지면 v5에서 추가. **의도된 생략 · 심각도 낮음**.

관련 기능: [회의록 서식 재설계](기능소개/회의록-서식-재설계.md).

  ↳ 재판정(2026-09-19, D121): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## DM→#agent-chat 이관(승인 표면 v7) 중 발견한 후속 과제 (2026-08-24)

- **`agent_chat_thread` 해석은 캐시가 없다** — 승인 게시마다 REST 3~4회(채널 조회·active
  스레드·archived·생성). 승인 게시 빈도가 낮아 실용상 무해하나, 빈발 시 directory의 기존
  `cache_path` 메커니즘 재사용을 검토. **성능 여지 · 심각도 낮음**.

  ↳ 재판정(2026-09-19, D122): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## cron 실패 통지 확장 중 남은 판단 (2026-08-24)

계획: `.omo/plans/cron-error-remediation.md`. 기능 소개:
[cron 실패 가시성과 주간 따라잡기](기능소개/cron-실패-가시성과-주간-따라잡기.md).

- **budget 재시도 큐는 replay 큐가 아니라 인시던트 표식이다** (CR-B 리스크) — `SHEET-FAIL`이
  행을 쌓고 다음 성공 tick이 열린 행을 **일괄** resolve한다. 다음 스냅샷이 현재 잔액을 그대로
  읽으므로 개별 재생은 불필요하지만, 이름이 그 의미를 배신한다 → 실제 replay 요구가 생기면
  그때 자료구조를 다시 본다. 지금은 회귀 테스트로 현재 의미만 고정했다.
  **동작 결함 아님 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D123): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`mail-daily-digest`는 여전히 실패 tick마다 exit 1 + 마커 줄이다** — 일 1회 산출물이라
  실패는 즉시 알려야 하고 마커 줄이 그 전달이다. exit 1이 남아 스케줄러 배너가 마커와
  중복 배달되지만 하루 1건 상한이라 홍수가 아니다. 조치: 소유자가 중복을 성가셔하면
  digest도 기록-후-exit 0으로 통일. **의도된 시끄러움 · 심각도 낮음**.

  ↳ 재판정(2026-09-19, D124): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 메일 결과 원채널 스레드 통지 중 발견한 후속 과제 (2026-08-22)

- **origin 전달은 SKILL.md 지침(LLM)에 의존한다** — 에이전트가 `--origin-channel-id`를
  빠뜨리면 결과가 기존처럼 owner DM으로 간다(해가 없는 방향의 폴백 — 발송 게이트 자체는 불변).
  결정론적 강제는 게이트웨이가 지시 채널 id를 CLI 호출에 주입하는 구조가 필요해 이번 범위 밖 →
  누락이 반복 관측되면 meeting처럼 게이트웨이 플러그인 훅 주입을 검토한다. **fail-safe 폴백 ·
  심각도 낮음**.

관련 기능: [메일 결과 원채널 스레드](기능소개/메일-결과-원채널-스레드.md).

  ↳ 재판정(2026-09-19, D125): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

- **재작성이 남긴 축소 1건: 복구 경로는 영수증 수신 표시를 받지 않는다** — v0.20.3이 ingress 승인 블록을 **동기** `_discord_message_admission()`으로 추출해, `await`하는 영수증 코드가 그 자리에 존재할 수 없게 됐다. 승인 직후 첫 비동기 지점인 `_dispatch_discord_message()`로 옮겼고 라이브 경로는 예전대로 물리 DM당 1회 발화하지만, 복구 경로 `_dispatch_recovered_message()`(`claim=False`)는 👀와 ledger `record_received` 행을 받지 못한다 — 해결(resolve)은 MOD1이 쓰는 metadata를 읽으므로 정상 동작하고 수신측 breadcrumb만 빠진다(원본 패치도 라이브 경로 한 곳만 덮었으므로 범위는 같다) → 복구 경로에도 영수증을 달지 결정하려면 그 경로가 재생하는 메시지의 👀가 소유자에게 혼란을 주는지부터 정한다. **동작 결함 아님 · 심각도: 낮음(드문 경로의 관측성 공백)**.

  ↳ 재판정(2026-09-19, D126): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 티켓 스윕-2 종결 중 발견한 후속 과제 (2026-08-17)

TRACK-A(PR #125) · TRACK-BC(PR #123) · TRACK-D(PR #129)를 착지시키고 보드·증적을 정리하며 남은 것들. 기능은 [todo 소유자-DM 승인 경로](기능소개/todo-승인-경로.md) · [승인 게시 복구와 강화 저널](기능소개/승인-게시-복구와-강화-저널.md) · [2-store 메모리 재배치](기능소개/2-store-메모리-재배치.md).

- **승인 단일성 E2E 재평가 조건이 발동했다(OBSERVE#5)** — 원 OBSERVE 원장의 기준은 “게이트 스키마/파사드 변경 시 재검토”이고, TRACK-BC가 공유층 `automation/interop/approval_lifecycle.py`와 `approval_lease.py`에 enriched journal·probe 복구 분기를 추가해 그 조건을 충족했다. 기존 단위·인터리빙 검사는 green이지만 producer 간 E2E 교차 케이스는 부재한다 → 다음 승인 생명주기 작업에서 새 복구 분기를 포함한 교차 E2E를 복원할지 재판정하고 근거를 원장에 남긴다. **알려진 동작 결함은 없음 · 심각도: 중(공유 승인층 회귀 탐지 범위)**.
  ↳ 재판정(2026-09-08 · 복원하지 않는다): 후속 과제 스윕 7 이 바로 그 「다음 승인 생명주기 작업」이었다(공유층에 릴리스 자가 회수 분기 추가). ① 단일성 불변식은 producer 를 추상화한 `tests/unit/test_approval_lifecycle_interleaving.py::test_two_producers_only_one_posts` 가 이미 고정하고, ② `automation/release_approval.py::cmd_request` 는 `tests/unit/approval_conformance_inventory.py:39` 의 `APPROVAL_PRODUCERS` 에 등재돼 파사드 경유가 기계로 강제되며, ③ 새 회수 분기는 **게시하지 않고 archive 만** 하므로 교차 E2E 가 더 잡을 회귀 표면이 없다(공유층 40 passed). 재발동 조건을 다시 적는다: **두 producer 가 같은 pending 레코드 파일을 공유하게 되면** 그때 교차 E2E 를 복원한다.

  ↳ 재판정(2026-09-19, D127): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 에이전트 자가 스킬 공존(SS-1) 작업 중 발견한 후속 과제 (2026-08-15)

자가 스킬 루트 반전과 감사 원장을 만들며 발견했다. 기능은 [소개](기능소개/에이전트-자가-스킬.md).

- **curator 노브를 기본값 그대로 수용했다** — stale 30일 / archive 90일 / consolidate off는 벤더 기본값이며, 자가 스킬의 실제 사용 주기에 맞는지 근거가 아직 없다 → **첫 감사 리포트 몇 회분을 받아본 뒤** 재검토한다(자주 쓰지 않지만 필요한 스킬이 90일에 걸려 아카이브되면 원장에 `archived` 델타로 보이고 `restore`·`pin`으로 되돌릴 수 있으므로 유실은 아니다). **동작 결함 아님 · 심각도 낮음(정책 조율 미완)**.

  ↳ 재판정(2026-09-19, D128): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 재개 백오프는 릴리스가 바뀔 때만 앞당겨진다 (2026-08-17 실측)

`retry_due` 는 **기록된 지문의 릴리스 sha ≠ 현재 릴리스 sha** 일 때 백오프를 무시하고 즉시 재시도한다
(`supply_chain_watch.py:88-99`). 설계대로 동작하지만, 운영 중 이것을 모르면 판단을 계속 틀리게 한다.

- **수정을 배포해도 그 틱에 재시도가 돌면 새 지문으로 백오프가 다시 걸린다** — 릴리스가 바뀌면 즉시
  재시도가 돌지만, 그 시점에 노드측 설치본(예: `/usr/local/libexec` 헬퍼)이 아직 옛 것이면 그대로 실패하고
  **현재 릴리스 지문으로 ~58분 백오프가 재무장**된다. 그 뒤로는 릴리스가 또 바뀌기 전까지 아무리 고쳐도
  재시도가 없다. 실측: 06:46 릴리스 수렴 → 재시도 → 구 헬퍼로 실패 → 06:50 `attempt 1, retry in 3474s`.
  즉 **릴리스 랜딩과 노드 재프로비저닝의 순서가 어긋나면 한 사이클(약 1시간)을 통째로 잃는다** →
  헬퍼·유닛 등 노드 설치 자산을 바꾸는 변경은 랜딩 후 **재프로비저닝을 먼저 끝내고** 나서
  릴리스를 한 번 더 움직이거나, 백오프 만료를 기다린다. **동작 결함 아님 · 심각도 낮음(운영 지식)**.

증적: `journalctl -u autophagy-supply-chain-watch`(2026-08-17 06:34 실패 → 06:46 릴리스 변경 →
06:50 백오프 재무장), `automation/supply_chain_watch.py:88-99`.

  ↳ 재판정(2026-09-19, D129): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## G3 — 릴리스 스토어

기능은 [소개](기능소개/릴리스-리텐션-검증.md). keep-last-5와 설치 전 blob 검증, 세대·용량
healthcheck까지 구현했다.

- **용량 경계가 절대값 1 GiB다** — 세대 수는 retention 계약으로 제한되지만 실제 파일 크기와 `/srv` 여유 공간은 노드마다 달라 절대값만으로는 작은 디스크의 압박을 늦게 알릴 수 있다. **동작·보안 문제 아님·심각도 낮음** — 현재는 총 바이트와 세대 수가 매 healthcheck 로그에 남고 상한 초과는 실패한다. → 실제 운영 수치를 축적한 뒤 free-space 비율 경보를 추가할지, `RELEASE_STORE_MAX_BYTES` 기본값을 조정할지 결정한다. 이번 작업은 실제 `/srv` 실행 금지라 로컬 기본값만 검증했다.

  ↳ 재판정(2026-09-19, D130): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 배포 체크아웃 드리프트 가드(DG-1) 작업 중 발견한 후속 과제

기능은 DONE「배포 체크아웃 드리프트 가드 (DG-1)」 참조, 증적 `docs/qa/DG-1/summary.txt`

- **마운트 ABI 검사는 지금 WARN 전용이다** — C(DG-1)는 deploy-skill.sh·land.sh에서 라이브 스킬 ABI 파손을 감지하면 `MOUNT-ABI-WARN`/`LAND-ABI-WARN`으로 알리고 계속 진행한다(배포 중 차단은 이미 소비된 승인을 고아로 만들어 더 나쁘 실패모드). 실제 파손은 승인 흐름의 fail-closed(게시 거부)로 나타난다. **심각도 중** — 가드는 있으나 자동 차단은 아니다. 조치: `DEPLOY_ABI_STRICT=1`/`LAND_ABI_STRICT=1` 옵인을 오탐율 관찰 후 기본값으로 승격할지 결정한다.
  ↳ 재판정(2026-09-19, D131): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **미추적 파일 드리프트는 여전히 보이지 않는다** — 로컬 probe도 `--untracked-files=no`를 유지한다(`logs/` 오탐 방지용 의도된 계약). 주 사고 유형(로컬 커밋·추적 파일 수정)은 ahead/dirty로 덮인다. 조치: 미추적 드리프트가 실제 관측되면 화이트리스트 탐지 검토. 심각도 중.

  ↳ 재판정(2026-09-19, D132): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #520. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 저장·라우팅 스윕(RTS) — doctype 개인노트 진입점만 실사용 이력이 없다 (2026-08-04 갱신)

앞서 「티켓 3건이 열려 있다」고 적어둔 것은 **낡았다**. 2026-08-04 실측으로 정정한다 — `t_1b8aab9b`·`t_929ca5ad`·`t_f92027cb` 세 티켓 모두 **`done`**(2026-07-29 15:08 종료)이고, 막혀 있던 「승인 게이트 경유 실제 볼트 쓰기」도 이미 수행됐다. 상세는 DONE「저장·라우팅 스윕(RTS) 종단 검증」 참조.

- **`doctype`의 “개인노트로 저장해” 진입점은 아직 실사용된 적이 없다** — 실증된 5건은 전부 `memory_relocate`(운영사실 재배치) 경로였고, `doctype_cli`의 개인노트 분기는 같은 `obsidian_write`를 쓰지만 진입점이 다르다. **심각도 낮음으로 하향**(종전 “중”) — 공유 하단(PARA upsert → commit → push → 원격 read-back)이 5회 실증돼 미지의 영역이 라우팅 분기 하나로 즐었고, 종단 회귀는 `tests/unit/test_doctype_save_routing_e2e.py` 5건이 고정하고 있다. 조치: 소유자가 처음 “개인노트로 저장해”를 쓸 때 결과를 한 번 확인한다(별도 작업 불필요).

  ↳ 재판정(2026-09-19, D133): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 승인 생명주기 공용화(2026-07-25 배포) 중 발견된 후속 과제

증적 `docs/qa/E12/00-single-live-approval.txt`, 패치 `docs/patch/2026-07-25-approval-lifecycle-consolidation.md`

- **승인 단일성 불변식의** e2e 교차 케이스는 drive-archive 시나리오에 있었으나 E11 폐기(2026-07-31)로 함께 제거됨. 나머지 9개 게이트는 단위+인터리밙 테스트로만 고정함(각 스킬의 일반 e2e 시나리오는 별도로 존재). 우선순위 낮음 — 불변식 자체는 이미 검증됨.

  ↳ 재판정(2026-09-19, D134): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 저장 경로간 ‘의미 중복’ 가능성 — 기억해(위키) · 개인노트 저장(Obsidian) 분류기가 서로를 모름

(cha 지적, 2026-07-29)

- **문제**: 당시 기억 분류기는 **위키 노트**(`wiki:` 키)로, 문서 분류기는 개인노트를 **Obsidian**(`obsidian:` 키)으로 보냈다. 같은 내용을 두 경로로 요청하면 서로 다른 source_key로 두 벌이 인덱싱되는 의미 중복 가능성이 있었다. Q4에서 옛 기억 분류기를 은퇴하고 짧은 기억은 omo, 문서·긴 기록은 Obsidian으로 안내한다. 기존 색인의 교차 출처 중복 관찰은 유지한다.
  ↳ 재판정(2026-09-19, D135): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **영향 범위**: recall 검색 품질(같은 사실이 두 출처로 나와 가중치 왜곡) · 트윈 판단 근거의 이중 계산. **보안 문제 아님** — 둘 다 소유자 게이트를 거치고 민감도 판정도 그대로 적용된다. 심각도 중 — 사용 패턴 의존적이라 실제 관측 전에는 빈도를 알 수 없다.
  ↳ 재판정(2026-09-19, D136): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **조치(관찰 후 결정)**: 지금 상위 라우터를 선제적으로 두면 사용되지 않을 추상을 하나 더 얹는 셈이다. 먼저 **중복이 실제로 관측되는지** 확인한다 — `personal_cha`에서 같은 내용이 `wiki:`와 `obsidian:` 두 키로 나오는 사례를 수집. 관측되면 (a) 두 분류기 앞에 공유 상위 판정(“이 요청은 기억인가 문서 저장인가”)를 두거나, (b) recall 단계에서 동일 내용의 교차 출처를 병합해 보여주는 방법 중 선택. calendar↔coordination의 `classify_meeting_request` 선례처럼 **공유 판정 함수 + 모호하면 clarify**가 (a)의 형태가 된다.

  ↳ 재판정(2026-09-19, D137): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## G5 — 스킬 위생 + mail 다이제스트

구현 내용은 [소개](기능소개/승인표면-메일다이제스트-정리.md). 아래는 코드 완료 뒤에도 소유자 권한·라이브 관측이 필요해 이번 PR에서 실행하지 않은 체크리스트다.

- **[재평가] JSON 계약 하드닝 뒤에도 항목별 1회 재시도가 필요한지는 라이브 실패율이 없다** → 배포 후 `classification_failed` 빈도와 추가 호출 비용을 관찰하고, 충분한 무실패 기간 전에는 회귀 보험을 제거하지 않는다. **심각도 낮음** — 재시도는 캘린더 위임 전에 현재 메일 분류만 반복해 외부효과 중복은 없다.

  ↳ 재판정(2026-09-19, D138): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## G2 — 승인 게이트·공급망 워처

기능은 [소개](기능소개/승인게이트-공급망워처-정리.md), 작업 배분은 `.omo/plans/parallel-followup-sweep.md` §5 G2다.

- **레거시 pre-schema 레코드의 자동 종결은 금지하고 소유자 실행 경로만 마련했다** — `action_hash`·`kind`·`channel_id`·`policy_version`·`surface`가 빠진 레코드는 현대 승인으로 승격하지 않는다. **안전 문제 아님·심각도 낮음** — fail-closed 보류가 유지된다. cha가 이미 실현된 효과와 정확한 `(skill, hash, message_id)`를 확인한 뒤 `abandon --legacy-only`를 실행하며, 현대 스키마 레코드는 이 경로가 거부한다. 현재 procurement 1건의 실행 여부는 소유자 판단으로 남긴다.
  ↳ 재판정(2026-09-19, D139): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`managed-activate`·`skill-publish` 자동 재개는 의도적으로 구현하지 않았다** — 전자는 레코드에 `--activate-managed <quarantine-dir>`가 없고 후자는 별도 프로그램과 `--managed-repo`가 필요하다. **추측 실행은 공급망 안전 위험·심각도 높음**. 자동화하려면 이 맥락을 승인 레코드 필드에 영속할지, 검증된 전용 상태 파일에 둘지 먼저 설계하고 그때 `SUPPORTED_KINDS`를 확장한다.

  ↳ 재판정(2026-09-19, D140): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## repair-report cron 활성화 중 발견한 후속 과제 (2026-08-13)

- **Hermes Python no-agent job은 정상 실행의 stdout도 cron output artifact에 보존하지 않을 수 있다** — `repair-report-consumer`뿐 아니라 `daily-cost-report`·`budget-watch`·`reminder-poller`의 실제 성공 run도 `Status: silent (empty output)`로 남았다. **수리 보고 처리 자체와 보안에는 영향 없음 · 심각도 낮음(스케줄러 관측성)** → Hermes의 Python no-agent stdout 수집 경로를 별도 조사하고, 그 전에는 성공 여부를 stdout 본문이 아니라 `Last run` 전진과 도메인 부작용의 exact readback으로 증명한다.

증적: `docs/qa/RRO-2/03-cron.md` §16 및 최종 활성화 절, 구현 근거 `automation/repair/cron/repair_report_consume_watch.py:30-39`.

  ↳ 재판정(2026-09-19, D141): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 공개 컷 전 인가 감사(2026-08-15) 중 발견한 후속 과제

- **root 소유 `managed-skills-allowed-signers`가 2개 이상 principal을 담게 되면 roster가 principal 선택 수단이 된다** — `managed_sync/cli.py:90-102`의 `_publisher_principal()`은 신뢰 principal 이름을 **roster 문서에서 직접 읽는다**(`roster.admin.publisher_principal`). 지금 안전한 이유는 `plan_signer_install`이 그 root 소유 파일에 **정확히 1개 entry**만 쓰기 때문이지(이름이 안 맞으면 `ssh-keygen -Y verify`가 거부) roster가 서명되기 때문이 아니다. 다중 그룹이나 키 로테이션 과도기로 entry가 늘면 roster를 쥔 쪽이 어느 principal을 쓸지 고르게 된다. **현재 동작 영향 없음 · 심각도 낮음** → entry를 늘리는 변경이 생기면 그때 principal 선택을 roster 밖(대역외로 고정한 로컬 설정)으로 옮긴다.

증적: `.omo/notepads/public-release/issues.md`의 「[2026-08-15] Security audit — auth/authorization」 두 번째 항목(수정된 roster replay 1건 + 위 3건의 근거와 기각 사유 포함).

  ↳ 재판정(2026-09-19, D142): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 지식 계층 4단계(2026-08-22) 중 발견한 후속 과제

위키 스키마 v2와 Obsidian→위키 큐레이션을 착지시키며 남은 것들이다([소개](기능소개/위키-큐레이션과-스키마-v2.md)).
같은 묶음에 있던 「RAG compose 프로젝트 이름 분열」은 `compose.yaml`을 유닛과 같은 `personal_rag`로
맞추고 두 파일을 대조하는 회귀를 걸어 해소했다.

- **엔터티 추출이 원천 frontmatter에만 의존한다** — Obsidian 노트에 `entity` 키가 없으면 제안된 초안의
  `entity`가 비어 검색 앵커가 생기지 않는다(증류 LLM은 본문 요약만 돌려준다) → 앵커 없는 제안이 많아지면
  `twin_distill`처럼 LLM 출력에서 엔터티를 함께 받아 검증한다. **노트 제안 자체는 정상 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D143): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`relations`는 저장·인덱싱까지만 흐르고 랭킹에 쓰이지 않는다** — `rank.py`가 소비하는 것은 `entity`와
  `event_date`뿐이다 → 관계 질의가 실제로 필요해지는 시점에 랭킹 신호로 승격한다. **심각도 낮음**.

  ↳ 재판정(2026-09-19, D144): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 연구계획서 자동생성 v2 실증 중 발견한 후속 과제 (2026-08-23)

완료 기능은 [연구계획서 자동생성](기능소개/연구계획서-자동생성.md)이다. 실제 문서 본문·개인정보·자격증명은 이 기록에 포함하지 않는다.

- **과거 제안서가 RAG 코퍼스에 들어오지 않았다** — knowledge-layer 단계에서 유예해 새 초안이 과거 제안서의 표현·실적을 자동 검색하지 못한다 → 민감도와 중복 제거 계약을 정한 뒤 과거 제안서 전용 인제스트를 추가한다. **영향 범위: 근거 회수율과 초안 품질 · 심각도 중**.

  ↳ 재판정(2026-09-19, D145): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 음성 녹취 회의록 자동화(speechtotext) 착지 후 남긴 것 (2026-08-25)

- 해소 (2026-09-01): sherpa-onnx 로컬 화자 분리 착지 — 전사본 블록에 `화자N` 헤더와 이름 범례가 붙고, 도구가 없으면 `DIARIZE-FAIL` 후 화자 없이 계속한다(파이썬 ML 스택 없이 바이너리 호출로 해결해 stdlib 정책 예외 불필요). [소개](기능소개/전사본-화자-구분과-문장-단위-출력.md)
  ↳ 재판정(2026-09-19, D146): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **proposal 이 루트 기본값을 여전히 따로 적는다** — 살아 있던 절반(override 불일치)은 닫았다:
  `proposal_publish._root_folder()` 가 `DRIVE_OUTPUTS_ROOT` 를 함께 읽는다. 남은 것은 기본
  문자열 중복이며, `outputs_root()` 유도로 바꾸려면 이 모듈이 일부러 피해 온 runtime-root
  임포트 경계를 들여야 한다(주입형 transport 설계). `proposal_config.drive_root` 는 소비처가
  없는 죽은 설정으로 확인됐다. **동작 영향 없음 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D147): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **다듬기는 결정적이고 요약이 아니다** — 문장·문단·연속중복·용어집만 손대고 발언자 구분이나
  군더더기 정리는 하지 않는다. LLM 재작성은 회의 원문을 또 다른 LLM 경계로 내보내는 결정이라
  meeting 의 민감도 게이트를 거치는 별도 설계가 필요하다. **동작 영향 없음 · 심각도 낮음**.

관련 기능: [음성 녹취 → 전사본 → 회의록 자동화](기능소개/음성-녹취-회의록-자동화.md).

  ↳ 재판정(2026-09-19, D148): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Plaud lifelog 동기화 후속 (2026-09-02)

> [이관 2026-09-03 · OBSERVE] 조건이 성립하기 전에는 조치하지 않는다.

- **get_note 항목의 `data_error_code`(실측 예: 10) 의미가 불확실해 지금은 오류 항목의 `data_content` 도 요약에 넣는다
  → 실사용에서 오류 항목이 관측되면 스킵 규칙을 넣는다.** 실스키마 자체는 2026-09-02 로그인 후 측정해 fetch.py 를
  교정했고(top-level 리스트·segments·next_cursor·빈=[]), 승인 게이트가 최종 검토라 잘못된 요약은 ✅ 단계에서 걸린다(심각도: 중).
  ↳ 처리(2026-09-03): get_note 오류 항목(`data_error_code`)이 실사용에서 관측되면 스킵 규칙을 넣는다 — 그 전에는 승인 게이트(✅)가 잘못된 요약을 거른다


  ↳ 재판정(2026-09-19, D149): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 기관메일 회신 원문 인용 후속 (2026-09-01)

> [이관 2026-09-03 · OBSERVE] 조건이 성립하기 전에는 조치하지 않는다.

- **mailon 웹메일의 답장 버튼(In-Reply-To/References 헤더) 경유는 vendor 변경이 필요해 미도입 → 상대 클라이언트의 스레드 묶음이 어긋나는 사례가 보고되면 `send_trigger` 계열에 답장 모드를 실측 기반으로 추가한다.** 현재는 본문 인용으로 사람 눈에는 회신으로 보이며 발송 안전성과 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): 상대 클라이언트의 스레드 묶음 어긋남이 실제로 보고되면 vendor 답장 모드를 실측 기반으로 추가한다 — 그 전에는 본문 인용으로 충분


  ↳ 재판정(2026-09-19, D150): STILL-BLOCKED — 원래 동결·벤더·외부 의존 조건이 유지된다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 화자 구분·문장 단위 출력 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · OBSERVE] 조건이 성립하기 전에는 조치하지 않는다.

- **API 백엔드(`gpt-4o-transcribe`)에는 화자 구분이 없다** — 그 응답에는 구간 타임스탬프가 없어 sherpa-onnx 의 turn 과 문장을 맞출 수 없다. `gpt-4o-transcribe-diarize` 는 화자를 주지만 **원음이 민감도 게이트를 거치기 전에** 외부로 나가므로 이 스킬의 기본 경로가 될 수 없다. 조치: 기본값으로 만들지 않고, 소유자가 비민감 녹취에 한해 켤 수 있는 명시 옵션으로 검토한다(지금은 아이디어 단계). **영향 범위: API 폴백 경로의 전사본에만 화자 헤더가 없다 · 심각도 낮음** — 운영 노드는 로컬 whisper.cpp 경로다.
  ↳ 처리(2026-09-03): 비민감 녹취 한정 명시 옵션은 소유자가 필요를 표명할 때 설계한다(원음이 게이트 전에 외부로 나가므로 기본값 불가)

  ↳ 재판정(2026-09-19, D151): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **자기소개 인식 패턴이 아직 측정되지 않은 휴리스틱이다** — `stt_speakers._INTRODUCTION`·`_TITLE` 은 "저는 ○○입니다"·"○○ 박사입니다" 계열 정규식과 불용어 목록이고, 실제 회의록 표본으로 재현율·오탐률을 잰 적이 없다. 조치: 실회의 전사본이 쌓이면 라벨링해 패턴을 조정하거나 LLM 근거 쪽 가중을 올린다. **영향 범위: 이름이 안 붙으면 `미상` 으로 남을 뿐 전사·회의록은 정상 · 심각도 낮음**.
  ↳ 처리(2026-09-03): 실회의 전사본 표본이 쌓이면 라벨링해 재현율·오탐률을 잰 뒤 패턴/LLM 가중을 조정한다

  ↳ 재판정(2026-09-19, D152): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **이름은 화자 분리 오류를 그대로 따라간다** — 자기소개 문장이 잘못된 클러스터에 배정되면 그 이름도 잘못된 라벨에 붙고, 병합 규칙상 소유자 override 전까지 유지된다(규칙 출처가 LLM 보다 세다). 조치: 범례가 출처와 시각(`자기소개 00:03:12`)을 적어 소유자가 그 지점을 직접 확인할 수 있게 해 두었고, 정정은 `polish --speakers` 한 번이다. 자동 교차검증(LLM 이견이 있을 때 규칙 신뢰를 낮추기)은 표본이 쌓인 뒤 판단한다. **영향 범위: 회의록의 발화 귀속이 어긋날 수 있다 · 심각도 중**.
  ↳ 처리(2026-09-03): 표본이 쌓인 뒤 LLM 이견 시 규칙 신뢰를 낮추는 교차검증을 판단한다 — 지금은 범례의 출처·시각으로 소유자가 직접 확인


  ↳ 재판정(2026-09-19, D153): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 후속 과제 스윕 4 착지 후 남긴 것 (2026-09-03)

- **[OBSERVE] 전환된 obsidian 클론에서 `git reset --hard` 의 지연 blob fetch 는 fetch 예산(900초)이 아니라 일반 git 예산(120초)에 묶인다 → 틱당 vault 델타가 커져 reset 이 타임아웃되는 사례가 관측되면 reset 도 fetch 예산으로 옮긴다.** 지금은 틱당 노트 한 건이라 해당 없음(심각도: 낮음).
  ↳ 재판정(2026-09-19, D154): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`automation/pipeline_lock.hold` 가 `@contextlib.contextmanager` 생성기라 `with hold()` 본문에서 frozen+slots dataclass 예외가 나면 contextlib 의 `exc.__traceback__` 대입이 TypeError 로 바뀌어 진짜 실패 사유가 사라진다(obsidian `clone_lock` 구현 중 실측, 그래서 그쪽은 class 기반 CM) → speechtotext·meeting 워처의 lock 본문에서 그런 예외가 날 수 있는지 확인하고, 있으면 class 기반 CM 으로 바꾼다.** 영향 범위: 워처 실패 사유의 표시뿐, 동작 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `fix(pipeline-lock): hold 를 class 기반 컨텍스트 매니저로 바꿔 frozen+slots 예외가 TypeError 로 가려지지 않게 한다` — `automation/pipeline_lock.hold` 가 `__enter__/__exit__` 클래스, tests/unit/test_pipeline_lock.py 에 frozen+slots 예외 전파 회귀(RED: TypeError → GREEN)
  ↳ 재판정(2026-09-19, D155): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`automation/memory_relocate/effects_live.py` 가 transport 승격 뒤에도 261 pure LOC 라 `automation/final/f2_loc_exceptions.txt` 예외가 남는다 → `RelocationStore` 를 별도 모듈로 분리해 등록부에서 내린다.** 코드 크기 규약만, 동작 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `refactor(memory-relocate): RelocationStore 를 relocation_store.py 로 분리해 effects_live.py 를 F2 예외 등록부에서 내린다` — effects_live 219 pure LOC, `automation/final/f2_loc_exceptions.txt` 에서 제거, 재수출로 호출부 무변경

  ↳ 재판정(2026-09-19, D156): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
# 후속 과제 스윕 5 — 소유자·관측 인계

## 후속 과제 스윕 5 착지 후 남긴 것 (2026-09-05) — 소유자·관측

- **[OWNER] 노드 ops는 peer의 config·채널 디렉터리를 읽을 권한과 peer로 cat할 sudo 권한이 없다 → 소유자가 `visudo`로 아래 파일별 읽기 권한 두 줄을 설치한다.** 스윕 실측은 ops rc=1(`PEER-IGNORED-CHANNELS-UNREADABLE`), peer rc=0 PASS였다(PR #409). 영향: 설치 전 매 틱 healthcheck FAIL·수리 티켓 잡음, 승인 게이트 판정 무영향 · **심각도 중**.

  ```sudoers
  <ops> ALL=(<peer>) NOPASSWD: /usr/bin/cat -- <peer-home>/.hermes/config.yaml
  <ops> ALL=(<peer>) NOPASSWD: /usr/bin/cat -- <peer-home>/.hermes/channel_directory.json
  ```

  자리표시는 설치의 계정·홈 값으로 바꾼다. 셸·Python sudo는 허용하지 않는다. 설치 후 ops로 같은 읽기 전용 프로브를 실행해 PASS를 확인한다.
  ↳ 처리(2026-09-06): 저장소 쪽은 PR #425로 닫혔고 노드 쪽은 **아직 OWNER 다**. 설치기가 node config 로 렌더하는 두 줄짜리 `automation/sudoers.d/autophagy-healthcheck-peer-read` 자산을 기존 설치 자산 목록에 등록해 새 노드 온보딩은 자동으로 이 권한을 받는다(계정·홈이 다른 설정에서도 렌더되는 회귀 포함). 기존 노드는 소유자가 [`peer_ignored_channels` 권한 복구](guide/operations.md#peer_ignored_channels-권한-복구)의 sudo 명령 한 번으로 그 파일만 `visudo -cf`·0440 root:root·원자 교체·digest 대조로 놓는다. 이 명령은 아직 실행되지 않았으므로 권한이 설치됐다거나 ops 프로브가 PASS 라고 말하지 않는다.
  ↳ 재판정(2026-09-20, D157): VERIFIED-SNAPSHOT — 실제 ops 계정에서 기존 peer 설정 프로브가 PASS였다. 필요한 입력 두 개를 읽을 수 있음을 확인했으며 sudoers 파일 전체 동일성이나 더 넓은 권한 부재의 증거는 아니다.
- **[OBSERVE] `DEFAULT_THRESHOLD=1.3`은 64분 녹음에서 3개 군집이지만 라이프로그에는 사람 18명이 적혀 있다 → 다음 다화자 회의 녹음에서 재검증하고 실제 화자 수를 알면 `--speaker-count`를 우선한다.** 이는 상한 8 안에서 쓰기 위한 운영값이지 화자 신원·인원수 확정이 아니다(PR #405·#412). 영향: 발화 귀속·가독성, 전사 내용 손실 없음 · **심각도 낮음**.
  앞선 원장의 "0.9 → 화자 41"과 재실행의 122개는 같은 오디오의 서로 다른 sherpa 실행 결과다. 이번 판단의 정본은 체크인된 [재실행 증적](qa/FU5/diarize-threshold-tuning.md)이며 1.20=11, 1.25/1.30/1.35=5/3/2개다.
  ↳ 처리(2026-09-06): 해소 — 소유자가 그 64분 녹음의 화자가 **둘**이라고 확인했다(사람 18명 목록은 언급된 이름이지 화자 수 추정이 아니었고, 이전의 "미확인" 분류는 본문의 UTC 표시 오류 탓이었다). PR #422가 발화 시간 점유율로 재검증해 1.30은 54.5%/26.0%/19.5%로 세 번째 군집이 무시할 수준이 아니고, 1.35는 71.5%/28.5% 두 군집, 1.40은 하나로 합쳐졌다 — 합의한 규칙("5% 이상 두 군집을 내는 가장 작은 값")대로 `DEFAULT_THRESHOLD=1.35`, stderr 에 `DIARIZE-CLUSTERS speakers=<n> threshold=<t> turns=<n>` 진단 한 줄, speechtotext 1.2.3. 정답이 없는 117분 녹음은 1.30=5개, 1.35=2개(52.8%/47.2%)로 참고만 한다. 화자 신원 정확도를 일반화하지 않으며 화자 수를 아는 큰 회의는 `--speaker-count` 를 쓴다. [증적](qa/FU6/diarize-threshold-validation.md).

  ↳ 재판정(2026-09-19, D158): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
# 후속 과제 스윕 6 — 소유자·관측 인계

## 후속 과제 스윕 6 착지 후 남긴 것 (2026-09-06) — 소유자·관측

- **[OWNER] PR #424 이전에 로컬 전사로 vault 에 이미 쓰인 라이프로그 노트 3건은 frontmatter `created`·`modified`·`녹음::`·출처 줄이 UTC 를 현지 시각처럼 담고 있다(제목·경로는 맞다) → 소유자가 별도 승인한 작업으로 그 노트만 고친다.** `plaud_sync_watch.py --repost-posted` 는 승인 카드를 다시 올릴 뿐 동결 본문을 재생성하거나 이미 쓰인 vault 노트를 갱신하지 않으므로 보정 수단이 아니다. 영향: 노트 3건의 본문 시각 표기, 새 노트는 정상 · **심각도 낮음**.
  ↳ 재판정(2026-09-19, D159): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OBSERVE] v1.3.0 롤아웃에서 `deploy_all --apply` 가 `incomplete: automation/skill_generation/deploy.sh` 를 찍었다 — Hermes 의 `--allow-tool-override` 프롬프트 뒤 deploy.sh 가 0 이 아닌 코드로 끝났는데, deploy_all 의 독립된 최종 프로브는 깨끗해 영수증을 썼다 → 배포기 종료 코드와 최종 상태를 어느 쪽이 맞는지 소유자가 정한다: deploy.sh 에 `--allow-tool-override` 를 넘길지(별도 권한 결정이라 조용히 넣지 않는다), 아니면 계획이 프롬프트 종료를 비치명으로 분류할지.** 영향: 롤아웃 로그의 오독뿐, 프로덕션 무영향 · **심각도 낮음**.
  ↳ 재판정(2026-09-19, D160): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 같은 롤아웃의 deploy_all 미선언 홈 경고가 손으로 설치한 `hermes-achievements` 플러그인, `interop-protocol` 플러그인(agent·peer), 그리고 스크립트 몇 개를 이름 붙여 냈다 → 소유자가 선언(해당 배포기 옆 `deploy-manifest.txt`)할지 제거할지 재고 정한다.** 자동 삭제 근거가 아니라 인벤토리 작업이다. 영향: 드리프트 탐지의 사각지대 목록만, 실행 무영향 · **심각도 낮음**.

  ↳ 재판정(2026-09-19, D161): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ [부분 해소 2026-10-01] 스크립트 3개(`regression_bank_weekly.py`·`release_v123_dm_watch.py`·`restart-hermes-gateway-once.sh`)와 `hermes-achievements` 는 release-convergence-blind-spots todo 34 의 격리 관례(`quarantine-<UTC 날짜>/`, 경로 `/`→`__`, INDEX.txt)대로 회수해 그 todo 의 후보에서 빠진다. `deploy_all --plan` 의 미선언 경고는 `interop-protocol` 4줄(agent·peer)만 남고, 그 배포·roster 는 같은 계획의 GAP-5 가 맡는다.
  ↳ 처리(2026-10-01): 남은 미선언 경고인 `interop-protocol` 플러그인(agent·peer)은 인터롭 배포기 선언에 들어갔다 — `f2ef2ac5`. 노드에 놓는 일은 roster 배치와 함께 웨이브 6 todo 33(소유자 승인)이 하고, 그때 이 경고도 사라진다. 따로, 실행 경로에서 빠진 옛 런타임 사본은 `policy=retired` 로 선언돼 릴리스 판정이 남아 있음(`retired-present`)만 보고한다 — memory_curator `1cd0051f`, reminder_poller `7c6fe009`, repair `fe36a843`. 그 사본들은 지우지 않으며, 실행 경로 밖으로의 격리는 todo 34(웨이브 6, 소유자 승인) 절차다.
# 후속 과제 스윕 7 — 소유자·관측 인계

## 라이프로그 전사·화자 품질 교정 착지 후 남긴 것 (2026-09-06) — 소유자·관측

> [이관 2026-09-08] 후속 과제 스윕 7 — 저장소가 지금 닫을 수 없는 몫만 여기 남긴다. 같은 묶음의 해소분은 「해소 기록」에 있다.

- **화자 상한 8 은 개인 라이프로그에 크다** — 2인 녹음이 상한 보수를 타면 최대 8명이 된다. 조치:
  lifelog 경로에만 낮은 상한(`SPEECHTOTEXT_DIARIZE_MAX_SPEAKERS`)을 줄지 실측 후 판단한다.
  **정확도 · 심각도 낮음**.
  ↳ 이관(2026-09-08 · OBSERVE): 상한을 낮추려면 라이프로그 실녹음의 군집 분포가 먼저 필요하다 — 이번 스윕에는 그 측정이 없다. 상한을 넘겨도 라벨을 버리지 않고 `--clustering.num-clusters=<상한>` 으로 한 번 다시 묶으므로(`DIARIZE-RECLUSTERED`) 2인 녹음이 8명이 되는 경로는 이미 닫혀 있다. 조건: 소유자가 화자 수를 아는 라이프로그 녹음이 생기면 그 분포로 판단한다.

  ↳ 재판정(2026-09-19, D162): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 라이프로그 화자 분리 정정 착지 후 남긴 것 (2026-09-07) — 소유자·관측

> [이관 2026-09-08] 후속 과제 스윕 7 — 저장소가 지금 닫을 수 없는 몫만 여기 남긴다. 같은 묶음의 해소분은 「해소 기록」에 있다.

- **화자 수 질의 패스가 sherpa 백엔드에서는 성과가 작다** — 실측에서 화자 수를 고정해도
  sherpa(eres2net)·titanet 은 세 번째 목소리를 0.5~0.8% 조각으로만 내놓고, pyannote 만
  발화 시간 11.4%·15.8% 로 찾아낸다(docs/qa/PLD1 §5). 즉 질의 패스의 값어치는 백엔드가
  pyannote 일 때 나온다. **그리고 그때도 절반이다**: 라벨을 걷어낸 초안으로 두 녹음을 끝까지
  돌리니 272.5초는 모델이 3(기준점 3)이라 답해 재분리가 일어났고, 549.4초는 8,624자 초안에서도
  2(기준점 4)라 답해 재분리가 없었다. 반대로 549.4초는 질의 패스 없이 기본값만으로 실질 4명이라
  기준점과 같다 — 두 경로가 서로 다른 녹음을 맞히므로 어느 하나를 자동 기본으로 삼을 근거가 없다. 조치: 라이프로그 경로만 `SPEECHTOTEXT_DIARIZE_BACKEND=pyannote`
  로 돌릴지 판단하려면 CPU-only 비용(0.57x 실시간 — 64분 녹음 한 패스 약 37분)을 소유자가
  받아들일지가 선행 조건이므로, 코드에서 기본값을 바꾸지 않고 남긴다. 영향: 현재 기본값
  으로도 발화 구분은 회복됐고 화자 수만 근사다. 심각도 중.
  ↳ 이관(2026-09-08 · OWNER): 기본 백엔드를 pyannote 로 바꿀지는 CPU-only 비용(0.57x 실시간 — 64분 녹음 한 패스 약 37분)을 소유자가 받아들일지가 선행 조건이다. 저장소가 임의로 기본값을 바꾸지 않는다.

  ↳ 재판정(2026-09-19, D163): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **약한 화자는 낱말을 한 번도 이기지 못해 문서에 오르지 못한다** — 549.4초 녹음은 실질
  4군집(22.5 / 21.9 / 20.7 / 16.4%)이 나오는데 전사본에는 `화자1`·`화자2` 둘만 오른다.
  2026-09-07 오후에 **조각화 쪽은 해소했다**(문장 조립 순서와 화자 변경 경계 — 화자0 블록
  47.6%→33.3%, 49.7%→39.3%, `docs/qa/PLD2`), 그러나 약한 두 화자가 문서에 오르지 못하는 것은
  남았다: 그들은 낱말 단위에서 한 번도 이기지 못하므로 문장 단위 다수결로도 올라오지 않는다.
  낱말 판정 분포는 direct 76.1% · low_coverage 7.1% · no_support 6.8% 이고, 이 둘은 그
  low_coverage/no_support 쪽에 몰려 있을 것으로 보이나 **화자별로는 아직 세지 않았다**.
  조치: reason 을 화자별로 쪼개 어느 화자가 어느 규칙에서 지는지 먼저 보고, 그 다음에 정책을
  손댄다. 겹침→화자0 은 1ms 겹침 회귀가 고정한 fail-safe 이고 실측에서도 겹침 몫 중앙값이
  0.457 이라 대부분 진짜 동시 발화이므로 그것은 계속 건드리지 않는다. 영향: 화자 수가 실제보다
  적게 보인다. 낱말은 잃지 않는다(`화자0` 으로 남는다). 심각도 중.
  ↳ 처리(2026-09-08 · 부분 해소 → OBSERVE): 원문이 요구한 「reason 을 화자별로 쪼개 본다」는 `stt_attribute.speaker_reason_breakdown` 으로 구현했다 — 후보 화자별 reason 건수를 돌려주며 배정 결과·전사본 바이트는 불변이다(합성 4화자 실측: 강한 둘 `direct`, 약한 둘 `low_coverage`·`no_support` — 원문의 가설과 같은 방향, 증적 `docs/qa/FU7/speaker-reason-breakdown.md`). **남은 것은 실녹음 측정이다**: 549.4초 녹음으로 어느 화자가 어느 규칙에서 지는지 세고 그 분포를 근거로 정책을 손댈지 판단한다. 소유자 오디오가 필요하므로 여기 남긴다.

  ↳ 재판정(2026-09-19, D164): NEEDS-EVIDENCE — 현재 판정에 필요한 원본·실측 증거가 부족하며 결함을 지어내지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 제안서 엔진 내제화 착지 후 남긴 것 (2026-09-08) — 소유자·관측

> [이관 2026-09-08] 후속 과제 스윕 7 — 저장소가 지금 닫을 수 없는 몫만 여기 남긴다. 같은 묶음의 해소분은 「해소 기록」에 있다.

- **엔진 트리의 공개 여부는 소유자 판단으로 남았다.** 원본 `kimm-docbot` 이 비공개였으므로
  `configs/public-export-manifest.txt` 에 디렉터리 한 줄로 제외해 현상을 유지했다. 공개로 승격하려면
  FS3 사유 원장에 엔진 경로를 등록해야 한다(약 78행). 판단 전까지 공개 배포본의 proposal 스킬은
  렌더 엔진 없이 나간다 — 내제화 이전과 같은 상태다.
  **영향: 공개 배포본의 proposal 스킬 완결성, private 저장소·노드 런타임 무영향 · 심각도 중**.
  ↳ 이관(2026-09-08 · OWNER): 공개 승격은 FS3 사유 원장에 엔진 경로 약 78행을 등록하는 결정이라 소유자 판단 없이는 저장소가 바꾸지 않는다. 판단 전까지 공개 배포본의 proposal 스킬은 렌더 엔진 없이 나간다(내제화 이전과 같은 상태).

  ↳ 재판정(2026-09-19, D165): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
# 2026-09-19 kanbanfix 통합 정산 — 해소·소유자·관측 인계

## 화자 식별(voice catalog ③) 착지 후 남긴 것 (2026-09-18)

> [이관 2026-09-19] kanbanfix 통합 정산. 아래는 열린 원장의 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

- **등록된 사람이 1명이면 여유(margin)가 점수와 같아 무력하다** — 2위 후보가 없어서다. 지금은
  `accept` 하나가 그 자리를 지키고 있다. 오확정이 관측되면 다음 지렛대는 「그 녹음의 다른 라벨을
  impostor cohort 로 써서 상대 점수를 정규화(AS-Norm)」이고, VC3 표로 계산하면 참 라벨은 자기
  녹음의 다른 라벨 평균보다 0.18 높고 가장 가까운 라벨은 0.065 높다. 영향: 오확정 위험(회의록에
  남의 발언 귀속). **심각도 중** — 지금은 실패 방향이 「확정 대신 제안」이라 조용히 틀리지 않는다.
  조치: 등록본이 2명 이상 쌓인 뒤 같은 표본으로 재측정하고, 그때도 accept 하나로 부족하면 cohort
  정규화를 더한다.
  ↳ 이관(2026-09-19 · OBSERVE): 조건 미성립. 주 세션이 2026-09-19 에 노드에 마운트된 `stt_catalog_cli list` 를 읽기 전용으로 실행한 결과 여전히 등록 1명 · 등록본 1건 · 60초다. 원문의 「등록본이 2명 이상 쌓인 뒤 재측정」이 성립하지 않으므로 AS-Norm 을 지금 얹지 않는다. 되돌아오는 조건: 카탈로그에 두 번째 사람이 등록될 때.
- **맥락 기반 후보 선택(prior)이 없다** — 녹음 시각의 캘린더 참석자·`--project` 과제의 담당·전사
  본문의 호칭·최근 30일 등장 이력은 "그 자리에 누가 있었을 만한가"를 이미 알고 있다. 영향:
  동점 구간에서 확정을 못 하고 제안에 머문다(안전한 실패). **심각도 낮음**. 조치: ⑤(stt_eval 비교)로
  제안→확정 전환율을 먼저 재고, 이득이 보이면 후보 shortlist 로 넣는다.
  ↳ 이관(2026-09-19 · OBSERVE): 원문이 선행으로 둔 ⑤(stt_eval 제안→확정 전환율) 측정은 등록 1명 · 녹음 1건인 지금 표본으로는 뜻이 없다(2026-09-19 노드 카탈로그 실측, 위 항목과 같다). 이득이 측정되기 전에는 shortlist 를 넣지 않는다.
- **임계값 표본이 녹음 1건·등록 1명이다.** 영향: accept 0.80 이 다른 마이크·다른 방에서 과소
  확정일 수 있다. **심각도 중**. 조치: ②b 워처로 등록본이 쌓이는 대로 `docs/qa/VC3` 의 같은 절차를
  다시 돌린다(스크립트는 측정 도구라 저장소에 없다 — 절차는 VC3 §1~2 가 정본).
  ↳ 이관(2026-09-19 · OBSERVE): 같은 2026-09-19 카탈로그 실측이 등록본 1건 · 60초 그대로다. VC3 §1~2 절차의 재실행은 ②b 워처로 새 등록본이 쌓인 뒤에만 뜻이 있어, 조건 성립 전에는 accept 0.80 을 손대지 않는다.
- **PLE1 표본 B·C 는 옛 전사본이라 교차 녹음 표본이 못 된다**(여러 사람이 `화자1` 하나로 뭉쳐
  있다). 영향: 실측에 교차 녹음 양성이 없다. **심각도 낮음**. 조치: `plaud_sync_watch.py --reprocess`
  로 다시 전사한 뒤 VC3 절차에 넣는다.
  ↳ 이관(2026-09-19 · OWNER): `plaud_sync_watch.py --reprocess` 는 노드 GPU 전사 · 새 승인 카드 · 노트 본문 재생성을 수반하는 프로덕션 상태 쓰기라 소유자가 의도적으로 돌리는 흐름이다. 이번 정산 세션은 실행하지 않았고 승인 경로도 바꾸지 않았다. 재전사가 끝나면 그 전사본을 VC3 절차에 넣는다.

증적: `docs/qa/VC3/summary.md`.
- **릴리스 승인에는 리마인더가 없다 — 카드가 한 번 게시되고 끝난다** — `supply_chain_remind.py`
  가 `result.request.kind != ApprovalKind.SKILL_DEPLOY.value` 로 걸러, 같은 `#approvals` 에 나란히
  놓이는 `ApprovalKind.RELEASE` 카드는 시간당 포인터를 받지 못한다. v1.9.0 이 6시간 반 동안
  `bound_pending` 으로 서 있는 내내 소유자를 찌른 것이 하나도 없었다(실측). 완결기의
  `decision --notify-stale` 는 요청이 tip 을 벗어났을 때만 한 건 보내므로 main 이 멈춰 있는
  동안에는 영영 조용하다 — 즉 「아무도 머지하지 않는 조용한 시간」이 정확히 리마인더가 가장
  필요한 시간이다. 영향: 릴리스가 조용히 지연된다(잘못 배포되는 방향은 아니라 안전 불변식은
  그대로다). **심각도 중**. 조치: 그 kind filter 를 `{SKILL_DEPLOY, RELEASE}` 로 넓히고 주기·문구는
  kind 별로 `approval_kpi.policy_table` 에서 읽는다 — 리마인더 본문은 이미 kind 를 받아 렌더하므로
  새 표면을 만들 필요가 없다.
  ↳ 처리(2026-09-19 · 해소): PR #505(fa-release-reminder, 머지 `f59c291d1`). `supply_chain_remind._REMINDED_KINDS` 가 `{SKILL_DEPLOY, RELEASE}` 로 넓어졌다. 증적 `fa-release-reminder/.omo/evidence/kfx/fa-release-reminder/red.txt`(`test_release_older_than_interval_gets_one_reminder_per_interval` 1 failed / 10 passed) → `green.txt`(11 passed); 주 세션 전체 CI 9819 passed · 1 기존 skip, 영수증 `b58c4422…`. **머지됐을 뿐 배포는 아니다**: 프로덕션은 아직 `b8333c5c6`(v1.9.1)이고 노드 리마인더 실측은 다음 릴리스 뒤 몫이다.
- **승인 요청이 tip 뒤로 밀리면 완결 타이머가 그 ✅ 를 실행하지 못한다** — 워크스테이션
  `autophagy-release-complete.timer` 가 2분마다 `RELEASE-DECISION: live request is bound to a
  different HEAD` 를 찍고 무동작으로 끝나는 것을 v1.9.0 대기 중 실측했다(exit 0 이라 실패로도 안
  보인다). 완결기는 `origin/main` 으로 묻는데 살아 있는 요청은 태그 **전** sha(`15bcde588`)에
  묶여 있고, 그 사이 PR 이 착지하면 둘이 갈라진다. 2026-09-09 의 `release_completion_target` 은
  **태그가 이미 잘린 뒤**의 같은 증상을 고친 것이라 이 구간(승인 대기 중 tip 이동)은 아직
  열려 있다. **이것은 이번 릴리스의 사고가 아니라 만성 상태다** — 14일치 저널에 그 줄이
  **2,418회**, 적어도 **2026-09-04** 부터 찍혀 있다(같은 기간 `executed release … awaiting
  retirement` 556회). 즉 완결 타이머는 상당 시간 동안 「살아 있는 승인을 실행한다」는 제 역할을
  하지 못하는 상태로 돌고 있었고, exit 0 이라 어떤 알람도 울리지 않았다. 영향: 세션의
  `release.sh` 가 죽으면 소유자의 ✅ 가 아무에게도 실행되지 않고 조용히 남는다 — 잘못 배포되는
  방향은 아니다. **심각도 중**. 조치: 완결기가 `origin/main`
  대신 **살아 있는 승인 레코드의 bound head** 로 먼저 묻고(그 sha 가 origin/main 의 조상인지만
  확인), 아니면 그 상태를 무동작이 아니라 소유자 통지로 올린다.
  ↳ 처리(2026-09-19 · 해소): PR #508(fb-completer-bound-head, 머지 `062b12979`). 완결기가 살아 있는 승인 레코드의 bound SHA 가 `origin/main` 의 조상인지 먼저 묻고 조상이면 그 SHA 에서 완결하며, 조상이 아니면 요청당 한 번 소유자 통지를 저널에 남긴다(RED `4c565be7b` → GREEN `9dae577bb`, 재개 GREEN `8564bcda9`). 증적 `fb-completer-bound-head/.omo/evidence/kfx/fb-completer-bound-head/resume-report.md` · `surface.txt`; 주 세션 release/origin-snapshot 356 passed. 배포 전이라 워크스테이션 타이머의 실제 저널 변화는 아직 관측하지 않았다.
- **릴리스 승인 카드에 ✅·⛔ 가 미리 붙지 않는다 — 소유자에게는 누를 버튼이 없다** — v1.9.0 카드를
  Discord 에서 직접 읽어 `reactions: []` 를 확인했다(게시 11시간 뒤). 「Owner-confirm 이모지 컨벤션」은
  「확정 메시지에는 봇이 ✅·⛔를 미리 부착한다(cha는 탭 한 번)」인데, 부착 코드를 가진 곳은
  `reaction_approval`·`calendar_confirm`·`budget_confirm`·`triage_confirm`·`repair_ops_discord` 등이고
  릴리스 승인 경로(`release_approval`·`release_card`·`skill_gate_surface`)에는 없다. **위의 「리마인더가
  없다」와 합쳐지면 릴리스 승인은 알림도 없고 버튼도 없는 표면**이 된다 — 소유자가 이모지를 직접
  찾아 달아야 하고, 그 마찰이 이번 11시간 정체의 즉각적 원인이다. 영향: 릴리스가 조용히 지연된다.
  **심각도 중**. 조치: 게시 직후 `✅`·`⛔` 를 봇으로 부착한다(판정은 `skill_gate._owner_reacted` 가
  `user_id == owner_id and not is_bot` 로 거르므로 봇 리액션은 결과를 바꾸지 않는다 — 이번에 코드로
  확인한 뒤 손으로 부착해 카드를 되살렸다). 공급망 승인(skill-deploy 등)도 같은 표면이므로 함께 본다.
  ↳ 처리(2026-09-19 · 해소): PR #523(d7-release-reactions, 머지 `100dea20e`). `SkillApprovalGate.commit` 이 0600 레코드를 쓴 직후 기존 API 로 ✅·⛔ 를 각각 best-effort 로 붙인다(실패는 `APPROVAL-REACTION-FAIL`, 카드·바인딩 불변). 판정 · nonce · 해시 · 렌더러는 손대지 않았다. 증적 `d7-release-reactions/.omo/evidence/kfx/d7-release-reactions/resume-report.md`(RED 4/4 → 관련 375 passed) · `real-surface.py`(loopback HTTP: POST 200 → PUT 204 ×2, 봇 리액션은 `BOUND_PENDING` 유지). **실제 Discord 카드의 버튼 확인은 다음 릴리스 승인 게시 때(C6) 남는다.**

## 후속 과제 스윕 7 착지 후 남긴 것 (2026-09-08)

> [이관 2026-09-19] kanbanfix 통합 정산. 아래는 열린 원장의 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

- **릴리스 승인 CLI 두 파일이 250 pure-LOC 경고 구간에 들어왔다** — 자가 회수 분기를 넣으며
  `automation/release_approval.py` 245 · `automation/release_abandon.py` 227 이 됐고, 전자는 회수 조립을
  후자로 옮겨 F2 등록부에서 내려온 참이다(등록부 항목 제거 완료). 조치: 다음 기능 추가 때 **먼저**
  CLI 명령 조립(argparse 배선)과 종결 표시(abandon 회신·감사 문구) 책임을 분리한 뒤 기능을 얹는다 —
  지금 나누면 이번 사이클의 검증된 이음새를 근거 없이 다시 흔든다. **영향: 다음 변경의 유지보수 비용,
  현재 동작·인가 경계 무영향 · 심각도 낮음**.
  ↳ 처리(2026-09-19 · 해소): 원문 조건(「다음 기능 추가 때 먼저 분리」)대로 PR #508 이 완결 기능을 얹기 **전에** SPLIT 커밋 `b3007c5b3` 로 승인 배선과 판정·종결 책임을 나눴다(분리 전후 characterization 123 passed, `fb-completer-bound-head/.omo/evidence/kfx/fb-completer-bound-head/resume-report.md`). F2 예외 등록부에는 두 파일이 없다. 이 정산은 pure LOC 를 다시 재지 않았다.

## healthcheck 래퍼 설치 자산의 non-root 운영자 결함 (2026-09-08)

> [이관 2026-09-19] kanbanfix 통합 정산. 아래는 열린 원장의 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

- **컨테이너 하네스가 `operator_account='root'` 로만 돌아, 운영자가 root 가 아닌 노드에서만 나타나는 결함을 v1.6.1 로 내보냈다** →
  `tests/e2e/install/systemd_container/run.sh` 에 non-root 운영자 경로(`--operator NAME`: 계정 생성·config 렌더·summary 필드)를 더한다.
  이번 사이클에서는 그 컨테이너 안에 손으로 계정을 만들어 증명했고(증적 12), 회귀는 argv 계약으로 고정했다.
  **영향: 같은 종류의 root 가정이 또 새어 나갈 수 있음 · 동작은 정상 · 심각도 중**.
  ↳ 처리(2026-09-19 · 해소): PR #507(fe-e2e-nonroot, 머지 `6b4ff9bb1`). `run.sh --operator NAME` 이 계정 생성 · config 렌더 · summary 필드를 맡는다. 증적 `fe-e2e-nonroot/.omo/evidence/kfx/fe-e2e-nonroot/red.txt`(argv 회귀 2 failed / 2 passed) → `green.txt`(4 passed) · `nonroot.txt`. 주 세션이 `--operator ops2 --stub-hermes` 로 컨테이너를 다시 돌려 `PROBE-STATE` ops2 소유 0755 · `PROBE-CONVERGED: inspect_probe=True` · `SUDOERS-VERIFIED` 0440 을 읽었다. 최종 rc=1 은 서비스 없는 컨테이너의 문서화된 healthcheck 경계이지 프로브 자산 실패가 아니다.

증적: [docs/qa/INSTALL-TUI/12-probe-asset-nonroot-operator.txt](qa/INSTALL-TUI/12-probe-asset-nonroot-operator.txt).

## 문서 검토 안내의 전송 경계 (2026-09-11)

> [이관 2026-09-19] kanbanfix 통합 정산. 아래는 열린 원장의 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

- **doctype·proposal·procurement 검토 안내가 통지 파사드 밖에서 전송된다** → 전송 수단을
  `owner_notice`로 옮길지 별도 설계한다. 지금은 doctype·proposal의 `hermes send` argv·청킹·실패 처리와
  procurement의 직접 메시지 POST·첨부 경로를 그대로 두고, 본문만 소유자 메시지 봉투로 렌더한다.
  **영향: 세 검토 안내의 전송 정책 일원화 미완 · 심각도 낮음**.
  ↳ 처리(2026-09-19 · 해소): PR #513(ff-review-notice, 머지 `e0a635e98`). 검토 안내 3종과 첨부가 통지 파사드로 모여 목적지 규칙이 하나가 됐다(RED `b5d2f6b23` → GREEN `868de534a`). 증적 `ff-review-notice/.omo/evidence/kfx/ff-review-notice/green.txt`(170 passed) · `resume-targeted-initial.txt`(365 passed) · `resume-surface.txt`.

새 안내는 검토 문서를 대상으로 식별하고, procurement가 이미 받은 Drive 링크를 위치로 싣는다.
링크가 없는 첨부·발행 실패 경로와 doctype·proposal의 문서 경로는
`Ref(scope="resource", url=None, search=("문서 검색", 파일명))`으로 안내한다.
doctype·proposal 검토 호출 시점에는 Drive URL이 전달되지 않으므로 추측하거나 재발행하지 않는다.
문서 경로는 대상 식별에 남고, 제안서의 검토 의견은 기존처럼 해당 문서에 저장된다.
봉투를 import할 수 없거나 렌더러가 필드를 거부하면 기존 본문을 바이트 그대로 전송한다.

## 승인 요청 안내 메시지·비공개 표면 허용경로 착지 후 남긴 것 (2026-09-09)

> [이관 2026-09-19] kanbanfix 통합 정산. 아래는 열린 원장의 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

> 착지 기능: [승인 요청 안내 메시지](기능소개/승인-요청-안내-메시지.md) ·
> [비공개 표면의 민감도 게이트](기능소개/비공개-표면-허용경로-추출.md)

- **안내 메시지는 새 요청에만 붙는다.** 이미 열려 있는 요청 스레드(진행 중인 캘린더·plaud 카드)는 옛
  형식이라 채널에서 여전히 본문 없는 시스템 줄로 보인다. 조치: 필요하면 각 워처의 **기존** 재게시 경로
  (`plaud_sync_watch.py --repost-posted` 등)로 다시 올린다 — 새 재게시 수단을 만들지 않는다.
  영향 범위: 표시만, 승인·해시 바인딩과 무관. 심각도: 낮음.
  ↳ 이관(2026-09-19 · OWNER · 노드 확인 선행): 옛 형식 카드가 지금도 채널에 남아 있는지 이번 세션은 확인하지 않았다(보드 읽기 전용 조회만 했고 승인 채널은 읽지 않았다). 그래서 「없다」고도 「재게시했다」고도 적지 않는다. 남아 있다면 기존 `--repost-posted` 경로만 쓰고 새 재게시 수단은 만들지 않는다는 원문 조치가 그대로다.
- **긴 본문 승인은 여전히 게시할 수 없다.** 수리 티켓 t_82644d12 의 두 절반 중 **쐐기**만 닫았다 —
  Discord 2000자를 넘는 승인 메시지는 이제 posting journal 을 예약하기 **전에** 거부되므로 그 키가
  영구히 막히지 않지만(예전에는 HTTP 400 뒤 모든 재시도가 POSTING_JOURNAL_STALE), 소유자가 요청한
  「요약 메시지 + 동일 메시지 본문 첨부」 게시·검증은 아직 없다. 조치: multipart 첨부 게시와
  lifecycle·리액션 확인 두 경로의 첨부 검증, 그리고 형식 판별자를 설계해 별도 사이클에서 구현한다
  (기존 승인 해시 검사는 그대로 두어야 이미 게시된 승인이 무효가 되지 않는다).
  영향 범위: `skills/mail/scripts/triage_{core,approval,confirm,gate}.py`. 심각도: 중.
  ↳ 처리(2026-09-19 · 해소 · 실게시 미검증): PR #511(k2-mail-attach, 머지 `292431317`). 장문 승인은 요약 메시지에 본문 첨부를 묶어 multipart 로 올리고, lifecycle · 리액션 확인 두 경로가 자격증명 없이 첨부를 내려받아 sha 를 대조하며 누락 · 불일치는 거부한다(`04c30cc3e`, `skills/mail/scripts/mail_approval_attachment.py`). 기존 승인 해시 검사는 불변. 증적 `k2-mail-attach/.omo/evidence/kfx/k2-mail-attach/red.txt` · `red-edge.txt` · `red-response.txt` → `green.txt`(168 passed); 주 세션 변경 테스트 27 passed. **2000자 초과 승인의 실제 게시 · read-back(C2)은 배포 뒤 몫이다.**
- **`VerifiedRoute` 채택은 두 곳뿐이다**(`lifelog_extract_live`·`stt_speaker_ask`). 같은 모양의 게이트가
  회의록 등 **공개·공유 산출물** 경로에도 있으나 소유자 지시 범위("내가 보는 private 채널")를 벗어난다.
  조치: 확장하려면 표면별로 따로 판단한다. 영향 범위: 없음(현행이 규칙과 일치). 심각도: 낮음.
  ↳ 이관(2026-09-19 · OWNER · 범위 결정): 회의록 등 공개 · 공유 산출물 표면은 원 지시(「내가 보는 private 채널」) 밖이라고 원문이 이미 못 박았다. 현행이 규칙과 일치하므로 확장 여부는 표면별 소유자 판단이고 저장소가 임의로 넓히지 않는다.

## Google Tasks 합성 과제 유입 수리 중 발견한 인접 결함 (2026-09-11)

> [이관 2026-09-19] kanbanfix 통합 정산. 아래는 열린 원장의 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

- **meeting 단위 테스트 4건이 워크스테이션의 실제 `gws` 로 소유자 Drive 를 읽는다** → PATH 에 로그만 남기는 가짜 `gws` 를 두고
  전량 스위트를 돌린 실측(`PYTEST_CURRENT_TEST` 기록): `tests/unit/test_meeting_skill.py::test_meeting_drive_publish_uses_note_date_and_label`(10회)
  · `::test_sensitive_meeting_skips_drive_publish`(4) · `::test_drive_facade_import_failure_does_not_block_local_save`(2) ·
  `tests/unit/test_meeting_project_ingest.py::test_pending_transcript_minutes_publish_under_its_project`(4) 가 `drive files list/get`
  (KIMM·autophagy 루트 폴더 조회, 폴더 id 재검증)을 실행한다.
  ↳ **2026-09-12 외부 도달은 닫혔다** — `tests/unit/conftest.py` 의 PATH 거부 스텁이 이 네 테스트의 `drive files list` 를
  exit 97 로 거부한다(실측: 가드 아래 `-k meeting` 305 passed — 네 테스트는 거부를 받고도 통과한다). 제안됐던 *파일 단위*
  autouse 가드는 채택하지 않았다: 같은 날 형제 워크트리의 pre-patch 사본이 실제로 Tasks 에 썼고, 파일 단위 가드는 그것을
  구조적으로 덮을 수 없다. **남은 것은 밀폐성뿐** — 네 테스트가 아직 `run` 주입 없이 실 캐시 경로를 *시도*하므로
  `DriveClient`/`drive_outputs` 의 `run` 을 주입하거나 `DRIVE_PUBLISH_ENABLED` 을 명시적으로 끄면 워크스테이션 상태에
  좌우되지 않는다. **영향: 외부효과 0(쓰기 0·읽기도 이제 차단) · 심각도 낮음**.
  ↳ 처리(2026-09-19 · 해소): PR #498(fh-test-hygiene, 머지 `57fdd7a70`, `cc35f60a7`). 네 테스트가 `DriveClient` 의 실행 경계를 정확히 스텁해 실 캐시 경로를 더는 시도하지 않는다. 주 세션 변이 증명(`kanbanfix/.omo/evidence/kfx/fh-parent-verification.md`): 스텁을 빼면 실제 `DriveClient._run` 진입 2회(RED), 되돌리면 0회(GREEN); meeting 묶음 + pyright 설정 테스트 284 passed. 앞선 가짜 gws 프로브는 결론이 없어 RED 근거로 세지 않았다.

## 반출 릴리스 노트 안내 착지 후 남긴 것 (2026-09-13)

> [이관 2026-09-19] kanbanfix 통합 정산. 아래는 열린 원장의 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

- **단위 테스트가 형제 테스트 모듈을 import 하면 편집기가 매번 가짜 오류를 낸다** → `pyrightconfig.json` 의
  `executionEnvironments` 에 `tests/unit` 이 없어 `from test_public_export import …` 같은 교차 import 8곳이 전부
  `reportImplicitRelativeImport` 를 낸다. 편집기 전용 설정이라 런타임·pytest·CI 는 무영향이지만, 편집 직후 진단을
  읽는 경로(에이전트 포함)에서는 진짜 오류와 섞인다.
  ↳ **조치**: `tests/unit` executionEnvironment 를 더하고, 선언 목록을 고정하는 `tests/unit/test_pyright_config.py` 를
  같은 커밋에서 갱신한다(PR #420 이 `skills/*/scripts` 19개를 더한 것과 같은 형태). **영향: 동작 결함 아님 · 심각도 낮음**.
  ↳ 처리(2026-09-19 · 해소): 같은 PR #498(`9c939a0b2` → `cc35f60a7`). `pyrightconfig.json` `executionEnvironments` 에 `tests/unit` 이 들어갔고 `tests/unit/test_pyright_config.py` 가 선언 목록을 고정한다(HEAD 확인: `{ "root": "tests/unit", "extraPaths": ["."] }`). 동적 meeting 스크립트 import 가 내는 기존 편집기 진단 12건은 이 항목 밖이라 숨기지 않았다.

## plaud ogg 형식 사고 착지 후 남긴 것 (2026-09-16)

> [이관 2026-09-19] kanbanfix 통합 정산. 아래는 열린 원장의 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

- **보류 중 클라우드 재확인의 일시 오류가 원래 실패 사유를 덮어쓴다** → `transcribe.recheck` 는 `get_note`·`get_transcript`·
  `get_file` 의 일시적 `PlaudMcpError` 를 `_block(record, effects, error.reason)` 으로 적어 `last_block_reason` 을
  `get_note: PlaudMcpError` 로 바꾼다. 2026-09-16 실측: 세 녹음이 전부 rc=5「지원하지 않는 형식」으로 막혔는데 `plaud 상태` 는
  MCP 오류만 보여 소유자와 에이전트가 승인 통지 문제로 오독했다(원인은 노드의 `audio/*.ogg` 를 보고서야 드러났다).
  ↳ **조치**: 카운트되지 않는(`counted=False`) 재확인 실패는 별도 필드(예 `last_recheck_error`)에 적거나 사유 문자열을
  「원 사유 · 재확인: …」 으로 이어 붙여 마지막 **녹음** 실패 사유가 남게 한다. 상태 스키마는 선택 필드 추가만.
  **영향: 동작 결함 아님(재시도·폴백은 그대로) · 진단 오도 · 심각도 중**.
  ↳ 처리(2026-09-19 · 해소): PR #501(k6-plaud-alias, 머지 `272e6e68b`, `38670f96c`). `transcribe.recheck` 의 카운트되지 않는 일시 오류는 선택 필드 `last_recheck_error`(`REASON_LIMIT` 로 자름)에 적고 `last_block_reason` 은 마지막 **녹음** 실패 사유로 남는다(`model.py` 선택 키 추가만). 증적 `k6-plaud-alias/.omo/evidence/kfx/k6-plaud-alias/red.txt` → `green.txt`(61 passed) · `parent-surface.py`(격리 상태에서 실제 워처 마이그레이션 실행).

# 해소 기록 — 닫혔지만 회계 가드가 원문을 요구한다

## 후속 과제 스윕 5 착지 후 남긴 것 (2026-09-05)

> [이관 2026-09-06 · 해소] 후속 과제 스윕 6 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **공개 반출 추가 원장이 append-only라 병렬 PR이 같은 끝줄에서 충돌한다 → `configs/public-export-baseline-additions.txt` 에 경로순 삽입 규칙을 두거나 lane별 파일을 게이트에서 합산하는 방식을 설계한다.** 이번 4개 lane은 직렬 재병합·local_ci 재실행(각 약 8분)이 필요했다. 영향: 병렬 착지 비용, 배포 동작 무관 · **심각도 낮음**.
  ↳ 처리(2026-09-06): 해소 — PR #417이 `public-export-baseline-additions.txt`·`public-export-review.txt` 두 원장을 경로순으로 한 번 정렬하고 머리말에 `sorted by path` 규칙을 적었다. `tests/unit/test_public_export_ledger_order.py` 가 정렬·중복을 고정하므로 lane 마다 자기 경로 자리에 끼워 넣으면 같은 끝줄 충돌이 사라진다.
  ↳ 재판정(2026-09-19, D166): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **정책 분리 뒤에도 `automation/plaud_sync/{transcribe.py,transcribe_live.py,note.py}`·`automation/plaud_sync/cron/plaud_sync_watch.py` 는 200–250 pure LOC 구간이고 `automation/healthcheck.sh` 는 203이다 → 다음 확장 때 mcp_client와 같은 규칙으로 책임을 나누어 250 상한 전에 분할한다.** 영향: 다음 변경의 유지보수 비용, 현재 동작 무관 · **심각도 낮음**.
  ↳ 처리(2026-09-06): 해소 — PR #418이 `transcribe.py`(130)·`transcribe_live.py`(68)에서 `transcribe_promote.py`(115)·`transcribe_live_effects.py`(199)를 떼어냈고, PR #419가 `note.py` 에서 `note_paths.py`(64)를, cron 래퍼(194)에서 `watch_runtime.py`(80)를 떼어냈다(부트스트랩·deploy-manifest·emit 된 워처 매니페스트 불변). PR #421은 `healthcheck.sh`(161)의 프로브 레지스트리를 `healthcheck_registry.sh`(61)로 source 분리했고 allowlist 출력은 바이트 동일하다. 공개 import 경로와 기존 테스트는 그대로다. `note.py` 는 PR #424의 몇 줄이 더해진 뒤에도 200 아래다.
  ↳ 재판정(2026-09-19, D167): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **basedpyright가 `skills/*/scripts` 의 형제 import(stt_gap·stt_split 등)에 기존 `reportImplicitRelativeImport` 를 보고한다 → 편의 개선 시 pyright 설정 또는 스킬별 import shim을 둔다.** 영향: LSP 설정·편집기 진단뿐, 런타임 무영향 · **심각도 낮음**.
  ↳ 처리(2026-09-06): 해소 — PR #420이 루트 `pyrightconfig.json` 에 `skills/*/scripts` 19개와 루트를 `executionEnvironments` 로 선언했다(편집기 전용, 런타임 무관). 표적 진단은 STT 2→0, mail 14→0이고 기존 automation 진단 1건은 그대로 1건이다. `tests/unit/test_pyright_config.py` 가 설정을 고정한다.

  ↳ 재판정(2026-09-19, D168): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 2026-09-05 수리 스윕-4 후 남긴 것

> [이관 2026-09-06 · 해소] 수리 스윕-4 후속 처리 세션 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **완결 타이머의 `systemd --user` 유닛이 메인 체크아웃의 `automation/release_complete.sh` 를 실행해, 매 틱 origin/main 으로 맞춰지는 것은 완결기 전용 워크트리뿐이다 — 그래서 그 스크립트 **자체**의 변경(스윕 호출 추가 등)은 메인 체크아웃을 ff-pull 하거나 유닛을 재설치하기 전까지 반영되지 않는다(실측: 도입 뒤 두 틱이 `RELEASE-APPLIED` 줄 없이 지나갔다) → `release_complete.sh` 가 워크트리 동기화 직후 그 워크트리의 자기 사본으로 exec 하게 하거나, 설치기가 `ExecStart` 를 워크트리 사본으로 가리키게 한다.** 영향 범위는 완결 타이머 스크립트 자체의 변경뿐이고 수렴·태그·마운트는 그 워크트리에서 돌아 무영향, **심각도 낮음**.
  ↳ 처리(2026-09-06): `fix(release-complete): 워크트리 동기화 직후 origin/main 의 자기 사본으로 exec 한다 — 유닛이 메인 체크아웃을 가리켜도 스크립트 변경이 다음 틱부터 반영된다(잠금 fd 상속·재진입 가드)` — `release_complete.sh` 가 워크트리를 origin/main 에 맞춘 직후 자기 사본과 `<worktree>/automation/release_complete.sh` 를 `cmp` 로 대조해 다르면 `SELF-UPDATE` 한 줄을 남기고 그 사본으로 `exec` 한다(잠금 fd 9 는 exec 너머로 상속, `RELEASE_COMPLETE_REEXEC=1` 재진입 가드, 동일 사본은 제자리 실행). tests/unit/test_release_complete.py 에 exec·가드·동일 사본 회귀 3건(RED: `SELF-UPDATE` 부재·decision 호출 → GREEN). 실표면: 진짜 origin/main(`24457d1f`) 워크트리에서 `SELF-UPDATE` 뒤 옛 사본이 `pending` 까지 진행, decision 호출 1건. 유닛의 `ExecStart` 는 그대로 메인 체크아웃이며 손 ff-pull 은 설치 때만 필요하다.

  ↳ 재판정(2026-09-19, D169): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## plaud 파이프라인 실동작 검증 착지 후 남긴 것 (2026-09-05)

> [이관 2026-09-05 · 해소] 후속 과제 스윕 5 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **상한(기본 2회)에 닿았는데 클라우드도 비어 `클라우드 폴백 보류` 된 녹음은 `transcribing` 에 남아 매 틱(10분) 오디오 전사를 통째로 다시 돈다** → 보류 상태에는 백오프(예: 클라우드 재확인만 하고 로컬 전사는 N틱마다)나 소유자 결정 카드를 둔다. 후보가 시도 횟수 순이라 새 녹음을 굶기지는 않지만(`182117055`), 1초 녹음 2건(0e188780·7ed7b257)이 릴리스 뒤 이 상태로 들어가 틱마다 whisper 를 잠깐씩 돈다. 영향: 노드 GPU 몇 초/틱, 외부효과 없음 — 심각도 낮음.
  ↳ 처리(2026-09-05): 해소 — PR #414의 선택 레코드 필드 `next_transcribe_at`이 로컬 재전사를 1h→2h→4h→8h→16h→24h(상한) 백오프로 예약한다. 클라우드는 로컬 전사 슬롯 밖에서 매 틱 재확인하고, `PLAUD_SYNC_TRANSCRIBE_GIVE_UP`(기본 5회)에도 비면 `abandoned`와 저널 줄로 종결한다. 기본 5회에서는 장기 백오프 단계 전에 종결될 수 있다.
  ↳ 재판정(2026-09-19, D170): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **우발 버튼으로 생긴 1~2초 녹음도 이제 로컬 전사 경로를 탄다** — 예전에는 클라우드가 비어 조용히 skip 됐지만 수리(`9292d680c`) 뒤에는 동결·전사되고, whisper 가 한 토큰이라도 내면 승인 카드가 올라온다 → 최소 길이 게이트(값은 정책: 예 5초 미만은 `abandoned`)를 둘지 소유자 판단. 소유자는 2초·5초 녹음 카드를 ⛔ 한 전례가 있다(1be8067b·772daac7). 심각도 낮음(카드 1장/건).
  ↳ 처리(2026-09-05): 해소 — PR #414의 `PLAUD_SYNC_MIN_DURATION_MS`(기본 5000) 미만은 발견 시 skip(저널만, 레코드·카드 없음)한다. 이미 `transcribing`인 짧은 녹음은 다음 처리에서 `abandoned`로 종결한다.
  ↳ 재판정(2026-09-19, D171): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **화자 분리가 64분 녹음에서 화자1~41 을 만들고 전부 `미상`이다(과분할)** — 전사 본문은 온전하지만 범례 한 줄이 40개 항목이고 블록 헤더의 화자 번호가 사실상 무의미하다 → sherpa-onnx 클러스터링 임계값·최대 화자 수(`SPEECHTOTEXT_DIARIZE_*`)를 실측 녹음으로 조정하거나 화자 수 상한을 넘으면 범례를 접는다. 심각도 낮음(내용 손실 없음, 가독성).
  ↳ 처리(2026-09-05): 해소 — PR #405의 `SPEECHTOTEXT_DIARIZE_MAX_SPEAKERS`(기본 8) 초과는 `DIARIZE-OVERSEGMENTED`로 화자 배정만 fail-soft한다. PR #412는 노드 실측으로 `DEFAULT_THRESHOLD`를 0.9→1.3으로 조정했다(재실행 0.9=122, 1.20=11, 1.25/1.30/1.35=5/3/2개). [증적](qa/FU5/diarize-threshold-tuning.md); 실제 화자 수 검증과 옛 41개 관측의 차이는 위 새 OBSERVE에 남겼다.
  ↳ 재판정(2026-09-19, D172): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **Plaud 요약이 없는 녹음의 노트 `## 요약` 이 `- (요약 없음)` 이다** — v2 양식은 Plaud 요약을 그대로 싣는 설계라 로컬 전사만 있는 녹음은 요약 절이 빈다(9/5 실측 279b84). 한눈에·결정 · 할 일은 로컬 전사에서 추출되므로 노트는 쓸 만하다 → 로컬 전사에서 요약도 같은 추출기로 만들지(LLM 호출 1회 추가, 민감도 게이트 동일)는 정책 판단. 심각도 낮음.
  ↳ 처리(2026-09-05): 해소 — PR #414의 `prompts/lifelog-extraction-v2.md`가 기존 구조화 추출과 **같은 LLM 호출**에서 `summary`를 받는다(추가 호출 0). Plaud 요약이 있으면 그것을 우선하며 기존 민감도 게이트를 유지한다.

  ↳ 재판정(2026-09-19, D173): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## provenance 가드의 남은 이스케이프 경로 (2026-09-05)

> [이관 2026-09-05 · 해소] 후속 과제 스윕 5.

- **`deploy_provenance.sh` 의 저장소 전체 검사(144·149행)는 아직 `git status --porcelain` / `git ls-files` 의 기본 따옴표 이스케이프를 그대로 출력한다 → 비ASCII 파일이 dirty·untracked 로 걸리면 차단 메시지가 `"\354\232\251…"` 로 나와 어느 파일인지 사람이 알 수 없다.** 디렉터리 인자 경로(193·202·213)는 `-c core.quotepath=false` 로 고쳤고 그것이 실제 배포를 막던 지점이었다. 남은 두 곳은 **판정에 영향이 없다** — 존재 여부(빈 문자열인가)만 보고 경로 문자열을 파일로 열지 않기 때문이다. 즉 오작동이 아니라 가독성 결손이며, `status --porcelain -z` 로 옮기려면 읽기 루프 구조를 함께 바꿔야 해서 이번 수정 범위 밖에 두었다(심각도: 낮음).
  ↳ 처리(2026-09-05): 해소 — PR #404가 개인 저장소 `personal_provenance_check`의 status·ls-files 두 호출에 `-c core.quotepath=false`를 더했다. 차단 판정은 그대로이고 한글 파일명이 읽히며 `tests/unit/test_deploy_provenance_nonascii.py`가 회귀를 고정한다.

  ↳ 재판정(2026-09-19, D174): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## peer 자가 스킬이 승인 심사 절차를 저작했다 (2026-09-05)

> [이관 2026-09-05 · 해소] 후속 과제 스윕 5 — 탐지는 코드로 닫혔고 노드 판독 권한은 위 OWNER가 소유한다.

- **`selfskill_audit` 는 자가 스킬의 이름 그림자(`SHADOWS-GOVERNED`)와 기능 겹침(`OVERLAPS-GOVERNED`)만 보고, SKILL.md 가 **승인 절차를 주장**하는지는 보지 않는다 → 본문이 승인 kind 접두어(`[release]`·`[skill-deploy]`·`[skill-publish]`)·`#approvals`·게이트 판정 어휘(`DO-NOT-APPROVE`·`승인 보류`)를 참조하면 `CLAIMS-APPROVAL-ROLE:<skill>` advisory 로 아침 보고에 싣는다.** peer 의 `autophagy-interop` 이 2026-09-01 에 `[release]` 심사 절차(라우팅 행·Quick Reference 50줄·참고 문서·교훈 12건)를 스스로 저작했고 v1.1.2~v1.2.2 의 모든 릴리스 카드에 거짓 ⛔ 를 붙였는데, 감사 원장에는 `edited` 델타 한 줄뿐이라 **무엇을** 저작했는지 아무도 보지 못했다(`docs/patch/2026-09-05-peer-gateway-ignores-approvals.md`). 토큰 매칭 규칙이라 오탐 상한은 governed 18개 상호 대조 0건으로 회귀 고정한다. 심각도 중 — 게이트·노드 동작은 무영향이지만 소유자 판단을 매 릴리스 오염시켰다.
  ↳ 처리(2026-09-05): 해소 — PR #408의 `automation/selfskill_audit/claims.py`가 본문 토큰을 검사해 아침 보고에 `CLAIMS-APPROVAL-ROLE:<skill>` advisory를 싣는다. 채널 토큰은 `ApprovalSurface`에서 지연 유도하고 governed corpus 0-hit는 `tests/unit/test_selfskill_claims.py`가 고정한다. 승인·차단 권한은 만들지 않는다.
  ↳ 재판정(2026-09-19, D175): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **peer 게이트웨이의 `discord.ignored_channels`(`#approvals`) 는 노드 config 에만 있고 저장소·프로브는 그 존재를 모른다 → healthcheck 의 read-only 프로브가 peer `config.yaml` 의 `discord.ignored_channels` 에 `#approvals` 채널 id(`channel_directory.json` 대조)가 들어 있는지 읽어 없으면 FAIL 로 낸다.** 다음 config 재생성·온보딩 재실행이 조용히 되돌릴 수 있고(2026-08-15 pin 부수 효과가 E7 을 되돌린 것과 같은 모양), 되돌아온 증상은 "카드에 peer 논평이 다시 붙는다" 뿐이라 발견이 다음 릴리스까지 늦다. 심각도 낮음(되돌아가도 게이트 판정은 불변).
  ↳ 처리(2026-09-05): 해소 — PR #409의 `automation/peer_gateway_probe.sh`를 healthcheck LOCAL `peer_ignored_channels`로 배선했다. peer 디렉터리에서 해석한 approvals id가 최상위 `discord.ignored_channels`에 있으면 PASS, 없으면 FAIL+`PEER-IGNORED-CHANNELS-RECOVERY`, 판독 불가면 `PEER-IGNORED-CHANNELS-UNREADABLE`로 FAIL-closed한다. allowlist 예시 매니페스트도 재생성됐다. [소개](기능소개/peer-게이트웨이-승인채널-차단-프로브.md).

  ↳ 재판정(2026-09-20, D176): VERIFIED-SNAPSHOT — peer 디렉터리의 유일한 approvals id가 최상위 discord.ignored_channels에 있음을 기존 엄격 파서로 확인했다. 실행 중 게이트웨이의 설정 적재까지 증명한 것은 아니다.
## 완결 타이머와 세션 release.sh 의 태그 경합 (2026-09-05)

> [이관 2026-09-05 · 해소] 후속 과제 스윕 5 — 기존 태그는 지우지 않고 재발 경로를 닫았다.

- **두 실행이 같은 ✅ 를 보고 동시에 태그를 자르면 한 커밋에 서명 태그가 둘 생긴다 → 완결기가 승인 레코드에 적힌 요청 버전을 재사용하게 한다.** 실측: 세션이 `--bump minor` 로 `v1.2.0` 을 13:11:22 에, 완결 타이머(`~/.hermes/release-completer/`)가 **기본 patch bump** 로 `v1.1.5` 를 13:11:23 에 잘랐다 — 둘 다 같은 커밋을 peel 하고 둘 다 update-trust 서명이 유효하다. `release_version_for` 의 "HEAD 에 이미 붙은 태그를 재사용" 은 태그가 **먼저 존재해야** 작동하므로 1초 차 경합을 막지 못한다.
  ↳ 처리(2026-09-05): 해소 — PR #406의 `release.sh`가 HEAD의 살아 있는 승인 레코드 버전을 next 계산보다 먼저 재사용한다. 세션·완결기가 태그 생성 전에도 같은 요청 버전으로 수렴한다.
  ↳ 재판정(2026-09-19, D177): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **갈라지는 조건은 좁다** — 세션이 `--bump patch`(완결기 기본값과 같음)로 돌면 양쪽이 같은 이름을 계산하고 `ensure_signed_tag` 가 동일 이름 기존 태그를 찾아 멱등 성공한다(v1.2.1·v1.2.2 실측: 중복 없음). 즉 문제는 **세션이 minor·major 를 쓸 때만** 나타난다.
  ↳ 처리(2026-09-05): 해소 — PR #406은 bump 종류가 아니라 승인 레코드의 version을 공통 입력으로 삼으므로 minor·major도 patch와 같은 태그 이름으로 수렴한다.
  ↳ 재판정(2026-09-19, D178): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- 프로덕션 영향은 없다(같은 커밋·같은 키라 어느 쪽으로 수렴해도 같은 코드). 다만 `public_export.sh` 는 `--version` 생략 시 source 커밋의 vX.Y.Z 태그가 **정확히 하나**여야 해서 `source commit has multiple semantic release tags` 로 막히므로, 그 릴리스만 `--version` 을 명시해야 한다. 서명 태그는 지우지 않는다(롤백 방지 floor 는 앞으로만 간다) — 심각도 낮음.
  ↳ 처리(2026-09-05): 해소 — PR #406이 동일 승인에서 이중 태그를 새로 만드는 원인을 닫아 이후 릴리스는 이 경합 때문에 `public_export.sh --version`을 따로 지정할 필요가 없다. 과거 이중 태그는 그대로 보존하므로 그 과거 릴리스를 반출할 때의 명시 요건은 유지한다(반출기 코드 변경 없음).
  ↳ 재판정(2026-09-19, D179): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **태그뿐 아니라 배포 실행도 경합한다 — 세션 `release.sh` 의 `deploy_all --apply` 와 완결 타이머의 `deploy_all --apply` 가 같은 ✅ 를 보고 동시에 스킬을 배포해, 스킬별 실행 lock 에서 진 쪽이 `EXECUTION-LOCK-BLOCK` 으로 실패한다 → 완결기가 같은 head 의 살아 있는 세션 실행을 감지하면 그 틱을 양보하거나, `deploy_all` 이 lock-blocked 스킬을 실패가 아니라 '다른 실행이 수렴 중' 으로 분류해 종료 전 재판정한다.** v1.2.3 실측(2026-09-05): 세션 17:32:36 시작, 완결기 17:33:42 시작 → 세션은 `meeting` 을, 완결기는 `speechtotext` 를 각각 양보했고 완결기 1차 실행은 `Failed with result 'exit-code'`(17:35:32), 2차 틱이 `already fully deployed`(17:36:26) 로 닫았다. 최종 상태는 양쪽 모두 `fully deployed` 로 수렴했고 `deploy_all --verify` rc 0 — 프로덕션 영향 없음. 다만 완결기의 sha 별 3회 상한을 한 번 소모하고 실패 저널이 남으므로, 세션이 `--no-deploy` 없이 돌 때마다 반복된다(심각도 낮음).
  ↳ 처리(2026-09-05): 해소 — PR #407의 `deploy_all.sh --apply`가 워크스테이션 `${DEPLOY_ALL_LOCK_DIR:-$HOME/.hermes/deploy-all}/apply.lock`을 flock으로 잡는다. 후행 실행은 `APPLY-LOCK-WAIT`→lock 획득 후 재판정→이미 전량 반영됐으면 `APPLY-LOCK-RECHECK`(`already fully deployed`)·rc 0으로 끝나며 `--plan`·`--verify`는 lock-free다. PR #413은 이 동시 실행 회귀의 release FIFO를 O_RDWR로 유지해 #409 첫 CI를 막았던 테스트 경합도 닫았다.

  ↳ 재판정(2026-09-19, D180): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## v1.1.0 편의 릴리스 착지 후 남긴 것 (2026-09-03)

> [이관 2026-09-05 · 해소] 후속 과제 스윕 5.

- **`release.sh` 를 손으로 돌린 세션과 워크스테이션 완결 타이머(`autophagy-release-complete.timer`)가 같은 ✅ 를 보고 `deploy_all --apply` 를 동시에 돌렸다 — 스킬별 실행 lock 이 서로를 `EXECUTION-LOCK-BLOCK` 으로 막아 두 실행 모두 `incomplete` 로 끝났고, 합집합이 우연히 전부 마운트돼 영수증은 다음 `--verify` 에서야 나왔다(v1.1.2 실측) → 릴리스 단위 lock(`/srv/autophagy-private/deploy-all/`)을 deploy_all 이 잡거나, 완결 타이머가 활성이면 `release.sh` 가 deploy 를 완결기에 위임하고 폴링만 하도록 한다.** 영향 범위: 이중 실행 자체는 멱등(마운트 digest 대조)이라 손상 없음, 다만 한쪽이 `SKILL-STALE`·rc=10 으로 끝나 사람이 오독한다 — 심각도 낮음.
  ↳ 처리(2026-09-05): 해소 — PR #407이 위 태그 경합 묶음의 배포 경합과 같은 원인을 워크스테이션 apply flock으로 닫았다(노드 lock 신설 아님). 후행 실행은 대기 후 재판정해 이미 전량 반영이면 배포기 재호출 없이 영수증·rc 0으로 끝난다.

  ↳ 재판정(2026-09-19, D181): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 2026-09-04 plaud 구간 전사 수리 (t_4e3d6630) 잔여

> [이관 2026-09-05 · 해소] 후속 과제 스윕 5 — 같은 헤딩의 기존 OWNER 불릿(빈 노트 upsert 승인)은 별도 보존한다.

- **반복 붕괴 구간은 회의록 경로에서 본문째 빠진다** — 임계 0.08 을 넘긴 구간은 표식만 남는다(라이프로그는
  `SPEECHTOTEXT_ALLOW_INCOMPLETE=1` 이라 보존). 실측: 사고 원본 61분의 구간 1 이 0.18 이라 회의록 정책에서는 15분이
  빠졌다. 전량 소실이던 예전보다는 순개선이라 이번 범위에서는 그대로 두고, 반복 구간을 **버리는 대신 접어서** 남기는
  방안은 별도 판단으로 남긴다. 심각도 낮음(표식으로 보이며 조용히 사라지지 않는다).
  ↳ 처리(2026-09-05): 해소 — PR #411(speechtotext 1.2.2 / meeting 1.7.1)이 반복 의심 창을 기존 표식 아래 HTML `<details>`에 FOLDED로 보존한다. `stt_blocks` render/parse 왕복이 접힌 원문을 유지하고 `meeting_extract.strip_folded()`는 `{{MEETING_TEXT}}`가 프롬프트 입력이 되는 경계에서만 제거한다. 민감도 검사·부록 원문은 그대로다. [소개](기능소개/plaud-긴-녹음-구간-전사.md).
  ↳ 재판정(2026-09-19, D182): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`automation/plaud_sync/mcp_client.py` 가 정확히 250 pure LOC** — 이번 변경 밖이지만 한 줄만 더해도 F2 파일 크기
  등록부에 예외를 쌓는다. 조치: 그 파일을 다음에 만질 때 분할한다. **심각도 낮음**.
  ↳ 처리(2026-09-05): 해소 — PR #410이 stdio 프로세스·프레이밍을 `automation/plaud_sync/mcp_transport.py`로 분리했다. client·transport 각각 155 pure LOC이며 `mcp_client`의 공개 import 경로는 그대로다.

  ↳ 재판정(2026-09-19, D183): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 릴리스 승인 카드가 peer 시야에 있다 (2026-09-05)

> [이관 2026-09-05 · 해소] follow-ups.md 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`[release]` 승인 카드가 `#approvals`(SKILL_APPROVALS 표면)에 게시돼 peer 봇이 보고, peer 가 매번 즉석 심사해 `⛔ 배포 승인 보류 / DO-NOT-APPROVE` 를 붙인다 → `release` kind 를 요청별 `#agent-chat` 스레드로 옮겨 peer 시야에서 뺀다.** 그 예외의 사유는 `tests/unit/test_request_thread_adoption_conformance.py:28-34` 에 적혀 있다 — "2차 주체인 peer 봇이 같은 채널을 봐야 하므로". 그런데 **`release` 만은 그 사유가 성립하지 않는다**: VA-1 릴리스 승인은 소유자 단독 ✅ 이고 peer attestation 이 없다(skill-deploy·attest·publish·submit·managed-activate 는 성립한다). 즉 peer 가 볼 이유가 없는 유일한 kind 가 peer 가 보는 채널에 있다.
  ↳ 재판정(2026-09-20, D184): VERIFIED-SNAPSHOT — 실제 v1.9.2 요청 카드가 포함된 최신 메시지 조회 창에서 그 카드 이후 보이는 다른 봇 발언은 0건이었다. 반응도 봇의 각 1개뿐이었다. 삭제된 과거 발언이나 모든 미래 요청까지 보장하지 않는다.
- **오탐은 구조적이라 정확도를 고쳐서는 닫히지 않는다.** peer 는 HEAD 를 노드 로컬 상태에서 찾는다(카드가 `origin/main = <직전 릴리스 sha>`, `운영 포인터 = /srv/autophagy-agent-releases/<직전 sha>` 를 함께 보고한다 — 둘 다 노드 값이다). 미러는 「ops 체크아웃 단방향 규칙」의 `sync_mirror` 대로 릴리스가 수렴한 **뒤에야** origin/main 을 따라오는데, 승인 카드는 정의상 아직 배포되지 않은 HEAD 를 가리킨다 — **승인 전에 통과할 수 있는 순간이 없다.** 2026-09-05 실측 반증: peer 가 "공유 저장소에 없음" 이라 한 커밋을 빈 저장소에서 `git fetch --depth 1 <origin> <sha>` 로 직접 받아냈고, `git ls-remote` 의 `refs/heads/main` 이 바로 그 sha 였다.
  ↳ 재판정(2026-09-20, D185): DECISION-PRESERVED — 실제 릴리스 요청은 공급망 approvals 표면에 남고 차단 설정·현재 관측은 D176·D184로 확인했다. 거절된 표면 이전이나 미러 HEAD 심사 재설계를 다시 구현하지 않았다.
- **자가 스킬 회수로는 닫히지 않았다(2026-09-04 시도).** peer 의 `skill-deploy-review` 를 아카이브한 뒤에도 같은 심사가 나왔고 문구만 `DO-NOT-APPROVE` → `배포 승인 보류` 로 바뀌었다. 실측: peer 1차 루트에 `autophagy-interop` 뿐이고 그 스킬은 여전히 아카이브 상태다. 즉 원인은 스킬이 아니라 **그 카드가 peer 에게 보인다는 사실** 이다.
  ↳ 재판정(2026-09-20, D186): HISTORY-UNVERIFIED — 자가 스킬 회수만을 종결 근거로 삼지 않고 현재 차단 설정과 실제 요청 창을 함께 관측했다. 현행 자가 저작 절차 본문은 재감사하지 않았으므로 이 세션에서 제거됐다고 주장하지 않는다.
- 조치 전 확인할 것: `surface_for(release)` 가 바뀌면 `approval_surface.POLICY_VERSION`(현재 8) bump 가 필요한지 — 필요하면 MAJOR 신호라 `release.sh --bump major` 사이클이다. 레코드 소비자(`skill_gate.py`·`release_approval.py`·`deploy_all.py`)는 채널이 아니라 레코드를 읽으므로 영향 없을 것으로 보이나 확인이 필요하다. 영향: 매 릴리스마다 거짓 ⛔ 가 붙어 소유자가 멀쩡한 릴리스를 막거나, 반대로 경보를 무시하는 습관이 든다 — 심각도 중(릴리스·노드 동작은 무영향).
  ↳ 처리(2026-09-05): 적어 둔 조치(B — `release` kind 를 `#agent-chat` 요청별 스레드로 이동)는 **채택하지 않았다**. 소유자 결정: 인터롭(공유 Lab 의 보고·조율)은 계속 필요하므로 peer LLM 게이트웨이는 유지하고, 근본 원인 두 겹만 걷어냈다. **원인 정정** — 3번째 불릿의 "원인은 스킬이 아니라 카드가 보인다는 사실" 은 절반만 맞았다: 심사를 지시한 문서는 아카이브한 `skill-deploy-review` 가 아니라 **pin 된 `autophagy-interop`** 이었다(2026-09-01 자가 저작 — 라우팅 표 `[release]` 행, `### [release] — Quick Reference` 50줄, `references/release-approval.md`, 교훈 불릿 12건; 그 절차가 노드 미러의 `git rev-parse origin/main` 과 `readlink /srv/autophagy-agent-current` 로 HEAD 를 찾으라고 적혀 있어 승인 전에는 통과할 수 없었다). **A(전송 계층)**: peer `~/.hermes/config.yaml` 최상위 `discord.ignored_channels` 에 `#approvals` 채널 id 를 넣었다 — Hermes Discord 어댑터의 거부 목록은 스레드의 부모 채널 id 까지 대조하고(`channel_keys`) `allowed_channels` 보다 우선하므로 카드 auto-thread 까지 막힌다. config 백업 후 YAML 파싱 검증, agent+peer 게이트웨이 쌍 재시동(둘 다 `active`, NRestarts 0). **C(지시 계층)**: `autophagy-interop` 을 tarball 백업(sha256 `a60d1fee…`)한 뒤 위 절차 전부와 `[skill-deploy]`·`[skill-publish]` 행·`scripts/publish-verify.py` 를 제거했다(344→231행, 잔존 참조 0, `hermes skills list` enabled 유지). PATENT EXPORT·Drive 아카이브·blocked-review 는 그대로다. peer attestation 은 `peer_attest.py` 가 REST 로 게시하는 경로라 무영향. 4번째 불릿(POLICY_VERSION bump 검토)은 B 를 하지 않으므로 소멸. 실표면 검증 지점: v1.2.3 승인 카드에 peer 논평이 없어야 한다. 상세 `docs/patch/2026-09-05-peer-gateway-ignores-approvals.md`.

  ↳ 재판정(2026-09-19, D187): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 동결 해제·repair 재발 수리 착지 후 남긴 것 (2026-09-04)

> [이관 2026-09-04 · 해소] follow-ups.md 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **재발 판정이 detect 경로에 `hermes kanban show` 왕복을 하나 더 얹는다** — `HermesKanban.is_closed` 는 `_run` 의 timeout=60 을 쓰므로, 보드가 응답하지 않으면 재발 1건당 최대 60초를 쓰고 나서야 fail-soft 로 예전 동작(occurrence 증가)으로 떨어진다. 실패 경로에서만 발생하고 카드·원장은 정확히 유지되지만, 워처가 몰아서 detect 할 때 지연이 합쳐질 수 있다. 조치: 노드에서 재발 실측을 몇 건 모은 뒤 필요하면 이 조회에만 짧은 전용 timeout 을 준다. **동작 결함 아님 · 심각도 낮음**.
  ↳ 해소(2026-09-04): 상태 조회에 전용 10초 상한을 줬다 — `HermesKanban._run(*args, timeout=_BOARD_TIMEOUT)` 가 mutation 의 60초를 유지하고 `is_closed` 만 `_STATUS_READ_TIMEOUT` 을 넘긴다. 노드 실측을 기다리지 않은 이유는 실패 방향이 바뀌지 않기 때문이다 — 조회가 끊기면 예전 동작(occurrence 증가)으로 떨어지므로 짧은 상한은 최악 지연만 6분의 1로 줄인다. 회귀 `tests/unit/test_repair_tickets.py::test_status_read_gives_up_sooner_than_a_board_mutation` 가 실제 subprocess 경계에서 show·create 의 timeout 을 비교한다.
  ↳ 재판정(2026-09-19, D188): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **닫힌 카드로 새 티켓을 발급할 때 예전 티켓과의 연결이 원장에만 남는다** — `RepairRegistry` 는 signature 항목을 새 ticket_id 로 덮어써 occurrence 를 1 로 되돌리므로, 새 카드 본문에는 "이전 카드 t_… 의 재발"이라는 문구가 없다. 소유자가 보드에서 두 카드를 잇는 단서는 같은 제목·signature 뿐이다. 조치: 새 카드 생성 시 이전 ticket_id 를 본문 한 줄로 싣는다(마스킹 규칙 그대로 — id 는 비밀이 아니다). **가시성 개선 · 심각도 낮음**.
  ↳ 해소(2026-09-04): 새 카드 본문이 `Supersedes closed card: t_…` 로 이전 카드를 지목한다. 같은 자리에서 **그 발급이 실제로는 새 카드를 만들지 못하던 결함**도 함께 닫았다 — `hermes kanban create --idempotency-key` 는 non-archived 동일 키에 기존 카드 id 를 돌려주므로(`docs/qa/RRC-0/01-cli-contract.md` ④) done 카드의 재발이 그 닫힌 카드로 되돌아가고 occurrence 만 1 로 리셋됐다. 이제 멱등키에 이전 카드 id 를 붙인다. 회귀 `tests/unit/test_repair_tickets.py::test_superseding_card_names_the_closed_card_and_asks_a_distinct_dedup_key`.

  ↳ 재판정(2026-09-19, D189): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 회의록 과제 양식·Action Item 데이터베이스 중 남긴 것 (2026-08-27)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **`skills/meeting/scripts/meeting_minutes.py` 가 정확히 250 pure-LOC 에 앉았다** — 천장과 같은
  값이라 다음 사이클이 한 줄만 더해도 F2 게이트가 빨개진다. 이음새는 이미 보인다: 양식 배치 경로
  (`_form_body` 와 그것이 부르는 슬롯 조립)를 `meeting_template` 쪽이나 별도 모듈로 떼면 여유가 생긴다.
  조치: 회의록 렌더러를 다음에 여는 사이클에서 그 분할을 함께 한다. **동작 영향 없음 · 심각도 낮음**.

  [해소 확인 2026-08-31] 양식 배치 경로를 `meeting_template` 로 이관해 233 pure LOC — 천장 여유 확보. 회귀: meeting 스코프 247 passed(`test_meeting_minutes.py` 포함).
  ↳ 재판정(2026-09-19, D190): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`meeting_action_db.items_from` 이 `Sequence[object]` 를 받는다** — 안에서
  `item.title`·`item.deadline`·`item.owner`·`item.basis` 를 읽는데 타입 검사기는 그 속성을 볼 수 없어
  오타나 필드 개명이 런타임까지 간다. 조치: 그 네 속성을 가진 `Protocol` 을 선언해 시그니처를 바꾼다
  (`meeting_types` import 는 순환이 되므로 구조적 타입이어야 한다). **동작 영향 없음 · 심각도 낮음**.

  [해소 확인 2026-08-31] `ActionSource` Protocol(title·deadline·owner·basis)을 `meeting_action_db` 안에 선언해 시그니처를 구조적 타입으로 바꿨다 — `tests/unit/test_meeting_action_db_protocol.py`.
  ↳ 재판정(2026-09-19, D191): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **meeting 야간 배치가 lock 을 최대 50분 잡으면 speechtotext 가 그동안 10틱 스킵된다** —
  상한 5건 × 실측 258.9초다. 데이터 손상은 없고 다음 틱에 회복되지만, 밤에 올라온 녹음의
  전사가 그만큼 밀린다. 조치: 상한을 3으로 낮추거나(최악 13분) 야간 배치를 5분 틱과 덜
  겹치는 시각으로 옮긴다 — 소유자가 자정을 지정했으므로 상한 쪽이 먼저다.
  **오동작 아님 · 심각도 낮음**.

  [해소 확인 2026-08-31] 야간 배치 기본 상한 5→3(최악 약 13분), env 오버라이드 유지 — `tests/unit/test_meeting_pending_watch.py` RED→GREEN.
  ↳ 재판정(2026-09-19, D192): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`ACTION-ID-EXHAUSTED` 가 소유자 통지에 실리지 않는다** — 과제·연도의 일련번호(10,000건)가 바닥나면
  회의록은 그대로 쓰고 신규 표의 번호 자리만 `—` 가 되지만, 그 사실은 stderr 마커와 실행 로그에만 남고
  완료 통지에는 나오지 않는다. 소유자가 번호 빠진 표를 눈으로 알아채야 한다는 뜻이다. 조치:
  `format_notify` 에 한 줄을 더하거나 요약 JSON 의 `actions_new` 와 함께 통지 문구를 분기한다.
  **회의록 생성은 정상 · 심각도 낮음**(한도가 과제·연도당 10,000건이라 도달 자체가 드물다).

  [해소 확인 2026-08-31] `format_notify` 가 관리번호 소진을 완료 통지에 명시한다 — `tests/unit/test_meeting_project_ingest.py` RED→GREEN.

  ↳ 재판정(2026-09-19, D193): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Gmail 승인 초안 Cc 보존 수리 중 남긴 것 (2026-08-26, t_0c46c0ad)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **`mail_preflight.py`가 250 pure-LOC 천장을 넘었다** — 정확히 250에 앉아 있던 파일이 Cc 보존
  수정으로 262가 되어 `automation/final/f2_loc_exceptions.txt`에 사유와 함께 등록했다. 조치:
  런타임 루트 해석 계층(`repo_root`/`_repo_module`/`_contracts`/`_gate`)을 별도 모듈로 분리하되,
  배포 스킬 import 표면(`tests/unit/test_skill_preflight_deployed_imports.py`)을 같은 사이클에서
  다시 검증한다. **코드 위생 · 심각도 낮음**(동작 영향 없음).

관련 회귀: `tests/unit/test_gmail_cc_preservation.py`, `tests/unit/test_mail_watch_draft_isolation.py`.

  [해소 확인 2026-08-31] 런타임 루트 해석 계층을 `mail_runtime.py` 로 분리(217 pure LOC, `meeting_runtime` 선례) — `test_skill_preflight_deployed_imports.py` 18 passed 로 배포 import 표면 재검증.

  ↳ 재판정(2026-09-19, D194): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 후속 과제 원장 분리(2026-08-26) 중 남긴 것

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **`automation/rag_stack/deploy.sh` 만 호스트 미지정 가드가 없다** — 나머지 17종은 이번에
  `DEPLOY-BLOCK` 으로 닫았지만, 이 스크립트의 호스트 해석 줄은 `tests/unit/test_rag_stack_deploy.py`
  가 provenance·lock 계약의 일부로 **문자열 그대로 고정**한다. 가드를 넣으려면 그 계약을 함께
  바꿔야 하는데 대상 노드도 변수도 다르다(`RAG_STACK_SSH_HOST`). 지금은
  `tests/unit/test_deploy_host_fail_closed_all.py` 의 `_CONTRACT_PINNED` 에 사유와 함께 제외돼 있다.
  조치: rag 노드 배포 계약을 여는 사이클에서 같은 가드를 넣고 그 pin 을 함께 갱신한다.
  **동작 영향 없음(빈 호스트면 지금도 ssh 가 실패한다) · 심각도 낮음**.

  [해소 확인 2026-08-31] 빈 호스트 `DEPLOY-BLOCK` 가드 추가(문구는 RAG 변수 기준으로 정정), `test_deploy_host_fail_closed_all.py` 의 `_CONTRACT_PINNED` 제외 제거로 보편 검사 편입 — 45 passed.
  ↳ 재판정(2026-09-19, D195): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`skills/meeting/scripts/meeting_actions.py` 가 254 pure-LOC 로 천장을 넘어 등록부에 올랐다** — 어느 쪽
  변경도 혼자서는 넘지 않았다. main 의 #287·#290 이 더한 카드 디스패치 줄과 이 브랜치가 더한 회의 날짜
  파일명 줄이 머지에서 만나 넘겼다. 이음새는 이미 보인다: 마일스톤 YAML 4함수(`_yaml_str`·`_emit_milestones`·
  `_parse_milestones`·`update_milestones`, 약 80줄)를 `meeting_milestones.py` 로 떼면 176 줄로 내려간다.
  조치: 그 분할을 별도 사이클에서 한다 — 머지 커밋 안의 80줄 이동은 리뷰 범위를 벗어난다.
  **동작 영향 없음 · 심각도 낮음**.

  [해소 확인 2026-08-31] 마일스톤 YAML 4함수를 `meeting_milestones.py` 로 분리(212 pure LOC), re-export 로 호출부 무변경 — meeting 스코프 green.

  ↳ 재판정(2026-09-19, D196): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## DM→#agent-chat 이관(승인 표면 v7) 중 발견한 후속 과제 (2026-08-24)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **잔여 owner-DM 발신자들이 남아 있다** — 승인 경로 밖 통지 발신자 중 이번에 이관한
  것은 research_trends·mail `dm_owner`뿐이다. 잔여: `procure_review.send_review`,
  `cost-report.send_cost_report`, `interop.gate_driver.main`,
  `hermes_plugin._send_direct_result`, `reminder_poller.DmSender`,
  `memory_curator.alert_owner`, `selfskill_audit/report`, `budget_confirm.dm_owner`,
  `patent_export_gate.dm_owner`(이상 agent 봇) + `owner_notice`/`healthcheck_notify`
  (ops 봇 — 길드 미참여 제약 동일). 조치: agent 봇 발신분부터 「config 있으면 채널 직송,
  없으면 DM 폴백」 선례 패턴을 순차 적용. **통지 도달에는 문제 없음 · 심각도 낮음**.

  [해소 확인 2026-08-31] agent 봇 발신자 8곳(gate_driver·hermes_plugin·reminder_poller·memory_curator·procure_review·cost-report·selfskill_audit·budget_confirm)을 owner_notice 파사드 채널-우선으로 이관(면제 제거) — `test_owner_notice_sender_migration*.py` RED→GREEN. `patent_export_gate` 는 특허 링크 노출 때문에 DM 유지(면제 사유 명시), ops 봇 2곳은 길드 미참여 제약으로 기존 보류 불릿이 소유.

  ↳ 재판정(2026-09-19, D197): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **스킬 시나리오 17개 중 배포 전에 실제로 실행되는 것은 3개뿐이다** — 이번에 더한 `tests/unit/test_skill_scenario_drift.py`는 실행 없이 보이는 드리프트(사라진 저장소 경로 참조, `bash -n` 파싱 실패)만 잡는다. calendar·mail·wiki 외 14개는 여전히 배포 시점에야 깨진 것이 드러난다. **배포 전 발견 비용만 영향 · 동작·보안 문제 아님 · 심각도 낮음** → 스킬별로 임시 HOME·더미 자격증명에서 시나리오를 실행하는 하네스를 하나씩 늘린다. 한 번에 17개를 유닛 스위트에 넣는 것은 금물이다 — 메일 발송·브라우저 기동이 섞여 있어 외부효과가 난다.

  [해소 확인 2026-08-31] `tests/unit/test_skill_scenario_execution.py` 신설 — recall·topics 시나리오를 임시 HOME·더미 자격증명으로 실제 실행(3→5), 잔여 시나리오는 제외 사유를 분류표(docstring)에 각각 명시(다음 후보 report·prompt).

  ↳ 재판정(2026-09-19, D198): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #516. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

보드의 열린 카드 8건을 정리하며 healthcheck 허용목록 래퍼(`<primary-node>`·`<rag-node>`)를 재생성하고
runtime-package 프로브의 `cron/` 오탐을 고쳤다. 그 과정에서 드러난 구조적 틈이다.

- **RAG 노드(`<rag-node>`)의 래퍼는 드리프트 프로브 대상이 아니다** — `healthcheck probe allowlist
  matches the checks`는 PRIMARY_NODE만 보고, RAG 노드의 래퍼는 07-14 손유지본(해시 3개)이라
  rag_stack 프로브와 그 티켓 명령이 거부되고 있었다(UNKNOWN·`REPAIR_TICKET_FAILED rc=126`). 이번에
  `--print <rag-node>`로 생성해 수동 설치했다(구본 `.bak-20260822-handmaintained`) → RAG 노드용 래퍼
  프로브 행을 추가한다. **수동 설치로 해소 · 심각도 낮음**.

  [해소 확인 2026-08-31] `healthcheck_wrapper_current` RAG 노드 행과 수리 매니페스트 항목 추가 — `tests/unit/test_healthcheck_rag_wrapper_probe.py` RED→GREEN, healthcheck 스코프 107 passed.

  ↳ 재판정(2026-09-19, D199): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## proposal v7 피드백 반영에서 발견 (2026-08-28)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **버전 사본의 `out/drafts.json` 이 refined 계보에서 갈라져 있다** — v000006 의
  `out/drafts.json` 은 그림 참조 5개(옛 세대)인데 실제 렌더 계보 `drafts.refined.json` 은
  6개(fig-s3-04 포함)다. 다음 판을 스테이징할 때 `drafts.json` 을 복사하면 refine→render 가
  `UNREFERENCED_FIGURE` 로 죽는다(v000007 제작 중 실측, refined 계보로 교체해 해소).
  조치 방향: improve/스테이징이 자식의 `drafts.json` 을 만들 때 부모의 refined 산출이 있으면
  그것을 기준 초안으로 삼도록 정리. 영향 범위: 수동 스테이징·improve 경로 한정, 렌더 게이트가
  fail-closed 로 잡아주므로 조용한 오염은 없음 — 심각도 낮음.

  [해소 확인 2026-08-31] 자식 스테이징이 부모 `out/drafts.refined.json` 을 기준 초안으로 삼는다(부재 시 `drafts.json` 폴백) — `tests/unit/test_proposal_staging_refined_lineage.py` RED→GREEN.

  ↳ 재판정(2026-09-19, D200): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 스킬 배포 사이클(2026-08-29) 중 발견한 후속 과제

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **proposal 은 마운트 후 invoke smoke 가 항상 실패한다** — `skills/proposal/scripts/scenario.sh` 가
  패키지 루트를 위치로 유추한다(`repo_root="$script_dir/../../.."`). peer 샌드박스에서 그것은
  `~/.hermes` 라 그 아래 `skills/proposal/…` 이 있어 `python3 -m skills.proposal.scripts.proposal_version`
  이 성립하지만, 라이브 스토어에서는 `/srv` 가 되어 `skills` 패키지가 없다 →
  `ModuleNotFoundError: No module named 'skills'` → `post-mount invoke smoke failed on agent`(exit 4).
  2026-08-29 실측: `INSTALLED`·`CONSUMED` 까지 끝난 뒤 이 단계에서만 죽어 틱이 `resume-exit:4` 를 남겼다.
  조치 방향: version 단계를 한 단 위 루트(`$script_dir/../..`)와 `python3 -m proposal.scripts.proposal_version`
  으로 바꾸면 두 배치 모두에서 성립한다(라이브 스토어에서 실행 확인). 또는 샌드박스 호출이 이미 넘기는
  `AUTOPHAGY_REPO_ROOT` 를 마운트 후 호출에도 넘기고 scenario 가 그것을 우선 쓰게 한다
  (`automation/deploy-skill.sh:809` 는 넘기고 `:994` 는 넘기지 않는다). 영향 범위: 마운트는 이미 성립한
  뒤라 배포를 막지 않는다 — 다만 공급망 워처 유닛이 매번 failed 로 끝나 진짜 실패와 구분되지 않고
  백오프 지문을 오염시킨다. 심각도 중.
  [해소 확인 2026-08-31] `AUTOPHAGY_REPO_ROOT` 우선 + 상위 루트/`proposal.scripts` 폴백으로 repo·샌드박스·라이브 세 배치 모두 실측 통과 — `test_skill_scenario_drift.py` 6 passed.
  ↳ 재판정(2026-09-19, D201): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **소유자 ✅ 이후에도 재개가 15회 연속 실패했는데 사유를 재현할 수 없다** — 2026-08-28 15:01 ~ 08-29 13:52
  동안 `skill-deploy:proposal` 이 같은 지문으로 15회 실패했고, 저널에 남은 사유는
  `REJECTED: valid peer attestation absent` → `REJECTED: owner approval binding invalid` 였다.
  `skill_gate_refresh.refresh_required` 는 `_owner_approval_present` 를 통과한 뒤에만 후자를 찍으므로
  **소유자 승인은 읽혔는데 `valid_approval` 이 바인딩을 무효로 판정했다**는 뜻이다. 그 pending 레코드가
  뒤이어 사라져 지금은 재현할 수 없다. 조치 방향: 다음 발생 시 레코드를 지우기 전에 보존하고,
  `skill_gate_approval.valid_approval` 의 거부를 사유별 토큰으로 나눠 로그에 남긴다. 영향 범위:
  peer attestation TTL 이 만료된 뒤 승인하면 릴리스가 바뀌기 전까지 배포가 서는 경로 — 소유자가 즉시
  누르지 못한 모든 배포가 해당한다. 심각도 높음.
  [해소 확인 2026-08-31] `valid_approval` 거부를 `APPROVAL-BINDING-REJECT:<cause>` 사유 토큰으로 분리하고 거부된 pending 레코드를 `pending-rejected/` 에 보존한다 — `tests/unit/test_skill_gate_approval_reject_reasons.py` RED→GREEN.
  ↳ 재판정(2026-09-19, D202): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **supersede 후 게시가 실패하면 승인 요청이 통째로 사라지고 아무 신호가 없다** — 2026-08-29 13:52 실측:
  어떤 실행이 proposal 을 REVIEW 까지 3회 진행했는데(`review-verdicts.jsonl` 3행, digest `537e39a4`)
  `proposals.jsonl` 에는 한 줄도 붙지 않았고 `pending/` 은 비어 있었다. 즉 기존 요청을 supersede 해
  메시지·레코드를 지운 뒤 새 요청 게시에 도달하지 못했고, 결과적으로 **소유자가 누를 요청이 0건인
  상태**가 되었다. 레코드가 없으면 틱은 아무 말도 하지 않으므로 침묵으로 남고, 소유자는 이미 눌렀다고
  믿는다. 「승인 메시지 단일성 규칙」이 막으려던 고아 메시지의 반대 방향이다. 조치 방향: 파사드의
  supersede→게시 구간에서 게시가 성공하기 전에는 기존 레코드를 지우지 않도록 바꾸거나, 최소한 게시
  실패를 기존 owner notice 경로로 표면화한다. 영향 범위: 승인 대기 중이던 결정이 조용히 증발 — 승인
  게이트의 가용성 결함이며 인가 경계는 훼손되지 않는다. 심각도 높음. 증적: 2026-08-29 배포 사이클
  (릴리스 `ec39b11f`/v1.0.133, proposal `deb0a49b`→`537e39a4`).

  [해소 확인 2026-08-31] supersede 후 게시 실패가 조용히 pending 0건으로 끝나지 않는다 — 레코드 복원/`SUPERSEDE-PUBLISH-FAILED` 표면화 + 주입 notifier seam(게이트 staging 체인 import 0 추가) — `tests/unit/test_approval_lifecycle_supersede_no_loss.py` RED→GREEN.

  ↳ 재판정(2026-09-19, D203): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 릴리스 운영 하드닝 잔여 (2026-08-31)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **⛔ 된 release 레코드가 다음 릴리스 요청을 막는다** — lifecycle 은 결정된(취소 포함)
  요청을 파괴하지 않으므로, ⛔ 된 `pending/release.json` 이 남으면 다음 `release.sh` 의
  요청 게시가 `owner-decided` 로 DEFER 된다. 스킬 게이트에는 감사 탈출구
  `skill_gate_retire.abandon` 이 있으나 release kind 에는 대응물이 없다. 조치 방향:
  release 전용의 감사되는 abandon(3필드 일치·fsync 감사·메시지 비삭제)을 설계해 붙인다.
  영향 범위: 소유자가 릴리스를 ⛔ 한 뒤의 다음 릴리스만 해당 — 인가 경계는 훼손되지
  않는 가용성 결함. 심각도 중.
  [해소 확인 2026-08-31] `automation/release_abandon.py` — 3필드 일치·fsync 감사·Discord 메시지 비삭제·byte-exact 아카이브(0600), `release.sh` 거부 메시지에 명령 힌트 — `tests/unit/test_release_abandon.py` 15 passed.
  ↳ 재판정(2026-09-19, D204): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **todo 스킬 샌드박스 시나리오가 `NOTIFY-FAIL`(TodoApprovalError) 3건을 남긴다** —
  2026-08-31 v1.0.139 전량 배포 중 todo SANDBOX 단계에서 실측. 결과 통지는 best-effort
  라 배포 판정에는 무영향이나, 더미 시크릿 경로라면 `NOTIFY-SKIP` 이어야 소음이 아니다.
  조치 방향: todo 시나리오의 통지 leg 가 E2E 주입 경로에서 SKIP 마커를 내는지 확인하고
  아니면 맞춘다. 영향 범위: 로그 소음뿐. 심각도 낮음.

  [해소 확인 2026-08-31] DUMMY- 시크릿 경로가 `NOTIFY-SKIP reason=dummy_secret` 을 낸다 — `tests/unit/test_todo_notify_sandbox.py` RED→GREEN + 실제 scenario `SCENARIO-PASS`.


  ↳ 재판정(2026-09-19, D205): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 제안서 HWPX 품질 수리 중 남긴 것 (2026-08-26)

> [이관 2026-08-31] 이미 닫힌 항목의 원문 보존.

- **릴리스 위생 게이트가 git SHA 를 Discord ID 로 오탐했다(해소)** — 스노플레이크 매처 `[0-9]{17,19}`
  가 문맥 없이 숫자 열만 봐서, 40자리 객체명 안의 17자리 연속 숫자에 걸렸다(`08a0693…705138677`).
  토큰 경계를 넣어 해소했고 진짜 ID 는 그대로 잡힌다. allowlist 로 우회하지 않았다 — 그랬다면 다음
  핀에서 같은 일이 반복된다.
소유자가 받은 `excavator/v000001` 이 "완성된 제안서로 볼 수 없다"고 지적해 렌더 체인과 집필 경로를
고쳤다. 본문 품질(마크다운 잔존·문단 분리·그림·자간)은 닫았고, 아래는 같은 문서에서 드러났으나
이번 범위 밖이라 남긴 것들이다. 증적: 구조 프로브와 렌더 페이지 이미지(마스킹 전 원시는 ops 전용).
  ↳ 재판정(2026-09-19, D206): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`merge-pr.sh` 가 계산 중인 mergeability 를 충돌로 보고한다** — push 직후 GitHub 은 잠시
  `mergeable: UNKNOWN` 을 돌려주는데 스크립트가 이를 `CONFLICT` 로 분류해 "origin/main 을 병합하고
  local_ci 를 다시 돌려라"고 안내한다. 이번 사이클에 3회 그 안내를 따라 불필요한 병합·재검증을 했고,
  마지막에는 브랜치가 **0 커밋 뒤처진** 상태에서도 같은 메시지가 나왔다. 조치: `UNKNOWN` 이면 짧게
  재조회하고, 그래도 미확정이면 충돌이 아니라 판정 보류로 보고한다. **불필요한 재작업 · 심각도 중**.
  [해소 확인 2026-08-31] UNKNOWN 은 3회 한도 재조회 후 `MERGEABILITY-UNKNOWN`(exit 4) 판정 보류로 보고하고 충돌 안내를 내지 않는다 — `tests/unit/test_merge_pr_gate.py` 15 passed.


  ↳ 재판정(2026-09-19, D207): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 배포 사이클(2026-08-27) 중 발견한 후속 과제

> 세 건 모두 닫혔다. 원 묶음 헤딩과 불릿 첫 줄은 회계 가드(A9) 대조 키라 그대로 둔다.

릴리스 `6c062e74` 를 배포하며 드러났다. 배포를 실제로 막은 proposal 샌드박스 회귀는 이 사이클에서 고쳤다.

- **스킬 배포 스크립트 5개가 노드 설정에서 호스트를 읽지 않는다** — `calendar` · `coordination` ·
  `mail` · `todo` · `wiki` 는 `${DEPLOY_SSH_HOST:-}` 만 보고, 나머지 11개가 쓰는
  `${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}` 폴백이 없다. `node.toml` 이 설정돼 있어도 exit 3 이라
  변수를 손으로 줘야 한다(2026-08-27 배포에서 mail·todo 두 번 걸렸다). `test_deploy_host_fail_closed_all.py`
  는 "아무 데도 없을 때 자기 이름으로 거부하는가"만 보고 "설정이 있으면 읽는가"는 안 봐서 못 잡는다.
  조치: 다섯 줄을 나머지와 통일하고 그 축을 테스트에 더한다. **배포 마찰뿐 · 안전 문제 아님 · 심각도 낮음**.
 [해소 확인 2026-08-28] 다섯 줄을 정본 패턴으로 통일했다 — `node_config_sh.py --print-env` 를 eval 한 뒤
  `${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}` 를 본다. 「설정이 있으면 읽는가」축은 어느 가드에도 없었으므로
  `tests/unit/test_deploy_host_reads_node_config.py` 로 새로 고정했다(기존 두 가드는 다른 질문을 본다). — `74a250c3`
  ↳ 재판정(2026-09-19, D208): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **todo 워처는 스킬 마운트 뒤에만 갱신해야 하는데 그 순서가 어디에도 없다** — 워처가 live 스킬 경로를
  `sys.path` 에 넣고 신규 `todo_execution_reconcile` 를 import 하므로, 마운트 전에 `deploy.sh` 를 돌리면 매
  틱 즉시 죽는다(2026-08-21 repair 워처 5일 침묵과 같은 모양). 다른 워처는 live 를 subprocess 로만 부른다.
  조치: `deploy.sh` 가 마운트를 선검사해 거부한다 — 산문보다 낫다. **이번엔 순서로 회피 · 심각도 중**.
 [해소 확인 2026-08-28] `deploy.sh` 가 push 이전에 마운트를 선검사하고 없으면 exit 5 로 거부한다. 필요한 모듈은
  `automation/live_mount_preflight.py` 가 워처 소스에서 도출하므로(정적 import + `import_module`/`__import__`)
  import 가 늘어도 등록을 잊을 수 없다. BLOCK·PASS 양쪽 분기를 실측했다. — `1d1b9acc`
  ↳ 재판정(2026-09-19, D209): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **프로덕션 스킬 6개가 `origin/main` 보다 낡다** — live digest 대조로 budget(2커밋) · calendar(2) ·
  coordination(2) · doctype(1) · procurement(1) · report(1). 소유자 ✅ 가 필요해 자동으로 따라오지 않는다.
  조치: 한 번에 묶어 승인받아 정렬한다. **구버전이 정상 동작 중 · 심각도 낮음**.
 [해소 확인 2026-08-27] 같은 배포 사이클에서 소유자 ✅ 로 묶어 정렬했다. 사용자 확인 완료.


  ↳ 재판정(2026-09-19, D210): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 회의록 서식 재설계 중 남긴 것 (2026-08-26)

- **노트 파일명과 Drive 배치는 아직 처리 날짜 기준이다** — 헤더 일시는 추출한 회의 날짜로
  고쳤지만 파일명 `YYYY-MM-DD-meeting-<ref>.md`와 그 이름에서 날짜를 읽는 발행은 처리 시각을
  쓴다(SKILL.md는 "날짜는 회의일"). 조치: 파일명을 `meeting.date` 기준으로 바꾸되 기발행분이
  중복 사본이 되지 않도록 upsert 키를 함께 본다. **배치 정확도 · 심각도 낮음**(내용 영향 없음).
  [해소 확인 2026-08-26] 노트 파일명이 추출된 회의 날짜를 쓰고, 날짜가 없거나 파싱 불가면 처리일로 폴백한다. 같은 ref 재실행이 사본을 만들지 않는 것까지 `tests/unit/test_meeting_skill.py` 가 고정한다.

  ↳ 재판정(2026-09-19, D211): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## cron 실패 통지 확장 중 남은 판단 (2026-08-24)

계획: `.omo/plans/cron-error-remediation.md`. 기능 소개:
[cron 실패 가시성과 주간 따라잡기](기능소개/cron-실패-가시성과-주간-따라잡기.md).

- **스트릭 상태 루트가 쓰기 불가면 사고가 영원히 침묵할 수 있다** — `store()`가 OSError를
  삼키므로 카운터가 임계치 아래에 얼어붙는데, `record()`는 정상 반환해 워처는 "기록됨"으로
  보고 exit 0을 낸다(배너도 없음). 예전에는 exit 1 배너가 우연한 방어선이었다. 조치:
  `watch_failure_streak.record()`가 store 실패를 반환값으로 구분하거나 healthcheck에
  `~/.hermes/watch-failure` 쓰기 가능 프로브 추가를 검토. **발생 조건이 노드 수준 고장(홈
  디렉터리 쓰기 불가)이라 희귀 · 심각도 낮음**.
  [해소 확인 2026-08-26] `record()` 가 store 실패를 반환값으로 구분하고 호출자(notes_organize·research_trends)가 그 신호를 처리한다 — 상태 루트가 쓰기 불가일 때 조용히 얼어붙지 않는다(`tests/unit/test_watch_failure_streak.py`).

  ↳ 재판정(2026-09-19, D212): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## RAG 런타임 배포·탐지 수리 중 발견한 후속 과제 (2026-08-22)

- **런타임 패키지 프로브가 네 패키지를 아직 보지 못한다** — `interop_runtime`·`regression_bank_runtime`·`reminder_poller_runtime`·`research_trends_runtime`은 이번에 실측한 `rag_ingest`·`memory_curator`와 내부 전개 레이아웃이 달라 같은 재귀 대조를 적용하면 오판한다 → 각 배포기의 실제 전개 경로를 확인해 `configs/runtime-package-manifest.txt`에 등록하거나, 등록할 수 없다면 패키지별 사유와 다른 판정 방법을 명시한다. **현재 동작과 무관 · 심각도: 중(같은 조용한 드리프트가 재발할 수 있음)**.
  [해소 확인 2026-08-25] 배포기가 실제로 싣는 파일만 릴리스 쪽에서 골라 비교하도록 매니페스트에 `deployed-python-files` 열을 추가했다(regression_bank·research_trends 등록, interop·reminder_poller 는 사유+대체 판정 명시) — `5fcaf886`

  ↳ 재판정(2026-09-19, D213): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 태그 없는 머지로 프로덕션이 다시 얼었다 (2026-08-21)

「릴리스 태그 규칙」이 생긴 다음 날 같은 정지가 재발했다. PR #203~#210 여덟 건이 태그 없이 main에 들어가 `origin/main` HEAD(`8ce01da0`)가 어떤 서명 태그의 peel 대상도 아니게 됐고, 리컨실러는 2분마다 수렴을 시도하다 exit 4로 실패했다. 이번엔 조용하지 않았다 — 실패 3회에서 소유자에게 드리프트 통지가 갔고, `automation/release-tag.sh`로 v1.0.25를 컷하자 2분 안에 수렴했다(`current -> 8ce01da0`, floor `v1.0.24@9c30748a` → `v1.0.25@8ce01da0`, `incident_open` 해제·`consecutive_failures` 0 확인).

- **설치기가 `/etc/autophagy/node.toml`을 설치하지 않는다** — `automation/install/assets.py`는 계정 홈 사본만 설치하는데, 리컨실러 유닛이 `ProtectHome=tmpfs`라 서비스에게 그 홈은 빈 tmpfs로 보인다. 그래서 신규 설치 노드는 토폴로지를 seed에서 읽게 되고, 그것이 정확히 2026-08-16에 플레이스홀더 호스트명으로 세 가지가 조용히 실패한 원인이었다(`e71e10cc`가 읽기 경로만 고쳐두고 쓰기 경로는 남겨둔 상태) → 설치기가 `root:root 0644`로 그 경로를 설치하게 하고, 계정별 사본은 호환을 위해 당분간 유지한다. **이 노드는 이미 그 파일이 있어 영향 없음 · 심각도: 중(새 설치가 조용히 seed 토폴로지로 돌아간다)**.
  [해소 확인 2026-08-25] 설치 계획이 `/etc/autophagy/node.toml` 을 root:root 0644 로 설치한다(계정 홈 사본 0600 유지) — `3e8d385a`
  ↳ 재판정(2026-09-19, D214): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **롤백 방지 floor와 update-channel 바인딩이 ops 소유라 downgrade 여지가 남는다** — 위 수정으로 임의의 미서명 코드 설치는 막혔지만, 악의의 ops가 `/srv/autophagy-private/deploy-reconcile/release-floor.json`을 지우고 update channel이나 미러 origin을 **과거의 진짜 서명 릴리스**로 되돌리면 이전 서명 버전으로의 downgrade를 유도할 수 있다(취약점이 있는 구버전으로 되돌리는 공격) → floor를 root 소유 디렉터리로 옮기고, 사전 게이트는 read-only 검사만 하며 root 헬퍼가 서명 재검증 뒤 단조 증가시키도록 분리한다. 기존 floor는 초기화하지 말고 정확히 마이그레이션해야 하며, 공개 채널 전환 시의 설치 단위 floor 규칙도 그대로 유지한다. **ops 침해를 전제로 하는 잔여 위험 · 심각도: 중**.
  [해소 확인 2026-08-25] floor 를 root 소유 `/var/lib/autophagy/update-trust/` 로 옮기고 ops 사전게이트는 읽기 전용, root 헬퍼만 단조 전진하도록 갈랐다. 기존 floor 는 삭제 없이 정확 복사 이관 — `6d17cb1d`

증적: 노드 `<primary-node>` — 리컨실러 저널 00:29~00:45 UTC, `release-floor.json`·`state.json` 전후 대조, `readlink /srv/autophagy-agent-current` = `8ce01da0`.

  ↳ 재판정(2026-09-19, D215): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 배포 스크립트 자체를 신뢰할 수 없었다 (2026-08-18)

죽은 워처 5개를 복구하다가, 그것들을 실어 나르는 **배포 계층 자체의 결함**이 드러났다. 이번 사건에서 가장 비싼 교훈은 워처 코드가 아니라 **배포가 실패해도 조용했다**는 점이다.

- **같은 `parents[3]` 결함이 승인 어댑터 4개에 더 있다** — `skills/calendar/scripts/calendar_approval.py` · `skills/budget/scripts/budget_gate.py` · `skills/wiki/scripts/wiki_approval.py` · `skills/patent-prep/scripts/patent_export_binding.py`. 이번에 binding 3건만 고쳤다. 같은 부류가 아닌 것도 구분해 둔다 — `calendar_cli.py`·`prompt_cli.py`·`topics_cli.py`는 checkout 상대 앱 import, `topics_registry.py`는 config 탐색, `todo_preflight.py`는 존재 검사형 probe라 무관하다. 조치: 같은 probe 형태를 적용하고 mounted-release 파라미터화에 추가한다. **잠복(해당 승인 경로가 돌 때 드러난다) · 심각도: 중**.
  [해소 확인 2026-08-25] 네 어댑터 모두 `parents[2:6]` + `/srv/autophagy-agent-current` 후보 탐색으로 이미 교체돼 있다(calendar_approval.py:47 · budget_gate.py:70 · wiki_approval.py:39 · patent_export_binding.py:35)
  ↳ 재판정(2026-09-19, D216): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **fail-closed 호스트 가드가 deploy.sh 둘에만 있다** — `DEPLOY_SSH_HOST` 미지정 시 리터럴 플레이스홀더로 ssh 를 시도해 DNS 오류가 진짜 원인을 가리는 문제를 mail·wiki 두 스크립트에서만 닫았다(이번 불릿의 범위가 mail 이었다). calendar·coordination·todo 와 `automation/**/deploy.sh` 는 그대로다(budget 은 2026-08-24 `NODE_DEPLOY_SSH_HOST` 폴백 통일로 해소) → 같은 가드를 전 배포 스크립트로 넓히고 `tests/unit/test_deploy_host_fail_closed.py` 의 목록을 전수로 바꾼다. **안전 문제 아님 · 심각도: 낮음(배포 절차 마찰)**.

증적: PR #161·#162·#163, cron 틱 `calendar/coordination 21:28:58 ok`, 워처 직접 실행 `notes/research rc=0`.
  [해소 확인 2026-08-26] `DEPLOY-BLOCK` 가드를 배포 스크립트 17종으로 넓혔고 리터럴 플레이스홀더는 0건이다(`automation/rag_stack/deploy.sh` 1종은 계약 고정으로 제외 — `follow-ups.md` 에 열린 채로 남겼다). `tests/unit/test_deploy_host_fail_closed.py` 의 목록은 손으로 적지 않고 `**/deploy.sh` 를 훑어 만들므로 새 스크립트가 자동으로 대상이 된다.

  ↳ 재판정(2026-09-19, D217): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

- **`hermes-update`가 소스를 교체하고도 게이트웨이를 재시동하지 않는다** — 벤더 자체 업데이트 경로에는 우리 릴리스 리컨실러가 갖고 있는 `autophagy-gateway-pair restart`에 해당하는 단계가 없어, 업데이트 시점부터 다음 재시동까지 프로세스가 **옛 코드 + 새 파일**의 불일치 상태로 돈다. 이번엔 그 상태가 20시간 잠복하다 첫 도구 호출에서 드러났다 → 벤더 업데이트 뒤 게이트웨이 쌍 재시동을 자동화하거나(래퍼), 최소한 "게이트웨이 기동 시각 < `~/.hermes/hermes-agent` 소스 mtime"을 보는 프로브를 넣어 잠복을 깬다. **재발 확실 · 심각도: 높음(도구 계층 전체 정지)**.
  [해소 확인 2026-08-25] 기동 시각 < 벤더 소스 mtime 을 판정하는 탐지 전용 프로브 `automation/hermes_compat/gateway_runtime_probe.py` 신설(재시동은 여전히 소유자 몫) — `69c4293b`
  ↳ 재판정(2026-09-19, D218): VERIFIED-SNAPSHOT — 두 실제 게이트웨이 기동 시각이 각 소스의 최신 mtime보다 늦어 기존 프로브가 모두 FRESH였다. 수동 실측이며 자동 감시 배선이나 미래 업데이트 안전성은 증명하지 않는다.
- **liveness 관점에서는 장애가 아니었다 — 그래서 아무도 못 봤다** — 유닛은 내내 `active/running`이었고 Discord도 연결돼 있어 텍스트 응답은 정상으로 나갔다. 즉 `systemctl is-active`나 Discord 연결성에 기대는 프로브는 이 장애를 원리적으로 통과시킨다. 소유자는 자기 요청이 두 번 실패한 뒤에야 알았다 → 위 mtime 프로브와 함께 `agent.conversation_loop`의 `Outer loop error` 연속 발생을 사건으로 승격하는 기준을 정한다. **탐지 공백 · 심각도: 중(장애 지속 시간이 소유자의 다음 요청 시점에 좌우된다)**.
  [해소 확인 2026-08-25] 같은 프로브가 liveness·연결성과 무관한 신선도 기준으로 판정한다 — `69c4293b`
  ↳ 재판정(2026-09-19, D219): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **벤더 버전이 계정별로 갈라진다 — 오늘 맞췄지만 재발을 막는 것은 없다** — 발견 시점에 agent는 v0.20.1, peer는 **v0.18.2**(07-14자)로 문제의 심볼이 아예 없었다 — 자기 코드끼리 일관돼 정상 동작했기 때문에 갈라짐 자체가 아무 신호를 내지 않았다. 「게이트웨이 재시동 규칙」은 두 계정을 항상 쌍으로 다루는데 벤더 업데이트는 계정별로 따로 일어나 그 쌍 가정이 깨져 있었다. 2026-08-18 소유자 지시로 ① peer를 agent 커밋 `7095e23eb`(v0.20.1)으로 ff해 같게 만든 뒤 ② 둘 다 upstream 최신 `a3995f8`(**v0.20.3**, 2026.8.16.2)로 `hermes update`했다 — 두 계정이 같은 커밋·같은 버전 문자열임을 대조하고(업스트림은 두 업데이트 사이에 움직일 수 있으므로 사후 대조가 필수다), config 포맷은 양쪽 모두 v33→v37로 마이그레이션됐다. 롤백 지점은 양쪽 `backup/pre-latest-20260818T012445Z`. 다만 **갈라짐을 막거나 보이게 하는 장치는 여전히 없다** — 다음 `hermes update`가 한 계정에서만 돌면 그날로 다시 갈라진다 → 두 계정의 벤더 버전을 함께 올리는 절차를 정하거나, 버전 대조를 헬스체크에 드러낸다. **현재 일치 · 심각도: 중(재발 시 한쪽만 회귀해 원인 규명이 어려워진다)**.
  [해소 확인 2026-08-25] 같은 프로브가 agent·peer 소스 지문 갈라짐을 기계 판정한다 — `69c4293b`
  ↳ 재판정(2026-09-19, D220): PARTIAL — 두 계정의 벤더 HEAD는 같고 추적 Python 바이트 지문은 다르다. agent 전용 패치가 차이 일부를 설명하지만 모든 차이가 의도됐다고 추정하지 않는다. root 프로브 권한을 넓히지 않고 계정별 읽기로 비교했다.
- **무패치 구동 탐지가 healthcheck 에 배선되지 않았다** — 이제 `python3 -m automation.hermes_compat.patch_state` 로 마커 상태를 기계 판정할 수 있고(기능 소개: [hermes-compat 무패치 구동 탐지](기능소개/hermes-compat-무패치-구동-탐지.md)), 매니페스트 notes 가 없는 검사를 있다고 말하던 오류도 제거했다. 다만 아직 사람이 돌려야 돌다 → healthcheck 는 ops 계정으로 도는데 대상은 `~agent/.hermes/hermes-agent` 라 권한 설계가 먼저 필요하다(같은 이유로 mailon 런타임 프로브도 배선이 미완이다). **탐지 공백 · 심각도: 중(지금은 사람이 돌려야 보인다)**.
  [해소 확인 2026-08-25] 마커·신선도·갈라짐 모두 기계 판정이 가능해졌다. **잔여**: ops→agent 권한 설계가 필요한 healthcheck 배선은 OWNER 항목으로 남는다 — `69c4293b`
  ↳ 처리(2026-10-01): 호환 패치 캐리어가 `automation/hermes_compat/deploy-manifest.txt` 에 `v2:tree;policy=held` 로 선언돼, 릴리스와 다르면 영수증 `held` 와 적용 통지의 「남은 소유자 조치」에 사유(`vendor-patch-carrier-is-owner-deployed`)와 함께 드러난다 — `57caf932`, `66e16e13`. 패치 상태(`patch_state`)의 healthcheck 배선은 그대로 OWNER 로 남는다.

  ↳ 재판정(2026-09-19, D221): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 스윕 3차·개인 서버 대화 채널 후속 과제 (2026-08-17)

완료 기능은 [개인 서버 대화 채널](기능소개/개인서버-대화-채널.md)과 [기관메일 발신자·전체 폴더·검색](기능소개/기관메일-발신자-전체폴더-검색.md), 완료 수리는 [peer trust-root 진단 분리](patch/2026-08-17-skill-gate-peer-trust-root-diagnostic.md)다.

- **RAG healthcheck의 일시 실패 원인을 귀속할 당시 관측치가 없다 (`t_029a7e08`)** — 같은 tick에서 embedding·Qdrant가 실패하고 5분 뒤 회복했지만 probe별 rc·latency·SSH/HTTP 구분과 당시 서비스 로그가 보존되지 않아 transport·서비스·자원 중 하나를 고를 수 없다 → 비공개 런타임 증적에 probe별 원인·시간을 먼저 남긴 뒤 재현된 원인에만 retry·timeout·서비스 임계값을 적용한다. **현재 서비스 정상·추측성 수정 금지 · 심각도: 중(재발 원인 미확정)**.
  [해소 확인 2026-08-25] 프로브별 rc·소요 ms·transport/service 경계를 비밀 없이 기록하는 `automation/healthcheck_probe_evidence.sh` 추가(기록 실패가 판정·종료코드를 바꾸지 않음) — `0925c9f2`
  ↳ 재판정(2026-09-19, D222): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **다섯 cron 래퍼가 폐기된 스킬 경로를 검사해 거짓 장애를 낸다** — budget·report·coordination·calendar·research-trends가 레거시 사용자 홈 경로를 하드코딩해 `not mounted` 또는 import 오류를 내지만 governed live 심링크는 정상이다 → mount 판정을 `automation/skill_mount_drift.py`와 같은 `/srv/autophagy-skills/live/<skill>` 정의로 통일한다. **보안 문제·실제 마운트 손상 아님 · 심각도: 중(주기 작업 실패·오진)**.
  [해소 확인 2026-08-25] 다섯 래퍼가 `automation/skill_mount.py` 단일 정의로 판정하고, 해결 불가 시 미마운트로 fail-closed 한다 — `83ce629c`

  ↳ 재판정(2026-09-19, D223): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 에이전트 자가 스킬 공존(SS-1) 작업 중 발견한 후속 과제 (2026-08-15)

자가 스킬 루트 반전과 감사 원장을 만들며 발견했다. 기능은 [소개](기능소개/에이전트-자가-스킬.md).

- **`selfskill_audit/ledger.py`가 249 순수 LOC로 천장(250) 코앞이다** — PR #113에서 이미 `store.py`(신뢰 경계 JSON I/O)로 한 번 쪼갰는데, PR #116의 `removed` 델타 추가로 다시 한 줄 차이까지 왔다. 지금은 규약 위반이 아니지만 **다음 변경이 무엇이든 천장을 넘긴다** → 다음에 이 파일을 열 때 줄을 더하지 말고 분할한다(후보: 스냅샷 수집 `_scan`/`_snapshot` 계열을 `scan.py`로, `_diff`+`Action`을 `delta.py`로). **동작 결함 아님 · 심각도 낮음(다음 작업자가 천장에 부딪혀서야 알게 되는 것이 비용)**.

증적: `docs/qa/SS-1/reference-inventory.md`(참조 154건 분류 · 위 12개 코드 행의 원문 판정 포함).
  [해소 확인 2026-08-26] `scan.py`(스냅샷 수집)·`delta.py`(델타 판정)로 분할해 천장 아래로 내렸다. 공개 이름은 `ledger.py` 에서 그대로 import 되므로 호출자는 변경 없음.

  ↳ 재판정(2026-09-19, D224): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 그룹 발행 공지(W-F3-C) 작업 중 발견한 후속 과제 (2026-08-15)

F4 해소 기록(2026-08-21): 복구 서브커맨드는 구현됐다. 남은 owner action은 라이브 메시지가
`delivered`인지 `not-delivered`인지 판정하는 것뿐이며 OWNER-37이 그 범위만 소유한다.

- **공지 전송이 애매하게 실패하면 그 릴리스의 공지가 사람 손 없이는 영영 막힌다** — `announce_ledger`는
  `PostingJournal` 예약을 일부러 남겨 다음 실행을 `POSTING_JOURNAL_STALE`로 거부한다(실패한
  send가 실제로는 도착했을 수 있어 재시도가 이중 게시가 되기 때문 — 의도된 fail-closed).
  그런데 예약을 감사와 함께 해제하는 공용 탈출구(`approval_lease.abandon`)을 announce 쪽에서
  부를 CLI verb가 없다 → 운영자가 `~/.hermes/managed-skills/announce/*.posting.json`을 손으로
  지우는 대신 그 탈출구를 부르는 서브커맨드를 추가한다. **보안·발행 결함 아님 — 공지는
  알림이고 발행은 그대로 성공한다 · 심각도 낮음(운영 절차 공백)**.
  [해소 확인 2026-08-26] 공지 예약을 감사와 함께 해제하는 서브커맨드를 `announcement_recovery.py` 에 추가했다 — 없는 예약은 fail-closed 로 거부하고, 공지 재전송은 하지 않는다(`tests/unit/test_managed_announcement_recovery.py`).

  ↳ 재판정(2026-09-19, D225): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 자동 배포가 한 번 실패하면 그 스킬만 조용히 빠진다 (2026-08-04 실측 · 해소)

소유자 ✅ → 자동 마운트 계약은 실제로 동작한다(coordination·wiki가 `done (owner-approved)`로 완주). 그러나 같은 날 나머지 3건은 승인이 있었는데도 마운트되지 않았다. **두 결함 모두 PR #55로 해소**했고(`4be42d0`·`070eafd`, 릴리스 `5cad5e47`로 수렴 확인), 남은 것은 아래 잔여 두 줄이다. 증적 `docs/qa/SCW-1/summary.txt`.

- **잔여 — 소유자에게 밀어주는 통지는 여전히 없다** — 위 두 수정은 정지를 자가치유시키고(릴리스 교체 시) 기다림을 보이게 하지만, 둘 다 노드 로컬 journal에만 남는다. 즉 같은 릴리스에서 지속적으로 실패하는 경우의 통지는 「수리 티켓 경로」 묶음의 PATH 결함에 그대로 종속된다. **심각도 낮음으로 하향**(종전 “중”) — 사고의 실제 침묵 모드는 사라졌고 남은 것은 push 통지뿐이다. 조치: 그 묶음의 PATH 결함을 고치면 이 부류가 자동으로 티켓화된다. **`tick.json`을 읽는 healthcheck 프로브는 검토 후 채택하지 않았다** — healthcheck는 ops crontab에서 돌고 로컬 프로브에는 ssh·sudo가 없으며(rc=126), `tick.json`은 agent 소유 0600이라 소유자 프로비저닝(allowlist sha256 또는 sudoers)이 필요해 “최소 코드 수정”을 넘고, 이미 그 묶음이 담당하는 통지 경로와 병렬 구조가 된다(「병렬 confirm 구조 신설 금지」).
  [해소 확인 2026-08-24] 종속 대상이던 「수리 티켓 경로」 PATH·allowlist 결함이 2026-08-20 허용목록 생성으로 해소돼 이 부류의 티켓화가 동작한다(2026-08-22 occurrence 적재 실측).

  ↳ 재판정(2026-09-19, D226): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 스킬 배포 파이프라인이 3겹으로 막혀 있었다 (2026-08-04 실측 · 전부 해소)

FS3 K2-A는 기존 `release_helper_probe.sh`의 실설치 자산 검사를 랜딩 출력에도 재사용했다.
금지된 `deploy-skill.sh` 실행 없이 저장소 회귀와 읽기 전용 프로브 계약만 검증했다.

위 「마운트된 스킬 5종…」의 **근본 원인**이다. 재배포를 실제로 시도해서야 드러났다 — 마운트가 낡은 것이 아니라 **마운트할 수가 없었다**. 하나를 풀자 다음 것이 드러나길 세 번 반복했고, 세 개 모두 **이번 스윕(2026-08-03)이 직접 만들었거나 드러낸** 것이다. 실제 재배포 없이는 세 겹 중 어느 것도 보이지 않았다.

- **설치본 특권 헬퍼의 드리프트를 감지하는 것이 없다** — 이전에도 같은 부류가 기록됐으나(`PYTHONDONTWRITEBYTECODE=1` 누락, 「조치 불요」로 종결) 이번엔 **인자 불일치로 배포 자체가 죽었다** — “무해한 드리프트”라는 앞서의 판단이 일반화될 수 없음을 보인다. **심각도 중**. 조치: 헬퍼 해시를 릴리스 원본과 대조하는 healthcheck 프로브를 두거나, 릴리스 수렴 직후 provisioner를 멱등 실행해 항상 같은 버전이 되게 한다.
  [해소 확인 2026-08-25] `automation/release_helper_probe.sh` + healthcheck `LOCAL_PROBES` 의 `release_helper_drift` + `tests/unit/test_healthcheck_helper_drift.py` 로 이미 감지된다

  ↳ 재판정(2026-09-19, D227): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 마운트된 스킬 5종이 릴리스보다 낡았다 — 머지된 수정이 미발효 (2026-08-04 실측)

OWNER 체크리스트 9번·F4 판정 중 발견. `skill_mount_drift.py`를 라이브에 직접 돌렸다.

- **이 드리프트를 상시 감시하는 경로가 사실상 무력화돼 있다** — `healthcheck.sh`의 `skill_mounts_current` 프로브가 이것을 보고 있지만, 실패해도 수리 티켓은 위 「수리 티켓 경로」 묶음의 PATH 결함으로 생성되지 않고, 알림도 노드 로컬 로그에만 남는다. **심각도 중(관측성)** — 두 결함이 겹쳐 “드리프트가 나도 아무도 모른다”가 된다. 조치: PATH 결함을 먼저 고치면 이 부류의 발견이 자동화된다(그 묶음에 종속).
  [해소 확인 2026-08-24] 동일 근거 — PATH·allowlist 결함 해소로 `skill_mounts_current` FAIL의 티켓화가 동작한다.

  ↳ 재판정(2026-09-19, D228): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 티켓 경로 — allowlist 뒤에 PATH 결함이 숨어 있었다 (2026-08-04)

OWNER 체크리스트 2번(`report_repair` allowlist 등록) 처리 중 발견. allowlist는 등록도론 rc=126은 해소됐고([소개](기능소개/배포-체크아웃-지연-감지.md) 「잔여」 항목), 그 뒤에 가려 있던 다음 결함이 드러났다.

- **allowlist를 통과해도 티켓은 여전히 안 생긴다 — `hermes`가 PATH에 없다** — 실측: 게이트 통과 후 `repair error: [Errno 2] No such file or directory: 'hermes'`(rc=1). 원인은 `healthcheck.sh:133`의 `sudo -n -u agent -H`가 로그인 셸을 거치지 않아 sudo secure_path만 받기 때문이다 — `hermes`는 `/home/agent/.local/bin/hermes`에 있고 그 디렉터리는 secure_path에 없다(agent 로그인 셸 PATH에는 있음). 즉 **자동 수리 티켓은 여전히 0건**이며, 드리프트를 *감지*해도 *티켓*은 안 된다는 상태는 그대로다(증상만 rc=126→rc=1로 바뀌었다). **동작·보안 무관 · 심각도 중**(관측성). 조치: `healthcheck.sh:133`의 명령을 `sudo -n -u agent -H env PATH=/home/agent/.local/bin:<secure_path> python3 -I ...`로 바꾸되, **명령이 바뀌면 해시 13개가 전부 무효해지므로** 신·구 해시를 함께 등록해 반영 사이의 회귀 창을 없앱 뒤 구 해시를 제거한다(미러 ff-pull은 리컨실러가 2분 주기로 따라오므로 창이 실재한다).
  [해소 확인 2026-08-24] 허용목록 래퍼를 릴리스에서 생성·재설치하는 방식([헬스체크 허용목록 생성](기능소개/헬스체크-허용목록-생성.md), 2026-08-20)으로 대체돼 수동 해시 등록 자체가 불필요해졌고, rc=126이 0이 되어 티켓 경로가 소생했다(2026-08-22 kanban 정리에서 티켓 occurrence 적재 실측).

  ↳ 재판정(2026-09-19, D229): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 배포 미러 임시 워크트리 누수 — trap 정리 실패 (2026-08-04 실측)

OWNER 체크리스트 1번(`.git` 권한 회수) 조사 중 발견. 권한 자체는 해소됐다(2775→2755, 그룹쓰기 1629개→0, agent 소유 356개→0).

- **`origin_snapshot.sh`의 스냅샷 워크트리가 trap 정리를 빠져나간다** — DG-2가 배포마다 만드는 `/tmp/autophagy-snapshot.*/tree`가 4개 남아 미러 `.git/worktrees`에 등록된 채 56MB를 점유하고 있었다(전부 dirty 0 · 미착지 0 · origin/main 조상으로 확인 후 정리함). **동작·보안 무관 · 심각도 낮음** — 용량과 등록 목록 오염뿐이다. 조치: `origin_snapshot.sh`의 trap이 어떤 경로에서 안 도는지(조기 exit·신호·SSH 끊김) 확인해 정리를 보장하거나, 배포 진입 시 오래된 스냅샷을 먼저 prune한다.
  [해소 확인 2026-08-26] 누수 경로의 정리를 보장하고 진입 시 오래된 스냅샷을 prune 한다 — dirty 하거나 미착지 커밋이 있는 워크트리는 남긴다(`tests/unit/test_origin_snapshot_cleanup.py`).

  ↳ 재판정(2026-09-19, D230): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 에이전트가 배포 미러의 git 내부를 쓸 수 있다 (2026-08-03 실측)

cha가 보고한 "Hermes가 테스트용 코드를 만들고 커밋하면 워크플로우 전체가 망가진다"의 물리적 원인이다.

- **`/srv/autophagy-agents/.git`과 `.git/refs`가 `2775`(그룹 쓰기 가능)이고 `agent`가 `autophagy` 그룹이다** — 워킹트리 쪽도 `AGENTS.md`·`automation/`·`prompts/`가 그룹 쓰기 가능하다. 실측: 미러에 **agent 소유 파일 1108개**(`.git/objects` 295 + `tests/unit` 126 + `skills/mail` 72 + `automation/memory_curator` 64 …). 즉 커밋 거부 훅은 `git commit`만 막고, `--no-verify`나 ref 직접 조작은 파일시스템 권한이 그대로 허용한다. **코드 주입 경로는 아니다** — 배포 소스는 root 소유 읽기 전용 릴리스이고 그 트리는 `origin/main`과 바이트 일치해야 하므로(DONE「봉인된 릴리스의 배포 provenance」) 에이전트가 쓴 코드는 배포될 수 없다. 실제 피해는 **DoS·감사 오염**이다: 미러의 `origin/main` ref가 어긋나면 ⑤ 재조정이 잘못 수렴하려 하거나 provenance 검증이 모든 배포를 막는다(이번 진단 중 실수로 재현했고 ops 인증 fetch로 즉시 복구). **심각도 중**. 조치: `.git` 그룹 쓰기 비트를 회수한다 — 단, `/tmp`의 스냅샷·수리 worktree 메타데이터가 그 권한을 쓰고 있어 단순 `chmod`가 아니다. 무엇이 정당하게 쓰는지 먼저 확정하고 ops 경유로 재배치한 뒤 회수한다.
  [해소 확인 2026-08-24] 그룹 쓰기 비트 회수 완료 — 2775→2755, 그룹쓰기 1629개→0, agent 소유 356개→0(「배포 미러 임시 워크트리 누수」 묶음의 조사 기록).

  ↳ 재판정(2026-09-19, D231): OWNER — 검사한 .git·.git/refs는 2755이며 두 에이전트 쓰기가 거부됐다. 같은 원문 항목의 워킹트리 우려는 automation/ 쓰기 허용으로 남으므로 D027과 함께 소유자 조치로 유지한다.
## 배포 체크아웃 드리프트 가드(DG-1) 작업 중 발견한 후속 과제

기능은 DONE「배포 체크아웃 드리프트 가드 (DG-1)」 참조, 증적 `docs/qa/DG-1/summary.txt`

- **수리 티켓 경로가 여전히 allowlist에 거부된다** — `report_repair`의 SSH 명령(`repair_cli.py detect`)도 sha256 allowlist에 없어 `REPAIR_TICKET_FAILED rc=126`가 난다(추정 아닌 실측). 즉 A가 드리프트를 *감지*해도 자동 *티켓*은 안 된다(FAIL 로그로만 드러남). **이번 범위 밖**: 노드 `~<operator-account>/.local/libexec/autophagy-healthcheck-probe`의 allowlist 갱신은 소유자 작업이다. **심각도 중** — A + B로 드리프트 빈도가 분단으로 줄어 방치해도 큰 사고로 번지지는 않는다. 조치: cha가 allowlist에 checkout probe와 report_repair 명령 해시를 등록(또는 checkout probe는 이제 로컬이라 불필요 — report_repair만 남음). **2026-08-17 재확인**: 여전히 살아 있다 — cron 틱 10:45·10:50·10:55·11:00·11:05 전부 `REPAIR_TICKET_FAILED rc=126` ×2(수동 실행만의 인공물이 아님을 cron 로그로 확정). 지금은 실제 FAIL 2건(HELPER-DRIFT·SKILL-STALE)이 티켓화되지 못하고 있다.
  [해소 확인 2026-08-24] 허용목록 래퍼를 릴리스에서 생성·재설치하는 방식([헬스체크 허용목록 생성](기능소개/헬스체크-허용목록-생성.md), 2026-08-20)으로 대체돼 수동 해시 등록 자체가 불필요해졌고, rc=126이 0이 되어 티켓 경로가 소생했다(2026-08-22 kanban 정리에서 티켓 occurrence 적재 실측).

  ↳ 재판정(2026-09-19, D232): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## G8 — LOC 등록부

기능은 [소개](기능소개/loc-등록부-재측정.md), 작업 배분은 `.omo/plans/parallel-followup-sweep.md` §5 G8이다. 8개 코드 그룹이 전부 머지된 HEAD(`2383a92`)에서 전수 재측정해 **초과 29건 · 등록 3건**이던 상태를 등록 29건으로 맞췄고, LOC 게이트는 `EXCEPTION 29 / VIOLATION 0 · LOC RESULT: PASS`가 됐다.

- **F2 감사는 LOC를 고쳐도 여전히 red다 — `f2_quality.sh:41`이 `ruff check .`를 그대로 돌린다** — CI는 `--exclude skills/mail/vendor`를 주는데(`.github/workflows/ci.yml`, "고칠 수 없는 트리를 린트하면 CI가 영구히 빨간불") F2만 안 준다. 실측: F2의 ruff 지적은 **전량 vendor 트리**이고 CI 기준으로는 `All checks passed!`다. **보안·동작 무관 · 심각도 중(감사 신호 손실 — LOC에서 방금 없앤 것과 같은 종류의 상시 red)** → `f2_quality.sh:41`에 같은 exclude를 주면 F2 전체가 green이 된다. 이번 범위(등록부)를 벗어나 손대지 않았다.
  [해소 확인 2026-08-24] `f2_quality.sh:43`이 이미 `--exclude skills/mail/vendor`를 적용하고 있다(2026-08-24 실측).

증적: `docs/qa/F2/module-loc.txt` (`EXCEPTION 29 / VIOLATION 0`), 전체 스위트 3111 passed, `ruff check . --exclude skills/mail/vendor` 통과.

  ↳ 재판정(2026-09-19, D233): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## H1 — 헬스체크 릴리스 관측성 OWNER 인계 (2026-08-05)

기능은 [소개](기능소개/헬스체크-릴리스-관측성.md), 증적은 `.omo/evidence/fs2/task-1-parallel-followup-sweep-2.txt`다.

- **노드 wrapper의 실제 알고리즘 재검증과 15개 명령 해시 등록은 아직 OWNER 몫이다** — 커밋 manifest는 원문 생성·shim 대조의 기준이며 실제 등록을 대신하지 않는다. **자동 수리 티켓 관측성 영향 · 심각도 중, 보안 하향 없음** → OWNER가 배포 노드의 agent UID·sudo secure_path를 명시 입력해 manifest를 재생성하고 wrapper 알고리즘으로 검증·등록한다.
  [해소 확인 2026-08-24] 허용목록 래퍼를 릴리스에서 생성·재설치하는 방식([헬스체크 허용목록 생성](기능소개/헬스체크-허용목록-생성.md), 2026-08-20)으로 대체돼 수동 해시 등록 자체가 불필요해졌고, rc=126이 0이 되어 티켓 경로가 소생했다(2026-08-22 kanban 정리에서 티켓 occurrence 적재 실측).

  ↳ 재판정(2026-09-19, D234): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 구독자 sync 자동화 배포(W-F3-B) 작업 중 발견한 후속 과제

틱을 배포물로 만들며 발견했다. 기능은 [소개](기능소개/구독자-sync-자동화-배포.md).

- **`automation/install/assets.py`가 250 pure-LOC 천장에 4줄 남았다** — opt-in 레지스트리를 `components.py`로 분리해 246으로 내려놓았으나, 다음 always-on 유닛·helper 추가가 F2 게이트를 깨뜨린다. **현재 동작 영향 없음·심각도 낮음** → 다음에 `assets.py`를 만지는 wave가 파일 종류별(systemd·sudoers·libexec·hooks) 빌더로 쪼개거나 사유와 함께 등록부에 올린다.
  [해소 확인 2026-08-24] 2026-08-24 실측 213 pure LOC — `components.py` 분리 이후 여유가 복원돼 전제가 소멸했다.

증적: `docs/qa/W-F3-B/summary.txt`(15/15 + 증명된 것/불가한 것 분리).

  ↳ 재판정(2026-09-19, D235): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

- **`automation/healthcheck.sh`가 249/250 pure LOC인데 LOC 예외에 등록되어 있지 않다** — probe 함수 하나(약 15줄)만 더해도 `f2_quality.sh`의 LOC 루프가 `VIOLATION`을 내므로, 다음에 헬스체크 항목을 추가하려는 사람은 자기 작업과 무관한 분할부터 해야 한다. 실제로 이번 스윕에서 quarantine 미확인 릴리스 probe를 넣으려다 여기서 막혔다. **현재 동작·보안 영향 없음 · 심각도 낮음(다음 작업자가 천장에 부딪혀서야 알게 되는 것이 비용)** → probe 함수군을 `healthcheck_probes.sh`로 떼어 source하거나, 분할이 부적절하다면 사유와 함께 `automation/final/f2_loc_exceptions.txt`에 등록한다.
  [해소 확인 2026-08-26] probe 함수군을 `automation/healthcheck_probes.sh` 로 떼어 source 한다 — 다음 probe 를 넣으려는 사람이 자기 작업과 무관한 분할부터 하지 않아도 된다.

  ↳ 재판정(2026-09-19, D236): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## F4 스코프 감사의 `approvals-send-log` 가 라이브 노드에서 FAIL (2026-08-21 발견)

병렬 후속 스윕 3 마감 중, `k-fix4` 워크트리에 커밋되지 않은 `automation/final/f4_scope.sh`
재실행 결과가 남아 있었다(라이브 노드 대상, `F4 RESULT: FAIL`).

- 소유자 승인 대비 전송 로그가 맞지 않는다 — `owner_approved_records=207`,
  [해소 확인 2026-08-25] 감사를 `automation/final/approvals_send_log_audit.py` 로 추출해 어긋난 행마다 `approval-missing`·`send-log-row-missing`·`method-not-matched` 사유를 붙인다. 원인 하나 확인: 매칭 규칙(2026-07-18 `35d104a7`)이 `mail.compose_send`(2026-07-19 `02c133fa`)보다 앞서 쓰여 정상 compose 발송이 계속 어긋난 것으로 세어졌다. **잔여**: 노드 1건 대조는 OWNER — `f0d1f796`
  `send_logged_records=25`, `sent_records=55`, **`unmatched_sends=31`**(전부
  `method='manual_reaction'`, 직전 증적 2026-07-18 에서는 0). 노드에서 31건 중 1건을 골라
  승인 레코드와 전송 로그를 직접 대조해 **(a) 검사기가 `manual_reaction` 을 매칭하지 못하는 것**과
  **(b) 실제 전송이 로깅되지 않는 것**을 가른 뒤, (a)면 매칭 규칙에 추가하고 (b)면 로깅 누락을 수리한다.
  **심각도: 중**(가른 뒤 (b)면 높음 — 승인-전송 감사 추적의 구멍이라는 뜻). 영향 범위는 승인 게이트의
  사후 감사이며 승인 자체나 외부효과 차단에는 영향이 없다.

이번 스윕 범위 밖인 이유: 이 스윕은 후속 과제 원장의 정산 회계를 다루고 이 검사는 라이브 노드의
운영 상태를 본다. 재실행 결과는 이전 웨이브(2026-07-18)의 `docs/qa/F4/` 증적을 덮어쓰므로
커밋하지 않고 복원했다 — 위 수치가 그 내용이다.

  ↳ 재판정(2026-09-20, D237): VERIFIED-SNAPSHOT — 기존 읽기 전용 감사가 승인 256·전송 감사 90·sent 89·주입 테스트 1·미대조 0으로 종료했다. 현재 발견 가능한 입력 범위이며 과거 F4 증적을 덮어쓰거나 외부 메일함 도달까지 주장하지 않는다.
## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

보드의 열린 카드 8건을 정리하며 healthcheck 허용목록 래퍼(`<primary-node>`·`<rag-node>`)를 재생성하고
runtime-package 프로브의 `cron/` 오탐을 고쳤다. 그 과정에서 드러난 구조적 틈이다.

- **허용목록 래퍼의 드리프트 지문이 프로브 명령 본문을 덮지 않는다** — `wrapper_inputs_digest`는
  [해소 확인 2026-08-25] `wrapper_inputs_digest` 가 기록된 프로브 명령 본문의 해시 집합을 지문 입력에 포함한다 — `523371d6`
  `LIVE_CHECKS`와 매니페스트 2종만 해시하므로, 프로브가 원격 명령 문자열만 바꾸면 지문은 같은데
  명령은 exit 126으로 거부돼 UNKNOWN이 조용히 난다(그래서 이번 runtime-package 수정은 릴리스 쪽
  필터만 바꿨다) → 지문에 기록된 명령 해시 목록을 함께 넣는다. **탐지 누락 위험 · 심각도 중**.
  ↳ 재판정(2026-09-19, D238): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **mail `scenario.sh`의 파사드 probe도 호출자 cwd에 민감하다** — `skills/mail/scripts/scenario.sh`가
  topics와 같은 `PYTHONPATH=… python3 -c 'import automation.…'` 형태로 분기를 고르므로, 샌드박스가
  릴리스 루트를 cwd로 물려주면 probe는 import에 성공하고 격리 CLI는 다른 답을 낼 수 있다(topics는
  이 불일치로 ✅ 뒤에도 매 틱 `SCENARIO-FAIL evidence list`였다 — PR #242로 수정). mail은 오늘
  샌드박스를 통과했지만 같은 모양이다 → `python3 -I` + 명시 `sys.path`로 고치고 cwd=checkout 회귀를
  둔다. **잠재 플레이크 · 심각도 낮음(현재 통과)**.
  [해소 확인 2026-08-26] 파사드 probe 를 `python3 -I` + 명시 sys.path 로 고쳐 호출자 cwd 가 답을 바꾸지 못한다(`tests/unit/test_mail_scenario.py` 가 두 cwd 에서 같은 답을 요구한다).
  ↳ 재판정(2026-09-19, D239): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **수동 healthcheck 실행도 수리 occurrence를 낸다** — `read_only=true`를 찍으면서도 REPAIR_TICKET
  경로는 항상 활성이라 진단용 실행이 카드에 occurrence를 더한다 → 수동 진단용 opt-out(예:
  `HEALTHCHECK_NO_REPAIR=1`)을 둔다. **노이즈만 · 심각도 낮음**.
  [해소 확인 2026-08-26] `HEALTHCHECK_NO_REPAIR=1` 이 수리 티켓 경로만 무력화한다 — 프로브 판정과 종료 코드는 그대로다(`tests/unit/test_healthcheck_checkout_ticket.py`).

  ↳ 재판정(2026-09-19, D240): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 지식 계층 4단계(2026-08-22) 중 발견한 후속 과제

위키 스키마 v2와 Obsidian→위키 큐레이션을 착지시키며 남은 것들이다([소개](기능소개/위키-큐레이션과-스키마-v2.md)).
같은 묶음에 있던 「RAG compose 프로젝트 이름 분열」은 `compose.yaml`을 유닛과 같은 `personal_rag`로
맞추고 두 파일을 대조하는 회귀를 걸어 해소했다.

- **컴파일된 `.pyc` 하나가 공개 릴리스를 막는다** — `tests/unit/test_f2_secret_pattern.py:32`는 규약대로
  토큰을 런타임에 조립하는데(`"ghp_" + (...)`), 컴파일된 `.pyc`는 그 결과를 **상수로 굳힌다**. 공개 export의
  스캔은 카나리아 설계상 `.gitignore`된 파일까지 훑으므로(`gitleaks dir`) 그 `.pyc` 하나로 릴리스가 멈춘다
  (2026-08-22 실측: 워킹트리 스캔 leak 1건 → 비-venv `__pycache__` 정리 후 0건) → export 절차에 정리 단계를
  넣거나 스캔에서 `*.pyc`를 제외할지 판단한다. **실제 비밀 아님 · 심각도 낮음(릴리스 때만 드러나는 운영자 함정)**.
  [해소 확인 2026-08-26] export 절차가 스캔 전에 비-venv `__pycache__` 를 정리한다. 스캔에서 `*.pyc` 를 빼는 쪽은 택하지 않았다 — 무시된 파일까지 훑는 것이 카나리아 설계의 핵심이기 때문이다(`tests/unit/test_public_export.py`).
  ↳ 재판정(2026-09-19, D241): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`wiki_store.SCHEMA_GUIDE`가 "필수 키 5개 정확히, 그 외 키 금지"로 남아 있다** — v1 twin 키 때부터
  있던 불일치이고 v2로 더 벌어졌다. 거부 판정 자체는 정확하고 **안내 문구만 실제 허용 범위보다 좁다**
  → 스키마와 같은 자리에서 안내를 함께 갱신한다. **심각도 낮음**.
  [해소 확인 2026-08-26] SCHEMA_GUIDE 가 실제 허용 키 집합을 말한다. 고정은 문장이 아니라 검증기에서 유도한 키 집합 대조라 다시 갈라지지 않는다(`tests/unit/test_wiki_store_schema_guide.py`).

  ↳ 재판정(2026-09-19, D242): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 연구계획서 자동생성 v2 실증 중 발견한 후속 과제 (2026-08-23)

완료 기능은 [연구계획서 자동생성](기능소개/연구계획서-자동생성.md)이다. 실제 문서 본문·개인정보·자격증명은 이 기록에 포함하지 않는다.

- **KD-AT 두 저장소를 함께 맞춰야 한다** — 고정 SHA로 안전은 확보했지만 `kimm-docbot` 또는 `im-not-ai` 변경 때 두 저장소의 코드·핀을 사람이 동기화해야 한다 → 호환성 검증, 핀 갱신, 롤백을 포함한 단일 운영 절차를 문서화한다. **영향 범위: 엔진 업그레이드와 유지보수 · 심각도 중**.
  [해소 확인 2026-08-25] `docs/guide/kd-at-두-저장소-운영.md` 에 호환성 검증·핀 갱신·롤백·증적 절차를 문서화했다 — `5e3c06fd`

  ↳ 재판정(2026-09-19, D243): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## interop 런타임 스테이징 검사기 사각지대 (2026-08-24, drive-doc-storage 롤아웃 중 발견)

- **무엇이 문제**: `deploy-skill.sh`의 `validate_gate_staging_imports`(및 `tests/unit/test_deploy_staging_is_derived_from_imports.py`)가 **상대 import를 따라가지 못한다**. PR #250이 `git_tag_signature.py`에 `from .typing_compat import override`를 도입했는데 `GATE_HELPERS`에 `typing_compat.py`가 누락돼도 로컬 검증·전체 스위트가 green이었고, 노드의 REQUEST 단계에서야 `ModuleNotFoundError`로 5개 스킬 배포 요청이 전부 실패했다(2026-08-24 실측).
  [해소 확인 2026-08-26] AST 워커가 `level>=1` 상대 import 를 패키지 디렉터리 기준으로 해석한다 — 픽스처(`tests/fixtures/deploy_staging_imports/`)가 누락 시 RED 가 되는 것을 고정한다.
  ↳ 재판정(2026-09-19, D244): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **어떻게 조치**: AST 워커에 `ast.ImportFrom`의 `level>=1`(패키지 내 상대 import) 해석을 추가해 staged 집합의 import-폐쇄성 검사가 상대 import도 포함하게 한다. 회귀 테스트: 상대 import 의존성을 가진 픽스처로 검사기가 RED가 되는 케이스.
  [해소 확인 2026-08-26] 위와 같은 변경. 동결된 `automation/deploy-skill.sh` 는 손대지 않았고 CI 가 도는 검사기 쪽만 고쳤다.
  ↳ 재판정(2026-09-19, D245): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **영향·심각도**: 보안 문제 아님 — 경로는 fail-closed로 멈춘다(조용한 오배포 없음). 가용성 문제: 매니페스트 누락이 노드에서만 발견돼 배포 요청이 막힌다. 급한 불은 `typing_compat.py`를 `GATE_HELPERS`에 추가해 해소됨(이 항목은 검사기 자체의 교정). **심각도 낮음**.
  [해소 확인 2026-08-26] 위와 같은 변경.

  ↳ 재판정(2026-09-19, D246): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## todo 승인 후 자동 등록 착지 후 남긴 것 (2026-08-25, 수리 티켓 t_e3243dc5 occ 2·4)

- **실행 파라미터가 없는 옛 승인 레코드는 자가 치유되지 않는다** — 이번 변경 이전에 만들어진
  approved 세대에는 `tasklist`·`title`·`notes`·`due` 가 없어 워처가 무엇을 쓸지 복원할 수 없고
  `TODO-EXEC legacy-unreplayable` 로 건너뛴다. 노드에 그런 세대가 2건 있다
  (`todo%3asha256%3a96f3af3f…`, `…e59dd4a4…`). 조치: 그 2건은 소유자가 이미 수동 등록을 마쳤으므로
  방치해도 중복 등록 위험이 없다. 남은 것을 확실히 닫으려면 각 claim 을 `verified` 로 조정하거나
  archive 세대를 정리한다. **동작 정상(건너뛰기), 로그 노이즈만 · 심각도 낮음**.
  [해소 확인 2026-08-26] 실측은 2건이 아니라 **4건**이었다(`32da22b1`·`89408841`·`96f3af3f`·`e59dd4a4`).
  claim 을 `verified` 로 손보는 쪽은 택하지 않았다 — 증명할 수 없는 쓰기에 receipt 를 쓰는 것이
  claim store 가 존재하는 이유 그 자체를 무너뜨리고, archive 세대는 소유자 승인의 정직한 이력이라
  지우지 않는다. 대신 리컨실러가 이 상태를 **사건이 아니라 정착 상태**로 다루게 해 매 틱 로그를
  없앴다(결과는 호출자에게 그대로 반환된다) — `beb0ecac`
  ↳ 재판정(2026-09-19, D247): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **실행 실패가 매 틱 재시도된다** — claim receipt 가 아직 없는 상태에서 create 가 실패하면
  (예: `gws` 자격증명 만료) 다음 틱이 다시 시도하고 `TODO-EXEC failed:…` 를 매 분 남긴다.
  중복 쓰기는 claim 이 막지만 저널이 시끄러워진다. 조치: 연속 실패 횟수를 세어 백오프하거나
  N 회 후 소유자에게 1회 통지한다(기존 `approval_reminder` 의 중복 억제 패턴 재사용).
  **쓰기 안전성에는 영향 없음 · 심각도 낮음**.
  [해소 확인 2026-08-26] 연속 실패가 백오프되고 저널 반복이 멈춘다 — 재시도 자체는 살아 있어 이후 성공이 정상 등록된다(`skills/todo/scripts/todo_execution_reconcile.py`, `tests/unit/test_todo_watch.py`).
  ↳ 재판정(2026-09-19, D248): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`docs/기능소개/todo-소유자-DM-승인-경로.md` 의 파일명이 여전히 현행과 어긋난다** — 내용에는
  정정 배너를 달았지만 이름은 "소유자-DM" 이다. `docs/done.md`·`docs/qa/RTS-5/00-summary.md` 가
  그 경로를 가리키는 역사적 기록이라 이름을 바꾸면 그 링크가 깨진다. 조치: 역사 기록의 링크를
  함께 옮기는 별도 사이클에서 리네임한다. **오해 소지는 배너로 차단됨 · 심각도 낮음**.
  [해소 확인 2026-08-26] `docs/기능소개/todo-승인-경로.md` 로 리네임하고 `done.md`·`qa/RTS-5/00-summary.md`·기능소개 상호참조까지 같은 사이클에서 옮겼다. FS3 회계 baseline 은 불변이라 건드리지 않는다.
  ↳ 재판정(2026-09-19, D249): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`todo_approval.py` 가 250 pure-LOC 를 넘어 예외 등록부에 올랐다** — 실행 파라미터 4개를 spec 에
  얹으며 248→254 가 됐다. 조치: transport/directory Protocol 포트를 별도 모듈로 빼면 25줄 남짓이
  줄지만, 승인 어댑터를 쪼개는 것은 배포 staging 목록과 conformance 인벤토리를 함께 재검증해야
  하므로 별도 사이클로 미룬다. **동작 영향 없음 · 심각도 낮음**.
  [해소 확인 2026-08-26] 그 별도 사이클을 지금 돌렸다. Protocol 포트 3개를
  `skills/todo/scripts/todo_approval_ports.py` 로 분리해 254→236, 등록부 행도 함께 제거했다
  (`test_no_stale_entries_below_the_ceiling` 이 죽은 행을 결함으로 본다). 선언만 옮겨
  conformance 인벤토리가 묶는 모듈은 그대로다 — `8d325dbd`

관련 기능: [todo 승인 후 자동 등록](기능소개/todo-승인-후-자동-등록.md).

  ↳ 재판정(2026-09-19, D250): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 회의록 과제 양식·Action Item 데이터베이스 중 남긴 것 (2026-08-27)

- **`meeting_project.py` 가 런타임 루트를 자기 방식으로 또 해석한다** — `_repo()` 의
  `Path(__file__).resolve().parents[3]` 는 `meeting_cli.runtime_root` 와 `stt_runtime.runtime_root`
  에 이어 **세 번째 사본**이다. 마운트 경로 형태가 바뀌면 셋을 따로 고쳐야 하고, 과거 이 해석이
  어긋났을 때 Drive 발행이 `DRIVE-PUBLISH-SKIP reason=ImportError` 로 조용히 건너뛰어진 전례가 있다.
  조치: 공유 해석기 하나로 모으고 세 호출부를 그것으로 돌린다. **지금은 동작 · 심각도 중**(조용한
  기능 상실 형태로 재발한다).

  **해소(2026-08-28)**: `meeting_runtime.runtime_root` 단일 정의로 합쳤다. 그리고 위의
  "지금은 동작" 판정은 **틀렸다** — 검증하지 않고 쓴 문장이었다. 라이브 실측에서 `_repo` 가
  `ModuleNotFoundError` 로 죽어 양식·action item 원장·미처리 전사본 조회가 **전부 조용히 무력**했고
  (`BOARD-FETCH-FAIL` 마커로 삼켜짐), 회귀는 마운트와 같은 깊이에서 별도 프로세스로 import 하는
  `tests/unit/test_meeting_runtime_root.py` 가 고정한다. `stt_runtime` 과의 사본 2개는 스킬 경계를
  넘는 import 를 만들지 않기 위해 그대로 둔다.

  ↳ 재판정(2026-09-19, D251): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## meeting 게이트웨이 플러그인 배포 경로 신설(2026-08-28) 중 남은 것

`!meeting` 이 "회의록 처리 시작에 실패했습니다"로 거부된 원인을 고치며(홈 플러그인 사본이 8/23 자로
5일 낡아 이미 없어진 규칙을 적용) 배포 경로와 드리프트 감시를 만들었다. 남은 두 건은 이 저장소가
닫을 수 없다.

- **[OWNER] 새 매니페스트 2행이 프로덕션에서 UNKNOWN 으로 나온다** — healthcheck 는 전용 키의
  `command=` 강제명령 래퍼를 거치고 그 래퍼는 명령 문자열 sha256 **정확 일치**만 허용한다.
  `.hermes/plugins/00-meeting-gate/{__init__.py,plugin.yaml}` 두 행이 내는 명령의 해시를 노드의
  `~/.local/libexec/autophagy-healthcheck-probe` 에 넣기 전까지 두 행은 exit 126 → UNKNOWN 이다.
  조용한 통과가 아니라 fail-closed 이므로 **탐지가 꺼진 것은 아니지만 아직 켜진 것도 아니다**.
  래퍼는 리포에 설치기가 없는 손 유지보수 자산이라 소유자만 넣을 수 있다. **심각도 중** —
  이 항목이 닫히기 전까지 플러그인 드리프트는 conformance(코드 대조)만 잡고 노드 실측은 못 잡는다.
  ↳ 재판정(2026-09-19, D252): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 게이트웨이 플러그인은 배포와 반영이 별개다** — 플러그인은 프로세스 시작 시 로드되므로
  `deploy.sh` 가 파일을 맞춰도 재시동 전까지 도는 코드가 다르다. `deploy.sh` 는 해시가 바뀐 틱에만
  `PLUGIN-CHANGED` 로 재시동 필요를 알리지만, 재시동 자체는 자동화하지 않았다 — 운영 규칙이
  "원인 확인 후에만, agent·peer 함께"이고 배포 스크립트가 매번 재시동하면 그 규칙이 무의미해진다.
  자동화하려면 재시동 주체가 아니라 **알림의 도달**을 고쳐야 한다(현재는 stderr 뿐이라 사람이
  `deploy.sh` 출력을 봐야 안다). **심각도 낮음** — 지금은 배포와 재시동이 같은 세션에서 이어진다.

  ↳ 재판정(2026-09-19, D253): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #518. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 제안서 노드 자율 구동 중 남긴 것 (2026-08-31)

> [해소 이관 2026-08-31] 제안서 노드 자율 구동의 3건을 수리 2건·재판정 1건으로 닫았다.

- **노드 agent 홈의 chromium 빌드가 세 벌(1.3G)이다** — 사용자공간 설치 프로브가
  `chromium_headless_shell-1234` 로 스모크를 통과하기 전에 `chromium-1228`·`headless_shell-1228`
  도 받았다. 조치 방향: `PROPOSAL_PREVIEW_CHROME` 이 가리키는 빌드만 남기고 나머지를 지운다.
  영향 범위: 디스크만. 심각도 낮음.
  [해소 재판정 2026-08-31] 1228 두 벌은 2026-07-14 `npx playwright install`이 만든 Hermes 브라우저 도구
  (`~/.hermes/hermes-agent/tools/browser_tool.py`) 자산이고, 제안서 것은 `chromium_headless_shell-1234`(340M)뿐이다 → 삭제 대상 없음.
  ↳ 재판정(2026-09-19, D254): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`PROPOSAL_*` env 는 게이트웨이 재시동 뒤에야 에이전트 도구 subprocess 에 닿는다** — 프로비저너는
  `~/.env.secrets`(systemd EnvironmentFile)에 upsert 만 하고 재시동은 설계상 하지 않는다. 조치 방향:
  재시동(agent·peer 함께)을 프로비저닝 체크리스트에 명시하거나 proposal CLI 가 cron 래퍼처럼
  `~/.env.secrets` 를 자가 로드하도록 검토한다. 영향 범위: 노드 대화형 렌더의 첫 실행만. 심각도 중.
  [해소 확인 2026-08-31] `skills/proposal/scripts/proposal_env.py`가 CLI 시작 시 `~/.env.secrets`의
  `PROPOSAL_*`·`KIMM_DOCBOT_*`를 fill-only로 읽고, 기존 환경값 우선은 `tests/unit/test_proposal_env_secrets.py`가 고정한다.
  ↳ 재판정(2026-09-19, D255): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **codex 이미지 경로의 비용이 예산 원장 단가와 다르다** — 원장은 API 단가로 예약하지만 codex
  `image_gen` 은 ChatGPT 구독에 청구된다. 조치 방향: 전송기별 단가 표를 분리해 원장이 실제 청구
  주체를 적게 한다. 영향 범위: 월 상한 판정의 정확도. 심각도 낮음.
  [해소 확인 2026-08-31] `skills/proposal/scripts/proposal_images.py`가 codex를 `chatgpt-subscription`으로
  USD 0·이미지 건수만 기록해 API 월 한도에서 제외하며, `tests/unit/test_proposal_images.py`가 이를 고정한다.

  ↳ 재판정(2026-09-19, D256): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 릴리스 승인 자동 완결 후속 (2026-08-31)

- **낡은 pending 릴리스 요청이 새 요청을 막는다 → 미결정·낡은 레코드만 release.sh 가 감사형 abandon 으로 자동 회수하게 한다.** release kind 의 lifecycle 은 다른 head 에 묶인 살아 있는 요청을 supersede 하지 않고 `binding-mismatch` 로 거부한다(`ReleaseSpec.bound` 가 action_hash 일치를 요구). 두 세션이 릴리스를 번갈아 올리면 앞선 요청의 head 가 origin/main 에서 밀려나는 순간 그 요청은 영원히 릴리스될 수 없는데도 `pending/release.json` 에 남아 **모든** 새 요청을 거부시킨다 — 2026-08-31 실측: v1.0.141@6a03321f(다른 세션) 가 c68a13dc 요청을 막았고, 노드 agent 계정에서 `python3 -m automation.release_abandon` 을 손으로 돌려야 풀렸다. 조치: `release.sh` 가 `request` 거부 사유가 `binding-mismatch` 이고 저장 레코드의 probe 가 `BOUND_PENDING`(소유자 미결정)이며 그 head 가 `origin/main` 의 조상이 아닐 때만 `release_abandon` 을 원격 실행(감사 줄·archive 그대로)한 뒤 한 번 재요청한다. 결정된(✅/⛔) 레코드는 지금처럼 건드리지 않는다(L3). 영향: 가용성 — 승인 자동 완결(타이머)은 무관하고 **요청 게시**만 막힌다. 심각도 중 — 병렬 세션이 릴리스를 올릴 때마다 재발한다. 회귀는 `tests/unit/test_release_sh.py` 에 stub 으로 고정한다.

  [해소 확인 2026-08-31] 구현: `automation/release.sh` + `release_approval.py abandon` 서브커맨드 — `binding-mismatch`·`bound_pending`이고 head가 origin/main tip에서 밀려난 경우만 감사형 abandon 뒤 정확히 1회 재요청하며 결정 레코드는 불변(L3).
  회귀: `tests/unit/test_release_sh.py` stub. 정정: 인시던트 head `6a03321f`는 origin/main의 조상이라 조상-아님 조건은 자기 사례를 못 거른다 — 실제 경계는 tip 동일성이다.

  ↳ 재판정(2026-09-19, D257): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Plaud lifelog 동기화 후속 (2026-09-02)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **Discord 리액션 transport·`record_push_approval` 이 memory_relocate 와 사본으로 갈라져 있다 → interop 공용
  모듈로 승격해 두 워처가 공유한다.** relocate 모듈 import 는 memory_curator 체인을 끌고 와 의도적으로 복사했다.
  영향 범위: 코드 중복뿐, 동작 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `refactor(interop): 리액션 승인 transport 를 공용 모듈로 모으고 plaud 종결 통지를 승인 스레드에 닫는다` — automation/interop/reaction_approval.py 신설, plaud_sync·memory_relocate 가 import

  ↳ 재판정(2026-09-19, D258): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **relocate·plaud 워처가 같은 obsidian write clone 에 `write_note` 한다 → 규약 (n)대로 공유 lock 도입을 검토한다.**
  경쟁 시 push 는 retryable 실패 후 다음 틱 재시도이고 read-back 검증이 오염을 막는다(심각도: 낮음).
  ↳ 처리(2026-09-03): `fix(obsidian-write): 클론 lock·tmp_pack 정리·fetch 전용 타임아웃·blobless 클론으로 승인된 노트 유실을 막는다` — automation/obsidian_write/clone_lock.py, `OBSIDIAN_WRITE_FETCH_TIMEOUT`(기본 900초), promisor 전환

  ↳ 재판정(2026-09-19, D259): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **결과 통지가 승인 스레드 게시까지만이고 outcome-archive(접두어+아카이브) 미채택 → `origin_notice.deliver(outcome=)`
  채택을 검토한다.** 통지는 best-effort 라 실행·영수증 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `refactor(interop): 리액션 승인 transport 를 공용 모듈로 모으고 plaud 종결 통지를 승인 스레드에 닫는다` — automation/interop/reaction_approval.py 신설, plaud_sync·memory_relocate 가 import · plaud `notify` 가 origin_notice.deliver(outcome=) 로 ✅ 완료·⛔ 취소·⌛ 만료를 스레드에 표시·아카이브

  ↳ 재판정(2026-09-19, D260): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **memory_relocate 도 message 소실(missing) 뒤 재게시 때 `resolve_new_binding` 만 불러 같은 요청에 스레드가 둘 생길 수 있다 → plaud 의 `effects_live.thread_candidates`(레코드 `approval_thread_id` 재사용)를 interop 공용으로 올려 두 생산자가 공유하도록 검토한다.** 재게시는 드물어 동작 영향 낮음, 보안 문제 아님.
  ↳ 처리(2026-09-03): `refactor(interop): 리액션 승인 transport 를 공용 모듈로 모으고 plaud 종결 통지를 승인 스레드에 닫는다` — automation/interop/reaction_approval.py 신설, plaud_sync·memory_relocate 가 import · 재게시가 레코드 approval_thread_id 를 먼저 재사용

  ↳ 재판정(2026-09-19, D261): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **Plaud 기본 녹음명이 일시라 노트 파일명에 날짜가 두 번 들어간다(`2026-09-02-2026-09-02-13…`) → `note.plan_lifelog_note` 슬러그가 파일명의 날짜 접두와 같은 날짜로 시작하면 한 번만 쓰도록 검토한다.** 미관 문제, 기능 영향 없음(id 해시 접미가 유일성을 보장).
  ↳ 처리(2026-09-03): `fix(plaud-sync): 녹음명이 일시로 시작하면 노트 파일명에 날짜를 한 번만 쓴다`

  ↳ 재판정(2026-09-19, D262): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **obsidian-write 클론의 `git fetch` 가 120초(`writer._GIT_TIMEOUT_SECONDS`)에 죽으며 매 틱 ~770 MB `tmp_pack_*` 잔해를 남겼다(2026-09-02 실측: 8/4 이후 한 달, 230개·176 GB, plaud·relocate 두 워처 공용) → 쓰기 전용 클론을 `--filter=blob:none` 부분 클론으로 바꾸거나 fetch 타임아웃을 델타 크기에 맞게 올리고, 실패한 fetch 의 `tmp_pack_*` 를 다음 시도 전에 정리한다.** 심각도 높음(디스크 성장 + 승인된 노트가 저장되지 않음), 보안 문제 아님. 임시 조치: 잔해 삭제 + 3600초 fetch 로 1회 따라잡기.
  ↳ 처리(2026-09-03): `fix(obsidian-write): 클론 lock·tmp_pack 정리·fetch 전용 타임아웃·blobless 클론으로 승인된 노트 유실을 막는다` — automation/obsidian_write/clone_lock.py, `OBSIDIAN_WRITE_FETCH_TIMEOUT`(기본 900초), promisor 전환


  ↳ 재판정(2026-09-19, D263): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 노드 배포 표면 감사 후속 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **드리프트 프로브가 "선언된 것"만 대조해 손배포 사본을 보지 못한다 → `automation/deploy_all_probe.py`(또는 healthcheck)에 홈 표면 스캔을 더해 매니페스트에 없는 `~/.hermes/scripts/*`·`~/.hermes/plugins/*/` 를 미선언 산출물로 보고한다.** 2026-09-01 실측(agent 홈): `restart-hermes-gateway-once.sh`(에이전트 저작 재시동 헬퍼)·`regression_bank_weekly.py`(출처 불명)·`send_cost_report.py`·`poll_reminders.py`·`repair_report_consume_watch.py`(리포 소스는 있으나 선언 없음)·`mail_attachment_drive_{sync,watch}.py`(이번 착지로 해소)가 `configs/watcher-deploy-manifest.txt` 에 없고, 프로브는 파생 매니페스트 행만 읽어 이들에 대해 "드리프트 0"을 보고한다 — 손배포일수록 안 보이는 구조다. 영향 범위: 노드 배포 판정 전체(심각도: 높음).
  ↳ 처리(2026-09-03): `feat(deploy): 매니페스트에 없는 홈 배포물을 프로브가 경고로 관측한다` — `undeclared` 관측(경고, `--strict-undeclared` 로 drift 승격), tests/unit/test_deploy_all_undeclared.py

  ↳ 재판정(2026-09-19, D264): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **리포에 소스가 있는 홈 배포물 3개에 배포 선언이 없다 → `automation/cost-report`·`automation/reminder_poller`·`automation/repair`(cron) 옆에 `deploy-manifest.txt` 를 신설하고 `python3 -m automation.watcher_manifest emit` 으로 파생본을 재생성한다.** `rg -c 'send_cost_report|poll_reminders|repair_report_consume' configs/watcher-deploy-manifest.txt` → 모두 0인데 노드에는 셋 다 배포돼 cron(daily-cost-report·reminder-poller·repair-report-consumer)으로 돈다. 영향 범위: 이 3개 워처의 드리프트 탐지(심각도: 중).
  ↳ 처리(2026-09-03): `feat(deploy): 손배포되던 홈 산출물 4종을 deploy-manifest 에 선언하고 빠진 배포기를 만든다` — cost-report·reminder_poller·repair 에 deploy-manifest.txt+deploy.sh, 중앙 표 emit 재생성

  ↳ 재판정(2026-09-19, D265): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **게이트웨이 플러그인 `05-skill-generation` 에 배포 선언이 없다 → `automation/skill_generation/deploy-manifest.txt` 를 신설해 `.hermes/plugins/05-skill-generation/{__init__.py,plugin.yaml}` 를 등록한다.** 지금은 노드 사본과 리포 소스 해시가 같지만(`3c0072ea…`) 감시가 없어 다음 변경부터 조용히 벌어진다 — `interop-protocol` 이 같은 조건에서 6주째 07-20 사본이다. 영향 범위: 플러그인 드리프트 탐지(심각도: 중).
  ↳ 처리(2026-09-03): 같은 커밋 — automation/skill_generation/deploy-manifest.txt 가 05-skill-generation 의 __init__.py·plugin.yaml 을 등록

  ↳ 재판정(2026-09-19, D266): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **미러 dirty 가 3일간 아무 사건도 만들지 않았다 → `checkout_mirrors_origin` 프로브가 실제로 FAIL·수리 티켓을 냈는지 노드 로그로 확인하고, 리컨실러의 `release-backlog` 다이제스트가 "미러가 dirty 라 못 따라온다"를 "소유자가 아직 릴리스를 안 올렸다"와 구분해 통지하도록 보강한다.** 2026-09-01 실측 `/srv/autophagy-private/deploy-reconcile/state.json`: `consecutive_failures=162`, `skip_reason=release-backlog`, `incident_open=false` — 08-29 부터 미러가 미커밋 파일로 동결된 동안 같은 조용한 경로를 탔다. 영향 범위: 노드 배포 정지 탐지(심각도: 중).
  ↳ 처리(2026-09-03): `fix(reconcile): 릴리스 백로그 다이제스트가 미러 동결(dirty/ahead)을 '릴리스 미실행'과 구분해 말한다` — `mirror_state` 를 state.json 에 additive 저장. 노드 로그로 프로브 FAIL 여부를 확인하는 부분은 OWNER 신규 항목

  ↳ 재판정(2026-09-20, D267): HISTORY-UNVERIFIED — 보이는 저널이 사고 기간보다 뒤에서 시작해 과거 프로브·티켓 발생을 입증할 수 없다. 사건이 없었다고 단정하거나 확인을 위해 새 장애를 만들지 않았다.
- **자가 스킬 감사 결과가 노드에 남지 않고 겹침 판정이 결정으로 이어지지 않는다 → `automation/selfskill_audit` 리포트를 `~/.hermes/logs/selfskill-audit/` 에도 남기고 `OVERLAPS-GOVERNED` 를 승격·폐기 결정 큐(소유자 항목)로 연결한다.** 노드에 자가 스킬 5개(2026-08-18~28)가 있고 `meeting-minutes-authoring`·`document-publishing` 은 governed meeting·doctype·report 와 겹치는데, `selfskill-audit-watch` 는 매일 ok 로 끝나며 산출물이 Discord 로만 간다. 영향 범위: 자가 스킬 거버넌스(심각도: 중).
  ↳ 처리(2026-09-03): `feat(selfskill-audit): 감사 결과를 노드 로컬 jsonl 에 남기고 겹침을 미결 원장으로 추적한다` — logs/selfskill-audit/<YYYY-MM>.jsonl + pending-overlaps.json. 승격·폐기 결정 자체는 OWNER 신규 항목


  ↳ 재판정(2026-09-19, D268): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 첨부 아카이브 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`mail_attachment_drive_sync.py` 가 250 pure-LOC 상한을 넘겨 F2 등록부에 예외로 올라갔다(378) → 순수 계획·이름 정책·상태 DB(`mail_attachment_archive.py`)와 Drive 실행(CLI)로 나누고 등록부에서 내린다.** 착지 사이클에서는 돌고 있는 archive.db·folders.json 호환을 한 파일에서 보장하는 쪽을 택했다. 영향 범위: 코드 크기 규약만, 동작 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `refactor(mail): 첨부 아카이브를 순수 계획(mail_attachment_archive)과 Drive 실행 CLI 로 나눈다` — 185/154 pure LOC, F2 등록부에서 내림


  ↳ 재판정(2026-09-19, D269): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 스킬 실행 경로 후속 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **mail 외 SKILL.md 들이 아직 `~/.hermes/skills/<skill>/scripts/…` 를 안내한다 → SS-1(2026-08-15) 이후 그 루트는 자가 스킬 전용이라 경로가 없다. wiki·topics·speechtotext·report·recall·proposal 등(`rg '~/.hermes/skills/[a-z-]+/scripts' skills/*/SKILL.md`)을 `/srv/autophagy-skills/live/<skill>/scripts/…` 로 바꾸고 각 스킬 버전을 올린다.** 최근 14일 세션 기록에서 없는 경로 시도 30건·미러 경로 실행 90건이 이 안내에서 비롯됐다 — 에이전트가 파일을 찾아 헤매다 낡은 사본을 채택한다. 지금은 mail 만 고쳤다(심각도: 중 — 다른 스킬도 같은 방식으로 낡은 코드를 실행할 수 있다; 발송 안전성은 각 스킬의 승인 게이트가 지킨다).
  ↳ 처리(2026-09-03): `docs(skills): SKILL.md 스크립트 경로를 governed live 마운트(/srv/autophagy-skills/live)로 고친다` — 15개 SKILL.md(meeting 은 configs·prompts 도), 버전 patch 상승

  ↳ 재판정(2026-09-19, D270): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **관리자 배포본 밖 사본 실행 거부(`mail_runtime.governed_copy_refusal`)가 mail 에만 있다 → calendar·budget·todo·wiki·coordination 등 mutating CLI 를 가진 스킬에 같은 판정을 넣되 사본을 늘리지 않는다.** 스킬은 import 시점에 `automation` 을 못 쓰므로 판정 함수의 단일 정의를 어디에 둘지(각 스킬 `*_runtime.py` 의 인라인 + 동일성 회귀, 또는 마운트된 릴리스 경유 lazy import)를 먼저 정한다. 게이트웨이 수준(`interop-protocol` 플러그인 `pre_tool_call`)의 경로 정책이 근본 해법이지만 그 플러그인은 배포 경로가 없어 [follow-ups-deferred](follow-ups-deferred.md) 의 OWNER 선행 조건에 묶여 있다(심각도: 중).
  ↳ 처리(2026-09-03): `refactor(skill-mount): governed 사본 거부 판정을 automation.skill_mount 단일 정의로 올린다` + `feat(calendar|wiki|coordination|budget|todo): … STALE-SKILL-COPY-BLOCK 으로 거부한다` + `test(skills): … conformance` — 정의는 skill_mount 하나, 스킬은 `<skill>_governed.py` 로 지연 호출·ImportError 시 fail-closed


  ↳ 재판정(2026-09-19, D271): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 기관메일 회신 원문 인용 후속 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **Gmail 계정 회신(`gws gmail +reply --message-id`)은 인용을 붙이지 않는다 → gws 가 원문을 자동 인용하는지 노드에서 실측한 뒤, 안 하면 `mail_gmail_send.ReplyMailRequest` 본문에 같은 `mail_quote.render_quote` 를 붙인다.** Gmail 은 message-id 스레딩으로 대화 보기에 원문이 이미 묶이므로 동작 정상 — 표시 일관성 문제일 뿐이고 발송 안전성과 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): 코드 불필요 — 실측(librarian, 2026-09-03): `gws`(npm `@googleworkspace/cli` 0.22.5, github.com/googleworkspace/cli) 의 `gmail +reply` 는 crates/google-workspace-cli/src/helpers/gmail/reply.rs 에서 `format_quoted_original(original)` 을 본문 뒤에 붙이고 In-Reply-To/References/threadId 도 자동 설정한다. Gmail 회신도 원문이 인용된다


  ↳ 재판정(2026-09-19, D272): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## cron 워처 수리 후속 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`typing.override` 직수입이 3.11 no-agent 런타임 밖 경로에 남아 있다 → 해당 경로를 cron 체인에 편입하거나 리팩터할 때 `automation/typing_compat.py`(또는 mail_runtime 의 인라인 폴백 패턴) 경유로 먼저 바꾼다.** `skills/doctype/scripts/doctype_save.py`(게이트웨이 대화 경로 전용 — doctype 은 cron 워처가 없다)·`automation/group_roster/editor.py`·`automation/managed_skills/submission_errors.py`(둘 다 워크스테이션 CLI 전용)가 `from typing import override` 를 직수입한다. Hermes cron 의 uv CPython 3.11 이 실행하는 체인에는 현재 도달하지 않아 동작 정상 — mail-triage-watch 를 매 틱 죽인 결함(2026-08-31)과 같은 계열이지만 지금은 잠복이다. 영향 범위: 현재 없음, **심각도 낮음**. cron 편입 순간 같은 ImportError 가 재발한다.
  ↳ 처리(2026-09-03): `fix(py311): typing.override 직수입을 3.11 호환 경로로 바꾸고 가드가 재발을 막는다` — 남은 직수입 전부 typing_compat/인라인 폴백으로, tests/unit/test_py311_syntax_guard.py 가 배포 소스 전수를 검사


  ↳ 재판정(2026-09-19, D273): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 화자 구분·문장 단위 출력 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **문장 경계가 구두점에 묶여 있어 구두점 없는 전사에서는 화자 블록이 무너진다** — whisper 가 구두점을 내지 않는 언어·구간(배포 검증 실측: 중국어 4화자 샘플 57초가 한 문장 158자 → 화자1 하나)에서는 `split_sentences` 가 문장 하나를 만들고 화자 배정도 그 문장의 최다 겹침 하나로 굳는다. 한국어 large-v3-turbo 출력은 735문장 중 1건만 구두점이 없어 실사용 영향은 낮다. 조치: `stt_diarize.assign` 이 문장 안 토큰 타이밍으로 화자 전환 지점(단어 경계 최근접)에서 문장을 쪼개거나, 구두점 없는 긴 문장을 시간 기준(예: 15초)으로 나눈다. **영향 범위: 구두점 없는 출력에 한정 · 심각도 중**.
  ↳ 처리(2026-09-03): `feat(speechtotext): 화자 경계와 15초 무구두점 구간에서 문장을 쪼개 한 문장이 여러 화자를 삼키지 않게 한다` — skills/speechtotext/scripts/stt_split.py `split_on_turns`, assign 직전에 적용(fail-soft)


  ↳ 재판정(2026-09-19, D274): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## healthcheck 폭주 수리(PR #347) 중 발견한 인접 결함

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`from typing import override` 를 폴백 없이 쓰는 모듈이 남아 있다 → `automation/typing_compat.override` 로 통일하거나 `tests/unit/test_py311_syntax_guard.py` 범위를 넓혀 기계적으로 막는다.** `automation/freeze_inventory.py`, `automation/group_roster/editor.py`, `automation/install/allowed_signers.py`, `automation/managed_skills/{publish_core,submission_errors,submission_source,submission_transport}.py`, `automation/managed_sync/verify.py`, `automation/memory_relocate/cli.py`, `automation/update_trust.py`, `automation/update_trust_state.py`, `skills/doctype/scripts/doctype_save.py`가 해당한다. Hermes no-agent cron 은 uv CPython 3.11 로 돌아 t_4829b4b5 의 원인처럼 이들 중 cron 자식 체인에 들어가는 것이 생기면 같은 ImportError 가 재발한다. 영향 범위: 현재 cron 체인에는 없음(정적 import 그래프 확인), 잠재 결함, **심각도 낮음**.
  ↳ 처리(2026-09-03): `fix(py311): typing.override 직수입을 3.11 호환 경로로 바꾸고 가드가 재발을 막는다` — 남은 직수입 전부 typing_compat/인라인 폴백으로, tests/unit/test_py311_syntax_guard.py 가 배포 소스 전수를 검사


  ↳ 재판정(2026-09-19, D275): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 요청별 승인 스레드 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **budget 에는 만료 경로가 없고 todo `_expire()` 는 통지 없이 아카이브해 `origin_notice.ThreadOutcome.EXPIRED` 가 두 스킬에서 미사용이다 → 만료 시 결과 통지(`deliver(..., outcome=EXPIRED)`)를 붙여 스레드가 `⌛ 만료 ·` 로 닫히게 한다.** 만료된 요청의 스레드가 활성 목록(=진행 중 요청 보드)에 남는 정확도 문제이며 실행·원장·영수증에는 무영향(심각도 낮음).
  ↳ 처리(2026-09-03): `feat(approval): 만료된 todo·budget 요청의 승인 스레드를 ⌛ 만료 로 닫고 결과를 통지한다` — todo `_expire` 가 EXPIRED 를 전달, budget 은 24시간 TTL 만료 분기 추가


  ↳ 재판정(2026-09-19, D276): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 티켓 스윕 후속 (2026-09-02)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **digest 요약 실패는 이제 `llm-calls.jsonl` 에 `digest_summary_failed` 로 남지만 classify 폴백(`_CLASSIFY_FALLBACK`)은 여전히 사유를 남기지 않는다 → `triage_llm.log_failure(purpose="classify", …)` 를 classify 재시도 소진 지점(`triage_digest.build_item`)에도 붙인다.** `⚠️ 분류 실패` 배지만 남고 원인(LlmCallError/LlmParseError)이 사라져, t_44b406fe 처럼 조치 불가능한 수리 티켓이 다시 생성될 수 있다. 영향 범위: 다이제스트 분류 실패의 원인 추적만, 동작은 fail-open 으로 정상 — 심각도 낮음.
  ↳ 처리(2026-09-03): `fix(mail-digest): classify 재시도 소진 사유를 llm-calls.jsonl 에 남긴다` — `triage_llm.log_failure(purpose="classify")`, tests/unit/test_mail_digest.py

  ↳ 재판정(2026-09-19, D277): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 티켓 t_bd0d3789 후속 (2026-09-03)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(브랜치 session/v110-convenience, v1.1.0 편의 릴리스). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`automation/drive_client.py` 의 `ensure_folder_path` 가 `~/.hermes/drive-publish/folders.json` 의 캐시된 폴더 id 를 살아 있는지 확인하지 않고 그대로 돌려준다 → 캐시 id 를 `files get fields=trashed,parents` 로 재검증하고, 휴지통에 있거나 없어졌으면 캐시를 버리고 새로 조회하도록 폴백을 넣는다.** 2026-08-26 에 옮겨지거나 버려진 옛 폴더(`autophagy/회의록/2026`)로의 게시가 조용히 성공하고, 파일은 살아 있는 트리 어디에도 보이지 않는다. 영향 범위: 파사드로 게시하는 모든 스킬 · 자료 유실은 없다(파일은 존재하되 자리가 틀리다) — 심각도 중.
  ↳ 처리(2026-09-03): `fix(drive): 캐시된 폴더 id 를 files get 으로 재검증하고 휴지통·부재면 재조회한다` — `automation/drive_client_cache.py`(`_folder_alive` + 접두 키 무효화), tests/unit/test_drive_client.py 가짜 runner 로 trashed → 재조회·캐시 재기록 회귀

  ↳ 재판정(2026-09-19, D278): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## LiteLLM GPT 전환과 헬스체크 정리 후 남긴 것 (2026-09-03)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(브랜치 session/v110-convenience, v1.1.0 편의 릴리스). 회계 가드가 원문을 요구해 불릿을 그대로 둔다. 노드 설치(OWNER)와 alerting 검토(OBSERVE)는 아래 두 불릿.

- **`automation/healthcheck.sh` 의 LiteLLM 프로브가 `/health/liveliness`(프록시 생존)만 봐서 상류 제공자 장애(2026-09-03 00:21 KST 부터 전 요청 429 잔액 소진)를 11시간 동안 아무도 몰랐다 → 30분 틱에 `glm-main` 실제 completion 1건(≈48건/일)을 더해 실패 시 FAIL·수리 티켓을 내고, LiteLLM `alerting` 에 outage 유형을 alert-dispatcher 와 함께 붙일지 검토한다.** ops 의 compose 배포본은 seed 보다 낡아 `/health`(모든 배치에 실제 completion, 하루 8,640건)를 치고 있었고 이번 배포로 seed 의 liveliness 로 수렴한다 — 그 뒤 실제 completion 프로브는 우리 healthcheck 만이 낼 수 있다. 영향 범위: 상류 장애 탐지 지연 — 심각도 중.
  ↳ 처리(2026-09-03): `feat(healthcheck): LiteLLM 에 실제 completion 프로브(litellm_completion)를 더해 상류 429·잔액 소진을 FAIL·수리 티켓으로 드러낸다` — `probe_litellm_completion`(glm-main, max_tokens 1, 20초, choices[0] 만 PASS, 상태·error type/code 만 출력), 래퍼 `# probe-type: litellm_completion` + allowlist 해시, tests/unit/test_healthcheck_probe_wrapper.py·test_healthcheck_allowlist_manifest.py(RED 5→GREEN 29). LiteLLM `alerting` 연동은 검토 보류(아래 OBSERVE).
  ↳ 재판정(2026-09-19, D279): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 재생성된 healthcheck 래퍼(`litellm_completion` 프로브 포함)는 노드에서 `automation/provision-healthcheck-probe.sh` 를 소유자가 돌려야 설치된다 → 설치 전까지 새 행은 allowlist 불일치로 FAIL 한다.** 영향: 설치 전 healthcheck 30분 틱의 LiteLLM completion 행 1건 — 심각도 낮음(설치 1회로 끝).
  ↳ 재판정(2026-09-19, D280): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OBSERVE] LiteLLM `alerting` 에 outage 유형을 alert-dispatcher 와 함께 붙일지 → 실제 completion 프로브가 2주간 429 를 몇 번 잡는지 본 뒤 결정(프로브가 충분하면 얹지 않는다).** 영향: 없음(관측 대기).

  ↳ 재판정(2026-09-19, D281): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

> [이관 2026-09-04 · 해소] BLOCKED 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`docs/guide/operations.md`가 안내하는 `userctl` 래퍼가 노드에 없다** — 표의 복구 명령 `userctl <node> <account> restart hermes-gateway.service`가 `command not found`로 실패해, 이번 재시동도 `sudo -u <account> env XDG_RUNTIME_DIR=/run/user/$(id -u <account>) systemctl --user restart hermes-gateway.service`로 우회했다 → 래퍼를 설치하거나 문서를 실제로 동작하는 명령으로 고친다. **동작 영향 없음 · 심각도: 낮음(장애 대응 중에 한 번 더 막힌다)**.
  ↳ 해소(2026-09-04): 재판정이다. `docs/guide/operations.md` 1절(10~17행)이 `userctl` 을 관리 셸에서 운영자가 직접 정의하는 셸 함수로 규정한다. 노드에 래퍼가 설치돼 있어야 한다는 전제 자체가 성립하지 않으므로 코드 수정 없이 닫는다.

  ↳ 재판정(2026-09-19, D282): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

> [이관 2026-09-04 · 해소] BLOCKED 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`docs/guide/operations.md`가 자격증명 조회 alias를 「프로비저닝에 없다」고 계속 말한다** — `automation/provision-agent.sh`가 이제 복원하므로 낡은 서술이지만, 그 문서는 `configs/freeze-inventory.txt`의 동결 대상이라 이번에 고치지 않았다(같은 문구를 가진 `onboarding-kit.md`와 기능소개 문서는 갱신했다). **오안내 위험만 · 동작 영향 없음 · 심각도 낮음** → 그 계획의 동결이 풀리는 사이클에서 한 문단을 함께 고친다.
  ↳ 해소(2026-09-04): 그 문구가 없다. `docs/guide/operations.md` 에서 alias 를 grep 하면 `~oriclaw/.bash_aliases` 를 말하는 360행만 걸리고, 그 파일은 동결 이후 커밋 3건(aae018e70, 32738984d, 7496167d2)을 받아 이미 갱신됐다.

  ↳ 재판정(2026-09-19, D283): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

> [이관 2026-09-04 · 해소] BLOCKED 에서 옮겨 왔다. 아래 「healthcheck 폭주 수리(PR #347)」 절의 불릿과 **같은 결함이 두 절에 두 번 기록된 것**이다. 회계 가드가 원문을 요구해 두 원문을 모두 남긴다.

- **수리 occurrence가 done 티켓에 계속 붙는다** — repair-detector가 signature로 기존 티켓을 찾을 때
  상태를 보지 않아, 완료된 healthcheck 티켓(t_6f3f7e1e)에 재발 occurrence가 55건까지 조용히 쌓였고
  (`created:false`) 보드의 열린 목록에는 아무것도 나타나지 않았다 → signature 티켓이 done/archived면
  새 티켓을 만들거나 reopen한다. **재발 가시성 영향 · 심각도 중**.
  ↳ 해소(2026-09-04): `automation/repair/repair_core.py` 의 `RepairRegistry.claim` 이 저장된 티켓이 닫혀 있으면 새 티켓을 발급하고, 티켓 상태를 읽을 수 없을 때는 기존 중복 제거 동작을 그대로 유지한다. 아래 「healthcheck 폭주 수리(PR #347)」 절의 중복 기록과 한 건이다.
  ↳ 재판정(2026-09-09): 그 수정은 **실행되지 않고 있었다** — repair detect 를 부르는 세 진입점(`repair_reporter`·`skills/repair/SKILL.md`·`healthcheck_command_builder.sh`)이 모두 계정 홈 사본을 가리켰고 그 사본은 7월 세대라 09-04 의 로직이 프로덕션에 존재하지 않았다. 그래서 소유자가 09-09 08:09 에 올린 수동 수리 2건이 다시 종결 카드에 묻혔다. 실행 경로를 불변 릴리스 런타임으로 고정해 닫았다(회귀 `tests/unit/test_repair_exec_path.py`).

  ↳ 재판정(2026-09-19, D284): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## healthcheck 폭주 수리(PR #347) 중 발견한 인접 결함

> [이관 2026-09-04 · 해소] BLOCKED 에서 옮겨 왔다. 위 「Hermes kanban 열린 이슈 정리(2026-08-22)」 절의 불릿과 같은 결함의 두 번째 기록이다.

- **수리 티켓 레지스트리(`~/.hermes/repair-tickets.json`)는 done 카드에도 같은 시그니처로 occurrence 만 계속 올린다 → `automation/repair/repair_core.py` 의 registry lookup 에서 카드 상태를 확인해 done 이면 새 티켓을 발급(또는 재오픈)하는 설계 판단을 별도 논의한다.** t_318263ba 2623회·t_c0718520 2233회·t_6f3f7e1e 2078회 등에서 done 카드가 재발해도 소유자가 볼 새 신호가 없다. 영향 범위: 수리 티켓 재발 감지, 운영 가시성 저하, **심각도 낮음**.
  ↳ 해소(2026-09-04): 위 불릿과 동일한 결함이며 이번 사이클에 `automation/repair/repair_core.py` 에서 닫혔다. `RepairRegistry.claim` 이 저장된 티켓이 closed 면 새 티켓을 발급하고, 상태를 읽지 못하면 기존 중복 제거 동작을 유지한다.
  ↳ 재판정(2026-09-09): 그 수정은 **실행되지 않고 있었다** — repair detect 를 부르는 세 진입점(`repair_reporter`·`skills/repair/SKILL.md`·`healthcheck_command_builder.sh`)이 모두 계정 홈 사본을 가리켰고 그 사본은 7월 세대라 09-04 의 로직이 프로덕션에 존재하지 않았다. 그래서 소유자가 09-09 08:09 에 올린 수동 수리 2건이 다시 종결 카드에 묻혔다. 실행 경로를 불변 릴리스 런타임으로 고정해 닫았다(회귀 `tests/unit/test_repair_exec_path.py`).

  ↳ 재판정(2026-09-19, D285): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## DM→#agent-chat 이관(승인 표면 v7) 중 발견한 후속 과제 (2026-08-24)

> [이관 2026-09-04 · 해소] OBSERVE 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **승인 스레드는 kind별 고정 앵커다** — 지시 메시지나 다이제스트 메시지 밑에 직접
  스레드를 다는 origin 앵커링(결과 통지의 `origin_notice`와 대칭)은 후속 개선. 조치:
  운영해 보고 소유자 선호에 따라 producer에 origin 전달을 확장. **UX 개선 · 심각도 낮음**.
  ↳ 해소(2026-09-04): 요청별 스레드로 구현됐다 — `automation/interop/approval_surface.py:61 class RequestThread`, `automation/interop/approval_directory.py:170 agent_chat_request_thread(kind, request)`, 그리고 AGENTS.md 의 「요청별 승인 스레드 규칙」(2026-09-01).

  ↳ 재판정(2026-09-19, D286): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 음성 녹취 회의록 자동화(speechtotext) 착지 후 남긴 것 (2026-08-25)

> [이관 2026-09-04 · 해소] OBSERVE 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **화자 분리(diarization)가 없다** — whisper.cpp의 `--diarize`는 채널 분리 녹음에만
  유효하고 `tinydiarize`는 실험적이다. 회의록의 "타인 액션아이템" 정확도는 LLM 추출에
  의존한다. 조치: 필요해지면 별도 로컬 diarization 파이프라인을 검토(파이썬 ML 스택이라
  stdlib 정책 예외가 필요). **현재 기능에 영향 없음 · 심각도 낮음**.
  ↳ 해소(2026-09-04): `skills/speechtotext/scripts/stt_diarize.py` 와 `skills/speechtotext/scripts/stt_speakers.py` 가 존재한다. 같은 소절의 「해소 (2026-09-01)」 줄이 sherpa-onnx 착지를 이미 기록해 두었다.

  ↳ 재판정(2026-09-19, D287): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 용어 교정 문서 단계 이동 (2026-09-05) — 소유자·관측

- **OWNER — Drive 에 교정 참고 문서가 아직 없다 → 소유자가 `autophagy/용어집.csv`(모든 산출물)와 필요하면 `autophagy/회의록/용어집.csv`·`autophagy/라이프로그/용어집.csv` 를 만들어야 실제 교정이 걸린다.** 2026-09-04 실측에서 공통 파일이 부재했고, 2026-09-04 QA 사고로 노드의 옛 `~/.hermes/speechtotext/glossary.txt` 도 지워졌다. 코드는 없는 참고 문서를 빈 것으로 읽으므로(fail-soft) 실패가 아니라 **교정 0건**으로 나타난다 — 형식과 자리는 [용어-교정-규약](guide/용어-교정-규약.md). 심각도 중(문서 품질), 저장소에서 닫을 수 없음.
  ↳ 재판정(2026-09-19, D288): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **OBSERVE — 라이프로그 노트의 파일 이름 슬러그는 교정하지 않는다 → 파일 탐색기에는 오인식 표기가 남을 수 있다.** 의도된 절충이다: 경로가 참고 문서를 따라 움직이면 용어집을 한 줄 고친 날 같은 녹음이 노트 둘로 갈라진다(제목·본문은 교정된다). 소유자가 파일 이름까지 맞추고 싶다고 말하면 그때 이관 규칙(옛 경로 → 새 경로 이동)을 함께 설계한다. 심각도 낮음.

  ↳ 재판정(2026-09-19, D289): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 라이프로그 전사·화자 품질 교정 중 소유자 몫으로 남은 것 (2026-09-06)

> [OWNER] 둘 다 노드 설정(`~/.env.secrets`)과 라우팅 정책 결정이라 이 저장소가 닫을 수 없다.

- **화자 임베딩 모델 상향** — 임베딩 경로는 `SPEECHTOTEXT_DIARIZE_EMBEDDING` 이 가리키는 노드 설정이라
  저장소가 바꾸지 않는다. 133초 2인 샘플에서 정답이 나오는 임계값 구간의 너비는 현재
  `eres2net_base` 가 한 칸(1.1), `eres2net_200k` 가 세 칸(0.6~1.0)이었다 — 임계값이 조금 어긋나도 답이
  흔들리지 않는다는 뜻이다. 조치: 소유자가 노드에서 모델을 받아 `~/.env.secrets` 의 경로를 바꾸고
  `SPEECHTOTEXT_DIARIZE_THRESHOLD` 를 그 모델에 맞춰 다시 잰다(임계값은 임베딩 모델에 붙은 값이다).
  표본이 녹음 하나이므로 확정이 아니라 권고다. 비교에 쓴 후보 3종(`campplus`·`eres2net_200k`·`titanet_large`)은 측정을 위해 **이미 노드 `/home/agent/sherpa-onnx/models/` 에 내려와 있다** — 경로만 바꾸면 되고, 쓰지 않기로 하면 그 파일들을 지우면 된다. **정확도 · 심각도 중** (증적 `docs/qa/PLQ1/summary.md`). [해소 2026-09-17: 소유자 지시("임베딩 모델 우선 상향해줘")로 노드 `~/.env.secrets` 를 `eres2net_200k` · `SPEECHTOTEXT_DIARIZE_THRESHOLD=0.8` 로 바꿨다. 근거는 소유자 기준점이 있는 라이프로그 두 건(272.5초=3명, 449.3초=4명)을 현재 가드(on=0.5/off=0.0)로 다시 잰 사다리 — `eres2net_base@1.0`(옛 프로덕션)은 4·2 로 오차 합 3, `eres2net_200k` 는 0.6·0.8 두 칸에서 3·4 로 오차 0, `campplus` 최선 2, `titanet_large` 최선 3. 프로덕션 경로(`resolve_toolchain(os.environ)`) 재실행이 같은 표본에서 4·2 → 3·4 로 바뀐 것을 확인했다. 후보 파일 4종은 노드에 그대로 둔다(재측정 재료). 증적 `docs/qa/PLE1/summary.md`. 남는 관측 항목: 593초 표본(소유자 기억 없음)은 base 3 · 200k 4 로 갈리고, FU6 64분 2인 녹음은 Drive 아카이브에 없어 새 모델로 재지 못했다 — 회의 경로는 `--speaker-count` 선언이 그대로 방어한다.]
  ↳ 재판정(2026-09-19, D290): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **whisper 전사 모델 상향 여부** — `SPEECHTOTEXT_WHISPER_MODEL` 이 가리키는 노드 설정이라 저장소가
  바꾸지 않는다. 순서는 (1) **고친 조립기로 한 건 다시 전사해 읽어 보기** — 소유자가 본 조각난 문장은
  모델이 아니라 조립기가 만든 것이었으므로(노드 실측 36/36 대 0/36) 그 원인이 사라진 전사본을 먼저
  읽어야 귀속이 가능하다, (2) 그래도 부족하면 `q5_0`→`q8_0`(같은 모델, 교체 한 번), (3) 그다음
  `large-v3-turbo`→`large-v3`(디코더 32층 증류본에서 원본으로, VRAM·시간 수 배). **두 후보 모두
  공표된 한국어 WER 차이는 없다** — 같은 오디오를 `--reprocess` 로 돌려 직접 비교해야 한다.
  **정확도 · 심각도 낮음** (근거: 공식 모델 카드·whisper.cpp 문서, `docs/qa/PLQ1/summary.md`).
  ↳ 재판정(2026-09-19, D291): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **Hermes 의 Claude(Anthropic) OAuth 채택 여부** — 소유자 질의(2026-09-06)에 대한 실측 답은 "쓸 수 있다"
  이다: 노드 hermes v0.20.3 의 `hermes_cli/auth_commands.py` 가
  `_OAUTH_CAPABLE_PROVIDERS = {"anthropic", "nous", "openai-codex", "xai-oauth", "qwen-oauth", "minimax-oauth"}`
  를 갖고 `provider == "anthropic"` 분기에서 `agent.anthropic_adapter.run_hermes_oauth_login_pure()` 로 PKCE
  로그인을 연다. 자격증명 소스도 둘이다 — hermes 자체 PKCE(`~/.hermes/.anthropic_oauth.json`)와 Claude Code
  재사용(`~/.claude/.credentials.json`). 현재 등록된 것은 `copilot`·`openai-codex` 뿐이고, 활성화는 노드에서
  `sudo -u agent hermes auth add anthropic --type oauth` 한 줄이다. 다만 **이번에 관측된 결함 셋 중 어느
  것도 모델 교체로 고쳐지지 않는다** — 요약 누락은 JSON 파싱과 빈 요약 처리, 전사 낱말 조각은 우리
  조립기, 화자 폭발은 임베딩 클러스터링이었다. 그리고 채택은 `configs/routing-policy.md` 의 **단일 티어
  불변식**(Codex OAuth 외 대체 경로 없음)을 바꾸는 정책 결정이라 코드보다 소유자 승인이 먼저다.
  조치: 요약·추출 품질을 더 올리고 싶다면 먼저 같은 티어 안에서 `AUTOPHAGY_CODEX_MODEL` 로 모델만 바꿔
  A/B 하고, 그래도 부족하면 그때 2차 티어를 정책과 함께 연다. **정책 결정 · 심각도 낮음**.

  ↳ 재판정(2026-09-19, D292): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## cytoplasm 신규 노드 설치 보고에서 드러난 공백 (2026-09-07)

> 원 묶음은 [follow-ups.md](follow-ups.md) 에 남아 있다(열린 것 1건: `LIVE_CHECKS` 선택화).
> 아래 6건은 2026-09-07 에 해소해 이리로 옮겼다 — 삭제가 아니라 이동이다.

- **공개 스냅샷에 특정 설치의 tailnet IP·프로덕션 호스트명이 남는다** → 값을 node config
  필드·자리표시자로 외부화하고, `automation/public_export_redaction.py:28` 의 규칙표에
  tailnet 대역(RFC 6598 CGNAT)·프로덕션 호스트명 패턴을 보는 **export fail-closed 검사**를
  신설한다. 대상은 `automation/healthcheck_registry.sh:12` ·
  `automation/credential_rotation/registry.py:121,135` ·
  `tests/e2e/drivers/w3_report_hub_ops.py:31` · `tests/unit/test_runtime_package_probe.py:210`
  이고, 같은 값을 가진 `docs/guide/*` 는 `configs/public-export-manifest.txt` 제외 목록에
  있어 공개되지 않는다. tailnet 전용 + Basic 인증이라 도달 가능한 서비스 노출은 아니지만
  `configs/inventory.md:4` 와 「개인화 코드 금지 규칙」(SC-2) 위반이다. 값만 지우면 같은
  방식으로 다시 들어오므로 검사가 본체다.
  **영향: 공개 배포본의 토폴로지 노출·규칙 위반, 런타임 무영향 · 심각도 중**.
  ↳ 처리(2026-09-07): 해소 — `public_export_redaction.assert_no_private_topology` 가 redaction 뒤 스냅샷 전체를 훑어 주소·호스트명이 남으면 export 를 세운다. 네 파일의 값은 노드 config 유도·env override·자리표시자로 바뀌었다. 범위는 실측(공개 1974파일에서 오탐 0)으로 정했다.

  ↳ 재판정(2026-09-19, D293): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **설치기가 `converge.d/automation/` 6파일을 설치하지 않아 첫 수렴이 SYNC-BLOCK 된다** →
  `automation/install/assets.py:219`~`233` 에 `__init__.py`·`git_tag_signature.py`·
  `update_trust.py`·`update_trust_state.py`·`node_config.py`·`node.example.toml` 을 더해
  `automation/provision-deploy-converge.sh:67`~`77` 과 같은 집합으로 맞춘다. 두 설치 경로가
  갈라져 있었고 기존 노드는 프로비저너 경로로 깔려 이 공백이 드러나지 않았다.
  **영향: 신규 설치 완주 불가(우회 없이는 릴리스 0건) · 심각도 높음**.
  ↳ 처리(2026-09-07): 해소 — `assets.py` 의 `CONVERGE_HELPERS` 가 프로비저너와 같은 9개를 놓고, `test_install_converge_parity.py` 가 두 설치 경로를 서로 대조한다.

  ↳ 재판정(2026-09-19, D294): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **설치기가 `/etc/autophagy/repair-approval.env` 를 만들지 않는데 유닛은 필수로 요구한다**
  → 설치기 자산에 빈 `root:ops 0640` 파일을 추가한다. 유닛을 `-` 선택으로 낮추지 않는다 —
  통지 자격증명 부재가 조용해지면 이 파일이 존재하는 이유와 정면으로 충돌한다
  (`automation/systemd/autophagy-deploy-reconcile.service:25`).
  **영향: 신규 설치의 EnableTimer 실패 · 심각도 높음**.
  ↳ 처리(2026-09-07): 해소 — 설치기가 키 이름을 적은 `root:ops 0640` 템플릿을 놓고, `FileSpec.create_only` 가 운영자 입력분을 다시 쓰지 않는다.

  ↳ 재판정(2026-09-19, D295): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **첫 설치에서 `_timer` 의 사전 `start` 가 아직 없는 릴리스 트리를 요구해 반드시 실패한다**
  → `automation/install/apply.py:263` 의 사전 `start` 를 제거하고 타이머 자기 틱에 맡긴다.
  유닛의 `WorkingDirectory`(`automation/systemd/autophagy-deploy-reconcile.service:12`)는 첫
  수렴이 만드는 디렉터리라 순환이다.
  **영향: 신규 설치의 EnableTimer 실패(위와 같은 지점) · 심각도 높음**.
  ↳ 처리(2026-09-07): 해소 — 사전 `start` 를 제거했다. 타이머 자기 틱이 첫 수렴을 수행한다.

  ↳ 재판정(2026-09-19, D296): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **재실행 시 이미 있는 저장소를 다시 clone 해 실패한다** →
  `automation/install/state.py:179` 의 `_repository_origin` 이 설치기 uid(root)로 git 을 돌려
  dubious-ownership 에 걸리고, 그 `None` 을 `automation/install/plan.py:298` 이 "저장소 없음"
  으로 오판해 `automation/install/apply.py:241`~`258` 의 맨 `clone` 에 도달한다. 조치는 둘 다 —
  판정은 `git -c safe.directory=<path>` 로 그 경로 하나에만 명시하고(선례
  `automation/release_provenance.py:63`), 적용은 존재 시 `remote set-url` 로 멱등하게 바꾼다.
  root 전역 `--system` 은 신뢰 범위를 그 경로 밖으로 넓히므로 택하지 않는다.
  **영향: 설치기 재실행 불가(첫 실행에는 드러나지 않음) · 심각도 중**.
  ↳ 처리(2026-09-07): 해소 — `_repository_origin` 이 그 경로 하나에만 `safe.directory` 를 명시하고(전역 아님), `_repository` 는 이미 있는 체크아웃을 `remote set-url` 로 수렴한다.

  ↳ 재판정(2026-09-19, D297): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`update-trust.pub` 배포 경로가 문서에만 있고 절차에 없다** →
  `docs/guide/manual-maintainer.md:245` 의 릴리스 컷에 asset 업로드 단계를 넣어
  `docs/guide/install.md:46`·`docs/guide/manual-member.md:25` 의 "릴리스에 동봉" 문구와 실제를
  맞춘다. 지문 대역외 대조 설계 자체는 의도대로 동작했고 키 파일 전달 경로만 비어 있었다.
  **영향: 신규 설치 진입 불가(`--update-trust-key` 는 필수 인자) · 심각도 중**.

근거: 각 불릿의 file:line (2026-09-07 현재 main `ee276a746` 기준 실측).
  ↳ 처리(2026-09-07): 해소 — `manual-maintainer.md` §1.7 에 `gh release upload` 단계와 asset 확인 명령을 넣어 문서와 실제를 맞췄다.

  ↳ 재판정(2026-09-19, D298): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`LIVE_CHECKS` 가 설치별로 조정 불가라 미설치 서비스가 상시 FAIL 한다** → 레지스트리가
  22개 행을 `core`·`report-hub`·`rag` 묶음으로 들고, 설치가 `HEALTHCHECK_SERVICES` 로 선언한
  묶음만 `LIVE_CHECKS` 로 내보낸다.
  ↳ 처리(2026-09-07): 해소 — 선언이 없으면 전부 유지(기존 설치 출력 바이트 동일, 22행),
  `HEALTHCHECK_SERVICES="core"` 는 22→14행, 모르는 이름은 exit 2 로 거부한다. 행 순서를
  보존해 허용목록 매니페스트·래퍼 입력 지문이 불변이다. 회귀는
  `tests/unit/test_healthcheck_registry.py`.

  ↳ 재판정(2026-09-19, D299): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 설치 마법사·프로필 착지 후 남긴 것 (2026-09-08)

> [이관 2026-09-08 · 해소] 설치기 착지 결과를 회계 원문과 함께 보존한다. RAG 원격 배포와 실호스트 관측은 아래 처리 상태를 따른다.

- **`rag`·`report-hub` 프로필은 헬스체크 선언만 정하고 RAG 스택·report-hub 유닛을 배치하지 않는다** →
  다음 단계로 `OPT_IN_COMPONENTS` 확장을 설계 판단한다(`automation/install/components.py:39–49`).
  **영향: 추가 서비스 배치는 별도 준비 · 동작은 정상 · 심각도 낮음**.
  ↳ 처리(2026-09-08): `report-hub`는 `OPT_IN_COMPONENTS`와 사용자 유닛 자산으로 설치되므로 해소했다. `rag`는 `automation/rag_stack/deploy.sh`가 별도 `RAG_NODE`에 배포하므로 주 노드 설치기 범위 밖의 OWNER/BLOCKED로 남긴다.
  ↳ 재판정(2026-09-19, D300): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **healthcheck SSH forced-command 래퍼는 여전히 owner-run 별도 절차다** →
  `automation/provision-healthcheck-probe.sh:2–6`의 절차를 설치 자산으로 편입할지 검토한다.
  **영향: 신규 노드의 `check healthcheck`가 `INFRA_FAILURE`로 FAIL할 수 있음 · 심각도 중**.
  ↳ 처리(2026-09-08): 설치기 `ProvisionHealthcheckProbe` 작업이 키·강제명령 래퍼·바인딩을 수렴하므로 해소했다. 기존 노드는 소유자가 설치기를 한 번 수동 실행해야 한다.
  ↳ 재판정(2026-09-19, D301): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **컨테이너 실제 설치 검증은 `hermes-gateway` 외부 전제에서 멈춘다** →
  실호스트 첫 완주 때 아래 QA 디렉터리에 증적을 추가한다(**OBSERVE**).
  **영향: Hermes·Discord 뒤 타이머·최종 healthcheck 완주 근거가 아직 없음 · 심각도 낮음**.
  ↳ 처리(2026-09-08): `--stub-hermes` 하네스의 상한은 `discord-readiness`다. Hermes와 토큰이 있는 실제 호스트에서만 그 경계 뒤의 완주를 관측한다(OBSERVE).

증적: [docs/qa/INSTALL-TUI/](qa/INSTALL-TUI/) · [systemd 하네스](../tests/e2e/install/systemd_container/README.md).

  ↳ 재판정(2026-09-19, D302): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 라이프로그 전사·화자 품질 교정 착지 후 남긴 것 (2026-09-06)

> [이관 2026-09-08 · 해소] 후속 과제 스윕 7 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **기본 임계값 1.35 는 회의에서 과병합 쪽으로 기운다** — PR #422 가 소유자 확인 64분 녹음(화자 2)으로
  고른 값이고 lifelog 에는 맞지만, 15분 실회의를 1.35 에서 화자 1명으로 묶는다(1.30=2, 1.10=9). 파편
  가드는 군집 수를 바꾸지 않으므로 그 보정 자체는 유효하다. 조치: 회의 경로에서는 `--speaker-count`
  를 쓰고(그것이 PR #422 문서와 이 문서가 같이 도달한 결론), 소유자가 화자 수를 아는 다화자 녹음이
  생기면 그 값으로 회의용 기본값을 별도로 잴지 판단한다. **정확도 · 심각도 중**
  (증적 `docs/qa/PLQ1/summary.md` §4, `docs/qa/FU6/diarize-threshold-validation.md`).
  ↳ 처리(2026-09-08): 해소 — 이 불릿의 전제가 낡았다. `skills/speechtotext/scripts/stt_diarize.py:32` 의 `DEFAULT_THRESHOLD` 는 2026-09-07 에 **1.0** 이 됐다(과분할은 `--clustering.num-clusters` 재클러스터·잔여 프룬으로 되돌릴 수 있으나 과병합은 화자가 문서에서 사라져 복구할 수 없다). 「회의 경로에서는 `--speaker-count` 를 쓴다」는 결론은 그대로 유효하며 1.35 기준의 재측정 요구는 소멸했다.

  ↳ 재판정(2026-09-19, D303): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`stt_window.text_of` 만 아직 세그먼트를 `" ".join` 으로 잇는다** — 반복 붕괴 검사 전용이라 문서에
  도달하지 않지만, 세그먼트가 이미 앞 공백을 갖고 오므로 이 경로만 조립 규칙이 다르다. 조치: 반복
  검사 입력을 문서와 같은 조립으로 통일할지 별도 판단(임계값 0.08 의 의미가 함께 바뀐다).
  **문서 영향 없음 · 심각도 낮음**.
  ↳ 처리(2026-09-08): 해소 — **통일하지 않는다(B)** 로 판정하고 그 이유를 `text_of` 주석에 못박았다. `text_of` 는 문서로 가지 않고 `stt_coverage.collapsed` 의 공백 기반 단어/8-gram 반복 검사에만 들어가므로 세그먼트마다 경계 하나가 필요하다 — 합성 실측(같은 문자열 160세그먼트)에서 한국어 무공백 입력은 문서 조립 0.00 / `text_of` 1.00 이라 A 로 바꾸면 한국어 반복이 검사에서 사라진다. 영어(앞 공백) 입력은 둘 다 1.00 이라 측정값이 같다. 임계값 0.08 은 그대로다. 회귀는 `tests/unit/test_stt_window.py`.

  ↳ 재판정(2026-09-19, D304): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 전사 정확도 문서 공개 검사 잔여 (2026-09-07)

> [이관 2026-09-08 · 해소] 후속 과제 스윕 7 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **기존 기능 소개 2곳에 계정 홈 절대 경로가 남아 전체 개인화 검사가 2건을 검출한다** →
  `docs/기능소개/대시보드-비밀번호-교체.md`의 자격증명 조회 예시와
  `docs/기능소개/제안서-노드-자율-구동.md`의 브라우저 예시를 설치별 자리표시자로 바꾼다.
  이번 lane은 신규 기능 소개만 쓰기 허용이라 기존 문서는 보존했다. 실제 비밀 값 검출은 아니며
  **영향: 기존 문서의 설치 종속 예시·전체 개인화 검사, 런타임 무영향 · 심각도 낮음**.

증적: `.omo/evidence/transcript-accuracy/task-21.md` (변경 전·후 동일 2건).
  ↳ 처리(2026-09-08): 해소 — 두 문서의 계정 홈 절대 경로를 저장소의 기존 관례(`<agent-home>`·`<ops-home>`, 근거 `docs/qa/RTS-5/03-node-residue.txt:6`)로 바꿨다. 변경 전 검출 3줄(대시보드 2 · 제안서 1) → 변경 후 0줄.

  ↳ 재판정(2026-09-19, D305): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## mailon 런타임 드리프트 프로브의 수렴 안내 (2026-09-07)

> [이관 2026-09-08 · 해소] 후속 과제 스윕 7 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **`mailon_runtime_drift.sh` 가 런타임이 릴리스 트리보다 *앞선* 창에서도 같은 DRIFT 문구로 "deploy.sh 를 돌려 수렴하라"고 안내한다 — 그 방향에서는 deploy 를 몇 번 돌려도 수렴하지 않는다(릴리스 트리는 서명 태그로만 전진한다)** →
  판정에 방향을 넣어, 런타임이 앞선 창이면 `automation/release.sh` 를 안내한다. 그 창은 "머지 직후 자기 변경을 배포"라는 가장 흔한 순서에서 열린다(2026-09-07 실측: runtime=78ee65a2 vs 릴리스 트리=d575b6de).
  **영향: 온디맨드 프로브의 안내 문구뿐 — healthcheck 레지스트리에 배선돼 있지 않아 반복 경보가 없고 런타임 동작과도 무관 · 심각도 낮음.**
  ↳ 처리(2026-09-08): 해소 — 프로브가 `git merge-base --is-ancestor` 로 방향을 판정해 런타임이 뒤처지면 기존 `deploy.sh`, 앞서면 `automation/release.sh`, 조상 관계를 확인할 수 없으면 방향을 단정하지 않고 둘 다 안내한다(fail-closed). 세 방향 회귀를 `tests/unit/test_mailon_runtime_drift.py` 에 추가했다(13 passed).

  ↳ 재판정(2026-09-19, D306): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 스킬 편집기 import 해석 복구로 드러난 타입 부채 (2026-09-07)

> [이관 2026-09-08 · 해소] 후속 과제 스윕 7 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **`automation.*` import 설정 공백을 메우며 Unknown에 가려졌던 기존 타입 오류 9건이 드러났다** →
  `pyrightconfig.json`의 19개 스킬 `extraPaths: ["."]` 확장은 유지하고, 아래 8개 파일은 스킬별 타입
  좁히기·정확한 시그니처로 별도 사이클에서 처리한다. 전사 감사 수리와 무관한 제품 파일은 이번 PR에서
  수정하지 않는다. `npx --yes basedpyright --outputjson skills`의 severity=error 총계는 **602→462**이며,
  새로 보인 진단은 `reportArgumentType` 7건 + `reportReturnType` 2건이다(줄 번호는 task-31 실측 기준).
  - `skills/budget/scripts/budget_approval.py:277` — `reportArgumentType` **1건**:
    `assert_never`에 전달하는 `Outcome`을 `Never`로 좁히지 못함 → 기존 분기 사실을 타입으로 표현한다.
    ↳ 재판정(2026-09-19, D308): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  - `skills/budget/scripts/budget_confirm.py:200` — `reportReturnType` **1건**:
    `str` 반환 자리에 `object` → 기존 문자열 보장 지점에서 반환 타입을 좁힌다.
    ↳ 재판정(2026-09-19, D309): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  - `skills/calendar/scripts/calendar_approval.py:285` — `reportArgumentType` **1건**:
    `Outcome` → `Never` 불일치 → 기존 분기 사실을 타입으로 표현한다.
    ↳ 재판정(2026-09-19, D310): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  - `skills/calendar/scripts/calendar_preflight.py:214,216` — `reportArgumentType` **2건**:
    `list[str]` → `JsonValue` 대입과 `dict[str, JsonValue]` → `Mapping[str, str | list[str]]` 인수 불일치 →
    실제 payload의 필드 타입과 `draft_sha256` 호출부 시그니처를 맞춘다.
    ↳ 재판정(2026-09-19, D311): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  - `skills/coordination/scripts/coordination_approval.py:274` — `reportArgumentType` **1건**:
    `Outcome` → `Never` 불일치 → 기존 분기 사실을 타입으로 표현한다.
    ↳ 재판정(2026-09-19, D312): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  - `skills/mail/scripts/triage_approval.py:407` — `reportArgumentType` **1건**:
    `Outcome` → `Never` 불일치 → 기존 분기 사실을 타입으로 표현한다.
    ↳ 재판정(2026-09-19, D313): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  - `skills/mail/scripts/triage_confirm.py:171` — `reportReturnType` **1건**:
    `str` 반환 자리에 `object` → 기존 문자열 보장 지점에서 반환 타입을 좁힌다.
    ↳ 재판정(2026-09-19, D314): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  - `skills/wiki/scripts/wiki_approval.py:293` — `reportArgumentType` **1건**:
    `Outcome` → `Never` 불일치 → 기존 분기 사실을 타입으로 표현한다.
  **영향 범위: 위 스킬의 편집기 정적 타입 진단뿐 · 심각도 낮음.** 보안·런타임 동작 문제가 아니라
  가려진 타입 부채라는 판정이다. 근거: 루트 `AGENTS.md`의 `pyrightconfig` 항목이 이 설정을
  편집기 전용이며 런타임·테스트·CI 동작과 무관하다고 선언하고, 이번 경로 확장은 위 8개 파일의
  실행 코드를 바꾸지 않았다. 설정을 되돌리면 Unknown으로 다시 숨길 뿐이므로 오류 억제·경로 복원으로
  처리하지 않는다. 후속 수리도 새 검증·예외 경로 없이 이미 참인 사실을 타입으로 표현한다.

증적: `.omo/evidence/transcript-accuracy/task-31.md` (B 결정·파일별 원문 진단·전체 회귀 출력).
  ↳ 처리(2026-09-08): 해소 — 지목된 9건이 진단에서 사라졌다. `Outcome`→`Never` 5건은 이미 참인 분기 사실을 타입으로 표현했고, `object`→`str` 2건은 기존 문자열 계약을 반환 경계에서 좁혔으며, calendar 2건은 실제 payload 의 JSON 값 타입(`JsonValue`)으로 시그니처를 맞췄다. `pyrightconfig.json` 되돌림·`# type: ignore`·`# noqa` 억제는 쓰지 않았고 런타임 동작은 불변이다(budget·calendar 347 passed, coordination·triage·wiki 375 passed).

    ↳ 재판정(2026-09-19, D315): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ 재판정(2026-09-19, D307): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 라이프로그 화자 분리 정정 착지 후 남긴 것 (2026-09-07)

> [이관 2026-09-08 · 해소] 후속 과제 스윕 7 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **재처리가 노트 이름을 바꿔 옛 노트를 고아로 남길 수 있다** — 노트 경로는 매 쓰기마다
  제목에서 새로 계산된다(`note.corrected_lifelog_note` → `lifelog_relpath`). 제목이 없던
  녹음을 `--reprocess` 하면 생성 제목이 붙어 파일 이름이 바뀌고, vault 의 옛 파일은 지워지지
  않은 채 남는다. 조치: 레코드에 이미 있는 `note_relpath` 를 재처리 경로에서 재사용해 첫
  경로에 못 박는다(`transcribe_promote`/`commit` 이음새). 영향: 데이터 손실은 없고 중복
  노트 1건이 생길 뿐이지만, 같은 녹음이 노트 둘로 갈라지면 RAG 인제스트가 둘 다 먹는다.
  심각도 중.
  ↳ 처리(2026-09-08): 해소 — `binding.finalize` 가 제목에서 경로를 다시 계산하지 않고 레코드의 `note_relpath` 를 재사용한다(발견 시 `sync.py` 가 고정한 첫 경로). 제목 없던 녹음을 `--reprocess` 해 생성 제목이 붙어도 노트 파일은 하나로 유지된다. 회귀 `tests/unit/test_plaud_sync_reprocess_note_path.py`(RED: 경로가 갈라짐 → GREEN), plaud 367 passed.

  ↳ 재판정(2026-09-19, D316): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 제안서 엔진 내제화 착지 후 남긴 것 (2026-09-08)

> [이관 2026-09-08 · 해소] 후속 과제 스윕 7 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **샌드박스 scenario 의 render 가 엔진 입력 계약에서 선다.** `scenario.sh` 의 가짜 draft 는 실제
  draft 가 쓰는 `drafts.json.planspec.json`·`.pms.json` 사이드카를 만들지 않아, render 가
  `refined drafts sidecar source is missing` 로 멈춘다. **회귀가 아니다** — 내제화 전에는 핀 검사가
  exit 4로 먼저 죽어 샌드박스가 render 를 한 번도 실행한 적이 없었고, 이제야 그 사실이 보인다.
  조치: 샌드박스 draft 단계가 사이드카를 함께 내도록 하면 scenario 가 렌더까지 완주한다. 영향 범위는
  샌드박스 스모크뿐이고 실제 렌더는 `.omo/evidence/docbot-internalization/` 의 실측으로 증명돼 있다.
  **영향: 샌드박스 E2E 의 render 단계 커버리지, 프로덕션 렌더 무영향 · 심각도 낮음**.
  ↳ 처리(2026-09-08): 해소 — `scenario.sh` 의 draft 단계가 실제 draft 와 **같은 writer**(`save_planspec`·`save_draft_file`·`_save_pms_snapshot`)로 `planspec.json`·`pms.json` 사이드카를 함께 낸다. 엔진 검증은 한 줄도 완화하지 않았고(`skills/proposal/engine/**` 무변경) 시나리오가 실제 HWPX 를 렌더해 `SCENARIO-PASS` 로 끝난다(coverage_score 1.0, refined=true).

  ↳ 재판정(2026-09-19, D317): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## v1.6.0 릴리스 착지 후 남긴 것 (2026-09-08)

> [이관 2026-09-08 · 해소] 후속 과제 스윕 7 — 원문은 당시 관측·제안이며 아래 처리 줄이 병합된 동작을 말한다.

- **소유자 ✅ 를 받은 릴리스 요청이 태그 전에 origin/main 이 전진하면 영구히 실행 불가가 되는데, `release.sh` 의 자동 회수는 `bound_pending` 만 다루고 완결 타이머는 매 틱 `RELEASE-DECISION: live request is bound to a different HEAD` 로 조용히 끝난다(2026-09-07 17:46 KST v1.6.0@2a0a20267 실측 — 승인 뒤 main 이 75커밋 전진, 다음 날 `release.sh` 는 `RELEASE-RETIRE-BLOCK: pending release does not match the latest signed head` 로 exit 4, 사람이 `release_approval_remote.sh abandon --version --head --message-id --reason` 을 돌려야 풀렸고 그동안 완결기는 18시간 동안 2분마다 같은 줄만 남겼다) → ① `release.sh` 가 시작 시 approved 이면서 head ≠ origin/main tip 인 레코드를 같은 감사형 abandon 으로 자가 회수하고 새 요청을 정확히 한 번 게시한다(새 요청은 옛 승인 범위의 상위집합이므로 재승인이 fail-closed 로 맞다), ② 완결 타이머는 같은 조건을 만나면 침묵하지 말고 소유자 통지를 에피소드당 1건 남긴다(매 틱 반복 금지). 인가 경계는 넓히지 않는다 — 옛 승인으로 새 tip 을 태그하는 경로는 만들지 않는다.** **영향: 릴리스 파이프라인 가용성(승인이 tip 전진보다 늦게 오는 모든 릴리스), 프로덕션 코드·노드 무영향 · 심각도 중**.
  ↳ 처리(2026-09-08): 해소 — `release.sh` 가 시작 시 `retire --head <base> --tip <tip>` 로 APPROVED 레코드를 검사해, 이미 태그된 base 는 기존 `release-history/` 로, base 도 현재 tip 도 아닌 미실행 승인은 **감사형 abandon** 으로 회수한 뒤 새 요청을 한 번 게시한다. 완결 타이머는 `decision --notify-stale` 로 같은 요청 신원당 소유자 통지 1건만 남긴다(마커 `release-stale-notified/<sha256(version,head,message_id)>`, 전송 실패는 마커 없이 다음 틱 재시도). 옛 ✅ 로 새 tip 을 태그하는 경로는 만들지 않았고 회귀가 미승인 tip 의 태그 호출 0 을 어서션한다.

  ↳ 재판정(2026-09-19, D318): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다; PR #508. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **감사형 abandon 은 Discord 를 건드리지 않으므로(`release_abandon` A3) 죽은 승인 카드가 채널에 그대로 남고, 카드 제목은 버전뿐이라 살아 있는 카드와 구분되지 않는다 — v1.6.0 은 같은 제목의 카드가 4장(`e638ea5cd`·`2a0a20267`·`21f673f7b`·`efb8dba9d`) 쌓였고 소유자가 어느 것을 눌러야 하는지 본문의 `배포 기준: <sha>` 로만 판별할 수 있었다(2026-09-08 소유자 질문) → abandon 이 원 메시지에 상태 회신 한 줄(예: `⛔ 만료 — <새 head> 로 재요청됨`)을 남기거나 카드 제목/본문에 접두어를 붙이도록 한다. 소유자 결정을 파괴하지 않는다는 A3 의 취지는 리액션을 지우지 않는 것이므로, 읽기 전용 표시 추가는 그 불변식과 충돌하지 않는다.** **영향: 승인 표면의 오독 위험(엉뚱한 카드에 ✅ → 아무 일도 일어나지 않아 대기가 길어진다), 실행 경로 무영향 · 심각도 낮음**.
  ↳ 처리(2026-09-08): 해소 — abandon 이 원 승인 메시지를 `message_reference` 로 가리키는 `⛔ 만료 — <사유>` 회신 한 줄을 best-effort 로 덧붙인다. 원 카드 본문·소유자 리액션은 건드리지 않아 A3 와 바이트 바인딩이 그대로 유지되고, 회신 실패는 `RELEASE-ABANDON-NOTICE-FAIL` 한 줄일 뿐 회수 종료코드·감사·archive 를 바꾸지 않는다. 이미 회수된 요청의 재실행은 회신을 중복 게시하지 않는다.

  ↳ 재판정(2026-09-19, D319): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 스윕 5 착지 후 남긴 것 (2026-09-08)

> [이관 2026-09-09 · 해소 3 + OBSERVE 1] 세 건은 PR #466·#467·#468 로 닫혔고, 프롬프트 v5 의 요약 산문 준수만
> 관측 조건이 아직 성립하지 않아 남는다. 회계 원문을 그대로 보존한다.

- **리컨실러가 converge 의 stderr 를 삼켜 배포 정지가 무증상이었다** — 노드가 08:44~22:40 약 8시간 동안 어떤 릴리스도
  설치하지 못했는데 저널에는 `mirror left-behind` 한 줄뿐이었고, 진짜 원인(`ModuleNotFoundError`)은 사람이 verifier 를
  손으로 돌려야 보였다 → converge 비영 종료 시 그 stderr 마지막 줄을 저널·healthcheck 티켓에 싣는다. **심각도 높음**.
  ↳ 처리(2026-09-09 · 해소): PR #466 — `run_converge` 가 실패한 helper 의 stderr 를 `[deploy-reconcile]` 마커로 400자까지 재발화한다. rc 0 과 lock-busy(5)는 2분 타이머 홍수를 막으려 침묵이고 반환 코드는 불변이다. 회귀 `tests/unit/test_deploy_reconcile_converge_stderr.py`(FS3 고정 파일을 피한 새 파일).
  ↳ 재판정(2026-09-19, D320): HISTORY-UNVERIFIED — 과거 완료 기록은 보존하되 현재 상태를 재검증했다고 주장하지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **공개 원장 3종의 역할 구분이 코드에만 있다** — baseline-additions(정당화)·manifest(제외)·review(공개)의 차이가
  테스트에만 강제되어 이번 사이클에서 `local_ci` 를 두 번(약 15분) 헛돌렸다 → `configs/public-export-review.txt` 헤더에
  "governed 새 파일 = 정확히 한 곳 + 경로 바이트순" 한 문단을 적는다. **심각도 중**.
  ↳ 처리(2026-09-09 · 해소): PR #468 — `public-export-review.txt`·`public-export-manifest.txt` 헤더가 manifest=제외 / review=공개 / baseline-additions=정당화, governed 는 `configs/`·`docs/` 뿐, 새 파일은 앞 둘 중 **정확히 한 곳** + 경로 바이트순 삽입임을 말한다. 기계 소비 문구 `sorted by path` 는 보존.
  ↳ 재판정(2026-09-19, D321): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **프롬프트 v5 의 요약 산문 준수는 미검증이다** — 결정론적 필터가 제목·파일명·사람·장소·담당자를 막지만 요약 본문은
  모델이 쓰므로 실녹음 1건으로만 확인된다 → v1.6.3 배포 후 첫 라이프로그 노트의 `## 요약` 을 확인하고 여전히 새면
  프롬프트를 조인다. **영향: 구조화 필드는 이미 안전 · 심각도 낮음**.
  ↳ 처리(2026-09-09 · **OBSERVE**): 노드 최신 plaud 노트가 2026-09-08 11:17 UTC 이고 v1.6.3 수렴은 13:40 UTC 라 **v5 프롬프트가 만든 노트가 아직 없다**. 요약 산문은 모델이 쓰므로 합성 입력으로는 준수를 증명할 수 없다. 되돌아오는 조건: v1.6.3 이후 첫 라이프로그 노트가 생기면 그 `## 요약` 을 읽고 판단한다.
  ↳ 재판정(2026-09-19, D322): NEEDS-EVIDENCE — 현재 판정에 필요한 원본·실측 증거가 부족하며 결함을 지어내지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **`lifelog_extract._reference()` 의 과잉 거부 여지** — 어절 전체가 친족어·일반명사인 경우만 떨어뜨리지만 첫 어절이
  친족어인 정당한 제목이 함께 떨어질 수 있다 → 노트 제목이 기본값(`녹음 (YYYY-MM-DD)`)으로 떨어지는 빈도를 관찰하고
  유의미하면 규칙표에 예외를 더한다. **영향: 폴백은 정보 손실이 아니라 보수적 기본값 · 심각도 낮음**.
  ↳ 처리(2026-09-09 · 해소): PR #467 — 규칙을 완화하지 않고(완화하면 규칙의 존재 이유인 `사람형님` 이 되살아난다) `reference_drops(raw)` 가 `ReferenceDrop(field, value)` 로 무엇이 떨어졌는지 데이터로 돌려주고, 효과 경계가 표본 5개·값 40자로 클립해 한 줄로 보고한다. `형님식당` 과잉 거부는 characterization 으로 **알려진 한계로 고정**했다 — 빈도가 쌓이면 그때 예외를 판단한다.
증적: 노드 실측은 ops 전용(`journalctl -u autophagy-deploy-reconcile`), 수리는 PR #462.

  ↳ 재판정(2026-09-19, D323): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 수리 티켓 유실 사고 수정 중 발견한 소유자 항목 (2026-09-09 · 소유자)

- **계정 홈의 죽은 수리 사본 `~/.hermes/repair/` 가 남아 있다** — 이번 사이클로 어느 실행 경로도 그것을
  부르지 않지만 디렉터리는 그대로다(7월 세대 코드 + `.pre-rro-*` 백업). 다음 사람이 현역으로 오인할 수
  있다. 제거는 노드의 파괴적 작업이라 소유자 몫이며, 릴리스 반영 뒤
  `automation/provision-healthcheck-probe.sh` 재실행과 함께 처리하면 된다. **영향 없음 · 혼동 위험 ·
  심각도 낮음**.

  ↳ 재판정(2026-09-19, D324): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 토큰 없이 설치 완주 착지 후 남긴 것 (2026-09-09)

> [이관 2026-09-09 · 해소] 2건 모두 같은 날 닫혔다. 아래 원문은 그때의 관측·제안이고 `↳ 처리` 줄이 실제로 병합된 동작을 말한다.

- **설치 시점에 판정하지 못한 Discord 전제를 이후에 다시 묻는 것이 없다** → 설치기는 이제
  「소유자 확인 절차」로 사람에게 넘기지만, 그 절차를 돌렸는지 아무도 되묻지 않는다.
  `automation/healthcheck_registry.sh` 의 `LIVE_CHECKS` 에는 Discord 전제 프로브가 없어
  (`grep -rn 'discord' automation/healthcheck*.sh` → 0건), 토큰이 끝내 설정되지 않은 노드는
  승인 카드가 도달하지 않는 채로 계속 `ALL_HEALTHY` 를 보고할 수 있다. 조치 후보는
  `discord_check.py` 를 읽기 전용 프로브로 `core` 묶음에 넣는 것인데, 그러면 healthcheck 가
  매 틱 Discord API 를 호출하므로 레이트리밋·주기를 먼저 정해야 한다.
  **영향: 설치 완주 노드의 승인 표면 미도달이 조용할 수 있음 · 동작은 정상 · 심각도 중**.
  ↳ 처리(2026-09-09): 해소 — **열린 질문에 답하는 대신 없앴다.** `automation/owner_notice_probe.sh` 의 `owner_notice_credentials` 는 네트워크를 부르지 않고 `/etc/autophagy/repair-approval.env` 하나만 읽는다. 매 틱 Discord API 를 부르면 Discord 장애가 수리 티켓이 되고(cry-wolf) 레이트리밋·주기라는 정책 질문이 새로 생기는데, 조용한 실패의 실제 모양은 "끝내 안 채웠다" 이고 그것은 그 파일로 판정된다. 선례 `peer_ignored_channels` 와 같은 성격(로컬·읽기 전용·설정 드리프트·fail-closed)이며 `core` 묶음·`LOCAL_PROBES`·`repair_guidance` 에 등록했다. **빈 파일은 여전히 정당한 구성**이므로(`신규-노드-설치-공백.md` §2-1) 프로브가 묻는 것은 「채웠는가」가 아니라 「결정했는가」다 — 채우거나 `OWNER_NOTICE_OPTIONAL=1` 로 선언하면 통과하고, 두 통과는 다른 문구를 낸다. 증명하지 못하는 것(토큰 유효성·인텐트·채널·DM 도달)은 소유자의 `discord_check.py` 가 닫는다. [소개](기능소개/소유자-통지-자격증명-프로브.md)
  ↳ 재판정(2026-09-20, D325): PARTIAL — 실제 GET 검사에서 토큰·인텐트·두 길드 권한·필수 채널 3종이 PASS였다. 소유자 종단 도달은 명시적 미검증 경고로 남으며 경고의 옛 owner-dm 명칭을 현행 승인 라우팅 증거로 쓰지 않는다.
- **`discord_check.main()` 의 argparse 실패는 `SystemExit` 로 탈출한다** → 설치기는 그것을
  같은 프로세스에서 부르는데(`automation/install/executor.py`), `RealExecutor.execute` 의
  `except` 는 `OSError`·`CalledProcessError`·`TrustKeyError` 만 잡는다. 지금은 argv 를 설치기가
  직접 조립하므로 도달하지 않지만, 값이 `-` 로 시작하는 config 경로 같은 입력이 생기면
  FAIL 결과 대신 트레이스백으로 죽는다. 조치는 그 호출을 `SystemExit` 까지 감싸는 것이다.
  **영향: 현재 도달 불가 · 동작은 정상 · 심각도 하**.
  ↳ 처리(2026-09-09): 해소 — `executor.py` 가 그 호출만 `except SystemExit` 로 감싸 `owner_actions.discord_usage_refused` 의 `USAGE-REFUSED` FAIL 을 낸다. **종료 상태를 판정표에 넣지 않은 것이 요점**이다: `SystemExit(2)` 와 토큰 부재의 rc=2 는 같은 숫자라, 그대로 매핑했으면 "설치기가 만든 명령이 틀렸다" 가 "소유자의 토큰이 없다"(WARN)로 둔갑해 설치가 거짓 이야기 위에서 계속 갔을 것이다. 회귀는 `tests/unit/test_install_token_absent.py -k usage` 가 그 오분류를 직접 금지한다.

증적: [docs/qa/OWNER-NOTICE-PROBE/01-probe-surface.txt](qa/OWNER-NOTICE-PROBE/01-probe-surface.txt).

  ↳ 재판정(2026-09-19, D326): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 소유자 통지 자격증명 프로브가 남긴 소유자 항목 (2026-09-09 · 소유자)

- **[OWNER] 이 릴리스를 받은 노드는 healthcheck 래퍼를 한 번 다시 만들어야 한다** → 프로브 행이
  하나 늘면 SSH 강제명령 허용목록 매니페스트가 한 줄 늘고(실측 `22a23`, 기존 22줄은 바이트 그대로)
  래퍼의 입력 지문이 바뀐다. 그전까지 `healthcheck probe allowlist matches the checks` 프로브가
  드리프트를 보고한다 — 이것은 결함이 아니라 **설계된 탐지**이고, 조치는 노드에서
  `automation/provision-healthcheck-probe.sh` 를 1회 실행하는 것이다(멱등, 같은 바이트면 무동작).
  같은 실행이 위 항목의 `~/.hermes/repair/` 정리와 함께 처리된다.
  **영향: 재프로비저닝 전까지 프로브 1건 FAIL · 감시 손실 없음 · 심각도 중**.


  ↳ 재판정(2026-09-19, D327): NODE-VERIFY — 노드·외부 상태 확인이 선행하며 현재 실측은 미완이다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
  ↳ 처리(2026-10-02 · 해소): 래퍼 재생성은 끝났다 — v1.14.4(`1fc11c72`)에서 두 노드 래퍼의 입력 지문이 현재 체크 목록에서 계산한 값과 같고(`WRAPPER-PASS`), `healthcheck probe allowlist matches the checks` 는 10-02 `T002501Z` 틱부터 두 노드 모두 PASS 다. `~/.hermes/repair/` 정리는 이 확인의 범위 밖이다. 근거는 [RCB-1 소유자 조치](qa/RCB-1/owner-actions.md) 「① 해소 확인」.
## 수리 티켓 유실 사고 수정 중 발견한 인접 결함 (2026-09-09)

> [이관 2026-09-09 · 해소] 설계 판단을 요구한 1건이 같은 날 닫혔다. 아래는 그때의 원문이고 `↳ 처리` 줄이 실제 결정이다.

- **게이트웨이 라이프사이클 다리가 배포는 됐으나 로드되지 않는다** — `automation/interop/hermes_hook.py` 는
  `~/.hermes/interop_runtime/automation/interop/` 에 실려 있지만 게이트웨이 플러그인
  (`~/.hermes/plugins/interop-protocol/__init__.py`)이 그것을 import 하지 않고, 그 런타임에는
  `automation/repair/` 자체가 없어 import 하면 `ModuleNotFoundError` 가 난다. 실측 근거: 보드의 수리 카드
  58장 중 `agent:<event_type>` 출처가 **0건**이다(전부 manual-repair·healthcheck·e2e). 조치에는 설계
  판단이 선행한다 — 훅을 실제로 등록하려면 `deploy-skill.sh` 의 interop 스테이징 목록에
  `automation/repair/*` 를 더해야 하고, 그러면 Hermes 라이프사이클 실패마다 카드가 열리므로 **어떤
  event_type 을 티켓으로 볼 것인가**를 먼저 정해야 한다(무제한이면 healthcheck 폭주와 같은 모양이 된다).
  **다른 두 detect 경로(수동·헬스체크)는 살아 있어 동작은 정상 · 관측 공백 · 심각도 중**.
  ↳ 처리(2026-09-09 · 해소): **켜지 않기로 결정**했다. 조사해 보니 붙일 훅이 없다 — `hermes_plugin.register()` 가 등록하는 것은 Hermes 가 실제로 부르는 여섯(`pre_gateway_dispatch`·`pre_tool_call`·`transform_llm_output`·`kanban_task_{claimed,completed,blocked}`)뿐이고 "에러를 동반한 라이프사이클 실패" 훅은 벤더에 없다. `hermes_hook.handle` 은 `agent:start` 같은 옛 이벤트 어휘로 쓰였고 어디에도 등록되지 않으며, 그 보고 절반은 `kanban_task_*` → `_send_kanban_report` 가 이미 같은 `TaskReport` 로 `#agents-log` 에 보낸다(즉 **대체된 선행 구현**이다). 티켓 절반은 지금 형태로 켜면 해롭다 — `record_lifecycle_failure` 의 `subprocess.run(timeout=90)` 이 async 훅 안에서 턴을 최대 90초 세우고, dedup 서명의 location 이 `task_id` 라 실패한 task 마다 새 카드가 열리며, 수리 카드가 blocked 로 생성되므로 `kanban_task_blocked` 에 걸면 자기 피드백 루프가 된다. 그래서 고아 `automation/interop/hermes_hook.py` 를 삭제하고 결정을 `automation/interop/AGENTS.md` 에 남겼다. 그 기능은 인프라 실패=healthcheck detect, 소유자가 눈치챈 실패=`!repair` 가 덮으며 둘 다 같은 날 릴리스 런타임 사본으로 고정됐다.

  ↳ 재판정(2026-09-19, D328): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 소유자 메시지 계약 착지 후 남긴 것 (2026-09-12)

- **[OBSERVE] 해시 감사의 유일한 EXCLUDED는 `automation/obsidian_write/gate_binding.py::request_approval`** — 자체 카드·probe 없이 주입 gate에 위임해 카드 안전성을 판정할 수 없다.
  → 구체 caller·게시 어댑터를 도입하는 때 그 probe·record·hash를 감사한 뒤 이관 여부를 재판정한다. 현재는 미이관 카드가 아니라 영구 위임 경계다.
  **영향: 미래 어댑터의 감사 누락 가능성, 현재 독립 카드 없음 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D329): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OBSERVE] 상태 CLI에 링크 정의 사본이 남는다** — `skills/plaud/scripts/plaud_cli.py::_thread_url`은 저장소 import가 없는 stdlib 샌드박스 때문에 인라인이다(`_LINK_LITERAL_ALLOWED`).
  → 공유 정의 변경 때 `test_plaud_skill_status.py`의 정상·결손·잘못된 좌표 동등성 검사를 함께 확인하고, 격리 배포 조건이 바뀌면 공유화한다. 현 동등성 핀이 있어 지금 무조건 import로 바꾸지 않는다.
  **영향: 길드·스레드 URL 정의의 향후 드리프트 위험 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D330): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OWNER] 수리 패치 포인터도 96자로 접힌다** — `repair_approval_render._field`의 `MAX_FIELD_CHARS=96`이 본문 대신 보여 주는 `patch_source_path`에 적용된다.
  → 소유자가 운영 경로의 길이 분포·접힘 빈도를 내용 없이 측정한 뒤, 필요하면 새 렌더 판본에서 완전한 포인터를 보존한다. todo 37은 골든의 임시 경로만 고쳤으며 이 작업도 운영 분포를 측정하지 않았다.
  **영향: 긴 경로일 때 소유자가 패치를 찾는 유일한 안내가 불완전할 수 있음 · 심각도 중**.
  ↳ 재판정(2026-09-19, D331): NEEDS-EVIDENCE — 현재 판정에 필요한 원본·실측 증거가 부족하며 결함을 지어내지 않는다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OBSERVE] 문법적 발신자 단서가 없는 동적 호출은 정적 검사의 경계다** — 계산된 `getattr`, `eval`/`exec`, descriptor·monkeypatch·미상 런타임 신원은 추적 시작점 자체가 없을 수 있다.
  → 이런 발신 경로를 도입할 때 사람 검토로 명시적 어댑터를 요구한다. `owner_message_sender_ast.py`의 선언된 한계이며, 단서가 있는데 `zip` 반환값에서 잃는 열린 결함과 혼동하지 않는다.
  **영향: AST CLEAN을 모든 Python 발신의 의미적 증명으로 읽을 수 없음 · 심각도 중**.
  ↳ 재판정(2026-09-19, D332): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[OBSERVE] 과거 pre-digest / unversioned release·deploy·publish·managed 카드는 여전히 frozen replay에 의존한다** — 이 네 경로는 렌더러가 실패하면 유효한 소유자 클릭도 거부한다. `content_sha256`를 가진 기록은 영향이 없다.
  → 실제 잔존 기록을 다룰 때 기존 검증 성공 뒤에만 digest 보정을 검토한다. 읽어온 미검증 본문을 바로 해시해 승인해서는 안 된다. 신규 기록은 digest를 저장한다.
  **영향: 과거 카드의 렌더러 가용성 의존, 신규 digest 카드와 구분 필요 · 심각도 낮음**.
  ↳ 재판정(2026-09-19, D333): STILL-OBSERVE — 원문에 명시된 관측 조건과 재개 기준을 유지한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **[해소·운영 참고] 릴리스 과대 카드 거부는 전파 예외 대신 exit 6이다** — `release_card.card_for_new_request`의 거부를 `release_approval.cmd_request`가 lifecycle 거부 코드로 돌려준다.
  → `release.sh` 운영자는 `RELEASE-CARD-REFUSED:`를 보고 요청 크기를 줄여 다시 요청한다. todo 25c 최종 게이트가 수용한 동작이며 실패를 성공으로 바꾼 것이 아니다.
  **영향: 릴리스 요청 실패의 운영 판독, 신규 게시·레코드·저널 없음 · 심각도 낮음**.

> [이관 2026-09-19] kanbanfix 통합 정산. 열린 원장에 남아 있던 이 묶음의 6건을 아래에 옮겼다. 본문은 원문 그대로이고, 각 불릿 끝의 `↳ 처리`·`↳ 이관` 줄이 현재 판정이다. 「해소」는 PR 머지까지를 뜻하며 배포는 아니다(프로덕션 `b8333c5c6`).

  ↳ 재판정(2026-09-19, D334): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **분할기가 두 벌이다** — `interop/chunker.chunk_message`는 URL 경계를 보존하지만 `release_spec.split_messages`는 줄·머리글 예산으로 나눈다.
  → 저장 레코드의 상세 재생·조각 수를 보존하는 공통 분할 계약을 먼저 정하고 통합을 검토한다. `release_notes.post_details`는 별도 분할기가 아니라 그 결과의 전송자다.
  **영향: 릴리스 상세와 일반 통지의 URL 절단·분할 정책이 달라질 수 있음 · 심각도 중**.
  ↳ 이관(2026-09-19 · BLOCKED → 소유자 결정): PR #502(fg1-splitter-unify, HEAD `c5fa00a15`) 는 **열려 있다**. 코드 쪽은 끝났다: 주 세션이 저장 카드 재생 회귀(2통이 3통이 되는 경계 URL, RED 3 failed / 5 passed)를 잡아 공통 primitive 에 URL 보존 정책을 두고 저장 v1~v4 는 옛 고정폭 분할, 새 v5 만 URL 보존을 고르게 했다(관련 344 passed, PR 체크 3 SUCCESS, `fg1-splitter-unify/.omo/evidence/kfx/fg1-splitter-unify/resume-report.md`). 그러나 그 브랜치는 chunker 를 스테이징하려고 **수정 동결된 `automation/deploy-skill.sh`** 의 `GATE_INTEROP_HELPERS` 한 줄을 바꾼다. 소유자의 명시적 해제가 없어 머지하지 않았다. 되돌아오는 조건: 그 한 줄의 동결 해제를 소유자가 승인하거나, 동결 파일을 건드리지 않는 스테이징 경로를 따로 정할 때.
- **지침 링크·앵커에 상시 가드가 없다** — 보드 A4는 `done.md`의 경로만 보고 앵커를 버리며, todo 28 검사기는 일회성 증적이다.
  → 루트 지침·가이드·소개 문서의 상대 링크와 앵커를 검사하는 저장소 가드를 추가하고 없는 경로·제목 변이로 실패를 증명한다.
  **영향: 낡은 지침 링크가 테스트 통과 뒤에도 남을 수 있음 · 심각도 중**.
  ↳ 처리(2026-09-19 · 해소): PR #500(fg2-doc-link-guard, 머지 `bb89e35d8`). `tests/unit/test_doc_links.py` 가 루트 지침 · 가이드 · 소개 트리의 상대 링크와 앵커를 전수 검사한다(`445a245d0`). 증적 `fg2-doc-link-guard/.omo/evidence/kfx/fg2-doc-link-guard/red.txt` → `green.txt`(6 passed).
- **발신자 그래프가 iterator 반환값에서 발신자를 잃는다** — `dict(zip((True, 1.0), (client.log, client.send_owner_dm)))[1]`은 실제 발신하지만 CLEAN이다.
  → `owner_message_sender_ast.py`의 iterator 허용과 `owner_message_sender_bindings.py`의 Call 결과 사이에 발신자 전파를 보존하거나, 전파를 증명 못 하면 거부한다. 표기별 예외 추가는 피한다.
  **영향: 명시적 발신자가 있어도 봉투 미채택을 놓치는 수정 가능한 검사 공백 · 심각도 중**.
  ↳ 처리(2026-09-19 · 해소): PR #514(fg3-sender-iterator, 머지 `f54e4aee4`). 발신자가 지나는 Subscript · `next`/`list`/`tuple` · iterator 언패킹에서 전파를 보존하고, 증명 못 하는 dict/zip 충돌은 거부한다. 증적 `fg3-sender-iterator/.omo/evidence/kfx/fg3-sender-iterator/implementation.md` · `import-surface.txt`(모호한 dict 거부, next/list 발신자 탐지); 주 세션 재실행 239 passed.
- **승인 lease가 원래 거부 예외를 TypeError로 바꾼다** — 생성기형 `FileKeyLease.hold`를 빠져나오는 frozen `SubmissionArtifactError`의 traceback 대입이 실패한다.
  → class 기반 context manager로 잠금·해제를 보존하고 실제 제출 거부 경로에서 원 예외·게시 0건·잠금 해제를 함께 검증한다.
  **영향: 과대 제출 카드 거부의 진단·오류 처리, 무승인 발송은 아님 · 심각도 중**.
  ↳ 처리(2026-09-19 · 해소): PR #504(fg4-lease-cm, 머지 `ac98be5be`). `FileKeyLease.hold` 가 class 기반 context manager 가 되어 frozen 예외의 traceback 대입이 더는 TypeError 로 바뀌지 않는다. 증적 `fg4-lease-cm/.omo/evidence/kfx/fg4-lease-cm/red.txt`(2 failed) → `green.txt`(2 passed); 주 세션이 importlib 로 실제 임계영역 안에서 frozen+slots 예외를 raise 해 같은 타입 · reason 보존과 같은 키 재획득을 확인했다.
- **승인 카드 3종이 봉투 뒤에 바인딩 footer 한 줄을 남긴다** — budget(`budget_core.py:250`)·calendar(`calendar_confirm.py:147`)·coordination(`coordination_lifecycle.py:137`)이 5필드 뒤에 `sha256`(budget 은 `draft id` 포함)을 덧붙여 6줄이 된다. 그 footer 는 이 계획보다 앞서 존재했고 승인 해시 바인딩이 의존한다.
  → 줄 수만을 이유로 카드 버전을 신설하지 않는다(이미 게시된 카드의 바이트를 바꾸면 소유자의 ✅ 가 소급 무효가 된다). 다른 이유로 카드 서식을 손댈 때 바인딩을 다섯 필드 안에 수용할 수 있는지 함께 검토한다.
  **영향: 표시상 군더더기뿐 — F3 실측으로 행동 필드 뒤에 오고 경쟁 지시를 만들지 않으며 승인 위치를 가리지 않는다(해시를 필드 안으로 옮기면 오히려 가독성 저하) · 심각도 낮음**.
  ↳ 이관(2026-09-19 · OBSERVE): 원문의 정책 그대로다. 줄 수만을 이유로 카드 버전을 신설하지 않는다(게시된 카드 바이트가 바뀌면 소유자 ✅ 가 소급 무효). 이번 통합에서 calendar 서식을 지나간 K4(PR #509)도 기존 footer 바이트를 보존하는 조건으로 진행됐다. 되돌아오는 조건: 다른 이유로 카드 서식을 손댈 때.
- **publish 카드가 기계용 wire 접두부로 11줄이 된다** — `skill_gate_specs.py:319`가 파싱 대상 접두부 뒤에 봉투를 잇는다. `_PUBLISH_BINDING`(`skill_gate_publish.py:34-38`)이 개행 구분 6줄 접두부를 요구해, F4 실측상 현재 11줄은 바인딩 일치·5줄 봉투 단독은 불일치·접두부를 5줄에 접어도 불일치다.
  → 「승인 흐름·게이트·POLICY_VERSION 불변」을 지키는 한 줄이지 않으므로, 게이트 파서 변경이 **독립적으로** 정당화될 때만 다룬다. peer attestation 은 publish 에 요구되지 않으므로 peer 의존은 근거가 아니다 — 게이트 파서 단독으로 성립한다.
  **영향: 소유자 가시 해악 미입증(F4) · 심각도 낮음**.
  ↳ 이관(2026-09-19 · OBSERVE): 원문의 판정 그대로다. 게이트 파서 변경이 독립적으로 정당화되지 않았고 소유자 가시 해악도 새로 관측되지 않아 손대지 않았다.

발신자 검사의 교훈: todo 38 초반의 모양 열거 대신, 현재는 알려진 발신자가 지나가는 모든 subscript에 하나의 값 기반 인증 조건을 적용한다.
불리언·실수·슬라이스·음수 인덱스의 누락을 남은 결함으로 다시 적지 않는다. 위 iterator 반환값 공백과
발신자 단서 자체가 없는 동적 경계는 다르며, 후자는 [보류 원장](follow-ups-deferred.md#소유자-메시지-계약-착지-후-남긴-것-2026-09-12)에 있다.
근거: [해시 감사](qa/OMUX/hash-binding-audit.md), [계약·파사드·분할기·검사 코드 대조와 재현 증적](../.omo/evidence/owner-message-ux/task-30.txt).

근거: [해시 감사 CSV](qa/OMUX/hash-binding-audit.csv), [코드 대조·현재 베이스 재현 증적](../.omo/evidence/owner-message-ux/task-30.txt).
분할기 통합·문서 링크 가드·iterator 전파·lease 오류·footer·publish 접두부 6건은 2026-09-19 에 위로 옮겨 왔다(각 불릿의 `↳` 줄이 처리).

## 캘린더 카드 누락 백스톱과 통지 라우팅 착지 후 남긴 것 (2026-09-10)

> [이관 2026-09-11 · 해소] 2026-09-10 에 남긴 2건이 다음 사이클에 둘 다 닫혔다. 아래는 그때의 원문이고 각 항목 끝의 `[해소 …]` 가 실제 처리다.

- **단위 테스트가 개발자 홈의 실제 캘린더 게이트에 초안을 쌓는다** → `create_draft` 를 부르면서
  `CALENDAR_GATE_DIR` 을 설정하지 않는 스위트(공유 픽스처 `tests/unit/approval_characterization_fixtures.py`
  포함 20개 파일)가 각 게이트의 기본 경로(`~/.hermes/*-gate`)에 쓴다. 실측: 이 워크스테이션의
  `~/.hermes/calendar-gate/drafts` 에 pending 초안 **398건**(노드는 executed 97·pending 1). 이번
  사이클에서는 **읽는 쪽**만 닫았다 — 카드 게시 백스톱이 그 초안들에 도달할 수 있게 되었으므로
  `test_calendar_confirm_reactions.py`·`test_calendar_approval_recovery_failures.py` 에
  `_isolated_gate` autouse 가드를 넣어 `list_drafts` 를 빈 목록으로 고정했다. **쓰는 쪽**은 공유
  픽스처와 budget·mail·wiki 계열까지 걸쳐 있어 별도 사이클이 필요하다.
  **영향: 프로덕션 무관 · 개발자 홈 오염 + 테스트가 로컬 상태에 의존 · 심각도 낮음**. [해소 2026-09-11: 쓰는 쪽을 닫았다. `tests/unit/conftest.py` 가 수집 전(import 시점, 모듈들이 import 시 `Path.home()` 을 평가하므로)에 HOME 을 `tempfile.mkdtemp(prefix="autophagy-unit-home-")` 로 바꾸고 `XDG_CONFIG_HOME`·`XDG_CACHE_HOME`·`XDG_DATA_HOME`·`XDG_STATE_HOME` 을 지우며 종료 시 그 디렉터리를 없앤다(자식 프로세스도 상속). `tests/unit/test_unit_home_isolation.py` 가 이를 고정한다(`--noconftest` 면 실패). 실측: HEAD 5e1239b7e 에서 일회용 HOME 으로 돈 전체 실행이 그 홈에 19개 파일을 썼고, conftest 뒤 같은 전체 실행(7944건 통과)은 0개(증적 `docs/qa/UNIT-HOME-0911/01-unit-home-isolation.txt`). 이미 쌓인 `~/.hermes` 잔재(이 워크스테이션 calendar-gate 427·wiki-gate 358·entity-preflight 2194 파일)는 소유자 데이터라 자동으로 지우지 않는다. 정리 절차는 [단위 테스트 홈 격리](기능소개/단위-테스트-홈-격리.md).]
  ↳ 재판정(2026-09-19, D335): OWNER-DECISION — 소유자의 내용·보존·권한 선택이 선행한다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
- **승인 리마인더 배달은 아직 소유자 DM 고정이다** → `confirm_reaction_watch._process_entries` 의
  `ReminderContext(deliver=lambda _channel_id, content: discord.send_owner_dm(content))` 가 넘겨받은
  `channel_id` 를 버리고 DM 으로 보낸다. 이번 지시("정리 통지를 `#notifications` 로")의 범위 밖이고,
  「요청별 승인 스레드 규칙」은 리마인더가 **그 요청의 스레드**에서 완결되기를 요구하므로 목적지가
  `#notifications` 인지 스레드인지는 소유자 판단이 필요하다 — 고르기 전에는 고치지 않는다.
  **영향: 리마인더가 스레드가 아닌 DM 으로 감 · 동작은 정상 · 심각도 낮음**. [해소 2026-09-11: 새 소유자 결정은 필요 없었다. 목적지는 기존 「요청별 승인 스레드 규칙」이 이미 정해 두었고(리마인더는 그 요청의 `#agent-chat` 스레드에서 완결), calendar 워처의 deliver 가 넘겨받은 `channel_id` 로 `DiscordApi.post_message` 를 부르도록 바꿔 그 규칙을 따르게 했다. 스레드 링크의 guild id 는 새 공용 헬퍼 `automation/interop/approval_reminder.py::channel_guild_resolver` 가 채우고(DM 승인은 `@me` 유지) todo·mail·repair 워처도 같은 리졸버를 쓴다(그쪽 스레드 카드 링크가 `@me` 였다). 증적: `docs/qa/REM-THREAD-0911/01-calendar-reminder-thread.txt` (전: DM open + `@me` 링크 / 후: 스레드로 POST 1건 `https://discord.com/channels/<guild>/<thread>/<card>`, DM open 0).]

  ↳ 재판정(2026-09-19, D336): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.
## 워킹트리 전량 커밋 착지 후 남긴 것 (2026-09-14)

> [이관 2026-09-14 · 해소] 같은 날 소유자 지시(「gitignore 에 넣으면 좋을 것들 점검하고 넣어줘」)로 닫혔다. 아래 원문은 그때의 관측·제안이고 `↳ 처리` 줄이 실제로 병합된 동작을 말한다.

- **`.omo/senpi-task/` 는 하네스가 매 턴 다시 쓰는 세션 장부인데 git 에 추적된다** → `00c4815b1` 이 기능 커밋과 함께
  21개 파일을 실은 뒤로 세션마다 워킹트리가 더러워진다(이번 사이클: 삭제 18 · 추가 95). `local_ci.sh` 는 이 경로를 dirty
  검사에서 빼두었지만, mass-ulw DAG 의 `runKey` 슬러그가 gitleaks `generic-api-key` 문턱을 넘어 `.gitleaksignore`
  fingerprint 3줄이 필요했고 긴 슬러그의 DAG 실행이 생길 때마다 같은 일이 반복된다.
  ↳ **조치**: `.gitignore` 에 `.omo/senpi-task/` 를 더하고 `git rm -r --cached` 로 추적을 끊을지(그때 `local_ci.sh` 의
  예외와 `.gitleaksignore` 3줄도 함께 걷어낸다), 아니면 지금처럼 이력으로 남길지는 설계 판단이라 소유자 결정 뒤 한
  커밋으로 처리한다. **영향: 동작 결함 아님 · 워킹트리 소음과 시크릿 스캔 오탐만 · 심각도 낮음**.
  ↳ 처리(2026-09-14): 해소 — `.gitignore` 에 `.omo/senpi-task/` 를 더하고 `git rm -r --cached` 로 98개 파일을 인덱스에서만 내렸다(디스크 사본 불변). 같은 점검에서 `.omo/run-continuation/` 도 2026-07-18 무시 규칙 이전에 추적된 89개가 인덱스에 남아 있어 함께 내렸고, 비어 있어 아직 보이지 않는 `.omo/thread-tools/`(팀 메일박스·영수증·lock) 와 도구가 만든 `*` .gitignore 에만 기대던 `.pytest_cache/`·`.ruff_cache/` 를 미리 올렸다. 제안과 달리 `local_ci.sh`·`release.sh` 의 exclude pathspec 은 **남긴다** — 무시된 뒤에는 중복이지만 해가 없고 `tests/unit/test_local_ci_push_gate.py` 가 그 예외를 고정한다. `.gitleaksignore` 3줄도 **남긴다** — `e0856361b` 가 그 blob 을 여전히 들고 있고 `public_export.sh` 는 `--all --full-history` 를 스캔하므로 지우면 다음 반출이 그 커밋에서 선다.
  ↳ 재판정(2026-09-19, D337): RESOLVED-SOURCE — 소스 수준 해소 근거를 확인했으며 노드 반영은 별도다. 개별 근거·미결 조건은 재판정 원장의 같은 ID에 보존한다.

## doctor 착지 후 남긴 소유자 항목 (2026-09-29 · 소유자)

> doctor 첫 운영 실측(`python3 -m automation.doctor`, 노드 agent·peer)이 드러낸 것. 전부 노드 설정·소유자 판단이라 이 저장소가 고칠 수 없다.

- **일일 비용 보고가 26회 연속 실패 중이다** — `send_cost_report.py` 는 `COST_REPORT_SOFT_CAP`(필수, USD)을 요구하는데 agent `~/.env.secrets` 에 없어 매일 `KeyError` 로 끝난다 → 소유자가 임계값을 정해 그 파일에 한 줄 더한다. **영향: 일일 지출 보고 중단 · 심각도 중**.
- **`mail-triage-repair-daily` 가 6회 연속 `drift_skip` 이다** — Hermes 가 "global inference config drifted since this job was created" 로 실행을 거부한다(2026-09-22 폴백 변경 뒤로 추정) → 설정 변경이 의도였는지 확인하고 그 잡을 다시 만든다(`hermes cron`). **영향: 메일 분류 보정 중단 · 심각도 중**. → **해소(2026-10-18)**: 그 잡은 모델을 쓰지 않는 no-agent 워처로 옮겨 drift_skip 대상이 아니다(N1).
  ↳ 재관측(2026-10-01, 릴리스 수렴 사각지대 웨이브 6): 2026-09-29 주 모델 변경 이후 Hermes 의 drift 보호가 계속 건너뛰어 관측 시점(2026-10-01) 8회 연속 skip 이었고, 그 잡은 모델이 고정(pin)돼 있지 않다. 소유자 결정은 둘 중 하나다 — `hermes cron edit <job> --provider <p> --model <m>` 로 고정하거나(이것은 모델을 코드·argv 에 적지 않는다는 단일 출처 규칙과 충돌한다), 현재 설정 아래에서 잡을 다시 만든다. 저장소 쪽 소유 결정은 `docs/follow-ups.md` 「릴리스 수렴 사각지대 해소 후 남긴 것」의 같은 잡 불릿이 맡는다.
- **peer 에는 폴백 모델이 없다** — 주 모델은 `custom:litellm` 이고 `fallback_providers` 가 비어 doctor 가 WARN 으로 남긴다 → 의도면 그대로 두고(첫 알람 1건 뒤 되풀이하지 않는다), 아니면 폴백을 선언·로그인한다. **영향: peer 응답 여유만 · 심각도 낮음**.
- **중앙 워처 매니페스트가 바뀌어 healthcheck 래퍼를 다시 설치해야 한다** — `configs/watcher-deploy-manifest.txt` 에 doctor-watch 두 줄이 더해져 래퍼 지문이 달라진다 → 릴리스 반영 뒤 노드 소유자가 `automation/provision-healthcheck-probe.sh` 를 1회 실행한다(`healthcheck_wrapper_current` 가 그전까지 알린다). **영향: 헬스체크 래퍼 드리프트 경고 · 심각도 낮음**.
- **알람의 실제 첫 발송은 릴리스 뒤에 확인한다** — 단위·봉투 렌더까지만 검증했다 → 배포 뒤 첫 정각 점검에서 `#notifications` 에 위 두 정기 작업 고장이 한 건으로 도착하는지 본다. **영향: 검증 공백 · 심각도 낮음**.

## 제안서 compose 중단 수리 후 남긴 것 (2026-09-29 · 동결·벤더)

> compose 재개 캐시([소개](기능소개/제안서-compose-중단-재개.md))로 이제 compose 를 다시 실행하면 이어서 끝낸다. 하지만 끊기는 원인 자체는 이 저장소가 지금 고칠 수 없다.

- **릴리스 수렴이 진행 중인 에이전트 작업을 끊는다** — 2분 리컨실러는 새 릴리스로 수렴하면 무조건 `autophagy-gateway-pair restart` 를 부른다. Hermes 는 종료할 때 도구 자식 프로세스를 죽이고(`killed 1 tool subprocess(es)`), 유닛도 `KillMode=mixed` 라 남은 것까지 정리된다. 턴은 끝났지만 백그라운드 도구가 도는 상태는 drain 이 세지 않는다(`active_at_start=0`) → 배포기 동결이 풀리면, 재시작 전에 agent 게이트웨이의 도구 자식 프로세스를 보고 유휴가 될 때까지(상한을 두고) 미루는 방안을 검토한다. **BLOCKED(배포기 수정 동결) · 영향: 수 분 넘게 도는 모든 백그라운드 도구 작업 · 심각도 중**.
- **재시작 뒤 세션이 스스로 이어가지 않는다** — Hermes 는 재시작 때 백그라운드 프로세스 추적과 `notify_on_complete` 약속을 잃어 완료 알림이 오지 않는다. 소유자가 "계속"이라고 말해야 compose 재실행이 일어난다 → Hermes upstream 에서 재시작 전후 백그라운드 작업 인계를 지원하면 채택한다. **BLOCKED(벤더) · 영향: 소유자 재지시 1회 · 심각도 낮음**.

## 공개 배포본 v1.14.6 반출 보류 (2026-10-06)

- **cytoplasm 이 v1.14.0 에 머물러 있다** — v1.14.6 반출이 내보낸 트리 단위 시험 1건(`test_rag_ingest_rules_declared` — HEAD 가 직전 공개 스냅샷이라 `deploy_tree.sh` 가 없었다)으로 `PUBLIC-EXPORT-BLOCK` 됐고, 시험 수정(PR #575)은 v1.14.6 태그 커밋에 없다. 소유자 결정(2026-10-06)으로 이번에는 건너뛴다 → 다음 정기 private 릴리스 태그가 잘리면 그 태그로 `public_export.sh` 를 돌리고 릴리스 노트·`update-trust.pub` 까지 게시한다(manual-maintainer §1.4~1.7). 다른 소스를 이미 잘린 버전 이름으로 내보내지 않는다. **영향: 신규 설치·공개 사용자가 v1.14.0 기준 · 운영 노드 무영향 · 심각도 낮음**.
  ↳ 처리(2026-10-07 · 해소): private v1.14.9(6728a40a) 태그로 `public_export.sh` 를 돌려 cytoplasm 을 반출했다 — 내보낸 트리 11072 passed, gitleaks 0건(카나리아 선행), 태그 자체 검증 Good, `PUBLIC-EXPORT-OK commit=f304d016`. 릴리스 노트·`update-trust.pub` 게시(v1.14.9 Latest). 증적: 릴리스 호스트 `~/release-logs/export-1149.log`.
증적: 반출 로그 `~/release-logs/export-1146.log`(릴리스 호스트), PR #575.

## peer attestation signed 전환 후 남긴 것 (2026-10-06)

- **signed 모드의 전체 사슬(답글 없는 서명 → ✅ → MOUNT)을 아직 실측하지 못했다** — 2026-10-06 실측에서 두 결함을 고쳤다(PR #579 승인 채널 id, #580 서명 모드 답글). 그런데 실측에 쓴 hello-autophagy는 digest가 live와 같아, ✅ 뒤 워처가 `retire-done (already-realized)`로 MOUNT 없이 종결했다. #580은 노드 릴리스 런타임에서 돌기 때문에 v1.14.8 반영 뒤에야 효과가 난다 → v1.14.8 반영 뒤 다음 실제 스킬 배포에서 `#approvals`에 `[skill-attest]` 글이 없고 ✅ 뒤 live 심링크가 새 digest를 가리키는지 본다. 더 빨리 보려면 데모 스킬을 한 줄 바꾼 뒤 `deploy-skill.sh hello-autophagy --request-only`를 실행하고 카드 1장에 ✅를 받는다. **영향: 승인 채널 글 소음·검증 공백 · 되돌리기는 릴리스 호스트 `node.toml` 한 줄 · 심각도 낮음**.
  ↳ 처리(2026-10-07 · 해소): 데모 변경(PR #582, digest 25f24e45)을 담은 v1.14.9 릴리스 승인 경로에서 `PEER-ATTEST-PASS (signed stdout record captured)` → `RELEASE-AUTHORIZED` → `INSTALLED`, live 가 10:32:05Z 부터 `25f24e45…`, #approvals 의 `[skill-attest]` 0건. 같은 변경을 소유자 ✅ 한 카드별 요청은 노드 재개가 `release … is not the origin/main tip`(exit 4)로 막혔다 — 릴리스에 안 들어간 변경은 카드별 ✅ 재개로 MOUNT 될 수 없다. 증적: 릴리스 호스트 `~/release-logs/release-1149.log`, 노드 `autophagy-supply-chain-watch` 저널.
증적: 릴리스 호스트 `~/release-logs/hello-signed-20261006{,-b}.log`·`release-1148.log`, 노드 `autophagy-supply-chain-watch` 저널(2026-10-06 14:55Z).

## 공개 우선 전환 준비 중 남긴 것 (2026-10-07)

- **릴리스 노트 게시가 다시 손 절차다** — 공개 우선 전환 뒤 릴리스는 `automation/release.sh` 가 cytoplasm 커밋에 서명 태그를 다는 것으로 끝나고, 옛 `public_export.sh` 가 성공 직후 찍던 `PUBLIC-EXPORT-NOTE-PENDING` 안내(노트·`update-trust.pub` 자산·지문 대조 명령)는 `release.sh` 에 없다. 그래서 manual-maintainer §1.7 을 기억하는 사람만 노트를 올린다(2026-09-09 v1.6.7 에서 실제로 잊혔다) → `release.sh` 완결 단계 끝에 같은 안내를 버전·저장소까지 채워 찍게 하고, 회귀 테스트로 고정한다. 자동 게시까지 할지는 서명키가 있는 워크스테이션 전제와 함께 판단한다. **영향: 노트를 빠뜨리면 신규 설치가 신뢰키 자산을 못 받음 · 기존 노드 무영향 · 심각도 중**.
  > ↳ 2026-10-09 해소 — `release.sh` 가 태그와 같은 단계에서 `ensure_release_note` 로 Release 노트·`update-trust.pub` 를 게시하고, 실패는 exit 11, 직전 태그의 노트 누락은 `RELEASE-NOTE-MISSING` 으로 막는다(`tests/unit/test_release_note_gate.py`·`test_release_sh.py`). v1.16.0·v1.17.0 노트도 보충했다.

- **수리 push 의 누출 게이트가 노드에서는 금칙어 없이 돈다** — `RepairWorkClone.push_branch` 는 `public_gate range` 를 돌리지만 노드에는 ops 저장소가 없어 금칙어 목록이 없고 경로·토폴로지 검사만 돈다(`PUBLIC-GATE-DENYLIST-UNSET`). 실명·과제명이 섞인 수리 패치는 막지 못한다 → 금칙어 목록 사본을 노드 비공개 루트(`/srv/autophagy-private/`)에 두고 수리 유닛 환경에 `PUBLIC_GATE_DENYLIST` 를 선언한다(목록 갱신 경로 포함). **영향: 노드 관찰 유래 패치의 실명 누출 가능성 · 심각도 중(노드 전환 F 전에 닫을 것)**.
  ↳ 처리(2026-10-07 · 해소): 노드 비공개 루트에 금칙어 목록 사본을 두고 수리 유닛 환경에 `PUBLIC_GATE_DENYLIST` 를 선언했다. 실제 id 를 담은 시험 커밋이 수리 push 경로에서 REFUSED 되는 것을 확인했다. 목록 갱신은 아직 손으로 한다(운영 저장소의 목록을 고치면 CI 비밀과 노드 사본을 함께 갱신).
