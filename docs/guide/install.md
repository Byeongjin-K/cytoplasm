# 설치 가이드 (제3자 단일 노드)

이 문서는 **자기 노드에 이 시스템을 처음 설치하는 사람**을 위한 것이다. 위에서
아래로 순서대로 따라가면 끝난다. 설치 절차의 단일 진실은 이 문서이며, 다른 문서는
이 문서를 링크할 뿐 같은 명령을 다시 적지 않는다.

읽기 전에 알아둘 것 하나: **설치기는 멱등이다.** 중간에 막히면 원인을 고치고
같은 명령을 다시 실행하면 된다. 이미 원하는 상태인 항목은 계획에서 빠지고, 남은
것만 실행된다. 그래서 "한 번에 완주"를 목표로 삼을 필요가 없다.

---

## 0. 전제

전제는 이 문서가 소유하지 않는다. 먼저 읽고 준비한다:

**[docs/guide/third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)**

거기서 준비하는 것은 5종이다 — Discord 앱·봇·서버·채널 3종, 모델 provider(LiteLLM
호환 `/v1`), Hermes 게이트웨이(검증된 버전 핀), 업데이트 신뢰키 번들, 시크릿.
그 문서 §6의 체크리스트 9항을 통과한 뒤에 이 문서로 돌아온다.

이 문서가 추가로 요구하는 것:

| 항목 | 왜 |
|---|---|
| Linux + **systemd** 호스트, root 권한 | 리컨실러·스모크·공급망 워처가 systemd 타이머다. 계정 linger도 systemd 기능이다 |
| `git`, `curl`, `ssh-keygen`, `useradd`/`groupadd`/`usermod`, `visudo`, `tar` | 설치기가 직접 호출한다. 대부분의 배포판 기본 이미지에 있지만 slim 이미지에는 없다 |
| origin 호스트의 SSH 호스트키가 ops 계정 `known_hosts`에 있을 것 | clone은 `StrictHostKeyChecking=yes`로 수행된다(§4 참조) |
| 각 서비스 계정의 Hermes 게이트웨이 | **설치기는 Hermes를 설치하지 않는다.** 외부 전제이며, 없으면 fail-closed로 멈추고 안내한다 |

systemd 없는 slim 컨테이너의 경계는 `docs/qa/P0-5/03-systemd-boundary.txt`에 있다.
[특권 systemd 컨테이너 하네스](../../tests/e2e/install/systemd_container/README.md)는
그 경계를 넘어 실제 계정·linger·디렉터리·peer 키까지 확인하지만, Hermes 외부 전제에서
멈춘다. 최종 설치 완주의 증명은 아니다(§9).

---

## 1. 소스 받기

설치기는 체크아웃 안에서 실행된다(자기 위치 기준으로 저장소 루트를 찾는다).

```bash
git clone <공개 저장소 URL> ~/autophagy-agents
cd ~/autophagy-agents
```

릴리스 번들에는 **업데이트 신뢰키 공개키**가 동봉되어 있다. 그 경로를 기억해 둔다
(아래에서 `<bundle>/update-trust.pub`으로 부른다).

---

## 2. 신뢰키 지문을 먼저 대조한다

이 단계가 이 설치에서 보안적으로 가장 중요한 사람 개입이다. 키는 설치기와 함께
오므로, 그 키가 진짜 유지보수자의 것인지는 **설치기가 아닌 경로**로 확인해야 한다.

```bash
python3 automation/install/trust_key_bootstrap.py fingerprint --key <bundle>/update-trust.pub
```

출력은 `SHA256:...` 한 줄이다. 이 값을 **공개 저장소 README와 릴리스 노트에 공지된
지문**과 눈으로 대조한다. 다르면 여기서 멈추고 유지보수자에게 확인한다.

쓰기 없이 무엇이 설치될지 먼저 보고 싶다면:

```bash
python3 automation/install/trust_key_bootstrap.py install --key <bundle>/update-trust.pub --dry-run
```

공지 지문을 인자로 넘기면 대조를 기계가 한다. 불일치면 **아무것도 쓰지 않고** rc 1이다:

```bash
python3 automation/install/trust_key_bootstrap.py install \
    --key <bundle>/update-trust.pub --dry-run \
    --expect-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang'
```

> 신뢰키는 두 개가 아니라 두 **종류**다. 여기서 다루는 것은 업스트림 코드 릴리스를
> 검증하는 **업데이트 신뢰키**이고, 그룹 관리형 스킬을 검증하는 **그룹 서명키**는
> 다른 파일(`/etc/autophagy/managed-skills-allowed-signers`)에 들어간다. 설치기는
> 한쪽을 다른 쪽 경로에 쓰는 것을 거부한다. 자세한 구분은 전제 문서 §4.2.

---

## 3. 노드 config 작성

처음이라면 [빠른 시작의 설치 마법사](quickstart-install.md#4-실행할-한-줄)가 프로필·origin URL·
호스트 이름·운영자 계정·공지 지문을 묻고 `~/.config/autophagy/node.toml`을 대신 작성한다.
`--config PATH`로 위치를 바꿀 수 있고, 파일이 이미 있으면 그대로 재사용한다
(`automation/install/wizard.py:142–218`). 아래처럼 **수동 편집해도 유효하다**.
마법사가 만든 파일로 아래 명령을 직접 실행한다면 `/tmp/node.toml` 대신 그 경로를 넣는다.

```bash
cp configs/node.example.toml /tmp/node.toml
$EDITOR /tmp/node.toml
```

`configs/node.example.toml`은 **cha의 프로덕션 값을 그대로 적어 둔 worked example**이다.
복사한 뒤 최소한 다음을 자기 값으로 바꾼다:

| 필드 | 바꿔야 하는 이유 |
|---|---|
| `origin_url` | 자기가 업데이트를 받아올 공개 저장소 |
| `require_signed_updates` | **생략하거나 `true`.** 예제의 `false`는 cha 노드의 전환기 opt-out이다. `false`는 변조 가능한 `main`을 root 입력으로 신뢰한다는 뜻이다 |
| `peer_attest_mode` | 신규 설치는 `signed`. (설치기는 이 값을 `signed`로 강제한다) |
| `primary_node_name` / `rag_node_name` | 자기 호스트 이름 |
| `deploy_ssh_host` | 워크스테이션에서 원격 배포하지 않는다면 빈 문자열 |
| `operator_account` | 자기 로그인 계정 |

경로·계정 필드는 기본값 그대로 두는 것을 권한다. 단, `release_store`와
`release_current`의 basename은 바꿀 수 없다 — 다른 조합을 주면 설치기가
`InstallAssetError`로 거부한다(조용히 잘못 설치하지 않는다).

작성한 파일은 설치기가 각 계정 홈의 `~/.hermes/node.toml`(0600)로 배포한다.

### 3.1 기존 `discord` 설치를 `signed`로 옮기기

`peer_attest_mode = "discord"`로 돌던 설치에서는 스킬 배포 때마다 peer 봇이 `#approvals`에
`[skill-attest] … verdict=PASS` 답글을 남긴다. `signed`로 옮기면 그 답글이 사라지고, peer는
서명한 증명을 배포 프로세스 stdout으로만 넘긴다.

1. **노드 준비를 먼저 확인한다.** 설치기를 한 번 다시 돌리면 `peer-attest-key` 단계가 peer 키를
   만들고 공개키를 게시한다. 이미 있으면 다시 만들지 않는다. 확인할 것: `~peer/.ssh/peer_attest_ed25519`가
   peer 소유 0600이고, `/etc/autophagy/peer-attest-<peer 계정>.pub`와 그 부모 디렉터리가 `root:root`이며
   agent가 쓸 수 없어야 한다(공개키 0644). `ssh-keygen -lf`로 두 공개키의 지문이 같은지도 본다.
   이 조건이 하나라도 어긋나면 게이트는 fail-closed로 거부한다.
2. **배포를 실행하는 머신의 설정을 바꾼다.** `deploy-skill.sh`·`deploy_all.sh`는 그 스크립트를 돌리는
   머신의 설정에서 모드를 읽는다. 노드에서는 `/etc/autophagy/node.toml`이 있으면 그것을, 없으면
   `~/.hermes/node.toml`을 읽는다. 원격 배포 워크스테이션(릴리스 호스트 포함)에도 같은 값을 맞춘다.
   **노드만 바꾸고 워크스테이션을 두면 discord 답글이 계속 나온다.** 확인 명령:
   `python3 automation/node_config_sh.py --print-env | grep PEER_ATTEST_MODE`.
3. **진행 중인 스킬 배포 요청을 정리한 뒤 바꾼다.** 스킬 요청의 승인 해시는 모드에 묶여 있다. 그래서
   discord 바인딩으로 이미 게시된 요청은 signed로 재게시하면 fail-closed로 거부된다.
   `skill_gate.py abandon`으로 사유를 남겨 폐기하고 다시 올린다(`docs/qa/SS-1/05-live-migration-evidence.txt`).
   릴리스 승인 요청은 모드를 저장하지 않으므로 그대로 둬도 된다.
4. **되돌리기:** 같은 머신들에서 값을 `"discord"`로 되돌린다. peer 봇 토큰이 peer
   `~/.env.secrets`에 남아 있어야 한다.
agent·peer 계정에는 각 게이트웨이 user unit의
`~/.config/systemd/user/<gateway-unit>.d/30-command-sync.conf`(0600)도 배포한다. 이
drop-in은 `DISCORD_COMMAND_SYNC_POLICY=bulk`를 고정해 게이트웨이 재시동 때 Discord
slash command를 건별 mutation하지 않고 한 번에 동기화한다. 설치기가 명시적으로
소유하는 desired state이므로 내용·소유권·모드가 달라지면 다음 실행에서 수렴하고,
이미 같으면 계획에서 빠진다.

`--config`를 생략한 dry-run은 예제와 동일한 기본값을 쓰고, 실제 설치는
`NODE-CONFIG-REQUIRED`로 거부한다. 제3자 설치는 **항상 `--config`를 명시한다.**

---

## 4. 계획을 먼저 본다 (`--dry-run`)

```bash
python3 -m automation.install \
    --config /tmp/node.toml \
    --profile core \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang' \
    --dry-run
```

- root 권한이 필요 없다.
- 아무것도 쓰지 않는다.
- 실제 실행이 무엇을 할지 번호가 붙은 액션 목록으로 전부 보여준다.
- 성공하면 **rc 0**이다.

아래는 프로필 도입 전 출력 예시다(당시 깨끗한 컨테이너 실측 전문:
`docs/qa/P0-5/01-dry-run-clean-container.txt`). 현재 번호·건수는 실제 출력으로 확인한다.
`--profile`을 주면 `/etc/autophagy/healthcheck.env` 파일 계획도 포함된다:

```
INSTALL PLAN
01. account agent home=/home/agent
...
09. directory /srv/autophagy-private/locks owner=ops:autophagy mode=2770
...
25. peer-attest-key private=/home/peer/.ssh/peer_attest_ed25519 public=/etc/autophagy/peer-attest-peer.pub owner=peer ... private-content=never-printed
26. check hermes-gateway
27. check discord-readiness
28. deploy-key /home/ops/.ssh/id_ed25519 comment=... private=never-printed
29. check deploy-key-registration
30. gitleaks version=8.30.1
31. repository /srv/autophagy-agents origin=<your-origin>
...
33. file /etc/autophagy/update-allowed-signers owner=root:root mode=0644 sha256=...
...
37. file /home/agent/.config/systemd/user/hermes-gateway.service.d/30-command-sync.conf owner=agent:agent mode=0600 sha256=...
38. file /home/peer/.config/systemd/user/hermes-gateway.service.d/30-command-sync.conf owner=peer:peer mode=0600 sha256=...
...
58. timer autophagy-deploy-reconcile.timer enabled
61. check update-trust
62. check healthcheck
```

읽는 법:

- **`account`/`group`/`directory`/`file`** — 만들어질 계정·디렉터리·파일과 그 소유·모드.
  파일은 내용 sha256으로 비교하므로, 이미 같은 내용이면 다음 실행의 계획에서 사라진다.
- **`deploy-key` / `peer-attest-key`** — 키를 **생성**한다. 개인키는 절대 출력되지 않는다.
- **`check`** — 쓰기가 아니라 판정이다. `--dry-run`에서는 실행되지 않고 자리만 보인다.
- **`timer`** — systemd 타이머 활성화. 여기가 자동 업데이트가 켜지는 지점이다.

지문이 공지값과 다르면 계획을 출력하기도 전에 멈춘다:

```
[FAIL] update-trust: bundled fingerprint SHA256:... does not match the published value
--- NOT-INSTALLED: 1건 중 실패 1 / 경고 0
```

### 프로필과 헬스체크 선언

`--profile <core|rag|report-hub|full>`로 이 노드가 운영할 묶음을 고른다. 위·아래 명령의
`core`는 예시이므로 실제 용도에 맞게 바꾸되 dry-run과 apply에는 **같은 값**을 쓴다.

| 프로필 | `HEALTHCHECK_SERVICES` 선언 | 자동 선택 컴포넌트 |
|---|---|---|
| `core` | `core` | 없음 |
| `rag` | `core rag` | 없음 |
| `report-hub` | `core report-hub` | `report-hub` |
| `full` | `core report-hub rag` | `report-hub` |

설치기는 `/etc/autophagy/healthcheck.env`를 `root:root 0644`로 배치한다.
`report-hub`와 `full`은 보고 허브 컴포넌트도 설치한다. **RAG는 별도 노드 배포**다:
`automation/rag_stack/deploy.sh`가 SSH로 `RAG_NODE`에 배포하며, 주 노드만 다루는
설치기에는 RAG 컴포넌트가 없다. `rag`·`full`의 RAG 선택은 원격 감시 선언이다.

### 선택 컴포넌트

`--with-component <managed-sync|report-hub>`를 반복해 명시적으로 추가할 수 있다.
프로필 선택과 합친 뒤 이름순으로 중복을 제거한다. 예를 들어 `--profile core
--with-component report-hub`도 허브를 설치하되 헬스체크 선언은 `core`로 유지한다.
감시까지 함께 원하면 `--profile report-hub`를 쓴다. 미선택 컴포넌트의 파일·유닛은
계획에 없고, 다른 프로필로 재실행해도 이미 설치한 컴포넌트를 제거하지는 않는다.
`managed-sync`는 기존처럼 root system 유닛과 타이머만 추가한다.

`report-hub`가 추가하는 것은 다음과 같다(경로·계정은 노드 config 기준).

- ops 홈의 `.config/systemd/user/`에 collector·dashboard 유닛 2개(`ops:ops 0644`).
  system 유닛으로 바꾸지 않으며 ops 사용자 매니저에서 `enable --now`한다.
- ops 홈의 `report-hub/` 디렉터리(`ops:ops 0750`)와
  `automation -> <release_current>/automation` 심볼릭 링크.
- `report-hub/hub.env` 빈 주석 템플릿(`ops:ops 0600`, 최초 한 번만 생성).
  재실행은 운영자가 채운 값을 덮어쓰지 않는다.

ops로 `hub.env`를 편집해 `REPORT_HUB_GUILD_ID`, `REPORT_HUB_DB`,
`REPORT_HUB_QUARANTINE_LOG`, `REPORT_HUB_PEERS_FILE`, `REPORT_HUB_DASHBOARD_USER`,
`REPORT_HUB_DASHBOARD_PASSWORD_SHA256`(64자리 SHA256 hex)를 채운다. 콜렉터의
`DISCORD_BOT_TOKEN`은 ops의 `.env.secrets`에 둘 수 있다. 비공개 peers 파일과 데이터
경로도 직접 준비한다. 선택 설정은 주석을 유지하면 채널 `agents-log`, 폴링 10초,
바인드 `127.0.0.1:8800` 기본값을 쓴다. 원격 접속은 자기 tailnet 주소를 지정한다.

**활성화(enabled)는 서비스 정상 동작(active)과 다르다.** 최초 릴리스 수렴 전에는
링크 대상 코드가 없어 재시작을 반복할 수 있다. 인증 설정이 없으면 대시보드는
기동을 거부한다. 코드·설정을 준비한 뒤 ops 사용자 유닛을 재시작하고 유닛 active 및
무인증 HTTP 401을 확인한다. 자세한 수동·설치기 경로는 보고 허브 가이드.

`--profile`을 생략하면 선언 파일을 새로 만들거나
지우지 않는다. 기존 파일·환경 선언도 없으면 **모든 프로브가 그대로 돈다**.
실행 환경에서 export한 `HEALTHCHECK_SERVICES`가 파일보다 우선한다. 빈 값을 export해도
파일을 무시하고 모든 프로브를 켠다(`automation/healthcheck_registry.sh:21–32,52–60`).
프로필을 준 신규 설치에서는 `healthcheck.sh --suggest`를 따로 돌려 선언을 만들 필요가 없다.
기존 노드·수동 서비스 변경 뒤의 복구는 운영 가이드를 따른다.

---

## 5. Discord 전제를 먼저 확인한다 (선택이지만 권장)

설치기도 같은 검사를 `discord-readiness` 체크로 수행하지만, 미리 돌리면 설치를
시작하기 전에 고칠 수 있다. 이 스크립트는 **GET만** 한다 — 게시·수정·삭제 없음.

```bash
set -a; . ~/.env.secrets; set +a          # DISCORD_BOT_TOKEN을 환경에 올린다
python3 automation/install/discord_check.py --config ~/.hermes/interop/config.json
```

토큰은 환경변수에서만 읽고 출력에 절대 포함되지 않는다. 실패는 항목을 지목한다:

```
[FAIL] token: INVALID-TOKEN: 401 — 토큰이 거부됐다. …
[FAIL] intent: MESSAGE-CONTENT-INTENT-OFF: Developer Portal → Bot → … 
[FAIL] permissions[<guild>]: MISSING-PERMISSION: …
[FAIL] channel[approvals]: MISSING-CHANNEL-ID: …
```

토큰이 비어 있으면 rc 2로 사용법 오류를 낸다. 채널 3종의 의미는 전제 문서 §1.5에 있다.

### 5.1 승인 표면 필수 키: `agent_chat_channel_id`

정책 v7(첫 태그 v1.0.71)부터 소유자 승인은 `#agent-chat` 아래의 요청별 스레드에 게시된다. 이 채널 ID는 `~/.hermes/interop/config.json`의 `agent_chat_channel_id`에 둔다.

키가 없으면 승인은 fail-closed로 게시를 거부하며, 오류는 정확히 다음과 같다.

```
ApprovalSurfaceError: agent_chat_channel_id is not configured in the interop config
```

이 경우 승인 게시만 멈추고 다른 기능은 계속 동작한다. `agent_chat_channel_id`에 채널 ID를 설정하면 재시작할 필요 없이 다음 승인 게시부터 정상 동작한다.

---

## 6. 실제 설치

```bash
sudo python3 -m automation.install \
    --config /tmp/node.toml \
    --profile core \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang'
```

**`sudo`는 환경변수를 지운다.** §5에서 `DISCORD_BOT_TOKEN`을 올려두었더라도 위 명령에는
전달되지 않으므로, 설치기의 `discord-readiness` 체크는 판정할 재료가 없어
`[WARN] discord-readiness: TOKEN-ABSENT`으로 남는다 — 토큰이 틀린 것이 아니라 아예 도달하지
않은 것이고, **설치는 거기서 멈추지 않고 계획 끝까지 진행한다**(경고는 종료코드를 바꾸지
않는다). 설치 중에 함께 판정받으려면 `--preserve-env`를 쓴다(마법사·`quickstart.sh`도 보존을
시도한다):

```bash
sudo --preserve-env=DISCORD_BOT_TOKEN python3 -m automation.install \
    --config /tmp/node.toml \
    --profile core \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang'
```

`sudo`가 `--preserve-env`를 거부하는 배포판이면 그대로 진행해도 된다 — Discord 전제는 설치
자체의 전제가 아니다. 설치가 끝나면 판정하지 못한 항목이 **「소유자 확인 절차」**로 출력되며,
거기 적힌 명령(이 설치의 `--config` 경로가 박혀 있다)을 나중에 직접 돌려 닫으면 된다.

root가 아니면 계획만 출력하고 거부한다:

```
[FAIL] root: run the installer as root; dry-run needs no privilege
```

실행은 계획 순서대로 진행하며 **첫 FAIL에서 멈춘다.** 이건 의도된 동작이다 —
전제가 깨진 채로 뒤 단계를 밀어붙이지 않는다. 고치고 같은 명령을 다시 실행하면
이미 끝난 항목은 건너뛴다. **WARN은 멈추지 않는다** — 판정해서 나온 실패가 아니라
판정 자체를 하지 못한 항목이라, 설치를 세우는 대신 끝에서 소유자에게 넘긴다.

중간에 사람이 개입해야 하는 지점이 정확히 두 곳 있다.

### 6.1 Hermes 게이트웨이 (`check hermes-gateway`)

계정별로 `hermes --version`과 게이트웨이 유닛 활성 여부를 확인한다. 없으면:

```
[FAIL] hermes-gateway: Hermes is an external prerequisite for <account>; install it in
       <home>, install/start <unit>, then rerun. The installer never installs Hermes.
```

전제 문서 §3의 버전 핀에 맞춰 각 계정에 설치·기동한 뒤 다시 실행한다.

### 6.2 배포 키 등록 (`check deploy-key-registration`)

설치기가 ops 계정용 ed25519 키쌍을 만들고 **공개키를 출력한다**(개인키는 출력되지
않는다):

```
[PASS] deploy-key-registration: register this PUBLIC key read-only, then rerun if clone fails: ssh-ed25519 AAAA... ops@<node>-autophagy-deploy
```

이 공개키를 소스 저장소에 **read-only deploy key로** 등록한다. 등록 전에는 다음
액션인 `repository` clone이 인증 실패로 멈춘다 — 그때 등록하고 같은 명령을 다시
실행하면 된다. clone은 `StrictHostKeyChecking=yes`로 수행되므로, ops 계정의
`known_hosts`에 origin 호스트키가 먼저 있어야 한다:

```bash
sudo runuser -u ops -- ssh-keyscan github.com >> /home/ops/.ssh/known_hosts   # 지문을 반드시 대조할 것
```

이 단계를 빼면 설치기가 clone 전에 멈추며 `KNOWN-HOSTS-MISSING`으로 호스트명과 위
명령을 그대로 알려준다 — 예전처럼 ssh 내부에서 이유 없이 죽지 않는다. **설치기가
호스트키를 대신 넣어주지는 않는다** — 어떤 호스트키가 진짜인지는 지문을 대역외로
대조한 사람만 정할 수 있고, 그 판단을 설치기가 가로채면 이 설정 자체가 무의미해진다.

> 그룹에 가입하는 경우 이 공개키가 3단계 핸드셰이크의 ①단계 제출물이다. 가입 절차
> 자체는 이 문서가 아니라 그룹 관리자/팀원 매뉴얼이 소유한다.

---

## 7. 끝났는지 어떻게 아는가

설치기의 마지막 두 액션이 종료 게이트다.

### `check update-trust`

`/etc/autophagy/update-allowed-signers`의 **소유·모드·지문**을 다시 읽어 판정한다.

```
[PASS] trust-key.file: /etc/autophagy/update-allowed-signers root:root 0644 정규 파일
[PASS] trust-key.fingerprint: 공지 지문과 일치 — SHA256:...
```

`--expect-update-trust-fingerprint`를 주지 않았다면 두 번째 줄은 PASS가 아니라
**WARN**이 되고 설치된 지문을 출력한다. 그 값을 공지값과 직접 대조하는 것은 사람의 몫이다.

언제든 다시 확인할 수 있다:

```bash
python3 automation/install/trust_key_bootstrap.py verify --expect-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang'
```

파일이 없으면 이렇게 나오고, 그 상태에서는 리컨실러가 **어떤 릴리스도 적용하지 않는다**:

```
[FAIL] trust-key.file: TRUST-KEY-MISSING: /etc/autophagy/update-allowed-signers가 없다. …
```

### `check healthcheck`

ops 계정으로 `automation/healthcheck.sh`를 실행한다. 이 스크립트는 **읽기 전용**이며,
§4의 선언으로 선택된 프로브를 전부 돌고 마지막 줄에 판정을 남긴다.

- 전부 통과: 로그 마지막이 `ALL_HEALTHY`, rc 0 →
  `[PASS] healthcheck: healthcheck.sh ALL_HEALTHY`
- 하나라도 실패: 실패한 체크마다 `FAIL <이름>`을 남기고 마지막이 `HEALTHCHECK_FAILED`,
  rc 1 → `[FAIL] healthcheck: healthcheck.sh rc=1`
- 원격 프로브가 **전부** 실패하면 개별 티켓 대신 `INFRA_FAILURE` 한 건으로 묶는다.
  이 경우 서비스 N개가 아니라 공유 SSH 경로 하나를 의심한다.

직접 돌려볼 수 있다(쓰기 없음):

```bash
sudo runuser -u ops -- bash /srv/autophagy-agents/automation/healthcheck.sh
```

### 최종 판정 줄

설치기는 마지막에 한 줄 요약을 낸다.

```
--- INSTALLED: N건 중 실패 0 / 경고 0
```

`INSTALLED` + 실패 0이면 rc 0이다. 실패가 하나라도 있으면 `NOT-INSTALLED` + rc 1이다.
**경고(WARN)는 rc를 바꾸지 않는다** — 대표적인 것이 위의 "지문을 직접 대조하라"이며,
그건 기계가 대신 판단할 수 없는 항목이기 때문이다.

### 사람이 승인할 연결이 끝났는지 — doctor

종료 게이트는 인프라만 판정한다. 주 모델 로그인·Discord 봇·승인 채널·Google 동의는 사람이 해야 하고,
그 상태는 doctor 가 계정 안에서 읽어 판정한다(쓰기 없음). 마법사는 설치 끝에 이것을 돌려 하나씩 안내한다.

```bash
cd /srv/autophagy-agent-current && python3 -m automation.doctor
```

`[FAIL]` 이 없으면 필수 연결은 끝났다. 항목별 승인 절차와 매시간 알람은 [연결·승인 점검](연결-승인-점검.md) 가이드가 소유한다.
프로필로 설치했다면 알람 워처(`doctor-watch` 타이머)도 이미 켜져 있다.

### 자동 업데이트가 켜졌는지

```bash
systemctl list-timers | grep autophagy
```

`autophagy-deploy-reconcile.timer`가 보이면 리컨실러가 살아 있다. 그 뒤로는 업스트림에
**서명된 릴리스 태그**가 올라올 때마다 노드가 스스로 수렴하고, 실패하면 스스로
되돌린다. 서명이 없는 head는 적용되지 않으며 그 사유가 healthcheck에 나타난다.

### 관리형 스킬 격리와 소유자 알림이 준비됐는지

`managed-sync`를 opt-in으로 설치했다면 설치 직후 다음 두 읽기 전용 검사를 추가로 한다.

```bash
sudo runuser -u agent -- bash -lc 'cd /srv/autophagy-agent-current && PYTHONPATH=. python3 -m automation.managed_sync status'
sudo runuser -u agent -- bash -lc 'set -a; source "$HOME/.env.secrets"; set +a; python3 -c '\''import os; required=("DISCORD_BOT_TOKEN","AUTOPHAGY_OWNER_ID"); missing=[name for name in required if not os.environ.get(name)]; print("OWNER-NOTICE-CONFIGURED" if not missing else "OWNER-NOTICE-MISSING:"+",".join(missing)); raise SystemExit(bool(missing))'\'''
```

첫 명령은 검증된 릴리스가 quarantine에 몇 건 남았는지 `pending=<N>`으로 보여준다. 둘째
명령은 값 자체를 출력하지 않고 소유자 DM 통지에 필요한 두 이름의 설정 여부만 판정한다.
실제 통지 경로는 `automation/managed_sync/notify.py`가 아니라
`automation/owner_notice.py`이며, `managed_sync_watch.py`가 격리 성공 뒤 이 공용 경로를
best-effort로 호출한다. `pending`이 1 이상인데 둘째 명령이 실패하면 격리는 정상이어도
소유자가 새 릴리스를 모를 수 있으므로 설치 완료로 판정하지 않고 자격증명을 보완한다.

---

## 8. 자주 막히는 곳

| 증상 | 원인 | 조치 |
|---|---|---|
| `INSTALL-BLOCK: <메시지>` | config 파싱·자산 렌더·키 파싱 실패 | 메시지가 지목한 파일을 고친다. 아무것도 쓰이지 않았다 |
| `[FAIL] root: …` | `sudo` 없이 실제 실행 | `sudo`를 붙이거나 `--dry-run`을 쓴다 |
| `[FAIL] EnsureAccount: … FileNotFoundError` | `loginctl`/`useradd` 부재 = systemd 없는 환경 | 실제 systemd 호스트에서 실행한다 |
| `[FAIL] hermes-gateway: …` | 계정별 Hermes 미설치·미기동 | 전제 문서 §3대로 설치 후 재실행 |
| `[FAIL] discord-readiness: discord_check.py rc=1` | 토큰·인텐트·권한·채널 중 하나 | §5를 단독 실행해 어느 항목인지 본다 |
| `[WARN] discord-readiness: TOKEN-ABSENT` | 토큰이 설치기 환경에 없어 **판정하지 못했다**(§6의 `sudo` 문제) | 설치는 멈추지 않는다. 끝에 출력되는 「소유자 확인 절차」의 명령으로 나중에 확인한다 |
| `[FAIL] discord-readiness: USAGE-REFUSED` | `discord_check.py`가 argv를 거부했다(`--config` 값이 `-`로 시작하는 등) | 넘긴 `--config` 경로를 고친다. 토큰 문제가 아니다 |
| `FAIL … owner notice credentials` (healthcheck) | `/etc/autophagy/repair-approval.env`에 통지 자격증명도, 받지 않겠다는 선언도 없다 | 두 값을 채우거나 `OWNER_NOTICE_OPTIONAL=1` 한 줄을 적는다. 프로브 출력이 그 두 갈래를 그대로 안내한다 |
| `FAIL … healthcheck probe allowlist matches the checks` | 프로브 목록이 바뀐 릴리스를 받았다(허용목록 지문 변경) | 노드에서 `automation/provision-healthcheck-probe.sh`를 1회 재실행한다(멱등 — 같은 바이트면 무동작) |
| `repository` 액션에서 멈춤 | deploy key 미등록 또는 `known_hosts` 부재 | §6.2 |
| `TRUST-KEY-FINGERPRINT-MISMATCH` | 번들 키 ≠ 공지 지문 | **진행하지 않는다.** 유지보수자에게 확인 |
| `SYNC-BLOCK` · `UPDATE-TRUST-BLOCK` · `EnableTimer`/`EnsureRepository` 실패 | 신규 노드에서만 드러나는 설치기 공백 | [신규 노드 설치에서 막히는 6곳](../troubleshooting/신규-노드-설치-공백.md) |

---

### 업데이트 신뢰키가 바뀌었을 때 (이미 설치한 서버)

유지보수자가 업데이트 신뢰키를 바꾸면(회전), 새 키로 서명된 릴리스는 이 서버의
`/etc/autophagy/update-allowed-signers` 에 새 키가 들어가기 전까지 **설치되지 않는다** —
서버는 옛 키로 검증되는 마지막 릴리스에 머문다. 업데이트 채널은 일부러 `/etc` 를 고치지
않으므로(자기 신뢰 근원을 스스로 바꾸는 채널은 한 번 장악되면 끝이다) 이 단계는 서버
운영자가 손으로 한다. 2026-10-13 회전이 처음이었다: v1.22.2 까지는 옛 키
`SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA`, v1.22.3 부터는 새 키
`SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang` 다.

**알아채는 법.** 리컨실러 저널과 헬스체크에 이런 줄이 남고, 소유자 통지가 설정돼 있으면
같은 내용이 한 번 온다.

```
[deploy-reconcile] UPDATE-TRUST-STALE-KEY 신뢰 키 갱신 필요: 더 새 릴리스 v1.40.0 가 이 서버의 신뢰 키(/etc/autophagy/update-allowed-signers)로 검증되지 않아 v1.22.2 에 머물러 있다. 새 태그 서명 키 지문 SHA256:… (BAD-SIGNATURE). …
```

```bash
journalctl --user -u autophagy-deploy-reconcile.service --since today | grep UPDATE-TRUST   # ops 계정에서
```

**고치는 법.** 줄에 찍힌 지문이 **README·공개 릴리스 노트의 지문과 같을 때만** 진행한다.
다르면 멈추고 유지보수자에게 확인한다 — 태그를 위조한 사람도 같은 경고를 만들 수 있다.

```bash
# 1. 최신 릴리스에 동봉된 공개키를 받아 지문을 본다
curl -fsSL -o /tmp/update-trust.pub \
    https://github.com/orientpine/cytoplasm/releases/latest/download/update-trust.pub
python3 /srv/autophagy-agent-current/automation/install/trust_key_bootstrap.py fingerprint \
    --key /tmp/update-trust.pub

# 2. 기존 줄을 보존한 채 새 키를 덧붙인다(먼저 --dry-run 으로 두 줄이 렌더되는지 본다)
sudo python3 /srv/autophagy-agent-current/automation/install/trust_key_bootstrap.py install --add \
    --key /tmp/update-trust.pub \
    --expect-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang' --dry-run
sudo python3 /srv/autophagy-agent-current/automation/install/trust_key_bootstrap.py install --add \
    --key /tmp/update-trust.pub \
    --expect-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang'
```

다음 리컨실러 틱(2분 이내)에 서버가 최신 릴리스로 전진하고 경고 줄이 사라진다.
`readlink /srv/autophagy-agent-current` 가 새 커밋을 가리키면 끝이다. 옛 키 줄은 남아 있어도
해가 없다 — 옛 키로는 이제 새 릴리스가 서명되지 않는다. 지우려면 `--add` 없이 같은 명령을
한 번 더 실행해 새 키 한 줄로 바꾼다.

### 프로젝트 대시보드를 이 서버에서 켜려면

자동 업데이트는 코드 트리(`/srv/autophagy-agent-current`)를 바꾸고 게이트웨이를 다시 띄울 뿐,
계정 홈의 Hermes cron 과 사용자 유닛은 놓지 않는다. 그래서 프로젝트 대시보드(v1.23.0~)는
서버가 최신 릴리스로 따라잡아도 **저절로 켜지지 않는다** — 소유자 노드에서는 릴리스 승인 뒤
`deploy_all.sh` 가 놓지만, 그 경로는 유지보수자 워크스테이션에서 SSH 로 도는 것이라 이 서버에는
닿지 않는다. 대시보드를 쓰려면 agent 계정으로 한 번 실행한다(다시 실행해도 같은 상태가 된다):

```bash
sudo -u agent -H bash -lc '
  set -e
  r=/srv/autophagy-agent-current/automation/project_board
  install -D -m 0755 "$r/cron/project_board_watch.py"   ~/.hermes/scripts/project_board_watch.py
  install -D -m 0755 "$r/cron/project_board_analyze.py" ~/.hermes/scripts/project_board_analyze.py
  install -D -m 0644 "$r/systemd/autophagy-project-board.service" ~/.config/systemd/user/autophagy-project-board.service
  hermes cron list --all | grep -q project-board-watch   || hermes cron create "every 2m"  --name project-board-watch   --no-agent --script project_board_watch.py   --deliver local
  hermes cron list --all | grep -q project-board-analyze || hermes cron create "every 30m" --name project-board-analyze --no-agent --script project_board_analyze.py --deliver local
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  systemctl --user daemon-reload
  systemctl --user enable --now autophagy-project-board.service
  systemctl --user is-active autophagy-project-board.service'
```

분석 cron 은 노드 설정 `analyze.enabled` 를 켜기 전까지 아무것도 하지 않는다. 설정·접속 주소는
[프로젝트 대시보드 가이드](프로젝트-대시보드.md)가 소유한다. 래퍼 두 파일은 릴리스마다 바뀔 수
있으므로, 대시보드가 이상하면 위 `install` 세 줄을 다시 실행한다.

## 9. 이 문서가 실제로 검증된 범위

정직하게 적는다. 근거는 `docs/qa/P0-5/`와 [`docs/qa/INSTALL-TUI/`](../qa/INSTALL-TUI/)에 있다.

- **검증됨**: `--dry-run` 전 구간이 깨끗한 컨테이너에서 rc 0. 전제 미충족 6종이 각각
  이름을 지목해 보고된다. 비-root 실행 거부. 신뢰키 지문 대조·불일치 거부.
- **systemd 실제 apply 앞단 검증됨**: 특권 컨테이너에서 계정·linger·디렉터리·peer
  attestation 키까지 수렴하고 `[FAIL] hermes-gateway`에서 설계대로 멈춘다.
- **Hermes 스텁 뒤 계획 전 구간 도달 검증됨(2026-09-09)**: 같은 특권 컨테이너에 게이트웨이
  스텁을 놓고 다시 돌리면 `discord-readiness`가 토큰 없이 `[WARN]`으로 지나가고, 배포키
  생성·등록 안내·gitleaks·체크아웃 2개·자산 파일·심링크·healthcheck 프로브·타이머·신뢰키
  검증까지 계획 50/50 항목에 도달한 뒤, 서비스가 없는 컨테이너이므로 `[FAIL] healthcheck`로
  끝난다. 증적 [docs/qa/INSTALL-TOKEN-WARN/](../qa/INSTALL-TOKEN-WARN/).
- **아직 실호스트에서 검증되지 않음**: 실제 Hermes·Discord 전제 뒤 `healthcheck.sh` 전부
  PASS → 서명 릴리스 push 후 `current` 전진. 하네스는 외부 전제를 대신 설치하거나 모의
  통과시키지 않는다(스텁은 Hermes 런타임도 Discord 접근도 증명하지 않는다). 첫 실제
  Linux+systemd 호스트 완주 때 닫힌다.

## 관련

- 전제: [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)
- 설치 마법사·스크립트 대안(위 §3→§4→§6→§7을 안내한다. 절차는 이 문서가 그대로 소유한다):
  [quickstart-install.md](quickstart-install.md) · [`automation/install/quickstart.sh`](../../automation/install/quickstart.sh)
- 환경변수 템플릿: [`configs/env.example`](../../configs/env.example)
- 노드 config 예제: [`configs/node.example.toml`](../../configs/node.example.toml)
- 검증 증적: [`docs/qa/P0-5/summary.md`](../qa/P0-5/summary.md)
