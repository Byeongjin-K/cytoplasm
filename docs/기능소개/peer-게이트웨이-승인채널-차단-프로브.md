# peer 게이트웨이 승인채널 차단 프로브

## 무엇을

healthcheck의 읽기 전용 LOCAL 프로브 `peer_ignored_channels`가 peer 설정의 **최상위
`discord.ignored_channels`** 에 공급망 승인 채널이 들어 있는지 확인한다. 채널 id는
peer의 `interop/config.json`에 설정된 `personal_approvals_channel_id`와
`deploy_approvals_channel_id`로 해석하며(둘 중 하나 이상 필수),
설정이 없거나 읽을 수 없으면 PASS로 추측하지 않고 **FAIL-closed** 한다.
`owner_notice_channel_id`가 설정돼 있으면 그 id도 함께
요구한다 — peer 게이트웨이가 소유자 통지 채널을 수신하면 통지(주간 연구 동향 청크·헬스체크·
릴리스)마다 스레드를 열어 논평과 `/sethome` nag 를 남겼다([소개](주간-연구동향-한-메시지-발송과-peer-통지채널-차단.md)).
통지 ID가 없는 설치는 승인 ID만 대조한다. 표시 이름과 채널 디렉터리는 읽지 않는다.

## 왜

peer 봇 신원은 승인 채널에서 REST 게시·리액션 조회를 해야 하지만, **LLM 게이트웨이가
승인 카드를 수신할 필요는 없다**. 수신하면 자가 스킬이 카드를 임의 심사해 소유자 판단을
흐릴 수 있다. 노드에서 넣은 차단 설정이 config 재생성·온보딩으로 사라져도 다음
릴리스까지 기다리지 않고 healthcheck가 드리프트를 드러낸다. 승인 게이트 판정은 바뀌지 않는다.

## 사용 시나리오

- **정상:** 소유자가 차단 설정을 유지한 상태에서 기존 `automation/healthcheck.sh`를 실행한다
  → 프로브가 gateway 설정과 interop 설정을 대조해 `PEER-IGNORED-CHANNELS-PASS`(rc 0)를 낸다.
  이는 디스크 설정 확인이지 실행 중 게이트웨이가 그 설정을 로드했다는 증명은 아니다.
- **설정 유실:** 재생성된 config에서 승인 채널이 빠진다 → FAIL과
  `PEER-IGNORED-CHANNELS-RECOVERY` 안내가 나온다. 소유자가 interop 설정의 ID를 복원하고,
  운영 규약에 따라 agent·peer 게이트웨이 재시동을 판단한다. 프로브는 편집·재시동하지 않는다.
- **권한 부족:** `PEER-IGNORED-CHANNELS-UNREADABLE`(rc 1)이면 개별 sudoers 행을
  손으로 추가하지 않는다. 새 자산을 포함한 `deploy_checkout`에서 아래 **한 번의 `sudo`
  명령만** 실행한다. 설치기를 다시 돌리지 않아 이 drop-in 외의 설치 상태를 바꾸지 않는다.

```bash
sudo env REPO_ROOT="$PWD" sh -s <<'SH'
set -eu
target=/etc/sudoers.d/autophagy-healthcheck-peer-read
temporary=$(mktemp "${target}.XXXXXX")
trap 'rm -f "$temporary"' EXIT
python3 - "$REPO_ROOT" /etc/autophagy/node.toml >"$temporary" <<'PY'
from pathlib import Path
import sys

from automation.node_asset_renderer import render_asset
from automation.node_config import load_node_config

repo_root = Path(sys.argv[1])
config = load_node_config(Path(sys.argv[2]))
source = repo_root / "automation/sudoers.d/autophagy-healthcheck-peer-read"
print(render_asset(source, config), end="")
PY
visudo -cf "$temporary"
wanted=$(sha256sum "$temporary" | awk '{print $1}')
if [ -f "$target" ] && [ ! -L "$target" ] \
  && [ "$(stat -c '%a:%U:%G' "$target")" = 440:root:root ] \
  && [ "$(sha256sum "$target" | awk '{print $1}')" = "$wanted" ]; then
  printf '%s\n' PEER-READ-OK
  exit 0
fi
chown root:root "$temporary"
chmod 0440 "$temporary"
mv -f "$temporary" "$target"
[ "$(stat -c '%a:%U:%G' "$target")" = 440:root:root ]
[ "$(sha256sum "$target" | awk '{print $1}')" = "$wanted" ]
printf '%s\n' PEER-READ-OK
SH
```

  명령은 node config의 계정·홈으로 템플릿을 렌더해 peer 설정 파일에 대한
  `/usr/bin/cat -- <path>`만 허용한다. `visudo -cf`가 먼저 실패하면 설치하지 않고, 같은
  바이트와 0440 `root:root`이면 no-op이다. 그 외에는 원자 교체 뒤 sha256·소유권·모드를
  다시 확인한다. 최종 파일명에는 `.`을 넣지 않는다. `sudoers.d`가 그런 파일을 무시한다.

잘못된 YAML·잘못되거나 없는 승인 ID도 FAIL이며, PyYAML이 없으면
`PEER-IGNORED-CHANNELS-PARSER-UNAVAILABLE`과 설치 안내를 낸다. 파일 본문·채널 id는
진단에 출력하지 않는다. 권한 설치 전 반복 FAIL·수리 티켓 잡음은 OWNER 원장에 남긴다.

이 ID 전환을 배포할 기존 설치는 위 sudoers 자산을 한 번 다시 설치해야 한다.
`interop/config.json` 한 파일의 읽기만 추가되며 셸·Python sudo 권한은 추가하지 않는다.
프로브는 LOCAL 실행이고 SSH 강제명령 문자열·허용 목록은 바뀌지 않는다.

## 관련

- PR #409 · `automation/peer_gateway_probe.sh` · `automation/healthcheck.sh`
- 승인 채널 차단 변경 기록 ·
  운영 규약 · [소유자·관측 원장](../follow-ups-deferred.md#후속-과제-스윕-5-착지-후-남긴-것-2026-09-05--소유자관측)
- [자가 스킬 승인 역할 주장 advisory](에이전트-자가-스킬.md#승인-역할-주장-advisory-2026-09-05)(PR #408)는
  본문 저작을 알리는 별도 보완책이다. 둘 다 승인 주체나 소유자 ✅ 게이트를 대체하지 않는다.
