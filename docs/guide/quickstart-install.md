# 빠른 시작 — 받은 것과, 실행할 한 줄

이 문서는 **매뉴얼이 아니라 표지**다. 설치 절차의 단일 진실은
[install.md](install.md)이고, 설치 이후의 사용법은 [manual-member.md](manual-member.md)가
소유한다. 여기서는 같은 내용을 다시 적지 않는다 — 처음 받은 사람이 **어디부터 손대야
하는지**만 알려주고 넘긴다.

---

## 도리로 시작하기 (기본)

```bash
python3 -m automation.install --dry-run
python3 -m automation.install.wizard
```

마법사 첫 선택·빈 입력·비대화 프로필 생략은 도리다. root·Hermes·업데이트 신뢰키
옵션 없이 포크·CLI·역할을 준비한다. 실제 설치에는 git·Bun이 필요하고,
역할별 봇·채널·모델 로그인은 자기 것으로 구성한다.
`quickstart.sh --dry-run-only`도 같은 기본 경로를 쓴다.
선택 역할은 마법사 `--dori-role okdori|moodori|gongdori`, 직접 설치기 `--role okdori|moodori|gongdori`다.
단계와 제한은 [설치 가이드](install.md#도리-기본-설치-p6)가 소유한다.

**이하 §1–§8은 명시적 레거시 Hermes 프로필용 안내**다.
기존 노드를 P6로 업데이트할 때는 설치기를 재실행하지 않고
[업그레이드·복구 절차](install.md#p6-기존-노드-업그레이드와-복구)를 따른다.

## 1. 받은 것

| 받은 것 | 무엇인가 |
|---|---|
| 저장소 체크아웃(또는 릴리스 번들) | 설치기와 스킬 소스. 설치기는 이 체크아웃 안에서 실행된다 |
| `<bundle>/update-trust.pub` | 업스트림 릴리스를 검증할 **업데이트 신뢰키** 공개키 |
| `SHA256:<공지된-지문>` | 위 키의 지문. **키와 같은 경로로 오면 안 되는 값**이다 (§3) |
| 이 문서 | 표지 |

## 2. 먼저 준비할 것

준비물의 정본은 [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)이고,
그 문서 §6의 체크리스트 9항을 통과한 뒤에 아래를 실행한다. 준비가 덜 됐어도 §4의
**계획 확인(dry-run)까지는** 안전하게 돌려볼 수 있으니, 무엇이 부족한지 먼저 보는 것을
권한다 — 부족한 항목은 하나씩 이름이 나온다.

호스트 조건 하나만 여기서 못박는다: **Linux + systemd + root 권한.** 리컨실러·워처가
systemd 타이머라서 systemd 없는 컨테이너는 계획 확인용이다. 특권 systemd 컨테이너로
검증한 실제 설치 범위와 외부 전제의 경계는 §8에 적었다.

## 3. 지문부터 대조한다 (사람이 해야 하는 유일한 판단)

키는 설치기와 함께 온다. 그러니 그 키가 진짜인지는 **설치기가 아닌 경로**로 확인해야
한다. 절차와 근거는 [install.md §2](install.md#2-신뢰키-지문을-먼저-대조한다)에 있다.

## 4. 실행할 한 줄

처음에는 **대화형 설치 마법사**로 시작한다. 저장소 체크아웃 안에서:

```bash
cd ~/autophagy-agents
python3 -m automation.install.wizard \
    --profile core \
    --update-trust-key <bundle>/update-trust.pub
```

프로필(`core`·`rag`·`report-hub`·`full`), 업데이트 origin URL, 호스트 이름, 운영자
로그인 계정, **별도 경로에서 받은 공지 지문**을 묻고 `node.toml`을 대신 작성한다.
기본 경로는 `~/.config/autophagy/node.toml`이며, `--config`로 지정한 파일이 이미 있으면
고치지 않고 재사용한다. 지문은 사용자가 준 값만 넘긴다 — 비우면 기계 대조가 빠지고
설치기 종료 게이트가 WARN이 된다(`automation/install/wizard.py:142–160`).

마법사에는 설치 로직이 없다. `quickstart.sh`처럼 **기존 설치기의 argv만 조립**하고,
계획 출력에서 집계한 한국어 요약을 보여준다. 직접 실행하고 싶으면
[install.md §3–§7](install.md#3-노드-config-작성)의 수동 절차도 그대로 유효하다.

자주 쓰는 마법사 옵션:

| 옵션 | 언제 |
|---|---|
| `--profile core` | 프로필 질문에 미리 답한다. 선택지는 `core`·`rag`·`report-hub`·`full` |
| `--origin-url` / `--node-name` / `--operator` | 새 config에 넣을 값을 미리 준다 |
| `--config PATH` | config 저장 위치를 바꾸거나 기존 파일을 재사용한다 |
| `--expect-update-trust-fingerprint 'SHA256:<공지된-지문>'` | 공지 지문을 질문 대신 인자로 준다 |
| `--dry-run-only` | 계획 확인 뒤 끝낸다. `sudo` 0회지만 config·계획 로그는 남는다 |
| `--yes` | 확인 프롬프트를 건너뛴다. **비대화 실행 전용**이며 질문에 필요한 값도 모두 줘야 한다 |
| `--with-component managed-sync` | 관리형 스킬 자동 수신을 함께 설치 (나중에 붙여도 된다) |

**비대화·스크립트 대안**인 `quickstart.sh`도 유지된다. 이쪽은
[install.md §3](install.md#3-노드-config-작성)대로 config를 먼저 만든 뒤 실행한다:

```bash
automation/install/quickstart.sh \
    --profile core \
    --config /tmp/node.toml \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:<공지된-지문>' \
    --yes
```

셸 래퍼도 `--profile core|rag|report-hub|full`을 받는다. 레거시 프로필 없이 노드 옵션만
주면 거부한다. `--group-roster` / `--expect-group-skill-fingerprint`는
마법사 옵션이 아니라 셸 래퍼·설치기 옵션이다 — 그룹 가입은
[manual-member.md §3](manual-member.md)가 소유한다.

## 5. 무엇을 보게 되는가

| 단계 | 무슨 일이 | 멈출 수 있는가 |
|---|---|---|
| ① 전제 확인 | Python·systemd·도구·업데이트 신뢰키 준비 상태를 보여준다 | 키를 읽을 수 없으면 이름을 대고 거부한다. 표시된 다른 부족 항목도 실제 설치 전에 준비한다 |
| ② 질문 → config 작성 | 노드 값을 묻고 `node.toml`을 쓴다. 기존 파일은 재사용한다 | **확인 전에도 config는 남는다**. 질문·확인이 필요한데 터미널이 없으면 거부한다 |
| ③ dry-run + 한국어 계획 요약 | root 없이 설치 계획을 받고 **그 출력에서** 종류별 건수·판정 목록을 집계한다 | 설치 자산은 쓰지 않고 check도 실행하지 않는다. rc 0은 전제 충족의 증명이 아니다 |
| ④ yes 확인 | `yes`를 입력해야 실제 설치로 간다(`--yes`는 명시적 비대화 확인) | `n`·엔터 등은 rc 3. 여기까지 `sudo` 0회 |
| ⑤ sudo apply | 같은 설치기 인자에서 `--dry-run`만 빼고 실행한다. 이미 root면 sudo는 생략한다 | 첫 실패에서 멈춘다. **원인을 고치고 같은 명령을 다시 실행한다**(멱등) |
| ⑥ 결과 + 다음 행동 | 설치기 판정과 로그 경로, 실패 원인별 조치 또는 설치 후 확인을 안내한다 | 결과는 설치기 종료코드를 그대로 따른다 |

| 종료코드 | 뜻 |
|---|---|
| `0` | 설치 완료 또는 `--dry-run-only` 계획 확인 완료(둘은 다르다) |
| `1` | 설치기 실패. 자식 설치기의 non-zero는 그대로 전파한다 |
| `2` | 사용법·전제 오류: `WIZARD-NO-TTY`, `WIZARD-UNKNOWN-PROFILE`, `WIZARD-TRUST-KEY-UNREADABLE` |
| `3` | 사용자가 확인 거부. 실제 설치·sudo는 실행하지 않았지만 config·계획 로그는 남는다 |

흐름·종료코드 근거: `automation/install/wizard.py:163–218`.

끝났다는 판정은 세 줄이다 — `[PASS] healthcheck: … ALL_HEALTHY`,
`[PASS] trust-key.fingerprint: …`, `--- INSTALLED: N건 중 실패 0`. 자세한 읽는 법은
[install.md §7](install.md#7-끝났는지-어떻게-아는가).

중간에 사람이 개입해야 하는 지점은 정확히 두 곳(**Hermes 게이트웨이 설치**, **배포 키
등록**)이며 둘 다 설치기가 그 자리에서 무엇을 하라고 출력한다 —
[install.md §6.1·§6.2](install.md#6-실제-설치).

> 팁: `sudo`는 환경변수를 지운다. 실제 설치 전에 `set -a; . ~/.env.secrets; set +a`로
> `DISCORD_BOT_TOKEN`을 올려두면 마법사는 **yes 뒤에만**
> `sudo --preserve-env=DISCORD_BOT_TOKEN` 허용 여부를 확인하고 보존을 시도한다.
> sudoers가 거부하면 경고한다. 토큰 부재는 설치기의 `discord-readiness`가 판정한다.

## 6. 막히면

- 설치기가 지목한 이름을 그대로 [install.md §8 자주 막히는 곳](install.md#8-자주-막히는-곳)에서 찾는다.
- 전제(Discord·모델·Hermes·신뢰키) 문제면 [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md).
- 마법사가 각 설치기 실행 뒤 출력하는 `전문:` 경로에서 로그를 본다.
  계획만 보고 끝내거나 취소해도 임시 `autophagy-wizard-*` 디렉터리의 로그는 남는다
  (`automation/install/wizard.py:55, 221–226`).

## 7. 설치 다음

마법사는 설치 끝(⑦)에 `python3 -m automation.doctor` 로 사람이 승인해야 하는 연결 — 주 모델 로그인,
Discord 봇, 승인 채널, Google 동의 — 을 하나씩 확인·안내한다. 나중에 다시 볼 때도 같은 명령이다
(절차: [연결·승인 점검](연결-승인-점검.md)).

설치가 끝나면 이 문서는 할 일이 없다. 이후는 **[manual-member.md](manual-member.md)**가
소유한다 — 그룹 가입 3단계 핸드셰이크(§3), 일상 승인 게이트 읽는 법(§4), 관리형 스킬이
자동으로 마운트되지 **않는** 이유(§5)가 거기 있다.

## 8. 검증된 범위

마법사·프로필 증적은 [`docs/qa/INSTALL-TUI/`](../qa/INSTALL-TUI/), 기존 셸 래퍼
증적은 [`docs/qa/W-F2.5-E/`](../qa/W-F2.5-E/quickstart-wrapper.md)에 있다.

- **마법사·프로필** — 질문으로 config 생성, 실제 dry-run 출력 집계, `n` 입력 시 rc 3·
  sudo 0회, 터미널 없음·모르는 `--profile`·읽을 수 없는 키의 명시적 거부를 확인했다.
  프로필의 선언 파일·환경 우선순위는 `tests/unit/test_install_profiles.py:59–154`가 다룬다.
- **systemd 실제 apply** — [특권 컨테이너 하네스](../../tests/e2e/install/systemd_container/README.md)로
  계정·linger·디렉터리·peer attestation 키의 생성·수렴을 확인했다. 실제 실행은
  `[FAIL] hermes-gateway`에서 **설계대로** 멈춘다. 외부 전제를 가짜 PASS로 바꾸지 않았다.
  계정·linger·키 상태의 독립 검사는 `tests/e2e/install/systemd_container/run.sh:193–220`에 있다.
- **아직 실호스트에서 검증되지 않음** — Hermes·Discord 전제 뒤의 clone → 타이머 활성 →
  최종 `healthcheck.sh` 전부 PASS는 실호스트에서만 닫을 경계다. 하네스가 앞단을 통과했다고
  설치 완주를 주장하지 않는다. 첫 실호스트 완주 증적을 같은 QA 디렉터리에 추가한다.

## 관련

- 설치 절차 단일 진실: [install.md](install.md)
- 전제: [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)
- 설치 이후 전부: [manual-member.md](manual-member.md)
- 마법사: [기능 소개](../기능소개/설치-마법사.md) · [`automation/install/wizard.py`](../../automation/install/wizard.py)
- 스크립트 대안: [`automation/install/quickstart.sh`](../../automation/install/quickstart.sh)
