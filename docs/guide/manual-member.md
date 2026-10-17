# 팀원 매뉴얼 — 그룹에 참여해 내 개인 에이전트를 쓴다 (W-M2)

독자는 **연구그룹에 참여하지만 자기 노드·자기 봇·자기 데이터로 자기 에이전트를
돌리는 연구원**이다. 관리자가 주는 것은 스킬과 신뢰 근거뿐이고, 설치·승인·데이터는
전부 내 것이다.

이 문서는 설치 **이후**를 소유한다. 설치 절차 자체는
[docs/guide/install.md](install.md) 하나가 소유하므로 여기서 다시 적지 않는다.

한 줄 요약: **내 설치에서 일어나는 모든 코드 마운트와 모든 외부효과는 내 ✅ 없이는
일어나지 않는다.** 관리형 스킬도 예외가 아니다(§5).

---

## 1. 사전 준비

준비물의 정본은 [docs/guide/third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)다.
먼저 그 문서 §6의 체크리스트 9항을 통과시킨다. 요약하면 다섯 가지다.

| 항목 | 비고 |
|---|---|
| Discord 앱·봇 1개 + 서버 2개 + 채널 3종 | 채널 3종은 `#agents-log`·인터롭 채널·개인 서버 `#approvals`. 소유자 전용 승인(메일·예산·캘린더 등)은 채널이 아니라 **봇 DM**이라 따로 만들 것이 없다 (§1.5) |
| 모델 provider 엔드포인트 | LiteLLM 호환 `/v1` |
| Hermes 게이트웨이 | **설치기가 설치하지 않는다.** 계정별로 미리 설치·기동 (§3의 버전 핀) |
| 업데이트 신뢰키 번들 | 릴리스에 동봉. 지문은 공개 저장소 공지값과 대조 |
| Linux + **systemd** 호스트, root 권한 | 리컨실러·워처가 systemd 타이머다. 컨테이너에서는 완주할 수 없다 |

그룹 가입에 필요한 것(관리자 서명키 지문·roster)은 여기가 아니라 §3에서 다룬다.
설치는 그것 없이도 완주하며, 가입은 나중에 붙여도 된다.

---

## 2. 설치, 그리고 "끝났다"의 판정

절차는 [install.md](install.md)를 그대로 따른다. 이 문서는 그 명령을 복사하지
않는다 — 같은 절차를 두 문서가 설명하면 반드시 한쪽이 낡기 때문이다.

팀원이 특히 주의할 두 가지만 짚는다.

- `--config`를 **항상 명시한다.** 생략하면 예제(= 다른 사람의 프로덕션) 기본값으로
  계획이 만들어진다.
- `require_signed_updates`는 **생략하거나 `true`.** 예제 파일의 `false`는 다른
  노드의 전환기 opt-out이며, 서명되지 않은 `main`을 root 입력으로 신뢰한다는 뜻이다.

관리형 스킬 자동 수신(§5)을 쓰려면 설치 시 컴포넌트를 **이름으로 요청**해야 한다.
대지 않으면 파일도 타이머도 만들어지지 않는다(비활성이 아니라 아예 없다).

```
--with-component managed-sync
```

나중에 붙여도 된다. 설치기는 멱등이라 같은 명령에 이 옵션만 더해 다시 실행하면
없는 것만 생긴다.

### 끝났다는 판정

install.md §7이 정본이고, 판정 줄은 세 개다.

```
[PASS] healthcheck: healthcheck.sh ALL_HEALTHY
[PASS] trust-key.fingerprint: 공지 지문과 일치 — SHA256:...
--- INSTALLED: N건 중 실패 0 / 경고 0
```

`INSTALLED` + 실패 0 = rc 0이면 끝이다. **경고(WARN)는 rc를 바꾸지 않는다** — 대표적인
것이 "지문을 사람이 직접 대조하라"이며, 기계가 대신 판단할 수 없는 항목이라 그렇다.
경고를 봤으면 그 항목은 손으로 닫는다.

자동 업데이트가 켜졌는지는 타이머로 본다.

```bash
systemctl list-timers | grep autophagy
```

`autophagy-deploy-reconcile.timer`가 보이면 리컨실러가 살아 있다.
`--with-component managed-sync`를 줬다면 `autophagy-managed-sync.timer`도 함께 보인다.

---

## 3. 그룹 가입 (3단계 핸드셰이크)

가입은 "관리자의 서명 공개키를 내 노드가 신뢰하게 만드는 것"이 전부다. 내 데이터나
승인 권한을 넘기는 절차가 아니다.

### ① 내 배포 공개키를 관리자에게 제출한다

설치기의 `deploy-key-registration` 체크가 정확히 그 줄을 출력한다.

```
[PASS] deploy-key-registration:
GROUP-JOIN-DEPLOY-PUBLIC-KEY access=read-only
send this exact public-key line to your group admin:
ssh-ed25519 AAAA... ops@<node>-autophagy-deploy
fingerprint=SHA256:...
share/compare fingerprints out-of-band
GROUP-DISCORD-FORBIDDEN: never use the group Discord channel
the private key stays on this installation
```

출력에 적힌 그대로다. **공개키 한 줄만** 보내고, 등록은 **read-only**로 요청한다.
개인키는 내 설치 밖으로 나가지 않는다. 나중에 다시 볼 일이 있으면 파일에서 읽으면 된다:

```bash
sudo cat /home/ops/.ssh/id_ed25519.pub
```

### ② 관리자 서명키 지문을 대역외로 받아 대조한다

관리자는 두 가지를 준다: roster 파일(`roster.yaml`)과 **서명 공개키 지문**
(`SHA256:...`).

> ⚠️ **지문을 그룹 Discord 채널로 받지 않는다.** 그 채널을 장악한 자가 곧 발행자가
> 된다. 대면·전화·이미 신뢰하는 별도 경로로 받는다. 코드도 같은 말을 한다 —
> `GROUP-DISCORD-FORBIDDEN`.

roster 파일 자체는 아무 경로로 받아도 된다. 그 안의 서명키가 진짜인지를 판정하는
것은 파일이 아니라 **대역외 지문**이기 때문이다.

### ③ 신뢰를 설치한다

두 옵션은 반드시 함께 준다. 하나만 주면 설치기가 거부한다.

```bash
sudo python3 -m automation.install \
    --config /tmp/node.toml \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:<업스트림-공지-지문>' \
    --group-roster /path/to/roster.yaml \
    --expect-group-skill-fingerprint 'SHA256:<관리자에게-대역외로-받은-지문>'
```

- 지문이 다르면 **아무것도 쓰기 전에** 멈춘다: `GROUP-TRUST-FINGERPRINT-MISMATCH`.
- `--group-roster` 없이 지문만 주면 `GROUP-ROSTER-REQUIRED`.
- roster만 주고 지문을 빼면 `GROUP-TRUST-FINGERPRINT-REQUIRED`.

성공하면 `/etc/autophagy/managed-skills-allowed-signers`(root:root 0644)에 한 줄이
설치된다. 이 파일이 그룹 스킬과 roster 서명을 검증하는 근거다.

> 신뢰키는 두 개가 아니라 두 **종류**다. **업데이트 신뢰키**
> (`/etc/autophagy/update-allowed-signers`)는 업스트림 소프트웨어 릴리스를 검증하고,
> **그룹 서명키**(`/etc/autophagy/managed-skills-allowed-signers`)는 그룹 관리자가
> 발행한 스킬을 검증한다. 소프트웨어는 그룹 관리자가 주지 않는다. 설치기는 한쪽을
> 다른 쪽 경로에 쓰는 것을 거부한다.

### ④ 런타임 roster 배치 (관리형 스킬을 받을 경우)

구독 런타임은 "누가 발행할 수 있는가"를 roster에서 읽는다. 관리자에게 받은 roster를
**agent 계정 홈**에 둔다.

```bash
sudo runuser -u agent -- install -m 600 /path/to/roster.yaml /home/agent/.hermes/roster.yaml
```

기본 경로는 `~/.hermes/roster.yaml`이고 `AUTOPHAGY_ROSTER`로 덮어쓸 수 있다.
**기본 principal은 없다** — 파일이 없거나 깨졌으면 sync가 아무도 신뢰하지 않고
exit 2로 멈춘다(이미 마운트된 스킬은 그대로다).

구독 설정 시드는 `configs/managed-sync.default.json`이고 런타임 경로는
`~/.hermes/managed-sync/config.json`이다. `remote_url`(관리자의 스킬 저장소),
`publisher`, 받고 싶은 스킬의 `opt_in`을 채운다. 알 수 없는 키는 조용히 무시되지 않고
exit 2로 거부된다.

첫 수신이 되는지는 손으로 한 번 돌려 확인한다(fetch·검증·격리까지만 한다 — 마운트는
하지 않는다).

```bash
sudo runuser -u agent -- python3 -m automation.managed_sync status
sudo runuser -u agent -- python3 -m automation.managed_sync sync
```

---

## 4. 일상 사용 — 승인 게이트 읽는 법

에이전트가 **바깥 세계를 바꾸려 할 때마다** 초안을 만들어 승인을 요청한다. 나는
읽고 이모지 하나를 누른다.

| 이모지 | 뜻 |
|---|---|
| ✅ (`:white_check_mark:`) | 승인 / 확정 / 실행 |
| ⛔ (`:no_entry:`) | 거부 / 취소 |

규칙은 전 기능 공통이다.

- **봇이 두 이모지를 미리 붙여 둔다.** 나는 탭 한 번만 한다.
- **내 리액션만 유효하다.** 봇이나 다른 사람의 리액션은 무시된다.
- **⛔이 우선이다.** 둘 다 붙어 있으면 취소로 처리한다(외부효과 fail-safe).
- **해시 바인딩 + fail-closed.** 승인은 내가 본 그 초안의 sha256에 묶인다. 승인 뒤
  내용이 바뀌면 실행되지 않는다. 확인할 수 없으면 실행하지 않는다.
- 텍스트로 `실행/취소 <id>`를 답장하는 것은 하위호환 폴백일 뿐이다. 에이전트는
  "실행 <id>라고 답장하라"고 요구하지 않는다 — 그런 요구를 받으면 의심한다.

### 승인이 필요한 것 / 그냥 되는 것

| 그냥 된다 (읽기·초안) | 내 ✅가 필요하다 (외부효과·코드) |
|---|---|
| 내 RAG 검색(`recall`), 위키 읽기 | 메일 발송, 캘린더 쓰기, 드라이브 발행 |
| 메일·문서 읽기와 요약 | 예산 집행, 할 일 등록 |
| 초안 작성, 분류, 제안 | 그룹 조율(peer와의 일정 확정) |
| 상태 조회·헬스체크 | **모든 스킬 마운트** — 개인·관리형 가리지 않는다 |

승인 표면은 둘로 갈린다. **소유자 전용 승인은 봇과 나의 DM**이고,
**스킬 공급망 승인(배포·attestation·관리형 활성화)은 개인 서버 `#approvals`**다.
여기와 아래의 `#approvals`·`#notifications`는 예시 이름이다. 승인 채널 ID는
`personal_approvals_channel_id`(명시적 배포 핀은 `deploy_approvals_channel_id`),
통지 채널 ID는 `owner_notice_channel_id`로 정한다. 같은 ID를 유지하면 Discord에서
이름을 바꿀 수 있다. 설정 없는 레거시 승인 조회만 이름 검색을 사용한다.
후자가 채널인 이유는 내 설치의 두 번째 주체(peer 봇)가 같은 메시지를 봐야 하기
때문이며, 그래서 DM으로 옮길 수 없다.

같은 논리적 요청에 대해 승인 메시지는 **하나만** 존재한다. 중복 게시나 조용한
덮어쓰기는 구조적으로 막혀 있으므로, 같은 요청이 두 번 보이면 그건 정상이 아니다.

---

## 5. 관리형 스킬이 도착했을 때

> ### 내 ✅ 없이는 어떤 관리형 스킬도 절대 동작하지 않는다.
>
> 관리자는 스킬을 **발행**할 수 있을 뿐, 내 노드에 **활성화**할 수 없다. 자동 배달과
> 자동 마운트는 다른 것이고, 이 시스템에는 후자가 없다. 이건 설정이 아니라 구조다 —
> 동기화 워처는 마운트 명령을 호출할 능력 자체가 없고, 그 사실이 테스트로 고정되어
> 있다.

배달되면 이런 일이 일어난다.

1. 타이머가 관리자 저장소를 폴링한다(기본 30분).
2. 릴리스의 **서명**을 `/etc/autophagy/managed-skills-allowed-signers`로 검증한다.
   서명·principal·시퀀스 중 하나라도 어긋나면 거부하고 사유를 로그에 남긴다.
3. 통과한 것만 **격리(quarantine)** 에 놓인다: `~/.hermes/managed-sync/quarantine/`.
4. 격리된 릴리스가 새로 생긴 틱에만 알림 1건이 온다.
   (거부는 매 틱 반복되므로 DM하지 않고 저널에만 남긴다.)

이 시점에 `readlink /srv/autophagy-skills/live/managed-<name>`은 **변하지 않았다.**
직접 확인할 수 있다. 이것이 D3가 코드로 지켜지고 있다는 증거다.

### 내가 활성화하기로 결정했다면

명령을 손으로 짜지 않는다. 정확한 명령을 시스템이 출력해 준다.

```bash
python3 -m automation.managed_sync activate-instructions managed-<name>
```

출력은 한 줄이다.

```
automation/deploy-skill.sh managed-<name> --activate-managed <격리-릴리스-경로>
```

그 명령을 실행하면 4단계 게이트(샌드박스 → 리뷰 → 요청+peer attestation → 마운트)를
거치고, `#approvals`에 승인 요청이 올라온다. **내가 ✅을 누른 뒤에야** 마운트된다.
누른 뒤 `readlink`가 새 digest로 바뀌면 끝이다.

- 이름 충돌(같은 base 이름의 일반 스킬이 이미 live)은 `COLLISION-BLOCK`으로 양방향
  차단된다. 우선순위는 없고, 내가 하나를 `--remove`해야 한다.
- 관리자가 릴리스를 취소(revoke)해도 **이미 마운트된 스킬은 자동으로 떨어지지 않는다.**
  대신 제거 요청이 표시되고, 실행 여부는 내가 정한다:
  `automation/deploy-skill.sh <skill> --remove`.

---

## 6. 내 개인 스킬 만들기

내가 만든 스킬은 **내 설치에만 있다.** 내가 명시적으로 제출한 고정 snapshot을 제외하면
관리자도 다른 팀원도 볼 수 없고, 업스트림
업데이트로 지워지지 않는다. 업스트림 릴리스는 저장소 트리를 수렴시킬 뿐이고, 개인
스킬은 저장소 밖(`~/.hermes/personal-skills/`)의 별도 git repo에 살기 때문이다.

### ① 저작 루트

```bash
mkdir -p ~/.hermes/personal-skills/<name>
cd ~/.hermes/personal-skills/<name>
git init
```

`git init`을 잊어도 된다 — `--personal`이 그 디렉터리가 자기 git top-level이 아니면
초기화해 준다. 이름은 `^[a-z0-9][a-z0-9-]{1,40}$`이고 **`managed-` 접두사는 예약어라
쓸 수 없다**:

```
[deploy-skill] ERROR: MANAGED-BLOCK: personal skills cannot use the reserved managed- prefix
```

### ② 최소 구조

```
~/.hermes/personal-skills/<name>/
├── SKILL.md            # 필수. frontmatter + 본문
└── scripts/
    └── scenario.sh     # 필수. 샌드박스 검증 시나리오
```

`SKILL.md` frontmatter의 `name`은 디렉터리명과 정확히 일치해야 한다.
`scenario.sh`는 `env -i HOME=… PATH=/usr/bin:/bin AUTOPHAGY_DEMO_SECRET=DUMMY-…`
환경에서 실행되며, 성공 시 stdout에 `SCENARIO-PASS`를 출력하고 exit 0이어야 한다.
형식과 로컬 재현 방법의 정본은 [docs/guide/스킬-제작.md](스킬-제작.md)다.
참조 구현은 `skills/hello-autophagy/`.

### ③ 커밋한다 (배포 전제)

개인 스킬의 provenance는 원격이 아니라 **내 repo의 깨끗한 커밋**이다. 확인은
세 가지고, 셋 다 배포를 막는다(실측):

```
DEPLOY-BLOCK: personal repository HEAD is not a committed branch tip
DEPLOY-BLOCK: personal repository worktree has uncommitted changes
DEPLOY-BLOCK: personal repository contains untracked files — commit them before deploying
```

마지막 것이 중요하다. `.gitignore`된 잔여물은 배포 입력이 아니므로 허용되지만,
**추적되지 않은 진짜 파일은 거부된다** — 커밋을 우회해 코드가 실려 나가는 경로를
남기지 않기 위해서다.

```bash
git add -A && git commit -m "add <name> skill"
```

깨끗하면 이렇게 나온다:

```
[deploy-provenance] OK: personal repository is clean at HEAD <sha>
```

### ④ 배포

```bash
automation/deploy-skill.sh --personal <name>
```

일반 스킬과 **같은 4단계 게이트**를 거친다. 내 설치의 소유자는 나이므로 승인도 내가
한다 — `#approvals`에 요청이 올라오고 ✅을 누르면 마운트된다. 배포되는 바이트는
워킹트리가 아니라 **승인된 HEAD 커밋의 `git archive`**이고, 마운트 직전에 HEAD가
그대로인지 다시 확인한다. 승인 뒤 코드를 고치면 그 배포는 통과하지 못한다.

유용한 플래그:

| 플래그 | 용도 |
|---|---|
| `--sandbox-only` | 승인 요청 없이 샌드박스만 돌려본다 |
| `--request-only` | 승인 요청만 올린다 |
| `--approve-only` | 승인 여부만 확인하고 마운트하지 않는다 |
| `--remove` | 이 스킬을 내 설치에서 내린다 |

`--personal`과 `--activate-managed`는 함께 쓸 수 없고, `SKILL_SRC_DIR`로 소스를
바꿔치기하는 것도 거부된다.

### ⑤ 확인

```bash
readlink /srv/autophagy-skills/live/<name>
```

바뀐 digest가 보이면 끝이다. **"커밋됨 ≠ 배포됨"** — 판정은 언제나 이 심링크다.

### ⑥ 그룹 검토에 제출 (선택)

그룹에 유용한 스킬이라면 live repo를 공유하지 않고, 깨끗한 한 커밋의 snapshot만
관리자에게 제안할 수 있다. 릴리스 설명 파일은 관리형 채널의 기존 changelog 형식이다.

```bash
python3 -m automation.managed_skills.submission_cli \
    --personal <name> --skill managed-<name> \
    --release-metadata <release.json> \
    --discord-token-file <0600 토큰파일>
```

명령은 `personal_provenance_check`를 그대로 실행하므로 dirty·untracked 상태에서는 첨부를
만들지도 게시하지도 않는다. 성공하면 sha가 고정된 tarball과 `ManagedManifest` v1이 기존
공급망 승인 표면에 올라간다. **이것은 검토 요청일 뿐이다.** 내 노드나 관리자 repo에는
아무것도 자동 import되지 않으며, 관리자가 검토 후 자기 워크스테이션에서 기존
`publish_cli`를 직접 실행해야만 발행 절차가 시작된다. 정확한 제출·검토 명령은
관리형 스킬 채널 가이드 §4가 단독 소유한다.

---

## 7. 팀 지식이 내 노트로 흘러오는 흐름

팀에서 오간 것 중 일부가 **내 개인 RAG**에 적재되고, `recall`로 검색될 때 출처가
함께 표시된다. 적재되는 것은 두 종류다.

| `source_type` | 출처 | `recall` 표기 |
|---|---|---|
| `peer-report` | 동료 에이전트가 `#agents-log`에 올린 보고 | `동료 보고: #agents-log 메시지 <id> (task <id>)` |
| `team-chat` | 팀 채널 대화 | `팀 채팅: #<channel> <id>` |

검색은 평소대로 한다.

```bash
python3 skills/recall/scripts/recall_cli.py search "<질문>"
```

### 정확히 무엇이 아닌지

- **자동 승격은 없다.** 팀에서 온 내용이 내 위키나 canonical 노트로 저절로 올라가지
  않는다. 그것은 의도적으로 만들지 않았다. 내 노트에 남기고 싶으면 내가 그렇게
  지시해야 하고, 그 저장은 평소의 저장 흐름을 그대로 탄다.
- **출처는 지워지지 않는다.** 팀에서 온 문장은 항상 팀 출처로 표시된다. 내가 쓴
  것처럼 섞이지 않는다.
- **사칭은 적재 전에 거부된다.** 실제 Discord 작성자 ID를 roster로 해석해
  보고의 `agent_id`와 exact-match할 때만 적재한다. 다른 사람의 신원을 주장하는
  보고는 warning을 남기고 **0건 적재**된다. roster가 없거나 깨졌으면 이 경로는
  fail-closed로 멈춘다.
- `recall`이 아무것도 못 찾으면 에이전트는 "기억 없음"이라고 답해야 하고, 지어내지
  않는다.

---

## 8. 내 것은 내 것

### 관리자가 **할 수 없는** 것

| 할 수 없다 | 왜 |
|---|---|
| 내 노드에 스킬을 **활성화** | 마운트는 내 4단계 게이트 + 내 ✅를 거친다(§5). 관리자는 발행만 한다 |
| 내 외부효과를 **대리 승인** | 승인 리액션은 내 Discord 계정만 유효하다. 관리자는 내 승인 표면에 존재하지 않는다 |
| 내 데이터를 **보기** | 내 RAG·노트·메일·대화는 내 노드와 내 계정에 있다. roster에는 이름·Discord ID·노드 라벨만 있고 데이터 경로는 없다 |
| 이미 마운트된 스킬을 **원격 회수** | 제거는 내 노드의 내 명령으로만 일어난다. 취소는 "제거 요청"까지가 끝이다 |
| 내 **소프트웨어**를 바꾸기 | 코드 업데이트는 업스트림 채널이고 별도 신뢰키를 쓴다. 그룹 관리자는 그 채널에 없다 |

### 관리자가 **할 수 있는** 것

- 그룹 스킬 저장소에 릴리스를 발행·취소한다(내 격리까지 도달, 그 이상은 못 간다).
- roster에서 내 등록을 `active` ↔ `removed`로 바꾼다.
- 저장소 provider에서 내 배포 공개키 등록을 폐기한다.

### 탈퇴하면 어떻게 되는가

관리자가 나를 제거하면 roster에서 내 항목이 `status: removed`가 되고, 저장소에서
내 read-only deploy key 등록이 폐기된다. 내 노드에서 실제로 관측되는 변화는 이렇다.

| 그대로다 | 멈춘다 |
|---|---|
| **이미 마운트된 관리형 스킬** — 계속 동작한다 | 새 릴리스 fetch가 SSH 인증 실패로 거부된다 |
| **내 개인 스킬** — 애초에 그룹과 무관하다 | 서명된 roster 갱신 수신 |
| 내 데이터·노트·RAG·대화 기록 전부 | |
| 내 소프트웨어 업데이트(업스트림 채널) | |

즉 **탈퇴는 공급이 끊기는 것이지 회수가 아니다.** 원격 회수는 이 시스템에 존재하지
않는 능력이다. 그룹 스킬을 더 쓰고 싶지 않다면 내가 직접 내린다:

```bash
automation/deploy-skill.sh managed-<name> --remove
```

`/etc/autophagy/managed-skills-allowed-signers`도 지우면 그 관리자의 서명은 더 이상
검증 근거가 되지 않는다.

---

## 9. 문제가 생겼을 때 — 확인 순서

**원인을 확인하기 전에 재시작·설정변경·키 재발급을 하지 않는다.** 순서대로 본다.

### ⓪ 연결·승인 점검 (읽기 전용)

```bash
cd /srv/autophagy-agent-current && python3 -m automation.doctor
```

에이전트가 답하지 않거나 승인 카드가 오지 않으면 먼저 이것을 본다. 모델 로그인 만료·봇 토큰·승인 채널·
Google 동의·반복 실패하는 정기 작업을 `[FAIL]` 로 짚고 조치 절차를 낸다([연결·승인 점검](연결-승인-점검.md)).
같은 문제는 매시간 점검에서 바뀔 때만 통지로도 온다.

### ① 헬스체크 (읽기 전용)

```bash
sudo runuser -u ops -- bash /srv/autophagy-agents/automation/healthcheck.sh
```

- 마지막 줄이 `ALL_HEALTHY`, rc 0이면 정상이다.
- 실패는 `FAIL <이름>`으로 어느 프로브인지 지목하고 마지막이 `HEALTHCHECK_FAILED`, rc 1이다.
- **원격 프로브가 전부 실패하면** `INFRA_FAILURE` 한 건으로 묶인다. 서비스 N개가 아니라
  공유 SSH 경로 하나를 의심한다.

### ② 신뢰키

```bash
python3 automation/install/trust_key_bootstrap.py verify --expect-fingerprint 'SHA256:<공지된-지문>'
```

`TRUST-KEY-MISSING`이면 리컨실러가 **어떤 릴리스도 적용하지 않는** 상태다.

### ③ 타이머와 유닛 로그

```bash
systemctl list-timers | grep autophagy
sudo journalctl -u autophagy-deploy-reconcile.service -n 50 --no-pager
sudo journalctl -u autophagy-managed-sync.service -n 50 --no-pager
```

관리형 스킬이 안 온다면 두 번째 저널이 답을 갖고 있다. 검증 실패는 **매 틱** 사유와
함께 여기 남는다(DM은 오지 않는다 — 매 틱 반복되므로 그 자체가 홍수가 된다).
`CONFIG-ERROR ... exit 2`면 roster나 `config.json`이 없거나 깨진 것이다(§3④).

### ④ 상태와 로그 위치

| 무엇 | 어디 |
|---|---|
| 관리형 동기화 상태·격리 | `~/.hermes/managed-sync/state.json` · `~/.hermes/managed-sync/quarantine/` |
| 무엇이 실제로 마운트됐는가 | `readlink /srv/autophagy-skills/live/<skill>` |
| `recall` 요청 로그(마스킹, 0600) | `~/.hermes/recall/logs/` (`RECALL_LOG_DIR`) |
| 승인 감사 로그 | `configs/env.example`의 `*_APPROVAL_LOG` 계열 — 스킬 배포는 `APPROVAL_LOG_PATH`, 외부효과는 `EXTERNAL_EFFECT_APPROVAL_LOG`, 캘린더·예산·할일·메일분류·수리도 각각 있다 |

로그 경로의 정본은 [`configs/env.example`](../../configs/env.example)이다 — 내
설치에서 어디로 설정했는지는 그 파일을 채운 값이 말해 준다.

### ⑤ 자주 막히는 곳

| 증상 | 원인 | 조치 |
|---|---|---|
| `GROUP-TRUST-FINGERPRINT-MISMATCH` | roster의 서명키 ≠ 대역외 지문 | **진행하지 않는다.** 관리자에게 대역외로 재확인 |
| `CONFIG-ERROR ... exit 2` (sync) | roster 또는 `config.json` 부재·손상 | §3④. 라이브 스킬은 영향받지 않는다 |
| `COLLISION-BLOCK` | 관리형과 일반 스킬 이름 충돌 | 우선순위 없음. 하나를 `--remove` |
| `DEPLOY-BLOCK: personal repository …` | 개인 repo가 dirty·미커밋·untracked | 커밋한다(§6③) |
| `MANAGED-BLOCK: … reserved managed- prefix` | 개인 스킬에 `managed-` 사용 | 다른 이름 |
| 배포 exit 9 | 내가 ⛔을 눌렀다 | 재시도하지 않는다 |
| 승인 눌렀는데 실행 안 됨 | 승인 뒤 내용이 바뀌어 해시가 어긋났다 | 새 요청을 받는다 |

설치 자체가 막히는 증상은 [install.md §8](install.md)이 소유한다.

---

## 관련

- 전제: [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)
- 설치 절차(정본): [install.md](install.md)
- 그룹 관리자 쪽 시점: [manual-group-admin.md](manual-group-admin.md)
- 스킬 형식·시나리오 계약: [스킬-제작.md](스킬-제작.md)
- 관리형 스킬 채널: managed-skill-channel.md
- Discord 토폴로지: discord-server-architecture.md
- 환경변수 정본: [`configs/env.example`](../../configs/env.example)
