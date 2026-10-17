# 제3자 런타임 전제 (공개본 설치 전에 스스로 마련해야 하는 것)

> **대상 독자**: 공개본을 받아 **자기 인프라에 자기 에이전트를 처음 세우는 사람**.
> **이 문서가 답하는 질문**: "설치를 시작하기 전에 내가 준비해야 하는 것이 정확히 무엇인가?"
>
> **소유 범위** — 이 문서는 *전제조건*만 소유한다. 설치 명령 자체는 `docs/guide/install.md`
> (P0-5)가 단독으로 소유하고, 역할별 사용법은 `manual-group-admin.md`(W-M1)·
> `manual-member.md`(W-M2)가 소유한다. 같은 절차를 두 문서가 설명하면 반드시 한쪽이 낡으므로
> 여기서는 **참조만** 한다.
>
> `docs/guide/onboarding-kit.md`와 혼동하지 말 것: 그쪽은 **cha의 공유 Lab에 합류하는**
> 연구원용이고, 이 문서는 **독립 설치**용이다. 겹치는 Discord 절차는 이 문서가 정본이다.

---

## 0. 한 장 요약 — 준비물 5종

| # | 준비물 | 왜 필요한가 | 자동 확인 |
|---|---|---|---|
| 1 | Linux + systemd 사용자 세션, Python 3.11+, `git`·`curl` | 런타임·워처·타이머가 전부 systemd user unit이다 | 설치기(W-F1-B) |
| 2 | **Discord 앱/봇 1개** + 서버 2개 + 채널 3종 | 소유자 승인·봇간 인터롭이 전부 Discord 표면이다 | `automation/install/discord_check.py` (§1.6) |
| 3 | **OpenAI 호환 `/v1` 모델 엔드포인트** + 자기 키·자기 예산 | 게이트웨이가 이 엔드포인트로만 추론한다 | §2.5 curl 1줄 |
| 4 | **Hermes 게이트웨이** (§3의 핀 범위) | 이 프로젝트는 게이트웨이를 설치하지 않는다 — 외부 선행조건이다 | 설치기가 버전 대조 |
| 5 | **업데이트 신뢰키 지문**(공개 repo README/릴리스 노트의 공지값) | 자동 업데이트가 서명 검증을 통과해야 전진한다 | `trust_key_bootstrap.py verify` (§4) |

준비되지 않은 항목이 있으면 설치기는 **진행하지 않고 멈춘다**(fail-closed). 그것이 정상 동작이다.

---

## 1. Discord 앱·봇·서버·채널

### 1.1 앱과 봇 생성

1. <https://discord.com/developers/applications> → **New Application** → 이름 입력
   (권장 규칙 `<ROMANIZEDNAME>-Agent`, `docs/guide/discord-server-architecture.md` §2.2).
   앱은 **자신의** Portal 계정 소유여야 한다.
2. 좌측 **Bot** → **Reset Token** → 토큰을 즉시 복사한다. 이 화면을 떠나면 재생성해야 한다.
3. 토큰은 **`~/.env.secrets`(mode 600)에만** 둔다. 변수명은 `DISCORD_BOT_TOKEN`
   (전체 목록은 `configs/env.example`).

> ⚠️ 토큰·API 키를 문서·커밋 메시지·주석·Discord 메시지에 **모양만이라도** 남기지 않는다.
> 이 저장소는 secret-scan이 커밋을 fail-closed로 막고, 오탐 하나가 전 배포를 세운다.

### 1.2 Message Content 인텐트 (가장 흔한 실패)

같은 **Bot** 화면 하단 → **Privileged Gateway Intents** → **Message Content Intent = ON** → Save.

- 이 토글이 꺼져 있으면 다른 봇 메시지의 `content`가 **빈 문자열**로 도착해 규약 파싱이 전부
  조용히 실패한다. 오류가 아니라 침묵으로 나타나므로 스스로 알아채기 어렵다.
- Portal에서 ON으로 저장한 뒤 **게이트웨이를 재시작**해야 적용된다.
- 확인은 §1.6의 `discord_check.py`가 대신한다(`intent` 체크).

### 1.3 초대 URL 구성

Portal의 **Installation** → Installation Contexts = *Guild Install* 로 두고, 초대 URL을 직접
구성한다:

```
https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&scope=bot+applications.commands&permissions=309237746752
```

| 항목 | 값 |
|---|---|
| scopes | `bot`, `applications.commands` |
| permissions 정수 | `309237746752` |
| 그 정수의 구성 | View Channels(1024) · Send Messages(2048) · Read Message History(65536) · Add Reactions(64) · Attach Files(32768) · Create Public Threads(34359738368) · Send Messages in Threads(274877906944) |
| **부여 금지** | `Administrator` · `Manage Guild` · `Manage Roles` |

금지 권한은 편의가 아니라 위협모델의 문제다 — 인젝션된 에이전트가 자기 승인 표면을 스스로
재구성할 수 있게 된다. `discord_check.py`는 이 3종을 보유한 봇을 **OVER-PRIVILEGED로 거부**한다.

### 1.4 서버 2개

| 서버 | 용도 |
|---|---|
| 협업 서버(공유) | 사람 논의 · 봇 구조화 보고 · 봇간 조율 트래픽 |
| **개인 서버** | 스킬 공급망 승인(`#approvals`) — 자기 노드의 2차 주체가 같은 채널을 봐야 하므로 DM으로 옮길 수 없다 |

- 개인 서버 이름 규칙과 PII 규율은 `docs/guide/discord-server-architecture.md` §2.2를 따른다
  (실명·생일이 든 서버명을 저장소에 남기지 않는다).
- **소유자 전용 승인(메일·예산·캘린더·위키·수리 등)은 서버 채널이 아니라 봇과 소유자의 DM**이다.
  별도 채널을 만들 필요가 없다.
- 혼자 시작한다면 협업 서버도 자기 것 하나면 된다. 그룹에 합류하는 경우 협업 서버는 관리자가
  운영하는 것을 쓰고 개인 서버만 자기가 만든다.

### 1.5 채널 3종 (런타임이 실제로 읽고 쓰는 것)

| 채널 | 위치 | 용도 | config 키 |
|---|---|---|---|
| `#agents-log` | 협업 서버 | 봇의 구조화 규약 보고(v0) 게시 | `agents_log_channel_id` |
| `#autophagy-agents` | 협업 서버 | 봇간 조율(`coord-`) 봉투 트래픽 | `interop_channel_id` |
| `#approvals` | **개인 서버** | 스킬 공급망 승인(배포·attestation·발행·managed 활성화) | `personal_approvals_channel_id` |

`#team`(사람 논의)은 편의 채널이며 **코드가 읽지 않는다** — 만들지 않아도 설치는 완주한다.
세 채널 모두 텍스트 채널이어야 하고, 봇이 그 채널에서 **메시지 이력을 읽을 수** 있어야 한다
(승인 리액션 판독과 봉투 수신이 이력 읽기에 의존한다).

### 1.6 자동 확인 — `automation/install/discord_check.py`

```bash
set -a; . ~/.env.secrets; set +a
python3 automation/install/discord_check.py \
  --agents-log-channel-id <id> --interop-channel-id <id> --approvals-channel-id <id>
# 또는 ~/.hermes/interop/config.json이 이미 있다면
python3 automation/install/discord_check.py --config ~/.hermes/interop/config.json
```

- **전부 GET이다.** 메시지를 게시하거나 수정·삭제하지 않으므로 운영 중인 서버에 돌려도 안전하다.
- 토큰은 환경변수에서만 읽고 **인자로 받지 않으며 출력에 절대 포함되지 않는다**(렌더 직전 한 번 더 마스킹).
- 종료코드: `0` 전부 통과 / `1` 하나 이상 실패 / `2` 사용법 오류(토큰 환경변수 없음).

진단은 조치 가능한 범주 이름으로 나온다:

| 진단 | 뜻 / 조치 |
|---|---|
| `INVALID-TOKEN` | 토큰이 거부됐다 → Reset Token 후 `~/.env.secrets` 갱신 |
| `NOT-A-BOT` | 사용자 토큰을 넣었다 → Bot 탭의 봇 토큰 사용 |
| `MESSAGE-CONTENT-INTENT-OFF` | §1.2 토글 OFF → ON 저장 + 게이트웨이 재시작 |
| `NO-GUILD` | 어느 서버에도 초대되지 않았다 → §1.3 초대 URL |
| `MISSING-PERMISSION: <이름>` | 그 권한이 빠졌다 → permissions 정수를 고쳐 재초대 |
| `OVER-PRIVILEGED: <이름>` | 금지 권한 보유 → 초대 취소 후 최소 권한으로 재초대 |
| `MISSING-CHANNEL-ID` | 그 채널의 id를 주지 않았다 → 채널 생성 후 id 전달 |
| `MISSING-CHANNEL` / `NO-ACCESS` | id가 없거나 View Channel이 막혔다 → 채널·권한 오버라이드 확인 |
| `NO-HISTORY` | Read Message History가 거부됐다 → 승인 리액션 판독 불가 |
| `WRONG-CHANNEL-TYPE` | 텍스트 채널이 아니다 |

공급망 승인 채널은 `personal_approvals_channel_id`로 식별하므로 표시 이름이 달라도
`PASS`다. `#approvals`·`#notifications`는 예시 이름이며 변경할 수 있다.
통지는 `owner_notice_channel_id`로 간다. 설정이 없는 레거시 승인 조회에만
캐시·유일한 `approvals` 이름 검색이 남으므로 이름 변경 전에 ID를 설정한다.
다른 채널 역할의 관례 이름 차이는 기존처럼 `WARN`으로 알린다.

[채널 ID 기능 소개](../기능소개/Discord-승인-통지-채널-ID.md)

---

## 2. 모델 provider

### 2.1 최소 요구

**OpenAI 호환 `/v1` 엔드포인트 1개**와 그것을 쓸 **자기 키**. 그 이상은 요구하지 않는다.
게이트웨이는 `POST /v1/chat/completions`(및 모델 목록)만 사용한다.

### 2.2 왜 LiteLLM을 권하는가

이 프로젝트의 라우팅·예산·민감도 정책(`configs/routing-policy.md`)은 **게이트웨이 앞단에서
강제되도록** 설계돼 있다. 자기 노드 loopback에 LiteLLM을 띄우면 프로바이더를 바꿔도 위쪽
설정이 그대로 남고, 월 하드캡과 태그 기반 거부를 **모델 호출 전에** 걸 수 있다.
LiteLLM이 아니어도 OpenAI 호환 엔드포인트면 동작하지만, 그 경우 예산 상한과 민감도 차단은
**당신이 직접 마련해야 한다**.

### 2.3 예시 — LiteLLM 최소 config

`configs/litellm-staging/config.yaml`을 자기 값으로 바꾼 최소형:

```yaml
model_list:
  - model_name: main            # 게이트웨이가 부르는 alias (자기 이름으로 바꿔도 된다)
    litellm_params:
      model: <provider>/<model-id>
      api_key: os.environ/<YOUR_PROVIDER_KEY_ENV>
    model_info:
      tags: ["default"]

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

가상 키에 월 예산을 건다(권장: soft alert < hard cap, `duration: 30d`). 상세 절차와 검증
항목은 `configs/routing-policy.md` §3·§5.

### 2.4 예시 — Hermes 쪽 배선

`~/.hermes/config.yaml`의 provider를 자기 엔드포인트로 맞춘다:

```yaml
custom_providers:
  - name: litellm
    base_url: http://127.0.0.1:4000/v1     # 자기 엔드포인트
    key_env: LITELLM_<ACCOUNT>_KEY          # 계정명 대문자, 하이픈은 _
default_model: main                         # §2.3의 alias
```

바꾼 뒤 `systemctl --user restart hermes-gateway.service`.

### 2.5 확인

```bash
curl -sf -H "Authorization: Bearer $YOUR_KEY" http://127.0.0.1:4000/v1/models | head -c 200
```

모델 목록이 오면 전제 충족이다. 401이면 키, 연결 거부면 엔드포인트 문제다.

### 2.6 이 저장소의 값은 cha 노드의 값이다

`configs/routing-policy.md`는 **정책 구조(일반)** 와 **이 설치의 현재 바인딩(예시)** 을 분리해
적어 두었다. 모델 id·예산 액수·키 이름·게이트웨이 경로는 전부 후자에 속하며 **자기 값으로
교체하는 것이 정상**이다. 정책 구조(단일 alias·태그 필터·fail-closed 예산·민감 태그 사전 차단)는
그대로 두는 것을 권한다 — 안전 속성이 거기에 걸려 있다.

---

## 3. Hermes 게이트웨이 버전 핀

**이 프로젝트는 Hermes 게이트웨이를 설치하지 않는다.** 외부 선행조건이며, 없으면 설치기는
안내와 함께 fail-closed로 멈춘다.

| 항목 | 값 |
|---|---|
| 검증된 버전 | **Hermes Agent v0.20.3 (2026.8.16.2)** — upstream `a3995f8a`, install method `git` |
| 검증 일자 | 2026-08-18 (프로덕션 노드 실측, agent·peer가 같은 커밋) |
| 검증된 Python | 3.11.15 (요구: 3.11+) |
| 지원 범위 | v0.20.x. 그 밖의 마이너 버전은 **미검증**이며 자기 책임으로 사용한다 |

확인:

```bash
PATH="$HOME/.local/bin:$PATH" hermes --version
```

- 설치기는 Hermes CLI의 존재와 게이트웨이 유닛의 활성 여부만 확인한다. 버전 핀 범위 검증은 아직 구현되지 않았으며 추후 추가할 계획이다.
- 상위 버전으로 올릴 때는 `automation/hermes_compat/`의 패치가 **전부 재적용되어야 한다**
  (`hermes update`는 패치를 되돌린다). 게이트웨이 버전 이동은 이 저장소에서 가장 깨지기 쉬운
  경계이므로, 올린 직후 `automation/healthcheck.sh`를 돌려 전부 PASS인지 확인한다.
- 게이트웨이를 재시작할 때는 **agent·peer 계정 세트를 함께** 재시작한다(한쪽만 금지 —
  루트 `AGENTS.md`「게이트웨이 재시동 규칙」).

---

## 4. 업데이트 신뢰키 부트스트랩

### 4.1 왜 키가 설치기에 동봉되는가 (닭-달걀)

노드의 자동 업데이터는 **유지보수자가 서명한 릴리스 태그에만** 수렴한다. 그 서명을 검증하려면
코드를 받기 **전에** 이미 유지보수자의 공개키를 갖고 있어야 한다. 코드를 주는 곳에서 키도
받는다면, 코드를 위조할 수 있는 자는 키도 함께 위조할 수 있으므로 검증이 무의미해진다.

그래서 키는 **설치기 번들에 동봉해** 배포한다 — 설치기를 실행한다는 것은 이미 그 작성자를
신뢰하기로 결정했다는 뜻이다. 그 신뢰가 실제로 값을 갖는 것은 **대역외 대조** 덕분이다:
지문이 공개 repo README와 각 릴리스 노트에 게시되고, 사용자가 **설치기가 아닌 경로로** 그
값을 읽어 설치본과 맞춰 본다.

### 4.2 두 개의 서명키 — 절대 섞지 않는다 (계획 D8)

|  | **업데이트 신뢰키** | **그룹 스킬 서명키** |
|---|---|---|
| 서명 대상 | 공개 repo 릴리스 태그 | 관리형 스킬(`managed-*`) 태그 |
| 검증 주체 | `automation/update_trust.py` (W-F1-D) | `automation/managed_sync/verify.py` |
| 파일 | `/etc/autophagy/update-allowed-signers` | `/etc/autophagy/managed-skills-allowed-signers` |
| 소유자 | 업스트림 유지보수자 | 그룹 관리자 |
| 전달 경로 | **설치기 동봉** + 지문 대역외 공지 | 가입 핸드셰이크에서 지문 대역외 전달(W-F2-B) |
| 회전 주체 | 유지보수자(W-M3) | 그룹 관리자(W-M1) |

그룹에 속한 노드는 **둘 다** 필요하고 파일도 소유자도 교체 주기도 다르다. 하나로 합치려는
시도는 신뢰 경계 위반이며, `plan_install()`이 그룹 스킬 파일 경로로의 설치를 **코드에서 거부**한다.

### 4.3 절차

```bash
# ① 번들 키의 지문을 먼저 본다 (쓰기 없음)
python3 automation/install/trust_key_bootstrap.py fingerprint --key <번들>/update-trust-key.pub

# ② 공개 repo README·릴리스 노트의 공지값과 눈으로 대조한다  ← 이 단계가 보안의 전부다

# ③ 공지값을 인자로 넘겨 설치한다. 불일치면 아무것도 쓰지 않고 non-zero로 끝난다.
sudo python3 automation/install/trust_key_bootstrap.py install \
  --key <번들>/update-trust-key.pub --expect-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang'

# ④ 설치본을 다시 검증한다 (소유·모드·지문)
python3 automation/install/trust_key_bootstrap.py verify --expect-fingerprint 'SHA256:NT5Ra3dsXhGg/+k6dvZaaE9epZcSuq4KuFGCMYQiang'
```

`--dry-run`을 붙이면 쓸 내용·경로·모드·지문만 출력하고 디스크를 건드리지 않는다.

### 4.4 설치 결과가 만족해야 하는 것

| 항목 | 기대값 | 왜 |
|---|---|---|
| 경로 | `/etc/autophagy/update-allowed-signers` | agent가 쓸 수 있는 경로(`~/.hermes/**`)에 두면 인젝션된 agent가 자기 키로 바꿔 자기 릴리스를 통과시킨다 |
| 소유/모드 | `root:root` `0644` | root만 쓰고, 비-root 리컨실러가 읽을 수 있어야 한다 |
| 파일 종류 | 정규 파일(심링크 거부) | 링크를 갈아끼우는 우회 차단 |
| 부모 디렉터리 | root 소유, group/other 쓰기 없음 | 파일만 지켜도 디렉터리가 열려 있으면 교체된다 |
| 네임스페이스 | `namespaces="git"` | git의 SSH 서명은 항상 `git` 네임스페이스다. 다른 값이면 `git verify-tag`가 *key is not permitted for use in signature namespace "git"* 로 **모든 릴리스를 거부**한다(실측 확인) |

`verify`는 이 항목들을 각각 다른 진단 코드로 알린다(`TRUST-KEY-WRONG-OWNER` ·
`TRUST-KEY-WRITABLE` · `TRUST-KEY-WRONG-MODE` · `TRUST-KEY-NOT-A-FILE` ·
`TRUST-KEY-FINGERPRINT-MISMATCH`).

### 4.5 지우면 어떻게 되는가

`/etc/autophagy/update-allowed-signers`가 없으면 리컨실러는 **검증할 근거가 없으므로 전진을
거부한다**(fail-closed). 업데이트가 멈추는 것이 정상 동작이며, 미검증 코드를 root 권한으로
적용하는 것보다 항상 낫다. 복구는 §4.3을 다시 수행하는 것이다.

> 회전(키 교체) 절차는 이 문서가 아니라 **W-M3 유지보수자 매뉴얼**이 소유한다.
> 노드 쪽에서 기억할 것은 하나다: **새 키를 먼저 추가하고, 새 서명이 검증되는 것을 확인한
> 뒤에 옛 키를 지운다.** 순서를 뒤집으면 그 노드의 업데이트가 즉시 멈춘다.

### 4.6 서명 필수 정책과 opt-out

제3자 설치의 `~/.hermes/node.toml`은 `require_signed_updates`를 생략하거나 다음처럼 명시합니다.

```toml
require_signed_updates = true
```

런타임 파일에서 필드를 생략한 코드 기본값도 `true`입니다. `false`는 공개 signed-tag 파이프라인
전환 전 cha의 비공개 origin을 위한 **명시적 과도기 opt-out**일 뿐입니다. 이 값을 쓰면 mutable
`main`이 root 적용 입력이 되어 upstream 쓰기 권한 탈취가 공급망 RCE로 이어질 수 있습니다.
`configs/node.example.toml`은 cha의 현재 설치를 재현하는 worked example이라 이 위험한 값을
명시하고 있으므로, 제3자가 example을 복사할 때는 반드시 `true`로 바꿉니다.

---

## 5. 시크릿 회전 절차

공통 원칙 세 가지:

1. **새 것을 먼저 추가하고, 동작을 확인한 뒤, 옛 것을 폐기한다.** 순서를 뒤집으면 그 사이가
   전면 장애 구간이 된다.
2. 시크릿 파일은 **자기 계정 소유 mode 600**, 홈은 700. 값은 어떤 로그·문서·메시지에도 남기지 않는다.
3. 변수 이름은 `configs/env.example`이 정본이다 — 여기서 다시 나열하지 않는다.

### 5.1 Discord 봇 토큰

1. Portal → Bot → **Reset Token** (이 순간 옛 토큰은 즉시 무효다 — 무중단 회전이 **불가능**한 유일한 항목).
2. `~/.env.secrets`의 `DISCORD_BOT_TOKEN`을 갱신(mode 600 유지).
3. **agent·peer 게이트웨이를 함께 재시작**한다(한쪽만 금지).
4. `python3 automation/install/discord_check.py --config ~/.hermes/interop/config.json`으로
   재확인 — `token`·`intent`·`channel[*]`이 전부 PASS여야 한다.

> ⚠️ 재시작 창 동안 진행 중이던 peer attestation·승인 응답은 fail-closed 타임아웃될 수 있다.
> 차단이 아니라 재요청 대상이다.

### 5.2 배포 키(deploy key)

설치별 **읽기 전용** 키(코드/스킬을 받아오는 용도)와, 이 저장소에만 있는 **쓰기 전용** 수리 키를
구분한다. 두 키를 절대 섞지 않는다.

| 키 | 위치 | 권한 |
|---|---|---|
| 설치별 소스 fetch 키 | 노드의 계정 홈 `~/.ssh/` 또는 `managed-sync` config의 `ssh_key_path` | **읽기 전용** |
| 수리 push 키 | `/srv/autophagy-private/repair_push_key` (ops:600) | 저장소 한정 write |

회전 순서:

1. 노드에서 새 키페어 생성(`ssh-keygen -t ed25519 -C '<node>-<purpose>'`, 개인키 600).
2. **공개키를 저장소에 먼저 등록**한다(GitHub → Settings → Deploy keys, read-only 체크 유지).
3. 설정이 새 개인키를 가리키게 바꾸고 `git ls-remote <remote>`로 **읽기가 되는지 확인**한다.
4. 확인된 뒤에야 저장소에서 옛 공개키를 **삭제**한다.
5. 수리 push 키는 홈이 아니라 `/srv/autophagy-private/`에 둔다 — 관련 systemd 유닛이
   `ProtectHome=yes`라 홈에 둔 키는 디스크에 있어도 런타임에만 사라진다. 호스트 키 DB
   (`/srv/autophagy-private/repair_known_hosts`)도 같은 이유로 고정하며 `accept-new`로
   우회하지 않는다.

### 5.3 서명키

| 키 | 회전 주체 | 노드가 할 일 |
|---|---|---|
| 업데이트 신뢰키 | 유지보수자(W-M3) | 새 지문을 대역외로 확인 → `trust_key_bootstrap.py install` → 새 릴리스 태그가 검증되는지 확인 → 옛 항목 제거 |
| 그룹 스킬 서명키 | 그룹 관리자(W-M1) | 관리자에게 **대역외로** 새 지문을 받아 `/etc/autophagy/managed-skills-allowed-signers` 갱신 → 다음 발행이 검증되는지 확인 |

두 경우 모두 **중첩 구간(옛 키 + 새 키 동시 등록)** 을 두고, 새 서명이 실제로 검증되는 것을
본 뒤에 옛 항목을 지운다.

> ⚠️ **지문을 그룹 Discord 채널로 전달하면 안 된다.** 그 채널을 장악한 자가 곧 발행자가 된다.
> 대역외(직접 대면·다른 매체·이미 신뢰된 별도 경로)로 전달한다.

---

## 6. 설치 전 체크리스트

| # | 항목 | 확인 방법 | 통과 기준 |
|---|---|---|---|
| 1 | Linux + systemd user session | `systemctl --user is-system-running` | 응답이 있다(`degraded`도 가능) |
| 2 | Python 3.11+ / `git` / `curl` | `python3 -V` 등 | 3.11 이상 |
| 3 | 봇 토큰이 `~/.env.secrets`에만 존재 | `stat -c '%a %U' ~/.env.secrets` | `600 <account>` |
| 4 | Message Content Intent ON | §1.6 checker | `intent` PASS |
| 5 | 최소 권한으로 초대, 금지 권한 없음 | §1.6 checker | `permissions[*]` PASS |
| 6 | 채널 3종 접근·이력 읽기 | §1.6 checker | `channel[*]` PASS |
| 7 | 모델 엔드포인트 응답 | §2.5 curl | 모델 목록 반환 |
| 8 | 게이트웨이 버전이 핀 범위 | `hermes --version` | §3 표와 일치 |
| 9 | 업데이트 신뢰키 설치·대조 | §4.3 ③④ | `verify`가 전부 PASS |

전부 통과했다면 `docs/guide/install.md`로 넘어간다.

---

## 관련

- 설치 절차: `docs/guide/install.md` (P0-5)
- Discord 토폴로지 정본: `docs/guide/discord-server-architecture.md`
- 공유 Lab 합류(별개 경로): `docs/guide/onboarding-kit.md`
- 라우팅·예산 정책: `configs/routing-policy.md`
- 환경변수 정본: `configs/env.example`
- 코드: `automation/install/discord_check.py` · `automation/install/trust_key_bootstrap.py`
  (테스트: `tests/unit/test_install_discord_check.py` · `tests/unit/test_install_trust_key_bootstrap.py`)
